import json
import logging
import time
from collections import deque
from pathlib import Path

from minitouchpy import CommandBuilder
from peewee import *
from playhouse.sqlite_ext import JSONField

import util
from api import BestdoriAPI
import yaml


# 可用手指数上限。minitouch 上报的 max_contacts一般是 10。
# 注意这是「设备能同时按下的触点数」,不是「游戏能判几个音」—— 10 指在
# 密集段能显著减少因手指被占满而漏掉的音符（实测 #786 从漏 94 个降到 0 个）。
# 上限设成 10 而非更高：超过 minitouch 常规上限的部分不会被设备接受。
MAX_FINGERS = 10


class PlayRecord(Model):
    class Meta:
        database = SqliteDatabase("data/play_records.db")

    play_time = TimestampField()
    play_offset = JSONField()
    chart_id = CharField()
    difficulty = CharField()
    succeed = BooleanField()
    result = JSONField()


PlayRecord.create_table(safe=True)


class Chart:
    def __init__(self, id_and_difficulty: tuple[str, str] = None, song_name=None):
        self._id_, self._difficulty = id_and_difficulty
        self._song_name = song_name
        self._chart_name = f"{self._id_}-{self._difficulty}"
        self._chart_data = BestdoriAPI.get_chart(self._id_, self._difficulty)
        self._logger = logging.getLogger(self._chart_name)
        self._bpms = []
        self.actions = []
        self._commands = []
        self._total = len(self._chart_data)
        self._process_time_chart()

        self.actions_to_cmd_index = 0
        self._a2c_offset = 0
        self._a2c_rounded_loss = 0.0
        # 本曲「睡不了」的 wait 情况(2026-10-29),给 play_song 的理想指针补齐用
        # (lag_feedback_apply_skipped):
        #   skipped_wait_ms = 所有没发出去的 wait 时长合计(时间线欠账)。
        #     来源有两类:补偿整段吃掉、以及本来就是 <0.5ms 的间隙(密集曲常见)。
        #     #684 焚音打实测 92 条、曲内 -73ms,而「补偿抽干」只有 8 条 ——
        #     所以判据只能看时长,看条数会漏。
        #   drained_waits  = 其中由补偿吃掉的那部分(纯诊断)。
        self.skipped_wait_ms = 0.0
        self.drained_waits = 0
        # 本曲为「同指 down/up 后紧跟同刻 move」额外插入的 commit 条数
        # (2026-10-29,见 actions_to_MNTcmd 里 last_by_finger 的注释)。
        # 它是这条修复是否生效的指纹:动作数不变、命令串里多出这么多 'c'。
        self.detached_moves = 0
        # 已发射 wait 命令的「理想时长」队列(滞后反馈的测量基准,2026-10-23 第四版)。
        # ⚠ 四版演变,都写在这里防止后人重蹈(测量端口径的完整踩坑史):
        #   第一版(基准=命令串解析的实际值):字符串解析丢小数,账本虚高
        #     ~900ms → 推早 100ms/曲,翻车回退。
        #   第二版(基准=理想时长,逐条增量记账):账本=补偿后残差,自限但
        #     注入只补增量,残差存量挂账 —— FULL 曲前 20% 欠 ~50ms 永远
        #     压不掉(曲线平台即挂账);短曲欠账小是假达标。
        #   第三版(基准=实际生效时长,逐条增量记账):想追存量,但「逐条
        #     增量」口径把**批间空闲**也记成漂移 —— 反馈生效后服务端提前
        #     干完、空闲变大、账本虚涨,无自限正反馈:实测账本 +1358ms
        #     (dump -75ms),推早 1.3s 批量 FAST,当场翻车。教训:逐条增量
        #     口径下「命令间一切时间差都算漂移」,与补偿天然耦合,不能用于
        #     反馈测量。
        #   第四版(本版,基准=理想时长 + **绝对对齐**记账):在线维护理想
        #     时间线指针(ideal += 上一条 wait 的理想时长),lag = start −
        #     ideal。与 _dump_server_lag_async 的重建口径完全一致:批间
        #     空闲正确表现为提前(lag 下降,自限);注入端改为「补足到池」
        #     (lag − _a2c_offset),排水消费池水时残差同步下降、下批补足
        #     为 0 —— 既自限又追回存量(第二版+第三版各自缺陷的合并解)。
        # mnt_callback 收到对应 jlog 'w' 事件时按序弹出(服务端按序执行,
        # 顺序天然一致;队列在 callback_data_lock 内读写)。
        self.wait_ideal_ms = deque()

    def _beat_to_time(self, beat: float) -> float:
        if not self._bpms:
            return 0

        def _get_time_for_section(
            bpm: float, previous_bpm_beat: float, current_bpm_beat: float
        ) -> float:
            return (current_bpm_beat - previous_bpm_beat) * (60.0 / bpm) if bpm else 0

        time_ = 0.0

        previous_bpm_beat = 0.0
        current_bpm = 0.0

        for bpm, bpm_beat in self._bpms:
            if bpm_beat > beat:
                break
            time_ += _get_time_for_section(current_bpm, previous_bpm_beat, bpm_beat)
            previous_bpm_beat = bpm_beat
            current_bpm = bpm

        time_ += _get_time_for_section(current_bpm, previous_bpm_beat, beat)

        return time_ * 1000

    def _process_time_chart(self):
        checkpoint_index = -1
        note_index = -1

        def get_checkpoint_index():
            nonlocal checkpoint_index
            checkpoint_index += 1
            return checkpoint_index

        def get_note_index():
            nonlocal note_index
            note_index += 1
            return note_index

        # 先收集全部 BPM 再换算(2026-10-21,issue 回帖第 2 点确认的真 bug):
        # 原来只有一遍遍历,BPM 条目「遇到才收进 _bpms」,而 Long/Slide 是单
        # 条目、处理它时其全部 connections 一次性换算 —— 长条跨过一个尚未
        # 遍历到的 BPM 变化时,后续 connection 按旧 BPM 换算,时间全错。
        # 缓存 1112 张谱面实测 29 张受影响(2.6%),最大偏差 2168ms
        # (#477/#421/#255 都在 1s 上下,见 _diag_bpm.py);#180/#443/#555/#568
        # 等歌历史小额 miss 也可由此解释。拆成两遍:先收集,再统一换算,
        # index/checkpoint 编号顺序与原单遍一致,不受影响。
        for note in self._chart_data:
            if note.get("type") == "BPM":
                self._bpms.append((note["bpm"], note["beat"]))

        for _, note in enumerate(self._chart_data):
            note_type = note["type"]

            if note_type == "BPM":
                continue
            elif note_type in ["Single", "Directional"]:
                note["time"] = self._beat_to_time(note["beat"])
                note["checkpoint_index"] = get_checkpoint_index()
                note["index"] = get_note_index()
            elif note_type in ["Slide", "Long"]:
                note["index"] = get_note_index()
                for connection in note["connections"]:
                    connection["time"] = self._beat_to_time(connection["beat"])
                    if not connection.get("hidden", False):
                        connection["checkpoint_index"] = get_checkpoint_index()
            else:
                self._logger.warning(
                    f"_chart_to_time_chart: Unknown type: {note_type}, Skipped"
                )
        self._logger.debug(
            f"_chart_to_time_chart: Succeed: {len(self._chart_data)} notes"
        )

    def notes_to_actions(
        self,
        screen_resolution: tuple[int, int],
        default_move_slice_size,
    ):
        notes: list[dict] = self._chart_data

        def get_lane_position(lane: int) -> tuple[int, int]:
            lane_config = util.get_runtime_info(screen_resolution)["lane"]
            return (
                lane_config["start_x"] + (lane + 0.5) * lane_config["w"],
                lane_config["h"],
            )

        actions = []
        # 可用手指数。minitouch 的 max_contacts 通常是 10（设备实际支持的上限），
        # 原实现只给5 个,是为了模拟真人 5 指 —— 但那是"手不够用"的近似,
        # 不是设备限制:多给的 contact 游戏照样识别。
        #
        # 5 指对绝大多数曲子够用（#596/#487/#486 实测抢不到手指数恒为 0）,但
        # #786（ときめきエクスペリエンス！,1097 音符/104s）有 8.6% 的音符抢不到手,
        # 最高时 5 指全被长按占满 → 这些音符**必然 MISS**。
        #
        # get_finger 按 id 升序返回**第一个空闲**手指,所以只要 5 指够用,
        # 提到 10 不会改变任何分配结果 —— 已实测 #596/#487/#486 的 actions
        # 逐字节一致（见 _exp_noimpact.py）,属零影响改动。
        available_fingers = [
            {
                "id": i,
                "occupied_time": [],
            }
            for i in range(1, MAX_FINGERS + 1)
        ]

        # 全部手指都被占用时 get_finger 会返回 None,调用方没有做任何处理 ——
        # 生成出来的指令要么带着 finger=None,要么被移动/长按分支直接丢弃,
        # 结果就是这些音符不会发出点击。它们必然 MISS,而且在结算里表现为
        # 「其余判定都正常、只有零星几个 miss,且 fast/slow 为 0」,和时序偏移
        # 完全不同。这里记录下来,区分「判定晚了」和「压根没点」。
        finger_missed: list[float] = []

        def get_finger(from_time, to_time) -> int:
            for finger in available_fingers:
                if any(
                    not (to_time <= occupied_from or from_time >= occupied_to)
                    for occupied_from, occupied_to in finger["occupied_time"]
                ):
                    continue
                else:
                    finger["occupied_time"].append((from_time, to_time))
                    return finger["id"]
            finger_missed.append(from_time)
            return None

        def add_tap(note_index, from_time, duration, pos):
            finger = get_finger(from_time, from_time + duration)
            actions.extend(
                [
                    {
                        "finger": finger,
                        "type": "down",
                        "time": from_time,
                        "pos": pos,
                        "note": note_index,
                    },
                    {
                        "finger": finger,
                        "type": "up",
                        "time": from_time + duration,
                        "note": note_index,
                    },
                ]
            )

        def split_number(num, part_size):
            result = []
            cur = 0
            while True:
                if num - cur > part_size:
                    result.append((cur, part_size))
                    cur += part_size
                else:
                    result.append((cur, num - cur))
                    break
            return result

        def add_smooth_move(
            note_index,
            finger,
            from_time,
            duration,
            from_,
            to,
            slice_size=default_move_slice_size,
            down=True,
            up=True,
        ):
            to_time = from_time + duration
            from_x, from_y = from_
            to_x, to_y = to

            # 零长度段：Slide 的相邻两个 connection 可能落在同一 beat —— 谱面用这种
            # 成对写法表达「一次划过两个 lane」(实测 #786 有 100 处,如 note#984 的
            # lane 1→0 @beat280、note#998 的 5→6/4→5/3→4/2→3)。
            # 此时 duration=0,下面的 (to_x - from_x) / duration 会抛
            # ZeroDivisionError,让整个 save_song() 中断。它跑在 post_task **之前**,
            # 所以外部表现是「曲目加载完就再无动静、连演出开始都不点」,而
            # autodori.exe 的 stderr 不被 GUI 捕获 → debug 日志里看不到 traceback。
            # 瞬时移动没有可插值的中间点,直接返回;调用方若要求按下/抬起,仍按原
            # 契约补上这两个动作,避免手指状态悬空。
            if duration <= 0:
                if down:
                    actions.append(
                        {
                            "finger": finger,
                            "type": "down",
                            "time": from_time,
                            "pos": from_,
                            "note": note_index,
                        }
                    )
                if up:
                    actions.append(
                        {
                            "finger": finger,
                            "type": "up",
                            "time": to_time,
                            "note": note_index,
                        }
                    )
                return

            slices = split_number(duration, slice_size)
            x_size = (to_x - from_x) / duration
            y_size = (to_y - from_y) / duration

            result = []
            if down:
                result.append(
                    {
                        "finger": finger,
                        "type": "down",
                        "time": from_time,
                        "pos": from_,
                        "note": note_index,
                    }
                )
            for i, (cur_slice_start, cur_slice_size) in enumerate(slices):
                # 幻影切片过滤(2026-10-21,固定漏 miss 悬案根因,帧语义重放
                # 实测 7/11 首漏点数精确吻合、3 首 ALL PERFECT 负对照为 0,
                # 见 _diag_final3.py):split_number 对非 slice_size 整除的
                # 时长(多为浮点噪声,如 duration=120.000000000004)会切出一
                # 个 ~1e-12 的尾片,其时刻 = from+duration+0.00001,恰好落在
                # 收尾 connection 的时刻上;而 move 的 +0.00001 偏移又保证它
                # 排在「同刻同手指开始的下一个 note 的 down」之后。两者间隙
                # 仅 10μs,不会插 wait → 同一个 minitouch commit → evdev 一帧
                # 内同 contact 只保留最终位置,触点在同一帧内从新 note 的
                # lane 直接跳到幻影位置 → 新音符的 tap/绿条头从未在自己的
                # lane 上出现过 →「压根没点」型 miss(fast/slow=0、great=0,
                # 数量逐场固定)。尾片位置与前一片目标相同(=终点),跳过无损。
                if cur_slice_size < 0.5:
                    continue
                result.append(
                    {
                        "finger": finger,
                        "type": "move",
                        "time": 0.00001 + from_time + cur_slice_start,
                        "to": (
                            from_x + x_size * (cur_slice_size + cur_slice_start),
                            from_y + y_size * (cur_slice_size + cur_slice_start),
                        ),
                        "note": note_index,
                    }
                )
            if up:
                result.append(
                    {
                        "finger": finger,
                        "type": "up",
                        "time": to_time,
                        "note": note_index,
                    },
                )
            actions.extend(result)

        for note in notes:
            note_data = note
            note_type = note_data["type"]
            note_index = note_data.get("index", None)

            if note_type == "Single":
                time_ = note_data["time"]
                from_lane = note_data["lane"]
                pos = get_lane_position(from_lane)

                if note_data.get("flick"):
                    finger = get_finger(time_, time_ + 80)
                    add_smooth_move(
                        note_index, finger, time_, 80, pos, (pos[0], pos[1] - 300)
                    )
                else:
                    add_tap(note_index, time_, 50, pos)

            elif note_type == "Directional":
                time_ = note_data["time"]
                fromlane = note_data["lane"]
                width = note_data["width"]
                direction = note_data["direction"]
                if direction == "Right":
                    tolane = fromlane + width
                else:
                    tolane = fromlane - width
                finger = get_finger(time_, time_ + 80)

                add_smooth_move(
                    note_index,
                    finger,
                    time_,
                    80,
                    get_lane_position(fromlane),
                    get_lane_position(tolane),
                )
            elif note_type in ["Long", "Slide"]:
                from_lane = note_data["connections"][0]["lane"]
                from_pos = get_lane_position(from_lane)
                from_time = note_data["connections"][0]["time"]
                to_time = note_data["connections"][-1]["time"]

                end_flick = note_data["connections"][-1].get("flick")
                if end_flick:
                    finger_end_time = to_time + 80
                else:
                    finger_end_time = to_time
                finger = get_finger(
                    from_time,
                    finger_end_time,
                )
                actions.append(
                    {
                        "finger": finger,
                        "type": "down",
                        "time": from_time,
                        "pos": from_pos,
                        "note": note_index,
                    }
                )

                end_pos = None

                for i, connection in enumerate(note_data["connections"]):
                    if i != len(note_data["connections"]) - 1:
                        next_connection = note_data["connections"][i + 1]
                        if connection["lane"] != next_connection["lane"]:
                            add_smooth_move(
                                note_index,
                                finger,
                                connection["time"],
                                next_connection["time"] - connection["time"],
                                get_lane_position(connection["lane"]),
                                get_lane_position(next_connection["lane"]),
                                down=False,
                                up=False,
                            )
                    else:
                        end_pos = get_lane_position(connection["lane"])

                if end_flick:
                    add_smooth_move(
                        note_index,
                        finger,
                        to_time,
                        80,
                        end_pos,
                        (end_pos[0], end_pos[1] - 300),
                        down=False,
                        up=False,
                    )
                actions.append(
                    {
                        "finger": finger,
                        "type": "up",
                        "time": finger_end_time,
                        "note": note_index,
                    }
                )
            else:
                logging.warning(f"notes_to_actions: Unknown type: {note_type}")

        actions.sort(key=lambda x: x["time"])
        actions: list[dict]

        actions_with_wait: list[dict] = []
        for i, action in enumerate(actions):
            actions_with_wait.append(action)
            if i != len(actions) - 1:
                current_time = action["time"]
                next_time = actions[i + 1]["time"]

                if next_time - current_time > 0.001:
                    actions_with_wait.append(
                        {
                            "type": "wait",
                            "time": current_time,
                            "length": next_time - current_time,
                        }
                    )
            else:
                pass

        [
            action.setdefault("index", index)
            for index, action in enumerate(actions_with_wait)
        ]
        self.actions = actions_with_wait

        if finger_missed:
            self._logger.warning(
                "%d 个音符没抢到手指(%d 指全被占用),这些音符不会发出点击指令 → 必然 MISS;"
                "首次出现在 %.0fms,前几个: %s",
                len(finger_missed),
                MAX_FINGERS,
                finger_missed[0],
                ", ".join("%.0f" % t for t in finger_missed[:8]),
            )

    def actions_to_MNTcmd(self, resolution, orientation, offset_info, size=50):
        self.command_builder = CommandBuilder()
        builder = self.command_builder
        actions = self.actions[
            self.actions_to_cmd_index : self.actions_to_cmd_index + size
        ]
        commands = self._commands

        up_offset = offset_info.get("up", 0)
        down_offset = offset_info.get("down", 0)
        move_offset = offset_info.get("move", 0)
        wait_offset = offset_info.get("wait", 0)
        interval_offset = offset_info.get("interval", 0)

        def append(command_to_append, action=None):
            commands.append(
                {
                    "command": command_to_append,
                    "action": action,
                }
            )

        def round_tuple(target):
            return tuple(round(x) for x in target)

        # 同帧 down/up 与 move 覆盖起点(2026-10-29):每条手指上一次发出的
        # 指令类型与时刻,用来判断「这条 move 与同指的 down/up 落在同一帧」。
        # 背景:#489/#649-special 每局固定 649 PERFECT + miss 2,差的正是最后
        # 两个判定点 —— 一条 10.1 秒滑条的头(84.63s)和尾。生成的动作是
        # `84631.6ms down f1 (641.5, lane3)` 与 `84631.6ms move f1 → 422.5
        # (lane1.5)`,move 只晚 10μs,不够插 wait,于是同一个 minitouch commit;
        # evdev 一帧内同 contact 只保留**最终**位置,游戏看到触点一出现就在
        # lane1.5,头判丢失 → 整条滑条无效 → 尾也 miss。
        # #304-special 的 97800ms 处同族(down lane3 与 move lane1.68)。
        # 修法:down/up 与同刻 move 之间强制插一次 commit()(不插 wait),
        # 让判定点独占一帧,下一帧再开始移动。时刻不变,只多一帧。
        # 只按**同指**判:跨指同帧是正常的(evdev 按 contact 各自取最终位置),
        # 零 miss 的谱面同样满是跨指同刻冲突,一起切帧纯属白费。
        # 实测注入处数(#489/#649 156、#304 193、#409 166、#522 144、#146 123):
        # 绝大多数只是同 lane 内的几十 px 微移,只有「一跳超过一个 lane」的那
        # 一两处会丢判定;但帧语义只认最终位置,不按位移大小分档更省心 ——
        # 多切一帧不动时刻,服务端每条 commit 的开销(几十 μs)由滞后反馈补。
        # 批边界不用管:发布时 publish() 末尾本来就会 commit,那两条天然分帧。
        last_by_finger = {}

        # append
        for i, action in enumerate(actions):
            action_type = action["type"]
            action_index = action["index"]

            # finger=None = 这个音符在解算时5 指全被占用、没抢到手指(见 notes_to_actions
            # 的 finger_missed)。它照样被排进了时间轴,但没有手指可用。
            #
            # 原实现在这里照样调builder.down/up,把 finger 直接格式化进命令串,
            # 生成出 `d None 200 590 1` / `u None` 这种**非法 minitouch 指令**。
            # 实测 #786(ときめきエクスペリエンス！) 5 指下会产生 252 条,
            # 其余三首为 0 —— 它是唯一一首密到 5 指不够的曲子。minitouch 收到
            # 非法 contact 会解析失败,把整批命令的语义搅乱(轻则该音符 MISS,
            # 重则整批下发错位),所以这里直接丢弃、不生成任何指令。
            # 语义上等价于"这个音符没被点",与解算层的判断一致。
            if action.get("finger", None) is None and action_type in ("down", "move", "up"):
                continue

            self._a2c_offset += interval_offset

            if action_type == "down":
                self._a2c_offset += down_offset
                finger = action["finger"]
                append(
                    builder.down(
                        finger,
                        *util.androidxy_to_MNTxy(
                            round_tuple(action["pos"]), resolution, orientation
                        ),
                        1,
                    ),
                    action_index,
                )
                last_by_finger[finger] = (action["time"], "down")
            elif action_type == "move":
                self._a2c_offset += move_offset
                finger = action["finger"]
                # 同指 down/up 之后紧跟着同刻 move:先封一帧,保住按下/抬起
                # 那个位置(见上面 last_by_finger 的注释)。0.001ms 与下面插
                # wait 的判据同一个门槛 —— 够不上插 wait 的就是同一帧。
                _prev = last_by_finger.get(finger)
                if (
                    _prev is not None
                    and _prev[1] in ("down", "up")
                    and action["time"] - _prev[0] <= 0.001
                ):
                    append(builder.commit())
                    self.detached_moves += 1
                last_by_finger[finger] = (action["time"], "move")
                append(
                    builder.move(
                        finger,
                        *util.androidxy_to_MNTxy(
                            round_tuple(action["to"]), resolution, orientation
                        ),
                        1,
                    ),
                    action_index,
                )

            elif action_type == "up":
                self._a2c_offset += up_offset
                finger = action["finger"]
                append(builder.up(finger), action_index)
                last_by_finger[finger] = (action["time"], "up")

            elif action_type == "wait":
                self._a2c_offset += wait_offset
                wait_for = action["length"]

                # 排水上限自适应(2026-10-25,issue #11):原固定 1ms 在高开销
                # 环境下排水容量 ≈ 开销积累(用户机器 wait 开销实测均值 0.97ms,
                # 贴着 1ms 上限,零余量),反馈补偿追不平,曲内滞后 45→110ms
                # 收敛不了。上限改为「实测开销 + 0.5ms 余量」,低开销机器
                # (实测 ~0.07ms)不受影响 —— max(1, …) 保底,行为与旧版完全一致。
                # 上限再大有过冲出 FAST 的风险,反馈环每批重置残差可自限,
                # 但余量 0.5ms 够把单批 ~44 条 wait 的波动拉平,先不放大。
                OFFSET_LIMIT = max(1, wait_offset + 0.5)
                offset_adjust = min(
                    wait_for,
                    min(OFFSET_LIMIT, max(-OFFSET_LIMIT, self._a2c_offset)),
                )
                # self._logger.debug(
                #    f"self._a2c_offset: {self._a2c_offset}, wait_for: {wait_for}, adjust: {offset_adjust}"
                # )
                wait_for -= offset_adjust
                self._a2c_offset -= offset_adjust

                LOSS_LIMIT = 2
                rounded_loss_adjust = min(
                    wait_for,
                    min(LOSS_LIMIT, max(-LOSS_LIMIT, self._a2c_rounded_loss)),
                )
                wait_for -= rounded_loss_adjust
                self._a2c_rounded_loss -= rounded_loss_adjust

                rounded_waitfor = round(wait_for)
                self._a2c_rounded_loss -= wait_for - rounded_waitfor
                if rounded_waitfor > 0.01:
                    append(builder.commit())
                    append(builder.wait(rounded_waitfor))
                    # 理想时长入队(反馈补偿基准,第四版绝对对齐口径):
                    # mnt_callback 收到这条 'w' 的 jlog 回执时弹出,用原始
                    # action["length"](未补偿/未取整),与 _dump_server_lag_async
                    # 的理想流重建口径一致 —— 在线 lag 与 dump 可直接对照。
                    self.wait_ideal_ms.append(action["length"])
                else:
                    # 这条间隙被跳过(补偿整段吃掉,或本来就不足 0.5ms)
                    # (密集曲一次几十条)。这里累加的是**原始间隙时长**而不是
                    # 补偿后的剩值 —— 因为在线估计器的理想指针只按「发出的 wait」
                    # 推进,没发出的这条它压根不会计入,指针就永久落后这么多。
                    # 指针落后 → lag 被算得偏正(假"晚") → 反馈持续往早推 →
                    # 越打越早。#684 焚音打 92 条、欠账约 73ms、曲内 -73ms。
                    self.skipped_wait_ms += action["length"]
                    if offset_adjust >= 0.5:
                        self.drained_waits += 1

        self.actions_to_cmd_index += size

    def dump_debug_config(self):
        dump_path = Path("debug/dump")
        dump_path.mkdir(parents=True, exist_ok=True)
        (
            dump_path / f"{self._song_name}-{self._difficulty}-{time.time()}.yml"
        ).write_text(
            yaml.safe_dump(
                {
                    "song_name": self._song_name,
                    "song_id": self._id_,
                    "chart": self._chart_data,
                    "actions": self.actions,
                    "commands": self._commands,
                },
                sort_keys=False,
                allow_unicode=True,
                indent=2,
            ),
            "utf-8",
        )

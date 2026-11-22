# -*- coding: utf-8 -*-
r"""「阅读活动故事」专用流程(2026-10-31 新增)。

需求口径(用户 2026-10-31 定):
- 分工:用户手动停在**活动主页**(活动 top 页面),bot 点右侧「活动故事」进话列表,
  按剧情顺序逐话跳过并领奖。
- 解锁机制:每话要**活动 PT 达阈值**才解锁。PT 不足、或前面的剧情没读就点后面,
  游戏会提示「该剧情尚未解锁」,「阅读」按钮同时变暗、点不动。
  ⇒ bot 必须从**列表最下面的未读话**开始按顺序读,绝不跳读。
- 已读判据:列表项右上角的「NEW」标记 —— 读完一话后 NEW 消失(2026-10-31 实测:
  第 1 话读完后 NEW 消失、列表自动选中下一个未读话)。
  这是游戏服务器状态、跨会话存在,**所以「恢复」以 NEW 为权威依据**:
  每次启动都从最下面的 NEW 开始,不靠本地文件推断断点。
- 本地 `data/event_story_progress.json` 只做记录(上次读了几话、卡在哪、时间),
  **不参与「从哪开始读」的决策**。
- 异常策略(用户要求"妥善中断或跳过并给出明确提示"):
  ① 等不到可点的「阅读」按钮(变暗/不存在)= 当前话未解锁 → 记 blocked=locked、
     停任务,日志说明是 PT 不足或前置剧情未读,达标后再运行;
  ② 逐话读到底(列表滚完没有 NEW)→ 正常收工,并在日志里提醒「若还有未解锁的话
     需 PT 达标」;
  ③ 入口认不出(不在活动主页)→ 不盲点,直接停并提示。
- 不勾「连续阅读已解锁的故事」:连续模式由游戏自己推进,bot 无法在每话之间
  控制跳过,反而容易卡在播放中。

「结束」的判据(2026-11-06 按用户反馈定死,改动前先读这段):
  唯一判据 = **列表里找不到 NEW 标记**,不是"读满 N 话",也不是活动主页的话数
  (那行浅灰小字 OCR 读不准,见下)。识别方式:在左栏最多向上滚 SCROLL_LIMIT 屏,
  每屏都做一次 NEW 模板匹配;滚完仍没有 NEW 就结束。
  2026-11-06 实机补充:读完最后一话后仍会向上滚满 6 屏、约 10 秒,而这 6 次截图
  完全一致(maa.log 里 6 次识别分数都是同一个 0.450737)—— 列表早就到顶,滚动
  是白做的,玩家看到的是「一直在点最后一个剧情、点了一会才结束」。
  所以现在加一条提前收工:滚动前后画面完全没变,连续两次就判定「列表已到顶」,
  直接按"没有未读"结束。**判据本身不变**,只是不再空滚;收尾日志按
  end_reason 区分「列表已到顶」与「滚满 6 屏仍无 NEW」两种结束原因。

为什么没有「还差几话」的数值提示:活动主页那行「7/7话 已解锁」是浅灰小字,
2026-10-31 用 1x / 3x / 3x+二值化三种输入实测,ppocr 都只吐出「尿臀情。」这类
乱码,读不出数字。与其编一个假数字,不如统一提示「PT 达标后再运行」。

模板取证:2026-10-31 实机 IPC 截图(debug/local/_evt_*.png,1280x720)。
坐标用粉色连通块定位,不用目测 —— 显示图是 1080 宽,与 1280 差 1.185 倍,
目测裁图会整体错位(与小故事流程同一坑)。离线分数:入口/阅读/NEW/无语音
正样本 1.0,负样本 ≤0.46;复用的 reader/menu 0.9787、skip 1.0、
skip/pink 0.9998、rankup_confirm 1.0。
"""

import json
import logging
import os
import time

import numpy as np

from challenge import click
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

# 活动主页右侧「活动故事」入口白卡按钮(书本图标 + 文字)
ENTRY_ROI = [1080, 330, 190, 90]

# 话列表左栏(条目 + NEW 标记)。列表会滚动,roi 取整栏。
LIST_ROI = [0, 60, 300, 560]
LIST_X = 10
LIST_Y = 60
LIST_W = 285

# 点条目时用的高度:条目约 92px,NEW 标在右上角,点条目中部更稳
CHAPTER_H = 96

# 找 NEW 时最多向上滚几屏。活动一般 7 话,一屏约 3 项,
# 从最底部滚到顶 3 次足够;留到 6 次是给「列表更长」的活动兜底。
SCROLL_LIMIT = 6

# 提前收工判据(2026-11-06 实机问题):读完最后一话后列表已经到顶,再滑 6 次
# 画面纹丝不动,白等约 10 秒,玩家看到的是「一直在点最后一个剧情」。
# 滚动前后两帧的平均像素差 < STILL_DIFF 视为「没滚动」,连续 STILL_LIMIT 次
# 就判定列表已到顶、按"没有未读"结束。
# 阈值取 1.0(0~255):列表真滚动时左栏整列都在变,平均差在个位数量级以上;
# 静态页面的呼吸/高光动画只有零点几。阈值给小了只是退回旧的 6 次空滚,
# 不会误判成"滚动成功",所以宁可取小。
STILL_DIFF = 1.0
STILL_LIMIT = 2

# 进度文件:只做记录,删除也不影响流程
PROGRESS_PATH = os.path.join("data", "event_story_progress.json")

# 本次运行的统计(进程内,每次启动归零)
_STATE = {
    "read": 0,           # 本次运行已读话数
    "blocked": "",       # "" | "locked"
    "entry_failed": False,  # 入口没认出来(不在活动主页)
    # 结束原因(只用于收尾文案,不参与判据):
    # "" | "no_new_no_scroll"(列表滚不动=已到顶) | "no_new_scroll_limit"(滚满 6 屏)
    "end_reason": "",
    "scroll_attempts": 0,  # 本次收工前实际滑动了几次
}


def event_story_state():
    """给收尾日志用的快照(autodori._main_impl 里读)。"""
    return dict(_STATE)


def pick_lowest(boxes):
    """从多个 NEW 命中里取**最靠下**的那个(列表最下 = 剧情最早)。

    「最靠下」是本流程的核心判据:游戏按剧情顺序解锁,从下往上读才不会
    撞「该剧情尚未解锁」。列表倒序(最新话在上、序章在下)。
    """
    if not boxes:
        return None
    return max(boxes, key=lambda b: b[1])


def load_progress():
    try:
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_progress(blocked=""):
    record = {
        "read": _STATE["read"],
        "blocked": blocked,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = load_progress()
    history = data.get("history") or []
    # 只留最近 20 次,避免文件无限长
    data["history"] = (history + [record])[-20:]
    data["last"] = record
    try:
        os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:  # 落盘失败绝不影响阅读
        logging.debug("活动故事进度写入失败(%s)", e)


def _screencap(context):
    """滚动后重新取一帧。失败返回 None,由调用方按「没找到」处理。"""
    try:
        job = context.tasker.controller.post_screencap()
        job.wait()
        img = job.get()
        if img is not None and getattr(img, "size", 0):
            return img
    except Exception as e:  # noqa: BLE001 —— 截图失败不该把任务炸掉
        logging.debug("活动故事列表截图失败(%s)", e)
    return None


def _swipe_list_up(context):
    """在左栏把列表往上翻(看更靠上的话)。

    手指从上往下拖 = 内容向上滚。方向写反会一直停在同一条上,
    表现为「滚了 6 次还是同一屏」。2026-11-06 起这种「滚不动」不再靠次数
    兜底,由 _frame_diff_mean 提前判出来(见 STILL_DIFF 注释)。
    """
    context.run_action("_eventstory_scroll", pipeline_override={
        "_eventstory_scroll": {
            "action": "Swipe",
            "begin": [150, 180, 1, 1],
            "end": [150, 520, 1, 1],
            "duration": 500,
            "post_delay": 300,
        },
    })


def _frame_diff_mean(a, b):
    """两帧的平均像素差(0~255)。取不到帧/尺寸不一致一律返回 255。

    返回大值是**保守方向**:当作"画面变了、滚动有效",继续滚下一屏 ——
    宁可多滚一次,也不要因为比对失败把还有未读的列表判成已读完。
    """
    try:
        if a is None or b is None or a.shape != b.shape:
            return 255.0
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
    except Exception:  # noqa: BLE001 —— 比对失败走保守分支
        return 255.0


class EventStoryEntry(CustomRecognition):
    """识别活动主页的「活动故事」入口按钮。

    只做识别:活动名/解锁话数都是浅灰小字或艺术字,OCR 读不准
    (见模块头注释),与其编数字不如不显示。
    """

    def analyze(self, context, argv):
        entry = context.run_recognition("_eventstory_entry", argv.image, {
            "_eventstory_entry": {
                "recognition": "TemplateMatch",
                "template": "event/event_story_entry.png",
                "roi": ENTRY_ROI,
                "threshold": 0.85,
            },
        })
        if entry is None or entry.best_result is None:
            return None

        last = load_progress().get("last") or {}
        if last.get("read") is not None:
            logging.info("活动故事：开始（上次运行读了 %s 话，%s）。",
                         last.get("read"), last.get("updated_at", ""))
        return CustomRecognition.AnalyzeResult(
            entry.best_result.box, json.dumps({"ok": True}),
        )


class EventChapterPick(CustomRecognition):
    """找列表里**最下面的未读话**(NEW 标记最低的那个)。

    NEW 是唯一的已读判据(读完后消失)。列表只显示 3 项左右,看不到的未读
    需要往上翻:所以这里自带「找不到就滚一屏再找」,最多 SCROLL_LIMIT 次。
    滚到底仍没有 NEW → 返回 found=false,由 EventChapterPickClick 收工。

    2026-11-06 实机补充:读完最后一话后列表已经到顶,旧写法还会再滑 6 次、
    白等约 10 秒(6 次截图完全一致),玩家看到「一直在点最后一个剧情」。
    现在滑完之后先比一帧:连续 STILL_LIMIT 次画面没变就判定列表到顶,
    立刻按"没有未读"返回。判据没变(仍然要"找不到 NEW"),只是不再空滑。

    不点 NEW 标记本身,而是返回该条目区域让 Click 点中部 —— 标记在条目
    右上角,点标记边缘容易落到条目外。
    """

    def analyze(self, context, argv):
        prev = None       # 上一次滑动之前的画面
        still = 0         # 连续「滑了但画面没变」的次数
        attempts = 0      # 本次实际滑动次数
        for attempt in range(SCROLL_LIMIT + 1):
            img = argv.image if attempt == 0 else _screencap(context)
            if img is None:
                break
            res = context.run_recognition("_eventstory_new_tag", img, {
                "_eventstory_new_tag": {
                    "recognition": "TemplateMatch",
                    "template": "event/event_new_tag.png",
                    "roi": LIST_ROI,
                    "threshold": 0.85,
                    "all": True,
                },
            })
            boxes = []
            if res is not None:
                results = list(getattr(res, "all_results", None) or [])
                if not results and res.best_result is not None:
                    results = [res.best_result]
                boxes = [r.box for r in results if r.box]
            if boxes:
                box = pick_lowest(boxes)
                y = max(int(box[1]) - 34, LIST_Y)
                logging.debug("活动故事：找到 %d 个未读话，取最下面的 y=%d",
                              len(boxes), y)
                return CustomRecognition.AnalyzeResult(
                    [LIST_X, y, LIST_W, CHAPTER_H],
                    json.dumps({"found": True, "new_y": int(box[1]),
                                "candidates": len(boxes)}),
                )
            if attempt < SCROLL_LIMIT:
                if prev is not None:
                    diff = _frame_diff_mean(prev, img)
                    still = still + 1 if diff < STILL_DIFF else 0
                    if still >= STILL_LIMIT:
                        _STATE["end_reason"] = "no_new_no_scroll"
                        _STATE["scroll_attempts"] = attempts
                        logging.debug(
                            "活动故事：连续 %d 次滑动后画面未变化（平均差 %.2f），"
                            "判定列表已到顶，停止滚动", still, diff)
                        return CustomRecognition.AnalyzeResult(
                            [LIST_X, LIST_Y, LIST_W, CHAPTER_H],
                            json.dumps({"found": False, "candidates": 0,
                                        "reason": "no_scroll"}),
                        )
                prev = img
                _swipe_list_up(context)
                attempts += 1
                time.sleep(0.5)
        reason = "no_new_scroll_limit" if attempts else "no_new_unknown"
        _STATE["end_reason"] = reason
        _STATE["scroll_attempts"] = attempts
        logging.debug("活动故事：滑动 %d 次仍未找到 NEW，按读完处理", attempts)
        return CustomRecognition.AnalyzeResult(
            [LIST_X, LIST_Y, LIST_W, CHAPTER_H],
            json.dumps({"found": False, "candidates": 0,
                        "reason": "scroll_limit" if attempts else "unknown"}),
        )


class EventChapterPickClick(CustomAction):
    """点选中的那一话;列表里没有未读话时把下一步改到收工节点。

    收工**不能靠返回 False** —— 2026-10-31 实机踩坑:返回 False 时 MAA 会走
    error handling 从 entry 重跑整条链,日志里出现
    `error handling loop detected [node.name=eventstory]`,任务被记为 Failed,
    还会误报「未检测到活动主页的入口」。正确做法是 override_next 到
    eventstory_done,让它按正常 next 链走到 stop。
    """

    def run(self, context, argv: CustomAction.RunArg):
        detail = argv.reco_detail.best_result.detail if argv.reco_detail else None
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        if not (detail or {}).get("found"):
            logging.debug("活动故事列表里没有未读的话，本次阅读结束（reason=%s）。",
                          (detail or {}).get("reason"))
            context.override_next("eventstory_pick", ["eventstory_done"])
            return CustomAction.RunResult(True)
        box = argv.box
        if not click(context, "_eventstory_pick_click", [box.x, box.y, box.w, box.h]):
            # 点击失败不能靠返回 False:那会走 MAA 的 error handling 从 entry 重跑
            # 整条链(见类注释)。显式记下失败原因,收尾日志才说得清是"没读完"还是
            # "中途出错"。
            _STATE["blocked"] = "click_failed"
            _save_progress("click_failed")
            logging.error("点击话列表里的条目失败，停止阅读。")
            context.override_next("eventstory_pick", ["eventstory_done"])
            return CustomAction.RunResult(True)
        return CustomAction.RunResult(True)


class EventChapterDone(CustomAction):
    """点「故事达成奖励」的「确定」并记一话完成。

    这是唯一能确认「一话真正读完」的位置:奖励弹窗每话必弹(2026-10-31 实测)。
    跳过确认后游戏回列表并自动选中下一个未读话,所以这里点完直接回 pick。
    """

    def run(self, context, argv: CustomAction.RunArg):
        box = argv.box
        if not click(context, "_eventstory_reward_ok", [box.x, box.y, box.w, box.h]):
            # 同上:返回 False 会触发 MAA 的 error handling 重跑 entry,
            # 收尾日志也会变成"入口没认出来"这种误导性结论。
            _STATE["blocked"] = "reward_failed"
            _save_progress("reward_failed")
            logging.error("点击「故事达成奖励」的「确定」失败，停止阅读。")
            context.override_next("eventstory_reward", ["eventstory_done"])
            return CustomAction.RunResult(True)
        _STATE["read"] += 1
        _save_progress()
        logging.info("活动故事已读第 %d 话（本次累计 %d 话），已领取达成奖励。",
                     _STATE["read"], _STATE["read"])
        return CustomAction.RunResult(True)


class EventChapterLocked(CustomAction):
    """等不到可点的「阅读」按钮 = 当前话未解锁(PT 不足或前置剧情未读)。

    用户 2026-10-31 定的口径:PT 不够时后面的话都点不动,继续试没有意义,
    直接停并说明原因;下次运行仍从这一话重试。
    """

    def run(self, context, argv: CustomAction.RunArg):
        _STATE["blocked"] = "locked"
        _save_progress("locked")
        logging.error(
            "活动故事暂停：当前话尚未解锁（活动 PT 不足或前面的剧情未读），"
            "本次已读 %d 话；活动 PT 达标后再次点击继续。", _STATE["read"])
        # 返回 True 再按 next 走到 stop:返回 False 会触发 MAA 的 error handling
        # 重跑 entry(见 EventChapterPickClick 的注释)。
        return CustomAction.RunResult(True)


class EventStoryNotHome(CustomAction):
    """入口识别失败:不在活动主页。不做盲点,直接停。"""

    def run(self, context, argv: CustomAction.RunArg):
        _STATE["entry_failed"] = True
        logging.error("未检测到活动主页的「活动故事」入口，"
                      "请先进入活动主页再点击开始。")
        return CustomAction.RunResult(True)


class EventStoryFinish(CustomAction):
    """正常收工:列表滚完没有未读话。

    只落盘,不打收工日志 —— 收尾文案统一由 autodori._main_impl 按
    「入口失败/未解锁/无未读/读到底」四种结局输出,这里再打会重复一条。
    """

    def run(self, context, argv: CustomAction.RunArg):
        _save_progress()
        logging.debug("活动故事收工:本次读 %d 话", _STATE["read"])
        return CustomAction.RunResult(True)

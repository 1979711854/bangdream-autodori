import argparse
import datetime
import json
import logging
import os
import random
import re
import signal
import string
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter, deque
from pathlib import Path
from typing import Optional, Union

import requests

data_path = Path("data")
data_path.mkdir(exist_ok=True)
cache_path = Path("cache")
cache_path.mkdir(exist_ok=True)
config_path = Path("data/config.yml")
Path("debug").mkdir(exist_ok=True)
if not config_path.exists():
    config_path.touch()
    config_path.write_text("{}", encoding="utf-8")


import cv2
import numpy as np
from fuzzywuzzy import fuzz as fzwzfuzz
from maa.context import Context
from maa.controller import AdbController
from maa.custom_action import CustomAction, CustomRecognitionResult
from maa.custom_recognition import CustomRecognition
from maa.define import RectType
from maa.notification_handler import NotificationHandler, NotificationType
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import AdbDevice, Toolkit
from minitouchpy import (
    MNT,
    MNTEvATive7LogEventData,
    MNTEvent,
    MNTEventData,
    MNTServerCommunicateType,
)

import player
from api import BestdoriAPI
from chart import Chart, PlayRecord
from challenge import ChallengeCPRecognition, SelectChallengeCP, challenge_overrides, click
from story import (
    DEFAULT_MAX_STORIES,
    StoryAvatarClick,
    StoryInsufficient,
    StoryRewardOk,
    story_insufficient,
    story_overrides,
    story_read_count,
)
import envcheck
from util import *

MIN_LIVEBOOST = 1
LIVEMODE = "freelive"
CHALLENGE_FINISH = "home"
DIFFICULTY = "hard"
# ---- 超高难度 SPECIAL 活动(限时单曲) ----
# 活动入口由玩家手动进入:玩家把界面停在「开演前的确认页」,脚本从那里接管
# (见 assets/resource/pipeline/special.json 的 special_wait),打完固定的
# **一首**就收尾停止。曲目与难度都固定,所以整条流程不选曲、不选难度 —— 这是
# 与常规挖矿流程唯一的实质差别。SPECIAL_MODE 由 `--mode special` 打开。
SPECIAL_MODE = False
# ---- 一键清理日常(Issue #4) ----
# 流程分三段,开关各自独立(结构由用户 10-09 定):
#   ① 启动游戏 + 点掉开场公告/赠送弹窗(每日签到奖励也在这里领取)
#      —— **必经流程,没有开关**:不点掉就进不了主界面,做成开关是误导。
#   ② daily.auto_clear_fire 开启时:清火阶段复用常规打歌主循环,火量不足即视为
#      清火完成(HandleLiveBoost 走清火分支,只 StopTask 不请求进程退出)。
#      **判据是火量,不是生命值** —— 别把收工点挪回 HandleLifeExhausted。
#   ③ daily.auto_free_pull 开启时:每日免费三抽。三抽固定 3 次(游戏内置每日上限)。
_DAILY_CLEAR_FIRE = False
_DAILY_FREE_PULL = False
#: 本次进程跑的是不是日常模式(--mode daily)。日常模式下 _get_override_pipeline
#: 要把「选择演出」界面的返回节点接回 daily_home(走完整阶段1:火罐检查+签到弹窗),
#: 而 main 模式(命令行调试/挑战演出)保持默认的 start_app 重试。
#: 2026-10-17 GUI 重构:顶栏「开始自由演出」已删除,日常是唯一的 GUI 打歌入口,
#: main 模式只留给命令行调试与挑战演出。
_DAILY_MODE = False
#: 本次进程跑的是不是「阅读角色故事」模式(--mode story,2026-10-25 新增)。
#: 用户在游戏里筛好「小故事未读」并停在「乐队-培养」页,bot 从列表左上角
#: 依次读;流程与模板见 src/story.py 与 pipeline/story.json。
_STORY_MODE = False
#: 读哪一种故事:member=小故事,memoir=回忆小故事(两种按钮并排,模板不同)
_STORY_TYPE = "member"
#: 本轮阅读上限(--mode story 启动时从 config.yml 的 story.max_stories 读,
#: 默认 50;GUI 不暴露此配置)
_STORY_LIMIT = DEFAULT_MAX_STORIES
#: 清火与三抽**同时**开启时的交替模式(用户 2026-10-17 定的新流程):
#: 每成功打完一首歌,检查一次每日免费三抽,三抽完再回去继续打歌,
#: 直到火量不足收工。只有两个开关同时打开才生效。
_DAILY_PULL_EACH_SONG = False
#: 清火阶段刚被 HandleLiveBoost 判定"火量不足"(_run_daily_flow 的循环出口)。
_daily_fire_exhausted = False
#: 日常入口火罐检查(用户 2026-10-17 需求):阶段1 回主页时火罐就是 0,
#: 整个日常直接停止 —— 与打歌循环里的火量判定是两个独立入口。
_daily_boost_zero = False
#: 本次进程内已确认"今日免费三抽已做完 / 当前不可抽"。由管线节点
#: daily_pull_done / daily_pull_locked 上的 MarkDailyPullDone 动作置位。
#: 背景(用户 2026-10-17):每首打完都跑一遍三抽太慢(每首白耗 20-40s),
#: 标记后每首打完只跳过,收尾时再复核一次。
_daily_pull_done = False
#: 交替模式下 main 任务是否"打完一首就停(回主页)"。
#:    True  = 尚未确认三抽:每首打完必须回主页,才能进抽卡页检查(_get_override_pipeline
#:            里把结算/打崩三处 next 改写成 daily_song_done)。
#:    False = 三抽已确认完成:改用原生 main 连打链(结算页点「再次演出」→ 选曲 → 开打),
#:            不再逐首回主页 —— 用户 2026-10-17 反馈"每次打完都回主界面太麻烦"。
#: 只有 _DAILY_PULL_EACH_SONG(清火+三抽同开)时才有意义。
_daily_song_stop = True
#: 本次进程内**实际开打**的歌曲序号(从 1 开始),只用于日志显示。
#: 2026-10-17 用户实测反馈:原来只在每个 main 任务前打「自动清火:第 N 轮打歌」,
#: 但①阶段1(daily)里 start_app 兜底可能已经打掉一首(游戏没停在主页时),
#: ②三抽确认后进入连打阶段、一个 main 任务里连打好几首 —— 两处都会让编号
#: 与实际顺序对不上。计数下沉到 play_song(每首真正开打时 +1)才是准的。
_song_play_seq = 0
#: 交替模式下单轮打歌的安全上限,防止 main 任务因异常反复重启却永不收工。
_DAILY_MAX_ROUNDS = 60
#: main 任务「没打到歌就正常结束」的最大容忍次数。弹窗类管线节点(如 2026-10-19
#: 教程弹窗 tutorial_close)没有 next,在链中途命中会把整个 main 任务"成功"带走,
#: 一首都没打、纯清火/交替模式都静默收工 —— 此时重试而不是放弃;超过次数说明
#: 入口画面不可用(异常弹窗每轮都断头),停止清火流程,防止无限重试。
_MAIN_NO_PLAY_MAX = 3
# 默认曲目:简中客户端标题(曲库 musicTitle 下标 3)。也可写纯数字曲目 id,
# 见 resolve_special_song()。换活动批次时改这里或 GUI 下拉。
DEFAULT_SPECIAL_SONG = "[超高难易度 新SPECIAL] SENSENFUKOKU"
OFFSET = {"up": 0, "down": 0, "move": 0, "wait": 0.0, "interval": 0.0}
PHOTOGATE_LATENCY = 30
DEFAULT_MOVE_SLICE_SIZE = 10
MAX_FAILED_TIMES = 10
CMD_SLICE_SIZE = 100

config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
# Optional timing tuning via data/config.yml:
#   timing:
#     photogate_latency_ms: 30   # ms from first-note detection row to judgement line
_timing_cfg = config.get("timing", {}) if isinstance(config, dict) else {}
if _timing_cfg.get("photogate_latency_ms") is not None:
    PHOTOGATE_LATENCY = int(_timing_cfg["photogate_latency_ms"])
    print("PHOTOGATE_LATENCY set to {}ms".format(PHOTOGATE_LATENCY))
maaresource = Resource()
maaresource.register_custom_recognition("ChallengeCPRecognition", ChallengeCPRecognition())
maaresource.register_custom_action("SelectChallengeCP", SelectChallengeCP())
maaresource.register_custom_action("StoryAvatarClick", StoryAvatarClick())
maaresource.register_custom_action("StoryRewardOk", StoryRewardOk())
maaresource.register_custom_action("StoryInsufficient", StoryInsufficient())


# ---------------------------------------------------------------------------
# 卡死看门狗(用户 2026-10-17 定的 L2 机制:兜底动作 1A + 60s 阈值,不做 L3 总超时)
#
# 背景:实机不可能把所有异常弹窗/界面都测到 —— 这类情况的共同特征是"少数几个
# 节点在原地反复命中、流程不推进"(2026-10-17 实测:「想精进演出技巧的话」弹窗
# 挡住返回箭头,daily→start_app→daily 空转)。MAA 的节点识别回调给了观测点:
#   同一节点在 60s 滑动窗口内命中 >= 8 次  => 判定卡死
# 正常流程不会触发:打歌单节点要阻塞一两分钟,三抽每步只命中一两次。
#
# 兜底按强度递进,绝不无限乱点(2026-10-26 #400 事故后重排):
#   1) 置 _STUCK_EVENT 通知等待循环自行退出 —— 卡点在 Play 等长循环里时,
#      post_stop 停不掉运行中的 CustomAction、返回键也够不着,唯一能从循环
#      里出去的就是循环自己(协作式解救);
#   2) 循环没响应 => 点已知弹窗按钮(「演出失败」的「退出」,纯按钮弹窗
#      不响应返回键,2026-10-26 实测按 4 次零反应);
#   3) 仍无变化 => 按一次安卓返回键(绝大多数弹窗/子页面都响应),等冷却;
#   4) 还卡 => post_stop 停任务 + warning,交给外层统一收尾。
#
# 白名单:故事/公告的"下一步"连点、三抽滚动、随机兜底点击本身就设计成可重复
# 命中,不能算卡死。special(故事)模式不启动本看门狗。
# ---------------------------------------------------------------------------

_WATCHDOG_STATIC_S = 75.0        # 画面与脚本都没有任何进展持续多久判卡死
_WATCHDOG_BACK_COOLDOWN_S = 4.0  # 两次返回键的最小间隔
_WATCHDOG_SCAN_INTERVAL_S = 2.0  # 后台扫描周期
#: 解救总轮数 = 事件通知 + 点已知弹窗按钮/返回键 + 返回键。到顶 post_stop。
_WATCHDOG_RESCUE_ROUNDS = 3
_WATCHDOG_SAMPLE_STEP = 8        # 采样图每隔多少像素取一个点
_WATCHDOG_PIXEL_DIFF = 0.8       # 采样点灰度平均差 >= 该值算"画面有变化"
# 脚本侧最近这么多秒内有活动(打歌心跳等)就**连图都不抓**:打歌期间保持零额外抓图,
# 不给时序敏感的发布线程添任何 GIL 竞争(实测单次抓图 ~5ms,虽小但没必要)。
_WATCHDOG_BUSY_QUIET_S = 10.0
#: 看门狗连续抓图失败多少次后,用 adb 子进程确认一次设备状态(掉线短路径,
#: 见 check() 内注释)。2s 一拍 × 10 次 ≈ 20s,远快于等满 75s 静止判据。
_WATCHDOG_CAP_FAIL_MAX = 10
# 抓图会被打歌线程(生命/暂停检测)和看门狗线程同时用到,串行化避免 IPC 串包
_SCREEN_CAP_LOCK = threading.Lock()
#: 抓图限时拿锁的超时。2026-10-20 凌晨场实测:MuMu 掉线时 IPC 调用会**挂死不
#: 返回**(不是立刻报错),挂死者永久持锁会把另一方拖死 —— 看门狗因此陪葬,
#: 全进程僵死零日志(01:13:31 之后无任何输出)。双方都改成限时拿锁:打歌线程
#: 短超时(卡久了吃音符时间预算),看门狗长超时(2s 一拍,等得起)。谁挂死
#: 持锁,另一方限时失败后继续活,看护能力不归零。
_CAP_LOCK_TIMEOUT_S = 1.0
_WATCHDOG_CAP_LOCK_TIMEOUT_S = 5.0

#: 卡死协作式解救事件(2026-10-26):看门狗判卡死后置位,等待类循环
#: (首音等待等)每帧检查,看到就 raise 走 on_error 链正规收尾。为什么必须
#: 协作:post_stop 停不掉**正在运行**的 CustomAction —— #400 那次按了 4 次
#: 返回键、post_stop 也发了,Play 里的 while True 照转不误。Event.clear()
#: 在 play_song 开头(每首新歌),防止上一轮的残留事件误杀下一首。
_STUCK_EVENT = threading.Event()


def _watchdog_sample():
    """给看门狗用的粗采样灰度图;抓不到(游戏没起来/IPC 忙/掉线挂死)返回 None。"""
    if current_player is None:
        return None
    # 限时拿锁(见 _CAP_LOCK_TIMEOUT_S 注释):持锁方挂死在 IPC 里时,看门狗
    # 绝不能跟着陪葬 —— 它是全进程最后的看护者。
    if not _SCREEN_CAP_LOCK.acquire(timeout=_WATCHDOG_CAP_LOCK_TIMEOUT_S):
        logging.warning(
            "看门狗抓图锁 %.0fs 拿不到,持锁方疑似挂死在 IPC 上,本轮跳过",
            _WATCHDOG_CAP_LOCK_TIMEOUT_S,
        )
        return None
    try:
        frame = current_player.ipc_capture_display()
        if frame is None:
            # 掉线时 MuMu IPC 返回 None(player.py 已显式判 None);
            # 不能让 None 流进 np.ascontiguousarray —— 会造出 0 维数组,
            # 在下面的 [::step, ::step] 处炸出难懂的下标错误
            return None
        arr = np.ascontiguousarray(frame, dtype=np.uint8)
    except Exception as e:  # noqa: BLE001 —— 看门狗自己绝不能把主流程带崩
        logging.debug("看门狗抓图失败: %s", e)
        return None
    finally:
        _SCREEN_CAP_LOCK.release()
    arr = arr[::_WATCHDOG_SAMPLE_STEP, ::_WATCHDOG_SAMPLE_STEP]
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    return arr.astype(np.float32)


def _sample_diff(a, b) -> float:
    if a.shape != b.shape:
        return 999.0
    return float(np.abs(a - b).mean())


#: 卡死解救允许点的高危弹窗按钮。**只放「演出失败」的「退出」**:模板
#: 复用 common/button/exit(管线 exit_button 节点同款,粉/白两版)。「继续
#: (复活)」要花星石、「重新开始」会让卡死的 Play 更乱,都不进白名单。
_STUCK_MODAL_TEMPLATES = (
    "common/button/exit/pink.png",
    "common/button/exit/white.png",
)
_stuck_modal_tpl_cache: dict = {}


def _load_stuck_modal_templates() -> dict:
    """懒加载退出按钮模板(cv2.imread 是 BGR)。读不到就返回空,不参与解救。"""
    if _stuck_modal_tpl_cache:
        return _stuck_modal_tpl_cache
    for name in _STUCK_MODAL_TEMPLATES:
        path = os.path.join("assets", "resource", "image", *name.split("/"))
        tpl = cv2.imread(path, cv2.IMREAD_COLOR)
        if tpl is not None:
            _stuck_modal_tpl_cache[name] = tpl
    return _stuck_modal_tpl_cache


def _click_stuck_modal_button() -> bool:
    """看门狗第 2 轮解救:整幅画面找「演出失败」弹窗的「退出」按钮并点掉。

    返回键对这种纯按钮弹窗无效(2026-10-26 实测按 4 次零反应),必须点按钮。
    匹配不到返回 False,调用方落到返回键。用 cv2 模板匹配而不是复用管线
    节点:看门狗线程同步 post 管线动作容易死锁(见 HandleLiveBoost 注释),
    而且任务线程被 Play 占着时节点根本轮不到执行。
    时序安全:只在画面静止 75s 后才会被调用 —— 打歌中音符一直在动,到不了
    这里,不存在与 minitouch 发布线程抢点的问题;首音等待阶段也还没开始
    发布命令。点击走 _click_at_box(MAA post_click,失败退 adb tap)。
    """
    tpls = _load_stuck_modal_templates()
    if not tpls:
        return False
    if not _SCREEN_CAP_LOCK.acquire(timeout=_WATCHDOG_CAP_LOCK_TIMEOUT_S):
        return False
    try:
        frame = current_player.ipc_capture_display()
    finally:
        _SCREEN_CAP_LOCK.release()
    if frame is None:
        return False
    # IPC 帧是 RGB,模板是 BGR,先转一致再匹配(见 _snap 注释)
    frame = np.ascontiguousarray(frame[:, :, :3][:, :, ::-1], dtype=np.uint8)
    for name, tpl in tpls.items():
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        if maxv >= 0.8:
            h, w = tpl.shape[:2]
            box = [maxloc[0], maxloc[1], w, h]
            logging.warning(
                "卡死解救:识别到「退出」按钮(%s score=%.3f box=%s),点击",
                name, maxv, box,
            )
            return _click_at_box(box)
    return False


class _StuckWatchdog(NotificationHandler):
    """卡死看门狗:「画面长期静止 + 脚本侧无进展」=> 分轮解救,无效则停任务。

    2026-10-26 解救链重排(#400 事故:卡在 Play 长循环里,按 4 次返回键 +
    post_stop 全部无效):
      第 1 轮 置 _STUCK_EVENT 通知等待循环自行退出(协作式解救);
      第 2 轮 点「演出失败→退出」等已知弹窗按钮(纯按钮弹窗不响应返回键);
      第 3 轮 按安卓返回键(多数弹窗/子页面响应;打歌中会误弹暂停,所以
              排在最后 —— 而打歌中画面不会静止,本来也到不了这轮);
      仍无效  post_stop 停任务,交给外层统一收尾。

    2026-10-17 重写 —— 老实现的两个判据在本 MAA 版本上全部失效:
      MAA 4.4.0 只把**任务级**通知(Tasker.Task.*)送到 Python,**节点级**
      (Node.Recognition.* / Node.NextList.*)一条也收不到(实测见本机脚本
      _diag_notify.py:跑一个必然命中的节点,只打印出两条 Tasker 事件)。
      于是「同一节点 60s 内命中 ≥8 次」和「90s 没有任何节点命中」都退化成与节点
      无关的定时器:后者等于「启动 90 秒后按一次返回键」,而打歌中按返回键会让
      游戏弹【暂停】界面 —— 2026-10-17 12:59 那次"莫名其妙暂停"就是这么来的。
    新判据(不依赖节点通知):
      · 每 2s 抓一张粗采样图,画面有变化就刷新活动时间;
      · 打歌线程每 1s 调 ping() 刷新活动时间(音符本来就在动,双保险);
      · 连续 _WATCHDOG_STATIC_S 秒既没画面变化也没 ping => 按返回键;
      · 按完进入同样长的观察窗口,连续 2 次无效 => post_stop 停任务。
    剧情资源下载时进度数字在变,算"有变化"(继续等);真卡在 0% 才会被按返回键
    救出来 —— 这是刻意的,总比永久挂着好。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._prev_sample = None
        self._backs = 0
        self._last_back = 0.0
        self._observe_until = 0.0      # 按下返回键后的观察窗口截止时间
        self._cap_fail = 0             # 连续抓图失败次数(掉线短路径计数)

    def ping(self) -> None:
        """脚本侧"还在推进"(打歌中的检测节拍等)时调用。"""
        with self._lock:
            self._last_activity = time.time()
            self._backs = 0
            self._prev_sample = None

    def on_tasker_task(self, noti_type, detail):
        # 任务级通知在这个 MAA 版本是**能收到**的,同样算一次进展
        self.ping()

    def check(self) -> bool:
        """扫描一次。返回 True = 已停任务(调用方应结束看门狗线程)。"""
        now = time.time()
        if now < self._observe_until:
            return False
        with self._lock:
            quiet_for = now - self._last_activity
        if quiet_for < _WATCHDOG_BUSY_QUIET_S:
            # 脚本侧刚有进展(打歌心跳/任务切换):这个窗口内连抓图都省掉,
            # 打歌期间看门狗的额外开销 = 0。
            return False
        sample = _watchdog_sample()
        # 掉线短路径(2026-10-20 凌晨场):MuMu 掉线时 IPC 挂死或返回 None,
        # 抓图连续失败;此时主线程多半也僵在 IPC/minitouch 上,若只等 75s 静止
        # 再按返回键(还要靠 adb),全程约 4 分钟且日志全无。连续抓图失败后用
        # adb 子进程确认一次设备状态 —— subprocess 自带超时,绝不挂死;查不到
        # 设备就登记退出 + post_stop,由退出看门狗接管硬退出,僵死也能收尾。
        if sample is None:
            self._cap_fail += 1
        else:
            self._cap_fail = 0
        if self._cap_fail >= _WATCHDOG_CAP_FAIL_MAX:
            state = _game_process_running()
            if state is None:
                logging.warning("模拟器连接断开(连续抓图失败且 adb 查不到设备),停止任务")
                _request_exit("模拟器连接断开")
                _force_post_stop()
                return True
            # 设备还在(只是抓图抽风):重置计数,交给静止判据正常走
            self._cap_fail = 0
        with self._lock:
            changed = sample is not None and (
                self._prev_sample is None
                or _sample_diff(self._prev_sample, sample) >= _WATCHDOG_PIXEL_DIFF
            )
            if sample is not None:
                self._prev_sample = sample
            if changed:
                self._last_activity = now
                self._backs = 0
            last_activity = self._last_activity
            backs = self._backs
        if now - last_activity <= _WATCHDOG_STATIC_S:
            return False                  # 画面在动 / 脚本在跑,正常
        if now - self._last_back < _WATCHDOG_BACK_COOLDOWN_S:
            return False                  # 冷却中,别连按
        self._last_back = now
        self._backs = backs + 1
        # 每轮解救后给同样长的观察窗口,否则 4s 冷却一过就会立刻连按
        self._observe_until = now + _WATCHDOG_STATIC_S
        if backs == 0:
            # 第 1 轮:协作式解救 —— 只置事件,不点不按。Play 等长循环每帧
            # 检查该事件,看到就 raise 走 on_error 链,由管线正规收尾(点
            # 「退出」带二次确认链,比看门狗盲点安全);若卡点不在长循环里
            # (管线空转),事件无人消费,75s 后落到第 2 轮。
            _STUCK_EVENT.set()
            logging.warning(
                "卡住保护:%.0fs 内画面与 MAA 都没有任何进展,"
                "已通知等待循环自行退出(1/%d)",
                _WATCHDOG_STATIC_S, _WATCHDOG_RESCUE_ROUNDS,
            )
            return False
        if backs == 1:
            # 第 2 轮:循环没能自行退出(卡点在管线/线程真死)。返回键对
            # 「演出失败」这类纯按钮弹窗无效,先试点已知弹窗按钮。
            logging.warning(
                "卡住保护:等待循环未响应,改为点击已知弹窗按钮(2/%d)",
                _WATCHDOG_RESCUE_ROUNDS,
            )
            if _click_stuck_modal_button():
                return False
        if backs < _WATCHDOG_RESCUE_ROUNDS:
            # 第 2 轮(没认出弹窗)/第 3 轮:返回键兜底。送达与否必须可见:
            # 2026-10-26 按 4 次返回键画面零反应,当时 adb 失败只写 DEBUG
            # 甚至不写,事后无法区分「键没送到」和「画面不响应」。
            r = _adb_shell(["input", "keyevent", "4"])
            if r is None:
                logging.warning(
                    "卡住保护:返回键可能未送达(adb 调用失败,设备掉线?)"
                )
            else:
                logging.warning(
                    "卡住保护:%.0fs 内画面与 MAA 都没有任何进展,"
                    "按返回键(%d/%d)",
                    _WATCHDOG_STATIC_S, self._backs, _WATCHDOG_RESCUE_ROUNDS,
                )
            return False
        logging.warning(
            "卡住保护:%d 轮解救都没有效果,停止任务",
            _WATCHDOG_RESCUE_ROUNDS,
        )
        _force_post_stop()
        return True


_STUCK_WATCHDOG = _StuckWatchdog()
maatasker = Tasker(notification_handler=_STUCK_WATCHDOG)
maacontroller: AdbController = None
device: AdbDevice = None
current_player: player.Player = None
current_orientation: int = 0
mnt: MNT = None
try:
    all_songs: dict = BestdoriAPI.get_song_list()
except Exception as _e:  # noqa: BLE001 —— 这里**绝不能**让异常逃出去
    # ⚠️ 这一行在模块级,`_main_impl` 的 try 之外 —— 一旦抛出去,PyInstaller 会直接
    # "Failed to execute script 'autodori'",进程无声无息死掉,GUI 里看起来就是
    # 「点了开始没反应」(2026-10-10 用户实报,日志:`host='bestdori.com' ...
    # ConnectTimeout` + MaxRetryError)。
    # 改用与 init_maa 相同的收尾方式:打一条能看懂的中文再干净退出(CRITICAL 会被
    # GUI 的「关键事件」显示出来)。
    logging.fatal(
        "启动失败:无法获取曲库(bestdori.com)。请检查网络或代理后重试,"
        "确认能打开 https://bestdori.com/api/songs/all.5.json 再运行。\n原因:%s",
        _e,
    )
    sys.exit(1)
all_song_name_indexes: dict[str, str] = {
    list(filter(lambda title: title is not None, sinfo["musicTitle"]))[0]: sid
    for sid, sinfo in all_songs.items()
}
# 再补一层简体中文标题(下标 3)。游戏客户端是简中,部分变体条目**只有中文标题带标注** ——
# 例如 #484 中文名是「キズナミュージック♪（支持3D演出模式）」而日文名是
# 「…（3Dライブモード対応）」,#763「熱色スターマイン(平行歌曲)」等平行曲同理。
# 只收日文时这些条目会退化到同名的基础曲(2026-09-16 实测:按错误谱面打歌)。
# 用 setdefault:只为上面那份索引**补空缺**,不覆盖已有键 —— Bestdori 的中文数据本身
# 有冲突(#597 的中文名与 #486 完全同名),覆盖会改变原本正确的判定。
for _sid, _sinfo in all_songs.items():
    _titles = _sinfo.get("musicTitle") or []
    _zh_title = _titles[3] if len(_titles) > 3 else None
    if _zh_title:
        all_song_name_indexes.setdefault(_zh_title, _sid)

# 标题 -> 同名条目列表(仅保留 ≥2 个的)。曲库 818 首里只有 8 组同名不同 id。
# 旧索引对同名键只留最后一个,另一首直接不可达 —— 2026-09-17 实测 `閃光` 只剩
# #467(Afterglow×レイヤ,EXPERT 27),Roselia 版 #410(EXPERT 26)被丢掉,
# 抽到 Roselia 版就必然按 #467 的谱面打,整首对不上。
#
# 8 组里 4 组(優勝 feat.Afterglow / HELL! or HELL? / 六兆年と一夜物語 /
# Mystic Light Quest)五档等级全同 —— 逐档 md5 比对确认它们本就是**同一份谱面**,
# 选谁都对;ときめきエクスペリエンス！(月島まりなver.) 只有 SPECIAL 差 1 级;
# 其余 3 组(閃光 / オレンジ / シル・ヴ・プレジデント)等级不同 = 谱面不同。
#
# 所以判据不是标题而是屏幕上的「乐曲等级」:该数字 = 高亮曲目在当前难度下的
# playLevel,而"等级可区分"与"谱面不同"实测完全等价。等级相同的组随便选一个,
# 等级不同的组按屏上数字挑;挑不出来就放弃本次选曲,绝不猜。
_title_to_ids: dict[str, list[str]] = {}
for _sid, _sinfo in all_songs.items():
    for _t in _sinfo.get("musicTitle") or []:
        # 只看能真正出现在索引里的标题:匹配只可能返回索引的键
        if _t and _t in all_song_name_indexes:
            _ids = _title_to_ids.setdefault(_t, [])
            if _sid not in _ids:
                _ids.append(_sid)
ambiguous_titles: dict[str, list[str]] = {
    _t: _ids for _t, _ids in _title_to_ids.items() if len(_ids) > 1
}
# 把索引里的默认值排到最前,保证"无法区分时"选中的和改动前是同一个
for _t, _ids in ambiguous_titles.items():
    _default = all_song_name_indexes.get(_t)
    if _default in _ids:
        _ids.remove(_default)
        _ids.insert(0, _default)

# 选歌界面上「乐曲等级」数字的位置(1280x720 绝对像素,未缩放)。
# 依据 debug/maa.log:该数字的框稳定落在 (1204,456,30,23) 附近,标签「乐曲等级」
# 在 (1092,455,77,24);用 debug/_exp_eval_match.py 复核,有等级数字的 48 轮样本
# 全部落在匹配到的曲目在该难度下的等级集合内。
_SONG_LEVEL_ROI = [1196, 450, 48, 32]
_DIFFICULTY_ORDER = ["easy", "normal", "hard", "expert", "special"]

current_song_name: str = None
current_song_id: str = None
# SongRecognition 选定的 (标题, 曲目 id)。同名多条目时标题无法反查唯一 id,
# 由识别阶段写入、紧随其后的 SaveSong 动作读取(PipelineTask 内同线程顺序执行)。
_resolved_song_id: Optional[tuple] = None
# 本次选中的标题可能对应的全部 id(仅同名多条目时 >1)。首音触发后用它挑
# 真正的谱面 —— 屏幕上的「乐曲等级」在部分组是同级的,分不开(2026-10-30)。
_ambiguous_song_ids: list = []
current_chart: Chart = None
play_failed_times: int = 0
callback_data: dict = {}
callback_data_lock = threading.Lock()
cmd_log_list: list[MNTEvATive7LogEventData] = []
cmd_log_list_lock = threading.Lock()
current_version = None

# 服务端滞后反馈补偿(2026-10-23,issue #8 修复方向 A):
# _adjust_offset 是按「命令开销估计」的开环补偿,实测拉不平 ~15-80ms/曲的
# 累计漂移(FULL FIRE BIRD 音符尾段 80ms → slow=315)。这里直接闭环:
#   - 测量端(mnt_callback):**绝对对齐**口径 —— 在线维护理想时间线指针,
#     lag = start - ideal(与 dump 同口径)。基准=理想时长(chart.wait_ideal_ms)。
#     ⚠ 四版口径踩坑(详见 chart.py __init__ 注释):①字符串解析值→账本虚高
#     ~900ms 翻车;②理想时长+逐条增量→自限但存量挂账;③实际生效时长+逐条
#     增量→批间空闲被记成漂移,无自限正反馈,账本 +1358ms 推早 1.3s 翻车。
#     结论:逐条增量口径与补偿天然耦合,反馈测量只能用绝对对齐。
#   - 执行端(play_song):「补足到池」—— 每批把池水位补到 lag(lag −
#     _a2c_offset),由 chart.py 既有机制按每条 wait ±1ms(OFFSET_LIMIT)
#     排水。排水生效→残差降→下批补足归 0,自限;存量由排水逐批追回。
# 反馈只补「实测残差」:_adjust_offset 已生效的部分不会重复计入(它生效后
# 滞后不再增长,增量自然为 0),两个控制器是协作而非叠加。
#
# ⚠ 2026-10-23 用户决定回退(总开关置 False):三场实测曲内漂移已被压住
# (音符增长 -12/-4.5/+9.6ms,仿真口径达标),但用户体感「打歌更不好」——
# 三场 fast=9/6/68 且 slow 全 0,是**整场整体偏早**,属 photogate 全局校准轴
# (反馈只修曲内相对漂移,不修全局偏移,两者正交不冲突但叠加影响体感)。
# 保留全部测量/诊断代码(只读、不碰时序),注入点由本开关控制。
# 2026-10-23 上午 A/B 对照实验(用户确认):重启反馈 —— 10:29 回退版基准场
# 非 FULL 曲也爆 SLOW(Off we go. 音符增长 +103ms → slow=337,Tempest/人マニア
# +84ms),photogate 校准同场 36→6 剧烈波动(全局轴),与曲内漂移轴正交。
# 反馈版 11:41/12:23 两场:短中曲 12 首 slow 全 0(达标),但 FULL 曲
# (CiRCLE THANKS MUSiC♪)仍 +55ms/slow=274 —— 定位出第二版「存量挂账」
# 缺陷(见上②)。第三版(基准=实际生效时长)当场翻车:批间空闲被逐条
# 增量口径记成漂移,账本 +1358ms(dump -75ms),推早 1.3s 批量 FAST。
# 第四版(本版):测量端绝对对齐(dump 同口径)+ 注入端补足到池,
# 理论上同时解决「挂账」与「正反馈」;实机验收:lagdump「音符曲内增长」
# 压回 ±10ms(FULL 曲也含),在线 lag 与 dump 曲线应一致(同口径)。
_LAG_FEEDBACK_ENABLED = True
# lag:绝对对齐口径的当前残差(= dump 同口径);ideal:理想时间线指针;
# last_start/last_sched:滚动状态。第四版注入端为补足式,不再需要 applied。
# 2026-10-28 加两道保护后多三个字段:
#   off          本曲已停注入(回执积压判死,lag 不可信)
#   backlog_since 积压首次超限的时刻(用来算持续时长)
#   backlog_peak  本曲回执积压峰值(歌尾打一行 INFO,用来校准上面的阈值)
#   overshoot_n  本曲被「单批补足超限」丢弃的批次数(取证用)
_lag_fb = {"lag": 0.0, "ideal": None, "last_start": None, "last_sched": 0.0,
           "off": False, "backlog_since": None, "backlog_peak": 0,
           "overshoot_n": 0,
           # 2026-10-28 加:测量端退化的两个直接计数(只读,不碰时序)。
           # 背景:同一天两场,一场线上 lag 全程 ±5ms 而 dump 曲内增长 -98ms
           # (ゼッタイ宣言,越打越快),另一场线上 lag 涨到 243ms 却在丢弃 75 批
           # (テレキャスタービーボーイ,实测 fast=54 而非 SLOW)——两个方向都飘,
           # 共同指向「在线理想时间线指针不可信」。要判是哪种退化,就得数:
           #   wait_recv  成功从 wait_ideal_ms 配对弹出的 'w' 回执条数
           #   fallback_n 队列空、退回命令串实际值的条数(该值已含补偿,比理想短,
           #              拿它当理想额度 = 把漂移吃成 0,lag 直接失真)
           # 2026-10-29 加两个「被跳过的等待」记账字段(修上面那个「指针不可信」):
           #   skip_applied 已补进理想指针的欠账合计(ms),诊断用
           #   pending_skip 锚定前(首条命令还没回执)攒下的欠账,锚定时一次补进
           "wait_recv": 0, "fallback_n": 0,
           "skip_applied": 0.0, "pending_skip": 0.0, "skip_warned": False}

# ---- 滞后反馈的两道保护(2026-10-28) ----
# 实机崩局取证(2026-10-28 00:13~00:19 三首,gate 同为 60):
#   #320 失配 11/15717 → miss 4;#763 失配 2719/16964 → miss 12;
#   #263 失配 322/5150 → 服务端 lag 从 12ms 涨到 117ms → 生命耗尽。
# 机理:jlog 的 'w' 回执丢了 → wait_ideal_ms 只进不出(弹出发生在回执里)
# → ideal 指针少推进 → lag 被算**大** → 「补足到池」把这个虚高值注回去
# → wait 被缩短 → 时间线被推早 → 批量 FAST/miss,正反馈一路到崩。
# 同一晚 00:27 那首失配只有 14 条时全 PERFECT:失配是间歇性爆发、不是长曲
# 固有属性,所以只能做事后保护,不能靠「这首没崩」推断下首没事。
# 保护①(挡住 0:19 那种崩):回执积压持续超限 = 回执在丢,lag 已不可信,
#   **本曲立刻停注入**,并清空池水 —— 不清的话虚高池水还会继续按
#   OFFSET_LIMIT 排水,时间线继续往早推。停注入后回退开环 _adjust_offset,
#   最差是偏晚(SLOW),不会批量 miss。
# 保护②(挡虚高注入):正常每批新漂移 ~2ms,单批要补 30ms 以上只可能是
#   测量异常。注早了要命(FAST/miss),不注最多偏晚,所以丢弃并记数。
_LAG_FB_MAX_BACKLOG = 400           # wait 回执积压上限(正常只领先 1~2 批)
_LAG_FB_BACKLOG_HOLD_S = 2.0        # 持续超限多久判定「回执真的在丢」
_LAG_FB_MAX_BATCH_INJECT_MS = 30.0  # 单批补足上限,超过当测量异常丢弃
# wait 欠账告警线(2026-10-29)。**只告警,不停注入、不清池水。**
# 历史:这一天先按「欠账 ≥20ms 就停注入 + 清池」做的(当时指针对不上,反馈
# 看不见这笔账),上线后实测它会**把歌推晚**:#489 第一局曲内 +37.7ms / 418
# 个 SLOW,#649/#489 后两局 +20/+37ms。
# 根因在测量端:在线理想指针只按「真正发出的 wait」推进,被跳过的 wait 不长
# 命令、指针就永久落后,lag 被算成假「偏晚」→ 停注入后开环补偿按这条路继续
# 往晚走。指针已在 play_song 里补齐(见 lag_feedback_apply_skipped),欠账现在
# 是**可见且可偿还**的真实残差,反馈该照常去排水 —— 所以这里只留一条告警。
# 判据仍必须用**时长**:#684 焚音打「补偿抽干」只数到 8 条,欠账却 73ms。
_LAG_FB_SKIP_WARN_MS = 50.0         # 单曲 wait 欠账告警线(ms)


def reset_lag_feedback():
    """每首歌开始时清零(与 cmd_log_list.clear() 同点调用)。"""
    global _lag_fb
    _lag_fb = {"lag": 0.0, "ideal": None, "last_start": None, "last_sched": 0.0,
               "off": False, "backlog_since": None, "backlog_peak": 0,
               "overshoot_n": 0, "wait_recv": 0, "fallback_n": 0,
               "skip_applied": 0.0, "pending_skip": 0.0, "skip_warned": False}


def _lag_fb_snapshot() -> dict:
    """取本曲反馈状态的快照,给 dump 线程做「在线 lag vs dump lag」对照。

    2026-10-28 加:_dump_server_lag_async 是 daemon 线程,要等 jlog 静默(≤20s)
    才出数;下一首开始时 reset_lag_feedback 会把 _lag_fb 清零,所以必须在本曲
    收尾**当场**快照,不能在线程里读全局。只读,不碰时序。
    """
    return {
        "lag": _lag_fb["lag"],
        "wait_recv": _lag_fb["wait_recv"],
        "fallback_n": _lag_fb["fallback_n"],
        "peak": _lag_fb["backlog_peak"],
        "overshoot_n": _lag_fb["overshoot_n"],
        "off": _lag_fb["off"],
        "skip_applied": _lag_fb["skip_applied"],
    }


def lag_feedback_batch_delta(chart, batch_no: int, now=None) -> float:
    """算出本批「补足到池」的池增量;返回 0 表示本批不注入(含两道保护)。

    调用点:play_song 每批发布前、持 callback_data_lock 时 —— 与 _lag_fb /
    wait_ideal_ms 的其他读写同锁,这里读队列长度是安全的。
    chart 传当前 Chart(需要 `wait_ideal_ms` 与 `_a2c_offset`),None 视为无表。
    now 只给离线测试注入时钟用。
    """
    if chart is None or not _LAG_FEEDBACK_ENABLED or _lag_fb["off"]:
        return 0.0
    if now is None:
        now = time.perf_counter()

    # wait 欠账告警(2026-10-29):只告警,照常注入 —— 欠账已由发布侧补进理想
    # 指针,反馈看得见也排得掉;以前在这里停注入会把整首歌推晚(见常量注释)。
    # 每曲只报一次,免得密集曲每批刷屏。
    # 文案(用户 2026-10-29 定):一行说完「多少、会怎样、别慌」;条数/是否抽干
    # 这些诊断数字放在曲末汇总里(那里本来就打),别在这里堆。
    skipped = getattr(chart, "skipped_wait_ms", 0.0)
    if skipped >= _LAG_FB_SKIP_WARN_MS and not _lag_fb["skip_warned"]:
        _lag_fb["skip_warned"] = True
        logging.warning(
            "滞后反馈: 本曲有 %.1fms 的等待被跳过,时间线偏短,补偿会逐步补回",
            skipped,
        )

    # 保护①:wait 回执积压(队列长度 = 已发 wait 数 − 已收 jlog 回执数)
    backlog = len(getattr(chart, "wait_ideal_ms", ()) or ())
    if backlog > _lag_fb["backlog_peak"]:
        _lag_fb["backlog_peak"] = backlog
    if backlog <= _LAG_FB_MAX_BACKLOG:
        _lag_fb["backlog_since"] = None
    else:
        since = _lag_fb["backlog_since"]
        if since is None:
            _lag_fb["backlog_since"] = now
            logging.warning(
                "滞后反馈: wait 回执积压 %d 条(>%d),lag=%.1fms 可能虚高;"
                "持续 %.1fs 未回落就本曲停注入",
                backlog, _LAG_FB_MAX_BACKLOG, _lag_fb["lag"],
                _LAG_FB_BACKLOG_HOLD_S,
            )
        elif now - since >= _LAG_FB_BACKLOG_HOLD_S:
            _lag_fb["off"] = True
            stale = getattr(chart, "_a2c_offset", 0.0)
            chart._a2c_offset = 0.0
            logging.warning(
                "滞后反馈: 本曲停止注入 —— wait 回执积压 %d 条(>%d)持续 %.1fs,"
                "lag=%.1fms 已不可信;清空池水 %.1fms,退回开环补偿(批#%d)",
                backlog, _LAG_FB_MAX_BACKLOG, now - since, _lag_fb["lag"],
                stale, batch_no,
            )
            return 0.0

    delta = _lag_fb["lag"] - getattr(chart, "_a2c_offset", 0.0)
    if delta > _LAG_FB_MAX_BATCH_INJECT_MS:
        # 保护②:虚高注入。只拦「往早推」这一侧(正增量把 wait 缩短);
        # 负增量是池水多于残差,应用它等于往晚放,不影响安全性。
        _lag_fb["overshoot_n"] += 1
        if _lag_fb["overshoot_n"] == 1:
            logging.warning(
                "滞后反馈: 单批补足 %.1fms 超过 %.0fms 上限,本批丢弃(lag=%.1fms "
                "池=%.1fms 批#%d);后续同类丢弃只记 debug",
                delta, _LAG_FB_MAX_BATCH_INJECT_MS, _lag_fb["lag"],
                getattr(chart, "_a2c_offset", 0.0), batch_no,
            )
        else:
            logging.debug(
                "滞后反馈: 单批补足 %.1fms 超限丢弃(第 %d 次,批#%d)",
                delta, _lag_fb["overshoot_n"], batch_no,
            )
        return 0.0
    return delta if abs(delta) > 0.01 else 0.0


def lag_feedback_apply_skipped(chart, applied: float) -> float:
    """把本批新增的「被跳过的等待」补进在线理想指针,返回累计已补值。

    2026-10-29,「越打越早」的根因修复。在线估计器的 ideal 只按**真正发出的
    wait**(wait_ideal_ms 队列的 'w' 回执)推进,而被跳过的 wait(被补偿整段
    吃掉、或本身 <0.5ms 被取整掉)根本不产生命令 → 指针永久落后这一段 →
    lag = start − ideal 被算成假「偏晚」→ 反馈持续往早推 → 时间线真的越打
    越短。5 首实测:在线 lag 终值 − dump lag@100% ≈ 被跳过的时长
    (98.0/98.4、97.9/98.7、104.2/105.8、21.4/22.0、11.4/11.0)。
    dump 侧重建理想流用的是**全部** action["length"](含被跳过那条),所以补上
    之后两边同口径,欠账变成可见、可排水的真实残差(反馈会用负池水把后续
    wait 拉长,把欠的时间还回来)。

    applied 由调用方保存(每首从 0 起),因此失败/中断都不会重记;返回新值。
    ⚠ 时序:补齐发生在命令生成后、发布前,指针会领先这批命令的执行一个
    「发布提前量」(2~3s)。这段窗口里 lag 少算一个单批欠账(通常 <2ms,
    方向是「假偏早」→ 池水偏负 → wait 偏长),落在安全侧;等这批命令执行完,
    lag 回到真值。要彻底对齐得给每条命令配一份额度队列,代价与收益不成比。
    """
    if chart is None or not _LAG_FEEDBACK_ENABLED:
        return applied
    with callback_data_lock:
        total = getattr(chart, "skipped_wait_ms", 0.0)
        new = total - applied
        if new <= 0.0:
            return applied
        if _lag_fb["ideal"] is None:
            # 首条命令还没回执,锚点未建立(锚定会把指针重置成首条命令的
            # start);先记账,mnt_callback 锚定时一次补进。
            _lag_fb["pending_skip"] += new
        else:
            _lag_fb["ideal"] += new
            _lag_fb["skip_applied"] += new
        return total


def reset_callback_data():
    global callback_data
    callback_data = {
        "wait": {"total": 0, "total_offset": 0.0},
        "move": {"uncommited": 0, "total": 0, "total_offset": 0.0},
        "up": {"uncommited": 0, "total": 0, "total_offset": 0.0},
        "down": {"uncommited": 0, "total": 0, "total_offset": 0.0},
        "interval": {"total": 0, "total_offset": 0.0},
        "last_cmd_endtime": -1,
    }


reset_callback_data()


# 选歌档位(以"历史最好成绩"判定,成就不会倒退):
#   0 = 未通关(从未打过,或打过但都失败) —— 仍有首通奖励
#   1 = 已通关但未 FULL COMBO            —— 仍有 FC 奖励
#   2 = 已 FC 但未 ALL PERFECT            —— 仍有 AP 奖励
#   3 = 已 ALL PERFECT                    —— 无新奖励,不再选
# 「挖矿为主」= 优先 0/1/2 三种(都还有奖励可拿),仅排除 3(已 ALL PERFECT)。
# 池内优先级 0 > 1 > 2,用有界重抽实现。
MINED_MAX_TIER = 2
# 软优先的重抽上限:调大 = 更执着地优先未打过,但抽歌耗时上升;
# 可用 data/config.yml 的 song_strategy_reject_limit 覆盖。
DEFAULT_REJECT_LIMIT = 10
# 排除已 AP 时的重抽上限:远大于软优先,表示"确实没有可挖的了"才让步。
AP_REROLL_STREAK_MAX = 30
_selection_rejections = 0
_ap_reroll_streak = 0
# 档位表按难度缓存,避免每次识别都全表重算;保存新战绩后失效。
_tier_map_cache: dict = {}


def _tier_from_records(records) -> int:
    """把一组打歌记录折算成"历史最好档位"。

    关键:只认成功的、且 result 非空的记录。失败记录(result={})若参与计算,
    miss/bad/good/great 会全部取默认 0,从而被误判成 ALL PERFECT —— 这正是
    旧实现把仅失败过的歌永久排除的原因。
    """
    best = 0
    for rec in records:
        if not rec.succeed:
            continue
        r = rec.result if isinstance(rec.result, dict) else None
        if not r:
            continue
        miss = int(r.get("miss", 0) or 0)
        bad = int(r.get("bad", 0) or 0)
        good = int(r.get("good", 0) or 0)
        great = int(r.get("great", 0) or 0)
        if miss == 0 and bad == 0 and good == 0:
            t = 3 if great == 0 else 2
        else:
            t = 1
        if t > best:
            best = t
    return best


def _build_tier_map(difficulty: str) -> dict:
    grouped: dict = {}
    for rec in PlayRecord.select().where(PlayRecord.difficulty == difficulty):
        grouped.setdefault(str(rec.chart_id), []).append(rec)
    return {
        str(sid): _tier_from_records(grouped.get(str(sid), []))
        for sid in all_songs
    }


def _tier_map(difficulty: str) -> dict:
    """该难度下全曲库的档位表。选曲判定与目标档位共用同一份,保证口径唯一 ——
    旧实现两处各算各的(一处取首条、一处取末条),实测 93 组结果互相矛盾。"""
    if difficulty not in _tier_map_cache:
        _tier_map_cache[difficulty] = _build_tier_map(difficulty)
    return _tier_map_cache[difficulty]


def _song_tier(chart_id: str, difficulty: str) -> int:
    return _tier_map(difficulty).get(str(chart_id), 0)


def _current_target_tier(difficulty: str) -> int:
    """当前最该打的档位(0 最优)。全部 AP 时返回 3,表示已无可挖。"""
    tiers = _tier_map(difficulty)
    return min(tiers.values()) if tiers else 0


def _runtime_config() -> dict:
    """实时重读 data/config.yml。

    模块级 config 只在 import 时读一次,用户运行中在 GUI 改的开关不会生效。
    凡是「改了就该在下一首/下一次判定生效」的设置,一律走这里,与
    photogate / song_strategy 的口径保持一致。读不到就当空配置。
    """
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _zero_boost_policy() -> str:
    """火罐为 0 时的策略(用户 2026-10-17 定的三态,只在 GUI「自由演出设置」一处配置)。

    三态在 GUI 的显示名(2026-10-17 二次改名):停在游戏主界面 / 继续挖矿 / 退出游戏。

    - "stop"(默认):停打歌,退回游戏主界面 —— 日常照常收尾(该做的三抽还会做);
    - "play":继续挖矿(0 火也能开演出,只是没有报酬);
    - "exit":停打歌 + 三抽收尾后关闭游戏。

    兼容旧配置:早先只有一个布尔键 play_at_zero_boost(true 继续 / false 退出),
    那个键**只对 main 模式生效**(日常历来是火尽即停)。所以没有新键时:
    日常一律按 stop(不改变老用户行为),main/挑战按旧布尔值回退。
    """
    cfg = _runtime_config()
    val = str(cfg.get("on_zero_boost") or "").strip().lower()
    if val in ("stop", "play", "exit"):
        return val
    if not _DAILY_MODE and "play_at_zero_boost" in cfg:
        return "play" if cfg.get("play_at_zero_boost") else "exit"
    return "stop" if _DAILY_MODE else "play"


def _song_strategy() -> str:
    """从 data/config.yml 读打歌策略:mine(挖矿为主,默认)/random(随机,抽到就打)。

    每次实时读文件,让 GUI 在运行中切换也能在下一首生效(与 photogate 一样)。
    """
    val = str(_runtime_config().get("song_strategy", "mine") or "mine").strip().lower()
    return val if val in ("mine", "random") else "mine"


def _reject_limit() -> int:
    """连续重抽上限,可用 data/config.yml 的 song_strategy_reject_limit 覆盖。
    调大 = 更严格地优先未打过的歌,但抽歌耗时上升。"""
    try:
        v = _runtime_config().get("song_strategy_reject_limit")
        if v is not None:
            return max(1, int(v))
    except Exception:
        pass
    return DEFAULT_REJECT_LIMIT


def check_song_available(name, id_, difficulty):
    if _song_strategy() == "random":
        # 随机选歌:第一次抽到什么就打什么,不再重抽(含已 AP / [FULL] 的歌)
        return True
    global _selection_rejections, _ap_reroll_streak
    tier = _song_tier(id_, difficulty)
    target = _current_target_tier(difficulty)

    # 全曲库都已 AP:已无可挖,无条件接受,否则会永远抽不到可打的歌
    if target > MINED_MAX_TIER:
        _selection_rejections = _ap_reroll_streak = 0
        return True

    # 硬规则:已 ALL PERFECT 无新奖励可取,重抽。判定基数是 Bestdori 全曲库,
    # 不等于账号实际可选曲目,故留 AP_REROLL_STREAK_MAX 次兜底:连续这么多
    # 次都只抽到已 AP 的歌,就认为确实没得挖了,让步接受。
    if tier > MINED_MAX_TIER:
        _ap_reroll_streak += 1
        if _ap_reroll_streak >= AP_REROLL_STREAK_MAX:
            _ap_reroll_streak = 0
            _selection_rejections = 0
            return True
        return False

    # 软优先:0/1/2 都有效,但优先更低档(0>1>2)。重抽有上限,超限即放宽,
    # 避免账号真实可选池里没有更低档歌时无限重抽。
    _ap_reroll_streak = 0
    if tier > target:
        _selection_rejections += 1
        if _selection_rejections >= _reject_limit():
            _selection_rejections = 0
            return True
        return False
    _selection_rejections = 0
    return True


def _song_level(song_id: str, difficulty: str):
    """曲库记录里该曲目在指定难度下的等级(选歌界面「乐曲等级」显示的就是它)。"""
    if difficulty not in _DIFFICULTY_ORDER:
        return None
    sinfo = all_songs.get(str(song_id)) or {}
    entry = (sinfo.get("difficulty") or {}).get(str(_DIFFICULTY_ORDER.index(difficulty)))
    return (entry or {}).get("playLevel")


def _candidate_ids(name: str) -> list:
    """标题 -> 可能的曲目 id 列表(重名时多个,默认项在最前)。"""
    if name in ambiguous_titles:
        return list(ambiguous_titles[name])
    sid = all_song_name_indexes.get(name)
    return [sid] if sid else []


def _read_screen_song_level(context: Context, image) -> Optional[int]:
    """读选歌界面右下「乐曲等级」的数字。读不到返回 None。

    只识别一个 2 位数字,约 100ms;位置见 `_SONG_LEVEL_ROI` 处的实测数据。
    """
    pipeline = {
        "song_level_ocr": {
            "recognition": "OCR",
            "only_rec": True,
            "roi": _SONG_LEVEL_ROI,
        }
    }
    try:
        text = context.run_recognition(
            "song_level_ocr", image, pipeline
        ).best_result.text
    except Exception:
        return None
    m = re.search(r"\d{1,2}", text or "")
    return int(m.group()) if m else None


def _pick_song_id(context: Context, image, name: str, ids: list) -> Optional[str]:
    """重名标题下挑出正确的曲目 id。挑不出返回 None(调用方按"识别失败"处理)。

    先用曲库里的等级筛:
      * 候选在当前难度下等级**全相同** → 实测这些条目谱面字节级一致,选默认项即可;
      * 等级不同 → 读屏上的「乐曲等级」,唯一命中的那个就是它;
      * 读不到 / 没有唯一命中 → 返回 None。宁可重抽,也不要拿错谱面打整首。
    """
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    levels = [_song_level(i, DIFFICULTY) for i in ids]
    if len(set(levels)) == 1:
        return ids[0]
    screen_level = _read_screen_song_level(context, image)
    if screen_level is not None:
        hit = [i for i, lv in zip(ids, levels) if lv == screen_level]
        if len(hit) == 1:
            logging.debug(
                "重名曲目 %r 按屏上等级 %s 选定 #%s(候选 %s / 等级 %s)",
                name, screen_level, hit[0], ids, levels,
            )
            return hit[0]
    logging.warning(
        "重名曲目无法区分,跳过本次选曲: %r 候选=%s 期望等级=%s 屏上等级=%r",
        name, ids, levels, screen_level,
    )
    return None


@maaresource.custom_recognition("SongRecognition")
class SongRecognition(CustomRecognition):
    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:

        roi = [200, 332, 368, 29]

        def ocr(model=None):
            pplname = "_ocrsong_" + "".join(random.choices(string.ascii_lowercase, k=7))
            pipeline = {
                pplname: {
                    "recognition": "OCR",
                    "only_rec": True,
                    "roi": roi,
                },
            }
            if model != None:
                pipeline[pplname]["model"] = model
            try:
                return context.run_recognition(
                    pplname,
                    argv.image,
                    pipeline,
                ).best_result.text
            except Exception:
                return ""

        # 两个模型各读一次:日文模型对日文歌名更准,默认模型对拉丁标题更准。
        # **两个读数一起**交给 matcher,不要各匹配一次再比分数 —— OCR 丢字很常见,
        # 而残留片段可能恰好是**另一首歌的完整标题**(2026-09-17 实测:`ぎゅっDAYS♪`
        # 被默认模型读成 `DAYS`,对 #120「DAYS」是满分 100,直接压过日文模型对正确
        # 条目的 53 分 → 按另一张谱面打整首)。合并后可用"长度一致性"识别这种截断。
        ja_name = ocr("ppocr_v3/ja_jp")
        default_name = ocr()  # , "ppocr_v4/zh_cn")
        logging.debug(
            "Song OCR with ppocr_v3/ja_jp: %r, with default: %r", ja_name, default_name
        )
        matched = fuzzy_match_song(ja_name, [default_name])
        if matched is None or matched[1] < 50:
            return CustomRecognition.AnalyzeResult(None, "")
        result_music_name, score = matched

        _candidates = _candidate_ids(result_music_name)
        song_id = _pick_song_id(
            context, argv.image, result_music_name, _candidates
        )
        if song_id is None:
            return CustomRecognition.AnalyzeResult(None, "")
        # 同名多条目且等级分不开时,_pick_song_id 只能给默认项;把候选列表留给
        # 「首音轨道」那一步定夺(见 _maybe_switch_chart_by_first_note)。
        global _ambiguous_song_ids
        _ambiguous_song_ids = list(_candidates) if len(_candidates) > 1 else []

        if LIVEMODE != "challengelive" and not check_song_available(result_music_name, song_id, DIFFICULTY):
            return CustomRecognition.AnalyzeResult(None, "")

        # 把选中的 id 交给紧随其后的 SaveSong 动作(同名多条目时不能再靠标题查表)
        global _resolved_song_id
        _resolved_song_id = (result_music_name, song_id)
        return CustomRecognition.AnalyzeResult(roi, result_music_name)


@maaresource.custom_recognition("LiveBoostEnoughRecognition")
class LiveBoostEnoughRecognition(CustomRecognition):
    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        # roi = [970, 29, 39, 21]
        roi = [979, 30, 61, 20]

        pipeline = {
            "live_boost_enough_ocr": {
                "recognition": "OCR",
                "only_rec": True,
                "roi": roi,
            },
        }
        # 2026-10-29:2026-10-28 22:02 事故 —— 演出已经开始(不在确认页)时
        # 火量区域一个字都读不到,`best_result` 是 None,旧写法 `.text` 直接抛
        # AttributeError(控制台两条 Traceback 的第一条),异常再被归到父节点
        # comfirm_song → on_error 在演出画面上永不命中 → 3s 超时把整场带走。
        # 识别不到只代表读不出,按 -1 返回,由调用方决定放行。
        try:
            reco = context.run_recognition(
                "live_boost_enough_ocr", argv.image, pipeline
            )
            live_boost = (
                reco.best_result.text if reco and reco.best_result else ""
            ) or ""
        except Exception as e:  # noqa: BLE001 —— 识别失败不能带崩整条链
            logging.debug("火量 OCR 异常: {}".format(e))
            live_boost = ""

        logging.debug("Live boost rec result: {}".format(live_boost))
        pattern = r"^\s*(\d+)\s*/"
        match = re.match(pattern, live_boost.replace(" ", ""))

        if match:
            try:
                live_boost = int(match.group(1))
            except:
                live_boost = -1
        else:
            live_boost = -1

        logging.debug("Live boost: {}".format(live_boost))
        return CustomRecognition.AnalyzeResult(roi, str(live_boost))


def _run_node_once(context, entry: str) -> None:
    """执行单个 pipeline 节点,出错只记日志不抛。

    注意 MaaContext.run_action 只执行该节点的识别+动作, **不会沿它的 next
    链继续**,所以多步操作必须把节点名逐个列出 —— 例如 close_app 自带的
    next("stop") 在这里不会生效,要再显式跑一次 "stop"。
    """
    try:
        context.run_action(entry)
    except Exception as e:
        logging.warning("run node %s failed: %s", entry, e)


# ---------------------------------------------------------------------------
# 退出联动:关游戏 → 停任务 → 收尾释放
#
# 背景(2026-09-18):火罐为 0 且配置为「退出游戏」时,游戏确实被关掉了,但脚本
# 仍在挂机 —— 界面一直停在「运行中」,进程不退出。原因是原来的链路全靠**软保证**:
#   1) `_exit_game_and_stop_task` 只把 need_to_stop 置位,能否真停取决于外层
#      PipelineTask 是否在下一个节点边界检查到它(动作阻塞期间检查不到);
#   2) main() 的收尾是 `sys.exit()`,而它只是抛 SystemExit,之后解释器还要
#      join 所有**非 daemon 线程** —— minitouchpy 的 STDIO 读线程恰好是非
#      daemon 的,一旦它阻塞在 readline 上,进程就永远退不掉。
# 这里补一层「状态判定 + 兜底」的联动:后台看门狗盯着"游戏是否还在"和"退出请求
# 是否被落实",两条都超时就带外 post_stop();最终所有退出路径都汇聚到 _shutdown()
# 统一释放资源并**硬退出**,不再依赖任何一条软保证。
# ---------------------------------------------------------------------------

GAME_PACKAGE = "com.bilibili.star.bili"

_EXIT_GRACE_S = 8.0  # 已登记退出请求后,任务仍未结束多久就带外强制停止
_EXIT_HARD_S = 20.0  # 强制停止后仍不结束,由看门狗直接收尾硬退出
_GAME_POLL_INTERVAL_S = 2.0  # 看门狗轮询周期
_GAME_EXIT_GRACE_S = 25.0  # 游戏连续缺席多久判定为「已退出」
_GAME_QUERY_FAIL_MAX = 3  # 连续多少次查不到设备状态就放弃该项判定

_exit_reason: Optional[str] = None  # 非 None = 已登记退出请求
_exit_requested_at: float = 0.0
_exit_stop_forced = False
_game_seen_running = False  # 见过游戏在跑之后才启用「游戏退出」判定
_shutting_down = False
_lifecycle_lock = threading.Lock()

# 用户显式指定的设备(端口 / 完整地址 / 序号)。非空时选设备阶段完全按它来,
# 不做任何自动判定。由 --device / GUI 下拉框写入。
DEVICE_OVERRIDE: str = ""


def _request_exit(reason: str) -> None:
    """登记「关掉游戏并停止脚本」的请求(幂等)。

    只登记不执行 —— 真正收尾由 _shutdown() 统一做。这样无论请求来自
    HandleLiveBoost、HandleLifeExhausted 还是看门狗,收尾路径都只有一条。
    """
    global _exit_reason, _exit_requested_at
    with _lifecycle_lock:
        if _exit_reason is not None:
            return
        _exit_reason = reason
        _exit_requested_at = time.time()
    logging.info("已请求退出: %s", reason)


def _console_stop(reason: str = "收到停止指令") -> None:
    """立即收尾退出(控制台 / Ctrl+C)。_shutdown 用 os._exit,一定会退。

    为什么不走「温柔地停任务」:用户按下停止时,任务多半卡在打歌或识别里,
    等管线自己走到节点边界可能要几十秒;这里直接走统一收尾,行为与
    「关掉控制台窗口」一致,游戏画面停在哪儿就是哪儿。
    """
    try:
        logging.warning("%s,立即收尾(游戏里若正在演出,请自行暂停/退出)", reason)
    except Exception:
        pass
    _shutdown(0, reason)


def _start_console_stop() -> None:
    """装一条不依赖主线程的停止通道(2026-10-30,用户实测「ctrl+c 按了没反应」)。

    根因:MAA 的任务跑在 C++ 层,主线程阻塞在 `job.wait().get()` 里;
    KeyboardInterrupt 只在**主线程执行 Python 字节码**时才投递,而打歌/识别
    全程不回到 Python,信号就一直挂着 —— 表现就是 Ctrl+C 按了没反应、进程
    停不下来,只能关窗口。

    做法:daemon 线程读 stdin,读到任意一行(空行也算)立刻 `_console_stop`;
    SIGINT / SIGBREAK 也接到同一个回调(主线程有机会执行时直接用这条)。
    GUI / 打包态没有控制台 stdin,线程读到 EOF 立即结束,零副作用。
    """
    for name in ("SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_: _console_stop("收到中断信号"))
        except Exception:  # noqa: BLE001 —— 非主线程/无控制台时装不上,忽略
            pass
    if sys.stdin is None or getattr(sys.stdin, "closed", True):
        return

    def _reader():
        try:
            for _line in sys.stdin:
                _console_stop("控制台输入停止")
                return
        except Exception:  # noqa: BLE001 —— stdin 被关掉/管道断开,正常结束
            return

    threading.Thread(target=_reader, daemon=True, name="console-stop").start()
    try:
        if sys.stdin.isatty():
            logging.info("停止方式:Ctrl+C,或直接在这里按一次回车")
    except Exception:
        pass


def _force_post_stop() -> None:
    """带外请求 MaaFramework 停止当前任务。

    need_to_stop 是节点边界的软检查,遇到阻塞中的动作(打歌循环、OCR 超时)不会
    生效;post_stop 由框架置位,是最后一道保险。
    """
    try:
        if maatasker.inited:
            maatasker.post_stop()
    except Exception as e:
        logging.warning("post_stop 失败: %s", e)


def _adb_shell(args: list, timeout: float = 6.0) -> Optional[str]:
    """在设备上执行一条 shell 命令;查不到(设备掉线/adb 报错)返回 None。

    返回 None 与「命令成功但输出为空」必须区分开:前者不能用来推断"进程不存在",
    否则一次 adb 抖动就会把脚本误停。
    """
    if device is None:
        return None
    cmd = [str(device.adb_path), "-s", str(device.address), "shell"] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # 同 _adb_shell_on:中文 Windows 的 locale 编码是 GBK,adb 输出的
            # UTF-8 字节会让 subprocess 抛 UnicodeDecodeError 而整个查询失败。
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logging.debug("adb 查询失败 %s: %s", args, e)
        return None
    err = (proc.stderr or "").strip()
    if proc.returncode not in (0, 1) or err:
        logging.debug(
            "adb 查询异常 %s: rc=%s err=%s", args, proc.returncode, err[:200]
        )
        return None
    return proc.stdout or ""


def _game_process_running() -> Optional[bool]:
    """游戏进程是否存活。None = 本次查不到,不参与判定。"""
    out = _adb_shell(["pidof", GAME_PACKAGE])
    if out is None:
        return None
    return bool(out.strip())


# ---------------------------------------------------------------------------
# 多开设备识别
#
# 背景:MuMu 多开时每个实例都是一个独立的 adb 设备(端口 16384 + 32n),
# 界面上长得一模一样。而选设备发生在 GUI 启动的 bot 子进程里,拿不到 stdin,
# 所以既不能弹窗询问、也**绝不能静默挑第一个** —— 那会让 minitouch 的绝对
# 坐标点在别的游戏窗口上。
#
# 判定分层(越靠前越可靠):
#   1. pm path <pkg>  —— 邦邦**已安装**。主判据:用户常态是「先开模拟器、
#      点开始演出,再由脚本自己点开始游戏」,此时 pidof 全为空,所以不能把
#      「进程存活」当唯一依据。
#   2. pidof <pkg>   —— 邦邦**正在运行**。装了但没在玩的实例要降权。
#   3. 端口号         —— 兜底:探测全失败时至少能让用户手动指定。
#
# 语义纪律:任何一层查询失败都返回 None(不可用),**绝不能当成「没装/没跑」**
# —— 一次 adb 抖动会让所有实例看起来都是空的,导致误报 fatal 或错选设备。
# 这与 _game_process_running 的三态口径一致。
# ---------------------------------------------------------------------------

_PROBE_TIMEOUT_S = 6.0


def _adb_shell_on(dev, args: list, timeout: float = _PROBE_TIMEOUT_S):
    """在指定设备上执行一条 shell 命令,查不到返回 None。

    与 _adb_shell 的区别只在于「用哪个设备」:选设备阶段 device 还是 None,
    必须显式传。返回 None 与「命令成功但输出为空」严格区分。
    """
    if dev is None:
        return None
    cmd = [str(dev.adb_path), "-s", str(dev.address), "shell"] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # 必须显式给编码:中文 Windows 的 locale 编码是 GBK,adb 回来的
            # UTF-8 字节(设备名、包路径)会让它抛 UnicodeDecodeError。
            # errors="replace" 保证解码失败也把输出拿回来 —— 这里只是做
            # 「含不含某字符串」的判定,尾部乱码无害,远好过整条调用崩掉。
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logging.debug("探测 adb 失败 %s %s: %s", dev.address, args, e)
        return None
    err = (proc.stderr or "").strip()
    if proc.returncode not in (0, 1) or err:
        # rc=1 常是「包不存在」这类正常否定答案,但 stderr 有内容一律视为查询失败
        logging.debug(
            "探测 adb 异常 %s %s: rc=%s err=%s",
            dev.address, args, proc.returncode, err[:200],
        )
        return None
    return proc.stdout or ""


def _probe_device(dev) -> dict:
    """探测单个设备上邦邦的安装/运行状态。

    返回 {"address","name","installed","running","foreground"}:
      installed / running —— True / False / None(查不到)
      foreground          —— True / False / None(查不到)
    """
    info = {
        "address": str(getattr(dev, "address", "") or "?"),
        "name": str(getattr(dev, "name", "") or "?"),
        "installed": None,
        "running": None,
        "foreground": None,
    }

    # 1) 已安装?pm path 命中即返回 "package:/data/app/..." 路径
    out = _adb_shell_on(dev, ["pm", "path", GAME_PACKAGE])
    if out is not None:
        info["installed"] = GAME_PACKAGE in out

    # 2) 正在运行?
    out = _adb_shell_on(dev, ["pidof", GAME_PACKAGE])
    if out is not None:
        info["running"] = bool(out.strip())

    # 3) 在前台?装了但切到别的游戏时,应当让用户选那个真正在玩的实例
    out = _adb_shell_on(dev, ["dumpsys", "window", "windows"])
    if out is None:
        # 老版本 dumpsys 不接受 windows 参数,退回全局查一次
        out = _adb_shell_on(dev, ["dumpsys", "window"])
    if out is not None and GAME_PACKAGE in out:
        info["foreground"] = True
    elif out is not None:
        # 只在明确抓到 mCurrentFocus 行时才敢判 False;
        # 抓不到焦点行(某些 ROM 不输出)保持 None,不误判。
        focus = ""
        for line in out.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                focus = line
                break
        if focus:
            info["foreground"] = GAME_PACKAGE in focus
    return info


def _probe_all_devices(devices: list) -> list:
    """并发探测一批设备。单个设备失败不影响其他,失败的项 installed=None。"""
    results = [None] * len(devices)

    def work(i, dev):
        try:
            results[i] = _probe_device(dev)
        except Exception as e:  # 探测绝不抛出:失败按「查不到」处理
            logging.debug("探测设备异常 %s: %s", getattr(dev, "address", "?"), e)
            results[i] = {
                "address": str(getattr(dev, "address", "?") or "?"),
                "name": str(getattr(dev, "name", "?") or "?"),
                "installed": None, "running": None, "foreground": None,
            }

    threads = []
    for i, dev in enumerate(devices):
        t = threading.Thread(target=work, args=(i, dev), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=_PROBE_TIMEOUT_S * 3 + 5)
    # 极端情况下 join 超时,补齐空洞,避免调用方拿到 None
    for i, r in enumerate(results):
        if r is None:
            results[i] = {
                "address": str(getattr(devices[i], "address", "?") or "?"),
                "name": str(getattr(devices[i], "name", "?") or "?"),
                "installed": None, "running": None, "foreground": None,
            }
    return results


def _ensure_override_device(devices: list, requested: str) -> list:
    """显式指定的实例若不在 MAA 枚举结果里,补一个进去。

    背景:MAA 的 find_adb_devices 只认它自己配置里登记过的地址(默认只有
    127.0.0.1:16384 一个)。多开时用户选第 2、3 个实例,它压根不在结果里,
    于是「我明明选了它,却报没有可用设备」。

    做法:按用户给的端口/地址直接 `adb connect`,再把它包成一个 AdbDevice
    塞回列表。配置从已知设备**克隆**(同一个 adb_path + 同样的输入/截屏
    方式),这样 screencap_methods / input_methods 这些能力配置不会丢 ——
    直接 new 一个空 AdbDevice 会退化成通用 mjpg/inject,打歌会崩。
    """
    want = str(requested or "").strip()
    if not want:
        return devices
    # 已在内(按端口或完整地址比对)则不动
    for d in devices:
        addr = str(getattr(d, "address", "") or "")
        if want in (addr, addr.split(":")[-1]):
            return devices
    if not devices:
        logging.error(
            "未枚举到任何模拟器设备,无法使用 --device %s 指定的实例", want
        )
        return devices

    adb_path = devices[0].adb_path
    # 把用户写的东西归一成 host:port
    if want.isdigit():
        address = "127.0.0.1:%s" % want
    elif ":" in want:
        address = want
    else:
        address = want

    # 连一次:失败说明这个实例根本没在跑,早报错比让用户对着死端口猜强
    try:
        proc = subprocess.run(
            [str(adb_path), "connect", address],
            capture_output=True, text=True, timeout=8,
            # 同 _adb_shell_on:显式 UTF-8 + replace。这里输出直接决定
            # 连不连得上,绝不能因为解码失败就误判成「连接失败」。
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "").strip()
        logging.info("adb connect %s: %s", address, out or "(无输出)")
        low = out.lower()
        if proc.returncode != 0 or (
            "connected" not in low and "already" not in low
        ):
            logging.error(
                "无法连接指定的实例 %s —— 请确认该模拟器已启动。"
                "MuMu 多开第 n 个实例的端口为 16384 + 32n。",
                address,
            )
            return devices
    except Exception as e:
        logging.error("adb connect %s 失败: %s", address, e)
        return devices

    try:
        extra = AdbDevice(
            name="MuMu (指定实例 %s)" % address.split(":")[-1],
            adb_path=adb_path,
            address=address,
            screencap_methods=devices[0].screencap_methods,
            input_methods=devices[0].input_methods,
            config=devices[0].config,
        )
    except Exception as e:
        logging.error("构造指定实例的设备对象失败: %s", e)
        return devices

    logging.info("已按 --device %s 追加该实例到候选设备", address)
    return list(devices) + [extra]


_ADB_CONNECT_TIMEOUT_S = 6.0


def _adb_connect(adb_path, address, timeout: float = _ADB_CONNECT_TIMEOUT_S) -> bool:
    """对地址做一次 `adb connect`;True = 这条连接可用。

    判据与 `device_scan._guess_ports` 一致:`adb connect` 对**没在跑**的实例也可能
    rc=0,必须看输出文本里出现 connected/already 才算数。
    """
    if not adb_path or not address:
        return False
    try:
        proc = subprocess.run(
            [str(adb_path), "connect", str(address)],
            capture_output=True, text=True, timeout=timeout,
            # 同 _adb_shell_on:显式 UTF-8 + replace,别让中文 Windows 的 GBK 默认
            # 编码把「连上没连上」这个判定搅掉。
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logging.debug("adb connect %s 失败: %s", address, e)
        return False
    low = ((proc.stdout or "") + (proc.stderr or "")).lower()
    return "connected to" in low or "already" in low


def _drop_unreachable_devices(devices: list) -> list:
    """从候选里剔除「adb 连不上」的实例(多候选时才做)。

    背景(2026-10-09 实机):MAA 的 `find_adb_devices` 走 MuMuManager,会把
    **已创建但从未启动**的实例也按 `16384 + 32n` 反推成候选 —— 实测一个
    `index=1` 的空壳被列成 `127.0.0.1:16416`。那个地址 adb 连不上,探测必然
    三条命令全 None(显示「安装状态未知」);更麻烦的是它把「本来只有一个可用
    实例」撑成「多实例」→ 走自动判定分支 → 真正那台一旦也探不出邦邦,就整体
    fatal「无法确定哪个实例是邦邦游戏」。
    GUI 侧 `device_scan.discover` 走 `adb devices`(只列在线的),两边口径本就不
    一致;这里补上 connect 这一步,让 bot 的候选表与 GUI 对齐。

    纪律:**全部连不上时保留原候选表**(宁可让后面的探测报「未知」,也不在 adb
    抖动时把唯一可用设备误删 —— 与 `_device_fingerprint` 取不到就不去重同口径)。
    """
    if len(devices) < 2:
        # 单候选走 `len(_device) == 1` 直接采用,不探测也不 fatal,没有误判空间
        return devices

    ok = [False] * len(devices)

    def work(i, dev):
        ok[i] = _adb_connect(
            getattr(dev, "adb_path", ""), str(getattr(dev, "address", "") or "")
        )

    # 必须并发:adb connect 连一个不存在的端口要等 TCP 超时(实测单个约 2s),
    # 串行在多开时会把启动拖住十几秒。与 _guess_ports 同一处理。
    threads = []
    for i, dev in enumerate(devices):
        t = threading.Thread(target=work, args=(i, dev), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=_ADB_CONNECT_TIMEOUT_S + 3)

    kept = []
    for i, dev in enumerate(devices):
        if ok[i]:
            kept.append(dev)
        else:
            logging.info(
                "候选设备 %s 连不上(MuMu 里已创建但没启动?),已从候选中剔除",
                getattr(dev, "address", "?"),
            )
    if not kept:
        logging.warning("候选设备全都连不上 —— adb 可能异常,保留原候选表继续探测")
        return devices
    return kept


def _format_device_choices(probes: list) -> str:
    """把探测结果渲染成给用户看的候选清单。"""
    if not probes:
        return "(没有枚举到任何 MuMu / 雷电模拟器设备)"
    lines = []
    for i, p in enumerate(probes):
        tags = []
        if p["installed"] is True:
            tags.append("已装邦邦")
        elif p["installed"] is False:
            tags.append("未装邦邦")
        else:
            tags.append("安装状态未知")
        if p["running"] is True:
            tags.append("运行中")
        elif p["running"] is False:
            tags.append("未运行")
        if p["foreground"] is True:
            tags.append("在前台")
        lines.append(
            "  [{}] {} ({})  {}".format(i, p["name"], p["address"], "、".join(tags))
        )
    return "\n".join(lines)


def _pick_device_by_probe(probes: list, requested: str = "") -> int:
    """按探测结果挑出应该使用的设备下标。返回 -1 表示挑不出来。

    requested 非空(用户/CLI 显式指定)时按「端口或序号匹配」精确命中,
    不做任何推断 —— 显式指定永远优先于自动判定。
    """
    # ① 显式指定:允许写端口(16416)、完整地址(127.0.0.1:16416)或序号(0)
    want = str(requested or "").strip()
    if want:
        for i, p in enumerate(probes):
            addr = p["address"]
            port = addr.split(":")[-1]
            # 序号写法(0/1/2…)是 MuMu 多开器的通用说法(参考实现也用
            # device.instance),端口则是完整地址。两者都接受,数字相同时
            # 以端口为准 —— 16384 显然是端口而不是序号。
            if want in (addr, port) or (want.isdigit() and int(want) == i):
                logging.info("使用指定的设备: [%d] %s (%s)", i, p["name"], addr)
                return i
        # 2026-10-10 改:**不再直接 -1**。用户上次选的实例很可能只是这次没开
        # ——多开时端口是 16384 + 32n,换台/没启动就会变。实机日志里这一路是:
        #   「无法连接指定的实例 127.0.0.1:16384」→「指定的设备…不在候选列表中」
        #   → CRITICAL「候选的 1 个模拟器实例都探测失败」
        # 用户在小号上勾了「每日免费三抽」,看到的就是「脚本卡住了啥也没干」。
        # 现在回退到自动识别;若自动识别同样判不出来(多台都装了邦邦且收敛不了),
        # 下面的 ②③④ 依旧返回 -1 → 照旧报「无法确定哪个实例是邦邦游戏」,
        # 所以不会静默挑一个错窗口去操作。
        logging.warning(
            "指定的设备「%s」不在候选列表中(实例未启动?多开端口变了?)"
            " —— 已回退到自动识别。若不是你想用的实例,请在 GUI 重新选择。",
            want,
        )

    # ② 装了邦邦的实例就是候选集(installed=False 的直接排除:
    #    它连包都没有,选它必然连错游戏)
    installed = [i for i, p in enumerate(probes) if p["installed"] is True]
    if not installed:
        # ③ 探测全部不可用/都没装 —— 唯一能做的就是让用户自己指定,
        #    绝不静默挑第一个(那正是「操作到其他游戏窗口」的成因)
        return -1
    if len(installed) == 1:
        logging.info(
            "自动识别到邦邦实例: [%d] %s (%s)",
            installed[0], probes[installed[0]]["name"], probes[installed[0]]["address"],
        )
        return installed[0]

    # ④ 多个实例都装了邦邦:优先选「正在运行」的,再优先「在前台」的
    running = [i for i in installed if probes[i]["running"] is True]
    if len(running) == 1:
        logging.info(
            "自动识别到正在运行的邦邦实例: [%d] %s (%s)",
            running[0], probes[running[0]]["name"], probes[running[0]]["address"],
        )
        return running[0]
    fg = [i for i in running if probes[i]["foreground"] is True]
    if len(fg) == 1:
        logging.info(
            "自动识别到前台邦邦实例: [%d] %s (%s)",
            fg[0], probes[fg[0]]["name"], probes[fg[0]]["address"],
        )
        return fg[0]
    # ⑤ 依然无法唯一确定 —— 交给用户,见 init_maa 的报错分支
    return -1


def _wait_game_exit(timeout: float) -> bool:
    """轮询确认游戏进程已消失,返回是否确认到。

    关掉游戏后立刻查 pidof 会命中「正在退出」的进程,所以要轮询 —— 这一步就是
    用户要的「检测到游戏已退出」,有了确认结论,后面的停止才有依据。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _game_process_running()
        if state is False:
            return True
        if state is None:
            # 查询本身不可用(设备掉线 / adb 报错):不空等。停止脚本这件事不依赖
            # 这个确认结论 —— 后面还有看门狗和统一收尾兜底。
            return False
        time.sleep(0.4)
    return False


def _stop_on_game_exit() -> bool:
    """data/config.yml 的 stop_when_game_exits(默认 true)。

    关掉它 = 保留旧行为(游戏没了也让 bot 靠 start_app 自愈)。
    """
    value = _runtime_config().get("stop_when_game_exits", True)
    return bool(value) if isinstance(value, bool) else True


def _release_minitouch() -> None:
    """停掉 minitouch,并杀掉它的子进程、关掉管道。

    关管道是必须的:minitouchpy 的 STDIO 读线程是**非 daemon** 线程,阻塞在
    `p.stderr.readline()` 上。只要管道没关,解释器退出时就会一直等它 ——
    进程永远退不掉,表现正是「脚本不自动停止」。
    """
    global mnt
    target, mnt = mnt, None
    if target is None:
        return
    try:
        target.stop()
    except Exception as e:
        logging.debug("mnt.stop 失败: %s", e)
    proc = getattr(target, "mnt_process", None)
    if proc is None:
        return
    try:
        proc.kill()
    except Exception:
        pass
    for stream in (
        getattr(proc, "stdin", None),
        getattr(proc, "stdout", None),
        getattr(proc, "stderr", None),
    ):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


def _shutdown(exit_code: int = 0, reason: str = "") -> None:
    """统一收尾:停任务 → 释放 minitouch → 释放 MaaFramework → 刷日志 → 硬退出。

    所有退出路径(正常结束 / 火罐不足 / 生命耗尽 / 失败超限 / 看门狗 / 异常)都
    汇聚到这里,保证资源一定被释放,且**一定真的退出**。用 os._exit 而不是
    sys.exit:后者只是抛 SystemExit,之后解释器还要 join 非 daemon 线程,卡住就
    再也退不出来。本函数不会返回。
    """
    global _shutting_down, maacontroller, current_player
    with _lifecycle_lock:
        if _shutting_down:
            return
        _shutting_down = True
    try:
        logging.info("MAA : %s", reason or "正常结束")
    except Exception:
        pass

    _force_post_stop()
    # 给框架一点时间把任务/控制器线程收干净。这只是"体面退出"的余量, 拿不到也
    # 不影响正确性 —— 后面一定会 os._exit。实测 post_stop 会新投递一个 stop 任务,
    # 所以 running 往往要等到停止任务被消费才转 False, 别把等待设长。
    t_wait = time.time()
    deadline = t_wait + 3.0
    while time.time() < deadline:
        try:
            if not maatasker.running:
                break
        except Exception:
            break
        time.sleep(0.2)
    logging.debug("框架收尾等待 %.2fs", time.time() - t_wait)

    _release_minitouch()

    # 显式放掉框架对象,触发 MaaControllerDestroy / MaaTaskerDestroy(正常析构会
    # 断开 adb 并关掉 maa.log);放不掉也无所谓 —— 下面马上硬退出。
    try:
        maatasker._controller_holder = None
        maacontroller = None
        current_player = None
        import gc as _gc

        _gc.collect()
    except Exception as e:
        logging.debug("释放框架对象失败: %s", e)

    # os._exit 会跳过 atexit,而 atexit 里挂着 timeEndPeriod(1) —— 手动补一次,
    # 别把系统时钟粒度留在 1ms。
    try:
        import ctypes

        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass

    try:
        logging.shutdown()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(exit_code)


def _exit_watchdog_loop() -> None:
    global _game_seen_running, _exit_stop_forced
    absent_since = None
    query_fails = 0
    while not _shutting_down:
        time.sleep(_GAME_POLL_INTERVAL_S)
        if _shutting_down:
            return
        try:
            task_running = maatasker.running
        except Exception:
            task_running = False

        # 1) 已登记退出请求,任务却迟迟不结束 → 带外强制停止;再拖就直接收尾。
        if _exit_reason and task_running:
            age = time.time() - _exit_requested_at
            if age >= _EXIT_HARD_S:
                logging.error("强制停止后任务仍未结束,直接收尾退出")
                _shutdown(0, "退出请求超时,看门狗强制收尾")
                return
            if age >= _EXIT_GRACE_S and not _exit_stop_forced:
                _exit_stop_forced = True
                logging.warning(
                    "退出请求 %.1fs 后任务仍未结束(%s),强制停止", age, _exit_reason
                )
                _force_post_stop()
            continue

        # 2) 游戏存活监测:见过游戏在跑之后,长时间查不到就认定它已退出。
        if not _stop_on_game_exit():
            continue
        state = _game_process_running()
        if state is None:
            query_fails += 1
            if query_fails == _GAME_QUERY_FAIL_MAX:
                logging.debug("连续查不到设备状态,暂停游戏存活判定")
            continue
        query_fails = 0
        if state:
            _game_seen_running = True
            absent_since = None
            continue
        if not _game_seen_running:
            # 启动阶段游戏本来就还没起来(接下来靠 start_app 拉起),不能判退出
            continue
        if absent_since is None:
            absent_since = time.time()
            continue
        gap = time.time() - absent_since
        if gap >= _GAME_EXIT_GRACE_S:
            logging.warning("游戏已退出(连续 %.0fs 未检测到进程),停止 MAA", gap)
            _game_seen_running = False
            _request_exit("游戏已退出")
            _force_post_stop()


def _start_exit_watchdog() -> None:
    """启动退出看门狗。daemon 线程,不会拖住进程退出。"""
    threading.Thread(
        target=_exit_watchdog_loop, name="exit-watchdog", daemon=True
    ).start()


def _watchdog_loop() -> None:
    """卡死看门狗的后台扫描线程(2s 一次,daemon,不会拖住进程退出)。"""
    while True:
        try:
            if _STUCK_WATCHDOG.check():
                break
        except Exception as e:  # noqa: BLE001 —— 看门狗自己绝不能把主流程带崩
            logging.debug("卡死看门狗检查异常: %s", e)
        time.sleep(_WATCHDOG_SCAN_INTERVAL_S)


def _start_stuck_watchdog() -> None:
    """启动卡死看门狗。special(故事)模式不启动:连续点「下一步」是正常行为。"""
    threading.Thread(
        target=_watchdog_loop, name="stuck-watchdog", daemon=True
    ).start()
    # 文案(2026-10-29 用户定):不叫「卡死自动恢复」,一行说完「什么时候会动、
    # 按什么顺序动」;「脚本」一律写成 MAA(用户习惯的说法就是 MAA)。
    logging.info(
        "卡住保护:已启用(画面与 MAA 连续 %.0fs 都没有任何进展才会介入,"
        "顺序:通知等待循环退出 → 点「演出失败→退出」→ 按返回键)",
        _WATCHDOG_STATIC_S,
    )


def _exit_game_and_stop_task(context, reason: str = "退出游戏") -> None:
    """关闭游戏并终止当前任务,同时登记退出请求。

    "stop" 节点是 StopTask,它的作用是把**当前 Context** 的 need_to_stop
    置位;而 run_action 与外层任务共享同一个 Context(getptr()),外层
    PipelineTask 会在下一个节点边界检查到该标志并直接正常返回,任务随即
    结束,不会继续走 next / on_error。

    这条链是软保证(见上方注释),所以这里额外做两件事:关掉游戏后轮询确认它
    真的退出了;登记退出请求 —— 看门狗发现任务不结束会强制 post_stop,再由
    _shutdown() 收尾硬退出。
    """
    global _game_seen_running
    _request_exit(reason)
    _run_node_once(context, "close_app")
    if _wait_game_exit(6.0):
        logging.info("游戏已退出,停止 MAA")
        _game_seen_running = False
    else:
        logging.warning("关闭游戏后未确认到进程消失,仍继续停止 MAA")
    _run_node_once(context, "stop")


def _reco_int(argv) -> int:
    """把自定义识别返回的 detail 字符串转成整数;读不到/非法一律返回 -1。

    2026-10-29:火量识别在「演出已经开始」等异常画面上会返回空 detail,
    旧写法 `int(detail)` 直接抛 ValueError(控制台第二条 Traceback),
    异常再被归到父节点 → on_error 在演出画面上永不命中 → 整场任务超时失败。
    这里统一兜成 -1,由调用方决定放行 —— 识别失败不能等价成「火量为 0」。
    """
    try:
        detail = str(argv.reco_detail.best_result.detail or "").strip()
    except Exception:  # noqa: BLE001
        return -1
    try:
        return int(detail)
    except (TypeError, ValueError):
        return -1


@maaresource.custom_action("HandleLiveBoost")
class HandleLiveBoost(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        liveboost = _reco_int(argv)
        if liveboost < 0:
            # 读不到 ≠ 火罐为 0:放行,继续按原链路开演。若真没火,开演后
            # 游戏自己的判定/下一次结算还能纠正;反过来误判成 0 会直接
            # 把整场日常停掉。
            logging.warning("火量识别失败(未读出),本轮放行不判火量")
            return CustomAction.RunResult(True)
        if liveboost < MIN_LIVEBOOST:
            # 日常模式(Issue #4)的收工判据就在这里 —— 清火要的就是"把火用完",
            # **判据是火量,不是生命值**(别把收工点挪回 HandleLifeExhausted)。
            # 2026-10-17 用户定的三态策略(见 _zero_boost_policy):
            #   stop = 停打歌,退回游戏主界面(日常照常收尾,该做的三抽还会做)
            #   play = 继续挖矿(0 火也能开演出,只是没有报酬)
            #   exit = 停打歌,日常收尾(含三抽)后关闭游戏
            # 只 StopTask、不请求进程退出,收尾统一由 _run_daily_flow 做;
            # "退回主界面"也在那一层做(这里是管线动作,同步 post 另一个 task 容易死锁)。
            policy = _zero_boost_policy()
            global _daily_fire_exhausted
            if policy == "play":
                logging.info(
                    "火罐不足(火量 %d),按设置「继续挖矿」继续", liveboost
                )
                return CustomAction.RunResult(True)
            # 交替模式(清火+三抽同开)下,_run_daily_flow 的循环靠这个标志
            # 判断"该收工了";非清火模式不读它,无副作用。
            _daily_fire_exhausted = True
            if _DAILY_CLEAR_FIRE:
                logging.info("火罐不足，日常已停止(火量 %d)", liveboost)
                _run_node_once(context, "stop")
                return CustomAction.RunResult(False)
            if policy == "exit":
                # 非日常(命令行 main / 挑战演出):保持旧的"退出游戏"语义。
                # 这里必须显式终止任务,不能只靠 ensure_liveboost 的
                # on_error("stop"): ensure_liveboost 是 comfirm_song 的 next
                # 节点,它返回 False 时 MaaFramework 会把错误归到**父节点**
                # comfirm_song 上,走的是 comfirm_song.on_error
                # (= random_choice_song) —— 任务并不会停。后果是游戏被关掉后
                # bot 还在跑,main 的 next 全不命中,最终落到 interrupt 里的
                # start_app(无 recognition = 必定命中)把游戏重新拉起来,
                # 表现为「退出游戏后又自动重启」。
                logging.info("Live boost not enough, ready to exit")
                _exit_game_and_stop_task(context, "火罐不足,按配置退出游戏")
                return CustomAction.RunResult(False)
            logging.info("火罐不足(火量 %d),按设置停止打歌", liveboost)
            _run_node_once(context, "stop")
            return CustomAction.RunResult(False)
        return CustomAction.RunResult(True)


@maaresource.custom_action("HandleDailyBoostZero")
class HandleDailyBoostZero(CustomAction):
    """日常入口(阶段1 回主页后)的火罐检查(用户 2026-10-17 需求)。

    火罐为 0 ⇒ 按「自由演出设置 → 火罐为0时」策略处理(停在主界面 / 继续挖矿 /
    退出游戏);火罐 >0 ⇒ 放行,继续阶段2/3。
    OCR 读不出(-1)时同样放行 —— 不能因为一次识别失败就误停用户的日常
    (宁可多跑,不可错停)。
    """

    def run(self, context: Context, argv: CustomAction.RunArg):
        liveboost = _reco_int(argv)
        if liveboost == 0:
            policy = _zero_boost_policy()
            if policy == "play":
                logging.info("火罐不足(0),按设置「继续挖矿」继续")
                return CustomAction.RunResult(True)
            global _daily_boost_zero
            _daily_boost_zero = True
            if policy == "exit":
                logging.info("火罐不足，日常已停止(按设置退出游戏)")
                _exit_game_and_stop_task(context, "火罐不足,按配置退出游戏")
                return CustomAction.RunResult(False)
            logging.info("火罐不足，日常已停止")
            _run_node_once(context, "stop")
            return CustomAction.RunResult(False)
        if liveboost > 0:
            logging.info("日常开始:当前火罐 %d", liveboost)
        else:
            logging.debug("日常入口火罐检查:OCR 未读出火量,放行")
        return CustomAction.RunResult(True)


@maaresource.custom_action("MarkDailyPullDone")
class MarkDailyPullDone(CustomAction):
    """把"今日免费三抽已做完/当前不可抽"标记到进程内(用户 2026-10-17 需求)。

    挂在管线节点 daily_pull_done(剩余0回/今日已完成)与 daily_pull_locked
    (尚未完成每日演出)上:这两条都是"这次抽不了"的终局状态。

    背景:原来每首打完都跑一遍三抽,一次 20-40 秒;标记之后
    _run_daily_flow 每首只跳过,收尾再复核一次。
    """

    def run(self, context: Context, argv: CustomAction.RunArg):
        global _daily_pull_done
        _daily_pull_done = True
        # reason 由管线节点的 custom_action_param 传入(2026-10-17 加):
        #   done   = 今日次数已抽完;locked = 需先完成每日演出,当前抽不了。
        # 原来两种情况共用一句"已确认今日没有可抽次数",读日志时分不清是哪种,
        # 排查"三抽为什么没抽"时会被误导。
        reason = ""
        try:
            reason = str(
                (json.loads(argv.custom_action_param or "{}") or {}).get("reason") or ""
            )
        except Exception:
            reason = ""
        if reason == "locked":
            logging.info(
                "每日免费三抽:需先完成每日演出,当前抽不了(收尾会再查一次)"
            )
        else:
            logging.info("每日免费三抽:今日次数已抽完")
        return CustomAction.RunResult(True)


# 崩局退出(2026-10-29):
# 12:35 场 #606 V.I.P MONSTER 崩局现场(debug/autodori-20261029-121834.log):
# life_exhausted_exit 的 OCR 明明命中了「退出」(0.9996) 并点掉弹窗,但 next 上的
# life_exhausted_confirm 还是 expected「退出」,而它的 roi [630,395,280,90] 在
# 那个弹窗上覆盖的是「重新开始」「继续」两个按钮 —— 连续 5 次 OCR 只读到
# 「斤开始 / 继」,5 秒后 Task timeout → on_error:stop → 整局清火停止,
# 后面 3 次 main 全部「未进入演出」(游戏停在得分页)。
# 现在管线不再要求二次确认(退出链直连 handle_life_exhausted),这里补 Python
# 侧的收尾确认:每轮抓一张图,①还在「演出失败」弹窗就再点一次「退出」;
# ②弹出「确认退出返回主页吗?」就点粉色的「退出」;③掉到得分页就点白色「确定」;
# ④三样都认不到 = 已经离开演出,直接放行。
# ② 是 2026-10-29 23:51 场 #462-hard 卡死的教训:崩局时第一次「退出」点掉的是
# 「演出失败」弹窗,紧接着弹出的二次确认弹窗没人认(它的白按钮是「取消」、
# 粉按钮是「退出」,上面的 roi 一个都不覆盖),_force_leave_live 判成「已不在
# 演出界面」就放行了,结果主链在弹窗上转了 90 秒、看门狗也被 MAA 的节点循环
# 喂得以为有进展,只能人工点掉(截图 debug/local/stuck_now.png)。
# 判据用模板而不是 OCR:exit/pink.png 在这张弹窗上实测 1.0000,白「取消」只有
# 0.62;小字 OCR 不稳,大按钮模板更可靠(见 AGENTS 第 5 节)。
_LIFE_EXIT_PIPELINE = {
    "life_exit_btn": {
        "recognition": "OCR",
        "only_rec": True,
        "expected": ["退出"],
        "roi": [300, 420, 160, 70],
    },
    "life_exit_confirm_btn": {
        "recognition": "TemplateMatch",
        "template": ["common/button/exit/pink.png"],
        "roi": [600, 400, 320, 100],
        "threshold": 0.85,
    },
    "life_result_ok": {
        "recognition": "TemplateMatch",
        "template": ["common/button/confirm/white.png"],
        "roi": [300, 380, 680, 320],
        "threshold": 0.8,
    },
}


def _force_leave_live(context, max_round=5) -> bool:
    """崩局后确保真的离开演出界面。认不到弹窗时返回 False(只代表没确认到)。

    只做三种已知画面的收尾(演出失败弹窗 / 退出二次确认 / 得分页),不猜其他 UI
    —— 认不出来就交给后续节点,绝不在这里死等。
    """
    # clean = 连续「三样都没认到」的轮数;点掉任何弹窗都会清零(见循环尾注释)。
    clean = 0
    for rnd in range(1, max_round + 1):
        screen = _grab_screen()
        if screen is None:
            logging.warning("崩局退出:第 %d 轮抓图失败,跳过", rnd)
            return False
        # ⚠ 通道顺序(2026-10-30 定位,这条坑埋了一整天):
        # `ipc_capture_display()` 返回的是 **RGB**(见 _snap 里 cv2.imwrite 前
        # 的 [:, :, ::-1]),而 `context.run_recognition` 底下的 OpenCV 按 **BGR**
        # 解释。彩色模板做相关匹配时通道一错就永远匹配不上:粉「退出」正确帧
        # 1.0000 → 错通道 **0.6734**,正好等于 maa.log 里那两次 0.673/0.6147。
        # 后果是这里的三个判据**从来就没命中过**,每轮都落到「三样都没认到」,
        # 于是每次都把弹窗留在屏幕上放行(00:07、00:14 两次卡死都是这个)。
        # OCR 之所以没暴露:白底深字的灰度对通道顺序不敏感,只有彩色模板会崩。
        screen = np.ascontiguousarray(screen[:, :, ::-1])
        try:
            reco = context.run_recognition(
                "life_exit_btn", screen, _LIFE_EXIT_PIPELINE
            )
            if reco is not None and reco.best_result:
                logging.info("崩局退出:第 %d 轮仍在「演出失败」弹窗,再点一次「退出」", rnd)
                _click_at_box(reco.best_result.box)
                clean = 0
                time.sleep(1.0)
                continue
            reco = context.run_recognition(
                "life_exit_confirm_btn", screen, _LIFE_EXIT_PIPELINE
            )
            if reco is not None and reco.best_result:
                logging.info("崩局退出:第 %d 轮弹出「确认退出返回主页吗?」,点「退出」", rnd)
                _click_at_box(reco.best_result.box)
                clean = 0
                time.sleep(1.0)
                continue
            reco = context.run_recognition(
                "life_result_ok", screen, _LIFE_EXIT_PIPELINE
            )
            if reco is not None and reco.best_result:
                logging.info("崩局退出:第 %d 轮停在得分页,点「确定」", rnd)
                _click_at_box(reco.best_result.box)
                clean = 0
                time.sleep(1.0)
                continue
        except Exception as e:  # noqa: BLE001 —— 收尾失败不能连带崩掉主流程
            logging.warning("崩局退出:第 %d 轮识别失败(%s)", rnd, e)
            return False
        # 「三样都认不到」不能一轮就放行(2026-10-30 00:07 场 #462-hard 踩坑):
        # 那一轮抓到的是**过渡帧/旧帧** —— 「确认退出返回主页吗?」其实已经在
        # 屏幕上,但粉「退出」只匹配到 0.673(阈值 0.85)、白「退出」OCR 读到空,
        # 于是判成「已不在演出界面」放行,主链又卡在弹窗上两分多钟。
        # 现在要求**连续两轮**都认不到才放行,两轮之间隔 0.6s 再抓一张;
        # 中途认出任何弹窗就回到上面点掉,并把连续计数清零。
        clean += 1
        if clean < 2:
            logging.info(
                "崩局退出:第 %d 轮三样都没认到(可能抓到过渡帧),0.6s 后复查", rnd
            )
            time.sleep(0.6)
            continue
        logging.info("崩局退出:连续 %d 轮都没认到弹窗,已不在演出界面", clean)
        return True
    logging.warning(
        "崩局退出:%d 轮后仍在演出界面,交给后续节点处理", max_round
    )
    return False


@maaresource.custom_action("HandleLifeExhausted")
class HandleLifeExhausted(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        # 这里**不是**清火阶段的收工点:生命值耗尽只说明这一首打崩了,只要还有火
        # 就该接着打下一首。「清火完成」的判据是**火量不足**,由 HandleLiveBoost
        # 在选曲确认页判定 —— 2026-10-15 之前错挂在这里,正常打完永远等不到收工。
        # 先确认真的离开了演出(管线那一步只保证点过「退出」,不保证生效)。
        _force_leave_live(context)
        # GUI 配置 data/config.yml 的 on_life_exhausted:
        #   auto = 回到主页后自动继续打歌; wait = 停在主页等用户手动操作
        mode = str(_runtime_config().get("on_life_exhausted", "auto") or "auto")
        if mode == "wait":
            # 不依赖 on_error 冒泡保证停在主页(上游 next 是 "main",一旦
            # life_exhausted_exit 的 next/on_error 被改动,这里会退化成沿
            # next("main")继续打歌),所以显式跑一次 stop。
            logging.info("生命值耗尽,已退出到主页,等待手动操作")
            # 登记退出请求:stop 走的是 need_to_stop 软检查,万一没落实,
            # 看门狗会在 8s 后强制 post_stop 并走统一收尾。
            _request_exit("生命值耗尽,等待手动操作")
            _run_node_once(context, "stop")
            return CustomAction.RunResult(False)
        logging.info("生命值耗尽,自动继续打歌")
        return CustomAction.RunResult(True)


# ---------------------------------------------------------------------------
# 超高难度 SPECIAL 活动(限时单曲)
#
# 与常规打歌的差别只在「怎么走到开演前」这一段:曲目固定、难度固定 SPECIAL、
# 不做选曲。玩家手动进入活动并把界面停在开演前的确认页,脚本从 special_wait
# 开始轮询(assets/resource/pipeline/special.json):认到「演出开始」就点,
# 认到标准确认 / OK 就先点掉,认到暂停键说明歌已经开始,直接接管打谱面。
# 打完一首即收尾停止,不做循环。
# ---------------------------------------------------------------------------
_SPECIAL_IDLE_WARN_INTERVAL_S = 15.0
_special_idle_since: Optional[float] = None
_special_idle_warned_at: float = 0.0

# ⚠️ 这里曾经放过「选曲校验」的两个标志位(_special_song_mismatch / _special_song_verified),
# 已按用户要求**整个移除**(2026-10-09)。原因见 SPECIAL 活动模式的说明:
# 「选择乐队」页的曲名 OCR **不稳定** —— 实测既会把 `[期间限定 SPECIAL]` 整段误识成
# `OIAL「期i限定 SPECIALI`(前缀全毁),也会在某些画面下读出与曲名无关的字符串
# (`不澄当形方柔郡父`,显然是立绘/背景上的字)。判据无论怎么调都会误拦用户的正确操作,
# 而用户的选择习惯是「跑错了自己重新选歌」→ 由人眼做这件事更可靠。
# **别再加回来**;真要校验曲名,只能用选歌页的「乐曲等级」数字(SPECIAL 用不上那个位置)。

#: 收尾时在得分界面找「确定」的轮数与间隔(得分界面会先播一段分数动画)
_SPECIAL_FINISH_ROUNDS = 8
_SPECIAL_FINISH_INTERVAL_S = 0.8


@maaresource.custom_action("SpecialIdle")
class SpecialIdle(CustomAction):
    """special_wait 的兜底节点:画面上没有任何已知按钮时在这里空转。

    必须是 DirectHit 节点(无 recognition = 必定命中),并放在 next 列表**最后**
    一位 —— 否则整条 next 全不命中时任务会被判失败直接终止。周期性打日志,让
    「一直等不到界面」这件事在日志里可见,而不是静默卡住。
    """

    def run(self, context: Context, argv: CustomAction.RunArg):
        global _special_idle_since, _special_idle_warned_at

        now = time.time()
        if _special_idle_since is None:
            _special_idle_since = now
            logging.info(
                "超高难度活动:开始等待演出界面,请确认游戏已停在开演前的确认页"
            )
        if now - _special_idle_warned_at >= _SPECIAL_IDLE_WARN_INTERVAL_S:
            _special_idle_warned_at = now
            logging.warning(
                "超高难度活动:本次已等待 %.0fs 仍未识别到「演出开始」/确认按钮,"
                "请检查游戏界面",
                now - _special_idle_since,
            )
        return CustomAction.RunResult(True)


@maaresource.custom_action("SpecialFinish")
class SpecialFinish(CustomAction):
    """活动单曲打完之后收尾:在得分界面点「确定」,再交给 next("stop") 结束任务。

    得分界面(活动版)右下角并排两个键:「再次演出」与「确定」。实测
    `live/button/liveagain.png` 在「再次演出」上命中 **0.9962**,
    `common/button/confirm/pink.png` 在「确定」上命中 **0.9981** —— 也就是说
    常规流程那套 `next:[event_reward_confirm, liveagain, live_home_button]`
    会直接点「再次演出」回到选曲页,与「打完一首就停」冲突。所以这里必须
    显式点「确定」。

    点击优先级:确定 → 下一步/OK/关闭 → 获得报酬弹窗兜底。**第一个成功的点击就
    收工** —— 需求是「点确定然后停」,不是替用户把后续界面全走完。

    节点本身**不配 on_error**:MAA 里同一节点的 next / on_error 出现重复元素会让
    整份 pipeline 失效(项目里已验证过),而这里两者都只能指向 "stop"。动作内部
    已把所有异常吞掉并始终返回 True,真出意外也是任务正常结束收场。
    """

    #: 按优先级依次尝试,第一个命中即点,点完立即收尾
    BUTTONS = (
        "confirm_button",        # 得分界面的「确定」(实测 0.9981)
        "next_button",           # 「下一步」,其它版本的结算页
        "ok_button",
        "close_button",
        "event_reward_confirm",  # 「获得报酬」弹窗,若挡在确定前面先点掉
    )

    def run(self, context: Context, argv: CustomAction.RunArg):
        logging.info("超高难度活动:本首已结束,收尾(点确定后停止)")
        clicked = None
        idle_rounds = 0
        for _ in range(_SPECIAL_FINISH_ROUNDS):
            for node in self.BUTTONS:
                try:
                    detail = context.run_action(node)
                except Exception as e:
                    logging.debug("special finish run %s failed: %s", node, e)
                    continue
                # run_action 认不到时返回 None;只有真执行了动作才算点过
                if detail is not None and getattr(detail, "completed", False):
                    logging.info("活动收尾: 已点击 %s", node)
                    clicked = node
                    break
            if clicked:
                break
            idle_rounds += 1
            if idle_rounds >= 2:
                break
            # 得分界面会先播分数动画,按钮可能晚一两轮才出现
            time.sleep(_SPECIAL_FINISH_INTERVAL_S)
        if not clicked:
            logging.info("超高难度活动:得分界面未出现可点按钮,直接停止")
        return CustomAction.RunResult(True)


#: 结算页数字的形近字符归一表。
#: 数字是补零的浅灰细体(实拍 `0848`、`0000`),OCR 常把 0 读成 `O`/`D`、8 读成 `B`、
#: 1 读成 `I`/`l`。不先归一的话 int() 直接抛异常,整项退化成 -1 —— 2026-10-15 实测
#: FAST/SLOW 长期显示 -1 就是这么来的。
_OCR_DIGIT_MAP = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "D": "0",
        "Q": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "Z": "2",
        "z": "2",
        "A": "4",
        "S": "5",
        "s": "5",
        "G": "6",
        "b": "6",
        "B": "8",
        "g": "9",
        "q": "9",
    }
)

#: 判定明细;它们的和 = 本曲总音符数
_RESULT_COUNTS = ("perfect", "great", "good", "bad", "miss")


def _ocr_to_int(text) -> int:
    """结算页 OCR 文本 → 整数;读不出数字返回 -1(未知)。

    不能直接 int():见 _OCR_DIGIT_MAP。先归一形近字符,再滤掉千分位逗号/空格等
    噪声,最后解析。
    """
    if text is None:
        return -1
    s = re.sub(r"[^0-9]", "", str(text).strip().translate(_OCR_DIGIT_MAP))
    return int(s) if s else -1


def _reconcile_play_result(result: dict) -> dict:
    """用判定字段之间**必然成立**的关系,纠正/剔除不可信的 OCR 读数。

    三条关系都不是经验拟合:
      1. GREAT 与 GOOD 同为 0 ⇒ 本曲没有非 PERFECT 判定 ⇒ `fast = slow = 0`
         (实拍结算页该处正是 `0000`)。
      2. miss 与 bad 同为 0 ⇒ 全程没有断连 ⇒ `maxcombo` 必等于总音符数
         (`perfect+great+good+bad+miss`)。
      3. `k = miss + bad` 次断连把全曲切成 k+1 段,最长一段至少
         `ceil(total/(k+1))`,且不可能超过 total。

    2026-10-15 实测的错值全被覆盖:全连曲把 `499` 读成 `439`、`830` 读成 `630`
    (关系 2 直接纠正);`maxcombo 6514` 超过总数(关系 3 判无效)。
    关系算不出确切值的一律置 -1(未知),**不猜数字**。
    """
    r = dict(result)
    counts = {}
    for key in _RESULT_COUNTS:
        value = r.get(key, -1)
        counts[key] = value if isinstance(value, int) and value >= 0 else -1
    if min(counts.values()) < 0:
        # 判定项本身没读全,推算的基数不可信,原样返回
        return r

    if counts["great"] == 0 and counts["good"] == 0:
        r["fast"] = r["slow"] = 0

    total = sum(counts.values())
    breaks = counts["bad"] + counts["miss"]
    maxcombo = r.get("maxcombo", -1)
    maxcombo = maxcombo if isinstance(maxcombo, int) else -1
    if breaks == 0:
        r["maxcombo"] = total
    elif not (-(-total // (breaks + 1)) <= maxcombo <= total):
        r["maxcombo"] = -1
    return r


#: 结算页 FAST/SLOW 的数字是**淡灰细体**:实拍 `0000` 的前三位灰度 ~210(背景
#: 250+、常规深色字 ~124),OCR 对整片淡灰细体直接返回空文本 —— 表现为
#: `GREAT 75(FAST -/SLOW -)`(2026-10-17 用户反馈"识别不正确")。
#: 阈值 235:背景(>=250)留白,淡灰字与深色字一律涂黑,得到黑字白底。
_RESULT_DIGIT_BINARIZE_THRESH = 235


def _binarize_result_rois(image, rois, thresh=_RESULT_DIGIT_BINARIZE_THRESH):
    """把结算页各数字 ROI 二值化成黑字白底,返回增强后的全图副本。

    只改 ROI 内部:背景(亮)保持白色,淡灰细体与深色字都拉黑 —— ppocr 对
    低对比度细体失效,对干净的黑字白底稳定。ROI 越界时裁掉,不抛异常。
    """
    out = image.copy()
    height, width = out.shape[:2]
    for x, y, w, h in rois:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        region = out[y0:y1, x0:x1]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
        out[y0:y1, x0:x1] = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
    return out


@maaresource.custom_recognition("PlayResultRecognition")
class PlayResultRecognition(CustomRecognition):
    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:

        types = {
            "score": {
                "roi": [1028, 192, 144, 35],
            },
            "maxcombo": {
                "roi": [1009, 391, 91, 28],
            },
            "perfect": {
                "roi": [829, 282, 90, 28],
            },
            "great": {
                "roi": [828, 322, 91, 27],
            },
            "good": {
                "roi": [829, 363, 91, 27],
            },
            "bad": {
                "roi": [829, 401, 90, 27],
            },
            "miss": {
                "roi": [830, 438, 91, 28],
            },
            "fast": {
                "roi": [1088, 283, 90, 27],
            },
            "slow": {
                "roi": [1088, 323, 91, 28],
            },
        }
        result = {type_: -1 for type_ in types.keys()}
        raw_texts = {}
        pipeline = {
            f"_PlayResultRecognition_ocr_{type_}": {
                "recognition": "OCR",
                "only_rec": True,
                "roi": type_value["roi"],
            }
            for type_, type_value in types.items()
        }
        # 二值化增强图(懒加载:只有字段读不出时才构造)
        enhanced = None
        for type_ in types:
            try:
                reco = context.run_recognition(
                    f"_PlayResultRecognition_ocr_{type_}",
                    argv.image,
                    pipeline,
                )
                ocrtext = reco.best_result.text if reco and reco.best_result else ""
            except Exception as e:
                ocrtext = ""
                logging.debug("Play result ocr error [{}]: {}".format(type_, e))
            raw_texts[type_] = ocrtext
            result[type_] = _ocr_to_int(ocrtext)
            if result[type_] == -1:
                # 原图读不出(典型:FAST/SLOW 的淡灰细体 0000,OCR 直接返回空文本)。
                # 用二值化增强图再试一次;原图能读到的值一律保留,不拿增强图覆盖 ——
                # 增强只兜底,不改变已验证可行的字段(perfect/maxcombo 等)。
                if enhanced is None:
                    enhanced = _binarize_result_rois(
                        argv.image, [v["roi"] for v in types.values()]
                    )
                try:
                    reco = context.run_recognition(
                        f"_PlayResultRecognition_ocr_{type_}",
                        enhanced,
                        pipeline,
                    )
                    ocrtext2 = reco.best_result.text if reco and reco.best_result else ""
                except Exception as e:
                    ocrtext2 = ""
                    logging.debug(
                        "Play result ocr error (enhanced) [{}]: {}".format(type_, e)
                    )
                if ocrtext2:
                    raw_texts[type_] = "{} => {}".format(ocrtext, ocrtext2)
                    result[type_] = _ocr_to_int(ocrtext2)

        # 原始文本一并落盘:读数异常时能分清"压根没识别到"和"识别成了别的数字"
        logging.debug("Play result raw: {}".format(raw_texts))
        result = _reconcile_play_result(result)
        logging.debug("Play result: {}".format(result))
        return CustomRecognition.AnalyzeResult([0, 0, 0, 0], json.dumps(result))


@maaresource.custom_action("SavePlayResult")
class SavePlayResult(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        try:
            global current_song_id, play_failed_times
            succeed: bool = json.loads(argv.custom_action_param).get("succeed")
            if succeed:
                # 成功即归零:MAX_FAILED_TIMES 的语义是「连续失败」而不是
                # 「本次运行累计失败」。原来只在失败时 +1、从不归零,挂机久了
                # (尤其 EXPERT/SPECIAL 偶发失败)会累计到 10 次,在某首歌之后
                # 突然关掉游戏,日志只有一句「演出失败次数超限」,极难解释。
                play_failed_times = 0
                playresult = argv.reco_detail.best_result.detail
                if isinstance(playresult, str):
                    playresult = json.loads(argv.reco_detail.best_result.detail)
            else:
                play_failed_times += 1
                playresult = {}
            if current_song_id is not None:
                PlayRecord.create(
                    play_time=int(time.time()),
                    play_offset=OFFSET,
                    result=playresult,
                    succeed=succeed,
                    chart_id=current_song_id,
                    difficulty=DIFFICULTY,
                )
                # 战绩变了,档位表缓存失效,下一首选歌时重算(否则提升不被承认)
                _tier_map_cache.pop(DIFFICULTY, None)
            else:
                # 启动时游戏已在演出失败界面,没有选中过歌曲,跳过保存记录
                logging.debug("No song selected, skip saving play result")
            if play_failed_times >= MAX_FAILED_TIMES:
                logging.error("Failed attempts exceed max failed times, stop")
                _exit_game_and_stop_task(context, "演出失败次数超限")
                return CustomAction.RunResult(False)
            return CustomAction.RunResult(True)
        except Exception as e:
            logging.error(f"Failed to save play result: {e}")
            return CustomAction.RunResult(False)


class LifeExhaustedDetected(Exception):
    """打歌中生命值耗尽,游戏弹出「演出失败」窗口,提前结束本次演出。"""


# 打歌中生命耗尽检测:
#  MAA 在自定义动作(Play)阻塞运行期间不会检查 pipeline 的 interrupt,所以
#  playsong 的 live_failed 中断在打歌中实际不生效,只会等整首打完才轮到。
#  这里在 play_song 循环里直接检测弹窗,命中即抛异常让 Play 返回失败,走
#  on_error 的生命耗尽处理链。
#  roi 与 live_failed 一致,覆盖弹窗左上「演出失败」标题(实测文字在 x266-372,
#  y236-259,roi 取稍大保证 OCR 读到完整四字)。
_LIFE_ROI = [256, 227, 155, 38]
_LIFE_PIPELINE = {
    "life_check": {
        "recognition": "OCR",
        "only_rec": True,
        "expected": ["演出失败"],
        "roi": _LIFE_ROI,
    },
}


def _life_exhausted_on_screen(context, screen=None) -> str:
    """检测当前画面是否出现「演出失败」弹窗(生命值耗尽)。

    screen 传入时复用调用方已抓的帧(打歌期间与暂停检测共用一张图,抓图次数保持
    1 次/秒,不给发布时序添额外开销)。

    返回 "hit" / "no_match" / "precheck_rejected" / "error" 而不是布尔值。
    「卡在演出失败弹窗、脚本没反应」有好几种成因(检测没机会跑、亮度预检把
    白底弹窗漏掉、OCR 异常),布尔值事后分不清是哪一种;调用方按状态记计数器,
    下次复现时日志能直接指向原因。

    先做廉价亮度预检:弹窗标题区是白底深字(实测平均亮度 ~224),普通打歌
    画面该区域几乎不会是大块白,先滤掉绝大部分帧,只有预检通过才跑 OCR,
    避免打歌中频繁 OCR 占用 CPU。检测耗时由调用方从 sleep 里补偿,不影响
    打歌时机。
    """
    try:
        screen = screen if screen is not None else _grab_screen()
        x, y, w, h = _LIFE_ROI
        region = screen[y : y + h, x : x + w]
        if float(region.mean()) < 150:
            return "precheck_rejected"
        reco = context.run_recognition("life_check", screen, _LIFE_PIPELINE)
        text = (reco.best_result.text if reco and reco.best_result else "") or ""
        logging.debug("life check ocr: {}".format(text))
        return "hit" if text else "no_match"
    except Exception as e:
        logging.debug("life check error: {}".format(e))
        return "error"


class LivePauseInterrupted(Exception):
    """打歌中出现「暂停」弹窗,已点「中断」把本局收掉(本局无报酬)。"""


# 打歌中「暂停」弹窗检测:
#  2026-10-17 用户实况:打歌途中弹出暂停框(标题「暂停」,三个键:中断/重新开始/继续),
#  脚本完全没反应。根因同生命耗尽:Play 自定义动作阻塞期间 MAA 不检查 pipeline 的
#  interrupt,playsong 的 interrupt 在打歌中不生效 → 这一局永远打不完。
#  用户定的处置:点「中断」退出本局(拿不到报酬,好过卡死)。
#  判据用「中断」按钮做模板(白底深字、位置固定,实测在本弹窗命中 1.0000、在
#  普通打歌画面/主页/二次确认弹窗上都远低于阈值),比 OCR 标题稳。
_PAUSE_ROI = [230, 395, 300, 120]
_PAUSE_PIPELINE = {
    "pause_check": {
        "recognition": "TemplateMatch",
        "template": "live/button/pause_interrupt.png",
        "roi": _PAUSE_ROI,
        "threshold": 0.8,
    },
}
# 二次确认弹窗里的粉色「中断」。这里只能 OCR:同一块粉色按钮在第一个暂停弹窗里
# 是「继续」,用模板实测 0.9138(远超阈值)会点错成"继续演出"。
_CONFIRM_ROI = [640, 410, 270, 95]
_CONFIRM_PIPELINE = {
    "live_pause_confirm": {
        "recognition": "OCR",
        "expected": ["中断"],
        "roi": _CONFIRM_ROI,
    },
}


def _grab_screen():
    """抓一张当前画面(ndarray);抓不到(掉线/锁被挂死方占用)返回 None。

    抓图串行化,避免与看门狗线程的 IPC 抓图串包;但必须限时拿锁 —— 2026-10-20
    凌晨场实测 MuMu 掉线时 IPC 调用挂死不返回,with 拿锁会无限等(见
    _CAP_LOCK_TIMEOUT_S 注释)。调用方(生命/暂停检测)对 None 都有兜底。
    """
    if not _SCREEN_CAP_LOCK.acquire(timeout=_CAP_LOCK_TIMEOUT_S):
        logging.debug(
            "抓图锁 %.0fs 拿不到,持锁方疑似挂死在 IPC 上,本帧跳过",
            _CAP_LOCK_TIMEOUT_S,
        )
        return None
    try:
        frame = current_player.ipc_capture_display()
        if frame is None:
            return None
        return np.ascontiguousarray(frame, dtype=np.uint8)
    finally:
        _SCREEN_CAP_LOCK.release()


# ---- 崩局诊断(2026-10-28) ----
# 背景:18:30 场 #189-special / #413-special 两首都在中途「演出失败」,而下发链
# 完全干净(曲内滞后 -6.1 / +0.6ms、dump 失配 0)、生命条检测一直没报(31/32 次
# 亮度预检跳过)—— 事后只剩「演出失败」那一条 OCR 文本,分辨不出是时间线整体
# 偏早/偏晚(分数一直不涨)还是某一段崩(分数涨过再断)。补三样只读取证,全部
# 复用打歌循环已经抓到的帧,不额外抢 IPC(外部连采会抢通道,见 _save_frame_async):
#   ①首音触发后约 1s 存一张「首音后」画面 —— 看第一批音符有没有被判;
#   ②崩局瞬间存帧 + OCR 左上角分数;
#   ③崩局一行日志 + 同目录 txt(首音触发参数/批次/指令索引/在线滞后)。
_FIRST_NOTE_DIAG = {}

# 左上角分数区(1280x720):截图实测数字在 x≈65..195 / y≈65..90;避开上方
# C/B/A/S/SS 评级条(y 30~55)与右上角暂停键。
_CRASH_SCORE_ROI = [50, 55, 240, 48]
_CRASH_SCORE_PIPELINE = {
    "crash_score": {
        "recognition": "OCR",
        "roi": _CRASH_SCORE_ROI,
        "only_rec": True,
    },
}


def _save_frame_async(tag, frame, subdir="debug/firstnote"):
    """把**已经抓到**的帧异步落盘(纯诊断:不抓新图、不参与判定、不占时序)。

    不在这里抓图:2026-10-27 实测外部/旁路线程连采 IPC 会和 bot 抢通道
    (滞后统计失配从 5/22020 涨到 3763/11472)。帧由打歌线程自己给。
    """
    if frame is None:
        return
    img = frame.copy()

    def _worker():
        try:
            os.makedirs(subdir, exist_ok=True)
            name = "%s/%s-%s.jpg" % (subdir, time.strftime("%H%M%S"), tag)
            # IPC 帧是 RGB,cv2 要 BGR;4 通道时只留前三个
            cv2.imwrite(name, img[:, :, :3][:, :, ::-1])
            logging.debug("诊断截图: %s", name)
        except Exception as e:  # noqa: BLE001 —— 诊断绝不能影响打歌
            logging.debug("诊断截图失败(%s): %s", tag, e)

    threading.Thread(target=_worker, daemon=True, name="diag-snap").start()


def _maybe_snap_firstnote_post(frame):
    """首音触发后约 1s,复用打歌循环已抓的帧存一张「首音后」画面。

    取第一道 ≥800ms 的生命检测节拍(那时第一批音符已经过判定线)。崩局时和
    trigger 截图对照:锚点偏早 → 判定线上没音符、分数不涨;偏晚 → 音符已压在
    判定线下方。纯诊断,不额外抓图,不参与判定。
    """
    diag = _FIRST_NOTE_DIAG
    t0 = diag.get("t")
    if t0 is None or diag.get("post_saved"):
        return
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if elapsed_ms < 800.0:
        return
    diag["post_saved"] = True
    _save_frame_async("firstnote+%dms" % int(elapsed_ms), frame)


def _dump_crash_diag(context, frame, song_label, batch_no, chart):
    """崩局(打歌中生命耗尽)取证:落帧 + 读分数 + 汇总日志 + txt。

    分数是事后区分两类崩局的唯一信号:时间线整体错(锚点偏早/偏晚)→ 分数
    几乎不涨;某一段崩(手势/游戏卡顿)→ 分数涨过一大截才断。
    全部只读 —— OCR 发生在已经判定失败之后,不再影响任何时序。
    """
    label = "%s-%s" % (time.strftime("%H%M%S"), song_label)
    _save_frame_async("crash", frame, "debug/crash")
    score = ""
    try:
        reco = context.run_recognition(
            "crash_score", frame, _CRASH_SCORE_PIPELINE
        )
        score = (reco.best_result.text if reco and reco.best_result else "") or ""
    except Exception as e:  # noqa: BLE001
        logging.debug("崩局分数 OCR 失败: %s", e)
    diag = dict(_FIRST_NOTE_DIAG)
    t0 = diag.get("t")
    played_s = (time.perf_counter() - t0) if t0 else -1.0
    sent = getattr(chart, "actions_to_cmd_index", -1)
    total = len(getattr(chart, "actions", []))
    lag = _lag_fb.get("lag", 0.0)
    summary = (
        "已打 %.1fs(自首音触发) · 发布批次 %d · 指令索引 %s/%s"
        " · 在线 lag %.1fms · 画面分数 '%s'"
    ) % (played_s, batch_no, sent, total, lag, score)
    logging.info("崩局诊断: %s", summary)
    try:
        os.makedirs("debug/crash", exist_ok=True)
        with open("debug/crash/%s.txt" % label, "w", encoding="utf-8") as f:
            f.write("# 崩局诊断 %s 曲目 %s photogate=%sms\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), song_label,
                PHOTOGATE_LATENCY,
            ))
            f.write("# " + summary + "\n")
            if diag.get("t") is not None:
                f.write((
                    "# 首音触发[%s]: band %.0fms change=%.2f %d/7 轨变化"
                    " 底噪=%.2f 触发帧 y=%.1f\n"
                ) % (
                    diag.get("kind", "?"), diag.get("band_ms", 0.0),
                    diag.get("change", 0.0), diag.get("lanes", 0),
                    diag.get("noise", 0.0), diag.get("y", -1.0),
                ))
            else:
                f.write("# 首音触发: 本局无记录(未经过 wait_first_note)\n")
    except Exception as e:  # noqa: BLE001 —— 诊断落盘失败不影响收尾
        logging.debug("崩局诊断落盘失败: %s", e)


def _pause_dialog_on_screen(context, screen=None) -> bool:
    """打歌中当前画面是否是「暂停」弹窗。异常一律当没弹,绝不带崩打歌。

    screen 传入时复用同一帧(见 _life_exhausted_on_screen 的说明)。
    """
    try:
        screen = screen if screen is not None else _grab_screen()
        reco = context.run_recognition("pause_check", screen, _PAUSE_PIPELINE)
        return bool(reco and reco.best_result)
    except Exception as e:
        logging.debug("pause check error: {}".format(e))
        return False


def _click_at_box(box) -> bool:
    """按识别框中心点一下。box = [x, y, w, h]。"""
    if not box:
        return False
    try:
        x, y, w, h = (int(v) for v in list(box)[:4])
    except Exception:  # noqa: BLE001
        return False
    try:
        maacontroller.post_click(x + w // 2, y + h // 2).wait()
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("点击失败(%s),改用 adb tap", e)
        _adb_shell(["input", "tap", str(x + w // 2), str(y + h // 2)])
        return True


def _interrupt_paused_live(context) -> None:
    """点「中断」→ 二次确认弹窗的粉色「中断」,把这一局收掉回到主页。

    ⚠ 不能直接用 _run_node_once(它是 run_action):run_action 不带识别结果,
    Click 动作拿不到框,会退化到 (0,0) 乱点(2026-10-17 实况踩到)。
    所以这里自己做"识别 → 按识别框中心点"两步,坐标全部来自识别。
    """
    logging.warning("打歌中出现「暂停」弹窗,点「中断」退出本局(本局将没有演出报酬)")
    try:
        reco = context.run_recognition(
            "pause_check", _grab_screen(), _PAUSE_PIPELINE
        )
        box = reco.best_result.box if reco and reco.best_result else None
        _click_at_box(box)
    except Exception as e:  # noqa: BLE001
        logging.warning("点「中断」失败: %s", e)
    time.sleep(1.0)
    try:
        reco2 = context.run_recognition(
            "live_pause_confirm", _grab_screen(), _CONFIRM_PIPELINE
        )
        box2 = reco2.best_result.box if reco2 and reco2.best_result else None
        _click_at_box(box2)
    except Exception as e:  # noqa: BLE001
        logging.warning("点二次确认「中断」失败: %s", e)
    time.sleep(1.5)


@maaresource.custom_action("Play")
class Play(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        try:
            play_song(context)
            return CustomAction.RunResult(True)
        except LifeExhaustedDetected:
            # 打歌中生命耗尽:返回失败,playsong 的 on_error 接管(记录+退出)
            return CustomAction.RunResult(False)
        except LivePauseInterrupted:
            # 打歌中暂停弹窗:已点「中断」,同样返回失败让 on_error 链收尾
            # (save_failed_playresult 记一条失败 → life_exhausted_exit 在主页认不出
            #  「退出」就 stop,任务结束在主界面,后续三抽正好从主页起手)
            return CustomAction.RunResult(False)
        except Exception as e:
            if isinstance(e, OSError):
                # MuMu 掉线时 minitouch 的 socket 随 adb forward 隧道一起断开,
                # Windows 上 sendall 报 WSAEINVAL([Errno 22] Invalid argument),
                # 不是代码 bug(2026-10-19 实测:模拟器掉线后只剩这条报错)。栈在
                # MAA 的 C 回调边界只剩两行,stack_info 没有增量信息,不再打。
                logging.error("模拟器连接断开,打歌中止(%s)", e)
            else:
                logging.error(f"Failed when play song: {e}", stack_info=True)
            return CustomAction.RunResult(False)


@maaresource.custom_action("SaveSong")
class SaveSong(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        name: CustomRecognitionResult = argv.reco_detail.best_result.detail
        save_song(name)
        return CustomAction.RunResult(True)


@maaresource.custom_action("StartChallengeLive")
class StartChallengeLive(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg):
        if current_chart is None:
            logging.error("挑战谱面尚未准备，停止演出。")
            return False
        return click(context, "_challenge_start_live", [1040, 584, 185, 87])


# 曲库里同一首歌可能有多个条目,靠标题前缀区分,而它们是**完全不同的谱面**:
#   `[FULL] キズナミュージック♪`(#249, expert 1485 音符) 与
#   `キズナミュージック♪`(#158, expert 436 音符)。
# OCR 常把方括号读错(`[FULL]` → `「FULLI`,2026-09-16 实跑),而 fuzzywuzzy 的
# WRatio 对"短标题被长查询包含"最高只给 90 分(源码:长度差 >1.5 倍时 partial 结果
# ×0.9),于是基础曲反而以 90 分压过正确条目的 87 分 → 选到错误谱面,长版只打了
# 前 101s 的短版谱面就停手,后半首全 miss。因此必须先按前缀标记分池再比相似度。
_TITLE_BRACKETS = "[【「『（(［"
_TITLE_BRACKET_TRANS = str.maketrans(
    _TITLE_BRACKETS, "[" * len(_TITLE_BRACKETS)
)
# 池内最高分低于此值 → 认为"分池分错了",回退到全库再比一遍
_POOL_SCORE_FLOOR = 60


def _has_title_prefix(title: str) -> bool:
    """标题是否带前缀标记(`[FULL]` / `[超高難易度 SPECIAL]` / `[原曲]` …)。

    只判断"开头是不是方括号类字符",不要求配对:OCR 的闭合括号经常读错(`]`→`I`)。
    """
    return bool(title) and title.lstrip().translate(_TITLE_BRACKET_TRANS).startswith("[")


_TITLE_CLOSERS = "）)】」』]"
_TITLE_OPENERS = "（([【「『"


def _has_title_suffix(title: str) -> bool:
    """标题是否带**尾部**括注(`(月島まりなver.)` / `(平行歌曲)` / `（支持3D演出模式）`)。

    与 `_has_title_prefix` 同款思路:只判断"结尾是不是闭括号类字符且前面还有
    开括号",不要求配对(OCR 读错括号很常见)。2026-10-21 实测:#24 与
    「ときめきエクスペリエンス！(月島麻里奈ver.)」(#786/#790)是不同谱面,
    OCR 读出带后缀的完整标题时,基础曲凭部分匹配 90 分压过变体 → 错配整首。
    """
    t = (title or "").rstrip()
    if len(t) < 2 or t[-1] not in _TITLE_CLOSERS:
        return False
    idx = max(t.find(c, 1) for c in _TITLE_OPENERS)
    return idx > 0


def _normalize_title(title: str) -> str:
    """标题归一:NFKC(全角→半角、兼容字符拆解)。

    OCR 会把半角读成全角(`DAYS` → `ＤＡYＳ`),而模糊匹配逐字符比,
    不归一的话正确的 `ぎゅっDAYS♪` 对 `ぎゅっＤＡYＳト` 只有 53 分(归一后 93 分)。

    这里**刻意不做** fuzzywuzzy `process.extractOne` 默认的 `full_process`(去标点):
    那一步会把 `[FULL]` 的方括号也抹掉,而方括号正是"前缀池"的判据;同时它会抹掉
    `♪`/`〜` 这类区分性字符,反而削弱正确条目。

    注意归一化**不能**单独解决问题:默认模型读出的 `DAYS` 本身就是干净的半角,
    那类截断要靠下面的读数权重。
    """
    return unicodedata.normalize("NFKC", title or "")


# OCR 形近字归一(只作用于 OCR 读数,曲库标题不经过这里)。
# 2026-10-21 实测(autodori-20261021-204718.log):单字标题「栞」(#513)被 ja 模型
# 读成「乗」(两个字下半都是木,字形相近),默认模型读出空串 → 匹配 0 分,
# SongRecognition 永远不命中 → 选歌死循环(点难度 → 认不出歌名 → 重新选歌)。
# 归一前已核对全曲库没有任何标题含「乗」,此替换不会误伤真标题;
# 以后再遇到新的形近字误读,往这张表里加即可。
_OCR_TITLE_MISREAD = str.maketrans({"乗": "栞"})


def fuzzy_match_song(name, other_names=()):
    """OCR 读数(可传多个模型的读数) → 曲库条目,返回 `(标题, 分数)`;无候选返回 None。

    三条判据,都对应一次实跑事故:

    1. **按前缀标记分池** —— 前缀不同就是不同的谱面(`[FULL] キズナミュージック♪`
       1485 音符 vs `キズナミュージック♪` 436 音符,见上方常量注释)。
    2. **NFKC 归一** —— 见 `_normalize_title`。
    3. **读数权重** = `len(读数) / 最长读数长度` —— 把"丢字多的那个读数"降权。
       (2026-10-21 新增第 4 条:**尾部括注分池**,见下方 `query_has_suffix`
       注释 —— 「ときめきエクスペリエンス！(月島麻里奈ver.)」实测事故。)

       为什么需要它:`ぎゅっDAYS♪` 在真实日志里,日文模型读成 `ぎゅっＤＡYＳト`,
       默认模型读成 `DAYS`。`DAYS` 恰好是 #120 的**完整标题**,满分 100;而正确条目
       `ぎゅっDAYS♪` 只有 93 分 —— 光看分数就会选到另一首曲子。默认模型这次**丢了
       4 个字符**,它的读数只解释了标题的一部分,证据强度本就应该打折。

       注意权重必须加在**读数**上、不能加在候选上:OCR 截断是双向的,`季節次死`
       (真值 `季節は次々死んでいく`)这种"只读出片段"的样本里,正确的长标题才是被
       候选长度系数冤枉的那个 —— 用候选长度会把这 7 条原本正确的样本改坏。

    用真实日志做过对照(`debug/_exp_eval_match.py`,以选歌界面「乐曲等级」数字为
    ground truth):现状 46/48,加 1+2 仍 46/48,加上第 3 条 → **48/48**,无回归。
    """
    readings = [
        r
        for r in (
            _normalize_title(t).translate(_OCR_TITLE_MISREAD)
            for t in (name,) + tuple(other_names)
        )
        if r
    ]
    if not readings:
        return None
    candidates = list(all_song_name_indexes.keys())
    longest = max(len(r) for r in readings)
    # 前缀池用**原始**读数判定(归一化理论上不动方括号,但别依赖这一点)
    query_has_prefix = _has_title_prefix(name) or (
        bool(other_names) and _has_title_prefix(other_names[0])
    )

    def best_of(pool):
        """池内取最高分。**同分时保持索引顺序**(与 `process.extractOne` 的
        `max()` 语义一致)—— 平行曲那类"只共享 `(平行歌曲)` 后缀"的读数会出现
        五路同分,换用别的平分判据会让结果在几条同分候选间漂移。"""
        best = None
        for key in pool:
            key_norm = _normalize_title(key)
            score = 0.0
            for r in readings:
                # 读数越短 = 丢的字越多 = 证据越弱
                score = max(score, fzwzfuzz.WRatio(r, key_norm) * len(r) / longest)
            if best is None or score > best[1]:
                best = (key, score)
        return best

    same_class = [k for k in candidates if _has_title_prefix(k) == query_has_prefix]
    # 后缀分池(2026-10-21):与前缀分池镜像 —— 读数带尾部括注(如
    # 「…（月島麻里奈vék）」)时,括注信息是屏上真实存在的,带后缀的变体
    # (#786/#790)才是正确目标;无后缀的基础曲(#24)靠"长查询被包含"的
    # 部分匹配 90 分压过变体 → 整首按错谱面。读数**不带**后缀时不收窄
    # (OCR 截断是双向的,基础曲读法保持原行为)。
    query_has_suffix = _has_title_suffix(name) or (
        bool(other_names) and _has_title_suffix(other_names[0])
    )
    if query_has_suffix:
        suffixed = [k for k in same_class if _has_title_suffix(k)]
        if suffixed and len(suffixed) != len(same_class):
            same_class = suffixed
    if same_class and len(same_class) != len(candidates):
        hit = best_of(same_class)
        if hit is not None and hit[1] >= _POOL_SCORE_FLOOR:
            return (hit[0], hit[1])
    hit = best_of(candidates)
    return (hit[0], hit[1]) if hit is not None else None


def _special_capable_id(sid: str) -> bool:
    """该曲目 id 是否真有 SPECIAL 谱面。

    谱面档位在曲库缓存里的键是 "0"~"4"(easy..special),有第 5 档才谈得上
    「超高难度」。只用本机曲库缓存判定,**不发任何网络请求**。
    """
    diff = (all_songs.get(str(sid)) or {}).get("difficulty") or {}
    return "4" in diff


def _prefer_special_id(title: str, sid: str) -> str:
    """同名多 id 时,改选真正带 SPECIAL 谱面的那一个。

    活动模式打的是写死的固定谱面,选错 id 就是整首按错谱面打;而 Bestdori 的
    同名条目里恰好存在**没有 SPECIAL 档**的那一个(2026-09-30 实测):

        「ときめきエクスペリエンス！ (月島まりなver.)」
          日文标题 -> 索引 #790 -> 只有 4 档,charts/790/special.json = 404
          简中标题 -> 索引 #786 -> 有 SPECIAL(Lv26, 1097 条指令)

    同一首歌靠"玩家写的是日文名还是简中名"决定能不能打,太脆。这里统一改成按
    **有没有 SPECIAL 谱面**这个客观信号挑 —— 仍然只做精确匹配,不引入模糊匹配。
    挑不出来(0 个,或多个候选都带 SPECIAL)就退回索引原值,不猜。
    """
    ids = ambiguous_titles.get(title) or [sid]
    capable = [i for i in ids if _special_capable_id(i)]
    if len(capable) == 1:
        picked = capable[0]
        if picked != sid:
            logging.info(
                "超高难度活动:标题 %r 命中多个 id %s,改选有 SPECIAL 谱面的 #%s",
                title, ids, picked,
            )
        return picked
    if len(capable) > 1:
        # 等级相同 = 同一份谱面(本仓库已用逐档 md5 验证过的等价判据,见 09-17 选曲
        # 分析),这种叫"同名重复条目",静默沿用即可;等级不同才是真歧义 —— 那说明
        # 确实存在两份不同的谱面,必须让人在日志里看见。
        levels = {
            (all_songs.get(i) or {}).get("difficulty", {}).get("4", {}).get("playLevel")
            for i in capable
        }
        if len(levels) > 1:
            logging.warning(
                "超高难度活动:标题 %r 有多个 id 带 SPECIAL 谱面且等级不同 %s,沿用 %s",
                title, capable, sid,
            )
        else:
            logging.debug(
                "超高难度活动:标题 %r 的候选 %s SPECIAL 等级同为 %s(同一份谱面),沿用 %s",
                title, capable, levels.pop(), sid,
            )
    elif len(ids) > 1:
        logging.warning(
            "超高难度活动:标题 %r 的候选 %s 都没有 SPECIAL 谱面,沿用 %s",
            title, ids, sid,
        )
    return sid


def resolve_special_song(name: str) -> Optional[tuple]:
    """「超高难度活动」配置的曲目 -> (标题, 曲目 id)。解析不出返回 None。

    取值允许两种写法:
      * 纯数字 —— 直接当 Bestdori 曲目 id 用(写死了就不再改,只提醒);
      * 标题 —— NFKC 归一后与曲库索引精确匹配(简中、日文标题都在索引里)。
        命中多个同名 id 时,由 `_prefer_special_id` 按「是否有 SPECIAL 谱面」
        挑出唯一可打的那个(见该函数说明)。

    **刻意不做模糊匹配**:这是用户写死在配置里的固定曲目,匹配错就是整首按错
    谱面打完全程。解析不出来宁可让脚本拒绝启动并打出提示,也绝不猜。
    """
    raw = (name or "").strip()
    if not raw:
        return None

    if raw.isdigit():
        sid = str(int(raw))
        if sid in all_songs:
            titles = [t for t in (all_songs[sid].get("musicTitle") or []) if t]
            # 手写 id 是明确的用户意图,不做替换;但没 SPECIAL 档的 id 必然取不到
            # 谱面,提前说清楚,免得到时候只看到一个 404。
            if not _special_capable_id(sid):
                logging.warning(
                    "超高难度活动:手写的曲目 id %s 没有 SPECIAL 谱面,谱面可能取不到",
                    sid,
                )
            return (titles[0] if titles else sid, sid)
        logging.error("超高难度活动:曲目 id %s 不在曲库中", sid)
        return None

    key = _normalize_title(raw)
    for title, sid in all_song_name_indexes.items():
        if _normalize_title(title) == key:
            return (title, _prefer_special_id(title, sid))
    logging.error(
        "超高难度活动:曲名 %r 不在曲库中(标题需与曲库一致,或直接写曲目 id)", raw
    )
    return None


def _get_orientation():
    """
    0, 1, 2, 3
    0: 0°
    1: 90°
    2: 180°
    3: 270°
    """
    try:
        command_list = [
            str(device.adb_path.absolute()),
            "-s",
            device.address,
            "shell",
            "dumpsys input|grep SurfaceOrientation",
        ]

        logging.debug(
            "get SurfaceOrientation command: {}".format(" ".join(command_list))
        )
        output = subprocess.check_output(command_list, text=True)
        match = re.search(r"SurfaceOrientation:\s*(\d+)", output)
        orientation = int(match.group(1))
        logging.debug("SurfaceOrientation: {}".format(orientation))
        return orientation
    except Exception as e:
        logging.error(f"Failed to get SurfaceOrientation: {e}")
        return 0


def save_song(name):
    global current_song_name, current_song_id, current_chart, current_orientation
    current_song_name = name
    # 同名多条目(閃光 / オレンジ …)时标题查不到唯一 id,用识别阶段选定的那个。
    # 只有当记录与本次标题一致时才采信,避免跨首歌残留。
    if _resolved_song_id and _resolved_song_id[0] == name:
        current_song_id = _resolved_song_id[1]
    else:
        current_song_id = all_song_name_indexes[current_song_name]
    # 歌名一确定就立刻打日志:下面 Chart()/notes_to_actions()/actions_to_MNTcmd()
    # 是重活(要拉取谱面、把上万个 note 解算成触控指令,实测耗时 5~15s),
    # 若把日志放在它们之后,GUI 要到"打歌即将开始"才收到歌名 —— 这正是
    # "选好歌后日志不显示歌名、打完才补上"的根因。用 INFO 级确保不被过滤。
    logging.info("Save song: {}".format(name))
    current_chart = _load_chart(current_song_id, current_song_name)
    current_orientation = _get_orientation()
    current_chart.actions_to_MNTcmd(
        (mnt.max_x, mnt.max_y), current_orientation, OFFSET, CMD_SLICE_SIZE
    )


_first_lane_cache: dict = {}


def _load_chart(song_id: str, name: str) -> "Chart":
    """按「曲目 id + 当前难度」建 Chart 并解算成动作。

    换谱面与正常选歌必须走**同一条**建表路径(2026-10-30 就踩过一次):换谱面时
    只调了 notes_to_actions、漏了 actions_to_MNTcmd,`command_builder` 没建出来,
    打歌一开局就 `'Chart' object has no attribute 'command_builder'`,整局被判失败。
    """
    chart = Chart((song_id, DIFFICULTY), name)
    chart.notes_to_actions(current_player.resolution, DEFAULT_MOVE_SLICE_SIZE)
    return chart


def _first_judgment_lanes(song_id: str, difficulty: str) -> set:
    """谱面**第一个判定时刻**落在哪些轨道上;读不到返回空集。

    同名不同谱面的条目靠它区分:シル・ヴ・プレジデント 的 #389 首音在 lane 6,
    #462 在 lane 2(2026-10-30 实测,两首 hard 都是 21 级、等级分不开)。
    同时刻的同时押要整组返回,单点就返回一个元素的集合。

    ⚠ 必须走 `Chart` 解析:Bestdori 原始 JSON 只有 beat,没有 time,只有
    `_process_time_chart` 之后才知道「第一个判定时刻」是哪一条。解析一次约几十
    毫秒、且谱面本来就在缓存里,同名候选最多两首,一次性开销可以接受(结果按
    曲目+难度记住,同一局不会重复解)。
    """
    key = (str(song_id), str(difficulty))
    if key in _first_lane_cache:
        return _first_lane_cache[key]
    try:
        data = Chart((song_id, difficulty), song_id)._chart_data
    except Exception:  # noqa: BLE001 —— 取不到谱面就不参与判定
        return set()
    events = []
    for n in data:
        kind = n.get("type")
        if kind in ("Single", "Directional"):
            t = n.get("time")
            if t is None:
                continue
            width = n.get("width") or 1
            for lane in range(n["lane"], n["lane"] + width):
                events.append((t, lane))
        elif kind in ("Slide", "Long"):
            for c in n.get("connections") or []:
                if c.get("hidden") or c.get("time") is None:
                    continue
                events.append((c["time"], c["lane"]))
                break  # 只取这条长条/滑条的第一个可见连接
    if not events:
        _first_lane_cache[key] = set()
        return set()
    first_t = min(t for t, _ in events)
    lanes = {lane for t, lane in events if t - first_t <= 1.0}
    _first_lane_cache[key] = lanes
    return lanes


# 屏幕分轨索引与谱面轨道的容差(2026-10-30)。检测带在判定线上方,游戏按透视
# 显示:同一个音符在带内的 x 比在判定线上的 x 更靠中间,越靠边的轨道偏得越多
# (实测 lane 6 的音符落在「第 5 条分轨」里)。所以不能要求索引完全相等,
# 允许 ±1;两条候选的轨道集合差得开时才敢用它定夺。
_LANE_HIT_TOLERANCE = 1


def _pick_id_by_first_lane(ids, difficulty: str, hits) -> Optional[str]:
    """用「首音触发落在哪几条分轨」在同名候选里挑 id;挑不出来返回 None。

    两侧都用 ±1 容差(见 `_LANE_HIT_TOLERANCE`)。只有**恰好命中一个**候选时才
    返回,多中/零中一律放弃 —— 宁可按默认谱面打(或跳过),也不猜。
    """
    if not ids or not hits:
        return None
    matched = []
    for sid in ids:
        lanes = _first_judgment_lanes(sid, difficulty)
        if not lanes:
            continue
        if any(abs(lane - h) <= _LANE_HIT_TOLERANCE for lane in lanes for h in hits):
            matched.append(sid)
    return matched[0] if len(matched) == 1 else None


def _maybe_switch_chart_by_first_note() -> None:
    """首音触发后,用触发轨道把同名候选里的谱面敲定(2026-10-30)。

    背景(autodori-20261030-001741.log):シル・ヴ・プレジデント 在曲库里有
    **两个同名条目** #389 / #462(EX 版),hard 都是 21 级,靠屏上「乐曲等级」
    分不开;`_pick_song_id` 按默认项取了 #462,而游戏里选中的是 #389 →
    整首按错谱面打,3 秒掉光生命,观感就是「完全对不上谱面」。

    首音触发时机脚本已经知道变化落在哪几条分轨(`_FIRST_NOTE_DIAG["lane_hits"]`),
    而两张谱面的首音轨道不同(#389 = lane 6、#462 = lane 2),一比就能定。
    这一步在**下发任何音符之前**,换谱面只是一次 Chart 重建(谱面已在缓存里)。
    """
    global current_chart, current_song_id
    ids = list(_ambiguous_song_ids)
    if len(ids) < 2 or current_chart is None:
        return
    # 候选列表来自「上一次选歌」,标题对不上说明已换歌/换模式,不能拿旧候选去改谱面。
    if not _resolved_song_id or _resolved_song_id[0] != current_song_name:
        return
    hits = _FIRST_NOTE_DIAG.get("lane_hits") or ()
    if not hits:
        logging.info(
            "同名候选 %s:首音没有记录到变化轨道,无法判谱面,沿用 #%s",
            ids, current_song_id,
        )
        return
    picked = _pick_id_by_first_lane(ids, DIFFICULTY, hits)
    if picked is None:
        logging.warning(
            "同名候选 %s:首音落在分轨 %s,分不开是哪一首,沿用 #%s",
            ids, list(hits), current_song_id,
        )
        return
    if picked == current_song_id:
        logging.debug(
            "同名候选 %s:首音分轨 %s 与 #%s 一致,正常打", ids, list(hits), picked
        )
        return
    logging.warning(
        "同名候选 %s:首音落在分轨 %s,与 #%s 的谱面不符(#%s 首音在 %s),"
        "改用 #%s 的谱面",
        ids, list(hits), current_song_id, picked,
        sorted(_first_judgment_lanes(picked, DIFFICULTY)), picked,
    )
    current_song_id = picked
    current_chart = _load_chart(picked, current_song_name)
    # 与 save_song 一致:先建出 command_builder 与第一批命令,否则打歌循环一进
    # 就 AttributeError(见 _load_chart 注释)。
    current_chart.actions_to_MNTcmd(
        (mnt.max_x, mnt.max_y), current_orientation, OFFSET, CMD_SLICE_SIZE
    )


def _prepare_special_song(cli_value: str) -> None:
    """超高难度活动:解析并预加载那唯一一张谱面。

    必须在 post_task 之前算好 —— 常规流程是选歌节点 OCR 出曲名后才调
    save_song();活动模式没有选歌步骤,曲目只能来自配置。提前算还顺带把
    5~15s 的谱面解算挪到开演之前,不会拖到「玩家已经进入活动」之后才开始。
    """
    global _resolved_song_id
    raw = (
        (cli_value or "").strip()
        or str(_runtime_config().get("special_song", "") or "").strip()
        or DEFAULT_SPECIAL_SONG
    )
    resolved = resolve_special_song(raw)
    if resolved is None:
        logging.error("超高难度活动:无法解析曲目 %r,停止", raw)
        _shutdown(1, "超高难度活动曲目无效")
    title, sid = resolved
    # 该模式只打 SPECIAL 档;没有这一档的 id 必然取不到谱面(404)。在这里挡下来,
    # 比让 Chart() 在解谱时抛异常更早、更明确 —— 且发生在 post_task 之前,
    # 不会在游戏里打到一半才崩。
    if not _special_capable_id(sid):
        logging.error("超高难度活动:曲目 %s (#%s) 没有 SPECIAL 谱面,停止", title, sid)
        _shutdown(1, "超高难度活动曲目无 SPECIAL 谱面")
    # 先落 _resolved_song_id,save_song 才会采信这个 id(同名曲目标题反查不到唯一 id)
    _resolved_song_id = (title, sid)
    logging.info("超高难度活动:曲目 %s (#%s),难度 %s", title, sid, DIFFICULTY)
    save_song(title)


def _reload_photogate():
    """每首歌开始前重读 data/config.yml 的 photogate_latency_ms,
    让 GUI 自动校准在单次运行里也能连续生效(不用重启 bot)。

    同时读 timing.measure_anchor(L2 锚点在线实测的总开关),口径与 photogate 一致:
    改完下一首就生效,不必重启。"""
    global PHOTOGATE_LATENCY, PHOTOGATE_MEASURE_ENABLED
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(cfg, dict):
            timing = cfg.get("timing", {})
            if isinstance(timing, dict) and timing.get("photogate_latency_ms") is not None:
                PHOTOGATE_LATENCY = int(timing["photogate_latency_ms"])
                logging.debug(
                    "PHOTOGATE_LATENCY reloaded to {}ms".format(PHOTOGATE_LATENCY)
                )
            if isinstance(timing, dict) and timing.get("measure_anchor") is not None:
                PHOTOGATE_MEASURE_ENABLED = bool(timing["measure_anchor"])
                logging.debug(
                    "PHOTOGATE_MEASURE_ENABLED reloaded to %s",
                    PHOTOGATE_MEASURE_ENABLED,
                )
    except Exception as e:
        logging.debug("Failed to reload photogate: {}".format(e))


def _dump_misalign_sample(
    song_label, n_events, n_ideal, miss, n_wait_ideal, n_wait_actual, samples
):
    """把一份错位样本写成文本,返回路径;写失败返回 None。

    2026-10-29(P2):错位局以前只有一行 WARNING,根本判不出原因 —— 到底是
    补偿把 wait 抽干了(诊断口径问题),还是命令流真的乱序(下发问题)。
    这里把前 N 条「实际 vs 理想」对照落盘,再配上 wait 的应发/实发条数:
      - ideal_extra:理想流有、实际流没发 → 大头应该是「wait 被抽干」;
        真乱序的话这里会大量出现在 d/m/u 上,那才是下发链出事。
      - actual_only / ideal_exhausted:实际多出一条理想没有的命令,通常是
        两流从更早的地方就分叉了,顺着 event 序号往回看前几条样本。
    只在对齐失败时调用,不进正常路径,也不影响打歌。
    """
    try:
        os.makedirs("debug/lagdump", exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', "_", song_label).strip() or "song"
        path = "debug/lagdump/%s-%s-misalign.txt" % (
            time.strftime("%Y%m%d-%H%M%S"), safe[:60],
        )
        lines = [
            "# 下发链错位样本",
            "# 曲目: %s" % song_label,
            "# 时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "# 命令流 %d 条;理想音符命令 %d 条(d/m/u 参与对齐);错位 %d 条"
            % (n_events, n_ideal, miss),
            "# 无音区 wait: 应发 %d 条 / 实发 %d 条(差 %d)"
            % (n_wait_ideal, n_wait_actual, n_wait_ideal - n_wait_actual),
            "#",
            "# 字段: event=命令流序号(含 commit,从 0 起) kind=错位类型",
            "#       actual=实发命令首字母 expect=该位置理想应为 skipped=跳过几条理想项",
            "#       ideal_j=出错时的理想流下标",
        ]
        for s in samples:
            lines.append(
                "[{event:06d}] {kind:<15s} actual={actual} expect={expect} "
                "skipped={skipped} ideal_j={ideal_j}".format(**s)
            )
        if not samples:
            lines.append("(无样本)")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception as e:  # noqa: BLE001 — 诊断落盘绝不影响打歌
        logging.warning("服务端执行滞后[%s]: 错位样本落盘失败(%s)", song_label, e)
        return None


#: 曲内漂移多大就值得提醒(ms)。经验:±10ms 内判定基本看不出来,20ms 起
#: 后半段开始掉 FAST/SLOW(2026-10-29 #395 实测 -31ms → 28 个 FAST;
#: 同一场其它首 ±6ms 都是 0~1 个)。
_DRIFT_WARN_MS = 20.0


def _drift_warning(growth_ms):
    """曲内漂移 → 一句告警文案;|增长| 未达阈值返回 None。

    文案口径(2026-10-29 用户反馈「本曲下发偏早 X ms(仅诊断)」读着别扭):
    说清「往哪边偏、偏了多少、会导致什么」,不写「下发 / 指针 / 配准」这类
    内部词;也**不能**写「仅诊断」—— 漂移会直接决定后半段掉 FAST 还是 SLOW,
    只是本行数据本身来自诊断采样。
    """
    if growth_ms is None or abs(growth_ms) < _DRIFT_WARN_MS:
        return None
    early = growth_ms < 0
    return "本曲%s(累计 %.0fms),后半段可能出 %s" % (
        "越打越早" if early else "越打越晚",
        abs(growth_ms), "FAST" if early else "SLOW",
    )


def _dump_server_lag_async(
    song_label: str, chart_actions: list, fb_snap: dict = None
) -> None:
    """歌结束后,把「服务端实际执行时刻 vs 谱面理想时间线」的滞后曲线写进日志。

    背景:FULL 曲后半段越打越慢、批量出 SLOW(2026-10-21 11:43 场,[FULL]
    FIRE BIRD slow≈104,而同 photogate=43ms 的短歌全 PERFECT)。下发链各环节
    的可观测补偿值(Adjust offset / total_cost 尖峰)全程平稳、photogate 恒定,
    唯独「服务端执行时间线本身的滞后累计」从未直接测过 —— minitouch jlog 为
    每条命令上报 start/end(服务端时钟),cmd_log_list 一直在存,但没消费过。
    判读(下一场实测):
      - 曲内增长(lag@100% - lag@10%)≈0 而 SLOW 仍复现 → 游戏侧(模拟器
        音频/渲染时钟相对墙钟漂移、游戏主线程停顿)实锤;
      - 增长随曲长/批次数上升(几十 ms 级)→ 本机下发链补偿不足,按斜率修。
    只在歌结束后由 daemon 线程跑,不碰发布时序;生命耗尽/暂停中断提前退出的
    局不采(需要完整一首才有意义)。chart_actions 在调用点先取好引用 —— 下一
    首歌会整体换掉 current_chart,worker 里不能再读全局。

    理想时间线的重建规则必须与 actions_to_MNTcmd 的实际发射一致:
      - 动作流 = [action, wait, action, wait, ...](wait.length = 相邻动作谱面
        时间差,即理想间隔,补偿只改实际值不改理想值);
      - finger=None 的 d/m/u 在发射前被丢弃(见 actions_to_MNTcmd 注释);
      - commit 不参与对齐;
      - **'w' 两边都跳过**(2026-10-29):实际发射的是补偿后的 wait,贴着门槛
        的会被排水抽成 0 而整条不发,理想流没法逐条重建。wait 只统计
        「应发/实发」条数(差值 = 排水/取整吃掉的那部分)。

    2026-10-22 扩展:原「曲内增长」统计的是全部带时命令(d/m/u/w),把音符处滞后
    与无音区 wait 超时混在一起(VIOLET LINE 增长 66.4ms 却 fast=284 的反例说明
    增长大头可能落在无音区)。现拆成两组输出,并把逐条 (cmd, ideal_ms, lag_ms)
    曲线落盘 debug/lagdump/*.json,供离线脚本 _diag_lagan.py 逐曲对照
    fast/slow/miss 判读归属(下发链补偿不足 vs 游戏侧时钟漂移)。
    注意 lag@N% 是「位置百分位」(歌进行到 N% 时的滞后),不是分布分位数 ——
    曲内增长 = 曲尾滞后 - 曲首滞后,即累计漂移量。
    """
    def _worker():
        try:
            # 发布线程领先 2~5s,jlog 按服务端执行节奏回传 —— 必须等静默才能
            # 拿到曲尾数据。下一首歌开始时 cmd_log_list 会被清空,所以每轮整表
            # 快照、保留最长一份;发现列表变短(已被下一首 reset)就停。
            best = []
            last_growth = time.perf_counter()
            t0 = last_growth
            while time.perf_counter() - t0 < 20.0:
                with cmd_log_list_lock:
                    snap = list(cmd_log_list)
                if len(snap) > len(best):
                    best = snap
                    last_growth = time.perf_counter()
                elif len(best) and len(snap) < len(best):
                    break
                if time.perf_counter() - last_growth >= 1.0:
                    break
                time.sleep(0.2)
            events = best
            if len(events) < 50:
                return

            # 重建理想发射序列 [(type, ideal_ms), ...]。
            # commit('c') 不参与对齐:它的条数受补偿取整影响(理想流里的 'c'
            # 实际可能没发,publish 的尾部 commit 恒定但与 action 无关)。
            # 注意两套命名:动作类型是 down/move/up(见 notes_to_actions),
            # minitouch 指令/jlog 是 d/m/u —— 必须映射,否则逐条对不上
            # (2026-10-21 实机每首歌都报「对齐失配过高」即此因,离线重放
            # _diag_replay.py 已证实两流条数与顺序完全一致,纯命名错位)。
            #
            # 2026-10-29 修口径:'w' 不再进对齐流,两边都跳过。
            # 旧版把 'w' 也放进理想流,判据是「round(length) > 0.01 才发」;
            # 但真实发射的是**补偿后**的 wait(actions_to_MNTcmd 里 wait_for 先
            # 被 _a2c_offset 排水、再被 _a2c_rounded_loss 校正),贴着门槛的
            # 间隙会被抽成 0 而整条不发 → 理想流有、实际流没有,序列就错位。
            # 这个机制在合成场景里可复现(_test_lagdump.py 场景 B)。
            # ⚠ 但它**不足以解释现场量级**:离线用真实谱面+日志里的 offset 重算
            # #684,整首只有 88 条 wait 会少发(理想 8473 条),而当日 dump 报
            # 8926/28010(32%);#763 同量级(21/5369 vs 1545/16944)。也就是
            # 说那个 32% 还有别的来源,当时没有 jlog 证据无法定位。
            # 修口径的价值在于把变量减少一个:此后 d/m/u 的失配只可能是真·
            # 下发缺命令/乱序,而不是 wait 口径造出来的噪声。wait 的条数改为
            # 单独统计(应发 vs 实发),差值留给 P3 判排水是否过头。
            _ACT_TO_CMD = {"down": "d", "move": "m", "up": "u"}
            t0_chart = chart_actions[0]["time"]
            ideal = []
            ideal_t = t0_chart
            n_wait_ideal = 0
            for a in chart_actions:
                at = a["type"]
                if at == "wait":
                    # 理想时间照旧推进(后面音符的理想时刻要用到),
                    # 计数按未补偿的旧判据 —— 它是「不补偿时该发几条」的基准。
                    if round(a["length"]) > 0.01:
                        n_wait_ideal += 1
                    ideal_t += a["length"]
                elif a.get("finger", None) is None:
                    continue
                else:
                    ideal.append((_ACT_TO_CMD[at], ideal_t))

            base = events[0].start_time
            pairs = []  # (指令字母, 理想时刻ms(相对t0_chart), 滞后ms) —— 曲线落盘用
            miss = 0
            n_wait_actual = 0
            # 错位样本(P2):只留前 60 条 —— 完整命令流上万条,判读用不到,
            # 也不该为诊断写几十万行文本。只在对齐失败时才落盘(见下)。
            SAMPLE_LIMIT = 60
            samples = []
            j = 0
            for ev_no, ev in enumerate(events):
                et = ev.cmd.split(" ")[0]
                if et == "c":
                    continue
                if et == "w":
                    # 'w' 只计数,不参与对齐(见上面的修口径注释)。
                    n_wait_actual += 1
                    continue
                if j < len(ideal) and et != ideal[j][0]:
                    # 实际流比理想流少了紧随其后的命令时,向前找最多 3 个理想
                    # 项重新对齐;找不到按多余事件丢弃。
                    k = j + 1
                    while k < len(ideal) and k - j <= 3 and ideal[k][0] != et:
                        k += 1
                    if k < len(ideal) and k - j <= 3:
                        miss += k - j
                        if len(samples) < SAMPLE_LIMIT:
                            samples.append({
                                "event": ev_no, "kind": "ideal_extra",
                                "actual": et, "expect": ideal[j][0],
                                "skipped": k - j, "ideal_j": j,
                            })
                        j = k
                    else:
                        miss += 1
                        if len(samples) < SAMPLE_LIMIT:
                            samples.append({
                                "event": ev_no, "kind": "actual_only",
                                "actual": et,
                                "expect": ideal[j][0] if j < len(ideal) else "-",
                                "skipped": 0, "ideal_j": j,
                            })
                        continue
                if j >= len(ideal):
                    miss += 1
                    if len(samples) < SAMPLE_LIMIT:
                        samples.append({
                            "event": ev_no, "kind": "ideal_exhausted",
                            "actual": et, "expect": "-",
                            "skipped": 0, "ideal_j": j,
                        })
                    continue
                lag = ev.start_time - base - (ideal[j][1] - t0_chart)
                pairs.append((et, ideal[j][1] - t0_chart, lag))
                j += 1
            lags = [p[2] for p in pairs]
            if len(lags) < 50 or miss > 0.02 * len(events):
                # 对齐失败只说明这份诊断数据算不出来(命令流重建与真实命令流
                # 对不上),与打歌本身无关 —— 打歌在那之前已经结束。文案写明,
                # 免得用户看到 WARNING 以为打歌出了问题。措辞口语化、压长度,
                # 让 GUI 控制台一行放得下(2026-10-26 用户反馈分行难读)。
                # 2026-10-28 用户反馈「命令流与谱面对不上」拗口:改成
                # 「N/M 条指令与谱面错位」,先说数量再说动作,并且把
                # 「仅诊断」讲明 —— 这条 WARNING 只影响滞后统计。
                # 2026-10-29(P2):同时把前 60 条「实际 vs 理想」对照落盘,
                # 否则这条 WARNING 永远是孤零零一行,没法判错位是排水抽干
                # wait 还是真的命令流乱了。
                # 拆两行(2026-10-29 用户要求):GUI 日志栏约 57 个半角宽,
                # 原来那一行 90+ 个半角会在「2843/15701」与「条指令与谱面
                # 错位」之间硬折,一句话被切成三行、长短参差。现在第一行只放
                # 数量(≤50 半角),第二行放曲名与处理结果(≤54 半角),曲名再长
                # 也不会折行;GUI 侧只解析第一行的比例(见 gui.LAG_MISALIGN_RE),
                # 曲名不进正则、也不再参与归属判断(报告永远属于最近一首)。
                # 两行都用 WARNING:GUI 的日志栏会过滤掉不在白名单里的 INFO,
                # 用 INFO 发第二行等于只有 debug 日志里有,界面上还是只剩半句。
                logging.warning(
                    "服务端执行滞后: 有 %d/%d 条与谱面错位(仅诊断)",
                    miss, len(events),
                )
                logging.warning("  %s 本局跳过统计,不影响打歌", song_label)
                path = _dump_misalign_sample(
                    song_label, len(events), len(ideal), miss,
                    n_wait_ideal, n_wait_actual, samples,
                )
                if path:
                    logging.info("服务端执行滞后[%s]: 错位样本已落盘 %s",
                                 song_label, path)
                return
            n = len(lags)
            v = [lags[min(n - 1, int(n * p))] for p in (0.1, 0.5, 0.9, 1.0)]
            logging.info(
                "服务端执行滞后[%s]: 命令 %d 条(失配 %d), lag@10%%=%.1f @50%%=%.1f "
                "@90%%=%.1f @100%%=%.1f ms, 曲内增长=%.1fms",
                song_label, len(events), miss, v[0], v[1], v[2], v[3], v[3] - v[0],
            )
            # 2026-10-28 诊断:在线反馈 lag 与 dump(绝对口径)对照。
            # 两者理论同口径,实测会分叉(±5ms vs -98ms / 243ms 虚高),分叉量
            # 就是测量端退化的程度;queue 空兜底条数用来判是不是那条路造成的。
            if fb_snap:
                logging.info(
                    "滞后反馈对照[%s]: 在线 lag 终值 %.1fms, dump lag@100%%=%.1fms,"
                    " 差值 %.1fms; wait 回执 %d 条, 队列空兜底 %d 条%s",
                    song_label, fb_snap.get("lag", 0.0), v[3],
                    fb_snap.get("lag", 0.0) - v[3],
                    fb_snap.get("wait_recv", 0),
                    fb_snap.get("fallback_n", 0),
                    ", 本曲已停注入" if fb_snap.get("off") else "",
                )

            def _pos_pct(seq, p):
                return seq[min(len(seq) - 1, int(len(seq) * p))]

            # 音符组(d/m/u,判定点命令):SLOW/FAST 归属要看这里的曲内增长,
            # 不是上面全命令混计的增长 —— 无音区 wait 超时不影响音符判定。
            # 2026-10-29 起 pairs 只含 d/m/u('w' 不参与对齐),这一组就是全量。
            note_lags = [p[2] for p in pairs if p[0] != "w"]
            note_growth = 0.0
            if len(note_lags) >= 50:
                a, b = _pos_pct(note_lags, 0.1), _pos_pct(note_lags, 1.0)
                note_growth = b - a
                logging.info(
                    "服务端执行滞后[%s]: 音符命令 %d 条, lag@10%%=%.1f @50%%=%.1f "
                    "@90%%=%.1f @100%%=%.1f ms, 音符曲内增长=%.1fms",
                    song_label, len(note_lags), a,
                    _pos_pct(note_lags, 0.5), _pos_pct(note_lags, 0.9), b, note_growth,
                )
                # 2026-10-29:漂移超阈值从 DEBUG/INFO 提到 WARNING —— 用户据此
                # 一眼分清「这首是下发链漂了」还是「gate 没对」,别去乱调 gate。
                _warn = _drift_warning(note_growth)
                if _warn:
                    logging.warning(_warn)
            # 无音区 wait 不再进对齐(补偿会抽干一部分,'w' 条数两边对不齐),
            # 但「该发几条 / 实发几条」本身是 P3(时序)要判的:差值就是被
            # _a2c_offset 排水吃掉的间隙数。这里改成一条计数行。
            logging.info(
                "服务端执行滞后[%s]: 无音区 wait 应发 %d 条 / 实发 %d 条(差 %d 条 = "
                "补偿抽干或未发)",
                song_label, n_wait_ideal, n_wait_actual,
                n_wait_ideal - n_wait_actual,
            )

            # 逐条曲线落盘,供 _diag_lagan.py 离线对照结算读数。文件会积累,
            # 定期清理(同 debug/firstnote 惯例)。写失败只告警,不影响任何流程。
            try:
                os.makedirs("debug/lagdump", exist_ok=True)
                safe = re.sub(r'[\\/:*?"<>|]', "_", song_label).strip() or "song"
                path = "debug/lagdump/%s-%s.json" % (
                    time.strftime("%Y%m%d-%H%M%S"), safe[:60],
                )
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "meta": {
                                "song": song_label,
                                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "events": len(pairs),
                                "note_cmds": len(note_lags),
                                # 2026-10-29:wait 不再配对,改记「应发/实发」条数
                                # (旧键 wait_cmds / gap_growth_ms 已废,离线脚本
                                # 若有引用要一起改)。
                                "wait_ideal": n_wait_ideal,
                                "wait_actual": n_wait_actual,
                                "miss": miss,
                                "note_growth_ms": round(note_growth, 2),
                            },
                            # [cmd, ideal_ms, lag_ms];ideal 相对谱面首动作,
                            # lag = 实际执行 - 理想(>0 即偏晚)
                            "events": [[c, round(i, 2), round(g, 2)] for c, i, g in pairs],
                        },
                        f, separators=(",", ":"),
                    )
                logging.info("服务端执行滞后[%s]: 曲线已落盘 %s", song_label, path)
            except Exception as e:  # noqa: BLE001 — 诊断落盘绝不影响打歌
                logging.warning("服务端执行滞后[%s]: 曲线落盘失败(%s)", song_label, e)
        except Exception as e:
            logging.warning("服务端执行滞后统计失败(%s): %s", song_label, e)

    threading.Thread(
        target=_worker, daemon=True, name="server-lag-dump"
    ).start()


def play_song(context=None):
    # 每首真正开打时 +1:这里才是"第几首"的唯一可靠计数点(见 _song_play_seq 注释)。
    global _song_play_seq
    _song_play_seq += 1
    logging.info("开始打歌 · 第 %d 首", _song_play_seq)
    _reload_photogate()
    cmd_log_list.clear()
    reset_callback_data()
    reset_lag_feedback()
    # 每首新歌清掉看门狗的卡死事件:上一首歌里事件置位后本循环已 raise,
    # 这里不清的话,残留事件会在下一首的首音等待里立刻误触发放弃。
    _STUCK_EVENT.clear()
    # 崩局诊断(2026-10-28):首音触发参数按曲重置,别让上一首的值串到下一首。
    _FIRST_NOTE_DIAG.clear()
    wait_first = get_runtime_info(current_player.resolution)["wait_first"]
    logging.info(
        "打歌: %s (#%s-%s), 动作%s, photogate=%sms, 检测带y=%s-%s",
        current_song_name,
        current_song_id,
        DIFFICULTY,
        len(current_chart.actions),
        PHOTOGATE_LATENCY,
        wait_first["from"],
        wait_first["to"],
    )

    def _get_wait_time():
        wait_for = 0.0
        index = current_chart.actions_to_cmd_index
        for action in current_chart.actions[index - CMD_SLICE_SIZE : index]:
            if action["type"] == "wait":
                wait_for += action["length"]
        return wait_for

    def _adjust_offset():
        global callback_data
        total_cost = 0.0
        for type_ in ["up", "down", "move", "wait", "interval"]:
            type_data = callback_data[type_]
            total = type_data["total"]
            if total != 0:
                total_cost += type_data["total_offset"] - OFFSET[type_] * total
                OFFSET[type_] = type_data["total_offset"] / total

        current_chart._a2c_offset += total_cost
        logging.debug("Adjust offset: {}".format(OFFSET))
        logging.debug("Adjust _actions_to_cmd_offset: {}".format(total_cost))

    wait_first_note()

    # 同名不同谱面:首音触发的那一刻才知道游戏里到底是哪一首(2026-10-30)。
    # 必须放在这里 —— 首音已经锚好、还没下发任何音符,换谱面不影响时间线
    # (两张谱面都从自己的首音开始打)。
    _maybe_switch_chart_by_first_note()

    # 打歌中生命耗尽检测:每 ≥LIFE_CHECK_INTERVAL 秒检查一次「演出失败」弹窗。
    # 弹窗出现后游戏会一直等待,检测不用太密;检测耗时从本次 sleep 里扣除
    # (仅在 sleep 预算充足时执行),保证下一批音符按谱面时间线准时发布,不会
    # 因检测而整体提前/延后。命中即抛异常,由 Play 返回失败走 on_error。
    last_life_check = 0.0
    LIFE_CHECK_INTERVAL = 1.0  # 秒
    # 生命检测统计。检测本身一直是生效的(实测 11/11 都处理了),但「卡在演出
    # 失败弹窗」是偶发的,靠这几个数能直接区分成因:
    #   starved 大   = 切片太密、sleep 预算不足,检测根本没机会跑
    #   precheck 大  = 白底弹窗被亮度预检漏掉
    #   checks 正常却没 hit = OCR 没读出来
    life_stats = {"checks": 0, "hit": 0, "precheck": 0, "error": 0, "starved": 0}
    starve_since = None

    def _log_life_stats():
        logging.info(
            "生命检测汇总: 执行 %d 次(命中 %d), 亮度预检跳过 %d, OCR 异常 %d, 因切片过密跳过 %d",
            life_stats["checks"],
            life_stats["hit"],
            life_stats["precheck"],
            life_stats["error"],
            life_stats["starved"],
        )

    # 自适应发布余量: 时间轴由服务端 wait 推进,python 必须在服务端把本批命令
    # 执行完之前发布下一批。固定 3ms 余量在「平衡」电源计划下会被 CPU 频率
    # 调节造成的偶发长停顿吃掉 → 服务端空转 → 之后所有音符按晚(偶发批量 miss)。
    # 这里按实测的「构建+发布」耗时 EMA 自适应放大余量(3~60ms),快机不变、慢机自愈。
    pub_margin_ms = 3.0
    overrun_ema = 0.0
    prev_sleep_end = None
    # 漏点诊断:每批发布时记录「实际发布时刻 vs 该批首个动作的谱面时间」。
    # 偏差 >判定窗口(约 ±50ms)的批次就是漏音符的嫌疑对象 —— 2026-10-17 用户
    # 反馈 I wonder/Merry Merry Thanks!! 固定漏 1-2 个且"完全没打",静态审计
    # (手指分配/交接顺序/坐标/判定点数)全部正确,只剩发布时序这一层可查。
    batch_no = 0
    batch_first_time = 0.0
    timeline_t0 = time.perf_counter()
    # 已补进理想指针的 wait 欠账(2026-10-29,见每批发布后的补齐代码):
    # 用「skipped_wait_ms 的增量」记账而不是逐条下发,失败/中断都不会重记。
    _fb_skip_applied = 0.0

    while True:
        now = time.perf_counter()
        if prev_sleep_end is not None:
            build_ms = (now - prev_sleep_end) * 1000.0
            overrun = build_ms - pub_margin_ms
            overrun_ema = 0.7 * overrun_ema + 0.3 * max(0.0, overrun)
            pub_margin_ms = min(60.0, max(3.0, 3.0 + 2.0 * overrun_ema))
            if pub_margin_ms >= 25.0 and int(pub_margin_ms) % 25 == 0:
                logging.debug(
                    "发布余量已提升到 %.0fms(主机抖动 EMA %.1fms)",
                    pub_margin_ms, overrun_ema,
                )
        if batch_no > 0:
            # 本批(刚 publish 的)首个动作的谱面时间 vs 实际发布时间
            drift_ms = (time.perf_counter() - timeline_t0) * 1000.0 - batch_first_time
            if abs(drift_ms) > 20.0:
                logging.debug(
                    "发布批次#%d 漂移 %.1fms(谱面起点 %.0fms, margin=%.1fms)",
                    batch_no, drift_ms, batch_first_time, pub_margin_ms,
                )
        current_chart.command_builder.publish(mnt, block=False)
        wait_time = _get_wait_time()
        sleep_s = max(0, wait_time - pub_margin_ms) / 1000.0
        # 告诉卡死看门狗「脚本还在推进」:打歌期间 Play 动作阻塞,而看门狗现在
        # 靠"画面静止"判卡死,这里再补一个脚本侧的进展心跳(音符密集时切片短、
        # 画面也一直在动,双保险)。
        _STUCK_WATCHDOG.ping()

        now = time.perf_counter()
        if context is not None and now - last_life_check >= LIFE_CHECK_INTERVAL:
            if sleep_s >= 0.2:
                starve_since = None
                check_t0 = time.perf_counter()
                # 本拍只抓一张图,生命检测与暂停检测共用(抓图实测 ~5ms,
                # 一秒抓两次纯属浪费,而且会吃掉下一批音符的时间预算)
                frame = _grab_screen()
                # 2026-10-20 凌晨场:掉线时 IPC 挂死/返回 None,_grab_screen 会
                # 返回 None —— 不能 continue(会跳过循环尾的 sleep 与下一批
                # 计算,重复发布当前命令),也不能把 None 传进检测函数(内部会
                # 白重抓一次,每拍最多卡 2s)。本拍只记 error 交给看门狗的掉线
                # 短路径收尾,循环继续走完 sleep 与下一批计算。
                if frame is None:
                    life_stats["error"] += 1
                    last_life_check = time.perf_counter()
                    sleep_s = max(0.0, sleep_s - (time.perf_counter() - check_t0))
                else:
                    # 崩局诊断(2026-10-28):首音后约 1s 复用这张已抓的帧存图,
                    # 不额外抓图 —— 崩局时对照 trigger 截图判断锚点偏早/偏晚。
                    _maybe_snap_firstnote_post(frame)
                    life_result = _life_exhausted_on_screen(context, frame)
                    life_stats["checks"] += 1
                    # 注意:_life_exhausted_on_screen 返回的状态名与统计键名不同名
                    # (函数给 "precheck_rejected",统计键是 "precheck"),早期写法是
                    # 直接 life_stats[life_result] 索引 → 亮度预检一拒绝就 KeyError,
                    # 整首歌被判失败(2026-09-16 11:47 实跑:6 首在首音触发后约 30ms
                    # 全部被这条分支终结)。改显式分支,新增状态也不会再炸。
                    if life_result == "hit":
                        life_stats["hit"] += 1
                    elif life_result == "precheck_rejected":
                        life_stats["precheck"] += 1
                    elif life_result == "error":
                        life_stats["error"] += 1
                    if life_result == "hit":
                        logging.info("打歌中生命值耗尽,提前结束本次演出")
                        _log_life_stats()
                        # 崩局取证(2026-10-28):用上面这张已经抓到的帧存画面 +
                        # 读分数,再汇总首音参数与指令进度。只读,不影响收尾。
                        _dump_crash_diag(
                            context, frame,
                            "%s-%s" % (current_song_id, DIFFICULTY),
                            batch_no, current_chart,
                        )
                        # 提前退出的局同样输出服务端执行滞后:生命耗尽往往就是
                        # 锚点/时序问题,这份数据正是定位要用的(正常局才有会
                        # 漏掉全部失败局)。jlog 由 reader 线程持续回传,daemon
                        # 线程自行等静默,不阻塞本线程抛异常走 on_error。
                        _dump_server_lag_async(
                            current_song_name, current_chart.actions,
                            _lag_fb_snapshot(),
                        )
                        raise LifeExhaustedDetected()
                    # 暂停弹窗(2026-10-17 用户实况):不点「中断」这一局永远挂着。
                    # 复用同一个 1s 检测节拍,模板匹配本身很便宜;检测耗时同样从
                    # sleep 预算里扣,不影响下一批音符的时间线。
                    if _pause_dialog_on_screen(context, frame):
                        _log_life_stats()
                        _interrupt_paused_live(context)
                        raise LivePauseInterrupted()
                    sleep_s = max(0.0, sleep_s - (time.perf_counter() - check_t0))
                    last_life_check = time.perf_counter()
            else:
                # 本批切片太密,扣掉检测耗时会把下一批发晚,只能跳过这一轮。
                # 偶发跳过无妨,连续跳过就意味着这段时间的弹窗不会被发现。
                life_stats["starved"] += 1
                if starve_since is None:
                    starve_since = now
                elif now - starve_since >= 3.0:
                    logging.warning(
                        "生命检测已连续 %.1fs 无法执行(切片过密,sleep 预算不足),"
                        "期间若弹出「演出失败」不会被发现",
                        now - starve_since,
                    )
                    starve_since = now

        time.sleep(sleep_s)
        prev_sleep_end = time.perf_counter()

        index = current_chart.actions_to_cmd_index
        upcoming = current_chart.actions[index : index + CMD_SLICE_SIZE]
        if upcoming:
            batch_no += 1
            batch_first_time = upcoming[0]["time"]
            with callback_data_lock:
                # 反馈补偿执行端(2026-10-23 第四版):「补足到池」——把池水位
                # (chart._a2c_offset,含开环补偿待排水额)补到与当前实测残差
                # (lag,绝对对齐口径)一致。排水消费池水时服务端残差同步下降,
                # 下批补足量归 0 → 既自限又追回存量(第二版自限但挂账、第三版
                # 追账但正反馈,各取一半)。机理与四版踩坑史见模块头注释与
                # chart.py __init__ 注释。
                # 测量滞后(发布领先 2~3s)使补足相位落后 1~2 批,误差方向
                # 偏少注(安全侧),量级 ≤2×每批新漂移(~2ms),可接受。
                if _LAG_FEEDBACK_ENABLED:
                    # 两道保护(回执积压停注入 / 单批补足超限丢弃)在函数里判,
                    # 返回 0 就是本批不注入;背景见 lag_feedback_batch_delta 上方。
                    _fb_pool_before = current_chart._a2c_offset
                    _fb_delta = lag_feedback_batch_delta(
                        current_chart, batch_no
                    )
                    if abs(_fb_delta) > 0.01:
                        current_chart._a2c_offset += _fb_delta
                        logging.debug(
                            "滞后反馈: 残差%.1fms 池%.1f→%.1f(批#%d)",
                            _lag_fb["lag"], _fb_pool_before,
                            current_chart._a2c_offset, batch_no,
                        )
                _adjust_offset()
                reset_callback_data()
            current_chart.actions_to_MNTcmd(
                (mnt.max_x, mnt.max_y), current_orientation, OFFSET, CMD_SLICE_SIZE
            )
            # 理想指针补齐(2026-10-29,「越打越早」的根因):见函数注释。
            _fb_skip_applied = lag_feedback_apply_skipped(
                current_chart, _fb_skip_applied
            )
        else:
            break
    # 反馈补偿的曲末汇总(2026-10-28):回执积压峰值是「jlog 到底丢没丢」的
    # 直接证据 —— 正常只领先 1~2 批(≤200 条),峰值贴着上限或者被保护①
    # 停过注入,就是这一局下发链出问题的实锤。阈值(400)要靠这些数校准。
    if _LAG_FEEDBACK_ENABLED:
        # 2026-10-28 加后两个数:wait 配对成功条数 / 队列空兜底条数。
        # 兜底条数>0 就说明本曲 lag 有一部分是「拿实际值当理想值」算出来的,
        # dump 与在线分叉时先看这个数。
        # 2026-10-29 末尾两项:被跳过的等待(含其中补偿抽干条数)与
        # 「已补进理想指针」的量。两者应相等;不等说明补齐链路有漏批。
        logging.info(
            "滞后反馈汇总: 回执积压峰值 %d 条(上限 %d), 单批超限丢弃 %d 批;"
            " wait 回执 %d 条, 队列空兜底 %d 条; 等待被跳过 %.1fms(告警线 %.0f,"
            "其中补偿抽干 %d 条), 已补进理想指针 %.1fms%s",
            _lag_fb["backlog_peak"], _LAG_FB_MAX_BACKLOG,
            _lag_fb["overshoot_n"], _lag_fb["wait_recv"],
            _lag_fb["fallback_n"],
            getattr(current_chart, "skipped_wait_ms", 0.0),
            _LAG_FB_SKIP_WARN_MS,
            getattr(current_chart, "drained_waits", 0),
            _lag_fb["skip_applied"],
            ", 本曲已停注入" if _lag_fb["off"] else "",
        )
    # 同指同帧修正的指纹(2026-10-29):动作数不变、命令串里多出这么多 'c'。
    # #489/#649-special 应各 2 处(滑条头/尾),其余曲目按谱面结构多少不等。
    if getattr(current_chart, "detached_moves", 0):
        logging.info(
            "判定点独占帧: 本曲为 %d 处「同指 down/up 后紧跟同刻 move」各补了一条 commit",
            current_chart.detached_moves,
        )
    _log_life_stats()
    _dump_server_lag_async(
        current_song_name, current_chart.actions, _lag_fb_snapshot()
    )
    time.sleep(2)


# ---- 首音候选可信度门(2026-10-27,用户反馈「刚开打歌对不上导致 miss」)----
# 背景:23:24 场第 1 首 #3-special ぽっぴん’しゃっふる,冻结期底噪长期 3.7~4.4,
# 首音候选以 change=3.42(仅高于阈值 1.4)越阈值就被锚定 → 实际晚了数百毫秒
# (触发帧截图 debug/firstnote/232424-trigger.jpg 上已显示 MISS、最近的音符
# 还在检测带上方),整曲错位,开打 7 秒生命耗尽。而失败局没有结算 FAST/SLOW,
# GUI 的 photogate 自动校准既拿不到信号、也不会调(量级也差两个数量级)。
# 正常曲目对照(同场第 2 首 ONENESS 全 PERFECT):触发 change=9.68,
# band 与顶部 7 行越阈值的时差 -4ms(实测正常范围 -8~+1ms)。
# 所以把「这不是音符、是背景光效在缓慢变化」做成两道可量化的判据:
#   ① 触发值必须显著高于冻结期底噪,否则只是噪声抖了一下;
#   ② band 与顶部灵敏判据必须是同一物体(越阈值时刻相近),否则是两段无关变化。
# 命中任一条就**不锚定**,重置基线继续等真首音。三条放行条件防止过度拦截:
# 不同候选累计拒绝 MAX_TRIGGER_REJECTS 次、累计拒绝超过 REJECT_GIVEUP_S 秒、
# 或顶部判据这次没记录到越阈值(没有可对照证据,安全侧放行)。
TRIGGER_NOISE_RATIO = 1.5       # 触发值 ≥ 底噪 × 1.5 才可信
TRIGGER_EDGE_WINDOW_MS = 150.0  # band 与顶部判据越阈值的最大允许时差
MAX_TRIGGER_REJECTS = 3         # 不同候选累计拒绝上限
REJECT_GIVEUP_S = 8.0           # 累计拒绝时长上限(秒),到点放宽


def _first_note_trigger_check(
    change_score,
    noise_floor,
    band_t,
    edge_last_t,
    rejects,
    rejected_s,
    threshold=3.0,
):
    """首音候选是否可信 → (ok, reason);reason 为空串表示通过。

    纯函数,离线回归见 _test_firstnote.py。判据与放行条件见上方常量注释。
    noise_floor=0 表示没有可信底噪(冻结强制超时的曲目),此时不启用判据①。
    """
    if rejects >= MAX_TRIGGER_REJECTS or rejected_s >= REJECT_GIVEUP_S:
        return True, ""
    if noise_floor > 0:
        floor = max(threshold, noise_floor * TRIGGER_NOISE_RATIO)
        if change_score < floor:
            return False, "低于底噪(change=%.2f < %.2f, 底噪=%.2f)" % (
                change_score, floor, noise_floor)
    if edge_last_t is not None:
        lead_ms = (band_t - edge_last_t) * 1000.0
        if lead_ms > TRIGGER_EDGE_WINDOW_MS:
            return False, "顶部判据早 %.0fms(不是同一个物体)" % lead_ms
        if lead_ms < -TRIGGER_EDGE_WINDOW_MS:
            return False, "顶部判据晚 %.0fms(不是同一个物体)" % (-lead_ms)
    return True, ""


# ---- L2:首音锚点在线实测(2026-10-27,先取证后启用)----
# 现状:锚点 = 「检测带越阈值时刻 + PHOTOGATE_LATENCY」。检测带到判定线那 55px
# 的传播时间被写死成一个数,但实测证据(2026-10-27 晚)否定了这个前提:
#   · 同一晚 photogate 被自动校准推过 40/41/47/50/55/59/60/67/70/72/85/86ms;
#   · 同一首 #179 檄!帝国華撃団 在 gate=50/59/67 下 fast=170/151/90、miss=29/22/4,
#     而同 gate=50 的 #12 走り始めたばかり 全 0 —— 一个常量没法同时适配两首曲;
#   · 首曲冷启动、失败局(无结算 FAST/SLOW)自动校准都够不着。
# 实测法:锚定后跟 1~2 帧,用「带内相对静默背景的亮部重心」位移算下落速度,
# 外推到判定线 y=590;测不出速度、或结果与常量估计差太多就回退常量。
# **默认关闭**:先靠 _dump_firstnote_trace 落盘的曲线核对口径(本轮),数据支持后
# 再把 PHOTOGATE_MEASURE_ENABLED 打开或写进 data/config.yml 的 timing.measure_anchor。
PHOTOGATE_MEASURE_ENABLED = False
MEASURE_MAX_FRAMES = 2        # 触发后最多额外抓几帧(1 帧 ≈ 6~10ms)
MEASURE_V_MIN = 0.15          # px/ms,低于当静止/未动
MEASURE_V_MAX = 4.0           # px/ms,高于当误检
MEASURE_MAX_DIFF_MS = 45.0    # 与常量估计的最大允许差异,超出视为实测不可信


def _weighted_centroid(weights):
    """按行权重算重心(返回行偏移);权重全 0 返回 -1。纯函数,便于离线回归。"""
    total = float(weights.sum())
    if total <= 1e-6:
        return -1.0
    return float((weights * np.arange(len(weights))).sum() / total)


# 首音锚点逐帧取证(2026-10-30,纯诊断,默认关闭):
# #462-hard 每次崩,但谱面数据已证无误(游戏 BMS 与 Bestdori 540/540 逐条
# 吻合),崩局画面分数 4718;触发帧里判定线附近是一条滑条(lane≈5.4),而谱面
# 前几个音符是 lane 2/5/1/4 的单键 —— 锚到的不是首音。
# 要分辨「前几个单键太淡、越不过 change 阈值(锚点被推到 5s 后的滑条)」还是
# 「抓帧停顿/画面未激活(锚点被推迟)」,只能看触发前那几秒逐帧的 change。
# 设 `AUTODORI_DUMP_FIRSTNOTE=1` 开启:每帧记 (ms, change, band 均值, 轨数),
# 每 300ms 存一张图,触发或异常时写 `debug/firstnote/<HHMMSS>-scan.txt`。
# 设 `=2` 只记数字、不存图 —— 怀疑「抓帧停顿 / IPC 返回旧帧」时用,额外 IPC 为 0
# (数字全部来自脚本本来就抓的那一帧)。关闭时只有一次 if 判断,零开销。
FIRSTNOTE_TRACE_ENABLED = os.environ.get("AUTODORI_DUMP_FIRSTNOTE") in ("1", "2")
FIRSTNOTE_TRACE_SHOTS = os.environ.get("AUTODORI_DUMP_FIRSTNOTE") != "2"


def wait_first_note():
    t_start = time.perf_counter()
    if FIRSTNOTE_TRACE_ENABLED:
        logging.info(
            "首音逐帧取证已开启(AUTODORI_DUMP_FIRSTNOTE=%s):%s",
            "1" if FIRSTNOTE_TRACE_SHOTS else "2",
            "每帧记数 + 每 300ms 存图" if FIRSTNOTE_TRACE_SHOTS else "只记数字,不存图",
        )
    last_avg = None
    waited_frames = 0
    _runtime = get_runtime_info(current_player.resolution)
    info = _runtime["wait_first"]
    _lane_info = _runtime["lane"]
    from_row, to_row = info["from"], info["to"]
    freezed = False
    row_count = to_row - from_row + 1
    edge_count = min(int(info.get("edge", 4)), row_count)
    # 诊断用信号:检测带顶部 edge_count 行的平均色。它由已经算好的 rows 直接
    # 派生,零额外采集成本;目前只记录「本来会何时触发」,不参与判定。
    last_edge_avg = None
    edge_cross_t = None
    # 冻结前的静默判定被重置的次数。冻结耗时本身只看最后一段 200 帧,看不出
    # 中间被打断过多少次;重置多说明前奏里有运动,冻结完成得比账面晚。
    freeze_resets = 0

    # Sub-frame sync: keep the original reference point (the consecutive-frame
    # band-average change crossing 3.0) but interpolate the exact crossing moment
    # between the last two frames, instead of being quantized to a whole capture
    # frame (~16-33ms).
    prev_change = None   # consecutive-frame change of the previous frame
    prev_frame_t = None
    CHANGE_THRESHOLD = 3.0
    _freeze_t0 = None    # 冻结期开始时刻(用于统计采集帧率)

    # 问题A修复(2026-08-28 日志实证):崩掉的歌,冻结完成后 50-100ms 内就有
    # 前奏残留元素(计数动画/"GO!")扫过检测带被当首音 → 图表比真首音早 ~1.5s
    # 开始 → 整首错位全 MISS → 崩。这里在冻结完成后给一段宽限期,期内越阈值
    # 一律视为前奏残留忽略;真首音通常要 1.5s+ 才到,不受影响。
    FIRST_NOTE_GRACE_MS = 500
    _freeze_done_t = None

    # 场景切换误触发修复(2026-10-21,连打第二首首音不对的根因):
    # 冻结完成后、真首音到来前,演出画面才「激活」(轨道亮起/READY 等)或转场,
    # 检测带出现一次整带跳变 —— 被当成首音 → 时间线整体早 ~2-3s → 开局全 miss
    # → 生命耗尽(实测 #462 两连崩,触发前带内静默 2.1s 后单帧 0→9.12;
    # 谱面首音 2727ms,锚点早了约 2.7s)。区分依据:音符只占 1 条轨道宽,
    # 场景变化会同时改动几乎所有轨道 —— 按轨道分组算变化,≥5/7 轨同时变的
    # 越阈值一律视为场景切换,重置基线继续等真音符。
    SCENE_MIN_LANES = 5    # 同时变化达到该轨道数 → 判为场景切换
    LANE_CHANGE_MIN = 1.0  # 单轨变化量达到该值计入"变化轨道"
    last_lane_avg = None   # 上一帧各轨道(检测带内)平均色,与 last_avg 同步维护

    # 首音候选可信度门的状态(判据见 _first_note_trigger_check 上方注释)。
    noise_samples = deque(maxlen=200)  # 冻结期各帧 change,用于估静默底噪
    noise_floor = 0.0                  # 冻结完成时的底噪;0 = 不启用判据①
    edge_last_t = None                 # 最近一次「顶部 7 行越阈值」的时刻
    prev_edge_change = None            # 上一帧顶部判据的帧间变化(判「越阈值那一帧」)
    last_rows = None                   # 上一帧逐行颜色,算「带内差分重心」取证用
    trigger_rejects = 0                # 不同候选被拒次数(间隔 >300ms 才算新候选)
    last_reject_t = None
    reject_since = None                # 首次拒绝时刻,算累计拒绝时长
    # 逐帧取证(仅 FIRSTNOTE_TRACE_ENABLED,见常量注释):(ms, change, band, 轨数)
    frame_log = []
    _last_trace_shot = 0.0

    # L2 取证/实测状态(见 PHOTOGATE_MEASURE_ENABLED 上方注释)。
    band_baseline = None               # 冻结完成时的逐行颜色 = 静默背景
    cent_history = deque(maxlen=12)    # 触发前若干帧的 (t_ms, 带内音符绝对 y)
    judge_y = _lane_info["h"]          # 判定线 y(命中区中心),590 @720p

    def _lane_avgs(scr):
        """检测带各轨道的平均色,shape=(7,3)。轨道 x 范围来自 util 的 lane 配置。"""
        x0, w = _lane_info["start_x"], _lane_info["w"]
        return np.array(
            [
                scr[from_row : to_row + 1, x0 + i * w : x0 + (i + 1) * w]
                .mean(axis=(0, 1))
                for i in range(7)
            ]
        )


    # 注意:曾尝试"越阈值后持续变化 CONFIRM_MS 毫秒确认再触发"(问题A 防误触发),
    # 但真音符进入检测带只产生一帧大变化(音符进入),之后带内下移帧间变化 <3.0,
    # 会被确认逻辑当成"候选掉落"丢弃 → 首音被拖后数秒 → 整首全 MISS。已撤回,
    # 恢复"越阈值即触发"。问题A 复现靠下面的每帧 wfT 日志定位。
    _log_t = 0.0
    _snap_start_done = False  # 起始帧截图只拍一次

    def _band_centroid():
        """触发帧「带内逐行差分」的亮部重心(0=检测带顶, row_count-1=带底)。

        纯取证,不参与判定:真音符是一小段亮块,差分重心应落在带内、随帧下移;
        背景光效/立绘动画的差分是整带弥散的。先在日志里攒几场数据,再决定要不要
        拿它做「锚点在线实测」(L2)—— 直接改锚点算法前必须先离线验证重心能否
        稳定提取(见 _diag_firstnote.py)。
        """
        if last_rows is None:
            return -1.0
        dy = np.abs(rows - last_rows).sum(axis=1)
        total = float(dy.sum())
        if total <= 1e-6:
            return -1.0
        return float((dy * np.arange(row_count)).sum() / total)

    def _rows_of(img):
        """检测带逐行 RGB 均值,shape=(row_count,3)。主循环与实测抓帧共用。"""
        arr = np.empty((row_count, 3), dtype=np.float64)
        for r in range(from_row, to_row + 1):
            avg, _ = evaluate_row_color(img, r)
            arr[r - from_row] = avg
        return arr

    def _band_y_from_rows(rows_):
        """当前帧带内音符的绝对 y;无有效差异返回 -1。

        用「相对静默基线」而不是帧间差分:差分在音符移动时会出现「新位置亮 +
        旧位置恢复」两块,重心落在中间、速度被系统性低估;基线法直接给音符当前
        所在行。背景有轻微动画时靠 -1.0 的门限把底噪压掉。
        """
        if band_baseline is None:
            return -1.0
        w = np.abs(rows_ - band_baseline).sum(axis=1)
        c = _weighted_centroid(np.clip(w - 1.0, 0, None))
        return -1.0 if c < 0 else from_row + c

    def _measure_anchor(y0, t0):
        """触发后实测下落速度,预测到判定线还需多久 → (wait_ms, 说明)。

        测不出返回 (None, 原因)。只多抓 1~2 帧(每帧 6~10ms),时间从原本要睡的
        photogate 里扣,不额外推后时间线。
        """
        y_prev, t_prev = y0, t0
        seen = 0
        for _ in range(MEASURE_MAX_FRAMES):
            img = current_player.ipc_capture_display()
            t = time.perf_counter()
            if img is None:
                break
            y = _band_y_from_rows(_rows_of(img))
            if y < 0:
                continue
            dt_ms = (t - t_prev) * 1000.0
            if dt_ms <= 0.5:
                continue
            v = (y - y_prev) / dt_ms
            seen += 1
            if MEASURE_V_MIN <= v <= MEASURE_V_MAX:
                return (judge_y - y) / v, "y=%.1f v=%.2fpx/ms" % (y, v)
            y_prev, t_prev = y, t
        return None, "速度不可信(样本%d)" % seen

    def _dump_trace(cross_t, change_score, kind, y0):
        """把触发前后若干帧的「带内音符 y」落盘,供 L2 启用前核对口径。

        纯日志:daemon 线程异步写,不参与判定、不占打歌时序。原来靠外部脚本
        连采 IPC 取证,会和 bot 抢通道(2026-10-27 实测把滞后统计的失配从
        5/22020 拉到 3763/11472),改成 bot 自己落盘。
        """
        # 主线程先拷一份快照:deque 在落盘线程里迭代时若被 while 循环继续 append,
        # 会抛 "deque mutated during iteration",文件就空了。
        history = list(cent_history)

        def _worker():
            try:
                os.makedirs("debug/firstnote", exist_ok=True)
                path = "debug/firstnote/%s-%s-%s-anchor.txt" % (
                    time.strftime("%H%M%S"), current_song_id, DIFFICULTY)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        "# %s (#%s-%s) photogate=%dms 检测带 y=%d-%d 判定线 y=%d\n"
                        % (current_song_name, current_song_id, DIFFICULTY,
                           PHOTOGATE_LATENCY, from_row, to_row, judge_y)
                    )
                    f.write(
                        "# 触发[%s] band %.0fms change=%.2f 底噪=%.2f 拒绝%d次 触发帧 y=%s\n"
                        % (kind, (cross_t - t_start) * 1000.0, change_score,
                           noise_floor, trigger_rejects,
                           "-" if y0 is None or y0 < 0 else "%.1f" % y0)
                    )
                    f.write("# t_ms\ty\n")
                    for t_ms, y in history:
                        f.write("%.0f\t%.1f\n" % (t_ms, y))
                logging.info("首音曲线已落盘 %s", path)
            except Exception as e:  # noqa: BLE001 —— 落盘失败绝不影响打歌
                logging.debug("首音曲线落盘失败(%s)", e)

        threading.Thread(
            target=_worker, daemon=True, name="firstnote-trace"
        ).start()

    def _resolve_anchor_wait(cross_t, wait_ms, y0, change_score, kind):
        """落盘曲线,并按需实测锚点;返回最终应睡毫秒。

        未启用实测(默认)时原样返回常量结果,行为与改动前完全一致。
        """
        _dump_trace(cross_t, change_score, kind, y0)
        # y0 < 0 = 触发帧带内没有可辨识的音符位置(音符可能已在带下方/背景遮挡),
        # 没有起点就没法测速,直接用常量。
        if not PHOTOGATE_MEASURE_ENABLED or y0 is None or y0 < 0:
            return wait_ms
        remain_ms = wait_ms - (time.perf_counter() - cross_t) * 1000.0
        m_wait, desc = _measure_anchor(y0, time.perf_counter())
        if m_wait is None:
            logging.info("首音锚点实测[%s]: 跳过(%s),用常量 %.0fms",
                         kind, desc, max(0.0, remain_ms))
            return max(0.0, remain_ms)
        if abs(m_wait - remain_ms) > MEASURE_MAX_DIFF_MS:
            logging.info(
                "首音锚点实测[%s]: %s → %.0fms,与常量 %.0fms 差 %.0fms 超限,回退常量",
                kind, desc, m_wait, remain_ms, abs(m_wait - remain_ms),
            )
            return max(0.0, remain_ms)
        logging.info("首音锚点实测[%s]: %s → %.0fms(常量 %.0fms,差 %.0fms)",
                     kind, desc, m_wait, remain_ms, m_wait - remain_ms)
        return max(0.0, m_wait)

    def _dump_frame_trace():
        """把逐帧 trace 落盘(只在 FIRSTNOTE_TRACE_ENABLED 时有内容)。"""
        if not FIRSTNOTE_TRACE_ENABLED or not frame_log:
            return
        try:
            os.makedirs("debug/firstnote", exist_ok=True)
            name = "debug/firstnote/%s-scan.txt" % time.strftime("%H%M%S")
            with open(name, "w", encoding="utf-8") as f:
                f.write(
                    "# %s (%s) 逐帧 trace:t_ms change band=(r,g,b) 轨数 "
                    "各轨变化量(7 个,≥1.0 计入轨数)\n"
                    % (current_song_name, "%s-%s" % (current_song_id, DIFFICULTY)))
                for ms, change, band, lanes, ldiff in frame_log:
                    f.write("%.1f\t%.3f\t(%.1f,%.1f,%.1f)\t%d\t%s\n" % (
                        ms, change, band[0], band[1], band[2], lanes,
                        " ".join("%.2f" % v for v in ldiff),
                    ))
            logging.info("首音逐帧 trace 已落盘 %s(%d 帧)", name, len(frame_log))
        except Exception as e:  # noqa: BLE001 —— 诊断不能影响打歌
            logging.debug("首音逐帧 trace 落盘失败: %s", e)

    def _trace(change, band, lanes, lane_diff=None):
        """记录一帧;开启时每 300ms 另存一张图(异步,不占打歌时序)。

        lane_diff 是 7 条轨道各自的帧间变化量 —— #462 的假触发到底是「音符」
        还是背景上的青色碎片,判据就是它落在哪几轨、是否与谱面首音同轨。
        """
        nonlocal _last_trace_shot
        if not FIRSTNOTE_TRACE_ENABLED:
            return
        now = time.perf_counter()
        ms = (now - t_start) * 1000.0
        frame_log.append((
            ms, float(change), tuple(float(v) for v in band), lanes,
            tuple(float(v) for v in lane_diff) if lane_diff is not None else (),
        ))
        if FIRSTNOTE_TRACE_SHOTS and now - _last_trace_shot >= 0.3:
            _last_trace_shot = now
            _snap("scan%06d" % int(ms))

    def _log_trigger(cross_t, change_score, kind, lanes=0, lane_hits=()):
        """记录首音触发点,并把「检测带顶部若干行」这个更灵敏的判据本来会在
        什么时候触发一并记下。两者之差就是现有判据的滞后量 —— 「前几个音符
        miss」若出在首音判定上,这条日志能直接量化,不必先改触发逻辑去赌。
        lanes = 触发帧里发生变化的轨道数(1-2 轨 = 音符形态,可作诊断)。
        底噪/带内重心是 2026-10-27 加的可信度门取证字段(见 _first_note_trigger_check)。"""
        _dump_frame_trace()
        band_ms = (cross_t - t_start) * 1000.0
        if edge_cross_t is None:
            edge_desc, lead = "未越阈值", ""
        else:
            edge_ms = (edge_cross_t - t_start) * 1000.0
            edge_desc = "%.0fms" % edge_ms
            lead = "(灵敏判据早 %.0fms)" % (band_ms - edge_ms)
        logging.info(
            "首音触发[%s]: band %.0fms change=%.2f, %d/7 轨变化, 顶部%d行 %s %s, "
            "底噪=%.2f 带内重心=%.1f/%.0f 拒绝%d次",
            kind, band_ms, change_score, lanes, edge_count, edge_desc, lead,
            noise_floor, _band_centroid(), row_count - 1, trigger_rejects,
        )
        # 2026-10-28 崩局诊断:把触发参数留给 _dump_crash_diag —— 崩局时
        # 「首音是怎么锚的」和「画面分数」放在一起才能判断锚点偏早还是偏晚。
        _FIRST_NOTE_DIAG.update({
            "kind": kind, "band_ms": band_ms, "lanes": lanes,
            # 2026-10-30:触发落在哪几条轨(0-6,屏幕分轨索引)。同名不同谱面
            # (#389/#462 シル・ヴ・プレジデント)靠它挑对 id,见 _maybe_switch_chart_by_first_note。
            "lane_hits": tuple(int(v) for v in lane_hits),
            "change": change_score, "noise": noise_floor,
            "y": -1.0 if note_y is None else float(note_y),
            "gate": PHOTOGATE_LATENCY, "t": cross_t, "post_saved": False,
        })

    def _reject_trigger(reason, change_score, frame_t, kind):
        """拒绝本次首音候选:计数(同一段持续变化只算一次)并记取证日志。

        调用方负责重置基线后 continue;这里只管计数与日志。间隔 >300ms 才算
        新候选,否则持续噪声会在几百毫秒内把拒绝配额烧光。
        """
        nonlocal trigger_rejects, last_reject_t, reject_since
        if last_reject_t is None or (frame_t - last_reject_t) > 0.3:
            trigger_rejects += 1
        last_reject_t = frame_t
        if reject_since is None:
            reject_since = frame_t
        edge_desc = (
            "未记录" if edge_last_t is None
            else "%.0fms" % ((edge_last_t - t_start) * 1000.0)
        )
        logging.warning(
            "首音候选被拒[%s · %s]: band %.0fms change=%.2f 底噪=%.2f "
            "顶部判据 %s 带内重心=%.1f/%.0f,第 %d 次拒绝(累计 %.1fs),重置基线继续等",
            kind, reason, (frame_t - t_start) * 1000.0, change_score,
            noise_floor, edge_desc, _band_centroid(), row_count - 1,
            trigger_rejects, frame_t - reject_since,
        )

    def _gate_reject(change_score, frame_t, kind):
        """执行可信度门;不通过就拒绝并把基线重置到本帧,返回 True(调用方 continue)。

        与场景切换分支同一套重置方式(基线推进到本帧、prev_change 记本帧变化),
        否则下一帧会因为 prev_change 仍为旧值而重复触发同一个候选。
        """
        nonlocal last_avg, last_edge_avg, last_lane_avg, last_rows
        nonlocal prev_change, prev_frame_t
        ok, reason = _first_note_trigger_check(
            change_score,
            noise_floor,
            frame_t,
            edge_last_t,
            trigger_rejects,
            (frame_t - reject_since) if reject_since else 0.0,
        )
        if ok:
            return False
        _reject_trigger(reason, change_score, frame_t, kind)
        last_avg = band_avg
        last_edge_avg = edge_avg
        last_lane_avg = lane_avg
        last_rows = rows
        prev_change = change_score
        prev_frame_t = frame_t
        return True

    def _snap(tag):
        """关键瞬间抓一帧存 debug/firstnote/,人工核对首音锚点用(纯诊断:
        daemon 线程异步抓,不参与判定、不占打歌时序)。#462 这类「触发签名
        和真音符一模一样却整首崩」的局面,只有看截图才能分辨锚点偏早还是
        偏晚 —— 截图里判定线上有没有音符、轨道上音符走到哪,一眼可判。"""
        def _worker():
            try:
                frame = current_player.ipc_capture_display()
                if frame is None:
                    return
                os.makedirs("debug/firstnote", exist_ok=True)
                name = "debug/firstnote/%s-%s.jpg" % (
                    time.strftime("%H%M%S"), tag)
                # IPC 帧是 RGB,cv2 要 BGR
                cv2.imwrite(name, frame[:, :, :3][:, :, ::-1])
                logging.debug("首音诊断截图: %s", name)
            except Exception as e:  # noqa: BLE001 —— 诊断截图绝不能影响打歌
                logging.debug("首音诊断截图失败(%s): %s", tag, e)
        threading.Thread(target=_worker, daemon=True, name="firstnote-snap").start()

    def _maybe_log(change_score, band_avg, note=""):
        """每帧 change_score 日志(问题A 复现用):静默期 ≤10 行/秒,变化显著或
        有事件时逐帧记录。复现问题后看 debug/autodori-*.log 里 wfF/wfT 行,
        change 越大说明检测带里变化越剧烈,结合 band 颜色可判断是不是真音符。"""
        nonlocal _log_t
        now_t = time.perf_counter()
        if not note and change_score <= 1.0 and (now_t - _log_t) < 0.1:
            return
        _log_t = now_t
        logging.debug(
            "wf{}{} change={:.2f} band=({:.1f},{:.1f},{:.1f}) waited={}".format(
                "T" if freezed else "F",
                (" " + note) if note else "",
                change_score,
                band_avg[0],
                band_avg[1],
                band_avg[2],
                waited_frames,
            )
        )

    #冻结阶段的总超时上限(见下方 FREEZE_TIMEOUT_S)。冻结成功的标志是
    # 「连续 200 帧 change<=3.0」,但如果检测带里始终有东西在动(立绘呼吸、
    # 背景光效、滚动字幕),静默判定就会被反复重置 —— 实测同一首#786 冻结耗时
    # 在 1.3s ~ 108.8s 之间剧烈波动(静默重置 0 ~ 5536 次),而能打成的
    # #487/#486 稳定在 1.2s / 重置 0 次。**首音基准点由冻结完成时刻决定**,
    # 等太久会让整首系统性偏后 → 全 MISS。所以必须给冻结封顶。
    #
    # 上限取值:正常曲子冻结约 1.2s;谱面首音本身在 2~5s。
    # 取 12s 既容得下前奏偏长的曲子,又不会等到歌都快结束了还在等。
    FREEZE_TIMEOUT_S = 12.0

    # 首音阶段总超时(2026-10-26 #400 CATASTROPHE BANQUET 事故):冻结封顶后
    # 本循环再无任何退出条件 —— 那首 MV 大立绘曲的全轨背景变化让每个首音候选
    # 都被 SCENE_MIN_LANES(>=5/7 轨)判成"场景切换"吞掉,一个音没打、生命耗尽
    # 弹「演出失败」,而 Play 卡在本循环里永不返回,MAA 任务线程被占死,看门狗
    # 按返回键(弹窗不响应 BACK)和 post_stop(停不掉运行中的 CustomAction)都
    # 救不了,整局僵死 8 分钟直到手动重启。取 240s:实测生命耗尽弹窗在开局后
    # 约 2.5~3 分钟出现,超时放弃时弹窗必已在屏上,playsong.on_error 的
    # life_exhausted_exit(OCR 点「退出」)能直接接管回主页继续下一首;正常曲
    # 进 live 到首音触发只要 4~8s,余量 30 倍。
    FIRST_NOTE_TIMEOUT_S = 240.0

    # 抓屏失败计数(对应下方 except)。
    # 原来这里只有一行 `logging.error` —— 抓屏**持续**失败时(模拟器 IPC 断连、
    # 游戏崩了、分辨率被改、MuMu 被切后台)整个 while 就成了无退避的死循环:
    # 实测约 53 万次/秒,play_song() 永不返回,只能等 playsong 节点的
    # timeout:50000 把整首判失败,日志里只剩一堆 "Failed to get screen"。
    # 改成:每次失败退避 50ms,连续 CAPTURE_FAIL_MAX 次仍失败就抛出,走 on_error。
    CAPTURE_FAIL_MAX = 20
    capture_failures = 0

    while True:
        # 看门狗协作式解救(见 _STUCK_EVENT 注释):等待循环每帧看一眼,
        # 看门狗判卡死就立刻放弃走 on_error,不用干等下面的 240s 总超时。
        # is_set() 是纯布尔读(几十 ns),且只在等待阶段执行 —— 打歌发布的
        # 热路径(逐批下发音符命令)一行不碰,对时序零影响。
        # 必须放在 try **外**:循环体整体包在 try 里,底部 except 会把一切
        # 异常当"抓屏失败"计数,raise 在里面只会被吞掉转圈。
        # 抛 RuntimeError → Play 的通用 except 捕获 → RunResult(False) →
        # playsong.on_error(save_failed_playresult → life_exhausted_exit)收尾。
        if _STUCK_EVENT.is_set():
            logging.warning("看门狗判定卡死,放弃本局等待首音,走 on_error 收尾")
            raise RuntimeError("看门狗判定卡死,放弃本局")
        if freezed and (
            time.perf_counter() - t_start
        ) >= FIRST_NOTE_TIMEOUT_S:
            logging.warning(
                "首音 %.0fs 仍未触发,放弃本局走 on_error(疑似 MV/场景变化"
                "把首音判据全吞了,见 SCENE_MIN_LANES 注释)",
                FIRST_NOTE_TIMEOUT_S,
            )
            raise RuntimeError(
                "等待首音 %ds 未触发,放弃本局" % FIRST_NOTE_TIMEOUT_S
            )
        try:
            screen = current_player.ipc_capture_display()
            capture_failures = 0
            frame_t = time.perf_counter()
            # 冻结阶段超时判定(只在冻结未完成时有意义)。
            if not freezed and (frame_t - t_start) * 1000.0 >= FREEZE_TIMEOUT_S * 1000.0:
                freezed = True
                _freeze_done_t = frame_t
                _log_t = 0.0
                logging.warning(
                    "冻结阶段超过 %.0fs 仍未静默(已重置 %d 次),强制进入首音检测;"
                    "检测带在此期间始终在变化,首音基准可能偏后 —— "
                    "若整首系统性偏后,先查这一条",
                    FREEZE_TIMEOUT_S,
                    freeze_resets,
                )
                last_avg = band_avg = None
                last_edge_avg = None
                last_lane_avg = None
                last_rows = None
                prev_change = None
                prev_frame_t = None
                # 强制超时说明检测带一直在动,此时算出来的「底噪」是运动水平,
                # 拿它当判据①会把真首音也拦掉 —— 干脆不启用(只留判据②)。
                noise_floor = 0.0
                # 没有静默基线,L2 的「带内音符位置」算不出来,一并不启用。
                band_baseline = None
                _maybe_log(0.0, (0.0, 0.0, 0.0), "force-timeout")
                continue
            rows = _rows_of(screen)
            if not _snap_start_done:
                _snap_start_done = True
                _snap("start")
            band_avg = rows.mean(axis=0)
            edge_avg = rows[:edge_count].mean(axis=0)
            lane_avg = _lane_avgs(screen)
            lane_diff = (
                np.abs(lane_avg - last_lane_avg).sum(axis=1)
                if last_lane_avg is not None
                else np.zeros(7)
            )
            lanes_changed = int((lane_diff >= LANE_CHANGE_MIN).sum())
            edge_change = (
                float(np.sum(np.abs(edge_avg - last_edge_avg)))
                if last_edge_avg is not None
                else 0.0
            )

            if not freezed:
                change_score = (
                    float(np.sum(np.abs(band_avg - last_avg)))
                    if last_avg is not None
                    else 0.0
                )
                _trace(change_score, band_avg, lanes_changed, lane_diff)
                if last_avg is not None:
                    # 底噪采样:冻结期每帧的 change。冻结完成时最后 200 帧必然
                    # 都 ≤ 阈值(deque 只留这么长),取中位数就是这台机器/这个
                    # 画面在这首歌开始时的静默水平 —— 触发值要显著高于它才算
                    # 真音符(判据①,见 _first_note_trigger_check)。
                    noise_samples.append(change_score)
                    if change_score <= CHANGE_THRESHOLD:
                        if waited_frames == 0:
                            _freeze_t0 = frame_t
                        waited_frames += 1
                    else:
                        waited_frames = 0
                        freeze_resets += 1
                    if waited_frames >= 200:
                        freezed = True
                        _freeze_done_t = frame_t
                        _log_t = 0.0  # 进入触发期,重置节流以便逐帧记录
                        noise_floor = (
                            float(np.median(noise_samples)) if noise_samples else 0.0
                        )
                        # 静默背景存一份:L2 用「当前帧相对它」定位音符所在行,
                        # 比帧间差分准(差分在移动时重心落在新旧位置中间)。
                        band_baseline = rows.copy()
                        fps = (
                            200.0 / max((frame_t - _freeze_t0) * 1000.0, 1e-6) * 1000.0
                        )
                        logging.info(
                            "冻结完成: 等待首音总耗时 %.0fms(静默判定被重置 %d 次), "
                            "最后 200 帧耗 %.0fms(约 %.0f fps), 检测带 y=%d-%d(顶部 %d 行作灵敏判据), "
                            "底噪=%.2f",
                            (frame_t - t_start) * 1000.0,
                            freeze_resets,
                            (frame_t - _freeze_t0) * 1000.0,
                            fps,
                            from_row,
                            to_row,
                            edge_count,
                            noise_floor,
                        )
                last_avg = band_avg
                last_edge_avg = edge_avg
                last_lane_avg = lane_avg
                last_rows = rows
                _maybe_log(change_score, band_avg)
                continue

            # freezed: detect the first note moving into the band, interpolated.
            # (曾尝试"持续变化确认"防误触发,但真音符进入检测带只产生一帧大变化,
            # 之后带内下移帧间变化 <3.0 会被误判掉落 → 首音拖后 → 整首全 MISS,
            # 已撤回。若问题A 仍复现,靠 wfT 日志定位误触发来源。)
            change_score = (
                float(np.sum(np.abs(band_avg - last_avg)))
                if last_avg is not None
                else 0.0
            )
            _trace(change_score, band_avg, lanes_changed, lane_diff)
            # 带内音符位置(相对静默背景):触发前攒一小段历史,触发时落盘供
            # L2 核对口径;只在 freezed 阶段算,冻结期不做无用功。
            note_y = _band_y_from_rows(rows)
            if note_y >= 0:
                cent_history.append(((frame_t - t_start) * 1000.0, note_y))
            # 灵敏判据的越阈值时刻:宽限期内的也照记 —— 真首音若正好落在宽限
            # 窗口里会被整段忽略,这条能看出它当时到底有没有越阈值。
            if (
                edge_cross_t is None
                and last_edge_avg is not None
                and edge_change >= CHANGE_THRESHOLD
            ):
                edge_cross_t = frame_t
            # 门控用的「最近一次越阈值」:只认「从阈值下穿到阈值上」那一帧。
            # 不能用「当前是否高于阈值」——持续高于阈值的背景噪声每帧都算命中,
            # 时差恒为 0,判据②就失效了。也不与 edge_cross_t 合并:后者是首次
            # 记录,专门用于诊断「顶部判据本来会何时触发」,语义不同。
            if (
                last_edge_avg is not None
                and prev_edge_change is not None
                and prev_edge_change < CHANGE_THRESHOLD <= edge_change
            ):
                edge_last_t = frame_t
            prev_edge_change = edge_change
            now_t = time.perf_counter()
            if (
                _freeze_done_t is not None
                and (now_t - _freeze_done_t) * 1000.0 < FIRST_NOTE_GRACE_MS
            ):
                # 冻结后宽限期内:前奏残留 UI(计数/"GO!"等)常在此窗口扫过检测带,
                # 一律忽略不触发,等真首音(通常 1.5s+ 后)
                if change_score >= CHANGE_THRESHOLD:
                    _maybe_log(
                        change_score,
                        band_avg,
                        "ignored(prelude {:.0f}ms)".format(
                            (now_t - _freeze_done_t) * 1000.0
                        ),
                    )
            elif last_avg is not None and prev_change is not None:
                if prev_change < CHANGE_THRESHOLD <= change_score:
                    if lanes_changed >= SCENE_MIN_LANES:
                        # 场景切换/演出画面激活:几乎所有轨道同帧变化,不是音符。
                        # 重置基线继续等 —— 真首音会在之后以「少数轨道变化」的
                        # 形态越阈值(见 SCENE_MIN_LANES 注释)。
                        _maybe_log(
                            change_score,
                            band_avg,
                            "ignored(scene %d/7 lanes)" % lanes_changed,
                        )
                        logging.debug(
                            "首音检测: %.0fms 忽略场景切换(%d/7 轨变化, change=%.2f),"
                            "继续等真音符",
                            (frame_t - t_start) * 1000.0,
                            lanes_changed,
                            change_score,
                        )
                        last_avg = band_avg
                        last_edge_avg = edge_avg
                        last_lane_avg = lane_avg
                        last_rows = rows
                        prev_change = change_score
                        prev_frame_t = frame_t
                        continue
                    if _gate_reject(change_score, frame_t, "interp"):
                        continue
                    # change crossed the threshold between the last two frames
                    frac = (CHANGE_THRESHOLD - prev_change) / max(
                        change_score - prev_change, 1e-9
                    )
                    cross_t = prev_frame_t + frac * (frame_t - prev_frame_t)
                    wait_ms = PHOTOGATE_LATENCY - (
                        time.perf_counter() - cross_t
                    ) * 1000.0
                    _maybe_log(
                        change_score,
                        band_avg,
                        "trigger(interp {:.2f}->{:.2f})".format(
                            prev_change, change_score
                        ),
                    )
                    _log_trigger(
                        cross_t, change_score, "interp", lanes_changed,
                        [i for i, v in enumerate(lane_diff) if v >= LANE_CHANGE_MIN],
                    )
                    _snap("trigger")
                    wait_ms = _resolve_anchor_wait(
                        cross_t, wait_ms, note_y, change_score, "interp"
                    )
                    time.sleep(max(0, wait_ms) / 1000)
                    break
                elif change_score >= CHANGE_THRESHOLD:
                    # Already above threshold on both frames: the entry happened
                    # at or before this frame; compensate the elapsed time.
                    if _gate_reject(change_score, frame_t, "direct"):
                        continue
                    wait_ms = PHOTOGATE_LATENCY - (
                        time.perf_counter() - frame_t
                    ) * 1000.0
                    _maybe_log(
                        change_score,
                        band_avg,
                        "trigger(direct {:.2f})".format(change_score),
                    )
                    _log_trigger(
                        frame_t, change_score, "direct", lanes_changed,
                        [i for i, v in enumerate(lane_diff) if v >= LANE_CHANGE_MIN],
                    )
                    _snap("trigger")
                    wait_ms = _resolve_anchor_wait(
                        frame_t, wait_ms, note_y, change_score, "direct"
                    )
                    time.sleep(max(0, wait_ms) / 1000)
                    break
            prev_change = change_score
            prev_frame_t = frame_t
            last_avg = band_avg
            last_edge_avg = edge_avg
            last_lane_avg = lane_avg
            last_rows = rows
            _maybe_log(change_score, band_avg)
        except Exception as e:
            logging.error(f"Failed to get screen: {e}")
            capture_failures += 1
            if capture_failures >= CAPTURE_FAIL_MAX:
                # 再等下去也没有意义:让 Play 捕获并走 on_error,而不是空转到超时
                raise RuntimeError(
                    "连续 %d 次抓屏失败,放弃等待首音" % capture_failures
                ) from e
            time.sleep(0.05)
            continue


def init_maa():
    user_path = "./"
    resource_path = "assets/resource"

    res_job = maaresource.post_bundle(resource_path)
    res_job.wait()
    Toolkit.init_option(user_path)
    for i in range(3):
        adb_devices = Toolkit.find_adb_devices()
        if adb_devices:
            break
    if not adb_devices:
        logging.fatal("No ADB device found.")
        sys.exit(1)

    global device, maacontroller
    _device: list[AdbDevice] = []
    for device in adb_devices:
        extra_names = device.config.get("extras", {}).keys()
        if "mumu" in extra_names or "ld" in extra_names:
            if (device.name, device.address) not in [
                (d.name, d.address) for d in _device
            ]:
                _device.append(device)
    # config 可能不是 dict:config.yml 为空文件时 yaml.safe_load 返回 None。
    # 本文件 :87 与 :2625 都做了这个判空,这里也必须做,否则空配置直接
    # AttributeError(启动阶段崩,且没有任何有用日志)。
    _cfg = config if isinstance(config, dict) else {}
    filter_str = _cfg.get("device", {}).get("filter", "devices")
    _device = eval(filter_str, {}, {"devices": _device})

    # 用户显式指定了实例,但它可能不在 MAA 枚举结果里 ——
    # MAA 只认自己配置中登记过的地址(默认仅 127.0.0.1:16384)。
    # 多开时用户选的第 2、3 个实例就会漏掉,表现为「我明明选了它,却连不上」。
    # 这里显式 adb connect 一次,让 MAA 的下一次 find 能认出它。
    _device = _ensure_override_device(_device, DEVICE_OVERRIDE)
    # MAA 会把「已创建但没启动」的实例也列成候选(见该函数说明),先按 adb 实际
    # 连接能力过滤一遍 —— 这既让候选表与 GUI 口径一致,也让「只剩一台真正在跑」
    # 时走 `len(_device) == 1` 直接采用,不必再进自动判定。
    _device = _drop_unreachable_devices(_device)

    if not _device:
        logging.fatal("No supported devices were found.")
        sys.exit(1)
    elif len(_device) == 1 and not DEVICE_OVERRIDE:
        device = _device[0]
    else:
        # 多个设备(或显式指定):靠包名探测自动判定「哪个才是邦邦」。
        # 这里绝对不能有 input() —— GUI 启动的 bot 子进程没有 stdin 通道,
        # 拿不到输入;更不能静默挑第一个,那会让 minitouch 的绝对坐标点在
        # 别的游戏窗口上(见 _probe_device 上方的分层说明)。
        probes = _probe_all_devices(_device)
        logging.info("检测到 %d 个模拟器实例,逐个探测邦邦:", len(probes))
        for line in _format_device_choices(probes).splitlines():
            logging.info("%s", line)
        idx = _pick_device_by_probe(probes, DEVICE_OVERRIDE)
        if idx < 0:
            # 两种失败必须分开报,否则用户不知道下一步该做什么:
            #   · 全部探测不可用(installed=None) → 是「连不上/命令超时」,该去启动模拟器;
            #   · 至少一台探到了「没装邦邦」      → 是「选哪台」,该去 GUI 里选实例。
            unknown = [p for p in probes if p["installed"] is None]
            if probes and len(unknown) == len(probes):
                logging.fatal(
                    "候选的 %d 个模拟器实例都探测失败(adb 连不上或命令超时)—— "
                    "请确认模拟器与安卓系统已启动完成,再重试。\n候选设备:\n%s",
                    len(probes), _format_device_choices(probes),
                )
            else:
                logging.fatal(
                    "无法确定哪个实例是邦邦游戏 —— 为避免操作到其他游戏窗口,已停止。\n"
                    "请在 GUI 的「自由演出设置」里选择「模拟器实例」,或用命令行 "
                    "--device 指定端口/序号。\n候选设备:\n%s",
                    _format_device_choices(probes),
                )
            sys.exit(1)
        device = _device[idx]

    maacontroller = AdbController(
        adb_path=device.adb_path,
        address=device.address,
        screencap_methods=device.screencap_methods,
        input_methods=device.input_methods,
        config=device.config,
    )

    for i in range(3):
        if maacontroller.post_connection().wait().succeeded:
            break

    # tasker = Tasker(notification_handler=MyNotificationHandler())
    maatasker.bind(maaresource, maacontroller)

    if not maatasker.inited:
        logging.fatal("Failed to init MAA.")
        sys.exit(1)

    logging.info("MAA inited.")


def mnt_callback(event: MNTEvent, data: MNTEventData):
    global callback_data
    if event == MNTEvent.EVATIVE7_LOG:
        data: MNTEvATive7LogEventData = data

        cmd = data.cmd
        cost = data.cost

        with cmd_log_list_lock:
            cmd_log_list.append(data)
        cmd_type = cmd.split(" ")[0]

        # 必须用 with 而不是裸 acquire/release:正文里任何一句抛异常
        # (例如 `int(cmd.split(" ")[-1])` 遇到非预期指令格式)都会跳过 release,
        # 锁就永远不释放 —— 打歌主循环在 _adjust_offset() 处永久等待,整首卡死。
        with callback_data_lock:
            # 服务端滞后在线估计(测量端,2026-10-23 第四版):**绝对对齐**口径
            # —— 在线维护理想时间线指针 ideal(每条带时命令理想额度=d/m/u
            # 占 0、wait 占理想时长,从 chart.wait_ideal_ms 队列弹出),
            # lag = start - ideal,与 _dump_server_lag_async 的重建口径一致。
            # ⚠ 前三版教训(完整踩坑史见 chart.py __init__ 注释):
            #   第一版基准=字符串解析值,账本虚高 ~900ms;
            #   第二版基准=理想时长但逐条增量记账,自限但存量挂账;
            #   第三版基准=实际生效时长逐条增量,批间空闲被记成漂移,
            #     无自限正反馈,账本 +1358ms 推早 1.3s 批量 FAST。
            #   共同点:后两版都是「逐条增量」口径 —— 它把命令间一切时间差
            #   都算漂移,批间空闲与补偿天然耦合,反馈测量只能用绝对对齐。
            if cmd_type != "c":
                if _lag_fb["ideal"] is None:
                    # 首条带时命令:理想线从它对齐(与 dump 的曲内增长差值
                    # 口径等价,起点绝对值不影响增长判读)。
                    # 2026-10-29:首条命令发布后、回执回来前攒下的 wait 欠账
                    # (pending_skip)在这里一次补进指针 —— 它们发生在锚点之后,
                    # 漏掉就是系统性落后,正是「越打越早」的来源。
                    _lag_fb["ideal"] = data.start_time + _lag_fb["pending_skip"]
                    _lag_fb["skip_applied"] += _lag_fb["pending_skip"]
                    _lag_fb["pending_skip"] = 0.0
                    _lag_fb["lag"] = 0.0
                else:
                    _lag_fb["ideal"] += _lag_fb["last_sched"]
                    _lag_fb["lag"] = data.start_time - _lag_fb["ideal"]
                _lag_fb["last_start"] = data.start_time
                if cmd_type == "w":
                    chart = current_chart
                    if chart is not None and chart.wait_ideal_ms:
                        _lag_fb["last_sched"] = chart.wait_ideal_ms.popleft()
                        _lag_fb["wait_recv"] += 1
                    else:
                        # 队列空(异常/丢事件):退回命令串里的实际值,至少
                        # 不让估计发疯;整型解析误差 ≤0.5ms/条,一次性小误差。
                        # ⚠ 2026-10-28:这条兜底是「测量端退化」的入口 —— 实际值
                        # 已含补偿(比理想短),拿它当理想额度会让 ideal 指针少推进,
                        # lag 被吃成 0(零下拉)甚至虚高(テレキャスタービーボーイ
                        # 涨到 243ms)。只计数不改行为,等实机数据定改法。
                        _lag_fb["fallback_n"] += 1
                        _lag_fb["last_sched"] = float(cmd.split(" ")[-1])
                else:
                    _lag_fb["last_sched"] = 0.0
            if (last_cmd_endtime := callback_data.get("last_cmd_endtime")) != -1:
                callback_data["interval"]["total"] += 1
                callback_data["interval"]["total_offset"] += (
                    data.start_time - last_cmd_endtime
                )
            callback_data["last_cmd_endtime"] = data.end_time
            if cmd_type in ["w"]:
                callback_data["wait"]["total"] += 1
                callback_data["wait"]["total_offset"] += cost - int(
                    cmd.split(" ")[-1]
                )
            elif cmd_type in ["u", "d", "m"]:
                type_ = {
                    "u": "up",
                    "d": "down",
                    "m": "move",
                }[cmd_type]
                callback_data[type_]["uncommited"] += 1
                callback_data[type_]["total"] += 1
                callback_data[type_]["total_offset"] += cost
            elif cmd_type in ["c"]:
                total_uncommited = 0
                for type_ in ["up", "down", "move"]:
                    total_uncommited += callback_data[type_]["uncommited"]

                if total_uncommited != 0:
                    for type_ in ["up", "down", "move"]:
                        callback_data[type_]["total_offset"] += cost * (
                            callback_data[type_]["uncommited"] / total_uncommited
                        )
                        callback_data[type_]["uncommited"] = 0


def init_player_and_mnt():
    global current_player, mnt

    extra_config = device.config["extras"]
    if "mumu" in extra_config.keys():
        extra_config = extra_config["mumu"]
        type_ = "mumu"
        if device.name == "MuMuPlayer12":
            type_ += "v4"
        if device.name == "MuMuPlayer12 v5":
            type_ += "v5"
    elif "ld" in extra_config.keys():
        extra_config = extra_config["ld"]
        type_ = "ld"

    path = extra_config["path"]
    index = extra_config["index"]

    current_player = player.Player(type_, Path(path), index)
    mnt = MNT(
        device.address,
        type_="EvATive7",
        communicate_type=MNTServerCommunicateType.STDIO,
        mnt_asset_path=Path("./assets/minitouch_EvATive7"),
        callback=mnt_callback,
        adb_executor=str(device.adb_path.absolute()),
    )

    logging.info("Mumu and MNT inited.")


#: 启动时清理 debug/ 下超过这么多天的 autodori-*.log(用户 2026-10-17 要求)。
#: 只清自己写的日志:maa.log 由 MAA 覆盖写、*.png 是人工调试截图,都不动。
_LOG_KEEP_DAYS = 7


def _purge_old_logs(keep_days: int = _LOG_KEEP_DAYS):
    """删掉 debug/ 里超过 keep_days 天的 autodori-*.log,返回 (个数, 字节数)。

    背景:原来日志只增不删,长期使用 debug/ 会累积几十上百 MB(用户实测
    47 个 .log 已有 28MB)。在 basicConfig 之后调用也安全:当天新建的日志
    文件 mtime 就是现在,远在 cutoff 之内,不会被误删。删除失败(被占用/
    无权限)只跳过,绝不阻断启动。顺手 makedirs,保证 FileHandler 不因
    目录缺失直接崩。
    """
    log_dir = Path("debug")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0, 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    freed = 0
    for item in log_dir.glob("autodori-*.log"):
        try:
            stat = item.stat()
            if stat.st_mtime >= cutoff:
                continue
            freed += stat.st_size
            item.unlink()
            removed += 1
        except OSError:
            continue
    return removed, freed


def configure_log():
    # Force UTF-8 on stdout so the log lines (Japanese song names etc.) read
    # correctly by the GUI subprocess pipe instead of being locale-encoded.
    # logging.basicConfig() 默认写到 stderr,所以 stdout/stderr 都要重配 UTF-8,
    # 否则 GUI 从管道读到的仍是 GBK,日文歌名会乱码。
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8")
            except Exception:
                pass
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s[%(levelname)s][%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                "debug/autodori-{}.log".format(
                    datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                ),
                mode="w",
                encoding="utf-8",
            ),
        ],
    )
    # 清理历史日志(放在 basicConfig 之前删,这里只报一行结果)。
    _purged_n, _purged_bytes = _purge_old_logs()
    if _purged_n:
        logging.info(
            "已清理 %d 个超过 %d 天的历史日志(释放 %.1f MB)",
            _purged_n, _LOG_KEEP_DAYS, _purged_bytes / 1048576.0,
        )
    # 静音刷屏的 logger:打歌时 minitouch 会为每条触控指令打 DEBUG(一首歌上万行、
    # 占日志 ~98%),peewee 打 SQL、urllib3 打 HTTP 细节,对用户都无意义。
    # 想恢复这些调试信息时,把对应名字从下面列表里去掉即可。
    for _name in (
        "minitouch.py",
        "minitouch",
        "peewee",
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "asyncio",
        "matplotlib",
    ):
        logging.getLogger(_name).setLevel(logging.WARNING)


def _get_override_pipeline():
    all_pipelines = {}

    if SPECIAL_MODE:
        # 超高难度活动:曲目与难度都固定,不需要 set_difficulty / select_live_mode。
        # 只把「打完之后去哪」这三处从"回选歌继续挖矿"改写成"收尾并停止"。
        # 三处都给**完整节点定义**(不依赖框架的字段级合并语义),其中
        # wait_playresult / save_succeed_playresult 的其余字段与 live.json 逐字一致。
        all_pipelines["wait_playresult"] = {
            "recognition": "TemplateMatch",
            "template": ["live/scored.png", "live/activity_scored.png"],
            "next": "wait_playresult1",
            "pre_wait_freezes": {"threshold": 0.65, "time": 1500},
            # 原值还带 next_button/close_button/ok_button/confirm_button:
            # 活动得分界面右下角就是「再次演出 + 确定」,而 confirm/pink 在该「确定」上
            # 实测命中 0.9981 —— 留着这些按钮会把「点确定」从 SpecialFinish 手里抢走,
            # 变成框架行为不可预期。活动模式下只保留失败识别。
            "interrupt": ["live_failed"],
            "post_delay": 1000,
            # 认不出结算页时不要按"演出失败"落库(那会写进一条假战绩),
            # 直接走收尾:点掉可能的弹窗后停止。
            "on_error": ["special_finish"],
        }
        all_pipelines["save_succeed_playresult"] = {
            "recognition": "Custom",
            "custom_recognition": "PlayResultRecognition",
            "action": "Custom",
            "custom_action": "SavePlayResult",
            "custom_action_param": {"succeed": True},
            # 原值是 [event_reward_confirm, liveagain, live_home_button]:
            # liveagain 会"再次演出"、live_home_button 会回到自由演出首页,
            # 都与"打完一首就停"冲突。奖励弹窗改由 SpecialFinish 里显式点掉。
            "next": ["special_finish"],
            # 同样清掉按钮类 interrupt,保证「点确定」只由 SpecialFinish 执行一次。
            "interrupt": [],
        }
        all_pipelines["handle_life_exhausted"] = {
            "action": "Custom",
            "custom_action": "HandleLifeExhausted",
            # 原值 next="main" 会掉回自由演出的选歌流程,活动模式下必须收尾停止。
            # 这里走 special_stop(空节点,只 next 到 "stop")而**不是** special_finish:
            # 演出失败时画面上是失败弹窗(退出/放弃),让 SpecialFinish 去盲点粉色按钮
            # 可能点到"重试";而失败弹窗此时已被 life_exhausted_exit/confirm 点掉,
            # 直接停即可。
            # 注意 on_error 不能也写 "stop":MAA 的 check_all_next_list 会把
            # next/interrupt/on_error 三个列表并起来查重,重复即**整份资源加载失败**
            # (已用 _diag_duprule.py 实证)。
            "next": ["special_stop"],
            "on_error": ["stop"],
        }
        return all_pipelines

    if _STORY_MODE:
        # 阅读角色故事(2026-10-25 新增):story.json 是闭环链,不需要
        # set_difficulty / select_live_mode 等打歌节点,照 SPECIAL_MODE 的
        # 惯例在这里早退,只按故事类型换按钮模板并传入读数上限。
        # 上限读 data/config.yml 的 story.max_stories(默认 50,GUI 不暴露):
        # 收工判据(点完头像等不到成员详情)若因界面变化失效,没有上限
        # 就会无限重读第一个角色。上限已在 _main_impl 读好(_STORY_LIMIT)。
        for name, override in story_overrides(_STORY_TYPE, _STORY_LIMIT).items():
            all_pipelines.setdefault(name, {}).update(override)
        return all_pipelines

    if _DAILY_MODE:
        # 日常模式:「选择演出」界面(live 首页)点返回箭头回主页后,接回 daily_home
        # 走完整阶段1(火罐检查 + 点公告 + 停),而不是 start_app 重试掉进打歌循环
        # —— 后者会跳过火罐0停日常的检查(2026-10-17 用户在该界面点「开始日常」
        # 实测空转:start_app.next 六个节点全不命中 → 无限循环)。
        # main 模式用 start.json 里的默认定义(next=start_app,回主页后
        # live_home_button 命中进打歌循环,即"重启接管"的既有行为)。
        all_pipelines["select_live_home_back"] = {
            "recognition": "OCR",
            "expected": ["选择演出"],
            "roi": [90, 30, 320, 70],
            "action": "Click",
            "target": [29, 40, 130, 90],
            "post_delay": 1500,
            "next": ["daily_home"],
        }

    # set_difficulty
    difficulty: str = DIFFICULTY
    roi = {
        "easy": [659, 495, 107, 97],
        "normal": [768, 494, 107, 97],
        "hard": [886, 494, 105, 97],
        "expert": [996, 493, 107, 97],
        "special": [1086, 449, 192, 184],
    }[difficulty]
    all_pipelines["set_difficulty"] = {
        "action": "Click",
        "recognition": "TemplateMatch",
        "template": [
            f"live/difficulty/{difficulty}_active.png",
            f"live/difficulty/{difficulty}_inactive.png",
        ],
        "next": "get_song_name",
        "target": roi,
        "timeout": 5000,
        "interrupt": ["random_choice_song"],
    }

    # live mode
    livemode_pipeline = {
        "recognition": "OCR",
        "expected": "",
        "roi": [679, 183, 257, 354],
        "action": "Click",
        "post_delay": 1000,
        "next": ["select_song", "select_live_mode", "live_home_button"],
        "interrupt": ["login_expired", "connect_failed"],
    }
    if LIVEMODE == "freelive":
        livemode_pipeline["expected"] = "自由演出"
    elif LIVEMODE == "challengelive":
        livemode_pipeline["expected"] = "挑战演出"
    all_pipelines["select_live_mode"] = livemode_pipeline

    if LIVEMODE == "challengelive":
        for name, override in challenge_overrides(CHALLENGE_FINISH).items():
            all_pipelines.setdefault(name, {}).update(override)

    # _daily_song_stop 只有"还没确认三抽"时才为 True:那时每首打完都得回主页,
    # 才能进抽卡页检查。三抽确认完成后置 False,main 走 live.json 的原生连打链
    # (结算页「再次演出」→ 选曲 → 下一首),不再逐首回主页(用户 2026-10-17 要求)。
    if _DAILY_PULL_EACH_SONG and _daily_song_stop:
        # 一键日常的交替模式(清火+三抽同开):main 任务必须**打完一首就停**,
        # 由 _run_daily_flow 在 Python 层串"打歌 → 三抽 → 打歌"。
        # 改三处(都写完整定义,与 live.json 逐字一致,只动 next):
        #   save_succeed_playresult: 结算页点「再次演出」→ 左上返回 → 主页 → 停;
        #   event_reward_confirm:     奖励弹窗点掉后同样先认再次演出,再回主页;
        #   handle_life_exhausted:    打崩一首也回主页停,照常去做三抽。
        # daily_song_done = 认 live_home(主页「演出」按钮)后 stop(见 live.json)。
        all_pipelines["save_succeed_playresult"] = {
            "recognition": "Custom",
            "custom_recognition": "PlayResultRecognition",
            "action": "Custom",
            "custom_action": "SavePlayResult",
            "custom_action_param": {"succeed": True},
            # 2026-10-17 用户实况(竞演演出活动的「活动分数」结算页,即桌面 1.png):
            # 右下并排「再次演出」+「确定」,原 next 里只有用不到的两项,于是落到
            # interrupt 的 confirm_button → 点「确定」→ 进活动剧情(那屏底部还有
            # LOADING/0MB/3MB 下载条)→ 所有识别全不命中 → 永久卡住。
            # 用户定的口径:只点「再次演出」,「确定」彻底不点。
            # activity_liveagain = 点再次演出 → 左上返回 → 主页 → 停(daily_song_done)。
            # ⚠ 2026-10-28 教训:这里**不要**挂 rankup_confirm。活动结算会经过
            # 「等级 EXP」结果页(整屏白底),rankup_confirm.png(232x58 白底黑字)
            # 在角色卡牌下方白底实测误命中 0.9928,抢在 next_button 前面点了空白处,
            # 画面不走 → 20s Task timeout,整场连打被终止。活动页的推进交给
            # interrupt 里的 next_button(OCR「下一步」)。
            "next": ["event_reward_confirm", "activity_liveagain", "daily_song_done"],
            "interrupt": [
                "story_loading_wait",
                "reader_menu",
                "next_button",
                "close_button",
                "ok_button",
                "read_after",
            ],
        }
        all_pipelines["event_reward_confirm"] = {
            "recognition": "OCR",
            "expected": ["获得报酬", "获得奖励"],
            "roi": [340, 105, 600, 100],
            "action": "Click",
            "target": [560, 500, 160, 90],
            "timeout": 3000,
            "post_delay": 800,
            # 奖励弹窗点掉后仍然优先「再次演出」(活动分数页可能在弹窗下面)。
            # ⚠ 2026-10-28:这里同样不要挂 rankup_confirm(白底误命中,见上一条)。
            "next": ["activity_liveagain", "daily_song_done"],
        }
        all_pipelines["handle_life_exhausted"] = {
            "action": "Custom",
            "custom_action": "HandleLifeExhausted",
            "next": ["daily_song_done"],
            "on_error": ["stop"],
        }

    return all_pipelines


def get_current_version():
    global current_version
    try:
        metadata_text = Path("assets/build_metadata.json").read_text(encoding="utf-8")
        metadata = json.loads(metadata_text)
        current_version = metadata["version"]
    except Exception:
        logging.debug("Failed to get current version")


# 自动更新检查指向的仓库。
# ⚠️ 2026-10-09 修正:原来硬编码的是**上游原项目** `EvATive7/autodori`,于是本仓库
# (1979711854/bangdream-autodori)发布的版本用户永远看不到 —— 日志里长期显示的
# `Newest version: v1.0.21` 就是上游的版本号。换仓库/改名只改这一处。
UPDATE_REPO = "1979711854/bangdream-autodori"


def check_update():
    logging.debug("Checking for updates...")
    try:
        version = requests.get(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
        ).json()["tag_name"]
        logging.debug(f"Current version: {current_version}")
        logging.debug(f"Newest version: {version}")
        if compare_semver(version, current_version) == 1:
            ORANGE = "\033[38;5;208m"
            BOLD = "\033[1m"
            RESET = "\033[0m"

            print(
                f"{ORANGE}{BOLD}有更新可用：{version}，在 https://github.com/{UPDATE_REPO}/releases 下载最新版本{RESET}"
            )
            print(
                f"{ORANGE}{BOLD}An update is available: {version}, download the latest version at https://github.com/{UPDATE_REPO}/releases{RESET}"
            )
            time.sleep(5)

    except Exception as e:
        logging.error("failed to check for updates: {}".format(e))


def enable_high_precision_timer():
    """把 Windows 时钟粒度从默认 ~15.6ms 提到 1ms。

    默认粒度下 time.sleep 最多会超睡 ~15ms; 平衡电源计划下 CPU 频率调节会
    进一步放大抖动。打歌主循环靠 sleep 控制发布节奏, 超睡会让下一批命令迟到、
    服务端空转 → 音符按晚。提到 1ms 是最直接、开销最低的时序改善。
    """
    try:
        import ctypes
        import atexit

        ctypes.windll.winmm.timeBeginPeriod(1)
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)
        logging.debug("clock resolution raised to 1ms")
    except Exception as e:
        logging.debug("failed to raise clock resolution: %s", e)


def _log_environment():
    """启动后打印一次环境诊断信息(版本/模拟器/分辨率/配置),便于远程排查。
    纯日志,不影响流程。若解析器/分辨率不对,这里一眼可见。"""
    try:
        version = current_version or "(未知)"
        res = current_player.resolution
        orient = _get_orientation()
        dname = device.name if device else "?"
        daddr = device.address if device else "?"
        dadb = str(device.adb_path) if device else "?"
        extras = device.config.get("extras", {}) if device else {}
        emu_type = "mumu" if "mumu" in extras else ("ld" if "ld" in extras else "?")
        emu_cfg = extras.get(emu_type, {}) if emu_type in extras else {}
        emu_path = emu_cfg.get("path", "")
        emu_index = emu_cfg.get("index", "")
        gate = PHOTOGATE_LATENCY
        life = (config or {}).get("on_life_exhausted", "auto")
        boost = (config or {}).get("play_at_zero_boost", True)
        wf = get_runtime_info(current_player.resolution)["wait_first"]
        logging.info("===== 环境诊断 =====")
        logging.info("版本: %s", version)
        logging.info(
            "模拟器: %s [%s] addr=%s index=%s 路径=%s",
            emu_type, dname, daddr, emu_index, emu_path,
        )
        logging.info("adb 路径: %s", dadb)
        logging.info(
            "分辨率: %sx%s, 方向: %s°, 首音检测带 y=%s-%s",
            res[0], res[1], orient, wf["from"], wf["to"],
        )
        logging.info(
            "photogate=%sms, 生命耗尽=%s, 火罐0继续=%s, 难度=%s, 模式=%s",
            gate, life, boost, DIFFICULTY,
            "超高难度SPECIAL活动(固定单曲, 打完即停)" if SPECIAL_MODE else LIVEMODE,
        )
        # 2026-10-28:锚点实测开关加到环境诊断里 —— 排查「为什么一首全 P、
        # 另一首整曲偏早」时必须先知道这一轮到底有没有实测锚点。
        logging.info(
            "首音锚点实测=%s(时机校准页可切换, 下一首生效)",
            "开" if PHOTOGATE_MEASURE_ENABLED else "关",
        )
        if SPECIAL_MODE:
            logging.info("活动曲目: %s (#%s)", current_song_name or "?", current_song_id or "?")
        logging.info(
            "退出联动: 游戏退出自动停止=%s, 退出请求宽限=%.0fs, 游戏缺席判定=%.0fs",
            _stop_on_game_exit(), _EXIT_GRACE_S, _GAME_EXIT_GRACE_S,
        )
        # 环境自检: 音频禁用/帧率/内存 等会直接造成漂移或掉判定的项
        # (2026-10-17 去掉了「电源计划」项:提示了也不能自动修,用户反馈作用不大)
        try:
            findings = envcheck.check(emu_path)
        except Exception as e:  # 自检绝不阻断主流程
            findings = [("INFO", "环境自检异常: %s" % e)]
        if not findings:
            logging.info("环境自检: 未发现明显风险项")
        for level, message in findings:
            getattr(logging, {"ERROR": "error", "WARN": "warning"}.get(level, "info"))(
                "环境自检[%s]: %s", level, message
            )
        logging.info("===== 环境诊断结束 =====")
    except Exception as e:
        logging.debug("环境诊断失败: %s", e)


def main():
    """入口:装好日志后转入 _main_impl,并保证**任何未捕获异常都落进 debug 日志**。

    打包态(autodori.exe)没有控制台,未捕获异常的 traceback 默认只写 stderr。
    GUI 把 stderr 并进 stdout 后又按「关键事件」白名单过滤,整段栈会被丢掉,
    于是 debug/ 日志里一片沉默 —— 早期崩溃(初始化、谱面预处理阶段)只能靠本地
    复现才能定位(踩过一次:SPECIAL 谱面除零,日志停在半路且无任何报错)。
    """
    configure_log()
    enable_high_precision_timer()
    try:
        _main_impl()
    except SystemExit:
        # argparse 参数错误等,保持原语义直接抛出
        raise
    except KeyboardInterrupt:
        logging.info("收到中断信号,准备退出")
        _shutdown(0, "用户中断")
    except BaseException as e:
        # logging.exception 会把完整栈写进 FileHandler(GUI 也读得到第一行),
        # 然后走统一收尾 —— _shutdown 不返回。
        logging.exception("MAA 异常终止: %r", e)
        _shutdown(1, "未捕获异常: %s" % (e,))


def _task_failed(job, what: str) -> bool:
    """MAA 任务是否失败(界面识别超时/链断),失败时打一条警告。

    2026-10-28 rank up 弹窗事故的根因之一:maa 的 `Job.get()` 只取结果,
    任务失败**不抛异常**(见 .venv/Lib/site-packages/maa/job.py:54),
    所以 `post_task(...).wait().get()` 外面的 try/except 永远抓不到
    Tasker.Task.Failed —— 连打阶段因为「获得报酬」弹窗的按钮认不出而
    3.5s 超时失败,Python 却当成"连打正常结束",继续打「停止继续打歌」
    并在弹窗画面上硬跑三抽。这里统一改看 `job.failed`。

    桩对象/旧版本没有 failed 属性时按成功处理,行为与改动前一致。
    """
    if getattr(job, "failed", False):
        logging.warning("%s任务失败(界面识别超时),按异常收尾", what)
        return True
    return False


def _run_daily_flow(entry: str) -> None:
    """日常(原「一键清理日常」)的分阶段执行。GUI 现在只有这一个打歌入口。

    阶段1 daily       启动游戏 + 点掉开场公告/赠送弹窗(每日签到奖励也在这里领取),
                      回主界面。**必经流程,没有开关** —— 不点掉就进不了主界面。
    阶段2 main        仅 daily.auto_clear_fire 开启时:复用常规打歌主循环清火;
                      火量不足时按「自由演出设置 → 火罐为0时」三态处理(_zero_boost_policy)。
                      判据只能是火量(不是生命值),见 HandleLiveBoost。
    阶段3 daily_pull  仅 daily.auto_free_pull 开启时:每日免费三抽。

    2026-10-17 用户改需求:
    1. 清火与三抽**同时**开启时不是"先清火再抽卡",而是先打一首、检查三抽
       (_DAILY_PULL_EACH_SONG),确认做完之后**后续连打不再逐首回主页**。
    2. 三抽**只在还没确认做完时才跑**:管线节点 daily_pull_done / daily_pull_locked
       命中后由 MarkDailyPullDone 置 _daily_pull_done,之后每首直接跳过(每首白跑
       一次要 20-40 秒);收尾再复核一次(用户要求"保险起见")。
    3. 火罐为 0 不再是"无条件停",而是读「火罐为0时」三态:停在游戏主界面(默认) /
       继续挖矿 / 退出游戏。选 stop 时这里会先退回主页再收尾 —— 管线里的 stop 只是
       停在选曲确认页,与选项名承诺的"停在游戏主界面"不符。
    4. **回主页只在两个时刻发生**(用户 2026-10-17 反馈"每首打完都回主界面太麻烦"):
       还未确认三抽时,每首打完回一次(才能进抽卡页);三抽确认完成后改连打,
       直到火尽,收尾复核三抽前再回一次。
    """
    global _daily_boost_zero, _daily_pull_done, _daily_song_stop, _daily_fire_exhausted
    _daily_boost_zero = False
    _daily_pull_done = False
    _daily_song_stop = True
    job = maatasker.post_task(entry, _get_override_pipeline()).wait()
    job.get()
    # 2026-10-28:阶段1(启动游戏/领签到/点公告)失败以前完全无提示,后面会连锁
    # 失败却只看到打歌阶段报错。这里只告警不中断 —— 有些弹窗会被后续阶段的
    # 兜底节点点掉,保持原行为。
    _task_failed(job, "日常启动阶段")
    if _daily_boost_zero:
        # 阶段1(daily)里 daily_boost_check 已打过「火罐不足,日常已停止」日志
        return

    def _pull(reason: str, force: bool = False) -> str:
        """做一次每日免费三抽,返回三态字符串:

        "ok"   = 做完了(或本来就抽完/不需要抽);
        "skip" = 前置校验认不到主页「演出」按钮,本轮**不抽**,打歌照常继续;
        "fail" = 三抽链自身失败(界面识别超时/链断),调用方应收尾。

        2026-10-28 修:以前只返回 bool,precheck 一失败就当异常处理,阶段A
        直接 break(autodori-20261028-180117.log:打了一首、三抽被跳过、火还剩
        12、整场收工)。"不在主页"只意味着这一轮不能抽卡,不等于清火要停。
        另外 precheck 原先 next 直连 daily_pull,校验成功就会把整套三抽跑一遍,
        Python 又 post 一次 → 每轮跑两遍(18:05 场两行「今日次数已抽完」)。
        """
        if not _DAILY_FREE_PULL:
            return "ok"
        if _daily_pull_done and not force:
            logging.debug("每日免费三抽已确认完成,跳过检查(%s)", reason)
            return "ok"
        logging.info("执行每日免费三抽(%s)", reason)
        # 2026-10-28 rank up 弹窗事故:进抽卡流程前先确认人在游戏主界面。
        # daily_pull 是无 recognition 的跳板节点(必定命中),任何画面进来都能
        # "开始三抽";弹窗挡着时它会把弹窗里的白色「确定」当成
        # song_announce_confirm 点掉,然后断头 → 任务报"成功",三抽其实没做。
        # precheck 认不到主页「演出」按钮就 5s 超时,这里跳过并告警。
        # ⚠ 它的 next 只能是 stop(只当阀门);链进 daily_pull 就等于校验=开抽。
        try:
            failed = _task_failed(
                maatasker.post_task(
                    "daily_pull_precheck", _get_override_pipeline()
                ).wait(),
                "三抽前置校验",
            )
        except Exception as e:
            logging.warning("三抽前置校验异常(%s)", e)
            failed = True
        if failed:
            logging.warning(
                "当前不在游戏主界面,跳过本次每日免费三抽(%s)", reason
            )
            return "skip"
        try:
            if _task_failed(
                maatasker.post_task("daily_pull", _get_override_pipeline()).wait(),
                "每日免费三抽",
            ):
                return "fail"
        except Exception as e:
            logging.warning("每日免费三抽异常结束(%s)", e)
            return "fail"
        return "ok"

    def _return_to_home(reason: str) -> None:
        """从打歌结束界面点左上返回箭头一路退回主页。

        连打模式的收工点在选曲确认页(HandleLiveBoost 在 comfirm_song 之后判定
        火量并 stop),而三抽只能从主页进入,所以收尾前必须先退回去。
        复用 live.json 的 activity_back_to_home:认左上返回箭头就点,自循环直到
        命中 daily_song_done(主页「演出」按钮)才 stop。失败不阻断收尾。
        """
        # 用户 2026-10-17:「收尾回主页(连打结束)」看不懂,改成直白的动作描述。
        logging.info("停止继续打歌")
        try:
            # 2026-10-28:弹窗挡着时这里会认不到返回箭头,空转 20s 后任务失败;
            # 失败要打出来(以前被 .get() 吞掉,后面三抽在弹窗画面上盲跑)。
            if _task_failed(
                maatasker.post_task(
                    "activity_back_to_home", _get_override_pipeline()
                ).wait(),
                "收尾回主页",
            ):
                logging.warning("收尾回主页失败(%s)", reason)
        except Exception as e:
            logging.warning("收尾回主页失败(%s): %s", reason, e)

    if _DAILY_CLEAR_FIRE and _DAILY_PULL_EACH_SONG:
        # 阶段A:逐首打(main 每首一停,override 保证),打完就查三抽,直到确认完成。
        # 平时一两首就够 —— 三抽要么做完(daily_pull_done)要么因"未完成每日演出"
        # 不可抽(daily_pull_locked),两者都会置 _daily_pull_done。
        confirmed = False
        no_play_streak = 0
        # 2026-10-28 用户反馈「打完第一首没去三抽」:阶段1(daily)的 start_app 兜底
        # 在「游戏没停在主页」时会把那一首直接打掉(日志:11:13:24 开始打歌 → 11:16:34
        # 结算,紧跟 11:16:52 才 post 第一个 main)。那首走的是 daily 任务,不经过
        # 下面循环里的 _pull,于是三抽被跳过、直接进连打。
        # 这里补一次:进循环前按已打首数查三抽,确认完成就直接进连打阶段。
        if _song_play_seq > 0:
            res = _pull("第 %d 首打完(启动阶段)" % _song_play_seq)
            if res == "fail":
                logging.warning("启动阶段那首打完后的三抽异常,继续清火")
            elif res == "skip":
                # 不在主页只影响这一轮抽卡,清火照常继续(见 _pull docstring)。
                logging.warning("启动阶段那首打完后的三抽被跳过,继续清火")
            elif _daily_pull_done:
                confirmed = True
        for round_no in range(1, _DAILY_MAX_ROUNDS + 1):
            if confirmed:
                break
            _daily_fire_exhausted = False
            seq_before = _song_play_seq
            # 这里不再打「第 N 轮打歌」:轮次编号改由 play_song 按实际开打顺序打
            # (用户 2026-10-17:旧编号与真实顺序对不上,且连打阶段一个任务打多首
            #  根本没有轮次行,不如统一成每首一行的「开始打歌 · 第 N 首」)。
            try:
                job = maatasker.post_task("main", _get_override_pipeline()).wait()
                job.get()
            except Exception as e:
                # main 异常终止(识别不到界面等):不再重试,直接收尾
                logging.warning("打歌阶段异常结束(%s),停止循环", e)
                break
            if _task_failed(job, "打歌阶段"):
                # 2026-10-28 rank up 弹窗事故:结算弹窗的按钮认不出,MAA 3.5s
                # 超时判任务失败,而 Job.get() 不抛异常 —— 以前当成"正常打完"。
                # 这时画面停在弹窗上,继续查三抽只会点错东西,直接收尾。
                break
            if _song_play_seq == seq_before and not _daily_fire_exhausted:
                # main 正常返回但一首都没开打:断头链 —— 弹窗类节点没有 next,
                # 链中途命中把任务"成功"带走(见 _MAIN_NO_PLAY_MAX 注释)。
                # 点掉弹窗后重试即可继续;超过容忍次数才停,防异常画面死循环。
                no_play_streak += 1
                if no_play_streak >= _MAIN_NO_PLAY_MAX:
                    logging.warning(
                        "打歌入口连续 %d 次提前结束,停止清火流程", no_play_streak
                    )
                    break
                logging.info(
                    "打歌入口提前结束(未进入演出,可能被弹窗打断),重试 %d/%d",
                    no_play_streak, _MAIN_NO_PLAY_MAX,
                )
                continue
            no_play_streak = 0
            if _daily_fire_exhausted:
                # 火罐不足:本阶段收工(三抽留到收尾复核)。
                # 选项名叫「停在游戏主界面」,而管线里的 stop 只是停在选曲确认页,
                # 所以这里统一退回主页 —— 收尾三抽也只能从主页进。
                # 这里不再重复打「火罐不足,日常已停止」:HandleLiveBoost 已经打过
                # 一条带火量的(用户 2026-10-17 反馈两行一模一样,冗余)。
                _return_to_home("火罐不足")
                break
            # 用实际开打的序号(不是阶段A 的 round_no):阶段1 可能已经打过一首。
            res = _pull("第 %d 首打完" % _song_play_seq)
            if res == "fail":
                # 三抽链自身失败(界面识别超时/链断):画面可能停在半路,收尾。
                break
            if res == "skip":
                # 只是没在主页、这一轮抽不了:继续打下一首,别把整场清火带走。
                continue
            if _daily_pull_done:
                confirmed = True
                break
        else:
            logging.warning(
                "已达安全上限 %d 轮,强制结束日常流程", _DAILY_MAX_ROUNDS
            )

        # 阶段B:三抽已确认 → 后续连打,不再逐首回主页(用户 2026-10-17 要求)。
        # _daily_song_stop=False 后 _get_override_pipeline 不再改写结算/打崩三处
        # 的 next,main 走 live.json 原生链:结算 →「再次演出」→ 选曲 → 下一首,
        # 直到 HandleLiveBoost 判定火量不足时 stop。
        if confirmed and not _daily_fire_exhausted:
            _daily_song_stop = False
            _daily_fire_exhausted = False
            logging.info("每日三抽已确认完成,后续打歌不再逐首回主页")
            try:
                job = maatasker.post_task("main", _get_override_pipeline()).wait()
                job.get()
            except Exception as e:
                logging.warning("连打阶段异常结束(%s)", e)
            else:
                # 2026-10-28:连打阶段的失败同样是结算弹窗点不掉导致的超时,
                # 以前被 .get() 吞掉,后面照样回主页、照样跑三抽。
                _task_failed(job, "连打阶段")
            if _daily_fire_exhausted:
                logging.info("火罐不足，日常已停止")
            # 连打收工停在选曲确认页,三抽只能从主页进 —— 收尾前先退回去。
            if _DAILY_FREE_PULL:
                _return_to_home("连打结束")

        # 收尾复核:三抽前面已确认做完也再验一次(用户 2026-10-17 要求"保险起见")
        if _pull("收尾复核", force=True) == "fail":
            logging.warning("收尾复核的三抽异常结束")
    elif _DAILY_CLEAR_FIRE:
        # 纯清火(没勾三抽):main 自己循环打到火尽。main 可能因断头弹窗(节点无
        # next)一首都没打就"成功"返回(2026-10-19 教程弹窗实测),此时重试而不是
        # 静默收工;超过 _MAIN_NO_PLAY_MAX 次视为入口不可用,停止清火。
        logging.info("自动清火:进入打歌循环,火量耗尽后日常停止")
        no_play_streak = 0
        while True:
            seq_before = _song_play_seq
            try:
                job = maatasker.post_task("main", _get_override_pipeline()).wait()
                job.get()
            except Exception as e:
                logging.warning("打歌阶段异常结束(%s)", e)
                break
            if _task_failed(job, "打歌阶段"):
                break
            if _daily_fire_exhausted:
                break
            if _song_play_seq == seq_before:
                # 一首都没打:断头弹窗把 main"成功"带走了,重试(见阶段A 同款逻辑)
                no_play_streak += 1
                if no_play_streak >= _MAIN_NO_PLAY_MAX:
                    logging.warning(
                        "打歌入口连续 %d 次提前结束,停止清火流程", no_play_streak
                    )
                    break
                logging.info(
                    "打歌入口提前结束(未进入演出,可能被弹窗打断),重试 %d/%d",
                    no_play_streak, _MAIN_NO_PLAY_MAX,
                )
                continue
            # 打到歌了:纯清火的 main 走原生连打链,一次调用就打到火尽,无需再循环
            break
        if _daily_fire_exhausted:
            # 与交替模式一致:火尽后退回游戏主界面(选项名承诺的落点)。
            # 「火罐不足」的日志由 HandleLiveBoost 打,这里不重复。
            _return_to_home("火罐不足")
    else:
        logging.info("未勾选「自动清火打歌」,跳过清火阶段")
        if _pull("清火未勾选,单独执行三抽") == "fail":
            logging.warning("单独执行三抽时异常结束")

    if _daily_fire_exhausted and _zero_boost_policy() == "exit":
        logging.info("火罐不足且设置为「退出游戏」")
        try:
            maatasker.post_task("close_app", {}).wait().get()
        except Exception as e:
            logging.warning("关闭游戏失败: %s", e)


def _main_impl():
    parser = argparse.ArgumentParser(
        description="AutoDori script with different modes."
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["main", "special", "daily", "story"],
        help=(
            "Specify the mode to run. 'main' = 常规挖矿;"
            "'special' = 超高难度 SPECIAL 活动(固定单曲,玩家手动进入后接管);"
            "'daily' = 日常(启动游戏+领签到/弹窗;清火打歌与每日三抽各有开关);"
            "'story' = 阅读角色故事(用户筛选未读并停在乐队-培养页后接管)"
        ),
        default="main",
    )
    parser.add_argument(
        "--story-type",
        type=str,
        choices=["member", "memoir"],
        help=(
            "story 模式读哪一种故事:member=小故事,memoir=回忆小故事"
            "(游戏里一次只能筛选一种未读,故拆成两个参数)"
        ),
        default="member",
    )
    parser.add_argument(
        "--difficulty",
        type=str,
        choices=["easy", "normal", "hard", "expert", "special"],
        help="打歌难度:main 与 daily 都读(daily 由 GUI「自由演出设置」传入)",
        default="hard",
    )
    parser.add_argument(
        "--livemode",
        type=str,
        choices=["freelive", "challengelive"],
        help="Specify the live mode to run",
        default="freelive",
    )
    parser.add_argument(
        "--challenge-finish",
        choices=["home", "exit"],
        default="home",
        help="挑战演出 CP 不足 200 后的动作: home 返回主页面, exit 退出游戏",
    )
    parser.add_argument(
        "--liveboost",
        type=int,
        default=1,
        help="Specify the min liveboost for main mode. If current liveboost is lower than this value, the script will exit.",
    )
    parser.add_argument(
        "--special-song",
        type=str,
        default="",
        help=(
            "超高难度活动(--mode special)要打的曲目:曲库标题或 Bestdori 曲目 id。"
            "留空则读 data/config.yml 的 special_song,再退回内置默认值。"
        ),
    )
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="Specify if skip version check",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="",
        help=(
            "指定要使用的模拟器实例:MuMu 多开时填端口(如 16416)、"
            "完整地址(如 127.0.0.1:16416)或候选序号(如 0)。"
            "留空则按已安装邦邦的实例自动判定。"
        ),
    )
    args = parser.parse_args()

    global DIFFICULTY, MIN_LIVEBOOST, LIVEMODE, SPECIAL_MODE, DEVICE_OVERRIDE, CHALLENGE_FINISH
    global _DAILY_MODE
    global _DAILY_CLEAR_FIRE, _DAILY_FREE_PULL
    DEVICE_OVERRIDE = str(args.device or "").strip()

    if not args.skip_version_check:
        get_current_version()
        if current_version != None:
            check_update()

    if args.mode == "special":
        # 活动曲固定按 SPECIAL 谱面打;入口节点是 special_wait(不是 main),
        # 因为整条流程不做选曲 —— 见 assets/resource/pipeline/special.json。
        SPECIAL_MODE = True
        DIFFICULTY = "special"
        entry = "special_wait"
    elif args.mode == "daily":
        # 日常(现在 GUI 唯一的打歌入口):分三阶段执行(见 _run_daily_flow)。
        # 阶段1(启动 + 领签到/弹窗)是必经流程、没有开关;阶段2/3 各由一个开关控制 ——
        # 结构由用户 10-09 定,别再退回成"三个选项都得勾"。
        SPECIAL_MODE = False
        _DAILY_MODE = True
        # 难度/GUI「自由演出设置」→ 阶段2 的 set_difficulty(2026-10-17 合并前,
        # 日常不传 --difficulty,难度恒为模块默认 hard,界面上选 expert 根本不生效)。
        DIFFICULTY = args.difficulty
        daily_cfg = _runtime_config().get("daily") or {}
        _DAILY_CLEAR_FIRE = bool(daily_cfg.get("auto_clear_fire", False))
        _DAILY_FREE_PULL = bool(daily_cfg.get("auto_free_pull", False))
        # 两个开关同开 = 交替模式:每打完一首先做三抽再继续清火
        # (用户 2026-10-17 改的需求,详见 _run_daily_flow)。
        global _DAILY_PULL_EACH_SONG
        _DAILY_PULL_EACH_SONG = _DAILY_CLEAR_FIRE and _DAILY_FREE_PULL
        entry = "daily"
    elif args.mode == "story":
        # 阅读角色故事(2026-10-25 新增):用户已在游戏里筛好未读并停在
        # 「乐队-培养」列表页,bot 直接接管 —— 不启动游戏、不做签到,
        # 入口节点 story 先认列表页,不在列表页就超时结束,不瞎点。
        SPECIAL_MODE = False
        _DAILY_MODE = False
        global _STORY_MODE, _STORY_TYPE, _STORY_LIMIT
        _STORY_MODE = True
        _STORY_TYPE = args.story_type
        story_cfg = _runtime_config().get("story") or {}
        _STORY_LIMIT = int(story_cfg.get("max_stories", DEFAULT_MAX_STORIES))
        # 开跑日志(用户定的口径):首轮点击前打一条
        logging.info("阅读角色故事:开始(%s,本轮上限 %d 篇)",
                     "小故事" if _STORY_TYPE == "member" else "回忆小故事",
                     _STORY_LIMIT)
        entry = "story"
    else:
        SPECIAL_MODE = False
        _DAILY_MODE = False
        DIFFICULTY = args.difficulty
        entry = "main"
    LIVEMODE = args.livemode
    CHALLENGE_FINISH = args.challenge_finish
    MIN_LIVEBOOST = args.liveboost
    init_maa()
    init_player_and_mnt()
    if SPECIAL_MODE:
        _prepare_special_song(args.special_song)
    _log_environment()

    # 退出看门狗:关游戏/停任务任何一条链没落实都由它兜底,保证脚本一定停。
    _start_exit_watchdog()
    # 卡死看门狗(用户 2026-10-17):未知弹窗/界面把流程卡在原地时按返回键自愈,
    # 两次无效就停任务。special(故事)模式连续点「下一步」是正常行为,不启用。
    if not SPECIAL_MODE:
        _start_stuck_watchdog()
    # 控制台停止通道(2026-10-30):MAA 任务阻塞在 C 层时 Ctrl+C 送不进来,
    # 详见 _start_console_stop 的注释。必须在跑任务之前装。
    _start_console_stop()
    try:
        if args.mode == "daily":
            _run_daily_flow(entry)
        else:
            maatasker.post_task(entry, _get_override_pipeline()).wait().get()
            if _STORY_MODE:
                # 收尾汇总(用户定的口径,按结束方式区分):材料不足 / 达到上限
                # (还有未读) / 没到上限就结束(列表读空) / 没读到第一个。
                if story_insufficient():
                    logging.info("解锁小故事的道具不足,已暂停;"
                                 "补充「中小碎片」后再次点击继续。")
                elif story_read_count() >= _STORY_LIMIT:
                    logging.info("本轮已阅读 %d 篇,暂停;若还有未读,再次点击继续。",
                                 story_read_count())
                elif story_read_count() == 0:
                    logging.info("未开始阅读:未检测到「乐队-培养」列表页,"
                                 "请检查游戏画面后重试。")
                else:
                    logging.info("可读小故事已全部读完,共 %d 篇。", story_read_count())
    except KeyboardInterrupt:
        logging.info("收到中断信号,准备退出")
    except Exception as e:
        logging.exception("任务异常结束: %s", e)
    finally:
        # 收尾只有这一条路径:释放 minitouch/MAA 并硬退出(_shutdown 不返回)。
        # 用 finally 保证异常路径也走收尾 —— 旧写法 mnt.stop() 在异常时会被跳过。
        logging.debug("Ready to exit")
        _shutdown(0, _exit_reason or "任务结束")


if __name__ == "__main__":
    main()

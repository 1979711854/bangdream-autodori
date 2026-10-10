# -*- coding: utf-8 -*-
"""autodori 邦邦小助手 · GUI 启动器（Maa / Avalonia 风格）

布局:左栏选项列表 + 中栏主区(常驻运行控制 + 当前选项的设置卡片) + 底部状态栏。
一次只显示一类设置,避免小窗口里控件互相挤压。支持亮/暗主题与字体缩放。

逻辑:与旧版一致 —— 选难度/火罐策略/生命值策略/photogate,启动源码版或
独立版 bot,实时显示关键日志,按结算 FAST/SLOW 自动校准 photogate。

打包: pyinstaller --onefile --windowed --name autodori_gui gui.py
"""
import ast
import collections
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import ui_theme as T
import ui_widgets as W

# 设备发现(多开支持)。独立模块而非 import autodori:bot 在模块级就会拉
# Bestdori 曲库(离线直接崩),且打包的 gui.exe 不含 maa/bin —— 都用不了。
#
# import device_scan 放在 try **之外**是刻意的:PyInstaller 靠静态分析决定
# 打进哪些模块,而 `*.spec` 被 gitignore(.gitignore 有一行 *.spec),没法靠
# spec 的 hiddenimports 兜底。把唯一的 import 藏进 try 里,将来一次无关的重构
# 就可能让它悄悄不进包 —— 症状是「界面能开,但设备列表永远是空的」,
# 而且**没有任何报错**,属于极难排查的一类故障。
# 运行期容错靠下面的 getattr 兜底,不会因为缺模块就起不来。
import device_scan
from device_scan import label_for as device_scan_label

# 高 DPI 下 Tk 默认不感知，必须在创建 Tk() 之前声明
T.enable_dpi_awareness()

# 打包版:所有文件(bot.exe/assets/data)都在 exe 所在目录;
# 源码版:项目根目录(按 gui.py 位置推导,便于多份副本各自独立)。
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(BASE, ".venv", "Scripts", "python.exe")
SCRIPT = os.path.join(BASE, "src", "autodori.py")
CONFIG = os.path.join(BASE, "data", "config.yml")
GUI_CONFIG = os.path.join(BASE, "data", "gui_config.json")
BUILD_META = os.path.join(BASE, "assets", "build_metadata.json")

# 顶栏的常规演出固定使用自由演出；挑战演出由独立按钮启动。
LIVE_MODE = "freelive"
DIFFICULTIES = ["easy", "normal", "hard", "expert", "special"]
CHALLENGE_DIFFICULTIES = ["easy", "normal", "hard", "expert"]
# 超高难度 SPECIAL 活动(限时单曲):曲目固定、难度固定 SPECIAL、不打循环。
# 标题写法必须与曲库一致(简中客户端标题),也可直接填 Bestdori 曲目 id。
SPECIAL_SONGS = (
    "[超高难易度 新SPECIAL] SENSENFUKOKU",
    "[超高难易度 新SPECIAL] 六兆年と一夜物語",
    "[超高难易度 新SPECIAL] HELL! or HELL?",
    "ときめきエクスペリエンス！ (月岛麻里奈ver.)",
    "[期间限定 SPECIAL]ピコっと！パピっと！！ガルパ☆ピコ！！！",
)
DEFAULT_SPECIAL_SONG = SPECIAL_SONGS[0]
# 打歌策略:显示名 → 写入 data/config.yml 的 song_strategy 值
SONG_STRATEGIES = ("挖矿为主", "随机选歌")
STRATEGY_TO_CFG = {"挖矿为主": "mine", "随机选歌": "random"}
CFG_TO_STRATEGY = dict((v, k) for k, v in STRATEGY_TO_CFG.items())
STRATEGY_HINT = {
    "挖矿为主": "优先没打过 / 没全连 / 没全完美的歌(推荐)",
    "随机选歌": "无指定偏好,抽到哪首就打哪首",
}
WINDOW_SIZES = ["960x640", "1120x720", "1280x800", "1440x900", "1600x1000"]
DEFAULT_WINDOW = "1440x900"
DEFAULT_VIEW = "live.show"
APP_VERSION = "1.3.2"

# photogate 自动校准参数(见 _calibrate_gate)
CAL_STEP_MS = 15        # 校准步长上限(ms),偏差大时快速收敛
CAL_MIN_DIFF = 3        # |FAST-SLOW| 低于此值视为信号弱,不调整
CAL_RANGE = (0, 150)    # photogate 允许范围(ms)
CAL_COLLAPSE_STEP = 15  # 打歌中途崩(无结算)时按上次方向继续调整的步长(ms)

# bot 日志行: 2026-08-19 17:15:38,978[INFO][root] message
LOG_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2} )?(\d{2}:\d{2}:\d{2}),\d+\[(\w+)\](?:\[[^\]]*\])?\s?(.*)$"
)
SONG_RE = re.compile(r"Save song:\s*(.+)$")
# v1.1.2 bot 在每首开打前还会打 INFO 行:「打歌: {歌名} (#{id}-{难度}), 动作N, ...」。
# 作为歌名更新的第二条来源,避免依赖单一行偶发漏读。
PLAY_RE = re.compile(r"打歌:\s*(.+?)\s*\(#\d+-\w+\)")

# 左栏选项树:(分组名, ((key, 标签, 图标), ...))
OPTION_TREE = (
    ("演出", (
        ("live.show", "自由演出设置", "live"),
        ("live.challenge", "挑战演出清 CP", "live"),
        ("live.special", "超高难度活动", "live"),
        ("live.gate", "时基校准", "app-notification"),
    )),
    ("日常", (
        ("daily", "日常", "clipboard-check"),
    )),
    ("运行", (
        ("logs", "运行日志", "chat-bubble-check"),
    )),
    ("说明", (
        ("docs.notes", "注意事项", "docs"),
        ("docs.preread", "用前必读", "docs"),
        ("docs.faq", "常见问题", "docs"),
    )),
    ("设置", (
        ("settings.ui", "界面", "info-circle"),
        ("settings.about", "关于", "info-circle"),
    )),
)
VIEW_KEYS = [k for _, items in OPTION_TREE for k, _, _ in items]

# 底部状态栏环境信息
ENV_INFO = ("自由 / 挑战演出", "MuMu Player 12", "1280 × 720")


def _parse_play_result(line):
    """从 bot 日志行 'Play result: {...}' 里解析判定 dict,失败返回 None。

    纯函数,便于离线测试。
    """
    marker = "Play result: "
    idx = line.find(marker)
    if idx < 0:
        return None
    try:
        data = ast.literal_eval(line[idx + len(marker):].strip())
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _calibrate_gate(data, current_ms):
    """根据本曲 FAST/SLOW 判定,返回建议的 photogate 值;信号不足返回 None。

    语义:FAST 偏多=按早了=photogate 偏小→增大;SLOW 偏多=按晚了→减小。
    步长按"歪曲比例"自适应:歪得多大步快速收敛,歪得少小步精调,不再固定 ±10。
    任何核心字段 <0(OCR 失败)或信号太弱(|FAST-SLOW|<CAL_MIN_DIFF)都跳过。
    """
    try:
        fast = int(data.get("fast", 0) or 0)
        slow = int(data.get("slow", 0) or 0)
        great = int(data.get("great", 0) or 0)
        perfect = int(data.get("perfect", 0) or 0)
        good = int(data.get("good", 0) or 0)
        bad = int(data.get("bad", 0) or 0)
        miss = int(data.get("miss", 0) or 0)
    except Exception:
        return None
    if min(fast, slow, great, perfect, good, bad, miss) < 0:
        return None
    if fast + slow == 0:
        return None  # 没有 FAST/SLOW,无需调整
    bias = fast - slow
    if abs(bias) < CAL_MIN_DIFF:
        return None  # 信号太弱,暂不调整

    # 误差量级估计:FAST+SLOW 占全部判定的比例越高,photogate 偏离越大。
    # 经验系数 50ms/100%:2% 歪→步长 1ms 精调,10%→5ms,30%→15ms(封顶)。
    total = perfect + great + good + bad + miss
    off_ratio = (fast + slow) / max(total, 1)
    step = max(1, min(CAL_STEP_MS, round(off_ratio * 50)))

    direction = 1 if bias > 0 else -1
    new_ms = current_ms + direction * step
    return max(CAL_RANGE[0], min(CAL_RANGE[1], new_ms))


# 注意事项内容(只读)
NOTES = """【模拟器设置】
• 使用 MuMu Player 12,分辨率 1280x720,Vulkan 渲染
• 保持高帧率,不要限制 30fps(会破坏打歌同步)
• 打歌期间尽量不要操作电脑,避免性能波动

【游戏设置】
• 游戏:邦邦国服(bilibili)
• 支持自由演出(freelive)和挑战演出(challengelive),不支持协力模式
• 自由演出的选曲列表设为"正常",清空歌曲筛选器
• 演出设定:将流速调整为 8.0
• 演出效果·音量设定:关闭"3D切入模式","动作模式"改为"轻量模式"
• 演出效果·音量设定:启用"FAST/SLOW表示"和"Perfect状态显示"

【使用提醒】
• 本脚本用于自动挖矿(自动打歌刷取资源/活动奖励),仅供个人使用
• 请遵守游戏规则与用户协议,不要用于破坏游戏秩序或影响其他玩家
• MuMu 多开时:在「自由演出设置 → 模拟器实例」里选要连的那台,选择会被记住。
  多开通常是克隆镜像,每个实例都装了同一个游戏,脚本看不出哪个才是邦邦,
  所以请务必手动指定 —— 连错会点到别的游戏窗口上
• 如有问题,先查看 debug 目录下的日志
"""


# 常见问题(只读)
FAQ = """Q:多开模拟器时怎么指定连哪个窗口?
A:在「自由演出设置 → 模拟器实例」里点刷新按钮,列表会列出所有在线实例(名称 + 端口)。
选对的那个即可,选择会被记住,下次启动自动沿用。点「预览」可以看某个实例当前的画面,
确认它是不是你要操作的那台。

列表只显示「实例名 + 端口」(以前还会附「装了邦邦 / 运行中」,太长会把框挤爆,
已去掉)。装没装邦邦不需要你看 —— 脚本启动时会自己去每个实例上按包名探测,
挑出装了邦邦的那台;多开克隆镜像时每台都一样,同样靠端口或「预览」逐个确认。

保持「自动」时,只有一个在线实例可以直接用;有多个时脚本会用启发式挑,
不确定就手动指定。

端口规律:MuMu 12 第 n 个实例是 127.0.0.1:(16384+32n),第一个 16384,第二个 16416。
命令行也支持:`--device 16416`(端口)、`--device 127.0.0.1:16416`(完整地址)、
`--device 0`(序号)三种写法都接受。

Q:提示「无法确定哪个实例是邦邦游戏」怎么办?
A:脚本在多个实例中无法唯一判断时会直接停止并列出候选,不会随便挑一个去乱点别的窗口。
按顺序排查:
① 点「刷新」重新扫描;② 确认模拟器已完全启动(不是停在启动画面);
③ 用「预览」逐个看画面,在下拉框里手动指定正确的实例;
④ 若列表里根本没有你的实例,确认 MuMu 是否为多开器版本、端口是否被占用。

Q:打歌总是 FAST(狂爆 FAST / 整体按早),怎么办?
A:先检查 MuMu 模拟器「设置 → 设备 → 声音」里的「禁用安卓系统声音」是否被勾选——若勾选请取消
(允许系统声音),这是 FAST 偏移最常见的原因,关闭后即完全正常;若还不行,再在「时基校准」里开自动校准 photogate。

Q:为什么无法正常打歌?
A:请查看注意事项和 README.md,检查游戏和模拟器设置是否正确。

Q:为什么有些歌会爆很多 GREAT 和 MISS?
A:个别歌曲难度较大,机器识别可能存在一定延迟;
模拟器长时间运行后发热/内存占用上升,触控输入延迟波动变大,精度下降。
建议:偶尔重启模拟器、保证电脑不过热、保持高帧率。

如果几乎每首都 GREAT 偏多(而不是个别难歌),通常是 photogate(打歌时基)没对准,需要调节:
• 最简单:在「时基校准」里打开自动校准 photogate,正常打几首歌,脚本会根据
每首的 FAST/SLOW 分布自动微调,几首内收敛,不用自己算;
• 也可以手动改 photogate 数值:结算页 GREAT 偏 SLOW(按晚)就减小,
偏 FAST(按早)就增大,每次调 10ms 左右,范围建议 0~150ms。

Q:使用脚本有封号的风险吗?
A:存在封号的可能性,但只要不用于冲榜,封号的概率就不大。

Q:我发现了 BUG?
A:可以反馈到 GitHub Issues。

Q:如何指定打某一首歌?
A:自由演出可手动把想打的歌加入游戏内的收藏,让脚本只从收藏里选;
挑战演出请先在游戏里选好歌曲和乐队,再从「挑战演出清 CP」页启动。
"""


# 用前必读:photogate 校准说明(只读)
PRE_READ = """【先看这里 · 狂爆 FAST / 整体按早?】
先检查 MuMu 模拟器「设置 → 设备 → 声音」里的「禁用安卓系统声音」是否被勾选,
若勾选请取消(允许系统声音)。这是 FAST 偏移最常见的原因,关闭后即完全正常。

【photogate 是什么】
bot 以光闸确定打歌起点:第一个音符进入屏幕检测带时按下秒表,
再等 photogate(毫秒)后开始整首歌。
这个值代表音符从检测带到判定线的耗时,还和每台电脑的截屏/触控延迟有关,
所以每台机器的最佳值不一样,默认 30 不一定适合你。

【怎么知道没对准】
先把注意事项里的设置都配好(流速 8.0 / 分辨率 1280x720 / 高帧率)。
正常打歌应基本全 PERFECT。
如果几乎每首 GREAT 都偏多(而不是个别难歌),就是 photogate 没对准。

【最简单:自动校准】
在「时基校准」里打开自动校准 photogate,正常打几首歌。
脚本会读每首结算的 FAST/SLOW 分布自动微调,几首内收敛,不用自己算。
每首结算后日志会显示一行判定: PERFECT … · GREAT …(FAST x/SLOW y),
若偏 FAST/SLOW,校准状态会出现自动校准: photogate 30 → 40 …。

【也可以手动调】
时基校准页的 photogate 数值:
• GREAT 偏 SLOW(按晚了)→ 减小
• GREAT 偏 FAST(按早了)→ 增大
每次调 10ms 左右,范围建议 0~150ms。
提示:(SLOW偏多则减小,FAST偏多则增大)

【注意】
• 本脚本并不保证每次都能 AP(全完美),目标是尽量稳定 FC(全连);个别难歌或
  电脑偶发波动出现 GREAT / 少量 MISS 属正常现象,不用反复纠结。
• 自动校准值会在下一首歌开始前生效,无需重启;
• 校准和每台机器绑定,换模拟器/电脑后建议重新校准;
• 打歌期间别动电脑,性能波动也会造成 GREAT。
"""

DOCS = {
    "docs.notes": NOTES,
    "docs.preread": PRE_READ,
    "docs.faq": FAQ,
}


class AutodoriGUI:
    def __init__(self, root):
        self.root = root
        root.title("BanG Dream · 邦邦自动挖矿助手")
        root.minsize(1080, 600)

        self.proc = None
        self.q = queue.Queue()
        self._last_cal_direction = 0  # 上次校准方向:+1 增大 / -1 减小 / 0 未知
        self._log_buf = collections.deque(maxlen=1500)
        # 完整原始日志(含时间戳/级别/logger),与显示用的精简缓冲分离:
        # 导出日志时输出这份全量,而不是被"关键事件"过滤后的精简集。
        self._raw_log = collections.deque(maxlen=20000)
        # 是否正处于一段 traceback 之内:栈的后续帧以「无时间戳裸行」到达,
        # 只有首行带 ERROR 级别。不追这几行的话栈就是断的(见 _handle_line)。
        self._in_traceback = False
        self.started_at = None
        self.songs_done = 0
        self.current_song = ""
        self._idle_text = "就绪"

        # 读取 GUI 偏好设置
        cfg = self._load_gui_config()
        self.theme = cfg.get("theme", "light")
        self.font_size = int(cfg.get("font_size", 10))
        self.window_size = cfg.get("window_size", DEFAULT_WINDOW)
        self.view = cfg.get("view", DEFAULT_VIEW)
        if self.window_size not in WINDOW_SIZES:
            self.window_size = DEFAULT_WINDOW
        if self.view not in VIEW_KEYS:
            self.view = DEFAULT_VIEW

        # 运行参数(重建界面时保留)
        self.difficulty = cfg.get("difficulty", "expert")
        self.challenge_difficulty = cfg.get("challenge_difficulty", "hard")
        if self.challenge_difficulty not in CHALLENGE_DIFFICULTIES:
            self.challenge_difficulty = "hard"
        self.challenge_exit_game = bool(cfg.get("challenge_exit_game", False))
        self.boost_mode = cfg.get("boost_mode", "继续打歌")
        self.life_mode = cfg.get("life_mode", "自动退出重新选歌")
        self.auto_cal = bool(cfg.get("auto_cal", False))
        self.song_strategy = cfg.get("song_strategy", "挖矿为主")
        if self.song_strategy not in SONG_STRATEGIES:
            self.song_strategy = "挖矿为主"
        # 超高难度活动是独立配置,刻意不共用上面的难度/策略(它是固定单曲 SPECIAL)
        self.special_song = cfg.get("special_song", DEFAULT_SPECIAL_SONG)
        if not str(self.special_song).strip():
            self.special_song = DEFAULT_SPECIAL_SONG
        # 模拟器实例(多开时指定连哪个)。保存的是 adb 地址(127.0.0.1:16416),
        # 空串 = 自动识别(按已安装邦邦的实例挑)。
        self.device_address = str(cfg.get("device_address", "") or "")
        # 手填的 adb 路径(自动发现覆盖不到时的出口);空串 = 自动发现
        # adb_path 只兼容「早期版本手填过并留在配置里」的情况 —— 界面上已不再
        # 提供手填入口(需求:删掉该功能)。留读是为了别让老配置里的路径失效。
        self.adb_path = str(cfg.get("adb_path", "") or "")
        # 日常页配置(见 Issue #4)。
        # 「启动游戏」= 启动游戏 + 领取每日签到奖励 + 领取弹窗赠送奖励 ——
        # 这几步是进主界面的必经流程,**没有关掉的余地**,所以恒为 True:
        # 界面上仍显示成一个勾选项(默认勾选),但点不动(见 _detail_daily)。
        # 真正可选的是「清火打歌」与「每日三抽」,各自一个开关。
        # 2026-10-10 用户明确:不能做成可选项 —— 之前不勾就点不动清火/三抽,
        # 新用户会误以为「脚本坏了、啥也干不了」。
        self.daily_enabled = True
        self.daily_auto_clear_fire = bool(cfg.get("daily_auto_clear_fire", False))
        self.daily_auto_free_pull = bool(cfg.get("daily_auto_free_pull", False))
        self._closing = False         # 关窗口标记:让后台扫描线程及时收手
        self._devices = []          # device_scan.discover() 的结果
        self._device_labels = []    # 与 self._devices 一一对应的下拉框标签
        self._device_scanning = False
        self._shots = {}            # adb address -> 截图路径(多开时肉眼区分实例)
        self.gate = self._read_gate()

        T.set_theme(self.theme)
        W.set_base_size(self.font_size)
        self._build()
        self.root.geometry(self.window_size)
        self._sync_run_state()

        self.root.after(120, self._poll_log)
        self.root.after(500, self._tick)
        # 开机自动扫一次实例:多开时用户一眼就能看到有几个窗口可选。
        # 延后触发,别拖慢界面弹出(扫描要跑 adb 子进程,几秒起步)。
        self.root.after(700, self._scan_devices_async)
        # 关闭窗口时杀掉 bot 子进程,防止它在后台继续点游戏
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 配置读写 ----------
    def _load_gui_config(self):
        try:
            with open(GUI_CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_gui_config(self):
        cfg = {
            "theme": self.theme,
            "font_size": int(self.font_size),
            "window_size": self.window_size,
            "view": self.view,
            "difficulty": self.difficulty,
            "challenge_difficulty": self.challenge_difficulty,
            "challenge_exit_game": self.challenge_exit_game,
            "boost_mode": self.boost_mode,
            "life_mode": self.life_mode,
            "auto_cal": bool(self.auto_cal),
            "song_strategy": self.song_strategy,
            "special_song": self.special_song,
            # 多开时指定实例;空串 = 自动识别。属于 GUI 自己的偏好,
            # 不写进 data/config.yml(那份是 bot 的运行配置)。
            "device_address": self.device_address,
            # 手填的 adb 路径;空串 = 自动发现
            "adb_path": self.adb_path,
            # 一键清理日常(见 Issue #4)
            "daily_enabled": bool(self.daily_enabled),
            "daily_auto_clear_fire": bool(self.daily_auto_clear_fire),
            "daily_auto_free_pull": bool(self.daily_auto_free_pull),
        }
        try:
            os.makedirs(os.path.dirname(GUI_CONFIG), exist_ok=True)
            with open(GUI_CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _read_gate(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("timing", {}).get("photogate_latency_ms", 30))
        except Exception:
            return 30

    def _version(self):
        try:
            with open(BUILD_META, encoding="utf-8") as f:
                raw = str(json.load(f).get("version", APP_VERSION))
            # 容忍 build.json 已带 "v" 前缀,避免渲染出 "vv1.1.2"
            return raw[1:] if raw.lower().startswith("v") else raw
        except Exception:
            return APP_VERSION

    # ---------- 骨架 ----------
    def _build(self):
        for child in self.root.winfo_children():
            child.destroy()
        th = T.get()
        self.root.configure(bg=th["app_bg"])

        shell = tk.Frame(self.root, bg=th["app_bg"])
        shell.pack(fill="both", expand=True)

        self._build_header(shell)

        body = tk.Frame(shell, bg=th["app_bg"])
        body.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(body, bg=th["surface"], width=376)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        self.host = tk.Frame(body, bg=th["app_bg"])
        self.host.pack(side="left", fill="both", expand=True)

        self._build_statusbar(shell)
        self._render_view()

    def _build_header(self, parent):
        th = T.get()
        bar = tk.Frame(parent, bg=th["surface"], height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        logo = tk.Canvas(bar, width=28, height=28, bg=th["surface"],
                         highlightthickness=0, bd=0)
        logo.pack(side="left", padx=(16, 8))
        logo.create_polygon(W.round_points(1, 1, 26, 26, 8),
                            fill=th["accent_soft"], outline="")
        W.draw_icon(logo, "live", 7, 7, th["accent"])

        tk.Label(bar, text="BanG Dream", bg=th["surface"], fg=th["text"],
                 font=(T._FONT, self.font_size + 5, "bold")).pack(side="left")
        tk.Label(bar, text="· 邦邦自动挖矿助手", bg=th["surface"],
                 fg=th["text_2"], font=T.font(self.font_size)).pack(
            side="left", padx=(8, 0))

        chip = tk.Frame(bar, bg=th["accent_soft"])
        chip.pack(side="left", padx=10)
        tk.Label(chip, text="v" + self._version(), bg=th["accent_soft"],
                 fg=th["accent_text"],
                 font=T.font(self.font_size - 1)).pack(padx=7, pady=2)

        # 运行控制按钮常驻顶栏右侧。
        # 顶栏高度固定、宽度只跟随窗口，不受内容区（歌曲名/日志/文档）影响，
        # 因此任何视图下按钮位置都恒定，不会被长文本挤出或遮挡(需求 1/2)。
        self.stop_btn = W.PushButton(bar, "停止", command=self.stop,
                                     kind="secondary", width=88, height=34)
        self.stop_btn.pack(side="right", padx=(0, 16))
        self.start_btn = W.PushButton(bar, "开始自由演出", command=self.start,
                                      kind="primary", width=144, height=34)
        self.start_btn.pack(side="right", padx=(0, 8))

    def _build_sidebar(self):
        th = T.get()
        # 外层容器,留出 padding 给 SidePanel
        outer = tk.Frame(self.sidebar, bg=th["surface"])
        outer.pack(fill="both", expand=True, padx=8, pady=10)

        # 整个侧栏包一个圆角矩形线框(Maa 任务列表观感)
        self.side_panel = W.SidePanel(outer, radius=14, padding=8,
                                      surface="panel")
        self.side_panel.pack(fill="both", expand=True)
        panel = self.side_panel.body

        self.nav_items = {}
        for group, items in OPTION_TREE:
            tk.Label(panel, text=group, bg=th["panel"], fg=th["text_3"],
                     font=T.font(self.font_size - 1, "bold")).pack(
                anchor="w", padx=10, pady=(12, 6))
            for key, label, icon in items:
                item = W.NavItem(panel, label, icon, width=348,
                                 font_offset=2,
                                 surface="panel",
                                 trailing="gear",
                                 command=lambda k=key: self._switch(k))
                item.pack(fill="x", padx=6, pady=3)
                self.nav_items[key] = item
        self.nav_items[self.view].select(True)

        # 左下角主题切换图标按钮(留在侧栏外底部,不被 SidePanel 包住)
        bottom = tk.Frame(self.sidebar, bg=th["surface"])
        bottom.pack(fill="x", side="bottom", pady=(0, 10))
        self.theme_btn = W.IconButton(
            bottom,
            "moon" if T.is_dark() else "sun",
            command=self._toggle_theme, size=34,
        )
        self.theme_btn.pack(side="left", padx=18)
        tk.Label(bottom, text="切换主题", bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(side="left", padx=4)

    def _build_statusbar(self, parent):
        th = T.get()
        bar = tk.Frame(parent, bg=th["surface"], height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=th["border"], height=1).pack(fill="x")

        line = tk.Frame(bar, bg=th["surface"])
        line.pack(fill="both", expand=True)

        self.status_dot = W.StatusDot(line, th["idle"])
        self.status_dot.pack(side="left", padx=(16, 6))
        self.status_text = tk.Label(line, text=self._idle_text,
                                    bg=th["surface"], fg=th["text_2"],
                                    font=T.font(self.font_size - 1))
        self.status_text.pack(side="left")

        for i, text in enumerate(ENV_INFO):
            tk.Label(line, text=text, bg=th["surface"], fg=th["text_3"],
                     font=T.font(self.font_size - 1)).pack(
                side="left", padx=(18 if i == 0 else 14, 0))

        self.status_gate = tk.Label(line, text="photogate %d ms" % self.gate,
                                    bg=th["surface"], fg=th["text_3"],
                                    font=T.font(self.font_size - 1))
        self.status_gate.pack(side="right", padx=16)

    def _switch(self, key):
        if key == self.view:
            return
        self.view = key
        self._save_gui_config()
        self._render_view()

    # ---------- 内容区 ----------
    def _render_view(self):
        for child in self.host.winfo_children():
            child.destroy()
        # 卡片里的控件随重建被销毁,必须先清掉引用 —— 否则 _sync_run_state 会去
        # 操作已销毁的控件并抛 TclError,连带把 _render_view 打断。
        self.special_start_btn = None
        self.device_box = None
        self.device_status = None
        self.device_refresh_btn = None
        self.device_preview_btn = None
        self.challenge_start_btn = None
        self.daily_start_btn = None
        for key, item in self.nav_items.items():
            item.select(key == self.view)

        th = T.get()
        scroll = W.ScrolledFrame(self.host)
        scroll.pack(fill="both", expand=True)

        grid = tk.Frame(scroll.inner, bg=th["app_bg"])
        grid.pack(fill="both", expand=True, padx=16, pady=16)
        grid.columnconfigure(0, weight=1)

        self._card_run(grid).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self._card_detail(grid).grid(row=1, column=0, sticky="nsew")
        self._sync_run_state()

    def _card_run(self, master):
        """常驻的运行状态卡片（只读，操作按钮已移至顶栏）。

        自上而下三段，彼此不共享横向空间：
        1. 状态行：状态点 + 运行中/未运行 + 提示语；
        2. 当前曲目：独占一整栏，长歌名按可用像素宽度截断（FitLabel）；
        3. 指标行：运行时长 / 已完成。

        歌名不再与按钮、指标同处一行，因此既不会挤走按钮，也不会被裁半(需求 1/2)。
        """
        th = T.get()
        card = W.Card(master)

        head = tk.Frame(card.body, bg=th["surface"])
        head.pack(fill="x")
        self.run_dot = W.StatusDot(head, th["idle"], size=10)
        self.run_dot.pack(side="left", pady=(3, 0))
        self.run_state = tk.Label(head, text="未运行", bg=th["surface"],
                                  fg=th["text"],
                                  font=T.font(self.font_size + 3, "bold"))
        self.run_state.pack(side="left", padx=8)
        self.run_hint = tk.Label(head, text="选择模式并点击对应的开始按钮",
                                 bg=th["surface"], fg=th["text_3"],
                                 font=T.font(self.font_size - 1))
        self.run_hint.pack(side="left")

        # 当前曲目独占一栏:FitLabel 不向上传递天然宽度,长歌名只会被截断,
        # 不会把卡片撑宽、更不会影响其它行的控件。
        song = tk.Frame(card.body, bg=th["surface"])
        song.pack(fill="x", pady=(12, 0))
        tk.Label(song, text="当前曲目", bg=th["surface"], fg=th["text_2"],
                 font=T.font(self.font_size - 1)).pack(anchor="w")
        self.m_song = W.FitLabel(
            song, self._fmt_song(self.current_song),
            size=self.font_size + 3, weight="bold", fg=th["text"],
        )
        self.m_song.pack(fill="x", pady=(2, 0))

        metrics = tk.Frame(card.body, bg=th["surface"])
        metrics.pack(fill="x", pady=(12, 0))
        self.m_time = W.Metric(metrics, "运行时长", "00:00:00")
        self.m_time.pack(side="left", padx=(0, 48))
        self.m_songs = W.Metric(metrics, "已完成", "%d 首" % self.songs_done)
        self.m_songs.pack(side="left")

        tk.Label(card.body, text="提示:若开始按钮首次点击无反应,关闭窗口重新打开一次即可",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(12, 0))
        return card

    @staticmethod
    def _fmt_song(name):
        """歌名占位与兜底截断。

        真正的按宽度截断由 FitLabel 按像素二分完成（保证整字省略、不撑破布局），
        这里只设一个宽松上限，避免极端情况下持有超长字符串。
        """
        if not name:
            return "—"
        limit = 60
        return name if len(name) <= limit else name[:limit] + "…"

    def _card_detail(self, master):
        """按当前选项渲染对应的设置卡片。"""
        if self.view == "live.show":
            return self._detail_show(master)
        if self.view == "live.challenge":
            return self._detail_challenge(master)
        if self.view == "live.special":
            return self._detail_special(master)
        if self.view == "live.gate":
            return self._detail_gate(master)
        if self.view == "logs":
            return self._detail_logs(master)
        if self.view in DOCS:
            return self._detail_docs(master)
        if self.view == "settings.ui":
            return self._detail_ui(master)
        if self.view == "daily":
            return self._detail_daily(master)
        return self._detail_about(master)

    def _detail_show(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "自由演出设置", "仅支持自由演出").pack(
            fill="x", pady=(0, 12))

        r = W.Row(card.body, "难度")
        r.pack(fill="x", pady=(0, 16))
        W.DropdownBox(r.slot, DIFFICULTIES, value=self.difficulty,
                      width=200, min_width=180,
                      on_change=self._on_difficulty).pack(side="left")

        r = W.Row(card.body, "打歌策略")
        r.pack(fill="x", pady=(0, 2))
        W.DropdownBox(r.slot, SONG_STRATEGIES, value=self.song_strategy,
                      width=200, min_width=180,
                      on_change=self._on_song_strategy).pack(side="left")
        self.strategy_hint = tk.Label(
            card.body, text=STRATEGY_HINT.get(self.song_strategy, ""),
            bg=th["surface"], fg=th["text_3"],
            font=T.font(self.font_size - 1), anchor="w", justify="left")
        self.strategy_hint.pack(anchor="w", pady=(0, 14))

        # 模拟器实例放在最上面:多开时这是**第一个**要确认的东西 ——
        # 连错实例会直接点到别的游戏窗口,比任何设置错误都严重。
        self._device_row(card.body)

        r = W.Row(card.body, "火罐为 0 时")
        r.pack(fill="x", pady=(0, 16))
        W.DropdownBox(r.slot, ["继续打歌", "退出游戏"],
                      value=self.boost_mode, width=200, min_width=180,
                      on_change=self._on_boost).pack(side="left")

        r = W.Row(card.body, "生命值耗尽后")
        r.pack(fill="x", pady=(0, 16))
        W.DropdownBox(r.slot, ["自动退出重新选歌", "等待手动操作"],
                      value=self.life_mode, width=240, min_width=200,
                      on_change=self._on_life).pack(side="left")

        tk.Label(card.body, text="修改后立即写入配置,下一次开始演出时生效",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(10, 0))
        return card

    def _detail_challenge(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "挑战演出清 CP",
                       "按剩余 CP 自动选择可用倍率").pack(fill="x", pady=(0, 12))

        self._device_row(card.body)

        r = W.Row(card.body, "难度")
        r.pack(fill="x", pady=(0, 16))
        W.DropdownBox(r.slot, CHALLENGE_DIFFICULTIES,
                      value=self.challenge_difficulty, width=200, min_width=180,
                      on_change=self._on_challenge_difficulty).pack(side="left")

        for line in (
            "先在游戏里选好挑战歌曲和队伍，停在挑战选曲界面。",
            "按剩余CP选择最高可用倍率:8一4一2一1倍；不足200 CP时停止。",
        ):
            tk.Label(card.body, text=line, bg=th["surface"], fg=th["text_2"],
                     font=T.font(self.font_size - 1), anchor="w",
                     justify="left").pack(anchor="w", pady=(0, 6))

        # 用户 10-09 反馈「点错了按钮、以为挑战难度没生效」——两个开始按钮(顶栏
        # 那个是自由演出)确实容易混,和「超高难度活动」页一样加一句显式提示。
        tk.Label(card.body,
                 text="注意:GUI 右上角的「开始自由演出」按钮走的是常规自由演出,与本页无关",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1), anchor="w",
                 justify="left").pack(anchor="w", pady=(6, 0))

        exit_game = tk.BooleanVar(master=card.body, value=self.challenge_exit_game)
        tk.Checkbutton(
            card.body, text="清完 CP 后退出游戏（未勾选则返回主页面）",
            variable=exit_game,
            command=lambda: self._on_challenge_exit_game(exit_game.get()),
            bg=th["surface"], fg=th["text_2"], selectcolor=th["surface"],
            activebackground=th["surface"], activeforeground=th["text_2"],
            font=T.font(self.font_size - 1), anchor="w",
        ).pack(anchor="w", pady=(8, 12))
        self.challenge_start_btn = W.PushButton(
            card.body, "开始清 CP", command=lambda: self.start(mode="challenge"),
            kind="primary", width=160, height=36)
        self.challenge_start_btn.pack(anchor="w", pady=(0, 4))
        return card

    def _on_challenge_difficulty(self, value):
        self.challenge_difficulty = value
        self._save_gui_config()

    def _on_challenge_exit_game(self, value):
        self.challenge_exit_game = value
        self._save_gui_config()

    def _detail_special(self, master):
        """超高难度 SPECIAL 活动(hard-coded 单曲,独立于自由演出配置)。

        这个模式的接管点很窄:玩家手动进入活动、把界面停在「选择乐队」页
        (右下角有「演出开始」按钮那一页),脚本只负责补点开演,然后按固定谱面
        打这一首,打完停在结算页并点掉「确定」,随即停止。
        """
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "超高难度 SPECIAL 活动",
                       "限时单曲 · 固定难度 SPECIAL").pack(fill="x", pady=(0, 12))

        r = W.Row(card.body, "曲目")
        r.pack(fill="x", pady=(0, 6))
        # 活动曲目标题很长(「[超高难易度 新SPECIAL] 曲名」),方框按可用宽度撑开
        W.DropdownBox(r.slot, SPECIAL_SONGS, value=self.special_song,
                      width=640, min_width=560,
                      on_change=self._on_special_song).pack(side="left")
        tk.Label(card.body,
                 text="曲目与难度由活动固定,不参与自由演出的抽歌/挖矿逻辑",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(0, 14))

        tk.Label(card.body, text="操作步骤", bg=th["surface"], fg=th["text_2"],
                 font=T.font(self.font_size, "bold")).pack(anchor="w")
        for i, line in enumerate((
            "1. 在游戏里手动进入到「选择乐队」界面(选好乐队,停在右下角「演出开始」按钮那一页)",
            "2. 回到 GUI 点击下面的「开始(超高难度活动)」,脚本会接管开演",
            "3. 打完歌后会停在结算页面,不会继续下一首(点「确定」后脚本自动停止)",
        ), 1):
            tk.Label(card.body, text=line, bg=th["surface"], fg=th["text_2"],
                     font=T.font(self.font_size - 1),
                     anchor="w", justify="left").pack(anchor="w", pady=(6 if i == 1 else 2, 0))

        tk.Label(card.body,
                 text="注意:GUI 右上角的「开始自由演出」按钮走的是常规自由演出,与本页无关",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(14, 8))

        self.special_start_btn = W.PushButton(
            card.body, "开始(超高难度活动)",
            command=lambda: self.start(mode="special"),
            kind="primary", width=200, height=36)
        self.special_start_btn.pack(anchor="w", pady=(0, 4))
        return card

    def _on_special_song(self, value):
        self.special_song = value
        self._save_gui_config()

    def _detail_gate(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "photogate 时基", "0 ~ 150 ms").pack(
            fill="x", pady=(0, 12))

        row = tk.Frame(card.body, bg=th["surface"])
        row.pack(fill="x")
        # 输入框可直接键入 0~150 任意整数(回车/失焦生效,越界自动归一化),
        # 不再只能按 10ms 一档地加减。
        self.gate_step = W.Stepper(row, value=self.gate, min_v=CAL_RANGE[0],
                                   max_v=CAL_RANGE[1], step=1, big_step=10,
                                   on_change=self._on_gate_manual)
        self.gate_step.pack(side="left")
        tk.Label(row, text="ms", bg=th["surface"], fg=th["text_2"],
                 font=T.font(self.font_size - 1)).pack(side="left", padx=8)
        tk.Label(row, text="可直接在输入框内键入 0 ~ 150 的任意数值,回车生效",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(side="left", padx=8)

        tk.Label(card.body, text="± 每次 1 ms;按住 Shift 点击 ± 每次 10 ms",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(8, 0))

        toggle = tk.Frame(card.body, bg=th["surface"])
        toggle.pack(fill="x", pady=(14, 4))
        tk.Label(toggle, text="自动校准 photogate", bg=th["surface"],
                 fg=th["text"], font=T.font(self.font_size)).pack(side="left")
        W.ToggleSwitch(toggle, value=self.auto_cal,
                       on_change=self._on_auto_cal).pack(side="left", padx=16)
        tk.Label(toggle, text="根据每首结算的 FAST/SLOW 自动微调",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(side="left")

        tk.Label(card.body, text="手动调参:SLOW 偏多则减小,FAST 偏多则增大",
                 bg=th["surface"], fg=th["text_2"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(10, 0))

        self.cal_label = tk.Label(card.body, text="", bg=th["surface"],
                                  fg=th["accent_text"], justify="left",
                                  font=T.font(self.font_size))
        self.cal_label.pack(anchor="w", pady=(8, 10), fill="x")

        W.PushButton(card.body, "恢复默认 30", command=self._reset_gate,
                     kind="secondary", height=30, padx=14).pack(anchor="w")
        return card

    def _detail_logs(self, master):
        th = T.get()
        card = W.Card(master)
        head = tk.Frame(card.body, bg=th["surface"])
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="运行日志", bg=th["surface"], fg=th["text"],
                 font=T.font(self.font_size + 1, "bold")).pack(side="left")
        W.PushButton(head, "打开日志目录", command=self._open_log_dir,
                     kind="ghost", height=26, padx=12).pack(side="right",
                                                           padx=(8, 0))
        W.PushButton(head, "清空", command=self._clear_logs, kind="ghost",
                     height=26, padx=12).pack(side="right", padx=(8, 0))
        W.PushButton(head, "导出日志", command=self._export_logs, kind="ghost",
                     height=26, padx=12).pack(side="right", padx=(8, 0))

        hint = tk.Label(
            card.body,
            text="界面只显示关键事件（选歌 / 开演 / 结算 / 错误），完整调试信息在「导出日志」"
                 "与 debug 目录的 autodori-YYYYMMDD-HHMMSS.log 中，遇到问题请一并上传 "
                 "autodori-*.log 和 maa.log。\n"
                 "提示:长期使用后 debug 目录会积累很多历史日志,请定期点「打开日志目录」"
                 "清理没用的 autodori-*.log(全部删除不影响运行)。",
            bg=th["surface"], fg=th["text_3"], font=T.font(self.font_size - 1),
            justify="left", anchor="w",
        )
        hint.pack(fill="x", pady=(0, 6))
        # 换行宽度跟随卡片实际宽度动态适配:窗口宽时尽量单行、不把符号挤到行首,
        # 窗口窄时按可用宽度自然折行。
        hint.bind("<Configure>", lambda _e: hint.configure(
            wraplength=max(hint.winfo_width(), 300)))

        self.full_log = W.LogConsole(card.body, height=16)
        self.full_log.pack(fill="both", expand=True)
        self._replay(self.full_log)
        return card

    def _detail_docs(self, master):
        th = T.get()
        card = W.Card(master)
        label = dict((k, l) for _, items in OPTION_TREE
                     for k, l, _ in items).get(self.view, "说明")
        W.SectionTitle(card.body, label, "只读").pack(fill="x", pady=(0, 10))
        # 方案 X:用 DocView 逐段 Label 垂直排布,整页随内容增高,
        # 由外层 ScrolledFrame 整页滚动,根治 Text 固定 height 截断问题。
        text = W.DocView(card.body)
        text.pack(fill="x")
        text.set_content(DOCS.get(self.view, ""))
        return card

    def _detail_ui(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "界面").pack(fill="x", pady=(0, 12))

        r = W.Row(card.body, "主题")
        r.pack(fill="x", pady=(0, 16))
        W.DropdownBox(r.slot, ["亮色", "暗色"],
                      value="暗色" if T.is_dark() else "亮色",
                      width=160, min_width=120,
                      on_change=self._on_theme_pick).pack(side="left")

        r = W.Row(card.body, "字体大小")
        r.pack(fill="x", pady=(0, 16))
        self.font_step = W.Stepper(r.slot, value=self.font_size, min_v=8,
                                   max_v=16, step=1)
        self.font_step.pack(side="left")
        tk.Label(r.slot, text="保存后重建界面生效", bg=th["surface"],
                 fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(side="left", padx=16)

        r = W.Row(card.body, "窗口尺寸")
        r.pack(fill="x", pady=(0, 16))
        self.size_box = W.DropdownBox(r.slot, WINDOW_SIZES,
                                      value=self.window_size,
                                      width=200, min_width=160,
                                      on_change=self._on_size_pick)
        self.size_box.pack(side="left")

        W.PushButton(card.body, "保存并应用", command=self._save_settings,
                     kind="primary", height=32, padx=16).pack(anchor="w")
        self.set_hint = tk.Label(card.body, text="主题与字体保存后立即生效",
                                 bg=th["surface"], fg=th["text_3"],
                                 font=T.font(self.font_size - 1))
        self.set_hint.pack(anchor="w", pady=(8, 0))
        return card

    def _detail_daily(self, master):
        """一键清理日常(Issue #4)。

        分成两类,这是用户 10-09 定的结构:
          · **总开关即「必经流程」**:启动游戏 + 领取每日签到奖励 + 领取弹窗赠送奖励。
            每日签到与开场赠送弹窗**不点掉就进不了主界面**,所以它们没有"关掉"的余地,
            不做成勾选项 —— 免得用户以为不勾就不会领。
          · **可选两步,各自独立开关**:清火打歌、每日免费三抽。
        """
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "日常",
                       "启动游戏(含签到/弹窗) + [清火] + [三抽]").pack(
            fill="x", pady=(0, 12))

        en = tk.BooleanVar(master=card.body, value=True)
        tk.Checkbutton(
            card.body, text="启动游戏",
            variable=en,
            # 点不动:取消勾选立刻弹回。它是必经流程,不给关掉的余地
            # (2026-10-10 用户明确「不能改成可选项」)。
            command=lambda: en.set(True),
            bg=th["surface"], fg=th["text"], selectcolor=th["surface"],
            activebackground=th["surface"], activeforeground=th["text"],
            font=T.font(self.font_size), anchor="w",
        ).pack(anchor="w", pady=(0, 2))
        tk.Label(card.body,
                 text="自动完成:启动游戏 + 领取每日签到奖励 + 领取弹窗赠送奖励",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1), anchor="w",
                 justify="left").pack(anchor="w", pady=(0, 10))

        # 两个可选步骤,各自独立。「启动游戏」恒为勾选,所以这里不需要置灰逻辑。
        # lambda 用默认参数绑住 var/cb —— 循环里的闭包直接引用循环变量会全部
        # 指到最后一项(晚绑定),点哪一个都触发同一个回调。
        for text, value, cb in (
            ("自动清火打歌（先打歌清火，完成每日演出后自动转入三抽）",
             self.daily_auto_clear_fire, self._on_daily_auto_clear_fire),
            ("每日免费三抽（固定 3 次，游戏内置上限；需先完成每日演出才解锁）",
             self.daily_auto_free_pull, self._on_daily_auto_free_pull),
        ):
            var = tk.BooleanVar(master=card.body, value=value)
            tk.Checkbutton(
                card.body, text=text,
                variable=var,
                command=lambda v=var, f=cb: f(v.get()),
                bg=th["surface"], fg=th["text_2"], selectcolor=th["surface"],
                activebackground=th["surface"], activeforeground=th["text_2"],
                font=T.font(self.font_size - 1), anchor="w",
            ).pack(anchor="w", pady=(0, 4))

        self.daily_start_btn = W.PushButton(
            card.body, "开始日常", command=lambda: self.start(mode="daily"),
            kind="primary", width=160, height=36)
        self.daily_start_btn.pack(anchor="w", pady=(10, 4))
        tk.Label(card.body,
                 text="清火与三抽都不勾时,只做「启动游戏 + 领签到/弹窗」两件事",
                 bg=th["surface"], fg=th["text_3"],
                 font=T.font(self.font_size - 1)).pack(anchor="w", pady=(8, 0))
        return card

    def _on_daily_auto_clear_fire(self, value):
        self.daily_auto_clear_fire = bool(value)
        self._save_gui_config()

    def _on_daily_auto_free_pull(self, value):
        self.daily_auto_free_pull = bool(value)
        self._save_gui_config()

    def _detail_about(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "关于").pack(fill="x", pady=(0, 10))
        for k, v in (
            ("项目", "BanG Dream · 邦邦自动挖矿助手"),
            ("版本", "v" + self._version()),
            ("演出模式", "自由演出 / 挑战演出"),
            ("分辨率", "1280 × 720 · MuMu Player 12"),
            ("仓库", "github.com/1979711854/bangdream-autodori"),
            ("提醒", "仅供个人学习使用,请遵守游戏规则与用户协议"),
        ):
            row = tk.Frame(card.body, bg=th["surface"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=k, bg=th["surface"], fg=th["text_2"],
                     font=T.font(self.font_size - 1), width=8,
                     anchor="w").pack(side="left")
            tk.Label(row, text=v, bg=th["surface"], fg=th["text"],
                     font=T.font(self.font_size - 1), anchor="w",
                     justify="left").pack(side="left")
        return card

    # ---------- 交互回调 ----------
    def _on_difficulty(self, value):
        self.difficulty = value
        self._save_gui_config()

    def _on_boost(self, value):
        self.boost_mode = value
        self._save_gui_config()

    def _on_life(self, value):
        self.life_mode = value
        self._save_gui_config()

    def _on_song_strategy(self, value):
        self.song_strategy = value
        self._save_gui_config()
        if getattr(self, "strategy_hint", None):
            self.strategy_hint.configure(text=STRATEGY_HINT.get(value, ""))
        self._write_config()

    def _on_gate_manual(self, value):
        self.gate = value
        self._write_config()
        if getattr(self, "status_gate", None):
            self.status_gate.configure(text="photogate %d ms" % self.gate)

    def _on_auto_cal(self, value):
        self.auto_cal = bool(value)
        self._save_gui_config()

    def _on_theme_pick(self, value):
        self._pending_theme = "dark" if value == "暗色" else "light"

    def _on_size_pick(self, value):
        self._pending_size = value

    def _toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self._save_gui_config()
        self._rebuild()

    def _save_settings(self):
        self.theme = getattr(self, "_pending_theme", self.theme)
        self.window_size = getattr(self, "_pending_size", self.window_size)
        try:
            self.font_size = int(self.font_step.get())
        except Exception:
            pass
        self._save_gui_config()
        self.root.geometry(self.window_size)
        self._rebuild()

    def _rebuild(self):
        T.set_theme(self.theme)
        W.set_base_size(self.font_size)
        self._build()
        self._sync_run_state()

    def _clear_logs(self):
        self._log_buf.clear()
        if getattr(self, "full_log", None):
            self.full_log.clear()

    def _open_log_dir(self):
        """打开 debug 日志目录,方便用户定位并上传日志文件。"""
        debug_dir = os.path.join(BASE, "debug")
        try:
            os.makedirs(debug_dir, exist_ok=True)
            if os.name == "nt":
                os.startfile(debug_dir)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", debug_dir])
        except Exception as e:
            messagebox.showerror("打开失败", str(e), parent=self.root)

    def _export_logs(self):
        """导出完整原始日志(含时间戳/级别/logger 的全量 DEBUG 流)。

        之前导出的是 GUI 界面里「关键事件」过滤后的精简缓冲,丢失了大量
        对定位打歌问题至关重要的细节(首音检测 wfF/wfT、触控偏移校准
        Adjust offset、生命检测 OCR 等),分析价值低。现在改为导出完整
        原始行,并附运行环境摘要,便于直接发给开发者分析。
        """
        from tkinter import filedialog
        default_name = "autodori-%s.log" % time.strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出日志",
            defaultextension=".log",
            initialfile=default_name,
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# autodori 导出日志 · %s\n" % time.strftime(
                    "%Y-%m-%d %H:%M:%S"))
                f.write("# 环境: 版本 v%s · 分辨率 1280x720 · 主题 %s\n" % (
                    self._version(), self.theme))
                f.write("# 运行配置: 难度 %s · photogate %d ms · 打歌策略 %s\n" % (
                    self.difficulty, self.gate, self.song_strategy))
                f.write("# 提示: 如遇打歌不准/崩溃,请一并上传 debug 目录下的\n")
                f.write("#   autodori-YYYYMMDD-HHMMSS.log 和 maa.log\n")
                f.write("# " + "-" * 70 + "\n")
                if not self._raw_log:
                    f.write("(无日志)\n")
                for line in self._raw_log:
                    f.write(line + "\n")
            n = len(self._raw_log)
            self._emit(time.strftime("%H:%M:%S"), "OK",
                       "已导出 %d 条完整日志到 %s" % (n, path))
        except Exception as e:
            self._emit(time.strftime("%H:%M:%S"), "ERROR",
                       "导出日志失败: %s" % e)
            messagebox.showerror("导出失败", str(e), parent=self.root)

    # ---------- 日志 ----------
    def _replay(self, console):
        for item in self._log_buf:
            console.line(*item)

    def _emit(self, stamp, level, message):
        # 统一兜底时间戳:调用方若没传(空串),用当前时刻补齐,保证日志栏
        # 每行都有「HH:MM:SS」,不会再出现时间列空白。
        if not stamp:
            stamp = time.strftime("%H:%M:%S")
        item = (stamp, level, message)
        self._log_buf.append(item)
        console = getattr(self, "full_log", None)
        # 切走「运行日志」页再切回时,full_log 可能指向已被重建销毁的旧控件;
        # 若在销毁窗口里对它 .line() 会抛 TclError 并连带杀死 _poll_log 轮询。
        # 这里只写入缓存,真正渲染交给 _replay/下一次 _detail_logs 重建。
        if console is not None:
            try:
                if console.winfo_exists():
                    console.line(*item)
            except Exception:
                pass

    # ---------- photogate 自动校准 ----------
    def _on_life_exhausted(self):
        """打歌中途生命耗尽(无结算):自动校准时按上一次 FAST/SLOW 方向继续调。

        崩溃说明当前 photogate 偏差仍大,用大步长;若无历史方向信号则提示手动。
        """
        if not self.auto_cal:
            return
        cur = self.gate
        if self._last_cal_direction == 0:
            self._set_cal("打歌中途崩了且无方向信号,请手动调整 photogate")
            return
        direction = self._last_cal_direction
        new_val = max(
            CAL_RANGE[0],
            min(CAL_RANGE[1], cur + direction * CAL_COLLAPSE_STEP),
        )
        verb = "增大(上次偏FAST按早)" if direction > 0 else "减小(上次偏SLOW按晚)"
        self._apply_photogate(new_val)
        self._set_cal("打歌中途崩,按上次方向{}: photogate {} → {} ms".format(
            verb, cur, new_val))

    def _on_play_result(self, data):
        """结算 OCR 到达:展示判定汇总;开启自动校准时按 FAST/SLOW 微调 photogate。"""
        self.songs_done += 1
        ms = getattr(self, "m_songs", None)
        if ms is not None and ms.winfo_exists():
            ms.set("%d 首" % self.songs_done)
        self._emit(
            "", "OK",
            "结算 · 分数 {} · COMBO {} · PERFECT {} · GREAT {}(FAST {}/SLOW {}) · GOOD {}/BAD {}/MISS {}".format(
                data.get("score", -1), data.get("maxcombo", -1),
                data.get("perfect", -1), data.get("great", -1),
                data.get("fast", -1), data.get("slow", -1),
                data.get("good", -1), data.get("bad", -1),
                data.get("miss", -1),
            ),
        )
        if not self.auto_cal:
            return
        try:
            fast_c = int(data.get("fast", 0) or 0)
            slow_c = int(data.get("slow", 0) or 0)
            self._last_cal_direction = (
                1 if fast_c > slow_c else (-1 if slow_c > fast_c else 0)
            )
        except Exception:
            self._last_cal_direction = 0
        cur = self.gate
        new_val = _calibrate_gate(data, cur)
        if new_val is None:
            self._set_cal("本曲信号不足/无需调整,photogate 保持 {}".format(cur))
            return
        if new_val == cur:
            self._set_cal("photogate 已在边界 {},不再调整".format(cur))
            return
        verb = "增大(偏FAST按早)" if new_val > cur else "减小(偏SLOW按晚)"
        self._apply_photogate(new_val)
        self._set_cal("自动校准: photogate {} → {} ms · {}".format(
            cur, new_val, verb))

    def _set_cal(self, text):
        # 切走 live.gate 页后,cal_label 指向的旧控件已被销毁,仅判非空会命中死控件
        # 而抛 TclError;必须再用 winfo_exists() 确认窗口仍存在。
        lbl = getattr(self, "cal_label", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=text)

    def _apply_photogate(self, new_val):
        """写新 photogate 到 config(保留 life/boost 设置),并更新界面。

        单独改写 timing 字段,不复用 _write_config,避免覆盖用户已选的其他策略。
        """
        old = self.gate
        self.gate = new_val
        step = getattr(self, "gate_step", None)
        if step is not None and step.winfo_exists():
            step.set(new_val)
        sg = getattr(self, "status_gate", None)
        if sg is not None and sg.winfo_exists():
            sg.configure(text="photogate %d ms" % self.gate)
        try:
            cfg = {}
            try:
                with open(CONFIG, encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    cfg = json.loads(content)
            except Exception:
                cfg = {}
            if not isinstance(cfg, dict):
                cfg = {}
            timing = cfg.get("timing")
            if not isinstance(timing, dict):
                timing = {}
                cfg["timing"] = timing
            timing["photogate_latency_ms"] = new_val
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._emit("", "ERROR", "写入 photogate 配置失败: {}".format(e))
            return
        self._emit("", "CAL", "photogate: {} → {} ms".format(old, new_val))

    def _reset_gate(self):
        self._apply_photogate(30)
        self._set_cal("photogate 已恢复为默认 30ms")

    # ---------- start / stop ----------
    def _bot_args(self, mode):
        if mode == "special":
            args = ["--mode", "special", "--special-song", str(self.special_song)]
        elif mode == "challenge":
            args = ["--mode", "main", "--difficulty", str(self.challenge_difficulty),
                    "--livemode", "challengelive", "--challenge-finish",
                    "exit" if self.challenge_exit_game else "home"]
        elif mode == "daily":
            args = ["--mode", "daily"]
        else:
            args = ["--mode", "main", "--difficulty", str(self.difficulty),
                    "--livemode", LIVE_MODE]
        if self.device_address:
            args += ["--device", str(self.device_address)]
        return args

    def start(self, mode="main"):
        """按按钮指定的模式拉起 bot。顶栏始终启动自由演出。"""
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showwarning("已在运行", "bot 已在运行,请先停止。")
            return
        self.songs_done = 0
        self.current_song = ""
        if getattr(self, "m_songs", None):
            self.m_songs.set("0 首")
        if getattr(self, "m_song", None):
            self.m_song.set("—")
        # 自绘按钮是 Canvas，点击不夺焦点 → <FocusOut> 不触发。
        # 若用户刚在 photogate 输入框里敲了新值还没回车，这里先落盘再写配置。
        if getattr(self, "gate_step", None):
            self.gate_step.commit()
        self._write_config()

        bot_args = self._bot_args(mode)

        if getattr(sys, "frozen", False):
            bot = os.path.join(BASE, "autodori.exe")
            if not os.path.exists(bot):
                messagebox.showerror("环境缺失", "找不到 {},请放在同目录".format(bot))
                return
            cmd = [bot] + bot_args
        else:
            if not os.path.exists(PYTHON):
                messagebox.showerror("环境缺失", "找不到 {}".format(PYTHON))
                return
            cmd = [PYTHON, SCRIPT] + bot_args
        # 每个元素都过一遍 str():命令里混进 int 会让 " ".join() 直接抛异常,
        # 表现为"点开始演出没反应、界面不报错"(踩过一次)。
        self._emit("", "INFO", ">>> " + " ".join(str(x) for x in cmd))
        # 隐藏 bot 子进程的控制台窗口(双保险)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self.started_at = time.time()
        self._idle_text = "运行中 · 请勿操作电脑"
        self._sync_run_state()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                self.q.put(line.rstrip("\n"))
        except Exception:
            pass
        self.q.put("__EOF__")

    # 「关键事件」模式只显示高价值事件:进程/初始化、选歌、开演、结算、生命耗尽、
    # 以及真正的错误(ERROR/CRITICAL/Traceback)。其余 DEBUG 噪声(指令流 wfF/wfT、
    # Adjust offset 校准、life check OCR、火罐数值、SurfaceOrientation 等)一律隐藏,
    # 与「全部输出」形成明显差异。
    _KEEP = (
        "MAA inited",
        "Mumu and MNT inited",
        "Save song",
        "Start play",
        "剩余",
        "无法识别挑战点数",
        ">>> ",
        "退出",
        "生命值耗尽",
        "演出失败",
        "提前结束",
        "游戏已退出",
        "脚本收尾",
        "强制停止",
        "Failed to init",
        "Traceback",
    )

    # 「关键事件」里仍然要显示的、但只以"错误级别"出现的标记(不靠行内关键词,
    # 而靠日志级别判断)。INFO/DEBUG 即使命中上面的 _KEEP 之外的词也隐藏。
    _LEVEL_KEEP = {"ERROR", "CRITICAL", "WARNING"}

    def _should_show(self, line, level=""):
        if level in self._LEVEL_KEEP:
            # 错误/严重/警告级别一律显示(它们是真正的关键事件)。
            # 但排除「Unknown type」这类谱面解析噪声警告——它们每次选歌都刷屏,
            # 对用户无意义,归入全部输出。
            if "Unknown type" in line:
                return False
            return True
        return any(m in line for m in self._KEEP)

    def _poll_log(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__EOF__":
                    self._on_finished()
                    continue
                # 设备扫描结果是 (tag, payload) 元组,不是日志文本
                if isinstance(line, tuple) and len(line) == 2 \
                        and line[0] == "__DEVICES__":
                    try:
                        self._on_devices_scanned(line[1])
                    except Exception as e:
                        self._emit("", "WARN",
                                   "处理设备扫描结果出错: %r" % (e,))
                    continue
                try:
                    self._handle_line(line)
                except Exception as e:
                    # 单行处理出错绝不致命:任何控件异常都不能中断轮询,
                    # 否则之后的歌名/日志都不再更新(打歌中途歌名卡死的根因)。
                    self._log_buf.append(("", "WARN",
                                          "处理日志行出错(已跳过): %r" % (e,)))
        except queue.Empty:
            pass
        # 「是否在运行」以**进程存活**为准,不能只等 stdout 的 EOF:
        # bot 是 PyInstaller onefile,它会派生子进程;只要还有子进程持有管道的
        # 写端,读线程就永远收不到 EOF —— 界面会一直停在「运行中 · 请勿操作电脑」,
        # 观感上就是「游戏退出了但脚本没停」。这里用 poll() 兜住这种情况。
        if self.proc is not None and self.proc.poll() is not None:
            self._emit("", "INFO", "bot 进程已退出(界面状态已复位)")
            self._on_finished()
        self.root.after(120, self._poll_log)

    def _handle_line(self, line):
        # 原始行一律进全量缓冲(含时间戳/级别/logger),供导出日志使用。
        self._raw_log.append(line)
        m = LOG_RE.match(line)
        if m:
            stamp, level, msg = m.group(1), m.group(2), m.group(3)
            # 一条 ERROR/CRITICAL 记录的正文之后可能紧跟整段 traceback —— 它是
            # 逐行写到管道的裸文本(无时间戳/级别),直到下一条日志记录出现为止。
            self._in_traceback = level in ("ERROR", "CRITICAL")
        else:
            # 无时间戳的非标准行(如 bot 子进程崩溃时的裸输出)用当前时刻兜底,
            # 保证每条日志都带上「HH:MM:SS」,不再出现时间栏空白。
            stamp, level, msg = time.strftime("%H:%M:%S"), "", line.strip()
            if self._in_traceback and line.strip():
                # traceback 的续行承载着真正有用的文件/行号/异常类型,必须一并
                # 显示,否则「关键事件」里只剩一句光秃秃的报错。缩进保留。
                self._emit(stamp, "ERROR", line.rstrip())
                return
        if not msg:
            return

        # 选曲确认后立即更新「当前曲目」并打一条带时间戳的选歌日志。
        # bot 侧 save_song() 已把日志提前到谱面解算之前并升为 INFO,因此歌名
        # 会在选曲完成的一刻到达;两条正则互为兜底,任一行到达都算选曲完成。
        song = SONG_RE.search(msg)
        if not song:
            song = PLAY_RE.search(msg)  # 兜底:「打歌: {歌名}」INFO 行
        if song:
            name = song.group(1).strip()
            if name and name != self.current_song:
                self.current_song = name
                m = getattr(self, "m_song", None)
                # 视图重建窗口里 m_song 可能已被销毁,set 会抛 TclError;
                # 此时只更新状态,重建后的卡片会用 current_song 渲染。
                if m is not None:
                    try:
                        if m.winfo_exists():
                            m.set(self._fmt_song(self.current_song))
                    except Exception:
                        pass
                self._emit(stamp, "INFO", "选曲 · {}".format(name))

        data = _parse_play_result(line)
        if data is not None:
            self._on_play_result(data)
            return
        if "打歌中生命值耗尽" in msg:
            self._on_life_exhausted()
            self._emit(stamp, "WARN", msg)
            return
        if self._should_show(line, level):
            if "CRITICAL" in line:
                lvl = "ERROR"
            elif "WARNING" in line:
                lvl = "WARN"
            elif level == "INFO":
                lvl = "INFO"
            else:
                lvl = "DEBUG"
            self._emit(stamp, lvl, msg)

    def _tick(self):
        if self.started_at is not None:
            m = getattr(self, "m_time", None)
            if m is not None:
                try:
                    if m.winfo_exists():
                        secs = int(time.time() - self.started_at)
                        m.set("%02d:%02d:%02d" % (secs // 3600, secs // 60 % 60,
                                                  secs % 60))
                except Exception:
                    pass
        self.root.after(1000, self._tick)

    def _sync_run_state(self):
        th = T.get()
        running = self.proc is not None and self.proc.poll() is None
        if getattr(self, "start_btn", None):
            self.start_btn.set_enabled(not running)
        if getattr(self, "stop_btn", None):
            self.stop_btn.set_enabled(running)
        if getattr(self, "special_start_btn", None):
            try:
                if self.special_start_btn.winfo_exists():
                    self.special_start_btn.set_enabled(not running)
            except Exception:
                pass
        if getattr(self, "challenge_start_btn", None):
            try:
                if self.challenge_start_btn.winfo_exists():
                    self.challenge_start_btn.set_enabled(not running)
            except Exception:
                pass
        if getattr(self, "daily_start_btn", None):
            try:
                if self.daily_start_btn.winfo_exists():
                    self.daily_start_btn.set_enabled(not running)
            except Exception:
                pass
        if getattr(self, "run_dot", None):
            self.run_dot.set(th["ok"] if running else th["idle"])
        if getattr(self, "run_state", None):
            self.run_state.configure(text="运行中" if running else "未运行")
        if getattr(self, "run_hint", None):
            self.run_hint.configure(
                text="正在自动演出,请勿操作电脑" if running
                else "选择模式并点击对应的开始按钮")
        if getattr(self, "status_dot", None):
            self.status_dot.set(th["ok"] if running else th["idle"])
        if getattr(self, "status_text", None):
            self.status_text.configure(
                text="运行中 · 请勿操作电脑" if running else self._idle_text)

    def _on_finished(self):
        self.proc = None
        self.started_at = None
        self._idle_text = "已停止"
        self._sync_run_state()

    def _kill_proc(self):
        """杀掉 bot 进程的整个进程树。

        pyinstaller onefile exe 会派生子进程,只用 terminate()/kill() 只能杀父进程,
        子进程会残留继续打歌。用 taskkill /f /t 按 PID 杀整棵树。
        """
        if self.proc is None:
            return
        try:
            subprocess.run(
                ["taskkill", "/f", "/t", "/pid", str(self.proc.pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def stop(self):
        self._kill_proc()
        self._on_finished()
        self._emit("", "WARN", "=== 手动停止 ===")

    def _on_close(self):
        # 先置关闭标记:正在跑的设备扫描线程据此尽快收手,不再派 adb 子进程。
        # 不这么做的话,关窗口时解释器会等这些子进程,表现为"点了关闭没反应"。
        self._closing = True
        # 关窗口时强制结束 bot 整个进程树,避免它留在后台继续点游戏
        self._kill_proc()
        self.root.destroy()

    # ---------- 模拟器实例选择（多开支持）----------

    # 不写「推荐」:多开时自动判定并不能保证选对(包名分不出实例),
    # 标成推荐会误导用户以为不用管。
    AUTO_DEVICE_LABEL = "自动"
    # 状态文字与下拉框的文字左缘对齐所需的左内边距(实机截图揪出这处错位)。
    # 实测标定(不是推算):pack(padx=N) 后 Label 文字落在 左缘+N+1,
    # DropdownBox._draw 用 `create_text(12, ..., anchor="w")`,框内文字落在
    # 左缘+12。实测 N=11 时仍差 11px,故取 N+1 == 12 → N=11 已经是全量。
    # 下面这两行由 _chk_align6.py 复核,改 Label 样式时务必重跑。
    _DEVICE_TEXT_PADX = 11
    _DEVICE_TEXT_ORIGIN = 12  # DropdownBox._draw 里 create_text 的 x

    def _scan_devices_async(self):
        """后台线程扫设备,完成后回主线程刷新下拉框。

        扫描要跑 adb 子进程(最多十几个 connect + 每个设备三条 shell),
        放主线程会把界面卡住几秒 —— Tk 单线程,卡住就没法重绘。

        线程是 daemon,但它持有的 adb 子进程在 interpreter 退出时可能让
        进程挂着不退(实测关闭窗口会卡住)。所以关窗口时置 _closing,
        让线程体尽快收手别再派新的子进程。
        """
        if self._device_scanning or getattr(self, "_closing", False):
            return
        self._device_scanning = True
        if getattr(self, "device_status", None):
            self.device_status.configure(text="正在扫描模拟器实例…")

        def work():
            try:
                # 传入手填路径(空串则自动发现)
                result = device_scan.discover(adb=self.adb_path)
                # 多开邦邦窗口时,包名/进程状态对每个实例都一样,光看文字
                # 分不出谁是谁 —— 抓一张缩略图让用户**亲眼**确认。
                # 并发抓:串行的话每个实例一张图要等好几秒。
                devs = result.get("devices", [])
                shots = {}
                if devs and not getattr(self, "_closing", False):
                    import threading as _th
                    lock = _th.Lock()

                    def grab(dev):
                        if getattr(self, "_closing", False):
                            return
                        try:
                            path = device_scan.capture(
                                result.get("adb", ""), dev.get("address", ""))
                            if path:
                                with lock:
                                    shots[dev["address"]] = path
                        except Exception:
                            pass

                    ts = []
                    for dev in devs:
                        t = _th.Thread(target=grab, args=(dev,), daemon=True)
                        t.start()
                        ts.append(t)
                    for t in ts:
                        t.join(timeout=20)
                result["shots"] = shots
            except Exception as e:
                result = {"adb": "", "devices": [], "shots": {}, "error": str(e)}
            if getattr(self, "_closing", False):
                return  # 窗口已关,别再往已销毁的队列里灌结果
            self.q.put(("__DEVICES__", result))

        threading.Thread(target=work, daemon=True).start()

    def _on_devices_scanned(self, result):
        """扫描回调(主线程)。"""
        self._device_scanning = False
        self._devices = result.get("devices") or []
        self._shots = result.get("shots") or {}
        self._device_labels = [device_scan_label(d) for d in self._devices]

        if not self._devices:
            # 一次都扫不到:下拉框必须重建,否则上一次扫到的实例还留在列表里,
            # 用户会选到一个已经不在线的窗口(比「选不到」更危险)。
            self._refresh_device_box()
            msg = ("未发现正在运行的模拟器实例 — 请先启动 MuMu,再点「刷新」"
                   if not result.get("adb")
                   else "未发现在线的模拟器实例 — 请确认 MuMu 已启动,再点「刷新」")
            if getattr(self, "device_status", None):
                self.device_status.configure(text=msg)
            return

        # 校验已保存的选择是否还在候选里(用户可能改了实例/重装了模拟器)
        if self.device_address and not any(
                d.get("address") == self.device_address for d in self._devices):
            self.device_address = ""
            self._save_gui_config()

        # 只报「客观事实」,不替用户断言哪个是邦邦 —— 多开常是克隆镜像,
        # 每个实例都装了同一个游戏,包名/进程状态完全一致,据此宣称"这个是
        # 邦邦"是误导(实测踩过)。用户看端口对得上自己的窗口即可。
        n = len(self._devices)
        # 状态行只报客观的「实例数量」,不再复述「装了邦邦」:多开克隆镜像时
        # 每台都装邦邦,这句话退化成零信息量;而单机同时装多个音游时又容易
        # 被读成「已判定该选哪台」。该选哪台靠端口 + 「预览」按钮自己看。
        if getattr(self, "device_status", None):
            if self.device_address:
                self.device_status.configure(
                    text="已选择 %s — 启动时按此实例连接"
                         % self._label_of(self.device_address))
            elif n == 1:
                self.device_status.configure(
                    text="发现 1 个实例 — 保持「自动」即可")
            else:
                self.device_status.configure(
                    text="发现 %d 个实例 — 点「预览」逐个看画面,按端口指定" % n)
        self._refresh_device_box()

    def _label_of(self, address):
        for dev, label in zip(self._devices, self._device_labels):
            if dev.get("address") == address:
                return label
        return address

    def _refresh_device_box(self):
        """按当前扫描结果重建下拉框选项。

        DropdownBox 没有 set_values,只能整体重建 —— 所以把它的父容器
        一起清掉再放一个新的(控件是纯展示,状态真值在 self.device_address)。
        """
        box = getattr(self, "device_box", None)
        if box is None:
            return
        parent = box.master
        try:
            box.destroy()
        except Exception:
            pass
        values = [self.AUTO_DEVICE_LABEL] + list(self._device_labels)
        current = self.AUTO_DEVICE_LABEL
        if self.device_address:
            current = self._label_of(self.device_address)
            if current not in values:
                # 上次选的实例这次没扫到(比如它没启动)——仍保留这一项,
                # 免得用户的选择被静默改掉(那等于换了个窗口接着点)。
                values.append(current)
        new_box = W.DropdownBox(
            parent, values, value=current, width=400, min_width=300,
            surface="surface", on_change=self._on_device)
        # pack 的默认行为是把控件**追加到父容器子控件列表末尾**。本行左边还
        # 有两个 IconButton(它们先创建),若直接 pack,重建后的下拉框会跑到
        # 按钮右侧 —— 表现为「框和按钮换了位置」。before=first 把它钉回第一位。
        try:
            first = parent.pack_slaves()[0]
        except Exception:
            first = None
        if first is not None and first is not new_box:
            new_box.pack(side="left", before=first)
        else:
            new_box.pack(side="left")
        self.device_box = new_box

    def _on_device(self, value):
        """下拉框选择变化:存回状态并落盘。

        注意 DropdownBox 选中项变了但**不会**回调自己(只有用户点菜单才会),
        这里也不需要重建下拉框 —— 它的 _index 已经由 _pick 改好了。
        """
        if value == self.AUTO_DEVICE_LABEL:
            self.device_address = ""
        else:
            self.device_address = ""
            for dev, label in zip(self._devices, self._device_labels):
                if label == value:
                    self.device_address = dev.get("address", "")
                    break
        self._save_gui_config()
        if getattr(self, "device_status", None):
            self.device_status.configure(
                text=("已选择 %s — 启动时按此实例连接"
                      % self._label_of(self.device_address))
                if self.device_address else "自动：脚本自行选择实例")

    def _device_row(self, master):
        """自由演出设置卡片里的「模拟器实例」一行。"""
        th = T.get()
        r = W.Row(master, "模拟器实例")
        r.pack(fill="x", pady=(0, 6))

        # 没扫到设备时也显示「自动识别」,保证用户永远看得到当前策略
        values = [self.AUTO_DEVICE_LABEL] + list(self._device_labels)
        current = self.AUTO_DEVICE_LABEL
        if self.device_address:
            current = self._label_of(self.device_address)
            if current not in values:
                values.append(current)
        # 框与两个按钮放进同一个横向容器 —— 关键:**不能**把 state 用的
        # fill="x" 标签与 side="left" 控件混在同一个父容器里。Tk 的 pack 按
        # **调用顺序**切空间:side="left" 的先把横向可用区占满,后调用的
        # fill="x" 只能被挤到它们上方一行 —— 表现为「状态文字浮在控件上面」,
        # 整行错位。嵌套一层容器后,两行各自独立 pack,不会互相抢空间。
        line = tk.Frame(r.slot, bg=th["surface"])
        line.pack(fill="x")

        box = W.DropdownBox(
            line, values, value=current, width=400, min_width=300,
            surface="surface", on_change=self._on_device)
        # 不再 fill="x"/expand —— 那一版会把方框拉满整行,视觉上过于抢眼,
        # 且标签文字只有几十个字符,撑开只是浪费空白。定宽即可。
        # anchor="center":下拉框(28px)与两个图标按钮(30px)高度不同,顶对齐
        # 会让框体上缘齐平而中轴偏低 —— 居中后三者共用同一水平中轴线。
        box.pack(side="left", anchor="center")
        self.device_box = box

        btn = W.IconButton(line, "refresh", size=30,
                           command=self._scan_devices_async,
                           tip="重新扫描模拟器实例")
        btn.pack(side="left", padx=(8, 0), anchor="center")
        self.device_refresh_btn = btn

        # 预览按钮:多开时包名/进程状态分不出实例,只能让用户看一眼画面
        prev = W.IconButton(line, "live", size=30,
                            command=self._preview_device,
                            tip="预览所选实例的画面")
        prev.pack(side="left", padx=(6, 0), anchor="center")
        self.device_preview_btn = prev

        # 状态文字**不**放 slot 内:它比框体多一整行高度,留在 slot 里会把
        # slot 撑高,Row 的垂直居中就会拿「框+状态」的总高去对标签,
        # 标签落在框与状态之间、反而对不齐框。挪到 Row 外面独立成行后,
        # slot 只剩框体那一行,标签即可与框精确同一中轴线。
        self.device_status = tk.Label(
            master, text="", bg=th["surface"], fg=th["text_3"],
            font=T.font(self.font_size - 1), anchor="w", justify="left",
            wraplength=760,   # 状态文字较长时不越出卡片右边界（实测截图里被截断）
        )
        # padx 见 _DEVICE_TEXT_PADX 的说明：让状态文字与框内文字左缘对齐。
        # 右侧留 11px 余量 —— 只加左边距的话，右对齐的文字会顶出卡片边界。
        self.device_status.pack(fill="x", pady=(4, 0),
                                padx=(self._DEVICE_TEXT_PADX,
                                      self._DEVICE_TEXT_PADX))
        if not self._devices:
            self.device_status.configure(
                text="点右侧按钮扫描实例 — 多开时在此指定连哪台")

        return r

    def _preview_device(self):
        """弹出所选实例的当前画面,让用户确认「这个端口就是我要的窗口」。

        多开邦邦窗口时,包名/进程状态对每个实例完全一致,唯一可靠的区分
        办法就是让用户自己看一眼画面(是不是在选曲页、是不是目标账号)。
        """
        if not self._devices:
            messagebox.showinfo("模拟器实例", "请先点右侧按钮扫描实例。")
            return
        shots = getattr(self, "_shots", {}) or {}
        # 没选具体实例时,预览第一个装了邦邦的;否则预览选中的
        address = self.device_address
        if not address:
            for dev in self._devices:
                if dev.get("running") is True:
                    address = dev.get("address", "")
                    break
        if not address:
            address = self._devices[0].get("address", "")

        path = shots.get(address)
        if not path or not os.path.isfile(path):
            messagebox.showwarning(
                "无法截图",
                "没能抓到该实例的画面。\n\n"
                "可能原因:实例未启动、或 adb 连接已断开。\n"
                "请确认 MuMu 正常运行后点「刷新」重试。")
            return
        try:
            img = tk.PhotoImage(file=path)
        except Exception as e:
            messagebox.showerror("无法预览", "读取截图失败: {}".format(e))
            return

        win = tk.Toplevel(self.root)
        win.title("实例预览 — {}".format(address))
        th = T.get()
        win.configure(bg=th["app_bg"])
        # 截图是 1280x720,按窗口可用宽度等比缩小(不依赖 PIL)
        max_w, max_h = 720, 405
        ratio = min(max_w / img.width(), max_h / img.height(), 1.0)
        w, h = max(1, int(img.width() * ratio)), max(1, int(img.height() * ratio))
        shown = img.subsample(
            max(1, int(img.width() / w) or 1), max(1, int(img.height() / h) or 1)
        )
        lbl = tk.Label(win, image=shown, bg=th["app_bg"], bd=0)
        lbl.image = shown  # 保持引用,否则会被回收成空白
        lbl.pack(padx=12, pady=(12, 6))
        tk.Label(
            win,
            text="端口 {}　·　这是该实例当前的画面".format(address),
            bg=th["app_bg"], fg=th["text_3"],
            font=T.font(self.font_size - 1),
        ).pack(pady=(0, 12))
        win.resizable(False, False)

    def _write_config(self):
        life = "auto" if self.life_mode == "自动退出重新选歌" else "wait"
        play_at_zero = self.boost_mode == "继续打歌"
        cfg = {
            "timing": {"photogate_latency_ms": self.gate},
            "on_life_exhausted": life,
            "play_at_zero_boost": play_at_zero,
            "song_strategy": STRATEGY_TO_CFG.get(self.song_strategy, "mine"),
            # 活动曲目也落一份到运行配置:直接手跑
            # `python src/autodori.py --mode special` 时能沿用界面里选的那首
            "special_song": str(self.special_song),
            # 一键清理日常(见 Issue #4):bot 实时读 data/config.yml 的 daily.*。
            # 三抽为固定3次(游戏内置每日免费上限);弹窗赠送领取并入启动流程。
            "daily": {
                "enabled": bool(self.daily_enabled),
                "auto_clear_fire": bool(self.daily_auto_clear_fire),
                "auto_free_pull": bool(self.daily_auto_free_pull),
            },
        }
        try:
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            # 合并写入,不要整份覆盖:data/config.yml 里还有 GUI 界面上没有、
            # 但 bot 会实时读取的键(如 stop_when_game_exits、song_strategy_reject_limit、
            # device.filter 等)。整份重写会在每次点「开始演出」时把它们抹掉,
            # 让用户手改的设置悄悄失效。
            merged = {}
            try:
                with open(CONFIG, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    merged = old
            except Exception:
                merged = {}
            # timing 也要逐键合并,别丢掉用户加的其他 timing 项
            old_timing = merged.get("timing")
            if isinstance(old_timing, dict):
                timing = dict(old_timing)
                timing.update(cfg["timing"])
                cfg["timing"] = timing
            merged.update(cfg)
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._emit("", "ERROR", "写入配置失败: {}".format(e))


def main():
    root = tk.Tk()
    AutodoriGUI(root)
    # Windows 下新开的 Tk 窗口若不主动获得焦点,第一次点击只激活窗口、
    # 不触发按钮(表现为"首次点开始演出没反应")。等窗口映射后提一前到前台并抢焦点。
    root.after(150, lambda: (root.lift(), root.focus_force()))
    root.mainloop()


if __name__ == "__main__":
    main()

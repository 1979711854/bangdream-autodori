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

# 演出模式仅支持自由演出,禁止协力模式(challengelive)
LIVE_MODE = "freelive"
DIFFICULTIES = ["easy", "normal", "hard", "expert", "special"]
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
APP_VERSION = "1.2.0"

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
        ("live.show", "演出设置", "live"),
        ("live.gate", "时基校准", "settings"),
    )),
    ("运行", (
        ("logs", "运行日志", "logs"),
    )),
    ("说明", (
        ("docs.notes", "注意事项", "docs"),
        ("docs.preread", "用前必读", "docs"),
        ("docs.faq", "常见问题", "docs"),
    )),
    ("设置", (
        ("settings.ui", "界面", "settings"),
        ("settings.about", "关于", "settings"),
    )),
)
VIEW_KEYS = [k for _, items in OPTION_TREE for k, _, _ in items]

# 底部状态栏环境信息
ENV_INFO = ("自由演出 · freelive", "MuMu Player 12", "1280 × 720")


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
• 演出模式仅支持自由演出(freelive),不支持协力模式
• 选曲列表设为"正常",清空歌曲筛选器
• 演出设定:将流速调整为 8.0
• 演出效果·音量设定:关闭"3D切入模式","动作模式"改为"轻量模式"
• 演出效果·音量设定:启用"FAST/SLOW表示"和"Perfect状态显示"

【使用提醒】
• 本脚本用于自动挖矿(自动打歌刷取资源/活动奖励),仅供个人使用
• 请遵守游戏规则与用户协议,不要用于破坏游戏秩序或影响其他玩家
• 不要同时运行多个实例
• 如有问题,先查看 debug 目录下的日志
"""


# 常见问题(只读)
FAQ = """Q:打歌总是 FAST(狂爆 FAST / 整体按早),怎么办?
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
A:代码本身暂不支持直接指定某首歌,但可以手动把想打的歌加入游戏内的收藏,让脚本只从收藏里随机选,相当于只打那一首。
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
        self.boost_mode = cfg.get("boost_mode", "继续打歌")
        self.life_mode = cfg.get("life_mode", "自动退出重新选歌")
        self.auto_cal = bool(cfg.get("auto_cal", False))
        self.song_strategy = cfg.get("song_strategy", "挖矿为主")
        if self.song_strategy not in SONG_STRATEGIES:
            self.song_strategy = "挖矿为主"
        self.gate = self._read_gate()

        T.set_theme(self.theme)
        W.set_base_size(self.font_size)
        self._build()
        self.root.geometry(self.window_size)
        self._sync_run_state()

        self.root.after(120, self._poll_log)
        self.root.after(500, self._tick)
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
            "boost_mode": self.boost_mode,
            "life_mode": self.life_mode,
            "auto_cal": bool(self.auto_cal),
            "song_strategy": self.song_strategy,
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
        self.start_btn = W.PushButton(bar, "开始演出", command=self.start,
                                      kind="primary", width=112, height=34)
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
        self.run_hint = tk.Label(head, text="配置完成后点击右上角「开始演出」",
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

        tk.Label(card.body, text="提示:若「开始演出」首次点击无反应,关闭窗口重新打开一次即可",
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
        if self.view == "live.gate":
            return self._detail_gate(master)
        if self.view == "logs":
            return self._detail_logs(master)
        if self.view in DOCS:
            return self._detail_docs(master)
        if self.view == "settings.ui":
            return self._detail_ui(master)
        return self._detail_about(master)

    def _detail_show(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "演出设置", "仅支持自由演出").pack(
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

    def _detail_about(self, master):
        th = T.get()
        card = W.Card(master)
        W.SectionTitle(card.body, "关于").pack(fill="x", pady=(0, 10))
        for k, v in (
            ("项目", "BanG Dream · 邦邦自动挖矿助手"),
            ("版本", "v" + self._version()),
            ("演出模式", "自由演出 (freelive)"),
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
        item = (stamp, level, message)
        self._log_buf.append(item)
        console = getattr(self, "full_log", None)
        if console is not None:
            console.line(*item)

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
        if getattr(self, "m_songs", None):
            self.m_songs.set("%d 首" % self.songs_done)
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
        if getattr(self, "cal_label", None):
            self.cal_label.configure(text=text)

    def _apply_photogate(self, new_val):
        """写新 photogate 到 config(保留 life/boost 设置),并更新界面。

        单独改写 timing 字段,不复用 _write_config,避免覆盖用户已选的其他策略。
        """
        old = self.gate
        self.gate = new_val
        if getattr(self, "gate_step", None):
            self.gate_step.set(new_val)
        if getattr(self, "status_gate", None):
            self.status_gate.configure(text="photogate %d ms" % self.gate)
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
    def start(self):
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

        if getattr(sys, "frozen", False):
            bot = os.path.join(BASE, "autodori.exe")
            if not os.path.exists(bot):
                messagebox.showerror("环境缺失", "找不到 {},请放在同目录".format(bot))
                return
            cmd = [bot, "--mode", "main", "--difficulty", self.difficulty,
                   "--livemode", LIVE_MODE]
        else:
            if not os.path.exists(PYTHON):
                messagebox.showerror("环境缺失", "找不到 {}".format(PYTHON))
                return
            cmd = [PYTHON, SCRIPT, "--mode", "main",
                   "--difficulty", self.difficulty, "--livemode", LIVE_MODE]
        self._emit("", "INFO", ">>> " + " ".join(cmd))
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
        ">>> ",
        "退出",
        "生命值耗尽",
        "演出失败",
        "提前结束",
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
                self._handle_line(line)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    def _handle_line(self, line):
        # 原始行一律进全量缓冲(含时间戳/级别/logger),供导出日志使用。
        self._raw_log.append(line)
        m = LOG_RE.match(line)
        if m:
            stamp, level, msg = m.group(1), m.group(2), m.group(3)
        else:
            stamp, level, msg = "", "", line.strip()
        if not msg:
            return

        song = SONG_RE.search(msg)
        if not song:
            song = PLAY_RE.search(msg)  # 兜底:「打歌: {歌名}」INFO 行
        if song:
            self.current_song = song.group(1).strip()
            if getattr(self, "m_song", None):
                self.m_song.set(self._fmt_song(self.current_song))

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
        if self.started_at is not None and getattr(self, "m_time", None):
            secs = int(time.time() - self.started_at)
            self.m_time.set("%02d:%02d:%02d" % (secs // 3600, secs // 60 % 60,
                                                secs % 60))
        self.root.after(1000, self._tick)

    def _sync_run_state(self):
        th = T.get()
        running = self.proc is not None and self.proc.poll() is None
        if getattr(self, "start_btn", None):
            self.start_btn.set_enabled(not running)
        if getattr(self, "stop_btn", None):
            self.stop_btn.set_enabled(running)
        if getattr(self, "run_dot", None):
            self.run_dot.set(th["ok"] if running else th["idle"])
        if getattr(self, "run_state", None):
            self.run_state.configure(text="运行中" if running else "未运行")
        if getattr(self, "run_hint", None):
            self.run_hint.configure(
                text="正在自动演出,请勿操作电脑" if running
                else "配置完成后点击右上角「开始演出」")
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
        # 关窗口时强制结束 bot 整个进程树,避免它留在后台继续点游戏
        self._kill_proc()
        self.root.destroy()

    def _write_config(self):
        life = "auto" if self.life_mode == "自动退出重新选歌" else "wait"
        play_at_zero = self.boost_mode == "继续打歌"
        cfg = {
            "timing": {"photogate_latency_ms": self.gate},
            "on_life_exhausted": life,
            "play_at_zero_boost": play_at_zero,
            "song_strategy": STRATEGY_TO_CFG.get(self.song_strategy, "mine"),
        }
        try:
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
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

# -*- coding: utf-8 -*-
"""autodori 邦邦小助手 - GUI 启动器

选择难度/火罐/photogate/生命值耗尽策略,启动源码版 bot 并显示实时日志。
分栏:主界面 / 设置(GUI外观)/ 注意事项。
打包: pyinstaller --onefile --windowed --name autodori_gui gui.py
"""
import ast
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

# 打包版:所有文件(bot.exe/assets/data)都在 exe 所在目录;
# 源码版:项目根目录(含 src/ .venv/ assets/ data/)。
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = r"E:\autodori-src"
PYTHON = os.path.join(BASE, ".venv", "Scripts", "python.exe")
SCRIPT = os.path.join(BASE, "src", "autodori.py")
CONFIG = os.path.join(BASE, "data", "config.yml")
GUI_CONFIG = os.path.join(BASE, "data", "gui_config.json")

# 演出模式仅支持自由演出,禁止协力模式(challengelive)
LIVE_MODE = "freelive"
DIFFICULTIES = ["easy", "normal", "hard", "expert", "special"]
WINDOW_SIZES = ["600x460", "640x480", "680x520", "720x550",
                "760x580", "860x620", "960x680", "1024x700", "1280x800"]

# photogate 自动校准参数(见 _calibrate_gate)
CAL_STEP_MS = 10        # 每次校准步长(ms)
CAL_MIN_DIFF = 3        # |FAST-SLOW| 低于此值视为信号弱,不调整
CAL_RANGE = (0, 150)    # photogate 允许范围(ms)


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
    # 经验系数 40ms/100%:2% 歪→步长 ~1ms 精调,10%→~4ms,30%→~10ms(封顶)。
    total = perfect + great + good + bad + miss
    off_ratio = (fast + slow) / max(total, 1)
    step = max(1, min(CAL_STEP_MS, round(off_ratio * 40)))

    direction = 1 if bias > 0 else -1
    new_ms = current_ms + direction * step
    return max(CAL_RANGE[0], min(CAL_RANGE[1], new_ms))

# 注意事项内容(只读)
NOTES = """【模拟器设置】
\u2022 使用 MuMu Player 12,分辨率 1280x720,Vulkan 渲染
\u2022 保持高帧率,不要限制 30fps(会破坏打歌同步)
\u2022 打歌期间尽量不要操作电脑,避免性能波动

【游戏设置】
\u2022 游戏:邦邦国服(bilibili)
\u2022 演出模式仅支持自由演出(freelive),不支持协力模式
• 选曲列表设为"正常",清空歌曲筛选器
• 演出设定:将流速调整为 8.0
• 演出效果·音量设定:关闭"3D切入模式","动作模式"改为"轻量模式"
• 演出效果·音量设定:启用"FAST/SLOW表示"和"Perfect状态显示"

【使用提醒】
\u2022 本脚本用于自动挖矿(自动打歌刷取资源/活动奖励),仅供个人使用
\u2022 请遵守游戏规则与用户协议,不要用于破坏游戏秩序或影响其他玩家
\u2022 不要同时运行多个实例
\u2022 如有问题,先查看 debug 目录下的日志
"""


# 常见问题(只读)
FAQ = """Q:为什么无法正常打歌?
A:请查看\"注意事项\"和 README.md,检查游戏和模拟器设置是否正确。

Q:为什么有些歌会爆很多 GREAT 和 MISS?
A:个别歌曲难度较大,机器识别可能存在一定延迟;
模拟器长时间运行后发热/内存占用上升,触控输入延迟波动变大,精度下降。
建议:偶尔重启模拟器、保证电脑不过热、保持高帧率。

如果几乎每首都 GREAT 偏多(而不是个别难歌),通常是 photogate(打歌时基)没对准,需要调节:
· 最简单:打开主界面「自动校准 photogate」,正常打几首歌,脚本会根据每首的
  FAST/SLOW 分布自动微调,几首内收敛,不用自己算;
· 也可以手动改 GUI 里的 photogate 数值:结算页 GREAT 偏 SLOW(按晚)就减小,
  偏 FAST(按早)就增大,每次调 10ms 左右,范围建议 0~150ms。

Q:使用脚本有封号的风险吗?
A:存在封号的可能性,但只要不用于冲榜,封号的概率就不大。

Q:我发现了 BUG?
A:可以反馈到 GitHub Issues。

Q:如何指定打某一首歌?
A:代码本身暂不支持直接指定某首歌,但可以手动把想打的歌加入游戏内的「收藏」,让脚本只从收藏里随机选,相当于只打那一首。
"""


# 用前必读:photogate 校准说明(只读)
PRE_READ = """【photogate 是什么】
bot 以"光闸"确定打歌起点:第一个音符进入屏幕检测带时按下秒表,
再等 photogate(毫秒)后开始整首歌。
这个值代表"音符从检测带到判定线的耗时",还和每台电脑的截屏/触控延迟有关,
所以【每台机器的最佳值不一样】,默认 30 不一定适合你。

【怎么知道没对准】
先把"注意事项"里的设置都配好(流速 8.0 / 分辨率 1280x720 / 高帧率)。
正常打歌应基本全 PERFECT。
如果几乎每首 GREAT 都偏多(而不是个别难歌),就是 photogate 没对准。

【最简单:自动校准】
勾选主界面「自动校准 photogate」,正常打几首歌。
脚本会读每首结算的 FAST/SLOW 分布自动微调,几首内收敛,不用自己算。
每首结算后日志会显示一行「判定: PERFECT … · GREAT …(FAST x/SLOW y)」,
若偏 FAST/SLOW,状态栏会出现"自动校准: photogate 30 → 40 …"。

【也可以手动调】
主界面 photogate 数值:
· GREAT 偏 SLOW(按晚了)→ 减小
· GREAT 偏 FAST(按早了)→ 增大
每次调 10ms 左右,范围建议 0~150ms。
提示:(SLOW偏多则减小,FAST偏多则增大)

【注意】
· 自动校准值会在下一首歌开始前生效,无需重启;
· 校准和每台机器绑定,换模拟器/电脑后建议重新校准;
· 打歌期间别动电脑,性能波动也会造成 GREAT。
"""


class AutodoriGUI:
    def __init__(self, root):
        self.root = root
        root.title("autodori · BanG Dream · 邦邦自动挖矿助手")
        root.minsize(560, 400)

        self.proc = None
        self.q = queue.Queue()
        self._style = ttk.Style()

        # 读取 GUI 偏好设置
        self.gui_cfg = self._load_gui_config()
        self.font_size = int(self.gui_cfg.get("font_size", 10))
        self.window_size = self.gui_cfg.get("window_size", "640x480")

        self._build_ui()
        self._apply_gui_settings()
        self.root.after(100, self._poll_log)
        # 关闭窗口时杀掉 bot 子进程,防止它在后台继续点游戏
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- GUI 偏好设置 ----------
    def _load_gui_config(self):
        try:
            with open(GUI_CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_gui_config(self):
        self.gui_cfg = {
            "font_size": int(self.font_var.get()),
            "window_size": self.win_var.get(),
        }
        os.makedirs(os.path.dirname(GUI_CONFIG), exist_ok=True)
        with open(GUI_CONFIG, "w", encoding="utf-8") as f:
            json.dump(self.gui_cfg, f, ensure_ascii=False, indent=2)

    def _apply_gui_settings(self):
        # 字体只影响控件/日志文字,不影响窗口尺寸(窗口由用户设定的分辨率决定)
        tkfont.nametofont("TkDefaultFont").configure(size=self.font_size)
        self._style.configure(".", font=("Microsoft YaHei UI", self.font_size))
        self.log.configure(font=("Consolas", self.font_size))
        self.note_text.configure(font=("Microsoft YaHei UI", self.font_size + 2))
        self.faq_text.configure(font=("Microsoft YaHei UI", self.font_size + 2))
        self.pre_text.configure(font=("Microsoft YaHei UI", self.font_size + 2))
        self.root.geometry(self.window_size)

    def _save_settings(self):
        self.font_size = int(self.font_var.get())
        self.window_size = self.win_var.get()
        self._save_gui_config()
        self._apply_gui_settings()
        self.status.configure(text="设置已保存")

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}

        header = ttk.Label(self.root, text="autodori · BanG Dream · 邦邦自动挖矿助手",
                           font=("Microsoft YaHei UI", 14, "bold"))
        header.pack(pady=(12, 4))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)

        # ===== 主界面 =====
        main_tab = ttk.Frame(self.nb)
        self.nb.add(main_tab, text="主界面")

        frame = ttk.LabelFrame(main_tab, text=" 设置 ", padding=12)
        frame.pack(fill="x", padx=14, pady=8)

        ttk.Label(frame, text="难度:").grid(row=0, column=0, sticky="w", **pad)
        self.diff_var = tk.StringVar(value="expert")
        ttk.Combobox(frame, textvariable=self.diff_var, values=DIFFICULTIES,
                     state="readonly", width=10).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(frame, text="演出模式:").grid(row=0, column=2, sticky="w", **pad)
        ttk.Label(frame, text="自由演出 (freelive)", foreground="#888").grid(
            row=0, column=3, sticky="w", **pad)

        ttk.Label(frame, text="火罐为0时:").grid(row=1, column=0, sticky="w", **pad)
        self.boost_var = tk.StringVar(value="继续打歌")
        ttk.Combobox(frame, textvariable=self.boost_var,
                     values=["继续打歌", "退出游戏"],
                     state="readonly", width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="photogate(ms):").grid(row=1, column=2, sticky="w", **pad)
        self.gate_var = tk.StringVar(value="30")
        ttk.Entry(frame, textvariable=self.gate_var, width=6).grid(row=1, column=3, sticky="w", **pad)

        ttk.Label(frame, text="生命值耗尽后:").grid(row=2, column=0, sticky="w", **pad)
        self.life_var = tk.StringVar(value="自动退出重新选歌")
        ttk.Combobox(frame, textvariable=self.life_var,
                     values=["自动退出重新选歌", "等待手动操作"],
                     state="readonly", width=16).grid(row=2, column=1, sticky="w", **pad)

        # photogate 提示(紧贴 photogate 下一行)与自动校准
        ttk.Label(frame, text="提示:(SLOW偏多则减小,FAST偏多则增大)",
                  foreground="#888").grid(row=3, column=2, columnspan=2, sticky="w", **pad)
        self.auto_cal_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="自动校准 photogate",
                        variable=self.auto_cal_var).grid(row=4, column=2, sticky="w", **pad)
        ttk.Button(frame, text="恢复默认 30", command=self._reset_gate).grid(
            row=4, column=3, sticky="w", **pad)
        self.cal_status = ttk.Label(frame, text="", foreground="#555")
        self.cal_status.grid(row=5, column=0, columnspan=4, sticky="w", **pad)

        btns = ttk.Frame(main_tab)
        btns.pack(fill="x", padx=10, pady=4)
        self.start_btn = ttk.Button(btns, text="开始打歌", command=self.start)
        self.start_btn.pack(side="left", **pad)
        self.stop_btn = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", **pad)
        self.status = ttk.Label(main_tab, text="就绪", foreground="#555")
        self.status.pack(side="top", anchor="w", padx=12)

        # ===== 设置(GUI外观)=====
        set_tab = ttk.Frame(self.nb, padding=16)
        self.nb.add(set_tab, text="设置")

        ttk.Label(set_tab, text="GUI 界面设置").grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(set_tab, text="字体大小:").grid(row=1, column=0, sticky="w", **pad)
        self.font_var = tk.StringVar(value=str(self.font_size))
        ttk.Spinbox(set_tab, from_=8, to=16, textvariable=self.font_var,
                    width=8).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(set_tab, text="窗口默认分辨率:").grid(row=2, column=0, sticky="w", **pad)
        self.win_var = tk.StringVar(value=self.window_size)
        ttk.Combobox(set_tab, textvariable=self.win_var, values=WINDOW_SIZES,
                     state="readonly", width=12).grid(row=2, column=1, sticky="w", **pad)

        ttk.Button(set_tab, text="保存设置", command=self._save_settings).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(set_tab, text="保存后立即生效,并在下次启动时保留",
                  foreground="#888").grid(row=4, column=0, columnspan=2, sticky="w", **pad)

        # ===== 日志(独立分栏,避免被窗口大小遮挡)=====
        log_tab = ttk.Frame(self.nb, padding=4)
        self.nb.add(log_tab, text="日志")
        self.log = tk.Text(log_tab, state="disabled", wrap="word",
                           font=("Consolas", self.font_size))
        log_scroll = ttk.Scrollbar(log_tab, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # ===== 注意事项 =====
        note_tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(note_tab, text="注意事项")

        self.note_text = tk.Text(note_tab, wrap="word",
                                 font=("Microsoft YaHei UI", self.font_size + 2),
                                 height=16, padx=8, pady=8)
        self.note_text.insert("1.0", NOTES)
        self.note_text.configure(state="disabled")
        note_scroll = ttk.Scrollbar(note_tab, command=self.note_text.yview)
        self.note_text.configure(yscrollcommand=note_scroll.set)
        self.note_text.pack(side="left", fill="both", expand=True)
        note_scroll.pack(side="right", fill="y")

        # ===== 用前必读(photogate 校准说明)=====
        pre_tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(pre_tab, text="用前必读")

        self.pre_text = tk.Text(pre_tab, wrap="word",
                                font=("Microsoft YaHei UI", self.font_size + 2),
                                height=16, padx=8, pady=8)
        self.pre_text.insert("1.0", PRE_READ)
        self.pre_text.configure(state="disabled")
        pre_scroll = ttk.Scrollbar(pre_tab, command=self.pre_text.yview)
        self.pre_text.configure(yscrollcommand=pre_scroll.set)
        self.pre_text.pack(side="left", fill="both", expand=True)
        pre_scroll.pack(side="right", fill="y")

        # ===== 常见问题 =====
        faq_tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(faq_tab, text="常见问题")

        self.faq_text = tk.Text(faq_tab, wrap="word",
                                font=("Microsoft YaHei UI", self.font_size + 2),
                                height=16, padx=8, pady=8)
        self.faq_text.insert("1.0", FAQ)
        self.faq_text.configure(state="disabled")
        faq_scroll = ttk.Scrollbar(faq_tab, command=self.faq_text.yview)
        self.faq_text.configure(yscrollcommand=faq_scroll.set)
        self.faq_text.pack(side="left", fill="both", expand=True)
        faq_scroll.pack(side="right", fill="y")

    # ---------- helpers ----------
    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _write_config(self):
        try:
            gate = int(self.gate_var.get())
        except ValueError:
            gate = 30
        life = "auto" if self.life_var.get() == "自动退出重新选歌" else "wait"
        play_at_zero = self.boost_var.get() == "继续打歌"
        cfg = {
            "timing": {"photogate_latency_ms": gate},
            "on_life_exhausted": life,
            "play_at_zero_boost": play_at_zero,
        }
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # ---------- photogate 自动校准 ----------
    def _on_play_result(self, data):
        """结算 OCR 到达:展示判定汇总;开启自动校准时按 FAST/SLOW 微调 photogate。"""
        self._log(
            "判定: PERFECT {} · GREAT {}(FAST {}/SLOW {}) · GOOD {}/BAD {}/MISS {}".format(
                data.get("perfect", -1),
                data.get("great", -1),
                data.get("fast", -1),
                data.get("slow", -1),
                data.get("good", -1),
                data.get("bad", -1),
                data.get("miss", -1),
            )
        )
        if not self.auto_cal_var.get():
            return
        try:
            cur = int(self.gate_var.get())
        except ValueError:
            cur = 30
        new_val = _calibrate_gate(data, cur)
        if new_val is None:
            self.cal_status.configure(
                text="本曲信号不足/无需调整,photogate 保持 {}".format(cur)
            )
            return
        if new_val == cur:
            self.cal_status.configure(
                text="photogate 已在边界 {},不再调整".format(cur)
            )
            return
        verb = "增大(偏FAST按早)" if new_val > cur else "减小(偏SLOW按晚)"
        self._apply_photogate(new_val)
        self.cal_status.configure(
            text="自动校准: photogate {} → {} ms · {}".format(cur, new_val, verb)
        )

    def _apply_photogate(self, new_val):
        """写新 photogate 到 config.yml(保留 life/boost 设置),并更新输入框。

        单独改写 timing 字段,不复用 _write_config,避免覆盖用户已选的其他策略。
        """
        old = self.gate_var.get()
        self.gate_var.set(str(new_val))
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
            self._log("写入 photogate 配置失败: {}".format(e))
            return
        self._log("photogate: {} → {} ms".format(old, new_val))

    def _reset_gate(self):
        self._apply_photogate(30)
        self.cal_status.configure(text="photogate 已恢复为默认 30ms")

    # ---------- start / stop ----------
    def start(self):
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showwarning("已在运行", "bot 已在运行,请先停止。")
            return
        diff = self.diff_var.get()
        self._write_config()
        if getattr(sys, "frozen", False):
            bot = os.path.join(BASE, "autodori.exe")
            if not os.path.exists(bot):
                messagebox.showerror("环境缺失", "找不到 {},请放在同目录".format(bot))
                return
            cmd = [bot, "--mode", "main", "--difficulty", diff, "--livemode", LIVE_MODE]
        else:
            if not os.path.exists(PYTHON):
                messagebox.showerror("环境缺失", "找不到 {}".format(PYTHON))
                return
            cmd = [
                PYTHON, SCRIPT,
                "--mode", "main",
                "--difficulty", diff,
                "--livemode", LIVE_MODE,
            ]
        self._log(">>> " + " ".join(cmd))
        # 隐藏 bot 子进程的控制台窗口(双保险)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=BASE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.configure(text="运行中...")
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                self.q.put(line.rstrip("\n"))
        except Exception:
            pass
        self.q.put("__EOF__")

    # 只在 GUI 里显示的关键事件/异常;其余 DEBUG 噪声(指令流、校准、OCR、火罐数值)不显示
    _KEEP = (
        "MAA inited",
        "Mumu and MNT inited",
        "Save song",
        "Start play",
        "First note",
        ">>> ",
        "WARNING",
        "ERROR",
        "CRITICAL",
        "Traceback",
        "退出",
        "生命值耗尽",
        "演出失败",
        "提前结束",
    )

    def _should_show(self, line):
        return any(m in line for m in self._KEEP)

    def _poll_log(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__EOF__":
                    self._on_finished()
                    continue
                data = _parse_play_result(line)
                if data is not None:
                    self._on_play_result(data)
                elif self._should_show(line):
                    self._log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    def _on_finished(self):
        self.proc = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="已停止")

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
        self._log("=== 手动停止 ===")

    def _on_close(self):
        # 关窗口时强制结束 bot 整个进程树,避免它留在后台继续点游戏
        self._kill_proc()
        self.root.destroy()


def main():
    root = tk.Tk()
    AutodoriGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

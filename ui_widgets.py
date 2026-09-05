# -*- coding: utf-8 -*-
"""Maa / SukiUI 风格自绘控件层（纯 Tkinter + Canvas，零第三方依赖）

设计约定：
- 所有控件在构造时读取 ui_theme 当前配色；整站换肤由 gui.py 重建界面完成。
- 圆角一律用 Canvas 多边形采样绘制，避免依赖图片与缩放失真。
- 不使用 ttk 控件，避免与自绘风格冲突。
"""
import math
import re
import tkinter as tk
import tkinter.font as tkfont

from ui_theme import _FONT, get as theme

_size = {"v": 10}
_font_cache = {}


def set_base_size(n):
    _size["v"] = int(n)
    _font_cache.clear()


def base_size():
    return _size["v"]


def _f(size=None, weight="normal", mono=False):
    key = (size, weight, mono)
    if key not in _font_cache:
        fam = "Consolas" if mono else _FONT
        _font_cache[key] = tkfont.Font(
            family=fam, size=int(size if size else _size["v"]), weight=weight
        )
    return _font_cache[key]


def round_points(x, y, w, h, r, steps=6):
    """顺时针生成圆角矩形采样点，供 create_polygon 使用。"""
    r = max(0.0, min(float(r), w / 2.0, h / 2.0))
    pts = []
    centers = (
        (x + w - r, y + r, -90.0),
        (x + w - r, y + h - r, 0.0),
        (x + r, y + h - r, 90.0),
        (x + r, y + r, 180.0),
    )
    for cx, cy, a0 in centers:
        for i in range(steps + 1):
            a = math.radians(a0 + i * 90.0 / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


def _mix(c1, c2, t):
    """两个 #RRGGBB 之间线性插值，用于 hover 过渡色。"""
    c1 = c1.lstrip("#")
    c2 = c2.lstrip("#")
    out = []
    for i in (0, 2, 4):
        a = int(c1[i:i + 2], 16)
        b = int(c2[i:i + 2], 16)
        out.append(int(round(a + (b - a) * t)))
    return "#%02X%02X%02X" % tuple(out)


class Card(tk.Canvas):
    """圆角卡片：直接继承 Canvas（避免外层 Frame 无法感知 Canvas 高度）。

    - create_window 把 body Frame 嵌进 Canvas
    - Canvas 自身高度 = 子控件总高度 + 2*padding
    - 子控件可重写 winfo_reqheight 声明自己需要的高度(如自定义视图)
    """

    def __init__(self, master, radius=10, padding=14, surface="surface", **kw):
        th = theme()
        self._radius = radius
        self._pad = padding
        self._surface = surface
        tk.Canvas.__init__(
            self, master, bg=th["app_bg"], highlightthickness=0, bd=0, **kw
        )
        self.body = tk.Frame(self, bg=th[surface])
        self._win = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_configure)
        self.body.bind("<Configure>", self._sync_height)
        self._h = -1

    def _content_height(self):
        """累加 body 内所有子控件的渲染高度。

        同时取 winfo_height()(实际渲染)和 winfo_reqheight()(内容声明高度)，
        取较大者——避免子控件被外层已渲染小尺寸"锁住"高度,
        导致内容溢出截断。
        """
        h = 0
        for child in self.body.winfo_children():
            try:
                info = child.pack_info()
                pady = info.get("pady", 0)
                if isinstance(pady, (tuple, list)):
                    p_top, p_bot = pady
                else:
                    p_top = p_bot = pady
                wh = max(int(child.winfo_height()),
                         int(child.winfo_reqheight()))
                h += wh + int(p_top) + int(p_bot)
            except Exception:
                pass
        return h

    def _sync_height(self, _e=None):
        req = self._content_height() + 2 * self._pad
        if req > 4 and req != self._h:
            self._h = req
            self.configure(height=req)

    def _on_configure(self, e):
        th = theme()
        w = max(e.width, 4)
        h = max(self._h, 4)
        self.delete("cardbg")
        self.create_polygon(
            round_points(1, 1, w - 2, h - 2, self._radius),
            fill=th[self._surface],
            outline=th["border"],
            width=1,
            tags="cardbg",
        )
        self.tag_lower("cardbg")
        self.itemconfigure(self._win, width=max(w - 2 * self._pad, 1))
        self.coords(self._win, self._pad, self._pad)
        # 父 Canvas 重新绘制背景后,顺带更新 body 宽度(防 width 拉伸未跟上)
        self.itemconfigure(self._win, width=max(w - 2 * self._pad, 1))


class SidePanel(tk.Canvas):
    """侧栏卡片：圆角矩形包住整个侧栏内容（仿 MAA 任务列表框）。

    与 Card 不同的是：高度填满父容器，body 区域占满除 padding 外的空间。
    """

    def __init__(self, master, radius=12, padding=8, surface="surface", **kw):
        th = theme()
        tk.Canvas.__init__(
            self, master, bg=th["app_bg"], highlightthickness=0, bd=0, **kw
        )
        self._radius = radius
        self._pad = padding
        self._surface = surface
        self.body = tk.Frame(self, bg=th[surface])
        self._win = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _e=None):
        th = theme()
        w = max(self.winfo_width(), 4)
        h = max(self.winfo_height(), 4)
        self.delete("panelbg")
        self.create_polygon(
            round_points(1, 1, w - 2, h - 2, self._radius),
            fill=th[self._surface],
            outline=th["border"],
            width=1,
            tags="panelbg",
        )
        self.tag_lower("panelbg")
        self.itemconfigure(self._win, width=max(w - 2 * self._pad, 1))
        self.coords(self._win, self._pad, self._pad)


class ScrolledFrame(tk.Frame):
    """可滚轮滚动的容器，隐藏滚动条以保持 Maa 的干净观感。"""

    def __init__(self, master, **kw):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["app_bg"], **kw)
        self._cv = tk.Canvas(self, bg=th["app_bg"], highlightthickness=0, bd=0)
        self._cv.pack(fill="both", expand=True)
        self.inner = tk.Frame(self._cv, bg=th["app_bg"])
        self._win = self._cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _e: self._cv.configure(scrollregion=self._cv.bbox("all")),
        )
        self._cv.bind("<Configure>", self._on_resize)
        self._wheel_id = self._cv.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind("<Destroy>", self._cleanup)

    def _cleanup(self, e=None):
        if e.widget is self:
            try:
                self._cv.unbind_all("<MouseWheel>", self._wheel_id)
            except Exception:
                pass

    def _on_resize(self, e):
        self._cv.itemconfigure(self._win, width=e.width)

    def _contains(self, widget):
        while widget is not None:
            if widget == self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, e):
        if isinstance(e.widget, tk.Text):
            return
        if not self._contains(e.widget):
            return
        top, bottom = self._cv.yview()
        if bottom - top >= 1.0:
            return
        self._cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def to_top(self):
        self._cv.yview_moveto(0.0)


class SectionTitle(tk.Frame):
    """卡片内的分组标题（可选右侧说明文字）。"""

    def __init__(self, master, text, hint=""):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["surface"])
        tk.Label(
            self, text=text, bg=th["surface"], fg=th["text"],
            font=_f(base_size() + 1, "bold"),
        ).pack(side="left")
        if hint:
            tk.Label(
                self, text=hint, bg=th["surface"], fg=th["text_3"],
                font=_f(base_size() - 1),
            ).pack(side="right")


class Row(tk.Frame):
    """标签 + 控件的标准行。"""

    def __init__(self, master, label, hint="", label_width=12, label_gap=28):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["surface"])
        self._lw = label_width
        if label:
            lb = tk.Label(
                self, text=label, bg=th["surface"], fg=th["text_2"],
                font=_f(), width=label_width, anchor="w",
            )
            lb.pack(side="left")
            # 标签与下拉框之间留固定空隙,让选项整体更靠右、更宽松
            tk.Frame(self, bg=th["surface"], width=label_gap).pack(side="left")
        self.slot = tk.Frame(self, bg=th["surface"])
        self.slot.pack(side="left", fill="x", expand=True)
        if hint:
            tk.Label(
                self, text=hint, bg=th["surface"], fg=th["text_3"],
                font=_f(base_size() - 1),
            ).pack(side="right")


class Segmented(tk.Canvas):
    """分段选择器（Maa 的选项控件观感）。"""

    def __init__(self, master, values, value=None, on_change=None, height=28,
                 width=None):
        th = theme()
        tk.Canvas.__init__(
            self, master, height=height, width=width if width else 1,
            highlightthickness=0, bd=0, bg=th["surface"],
        )
        self.values = list(values)
        self.on_change = on_change
        self._hover = -1
        try:
            self.index = self.values.index(value) if value in self.values else 0
        except ValueError:
            self.index = 0
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(-1))

    @property
    def value(self):
        return self.values[self.index]

    def set(self, value):
        if value in self.values and self.values.index(value) != self.index:
            self.index = self.values.index(value)
            self._draw()
            return True
        return False

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self._draw()

    def _hit(self, e):
        w = max(self.winfo_width(), 2)
        seg = w / float(len(self.values))
        i = int(e.x / seg) if seg > 0 else 0
        return max(0, min(len(self.values) - 1, i))

    def _motion(self, e):
        self._set_hover(self._hit(e))

    def _click(self, e):
        i = self._hit(e)
        if i != self.index:
            self.index = i
            self._draw()
            if self.on_change:
                self.on_change(self.value)

    def _draw(self):
        th = theme()
        self.delete("all")
        w = max(self.winfo_width(), 4)
        h = max(self.winfo_height(), 4)
        n = len(self.values)
        ins = 1.0
        tw = (w - 2 * ins) / n
        self.create_polygon(
            round_points(ins, ins, w - 2 * ins, h - 2 * ins, (h - 2 * ins) / 2.0),
            fill=th["surface_sunken"], outline="", tags="track",
        )
        f = _f(base_size() - 1)
        fb = _f(base_size() - 1, "bold")
        for i, v in enumerate(self.values):
            x0 = ins + i * tw
            sel = i == self.index
            text = v
            # 文字过长时按可视宽度截断，保证不溢出分段
            limit = tw - 10
            while f.measure(text) > limit and len(text) > 2:
                text = text[:-2] + "…"
            if sel:
                self.create_polygon(
                    round_points(x0 + 2, ins + 2, tw - 4, h - 2 * ins - 4, 6),
                    fill=th["surface"], outline=th["border"], width=1,
                )
                fill = th["accent_text"]
                use = fb
            elif i == self._hover:
                self.create_polygon(
                    round_points(x0 + 2, ins + 2, tw - 4, h - 2 * ins - 4, 6),
                    fill=th["hover"], outline="",
                )
                fill = th["text"]
                use = f
            else:
                fill = th["text_2"]
                use = f
            self.create_text(
                x0 + tw / 2.0, h / 2.0, text=text, fill=fill, font=use,
            )


class DropdownBox(tk.Canvas):
    """下拉方框：一个方框，点开弹出选项菜单（Maa 风格）。

    直接继承 Canvas。用 <Configure> 在每次尺寸变化(含首次布局)后按真实
    宽度重绘，保证方框长度始终与实际控件宽度一致、初显不空白。
    调用方需用 pack(fill="x") 让它横向撑满可用空间。
    """

    def __init__(self, master, values, value=None, on_change=None,
                 width=240, height=34, min_width=160, surface="surface"):
        th = theme()
        self.values = list(values)
        self.on_change = on_change
        self._surface = surface
        self._min_w = max(int(min_width), 60)
        try:
            self._index = self.values.index(value) if value in self.values else 0
        except ValueError:
            self._index = 0
        self._hover = False
        self._menu = None
        tk.Canvas.__init__(
            self, master, width=max(width, self._min_w), height=height,
            highlightthickness=0, bd=0, bg=th[surface], cursor="hand2",
        )
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def _redraw(self, _e=None):
        # 首次或每次尺寸变化都按真实宽度重画,杜绝初始空白/长度不匹配
        self._draw()

    def get(self):
        return self.values[self._index]

    def set(self, value):
        if value in self.values:
            i = self.values.index(value)
            if i != self._index:
                self._index = i
                self._draw()
                return True
        return False

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._draw()

    def _draw(self):
        th = theme()
        self.delete("all")
        # 用真实渲染宽度,保证方框与控件一致
        w = self.winfo_width()
        h = self.winfo_height()
        if w < self._min_w:
            w = self._min_w
        if w <= 1 or h <= 1:
            return
        bg = th["hover"] if self._hover else th["surface_sunken"]
        self.create_polygon(
            round_points(1, 1, w - 2, h - 2, 7),
            fill=bg, outline=th["border"], width=1,
        )
        text = str(self.values[self._index])
        self.create_text(
            12, h / 2.0, text=text, fill=th["text"], anchor="w",
            font=_f(base_size(), "normal"),
        )
        cx = w - 16
        cy = h / 2.0
        self.create_polygon(
            cx - 4, cy - 2, cx + 4, cy - 2, cx, cy + 3,
            fill=th["text_2"], outline="",
        )

    def _open(self, _e=None):
        if self._menu is not None:
            return
        th = theme()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 1
        self._menu = tk.Menu(self, tearoff=0,
                             bg=th["surface"], fg=th["text"],
                             activebackground=th["accent_soft"],
                             activeforeground=th["accent_text"],
                             relief="flat", bd=1,
                             font=_f(base_size()))
        for i, v in enumerate(self.values):
            self._menu.add_command(
                label=str(v),
                command=lambda idx=i: self._pick(idx),
            )
        try:
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()
            self._menu = None
            self._draw()

    def _pick(self, idx):
        if idx != self._index:
            self._index = idx
            self._draw()
            if self.on_change:
                self.on_change(self.values[idx])


class ToggleSwitch(tk.Canvas):
    """开关控件。"""

    def __init__(self, master, value=False, on_change=None, width=40, height=22):
        th = theme()
        tk.Canvas.__init__(
            self, master, width=width, height=height, highlightthickness=0,
            bd=0, bg=th["surface"],
        )
        self.on_change = on_change
        self.value = bool(value)
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _e=None):
        self.value = not self.value
        self._draw()
        if self.on_change:
            self.on_change(self.value)

    def set(self, val):
        self.value = bool(val)
        self._draw()

    def _draw(self):
        th = theme()
        self.delete("all")
        w = int(self.cget("width"))
        h = int(self.cget("height"))
        pad = 2
        r = (h - 2 * pad) / 2.0
        track = th["accent"] if self.value else th["border_strong"]
        self.create_polygon(
            round_points(pad, pad, w - 2 * pad, h - 2 * pad, r),
            fill=track, outline="",
        )
        kr = r - 3
        cx = w - pad - r if self.value else pad + r
        self.create_oval(cx - kr, h / 2.0 - kr, cx + kr, h / 2.0 + kr,
                         fill="#FFFFFF", outline="")


class NumberField(tk.Frame):
    """带 1px 边框的数值输入框。用 Frame 充当边框，避免 ttk 主题干扰。"""

    def __init__(self, master, value=0, width=7, on_change=None, justify="center"):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["border"], padx=1, pady=1)
        inner = tk.Frame(self, bg=th["surface"])
        inner.pack(fill="both", expand=True)
        self.on_change = on_change
        self._var = tk.StringVar(value=str(value))
        self.entry = tk.Entry(
            inner, textvariable=self._var, width=width, bd=0,
            highlightthickness=0, relief="flat", justify=justify,
            bg=th["surface"], fg=th["text"], insertbackground=th["text"],
            font=_f(),
        )
        self.entry.pack(padx=8, pady=4)
        vcmd = (self.register(self._validate), "%P")
        self.entry.configure(validate="key", validatecommand=vcmd)
        self.entry.bind("<FocusOut>", lambda _e: self._emit())
        self.entry.bind("<Return>", lambda _e: self._emit())

    def _validate(self, pending):
        if pending == "" or pending.isdigit():
            return True
        return False

    def _emit(self):
        if self.on_change:
            self.on_change(self.get())

    def get(self, default=0):
        raw = self._var.get().strip()
        try:
            return int(raw)
        except ValueError:
            return default

    def set(self, value):
        self._var.set(str(value))


class Stepper(tk.Frame):
    """− 值 + 步进器，用于 photogate 这类需要微调的参数。

    中间是**可直接编辑**的输入框：可键入 min_v~max_v 区间内任意整数，
    回车或失焦即生效；越界自动归一化并回写。
    ± 按钮每次走 ``step``；按住 Shift 点击走 ``big_step``（快速粗调）。
    """

    def __init__(self, master, value=0, min_v=0, max_v=150, step=1,
                 big_step=10, on_change=None, width=8):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["border"], padx=1, pady=1)
        inner = tk.Frame(self, bg=th["surface"])
        inner.pack(fill="both", expand=True)
        self.min_v = min_v
        self.max_v = max_v
        self.step = step
        self.big_step = big_step
        self.on_change = on_change
        self._last = max(min_v, min(max_v, int(value)))
        self._var = tk.StringVar(value=str(self._last))
        self._minus = tk.Label(
            inner, text="−", bg=th["surface"], fg=th["text_2"],
            font=_f(base_size() + 2), width=3, cursor="hand2",
        )
        self._minus.pack(side="left", fill="y")
        self.entry = tk.Entry(
            inner, textvariable=self._var, width=width, bd=0,
            highlightthickness=0, relief="flat", justify="center",
            bg=th["surface"], fg=th["text"], insertbackground=th["text"],
            font=_f(base_size(), "bold"),
        )
        self.entry.pack(side="left", fill="y", pady=4)
        self._plus = tk.Label(
            inner, text="+", bg=th["surface"], fg=th["text_2"],
            font=_f(base_size() + 2), width=3, cursor="hand2",
        )
        self._plus.pack(side="left", fill="y")
        vcmd = (self.register(lambda p: p == "" or p.isdigit()), "%P")
        self.entry.configure(validate="key", validatecommand=vcmd)
        for w, delta in ((self._minus, -1), (self._plus, 1)):
            # Shift(0x0001) 按下时走大步长,便于快速粗调
            w.bind("<Button-1>",
                   lambda _e, d=delta: self._bump(d, bool(_e.state & 0x0001)))
        self.entry.bind("<FocusOut>", lambda _e: self._commit())
        self.entry.bind("<Return>", lambda _e: self._commit())

    def _bump(self, direction, big=False):
        cur = self._last
        delta = self.big_step if big else self.step
        self.set(cur + direction * delta)
        self._commit()

    def commit(self):
        """对外暴露的提交入口。

        自绘 PushButton 是 Canvas，点击时**不会**夺走输入框的键盘焦点，
        因此 <FocusOut> 不触发：用户改完数值直接点「开始演出」会用到旧值。
        需要在执行动作前显式调用本方法把待提交的输入落盘为实际值。
        """
        self._commit()

    def _commit(self):
        """把输入框内容归一化为合法整数：越界钳制、非法/空值回滚上次有效值。"""
        raw = self._var.get().strip()
        try:
            val = max(self.min_v, min(self.max_v, int(raw)))
        except (TypeError, ValueError):
            val = self._last
        if str(val) != raw:
            self._var.set(str(val))  # 回写归一化后的值,避免框内残留越界数字
        changed = val != self._last
        self._last = val
        if changed and self.on_change:
            self.on_change(val)

    def get(self):
        return self._last

    def set(self, value):
        value = max(self.min_v, min(self.max_v, int(value)))
        self._last = value
        self._var.set(str(value))

    def enable(self, on=True):
        th = theme()
        state = "normal" if on else "disabled"
        self.entry.configure(state=state, disabledforeground=th["text_3"])
        fg = th["text_2"] if on else th["text_3"]
        self._minus.configure(fg=fg)
        self._plus.configure(fg=fg)


class PushButton(tk.Canvas):
    """自绘按钮：primary / secondary / ghost / danger。"""

    def __init__(self, master, text, command=None, kind="primary", width=None,
                 height=34, padx=18):
        th = theme()
        self._text = text
        self._kind = kind
        self._cmd = command
        self._enabled = True
        self._down = False
        self._hover = False
        f = _f(base_size(), "bold" if kind == "primary" else "normal")
        w = width if width else int(f.measure(text)) + padx * 2
        tk.Canvas.__init__(
            self, master, width=w, height=height, highlightthickness=0, bd=0,
            bg=th["surface"] if kind in ("secondary", "ghost") else th["surface"],
            cursor="hand2",
        )
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def _set_hover(self, on):
        if self._enabled and on != self._hover:
            self._hover = on
            self._draw()

    def _press(self, _e):
        if not self._enabled:
            return
        self._down = True
        self._draw()

    def _release(self, _e):
        if not self._enabled:
            return
        self._down = False
        self._draw()
        if self._cmd:
            self._cmd()

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_enabled(self, on):
        if self._enabled != on:
            self._enabled = on
            self.configure(cursor="hand2" if on else "arrow")
            self._draw()

    def _colors(self):
        th = theme()
        if not self._enabled:
            if self._kind == "primary":
                return th["border_strong"], th["text_3"], ""
            return th["surface_sunken"], th["text_3"], th["border"]
        if self._kind == "primary":
            if self._down:
                return th["accent"], "#FFFFFF", ""
            return (th["accent_hover"] if self._hover else th["accent"]), "#FFFFFF", ""
        if self._kind == "danger":
            base = th["danger"]
            return base, "#FFFFFF", ""
        if self._kind == "ghost":
            bg = th["hover"] if self._hover else th["surface"]
            return bg, th["text_2"], ""
        bg = th["hover"] if self._hover else th["surface"]
        return bg, th["text"], th["border"]

    def _draw(self):
        self.delete("all")
        w = int(self.cget("width"))
        h = int(self.cget("height"))
        fill, fg, outline = self._colors()
        ow = 1 if outline else 0
        self.create_polygon(
            round_points(1, 1, w - 2, h - 2, 8), fill=fill,
            outline=outline, width=ow,
        )
        f = _f(base_size(), "bold" if self._kind in ("primary", "danger") else "normal")
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg, font=f)


# ---- 桌面 SVG 图标(path 数据, 24x24 viewBox, stroke 型) ----
# 来源: 桌面 settings.svg(齿轮) 与 half-moon.svg(月牙)。作为描边路径在 Canvas 复刻。
SETTINGS_SVG = ("M19.6224 10.3954 L18.5247 7.7448 L20 6 L18 4 L16.2647 5.48295 "
                "L13.5578 4.36974 L12.9353 2 H10.981 L10.3491 4.40113 "
                "L7.70441 5.51596 L6 4 L4 6 L5.45337 7.78885 L4.3725 10.4463 "
                "L2 11 V13 L4.40111 13.6555 L5.51575 16.2997 L4 18 L6 20 "
                "L7.79116 18.5403 L10.397 19.6123 L11 22 H13 L13.6045 19.6132 "
                "L16.2551 18.5155 C16.6969 18.8313 18 20 18 20 L20 18 "
                "L18.5159 16.2494 L19.6139 13.598 L21.9999 12.9772 L22 11 "
                "L19.6224 10.3954 Z M12 15 C13.6569 15 15 13.6569 15 12 "
                "C15 10.3431 13.6569 9 12 9 C10.3431 9 9 10.3431 9 12 "
                "C9 13.6569 10.3431 15 12 15 Z")
MOON_SVG = ("M3 11.5066 C3 16.7497 7.25034 21 12.4934 21 C16.2209 21 "
            "19.4466 18.8518 21 15.7259 C12.4934 15.7259 8.27411 11.5066 "
            "8.27411 3 C5.14821 4.55344 3 7.77915 3 11.5066 Z")

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


# 拆解式解析器,处理每个命令并允许隐式续写
def _svg_path_pts(d, n=14):
    """返回 [(pts, closed), ...]。pts 内为 24 视框坐标点列。"""
    toks = re.findall(r"[MmLlCcHhVvZz]|" + _NUM, d)
    subs = []
    cur_pts = None
    closed = False
    x = y = 0.0
    sx = sy = 0.0        # 上一个 C 的第二个控制点(相对续写)
    last_cmd = None

    def push_end(p):
        cur_pts.append((round(p[0], 3), round(p[1], 3)))

    def bez(a, c1, c2, b):
        for k in range(1, n + 1):
            t = k / float(n)
            mt = 1.0 - t
            X = mt ** 3 * a[0] + 3 * mt * mt * t * c1[0] + \
                3 * mt * t * t * c2[0] + t ** 3 * b[0]
            Y = mt ** 3 * a[1] + 3 * mt * mt * t * c1[1] + \
                3 * mt * t * t * c2[1] + t ** 3 * b[1]
            push_end((X, Y))

    i = 0
    while i < len(toks):
        if re.match(r"[MmLlCcHhVvZz]$", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in ("Z", "z"):
                if cur_pts:
                    subs.append((cur_pts, True))
                cur_pts = None
                sx = sy = 0.0
                continue
        else:
            # 隐式续写:沿用上一个需要参数的绘图命令(M→L)
            if last_cmd in ("M", "m"):
                cmd = "L"
            elif last_cmd in ("C", "c"):
                cmd = "C"
            elif last_cmd in ("L", "l"):
                cmd = "L"
            elif last_cmd in ("H", "h"):
                cmd = "H"
            elif last_cmd in ("V", "v"):
                cmd = "V"
            else:
                cmd = "L"

        rel = cmd.islower()
        base = cmd.upper()

        if base == "M":
            X, Y = float(toks[i]), float(toks[i + 1])
            i += 2
            if rel:
                X, Y = x + X, y + Y
            x, y = X, Y
            if cur_pts is None:
                cur_pts = []
                closed = False
            # 保持同一子路径(隐式后续)
            if not cur_pts:
                cur_pts = [(round(x, 3), round(y, 3))]
                # 但子路径起点 = 当前
            last_cmd = "M"
        elif base == "L":
            X, Y = float(toks[i]), float(toks[i + 1])
            i += 2
            if rel:
                X, Y = x + X, y + Y
            push_end((X, Y))
            x, y = X, Y
            last_cmd = "L"
        elif base == "H":
            X = float(toks[i]); i += 1
            if rel:
                X = x + X
            push_end((X, y))
            x = X
            last_cmd = "H"
        elif base == "V":
            Y = float(toks[i]); i += 1
            if rel:
                Y = y + Y
            push_end((x, Y))
            y = Y
            last_cmd = "V"
        elif base == "C":
            x1, y1 = float(toks[i]), float(toks[i + 1])
            x2, y2 = float(toks[i + 2]), float(toks[i + 3])
            X, Y = float(toks[i + 4]), float(toks[i + 5])
            i += 6
            if rel:
                x1, y1 = x + x1, y + y1
                x2, y2 = x + x2, y + y2
                X, Y = x + X, y + Y
            a = (x, y)
            bez(a, (x1, y1), (x2, y2), (X, Y))
            x, y = X, Y
            sx, sy = x2, y2
            last_cmd = "C"
        else:
            # 未知命令,跳到下一个命令字母
            while i < len(toks) and not re.match(r"[MmLlCcHhVvZz]$", toks[i]):
                i += 1
    if cur_pts:
        subs.append((cur_pts, closed))
    return subs


def _svg_stroke(canvas, d, x, y, size, color, stroke=1.6):
    """把 24x24 视框的描边 SVG 路径按 size 绘制到 (x, y) 起始区域。

    高保真版本：PIL ImageDraw 在 8x 超采样画布上画描边，
    LANCZOS 降采样到目标 size 以获得与 SVG 矢量渲染一致的平滑边。
    结果通过 Tk PhotoImage + canvas.create_image 贴图，避免 Tk Canvas
    多段折线拼接贝塞尔时的"棱角"和"锯齿"。
    PIL/Pillow 已在 requirements.txt(项目中已用)。
    """
    try:
        from PIL import Image, ImageDraw, ImageTk
    except Exception:
        # 极端兜底：缺 PIL 时退回原来的多段折线绘制
        scale = size / 24.0
        sw = max(1.0, stroke * scale)
        for pts, closed in _svg_path_pts(d):
            mapped = [(x + px * scale, y + py * scale) for px, py in pts]
            if len(mapped) < 2:
                continue
            if closed:
                canvas.create_polygon(mapped, outline=color, fill="", width=sw,
                                      joinstyle="round")
            else:
                canvas.create_line(mapped, fill=color, width=sw,
                                   capstyle="round", joinstyle="round")
        return

    SS = 8  # 超采样倍率;越大越平滑,代价是渲染耗时
    W = max(16, int(round(size * SS)))  # 内部画布像素
    pts_list = _svg_path_pts(d)
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    # 把 hex 颜色转 rgb(只用前景通道,alpha 由 stroke 决定)
    r, g, b = _hex_to_rgb(color)
    # 在超采样坐标系里描边;stroke 也要按 SS 缩放
    sw_inner = max(1, int(round(stroke * SS)))
    s_factor = W / 24.0
    for pts, closed in pts_list:
        mapped = [(px * s_factor, py * s_factor) for px, py in pts]
        if len(mapped) < 2:
            continue
        if closed:
            # PIL 的 polygon 不支持 joint;改用 line 闭合路径,以获得圆滑拐角
            closed_pts = mapped + [mapped[0]]
            draw.line(closed_pts, fill=(r, g, b, 255), width=sw_inner,
                      joint="curve")
        else:
            draw.line(mapped, fill=(r, g, b, 255), width=sw_inner,
                      joint="curve")
    # 降采样到目标 size
    if (W, W) != (size, size):
        im = im.resize((int(size), int(size)), Image.LANCZOS)
    # 转 PhotoImage 并贴到画布
    photo = ImageTk.PhotoImage(im)
    # 必须把 photo 引用挂在 canvas 上,否则会被 GC
    _stash_photo(canvas, photo)
    canvas.create_image(int(x), int(y), image=photo, anchor="nw")


def _hex_to_rgb(c):
    if isinstance(c, str) and c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0, 0, 0


_photo_refs = []


def _stash_photo(canvas, photo):
    """把 photo 引用挂到 canvas 上,防 GC;并维护一个全局上限,避免无限增长。"""
    if not hasattr(canvas, "_svg_photos"):
        canvas._svg_photos = []
    canvas._svg_photos.append(photo)
    # 全局兜底:每个 canvas 最多保留最近 256 张
    if len(canvas._svg_photos) > 256:
        del canvas._svg_photos[: len(canvas._svg_photos) - 256]


def draw_icon(canvas, kind, x, y, color, size=14):
    """在画布上画极简线性图标（不使用 emoji，保证跨平台一致）。

    kind ∈ {live, logs, docs, settings, theme, sun, moon, gear}
    gear / moon 用桌面 SVG 描边路径采样复刻,其余为几何绘制。
    x, y 为左上角,size 为图标像素边长。
    """
    s = size / 14.0
    if kind == "live":
        canvas.create_line(x + 5 * s, y + 2 * s, x + 5 * s, y + 9 * s,
                           fill=color, width=1.4 * s)
        canvas.create_line(x + 5 * s, y + 2 * s, x + 11 * s, y + 3.6 * s,
                           fill=color, width=1.4 * s)
        canvas.create_oval(x + 2 * s, y + 9 * s, x + 8 * s, y + 14 * s,
                           outline=color, width=1.4 * s)
    elif kind == "logs":
        for i, wdt in enumerate((11, 9, 6)):
            canvas.create_line(x + 1.5 * s, y + (3.5 + i * 3.6) * s,
                               x + (1.5 + wdt) * s, y + (3.5 + i * 3.6) * s,
                               fill=color, width=1.4 * s)
    elif kind == "docs":
        # 文档图标:外框 + 折角 + 内文横线(线宽加大,深色下也清晰)
        # 外框(用 polygon 画一个右上折角的矩形)
        pts = [
            x + 3 * s, y + 2 * s,
            x + 9 * s, y + 2 * s,
            x + 12 * s, y + 5 * s,
            x + 12 * s, y + 12 * s,
            x + 3 * s, y + 12 * s,
        ]
        canvas.create_polygon(pts, outline=color, fill="", width=1.6 * s)
        # 折角
        canvas.create_line(x + 9 * s, y + 2 * s, x + 9 * s, y + 5 * s,
                           fill=color, width=1.6 * s)
        canvas.create_line(x + 9 * s, y + 5 * s, x + 12 * s, y + 5 * s,
                           fill=color, width=1.6 * s)
        # 文字横线
        for i, yy in enumerate((7.5, 9.5, 11.5)):
            wd = 7.0 if i < 2 else 5.0
            canvas.create_line(x + 4.5 * s, y + yy * s - 0.6,
                               x + (4.5 + wd) * s, y + yy * s - 0.6,
                               fill=color, width=1.4 * s)
    elif kind == "settings":
        # 三条横线 + 第2条上一个小圆点(滑块控件观感,比齿轮更易辨识)
        # 横线
        for i, yy in enumerate((3.5, 7.0, 10.5)):
            canvas.create_line(x + 1.5 * s, y + yy * s,
                               x + 12.5 * s, y + yy * s,
                               fill=color, width=1.4 * s)
        # 第2条上画一个实心圆(代表滑块)
        cx = x + 8.0 * s
        cy = y + 7.0 * s
        r = 1.6 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=color, outline="")
    elif kind == "theme":
        canvas.create_oval(x + 2.5 * s, y + 2.5 * s, x + 11.5 * s, y + 11.5 * s,
                           outline=color, width=1.3 * s)
        canvas.create_polygon(
            x + 7 * s, y + 2.5 * s, x + 11.5 * s, y + 7 * s,
            x + 11.5 * s, y + 11.5 * s, x + 7 * s, y + 2.5 * s,
            fill=color, outline="",
        )
    elif kind == "gear":
        # 桌面 settings.svg 齿轮外形(描边),用于侧栏行尾与设置类导航图标
        _svg_stroke(canvas, SETTINGS_SVG, x, y, size, color, stroke=1.4)
    elif kind == "sun":
        cx, cy, r = x + 7 * s, y + 7 * s, 2.8 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
        # 八道光线(更粗更长)
        import math as _m
        for ang in range(0, 360, 45):
            rad = _m.radians(ang)
            x1 = cx + _m.cos(rad) * 4.4 * s
            y1 = cy + _m.sin(rad) * 4.4 * s
            x2 = cx + _m.cos(rad) * 6.4 * s
            y2 = cy + _m.sin(rad) * 6.4 * s
            canvas.create_line(x1, y1, x2, y2, fill=color, width=1.8 * s)
    elif kind == "moon":
        # 桌面 half-moon.svg 月牙外形(描边),用于深色模式切换按钮
        _svg_stroke(canvas, MOON_SVG, x, y, size, color, stroke=1.5)


class NavItem(tk.Canvas):
    """侧栏导航项：图标 + 文字，可选行尾小齿轮装饰图标（Maa 任务列表观感）。"""

    def __init__(self, master, text, icon, command=None, width=150, height=38,
                 font_offset=1, surface="surface", trailing=None,
                 trailing_size=12):
        th = theme()
        tk.Canvas.__init__(
            self, master, width=width, height=height, highlightthickness=0,
            bd=0, bg=th[surface], cursor="hand2",
        )
        self._text = text
        self._icon = icon
        self._cmd = command
        self._selected = False
        self._hover = False
        self._fo = int(font_offset)
        self._surface = surface
        self._trailing = trailing
        self._tsize = int(trailing_size)
        self.bind("<Button-1>", lambda _e: self._cmd and self._cmd())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._draw()

    def select(self, on):
        self._selected = on
        self._draw()

    def _draw(self):
        th = theme()
        self.delete("all")
        # 一律用"实际渲染宽高";仅当尚未布局(<4px)才回退到构造时请求的 cget 值。
        # 关键:不要 max(cget, winfo)——cget 只是请求值,可能大于真实可用宽度,
        # 用它画会让行尾齿轮溢出画布右侧被裁。
        w = self.winfo_width()
        if w < 4:
            w = int(self.cget("width"))
        h = self.winfo_height()
        if h < 4:
            h = int(self.cget("height"))
        if self._selected:
            bg = th["accent_soft"]
            fg = th["accent_text"]
        elif self._hover:
            bg = th["hover"]
            fg = th["text"]
        else:
            bg = th[self._surface]
            fg = th["text_2"]
        self.create_polygon(round_points(6, 3, w - 12, h - 6, 8), fill=bg, outline="")
        draw_icon(self, self._icon, 14, (h - 14) / 2.0, fg)
        self.create_text(
            36, h / 2.0, text=self._text, fill=fg, anchor="w",
            font=_f(base_size() + self._fo,
                    "bold" if self._selected else "normal"),
        )
        # 行尾小齿轮:高度对齐导航文字(label 字体行高),并留右侧内边距避免被裁
        if self._trailing:
            tail_color = th["accent_text"] if self._selected else th["text_3"]
            lab = _f(base_size() + self._fo)
            gs = max(10, lab.metrics("linespace") - 3)  # 与文字同高
            right = 14  # 画布右缘留白
            gx = w - right - gs
            gy = (h - gs) / 2.0
            draw_icon(self, self._trailing, gx, gy, tail_color, size=gs)


class IconButton(tk.Canvas):
    """仅图标的圆形按钮,常用于侧栏底部主题切换。"""

    def __init__(self, master, icon, command=None, size=36, tip=None):
        th = theme()
        self._size = size
        tk.Canvas.__init__(
            self, master, width=size, height=size, highlightthickness=0,
            bd=0, bg=th["surface"], cursor="hand2",
        )
        self._icon = icon
        self._cmd = command
        self._hover = False
        self._tip = tip
        self.bind("<Button-1>", lambda _e: self._cmd and self._cmd())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def set_icon(self, kind):
        self._icon = kind
        self._draw()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._draw()

    def _draw(self):
        th = theme()
        self.delete("all")
        s = self._size
        bg = th["hover"] if self._hover else th["surface"]
        self.create_oval(2, 2, s - 2, s - 2, fill=bg, outline=th["border"])
        fg = th["text"]
        draw_icon(self, self._icon, (s - 14) / 2.0, (s - 14) / 2.0, fg, size=14)


class StatusDot(tk.Canvas):
    """状态圆点。"""

    def __init__(self, master, color=None, size=9):
        th = theme()
        tk.Canvas.__init__(
            self, master, width=size, height=size, highlightthickness=0, bd=0,
            bg=th["surface"],
        )
        self._size = size
        self.set(color or th["idle"])

    def set(self, color):
        self.delete("all")
        s = self._size
        self.create_oval(1, 1, s - 1, s - 1, fill=color, outline="")


class Metric(tk.Frame):
    """指标块：小标签 + 数值。"""

    def __init__(self, master, label, value="—"):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["surface"])
        tk.Label(self, text=label, bg=th["surface"], fg=th["text_2"],
                 font=_f(base_size() - 1)).pack(anchor="w")
        self._v = tk.Label(self, text=value, bg=th["surface"], fg=th["text"],
                           font=_f(base_size() + 5, "bold"))
        self._v.pack(anchor="w")

    def set(self, value):
        self._v.configure(text=value)


class FitLabel(tk.Frame):
    """单行文本：宽度不够时自动按像素截断并加省略号，**且绝不撑大外层布局**。

    长歌名是典型场景。普通 ``Label`` 的天然宽度会一路向上传递，
    把卡片→grid→滚动区整体撑宽，导致右侧按钮被挤出可视区(需求 1/2 的根因)。
    本控件用两个手段根治：

    1. ``pack_propagate(False)`` + 请求宽度恒为 1px：内部 Label 的天然宽度
       不再影响父容器的几何计算，宽度完全由外层分配；
    2. ``<Configure>`` 里按真实宽度二分截断文本，超出部分用 "…" 表示，
       而不是被画布硬裁掉半个字。

    高度固定为字体行高，保证整行不会因为文本长度变化而抖动。
    """

    def __init__(self, master, text="", size=None, weight="normal", fg=None,
                 bg=None, anchor="w", surface="surface"):
        th = theme()
        self._font = _f(size, weight)
        self._bg = bg or th[surface]
        self._fg = fg or th["text"]
        self._anchor = anchor
        self._full = text if text else ""
        h = int(self._font.metrics("linespace")) + 2
        tk.Frame.__init__(self, master, bg=self._bg, width=1, height=h)
        self.pack_propagate(False)  # 关键:不让内部 Label 的天然宽度向上传递
        self._lbl = tk.Label(
            self, text=self._full, bg=self._bg, fg=self._fg, font=self._font,
            anchor=anchor,
        )
        self._lbl.pack(fill="both", expand=True)
        self.bind("<Configure>", self._fit)

    def set(self, text):
        self._full = text if text else ""
        self._fit()

    def get(self):
        return self._full

    def _fit(self, _e=None):
        if not self.winfo_exists():
            return
        w = self.winfo_width()
        full = self._full
        if w > 2:
            shown = self._truncate(full, w)
        else:
            # 宽度尚未完成布局(初始 width=1,或布局正被重算)。此时若直接 return
            # 会让 set() 静默失败、文本停留旧值(歌名不切换)。先把文本更新上
            # (即使暂时被裁),再延迟重试等布局就绪后按真实宽度截断。
            shown = full
            try:
                self.after(50, self._fit)
            except Exception:
                pass
        if self._lbl.cget("text") != shown:
            self._lbl.configure(text=shown)

    def _truncate(self, text, max_w):
        if not text or self._font.measure(text) <= max_w:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:  # 二分找能放下的最长前缀
            mid = (lo + hi + 1) // 2
            if self._font.measure(text[:mid] + ell) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo] + ell) if lo > 0 else ell


class DocView(tk.Frame):
    """只读文档视图（注意事项 / 用前必读 / 常见问题）。

    逐行解析文档,每段用独立 Label 垂直排布进 Card,整页随内容自然增高,
    由外层 ScrolledFrame 整页滚动——彻底规避 Tk ``Text`` 固定 ``height``
    截断、且 wrap 后"逻辑行≠屏幕行"导致高度测量失真、不自带滚动条的固有问题。

    - 标题【…】加粗加大；Q: 用强调色；bullet / sub 用缩进模拟；
    - 每行 Label 按容器宽度自动 wrap,宽度变化时实时重排；
    - 不使用 Text,故不受其裁剪与滚动限制影响。
    """

    def __init__(self, master, **kw):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["surface"], **kw)
        self._fs = base_size()
        self._items = []  # [(widget, padx), ...]
        self.bind("<Configure>", self._relayout)

    def _relayout(self, _e=None):
        if not self.winfo_exists():
            return
        w = self.winfo_width()
        if w <= 4:
            return
        for w_, padx in self._items:
            if isinstance(w_, tk.Label):
                wl = max(w - 2 * padx - 4, 40)
                if w_.cget("wraplength") != wl:
                    w_.configure(wraplength=wl)

    def set_content(self, text):
        for w_, _ in self._items:
            w_.destroy()
        self._items = []
        th = theme()
        fs = self._fs
        for raw in (text or "").split("\n"):
            s = raw.strip()
            if not s:
                sp = tk.Frame(self, bg=th["surface"], height=8)
                sp.pack(fill="x")
                self._items.append((sp, 0))
                continue
            if s.startswith("【"):
                fnt, fg, padx, pady = _f(fs + 2, "bold"), th["text"], 0, (12, 4)
                disp = s
            elif s.startswith(("•", "·", "・", "- ")):
                fnt, fg, padx, pady = _f(fs), th["text"], 16, (2, 2)
                disp = "• " + s[1:].strip()
            elif s.startswith(("Q:", "Q：")):
                fnt, fg, padx, pady = _f(fs, "bold"), th["accent_text"], 0, (12, 2)
                disp = s
            elif s.startswith(("A:", "A：")):
                fnt, fg, padx, pady = _f(fs), th["text"], 14, (2, 2)
                disp = s
            elif raw.startswith(" "):
                fnt, fg, padx, pady = _f(fs), th["text_2"], 34, (2, 2)
                disp = s
            else:
                fnt, fg, padx, pady = _f(fs), th["text"], 0, (2, 2)
                disp = s
            lbl = tk.Label(
                self, text=disp, bg=th["surface"], fg=fg, font=fnt,
                anchor="w", justify="left",
            )
            lbl.pack(anchor="w", fill="x", padx=padx, pady=pady)
            self._items.append((lbl, padx))
        # 首帧容器宽度可能尚未就绪,wraplength 暂无法正确计算;
        # 延迟两次重排,待布局完成后再按真实宽度换行,确保整页高度正确。
        self.update_idletasks()
        self.after(0, self._relayout)
        self.after(80, self._relayout)


class LogConsole(tk.Frame):
    """日志终端：等宽字体 + 级别分色 + 自动跟随。"""

    def __init__(self, master, height=12):
        th = theme()
        tk.Frame.__init__(self, master, bg=th["border"], padx=1, pady=1)
        inner = tk.Frame(self, bg=th["log_bg"])
        inner.pack(fill="both", expand=True)
        fs = base_size()
        self.text = tk.Text(
            inner, height=height, bd=0, highlightthickness=0, relief="flat",
            wrap="word", bg=th["log_bg"], fg=th["text"], font=("Consolas", fs),
            spacing1=1, spacing3=1, cursor="arrow", insertwidth=0,
        )
        self.text.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        for tag, color in (
            ("ts", th["text_3"]), ("info", th["text"]), ("ok", th["ok"]),
            ("warn", th["warn"]), ("err", th["danger"]),
            ("cal", th["accent_text"]), ("dim", th["text_3"]),
        ):
            self.text.tag_configure(tag, foreground=color)
        self.text.configure(state="disabled")
        self.text.bind("<Key>", lambda _e: "break")
        self._autoscroll = True
        self.text.bind("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, e):
        self.text.yview_scroll(int(-1 * (e.delta / 120)), "units")
        top, bottom = self.text.yview()
        self._autoscroll = bottom >= 0.999
        return "break"

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def append(self, text, tag="info"):
        self.text.configure(state="normal")
        self.text.insert("end", text + "\n", tag)
        over = int(self.text.index("end-1c").split(".")[0]) - 2000
        if over > 0:
            self.text.delete("1.0", "%d.0" % over)
        self.text.configure(state="disabled")
        if self._autoscroll:
            self.text.see("end")

    def line(self, stamp, level, message):
        self.text.configure(state="normal")
        if stamp:
            self.text.insert("end", stamp + "  ", "ts")
        self.text.insert("end", level.rjust(5) + "  ", {
            "OK": "ok", "WARN": "warn", "ERROR": "err", "CAL": "cal",
        }.get(level, "info"))
        self.text.insert("end", message + "\n", "info")
        self.text.configure(state="disabled")
        if self._autoscroll:
            self.text.see("end")

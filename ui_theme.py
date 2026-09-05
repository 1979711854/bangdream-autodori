# -*- coding: utf-8 -*-
"""autodori GUI 主题令牌（Maa / SukiUI 风格）

纯 Tkinter 自绘方案的设计令牌集中在这里，切换主题只需切换字典。
组件在构造时取色；整体换肤由 gui.py 重建界面完成（保证状态不丢）。
"""
import os

LIGHT = {
    "app_bg": "#F3F4F7",
    "surface": "#FFFFFF",
    "panel": "#F7F8FB",      # 侧栏卡片底色,比 surface 略深一档
    "surface_alt": "#F7F8FA",
    "surface_sunken": "#EEF0F3",
    "border": "#E4E6EA",
    "border_strong": "#D3D1C7",
    "text": "#20242B",
    "text_2": "#6B7280",
    "text_3": "#9AA0AA",
    "accent": "#2E7BD6",
    "accent_hover": "#3D8AE6",
    "accent_soft": "#E6F1FB",
    "accent_text": "#185FA5",
    "ok": "#3B6D11",
    "warn": "#854F0B",
    "danger": "#A32D2D",
    "idle": "#B4B2A9",
    "hover": "#F1EFE8",
    "log_bg": "#FFFFFF",
    "shadow": "#D8DBE0",
}

DARK = {
    "app_bg": "#17191D",
    "surface": "#212429",
    "panel": "#1B1E22",      # 侧栏卡片底色,比 surface 略深
    "surface_alt": "#1C1F24",
    "surface_sunken": "#14161A",
    "border": "#2E3239",
    "border_strong": "#3A3F47",
    "text": "#E6E8EC",
    "text_2": "#9AA0AA",
    "text_3": "#6E747E",
    "accent": "#4C8FF5",
    "accent_hover": "#5C9DFF",
    "accent_soft": "#1B3149",
    "accent_text": "#8FBEFF",
    "ok": "#7FB24A",
    "warn": "#D2A03C",
    "danger": "#E46C6C",
    "idle": "#5A6068",
    "hover": "#282C33",
    "log_bg": "#1A1D21",
    "shadow": "#0F1114",
}

THEMES = {"light": LIGHT, "dark": DARK}

_FONT = "Microsoft YaHei UI"
_MONO = "Consolas"

_state = {"name": "light"}


def set_theme(name):
    if name in THEMES:
        _state["name"] = name


def theme_name():
    return _state["name"]


def get():
    return THEMES[_state["name"]]


def is_dark():
    return _state["name"] == "dark"


def font(size=10, weight="normal"):
    return (_FONT, size, weight)


def mono(size=10):
    return (_MONO, size)


def enable_dpi_awareness():
    """Windows 高 DPI 下 Tk 默认不感知，字体会模糊。必须在建 Tk() 前调用。"""
    if os.name != "nt":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll

            windll.user32.SetProcessDPIAware()
        except Exception:
            pass

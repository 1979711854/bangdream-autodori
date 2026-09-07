# -*- coding: utf-8 -*-
"""打歌环境自检: 找出会导致时基漂移/掉判定的环境配置问题。

背景(实测结论):
- 游戏判定时钟锚定在音频轨上。MuMu 开启「禁用安卓系统声音」
  (shell_config.json 的 renderer.audio_out_hardware_off = "1")会让音频管道
  不推进, 游戏时钟相对真实时间漂移 → 开局正常、越打越偏。这是第一杀手。
- Windows「平衡/节能」电源计划会动态调节 CPU 频率, 打歌中偶发长停顿 →
  批量 miss。高性能计划下明显好转。
- 模拟器帧率上限过低(fps_limit < 60)会放大判定量化与卡顿。

本模块只做读取与诊断, 不修改任何配置(避免动用户的模拟器设置)。
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
import re
import subprocess
from pathlib import Path

# Windows 电源计划 GUID(常见三种 + 卓越性能)
POWER_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "平衡",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "高性能",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "节能",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "卓越性能",
}
_GOOD_PLANS = {"高性能", "卓越性能"}


def _mumu_config_path(emulator_path) -> Path | None:
    """定位 MuMu 实例配置 (vms/*/configs/shell_config.json)。"""
    try:
        base = Path(str(emulator_path))
        candidates = list(base.glob("vms/*/configs/shell_config.json"))
        if not candidates:
            return None
        # 多开时取第一个(与 device.index 无法直接映射, 仅用于诊断)
        return sorted(candidates)[0]
    except Exception:
        return None


def _read_mumu_config(emulator_path) -> dict | None:
    path = _mumu_config_path(emulator_path)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _memory_load_percent() -> int:
    class MEMSTAT(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPage", ctypes.c_ulonglong),
            ("ullAvailPage", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        stat = MEMSTAT()
        stat.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.dwMemoryLoad)
    except Exception:
        return -1


def active_power_plan() -> str:
    """返回当前电源计划名称; 无法获取时返回空串。"""
    try:
        raw = subprocess.check_output(
            ["powercfg", "/getactivescheme"], timeout=5
        )
    except Exception:
        return ""
    # 中文 Windows 下 powercfg 输出为 GBK, 不能直接按 utf-8 解码
    out = ""
    for enc in ("utf-8", "gbk"):
        try:
            out = raw.decode(enc)
            break
        except Exception:
            continue
    if not out:
        out = raw.decode("utf-8", "ignore")
    match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        out,
    )
    if match:
        guid = match.group(1).lower()
        return POWER_PLANS.get(guid, "自定义(%s)" % guid[:8])
    return out.strip()


def check(emulator_path=None) -> list[tuple[str, str]]:
    """返回 [(级别, 说明)], 级别: ERROR/WARN/INFO。"""
    findings: list[tuple[str, str]] = []

    # 1. 音频(时基漂移第一杀手)
    cfg = _read_mumu_config(emulator_path)
    if cfg is None:
        findings.append(
            ("INFO", "未能读取模拟器配置, 跳过音频/帧率自检")
        )
    else:
        renderer = cfg.get("renderer", {}) if isinstance(cfg, dict) else {}
        audio_off = str(renderer.get("audio_out_hardware_off", "0")).strip()
        if audio_off in ("1", "true", "True"):
            findings.append(
                (
                    "ERROR",
                    "模拟器开启了「禁用安卓系统声音」——音频管道不推进会导致游戏"
                    "判定时钟漂移(开局正常、越打越偏)。请在 MuMu 设置→声音中关闭"
                    "该选项后重启模拟器。",
                )
            )
        fps_limit = renderer.get("fps_limit")
        try:
            fps = int(float(fps_limit)) if fps_limit is not None else 0
        except (TypeError, ValueError):
            fps = 0
        if 0 < fps < 60:
            findings.append(
                (
                    "WARN",
                    f"模拟器帧率上限 {fps}fps(<60), 判定量化与卡顿会被放大, "
                    "建议在 MuMu 设置→性能中提高到 60 或更高。",
                )
            )

    # 2. Windows 电源计划
    plan = active_power_plan()
    if plan and plan not in _GOOD_PLANS and not plan.startswith("自定义"):
        findings.append(
            (
                "WARN",
                f"Windows 电源计划为「{plan}」, CPU 频率动态调节会造成打歌中偶发"
                "长停顿并批量 miss。建议改用「高性能」或「卓越性能」。",
            )
        )
    elif plan:
        findings.append(("INFO", f"Windows 电源计划: {plan}"))

    # 3. 内存压力
    load = _memory_load_percent()
    if load >= 85:
        findings.append(
            (
                "WARN",
                f"系统内存占用 {load}%, 内存压力过高会触发换页与卡顿, "
                "建议关闭其他程序后再打歌。",
            )
        )

    # 4. CPU 核数
    cores = os.cpu_count() or 0
    if 0 < cores < 4:
        findings.append(
            ("WARN", f"CPU 仅 {cores} 核, 难以同时承载模拟器与脚本。")
        )

    return findings

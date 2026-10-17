# -*- coding: utf-8 -*-
"""打歌环境自检: 找出会导致时基漂移/掉判定的环境配置问题。

背景(实测结论):
- 游戏判定时钟锚定在音频轨上。MuMu 开启「禁用安卓系统声音」
  (shell_config.json 的 renderer.audio_out_hardware_off = "1")会让音频管道
  不推进, 游戏时钟相对真实时间漂移 → 开局正常、越打越偏。这是第一杀手。
- 模拟器帧率上限过低(fps_limit < 60)会放大判定量化与卡顿。

2026-10-17:删掉「Windows 电源计划」自检项。用户实测反馈"作用不大"——
提示只说明了原理,既不能自动修正,又每次启动刷一条 WARN。

本模块只做读取与诊断, 不修改任何配置(避免动用户的模拟器设置)。
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
from pathlib import Path


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
        # 真实生效帧率优先取 fps_limit_real: 高帧率模式下它与 fps_limit 不同
        # (如 fps_limit=45 实际渲染 90), 缺失时退回 fps_limit
        fps_real = renderer.get("fps_limit_real")
        fps_base = renderer.get("fps_limit")
        try:
            if fps_real not in (None, "", "0"):
                fps = int(float(fps_real))
            elif fps_base is not None:
                fps = int(float(fps_base))
            else:
                fps = 0
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

    # 2. 内存压力
    load = _memory_load_percent()
    if load >= 85:
        findings.append(
            (
                "WARN",
                f"系统内存占用 {load}%, 内存压力过高会触发换页与卡顿, "
                "建议关闭其他程序后再打歌。",
            )
        )

    # 3. CPU 核数
    cores = os.cpu_count() or 0
    if 0 < cores < 4:
        findings.append(
            ("WARN", f"CPU 仅 {cores} 核, 难以同时承载模拟器与 MAA。")
        )

    return findings

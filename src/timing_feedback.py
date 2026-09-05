# -*- coding: utf-8 -*-
"""打歌中判定反馈闭环（移植自 MaaBanGDream v1.2.0 的 timing_feedback）。

原理：游戏在每次判定（GREAT/GOOD/BAD）时会在判定文字正下方显示 FAST/SLOW
彩条（FAST=亮天蓝，SLOW=亮橙）。直接读取游戏自己的判定反馈，比任何视觉
推算都可靠。控制器以极小步长（±1ms）、长冷却（2s）、硬上限（±12ms）调整
打歌时基——只修正常量/缓变偏差，绝不追逐瞬时波动，天然不会振荡。

与上游的差异：输入帧为 RGB（MuMu IPC 截帧），HSV 转换用 COLOR_RGB2HSV。
"""
from __future__ import annotations

from collections import Counter, deque
from enum import Enum

import cv2
import numpy as np


class TimingFeedback(str, Enum):
    FAST = "fast"
    SLOW = "slow"


class TimingFeedbackDetector:
    """读取判定文字正下方的 FAST/SLOW 彩条（720p 基准坐标）。"""

    # 判定条实测几何（MaaBanGDream 实测）：位于判定文字正下方
    # （GREAT 下方约 y 514-556），FAST 为亮天蓝（H≈102-110），SLOW 为亮橙（H≈11）。
    ROI = (570, 514, 710, 556)
    MIN_COLOURED_PIXELS = 600
    # 判定条常只持续 2-3 帧且中间会闪断 1 帧；最近 3 帧内同色出现
    # ≥2 帧即报告，容忍闪烁，拒绝孤立单帧的过线音符。
    PERSISTENCE_FRAMES = 2

    def __init__(self) -> None:
        self._recent: deque[TimingFeedback | None] = deque(maxlen=3)
        self._armed = False
        self.sightings = 0
        self.reports = 0

    def detect(self, image: np.ndarray) -> TimingFeedback | None:
        if not isinstance(image, np.ndarray) or image.shape[:2] != (720, 1280):
            return None
        x1, y1, x2, y2 = self.ROI
        hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2HSV)
        slow = int(np.count_nonzero(cv2.inRange(
            hsv, (0, 140, 160), (25, 255, 255),
        )))
        fast = int(np.count_nonzero(cv2.inRange(
            hsv, (95, 160, 160), (120, 255, 255),
        )))
        kind: TimingFeedback | None = None
        if slow >= self.MIN_COLOURED_PIXELS and slow >= fast * 2:
            kind = TimingFeedback.SLOW
        elif fast >= self.MIN_COLOURED_PIXELS and fast >= slow * 2:
            kind = TimingFeedback.FAST
        self._recent.append(kind)
        if kind is not None:
            self.sightings += 1
        same = [seen for seen in self._recent if seen == kind and kind is not None]
        # 每个判定条周期只上报一次（armed）；条彻底消失后才允许下一次上报。
        if (
            kind is not None
            and not self._armed
            and len(same) >= self.PERSISTENCE_FRAMES
        ):
            self._armed = True
            self.reports += 1
            return kind
        if kind is None and not any(self._recent):
            self._armed = False
        return None


class AdaptiveTimingController:
    """基于去抖反馈的有界局内时基修正（正值=按更早）。"""

    def __init__(
        self,
        initial_offset_ms: int,
        *,
        step_ms: int = 1,
        unanimous_step_ms: int | None = None,
        minimum_samples: int = 12,
        imbalance: int = 8,
        window_size: int = 16,
        maximum_live_adjustment_ms: int = 12,
        adjustment_cooldown_seconds: float = 2.0,
    ) -> None:
        self.initial_offset_ms = int(initial_offset_ms)
        self.current_offset_ms = int(initial_offset_ms)
        self.step_ms = int(step_ms)
        self.unanimous_step_ms = (
            None if unanimous_step_ms is None else int(unanimous_step_ms)
        )
        self.minimum_samples = int(minimum_samples)
        self.imbalance = int(imbalance)
        self.maximum_live_adjustment_ms = int(maximum_live_adjustment_ms)
        self.adjustment_cooldown_seconds = float(adjustment_cooldown_seconds)
        self.fast_samples = 0
        self.slow_samples = 0
        self.valid_samples = 0
        self.ignored_samples = 0
        self._ignored_reasons: Counter[str] = Counter()
        self._samples: deque[TimingFeedback] = deque(maxlen=int(window_size))
        self._visible: TimingFeedback | None = None
        self._last_adjusted_at = float("-inf")

    def update(
        self,
        feedback: TimingFeedback | None,
        now: float,
        *,
        eligible: bool = True,
        ignored_reason: str = "ineligible",
    ) -> int | None:
        if feedback is None:
            self._visible = None
            return None
        if feedback == self._visible:
            return None
        self._visible = feedback
        if not eligible:
            self.ignored_samples += 1
            self._ignored_reasons[ignored_reason] += 1
            return None
        self.valid_samples += 1
        self._samples.append(feedback)
        if feedback is TimingFeedback.FAST:
            self.fast_samples += 1
        else:
            self.slow_samples += 1

        if len(self._samples) < self.minimum_samples:
            return None
        error = (
            sum(sample is TimingFeedback.SLOW for sample in self._samples)
            - sum(sample is TimingFeedback.FAST for sample in self._samples)
        )
        if abs(error) < self.imbalance:
            return None
        if now - self._last_adjusted_at < self.adjustment_cooldown_seconds:
            return None

        direction = 1 if error > 0 else -1
        # 窗口内全部同向说明信号一致，可放大步长；混合窗口用小步长，
        # 降低个别误检把偏移推反的风险。
        step = self.step_ms
        if (
            self.unanimous_step_ms is not None
            and abs(error) == len(self._samples)
        ):
            step = self.unanimous_step_ms
        lower = max(-250, self.initial_offset_ms - self.maximum_live_adjustment_ms)
        upper = min(250, self.initial_offset_ms + self.maximum_live_adjustment_ms)
        adjusted = max(
            lower,
            min(upper, self.current_offset_ms + direction * step),
        )
        self._samples.clear()
        if adjusted == self.current_offset_ms:
            return None
        self.current_offset_ms = adjusted
        self._last_adjusted_at = float(now)
        return adjusted

    @property
    def ignored_reasons(self) -> dict[str, int]:
        return dict(self._ignored_reasons)

# -*- coding: utf-8 -*-
"""诊断录屏: 打歌过程中以 ~20fps 抓帧存 JPEG, 供逐帧检查音符/触点/判定对齐。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import cv2
import numpy as np
import player

OUT = Path("debug/rec")
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.jpg"):
    old.unlink()

p = player.Player("mumuv5", Path(r"D:/MuMu Player 12"), 0)
print("waiting for gameplay (judgement line appears)...", flush=True)

# 等待进入打歌画面: 判定线区域 (y=590) 变亮说明 note 轨道出现
def in_game():
    frame = p.ipc_capture_display()
    return float(frame[585:600, 200:1080].mean()) > 60

while not in_game():
    time.sleep(0.5)

print("gameplay detected, capturing...", flush=True)
t0 = time.perf_counter()
i = 0
DUR = 100  # 秒
while time.perf_counter() - t0 < DUR:
    frame = p.ipc_capture_display()  # RGB
    ts = time.perf_counter() - t0
    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(OUT / f"f{i:05d}_{ts:.3f}.jpg"), bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
    i += 1
    time.sleep(0.045)
print(f"done, {i} frames")

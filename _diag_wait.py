# -*- coding: utf-8 -*-
"""诊断: minitouch EvATive7 服务端 wait 指令的计时精度。

发 50 个 wait(100ms)（理论总长 5000ms），用回调里的 cost（服务端实测耗时）
和墙钟分别对比，判断时间轴漂移是来自服务端 wait 走快，还是游戏侧时钟滞后。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from minitouchpy import (
    MNT,
    CommandBuilder,
    MNTEvent,
    MNTEventData,
    MNTServerCommunicateType,
)

ADB = r"D:\MuMu Player 12\nx_main\adb.exe"
ADDR = "127.0.0.1:16384"
N = 50
WAIT_MS = 100

waits = []  # (requested_ms, cost)
t0 = None
t_end = None


def cb(event: MNTEvent, data: MNTEventData):
    global t_end
    if event == MNTEvent.EVATIVE7_LOG:
        cmd = data.cmd.split(" ")[0]
        if cmd == "w":
            waits.append((int(data.cmd.split(" ")[-1]), data.cost))
        t_end = time.perf_counter()


mnt = MNT(
    ADDR,
    type_="EvATive7",
    communicate_type=MNTServerCommunicateType.STDIO,
    mnt_asset_path=Path("./assets/minitouch_EvATive7"),
    callback=cb,
    adb_executor=ADB,
)
print("MNT connected.")

b = CommandBuilder()
for _ in range(N):
    b.commit()
    b.wait(WAIT_MS)

t0 = time.perf_counter()
b.publish(mnt, block=True)
wall = time.perf_counter() - t0

time.sleep(0.5)  # 收尾回调

n = len(waits)
total_req = sum(r for r, _ in waits)
total_cost = sum(c for _, c in waits)
print(f"callbacks: {n}/{N}")
print(f"requested total : {total_req} ms")
print(f"server cost total: {total_cost:.1f} ms  (diff {total_cost - total_req:+.1f} ms, {(total_cost/total_req-1)*100:+.3f}%)")
print(f"wall elapsed    : {wall*1000:.1f} ms  (diff {wall*1000 - total_req:+.1f} ms, {(wall*1000/total_req-1)*100:+.3f}%)")
if n:
    costs = [c for _, c in waits]
    print(f"per-wait cost: min={min(costs):.2f} max={max(costs):.2f} mean={total_cost/n:.3f} ms")

mnt.stop()

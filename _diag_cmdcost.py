# -*- coding: utf-8 -*-
"""诊断: d/m/u/c 指令的服务端执行开销是否侵蚀 wait 时间轴。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from minitouchpy import MNT, CommandBuilder, MNTEvent, MNTEventData, MNTServerCommunicateType

ADB = r"D:\MuMu Player 12\nx_main\adb.exe"
ADDR = "127.0.0.1:16384"

costs = {"w": [], "d": [], "u": [], "m": [], "c": []}

def cb(event: MNTEvent, data: MNTEventData):
    if event == MNTEvent.EVATIVE7_LOG:
        cmd = data.cmd.split(" ")[0]
        if cmd in costs:
            costs[cmd].append(data.cost)

mnt = MNT(ADDR, type_="EvATive7", communicate_type=MNTServerCommunicateType.STDIO,
          mnt_asset_path=Path("./assets/minitouch_EvATive7"), callback=cb, adb_executor=ADB)
print("MNT connected.")

# 模拟真实打歌流: 每 100ms 内塞 8 个 down/up + commit (≈高难度谱面密度)
b = CommandBuilder()
for i in range(30):
    for f in range(1, 5):
        b.down(f, 100 + f * 100, 500, 1)
        b.up(f)
    b.commit()
    b.wait(100)

t0 = time.perf_counter()
b.publish(mnt, block=True)
wall = time.perf_counter() - t0
time.sleep(0.3)

req = 30 * 100
tot_w = sum(costs["w"])
tot_touch = sum(sum(costs[k]) for k in "dumc")
print(f"wall={wall*1000:.0f}ms  wait_requested={req}ms  wait_cost_total={tot_w:.0f}ms")
for k in "dumc":
    v = costs[k]
    if v:
        print(f"  {k}: n={len(v)} mean={sum(v)/len(v):.3f}ms max={max(v):.2f}ms")
print(f"touch overhead total={tot_touch:.0f}ms, wall-wait={(wall*1000 - tot_w):.0f}ms")
mnt.stop()

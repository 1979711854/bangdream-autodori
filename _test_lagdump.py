# -*- coding: utf-8 -*-
"""临时自检:_dump_server_lag_async 的重建+对齐路径,用合成 jlog 验证。
注入已知误差(每 wait +0.07ms 超时、60% 处一次 50ms 停顿),
预期日志里的「曲内增长」≈ n_w*0.07 + 50 + 命令间隙总和。用完即删。"""
import logging
import random
import sys
import time

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")

import autodori  # noqa: E402
from minitouchpy import MNTEvATive7LogEventData  # noqa: E402

random.seed(42)

# 构造动作流:400 个音符(d+u),间隔不等,含 <0.5ms 的相邻贴脸间隔和 finger=None
actions = []
t = 1500.0
for i in range(400):
    finger = None if i == 137 else 0
    actions.append({"type": "down", "time": t, "finger": finger, "index": len(actions)})
    if i % 4 == 0:  # 绿条切片(move),覆盖 'm'
        actions.append({"type": "move", "time": t + 30, "finger": finger, "index": len(actions)})
    actions.append({"type": "up", "time": t + 60, "finger": finger, "index": len(actions)})
    gap = random.choice([0.0004, 0.4, 3.0, 80.0, 250.0])  # ms
    if gap > 0.001:
        actions.append({"type": "wait", "time": t + 60, "length": gap, "index": len(actions)})
        t = t + 60 + gap

# 模拟实际发射序列(与 actions_to_MNTcmd/publish 一致)与服务端执行时刻
events = []
t_srv = 0.0
stall_at = 0.6 * len(actions)
n_w = 0
gap_total = 0.0
CMD_SLICE = 100
for start in range(0, len(actions), CMD_SLICE):
    chunk = actions[start : start + CMD_SLICE]
    for idx, a in enumerate(chunk):
        at = a["type"]
        if at == "wait":
            if round(a["length"]) > 0.01:
                r = round(a["length"])
                for cmd in (f"c", f"w {r}"):
                    g = random.uniform(0.005, 0.02)
                    gap_total += g
                    cost = g
                    if cmd.startswith("w"):
                        cost = r + 0.07 + random.uniform(-0.02, 0.02)
                        n_w += 1
                    if len(actions) - (start + idx) < (1 - 0.6) * len(actions):
                        pass
                    events.append(MNTEvATive7LogEventData(t_srv, t_srv + cost, cost, cmd))
                    t_srv += cost
                    if cmd == "w" and abs((start + idx) - stall_at) < 2:
                        t_srv += 50.0  # 注入 50ms 停顿
        elif a.get("finger", None) is None:
            continue
        else:
            # 事件命令用 minitouch 指令字母(d/m/u),与真实 jlog 一致;
            # 动作流仍是类型名(down/move/up)—— 覆盖 2026-10-21 的命名错位回归。
            g = random.uniform(0.02, 0.04)
            gap_total += g
            letter = {"down": "d", "move": "m", "up": "u"}[at]
            events.append(MNTEvATive7LogEventData(t_srv, t_srv + g, g, f"{letter} 0 540 300 1"))
            t_srv += g

with autodori.cmd_log_list_lock:
    autodori.cmd_log_list.clear()
    autodori.cmd_log_list.extend(events)

print(f"注入: waits={n_w}, 命令间隙总和≈{gap_total:.1f}ms, 预期增长≈{n_w*0.07+gap_total+50:.1f}ms")
autodori._dump_server_lag_async("TEST", actions)
time.sleep(3)

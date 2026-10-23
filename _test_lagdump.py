# -*- coding: utf-8 -*-
"""临时自检:_dump_server_lag_async 的重建+对齐路径,用合成 jlog 验证。
注入已知误差(每 wait +0.07ms 超时、60% 处一次 50ms 停顿、逐命令间隙),
断言三件事:
  1. 汇总行「曲内增长」≈ n_w*0.07 + 全部间隙 + 50;
  2. 音符组(d/m/u)与 gap 组(wait)的曲内增长各自与注入值吻合
     (2026-10-22 扩展:两组分列是 SLOW/FAST 归属判读的依据);
  3. 曲线 JSON 落盘 debug/lagdump/ 且 meta 计数与注入一致。
用完即删。"""
import glob
import json
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

# 模拟实际发射序列(与 actions_to_MNTcmd/publish 一致)与服务端执行时刻。
# 每条命令前都注入随机间隙(服务端空闲,计入 lag 累计),wait 另注入 +0.07ms
# 超时,60% 位置附近的一条 wait 之后注入 50ms 停顿(服务端卡顿,其后所有
# 命令的 lag 整体抬高 50ms —— 单一时间线,两组增长都应包含它)。
events = []
t_srv = 0.0
stall_at = 0.6 * len(actions)
n_w = 0
gap_total = 0.0
gap_note = 0.0   # d/m/u 命令前的间隙累计
gap_wait = 0.0   # w 命令前的间隙累计
stall_injected = 0
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
                        gap_wait += g
                    if len(actions) - (start + idx) < (1 - 0.6) * len(actions):
                        pass
                    events.append(MNTEvATive7LogEventData(t_srv, t_srv + cost, cost, cmd))
                    t_srv += cost
                    if (not stall_injected and cmd.startswith("w")
                            and abs((start + idx) - stall_at) < 5):
                        t_srv += 50.0  # 注入 50ms 停顿(其后所有命令滞后 +50)
                        stall_injected += 1
        elif a.get("finger", None) is None:
            continue
        else:
            # 事件命令用 minitouch 指令字母(d/m/u),与真实 jlog 一致;
            # 动作流仍是类型名(down/move/up)—— 覆盖 2026-10-21 的命名错位回归。
            g = random.uniform(0.02, 0.04)
            gap_total += g
            gap_note += g
            letter = {"down": "d", "move": "m", "up": "u"}[at]
            events.append(MNTEvATive7LogEventData(t_srv, t_srv + g, g, f"{letter} 0 540 300 1"))
            t_srv += g

# 经真实 mnt_callback 注入:同时驱动 cmd_log_list 与服务端滞后在线估计器
# (_lag_fb,反馈补偿的测量端)。第四版估计器=绝对对齐口径(lag=start-ideal,
# 与 dump 同口径),合成场景批间无空闲,两口径数值一致。
# 估计器的 wait 理想时长来自 current_chart.wait_ideal_ms(生成 wait 命令时
# 入队;合成场景里 emitted wait 的理想值 = length 本身,先全部预填 ——
# 真实流程里命令生成领先服务端执行数秒,队列先满后空是常态)。
import collections

ideals = [a["length"] for a in actions
          if a["type"] == "wait" and round(a["length"]) > 0.01]


class _FakeChart:
    def __init__(self, ideals):
        self.wait_ideal_ms = collections.deque(ideals)


autodori.reset_callback_data()
autodori.reset_lag_feedback()
autodori.current_chart = _FakeChart(ideals)
for ev in events:
    autodori.mnt_callback(autodori.MNTEvent.EVATIVE7_LOG, ev)
autodori.current_chart = None

# 滞后是单一时间线的累计:wait 超时、停顿与其前的全部命令间隙都会传导给后续
# 所有命令,所以音符组/gap 组的「曲尾滞后」都含 n_w*0.07 + 间隙 + 停顿。
# 两组的差异只在采样位置(gap 组末样本是最后一条 wait,不含其后尾段间隙的
# 累计),所以预期都向「全命令增长」靠,gap 组容差再放宽一档。
STALL = 50.0 if stall_injected else 0.0
exp_all = n_w * 0.07 + gap_total + STALL
exp_note = n_w * 0.07 + gap_note + STALL
print(f"注入: waits={n_w}, 间隙总和≈{gap_total:.1f}(音符前{gap_note:.1f}/wait前{gap_wait:.1f}), "
      f"停顿={STALL}ms")
print(f"预期: 全命令增长≈{exp_all:.1f} 音符增长≈{exp_note:.1f} gap增长≈{exp_all:.1f}")
autodori._dump_server_lag_async("TEST", actions)
time.sleep(3)

# ---- 断言(与肉眼比对并存,数值断言防回归;容差放宽到 max(20%, 5ms)) ----
def ok(actual, expected, label):
    tol = max(0.2 * abs(expected), 5.0)
    if label.startswith("gap"):
        tol = max(0.25 * abs(expected), 12.0)  # 采样位置差:末条 wait 后的间隙不计入
    assert abs(actual - expected) <= tol, f"{label}: 实际{actual:.1f} 预期{expected:.1f}"
    print(f"PASS {label}: {actual:.1f} (预期≈{expected:.1f})")

files = sorted(glob.glob("debug/lagdump/*-TEST.json"))
assert files, "lagdump JSON 未落盘"
with open(files[-1], encoding="utf-8") as f:
    dump = json.load(f)
meta = dump["meta"]
assert meta["song"] == "TEST"
assert meta["note_cmds"] > 0 and meta["wait_cmds"] == n_w, (
    f"meta 计数不对: note={meta['note_cmds']} wait={meta['wait_cmds']} n_w={n_w}"
)
assert len(dump["events"]) == meta["events"] > 0
# 事件三元组结构: [cmd, ideal_ms, lag_ms],lag 全部为有限数
for c, ideal, lag in dump["events"][:10]:
    assert c in ("d", "m", "u", "w") and 0 <= ideal and -1e4 < lag < 1e4
ok(meta["note_growth_ms"], exp_note, "音符曲内增长")
ok(meta["gap_growth_ms"], exp_all, "gap曲内增长(≈全命令,容差更宽)")
print(f"PASS 曲线落盘: {files[-1]} (events={meta['events']})")

# 在线估计器(_lag_fb)应与离线 dump 同口径(第四版绝对对齐):
# 当前残差 ≈ raw 曲线终点(= exp_all + 首批位置百分位前的少量累计,
# 容差同上)。
fb = autodori._lag_fb
ok(fb["lag"], exp_all, "在线估计器 lag(绝对对齐)")
assert fb["ideal"] is not None, "绝对口径下 ideal 指针应已建立"
# 重置函数
autodori.reset_lag_feedback()
assert autodori._lag_fb["lag"] == 0.0 and autodori._lag_fb["last_start"] is None
print("PASS reset_lag_feedback")

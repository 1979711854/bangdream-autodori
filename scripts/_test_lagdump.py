# -*- coding: utf-8 -*-
"""滞后诊断回归:_dump_server_lag_async 的重建/对齐口径。

2026-10-21 建;2026-10-29 改口径后重写(现场 #684 / #763)。
改口径的原因:
  旧版把 'w' 也放进理想流,判据是「round(length) > 0.01 才发」;但真实发的
  是补偿后的 wait(actions_to_MNTcmd 里先被 _a2c_offset 排水、再被
  _a2c_rounded_loss 校正),贴着门槛的间隙会被抽成 0 而整条不发 → 理想流有、
  实际流没有,类型序列从那里开始错位。这个机制下面场景 B 可复现。
  注意它**不足以解释现场量级**:离线用真实谱面(#684 special)重算,整首只有
  88 条 wait 会少发(理想 8473),而当日 dump 报 8926/28010(32%)。所以那个
  数还有别的来源,当时没有 jlog 证据没法定位 —— 场景 C 就是「真·缺命令」的
  形态,改口径后只有它会报错位,变量因此少一个。
改法(P1):只按 d/m/u 对齐,'w' 两边都跳过;wait 改成统计「应发/实发」条数,
  差值就是被排水抽干或取整丢弃的条数(P3 判时序要用这个数)。
改法(P2):对齐失败时把前 N 条「实际 vs 理想」对照落盘 debug/lagdump/。

三个合成场景(动作流 → jlog 事件在本文件里生成,不连设备、不读谱面缓存):
  A 正常局:注入每命令间隙 + wait 超时 + 一次 50ms 停顿,断言曲尾滞后 = 注入漂移;
  B 排水局:三成 wait 被抽干(整条不发)→ 旧口径会算成错位,现在必须 miss=0;
  C 真错位:再丢 1/20 的判定点命令 → 必须报错位并落盘样本。

跑法:.venv/Scripts/python.exe scripts/_test_lagdump.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)
import glob
import json
import logging
import os
import random
import sys
import time

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(message)s")

import autodori  # noqa: E402
from minitouchpy import MNTEvATive7LogEventData  # noqa: E402

PASS = 0
FAIL = 0


def ok(label, actual, expected, ratio=0.2, floor=5.0):
    """带容差断言(容差 = max(ratio*|expected|, floor),与旧版同口径)。"""
    global PASS, FAIL
    tol = max(ratio * abs(expected), floor)
    if abs(actual - expected) <= tol:
        PASS += 1
        print(f"  PASS  {label}: {actual:.1f} (预期≈{expected:.1f})")
    else:
        FAIL += 1
        print(f"  FAIL  {label}: {actual:.1f} (预期≈{expected:.1f}, 容差 {tol:.1f})")


def eq(label, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  PASS  {label} -> {actual}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} -> {actual} (want {expected})")


def build_actions(notes=400, seed=42,
                  holes=(0.0004, 0.4, 0.6, 1.2, 3.0, 80.0, 250.0)):
    """按真实谱面生成器的方式造动作流:相邻两个动作之间必有一条 wait。

    _test 早期的版本只在音符组之后插 wait,音符内部的 20/60ms 间距没有对应的
    wait —— 那种时间线里理想流推进得比真实谱面少,量出来的漂移会偏小,
    期望值只能靠猜。这里让 wait 覆盖全部间隔,断言就能算准。
    """
    rnd = random.Random(seed)
    plan = []
    t = 1500.0
    for i in range(notes):
        finger = None if i == 137 else 0     # 5 指不够的局:解算层给 None
        plan.append(("down", t, finger))
        if i % 4 == 0:                        # 绿条切片,覆盖 'm'
            plan.append(("move", t + 30, finger))
            plan.append(("up", t + 60, finger))
            t += 60
        else:
            plan.append(("up", t + 20, finger))
            t += 20
        t += rnd.choice(holes)

    acts = []
    prev = None
    for typ, at, finger in plan:
        if prev is not None and at > prev:
            acts.append({"type": "wait", "time": prev, "length": at - prev,
                         "index": len(acts)})
        acts.append({"type": typ, "time": at, "finger": finger,
                     "index": len(acts)})
        prev = at
    return acts


def ideal_stream(acts):
    """与 _dump_server_lag_async 一致:只收 d/m/u,'w' 只计数不进对齐流。"""
    act_to_cmd = {"down": "d", "move": "m", "up": "u"}
    t0 = acts[0]["time"]
    ideal = []
    t = t0
    n_wait = 0
    for a in acts:
        if a["type"] == "wait":
            if round(a["length"]) > 0.01:
                n_wait += 1
            t += a["length"]
        elif a.get("finger", None) is None:
            continue
        else:
            ideal.append((act_to_cmd[a["type"]], t))
    return ideal, t0, n_wait


def build_events(acts, seed=7, dry_every=0, dry_max=1.5, drop_note_every=0,
                 stall_ratio=0.6, stall_ms=50.0):
    """生成 jlog 事件 + 精确漂移账本。

    dry_every=N:每第 N 条**可被抽干**的 wait(长度 ≤ dry_max)整条不发 —— 真实
     补偿每次最多抽 1ms(OFFSET_LIMIT = max(1, wait_offset+0.5)),只能吃掉
     这类贴着取整门槛的间隙,不会吃 80/250ms 的大空档。抽 20ms 以上的会让
     滞后算成 -14s,那不是现场形态。
    drop_note_every=N:每第 N 个判定点命令没发(真·命令流缺失);
    stall_ratio/stall_ms:在该进度处插一次服务端停顿。

    漂移账本:ideal_now 按理想流推进(每条 wait 都加 length,与实际发没发无关),
    某条命令的滞后 = t_srv − (ideal_now − t0)。最后一个判定点命令的滞后可以
    直接算出来,断言不用估;被抽干/被丢弃的 wait 也自然体现在里面(理想前进、
    实际没前进 → 滞后变小)。
    """
    rnd = random.Random(seed)
    act_to_cmd = {"down": "d", "move": "m", "up": "u"}
    t0 = acts[0]["time"]
    stall_at = stall_ratio * len(acts)
    evs = []
    t_srv = 0.0
    ideal_now = t0
    drift_at_note = []    # 每条判定点命令开始时刻的滞后
    n_wait_dried = 0
    n_wait_emitted = 0
    n_wait_seen = 0
    n_note_seen = 0
    stall_injected = False

    for idx, a in enumerate(acts):
        if a["type"] == "wait":
            ideal_now += a["length"]
            if round(a["length"]) <= 0.01:
                continue
            if a["length"] <= dry_max:
                n_wait_seen += 1
                if dry_every and n_wait_seen % dry_every == 0:
                    n_wait_dried += 1  # 补偿把这条抽成 0 → 整批不发
                    continue
            n_wait_emitted += 1
            r = round(a["length"])
            for cmd, cost in (("c", 0.01),
                              (f"w {r}", r + 0.07 + rnd.uniform(-0.02, 0.02))):
                evs.append(MNTEvATive7LogEventData(t_srv, t_srv + cost, cost, cmd))
                t_srv += cost
                if (not stall_injected and cmd.startswith("w")
                        and abs(idx - stall_at) < 5):
                    t_srv += stall_ms
                    stall_injected = True
        else:
            if a.get("finger", None) is None:
                continue
            # 用「见过几条」计数,不能用「发出去几条」—— dropped 的不增加后者,
            # 条件会一直成立,后面的命令会被全部丢掉(踩过)。
            n_note_seen += 1
            if drop_note_every and n_note_seen % drop_note_every == 0:
                continue               # 真·少发一条判定点命令
            drift_at_note.append(t_srv - (ideal_now - t0))
            cost = rnd.uniform(0.02, 0.04)
            letter = act_to_cmd[a["type"]]
            evs.append(MNTEvATive7LogEventData(
                t_srv, t_srv + cost, cost, f"{letter} 0 540 300 1"))
            t_srv += cost
    return {
        "events": evs,
        "drift_end": t_srv - (ideal_now - t0),    # 最后一条命令之后的滞后
        "drift_last_note": drift_at_note[-1],     # 最后一个判定点的滞后
        "n_wait_emitted": n_wait_emitted,
        "n_wait_dried": n_wait_dried,
        "stall": stall_ms if stall_injected else 0.0,
    }


def run_dump(label, acts, evs):
    """把事件灌进真实 mnt_callback,再触发 dump(它在线程里等静默后落盘)。"""
    autodori.reset_callback_data()
    autodori.reset_lag_feedback()
    # 先清掉本 label 的历史产物:debug/lagdump 会积累,上一次跑的文件会被当成
    # 本次结果(踩过 —— 场景 C 明明没落盘,却读到了上一轮的样本)。
    for old in glob.glob(f"debug/lagdump/*-{label}.json") + \
            glob.glob(f"debug/lagdump/*-{label}-misalign.txt"):
        try:
            os.remove(old)
        except OSError:
            pass
    # reset_callback_data() 只清偏移统计,不清 cmd_log_list —— 上一场景的命令会
    # 留在列表里,下一场 dump 快照到的条数与理想流对不上,全部判成错位。
    with autodori.cmd_log_list_lock:
        autodori.cmd_log_list.clear()
    autodori.current_chart = None      # dump 不读 wait_ideal_ms,只消费 jlog
    for ev in evs:
        autodori.mnt_callback(autodori.MNTEvent.EVATIVE7_LOG, ev)
    autodori._dump_server_lag_async(label, acts)
    time.sleep(2.5)                    # dump 至少要等 1s 静默
    autodori.current_chart = None


def load_json(label):
    files = sorted(glob.glob(f"debug/lagdump/*-{label}.json"))
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), files[-1]


def load_misalign(label):
    files = sorted(glob.glob(f"debug/lagdump/*-{label}-misalign.txt"))
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as f:
        return f.read(), files[-1]


# ==================== 场景 A:正常局(含停顿) ====================
print("=== 场景 A:正常局 ===")
acts_a = build_actions()
ideal_a, t0_a, n_wait_ideal_a = ideal_stream(acts_a)
sc_a = build_events(acts_a)
print(f"注入: 判定点 {len(ideal_a)} 条(动作 {len(acts_a)}),wait 应发 "
      f"{n_wait_ideal_a} 条,停顿 {sc_a['stall']}ms,总漂移≈{sc_a['drift_end']:.1f}ms")
run_dump("TEST", acts_a, sc_a["events"])

dump_a, path_a = load_json("TEST")
if dump_a is None:
    FAIL += 1
    print("  FAIL  场景A 未落盘 JSON")
else:
    meta_a = dump_a["meta"]
    eq("场景A 曲名", meta_a["song"], "TEST")
    eq("场景A 判定点命令数", meta_a["note_cmds"], len(ideal_a))
    eq("场景A wait 应发条数", meta_a["wait_ideal"], n_wait_ideal_a)
    eq("场景A wait 实发条数", meta_a["wait_actual"], sc_a["n_wait_emitted"])
    eq("场景A 正常局不该报错位", meta_a["miss"], 0)
    # 'w' 已退出对齐流,events 里只应有判定点命令
    bad = [c for c, _, _ in dump_a["events"] if c not in ("d", "m", "u")]
    eq("场景A events 只含 d/m/u", bad, [])
    # 最后一个判定点的滞后 = 注入漂移(绝对值口径,不靠估算)
    ok("场景A 曲尾滞后 = 注入漂移", dump_a["events"][-1][2],
       sc_a["drift_last_note"], ratio=0.15, floor=3.0)
    ok("场景A 音符曲内增长", meta_a["note_growth_ms"],
       sc_a["drift_last_note"], ratio=0.3, floor=15.0)
    print(f"  PASS  场景A 曲线落盘: {path_a}")

# 在线估计器(_lag_fb)是绝对对齐口径,但它的理想额度来自 chart.wait_ideal_ms
# 队列;合成场景里没有真实 chart,兜底走命令串实际值,且估计器比命令晚一拍
# 消费 sched,数值断言没有意义(实机口径见 scripts/_test_lagguard.py)。这里只确认
# 绝对对齐下指针建立、重置干净。
if autodori._lag_fb["ideal"] is None:
    FAIL += 1
    print("  FAIL  绝对口径下 ideal 指针应已建立")
else:
    PASS += 1
    print("  PASS  ideal 指针已建立")
autodori.reset_lag_feedback()
eq("reset_lag_feedback 清零 lag", autodori._lag_fb["lag"], 0.0)

# ==================== 场景 B:wait 被排水抽干 ====================
# 旧口径会把每条被抽干的 wait 记成一次错位(#684 的 32% 就是这么来的),
# 改口径后它只是「应发/实发」的差值,不该影响对齐。
print("=== 场景 B:三成 wait 被抽干 ===")
acts_b = build_actions()
sc_b = build_events(acts_b, dry_every=3)
print(f"抽干 {sc_b['n_wait_dried']} 条 wait(实发 {sc_b['n_wait_emitted']} 条)")
run_dump("DRY", acts_b, sc_b["events"])
dump_b, path_b = load_json("DRY")
if dump_b is None:
    FAIL += 1
    print("  FAIL  场景B 未落盘 JSON")
else:
    meta_b = dump_b["meta"]
    eq("场景B 抽干 wait 不算错位", meta_b["miss"], 0)
    eq("场景B 应发 wait 条数", meta_b["wait_ideal"],
       sc_b["n_wait_emitted"] + sc_b["n_wait_dried"])
    eq("场景B 实发 wait 条数", meta_b["wait_actual"], sc_b["n_wait_emitted"])
    eq("场景B 应发-实发 = 抽干条数",
       meta_b["wait_ideal"] - meta_b["wait_actual"], sc_b["n_wait_dried"])
    ok("场景B 抽干后仍能量出漂移", dump_b["events"][-1][2],
       sc_b["drift_last_note"], ratio=0.2, floor=5.0)
    print(f"  PASS  场景B 曲线落盘: {path_b}")

# ==================== 场景 C:真·命令流缺失 ====================
# 判定点命令少发(不是 wait),对齐必然失败:要报错位 + 落盘样本(P2)。
print("=== 场景 C:少发 1/10 判定点命令 ===")
acts_c = build_actions()
# 丢 1/10(约 90 条)才能越过 2% 的报错位门槛;丢太少会静默通过。
sc_c = build_events(acts_c, dry_every=3, drop_note_every=10)
run_dump("DESYNC", acts_c, sc_c["events"])
dump_c, _ = load_json("DESYNC")
text_c, path_c = load_misalign("DESYNC")
if text_c is None and dump_c is None:
    FAIL += 1
    print("  FAIL  场景C 既无 JSON 也无错位样本")
else:
    if dump_c is not None:
        meta_c = dump_c["meta"]
        if meta_c["miss"] == 0:
            FAIL += 1
            print("  FAIL  少发判定点命令却报 0 失配")
        else:
            PASS += 1
            print(f"  PASS  场景C 报出失配 {meta_c['miss']} 条")
    if text_c is not None:
        n_sam = text_c.count("] ideal_extra") + text_c.count("] actual_only")
        eq("场景C 样本含错位类型", n_sam > 0, True)
        eq("场景C 样本头带 wait 应发/实发", "无音区 wait" in text_c, True)
        eq("场景C 样本不超过 60 条",
           sum(1 for ln in text_c.splitlines() if ln.startswith("[")) <= 60, True)
        print(f"  PASS  场景C 错位样本: {path_c}")
    else:
        FAIL += 1
        print("  FAIL  场景C 未落盘错位样本(P2 未生效)")

# ---- 曲内漂移告警文案(2026-10-29) ----
print("\n== 曲内漂移告警 ==")
eq("提前 31ms -> 越打越早/FAST", autodori._drift_warning(-31.1),
   "本曲越打越早(累计 31ms),后半段可能出 FAST")
eq("延后 25ms -> 越打越晚/SLOW", autodori._drift_warning(25.0),
   "本曲越打越晚(累计 25ms),后半段可能出 SLOW")
eq("+5ms 不告警", autodori._drift_warning(5.0), None)
eq("-19.9ms 不告警", autodori._drift_warning(-19.9), None)
eq("None 不告警", autodori._drift_warning(None), None)

print(f"\n合计: PASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)

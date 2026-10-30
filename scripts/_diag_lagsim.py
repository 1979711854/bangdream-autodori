# -*- coding: utf-8 -*-
"""离线仿真:反馈式滞后补偿(issue #8 修复方向 A)的效果验证。

用现有 debug/lagdump/*.json 的实测曲线做重放仿真:
  - 每条命令的超额开销 e_i = lag_i - lag_{i-1}(内禀成本,重放时保持不变);
  - 反馈控制器:每批(CMD_SLICE_SIZE=100 事件)把「最新实测滞后」注入
    _a2c_offset 式累加器,之后每条 wait 按 ±1ms 饱和排水(与 chart.py
    OFFSET_LIMIT=1 的实机行为一致);
  - 可注入延迟模型(python 发布领先服务端 2~5s → 测量滞后 D ms)。

输出每首歌:原始 vs 仿真的 音符滞后增长/尾段均值/全程最大滞后。
判读:仿真后增长应 <10ms、最大滞后 <40ms(判定窗 60~70ms 的一半余量)。

用法: python scripts/_diag_lagsim.py [lagdump目录] [延迟ms,默认3000]

⚠ 结论局限(2026-10-23 FULL 曲 55ms 残差定位后补记):
lagdump 实测曲线本身是「反馈开着」的补偿后残差,不是裸漂移。本仿真把它
当裸曲线重放再叠一层反馈 = 双重补偿,所以「仿真压平」≠ 实机会好 ——
第二版实机 FULL 曲挂账 50ms 而仿真 -0.6ms 即此假象。仿真仍有的价值:
验证控制器不发散、排水不越界、参数不敏感;机理对错只能实机验证。
另外实机第三版测量基准已改为 wait 实际生效时长(chart.wait_sched_ms),
测量信号=裸漂移(不受补偿影响),与本仿真「meas 取自 raw」的结构终于同构。"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

CMD_SLICE = 100   # 与实机 CMD_SLICE_SIZE 一致
# 2026-10-23 加 LAGSIM_LIMIT 环境变量:对比 chart.py OFFSET_LIMIT 放宽后的排水效果。
# FULL 曲实测反馈版仍顶 50ms 平台(slow=274),疑因 OFFSET_LIMIT=1 每条 wait
# 最多消化 ±1ms、密集谱面 wait 稀疏排水跟不上;放宽到 3 先在此仿真验证再实机。
LIMIT = float(os.environ.get("LAGSIM_LIMIT", "1.0"))
#       ↑ 默认 1.0 与 chart.py OFFSET_LIMIT=1 的实机行为一致


def simulate(events, delay_ms=3000.0):
    """events: [(cmd, ideal_ms, lag_ms)] 原始实测(按 ideal 升序)。
    返回仿真后的 [(ideal, lag)] 与统计。"""
    # e_i = lag_i - lag_{i-1}:第 i 条命令的超额成本(重放中视为内禀量)
    # r_i:wait 的计划时长 = ideal_{i+1} - ideal_i(动作流中 wait 后必跟动作)
    n = len(events)
    r = [0.0] * n
    for i in range(n - 1):
        r[i] = max(0.0, events[i + 1][1] - events[i][1])

    raw = [0.0] * n            # 不补偿的累计滞后(应≈实测 lag)
    sim = [0.0] * n            # 仿真补偿后的累计滞后
    acc = 0.0                  # 反馈累加器(≈_a2c_offset 的反馈分量)
    last_injected = 0.0        # 上次注入时的「实测滞后」读数
    measured_raw = 0.0         # 原始时间线的累计滞后(=实测 lag 重放)
    j_meas = 0                 # freshest 可用测量的下标(延迟模型)
    shift = 0.0                # 已应用的累计提前量

    for i in range(n):
        cmd, ideal, lag = events[i]
        e = lag if i == 0 else lag - events[i - 1][2]
        raw[i] = measured_raw = measured_raw + e
        sim[i] = raw[i] - shift

        # 反馈注入:每 CMD_SLICE 条一批,读「D ms 之前」的实测滞后。
        # 上限 i:raw[] 是边重放边填充的,delay=0 时指针不允许越过当前事件,
        # 否则会读到尚未重放的 0(2026-10-23 delay=0 异常即此因)。
        if i % CMD_SLICE == 0:
            target_ideal = ideal - delay_ms
            while (j_meas + 1 <= i and j_meas + 1 < n
                   and events[j_meas + 1][1] <= target_ideal):
                j_meas += 1
            meas = raw[j_meas]
            acc += meas - last_injected
            last_injected = meas
            if os.environ.get("LAGSIM_TRACE") and i % (CMD_SLICE * 10) == 0:
                print("TRACE i=%d ideal=%.0f j=%d meas=%.1f acc=%.2f sim=%.1f"
                      % (i, ideal, j_meas, meas, acc, sim[i]))

        # wait 排水:±LIMIT 饱和,缩短不越过计划时长
        if cmd == "w":
            d = min(LIMIT, max(-LIMIT, acc))
            if d > 0:
                d = min(d, r[i])
            acc -= d
            shift += d
    return raw, sim


def stats(seq, events):
    n = len(seq)
    growth = seq[n - 1] - seq[max(0, int(n * 0.1))]
    tail = seq[int(n * 0.9):]
    return growth, sum(tail) / len(tail), max(seq), min(seq)


def main():
    lagdir = sys.argv[1] if len(sys.argv) > 1 else "debug/lagdump"
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 3000.0
    files = [f for f in sorted(glob.glob(os.path.join(lagdir, "*.json")))
             if "-TEST.json" not in f]
    if not files:
        print("没有 lagdump 数据。")
        return
    print("延迟模型 %.0fms(python 发布领先)" % delay)
    print("%-26s %18s %18s %18s %10s" % (
        "曲名", "原始:增/尾/峰", "仿真:增/尾/峰", "仿真最大滞后", "wait条数"))
    print("-" * 100)
    for path in files:
        d = json.load(open(path, encoding="utf-8"))
        ev = [(c, i, g) for c, i, g in d["events"]]
        if len(ev) < 100:
            continue
        raw, sim = simulate(ev, delay)
        waits = sum(1 for c, _, _ in ev if c == "w")
        rg, rt, rmax, rmin = stats(raw, ev)
        sg, st, smax, smin = stats(sim, ev)
        print("%-26s %7.1f/%6.1f/%6.1f %7.1f/%6.1f/%6.1f %18.1f %10d" % (
            d["meta"].get("song", "?")[:26],
            rg, rt, rmax, sg, st, smax, smin, waits))


if __name__ == "__main__":
    main()

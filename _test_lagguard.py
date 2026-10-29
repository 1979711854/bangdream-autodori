# -*- coding: utf-8 -*-
"""滞后反馈两道保护的离线回归(2026-10-28)。

背景(2026-10-28 00:13~00:19 三首崩局,gate 同为 60):
  #320 失配 11/15717 → miss 4;#763 失配 2719/16964 → miss 12;
  #263 失配 322/5150 → 服务端 lag 12ms 涨到 117ms → 生命耗尽。
机理:jlog 的 'w' 回执丢了 → wait_ideal_ms 只进不出 → ideal 指针少推进 →
lag 被算大 → 「补足到池」把虚高值注回去 → 时间线推早 → 批量 FAST/miss。
原两道保护都在 autodori.lag_feedback_batch_delta 里:
  ① wait 回执积压持续超限(默认 400 条 / 2.0s)→ 本曲停注入 + 清空池水;
  ② 单批补足 > 30ms(往早推那一侧)→ 丢弃并记数,不注。

2026-10-29 补两块(「越打越早」的根因 = 理想指针漏算没睡成的 wait):
  ③ wait 欠账改成**只告警、不停注入**(停注入会把整首歌推晚,#489 实测);
  ④ lag_feedback_apply_skipped 把欠账补进理想指针 + 一段闭环仿真,对照
     「补齐前 lag 假 0 / 补齐后 = dump 的曲内增长」。

跑法:.venv/Scripts/python.exe _test_lagguard.py
"""
import collections
import logging
import sys

sys.path.insert(0, "src")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

import autodori  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-56s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-56s -> %s (want %s)" % (label, got, want))


def ok(label, got, want, tol=0.5):
    """浮点断言:闭环仿真的数会带服务端开销的小数尾巴,按容差判。"""
    global PASS, FAIL
    if abs(got - want) <= tol:
        PASS += 1
        print("  PASS  %-56s -> %.2f" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-56s -> %.2f (want %.2f±%.2f)" % (label, got, want, tol))


class FakeChart:
    """只提供 delta 计算需要的字段。"""

    def __init__(self, backlog=0, pool=0.0, drained=0, skipped=0.0):
        self.wait_ideal_ms = collections.deque([5.0] * backlog)
        self._a2c_offset = pool
        self.drained_waits = drained
        self.skipped_wait_ms = skipped


def call(chart, now=0.0, batch_no=1):
    return autodori.lag_feedback_batch_delta(chart, batch_no, now=now)


def main():
    print("== 正常路径:补足量 = lag - 池 ==")
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = 12.0
    check("lag 12 / 池 0 → 注入 12", call(FakeChart(pool=0.0)), 12.0)
    check("lag 12 / 池 4 → 注入 8", call(FakeChart(pool=4.0)), 8.0)
    check("lag≈池(噪声 <0.01)→ 不注入", call(FakeChart(pool=12.0)), 0.0)

    print("== 保护②:单批补足 >30ms 丢弃(只拦往早推的正向) ==")
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = 117.0
    check("lag 117 / 池 0 → 丢弃", call(FakeChart(pool=0.0)), 0.0)
    check("丢弃计数 = 1", autodori._lag_fb["overshoot_n"], 1)
    check("再丢一次仍是 0", call(FakeChart(pool=0.0, backlog=10)), 0.0)
    check("丢弃计数 = 2", autodori._lag_fb["overshoot_n"], 2)
    check("30ms 整不拦(边界)", call(FakeChart(pool=87.0)), 30.0)
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = -50.0
    check("负向(池水多于残差)不拦 → 往晚放", call(FakeChart(pool=0.0)), -50.0)

    print("== 保护①:回执积压持续超限 → 本曲停注入 ==")
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = 20.0
    limit = autodori._LAG_FB_MAX_BACKLOG + 100
    check("首次超限只记录起点,照常注入",
          call(FakeChart(backlog=limit), now=0.0), 20.0)
    check("未到持续时间,仍照常注入",
          call(FakeChart(backlog=limit), now=1.0), 20.0)
    check("积压回落后计时清零",
          call(FakeChart(backlog=10), now=1.5), 20.0)
    check("backlog_since 已清", autodori._lag_fb["backlog_since"], None)
    check("重新超限重新计时(1.0s 内照常注入)",
          call(FakeChart(backlog=limit), now=2.0), 20.0)
    chart = FakeChart(backlog=limit, pool=25.0)
    check("持续超限 → 本批停注入",
          call(chart, now=2.0 + autodori._LAG_FB_BACKLOG_HOLD_S), 0.0)
    check("已标记停注入", autodori._lag_fb["off"], True)
    check("池水被清空", chart._a2c_offset, 0.0)
    check("停注入后即便积压回落也不再注入",
          call(FakeChart(backlog=0), now=99.0), 0.0)
    check("积压峰值已记录(取证用)", autodori._lag_fb["backlog_peak"], limit)

    print("== wait 欠账只告警、绝不停注入(2026-10-29 改) ==")
    # 停注入会把整首歌推晚(#489 第一局 +37.7ms / 418 SLOW、#649/#489 后两局
    # +20/+37ms),所以欠账大也照常注入,只打一条告警(每曲一次)。
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = 12.0
    limit = autodori._LAG_FB_SKIP_WARN_MS
    check("欠账未到告警线照常注入",
          call(FakeChart(pool=0.0, skipped=limit - 5)), 12.0)
    check("未告警时 skip_warned 仍为 False", autodori._lag_fb["skip_warned"], False)
    chart = FakeChart(pool=8.0, skipped=limit, drained=2)
    check("欠账到告警线仍照常注入(不停、不清池)", call(chart), 4.0)
    check("已告警", autodori._lag_fb["skip_warned"], True)
    check("未标记停注入", autodori._lag_fb["off"], False)
    check("池水未被清空", chart._a2c_offset, 8.0)
    check("告警后继续注入", call(FakeChart(pool=0.0, skipped=limit + 20)), 12.0)
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = -30.0
    check("欠账超限的负向也照常注入(往晚放)",
          call(FakeChart(pool=0.0, skipped=limit + 50)), -30.0)
    # 只看条数不看时长会漏:#684 焚音打抽干只有 8 条,欠账却 73ms
    autodori.reset_lag_feedback()
    autodori._lag_fb["lag"] = -73.0
    check("#684 形态(抽干 8 条/欠账 73ms)照常注入,由反馈把时间还回来",
          call(FakeChart(pool=0.0, skipped=73.0, drained=8)), -73.0)

    print("== 理想指针补齐:没睡成的 wait 要推进 ideal(2026-10-29) ==")
    # 不补齐的话 lag 恒偏「晚」(假残差),反馈一直往早推;补齐后指针与 dump
    # 重建口径一致(见 lag_feedback_apply_skipped 注释)。
    autodori.reset_lag_feedback()
    chart = FakeChart()
    chart.skipped_wait_ms = 0.0
    autodori._lag_fb["ideal"] = 1000.0
    check("无欠账 → 已补值不变",
          autodori.lag_feedback_apply_skipped(chart, 0.0), 0.0)
    check("无欠账 → 指针不动", autodori._lag_fb["ideal"], 1000.0)
    chart.skipped_wait_ms = 1.5
    check("本批新增 1.5ms → 已补值 1.5",
          autodori.lag_feedback_apply_skipped(chart, 0.0), 1.5)
    check("指针前推 1.5ms", autodori._lag_fb["ideal"], 1001.5)
    check("诊断计数 skip_applied", autodori._lag_fb["skip_applied"], 1.5)
    chart.skipped_wait_ms = 4.0
    check("再新增 2.5ms → 已补值 4.0",
          autodori.lag_feedback_apply_skipped(chart, 1.5), 4.0)
    check("指针累计前推 4.0ms", autodori._lag_fb["ideal"], 1004.0)
    check("同一份欠账不重记(重复调用)",
          autodori.lag_feedback_apply_skipped(chart, 4.0), 4.0)
    check("重复调用后指针不动", autodori._lag_fb["ideal"], 1004.0)
    # 锚定前攒下的欠账:锚点会把指针重置成首条命令的 start,必须一次补进
    autodori.reset_lag_feedback()
    chart = FakeChart()
    chart.skipped_wait_ms = 2.0
    check("锚定前 → 记进 pending_skip",
          autodori.lag_feedback_apply_skipped(chart, 0.0), 2.0)
    check("理想指针仍未建立", autodori._lag_fb["ideal"], None)
    check("pending_skip = 2.0", autodori._lag_fb["pending_skip"], 2.0)
    autodori.reset_callback_data()
    ev = autodori.MNTEvATive7LogEventData(500.0, 500.0, 0.0, "d 1 100 200 0")
    autodori.mnt_callback(autodori.MNTEvent.EVATIVE7_LOG, ev)
    check("锚定后指针 = start + 欠账", autodori._lag_fb["ideal"], 502.0)
    check("锚定时 pending_skip 清零", autodori._lag_fb["pending_skip"], 0.0)
    check("锚定时计入 skip_applied", autodori._lag_fb["skip_applied"], 2.0)
    check("锚定后 lag = 0", autodori._lag_fb["lag"], 0.0)

    print("== 闭环对照:补齐前 lag 假 0,补齐后 = dump 的「曲内增长」 ==")
    # 合成一首歌:每条判定点后跟一条 5ms wait,另有 0.5ms 的间隙根本没睡成
    # (密集曲常态)。服务端每条命令都在「理想时刻 − 累计欠账」开始,理想队列
    # 只含真正发出的 wait —— 与实机同口径,所以可以离线复现那两个数。
    def simulate(with_fix, iters=30, wait_ms=5.0, skip_ms=0.5, cost=0.07):
        autodori.reset_lag_feedback()
        autodori.reset_callback_data()
        chart = FakeChart()
        chart.wait_ideal_ms = collections.deque([wait_ms] * iters)
        autodori.current_chart = chart
        applied = 0.0
        t = 1000.0
        for _ in range(iters):
            autodori.mnt_callback(
                autodori.MNTEvent.EVATIVE7_LOG,
                autodori.MNTEvATive7LogEventData(t, t + 0.03, 0.03, "d 1 100 200 1"),
            )
            t += wait_ms + cost                    # 发出 5ms wait + 服务端开销
            autodori.mnt_callback(
                autodori.MNTEvent.EVATIVE7_LOG,
                autodori.MNTEvATive7LogEventData(t, t + cost, cost,
                                                 "w %d" % int(wait_ms)),
            )
            chart.skipped_wait_ms += skip_ms       # 这一段压根没睡
            if with_fix:
                applied = autodori.lag_feedback_apply_skipped(chart, applied)
        lag = autodori._lag_fb["lag"]
        autodori.current_chart = None
        return lag, applied

    n, wait, cost, skip = 30, 5.0, 0.07, 0.5
    # 估计器比命令晚一拍:lag 在命令回执时算,最后一条 wait 的额度(和最后
    # 一批补进来的欠账)要等下一条命令才计入 —— 理论值据此写,别拿「整首」
    # 直接算,否则差一条 wait。
    lag_nofix, _ = simulate(with_fix=False, wait_ms=wait)
    ok("补齐前:lag 被算成「假偏晚」(开销合计 + 一拍 wait)",
       lag_nofix, wait + n * cost, tol=0.3)
    lag_fix, applied = simulate(with_fix=True, wait_ms=wait)
    ok("补齐后:lag = 真实提前量(欠账 − 开销,少算最后一批)",
       lag_fix, wait + n * cost - (n - 1) * skip, tol=0.3)
    check("补齐后欠账全额入账", applied, n * skip)
    # 两者之差就是「在线 lag 终值 − dump lag@100%」;实测 5 首都 ≈ 欠账
    ok("修复前后差值 ≈ 欠账合计(实测 5 首同形态)",
       lag_nofix - lag_fix, (n - 1) * skip, tol=0.3)

    print("== 每首重置 ==")
    autodori.reset_lag_feedback()
    for key, want in (("off", False), ("backlog_since", None),
                      ("backlog_peak", 0), ("overshoot_n", 0),
                      ("lag", 0.0), ("ideal", None),
                      ("skip_applied", 0.0), ("pending_skip", 0.0),
                      ("skip_warned", False)):
        check("reset 后 %s" % key, autodori._lag_fb[key], want)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

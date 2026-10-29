# -*- coding: utf-8 -*-
"""滞后反馈两道保护的离线回归(2026-10-28)。

背景(2026-10-28 00:13~00:19 三首崩局,gate 同为 60):
  #320 失配 11/15717 → miss 4;#763 失配 2719/16964 → miss 12;
  #263 失配 322/5150 → 服务端 lag 12ms 涨到 117ms → 生命耗尽。
机理:jlog 的 'w' 回执丢了 → wait_ideal_ms 只进不出 → ideal 指针少推进 →
lag 被算大 → 「补足到池」把虚高值注回去 → 时间线推早 → 批量 FAST/miss。
两道保护都在 autodori.lag_feedback_batch_delta 里:
  ① wait 回执积压持续超限(默认 400 条 / 2.0s)→ 本曲停注入 + 清空池水;
  ② 单批补足 > 30ms(往早推那一侧)→ 丢弃并记数,不注。

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


class FakeChart:
    """只提供 delta 计算需要的两个字段。"""

    def __init__(self, backlog=0, pool=0.0):
        self.wait_ideal_ms = collections.deque([5.0] * backlog)
        self._a2c_offset = pool


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

    print("== 每首重置 ==")
    autodori.reset_lag_feedback()
    for key, want in (("off", False), ("backlog_since", None),
                      ("backlog_peak", 0), ("overshoot_n", 0),
                      ("lag", 0.0), ("ideal", None)):
        check("reset 后 %s" % key, autodori._lag_fb[key], want)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

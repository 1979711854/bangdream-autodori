# -*- coding: utf-8 -*-
"""首音候选可信度门离线回归(2026-10-27)。

判据本体在 src/autodori.py:_first_note_trigger_check。这里只喂数字,
不碰画面/MAA/模拟器。

用例来自实测:
  · 23:24 场第 1 首 #3-special:底噪长期 3.7~4.4,候选 change=3.42 就被锚定,
    锚点晚数百毫秒整曲错位(触发帧截图 debug/firstnote/232424-trigger.jpg
    上已显示 MISS),开打 7 秒生命耗尽 —— 判据①必须拒掉它;
  · 同场第 2 首 ONENESS 全 PERFECT:change=9.68,band 与顶部判据差 -4ms ——
    必须放行;
  · 事故里 band 与顶部 7 行差 538ms(正常实测 -8~+1ms) —— 判据②必须拒掉。

跑法:.venv/Scripts/python.exe _test_firstnote.py
"""
import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")

import autodori as A  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-62s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-62s -> %s (want %s)" % (label, got, want))


def gate(change, noise, edge_delta_ms, rejects=0, rejected_s=0.0):
    """band_t = 10.0s;edge_last_t 按差值反推。edge_delta_ms=None 表示没记录。"""
    band_t = 10.0
    edge_t = None if edge_delta_ms is None else band_t - edge_delta_ms / 1000.0
    ok, reason = A._first_note_trigger_check(
        change, noise, band_t, edge_t, rejects, rejected_s
    )
    return ok, reason


def main():
    print("== 判据① 底噪比 ==")
    ok, reason = gate(9.68, 2.0, -4)
    check("正常曲目(ONENESS): change=9.68 底噪=2.0 差-4ms", ok, True)
    ok, reason = gate(3.42, 2.8, -4)
    check("事故曲目(#3-special): change=3.42 底噪=2.8", ok, False)
    check("  拒绝原因指向底噪", "底噪" in reason, True)
    ok, _ = gate(3.05, 0.0, 0)
    check("强制超时(noise=0)不启用判据①: change=3.05", ok, True)
    ok, _ = gate(3.00, 2.0, 0)
    check("边界: change 恰等于 max(3.0, 底噪x1.5) 不算低于", ok, True)
    ok, _ = gate(3.5, 2.0, 0)
    check("淡音符: change=3.5 底噪=2.0 放行", ok, True)
    ok, _ = gate(4.49, 2.9, 0)
    check("底噪 2.9 时阈值 4.35: change=4.49 放行", ok, True)
    ok, _ = gate(4.34, 2.9, 0)
    check("底噪 2.9 时阈值 4.35: change=4.34 拒绝", ok, False)

    print("== 判据② 顶部判据时差 ==")
    ok, reason = gate(10.0, 0.0, 538)
    check("事故形态: 顶部判据早 538ms", ok, False)
    check("  拒绝原因指向顶部判据", "顶部判据早" in reason, True)
    ok, _ = gate(10.0, 0.0, 200)
    check("顶部判据晚 200ms(反向异常)", ok, False)
    ok, _ = gate(10.0, 0.0, 140)
    check("顶部判据早 140ms(窗口内)放行", ok, True)
    ok, _ = gate(10.0, 0.0, -140)
    check("顶部判据晚 140ms(窗口内)放行", ok, True)
    ok, _ = gate(10.0, 0.0, None)
    check("顶部判据没记录(无对照证据)放行", ok, True)

    print("== 放行条件(防止过度拦截) ==")
    ok, _ = gate(3.42, 2.8, 538, rejects=3)
    check("不同候选已拒绝 3 次后放宽", ok, True)
    ok, _ = gate(3.42, 2.8, 538, rejects=2)
    check("拒绝 2 次时仍拦", ok, False)
    ok, _ = gate(3.42, 2.8, 538, rejected_s=8.0)
    check("累计拒绝 8s 后放宽", ok, True)
    ok, _ = gate(3.42, 2.8, 538, rejected_s=7.9)
    check("累计拒绝 7.9s 时仍拦", ok, False)

    print("== L2 重心工具函数 ==")
    import numpy as np

    check("单点权重 → 行偏移", A._weighted_centroid(np.array([0.0, 0.0, 1.0, 0.0])), 2.0)
    check("两点等权 → 中点", A._weighted_centroid(np.array([1.0, 0.0, 0.0, 1.0])), 1.5)
    check("全 0 权重 → -1", A._weighted_centroid(np.zeros(4)), -1.0)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

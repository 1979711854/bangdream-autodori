# -*- coding: utf-8 -*-
"""卡死看门狗离线回归(2026-10-17 重写为「画面静止」判据)。

历史:最早按「同一节点 60s 内命中 ≥8 次」判卡死,后来加「90s 无任何节点命中」。
但实测 MAA 4.4.0 只把任务级通知送到 Python,节点级通知一条都收不到
(见 _diag_notify.py),两个判据都退化成定时器 —— 后者在打歌中按返回键,
让游戏弹【暂停】(2026-10-17 12:59 实况)。现在改成:
画面 + 脚本心跳都没有进展 → 按返回键(最多 2 次) → 停任务。

跑法:.venv/Scripts/python.exe _test_watchdog.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, ".")

import autodori as A  # noqa: E402

PASS = 0
FAIL = 0

FROZEN = np.zeros((90, 160), dtype=np.float32)


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-56s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-56s -> %s (want %s)" % (label, got, want))


def _moving():
    """模拟"画面在动":每次返回不同的采样图。"""
    return np.random.rand(90, 160).astype(np.float32)


def test_static_then_back_then_stop():
    print("=== 1. 静止 -> 按返回键(2次) -> 停任务 ===")
    wd = A._StuckWatchdog()
    with patch.object(A, "_watchdog_sample", lambda: FROZEN), \
         patch.object(A, "_adb_shell") as sh, \
         patch.object(A, "_force_post_stop") as st:
        # 启动头 10s 属于"busy 静默期",连图都不抓
        check("刚启动不动作", wd.check(), False)
        check("刚启动不按返回键", sh.call_count, 0)

        # 静默期过去 -> 抓第一帧(建立基准),此时还不能算卡死
        wd._last_activity -= A._WATCHDOG_BUSY_QUIET_S + 1
        check("建立基准帧不动作", wd.check(), False)
        check("建立基准帧不按返回键", sh.call_count, 0)

        # 静止时长越过阈值
        wd._last_activity -= A._WATCHDOG_STATIC_S + 1
        check("静止超阈值 -> 不直接停任务", wd.check(), False)
        check("按了 1 次返回键", sh.call_count, 1)
        check("返回键命令正确", sh.call_args[0][0], ["input", "keyevent", "4"])

        # 观察窗口内不重复按
        wd._last_activity -= A._WATCHDOG_STATIC_S + 1
        check("观察窗口内不停任务", wd.check(), False)
        check("观察窗口内不重复按", sh.call_count, 1)

        # 观察窗口过去,仍然静止 -> 第 2 次
        wd._observe_until = 0.0
        wd._last_back -= A._WATCHDOG_BACK_COOLDOWN_S + 1
        check("第 2 次按返回键", wd.check(), False)
        check("按了 2 次返回键", sh.call_count, 2)

        # 两次都无效 -> 停任务
        wd._observe_until = 0.0
        wd._last_back -= A._WATCHDOG_BACK_COOLDOWN_S + 1
        check("两次无效 -> 停任务", wd.check(), True)
        check("post_stop 只调 1 次", st.call_count, 1)


def test_moving_and_ping():
    print("=== 2. 画面在动 / ping 心跳 -> 永不误判 ===")
    wd = A._StuckWatchdog()
    with patch.object(A, "_watchdog_sample", _moving), \
         patch.object(A, "_adb_shell") as sh, \
         patch.object(A, "_force_post_stop") as st:
        wd._last_activity -= A._WATCHDOG_STATIC_S + 5
        for _ in range(5):
            wd.check()
        check("画面在动不按返回键", sh.call_count, 0)
        check("画面在动不停任务", st.call_count, 0)

    # 静态画面 + 打歌线程每秒 ping(真实打歌就是这样)
    wd = A._StuckWatchdog()
    with patch.object(A, "_watchdog_sample", lambda: FROZEN), \
         patch.object(A, "_adb_shell") as sh, \
         patch.object(A, "_force_post_stop") as st:
        for _ in range(5):
            wd.ping()
            wd._last_activity -= 5        # 只过 5s,远没到 75s
            wd.check()
        check("有 ping 心跳不按返回键", sh.call_count, 0)
        check("有 ping 心跳不停任务", st.call_count, 0)

    # 任务级通知(这个 MAA 版本能收到)同样算进展
    wd = A._StuckWatchdog()
    with patch.object(A, "_watchdog_sample", lambda: FROZEN), \
         patch.object(A, "_adb_shell") as sh:
        wd._last_activity -= A._WATCHDOG_STATIC_S + 1
        wd.on_tasker_task(None, None)
        wd.check()
        check("任务级通知算一次进展", sh.call_count, 0)

    # 刚 ping 过就连图都不抓:打歌期间看门狗的额外抓图 = 0
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return FROZEN

    wd = A._StuckWatchdog()
    with patch.object(A, "_watchdog_sample", _counting), \
         patch.object(A, "_adb_shell"):
        wd.ping()
        for _ in range(3):
            wd.check()
        check("刚 ping 过不抓图(打歌期间零额外抓图)", calls["n"], 0)


def test_wiring():
    print("=== 3. 接线:阈值 / 抓图串行 / 打歌心跳 / special 不启动 ===")
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    check("Tasker 挂了看门狗回调",
          "Tasker(notification_handler=_STUCK_WATCHDOG)" in src, True)
    check("静止阈值 75s", A._WATCHDOG_STATIC_S, 75.0)
    check("最多按 2 次返回键(方案 1A)", A._WATCHDOG_MAX_BACKS, 2)
    check("看门狗线程是 daemon",
          'name="stuck-watchdog", daemon=True' in src, True)
    check("special 模式不启动看门狗",
          "if not SPECIAL_MODE:\n        _start_stuck_watchdog()" in src, True)
    check("抓图有锁(避免与打歌线程串包)", "_SCREEN_CAP_LOCK" in src, True)
    check("打歌线程有 ping 心跳",
          "_STUCK_WATCHDOG.ping()" in src, True)
    # 旧的节点判据必须彻底拆掉:否则返回键还会在打歌中被按下去
    for old in ("on_node_recognition", "_WATCHDOG_EXEMPT_NODES",
                "_WATCHDOG_NO_HIT_S", "_WATCHDOG_STUCK_HITS"):
        check("旧节点判据已移除: %s" % old, old in src, False)


def test_pause_click_uses_recognition():
    print("=== 4. 暂停中断必须按识别框点(不能退化成 (0,0)) ===")
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    fn = src[src.index("def _interrupt_paused_live"):]
    fn = fn[: fn.index("@maaresource.custom_action(\"Play\")")]
    check("暂停中断不再用 run_node_once(run_action 没有识别结果)",
          "_run_node_once(context" not in fn, True)
    check("按识别框中心点击", "_click_at_box" in fn, True)
    check("_click_at_box 用 post_click", "post_click" in src, True)


def main():
    test_static_then_back_then_stop()
    test_moving_and_ping()
    test_wiring()
    test_pause_click_uses_recognition()
    print("\nPASS=%d  FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

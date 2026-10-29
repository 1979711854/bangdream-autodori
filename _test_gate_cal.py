# -*- coding: utf-8 -*-
"""photogate 校准器离线回归(2026-10-27,L3 两条)。

背景:一晚实测 photogate 被自动校准推成 47→55→70→85→70→55→40,方向来回
反转 —— 单曲噪声与曲内漂移都被当成全局偏移。改动只有两条:
  1. 方向反转时步长减半(连续同向不变,收敛速度不受影响);
  2. 本曲无 FAST/SLOW 时不再静默跳过(GUI 侧提示,见 gui._on_play_result)。

跑法:.venv/Scripts/python.exe _test_gate_cal.py
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import gui  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-58s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-58s -> %s (want %s)" % (label, got, want))


def payload(fast, slow, perfect=900):
    return {"fast": fast, "slow": slow, "perfect": perfect, "great": 0,
            "good": 0, "bad": 0, "miss": 0}


def test_manual_lock():
    """手动改值 = 锁定(2026-10-28):关自动校准 + 清方向记忆 + 落盘。

    背景:18:05 场手动设 75,下一首结算被自动校准改成 74,用户以为"改了没用"。
    """
    import os
    import tempfile

    import yaml

    tmpdir = tempfile.mkdtemp()
    cfg = os.path.join(tmpdir, "config.yml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("timing:\n  photogate_latency_ms: 60\n")

    app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
    app.gate = 60
    app.auto_cal = True
    app._last_cal_direction = 1
    app._cal_direction_prev = 1
    app.status_gate = None
    app.gate_step = None
    app.auto_cal_switch = None
    saved = []
    cal = []
    app._save_gui_config = lambda: saved.append(True)
    app._emit = lambda *a: cal.append(a)
    app._write_config = lambda: None

    old = gui.CONFIG
    gui.CONFIG = cfg
    try:
        app._on_gate_manual(50)
        with open(cfg, encoding="utf-8") as f:
            got = yaml.safe_load(f.read())["timing"]["photogate_latency_ms"]
        check("手动改值 -> 写入 config.yml", got, 50)
        check("手动改值 -> 关闭自动校准", app.auto_cal, False)
        check("手动改值 -> 清零方向记忆",
              (app._last_cal_direction, app._cal_direction_prev), (0, 0))
        check("关自动校准要落盘 gui_config", bool(saved), True)
        check("CAL 行区分手动来源",
              any("手动输入" in str(a) for a in cal), True)
    finally:
        gui.CONFIG = old


def test_measure_anchor_switch():
    """首音锚点实测开关(2026-10-28):GUI 开关写 config.yml 的 timing.measure_anchor。

    用户要求:以前只能手改 data/config.yml,每次测试都要重新配,太麻烦。
    """
    import os
    import tempfile

    import yaml

    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, "config.yml")
    with open(cfg, "w", encoding="utf-8") as f:
        # 带一个 GUI 不认识的键,验证写入只合并 timing、不抹别的
        f.write("timing:\n  photogate_latency_ms: 70\n"
                "on_zero_boost: play\n")

    app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
    app.measure_anchor = False
    cal = []
    app._emit = lambda *a: cal.append(a)

    old = gui.CONFIG
    gui.CONFIG = cfg
    try:
        app._on_measure_anchor(True)
        with open(cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f.read())
        check("打开后 config.yml 写入 measure_anchor",
              data["timing"].get("measure_anchor"), True)
        check("写入只合并 timing,不抹别的键",
              data.get("on_zero_boost"), "play")
        check("不动 photogate", data["timing"]["photogate_latency_ms"], 70)
        check("界面状态同步", app.measure_anchor, True)
        check("CAL 行写明开关", any("首音锚点实测" in str(a) for a in cal), True)

        # 读回:重启 GUI 后要能恢复勾选状态
        app2 = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
        check("_read_measure_anchor 读回 True", app2._read_measure_anchor(), True)

        app._on_measure_anchor(False)
        with open(cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f.read())
        check("关闭后写回 False", data["timing"].get("measure_anchor"), False)
    finally:
        gui.CONFIG = old


def test_gui_render_gate():
    """真建 Tk root 渲染「时机校准」页(无显示环境则跳过)。

    静态断言看不出控件建不起来 —— 新加的开关必须真渲染一遍。
    """
    print("\n== GUI 时机校准页真渲染 ==")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print("  SKIP  无显示环境: %s" % e)
        return
    try:
        root.withdraw()
        app = gui.AutodoriGUI(root)
        app.view = "live.gate"
        app._render_view()

        def texts(w, out=None):
            out = [] if out is None else out
            for c in w.winfo_children():
                try:
                    t = str(c.cget("text") or "")
                except Exception:
                    t = ""
                if t:
                    out.append(t)
                texts(c, out)
            return out

        shown = texts(root)
        check("渲染出「自动校准 photogate」",
              any("自动校准 photogate" in t for t in shown), True)
        check("渲染出「实测首音锚点」",
              any("实测首音锚点" in t for t in shown), True)
        check("开关句柄已建立",
              getattr(app, "measure_anchor_switch", None) is not None, True)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("== 基础步长 ==")
    # 分母是 perfect+great+good+bad+miss(不含 fast/slow):
    # off_ratio = 100/900 = 0.111 → step = round(0.111*50) = 6
    check("FAST 100 / SLOW 0 → 60+6", gui._calibrate_gate(payload(100, 0), 60), 66)
    check("FAST 0 / SLOW 100 → 60-6", gui._calibrate_gate(payload(0, 100), 60), 54)

    print("== 信号不足/无效 ==")
    check("无 FAST/SLOW 返回 None", gui._calibrate_gate(payload(0, 0), 60), None)
    check("|FAST-SLOW|<3 返回 None", gui._calibrate_gate(payload(1, 0), 60), None)
    check("OCR 失败(-1)返回 None", gui._calibrate_gate(payload(-1, 0), 60), None)

    print("== 抗振荡:方向反转步长减半 ==")
    check("同向(上曲也是 FAST)保持 +6",
          gui._calibrate_gate(payload(100, 0), 60, prev_direction=1), 66)
    check("反转(上曲是 SLOW)减半 +3",
          gui._calibrate_gate(payload(100, 0), 60, prev_direction=-1), 63)
    check("同向(上曲也是 SLOW)保持 -6",
          gui._calibrate_gate(payload(0, 100), 60, prev_direction=-1), 54)
    check("反转(上曲是 FAST)减半 -3",
          gui._calibrate_gate(payload(0, 100), 60, prev_direction=1), 57)
    check("无历史方向按原步长",
          gui._calibrate_gate(payload(100, 0), 60, prev_direction=0), 66)

    print("== 边界夹紧与大步长 ==")
    check("上限 150 夹紧", gui._calibrate_gate(payload(900, 0), 150), 150)
    check("下限 0 夹紧", gui._calibrate_gate(payload(0, 900), 0), 0)
    # 2026-10-28:常规偏斜步长上限由 15 收到 10 —— 实机 76+15=91 直接过冲成 slow=134。
    check("FAST 占判定 44% → 仍封顶 10",
          gui._calibrate_gate(payload(400, 0, perfect=900), 60), 70)
    # 2026-10-29:一边倒(FAST/SLOW 覆盖过半)时放宽到 30ms —— 实机 #342 徒花
    # great 598 全是 FAST、perfect 132,off_ratio 0.82,理论需求 41ms,
    # 旧的 10ms 封顶要爬 3 首,期间一直在刷 FAST。
    check("一边倒(82%) → 放宽到 +30",
          gui._calibrate_gate(
              {"fast": 598, "slow": 0, "perfect": 132, "great": 598,
               "good": 0, "bad": 0, "miss": 0}, 30), 60)
    check("一边倒但反转 → 30 减半 +15",
          gui._calibrate_gate(
              {"fast": 598, "slow": 0, "perfect": 132, "great": 598,
               "good": 0, "bad": 0, "miss": 0}, 30, prev_direction=-1), 45)

    print("== 实机轨迹复现(2026-10-28 18:05 场) ==")
    # 第 1 首:slow=134 / 总判定 757,上曲方向是 FAST → 反转减半 → 4ms
    check("91 -> 87(slow 134,上曲 FAST)",
          gui._calibrate_gate(
              {"fast": 0, "slow": 134, "perfect": 620, "great": 134,
               "good": 0, "bad": 0, "miss": 3}, 91, prev_direction=1), 87)
    # 第 2 首:slow=79 / 757,同向 → 5ms
    check("87 -> 82(slow 79,同向)",
          gui._calibrate_gate(
              {"fast": 0, "slow": 79, "perfect": 677, "great": 79,
               "good": 0, "bad": 0, "miss": 1}, 87, prev_direction=-1), 82)

    print("\n== 手动改值 = 锁定 ==")
    test_manual_lock()

    print("\n== 首音锚点实测开关 ==")
    test_measure_anchor_switch()

    test_gui_render_gate()

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

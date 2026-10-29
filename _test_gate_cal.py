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


def test_misalign_skip():
    """下发链错位局不参与校准(2026-10-29,P0 止血)。

    现场 debug/autodori-20261029-172920.log:#684 焚音打 dump 报
    「有 8926/28010 条与谱面错位」,同曲结算 fast=805 / miss=19,
    自动校准按 off_ratio 50% 一步 +25 把 gate 从 60 推到 85 —— 之后
    #763 / #342 全在错的 gate 上打。错位局的下发时机本身就不对,
    FAST/SLOW 归属不可信,不能拿来调 photogate。
    """
    import collections
    import time

    print("\n== 下发链错位局不参与校准 ==")

    # 1) 日志行解析:2026-10-29 起 bot 把这条拆成两行,曲名不在第一行
    check("解析错位行(第一行)",
          gui._parse_misalign(
              "2026-10-29 17:58:56,493[WARNING][root] 服务端执行滞后: "
              "有 2843/15701 条与谱面错位(仅诊断)"),
          (2843, 15701))
    check("普通命令统计行不误判",
          gui._parse_misalign(
              "服务端执行滞后[焚音打]: 命令 28010 条(失配 0)"),
          None)
    check("第二行(处理结果)不误判",
          gui._parse_misalign(
              "  迷星叫(平行歌曲) 本局跳过统计,不影响打歌"),
          None)

    misalign_line = (
        "2026-10-29 17:58:56,493[WARNING][root] 服务端执行滞后: "
        "有 8926/28010 条与谱面错位(仅诊断)")
    song_line = "2026-10-29 17:39:33,618[INFO][root] 打歌: 焚音打 (#684-special)"
    payload_684 = {"score": 1596180, "maxcombo": 646, "perfect": 774,
                   "great": 805, "good": 0, "bad": 0, "miss": 19,
                   "fast": 805, "slow": 0}

    def fake_app(song="焚音打"):
        app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
        app.gate = 60
        app.auto_cal = True
        app.current_song = song
        app.songs_done = 0
        app.m_songs = None
        app._last_cal_direction = 0
        app._cal_direction_prev = 0
        app._raw_log = collections.deque()
        app._misalign_pending = None
        app.applied = []
        app.cal = []
        app._should_show = lambda *a: False

        def _apply(v):
            # 真实现会写 config.yml 并刷新状态栏,这里只记账 + 改 gate
            app.applied.append(v)
            app.gate = v
        app._apply_photogate = _apply
        app._set_cal = lambda t: app.cal.append(t)
        app._emit = lambda *a: None
        return app

    # 2) 对照组:没有错位报告时,同一份结算确实会把 60 推到 85(实机值)
    app = fake_app()
    app._on_play_result(payload_684)
    check("对照:无错位时 60 → 85(实机轨迹)", app.gate, 85)

    # 3) 错位报告到达 → 结算时跳过校准,gate 不动、方向记忆不动
    app = fake_app()
    app._handle_line(misalign_line)
    check("错位行记入待处理", app._misalign_pending is not None, True)
    app._on_play_result(payload_684)
    check("错位局 gate 不动", app.gate, 60)
    check("错位局不写入 photogate", app.applied, [])
    check("CAL 行写明未参与校准",
          any("未参与校准" in t and "错位" in t for t in app.cal), True)
    check("错位局不污染方向记忆",
          (app._last_cal_direction, app._cal_direction_prev), (0, 0))
    check("报告消费掉,不留给下一首", app._misalign_pending, None)
    check("结算计数照常 +1", app.songs_done, 1)

    # 4) 换曲(新的「打歌:」行)清掉上一首的报告 → 照常校准。
    #    崩局那首没有结算,报告会一直挂着,不清就会误伤新的一首。
    app = fake_app()
    app._handle_line(misalign_line)
    app._handle_line(song_line)
    check("换曲清掉旧报告", app._misalign_pending, None)
    app._on_play_result(payload_684)
    check("换曲后照常校准", app.gate, 85)

    # 5) 过期报告(崩局那首没结算)→ 不生效
    app = fake_app()
    app._misalign_pending = (0.32, time.time() - 10000)
    app._on_play_result(payload_684)
    check("过期报告不生效", app.gate, 85)
    check("过期报告被清理", app._misalign_pending, None)

    # 6) 比例门槛:低于 CAL_MISALIGN_SKIP_RATIO 的不拦
    app = fake_app()
    app._misalign_pending = (0.01, time.time())
    app._on_play_result(payload_684)
    check("低于门槛比例不跳过", app.gate, 85)


def test_drift_gate_and_bias():
    """曲内漂移门控 + 小额偏置累计(2026-10-29)。

    背景:#395 曲内 -31ms、fast 28 → 校准把 gate 从 65 推到 66,下一首被推晚
    掉了 10 个 slow;另一头「只差一点、擦边爆 1 个 slow」以前永远调不动。
    """
    import os
    import tempfile
    import time as _t

    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, "config.yml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("timing:\n  photogate_latency_ms: 65\n")

    def make_app():
        app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
        app.gate = 65
        app.auto_cal = True
        app.measure_anchor = False
        app.songs_done = 0
        app.m_songs = None
        app.cal_label = None
        app.status_gate = None
        app.gate_step = None
        app.auto_cal_switch = None
        app._misalign_pending = None
        app._drift_pending = None
        app._bias_acc = 0
        app._last_cal_direction = 0
        app._cal_direction_prev = 0
        app.cal_text = ""
        app.warns = []
        app._emit = lambda *a: app.warns.append(a)
        app._set_cal = lambda t: setattr(app, "cal_text", t)
        return app

    old = gui.CONFIG
    gui.CONFIG = cfg
    try:
        print("\n== 曲内漂移解析 ==")
        line = ("2026-10-29 22:21:24,476[INFO][root] 服务端执行滞后[X]: 音符命令 "
                "5614 条, lag@10%=-0.7 ... 音符曲内增长=-31.1ms")
        check("解析负漂移", gui._parse_drift(line), -31.1)
        check("解析正漂移", gui._parse_drift("音符曲内增长=5.7ms"), 5.7)
        check("无该字段返回 None", gui._parse_drift("随便一行"), None)

        print("\n== 漂移门控 ==")
        app = make_app()
        app._drift_pending = (-31.1, _t.time())
        app._on_play_result(payload(28, 0, perfect=746))
        check("漂移 -31ms 不调 gate", app.gate, 65)
        check("漂移局不更新方向记忆", app._cal_direction_prev, 0)
        check("CAL 行写明曲内漂移", "曲内漂移" in app.cal_text, True)

        app = make_app()
        app._drift_pending = (-31.1, _t.time() - gui.MISALIGN_TTL_S - 5)
        app._on_play_result(payload(28, 0, perfect=746))
        check("过期漂移报告不拦校准", app.gate > 65, True)

        app = make_app()
        app._drift_pending = (2.5, _t.time())
        app._on_play_result(payload(28, 0, perfect=746))
        check("漂移未达 20ms 照常校准", app.gate > 65, True)

        print("\n== 小额偏置累计 ==")
        app = make_app()
        app._on_play_result(payload(0, 1))
        check("第 1 个 slow 不动 gate", (app.gate, app._bias_acc), (65, -1))
        app._on_play_result(payload(0, 1))
        check("第 2 个 slow 累计 -2", (app.gate, app._bias_acc), (65, -2))
        app._on_play_result(payload(0, 1))
        check("累计到 -3 → gate 64", (app.gate, app._bias_acc), (64, 0))
        check("CAL 行写明小额累计", "小额偏置累计" in app.cal_text, True)

        app = make_app()
        app._bias_acc = -2
        app._on_play_result(payload(0, 100))
        check("大偏斜清零累计", app._bias_acc, 0)
        check("大偏斜按步长调整", app.gate < 65, True)
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
        # 2026-10-29:实测首音锚点的开关从界面下线(代码保留,手改 config.yml 启用)。
        # 这里正着断言「界面上不该再有它」—— 防止以后又被顺手加回来。
        check("界面不再出现「实测首音锚点」",
              any("实测首音锚点" in t for t in shown), False)
        check("界面不再出现锚点适用场景",
              any("适用于检测" in t for t in shown), False)
        check("开关句柄已移除",
              getattr(app, "measure_anchor_switch", None) is None, True)
        # 值仍要从 config.yml 读进来:否则点开始打歌时 _write_config 会把
        # 手改的 measure_anchor 写回 false。
        check("measure_anchor 仍从配置读取(属性存在)",
              hasattr(app, "measure_anchor"), True)
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

    test_misalign_skip()

    test_drift_gate_and_bias()

    test_gui_render_gate()

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

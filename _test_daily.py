# -*- coding: utf-8 -*-
"""一键清理日常的开关结构 + 分阶段执行,离线回归(_test_daily.py)。

覆盖用户 2026-10-09 定的结构:
  · **总开关 = 必经流程**(启动游戏 + 领每日签到 + 领弹窗赠送)—— 没有独立开关,
    因为不点掉这些弹窗根本进不了主界面;
  · **清火打歌 / 每日免费三抽 各自一个开关**,必须真的能各自跳过。
早先那版把三项都做成勾选项(而且总开关会把它们一起勾上),是错的 —— 这里守着别再退回去。

跑法:.venv/Scripts/python.exe _test_daily.py
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "src")
sys.path.insert(0, ".")

import autodori as A
import gui

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-52s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-52s -> %s (want %s)" % (label, got, want))


class FakeTasker:
    """记录 post_task 被要求跑的入口节点。"""

    def __init__(self):
        self.posted = []

    def post_task(self, node, pipeline=None):
        self.posted.append(node)

        class _R:
            def wait(self_):
                return self_

            def get(self_):
                return None

        return _R()


def test_flow_gating():
    print("=== 1. 分阶段执行:两个开关各自生效 ===")
    fake = FakeTasker()
    with patch.object(A, "maatasker", fake), \
         patch.object(A, "_get_override_pipeline", lambda: {}):
        for clear, pull, want in (
            (False, False, ["daily"]),                     # 都不勾 -> 只做必经流程
            (True, False, ["daily", "main"]),              # 只清火
            (False, True, ["daily", "daily_pull"]),        # 只三抽
            (True, True, ["daily", "main", "daily_pull"]),  # 都做
        ):
            fake.posted.clear()
            A._DAILY_CLEAR_FIRE = clear
            A._DAILY_FREE_PULL = pull
            A._run_daily_flow("daily")
            check("清火=%s 三抽=%s -> 跑的节点" % (clear, pull), fake.posted, want)


def test_bot_reads_both_switches():
    print("=== 2. bot 真的读了两个开关 ===")
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    check("读 daily.auto_clear_fire", 'daily_cfg.get("auto_clear_fire"' in src, True)
    check("读 daily.auto_free_pull", 'daily_cfg.get("auto_free_pull"' in src, True)
    # 旧的三个"假开关"不该再出现在 bot 里(它们从来没被读过)
    for key in ("auto_start_game", "claim_login_bonus"):
        check("不再引用 daily.%s" % key, key not in src, True)


def test_gui_config_keys():
    print("=== 3. GUI 配置:只留 3 个键,旧的两个已移除 ===")
    app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
    app.theme = "light"
    app.font_size = 10
    app.window_size = gui.DEFAULT_WINDOW
    app.view = "daily"
    app.difficulty = "expert"
    app.challenge_difficulty = "hard"
    app.challenge_exit_game = False
    app.boost_mode = "继续打歌"
    app.life_mode = "自动退出重新选歌"
    app.auto_cal = False
    app.song_strategy = "挖矿为主"
    app.special_song = "x"
    app.device_address = ""
    app.adb_path = ""
    app.gate = 30
    app.daily_enabled = True
    app.daily_auto_clear_fire = True
    app.daily_auto_free_pull = False

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "gui_config.json"
        with patch.object(gui, "GUI_CONFIG", str(p)):
            app._save_gui_config()
            saved = json.loads(p.read_text(encoding="utf-8"))
        check("daily_enabled 落盘", saved.get("daily_enabled"), True)
        check("daily_auto_clear_fire 落盘", saved.get("daily_auto_clear_fire"), True)
        check("daily_auto_free_pull 落盘", saved.get("daily_auto_free_pull"), False)
        check("旧键 daily_auto_start 已不再写", "daily_auto_start" in saved, False)
        check("旧键 daily_claim_login 已不再写", "daily_claim_login" in saved, False)

        # _write_config 写的是 bot 实时读的 data/config.yml,daily 块同样只该有 3 个键
        cfg = Path(d) / "config.yml"
        with patch.object(gui, "CONFIG", str(cfg)):
            app._write_config()
        written = json.loads(cfg.read_text(encoding="utf-8"))
        check("config.yml 的 daily 块", sorted(written["daily"]),
              ["auto_clear_fire", "auto_free_pull", "enabled"])

    print("=== 4. GUI 源码里不再有那两个回调/属性 ===")
    src = Path("gui.py").read_text(encoding="utf-8")
    for name in ("daily_auto_start", "daily_claim_login",
                 "_on_daily_auto_start", "_on_daily_claim_login"):
        check("gui.py 不含 %s" % name, name in src, False)


def test_rename_and_default_on():
    print("=== 5. 日常页改名 / 默认勾选(2026-10-10 用户要求) ===")
    src = Path("gui.py").read_text(encoding="utf-8")
    check("左栏条目改名为「日常」", '("daily", "日常", "clipboard-check")' in src, True)
    check("勾选项改名为「启动游戏」", 'card.body, text="启动游戏"' in src, True)
    check("界面上不再出现「启用一键清理日常」", "启用一键清理日常" in src, False)
    # 「启动游戏」是必经流程,没有关掉的余地 —— 恒为 True,界面上也点不动
    # (2026-10-10 用户明确「不能改成可选项」)
    check("总开关恒为勾选(硬编码 True)", "self.daily_enabled = True" in src, True)
    check("界面上的勾选点不动", "command=lambda: en.set(True)" in src, True)
    check("不再留可切换的回调", "_on_daily_enabled" in src, False)
    check("不再有「勾选后:」的说法", "勾选后:" in src, False)
    check("删掉那段冗余说明", "签到与开场弹窗" in src, False)

    # 流水线契约:主界面「招募」按钮**正上方**挂着一个「免费招募」活动横幅
    # (y≈563),真按钮在 y≈674。expected 是子串匹配,两个都算命中,而 MAA 取了
    # 靠上的那个 -> 点在横幅上等于没点,人一直留在主界面空转(2026-10-10 实机)。
    d = json.loads(Path("assets/resource/pipeline/daily.json").read_text(encoding="utf-8"))
    og = d["daily_pull_open_gacha"]
    check("open_gacha 用正则锚定整词", og["expected"], ["^招募$"])
    check("open_gacha 的 roi 已排除免费招募横幅(y>=600)", og["roi"][1] >= 600, True)
    check("新增 still_home 兜底节点", "daily_pull_still_home" in d, True)
    nxt = d["daily_pull_find_gacha"]["next"]
    check("兜底排在滑动之前", nxt.index("daily_pull_still_home") < nxt.index("daily_pull_scroll"), True)
    check("兜底把控制权交回 open_gacha",
          d["daily_pull_still_home"]["next"], ["daily_pull_open_gacha"])


def test_gui_render_daily():
    """真建 Tk root 但 withdraw(),走真实 _render_view() 渲染日常页。

    静态断言看不出控件建不起来,也无显示环境(CI)时跳过,不算失败。
    """
    print("=== 6. GUI 日常页真渲染(无显示环境则跳过) ===")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print("  SKIP  无显示环境: %s" % e)
        return
    try:
        root.withdraw()
        app = gui.AutodoriGUI(root)
        app.view = "daily"
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
        check("渲染出「启动游戏」勾选项", any(t == "启动游戏" for t in shown), True)
        check("页面上没有任何「一键清理日常」", not any("一键清理日常" in t for t in shown), True)

        # 「启动游戏」必须点不动:真去 invoke 一下,勾选状态不能被取消
        def find_chk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Checkbutton) and str(c.cget("text")) == "启动游戏":
                    return c
                got = find_chk(c)
                if got is not None:
                    return got
            return None

        chk = find_chk(root)
        check("找到「启动游戏」勾选框", chk is not None, True)
        if chk is not None:
            chk.invoke()
            state = bool(int(chk.getvar(chk.cget("variable"))))
            check("点了它仍然是勾选状态(不可取消)", state, True)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_anim_wait_contract():
    print("=== 7. 抽卡动画等待:中断/兜底必须挂在「正在等待」的节点上 ===")
    d = json.loads(Path("assets/resource/pipeline/daily.json").read_text(encoding="utf-8"))
    cc, aw = d["daily_pull_confirm_click"], d["daily_pull_anim_wait"]

    # 2026-10-10 实机日志:[cur_node_=daily_pull_confirm_click] [list=["daily_pull_anim_wait"]]
    # -> 等待者是 confirm_click。interrupt/on_error 写在 anim_wait 上不生效;而且
    #    anim_wait 的 timeout:8000 也被父节点的 reco_timeout=20000 顶掉 ——
    #    等不到就 20s 判 "Task timeout",整个任务失败,游戏被丢在动画页。
    check("等待者 confirm_click 有 interrupt",
          "daily_pull_cut_anim" in (cc.get("interrupt") or []), True)
    check("等待者 confirm_click 有 on_error 兜底",
          cc.get("on_error"), ["daily_pull_tap_center"])
    # 实机(20:32):动画页 + 角色揭示页都只认「点击」,而原来要干等 90s 才点一次。
    # 改成秒级重试:等不到结果就点一下屏幕中央,再等,直到结算页的「招募结果」出现。
    check("等待者超时压到秒级(快速重试点击)",
          0 < cc.get("timeout", 99999) <= 6000, True)
    tc = d["daily_pull_tap_center"]
    check("tap_center 超时后交回重试节点", tc.get("on_error"), ["daily_pull_tap_retry"])
    check("重试节点回到 tap_center(平铺循环,不自引用)",
          d["daily_pull_tap_retry"]["next"], ["daily_pull_tap_center"])
    check("anim_wait 不再挂那份死代码 on_error", aw.get("on_error"), None)

    cut = d["daily_pull_cut_anim"]
    check("切动画节点认 TOUCH TO CUT", cut["template"], "daily/button/touch_to_cut.png")
    check("切动画节点是点击", cut["action"], "Click")
    check("模板文件真实存在",
          Path("assets/resource/image/daily/button/touch_to_cut.png").is_file(), True)

    # 每一处「等动画」都得有可用中断,否则同样的坑会换个位置再踩一次
    for n in ("daily_pull_confirm_click", "daily_pull_anim_wait", "daily_pull_result_check"):
        check("%s 的中断里含切动画" % n,
              "daily_pull_cut_anim" in (d[n].get("interrupt") or []), True)


def test_costume_popup():
    print("=== 8. 「获得3D服装」弹窗必须排在 next 最前(不是塞 interrupt) ===")
    d = json.loads(Path("assets/resource/pipeline/daily.json").read_text(encoding="utf-8"))
    ok = d["daily_pull_costume_ok"]

    # 2026-10-10 实机:MAA 把 next 排在 interrupt 之前检查。弹窗盖在结算页上时,
    # 下方会露出「免费」(y=651,弹窗底边 650) → daily_pull_again 抢先命中点了它,
    # 弹窗的「确定」虽然在 interrupt 里、也确实认得(0.9787),但永远轮不到 →
    # 弹窗留着 → 下游空等 20s → Task timeout。所以必须做成最高优先级的 next。
    check("弹窗节点用确定按钮做判据",
          ok["template"], "common/button/confirm/white.png")
    check("判据 ROI 限定在弹窗按钮区(不会撞上结算页的确定)", ok["roi"], [480, 490, 330, 175])
    check("判据是点击", ok["action"], "Click")
    # 抽到带服装的卡会**连着弹两个**:「获得3D服装」(确定在 y≈561)之后再来一个
    # 「获得服装」(矮一点,确定在 y≈523)。同一个节点要能罩住两个 —— ROI 顶边必须
    # 留够余量,实测两图 0.9787 / 1.0000,其余 12 张无弹窗画面全 ≤0.48。
    check("ROI 顶边留够余量(罩得住矮的那个弹窗)", ok["roi"][1] <= 500, True)

    for n in ("daily_pull_result_check", "daily_pull_again", "daily_pull_confirm_wait"):
        nxt = d[n]["next"]
        check("%s 里弹窗排第一" % n, nxt[0], "daily_pull_costume_ok")

    # 回归那张必须抢先的「免费」还在,只是排到弹窗后面
    check("「免费」仍在候选里", "daily_pull_again" in d["daily_pull_result_check"]["next"], True)


def main():
    test_flow_gating()
    test_bot_reads_both_switches()
    test_gui_config_keys()
    test_rename_and_default_on()
    test_anim_wait_contract()
    test_costume_popup()
    test_gui_render_daily()
    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

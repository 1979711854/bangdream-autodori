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

import yaml

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
    """记录 post_task 被要求跑的入口节点。

    boost_zero=True 时模拟「阶段1 的火罐检查发现火罐为 0」:
    daily 任务里 daily_boost_check 会把 A._daily_boost_zero 置位。
    fire_out=True 时模拟「打歌中火量不足」:main 任务里 HandleLiveBoost
    会把 A._daily_fire_exhausted 置位。
    """

    def __init__(self, boost_zero=False, fire_out=False):
        self.posted = []
        self.boost_zero = boost_zero
        self.fire_out = fire_out

    def post_task(self, node, pipeline=None):
        self.posted.append(node)
        if node == "daily" and self.boost_zero:
            A._daily_boost_zero = True
        if node == "main" and self.fire_out:
            A._daily_fire_exhausted = True

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
         patch.object(A, "_DAILY_MODE", True), \
         patch.object(A, "_get_override_pipeline", lambda: {}), \
         patch.object(A, "_DAILY_MAX_ROUNDS", 2), \
         patch.object(A, "_zero_boost_policy", lambda: "stop"):
        for clear, pull, want in (
            (False, False, ["daily"]),                     # 都不勾 -> 只做必经流程
            (True, False, ["daily", "main"]),              # 只清火(main 自己循环到火尽)
            (False, True, ["daily", "daily_pull"]),        # 只三抽
            # 都做(2026-10-17 新流程):每打完一首先查三抽再回去打,火不尽就一直
            # 交替下去;FakeTasker 不会置「火量不足」,所以跑满安全上限 2 轮
            # (阶段1 的 daily 只跑一次,之后是 main/daily_pull 交替),
            # 最后再跑一次收尾复核(force=True,用户要求"保险起见")。
            (True, True, ["daily"] + ["main", "daily_pull"] * 2 + ["daily_pull"]),
        ):
            fake.posted.clear()
            A._daily_boost_zero = False
            A._daily_fire_exhausted = False
            A._daily_pull_done = False
            A._DAILY_CLEAR_FIRE = clear
            A._DAILY_FREE_PULL = pull
            A._DAILY_PULL_EACH_SONG = clear and pull
            A._run_daily_flow("daily")
            check("清火=%s 三抽=%s -> 跑的节点" % (clear, pull), fake.posted, want)


def test_boost_zero_stops_daily():
    print("=== 1c. 火罐为 0:日常直接停止(2026-10-17 用户需求) ===")
    fake = FakeTasker(boost_zero=True)
    with patch.object(A, "maatasker", fake), \
         patch.object(A, "_get_override_pipeline", lambda: {}), \
         patch.object(A, "_DAILY_MAX_ROUNDS", 2):
        A._DAILY_CLEAR_FIRE = True
        A._DAILY_FREE_PULL = True
        A._DAILY_PULL_EACH_SONG = True
        A._run_daily_flow("daily")
    check("入口火罐0 -> 只跑阶段1,不打歌不三抽", fake.posted, ["daily"])

    # 打歌循环里火量不足:不再每首查三抽,只在收尾复核一次(用户 2026-10-17 要求)
    fake = FakeTasker(fire_out=True)
    with patch.object(A, "maatasker", fake), \
         patch.object(A, "_DAILY_MODE", True), \
         patch.object(A, "_get_override_pipeline", lambda: {}), \
         patch.object(A, "_DAILY_MAX_ROUNDS", 2), \
         patch.object(A, "_zero_boost_policy", lambda: "stop"):
        A._DAILY_CLEAR_FIRE = True
        A._DAILY_FREE_PULL = True
        A._DAILY_PULL_EACH_SONG = True
        A._daily_pull_done = False
        A._run_daily_flow("daily")
    # 2026-10-17:火尽后先回主界面(选项名「停在游戏主界面」),再收尾复核三抽
    check("打歌中火罐0 -> 打一首即停,回主页后只在收尾复核三抽",
          fake.posted, ["daily", "main", "activity_back_to_home", "daily_pull"])

    # 只清火不开三抽:火尽后自然结束(无三抽可跳)
    fake = FakeTasker(fire_out=True)
    with patch.object(A, "maatasker", fake), \
         patch.object(A, "_DAILY_MODE", True), \
         patch.object(A, "_get_override_pipeline", lambda: {}), \
         patch.object(A, "_DAILY_MAX_ROUNDS", 2), \
         patch.object(A, "_zero_boost_policy", lambda: "stop"):
        A._DAILY_CLEAR_FIRE = True
        A._DAILY_FREE_PULL = False
        A._DAILY_PULL_EACH_SONG = False
        A._run_daily_flow("daily")
    check("只清火 -> 火尽回主页收工",
          fake.posted, ["daily", "main", "activity_back_to_home"])

    # 源码级:2026-10-17 合并入口后,火罐为 0 走三态策略(见 _zero_boost_policy):
    #   play  = 继续打歌(直接 return,不进清火收尾分支)
    #   stop  = 清火阶段 stop 收尾,日常照常做三抽
    #   exit  = 非日常(命令行 main / 挑战演出)退出游戏
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    boost = src[src.index("class HandleLiveBoost"):src.index("@maaresource.custom_action(\"HandleDailyBoostZero\")")]
    check("打歌中火罐不足走三态策略", "_zero_boost_policy()" in boost, True)
    check("play 策略在清火收尾分支之前(0 火继续打)",
          boost.index('if policy == "play":')
          < boost.index("_daily_fire_exhausted = True"), True)
    check("清火阶段用 stop 节点收尾(不请求进程退出)",
          '_run_node_once(context, "stop")' in boost, True)
    check("exit 策略交给 _exit_game_and_stop_task",
          "_exit_game_and_stop_task" in boost, True)
    dz = src[src.index("class HandleDailyBoostZero"):src.index("@maaresource.custom_action(\"MarkDailyPullDone\")")]
    check("入口检查同样走三态策略", "_zero_boost_policy()" in dz, True)
    check("入口检查 play 策略放行", 'if policy == "play":' in dz, True)
    check("入口检查 OCR 读不出时放行", "放行" in dz, True)

    # 管线:daily_home 认出主页后先查火罐,再点公告/停
    d = json.loads(Path("assets/resource/pipeline/daily.json").read_text(encoding="utf-8"))
    check("daily_home 先查火罐", d["daily_home"]["next"][0], "daily_boost_check")
    bc = d["daily_boost_check"]
    check("火罐检查用 LiveBoostEnoughRecognition",
          bc["custom_recognition"], "LiveBoostEnoughRecognition")
    check("火罐检查动作是 HandleDailyBoostZero",
          bc["custom_action"], "HandleDailyBoostZero")


def test_pull_each_song_mode():
    print("=== 1b. 交替模式:每首打完先三抽再继续打(2026-10-17) ===")
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    flow = src[src.index("def _run_daily_flow"):src.index("def _main_impl")]
    check("交替模式由两个开关同开触发",
          "_DAILY_PULL_EACH_SONG = _DAILY_CLEAR_FIRE and _DAILY_FREE_PULL" in src, True)
    check("交替循环里先 main 后三抽",
          flow.index('post_task("main"') < flow.index('_pull("第 %d 首打完"'), True)
    check("火量不足即退出循环(有 _daily_fire_exhausted 判据)",
          "_daily_fire_exhausted" in flow, True)
    check("main 任务异常不再重试(直接 break)",
          "打歌阶段异常结束" in src, True)
    # 2026-10-17 用户要求:三抽只在没确认做完时检查,收尾再复核一次
    check("三抽确认做完后跳过检查", "if _daily_pull_done and not force:" in src, True)
    check("收尾复核走 force=True", 'force=True' in flow, True)
    check("标记动作 MarkDailyPullDone 已注册",
          'custom_action("MarkDailyPullDone")' in src, True)
    d2 = json.loads(Path("assets/resource/pipeline/daily.json").read_text(encoding="utf-8"))
    for nm in ("daily_pull_done", "daily_pull_locked"):
        check("%s 挂了标记动作" % nm,
              d2[nm].get("custom_action"), "MarkDailyPullDone")

    # override:交替模式下结算/奖励/打崩三条路都必须回主页停(daily_song_done),
    # 不能再去 liveagain(再次演出 = 永不停)
    ov = A._get_override_pipeline.__doc__  # 占位防呆
    with patch.object(A, "_DAILY_PULL_EACH_SONG", True), \
         patch.object(A, "SPECIAL_MODE", False), \
         patch.object(A, "DIFFICULTY", "hard"), \
         patch.object(A, "LIVEMODE", "freelive"):
        pipes = A._get_override_pipeline()
    check("save_succeed_playresult 去掉 liveagain(改点 activity_liveagain)",
          "liveagain" not in pipes["save_succeed_playresult"]["next"], True)
    check("save_succeed_playresult 先认再次演出",
          "activity_liveagain" in pipes["save_succeed_playresult"]["next"], True)
    check("save_succeed_playresult 不再点「确定」(confirm_button 已摘掉)",
          "confirm_button" not in pipes["save_succeed_playresult"]["interrupt"], True)
    check("save_succeed_playresult 交回 daily_song_done",
          pipes["save_succeed_playresult"]["next"][-1], "daily_song_done")
    check("event_reward_confirm 也先认再次演出再回主页",
          pipes["event_reward_confirm"]["next"], ["activity_liveagain", "daily_song_done"])
    check("handle_life_exhausted 也交回 daily_song_done",
          pipes["handle_life_exhausted"]["next"], ["daily_song_done"])
    d = json.loads(Path("assets/resource/pipeline/live.json").read_text(encoding="utf-8"))
    check("live.json 有 daily_song_done 节点", "daily_song_done" in d, True)
    check("daily_song_done 回主页后即停(stop 在 next 里)",
          "stop" in d["daily_song_done"]["next"], True)
    # 非交替模式不能改写(只清火或只三抽时 main 仍要内部循环)
    with patch.object(A, "_DAILY_PULL_EACH_SONG", False), \
         patch.object(A, "SPECIAL_MODE", False), \
         patch.object(A, "DIFFICULTY", "hard"), \
         patch.object(A, "LIVEMODE", "freelive"):
        pipes = A._get_override_pipeline()
    check("非交替模式不改写结算节点",
          "save_succeed_playresult" not in pipes, True)

    # 2026-10-17 新流程:三抽确认完成后切"连打"(_daily_song_stop=False),这时
    # 不能再改写结算/打崩节点,否则又是"每首打完回主页"(用户反馈太麻烦)。
    with patch.object(A, "_DAILY_PULL_EACH_SONG", True), \
         patch.object(A, "_daily_song_stop", False), \
         patch.object(A, "SPECIAL_MODE", False), \
         patch.object(A, "DIFFICULTY", "hard"), \
         patch.object(A, "LIVEMODE", "freelive"):
        pipes = A._get_override_pipeline()
    check("三抽确认后(连打模式)不改写结算节点",
          "save_succeed_playresult" not in pipes, True)
    check("三抽确认后不改写打崩节点",
          "handle_life_exhausted" not in pipes, True)
    check("每次都从「每首一停」开始", "_daily_song_stop = True" in flow, True)
    check("确认完成后切连打", "_daily_song_stop = False" in flow, True)
    check("连打结束先回主页再收尾复核", "_return_to_home" in flow, True)
    # 2026-10-17:选项名「停在游戏主界面」,火尽后必须真的退回主页再收尾
    check("火尽后退回游戏主界面", '_return_to_home("火罐不足")' in flow, True)
    check("三抽标记带原因(done/locked)",
          '"reason": "locked"' in Path(
              "assets/resource/pipeline/daily.json").read_text(encoding="utf-8"), True)


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
    # 2026-10-17 二次改名:显示名改成「继续挖矿」(配置值仍是 play)
    app.boost_mode = "继续挖矿"
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
        # 2026-10-17 合并入口:启动游戏是必经流程,daily_enabled 是死配置,不再落盘
        check("daily_enabled 不再落盘", "daily_enabled" in saved, False)
        check("daily_auto_clear_fire 落盘", saved.get("daily_auto_clear_fire"), True)
        check("daily_auto_free_pull 落盘", saved.get("daily_auto_free_pull"), False)
        check("旧键 daily_auto_start 已不再写", "daily_auto_start" in saved, False)
        check("旧键 daily_claim_login 已不再写", "daily_claim_login" in saved, False)

        # _write_config 写的是 bot 实时读的 data/config.yml,daily 块同样只该有 3 个键。
        # **它写的是 YAML**(见 gui._write_config:与 bot 侧口径一致,还要逐键合并用户手改的
        # 配置),所以这里必须 yaml.safe_load —— 早先写的是 json.loads,实现切成 YAML 之后就
        # 一直抛 JSONDecodeError,而那是个未捕获异常,连带后面所有用例都不跑。
        cfg = Path(d) / "config.yml"
        with patch.object(gui, "CONFIG", str(cfg)):
            app._write_config()
        written = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        check("config.yml 的 daily 块", sorted(written["daily"]),
              ["auto_clear_fire", "auto_free_pull"])
        # 火罐为0三态:2026-10-17 合并入口后只剩 on_zero_boost 一个键
        check("config.yml 写 on_zero_boost", written.get("on_zero_boost"), "play")
        check("旧键 play_at_zero_boost 不再写",
              "play_at_zero_boost" in written, False)
        # 2026-10-17 二次改名:停在游戏主界面 / 继续挖矿 / 退出游戏
        check("三态显示名已更新", list(gui.BOOST_MODES),
              ["停在游戏主界面", "继续挖矿", "退出游戏"])
        check("老显示名能迁移(停止日常→停在游戏主界面)",
              gui.LEGACY_BOOST.get("停止日常"), "停在游戏主界面")
        check("老显示名能迁移(继续打歌刷奖励→继续挖矿)",
              gui.LEGACY_BOOST.get("继续打歌刷奖励"), "继续挖矿")

    print("=== 4. GUI 源码里不再有那两个回调/属性 ===")
    src = Path("gui.py").read_text(encoding="utf-8")
    for name in ("daily_auto_start", "daily_claim_login",
                 "_on_daily_auto_start", "_on_daily_claim_login"):
        check("gui.py 不含 %s" % name, name in src, False)


def test_rename_and_default_on():
    print("=== 5. 日常页改名 / 默认勾选(2026-10-10 用户要求) ===")
    src = Path("gui.py").read_text(encoding="utf-8")
    # 2026-10-17 用户要求:左栏词条从「日常」再改为「清理日常」
    check("左栏条目改名为「清理日常」",
          '("daily", "清理日常", "clipboard-check")' in src, True)
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
    # still_home 的 next 允许在前面挂「回到主页先把公告点掉」的节点 ——
    # 公告弹窗每次回主页都会再出现(见 SKILL.md 铁律),所以 song_announce_confirm
    # 排第一是对的;这里只要求**最后**必须交回 open_gacha。
    check("兜底把控制权交回 open_gacha",
          d["daily_pull_still_home"]["next"][-1], "daily_pull_open_gacha")


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


class FakeBoostCtx:
    """记录被请求执行的节点名(HandleLiveBoost 通过 run_action 请求 stop/close_app)。"""

    def __init__(self):
        self.actions = []

    def run_action(self, entry):
        self.actions.append(entry)


class _BoostArgv:
    """HandleLiveBoost 只读 argv.reco_detail.best_result.detail(OCR 出的火量文本)。"""

    def __init__(self, text):
        class _Best:
            detail = text

        class _Detail:
            best_result = _Best()

        self.reco_detail = _Detail()


def _run_boost(text, clear_fire, play_at_zero):
    """跑一次 HandleLiveBoost,返回它实际请求执行的节点列表。"""
    ctx = FakeBoostCtx()
    with patch.object(A, "_DAILY_CLEAR_FIRE", clear_fire), \
         patch.object(A, "_DAILY_MODE", clear_fire), \
         patch.object(A, "MIN_LIVEBOOST", 1), \
         patch.object(A, "_runtime_config", lambda: {"play_at_zero_boost": play_at_zero}), \
         patch.object(A, "_wait_game_exit", lambda *_: True), \
         patch.object(A, "_request_exit", lambda *_: None):
        A.HandleLiveBoost().run(ctx, _BoostArgv(text))
    return ctx.actions


def test_clear_fire_ends_on_low_boost():
    """清火阶段的收工判据必须是**火量**,且在 play_at_zero_boost 之前判。

    2026-10-15 实机:开着「火罐0继续」(play_at_zero_boost=True),火为 0 之后又连打
    3 首,阶段3「每日免费三抽」始终没执行 —— 收工点当时错挂在 HandleLifeExhausted
    (生命值耗尽)上,正常打完根本走不到那一步。
    """
    print("=== 9. 清火完成:火量不足即收工(阶段3 才接得上) ===")

    check("清火模式 + 火量0 -> 只请求 stop(不关游戏,外层才能接着三抽)",
          _run_boost("0", True, True), ["stop"])
    check("清火模式 + 火量0(即便关掉 play_at_zero) -> 同样只 stop",
          _run_boost("0", True, False), ["stop"])
    check("清火模式 + 火量够 -> 什么都不做,继续打",
          _run_boost("5", True, True), [])
    check("非清火模式 + 火量0 + 火罐0继续 -> 继续打(旧行为不变)",
          _run_boost("0", False, True), [])
    check("非清火模式 + 火量0 + 不继续 -> 关游戏退出",
          _run_boost("0", False, False), ["close_app", "stop"])

    # 源码级防回退:收工点必须在 HandleLiveBoost,且排在 play_at_zero_boost 之前
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    boost = src[src.index("class HandleLiveBoost"):src.index("class HandleLifeExhausted")]
    check("清火判定在 HandleLiveBoost 里", "_DAILY_CLEAR_FIRE" in boost, True)
    check("0 火策略在清火收尾分支之前求值",
          boost.index("policy = _zero_boost_policy()")
          < boost.index("if _DAILY_CLEAR_FIRE:"), True)
    check("play 策略直接放行(不进 stop 分支)",
          boost.index('if policy == "play":')
          < boost.index("_daily_fire_exhausted = True"), True)
    life = src[src.index("class HandleLifeExhausted"):][:900]
    check("HandleLifeExhausted 不再挂清火分支", "_DAILY_CLEAR_FIRE" not in life, True)


def test_select_live_home_back():
    print("=== 1d. 「选择演出」界面卡死修复(2026-10-17 实机空转) ===")
    src = Path("src/autodori.py").read_text(encoding="utf-8")
    d = json.loads(Path("assets/resource/pipeline/start.json").read_text(encoding="utf-8"))

    # 判据:OCR「选择演出」标题(已在实机截图验证命中),动作点左上角返回箭头
    sb = d["select_live_home_back"]
    check("返回节点 OCR 认「选择演出」", sb["expected"], ["选择演出"])
    check("ROI 只框标题(不含返回箭头)", sb["roi"], [90, 30, 320, 70])
    check("点击目标是左上角返回箭头(固定坐标)", sb["target"], [29, 40, 130, 90])
    # 挂在 start_app.next 的 select_song 之后:选曲列表兜底保持优先
    nxt = d["start_app"]["next"]
    check("start_app.next 含返回节点", "select_live_home_back" in nxt, True)
    check("返回节点排在 select_song 之后",
          nxt.index("select_song") < nxt.index("select_live_home_back"), True)
    # main 模式(默认定义):回主页后 start_app 重试,live_home_button 命中进打歌循环
    check("main 模式回主页后重试 start_app", sb["next"], ["start_app"])

    # daily 模式:override 接回 daily_home,走完整阶段1(火罐检查+公告+停)
    with patch.object(A, "_DAILY_MODE", True), \
         patch.object(A, "SPECIAL_MODE", False), \
         patch.object(A, "DIFFICULTY", "hard"), \
         patch.object(A, "LIVEMODE", "freelive"):
        pipes = A._get_override_pipeline()
    check("daily 模式返回节点接 daily_home",
          pipes["select_live_home_back"]["next"], ["daily_home"])
    with patch.object(A, "_DAILY_MODE", False), \
         patch.object(A, "SPECIAL_MODE", False), \
         patch.object(A, "DIFFICULTY", "hard"), \
         patch.object(A, "LIVEMODE", "freelive"):
        pipes = A._get_override_pipeline()
    check("main 模式不改写返回节点",
          "select_live_home_back" not in pipes, True)
    check("daily 模式标志在 _main_impl 里置位",
          "_DAILY_MODE = True" in src, True)


def main():
    test_flow_gating()
    test_pull_each_song_mode()
    test_boost_zero_stops_daily()
    test_select_live_home_back()
    test_bot_reads_both_switches()
    test_gui_config_keys()
    test_rename_and_default_on()
    test_anim_wait_contract()
    test_costume_popup()
    test_clear_fire_ends_on_low_boost()
    test_gui_render_daily()
    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

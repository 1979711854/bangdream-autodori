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


def main():
    test_flow_gating()
    test_bot_reads_both_switches()
    test_gui_config_keys()
    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

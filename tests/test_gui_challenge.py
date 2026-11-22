import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.body = self
        self.slot = self
        self.inner = self
        self.enabled = None
        self.exists = True

    def pack(self, *args, **kwargs):
        return self

    def grid(self, *args, **kwargs):
        return self

    def columnconfigure(self, *args, **kwargs):
        pass

    def winfo_children(self):
        return []

    def winfo_exists(self):
        return self.exists

    def set_enabled(self, enabled):
        self.enabled = enabled


class FakeButton(FakeWidget):
    def __init__(self, parent, label, command, **kwargs):
        super().__init__()
        self.label = label
        self.command = command


class FakeProcess:
    def __init__(self, running):
        self.running = running

    def poll(self):
        return None if self.running else 0


class GuiChallengeTests(unittest.TestCase):
    def make_app(self):
        app = gui.AutodoriGUI.__new__(gui.AutodoriGUI)
        app.difficulty = "expert"
        app.challenge_difficulty = "hard"
        app.challenge_cp_multiplier = 8
        app.challenge_exit_game = False
        app.special_song = "song-id"
        app.device_address = ""
        app.adb_path = ""
        return app

    def test_each_button_has_separate_bot_arguments(self):
        app = self.make_app()
        # 2026-10-17 合并入口:常规打歌统一走日常(main 按钮已删),
        # 日常也要吃「打歌设置」里的难度。
        self.assertEqual(app._bot_args("daily"), [
            "--mode", "daily", "--difficulty", "expert"])
        self.assertEqual(app._bot_args("challenge"), [
            "--mode", "main", "--difficulty", "hard", "--livemode", "challengelive",
            "--challenge-cp-multiplier", "8", "--challenge-finish", "home"])
        app.challenge_exit_game = True
        self.assertEqual(app._bot_args("challenge")[-2:], ["--challenge-finish", "exit"])
        self.assertEqual(app._bot_args("special"), [
            "--mode", "special", "--special-song", "song-id"])
        app.challenge_difficulty = "easy"
        self.assertEqual(app._bot_args("challenge")[3], "easy")
        self.assertEqual(app._bot_args("daily")[3], "expert")
        for mode in ("daily", "special", "story_member", "story_memoir", "event_story"):
            with self.subTest(mode=mode):
                self.assertNotIn("--challenge-cp-multiplier", app._bot_args(mode))
        for address in ("127.0.0.1:16384", "127.0.0.1:16416"):
            app.device_address = address
            for mode in ("daily", "special", "challenge"):
                with self.subTest(mode=mode, address=address):
                    args = app._bot_args(mode)
                    self.assertEqual(args.count("--device"), 1)
                    self.assertEqual(args[-2:], ["--device", address])

    def test_challenge_settings_survive_reload_from_legacy_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gui_config.json"
            # 从旧格式启动，再重启确认写回的是整数而且能恢复用户选择。
            path.write_text(json.dumps({"difficulty": "expert", "view": "live.challenge"}),
                            encoding="utf-8")
            with patch.object(gui, "GUI_CONFIG", str(path)), \
                 patch.object(gui, "CONFIG", str(Path(folder) / "config.yml")), \
                 patch.object(gui.T, "set_theme"), \
                 patch.object(gui.W, "set_base_size"), \
                 patch.object(gui.AutodoriGUI, "_build"), \
                 patch.object(gui.AutodoriGUI, "_sync_run_state"):
                app = gui.AutodoriGUI(Mock())
                self.assertEqual(app.challenge_cp_multiplier, 8)
                app._on_challenge_difficulty("normal")
                app._on_challenge_cp_multiplier("400（2 倍）")
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["challenge_difficulty"], "normal")
                self.assertIs(type(saved["challenge_cp_multiplier"]), int)
                self.assertEqual(saved["challenge_cp_multiplier"], 2)
                self.assertEqual(saved["difficulty"], "expert")
                self.assertEqual(saved["view"], "live.challenge")
                self.assertEqual(app._load_gui_config(), saved)
                self.assertEqual(gui.AutodoriGUI(Mock()).challenge_cp_multiplier, 2)
                app._on_challenge_exit_game(True)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(saved["challenge_exit_game"])
                app._on_challenge_exit_game(False)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(saved["challenge_exit_game"])

    def test_challenge_page_button_and_view_lifetime(self):
        app = self.make_app()
        app.font_size = 10
        app.host = FakeWidget()
        app.view = "live.challenge"
        app.nav_items = {"live.challenge": type("Nav", (), {"select": lambda *a: None})()}
        app.proc = None
        app.start_btn = FakeWidget()
        app.stop_btn = FakeWidget()
        started = []
        app.start = lambda mode="main": started.append(mode)
        app._sync_run_state = lambda: None
        app._device_row = lambda master: None
        app._save_gui_config = Mock()
        with patch.object(gui.T, "get", return_value={"app_bg": "white", "surface": "white",
                                                     "text_2": "black", "text_3": "gray"}), \
             patch.object(gui.T, "font", return_value="font"), \
             patch.object(gui.tk, "Frame", FakeWidget), \
             patch.object(gui.tk, "Label", FakeWidget), \
             patch.object(gui.tk, "BooleanVar"), \
             patch.object(gui.tk, "Checkbutton", FakeWidget), \
             patch.object(gui.W, "ScrolledFrame", FakeWidget), \
             patch.object(gui.W, "Card", FakeWidget), \
             patch.object(gui.W, "SectionTitle", FakeWidget), \
             patch.object(gui.W, "Row", side_effect=FakeWidget) as row, \
             patch.object(gui.W, "DropdownBox", side_effect=FakeWidget) as dropdown, \
             patch.object(gui.W, "PushButton", FakeButton):
            app._card_run = lambda master: FakeWidget()
            app._render_view()
            self.assertEqual(row.call_args.args[1], "CP 倍率")
            cp_dropdown = dropdown.call_args
            self.assertEqual(list(cp_dropdown.args[1]), [
                "1600（8 倍）", "800（4 倍）", "400（2 倍）", "200（1 倍）"])
            self.assertEqual(cp_dropdown.kwargs["value"], "1600（8 倍）")
            for label, multiplier in (("800（4 倍）", "4"), ("400（2 倍）", "2"),
                                      ("1600（8 倍）", "8"), ("200（1 倍）", "1")):
                with self.subTest(label=label):
                    cp_dropdown.kwargs["on_change"](label)
                    args = app._bot_args("challenge")
                    self.assertEqual(args[args.index("--challenge-cp-multiplier") + 1], multiplier)
            first = app.challenge_start_btn
            self.assertEqual(first.label, "开始清 CP")
            first.command()
            self.assertEqual(started, ["challenge"])
            app.view = "live.show"
            app._card_detail = lambda master: FakeWidget()
            app._render_view()
            self.assertIsNone(app.challenge_start_btn)
            app.view = "live.challenge"
            app._card_detail = lambda master: gui.AutodoriGUI._detail_challenge(app, master)
            app._render_view()
            self.assertIsNot(first, app.challenge_start_btn)
            self.assertEqual(dropdown.call_args.kwargs["value"], "200（1 倍）")

    def test_challenge_button_tracks_process_and_cp_logs_are_key_events(self):
        app = self.make_app()
        app.start_btn = FakeWidget()
        app.stop_btn = FakeWidget()
        app.challenge_start_btn = FakeWidget()
        app.special_start_btn = FakeWidget()
        app.proc = FakeProcess(True)
        with patch.object(gui.T, "get", return_value={}):
            app._sync_run_state()
            self.assertFalse(app.challenge_start_btn.enabled)
            app.proc = None
            app._sync_run_state()
            self.assertTrue(app.challenge_start_btn.enabled)
        for message in (
            "剩余 12303 CP，本次使用 8 倍（1600 CP）。",
            "剩余 103 CP，不足 200 CP，准备返回主页面。",
            "剩余 103 CP，不足 200 CP，准备退出游戏。",
            "无法识别挑战点数，停止挑战演出。",
        ):
            self.assertTrue(app._should_show(message, "INFO"), message)


if __name__ == "__main__":
    unittest.main()

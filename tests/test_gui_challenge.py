import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        app.challenge_exit_game = False
        app.special_song = "song-id"
        app.device_address = ""
        app.adb_path = ""
        return app

    def test_each_button_has_separate_bot_arguments(self):
        app = self.make_app()
        self.assertEqual(app._bot_args("main"), [
            "--mode", "main", "--difficulty", "expert", "--livemode", "freelive"])
        self.assertEqual(app._bot_args("challenge"), [
            "--mode", "main", "--difficulty", "hard", "--livemode", "challengelive",
            "--challenge-finish", "home"])
        app.challenge_exit_game = True
        self.assertEqual(app._bot_args("challenge")[-2:], ["--challenge-finish", "exit"])
        self.assertEqual(app._bot_args("special"), [
            "--mode", "special", "--special-song", "song-id"])
        app.challenge_difficulty = "easy"
        self.assertEqual(app._bot_args("challenge")[3], "easy")
        self.assertEqual(app._bot_args("main")[3], "expert")
        for address in ("127.0.0.1:16384", "127.0.0.1:16416"):
            app.device_address = address
            for mode in ("main", "special", "challenge"):
                with self.subTest(mode=mode, address=address):
                    args = app._bot_args(mode)
                    self.assertEqual(args.count("--device"), 1)
                    self.assertEqual(args[-2:], ["--device", address])

    def test_challenge_difficulty_is_saved_separately(self):
        app = self.make_app()
        app.theme = "light"
        app.font_size = 10
        app.window_size = gui.DEFAULT_WINDOW
        app.view = "live.challenge"
        app.boost_mode = "继续打歌"
        app.life_mode = "自动退出重新选歌"
        app.auto_cal = False
        app.song_strategy = "挖矿为主"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gui_config.json"
            with patch.object(gui, "GUI_CONFIG", str(path)):
                app._on_challenge_difficulty("normal")
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["challenge_difficulty"], "normal")
                self.assertEqual(saved["difficulty"], "expert")
                self.assertEqual(saved["view"], "live.challenge")
                self.assertEqual(app._load_gui_config(), saved)
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
             patch.object(gui.W, "Row", FakeWidget), \
             patch.object(gui.W, "DropdownBox", FakeWidget), \
             patch.object(gui.W, "PushButton", FakeButton):
            app._card_run = lambda master: FakeWidget()
            app._render_view()
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

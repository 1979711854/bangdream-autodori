import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from challenge import ChallengeCPRecognition, SelectChallengeCP, challenge_overrides, choose_cp_multiplier, parse_cp_balance


class FakeContext:
    def __init__(self, click_ok=True, override_ok=True):
        self.actions = []
        self.overrides = []
        self.click_ok = click_ok
        self.override_ok = override_ok

    def run_action(self, name, pipeline_override):
        self.actions.append(pipeline_override[name])
        return SimpleNamespace(completed=self.click_ok)

    def override_pipeline(self, override):
        self.overrides.append(override)
        return self.override_ok


def selection_arg(balance, finish="home"):
    result = SimpleNamespace(detail=json.dumps({"cp": balance, "origin": [334, 36]}))
    return SimpleNamespace(reco_detail=SimpleNamespace(best_result=result),
                           custom_action_param=json.dumps({"finish": finish}))


class ChallengeTests(unittest.TestCase):
    def test_multiplier_boundaries(self):
        for balance, expected in ((0, 0), (199, 0), (200, 1), (399, 1), (400, 2),
                                  (799, 2), (800, 4), (1599, 4), (1600, 8), (12303, 8)):
            with self.subTest(balance=balance):
                self.assertEqual(choose_cp_multiplier(balance), expected)

    def test_clears_full_balance_with_remainder_below_one_cost(self):
        balance, multipliers = 12303, []
        while multiplier := choose_cp_multiplier(balance):
            multipliers.append(multiplier)
            balance -= multiplier * 200
        self.assertEqual(multipliers, [8] * 7 + [4, 1])
        self.assertEqual(balance, 103)

    def test_ocr_numbers_and_invalid_readings(self):
        for text in ("12303", "12,303", "１２，３０３", " 12 303 "):
            self.assertEqual(parse_cp_balance(text), 12303)
        for text in ("", "CP12303", "12O03", "-200", "123/200", "1600消耗"):
            self.assertIsNone(parse_cp_balance(text))

    def test_selection_updates_start_cost_for_each_fresh_balance(self):
        action = SelectChallengeCP()
        for balance, cost, row in ((1600, 1600, 396), (1599, 800, 323),
                                   (799, 400, 250), (399, 200, 177)):
            with self.subTest(balance=balance):
                context = FakeContext()
                self.assertTrue(action.run(context, selection_arg(balance)))
                self.assertEqual(context.actions[0]["target"], [868, 36 + row - 10, 20, 20])
                self.assertEqual(context.actions[1]["target"], [669, 602, 200, 39])
                pattern = context.overrides[0]["challenge_start"]["expected"]
                self.assertRegex(f"CP {cost} 消耗", pattern)
                self.assertNotRegex(f"1{cost}消耗", pattern)

    def test_dialog_recognition_uses_framework_list_box(self):
        class RecognitionContext:
            def run_recognition(self, entry, image, pipeline_override):
                if entry == "_challenge_cp_heading":
                    return SimpleNamespace(best_result=SimpleNamespace(box=[362, 64, 558, 37]))
                self.balance_roi = pipeline_override[entry]["roi"]
                return SimpleNamespace(best_result=SimpleNamespace(text="12303"))
        context = RecognitionContext()
        result = ChallengeCPRecognition().analyze(context, SimpleNamespace(image=None))
        self.assertEqual(result.box, [334, 36, 611, 647])
        self.assertEqual(json.loads(result.detail), {"cp": 12303, "origin": [334, 36]})
        self.assertEqual(context.balance_roi, [684, 124, 156, 32])

    def test_unreadable_cp_does_not_finish_or_click(self):
        for finish in ("home", "exit"):
            context = FakeContext()
            self.assertFalse(SelectChallengeCP().run(context, selection_arg(None, finish)))
            self.assertEqual(context.actions, [])
            self.assertEqual(context.overrides, [])

    def test_insufficient_cp_routes_to_selected_finish_without_starting(self):
        for balance in (0, 103, 199):
            for finish, entry in (("home", "challenge_finish_home"), ("exit", "close_app")):
                with self.subTest(balance=balance, finish=finish):
                    context = FakeContext()
                    self.assertTrue(SelectChallengeCP().run(context, selection_arg(balance, finish)))
                    self.assertEqual(context.actions, [])
                    override = context.overrides[0]
                    self.assertEqual(override["challenge_cp_dialog"]["next"], entry)
                    self.assertEqual(override["challenge_finish_home"]["target"], [394, 602, 235, 39])
                    self.assertNotIn("challenge_start", override)
        context = FakeContext(override_ok=False)
        self.assertFalse(SelectChallengeCP().run(context, selection_arg(103)))

    def test_failed_selection_does_not_confirm(self):
        context = FakeContext(click_ok=False)
        self.assertFalse(SelectChallengeCP().run(context, selection_arg(1600)))
        self.assertEqual(len(context.actions), 1)
        self.assertEqual(context.overrides, [])
        context = FakeContext(override_ok=False)
        self.assertFalse(SelectChallengeCP().run(context, selection_arg(1600)))
        self.assertEqual(len(context.actions), 1)

    def test_challenge_route_does_not_require_liveboost_or_random_song(self):
        overrides = challenge_overrides()
        self.assertIn("challenge_back_to_song", overrides["main"]["next"])
        self.assertEqual(overrides["select_song"]["next"], ["set_difficulty"])
        self.assertEqual(overrides["set_difficulty"]["interrupt"], [])
        self.assertEqual(overrides["get_song_name"]["interrupt"], [])
        for name, override in overrides.items():
            for field in ("next", "interrupt"):
                value = override.get(field, [])
                nodes = [value] if isinstance(value, str) else value
                self.assertNotIn("ensure_liveboost", nodes, name)
                self.assertNotIn("random_choice_song", nodes, name)
        self.assertEqual(overrides["challenge_cp_dialog"]["on_error"], "stop")
        self.assertEqual(overrides["challenge_start"]["on_error"], "stop")
        # Keep the release's exit/confirmation handling on failed lives.
        self.assertNotIn("save_failed_playresult", overrides)
        self.assertIn("event_reward_confirm", overrides["save_succeed_playresult"]["next"])
        self.assertEqual(overrides["challenge_finish_home"]["next"], "to_tome")
        self.assertEqual(overrides["challenge_cp_dialog"]["custom_action_param"], {"finish": "home"})
        self.assertEqual(challenge_overrides("exit")["challenge_cp_dialog"]["custom_action_param"], {"finish": "exit"})


if __name__ == "__main__":
    unittest.main()

"""Regression for the missing simultaneous tap in song 655 HARD."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api import BestdoriAPI
from chart import Chart


class LocalChartTests(unittest.TestCase):
    def test_fixed_chart_bypasses_cache_and_generates_both_touches(self):
        with patch.object(BestdoriAPI, "_fetch_and_cache",
                          side_effect=AssertionError("Must use local chart")):
            chart = Chart(("655", "hard"))
        chart.notes_to_actions((1280, 720), 10)
        checkpoints = sum(
            1 if n["type"] in ("Single", "Directional") else
            sum(not c.get("hidden", False) for c in n.get("connections", []))
            for n in chart._chart_data
        )
        self.assertEqual(checkpoints, 487)
        downs = [a for a in chart.actions if a["type"] == "down"
                 and abs(a["time"] - 49 * 60000 / 183) < 1e-6]
        self.assertEqual(len(downs), 2)
        self.assertEqual({a["pos"] for a in downs}, {(347.5, 590), (1082.5, 590)})
        self.assertEqual(len({a["finger"] for a in downs}), 2)
        self.assertNotIn(None, {a["finger"] for a in downs})

    def test_other_songs_and_difficulties_keep_normal_loading(self):
        for song_id, difficulty in [(655, "expert"), (654, "hard")]:
            with self.subTest(song_id=song_id, difficulty=difficulty):
                with patch.object(BestdoriAPI, "_fetch_and_cache", return_value=[]) as fetch:
                    self.assertEqual(BestdoriAPI.get_chart(song_id, difficulty), [])
                fetch.assert_called_once_with(
                    f"https://bestdori.com/api/charts/{song_id}/{difficulty}.json",
                    f"{song_id}-{difficulty}",
                )


if __name__ == "__main__":
    unittest.main()

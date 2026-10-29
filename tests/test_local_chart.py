"""本地谱面覆盖(chart_overrides)的回归:Bestdori 数据与游戏内谱面不一致的修正。

背景(2026-10-26):
- #655-hard:游戏结算 487 个判定点,Bestdori 只有 486 —— 缺 beat 49 / 第 7 轨
  的普通单键(SSM 的 BMS 里该位置对象写的是 01=bgm,游戏照样判;上游 PR #13)。
- #35/#681、#147/#674、#144-hard、#710-expert:Bestdori 把「fever 滑条尾」标成了
  flick,游戏数据里那是普通滑条尾(fever_note_slide_end_a / fever_slide_end_a,
  没有 flick 变体)。按 flick 上划会离轨导致尾判固定 miss,条数 = 这类尾巴条数。

覆盖文件是**完整谱面**(Bestdori 原格式),放在 assets/chart_overrides/,
加载顺序必须早于磁盘缓存(缓存里就是错的数据)。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from api import BestdoriAPI  # noqa: E402
from chart import Chart  # noqa: E402


def checkpoints(chart):
    count = 0
    for note in chart:
        if note["type"] in ("Single", "Directional"):
            count += note.get("width") or 1
        elif note["type"] in ("Long", "Slide"):
            count += sum((c.get("width") or 1) for c in note["connections"])
    return count


def slide_tail_flicks(chart):
    return sum(1 for note in chart
               if note["type"] == "Slide" and note["connections"][-1].get("flick"))


class LocalChartTests(unittest.TestCase):
    def setUp(self):
        # 覆盖目录按 CWD 解析(与 assets/resource 一致),测试固定在工作区根。
        self._cwd = Path.cwd()
        import os
        os.chdir(ROOT)

    def tearDown(self):
        import os
        os.chdir(self._cwd)

    def test_655_hard_uses_override_and_generates_both_touches(self):
        with patch.object(BestdoriAPI, "_fetch_and_cache",
                          side_effect=AssertionError("必须走本地覆盖,不能查缓存/网络")):
            chart = Chart(("655", "hard"))
        chart.notes_to_actions((1280, 720), 10)
        self.assertEqual(checkpoints(chart._chart_data), 487)

        tail_time = 49 * 60000 / 183
        downs = [a for a in chart.actions
                 if a["type"] == "down" and abs(a["time"] - tail_time) < 1e-6]
        self.assertEqual(len(downs), 2)  # 缺失单键与第 2 轨长条起点同拍
        self.assertEqual({a["pos"] for a in downs}, {(347.5, 590), (1082.5, 590)})
        self.assertEqual(len({a["finger"] for a in downs}), 2)
        self.assertNotIn(None, {a["finger"] for a in downs})

    def test_fever_slide_tails_are_not_flick(self):
        # 这几张谱面的 flick 滑条尾在游戏里是普通(fever)滑条尾;
        # 判定点数与游戏结算一致,只有 flick 标记被去掉。
        for key, expected_points in (("35-expert", 427), ("681-expert", 427),
                                     ("147-expert", 591), ("674-expert", 591),
                                     ("144-hard", 432), ("710-expert", 531),
                                     ("24-expert", 508), ("16-hard", 339)):
            with self.subTest(chart=key):
                song_id, difficulty = key.split("-")
                chart = BestdoriAPI.get_chart(song_id, difficulty)
                self.assertEqual(checkpoints(chart), expected_points)
                self.assertEqual(slide_tail_flicks(chart), 0)

    def test_other_songs_and_difficulties_keep_normal_loading(self):
        for song_id, difficulty in (("655", "expert"), ("654", "hard")):
            with self.subTest(song_id=song_id, difficulty=difficulty):
                with patch.object(BestdoriAPI, "_fetch_and_cache",
                                  return_value=[]) as fetch:
                    self.assertEqual(BestdoriAPI.get_chart(song_id, difficulty), [])
                fetch.assert_called_once_with(
                    f"https://bestdori.com/api/charts/{song_id}/{difficulty}.json",
                    f"{song_id}-{difficulty}",
                )


if __name__ == "__main__":
    unittest.main()

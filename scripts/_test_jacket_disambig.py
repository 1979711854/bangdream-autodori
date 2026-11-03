# -*- coding: utf-8 -*-
"""重名曲目「曲绘判别」离线回归(不需要模拟器/网络)。

背景(2026-10-31):オレンジ 在曲库里有 #316 / #676 两个同名条目,expert 都是
**25 级**,但谱面完全不同(651 / 683 判定、BPM 200 / 140)。`_pick_song_id`
原来「等级相同 = 同一份谱面」直接选默认项,而游戏里抽到的是另一首 → 整首对不上,
4.4 秒生命耗尽(战绩库 10-27 起 8 局全崩)。

修法:
  1) 等级全相同时先比谱面指纹(`_charts_identical`),真一样才认默认;
  2) 谱面不同就用选曲页右侧曲绘做灰度 NCC(`_match_jacket`),
     最高分 ≥0.30 且领先第二名 ≥0.15 才采用,否则沿用默认并告警。

本测试用合成曲绘 + 合成屏幕验证判别逻辑,不依赖真实资源(真实样本实测:
正确曲绘 NCC 0.51、其它候选 ≤0.09)。

用法:  .venv\\Scripts\\python.exe scripts/_test_jacket_disambig.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import sys
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, "src")

import autodori  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (label, ("  <- " + extra) if extra else ""))


def png_of(gray):
    ok, buf = cv2.imencode(".png", gray)
    assert ok
    return buf.tobytes()


def make_pattern(seed, kind):
    rng = np.random.RandomState(seed)
    img = np.full((128, 128), 60, np.uint8)
    if kind == "circle":
        cv2.circle(img, (64, 64), 40, 220, -1)
        cv2.rectangle(img, (10, 10), (40, 40), 160, -1)
    elif kind == "lines":
        for y in range(0, 128, 12):
            cv2.line(img, (0, y), (127, 127 - y), 230, 3)
    else:
        img = rng.randint(0, 255, (128, 128), dtype=np.uint8)
    return img


def make_screen(jacket_gray, dx=0, dy=0):
    """按真实布局把曲绘贴到选曲页右侧,其余区域填中灰。

    dx/dy 用来模拟实机那 8px 的偏移(见 autodori._JACKET_SEARCH_PAD 注释)。
    """
    screen = np.full((720, 1280, 3), 128, np.uint8)
    x, y, w, h = autodori._SONG_JACKET_ROI
    patch = cv2.resize(jacket_gray, (w, h))
    screen[y + dy:y + dy + h, x + dx:x + dx + w] = cv2.cvtColor(
        patch, cv2.COLOR_GRAY2BGR)
    return screen


def crop_jacket(screen):
    """按 _SONG_JACKET_ROI 裁出曲绘区(_jacket_ncc 的入参就是这个裁图)。"""
    x, y, w, h = autodori._SONG_JACKET_ROI
    return screen[y:y + h, x:x + w]


P1 = make_pattern(1, "circle")
P2 = make_pattern(2, "lines")
P3 = make_pattern(3, "random")
JACKETS = {"a": png_of(P1), "b": png_of(P2), "c": png_of(P3)}


def fake_jacket(sid):
    return JACKETS.get(sid)


def main():
    print("== NCC:同一张图相似度最高 ==")
    screen_a = make_screen(P1)
    hip = autodori._jacket_ncc(crop_jacket(screen_a), JACKETS["a"])
    low = autodori._jacket_ncc(crop_jacket(screen_a), JACKETS["b"])
    check("贴 a 时 a 的 NCC 很高", hip is not None and hip > 0.8, str(hip))
    check("贴 a 时 b 的 NCC 明显低", low is not None and low < 0.4, str(low))

    print("\n== _match_jacket:挑出正确候选 ==")
    with mock.patch.object(autodori.BestdoriAPI, "get_song_jacket",
                           side_effect=fake_jacket):
        hit = autodori._match_jacket(screen_a, ["b", "a"])
        check("返回 (a, best, second)", hit is not None and hit[0] == "a", str(hit))
        check("best ≥ 阈值 0.30", hit[1] >= autodori.JACKET_MIN_NCC, str(hit))
        check("领先 ≥ 0.15", hit[1] - hit[2] >= autodori.JACKET_MIN_MARGIN, str(hit))

        screen_b = make_screen(P2)
        hit = autodori._match_jacket(screen_b, ["a", "b"])
        check("换成贴 b → 选 b", hit is not None and hit[0] == "b", str(hit))

        check("取不到曲绘 → None(调用方走默认)",
              autodori._match_jacket(screen_a, ["a", "zzz"]) is None)
        check("候选只有 1 个 → None", autodori._match_jacket(screen_a, ["a"]) is None)
        check("空图 → None", autodori._match_jacket(None, ["a", "b"]) is None)

    print("\n== 曲绘位置偏移(实机低 8px,_JACKET_SEARCH_PAD 的作用) ==")
    # 用高频噪声图案:平移 8px 会让「精确裁图」的 NCC 崩掉,只有滑动搜索救得回
    # (实机上是 0.51 → 0.87,合成图案更极端,用来钉住「必须搜索」这条)。
    noise = make_pattern(4, "random")
    noise_png = png_of(noise)
    tight = make_screen(noise, dx=2, dy=8)
    exact = autodori._jacket_ncc(crop_jacket(tight), noise_png)
    check("精确裁图(不搜索)遇 8px 偏移分数崩掉",
          exact is not None and exact < 0.5, str(exact))
    with mock.patch.object(autodori.BestdoriAPI, "get_song_jacket",
                           side_effect=lambda sid: noise_png if sid == "a" else JACKETS["b"]):
        hit = autodori._match_jacket(tight, ["b", "a"])
        check("偏移 8px 滑动搜索仍选对 a", hit is not None and hit[0] == "a", str(hit))
        check("偏移后 best 仍过阈值", hit is not None and hit[1] >= autodori.JACKET_MIN_NCC,
              str(hit))
        hit = autodori._match_jacket(tight, ["a", "b"])
        check("候选顺序反着排同样选 a", hit is not None and hit[0] == "a", str(hit))

    print("\n== _charts_identical:谱面指纹 ==")
    chart = [{"type": "BPM", "bpm": 200, "beat": 0},
             {"type": "Single", "lane": 1, "beat": 4}]
    with mock.patch.object(autodori.BestdoriAPI, "get_chart", return_value=chart):
        check("同一份谱面 → True", autodori._charts_identical(["676", "316"], "expert"))
    other = [{"type": "BPM", "bpm": 140, "beat": 0},
             {"type": "Single", "lane": 6, "beat": 4}]

    def chart_of(sid, diff):
        return chart if sid == "676" else other

    with mock.patch.object(autodori.BestdoriAPI, "get_chart", side_effect=chart_of):
        check("谱面不同 → False", not autodori._charts_identical(["676", "316"], "expert"))

    def boom(sid, diff):
        raise RuntimeError("no chart")

    with mock.patch.object(autodori.BestdoriAPI, "get_chart", side_effect=boom):
        check("取不到谱面 → False(交给曲绘)", not autodori._charts_identical(["676", "316"], "expert"))

    print("\n== _pick_song_id:等级相同时的分支 ==")
    ids = ["676", "316"]
    with mock.patch.object(autodori, "_song_level", return_value=25), \
         mock.patch.object(autodori, "_charts_identical", return_value=True):
        check("谱面真一样 → 默认项", autodori._pick_song_id(None, None, "オレンジ", ids), "676")
    with mock.patch.object(autodori, "_song_level", return_value=25), \
         mock.patch.object(autodori, "_charts_identical", return_value=False), \
         mock.patch.object(autodori, "_match_jacket", return_value=("316", 0.52, 0.07)):
        check("谱面不同 + 曲绘命中 → 选 #316",
              autodori._pick_song_id(None, None, "オレンジ", ids), "316")
    with mock.patch.object(autodori, "_song_level", return_value=25), \
         mock.patch.object(autodori, "_charts_identical", return_value=False), \
         mock.patch.object(autodori, "_match_jacket", return_value=("316", 0.32, 0.30)):
        check("曲绘领先不足 → 沿用默认 #676",
              autodori._pick_song_id(None, None, "オレンジ", ids), "676")
    with mock.patch.object(autodori, "_song_level", return_value=25), \
         mock.patch.object(autodori, "_charts_identical", return_value=False), \
         mock.patch.object(autodori, "_match_jacket", return_value=None):
        check("曲绘不可用 → 沿用默认 #676",
              autodori._pick_song_id(None, None, "オレンジ", ids), "676")

    print("\n== 真实样本(有 debug/local 下的截图时) ==")
    from pathlib import Path
    shot = Path("debug/local/nav1.png")
    jacket = Path("debug/local/jacket_hashikimi.png")
    if shot.is_file() and jacket.is_file():
        screen = cv2.imread(str(shot))
        ref = jacket.read_bytes()
        exact = autodori._jacket_ncc(crop_jacket(screen), ref)
        # 硬裁(不搜索)实测 0.51:只防「实现被改坏」
        check("实机选曲页 × 正确曲绘:硬裁 NCC > 0.35",
              exact is not None and exact > 0.35, str(exact))
        # 实机走的是「外扩 + 滑动搜索」这条路,实测 0.87 —— 才是真正生效的路径
        pad = autodori._JACKET_SEARCH_PAD
        x, y, w, h = autodori._SONG_JACKET_ROI
        padded = screen[y - pad:y + h + pad, x - pad:x + w + pad]
        slid = autodori._jacket_ncc(padded, ref, search_pad=pad)
        check("实机选曲页 × 正确曲绘:滑动搜索 NCC > 0.75",
              slid is not None and slid > 0.75, str(slid))
    else:
        print("  SKIP  缺 debug/local/nav1.png 或 jacket_hashikimi.png")

    print("\n%d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

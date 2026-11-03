# -*- coding: utf-8 -*-
"""本地谱面覆盖(chart_overrides)的数据回归 —— 离线,不联网。

覆盖文件是「完整谱面」,一旦 Bestdori 侧数据更新就会遮住新数据,所以这里把
每张覆盖的**判定点数**钉住;改动覆盖后必须同步改这张表,否则 CI/本地回归会报。

2026-10-31 新增 #56/#763(熱色スターマイン / 平行歌曲):游戏里 12 条
「fever 方向フリック」(directional_fl_l/r_fever,横跨 2 轨)在 Bestdori 的 JSON
里整条缺失。证据:
  · 游戏结算恒定 perfect 643 + miss 12 = 655,而 Bestdori 谱面只有 643 判定;
  · 平行歌曲 #763 的游戏 BMS 里 directional_fl_l/r_fever 恰好 12 条;
  · 恒定 maxcombo 404 = beat 218 之前的判定点数,第一条漏的正是 beat 218。
所以补 12 条 Directional,判定点 643 → 655。

2026-11-03 新增 #20-expert(シュガーソングとビターステップ):用户实机逐帧观察
到恒定 miss=2 的两处断点 —— 第 538 个判定点(beat 163.5,lane 0)与第 602 个
判定点(beat 179.5,lane 5)都是「绿条结尾没松手」。Bestdori 在这两条 Slide 尾上
标了 `flick`,bot 就划一下再抬手,游戏要的是判定时刻松手 → 删掉两条尾巴的 flick
(与 #59-hard 同族)。判定点不变(811),只改手势。

用法:  .venv\\Scripts\\python.exe scripts/_test_chart_override.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from api import BestdoriAPI  # noqa: E402

PASS = 0
FAIL = 0
ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "assets" / "chart_overrides"


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (label, ("  <- " + extra) if extra else ""))


def judgments(chart):
    """判定点数。Directional 的 width 是横跨轨道数,**不是**判定点数(游戏实测:
    #413/#189 全 PERFECT 时 maxcombo 等于「Directional 各计 1」的总数)。"""
    n = 0
    for note in chart:
        t = note.get("type")
        if t in ("Single", "Directional"):
            n += 1
        elif t in ("Long", "Slide"):
            n += sum(1 for c in note["connections"] if not c.get("hidden"))
    return n


def judgments_before(chart, beat):
    n = 0
    for note in chart:
        t = note.get("type")
        if t in ("Single", "Directional"):
            if note["beat"] < beat:
                n += 1
        elif t in ("Long", "Slide"):
            n += sum(
                1 for c in note["connections"]
                if not c.get("hidden") and c["beat"] < beat
            )
    return n


# 每张覆盖的判定点数(与游戏结算对齐)。新增/修改覆盖时同步这里。
EXPECT = {
    "655-hard": 487,
    "35-expert": 427,
    "681-expert": 427,
    "147-expert": 591,
    "674-expert": 591,
    "144-hard": 432,
    "710-expert": 531,
    "24-expert": 508,
    "16-hard": 339,
    "56-special": 655,
    "763-special": 655,
    "59-hard": 384,
    "20-expert": 811,
    "59-expert": 544,
    "12-expert": 531,
}

# 被误标 flick 的 Slide 尾:文件 → {(beat, lane): 该尾巴是第几个判定点}。
# 判据同 #59-hard:用户实机看到「绿条结尾没松手」,说明游戏要的是判定时刻松手。
SLIDE_TAIL_FIXES = {
    "20-expert": {(163.5, 0): 538, (179.5, 5): 602},
    "59-expert": {(135, 5): 299, (139, 5): 306},
    "12-expert": {(158, 6): 356},
}

# 熱色スターマイン 缺失的 12 条 fever 方向 flick(beat → lane/direction)。
STARMINE_FEVER = {
    218: (1, "Left"), 221: (1, "Left"), 222: (1, "Right"),
    226: (5, "Right"), 229: (5, "Right"), 230: (5, "Left"),
    234: (1, "Left"), 237: (1, "Left"), 238: (1, "Right"),
    242: (5, "Right"), 245: (5, "Right"), 246: (5, "Left"),
}


def main():
    print("== 覆盖文件存在且格式完整 ==")
    for key in EXPECT:
        path = OVERRIDES / ("%s.json" % key)
        check("%s 存在" % key, path.is_file())
        if not path.is_file():
            continue
        chart = json.loads(path.read_text(encoding="utf-8"))
        check("%s 是列表且每条都有 type" % key,
              isinstance(chart, list) and all("type" in n for n in chart))

    print("\n== 判定点数(与游戏结算对齐) ==")
    for key, want in EXPECT.items():
        path = OVERRIDES / ("%s.json" % key)
        if not path.is_file():
            continue
        got = judgments(json.loads(path.read_text(encoding="utf-8")))
        check("%s 判定点 = %d" % (key, want), got == want, "实际 %d" % got)

    print("\n== 熱色スターマイン:12 条 fever 方向 flick ==")
    for key in ("56-special", "763-special"):
        path = OVERRIDES / ("%s.json" % key)
        if not path.is_file():
            continue
        chart = json.loads(path.read_text(encoding="utf-8"))
        got = {
            n["beat"]: (n["lane"], n["direction"])
            for n in chart
            if n.get("type") == "Directional" and n.get("width") == 2
        }
        # 这段区间本来就还有 3 条普通方向 flick(225/233/241),只核对补进来的 12 条。
        check("%s 补的 12 条位置/方向都对" % key,
              all(got.get(b) == v for b, v in STARMINE_FEVER.items()),
              str({b: got.get(b) for b in STARMINE_FEVER}))
        check("%s 第一条在第 405 个判定点(maxcombo 404)" % key,
              judgments_before(chart, 218) == 404,
              "实际 %d" % judgments_before(chart, 218))

    print("\n== 误标 flick 的 Slide 尾已改成普通尾巴 ==")
    for name, tails_want in SLIDE_TAIL_FIXES.items():
        path = OVERRIDES / ("%s.json" % name)
        if not path.is_file():
            continue
        chart = json.loads(path.read_text(encoding="utf-8"))
        tails = {}
        for note in chart:
            if note.get("type") != "Slide":
                continue
            last = note["connections"][-1]
            tails[(round(last["beat"], 3), last.get("lane"))] = last
        for key, idx in tails_want.items():
            conn = tails.get(key)
            check("%s beat %s lane %s 的尾巴不带 flick" % (name, key[0], key[1]),
                  conn is not None and not conn.get("flick"), str(conn))
            check("%s beat %s 是第 %d 个判定点" % (name, key[0], idx),
                  judgments_before(chart, key[0]) + 1 == idx,
                  "实际 %d" % (judgments_before(chart, key[0]) + 1))

    print("\n== get_chart 优先读覆盖(不联网) ==")
    for key, want in (("56-special", 655), ("763-special", 655),
                      ("20-expert", 811), ("59-expert", 544),
                      ("12-expert", 531)):
        chart = BestdoriAPI.get_chart(*key.split("-"))
        check("%s get_chart 读到覆盖版" % key, judgments(chart) == want)

    print("\n%d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

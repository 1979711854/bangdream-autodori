# -*- coding: utf-8 -*-
"""同名不同谱面 —— 用「首音轨道」定 id 的离线回归(2026-10-30)。

背景:
  曲库里有两首同名「シル・ヴ・プレジデント」:
    #389(anime,封面 389_sil_vious_pre)      hard 21 级,549 判定点,首音 lane 6 @3068ms
    #462(tie_up,封面 462_sil_vious_pre_ex)   hard 21 级,540 判定点,首音 lane 2 @2727ms
  两份**谱面不同**,但屏幕上「乐曲等级」都是 21 → `_pick_song_id` 的
  「等级相同 = 同一份谱面」判据在这里失效,取了默认的 #462,而游戏里选中的是
  #389 → 整首对不上、3 秒掉光生命(autodori-20261030-001741.log)。

  修法:首音触发时脚本已经知道变化落在哪几条分轨,拿它和候选谱面的首音轨道比,
  恰好命中一个才换。换谱面发生在下发任何音符之前,不影响时间线。

跑法:  .venv\\Scripts\\python.exe scripts/_test_songdisambig.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

import autodori  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-58s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-58s -> %s (want %s)" % (label, got, want))


def main():
    print("== 谱面首音轨道(数据事实,来自缓存谱面)")
    check("#389-hard 首音轨道", autodori._first_judgment_lanes("389", "hard"), {6})
    check("#462-hard 首音轨道", autodori._first_judgment_lanes("462", "hard"), {2})
    check("#389-hard 与 #462-hard 首音不同",
          autodori._first_judgment_lanes("389", "hard")
          != autodori._first_judgment_lanes("462", "hard"), True)

    print("== 用触发分轨定 id(±1 容差,恰好一个才认)")
    ids = ["389", "462"]
    # 实测触发落在分轨 5、6(001810-scan.txt),对应谱面 lane 6 → #389
    check("触发分轨 {5,6} → #389", autodori._pick_id_by_first_lane(ids, "hard", {5, 6}), "389")
    check("触发分轨 {6} → #389", autodori._pick_id_by_first_lane(ids, "hard", {6}), "389")
    check("触发分轨 {2} → #462", autodori._pick_id_by_first_lane(ids, "hard", {2}), "462")
    check("触发分轨 {0} 都不像 → 放弃(None)",
          autodori._pick_id_by_first_lane(ids, "hard", {0}), None)
    check("空分轨 → 放弃", autodori._pick_id_by_first_lane(ids, "hard", ()), None)
    check("单候选直接返回", autodori._pick_id_by_first_lane(["462"], "hard", {6}), None)

    print("== 同名但谱面相同的组:不会误换")
    # 取一组 5 档等级全同的同名条目(见 autodori 里 ambiguous_titles 的注释)
    same = None
    for title, cand in autodori.ambiguous_titles.items():
        if len(cand) < 2:
            continue
        levels = {autodori._song_level(i, "hard") for i in cand}
        firsts = {frozenset(autodori._first_judgment_lanes(i, "hard")) for i in cand}
        if len(levels) == 1 and len(firsts) == 1:
            same = (title, cand)
            break
    if same is None:
        check("找到一组等级/首音都相同的同名条目", False, True)
    else:
        title, cand = same
        hits = set(sorted(autodori._first_judgment_lanes(cand[0], "hard"))[:1])
        hits = {next(iter(hits))} if hits else set()
        check("同谱面组 %s 分不开 → 放弃" % title[:16],
              autodori._pick_id_by_first_lane(cand, "hard", hits), None)

    print("== 换谱面必须与正常选歌走同一条建表路径(2026-10-30 踩过)")
    # 第一版只调了 notes_to_actions、漏了 actions_to_MNTcmd →
    # `'Chart' object has no attribute 'command_builder'`,打歌一开局就失败。
    import inspect
    switch_src = inspect.getsource(autodori._maybe_switch_chart_by_first_note)
    check("换谱面调用 _load_chart", "_load_chart(" in switch_src, True)
    check("换谱面建出 command_builder(调 actions_to_MNTcmd)",
          "actions_to_MNTcmd(" in switch_src, True)
    check("save_song 也走 _load_chart",
          "_load_chart(" in inspect.getsource(autodori.save_song), True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

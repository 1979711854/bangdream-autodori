# -*- coding: utf-8 -*-
"""绿条(滑条/长条)结尾的松手检查 —— 离线,只扫 cache 里已缓存的谱面。

背景(2026-11-03):用户实机观察到的固定 miss 全是「绿条结尾没松手」。这一条
逻辑上必须成立:每个 Long/Slide 的最后一个 connection 都要有
  ① 普通尾巴:判定时刻的 up(松手);
  ② flick 尾:判定时刻的 move(划)+ 判定后 80ms 的 up。
两者都没有 = 这个音符必然 MISS,不需要实机验证。本脚本把这条不变量对全部
已缓存谱面跑一遍,并按曲目统计:
  · 没抢到手指(finger=None,指令被丢弃)        → 必然 miss
  · 有尾巴但没有 up                            → 必然 miss
  · up 时刻与判定时刻不符(>1ms)               → 大概率 miss
  · flick 尾(up 在判定后 80ms)                 → 语法正确,但游戏真需要 flick 才对;
                                                这是「疑似误标」的候选池

用法:
  .venv\\Scripts\\python.exe scripts/_diag_slide_tails.py            # 全量
  .venv\\Scripts\\python.exe scripts/_diag_slide_tails.py 59 20 12   # 只查指定曲目
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import logging
import re
import sys

sys.path.insert(0, "src")

import chart as chart_mod  # noqa: E402
from api import BestdoriAPI  # noqa: E402

RESOLUTION = (1280, 720)
MOVE_SLICE = 8
CHART_KEY = re.compile(r"^\d+-(easy|normal|hard|expert|special)$")


def scan_chart(song_id, difficulty):
    """返回 (统计 dict, 问题列表)。"""
    c = chart_mod.Chart((song_id, difficulty), song_id)
    c.notes_to_actions(RESOLUTION, MOVE_SLICE)
    # 说明:方向只影响 flick 往哪划,不影响「有没有 up」这条不变量,
    # 所以 get_flick_directions 在 main() 里被换成「只读缓存」,避免全量扫谱
    # 时对上千张谱逐个发网络请求。


    by_note = {}
    for a in c.actions:
        if a.get("type") in ("down", "move", "up"):
            by_note.setdefault(a.get("note"), []).append(a)

    stat = dict(tails=0, release=0, flick=0, no_finger=0, no_up=0, bad_time=0,
                flick_no_move=0)
    problems = []
    for note in c._chart_data:
        if note.get("type") not in ("Long", "Slide"):
            continue
        stat["tails"] += 1
        tail = note["connections"][-1]
        tail_t = tail["time"]
        is_flick = bool(tail.get("flick"))
        acts = by_note.get(note.get("index"), [])
        finger = next((a.get("finger") for a in acts if a["type"] == "down"), "无down")
        ups = [a["time"] for a in acts if a["type"] == "up"]
        moves = [a["time"] for a in acts if a["type"] == "move"]
        if finger is None:
            stat["no_finger"] += 1
            problems.append((note.get("index"), tail_t, "没抢到手指"))
            continue
        if not ups:
            stat["no_up"] += 1
            problems.append((note.get("index"), tail_t, "没有 up"))
            continue
        expect = tail_t + 80 if is_flick else tail_t
        if not any(abs(t - expect) < 1.0 for t in ups):
            stat["bad_time"] += 1
            problems.append((note.get("index"), tail_t,
                             "up 时刻 %.1f,期望 %.1f" % (min(ups), expect)))
            continue
        if is_flick:
            stat["flick"] += 1
            if not any(abs(t - tail_t) < 1.0 for t in moves):
                stat["flick_no_move"] += 1
                problems.append((note.get("index"), tail_t, "标了 flick 但没有划动"))
        else:
            stat["release"] += 1
    return stat, problems


def main():
    logging.disable(logging.WARNING)  # 谱面解析会刷 Unknown type: System

    # 方向解析只影响 flick 往哪边划,不影响「有没有 up」这条不变量。
    # 全量扫上千张谱时若每张都发一次网络请求会非常慢 → 这里只读缓存。
    _cache = BestdoriAPI._cache

    def _cached_dirs(song_id, difficulty):
        v = _cache.get("flickdir-%s-%s" % (song_id, difficulty))
        if isinstance(v, dict) and v.get("available"):
            return v.get("dirs")
        return None

    BestdoriAPI.get_flick_directions = staticmethod(_cached_dirs)

    only = set(sys.argv[1:])
    keys = [str(k) for k in BestdoriAPI._cache.iterkeys() if CHART_KEY.match(str(k))]
    if only:
        keys = [k for k in keys if k.split("-")[0] in only]
    keys.sort(key=lambda k: (int(k.split("-")[0]), k))

    total = dict(tails=0, release=0, flick=0, no_finger=0, no_up=0, bad_time=0,
                 flick_no_move=0)
    flick_songs = []
    bad_songs = []
    done = 0
    for key in keys:
        song_id, difficulty = key.split("-")
        try:
            stat, problems = scan_chart(song_id, difficulty)
        except Exception as e:  # noqa: BLE001
            print("  SKIP %s: %s" % (key, e))
            continue
        done += 1
        for k in total:
            total[k] += stat[k]
        if stat["flick"]:
            flick_songs.append((key, stat["flick"], stat["tails"]))
        if stat["no_finger"] or stat["no_up"] or stat["bad_time"] or stat["flick_no_move"]:
            bad_songs.append((key, stat, problems))
        if done % 200 == 0:
            print("  ... 已扫 %d/%d" % (done, len(keys)))

    print("\n== 全量统计(已扫 %d 张谱) ==" % done)
    print("  绿条尾 %d 条:按时松手 %d,标 flick %d,没抢到手指 %d,没有 up %d,"
          "up 时刻不符 %d,flick 但没有划动 %d"
          % (total["tails"], total["release"], total["flick"], total["no_finger"],
             total["no_up"], total["bad_time"], total["flick_no_move"]))

    print("\n== 结构性问题(必然 miss / 大概率 miss) ==")
    if not bad_songs:
        print("  无")
    for key, stat, problems in bad_songs:
        print("  #%s  尾巴 %d | 没手指 %d 没up %d 时刻不符 %d 缺划动 %d"
              % (key, stat["tails"], stat["no_finger"], stat["no_up"],
                 stat["bad_time"], stat["flick_no_move"]))
        for idx, t, why in problems[:5]:
            print("      note#%s t=%.0fms %s" % (idx, t, why))
        if len(problems) > 5:
            print("      ... 共 %d 处" % len(problems))

    print("\n== 带 flick 尾的谱面(%d 张,疑似误标候选池) ==" % len(flick_songs))
    flick_songs.sort(key=lambda x: -x[1])
    for key, n_flick, n_tail in flick_songs:
        print("  #%-18s flick 尾 %3d / 尾巴 %3d" % (key, n_flick, n_tail))


if __name__ == "__main__":
    main()

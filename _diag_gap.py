"""离线分析: 同一手指「抬起后立即按下」的间隔分布。

假设: chart.Chart.get_finger() 复用手指的条件是区间不重叠
(from_time >= occupied_to), 因此允许「上一个音符刚抬起、下一个音符立刻按下」
(gap = 0ms)。若这两个事件落在 minitouch 的同一个 commit 里, 游戏侧可能把它
当作"连续按压"而非两次独立点击, 后一个音符就完全不判定 -> 表现为零星 miss
且 fast/slow = 0。

本脚本只统计现象, 不修改任何逻辑。

用法:
    .venv/Scripts/python.exe _diag_gap.py 147 expert 180 hard
"""

import os
import sys
import logging

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
logging.disable(logging.WARNING)

from chart import Chart  # noqa: E402

RESOLUTION = (1280, 720)
MOVE_SLICE = 10


def analyze(song_id, difficulty):
    try:
        chart = Chart((song_id, difficulty))
    except Exception as exc:
        print("  [取谱失败] %s-%s: %s" % (song_id, difficulty, exc))
        return
    chart.notes_to_actions(RESOLUTION, MOVE_SLICE)
    actions = chart.actions

    # 按手指收集 down / up 时刻
    per_finger = {}
    for a in actions:
        if a.get("finger") is None:
            continue
        t = a.get("type")
        if t not in ("down", "up"):
            continue
        per_finger.setdefault(a["finger"], {"down": [], "up": []})[t].append(a["time"])

    print("  %s-%s  (#%s-%s)" % (song_id, difficulty, song_id, difficulty))
    total_gap0 = 0
    total_tight = 0
    for fid in sorted(per_finger):
        downs = sorted(per_finger[fid]["down"])
        ups = sorted(per_finger[fid]["up"])
        if len(downs) != len(ups):
            print("    finger %d: down=%d up=%d (不配对!)" % (fid, len(downs), len(ups)))
            continue
        gaps = []
        for i in range(len(downs) - 1):
            gaps.append(downs[i + 1] - ups[i])
        g0 = [g for g in gaps if abs(g) < 0.001]
        tight = [g for g in gaps if abs(g) < 20]
        total_gap0 += len(g0)
        total_tight += len(tight)
        print("    finger %d: 音符 %d 个, gap=0 的有 %d, gap<20ms 的有 %d"
              % (fid, len(downs), len(g0), len(tight)))
        if g0:
            print("        gap=0 时刻: %s" % (["%.1f" % v for v in
                  [downs[i + 1] for i in range(len(downs) - 1) if abs(gaps[i]) < 0.001][:12]],))
    print("    >> 合计: gap=0 %d 处, gap<20ms %d 处" % (total_gap0, total_tight))
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = ["147", "expert"]
    pairs = [(args[i], args[i + 1]) for i in range(0, len(args) - 1, 2)]
    # 已知 miss 数, 便于对照
    known = {"147-expert": 2, "180-hard": 2, "386-hard": 3, "664-hard": None,
             "481-expert": 1, "756-expert": 1, "339-expert": 2}
    print("同一手指「抬起后立即按下」间隔分析")
    print("(对照: 147-expert 实测每次都漏 2 个)\n")
    for sid, diff in pairs:
        analyze(sid, diff)

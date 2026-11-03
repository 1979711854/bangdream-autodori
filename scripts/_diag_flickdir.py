# -*- coding: utf-8 -*-
"""按曲方向覆盖(#12/#20 那类老曲)的排查工具 —— 本地脚本,不联网(用缓存)。

用途:老曲 BMS 在 Bestdori 上取不到,「零位移长条 + 方向 flick」的方向反推
不出来,只能靠实机 combo 反推一条条补。流程:

  1. 打一遍目标曲,记下结算的 maxcombo / miss;
  2. 跑本脚本 `--maxcombo N`,它会显示第 N+1 个判定点是哪个音符
     (第一个 miss 通常就在那里,同拍多音会有并列);
  3. 如果那个点是 flick 尾且方向未知,把对应条目写进
     `assets/chart_overrides/<曲id>-<难度>.flickdir.json`:
       {"<beat>,<lane>": "L"}       # 或 "R"
  4. 重打,重复,直到 miss 归零或只剩「本来就是普通 flick」的那些。

用法:
  python scripts/_diag_flickdir.py 12 expert
  python scripts/_diag_flickdir.py 12 expert --maxcombo 351
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import argparse
import sys

sys.path.insert(0, "src")

import chart as chart_mod  # noqa: E402
from api import BestdoriAPI  # noqa: E402


def load_judgments(chart):
    """按时间排序的判定点列表:(beat, type, note, connection)。"""
    pts = []
    for n in chart:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t in ("Single", "Directional"):
            pts.append((n["beat"], t, n, None))
        elif t in ("Long", "Slide"):
            for c in n["connections"]:
                if not c.get("hidden"):
                    pts.append((c["beat"], t, n, c))
    pts.sort(key=lambda x: x[0])
    return pts


def tail_key(note, connection):
    """该 connection 是长条/滑条尾巴时返回 (beat, lane),否则 None。"""
    if not isinstance(note, dict) or note.get("type") not in ("Long", "Slide"):
        return None
    if connection is not note["connections"][-1] or not connection.get("flick"):
        return None
    return (round(connection["beat"], 3), connection["lane"])


def resolve(note, connection, bms, overrides):
    """(方向, 来源),与 chart.notes_to_actions 的口径一致。"""
    lane = connection.get("lane", 0)
    width = connection.get("width") or 1
    beat = round(connection["beat"], 3)
    for l in range(lane, lane + width):
        if (beat, l) in overrides:
            d = overrides[(beat, l)]
            return (None if d == "U" else d), "按曲覆盖"
    if bms is not None:
        for l in range(lane, lane + width):
            if (beat, l) in bms:
                return bms[(beat, l)], "BMS"
        return None, "普通 flick"
    # 2026-11-02(issue #14):与 chart.notes_to_actions 对齐 —— BMS 不可用时
    # 不再用轨迹反推决定手势,一律上划;这里只把反推结果当「补覆盖的参考」。
    guess = chart_mod.infer_flick_direction(note["connections"])
    if guess:
        return None, "判不出(按上划;反推建议 %s)" % guess
    return None, "判不出(按上划)"


def main():
    ap = argparse.ArgumentParser(description="按曲方向覆盖排查")
    ap.add_argument("song", help="曲目 id,如 12")
    ap.add_argument("difficulty", help="easy/normal/hard/expert/special")
    ap.add_argument("--maxcombo", type=int, default=None,
                    help="结算 maxcombo;脚本会显示第 N+1 个判定点(第一个 miss)")
    args = ap.parse_args()

    chart = BestdoriAPI.get_chart(args.song, args.difficulty)
    bms = BestdoriAPI.get_flick_directions(args.song, args.difficulty)
    overrides = BestdoriAPI.get_flick_direction_overrides(args.song, args.difficulty)
    pts = load_judgments(chart)

    flick_idx = {}
    for i, (beat, t, note, conn) in enumerate(pts, 1):
        key = tail_key(note, conn)
        if key:
            flick_idx[key] = i

    print("曲目 %s-%s:判定点 %d,BMS 方向表 %s,按曲覆盖 %d 条"
          % (args.song, args.difficulty, len(pts),
             "不可用" if bms is None else "%d 条" % len(bms), len(overrides)))
    print("flick 尾(%d 条):" % len(flick_idx))
    for (beat, lane), idx in sorted(flick_idx.items(), key=lambda kv: kv[1]):
        # 找到对应 connection 以取 head/type
        note = conn = None
        for b, t, n, c in pts:
            if tail_key(n, c) == (beat, lane):
                note, conn = n, c
                break
        direction, source = resolve(note, conn, bms, overrides)
        head = note["connections"][0]["lane"]
        print("  #%-4d beat=%-8s %-5s head=%s tail=%s  → %s(%s)"
              % (idx, beat, note["type"], head, lane,
                 direction or "上划", source))

    if args.maxcombo is not None:
        j = args.maxcombo  # 0-based:第 N+1 个判定点
        print("\n结算 maxcombo=%d → 第一个 miss 是第 %d 个判定点:" % (args.maxcombo, j + 1))
        if 0 <= j < len(pts):
            beat, t, n, c = pts[j]
            print("  beat=%s type=%s" % (beat, t), end="")
            if t in ("Single", "Directional"):
                print(" lane=%s width=%s flick=%s" % (
                    n.get("lane"), n.get("width"), n.get("flick")))
            else:
                print(" connections=%s" % (
                    [(cc["lane"], cc["beat"], "H" if cc.get("hidden") else "")
                     for cc in n["connections"]],))
            key = tail_key(n, c)
            if key and key in flick_idx:
                d, src = resolve(n, c, bms, overrides)
                print("  → 这一条是 flick 尾(当前 %s,来源 %s);覆盖写法: {\"%s,%s\": \"L\"}"
                      % (d or "上划", src, key[0], key[1]))
        else:
            print("  越界(判定点只有 %d 个)" % len(pts))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""方向 flick(方向フリック)支持 —— 离线回归(不需要网络/模拟器)。

背景(2026-10-31):#413 DRIVE US CRAZY / #189 えがお･シング･あ･ソング /
#344 Sing Alive 每次都曲中段「演出失败」,下发链干净。根因是这些谱面的滑条尾
是「方向フリック」,游戏要求左右划,而 chart.py 一律向上划 300px → 每条都
MISS。方向只能从游戏 BMS 读(api.get_flick_directions)。

这里锁定四件事:
  1. BMS 目录号 = ceil(曲id/10)*10;
  2. BMS 解析只认 `*_dir_flick_*` 对象,并给出正确的 L/R;
  3. 轨迹反推规则(与三首游戏 BMS 对账过的样例);
  4. 解算出来的动作:方向 L/R 就水平划、普通 flick 仍上划、反推失败也上划。

用法:  .venv\\Scripts\\python.exe scripts/_test_flickdir.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

from unittest import mock

import chart as chart_mod  # noqa: E402
from api import BestdoriAPI  # noqa: E402

PASS = 0
FAIL = 0

RESOLUTION = (1280, 720)
MOVE_SLICE = 10
LANE_W = 147
LANE_X = lambda lane: 127 + (lane + 0.5) * LANE_W  # noqa: E731
TAIL_TIME = 12 * 1000.0 / 3  # BPM 180:12 beat = 4000ms


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (label, ("  <- " + extra) if extra else ""))


# ---------------------------------------------------------------- 1. 目录号
print("[1] musicscore 目录号")
for song_id, want in [("413", 420), ("344", 350), ("320", 320),
                      ("109", 110), ("1", 10), ("700", 700), ("701", 710)]:
    got = BestdoriAPI._bms_dir_of(song_id)
    check("#%s → %d" % (song_id, want), got == want, "got %s" % got)


# ---------------------------------------------------------------- 2. BMS 解析
print("\n[2] BMS 解析")
BMS = "\n".join([
    "#WAV01 slide_end_dir_flick_l_a.wav",
    "#WAV02 slide_end_dir_flick_r_b.wav",
    "#WAV03 long_end_dir_flick_l.wav",
    "#WAV04 long_end_dir_flick_r.wav",
    "#WAV05 slide_end_flick_a.wav",
    "#WAV06 slide_end_a.wav",
    "#WAV07 bd.wav",
    # 小节 001、1x 通道 12(=lane2)、4 格:第 0 格 L,第 2 格普通 flick
    "#00112:01000500",
    # 5x 通道 56(=lane0):第 1 格 long_end_dir_flick_r
    "#00156:00040000",
    # 小节 002、通道 16(=lane0)、2 格:第 1 格 long_end_dir_flick_l
    "#00216:0003",
    "#00301:07",  # BGM 通道,忽略
])
dirs = BestdoriAPI._parse_bms_flick_dirs(BMS)
check("L 滑条尾 → (beat 4, lane2)", dirs.get((4.0, 2)) == "L", str(dirs))
check("右长条尾 → (beat 5, lane0)", dirs.get((5.0, 0)) == "R", str(dirs))
check("左长条尾 → (beat 10, lane0)", dirs.get((10.0, 0)) == "L", str(dirs))
check("普通 flick / 普通尾不入表", len(dirs) == 3, str(dirs))


# ---------------------------------------------------------------- 3. 轨迹反推
print("\n[3] 轨迹反推(样例取自三首游戏 BMS 对账)")


def conn(*lanes_beat):
    return [{"lane": lanes_beat[i], "beat": lanes_beat[i + 1]}
            for i in range(0, len(lanes_beat), 2)]


cases = [
    # 头尾不同轨:方向与位移相反
    ("#413 beat161 head1→tail3 真值 L", conn(1, 0, 3, 2), "L"),
    ("#413 beat162 head5→tail3 真值 R", conn(5, 0, 3, 2), "R"),
    ("#413 beat222 head2→tail0 真值 R", conn(2, 0, 0, 2), "R"),
    # 头尾同轨:朝最远中段点
    ("#413 beat12 head2 mid1 tail2 真值 L", conn(2, 0, 1, 1, 2, 2), "L"),
    ("#413 beat16 head4 mid5 tail4 真值 R", conn(4, 0, 5, 1, 4, 2), "R"),
    ("#413 beat384 head3 mids[3,0] tail3 真值 L",
     conn(3, 0, 3, 1, 0, 2, 3, 3), "L"),
    # 零位移、无中段:猜不出
    ("零位移长条 → None", conn(5, 0, 5, 4), None),
]
for label, c, want in cases:
    got = chart_mod.infer_flick_direction(c)
    check(label, got == want, "got %s" % got)


# ---------------------------------------------------------------- 4. 动作生成
print("\n[4] 动作生成")


def build_with_dirs(chart_notes, dirs_value, overrides=None):
    with mock.patch.object(
        chart_mod.BestdoriAPI, "get_chart", return_value=chart_notes
    ), mock.patch.object(
        chart_mod.BestdoriAPI, "get_flick_directions", return_value=dirs_value
    ), mock.patch.object(
        chart_mod.BestdoriAPI, "get_flick_direction_overrides",
        return_value=dict(overrides or {}),
    ):
        c = chart_mod.Chart(("413", "special"), "synthetic")
        c.notes_to_actions(RESOLUTION, MOVE_SLICE)
    return c


def tail_moves(c):
    """滑条尾到 flick 结束之间的 move(时间 >= 尾判定时刻)。"""
    return [a for a in c.actions if a["type"] == "move" and a["time"] >= TAIL_TIME]


BPM = {"type": "BPM", "bpm": 180, "beat": 0}
SLIDE = {"type": "Slide", "connections": [
    {"lane": 2, "beat": 8},
    {"lane": 1, "beat": 10, "hidden": True},
    {"lane": 2, "beat": 12, "flick": True},
]}
LONG = {"type": "Long", "connections": [
    {"lane": 5, "beat": 8},
    {"lane": 5, "beat": 12, "flick": True},
]}
SINGLE_FLICK = {"type": "Single", "lane": 3, "beat": 12, "flick": True}

c = build_with_dirs([BPM, SLIDE], {(12.0, 2): "L"})
mv = tail_moves(c)
check("BMS=L → 水平向左划", mv and mv[-1]["to"][0] < LANE_X(2),
      str(mv[-1]["to"] if mv else None))
check("BMS=L → 不改变 y", mv and abs(mv[-1]["to"][1] - 590) < 0.5,
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], {(12.0, 2): "R"})
mv = tail_moves(c)
check("BMS=R → 水平向右划", mv and mv[-1]["to"][0] > LANE_X(2),
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], {})
mv = tail_moves(c)
check("BMS 有但非方向 flick → 仍向上划", mv and mv[-1]["to"][1] < 590,
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], None)
mv = tail_moves(c)
check("BMS 不可用 → 按轨迹反推(此样例 L)", mv and mv[-1]["to"][0] < LANE_X(2),
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, LONG], None)
mv = tail_moves(c)
check("BMS 不可用 + 零位移长条 → 猜不出,向上划",
      mv and mv[-1]["to"][1] < 590 and abs(mv[-1]["to"][0] - LANE_X(5)) < 0.5,
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SINGLE_FLICK], {(12.0, 3): "L"})
flick = [a for a in c.actions if a["type"] == "move" and a["time"] >= TAIL_TIME]
check("单键 flick 不受方向表影响,仍向上划",
      flick and flick[-1]["to"][1] < 590, str(flick[-1]["to"] if flick else None))

# 靠边夹紧:lane0 向左划不能出屏
SLIDE_EDGE = {"type": "Slide", "connections": [
    {"lane": 0, "beat": 8},
    {"lane": 0, "beat": 12, "flick": True},
]}
c = build_with_dirs([BPM, SLIDE_EDGE], {(12.0, 0): "L"})
mv = tail_moves(c)
check("最左轨向左划 → 夹在屏幕内(x>=0)",
      mv and 0 <= mv[-1]["to"][0] < LANE_X(0), str(mv[-1]["to"] if mv else None))


print("\n[5] 按曲方向覆盖(2026-10-31,老曲用)")
ov = BestdoriAPI._parse_flick_direction_overrides(
    {"12.0,2": "L", "30,1": "r", "13.5,3": "U", "bad": "L", "64,4": "?"}
)
check("解析正常键", ov.get((12.0, 2)) == "L" and ov.get((30.0, 1)) == "R", str(ov))
check("U 原样保留", ov.get((13.5, 3)) == "U", str(ov))
check("坏键跳过(只剩 4 条)", len(ov) == 4 and (64.0, 4) in ov, str(ov))

c = build_with_dirs([BPM, SLIDE], {(12.0, 2): "R"}, overrides={(12.0, 2): "L"})
mv = tail_moves(c)
check("覆盖优先于 BMS(R→L)", mv and mv[-1]["to"][0] < LANE_X(2),
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], None, overrides={(12.0, 2): "R"})
mv = tail_moves(c)
check("BMS 不可用时覆盖优先于轨迹推测(L→R)", mv and mv[-1]["to"][0] > LANE_X(2),
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], {(12.0, 2): "R"}, overrides={})
mv = tail_moves(c)
check("没有覆盖时仍用 BMS", mv and mv[-1]["to"][0] > LANE_X(2),
      str(mv[-1]["to"] if mv else None))

c = build_with_dirs([BPM, SLIDE], None, overrides={(12.0, 2): "U"})
mv = tail_moves(c)
check("覆盖 U → 强制上划(即使轨迹能推测)",
      mv and mv[-1]["to"][1] < 590 and abs(mv[-1]["to"][0] - LANE_X(2)) < 0.5,
      str(mv[-1]["to"] if mv else None))


print("\n%d PASS / %d FAIL" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)

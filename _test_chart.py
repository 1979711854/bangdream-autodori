# -*- coding: utf-8 -*-
"""谱面解算 —— 离线回归(不需要模拟器,不需要游戏,不需要网络)。

锁定 2026-10-01 实机暴露的一个真实缺陷:

    打 #786「ときめきエクスペリエンス！」时,脚本加载完曲目就再无动静 ——
    **连「演出开始」都不点**。原因是 Save song -> notes_to_actions ->
    add_smooth_move 里的 `(to_x - from_x) / duration` 抛 ZeroDivisionError,
    而它跑在 post_task **之前**,MAA 任务根本没启动;又因为 autodori.exe 的
    stderr 不被 GUI 捕获,debug/*.log 里连 traceback 都看不到。

    触发条件是谱面用「相邻两个 connection 同 beat」表达一次划过两个 lane
    (#786 有 100 处,如 note#984 的 lane 1→0 @beat280)。其余三首活动曲
    (596/487/486)零长度段为 0,所以此前一直没暴露。

这里用**合成谱面**复现,不碰 Bestdori 缓存与网络 —— 真实谱面会随曲库更新而变,
而「同拍成对 connection 不得中断解算」是应当长期成立的不变量。

用法:  .venv\\Scripts\\python.exe _test_chart.py
"""

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

import chart as chart_mod  # noqa: E402
from chart import Chart  # noqa: E402

PASS = 0
FAIL = 0

RESOLUTION = (1280, 720)
MOVE_SLICE = 10  # 与 src/autodori.py 的 DEFAULT_MOVE_SLICE_SIZE 一致

BPM_NOTE = {"type": "BPM", "bpm": 180, "beat": 0}


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (label, ("  <- " + extra) if extra else ""))


def build(notes):
    """用合成谱面构造 Chart 并解算,返回 Chart 实例;异常原样抛出给调用方断言。"""
    with mock.patch.object(chart_mod.BestdoriAPI, "get_chart", return_value=notes):
        chart = chart_mod.Chart(("0", "special"), "synthetic")
    chart.notes_to_actions(RESOLUTION, MOVE_SLICE)
    return chart


def moves(chart):
    return [a for a in chart.actions if a["type"] == "move"]


def test_zero_length_segment():
    """同拍成对 connection(=零长度段)不得中断解算,也不得生成 move。"""
    print("\n[1] 零长度段(Slide 相邻 connection 同 beat)")

    # 只有一段,且同 beat:唯一的插值段退化为零长度
    chart = build([BPM_NOTE, {
        "type": "Slide",
        "connections": [{"lane": 1, "beat": 2}, {"lane": 0, "beat": 2}],
    }])
    check("不抛 ZeroDivisionError,解算完成", chart.actions is not None)
    check("零长度段不产生 move 动作", len(moves(chart)) == 0,
          "实际 %d 个 move" % len(moves(chart)))
    check("仍保留起止的 down/up",
          any(a["type"] == "down" for a in chart.actions)
          and any(a["type"] == "up" for a in chart.actions))
    check("手指编号不为 None(未丢指)",
          all(a.get("finger") is not None
              for a in chart.actions if a["type"] in ("down", "up")))


def test_normal_slide_not_broken():
    """修复不得误伤正常的非零长度滑条。"""
    print("\n[2] 正常滑条(相邻 connection 不同 beat)")

    chart = build([BPM_NOTE, {
        "type": "Slide",
        "connections": [{"lane": 1, "beat": 2}, {"lane": 5, "beat": 3}],
    }])
    check("正常滑条仍产生 move 动作", len(moves(chart)) > 0,
          "实际 %d 个 move" % len(moves(chart)))
    check("move 均带 to 坐标",
          all("to" in a and a["to"] is not None for a in moves(chart)))


def test_degenerate_segment_contributes_nothing():
    """在滑条中间插入零长度段,不应改变最终生成的 move 数量。"""
    print("\n[3] 混合:零长度段 + 正常段")

    mixed = build([BPM_NOTE, {
        "type": "Slide",
        "connections": [
            {"lane": 1, "beat": 2},
            {"lane": 0, "beat": 2},   # 同 beat -> 零长度(应被跳过)
            {"lane": 5, "beat": 3},   # 正常段
        ],
    }])
    plain = build([BPM_NOTE, {
        "type": "Slide",
        "connections": [
            {"lane": 1, "beat": 2},
            {"lane": 5, "beat": 3},   # 与 mixed 的那一段等价
        ],
    }])
    check("含零长度段时仍能解算", len(mixed.actions) > 0)
    check("零长度段不额外贡献 move(与去掉它的等价谱面一致)",
          len(moves(mixed)) == len(moves(plain)),
          "mixed=%d plain=%d" % (len(moves(mixed)), len(moves(plain))))


def test_real_song_pattern():
    """#786 的真实写法:同拍成对 + hidden 检查点。"""
    print("\n[4] #786 同拍成对写法(note#984 / note#998 的形状)")

    chart = build([BPM_NOTE, {
        "type": "Slide",
        "connections": [
            {"lane": 1, "beat": 280},
            {"lane": 0, "beat": 280},
            {"lane": 5, "beat": 281},
            {"lane": 6, "beat": 281, "hidden": True},
            {"lane": 1, "beat": 282},
        ],
    }])
    check("不抛异常,解算完成", len(chart.actions) > 0)
    check("保留非零长度段的 move", len(moves(chart)) > 0,
          "实际 %d 个 move" % len(moves(chart)))
    check("action 时间单调不减(排序未被破坏)",
          all(chart.actions[i]["time"] <= chart.actions[i + 1]["time"]
              for i in range(len(chart.actions) - 1)))


def test_long_and_flick_untouched():
    """Long 与 flick(走 80ms 固定时长)路径不受影响。"""
    print("\n[5] Long / flick 路径")

    # note 索引由 _process_time_chart 按 Single/Directional/Long/Slide 顺序发放:
    # flick Single -> 1,Long -> 2
    chart = build([BPM_NOTE,
                   {"type": "Single", "lane": 3, "beat": 1},
                   {"type": "Single", "lane": 3, "beat": 2, "flick": True},
                   {"type": "Long", "connections": [
                       {"lane": 2, "beat": 3}, {"lane": 4, "beat": 5}]}])
    check("Single(flick) 产生 move", any(
        a["type"] == "move" and a.get("note") == 1 for a in chart.actions))
    check("跨 lane 的 Long 产生 move", any(
        a["type"] == "move" and a.get("note") == 2 for a in chart.actions))
    check("Long 有起止 down/up", any(
        a["type"] == "down" and a.get("note") == 2 for a in chart.actions))

    # 直握(首尾同 lane)按既有设计不产生中间 move —— 这里只是钉住这个语义,
    # 免得以后误以为它也走零长度分支。
    straight = build([BPM_NOTE, {"type": "Long", "connections": [
        {"lane": 2, "beat": 3}, {"lane": 2, "beat": 5}]}])
    check("直握 Long 不产生 move(既有设计)",
          len(moves(straight)) == 0,
          "实际 %d 个 move" % len(moves(straight)))


def test_no_illegal_minitouch_command():
    """手指不够时**不得**生成 `d None ...` / `u None` 这类非法 minitouch 指令。

    2026-10-09 实机复现:打 #786(ときめきエクスペリエンス！)三次都没打出结算,
    db 里零记录。根因之一是 5 指分配对该曲不够(1097 音符/104s,8.6% 的音符
    抢不到手),而 notes_to_actions 把这些音符的 finger=None 原样带进
    actions_to_MNTcmd,最终格式化出 `d None 200 590 1` / `u None` ——
    minitouch 收到非法 contact 会解析失败,把那批命令的语义搅乱。

    这里用**同一时刻 12 个以上音符**的合成谱面强制触发「手指不够」,
    断言最终下发给minitouch 的指令串里一个 None 都没有。
    """
    print("\n[6] 手指耗尽时不产生非法 minitouch 指令")

    # 同一拍上12 个 Single(7 个 lane 循环)+ 每个后面再跟一个占手的长条,
    # 保证手指被占满,get_finger 返回 None。
    notes = [BPM_NOTE]
    for i in range(20):
        notes.append({"type": "Single", "lane": i % 7, "beat": 1.0})
    chart = build(notes)

    finger_actions = [a for a in chart.actions if a.get("finger", "x") is None]
    check("确实触发了手指耗尽(否则本用例测不到东西)", len(finger_actions) > 0,
          "finger=None 的动作 %d 个" % len(finger_actions))

    chart.actions_to_MNTcmd(
        RESOLUTION, 0,
        {"up": 0, "down": 0, "move": 0, "wait": 0, "interval": 0},
        size=10 ** 9,
    )
    cmds = [c.get("command") or "" for c in chart._commands]
    bad = [s for s in cmds if s.startswith(("d ", "m ", "u ")) and "None" in s]
    check("最终指令串无 None contact", not bad, "非法 %d 条: %s" % (len(bad), bad[:3]))

    # 每个 contact 必须是正整数 —— minitouch 的 contact id 从 0 开始,
    # 但本项目手指 id 从 1 起(见 available_fingers),不能出现 0 或负数。
    import re as _re

    ids = [int(m.group(1)) for s in cmds
           for m in [_re.match(r"[dmu] (-?\d+)", s)] if m]
    check("contact id 均为正整数", all(1 <= i <= chart_mod.MAX_FINGERS for i in ids),
          "越界 id: %s" % sorted({i for i in ids if not 1 <= i <= chart_mod.MAX_FINGERS}))
    check("contact id 不超 MAX_FINGERS(%d)" % chart_mod.MAX_FINGERS,
          not ids or max(ids) <= chart_mod.MAX_FINGERS,
          "最大 %d" % (max(ids) if ids else -1))


def test_finger_budget():
    """可用手指数应≥ minitouch 常规上限,避免密集曲必然漏打。"""
    print("\n[7] 手指预算")
    check("MAX_FINGERS >= 10", chart_mod.MAX_FINGERS >= 10,
          "当前 %d" % chart_mod.MAX_FINGERS)

    # 同步 8 个音符(7 个 lane,首尾同 lane 造重复)时,5 指会漏、10 指不该漏。
    notes = [BPM_NOTE] + [{"type": "Single", "lane": i % 7, "beat": 1.0} for i in range(8)]
    chart = build(notes)
    check("同拍 8 音符时无 finger=None",
          all(a.get("finger", "x") is not None for a in chart.actions
              if a["type"] in ("down", "move", "up")))


def main():
    test_zero_length_segment()
    test_normal_slide_not_broken()
    test_degenerate_segment_contributes_nothing()
    test_real_song_pattern()
    test_long_and_flick_untouched()
    test_no_illegal_minitouch_command()
    test_finger_budget()
    print("\n合计: %d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

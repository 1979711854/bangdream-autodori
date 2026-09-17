"""离线复算: 谱面里有多少音符抢不到手指(5 指全被占用)。

原理: chart.Chart.notes_to_actions() 内部维护 5 根手指的占用时间轴,
同步音符多于 5 个(含被长按/滑条长期占用的)时 get_finger() 返回 None,
这些音符不会生成点击指令 -> 必然 MISS。该方法自己会 logger.warning 汇总,
本脚本只负责捕获并显示它。

用法:
    .venv/Scripts/python.exe _diag_finger.py 664 hard
    .venv/Scripts/python.exe _diag_finger.py 664 hard 307 hard 412 hard
"""

import logging
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

# 捕获被测代码自己的判据输出
_CAPTURED = []


class _Handler(logging.Handler):
    def emit(self, record):
        _CAPTURED.append((record.name, record.levelname, record.getMessage()))


_root = logging.getLogger()
_root.addHandler(_Handler())
_root.setLevel(logging.WARNING)

# 静音第三方噪音, 只留我们关心的
for _n in ("BestdoriAPI", "urllib3", "peewee"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from chart import Chart  # noqa: E402

RESOLUTION = (1280, 720)
MOVE_SLICE = 10


def check(song_id, difficulty):
    _CAPTURED.clear()
    try:
        chart = Chart((song_id, difficulty))
    except Exception as exc:
        print("  [取谱失败] %s-%s: %s" % (song_id, difficulty, exc))
        return None

    total_notes = len(chart._chart_data) if chart._chart_data else 0
    try:
        chart.notes_to_actions(RESOLUTION, MOVE_SLICE)
    except Exception as exc:
        print("  [生成指令失败] %s-%s: %s" % (song_id, difficulty, exc))
        return None
    actions = chart.actions

    finger_msgs = [
        m for (_n, _lv, m) in _CAPTURED if "手指" in m or "finger" in m.lower()
    ]

    # 独立复核: 数量真正带 finger 字段的指令里 finger=None 的
    # 注意 wait 指令没有 finger 键, 不能用 a.get("finger") is None 判断
    finger_cmds = [a for a in actions if "finger" in a]
    none_finger = [a for a in finger_cmds if a["finger"] is None]
    none_notes = sorted({a.get("note") for a in none_finger}, key=lambda x: (x is None, x))

    print("  %s-%s: 谱面音符 %d, 生成指令 %d (其中手指指令 %d)" % (
        song_id, difficulty, total_notes, len(actions), len(finger_cmds)))
    if none_finger:
        print("    >> finger=None 的指令 %d 条, 涉及音符 %d 个" % (len(none_finger), len(none_notes)))
        print("    >> 音符序号: %s" % (none_notes[:30],))
    else:
        print("    >> 无 finger=None, 手指分配充足")
    for m in finger_msgs:
        print("    >> [被测代码告警] %s" % m)
    return none_notes


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = ["664", "hard"]

    pairs = [(args[i], args[i + 1]) for i in range(0, len(args) - 1, 2)]
    print("离线复算: 5 指占用导致的漏打 (%d 个谱面)" % len(pairs))
    for sid, diff in pairs:
        print("-" * 60)
        check(sid, diff)

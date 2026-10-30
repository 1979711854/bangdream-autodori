# -*- coding: utf-8 -*-
"""结算 OCR 读数修正 —— 离线回归(不需要模拟器、不需要游戏)。

2026-10-15 用户反馈两条现象:
  1. 日志里出现 `COMBO -1`、`GREAT 0(FAST -1/SLOW -1)` —— 看着像"负判定数";
  2. `COMBO` 与判定总数对不上:全连曲 `PERFECT 829 + GREAT 1` 却报 `COMBO 630`。

根因:结算页数字是**补零的浅灰细体**(实拍 `0848`/`0000`),OCR 把 0 读成字母
`O`/`D`、8 读成 `B` 之后 int() 抛异常,旧实现吞掉异常写 -1;而 -1 在意义上是
"未知",却被 GUI 原样打印。maxcombo 则是读错值后无人校验。

修复分三层,这里逐层验证:
  1. `_ocr_to_int`           —— 形近字符归一后再解析;
  2. `_reconcile_play_result` —— 用判定字段之间必然成立的关系纠正/否决读数;
  3. `gui._fmt_count`        —— -1 显示为 `-`。

用法:  .venv\\Scripts\\python.exe scripts/_test_playresult.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT_SRC = ROOT / "src" / "autodori.py"

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (label, ("| " + str(extra)) if extra else ""))


def load_symbols(path, names):
    """按名提取顶层函数/常量,exec 到独立命名空间。

    不直接 import src/autodori.py:它会拉起 MAA/adb 依赖并在磁盘上建目录,
    离线测试没必要付这个代价(与 scripts/_test_special.py 同一手法)。
    """
    src = path.read_text(encoding="utf-8")
    pieces = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            pieces.append(ast.get_source_segment(src, node))
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(t in names for t in targets):
                pieces.append(ast.get_source_segment(src, node))
    ns = {"re": re}
    exec("\n\n".join(pieces), ns)
    return ns


bot = load_symbols(
    BOT_SRC,
    {"_OCR_DIGIT_MAP", "_RESULT_COUNTS", "_ocr_to_int", "_reconcile_play_result"},
)
ocr_to_int = bot["_ocr_to_int"]
reconcile = bot["_reconcile_play_result"]


# --------------------------------------------------------------------------
# 1. 形近字符归一
# --------------------------------------------------------------------------
print("[1] _ocr_to_int:形近字符归一")
for text, want in [
    ("0848", 848),
    ("O848", 848),            # 0 读成字母 O
    ("o848", 848),
    ("OOOO", 0),              # FAST/SLOW 的 0000 整串读成字母
    ("0000", 0),
    ("B0", 80),               # 8 读成 B
    ("l234", 1234),           # 1 读成 l
    ("1,234", 1234),          # 分数带千分位
    ("2 575 026", 2575026),   # 分数被读成带空格
    ("", -1),
    ("--", -1),
    (None, -1),
]:
    got = ocr_to_int(text)
    check("ocr_to_int(%r) == %r" % (text, want), got == want, "got %r" % got)


# --------------------------------------------------------------------------
# 2. 真实日志样本(取自 debug/autodori-20261015-162122.log 等)
# --------------------------------------------------------------------------
def case(label, data, want):
    got = reconcile(dict(data))
    bad = {k: (want[k], got.get(k)) for k in want if got.get(k) != want[k]}
    check(label, not bad, "期望/实得 %r" % bad)


print("\n[2] _reconcile_play_result:真实日志样本")
# 16:37 全连(829+1)却报 630 —— 关系 2 应直接纠正为总数 830
case("16:37 全连把 830 读成 630",
     {"score": 2507763, "maxcombo": 630, "perfect": 829, "great": 1, "good": 0,
      "bad": 0, "miss": 0, "fast": -1, "slow": -1},
     {"maxcombo": 830, "fast": -1, "slow": -1})
# 16:24 全连(499)报 439;GREAT/GOOD 全 0 时 FAST/SLOW 必然是 0
case("16:24 全连把 499 读成 439",
     {"score": 2458859, "maxcombo": 439, "perfect": 499, "great": 0, "good": 0,
      "bad": 0, "miss": 0, "fast": -1, "slow": -1},
     {"maxcombo": 499, "fast": 0, "slow": 0})
# 16:30 maxcombo 整项识别失败(-1):总数 627、4 次断连 ⇒ 下界 126 ⇒ 仍判未知
case("16:30 maxcombo 识别失败保持未知",
     {"score": 2483094, "maxcombo": -1, "perfect": 623, "great": 0, "good": 0,
      "bad": 0, "miss": 4, "fast": -1, "slow": -1},
     {"maxcombo": -1, "fast": 0, "slow": 0})
# 16:27 有 4 次 miss、连击 130 ≥ 下界 95 ⇒ 合法,保持不动
case("16:27 合法的 130 不被改动",
     {"score": 2285962, "maxcombo": 130, "perfect": 468, "great": 0, "good": 0,
      "bad": 0, "miss": 4, "fast": -1, "slow": -1},
     {"maxcombo": 130, "fast": 0, "slow": 0})
# 16:33 恰好落在理论下界 234 上 ⇒ 合法
case("16:33 下界值 234 合法保留",
     {"score": 2533470, "maxcombo": 234, "perfect": 375, "great": 92, "good": 0,
      "bad": 0, "miss": 1, "fast": -1, "slow": -1},
     {"maxcombo": 234, "fast": -1, "slow": -1})
# 10-12 的噪声值 6514 超过总音符数 555 ⇒ 否决
case("10-12 超总数的 6514 判无效",
     {"score": 2500714, "maxcombo": 6514, "perfect": 520, "great": 33, "good": 0,
      "bad": 0, "miss": 2, "fast": -1, "slow": -1},
     {"maxcombo": -1, "fast": -1, "slow": -1})
# 10-11 全连却报 520,总数 326 ⇒ 纠正
case("10-11 全连把 326 读成 520",
     {"score": 1913733, "maxcombo": 520, "perfect": 316, "great": 10, "good": 0,
      "bad": 0, "miss": 0, "fast": -1, "slow": -1},
     {"maxcombo": 326, "fast": -1, "slow": -1})
# 10-10 本就读对的样本:纠正后必须一模一样(不能把对的改坏)
case("10-10 正确的 424 保持不动",
     {"score": 616276, "maxcombo": 424, "perfect": 422, "great": 2, "good": 0,
      "bad": 0, "miss": 0, "fast": 2, "slow": 0},
     {"maxcombo": 424, "fast": 2, "slow": 0})
# 判定项没读全时任何推算都不可信 ⇒ 原样返回
case("判定项缺失时不推算",
     {"score": 123, "maxcombo": 999, "perfect": -1, "great": 0, "good": 0,
      "bad": 0, "miss": 0, "fast": -1, "slow": -1},
     {"maxcombo": 999, "fast": -1, "slow": -1})


# --------------------------------------------------------------------------
# 3. GUI 展示
# --------------------------------------------------------------------------
print("\n[3] gui 展示:-1 不再当数字打印")
sys.path.insert(0, str(ROOT))
import gui

check("_fmt_count(-1) == '-'", gui._fmt_count(-1) == "-")
check("_fmt_count(None) == '-'", gui._fmt_count(None) == "-")
check("_fmt_count(0) == '0'", gui._fmt_count(0) == "0")
check("_fmt_count(1234) == '1234'", gui._fmt_count(1234) == "1234")


class Stub:
    """只承载 _on_play_result 依赖的状态,避开 Tk 控件。"""

    # 2026-10-29:_on_play_result 开头会消费「下发链错位」与「曲内漂移」两个
    # 报告(漂移门控)。直接借用真方法而不是在桩里重写一份 —— 它们只读
    # self._misalign_pending / _drift_pending,桩上没有就当没报告。
    _take_misalign = gui.AutodoriGUI._take_misalign
    _take_drift = gui.AutodoriGUI._take_drift

    def __init__(self):
        self.songs_done = 0
        self.m_songs = None
        self.auto_cal = False
        self.emitted = []

    def _emit(self, stamp, level, message):
        self.emitted.append(message)


stub = Stub()
gui.AutodoriGUI._on_play_result(
    stub,
    {"score": 2507763, "maxcombo": -1, "perfect": 829, "great": 1, "good": 0,
     "bad": 0, "miss": 0, "fast": -1, "slow": -1},
)
msg = stub.emitted[-1]
print("      日志行 -> %s" % msg)
check("整行不含 -1", "-1" not in msg)
check("未知项显示为 `-`", "COMBO -" in msg and "FAST -/SLOW -" in msg)

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)

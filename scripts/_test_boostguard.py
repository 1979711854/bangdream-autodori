# -*- coding: utf-8 -*-
"""火量识别的异常护栏回归(2026-10-29)。

背景:2026-10-28 22:02 场(实际时钟 10-29)演出被提前点开后,bot 还在按
「确认页」流程跑:
  · `LiveBoostEnoughRecognition` 在演出画面上读火量 → best_result 为 None
    → 旧写法 `.text` 抛 AttributeError(控制台第 1 条 Traceback);
  · `HandleLiveBoost` 拿到空 detail → `int("")` 抛 ValueError(第 2 条);
  · 异常被归到父节点 comfirm_song → on_error 在演出画面上永不命中 →
    3s 超时 → 整场任务失败、游戏停在「演出失败」弹窗。
本测试守住:两条路径都不许抛,读不到就返回 -1 / 放行。

跑法:.venv/Scripts/python.exe scripts/_test_boostguard.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import autodori as A  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-50s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-50s -> %s (want %s)" % (label, got, want))


class FakeBest:
    def __init__(self, text):
        self.text = text
        self.detail = text


class FakeReco:
    def __init__(self, text=None):
        self.best_result = FakeBest(text) if text is not None else None


class FakeCtx:
    def __init__(self, ret):
        self.ret = ret

    def run_recognition(self, name, image, pipeline=None):
        if isinstance(self.ret, Exception):
            raise self.ret
        return self.ret


class FakeArgv:
    def __init__(self, detail=""):
        self.image = None
        self.reco_detail = FakeReco(detail)


def main():
    print("== 火量识别:读不到不抛 ==")
    rec = A.LiveBoostEnoughRecognition()
    for label, ret in (
        ("返回对象但无 best_result", FakeReco(None)),
        ("识别返回 None", None),
        ("识别抛异常", RuntimeError("boom")),
    ):
        try:
            res = rec.analyze(FakeCtx(ret), FakeArgv())
            detail = res.detail
        except Exception as e:  # noqa: BLE001
            detail = "RAISE:%r" % (e,)
        check("读不到 → -1(%s)" % label, detail, "-1")

    print("== 火量识别:正常读数 ==")
    for text, want in (("12/25", "12"), (" 3 / 25", "3"), ("--", "-1")):
        res = rec.analyze(FakeCtx(FakeReco(text)), FakeArgv())
        check("OCR %r → %s" % (text, want), res.detail, want)

    print("== HandleLiveBoost:空/非法 detail 放行 ==")
    action = A.HandleLiveBoost()
    for label, detail in (("空串", ""), ("-1", "-1"), ("非数字", "n/a")):
        try:
            result = action.run(object(), FakeArgv(detail))
            got = bool(getattr(result, "success", result))
        except Exception as e:  # noqa: BLE001
            got = "RAISE:%r" % (e,)
        check("detail=%s → 放行(True)" % label, got, True)

    print("== HandleDailyBoostZero:空 detail 也放行 ==")
    daily = A.HandleDailyBoostZero()
    try:
        result = daily.run(object(), FakeArgv(""))
        got = bool(getattr(result, "success", result))
    except Exception as e:  # noqa: BLE001
        got = "RAISE:%r" % (e,)
    check("空 detail → 放行,不误停日常", got, True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""崩局诊断离线回归(2026-10-28)。

背景:18:30 场 #189/#413 两首中途「演出失败」,而下发链干净、生命条没报,
事后只有一条 OCR 文本,分辨不出是时间线错还是某一段崩。新增三样只读取证:
  1. 首音触发后约 1s 复用已抓的帧存「首音后」画面;
  2. 崩局瞬间存帧 + OCR 左上角分数;
  3. 崩局一行日志 + debug/crash/<时间>-<曲id-难度>.txt。
本测试守住:不重复截图、没有首音记录时不崩、落盘内容包含关键字段。

跑法:.venv/Scripts/python.exe _test_crashdiag.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import numpy as np  # noqa: E402

import autodori as A  # noqa: E402

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-52s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-52s -> %s (want %s)" % (label, got, want))


def frame():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


class FakeReco:
    def __init__(self, text):
        self.best_result = type("B", (), {"text": text})() if text else None


class FakeContext:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    def run_recognition(self, name, screen, pipeline=None):
        self.calls.append(name)
        return FakeReco(self.text)


class FakeChart:
    def __init__(self, sent=1234, total=5678):
        self.actions_to_cmd_index = sent
        self.actions = list(range(total))


def main():
    saved = []
    old_save = A._save_frame_async

    def fake_save(tag, img, subdir="debug/firstnote"):
        saved.append((tag, subdir))

    A._save_frame_async = fake_save
    tmp = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        print("== 首音后截图:只拍一次、不额外抓图 ==")
        A._FIRST_NOTE_DIAG.clear()
        A._maybe_snap_firstnote_post(frame())
        check("无首音记录时不截图", len(saved), 0)
        A._FIRST_NOTE_DIAG.update({"t": time.perf_counter()})
        A._maybe_snap_firstnote_post(frame())
        check("首音后 <800ms 不截图", len(saved), 0)
        A._FIRST_NOTE_DIAG["t"] = time.perf_counter() - 1.2
        A._maybe_snap_firstnote_post(frame())
        check("首音后 ≥800ms 存一张", len(saved), 1)
        A._maybe_snap_firstnote_post(frame())
        check("本局只拍一次", len(saved), 1)

        print("== 崩局取证:落帧 + 分数 + txt ==")
        os.chdir(tmp)
        A._FIRST_NOTE_DIAG.clear()
        A._FIRST_NOTE_DIAG.update({
            "kind": "interp", "band_ms": 4359.0, "lanes": 3,
            "change": 9.31, "noise": 0.0, "y": 512.4,
            "gate": 70, "t": time.perf_counter() - 2.5, "post_saved": True,
        })
        saved.clear()
        ctx = FakeContext("0000123")
        A._dump_crash_diag(ctx, frame(), "189-special", 48, FakeChart())
        check("崩局存帧到 debug/crash", saved,
              [("crash", "debug/crash")])
        check("分数 OCR 用的是 crash_score 节点", ctx.calls, ["crash_score"])
        files = os.listdir("debug/crash")
        check("txt 已落盘", len([f for f in files if f.endswith(".txt")]), 1)
        body = open(os.path.join("debug/crash", files[0]), encoding="utf-8").read()
        check("txt 带分数", "'0000123'" in body, True)
        check("txt 带首音触发参数", "首音触发[interp]" in body, True)
        check("txt 带指令索引", "1234/5678" in body, True)

        print("== 没有首音记录时也要能落盘 ==")
        A._FIRST_NOTE_DIAG.clear()
        _clear = os.listdir("debug/crash")
        A._dump_crash_diag(FakeContext(""), frame(), "413-special", 10,
                           FakeChart())
        files = [f for f in os.listdir("debug/crash") if f not in _clear]
        check("无首音记录仍写 txt", len(files), 1)
        body = open(os.path.join("debug/crash", files[0]), encoding="utf-8").read()
        check("txt 说明本局无首音记录", "本局无记录" in body, True)
        check("OCR 读不出分数时为空白", "画面分数 ''" in body, True)
    finally:
        A._save_frame_async = old_save
        os.chdir(old_cwd)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

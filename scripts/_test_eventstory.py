# -*- coding: utf-8 -*-
"""活动故事阅读的离线回归(2026-10-31)。

覆盖四件事:
1. 「取最下面的 NEW」—— 不撞「该剧情尚未解锁」的核心判据;
2. 进度文件落盘与读取(只做记录,不参与流程判断);
3. 列表滚不动时提前收工(2026-11-06 实机:到顶后仍空滑 6 次约 10 秒);
4. eventstory.json 的节点引用完整性 + MAA 查重铁律(不跑真机也能查出笔误)。

有实机截图(debug/local/_evt_*.png)时额外做真实模板匹配:
列表页必须选中 y 更大的那个 NEW,剧情界面必须一个都认不出。
截图不在仓库里,缺了就跳过那一组,不算失败。

跑法:python scripts/_test_eventstory.py
"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)
import json
import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import eventstory as ev  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


def test_pick_lowest():
    print("[2] 取最下面的 NEW")
    boxes = [[234, 182, 60, 44], [234, 92, 60, 44], [234, 400, 60, 44]]
    got = ev.pick_lowest(boxes)
    check("三个 NEW 取 y 最大的", got == [234, 400, 60, 44], f"got={got}")
    check("空列表返回 None", ev.pick_lowest([]) is None)


def test_progress_file():
    print("[3] 进度落盘与读取")
    tmp = tempfile.mkdtemp()
    old = ev.PROGRESS_PATH
    ev.PROGRESS_PATH = os.path.join(tmp, "event_story_progress.json")
    try:
        ev._STATE.update({"read": 2, "blocked": "", "entry_failed": False})
        ev._save_progress()
        data = ev.load_progress()
        check("last 写入", data.get("last", {}).get("read") == 2, f"{data}")
        check("history 追加", len(data.get("history") or []) == 1)
        ev._save_progress("locked")
        data = ev.load_progress()
        check("blocked 写入", data["last"]["blocked"] == "locked")
        check("history 累积", len(data.get("history") or []) == 2)
    finally:
        ev.PROGRESS_PATH = old


class _FakeCtx:
    """只实现 analyze 里用到的 run_recognition,其它一律不用。"""

    def run_recognition(self, *args, **kwargs):
        return None


class _FakeArg:
    def __init__(self, image):
        self.image = image


def test_pick_early_stop():
    """2026-11-06 实机问题:列表到顶后仍空滑 6 次(约 10 秒)。

    判据不变(没有 NEW 才算读完),只是"连续两次画面没变"就提前收工。
    """
    print("[6] 列表滚不动时提前收工")
    import time as _time

    saved_shot, saved_swipe = ev._screencap, ev._swipe_list_up
    saved_time = ev.time
    ev.time = type("T", (), {"sleep": staticmethod(lambda _s: None),
                             "strftime": staticmethod(_time.strftime)})()
    calls = {"swipe": 0}
    ev._swipe_list_up = lambda ctx: calls.__setitem__("swipe", calls["swipe"] + 1)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    ev._STATE.update({"read": 0, "blocked": "", "entry_failed": False,
                      "end_reason": "", "scroll_attempts": 0})
    try:
        ev._screencap = lambda ctx: frame.copy()
        res = ev.EventChapterPick().analyze(_FakeCtx(), _FakeArg(frame.copy()))
        detail = json.loads(res.detail)
        check("画面不动时 found=false", detail.get("found") is False, f"{detail}")
        check("只滑 2 次就收工", calls["swipe"] == 2, f"swipe={calls['swipe']}")
        check("结束原因=列表已到顶",
              ev._STATE["end_reason"] == "no_new_no_scroll", f"{ev._STATE}")

        # 反向:每次滑动画面都在变 → 必须滚满 SCROLL_LIMIT 次才结束
        ev._STATE.update({"end_reason": "", "scroll_attempts": 0})
        calls["swipe"] = 0
        frames = iter([np.full((720, 1280, 3), (i + 1) * 20, dtype=np.uint8)
                       for i in range(ev.SCROLL_LIMIT + 2)])
        ev._screencap = lambda ctx: next(frames)
        ev.EventChapterPick().analyze(_FakeCtx(), _FakeArg(frame.copy()))
        check("画面在变时滚满 %d 次" % ev.SCROLL_LIMIT,
              calls["swipe"] == ev.SCROLL_LIMIT, f"swipe={calls['swipe']}")
        check("结束原因=滚满上限",
              ev._STATE["end_reason"] == "no_new_scroll_limit", f"{ev._STATE}")
    finally:
        ev._screencap, ev._swipe_list_up = saved_shot, saved_swipe
        ev.time = saved_time
        ev._STATE.update({"read": 0, "blocked": "", "entry_failed": False,
                          "end_reason": "", "scroll_attempts": 0})


def test_frame_diff():
    print("[7] 画面比对")
    a = np.zeros((10, 10, 3), dtype=np.uint8)
    check("同一帧差为 0", ev._frame_diff_mean(a, a.copy()) == 0.0)
    b = np.full((10, 10, 3), 200, dtype=np.uint8)
    check("大改动的平均差很大", ev._frame_diff_mean(a, b) > 1.0)
    check("尺寸不一致按「变了」处理(保守)",
          ev._frame_diff_mean(a, np.zeros((5, 5, 3), dtype=np.uint8)) == 255.0)


def test_pipeline_refs():
    print("[4] pipeline 节点引用")
    p = ROOT / "assets" / "resource" / "pipeline" / "eventstory.json"
    nodes = json.loads(p.read_text(encoding="utf-8"))
    all_nodes = {}
    for f in (ROOT / "assets" / "resource" / "pipeline").glob("*.json"):
        all_nodes.update(json.loads(f.read_text(encoding="utf-8")))
    missing = []
    for name, node in nodes.items():
        refs = []
        for key in ("next", "interrupt", "on_error"):
            val = node.get(key)
            if val is None:
                continue
            refs += [val] if isinstance(val, str) else list(val)
        for r in refs:
            if r not in all_nodes:
                missing.append(f"{name}.{r}")
    check("引用的节点都存在", not missing, f"缺失={missing}")

    # MAA 铁律:同一节点的 next/interrupt/on_error 合并后不能有重复名字,
    # 否则整份资源加载失败。自引用(next 里含自己)是合法的 —— 等加载/等菜单
    # 这类节点都这么写(story.json 的 story_load_wait 同样)。
    dups = []
    for name, node in nodes.items():
        refs = []
        for key in ("next", "interrupt", "on_error"):
            val = node.get(key)
            if val is None:
                continue
            refs += [val] if isinstance(val, str) else list(val)
        seen = set()
        for r in refs:
            if r in seen:
                dups.append(f"{name}.{r}")
            seen.add(r)
    check("next/interrupt/on_error 合集无重复", not dups, f"重复={dups}")


def _has_shots():
    return all((ROOT / "debug" / "local" / f).exists() for f in
               ("_evt_done.png", "_evt_rd2.png"))


def test_templates_on_shot():
    print("[5] 实机截图模板匹配")
    tpl = ROOT / "assets" / "resource" / "image" / "event" / "event_new_tag.png"
    img_path = ROOT / "debug" / "local" / "_evt_done.png"
    img = cv2.imread(str(img_path))
    t = cv2.imread(str(tpl))
    if img is None or t is None:
        check("读取截图与模板", False)
        return
    x, y, w, h = ev.LIST_ROI
    crop = img[y:y + h, x:x + w]
    res = cv2.matchTemplate(crop, t, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= 0.85)
    boxes = [[int(px) + x, int(py) + y, t.shape[1], t.shape[0]]
             for py, px in zip(ys, xs)]
    check("列表页至少命中一个 NEW", bool(boxes), f"命中={len(boxes)}")
    if boxes:
        got = ev.pick_lowest(boxes)
        # 截图里第 2 话(上)和第 1 话(下)都带 NEW,必须选位置更低的第 1 话
        check("选中位置更低的 NEW", got[1] > min(b[1] for b in boxes),
              f"got={got}")

    # 剧情界面不该认出 NEW / 阅读按钮,否则会在播放中误点
    rd = cv2.imread(str(ROOT / "debug" / "local" / "_evt_rd2.png"))
    if rd is not None:
        for name in ("event_new_tag.png", "event_read_btn.png"):
            tpl2 = cv2.imread(str(ROOT / "assets" / "resource" / "image"
                                   / "event" / name))
            r = cv2.matchTemplate(rd, tpl2, cv2.TM_CCOEFF_NORMED)
            _, best, _, _ = cv2.minMaxLoc(r)
            check(f"剧情界面不误命中 {name}", best < 0.85, f"best={best:.3f}")


def main():
    test_pick_lowest()
    test_progress_file()
    test_pick_early_stop()
    test_frame_diff()
    test_pipeline_refs()
    if _has_shots():
        test_templates_on_shot()
    else:
        print("[5] 实机截图不在(debug/local/_evt_*.png),跳过模板匹配")
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

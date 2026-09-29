"""按 pipeline 里的定义在**一张截图**上跑一遍模板识别,回答:
「玩家停在这个页面时,脚本会命中哪个节点、会不会误命中?」

用途:新活动/新界面接入前,拿一张 1280x720 的实机截图离线验证模板与阈值,
不跑真机、不点屏幕。也能在游戏更新改版后快速定位「哪个模板失效了」。

用法(项目根,受管 venv):
    python _diag_tpl.py debug/_shot_confirm.png
    python _diag_tpl.py debug/_shot_confirm.png special_wait special_click_confirm
    python _diag_tpl.py debug/_shot_confirm.png --all      # 连未达阈值的也列出来

注意:只做 TemplateMatch;OCR 节点需要 MAA 运行期,不在此工具范围内。
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = ROOT / "assets" / "resource" / "pipeline"
IMAGE_DIR = ROOT / "assets" / "resource" / "image"
DEFAULT_THRESHOLD = 0.8  # MAA TemplateMatch 默认阈值


def load_pipeline():
    merged = {}
    where = {}
    for p in sorted(PIPELINE_DIR.glob("*.json")):
        for name, node in json.loads(p.read_text(encoding="utf-8")).items():
            merged[name] = node
            where[name] = p.name
    return merged, where


def as_list(val):
    if val is None:
        return []
    return [val] if isinstance(val, str) else list(val)


def best_score(img, tpl_path, roi):
    """返回该模板在(可选 ROI 内)的最高 TM_CCOEFF_NORMED 分数与位置。"""
    tpl = cv2.imread(str(tpl_path))
    if tpl is None:
        return None, None, "模板文件不存在"
    crop, off = img, (0, 0)
    if roi and len(roi) == 4:
        x, y, w, h = [int(v) for v in roi]
        crop = img[y:y + h, x:x + w]
        off = (x, y)
    if crop.size == 0:
        return None, None, "ROI 越界"
    if tpl.shape[0] > crop.shape[0] or tpl.shape[1] > crop.shape[1]:
        return None, None, "模板比区域大"
    res = cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED)
    y, x = np.unravel_index(int(np.argmax(res)), res.shape)
    return float(res[y, x]), (int(x) + off[0], int(y) + off[1]), None


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        sys.exit(1)
    shot = Path(argv[0])
    show_all = "--all" in argv
    only = [a for a in argv[1:] if not a.startswith("--")]

    img = cv2.imread(str(shot))
    if img is None:
        print("读不到截图:", shot)
        sys.exit(1)
    h, w = img.shape[:2]
    print("截图 %s  %dx%d" % (shot, w, h))
    if (w, h) != (1280, 720):
        print("注意: 模板按 1280x720 裁剪,非该分辨率的匹配结果不可信")

    merged, where = load_pipeline()
    rows = []
    for name, node in merged.items():
        if only and name not in only:
            continue
        if node.get("recognition") != "TemplateMatch":
            continue
        thr = node.get("threshold", DEFAULT_THRESHOLD)
        for tpl in as_list(node.get("template")):
            score, pos, err = best_score(img, IMAGE_DIR / tpl, node.get("roi"))
            if err:
                rows.append((name, tpl, None, None, err, thr, node))
                continue
            hit = score >= thr
            if node.get("inverse"):
                hit = not hit
            rows.append((name, tpl, score, pos, None, thr, node))

    rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    print()
    print("%-34s %-34s %8s %6s %-16s %s" % ("节点", "模板", "score", "阈值", "位置", "命中"))
    print("-" * 110)
    for name, tpl, score, pos, err, thr, node in rows:
        if score is None:
            print("%-34s %-34s %8s %6.2f %-16s %s" % (name, tpl, "-", thr, "-", err))
            continue
        hit = score >= thr
        if node.get("inverse"):
            hit = not hit
        if not show_all and not hit and not node.get("inverse"):
            continue
        print("%-34s %-34s %8.4f %6.2f %-16s %s%s"
              % (name, tpl, score, thr, "%d,%d" % pos,
                 "命中" if hit else "未达阈值",
                 "(inverse)" if node.get("inverse") else ""))

    print()
    hits = [(r[0], r[2]) for r in rows if r[2] is not None]
    over = [(n, s) for n, s in hits if s >= 0.8]
    if not over:
        print("结论: 这张页面上没有任何模板达阈值 —— 该页会被判为「空屏」")
    else:
        top = max(over, key=lambda x: x[1])
        print("结论: 最高分 %s (%.4f)" % (top[0], top[1]))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""验证结算页 FAST/SLOW 二值化增强后 OCR 能否读出数字。

走 custom recognition + post_task 拿到真实 Context(离线,不连设备)。

用法: .venv\\Scripts\\python.exe _test_fastslow_ocr.py [截图.jpg]
"""
import logging
import sys

logging.basicConfig(level=logging.WARNING)

import cv2  # noqa: E402
from maa.controller import AdbController  # noqa: E402
from maa.custom_recognition import CustomRecognition  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402

ROIS = {
    "score": [1028, 192, 144, 35],
    "maxcombo": [1009, 391, 91, 28],
    "perfect": [829, 282, 90, 28],
    "great": [828, 322, 91, 27],
    "good": [829, 363, 91, 27],
    "bad": [829, 401, 90, 27],
    "miss": [830, 438, 91, 28],
    "fast": [1088, 283, 90, 27],
    "slow": [1088, 323, 91, 28],
}
SHOT = None


def binarize(image, rois, thresh=235):
    out = image.copy()
    h, w = out.shape[:2]
    for x, y, rw, rh in rois:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + rw), min(h, y + rh)
        region = out[y0:y1, x0:x1]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
        out[y0:y1, x0:x1] = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
    return out


class TestOCR(CustomRecognition):
    def analyze(self, context, argv):
        img = SHOT
        enhanced = binarize(img, list(ROIS.values()))
        pipeline = {
            f"_t_{k}": {"recognition": "OCR", "only_rec": True, "roi": v}
            for k, v in ROIS.items()
        }
        print("%-10s %-14s %-14s" % ("字段", "原图", "增强图"))
        for k in ROIS:
            texts = []
            for image in (img, enhanced):
                reco = context.run_recognition(f"_t_{k}", image, pipeline)
                texts.append(
                    reco.best_result.text if reco and reco.best_result else "")
            print("%-10s %-14s %-14s" % (k, repr(texts[0]), repr(texts[1])))
        return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="done")


def main():
    global SHOT
    path = sys.argv[1] if len(sys.argv) > 1 else "screenshots/record1.jpg"
    SHOT = cv2.imread(path)
    if SHOT is None:
        print("读不到截图:", path)
        return 1
    res = Resource()
    res.post_bundle("assets/resource").wait()
    res.custom_recognition("_TestOCR")(TestOCR)
    ctrl = AdbController(
        adb_path=r"D:\MuMu Player 12\nx_main\adb.exe",
        address="127.0.0.1:16384",
    )
    job = ctrl.post_connection()
    job.wait()
    if not job.succeeded:
        print("adb 连接失败")
        return 1
    tasker = Tasker()
    tasker.bind(res, ctrl)
    tasker.post_task(
        "_test_entry",
        {"_test_entry": {"recognition": "Custom", "custom_recognition": "_TestOCR"}},
    ).wait().get()
    return 0


if __name__ == "__main__":
    sys.exit(main())

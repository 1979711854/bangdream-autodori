# -*- coding: utf-8 -*-
"""验证「选择演出」界面的 OCR 判据与返回箭头位置。"""

import _bootstrap  # noqa: E402,F401  (脚本在 scripts/,由它设置 CWD 与 sys.path)
import logging
import sys

logging.basicConfig(level=logging.WARNING)

import cv2  # noqa: E402
from maa.controller import AdbController  # noqa: E402
from maa.custom_recognition import CustomRecognition  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402

SHOT = None
ROIS = {
    "title": [90, 30, 320, 70],       # 「选择演出」标题
    "free": [430, 300, 300, 260],     # 「自由演出」大按钮
}


class LiveHomeOCR(CustomRecognition):
    def analyze(self, context, argv):
        for name, roi in ROIS.items():
            pipeline = {"_t": {"recognition": "OCR", "only_rec": True, "roi": roi}}
            reco = context.run_recognition("_t", SHOT, pipeline)
            text = reco.best_result.text if reco and reco.best_result else ""
            print("%-8s roi=%s -> %r" % (name, roi, text))
        # 左上角返回箭头区域(截图目视 x≈40-90, y≈45-75)
        x, y, w, h = 20, 30, 140, 100
        cv2.imwrite("_diag_back_arrow.png", SHOT[y:y + h, x:x + w])
        return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail="")


def main():
    global SHOT
    path = sys.argv[1] if len(sys.argv) > 1 else "_diag_daily_stuck.png"
    SHOT = cv2.imread(path)
    if SHOT is None:
        print("读不到:", path)
        return 1
    res = Resource()
    res.post_bundle("assets/resource").wait()
    res.custom_recognition("_LH")(LiveHomeOCR)
    ctrl = AdbController(
        adb_path=r"D:\MuMu Player 12\nx_main\adb.exe",
        address="127.0.0.1:16384")
    job = ctrl.post_connection()
    job.wait()
    if not job.succeeded:
        print("adb 连接失败")
        return 1
    tasker = Tasker()
    tasker.bind(res, ctrl)
    tasker.post_task(
        "_t", {"_t": {"recognition": "Custom",
                      "custom_recognition": "_LH"}}).wait().get()
    return 0


if __name__ == "__main__":
    sys.exit(main())

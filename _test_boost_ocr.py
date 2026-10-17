# -*- coding: utf-8 -*-
"""验证主页火罐 OCR:roi [979,30,61,20] 在主页截图上能否读出火量。"""
import logging
import sys

logging.basicConfig(level=logging.WARNING)

import cv2  # noqa: E402
from maa.controller import AdbController  # noqa: E402
from maa.custom_recognition import CustomRecognition  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402

SHOT = None
ROI = [979, 30, 61, 20]


class BoostOCR(CustomRecognition):
    def analyze(self, context, argv):
        pipeline = {"_b": {"recognition": "OCR", "only_rec": True, "roi": ROI}}
        reco = context.run_recognition("_b", SHOT, pipeline)
        text = reco.best_result.text if reco and reco.best_result else ""
        print("主页火罐 OCR 原文: %r" % text)
        return CustomRecognition.AnalyzeResult(box=(0, 0, 1, 1), detail=text)


def main():
    global SHOT
    path = sys.argv[1] if len(sys.argv) > 1 else "_live_now.png"
    SHOT = cv2.imread(path)
    if SHOT is None:
        print("读不到:", path)
        return 1
    res = Resource()
    res.post_bundle("assets/resource").wait()
    res.custom_recognition("_BoostOCR")(BoostOCR)
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
                      "custom_recognition": "_BoostOCR"}}).wait().get()
    return 0


if __name__ == "__main__":
    sys.exit(main())

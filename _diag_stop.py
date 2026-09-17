"""验证火罐不足时的退出逻辑: _exit_game_and_stop_task 能否真正终止任务。

不需要模拟器 —— 用 CustomController 提供纯黑画面, 直接跑 MaaFramework
pipeline, 检验真实修复代码的行为。

判据: 自定义动作执行后, 任务应在"陷阱节点"之前就结束(耗时远小于陷阱超时)。
若终止失效, 任务会走到陷阱节点并卡满超时。

用法: .venv/Scripts/python.exe _diag_stop.py
"""
import logging
import os
import sys
import time

import numpy as np

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
logging.disable(logging.INFO)

import autodori  # noqa: E402
from maa.controller import AdbController, CustomController  # noqa: E402
from maa.custom_action import CustomAction  # noqa: E402

TRAP_TIMEOUT_MS = 20000
CALLS = []


class FakeController(CustomController):
    """假控制器: 固定纯黑画面, 只记录收到的指令。"""

    def connect(self):
        return True

    def request_uuid(self):
        return "fake-uuid"

    def start_app(self, intent):
        CALLS.append("start_app")
        print("  [ctrl] start_app(%s)   <- 游戏被启动" % intent)
        return True

    def stop_app(self, intent):
        CALLS.append("stop_app")
        print("  [ctrl] stop_app(%s)   <- 游戏被关闭" % intent)
        return True

    def screencap(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def click(self, x, y):
        return True

    def swipe(self, x1, y1, x2, y2, duration):
        return True

    def touch_down(self, contact, x, y, pressure):
        return True

    def touch_move(self, contact, x, y, pressure):
        return True

    def touch_up(self, contact):
        return True

    def press_key(self, keycode):
        return True

    def input_text(self, text):
        return True


@autodori.maaresource.custom_action("TestExitProbe")
class TestExitProbe(CustomAction):
    """直接调用生产代码里的修复函数, 模拟 HandleLiveBoost 的退出分支。"""

    def run(self, context, argv):
        print("  [probe] 进入自定义动作, 调用生产修复函数")
        t0 = time.time()
        autodori._exit_game_and_stop_task(context)
        print("  [probe] 修复函数返回 (%.2fs)" % (time.time() - t0))
        return CustomAction.RunResult(False)  # 与生产代码保持一致


def main():
    use_fake = "--fake" in sys.argv
    print("=== 加载资源 ===")
    autodori.maaresource.post_bundle("assets/resource").wait()
    print("  resource loaded: %s" % autodori.maaresource.loaded)

    if use_fake:
        print("=== 使用假控制器 (纯黑画面) ===")
        controller = FakeController()
    else:
        print("=== 连接真机模拟器 ===")
        controller = AdbController(
            adb_path=r"D:\MuMu Player 12\nx_main\adb.exe",
            address="127.0.0.1:16384",
        )
    conn = controller.post_connection().wait().succeeded
    print("  controller connected: %s" % conn)
    if not conn:
        print("控制器连接失败")
        return 2
    autodori.maatasker.bind(autodori.maaresource, controller)
    print("  tasker inited: %s" % autodori.maatasker.inited)
    if not autodori.maatasker.inited:
        print("Tasker 初始化失败")
        return 2

    pipeline = {
        "probe_entry": {"next": ["probe_node"]},
        "probe_node": {
            "action": "Custom",
            "custom_action": "TestExitProbe",
            "next": "probe_trap",
        },
        # 陷阱: 只有终止失效时才会走到这里, 并卡满超时
        "probe_trap": {
            "recognition": "OCR",
            "expected": ["ZZZ_IMPOSSIBLE_TEXT_ZZZ"],
            "timeout": TRAP_TIMEOUT_MS,
        },
    }

    print("=== 运行测试任务 (陷阱超时 %ds) ===" % (TRAP_TIMEOUT_MS // 1000))
    t0 = time.time()
    job = autodori.maatasker.post_task("probe_entry", pipeline)
    result = job.wait().get()
    elapsed = time.time() - t0

    print()
    print("  任务结果: succeeded=%s" % job.succeeded)
    print("  耗时: %.2f s" % elapsed)
    print("  控制器收到的调用: %s" % (CALLS,))
    print()
    ok = elapsed < TRAP_TIMEOUT_MS / 1000.0 * 0.5
    if ok:
        print("结论: 终止生效 —— 任务在陷阱节点前结束, 游戏关闭后不会再被拉起")
    else:
        print("结论: 终止失效 —— 任务走到了陷阱节点")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""验证「关游戏 → 停任务 → 收尾释放」整条退出联动会不会卡死。

背景(2026-09-18):火罐为 0 且配置为「退出游戏」时,游戏确实被关掉,但脚本仍在
挂机。旧收尾是 `mnt.stop(); sys.exit()`,而 sys.exit() 只抛 SystemExit,之后解释器
还要 join 非 daemon 线程 —— minitouchpy 的 STDIO 读线程恰好是非 daemon 的,一旦它
阻塞在 readline 上,进程就永远退不掉。

本脚本用「父进程按 GUI 的方式用管道拉起子进程」复现这条链路,测量:
  - 子进程是否真的退出、耗时多少
  - 管道的 EOF 是否按时到达(界面「运行中」的复位就靠它)

对照组 `--legacy` 用旧收尾(mnt.stop + sys.exit),用来确认新收尾确实修掉了卡死。

用法:
  .venv/Scripts/python.exe _diag_exitlink.py            # 跑新/旧两组并对比
  .venv/Scripts/python.exe _diag_exitlink.py --child    # 只跑子进程(内部用)
"""
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
sys.path.insert(0, str(BASE / "src"))

import autodori  # noqa: E402
from maa.controller import CustomController  # noqa: E402
from maa.custom_action import CustomAction  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402

TRAP_TIMEOUT_MS = 20000
EOF_TIMEOUT_S = 60.0
MUMU_ADB = r"D:\MuMu Player 12\nx_main\adb.exe"
MUMU_ADDR = "127.0.0.1:16384"


class FakeController(CustomController):
    """假控制器:纯黑画面。不发任何真实点击,只记录收到的指令。"""

    def connect(self):
        return True

    def request_uuid(self):
        return "fake-uuid"

    def start_app(self, intent):
        print("  [ctrl] start_app(%s)" % intent, flush=True)
        return True

    def stop_app(self, intent):
        print("  [ctrl] stop_app(%s)  <- 游戏被关闭" % intent, flush=True)
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


@autodori.maaresource.custom_action("DiagExitProbe")
class DiagExitProbe(CustomAction):
    """直接调用生产代码的退出函数,模拟 HandleLiveBoost 的退出分支。"""

    def run(self, context, argv):
        print("  [probe] 调用 _exit_game_and_stop_task", flush=True)
        t0 = time.time()
        autodori._exit_game_and_stop_task(context, "diag 退出联动测试")
        print("  [probe] 返回 (%.2fs)" % (time.time() - t0), flush=True)
        return CustomAction.RunResult(False)


@autodori.maaresource.custom_action("DiagWatchdogProbe")
class DiagWatchdogProbe(CustomAction):
    """只登记退出请求、**不**执行 stop,用来验证看门狗兜底。

    模拟「need_to_stop 那条软路径没落实」:任务随后进入陷阱节点继续跑满超时,
    只有看门狗带外 post_stop 才能把它停下来。
    """

    def run(self, context, argv):
        print("  [probe] 只登记退出请求, 不调用 stop(软路径故意不生效)", flush=True)
        autodori._request_exit("diag 看门狗兜底测试")
        return CustomAction.RunResult(True)


def _find_device():
    """优先用 MAA 探测到的模拟器;探测不到就退回 MuMu 默认地址。"""

    class _Stub:
        def __init__(self, adb_path, address):
            self.adb_path = Path(adb_path)
            self.address = address
            self.name = "diag-stub"
            self.config = {}

    for with_option in (False, True):
        try:
            if with_option:
                Toolkit.init_option(str(BASE))
            devices = Toolkit.find_adb_devices()
        except Exception:
            devices = []
        for dev in devices or []:
            extras = (dev.config or {}).get("extras", {})
            if "mumu" in extras or "ld" in extras:
                return dev
    return _Stub(MUMU_ADB, MUMU_ADDR)


def _device_description(dev):
    return "{} @ {}".format(dev.name, dev.address)


def run_child(mode: str) -> int:
    """子进程:跑一次完整的「任务 → 退出请求 → 收尾」。

    mode: new = 新收尾 / legacy = 旧收尾(mnt.stop + sys.exit)
          legacy-nostop = 旧收尾但跳过 mnt.stop(用来验证非 daemon 读线程会不会
                          把 sys.exit 卡住 —— 这是「脚本不停止」的机制性证据)
    """
    legacy = mode in ("legacy", "legacy-nostop")
    autodori.configure_log()
    autodori.enable_high_precision_timer()
    autodori.device = _find_device()
    print(
        "  [child] 设备: %s (adb=%s)"
        % (_device_description(autodori.device), autodori.device.adb_path),
        flush=True,
    )
    print(
        "  [child] 游戏进程查询: %r" % (autodori._game_process_running(),), flush=True
    )

    autodori.maaresource.post_bundle(str(BASE / "assets" / "resource")).wait()
    print("  [child] resource loaded: %s" % autodori.maaresource.loaded, flush=True)

    controller = FakeController()
    if not controller.post_connection().wait().succeeded:
        print("  [child] 假控制器连接失败", flush=True)
        return 2
    autodori.maatasker.bind(autodori.maaresource, controller)
    if not autodori.maatasker.inited:
        print("  [child] Tasker 初始化失败", flush=True)
        return 2

    # 真实 MNT:非 daemon 读线程就在这里产生,是本脚本要验证的关键资源。
    autodori.mnt = autodori.MNT(
        autodori.device.address,
        type_="EvATive7",
        communicate_type=autodori.MNTServerCommunicateType.STDIO,
        mnt_asset_path=BASE / "assets" / "minitouch_EvATive7",
        callback=None,
        adb_executor=str(autodori.device.adb_path),
    )
    print("  [child] MNT inited", flush=True)

    probe = "DiagWatchdogProbe" if mode == "watchdog" else "DiagExitProbe"
    # watchdog 组靠看门狗兜底,陷阱必须比 _EXIT_HARD_S 更长,否则"陷阱超时结束"
    # 会与"看门狗硬收尾"混在一起分不清
    trap_timeout = 40000 if mode == "watchdog" else TRAP_TIMEOUT_MS
    pipeline = {
        "diag_entry": {"next": ["diag_probe"]},
        "diag_probe": {
            "action": "Custom",
            "custom_action": probe,
            "next": "diag_trap",
        },
        # 陷阱:终止失效时任务会走到这里并卡满超时
        "diag_trap": {
            "recognition": "OCR",
            "expected": ["ZZZ_IMPOSSIBLE_TEXT_ZZZ"],
            "timeout": trap_timeout,
        },
    }

    if not legacy:
        autodori._start_exit_watchdog()

    t0 = time.time()
    autodori.maatasker.post_task("diag_entry", pipeline).wait().get()
    print("  [child] 任务结束,耗时 %.2fs" % (time.time() - t0), flush=True)

    if legacy:
        # 旧收尾(2026-09-18 之前的 main() 尾部)
        if mode == "legacy-nostop":
            print("  [child] 旧收尾: 跳过 mnt.stop(),直接 sys.exit()", flush=True)
        else:
            try:
                autodori.mnt.stop()
            except Exception as exc:
                print("  [child] mnt.stop 异常: %s" % exc, flush=True)
            print("  [child] 旧收尾: mnt.stop() 完成", flush=True)
        logging.debug("Ready to exit")
        print("  [child] 旧收尾: 准备 sys.exit()", flush=True)
        sys.exit(0)

    print("  [child] 新收尾: 准备 _shutdown()", flush=True)
    autodori._shutdown(0, "diag 收尾测试")
    print("  [child] 不应该到达这里(_shutdown 不返回)", flush=True)
    return 3


def run_under_pipe(mode: str) -> dict:
    """父进程:按 GUI 的方式用管道拉起子进程,测量退出与 EOF。"""
    cmd = [sys.executable, str(BASE / "_diag_exitlink.py"), "--child", "--mode", mode]
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    state = {"eof_at": None}
    keep = (
        "[child]", "[probe]", "[ctrl]",
        "脚本收尾", "游戏已退出", "已请求退出", "强制停止",
        "Ready to exit", "WARN", "ERROR", "Traceback",
    )

    def reader():
        for line in iter(proc.stdout.readline, ""):
            text = line.rstrip("\n")
            if any(mark in text for mark in keep):
                print("    | " + text, flush=True)
        state["eof_at"] = time.time() - t0

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(EOF_TIMEOUT_S)

    # EOF 到达 ≠ 进程已经回收,直接 poll 会误判成"还活着";先给它一点时间退干净。
    if not thread.is_alive():
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    result = {
        "eof_at": state["eof_at"],
        "eof_timeout": thread.is_alive(),
        "hung": proc.poll() is None,  # EOF 没到时进程是否还活着
        "elapsed": time.time() - t0,
        "returncode": proc.poll(),
        "killed": False,
    }
    if result["hung"]:
        subprocess.run(
            ["taskkill", "/f", "/t", "/pid", str(proc.pid)],
            capture_output=True,
        )
        time.sleep(0.5)
        result["killed"] = True
        result["returncode"] = proc.poll()
    return result


def report(title: str, res: dict) -> bool:
    print()
    print("  --- %s ---" % title)
    if res["eof_timeout"]:
        print("  管道 EOF      : 未在 %.0fs 内到达" % EOF_TIMEOUT_S)
    else:
        print("  管道 EOF      : %.2fs" % res["eof_at"])
    if res["hung"]:
        print("  进程状态      : %.0fs 后仍存活,已由诊断强制结束" % res["elapsed"])
    else:
        print("  进程状态      : 已自行退出")
    print("  退出码        : %s%s" % (res["returncode"], "(taskkill)" if res["killed"] else ""))
    print("  总耗时        : %.2fs" % res["elapsed"])
    ok = (not res["hung"]) and (not res["eof_timeout"])
    if ok:
        verdict = "通过 —— 进程自行退出, 管道按时 EOF(界面能复位)"
    elif res["hung"]:
        verdict = "失败 —— 进程卡住不退出(即「脚本仍处于挂机状态」)"
    else:
        verdict = "失败 —— 进程虽退出, 但管道一直被占着, 界面会永远停在「运行中」"
    print("  判定          : %s" % verdict)
    return ok


def main():
    if "--child" in sys.argv:
        if "--mode" in sys.argv:
            return run_child(sys.argv[sys.argv.index("--mode") + 1])
        return run_child("legacy" if "--legacy" in sys.argv else "new")

    print("=" * 72)
    print("退出联动诊断: 用 GUI 的方式拉起子进程, 看它能不能停干净")
    print("=" * 72)

    cases = [
        ("旧收尾 (mnt.stop + sys.exit)", "legacy"),
        ("旧收尾但跳过 mnt.stop (机制对照)", "legacy-nostop"),
        ("新收尾 (_shutdown: post_stop + 释放资源 + 硬退出)", "new"),
        ("新收尾 + 软路径失效 (看门狗兜底)", "watchdog"),
    ]
    results = []
    for index, (title, mode) in enumerate(cases, 1):
        print()
        print("[%d/%d] %s" % (index, len(cases), title))
        res = run_under_pipe(mode)
        results.append((title, report(title, res), res))

    print()
    print("=" * 72)
    for title, ok, res in results:
        print(
            "%-36s %-10s (EOF %s / 卡住 %s)"
            % (
                title[:36],
                "干净" if ok else "有问题",
                "%.2fs" % res["eof_at"] if res["eof_at"] is not None else "未到",
                "是" if res["hung"] else "否",
            )
        )
    print("=" * 72)
    new_cases_ok = all(ok for _, ok, _ in results[2:])
    return 0 if new_cases_ok else 1


if __name__ == "__main__":
    sys.exit(main())

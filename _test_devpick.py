# -*- coding: utf-8 -*-
"""多开设备识别逻辑的离线回归(_test_devpick.py)。

不需要真模拟器:直接构造探测结果,验证 _pick_device_by_probe 的判定分支。
跑法:.venv/Scripts/python.exe _test_devpick.py
"""
import sys

sys.path.insert(0, "src")

import autodori as A

PASS = 0
FAIL = 0


def P(addr, name="MuMu", installed=None, running=None, foreground=None):
    return {
        "address": addr, "name": name,
        "installed": installed, "running": running, "foreground": foreground,
    }


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-52s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-52s -> %s (want %s)" % (label, got, want))


def main():
    print("=== 1. 唯一装了邦邦 -> 自动选中 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True),
    ]
    check("只有一个 installed=True", A._pick_device_by_probe(probes), 1)

    print("=== 2. 多个都装了,但只有一个在运行 -> 选中运行的那个 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=True, running=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=True),
    ]
    check("按 running 收敛", A._pick_device_by_probe(probes), 1)

    print("=== 3. 多个都在运行,只有一个在前台 -> 选前台的 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=True, running=True, foreground=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=True, foreground=True),
    ]
    check("按 foreground 收敛", A._pick_device_by_probe(probes), 1)

    print("=== 4. 都装了也都开着(用户在两个实例里都登录了) -> 拒绝猜 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=True, running=True, foreground=True),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=True, foreground=True),
    ]
    check("无法唯一确定 -> -1", A._pick_device_by_probe(probes), -1)

    print("=== 5. 探测全失败(adb 抖动)== 绝不静默挑第一个 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=None),
        P("127.0.0.1:16416", "MuMu-1", installed=None),
    ]
    check("installed=None 不当作没装", A._pick_device_by_probe(probes), -1)

    print("=== 6. 明确都没装邦邦 -> -1(而不是选第一个) ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=False),
        P("127.0.0.1:16416", "MuMu-1", installed=False),
    ]
    check("全 False -> -1", A._pick_device_by_probe(probes), -1)

    print("=== 7. 显式指定优先于自动判定 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=True, running=True),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=True),
        P("127.0.0.1:16448", "MuMu-2", installed=True, running=True),
    ]
    check("按完整地址指定", A._pick_device_by_probe(probes, "127.0.0.1:16416"), 1)
    check("只给端口号", A._pick_device_by_probe(probes, "16448"), 2)
    check("只给序号", A._pick_device_by_probe(probes, "0"), 0)
    check("指定不存在的端口", A._pick_device_by_probe(probes, "99999"), -1)

    print("=== 8. 装了邦邦但没运行也算候选(用户常态) ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=False),
        P("127.0.0.1:16448", "MuMu-2", installed=True, running=False),
    ]
    # 两个候选都没跑、都不在前台 -> 无法确定,交回用户
    check("两个候选都在等用户", A._pick_device_by_probe(probes), -1)
    probes2 = [
        P("127.0.0.1:16384", "MuMu-0", installed=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=False),
    ]
    check("唯一候选即使没运行也选中", A._pick_device_by_probe(probes2), 1)

    print("=== 9. 候选清单渲染 ===")
    probes = [
        P("127.0.0.1:16384", "MuMu-0", installed=False),
        P("127.0.0.1:16416", "MuMu-1", installed=True, running=True, foreground=True),
        P("127.0.0.1:16448", "MuMu-2", installed=None),
    ]
    txt = A._format_device_choices(probes)
    print(txt)
    check("清单含三个实例", txt.count("MuMu-"), 3)
    check("空清单不炸", "没有枚举到" in A._format_device_choices([]), True)

    print("=== 10. 单设备场景不受影响 ===")
    probes = [P("127.0.0.1:16384", "MuMu-0", installed=True)]
    check("单设备直接选中", A._pick_device_by_probe(probes), 0)

    # === 11. 剔除 adb 连不上的候选(2026-10-09 实机 fatal 的修法) ===
    # MAA 会把「已创建但没启动」的 MuMu 实例也按 16384+32n 列进候选,那个地址
    # adb 连不上 → 探测全 None;还会把「只有一台真在跑」撑成「多实例」→ 进自动
    # 判定 → 真机一旦也探不出邦邦就整体 fatal。修法:先 adb connect 过滤。
    class FakeDev:
        def __init__(self, addr):
            self.address = addr
            self.adb_path = "adb.exe"

    real_connect = A._adb_connect
    try:
        A._adb_connect = lambda _adb, addr, **_kw: addr == "127.0.0.1:16384"

        devs = [FakeDev("127.0.0.1:16384"), FakeDev("127.0.0.1:16416")]
        kept = A._drop_unreachable_devices(devs)
        check("连不上的空壳实例被剔除", [d.address for d in kept], ["127.0.0.1:16384"])

        # 只剩一台 → 直接走 len==1 分支,不再 fatal(这就是那次报错的修法)
        kept = A._drop_unreachable_devices(devs)
        check("剔除后只剩一台可被直接采用", len(kept) == 1 and kept[0].address == "127.0.0.1:16384", True)

        # 单候选不做 connect(没有误判空间,也省一次启动等待)
        A._adb_connect = lambda *_a, **_kw: False
        one = [FakeDev("127.0.0.1:16384")]
        check("单候选原样返回", A._drop_unreachable_devices(one) is one, True)

        # 全部连不上 → 保留原表(adb 抖动时不能把唯一可用设备误删)
        A._adb_connect = lambda *_a, **_kw: False
        check("全连不上时保留原候选表", len(A._drop_unreachable_devices(devs)), 2)
    finally:
        A._adb_connect = real_connect

    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

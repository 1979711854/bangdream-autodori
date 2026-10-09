# -*- coding: utf-8 -*-
"""device_scan 的离线回归(_test_devscan.py)。

不依赖真实模拟器:把 adb / MuMuManager 的调用全部替换成可控的假实现,
覆盖「几开」「装没装邦邦」「在不在前台」「端口连不上」等组合。

跑法:.venv/Scripts/python.exe _test_devscan.py
"""
import io
import sys
import types

sys.path.insert(0, ".")

import device_scan as D

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


class FakeAdb:
    """模拟 adb.exe:online 是真实在线端口,installed/running/foreground 是各端口状态。"""

    def __init__(self, online, installed=(), running=(), foreground=(),
                 connectable=()):
        self.online = list(online)
        self.installed = set(installed)
        self.running = set(running)
        self.foreground = set(foreground)
        self.connectable = set(connectable)
        self.connected = []

    def devices(self, _adb):
        return list(self.online)

    def connect(self, _adb, address):
        self.connected.append(address)
        if address in self.connectable:
            return 0, "connected to %s" % address
        return 0, "cannot connect to %s: Connection refused" % address

    def shell(self, _adb, address, args, timeout=None):
        cmd = args[0] if args else ""
        if address not in self.online and address not in self.connectable:
            return None  # 设备不在线 -> 查不到
        if cmd == "pm":
            if address in self.installed:
                return "package:/data/app/com.bilibili.star.bili-1/base.apk\n"
            return ""
        if cmd == "pidof":
            return ("12345\n" if address in self.running else "")
        if cmd == "dumpsys":
            if address in self.foreground:
                return "  mCurrentFocus=Window{abc u0 com.bilibili.star.bili/com.x.MainActivity}"
            return "  mCurrentFocus=Window{def u0 com.other.game/com.y.MainActivity}"
        return None


def install_fakes(monkey_adb, monkey_manager=None):
    D.list_adb_devices = monkey_adb.devices

    # 真实实现的 _connect_addresses 返回**完整地址**,mock 必须对齐这个契约。
    # discover() 现在会在「MuMuManager 给了端口但 adb devices 没报」时主动补连,
    # 不 mock 掉的话测试会真的去起 adb 子进程。
    def fake_connect(_adb, addresses):
        out = []
        for addr in addresses:
            rc, text = monkey_adb.connect(None, addr)
            if rc == 0 and ("connected to" in text.lower() or "already" in text.lower()):
                out.append(addr)
        return out

    D._connect_addresses = fake_connect
    D._guess_ports = lambda adb, count=8: fake_connect(
        adb, ["127.0.0.1:%d" % p for p in D._MUMU_PORTS[:count]])
    # discover 内部经 _adb_shell 拿状态,必须一并替换,否则会真去调 adb
    D._adb_shell = monkey_adb.shell
    if monkey_manager is not None:
        D.list_mumu_instances = lambda m: monkey_manager
    else:
        D.list_mumu_instances = lambda m: None
    D.find_adb = lambda: "fake_adb.exe"
    D.find_mumu_manager = lambda: "fake_mgr.exe"


def main():
    print("=== 1. 三开:只有 16416 装了邦邦且在运行 ===")
    adb = FakeAdb(
        online=["127.0.0.1:16384", "127.0.0.1:16416", "127.0.0.1:16448"],
        installed=["127.0.0.1:16416"],
        running=["127.0.0.1:16416"],
        foreground=["127.0.0.1:16416"],
    )
    install_fakes(adb)
    res = D.discover()
    devs = res["devices"]
    check("发现 3 个实例", len(devs), 3)
    check("端口顺序正确", [d["port"] for d in devs], ["16384", "16416", "16448"])
    check("只有 16416 标记已装邦邦",
          [d["installed"] for d in devs], [False, True, False])
    check("只有 16416 标记运行中",
          [d["running"] for d in devs], [False, True, False])
    labels = [D.label_for(d) for d in devs]
    print("     下拉框标签示例:", labels[1])
    # 2026-10-09 用户要求:标签**只留「名称 · 端口」**,去掉尾部的
    # 「(装了邦邦、运行中、在前台)」——那段太长会把下拉框挤爆。
    # ⚠️ 「不显示」≠「不探测」:状态仍照实留在 dev 字典里,给状态行/预览用。
    check("标签含端口", labels[1].endswith("端口 16416"), True)
    check("标签不再带状态括号", "(" not in labels[1] and "（" not in labels[1], True)
    check("状态字眼不出现在标签里",
          any(w in labels[1] for w in ("装了邦邦", "未装邦邦", "运行中", "在前台")), False)
    check("状态仍在探测字段里(装了邦邦)", devs[1]["installed"], True)
    check("状态仍在探测字段里(运行中/在前台)",
          [devs[1]["running"], devs[1]["foreground"]], [True, True])
    check("未装邦邦同样只进字段", devs[0]["installed"], False)
    # 探测不可用(三个字段全 None)时更不能编造状态,只给名称 + 端口
    unknown = D.label_for({"name": "模拟器实例", "port": "16448",
                           "installed": None, "running": None, "foreground": None})
    check("状态未知时只给名称+端口", unknown, "模拟器实例 · 端口 16448")
    check("状态未知时不编造", any(w in unknown for w in ("装了邦邦", "未装邦邦", "运行中")), False)

    print("=== 2. 单开:最常见场景,不得退化 ===")
    adb = FakeAdb(online=["127.0.0.1:16384"], installed=["127.0.0.1:16384"],
                  running=["127.0.0.1:16384"])
    install_fakes(adb)
    check("发现 1 个", len(D.discover()["devices"]), 1)

    print("=== 3. 装了邦邦但没运行(用户先开模拟器再点开始) ===")
    adb = FakeAdb(online=["127.0.0.1:16384", "127.0.0.1:16416"],
                  installed=["127.0.0.1:16416"], running=[])
    install_fakes(adb)
    devs = D.discover()["devices"]
    check("未运行也算已装邦邦", devs[1]["installed"], True)
    check("运行中标记为 False", devs[1]["running"], False)
    check("标签不误报运行中", "运行中" in D.label_for(devs[1]), False)

    print("=== 4. adb 连不上任何实例 -> 空列表,绝不编造 ===")
    adb = FakeAdb(online=[], installed=[], running=[])
    install_fakes(adb)
    check("无设备 -> 空", D.discover()["devices"], [])

    print("=== 5. MuMuManager 给出实例名 -> 标签用真名 ===")
    adb = FakeAdb(online=["127.0.0.1:16384"], installed=["127.0.0.1:16384"])
    install_fakes(adb, monkey_manager=[
        {"index": 0, "name": "邦邦专用实例", "port": "16384", "started": True, "android": True},
    ])
    devs = D.discover()["devices"]
    check("用 MuMuManager 的名字", devs[0]["name"], "邦邦专用实例")
    check("标签含真名", "邦邦专用实例" in D.label_for(devs[0]), True)

    print("=== 6. 模拟器开着但 adb 未连上 -> connect 兜底能找到 ===")
    adb = FakeAdb(online=[], installed=["127.0.0.1:16384"],
                  running=["127.0.0.1:16384"],
                  connectable=["127.0.0.1:16384"])
    install_fakes(adb)
    devs = D.discover()["devices"]
    check("connect 兜底发现 1 个", len(devs), 1)
    check("并正确识别已装邦邦", devs[0]["installed"], True)

    print("=== 7. 设备离线 -> installed 为 None(不可用),不是 False ===")
    adb = FakeAdb(online=[], installed=[], running=[],
                  connectable=["127.0.0.1:16384"])
    install_fakes(adb)
    devs = D.discover()["devices"]
    # 探测不可用(installed/running/foreground 全 None)时,标签必须**只**给
    # 探测字段全为 None(查不到)时,标签必须只给名称 + 端口,
    # **不能编造状态** —— 查不到就承认查不到。
    for d in devs:
        if d.get("installed") is not None:
            continue  # 这台是真探测到了,状态该显示就显示(见上面的用例)
        lab = D.label_for(d)
        check("不可达时不编造状态(%s)" % lab[:18],
              any(w in lab for w in ("装了邦邦", "未装邦邦", "运行中")), False)
        check("不可达时标签含端口", d["port"] in lab, True)

    print("=== 7b. 实例名按端口匹配,不按下标(回归) ===")
    # 场景: MuMuManager 列了 3 个实例(含未启动的空壳),但 adb 只在线 2 台,
    # 且顺序与实例顺序**不一致**(雷电 5555 排在 MuMu 16384 前面)。
    # 旧实现按列表下标硬凑 -> 5555 会被贴上 index0 的名字(错配)。
    mgr = [
        {"index": 0, "name": "MuMu模拟器",   "port": "16384", "started": True,  "android": True},
        {"index": 1, "name": "空壳未初始化",  "port": "",       "started": False, "android": False},
        {"index": 2, "name": "雷电模拟器",   "port": "5555",  "started": True,  "android": True},
    ]
    adb2 = FakeAdb(online=["127.0.0.1:16384", "127.0.0.1:5555"],
                   installed=["127.0.0.1:16384"])
    install_fakes(adb2, mgr)
    got = D.discover()["devices"]
    byp = {d["port"]: d["name"] for d in got}
    print("     端口->名称:", byp)
    check("16384 拿到 MuMu 的名字", byp.get("16384"), "MuMu模拟器")
    check("5555 拿到雷电的名字(不是下标硬凑)", byp.get("5555"), "雷电模拟器")
    check("在线设备数 = 2", len(got), 2)
    check("未初始化的空实例没混进列表", "空壳未初始化" not in [d["name"] for d in got], True)
    # 端口缺失时必须用中性名,不能猜
    mgr2 = [{"index": 0, "name": "某实例", "port": "", "started": True, "android": True}]
    adb3 = FakeAdb(online=["127.0.0.1:16448"], installed=[])
    install_fakes(adb3, mgr2)
    only = D.discover()["devices"][0]
    print("     端口缺失时:", only["name"])
    check("端口缺失时用中性名而非贴错", only["name"], "模拟器实例 16448")

    print("=== 7c. 影子端口去重(回归) ===")
    # 实测:两个 MuMu 实例 -> adb devices 报 4 条(16384/16416 是真身,
    # 5555/5557 是历史残留端口,android_id 与真身完全相同)。
    # 不去重的话用户会看到 4 个选项,以为要选 4 次,还可能连错窗口。
    fps = {
        "127.0.0.1:16384": "dev-A", "127.0.0.1:5555": "dev-A",   # 同一台
        "127.0.0.1:16416": "dev-B", "127.0.0.1:5557": "dev-B",   # 同一台
    }
    D._adb_shell = lambda adb, addr, args, timeout=None: (
        fps.get(addr, "") if args[:2] == ["settings", "get"] else ""
    )
    mgr = [
        {"index": 0, "name": "MuMu模拟器", "port": "16384", "started": True, "android": True},
        {"index": 1, "name": "MuMu安卓设备-1", "port": "16416", "started": True, "android": True},
    ]
    adb4 = FakeAdb(online=list(fps.keys()), installed=["127.0.0.1:16384"])
    install_fakes(adb4, mgr)
    D._device_fingerprint = lambda adb, addr: fps.get(addr, "")
    got = D.discover()["devices"]
    ports = [d["port"] for d in got]
    print("     去重后端口:", ports)
    check("4 条 adb 记录去重成 2 台", len(got), 2)
    check("保留的是 MuMu 官方端口", ports, ["16384", "16416"])
    check("名字正确对应", [d["name"] for d in got], ["MuMu模拟器", "MuMu安卓设备-1"])

    print("=== 7d. 真指纹取不到时不去重(宁可多列也不误删) ===")
    D._device_fingerprint = lambda adb, addr: ""
    got2 = D.discover()["devices"]
    check("取不到指纹 -> 原样保留全部", len(got2), 4)

    print("=== 7e. MuMuManager 有端口但 `adb devices` 没报 -> 必须主动补连(回归) ===")
    # 用户 10-09 实测:两个 MuMu 实例的安卓都已启动完成,`adb devices` 却只报
    # `emulator-5554`(MuMu 的 16384/16416 **不会自动注册**)→ 旧实现只在
    # ports 为空时才猜端口,而 emulator-5554 已经占住 ports → 另一台永远发现不了,
    # 表现就是「GUI 只找到一个游戏窗口」。
    mgr = [
        {"index": 0, "name": "MuMu模拟器", "port": "16384", "started": True, "android": True},
        {"index": 1, "name": "MuMu安卓设备-1", "port": "16416", "started": True, "android": True},
    ]
    adb5 = FakeAdb(online=["emulator-5554"],
                   installed=["127.0.0.1:16384"],
                   running=["127.0.0.1:16384"],
                   connectable=["127.0.0.1:16384", "127.0.0.1:16416"])
    install_fakes(adb5, mgr)
    # emulator-5554 与 16384 是同一台(实测 android_id/boot_id 相同)→ 让指纹相同
    D._device_fingerprint = lambda adb, addr: (
        "dev-A" if addr in ("127.0.0.1:16384", "emulator-5554") else "")
    got5 = D.discover()["devices"]
    ports5 = sorted(d["port"] for d in got5)
    print("     端口:", ports5)
    check("按 MuMuManager 端口补连 -> 两台都出来", ports5, ["16384", "16416"])
    check("补连的那两台都真去 connect 了",
          sorted(set(adb5.connected))[:2], ["127.0.0.1:16384", "127.0.0.1:16416"])
    check("有邦邦那台仍照实标 installed",
          [d["installed"] for d in got5 if d["port"] == "16384"], [True])

    print("=== 8. JSON 可序列化(命令行调试/日志用) ===")
    import json
    adb = FakeAdb(online=["127.0.0.1:16384"], installed=["127.0.0.1:16384"])
    install_fakes(adb)
    res = D.discover()
    try:
        json.loads(D.to_json(res))
        check("to_json 可往返", True, True)
    except Exception as e:
        check("to_json 可往返", str(e), True)

    print("=== 9. 标签在极端情况下不炸 ===")
    for dev in [
        {"name": "x", "port": "1"},
        {"name": "x", "port": "1", "installed": None},
        {"name": "x", "port": "1", "installed": True, "running": None,
         "foreground": None},
    ]:
        try:
            s = D.label_for(dev)
            check("label_for(%s) 有输出" % dev.get("port"), bool(s), True)
        except Exception as e:
            check("label_for 抛异常", str(e), True)

    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

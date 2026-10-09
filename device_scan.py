# -*- coding: utf-8 -*-
"""GUI 侧的模拟器实例发现（不依赖 MaaFramework / 打包产物）。

为什么单独一个模块:打包的 autodori_gui.exe **不含** maa/bin(见 autodori_gui.spec
的 datas 为空),所以 GUI 没法 import maa.toolkit 去枚举设备;而 bot 侧
`import autodori` 又会在模块级拉 Bestdori 曲库(离线直接崩),也不适合拿来
当探测工具。所以 GUI 用最朴素的办法:找到 adb.exe,问它有哪些设备。

与 bot 侧 `_probe_device` 的判据保持一致(pm path / pidof),两边看到的是
同一套事实,不会出现"下拉框里选了 A、bot 却认为该选 B"的错位。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import threading

# 邦邦国服包名 —— 与 src/autodori.py 的 GAME_PACKAGE 必须一致
GAME_PACKAGE = "com.bilibili.star.bili"

# MuMu 12 多开:第一个实例 16384,之后每个 +32;雷电是 5555 往后 +2。
# 这里只作为"用户没装 adb 时的兜底猜测",真正认设备靠 pm path。
_MUMU_PORTS = [16384 + 32 * i for i in range(8)]
_LD_PORTS = [5555 + 2 * i for i in range(8)]

_TIMEOUT = 6.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# MuMu / 雷电的常见安装位置。含 32 位与注册表风格路径,覆盖不同安装选择。
_ADB_CANDIDATES = (
    r"D:\MuMu Player 12\nx_main\adb.exe",
    r"D:\Program Files\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"D:\leidian\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"D:\Program Files\LDPlayer\LDPlayer9\adb.exe",
)

# 从旧日志里捞 adb 路径(上次成功跑过必有)
_LOG_ADB_RE = re.compile(r"adb 路径: (.+?)\s*$", re.MULTILINE)


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _run(args, timeout=_TIMEOUT):
    """跑一条命令,返回 (returncode, stdout)。失败返回 (None, "")。"""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            # 必须显式给编码:中文 Windows 的默认编码是 GBK,adb 输出里的
            # UTF-8 字节(设备名、中文路径)会让它抛 UnicodeDecodeError,
            # 表现为 GUI 启动时刷一屏 traceback(打包态才有日志所以看得见)。
            # errors="replace" 兜底:解码失败也要把输出拿回来,只是尾部
            # 可能有乱码,远好过整个调用失败。
            encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return None, ""
    return proc.returncode, proc.stdout or ""


def _adb_from_logs(base: str):
    """从最近几次运行日志里恢复 adb 路径 —— 最可靠,顺带尊重用户上次的选择。"""
    logs = sorted(glob.glob(os.path.join(base, "debug", "autodori-*.log")),
                  key=os.path.getmtime, reverse=True)[:5]
    for path in logs:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = _LOG_ADB_RE.findall(f.read())
        except Exception:
            continue
        for cand in reversed(m):
            if cand and os.path.isfile(cand):
                return cand
    return None


def _adb_from_windows_dir() -> str:
    """扫 MuMu / 雷电的安装根目录,拼出 adb.exe 路径。"""
    roots = ("C:\\", "D:\\", "E:\\")
    patterns = (
        "MuMu*/*/adb.exe", "MuMu*/nx_main/adb.exe", "MuMu*/shell/adb.exe",
        "MuMuPlayer*/nx_main/adb.exe", "MuMuPlayer*/shell/adb.exe",
        "Netease/MuMu*/shell/adb.exe", "leidian/LDPlayer*/adb.exe",
    )
    for root in roots:
        for pat in patterns:
            try:
                hits = glob.glob(os.path.join(root, pat))
            except Exception:
                continue
            for hit in sorted(hits):
                if os.path.isfile(hit):
                    return hit
    return None


def find_adb(override: str = "") -> str:
    """定位一个可用的 adb.exe。找不到返回空串。

    override 非空(用户在 GUI 里手填)时直接用它 —— 自动发现覆盖不到的情况
    (装在非默认目录、便携版、被安全软件隔离)必须留个手动出口,
    否则用户就只能干瞪眼。
    """
    manual = str(override or "").strip()
    if manual:
        if os.path.isfile(manual):
            return manual
        # 允许只填目录:补上该目录下的常见子路径
        base = manual if os.path.isdir(manual) else os.path.dirname(manual)
        for sub in ("nx_main", "shell", "."):
            for name in ("adb.exe", "adb"):
                cand = os.path.normpath(os.path.join(base, sub, name))
                if os.path.isfile(cand):
                    return cand
        return ""  # 手填的路径不对,不要退回自动 —— 静默换一条用户不会知道
    cached = _adb_from_logs(_base_dir())
    if cached:
        return cached
    for cand in _ADB_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    found = _adb_from_windows_dir()
    if found:
        return found
    # 最后试 PATH 上的 adb
    for name in ("adb", "adb.exe"):
        rc, out = _run(["where", name], timeout=3)
        if rc == 0 and out.strip():
            first = out.strip().splitlines()[0].strip()
            if os.path.isfile(first):
                return first
    return ""


def find_mumu_manager() -> str:
    """定位 MuMuManager.exe —— 枚举「有几个实例」最权威的数据源。"""
    for cand in (
        r"D:\MuMu Player 12\nx_main\MuMuManager.exe",
        r"D:\MuMu Player 12\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
        r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
    ):
        if os.path.isfile(cand):
            return cand
    for root in ("C:\\", "D:\\", "E:\\"):
        for pat in ("MuMu*/nx_main/MuMuManager.exe", "MuMu*/MuMuManager.exe",
                    "Netease/MuMu*/shell/MuMuManager.exe"):
            try:
                for hit in sorted(glob.glob(os.path.join(root, pat))):
                    if os.path.isfile(hit):
                        return hit
            except Exception:
                continue
    return ""


def list_mumu_instances(manager: str) -> list:
    """用 MuMuManager 列出本机 MuMu 实例(多开的真实数量)。

    与 MAA 内部用的完全是同一套命令(MAA 的 find_mumu_serials 也走
    MuMuManager),所以 GUI 看到的实例集合与 bot 侧一致。
    返回 [{"index", "name", "port", "started", "android"}];
    `port` 是权威的 adb 端口 —— discover() 靠它把实例名对上正确的设备,
    不用列表下标(下标对齐在多开/多模拟器混跑时会错配)。
    找不到 manager 或全部查询失败时返回 None(表示"查不到",不是"没有实例")。
    """
    if not manager:
        return None
    found = []
    # 用 `info -v all` 一次拿全部实例,而不是逐个 `--vmindex N`:
    #   · N 次进程启动 + N 次 RPC ≈ 几秒,一次只要几百毫秒;
    #   · 单查(`--vmindex N`)在实例**未启动时不返回 adb_port**,字段不全;
    #     `-v all` 在实例启动后字段完整(实测对比过两种输出)。
    try:
        proc = subprocess.run(
            [manager, "info", "-v", "all"],
            capture_output=True, text=True, timeout=_TIMEOUT * 3,
            # 同 _run:显式 UTF-8 + replace,否则实例名含中文时会解码失败
            encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    try:
        allinfo = json.loads(proc.stdout or "")
    except Exception:
        return None
    if not isinstance(allinfo, dict):
        return None
    # 输出形如 {"0": {...}, "1": {...}} —— 按 index 排,保证顺序稳定。
    for key in sorted(allinfo, key=lambda k: str(k)):
        data = allinfo[key]
        if not isinstance(data, dict) or data.get("errcode"):
            continue
        idx = data.get("index", key)
        try:
            idx = int(idx)
        except Exception:
            continue
        found.append({
            "index": int(data.get("index", idx)),
            "name": str(data.get("name") or ("MuMu模拟器 %s" % idx)),
            # adb_port 在 info 输出里是权威端口;取不到就留空,让调用方退回
            # index 匹配 —— **不要**用"16384+32*index"反推,不同版本/渠道
            # 不保证符合那个规律,猜出来的端口会把名字贴到错误的设备上。
            "port": str(data.get("adb_port") or ""),
            "started": bool(data.get("is_process_started")),
            "android": bool(data.get("is_android_started")),
        })
    return found or None


def _adb_shell(adb: str, address: str, args, timeout=_TIMEOUT):
    """在指定 address 上执行 shell 命令。返回 str 或 None(查不到)。"""
    if not adb or not address:
        return None
    rc, out = _run([adb, "-s", address, "shell"] + list(args), timeout=timeout)
    if rc is None:
        return None
    # rc=1 往往是「包不存在」这类正常否定答案;真错会带 stderr,
    # 但这里只取 stdout,拿不到就当查不到 —— 语义与 bot 侧一致。
    return out


def _describe(adb: str, address: str) -> dict:
    """探测单个设备的邦邦安装/运行状态。"""
    info = {
        "address": address,
        "installed": None,
        "running": None,
        "foreground": None,
    }
    out = _adb_shell(adb, address, ["pm", "path", GAME_PACKAGE])
    if out is not None:
        info["installed"] = GAME_PACKAGE in out
    out = _adb_shell(adb, address, ["pidof", GAME_PACKAGE])
    if out is not None:
        info["running"] = bool(out.strip())
    out = _adb_shell(adb, address, ["dumpsys", "window", "windows"])
    if out is None:
        out = _adb_shell(adb, address, ["dumpsys", "window"])
    if out is not None:
        for line in out.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                info["foreground"] = GAME_PACKAGE in line
                break
    return info


def list_adb_devices(adb: str) -> list:
    """列出当前 adb 已知的所有在线设备地址。"""
    if not adb:
        return []
    try:
        proc = subprocess.run(
            [adb, "devices"], capture_output=True, text=True,
            timeout=_TIMEOUT,
            # 同 _run:adb 输出可能含 UTF-8,中文 Windows 默认 GBK 会解码失败
            encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    out = []
    for line in (proc.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def _connect_addresses(adb: str, addresses: list) -> list:
    """并发 `adb connect` 一批地址,返回**真的连上**的那些(保持输入顺序)。

    判据与 _guess_ports 一致:`adb connect` 对没在跑的实例也可能 rc=0,
    必须看输出文本里的 connected/already。

    ⚠️ **为什么必须显式 connect**:MuMu 的 `16384/16416` **不会自动出现在
    `adb devices` 里** —— 2026-10-09 实测,两个实例的安卓都已启动完成,
    `adb devices` 却只报一条 `emulator-5554`;不主动 connect,那两个实例就
    永远发现不了(这正是用户报的「GUI 只找到一个实例」的根因)。

    必须并发:adb connect 连一个**不存在的**端口要等 TCP 超时(实测单个约 2s,
    串行 8 个就是 16 秒,用户点一次「刷新」要干等)。并发后总耗时约等于最慢的那个。
    """
    if not adb or not addresses:
        return []
    found = []
    lock = threading.Lock()

    def probe(address):
        rc, out = _run([adb, "connect", address], timeout=6)
        if rc != 0:
            return
        text = (out or "").lower()
        if "connected to" in text or "already" in text:
            with lock:
                found.append(address)

    threads = []
    for addr in addresses:
        t = threading.Thread(target=probe, args=(addr,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=10)
    return [a for a in addresses if a in found]


def _guess_ports(adb: str, count: int = 8) -> list:
    """按 MuMu / 雷电的端口规律试 connect,只返回**真的连上**的端口。

    MuMu 12 多开实例通常在 16384 + 32n,但不同版本/渠道端口并不保证一致,
    所以这里把规律只当"候选",用 connect 的真实回执做筛选。候选按端口升序
    返回,保证下拉框里端口是升序(用户预期)。
    """
    candidates = ["127.0.0.1:%d" % p
                  for p in (_MUMU_PORTS[:count] + _LD_PORTS[:count])]
    return _connect_addresses(adb, candidates)


def _device_fingerprint(adb: str, address: str) -> str:
    """取设备内唯一标识,用来判断两个端口是不是**同一台**设备。

    动机(实测踩到):开了两个 MuMu 实例后,`adb devices` 会报**四条**:
        127.0.0.1:16384  127.0.0.1:16416  127.0.0.1:5555  127.0.0.1:5557
    但 5555/5557 是 MuMu 早期用的端口,adb 连接记录没被清理,重新 connect
    后又冒出来 —— 实测 `settings get secure android_id` 与
    `/proc/sys/kernel/random/boot_id` 显示 5555 与 16384 **完全相同**,
    5557 与 16416 也相同,也就是**只有 2 台设备却出现 4 个选项**。

    这些"影子"必须去掉,否则用户会以为要选 4 次,而且可能连错窗口。
    返回空串表示取不到(那就不去重,宁可多列也不误删)。
    """
    for args in (["settings", "get", "secure", "android_id"],
                 ["cat", "/proc/sys/kernel/random/boot_id"]):
        out = _adb_shell(adb, address, args)
        val = (out or "").strip()
        if val and val not in ("null", ""):
            return val
    return ""


def _dedupe_same_device(adb: str, devices: list, official_ports: set) -> list:
    """同一台设备被多个端口连上时只保留一个。

    保留规则:优先留 MuMuManager 认可的端口(实例真身),否则留端口号最小的
    —— 目的是让下拉框里的端口与用户预期(MuMu 的 16384/16416)一致,
    而不是留下一串看着莫名其妙的 5555。
    """
    by_fp, kept, dropped = {}, [], []
    for dev in devices:
        fp = _device_fingerprint(adb, dev["address"])
        if not fp:
            kept.append(dev)
            continue
        if fp not in by_fp:
            by_fp[fp] = dev
            kept.append(dev)
            continue
        # 撞上了:按规则挑一个留下
        win = by_fp[fp]
        win_is_official = win["port"] in official_ports
        dev_is_official = dev["port"] in official_ports
        if dev_is_official and not win_is_official:
            kept[kept.index(win)] = dev
            by_fp[fp] = dev
            dropped.append(win)
        else:
            dropped.append(dev)
    return kept


def _describe_all(adb: str, addresses: list) -> list:
    """并发探测一批设备。单个失败不影响其他,返回顺序与输入一致。

    每个设备要跑 3 条 adb shell(往返各几百毫秒),串行的话多开时
    十几秒起步;并发后总耗时约等于最慢的那一个。
    """
    results = [None] * len(addresses)

    def work(i, addr):
        try:
            results[i] = _describe(adb, addr)
        except Exception:
            results[i] = {
                "address": addr, "installed": None,
                "running": None, "foreground": None,
            }

    threads = []
    for i, addr in enumerate(addresses):
        t = threading.Thread(target=work, args=(i, addr), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=_TIMEOUT * 3 + 5)
    for i, addr in enumerate(addresses):
        if results[i] is None:
            results[i] = {
                "address": addr, "installed": None,
                "running": None, "foreground": None,
            }
    return results


def discover(adb: str = "", manager: str = "") -> dict:
    """发现可用的模拟器实例。返回 {adb, manager, devices:[...]}。

    数据来源是两条腿:
      · MuMuManager —— 权威的「本机有几个 MuMu 实例 / 分别叫什么」;
      · adb —— 权威的「哪些实例在线、装了邦邦没、在前台没」。

    devices 每项: {address, port, index, name, installed, running, foreground}
    installed/running/foreground 为 True/False/None(查不到)。
    """
    adb = adb or find_adb()
    manager = manager or find_mumu_manager()
    if not adb:
        return {"adb": "", "manager": manager, "devices": []}

    # adb 自己认识的设备(最可信:能列出来就说明在线)
    online = list_adb_devices(adb)
    local = [a for a in online
             if a.startswith("127.0.0.1:") or a.startswith("emulator-")]

    instances = list_mumu_instances(manager)
    # 端口候选 = adb 已在线的 + (必要时)按规律试连上的。
    # 猜端口很慢(每个 connect 一次 adb 往返),所以只有在 adb 一个设备都
    # 报不出来时才猜,且按已知实例数收敛 —— 否则「没开模拟器」这个最常见的
    # 情况也要白等十几秒。
    ports = list(local)
    # ⚠️ MuMu 的 16384/16416 不会自动进 `adb devices`(实测:两个实例都起着,
    # adb 只报 emulator-5554) → 必须按 MuMuManager 给的**权威端口**主动 connect,
    # 否则多开的其余实例永远发现不了。原来只在 ports 为空时才猜端口,而
    # emulator-5554 已经把 ports 占住 → 猜都不猜(用户报的「只找到一个实例」根因)。
    declared = sorted({"127.0.0.1:%s" % i["port"]
                       for i in (instances or []) if i.get("port")})
    missing = [a for a in declared if a not in ports]
    if missing:
        for addr in _connect_addresses(adb, missing):
            if addr not in ports:
                ports.append(addr)
    if not ports:
        guess_count = min(len(instances), 8) if instances else 2
        for addr in _guess_ports(adb, guess_count):
            if addr not in ports:
                ports.append(addr)

    # 实例名要**按端口/index 对应**,不能用列表下标硬凑。
    # 反例(实测会遇到):adb 已在线的设备顺序与 MuMuManager 返回的实例顺序
    # 未必一致 —— 例如 5555(雷电)排在 16384(MuMu)前面,下标对齐就会把
    # 雷电那台显示成 MuMu 的名字。多开时这种错配会让用户选错实例。
    by_port = {}
    for inst in (instances or []):
        if inst.get("port"):
            by_port.setdefault(inst["port"], inst)

    devices = []
    infos = _describe_all(adb, ports)
    for i, addr in enumerate(ports):
        info = infos[i]
        port = addr.split(":")[-1] if ":" in addr else addr
        name = "模拟器实例 %s" % port
        # 实例名只认端口精确匹配。**不要**退回按列表下标硬凑 ——
        # MuMuManager 列出的是「全部已创建的实例」,含未启动/未初始化的空壳;
        # 而 ports 来自「当前在线的 adb 设备」。两者长度和顺序都不保证一致,
        # 下标对齐会把名字贴到错误的设备上(实测本机就有一个 index=1 的
        # 空实例,disk_size=0,从未启动)。匹配不上就用中性名,宁可信息少
        # 也不能贴错 —— 用户据此判断连哪台,贴错等于连错窗口。
        inst = by_port.get(str(port))
        if inst is not None:
            name = inst["name"]
        devices.append({
            "address": addr,
            "port": str(port),
            "index": i,
            "name": name,
            "installed": info["installed"],
            "running": info["running"],
            "foreground": info["foreground"],
        })
    # 同一台设备常会被多个端口连上(实测:两个 MuMu 实例 → adb 报 4 条,
    # 其中 5555/5557 是影子端口,android_id 与 16384/16416 相同)。
    # 不去掉的话用户会看到 4 个选项、以为要选 4 次,还可能连错窗口。
    if len(devices) > 1:
        official = set()
        for inst in (instances or []):
            if inst.get("port"):
                official.add(inst["port"])
        devices = _dedupe_same_device(adb, devices, official)
    return {"adb": adb, "manager": manager, "devices": devices}


def label_for(dev: dict) -> str:
    """给下拉框生成标签：**只留「实例名 · 端口」**。

    2026-10-09 用户要求去掉尾部的「(装了邦邦、运行中、在前台)」——那段太长，把
    下拉框挤爆（实测「模拟器实例名 · 端口 16384(装了邦邦、运行中、在前台)」远超框宽）。

    注意**只是不显示，不是不探测**：`installed` / `running` / `foreground` 仍照实
    探测并留在 `dev` 里，供状态行与「预览」按钮使用（`discover()` 的返回值不变）。
    该选哪台本来也不靠这句话 —— 多开时靠端口 + 「预览」逐个看画面。
    """
    name = dev.get("name") or "模拟器实例"
    port = dev.get("port") or "?"
    return "%s · 端口 %s" % (name, port)


def capture(adb: str, address: str, timeout: float = 12.0):
    """抓某个实例的屏幕并存成 PNG,返回文件路径;失败返回 None。

    用途:多开邦邦窗口时,包名和进程状态完全分不出彼此,必须让用户**亲眼**
    确认这个端口对应的是不是自己要的窗口(在不在选曲页/是不是目标账号)。
    """
    if not adb or not address:
        return None
    import tempfile
    try:
        proc = subprocess.run(
            [adb, "-s", address, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    data = proc.stdout or b""
    # PNG magic。缺了说明设备没响应(返回的其实是错误文本)
    if len(data) < 8 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    path = os.path.join(tempfile.gettempdir(),
                        "autodori_dev_%s.png" % address.replace(":", "_").replace("-", "_"))
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        return None
    return path


def to_json(result: dict) -> str:
    """给 GUI 与命令行调试用。"""
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(to_json(discover()))

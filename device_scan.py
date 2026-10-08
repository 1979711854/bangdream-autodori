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
    返回 [{"index": int, "name": str, "started": bool, "android": bool}];
    找不到 manager 或全部查询失败时返回 None(表示"查不到",不是"没有实例")。
    """
    if not manager:
        return None
    found = []
    for idx in range(16):  # MuMu 最多 16 开,超出的直接停
        try:
            proc = subprocess.run(
                [manager, "info", "--vmindex", str(idx)],
                capture_output=True, text=True, timeout=_TIMEOUT,
                # 同 _run:显式 UTF-8 + replace,否则实例名含中文时会解码失败
                encoding="utf-8", errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception:
            break
        try:
            data = json.loads(proc.stdout or "")
        except Exception:
            break
        if not isinstance(data, dict) or data.get("errcode"):
            break  # -200 = player index not found,后面没有了
        if "index" not in data:
            break
        found.append({
            "index": int(data.get("index", idx)),
            "name": str(data.get("name") or ("MuMu模拟器 %s" % idx)),
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


def _guess_ports(adb: str, count: int = 8) -> list:
    """按 MuMu / 雷电的端口规律试 connect,只返回**真的连上**的端口。

    MuMu 12 多开实例通常在 16384 + 32n,但不同版本/渠道端口并不保证一致,
    所以这里把规律只当"候选",用 connect 的真实回执做筛选 ——
    adb connect 对没在跑的实例也可能 rc=0,必须看输出文本。

    必须并发:adb connect 连一个**不存在的**端口要等 TCP 超时(实测单个约
    2s,串行 8 个端口就是 16 秒,用户点一次「刷新」要干等)。并发后总耗时
    等于最慢的那一个,退出关窗时也不会被拖住。
    """
    candidates = _MUMU_PORTS[:count] + _LD_PORTS[:count]
    found = []
    lock = threading.Lock()

    def probe(port):
        address = "127.0.0.1:%d" % port
        rc, out = _run([adb, "connect", address], timeout=6)
        if rc != 0:
            return
        text = (out or "").lower()
        if "connected to" in text or "already" in text:
            with lock:
                found.append(address)

    threads = []
    for port in candidates:
        t = threading.Thread(target=probe, args=(port,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=10)
    # 按候选顺序返回,保证下拉框里端口是升序(用户预期)
    return [a for a in ("127.0.0.1:%d" % p for p in candidates) if a in found]

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
    if not ports:
        guess_count = min(len(instances), 8) if instances else 2
        for addr in _guess_ports(adb, guess_count):
            if addr not in ports:
                ports.append(addr)

    devices = []
    infos = _describe_all(adb, ports)
    for i, addr in enumerate(ports):
        info = infos[i]
        port = addr.split(":")[-1] if ":" in addr else addr
        name = "模拟器实例 %s" % port
        # 有 MuMuManager 的权威数据就换成它的实例名
        try:
            if instances and i < len(instances):
                name = instances[i]["name"]
        except Exception:
            pass
        devices.append({
            "address": addr,
            "port": str(port),
            "index": i,
            "name": name,
            "installed": info["installed"],
            "running": info["running"],
            "foreground": info["foreground"],
        })
    return {"adb": adb, "manager": manager, "devices": devices}


def label_for(dev: dict) -> str:
    """给下拉框生成标签。

    **刻意不写「已装邦邦 / 这是邦邦」之类的断言** —— 实测多开场景下
    MuMu 往往克隆实例镜像，每个实例都装了同一个游戏，`pm path` / `pidof`
    对它们返回完全一样的结果,据此宣称"哪个是邦邦"是**误导**。
    这里只给用户能自己核对的事实:实例名(来自 MuMuManager)+ 端口/序号,
    外加「在前台」这种一眼可验证的状态(用户看屏幕就知道对不对)。
    """
    name = dev.get("name") or "模拟器实例"
    port = dev.get("port") or "?"
    tags = []
    if dev.get("foreground") is True:
        tags.append("在前台")
    if not tags:
        return "%s · 端口 %s" % (name, port)
    return "%s · 端口 %s(%s)" % (name, port, "、".join(tags))


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

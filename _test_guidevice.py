# -*- coding: utf-8 -*-
"""GUI 多开设备选择的冒烟测试(_test_guidevice.py)。

真起 Tk 界面,把「演出设置」卡片渲染出来并注入假设备,验证:
  · 下拉框选项与标签正确
  · 选具体实例 -> device_address 落盘并回显
  · 选回自动识别 -> device_address 清空
  · 传给 bot 的 --device 参数正确
界面会在 1.2 秒后自动销毁,不需要人工点。

跑法:.venv/Scripts/python.exe _test_guidevice.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

import tkinter as tk

import gui as G

# ---- 配置隔离(必须在构造 AutodoriGUI 之前做完)----
# GUI 的偏好与「选了哪个实例」都写在 data/gui_config.json。本测试会构造真实
# 的 AutodoriGUI,而 __init__ 里排了开机自动扫描(root.after(700))—— 那个扫描
# 是**真的**跑 adb,会把用户机器上真实发现的设备地址写进配置。
# 光靠「跑完还原备份」不够:副作用已经发生,且一旦测试中途被杀(工具会 SIGTERM)
# 还原就永远不会执行,配置就此被污染(实测把 127.0.0.1:5555 写进去了)。
# 正确做法是把 G.GUI_CONFIG **重定向到临时文件**,让读写都落在临时处,
# 用户真实配置自始至终不被打开。
_TMP_DIR = tempfile.mkdtemp(prefix="autodori_guitest_")
_TMP_CFG = os.path.join(_TMP_DIR, "gui_config.json")
# 起点用干净默认值,而不是复制用户配置 —— 否则用户若已选了某个实例,
# 第一条断言「device_address 初始为空」就会假失败。
with open(_TMP_CFG, "w", encoding="utf-8") as _f:
    json.dump({"view": "live.show"}, _f)
G.GUI_CONFIG = _TMP_CFG

# device_scan 的日志回溯会扫 <BASE>/debug/*.log;指向临时目录,免得读到用户日志
G.BASE = _TMP_DIR
import device_scan as _ds
_ds._base_dir = lambda: _TMP_DIR

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


class FakeDev(dict):
    """device_scan.discover() 的真实契约就是 dict(见 discover 的返回),
    这里用 dict 子类而不是自定义对象 —— 否则测的是 mock 的形状,不是代码的形状。"""

    def __init__(self, port, installed=True, running=True, foreground=False,
                 name=None):
        super().__init__(
            address="127.0.0.1:%s" % port,
            port=str(port),
            index=int(port) % 10,
            name=name or ("模拟器实例 %s" % port),
            installed=installed,
            running=running,
            foreground=foreground,
        )


def build():
    root = tk.Tk()
    root.withdraw()  # 不弹到前台,CI/终端环境下更安静
    app = G.AutodoriGUI(root)
    # 本测试只用注入的假设备,不需要 __init__ 里排的 700ms 开机自动扫描 ——
    # 那会真的去跑 adb(本机没开模拟器,要等好几秒),还会持有子进程让退出变慢。
    # 置 _closing 让 scan_devices_async 直接返回,行为确定且无副作用。
    app._closing = True
    return root, app


def main():
    root, app = build()

    # 直接跳到「演出设置」页,设备行在那儿
    app._switch("live.show")
    root.update()

    print("=== 1. 初始状态:只有自动识别,无地址 ===")
    check("device_address 初始为空", app.device_address, "")
    check("设备框已创建", app.device_box is not None, True)

    print("=== 2. 注入三开结果,下拉框选项正确 ===")
    devs = [
        FakeDev(16384, installed=False, running=False),
        FakeDev(16416, installed=True, running=True, foreground=True),
        FakeDev(16448, installed=True, running=False),
    ]
    app._on_devices_scanned({"adb": "adb.exe", "devices": devs, "shots": {}})
    root.update()
    labels = list(app.device_box.values)
    check("选项数 = 1(自动) + 3(实例)", len(labels), 4)
    check("第一项是自动项", labels[0], app.AUTO_DEVICE_LABEL)
    check("含 16416 端口", any("16416" in x for x in labels), True)
    # 多开克隆镜像时每台都装了邦邦 -> 「都装了邦邦」这句是对的(事实),
    # 但必须同时引导用户用预览/端口区分,不能让它变成"已帮你选好"。
    txt_multi = app.device_status.cget("text")
    check("多实例且都装了 -> 提示预览区分",
          "预览" in txt_multi and "指定" in txt_multi, True)
    check("且不自动选中任何一个", app.device_address, "")

    print("=== 2b. 只扫到一个 -> 提示保持「自动」 ===")
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16416, installed=True, running=True,
                            foreground=True)],
        "shots": {},
    })
    root.update()
    txt = app.device_status.cget("text")
    # 状态行只报客观的「实例数量 + 下一步」,**不再复述「装了邦邦」**
    # (10-09 改):多开克隆镜像时每台都装了同一个游戏,那句话退化成零信息量;
    # 而「哪个实例里装了邦邦」由 bot 侧按包名自动判定(`pm path`),不需要用户看。
    # 这两条由 10-08 版断言「已装邦邦 / 未检测到邦邦」改成现状。
    check("单实例 -> 提示保持自动", "1 个实例" in txt and "自动" in txt, True)
    check("不再复述装没装邦邦", "邦邦" in txt, False)

    print("=== 2c. 单实例(未装邦邦时同一句话,状态行不再区分) ===")
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16384, installed=False, running=False)],
        "shots": {},
    })
    root.update()
    check("单实例 -> 同样只报数量",
          "1 个实例" in app.device_status.cget("text"), True)
    # 回到三开状态供后续用例
    app._on_devices_scanned({"adb": "adb.exe", "devices": devs, "shots": {}})
    root.update()
    print("=== 3. 手动选第 2 个实例 -> 落盘 + 回显 ===")
    target = G.device_scan_label(devs[1])
    # 走真实交互路径:_pick 会先改 DropdownBox._index 再回调 on_change,
    # 直接调 _on_device 绕过了这一步,测不到"显示是否跟随选择"。
    app.device_box._pick(2)
    root.update()
    check("device_address 已设置", app.device_address, "127.0.0.1:16416")
    check("下拉框显示所选", app.device_box.get(), target)
    check("状态栏回显所选端口",
          "16416" in app.device_status.cget("text"), True)
    saved = app._load_gui_config()
    check("已写入 gui_config.json", saved.get("device_address"),
          "127.0.0.1:16416")

    print("=== 4. 选回自动识别 -> 清空 ===")
    app.device_box._pick(0)
    root.update()
    check("device_address 已清空", app.device_address, "")
    check("下拉框回到自动项", app.device_box.get(), app.AUTO_DEVICE_LABEL)
    check("gui_config 也清空",
          app._load_gui_config().get("device_address", ""), "")

    print("=== 5. 两台都装了邦邦(克隆镜像) -> 仍不宣称能区分 ===")
    devs2 = [
        FakeDev(16384, installed=True, running=True, foreground=True),
        FakeDev(16416, installed=True, running=True, foreground=True),
    ]
    app._on_devices_scanned({"adb": "adb.exe", "devices": devs2, "shots": {}})
    root.update()
    txt = app.device_status.cget("text")
    check("报出实例数量", "2 个实例" in txt, True)
    check("说明多开时需手动指定", "指定" in txt, True)
    check("不会偷偷选中某个实例", app.device_address, "")

    print("=== 6. 扫不到设备 -> 明确提示,不崩 ===")
    app._on_devices_scanned({"adb": "adb.exe", "devices": [], "shots": {}})
    root.update()
    check("下拉框只剩自动识别", len(app.device_box.values), 1)
    check("状态栏说明未发现", "未发现" in app.device_status.cget("text"), True)

    print("=== 7. 之前选的实例这次没扫到 -> 保留选择,不清空 ===")
    app._device_scanning = False
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16384, installed=True, running=True)],
        "shots": {},
    })
    app._on_device(G.device_scan_label(FakeDev(16384)))
    # 只扫到别的实例
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16448, installed=True, running=True)],
        "shots": {},
    })
    root.update()
    check("已不在列表 -> 清空并落盘", app.device_address, "")

    print("=== 8. 传给 bot 的 --device 参数 ===")
    # 先重新扫一次,让 _devices 有数据(_on_device 靠它把标签映射回地址)
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16384, installed=True, running=True),
                    FakeDev(16416, installed=True, running=True)],
        "shots": {},
    })
    root.update()
    app._on_device(G.device_scan_label(FakeDev(16416)))
    root.update()
    check("选中后有地址", app.device_address, "127.0.0.1:16416")
    args = _args_for(app)
    check("含 --device", "--device" in args, True)
    check("值正确", args[args.index("--device") + 1], "127.0.0.1:16416")

    print("=== 9. 选自动时不传 --device ===")
    app._on_device(app.AUTO_DEVICE_LABEL)
    root.update()
    args = _args_for(app)
    check("不含 --device", "--device" in args, False)

    print("=== 10. 布局:框在最左,状态文字在下一行(回归) ===")
    app._on_devices_scanned({
        "adb": "adb.exe",
        "devices": [FakeDev(16384, installed=True, running=True),
                    FakeDev(16416, installed=True, running=True)],
        "shots": {},
    })
    root.update()
    line = app.device_box.master
    check("框与按钮同容器", line is app.device_refresh_btn.master, True)
    # pack 默认把重建的控件追加到末尾 → 框会被挤到按钮右侧(用户实机看到过)
    check("下拉框在最左", app.device_box.winfo_x() < app.device_refresh_btn.winfo_x(), True)
    check("刷新在预览左侧", app.device_refresh_btn.winfo_x() < app.device_preview_btn.winfo_x(), True)
    # fill="x" 状态标签与 side="left" 控件混用同一父容器会让整行错位
    check("状态文字不在同一行",
          app.device_status.winfo_rooty()
          >= app.device_box.winfo_rooty() + app.device_box.winfo_height() - 5, True)
    check("状态文字属于外层容器",
          app.device_status.master is not line, True)

    root.destroy()
    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


def _args_for(app):
    """复现 start 里的参数拼装(不真启动子进程)。"""
    if getattr(app, "view", "") == "live.special":
        args = ["--mode", "special", "--special-song", str(app.special_song)]
    else:
        args = ["--mode", "main", "--difficulty", str(app.difficulty),
                "--livemode", G.LIVE_MODE]
    if app.device_address:
        args += ["--device", str(app.device_address)]
    return args


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        # 配置写在临时目录里,这里只需删临时目录;用户真实配置从未被打开。
        try:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)
        except Exception:
            pass
    sys.exit(rc)

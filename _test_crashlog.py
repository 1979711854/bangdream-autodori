# -*- coding: utf-8 -*-
"""崩溃可观测性 —— 离线回归(不需要模拟器,不需要游戏)。

对应一次真实事故:超高难度 SPECIAL 曲目的谱面除零,崩溃发生在 `post_task`
**之前**,`debug/` 日志停在半路、一个字的报错都没有 —— 因为:

  1. bot 侧:早期异常落在 `main()` 的 try 之外,打包态(autodori.exe)没有
     控制台,traceback 只写 stderr,跟着管道被 GUI 的「关键事件」白名单滤掉。
  2. GUI 侧:traceback 的续行是无时间戳的裸行,只认第一行(/ERROR 级别那行)
     会让整个调用栈断掉,只剩一句光秃秃的报错。

本测试分三块:

A. 结构   —— `main()` 必须把整条主流程(`_main_impl`)包在 try 里,且初始化、
             谱面预处理、post_task 这些"会炸的"调用都在 `_main_impl` 内;
             日志必须在此之前就装好,否则异常连落盘的处理器都没有。
B. 真跑   —— 子进程里把 `_main_impl` 换成抛异常的桩,走真实 `main()`,
             验证 traceback 确实写进 `debug/*.log` 且退出码 == 1。
C. GUI    —— 用最小实例喂入一段真栈,验证「关键事件」能同时拿到首行与续行。

用法:  .venv\\Scripts\\python.exe _test_crashlog.py
"""

import ast
import collections
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOT_SRC = ROOT / "src" / "autodori.py"
GUI_SRC = ROOT / "gui.py"
DEBUG_DIR = ROOT / "debug"

# 每次运行都换一个新标记,确保读到的日志确实是本次子进程产的,而不是旧的。
PROBE = "CRASHLOG_PROBE_%d" % os.getpid()

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (label, ("| " + str(extra)) if extra else ""))


def toplevel_funcs(path: Path):
    """返回 {函数名: ast.FunctionDef},只取模块顶层。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def calls_in(node):
    """收集 node 体内出现过的被调用名(裸名 + 属性名两种写法都收)。"""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


# ---------------------------------------------------------------- A. 结构

def test_structure():
    print("\n[A] main() / _main_impl() 的结构")
    funcs = toplevel_funcs(BOT_SRC)
    check("存在 main()", "main" in funcs)
    check("存在 _main_impl()", "_main_impl" in funcs)
    if "main" not in funcs or "_main_impl" not in funcs:
        return

    main_node, impl_node = funcs["main"], funcs["_main_impl"]

    # main() 必须真的把 _main_impl 包在 try 里(而不是裸调)。
    guarded = False
    for sub in ast.walk(main_node):
        if isinstance(sub, ast.Try):
            for stmt in sub.body:
                for inner in ast.walk(stmt):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id == "_main_impl"):
                        guarded = True
    check("main() 把 _main_impl() 包在 try 中", guarded)

    main_calls = calls_in(main_node)
    check("main() 里先装日志(configure_log)", "configure_log" in main_calls)
    check("main() 用 logging.exception 记全栈",
          any(isinstance(s, ast.Attribute) and s.attr == "exception"
              for s in ast.walk(main_node) if isinstance(s, ast.Attribute)))
    check("异常后走统一收尾(_shutdown)", "_shutdown" in main_calls)

    # 早期会炸的调用都必须在被 try 罩住的 _main_impl 里。
    impl_calls = calls_in(impl_node)
    for name in ("init_maa", "init_player_and_mnt", "_prepare_special_song",
                 "_log_environment", "post_task"):
        check("_main_impl() 内含 %s" % name, name in impl_calls)

    # 反证:这些调用不能还留在 main() 裸跑。
    for name in ("init_maa", "init_player_and_mnt", "_prepare_special_song"):
        check("main() 内不再裸调 %s" % name, name not in main_calls)


# ---------------------------------------------------------------- B. 真跑

CHILD = r'''
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
import autodori

def boom():
    raise ZeroDivisionError("%(probe)s")

autodori._main_impl = boom
autodori.main()
print("UNREACHABLE: main() 之后还能执行")
''' % {"probe": PROBE}


def test_runtime_traceback():
    print("\n[B] 真起子进程:早期异常是否落进 debug 日志")
    before = set(DEBUG_DIR.glob("autodori-*.log")) if DEBUG_DIR.is_dir() else set()

    proc = subprocess.run(
        [sys.executable, "-c", CHILD],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )

    check("退出码 == 1(异常路径)", proc.returncode == 1,
          "实际 %s" % proc.returncode)
    check("没有跑到 main() 之后", "UNREACHABLE" not in (proc.stdout or ""))

    after = set(DEBUG_DIR.glob("autodori-*.log"))
    fresh = sorted(after - before)
    check("本次运行产出了新日志文件", len(fresh) == 1,
          "新增 %d 个: %s" % (len(fresh), [p.name for p in fresh]))
    if not fresh:
        return
    log = fresh[0].read_text(encoding="utf-8", errors="replace")

    check("日志含异常标记 " + PROBE, PROBE in log)
    check("日志含 'Traceback (most recent call last)'",
          "Traceback (most recent call last)" in log)
    check("日志含异常类型与消息",
          re.search(r"ZeroDivisionError: %s" % PROBE, log) is not None)
    check("日志含栈帧(File ...)",
          re.search(r'File ".*", line \d+', log) is not None)
    check("日志含统一收尾记录(脚本收尾: 未捕获异常)",
          "脚本收尾: 未捕获异常" in log)


# ---------------------------------------------------------------- C. GUI

def test_gui_traceback_continuation():
    print("\n[C] GUI: traceback 续行是否被「关键事件」保留")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import gui  # noqa: E402  (需要先补 sys.path)

    # 不建 Tk 根窗口 —— 只测 _handle_line 的过滤逻辑,用一个最小实例即可。
    app = object.__new__(gui.AutodoriGUI)
    app._log_buf = collections.deque(maxlen=200)
    app._raw_log = collections.deque(maxlen=200)
    app._in_traceback = False
    app.current_song = ""
    app.songs_done = 0

    lines = [
        "2026-10-01 16:00:00,123[ERROR][autodori] 脚本异常终止: ZeroDivisionError('%s')" % PROBE,
        "Traceback (most recent call last):",
        '  File "src/autodori.py", line 2223, in _main_impl',
        "    _prepare_special_song(args.special_song)",
        '  File "src/chart.py", line 201, in add_smooth_move',
        "ZeroDivisionError: float division by zero",
    ]
    for ln in lines:
        app._handle_line(ln)

    shown = [m for (_stamp, _level, m) in app._log_buf]
    check("首行(ERROR 级)进入关键事件",
          any("脚本异常终止" in m for m in shown))
    check("traceback 标题行被保留",
          any("Traceback (most recent call last)" in m for m in shown))
    check("栈帧行被保留",
          any('File "src/chart.py", line 201' in m for m in shown))
    check("异常类型行被保留",
          any("ZeroDivisionError: float division by zero" in m for m in shown))
    check("整段 6 行无遗漏", len(shown) == 6,
          "实际 %d 行: %r" % (len(shown), shown))

    # 之后的正常日志必须把「在栈里」这个状态清掉,否则会把无关裸行也当 ERROR 显示。
    app._handle_line("2026-10-01 16:00:01,000[INFO][autodori] 脚本收尾: 正常结束")
    app._handle_line("这条裸行不属于任何 traceback,不该被显示")
    check("新日志记录后不再吞入无关裸行",
          not any("不属于任何 traceback" in m for m in
                  [m for (_s, _l, m) in app._log_buf]))


def main():
    test_structure()
    test_runtime_traceback()
    test_gui_traceback_continuation()
    print("\n合计: %d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

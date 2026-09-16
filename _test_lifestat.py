# -*- coding: utf-8 -*-
"""离线验证「生命检测状态 → 统计计数器」的映射不再漏项。

背景(2026-09-16 实跑):`_life_exhausted_on_screen` 返回 "precheck_rejected",
而统计字典的键叫 "precheck",`play_song` 里却写成 `life_stats[life_result] += 1`
→ 亮度预检一拒绝就 KeyError,被 `Play.run` 的兜底 except 吞成 "Failed when play
song",整首歌在首音触发后约 30ms 被判失败(那轮 9 首里 6 首这样死掉)。

本测试用 AST 从真实源码取两处事实,断言它们对齐:
  1. `_life_exhausted_on_screen` 所有 `return "<status>"` 的字面量集合;
  2. `play_song` 里对 `life_result` 做比较的字面量集合。
若以后有人给检测函数加新状态而忘了加计数器分支,这里会直接失败。

用法: python _test_lifestat.py
"""
import ast
import io
import sys

SRC = "src/autodori.py"


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise SystemExit("找不到函数 %s" % name)


def main():
    raw = io.open(SRC, encoding="utf-8").read()
    tree = ast.parse(raw)

    # 1. 检测函数返回的状态字面量。注意 return 里有 IfExp 形式
    #    (`return "hit" if text else "no_match"`),不能只收 Constant。
    returned = set()

    def _collect(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            returned.add(node.value)
        elif isinstance(node, ast.IfExp):
            _collect(node.body)
            _collect(node.orelse)

    for node in ast.walk(_func(tree, "_life_exhausted_on_screen")):
        if isinstance(node, ast.Return):
            _collect(node.value)
    returned.discard("no_match")  # no_match 不计数,属于预期

    # 2. play_song 里 life_result 的比较目标
    handled = set()
    play = _func(tree, "play_song")
    for node in ast.walk(play):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "life_result":
            continue
        for comp in node.comparators:
            if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                handled.add(comp.value)
    handled.discard("no_match")

    ok = True
    print("检测函数返回状态:", sorted(returned))
    print("play_song 已分支  :", sorted(handled))
    missing = returned - handled
    if missing:
        ok = False
        print("  [FAIL] 有状态没有计数器分支,会 KeyError:", sorted(missing))
    else:
        print("  [PASS] 每个返回状态都有对应分支")

    # 反向:分支里出现但函数不会返回的状态(多半是笔误)
    extra = handled - returned
    if extra:
        ok = False
        print("  [FAIL] 分支里的状态函数从不返回(疑似笔误):", sorted(extra))
    else:
        print("  [PASS] 分支里没有多余状态")

    # 3. 源码里不得再出现对 life_stats 的直接下标索引(注释行不算)
    bad = [
        (i + 1, ln.strip())
        for i, ln in enumerate(raw.splitlines())
        if "life_stats[" in ln
        and "[life_result]" in ln
        and not ln.strip().startswith("#")
    ]
    if bad:
        ok = False
        for ln_no, text in bad:
            print("  [FAIL] %d 行仍在直接索引: %s" % (ln_no, text))
    else:
        print("  [PASS] 没有 life_stats[life_result] 式直接索引")

    print("\n结果: " + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

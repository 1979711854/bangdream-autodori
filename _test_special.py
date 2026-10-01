# -*- coding: utf-8 -*-
"""超高难度 SPECIAL 活动 —— 离线回归(不需要模拟器,不需要游戏)。

覆盖四块,对应这个功能的四个真实风险点:

1. 曲目解析   —— `resolve_special_song` 真跑(AST 提取),验证简中/日文标题、
                 纯数字 id、非法值。解析错 = 整首按错谱面打,所以这里必须严。
2. pipeline 接线 —— special.json + 既有 json 合并后,所有 next/interrupt/on_error
                 都能解析到真实节点;节点名不与其他文件冲突;同一节点的三个列表
                 内部与之间**都不允许重复元素**(MAA 遇到重复会让整份 pipeline
                 失效,只留 "Failed to init MAA.");special_idle 必须是
                 special_wait.next 的最后一位且是 DirectHit(否则等不到界面时
                 整条链全不命中,任务被判失败)。
3. bot 分支   —— `--mode special` 时 entry=special_wait、难度被强制 special;
                 谱面预加载发生在 player 建好之后、post_task 之前。
4. GUI 接线   —— 导航项存在、`start(mode)` 分流、`--special-song` 透传,
                 且 start() 拼命令行时每个元素都过了 str()。

用法:  .venv\\Scripts\\python.exe _test_special.py
"""

import ast
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = ROOT / "assets" / "resource" / "pipeline"
BOT_SRC = ROOT / "src" / "autodori.py"
GUI_SRC = ROOT / "gui.py"

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


def load_toplevel(path):
    src = path.read_text(encoding="utf-8")
    return ast.parse(src), src


# --------------------------------------------------------------------------
# 1. resolve_special_song 真跑
# --------------------------------------------------------------------------
def build_song_index():
    """真实曲库索引 + 同名分组(有缓存就用缓存,取不到就退回最小 stub)。"""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from api import BestdoriAPI

        songs = BestdoriAPI.get_song_list()
        idx = {}
        for sid, sinfo in songs.items():
            titles = sinfo.get("musicTitle") or []
            first = [t for t in titles if t]
            if first:
                idx[first[0]] = sid
            zh = titles[3] if len(titles) > 3 else None
            if zh:
                idx.setdefault(zh, sid)
        return songs, idx, build_ambiguous(songs, idx)
    except Exception as e:  # 离线/缓存缺失时的最小可用替身
        print("  (警告: 取真实曲库失败,退回 stub: %s)" % e)
        songs = {
            "596": {"musicTitle": [
                "[超高難易度 SPECIAL] SENSENFUKOKU",
                "[New SPECIAL Difficulty] SENSENFUKOKU",
                None,
                "[超高难易度 新SPECIAL] SENSENFUKOKU",
                None],
                "difficulty": {"0": {}, "1": {}, "2": {}, "3": {}, "4": {}}},
            "786": {"musicTitle": [
                "ときめきエクスペリエンス！ (月島まりなver.)",
                "Tokimeki Experience! (Tsukishima Marina ver.) ",
                None,
                "ときめきエクスペリエンス！ (月岛麻里奈ver.)",
                None],
                "difficulty": {"0": {}, "1": {}, "2": {}, "3": {}, "4": {}}},
            "790": {"musicTitle": [
                "ときめきエクスペリエンス！ (月島まりなver.)",
                "Tokimeki Experience! (Tsukishima Marina ver.) ",
                None,
                "ときめきエクスペリエンス！ (月岛麻里奈ver.)",
                None],
                "difficulty": {"0": {}, "1": {}, "2": {}, "3": {}}},
        }
        idx = {}
        for sid, sinfo in songs.items():
            titles = sinfo["musicTitle"]
            first = [t for t in titles if t]
            if first:
                idx[first[0]] = sid
            zh = titles[3] if len(titles) > 3 else None
            if zh:
                idx.setdefault(zh, sid)
        return songs, idx, build_ambiguous(songs, idx)


def build_ambiguous(songs, idx):
    """同名标题分组 —— 与 bot 里那段构建逻辑等价(那边是模块级推导式,没法 AST 抽取)。"""
    t2ids = {}
    for sid, sinfo in songs.items():
        for t in sinfo.get("musicTitle") or []:
            if t and t in idx:
                ids = t2ids.setdefault(t, [])
                if sid not in ids:
                    ids.append(sid)
    amb = {t: ids for t, ids in t2ids.items() if len(ids) > 1}
    for t, ids in amb.items():          # 索引默认值排最前,保持与 bot 一致
        default = idx.get(t)
        if default in ids:
            ids.remove(default)
            ids.insert(0, default)
    return amb


def extract_func(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(mod)
            return compile(mod, "<extract>", "exec")
    raise AssertionError("函数 %s 不存在" % name)


def test_resolve():
    print("[1] resolve_special_song 曲目解析")
    tree, _ = load_toplevel(BOT_SRC)
    code = extract_func(tree, "resolve_special_song")
    songs, idx, amb = build_song_index()
    ns = {
        "all_songs": songs,
        "all_song_name_indexes": idx,
        "ambiguous_titles": amb,
        "_normalize_title": lambda t: unicodedata.normalize("NFKC", t or ""),
        "logging": logging,
        "Optional": Optional,
    }
    # resolve 现在会调这两个辅助函数 → 一起抽出来执行
    for helper in ("_special_capable_id", "_prefer_special_id"):
        exec(extract_func(tree, helper), ns)
    exec(code, ns)
    resolve = ns["resolve_special_song"]

    check("简中标题解析到 596",
          (resolve("[超高难易度 新SPECIAL] SENSENFUKOKU") or ("", ""))[1] == "596",
          resolve("[超高难易度 新SPECIAL] SENSENFUKOKU"))
    check("日文标题同样解析到 596",
          (resolve("[超高難易度 SPECIAL] SENSENFUKOKU") or ("", ""))[1] == "596")
    check("纯数字 id 可用",
          (resolve("596") or ("", ""))[1] == "596")
    check("前后空白被容忍",
          (resolve("  [超高难易度 新SPECIAL] SENSENFUKOKU  ") or ("", ""))[1] == "596")
    check("不存在的曲名返回 None", resolve("不存在的曲子") is None)
    check("不存在的 id 返回 None", resolve("999999") is None)
    check("空串返回 None", resolve("") is None)
    check("None 返回 None", resolve(None) is None)
    title, sid = resolve("596")
    check("数字 id 会带出真实标题", bool(title) and sid == "596", (title, sid))

    # 同名消歧:ときめきエクスペリエンス！(月島まりなver.) 的日文标题在索引里指向
    # #790,而 #790 没有 SPECIAL 谱面(charts/790/special.json = 404)。活动模式
    # 打的是固定谱面,必须落到有 SPECIAL 档的 #786 上,否则整首按错谱面打。
    zh = "ときめきエクスペリエンス！ (月岛麻里奈ver.)"
    jp = "ときめきエクスペリエンス！ (月島まりなver.)"
    if "786" in songs and "790" in songs:
        check("候选确有 786/790 两个 id", set(amb.get(jp) or []) >= {"786", "790"},
              amb.get(jp))
        check("786 有 SPECIAL 档",
              ns["_special_capable_id"]("786") is True)
        check("790 没有 SPECIAL 档",
              ns["_special_capable_id"]("790") is False)
        check("简中标题 -> 786", (resolve(zh) or ("", ""))[1] == "786", resolve(zh))
        check("日文标题也被改选到 786(不是 790)",
              (resolve(jp) or ("", ""))[1] == "786", resolve(jp))
        check("手写 id 790 仍按原样返回(不替换用户明确意图)",
              (resolve("790") or ("", ""))[1] == "790")
    else:
        print("  (跳过高难度消歧断言:曲库缓存里没有 786/790)")

    # 下拉框里每一首都必须真的能打:解析得到 + 该 id 确实有 SPECIAL 谱面。
    # 标题直接从 gui.py 抽,免得测试里再抄一份、日后漂移。
    gui_tree = ast.parse(GUI_SRC.read_text(encoding="utf-8"))
    dropdown = None
    for node in gui_tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SPECIAL_SONGS" for t in node.targets
        ):
            dropdown = ast.literal_eval(node.value)
    check("从 gui.py 抽到 SPECIAL_SONGS", bool(dropdown), dropdown)
    for item in dropdown or ():
        got = resolve(item)
        ok = bool(got) and ns["_special_capable_id"](got[1])
        check("下拉框曲目可打: %s -> #%s" % (item, got[1] if got else None),
              ok, got)


# --------------------------------------------------------------------------
# 2. pipeline 接线
# --------------------------------------------------------------------------
def merged_pipeline():
    merged = {}
    where = {}
    for p in sorted(PIPELINE_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for name, node in data.items():
            merged[name] = node
            where[name] = p.name
    return merged, where


def refs(node):
    out = []
    for key in ("next", "interrupt", "on_error"):
        val = node.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            val = [val]
        for item in val:
            out.append((key, item))
    return out


def test_pipeline():
    print("[2] pipeline 接线")
    special = json.loads((PIPELINE_DIR / "special.json").read_text(encoding="utf-8"))
    merged, where = merged_pipeline()

    # 2.1 节点名不与其他文件冲突(重名会让活动节点覆盖常规流程的节点)
    collisions = [n for n in special if where.get(n) != "special.json"]
    check("special.json 节点名无跨文件冲突", not collisions, collisions)

    # 2.2 所有引用都能解析
    unresolved = []
    for name, node in special.items():
        for key, target in refs(node):
            if target not in merged:
                unresolved.append((name, key, target))
    check("special.json 内部引用全部可解析", not unresolved, unresolved)

    # 2.3 三个列表内部与之间都不允许重复元素(MAA:重复 = 整份 pipeline 失效)
    dup_problems = []
    for name, node in special.items():
        seen, all_items = set(), []
        for key in ("next", "interrupt", "on_error"):
            val = node.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                val = [val]
            for item in val:
                if item in seen:
                    dup_problems.append((name, key, item))
                seen.add(item)
                all_items.append(item)
    check("三列表无重复元素", not dup_problems, dup_problems)

    # 2.4 special_idle 必须是 DirectHit 且排在 next 最后
    idle = special.get("special_idle", {})
    check("special_idle 是 DirectHit(无 recognition)", "recognition" not in idle)
    check("special_idle.next 回到 special_wait", idle.get("next") == ["special_wait"])
    wait_next = special.get("special_wait", {}).get("next") or []
    check("special_idle 是 special_wait.next 的最后一位",
          bool(wait_next) and wait_next[-1] == "special_idle", wait_next)
    check("special_idle 带 post_delay 限速(不做全速空转)",
          isinstance(idle.get("post_delay"), (int, float)))

    # 2.5 收尾链路:打完 -> special_finish -> stop
    check("special_finish.next == ['stop']",
          special.get("special_finish", {}).get("next") == ["stop"])
    # on_error 只能是 "stop",与 next 重复 → 按 MAA 的规则会整份失效,故必须缺省
    check("special_finish 不配 on_error(避免与 next 重复)",
          "on_error" not in special.get("special_finish", {}))
    # special_stop:演出失败时的零点击终止节点(失败弹窗已由上游点掉,不再盲点)
    check("special_stop 只 next 到 stop",
          special.get("special_stop", {}).get("next") == ["stop"])
    check("special_stop 不带任何点击动作(失败路径不盲点)",
          "action" not in special.get("special_stop", {})
          and "recognition" not in special.get("special_stop", {}))

    # 2.6 打歌入口接到既有演出核心
    check("special_song_running -> playsong",
          special.get("special_song_running", {}).get("next") == ["playsong"])
    check("playsong 节点存在且带 on_error",
          isinstance(merged.get("playsong"), dict) and merged["playsong"].get("on_error"))

    # 2.6b 「演出开始」用模板 + OCR 双识别,且模板优先
    # (09-29 实测:活动确认页的「确定」= confirm/pink 匹配 0.9997;其后的开演页沿用标准模板)
    check("special_click_start 用 startlive 模板",
          special.get("special_click_start", {}).get("template")
          == "live/button/startlive.png")
    check("special_click_start_ocr 用 OCR 识别「演出开始」",
          "演出开始" in (special.get("special_click_start_ocr", {}).get("expected") or []))
    check("模板识别排在 OCR 兜底之前",
          wait_next.index("special_click_start") < wait_next.index("special_click_start_ocr"),
          wait_next)

    # 2.7 活动节点里不允许出现「无 recognition 且带 Click」的盲点节点
    blind = [
        n for n, node in special.items()
        if "recognition" not in node and node.get("action") == "Click"
    ]
    check("special.json 无盲点击节点", not blind, blind)

    # 2.8 把运行期覆盖也算进来,从入口做图可达性检查
    #     —— 节点名写错/链路断掉在文件级检查里看不出来,可达性才看得出来。
    runtime = dict(merged)
    runtime.update(special_branch_dicts(load_toplevel(BOT_SRC)[0]))
    reach, queue = set(), ["special_wait"]
    while queue:
        cur = queue.pop()
        if cur in reach:
            continue
        reach.add(cur)
        for _, target in refs(runtime.get(cur) or {}):
            if target not in reach:
                queue.append(target)
    for want in ("playsong", "special_finish", "special_stop", "stop", "save_succeed_playresult"):
        check("运行期图可达 %s" % want, want in reach)
    check("图不可达 liveagain(打完不会再次演出)", "liveagain" not in reach,
          sorted(n for n in reach if "aga" in n))


def special_branch_dicts(tree):
    """取 _get_override_pipeline 里 `if SPECIAL_MODE:` 分支真正写进 all_pipelines 的
    节点字典(**按 AST 取值,不看注释**),返回 {节点名: 节点定义}。"""
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_get_override_pipeline")
    out = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "SPECIAL_MODE"):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Subscript):
                target = stmt.targets[0]
                if isinstance(target.value, ast.Name) and target.value.id == "all_pipelines":
                    out[target.slice.value] = ast.literal_eval(stmt.value)
        if out:
            break
    return out


def test_bot_branch():
    print("[3] bot 分支接线")
    tree, src = load_toplevel(BOT_SRC)

    # 3.1 _get_override_pipeline 在 SPECIAL_MODE 下提前返回三条改写
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_get_override_pipeline")
    seg = ast.get_source_segment(src, fn)
    check("_get_override_pipeline 有 SPECIAL_MODE 分支", "if SPECIAL_MODE:" in seg)
    check("分支提前返回", seg.index("if SPECIAL_MODE:") < seg.index("# set_difficulty"))

    overrides = special_branch_dicts(tree)
    check("分支正好改写三个节点",
          set(overrides) == {"wait_playresult", "save_succeed_playresult",
                             "handle_life_exhausted"}, sorted(overrides))
    check("save_succeed_playresult.next -> special_finish",
          (overrides.get("save_succeed_playresult") or {}).get("next") == ["special_finish"])
    check("handle_life_exhausted.next -> special_stop(失败不盲点按钮)",
          (overrides.get("handle_life_exhausted") or {}).get("next") == ["special_stop"])
    check("wait_playresult.on_error -> special_finish",
          (overrides.get("wait_playresult") or {}).get("on_error") == ["special_finish"])
    flat = json.dumps(overrides, ensure_ascii=False)
    check("改写里不再出现 liveagain / live_home_button",
          "liveagain" not in flat and "live_home_button" not in flat)

    # 3.1c 得分界面的「确定」只能由 SpecialFinish 点一次:
    #      confirm/pink 在活动得分界面的「确定」上实测命中 0.9981,若留在 interrupt 里
    #      会被框架抢先点掉,行为不可预期 → 两条 interrupt 都必须清掉按钮类节点。
    wp_interrupt = (overrides.get("wait_playresult") or {}).get("interrupt") or []
    check("wait_playresult.interrupt 已清掉按钮类节点",
          not ({"confirm_button", "next_button", "ok_button", "close_button"}
               & set(wp_interrupt)), wp_interrupt)
    check("wait_playresult 仍保留 live_failed(否则失败认不出)", "live_failed" in wp_interrupt)
    check("save_succeed_playresult.interrupt 已清空",
          (overrides.get("save_succeed_playresult") or {}).get("interrupt") == [])

    # 3.1b 改写节点同样受"三列表不得重复"约束
    dup = []
    for name, node in overrides.items():
        seen = set()
        for key in ("next", "interrupt", "on_error"):
            val = node.get(key)
            if val is None:
                continue
            if isinstance(val, str):
                val = [val]
            for item in val:
                if item in seen:
                    dup.append((name, key, item))
                seen.add(item)
    check("改写节点三列表无重复元素", not dup, dup)

    # 3.2 入口:--mode special -> entry=special_wait, 难度 special
    # 主流程自 v1.2.7 起搬进 _main_impl(),由 main() 用 try 罩住(见 _test_crashlog.py);
    # 这里按"入口主流程"取源码,两种结构都能过,避免下次搬动又假失败。
    entry_fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "_main_impl"),
        None,
    ) or next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    main_seg = ast.get_source_segment(src, entry_fn)
    check("入口主流程里 entry = special_wait", '"special_wait"' in main_seg)
    check("入口主流程里强制 DIFFICULTY = \"special\"",
          'DIFFICULTY = "special"' in main_seg)
    check("--mode choices 含 special", '"special"' in src and '"main", "special"' in src)
    check("--special-song 参数已注册", '"--special-song"' in src)

    # 3.3 谱面预加载的位置:建好 player 之后、post_task 之前
    lines = src.splitlines()
    i_player = next(i for i, l in enumerate(lines) if l.strip() == "init_player_and_mnt()")
    i_prep = next(i for i, l in enumerate(lines) if l.strip() == "_prepare_special_song(args.special_song)")
    i_task = next(i for i, l in enumerate(lines) if "maatasker.post_task(" in l)
    check("预加载在 init_player_and_mnt 之后", i_prep > i_player, (i_player, i_prep))
    check("预加载在 post_task 之前", i_prep < i_task, (i_prep, i_task))

    # 3.4 两个自定义动作都已注册到 maaresource
    for action in ("SpecialIdle", "SpecialFinish"):
        check("已注册 custom action %s" % action,
              '@maaresource.custom_action("%s")' % action in src)

    # 3.5 SpecialFinish 在得分界面点「确定」后收尾
    #     (得分界面右下角是「再次演出 + 确定」,liveagain 命中 0.9962、确定 0.9981,
    #      所以必须显式点确定,且确定要排在优先级第一位)
    fin = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SpecialFinish")
    fin_seg = ast.get_source_segment(src, fin)
    click_nodes = []
    for node in ast.walk(fin):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BUTTONS" for t in node.targets
        ) and isinstance(node.value, ast.Tuple):
            click_nodes = [e.value for e in node.value.elts
                           if isinstance(e, ast.Constant)]
    check("SpecialFinish 的点击清单非空", bool(click_nodes), click_nodes)
    check("SpecialFinish 会点「确定」(confirm_button)", "confirm_button" in click_nodes,
          click_nodes)
    check("「确定」排在点击优先级第一位",
          bool(click_nodes) and click_nodes[0] == "confirm_button", click_nodes)
    check("SpecialFinish 保留获得报酬弹窗兜底",
          "event_reward_confirm" in click_nodes, click_nodes)
    check("SpecialFinish 检查 completed 才算点过", "completed" in fin_seg)
    check("SpecialFinish 点中即收工(不把后续界面全走完)", "break" in fin_seg)


def test_gui():
    print("[4] GUI 接线")
    src = GUI_SRC.read_text(encoding="utf-8")
    check("导航项 live.special 已注册", '("live.special", "超高难度活动", "live")' in src)
    check("_card_detail 路由到 _detail_special",
          "self.view == \"live.special\"" in src and "_detail_special(master)" in src)
    check("start 支持 mode 参数", 'def start(self, mode="main")' in src)
    check("活动模式透传 --special-song", '"--special-song"' in src)
    check("活动按钮走 special 模式", 'self.start(mode="special")' in src)
    check("special_song 持久化到 gui_config", '"special_song": self.special_song' in src)
    check("_render_view 清理已销毁卡片按钮引用",
          "self.special_start_btn = None" in src)

    # start() 拼命令行:cmd 的每个元素必须是 str/字面量/str() 包裹
    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and "AutodoriGUI" in n.name)
    start_fn = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "start")
    bad = []
    for node in ast.walk(start_fn):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "bot_args" for t in node.targets
        ):
            for el in node.value.elts:
                if isinstance(el, ast.Constant) and isinstance(el.value, str):
                    continue
                if isinstance(el, ast.Call) and isinstance(el.func, ast.Name) \
                        and el.func.id == "str":
                    continue
                bad.append(ast.dump(el)[:60])
    check("bot_args 每项都是 str 或不含裸表达式", not bad, bad)


def test_gui_render():
    """真建 Tk root 但 withdraw(),走真实 _render_view() 渲染活动页。

    静态断言看不出"控件建不起来"(比如 W.Row/DropdownBox 参数写错会直接抛异常),
    这里把整条渲染链路跑一遍。无显示环境(CI/无桌面)时跳过,不算失败。
    """
    print("[5] GUI 活动页真渲染(无显示环境则跳过)")
    try:
        import tkinter as tk
    except Exception as e:
        print("  SKIP  无 tkinter: %s" % e)
        return
    try:
        root = tk.Tk()
    except Exception as e:
        print("  SKIP  无显示环境: %s" % e)
        return

    try:
        root.withdraw()
        sys.path.insert(0, str(ROOT))
        import gui as G

        app = G.AutodoriGUI(root)
        app.view = "live.special"
        app._render_view()
        check("活动页渲染出启动按钮",
              getattr(app, "special_start_btn", None) is not None)
        # 切走再切回:卡片控件会被销毁重建,引用必须跟着更新,不能指向死控件
        app.view = "live.show"
        app._render_view()
        check("切到其它页后按钮引用被清空", app.special_start_btn is None)
        app.view = "live.special"
        app._render_view()
        check("切回活动页重新建出按钮", app.special_start_btn is not None)
        check("special_song 默认值已落在界面状态里",
              bool(str(app.special_song).strip()))

        # 曲目标题很长,下拉框必须装得下,否则文字会被截断 —— 这里用真实字体实测,
        # 防止以后被人把 width 调回去。
        #
        # 注意:measure() 必须在 Tk 起来之后取。字体未就绪时 Tk 会给出虚高值
        # (2026-09-30 实测同一字符串 268px -> 415px,差 55%),看门狗会因此误判。
        # 下面用真渲染 Label 的 reqwidth 交叉验证一次,锁住这个前提。
        import tkinter.font as tkfont

        def _walk(w):
            out = []
            for c in w.winfo_children():
                out.append(c)
                out += _walk(c)
            return out

        root.update_idletasks()
        ft = tkfont.Font(font=G.W._f(G.W.base_size(), "normal"))
        longest = max(G.SPECIAL_SONGS, key=ft.measure)
        probe = tk.Label(root, text=longest, font=G.W._f(G.W.base_size(), "normal"))
        probe.update_idletasks()
        measured, rendered = ft.measure(longest), probe.winfo_reqwidth()
        probe.destroy()
        check("字体度量与真实渲染一致(%r: measure=%d, label=%d)"
              % (longest[:12] + "…", measured, rendered),
              0 <= rendered - measured <= 12, rendered - measured)

        boxes = [c for c in _walk(app.host) if isinstance(c, G.W.DropdownBox)]
        check("活动页有且只有一个曲目下拉框", len(boxes) == 1, len(boxes))
        if boxes:
            box = boxes[0]
            # 文本从 x=12 起画,右侧还要给箭头留位置 → 需求 = 文本宽 + 24px 余量
            need = max(ft.measure(v) for v in G.SPECIAL_SONGS) + 24
            check("曲目下拉框请求宽度 >= 最长标题所需 %dpx" % need,
                  box.winfo_reqwidth() >= need,
                  "req=%d" % box.winfo_reqwidth())
            # 最小窗口是 minsize 1080x600,缩到最小时靠的就是 min_width
            check("曲目下拉框 min_width >= 最长标题所需 %dpx" % need,
                  box._min_w >= need, "min=%d" % box._min_w)

        # _write_config 会落盘到 data/config.yml(用户真实配置),先备份再还原
        cfg_path = ROOT / "data" / "config.yml"
        backup = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else None
        try:
            app._write_config()
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            check("运行配置里写入了 special_song",
                  cfg.get("special_song") == str(app.special_song),
                  cfg.get("special_song"))
            check("_write_config 仍是合并写入(未抹掉其它键)",
                  "play_at_zero_boost" in cfg and "song_strategy" in cfg)
        finally:
            if backup is not None:
                cfg_path.write_text(backup, encoding="utf-8")
    except Exception as e:
        check("活动页真渲染无异常", False, "%s: %s" % (type(e).__name__, e))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    test_resolve()
    test_pipeline()
    test_bot_branch()
    test_gui()
    test_gui_render()
    print("\n合计: %d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

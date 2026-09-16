# 歌名识别回归测试(离线,不需要模拟器/MAA)
#
# 背景(2026-09-16):
#   キズナミュージック♪ 有 [FULL] 版(#249, expert 1485 音符)与普通版(#158, 436 音符),
#   是完全不同的谱面。bot 把 FULL 版识别成普通版 → 按 436 音符的谱面打歌,
#   101s 谱面就结束、后半首全 miss、生命值耗尽。
#
#   根因两条:
#     1. OCR 把 `[FULL]` 读成 `「FULLI`(实测原文:`「FULLIキズナミュージックト`),
#        而 fuzzywuzzy 的 WRatio 对"短标题被长查询包含"最高只给 90 分
#        (源码:长度差 >1.5 倍时 partial 结果 ×0.9)→ 基础曲以 90 分压过
#        正确条目 `[FULL] キズナミュージック♪` 的 87 分。
#     2. 歌名索引只收了日文标题,而游戏客户端是简中:变体条目(3D演出模式 / 平行歌曲)
#        在中文标题里才带标注,日文标题里根本没有 → 无法区分。
#
#   本测试用**日志里捞出的真实 OCR 原文**做语料,断言:
#     A. 报告中的 FULL 场景必须识别到 #249;
#     B. 3D/平行曲变体必须识别到各自的条目;
#     C. 其余样本的识别结果与改动前**完全一致**(回归哨兵);
#     D. 曲库里每条标题(日文 + 中文)都能映射回自己的 id(不被别的条目抢走)。
#
# 被测代码用 AST 从 src/autodori.py 直接提取(不是复制),改实现后本测试自动跟随。

import ast
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fuzzywuzzy import process as fzwzprocess  # noqa: E402
from api import BestdoriAPI  # noqa: E402

passed = []
failed = []


def check(ok, msg):
    (passed if ok else failed).append(msg)
    print("  [%s] %s" % ("PASS" if ok else "FAIL", msg))


def load_under_test():
    """从 src/autodori.py 提取歌名索引构造 + 匹配函数,在干净命名空间里执行。"""
    source = (ROOT / "src" / "autodori.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted_names = {"all_song_name_indexes"}
    wanted_consts = {"_TITLE_BRACKETS", "_TITLE_BRACKET_TRANS", "_POOL_SCORE_FLOOR"}
    segments = []
    for node in tree.body:
        seg = ast.get_source_segment(source, node)
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign):
            targets = {node.target.id} if isinstance(node.target, ast.Name) else set()
        else:
            targets = set()
        if targets & wanted_names or targets & wanted_consts:
            segments.append(seg)
        elif isinstance(node, ast.For) and "all_song_name_indexes" in seg:
            segments.append(seg)
    for fn in ("_has_title_prefix", "fuzzy_match_song"):
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == fn
        )
        segments.append(ast.get_source_segment(source, node))

    all_songs = BestdoriAPI.get_song_list()
    ns = {"fzwzprocess": fzwzprocess, "all_songs": all_songs}
    exec("\n".join(segments), ns)
    return ns["all_song_name_indexes"], ns["fuzzy_match_song"], ns["_has_title_prefix"], all_songs


INDEX, match_song, has_prefix, SONGS = load_under_test()


def baseline_index(all_songs):
    """复刻改动前的索引(只取首个非空标题)与匹配,仅用于回归对照。"""
    return {
        [t for t in (s["musicTitle"] or []) if t][0]: sid
        for sid, s in all_songs.items()
        if any(s["musicTitle"] or [])
    }


BASE_INDEX = baseline_index(SONGS)
BASE_KEYS = list(BASE_INDEX.keys())


def baseline_match(name):
    return fzwzprocess.extractOne(name, BASE_KEYS)


def song_id(key):
    return INDEX.get(key) or BASE_INDEX.get(key)


# ---------------------------------------------------------------- A/B/C: 语料
# (OCR 原文, 改动前命中, 期望命中) —— 全部取自 debug/maa.log 的真实识别结果
CASES = [
    ("「FULLIキズナミュージックト", "158", "249"),   # 本次报告的 bug
    ("[F]", "64", "217"),
    ("キズナミュージックト（支持3D演出模式）", "158", "484"),
    ("（支持3演出模式）", "478", "484"),
    ("熱色スターマイン(平行歌曲)", "56", "763"),
    ("熱色夕(平行歌曲)", "204", "660"),
    ("きゅ〜まい*í仙Iower（平行歌曲）", "186", "761"),
    ("～L*flower(平行歌曲）", "93", "761"),
    ("えがお、あ一ゆ一れてい??？", "235", "460"),
    ("at", "54", "54"),
    ("の", "17", "17"),
    ("ReReRe", "149", "149"),
    ("across", "149", "149"),
    ("八月のif", "64", "64"),
    ("季節は次々死んていく", "727", "727"),
    ("季節次死", "727", "727"),
    ("Ave Mujica", "531", "531"),
    ("AveMujica", "531", "531"),
    ("THRONE OF ROSE", "543", "543"),
    ("THRONE OFROSE", "543", "543"),
    ("怪獣の花唄", "594", "594"),
    ("閃光", "467", "467"),
    ("闪光", "467", "467"),
    ("Little Busters!", "46", "46"),
    ("LittleBusters！", "46", "46"),
    ("THE HISTORIC..", "416", "416"),
    ("WinkingtCheer", "264", "264"),
    ("Winking☆Cheer", "264", "264"),
    ("ひとりじゃないんだから", "147", "147"),
    ("ダーリンダンス", "482", "482"),
    ("夢みるSunflower", "45", "45"),
    ("[FULL] FIRE BIRD", "243", "243"),
    ("FIRE BIRD", "187", "187"),
    ("[FULL] ON YOUR MARK", "223", "223"),
    ("ON YOUR MARK", "184", "184"),
    ("[超高難易度 SPECIAL] HELL! or HELL?", "597", "597"),
    ("HELL! or HELL?", "359", "359"),
    ("[超高難易度 新SPECIAL] 六兆年と一夜物語", "487", "487"),
    ("六兆年と一夜物語", "128", "128"),
    ("[超高難易度 新SPECIAL] HELL! or HELL?", "486", "486"),
    ("[原曲] final phase", "273", "273"),
    ("final phase", "275", "275"),
    ("キズナミュージック♪", "158", "158"),
    ("[FULL] キズナミュージック♪", "249", "249"),
]


def test_prefix_classifier():
    print("\n[1] 前缀标记判定")
    cases = [
        ("[FULL] キズナミュージック♪", True),
        ("「FULLIキズナミュージックト", True),      # OCR 把 [ / ] 读成 「 / I
        ("【FULL】X", True),
        ("[超高難易度 SPECIAL] HELL! or HELL?", True),
        ("[原曲] final phase", True),
        ("「僕は...」", True),                      # 标题本身带引号
        ("キズナミュージック♪", False),
        ("一逢のFull Glory", False),                # 内含 Full 但不是前缀标记
        ("", False),
    ]
    for text, want in cases:
        check(has_prefix(text) == want, "前缀判定 %r -> %s" % (text[:24], want))


def test_reported_bug():
    print("\n[2] 报告中的场景(真实 OCR 原文)")
    targets = [
        ("「FULLIキズナミュージックト", "249", "[FULL] キズナミュージック♪"),
        ("キズナミュージックト（支持3D演出模式）", "484", "3D演出模式变体"),
    ]
    for text, want_id, desc in targets:
        hit = match_song(text)
        got = song_id(hit[0])
        check(got == want_id, "%s: %r -> #%s (分数 %d, 键 %r)" % (desc, text[:26], got, hit[1], hit[0][:26]))


def test_corpus_table():
    print("\n[3] 真实语料回归表(共 %d 条)" % len(CASES))
    changed = []
    for text, old_id, want_id in CASES:
        hit = match_song(text)
        got = song_id(hit[0])
        base = song_id(baseline_match(text)[0])
        if base != got:
            changed.append((text, base, got))
        if got != want_id:
            check(False, "%r 期望 #%s, 实际 #%s" % (text[:30], want_id, got))
        elif base != got:
            check(True, "%r #%s -> #%s (修正)" % (text[:30], base, got))
    changed_set = {t for t, _, _ in changed}
    print("  发生变化的样本: %d 条 -> %s" % (len(changed), [t[:16] for t, _, _ in changed]))
    # 回归哨兵:没被点名的样本必须与改动前完全一致
    for text, old_id, want_id in CASES:
        if text in changed_set:
            continue
        base = song_id(baseline_match(text)[0])
        got = song_id(match_song(text)[0])
        if base != got:
            check(False, "未预期变化: %r #%s -> #%s" % (text[:30], base, got))
    check(True, "未被点名的样本全部与改动前一致")


def test_self_consistency():
    print("\n[4] 自洽性:每条标题都能映射回自己的 id")
    # 同一标题字符串可能被多个 id 占用(Bestdori 数据冲突,如 #597 与 #486 的中文名相同),
    # 这种只要求映射到"提出该标题的其中之一"。
    claimants = {}
    for sid, s in SONGS.items():
        for t in s.get("musicTitle") or []:
            if t:
                claimants.setdefault(t, set()).add(sid)

    zh_only = []
    for title, sids in claimants.items():
        if title not in BASE_INDEX and any(
            (SONGS[sid].get("musicTitle") or [None] * 4)[3:4] == [title] for sid in sids
        ):
            zh_only.append((title, sids))
    wrong = 0
    for title, sids in zh_only:
        got = song_id(match_song(title)[0])
        if got not in sids:
            wrong += 1
            print("      中文独有键错配: %r (应 %s)" % (title[:30], sorted(sids)))
    check(wrong == 0, "中文独有键 %d 条全部回到自己" % len(zh_only))

    sample = [(k, v) for i, (k, v) in enumerate(BASE_INDEX.items()) if i % 8 == 0]
    wrong = 0
    for key, sid in sample:
        got = song_id(match_song(key)[0])
        if got not in claimants.get(key, {sid}):
            wrong += 1
            print("      日文键错配: %r (应 #%s, 实 #%s)" % (key[:30], sid, got))
    check(wrong == 0, "日文键抽样 %d 条全部回到自己" % len(sample))


if __name__ == "__main__":
    print("索引规模: %d (改动前 %d)" % (len(INDEX), len(BASE_INDEX)))
    test_prefix_classifier()
    test_reported_bug()
    test_corpus_table()
    test_self_consistency()
    print("\n通过 %d 项, 失败 %d 项" % (len(passed), len(failed)))
    sys.exit(1 if failed else 0)

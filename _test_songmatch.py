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
import logging
import re
import sys
import unicodedata
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fuzzywuzzy import fuzz as fzwzfuzz  # noqa: E402
from fuzzywuzzy import process as fzwzprocess  # noqa: E402
from api import BestdoriAPI  # noqa: E402

passed = []
failed = []


def check(ok, msg):
    (passed if ok else failed).append(msg)
    print("  [%s] %s" % ("PASS" if ok else "FAIL", msg))


def load_under_test():
    """从 src/autodori.py 提取歌名索引构造 + 匹配函数,在干净命名空间里执行。

    提取方式:凡是源码里出现下列"种子名"的赋值/循环语句都收进来(保持源码顺序),
    再补上三个函数。这样改实现时只要种子名不变,本测试自动跟随。
    """
    source = (ROOT / "src" / "autodori.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    seeds = ("all_song_name_indexes", "_title_to_ids", "ambiguous_titles")
    wanted_consts = {
        "_TITLE_BRACKETS", "_TITLE_BRACKET_TRANS", "_POOL_SCORE_FLOOR",
        "_SONG_LEVEL_ROI", "_DIFFICULTY_ORDER",
    }
    segments = []
    for node in tree.body:
        seg = ast.get_source_segment(source, node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                {t.id for t in node.targets if isinstance(t, ast.Name)}
                if isinstance(node, ast.Assign)
                else ({node.target.id} if isinstance(node.target, ast.Name) else set())
            )
            if targets & wanted_consts:
                segments.append(seg)
                continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.For)) and any(
            s in seg for s in seeds
        ):
            segments.append(seg)
    for fn in ("_has_title_prefix", "_normalize_title", "fuzzy_match_song",
               "_song_level", "_candidate_ids", "_pick_song_id"):
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == fn
        )
        segments.append(ast.get_source_segment(source, node))

    all_songs = BestdoriAPI.get_song_list()
    ns = {
        "fzwzfuzz": fzwzfuzz,
        "unicodedata": unicodedata,
        "re": re,
        "logging": logging,
        "Optional": Optional,
        "Context": object,
        "DIFFICULTY": "hard",
        "all_songs": all_songs,
    }
    exec("\n".join(segments), ns)
    return ns


NS = load_under_test()
INDEX = NS["all_song_name_indexes"]
match_song = NS["fuzzy_match_song"]
has_prefix = NS["_has_title_prefix"]
AMBIGUOUS = NS["ambiguous_titles"]
SONGS = NS["all_songs"]



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


def test_two_model_readings():
    print("\n[7] 两个模型读数合并(本次报告的 ぎゅっDAYS♪ 截断)")
    # 真实日志 2026-09-16 12:56:13:日文模型读出全角残字,默认模型只读出片段,
    # 而那个片段恰好是 **另一首歌(#120 DAYS)** 的完整标题 → 旧逻辑按 100 分选中它。
    hit = match_song("ぎゅっＤＡYＳト", ["DAYS"])
    check(
        song_id(hit[0]) == "169",
        "「ぎゅっＤＡYＳト + DAYS」-> #%s (%r, %.0f 分)" % (song_id(hit[0]), hit[0], hit[1]),
    )
    hit = match_song("ぎゅっDAYS♪", ["DAYS"])
    check(song_id(hit[0]) == "169", "读数完整时仍为 #169")
    # 不能矫枉过正:真的抽到 DAYS 时必须还是 #120
    hit = match_song("DAYS")
    check(song_id(hit[0]) == "120", "单个读数 'DAYS' 仍命中 #%s" % song_id(hit[0]))
    hit = match_song("DAYS", ["DAYS"])
    check(song_id(hit[0]) == "120", "两个读数都是 'DAYS' 时仍是 #120")
    # 截断:只读出片段时,正确的长标题不该被"候选长度"冤枉
    hit = match_song("季節次死")
    check(song_id(hit[0]) == "727", "截断读数 '季節次死' -> #%s" % song_id(hit[0]))


def test_ambiguous_resolution():
    print("\n[5] 重名曲目消歧(按选歌界面「乐曲等级」数字)")
    pick = NS["_pick_song_id"]
    cand = NS["_candidate_ids"]
    song_level = NS["_song_level"]
    calls = {"n": 0}

    def resolve(title, level, difficulty):
        NS["DIFFICULTY"] = difficulty
        NS["_read_screen_song_level"] = lambda context, image: (
            calls.__setitem__("n", calls["n"] + 1) or level
        )
        calls["n"] = 0
        return pick(None, None, title, cand(title))

    # 报告的场景:閃光 有 Roselia(#410,EXPERT 26)与 Afterglow×レイヤ(#467,EXPERT 27)
    # 两版,谱面完全不同
    ids = cand("閃光")
    check(set(ids) == {"410", "467"}, "閃光 认出两条同名: %s" % sorted(ids))
    for diff, a, b in (("expert", "410", "467"), ("hard", "410", "467")):
        la, lb = song_level(a, diff), song_level(b, diff)
        check(la != lb, "閃光 在 %s 下两条等级不同(%s vs %s)" % (diff, la, lb))
        check(resolve("閃光", la, diff) == a, "閃光 屏上等级 %s -> #%s" % (la, a))
        check(resolve("閃光", lb, diff) == b, "閃光 屏上等级 %s -> #%s" % (lb, b))
    check(resolve("閃光", None, "expert") is None, "閃光 读不到等级 -> 拒绝(不赌)")
    check(resolve("閃光", 99, "expert") is None, "閃光 等级对不上任何一条 -> 拒绝")

    # 等级相同的组:实测谱面字节级一致,直接取默认项,且不该浪费一次 OCR
    # 注意这里必须用**重名键本身**的写法:索引里 `[超高难易度 新SPECIAL] 六兆年と一夜物語`
    # (简中「难」)才是重名的那个键,日文「難」的写法只对应一条。
    title = "[超高难易度 新SPECIAL] 六兆年と一夜物語"
    ids = cand(title)
    check(len(ids) == 2, "六兆年 认出两条同名: %s" % ids)
    NS["DIFFICULTY"] = "expert"
    NS["_read_screen_song_level"] = lambda context, image: calls.__setitem__(
        "n", calls["n"] + 1
    )
    calls["n"] = 0
    got = pick(None, None, title, ids)
    check(got == ids[0], "等级无区分的组取默认项 #%s" % got)
    check(calls["n"] == 0, "等级无区分的组不额外读屏")

    # 多组同名曲一律能按等级复位到正确条目
    pairs = [("オレンジ", "hard", "316", "676"), ("シル・ヴ・プレジデント", "expert", "389", "462")]
    for title, diff, a, b in pairs:
        la, lb = song_level(a, diff), song_level(b, diff)
        if la == lb:
            print("      跳过 %r:在 %s 下等级相同(%s)" % (title, diff, la))
            continue
        check(resolve(title, la, diff) == a, "%s 屏上等级 %s -> #%s" % (title, la, a))
        check(resolve(title, lb, diff) == b, "%s 屏上等级 %s -> #%s" % (title, lb, b))


def test_index_integrity():
    print("\n[6] 索引完整性")
    check(len(AMBIGUOUS) == 8, "全曲库重名标题 %d 组(预期 8)" % len(AMBIGUOUS))
    for title, ids in AMBIGUOUS.items():
        default = INDEX.get(title)
        check(
            ids[0] == default,
            "%r 默认项 %s 排在最前(不改变原有选择)" % (title[:22], default),
        )
        check(all(i in SONGS for i in ids), "%r 的 id 都存在于曲库" % title[:22])


if __name__ == "__main__":
    print("索引规模: %d (改动前 %d)" % (len(INDEX), len(BASE_INDEX)))
    test_prefix_classifier()
    test_reported_bug()
    test_corpus_table()
    test_self_consistency()
    test_two_model_readings()
    test_ambiguous_resolution()
    test_index_integrity()
    print("\n通过 %d 项, 失败 %d 项" % (len(passed), len(failed)))
    sys.exit(1 if failed else 0)

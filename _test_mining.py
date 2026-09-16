# -*- coding: utf-8 -*-
"""验证「挖矿为主」档位判定与选曲策略(直接执行 src/autodori.py 的真实函数)。

做法:用 AST 从源码里抽出相关函数与模块级常量,注入真实 peewee 模型、
真实曲库、真实 DB 后 exec —— 测的是源码本身,不是复制出来的逻辑。
"""
import ast
import sys
import os
from pathlib import Path

sys.path.insert(0, 'src')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from peewee import SqliteDatabase, Model, TimestampField, CharField, BooleanField
from playhouse.sqlite_ext import JSONField
from diskcache import Cache

# ---------- 真实 DB 模型(与 src/chart.py 一致) ----------
db = SqliteDatabase('data/play_records.db')


class PlayRecord(Model):
    play_time = TimestampField()
    play_offset = JSONField()
    chart_id = CharField()
    difficulty = CharField()
    succeed = BooleanField()
    result = JSONField()

    class Meta:
        database = db


all_songs = Cache('cache').get('allsongs')

WANTED_FUNCS = {
    '_tier_from_records', '_build_tier_map', '_tier_map', '_song_tier',
    '_current_target_tier', '_reject_limit', 'check_song_available',
}
WANTED_VARS = {
    'MINED_MAX_TIER', 'DEFAULT_REJECT_LIMIT', 'AP_REROLL_STREAK_MAX',
    '_selection_rejections', '_ap_reroll_streak', '_tier_map_cache',
}

tree = ast.parse(Path('src/autodori.py').read_text(encoding='utf-8'))
nodes = []
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in WANTED_FUNCS:
        nodes.append(n)
    elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
        if n.target.id in WANTED_VARS:      # 带类型标注的赋值,如 _tier_map_cache: dict = {}
            nodes.append(n)
    elif isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name) and t.id in WANTED_VARS:
                nodes.append(n)

import yaml  # noqa: E402
ns = {
    'PlayRecord': PlayRecord, 'all_songs': all_songs, 'yaml': yaml,
    'config_path': Path('data/config.yml'), 'print': print,
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<autodori>', 'exec'), ns)
ns['_song_strategy'] = lambda: 'mine'          # 固定为挖矿策略

tier_from_records = ns['_tier_from_records']
song_tier = ns['_song_tier']
target_tier = ns['_current_target_tier']
check = ns['check_song_available']

ok = True


def check_(cond, label):
    global ok
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    ok = ok and bool(cond)


class R:
    def __init__(self, succeed, result):
        self.succeed, self.result = succeed, result


print("场景 1:失败记录不再被误判为 ALL PERFECT")
check_(tier_from_records([]) == 0, "无记录 -> tier0")
check_(tier_from_records([R(False, {})]) == 0, "仅失败记录(result={}) -> tier0(旧实现误判为 3)")
check_(tier_from_records([R(True, {})]) == 0, "succeed 但 result 为空 -> tier0")
check_(tier_from_records([R(False, {}), R(False, {})]) == 0, "多次失败 -> 仍 tier0")

print("\n场景 2:按历史最好成绩判定,成就不倒退")
check_(tier_from_records([R(True, {"miss": 5, "great": 10})]) == 1, "未 FC -> tier1")
check_(tier_from_records([R(True, {"miss": 0, "bad": 0, "good": 0, "great": 3})]) == 2, "FC 未 AP -> tier2")
check_(tier_from_records([R(True, {"miss": 0, "bad": 0, "good": 0, "great": 0})]) == 3, "AP -> tier3")
check_(tier_from_records([R(True, {"miss": 9, "great": 1}), R(True, {"great": 0, "miss": 0, "bad": 0, "good": 0})]) == 3,
       "先差后 AP -> 取最好 tier3(旧实现取首条会停在 tier1)")
check_(tier_from_records([R(True, {"great": 0, "miss": 0, "bad": 0, "good": 0}), R(True, {"miss": 3})]) == 3,
       "先 AP 后失败 -> 仍 tier3(成就不会被后期失败抹掉)")
check_(tier_from_records([R(False, {}), R(True, {"miss": 0, "great": 0, "bad": 0, "good": 0})]) == 3,
       "失败之后再 AP -> tier3")

print("\n场景 3:与旧实现对比(真实数据,expert)")
def old_tier(recs):
    """旧实现:get_or_none 取首条;空 result 当全零 -> 误判 AP。"""
    if not recs:
        return 0
    r = recs[0][1] if isinstance(recs[0][1], dict) else {}
    m, b = int(r.get('miss', 0) or 0), int(r.get('bad', 0) or 0)
    g, gr = int(r.get('good', 0) or 0), int(r.get('great', 0) or 0)
    if m == 0 and b == 0 and g == 0:
        return 3 if gr == 0 else 2
    return 1

rows = list(PlayRecord.select().order_by(PlayRecord.id))
groups = {}
for r in rows:
    groups.setdefault((str(r.chart_id), r.difficulty), []).append(r)

diffs = 0
only_fail_locked = 0
for (cid, diff), recs in groups.items():
    old = old_tier([(r.succeed, r.result) for r in recs])
    new = tier_from_records(recs)
    if old != new:
        diffs += 1
    if old == 3 and new == 0:
        only_fail_locked += 1
print(f"   有记录的(歌,难度)组合: {len(groups)}")
print(f"   新旧判定不一致: {diffs} 组")
check_(diffs > 0, "新实现确实修正了旧实现的误判")
check_(only_fail_locked > 0, f"其中 {only_fail_locked} 首'仅失败却被当 AP'得到纠正")

print("\n场景 4:口径一致 —— _song_tier 与 _current_target_tier 同源")
tmap = ns['_tier_map']('expert')
check_(target_tier('expert') == min(tmap.values()) if tmap else True,
       "_current_target_tier == 全曲库档位的最小值(与 _song_tier 同源)")
bad = [sid for sid in tmap if song_tier(sid, 'expert') != tmap[sid]]
check_(not bad, "每首曲目的 _song_tier 均等于档位表取值(无第二套算法)")

print("\n场景 5:候选池 = tier0/1/2,仅排除 tier3;池内优先更低档")
orig_map, orig_target = ns['_tier_map'], ns['_current_target_tier']
ns['_tier_map'] = lambda d: {'s0': 0, 's1': 1, 's2': 2, 's3': 3}

# --- 5a:存在未打过(target=0) -> 只优先 tier0 ---
ns['_current_target_tier'] = lambda d: 0
ns['_selection_rejections'] = ns['_ap_reroll_streak'] = 0
check_(check('', 's0', 'x') is True, "target=0 抽到 tier0 -> 接受")
check_(check('', 's1', 'x') is False, "target=0 抽到 tier1 -> 拒绝(优先更低档)")
check_(check('', 's2', 'x') is False, "target=0 抽到 tier2 -> 拒绝")
check_(check('', 's3', 'x') is False, "target=0 抽到 tier3(已AP) -> 拒绝")

# --- 5b:软优先有上限,超限放宽到候选池内(但不放行 tier3) ---
ns['_selection_rejections'] = 0
ns['_ap_reroll_streak'] = 0
limit = ns['_reject_limit']()
last = None
for _ in range(limit):
    last = check('', 's2', 'x')
check_(last is True, f"软优先连续拒绝达上限({limit})后接受 tier2(仍在候选池)")
check_(ns['_selection_rejections'] == 0, "接受后软优先计数归零")

# --- 5c:已 AP 单独计数,兜底阈值远高于软优先 ---
ns['_selection_rejections'] = 0
ns['_ap_reroll_streak'] = 0
ap_cap = ns['AP_REROLL_STREAK_MAX']
res = [check('', 's3', 'x') for _ in range(ap_cap)]
check_(all(r is False for r in res[:-1]) and res[-1] is True,
       f"已 AP 连续拒绝 {ap_cap} 次后才让步接受(防死循环)")

# --- 5d:target=2 时 tier2 可接受(全连也有奖励) ---
ns['_current_target_tier'] = lambda d: 2
ns['_selection_rejections'] = ns['_ap_reroll_streak'] = 0
check_(check('', 's2', 'x') is True, "target=2 抽到 tier2(FC未AP) -> 接受")
check_(check('', 's3', 'x') is False, "target=2 抽到 tier3 -> 仍拒绝")

# --- 5e:全曲库已 AP -> 无条件接受,不卡死 ---
ns['_current_target_tier'] = lambda d: 3
ns['_selection_rejections'] = ns['_ap_reroll_streak'] = 0
check_(check('', 's3', 'x') is True, "全部已 AP -> 接受,不会卡死")

ns['_tier_map'], ns['_current_target_tier'] = orig_map, orig_target

print("\n结果: " + ("全部通过 ✅" if ok else "存在失败 ❌"))
sys.exit(0 if ok else 1)

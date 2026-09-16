# -*- coding: utf-8 -*-
"""离线验证 GUI 日志处理:_handle_line 的时间戳与选曲实时性。

不改任何真实控件:用一个最小桩对象承载 _handle_line 依赖的属性,
直接调用未绑定的 AutodoriGUI._handle_line(self=桩, line)。
"""
import re
import sys
import time

import gui

# 只保留纯逻辑依赖,避开 Tk 初始化(不创建窗口)
SONG_RE = gui.SONG_RE
PLAY_RE = gui.PLAY_RE
LOG_RE = gui.LOG_RE


class Stub:
    """承载 _handle_line 所需的全部状态/方法,不涉及任何 Tk 控件。"""

    def __init__(self):
        self._raw_log = []
        self.current_song = ""
        self.songs_done = 0
        self.auto_cal = False
        self.emitted = []          # [(stamp, level, msg), ...]
        self.m_song = None         # 无控件 -> 走纯状态分支
        self.m_songs = None

    def _emit(self, stamp, level, message):
        self.emitted.append((stamp, level, message))

    # _on_play_result 会用到这些
    def _set_cal(self, text):
        pass

    def _apply_photogate(self, v):
        pass

    _should_show = gui.AutodoriGUI._should_show
    _KEEP = gui.AutodoriGUI._KEEP
    _LEVEL_KEEP = gui.AutodoriGUI._LEVEL_KEEP
    _handle_line = gui.AutodoriGUI._handle_line
    _on_play_result = gui.AutodoriGUI._on_play_result


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    return cond


class RealEmitStub(Stub):
    """走真实 _emit(不覆盖),用于验证时间戳兜底。

    真实 _emit 写入 _log_buf;这里额外镜像到 emitted,让所有场景共用同一访问口径。
    """

    _emit = gui.AutodoriGUI._emit

    def __init__(self):
        super().__init__()
        self._log_buf = []          # 真实 _emit 会写入
        self.full_log = None        # 无控件 -> 只进缓存
        self._n = 0

    def _before(self):
        self._n = len(self._log_buf)

    def _drain(self):
        self.emitted.extend(self._log_buf[self._n:])
        self._before()


stamp_re = re.compile(r"^\d{2}:\d{2}:\d{2}$")
ok = True

# --- 场景 1:选曲完成 -> 应立刻出现带时间戳的「选曲 · 歌名」 ---
print("场景 1:选曲实时性 + 时间戳")
s = Stub()
s._handle_line("2026-09-10 19:36:44,810[DEBUG][root] Save song: BIBIBABI BRAVER")
ok &= check(s.current_song == "BIBIBABI BRAVER", "current_song 立即更新为歌名")
song_lines = [e for e in s.emitted if "选曲" in e[2]]
ok &= check(len(song_lines) == 1, "立刻产出 1 条「选曲」日志(而非等打歌结束)")
ok &= check(bool(song_lines) and bool(stamp_re.match(song_lines[0][0])),
            "选曲日志时间戳格式 HH:MM:SS -> %r" % (song_lines[0][0] if song_lines else None))
ok &= check(bool(song_lines) and "BIBIBABI BRAVER" in song_lines[0][2],
            "选曲日志内容含歌名")

# --- 场景 2:同一首歌的「打歌:」INFO 行不应重复刷「选曲」 ---
print("场景 2:去重")
n_before = len(song_lines)
s._handle_line("2026-09-10 19:36:57,464[INFO][root] 打歌: BIBIBABI BRAVER (#739-expert), 动作10373, photogate=30ms, 检测带y=510-535")
song_lines2 = [e for e in s.emitted if "选曲" in e[2]]
ok &= check(len(song_lines2) == n_before, "重复歌名不重复产生「选曲」日志")

# --- 场景 3:换歌 -> 应再次产生选曲日志 ---
print("场景 3:换歌")
s._handle_line("2026-09-10 19:40:00,100[INFO][root] Save song: 熱色スターマイン")
ok &= check(s.current_song == "熱色スターマイン", "歌名切换到新曲")
ok &= check(len([e for e in s.emitted if "选曲" in e[2]]) == n_before + 1,
            "换歌产生新「选曲」日志")

# --- 场景 4:结算行必须带时间戳 ---
print("场景 4:结算时间戳")
s2 = RealEmitStub()
pay = ("2026-09-10 19:41:00,500[DEBUG][root] Play result: "
       "{'score': 2541856, 'maxcombo': 603, 'perfect': 667, 'great': 1, "
       "'good': 0, 'bad': 0, 'miss': 0, 'fast': 1, 'slow': 0}")
s2._handle_line(pay)
s2._drain()
settle = [e for e in s2.emitted if "结算" in e[2]]
ok &= check(len(settle) == 1, "产出 1 条结算日志")
ok &= check(bool(settle) and bool(stamp_re.match(settle[0][0])),
            "结算时间戳格式 HH:MM:SS -> %r" % (settle[0][0] if settle else None))
ok &= check(bool(settle) and "2541856" in settle[0][2]
            and "FAST 1/SLOW 0" in settle[0][2], "结算内容完整")

# --- 场景 5:非标准裸行也要有兜底时间戳 ---
print("场景 5:裸行兜底时间戳")
s2._before()
s2._handle_line("Traceback (most recent call last):")
s2._drain()
raw = [e for e in s2.emitted if "Traceback" in e[2]]
ok &= check(bool(raw) and bool(stamp_re.match(raw[0][0])),
            "裸行兜底时间戳 -> %r" % (raw[0][0] if raw else None))

print("\n场景 6:_emit 对所有空时间戳兜底")
e = RealEmitStub()
e._emit("", "CAL", "photogate: 30 → 45 ms")      # 模拟 _apply_photogate
e._emit("", "INFO", ">>> python src/autodori.py")  # 模拟启动命令
e._emit("", "WARN", "=== 手动停止 ===")            # 模拟停止
all_stamped = all(bool(stamp_re.match(s)) for s, _, _ in e._log_buf)
ok &= check(len(e._log_buf) == 3, "3 条消息均写入缓冲")
ok &= check(all_stamped, "空 stamp 全部被兜底为 HH:MM:SS -> %r"
            % [s for s, _, _ in e._log_buf])
# 已带时间戳的应原样保留,不被覆盖
e._emit("19:00:00", "INFO", "keep")
ok &= check(e._log_buf[-1][0] == "19:00:00", "显式时间戳原样保留")

print("\n结果: " + ("全部通过 ✅" if ok else "存在失败 ❌"))
sys.exit(0 if ok else 1)

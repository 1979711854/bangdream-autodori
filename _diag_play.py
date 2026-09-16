# -*- coding: utf-8 -*-
"""从 debug/autodori-*.log 里按「每首歌」聚合首音对齐数据,定位「整首全 MISS / 生命耗尽」。

用法:
    python _diag_play.py                 # 扫 debug/ 下全部日志
    python _diag_play.py <log> [<log>...]  # 只看指定日志

输出每首歌一行:
    song  freeze(fps)  →  首音触发相对冻结的毫秒数  prelude忽略条数/最大偏移  结局

判据(2026-09-16 用 09:50 与 11:47 两轮实跑校验):
  * 触发点落在冻结后 <900ms → 命中前奏残留(计数动画/GO!),图表整体提前 ≥1.5s
    → 整首错位全 MISS → 约 5~9 秒生命耗尽。
  * 触发点 >1000ms → 正常对齐,能打完整首。
  * 同一首歌两次运行结果相反(DAYS 498ms 崩 / 2869ms 打完整首),说明触发点
    是随机落在残留窗口里,不是歌本身有问题。
"""
import glob
import os
import re
import sys

PAT_SONG = re.compile(r"\[INFO\]\[root\] 打歌: (.+?) \(#(\S+?)\), 动作(\d+)")
PAT_FREEZE = re.compile(
    r"Picture freezed.*?freeze 200帧耗 (\d+)ms, 约 (\d+) fps"
)
PAT_FREEZE_NEW = re.compile(r"冻结完成: 等待首音总耗时 (\d+)ms\(静默判定被重置 (\d+) 次\), 最后 200 帧耗 (\d+)ms\(约 (\d+) fps\)")
PAT_TRIG = re.compile(r"首音触发\[(\w+)\]: band (\d+)ms")
PAT_TRIG_OLD = re.compile(r"wfT trigger\(interp ([\d.]+)->([\d.]+)\)")
PAT_PRELUDE = re.compile(r"wfT ignored\(prelude ([\d.]+)ms\)")
PAT_HIT = re.compile(r"打歌中生命值耗尽")
PAT_CRASH = re.compile(r"Failed when play song: (.+)")
PAT_SUMMARY = re.compile(r"生命检测汇总: 执行 (\d+) 次\(命中 (\d+)\), 亮度预检跳过 (\d+), OCR 异常 (\d+), 因切片过密跳过 (\d+)")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def _ts(line):
    m = TS.match(line)
    return m.group(1) if m else ""


def _ms(a, b):
    """两个 HH:MM:SS,mmm 时间戳的毫秒差。"""
    from datetime import datetime

    fmt = "%Y-%m-%d %H:%M:%S,%f"
    return int(
        (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() * 1000
    )


def analyze(path):
    songs = []
    cur = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = PAT_SONG.search(line)
            if m:
                cur = {
                    "name": m.group(1),
                    "id": m.group(2),
                    "actions": int(m.group(3)),
                    "t0": _ts(line),
                    "freeze_t": None,
                    "fps": None,
                    "resets": None,
                    "trig_t": None,
                    "trig_band": None,
                    "prelude_n": 0,
                    "prelude_max": 0.0,
                    "end": "?",
                    "end_t": None,
                }
                songs.append(cur)
                continue
            if cur is None:
                continue
            m = PAT_FREEZE_NEW.search(line) or PAT_FREEZE.search(line)
            if m and cur["freeze_t"] is None:
                if m.re is PAT_FREEZE_NEW:
                    cur["resets"] = int(m.group(2))
                    cur["fps"] = int(m.group(4))
                else:
                    cur["fps"] = int(m.group(2))
                cur["freeze_t"] = _ts(line)
                continue
            m = PAT_TRIG.search(line)
            if m and cur["trig_t"] is None:
                cur["trig_t"], cur["trig_band"] = _ts(line), int(m.group(2))
                continue
            # 旧版日志(无 冻结完成/首音触发 摘要):只用逐帧行 wfT trigger(...)
            if (
                cur["trig_t"] is None
                and PAT_TRIG_OLD.search(line)
                and "ignored" not in line
            ):
                cur["trig_t"] = _ts(line)
                continue
            m = PAT_PRELUDE.search(line)
            if m:
                cur["prelude_n"] += 1
                cur["prelude_max"] = max(cur["prelude_max"], float(m.group(1)))
                continue
            if PAT_HIT.search(line) and cur["end"] == "?":
                cur["end"], cur["end_t"] = "生命耗尽", _ts(line)
                continue
            m = PAT_CRASH.search(line)
            if m and cur["end"] == "?":
                cur["end"], cur["end_t"] = "崩溃:" + m.group(1).strip(), _ts(line)
                continue
    for i, s in enumerate(songs):
        if s["end"] == "?":
            s["end"] = "打完整首"
            if i + 1 < len(songs):
                s["end_t"] = songs[i + 1]["t0"]
    return songs


def render(path):
    songs = analyze(path)
    if not songs:
        return
    print("=" * 100)
    print(os.path.basename(path))
    print(
        "%-26s %-13s %6s %8s %8s %7s %10s  %s"
        % ("歌曲", "谱面", "帧率", "冻结ms", "触发ms", "残留条", "残留最大", "结局")
    )
    for s in songs:
        if s["freeze_t"] and s["trig_t"]:
            fz = _ms(s["t0"], s["freeze_t"])
            tg = _ms(s["freeze_t"], s["trig_t"])
            tg_s = str(tg)
        else:
            fz = _ms(s["t0"], s["freeze_t"]) if s["freeze_t"] else -1
            tg_s = "--"
        dur = _ms(s["t0"], s["end_t"]) if s["end_t"] else -1
        print(
            "%-26s %-13s %6s %8s %8s %7d %10s  %s(%dms)"
            % (
                s["name"][:24],
                s["id"][:12],
                str(s["fps"] or "--"),
                str(fz),
                tg_s,
                s["prelude_n"],
                ("%.0f" % s["prelude_max"]) if s["prelude_n"] else "--",
                s["end"],
                dur,
            )
        )


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(glob.glob("debug/autodori-2026*.log"))
    for p in args:
        render(p)

# -*- coding: utf-8 -*-
"""scripts/ 下一次性脚本的公共路径引导(2026-10-30 目录整理时新增)。

背景:这些检测 / 诊断脚本原本散在仓库根,靠「CWD = 仓库根」去找 src/、assets/、
data/、debug/,或者靠 `Path(__file__).parent` 当仓库根。2026-10-30 全部挪进
scripts/ 之后两条都失效 —— 脚本目录变成 scripts/、CWD 变成调用者所在目录,
于是统一在这里算一次仓库根。

用法:每个脚本在**第三方 / 项目模块** import 之前写一行

    import _bootstrap  # noqa: F401  (把 CWD 设成仓库根并注入 sys.path)

`python scripts/xxx.py` 时 Python 会把 scripts/ 放进 sys.path[0],所以能直接
import 到本模块;从任何目录调用都成立,不再要求先 cd 到仓库根。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # scripts/ 的上一级 = 仓库根
# chdir 回仓库根:脚本里用的都是 root 相对路径(assets/、data/、debug/、src/)
os.chdir(ROOT)
for _p in (str(ROOT / "src"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

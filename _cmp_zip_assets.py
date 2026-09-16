# -*- coding: utf-8 -*-
"""比对发布包内 assets/ 与仓库 assets/ 的差异。

build.py 打 assets 时会忽略 misc/ 与 MaaCommonAssets/,并额外放入 minitouch。
本脚本按同一口径计算"应当入包"的集合,再与包内实际条目比对。
用法: python _cmp_zip_assets.py <zip路径>
"""
import os
import sys
import zipfile

zip_path = sys.argv[1] if len(sys.argv) > 1 else "dist/autodori_v1.2.3_Windows_X64.zip"
SEP = os.sep

z = zipfile.ZipFile(zip_path)
inzip = {n for n in z.namelist() if n.startswith("assets/") and not n.endswith("/")}

repo = set()
for root, dirs, files in os.walk("assets"):
    dirs[:] = [d for d in dirs if d not in ("misc", "MaaCommonAssets")]
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), ".")
        repo.add(rel.replace(SEP, "/"))

print("仓库 assets 应入包: %d" % len(repo))
print("包内 assets      : %d" % len(inzip))

miss = sorted(repo - inzip)
extra = sorted(inzip - repo)
print()
print("包内缺失(%d):" % len(miss))
for m in miss[:40]:
    print("   ", m)
print()
print("包内多出(%d) —— 预期是 build.py 额外放进去的 minitouch / build_metadata:" % len(extra))
for m in extra[:40]:
    print("   ", m)

# 同名文件内容是否一致(忽略 minitouch 与 build_metadata,它们由构建过程生成)
print()
print("同名文件内容比对(不一致即内容陈旧):")
bad = 0
for n in sorted(repo & inzip):
    a = open(n, "rb").read()
    b = z.read(n)
    if a != b:
        bad += 1
        print("   [不一致] %s  仓库 %d bytes / 包内 %d bytes" % (n, len(a), len(b)))
if bad == 0:
    print("   全部一致")

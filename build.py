import json
import os
import shutil
import site
import subprocess
import sys
import zipfile
import argparse
import requests
from io import BytesIO
from zipfile import ZipFile


parser = argparse.ArgumentParser()
parser.add_argument(
    "--version", type=str, help="Specify the version of autodori", default=None
)
parser.add_argument(
    "--os",
    type=str,
    help="Specify the operating system on which the building is running",
    default="none",
)
parser.add_argument(
    "--arch",
    type=str,
    help="Specify the arch on which the building is running",
    default="none",
)
args = parser.parse_args()
if args.version is None:
    VERSION = "unknown"
else:
    VERSION = args.version
ZIP_FILENAME = f"autodori_{VERSION}_{args.os}_{args.arch}.zip"


# 获取当前工作目录
current_dir = os.getcwd()

# 获取 site-packages 目录列表
site_packages_paths = site.getsitepackages()

# 查找包含 maa/bin 的路径
maa_bin_path = None
for path in site_packages_paths:
    potential_path = os.path.join(path, "maa", "bin")
    if os.path.exists(potential_path):
        maa_bin_path = potential_path
        break

if maa_bin_path is None:
    raise FileNotFoundError("Path containing maa/bin not found")

# 构建 --add-data 参数
add_data_param = f"{maa_bin_path}{os.pathsep}maa/bin"

# 查找包含 MaaAgentBinary 的路径
maa_bin_path2 = None
for path in site_packages_paths:
    potential_path = os.path.join(path, "MaaAgentBinary")
    if os.path.exists(potential_path):
        maa_bin_path2 = potential_path
        break

if maa_bin_path2 is None:
    raise FileNotFoundError("Path containing MaaAgentBinary not found")

# 构建 --add-data 参数
add_data_param2 = f"{maa_bin_path2}{os.pathsep}MaaAgentBinary"


def _local_minitouch_ok(path):
    """本地那份 assets/minitouch_EvATive7 有没有真货:至少一个 ABI 下有非空二进制。"""
    try:
        for abi in os.listdir(path):
            exe = os.path.join(path, abi, "minitouch")
            if os.path.isfile(exe) and os.path.getsize(exe) > 0:
                return True
    except OSError:
        pass
    return False


# 下载 minitouch.zip 并解压
def download_and_extract_minitouch():
    """下载并解压 minitouch.zip,返回临时目录(内含 minitouch/ 子目录)。

    下载失败时退回仓库里已提交的 assets/minitouch_EvATive7 —— 目录结构与解压后的
    `minitouch/` 完全一致,所以两者可以互换。没有这个兜底,任何拿不到 github.com
    的机器都完全无法构建:2026-10-13 实测本机 requests 访问 github.com 必报
    SSLCertVerificationError,而 PowerShell / 浏览器正常 —— 两边用的证书库不同
    (requests 只认 certifi 自带的那份)。注意 bot 运行时依赖的 bestdori.com 与
    api.github.com 走的是另外的证书链,不受影响。
    """
    url = "https://github.com/EvATive7/minitouch/releases/latest/download/minitouch.zip"
    temp_dir = os.path.join(current_dir, "minitouch_temp")

    try:
        # 下载文件
        print("Downloading minitouch.zip...")
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            with ZipFile(BytesIO(response.content)) as zip_ref:
                # 解压到临时目录
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)  # 清理旧的临时文件夹
                zip_ref.extractall(temp_dir)
            print("minitouch.zip downloaded and extracted.")
            return temp_dir
        print(f"warning: minitouch.zip download returned HTTP {response.status_code}")
    except Exception as e:
        print(f"warning: minitouch.zip download failed: {e}")

    local_dir = os.path.join(current_dir, "assets", "minitouch_EvATive7")
    if _local_minitouch_ok(local_dir):
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)
        shutil.copytree(local_dir, os.path.join(temp_dir, "minitouch"))
        print("fallback: staged committed assets/minitouch_EvATive7 instead.")
        return temp_dir

    raise Exception(
        "Failed to obtain minitouch: download failed and "
        "assets/minitouch_EvATive7 has no usable binaries."
    )


# 将 minitouch 文件夹移动到 dist/assets/minitouch_EvATive7 目录
def move_minitouch_to_assets(temp_dir):
    minitouch_src_dir = os.path.join(temp_dir, "minitouch")
    minitouch_dest_dir = os.path.join(
        current_dir, "dist", "assets", "minitouch_EvATive7"
    )

    if os.path.exists(minitouch_dest_dir):
        shutil.rmtree(minitouch_dest_dir)  # 如果目标目录已存在，先删除

    shutil.copytree(minitouch_src_dir, minitouch_dest_dir)

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    print(f"minitouch files have been copied to {minitouch_dest_dir}")


# 复制 assets 文件夹到 dist 目录
dist_dir = os.path.join(current_dir, "dist")
assets_source_path = os.path.join(current_dir, "assets")
assets_dest_path = os.path.join(dist_dir, "assets")
syc_bat_source_path = os.path.join(current_dir, "syc.bat")
syc_bat_dest_path = os.path.join(dist_dir, "syc.bat")
metedata_file_path = os.path.join(assets_dest_path, "build_metadata.json")

if not os.path.exists(assets_source_path):
    raise FileNotFoundError("assets folder not found")

# 如果目标路径存在，先删除它
if os.path.exists(dist_dir):
    shutil.rmtree(dist_dir)

# 运行 PyInstaller 打包命令
# 两个入口各打一个 onefile exe:GUI 用 --noconsole(双击不弹黑框),bot 保留控制台
# (GUI 靠 stdout 管道读它的日志)。
# 缺了 GUI exe 用户解压后就没有可双击的启动器 —— 2026-09-16 的 v1.2.3 就是这么
# 发出去的,只能事后手工替换 zip。两个都要打。
# 必须用子进程逐个调用,不能用 PyInstaller.__main__.run():后者在同一进程内被
# 调用第二次时会复用第一次的全局配置,产物不可靠。
DLL_DIR = os.path.join(current_dir, "assets", "misc", "windows", "dll")


def build_exe(entry: str, name: str, console: bool, bundle_maa: bool):
    """bundle_maa=False 时不把 maa/bin 与 MaaAgentBinary 打进包。

    只有 bot 需要它们。GUI 侧(gui.py / ui_theme.py / ui_widgets.py /
    device_scan.py)**从不 import maa** —— 2026-10-13 实测:autodori_gui.exe 的
    PYZ 里 maa* 模块数为 0,autodori.exe 是 13 个;而给 GUI 也塞 --add-data
    会让它白白多出 92 个数据文件、体积从 31.8MB 涨到 62.7MB,发布 zip 跟着从
    ~124MB 顶到 ~155MB。device_scan.py 顶部的注释本来就写明「打包的
    autodori_gui.exe 不含 maa/bin」,所以这是修回原状,不是新行为。
    """
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        entry,
        "--onefile",
        "--noconfirm",
        f"--name={name}",
    ]
    if bundle_maa:
        cmd.append(f"--add-data={add_data_param}")
        cmd.append(f"--add-data={add_data_param2}")
    if not console:
        cmd.append("--noconsole")
    if sys.platform == "win32":
        for _dll in ("msvcp140.dll", "vcruntime140.dll"):
            cmd.append(f"--add-binary={os.path.join(DLL_DIR, _dll)}{os.pathsep}.")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


for _entry, _name, _console, _bundle_maa in (
    (os.path.join(current_dir, "src", "autodori.py"), "autodori.exe", True, True),
    (os.path.join(current_dir, "gui.py"), "autodori_gui.exe", False, False),
):
    build_exe(_entry, _name, _console, _bundle_maa)


# 使用 shutil 复制整个文件夹
shutil.copytree(
    assets_source_path,
    assets_dest_path,
    ignore=lambda dirname, _: (
        ["misc", "MaaCommonAssets"] if os.path.basename(dirname) else []
    ),
)
# 复制OCR模型
ocr_model_path = os.path.join(assets_dest_path, "resource", "model", "ocr")
ocr_src_v4 = os.path.join(current_dir, "assets", "MaaCommonAssets", "OCR", "ppocr_v4", "zh_cn")
if os.path.isdir(ocr_src_v4) and os.listdir(ocr_src_v4):
    # 子模块已递归检出：使用其中的 OCR 模型（原作者意图）
    if os.path.exists(ocr_model_path):
        shutil.rmtree(ocr_model_path)
    shutil.copytree(
        ocr_src_v4,
        ocr_model_path,
        ignore=lambda *_: ["README.md"],
        dirs_exist_ok=True,
    )
    shutil.copytree(
        os.path.join(current_dir, "assets", "MaaCommonAssets", "OCR", "ppocr_v3", "ja_jp"),
        os.path.join(ocr_model_path, "ppocr_v3", "ja_jp"),
        dirs_exist_ok=True,
    )
    print("OCR model copied from MaaCommonAssets submodule.")
else:
    # 子模块未检出（如 CI 未递归拉取）：保留 assets/resource/model/ocr 中已提交的模型。
    # 该目录的模型与子模块内容一致，且已随仓库提交，因此无需网络/子模块即可构建。
    if not os.path.exists(ocr_model_path):
        raise FileNotFoundError(
            "OCR model not found: neither the MaaCommonAssets submodule nor the "
            "committed assets/resource/model/ocr directory is present."
        )
    print("MaaCommonAssets OCR submodule not found; using committed OCR model under assets/resource/model/ocr")
temp_dir = download_and_extract_minitouch()
move_minitouch_to_assets(temp_dir)
json.dump(
    {"version": args.version},
    open(metedata_file_path, "w", encoding="utf-8"),
    ensure_ascii=False,
)

# # 复制 syc.bat 文件
# if os.path.exists(syc_bat_source_path):
#     shutil.copy(syc_bat_source_path, syc_bat_dest_path)
# else:
#     raise FileNotFoundError("syc.bat file not found")

# 把发布说明与截图一并放进包内,与历史发布包的结构保持一致
# (放在 assets 复制之后 —— PyInstaller 会以 --noconfirm 操作 dist/,不要先放东西进去)
for _doc in ("README.md", "CHANGELOG.md", "RELEASE_NOTES.md"):
    _doc_src = os.path.join(current_dir, _doc)
    if os.path.exists(_doc_src):
        shutil.copy(_doc_src, os.path.join(dist_dir, _doc))
    else:
        print(f"warning: {_doc} not found, skipped")
# 同上面的文档/截图:缺了就跳过并告警,不能让构建整体崩掉 ——
# 此时 dist_dir 早已被 rmtree 清空,抛异常会让整轮产物全部丢失。
_challenge_bat = os.path.join(current_dir, "run_challenge_live.bat")
if os.path.exists(_challenge_bat):
    shutil.copy(_challenge_bat, dist_dir)
else:
    print("warning: run_challenge_live.bat not found, skipped")
if os.path.isdir(os.path.join(current_dir, "screenshots")):
    shutil.copytree(
        os.path.join(current_dir, "screenshots"),
        os.path.join(dist_dir, "screenshots"),
        dirs_exist_ok=True,
    )
else:
    print("warning: screenshots/ not found, skipped")

# 压缩 dist 文件夹为 zip 文件，并保存在 dist 目录中
zip_filepath = os.path.join(dist_dir, ZIP_FILENAME)

with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(dist_dir):
        for file in files:
            # 获取文件的绝对路径并相对路径
            file_path = os.path.join(root, file)
            # 跳过刚生成的压缩包
            if file == ZIP_FILENAME:
                continue
            arcname = os.path.relpath(file_path, dist_dir)
            zipf.write(file_path, arcname)

# 删除 dist 文件夹中的所有文件和文件夹，保留压缩包
for root, dirs, files in os.walk(dist_dir):
    for file in files:
        file_path = os.path.join(root, file)
        # 不删除生成的压缩包
        if file != ZIP_FILENAME:
            os.remove(file_path)
    for dir in dirs:
        shutil.rmtree(os.path.join(root, dir), ignore_errors=True)


print(f"Packaging and compression completed: {zip_filepath}")

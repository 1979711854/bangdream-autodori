<div align="center">

# autodori · BanG Dream · 邦邦自动挖矿助手

邦多利(BanG Dream! 国服)自动打歌脚本 + GUI 启动器

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)  ![MaaFramework](https://img.shields.io/badge/MaaFramework-LGPLv3-blue)  ![License](https://img.shields.io/badge/License-GPLv3-green)

</div>

## ✨ 功能

- **GUI 启动器**(Tkinter,无需命令行):选难度 / 火罐策略 / photogate / 生命值耗尽策略
- 自动选歌、打歌、结算后再演连打
- 自动处理:PT 奖励弹窗、角色对话、演出失败、活动故事等结算流程
- 生命值耗尽后可选择**自动退出重新选歌**或**等待手动操作**
- 火罐为 0 时可选择**继续打歌**或**退出游戏**
- 模拟器兼容:Mumu 12 / MumuV5 / 雷电(推荐 MuMu)

## 📌 致谢与来源说明

本项目基于 **GPLv3** 许可证的 [EvATive7/autodori](https://github.com/EvATive7/autodori) 修改而来,感谢原作者的辛勤付出与开源精神。

**本版本在原版基础上新增/修改了:**
- 新增 Tkinter **GUI 启动器**(分栏:主界面 / 设置 / 注意事项 / 常见问题)
- 新增结算弹窗处理(PT 奖励、角色对话、演出失败、活动故事)
- 新增生命值耗尽策略(自动退出重新选歌 / 等待手动)、火罐为 0 策略
- 修复若干弹窗/模板问题,补充日志过滤与 GUI 偏好设置

**依据 GPLv3,本仓库的修改与分发需遵守:**
- 保留 GPLv3 许可证(见 [LICENSE](LICENSE))
- 注明原作者与来源(本说明即为此目的)
- 修改部分开源共享、禁止商业化
- 本项目为 fork 修改版,与原版项目相互独立,请优先阅读本仓库的 README

## 🛠 环境要求(重要,否则无法正常打歌)

### 模拟器设置
- 使用 MuMu Player 12,分辨率 **1280x720**,Vulkan 渲染
- **保持高帧率,不要限制 30fps**(会破坏打歌同步)
- 打歌期间尽量不要操作电脑,避免性能波动

### 游戏设置
- 选曲列表设为**"正常"**,清空歌曲筛选器
- 演出设定:将流速调整为 **8.0**
- 演出效果·音量设定:关闭 **"3D切入模式"**,将**"动作模式"**改为**"轻量模式"**
- 演出效果·音量设定:启用 **"FAST/SLOW表示"** 和 **"Perfect状态显示"**
- 演出模式仅支持**自由演出(freelive)**,不支持协力模式

## 🚀 使用方法

### 方式一:GUI 启动器(推荐)

**本地一键启动(任选其一):**
- 双击项目根目录的 **`autodori_gui.exe`**(打包好的 GUI,直接打开)
- 或双击 **`run_gui.bat`**(有 exe 就启动 exe,没有就自动用 `python gui.py` 跑源码版)
- 或命令行 `python gui.py`(源码版,需先装好依赖)

打开后:在主界面选择难度、火罐策略、photogate、生命值耗尽策略等参数,点击 **开始打歌** 即可;日志区实时显示关键事件,「注意事项」「常见问题」分栏有设置说明。

> 关于 `autodori_gui.exe`:它是用 `pyinstaller --onefile --windowed --name autodori_gui gui.py` 打包的产物,位于项目根目录(`dist/` 里也有一份,是构建输出目录)。exe 已被 `.gitignore` 忽略、**不会上传到仓库**,需要时可在 Release 下载或自行重新打包。

### 方式二:源码运行

```bash
git clone https://github.com/1979711854/bangdream-autodori
cd bangdream-autodori
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python build.py          # 自动整理/下载依赖
python gui.py            # 启动 GUI
# 或命令行直接跑:
python src/autodori.py --mode main --difficulty expert --livemode freelive
```

> 打包 GUI 为 exe:`pyinstaller --onefile --windowed --name autodori_gui gui.py`

## 🖥 GUI 说明

- **主界面**:难度、火罐为0时策略、photogate、生命值耗尽策略 + 开始/停止 + 实时日志
- **设置**:字体大小、窗口默认分辨率(存 `data/gui_config.json`)
- **注意事项**:模拟器/游戏设置提醒
- **常见问题**:常见问题解答

## ❓ 常见问题

**Q:为什么无法正常打歌?**
A:请查看「注意事项」和 README.md,检查游戏和模拟器设置是否正确。

**Q:为什么有些歌会爆很多 GREAT 和 MISS?**
A:个别歌曲难度较大,机器识别可能存在一定延迟;
模拟器长时间运行后发热/内存占用上升,触控输入延迟波动变大,精度下降。
建议:偶尔重启模拟器、保证电脑不过热、保持高帧率。

**Q:使用脚本有封号的风险吗?**
A:存在封号的可能性,但只要不用于冲榜,封号的概率就不大。

**Q:我发现了 BUG?**
A:可以反馈到 GitHub Issues。

**Q:如何指定打某一首歌?**
A:代码本身暂不支持直接指定某首歌,但可以手动把想打的歌加入游戏内的「收藏」,让脚本只从收藏里随机选,相当于只打那一首。

## ⚠️ 注意

1. 推荐使用最新版 MuMu 模拟器;雷电模拟器测试较少,且存在性能问题。
2. 本项目尚不完善,可能发生错误,欢迎 Issue 和 PR。

## 📝 风险、使用限制、免责声明、许可证和版权

本项目的初衷仅是作为小助手,方便各位玩家更轻松地体验游戏和养成的乐趣,禁止利用本项目从事破坏游戏公平的行为。请大家爱护邦邦游戏环境,遵守游戏规则。

请务必知悉,本项目**不能用于冲榜**。官方总是对冲榜用户进行二次检测,在模拟器环境上运行、非常规输入方式等都是高风险因素,将本项目用于冲榜几乎必然触发封号。

本项目以开源且免费的形式发布,禁止任何个人或组织以商业化方式使用或传播。

因使用或无法使用本项目所导致的任何直接或间接损失,本项目及开发者均不承担责任。用户在使用过程中应自行评估并承担全部风险。

本项目在 **GPLv3** 许可下开放源代码,修改、复制、分发请遵守[项目许可证](LICENSE)。本项目是 [EvATive7/autodori](https://github.com/EvATive7/autodori) 的 fork,再分发时需保留原作者署名与来源说明(见上方「致谢与来源说明」),并保持 GPLv3 许可。

本项目还直接引用、修改或分发了以下开源代码、组件或二进制:
- [minitouch ver.EvATive7](https://github.com/EvATive7/minitouch)(Apache License 2.0)
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)(LGPLv3)

# AGENTS.md — autodori 项目交接文档

> 给下一个接手本仓库的 AI 会话。内容 = 已验证的项目机制 + 踩过的坑 + 当前进度。
> 更新于 2026-10-17。与代码冲突时以代码为准,改完记得回写本文档。

## 每次会话必读:用户协作规则(最高优先级)

> 下面这几条本在第 10 节,提到文件最前面是因为本文件较长、注入时被截断过(第 10 节
> 只剩一句)。完整版见 **第 10 节**,两处一致,改的时候一起改。

- **优先级:需求澄清 > 正确实现 > 速度**。需求不明确必须反问:复述理解 → 列不确定点 →
  每问给 A/B 选项或建议默认值(最多 3 问)→ 说明不确认时的默认做法与风险。
- **禁止**:编造业务规则、把 UI 文案的"应该"当确定逻辑、未确认就大改代码、用"通常/一般来说"搪塞。
- **禁止**:改完就自动递增版本号 / 自动打包 —— 打包要等用户明确说。
- 回复风格:短句、一条一事、不用 emoji、不要 AI 腔;改文案照抄用户原文(含标点风格)。
- 每处改动附带背景注释(为什么这么做、踩过什么坑),这是本仓库风格。
- 用户回答后先给**简短实现计划**,再写代码。

## 0. 项目是什么

邦邦(BanG Dream!)**自动挖矿助手**:MaaFramework 4.4.0 管线 bot(`src/autodori.py` +
`assets/resource/pipeline/*.json`)+ Tkinter GUI(`gui.py`),控制 MuMu 模拟器里的游戏
自动打歌、清火、每日免费三抽。

## 1. 环境与命令规范

- 工作区 `e:/autodori-src`,Python 用 `.venv`。
- **所有命令先 `cd e:/autodori-src` 并设 `$env:PYTHONIOENCODING="utf-8"`** ——
  PowerShell 是 GBK,不设打中文会报 UnicodeEncodeError。打包还要 `$env:PYTHONUTF8="1"`。
- MuMu 12 在 `D:\MuMu Player 12`,adb 在 `nx_main\adb.exe`,地址 `127.0.0.1:16384`,
  包名 `com.bilibili.star.bili`,分辨率固定 1280x720(判定区 7 轨,lane 配置在 `util.py`)。
- 上游 `github.com/1979711854/bangdream-autodori`,`gh` 已登录。
- **bot 的 exe 从磁盘读 `assets/`**(不打进 exe):改 pipeline JSON / 模板图**不用重新打包**,
  同步文件即生效;只有改 `.py`(含 `gui.py` 文案)才需要 `build.py`。

## 2. 架构与关键路径

| 内容 | 路径 |
|---|---|
| bot 主程序(custom 识别/动作、override) | `src/autodori.py` |
| 谱面拉取与解算 | `src/api.py`(Bestdori API + diskcache)、`src/chart.py`、`src/util.py` |
| 打歌下发(动作→MNT 命令)、模拟器 IPC | `src/player.py` + `minitouchpy` |
| 挑战赛(挑战演出)专用流程 | `src/challenge.py` |
| 环境自检(分辨率/设备) | `src/envcheck.py` |
| 管线节点 | `assets/resource/pipeline/{common,live,daily,main,start,special}.json` |
| 识别模板 | `assets/resource/image/**` |
| GUI(启动子进程、写配置、说明页) | `gui.py`;说明页渲染 `ui_widgets.py` → `DocView` |
| bot 日志(阶段/异常,UTF-8) | `debug/autodori-*.log` |
| MAA 日志(节点轨迹/匹配分数/点击坐标) | `debug/maa.log` |
| 配置 | `data/gui_config.json` / `data/config.yml` |
| 战绩 | `data/play_records.db`(SQLite) |
| 打包 | `build.py`(`ZIP_FILENAME` 动态拼版本号) |
| 本机诊断脚本(gitignore,list_dir 看不到) | 根目录 `_test_*.py` `_diag_*.py` `_chk_*.py` `_exp_*.py` |

## 3. MAA 管线三条铁律(最容易踩)

1. **识别链按列表顺序命中,先中先得**。节点被检查时按 `next` → `interrupt` 拼接顺序识别,
   interrupt 只是备胎。只要 next 里有个每轮都命中的节点,interrupt 永远轮不到。
   **要抢占某个检查,必须放目标节点 `next` 的首位。**
2. **查重**:同一节点名在单个节点的 `next`/`interrupt`/`on_error` 合集中出现两次,
   会导致**整份资源加载失败**。改完必跑 `scripts/check_pipeline.py`(当前基线 97 节点 0 问题)。
3. 所有 JSON 合并为一个命名空间,节点名不能跨文件重复;无 `recognition` 的节点 = 必定命中
   (分支跳板);`roi` 是 `[x,y,w,h]` 全图坐标。**改被 override 的节点时,要去
   `_get_override_pipeline()` 确认最终定义**(freelive 改 `set_difficulty`/`select_live_mode`,
   challengelive 改 `main`/`select_song`,special 改结算三节点,daily 交替模式改结算/奖励/打崩)。

## 4. 排查「空转/卡死」标准流程

1. 看最新 `debug/autodori-*.log` 尾部 → 判断卡在哪个阶段(打歌/三抽/启动)。
   **阶段1(daily pipeline)几乎不打 Python 日志是正常的**,别以为没跑。
2. 统计 `debug/maa.log` 里 `node hit [result.name=X]` 的次数,重复几十次的就是空转节点;
   看 `run_recognition [cur_node_=X] [list=[...]]` 了解识别链,`MtouchHelper click [x=][y=]`
   看它点了哪、`all_results_` 看模板分数。
3. IPC 截图(`scripts/shot.py`)看画面 —— **不要用 PowerShell 截屏**;游戏关服后 IPC 返回
   过期帧,先用 `adb shell pidof com.bilibili.star.bili` 确认游戏活着。
4. 判因:①模态弹窗挡住点击但识别目标在弹窗外 → 每轮命中空转;②OCR/模板认不出 → 死循环;
   ③状态机在少数节点间打转。多数是 ①。
5. 修:按第 5 节加节点;**别靠猜,截图取证**。

现成的通用兜底:卡死看门狗(第 9 节第 10 条)——**画面 + 脚本心跳连续 75s 都没有进展**
才按安卓返回键(最多 2 次),两次无效就停任务。判据不是节点命中:MAA 4.4.0 不送节点
级通知(见第 9 节第 10 条),按节点数判会退化成定时器,以前就是这样在打歌中误按
返回键把游戏按成【暂停】的。

## 5. 新弹窗 / 新界面处理

1. IPC 截图,从截图**紧裁**按钮/特征区,存 `assets/resource/image/common/button/` 或对应页面目录。
2. `scripts/tmtest.py <截图> <模板>` 预验证:正样本 ≥0.95,还要拿无弹窗截图做**负样本**,
   负样本分数要明显低于阈值,否则会误点。
3. 在 `common.json` 加节点(TemplateMatch + roi 限定按钮区 + threshold;生产常用 0.8)。
   OCR 判据要实测 —— 小字/细体(如右下角「关闭」两字)ppocr 经常读不出,优先模板匹配。
4. **挂到所有相关枢纽 `next` 首位**:
   - 自由演出:`live_home_button`、`main`
   - 日常:`daily`、`daily_home`、`daily_boost_check`、`daily_pull`、`daily_pull_home`、
     `daily_pull_open_gacha`、`daily_pull_find_gacha`、`daily_pull_btn_check`
   - 启动:`start_app`
5. `scripts/check_pipeline.py` 校验 → 实测(`src\autodori.py --mode daily`)。
6. 离线回归:改 daily 逻辑跑 `_test_daily.py`;改结算读数跑 `_test_playresult.py`;
   改谱面解算跑 `_test_chart.py`;改看门狗跑 `_test_watchdog.py`。

## 6. 结算读数口径

`PlayResultRecognition` 各字段 roi 在 `types` 里;**`-1` 表示识别失败,不是数值**。三层处理:
1. 形近字符归一(`_ocr_to_int`):0↔O/D、8↔B、1↔I/l,别改回裸 `int()`。
2. 关系校验(`_reconcile_play_result`):GREAT/GOOD 全 0 → fast=slow=0;`miss+bad=0` →
   maxcombo=总音符数;k 次断连 → 最长连击 ∈ [ceil(total/(k+1)), total]。推不出就置 -1,不猜。
3. 展示层 `gui._fmt_count` 把 -1 打成 `-`。
4. 实测 `FAST -/SLOW -`:该局游戏内 FAST/SLOW **没显示**(用户关闭了显示)或数字是淡灰细体;
   OCR 读不出时用 `_binarize_result_rois` 二值化增强图重试(背景≥250 留白,其余涂黑)。
   这不是 bug,别乱改 roi。
5. 取证:`Play result raw: {...}`(OCR 原文)与 `Play result: {...}`(校验后)两行。

## 7. 打包与发版

- 打包:`python build.py --version v1.3.3-rcN --os windows --arch amd64`,产物在 `dist/`。
  build.py 最后只留 zip + 空目录,要运行先 `Expand-Archive` 解回 dist。
- **产物名 `autodori_v<版本>_Windows_X64.zip`**(`ZIP_FILENAME` 动态拼接,用户 2026-10-17 定,
  取代旧的固定名 `bangdream-autodori_win64.zip`)。README 中/英两版下载说明已同步。
- 打包后必须:**解回 dist → 两个 exe 同步到仓库根 → `Copy-Item dist\assets\build_metadata.json assets\ -Force`**。
  漏拷 metadata 的后果很隐蔽:exe 是新的,但 GUI 关于页显示旧版本号(用户会报"exe 是旧版")。
  核对:`_chk_exe.py --grep <新符号> autodori.exe`。
- 版本号:`gui.py` 的 `APP_VERSION`(兜底)+ 构建 `--version`(写 `assets/build_metadata.json`)。
- 发版文本**三处同版**:GUI 说明页 `CHANGELOG`(`gui.py`)/ `RELEASE_NOTES.md`(CI 正文)/
  `CHANGELOG.md`。当前版本在最上方,旧版本保留。条目一句话,只讲用户可见变化。
- **发版文本写法(用户 2026-10-17 定,写 Release Note / 更新内容条目前必须遵守)**:
  1. 每条一行,控制在 15~25 字,不超过两行;
  2. 动词开头,统一用:新增 / 优化 / 修改 / 修复 / 调整 / 合并;
  3. 一条只讲一件事,不要把多个改动塞进同一条;
  4. 只写结果,不写实现细节 —— 不写"怎么做的",只写"做了什么";
  5. 用正式书面语,不要过多的口语、比喻、吐槽。**禁用词:一条龙、挖矿、空转、卡死、
     看门狗、原地、顺手、干脆**(仅限发版文本,代码与文档正文不受此限);
  6. 长度尽量均匀,不要一条很短一条很长;
  7. 同类合并:界面、逻辑、修复、文档分别归拢;
  8. 不写函数名、变量名、内部实现;用户可见的文件名可以保留;
  9. 修复类统一写成「修复……的问题」;
  10. 不确定的细节不要编,宁可写概括;
  11. 先出一版再自查:有没有超过两行的、有没有口语词、有没有一条讲多件事 ——
      有就重写。

  输出格式:`## vX.Y.Z 更新内容` + 每行一条 `- [x] ……`。
  改写示例:「合并打歌入口:只保留「清理日常」,启动游戏、领签到与弹窗奖励、清火打歌、
  每日三抽一条龙」→「合并打歌入口,统一为「清理日常」」;
  「打歌中被游戏暂停时自动点「中断」退回主页」→「添加处理「暂停」界面的逻辑」;
  「优化打歌时的资源占用:看门狗在打歌期间不再额外抓图」→「优化打歌时的资源占用」;
  「修复火量耗尽后仍继续打歌、每日三抽不执行的问题」→「修复火量耗尽后仍继续打歌的问题」。
- **Release 标题格式(用户 2026-10-17 定,CI 已自动拼接,不用手改)**:
  `BanG Dream · 邦邦自动挖矿助手 <tag>`;tag 带 `-rc` 时追加 `(预发布)`,
  例:`BanG Dream · 邦邦自动挖矿助手 v1.3.3-rc2(预发布)`。
  实现见 `.github/workflows/build.yml` 的 `name:` 表达式。以前不写 `name` 时
  GitHub 直接拿 tag 当标题,与列表里历史版本样式不一致。
- **预发布正文首段格式(用户 2026-10-17 定)**:`RELEASE_NOTES.md` 开头照抄下面两行,
  版本号跟着实际版本改。**两行分开写**(GitHub 渲染成两行),不加粗,
  不写「请谨慎用于常用账号」:

  预发布版:未经充分实机测试(验证充分后再发正式版 vX.Y.Z)。
  修复 + 优化版本,配置格式无变更 —— 下载解压后覆盖到旧目录即可(data/ 与 cache/ 会保留)。
- 发版:更新三处文本 → 提交推送 → `git tag -a vX.Y.Z[-rcN]` + push → CI 自动构建发 Release。
- **用户 2026-10-17 明确:不要每次改完就自动递增版本号/打包**,等用户说"打包"再执行。

## 8. 踩坑清单(按重要度)

- **GBK 控制台**:见第 1 节。`gh --json` 重定向到文件是 UTF-16,读取要 `encoding='utf-16'`。
- **`list_dir` 遵守 .gitignore**:`_test_*`/`_diag_*`/`.venv` 看不到,找文件用 `Get-ChildItem -Force`。
- **公告类弹窗回主界面会再次出现**,直到点掉;测试时别手动帮它点。
- **README 中英双语必须同步**;内嵌图一律**相对路径 + JPEG**(PNG 曾在用户环境不显示,
  实测 5.4MB→1.1MB);新增图要 `git add screenshots/`。
- **重启 bot 时游戏停在游戏内某页 → `start_app` 原地空转**:main/daily 的 interrupt 里
  `start_app` 无 recognition 必定命中,它只认开始页菜单/自由演出首页按钮。已补三处兜底:
  `comfirm_song`(选择乐队)、`select_song`(选曲列表)、`select_live_home_back`(「选择演出」界面
  → 点左上角返回箭头回主页)。再来新界面就在 `start_app.next` 继续加。
- **modal 弹窗顶在 live 首页时,顶部标题栏 OCR 可能残留命中** → 要先挂 `tutorial_close`
  这类关闭节点(必须在 `select_live_home_back` 之前),否则会一直点被挡住的返回箭头。
- **清火收工判据是火量,不是生命值**:`HandleLiveBoost` 判定必须排在 0 火策略之前;
  `HandleLifeExhausted`(打崩一首)只代表这一首失败,还有火就继续打。
- **火罐为 0 只有一处配置**:`on_zero_boost`(stop/play/exit,见 `_zero_boost_policy`),
  GUI「自由演出设置」页 → `data/config.yml`(显示名:停在游戏主界面 / 继续挖矿 / 退出游戏)。
  **日常也读它**(2026-10-17 合并入口后);
  旧键 `play_at_zero_boost` 只在非日常的兼容路径里生效,别再往 GUI 加第二份。
- `tests/test_gui_challenge.py` 已全绿(原来那 1/4 失败是测试夹具没给
  `daily_auto_clear_fire` 属性,不是业务问题)。
- 谱面来自 `bestdori.com/api/charts/{id}/{diff}.json`,diskcache 永久缓存,已核对缓存与实时一致。
- **跨 BPM 长条换算 bug(2026-10-21 已修,源码)**:`_process_time_chart` 原来单遍
  遍历、BPM「遇到才收」,Long/Slide 跨过未遍历到的 BPM 变化时后续 connection
  按旧 BPM 换算,时间全错。缓存 1112 张谱面实测 29 张受影响(2.6%,最大偏差
  2168ms,#180/#443/#555/#568 历史小额 miss 可由此解释)。已改为先收集全部 BPM
  再统一换算。来源:issue #7 回帖(2026-10-21)第 2 点。已随 v1.3.3 正式发布。
- **歌名变体错配(2026-10-21 已修,源码)**:「ときめきエクスペリエンス！
  (月島まりなver.)」(#786/#790)被 OCR 读出完整标题(屏显繁体「麻里奈」,
  与曲库「まりな」还不一致),但匹配器让无后缀基础曲(#24)的 90 分部分匹配
  压过变体 → 整首按错谱面(10:20 场 #24 无结算即此因)。已加 `_has_title_suffix`
  + 匹配器「尾部括注分池」(与前缀分池镜像);`_test_songmatch.py` 76 项全过、
  历史语料零回归。来源:issue #7 回帖第 4 点。已随 v1.3.3 正式发布。
- **固定漏 miss 悬案(2026-10-21 已结案,根因=幻影切片)**:静态链路(手指分配/交接
  顺序/坐标/判定点总数)一直是对的,漏点在**动作生成层**:`split_number` 对非
  slice_size 整除的时长(多为浮点噪声,如 120.000000000004)切出 ~1e-12 尾片,
  时刻恰好 = 收尾 connection 时刻 +0.00001,排在同刻同指下一个 note 的 down 之后;
  间隙 10μs 不插 wait → 同一 minitouch commit → evdev 一帧内同 contact 只保留
  最终位置 → 新音符的 tap/绿条头从未在自己 lane 上出现 →「压根没点」型 miss
  (fast/slow=0、great=0,逐场固定)。已在 `add_smooth_move` 跳过 <0.5ms 尾片修复。
  离线帧语义重放(`_diag_final3.py`,生产参数 10ms 切片/批次 100)11 首(含
  #438=5/#458=11/#570=7/#629=4/#632=3/#664=6/#647=13/#463=15 及 3 首 ALL PERFECT
  负对照)漏点数**全部精确吻合**,修复后全部清零;`_test_chart.py` 21/21。
  **发布时序层已排除**:`Adjust offset` 实测每条命令开销 0.02~0.07ms、批次创新
  成本全曲仅几 ms,服务端时间线不漂;漂移日志 +2ms/批只是 Python 领先幅度增大
  (命令在缓冲区等),无害。已随 v1.3.3 正式发布。
- **FULL 曲越打越慢出 SLOW(2026-10-21 取证中,未结案)**:12 首连打
  (autodori-20261021-114335.log)里 [FULL]FIRE BIRD 后半段越打越慢
  (slow≈104/great 121)、新時代 slow=11,其余全 PERFECT;首曲 ときめき
  (月島まりなver.) fast=306 是冷启动校准问题(photogate 28→43 后未复现,
  用户确认不用管)。已排除:
  ①python 发布漂移(-1.3~-2.1ms/批,跨会话不恒定;全 PERFECT 歌同样累计
  领先 2~5s —— 是 3ms margin 的设计行为,与 SLOW 无因果);
  ②服务端 wait/间隙补偿(Adjust offset 全程平稳:wait ~0.07ms/条、
  interval ~0.01ms/条,total_cost 尖峰 12 首分布一致 ≤8ms,第 11/12 首无恶化);
  ③interval 补偿漏计(wait 生成 commit+wait、publish 每批追加 commit,按
  action 计数确实不一一对应,但实测仅 ~4.7ms/曲,对 ±60ms 判定窗是噪声);
  ④photogate 数值(自动校准第 2~12 首恒 43ms,第 12 首 42,差 1ms)。
  剩余候选=游戏侧(模拟器音频/渲染时钟相对墙钟漂移、游戏主线程停顿)。
  **已加仪器**:`_dump_server_lag_async`(src/autodori.py,play_song 正常收尾
  后调用)—— daemon 线程等 jlog 静默(≤20s)后快照 cmd_log_list,把每条命令
  的服务端实际执行时刻对齐到谱面理想时间线(wait.length 重建,不含补偿),
  输出「服务端执行滞后[歌名]: … 曲内增长=…ms」一行 INFO。lag 曲内增长 =
  wait 超时累计 + 命令间隙累计(补偿后净残差);合成 jlog 自检零失配
  (_test_lagdump.py)。**判读**:增长≈0 而 SLOW 复现 → 游戏侧实锤,只能从
  模拟器/游戏设置侧想办法;增长几十 ms → 下发链补偿不足,按斜率修。
  注意:jlog(start/end/cost)此前从不落盘、cmd_log_list 每首歌清空,本次是
  它第一次被消费;生命耗尽/暂停中断提前退出的局不采。`_diag_drift.py` 现接受
  日志路径参数(`python _diag_drift.py debug\autodori-xxx.log`)。
  **2026-10-21 用户决定**:先发 v1.3.3 正式版,问题遗留待后续修
  (Release Notes / CHANGELOG / GUI 更新日志均已列「已知问题」)。
  已发 issue #8 向用户征集日志与复现信息(标题「长曲…SLOW 判定增多」)。
  ⚠ 滞后仪器的命名错位已修(2026-10-21 下午):理想流的动作类型名
  (down/move/up)现映射为指令字母(d/m/u),失败文案也改为
  「诊断数据本局不采…不影响打歌」;`_test_lagdump.py` 合成事件改用真实
  字母并补 move 覆盖,零失配。实机已采到正常局曲线(曲内增长 9~34ms)。
  **⚠ 2026-10-21 用户报:镜像 fork(Ars1027)的包打 FULL FIRE BIRD 出现
  中途 FAST(偏早),与我们 SLOW(偏晚)方向相反 —— 两者都支持游戏侧
  时钟漂移,数据留给 FULL 曲排查用。**
- **连打第二首首音不对 → 开局全 miss → 生命耗尽(2026-10-21 已修一类,
  #462 仍遗留)**:根因 = 冻结完成后、真首音到来前,演出画面才激活/转场,
  检测带出现整带跳变被当成首音 → 时间线整体早 2~3s → 开局全 miss。
  修复(commit 78411b9):越阈值时按轨道分布判别 —— 音符只占 1~2 轨,
  场景切换同帧改动 ≥5/7 轨(SCENE_MIN_LANES)→ 重置基线继续等真音符;
  触发行日志现带「N/7 轨变化」。修复后连打多数歌正常。
  **遗留**:シル・ヴ・プレジデント(#462)仍每次崩,其假触发呈 2/7 轨
  (音符形态,判据抓不到),触发前后日志与正常歌无差别,锚点偏早/偏晚
  未定。已加取证(commit b4f32c6):wait_first_note 在起始帧与触发帧
  异步截图存 `debug/firstnote/`(纯诊断不碰时序);生命耗尽提前退出局
  也输出服务端执行滞后(此前只有正常局才有)。**下一步 = 用 #462 崩局
  的截图 + 滞后数据判明锚点方向再修**;截图目录会积累,注意清理。
- **#655 / #144 固定 miss=1 已结案(2026-10-21,非 bot bug)**:
  シカ色デイズ(#655-hard)恒 perfect=486+miss=1、ツナグ、ソラモヨウ
  (#144-hard)恒 perfect≈431+miss=1,战绩库多月 5/5 局复现,fast/slow
  波动而 miss 纹丝不动。证据链:①修复后帧语义重放全判定点覆盖 0 漏
  (bot 对它知道的每个音都点了,非幻影切片复发);②#655 谱面 JSON
  判定点 486 < 游戏结算 487 —— 游戏有一个 note 数据里没有;③#144 总数
  对上(432=432)但结合恒 miss=1,判定为数据里有一个 note 的轨位/时间
  与游戏不符;④Bestdori 线上与缓存逐字节一致(排缓存过期);⑤JSON 的
  System 条目只有 BGM/Fever 音频事件,无隐藏音符;⑥补充:#655 结算
  maxcombo=430 → miss 在第 431 个音符(约全曲 88% 处),之后 55 连未超
  430,单点结构性缺口自洽;⑦判定点数已独立复算(不经过 bot 解析器,直接
  数原始 JSON 的 connections):#655 = Single 315 + Long 连接点 80 +
  Slide 连接点 91 = **486,游戏 487,数据缺 1 实锤**;#144 = 432 = 游戏
  432,总数吻合 → 属数据错位型(轨位/时间录错)。⑧同类的还有
  Hacking to the Gate(#16-hard):战绩库 7 局恒 miss=2、maxcombo 恒 229
  (miss 在第 ~230 音符),JSON 判定点 339 = 游戏 339,总数吻合 → 数据
  错位型,与 #144 同类。**结论:Bestdori 社区
  谱面数据与游戏实际谱面存在差异,bot 侧无法可靠修复(瞎补位置反而可能
  多错),不要重复排查。**可向 Bestdori 报数据问题;人工确认办法 = 录
  bot 打 #655 的视频,看全曲 ~88% 处是否有音符无人触碰。
- **⚠ `assets/minitouch_EvATive7/` 不在 git 里**(下载产物,gitignore):
  被删后 build.py 报 "no usable binaries" 且 minitouch.zip 本机下载有证书
  问题。恢复办法:从任一已发布 zip 里取 `assets/minitouch_EvATive7`
  (2026-10-21 曾从 rc2 包恢复过一次)。
- **v1.3.3 发布状态(2026-10-21 晚)**:用户删过远端 tag+Release 后重发 ——
  tag v1.3.3 现指向 78411b9(含首音轨道判别修复),CI 自动重发 Release。
  b4f32c6(首音截图取证)在其后,**不在 tag 包内**;本地 dist 的 zip 是
  最新的。下次发版时把 tag 移到最新提交即可(用户删过旧 tag 的先例)。
- git 提交用 Conventional Commits 中文描述;注释写清背景原因(本仓库风格)。

## 9. 2026-10-17 的改动与当前状态

已完成(v1.3.3-rc1/rc2/rc3 已打包过,弹窗+看门狗这轮**未打包**):

1. **日常:先打一首查三抽,确认完成后连打不再回主页**(`_DAILY_PULL_EACH_SONG`)。
   - 阶段A(`_daily_song_stop=True`):main 每首一停 —— `save_succeed_playresult`/
     `event_reward_confirm`/`handle_life_exhausted` 被 override 到 `daily_song_done`
     (回主页即停),Python 层打完一首就 `_pull()` 查三抽。
   - 三抽确认完成(`_daily_pull_done`,done 或 locked 都会置位)后进入阶段B:
     `_daily_song_stop=False` → 三处 override 不再注入,main 走 live.json 原生连打链
     (结算 →「再次演出」→ 选曲 → 下一首),一次打到火尽,**不再逐首回主页**
     (用户 2026-10-17 反馈"每首回主界面太麻烦")。
   - 连打收工点在选曲确认页 → `_return_to_home()`(`post_task("activity_back_to_home")`)
     退回主页,再 `_pull("收尾复核", force=True)`。
   - 安全上限 `_DAILY_MAX_ROUNDS=60` 只约束阶段A。
   - ⚠ 阶段B(连打)与 `_return_to_home` 尚未端到端实测。
2. **火罐为 0 直接停日常**:入口 `daily_boost_check`(`HandleDailyBoostZero`,主页顶部 OCR 火罐)
   + 打歌中 `HandleLiveBoost`;两条路都 `stop` 且不跑后续三抽;OCR 读不出(-1)时放行。
3. **「选择演出」界面点开始日常空转**:新增 `select_live_home_back`(OCR「选择演出」→ 点左上角
   返回箭头);daily 模式 override 去向 `daily_home`,main 模式去向 `start_app` 重试。
4. **「想精进演出技巧的话」弹窗卡死**:新增 `tutorial_close`(模板匹配右下角「关闭」),
   挂 6 个枢纽 next 首位。
5. **卡死看门狗**(用户定的 L2 机制,方案 1A):`_StuckWatchdog` + `_watchdog_loop` 线程。
   最初的节点命中判据在本 MAA 版本收不到通知、实际无效,现已改成「画面静止 + 脚本心跳
   连续 75s 无进展」→ 按返回键(最多 2 次)→ `post_stop`,详见第 10 条。special 模式不启用。
   **不做 L3 任务级总超时**(用户明确)。
6. **FAST/SLOW 显示 `-`**:加 `_binarize_result_rois` 兜底(原图读不出才用增强图)。
7. **zip 命名**统一 `autodori_v<版本>_Windows_X64.zip`。
8. **结算页改点「再次演出」,「确定」彻底不点**(用户 2026-10-17 实况:竞演活动的
   「活动分数」结算页右下并排「再次演出」+「确定」,旧链先落到 `confirm_button`
   → 点「确定」→ 触发活动剧情 → 那屏所有识别全不命中 → 永久卡住)。
   新增 `activity_liveagain`(点再次演出)→ `activity_back_to_home`(左上返回箭头,
   自循环走两级:选曲页→选择演出页)→ `activity_select_song_back`(OCR「选择乐曲」
   兜底)→ 主页(`daily_song_done` 改成只认主页「演出」按钮再 stop,不再点击)。
   交替模式 override 的 `save_succeed_playresult.next`/`event_reward_confirm.next` 已接上。
   **⚠️ 这段「再次演出 → 返回 → 主页」尚未端到端实测**(要打完整一首歌才会出现该页)。
9. **剧情界面兜底**:实测 `确定` 之后那屏就是标准剧情阅读界面 —— 右上「← 菜单」
   (`common/reader/menu.png`,命中 0.955)→ 工具条「▶▶| 跳过」(`common/reader/skip.png`,
   1.000)→ 弹窗粉色「跳过」(`common/button/skip/pink.png`,0.9998)→ 直接回主页。
   现有 `reader_menu → reader_skip → skip_button` 就是它,已挂到 `start_app`/`daily`/
   `daily_pull`/`daily_pull_find_gacha`/`daily_pull_btn_check`/`save_succeed_playresult`;
   `reader_menu` 加 roi `[1000,0,280,120]` + threshold 0.8(避免命中工具条里那个「菜单」)。
   另加 `story_loading_wait`(OCR 底部 `LOADING`/`.*MB/.*`):剧情**资源下载中**时工具条
   根本不出来,只能等;该节点每 3s 命中一次原地等,下载完自然落到「菜单→跳过」。
10. **看门狗改成「画面静止」判据(2026-10-17 二次重写,重要)**:
    - **踩坑**:MAA 4.4.0 **只把任务级通知(`Tasker.Task.*`)送到 Python,节点级
      (`Node.Recognition.*` / `Node.NextList.*`)一条都收不到** —— 实测脚本
      `_diag_notify.py`(根目录,gitignore)跑一个必然命中的节点,只打印两条 Tasker 事件。
      所以最早的「同一节点 60s 内命中 ≥8 次」从来没生效过;本轮加的「90s 无任何节点
      命中」则退化成「启动 90 秒后按一次返回键」,而**打歌中按返回键 = 游戏弹【暂停】**,
      2026-10-17 12:59 用户报的"莫名其妙暂停"就是它(obj log:
      `卡死看门狗:90s 内没有任何节点命中...按返回键(1/2)` 之后 3 秒 `pause_check` 命中)。
    - **现在**:每 2s 抓一张 8 像素步长的粗采样图,画面有变化 → 刷新活动时间;
      打歌线程每批音符 `_STUCK_WATCHDOG.ping()` 刷新活动时间;任务级通知也 ping。
      连续 `_WATCHDOG_STATIC_S=75s` 既没画面变化也没 ping → 按返回键(最多 2 次,
      每次按完再给 75s 观察窗口)→ 仍无变化 `post_stop`。
    - 抓图有 `_SCREEN_CAP_LOCK` 串行化(打歌线程的 `_grab_screen()` 与看门狗线程共用)。
    - **打歌期间看门狗的额外抓图 = 0**:脚本侧 10s 内有 ping 就直接早退、连图都不抓
      (`_WATCHDOG_BUSY_QUIET_S`);打歌线程本身也改成**每拍只抓一张图**,生命检测与
      暂停检测共用同一帧(实测 MuMu IPC 抓图 mean 5.2ms / max 8.1ms,粗采样 0.16ms ——
      一秒抓两张纯属浪费,还会吃掉下一批音符的时间预算)。
    - 剧情资源下载时进度数字在变 = 有变化,继续等;真卡 0% 才会被返回键救出来(刻意)。
    - ⚠ **不要再给看门狗加依赖节点通知的判据**(这个 MAA 版本收不到)。

11. **打歌中「暂停」弹窗 → 点「中断」**(用户 2026-10-17 定):弹窗是「暂停 / 演出暂停了。
    确认重新进行演出吗?」+ 三个键 中断(白)/重新开始(白)/继续(粉)。两步走:
    中断 → 二次确认「确认中断当前演出返回主页吗?」→ 粉色「中断」→ 回主页(实测)。
    - 打歌**途中**必须靠 Python 检测,因为 Play 自定义动作阻塞期间 pipeline 的
      interrupt 不生效(与生命耗尽同理):`play_song` 的 1s 检测节拍里加了
      `_pause_dialog_on_screen`(模板 `live/button/pause_interrupt.png`,即白色「中断」
      按钮,roi `[230,395,300,120]`,实测本弹窗 1.0000、普通画面/二次确认弹窗都远低于阈值),
      命中则 `_interrupt_paused_live()` → 抛 `LivePauseInterrupted` → Play 返回失败走 on_error。
      **别用 `_run_node_once`(run_action)去点**:run_action 不带识别结果,Click 拿不到框,
      退化到 (0,0) 乱点(2026-10-17 实测日志 `MtouchHelper::click [x=0][y=0]`)。
      现在是"自己 run_recognition 拿 box → `post_click(box 中心)`"。
    - 打歌**前后**(等开演/等结算)由管线节点 `live_pause_interrupt` → `live_pause_confirm`
      处理,已挂 `wait_live_start`/`playsong`/`wait_playresult`/`wait_playresult1` 的 interrupt 首位。
    - `live_pause_confirm` 用 OCR「中断」(roi 罩粉色键 `[640,410,270,95]`):不能用模板,
      粉键在第一个弹窗里是「继续」,实测同模板在暂停弹窗上 0.9138,会点错。

12. **合并「开始自由演出」与「日常」**(用户 2026-10-17 定):两个入口本来是同一套
    选曲/难度/清火逻辑,只留日常。
    - GUI:顶栏按钮 → 「开始日常」(`_bot_args` 里 main 分支删除,日常传 `--difficulty`);
      左栏词条一律用「**自由演出设置**」(2026-10-17 二次改名,之前一度叫「打歌设置」)
      与「**清理日常**」(之前叫「日常」);挑战演出 / 超高难度活动的独立按钮不动。
    - bot:`--mode daily` 现在读 `--difficulty`(**合并前日常难度恒为模块默认 hard,
      GUI 选 expert 完全不生效**,这是真 bug);`--mode main` 保留给命令行与挑战演出。
    - 「火罐为0时」三态 `on_zero_boost`(stop/play/exit),只留在「自由演出设置」一处,
      日常也读;`play` = 0 火继续打刷奖励。旧配置 `play_at_zero_boost` 对日常仍按
      "火尽即停"迁移(不改老用户行为)。显示名 2026-10-17 二次改为
      **停在游戏主界面 / 继续挖矿 / 退出游戏**;`gui.LEGACY_BOOST` 负责把老显示名
      映射到新名字(gui_config.json 存的是显示名,每次改名都必须补映射)。
    - 三抽不再每首盲跑:管线 `daily_pull_done`/`daily_pull_locked` 命中后由
      `MarkDailyPullDone` 置 `_daily_pull_done`(带 `custom_action_param.reason`
      = done/locked,日志分别报"次数已抽完"/"需先完成每日演出")。
    - 死配置 `daily.enabled` 已从 GUI/`config.yml` 移除(bot 从来没读过)。

13. **日志与 GUI 可读性**(用户 2026-10-17 要求):
    - `configure_log()` 启动时调 `_purge_old_logs()`:删 `debug/` 下超过 7 天的
      `autodori-*.log`(`_LOG_KEEP_DAYS`),当天新文件不在范围;`maa.log` 与人工
      截图(`*.png`)不碰。顺手 makedirs,FileHandler 不再因目录缺失崩。
    - GUI「运行日志」白名单 `gui.AutodoriGUI._KEEP` 补齐日常阶段事件(日常开始 /
      自动清火 / 每日免费三抽 / 三抽已确认完成 / 火罐不足 / 收尾回主页 / 环境自检 /
      已清理 / 识别到邦邦实例);新增 `_NOISE` 黑名单(Unknown type / 改用 adb tap /
      Adjust offset),无论级别都不进界面,但仍完整写进 `_raw_log` 与导出文件。
    - `ui_widgets._log_wrap()`:日志消息超过 `LogConsole._wrap_limit()`(按控件实际
      宽度算)时,在**中文标点之后**软换行、续行缩进两格。断点必须占满 40% 宽度,
      否则宁可交给 Text 自动折行 —— 避免出现「…「高性能」/ 计划。」这种断法。
      只影响界面显示,导出日志仍是原始整行。

14. **火罐为 0 的三态与说明页滚动**(用户 2026-10-17 下半场):
    - 选项名改为 **停在游戏主界面 / 继续挖矿 / 退出游戏**(配置值仍是 stop/play/exit)。
      下拉下面那行解释文字已删(用户要求"提示词都删掉")。
    - `stop` 现在**真的退回游戏主界面**:管线里的 `HandleLiveBoost` 只 `_run_node_once(stop)`,
      落点是选曲确认页;由 `_run_daily_flow` 在任务返回后调 `_return_to_home("火罐不足")`
      (`post_task("activity_back_to_home")`)补齐。不要在 custom action 里同步 post
      另一个 task —— 当前任务还在跑,容易死锁。
    - `ScrolledFrame(show_scrollbar=True)`:`docs.*` 页面启用右侧常驻滚动条并设
      `yscrollincrement=32`(默认按视口比例,长文档一滚半屏);其他页面仍隐藏滚动条。
    - `CHANGELOG` 已补 v1.3.3 全部条目(含本轮 8 项)。**版本号未递增**(用户要求
      确认后再打包、不要改版本号)。

15. **断头弹窗兜底 + 掉线容错日志**(2026-10-19,未打包):
    - **根因**:弹窗类节点(tutorial_close/song_announce_confirm/skip_button/
      close_button/ok_button/confirm_button/next_button/tap_to_next/
      story_loading_wait 等约 10 个)**没有 next**,main 链中途命中会把任务
      "成功"带走 —— 纯清火模式一首没打就静默收工(实测 12:45 教程弹窗在
      点「演出」后弹出,点掉后 main 4.87s 直接 Succeeded;12:41 首次启动
      也是它,教程弹窗不是每次都弹所以时好时坏)。
    - **修复(Python 层兜底,不动管线)**:`_run_daily_flow` 阶段A 与纯清火
      分支以 `_song_play_seq`(play_song 真正开打才 +1)是否增长判"打没
      打歌",没增长就重试 post main,连续 `_MAIN_NO_PLAY_MAX=3` 次才收工
      (防异常画面死循环)。重试日志含白名单词「提前结束」,GUI 可见。
    - **掉线容错**:`Play.run` 捕获 `OSError` 时打「模拟器连接断开,打歌
      中止」—— MuMu 掉线后 minitouch 的 socket sendall 报 WSAEINVAL
      ([Errno 22]),是环境事件不是代码 bug;栈在 MAA C 回调边界只剩两行,
      不再打 stack_info。
    - 断头节点们按语境补 next(包装节点方案)仍可做,但有 Python 兜底后
      优先级降低;若以后再出现"重试 3 次收工"的日志,再按场景补链。
    - `_test_daily.py` 桩已适配(main 返回模拟"至少开打一首")并新增
      断头重试用例,基线 117 断言 0 问题。

待办/未决:
- 打包这轮改动(等用户指令);`.py` 改动必须重打包才进 exe,管线 JSON 立即生效。
- **实测日常整轮**(难度是否真按界面所选、三抽标记是否生效、0 火三态行为)。
- **实测连打阶段与收尾回主页**(2026-10-17 新增:第 1 首后查完三抽,看第 2 首起是否
  不再回主页;火尽后 `activity_back_to_home` 能否一路退回主页再进三抽)。
- **实测「再次演出」落点**(`--mode daily` 跑一轮,看 `activity_liveagain` 之后的节点轨迹)。
- **实测暂停中断链**(打歌时手动按模拟器右上暂停键复现;确认 OCR「中断」读得出、能回主页)。
- 固定漏 miss 悬案已结案(见第 8 节),待实机回归确认 miss=0 后打包。

## 10. 用户协作规则(必须遵守)

> 本节的精简版已前置到文件最顶部(那里不会被截断),改本节时记得同步顶部。

- **优先级:需求澄清 > 正确实现 > 速度**。需求不明确必须反问:先复述理解 → 列不确定点 →
  每问给 A/B 选项或建议默认值(最多 3 问)→ 说明不确认时的默认做法与风险。
- **禁止**:编造业务规则、把 UI 文案的"应该"当确定逻辑、未确认大改代码、用"通常/一般来说"搪塞。
- 低风险细节(命名/格式/注释)可自行处理,但要在回复里说明做了哪些小假设。
- 用户回答后先给**简短实现计划**,再写代码。
- 用户反感 AI 腔与冗余:短句、一条一事、不用 emoji;改文案照抄用户原文(含标点风格)。
- 每文件改动附带背景注释(为什么这么做、踩过什么坑),这是本仓库风格。
- 不要每次改完就递增版本号/打包。

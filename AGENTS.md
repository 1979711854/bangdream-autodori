# AGENTS.md — autodori 项目交接文档

> 给下一个接手本仓库的 AI 会话。内容 = 已验证的项目机制 + 踩过的坑 + 当前进度。
> 更新于 2026-10-29。与代码冲突时以代码为准,改完记得回写本文档。

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
| 本地谱面覆盖(数据侧修谱面) | `assets/chart_overrides/<曲id>-<难度>.json`(完整谱面,`get_chart` 优先于缓存加载;说明见该目录 README) |
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
| 回归测试(已入库,可 `git ls-files '_test_*'` 核) | 根目录 `_test_*.py`(20+ 个) |
| 本机一次性诊断脚本/截图(**不入库**) | `debug/local/`(2026-10-28 从根目录归拢 111 个);根目录另留少数已入库的 `_diag_*.py`(`lagsim`/`gap`/`tpl` 等) |

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
才分轮解救:①置 `_STUCK_EVENT` 通知等待循环自行退出(协作式,post_stop 停不掉运行中的
CustomAction,只有循环自己能带自己出去)→ ②点「演出失败→退出」等已知弹窗按钮(纯按钮
弹窗不响应返回键)→ ③按安卓返回键 → 无效就停任务。判据不是节点命中:MAA 4.4.0 不送节点
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
7. **弹窗内的按钮不要用固定 target 坐标点**(2026-10-26 event_reward_confirm 踩坑:
   「获得报酬」弹窗版式随活动类型变化,固定 (640,545) 点不中 OK(实际在 y582-619),
   弹窗留在屏上 → next 全不命中 → main 链结束 → 日常直接收工退出)。正确写法:
   OCR/模板认出弹窗 → next 到模板节点点按钮(`event_reward_confirm`(只识别) →
   `event_reward_ok`(TemplateMatch ok_button) → 原 next 链)。屏幕边缘固定 UI
   (返回箭头/左下图标/屏幕中心)用固定坐标没问题。

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

- **崩局收尾被一个死节点判死(2026-10-29 已修,待实机验证)**:现场
  `autodori-20261029-121834.log` 12:35 场 #606 V.I.P MONSTER 崩局。`life_exhausted_exit`
  的 OCR 明明命中「退出」(0.9996) 并点掉了弹窗,但 `next` 上的 `life_exhausted_confirm`
  还是 `expected:"退出"`,而它的 `roi [630,395,280,90]` 在那个弹窗上覆盖的是
  「重新开始」「继续」—— `maa.log` 里连续 5 次只读到「斤开始 / 继」,5 秒
  `Task timeout` → `on_error: stop` → 整局清火停止,后面 3 次 main 全部
  「未进入演出」(游戏停在得分页)。用户观感是「歌还在、bot 不动」。
  修法:删掉该节点,`life_exhausted_exit.next → handle_life_exhausted`、
  `on_error → main`;Python `HandleLifeExhausted` 开头加 `_force_leave_live()`
  三轮兜底(还在「演出失败」弹窗就再点一次退出 / 掉到得分页点白色「确定」/
  都认不到放行)。
  **通用教训:expected 与 roi 覆盖的按钮不一致的节点 = 永远不会命中的死节点,
  只能靠 timeout 走 on_error。改链路时顺手核一遍 roi 与 expected 是不是同一个按钮。**
- **「固定漏 miss」的两类真因(2026-10-29 排查)**:
  ① **全局偏移卡在判定边界**(#146 新宝島-special):16:55 局 `gate=50` → miss 1、
  maxcombo 446;17:18 局 `gate=60` → **AP(598 全 PERFECT)**。两局动作数/命令数
  完全相同(14012 / 20575),dump 失配 0、曲内增长 +0.1ms,唯一差别是 gate 差 10ms。
  结论:这类「固定漏」不是谱面或手势的结构问题,是那个判定点恰好压在窗口边缘,
  gate 落在同一侧就每次都漏。**判据:dump 干净 + fast/slow=0 + 只漏单个
  → 先怀疑 photogate,别改谱面。**
  ② **同帧 down 与 move 覆盖起点位置**(#304 SENSENFUKOKU-special,两局都是
  miss 2 / maxcombo 970):第 971 判定点 = 161 连接长滑条的起点,离线动作是
  `97800.0ms down f1 (641.5, lane3)` 与 `97800.0ms move f1 → 447.85 (lane1.95)`
  同一时间戳 → 同一 minitouch commit 里同 contact 只保留最终位置 → 游戏看到
  「按在 lane1.95」→ 起点直接 MISS(与幻影切片同一机制,见 chart.py 注释)。
  #146 的第 447 点、#409 的第 1052 点是跨指型(另一根手指同帧 move/down),但零 miss
  的 #522 也有 236 处同类同刻冲突,跨指型只是嫌疑。
  **修法(未做)**:`chart.py` 生成命令时,若 down/up 与同刻 move 共存,在 down/up
  后强制插一次 `commit()`(不插 wait),让判定点独占一帧;只影响冲突时刻
  (#304 是 44 处、#522 是 42 处)。
- **photogate 是「整首偏移」,不是「曲内漂移」,但会连着几首刷 FAST(2026-10-29)**:
  gate=30 的场次 `#342 徒花` great 598 全是 FAST、`#711` fast 698、`#145` 377、
  `#117` 455;gate=55~60 的场次 fast=0。同一批歌的 dump 曲内增长只有 -6~-28ms,
  撑不起这些 fast —— 根源是 GUI 自动校准每首最多 ±10ms,从 30 爬回 55 要走 3 首。
  已改:`CAL_STEP_MS_HIGH=30` + `CAL_HIGH_RATIO=0.5`,`(fast+slow)/总判定 ≥ 50%`
  (一边倒)时步长上限放到 30,常规仍 10;方向反转减半不变。
  **判读:游戏里第一个音符就一片 FAST = gate 偏小;前半段干净、后半段才变快 = 曲内漂移。**
- **「实测首音锚点」目前是空转,不要开(2026-10-29 评估)**:15 次实测(3 份日志)
  **全部回退或跳过** —— 9 次「超限,回退常量」(实测 86~231ms vs 常量 23~50ms,
  差 46~202ms),2 次「速度不可信(样本2)」。反证:#146 在 `gate=60` 时若真要
  86~93ms 还该偏早 26~33ms,实际却 AP → 实测值系统性高估(触发帧 y 是差分重心,
  长条/密集音符的重心不在音符头部;速度只用单样本差分,噪声大)。
  成本是每次首音多抓 1~2 帧 IPC。**结论:当前口径未通过验证,保持默认关闭;
  要救冷启动/失败局,先按 `debug/firstnote/*-anchor.txt` 的曲线改测量口径。**
- **下载链补偿的测量端会退化,两个方向都飘(2026-10-28,诊断已加、修法未定)**:
  同一天两场:
  ① `autodori-20261028-111256.log` #148 ゼッタイ宣言越打越快:dump
  `lag@10%=-1.2 → @100%=-99.2`,音符曲内增长 **-98ms**,结算 `fast=324 / miss=11`;
  而在线反馈全程只报"残差 ±5ms"。
  ② `autodori-20261028-112711.log` #6 テレキャスタービーボーイ:在线 lag 从 34.1ms
  **单调涨到 243ms**,保护② 丢弃 75 批(几乎每批),dump 报 `失配 477/14210` 跳过;
  结算却是 `fast=54 / slow=0`(若真滞后 243ms 该出 SLOW)。
  结论:在线 lag 不可信,**`wait_ideal_ms` 队列空时退回命令串实际值**(该值已含补偿,
  比理想短)是主要入口 → 把漂移吃成 0 或让 ideal 指针虚高。两个方向的根因是同一个。
  离线证据:重放 `chart.py` 的 wait 分支(#148 实发比理想短 **556ms**,#4 短 266ms,
  #4 靠 `_adjust_offset` 追平、#148 只追回 458ms)。
  **已加诊断(只读,不动时序)**:`_lag_fb` 新增 `wait_recv`(成功配对条数)/
  `fallback_n`(队列空兜底条数);曲末多两行 ——
  `滞后反馈汇总: … wait 回执 N 条, 队列空兜底 M 条` 与
  `滞后反馈对照[歌名]: 在线 lag 终值 Xms, dump lag@100%=Yms, 差值 Z; …`。
  **判读**:M 远小于 N 且差值大 → 队列配对/丢回执主导;M≈N → 兜底分支主导,
  改测量口径(换成不依赖逐条配对的累计指针)。
  相关保护:`_LAG_FB_MAX_BATCH_INJECT_MS=30`(保护②,挡正方向虚高)、
  `_LAG_FB_MAX_BACKLOG=400`(保护①,挡回执丢失)。**保护②只挡正方向,
  负方向(越打越快)它拦不住。**
- **打完第一首不抽三抽(2026-10-28 已修)**:阶段1(daily)的 `start_app` 兜底在
  「游戏没停在主页」时会把那一首直接打掉(`autodori-20261028-111256.log`:
  11:13:24 开打 → 11:16:34 结算 → 11:16:52 才 post 第一个 main),那首走 daily
  任务、不经过阶段A 的 `_pull` → 三抽漏掉直接进连打。已修:进阶段A 循环前若
  `_song_play_seq > 0` 先补一次 `_pull("第 N 首打完(启动阶段)")`,确认完成直接
  进连打。回归 `_test_daily.py` 130 项。
- **结算弹窗点不掉 → 连打中断 + 三抽在弹窗上乱跑(2026-10-28 已修,待实机验证)**:
  现场 `debug/autodori-20261028-100925.log`。连打最后一首结算后 `event_reward_confirm`
  (OCR「获得报酬」0.9978)命中,但 `event_reward_ok` 只配 `ok/white.png`(181x37),
  连认 3 次都是 0.4579 → 3.5s 后 `Task timeout [event_reward_confirm]` →
  `Tasker.Task.Failed`。**maa 的 `Job.get()` 不抛异常**
  (`.venv/Lib/site-packages/maa/job.py:54`),外面那层 try/except 抓不到,
  Python 把失败当"连打正常结束" → `activity_back_to_home` 认不到返回箭头空转 20s
  → `_pull("收尾复核")` 照跑,而 `daily_pull` 是无 recognition 的跳板(必定命中),
  把弹窗里 154x42 的白色「确定」当 `song_announce_confirm`(0.99999)点掉 →
  该节点无 next,任务"成功"结束 —— 三抽一次没做,日志却已打「执行每日免费三抽」。
  修复:①`event_reward_ok` 模板补 `common/button/confirm/white.png`,加
  `roi [400,400,480,280]` 与阈值 0.8,next 补 `next_button`(弹窗点掉后若在
  活动「等级 EXP」页,靠 OCR「下一步」推进);②新增 `daily_pull_precheck`
  (认主页「演出」按钮,timeout 5000)挡在三抽前,认不到就跳过并告警;
  ③Python 统一走 `_task_failed()` 看 `job.failed`,阶段1/main/连打/回主页/三抽
  都会留警告。回归 `_test_daily.py` 129 项(新增 1e 小节)。
- **⚠ 同一坑的第二次翻车:`rankup_confirm` 绝不能挂进结算链(2026-10-28 当天)**:
  按上面的思路把 `rankup_confirm`(补了 `confirm/white.png` 模板)挂到
  `save_succeed_playresult.next` 首位后,下一场直接终止
  (`autodori-20261028-105318.log`)。活动结算顺序实为:SS 评分页 →「下一步」→
  **等级 EXP 结果页**(整屏白底,显示「等级 181 / 还需要 75280」)→「下一步」→
  得分页(右下「再次演出」+ 粉色「确定」)。白底页上 `rankup_confirm.png`(232x58
  白底黑字)和 `confirm/white.png` 双双误命中(0.9928 / 0.9895),(524,497) 是
  角色卡牌下方的纯白区域;它抢在 `next_button` 前面点了空白处,画面不推进 →
  20s `Task timeout [rankup_confirm]` → 整场终止。**判据:白底黑字按钮模板在
  整屏白底的页面上相关系数极高,不能拿它当"哪一页"的判据,只能当"点哪个位置"
  的判据,且必须在确认是那个弹窗之后才用。** 正确做法:结算链交给
  `interrupt` 的 `next_button`(OCR「下一步」)推进,`rankup_confirm` 恢复只认
  专用模板、只留在 `liveagain.next`。截图取证:`debug/local/now.png`(等级 EXP 页)、
  `debug/local/after1.png`/`after2.png`(得分页)。
- **三抽前置校验反而跑两遍、跳过被当成收工(2026-10-28 晚,已修,待实机验证)**:
  `daily_pull_precheck.next` 原来直连 `daily_pull` —— 后者是无 recognition 的跳板
  (必定命中),于是 post 一次 precheck 就执行完整套三抽,Python 随后又 post 一次,
  每轮跑两遍(`autodori-20261028-180506.log` 两行「今日次数已抽完」;
  maa.log 18:08:30 与 18:08:50 各一轮)。连带的第二个问题:链内任何失败都被记成
  「三抽前置校验任务失败 / 当前不在游戏主界面」,但 18:01 场 precheck 明明命中了
  主页按钮,失败发生在抽卡页的 `story_loading_wait`;而 `_pull` 返回 False 会让
  阶段A break,整场只打一首就收工、火剩 12。修复三条:
  ①precheck 的 next 只留 `stop`(只当阀门);
  ②`_pull` 返回三态 ok/skip/fail —— skip(不在主页)只跳过本轮三抽、继续打歌,
    fail(三抽链自身失败)才收尾;
  ③三抽链改用专用 `daily_pull_loading`(加载完回 `daily_pull_find_gacha`),
    不再挂 `story_loading_wait` —— 它在抽卡页会误命中「NOWLOADING… / 5MB/5MB」,
    而 next 只回剧情链,3s 超时把整轮三抽带走。
  回归 `_test_daily.py` 139 项。
- **photogate 手动改的值会被下一首结算改走(2026-10-28 晚,已修,待实机验证)**:
  18:05 场实测:第 3 首全 PERFECT(校准不动作)→ 手动设 75 → 第 4 首结算 slow=7
  → 自动校准又改成 74(次日 18:18:04 写回)。手动值不设防,用户看起来就是
  「改了没用 / 自己变了」。修复:手动改值(含 ± 按钮、点开始时的强制 commit)与
  「恢复默认」= 锁定 —— 自动关掉「自动校准 photogate」(CAL 行写明,开关视觉同步,
  要恢复重新勾选),并清零方向记忆(`_cal_direction_prev` / `_last_cal_direction`,
  否则接手的第一首会拿旧方向判「反转」只走半步)。
  另把 `CAL_STEP_MS` 15→10:18:01 场 gate=76 一步 +15 直接过冲到 91(下一首
  slow=134),再花两首 4/5ms 才回到 82(全 PERFECT)。回归 `_test_gate_cal.py`
  15 项,含 91→87、87→82 的实机轨迹复现。
  **重新勾选「自动校准」后的行为(用户 2026-10-28 问)**:从**下一首结算**起恢复
  自动校准(当前这首不参与);方向记忆已清零,第一步按满步长走,不会被「反转
  减半」压成半步。
- **「实测首音锚点」(L2)做成 GUI 开关(2026-10-28 晚)**:以前只能手改
  `data/config.yml` 的 `timing.measure_anchor`,每次测试都要重配。现时机校准页
  多一个开关(`_on_measure_anchor` → `_write_timing()` 合并写,与 photogate 用
  同一套落盘逻辑,不会抹别的键);bot 每首开打前 `_reload_photogate()` 重读,
  **下一首生效,不用重打包**。环境诊断也加了一行「首音锚点实测=开/关」。
  回归 `_test_gate_cal.py` 30 项(含真 Tk 渲染该页)。
- **崩局(首音后崩盘)诊断(2026-10-28 晚,新增只读取证)**:18:30 场 #189-special /
  #413-special 两首都在中途「演出失败」,而下发链干净(曲内滞后 -6.1 / +0.6ms、
  dump 失配 0)、生命条检测 31/32 次亮度预检跳过 —— 事后只剩一条「演出失败」OCR
  文本,分不清是时间线整体错(锚点)还是某一段崩(手势/游戏侧卡顿)。现补三样,
  全部复用打歌循环**已经抓到**的帧(不额外抢 IPC,理由见 `_save_frame_async`):
  ①首音触发后第一道 ≥800ms 的生命检测节拍,存
    `debug/firstnote/<HHMMSS>-firstnote+<ms>.jpg`(第一批音符已过判定线);
  ②崩局瞬间存 `debug/crash/<HHMMSS>-<曲id-难度>-crash.jpg`,并 OCR 左上角分数
    (`_CRASH_SCORE_ROI`,避开 C/B/A/S/SS 评级条);
  ③一行 INFO「崩局诊断: 已打 Xs · 发布批次 N · 指令索引 i/总数 · 在线 lag · 画面分数」
    + 同名 txt(kind/band/轨数/底噪/触发帧 y)。
  **判读**:分数几乎为 0 → 时间线整体错(对照 trigger 与 firstnote+Xms 两张图,
  锚点偏早则判定线上没音符、偏晚则音符已压在判定线下方);分数涨过一大截再断
  → 某一段崩。回归 `_test_crashdiag.py`(13 项)。
- **开局首音误锚 → 整曲 miss(2026-10-27 加可信度门,尚未实机验证)**:23:24 场
  #3-special ぽっぴん’しゃっふる 首曲,冻结期底噪 3.7~4.4,候选 `change=3.42`
  (仅高于阈值 1.4)就被锚定,触发帧截图 `debug/firstnote/232424-trigger.jpg`
  上已显示 MISS、最近音符还在检测带上方 → 整曲错位,开打 7 秒生命耗尽。
  失败局没有结算 FAST/SLOW,GUI 自动校准既拿不到信号也修不了这个量级(差两个数量级)。
  现 `wait_first_note()` 加 `_first_note_trigger_check()`:①触发值 ≥ 冻结期底噪×1.5;
  ②band 与顶部 7 行越阈值时差 ≤150ms(正常实测 -8~+1ms,事故 538ms)。命中即拒不锚定、
  重置基线继续等;不同候选累计拒 3 次或累计拒 8s 后放宽。触发/拒绝日志带
  「底噪 / 带内重心 / 顶部判据」取证字段。离线回归 `_test_firstnote.py`(18 项)。
  **L2 方向**(把 photogate 常量换成实测下落速度)验证脚本 `_diag_firstnote.py`
  (`--image` 单图 / `--capture` IPC 连采);单图模式已确认绝对亮度不可用
  (两帧背景亮度差 2.6 倍,分轨也分不开),必须用帧间差分,且要在打歌画面连采。
- **L2 锚点实测 / L3 校准器(2026-10-27 落地,实测默认关闭)**:
  · **落盘取证**:`wait_first_note()` 冻结完成时存 `band_baseline`(静默背景),
    此后每帧算「带内音符绝对 y」(相对基线,不用帧间差分 —— 差分在音符移动时
    重心落在新旧位置中间,速度会被系统性低估),保留最近 12 帧,触发时异步写
    `debug/firstnote/<HHMMSS>-<曲id>-<难度>-anchor.txt`(两列 t_ms / y)。
    **不要再拿外部脚本连采 IPC 取证**:会和 bot 抢通道(实测把滞后统计失配从
    5/22020 拉到 3763/11472),且脚本本身不落盘。
  · **L2 实测**:`_measure_anchor()` 触发后多抓 1~2 帧算下落速度,外推到判定线
    (util lane.h = 590 @720p);速度不在 0.15~4.0 px/ms、或与常量估计差 >45ms
    就回退常量。开关 `PHOTOGATE_MEASURE_ENABLED`(默认 False),也可写
    `data/config.yml` 的 `timing.measure_anchor`,由 `_reload_photogate()` 每首重读。
  · **L3 两条(已生效)**:`gui._calibrate_gate(..., prev_direction)` 方向反转时
    步长减半(抗一晚 47→85→40 的摆动);本曲无 FAST/SLOW 时 GUI 明确提示
    「未参与校准」(以前静默跳过,用户以为一直在调)。
  · 回归:`_test_gate_cal.py`(13 项)、`_test_firstnote.py`(21 项)。
- **GBK 控制台**:见第 1 节。`gh --json` 重定向到文件是 UTF-16,读取要 `encoding='utf-16'`。
- **`list_dir` 遵守 .gitignore**:`_test_*`/`_diag_*`/`.venv` 看不到,找文件用 `Get-ChildItem -Force`。
- **公告类弹窗回主界面会再次出现**,直到点掉;测试时别手动帮它点。
- **README 中英双语必须同步**;内嵌图一律**相对路径 + JPEG**(PNG 曾在用户环境不显示,
  实测 5.4MB→1.1MB);新增图要 `git add screenshots/`。
- **重启 bot 时游戏停在游戏内某页 → `start_app` 原地空转**:main/daily 的 interrupt 里
  `start_app` 无 recognition 必定命中,它只认开始页菜单/自由演出首页按钮。已补三处兜底:
  `comfirm_song`(选择乐队)、`select_song`(选曲列表)、`select_live_home_back`(「选择演出」界面
  → 点左上角返回箭头回主页)。再来新界面就在 `start_app.next` 继续加。
- **`start_app` 的 interrupt 原来漏挂 `close_button`**(2026-10-27 生日弹窗踩坑):
  「生日庆祝计划 PASS」宣传弹窗轮播有 2 页,`tap_start` 等待期 interrupt 里有
  `close_button`,点掉了第 1 页;随后进入 `start_app` 主循环,第 2 页的白色「关闭」
  按钮无人识别 → `start_app` 每 22s 空转命中。修复 = `start_app.interrupt` 末位补
  `close_button`(排在 ok/confirm 之后,保持奖励类弹窗优先 OK)。教训:新弹窗即使
  通用按钮模板(`close/white.png`)能认出,也要确认**当前阶段的枢纽链**里挂了它 ——
  各阶段(tap_start/start_app/main/daily)的 interrupt 名单是各自独立的。
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
- **每日三抽卡 TOUCH TO CUT(issue #10,2026-10-22 已修)**:第三抽点完「招募」后
  上一抽结果页尚未切走,`daily_pull_anim_wait` 提前命中旧页「招募结果」→ 误点
  「再次招募」→ 抽卡动画(TOUCH TO CUT)播放,但 `daily_pull_again` 只等确认框、
  20s 超时退出 → main/start_app 也不认识 TOUCH TO CUT → 卡近 2 分钟到手动点击。
  修复:again 加 `interrupt: [daily_pull_cut_anim]` + next 补 `daily_pull_anim_wait`;
  main/start_app 的 next 首位挂 `daily_pull_cut_anim` 兜底。**未改误识别时序本身**
  —— 旧页误判后点「再次招募」与正常流程等价,动画被点掉后会回到正确的结果页,
  所以是良性的;单次日志定位,未稳定复现(报告者原话)。
- **单字标题「栞」选不到(2026-10-21 已修,源码)**:#513「栞」在选难度页被
  ja 模型 OCR 读成形近字「乗」(下半都是木)、默认模型读出空串 → 匹配 0 分,
  SongRecognition 永不命中 → 随机选歌死循环(点难度→认不出→重选,约 3s/轮,
  maa.log 里 `random_choice_song → set_difficulty → _ocrsong_*` 循环即此)。
  已加 `_OCR_TITLE_MISREAD` 形近字表(乗→栞,只作用于 OCR 读数;已核对全曲库
  无标题含「乗」,零误伤)。`_test_songmatch.py` 80 项全过。以后再遇到单字/生僻
  标题误读,往这张表里加。
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
  **2026-10-22 数据分析(42 局,autodori-20261022-121049/184307)**:
  ①全部曲都有滞后增长(6~126ms),用户假想「短曲只是累计少、不显」的前半成立;
  ②但增长率不恒定:每万条命令 19.1~63.8ms(3 倍方差),两首 FULL 曲速率反而
  最低档(23.8/19.1),非 FULL 的 DRIVE US CRAZY 反而累计 126ms/slow=152 ——
  触发条件是「曲尾音符处有效滞后超判定窗」,不是曲长;上午场速率整体高于晚上场。
  ③指标缺陷实锤:「曲内增长」混计 d/m/u/w,VIOLET LINE 增长 66.4ms 却 fast=284
  (该局 photogate=34ms,冷启动校准偏差是另一个轴)。
  **仪器已扩展(2026-10-22)**:`_dump_server_lag_async` 把统计拆成音符组
  (d/m/u)与 gap 组(wait)两行 INFO,并把逐条 (cmd, ideal_ms, lag_ms) 曲线
  落盘 `debug/lagdump/<时间戳>-<歌名>.json`(会积累,注意清理);离线判读跑
  `_diag_lagan.py`(逐曲音符尾段均值/斜率 vs fast/slow/miss 对照表)。
  判读口径:音符尾段均值 >25ms → 下发链候选;≈0 而 gap 大 → 漂移被无音区
  吸收;<-25ms → 偏早(FAST 方向)。改的是 .py,实机生效需重新打包。
  **下一步 = 实机采数(含 FULL 曲)→ `_diag_lagan.py` 出归属结论 → 经用户
  确认修复方向后再动时序(勿直接改补偿)。**
  **2026-10-23 v1.3.4 首采判读(两场 9 首,photogate=28)**:
  ①**音符处滞后实锤**:音符增 ≈ gap增(r=+1.00,单一时间线,漂移没有被无音区
  吸收),音符增 vs slow r=+0.80 —— SLOW 就是音符执行偏晚,归「下发链/server
  累计开销」,不是游戏侧时钟;
  ②阈值效应:音符尾段均值 <40ms 的歌全 0/0/0,>70ms 的两首([FULL]FIRE BIRD
  78ms→slow=315/miss=16,V.I.P MONSTER 67ms→slow=89/miss=7)SLOW 爆炸,
  判定窗约 60~70ms;
  ③曲线形态:0→80ms 近似匀速累计(前 20% ≈0,之后 ~15-20ms/分),是持续
  累计不是单次跳变,现有 OFFSET/margin(按命令开销估计的开环补偿)拉不平;
  ④会话方差大:同曲同难度两场 slow 差 3 倍(10-21 slow≈104 vs 本次 315),
  photogate 43→28 —— 「感觉更差」主要是会话速率方差,不是代码变坏(本轮
  改动纯观测);**修复方向已向用户提出:把开环补偿升级为按服务端实测滞后的
  反馈补偿(每批发布后用 jlog 实测滞后,从下一批 wait 扣回),待确认。**
  **修复已实施(2026-10-23 用户确认方向 A 后)**:
  - 测量端:`mnt_callback` 里 `_lag_fb`(仅带时命令 d/m/u/w,commit 成本体现
    在相邻 start 差里;lag += start_i - start_{i-1} - sched_{i-1},sched 只含
    wait 计划时长)—— 口径与 `_dump_server_lag_async` 离线对齐完全一致
    (_test_lagdump.py 合成 jlog 断言估计器 lag == raw@100%);
  - 执行端:`play_song` 每批把 `lag - applied` 增量注入 `chart._a2c_offset`,
    由 chart.py 既有 ±1ms/wait(OFFSET_LIMIT)排水;只补实测增量,
    与 _adjust_offset 是协作不是叠加(后者生效后滞后不增长,增量为 0);
  - 每首歌 `reset_lag_feedback()`(与 cmd_log_list.clear() 同点);
  - 仿真依据:`_diag_lagsim.py`(离线重放实测曲线,P 控制器 + 饱和排水)
    在测量延迟 0~5000ms 下把 9 首的曲内增长 15~75ms 压到 ±8ms、峰值 ≤21ms、
    过冲 ≥-26ms;⚠ 仿真曾因测量指针越界读未重放值得出 delay=0 异常的假象;
  - 注入日志:DEBUG「滞后反馈: 累计…ms 注入…ms(批#…)」;
  - **验收指标(实机)**:log 里「音符曲内增长」应压到 ±10ms 内、FULL 曲
    slow 归零或个位数;若出现批量 FAST(过冲)再考虑加阻尼(gain<1)。
  **⚠ 第一版实机翻车与修复(2026-10-23 00:2x 场)**:三场均批量 FAST,
  FULL FIRE BIRD 音符增长 -100ms、斜率 -65ms/分。取证:注入日志里估计器
  累计 +900ms 而 dump -100ms —— 根因是**反馈基准接错**:估计器用「实际下发
  的 wait 值」当 sched,补偿缩短 wait 后估计器看不见效果,只报纯处理开销,
  反馈无自限,把时间线一路推早。修复:wait 命令生成时把**理想时长**
  (action["length"],未补偿未取整)推入 `chart.wait_ideal_ms` 队列,
  mnt_callback 收到 'w' jlog 回执时按序弹出作为 sched(与 dump 口径一致,
  反馈归零目标 = dump 量);队列空时退回命令串值兜底。合成自检不跑闭环
  抓不到这类错 —— 已在 _test_lagdump.py 补估计器断言;闭环行为仍以
  _diag_lagsim.py 仿真 + 实机为准。教训:**接反馈环先确认「测量信号在补偿
  生效后会变化」,否则环是无自限的正反馈。**
  **第二版修复后实机(2026-10-23 00:3x,3 首,photogate=32)**:曲内漂移
  控制达标 —— 音符增长 曇天 -12.0 / キミの記憶 -4.5 / V.I.P +9.6ms
  (修复前 +78~-100ms),曲线无正负向爬坡;但三场 fast=9/6/68 且 slow 全 0,
  用户体感「打歌更不好」。判读:**整场整体偏早是另一个轴**(photogate 全局
  校准,32ms 疑似偏大;参照 10-22 VIOLET LINE photogate=34 时 fast=284 的
  同款形态,该轴在反馈机制之前就存在)。反馈只修曲内相对漂移,与 photogate
  全局偏移**正交不冲突**(一个修时间轴上的斜率,一个修截距),但叠加决定体感。
  **⚠ 用户决定回退(2026-10-23)**:`_LAG_FEEDBACK_ENABLED = False`
  (src/autodori.py,注入点守卫),打歌时序回到 v1.3.3 行为;全部测量/诊断
  代码保留(只读不碰时序),重启实验置 True 即可。**回退版已重新打包同步。**

  ══ issue #8 研究总结(2026-10-23 结稿,供后续会话接手)══
  **问题**:FULL/长曲越打越慢出批量 SLOW(此前 FIRE BIRD slow=315)。
  **已排除**:①游戏侧时钟漂移(音符增长 vs slow r=+0.80,归下发链/server
  累计开销);②缓存/谱面数据(与 Bestdori 一致);③发布线程漂移、
  wait 补偿漏计、photogate 数值波动(逐项量化过,均非主因)。
  **已确认的机理**:时间轴由服务端 wait 睡眠推进,每条命令微小开销
  (d/m/u 处理几十 μs、wait 超时 ~0.07ms)逐条累积;现有 _adjust_offset
  是「按命令开销均值估计」的开环补偿,拉不平会话速率方差(同曲两场
  slow 差 3 倍,photogate 43→28→32)。**判定窗约 60~70ms**,音符尾段
  滞后 <40ms 的歌全 0/0/0,>70ms 批量 SLOW。
  **2026-10-23 上午 A/B 对照实验(回退版基准 10:29 场 vs 反馈版 11:41 场)**:
  基准场非 FULL 曲也爆 SLOW(Off we go. +103ms→slow=337,Tempest/人マニア
  +84ms);反馈版清火 12 首音符增长全部压进 -3~+25ms、slow 全 0 —— 短中曲
  曲内漂移轴实锤修好。**但 FULL 曲(CiRCLE THANKS MUSiC♪)+55ms/slow=274,
  lagdump 曲线呈「前 20% 快速爬升→50ms 平台」**。
  **第二版反馈的缺陷:存量挂账(2026-10-23 下午定位)**:测量基准=理想时长
  → lag=补偿后残差 R;注入只补 R 的增量(dR),残差存量从未进入排水池 ——
  裸漂移 ~118ms 只追回 63ms,前 20% 欠的 ~50ms 永远挂账(平台即挂账);
  短曲欠账小所以"达标"是假达标。⚠ _diag_lagsim.py 仿真「压平」是
  **双重补偿假象**(把含反馈的实测曲线当裸曲线重放),仿真通过≠实机会好,
  docstring 已补局限声明。OFFSET_LIMIT 排水限速不是瓶颈(CiRCLE 12054 条
  wait 容量 12s,LAGSIM_LIMIT=1/3 仿真结果相同,曾误判已纠正)。
  **第三版翻车(2026-10-23 13:24 场,当场废弃)**:测量基准改实际生效时长
  想追存量,但「逐条增量」记账把**批间空闲**也记成漂移 —— 反馈生效后
  服务端提前干完、空闲变大、账本虚涨,无自限正反馈:FULL キズナミュージック
  账本 +1358ms(dump -75ms),推早 1.3s 批量 FAST。批#1-2(反馈未生效)
  增量=纯开销 9.3/9.4ms 正常,批#3 起跳到 16.7→46.5ms 即正反馈形态。
  **教训:逐条增量口径下「命令间一切时间差都算漂移」,与补偿天然耦合,
  反馈测量只能用绝对对齐。**
  **第四版(2026-10-23 13:38 打包,待实机验证)**:测量端**绝对对齐**
  (在线理想时间线指针 ideal,lag = start − ideal,与 dump 重建口径一致,
  队列恢复 wait_ideal_ms 理想时长)+ 注入端「补足到池」(每批注入
  lag − chart._a2c_offset,把池水位补到与残差一致)。排水消费池水时
  残差同步下降、下批补足归 0 —— 第二版(自限但挂账)与第三版(追账但
  正反馈)缺陷的合并解。测量滞后使补足相位落后 1~2 批,误差偏少注
  (安全侧)。**实机验收**:lagdump「音符曲内增长」FULL 曲压回 ±10ms;
  在线 lag 与 dump 曲线一致(同口径);注入日志格式改为
  「滞后反馈: 残差Xms 池A→B(批#N)」。
  **第四版复验(2026-10-23 17:44 场,清火 6 首,photogate 38~39)**:
  音符增长全场 ±2.6ms 内、slow 全 0、fast 合计 19(0.1%)。photogate
  校准器受益:轨迹 32→38→39 两首收敛(此前从错误起点爬十几首),漂移
  被修平后校准信号变干净、不再被带偏。**连续两场(13:40 FULL×3 +
  17:44 普通×6)达标,曲内漂移轴判定修复完成。**
  **第四版三、四场复验(2026-10-24 00:19/00:44,清火 8 首 + FULL×2,
  photogate=39 恒定)**:普通曲全部 ±6ms、FULL きゅ〜まい＊flower +4.9、
  FULL FIRE BIRD +0.8/全 PERFECT(origin 曲,历史 slow=315)。五场连续
  达标(FULL×5+普通×14),跨时段稳定,issue #8 曲内漂移轴结案;
  photogate 轴在反馈版下亦稳定(39 恒定、fast/slow 全 0)。待发版。
  **v1.3.4 已发版(2026-10-24 凌晨)**:commit 1a02458 + tag v1.3.4 push,
  CI 自动构建发 Release(标题「BanG Dream · 邦邦自动挖矿助手 v1.3.4」,
  正文取 RELEASE_NOTES.md)。发版文本四处:RELEASE_NOTES.md / CHANGELOG.md
  (含四版口径踩坑经验,现象/原因/修复三段式)/ gui.py CHANGELOG(v1.3.4
  块,已删 v1.3.3「已知问题」行)/ README 中英双语(功能条目 +
  「🎵 支持 FULL 曲」小节,配图 screenshots/full.jpg,由用户桌面 full.png
  转 JPEG 216KB)。1:05 重新打包(改了 gui.py),exe 已同步仓库根。
  issue #8 回复话术已交给用户自行发布。本地 dist zip 为最终产物。
  **issue #11 高开销机器反馈不收敛 → v1.3.5-rc1(2026-10-25)**:MuMu 用户
  (nighteron)复测 v1.3.4 仍 fast→perfect→slow,This game 音符曲内增长
  67.9ms(45→110ms 不收敛)、slow=16。取证:其机器 wait 单条开销实测
  均值 0.97ms(0.67~1.24),贴着 chart.py OFFSET_LIMIT=1 排水上限,排水
  容量(3225ms/歌)≈ 积累(~3249ms)零余量 → 反馈环空转。我们自己机器
  0.07ms 有 14 倍余量所以实机验收没暴露。修复:OFFSET_LIMIT 改自适应
  max(1, wait_offset+0.5)(低开销机器 max 兜底=1 行为不变);离线验证
  用合成裸曲线(用户的 lagdump 是带补偿曲线,直接重放是双重补偿假象;
  合成 LIMIT=1.0 尾段 108.9 与实机 105.3 吻合,模型可信)——注意 lagsim
  尾段有 ~3000ms 测量延迟地板(~90ms),判读只看斜率不看绝对值。
  _test_chart.py 21/21。附带发现:用户 10-23 附件实为 v1.3.3-rc 前构建
  (旧文案「对齐失配过高」+ miss=全部带时命令的命名错位特征签名),
  maa.log Working 目录 autodori_v1.3.3_Windows_X64。v1.3.5-rc1 已打包
  (gui.py APP_VERSION 1.3.5 + 三处发版文本),exe/metadata 已同步。
  **失配炸弹 + 反馈两道保护(2026-10-28)**:00:13~00:19 三首(gate 同为 60):
  #320 失配 11/15717 → miss 4;#763 2719/16964 → miss 12;#263 322/5150 →
  lag 12ms→117ms → 生命耗尽。同场 00:25 迷星叫 fast=231/slow=0(整体偏早,
  gate 60 比实际小 ~15ms)→ GUI 自动校准推到 75,下两首全 PERFECT;00:27
  失配只剩 14 条时一切正常 → **失配是间歇性爆发,不是长曲固有属性**。
  机理:jlog 的 'w' 回执丢了 → wait_ideal_ms 只进不出(弹出发生在回执里)→
  ideal 指针少推进 → lag 被算**大** → 「补足到池」把虚高值注回去 → wait
  被缩短 → 时间线推早 → FAST/miss 正反馈直到崩。已加两道保护(src/autodori.py
  `lag_feedback_batch_delta`,play_song 每批调用):① 回执积压持续 >400 条达
  2.0s → 本曲停注入 + **清空池水**(不清的话虚高池水还会按 OFFSET_LIMIT
  继续排水,越打越早),退回开环 _adjust_offset;② 单批补足 >30ms(往早推
  那一侧)丢弃 + 首次 WARN、后续 debug 记数。曲末加 INFO「滞后反馈汇总:
  回执积压峰值 N 条(上限 400), 单批超限丢弃 K 批」—— 正常只领先 1~2 批
  (≤200),峰值贴上限或被停注入即下发链出问题的实锤,400 靠这些数校准。
  回归 `_test_lagguard.py`(25 项)。
  **photogate「自发变化」结案(2026-10-28)**:实测 72→67(23:28)、67→60(23:46)
  都紧跟全 PERFECT 曲(fast=slow=0)。代码级排除:自动校准 `_calibrate_gate`
  在 fast+slow==0 直接 return None(v1.3.4 与当前版同)、`_on_life_exhausted`
  只 ±15(且崩溃局常因方向=0 不写)、`_reset_gate` 是 30 —— 都不可能是 −5/−7。
  唯一写值是**手动输入框提交**(`_on_gate_manual`:打字后失焦/回车,或点「开始」
  时 `gate_step.commit()` 强制提交),它原来**不打任何日志**,CAL 也只进界面
  缓冲、不进导出日志 → 事后无从追。已改:手动改值统一走
  `_apply_photogate(source="手动输入")` 打 CAL(`photogate(手动输入): A → B ms`);
  `_write_config` 发现文件值与界面值不一致时 WARN「已按界面值写回」(同时能
  抓到第二个 GUI 实例拿旧值覆盖);CAL 镜像进 `_raw_log`,`导出日志`可见。
  顺带修 `Stepper._bump`:set() 先更新 _last,紧接着的 _commit 认为"没变化"
  不回调 → **± 按钮原来完全不生效**(只变数字)。结论:再遇 gate 异常先看
  界面 CAL 行与导出日志,别只看 bot 的 debug 日志(bot 只记录"本首用的值")。
  **第四版实测达标(2026-10-23 13:40 场,三首 FULL 曲)**:キズナミュージック
  音符增 +2.2/尾段 0.5、FIRE BIRD +17.6/尾段 0.6(gap -13.5,部分漂移被
  无音区吸收)、CiRCLE THANKS MUSiC -26.0/尾段 -7.7 —— 三首 slow 全 0、
  miss 0,曲线无平台(此前第二版 FULL 尾段 +50ms 平台消失),存量挂账被
  追回实锤。注入日志池水位曲尾收敛到 0.1ms(每批排水喝干、补足量=当批
  新残差 5~14ms),无正反馈。fast=51/64 为 photogate=32 全局轴既有量级
  (正交,历史 32 时 fast=9/6/68 同级)。CiRCLE -26ms 增长略偏早(截距轴
  或补足相位),判定窗内不影响判定。**第四版方案定稿:测量端绝对对齐
  (在线 ideal 指针,lag=start−ideal,dump 同口径)+ 注入端补足到池**。
  遗留观察项:①FIRE BIRD 音符/gap 增长劈叉(+17.6/-13.5)的机理;
  ②补足相位滞后 1~2 批在剧烈波动会话下的表现(本场 photogate 恒 32、
  会话稳定,未测);③CiRCLE 整体 -26ms 是否补足过冲(如复现可考虑给
  _fb_delta 加小阻尼)。
  **photogate 全局偏移轴(截距)的进展**:GUI 自动校准(_calibrate_gate)
  已实现且在场(11:41 场 15→34 爬坡轨迹),两个缺陷:①收敛慢(信号弱时
  步长 1~2ms,从错误起点爬回要十几首);②会被曲内漂移带偏(校准器只看
  fast/slow,分不清全局偏移与曲内漂移,10:29 场 36→6 带偏实锤)。
  反馈修好后 ② 大幅缓解;候选改进=给步长设下限(|FAST-SLOW|>100 时
  至少 5ms),待反馈验收后再议。LEVEL5 fast=307 是 photogate=15 起点
  太低(上场被带偏的遗留值),不是 34 偏大 —— 校准语义:FAST 多=偏早
  =photogate 偏小=应增大。
  **两个独立的判定偏移轴(后续工作按轴拆)**:
  1. 曲内漂移(斜率):反馈修法第三版待验收,开关 `_LAG_FEEDBACK_ENABLED`;
  2. 全局偏移(截距):photogate 自动校准已实现但收敛慢/被带偏(见上),
    手动调节(photogate 时基输入框)也已存在;待反馈验收后按需改进。
  3. 固定 miss 1~2(谱面数据错;可用 chart_overrides 数据侧覆盖修,见 #9 与
    2026-10-26 条目)。
  **工具链(全保留)**:debug/lagdump/*.json(逐命令曲线)、
  `_diag_lagan.py`(逐曲判读表,--curve 单曲曲线;第一个参数是 lagdump
  目录,不是日志路径)、`_diag_lagsim.py`(反馈仿真,LAGSIM_TRACE=1 可
  追踪,LAGSIM_LIMIT 可调排水限速对比;⚠ 仿真结论有双重补偿局限)、
  `_test_lagdump.py`(估计器断言)、log 关键字「服务端执行滞后」
  「滞后反馈」「Adjust offset」。
  ══════════════════════════════════════
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
  **⚠ 2026-10-22 用户更正:#462 的问题只在 hard 难度出现**(10-22 打的
  expert 全 P 898 不构成修复证据,expert 本来就不崩);判「修复」必须用
  hard 难度实测。
- **#655 / #144 固定 miss=1 已结案(2026-10-21,非 bot bug)**:
  **2026-10-26 进展(用户给了上游 PR #13 与游戏 BMS 下载地址)**:两类「固定 miss」
  都已落到**数据侧**修好,机制见第 2 节 `assets/chart_overrides/`,
  **完整排查流程(分型/combo 反推/BMS 用法/验证口径/禁区)见 9.6 playbook**。
  ①#655-hard 缺音实锤并补齐:BMS 逐事件对账 487 条,Bot 用的 Bestdori JSON 只有
    486,唯一缺口 = beat 49 / lane 6 的普通单键(BMS 对象写的是 01=bgm655.wav,
    游戏照样判),与第 2 轨长条起点同拍 → `chart_overrides/655-hard.json`,判定点
    恢复到 487。**旧结论「bot 侧无法可靠修复」只对未定位的差异成立,已定位的可以修。**
  ②#144-hard 的恒 miss=1 不是「轨位/时间录错」,而是 flick 错标(见下条 #35 条目):
    它唯一那条 flick 滑条尾在游戏 BMS 里是 `fever_note_slide_end_a`(普通 fever 尾),
    已收入 `chart_overrides/144-hard.json`。#16-hard(恒 miss=2)同族 —— BMS 取不到,
    但恒定 maxcombo=229 恰好 = 首个 flick 尾序号 230 − 1,两条尾同拍(161.5,L2/L6),
    已收入 `chart_overrides/16-hard.json`。**#16-hard 不再是「数据错位型」。**
  下面 2026-10-21 的取证链保留(①②⑤⑥⑦仍有效;③④⑧的结论已被上述修正)。
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
  2026-10-22 新候选(待复算谱面判定点数后再定):ひとりじゃないんだから
  3 局(平行歌曲/普通版)均 perfect=589/maxcombo=336/miss=2;
  えがおのオーケストラっ！ 2 局(平行歌曲)均 perfect=425/miss=2 ——
  多局纹丝不动,符合数据错位型特征;单局小额 miss(ファティマ/ときめき/
  サムライハート/走り始めたばかり 各 1~2)证据不足,先不列。
- **#35/#681、#147/#674 expert 固定 miss=2(2026-10-26 取证,根因=滑条 flick 尾手势,待修)**:
  ①对账:expert 判定点 #35=427、#147=591(长条逐段计),与游戏结算
    (425+2 / 589+2)完全一致 → **非缺音型**,音符都在,是我们的手势对其中
    2 个点失效;扫全战绩库,只有这 4 个 chart-difficulty 是「每局干净
    miss=2(fast/slow=0)」,两者的 hard 均无此问题。
  ②BMS 解码器已校准(`_diag_655bms.py`,对 #655-hard 全量对账,487 事件
    精确复现 PR #13 的缺音):音符通道 = 11~18 + 51~58 两组(通道 01 是
    BGM;但 01 对象写在音符通道里 = 游戏照样判,这就是 #655 缺音根因);
    通道→lane 实测映射 **16→0, 11→1, 12→2, 13→3, 14→4, 15→5, 18→6**
    (5x 组同构);对象语义 03=普通键、02=flick 键、0H/0I=绿条段/尾、
    0J/0K=滑条段/尾、0O/0P=flick 尾(未见样本);**长条头/尾大量用普通
    03 表示** —— 对象值不带「长条」信息,别按对象值猜结构。
  ③**flick 错标成立(2026-10-26 二次取证,纠正前一轮的误判)**:游戏 BMS 的滑条尾
    有三种对象:**`slide_end_a/b`(普通尾)**、**`slide_end_flick_a/b`(真 flick 尾)**、
    **`fever_note_slide_end_a` / `fever_slide_end_a`(fever 尾,没有 flick 变体)**。
    实测对照:打得中的谱面(#438 25 局全 P、#242 15 局全 P、#301、#304)里 flick 尾
    全部是 `slide_end_flick_*`;打不中的 #35/#681 各 2 条、#147/#674 各 2 条、#144-hard
    1 条都是 `fever_note_slide_end_a`,#710-expert 1 条是 `fever_slide_end_a` ——
    **Bestdori 把 fever 滑条尾转录成了 flick**。我们按 flick 上划 300px 离轨,游戏那边
    只是普通滑条尾,尾判必丢;恒定 miss 条数 = 这类尾巴的条数(逐局全一致)。
    前一轮「9/5 个 flick 尾全部是真 flick(04/0F)」是把 fever 对象 0F/0J 当成 flick 了,
    真 flick 尾在 BMS 里叫 `slide_end_flick_*`(第一轮没见过的样本)。
    **BMS 通道语义(重要)**:1x 通道=普通 tap+滑条(Slide 全在 1x);5x 通道=绿条
    (Long)专用,头尾成对;绿条头/尾大量用普通 03 表示,BMS 看「哪些 03 连成绿条」
    是盲区(但 1x/5x 分组可判定是不是绿条)。**目录编号不是 id+9**(681→690 是巧合),
    要靠 `_diag_bmsfetch.py` 暴力探测;老曲(24/16/20)按现命名规律取不到。
  ④**miss 定位(combo 反推,maxcombo 恒定)**:#35/681 expert maxcombo=215 →
    miss1=第 216 点=Slide 125→126 的 flick 尾(126@L6),miss2=孪生
    Slide 127→128 的 flick 尾(#221,最可能);#147/674 maxcombo=336 →
    与两条 charge 滑条(144→147、160→163)的 flick 尾自洽(计 222/31/336 段)。
  ⑤**泛化**:同族判据 = 「Bestdori 标了 flick、游戏里却是 fever 滑条尾」。已确认
    并出覆盖文件:#35/#681(2 条)、#147/#674(2 条)、#144-hard(1 条)、
    #710-expert(1 条)、#24-expert(2 条)、#16-hard(2 条)。后两张 BMS 取不到
    (老曲目录/文件名不符),判据换成 **combo 反推:恒定 maxcombo 恰好 = 首个 flick 尾
    序号 − 1 且滑条本体全是 charge**(#24:287 = 288−1;#16:229 = 230−1;2026-10-26
    实机 #35/#147/#674 归零、#24 仍 2 印证)。**#20-expert 不是这一族**:maxcombo=538
    对应的是 flick 尾(#538)之后的同拍单键(#539),机制另查。仍有恒定 miss 未解释:
    #537-hard(2)、#459-hard(1)、#647-expert(1),这三张谱里根本没有 flick 尾滑条。
    **别再按几何特征猜**:带 flick 尾滑条的 495 张谱里 449 张能全 P。
  ⑥**手势差异只是表象**:lagdump 对照过「连续横扫 vs 静止 dwell」,但那不是判据 ——
    打不中的尾巴在游戏里根本不是 flick 对象,上划本身就是错的。
  ⑦**上一轮手势修复无效,已回退**:chart.py 的 `_SLIDE_FLICK_SETTLE_MS`(末段横移
    提前 60ms 到位停顿再上划)实测没解决问题 —— 2026-10-26 18:03 源码局打
    #35-expert,动作数 2398(= 带 settle 的特征值,不带是 2422)说明改动已生效,
    结果仍 miss=2 / maxcombo=215。**2026-10-26 用户确认回退**,chart.py 恢复
    v1.3.3 手势,常量与那段末段缩短逻辑都删了。现在走数据侧:见第 2 节
    `assets/chart_overrides/`,#35-expert 的 2 条尾变成普通滑条尾手势(到位即抬手)。
    **实机验证口径**:源码模式打 #35-expert / #147-expert / #144-hard / #710-expert
    各数局,miss 应 2/1→0;日志里 `Local chart override for <id>-<难度>` + 动作数
    2390(#35-expert;未加载覆盖是 2422)可确认覆盖生效。⚠ api.py 的覆盖加载是
    .py 改动,exe 生效要重新打包;覆盖 JSON 本身从磁盘读,以后新增不用打包。
- **⚠ `assets/minitouch_EvATive7/` 不在 git 里**(下载产物,gitignore):
  被删后 build.py 报 "no usable binaries" 且 minitouch.zip 本机下载有证书
  问题。恢复办法:从任一已发布 zip 里取 `assets/minitouch_EvATive7`
  (2026-10-21 曾从 rc2 包恢复过一次)。
- **v1.3.3 发布状态(2026-10-21 晚,二次重发)**:当晚因「栞」选歌死循环
  修复(commit 3b305fa)再次删旧 Release+tag 重发 —— tag v1.3.3 现指向
  3b305fa(含首音轨道判别、首音截图取证、栞形近字归一全部修复),CI 自动重发
  Release(标题/正文/资产均已核对)。本地 dist 的 zip 与仓库根两个 exe 同步为
  最新,`_chk_exe.py --grep _OCR_TITLE_MISREAD` 核对通过。发版文本三处均已加
  「修复部分单字歌名曲目无法选中的问题」。
- **v1.3.4 打包状态(2026-10-22 晚,仅本地取证包,未发版)**:滞后仪扩展
  (音符/gap 分组 + lagdump 曲线落盘)+ APP_VERSION 升 1.3.4,
  `build.py --version v1.3.4` 打出 `dist/autodori_v1.3.4_Windows_X64.zip`
  (minitouch 走本地副本属正常);两个 exe 已同步仓库根,
  `assets/build_metadata.json` 已拷,版本号与新符号(lagdump、音符曲内增长)
  核对通过。**未写发版文本、未打 tag**(取证阶段,等判读结论出来再定
  v1.3.4 的 Release 内容)。
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
      连续 `_WATCHDOG_STATIC_S=75s` 既没画面变化也没 ping → 分轮解救(2026-10-26
      #400 事故后重排):第 1 轮置 `_STUCK_EVENT`(首音等待等循环每帧检查,看到就
      raise 走 on_error —— **post_stop 停不掉运行中的 CustomAction**,协作式解救是
      唯一出路);第 2 轮点「演出失败→退出」按钮(`_click_stuck_modal_button`,
      cv2 模板匹配复用 common/button/exit/*,负样本实测最高 0.498 远低于 0.8);
      第 3 轮按返回键(adb 结果 WARNING 可见,以前失败只写 DEBUG 无法区分
      「没送到」和「画面不响应」);无效 → `post_stop`。首音等待另有 240s
      总超时兜底(`FIRST_NOTE_TIMEOUT_S`,看门狗失灵时保底)。
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

## 9.5 2026-10-25 新增:阅读角色故事(story 模式)

**需求口径(用户定,勿改)**:
- 分工:用户在游戏里把成员按「小故事未读」筛选好、停在「乐队-培养」列表页,
  再从 GUI 发起;bot 只按列表顺序读。列表筛选**实时收缩** ⇒ bot 每次都点
  左上角第一格头像,进不了成员详情(20s 超时)即全部读完,任务自然结束。
- **每轮固定 50 篇**(用户定):到量就暂停,「若还有未读,再次点击继续」;
  `story.max_stories` 只作 config.yml 兜底,GUI 不暴露。剩约 300 篇时用户
  会连点多轮。
- **回忆小故事入口已砍**(用户定:解锁消耗太多不合算):GUI 只有「阅读
  小故事」;后端 `--story-type memoir` 保留,命令行可调试。
- 每篇小故事要消耗道具解锁(500+300),弹窗出现就点「解锁」→「已解锁」
  点「无语音」(零下载)→ 剧情「菜单→跳过→确认跳过」→ 奖励弹窗点「确定」
  → 成员详情点返回 → 下一格。奖励只点掉不校验数值。
- rank up 弹窗(编队等级提升,读故事涨乐队经验会弹)与奖励弹窗的「确定」
  是**同款按钮资产**(实测 0.9968),直接复用 story_reward_ok 模板,靠放宽
  roi(三档按钮 y:495/515/553)一个节点全覆盖,没有单独的 rank up 节点。
- **rank up 弹窗是全局风险,不只 story**(2026-10-25 实测):清火连打中结算页
  点「再次演出」的瞬间弹出 RANK UP!(乐队升级),点击被模态吞掉,
  liveagain.next 20s 全不命中 → 主任务结束 → 纯清火循环看到"已打到歌"
  正常收尾退出(火尽判定在选曲确认页,没走到)→ 日常整体提前结束,三抽没跑。
  修复:模板移到 common/button/rankup_confirm.png(与 story 奖励「确定」同款
  资产,story/live 两处引用),live.json 新增 rankup_confirm 节点挂
  liveagain.next 尾部 —— 弹窗在场时 select_song/event_reward_confirm 必不
  命中,它兜底点掉确定后自循环回 liveagain 重来。经验:**同一弹窗在多个
  模式出现时,识别与处理要做成通用节点,别只修发起功能的那条链**。
- **材料不足也会暂停**(用户 2026-10-25 定):「小故事解锁」弹窗有材料不足
  变体(红字「需要的道具不足」,按钮变 取消/去获得必要道具,红色「解锁」
  不存在,_story_11 取证);story_insufficient 节点识别红字 → StoryInsufficient
  只记标记返回 False → on_error stop,**不点任何按钮**(画面保持原样)。
  每篇消耗相同,一篇不够篇篇不够,停任务由收尾汇总打暂停日志。实测有效。
- **运行日志口径**(用户定):开跑一条(含本轮上限);每篇开始一条;
  每篇完成一条(StoryRewardOk 挂奖励「确定」—— 这是唯一能确认读完的位置);
  收尾汇总按四态区分:材料不足 / 达上限暂停(还有未读,再点继续) /
  列表读空(全部读完,共 X 篇) / 启动时不在列表页。解锁/无语音/跳过/
  rank up 的逐次点击不打日志(要看细节查 maa.log node hit)。

**文件**:管线 `assets/resource/pipeline/story.json`(12 节点闭环);
流程与 CustomAction `StoryAvatarClick` 在 `src/story.py`(照 challenge.py
惯例,autodori.py 模块级注册);GUI 卡片 `_detail_story`(挂在日常页第二张,
`_card_detail` 的 daily 分支用 wrap Frame 装两张卡);模板
`assets/resource/image/story/`(7 张)。

**实机验证**(2026-10-25 两轮共 100 篇零失败):单篇全链约 18s;入口自愈
(启动时若停在成员详情/弹窗未关,先点掉再进主循环)实测有效。

**踩坑(复用价值高)**:
- **截图坐标换算**:IPC 截图目测坐标是显示比例,与实际 1280x720 差
  **1.185 倍**。第一版模板整体错位裁到平坦区,产生 CCOEFF=1.0 的退化假命中
  (纯色按钮/纯白弹窗底特别容易中招);之前 tmtest「正样本 1.0」是自匹配,
  没有意义。生产模板 `skip/pink.png` 的实测命中位置可用来反推换算系数。
- **按钮模板只能裁固定文字一行**:「小故事」按钮的副标题随故事名变,
  模板多裁 7px 副标题就掉到 0.73(换角色即失配);收紧到标题一行后 0.97。
- **弹窗动画期识别是假阴性**:跳过确认后 1.5s 奖励弹窗还在渐入(MAA 实测
  0.24,静帧 0.98),next 里排在后面的「返回箭头」会抢先命中、点击被模态
  吞掉。对策:点击后 post_delay 加大到 2s+,且**模态弹出期间不要把弹窗外
  可见的按钮(如返回箭头)放进 next**。
- 红色按钮互相像(解锁/跳过 CCOEFF 0.82),靠 roi 限定各自弹窗区分;
  「无语音」阈值 0.85(负样本最高 0.785)。
- `story_member_back` 不能自循环:列表页也有同款返回箭头,连点两次会退到
  主菜单。返回失败就超时停任务(安全),靠用户重跑。

**待办**:打包(改了 .py,exe 生效需重打包,等用户指令);剩余约 250 篇
用户自行连点几轮读完。

## 9.6 2026-10-26 新增:固定 miss 排查 playbook(谱面数据 vs 游戏实际谱面)

**先分型,再动手。** 「每局漏同样的几个、fast/slow=0」和「漏点跟着 fast/slow 波动」
是两回事,混在一起查会白跑(2026-10-21 就跑偏过一轮:把 fever 对象 0F/0J 当成 flick,
写成「游戏数据无误、bot 没法修」)。

### 1) 分型三条判据

- **缺音型**:JSON 判定点总数 < 游戏结算总数 → 游戏有音符数据里没有。补音即可。
  - 计数口径:Single/Directional 计 `width`(默认 1);Long/Slide 逐个 `connections`
    计 `width` 求和;System/BPM 不算。**别直接拿 `len(JSON)` 当判定点数。**
  - 游戏总数 = perfect+great+good+bad+miss;OCR 读出 -1 的局不能用来对账。
- **错标型**:总数吻合、miss 数恒定、fast/slow=0、maxcombo 恒定,且 `maxcombo+1`
  恰好是某个可疑判定点 → 该点手势与游戏要的不一样。目前见到的一律是
  **fever 滑条尾被 Bestdori 标成 flick**。
- **时序型**:fast/slow 非 0 或 miss 数波动 → 先查 photogate / 曲内漂移(第 8 节
  LAG 反馈、issue #8/#11),**不要碰谱面**。

### 2) combo 反推落点(不用录像)

- miss 恒定 k 局、maxcombo 恒定 c:第一个 miss = 第 c+1 个判定点。
- k>1 时 c 可能落在任一段间隔里,要交叉验证:`c+1` 是否恰为可疑点,且可疑点总数 = k。
- 平行歌曲/原曲谱面 JSON 完全相同(#35 = #681),一处结论两处可用。

### 3) 取游戏 BMS(最强证据)

- 地址:`https://bestdori.com/assets/cn/musicscore/musicscore<目录>_rip/<bgmFile>_<难度>.txt`
- `bgmFile` 取 `https://bestdori.com/api/songs/<id>.json` 的 `bgmFile` 字段。
- **目录编号不能按 id 推算**(681→690 只是巧合;144→150、242→250、438→440、
  301/304→310、710→710)。用 `_diag_bmsfetch.py <文件名>` 暴力探测 1..1400
  (24 线程几秒);返回体 14084 字节 = SPA 兜底页,说明该目录没有这个文件。
- 老曲 #16/#20/#24 用多种文件名 × 1..1400 都没找到 → 放弃 BMS,只走 combo 判据,
  并在文档里标注「未 BMS 佐证」。
- 通道:1x(11~18)= 普通键与滑条;**5x(51~58)= 绿条**(头尾成对)。通道→lane:
  **16→0, 11→1, 12→2, 13→3, 14→4, 15→5, 18→6**(5x 同构)。
  01 通道是 BGM,但对象写在音符通道里时游戏照样判 —— 这就是 #655 缺音根因。
- 对象按 `#WAVxx` 的**名字**读,编号各曲不同(同为真 flick 尾,438 是 0K/0L,
  301/304/242 是 0O/0P)。
- 滑条尾只有三种:

  | 对象名 | 游戏里是什么 | 我们该怎么做 |
  |---|---|---|
  | `slide_end_a/b` | 普通滑条尾 | 到位 + 抬手(默认手势) |
  | `slide_end_flick_a/b` | 真 flick 尾 | 上划 300px(默认手势,别动) |
  | `fever_note_slide_end_a` / `fever_slide_end_a` | fever 滑条尾,**没有 flick 变体** | 到位 + 抬手;Bestdori 标 flick 是错的 |

- 对账工具:`_diag_35objmap.py <bms 路径> <曲id> <难度>` 输出「BMS 对象 → JSON 属性」
  交叉表;两边事件数相等、每个属性桶都能解释,才算解码对齐。

### 4) 数据侧修法(不要改手势)

- 覆盖文件:`assets/chart_overrides/<曲id>-<难度>.json`,**完整谱面**(Bestdori 原格式);
  `BestdoriAPI.get_chart()` 在磁盘缓存之前加载(缓存里就是错数据),细节见该目录 README。
- 生成脚本 `_exp_gen_chart_override.py`(本地,不入库):PATCHES 表写死每张谱面的
  「补哪些音 / 去几条 flick / 判定点应为多少」,跑一次重出全部覆盖,断言不过就报错。
- 覆盖必须用**线上** JSON 生成(`/api/charts/`),别拿缓存或旧拷贝,否则把旧数据固化进仓库。

### 5) 实机验证口径

- 日志两条:`Local chart override for <id>-<难度>` 与 `打歌: <曲名> (#id-难度), 动作N`。
- 动作数是覆盖是否生效的指纹(2026-10-26 实测):

  | 谱面 | 覆盖生效 | 未生效 |
  |---|---|---|
  | 655-hard | 3983 | 3980 |
  | 35-expert / 681-expert | 2390 | 2422 |
  | 147-expert / 674-expert | 4085 | 4115 |
  | 144-hard | 1963 | 1979 |
  | 710-expert | 3205 | 3221 |
  | 24-expert | 2032 | 2064 |
  | 16-hard | 1798 | 1822 |

- 判定标准:同一首歌数局 miss 归零、maxcombo = 判定点总数。
- **2026-10-26 实测**:655-hard 1 局 0 miss(maxcombo 487);35-expert 2 局 0;
  147-expert 1 局 0;674-expert 2 局 0;144-hard 1 局 0;16-hard 1 局 0;24-expert 1 局 0。
  681-expert、710-expert 覆盖与已验证谱面同源(681 与 35 同一张谱),尚未单独重打。

### 6) 禁区(每条都付过代价)

- **别按几何特征猜**(多段横移 / 末段短 / 折返):带 flick 尾的 495 张谱里 449 张能全 P。
- **别改手势去凑**:60ms 提前到位停顿(`_SLIDE_FLICK_SETTLE_MS`)实测无效,已回退;
  在错数据上改手势会把别的谱面一起弄坏。
- **别把 fever 对象当 flick**:`fever_note_*` 系列没有 flick 变体。
- **别拿缓存判新旧**:缓存与线上可能都是错的;覆盖只在 `get_chart` 入口生效。
- **覆盖会盖住 Bestdori 后续修复**:上游改了数据也不会生效,要人工复核后删覆盖。
- **别把「总数吻合」当成「数据没错」**:错标型恰恰是总数吻合的。

### 7) 仍未结案(接手先看这里)

- **2026-10-29 补充三首**:#146-special(miss 1 / maxcombo 446)已结案 —— 是 photogate
  临界,同一谱面 gate 50→miss 1、gate 60→AP,数据层 BMS 对照一致,见第 8 节。
  #304-special(miss 2 / maxcombo 970,两局完全一致)= 长滑条起点被同帧 down+move
  覆盖,机制已定位、修法未做。#409-special(miss 2 / maxcombo 1051)= 长条头,
  与另一指 move 同帧,跨指型,未定。
- #20-expert:恒定 miss=2 / maxcombo=538,但 flick 尾在 #538,第一个 miss 是紧随其后的
  同拍单键 #539(lane 5)→ 不属 flick 错标族,机制待查。
- #537-hard(恒 2)、#459-hard(恒 1)、#647-expert(恒 1):谱面里没有 flick 尾滑条,
  是别的类型。
- #24-expert 19:39 一局 miss=6(maxcombo 501,全集中在曲尾 #502~#508,lag 曲内增长
  -5.7ms 正常),之后同谱面 0 miss。疑游戏侧卡顿,一次性,再复现再查。
- #462-hard 开局崩局属另一条轴(第 8 节)。

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

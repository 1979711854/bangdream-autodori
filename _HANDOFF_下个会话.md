# 交接文档：BangDream GUI 改造（Maa/SukiUI 风格）

> **写给下一个会话**：GUI 已是 MaaBanGDream（SukiUI）风格新版；**bot 打歌逻辑已回退到 v1.1.2 固定挖矿版**(用户决定,含首音宽限期/生命耗尽自动退出修复)。用户要"新 GUI + 旧打歌逻辑",v1.1.3 的闭环时基/判定反馈实验已删除。当前状态已提交 `1694c5a`。下一步:验证新 GUI + v1.1.2 bot 正常打歌。

---

## 〇、2026-09-06 新：bot 回退 v1.1.2 + 保留新 GUI（已提交 1694c5a）

**背景**：用户改乱了代码、删了旧源码,担心打歌逻辑丢失。git 历史全保留。
**用户决定**：GUI 要新版(SukiUI),但"打歌的逻辑要之前的"→ 回归固定挖矿即可。

**本次改动（已提交 `1694c5a`）**：
1. `src/autodori.py` + `src/chart.py` → `git checkout 7343c10` 回退到 **v1.1.2**(固定挖矿、首音宽限期 FIRST_NOTE_GRACE_MS=500、生命耗尽自动退出、日志诊断头)。
2. 删除 v1.1.3 独有文件 `src/timing_feedback.py`、`src/test.py`;autodori/chart 中 PENDING_SHIFT/judgement_feedback 闭环全部移除(无残留引用)。
3. 保留新 GUI:`gui.py` + `ui_widgets.py` + `ui_theme.py`(Maa/SukiUI 风格自绘,已 add 提交)。

**当前运行方式**：`E:\autodori-src\autodori_gui.exe`(新 GUI,9-5 17:15 构建)+ 同目录 `autodori.exe`(v1.1.2,8-28 20:23)。旧 `E:\bangdream-autodori_win64` 运行目录已删除。GUI frozen 模式 BASE=exe 所在目录。
**接口注意**：新 GUI 写入的 `song_strategy`(挖矿/随机)被 v1.1.2 bot **忽略**(v1.1.2 只按 tier 固定挖矿)——用户接受"固定挖矿"。photogate/on_life_exhausted/play_at_zero_boost 正常。
**待办**：真机验证新 GUI+v1.1.2 bot 打歌正常;若 gui.py 再改需重建 autodori_gui.exe(pyinstaller 需带 ui_widgets/ui_theme + PIL)。

---

## 〇、2026-09-05 晚 新增（本会话已完成，勿再改）

### 打歌精准度修复三：打歌中段狂爆 great + 歌名切换 + 日志简化

**症状（`debug/autodori-20260905-190653.log` 实证）**：
1. 第一首 My Dearest 正常，第二首「ヒロイン育成計画」歌名没切换。
2. 两首都在打歌**途中突然**狂爆 great 甚至 miss（非波动，开局正常）。
3. 关键事件不显示结算结果（太省略）。
4. 要求删除「全部输出」、导出日志默认完整内容。

**根因**：
- 狂爆 great：`_a2c_offset`（跨批累积的触控补偿量）**无上限**。打歌中段模拟器触控延迟爬升 → EMA 滞后 → `total_cost` 长期为正 → `_a2c_offset` 持续累积 → wait 被拉满缩短 → 整段音符系统性按早。结算实证 `fast: 133/286, slow: 0`（全按早）。
- 歌名切换：`FitLabel._fit()` 在宽度未就绪（`winfo_width()<=2`）时静默 return，`set()` 失效（第二首不刷新，竞态）。
- 结算太省略：`_on_play_result` 只输出 PERFECT/GREAT/GOOD/BAD/MISS，漏了 score 和 maxcombo。

**修复**：
1. `src/autodori.py`：`total_cost` 限幅 ±2ms + `_a2c_offset` 钳制 ±12ms，杜绝无界累积造成的整段偏移。
2. `ui_widgets.py`：`FitLabel._fit()` 宽度≤2 时 `after(50)` 延迟重试（而非静默 return）。
3. `gui.py`：① 结算汇总加「分数 / COMBO」；② 删除「全部输出」Segmented 及 verbose 机制，关键事件固定；③ 导出日志保持导出 `_raw_log` 完整原始日志。

---

### 打歌精准度修复二：触控偏移不收敛 + 日志体验

**症状（`debug/autodori-20260905-184247.log` 实证）**：
1. only my railgun 因电脑卡顿 miss（外部环境）。
2. 第二首「私色きらめき日和」首音后 6 秒才第一次校准、随即演出失败（全 MISS）。
3. 第三首「COMIC PANIC!!!」增强模式能打完但狂爆 great + 几个 miss。

**根因**：
- `_adjust_offset` 用本批次瞬时均值**全量覆盖** `OFFSET`，每批样本少、噪声大，导致 down 偏移在 0.01~0.33 间剧烈震荡、永不收敛（狂爆 great 的直接原因）。
- 上一轮的「生命耗尽时清零 OFFSET」会让下一首开局用全 0 偏移播放数秒（慢歌前奏长、首音后 6 秒才第一次校准），难歌开局密集音符立即错位崩（第二首全 MISS）。

**修复（`src/autodori.py`）**：
1. `_adjust_offset` 改为 **EMA 指数滑动平均**（`ema_alpha=0.3`），偏移平滑收敛、单批异常值只轻微扰动。
2. `reset_offset_and_callbacks` 从「清零」改为「**半衰**」（`OFFSET = {k: v*0.5 ...}`），保留大部分收敛值，避免下一首开局严重错位；回调数据仍彻底清零（那才是脏数据来源）。

### 日志体验（`gui.py`）
1. 运行日志新增「**打开日志目录**」按钮（`_open_log_dir`，`os.startfile` 打开 debug 目录）+ 上传说明文字（上传 `autodori-*.log` 和 `maa.log`）。
2. 「**关键事件 vs 全部输出**」差异化：关键事件只显示高价值事件（开始/歌名/生命耗尽/真实 ERROR/WARNING），隐藏 Adjust offset / wfF·wfT / Unknown type 警告 / Live boost / SurfaceOrientation 等 DEBUG 噪声；新增 `_rebuild_log_view`，切换模式时从 `_raw_log` 重放，差异始终清晰。
3. 「**导出日志**」改为导出完整原始日志（新增 `_raw_log` 全量缓冲，含时间戳/级别/logger），而非被过滤的精简集；导出文件头部附环境摘要 + 上传指引。

---

### 打歌精准度修复：生命耗尽后重进歌全 MISS

**症状**：打歌生命值耗尽 → 退出重选歌 → 「当前曲目」不显示新歌名 + 重进歌后全 MISS。

**根因（日志实证 `debug/autodori-20260905-181741.log`）**：
- 全局 `OFFSET`（触控偏移自适应校准量）在生命耗尽（打歌中断）后**从不重置**。
- 生命耗尽会让当前批次回调数据残缺（悬空 down/move/up 指令、延迟回调），`_adjust_offset` 用它算出被污染的 `OFFSET`（日志里 down 偏移从正常的 ~0.02 漂移到 ~0.25）。
- 下一首 `save_song()` 生成第一批命令时用了这个脏 `OFFSET` → 触控时间轴与谱面整体错位 → 开局全 MISS → 血量掉光 → 又触发"演出失败" → 循环。
- 「当前曲目空白」是上述全 MISS 的**连带现象**（bot 在 7~8 秒内快速"选歌→打歌→崩"循环，歌名频繁跳变），非 GUI 解析 bug。已确认 `SONG_RE`、`FitLabel` 均正常。

**修复**（`src/autodori.py`）：
1. 新增 `_DEFAULT_OFFSET = dict(OFFSET)` 快照 + `reset_offset_and_callbacks()` 函数。
2. 在 `Play.run` 捕获 `LifeExhaustedDetected` 时、以及 `HandleLifeExhausted` 处理链中，各调用一次 `reset_offset_and_callbacks()`（双保险）。
3. 复位后下一首从默认偏移（全 0）重新校准。

**验证**：语法通过；`_t_offset.py` 单测验证 OFFSET 复位为全 0 且 callback_data 清零。

> 注：本轮工作区还有上轮未提交的改动（gui.py 按钮移顶栏/歌名独立栏/打歌策略/photogate 自由输入）。git 未提交，diff 见 `git diff`。

---

## 一、项目背景与技术栈

- **项目根目录**：`E:\autodori-src - 副本`
- **技术栈**：纯 **Tkinter 自绘**（Canvas 多边形采样画圆角）+ **PIL/Pillow**（已在 requirements.txt，作为运行时依赖用于 SVG 图标高保真渲染）+ 亮/暗双主题。
- **目标外观**：模仿 `E:\MaaBanGDream-v1.2.0-win-x64`（MFAAvalonia + SukiUI）。
- **主要文件**：
  - `gui.py` — 主界面组装、视图切换、各 Detail 页布局、文档常量（NOTES/FAQ/PRE_READ）。
  - `ui_widgets.py` — 全部自绘控件层：`Card`、`SidePanel`、`ScrolledFrame`、`RichText`、`NavItem`、`PushButton`、`IconButton`、`DropdownBox`、`Segmented`、`ToggleSwitch`、`Stepper`、`NumberField`、`LogConsole`、SVG 图标渲染等。
  - `ui_theme.py` — 主题色板 token 与字体。
  - `_shot.py` — 截图验证脚本（通过写 `data/gui_config.json` 改主题/视图，拉起 GUI 后 PrintWindow 抓图）。
  - `data/gui_config.json` — 持久化（theme / font_size / window_size / view 等）。

---

## 二、已确认完成 ✅（不要再动，除非用户新提）

1. **侧栏行尾齿轮图标** = 桌面 `C:\Users\zjn\Desktop\settings.svg` 外形。方案 A：PIL 8x 超采样 → LANCZOS 降采样 → `ImageTk.PhotoImage` → `canvas.create_image` 贴图。用户已确认"**现在齿轮没问题**"。
   - 相关代码：`ui_widgets.py` 的 `SETTINGS_SVG` 常量、`_svg_stroke()`（PIL 高保真，缺 PIL 时退回多段折线）、`draw_icon(kind="gear"/"moon")`。
   - **关键经验（勿回退）**：`NavItem._draw()` 渲染宽度必须用 `winfo_width()`，**不要** `max(winfo_width, cget("width"))`——`cget` 只是构造请求值(348)，可能大于 pack 后实际宽(约332)，用它画会让行尾齿轮溢出画布被裁。
2. **深色模式按钮图标** = 桌面 `half-moon.svg`（月牙 `MOON_SVG`）；亮色模式显示 `sun`。
3. **齿轮尺寸**：与文字等高，由 label 字体 `metrics("linespace")-3`（约18px）派生，不再偏小。
4. **"恢复默认 30"按钮化**：`gui.py` 的 `_detail_gate` 中为 `kind="secondary"` PushButton（带边框）。

---

## 三、已解决 ✅（DocView 改造，原"🔴 唯一未解决"）

> **状态更新（2026-09-05）**：原文档页"文字显示不全 + 不能滚动"问题已根治。
> 采用交接建议的**方案 X**：新增 `ui_widgets.py` 的 `DocView` 控件，逐行把文档
> 渲染为独立 `Label` 垂直排布进 `Card`，整页随内容自然增高，由外层 `ScrolledFrame`
> 整页滚动。**已废弃 `RichText`（仍保留类未删，仅 `_detail_docs` 改用 `DocView`）。**
> 验证：`_test_docview.py` 头测三份文档 `clipped=False` + 换行正常；`_verify_shots.py`
> 真实截图 docs.notes 顶部/滚到底部均完整显示【使用提醒】段（亮/暗主题均通过）。

### 症状（已修复）
"注意事项"（docs.notes）页面，第三段 **【使用提醒】** 以及更长文档（docs.faq、docs.preread）**显示不全 / 被截断**；即便补足高度，文字也无法在固定区域**滚动**查看。用户诉求："**文字显示完全，且能正常滚动**"。

### 代码位置
- `gui.py:599` `_detail_docs()` — 用 `W.RichText(card.body, height=20)`，`text.pack(fill="x")`（不 expand），`text.set_content(DOCS[...])`。
- `ui_widgets.py`：
  - `ScrolledFrame` (行174) — 整个 host 内容区的滚动容器（隐藏滚动条，滚轮滚动）。
  - `RichText` (行1153) — 只读富文本，重写 `winfo_reqheight()` 与 `_content_height_px()`。
  - `Card` (行69) — 圆角卡片，Canvas 包裹 body Frame，靠子控件高度自适配。

### 布局结构（理解问题的关键）
```
host(右侧内容区)
└─ ScrolledFrame (pack fill both expand)        ← 可滚动容器
   └─ self._cv = Canvas (pack fill both expand)
      └─ self.inner = Frame (window 内嵌, 宽=canvas宽)
         └─ grid = Frame(scroll.inner)  pack fill both expand padx/pady
            ├─ self._card_run(grid)  row=0   sticky="ew"
            └─ self._card_detail(grid) row=1  sticky="nsew"   ← 里面是 RichText
```

### 已知的 Tk 陷阱（已排查确认）
1. **Tk `Text` 控件的 `height=N` 属性 = 可见"行数"，内容超出即被裁剪，不会自动长高、也不自动出滚动条**。这是"显示不全"的第一元凶。
   - `bbox("end-1c")` / `dlineinfo(...)` 在文本超出可见区、或未完成布局时**返回 None**，导致高度测量失真。
   - `count -update` 返回的是**字符数**，不是像素高度——不要当像素用。
2. **Text 一旦被 `pack(fill="both", expand=True)` 或外层 `grid(sticky="nsew")` 锁成某个小尺寸，widget 本身并不会突破这个几何尺寸去显示更多行**。
3. `ScrolledFrame` 的高度取决于 `inner` 的 `<Configure>`（`scrollregion=bbox("all")`）。若子 Card 给的高度不足/过高，滚动 region 就不对，表现为"不能滚到最底下"或"卡住只有一屏"。

### 已尝试的修复与"仍未完全解决"的原因
`RichText.set_content()` 末尾已加：
```python
line_count = int(self.index("end-1c").split(".")[0])
self.configure(height=max(line_count + 1, 4))   # 把 Text 可见行数设为实际逻辑行数
self.update_idletasks()
self.event_generate("<Configure>", when="now")
```
- **单测通过**（独立脚本里 RichText 485px、第20行可见、19行全渲染）。
- **但实际 App 截图仍只显示 2 段 / 截断**。怀疑方向（按可能性排序）：
  1. **逻辑行 ≠ 换行后的可见行**：NOTES 某些行在窄卡片宽度下发生 `wrap="word"` 自动换行，`end-1c` 的行号是"逻辑行"不是"屏幕行"，`height=逻辑行数+1` 仍小于实际屏幕行数 → 底部仍被裁。**需要按真实 wrap 后的屏幕行数设 height**（可先用 `winfo_reqheight()` 反推，或逐行 `dlineinfo` 算出最大可见行号）。
  2. **`grid` 的 `expand=True`/`sticky="nsew"` + Card 高度自适配 与 ScrolledFrame 的联动死锁**：Card 想"撑满 grid 高度"而 grid 想"随内容长高"，两者循环取最小，导致最终 Card/ScrolledFrame 只给了一屏高度，Text 就算 height 够大也被外层裁掉。
  3. **布局时序**：`set_content` 在 widget 尚未完成首次布局时执行，`update_idletasks()` 仍不足以让 wrap 生效；需在窗口显示后（`after`/`<Map>`）再强制一次 re-layout + 读真实行数。
  4. 截图时序：`_shot.py` 等待已加长到 4.5s，若仍截断先排除这个（不要一开始就怀疑截图）。

### 根治方案与实现（本次会话完成）
用户既要"**显示全**"又要"**能滚动**"，那 `RichText` 就不该试图在一个固定 `Text` 里塞下全部内容。二选一更稳妥：

- **方案 X（推荐，改动最小且契合现状）**：让文档页整个走 `ScrolledFrame` 的滚动——即**不要再让 RichText 自己尝试长到全高**，而是限制 Text 为一个固定可视高度 + 给 Text 加自己的垂直滚动，或（更贴合"整页滚动"）改用**非 Text 的纯 Frame 排版**来渲染富文本行（标题/正文/列表各用一个 Label 或 Canvas 逐段 pack 进 `card.body`），由 Card→grid→ScrolledFrame 天然撑高整页，从而整页可滚。Text 控件的固定 height 裁剪 + 不自带滚动，是当前所有问题的根源。
- **方案 Y**：保留 RichText，但给 `height` 设为"wrap 后的真实屏幕行数"，并确保外层不锁高度（去掉 grid 的 expand 锁或 Card 高度钳制）。缺点：仍不可滚动、且换行数变了会再溢出，脆弱。

> 结论供参考：**从架构上，只读文档用 Tk `Text` + 固定 height 硬塞，本身就和"长文档可滚动"冲突**。优先把它改造成逐段 `Label`/`Canvas` 垂直排布放进 Card，让整页随内容增高并由外层 ScrolledFrame 滚动。

---

## 四、验证手段（下个会话务必用）

- 截图脚本：`_shot.py`（在 `E:\autodori-src - 副本` 下运行）。
  ```bash
  rm -f _shot_*.png
  .venv/Scripts/pythonw.exe  # 实际入口是 gui.py
  # 现用方式: 直接 python .venv/Scripts/python.exe _shot.py (会 taskkill 所有 pythonw)
  ```
  注意：`_shot.py` 用 `pythonw.exe` 拉起 `gui.py`，逐个 combo 截图后 `taskkill /im pythonw.exe`。
- 截图输出：`_shot_{theme}_{view}.png`，当前验证重点是 `_shot_light_docs_notes.png` / `_shot_dark_docs_notes.png` 里 **【使用提醒】** 段是否可见、能否滚到底。
- **手动验证**：直接跑 GUI，点侧栏"注意事项/常见问题/用前必读"，滚轮滚动确认能看全。
- 项目有 `.venv`（Pillow 已在），无需重装依赖。

---

## 五、给下一个会话的操作提醒

1. 先复现：跑 `_shot.py` 看 docs.notes 是否还截断（先排除截图时序）。
2. 读 `ui_widgets.py` 的 `RichText`/`Card`/`ScrolledFrame` 与 `gui.py:_detail_docs/_render_view` 后，**优先按"方案 X"（改造文档页为逐段可滚动布局）** 方向重构，一劳永逸。
3. 不要改动已确认完成的齿轮/图标部分（见第二节），除非用户明确新提需求。
4. 改动遵循现有约定：所有控件从 `ui_theme` token 取色、圆角用 `round_points` 多边形采样、不引入 ttk、不新增第三方运行时依赖。

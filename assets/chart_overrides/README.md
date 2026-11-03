# 本地谱面覆盖(chart_overrides)

Bestdori 的谱面 JSON 是社区转录数据,与游戏内谱面存在个别差异。这里放**已取证**的
修正版谱面,由 `BestdoriAPI.get_chart()` 在**查询磁盘缓存之前**加载(缓存里同样是
错的那份数据),因此不需要删 `cache/`。

- 命名:`<曲id>-<难度>.json`,难度用 Bestdori 的小写名(easy/normal/hard/expert/special)。
- 内容:**完整谱面**,Bestdori 原格式(一个 JSON 列表),不是增量补丁。
- 改的是数据,不是手势:除下面列出的谱面外,其余谱面走原始数据 + 原有打歌手势。

## 已收录

| 文件 | 差异 | 依据 |
|---|---|---|
| `655-hard.json` | 补 beat 49 / 第 7 轨(lane 6)普通单键,判定点 486 → 487 | 游戏 BMS 该位置有事件(对象 01=bgm,游戏照样判),与第 2 轨长条起点同拍;上游 PR #13 |
| `35-expert.json` / `681-expert.json` | 去掉 2 条滑条尾的 `flick`(fever 滑条尾) | BMS 对象是 `fever_note_slide_end_a`,不是 `slide_end_flick_a/b`;此前每局固定 miss=2 |
| `147-expert.json` / `674-expert.json` | 同上,去 2 条 | 同上(`fever_note_slide_end_a`) |
| `144-hard.json` | 去 1 条 | 同上(`fever_note_slide_end_a`),此前每局固定 miss=1 |
| `710-expert.json` | 去 1 条 | BMS 对象 `fever_slide_end_a`,此前每局固定 miss=1 |
| `24-expert.json` | 去 2 条 | ときめきエクスペリエンス！;BMS 取不到,判据 = combo 反推(恒定 maxcombo 287 恰好 = 首个 flick 尾序号 288 - 1)+ 滑条本体全是 charge(fever) |
| `16-hard.json` | 去 2 条 | Hacking to the Gate;同上判据(maxcombo 229 = 首个 flick 尾 230 - 1),两条尾巴同拍(161.5,L2/L6) |
| `56-special.json` / `763-special.json` | 补 12 条 `Directional`(fever 方向 flick),判定点 643 → 655 | 熱色スターマイン / 平行歌曲。#763-special 的游戏 BMS 里 `directional_fl_l/r_fever` 恰好 12 条(横跨 2 轨),Bestdori 的 JSON 整条缺失;游戏结算恒定 perfect 643 + miss 12 = 655,恒定 maxcombo 404 = beat 218 之前的判定点数,第一条漏的就是 beat 218 |
| `59-hard.json` | 去掉 1 条绿条尾的 `flick`(beat 135 lane 4),判定点仍 384 | ふわふわ時間;用户慢放确认 combo 197 时第 5 轨绿条尾没松手 → 游戏要判定时刻松手,bot 却划 80ms 再抬手 |
| `59-expert.json` | 去掉 2 条 Slide 尾的 `flick`(beat 135 / 139,都是 lane 5),判定点仍 544 | 同一首歌的 expert 谱面(与 hard 完全不同);用户 2026-11-03 看到 combo 298 与断连后「左起第 6 轨绿条结尾没松手」,对应第 299 / 306 个判定点。BMS 取不到,判据 = 实机观察 + 同族形状;11-03 19:19 复打 **544 全 PERFECT** |
| `12-expert.json` | 去掉 1 条 Slide 尾的 `flick`(beat 158 lane 6),判定点仍 531 | 走り始めたばかりのキミに;用户 2026-11-03 看到 combo 355 时「左起第 7 轨绿条结尾没松手」,对应第 356 个判定点。这条尾巴此前试过 L / U / R 三种方向(`.flickdir.json`)都不归零,说明游戏要的是松手不是划动;11-03 19:22 复打 **531 全 PERFECT** |
| `20-expert.json` | 去掉 2 条 Slide 尾的 `flick`(beat 163.5 lane 0 / beat 179.5 lane 5),判定点仍 811 | シュガーソングとビターステップ;用户 2026-11-03 实机逐帧看到两处「绿条结尾没松手」,对应第 538 / 602 个判定点。同族于 #59,不是方向问题(方向覆盖两轮都没用,已删 `.flickdir.json`);11-03 19:16 复打 **811 全 PERFECT** |

生成脚本见 `debug/local/_exp_gen_chart_override.py`(本地工具,不入库),补丁表里
写着每张谱面的预期判定点数,跑一次即可复现上面这些文件。
覆盖文件的判定点数由 `scripts/_test_chart_override.py` 钉住(离线回归),改覆盖
必须同步改那张表。

## 取证方法(BMS)

游戏的谱面音源包可直接下载,目录编号 = `ceil(曲id/10)*10`(2026-10-31 用十几个
样本复核:413→420、344→350、148→150、320→320、189/185/187→190、586→590、
763→770、681→690),文件名 `<bgmFile>_<难度>.txt`:

```
https://bestdori.com/assets/cn/musicscore/musicscore<ceil(id/10)*10>_rip/<bgmFile>_<难度>.txt
```

⚠️ 不是所有曲子都有:熱色スターマイン(#56)整个歌曲在镜像里都取不到,
只有平行歌曲 #763 有 —— 两张谱面数据相同,用 #763 的 BMS 反推 #56。
取不到时才用 `_diag_bmsfetch.py` 暴力探测 1..1400。

`bgmFile` 来自 `https://bestdori.com/api/songs/<id>.json`。音符通道 = `11~18` +
`51~58`,通道→轨映射 `16→0, 11→1, 12→2, 13→3, 14→4, 15→5, 18→6`(5x 组同构)。
对象值语义(不同曲目编号会变,看 `#WAVxx` 定义):普通键/长条头尾、`flick`,
滑条段/尾,`slide_end_flick_*`(真 flick 尾)、`fever_note_slide_end_*`(fever 尾,
**没有** flick 变体)。

判据:谱面里标了 `flick` 的滑条尾,若游戏对象是 `slide_end_flick_*` → 手势正确;
若是 `fever_*_slide_end_*` → 游戏里那只是普通滑条尾,`flick` 是转录错误。

完整排查流程(分型 / combo 反推 / BMS 用法 / 实机验证口径 / 禁区)见
`AGENTS.md` 第 9.6 节。

BMS 取不到时(老曲目录/文件名不符合现命名规律,id 24/16/20 等)用 combo 反推:
游戏结算的恒定 `maxcombo` 若恰好等于「首个 flick 尾判定点序号 − 1」,且该滑条本体
全是 `charge`(fever),就是同一族;而 `maxcombo` 落在别处(如 #20-expert 的 538 对应
flick 尾之后的同拍单键)说明另有原因,不要套用。

## 按曲方向覆盖(`<id>-<难度>.flickdir.json`,2026-10-31)

老曲的谱面资源在 Bestdori 上取不到,而它们的 flick 尾里有「零位移长条 + 方向
flick」——方向反推不出来。这里放一份**只含方向**的小文件,不重写整份谱面:

> ⚠ 2026-11-03 结论:对 #12-expert / #20-expert 那两条尾巴,改方向**不管用**
> (L/U/R 都试过),因为游戏要的是「判定时刻松手」而不是划动。当前没有 flickdir
> 文件;遇到同类先按下面「实验记录」判,能删 `flick` 就别调方向。

```json
{
  "158,6": "R",
  "30,1": "L",
  "64,4": "U"
}
```

- 键 = `<beat>,<lane>`(beat 用谱面原值,允许小数);值 = `L` 左 / `R` 右 /
  `U` 强制按普通 flick 上划(轨迹反推猜错时用来排除干扰)。
- 优先级:按曲覆盖 > 游戏 BMS > 轨迹反推(见 `chart.notes_to_actions`)。
- 迭代方法:打一局 → 记结算 `maxcombo` → 跑
  `python scripts/_diag_flickdir.py <id> <难度> --maxcombo N` 看第 N+1 个判定点
  是哪条 flick 尾 → 补/翻一条方向 → 重打,直到 miss 归零。

### 实验记录(2026-10-31 ~ 11-03,已全部撤回)

| 曾经的实验 | 试过的值 | 结果 | 结论 |
|---|---|---|---|
| `12-expert.flickdir.json` | `"158,6"`: `L` / `U` / `R` | miss 5 / miss 1 / miss 1 | 方向怎么改都不归零 → 改用整谱覆盖删 `flick` |
| `20-expert.flickdir.json` | `"163.5,0": "U"`、`"179.5,5": "U"` | 两轮仍 miss 2 / maxcombo 538 | 同上 |

两个文件都已在 2026-11-03 删除,当前 `assets/chart_overrides/` 里**没有 flickdir 文件**。
判据(用户实机观察):这些尾巴游戏要的是**判定时刻松手**,不是划动 —— 遇到同类先问
「那一瞬间手指是松手了还是划走了」,不要先调方向。

## 注意

- Bestdori 侧数据如果以后修好了,这里的覆盖会**盖住**新数据,需要人工复核后删除。
- 覆盖文件按工作目录解析(`assets/chart_overrides/`),与 `assets/resource` 一致;
  打包版直接读磁盘上的 `assets/`,新增/修改覆盖文件不用重新打包。

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

生成脚本见仓库根 `_exp_gen_chart_override.py`(本地工具,不入库),补丁表里写着每张
谱面的预期判定点数,跑一次即可复现上面这些文件。

## 取证方法(BMS)

游戏的谱面音源包可直接下载,目录编号与文件名不能靠 id 推算,需要暴力探测:

```
https://bestdori.com/assets/cn/musicscore/musicscore<目录>_rip/<bgmFile>_<难度>.txt
```

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

## 注意

- Bestdori 侧数据如果以后修好了,这里的覆盖会**盖住**新数据,需要人工复核后删除。
- 覆盖文件按工作目录解析(`assets/chart_overrides/`),与 `assets/resource` 一致;
  打包版直接读磁盘上的 `assets/`,新增/修改覆盖文件不用重新打包。

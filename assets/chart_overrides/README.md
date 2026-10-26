# 本地谱面修正

`655-hard.json` 用于「シカ色デイズ」HARD。识别曲目 ID 为 655、
难度为 hard 时，`BestdoriAPI.get_chart()` 优先加载此文件，不读取远端或旧缓存。

基于 2026-10-26 获取的 https://bestdori.com/api/charts/655/hard.json，
仅补入 `{"type":"Single","beat":49,"lane":6}`。
该音符是第二轨长条起点同拍的最右轨单键。
修正后可见判定点从 486 增至 487，其余事件保持原样。

依据：SSM 3.7.0 附带的 `655_shika_iro_hard.txt` 中
`#01218:03010000` 的第 49 拍事件，以及三次实打均为
486 Perfect、1 Miss、最大连击 430 的日志。

此目录与其他 assets 一同发布；程序须从项目或发行目录启动。

import json
import logging
import requests
from diskcache import Cache
from pathlib import Path
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


# 网络请求的硬超时(秒):(连接, 读取)。
# ⚠️ **必须显式传**:requests 默认**没有超时** —— 网络一抖,单个请求会一直挂到
# 操作系统的 TCP 超时(Windows 约 21s),再乘上重试次数 → 启动阶段干等一两分钟,
# 用户看到的就是「点了开始没反应」。
# 2026-10-10 实测日志:`Connection to bestdori.com timed out. (connect timeout=None)`
# + MaxRetryError → bot 在**模块级**拉曲库时超时,未捕获异常直接把进程打死。
_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 20.0
_TIMEOUT = (_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S)


class BestdoriUnavailable(RuntimeError):
    """曲库 / 谱面拉不到(网络不通或站点异常)。

    消息本身是给用户看的中文,调用方直接打出来即可 —— 别再抛原始 urllib3 栈。
    """


class BestdoriAPI:
    base = "https://bestdori.com/api"
    _logger = logging.getLogger("BestdoriAPI")
    _cache = Cache("cache")
    _session = requests.Session()
    _adapter = HTTPAdapter(
        max_retries=Retry(
            # 原来 total=3 + backoff_factor=2:失败要干等约 30s 才有结论。
            # 收紧成最多 2 次、退避 1s → 几秒内就能给用户一个明确结果。
            total=2,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            connect=2,
            read=2,
        )
    )
    _session.mount("http://", _adapter)
    _session.mount("https://", _adapter)

    @staticmethod
    def _fetch_and_cache(url, cache_name, expire=None):
        if cache_ := BestdoriAPI._cache.get(cache_name):
            BestdoriAPI._logger.info(f"Cache hit for {cache_name}")
            return cache_
        try:
            response = BestdoriAPI._session.get(url, timeout=_TIMEOUT).json()
        except Exception as e:
            # 统一换成一句能看懂的中文。带上原始原因便于排查,但不再往外抛 urllib3 的栈。
            raise BestdoriUnavailable(
                "无法连接曲库站点 bestdori.com(%s)。请检查网络或代理后重试。" % e
            ) from e
        BestdoriAPI._cache.set(cache_name, response, expire=expire)
        BestdoriAPI._logger.info(f"Cache set for {cache_name}")
        return response

    @staticmethod
    def get_song_list():
        url = BestdoriAPI.base + "/songs/all.5.json"
        # ⚠️ 这个 TTL 决定「网络抖动 / 离线时还能不能跑」,别调回 1 小时。
        # 原来 expire=3600*1 → 每小时都要重新联网拉一次,网络一抖整个启动就失败;
        # 曲目表只有出新曲才变,30 天足够。真需要立刻刷新,删掉 cache/ 目录即可。
        return BestdoriAPI._fetch_and_cache(url, "allsongs", expire=3600 * 24 * 30)

    @staticmethod
    def get_chart(song_id: str, difficulty: str):
        cacheid = f"{song_id}-{difficulty}"
        # 本地谱面覆盖(数据侧兜底):Bestdori 是社区转录数据,个别音符与游戏内
        # 谱面不一致,盲修要命,所以只对已取证的具体谱面做数据侧修正(2026-10-26):
        #   ① 缺音:#655-hard 游戏 487 个判定点、Bestdori 只有 486 —— 缺 beat 49
        #      第 7 轨的普通单键(BMS 里对象写的是 01=bgm,游戏照样判;上游 PR #13)。
        #   ② flick 错标:#35/#681、#147/#674、#144-hard、#710-expert 的「fever
        #      滑条尾」被标成 flick,而游戏 BMS 里是 fever_note_slide_end_a /
        #      fever_slide_end_a(没有 flick 变体)→ 按 flick 上划离轨,尾判固定 miss。
        #      (对照:能打中的谱面里这类尾巴是 slide_end_flick_a/b 对象。)
        # override 文件 = 完整谱面(Bestdori 原格式 JSON 列表),放
        # assets/chart_overrides/<曲id>-<难度>.json,不动打歌手势。
        # ⚠️ 必须排在磁盘缓存之前:缓存里就是那份错数据,不能盖过修复。
        override = Path("assets/chart_overrides") / f"{song_id}-{difficulty}.json"
        if override.is_file():
            BestdoriAPI._logger.info(f"Local chart override for {cacheid}")
            return json.loads(override.read_text(encoding="utf-8"))
        url = BestdoriAPI.base + f"/charts/{song_id}/{difficulty}.json"
        return BestdoriAPI._fetch_and_cache(url, cacheid)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    songlist = BestdoriAPI.get_song_list()
    chart = BestdoriAPI.get_chart(1, "easy")
    pass

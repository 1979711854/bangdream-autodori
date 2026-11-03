import json
import logging
import re
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

    # ---- 方向フリック(方向 flick)方向表(2026-10-31) ----------------------
    #
    # 背景:#413 DRIVE US CRAZY / #189 えがお･シング･あ･ソング / #344 Sing Alive
    # 这几首每次都在曲中段「演出失败(生命值已用尽)」,而下发链全程干净
    # (在线 lag 亚毫秒、曲内漂移 ±3ms)。对账游戏 BMS 才定位到:这几张谱的
    # 滑条/长条尾巴大量(31~37 条)是「方向フリック」——
    # `slide_end_dir_flick_l/r_*`、`long_end_dir_flick_l/r` 对象,游戏要求
    # 往左或往右划;chart.py 以前一律向上划 300px,方向不对就每条都判 MISS,
    # 生命值被清空,崩溃点因此随曲子落在 40~90 秒不等。
    #
    # 对照(同一套 BMS 对账):能全 AP/通过的特殊谱面,尾巴全是普通
    # `slide_end_flick_a/b`(#586 13 条、#387 43 条、#148 17 条、#120 8 条);
    # 三首每次都崩的谱面,尾巴 100% 是 `*_dir_flick_*`。分界线非常干净。
    #
    # Bestdori 的 chart JSON **不带方向**(2026-10-31 核对过线上与缓存,连接里
    # 只有 `flick: true`),方向只能从游戏自己的谱面文件(BMS)读。
    # CN 服的 musicscore 在 Bestdori 资源里,目录按 10 曲一组切:
    #   assets/cn/musicscore/musicscore<ceil(曲id/10)*10:03d>_rip/<bgmFile>_<难度>.txt
    # 实测 413→420、344→350、148→150、320→320、189/185/187→190、586→590。
    # ⚠️ 以前 AGENTS 里写的是「目录编号不可推算(681→690 是巧合)」——那是只拿
    # 到一个样本时的结论;按 10 取整在十几个样本上都成立,可以稳定推算。
    #
    # 取不到(老曲目录/文件名不符、断网)返回 None,由 chart.py 按轨迹反推兜底,
    # 反推不出来就保持原来的向上划 —— 绝不让取方向失败影响打歌。
    BMS_BASE = "https://bestdori.com/assets/cn/musicscore"
    # 通道 → 轨道(1x 组=普通键与滑条,5x 组=绿条,两组同构):
    # 16→0, 11→1, 12→2, 13→3, 14→4, 15→5, 18→6(见 AGENTS 9.6 的 BMS 解码)。
    _BMS_LANE_OF = {
        "16": 0, "11": 1, "12": 2, "13": 3, "14": 4, "15": 5, "18": 6,
        "56": 0, "51": 1, "52": 2, "53": 3, "54": 4, "55": 5, "58": 6,
    }
    # 只认这四种对象。普通 flick 尾(slide_end_flick_a/b)不在这里,保持上划。
    _BMS_DIR_FLICK_PREFIX = (
        "slide_end_dir_flick_l", "slide_end_dir_flick_r",
        "long_end_dir_flick_l", "long_end_dir_flick_r",
    )

    @staticmethod
    def _bms_dir_of(song_id) -> int:
        """musicscore 目录号 = 曲 id 向上取整到 10 的倍数。"""
        return ((int(song_id) + 9) // 10) * 10

    @staticmethod
    def _parse_bms_flick_dirs(text: str) -> dict:
        """从 BMS 文本解析方向 flick 尾巴 → {(beat, lane): "L"/"R"}。

        纯函数,离线回归见 scripts/_test_flickdir.py。
        对象名按 #WAVxx 查名字(编号各曲不同),不能按编号猜。
        """
        wav = {}
        dirs = {}
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("#"):
                continue
            m = re.match(r"^#WAV(\w+)\s+(\S+)$", line)
            if m:
                wav[m.group(1).upper()] = m.group(2).lower()
                continue
            m = re.match(r"^#(\d{3})(\w{2}):(.+)$", line)
            if not m:
                continue
            meas, ch, val = int(m.group(1)), m.group(2), m.group(3).strip()
            lane = BestdoriAPI._BMS_LANE_OF.get(ch)
            if lane is None:
                continue
            pairs = [val[i:i + 2] for i in range(0, len(val), 2)]
            if not pairs:
                continue
            for i, pair in enumerate(pairs):
                pair = pair.upper()
                if pair in ("00", ""):
                    continue
                name = wav.get(pair, "")
                if not name.startswith(BestdoriAPI._BMS_DIR_FLICK_PREFIX):
                    continue
                direction = (
                    "L" if name.startswith(
                        ("slide_end_dir_flick_l", "long_end_dir_flick_l")
                    ) else "R"
                )
                beat = round(meas * 4 + 4.0 * i / len(pairs), 3)
                dirs[(beat, lane)] = direction
        return dirs

    @staticmethod
    def get_flick_directions(song_id: str, difficulty: str):
        """取一张谱面的方向 flick 尾巴 → {(beat, lane): "L"/"R"}。

        返回:
          dict —— BMS 取到了。空 dict = 本谱没有方向 flick(尾巴全是普通 flick)。
          None —— 取不到(老曲没有 BMS / 断网 / 解析失败),调用方按轨迹反推。
        结果(含"取不到")都进 diskcache:同一首歌只在第一次加载时联网。
        """
        cacheid = f"flickdir-{song_id}-{difficulty}"
        cached = BestdoriAPI._cache.get(cacheid)
        if isinstance(cached, dict) and "available" in cached:
            BestdoriAPI._logger.debug("Cache hit for %s", cacheid)
            return cached.get("dirs") if cached["available"] else None

        try:
            info = BestdoriAPI._fetch_and_cache(
                BestdoriAPI.base + f"/songs/{song_id}.json",
                f"songinfo-{song_id}",
                expire=3600 * 24 * 30,
            )
            bgm = (info or {}).get("bgmFile")
            if not bgm:
                raise ValueError("song info 没有 bgmFile")
            url = "{}/musicscore{:03d}_rip/{}_{}.txt".format(
                BestdoriAPI.BMS_BASE, BestdoriAPI._bms_dir_of(song_id),
                bgm, difficulty,
            )
            resp = BestdoriAPI._session.get(url, timeout=_TIMEOUT)
            text = (
                resp.content.decode("utf-8", errors="replace")
                if resp.status_code == 200 else ""
            )
            # 14084 字节的 SPA 兜底页 / 404:该目录没有这个文件(老曲常见)。
            if "#WAV" not in text[:4000]:
                BestdoriAPI._logger.info(
                    "方向フリック: %s 没有 BMS 资源(回退轨迹反推)", cacheid
                )
                BestdoriAPI._cache.set(
                    cacheid, {"available": False, "dirs": {}},
                    expire=3600 * 6,
                )
                return None
            dirs = BestdoriAPI._parse_bms_flick_dirs(text)
            BestdoriAPI._cache.set(
                cacheid, {"available": True, "dirs": dirs},
                expire=3600 * 24 * 30,
            )
            BestdoriAPI._logger.info(
                "方向フリック: %s 取到 %d 条", cacheid, len(dirs)
            )
            return dirs
        except Exception as e:  # noqa: BLE001 —— 取方向失败绝不能拖垮打歌
            BestdoriAPI._logger.warning(
                "方向フリック: %s 取不到(%s),本曲回退轨迹反推", cacheid, e
            )
            # 网络问题可能只是一时,缓存 1 小时让下一首还能重试。
            BestdoriAPI._cache.set(
                cacheid, {"available": False, "dirs": {}}, expire=3600
            )
            return None

    # ---- 曲绘(重名曲目判别,2026-10-31) -----------------------------------
    #
    # 问题:オレンジ 在曲库里有 #316 / #676 两个同名条目,expert 都是 **25 级**,
    # 但谱面完全不同(651 / 683 判定、BPM 200 / 140)。`_pick_song_id` 原来的
    # 「等级相同 = 同一份谱面」判据在这对曲目上失效,按默认 #676 打而游戏里是
    # #316 → 整首对不上,4.4 秒就崩(战绩库 10-27 起 8 局全灭)。
    # 选曲页右侧能看到曲绘(封面),两张差别极大,可以直接比对。
    #
    # 资源路径(2026-10-31 从 bestdori 页面实测):目录号 = ceil(曲id/10)*10,
    # **不补零**(20 / 320 / 680):
    #   assets/<server>/musicjacket/musicjacket<dir>/
    #     assets-star-forassetbundle-startapp-musicjacket-musicjacket<dir>-<封面名>-jacket.png
    # cn 服缺部分老封面(hashikimi 那类),先试 jp 再试 cn。
    JACKET_BASE = (
        "https://bestdori.com/assets/{server}/musicjacket/musicjacket{dir}_rip"
    )
    _JACKET_FILE = (
        "assets-star-forassetbundle-startapp-musicjacket-musicjacket{dir}-{name}-jacket.png"
    )

    @staticmethod
    def get_song_jacket(song_id: str):
        """取曲绘 PNG 字节(给重名候选判别用);取不到返回 None。

        结果进 diskcache:同一首只在第一次选曲时联网。
        """
        cacheid = f"jacket-{song_id}"
        cached = BestdoriAPI._cache.get(cacheid)
        if isinstance(cached, bytes):
            return cached or None
        try:
            info = BestdoriAPI._fetch_and_cache(
                BestdoriAPI.base + f"/songs/{song_id}.json",
                f"songinfo-{song_id}",
                expire=3600 * 24 * 30,
            )
            names = (info or {}).get("jacketImage") or []
            name = names[0] if names else None
            if not name:
                raise ValueError("song info 没有 jacketImage")
            d = ((int(song_id) + 9) // 10) * 10
            fn = BestdoriAPI._JACKET_FILE.format(dir=d, name=name)
            for server in ("jp", "cn"):
                url = "{}/{}".format(
                    BestdoriAPI.JACKET_BASE.format(server=server, dir=d), fn
                )
                try:
                    # 封面只用于重名判别,别用全局 _TIMEOUT(5/20s + 重试)拖住
                    # 选曲节点的 4s 超时;失败就换下一个服务器/回退默认。
                    resp = BestdoriAPI._session.get(url, timeout=(3.0, 8.0))
                except Exception:
                    continue
                # 不存在时 CDN 返回 14084 字节的 SPA 兜底页(text/html)。
                if (
                    resp.status_code == 200
                    and len(resp.content) > 2000
                    and resp.headers.get("content-type", "").startswith("image")
                ):
                    BestdoriAPI._cache.set(
                        cacheid, resp.content, expire=3600 * 24 * 30
                    )
                    BestdoriAPI._logger.info(
                        "曲绘: %s 取到 %d 字节", cacheid, len(resp.content)
                    )
                    return resp.content
            raise ValueError("封面资源不存在")
        except Exception as e:  # noqa: BLE001 —— 取封面失败只影响判别,不能拖垮选曲
            BestdoriAPI._logger.warning("曲绘: %s 取不到(%s)", cacheid, e)
            BestdoriAPI._cache.set(cacheid, b"", expire=3600 * 6)
            return None

    # ---- 按曲方向覆盖(2026-10-31) ----------------------------------------
    #
    # 用途:老曲(#12 / #20 等)的谱面资源在 Bestdori 上取不到,而它们的 flick 尾
    # 里有「零位移长条 + 方向 flick」(头尾同轨),轨迹反推不出左右;方向猜错的
    # 那几条每局固定 MISS(#12 miss 5、#20 miss 2)。
    # 这里放一份**只含方向**的小文件,不重写整份谱面:
    #   assets/chart_overrides/<曲id>-<难度>.flickdir.json
    #   {"30,1": "L", "64,4": "R", ...}   # "beat,lane" → 方向
    # 优先级最高(覆盖 > BMS > 轨迹反推),方便用实机 maxcombo 一条条反推补齐。
    @staticmethod
    def _parse_flick_direction_overrides(raw) -> dict:
        """{"<beat>,<lane>": "L"/"R"} → {(beat, lane): "L"/"R"}(纯函数,便于回归)。"""
        dirs = {}
        for key, value in (raw or {}).items():
            try:
                beat_s, lane_s = str(key).split(",")
                v = str(value).strip().upper()
                if v.startswith("L"):
                    d = "L"
                elif v.startswith("U"):
                    # U = 明确按普通 flick 上划(方向反推猜错时用来排除干扰)
                    d = "U"
                else:
                    d = "R"
                dirs[(round(float(beat_s), 3), int(lane_s))] = d
            except Exception:
                continue
        return dirs

    @staticmethod
    def get_flick_direction_overrides(song_id: str, difficulty: str) -> dict:
        """读按曲方向覆盖 → {(beat, lane): "L"/"R"};没有/读失败返回 {}。"""
        path = Path("assets/chart_overrides") / f"{song_id}-{difficulty}.flickdir.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 —— 覆盖坏了只告警,不影响打歌
            BestdoriAPI._logger.warning("方向覆盖读取失败(%s): %s", path, e)
            return {}
        dirs = BestdoriAPI._parse_flick_direction_overrides(raw)
        BestdoriAPI._logger.info("方向覆盖: %s 读取 %d 条", path.name, len(dirs))
        return dirs

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

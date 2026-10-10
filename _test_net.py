# -*- coding: utf-8 -*-
"""曲库拉取失败的健壮性回归(_test_net.py)。

背景(2026-10-10 用户实报):bot 在**模块级**拉 bestdori 曲库,网络一抖就
`ConnectTimeout` → 未捕获异常 → PyInstaller 直接 "Failed to execute script",
进程无声死掉,GUI 里看起来是「点了开始没反应」。而且 requests 默认**没有超时**,
单个请求会挂到系统 TCP 超时再乘重试次数 → 前面还要干等一两分钟。

这里守三件事:
  1. 请求必须带显式 timeout;
  2. 拉取失败要抛**能看懂的中文** BestdoriUnavailable,而不是 urllib3 的栈;
  3. 模块级那次拉取失败时,autodori 必须**干净退出并打出中文提示**,不能带 traceback 崩。

跑法:.venv/Scripts/python.exe _test_net.py
"""
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")

import api as A

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  PASS  %-50s -> %s" % (label, got))
    else:
        FAIL += 1
        print("  FAIL  %-50s -> %s (want %s)" % (label, got, want))


class BoomSession:
    """模拟连不上:requests 抛 ConnectTimeout 那类异常。"""

    def __init__(self):
        self.kwargs = None

    def get(self, url, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("Connection to bestdori.com timed out.")


def test_timeout_and_message():
    print("=== 1. 请求带显式 timeout + 失败抛中文异常 ===")
    real_session = A.BestdoriAPI._session
    real_cache_get = A.BestdoriAPI._cache.get
    A.BestdoriAPI._session = BoomSession()
    # 让缓存一定不命中
    A.BestdoriAPI._cache.get = lambda *a, **k: None
    try:
        check("超时常量已定义", hasattr(A, "_TIMEOUT") and isinstance(A._TIMEOUT, tuple), True)
        try:
            A.BestdoriAPI._fetch_and_cache("https://x/y.json", "probe")
            check("失败时抛异常", False, True)
        except A.BestdoriUnavailable as e:
            check("抛的是 BestdoriUnavailable", True, True)
            check("消息是中文且可读", "无法连接曲库站点" in str(e) and "bestdori.com" in str(e), True)
            check("不带 urllib3 原始栈", "urllib3" not in str(e) and "Traceback" not in str(e), True)
        except Exception as e:  # noqa: BLE001
            check("抛的是 BestdoriUnavailable(而不是 %s)" % type(e).__name__, False, True)
        check("确实传了 timeout", (A.BestdoriAPI._session.kwargs or {}).get("timeout") is not None, True)
    finally:
        A.BestdoriAPI._session = real_session
        A.BestdoriAPI._cache.get = real_cache_get


def test_song_list_expire():
    print("=== 2. 曲库缓存 TTL 足够长(网络抖动/离线还能跑) ===")
    real_get, real_set, real_sess = (A.BestdoriAPI._cache.get,
                                     A.BestdoriAPI._cache.set,
                                     A.BestdoriAPI._session)
    captured = {}

    class OkSession:
        def get(self, url, **kwargs):
            class R:
                @staticmethod
                def json():
                    return {"1": {"musicTitle": ["t"]}}
            return R()

    A.BestdoriAPI._cache.get = lambda *a, **k: None
    A.BestdoriAPI._cache.set = lambda k, v, expire=None: captured.update(expire=expire)
    A.BestdoriAPI._session = OkSession()
    try:
        A.BestdoriAPI.get_song_list()
    finally:
        A.BestdoriAPI._cache.get, A.BestdoriAPI._cache.set, A.BestdoriAPI._session = \
            real_get, real_set, real_sess
    exp = captured.get("expire")
    check("曲库 TTL >= 7 天(实际 %s 秒)" % exp, bool(exp) and exp >= 7 * 24 * 3600, True)


def test_module_level_guard():
    print("=== 3. 模块级拉取失败 -> 干净退出 + 中文提示(不是 traceback) ===")
    code = (
        "import sys; sys.path.insert(0, 'src')\n"
        "import api\n"
        "def boom():\n"
        "    raise RuntimeError('Connection to bestdori.com timed out.')\n"
        "api.BestdoriAPI.get_song_list = staticmethod(boom)\n"
        "import autodori\n"
        "print('SHOULD_NOT_REACH_HERE')\n"
    )
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    check("退出码非 0", p.returncode != 0, True)
    check("没有跑到后面的代码", "SHOULD_NOT_REACH_HERE" not in out, True)
    check("打出了中文提示", "无法获取曲库" in out, True)
    check("没有 PyInstaller 式未捕获栈", "Traceback (most recent call last)" not in out, True)


def main():
    test_timeout_and_message()
    test_song_list_expire()
    test_module_level_guard()
    print()
    print("=" * 60)
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

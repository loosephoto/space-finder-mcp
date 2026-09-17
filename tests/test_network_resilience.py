"""ネットワーク遮断時の「素早く諦める」挙動の回帰テスト。

CelesTrak は短時間に多数のリクエストを送ると IP 単位で遮断し、しかも TCP の connect が
返ってこない（blackhole）ことがある。実測では 1 回の呼び出しが 120 秒以上固まり、
並列に走っている他のツール呼び出しまで待たされた。nasa_budget と同じく「投げる前に止める」
方針にしてあるので、遮断中は HTTP を出さずに即失敗すること。
"""
import sys
import time
import unittest

import requests

sys.path.insert(0, "src")


class NetworkResilienceTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import celestrak

        self.celestrak = celestrak
        self._old_until = celestrak._COOLDOWN_UNTIL
        self._old_get = requests.get

    def tearDown(self):
        self.celestrak._COOLDOWN_UNTIL = self._old_until
        self.celestrak._COOLDOWN_LAST = ""
        requests.get = self._old_get

    def test_celestrak_fails_fast_after_connection_failure(self):
        calls = []

        def boom(*args, **kwargs):
            calls.append(kwargs.get("timeout"))
            raise requests.ConnectionError("simulated blackhole")

        requests.get = boom
        self.celestrak._COOLDOWN_UNTIL = 0.0

        with self.assertRaises(requests.RequestException):
            self.celestrak._get({"CATNR": 25544})
        self.assertEqual(len(calls), 1)
        # connect タイムアウトを渡していること（無指定だと blackhole で無限に待つ）
        self.assertIsNotNone(calls[0])

        started = time.perf_counter()
        with self.assertRaises(requests.RequestException):
            self.celestrak._get({"CATNR": 25544})
        self.assertEqual(len(calls), 1, "遮断中なのに再び HTTP を投げています（fail fast していない）")
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_celestrak_403_records_cooldown(self):
        """403 を受けたら遮断として記憶し、次の呼び出しは HTTP を出さずに即失敗すること。"""
        from space_finder_mcp import celestrak

        class Resp403(requests.Response):
            def __init__(self):
                super().__init__()
                self.status_code = 403
                self._content = b""
                self.url = "https://celestrak.org/NORAD/elements/gp.php"

            def raise_for_status(self):
                err = requests.HTTPError("403 Client Error", response=self)
                raise err

        calls = []

        def forbidden(*args, **kwargs):
            calls.append(1)
            return Resp403()

        requests.get = forbidden
        celestrak._COOLDOWN_UNTIL = 0.0
        with self.assertRaises(requests.HTTPError):
            celestrak._get({"CATNR": 25544})
        remaining, reason = celestrak.blocked_status()
        self.assertGreater(remaining, 0.0, "403 後に遮断として記録されていません")
        self.assertIn("403", reason)

        with self.assertRaises(requests.RequestException):
            celestrak._get({"CATNR": 25544})       # fail fast（HTTP を投げない）
        self.assertEqual(len(calls), 1, "遮断中の再呼び出しで HTTP を投げています")

    def test_tools_return_error_result_when_celestrak_is_blocked(self):
        """遮断中でもツールは例外を漏らさず error 付き CallToolResult を返すこと。"""
        from space_finder_mcp.server import mcp

        def boom(*args, **kwargs):
            raise requests.ConnectionError("simulated blackhole")

        requests.get = boom
        self.celestrak._COOLDOWN_UNTIL = time.time() + 60      # すでに遮断中
        for name, kw in (("sat_tle", {"name": "iss"}), ("sat_ground_track", {"name": "iss"})):
            tool = mcp._tool_manager._tools[name]
            fn = getattr(tool.fn, "sync_fn", tool.fn)
            started = time.perf_counter()
            res = fn(**kw)
            elapsed = time.perf_counter() - started
            self.assertIsInstance(res.structuredContent, dict)
            self.assertLess(elapsed, 5.0, "{} が遮断中でも待たされています".format(name))


if __name__ == "__main__":
    unittest.main()

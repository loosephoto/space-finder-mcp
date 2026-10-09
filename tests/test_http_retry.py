"""http_retry.get の再試行挙動の回帰テスト。

- 一時的な ConnectionError（DNS 解決失敗を含む）は再試行して成功を返すこと
- 再試行を使い切ったら最後の例外を送出すること
- HTTP エラー（4xx/5xx）やタイムアウトは再試行しないこと（遮断の fail fast を壊さない）
"""
import sys
import unittest

import requests

sys.path.insert(0, "src")


class HttpRetryTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import http_retry

        self.mod = http_retry
        self._old_get = requests.get
        self._old_sleep = http_retry.time.sleep
        http_retry.time.sleep = lambda *a, **k: None   # テストでは待たない

    def tearDown(self):
        requests.get = self._old_get
        self.mod.time.sleep = self._old_sleep

    def test_retries_transient_connection_error_then_succeeds(self):
        calls = []

        class Resp(requests.Response):
            def __init__(self):
                super().__init__()
                self.status_code = 200
                self._content = b"ok"

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) < 2:
                raise requests.ConnectionError("simulated name resolution failure")
            return Resp()

        requests.get = flaky
        r = self.mod.get("https://example.invalid/x", timeout=(5, 5))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(calls), 2, "一時的な接続失敗が再試行されていません")

    def test_raises_last_connection_error_after_exhausting_retries(self):
        calls = []

        def boom(*args, **kwargs):
            calls.append(1)
            raise requests.ConnectionError("still failing")

        requests.get = boom
        with self.assertRaises(requests.ConnectionError):
            self.mod.get("https://example.invalid/x")
        self.assertEqual(len(calls), self.mod.RETRIES + 1)

    def test_http_error_is_not_retried(self):
        calls = []

        class Resp500(requests.Response):
            def __init__(self):
                super().__init__()
                self.status_code = 500
                self._content = b""

        def server_error(*args, **kwargs):
            calls.append(1)
            return Resp500()

        requests.get = server_error
        r = self.mod.get("https://example.invalid/x")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(len(calls), 1, "HTTP エラーを再試行しています（fail fast を壊す）")

    def test_timeout_is_not_retried(self):
        calls = []

        def timed_out(*args, **kwargs):
            calls.append(1)
            raise requests.Timeout("read timed out")

        requests.get = timed_out
        with self.assertRaises(requests.Timeout):
            self.mod.get("https://example.invalid/x")
        self.assertEqual(len(calls), 1, "タイムアウトを再試行しています（待ち時間が倍増する）")


if __name__ == "__main__":
    unittest.main()

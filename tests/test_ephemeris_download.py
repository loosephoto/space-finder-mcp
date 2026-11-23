"""de421.bsp の取得（初回ダウンロード）とエラー契約の回帰テスト。

issue #2: TLS 検証を差し替えるプロキシ配下で Skyfield のダウンロードが
`CERTIFICATE_VERIFY_FAILED` で失敗し、エラー文面からは「どこへ何を置けばよいか」
が分からなかった。実装は

- 明示 CA（SPACE_FINDER_CA_BUNDLE 等）→ OS ストア → certifi の順に試し、
  JPL 本家 → NAIF 旧版アーカイブへフォールバックする（検証は無効化しない）
- 取得先のパス・手動取得の curl・CA の指定方法を `EphemerisUnavailable` の文面に入れる
- ツール境界（`server._guarded`）では例外ではなく `structuredContent.error =
  "ephemeris_unavailable"` の結果として返す

ここではネットワークに出ずに、その契約だけを固定する。
"""
import os
import ssl
import sys
import tempfile
import unittest

sys.path.insert(0, "src")

from space_finder_mcp import ephemeris  # noqa: E402

_ENV_KEYS = ("SPACE_FINDER_DATA_DIR", "SPACE_FINDER_DE421", "SPACE_FINDER_CA_BUNDLE",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


class _EnvSandbox(unittest.TestCase):
    """環境変数と module 属性を退避し、テストごとに元へ戻す。"""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self._saved = {name: getattr(ephemeris, name)
                       for name in ("_fetch", "_ca_contexts", "ensure_de421",
                                    "_EPH", "_TS")}

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(ephemeris, name, value)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _kernel(self, name="de421.bsp"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"\0" * (ephemeris.DE421_MIN_BYTES + 1))
        return path


class EnsureDe421Tests(_EnvSandbox):
    def test_existing_file_is_used_without_network(self):
        path = self._kernel()
        os.environ["SPACE_FINDER_DE421"] = path

        def boom(*args, **kwargs):
            raise AssertionError("取得済みのファイルで通信してはいけない")

        ephemeris._fetch = boom
        ephemeris._ca_contexts = boom
        self.assertEqual(ephemeris.ensure_de421(), os.path.abspath(path))

    def test_env_overrides_data_dir_and_file(self):
        os.environ["SPACE_FINDER_DATA_DIR"] = self.dir
        self.assertEqual(ephemeris.de421_path(),
                         os.path.join(self.dir, "de421.bsp"))
        custom = os.path.join(self.dir, "custom.bsp")
        os.environ["SPACE_FINDER_DE421"] = custom
        self.assertEqual(ephemeris.de421_path(), os.path.abspath(custom))

    def test_certificate_failure_reports_path_and_remedies(self):
        dest = os.path.join(self.dir, "de421.bsp")
        os.environ["SPACE_FINDER_DE421"] = dest
        ephemeris._ca_contexts = lambda: [("SPACE_FINDER_CA_BUNDLE",
                                           ssl.create_default_context())]

        def fail(url, dest_path, ctx):
            raise ssl.SSLCertVerificationError(
                1, "certificate verify failed: self-signed certificate in certificate chain")

        ephemeris._fetch = fail
        with self.assertRaises(ephemeris.EphemerisUnavailable) as cm:
            ephemeris.ensure_de421()
        exc = cm.exception
        msg = str(exc)
        # 文面だけで対処できること（配置先・手動取得・CA・既存ファイルの指定）
        self.assertIn(dest, msg)
        self.assertIn("curl -L -o", msg)
        self.assertIn(ephemeris.DE421_URLS[0], msg)
        self.assertIn("SPACE_FINDER_CA_BUNDLE", msg)
        self.assertIn("SPACE_FINDER_DE421", msg)
        self.assertIn("certificate", exc.reason)
        self.assertEqual(exc.path, dest)

        res = ephemeris.error_result(exc)
        self.assertEqual(res.structuredContent["error"], "ephemeris_unavailable")
        self.assertEqual(res.structuredContent["de421_path"], dest)
        self.assertEqual(res.structuredContent["data_dir"], self.dir)
        self.assertIn("curl", res.content[0].text)

    def test_certificate_failure_tries_every_ca_and_mirror(self):
        attempts = []
        contexts = [("explicit", object()), ("os", object())]
        ephemeris._ca_contexts = lambda: list(contexts)

        def fail(url, dest, ctx):
            attempts.append((url, ctx))
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")

        ephemeris._fetch = fail
        with self.assertRaises(ephemeris.EphemerisUnavailable):
            ephemeris.ensure_de421(os.path.join(self.dir, "de421.bsp"))
        # 同じ URL を全 CA で試し、次にミラーを試す（CA を替えれば直る類の失敗）
        self.assertEqual(len(attempts), len(contexts) * len(ephemeris.DE421_URLS))
        for url in ephemeris.DE421_URLS:
            self.assertEqual([c for u, c in attempts if u == url],
                             [c for _, c in contexts])

    def test_network_failure_jumps_to_next_mirror(self):
        attempts = []
        ephemeris._ca_contexts = lambda: [("os", object()), ("certifi", object())]

        def fail(url, dest, ctx):
            attempts.append(url)
            raise OSError("Connection refused")

        ephemeris._fetch = fail
        with self.assertRaises(ephemeris.EphemerisUnavailable):
            ephemeris.ensure_de421(os.path.join(self.dir, "de421.bsp"))
        # 証明書以外の失敗は CA を替えても直らないので、ミラーだけ切り替える
        self.assertEqual(attempts, list(ephemeris.DE421_URLS))

    def test_truncated_file_is_not_reused(self):
        dest = os.path.join(self.dir, "de421.bsp")
        with open(dest, "wb") as f:
            f.write(b"\0" * 1024)                    # 途中まで落ちたファイル
        ephemeris._ca_contexts = lambda: [("os", object())]
        ephemeris._fetch = lambda url, p, ctx: None  # 何も落とさない
        with self.assertRaises(ephemeris.EphemerisUnavailable):
            ephemeris.ensure_de421(dest)
        # 壊れた実体は消し、取得できたものとして返さない
        self.assertFalse(os.path.exists(dest))

    def test_load_does_not_cache_partial_state(self):
        saved = ephemeris._EPH, ephemeris._TS
        try:
            ephemeris._EPH = ephemeris._TS = None

            def fail(path=None):
                raise ephemeris.EphemerisUnavailable("取得元に接続できませんでした", path)

            ephemeris.ensure_de421 = fail
            with self.assertRaises(ephemeris.EphemerisUnavailable):
                ephemeris.load()
            self.assertIsNone(ephemeris._EPH)
        finally:
            ephemeris._EPH, ephemeris._TS = saved


class FetchTests(unittest.TestCase):
    def test_short_download_leaves_no_files(self):
        """Content-Length に足りない応答は成功にしない（.part も残さない）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = os.path.join(tmp.name, "de421.bsp")

        class _Resp:
            headers = {"Content-Length": str(ephemeris.DE421_MIN_BYTES + 10)}

            def __init__(self):
                self.sent = 0

            def read(self, n):
                if self.sent:
                    return b""
                self.sent = 1
                return b"x" * 1024

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        orig = ephemeris.urllib.request.urlopen
        ephemeris.urllib.request.urlopen = lambda *a, **k: _Resp()
        try:
            with self.assertRaises(OSError):
                ephemeris._fetch(ephemeris.DE421_URLS[0], dest, None)
        finally:
            ephemeris.urllib.request.urlopen = orig
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))


class ServerBoundaryTests(unittest.TestCase):
    def test_ephemeris_unavailable_becomes_error_result(self):
        """ツール境界では例外ではなく結果を返す（対処手順を content に載せる）。"""
        from space_finder_mcp import server

        def tool(**kwargs):
            raise ephemeris.EphemerisUnavailable("certificate verify failed")

        res = server._guarded(tool, (), {})
        self.assertEqual(res.structuredContent["error"], "ephemeris_unavailable")
        self.assertIn("de421.bsp", res.content[0].text)


if __name__ == "__main__":
    unittest.main()

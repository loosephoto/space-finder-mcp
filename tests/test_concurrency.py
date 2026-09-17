"""並列ツール呼び出し（LLM が複数ツールを同時に投げる前提）の回帰テスト。

守る性質:
1. 同じ引数の並行呼び出しは1回の実行にまとまる（ttl_cache の single-flight）
2. 先頭が失敗しても後続は待ち続けず、自力で実行して結果を返す
3. 同期ツールはワーカースレッドで実行され、複数の呼び出しが本当に並行になる
4. 登録済みツールはすべてスレッド実行ラッパー（引数スキーマ・docstring は元のまま）
"""
import asyncio
import inspect
import sys
import threading
import time
import unittest

sys.path.insert(0, "src")


class ConcurrencyTests(unittest.TestCase):
    def test_ttl_cache_single_flight(self):
        from space_finder_mcp.cache import ttl_cache

        calls = []

        @ttl_cache(60, maxsize=4)
        def slow(x):
            calls.append(x)
            time.sleep(0.25)
            return {"v": x, "call": len(calls)}

        outs = []
        threads = [threading.Thread(target=lambda: outs.append(slow(7))) for _ in range(6)]
        started = time.perf_counter()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        elapsed = time.perf_counter() - started

        self.assertEqual(len(calls), 1, "同一引数の並行呼び出しが1回に集約されていません")
        self.assertEqual(len(outs), 6)
        self.assertEqual({o["call"] for o in outs}, {1}, "並行呼び出しで別々の結果が返りました")
        self.assertLess(elapsed, 0.75, "待ち合わせが効いていません（逐次実行になっています）")

    def test_ttl_cache_leader_failure_does_not_hang_followers(self):
        from space_finder_mcp.cache import ttl_cache

        calls = []

        @ttl_cache(60, maxsize=4)
        def flaky(x):
            calls.append(x)
            if len(calls) == 1:
                time.sleep(0.1)
                raise RuntimeError("leader failed")
            return "ok"

        out = []

        def run():
            try:
                out.append(flaky(1))
            except RuntimeError as e:
                out.append(str(e))

        threads = [threading.Thread(target=run) for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)
        self.assertFalse(any(th.is_alive() for th in threads), "後続が待ち続けています")
        self.assertIn("ok", out, "先頭失敗後に後続が自力で実行していません")

    def test_threaded_wrapper_runs_concurrently(self):
        from space_finder_mcp.server import _threaded

        @_threaded
        def sleepy(tag):
            time.sleep(0.3)
            return tag

        async def gather3():
            return await asyncio.gather(*[sleepy(t) for t in ("a", "b", "c")])

        started = time.perf_counter()
        got = asyncio.run(gather3())
        elapsed = time.perf_counter() - started

        self.assertEqual(got, ["a", "b", "c"])
        self.assertLess(elapsed, 0.9 * 0.3 * 3,
                        "ワーカースレッドへ逃がしていません（逐次実行になっています）")
        self.assertIsNotNone(getattr(sleepy, "sync_fn", None),
                             "ゲート用に元の同期関数（sync_fn）が残っていません")

    def test_all_tools_are_thread_offloaded(self):
        from space_finder_mcp.server import mcp

        tools = mcp._tool_manager._tools
        self.assertTrue(tools)
        for name, tool in tools.items():
            self.assertTrue(tool.is_async, "{} が async ラッパーとして登録されていません".format(name))
            fn = tool.fn
            self.assertTrue(hasattr(fn, "sync_fn"), "{} に sync_fn がありません".format(name))
            # 引数スキーマが元の関数から生成されていること（ラッパー化で欠けない）
            self.assertEqual(str(inspect.signature(fn)),
                             str(inspect.signature(fn.sync_fn)),
                             "{} のシグネチャが変わりました".format(name))

    def test_matplotlib_rendering_is_serialized(self):
        """matplotlib はプロセス全体の状態を持つため、描画はロックで直列化されていること。"""
        from space_finder_mcp import img_common
        from pathlib import Path

        self.assertTrue(hasattr(img_common, "RENDER_LOCK"))
        root = Path(img_common.__file__).parent
        for mod in ("sky_overlay.py", "solar_system.py"):
            src = (root / mod).read_text(encoding="utf-8")
            self.assertIn("with RENDER_LOCK", src, "{} に描画ロックがありません".format(mod))


if __name__ == "__main__":
    unittest.main()

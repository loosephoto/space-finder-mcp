"""起動時の事前 import（stdio サーバーの無応答を防ぐ）の回帰テスト。

mcp の stdio サーバーが動き出した後（イベントループ稼働中）に numpy / matplotlib /
skyfield を import すると、この環境では import が完了せずツール呼び出しが無応答になる
（実測: Windows + Python 3.11。PIL.Image・sgp4・requests は問題なし）。
server.py は numpy を起動時に import して回避しているので、それを機械的に守る。
実際の応答は `scripts/check-tools.py --stdio` が確認する。
"""
import sys
import unittest

sys.path.insert(0, "src")


class StartupImportTests(unittest.TestCase):
    def test_numpy_is_imported_by_server_module(self):
        import space_finder_mcp.server  # noqa: F401

        self.assertIn("numpy", sys.modules,
                      "server.py が numpy を事前 import していません"
                      "（stdio でツール呼び出しが無応答になります）")

    def test_preimport_block_precedes_fastmcp_run(self):
        """事前 import が `mcp.run()` より前（＝モジュール読み込み時）にあること。"""
        from pathlib import Path
        import space_finder_mcp.server as srv

        src = Path(srv.__file__).read_text(encoding="utf-8")
        self.assertIn("import numpy", src)
        self.assertLess(src.index("import numpy"), src.index("def main") if "def main" in src else len(src))
        # 遅延 import を新たに足すときは、ここではなく起動時の事前 import に加えること
        for name in ("space_finder_mcp.server",):
            self.assertIn(name, sys.modules)


if __name__ == "__main__":
    unittest.main()

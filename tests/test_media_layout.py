"""メディアのリンク行の「見た目の統一」と「改行（融合しない）」の回帰テスト。

実バグ: 画像を複数返すと、content の単一改行が Markdown のソフト改行として
同一段落に畳み込まれ、リンクどうしや直前の caption が 1 行に融合して見えた
（1枚だけのときは段落が分かれるため気付きにくい）。
"""
import asyncio
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, "src")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_check_tools():
    path = os.path.join(ROOT, "scripts", "check-tools.py")
    spec = importlib.util.spec_from_file_location("check_tools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # __main__ ガードがあるので main() は走らない
    return mod


class MediaLinkLineTests(unittest.TestCase):
    def test_label_is_built_from_kind(self):
        from space_finder_mcp import img_common as ic

        self.assertEqual(ic.media_link_line("ハッブル", url="https://e/a.jpg"),
                         "🖼️ [画像を開く: ハッブル](https://e/a.jpg)")
        self.assertTrue(ic.media_link_line("月齢マップ", path="C:/x/y.png", kind="figure")
                        .startswith("🖼️ [生成した画像を開く: 月齢マップ](file:///"))
        self.assertIn("🎧 [音声を開く: X]", ic.media_link_line("X", url="https://e/c.mp3", kind="audio"))
        self.assertIn("🎬 [動画を開く: X]", ic.media_link_line("X", url="https://e/b.mp4", kind="video"))
        self.assertIn("📄 [ファイルを開く: X]", ic.media_link_line("X", url="https://e/d.fits", kind="file"))
        self.assertEqual(ic.media_link_line("X"), "")            # リンク先なし＝空文字

    def test_legacy_label_does_not_double_the_verb(self):
        from space_finder_mcp import img_common as ic

        line = ic.media_link_line("生成した画像を開く（月齢マップ 2026-09）",
                                  path="C:/x/y.png", kind="figure")
        self.assertEqual(line.count("生成した画像を開く"), 1)
        self.assertIn("[生成した画像を開く: 月齢マップ 2026-09]", line)

    def test_generated_label_is_canonical(self):
        from space_finder_mcp import img_common as ic

        for kind, url, path in (("image", "https://e/a.jpg", None),
                                ("figure", None, "C:/x/y.png"),
                                ("audio", "https://e/a.mp3", None),
                                ("video", "https://e/a.mp4", None),
                                ("file", "https://e/a.fits", None)):
            line = ic.media_link_line("対象", url=url, path=path, kind=kind)
            self.assertTrue(ic.is_media_link_line(line), line)
            self.assertTrue(ic.canonical_media_label(ic.media_link_label(line)), line)

    def test_non_canonical_label_is_rejected(self):
        from space_finder_mcp import img_common as ic

        line = "🖼️ [サムネイル画像を開く: X](https://e/a.jpg)"
        self.assertTrue(ic.is_media_link_line(line))
        self.assertFalse(ic.canonical_media_label(ic.media_link_label(line)))


class LayoutTests(unittest.TestCase):
    def test_links_are_separated_by_blank_lines(self):
        from space_finder_mcp import img_common as ic

        raw = "\n".join([
            "1. **A** (2020)",
            "   🖼️ [画像を開く: A](https://e/a.jpg)",
            "2. **B** (2021)",
            "   🖼️ [画像を開く: B](https://e/b.jpg)",
        ])
        laid = ic.layout_media_links(raw)
        lines = laid.split("\n")
        for i, line in enumerate(lines):
            if not ic.is_media_link_line(line):
                continue
            prev = lines[i - 1] if i > 0 else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            self.assertEqual((prev.strip(), nxt.strip()), ("", ""), laid)
        self.assertIn("   🖼️ [画像を開く: A]", laid)      # リスト内の字下げは保つ

    def test_layout_is_idempotent_and_keeps_text(self):
        from space_finder_mcp import img_common as ic

        raw = "caption\n🖼️ [画像を開く: A](https://e/a.jpg)\ntail"
        once = ic.layout_media_links(raw)
        self.assertEqual(ic.layout_media_links(once), once)
        self.assertIn("caption", once)
        self.assertIn("tail", once)

    def test_text_without_links_is_untouched(self):
        from space_finder_mcp import img_common as ic

        raw = "ただの本文\n行が続く"
        self.assertEqual(ic.layout_media_links(raw), raw)


class DeliveryTests(unittest.TestCase):
    """全ツールの出口（server._threaded）で整形されて配送されること。"""

    def test_threaded_applies_layout(self):
        from mcp.types import CallToolResult, TextContent
        from space_finder_mcp import img_common as ic
        from space_finder_mcp.server import _threaded

        def fake_tool():
            return CallToolResult(
                content=[TextContent(type="text", text="caption\n"
                                     "🖼️ [画像を開く: A](https://e/a.jpg)\n"
                                     "🖼️ [画像を開く: B](https://e/b.jpg)")],
                structuredContent={"image_url": "https://e/a.jpg"})

        result = asyncio.run(_threaded(fake_tool)())
        text = "\n".join(c.text for c in result.content)
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if not ic.is_media_link_line(line):
                continue
            prev = lines[i - 1] if i > 0 else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            self.assertEqual((prev.strip(), nxt.strip()), ("", ""), text)
        self.assertEqual(result.structuredContent, {"image_url": "https://e/a.jpg"})


class GateDetectionTests(unittest.TestCase):
    def test_gate_flags_non_canonical_links(self):
        ct = _load_check_tools()
        bad = "🖼️ [サムネイル画像を開く: X](https://e/a.jpg)\n"
        issues = ct._media_text_issues(bad)
        self.assertEqual(len(issues), 1)
        self.assertIn("統一", issues[0])
        ok = "🖼️ [画像を開く: X](https://e/a.jpg)\n\n🎧 [音声を開く: Y](https://e/y.mp3)"
        self.assertEqual(ct._media_text_issues(ok), [])

    def test_gate_layout_normalizes_fused_text(self):
        # 融合した生テキストは配信時（server._delivered）に空行を補われるので、
        # ゲートの融合検査は「整形後の形」で行う（整形が壊れたらここで検出できる）。
        ct = _load_check_tools()
        fused = "caption\n🖼️ [画像を開く: A](https://e/a.jpg)\n🖼️ [画像を開く: B](https://e/b.jpg)"
        self.assertEqual(ct._media_text_issues(fused), [])
        from space_finder_mcp import img_common as ic
        laid = ic.layout_media_links(fused)
        self.assertIn("caption\n\n🖼️", laid)
        self.assertIn("](https://e/a.jpg)\n\n🖼️", laid)

    def test_gate_delivery_check_passes(self):
        ct = _load_check_tools()
        self.assertEqual(ct.delivery_issues(), [])


if __name__ == "__main__":
    unittest.main()

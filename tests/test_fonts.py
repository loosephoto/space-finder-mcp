"""画像内の日本語フォント探索（Windows / macOS / Linux 共通）の回帰テスト。

旧実装は Windows のパス（C:/Windows/Fonts/...）だけを見ていたため、macOS / Linux では
PIL 既定フォントに落ちて画像内の日本語が全部豆腐（□）になった。ここでは

- 候補に macOS（ヒラギノ）と Linux（Noto CJK / IPA / VL / Takao）が入っていること
- 解決したフォントが実際に日本語グリフを持つこと（cmap を直接確認）
- 日本語を持たないフォントより日本語を持つフォントを優先すること
- 環境変数 SPACE_FINDER_FONT が最優先されること
- 標準フォントディレクトリの走査（環境差の吸収）が機能すること
- matplotlib 経路も日本語フォントを掴むこと

を機械的に守る。OS 側に日本語フォントが1つも無い環境では該当テストを skip する。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, "src")

from space_finder_mcp import img_common as ic  # noqa: E402

CJK_PROBE = ("日", "曜", "語")


def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def _find_non_japanese_font():
    """日本語グリフを持たないフォント（比較用）。無ければ None。"""
    candidates = []
    for d in ic._font_search_dirs():
        if not os.path.isdir(d):
            continue
        for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
            candidates.append(os.path.join(d, name))
    candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for p in candidates:
        if os.path.isfile(p) and ic._cmap_has_glyphs(p, CJK_PROBE) is False:
            return p
    return None


class FontResolutionTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SPACE_FINDER_FONT", None)
        os.environ.pop("SPACE_FINDER_FONT_BOLD", None)
        self._cands = ic._FONT_CANDIDATES
        self._dirs = ic._font_search_dirs
        self._clear()

    def tearDown(self):
        ic._FONT_CANDIDATES = self._cands
        ic._font_search_dirs = self._dirs
        os.environ.pop("SPACE_FINDER_FONT", None)
        os.environ.pop("SPACE_FINDER_FONT_BOLD", None)
        self._clear()

    def _clear(self):
        ic._resolve_font.cache_clear()
        ic._scan_font_dirs.cache_clear()
        ic.load_font.cache_clear()

    # ---- 候補の網羅性（Windows 固定だった旧実装の再発防止） ----
    def test_candidates_cover_windows_macos_and_linux(self):
        for bold in (False, True):
            paths = list(ic._FONT_CANDIDATES[bold])
            win = [p for p in paths if "fonts" in p.lower() and not p.startswith("/")]
            mac = [p for p in paths if p.startswith("/System/Library/Fonts")
                   or p.startswith("/Library/Fonts")]
            linux = [p for p in paths if p.startswith("/usr/share/fonts")]
            self.assertTrue(win, "Windows の候補がありません（bold={}）".format(bold))
            self.assertTrue(mac, "macOS（ヒラギノ等）の候補がありません（bold={}）".format(bold))
            self.assertTrue(linux, "Linux（Noto CJK / IPA 等）の候補がありません（bold={}）".format(bold))

    def test_macos_candidates_are_hiragino_family(self):
        paths = list(ic._FONT_CANDIDATES[False]) + list(ic._FONT_CANDIDATES[True])
        self.assertTrue(any("ヒラギノ角ゴシック" in p for p in paths))

    # ---- cmap 判定そのものの妥当性 ----
    def test_cmap_detection(self):
        self.assertFalse(ic._cmap_has_glyphs("", CJK_PROBE))
        self.assertIsNone(ic._cmap_has_glyphs(os.path.join(tempfile.gettempdir(),
                                                           "no-such-font.ttf"), CJK_PROBE))
        win_font = _first_existing(ic._FONT_CANDIDATES[False] + ic._FONT_CANDIDATES[True])
        if win_font:
            self.assertIs(ic._cmap_has_glyphs(win_font, CJK_PROBE), True,
                          "解決候補のフォントに日本語グリフがありません: " + win_font)
        no_jp = _find_non_japanese_font()
        if no_jp:
            self.assertIs(ic._cmap_has_glyphs(no_jp, CJK_PROBE), False,
                          "日本語なしフォントを日本語ありと誤判定: " + no_jp)

    # ---- 解決結果 ----
    def test_resolved_font_has_japanese_glyphs(self):
        status = ic.font_status()
        for key in ("regular", "bold"):
            info = status[key]
            if not info["path"]:
                self.skipTest("この環境に日本語フォントがありません")
            self.assertTrue(info["japanese_glyphs"],
                            "{} の日本語フォントが豆腐になります: {}".format(key, info))

    def test_load_font_returns_truetype_font_with_japanese(self):
        path, _src = ic._resolve_font(False)
        if not path or not ic._cmap_has_glyphs(path, CJK_PROBE):
            self.skipTest("この環境に日本語フォントがありません")
        font = ic.load_font(20)
        self.assertEqual(os.path.normcase(getattr(font, "path", "")), os.path.normcase(path))

    def test_prefers_japanese_font_over_non_japanese(self):
        jp = _first_existing(ic._FONT_CANDIDATES[False] + ic._FONT_CANDIDATES[True])
        no_jp = _find_non_japanese_font()
        if not jp or not no_jp:
            self.skipTest("比較用のフォントが揃っていません")
        ic._FONT_CANDIDATES = {False: (no_jp, jp), True: (no_jp, jp)}
        ic._font_search_dirs = lambda: []
        ic._resolve_font.cache_clear()
        ic._scan_font_dirs.cache_clear()
        path, src = ic._resolve_font(False)
        self.assertEqual(os.path.normcase(path), os.path.normcase(jp))
        self.assertEqual(src, "platform")

    def test_env_var_wins(self):
        jp = _first_existing(ic._FONT_CANDIDATES[False])
        if not jp:
            self.skipTest("この環境に日本語フォントがありません")
        os.environ["SPACE_FINDER_FONT"] = jp
        ic._resolve_font.cache_clear()
        path, src = ic._resolve_font(False)
        self.assertEqual(src, "env")
        self.assertEqual(os.path.normcase(path), os.path.normcase(os.path.abspath(jp)))

    def test_scan_finds_font_by_filename(self):
        """既知パスに無くても、標準フォントディレクトリの走査で見つけられること。"""
        jp = _first_existing(ic._FONT_CANDIDATES[False] + ic._FONT_CANDIDATES[True])
        if not jp:
            self.skipTest("コピー元になる日本語フォントがありません")
        with tempfile.TemporaryDirectory() as d:
            dst = os.path.join(d, "NotoSansCJK-Regular.ttc")
            shutil.copyfile(jp, dst)
            ic._FONT_CANDIDATES = {False: (), True: ()}
            ic._font_search_dirs = lambda: [d]
            ic._resolve_font.cache_clear()
            ic._scan_font_dirs.cache_clear()
            path, src = ic._resolve_font(False)
            self.assertEqual(src, "scan", "標準フォントディレクトリの走査が機能していません")
            self.assertEqual(os.path.normcase(path), os.path.normcase(dst))
            self.assertTrue(ic._cmap_has_glyphs(path, CJK_PROBE))

    def test_scan_is_empty_when_dirs_missing(self):
        ic._font_search_dirs = lambda: [os.path.join(tempfile.gettempdir(), "no-such-fontdir")]
        ic._scan_font_dirs.cache_clear()
        self.assertEqual(ic._scan_font_dirs(False), ())


class MatplotlibFontTests(unittest.TestCase):
    def test_matplotlib_gets_japanese_font(self):
        path, _src = ic._resolve_font(False)
        if not path or not ic._cmap_has_glyphs(path, CJK_PROBE):
            self.skipTest("この環境に日本語フォントがありません")
        family = ic.apply_matplotlib_cjk_font()
        self.assertIsNotNone(family)
        from matplotlib import font_manager, rcParams
        self.assertEqual(list(rcParams["font.family"]), ["sans-serif"])
        self.assertFalse(rcParams["axes.unicode_minus"])
        fp = font_manager.FontProperties(family=rcParams["font.sans-serif"])
        found = font_manager.findfont(fp, fallback_to_default=False)
        self.assertIs(ic._cmap_has_glyphs(found, CJK_PROBE), True,
                      "matplotlib が日本語フォントを掴めていません: " + str(found))


if __name__ == "__main__":
    unittest.main()

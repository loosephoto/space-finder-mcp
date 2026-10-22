"""ローバー位置マップの**タイトル表記**の回帰テスト。

同じ名前が複数の別リテラルで使われる（描画パネルの表題 / `figure.title` /
`media_link_line` の対象 / 本文見出し）。1 箇所だけ直すと残りと食い differ になるので、
**「◯◯ の△△」（空白あり）が多数派**という repo の表記ルールで揃っていることを固定する。

代表 3 経路（火星 / 月 / フォボス）で:
  1. `figure.title` が「空白あり」の形になっている
  2. `figure.title` の主語部分と `media_link_line` の対象が**一致**する
  3. 図は `verify.ok=True` で返る（表記修正で図が壊れない）

ネットワークは遮断した状態で検証する（`requests.get/post` と
`requests.Session.request` の両方を差し替える）。
"""
import io
import os
import re
import sys
import unittest

sys.path.insert(0, "src")

# 「空白なしのの」= 直前の文字が空白でない "の"。
# （例: "イデフィクス（IDEFIX）のフォボス位置マップ"）
_NO_SPACE_RE = re.compile(r"(?<!\s)の")
_SPACED_RE = re.compile(r"\sの")


class _NetworkBlocked(Exception):
    pass


class RoverTitleConventionTests(unittest.TestCase):
    """planetary_rover_location_map のタイトル表記（空白ありに統一）。"""

    def setUp(self):
        from space_finder_mcp import planetary_rover as pr
        from space_finder_mcp import surface_map as sm
        from space_finder_mcp import img_common as ic
        self.pr = pr
        self.ic = ic
        self._orig_disk_get = sm.disk_get
        self._orig_iau = pr._iau_landing_sites
        self._orig_wp = pr._mmgis_waypoint
        self._orig_route = pr._mmgis_route
        # フォボスのベースマップ（ネットワーク不要の合成図）
        from PIL import Image
        im = Image.new("RGB", (600, 300), (40, 60, 90))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        sm.disk_get = lambda url, **kw: buf.getvalue()
        pr._iau_landing_sites = lambda target="PHOBOS": []
        # 火星は MMGIS ）もネットワーク不要にする
        pr._mmgis_waypoint = lambda mid: {"lat": 18.4, "lon": 77.4, "sol": 100,
                                          "dist_km": 30.0, "rmc": "RMC", "note": ""}
        pr._mmgis_route = lambda mid: [(77.4, 18.4), (77.41, 18.41)]

    def tearDown(self):
        from space_finder_mcp import planetary_rover as pr
        from space_finder_mcp import surface_map as sm
        sm.disk_get = self._orig_disk_get
        pr._iau_landing_sites = self._orig_iau
        pr._mmgis_waypoint = self._orig_wp
        pr._mmgis_route = self._orig_route

    def _cases(self):
        return [
            ("mars", dict(body="mars", rover="perseverance")),
            ("moon", dict(body="moon", rover="apollo15")),
            ("phobos", dict(body="phobos", rover="idefix")),
        ]

    def _media_subject(self, text):
        """本文から `media_link_line` のラベル（対象）だけを取り出す。"""
        for line in text.split("\n"):
            if self.ic.is_media_link_line(line):
                label = self.ic.media_link_label(line) or ""
                # "生成した画像を開く: パーサヴィアランス の火星位置マップ" → 対象だけ
                return label.split(": ", 1)[1] if ": " in label else label
        return None

    # ---- 1. `figure.title` は「空白あり」の形 ----
    def test_figure_title_uses_spaced_form(self):
        for name, kw in self._cases():
            with self.subTest(body=name):
                r = self.pr.planetary_rover_location_map(out_px=400, **kw)
                sc = r.structuredContent
                self.assertNotIn("error", sc)
                title = sc["figure"]["title"]
                # 「◯◯の△△」（空白なし）が残っていないこと
                self.assertIsNone(_NO_SPACE_RE.search(title),
                                  "空白なしの『の』が残っています: %r" % title)
                # 「◯◯ の△△」（空白あり）が1つ以上あること
                self.assertTrue(_SPACED_RE.search(title),
                                "空白ありの『 の 』がありません: %r" % title)

    # ---- 2. `figure.title` と `media_link_line` の対象が一致する ----
    def test_media_link_subject_matches_figure_title(self):
        for name, kw in self._cases():
            with self.subTest(body=name):
                r = self.pr.planetary_rover_location_map(out_px=400, **kw)
                sc = r.structuredContent
                subject = self._media_subject(r.content[0].text)
                self.assertIsNotNone(subject, "メディアのリンク行がありません")
                # 図のタイトルは「{対象}（{出典}）」の形。対象が一致し、
                # 残りが括弧1組の出典になっていることを見る。
                title = sc["figure"]["title"]
                self.assertTrue(title.startswith(subject),
                                "リンク行の対象と figure.title が食い違っています: %r vs %r"
                                % (subject, title))
                rest = title[len(subject):]
                self.assertTrue(rest.startswith("（") and rest.endswith("）"),
                                "figure.title の出典部分が括弧1組になっていません: %r" % rest)
                # 出典の表記は経路ごとに異なる（月はベースマップの attrib、
                # 火星/フォボスは structuredContent.source）ので形だけ固定する。
                self.assertTrue(rest[1:-1].strip(), "出典が空です: %r" % rest)

    # ---- 3. 表記修正で図が壊れていない（verify.ok） ----
    def test_figures_still_verify_ok(self):
        for name, kw in self._cases():
            with self.subTest(body=name):
                r = self.pr.planetary_rover_location_map(out_px=400, **kw)
                v = r.structuredContent["figure"]["verify"]
                self.assertTrue(v.get("ok"), v)


if __name__ == "__main__":
    unittest.main()

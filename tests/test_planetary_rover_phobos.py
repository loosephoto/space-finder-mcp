"""フォボス（MMX / IDEFIX）用のローバー位置マップの回帰テスト。

実測にもとづく前提（すべてこのリポジトリで実測して確定した）:

1. **USGS のフォボス全球等角図の向き** — 配布図の印字目盛は「西向き正の大きさ」で
   並ぶ（左端 180 → 0 → 340）が、地図自体は**標準の東向き正**（経度が右へ増加）。
   IAU 地名 20 点の実測で確定: Stickney（49°W = 311°E）は図の左 36.39% にあり、
   IAU Feature 5707 / Wikipedia「1°N 49°W」と一致する。よって `left_edge_lon=-180`
   （横回転なし）でよい。**この向きを取り違えると全マーカーが左右反転する**。

2. **配布 PNG は余白がある** — 5499x2999 px のうち地形ラスタは cols 249..5250 /
   rows 248..2750 だけ（残りは白地・目盛ラベル・題字・クレジット）。
   `image_view(crop=…)` で相対範囲を指定して切り出す（解像度非依存）。

3. **フォボスの経度は IAU では西向き正（+West 0-360）** — 内部値は東向き正で保持し、
   `site_lon_w` で西向き正を受け取る（両方の指定は排他）。

4. **0〜360°E の公表値をそのまま投影すると画像外に出る** — 投影は -180〜180° が
   前提なので、必ず折り返してから使う（実測: 311°E は画像の外）。

5. **全球図の画素検証の許容は 0.01° では足りない** — 全球図は 1px≒0.36° なので
   許容は画素分解能から決める（planetary_map と同じ考え方）。

ネットワークは全部遮断した状態で検証する（`requests.get/post` と
`requests.Session.request` の両方を差し替える）。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, "src")


def _make_phobos_like_map(w=1200, h=600):
    """USGS フォボス全球図に似た 2:1 のテスト用ベースマップを作る。

    画素の R 値に「その画素の東経（0〜360°E）」を符号化し、G 値に緯度を
    符号化してある（0.5 を足して 0 を避ける）。これで「指定座標の点が
    正しい経度・緯度の画素に落ちるか」を機械的に検査できる。
    """
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for x in range(w):
        lon_e = (x / float(w) * 360.0) - 180.0        # -180..180（東向き正）
        r = int(round((lon_e + 180.0) / 360.0 * 200.0)) + 20
        for y in range(h):
            lat = 90.0 - (y / float(h) * 180.0)
            g = int(round((lat + 90.0) / 180.0 * 200.0)) + 20
            px[x, y] = (r, g, 60)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), w, h


class _NetworkBlocked(Exception):
    pass


class PhobosRoverMapTests(unittest.TestCase):
    """planetary_rover_location_map のフォボス経路（MMX / IDEFIX）。"""

    def setUp(self):
        from space_finder_mcp import planetary_rover as pr
        from space_finder_mcp import surface_map as sm
        self.pr = pr
        self.sm = sm
        self.data, self.w, self.h = _make_phobos_like_map()
        self._orig_disk_get = sm.disk_get
        self._orig_iau = pr._iau_landing_sites
        sm.disk_get = lambda url, **kw: self.data        # ベースマップはネットワーク断
        pr._iau_landing_sites = lambda target="PHOBOS": []  # 着陸地点は未公表

    def tearDown(self):
        self.sm.disk_get = self._orig_disk_get
        self.pr._iau_landing_sites = self._orig_iau

    # ---- 1. 未公表のときは「制約帯だけの図」を返す（エラーにしない）----
    def test_phobos_without_published_site_renders_constraint_band(self):
        r = self.pr.planetary_rover_location_map(body="phobos", rover="idefix", out_px=600)
        sc = r.structuredContent
        self.assertNotIn("error", sc)
        self.assertEqual(sc["landing_site_status"], "not_published")
        self.assertTrue(sc["constraint_band_drawn"])
        self.assertTrue(sc["figure"]["verify"]["ok"])
        self.assertTrue(sc["figure"]["notes"])
        # 「着陸地点が未公表である」旨が本文と注記の両方に出ていること
        joined = "".join(sc["figure"]["notes"])
        self.assertIn("未公表", joined)
        self.assertIn("着陸地点は未公表", r.content[0].text)

    # ---- 2. 注記は「制約帯は着陸地点ではない」と明言する（誤読防止）----
    def test_constraint_band_is_not_presented_as_the_landing_site(self):
        r = self.pr.planetary_rover_location_map(body="phobos", rover="idefix", out_px=600)
        joined = "".join(r.structuredContent["figure"]["notes"])
        self.assertIn("着陸地点そのものではない", joined)

    # ---- 3. 座標を指定すると着陸予定点として描かれる（公表後に使える経路）----
    def test_explicit_site_is_drawn_and_verified(self):
        r = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0, site_lon_w=49.0, out_px=600)
        sc = r.structuredContent
        self.assertNotIn("error", sc)
        self.assertEqual(sc["landing_site_status"], "user_specified")
        self.assertEqual(sc["lat"], 1.0)
        # 内部値は東向き正（-49.0 = 311°E）
        self.assertAlmostEqual(sc["lon"], -49.0, places=6)
        v = sc["figure"]["verify"]
        self.assertTrue(v["ok"], v)
        self.assertGreater(v["marker_pixels"], 0)
        self.assertEqual(v["latlon_from_px"][0], -49.0)
        self.assertEqual(v["latlon_from_px"][1], 1.0)

    # ---- 4. 東向き正と西向き正は排他（混同を防ぐ）----
    def test_east_and_west_longitude_are_mutually_exclusive(self):
        r = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0, site_lon=311.0, site_lon_w=49.0)
        self.assertEqual(r.structuredContent.get("error"), "conflicting longitude")

    # ---- 5. 東向き正と西向き正が同じ場所を指すこと（変換の整合）----
    def test_west_positive_and_east_positive_agree(self):
        a = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0, site_lon_w=49.0, out_px=600)
        b = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0, site_lon=311.0, out_px=600)
        self.assertAlmostEqual(a.structuredContent["lon"], b.structuredContent["lon"], places=6)
        self.assertTrue(a.structuredContent["figure"]["verify"]["ok"])
        self.assertTrue(b.structuredContent["figure"]["verify"]["ok"])

    # ---- 6. 緯度だけ指定はエラー（不完全な座標は黙って無視しない）----
    def test_latitude_without_longitude_is_rejected(self):
        r = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0)
        self.assertEqual(r.structuredContent.get("error"), "incomplete site coordinates")

    # ---- 7. 0〜360°E の公表値でも画像内に落ちる（±180°へ折り返す）----
    def test_longitude_above_180_is_folded_into_view(self):
        r = self.pr.planetary_rover_location_map(
            body="phobos", rover="idefix", site_lat=1.0, site_lon=311.0, out_px=600)
        sc = r.structuredContent
        self.assertTrue(sc["figure"]["verify"]["ok"], sc["figure"]["verify"])
        self.assertGreater(sc["figure"]["verify"]["marker_pixels"], 0)

    # ---- 8. 不正値は例外を漏らさず既定へ落ちる ----
    def test_invalid_coordinates_do_not_raise(self):
        for kw in ({"site_lat": "abc", "site_lon": "xyz"},
                   {"site_lat": None, "site_lon": None},
                   {"site_lat": 999.0, "site_lon": 999.0},
                   {"site_lat": "1.0", "site_lon": "311"}):
            r = self.pr.planetary_rover_location_map(body="phobos", rover="idefix", **kw)
            self.assertIsNotNone(r.structuredContent)

    # ---- 9. 天体/ローバーの不一致は案内して停止する ----
    def test_body_mismatch_is_reported(self):
        r = self.pr.planetary_rover_location_map(body="mars", rover="idefix")
        self.assertEqual(r.structuredContent.get("error"), "body mismatch")
        r2 = self.pr.planetary_rover_location_map(body="pluto", rover="idefix")
        self.assertEqual(r2.structuredContent.get("error"), "unknown body")
        r3 = self.pr.planetary_rover_location_map(body="phobos", rover="nope")
        self.assertEqual(r3.structuredContent.get("error"), "unsupported rover")

    # ---- 10. IAU/Gazetteer に着陸地点が出たら自動で拾う ----
    def test_published_iau_landing_site_is_picked_up(self):
        self.pr._iau_landing_sites = lambda target="PHOBOS": [
            {"name": "MMX Landing Site", "lat": 5.0, "lon": 311.0, "lon_w": 49.0,
             "diameter": 0.2, "type": "Landing Site, landing sites",
             "link": "http://planetarynames.wr.usgs.gov/Feature/1"}]
        r = self.pr.planetary_rover_location_map(body="phobos", rover="idefix", out_px=600)
        sc = r.structuredContent
        self.assertEqual(sc["landing_site_status"], "published")
        self.assertEqual(sc["landing_site_name"], "MMX Landing Site")
        self.assertEqual(sc["lat"], 5.0)
        self.assertTrue(sc["figure"]["verify"]["ok"])
        self.assertTrue(sc["figure"]["markers"])

    # ---- 11. 火星の経路は既存の挙動を保つ（ body's既定が mars）----
    def test_mars_default_path_unchanged(self):
        from space_finder_mcp import planetary_rover as pr
        orig_wp = pr._mmgis_waypoint
        orig_route = pr._mmgis_route
        pr._mmgis_waypoint = lambda mid: {"lat": 18.4, "lon": 77.4, "sol": 100,
                                          "dist_km": 30.0, "rmc": "RMC", "note": ""}
        pr._mmgis_route = lambda mid: [(77.4, 18.4), (77.41, 18.41)]
        try:
            r = pr.planetary_rover_location_map(out_px=400)
            sc = r.structuredContent
            self.assertNotIn("error", sc)
            self.assertEqual(sc["body"], "mars")
            self.assertEqual(sc["rover"], "perseverance")
            self.assertEqual(sc["sol"], 100)
            self.assertEqual(sc["dist_km"], 30.0)
            self.assertTrue(sc["figure"]["verify"]["ok"])
        finally:
            pr._mmgis_waypoint = orig_wp
            pr._mmgis_route = orig_route


class ImageViewCropTests(unittest.TestCase):
    """`image_view(crop=…)` の回帰テスト（配布画像の余白・題字・クレジット除去）。"""

    def setUp(self):
        from space_finder_mcp import surface_map as sm
        self.sm = sm
        # 余白つき（白地の左右 + 題字のある上下）の画像を作る
        from PIL import Image
        w, h = 500, 300
        im = Image.new("RGB", (w, h), (255, 255, 255))
        px = im.load()
        for x in range(50, 450):
            for y in range(60, 240):
                px[x, y] = (10 + x % 200, 20 + y % 200, 30)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        self.data = buf.getvalue()
        self._orig = sm.disk_get
        sm.disk_get = lambda url, **kw: self.data

    def tearDown(self):
        self.sm.disk_get = self._orig

    def test_crop_removes_margins_and_keeps_ar_correction_metadata(self):
        sv = self.sm.image_view("https://example.invalid/x.png", 0.0, 0.0, 360.0, 400,
                                 crop=(50 / 500.0, 60 / 300.0, 450 / 500.0, 240 / 300.0))
        self.assertIsNotNone(sv["crop_px"])
        # 切り出し後は 400x180（AR 2.222…）なので 2:1 補正される
        self.assertTrue(sv["ar_corrected"])
        self.assertAlmostEqual(sv["source_ar"], 400 / 180.0, places=3)
        self.assertGreater(sv["ar_correction_percent"], 0.0)
        # 投影は変わらない（左端 -180、右端 +180）
        self.assertAlmostEqual(sv["g2px"](-180.0, 0.0)[0], 0.0, places=6)
        self.assertAlmostEqual(sv["g2px"](180.0, 0.0)[0], 400.0, places=6)

    def test_crop_is_optional_and_none_is_a_noop(self):
        sv = self.sm.image_view("https://example.invalid/x.png", 0.0, 0.0, 360.0, 400)
        self.assertIsNone(sv["crop_px"])

    def test_invalid_crop_is_ignored(self):
        sv = self.sm.image_view("https://example.invalid/x.png", 0.0, 0.0, 360.0, 400,
                                 crop=(0.9, 0.9, 0.1, 0.1))     # 入れ替えは不正
        self.assertIsNone(sv["crop_px"])


class PhobosLongitudeConventionTests(unittest.TestCase):
    """フォボスの経度規約（西向き正・IAU）の取り違え防止。"""

    def test_iau_longitudes_are_east_positive_zero_to_360(self):
        """IAU/KMZ の center_lon は東向き正 0〜360（Stickney=311 は 49°W）。"""
        from space_finder_mcp import planetary_rover as pr
        self.assertTrue(pr.IAU_LON_EAST_POSITIVE)
        # 311°E == 49°W
        self.assertAlmostEqual((360.0 - 311.0) % 360.0, 49.0, places=9)

    def test_site_lon_w_converts_to_east_positive(self):
        from space_finder_mcp import planetary_rover as pr
        self.assertAlmostEqual((360.0 - 49.0) % 360.0, 311.0, places=9)

    def test_fmt_lon_renders_japanese_direction(self):
        from space_finder_mcp import planetary_rover as pr
        self.assertIn("西経", pr._fmt_lon(-49.0))
        self.assertIn("東経", pr._fmt_lon(49.0))
        self.assertIn("0°", pr._fmt_lon(0.0))


if __name__ == "__main__":
    unittest.main()

"""全球等角図ベースマップの「経度原点（左端の経度）」の回帰テスト。

実測にもとづく前提: USGS/PDS の全球モザイクと Hubble OPAL の全球図は
**画像の左端が東経 0°** で東向きに増える（USGS の .lbl は CenterLongitude=180・
最小経度 0.0、OPAL の readme は "left edge = 0 System III W"）。地点マーカー図の
投影は左端 西経 180° なので、そのまま描くと全地点が 180° ずれる。
`image_view(left_edge_lon=0.0)` が半周回して合わせることを検証する。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, "src")


def _make_lon_encoded_image(w=720, h=360):
    """画素の R 値に「その画素が表す東経（0〜360°E）」を符号化した 2:1 画像を作る。

    前提: 左端が東経 0°、東向きに増える地図（USGS/PDS・OPAL の規約）。
    """
    from PIL import Image
    im = Image.new("RGB", (w, h))
    px = im.load()
    for x in range(w):
        r = int(round((x / float(w) * 360.0) / 360.0 * 255.0))
        for y in range(h):
            px[x, y] = (r, 0, 0)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), w, h


class BasemapLongitudeOriginTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import surface_map as sm
        self.sm = sm
        self.data, self.w, self.h = _make_lon_encoded_image()
        self._orig = sm.disk_get
        sm.disk_get = lambda url, **kw: self.data      # ネットワークへ出さない

    def tearDown(self):
        self.sm.disk_get = self._orig

    def _decoded_lon(self, img, x, y, w):
        """図の画素が表す東経（符号化した R 値を読み戻す。0〜360°E）。"""
        r = img.convert("RGB").getpixel((min(max(int(x), 0), w - 1), int(y)))[0]
        return r / 255.0 * 360.0

    def test_left_edge_zero_is_rotated_half_turn(self):
        """左端 0°E の地図は 180° 回され、queried 経度の画素にその経度が来る。"""
        # 全面図（span=360）と局所図（span=120・中心は経度0°）の両経路を叩く。
        # 局所図は表示窓の外の経度は読めないので、窓に入る経度だけを検証する。
        for span, lons in ((360.0, (20.0, 90.0, 150.0, -90.0, -30.0, 175.0)),
                           (120.0, (20.0, -20.0, 45.0, -45.0))):
            sv = ("https://example.invalid/g.png", 0.0, 0.0, span, 900)
            v = self.sm.image_view(sv[0], sv[1], sv[2], sv[3], sv[4],
                                   left_edge_lon=0.0)
            self.assertAlmostEqual(v["lon_roll_deg"], 180.0, places=6)
            self.assertAlmostEqual(v["left_edge_lon"], 0.0, places=6)
            img = v["img"]
            # 継ぎ目（回転後は経度 0° の位置）の近くは LANCZOS 補間の影響が大きい
            # （実測: 継ぎ目直上では 0° の画素が 25〜120° 相当に化ける）。実データの全球図は
            # 経度 0/360° が連続なので実害は無いが、この合成画像は経度をランプで符号化して
            # いるため継ぎ目で不連続になる。よって継ぎ目から離れた経度だけを見る。
            for lon in lons:
                x, y = v["g2px"](lon, 0.0)
                if not (0 <= x < img.size[0] and 0 <= y < img.size[1]):
                    self.fail("経度 {} が図の外（span={}）".format(lon, span))
                got = self._decoded_lon(img, x, y, img.size[0])
                # 経度は円環なので差は 0〜180° に畳んで比較する
                diff = abs((got - (lon % 360.0) + 180.0) % 360.0 - 180.0)
                # 回転を忘れると 180° ずれる。補間誤差（数°）と区別できる緩さで見る
                self.assertLess(diff, 8.0,
                                "span={} lon={} → 画素の経度 {}（差 {:.2f}°）".format(
                                    span, lon, got, diff))

    def test_default_origin_needs_no_rotation(self):
        """左端 -180°E の地図（既定）は回さない（既存の5天体の挙動を壊さない）。"""
        for kw in ({}, {"left_edge_lon": -180.0}):
            v = self.sm.image_view("https://example.invalid/g.png", 0.0, 0.0, 360.0, 900, **kw)
            self.assertEqual(v["lon_roll_deg"], 0.0)
            x, _ = v["g2px"](0.0, 0.0)
            self.assertAlmostEqual(x, v["img"].size[0] / 2.0, delta=2.0)

    def test_roll_pixels_recorded_for_notes(self):
        """注記に出すための回転量・元画素数が戻り値に残る（実測の数値を注記へ流す）。"""
        v = self.sm.image_view("https://example.invalid/g.png", 0.0, 0.0, 360.0, 900,
                               left_edge_lon=0.0)
        self.assertEqual(v["pixels"], [self.w, self.h])
        self.assertAlmostEqual(v["source_ar"], 2.0, places=3)


class BasemapBodyConfigTests(unittest.TestCase):
    def test_verified_image_basemaps_have_left_edge_lon(self):
        """左端 0°E が確認できた天体だけが image 経路で、原点を自己申告している。"""
        from space_finder_mcp import planetary_map as pm
        for b in ("jupiter", "saturn", "uranus", "neptune", "pluto", "bennu"):
            cfg = pm.MAP_BODIES[b]
            self.assertEqual(cfg["basemap"], "image", b)
            img = cfg["image"]
            self.assertTrue(img["url"].startswith("https://"), b)
            self.assertTrue(img.get("credit"), b)
            self.assertAlmostEqual(img.get("src_ar"), 2.0, places=3)
            self.assertAlmostEqual(img["left_edge_lon"], 0.0, places=6)

    def test_gas_giants_declare_observation_epoch(self):
        """気体惑星の図は「その年の大気」なので、観測年代を注記へ出せること。"""
        from space_finder_mcp import planetary_map as pm
        for b in ("jupiter", "saturn", "uranus", "neptune"):
            self.assertTrue(pm.MAP_BODIES[b]["image"].get("epoch"), b)

    def test_graticule_stays_for_bodies_without_verified_map(self):
        """全球等角図が確認できない天体は座標グリッドのままにする（推測で埋めない）。"""
        from space_finder_mcp import planetary_map as pm
        self.assertEqual(pm.MAP_BODIES["ryugu"]["basemap"], "graticule")

    def test_no_body_left_without_basemap_kind(self):
        """basemap は3種のいずれか。宣言した経度原点は妥当な範囲の数値であること。"""
        from space_finder_mcp import planetary_map as pm
        for b, cfg in pm.MAP_BODIES.items():
            self.assertIn(cfg["basemap"], ("trek", "image", "graticule"), b)
            if cfg["basemap"] == "image":
                le = cfg["image"].get("left_edge_lon")
                if le is not None:
                    self.assertTrue(-180.0 <= float(le) < 180.0, (b, le))

    def test_documented_venus_origin_is_not_rotated(self):
        """金星は USGS の .lbl が左端 -180°E（CenterLongitude=0.0）なので回転しない。"""
        from space_finder_mcp import planetary_map as pm
        self.assertAlmostEqual(
            pm.MAP_BODIES["venus"]["image"]["left_edge_lon"], -180.0, places=6)


if __name__ == "__main__":
    unittest.main()

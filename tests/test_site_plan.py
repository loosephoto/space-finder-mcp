"""月面遺跡（局地サイトプラン）と、人工物着陸地点重ね表示の回帰テスト。

実測に基づく:
- 等角図法の天体面地図は 1px あたり数百 m〜20 km なので、月面の機材
  （LM 0 m・旗 8〜11 m・LRV 81〜165 m・ALSEP 75〜200 m）はどのズームでも
  1 画素に収まらない。だから位置図（等角図法）と局地サイトプラン（m 単位）の
  2 段構えにする。
- 凡例・見出しは図体に重ならない**帯**に置く。重なると機材が「消えて」見える
  （A12 の ALSEP が実際にその状態になった）。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from space_finder_mcp import site_plan as sp  # noqa: E402


class TestLocalGeometry(unittest.TestCase):
    """距離・方位から局地平面座標を求める計算。"""

    def test_azimuth_to_xy(self):
        # 北が 0・時計回り
        e, n = sp.azimuth_to_xy(100, 0)
        self.assertAlmostEqual(e, 0.0, places=6)
        self.assertAlmostEqual(n, 100.0, places=6)
        e, n = sp.azimuth_to_xy(100, 90)
        self.assertAlmostEqual(e, 100.0, places=6)
        self.assertAlmostEqual(n, 0.0, places=6)
        e, n = sp.azimuth_to_xy(100, 180)
        self.assertAlmostEqual(e, 0.0, places=6)
        self.assertAlmostEqual(n, -100.0, places=6)

    def test_local_offset_m(self):
        # 1 度 の東西は月では約 30.3 km
        e, n = sp.local_offset_m(0.0, 1.0, 0.0, 0.0, sp.MOON_RADIUS_M)
        self.assertAlmostEqual(e, 30323.0, delta=60.0)
        self.assertAlmostEqual(n, 0.0, places=6)
        # 北 1 度 = 約 30.3 km
        e, n = sp.local_offset_m(1.0, 0.0, 0.0, 0.0, sp.MOON_RADIUS_M)
        self.assertAlmostEqual(n, 30323.0, delta=60.0)
        self.assertAlmostEqual(e, 0.0, places=6)

    def test_fmt_m(self):
        self.assertEqual(sp.fmt_m(11), "11 m")
        self.assertEqual(sp.fmt_m(318.62), "319 m")
        self.assertEqual(sp.fmt_m(10500), "10.50 km")

    def test_nice_scale(self):
        # 1/2/5 x 10^n の「きれいな」値（内部は span/4 で決める）
        self.assertEqual(sp._nice_scale_m(50.0), 20.0)
        self.assertEqual(sp._nice_scale_m(200.0), 50.0)
        self.assertEqual(sp._nice_scale_m(1000.0), 500.0)
        # 必ず 1/2/5 x 10^n になる
        for span in (7.3, 50.0, 130.0, 400.0, 2600.0):
            v = sp._nice_scale_m(span)
            exp = math.floor(math.log10(v))
            mant = round(v / (10 ** exp), 6)
            self.assertIn(mant, (1.0, 2.0, 5.0), msg="{} -> {}".format(span, v))


class TestSiteData(unittest.TestCase):
    """一次情報データの整合性。"""

    def test_apollo_sites_present(self):
        for k in ("apollo11", "apollo12", "apollo14", "apollo15", "apollo16", "apollo17"):
            self.assertIn(k, sp.APOLLO_SITES)
            s = sp.APOLLO_SITES[k]
            self.assertIn("lat", s)
            self.assertIn("lon", s)
            self.assertTrue(s["artifacts"])

    def test_lm_is_always_present(self):
        for k, s in sp.APOLLO_SITES.items():
            kinds = [a.get("kind") for a in s["artifacts"]]
            self.assertIn("lm", kinds, msg=k)

    def test_flag_without_azimuth_is_not_drawn(self):
        """方位の公開値がない旗は**図には描かない**（推測で埋めない）。"""
        for k, s in sp.APOLLO_SITES.items():
            for a in s["artifacts"]:
                if a.get("kind") == "flag" and a.get("az_deg") is None:
                    self.assertEqual(a["position_status"], "relation", msg=k)
                    res = sp.site_artifacts_with_xy(s, sp.MOON_RADIUS_M)
                    got = [x for x in res if x["id"] == a["id"]][0]
                    self.assertTrue(got.get("no_position"), msg=k)

    def test_apollo15_flag_is_published(self):
        """A15 の旗は距離 11 m・方位 30 度が公開値。"""
        arts = {a["id"]: a for a in
                sp.site_artifacts_with_xy(sp.APOLLO_SITES["apollo15"], sp.MOON_RADIUS_M)}
        f = arts["flag"]
        self.assertFalse(f.get("no_position"))
        self.assertAlmostEqual(f["east_m"], 11.0 * 0.5, delta=0.01)   # 30度 -> sin=0.5
        self.assertAlmostEqual(f["north_m"], 11.0 * 0.866, delta=0.01)

    def test_apollo15_lrv_is_published(self):
        """A15 の LRV 最終駐車地点は 165 m・方位 102 度。"""
        arts = {a["id"]: a for a in
                sp.site_artifacts_with_xy(sp.APOLLO_SITES["apollo15"], sp.MOON_RADIUS_M)}
        r = arts["lrv"]
        self.assertFalse(r.get("no_position"))
        self.assertAlmostEqual(r["east_m"], 165.0 * 0.9781, delta=0.5)
        self.assertAlmostEqual(r["north_m"], -165.0 * 0.2079, delta=0.5)

    def test_apollo11_flag_not_drawn(self):
        arts = sp.site_artifacts_with_xy(sp.APOLLO_SITES["apollo11"], sp.MOON_RADIUS_M)
        f = [x for x in arts if x["id"] == "flag"][0]
        self.assertTrue(f.get("no_position"))
        self.assertEqual(f["position_status"], "relation")

    def test_lunar_rover_entries(self):
        e = sp.lunar_rover_entries()
        for k in ("apollo11", "apollo15", "lunokhod1", "lunokhod2", "yutu", "yutu2"):
            self.assertIn(k, e)
        self.assertEqual(e["apollo15"]["rover"], "LRV")
        self.assertIsNone(e["apollo11"]["rover"])

    def test_chang_e_coordinates(self):
        """嫦娥4号（玉兔2号）は月裏側。経度は 177.6°E。"""
        e = sp.lunar_rover_entries()
        self.assertAlmostEqual(e["yutu2"]["lat"], 45.4446, places=4)
        self.assertAlmostEqual(e["yutu2"]["lon"], 177.5991, places=4)


class TestSitePlanRender(unittest.TestCase):
    """局地サイトプランの描画。"""

    def _render(self, key, out_px=600):
        return sp.render_site_plan(sp.APOLLO_SITES[key], out_px=out_px,
                                   title="t", subtitle="s")

    def test_render_returns_geometry(self):
        r = self._render("apollo15")
        self.assertEqual(r["img"].size, (600, 600))
        self.assertGreater(r["px_per_m"], 0)
        self.assertGreater(r["scale_bar_m"], 0)

    def test_legend_does_not_cover_the_map_body(self):
        """凡例・見出しは図体と重ならない帯に置く（機材が隠れるので）。"""
        for key in ("apollo11", "apollo12", "apollo14", "apollo15", "apollo16", "apollo17"):
            r = self._render(key)
            lx0, ly0, lx1, ly1 = r["legend_box"]
            bx0, by0, bx1, by1 = r["body_box"]
            self.assertLessEqual(ly1, by0 + 1,
                                 msg="{}: 凡例帯が図体に重なっている".format(key))
            self.assertGreaterEqual(by0, ly1 - 1, msg=key)

    def test_markers_inside_body(self):
        """描く機材のすべてが図体領域 안에収まる。"""
        for key in ("apollo11", "apollo12", "apollo14", "apollo15", "apollo16", "apollo17"):
            r = self._render(key)
            bx0, by0, bx1, by1 = r["body_box"]
            for a in r["drawn"]:
                # 局地座標から中心を基準に計算した位置が図体内か
                cx = 600 / 2.0
                cy = (by0 + by1) / 2.0
                px = cx + a["east_m"] * r["px_per_m"]
                py = cy - a["north_m"] * r["px_per_m"]
                self.assertGreaterEqual(px, bx0 - 1, msg="{} {}".format(key, a["id"]))
                self.assertLessEqual(px, bx1 + 1, msg="{} {}".format(key, a["id"]))
                self.assertGreaterEqual(py, by0 - 1, msg="{} {}".format(key, a["id"]))
                self.assertLessEqual(py, by1 + 1, msg="{} {}".format(key, a["id"]))

    def test_apollo15_has_all_five_artifacts(self):
        r = self._render("apollo15")
        ids = {a["id"] for a in r["drawn"]}
        self.assertEqual(ids, {"lm", "flag", "alsep", "lrrr", "lrv"})

    def test_apollo11_flag_in_not_drawn(self):
        r = self._render("apollo11")
        self.assertIn("flag", {a["id"] for a in r["not_drawn"]})
        self.assertNotIn("flag", {a["id"] for a in r["drawn"]})

    def test_pixel_probe_markers_visible(self):
        """実描画でutan(FLAG/ALSEP/LM) のマーカーが実際に画素として出ていること。"""
        import numpy as np
        r = self._render("apollo15", out_px=700)
        arr = np.asarray(r["img"].convert("RGB")).astype(int)
        bx0, by0, bx1, by1 = r["body_box"]
        body = arr[by0:by1, :, :]
        for kind in ("lm", "rover", "alsep", "lrrr", "flag"):
            m = (np.abs(body - np.array(sp.KIND_COLORS[kind])).max(axis=2) < 40)
            self.assertGreater(int(m.sum()), 20,
                               msg="{} のマーカーが図体に見えない".format(kind))


if __name__ == "__main__":
    unittest.main()

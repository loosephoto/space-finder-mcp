"""地上視点の星空パノラマ（`sky_map_with_satellites(engine="ground")`）の回帰テスト。

実バグ／仕様として固定したいもの:

- **太陽高度の補間が深い夜で飛ばない**（旧実装は「限界等級 99」の番兵値のため
  -19° で全星が出て、-20° で戻る不連続があった。表の値を実値にして
  `min(光害モード, 空の明るさ)` で合わせる）
- **月の欠けの向きは太陽の方向**（旧実装は「太陽が方位で右か左か」の2値だけで、
  高度差を無視していた）。明るい側が太陽側を向くことを画素の重心で確かめる
- **月が新月前後でも位置は示す**（旧実装は照度2%未満で何も描かなかった）
- **昼は星を描かない**（限界等級が負になる）
- **天の川は夜だけ**（昼は面輝度 0 で、位置は破線で示す）
- **銀経・銀緯の変換**（銀河面の向き。符号を間違えると帯が別の場所に出る）
- **星の色**（B-V が大きい＝赤い星は R>B、小さい＝青い星は B>R）
- **依頼表示（show）の一致**（"M31" で "M31 アンドロメダ銀河" に当たる）
- **ツールの出力契約**（メディアのリンク先行・image_path の実在・figure/1 の注記と検証）
"""
import io
import math
import os
import sys
import unittest

sys.path.insert(0, "src")

import numpy as np  # noqa: E402

from space_finder_mcp import ground_view as gv  # noqa: E402


class SkyStateTests(unittest.TestCase):
    def test_day_limits_hide_all_stars(self):
        for alt in (10.0, 40.0):
            self.assertLess(gv._sky_state(alt)["lim_z"], -4.0,
                            "昼の限界等級が負でない（星が描かれてしまう）")

    def test_deep_night_is_continuous(self):
        """-19° で限界等級が跳ねない（番兵値をやめた回帰）。"""
        vals = [gv._sky_state(a)["lim_z"] for a in (-18.0, -19.0, -20.0, -25.0, -30.0)]
        self.assertLess(max(vals), 10.0, "深い夜で限界等級が飛んでいる: {}".format(vals))
        self.assertLessEqual(max(vals) - min(vals), 2.5, "夜の限界等級が不連続: {}".format(vals))

    def test_milky_way_only_at_night(self):
        self.assertEqual(gv._sky_state(20.0)["mw"], 0.0)
        self.assertGreater(gv._sky_state(-30.0)["mw"], 0.9)

    def test_twilight_dims_stars_before_night(self):
        self.assertLess(gv._sky_state(-4.0)["lim_z"], gv._sky_state(-18.0)["lim_z"])


class PhaseNameTests(unittest.TestCase):
    def test_phase_names(self):
        self.assertEqual(gv.phase_name(40.0, False), "昼")
        self.assertEqual(gv.phase_name(1.0, False), "夕方")
        self.assertEqual(gv.phase_name(1.0, True), "朝焼け")
        self.assertIn("市民", gv.phase_name(-3.0, True))
        self.assertIn("明け", gv.phase_name(-3.0, True))
        self.assertIn("暮れ", gv.phase_name(-3.0, False))
        self.assertEqual(gv.phase_name(-40.0, False), "夜")


class MoonShadeTests(unittest.TestCase):
    def _centroid(self, mask):
        ys, xs = np.mgrid[0:mask.shape[0], 0:mask.shape[1]]
        c = mask.sum()
        return float((xs * mask).sum() / c), float((ys * mask).sum() / c)

    def test_full_moon_is_fully_lit(self):
        """満月は円盤全体が光る。Lambert 球なので平均は 2/3（周縁が暗い）。"""
        m = gv._moon_shade(41, 1.0, 0.0)
        self.assertGreater(float(m.max()), 0.99)
        self.assertAlmostEqual(float(m[m > 0].mean()), 2.0 / 3.0, delta=0.03)

    def test_new_moon_is_dark(self):
        self.assertLess(float(gv._moon_shade(41, 0.0, 0.0).max()), 0.05)

    def test_lit_fraction_grows_with_illumination(self):
        """位相角から欠けの形が決まる（境界は Lambert 陰影なので面積比は単調増加）。"""
        lit = [float((gv._moon_shade(81, f, 0.0) > 0.5).sum())
               / float((gv._moon_shade(81, f, 0.0) > 0.0).sum()) for f in (0.25, 0.5, 0.75)]
        self.assertLess(lit[0], lit[1])
        self.assertLess(lit[1], lit[2])
        self.assertLess(abs(lit[1] - 0.5), 0.12, "半月（照度0.5）の明るい面積が半分から離れている")

    def test_bright_side_faces_the_sun(self):
        """太陽が画像の +x にあるとき、明るい側が +x を向く（高度差も反映する）。"""
        size = 41
        cx, cy = self._centroid(gv._moon_shade(size, 0.5, 0.0))
        self.assertGreater(cx - size / 2.0, 4.0, "右（太陽側）が明るくない")
        self.assertLess(abs(cy - size / 2.0), 2.0)
        cx2, cy2 = self._centroid(gv._moon_shade(size, 0.5, math.pi / 2.0))
        self.assertGreater(cy2 - size / 2.0, 4.0, "下（太陽側）が明るくない（角度が効いていない）")


class GalacticCoordTests(unittest.TestCase):
    def test_sgr_a_lands_on_the_galactic_plane(self):
        """いて座A*（銀河中心）を空の座標へ置き、銀経・銀緯へ戻すと (0,0) になる。"""
        ts, eph = gv._load_ephemeris()
        from skyfield.api import Star, wgs84
        lat, lon = 35.68, 139.69
        t = ts.utc(2026, 8, 1, 12, 0, 0)          # 銀河中心が南中に近い時刻
        star = Star(ra_hours=266.41683 / 15.0, dec_degrees=-29.00781)
        alt, az, _ = (eph["earth"] + wgs84.latlon(lat, lon)).at(t).observe(star).apparent().altaz()
        self.assertGreater(float(alt.degrees), 0.0, "この時刻は銀河中心が地平線上のはず")
        sky_h, W = 900, 1
        row = int(round((90.0 - float(alt.degrees)) / 90.0 * sky_h))
        l, b, _ = gv.galactic_lb(sky_h, W, 1.0, float(az.degrees), lat, lon,
                                 float(t.gmst) + lon / 15.0)
        dl = min(abs(float(l[row, 0])), 360.0 - abs(float(l[row, 0])))
        self.assertLess(dl, 3.0, "銀経が銀河中心から {:.1f}° ずれている".format(dl))
        self.assertLess(abs(float(b[row, 0])), 3.0,
                        "銀緯が銀河面から {:.1f}° ずれている".format(float(b[row, 0])))


class StarColorTests(unittest.TestCase):
    def test_red_and_blue_stars_differ(self):
        red = np.atleast_1d(gv.bv_to_rgb(1.8))
        blue = np.atleast_1d(gv.bv_to_rgb(-0.3))
        self.assertGreater(red[0], red[2], "赤い星（B-V=1.8）で R>B になっていない")
        self.assertGreater(blue[2], blue[0], "青い星（B-V=-0.3）で B>R になっていない")


class ParseWhenTests(unittest.TestCase):
    def test_iso8601_forms(self):
        self.assertEqual(gv._parse_when("2026-10-10T12:00:00Z"), (2026, 10, 10, 12, 0, 0))
        self.assertEqual(gv._parse_when("2026-10-10 08:15"), (2026, 10, 10, 8, 15, 0))

    def test_bad_value_is_reported(self):
        self.assertEqual(gv._parse_when("いつか"), "bad")


class FocusMatchTests(unittest.TestCase):
    def test_short_names_match(self):
        self.assertTrue(gv._in_focus("M31 アンドロメダ銀河", ["M31"]))
        self.assertTrue(gv._in_focus("木星", ["木星"]))
        self.assertFalse(gv._in_focus("デネブ", ["ベガ"]))

    def test_none_means_everything(self):
        self.assertTrue(gv._in_focus("何か", None))


class ToolContractTests(unittest.TestCase):
    """ネットワークを断っても、画像・リンク先行・figure/1・検証が揃うこと。"""

    def setUp(self):
        import requests
        self.requests = requests
        self.orig = (requests.get, requests.post)
        def boom(*a, **k):
            raise requests.ConnectionError("test: network disabled")
        requests.get, requests.post = boom, boom

    def tearDown(self):
        self.requests.get, self.requests.post = self.orig

    def test_result_contract(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z")
        sc = r.structuredContent
        kinds = [getattr(c, "type", "?") for c in r.content]
        self.assertIn("image", kinds)
        text = "\n".join(getattr(c, "text", "") for c in r.content[:kinds.index("image")])
        self.assertIn("🖼", text, "画像より前にリンク行がない")
        self.assertTrue(os.path.exists(sc["image_path"]), "image_path のファイルが無い")
        self.assertIn("ground", sc["engine"])
        self.assertIn("phase", sc)
        self.assertTrue(sc["star_catalog"]["source"], "星表の出典が空")
        fig = sc["figure"]
        self.assertEqual(fig["schema"], "figure/1")
        self.assertTrue(fig["notes"])
        self.assertTrue(fig["caption"])
        self.assertTrue((fig.get("verify") or {}).get("ok"), "verify.ok が真でない: {}".format(fig.get("verify")))
        self.assertEqual(kinds[-1], "image", "画像ブロックは最後に置く")

    def test_bad_coordinates_and_when_are_reported(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r1 = sky_map_with_satellites(lat="abc", lon="xyz")
        self.assertTrue(r1.structuredContent.get("error"))
        r2 = sky_map_with_satellites(place="東京", when="いつか")
        self.assertTrue(r2.structuredContent.get("error"))

    def test_legacy_engines_still_dispatch(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", engine="simple")
        self.assertIn("simple", r.structuredContent["engine"])
        self.assertIn("image", [getattr(c, "type", "?") for c in r.content])


if __name__ == "__main__":
    unittest.main()

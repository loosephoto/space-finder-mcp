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
- **show は月・太陽にも効く**（旧実装は draw_planets の月/太陽の分岐にフィルタが無く、
  `show="木星"` でも月が円盤＋ラベルで描かれていた）
- **when の空白のみは「未指定」として現在時刻**（旧実装は None を返し `ts.utc(*None)` で
  TypeError がツール外へ漏れた。空文字と同じ扱いに正規化する）
- **when の数値オフセットは UTC へ直す**（旧実装は `Z` だけ置換し `+09:00` を
  UTC として読み、9 時間ずれた図を無警告で出していた）
- **遮蔽高さ occl は描いた層から測る**（建物矩形の top だけだと塔・段状上層部・
  アンテナが入らず、塔の陰の衛星を「視野内なのに描かれていない」と誤判定して
  verify.ok=False になった）
- **視野の内外は描画・本文・検証で同じ判定**（旧実装は描画が ±30px を許し、
  本文と verify は `0<=x<W` で、図に部分的に描かれる衛星を「図には描かれない」と書いた）
- **視野外の天体は本文に理由付きで出る**（旧実装は「見えている天体: なし」だけで、
  地平線上に惑星があっても視野外だと何も言わなかった）
- **TLE 未取得を「地平線下」と書かない**（旧実装は取得失敗を無言で落とし、仰角48°の
  静止衛星について地平線下と言い切っていた）
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

    def test_whitespace_when_does_not_leak_an_exception(self):
        """空白のみの when で例外がツール外へ漏れない（旧実装は TypeError）。"""
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="   ")
        self.assertFalse(r.structuredContent.get("error"))
        self.assertTrue(r.structuredContent.get("time_utc"))

    def test_offset_when_matches_the_utc_equivalent(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        a = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z")
        b = sky_map_with_satellites(place="東京", when="2026-10-10T21:00:00+09:00")
        self.assertEqual(a.structuredContent["time_utc"], b.structuredContent["time_utc"])

    def test_tle_failure_is_not_reported_as_below_horizon(self):
        """TLE が取れないのを「地平線下」と書かない。"""
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z")
        text = "\n".join(getattr(c, "text", "") for c in r.content)
        self.assertIn("TLE", text)
        self.assertNotIn("なし(地平線下)", text)
        self.assertTrue(r.structuredContent["satellite_errors"])

    def test_unknown_light_pollution_is_reported(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z", lp="nope")
        text = "\n".join(getattr(c, "text", "") for c in r.content)
        self.assertIn("未対応", text)
        self.assertEqual(r.structuredContent["light_pollution"]["name"], gv.LP_MODES["suburb"]["name"])

    def test_milky_way_plane_outside_the_view_is_not_nan(self):
        """銀河面が視野に入らないとき milky_way に NaN を出さない（JSON 不正になる）。"""
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z",
                                    span=120.0, az0=90.0)
        mw = r.structuredContent["milky_way"]
        self.assertTrue(mw)
        for key, val in mw.items():
            self.assertEqual(val, val, "milky_way.{} が NaN".format(key))
        text = "\n".join(getattr(c, "text", "") for c in r.content)
        self.assertIn("視野の外", text)

    def test_show_with_no_match_is_reported(self):
        """show がどの天体にも当たらないときは黙って空の図を返さない。"""
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", when="2026-10-10T12:00:00Z", show="ありえない名前")
        text = "\n".join(getattr(c, "text", "") for c in r.content)
        self.assertIn("一致する天体がありません", text)

    def test_legacy_engines_still_dispatch(self):
        from space_finder_mcp.sky_overlay import sky_map_with_satellites
        r = sky_map_with_satellites(place="東京", engine="simple")
        self.assertIn("simple", r.structuredContent["engine"])
        self.assertIn("image", [getattr(c, "type", "?") for c in r.content])


class VerifyFrameTests(unittest.TestCase):
    """狭い視野で「視野外」を欠落に数えない（verify が偽にならない）ことを固定する。"""

    def _img(self, w=200, h=200):
        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (w, h), (0, 0, 0)).save(b, "JPEG")
        return b.getvalue()

    def _verify(self, sats, occl=None):
        return gv._ground_verify(self._img(), sats, 180.0, 0.0, 100, 0,
                                 np.full(200, 200.0) if occl is None else occl)

    def test_out_of_frame_is_not_a_failure(self):
        v = self._verify({"A": {"az": 200.0, "alt": 45.0}})     # 視野 0..180° の外
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["out_of_frame"], ["A"])
        self.assertEqual(v["missing"], [])

    def test_below_horizon_is_not_a_failure(self):
        v = self._verify({"A": {"az": 90.0, "alt": -5.0}})
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["below_horizon"], ["A"])

    def test_occluded_is_not_a_failure(self):
        v = self._verify({"A": {"az": 90.0, "alt": 5.0}}, occl=np.full(200, 0.0))
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["occluded"], ["A"])

    def test_in_frame_without_marker_is_a_failure(self):
        v = self._verify({"A": {"az": 90.0, "alt": 45.0}})
        self.assertFalse(v["ok"])
        self.assertEqual(len(v["missing"]), 1)

    def test_frame_x_maps_azimuth(self):
        self.assertAlmostEqual(gv._frame_x(90.0, 180.0, 0.0, 200), 100.0)
        self.assertAlmostEqual(gv._frame_x(350.0, 180.0, 270.0, 200), 88.888, places=2)


class ParseWhenOffsetTests(unittest.TestCase):
    """when の解釈（空白・数値オフセット・暦に無い日付）。"""

    def test_whitespace_only_is_treated_as_unspecified(self):
        """空白のみは空文字と同じ「未指定」に正規化する（None を返すと例外が漏れる）。"""
        import datetime as dt
        for blank in ("   ", "\t", "\n"):
            got = gv._parse_when(blank)
            self.assertEqual(len(got), 6, "空白のみで 6 要素の時刻が返らない: {!r}".format(blank))
            self.assertEqual(got[0], dt.datetime.now(dt.timezone.utc).year)

    def test_empty_or_none_is_now(self):
        import datetime as dt
        for empty in (None, "", "  ".strip()):
            got = gv._parse_when(empty)
            self.assertEqual(len(got), 6)
            self.assertEqual(got[0], dt.datetime.now(dt.timezone.utc).year)

    def test_numeric_offset_is_converted_to_utc(self):
        self.assertEqual(gv._parse_when("2026-10-10T21:00:00+09:00"), (2026, 10, 10, 12, 0, 0))
        self.assertEqual(gv._parse_when("2026-10-10T07:00:00-05:00"), (2026, 10, 10, 12, 0, 0))
        self.assertEqual(gv._parse_when("2026-10-10T21:00+0900"), (2026, 10, 10, 12, 0, 0))

    def test_fractional_seconds_and_z_are_accepted(self):
        self.assertEqual(gv._parse_when("2026-10-10T12:00:00.500Z"), (2026, 10, 10, 12, 0, 0))

    def test_impossible_date_is_bad(self):
        self.assertEqual(gv._parse_when("2026-02-30T12:00:00Z"), "bad")
        self.assertEqual(gv._parse_when("2026-10-10"), "bad")


class FramePredicateTests(unittest.TestCase):
    """視野の内外の判定は 1 つの述語（描画・本文・検証で同じ）。"""

    def test_in_frame_boundaries(self):
        self.assertTrue(gv._in_frame(0.0, 100))
        self.assertTrue(gv._in_frame(99.999, 100))
        self.assertFalse(gv._in_frame(-0.001, 100))
        self.assertFalse(gv._in_frame(100.0, 100))

    def test_frame_x_is_used_by_planets(self):
        """span の外にある惑星は描かれず、out_of_view に載る。"""
        scene = _scene(planets={"木星": dict(alt=30.0, az=200.0),
                                "土星": dict(alt=40.0, az=45.0)})
        _img, info = gv.render(scene, span=90.0, az0=0.0, sky_h=300, lp=gv._set_lp("suburb"))
        self.assertIn("木星", info["out_of_view"])
        self.assertNotIn("土星", info["out_of_view"])
        self.assertEqual(list(info["planets"]), ["土星"])
        marker = [m for m in info["planet_markers"] if m["id"] == "木星"][0]
        self.assertFalse(marker["in_frame"])
        self.assertTrue([m for m in info["planet_markers"] if m["id"] == "土星"][0]["in_frame"])


class TrailRunTests(unittest.TestCase):
    """可視区間が飛んだら破線を分ける（別々のパスを 1 本に繋がない）。"""

    def test_gap_splits_the_runs(self):
        pts = [{"az": 10.0, "alt": 5.0, "min": -5}, {"az": 12.0, "alt": 6.0, "min": -4},
               {"az": 300.0, "alt": 3.0, "min": 50}, {"az": 302.0, "alt": 4.0, "min": 51}]
        runs = gv._trail_runs(pts)
        self.assertEqual([len(r) for r in runs], [2, 2])
        self.assertEqual(gv._trail_runs([]), [])
        self.assertEqual([len(r) for r in gv._trail_runs(pts[:2])], [2])


class NearestWrapTests(unittest.TestCase):
    """RA の 0h/24h の巻き戻し。"""

    def _cat(self, ra_hours):
        return dict(ra_deg=np.array([ra_hours * 15.0]), dec_deg=np.array([10.0]),
                    vmag=np.array([1.0], np.float32), bv=np.array([0.6], np.float32))

    def test_star_near_zero_hours_matches_from_the_other_side(self):
        self.assertEqual(gv._nearest(self._cat(23.98), 0.01, 10.0), 0)

    def test_unrelated_position_returns_none(self):
        self.assertIsNone(gv._nearest(self._cat(23.98), 12.0, 10.0))


class StarAppearanceTests(unittest.TestCase):
    """「淡い星＝天の川の粒」は減光前の等級で判定する。"""

    def test_faint_flag_uses_intrinsic_magnitude(self):
        cat = dict(ra_deg=np.array([90.0]), dec_deg=np.array([0.0]),
                   vmag=np.array([5.2], np.float32), bv=np.array([0.6], np.float32))
        lin = np.zeros((300, 300, 3), np.float32)
        st = gv.draw_stars(lin, cat, np.array([10.0]), np.array([180.0]), 360.0, 0.0,
                           300, 300, 0, 8.6, 5.4)
        self.assertEqual(st["stars_drawn"], 1)
        self.assertEqual(st["faint_drawn"], 0,
                         "大気減光後の等級で『淡い星』判定している（本来 5.2 等）")


class MilkyWayStatsTests(unittest.TestCase):
    """銀河面が視野に入らないときに NaN を出さない。"""

    def test_empty_selection_is_zero(self):
        arr = np.arange(6, dtype=np.float32).reshape(2, 3)
        self.assertEqual(gv._mean_where(arr, np.zeros_like(arr, bool)), 0.0)
        self.assertEqual(gv._mean_where(arr, arr > 3), 4.5)


class CityPaletteTests(unittest.TestCase):
    """街の色はグローバルを書き換えない（並行呼び出しで混ざらない）。"""

    def test_palette_is_pure(self):
        from PIL import Image
        before = dict(gv.CITY_NIGHT)
        gv.draw_ground(Image.new("RGB", (100, 300), (0, 0, 0)), 200, 60, 100, 1, 1.0)
        self.assertEqual(gv.CITY_NIGHT, before)
        self.assertFalse(hasattr(gv, "CITY"), "モジュールグローバル CITY が復活している")
        self.assertEqual(gv._city_palette(1.0)["body"], gv.CITY_DAY["body"])
        self.assertEqual(gv._city_palette(0.0)["body"], gv.CITY_NIGHT["body"])


class OcclusionTests(unittest.TestCase):
    """遮蔽高さは「実際に描いた層」から測る。"""

    W, H, YH, GH = 333, 1400, 1116, 155

    def _ground(self, day_mix=1.0):
        from PIL import Image
        return gv.draw_ground(Image.new("RGB", (self.W, self.H), (0, 0, 0)),
                              self.YH, self.GH, self.W, 20261010, day_mix)

    def _rect_only(self, skyline):
        occl = np.full(self.W, float(self.YH), np.float32)
        for x0, x1, top in skyline:
            xa, xb = max(0, int(x0)), min(self.W, int(x1))
            if xb > xa:
                occl[xa:xb] = np.minimum(occl[xa:xb], float(top))
        return occl

    def test_occl_never_sits_below_the_drawn_layer(self):
        layer, _skyline, occl = self._ground()
        alpha = np.asarray(layer)[:, :, 3]
        above = alpha[:self.YH] > 40
        for x in np.nonzero(above.any(axis=0))[0]:
            top = int(np.nonzero(above[:, x])[0].min())
            self.assertLessEqual(int(occl[x]), top,
                                 "occl が描画より下にある列 {}: occl={} 描画={}".format(x, occl[x], top))

    def test_occl_is_higher_than_buildings_only(self):
        """塔・段状上層部・アンテナは建物矩形のリストに無いので必ず差が出る。"""
        _layer, skyline, occl = self._ground()
        gap = self._rect_only(skyline) - occl
        self.assertGreater(float(gap.max()), 4.0,
                           "描いた層から測った occl が建物矩形と同じ（塔が入っていない）")


class TowerShadowVerifyTests(unittest.TestCase):
    """塔の陰の衛星は「隠れている」であって「欠落」ではない（旧実装は ok=False）。"""

    def test_hidden_satellite_is_occluded_not_missing(self):
        from PIL import Image
        span, az0, sky_h, alt = 30.0, 0.0, 1000, 3.0
        az = 0.62 * span                                    # 塔の列（draw_ground の tx = 0.62W）
        scene = _scene(satellites={"TEST": {"az": az, "alt": alt, "kind": "geo", "trail": []}})
        img, info = gv.render(scene, span=span, az0=az0, sky_h=sky_h, lp=gv._set_lp("suburb"))
        W, H = info["size"]
        tx = int(0.62 * W)
        y = info["y_horizon"] - alt / 90.0 * sky_h
        _l, skyline, _o = gv.draw_ground(Image.new("RGB", (W, H), (0, 0, 0)),
                                        info["y_horizon"], info["ground_h"], W, 20261010, 0.0)
        rects = np.full(W, float(info["y_horizon"]), np.float32)
        for x0, x1, top in skyline:
            xa, xb = max(0, int(x0)), min(W, int(x1))
            if xb > xa:
                rects[xa:xb] = np.minimum(rects[xa:xb], float(top))
        self.assertLess(y, rects[tx], "前提が崩れた: 建物矩形だけでも塔の位置を隠している")
        self.assertGreater(y, info["occl"][tx], "前提が崩れた: 描いた層でも隠れていない")
        v = gv._ground_verify(img, {"TEST": {"az": az, "alt": alt}}, span, az0,
                              sky_h, info["header"], info["occl"])
        self.assertTrue(v["ok"], v)
        self.assertIn("TEST", v["occluded"])


class ShowFilterTests(unittest.TestCase):
    """show は月・太陽にも効く（旧実装は月が常に円盤＋ラベルで描かれた）。"""

    def test_moon_is_not_drawn_when_not_requested(self):
        lin = np.zeros((300, 300, 3), np.float32)
        pl = {"月": dict(alt=45.0, az=180.0, illum=0.5, dist_km=384400.0),
              "木星": dict(alt=30.0, az=190.0)}
        drawn, labels = gv.draw_planets(lin, pl, 360.0, 0.0, 300, 300, 0, lambda a: 6.0,
                                        90.0, -30.0,
                                        in_focus=lambda n: gv._in_focus(n, ["木星"]))
        self.assertEqual(list(drawn), ["木星"])
        self.assertEqual([n for n, _x, _y, _v in labels], ["木星"])

    def test_moon_is_drawn_without_focus(self):
        lin = np.zeros((300, 300, 3), np.float32)
        pl = {"月": dict(alt=45.0, az=180.0, illum=0.5, dist_km=384400.0)}
        drawn, labels = gv.draw_planets(lin, pl, 360.0, 0.0, 300, 300, 0, lambda a: 6.0,
                                        90.0, -30.0)
        self.assertIn("月", drawn)
        self.assertEqual(len(labels), 1)


class HeaderPlanTests(unittest.TestCase):
    """ヘッダは画像幅に合わせて組む（固定座標だと狭い図で重なり・見切れる）。"""

    def _plan(self, W, label=""):
        import datetime as dt
        cat = {"source": "Hipparcos 実星表（Vmag<8.5）"}
        scene = {"lat": 35.68, "lon": 139.69}
        return gv._header_plan(W, cat, scene, "夜", dt.datetime(2026, 10, 10, 21, 0),
                               360.0, label, gv._set_lp("suburb"), {"mw": 1.0})

    def test_every_item_fits_the_canvas(self):
        from PIL import Image, ImageDraw
        dr = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        for W in (240, 333, 1000, 1400, 3400):
            height, items = self._plan(W)
            self.assertTrue(items, "W={} で行が 1 つも無い".format(W))
            self.assertGreater(height, 60)
            for x, _y, text, font, _c, anchor in items:
                width = dr.textlength(text, font=font)
                if anchor == "la":
                    self.assertLessEqual(x + width, W, "W={} ではみ出し: {}".format(W, text[:20]))
                else:
                    self.assertGreaterEqual(x - width, 0, "W={} ではみ出し: {}".format(W, text[:20]))

    def test_wide_layout_does_not_overlap_left_and_right(self):
        from PIL import Image, ImageDraw
        dr = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        height, items = self._plan(3400)
        self.assertEqual(height, gv.HEADER_H)
        left = [it for it in items if it[5] == "la"]
        right = [it for it in items if it[5] == "ra"]
        for lx, ly, lt, lf, _lc, _la in left:
            lw = dr.textlength(lt, font=lf)
            for rx, ry, rt, rf, _rc, _ra in right:
                rw = dr.textlength(rt, font=rf)
                if ly + 48 < ry or ry + 30 < ly:            # 縦に重ならない
                    continue
                self.assertLessEqual(lx + lw, rx - rw,
                                     "ヘッダの左右が重なる: {} / {}".format(lt[:12], rt[:12]))


def _scene(planets=None, satellites=None, when=(2026, 10, 10, 12, 0, 0)):
    """render 用の最小シーン（暦はローカルの timescale だけで足りる）。"""
    ts = gv._load_ephemeris()[0]
    cat = dict(ra_deg=np.array([0.0, 90.0, 180.0]), dec_deg=np.array([10.0, 20.0, -10.0]),
               vmag=np.array([1.0, 2.0, 3.0], np.float32),
               bv=np.array([0.6, 0.6, 0.6], np.float32),
               source="テスト星表", fallback=True)
    return dict(catalog=cat, star_alt=np.array([45.0, 30.0, 60.0]),
                star_az=np.array([10.0, 100.0, 200.0]), planets=planets or {},
                sun=dict(alt=-30.0, az=200.0, rising=False), satellites=satellites or {},
                dso_alt=np.array([10.0]), dso_az=np.array([100.0]),
                lat=35.68, lon=139.69, t=ts.utc(*when), when_utc=when)


if __name__ == "__main__":
    unittest.main()

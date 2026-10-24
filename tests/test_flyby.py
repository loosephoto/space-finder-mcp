"""接近通過（フライバイ）対応の回帰テスト（`planetary_orbiter_track`）。

実バグ／仕様として固定したいもの:

- **Horizons の数値JD は TDB として解釈される**（実測: 数値JD と暦文字列が同じ状態を返し、
  見出しは "… TDB"）。UTC の暦から作った JD をそのまま送ると **69.184 秒ずれる**ので
  `_jd_from_utc` で補正する（実測: BepiColombo 第6水星フライバイの最接近が ESA 公表値
  05:58:52 UTC に対し補正前は 69 秒ずれ、補正後は **-1 秒**）
- **ミッション名でフライバイ機を指定できる**（旧実装は Horizons の負のID しか受け付けず、
  図の題名も "-159" と ID のまま出ていた）
- **最接近（CA）を自動探索**して、最接近時刻・高度・相対速度・最接近点を出す（旧実装は
  指定時刻の1点だけ）
- **窓内の最小距離と接近通過を区別する**（天体半径の 50 倍より遠い「最小」はフライバイではない。
  実測: Europa Clipper の 2030-06 の窓では最小でも 1,512 万 km）
- **ガリレオ衛星（イオ・エウロパ・ガニメデ・カリスト）を対象天体に追加**
  （自転モデルは NAIF pck00011.tpc の値。旧実装は "不明な天体 'europa'"）
- **flyby=False の従来動作を変えない**
"""
import datetime
import io
import sys
import unittest

sys.path.insert(0, "src")

from PIL import Image  # noqa: E402

from space_finder_mcp import planetary_map as pm  # noqa: E402

JD_EPOCH = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)


class TimeScaleTests(unittest.TestCase):
    """Horizons に送る数値JD は TDB（実測）。UTC の暦からは 69.184 秒ずらす。"""

    def test_jd_from_utc_adds_tt_minus_utc(self):
        jd = pm._jd_from_utc(JD_EPOCH)
        # 2451545.0 の近くでは float64 の分解能が ~5e-10 日（≒47 µs）なので places=12 は落ちる
        self.assertAlmostEqual(jd - 2451545.0, pm._TT_MINUS_UTC_S / 86400.0, places=8)
        self.assertAlmostEqual((jd - 2451545.0) * 86400.0, pm._TT_MINUS_UTC_S, places=4)

    def test_round_trip_through_tdb(self):
        for t in (datetime.datetime(2025, 1, 8, 5, 58, 52, tzinfo=datetime.timezone.utc),
                  datetime.datetime(2026, 10, 24, 22, 0, 0, tzinfo=datetime.timezone.utc),
                  datetime.datetime(2030, 12, 3, 0, 0, 0, tzinfo=datetime.timezone.utc)):
            back = pm._jd_to_utc_datetime(pm._jd_from_utc(t))
            # 往復の丸めは float64 の分解能（~50 µs）ぶんだけ残る
            self.assertLess(abs((back - t).total_seconds()), 1e-3, t)

    def test_tt_minus_utc_is_the_2020s_value(self):
        # TT−UTC は 69.184 秒（2020年代）。桁を間違えると 69 秒 → 690 秒になる
        self.assertTrue(69.0 < pm._TT_MINUS_UTC_S < 69.5)


class SpacecraftNameTests(unittest.TestCase):
    """フライバイ機をミッション名（和名・別名）で指定できる。"""

    def test_ids_are_horizons_ids(self):
        for key, v in pm.SPACECRAFT.items():
            self.assertRegex(str(v["id"]), r"^-\d+$", key)
            self.assertTrue(v.get("ja") and v.get("en"), key)
            self.assertTrue(v.get("aliases"), key)

    def test_verified_ids(self):
        # すべて JPL Horizons に問い合わせて確認した値（2026-10-24 実測）
        want = {"europa clipper": "-159", "juice": "-28", "bepicolombo": "-121",
                "hera": "-91", "psyche": "-255", "lucy": "-49", "new horizons": "-98",
                "hayabusa 2": "-37", "juno": "-61", "parker solar probe": "-96",
                "solar orbiter": "-144", "mmx": "-239"}
        for k, v in want.items():
            self.assertEqual(pm.SPACECRAFT[k]["id"], v, k)

    def test_english_name_matches(self):
        self.assertEqual(pm._spacecraft_pick(pm._name_candidates("Europa Clipper")),
                         "europa clipper")
        self.assertEqual(pm._spacecraft_pick(pm._name_candidates("JUICE")), "juice")

    def test_japanese_alias_matches(self):
        self.assertEqual(pm._spacecraft_pick(pm._name_candidates("エウロパ・クリッパー")),
                         "europa clipper")
        self.assertEqual(pm._spacecraft_pick(pm._name_candidates("ベピコロンボ")), "bepicolombo")
        self.assertEqual(pm._spacecraft_pick(pm._name_candidates("はやぶさ2")), "hayabusa 2")

    def test_unknown_returns_none(self):
        self.assertIsNone(pm._spacecraft_pick(pm._name_candidates("ノーチラス")))


class GalileanBodyTests(unittest.TestCase):
    """ガリレオ衛星を対象天体に追加（自転モデルは NAIF pck00011.tpc の実測値）。"""

    # pck00011.tpc: BODY50x_POLE_RA / POLE_DEC / PM（実測・2026-10-24）
    PCK = {
        "io": (268.05, -0.009, 64.50, 0.003, 200.39, 203.4889538, 1821.5),
        "europa": (268.08, -0.009, 64.51, 0.003, 36.022, 101.3747235, 1560.8),
        "ganymede": (268.20, -0.009, 64.57, 0.003, 44.064, 50.3176081, 2631.2),
        "callisto": (268.72, -0.009, 64.83, 0.003, 259.51, 21.5710715, 2410.3),
    }

    def test_bodies_exist_with_pck_constants(self):
        for key, (a0, da, d0, dd, w0, wd, r) in self.PCK.items():
            cfg = pm.BODIES[key]
            self.assertAlmostEqual(cfg["a0"], a0, places=4, msg=key)
            self.assertAlmostEqual(cfg["da"], da, places=4, msg=key)
            self.assertAlmostEqual(cfg["d0"], d0, places=4, msg=key)
            self.assertAlmostEqual(cfg["dd"], dd, places=4, msg=key)
            self.assertAlmostEqual(cfg["W0"], w0, places=4, msg=key)
            self.assertAlmostEqual(cfg["Wd"], wd, places=5, msg=key)
            self.assertAlmostEqual(cfg["radius"], r, places=1, msg=key)

    def test_centers_are_horizons_satellite_ids(self):
        for key, c in (("io", "@501"), ("europa", "@502"),
                       ("ganymede", "@503"), ("callisto", "@504")):
            self.assertEqual(pm.BODIES[key]["center"], c, key)

    def test_galilean_bodies_use_the_global_image_basemap(self):
        for key in self.PCK:
            self.assertNotIn("tile", pm.BODIES[key], key)          # Trek にタイルが無い
            self.assertEqual(pm.MAP_BODIES[key]["basemap"], "image", key)
            self.assertTrue(pm.MAP_BODIES[key]["image"]["url"].startswith("https://"), key)
            self.assertIn("credit", pm.MAP_BODIES[key]["image"], key)

    def test_attrib_comes_from_the_image_credit(self):
        for key in self.PCK:
            body_cfg = dict(pm.BODIES[key])
            attrib = pm._basemap_attrib(body_cfg, key)
            self.assertEqual(attrib, pm.MAP_BODIES[key]["image"]["credit"], key)
            self.assertTrue(attrib)


class BasemapDispatchTests(unittest.TestCase):
    """タイル（Trek）と全球画像（1枚）でベースマップを選び分ける。"""

    def _stub(self, calls):
        def fake_surface(body, lon, lat, span, zoom, out_px, **kw):
            calls.append(("tile", body.get("ja")))
            return {"img": Image.new("RGB", (10, 10)), "g2px": lambda a, b: (0, 0),
                    "inv": lambda x, y: (0, 0), "tiles_failed": 0, "tiles_total": 1}

        def fake_image(url, lon, lat, span, out_px, **kw):
            calls.append(("image", url[:32]))
            return {"img": Image.new("RGB", (10, 10)), "g2px": lambda a, b: (0, 0),
                    "inv": lambda x, y: (0, 0), "credit": kw.get("credit")}
        return fake_surface, fake_image

    def test_tile_body_uses_surface_view(self):
        calls = []
        old_s, old_i = pm.surface_view, pm.image_view
        fake_s, fake_i = self._stub(calls)
        try:
            pm.surface_view, pm.image_view = fake_s, fake_i
            pm._basemap_view(dict(pm.BODIES["moon"]), "moon", 0.0, 0.0, 120.0, 3, 600)
        finally:
            pm.surface_view, pm.image_view = old_s, old_i
        self.assertEqual(calls[0][0], "tile")

    def test_image_body_uses_image_view(self):
        calls = []
        old_s, old_i = pm.surface_view, pm.image_view
        fake_s, fake_i = self._stub(calls)
        try:
            pm.surface_view, pm.image_view = fake_s, fake_i
            pm._basemap_view(dict(pm.BODIES["europa"]), "europa", 0.0, 0.0, 120.0, 3, 600)
        finally:
            pm.surface_view, pm.image_view = old_s, old_i
        self.assertEqual(calls[0][0], "image")

    def test_image_body_without_definition_raises(self):
        with self.assertRaises(ValueError):
            pm._image_basemap("ryugu")          # graticule のみで image 定義が無い


class ClosestApproachTests(unittest.TestCase):
    """最接近の探索（粗い走査 → 1分刻み → 3点放物線フィット）を合成データで固定する。"""

    BODY = {"ja": "テスト", "en": "Test", "center": "@999", "radius": 500.0,
            "a0": 0.0, "da": 0.0, "d0": 90.0, "dd": 0.0, "W0": 0.0, "Wd": 360.0}

    def _run(self, t0_days, dmin_km, v_kms=6.0, half_days=1.0, min_offset_days=0.0):
        """距離 d(t) = sqrt(dmin² + (v·Δt)²) の合成状態を返すようにスタブする。

        `min_offset_days` は最接近を窓の中心からずらす量（端の検出テスト用）。
        """
        jd0 = pm._jd_from_utc(JD_EPOCH) + t0_days
        jd_min = jd0 + min_offset_days

        def states(cmd, center, jds):
            # 距離 d(t)=sqrt(b²+(v·Δt)²) を満たす双曲線通過。速度は
            # 動径成分 v²Δt/r・接線成分 v·b/r で、|v| は常に v（角運動量 b·v が保存）。
            out = []
            for jd in jds:
                dt = (jd - jd_min) * 86400.0
                r = (dmin_km ** 2 + (v_kms * dt) ** 2) ** 0.5
                v_r = v_kms * v_kms * dt / max(r, 1e-9)
                v_t = v_kms * dmin_km / max(r, 1e-9)
                out.append((r, 0.0, 0.0, v_r, v_t, 0.0))
            return out

        def single(cmd, center, jd):
            return states(cmd, center, [jd])[0]

        old_many, old_single = pm._horizons_states, pm._horizons_state_single
        try:
            pm._horizons_states, pm._horizons_state_single = states, single
            return pm._closest_approach(self.BODY, "-999", jd0, half_days)
        finally:
            pm._horizons_states, pm._horizons_state_single = old_many, old_single

    def test_finds_the_minimum_time_to_seconds(self):
        ca = self._run(0.37, 1000.0)
        self.assertTrue(ca["ok"])
        self.assertFalse(ca["edge"])
        err_s = abs((ca["jd"] - (pm._jd_from_utc(JD_EPOCH) + 0.37)) * 86400.0)
        self.assertLess(err_s, 2.0, "最接近時刻の誤差 %.1f 秒" % err_s)

    def test_reports_altitude_and_speed(self):
        ca = self._run(0.37, 1000.0, v_kms=6.0)
        self.assertAlmostEqual(ca["alt_km"], 500.0, delta=2.0)      # 1000 km − 半径 500 km
        self.assertAlmostEqual(ca["speed_kms"], 6.0, delta=0.05)

    def test_encounter_classification(self):
        close = self._run(0.37, 1000.0)
        self.assertTrue(close["encounter"], close)                  # 半径500kmの50倍=25,000km以内
        far = self._run(0.37, 1.0e7)
        self.assertFalse(far["encounter"], far)                     # 窓内の最小であって通過ではない

    def test_edge_minimum_is_flagged(self):
        # 最小が窓の外（0.9 日先）にあるとき、窓の端で最小になる
        ca = self._run(0.0, 1000.0, half_days=0.05, min_offset_days=0.9)
        self.assertTrue(ca["edge"], ca)
        # 窓の中に最小があるときは edge にならない（偽陽性を出さない）
        ok = self._run(0.0, 1000.0, half_days=1.0, min_offset_days=0.9)
        self.assertFalse(ok["edge"], ok)

    def test_scan_uses_batched_coarse_points(self):
        ca = self._run(0.37, 1000.0)
        self.assertGreaterEqual(ca["coarse_points"], 97)            # ±1日を30分刻み
        self.assertLessEqual(ca["coarse_points"], 201)              # 上限 200 点＋1
        self.assertEqual(ca["fine_points"], 41)                     # ±30分を1分刻み
        self.assertEqual(ca["coarse_min"], 30.0)


class FlybyRenderTests(unittest.TestCase):
    """実際の描画: flyby=False の従来動作を変えず、flyby=True で CA を出す。"""

    def _patch(self, ca):
        self.calls = {}

        def fake_state(body, cmd, jd):
            return {"lat": 10.0, "lon": 20.0, "alt_km": 50.0, "speed_kms": 1.6, "dist_km": 1787.4}

        def fake_ca(body, cmd, jd0, half_days, coarse_min=30.0):
            self.calls["ca_window"] = half_days
            return ca

        def fake_view(body_cfg, body_key, lon, lat, span, zoom, out_px):
            self.calls["view_key"] = body_key
            return {"img": Image.new("RGB", (1200, 900), (20, 20, 20)),
                    "g2px": lambda lon, lat: (600.0, 450.0),
                    "inv": lambda x, y: (20.0, 10.0),
                    "zoom": 1, "deg_per_px": 0.6, "tiles_failed": 0, "tiles_total": 1}

        self._old = (pm._state, pm._closest_approach, pm._basemap_view,
                     pm._horizons_state_single, pm._vec_to_state)
        pm._state = fake_state
        pm._closest_approach = fake_ca
        pm._basemap_view = fake_view
        pm._horizons_state_single = (lambda cmd, center, jd:
                                     (1787.4, 0.0, 0.0, 0.0, 1.6, 0.0))
        # flyby では CA の状態も _vec_to_state から取るので、同じ値にそろえる
        # （偽の投影 inv が (20, 10) を返すため、報告座標もそれに合わせる）
        pm._vec_to_state = lambda body, x, y, z, vx, vy, vz, jd: fake_state(body, "", jd)

    def _restore(self):
        (pm._state, pm._closest_approach, pm._basemap_view,
         pm._horizons_state_single, pm._vec_to_state) = self._old

    def _ca_fixture(self, encounter=True, edge=False):
        jd = pm._jd_from_utc(datetime.datetime(2025, 1, 8, 5, 58, 51, tzinfo=datetime.timezone.utc))
        return {"ok": True, "jd": jd, "edge": edge, "encounter": encounter,
                "encounter_threshold_km": 122000.0, "dist_km": 2735.5, "alt_km": 295.8,
                "speed_kms": 4.4256, "lat": 45.0028, "lon": 40.7486,
                "coarse_points": 193, "fine_points": 41, "coarse_min": 30.0, "half_days": 1.0}

    def test_default_mode_is_unchanged(self):
        self._patch(self._ca_fixture())
        try:
            r = pm.planetary_orbiter_track(body="moon", orbiter="lro",
                                           when="2026-10-14T21:00:00+09:00")
        finally:
            self._restore()
        sc = r.structuredContent
        self.assertIsNone(sc["closest_approach"])            # flyby=False では探索しない
        self.assertEqual(sc["display_time_utc"], sc["requested_time_utc"])
        self.assertEqual(sc["figure"]["markers"][0]["id"], "current")
        self.assertFalse(sc["flyby"])

    def test_flyby_mode_moves_the_display_time_to_ca(self):
        self._patch(self._ca_fixture())
        try:
            r = pm.planetary_orbiter_track(body="mercury", orbiter="bepicolombo",
                                           when="2025-01-08T06:30:00Z", flyby=True)
        finally:
            self._restore()
        sc = r.structuredContent
        ca = sc["closest_approach"]
        self.assertEqual(ca["utc"], "2025-01-08 05:58:51 UTC")
        self.assertEqual(ca["altitude_km"], 295.8)
        self.assertTrue(ca["encounter"])
        self.assertEqual(sc["display_time_utc"], "2025-01-08 05:58 UTC")
        self.assertEqual(sc["requested_time_utc"], "2025-01-08 06:30 UTC")
        self.assertEqual(sc["name_ja"], "ベピコロンボ（みお）")
        self.assertEqual(sc["figure"]["markers"][0]["id"], "closest_approach")
        self.assertIn("最接近", sc["figure"]["markers"][0]["label"])
        txt = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
        self.assertIn("最接近（CA）", txt)
        self.assertIn("表示時刻を最接近時刻へ置き直しました", txt)
        self.assertIn("水星フライバイ6回", txt)              # ミッションの出来事
        self.assertTrue(sc["figure"]["verify"]["ok"])

    def test_far_minimum_is_not_called_a_flyby(self):
        self._patch(self._ca_fixture(encounter=False))
        try:
            r = pm.planetary_orbiter_track(body="europa", orbiter="europa clipper",
                                           when="2030-06-01T00:00:00Z", flyby=True)
        finally:
            self._restore()
        sc = r.structuredContent
        self.assertFalse(sc["closest_approach"]["encounter"])
        self.assertEqual(sc["display_time_utc"], sc["requested_time_utc"])   # 置き直さない
        self.assertEqual(sc["figure"]["markers"][0]["id"], "current")
        txt = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
        self.assertIn("接近通過は見つかりませんでした", txt)

    def test_edge_minimum_warns_to_widen_the_window(self):
        self._patch(self._ca_fixture(edge=True))
        try:
            r = pm.planetary_orbiter_track(body="mercury", orbiter="bepicolombo",
                                           when="2025-01-08T06:30:00Z", flyby=True)
        finally:
            self._restore()
        txt = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
        self.assertIn("探索範囲", txt)
        self.assertEqual(r.structuredContent["display_time_utc"], "2025-01-08 06:30 UTC")

    def test_search_error_is_reported_not_swallowed(self):
        def boom(body, cmd, jd0, half_days, coarse_min=30.0):
            raise ValueError("Horizons 応答に座標ブロックがありません")
        old_ca = pm._closest_approach
        self._patch(self._ca_fixture())
        pm._closest_approach = boom
        try:
            r = pm.planetary_orbiter_track(body="mercury", orbiter="bepicolombo",
                                           when="2025-01-08T06:30:00Z", flyby=True)
        finally:
            pm._closest_approach = old_ca
            self._restore()
        sc = r.structuredContent
        self.assertFalse(sc["closest_approach"]["ok"])
        txt = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
        self.assertIn("最接近の探索に失敗", txt)

    def test_japanese_body_name_is_expanded(self):
        self._patch(self._ca_fixture(encounter=False))
        try:
            r = pm.planetary_orbiter_track(body="エウロパ", orbiter="エウロパ・クリッパー",
                                           when="2030-06-01T00:00:00Z", flyby=True)
        finally:
            self._restore()
        sc = r.structuredContent
        self.assertEqual(sc["body"], "europa")
        self.assertEqual(sc["name_ja"], "エウロパ・クリッパー")
        self.assertEqual(self.calls["view_key"], "europa")


if __name__ == "__main__":
    unittest.main()

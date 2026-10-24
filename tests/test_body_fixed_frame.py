"""天体固定座標（IAU/WGCCRE）への変換の回帰テスト。

背景: `_j2000_to_body()` は長らく `Rz(W)·Ry(δ−90°)·Rz(α)` で回しており、IAU/WGCCRE の
標準式 `Rz(W)·Rx(90°−δ)·Rz(α+90°)` と**経度が +90.00° ずれていた**（2026-10-24 の月・火星・
水星・ガニメデの4天体すべてで 経度の差 +90.00°・緯度の差 0.00°＝緯度は元から正しかった）。

ずれの向き（どちらが正しいか）は物理と公表値で決まるので、このファイルは JPL Horizons の
生の状態ベクトルを定数として埋め込み（＝ネットワーク不要）、次の 4 種類の物理チェックで固定する:

  (1) 月の「地球直下」の経度は光学秤動（±7.9°）の中に入る（緯度は ±6.7°）
  (2) 同期回転衛星（ガリレオ4衛星）の「木星直下」の経度はほぼ 0°（本初子午線が平均木星直下点）
  (3) Juno PJ34（2021-06-07 16:56:08 UTC）の探査機・太陽の直下点が論文公表値と一致
  (4) 月の「太陽直下」と「地球直下」の角距離が、満月近くの照度 96% と整合（位相角 ≈ 21.9°）

数値は `_body_to_latlon()` の東向き正・惑星中心経度（°E）。
"""

import datetime
import math
import unittest

from space_finder_mcp import planetary_map as pm

UTC = datetime.timezone.utc

# JPL Horizons の状態ベクトル（ICRF/J2000, km, 天体中心から見た始点天体の位置）
FIXTURES: list[dict] = [
    dict(name="moon_sub_earth_20261024", key="moon", src="399", center="@301",
         utc="2026-10-24T12:00:00Z",
         vec=(-371036.966846, -43838.881142, -42894.360127), lon=-5.6258, lat=-3.3365),
    dict(name="moon_sub_earth_20261003", key="moon", src="399", center="@301",
         utc="2026-10-03T12:00:00Z",
         vec=(58670.319786, -324345.041653, -168299.734343), lon=1.4432, lat=-3.9171),
    dict(name="moon_sub_earth_20261010", key="moon", src="399", center="@301",
         utc="2026-10-10T12:00:00Z",
         vec=(373234.907123, 81181.344619, 62460.746714), lon=4.9694, lat=3.6994),
    dict(name="moon_sub_earth_20261017", key="moon", src="399", center="@301",
         utc="2026-10-17T12:00:00Z",
         vec=(-76952.728791, 353078.241809, 181653.198712), lon=-1.1776, lat=3.6992),
    dict(name="moon_sub_sun_20261024", key="moon", src="10", center="@301",
         utc="2026-10-24T12:00:00Z",
         vec=(-128286647.892761, -69792038.278997, -30276231.416436), lon=16.2654, lat=0.0044),
    dict(name="ganymede_sub_jupiter_pj34", key="ganymede", src="599", center="@503",
         utc="2021-06-07T16:56:08Z",
         vec=(-119730.155584, 958603.592643, 457858.880533), lon=-2.6325, lat=0.1981),
    dict(name="io_sub_jupiter_pj34", key="io", src="599", center="@501",
         utc="2021-06-07T16:56:08Z",
         vec=(-369288.52484, 183973.76829, 81538.827797), lon=-0.7435, lat=-0.0196),
    dict(name="europa_sub_jupiter_pj34", key="europa", src="599", center="@502",
         utc="2021-06-07T16:56:08Z",
         vec=(-674325.868251, 55810.978702, 10152.287897), lon=-1.1922, lat=-0.4325),
    dict(name="callisto_sub_jupiter_pj34", key="callisto", src="599", center="@504",
         utc="2021-06-07T16:56:08Z",
         vec=(419794.560576, -1663526.994564, -778287.287975), lon=1.7246, lat=-0.0327),
    dict(name="juno_ganymede_pj34", key="ganymede", src="-61", center="@503",
         utc="2021-06-07T16:56:08Z",
         vec=(2515.33605, 1353.594441, 2315.765992), lon=-57.9007, lat=23.6736),
    dict(name="sun_ganymede_pj34", key="ganymede", src="10", center="@503",
         utc="2021-06-07T16:56:08Z",
         vec=(-581904123.994129, 437663962.0539, 201803547.084005), lon=41.3097, lat=0.1721),
    dict(name="mars_sub_earth_20261024", key="mars", src="399", center="@499",
         utc="2026-10-24T12:00:00Z",
         vec=(156119281.237047, -144101574.355187, -68615253.464053), lon=7.5432, lat=19.2308),
    dict(name="mercury_sub_sun_20261024", key="mercury", src="10", center="@199",
         utc="2026-10-24T12:00:00Z",
         vec=(-53776322.219997, 10638803.888085, 11256534.348269), lon=-168.9457, lat=-0.0235),
]

# Juno PJ34 フライバイの公表値（JPL PDS の接近ログ＝2021-06-07 16:56:08 UTC、
# 高度 1046 km、直下点 23.6°N / 56.8°W。太陽直下は 0.06°N / 318°W = 42°E）
PUBLISHED_PJ34: dict = dict(lon_west=56.8, lat_north=23.6, sun_lon_west=318.0, sun_lat_north=0.06)


def sub_point(fx: dict):
    """フィクスチャの生ベクトルを天体固定座標へ回し、真下の点（°E, °N）を返す。"""
    body = pm.BODIES[fx["key"]]
    jd = pm._jd_from_utc(datetime.datetime.strptime(fx["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
    xb, yb, zb = pm._j2000_to_body(body, *fx["vec"], jd)
    lat, lon, _ = pm._body_to_latlon(body, xb, yb, zb)
    return lon, lat


def angular_distance(lon1, lat1, lon2, lat2):
    """2 点の大円距離（°）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    return math.degrees(math.acos(max(-1.0, min(1.0,
        math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))))


class BodyFixedFixtures(unittest.TestCase):
    """実測値を固定する（変換そのものの回帰）。"""

    def test_measured_longitudes_and_latitudes(self):
        for fx in FIXTURES:
            with self.subTest(name=fx["name"]):
                lon, lat = sub_point(fx)
                self.assertAlmostEqual(lon, fx["lon"], delta=0.01)
                self.assertAlmostEqual(lat, fx["lat"], delta=0.01)

    def test_rotation_preserves_length(self):
        for fx in FIXTURES:
            with self.subTest(name=fx["name"]):
                body = pm.BODIES[fx["key"]]
                jd = pm._jd_from_utc(datetime.datetime.strptime(
                    fx["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
                xb, yb, zb = pm._j2000_to_body(body, *fx["vec"], jd)
                self.assertAlmostEqual(math.sqrt(xb * xb + yb * yb + zb * zb),
                                       math.sqrt(sum(c * c for c in fx["vec"])), delta=1e-6)

    def test_90_degree_offset_is_gone(self):
        """旧実装の値（標準式 +90°）とは 45° 以上離れていること。"""
        for fx in FIXTURES:
            with self.subTest(name=fx["name"]):
                lon, _ = sub_point(fx)
                gap = abs((lon - (fx["lon"] + 90.0) + 180.0) % 360.0 - 180.0)
                self.assertGreater(gap, 45.0)


class PhysicalChecks(unittest.TestCase):
    """ずれの向きを決めた物理チェック（この 4 つがテストの本体）。"""

    def test_moon_sub_earth_is_within_optical_libration(self):
        """月の地球直下は経度 ±7.9°・緯度 ±6.7° の光学秤動の中に入る。"""
        lons, lats = [], []
        for fx in FIXTURES:
            if fx["key"] != "moon" or fx["src"] != "399":
                continue
            lon, lat = sub_point(fx)
            lons.append(lon)
            lats.append(lat)
        self.assertEqual(len(lons), 4)
        for lon in lons:
            self.assertLess(abs(lon), 7.9, "月の地球直下の経度が光学秤動の外（lon=%.2f）" % lon)
        for lat in lats:
            self.assertLess(abs(lat), 6.7, "月の地球直下の緯度が秤動の外（lat=%.2f）" % lat)
        # 秤動を実際にサンプルできていること（全点が同じ側に固まっていない）
        self.assertLess(min(lons), -1.0)
        self.assertGreater(max(lons), 4.0)

    def test_moon_phase_angle_matches_illumination(self):
        """地球直下と太陽直下の角距離 = 位相角。2026-10-24 は照度 96%（記事）と整合。"""
        earth = next(fx for fx in FIXTURES if fx["name"] == "moon_sub_earth_20261024")
        sunfx = next(fx for fx in FIXTURES if fx["name"] == "moon_sub_sun_20261024")
        lon_e, lat_e = sub_point(earth)
        lon_s, lat_s = sub_point(sunfx)
        phase = angular_distance(lon_e, lat_e, lon_s, lat_s)
        self.assertAlmostEqual(phase, 21.9, delta=0.5)
        illumination = (1.0 + math.cos(math.radians(phase))) / 2.0
        self.assertAlmostEqual(illumination * 100.0, 96.4, delta=0.6)

    def test_galilean_moons_face_jupiter(self):
        """同期回転衛星の本初子午線は平均木星直下点 → 木星直下の経度は 0° 付近。"""
        seen = 0
        for fx in FIXTURES:
            if fx["src"] != "599":
                continue
            seen += 1
            lon, lat = sub_point(fx)
            with self.subTest(name=fx["name"]):
                self.assertLess(abs(lon), 5.0, "%s の木星直下の経度が 0° から離れすぎ（%.2f）" % (fx["key"], lon))
                self.assertLess(abs(lat), 1.0, "%s の木星直下の緯度が大きすぎ（%.2f）" % (fx["key"], lat))
        self.assertEqual(seen, 4, "ガリレオ4衛星すべてを検査すること")

    def test_juno_pj34_matches_published_values(self):
        """Juno PJ34 の公表値（探査機 23.6°N / 56.8°W、太陽 0.06°N / 318°W）と一致。"""
        lon, lat = sub_point(next(fx for fx in FIXTURES if fx["name"] == "juno_ganymede_pj34"))
        self.assertAlmostEqual(lon, -PUBLISHED_PJ34["lon_west"], delta=2.0)
        self.assertAlmostEqual(lat, PUBLISHED_PJ34["lat_north"], delta=0.5)
        sun_lon, sun_lat = sub_point(next(fx for fx in FIXTURES if fx["name"] == "sun_ganymede_pj34"))
        self.assertAlmostEqual(sun_lon, 360.0 - PUBLISHED_PJ34["sun_lon_west"], delta=2.0)
        self.assertAlmostEqual(sun_lat, PUBLISHED_PJ34["sun_lat_north"], delta=0.5)

    def test_mars_and_mercury_reference_points(self):
        """火星の地球直下（2026-10-24）と水星の太陽直下（軸傾斜ほぼ 0 → 緯度 ≈ 0）。"""
        lon, lat = sub_point(next(fx for fx in FIXTURES if fx["name"] == "mars_sub_earth_20261024"))
        self.assertAlmostEqual(lon, 7.54, delta=0.05)
        self.assertAlmostEqual(lat, 19.23, delta=0.05)
        lon, lat = sub_point(next(fx for fx in FIXTURES if fx["name"] == "mercury_sub_sun_20261024"))
        self.assertAlmostEqual(lon, -168.95, delta=0.05)
        self.assertLess(abs(lat), 0.1)


if __name__ == "__main__":
    unittest.main()

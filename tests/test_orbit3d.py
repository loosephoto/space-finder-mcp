"""天体中心 3D 俯瞰（orbit3d）とカメラ引数の回帰テスト（ネットワーク不要）。

観点:
  1. 幾何（緯度経度高度 → 3D、軌道面の法線、傾斜角の定義 0–180°）
  2. 自転軸の逆変換（`_pole_axis_in_frame` が本当に天体の z 軸を戻すか）
  3. 描画（合成軌道で marker/軌道の画素が出る・verify.ok・カメラが反映される）
  4. カメラ引数の検証（範囲外・非数値・NaN をクランプせずエラーにする）
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from space_finder_mcp import orbit3d  # noqa: E402
from space_finder_mcp import orbit_view  # noqa: E402
from space_finder_mcp import planetary_map as pm  # noqa: E402

R_MOON = 1737.4


def circle_points(inc_deg=0.0, alt_km=100.0, r_body=R_MOON, n=72, retro=False):
    """赤道面から inc_deg 傾けた円軌道の点列（天体固定座標・km）。"""
    r = r_body + alt_km
    inc = math.radians(inc_deg)
    pts = []
    for k in range(n):
        th = 2 * math.pi * k / n * (-1.0 if retro else 1.0)
        x = r * math.cos(th)
        y = r * math.sin(th) * math.cos(inc)
        z = r * math.sin(th) * math.sin(inc)
        pts.append((x, y, z))
    return pts


class GeometryTests(unittest.TestCase):
    def test_to_xyz_axes(self):
        x, y, z = orbit3d.to_xyz(0.0, 0.0, 0.0, 1000.0)
        self.assertAlmostEqual(x, 1000.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(z, 0.0)
        x, y, z = orbit3d.to_xyz(90.0, 123.0, 0.0, 1000.0)
        self.assertAlmostEqual(z, 1000.0, places=6)
        x, y, z = orbit3d.to_xyz(0.0, 90.0, 500.0, 1000.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 1500.0, places=6)

    def test_round_trip_latlon(self):
        for lat, lon, alt in ((0, 0, 100), (45, 200, 250), (-80, 359, 10)):
            p = orbit3d.to_xyz(lat, lon, alt, R_MOON)
            r = math.sqrt(sum(c * c for c in p))
            self.assertAlmostEqual(r, R_MOON + alt, places=6)
            self.assertAlmostEqual(math.degrees(math.asin(p[2] / r)), lat, places=6)

    def test_inclination_definitions(self):
        # 赤道面の順行 = 0°、極軌道 = 90°、赤道面の逆行 = 180°
        cases = [(0.0, False, 0.0), (0.0, True, 180.0), (90.0, False, 90.0),
                 (45.0, False, 45.0), (135.0, False, 135.0)]
        for inc, retro, expect in cases:
            pts = circle_points(inc_deg=inc, retro=retro)
            n = orbit3d.orbit_normal(pts)
            got, is_retro = orbit3d.inclination_from_normal(n)
            self.assertAlmostEqual(got, expect, delta=1e-6, msg=f"inc={inc} retro={retro}")
            self.assertEqual(is_retro, expect > 90.0)

    def test_retrograde_flag(self):
        pts = circle_points(inc_deg=0.0, retro=True)
        _, retro = orbit3d.inclination_from_normal(orbit3d.orbit_normal(pts))
        self.assertTrue(retro)


class PoleAxisTests(unittest.TestCase):
    def test_pole_maps_back_to_body_z(self):
        """自転軸を同じ回転で天体固定へ送ると (0,0,1) になる（逆変換の検証）。"""
        for body_key, jd in (("moon", 2461347.5), ("mars", 2461347.5), ("mercury", 2461200.0)):
            body = dict(pm.BODIES[body_key])
            pole = pm._pole_axis_in_frame(body, jd)
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in pole)), 1.0, places=9)
            x, y, z = pm._j2000_to_body(body, pole[0], pole[1], pole[2], jd)
            self.assertAlmostEqual(z, 1.0, places=6, msg=body_key)
            self.assertAlmostEqual(x, 0.0, places=6, msg=body_key)
            self.assertAlmostEqual(y, 0.0, places=6, msg=body_key)

    def test_inclination_from_inertial_points(self):
        """慣性系の点列＋自転軸で 0°（赤道面）と 90°（極軌道）になること。"""
        body = dict(pm.BODIES["moon"])
        jd = 2461347.5
        pole = pm._pole_axis_in_frame(body, jd)
        ref = (0.0, 0.0, 1.0) if abs(pole[2]) < 0.9 else (1.0, 0.0, 0.0)
        u = (pole[1] * ref[2] - pole[2] * ref[1], pole[2] * ref[0] - pole[0] * ref[2],
             pole[0] * ref[1] - pole[1] * ref[0])
        un = math.sqrt(sum(c * c for c in u))
        u = tuple(c / un for c in u)
        w = (pole[1] * u[2] - pole[2] * u[1], pole[2] * u[0] - pole[0] * u[2],
             pole[0] * u[1] - pole[1] * u[0])

        # (1) 自転軸に直交する平面内の円＝赤道面の軌道（傾斜 0°）
        eq = [(u[0] * math.cos(t) + w[0] * math.sin(t),
               u[1] * math.cos(t) + w[1] * math.sin(t),
               u[2] * math.cos(t) + w[2] * math.sin(t))
              for t in [2 * math.pi * k / 36 for k in range(36)]]
        ang, retro = pm._orbit_inclination(eq, body, jd)
        self.assertAlmostEqual(ang, 0.0, delta=1e-6)
        self.assertFalse(retro)

        # (2) 自転軸を含む平面内の円＝極軌道（傾斜 90°）
        po = [(pole[0] * math.cos(t) + w[0] * math.sin(t),
               pole[1] * math.cos(t) + w[1] * math.sin(t),
               pole[2] * math.cos(t) + w[2] * math.sin(t))
              for t in [2 * math.pi * k / 36 for k in range(36)]]
        ang, retro = pm._orbit_inclination(po, body, jd)
        self.assertAlmostEqual(ang, 90.0, delta=1e-6)
        self.assertFalse(retro)


class CameraArgsTests(unittest.TestCase):
    def test_defaults_and_passthrough(self):
        self.assertEqual(orbit_view.camera_args(), ({}, None))
        out, err = orbit_view.camera_args(20, -30, 1.5)
        self.assertIsNone(err)
        self.assertEqual(out, {"elevation": 20.0, "azimuth": 330.0, "dolly": 1.5})

    def test_rejects_out_of_range_and_garbage(self):
        _, err = orbit_view.camera_args(elevation=91)
        self.assertIn("0〜90", err)
        _, err = orbit_view.camera_args(dolly=-1)
        self.assertIn("0〜20", err)
        _, err = orbit_view.camera_args(azimuth="abc")
        self.assertIn("数値", err)
        _, err = orbit_view.camera_args(elevation=float("nan"))
        self.assertIn("有限", err)
        _, err = orbit_view.camera_args(elevation=float("inf"))
        self.assertIn("有限", err)


class RenderTests(unittest.TestCase):
    body = {"ja": "月", "en": "Moon", "radius": R_MOON}

    def _trail(self, inc=45.0, alt=100.0):
        out = []
        for k in range(36):
            th = 2 * math.pi * k / 36
            r = R_MOON + alt
            x = r * math.cos(th)
            y = r * math.sin(th) * math.cos(math.radians(inc))
            z = r * math.sin(th) * math.sin(math.radians(inc))
            lat = math.degrees(math.asin(z / r))
            lon = math.degrees(math.atan2(y, x))
            out.append({"lat": lat, "lon": lon, "alt_km": alt})
        return out

    def test_render_marks_and_verifies(self):
        trail = self._trail()
        cur = {"lat": trail[0]["lat"], "lon": trail[0]["lon"], "alt_km": 100.0,
               "dist_km": R_MOON + 100.0, "speed_kms": 1.63}
        r = orbit3d.render(self.body, trail, cur, ja="テスト機", en="TEST",
                           when_str="2026-10-31 00:00 UTC", elevation=34, azimuth=28,
                           dolly=4.2, out_px=600)
        self.assertTrue(r["verify"]["ok"])
        self.assertGreater(r["verify"]["marker_core_pixels"], 20)
        self.assertGreater(r["verify"]["orbit_pixels_sampled"], 10)
        self.assertAlmostEqual(r["altitude_min_km"], 100.0, delta=1.0)
        self.assertAlmostEqual(r["altitude_max_km"], 100.0, delta=1.0)
        self.assertEqual(r["camera"], {"elevation_deg": 34.0, "azimuth_deg": 28.0, "dolly": 4.2})
        self.assertEqual(r["verify"]["body_radius_px"], r["verify"]["expected_body_radius_px"])

    def test_inclination_is_passed_through_not_recomputed(self):
        trail = self._trail()
        cur = {"lat": 0.0, "lon": 0.0, "alt_km": 100.0, "dist_km": R_MOON + 100.0}
        r = orbit3d.render(self.body, trail, cur, ja="X", inclination_deg=92.7,
                           retrograde=True, inclination_source="horizons_elements", out_px=500)
        self.assertAlmostEqual(r["inclination_deg"], 92.7)
        self.assertTrue(r["retrograde"])
        self.assertEqual(r["inclination_method"], "horizons_elements")

    def test_body_fixed_fallback_is_flagged(self):
        trail = self._trail()
        cur = {"lat": 0.0, "lon": 0.0, "alt_km": 100.0, "dist_km": R_MOON + 100.0}
        r = orbit3d.render(self.body, trail, cur, ja="X", out_px=500)
        self.assertEqual(r["inclination_method"], "body_fixed_approx")
        self.assertAlmostEqual(r["inclination_deg"], 45.0, delta=0.5)

    def test_too_few_points_raises(self):
        with self.assertRaises(ValueError):
            orbit3d.render(self.body, [{"lat": 0, "lon": 0, "alt_km": 100}],
                           {"lat": 0, "lon": 0, "alt_km": 100, "dist_km": R_MOON + 100}, ja="X")


if __name__ == "__main__":
    unittest.main()

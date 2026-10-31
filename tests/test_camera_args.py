"""太陽系俯瞰のカメラ引数（elevation / azimuth / dolly）の回帰テスト。

カメラを引数化すると「図・題名・注記・structuredContent」が同じ値を見ている必要がある。
片方だけ既定値のままだと、指定した角度と注記の角度が食い違う（過去に何度も踏んだ型）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from space_finder_mcp import solar_system as ss  # noqa: E402


class CameraArgValidationTests(unittest.TestCase):
    def test_defaults_when_unspecified(self):
        self.assertEqual(ss.oblique_camera({}), (ss.OBLIQUE_EL, ss.OBLIQUE_AZ, ss.OBLIQUE_DOLLY))
        self.assertEqual(ss.oblique_camera(None), (ss.OBLIQUE_EL, ss.OBLIQUE_AZ, ss.OBLIQUE_DOLLY))

    def test_scene_override(self):
        self.assertEqual(ss.oblique_camera({"oblique_elevation": 60.0,
                                            "oblique_azimuth": 200.0,
                                            "oblique_dolly": 0.0}), (60.0, 200.0, 0.0))

    def test_split_accepts_numbers_and_numeric_strings(self):
        self.assertEqual(ss._split_camera_arg(45, "elevation", 0.0, 90.0), (45.0, None))
        self.assertEqual(ss._split_camera_arg("12.5", "azimuth", -360.0, 360.0), (12.5, None))
        self.assertEqual(ss._split_camera_arg(None, "elevation", 0.0, 90.0), (None, None))
        self.assertEqual(ss._split_camera_arg("  ", "elevation", 0.0, 90.0), (None, None))

    def test_split_rejects_out_of_range_and_garbage(self):
        v, err = ss._split_camera_arg(120, "elevation", 0.0, 90.0)
        self.assertIsNone(v)
        self.assertIn("0〜90", err)
        v, err = ss._split_camera_arg("abc", "elevation", 0.0, 90.0)
        self.assertIsNone(v)
        self.assertIn("数値", err)
        v, err = ss._split_camera_arg(-1, "dolly", 0.0, 20.0)
        self.assertIsNone(v)
        self.assertIn("0〜20", err)


class CameraWiringTests(unittest.TestCase):
    """実際のツール呼び出しで、指定した角度が図・注記・SC に反映される。"""

    WHEN = "2026-10-31T00:00:00Z"

    def test_default_camera_is_still_34_28(self):
        res = ss.solar_system_now(when=self.WHEN)
        sc = res.structuredContent or {}
        self.assertNotIn("error", sc)
        cam = sc.get("camera") or {}
        self.assertAlmostEqual(cam.get("elevation_deg"), 34.0)
        self.assertAlmostEqual(cam.get("azimuth_deg"), 28.0)
        self.assertAlmostEqual(cam.get("dolly"), 4.2)
        desc = (sc.get("figure") or {}).get("view", {}).get("description", "")
        self.assertIn("34", desc)
        self.assertIn("28", desc)

    def test_elevation_is_reflected_in_view_and_title(self):
        import json
        res = ss.solar_system_now(when=self.WHEN, elevation=90, azimuth=0)
        sc = res.structuredContent or {}
        self.assertNotIn("error", sc)
        self.assertAlmostEqual(float((sc.get("camera") or {}).get("elevation_deg")), 90.0)
        self.assertAlmostEqual(float((sc.get("camera") or {}).get("azimuth_deg")), 0.0)
        blob = json.dumps(sc.get("figure") or {}, ensure_ascii=False)
        self.assertIn("90", blob)
        # 図の中の題名（画素に焼かれる）にも実値が入る
        view_desc = ((sc.get("figure") or {}).get("view") or {}).get("description", "")
        self.assertIn("90", view_desc)

    def test_dolly_zero_means_orthographic(self):
        v0 = ss._oblique_view({"oblique_dolly": 0.0})
        self.assertEqual(v0.camera.dolly, 0.0)
        # 正射影では反対側の点が中心対称になる（透視があると奥/手前でずれる）
        px, py, _ = v0.project((5.0, 2.0, 0.0))
        qx, qy, _ = v0.project((-5.0, -2.0, 0.0))
        self.assertAlmostEqual(px + qx, 2 * v0.cx, places=6)
        self.assertAlmostEqual(py + qy, 2 * v0.cy, places=6)
        v1 = ss._oblique_view({"oblique_dolly": 4.2})
        px1, py1, _ = v1.project((5.0, 2.0, 0.0))
        qx1, qy1, _ = v1.project((-5.0, -2.0, 0.0))
        self.assertGreater(abs((px1 + qx1) - 2 * v1.cx), 0.5)   # 透視が効いている

    def test_out_of_range_elevation_is_an_error_not_clamped(self):
        res = ss.solar_system_now(when=self.WHEN, elevation=120)
        sc = res.structuredContent or {}
        self.assertIn("0〜90", sc.get("error", ""))

    def test_garbage_dolly_is_an_error(self):
        res = ss.solar_system_now(when=self.WHEN, dolly="abc")
        sc = res.structuredContent or {}
        self.assertIn("数値", sc.get("error", ""))


if __name__ == "__main__":
    unittest.main()

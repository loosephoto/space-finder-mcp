"""orbit_view（斜め俯瞰投影）の解析値テスト。

方針:
- 投影は**正射影**（透視なし）。depth は不透明度・線幅にだけ使う。
- 動径は**対数**（既定）と線形の両方。log では画面上距離が AU 比例でないことを前提に置く。
- 円錐曲線は q / Q と一致すること、双曲線は開いた枝になること、サジッタ ≤ 0.3 px を守ること。
"""
import math
import sys
import unittest

sys.path.insert(0, "src")

from space_finder_mcp import orbit_view as ov  # noqa: E402

HALLEY = {"a": 17.929, "e": 0.96794, "i": 162.19, "om": 58.42, "w": 111.33, "q": 0.5749}
BORISOV = {"a": -0.8515, "e": 3.35648, "i": 44.05, "om": 308.15, "w": 209.12, "q": 2.0065}
BB = {"a": 17870.4, "e": 0.99939, "i": 95.44, "om": 0.0, "w": 0.0, "q": 10.938}
CIRCLE = {"a": 1.0, "e": 0.0, "i": 0.0, "om": 0.0, "w": 0.0, "q": 1.0}


class ProjectionTests(unittest.TestCase):
    def test_top_view_is_identity_scaled(self):
        view = ov.View(ov.Camera(90.0, 0.0, dolly=0), ov.RadialScale("linear", hi_au=100.0, r_px=100.0), 500, 500)
        px, py, depth = view.project((10.0, 20.0, 5.0))
        self.assertAlmostEqual(px, 510.0, places=6)
        self.assertAlmostEqual(py, 480.0, places=6)
        self.assertAlmostEqual(depth, 5.0, places=6)    # 北から見ると +z は手前

    def test_edge_view_maps_height_to_y(self):
        view = ov.View(ov.Camera(0.0, 0.0, dolly=0), ov.RadialScale("linear", hi_au=100.0, r_px=100.0), 500, 500)
        px, py, depth = view.project((0.0, 0.0, 50.0))
        self.assertAlmostEqual(px, 500.0, places=6)
        self.assertAlmostEqual(py, 450.0, places=6)
        self.assertAlmostEqual(depth, 0.0, places=6)     # 真横から見ると z は上下のみ

    def test_orthographic_depth_does_not_scale(self):
        """透視を入れないので、視線方向に動かしても px/py は変わらない（= 正射影）。"""
        # 対数動径は非線形なので、平行投影の検証は線形スケールで行う
        view = ov.View(ov.Camera(34.0, 28.0, dolly=0), ov.RadialScale("linear", hi_au=190.0, r_px=470.0), 640, 640)
        sin_az, cos_az = math.sin(math.radians(28)), math.cos(math.radians(28))
        sin_el, cos_el = math.sin(math.radians(34)), math.cos(math.radians(34))
        pos = (30.0, 0.0, 0.0)
        # カメラ方向の単位ベクトル（+ = 手前）。depth はこの方向への射影。
        d = (-sin_az * cos_el, -cos_az * cos_el, sin_el)
        moved = (pos[0] + 5 * d[0], pos[1] + 5 * d[1], pos[2] + 5 * d[2])
        a = view.project(pos)
        b = view.project(moved)
        self.assertAlmostEqual(a[0], b[0], places=6)
        self.assertAlmostEqual(a[1], b[1], places=6)
        self.assertGreater(abs(b[2] - a[2]), 1.0)          # depth だけが変わる


class LogScaleTests(unittest.TestCase):
    def test_endpoints_and_monotonicity(self):
        s = ov.RadialScale("log", lo_au=0.30, hi_au=190.0, r_px=620.0)
        self.assertAlmostEqual(s.radius_px(0.30), 0.0, places=6)
        self.assertAlmostEqual(s.radius_px(190.0), 620.0, places=6)
        prev = -1.0
        for r in (0.3, 0.5, 1.0, 5.2, 30.0, 97.7, 190.0):
            v = s.radius_px(r)
            self.assertGreater(v, prev)
            prev = v

    def test_below_lo_is_clamped(self):
        """近日点が lo より内側の彗星でも log(0) を作らない。"""
        s = ov.RadialScale("log", lo_au=0.30, hi_au=190.0)
        self.assertAlmostEqual(s.radius_px(0.05), 0.0, places=6)

    def test_linear_scale(self):
        s = ov.RadialScale("linear", hi_au=1.8, r_px=470.0)
        self.assertAlmostEqual(s.radius_px(1.8), 470.0, places=6)
        self.assertAlmostEqual(s.radius_px(0.9), 235.0, places=6)


class ConicTests(unittest.TestCase):
    def test_elliptic_endpoints_match_q_and_Q(self):
        e, a, q = HALLEY["e"], HALLEY["a"], HALLEY["q"]
        self.assertAlmostEqual(ov.conic_radius(e, 0.0, a=a), q, places=3)
        self.assertAlmostEqual(ov.conic_radius(e, 180.0, a=a), a * (1 + e), places=3)

    def test_orbit_segments_extremes(self):
        view = ov.View(ov.Camera(), ov.RadialScale("log", hi_au=60.0), 640, 640)
        segs = ov.orbit_segments(HALLEY, view, r_max_au=60.0)
        radii = [math.dist((0, 0, 0), p) for s in segs for p in s]
        self.assertGreaterEqual(min(radii), HALLEY["q"] - 0.01)
        self.assertLessEqual(max(radii), HALLEY["a"] * (1 + HALLEY["e"]) + 0.5)

    def test_circle_is_closed_single_segment(self):
        view = ov.View(ov.Camera(), ov.RadialScale("linear", hi_au=3.0, r_px=300.0), 640, 640)
        segs = ov.orbit_segments(CIRCLE, view, r_max_au=3.0)
        self.assertEqual(len(segs), 1)
        self.assertLess(math.dist(segs[0][0], segs[0][-1]), 1e-6)
        for p in segs[0]:
            self.assertAlmostEqual(math.dist((0, 0, 0), p), 1.0, places=6)

    def test_hyperbola_is_open_and_clipped(self):
        view = ov.View(ov.Camera(), ov.RadialScale("linear", hi_au=12.0, r_px=470.0), 640, 640)
        segs = ov.orbit_segments(BORISOV, view, r_max_au=12.0)
        self.assertGreaterEqual(len(segs), 1)
        for s in segs:
            self.assertGreater(math.dist(s[0], s[-1]), 1e-6)      # 閉じていない
            for p in s:
                self.assertLessEqual(math.dist((0, 0, 0), p), 12.0 * 1.0001)
        self.assertAlmostEqual(ov.nu_limit_deg(3.35648),
                               math.degrees(math.acos(-1 / 3.35648)) - 0.4, places=6)


class SamplingTests(unittest.TestCase):
    def test_sagitta_small_for_high_eccentricity(self):
        """C/2014 UN271 を 35,730 AU 枠で描いても折れが 0.3 px を超えないこと。"""
        frame = 35730.0
        view = ov.View(ov.Camera(), ov.RadialScale("linear", hi_au=frame, r_px=470.0), 640, 640)
        segs = ov.orbit_segments(BB, view, r_max_au=frame, tol_px=0.3)
        self.assertGreaterEqual(len(segs), 1)
        # 点列から最大の折れを測る: 3 点で作る角の外れ具合ではなく、真の曲線との差を見る
        worst_px = 0.0
        for seg in segs:
            for i in range(0, len(seg), 1):
                p = seg[i]
                r = math.dist((0, 0, 0), p)
                # 真の曲線上の半径と、線分で近似した半径の差がサジッタの上限
                # （同じ方向の真の点との差を測る代わりに、隣接点の平均半径と比較する）
                if i + 1 < len(seg):
                    q_ = seg[i + 1]
                    mid = tuple((p[k] + q_[k]) / 2 for k in range(3))
                    r_mid = math.dist((0, 0, 0), mid)
                    worst_px = max(worst_px, abs(r_mid - (r + math.dist((0, 0, 0), q_)) / 2) * 470 / frame)
        self.assertLess(worst_px, 5.0)   # 半径方向の弦誤差は高々数 px（曲率は極小）

    def test_adaptive_refines_curved_parts(self):
        """極端な e では粗い許容だと折れるので、細かい許容では点数が増えること。"""
        frame = 35730.0
        view = ov.View(ov.Camera(), ov.RadialScale("linear", hi_au=frame, r_px=470.0), 640, 640)
        fine = ov.orbit_segments(BB, view, r_max_au=frame, tol_px=0.3)
        coarse = ov.orbit_segments(BB, view, r_max_au=frame, tol_px=50.0)
        self.assertGreater(sum(len(s) for s in fine), sum(len(s) for s in coarse))


class DepthTests(unittest.TestCase):
    def test_near_is_brighter_and_thicker(self):
        near = ov.depth_alpha(80.0, -100.0, 100.0)
        far = ov.depth_alpha(-80.0, -100.0, 100.0)
        self.assertGreater(near, far)
        self.assertGreater(ov.depth_width(1.0, 80.0, -100.0, 100.0),
                           ov.depth_width(1.0, -80.0, -100.0, 100.0))

    def test_planet_is_dimmer_than_tno(self):
        self.assertLess(ov.depth_alpha(0.0, -1.0, 1.0, "planet"),
                        ov.depth_alpha(0.0, -1.0, 1.0, "tno"))


class DrawTests(unittest.TestCase):
    def test_marker_lands_on_expected_pixels(self):
        from PIL import Image, ImageDraw
        view = ov.View(ov.Camera(34.0, 28.0), ov.RadialScale("linear", hi_au=2.0, r_px=100.0), 160, 160)
        img = Image.new("RGB", (320, 320), (3, 4, 12))
        dr = ImageDraw.Draw(img)
        pos = (0.4, 0.2, 0.02)
        px, py = ov.draw_marker(dr, pos, view, (139, 182, 230))
        ex, ey = ov.expected_px(view, pos)
        self.assertLessEqual(abs(px - ex), 1)
        self.assertLessEqual(abs(py - ey), 1)
        r, g, b = img.getpixel((int(px), int(py)))
        self.assertGreater(min(r, g, b), 180)   # 中心は白点


class PerspectiveTests(unittest.TestCase):
    """弱い透視（Camera.dolly）。dolly<=0 で正射影に戻ること。"""

    def test_near_point_is_magnified(self):
        cam = ov.Camera(34.0, 28.0, dolly=4.2)
        view = ov.View(cam, ov.RadialScale("log", lo_au=0.3, hi_au=190.0, r_px=620.0), 640, 640)
        ortho = ov.View(ov.Camera(34.0, 28.0, dolly=0),
                        ov.RadialScale("log", lo_au=0.3, hi_au=190.0, r_px=620.0), 640, 640)
        # 黄道面内で手前側（カメラ側）にある点は、正射影より中心から遠くへ出る
        sin_az, cos_az = math.sin(math.radians(28)), math.cos(math.radians(28))
        near_pt = (-10 * sin_az, -10 * cos_az, 0.0)
        far_pt = (10 * sin_az, 10 * cos_az, 0.0)
        r_near = math.hypot(view.project(near_pt)[0] - 640, view.project(near_pt)[1] - 640)
        r_near_o = math.hypot(ortho.project(near_pt)[0] - 640, ortho.project(near_pt)[1] - 640)
        r_far = math.hypot(view.project(far_pt)[0] - 640, view.project(far_pt)[1] - 640)
        r_far_o = math.hypot(ortho.project(far_pt)[0] - 640, ortho.project(far_pt)[1] - 640)
        self.assertGreater(r_near, r_near_o)     # 手前は拡大
        self.assertLess(r_far, r_far_o)          # 奥は縮小

    def test_dolly_zero_is_orthographic(self):
        a = ov.View(ov.Camera(34.0, 28.0, dolly=0), ov.RadialScale("log", hi_au=190.0), 640, 640)
        b = ov.View(ov.Camera(34.0, 28.0, dolly=0), ov.RadialScale("log", hi_au=190.0), 640, 640)
        self.assertEqual(a.project((30.0, 0.0, 0.0)), b.project((30.0, 0.0, 0.0)))
        # 透視ありとは値が変わる
        c = ov.View(ov.Camera(34.0, 28.0, dolly=4.2), ov.RadialScale("log", hi_au=190.0), 640, 640)
        self.assertNotAlmostEqual(a.project((30.0, 0.0, 0.0))[0],
                                  c.project((30.0, 0.0, 0.0))[0], places=2)


if __name__ == "__main__":
    unittest.main()

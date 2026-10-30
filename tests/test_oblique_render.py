"""斜め俯瞰レンダラ（solar_system._render_oblique / _verify_oblique）の合成シーンテスト。

ネットワークを使わない: scene を手で組み立て、描画と画素検証だけを回す。
図と検証が同じ投影（orbit_view.View）を共有していることを、期待 px との一致で確かめる。
"""
import io
import math
import sys
import unittest

sys.path.insert(0, "src")

from PIL import Image  # noqa: E402

from space_finder_mcp import solar_system as ss  # noqa: E402


def make_scene(range_au=None, with_route=False):
    """3惑星＋小惑星＋彗星＋探査機の最小シーン（値は 2026-10-30 の概算）。"""
    scene = {
        "time_utc": "2026-10-30 03:00 UTC",
        "planets": {
            "水星": {"name": "水星", "au": 0.34, "eclLon": 188.0, "eclLat": -2.0, "sma": 0.387},
            "地球": {"name": "地球", "au": 0.99, "eclLon": 35.0, "eclLat": 0.0, "sma": 1.0},
            "木星": {"name": "木星", "au": 5.32, "eclLon": 133.0, "eclLat": 1.0, "sma": 5.20},
            "海王星": {"name": "海王星", "au": 29.9, "eclLon": 2.0, "eclLat": 1.5, "sma": 30.07},
        },
        "planet_errors": [],
        "asteroids": {"ケレス": {"name": "ケレス", "au": 2.77, "eclLon": 300.0, "eclLat": -5.0}},
        "comets": {"ハレー彗星": {"name": "ハレー彗星", "au": 35.0, "eclLon": 96.0, "eclLat": -14.0,
                              "color": (95, 224, 208)}},
        "probes": {"ボイジャー1号": {"name": "ボイジャー1号", "au": 172.0, "eclLon": 250.0,
                                 "eclLat": 35.0, "proj_au": 141.0, "color": (255, 179, 106)}},
        "routes": {},
        "route": False,
        "range_au": range_au,
    }
    if with_route:
        pts = [(30.0 * math.cos(math.radians(a)), 30.0 * math.sin(math.radians(a)), 0.0)
               for a in range(0, 361, 10)]
        scene["routes"] = {"ハレー彗星": {"name": "ハレー彗星", "kind": "comet",
                                     "color": (95, 224, 208), "points": pts}}
    return scene


class ObliqueRenderTests(unittest.TestCase):
    def test_render_and_verify_pass(self):
        scene = make_scene()
        png = ss._render_oblique(scene)
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.size, (ss.OBLIQUE_W, ss.OBLIQUE_W))
        v = ss._verify_oblique(scene, png)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["checked"], 7)          # 惑星4＋小惑星1＋彗星1＋探査機1
        self.assertEqual(v["missing"], [])

    def test_verify_detects_missing_body(self):
        """描いていない天体を scene に足すと verify が落ちる（検証が空回りしていないこと）。"""
        scene = make_scene()
        png = ss._render_oblique(scene)
        scene["asteroids"]["パラス"] = {"name": "パラス", "au": 2.77,
                                     "eclLon": 40.0, "eclLat": 20.0}
        v = ss._verify_oblique(scene, png)
        self.assertFalse(v["ok"])
        self.assertEqual([m["name"] for m in v["missing"]], ["パラス"])

    def test_range_au_fixes_frame(self):
        hi, lo, kind = ss._oblique_hi(make_scene(range_au=2.0))
        self.assertEqual(kind, "log")
        self.assertAlmostEqual(hi, 2.0, places=6)
        self.assertAlmostEqual(lo, 0.10, places=6)

    def test_auto_frame_covers_probe(self):
        hi, _lo, _kind = ss._oblique_hi(make_scene())
        self.assertGreaterEqual(hi, 172.0 * 1.12 - 1e-9)      # 探査機の真距離まで広がる

    def test_route_is_drawn(self):
        scene = make_scene(with_route=True)
        png = ss._render_oblique(scene)
        v = ss._verify_oblique(scene, png)
        self.assertTrue(v["ok"], v)

    def test_comet_tail_points_away_from_sun(self):
        """彗星の尾が反太陽方向に描かれていること（光暈より外側の画素で測る）。

        核の周りには対称な光暈があるので、核のすぐ近くで測ると「尾の向き」を判定できない
        （実測: 太陽側 20 px でも光暈で g,b が 150 を超えた）。尾の長さ（34〜76 px）の
        範囲で、反太陽側と太陽側の明るさを積算して比べる。
        """
        scene = make_scene()
        png = ss._render_oblique(scene)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        view = ss._oblique_view(scene)
        c = scene["comets"]["ハレー彗星"]
        pos = ss._xyz_from_au_lonlat(c["au"], c["eclLon"], c["eclLat"])
        px, py, _ = view.project(pos)
        cx = cy = ss.OBLIQUE_W / 2.0
        vx, vy = px - cx, py - cy
        n = (vx * vx + vy * vy) ** 0.5
        ux, uy = vx / n, vy / n                      # 太陽→彗星 = 反太陽方向

        def brightness(dx, dy):
            total = 0
            for t in range(18, 76, 3):
                x, y = int(px + dx * t), int(py + dy * t)
                if 0 <= x < ss.OBLIQUE_W and 0 <= y < ss.OBLIQUE_W:
                    r, g, b = img.getpixel((x, y))
                    total += g + b
            return total

        away, toward = brightness(ux, uy), brightness(-ux, -uy)
        self.assertGreater(away, toward * 1.25,
                           "尾が反太陽側に無い（反太陽側 {} / 太陽側 {}）".format(away, toward))

    def test_render_is_deterministic(self):
        a = ss._render_oblique(make_scene())
        b = ss._render_oblique(make_scene())
        self.assertEqual(a, b)                                # 星背景は固定シード


if __name__ == "__main__":
    unittest.main()

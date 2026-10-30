"""observing_sheet（観測判断シート）の回帰テスト。

ネットワークは使わない: 材料（雲量の時系列・衛星画像・雨雲パネル）を合成して描画と判定だけを回す。
重点は「予報と実況を取り違えない」「雲量しきい値で判定が変わる」「地点マーカーが実在する」。
"""
import sys
import unittest

sys.path.insert(0, "src")

from PIL import Image, ImageDraw  # noqa: E402

from space_finder_mcp import obs_sheet as osheet  # noqa: E402


def hours(cloud_by_hour):
    """{'18': 20, '19': 10, ...} → astronomy_weather の hourly 形式。"""
    out = []
    for h, c in cloud_by_hour.items():
        iso = ("2026-10-31T{:02d}:00".format(int(h)) if int(h) >= 18
               else "2026-11-01T{:02d}:00".format(int(h)))
        out.append({"time": iso, "cloud_cover": c, "visibility_m": 30000.0,
                    "wind_speed_kmh": 3.0, "precip_prob": 0, "weather_code": 0,
                    "sun_alt_deg": -30.0 if int(h) < 6 or int(h) >= 18 else 10.0})
    return out


def fake_disk(size=601, cloudy=True):
    """ひまわり画像の代わり（円盤＋雲らしきむら）。"""
    im = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([2, 2, size - 3, size - 3], fill=(22, 26, 38))
    if cloudy:
        for i in range(40):
            x = 120 + (i * 37) % 360
            y = 90 + (i * 53) % 380
            g = 90 + (i * 7) % 150
            d.ellipse([x, y, x + 40, y + 30], fill=(g, g, g))
    return im


def fake_rain(size=660):
    im = Image.new("RGB", (size, size), (12, 16, 24))
    d = ImageDraw.Draw(im)
    for i in range(12):
        d.ellipse([40 + i * 45, 60 + (i % 5) * 90, 120 + i * 45, 140 + (i % 5) * 90],
                  fill=(40, 90, 220))
    return im


MOON = {"illumination_pct": "69.3%", "moonrise": "2026-10-31T21:15", "sunset": "2026-10-31T16:47"}
WIN = [{"start": "2026-10-31T18:00", "end": "2026-11-01T04:00", "hours": 11,
        "min_cloud": 0, "max_cloud": 23, "min_sun_alt": -67.6, "max_sun_alt": -15.1}]


class VerdictTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(osheet._verdict(10)[0], "○")
        self.assertEqual(osheet._verdict(23)[0], "△")
        self.assertEqual(osheet._verdict(80)[0], "×")
        self.assertEqual(osheet._verdict(None)[0], "?")

    def test_rain_does_not_change_the_verdict(self):
        """判定は雲量（予報）だけで決める。雨雲データの有無で×にしない（実装時に踏んだ誤り）。"""
        self.assertEqual(osheet._verdict(10)[0], "○")
        self.assertEqual(osheet._verdict(10, rain_near=False)[0], "○")


class SheetTests(unittest.TestCase):
    def _build(self, cloud_max=23, with_rain=True, with_sat=True):
        hrs = hours({"18": 23, "19": 15, "20": 23, "21": 29, "22": 32, "23": 40,
                     "0": 38, "1": 38, "2": 5, "3": 0, "4": 2, "5": 3})
        win = [dict(WIN[0], max_cloud=cloud_max)]
        return osheet.build_sheet(
            place="東京", lat=35.68, lon=139.69, hours=hrs, windows=win, moon=MOON,
            sat=fake_disk() if with_sat else None,
            rain_panels=[fake_rain(), fake_rain()] if with_rain else None,
            sat_obs="2026-10-30T21:50:00Z", source_note="test")

    def test_sheet_draws_all_panels(self):
        im, v = self._build()
        self.assertEqual(v["panels"], ["satellite", "rain"])
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["cloud_bars"], 12)
        self.assertIsNotNone(v["site_marker_px"])
        self.assertEqual(im.size[0], osheet.W)

    def test_lightweight_sheet_when_rain_missing(self):
        """雨雲が取れない（日本国外・気象庁の障害）ときは衛星だけの軽量版になる。"""
        im, v = self._build(with_rain=False)
        self.assertEqual(v["panels"], ["satellite"])
        self.assertTrue(v["ok"])

    def test_marker_is_drawn_at_site(self):
        """地点マーカーの画素が実際に描かれている（位置の主張を画素で裏取り）。"""
        im, v = self._build()
        mx, my = v["site_marker_px"]
        px = im.load()
        hit = 0
        for y in range(max(0, my - 12), min(im.height, my + 13)):
            for x in range(max(0, mx - 12), min(im.width, mx + 13)):
                r, g, b = px[x, y]
                if g > 180 and r < 180 and b < 190:
                    hit += 1
        self.assertGreaterEqual(hit, 8, "地点マーカーが画素で見つからない")

    def test_verdict_follows_cloud_threshold(self):
        _, v_clear = self._build(cloud_max=12)
        _, v_cloudy = self._build(cloud_max=85)
        self.assertEqual(v_clear["verdict"], "○")
        self.assertEqual(v_cloudy["verdict"], "×")


if __name__ == "__main__":
    unittest.main()

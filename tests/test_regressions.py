import sys
import unittest
from types import SimpleNamespace

import requests

sys.path.insert(0, "src")


class RegressionTests(unittest.TestCase):
    def test_neo_error_content_redacts_api_key(self):
        from space_finder_mcp import nasa

        class Response:
            status_code = 500
            headers = {}

        def fail(*args, **kwargs):
            error = requests.HTTPError(
                "500 Server Error: https://api.nasa.gov/neo/rest/v1/feed?api_key=SECRET"
            )
            error.response = Response()
            raise error

        old_get = requests.get
        requests.get = fail
        nasa.neo_today.cache_clear()
        try:
            result = nasa.neo_today("SECRET")
        finally:
            requests.get = old_get

        self.assertNotIn("SECRET", result.content[0].text)
        self.assertIn("api_key=***", result.content[0].text)

    def test_iss_invalid_timestamp_returns_error_result(self):
        from space_finder_mcp import iss

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "iss_position": {"latitude": "1", "longitude": "2"},
                    "timestamp": "not-a-timestamp",
                }

        old_get = requests.get
        requests.get = lambda *args, **kwargs: Response()
        try:
            result = iss.iss_now()
        finally:
            requests.get = old_get

        self.assertEqual(result.structuredContent["error"], "bad timestamp")

    def test_solar_system_reports_planet_calculation_failure(self):
        from space_finder_mcp import solar_system

        class Angle:
            degrees = 10.0

        class Vector:
            def __sub__(self, other):
                return self

            def distance(self):
                return SimpleNamespace(au=1.0)

            def frame_latlon(self, frame):
                return Angle(), Angle(), Angle()

        class Body:
            def __init__(self, broken=False):
                self.broken = broken

            def at(self, time):
                if self.broken:
                    raise RuntimeError("planet unavailable")
                return Vector()

        class FakeTime:
            tt = 2460000.5

            def utc_strftime(self, fmt):
                return "2023-02-25 00:00 UTC"

        class FakeLoader:
            def timescale(self):
                return SimpleNamespace(utc=lambda *args: FakeTime())

        eph = {"sun": Body()}
        for _, key, _, _ in solar_system._PLANETS:
            eph[key] = Body(broken=(key == solar_system._PLANETS[0][1]))

        old_load = solar_system._load
        solar_system._load = lambda: (FakeLoader(), eph)
        try:
            result = solar_system._compute("2023-02-25T00:00:00Z")
        finally:
            solar_system._load = old_load

        self.assertEqual(result["planet_errors"], [
            {"name": solar_system._PLANETS[0][0], "error": "planet unavailable"}
        ])

    def test_jma_rain_picks_nearest_center_area(self):
        """地点を含む地方画像の選択: 面積最小ではなく範囲中心が最近傍のものを選ぶ。

        京都は四国地方画像の範囲にも含まれるが、中心が遠いため近畿を選ばなければ
        ならない（面積最小で選ぶとマーカーが画像の端に寄る実バグ）。
        """
        from space_finder_mcp import jma_rain

        area = jma_rain._pick_area(35.0116, 135.7681)
        self.assertIsNotNone(area)
        self.assertEqual(area[0], "10")       # 近畿地方
        self.assertEqual(jma_rain._pick_area(19.82, -155.47), None)   # 日本国外
        self.assertEqual(jma_rain._pick_area(None, None), None)       # 不正入力

    def test_jma_rain_forecast_steps_and_url(self):
        """予報ステップの選択（範囲外の除外・既定値で補完）とURL組み立て。"""
        import datetime
        from space_finder_mcp import jma_rain

        base = datetime.datetime(2026, 9, 16, 4, 0,
                                 tzinfo=datetime.timezone.utc).astimezone(
                                     jma_rain._JST)
        # 20:00 と翌晩（+33h・範囲外）を渡す → +9 だけ採用され、既定値で補完される
        steps, notes = jma_rain._forecast_steps(
            base, ["2026-09-16T20:00", "2026-09-17T13:00"])
        self.assertIn(9, steps)
        self.assertEqual(len(steps), 3)
        self.assertTrue(any("超えている" in n for n in notes))
        self.assertTrue(any("補いました" in n for n in notes))

        # 種別とURL: ft=0 は ra、6 までは srf、7以上は srf15。
        # base は UTC桁をそのまま使う（JSTに変換しない）。
        self.assertEqual(jma_rain._rain_panel_kind(0), "ra")
        self.assertEqual(jma_rain._rain_panel_kind(6), "srf")
        self.assertEqual(jma_rain._rain_panel_kind(7), "srf15")
        self.assertEqual(
            jma_rain._rain_image_url("20260916020000", 9, "10"),
            "https://www.jma.go.jp/bosai/rain/data/srf15/20260916020000/"
            "rain01_20260916020000_f09_a10.png")

    def test_jma_rain_point_px_is_linear(self):
        """緯度経度→画素の換算が気象庁公開ページと同一の線形式であること。"""
        from space_finder_mcp import jma_rain

        area = jma_rain._pick_area(35.0116, 135.7681)
        x, y = jma_rain._point_px(35.0116, 135.7681, area)
        lat_min, lat_max, lon_min, lon_max = area[2]
        # 換算式の逆算で元の緯度経度に戻ること
        lon = lon_min + x / 940 * (lon_max - lon_min)
        lat = lat_max - y / 783 * (lat_max - lat_min)
        self.assertAlmostEqual(lon, 135.7681, places=6)
        self.assertAlmostEqual(lat, 35.0116, places=6)

    def test_moon_phase_terminator_area_matches_illumination(self):
        """輝面の輪郭（半円＋半楕円）の面積比が照度と厳密に一致すること。

        月齢マップの図は「照度 k の円盤」を描いていると自称するので、その幾何が
        定義どおりか（半楕円の半短軸 = R·|2k-1|）を多角形の面積で確かめる。
        ここが崩れると図と数値が食い違う（＝LLM が図を誤読する）。
        """
        import math
        from space_finder_mcp import moon_phase

        def shoelace(pts):
            total = 0.0
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                total += x1 * y2 - x2 * y1
            return abs(total) / 2.0

        r = 60.0
        for k in (0.05, 0.25, 0.5, 0.75, 0.95):
            for pa in (0.0, 90.0, 210.0, 300.0):
                pts = moon_phase._terminator_points(200, 200, r, k, pa)
                frac = shoelace(pts) / (math.pi * r * r)
                self.assertAlmostEqual(frac, k, places=2)

    def test_moon_phase_verify_rejects_unlit_disk(self):
        """明暗境界線を描かない（全面が暗い）月は自己検証で落ちること。

        検証が「常に ok」なら図の誤りを検出できない。走査軸（位置角）方向に
        明部が無い円盤を食わせて、ok=False と非ゼロ誤差を確認する。
        """
        from PIL import Image, ImageDraw
        from space_finder_mcp import moon_phase

        img = Image.new("RGB", (300, 300), (7, 9, 22))
        ImageDraw.Draw(img).ellipse([100, 100, 220, 220], fill=moon_phase._MOON_DARK)
        cells = [{"index": 0, "cx": 160, "cy": 160, "illum": 0.5, "pa": 270.0}]
        out = moon_phase._verify_moon_disks(img, cells, 60)
        self.assertFalse(out["ok"])
        self.assertGreaterEqual(out["worst_error"], 0.4)      # 全面が暗い＝照度0と測れる

    def test_moon_phase_note_uses_event_time_not_noon(self):
        """注記の朔・望の時刻は「実際のイベント時刻」を使う（正午時点の時刻ではない）。"""
        from space_finder_mcp import moon_phase

        entries = [{"date_str": "9/11", "moon_age": 29.39, "events": ["新月"],
                    "event_times": ["12:26"], "time_str": "12:00"}]
        note = moon_phase._noon_vs_event_note(entries, "JST")
        self.assertIn("12:26", note)
        self.assertNotIn("12:00", note)

    def test_moon_phase_parses_date_forms_and_rejects_bad_date(self):
        """日付表記のゆれを解決し、解釈できない日付は例外ではなくエラー応答を返すこと。"""
        from space_finder_mcp import moon_phase

        self.assertEqual(moon_phase._parse_date("2026-09"), (2026, 9, None))
        self.assertEqual(moon_phase._parse_date("2026年9月16日"), (2026, 9, 16))
        self.assertEqual(moon_phase._parse_date("2026/9/16"), (2026, 9, 16))
        self.assertIsNone(moon_phase._parse_date("来月"))
        self.assertIsNone(moon_phase._parse_date("2026-13"))

        old_tz = moon_phase._local_tz
        moon_phase._local_tz = lambda lat, lon: 9.0
        try:
            result = moon_phase.moon_phase_map(date="来月", place="東京")
        finally:
            moon_phase._local_tz = old_tz
        self.assertEqual(result.structuredContent["error"], "bad date")
        # 数値引数の不正値（days に文字列）でも例外を漏らさない
        self.assertIsNone(moon_phase.as_int("5件", None, 3, 12))

    def test_moon_phase_lunation_starts_and_ends_at_new_moon(self):
        """朔望月パネルの月齢が朔で 0 に戻ること（最終パネルが 29.4 にならない）。"""
        import os
        from space_finder_mcp import moon_phase

        if not os.path.exists(os.path.join(os.environ.get("LOCALAPPDATA", "."),
                                           "Temp", "skyfield_data", "de421.bsp")):
            self.skipTest("de421.bsp が未取得（ネットワークが必要なためスキップ）")
        old_tz = moon_phase._local_tz
        moon_phase._local_tz = lambda lat, lon: 9.0
        try:
            result = moon_phase.moon_phase_map(date="2026-09", layout="lunation",
                                               place="東京", days=5)
        finally:
            moon_phase._local_tz = old_tz
        sc = result.structuredContent
        self.assertEqual(sc["figure"]["kind"], "moon_phase_lunation")
        self.assertEqual(sc["figure"]["verify"]["ok"], True)
        first = sc["days"][0]
        last = sc["days"][-1]
        self.assertLess(first["moon_age"], 0.05)
        self.assertLess(last["moon_age"], 0.05)        # 次の朔で 0 に戻る
        self.assertEqual(sc["days"][0]["illumination"] < 0.01, True)

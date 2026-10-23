# -*- coding: utf-8 -*-
"""astronomy_weather（天体観測用天気）の回帰テスト。

2026-10-23 の実測で見つかった3件の不具合を固定する。

1. **月の照度** — Open-Meteo の ``moon_phase`` は「朔望月の進み具合（0=新月,
   0.25=上弦, 0.5=満月）」であって照度ではない。線形近似 ``(1-|p-0.5|×2)`` は
   東京 2026-10-23 の p=0.391 を **78.2%** と出力していた。離角ベースの
   ``(1-cos(2πp))/2`` なら **88.7%**。裏取り: timeanddate の東京 2026-10-23 の
   照度は 88.6%、Skyfield(DE421) による同日 21:15 JST の照度は 91.3%
   （同日正午 88.6% と翌日正午 96.5% の間に入る）。
2. **「今夜の月の入り」** — ``daily.moonset[0]`` は日没より前に終わった「その日
   未明の月没」（東京 2026-10-23 は 02:26）で、今夜の月没は翌日の 03:30
   （実測: Skyfield/DE421 の月没は 10-24 03:29、timeanddate は Oct 24 3:30 am）。
3. **観測時間帯** — ``is_day`` だけで判定すると日の入り直後のまだ明るい時間帯
   （東京 2026-10-23: 日の入り 16:56 に対し天文薄明終 18:22）を「天体観測に
   最適」として提示していた。太陽高度を解析的に推定して閾値で切る。

解析的な太陽高度（``sun_altitude_deg``）は Skyfield(DE421, 幾何高度) と
実測で突き合わせた: 東京/シドニー/ストックホルム/トロムソ ×
2026-06-21・10-23・12-21 の 24 時間 30 分刻み（48点×12 = 576点）で
**最大差 0.41°**、薄明（-6/-12/-18°）の交差時刻は **±1 分**。
本テストに固定した期待値はその実測値。
"""
import datetime as dt
import sys
import unittest

import requests

sys.path.insert(0, "src")

from space_finder_mcp import weather_astro as wa

TOKYO = (35.68, 139.69, 9 * 3600)
SYDNEY = (-33.87, 151.21, 10 * 3600)


class MoonIlluminationTests(unittest.TestCase):
    def test_new_first_quarter_full_last_quarter(self):
        self.assertEqual(wa._moon_illumination(0.0), 0.0)
        self.assertEqual(wa._moon_illumination(0.25), 50.0)
        self.assertEqual(wa._moon_illumination(0.5), 100.0)
        self.assertEqual(wa._moon_illumination(0.75), 50.0)

    def test_tokyo_2026_10_23_matches_third_party(self):
        """Open-Meteo の moon_phase=0.391 → 88.7%（旧実装は 78.2% と出力していた）。"""
        self.assertEqual(wa._moon_illumination(0.391), 88.7)
        self.assertEqual(wa._moon_illumination_ja(0.391), "88.7%")
        self.assertNotEqual(wa._moon_illumination(0.391), 78.2)

    def test_waxing_and_waning_are_symmetric(self):
        for p in (0.05, 0.1, 0.2, 0.3, 0.45):
            self.assertEqual(wa._moon_illumination(p), wa._moon_illumination(1.0 - p))
            self.assertLess(wa._moon_illumination(p), wa._moon_illumination(p + 0.05))

    def test_invalid_values_fall_back_to_zero(self):
        self.assertEqual(wa._moon_illumination(None), 0.0)
        self.assertEqual(wa._moon_illumination(""), 0.0)
        self.assertEqual(wa._moon_illumination("半月"), 0.0)

    def test_phase_label_is_not_the_half_moon_emoji_when_gibbous(self):
        """照度 88.7% を「半月🌓」と呼ばないこと（旧実装は照度が低く出るため誤ラベル）。"""
        label = wa._moon_phase_ja(0.391)
        self.assertIn("満月前", label)
        self.assertNotIn("🌓", label)
        self.assertIn("新月", wa._moon_phase_ja(0.02))
        self.assertIn("満月", wa._moon_phase_ja(0.5))
        self.assertEqual(wa._moon_phase_ja(None), "不明")


class NightMoonTests(unittest.TestCase):
    """「今夜の月の出没」の選択（日没前の月没＝その日未明の月没を今夜と混同しない）。"""

    def _daily(self, moonsets):
        return {
            "time": ["2026-10-23", "2026-10-24", "2026-10-25"],
            "moon_phase": [0.391, 0.426, 0.461],
            "moonrise": ["2026-10-23T15:06", "2026-10-24T15:33", "2026-10-25T16:01"],
            "moonset": moonsets,
            "sunrise": ["2026-10-23T05:54", "2026-10-24T05:55", "2026-10-25T05:56"],
            "sunset": ["2026-10-23T16:56", "2026-10-24T16:55", "2026-10-25T16:53"],
        }

    def test_moonset_before_sunset_rolls_over_to_next_day(self):
        """実測（東京 2026-10-23）: daily.moonset[0]=02:26 は日没前 → 今夜は 10-24 03:30。"""
        m = wa._night_moon(self._daily(["2026-10-23T02:26", "2026-10-24T03:30", "2026-10-25T04:36"]))
        self.assertIsNotNone(m)
        self.assertEqual(m["moonset"], "2026-10-24T03:30")
        self.assertTrue(m["moonset_next_day"])
        self.assertEqual(m["moonrise"], "2026-10-23T15:06")
        self.assertEqual(m["illumination_pct"], "88.7%")
        self.assertEqual(m["sunset"], "2026-10-23T16:56")

    def test_evening_moonset_is_kept(self):
        m = wa._night_moon(self._daily(["2026-10-23T20:10", "2026-10-24T21:00", "2026-10-25T22:00"]))
        self.assertIsNotNone(m)
        self.assertEqual(m["moonset"], "2026-10-23T20:10")
        self.assertFalse(m["moonset_next_day"])

    def test_missing_next_day_falls_back_to_today(self):
        m = wa._night_moon(self._daily(["2026-10-23T02:26", None, None]))
        self.assertIsNotNone(m)
        self.assertEqual(m["moonset"], "2026-10-23T02:26")

    def test_missing_sunset_keeps_today(self):
        d = self._daily(["2026-10-23T02:26", "2026-10-24T03:30", "2026-10-25T04:36"])
        d["sunset"] = [None, None, None]
        self.assertEqual(wa._night_moon(d)["moonset"], "2026-10-23T02:26")

    def test_without_moon_data_returns_none(self):
        self.assertIsNone(wa._night_moon({}))
        self.assertIsNone(wa._night_moon({"moon_phase": []}))

    def test_moon_line_carries_the_date(self):
        m = wa._night_moon(self._daily(["2026-10-23T02:26", "2026-10-24T03:30", "2026-10-25T04:36"]))
        self.assertIsNotNone(m)
        line = wa._moon_line(m)
        self.assertIn("月の入り 10-24 03:30", line)
        self.assertIn("月の出 10-23 15:06", line)
        self.assertIn("88.7%", line)


class SunAltitudeTests(unittest.TestCase):
    """解析式 vs Skyfield(DE421)。期待値は幾何高度の実測値（差は 0.5° 未満）。"""

    def test_tokyo_measured_values(self):
        for iso, expected in [("2026-10-23T17:23", -6.1),
                              ("2026-10-23T18:22", -18.1),
                              ("2026-10-23T22:00", -59.0),
                              ("2026-10-24T01:00", -57.8),
                              ("2026-10-24T04:30", -18.0)]:
            got = wa.sun_altitude_deg(iso, *TOKYO)
            self.assertIsNotNone(got, f"{iso}: 解析できなかった")
            self.assertAlmostEqual(got, expected, delta=0.6,
                                   msg=f"{iso}: 解析式 {got} vs Skyfield {expected}")

    def test_tokyo_noon_is_the_daily_maximum(self):
        """東京 2026-10-23 の南中高度は 43.0°（Skyfield の実測は 11:25 に 42.95°）。"""
        noon = wa.sun_altitude_deg("2026-10-23T11:26", *TOKYO)
        self.assertIsNotNone(noon)
        self.assertAlmostEqual(noon, 43.0, delta=0.5)

    def test_southern_hemisphere(self):
        for iso, expected in [("2026-12-21T13:00", 72.0),
                              ("2026-12-21T22:00", -26.7),
                              ("2026-06-21T05:00", -24.1),
                              ("2026-06-21T22:00", -62.4)]:
            got = wa.sun_altitude_deg(iso, *SYDNEY)
            self.assertIsNotNone(got, f"{iso}: 解析できなかった")
            self.assertAlmostEqual(got, expected, delta=0.6,
                                   msg=f"{iso}: 解析式 {got} vs Skyfield {expected}")

    def test_polar_day_and_polar_night_do_not_raise(self):
        """トロムソ（69.65N）: 夏至は真夜中でも太陽が地平線上、冬至は一日中地平線下。"""
        midnight_sun = wa.sun_altitude_deg("2026-06-21T00:00", 69.65, 18.96, 3600)
        polar_noon = wa.sun_altitude_deg("2026-12-21T12:00", 69.65, 18.96, 3600)
        polar_midnight = wa.sun_altitude_deg("2026-12-21T00:00", 69.65, 18.96, 3600)
        self.assertIsNotNone(midnight_sun)
        self.assertIsNotNone(polar_noon)
        self.assertIsNotNone(polar_midnight)
        self.assertGreater(midnight_sun, 0.0)
        self.assertLess(polar_noon, 0.0)
        self.assertLess(polar_midnight, -30.0)

    def test_utc_offset_changes_the_hour_angle(self):
        a = wa.sun_altitude_deg("2026-10-23T22:00", 35.68, 139.69, 9 * 3600)
        b = wa.sun_altitude_deg("2026-10-23T22:00", 35.68, 139.69, 0)
        self.assertNotAlmostEqual(a, b, places=1)

    def test_invalid_input_returns_none(self):
        self.assertIsNone(wa.sun_altitude_deg("2026-02-30T25:00", *TOKYO))
        self.assertIsNone(wa.sun_altitude_deg("", *TOKYO))
        self.assertIsNone(wa.sun_altitude_deg("2026-10-23T17:00", None, 139.69, 32400))
        self.assertIsNone(wa.sun_altitude_deg("2026-10-23T17:00", 35.68, 139.69, None))
        self.assertIsNone(wa.sun_altitude_deg("2026-10-23T17:00", "北緯", 139.69, 32400))

    def test_sun_altitudes_list_matches_single_calls(self):
        times = ["2026-10-23T18:00", "2026-10-23T22:00", "2026-10-24T04:00"]
        got = wa.sun_altitudes(times, *TOKYO)
        self.assertEqual(got, [wa.sun_altitude_deg(t, *TOKYO) for t in times])
        self.assertTrue(all(g is not None for g in got))
        self.assertLess(got[0], -10.0)      # 18:00 は天文薄明の途中（-18° より上）
        self.assertLess(got[1], -50.0)      # 22:00 は十分暗い
        self.assertLess(got[2], -15.0)      # 04:00 はまだ暗い


class DarkWindowTests(unittest.TestCase):
    """太陽高度で薄明を除いた時間帯の抽出（合成データ・ネットワーク不要）。"""

    def _hourly(self, cloud=10, is_day=True):
        times, clouds, vis, wind, pop, code, day = [], [], [], [], [], [], []
        start = dt.datetime(2026, 10, 23, 0)
        for k in range(48):
            t = start + dt.timedelta(hours=k)
            times.append(t.strftime("%Y-%m-%dT%H:%M"))
            clouds.append(cloud)
            vis.append(40000)
            wind.append(2.0)
            pop.append(0)
            code.append(1)
            day.append(1 if (is_day and 7 <= t.hour <= 16) else 0)
        return {"time": times, "cloud_cover": clouds, "visibility": vis,
                "wind_speed_10m": wind, "precipitation_probability": pop,
                "weather_code": code, "is_day": day}

    def test_bright_twilight_is_excluded(self):
        """17:00/18:00（日の入り直後の薄明）は暗い時間帯に入らない。"""
        h = self._hourly()
        alts = wa.sun_altitudes(h["time"], *TOKYO)
        windows = wa._night_obs_windows(h, {}, sun_alts=alts, sun_alt_max=-15.0)
        starts = [w["start"] for w in windows]
        self.assertIn("2026-10-23T19:00", starts)     # 太陽高度 -24° まで下がってから
        self.assertNotIn("2026-10-23T18:00", [w["start"] for w in windows])
        for w in windows:
            self.assertLessEqual(w["max_sun_alt"], -15.0)
            self.assertLess(w["min_sun_alt"], w["max_sun_alt"])

    def test_loose_threshold_includes_civil_twilight(self):
        h = self._hourly()
        alts = wa.sun_altitudes(h["time"], *TOKYO)
        windows = wa._night_obs_windows(h, {}, sun_alts=alts, sun_alt_max=-5.0)
        self.assertIn("2026-10-23T18:00", [w["start"] for w in windows])
        self.assertNotIn("2026-10-23T17:00", [w["start"] for w in windows])

    def test_without_sun_altitude_falls_back_to_is_day(self):
        """太陽高度が取れない地点（極昼など）でも is_day で判定して窓を返す。"""
        h = self._hourly()
        windows = wa._night_obs_windows(h, {}, sun_alts=None, sun_alt_max=-15.0)
        # is_day だけの判定では「日の入り直後＝夜」なので薄明も入ってしまう（旧挙動）
        self.assertIn("2026-10-23T17:00", [w["start"] for w in windows])
        self.assertIsNone(windows[0]["min_sun_alt"])

    def test_is_dark_uses_sun_altitude_when_available(self):
        h = {"is_day": [0]}
        self.assertTrue(wa._is_dark(0, [-20.0], h, -15.0))
        self.assertFalse(wa._is_dark(0, [-10.0], h, -15.0))
        self.assertTrue(wa._is_dark(0, [None], h, -15.0))     # is_day にフォールバック
        self.assertFalse(wa._is_dark(0, [None], {"is_day": [1]}, -15.0))


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _stub_forecast(payload):
    def fake_get(url, *args, **kwargs):
        if "geocoding" in url or "nominatim" in url:
            raise AssertionError("lat/lon 指定なのにジオコーディングを呼んだ")
        return _FakeResp(payload)
    return fake_get


class AstronomyWeatherToolTests(unittest.TestCase):
    """Open-Meteo の応答を差し替えた統合テスト（ネットワーク不要）。"""

    def setUp(self):
        self._old_get = requests.get
        wa.astronomy_weather.cache_clear()

    def tearDown(self):
        requests.get = self._old_get
        wa.astronomy_weather.cache_clear()

    def _payload(self):
        times, clouds, vis, wind, pop, code, day = [], [], [], [], [], [], []
        start = dt.datetime(2026, 10, 23, 0)
        for k in range(72):
            t = start + dt.timedelta(hours=k)
            times.append(t.strftime("%Y-%m-%dT%H:%M"))
            clouds.append(10)
            vis.append(40000)
            wind.append(2.0)
            pop.append(0)
            code.append(1)
            day.append(1 if 7 <= t.hour <= 16 else 0)
        return {"latitude": 35.7, "longitude": 139.7, "utc_offset_seconds": 32400,
                "hourly": {"time": times, "cloud_cover": clouds, "visibility": vis,
                           "is_day": day, "precipitation_probability": pop,
                           "weather_code": code, "wind_speed_10m": wind},
                "daily": {"time": ["2026-10-23", "2026-10-24", "2026-10-25"],
                          "moon_phase": [0.391, 0.426, 0.461],
                          "moonrise": ["2026-10-23T15:06", "2026-10-24T15:33", "2026-10-25T16:01"],
                          "moonset": ["2026-10-23T02:26", "2026-10-24T03:30", "2026-10-25T04:36"],
                          "sunrise": ["2026-10-23T05:54", "2026-10-24T05:55", "2026-10-25T05:56"],
                          "sunset": ["2026-10-23T16:56", "2026-10-24T16:55", "2026-10-25T16:53"]}}

    def _call(self, lat, lon, **kw):
        requests.get = _stub_forecast(self._payload())
        return wa.astronomy_weather(latitude=lat, longitude=lon, include_rain=False, **kw)

    def test_excludes_twilight_and_reports_night_moonset(self):
        res = self._call(35.6811, 139.6911)
        sc = res.structuredContent
        text = "\n".join(c.text for c in res.content if c.type == "text")

        # 太陽高度で判定していること（is_day ではなく）
        self.assertEqual(sc["sun_alt_source"], "sun_altitude")
        self.assertEqual(sc["sun_alt_max"], -15.0)
        # 日の入り直後の薄明は窓に入らない
        self.assertNotIn("2026-10-23T17:00", [h["time"] for h in sc["hourly"]])
        self.assertNotIn("2026-10-23T18:00", [h["time"] for h in sc["hourly"]])
        self.assertIn("2026-10-23T19:00", [w["start"] for w in sc["windows"]])
        for w in sc["windows"]:
            self.assertLessEqual(w["max_sun_alt"], -15.0)
        for h in sc["hourly"]:
            self.assertIsNotNone(h["sun_alt_deg"])
        # 月: 照度は離角ベース、「今夜の月の入り」は翌日 03:30
        self.assertEqual(sc["moon"]["illumination_pct"], "88.7%")
        self.assertEqual(sc["moon"]["moonset"], "2026-10-24T03:30")
        self.assertTrue(sc["moon"]["moonset_next_day"])
        self.assertIn("月の入り 10-24 03:30", text)
        self.assertNotIn("02:26", text)
        self.assertIn("太陽高度", text)

    def test_loose_threshold_widens_the_window(self):
        res = self._call(35.6822, 139.6922, sun_alt_max=-5)
        sc = res.structuredContent
        self.assertEqual(sc["sun_alt_max"], -5.0)
        self.assertIn("2026-10-23T18:00", [w["start"] for w in sc["windows"]])

    def test_out_of_range_threshold_is_clamped(self):
        res = self._call(35.6833, 139.6933, sun_alt_max=10)
        self.assertEqual(res.structuredContent["sun_alt_max"], -1.0)

    def test_network_down_returns_error_result(self):
        def boom(*args, **kwargs):
            raise requests.ConnectionError("simulated blackhole")
        requests.get = boom
        res = wa.astronomy_weather(latitude=35.6844, longitude=139.6944, include_rain=False)
        self.assertEqual(res.structuredContent.get("error"), "simulated blackhole")
        self.assertIn("接続に失敗", res.content[0].text)


if __name__ == "__main__":
    unittest.main()

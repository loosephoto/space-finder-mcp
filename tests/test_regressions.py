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

    def test_satellite_status_ranks_acronym_matches_first(self):
        """query の一致度順に並べること（"meteor" が "Meteorological" に埋もれない）。

        fullname の単純な部分一致では、DMSP 等の「Meteorological（気象）」を
        Meteor-M として拾ってしまい、本来の Meteor-M が上位20件から消える（実測）。
        acronym 一致を優先し、名称の途中一致は件数を分けて報告することを固定する。
        """
        from space_finder_mcp import oscar

        catalogue = [
            {"acronym": "DMSP-F01", "fullname": "Defense Meteorological Satellite Program - F01",
             "slug": "dmsp_f01", "space_agency": "DoD,NOAA", "status": "inactive",
             "launch_date": "11 Sep 1976"},
            {"acronym": "COSMIC-1", "fullname": "Constellation Observing System for Meteorology - 1",
             "slug": "cosmic_1", "space_agency": "NSPO,NOAA", "status": "inactive",
             "launch_date": "14 Apr 2006"},
            {"acronym": "Meteor-M N2-3", "fullname": "Meteor-M Number 2-3",
             "slug": "meteor_m_n2_3", "space_agency": "Roscosmos", "status": "operational",
             "launch_date": "27 Jun 2023"},
            {"acronym": "Meteor-M N2-4", "fullname": "Meteor-M Number 2-4",
             "slug": "meteor_m_n2_4", "space_agency": "Roscosmos", "status": "operational",
             "launch_date": "29 Feb 2024"},
            {"acronym": "FY-3D", "fullname": "Feng-Yun 3D", "slug": "fy_3d",
             "space_agency": "CMA", "status": "operational", "launch_date": "14 Nov 2017"},
        ]
        old = oscar._catalogue
        oscar._catalogue = lambda: {"satellites": catalogue, "total": len(catalogue),
                                    "pages": 1, "failed_pages": []}
        oscar.satellite_status.cache_clear()
        try:
            result = oscar.satellite_status(query="meteor", limit=5)
        finally:
            oscar._catalogue = old
            oscar.satellite_status.cache_clear()
        sc = result.structuredContent
        self.assertEqual(sc["results"][0]["acronym"], "Meteor-M N2-4")   # 運用中・新しい順
        self.assertEqual(sc["results"][1]["acronym"], "Meteor-M N2-3")
        self.assertEqual(sc["strong_matches"], 2)          # acronym 一致の2件
        self.assertEqual(sc["weak_only_matches"], 2)       # Meteorological の2件
        self.assertTrue(all(r["match_score"] >= 50 for r in sc["results"][:2]))
        self.assertIn("部分一致のみ 2 件", result.content[0].text)

    def test_satellite_status_scans_every_page_and_reports_failures(self):
        """全ページを走査し、取得できなかったページは黙って落とさず報告すること。"""
        from space_finder_mcp import oscar

        calls = []

        def fake_page(page):
            calls.append(page)
            if page == 2:
                raise RuntimeError("page 2 failed")
            if page == 1:
                return {"page": {"totalElements": 90, "totalPages": 3, "number": 1},
                        "_embedded": {"satellites": [{"acronym": "A-1"}]}}
            return {"page": {"totalElements": 90, "totalPages": 3, "number": page},
                    "_embedded": {"satellites": [{"acronym": "A-%d" % page}]}}

        old = oscar._fetch_page
        old_disk = oscar._disk_read
        oscar._fetch_page = fake_page
        oscar._disk_read = lambda ttl: None            # ディスクキャッシュを使わない
        try:
            cat = oscar._catalogue.__wrapped__()       # キャッシュ層を迂回して直接検証
        finally:
            oscar._fetch_page = old
            oscar._disk_read = old_disk
        self.assertEqual(sorted(calls), [1, 2, 3])     # 全ページを要求している
        self.assertEqual(cat["total"], 90)
        self.assertEqual(cat["failed_pages"], [2])     # 失敗ページを記録
        self.assertEqual(len(cat["satellites"]), 2)    # 取れた分だけ返す（欠けを隠さない）

    def test_space_weather_falls_back_to_noaa_swpc(self):
        """NASA が使えないときは NOAA SWPC（認証不要）に切り替え、出典を明記すること。"""
        import requests
        from space_finder_mcp import donki, swpc

        def nasa_down(*args, **kwargs):
            raise requests.ConnectionError("simulated NASA failure")

        old_get_cached = donki._get_cached
        old_fetch_all = swpc.fetch_all
        donki._get_cached = nasa_down
        swpc.fetch_all = lambda: {"sections": {"kp": {"time_tag": "2026-09-16T07:45:00",
                                                     "current": 1, "max_3h": 3, "max_24h": 4},
                                             "scales": {"current": {"G": {"scale": "0"}},
                                                        "forecast": {"1": {"G": {"scale": "1"}}}}},
                                  "failed": ["proton: simulated failure"],
                                  "fetched_utc": "2026-09-16T07:46:00Z"}
        donki.space_weather.cache_clear()
        try:
            result = donki.space_weather(kind="all")
        finally:
            donki._get_cached = old_get_cached
            swpc.fetch_all = old_fetch_all
            donki.space_weather.cache_clear()

        sc = result.structuredContent
        self.assertEqual(sc["source"], "NOAA SWPC")
        self.assertTrue(sc["fallback"])
        self.assertNotEqual(sc.get("error"), "fetch failed")      # エラーで終わらせない
        self.assertIn("NOAA SWPC", result.content[0].text)
        self.assertIn("Kp", result.content[0].text)
        self.assertIn("proton", result.content[0].text)           # 欠けは隠さない
        self.assertEqual(sc["failed"], ["proton: simulated failure"])

    def test_swpc_xray_class_and_failed_sections(self):
        """X線クラス表記と、取得できなかった項目の記録（黙って落とさない）。"""
        from space_finder_mcp import swpc

        self.assertEqual(swpc._xray_class(2.37e-7), "B2.4")
        self.assertEqual(swpc._xray_class(1.0e-6), "C1.0")
        self.assertEqual(swpc._xray_class(3.5e-5), "M3.5")
        self.assertEqual(swpc._xray_class("?"), "?")
        self.assertEqual(swpc._xray_class(None), "?")

        def fake_json(path):
            if "xrays" in path:
                return [{"energy": "0.1-0.8nm", "flux": 2.37e-7, "time_tag": "2026-09-16T07:00:00Z"}]
            raise RuntimeError("simulated SWPC failure")

        old = swpc._get_json
        swpc._get_json = fake_json
        try:
            data = swpc.fetch_all.__wrapped__()        # キャッシュ層を迂回
        finally:
            swpc._get_json = old
        self.assertIn("xray", data["sections"])
        self.assertEqual(data["sections"]["xray"]["latest_class"], "B2.4")
        # X線以外の全項目は failed に残る（静かに欠けさせない）。
        # 項目数は「表示対象の一覧」から採る（セクションを足しても壊れないように）。
        self.assertEqual(len(data["failed"]), len(swpc._SECTIONS_BY_KIND["all"]) - 1)
        self.assertTrue(all("simulated SWPC failure" in f for f in data["failed"]))

    def test_swpc_all_items_failed_returns_error_not_calm(self):
        """SWPC も全項目失敗したら「静穏」ではなくエラーとして返すこと。

        空のサマリを返すと、ホストLLM が「宇宙天気は静穏」と読み違える（障害を
        静穏と誤読させる）。1項目も取れない場合はエラー結果にする。
        """
        from space_finder_mcp import swpc

        def all_fail(path):
            raise RuntimeError("simulated total failure")

        old = swpc._get_json
        old_fetch = swpc.fetch_all
        swpc._get_json = all_fail
        swpc.fetch_all.cache_clear() if hasattr(swpc.fetch_all, "cache_clear") else None
        try:
            data = swpc.fetch_all.__wrapped__()
            swpc.fetch_all = lambda: data
            result = swpc.space_weather_now("all", nasa_reason="NASA 429")
        finally:
            swpc._get_json = old
            swpc.fetch_all = old_fetch
        self.assertEqual(result.structuredContent["error"], "no space weather data")
        self.assertNotIn("静穏", result.content[0].text)
        self.assertIn("取得できませんでした", result.content[0].text)
        self.assertEqual(len(result.structuredContent["failed"]),
                         len(swpc._SECTIONS_BY_KIND["all"]))


class CometApparitionTests(unittest.TestCase):
    """彗星の見え方チャート（comet_apparition）と彗星名の解決まわりの回帰テスト。"""

    def test_unknown_comet_hint_is_defined(self):
        # 実測: _comet_unknown_hint が未定義のまま呼ばれ、未知の彗星名で NameError が
        # ツールの外へ漏れていた（規約1違反）。オフラインで再現させて固定する。
        from space_finder_mcp import solar_system as ss

        self.assertTrue(callable(getattr(ss, "_comet_unknown_hint", None)))
        self.assertIn("指定できる彗星の例", ss._comet_unknown_hint("存在しない彗星XYZ"))

        def boom(*args, **kwargs):
            raise requests.ConnectionError("offline")

        old_get = requests.get
        requests.get = boom
        try:
            r = ss.solar_system_now(comet="存在しない彗星XYZ", view="comet_orbit")
        finally:
            requests.get = old_get
        self.assertIsNotNone(r.structuredContent)
        self.assertIn("error", r.structuredContent)
        self.assertIn("指定できる彗星の例", r.content[0].text)

    def test_apparition_view_requires_comet(self):
        from space_finder_mcp import solar_system as ss

        r = ss.solar_system_now(view="apparition")
        self.assertIn("error", r.structuredContent or {})
        self.assertIn("彗星の指定が必要", r.content[0].text)
        # 複数指定は1天体ずつであることを明示して停止する（推測しない）
        r2 = ss.solar_system_now(comet="169P,ハレー彗星", view="apparition")
        self.assertIn("1天体ずつ", r2.content[0].text)

    def test_comet_xyz_from_elements_conics(self):
        # 近日点通過時刻では r = q になる（楕円・放物線・双曲線のすべて）。
        import math

        from space_finder_mcp.solar_system import comet_xyz_from_elements

        base = {"i": math.radians(11.3), "node": math.radians(40.0),
                "argp": math.radians(200.0), "q": 0.6, "tp": 2461300.5}
        for e in (0.5, 1.0, 1.2):
            el = dict(base, e=e)
            r = comet_xyz_from_elements(el, 2461300.5)[3]
            self.assertAlmostEqual(r, 0.6, places=6, msg="e={}".format(e))
            # 近日点通過の前後で r は増える（放物線・双曲線でも同じ）
            self.assertGreater(comet_xyz_from_elements(el, 2461330.5)[3], 0.6)

    def test_comet_xyz_from_elements_requires_q_and_tp(self):
        import math

        from space_finder_mcp.solar_system import comet_xyz_from_elements

        with self.assertRaises(ValueError):
            comet_xyz_from_elements({"e": 0.7, "i": 0.1, "node": 0.0, "argp": 0.0},
                                    2461300.5)

    def test_peri_times_guards_sentinel_period(self):
        # 双曲線では Horizons が周期に 1e99 の番兵を返すことがある。そのままだと
        # 巨大な JD を作り、日付への変換で OverflowError になっていた。
        from space_finder_mcp.comet_apparition import _fmt_jd, _peri_times

        nel = {"e": 1.0001831, "tp": 2460581.3, "period_days": 1.0e99}
        prev, nxt = _peri_times(nel, 2461305.9)
        self.assertEqual(prev, 2460581.3)
        self.assertIsNone(nxt)
        self.assertEqual(_fmt_jd(None), "-")
        self.assertEqual(_fmt_jd(2461305.9), "2026-09-22")
        self.assertEqual(_fmt_jd(1.0e99), "-")

    def test_apparition_error_paths_do_not_leak(self):
        # 未知の彗星・不正な when でも例外を外へ出さず error を返す。
        from space_finder_mcp.comet_apparition import comet_apparition_result

        r = comet_apparition_result("存在しない彗星XYZ", days=30)
        self.assertIn("error", r.structuredContent or {})
        r2 = comet_apparition_result("169P", when_iso="変な値", days=30)
        self.assertIn("error", r2.structuredContent or {})


class OrbitRouteTests(unittest.TestCase):
    """太陽系俯瞰図に重ねる彗星の通過経路（orbit_path_xyz / route=True）の回帰テスト。"""

    def test_orbit_path_ellipse_is_closed_and_within_q_and_Q(self):
        # 経路は「閉じた楕円」として描くので、先頭と末尾が一致し、r が q〜Q に収まること。
        import math

        from space_finder_mcp.solar_system import orbit_path_xyz

        el = {"e": 0.8470, "q": 0.3395, "i": 0.2058, "node": 0.5218, "argp": 0.3256}
        pts = orbit_path_xyz(el, n=360)
        self.assertEqual(len(pts), 361)
        self.assertLess(math.dist(pts[0], pts[-1]), 1e-9)          # 閉曲線
        rs = [math.dist((0.0, 0.0, 0.0), p) for p in pts]
        q, Q = el["q"], el["q"] / (1.0 - el["e"]) * (1.0 + el["e"])
        self.assertGreaterEqual(min(rs), q * 0.999)
        self.assertLessEqual(max(rs), Q * 1.001)

    def test_orbit_path_hyperbola_is_cut_at_r_cap(self):
        # e>=1 は閉じないので r_cap で切る（閉じた線として描くと嘘になる）。
        import math

        from space_finder_mcp.solar_system import orbit_path_xyz

        pts = orbit_path_xyz({"e": 1.2, "q": 0.4, "i": 0.1, "node": 0.2, "argp": 0.3}, r_cap=5.0)
        rs = [math.dist((0.0, 0.0, 0.0), p) for p in pts]
        self.assertLessEqual(max(rs), 5.0 + 1e-9)
        self.assertGreater(math.dist(pts[0], pts[-1]), 1e-9)       # 閉じていない
        with self.assertRaises(ValueError):
            orbit_path_xyz({"e": 1.2, "q": None, "i": 0.1, "node": 0.0, "argp": 0.0})

    def test_norm_orbit_el_converts_degrees_once(self):
        # Horizons 経路は度、SBDB 経路はラジアン。混ぜると角度が 57 倍ずれる。
        import math

        from space_finder_mcp.solar_system import norm_orbit_el

        hz = norm_orbit_el("1P", {"typ": "horizons", "e": 0.967, "q": 0.586, "tp_jd": 2446470.5,
                                  "i_deg": 162.26, "node_deg": 58.42, "argp_deg": 111.33,
                                  "period_days": 27509.0})
        self.assertAlmostEqual(hz["i"], math.radians(162.26), places=12)
        self.assertLess(hz["i"], math.pi + 0.1)                    # ラジアン（度なら 162 になる）
        sb = norm_orbit_el("2P", {"e": 0.847, "q": 0.3395, "a": 4.099, "fullname": "2P/Encke",
                                  "_raw": {"tp": 2460581.3, "i": 0.2058, "node": 0.5218,
                                           "argp": 0.3256, "period_days": 1207.0, "m1": 15.7,
                                           "k1": 4.5}})
        self.assertAlmostEqual(sb["i"], 0.2058, places=12)
        self.assertEqual(sb["a"], 4.099)
        self.assertEqual(sb["m1"], 15.7)

    def test_peri_times_shared_helper(self):
        # solar_system 側へ集約した peri_times が comet_apparition からも同じ結果を返す。
        from space_finder_mcp.comet_apparition import _peri_times
        from space_finder_mcp.solar_system import peri_times

        nel = {"e": 0.847, "tp": 2460581.3, "period_days": 1207.0}
        self.assertEqual(peri_times(nel, 2461305.9), _peri_times(nel, 2461305.9))
        prev, nxt = peri_times(nel, 2461305.9)
        self.assertAlmostEqual(nxt - prev, 1207.0, places=6)
        self.assertLessEqual(abs(2461305.9 - nxt), 1207.0)
        self.assertEqual(peri_times({"e": 1.0002, "tp": 2460581.3, "period_days": 1207.0},
                                    2461305.9)[1], None)

    def test_route_overview_returns_image_and_verified_marks(self):
        # route=True で通過経路を重ねた俯瞰図: 目印の画素・破線の実在まで検証して返す。
        from space_finder_mcp.solar_system import solar_system_now

        r = solar_system_now(comet="エンケ彗星", route=True)
        sc = r.structuredContent or {}
        self.assertNotIn("error", sc)
        fig = sc.get("figure") or {}
        v = fig.get("verify") or {}
        self.assertTrue(v.get("ok"), v.get("missing"))
        self.assertEqual(v["routes"][0]["segments"], 720)
        self.assertTrue(any(m.get("pixels_found") and m["pixels_found"] >= 3
                            for m in v["routes"][0]["marks"]))
        self.assertTrue(any("通過経路" in n for n in fig.get("notes", [])))
        rt = (sc.get("comet_routes") or {})["エンケ彗星"]
        self.assertNotIn("points", rt)                              # 点列は structuredContent に入れない
        self.assertEqual(rt["marks"][0]["id"], "perihelion")
        self.assertEqual(len(r.content), 2)
        self.assertIn("file:///", r.content[0].text.splitlines()[0])   # 画像より前にリンク（規約13）

    def test_route_mark_inside_sun_disk_is_not_claimed(self):
        # 近日点が誇張した太陽円盤の内側に入る彗星では、目印を描かず「図からは確認できない」を
        # 注記に出す（描いていないのに描いたと言わない）。
        from space_finder_mcp.solar_system import solar_system_now

        r = solar_system_now(comet="紫金山・アトラス彗星", route=True)
        sc = r.structuredContent or {}
        self.assertNotIn("error", sc)
        v = (sc.get("figure") or {}).get("verify") or {}
        self.assertTrue(v.get("ok"), v.get("missing"))
        marks = v["routes"][0]["marks"]
        self.assertEqual(marks[0]["label"], "近日点")
        self.assertFalse(marks[0]["drawn"])
        self.assertEqual(marks[0]["reason"], "sun_disk")
        self.assertTrue(v["routes"][0]["occluded"][0]["verified"])
        self.assertTrue(any("確認できない" in n for n in sc["figure"]["notes"]))

    def test_accurate_route_marks_are_not_hidden_by_sun_marker(self):
        # 実測: 線形(±45AU)は約12 px/AU しかなく、太陽マーカー(s=300)は半径約1.7 AU 相当。
        # 太陽を後に描いていたため、エンケ彗星の近日点(0.34 AU)の◇が完全に隠れていた
        # （画素0）。太陽を最背面・◇を最前面にして、目印の画素が測れることを固定する。
        from space_finder_mcp.solar_system import solar_system_now

        r = solar_system_now(comet="エンケ彗星", route=True, engine="accurate")
        sc = r.structuredContent or {}
        self.assertNotIn("error", sc)
        self.assertIn("accurate", sc.get("engine", ""))
        fig = sc.get("figure") or {}
        v = fig.get("verify") or {}
        self.assertTrue(v.get("ok"), v.get("missing"))
        marks = {m["id"]: m for m in v["routes"][0]["marks"]}
        self.assertIsNotNone(marks["perihelion"].get("px"))
        self.assertGreaterEqual(marks["perihelion"].get("pixels_found") or 0, 3)
        self.assertGreaterEqual(marks["aphelion"].get("pixels_found") or 0, 3)
        # 太陽マーカーの誇張（半径 AU）を注記・scale に出していること
        self.assertTrue(any("太陽の描画マーカー" in x
                            for x in (fig.get("scale") or {}).get("exaggerated", [])))
        self.assertTrue(any("誇張した太陽マーカー" in n for n in fig.get("notes", [])))


class OrbitRouteNbodyPerihelionTests(unittest.TestCase):
    """通過経路の近日点日付は SBDB の2体近似だけでは足りない（n 体解を併記する）回帰テスト。

    実測 1P/Halley: 次回近日点は SBDB の2体近似で 2062-01-08、JPL Horizons の n 体解で
    2061-07-28（163.8 日の差）。SBDB の要素は古いエポックの接触軌道なので、摂動の大きい
    彗星では2体近似が数か月ずれる。片方だけ出すと「図の日付＝実際の回帰」と誤読される。
    """

    def _patch(self):
        """SBDB / Horizons をネットワークなしの固定値に差し替える（元へ戻す関数を返す）。"""
        import math

        from space_finder_mcp import solar_system as ss

        el = {"e": 0.9679359956953211, "i": math.radians(162.1905300439129),
              "node": math.radians(59.09894720612437),
              "argp": math.radians(112.2414314637764),
              "a": 17.834, "epoch": 2439875.5, "fullname": "1P/Halley",
              "q": 0.5748638313743413, "tp": 2446469.9736161465,
              # ma はラジアン・n は度/日（_sbdb_elements が変換して返す形に合わせる。
              # これが無いと現在位置のケプラー伝播が KeyError になり、経路が作られない）
              "ma": 6.0206, "n": 0.012984,
              "period_days": 27728.04608790421, "kind": "cn"}
        old = (ss._sbdb_elements, ss._horizons_cmd_for, ss._horizons_perihelion_jd)
        ss._sbdb_elements = lambda sstr: dict(el)
        ss._horizons_cmd_for = lambda cid: "DES={};CAP".format(cid)

        def restore():
            (ss._sbdb_elements, ss._horizons_cmd_for, ss._horizons_perihelion_jd) = old

        return ss, restore

    def test_route_shows_nbody_perihelion_alongside_two_body(self):
        ss, restore = self._patch()
        ss._horizons_perihelion_jd = lambda cmd, jd: 2474034.209248025   # 2061-07-28（n 体解）
        try:
            result = ss.solar_system_now(comet="ハレー彗星", route=True)
        finally:
            restore()

        sc = result.structuredContent
        mark = sc["comet_routes"]["ハレー彗星"]["marks"][0]
        self.assertEqual(mark["date"], "2062-01-08")                 # SBDB の2体近似
        self.assertEqual(mark["date_nbody"], "2061-07-28")           # Horizons の n 体解
        self.assertAlmostEqual(mark["nbody_diff_days"], -163.8, delta=0.2)
        self.assertEqual(sc["comet_routes"]["ハレー彗星"]["tp_nbody_date"],
                         "2061-07-28")
        notes = "\n".join(sc["figure"]["notes"])
        self.assertIn("2061-07-28", notes)
        self.assertIn("差 163.8 日", notes)
        self.assertIn("2061-07-28（Horizons n 体解）", result.content[0].text)

    def test_route_notes_say_nbody_unavailable_instead_of_silently_two_body(self):
        # Horizons を引けないとき（遮断・応答異常）に2体近似だけを出すと、ずれを隠したまま
        # 「実際の回帰」として読まれる。取得できなかった旨と理由を注記に残す。
        ss, restore = self._patch()

        def boom(cmd, jd):
            raise ValueError("Horizons に接続できません")

        ss._horizons_perihelion_jd = boom
        try:
            result = ss.solar_system_now(comet="ハレー彗星", route=True)
        finally:
            restore()

        sc = result.structuredContent
        rtout = sc["comet_routes"]["ハレー彗星"]
        self.assertIsNone(rtout["tp_nbody_jd"])
        self.assertEqual(rtout["tp_nbody_date"], "-")            # 日付は無い（捏造しない）
        self.assertNotIn("date_nbody", rtout["marks"][0])
        notes = "\n".join(sc["figure"]["notes"])
        self.assertIn("n 体解を取得できなかった", notes)
        self.assertIn("Horizons に接続できません", notes)
        self.assertNotIn("date_nbody", str(sc["comet_routes"]))


class RangeAuTests(unittest.TestCase):
    """太陽系俯瞰図の表示範囲指定（range_au）。土星より内側だけを拡大して見るための回帰テスト。"""

    def test_fixed_range_lists_outside_planets_with_numbers(self):
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(range_au=10).structuredContent
        self.assertEqual(sc["range_au"], 10.0)
        self.assertEqual(sc["figure"]["scale"]["range_au"]["hi"], 10.0)
        names = [o["name"] for o in sc["out_of_range"]]
        self.assertIn("天王星", names)
        self.assertIn("海王星", names)
        # 範囲外は黙って消さず、数値付きで注記に列挙する
        joined = "\n".join(sc["figure"]["notes"])
        self.assertIn("表示範囲10 AU の外にあるため描いていない", joined)
        self.assertIn("19.44 AU", joined)                      # 天王星の実際の日心距離
        self.assertIn("表示範囲の外の惑星軌道の円は描いていない", joined)

    def test_both_engines_honor_range_au(self):
        from space_finder_mcp.solar_system import solar_system_now

        for eng in ("simple", "accurate"):
            sc = solar_system_now(range_au=10, engine=eng).structuredContent
            self.assertEqual(sc["figure"]["scale"]["range_au"]["hi"], 10.0, eng)
            self.assertIn("表示範囲", "\n".join(sc["figure"]["notes"]), eng)

    def test_linear_scale_actually_zooms_with_range_au(self):
        # 線形版の縮尺は「画像幅/(2*lim)」。lim を 45 AU → 10 AU に絞れば px/AU が約4.5倍に
        # なる（figure.scale.px_per_AU に実測値を出しているので数値で検証できる）。
        from space_finder_mcp.solar_system import solar_system_now

        auto = solar_system_now(engine="accurate").structuredContent["figure"]["scale"]
        tight = solar_system_now(engine="accurate", range_au=10).structuredContent["figure"]["scale"]
        self.assertGreater(auto.get("px_per_AU") or 0, 5.0)
        self.assertLess(auto.get("px_per_AU") or 0, 20.0)          # 実測 約12 px/AU（縮尺は固定）
        self.assertGreater((tight.get("px_per_AU") or 0), (auto.get("px_per_AU") or 0) * 3.0)

    def test_inner_planets_visible_and_outer_not_drawn_with_range_au(self):
        # range_au=10 では太陽マーカーの誇張半径が 1.7 AU → 0.4 AU に下がり、内惑星が隠れない。
        # 地球（青）のマーカーが実際に画素として現れ、範囲外の海王星（同じ青系）は描かれない。
        import numpy as np
        from PIL import Image

        from space_finder_mcp.solar_system import solar_system_now

        def sun_marker_au(sc):
            for x in (sc["figure"]["scale"].get("exaggerated") or []):
                if "太陽の描画マーカー" in x:
                    return float(x.split("半径 ")[1].split(" AU")[0])
            return None

        auto = solar_system_now(engine="accurate").structuredContent
        tight = solar_system_now(engine="accurate", range_au=10).structuredContent
        self.assertGreater(sun_marker_au(auto), 1.0)               # 1.7 AU（内惑星を覆う大きさ）
        self.assertLess(sun_marker_au(tight), 0.5)                 # 0.4 AU
        a = np.asarray(Image.open(tight["image_path"]).convert("RGB")).astype(int)
        blue = (a[:, :, 2] > 140) & (a[:, :, 2] - a[:, :, 0] > 55)
        self.assertGreater(int(blue.sum()), 50)                    # 地球のマーカーが見えている
        # 海王星（30 AU）は範囲外なので描かれていない ＝ 青系は地球だけ
        self.assertIn("海王星", [o["name"] for o in tight["out_of_range"]])
        # 画素検証（figure.verify）が範囲内の描画と範囲外の非描画を実際に測っていること
        v = tight["figure"].get("verify") or {}
        self.assertTrue(v.get("ok"), v)
        rv = v.get("range_au") or {}
        self.assertTrue(rv.get("planets_drawn"), rv)
        self.assertTrue(all(c["ok"] for c in rv["planets_drawn"]), rv["planets_drawn"])
        self.assertTrue(all(c["ok"] for c in rv["out_of_range_zero_pixels"]),
                        rv["out_of_range_zero_pixels"])
        self.assertTrue((rv.get("sun_marker_vs_inner_planet") or {}).get("ok"), rv)

    def test_route_labels_do_not_hide_planet_markers(self):
        # range_au=10＋route では ◇の日付ラベル箱が金星のマーカーを覆っていた（実測 255→13 px）。
        # ラベルを惑星マーカーより下の zorder にして、金星の画素が残ることを固定する。
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(range_au=10, engine="accurate", comet="エンケ彗星",
                              route=True).structuredContent
        v = sc["figure"].get("verify") or {}
        self.assertTrue(v.get("ok"), v)
        drawn = {c["name"]: c["pixels_found"]
                 for c in ((v.get("range_au") or {}).get("planets_drawn") or [])}
        self.assertGreaterEqual(drawn.get("金星", 0), 20, drawn)

    def test_range_au_accepts_body_names(self):
        # LLM が「火星まで」「木星まで」と判断して縮尺を選べるように、天体名も受け付ける。
        # 名前は「その天体の軌道の円が入る」ように長半径×1.08 で決める。
        from space_finder_mcp.solar_system import solar_system_now

        for raw, want, word in (("火星", 1.524 * 1.08, "火星"), ("木星まで", 5.20 * 1.08, "木星"),
                                ("saturn", 9.58 * 1.08, "土星"), ("土星の軌道", 9.58 * 1.08, "土星")):
            sc = solar_system_now(range_au=raw).structuredContent
            self.assertAlmostEqual(sc["range_au"], round(want, 2), places=2, msg=raw)   # 名前は2桁に丸める
            self.assertIn(word, sc.get("range_resolved") or "", raw)
            self.assertTrue((sc["figure"].get("verify") or {}).get("ok"), (raw, sc["figure"].get("verify")))
            self.assertIn("表示範囲の決め方", chr(10).join(sc["figure"]["notes"]), raw)

    def test_range_au_fit_follows_the_specified_bodies(self):
        # "fit" は「その呼び出しで指定した天体が全部入る範囲」。イトカワ（a=1.32 AU）なら約1.5 AU。
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(asteroid="イトカワ", range_au="fit").structuredContent
        self.assertTrue(1.2 <= float(sc["range_au"]) <= 2.0, sc["range_au"])
        self.assertIn("指定した天体", sc.get("range_resolved") or "")
        # 指定天体が無いときの "fit" は自動（45 AU 起点）へ戻るだけで、エラーにはしない
        sc2 = solar_system_now(range_au="fit").structuredContent
        self.assertFalse(sc2.get("range_au"))

    def test_unresolvable_range_name_is_rejected_with_candidates(self):
        # 未知の名前は推測せず、候補一覧つきで停止する（勝手な縮尺にしない）。
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(range_au="ドラえもん").structuredContent
        self.assertIn("error", sc)
        self.assertIn("解決できない表示範囲", sc["error"])
        self.assertIn("木星", sc.get("range_targets") or [])

    def test_range_au_below_minimum_is_rejected(self):
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(range_au=0.2).structuredContent
        self.assertIn("error", sc)
        self.assertIn("0.5 AU", sc["error"])

    def test_route_leaving_the_range_falls_back_to_log_and_is_listed(self):
        # ハレー彗星（遠日点 35 AU）は 5 AU の枠に収まらないので線形版は使えない。
        from space_finder_mcp.solar_system import solar_system_now

        sc = solar_system_now(comet="ハレー彗星", route=True, range_au=5).structuredContent
        self.assertTrue(sc["engine"].startswith("simple"), sc["engine"])
        joined = "\n".join(sc["figure"]["notes"])
        self.assertIn("表示範囲5 AU の外にあるため描いていない", joined)
        self.assertTrue(any(o.get("type") == "route_mark" for o in sc["out_of_range"]), sc["out_of_range"])

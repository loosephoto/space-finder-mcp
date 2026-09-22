"""JPL SSD/CNEOS（天体異常系: 火球・接近・衝突リスク）と SWPC フレアイベントの回帰テスト。

実測した API の癖の再発防止:
- Fireball の `energy` は **10^10 J 単位**（kt ではない）。kt なのは `impact-e` だけ
- 0 件のとき Fireball/CAD は **`data` キー自体を返さない**（count と signature のみ）
- `fields` の並びは将来変わりうる → 位置でなくフィールド名で引く
- `data` の値は文字列（"0.079"）。`vel`（速度）や `lat` は null になりうる
- Sentry は **削除済み・未登録でも HTTP 200** を返し、本文の `error` で知らせる
- `limit` は CAD/Fireball にはあるが **Sentry（summary）には無い** → 自分で切る
- SWPC の `xray-flares-7-day.json` はフレアごとに1行。**空配列は「フレアなし」**
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, "src")


_TMP_DIR = None
_OLD_STORE = None


def setUpModule():
    """ツールはカレンダーの蓄積ストアへ書くので、テストでは実ストアに触らない。"""
    global _TMP_DIR, _OLD_STORE
    from space_finder_mcp import calendar_store as store
    _TMP_DIR = tempfile.mkdtemp(prefix="ssd_test_store_")
    _OLD_STORE = (store.STORE_DIR, store.STORE_PATH)
    store.STORE_DIR = _TMP_DIR
    store.STORE_PATH = os.path.join(_TMP_DIR, "calendar_store.json")


def tearDownModule():
    from space_finder_mcp import calendar_store as store
    if _OLD_STORE:
        store.STORE_DIR, store.STORE_PATH = _OLD_STORE
    if _TMP_DIR:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.text = ""
    r.json = lambda: payload

    def _raise():
        if status >= 400:
            raise requests.HTTPError("{}".format(status))
    r.raise_for_status = _raise
    return r


FIREBALL_PAYLOAD = {
    "signature": {"version": "1.2", "source": "NASA/JPL Fireball Data API"},
    "count": "2",
    "fields": ["date", "energy", "impact-e", "lat", "lat-dir", "lon", "lon-dir",
               "alt", "vel", "vx", "vy", "vz"],
    "data": [
        ["2026-09-15 11:26:13", "2.2", "0.079", "37.6", "S", "161.6", "W", "37.0",
         None, "3.0", "4.0", "0.0"],
        ["2026-09-11 10:18:03", "6.1", "0.2", None, None, None, None, "70.0",
         None, None, None, None],
    ],
}

FIREBALL_EMPTY = {"count": "0", "signature": {"version": "1.2", "source": "NASA/JPL Fireball Data API"}}

CAD_PAYLOAD = {
    "signature": {"version": "1.5", "source": "NASA/JPL SBDB Close Approach Data API"},
    "count": 2, "total": 16,
    "fields": ["des", "orbit_id", "jd", "cd", "dist", "dist_min", "dist_max", "v_rel",
               "v_inf", "t_sigma_f", "h", "diameter", "diameter_sigma", "fullname"],
    "data": [
        ["2026 SM9", "1", "2461304.5", "2026-Sep-21 01:02", "0.0063365", "0.00631",
         "0.00636", "13.518673", "13.487", "< 00:01", "27.479", None, None, "  (2026 SM9)"],
        ["2019 OK", "4", "2461330.9", "2026-Oct-16 02:11", "0.02", "0.019", "0.021",
         "24.5", "24.4", "2_06:31", "19.3", "0.057", "0.01", " (2019 OK)"],
    ],
}

SENTRY_SUMMARY = {
    "signature": {"version": "2.0", "source": "NASA/JPL Sentry Data API"},
    "count": 3,
    "data": [
        {"des": "2012 VS76", "fullname": "(2012 VS76)", "ip": "1.9442009e-05",
         "ps_cum": "-5.74", "ps_max": "-6.0", "n_imp": 3, "h": "27.1",
         "diameter": "0.014", "range": "2081-2120", "last_obs": "2012-11-16"},
        {"des": "2000 SG344", "fullname": "(2000 SG344)", "ip": "0.002743395186",
         "ps_cum": "-2.76", "ps_max": "-3.10", "n_imp": 300, "h": "24.79",
         "diameter": "0.037", "range": "2069-2122", "last_obs": "2000-10-03"},
        {"des": "2022 KK2", "fullname": "(2022 KK2)", "ip": "0.0001203297828",
         "ps_cum": "-5.58", "ps_max": "-5.78", "n_imp": 33, "h": "28.45",
         "diameter": "0.0069", "range": "2060-2122", "last_obs": "2022-05-23"},
    ],
}

SENTRY_OBJECT = {
    "signature": {"version": "2.0", "source": "NASA/JPL Sentry Data API"},
    "summary": {"des": "2000 SG344", "fullname": "(2000 SG344)", "ip": "0.002743395186",
                "diameter": "0.0370000000000001", "energy": "1.047e+00", "h": "24.79",
                "n_imp": 300, "darc": "507.40 days", "first_obs": "1999-05-15",
                "last_obs": "2000-10-03", "method": "IOBS", "ps_cum": "-2.76",
                "ps_max": "-3.10"},
    "data": [{"date": "2122-08-04.72", "ip": "1.064e-06", "ps": "-6.42",
              "energy": "1.046e+00"}],
}


class SsdHelpersTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import ssd
        self.ssd = ssd

    def test_fields_are_resolved_by_name_not_position(self):
        """fields の並びが変わっても値が入れ替わらない（位置で決め打ちしない）。"""
        payload = {"fields": ["impact-e", "date"],
                   "data": [["0.079", "2026-09-15 11:26:13"]]}
        rec = self.ssd._records(payload)[0]
        self.assertEqual(rec["impact-e"], "0.079")
        self.assertEqual(rec["date"], "2026-09-15 11:26:13")

    def test_missing_data_key_is_empty_not_error(self):
        """0件のとき API は data キーを返さない（実測）。空として扱う。"""
        self.assertEqual(self.ssd._records({"count": "0"}), [])

    def test_sentry_200_with_error_body_is_detected(self):
        """Sentry は削除済み・未登録でも 200 を返す。error キーで検出する。"""
        removed = {"error": "specified object removed", "removed": "2021-02-21 08:22:28"}
        msg = self.ssd._api_error(removed)
        self.assertIn("removed", msg)
        self.assertIn("2021-02-21", msg)
        self.assertIsNone(self.ssd._api_error({"count": 3, "data": []}))

    def test_uncertainty_and_location_and_diameter(self):
        self.assertEqual(self.ssd._uncertainty_text("< 00:01"), "1分未満")
        self.assertEqual(self.ssd._uncertainty_text("2_06:31"), "± 2日 6時間31分")
        self.assertEqual(self.ssd._uncertainty_text(None), "不明")
        self.assertEqual(self.ssd._uncertainty_text("?"), "?")

        # 位置が未報告（lat/lat-dir が null）→ 架空の座標を作らない
        text = self.ssd._location_text({"lat": None, "lat-dir": None, "lon": None, "lon-dir": None})
        self.assertIn("位置不明", text)
        self.assertNotIn("緯", text)

        # H=27.479 → D[km] = 1329*10^(-H/5)/sqrt(0.14)。約 11 m（アルベド0.14 仮定）
        d = self.ssd._diameter_from_h("27.479")
        self.assertAlmostEqual(d, 11.3, delta=0.5)
        self.assertIsNone(self.ssd._diameter_from_h("?"))

    def test_diameter_text_adds_meters_below_1km(self):
        """Sentry の直径は km。1 km 未満は m 換算も添える（数値から生成）。"""
        self.assertEqual(self.ssd._diameter_text("0.0071"), "0.0071 km（約 7 m）")
        self.assertEqual(self.ssd._diameter_text("0.037"), "0.037 km（約 37 m）")
        self.assertIn("km", self.ssd._diameter_text("12.5"))
        self.assertEqual(self.ssd._diameter_text(None), "不明")

    def test_flare_rank_orders_by_class_then_number(self):
        """フレア規模の比較（X1.0 > M9.9 > C9.9 > B1.0、不明は最下位）。"""
        from space_finder_mcp import swpc
        self.assertGreater(swpc._flare_rank("X1.0"), swpc._flare_rank("M9.9"))
        self.assertGreater(swpc._flare_rank("M9.9"), swpc._flare_rank("C9.9"))
        self.assertGreater(swpc._flare_rank("C1.0"), swpc._flare_rank("?"))
        self.assertEqual(swpc._flare_rank(None), -1.0)


class FireballTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import ssd
        self.ssd = ssd

    def _run(self, payload, **kw):
        with mock.patch.object(self.ssd, "_get_json", lambda *a, **k: payload):
            return self.ssd.fireball_reports.__wrapped__(**kw)

    def test_energy_units_are_radiated_joules_and_impact_kilotons(self):
        """`energy` は 10^10 J（kt ではない）、kt は `impact-e`。取り違えない。"""
        res = self._run(FIREBALL_PAYLOAD)
        self.assertNotIn("error", res.structuredContent)
        first = res.structuredContent["results"][0]
        self.assertAlmostEqual(first["radiated_energy_j"], 2.2e10)
        self.assertAlmostEqual(first["impact_energy_kt"], 0.079)
        self.assertIn("衝突エネルギー 0.079 kt", res.content[0].text)
        self.assertIn("放射エネルギー 2.2e+10 J", res.content[0].text)

    def test_null_location_and_missing_velocity_are_not_invented(self):
        """2件目は lat/経度も速度成分も null → 位置不明・速度を書かない。"""
        res = self._run(FIREBALL_PAYLOAD)
        second = res.structuredContent["results"][1]
        self.assertFalse(second["location_reported"])
        self.assertIsNone(second["entry_speed_km_s"])
        self.assertIn("位置不明", res.content[0].text)
        self.assertNotIn("突入速度 約 0.0 km/s", res.content[0].text)
        # 位置は最大光度時点であり落下地点ではない（誤読させない）
        self.assertIn("落下地点ではありません", res.content[0].text)

    def test_entry_speed_is_computed_from_components(self):
        """vx,vy,vz = (3,4,0) → 5.0 km/s（数値から生成）。"""
        res = self._run(FIREBALL_PAYLOAD)
        self.assertAlmostEqual(res.structuredContent["results"][0]["entry_speed_km_s"], 5.0)
        self.assertIn("突入速度 約 5.0 km/s", res.content[0].text)

    def test_zero_hits_has_no_data_key(self):
        """data キーが無い 0 件応答でも落ちず、「ありませんでした」を返す。"""
        res = self._run(FIREBALL_EMPTY)
        self.assertEqual(res.structuredContent["shown"], 0)
        self.assertIn("ありませんでした", res.content[0].text)

    def test_connection_failure_returns_error_result(self):
        def boom(*a, **k):
            raise requests.ConnectionError("simulated network failure")
        with mock.patch.object(self.ssd, "_get_json", boom):
            res = self.ssd.fireball_reports.__wrapped__()
        self.assertIn("error", res.structuredContent)
        self.assertIn("取得できませんでした", res.content[0].text)

    def test_min_energy_uses_impact_e_min_in_kilotons(self):
        """下限は kt の impact-e-min に渡す（energy-min は 10^10 J 単位なので使わない）。"""
        seen = {}

        def fake(endpoint, params=None):
            seen.update(params or {})
            return FIREBALL_EMPTY
        with mock.patch.object(self.ssd, "_get_json", fake):
            self.ssd.fireball_reports.__wrapped__(min_impact_energy_kt=0.5)
        self.assertEqual(seen["impact-e-min"], "0.5")
        self.assertNotIn("energy-min", seen)


class CloseApproachTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import ssd
        self.ssd = ssd

    def _run(self, payload, **kw):
        with mock.patch.object(self.ssd, "_get_json", lambda *a, **k: payload):
            return self.ssd.neo_close_approach.__wrapped__(**kw)

    def test_distance_is_converted_to_km_and_lunar_distances(self):
        """au → km / 月距離（LD=384,400 km）を併記する。"""
        res = self._run(CAD_PAYLOAD)
        first = res.structuredContent["results"][0]
        self.assertAlmostEqual(first["distance_au"], 0.0063365, places=7)
        self.assertAlmostEqual(first["distance_km"],
                               0.0063365 * self.ssd.AU_KM, delta=1.0)
        self.assertAlmostEqual(first["distance_lunar"],
                               first["distance_km"] / 384400.0, delta=0.01)
        self.assertIn("月距離", res.content[0].text)

    def test_known_diameter_beats_h_estimate(self):
        """既知の直径があればそれを優先し、推定フラグを立てない。"""
        res = self._run(CAD_PAYLOAD)
        known = res.structuredContent["results"][1]
        self.assertAlmostEqual(known["estimated_diameter_m"], 57.0, delta=0.5)
        self.assertFalse(known["diameter_is_estimate"])
        self.assertIn("既知の直径", res.content[0].text)

        est = res.structuredContent["results"][0]
        self.assertTrue(est["diameter_is_estimate"])
        self.assertIn("推定直径", res.content[0].text)
        self.assertIn("アルベド0.14", res.content[0].text)

    def test_total_greater_than_shown_reports_truncation(self):
        """API の total(16) を尊重し、truncated を残す（取りこぼしを黙らない）。"""
        res = self._run(CAD_PAYLOAD)
        self.assertEqual(res.structuredContent["total"], 16)
        self.assertTrue(res.structuredContent["truncated"])
        self.assertIn("全16件中2件", res.content[0].text)

    def test_days_and_distance_are_passed_through(self):
        seen = {}

        def fake(endpoint, params=None):
            seen.update(params or {})
            return CAD_PAYLOAD
        with mock.patch.object(self.ssd, "_get_json", fake):
            self.ssd.neo_close_approach.__wrapped__(days=30, max_distance_au=0.02)
        self.assertEqual(seen["date-max"], "+30")
        self.assertEqual(seen["dist-max"], "0.02")
        self.assertNotIn("pha", seen)

    def test_hazardous_only_sets_pha(self):
        seen = {}

        def fake(endpoint, params=None):
            seen.update(params or {})
            return {"count": 0, "total": 0}
        with mock.patch.object(self.ssd, "_get_json", fake):
            res = self.ssd.neo_close_approach.__wrapped__(hazardous_only=True)
        self.assertEqual(seen["pha"], "true")
        self.assertEqual(res.structuredContent["shown"], 0)

    def test_port_and_timeouts_are_fixed(self):
        """(connect, read) タイムアウトを必ず指定する（規約14）。"""
        self.assertEqual(len(self.ssd.TIMEOUT), 2)
        self.assertLessEqual(self.ssd.TIMEOUT[0], 15)


class ImpactRiskTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import ssd
        self.ssd = ssd

    def _run(self, payload, **kw):
        with mock.patch.object(self.ssd, "_get_json", lambda *a, **k: payload):
            return self.ssd.impact_risk.__wrapped__(**kw)

    def test_summary_is_sorted_by_probability_and_cut_client_side(self):
        """Sentry の summary には limit が無い（400）。自分で並べ替えて切る。"""
        res = self._run(SENTRY_SUMMARY, limit=2)
        results = res.structuredContent["results"]
        self.assertEqual([r["designation"] for r in results], ["2000 SG344", "2022 KK2"])
        self.assertEqual(res.structuredContent["total"], 3)
        self.assertTrue(res.structuredContent["truncated"])

    def test_probability_is_shown_with_odds(self):
        res = self._run(SENTRY_SUMMARY, limit=1)
        self.assertIn("回に1回", res.content[0].text)
        self.assertIn("パレルモスケール", res.content[0].text)

    def test_no_hits_at_all_is_not_an_error(self):
        res = self._run({"count": 0, "data": []})
        self.assertEqual(res.structuredContent["shown"], 0)
        self.assertNotIn("error", res.structuredContent)
        self.assertIn("見つかっていません", res.content[0].text)

    def test_designation_400_tells_user_to_use_designation(self):
        """和名（アポフィス等）は JPL が受け付けない → 推測せず指定方法を案内する。"""
        def boom(endpoint, params=None):
            raise requests.HTTPError(
                "400",
                response=_resp({"code": "400", "message": "invalid designation"}, 400))
        with mock.patch.object(self.ssd, "_get_json", boom):
            res = self.ssd.impact_risk.__wrapped__(designation="アポフィス")
        self.assertIn("error", res.structuredContent)
        self.assertIn("仮符号", res.content[0].text)
        self.assertIn("invalid designation", res.content[0].text)

    def test_removed_object_is_explained_not_reported_as_crash(self):
        """削除済み（HTTP 200 + error）は「リスクなし」を意味しうる旨を返す。"""
        res = self._run({"error": "specified object removed",
                         "removed": "2021-02-21 08:22:28"}, designation="99942")
        self.assertTrue(res.structuredContent.get("not_found"))
        self.assertIn("削除", res.content[0].text)

    def test_object_mode_uses_summary_and_vi_rows(self):
        res = self._run(SENTRY_OBJECT, designation="2000 SG344")
        self.assertEqual(res.structuredContent["mode"], "object")
        self.assertEqual(res.structuredContent["virtual_impactors"], 1)
        self.assertIn("(2000 SG344)", res.content[0].text)
        self.assertIn("観測弧 507.40 days", res.content[0].text)


class SwpcFlaresTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import swpc
        self.swpc = swpc

    def test_flare_events_are_collected_and_rendered(self):
        payload = [
            {"begin_time": "2026-09-20T14:29:00Z", "max_time": "2026-09-20T14:33:00Z",
             "end_time": "2026-09-20T14:40:00Z", "begin_class": "B3.7",
             "max_class": "M1.5", "satellite": 18},
            {"begin_time": "2026-09-21T11:08:00Z", "max_time": "2026-09-21T11:17:00Z",
             "end_time": "2026-09-21T11:25:00Z", "begin_class": "B3.3",
             "max_class": "B6.8", "satellite": 18},
        ]
        with mock.patch.object(self.swpc, "_get_json", lambda path: payload):
            sec = self.swpc.fetch_all.__wrapped__()["sections"]["flares"]
        self.assertEqual(sec["count"], 2)
        self.assertEqual(sec["max_class"], "M1.5")
        lines = self.swpc._sec_lines("flares", sec)
        self.assertTrue(any("M1.5" in ln for ln in lines))
        self.assertTrue(any("2件" in ln for ln in lines))

    def test_empty_flare_list_is_calm_not_failure(self):
        """空配列は「7日間フレアなし」という正当な値（failed にしない）。"""
        with mock.patch.object(self.swpc, "_get_json", lambda path: []):
            data = self.swpc.fetch_all.__wrapped__()
        self.assertIn("flares", data["sections"])
        self.assertEqual(data["sections"]["flares"]["count"], 0)
        self.assertFalse([f for f in data["failed"] if "flares" in f])
        lines = self.swpc._sec_lines("flares", data["sections"]["flares"])
        self.assertIn("静穏", "\n".join(lines))

    def test_non_list_response_is_a_failure(self):
        with mock.patch.object(self.swpc, "_get_json", lambda path: {"error": "nope"}):
            data = self.swpc.fetch_all.__wrapped__()
        self.assertTrue([f for f in data["failed"] if "flares" in f])

    def test_advice_mentions_m_class_flare_when_geomagnetic_is_calm(self):
        sections = {"kp": {"current": 2},
                    "scales": {"current": {"G": {"scale": "0"}}, "forecast": {}},
                    "flares": {"max_class": "M2.0", "max_time": "2026-09-20T14:33:00Z"}}
        self.assertIn("M2.0", self.swpc._advice(sections))

    def test_advice_stays_calm_for_small_flares(self):
        sections = {"kp": {"current": 1},
                    "scales": {"current": {"G": {"scale": "0"}}, "forecast": {}},
                    "flares": {"max_class": "C9.9", "max_time": "2026-09-20T14:33:00Z"}}
        self.assertIn("静穏", self.swpc._advice(sections))


if __name__ == "__main__":
    unittest.main()

class AnomalyCalendarStoreTests(unittest.TestCase):
    """天体異常系の呼び出し結果がカレンダーの蓄積ストアへ反映される（v0.34.0）。"""

    def setUp(self):
        from space_finder_mcp import calendar_store as store
        from space_finder_mcp import ssd
        self.store, self.ssd = store, ssd
        store.save(store._blank())                      # 各テストは空のストアから始める

    def test_event_time_parses_both_jpl_formats(self):
        """CAD は英語月名・Fireball は数字（秒あり）。ロケール非依存で解く。"""
        cad = self.ssd._parse_event_time("2026-Sep-21 01:02")
        num = self.ssd._parse_event_time("2026-09-15 11:26:13")
        self.assertEqual((cad.year, cad.month, cad.day, cad.hour, cad.minute),
                         (2026, 9, 21, 1, 2))
        self.assertEqual(num.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-15 11:26:13")
        for junk in ("", None, "not a date", "2026-13-45 99:99", "20260915", "2026-Xxx-01 00:00"):
            self.assertIsNone(self.ssd._parse_event_time(junk))

    def test_fireball_call_stores_records(self):
        with mock.patch.object(self.ssd, "_get_json", return_value=FIREBALL_PAYLOAD):
            res = self.ssd.fireball_reports.__wrapped__(days=30)
        self.assertEqual(res.structuredContent["calendar_stored"], 2)
        recs = self.store.records(kinds={"fireball"})
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["start_utc"], "2026-09-15T11:26:13Z")   # UTC のまま蓄積
        self.assertIn("過去の観測記録", recs[0]["detail"])
        self.assertIn("落下地点ではない", recs[0]["detail"])
        self.assertIn("fireball_reports の呼び出しで蓄積", recs[0]["source"])   # 来歴

    def test_neo_call_stores_records_with_tdb_note(self):
        with mock.patch.object(self.ssd, "_get_json", return_value=CAD_PAYLOAD):
            res = self.ssd.neo_close_approach.__wrapped__(days=7)
        self.assertEqual(res.structuredContent["calendar_stored"], 2)
        recs = self.store.records(kinds={"neo"})
        self.assertEqual(len({r["key"] for r in recs}), 2)            # キーが衝突しない
        self.assertTrue(all(r["key"].startswith("neo:") for r in recs))
        self.assertTrue(all(r["start_utc"].endswith("Z") for r in recs))
        self.assertIn("TDB", recs[0]["detail"])
        self.assertIn("接近 ≠ 衝突", recs[0]["detail"])
        self.assertIn("neo_close_approach の呼び出しで蓄積", recs[0]["source"])

    def test_unparsable_rows_are_skipped_not_crashed(self):
        payload = {"fields": ["des", "cd", "dist"], "data": [["X", "いつか", "0.01"]]}
        recs = self.ssd._neo_calendar_records(
            [{"designation": "X", "close_approach": "いつか"}], days=7, dist=0.05)
        self.assertEqual(recs, [])
        self.assertEqual(payload["fields"][0], "des")                 # 入力は壊さない

    def test_errors_are_not_stored(self):
        with mock.patch.object(self.ssd, "_get_json",
                               side_effect=requests.ConnectionError("boom")):
            res = self.ssd.fireball_reports.__wrapped__(days=30)
        self.assertIn("error", res.structuredContent)
        self.assertEqual(self.store.records(), [])                    # エラーは蓄積しない

    def test_zero_results_keeps_provenance_only(self):
        with mock.patch.object(self.ssd, "_get_json", return_value=FIREBALL_EMPTY):
            res = self.ssd.fireball_reports.__wrapped__(days=30)
        self.assertEqual(res.structuredContent["calendar_stored"], 0)
        self.assertEqual(self.store.records(), [])
        self.assertIn("cneos-fireball", self.store.stats()["sources"])   # 0件でも来歴は残す

    def test_impact_risk_stores_nothing(self):
        """Sentry の衝突確率は日付が無い（数十年〜百年の幅）→ カレンダーに置かない。"""
        with mock.patch.object(self.ssd, "_get_json", return_value=SENTRY_SUMMARY):
            self.ssd.impact_risk.__wrapped__()
        self.assertEqual(self.store.records(), [])

    def test_store_failure_is_reported_not_raised(self):
        with mock.patch.object(self.ssd, "_get_json", return_value=FIREBALL_PAYLOAD),                 mock.patch("space_finder_mcp.calendar_store.upsert", side_effect=OSError("disk")):
            res = self.ssd.fireball_reports.__wrapped__(days=30)
        self.assertEqual(res.structuredContent["calendar_stored"], 0)
        self.assertIn("カレンダーへの蓄積はできませんでした", res.content[0].text)
        self.assertEqual(res.structuredContent["shown"], 2)          # 本体の結果は返る

    def test_calendar_kinds_include_anomalies(self):
        """図の凡例・notes・kinds 検証は KIND_ORDER / KINDS の1表から出る（色は重複させない）。"""
        for key in ("neo", "fireball"):
            self.assertIn(key, self.store.KIND_ORDER)
            self.assertIn(key, self.store.KINDS)
        colors = [self.store.KINDS[k][1] for k in self.store.KIND_ORDER]
        self.assertEqual(len(colors), len(set(colors)))


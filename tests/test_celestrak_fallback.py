"""CelesTrak 遮断時に代替 TLE 源へ切り替わることの回帰テスト（ネットワーク不要）。

実測 2026-09: CelesTrak の gp.php は curl でも 20 秒以上無応答（blackhole）になり、
位置系ツールが全滅した。TLE は同じ衛星でもエポックにより要素値が異なるので、遮断中は公開ミラー（tle.ivanstanojevic.me / db.satnogs.org）へ切り替える。
ここでは通信をすべて差し替えて、切り替え・出典表示・fail fast・グループ検索の案内を検証する。
"""
import sys
import time
import unittest

import requests

sys.path.insert(0, "src")

TLE1 = "1 29479U 06041A   26265.61027173  .00000358  00000+0  69728-4 0  9991"
TLE2 = "2 29479  98.0550 280.3324 0016952 343.4387  16.6263 14.68726603 69177"


class FakeResp:
    """requests.Response の最小代役（status_code / raise_for_status / json / text）。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.url = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("{} Client Error".format(self.status_code), response=self)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class CelestrakFallbackTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import celestrak

        self.c = celestrak
        self._old_get = requests.get
        self._old_until = celestrak._COOLDOWN_UNTIL
        self.c._COOLDOWN_UNTIL = 0.0
        self.c._FALLBACK_CACHE.clear()
        self.c._FALLBACK_DOWN_UNTIL.clear()
        self.c._FALLBACK_DOWN_REASON.clear()
        self.c._tle_text_cached.cache_clear()
        self.c._fetch_tle_cached.cache_clear()

    def tearDown(self):
        requests.get = self._old_get
        self.c._COOLDOWN_UNTIL = self._old_until
        self.c._COOLDOWN_LAST = ""
        self.c._FALLBACK_CACHE.clear()
        self.c._FALLBACK_DOWN_UNTIL.clear()
        self.c._FALLBACK_DOWN_REASON.clear()

    # ---- 差し替えヘルパ ----
    def _patch(self, celestrak=None, mirror=None, satnogs=None):
        """URL ごとに応答（FakeResp / 例外）を割り当てて requests.get を差し替える。"""
        calls = []

        def fake(url, **kwargs):
            calls.append(url)
            if "celestrak.org" in url:
                if isinstance(celestrak, Exception):
                    raise celestrak
                return celestrak
            if "ivanstanojevic" in url:
                if isinstance(mirror, Exception):
                    raise mirror
                return mirror
            if "satnogs" in url:
                if isinstance(satnogs, Exception):
                    raise satnogs
                return satnogs
            raise AssertionError("unexpected url: " + url)

        requests.get = fake
        return calls

    # ---- 代替源への切り替え ----
    def test_fetch_tle_ex_uses_mirror_when_celestrak_blocked(self):
        self._patch(celestrak=requests.ConnectionError("simulated blackhole"),
                    mirror=FakeResp(payload={"name": "HINODE (SOLAR-B)",
                                             "line1": TLE1, "line2": TLE2}))
        tle, source = self.c.fetch_tle_ex(norad_id=29479)
        self.assertEqual(tle[0], "HINODE (SOLAR-B)")
        self.assertEqual((tle[1], tle[2]), (TLE1, TLE2))
        self.assertEqual(source, "tle.ivanstanojevic.me")
        self.assertIn("代替源", self.c.source_label(source))
        # 出典つきラッパでない方（従来 API）も同じ TLE を返す
        self.assertEqual(self.c.fetch_tle(norad_id=29479)[1], TLE1)

    def test_fallback_uses_satnogs_when_first_mirror_is_down(self):
        calls = self._patch(celestrak=requests.ConnectionError("blackhole"),
                            mirror=requests.ConnectionError("blackhole"),
                            satnogs=FakeResp(payload=[{"tle0": "0 HINODE (SOLAR B)",
                                                       "tle1": TLE1, "tle2": TLE2}]))
        tle, source = self.c.fetch_tle_ex(norad_id=29479)
        self.assertEqual(source, "db.satnogs.org")
        self.assertEqual(tle[2], TLE2)
        self.assertEqual(len([u for u in calls if "celestrak.org" in u]), 1)

    def test_celestrak_404_also_consults_mirror(self):
        """CelesTrak が「該当なし(404)」でも、ミラーに有ればそれを返す。"""
        self._patch(celestrak=FakeResp(status_code=404),
                    mirror=FakeResp(payload={"name": "HINODE", "line1": TLE1, "line2": TLE2}))
        tle, source = self.c.fetch_tle_ex(norad_id=29479)
        self.assertEqual(source, "tle.ivanstanojevic.me")
        self.assertEqual(tle[0], "HINODE")

    def test_fallback_fails_fast_after_all_sources_are_down(self):
        calls = self._patch(celestrak=requests.ConnectionError("blackhole"),
                            mirror=requests.ConnectionError("blackhole"),
                            satnogs=requests.ConnectionError("blackhole"))
        with self.assertRaises(requests.RequestException):
            self.c.fetch_tle_ex(norad_id=29479)
        first = len(calls)
        self.assertGreaterEqual(first, 2)          # CelesTrak + 代替源は試す
        started = time.perf_counter()
        with self.assertRaises(requests.RequestException):
            self.c.fetch_tle_ex(norad_id=29479)    # 2回目は HTTP を投げない
        self.assertEqual(len(calls), first, "遮断を記憶せず再び HTTP を投げています")
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertTrue(self.c.fallback_status(), "代替源の遮断が記録されていません")

    def test_tle_elements_are_parsed_from_tle_lines(self):
        """代替源は要素の JSON を返さないので、TLE 本体から列位置で切り出す。"""
        e = self.c.tle_elements(TLE1, TLE2)
        self.assertEqual(e["norad_id"], "29479")
        self.assertEqual(e["intl_designator"], "06041A")
        self.assertEqual(e["inclination_deg"], "98.0550")
        self.assertEqual(e["ra_of_asc_node_deg"], "280.3324")
        self.assertEqual(e["eccentricity"], "0.0016952")
        self.assertEqual(e["arg_perigee_deg"], "343.4387")
        self.assertEqual(e["mean_anomaly_deg"], "16.6263")
        self.assertEqual(e["mean_motion_rev_day"], "14.68726603")
        self.assertTrue(e["epoch"].startswith("2026-09-"), e["epoch"])
        self.assertEqual(self.c.tle_epoch_iso("2 29479"), "")   # 壊れた行でも例外を出さない

    # ---- ツール経路（sat_tle / tiangong / satellite_map / sky_overlay）----
    @staticmethod
    def _tool_fn(name):
        from space_finder_mcp.server import mcp

        tool = mcp._tool_manager._tools[name]
        return getattr(tool.fn, "sync_fn", tool.fn)

    def test_sat_tle_returns_records_from_mirror_with_source(self):
        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload={"name": "HINODE (SOLAR-B)",
                                             "line1": TLE1, "line2": TLE2}))
        res = self._tool_fn("sat_tle")(name="hinode")
        sc = res.structuredContent
        self.assertEqual(sc["source"], "tle.ivanstanojevic.me")
        self.assertEqual(sc["results"][0]["object_name"], "HINODE (SOLAR-B)")
        self.assertEqual(sc["results"][0]["inclination_deg"], "98.0550")
        self.assertIn(TLE1, sc["results"][0]["tle"])
        text = res.content[0].text
        self.assertIn("代替源", text)
        self.assertIn("出典:", text)

    def test_sat_tle_group_query_explains_that_celestrak_is_required(self):
        """グループ検索は代替源に無いので、例外ではなく案内付きのエラー結果を返す。"""
        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=requests.ConnectionError("blackhole"),
                    satnogs=requests.ConnectionError("blackhole"))
        res = self._tool_fn("sat_tle")(group="stations")
        self.assertIn("error", res.structuredContent)
        self.assertIn("GROUP=stations", res.content[0].text)
        self.assertIn("代替源", res.content[0].text)
        self.assertIn("fallback", res.structuredContent)

    def test_sat_ground_track_plumbs_mirror_source(self):
        """satellite_map は TLE の出典を受け取り、CelesTrak と誤記しない。"""
        from space_finder_mcp import satellite_map

        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload={"name": "HINODE (SOLAR-B)",
                                             "line1": TLE1, "line2": TLE2}))
        got = satellite_map._fetch_tle2(29479)
        self.assertEqual(got[:3], ("HINODE (SOLAR-B)", TLE1, TLE2))
        self.assertEqual(got[3], "tle.ivanstanojevic.me")
        self.assertIn("代替源", self.c.source_label(got[3]))

    def test_tiangong_now_uses_mirror_and_reports_source(self):
        from space_finder_mcp import tiangong

        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload={"name": "TIANGONG", "line1": TLE1, "line2": TLE2}))
        tiangong._fetch_tiangong_tle.cache_clear()
        res = self._tool_fn("tiangong_now")()
        self.assertEqual(res.structuredContent.get("tle_source"), "tle.ivanstanojevic.me")
        self.assertNotIn("error", res.structuredContent)
        self.assertIn("代替源", res.content[0].text)
        tiangong._fetch_tiangong_tle.cache_clear()

    def test_sky_overlay_fetch_tle_returns_source(self):
        from space_finder_mcp import sky_overlay

        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload={"name": "HINODE", "line1": TLE1, "line2": TLE2}))
        tle, source = sky_overlay._fetch_tle(29479)
        self.assertEqual(tle, (TLE1, TLE2))
        self.assertIn(source, sky_overlay._tle_source_summary({29479: source}))
        self.assertEqual(sky_overlay._tle_source_summary({29479: "celestrak.org"}),
                         "CelesTrak TLE")

    def test_sat_tle_404_consults_mirror(self):
        """CelesTrak が「該当なし(404)」でもミラーに有れば返す（名前検索の取りこぼし対策）。"""
        self._patch(celestrak=FakeResp(status_code=404),
                    mirror=FakeResp(payload={"name": "HINODE (SOLAR-B)",
                                             "line1": TLE1, "line2": TLE2}))
        res = self._tool_fn("sat_tle")(norad_id=29479)
        self.assertEqual(res.structuredContent["source"], "tle.ivanstanojevic.me")
        self.assertEqual(res.structuredContent["shown"], 1)

    def test_mirror_name_search_warns_about_stale_epochs(self):
        """代替源の名前検索は古いエポックを混ぜることがあるので、件数を数えて注記する。"""
        old_tle1 = "1 44713U 19074A   23361.79593842  .00010000  00000+0  15000-3 0  9990"
        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload={"member": [
                        {"name": "STARLINK-1007", "line1": old_tle1, "line2": TLE2},
                        {"name": "STARLINK-3093", "line1": TLE1, "line2": TLE2},
                    ]}))
        res = self._tool_fn("sat_tle")(name="starlink")
        text = res.content[0].text
        self.assertIn("古いエポック", text)
        self.assertIn("2 件中 1 件", text)
        self.assertIn("古いエポック", res.structuredContent.get("note", ""))
        # 新しい方だけの検索結果では注記を出さない
        records = [{"epoch": self.c.tle_epoch_iso(TLE1)}]
        self.assertEqual(self.c.stale_epoch_note(records), "")

    def test_fetch_tle_ex_does_not_leak_exception_shape(self):
        """代替源が JSON でない応答を返しても例外は RequestException に正規化される。"""
        self._patch(celestrak=requests.ConnectionError("blackhole"),
                    mirror=FakeResp(payload=None, text="<html>maintenance</html>"))
        with self.assertRaises(requests.RequestException):
            self.c._get_json("https://tle.ivanstanojevic.me/api/tle/29479",
                             "tle.ivanstanojevic.me")

    def test_name_search_during_mirror_cooldown_is_not_cached(self):
        host = "tle.ivanstanojevic.me"
        self.c._FALLBACK_DOWN_UNTIL[host] = time.time() + 60
        self.assertEqual(self.c._fallback_name("probe-name", 5), [])
        key = ("name", "probe-name", 5)
        self.assertNotIn(key, self.c._FALLBACK_CACHE)

        self.c._FALLBACK_DOWN_UNTIL.pop(host, None)
        self._patch(mirror=FakeResp(payload={"member": [
            {"name": "PROBE-NAME", "line1": TLE1, "line2": TLE2},
        ]}))
        self.assertEqual(len(self.c._fallback_name("probe-name", 5)), 1)


    def test_fallback_name_cache_has_a_fixed_size_limit(self):
        from unittest.mock import patch

        payload = {"member": [{"name": "TEST", "line1": TLE1, "line2": TLE2}]}
        with patch.object(self.c, "_get_json", return_value=payload):
            for i in range(300):
                self.c._fallback_name("unique-{}".format(i), 5)
        self.assertLessEqual(len(self.c._FALLBACK_CACHE), 256)

    def test_catnr_fallback_warns_when_tle_epoch_is_stale(self):
        from unittest.mock import patch

        old_line1 = "1 29479U 06041A   23361.79593842  .00000358  00000+0  69728-4 0  9991"
        old_line2 = "2 29479  98.0550 280.3324 0016952 343.4387  16.6263 14.68726603 69177"
        with patch.object(self.c, "_fallback_catnr",
                          return_value=("HINODE", old_line1, old_line2, "tle.ivanstanojevic.me")):
            result = self.c._fallback_sat_tle({"CATNR": 29479}, 5)
        self.assertIn("古いエポック", result[2])
        self.assertIn("30 日", result[2])
        self.assertNotIn("提供元の違いで値は変わりません", result[2])

    def test_stale_epoch_warning_states_cutoff_and_oldest_separately(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        records = [{"epoch": (now - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")},
                   {"epoch": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")}]
        note = self.c.stale_epoch_note(records)
        self.assertIn("30 日", note)
        self.assertIn("最古", note)

    def test_404_mirror_result_does_not_claim_celestrak_is_blocked(self):
        self._patch(celestrak=FakeResp(status_code=404),
                    mirror=FakeResp(payload={"name": "HINODE", "line1": TLE1, "line2": TLE2}))
        res = self._tool_fn("sat_tle")(norad_id=29479)
        self.assertNotIn("遮断中", res.structuredContent["note"])
        self.assertIn("見つからず", res.structuredContent["note"])

    def test_sky_overlay_summary_handles_missing_and_mixed_sources(self):
        from space_finder_mcp import sky_overlay

        self.assertIn("未取得", sky_overlay._tle_source_summary({}))
        mixed = sky_overlay._tle_source_summary({"iss": "celestrak.org",
                                                 "tiangong": "db.satnogs.org"})
        self.assertIn("celestrak.org", mixed)
        self.assertIn("db.satnogs.org", mixed)
        self.assertNotIn("遮断中", mixed)


    def test_satellite_map_error_lists_all_tle_sources_tried(self):
        self._patch(celestrak=requests.ConnectionError("primary offline"),
                    mirror=requests.ConnectionError("mirror offline"),
                    satnogs=requests.ConnectionError("database offline"))
        res = self._tool_fn("sat_ground_track")(norad_id=29479, minutes=5)
        self.assertEqual(res.structuredContent.get("sources_tried"),
                         list(self.c.TLE_SOURCE_ORDER))
        self.assertNotEqual(res.structuredContent.get("source"), "celestrak.org")

    def test_tiangong_error_does_not_attribute_all_failures_to_celestrak(self):
        from space_finder_mcp import tiangong

        self._patch(celestrak=requests.ConnectionError("primary offline"),
                    mirror=requests.ConnectionError("mirror offline"),
                    satnogs=requests.ConnectionError("database offline"))
        tiangong._fetch_tiangong_tle.cache_clear()
        try:
            res = self._tool_fn("tiangong_now")()
            self.assertEqual(res.structuredContent.get("sources_tried"),
                             list(self.c.TLE_SOURCE_ORDER))
            self.assertNotEqual(res.structuredContent.get("source"), "celestrak.org")
            self.assertNotIn("CelesTrak への接続に失敗", res.content[0].text)
        finally:
            tiangong._fetch_tiangong_tle.cache_clear()

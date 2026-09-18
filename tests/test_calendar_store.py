"""宇宙・天文カレンダー（蓄積ストア／ユーザー予定）の回帰テスト。

実バグ（時刻付きの予定が終日になる・repeat の展開で例外・年末プレースホルダの混入・
削除が復活する・ストアが Temp に置かれる）の再発防止。
"""
import datetime as dt
import os
import sys
import tempfile
import unittest

import requests

sys.path.insert(0, "src")


class CalendarStoreTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import calendar_store as store
        self.store = store
        self._old = store.STORE_PATH
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        store.STORE_PATH = path
        self.path = path

    def tearDown(self):
        self.store.STORE_PATH = self._old
        if os.path.exists(self.path):
            os.remove(self.path)

    def _rec(self, key="ll2:1", start="2026-10-20T04:41:00Z", title="MMX", **kw):
        rec = {"key": key, "kind": "launch", "title": title, "start_utc": start, "solid": True,
               "source": "Launch Library 2"}
        rec.update(kw)
        return rec

    def test_store_is_not_under_temp(self):
        """予定が消えないよう、保存先は %LOCALAPPDATA% 直下（cache の Temp 配下ではない）。"""
        from space_finder_mcp import calendar_store as store
        self.assertIn(os.environ.get("LOCALAPPDATA", "."), store.STORE_DIR)
        self.assertNotIn("Temp", store.STORE_DIR)

    def test_upsert_keeps_first_seen_and_increments_revision(self):
        self.store.upsert([self._rec(title="旧")], "ll2:2026-10", window="ll2:2026-10")
        self.store.upsert([self._rec(title="新")], "ll2:2026-10", window="ll2:2026-10")
        rec = [r for r in self.store.records() if r["key"] == "ll2:1"][0]
        self.assertEqual(rec["title"], "新")
        self.assertEqual(rec["revision"], 2)
        self.assertLessEqual(rec["first_seen"], rec["last_seen"])

    def test_partial_fetch_is_not_treated_as_covered(self):
        """complete=False（部分的失敗）を「窓を覆った」と見なさない＝穴を凍結しない。"""
        self.store.upsert([self._rec()], "ll2:2026-10", window="ll2:2026-10", complete=False)
        self.assertFalse(self.store.source_is_fresh("ll2:2026-10", 3600, window="ll2:2026-10"))

    def test_year_end_placeholder_detection(self):
        """LL2/Nasa が日付未定を年末に置く実測に対する共通判定。"""
        for s in ("2026-12-31T21:18:00Z", "2027-12-30T00:00:00Z", "2026-01-01T00:00:00Z"):
            self.assertTrue(self.store.is_year_end_placeholder(s), s)
        for s in ("2026-10-20T04:41:00Z", "2026-12-29T00:00:00Z", "2027-01-02T00:00:00Z"):
            self.assertFalse(self.store.is_year_end_placeholder(s), s)

    def test_prune_keeps_user_events(self):
        self.store.upsert([self._rec(start="2020-01-01T00:00:00Z")], "ll2:old", window="ll2:old")
        self.store.user_add("古い予定", dt.date(2020, 1, 1))
        removed = self.store.prune(keep_past_days=45)
        self.assertGreaterEqual(removed, 1)
        left = self.store.records()
        self.assertFalse([r for r in left if r["kind"] == "launch"])
        self.assertTrue([r for r in left if r["kind"] == "user"])   # 予定は永久に残る

    def test_user_event_not_resurrected_by_upsert(self):
        rec, err = self.store.user_add("消す予定", dt.date(2026, 10, 5))
        self.assertIsNone(err)
        removed, cands, err = self.store.user_remove(key=rec["key"])
        self.assertIsNotNone(removed)
        self.store.upsert([dict(rec)], "user", complete=True)        # 古いコピーが再流入
        self.assertEqual(self.store.records(), [])                   # tombstone で消えたまま

    def test_user_remove_is_idempotent_and_ambiguous_stops(self):
        rec, _ = self.store.user_add("観望会", dt.date(2026, 10, 17), time_value="19:30")
        removed, cands, err = self.store.user_remove(key=rec["key"])
        self.assertTrue(removed)
        again, cands2, err2 = self.store.user_remove(key=rec["key"])
        self.assertIsNone(again)                                     # 冪等（エラーにしない）
        self.assertIsNone(err2)
        self.store.user_add("重複", dt.date(2026, 10, 5))
        self.store.user_add("重複", dt.date(2026, 10, 6))
        none_removed, cands3, _ = self.store.user_remove(title="重複")
        self.assertIsNone(none_removed)
        self.assertEqual(len(cands3), 2)                             # 推測して消さず候補提示
        self.assertEqual(len(self.store.records(kinds={"user"})), 2)

    def test_repeat_expansion(self):
        rec, _ = self.store.user_add("毎週の会", dt.date(2026, 10, 8), time_value="9:00", repeat="weekly")
        occ = self.store.expand_user_occurrences(rec, 2026, 10)
        self.assertEqual([s.day for s, _ in occ], [1, 8, 15, 22, 29])   # 木曜が5回
        rec2, _ = self.store.user_add("単発", dt.date(2026, 10, 8))
        self.assertEqual(len(self.store.expand_user_occurrences(rec2, 2026, 10)), 1)
        self.assertEqual(len(self.store.expand_user_occurrences(rec2, 2026, 11)), 0)

    def test_date_and_time_parsing_is_defensive(self):
        p = self.store.parse_local_date
        self.assertEqual(p("2026-10-24"), dt.date(2026, 10, 24))
        self.assertEqual(p("2026年10月24日"), dt.date(2026, 10, 24))
        self.assertEqual(p("10/24", today=dt.date(2026, 9, 17)), dt.date(2026, 10, 24))
        self.assertIsNone(p("3/1", today=dt.date(2026, 9, 17)))       # 過去すぎる→年を要求
        self.assertIsNone(p("2026-13-40"))
        self.assertIsNone(p(""))
        self.assertEqual(self.store.parse_local_time("10時30分"), (10, 30))
        self.assertEqual(self.store.parse_local_time("9:05"), (9, 5))
        self.assertIsNone(self.store.parse_local_time("25:00"))
        self.assertIsNone(self.store.parse_local_time("あさ"))


class CalendarToolTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import calendar_store as store
        from space_finder_mcp import space_calendar as cal
        self.store, self.cal = store, cal
        self._old = store.STORE_PATH
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        store.STORE_PATH = path
        self.path = path

    def tearDown(self):
        self.store.STORE_PATH = self._old
        if os.path.exists(self.path):
            os.remove(self.path)

    def _put(self, recs, src="ll2:2026-10"):
        self.store.upsert(recs, src, window=src, complete=True)

    def test_undated_launch_goes_to_tray_not_date_cell(self):
        """日付未定（solid=False）を日付セルに描かない＝架空の打ち上げを出さない。"""
        self._put([{"key": "ll2:a", "kind": "launch", "title": "TBD ロケット",
                    "start_utc": "2026-10-31T00:00:00Z", "solid": False, "precision": "Month"}])
        drawn, tray = self.cal._assemble(2026, 10, 9.0, None, {})
        self.assertEqual(drawn, [])
        self.assertEqual(len(tray), 1)

    def test_user_event_is_floating_local(self):
        """place を変えても予定の日付が動かない（打ち上げは動く）。"""
        self.store.user_add("予定", dt.date(2026, 10, 23), end_date=dt.date(2026, 10, 26))
        self._put([{"key": "ll2:b", "kind": "launch", "title": "X",
                    "start_utc": "2026-10-07T00:00:00Z", "solid": True}])
        tokyo, _ = self.cal._assemble(2026, 10, 9.0, None, {})
        hawaii, _ = self.cal._assemble(2026, 10, -10.0, None, {})
        u_t = [(e["date"], e["title"]) for e in tokyo if e["kind"] == "user"]
        u_h = [(e["date"], e["title"]) for e in hawaii if e["kind"] == "user"]
        self.assertEqual(u_t, u_h)                                    # 予定は動かない
        l_t = [e["date"] for e in tokyo if e["kind"] == "launch"]
        l_h = [e["date"] for e in hawaii if e["kind"] == "launch"]
        self.assertNotEqual(l_t, l_h)                                 # 打ち上げは現地時刻で動く

    def test_timed_user_event_is_not_all_day(self):
        """時刻付きの予定が「終日」になるバグの再発防止。"""
        self.store.user_add("観望会", dt.date(2026, 10, 17), time_value="19:30")
        drawn, _ = self.cal._assemble(2026, 10, 9.0, None, {})
        ev = [e for e in drawn if e["kind"] == "user"][0]
        self.assertFalse(ev["all_day"])
        self.assertEqual(ev["dt"].strftime("%H:%M"), "19:30")

    def test_repeat_is_visible_in_listing(self):
        """繰り返し予定は各 occurrence が同じ id を持ち、本文で「毎週」等が分かる。"""
        rec, _ = self.store.user_add("毎週の会", dt.date(2026, 10, 8), time_value="9:00", repeat="weekly")
        drawn, _ = self.cal._assemble(2026, 10, 9.0, None, {})
        occ = [e for e in drawn if e["kind"] == "user"]
        self.assertEqual(len(occ), 5)
        self.assertEqual({e["key"] for e in occ}, {rec["key"]})
        self.assertIn("毎週", self.cal._event_line(occ[0]))

    def test_single_day_public_event_has_no_span(self):
        """単日の公開イベントに「〜同日」を出さない。"""
        self.store.upsert([{"key": "public:u", "kind": "public", "title": "特別公開",
                            "start_local": "2026-10-03T00:00", "end_local": "2026-10-03",
                            "all_day": True, "certainty": "unverified"}], "public:2026",
                          window="2026", complete=True)
        drawn, _ = self.cal._assemble(2026, 10, 9.0, None, {})
        line = self.cal._event_line(drawn[0])
        self.assertNotIn("〜", line)
        self.assertIn("終日", line)

    def test_space_calendar_rejects_bad_kinds_and_place(self):
        r = self.cal.space_calendar(year=2026, month=10, kinds="launch,unknown")
        self.assertIn("error", r.structuredContent)
        r2 = self.cal.space_calendar(year=2026, month=10, place="そんな場所はない")
        self.assertIn("error", r2.structuredContent)

    def test_calendar_event_add_requires_parseable_date(self):
        r = self.cal.calendar_event_add(title="x", date="3/1")
        self.assertIn("error", r.structuredContent)
        r2 = self.cal.calendar_event_add(title="", date="2026-10-01")
        self.assertIn("error", r2.structuredContent)

    def test_offline_fetch_failures_do_not_raise(self):
        """接続不可でも例外をツール外へ漏らさない（取得層は (records, error) を返す）。"""
        old_get = requests.get

        def boom(*a, **k):
            raise requests.ConnectionError("offline")

        requests.get = boom
        try:
            rows, err = self.cal._ll2_fetch(dt.date(2026, 10, 1), dt.date(2026, 11, 1))
            self.assertEqual(rows, [])
            self.assertIsInstance(err, str)
            pub, perr = self.cal._public_events(2026, 10)
            self.assertEqual(pub, [])
            self.assertIsInstance(perr, str)
        finally:
            requests.get = old_get

    def test_ll2_429_is_remembered_and_fails_fast(self):
        """429 を記憶して以降は HTTP を出さない（匿名枠の枯渇を並列呼び出しに波及させない）。"""
        old_get = requests.get
        calls = []

        class R:
            status_code = 429
            headers = {"Retry-After": "104"}

        def boom(*a, **k):
            calls.append(1)
            return R()

        from space_finder_mcp import launch
        requests.get = boom
        try:
            launch.reset_blocked()
            _, err = self.cal._ll2_fetch(dt.date(2026, 10, 1), dt.date(2026, 11, 1))
            self.assertIsInstance(err, str)
            _, err2 = self.cal._ll2_fetch(dt.date(2026, 11, 1), dt.date(2026, 12, 1))
            self.assertIn("429", err2)
            self.assertEqual(len(calls), 1)          # 2回目は HTTP を出していない
        finally:
            requests.get = old_get
            launch.reset_blocked()

    def test_warm_calendar_needs_no_network(self):
        """蓄積済みなら space_calendar は1回も HTTP を出さない（read-through の検証）。

        打ち上げ・公開イベント・計算値のソースをすべて「TTL 内・complete」にした状態で
        requests.get を禁止し、それでも描画できることを確かめる。
        """
        from space_finder_mcp.solar_eclipse import _local_tz
        _local_tz.__dict__.setdefault("_c", {})[(35.7, 139.7)] = 9.0     # 東京の TZ を先に埋める
        S = self.store
        for src, ttl in (("ll2:2026-09", S.TTL_LAUNCH_CURRENT), ("ll2:2026-10", S.TTL_LAUNCH_FUTURE),
                         ("ll2:2026-11", S.TTL_LAUNCH_FUTURE), ("ll2:2026-12", S.TTL_LAUNCH_FUTURE)):
            self.store.upsert([], src, window=src, complete=True, ttl=ttl)
        self.store.upsert([{"key": "ll2:w", "kind": "launch", "title": "MMX", "solid": True,
                            "start_utc": "2026-10-19T19:41:00Z"}], "ll2:2026-10",
                          window="ll2:2026-10", complete=True, ttl=S.TTL_LAUNCH_FUTURE)
        self.store.upsert([], "public:2026", window="2026", complete=True, ttl=S.TTL_PUBLIC)
        self.store.upsert([{"key": "computed:phase:x", "kind": "sky", "title": "満月", "solid": True,
                            "start_utc": "2026-10-26T04:11:00Z"}], "computed:2026-10",
                          window="v1", complete=True, ttl=S.TTL_NEVER)
        self.store.kv_put("corroboration_map_v1", {})

        def no_http(*a, **k):
            raise AssertionError("蓄積済みなのに HTTP を出した")

        old_get = requests.get
        requests.get = no_http
        try:
            r = self.cal.space_calendar(year=2026, month=10, place="東京")
        finally:
            requests.get = old_get
        self.assertNotIn("error", r.structuredContent)
        self.assertTrue(r.structuredContent["figure"]["verify"]["ok"])
        self.assertEqual(r.structuredContent["warnings"], [])
        self.assertTrue(r.structuredContent["image_path"])

    def test_corroboration_matches_nasa_list(self):
        """NASA 公式リスト（API）と突き合わせて公式URLを足す。"""
        mapping = self.cal._corroboration_map(
            [{"key": "ll2:1", "title": "Crew-13"}],
            [{"slug": "nasas-spacex-crew-13", "title": "NASA's SpaceX Crew-13",
              "link": "https://www.nasa.gov/event/nasas-spacex-crew-13/"}])
        self.assertIn("ll2:1", mapping)
        self.assertIn("nasa.gov", mapping["ll2:1"]["nasa_url"])
        self.assertEqual(self.cal._corroboration_map([{"key": "ll2:2", "title": "X"}], []), {})


class Ll2BudgetTests(unittest.TestCase):
    def test_shared_budget_blocks_all_ll2_tools(self):
        """429 の記憶は launch.py の3ツールとカレンダーで共有され、遮断中は HTTP を出さない。"""
        from space_finder_mcp import launch
        launch.reset_blocked()
        launch.note_429("104")
        try:
            self.assertGreater(launch.blocked_left(), 0)
            launch.upcoming_launches.cache_clear()
            r = launch.upcoming_launches(5)
            self.assertIn("error", r.structuredContent)              # 送信せずにエラーを返す
            self.assertIn("Retry-After", r.structuredContent["error"])
            r2 = launch.china_launches(3)
            self.assertIn("error", r2.structuredContent)
        finally:
            launch.reset_blocked()
        self.assertEqual(launch.blocked_left(), 0.0)

    def test_429_retry_after_is_clamped(self):
        """Retry-After が異常値でも待ち時間を暴走させない。"""
        from space_finder_mcp import launch
        launch.note_429("99999")
        try:
            self.assertLessEqual(launch.blocked_left(), 900.0)
        finally:
            launch.reset_blocked()
        launch.note_429("?")
        try:
            self.assertGreater(launch.blocked_left(), 0)
        finally:
            launch.reset_blocked()


if __name__ == "__main__":
    unittest.main()

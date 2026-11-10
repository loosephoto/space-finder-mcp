"""2026-10-31 の監査で見つかった不具合の再発防止テスト。

いずれも「実測で確認した不都合」に対応する。ネットワークは使わない（差し替える）。
"""
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from space_finder_mcp import cache, input_utils, nasa_budget, name_common, tool_docs  # noqa: E402


class TtlCacheSingleFlightTests(unittest.TestCase):
    """失敗経路でも N 並列が N 回 upstream を叩かない（stampede 防止）。"""

    def test_failed_result_is_shared_with_concurrent_followers_only(self):
        calls = {"n": 0}
        lock = threading.Lock()

        @cache.ttl_cache(60, maxsize=8, skip_if=lambda v: bool(v.get("error")))
        def failing(key):
            with lock:
                calls["n"] += 1
            time.sleep(0.12)
            return {"error": "boom"}

        threads = [threading.Thread(target=lambda: failing("k")) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 先頭 1 回だけが upstream を叩く（以前は 12 回だった）
        self.assertEqual(calls["n"], 1)

        # 新規に来た呼び出し（先頭）は必ず実行し直す = 障害回復を遅らせない
        failing("k")
        self.assertEqual(calls["n"], 2)

    def test_success_is_still_coalesced(self):
        calls = {"n": 0}
        lock = threading.Lock()

        @cache.ttl_cache(60, maxsize=8)
        def ok(key):
            with lock:
                calls["n"] += 1
            time.sleep(0.1)
            return {"ok": True}

        threads = [threading.Thread(target=lambda: ok("x")) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(ok.cache_info()["entries"], 1)


class InputUtilsShapeTests(unittest.TestCase):
    def test_as_list_normalizes_none_and_scalars(self):
        self.assertEqual(input_utils.as_list(None), [])
        self.assertEqual(input_utils.as_list("x"), [])
        self.assertEqual(input_utils.as_list({"a": 1}), [])
        self.assertEqual(input_utils.as_list([1, 2]), [1, 2])

    def test_as_float_does_not_raise_on_huge_int(self):
        self.assertEqual(input_utils.as_float(10 ** 400, 0.0), 0.0)

    def test_as_int_ignores_non_finite_bounds(self):
        self.assertEqual(input_utils.as_int(5, 7, minimum=float("inf")), 5)
        self.assertEqual(input_utils.as_int(5, 7, maximum=float("nan")), 5)


class NameResolverContractTests(unittest.TestCase):
    """通信断を「名前が解決できない」と誤案内しない。"""

    def test_sesame_connection_error_raises_resolver_unavailable(self):
        def boom(*a, **k):
            raise requests.ConnectionError("no route to host")

        old = requests.get
        requests.get = boom
        try:
            with self.assertRaises(name_common.ResolverUnavailable):
                name_common._sesame_lookup("M31")
        finally:
            requests.get = old

    def test_unresolved_name_still_returns_none(self):
        class R:
            status_code = 200
            content = b"<Sesame><Target/></Sesame>"

        old = requests.get
        requests.get = lambda *a, **k: R()
        try:
            self.assertIsNone(name_common._sesame_lookup("NoSuchObject123"))
        finally:
            requests.get = old

    def test_catalog_search_reports_resolver_unavailable(self):
        from space_finder_mcp import cds

        def boom(*a, **k):
            raise requests.ConnectionError("no route to host")

        old_get, old_resolve = requests.get, cds.resolve_object
        requests.get = boom
        cds.resolve_object = lambda name: (_ for _ in ()).throw(
            name_common.ResolverUnavailable("down"))
        try:
            res = cds.catalog_search(object_name="M31", catalog="2mass", max_rows=3)
        finally:
            requests.get = old_get
            cds.resolve_object = old_resolve
        sc = res.structuredContent or {}
        self.assertEqual(sc.get("error"), "resolver_unavailable")
        self.assertIn("接続できませんでした", res.content[0].text)


class SurfaceMapScaleTests(unittest.TestCase):
    """窓が端で切り詰められても、マーカーは地形と同じ場所に載る。"""

    def test_marker_matches_terrain_when_window_is_clamped(self):
        from PIL import Image
        from space_finder_mcp import surface_map as sm

        W0, H0 = 1000, 500
        im = Image.new("RGB", (W0, H0), (0, 0, 0))
        px = im.load()
        for y in range(H0):
            lat = 90 - (y + 0.5) / H0 * 180
            g = int((lat + 90) * 255 / 180)
            for x in range(W0):
                lon = (x + 0.5) / W0 * 360 - 180
                px[x, y] = (int((lon + 180) * 255 / 360), g, 7)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        png = buf.getvalue()

        old = sm.disk_get
        sm.disk_get = lambda *a, **k: png
        try:
            for span in (120.0, 200.0, 359.0):
                view = sm.image_view("http://example.invalid/x.png", lon=0.0, lat=0.0,
                                     span_deg=span, out_px=600)
                img = view["img"]
                x, y = view["g2px"](0.0, 40.0)
                xi = int(round(min(max(x, 0), img.size[0] - 1)))
                rows = [yy for yy in range(img.size[1])
                        if abs((img.getpixel((xi, yy))[1] / 255.0 * 180 - 90) - 40) < 1.5]
                self.assertTrue(rows, "lat=40 の帯が画像に無い (span={})".format(span))
                painted = sum(rows) / len(rows)
                self.assertLess(abs(y - painted), 5.0,
                                "span={} で g2px と実画素が {:.1f}px ずれた".format(span, y - painted))
        finally:
            sm.disk_get = old


class ToolDescriptionTests(unittest.TestCase):
    """tools/list に載る説明が空にならない・引数があれば Args を含む。"""

    def test_every_registered_tool_has_description_and_args(self):
        from space_finder_mcp import server
        missing = []
        for t in server.mcp._tool_manager.list_tools():
            desc = getattr(t, "description", "") or ""
            props = ((getattr(t, "parameters", None) or {}) or {}).get("properties") or {}
            if not desc.strip():
                missing.append((t.name, "description が空"))
            elif props and "Args:" not in desc:
                missing.append((t.name, "Args が無い"))
        self.assertEqual(missing, [])

    def test_args_block_is_kept_when_it_follows_example_line(self):
        doc = "概要。\n\n例:「これ」\nArgs:\n    limit: 件数。\n"
        out = tool_docs.compact_description(doc)
        self.assertIn("Args:", out)
        self.assertIn("limit: 件数。", out)


class EphemerisRangeTests(unittest.TestCase):
    def test_out_of_range_when_returns_error_result(self):
        from space_finder_mcp import ground_view as gv
        self.assertIsNone(gv.ephemeris_range_error((2026, 10, 31, 0, 0, 0)))
        self.assertIn("DE421", gv.ephemeris_range_error((2999, 12, 31, 0, 0, 0)))
        self.assertIn("DE421", gv.ephemeris_range_error((1800, 1, 1, 0, 0, 0)))

    def test_solar_system_now_reports_no_planets(self):
        from space_finder_mcp import solar_system as ss
        res = ss.solar_system_now(when="2100-01-01T00:00:00Z")
        self.assertEqual((res.structuredContent or {}).get("error"), "no_planets")


class SpaceCalendarValidationTests(unittest.TestCase):
    def test_invalid_month_is_an_error_not_a_silent_rounding(self):
        from space_finder_mcp import space_calendar as sc
        for month in (0, 13):
            res = sc.space_calendar(year=2026, month=month)
            self.assertIn("1〜12", (res.structuredContent or {}).get("error", ""))

    def test_out_of_ephemeris_range_year_is_an_error(self):
        from space_finder_mcp import space_calendar as sc
        res = sc.space_calendar(year=2100, month=1)
        self.assertIn("DE421", (res.structuredContent or {}).get("error", ""))


class NasaBudgetKeyTests(unittest.TestCase):
    def test_cooldown_is_per_key(self):
        nasa_budget._cooldown_until.clear()
        nasa_budget._announced_wait.clear()
        nasa_budget.note_429(nasa_budget.DEMO_KEY, "120")
        ok_custom, _wait, _used = nasa_budget.check("custom-key-abc")
        self.assertTrue(ok_custom, "DEMO_KEY の 429 が別キーの呼び出しを止めている")
        ok_demo, wait_demo, _ = nasa_budget.check(nasa_budget.DEMO_KEY)
        self.assertFalse(ok_demo)
        self.assertGreater(wait_demo, 0)
        self.assertIn("DEMO_KEY", nasa_budget.blocked_message(nasa_budget.DEMO_KEY))
        nasa_budget._cooldown_until.clear()
        nasa_budget._announced_wait.clear()


class HyperbolicCometTests(unittest.TestCase):
    """e>=1 の彗星はケプラー（楕円）で解かず円錐曲線の一般解へ振る。"""

    def test_comet_position_uses_conic_solver_for_e_greater_than_one(self):
        from space_finder_mcp import solar_system as ss
        import math

        el = {"e": 3.356, "i": 0.7, "node": 1.2, "argp": 0.3, "epoch": 2458853.5,
              "q": 2.0065, "tp": 2458826.05, "fullname": "2I"}
        old = ss._sbdb_elements
        ss._sbdb_elements = lambda cid: el
        try:
            out = ss._comet_position("2I", 2461000.5)
        finally:
            ss._sbdb_elements = old
        self.assertEqual(len(out), 6)
        for v in out:
            self.assertTrue(math.isfinite(float(v)))


class MediaShapeTests(unittest.TestCase):
    def test_media_items_helper_survives_null_and_scalar(self):
        from space_finder_mcp import media
        self.assertEqual(media._items({"collection": {"items": None}}), [])
        self.assertEqual(media._items({"collection": {"items": "x"}}), [])
        self.assertEqual(media._items({"collection": None}), [])
        self.assertEqual(media._items({"collection": {"items": [1]}}), [1])

    def test_jaxa_child_collections_survives_null_links(self):
        from space_finder_mcp import jaxa
        old = jaxa._get_json
        jaxa._get_json = lambda url, *a, **k: {"links": None}
        try:
            self.assertEqual(jaxa._child_collections(), [])
        finally:
            jaxa._get_json = old


class ExpandDatetimeTests(unittest.TestCase):
    """CDSE / AWS Earth Search は date-only を 400 で拒否するため RFC3339 へ展開する。"""

    def test_single_date_expands_to_full_day_range(self):
        from space_finder_mcp.stac_common import expand_datetime
        self.assertEqual(expand_datetime("2026-01-01"),
                         "2026-01-01T00:00:00Z/2026-01-01T23:59:59Z")

    def test_start_end_expands_both_sides(self):
        from space_finder_mcp.stac_common import expand_datetime
        self.assertEqual(expand_datetime("2026-01-01/2026-03-31"),
                         "2026-01-01T00:00:00Z/2026-03-31T23:59:59Z")

    def test_full_timestamp_and_open_end_are_untouched(self):
        from space_finder_mcp.stac_common import expand_datetime
        full = "2026-01-01T00:00:00Z/2026-03-31T23:59:59Z"
        self.assertEqual(expand_datetime(full), full)
        self.assertEqual(expand_datetime("2026-01-01/"), "2026-01-01T00:00:00Z/")

    def test_month_and_year_forms(self):
        from space_finder_mcp.stac_common import expand_datetime
        self.assertEqual(expand_datetime("2026-02"),
                         "2026-02-01T00:00:00Z/2026-02-28T23:59:59Z")
        self.assertEqual(expand_datetime("2024"),
                         "2024-01-01T00:00:00Z/2024-12-31T23:59:59Z")

    def test_nonexistent_calendar_date_passes_through_unchanged(self):
        from space_finder_mcp.stac_common import expand_datetime
        self.assertEqual(expand_datetime("2026-02-30"), "2026-02-30")

    def test_stac_bodies_carry_expanded_datetime_offline(self):
        """date-only を渡すとリクエスト body は RFC3339 になる（上流 400 の防止・オフライン）。"""
        from space_finder_mcp import copernicus, stac_search
        bodies = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": []}

        def fake_post(url, headers=None, json=None, timeout=None):
            bodies.append(json)
            return _Resp()

        for mod, fn in ((copernicus, "copernicus_search"), (stac_search, "stac_search")):
            with mock.patch.object(mod.requests, "post", fake_post):
                res = getattr(mod, fn).__wrapped__(datetime="2026-10-01/2026-10-02")
            self.assertNotIn("error", res.structuredContent)
        self.assertEqual(len(bodies), 2)
        for b in bodies:
            self.assertEqual(b["datetime"], "2026-10-01T00:00:00Z/2026-10-02T23:59:59Z")


class AlmaRetryTests(unittest.TestCase):
    """完全一致 0 件の再試行は IN 句を外して部分一致だけで拾い直す。"""

    def test_retry_drops_exact_in_clause(self):
        from space_finder_mcp import alma
        queries = []
        hdr = ["obs_publisher_did", "target_name", "s_ra", "s_dec", "band_list", "em_min",
               "em_max", "proposal_id", "data_rights", "dataproduct_type", "s_resolution",
               "t_exptime", "pwv", "qa2_passed", "obs_release_date", "pi_name", "pub_title",
               "publication_year", "access_url"]
        row1 = ["uid://a1", "HL Tau", "202.5", "-18.3", "6", "1.3", "1.0", "2023.1.001",
                "Public", "cube", "0.5", "300", "0.6", "T", "2024-05-01", "PI", "Title",
                "2025", ""]
        row2 = ["uid://a2", "HL_Tau", "202.5", "-18.3", "6", "1.3", "1.0", "2023.1.002",
                "Public", "cube", "0.5", "300", "0.6", "T", "2024-05-02", "PI", "Title",
                "2025", ""]

        def fake_query(q, limit):
            queries.append(q)
            if "target_name IN" in q:
                return [], None          # 完全一致は 0 件
            return [hdr, row1, row2], None  # 再試行（部分一致）で 2 件

        with mock.patch.object(alma, "_do_query", fake_query):
            res = alma.alma_search.__wrapped__(object_name="HL TAU")
        self.assertEqual(len(queries), 2)
        self.assertIn("target_name IN", queries[0])
        self.assertNotIn("target_name IN", queries[1])
        self.assertIn("LIKE", queries[1])
        text = res.content[0].text
        self.assertIn("表記ゆれ", text)
        self.assertIn("2件 表示", text)

    def test_coordinate_search_does_not_suggest_unrelated_names(self):
        """座標検索 0 件では _candidate_names("")（LIKE '%'）を呼ばない。"""
        from space_finder_mcp import alma
        calls = []

        def fake_query(q, limit):
            calls.append(q)
            return [], None

        with mock.patch.object(alma, "_do_query", fake_query):
            res = alma.alma_search.__wrapped__(ra=10.1234, dec=-10.5678, radius=0.0001)
        self.assertEqual(res.structuredContent["count"], 0)
        self.assertEqual(res.structuredContent["candidates"], [])
        self.assertEqual(len(calls), 1)   # 円錐検索 1 本だけ（名前の候補抽出はしない）


class CsaZeroResultNotCachedTests(unittest.TestCase):
    """csa の 0 件応答は 24 時間キャッシュしない（一時的な空応答の固定化防止）。"""

    def test_zero_total_is_not_cached(self):
        from space_finder_mcp import csa
        calls = {"n": 0}

        def fake_ckan(action, params=None):
            calls["n"] += 1
            return {"success": True, "result": {"count": 0, "results": []}}

        with mock.patch.object(csa, "_ckan", fake_ckan):
            csa.csa_dataset_search(query="zzqqxxnotexist-v0500-2")   # デコレータ経由
            csa.csa_dataset_search(query="zzqqxxnotexist-v0500-2")
        self.assertEqual(calls["n"], 2, "0 件応答がキャッシュされてはならない")


class MastDateValidationTests(unittest.TestCase):
    """存在しない日付（02-30 等）は黙って無視せずエラーにする（roscosmos と同じ扱い）。"""

    def test_nonexistent_date_returns_error_without_http(self):
        from space_finder_mcp import mast
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            raise requests.ConnectionError("blocked")

        with mock.patch.object(mast.requests, "post", boom), \
                mock.patch.object(mast.requests, "get", boom), \
                mock.patch.object(requests.Session, "request", boom):
            res = mast.mast_observations.__wrapped__(ra=10.68, dec=41.27,
                                                     start_date="2026-02-30")
        self.assertEqual(calls["n"], 0)
        self.assertIn("error", res.structuredContent)
        self.assertIn("2026-02-30", res.content[0].text)
        self.assertIn("YYYY-MM-DD", res.content[0].text)

    def test_valid_dates_still_reach_filters(self):
        from space_finder_mcp import mast
        built = {}

        def fake_filters(*a, **k):
            built["args"] = a
            return []

        def boom(*a, **k):
            raise requests.ConnectionError("blocked")

        # HTTP は必ず遮断する（このテストは「有効な日付が filters に届くか」だけを見る。
        # 実通信にするとネットワークの状態で import ロックの競合が起きてERRORになる）
        with mock.patch.object(mast, "_build_filters", fake_filters), \
                mock.patch.object(mast.requests, "post", boom), \
                mock.patch.object(mast.requests, "get", boom), \
                mock.patch.object(requests.Session, "request", boom):
            mast.mast_observations.__wrapped__(ra=10.68, dec=41.27,
                                               start_date="2026-02-28",
                                               end_date="2026-03-31")
        self.assertEqual(built["args"][3], "2026-02-28")
        self.assertEqual(built["args"][4], "2026-03-31")


class EsoSeeingWindowTests(unittest.TestCase):
    """API が窓外のサンプルを返しても集計は窓内だけに使う。"""

    def test_out_of_window_samples_are_excluded(self):
        import datetime as dt
        from space_finder_mcp import eso
        now = dt.datetime.now(dt.timezone.utc)
        old_ms = int((now - dt.timedelta(hours=9)).timestamp() * 1000)
        in_ms = int((now - dt.timedelta(hours=1)).timestamp() * 1000)

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"dimm_paranal-fwhm": [[old_ms, 0.9], [in_ms, 0.7]]}

        with mock.patch.object(eso.requests, "get",
                               lambda url, params=None, headers=None, timeout=None: _Resp()):
            res = eso.eso_seeing.__wrapped__(hours=6)
        sc = res.structuredContent
        self.assertEqual(sc["fields"]["dimm_paranal-fwhm"]["samples"], 1)
        self.assertAlmostEqual(sc["fields"]["dimm_paranal-fwhm"]["avg"], 0.7, places=3)
        self.assertIn("除外しました", res.content[0].text)

    def test_only_stale_data_reports_window_miss(self):
        import datetime as dt
        from space_finder_mcp import eso
        now = dt.datetime.now(dt.timezone.utc)
        old_ms = int((now - dt.timedelta(hours=9)).timestamp() * 1000)

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"dimm_paranal-fwhm": [[old_ms, 0.9]]}

        with mock.patch.object(eso.requests, "get",
                               lambda url, params=None, headers=None, timeout=None: _Resp()):
            res = eso.eso_seeing.__wrapped__(hours=6)
        self.assertNotIn("dimm_paranal-fwhm", res.structuredContent["fields"])
        self.assertIn("指定した期間内のデータなし", res.content[0].text)
        self.assertIn("窓外", res.content[0].text)



class RoverNameResolveTests(unittest.TestCase):
    """planetary_rover_location_map は日本語名・英語名もキーへ寄せる（docstring の例が通ること）。"""

    def test_japanese_names_resolve_to_keys(self):
        from space_finder_mcp.planetary_rover import _resolve_rover_key as r
        self.assertEqual(r("パーサヴィアランス"), "perseverance")
        self.assertEqual(r("キュリオシティ"), "curiosity")
        self.assertEqual(r("イデフィクス"), "idefix")
        self.assertEqual(r("玉兔2号"), "yutu2")
        self.assertEqual(r("ルノホート1号"), "lunokhod1")
        self.assertEqual(r("アポロ15号"), "apollo15")

    def test_english_and_key_forms_resolve_to_themselves(self):
        from space_finder_mcp.planetary_rover import _resolve_rover_key as r
        self.assertEqual(r("Perseverance"), "perseverance")
        self.assertEqual(r("perseverance"), "perseverance")
        self.assertEqual(r("LRV（Apollo 15）"), "apollo15")
        self.assertEqual(r(""), "")

    def test_unknown_name_passes_through_lowercased(self):
        from space_finder_mcp.planetary_rover import _resolve_rover_key as r
        self.assertEqual(r("SOMEUNKNOWN"), "someunknown")


class CalendarLayoutTests(unittest.TestCase):
    """セル高は 3 イベント＋「＋N 件」が収まること。本文表はヘッダと同じ 4 セル。"""

    def test_cell_height_fits_three_events_and_overflow_note(self):
        from space_finder_mcp import space_calendar as sc
        # 内枠（-4）の中に「y0+50 開始・各 62px の 3 イベント＋＋N 件 1 行」が入ること
        self.assertGreaterEqual(sc.CELL_H - 4, 50 + 3 * 62 + 16)

    def test_event_line_has_four_cells(self):
        import datetime as dt
        from space_finder_mcp import space_calendar as sc
        ev = {"date": dt.date(2026, 11, 7), "all_day": False,
              "dt": dt.datetime(2026, 11, 7, 9, 5), "kind": "launch",
              "title": "打ち上げ", "end": None, "certainty": "confirmed"}
        line = sc._event_line(ev)
        self.assertEqual(line.count("|"), 5, line)      # | 日付 | 時刻 | カテゴリ | 内容 |
        self.assertTrue(line.startswith("| 2026-11-07 | 09:05 |"))


class Orbit3dTagTests(unittest.TestCase):
    """_tag は既存の矩形（見出し・先のラベル）と重ならない位置へずれる。"""

    def test_tag_avoids_existing_boxes(self):
        from PIL import Image, ImageDraw
        from space_finder_mcp.orbit3d import _tag
        from space_finder_mcp.img_common import load_font
        img = Image.new("RGB", (220, 160), (0, 0, 0))
        dr = ImageDraw.Draw(img)
        placed = [(0, 0, 210, 30)]
        _tag(dr, (10, 10), "ラベル", (255, 255, 255), load_font(14), placed=placed)
        box = placed[-1]
        r = (0, 0, 210, 30)
        disjoint = box[2] < r[0] or box[0] > r[2] or box[3] < r[1] or box[1] > r[3]
        self.assertTrue(disjoint, box)
        # ずらした先もキャンバス内
        self.assertGreaterEqual(box[0], 4)
        self.assertGreaterEqual(box[1], 4)

    def test_tag_without_placed_draws_at_given_position(self):
        from PIL import Image, ImageDraw
        from space_finder_mcp.orbit3d import _tag
        from space_finder_mcp.img_common import load_font
        img = Image.new("RGB", (220, 160), (0, 0, 0))
        dr = ImageDraw.Draw(img)
        _tag(dr, (10, 10), "ラベル", (255, 255, 255), load_font(14))
        px = img.load()
        # 背景矩形が描かれている
        self.assertEqual(px[12, 12], (8, 10, 18))



class NegativeCacheBoundedTests(unittest.TestCase):
    """ttl_cache の失敗記録（negative）は掃除されずに際限なく溜まらない。"""

    def test_negative_entries_are_bounded(self):
        from space_finder_mcp import cache

        @cache.ttl_cache(60, maxsize=8, skip_if=lambda v: bool(v.get("error")))
        def failing(key):
            return {"error": "x"}

        for i in range(700):
            failing(i)
        info = failing.cache_info()
        self.assertLessEqual(info["negatives"], 512, info)

    def test_expired_negative_entries_are_purged_on_write(self):
        from space_finder_mcp import cache

        @cache.ttl_cache(60, maxsize=8, skip_if=lambda v: not v)
        def empty(key):
            return []

        with mock.patch.object(cache, "NEGATIVE_TTL", 0.05):
            empty("a")
            empty("b")
            time.sleep(0.2)
            empty("c")                    # この書き込みのタイミングで期限切れ分を掃除する
        self.assertLessEqual(empty.cache_info()["negatives"], 1)


class SlimSchemaRefTests(unittest.TestCase):
    """$ref + null は畳まない（"type": "null" を付けると意味が変わるため）。"""

    def test_ref_plus_null_stays_anyof(self):
        from space_finder_mcp.tool_docs import slim_schema
        node = {"anyOf": [{"$ref": "#/x"}, {"type": "null"}]}
        out = slim_schema(node)
        self.assertIn("anyOf", out)
        self.assertNotIn("type", out)
        self.assertEqual(out["anyOf"][0], {"$ref": "#/x"})
        self.assertEqual(slim_schema(out), out)      # 冪等

    def test_plain_type_plus_null_still_folds(self):
        from space_finder_mcp.tool_docs import slim_schema
        out = slim_schema({"anyOf": [{"type": "string"}, {"type": "null"}]})
        self.assertEqual(out.get("type"), ["string", "null"])
        self.assertNotIn("anyOf", out)


class JsonKeyCollisionTests(unittest.TestCase):
    """非 str キーの変換先が既存キーと衝突しても黙って落とさない（str キーを優先）。"""

    def test_int_and_str_key_both_survive(self):
        from space_finder_mcp.jsonio import to_json
        out = to_json({1: "int値", "1": "str値"})
        self.assertEqual(out["1"], "str値")          # 元が str のキーを優先
        self.assertIn("_1", out)
        self.assertEqual(out["_1"], "int値")

    def test_result_is_insertion_order_independent(self):
        from space_finder_mcp.jsonio import to_json
        a = to_json({1: "int値", "1": "str値"})
        b = to_json({"1": "str値", 1: "int値"})
        self.assertEqual(a, b)



class EodashboardCacheTests(unittest.TestCase):
    """173 件のコレクション JSON は並列取得＋ディスクキャッシュ（冷時 40 秒→1.5 秒）。"""

    def setUp(self):
        import shutil
        import tempfile
        from space_finder_mcp import cache, eodashboard
        self.cache, self.eo = cache, eodashboard
        self._old_root = cache.CACHE_ROOT
        self.tmp = tempfile.mkdtemp()
        cache.CACHE_ROOT = self.tmp
        eodashboard._collections_cache = None
        self._orig_get = eodashboard.requests.get

    def tearDown(self):
        import shutil
        self.cache.CACHE_ROOT = self._old_root
        self.eo.requests.get = self._orig_get
        self.eo._collections_cache = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_get(self, fail_one=False):
        calls = {"n": 0}

        class R:
            status_code = 200

            def __init__(self, payload):
                self._p = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._p

        def get(url, **kw):
            calls["n"] += 1
            if url == self.eo.TREE_API:
                return R({"tree": [{"path": "collections/c{}.json".format(i)}
                                   for i in range(20)]})
            cid = url.rsplit("/", 1)[-1][:-5]
            if fail_one and cid == "c3":
                r = R({})
                r.status_code = 404
                return r
            return R({"Name": cid, "Title": cid.upper(), "Themes": ["T"], "Agency": ["A"]})
        return get, calls

    def test_list_is_written_to_disk_and_read_back_without_requests(self):
        get, calls = self._fake_get()
        self.eo.requests.get = get
        rows = self.eo._fetch_collection_list()
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({r["id"] for r in rows}), 20)
        self.eo._collections_cache = None                  # プロセス内キャッシュも捨てる

        def boom(*a, **k):
            raise requests.ConnectionError("offline")
        self.eo.requests.get = boom
        rows2 = self.eo._fetch_collection_list()           # ディスクから読む（HTTP 0 回）
        self.assertEqual(len(rows2), 20)
        self.assertEqual(rows2[0]["id"], rows[0]["id"])

    def test_incomplete_list_is_not_fixed_on_disk(self):
        get, _calls = self._fake_get(fail_one=True)
        self.eo.requests.get = get
        rows = self.eo._fetch_collection_list()
        self.assertEqual(len(rows), 19)                    # 1 件は取得失敗
        from space_finder_mcp.cache import disk_cache_path
        self.assertFalse(os.path.exists(
            disk_cache_path("eodashboard_collections_v1", subdir="catalogue")))

    def test_corrupt_disk_cache_is_ignored(self):
        from space_finder_mcp.cache import disk_cache_path
        path = disk_cache_path("eodashboard_collections_v1", subdir="catalogue")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        get, _calls = self._fake_get()
        self.eo.requests.get = get
        rows = self.eo._fetch_collection_list()            # 壊れたキャッシュは無視して再取得
        self.assertEqual(len(rows), 20)


class BoxBlurCropTests(unittest.TestCase):
    """box_blur は 0 領域を切り落としても全画面版と厳密に同じ結果になる。"""

    def test_crop_equals_full_image_blur(self):
        import numpy as np
        from space_finder_mcp.ground_view import box_blur
        rng = np.random.default_rng(11)
        a = (rng.random((240, 320, 3)) * 0.4).astype(np.float32)
        a[180:] = 0.0                       # 地面側（0）を切り落とす経路を通す
        a[:, :30] = 0.0
        r = 5

        def full(x, r):
            out = x.astype(np.float32, copy=True)
            k = 2 * r + 1
            for ax in (0, 1):
                n = out.shape[ax]
                pad = [(0, 0)] * out.ndim
                pad[ax] = (r, r)
                c = np.cumsum(np.pad(out, pad, mode="edge"), axis=ax)
                c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
                out = (np.take(c, np.arange(k, k + n), axis=ax)
                       - np.take(c, np.arange(n), axis=ax)) / k
            return out

        self.assertEqual(float(np.max(np.abs(full(a, r) - box_blur(a, r)))), 0.0)

    def test_all_zero_input_returns_zeros(self):
        import numpy as np
        from space_finder_mcp.ground_view import box_blur
        a = np.zeros((50, 60, 3), np.float32)
        self.assertFalse(np.any(box_blur(a, 3)))


class GalacticLbIdentityTests(unittest.TestCase):
    """1 次元三角関数化しても galactic_lb の値は厳密に同じ（式・演算順を変えない）。"""

    def test_matches_reference_formula(self):
        import math
        import numpy as np
        from space_finder_mcp.ground_view import galactic_lb

        def reference(sky_h, W, span, az0, lat, lon, lst_hours):
            rows = np.arange(sky_h, dtype=np.float64)
            alt = np.repeat((90.0 * (1.0 - rows / sky_h))[:, None], W, 1)
            az = np.repeat((az0 + (np.arange(W) + 0.5) / W * span)[None, :], sky_h, 0)
            a, d, phi = np.radians(az), np.radians(alt), math.radians(lat)
            sin_dec = np.sin(d) * math.sin(phi) + np.cos(d) * math.cos(phi) * np.cos(a)
            dec = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
            ha = np.arctan2(-np.sin(a) * np.cos(d),
                            np.sin(d) * math.cos(phi) - np.cos(d) * math.sin(phi) * np.cos(a))
            ra = np.radians(((lst_hours - np.degrees(ha) / 15.0) % 24.0) * 15.0)
            aG, dG = math.radians(192.85948), math.radians(27.12825)
            y = np.cos(dec) * np.sin(ra - aG)
            x = np.sin(dec) * math.cos(dG) - np.cos(dec) * math.sin(dG) * np.cos(ra - aG)
            b = np.degrees(np.arcsin(np.clip(
                np.sin(dec) * math.sin(dG) + np.cos(dec) * math.cos(dG) * np.cos(ra - aG),
                -1.0, 1.0)))
            l = (122.93192 - np.degrees(np.arctan2(y, x))) % 360.0
            return l, b, alt

        args = (37, 53, 120.0, 15.0, 35.68, 139.77, 10.5)
        l1, b1, a1 = reference(*args)
        l2, b2, a2 = galactic_lb(*args)
        self.assertTrue(np.array_equal(l1, l2))
        self.assertTrue(np.array_equal(b1, b2))
        self.assertTrue(np.array_equal(a1, a2))





class ToolDocsBoldSentenceTests(unittest.TestCase):
    """太字を行またぐ段落を `。` で割ると `****` になる（2026-11-10 監査）。"""

    def test_adjacent_bold_lines_split_into_balanced_sentences(self):
        s = tool_docs._sentences("**A。**\n**B。**")
        self.assertEqual(s, ["**A。**", "**B。**"])
        # 文は "\n\n" で接合される（"".join だと境界に **** が出るだけ）
        self.assertNotIn("****", "\n\n".join(s))
        for one in s:
            self.assertEqual(one.count("**") % 2, 0)

    def test_bold_span_with_two_sentences_stays_one(self):
        self.assertEqual(tool_docs._sentences("**A。B。**"), ["**A。B。**"])

    def test_plain_text_still_splits(self):
        self.assertEqual(tool_docs._sentences("A。B。"), ["A。", "B。"])

    def test_calendar_description_has_no_quad_asterisk(self):
        from space_finder_mcp import server
        d = server.mcp._tool_manager._tools["space_calendar"].description
        self.assertNotIn("****", d)
        self.assertIn("figure.notes", d)

    def test_examples_before_args_kept_and_not_duplicated(self):
        doc = ("要約。\n\n例:「テスト」\n\n"
               "Args:\n    x: 引数。\n        例: \"a\", \"b\"。\n")
        d = tool_docs.compact_description(doc)
        self.assertEqual(d.count("例:「テスト」"), 1)
        self.assertEqual(d.count('例: "a", "b"。'), 1)

    def test_example_in_same_block_as_args_is_kept(self):
        doc = "英国のコレクション。\n\n例:「英国の衛星データ」\nArgs:\n    limit: 件数。\n"
        d = tool_docs.compact_description(doc)
        self.assertIn("例:「英国の衛星データ」", d)
        self.assertIn("Args:", d)

    def test_every_tool_has_example(self):
        from space_finder_mcp import server
        names = [t.name for t in server.mcp._tool_manager.list_tools()
                 if "例" not in (t.description or "")]
        self.assertEqual(names, [])


class JsonioDecimalTests(unittest.TestCase):
    def test_nonfinite_decimal_becomes_none(self):
        import decimal
        from space_finder_mcp import jsonio
        for v in ("NaN", "Infinity", "-Infinity", "sNaN"):
            self.assertIsNone(jsonio.to_json({"v": decimal.Decimal(v)})["v"])

    def test_finite_decimal_unchanged(self):
        import decimal
        from space_finder_mcp import jsonio
        self.assertEqual(jsonio.to_json({"v": decimal.Decimal("2")})["v"], 2)
        self.assertEqual(jsonio.to_json({"v": decimal.Decimal("1.5")})["v"], 1.5)


class FmtNumTests(unittest.TestCase):
    def test_small_nonzero_and_nonfinite(self):
        from space_finder_mcp import img_common as ic
        self.assertEqual(ic._fmt_num(0.0), "0")
        self.assertEqual(ic._fmt_num(-0.0), "0")
        self.assertNotEqual(ic._fmt_num(1e-5), "0")
        self.assertNotEqual(ic._fmt_num(-1e-5), "-0")
        self.assertEqual(ic._fmt_num(float("inf")), "-")
        self.assertEqual(ic._fmt_num(float("-inf")), "-")
        self.assertEqual(ic._fmt_num(float("nan")), "-")
        self.assertEqual(ic._fmt_num(None), "-")

    def test_normal_values_unchanged(self):
        from space_finder_mcp import img_common as ic
        self.assertEqual(ic._fmt_num(1234.0), "1,234")
        self.assertEqual(ic._fmt_num(1.5), "1.50")
        self.assertEqual(ic._fmt_num(0.5), "0.5")


class EncodeJpegTests(unittest.TestCase):
    def test_respects_max_bytes(self):
        import numpy as np
        from PIL import Image
        from space_finder_mcp import img_common as ic
        im = Image.fromarray(np.random.default_rng(0).integers(
            0, 256, (2000, 2500, 3), dtype=np.uint8))
        data = ic.encode_jpeg(im, max_bytes=800_000)
        self.assertLessEqual(len(data), 800_000)


class ParseDotenvTests(unittest.TestCase):
    def test_inline_comment_stripped(self):
        from space_finder_mcp import env_config
        self.assertEqual(env_config.parse_dotenv("A=1 # comment"), {"A": "1"})
        self.assertEqual(env_config.parse_dotenv("A=abc #c"), {"A": "abc"})
        self.assertEqual(env_config.parse_dotenv("A=abc#def"), {"A": "abc#def"})
        self.assertEqual(env_config.parse_dotenv('A="abc" # c'), {"A": "abc"})
        self.assertEqual(env_config.parse_dotenv("A=  x  "), {"A": "x"})


class NasaBudgetPerKeyTests(unittest.TestCase):
    def setUp(self):
        nasa_budget._calls.clear()
        nasa_budget._cooldown_until.clear()

    def tearDown(self):
        nasa_budget._calls.clear()
        nasa_budget._cooldown_until.clear()

    def test_other_key_usage_does_not_block_demo_key(self):
        for _ in range(30):
            nasa_budget.record("custom-key")
        ok, _wait, used = nasa_budget.check("DEMO_KEY")
        self.assertTrue(ok)
        self.assertEqual(used, 0)

    def test_demo_key_usage_does_not_block_custom_key(self):
        for _ in range(30):
            nasa_budget.record("DEMO_KEY")
        ok, _wait, used = nasa_budget.check("custom-key")
        self.assertTrue(ok)
        self.assertEqual(used, 0)
        ok2, _w2, used2 = nasa_budget.check("DEMO_KEY")
        self.assertFalse(ok2)
        self.assertEqual(used2, 30)


class JaxaPublicWindowTests(unittest.TestCase):
    def test_window_uses_observer_local_date(self):
        import datetime as dt
        from space_finder_mcp import space_calendar as sc
        seen = {}

        def fake_fresh(src, ttl, window=None):
            seen["window"] = window
            return True

        with mock.patch.object(sc.store, "source_is_fresh", fake_fresh):
            sc._jaxa_public_events(9.0)
        expect = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                  + dt.timedelta(hours=9)).date().isoformat()
        self.assertEqual(seen.get("window"), expect)


class GeocodeDedupeTests(unittest.TestCase):
    def test_same_query_not_sent_twice(self):
        from space_finder_mcp import weather_astro as wa
        calls = []

        class R:
            status_code = 200
            headers = {}
            content = b'{"results":[]}'
            text = '{"results":[]}'

            def json(self):
                return {"results": []}

            def raise_for_status(self):
                pass

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(dict(params or {}))
            return R()

        wa._geocode.cache_clear()
        try:
            with mock.patch.object(wa.requests, "get", fake_get), \
                 mock.patch.object(wa, "_geocode_nominatim", lambda p: None):
                wa._geocode("白馬")
        finally:
            wa._geocode.cache_clear()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

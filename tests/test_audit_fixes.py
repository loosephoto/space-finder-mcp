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


if __name__ == "__main__":
    unittest.main()

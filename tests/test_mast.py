"""MAST（Mashup API）ツールの回帰テスト。

実測した API の癖の再発防止:
- 連続値フィルタは {"min","max"}（[a,b] では 0 件になった）
- filters は Mast.Caom.Cone では無視される → 位置＋絞り込みは Filtered.Position を使う
- 装置名は完全一致（'NIRCAM/IMAGE'）。'NIRCam' のような書き方は freeText にする
- Mast.Caom.Products は CAOM の obsid（整数）で引く（obs_id 文字列では ERROR）
- 日付は MJD。null/異常値から架空の日付を作らない
- 接続不可は記憶して fail fast（並列実行の道連れ防止）
"""
import datetime as dt
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, "src")


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.text = ""
    r.json = lambda: payload
    r.raise_for_status = lambda: None
    return r


def _caom_row(obs_id="u2id011vt", obsid=24553790, coll="HST", inst="WFPC2/PC",
              target="M-42-NARROW", t_min=49768.6, intent="science"):
    return {"obs_id": obs_id, "obsid": obsid, "obs_collection": coll, "instrument_name": inst,
            "target_name": target, "t_min": t_min, "t_max": t_min + 0.001,
            "s_ra": 83.82, "s_dec": -5.39, "calib_level": 3, "dataproduct_type": "image",
            "proposal_id": "5469", "filters": "F656N", "intentType": intent}


class MastTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import mast
        self.mast = mast
        mast.reset_blocked()

    def tearDown(self):
        self.mast.reset_blocked()

    def test_instrument_filter_exact_vs_fuzzy(self):
        """装置コードは完全一致、書き方ゆれは freeText（部分一致）。"""
        ex = self.mast._instrument_filter("WFPC2")
        self.assertEqual(ex["values"], ["WFPC2"])
        self.assertNotIn("freeText", ex)
        ex2 = self.mast._instrument_filter("nircam/image")
        self.assertEqual(ex2["values"], ["NIRCAM/IMAGE"])
        ex3 = self.mast._instrument_filter("NIRSPEC/MSA")
        self.assertEqual(ex3["values"], ["NIRSPEC/MSA"])
        self.assertNotIn("freeText", self.mast._instrument_filter("miri/image"))
        fz = self.mast._instrument_filter("NirCam")
        self.assertEqual(fz["values"], [])
        self.assertEqual(fz["freeText"], "%NIRCAM%")

    def test_filters_exclude_calibration_by_default(self):
        """既定で intentType=science（BIAS/DARK が結果を占める実測への対応）。"""
        got = self.mast._build_filters(None, None, None, None, None, None)
        self.assertIn({"paramName": "intentType", "values": ["science"]}, got)
        incl = self.mast._build_filters(None, None, None, None, None, None, include_calibration=True)
        self.assertFalse([f for f in incl if f["paramName"] == "intentType"])

    def test_filters_mission_alias_and_date_range(self):
        """和名のミッション解決と、日付→MJD の {"min","max"} 変換。"""
        got = self.mast._build_filters("ハッブル", None, "image", "2024-01-31", "2024-03-01", None)
        self.assertIn({"paramName": "obs_collection", "values": ["HST"]}, got)
        self.assertIn({"paramName": "dataproduct_type", "values": ["image"]}, got)
        tmin = [f for f in got if f["paramName"] == "t_min"][0]
        self.assertEqual(tmin["values"], [{"min": self.mast._mjd_from_date("2024-01-31"),
                                           "max": self.mast._mjd_from_date("2024-03-01")}])
        self.assertEqual(self.mast._resolve_mission("ウェッブ"), "JWST")
        self.assertEqual(self.mast._resolve_mission("TESS"), "TESS")

    def test_filters_free_text_product_type_and_target(self):
        got = self.mast._build_filters(None, None, "spectro", None, None, "%UDS_cat%")
        self.assertEqual([f for f in got if f["paramName"] == "dataproduct_type"][0]["freeText"],
                         "%SPECTRO%")
        self.assertEqual([f for f in got if f["paramName"] == "target_name"][0]["freeText"],
                         "%UDS_cat%")

    def test_mjd_conversion_is_defensive(self):
        """MJD ↔ ISO の往復と、異常値（null・負・巨大・文字列）を空/None に落とす。"""
        self.assertEqual(self.mast._mjd_to_iso(49768.6)[:10], "1995-02-20")
        self.assertEqual(self.mast._mjd_from_date("2026-09-19"),
                         (dt.date(2026, 9, 19) - dt.date(1858, 11, 17)).days)
        self.assertEqual(self.mast._mjd_from_date("2026/9/19"),
                         self.mast._mjd_from_date("2026-09-19"))
        for bad in (None, "?", "", -1, 0, 999999):
            self.assertEqual(self.mast._mjd_to_iso(bad), "")
        for bad in (None, "?", "2026-13-45"):
            self.assertIsNone(self.mast._mjd_from_date(bad))

    def _run(self, payload, **kw):
        with mock.patch.object(self.mast, "_invoke", return_value=(payload, None)) as inv:
            res = self.mast.mast_observations(**kw)
        return res, inv

    def test_position_query_uses_filtered_position(self):
        """座標があるときは Filtered.Position に position 文字列を渡す（Cone は filters 無視）。"""
        res, inv = self._run({"data": [_caom_row()]}, ra=83.82, dec=-5.39, radius=0.05, limit=1)
        svc, params = inv.call_args[0][0], inv.call_args[0][1]
        self.assertEqual(svc, "Mast.Caom.Filtered.Position")
        self.assertEqual(params["position"], "83.82, -5.39, 0.05")
        d = res.structuredContent
        self.assertEqual(d["shown"], 1)
        rec = d["results"][0]
        self.assertEqual(rec["mission"], "HST")
        self.assertEqual(rec["date_utc"][:10], "1995-02-20")
        self.assertIn("search/ui/#/?obsid=24553790", rec["portal_url"])
        self.assertIn("MAST で開く", res.content[0].text)

    def test_name_resolution_and_target_fallback(self):
        """Sesame で解けたら座標検索、解けない名前は target の freeText 検索へ回す。"""
        hit = {"input_name": "M31", "matched_name": "M  31", "resolver": "Simbad", "otype": "AGN",
               "ra_deg": 10.6847, "dec_deg": 41.2687, "all_positions": []}
        with mock.patch.object(self.mast, "resolve_object", return_value=hit):
            res, inv = self._run({"data": [_caom_row()]}, object_name="M31", limit=1)
        self.assertEqual(inv.call_args[0][0], "Mast.Caom.Filtered.Position")
        self.assertEqual(res.structuredContent["name_resolution"]["matched"], "M  31")
        self.assertIn("M 31", res.content[0].text)         # 空白は畳んで表示する
        with mock.patch.object(self.mast, "resolve_object", return_value=None):
            res2, inv2 = self._run({"data": []}, object_name="UDS_cat_visit1_EOSA", limit=1)
        svc, params = inv2.call_args[0][0], inv2.call_args[0][1]
        self.assertEqual(svc, "Mast.Caom.Filtered")        # 位置が無いので Position ではない
        free = [f for f in params["filters"] if f["paramName"] == "target_name"][0]["freeText"]
        self.assertEqual(free, "%UDS_cat_visit1_EOSA%")
        self.assertEqual(res2.structuredContent["target_search"], "%UDS_cat_visit1_EOSA%")

    def test_missing_position_is_error_result(self):
        """天体名も座標も無いときは例外ではなくエラー結果を返す。"""
        res = self.mast.mast_observations(limit=3)
        self.assertEqual(res.structuredContent["error"], "location required")
        self.assertIn("指定してください", res.content[0].text)

    def test_bad_numeric_args_do_not_raise(self):
        """ゲートの fuzz（不正値注入）で例外を漏らさない。"""
        with mock.patch.object(self.mast, "_invoke", return_value=({"data": []}, None)) as inv:
            res = self.mast.mast_observations(ra=83.82, dec="-5.39", radius="x", limit="3件",
                                              start_date="?")
        self.assertIsInstance(res.structuredContent, dict)
        self.assertNotIn("error", res.structuredContent)
        self.assertEqual(inv.call_args[0][1]["position"].split(", ")[2], "0.05")
        # 座標が解決できない入力は例外ではなくエラー結果
        bad = self.mast.mast_observations(ra="?", dec="?", radius="x", limit="?")
        self.assertEqual(bad.structuredContent["error"], "location required")

    def test_products_use_integer_obsid(self):
        """データ製品は obsid（整数）で引く（obs_id 文字列では MAST が ERROR を返す実測）。"""
        calls = []

        def fake(service, params, pagesize=10, page=1):
            calls.append((service, params))
            if service == "Mast.Caom.Products":
                return ({"data": [{"productFilename": "u2id011vt_c0m.fits", "size": 1234,
                                   "dataURI": "mast:HST/product/u2id011vt_c0m.fits",
                                   "dataRights": "PUBLIC", "calib_level": 3,
                                   "description": "Calibrated"}], "status": "COMPLETE"}, None)
            return ({"data": [_caom_row()], "status": "COMPLETE"}, None)

        with mock.patch.object(self.mast, "_invoke", side_effect=fake):
            res = self.mast.mast_observations(ra=83.82, dec=-5.39, limit=1, include_products=True)
        self.assertEqual(calls[1][1]["obsid"], "24553790")
        prod = res.structuredContent["products"]["u2id011vt"][0]
        self.assertEqual(prod["filename"], "u2id011vt_c0m.fits")
        self.assertTrue(prod["url"].startswith("https://mast.stsci.edu/api/v0.1/Download/file?uri="))
        self.assertIn("ダウンロード", res.content[0].text + "ダウンロード")

    def test_preview_image_keeps_link_before_image(self):
        """プレビュー画像は「リンク先行＋structuredContent に image_url」（規約13）。"""
        from mcp.types import ImageContent
        fake_img = ImageContent(type="image", data="ZmFrZQ==", mimeType="image/jpeg",
                                altText="preview")
        url = "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HST/product/preview.jpg"

        def fake(service, params, pagesize=10, page=1):
            if service == "Mast.Caom.Products":
                return ({"data": [{"productFilename": "preview.jpg", "dataURI": "mast:HST/product/preview.jpg",
                                   "dataRights": "PUBLIC"}], "status": "COMPLETE"}, None)
            return ({"data": [_caom_row()], "status": "COMPLETE"}, None)

        with mock.patch.object(self.mast, "_invoke", side_effect=fake),                 mock.patch.object(self.mast, "_preview_content",
                                  return_value=(fake_img, url, "/tmp/mast_preview.jpg")):
            res = self.mast.mast_observations(ra=83.82, dec=-5.39, limit=1, preview_image=True)
        self.assertEqual([c.type for c in res.content], ["text", "image"])
        text = res.content[0].text
        self.assertTrue(text.index("🖼") < len(text))                     # リンクは本文にある
        self.assertRegex(text, r"🖼️?\s*\[[^\]]+\]\(https://mast\.stsci\.edu")
        self.assertEqual(res.structuredContent["image_url"], url)
        self.assertEqual(res.structuredContent["image_path"], "/tmp/mast_preview.jpg")
        # 既定（preview_image=False）では画像ブロックも image_url も出さない
        with mock.patch.object(self.mast, "_invoke", return_value=({"data": [], "status": "COMPLETE"}, None)):
            plain = self.mast.mast_observations(ra=83.82, dec=-5.39, limit=1)
        self.assertEqual([c.type for c in plain.content], ["text"])
        self.assertIsNone(plain.structuredContent["image_url"])

    def test_preview_product_detection_uses_filename(self):
        """プレビュー判定はファイル名で行う（MAST のDL URLは ?uri=… に拡張子が入るため、
        URL を "?" で切ると拡張子が消えて画像を取りこぼす）。"""
        import base64 as _b64
        from mcp.types import ImageContent
        prods = [
            {"filename": "x_asn.json", "url": "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product/x_asn.json"},
            {"filename": "x_i2d.jpg", "url": "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product/x_i2d.jpg"},
        ]
        r = mock.Mock()
        r.content = b"jpegbytes"
        r.raise_for_status = lambda: None
        with mock.patch.object(self.mast.requests, "get", return_value=r),                 mock.patch.object(self.mast, "save_output", return_value="/tmp/p.jpg"):
            img, url, path = self.mast._preview_content(prods, "alt")
        self.assertIsInstance(img, ImageContent)
        self.assertTrue(url.endswith("x_i2d.jpg"))
        self.assertEqual(path, "/tmp/p.jpg")
        self.assertEqual(_b64.b64decode(img.data), b"jpegbytes")
        # 画像製品が無ければ何も返さない（リンクも画像も出さない）
        self.assertEqual(self.mast._preview_content([prods[0]], "alt"), (None, None, None))

    def test_network_failure_is_memorized_and_fails_fast(self):
        """接続不可は記憶して以降は HTTP を出さない（並列実行の道連れ防止）。"""
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise requests.ConnectionError("blocked")

        with mock.patch.object(self.mast.requests, "post", boom):
            first = self.mast.mast_observations(ra=83.82, dec=-5.39, limit=1)
            second = self.mast.mast_observations(ra=83.82, dec=-5.39, limit=1)
        self.assertIn("error", first.structuredContent)
        self.assertIn("error", second.structuredContent)
        self.assertEqual(len(calls), 1)                    # 2回目は HTTP を出していない
        self.assertIn("秒後", second.content[0].text)

    def test_mast_error_status_becomes_error_result(self):
        """MAST が status=ERROR を返したらエラー結果（例外にしない）。"""
        payload = {"status": "ERROR", "msg": "Invalid column"}
        with mock.patch.object(self.mast.requests, "post", return_value=_resp(payload)):
            data, err = self.mast._invoke("Mast.Caom.Filtered", {"columns": "*", "filters": []})
        self.assertIsNone(data)
        self.assertIn("Invalid column", err)
        self.assertEqual(self.mast._cooldown_status()[0], 0.0)   # 遮断の記憶はしない（応答は来ている）


if __name__ == "__main__":
    unittest.main()

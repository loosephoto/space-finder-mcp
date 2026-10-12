"""DLR EOC STAC / Roscosmos オープンデータ STAC の回帰テスト（ネットワーク不要）。

再発防止する仕様（実測で確認したもの）:
- DLR の STAC は `filter`（CQL2）も `sortby` も受け付けない（前者は 400、後者は無視）。
  雲量の絞り込みは取得後にクライアント側で行い、「除外件数」と「雲量情報なし件数」を
  分けて返す（サーバー側フィルタを装わない）。
- Roscosmos のカタログは静的で `/search` を持たない。日ノードは親（年・月）を辿らず
  直接 GET できるので、日付からパスを組み立てて1リクエストで引く。
- Roscosmos の GeoTIFF アセットは `proj:epsg` を持たないことがあり、キー
  （`image.tif.ms.4326._`）に EPSG が入っている。
- 未知のコレクションパスは推測せず、候補を返して停止する。
"""
import sys
import unittest

sys.path.insert(0, "src")

import requests


class _Resp:
    """requests.Response の最小の代役（status_code / json / raise_for_status）。"""

    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP {}: error".format(self.status_code))


class DlrSearchTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import dlr
        self.dlr = dlr
        self._post = dlr.requests.post
        self._get = dlr.requests.get

    def tearDown(self):
        self.dlr.requests.post = self._post
        self.dlr.requests.get = self._get

    def test_cloud_filter_is_client_side_and_counts_exclusions(self):
        features = [
            {"id": "A", "collection": "S2_L2A_MAJA", "bbox": [1, 2, 3, 4],
             "properties": {"datetime": "2026-01-01T00:00:00Z", "eo:cloud_cover": 5},
             "assets": {"thumbnail": {"href": "https://x/a.jpg", "roles": ["thumbnail"], "type": "image/png"}}},
            {"id": "B", "collection": "S2_L2A_MAJA",
             "properties": {"datetime": "2026-01-02T00:00:00Z", "eo:cloud_cover": 40}, "assets": {}},
            {"id": "C", "collection": "S2_L2A_MAJA",
             "properties": {"datetime": "2026-01-03T00:00:00Z"}, "assets": {}},
        ]
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _Resp({"type": "FeatureCollection", "numberMatched": 3,
                          "numberReturned": 3, "features": features})

        self.dlr.requests.post = fake_post
        r = self.dlr.dlr_search("S2_L2A_MAJA", max_cloud_cover=20, limit=2,
                                datetime="2026-01-01/2026-01-31")
        sc = r.structuredContent
        self.assertIsNone(sc.get("error"))
        self.assertEqual([x["id"] for x in sc["results"]], ["A"])
        self.assertEqual(sc["excluded_by_cloud"], 1)     # 雲量 40% で除外
        self.assertEqual(sc["without_cloud_info"], 1)    # 雲量情報が無いので除外（別枠で数える）
        self.assertEqual(sc["matched"], 3)
        # 雲量で絞るときは多めに取る（サーバー側フィルタが無いため）
        self.assertEqual(calls[0]["limit"], 8)
        self.assertNotIn("filter", calls[0])
        text = r.content[0].text
        self.assertIn("🖼️ [画像を開く: A のサムネイル](https://x/a.jpg)", text)
        self.assertIn("サーバー側フィルタ非対応", text)

    def test_bad_bbox_stops_without_network(self):
        def boom(*a, **k):
            raise AssertionError("ネットワークを呼んではいけない")

        self.dlr.requests.post = boom
        r = self.dlr.dlr_search("S2_L2A_MAJA", bbox="1,2,3")
        self.assertEqual(r.structuredContent["error"], "bad bbox")

    def test_network_failure_is_error_result_not_exception(self):
        def boom(*a, **k):
            raise requests.ConnectionError("blocked")

        self.dlr.requests.post = boom
        r = self.dlr.dlr_search("S2_L2A_MAJA", bbox="1,2,3,4")
        self.assertTrue(r.structuredContent.get("error"))
        self.assertEqual(r.structuredContent["source"], "geoservice.dlr.de/eoc/ogc/stac/v1")

    def test_collections_report_license_caveat(self):
        self.dlr.requests.get = lambda *a, **k: _Resp({
            "collections": [{"id": "S2_L2A_MAJA", "title": "S2", "description": "d",
                             "license": "CC-BY-4.0"}]})
        r = self.dlr.dlr_collections(limit=5)
        sc = r.structuredContent
        self.assertEqual(sc["total"], 1)
        self.assertEqual(sc["results"][0]["license"], "CC-BY-4.0")
        self.assertIn("★", r.content[0].text)
        self.assertIn("ライセンス", r.content[0].text)


class RoscosmosCatalogTests(unittest.TestCase):
    """静的 STAC（/search なし）を木を辿って読む。"""

    def setUp(self):
        from space_finder_mcp import roscosmos
        self.rs = roscosmos
        self._get = roscosmos.requests.get
        self.rs._STATE["base"] = self.rs.BASES[0]
        self.rs._STATE["dead"] = set()

    def tearDown(self):
        self.rs.requests.get = self._get
        self.rs._STATE["base"] = self.rs.BASES[0]
        self.rs._STATE["dead"] = set()

    def _serve(self, docs, base=None):
        base = base or self.rs.BASES[0]

        def fake_get(url, headers=None, timeout=None):
            path = url[len(base) + 1:] if url.startswith(base + "/") else url
            if path in docs:
                return _Resp(docs[path])
            return _Resp(None, status=404)
        self.rs.requests.get = fake_get

    def test_collections_walks_two_levels(self):
        self._serve({
            "catalog.json": {"type": "Catalog", "links": [
                {"rel": "child", "href": self.rs.BASES[0] + "/Electro-L/collection.json", "title": "Electro-L"},
                {"rel": "child", "href": self.rs.BASES[0] + "/MM/collection.json", "title": "MM"}]},
            "Electro-L/collection.json": {"id": "roscosmos-opendata.Electro-L", "type": "Collection",
                                          "summaries": {"platform": ["EL2"], "gsd": ["1000"]},
                                          "extent": {"temporal": {"interval": [["2022-04-01T01:00:00", "2026-10-12T00:00:00"]]}},
                                          "links": [{"rel": "child", "href": self.rs.BASES[0] + "/Electro-L/MSU-GS/collection.json"}]},
            "Electro-L/MSU-GS/collection.json": {"id": "roscosmos-opendata.Electro-L.MSU-GS", "type": "Collection",
                                                 "links": [{"rel": "child", "href": self.rs.BASES[0] + "/Electro-L/MSU-GS/L2/collection.json"}]},
            "MM/collection.json": {"id": "roscosmos-opendata.MM", "type": "Collection", "links": []},
        })
        r = self.rs.roscosmos_collections(level=2)
        sc = r.structuredContent
        self.assertIsNone(sc.get("error"))
        paths = [x["path"] for x in sc["results"]]
        self.assertEqual(paths, ["Electro-L", "MM", "Electro-L/MSU-GS"])
        self.assertEqual(sc["results"][0]["temporal"][0][:10], "2022-04-01")
        self.assertEqual(sc["results"][0]["platforms"], ["EL2"])
        self.assertIn("出典", r.content[0].text)

    def test_search_resolves_day_and_reads_epsg_from_asset_key(self):
        item = {
            "type": "Feature", "id": "ETRIS.EL2.MSU-GS._._._.2026-10-11T0200.L2_RGB_VIS_IR._.NTSOMZ_MSK",
            "bbox": [-86.7, -73.6, 57.8, 73.6],
            "properties": {"title": "EL2.MSU-GS.2026-10-11T0200.L2_RGB_VIS_IR",
                           "datetime": "2026-10-11T02:00:00.000000Z", "platform": "EL2",
                           "instruments": ["MSU-GS"], "processing:level": "L2_RGB_VIS_IR", "gsd": 1000.0},
            "assets": {
                "thumbnail.png.ms.3857._": {"href": "https://x/q.png", "type": "image/png",
                                            "roles": ["thumbnail"]},
                "image.tif.ms.4326._": {"href": "https://x/d.tif", "type": "image/tiff",
                                        "file:size": 2097152},
            },
        }
        self._serve({
            "Electro-L/MSU-GS/L2_RGB_VIS_IR/collection.json": {"id": "roscosmos-opendata.Electro-L.MSU-GS.L2_RGB_VIS_IR",
                                                               "type": "Collection", "links": []},
            "Electro-L/MSU-GS/L2_RGB_VIS_IR/2026/10/11/collection.json": {
                "id": "…2026.10.11", "type": "Collection",
                "links": [{"rel": "item", "href": self.rs.BASES[0] + "/EL/…/item.json"}]},
            "EL/…/item.json": item,
        })
        r = self.rs.roscosmos_search("Electro-L/MSU-GS/L2_RGB_VIS_IR", date="2026-10-11", limit=1)
        sc = r.structuredContent
        self.assertIsNone(sc.get("error"))
        self.assertEqual(sc["date"], "2026-10-11")
        rec = sc["results"][0]
        self.assertEqual(rec["quicklook_url"], "https://x/q.png")
        self.assertIn("4326", rec["data"])            # キー `image.tif.ms.4326._` から EPSG を読む
        self.assertEqual(rec["data"]["4326"]["size_mb"], 2.0)
        text = r.content[0].text
        self.assertIn("🖼️ [画像を開く: EL2.MSU-GS.2026-10-11T0200.L2_RGB_VIS_IR のクイックルック](https://x/q.png)", text)
        self.assertIn("📄 [ファイルを開く: GeoTIFF（EPSG:4326）（2.0 MB）](https://x/d.tif)", text)

    def test_unknown_collection_returns_candidates_without_guessing(self):
        self._serve({
            "catalog.json": {"type": "Catalog", "links": [
                {"rel": "child", "href": self.rs.BASES[0] + "/MM/collection.json", "title": "MM"}]},
            "MM/collection.json": {"id": "roscosmos-opendata.MM", "type": "Collection", "links": []},
        })
        r = self.rs.roscosmos_search("Nope/XX", limit=1)
        sc = r.structuredContent
        self.assertEqual(sc["error"], "unknown collection")
        self.assertIn("MM", sc["candidates"])
        self.assertIn("候補", r.content[0].text)

    def test_bad_date_is_reported_without_network(self):
        def boom(*a, **k):
            raise AssertionError("ネットワークを呼んではいけない")

        self.rs.requests.get = boom
        r = self.rs.roscosmos_search("Electro-L/MSU-GS/L2", date="2026-13-99")
        self.assertEqual(r.structuredContent["error"], "bad date")

    def test_host_fallback_then_unavailable(self):
        """片方のホストが接続不可なら他方へ切り替え、両方だめならエラー結果を返す。"""
        docs = {
            "catalog.json": {"type": "Catalog", "links": [
                {"rel": "child", "href": self.rs.BASES[0] + "/MM/collection.json", "title": "MM"}]},
            "MM/collection.json": {"id": "roscosmos-opendata.MM", "type": "Collection", "links": []},
        }

        def get_primary_dead(url, headers=None, timeout=None):
            if url.startswith(self.rs.BASES[0]):
                raise requests.ConnectionError("primary blocked")
            path = url[len(self.rs.BASES[1]) + 1:]
            return _Resp(docs[path]) if path in docs else _Resp(None, status=404)

        self.rs.requests.get = get_primary_dead
        r = self.rs.roscosmos_collections(level=1)
        self.assertIsNone(r.structuredContent.get("error"))
        self.assertEqual(r.structuredContent["shown"], 1)
        self.assertEqual(self.rs._STATE["base"], self.rs.BASES[1])

        self.rs._STATE["base"] = self.rs.BASES[0]
        self.rs._STATE["dead"] = set()

        def all_dead(*a, **k):
            raise requests.ConnectionError("blocked")

        self.rs.requests.get = all_dead
        r2 = self.rs.roscosmos_collections(level=3)
        self.assertTrue(r2.structuredContent.get("error"))
        self.assertEqual(self.rs._STATE["dead"], set(self.rs.BASES))


class RoscosmosHelpersTests(unittest.TestCase):
    def test_strip_base_and_links(self):
        from space_finder_mcp import roscosmos as rs
        href = rs.BASES[0] + "/Electro-L/MSU-GS/collection.json"
        self.assertEqual(rs._strip_base(href), "Electro-L/MSU-GS/collection.json")
        doc = {"links": [{"rel": "child", "href": href, "title": "t"},
                         {"rel": "item", "href": rs.BASES[1] + "/a/item.json"},
                         {"rel": "self", "href": href}]}
        self.assertEqual(rs._child_links(doc), [("t", "Electro-L/MSU-GS/collection.json")])
        self.assertEqual(rs._item_links(doc), ["a/item.json"])

    def test_parse_date_accepts_iso_and_rejects_garbage(self):
        from space_finder_mcp import roscosmos as rs
        d, err = rs._parse_date("2026-10-11T02:00:00Z")
        self.assertIsNone(err)
        self.assertEqual(str(d), "2026-10-11")
        d2, err2 = rs._parse_date("2026/10/11")
        self.assertIsNone(d2)
        self.assertIn("YYYY-MM-DD", err2)


if __name__ == "__main__":
    unittest.main()

"""学術文献ツール（literature.py）の回帰テスト。

実測した挙動の再発防止（すべて実際に踏んだバグ・落とし穴）:
- Crossref の `select` に `language` を入れると **HTTP 400**（select-not-available）になる
- Crossref は裸の天体名（1〜2語）で `query.bibliographic` を使うと無関係な高被引用論文を返す
  （"Mars" → FinnGen の遺伝学論文）→ **2語以下は `query.title`**
- J-STAGE の **`ERR_001` は「該当なし」**であってエラーではない（"大気流出" が ERR_001、"火星" は
  status=0 で 159件）。`WARN_002` は結果を伴う警告。`ERR_011` はパラメータ不正（service=2 用）
- OpenAlex の `search=`（全文）は無関係な文献を拾う（"Mars" 単独で R 言語マニュアルが1位）
  → **`title_and_abstract.search`** を使う。裸の天体名は **惑星科学概念で絞ると精度が上がる**
- **429 は1回だけ再試行**し、`Retry-After` はクランプする（恒久的な遮断で固まらないため）
- 日本語クエリは英語語へ置換して英語圏ソースへ。置換できないときは**スキップして理由を返す**
- **キャッシュされた戻り値を書き換えない**（書き換えると並行時に他人の結果を壊す）
"""
import json
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, "src")


class FakeResp:
    """requests.Response の最小の代役。"""

    def __init__(self, payload="", status=200, headers=None, ctype="application/json"):
        self.status_code = status
        self.text = payload
        self.content = payload.encode("utf-8")
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", ctype)

    def json(self):
        return json.loads(self.text)


def fake_get(handlers, calls):
    """URL の断片 → 応答（または params を受けて応答を返す関数）で HTTP を差し替える。"""
    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        for frag, handler in handlers.items():
            if frag in url:
                return handler(url, params or {}) if callable(handler) else handler
        raise AssertionError("テストが想定していない URL: " + url)
    return _get


def json_route(obj, status=200, headers=None):
    return FakeResp(json.dumps(obj, ensure_ascii=False), status=status, headers=headers)


OPENALEX_JSON = {
    "results": [{
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1029/2019JE006107",
        "title": "THEMIS Observations of the 2018 Mars Global Dust Storm",
        "publication_year": 2019, "cited_by_count": 71, "type": "article", "language": "en",
        "authorships": [{"author": {"display_name": "Michael D. Smith"}}],
        "primary_location": {"landing_page_url": "https://doi.org/10.1029/2019JE006107",
                             "source": {"display_name": "JGR Planets"}},
        "open_access": {"is_oa": False},
        "abstract_inverted_index": {"Mars": [0], "dust": [1], "storm.": [2]},
    }],
}
CROSSREF_JSON = {"message": {"items": [
    {"DOI": "10.1016/S0140-6736(03)14286-9", "title": ["Efficacy of perindopril (the EUROPA study)"],
     "container-title": ["The Lancet"], "issued": {"date-parts": [[2003, 9]]},
     "is-referenced-by-count": 1541, "type": "journal-article"},
    {"DOI": "10.1038/34857", "title": ["Evidence for a subsurface ocean on Europa"],
     "container-title": ["Nature"], "issued": {"date-parts": [[1998]]},
     "is-referenced-by-count": 593, "type": "journal-article"},
]}}
NTRS_JSON = {"results": [{
    "id": "abc", "title": "The Mars Sample Return Project", "abstract": "MSR is underway.",
    "center": {"name": "Jet Propulsion Laboratory"}, "stiType": "OTHER",
    "publications": [{"submissionId": "20210001177", "publicationDate": "1999-10-04T00:00:00+00:00"}],
    "authorAffiliations": [{"meta": {"author": '"O\'Neil, W.", \'Cazaux, C.\''}}],
    "downloads": [{"links": {"pdf": "/api/citations/20210001177/downloads/20210001177.pdf"}}],
}]}
CINII_JSON = {"opensearch:totalResults": 1, "items": [{
    "@id": "https://cir.nii.ac.jp/crid/1572543027352227072", "title": "火星大気の流出",
    "dc:creator": ["益永 圭"], "prism:publicationName": "遊・星・人 : 日本惑星科学会誌",
    "prism:publicationDate": "2023-09-25", "dc:type": "Article"}]}
JAXA_JSON = {"hits": {"total": 1, "hits": [{
    "id": 6734, "metadata": {
        "title": ["Trajectory Guidance Operation of Hayabusa2"],
        "creator": {"creatorName": ["吉川, 真", "Yoshikawa, Makoto"]},
        "date": [{"dateType": "Issued", "value": "2018-07"}],
        "publisher": ["JAXA(ISAS)"], "itemtype": "conference paper",
        "description": [{"descriptionType": "Other", "value": "会議概要（要旨ではない）"}],
    }}]}}
ZENODO_JSON = {"hits": {"hits": [{
    "id": 6698504, "doi": "10.5281/zenodo.6698504",
    "metadata": {"title": "Mars Express HRSC dataset", "doi": "10.5281/zenodo.6698504",
                 "publication_date": "2022-06-23", "resource_type": {"type": "dataset"},
                 "creators": [{"name": "Monica Pondrelli"}], "description": "<p>desc</p>"},
    "links": {"self_html": "https://zenodo.org/records/6698504"}}]}}
DATACITE_JSON = {"data": [{
    "attributes": {"doi": "10.17632/x.3", "publicationYear": "2026", "url": "https://data.example/x",
                   "publisher": "Mendeley Data", "titles": [{"title": "Aerosol vertical distribution on Mars"}],
                   "creators": [{"name": "Fedorova, Anna"}], "language": "en",
                   "types": {"resourceTypeGeneral": "Dataset"},
                   "descriptions": [{"descriptionType": "Abstract", "description": "Airborne dust."}]}}]}
JSTAGE_XML_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xml:lang="ja">
  <result><status>0</status><message/></result>
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <article_title><en><![CDATA[General Remarks]]></en><ja><![CDATA[総論]]></ja></article_title>
    <article_link><en>https://www.jstage.jst.go.jp/article/tits/26/8/26_8_56/_article</en></article_link>
    <author><en><name><![CDATA[Kenji SATAKE]]></name></en></author>
    <material_title><en><![CDATA[TRENDS IN THE SCIENCES]]></en></material_title>
    <pubyear>2021</pubyear><prism:doi>10.5363/tits.26.8_56</prism:doi>
    <link href="https://www.jstage.jst.go.jp/article/tits/26/8/26_8_56/_article"/>
  </entry>
</feed>"""


def jstage_status(code):
    return """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><result><status>{}</status><message>{}</message></result></feed>""".format(code, code)


def default_handlers(extra=None):
    """全ソースを架空の応答で置き換える（ネットワークへ出ない）。"""
    h = {
        "api.openalex.org": json_route(OPENALEX_JSON),
        "api.crossref.org": json_route(CROSSREF_JSON),
        "ntrs.nasa.gov": json_route(NTRS_JSON),
        "cir.nii.ac.jp": json_route(CINII_JSON),
        "jaxa.repo.nii.ac.jp": json_route(JAXA_JSON),
        "zenodo.org": json_route(ZENODO_JSON),
        "api.datacite.org": json_route(DATACITE_JSON),
        "api.jstage.jst.go.jp": FakeResp(JSTAGE_XML_OK, ctype="application/atom+xml"),
        "cds.unistra.fr": FakeResp("<xml/>", status=404, ctype="application/xml"),   # Sesame は未解決
    }
    h.update(extra or {})
    return h


class LiteratureTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import literature, name_common
        self.L = literature
        self.NC = name_common
        literature._search_source.cache_clear()          # テスト間でキャッシュを共有しない
        name_common._sesame_lookup.cache_clear()

    def _patch(self, extra=None):
        calls = []
        patcher = mock.patch.object(requests, "get", fake_get(default_handlers(extra), calls))
        return patcher, calls

    # ---- 1. Crossref: select に language を入れない／語数でクエリフィールドを選ぶ ----
    def test_crossref_query_field_and_select(self):
        patcher, calls = self._patch()
        with patcher:
            self.L._crossref("Mars", 3, None, None, "relevance", False)
            self.L._crossref("Mars global dust storm", 3, None, None, "relevance", False)
        cr = [c for c in calls if "api.crossref.org" in c["url"]]
        self.assertIn("query.title", cr[0]["params"])              # 2語以下はタイトル検索
        self.assertNotIn("query.bibliographic", cr[0]["params"])
        self.assertIn("query.bibliographic", cr[1]["params"])      # 3語以上は書誌検索
        for c in cr:
            self.assertNotIn("language", c["params"]["select"])    # 実測: 入れると HTTP 400

    # ---- 2. OpenAlex: 全文検索を使わず title_and_abstract.search ＋ 概念フィルタ ----
    def test_openalex_uses_title_abstract_search_and_concept(self):
        patcher, calls = self._patch()
        with patcher:
            self.L._openalex("Mars", 3, None, None, "citations", False,
                             concept=self.L.PLANETARY_CONCEPT)
        oa = [c for c in calls if "api.openalex.org" in c["url"]][0]
        self.assertNotIn("search", oa["params"])                   # 実測: 全文検索は無関係な文献を拾う
        self.assertIn("title_and_abstract.search:Mars", oa["params"]["filter"])
        self.assertIn("concepts.id:" + self.L.PLANETARY_CONCEPT, oa["params"]["filter"])
        self.assertEqual(oa["params"]["sort"], "cited_by_count:desc")

    # ---- 3. J-STAGE: ERR_001 は「該当なし」、WARN_002 は結果あり、ERR_011 はエラー ----
    def test_jstage_status_codes(self):
        def fixed(xml):
            return {"api.jstage.jst.go.jp": FakeResp(xml, ctype="application/atom+xml")}
        with mock.patch.object(requests, "get", fake_get(fixed(jstage_status("ERR_001")), [])):
            self.assertEqual(self.L._jstage("大気流出", 3, None, None, "relevance", False), [])
        with mock.patch.object(requests, "get", fake_get(fixed(JSTAGE_XML_OK), [])):
            recs = self.L._jstage("火星", 3, None, None, "relevance", False)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["doi"], "10.5363/tits.26.8_56")
        with mock.patch.object(requests, "get", fake_get(fixed(jstage_status("ERR_011")), [])):
            with self.assertRaises(RuntimeError):
                self.L._jstage("火星", 3, None, None, "relevance", False)

    # ---- 4. 429 は1回だけ再試行し、Retry-After をクランプする ----
    def test_retry_429_once_with_clamped_wait(self):
        seq = []

        def handler(url, params):
            seq.append(url)
            if len(seq) == 1:
                return FakeResp('{"message":"slow down"}', status=429,
                                headers={"Retry-After": "99999"})
            return FakeResp(json.dumps(OPENALEX_JSON))

        with mock.patch.object(requests, "get", fake_get({"api.openalex.org": handler}, [])), \
                mock.patch("time.sleep") as sl:
            recs = self.L._openalex("Mars", 1, None, None, "relevance", False)
        self.assertEqual(len(seq), 2)
        self.assertEqual(len(recs), 1)
        sl.assert_called_once()
        self.assertLessEqual(sl.call_args[0][0], 5.0)              # 19時間待ちのような値で固まらない

    def test_persistent_429_becomes_error_report(self):
        with mock.patch.object(requests, "get",
                               fake_get({"api.openalex.org": FakeResp("{}", status=429)}, [])), \
                mock.patch("time.sleep"):
            recs, rep = self.L._search_source("openalex", "Mars", 3, None, None, "relevance", False)
        self.assertEqual(recs, [])
        self.assertEqual(rep["status"], "error")
        self.assertIn("429", rep["error"])

    # ---- 5. 日本語クエリの英語化と、できないときのスキップ ----
    def test_japanese_query_translation(self):
        self.assertEqual(self.L.to_english("はやぶさ2 リュウグウ")["text"], "hayabusa2 Ryugu")
        self.assertEqual(self.L.to_english("火星 大気流出")["text"], "Mars atmospheric escape")
        self.assertTrue(self.L.to_english("最近の研究")["untranslated"])

    def test_untranslatable_japanese_skips_english_sources(self):
        patcher, _ = self._patch()
        with patcher:
            res = self.L.search("最近の研究", sources="auto", limit=3)
        st = {s["source"]: s for s in res["sources"]}
        self.assertEqual(st["openalex"]["status"], "skipped")
        self.assertEqual(st["openalex"]["skip_reason"], "no_english_query")
        self.assertTrue(st["cinii"]["status"] in ("ok", "empty"))

    # ---- 6. DOI 重複の統合（被引用数は大きい方を採用） ----
    def test_merge_same_doi_across_sources(self):
        cross = {"message": {"items": [{
            "DOI": "10.1029/2019JE006107",
            "title": ["THEMIS Observations of the 2018 Mars Global Dust Storm"],
            "issued": {"date-parts": [[2019]]}, "is-referenced-by-count": 99,
            "type": "journal-article"}]}}
        with self._patch({"api.crossref.org": json_route(cross)})[0]:
            res = self.L.search("Mars global dust storm", sources="openalex,crossref", limit=5)
        self.assertEqual(len(res["results"]), 1)
        rec = res["results"][0]
        self.assertEqual(rec["source"], "openalex")            # 先に来たソースを代表にする
        self.assertIn("crossref", rec["also_in"])              # 他ソースにもあることを残す
        self.assertEqual(rec["citations"], 99)                 # 大きい方の被引用数

    # ---- 7. キャッシュされた戻り値を書き換えない（並行時に他人の結果を壊さない） ----
    def test_cached_records_are_not_mutated_by_callers(self):
        patcher, _ = self._patch()
        with patcher:
            r1 = self.L.search("Mars global dust storm", sources="openalex", limit=3)
            self.assertTrue(r1["results"])
            r1["results"][0]["title"] = "MUTATED"
            r1["results"][0]["authors"].append("追加")
            r2 = self.L.search("Mars global dust storm", sources="openalex", limit=3)
        self.assertNotEqual(r2["results"][0]["title"], "MUTATED")
        self.assertNotIn("追加", r2["results"][0]["authors"])

    # ---- 8. 要旨は MAX_ABSTRACT で切る（トークン量の抑制） ----
    def test_abstract_truncated(self):
        big = {"results": [dict(OPENALEX_JSON["results"][0],
                                abstract_inverted_index={("w%d" % i): [i] for i in range(5000)})]}
        with self._patch({"api.openalex.org": json_route(big)})[0]:
            res = self.L.search("Mars", sources="openalex", limit=1)
        self.assertLessEqual(len(res["results"][0]["abstract"]), self.L.MAX_ABSTRACT)

    # ---- 9. 全ソース失敗でも例外を漏らさずエラー結果を返す ----
    def test_all_sources_failed_returns_error_result(self):
        def boom(*a, **k):
            raise requests.ConnectionError("simulated network failure")
        with mock.patch.object(requests, "get", boom):
            r = self.L.space_literature_search(query="Mars dust storm")
            r2 = self.L.planetary_evidence(object_name="火星")
        self.assertEqual(r.structuredContent["error"], "all sources failed")
        self.assertEqual(r2.structuredContent["error"], "all sources failed")
        self.assertTrue(r.content[0].text and r2.content[0].text)
        self.assertNotIn("api_key", json.dumps(r.structuredContent, ensure_ascii=False))

    # ---- 10. 不正・未知の引数でも例外を漏らさない ----
    def test_invalid_arguments_do_not_raise(self):
        for kw in ({"query": None}, {"query": ""}, {"query": "Mars", "limit": "abc"},
                   {"query": "Mars", "limit": -5}, {"query": "Mars", "sort": 123,
                                                    "year_from": "?", "include_abstract": "false",
                                                    "planetary_only": "yes"},
                   {"query": "Mars", "sources": "unknown_src"}):
            with self._patch()[0]:
                r = self.L.space_literature_search(**kw)
            self.assertIsNotNone(r.structuredContent)
        with self._patch()[0]:
            r = self.L.space_literature_search(query="Mars", sources="unknown_src")
        self.assertEqual(r.structuredContent["error"], "unknown sources")
        with self._patch()[0]:
            r = self.L.planetary_evidence(object_name=None)
        self.assertEqual(r.structuredContent["error"], "missing object_name")

    # ---- 11. NTRS の著者は連結文字列を分解して読む／計画中の概念フィルタ順位付け ----
    def test_ntrs_authors_split_and_pdf_url(self):
        recs = self.L._ntrs_authors('"O\'Neil, W.", \'Cazaux, C.\'')
        self.assertEqual(recs, ["O'Neil, W.", "Cazaux, C."])
        patcher, _ = self._patch()
        with patcher:
            out = self.L._ntrs("Mars Sample Return", 3, None, None, "relevance", False)
        self.assertEqual(out[0]["pdf_url"],
                         "https://ntrs.nasa.gov/api/citations/20210001177/downloads/20210001177.pdf")
        self.assertEqual(out[0]["url"], "https://ntrs.nasa.gov/citations/20210001177")

    def test_planetary_evidence_prefers_concept_filtered_openalex(self):
        patcher, _ = self._patch()
        with patcher:
            r = self.L.planetary_evidence(object_name="火星", limit=3)
        sc = r.structuredContent
        self.assertEqual(sc["results"][0]["source"], "openalex")   # 概念フィルタが効くソースを上位へ
        self.assertTrue(any("概念フィルタ" in c for c in sc["caveats"]))
        self.assertTrue(any("文献（書誌）" in c for c in sc["caveats"]))
        self.assertEqual(sc["resolved"], None)                     # Sesame 未解決（モックは404）
        self.assertEqual(sc["queries_used"], ["Mars", "火星"])
        self.assertIn("JAXA Research and Development Repository", sc["credit"])


if __name__ == "__main__":
    unittest.main()

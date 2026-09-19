"""NASA GCN（GCN Circulars）ツールの回帰テスト。

実測した挙動の再発防止:
- 一覧は HTML（公開アーカイブのページ）が既定。サイト内部の `?_data=` JSON ルートは
  403 を返すことがある（実測 2026-09）ので、HTML が失敗したときだけ JSON を試す
- 本文の入れ物は投稿経路で違う（メール投稿=pre/code、Web フォーム=usa-paragraph）
- HTML 経路は1ページ100件で、総件数が分からない → 「全N件」と断定しない
- 404 は「存在しない Circular」として案内する（接続失敗と混同しない）
"""
import datetime as dt
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, "src")

LIST_HTML = """<ol>
<li value="45640"><a class="usa-link" data-discover="true" href="/circulars/45640?startDate=2026-09-13&amp;endDate=2026-09-19">EP Alert 260918.62: Global MASTER-Net observations report</a></li>
<li value="45639"><a class="usa-link" data-discover="true" href="/circulars/45639?startDate=2026-09-13&amp;endDate=2026-09-19">Fermi GBM Sub-Threshold Detection of GRB 260918A</a></li>
<li value="45639"><a class="usa-link" href="/circulars/45639?x=1">同番号の重複行</a></li>
</ol>"""

LIST_JSON = '{"page":1,"items":[{"circularId":"45640","subject":"EP Alert 260918.62: Global MASTER-Net observations report"},{"circularId":"45639","subject":"Fermi GBM Sub-Threshold Detection of GRB 260918A"}],"totalItems":84,"limit":100,"isGroupView":false}'

DETAIL_EMAIL = """<html><head><title>GCN - Circulars - 45640 - EP Alert 260918.62: Global MASTER-Net observations report</title></head><body>
<small>(<span title="Saturday, September 19, 2026 at 2:18:54 AM UTC">5 hours ago</span>)</small>
<div class="grid-row" data-testid="grid"><div class="desktop:grid-col-1" data-testid="grid"><b>From</b></div><div class="grid-col-fill" data-testid="grid">Vladimir Lipunov at Moscow State U/Krylov Obs &lt;lipunov@xray.sai.msu.ru&gt;</div></div>
<div class="grid-row" data-testid="grid"><div class="desktop:grid-col-1" data-testid="grid"><b>Via</b></div><div class="grid-col-fill" data-testid="grid">email</div></div>
<div class="FBbHX margin-y-2"><pre><code>V.Lipunov reports on behalf of the MASTER team:
MASTER-OAFA observed the EP alert at 2026-09-18 04:11:39 UT.</code></pre></div>
</body></html>"""

DETAIL_FORM = """<html><head><title>GCN - Circulars - 45639 - Fermi GBM Sub-Threshold Detection of GRB 260918A</title></head><body>
<div class="grid-row" data-testid="grid"><div class="desktop:grid-col-1" data-testid="grid"><b>From</b></div><div class="grid-col-fill" data-testid="grid">Angus Jameson &lt;abj0023@uah.edu&gt;</div></div>
<div class="grid-row" data-testid="grid"><div class="desktop:grid-col-1" data-testid="grid"><b>Via</b></div><div class="grid-col-fill" data-testid="grid">Web form</div></div>
<div class="YEsaP margin-y-2"><p class="usa-paragraph">A. Jameson (UAH) reports on behalf of the Fermi-GBM Team:</p> <p class="usa-paragraph">We detect a sub-threshold burst.</p></div>
<div class="grid-row" data-testid="grid"><b>Subject</b></div>
</body></html>"""


class GcnTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import gcn
        self.gcn = gcn

    def _resp(self, text, status=200):
        r = mock.Mock()
        r.status_code = status
        r.text = text

        def _raise():
            if status >= 400:
                raise requests.HTTPError("{}".format(status))
        r.raise_for_status = _raise
        return r

    def test_date_window_is_utc_and_inclusive(self):
        """days=1 は当日だけ。範囲は UTC の日付で出す。"""
        today = dt.date(2026, 9, 19)
        self.assertEqual(self.gcn._date_window(1, today), ("2026-09-19", "2026-09-19"))
        self.assertEqual(self.gcn._date_window(7, today), ("2026-09-13", "2026-09-19"))

    def test_html_list_parsing_and_dedupe(self):
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(LIST_HTML)):
            items, total, err = self.gcn._list_html(7, None)
        self.assertIsNone(err)
        self.assertEqual([i["circular_id"] for i in items], ["45640", "45639"])
        self.assertIn("Fermi GBM", items[1]["subject"])

    def test_empty_result_page_is_not_a_parse_failure(self):
        """ヒット0件のページ（フォームだけ）は「空の結果」であって解析失敗ではない。"""
        page = '<html><form><input name="startDate" value="2026-09-13"></form><ol></ol></html>'
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(page)):
            items, total, err = self.gcn._list_html(7, "sentinel")
        self.assertEqual((items, total, err), ([], 0, None))
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp("<html>broken</html>")):
            items, total, err = self.gcn._list_html(7, None)
        self.assertIsNone(items)
        self.assertEqual(err, "html parse failed")

    def test_json_list_parsing_and_bad_shape(self):
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(LIST_JSON)):
            items, total, err = self.gcn._list_json(7, None)
        self.assertEqual((len(items), total, err), (2, 84, None))
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp('{"foo":1}')):
            items, total, err = self.gcn._list_json(7, None)
        self.assertIsNone(items)
        self.assertEqual(err, "unexpected json shape")

    def test_list_prefers_html_then_falls_back_to_json(self):
        """HTML が既定、失敗時のみ内部 JSON ルートを試す（403 対策）。"""
        with mock.patch.object(self.gcn, "_list_html", return_value=(None, 0, "html fail")), \
                mock.patch.object(self.gcn, "_list_json",
                                  return_value=([{"circular_id": "1", "subject": "x"}], 1, None)):
            res = self.gcn.gcn_alerts(days=3, limit=5)
        self.assertEqual(res.structuredContent["list_path"], "json")
        self.assertEqual(res.structuredContent["shown"], 1)
        self.assertTrue(res.structuredContent["results"][0]["url"].endswith("/circulars/1"))

    def test_html_page_cap_is_not_reported_as_total(self):
        """HTML 経路は1ページ100件。総件数と断定しない。"""
        with mock.patch.object(self.gcn, "_list_html",
                               return_value=([{"circular_id": str(i), "subject": "s"} for i in range(120)],
                                             100, None)):
            res = self.gcn.gcn_alerts(days=60, limit=5)
        self.assertIn("上限100件", res.content[0].text)
        self.assertNotIn("全 100 件中", res.content[0].text)

    def test_detail_email_submission_body(self):
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(DETAIL_EMAIL)):
            rec, err = self.gcn._detail("45640")
        self.assertIsNone(err)
        self.assertIn("Lipunov", rec["submitter"])
        self.assertIn("2026-09-18 04:11:39 UT", rec["body"])
        self.assertEqual(rec["subject"], "EP Alert 260918.62: Global MASTER-Net observations report")

    def test_detail_web_form_submission_body(self):
        """Web フォーム投稿は usa-paragraph 側から本文を拾う。"""
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(DETAIL_FORM)):
            rec, err = self.gcn._detail("45639")
        self.assertIsNone(err)
        self.assertIn("sub-threshold burst", rec["body"])
        self.assertIn("Angus Jameson", rec["submitter"])

    def test_detail_404_and_parse_failure(self):
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp("", status=404)):
            rec, err = self.gcn._detail("99999999")
        self.assertEqual(err, "not_found")
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp("<html>no body</html>")):
            rec, err = self.gcn._detail("1")
        self.assertIn("解析できません", err)

    def test_tool_detail_mode_and_invalid_id(self):
        with mock.patch.object(self.gcn.requests, "get", return_value=self._resp(DETAIL_EMAIL)):
            res = self.gcn.gcn_alerts(circular_id="45640")
        self.assertIn("MASTER", res.content[0].text)
        self.assertEqual(res.structuredContent["circular"]["circular_id"], "45640")
        bad = self.gcn.gcn_alerts(circular_id="abc")
        self.assertEqual(bad.structuredContent["error"], "invalid circular_id")
        nf = self.gcn.gcn_alerts(circular_id="99999999")
        self.assertEqual(nf.structuredContent["error"], "not_found")
        self.assertIn("存在しません", nf.content[0].text)

    def test_both_paths_failing_is_error_result(self):
        with mock.patch.object(self.gcn, "_list_html", return_value=(None, 0, "html fail")), \
                mock.patch.object(self.gcn, "_list_json", return_value=(None, 0, "403")):
            res = self.gcn.gcn_alerts(days=7)
        self.assertEqual(res.structuredContent["error"], "403")
        self.assertIn("取得できませんでした", res.content[0].text)

    def test_bad_numeric_args_do_not_raise(self):
        """ゲートの fuzz（不正値注入）で例外を漏らさない。"""
        with mock.patch.object(self.gcn, "_list_html",
                               return_value=([{"circular_id": "1", "subject": "x"}], 1, None)):
            res = self.gcn.gcn_alerts(days="?", query=None, limit="100件")
        self.assertIsInstance(res.structuredContent, dict)
        self.assertEqual(res.structuredContent["days"], 7)
        self.assertEqual(res.structuredContent["shown"], 1)

    def test_network_failure_becomes_error_result(self):
        def boom(*a, **k):
            raise requests.ConnectionError("blocked")
        with mock.patch.object(self.gcn.requests, "get", boom):
            res = self.gcn.gcn_alerts(days=7)
        self.assertIn("error", res.structuredContent)
        self.assertIn("接続に失敗", res.content[0].text)


if __name__ == "__main__":
    unittest.main()

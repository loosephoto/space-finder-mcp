"""JAXA 施設公開（カレンダーの追加ソース）の回帰テスト。

実測した HTML の癖の再発防止:
- 施設名ラベルだけが付いた行だけを採る（「お知らせ」「休館案内」を日付セルに描かない）
- 1件に複数日（現地／オンライン）や期間が並ぶ → 開始日＋終了日に寄せる
- ISAS の相対URLを絶対化する・講演会や配信は対象外（「公開」を含む行だけ）
- 両ソースに載る特別公開を2件にしない（キーとタイトルで寄せる）
- 取得失敗時は complete=False（＝当日中は再取得を許す）
"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, "src")

FANFUN = """
<ul class="txt-list--date">
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="03">沖縄</span></div>
    <div class="mete-set__item"><time datetime="">2026年10月3日（土）12:00～17:30</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://track.sfo.jaxa.jp/info/2026/unnaFes.html" target="_blank">2026年度 沖縄宇宙通信所 施設紹介 inうんなまつり</a></div></div>
</li>
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="03">相模原</span></div>
    <div class="mete-set__item"><time datetime="">【特別公開】2026年11月7日（土）10:00~16:00【オンライン】2026年11月8日（日）10:00~16:00（予定）</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://www.isas.jaxa.jp/outreach/events/004226.html" target="_blank">JAXA相模原キャンパス 特別公開 2026</a></div></div>
</li>
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="03">筑波</span></div>
    <div class="mete-set__item"><time datetime="">2026年7月17日（金）~ 2026年9月30日（水）</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://fanfun.jaxa.jp/event/detail/1.html">筑波宇宙センター 特別公開 2026（夏の公開期間）</a></div></div>
</li>
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="03">地球観測</span></div>
    <div class="mete-set__item"><time datetime="">2026年10月17日（土）10:00～16:00（最終入場15:30まで）</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://www.satnavi.jaxa.jp/ja/news/2026/08/06/12665/index.html">地球観測センター施設一般公開のお知らせ</a></div></div>
</li>
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="01">お知らせ</span></div>
    <div class="mete-set__item"><span data-icn-color="03">種子島</span></div>
    <div class="mete-set__item"><time datetime="">2026年9月9日（水）</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://fanfun.jaxa.jp/information/detail/23416.html">H3ロケット10号機打上げに伴うセンター施設公開中止のお知らせ</a></div></div>
</li>
<li>
  <div class="txt-list--date__meta-set"><div>
    <div class="mete-set__item"><span data-icn-color="06">休館案内</span></div>
    <div class="mete-set__item"><span data-icn-color="03">勝浦</span></div>
    <div class="mete-set__item"><time datetime="">2026年9月10日（木）</time></div>
  </div></div>
  <div class="txt-list--date__link"><div><a href="https://fanfun.jaxa.jp/information/detail/23421.html">勝浦宇宙通信所 設備点検作業に伴う臨時休館のお知らせ</a></div></div>
</li>
</ul>
"""

ISAS = """
<table class="events-table">
<tr><th class="events-table__hd">11月</th><td class="events-table__body">
<div class="events-table__blc"><time>2026年11月7日 現地開催／11月8日 オンライン開催</time>
<p><a href="/outreach/events/004226.html">JAXA相模原キャンパス特別公開 2026</a></p></div>
</td></tr>
<tr><th class="events-table__hd">10月</th><td class="events-table__body">
<div class="events-table__blc"><time>2026年10月23日 18時30分〜 配信</time>
<p><a href="https://edu.jaxa.jp/activities/academy/2026/202602.html" target="_blank">JAXAアカデミー 「月探査の未来へ」 （宇宙教育センター）</a></p></div>
</td></tr>
<tr><th class="events-table__hd">10月</th><td class="events-table__body">
<div class="events-table__blc"><time>2025年10月11日 現地開催／10月12日 オンライン開催</time>
<p><a href="/outreach/events/003984.html">JAXA相模原キャンパス特別公開 2025</a></p></div>
</td></tr>
</table>
"""


def _resp(text):
    r = mock.Mock()
    r.text = text
    r.encoding = "utf-8"
    r.raise_for_status = lambda: None
    return r


class JaxaPublicEventsTests(unittest.TestCase):
    def setUp(self):
        from space_finder_mcp import calendar_store as store
        from space_finder_mcp import space_calendar as sc
        self.store, self.sc = store, sc
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

    def _fake_get(self, url, **kw):
        return _resp(FANFUN if "fanfun" in url else ISAS)

    def test_fanfun_keeps_only_facility_openings(self):
        """施設名ラベルだけの行が公開イベント。「お知らせ」「休館案内」は採らない。"""
        with mock.patch.object(self.sc.requests, "get", self._fake_get):
            recs = self.sc._jaxa_fanfun_visit()
        titles = [r["title"] for r in recs]
        self.assertEqual(len(recs), 4)
        self.assertTrue(all(r["kind"] == "public" for r in recs))
        self.assertNotIn("H3ロケット10号機打上げに伴うセンター施設公開中止", titles)
        self.assertFalse(any("休館" in t for t in titles))
        self.assertEqual(set(r["venue"] for r in recs), {"沖縄", "地球観測", "相模原", "筑波"})

    def test_fanfun_multiday_and_period(self):
        """現地／オンラインの2日は開始〜終了、期間は end_local に入れる。"""
        with mock.patch.object(self.sc.requests, "get", self._fake_get):
            recs = {r["title"]: r for r in self.sc._jaxa_fanfun_visit()}
        sp = recs["JAXA相模原キャンパス 特別公開 2026"]
        self.assertEqual(sp["start_local"][:10], "2026-11-07")
        self.assertEqual(sp["end_local"], "2026-11-08")
        self.assertEqual(sp["certainty"], "unverified")      # 「（予定）」付き
        self.assertEqual(sp["venue"], "相模原")
        free = recs["筑波宇宙センター 特別公開 2026（夏の公開期間）"]
        self.assertEqual((free["start_local"][:10], free["end_local"]), ("2026-07-17", "2026-09-30"))
        self.assertEqual(free["certainty"], "confirmed")

    def test_trailing_oshirase_is_stripped(self):
        """「…のお知らせ」は日付セルの見やすさのため落とす。"""
        with mock.patch.object(self.sc.requests, "get", self._fake_get):
            recs = self.sc._jaxa_fanfun_visit()
        self.assertNotIn("のお知らせ", " ".join(r["title"] for r in recs))

    def test_isas_absolute_url_and_public_filter(self):
        """ISAS は「公開」を含む行だけ・相対URLは絶対化する。"""
        with mock.patch.object(self.sc.requests, "get", self._fake_get):
            recs = self.sc._jaxa_isas_events()
        self.assertEqual(len(recs), 2)                       # 相模原2026・相模原2025（講演会は除外）
        self.assertTrue(all("公開" in r["title"] for r in recs))
        self.assertTrue(all(r["url"].startswith("https://www.isas.jaxa.jp/outreach/events/")
                            for r in recs))
        self.assertEqual(recs[0]["start_local"][:10], "2026-11-07")
        self.assertIsNone(recs[0]["end_local"])              # 年が無い「11月8日」は範囲にしない

    def test_duplicate_special_opening_is_one_record(self):
        """両ソースに載る相模原の特別公開は1件（キーとタイトルの双方で寄せる）。"""
        n = 0

        def counted(url, **kw):
            nonlocal n
            n += 1
            return self._fake_get(url)

        with mock.patch.object(self.sc.requests, "get", counted):
            self.sc._jaxa_public_events()
            again = self.sc._jaxa_public_events()             # 同じ取得日なら取り直さない
        recs = [r for r in self.store.records(kinds={"public"}) if r["key"].startswith("jaxa:")]
        keys = [r["key"] for r in recs]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sum(1 for k in keys if k.endswith("004226.html")), 1)
        self.assertEqual(len(recs), 5)                        # fanfun 4件 + ISAS 2025（相模原2026は重複で寄せる）
        self.assertEqual(n, 2)                                # 2回目はキャッシュ済み（API 0回）
        self.assertIsNone(again)

    def test_failure_marks_source_incomplete(self):
        """片方の取得失敗は complete=False（当日中に再取得できる）＋警告文になる。"""
        def boom(url, **kw):
            if "fanfun" in url:
                raise requests.RequestException("blocked")
            return _resp(ISAS)

        with mock.patch.object(self.sc.requests, "get", boom):
            warn = self.sc._jaxa_public_events()
        self.assertIn("ファン!ファン!JAXA!", warn or "")
        meta = self.store.source_meta("jaxa_public:v1")
        self.assertFalse(meta["complete"])
        self.assertFalse(self.store.source_is_fresh(
            "jaxa_public:v1", self.store.TTL_PUBLIC, window=dt.date.today().isoformat()))
        recs = [r for r in self.store.records(kinds={"public"}) if r["key"].startswith("jaxa:")]
        self.assertTrue(recs)                                 # 取れた方は蓄積する

    def test_records_land_in_the_month_grid(self):
        """蓄積した JAXA 分が暦（2026年10月）の日付セルに載る。"""
        with mock.patch.object(self.sc.requests, "get", self._fake_get):
            self.sc._jaxa_public_events()
        drawn, _tray = self.sc._assemble(2026, 10, 9.0, {"public"}, {})
        titles = [e["title"] for e in drawn]
        self.assertIn("地球観測センター施設一般公開", titles)

    def test_missing_date_is_skipped(self):
        """日付が無い行（月だけの告知）は落とす（架空の予定を描かない）。"""
        html = ('<li><div><span data-icn-color="03">筑波</span>'
                '<time datetime="">近日公開</time>'
                '<a href="https://fanfun.jaxa.jp/x">筑波宇宙センター 特別公開</a></div></li>')
        with mock.patch.object(self.sc.requests, "get", lambda url, **kw: _resp(html)):
            self.assertEqual(self.sc._jaxa_fanfun_visit(), [])


if __name__ == "__main__":
    unittest.main()

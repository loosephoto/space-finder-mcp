"""NASA GCN（General Coordinates Network）の過渡天体速報（GCN Circulars）を返す（認証不要）。

GCN は GRB・重力波・ニュートリノ・X線新星などの速報を世界の観測所から集めて配る
NASA GSFC のサービス。当ツールはその **Circulars（人が書く速報）** を期間・キーワードで
一覧し、必要なら1件の本文（投稿者・観測時刻・本文）まで取る。

データ経路（実測 2026-09）:
- 一覧: `https://gcn.nasa.gov/circulars` に `startDate`/`endDate`/`query` を付け、
  サイト内部のデータルート `?_data=routes%2Fcirculars._archive._index` で **JSON** を取る
  （`{"items":[{"circularId":…,"subject":…}],"totalItems":…}`）。これは公開 API ではなく
  サイト内部のルートなので、失敗・形の変化時は **同じ URL の HTML**
  （`<li value="45640"><a href="/circulars/45640…">題名</a></li>`）へフォールバックする。
- 本文: `https://gcn.nasa.gov/circulars/<id>` の HTML から 投稿者（From）・日時・本文
  （`<pre><code>…`）を抽出する。個別の JSON ルートには本文が無い（実測）。
- 旧 `gcn.gsfc.nasa.gov` は 2023-04-17 に廃止されアーカイブは凍結（履歴専用）。
- 機械可読の **Notices** は Kafka（`kafka.gcn.nasa.gov:9092`・`gcn.notices.*`）のみで、
  依存が増えるため本ツールの対象外（Circulars は人間が書く速報で、Notice ではない）。
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_FORECAST, ttl_cache, is_error_result
from .input_utils import as_int

ARCHIVE = "https://gcn.nasa.gov/circulars"
CIRCULAR = "https://gcn.nasa.gov/circulars/{}"
DATA_ROUTE = "routes%2Fcirculars._archive._index"
UA = {"User-Agent": "space-finder-mcp/0.33 (MCP; GCN circulars)"}
# サイト内部のデータルート（?_data=…）はブラウザ風 UA 以外を 403 で弾く（実測: 独自 UA は 403、
# "Mozilla/5.0" は 200）。HTML 経路は独自 UA でも通るので、JSON 経路だけこちらを使う。
UA_BROWSER = {"User-Agent": "Mozilla/5.0 (compatible; space-finder-mcp/0.33)"}
TIMEOUT = (10, 30)
MAX_DAYS = 60


def _strip(s: str) -> str:
    """HTML 断片をテキストへ（タグ除去＋実体参照の復元＋空白の畳み込み）。"""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def _get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> tuple:
    """GET する。戻り: (本文, エラー文字列 or None)。例外は外へ出さない。"""
    try:
        r = requests.get(url, params=params, headers=headers or UA, timeout=TIMEOUT)
        if r.status_code == 404:
            return None, "not_found"
        r.raise_for_status()
    except requests.RequestException as ex:
        return None, "GCN への接続に失敗しました: {}".format(str(ex)[:120])
    return r.text, None


def _date_window(days: int, today: Optional[dt.date] = None) -> tuple:
    """(startDate, endDate) を UTC の日付で返す（endDate は当日＝その日を含む）。"""
    d = today or dt.datetime.now(dt.timezone.utc).date()
    return (d - dt.timedelta(days=days - 1)).isoformat(), d.isoformat()


_LI_RE = re.compile(r'<li value="(\d+)">\s*<a[^>]*href="(/circulars/\d+[^"]*)"[^>]*>(.*?)</a>', re.S)
_FROM_RE = re.compile(r"<b>From</b></div><div[^>]*>(.*?)</div>", re.S)
_TITLE_RE = re.compile(r'<span title="([^"]+)"')
_BODY_RE = re.compile(r"<pre><code>(.*?)</code></pre>", re.S)
_TITLE_TAG_RE = re.compile(r"<title>GCN - Circulars - \d+ - (.*?)</title>", re.S)
# 本文の入れ物は投稿経路で変わる（実測: メール投稿=<pre><code>、Web フォーム=<p class="usa-paragraph">）。
# どちらでも拾えるよう「Via 行の直後〜次の grid-row」を本文ブロックとして切り出す。
_BODY_BLOCK_RE = re.compile(r'<b>Via</b>.*?</div></div>(.*?)(?=<div class="grid-row"|</main>|<footer|</body>|$)', re.S)


def _list_json(days: int, query: Optional[str], today=None) -> tuple:
    """一覧を JSON（サイト内部のデータルート）で取る。戻り: (items, total, err)。"""
    start, end = _date_window(days, today)
    params = {"startDate": start, "endDate": end, "page": 1, "_data": DATA_ROUTE}
    if query:
        params["query"] = query
    text, err = _get(ARCHIVE, params, headers=UA_BROWSER)
    if err:
        return None, 0, err
    try:
        data = json.loads(text)
    except ValueError:
        return None, 0, "json parse failed"
    items = data.get("items")
    if not isinstance(items, list):
        return None, 0, "unexpected json shape"
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("circularId"):
            out.append({"circular_id": str(it["circularId"]), "subject": _strip(it.get("subject") or "")})
    return out, int(data.get("totalItems") or len(out)), None


def _list_html(days: int, query: Optional[str], today=None) -> tuple:
    """JSON ルートが使えないときのフォールバック（同じ URL の HTML を解析）。"""
    start, end = _date_window(days, today)
    params = {"startDate": start, "endDate": end}
    if query:
        params["query"] = query
    text, err = _get(ARCHIVE, params)
    if err:
        return None, 0, err
    out, seen = [], set()
    for cid, href, title in _LI_RE.findall(text):
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"circular_id": cid, "subject": _strip(title)})
    if not out:
        # ヒット0件のページは <li value="…"> が無く、検索フォームと見出しだけが描かれる（実測）。
        # フォームがあれば「空の結果」であって解析失敗ではない（解析失敗と混同すると JSON へ無駄に落ちる）。
        if 'name="startDate"' in text or "<ol" in text:
            return [], 0, None
        return None, 0, "html parse failed"
    return out, len(out), None


def _detail(circular_id: str) -> tuple:
    """1件の Circular の本文を取る。戻り: (dict, err)。"""
    text, err = _get(CIRCULAR.format(circular_id))
    if err:
        return None, err
    m = _TITLE_RE.search(text)
    frm = _FROM_RE.search(text)
    block = _BODY_BLOCK_RE.search(text)
    body = _BODY_RE.search(block.group(1)) if block else None
    body_text = _strip(block.group(1)) if block else ""
    if not (frm or body):
        return None, "ページ構造を解析できませんでした（サイト構造が変わった可能性）"
    rec = {
        "circular_id": str(circular_id),
        "subject": _strip(_TITLE_TAG_RE.search(text).group(1)) if _TITLE_TAG_RE.search(text) else "",
        "submitted_utc": (m.group(1) if m else ""),
        "submitter": _strip(frm.group(1)) if frm else "",
        "body": _strip(body.group(1)) if body else body_text,
        "url": CIRCULAR.format(circular_id),
    }
    return rec, None


@ttl_cache(TTL_FORECAST, maxsize=32, skip_if=is_error_result)
def gcn_alerts(days: int = 7, query: Optional[str] = None, circular_id: Optional[str] = None,
               limit: int = 25) -> CallToolResult:
    """NASA GCN（General Coordinates Network）の過渡天体速報（GCN Circulars）を返す（認証不要）。

    GCN は GRB・重力波・ニュートリノ・X線新星などの速報を世界の観測所から集めて配る
    NASA GSFC のサービスです。本ツールは **Circulars（人が書く速報）** を期間・キーワードで
    新しい順に一覧し、`circular_id` を指定すればその1件の本文（投稿者・観測時刻・本文）まで返します。

    例:「直近1週間のGRB速報」「今日のGCN速報」「重力波の続報」「EP（アインシュタイン探査機）のアラート」

    Args:
        days: 何日前までを対象にするか（1〜60、既定 7）。UTC 日付で絞り込みます。
        query: キーワード（例 "GRB", "neutrino", "GW", "SVOM", "EP"）。省略時は全件。
        circular_id: Circular 番号（例 "45640"）。指定すると1件の本文を返します（一覧は返しません）。
        limit: 一覧で返す最大件数（既定 25、最大 100）。

    注意: ここで返すのは人が書く Circular です。機械可読の **Notices** は Kafka
    （kafka.gcn.nasa.gov:9092・`gcn.notices.*`）配信のみで、本ツールの対象外です。
    出典: NASA GCN (gcn.nasa.gov)。回答時は Circular のリンクをそのまま提示してください。
    """
    limit = as_int(limit, 25, 1, 100) or 25
    days = as_int(days, 7, 1, MAX_DAYS) or 7
    q = " ".join(str(query).split()) if query else ""

    if circular_id is not None:
        cid = " ".join(str(circular_id).split())
        if not cid.isdigit():
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "circular_id は Circular 番号（数字）で指定してください（例 45640）。"
                    "一覧は circular_id を省略して days / query で取得できます。"))],
                structuredContent={"error": "invalid circular_id", "circular_id": str(circular_id)})
        rec, err = _detail(cid)
        if err == "not_found":
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "GCN Circular {} は存在しません（番号を確認してください）。"
                    "一覧は circular_id を省略して days / query で取得できます。".format(cid)))],
                structuredContent={"error": "not_found", "circular_id": cid, "source": "NASA GCN"})
        if err:
            return CallToolResult(
                content=[TextContent(type="text", text="GCN Circular {} を取得できませんでした: {}".format(cid, err))],
                structuredContent={"error": err, "circular_id": cid, "source": "NASA GCN"})
        body = rec["body"] or "(本文が空でした)"
        text = "\n".join([
            "📄 [GCN Circular {} を開く]({})".format(rec["circular_id"], rec["url"]),
            "🛰 **GCN Circular {}**: {}".format(rec["circular_id"], rec["subject"] or "(題名不明)"),
            "投稿: {}{}".format(rec["submitter"] or "?", "（{}）".format(rec["submitted_utc"]) if rec["submitted_utc"] else ""),
            "",
            body,
            "",
            "出典: NASA GCN（gcn.nasa.gov）／ Circulars は観測者コミュニティが書く速報です。",
        ])
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent={"circular": rec, "source": "NASA GCN", "url": rec["url"]})

    # 既定は HTML（公開アーカイブのページ。サーバー描画なので安定）。サイト内部の JSON ルートは
    # 403 を返すことがある（実測 2026-09: 一時的に全 UA で 403）ため、HTML が失敗したときだけ試す。
    items, total, err = _list_html(days, q)
    path = "html"
    if err:
        items, total, err = _list_json(days, q)
        path = "json"
    if err:
        return CallToolResult(
            content=[TextContent(type="text", text="GCN Circulars を取得できませんでした: {}".format(err))],
            structuredContent={"error": err, "source": "NASA GCN",
                               "note": "一覧ページ（HTML）と内部データルート（JSON）の両方に失敗しました（サイト構造の変更・接続不可・一時ブロック）"})
    shown = items[:limit]
    start, end = _date_window(days)
    if path == "html" and total >= 100:   # HTML は1ページ100件まで。総件数は断定できない
        count_note = "先頭 {} 件（一覧ページの上限100件／それ以前は期間を狭めてください）".format(len(shown))
    else:
        count_note = "全 {} 件中 {} 件".format(total, len(shown))
    head = "🔭 **NASA GCN 過渡天体速報（GCN Circulars）** 直近{}日（{}〜{}）{}: {}".format(
        days, start, end, "／検索: {}".format(q) if q else "", count_note)
    lines = [head]
    for it in shown:
        lines.append("- <{}> [{}]({})".format(it["circular_id"], it["subject"] or "(題名不明)",
                                               CIRCULAR.format(it["circular_id"])))
    if not shown:
        lines.append("該当する Circular はありませんでした（期間・キーワードを広げてください）。")
    lines += [
        "",
        "ℹ️ これは観測者コミュニティが書く **Circular**（速報・続報・訂正）です。一覧ページの上限は100件/ページです。"
        "機械可読の Notices（重力波・ニュートリノ等）は Kafka 配信で、本ツールの対象外。",
        "出典: NASA GCN（gcn.nasa.gov）／ 1件の本文は circular_id 指定で取得できます。",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "days": days, "start_date": start, "end_date": end, "query": q or None,
            "total": total, "shown": len(shown), "list_path": path,
            "results": [dict(it, url=CIRCULAR.format(it["circular_id"])) for it in shown],
            "source": "NASA GCN",
        })

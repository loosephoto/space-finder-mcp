"""Roscosmos（ロシア連邦宇宙機関）オープンデータ STAC（認証不要）。

Госкорпорация «Роскосмос» が公開する**静的 STAC 1.0.0 カタログ**（200TB 超）から、
気象衛星の全球画像を検索する。収録は Elektro-L（静止・全球ディスク）・Arktika-M
（高楕円軌道）・Meteor-M（極軌道モザイク）と、そこから作られた全球モザイク
（EL_ARCM / MM）。出典: s3.gptl.ru/stac-web-free/catalog.json

⚠️ このカタログは `/search` も `/collections` も持たない（実測 404）。代わりに
`catalog.json → コレクション → 年 → 月 → 日` の木を辿り、日ノードの `rel=item`
リンクからアイテムを列挙する（日ノードのパスは親を辿らず直接 GET できることを実測で確認）。
ホストは s3.gptl.ru と s3ext.gptl.ru の2つが同じ内容を返すため、到達できない側は
記憶して他方へ切り替える（遮断時の fail fast）。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_HOURLY, ttl_cache, is_error_result
from .img_common import media_link_line
from .input_utils import as_int

BASES = ("https://s3.gptl.ru/stac-web-free", "https://s3ext.gptl.ru/stac-web-free")
SOURCE = "s3.gptl.ru/stac-web-free（Roscosmos オープンデータ STAC）"
UA = {"User-Agent": "space-finder-mcp/0.39 (MCP; Roscosmos open data STAC)"}

_LOCK = threading.Lock()
_STATE = {"base": BASES[0], "dead": set()}     # 到達できたホスト / 遮断を記憶したホスト


class _Unavailable(Exception):
    """カタログのどのホストにも到達できない（遮断・接続不可）。"""


def _fetch_json(path: str) -> Optional[dict]:
    """カタログ内の JSON を取得する。

    戻り: 取得できた JSON（辞書）。404 は None（その日・そのノードが無い）。
    どのホストにも到達できないときは `_Unavailable`。
    """
    with _LOCK:
        order = [b for b in ([_STATE["base"]] + list(BASES)) if b not in _STATE["dead"]]
    tried, last_err = [], None
    for base in order:
        if base in tried:
            continue
        tried.append(base)
        url = "{}/{}".format(base, str(path).lstrip("/"))
        try:
            r = requests.get(url, headers=UA, timeout=(10, 25))
        except requests.RequestException as e:      # 接続不可・タイムアウトは遮断として記憶
            last_err = e
            with _LOCK:
                _STATE["dead"].add(base)
            continue
        if r.status_code == 404:
            with _LOCK:
                _STATE["base"] = base
            return None
        try:
            r.raise_for_status()
            doc = r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            continue
        with _LOCK:
            _STATE["base"] = base
        return doc
    raise _Unavailable(str(last_err) if last_err else "到達できるホストがありません")


def _strip_base(href: str) -> str:
    """リンクの絶対URLからホスト部分を落としてカタログ内の相対パスにする。"""
    for base in BASES:
        if href.startswith(base + "/"):
            return href[len(base) + 1:]
    return str(href).lstrip("/")


def _child_links(doc: dict) -> list:
    """`rel=child` の (タイトル, 相対パス) を返す。"""
    out = []
    for l in (doc or {}).get("links", []) or []:
        if l.get("rel") != "child" or not l.get("href"):
            continue
        out.append((l.get("title") or "", _strip_base(l["href"])))
    return out


def _item_links(doc: dict) -> list:
    """`rel=item` の相対パスを返す。"""
    return [_strip_base(l["href"]) for l in (doc or {}).get("links", []) or []
            if l.get("rel") == "item" and l.get("href")]


def _path_of(doc: dict, path: str) -> str:
    """コレクションの表示用パス（`…/collection.json` を落とす）。"""
    if path.endswith("/collection.json"):
        return path[: -len("/collection.json")]
    return path


def _summaries(doc: dict) -> dict:
    """コレクションの summaries から代表値を取り出す。"""
    s = doc.get("summaries") or {}
    out = {}
    for key, name in (("platform", "platforms"), ("instruments", "instruments"),
                      ("processing:level", "processing_levels"), ("gsd", "gsd")):
        v = s.get(key)
        if v:
            out[name] = v
    return out


def _temporal(doc: dict) -> Optional[list]:
    ext = ((doc.get("extent") or {}).get("temporal") or {}).get("interval") or []
    return ext[0] if ext else None


def _asset_pick(item: dict) -> tuple:
    """アイテムの assets から (クイックルックURL, {EPSG: {href, size_mb}}) を選ぶ。"""
    quick, data = None, {}
    for key, v in (item.get("assets") or {}).items():
        if not isinstance(v, dict) or not v.get("href"):
            continue
        roles = v.get("roles") or []
        mime = v.get("type") or ""
        href = v["href"]
        if quick is None and ("thumbnail" in roles or mime.startswith("image/png")):
            quick = href
            continue
        if mime.startswith("image/tiff") or "image.tif" in str(v.get("title", "")) or href.lower().endswith(".tif"):
            size = v.get("file:size")
            mb = round(float(size) / 1048576.0, 1) if isinstance(size, (int, float)) else None
            epsg = v.get("proj:epsg")
            if epsg is None:
                # アセットのキーは `image.tif.ms.3857._` 形式で EPSG を含む（実測）
                m = re.search(r"ms\.(\d{4,5})\.", str(key))
                epsg = int(m.group(1)) if m else None
            data[str(epsg) if epsg is not None else "?"] = {"href": href, "size_mb": mb}
    return quick, data


@ttl_cache(TTL_DAILY, maxsize=16, skip_if=is_error_result)
def roscosmos_collections(level: int = 2) -> CallToolResult:
    """Roscosmos オープンデータ STAC のコレクション一覧を返す（認証不要）。

    例:「ロシアの気象衛星の公開データ」「Elektro-L の全球画像コレクション」
    content に表示用サマリ、structuredContent に JSON（パス/説明/収録期間/観測機器）を返す。
    このカタログは /collections を持たないため、カタログ木を辿って列挙します。

    Args:
        level: 辿る深さ（1=最上位のみ、2=観測機器まで〈既定〉、3=処理レベルまで。最大 3）。
    """
    level = as_int(level, 2, 1, 3)
    try:
        root = _fetch_json("catalog.json")
        if not isinstance(root, dict):
            raise _Unavailable("catalog.json を取得できません")
        frontier = _child_links(root)
        records, depth = [], 0
        while frontier and depth < level:
            depth += 1
            nxt = []
            for _title, path in frontier:
                doc = _fetch_json(path)
                if not isinstance(doc, dict):
                    continue
                full = _path_of(doc, path)
                records.append({
                    "path": full,
                    "id": doc.get("id"),
                    "title": doc.get("title") or _title,
                    "description": (doc.get("description") or "").strip().replace("\n", " ")[:200],
                    "depth": depth,
                    "temporal": _temporal(doc),
                    **_summaries(doc),
                })
                if depth < level:
                    nxt.extend(_child_links(doc))
            frontier = nxt
    except (_Unavailable, ValueError) as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Roscosmos オープンデータ STAC への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": SOURCE},
        )
    if not records:
        return CallToolResult(
            content=[TextContent(type="text", text="Roscosmos オープンデータ STAC のコレクションを取得できませんでした。")],
            structuredContent={"error": "no collections", "source": SOURCE, "results": []},
        )
    lines = [f"Roscosmos オープンデータ STAC のコレクション（深さ {level}・{len(records)} 件）:"]
    for i, c in enumerate(records, 1):
        lines.append("{}{}. **{}**".format("　" * (c["depth"] - 1), i, c["path"]))
        if c.get("title") and c["title"] != c["path"]:
            lines.append(f"   {c['title']}")
        bits = []
        if c.get("platforms"):
            bits.append("衛星 " + "/".join(str(x) for x in c["platforms"]))
        if c.get("instruments"):
            bits.append("機器 " + "/".join(str(x) for x in c["instruments"]))
        if c.get("gsd"):
            bits.append("解像度 " + "/".join(str(x) for x in c["gsd"]) + " m")
        if c.get("processing_levels"):
            bits.append("レベル " + "/".join(str(x) for x in c["processing_levels"]))
        if c.get("temporal"):
            bits.append("収録 {} 〜 {}".format(str(c["temporal"][0])[:10], str(c["temporal"][1])[:10]))
        if bits:
            lines.append("   " + " ／ ".join(bits))
    lines.append("出典: " + SOURCE + "（Госкорпорация «Роскосмос» / НЦ ОМЗ / ЕТРИС ДЗЗ）")
    lines.append("※ 検索は roscosmos_search ツール（コレクションのパスと日付を指定）で行います。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"level": level, "shown": len(records), "results": records, "source": SOURCE},
    )


def _parse_date(value) -> tuple:
    """日付引数を date にする。戻り: (date, エラーメッセージ)。"""
    text = str(value or "").strip()
    if not text:
        return None, None
    head = text[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date(), None
    except ValueError:
        return None, ("日付は 'YYYY-MM-DD'（UTC）で指定してください"
                      "（例: 2026-10-11。指定された値: {}）。".format(text[:40]))


@ttl_cache(TTL_HOURLY, maxsize=64, skip_if=is_error_result)
def roscosmos_search(collection: str = "Electro-L/MSU-GS/L2_RGB_VIS_IR",
                     date: Optional[str] = None, limit: int = 3,
                     lookback: int = 7) -> CallToolResult:
    """Roscosmos の気象衛星画像（Elektro-L・Arktika-M・Meteor-M）を検索する（認証不要）。

    例:「ロシアの静止気象衛星エレクトロLの全球画像」「今日のMeteor-Mモザイク」
    content に表示用サマリとクイックルック/データのリンク、structuredContent に JSON
    （id/日時/衛星/機器/処理レベル/解像度/bbox/クイックルックURL/GeoTIFF URL）を返す。

    ⚠️ このカタログは検索APIを持たない静的 STAC のため、`collection` のパスと
    日付（UTC）から木を辿って列挙します。日付を省略すると、今日から最大 `lookback` 日
    さかのぼって**データがある最新日**を使います。

    Args:
        collection: コレクションのパス（例 "Electro-L/MSU-GS/L2_RGB_VIS_IR"、
            "MM/MSU-MR/L3MD"、"EL_ARCM/MSU-GS/L3BT9"、"Arctic-M/MSU-GS-A/L2_RGB_VIS_IR"）。
            roscosmos_collections で確認できます。未知のパスは候補を返して停止します。
        date: 日付（"YYYY-MM-DD"・UTC）。省略でデータがある最新日。
        limit: 返す件数（既定 3、最大 8）。
        lookback: 日付省略時にさかのぼる最大日数（既定 7、最大 30）。
    """
    limit = as_int(limit, 3, 1, 8)
    lookback = as_int(lookback, 7, 0, 30)
    path = str(collection or "").strip().strip("/")
    if path.endswith("/collection.json"):
        path = path[: -len("/collection.json")]
    want, date_err = _parse_date(date)
    if date_err:
        return CallToolResult(
            content=[TextContent(type="text", text=date_err)],
            structuredContent={"error": "bad date", "date": date},
        )
    if not path:
        return CallToolResult(
            content=[TextContent(type="text", text="collection にはコレクションのパスを指定してください"
                                                   "（例 \"Electro-L/MSU-GS/L2_RGB_VIS_IR\"）。")],
            structuredContent={"error": "no collection", "collection": collection},
        )
    try:
        col = _fetch_json(f"{path}/collection.json")
        if col is None:
            # 未知のパスは推測せず、既知のコレクションを候補として返して停止する
            cand = roscosmos_collections(level=2)
            paths = [r.get("path") for r in (cand.structuredContent or {}).get("results", [])]
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "コレクション '{}' はこのカタログにありません。候補（パス）:\n".format(path)
                    + "\n".join("  - " + str(p) for p in paths[:20])))],
                structuredContent={"error": "unknown collection", "collection": path,
                                   "candidates": paths, "source": SOURCE},
            )
        days = [want] if want else []
        if not days:
            today = datetime.now(timezone.utc).date()
            days = [today - timedelta(days=i) for i in range(lookback + 1)]
        day_doc, used = None, None
        for d in days:
            doc = _fetch_json("{}/{:04d}/{:02d}/{:02d}/collection.json".format(
                path, d.year, d.month, d.day))
            if isinstance(doc, dict) and _item_links(doc):
                day_doc, used = doc, d
                break
        if day_doc is None:
            span = "{}".format(days[-1]) if len(days) == 1 else "{} 〜 {}".format(days[-1], days[0])
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "コレクション '{}' に {}（UTC）のデータが見つかりませんでした。".format(path, span)))],
                structuredContent={"collection": path, "total": 0, "results": [],
                                   "searched_days": [str(d) for d in days], "source": SOURCE},
            )
        item_paths = _item_links(day_doc)[:limit]
        records, failed = [], 0
        for ip in item_paths:
            it = _fetch_json(ip)
            if not isinstance(it, dict):
                failed += 1
                continue
            props = it.get("properties") or {}
            quick, data = _asset_pick(it)
            records.append({
                "id": it.get("id"),
                "datetime": (props.get("datetime") or "")[:19],
                "title": props.get("title"),
                "platform": props.get("platform"),
                "instruments": props.get("instruments"),
                "processing_level": props.get("processing:level"),
                "gsd_m": props.get("gsd"),
                "bbox": it.get("bbox"),
                "quicklook_url": quick,
                "data": data,
                "source_url": "{}/{}".format(BASES[0], ip),
            })
    except (_Unavailable, ValueError) as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Roscosmos オープンデータ STAC の取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": SOURCE},
        )
    total_items = len(_item_links(day_doc))
    lines = ["Roscosmos {} の画像（{} UTC・全 {} 件中 {}{}）:".format(
        path, used, total_items, len(records), "・取得失敗 {} 件".format(failed) if failed else "")]
    for i, r in enumerate(records, 1):
        bits = []
        if r["platform"]:
            bits.append(str(r["platform"]))
        if r["instruments"]:
            bits.append("/".join(r["instruments"]) if isinstance(r["instruments"], list) else str(r["instruments"]))
        if r["processing_level"]:
            bits.append(str(r["processing_level"]))
        if r["gsd_m"]:
            bits.append("{} m".format(r["gsd_m"]))
        lines.append("{}. **{}**  {}".format(i, str(r["title"] or r["id"])[:56], r["datetime"] + " UTC"))
        if bits:
            lines.append("   " + " ／ ".join(bits))
        if r["bbox"]:
            lines.append("   領域 bbox: {}".format([round(float(x), 2) for x in r["bbox"][:4]]))
        if r["quicklook_url"]:
            lines.append(media_link_line("{} のクイックルック".format(str(r["title"] or r["id"])[:40]),
                                         url=r["quicklook_url"], kind="image"))
        for epsg, d in (r["data"] or {}).items():
            size = "（{} MB）".format(d["size_mb"]) if d.get("size_mb") else ""
            label = "GeoTIFF（EPSG:{}）{}".format(epsg, size) if epsg != "?" else "GeoTIFF{}".format(size)
            lines.append(media_link_line(label, url=d["href"], kind="file"))
    lines.append("出典: " + SOURCE + "（Госкорпорация «Роскосмос» / НЦ ОМЗ / ЕТРИС ДЗЗ）")
    lines.append("※ 日付は UTC の観測日です（静止衛星は 15〜30 分間隔、極軌道モザイクは日次の全球合成）。")
    if not want:
        lines.append("※ 日付を省略したため、データがある最新日（{} UTC）を使いました。".format(used))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": path, "date": str(used),
                           "total_items": total_items, "shown": len(records),
                           "failed_items": failed, "results": records, "source": SOURCE},
    )

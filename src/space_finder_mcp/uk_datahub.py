"""UK EO DataHub（英国地球観測データハブ）STAC API。

Sentinel・UKCP 気候モデル・EOCIS 等の英国の衛星・地球観測データを STAC で検索する。
出典: eodatahub.org.uk/api/catalogue/stac。公開カタログは認証不要。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .stac_common import parse_bbox

STAC = "https://eodatahub.org.uk/api/catalogue/stac"
UA = {"User-Agent": "space-finder-mcp/0.3 (MCP; UK EO DataHub STAC)"}


def uk_stac_search(collection: Optional[str] = None, query: Optional[str] = None,
                   bbox: Optional[str] = None, datetime: Optional[str] = None,
                   limit: int = 5) -> CallToolResult:
    """英国 EO DataHub の衛星・気候データをSTACで検索する（公開カタログは認証不要）。

    例:「英国の気候モデルデータ」「UKのSentinel画像」
    content に表示用サマリ、structuredContent に JSON（id/日時/bbox/コレクション）を返す。

    Args:
        collection: コレクションID（例 "sentinel2_ard", "ukcp"）。省略で全コレクション。
        query: キーワード検索（例 "climate", "sentinel"）。
        bbox: 空間範囲（"lon_min,lat_min,lon_max,lat_max"）。
        datetime: 時間範囲（"YYYY-MM-DD" または "start/end"）。省略で最新。
        limit: 返す件数（既定 5、最大 10）。
    """
    limit = max(1, min(int(limit), 10))
    body: dict = {"limit": limit}
    if collection:
        body["collections"] = [collection]
    bbox_vals, bbox_err = parse_bbox(bbox)
    if bbox_err:
        return CallToolResult(
            content=[TextContent(type="text", text=bbox_err)],
            structuredContent={"error": "bad bbox", "bbox": bbox},
        )
    if bbox_vals:
        body["bbox"] = bbox_vals
    if datetime:
        body["datetime"] = datetime
    if query:
        body["q"] = query
    try:
        r = requests.post(f"{STAC}/search", headers=UA, json=body, timeout=35)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"UK EO DataHub STAC 検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "eodatahub.org.uk/api/catalogue/stac"},
        )
    feats = d.get("features", [])
    if not feats:
        return CallToolResult(
            content=[TextContent(type="text", text="条件一致のデータがありませんでした。コレクションや期間を変更してください。")],
            structuredContent={"collection": collection, "query": query, "total": 0, "results": []},
        )
    records = []
    for it in feats:
        props = it.get("properties", {})
        rec = {
            "id": it.get("id", ""),
            "datetime": (props.get("datetime") or props.get("start_datetime") or "")[:19],
            "collection": it.get("collection"),
            "bbox": it.get("bbox"),
            "cloud_cover": props.get("eo:cloud_cover"),
            "assets": {k: v.get("href") for k, v in list((it.get("assets") or {}).items())[:4]},
        }
        records.append(rec)
    lines = [f"UK EO DataHub のデータ（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        cc = r["cloud_cover"]
        cc_s = f" 雲量 {cc}%" if cc is not None else ""
        lines.append(f"{i}. **{r['id'][:56]}**  {r['datetime']} UTC  [{r['collection']}]{cc_s}")
        if r["bbox"]:
            lines.append(f"   領域 bbox: {[round(x,2) for x in r['bbox']]}")
    lines.append("出典: eodatahub.org.uk/api/catalogue/stac ／ 商用データ(airbus/planet)は要アカウント。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": collection, "query": query, "shown": len(records), "results": records},
    )


def uk_stac_collections(limit: int = 20) -> CallToolResult:
    """英国 EO DataHub の利用可能なコレクション一覧を返す（認証不要）。

    例:「英国の衛星データコレクション」
    Args:
        limit: 返す件数（既定 20、最大 100）。
    """
    limit = max(1, min(int(limit), 100))
    try:
        r = requests.get(f"{STAC}/collections", headers=UA, timeout=30)
        r.raise_for_status()
        cols = r.json().get("collections", [])
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"UK EO DataHub への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "eodatahub.org.uk"},
        )
    total = len(cols)
    cols = cols[:limit]
    lines = [f"UK EO DataHub コレクション（全 {total} 件中、先頭 {len(cols)} 件）:"]
    for i, c in enumerate(cols, 1):
        lines.append(f"{i}. **{c.get('id','?')}** — {(c.get('description') or '')[:90]}")
    lines.append("出典: eodatahub.org.uk/api/catalogue/stac")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"total": total, "shown": len(cols),
                           "results": [{"id": c.get("id"), "description": (c.get("description") or "")[:200]} for c in cols]},
    )

"""INPE（ブラジル宇宙研究所）BDC STAC API（認証不要）。

CBERS-4/4A・Amazonia-1・GOES-19 等のブラジル及び国際衛星の地球観測画像を
STAC で検索・表示する。出典: data.inpe.br/bdc/stac/v1。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_SHORT, ttl_cache, is_error_result
from .stac_common import parse_bbox, parse_cloud_cover

STAC = "https://data.inpe.br/bdc/stac/v1"
UA = {"User-Agent": "space-finder-mcp/0.3 (MCP; INPE BDC STAC)"}

# よく使うコレクション（ID -> 説明）
COMMON_COLLECTIONS = {
    "CB4-WFI-L4-SR-1": "CBERS-4 WFI 地表反射率（ブラジル）",
    "CB4-WFI-L4-DN-1": "CBERS-4 WFI デジタル値",
    "CB4A-WFI-L4-SR-1": "CBERS-4A WFI 地表反射率",
    "CB4A-WPM-L2-DN-1": "CBERS-4A WPM パンクロマチック",
    "AMZ1-WFI-L4-SR-1": "Amazonia-1 WFI 地表反射率",
    "CBERS-WFI-16D-2": "CBERS WFI 16日合成",
    "S2-16D-2": "Sentinel-2 16日合成",
    "GOES19-L2-CMI-1": "GOES-19 気象衛星",
}


@ttl_cache(TTL_DAILY, maxsize=32, skip_if=is_error_result)
def inpe_collections(limit: int = 30) -> CallToolResult:
    """ブラジルINPEの衛星データコレクション一覧を返す（認証不要）。

    例:「ブラジルの衛星データ」「CBERSのコレクション」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 30、最大 100）。
    """
    limit = max(1, min(int(limit), 100))
    try:
        r = requests.get(f"{STAC}/collections", headers=UA, timeout=30)
        r.raise_for_status()
        cols = r.json().get("collections", [])
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"INPE BDC STAC への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "data.inpe.br/bdc/stac/v1"},
        )
    total = len(cols)
    cols = cols[:limit]
    lines = [f"INPE（ブラジル宇宙研究所）衛星データコレクション（全 {total} 件中、先頭 {len(cols)} 件）:"]
    for i, c in enumerate(cols, 1):
        cid = c.get("id", "?")
        desc = (c.get("description") or "")[:80]
        mark = f"  ★ {COMMON_COLLECTIONS[cid]}" if cid in COMMON_COLLECTIONS else ""
        lines.append(f"{i}. **{cid}**{mark}")
        if desc:
            lines.append(f"   {desc}")
    lines.append("出典: data.inpe.br/bdc/stac/v1 ／ 検索は inpe_search ツールで。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"total": total, "shown": len(cols),
                           "results": [{"id": c.get("id"), "description": (c.get("description") or "")[:200]} for c in cols]},
    )


@ttl_cache(TTL_SHORT, maxsize=64, skip_if=is_error_result)
def inpe_search(collection: str = "CB4-WFI-L4-SR-1", bbox: Optional[str] = None,
                datetime: Optional[str] = None, max_cloud_cover: Optional[float] = None,
                limit: int = 5) -> CallToolResult:
    """ブラジルINPEの衛星画像（CBERS・Amazonia-1・GOES等）をSTACで検索する（認証不要）。

    例:「ブラジルのCBERS衛星画像」「サンパウロ周辺の最近の衛星画像」
    content に表示用サマリ、structuredContent に JSON（id/日時/bbox/雲量）を返す。

    Args:
        collection: STAC コレクションID（既定 "CB4-WFI-L4-SR-1"）。inpe_collections で確認可。
        bbox: 空間範囲（"lon_min,lat_min,lon_max,lat_max"）。
        datetime: 時間範囲（"YYYY-MM-DD" または "start/end"）。省略で最新。
        max_cloud_cover: 雲量上限（%）（eo:cloud_cover を持つ場合）。
        limit: 返す件数（既定 5、最大 10）。
    """
    limit = max(1, min(int(limit), 10))
    body: dict = {"limit": limit, "collections": [collection]}
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
    cloud, cloud_err = parse_cloud_cover(max_cloud_cover)
    if cloud_err:
        return CallToolResult(
            content=[TextContent(type="text", text=cloud_err)],
            structuredContent={"error": "bad max_cloud_cover", "max_cloud_cover": max_cloud_cover},
        )
    if cloud is not None:
        body["filter"] = {"op": "<", "args": [{"property": "eo:cloud_cover"}, cloud]}
    try:
        r = requests.post(f"{STAC}/search", headers=UA, json=body, timeout=35)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"INPE STAC 検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "data.inpe.br/bdc/stac/v1"},
        )
    feats = d.get("features", [])
    if not feats:
        return CallToolResult(
            content=[TextContent(type="text", text=f"コレクション '{collection}' に条件一致の画像がありませんでした。")],
            structuredContent={"collection": collection, "total": 0, "results": []},
        )
    records = []
    for it in feats:
        props = it.get("properties", {})
        rec = {
            "id": it.get("id", ""),
            "datetime": (props.get("datetime") or props.get("start_datetime") or "")[:19],
            "cloud_cover": props.get("eo:cloud_cover"),
            "bbox": it.get("bbox"),
            "collection": collection,
            "assets": {k: v.get("href") for k, v in list((it.get("assets") or {}).items())[:6]},
        }
        records.append(rec)
    lines = [f"INPE {collection} の衛星画像（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        cc = r["cloud_cover"]
        cc_s = f" 雲量 {cc}%" if cc is not None else ""
        lines.append(f"{i}. **{r['id'][:48]}**  {r['datetime']} UTC{cc_s}")
        if r["bbox"]:
            lines.append(f"   領域 bbox: {[round(x,2) for x in r['bbox']]}")
    lines.append("出典: data.inpe.br/bdc/stac/v1 ／ 画像ダウンロードは INPE のアカウント/規約に従います。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": collection, "shown": len(records), "results": records},
    )

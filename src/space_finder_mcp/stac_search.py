"""AWS Earth Search STAC — 衛星データ検索（Sentinel-2 / Landsat / NAIP / DEM 等, 認証不要）。

earth-search.aws.element84.com/v1 は STAC (SpatioTemporal Asset Catalog) 規格の
衛星データカタログで、Sentinel-2・Landsat 8/9・NAIP・Copernicus DEM などを
認証不要で検索できる。bbox・日時・雲量・コレクションで絞り込む。
出典: earth-search.aws.element84.com
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_SHORT, ttl_cache, is_error_result
from .stac_common import parse_bbox, parse_cloud_cover

STAC = "https://earth-search.aws.element84.com/v1"
UA = {"User-Agent": "space-finder-mcp/0.7 (MCP; AWS Earth Search STAC)"}

COMMON_COLLECTIONS = {
    "sentinel-2-l2a": "Sentinel-2 地表反射率（最新・推奨）",
    "sentinel-2-l1c": "Sentinel-2 トップオブ大気",
    "sentinel-2-pre-c1-l2a": "Sentinel-2 旧コレクション地表反射率",
    "landsat-c2-l2": "Landsat 8/9 地表反射率",
    "sentinel-1-grd": "Sentinel-1 合成開口レーダー(GRD)",
    "naip": "NAIP 米国航空写真",
    "cop-dem-glo-30": "Copernicus DEM 標高(30m)",
    "cop-dem-glo-90": "Copernicus DEM 標高(90m)",
}


def _parse_assets(assets: dict) -> dict:
    """STAC アイテムの assets から主要なファイルURLを抽出する。"""
    out = {}
    for k, v in assets.items():
        href = v.get("href", "")
        if href and any(ext in href.lower() for ext in (".tif", ".tiff", ".jp2", ".xml")):
            out[k] = href
            if len(out) >= 6:
                break
    return out


@ttl_cache(TTL_DAILY, maxsize=32, skip_if=is_error_result)
def stac_collections() -> CallToolResult:
    """AWS Earth Search で利用可能な衛星データコレクション一覧を返す（認証不要）。

    例:「AWSの衛星データ」「利用できるLandsatコレクション」
    Args:
        （なし）
    """
    try:
        r = requests.get(f"{STAC}/collections", headers=UA, timeout=30)
        r.raise_for_status()
        cols = r.json().get("collections", [])
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Earth Search STAC への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "earth-search.aws.element84.com"},
        )
    records = []
    for c in cols:
        cid = c.get("id", "?")
        records.append({
            "id": cid,
            "title": c.get("title", ""),
            "description": (c.get("description") or "")[:200],
            "note": COMMON_COLLECTIONS.get(cid, ""),
        })
    lines = [f"🌍 AWS Earth Search の衛星データコレクション（{len(records)} 件）:"]
    for i, c in enumerate(records, 1):
        mark = f"  ★ {c['note']}" if c["note"] else ""
        lines.append(f"{i}. **{c['id']}**{mark}")
        if c.get("title"):
            lines.append(f"   {c['title']}")
    lines.append("出典: earth-search.aws.element84.com/v1 ／ 検索は stac_search ツールで。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"shown": len(records), "results": records},
    )


@ttl_cache(TTL_SHORT, maxsize=64, skip_if=is_error_result)
def stac_search(collection: str = "sentinel-2-l2a", bbox: Optional[str] = None,
                place: Optional[str] = None, datetime: Optional[str] = None,
                max_cloud_cover: Optional[float] = None, limit: int = 5) -> CallToolResult:
    """AWS Earth Search で衛星データ（Sentinel-2 / Landsat / NAIP / DEM）をSTAC検索する（認証不要）。

    例:「東京のLandsat画像」「関東のSentinel-2の雲が少ない画像」
    場所は bbox（"lon_min,lat_min,lon_max,lat_max"）または place（地名）。
    content に表示用サマリ、structuredContent に JSON（id/日時/雲量/bbox/アセットURL）を返す。

    Args:
        collection: コレクションID（既定 "sentinel-2-l2a"）。stac_collections で確認可。
                    例: "landsat-c2-l2", "sentinel-2-l1c", "sentinel-1-grd", "naip"。
        bbox: 空間範囲（"lon_min,lat_min,lon_max,lat_max"）。
        place: 地名（bbox より優先。Open-Meteo のジオコーディングで解決）。
        datetime: 時間範囲（"YYYY-MM-DD" または "start/end"）。省略で最近。
        max_cloud_cover: 雲量上限（%）（eo:cloud_cover を持つ場合）。
        limit: 返す件数（既定 5、最大 10）。
    """
    limit = max(1, min(int(limit), 10))
    body: dict = {"limit": limit, "collections": [collection]}

    # 場所解決
    if place:
        from .weather_astro import _geocode
        g = _geocode(place)
        if not g:
            return CallToolResult(
                content=[TextContent(type="text", text=f"地名 '{place}' を解決できませんでした。bbox を直接指定してください。")],
                structuredContent={"error": "geocode failed", "place": place},
            )
        lat, lon = g["latitude"], g["longitude"]
        body["bbox"] = [lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1]
    else:
        bbox_vals, bbox_err = parse_bbox(bbox)
        if bbox_err:
            return CallToolResult(
                content=[TextContent(type="text", text=bbox_err)],
                structuredContent={"error": "bad bbox", "bbox": bbox},
            )
        if bbox_vals:
            body["bbox"] = bbox_vals
        else:
            # 既定: 日本・関東
            body["bbox"] = [139.6, 35.5, 139.9, 35.8]

    if datetime:
        body["datetime"] = datetime
    cloud, cloud_err = parse_cloud_cover(max_cloud_cover)
    if cloud_err:
        return CallToolResult(
            content=[TextContent(type="text", text=cloud_err)],
            structuredContent={"error": "bad max_cloud_cover", "max_cloud_cover": max_cloud_cover},
        )
    if cloud is not None:
        # Earth Search は STAC の query パラメータでフィールドフィルタ（cql filter は非対応）
        body["query"] = {"eo:cloud_cover": {"lt": cloud}}

    try:
        r = requests.post(f"{STAC}/search", headers=UA, json=body, timeout=35)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Earth Search STAC 検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "earth-search.aws.element84.com"},
        )

    feats = d.get("features", [])
    matched = d.get("context", {}).get("matched") or d.get("numberMatched", 0)
    if not feats:
        return CallToolResult(
            content=[TextContent(type="text", text=f"コレクション '{collection}' に条件一致のデータがありませんでした。")],
            structuredContent={"collection": collection, "total": 0, "results": []},
        )

    records = []
    for it in feats:
        props = it.get("properties", {})
        rec = {
            "id": it.get("id", ""),
            "datetime": (props.get("datetime") or props.get("start_datetime") or "")[:19],
            "collection": it.get("collection"),
            "cloud_cover": props.get("eo:cloud_cover"),
            "bbox": it.get("bbox"),
            "platform": props.get("platform"),
            "assets": _parse_assets(it.get("assets") or {}),
        }
        records.append(rec)

    lines = [f"🌍 {collection} の衛星データ（全 {matched} 件中、先頭 {len(records)} 件）: AWS Earth Search"]
    for i, r in enumerate(records, 1):
        cc = r["cloud_cover"]
        cc_s = f" 雲量 {cc:.1f}%" if cc is not None else ""
        plat = f" [{r['platform']}]" if r.get("platform") else ""
        lines.append(f"{i}. **{r['id'][:52]}**  {r['datetime']} UTC{plat}{cc_s}")
        if r["bbox"]:
            lines.append(f"   領域 bbox: {[round(x,2) for x in r['bbox']]}")
        if r.get("assets"):
            first = next(iter(r["assets"].values()))
            lines.append(f"   データ: {first[:80]}")
    lines.append("出典: earth-search.aws.element84.com/v1 ／ 画像データURLは structuredContent の assets に含まれます。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": collection, "total": matched, "shown": len(records),
                           "results": records},
    )

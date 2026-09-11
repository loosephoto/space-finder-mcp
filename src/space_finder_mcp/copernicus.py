"""ESA Copernicus Data Space Ecosystem（欧州）STAC API。

検索は認証不要。ダウンロードのみ OAuth2 client_credentials が必要（環境変数
CDSE_CLIENT_ID / CDSE_CLIENT_SECRET があれば、プレビュー画像取得にも使う）。
キーはサーバー側でのみ保持し、クライアントへ晒さない。
STAC v1 エンドポイント: https://stac.dataspace.copernicus.eu/v1
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .stac_common import parse_bbox, parse_cloud_cover

STAC = "https://stac.dataspace.copernicus.eu/v1"
UA = {"User-Agent": "space-finder-mcp/0.2 (MCP; Copernicus STAC)"}

# よく使うコレクション（Sentinelシリーズほか）
COMMON_COLLECTIONS = {
    "sentinel-2-l2a": "Sentinel-2 光学・可視/近赤外（地表反射率）",
    "sentinel-2-l1c": "Sentinel-2 光学・可視（トップオブ大気）",
    "sentinel-1-grd": "Sentinel-1 合成開口レーダー（地表）",
    "sentinel-3-olci": "Sentinel-3 海洋/陸地カラー",
    "sentinel-3-slstr": "Sentinel-3 熱赤外/放射計",
    "sentinel-5p": "Sentinel-5P 大気成分（NO2/O3等）",
}


def _fetch_collections() -> list[dict]:
    r = requests.get(f"{STAC}/collections", headers=UA, timeout=25)
    r.raise_for_status()
    return r.json().get("collections", [])


def copernicus_collections(limit: int = 20) -> CallToolResult:
    """ESA Copernicus の利用可能な衛星データコレクション一覧を返す（認証不要）。

    例:「Copernicusの衛星データ」「Sentinelのコレクション」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 20、最大 100）。
    """
    limit = max(1, min(int(limit), 100))
    try:
        cols = _fetch_collections()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Copernicus STAC への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "stac.dataspace.copernicus.eu"},
        )
    total = len(cols)
    cols = cols[:limit]
    lines = [f"ESA Copernicus 衛星データコレクション（全 {total} 件中、先頭 {len(cols)} 件）:"]
    for i, c in enumerate(cols, 1):
        cid = c.get("id", "?")
        title = c.get("title", "") or ""
        desc = (c.get("description", "") or "")[:90]
        mark = ""
        if cid in COMMON_COLLECTIONS:
            mark = f" ★ {COMMON_COLLECTIONS[cid]}"
        lines.append(f"{i}. **{cid}** — {title}{mark}")
        if desc:
            lines.append(f"   {desc}")
    lines.append("出典: stac.dataspace.copernicus.eu ／ 検索は copernicus_search ツールで。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"total": total, "shown": len(cols),
                           "results": [{"id": c.get("id"), "title": c.get("title"),
                                        "description": (c.get("description") or "")[:200]} for c in cols]},
    )


def copernicus_search(collection: str = "sentinel-2-l2a", bbox: Optional[str] = None,
                      datetime: Optional[str] = None, max_cloud_cover: Optional[float] = None,
                      limit: int = 5) -> CallToolResult:
    """ESA Copernicus（Sentinel ほか）の衛星画像を STAC で検索する（検索は認証不要）。

    例:「東京のSentinel-2画像」「関東の最近の衛星画像」
    content に表示用サマリ、structuredContent に JSON（id/日時/bbox/雲量/プレビューURL）を返す。
    画像のダウンロードには CDSE アカウント（OAuth2）が必要だが、検索・プレビューURL取得は無料。

    Args:
        collection: STAC コレクションID（既定 "sentinel-2-l2a"）。copernicus_collections で確認可。
        bbox: 空間範囲（"lon_min,lat_min,lon_max,lat_max"、例 東京: "139.6,35.5,139.9,35.8"）。
        datetime: 時間範囲（"YYYY-MM-DD" または "start/end"、例 "2026-01-01/2026-03-31"）。省略で最新。
        max_cloud_cover: 雲量の上限（%）（Sentinel-2 等 eo:cloud_cover を持つ場合）。
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
            content=[TextContent(type="text", text=f"Copernicus STAC 検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "stac.dataspace.copernicus.eu"},
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
            "thumbnail": None,
            "assets": {k: v.get("href") for k, v in list((it.get("assets") or {}).items())[:6]},
        }
        assets = it.get("assets") or {}
        # プレビュー/サムネイルを探す
        for key in ("thumbnail", "preview", "quicklook", "visual"):
            if key in assets:
                rec["thumbnail"] = assets[key].get("href")
                break
        records.append(rec)

    lines = [f"Copernicus {collection} の衛星画像（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        cc = r["cloud_cover"]
        cc_s = f"雲量 {cc}%" if cc is not None else ""
        lines.append(f"{i}. **{r['id'][:44]}**  {r['datetime']} UTC  {cc_s}")
        if r["bbox"]:
            lines.append(f"   領域 bbox: {[round(x,2) for x in r['bbox']]}")
        if r.get("thumbnail"):
            lines.append(f"   プレビュー: {r['thumbnail']}")
    lines.append("出典: stac.dataspace.copernicus.eu ／ ダウンロードには CDSE アカウント(OAuth2)が必要。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": collection, "shown": len(records), "results": records},
    )

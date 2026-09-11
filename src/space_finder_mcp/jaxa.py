"""JAXA Earth API（日本）STAC COG カタログ（認証不要）。

JAXA Earth の地球観測データ（ALOS・GSMaP・GCOM 等）を STAC catalog.json から
辿って、コレクション一覧とコレクション内の利用可能アセットを返す。
出典: data.earth.jaxa.jp ／ STAC カタログは静的JSON（S3 上の collection.json）。
"""
from __future__ import annotations

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_SHORT, ttl_cache, is_error_result
from .input_utils import as_int

ROOT = "https://data.earth.jaxa.jp/stac/cog/v1/catalog.json"
UA = {"User-Agent": "space-finder-mcp/0.2 (MCP; JAXA Earth STAC)"}


def _get_json(url: str) -> dict:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def _child_collections(catalog_url: str = ROOT, depth: int = 0,
                       max_collections: int = 60) -> list[dict]:
    """catalog.json を再帰的に辿り、collection.json を集める。"""
    out: list[dict] = []
    try:
        cat = _get_json(catalog_url)
    except requests.RequestException:
        return out
    for l in cat.get("links", []):
        if l.get("rel") == "child":
            href = l["href"]
            if "collection.json" in href:
                if len(out) >= max_collections:
                    return out
                try:
                    c = _get_json(href)
                    out.append({
                        "id": c.get("id"),
                        "title": c.get("title") or "",
                        "description": (c.get("description") or "")[:200],
                        "url": href,
                        "assets": list((c.get("assets") or {}).keys())[:8],
                    })
                except requests.RequestException:
                    continue
            elif depth < 2:
                # 中間カタログを再帰
                out.extend(_child_collections(href, depth + 1, max_collections))
    return out


@ttl_cache(TTL_DAILY, maxsize=32, skip_if=is_error_result)
def jaxa_datasets(limit: int = 30) -> CallToolResult:
    """JAXA Earth の地球観測データセット（ALOS/GSMaP/GCOM等）一覧を返す（認証不要）。

    例:「JAXAの地球観測データ」「GSMaP降雨データセット」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返すデータセット件数（既定 30、最大 100）。
    """
    limit = as_int(limit, 30, 1, 100)
    cols = _child_collections(max_collections=limit)
    if not cols:
        return CallToolResult(
            content=[TextContent(type="text", text="JAXA Earth STAC カタログを取得できませんでした（一時的障害の可能性）。")],
            structuredContent={"error": "no collections", "source": "data.earth.jaxa.jp"},
        )
    lines = [f"JAXA Earth の地球観測データセット（{len(cols)} 件）:"]
    for i, c in enumerate(cols, 1):
        lines.append(f"{i}. **{c['id']}**")
        if c.get("title"):
            lines.append(f"   {c['title'][:80]}")
        if c.get("description"):
            lines.append(f"   {c['description'][:70]}")
        if c.get("assets"):
            lines.append(f"   アセット: {', '.join(c['assets'])}")
    lines.append("出典: data.earth.jaxa.jp/stac ／ 検索は jaxa_dataset_search ツールで。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"shown": len(cols), "results": cols},
    )


@ttl_cache(TTL_SHORT, maxsize=64, skip_if=is_error_result)
def jaxa_dataset_search(query: str, limit: int = 10) -> CallToolResult:
    """JAXA Earth のデータセットをキーワード検索する（認証不要）。

    例:「GSMaPの降雨データ」「ALOSの標高データ」「GCOMの海面水温」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        query: 検索語（例 "GSMaP", "ALOS", "GCOM", "precip", "rain", "FNF", "AW3D"）。
        limit: 返す件数（既定 10、最大 100）。
    """
    limit = as_int(limit, 10, 1, 100)
    q = (query or "").strip().lower()
    cols = _child_collections(max_collections=100)
    if not q:
        return CallToolResult(
            content=[TextContent(type="text", text="検索語を指定してください（例: GSMaP, ALOS, GCOM, precip）。")],
            structuredContent={"error": "empty query"},
        )
    hits = [c for c in cols if q in (c["id"].lower() + " " + c["title"].lower() + " " + c["description"].lower())]
    total = len(hits)
    hits = hits[:limit]
    if not hits:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{query}' に一致するJAXAデータセットが見つかりませんでした。全データセットは jaxa_datasets で確認できます。")],
            structuredContent={"query": query, "total": 0, "results": []},
        )
    lines = [f"JAXA Earth データセット検索: '{query}'（{total} 件中 {len(hits)} 件表示）:"]
    for i, c in enumerate(hits, 1):
        lines.append(f"{i}. **{c['id']}**")
        if c.get("title"):
            lines.append(f"   {c['title'][:80]}")
        if c.get("assets"):
            lines.append(f"   アセット: {', '.join(c['assets'])}")
    lines.append("出典: data.earth.jaxa.jp/stac")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": query, "total": total, "shown": len(hits), "results": hits},
    )

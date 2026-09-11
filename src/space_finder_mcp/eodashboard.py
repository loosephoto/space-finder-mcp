"""EO Dashboard (eodashboard.org) — NASA・ESA・JAXA 3機関共同の地球観測データカタログ。

EO Dashboard のデータは GitHub リポジトリで無料公開されている:
- カタログ定義: github.com/ESA-eodashboards/eodashboard-catalog (collections/*.json, 173件)
- 画像・説明: github.com/eurodatacube/eodash-assets (collections/<ID>/thumbnail.jpg, legend.png, <ID>.md)
- STAC: ESA-eodashboards.github.io/eodashboard-catalog/trilateral/catalog.json

本モジュールは「検索・概要・学習」用途（メタデータ・説明・画像・参照リンク）を
認証不要で提供する。時系列データ（Sentinel Hub統計API）は別途キーが必要。
"""
from __future__ import annotations

import io
from typing import Optional
import base64

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import TTL_HOURLY, ttl_cache, is_error_result

# コレクション定義JSON（GitHub raw）
RAW = "https://raw.githubusercontent.com/ESA-eodashboards/eodashboard-catalog/main/collections"
# カタログのツリー一覧（GitHub API）
TREE_API = "https://api.github.com/repos/ESA-eodashboards/eodashboard-catalog/git/trees/main?recursive=1"
# 画像・説明アセット
ASSETS = "https://raw.githubusercontent.com/eurodatacube/eodash-assets/refs/heads/main/collections"
UA = {"User-Agent": "space-finder-mcp/0.5 (MCP; EO Dashboard catalog)"}

# テーマの日本語訳
_THEMES_JA = {
    "agriculture": "農業", "atmosphere": "大気", "biomass": "バイオマス",
    "covid-19": "COVID-19", "cryosphere": "雪氷圏", "economy": "経済",
    "extreme-events": "極端現象", "oceans": "海洋", "land": "陸域", "water": "水圏",
    "society": "社会", "urban": "都市",
}

# テーマ別の代表的なカテゴリマッピング（コレクションJSONのThemesから）
# コレクションJSONを集めて一覧を作るためのキャッシュ
_collections_cache: list[dict] | None = None


def _fetch_collection_list(force: bool = False) -> list[dict]:
    """コレクション一覧（ID・タイトル・テーマ・機関）を取得する。キャッシュあり。"""
    global _collections_cache
    if _collections_cache and not force:
        return _collections_cache
    out: list[dict] = []
    try:
        r = requests.get(TREE_API, headers=UA, timeout=30)
        r.raise_for_status()
        tree = r.json().get("tree", [])
        ids = [t["path"].split("/")[-1][:-5] for t in tree
               if t["path"].startswith("collections/") and t["path"].endswith(".json")
               and "/" not in t["path"].split("collections/")[1][:-5]]
        # 各JSONを取得（並列ではなく順次・多めのタイムアウト）
        for cid in ids:
            meta = _fetch_meta(cid)
            if meta:
                out.append(meta)
        # 検索可能な状態でキャッシュ
        _collections_cache = out
    except requests.RequestException:
        pass
    return out


def _fetch_meta(cid: str) -> Optional[dict]:
    """1コレクションのメタデータJSONを取得する。"""
    try:
        r = requests.get(f"{RAW}/{cid}.json", headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "id": d.get("EodashIdentifier") or cid,
            "name": d.get("Name") or cid,
            "title": d.get("Title") or d.get("Subtitle") or cid,
            "subtitle": d.get("Subtitle", ""),
            "description": d.get("Description", ""),
            "themes": d.get("Themes", []),
            "tags": d.get("Tags", []),
            "agency": d.get("Agency", []),
            "satellites": (d.get("DataSource", {}).get("Spaceborne", {}).get("Satellite", []) or []),
            "sensors": (d.get("DataSource", {}).get("Spaceborne", {}).get("Sensor", []) or []),
            "references": d.get("References", []),
            "image": d.get("Image", ""),
            "legend": d.get("Legend", ""),
        }
    except (requests.RequestException, ValueError):
        return None


@ttl_cache(TTL_HOURLY, maxsize=32, skip_if=is_error_result)
def eodashboard_collections(themes: Optional[str] = None, agency: Optional[str] = None,
                            keyword: Optional[str] = None, limit: int = 20) -> CallToolResult:
    """EO Dashboard（NASA・ESA・JAXA共同）の地球観測データセットを検索する（認証不要）。

    3機関の衛星データ（大気・海洋・陸域・雪氷・農業・社会経済など173件）の
    一覧・検索を返す。content に表示用サマリ、structuredContent に JSON を返す。

    例:「NASAの大気データ」「気候変動の衛星データ」「NO2 のデータセット」
    テーマで絞る: agriculture / atmosphere / biomass / covid-19 / cryosphere /
                 economy / extreme-events / oceans

    Args:
        themes: テーマ（例 "atmosphere", "oceans"）。カンマ区切りで複数可。
        agency: 機関（例 "NASA", "ESA", "JAXA"）。
        keyword: キーワード（タイトル・説明に部分一致）。
        limit: 返す件数（既定 20、最大 50）。
    """
    limit = max(1, min(int(limit), 50))
    cols = _fetch_collection_list()
    if not cols:
        return CallToolResult(
            content=[TextContent(type="text", text="EO Dashboard カタログを取得できませんでした（GitHub API レート制限か一時的障害の可能性）。")],
            structuredContent={"error": "no collections", "source": "github.com/ESA-eodashboards/eodashboard-catalog"},
        )

    # 絞り込み
    results = cols
    if themes:
        want = {t.strip().lower() for t in themes.split(",")}
        results = [c for c in results if any(t.lower() in want for t in c["themes"])]
    if agency:
        ag = agency.strip().lower()
        results = [c for c in results if any(ag in a.lower() for a in c["agency"])]
    if keyword:
        kw = keyword.strip().lower()
        results = [c for c in results
                   if kw in (c["title"] + " " + c["subtitle"] + " " + c["name"] + " "
                             + " ".join(c["tags"]) + " " + " ".join(c["satellites"])).lower()]

    total = len(results)
    results = results[:limit]
    if not results:
        return CallToolResult(
            content=[TextContent(type="text", text=f"条件（themes={themes}, agency={agency}, keyword={keyword}）に一致するEO Dashboardデータセットはありませんでした。")],
            structuredContent={"themes": themes, "agency": agency, "keyword": keyword, "total": 0, "results": []},
        )

    lines = [f"🌍 EO Dashboard の地球観測データセット（全 {total} 件中、先頭 {len(results)} 件）: NASA×ESA×JAXA 共同"]
    for i, c in enumerate(results, 1):
        agency = "/".join(c["agency"]) if c["agency"] else "?"
        sat = ", ".join(c["satellites"]) if c["satellites"] else ""
        th = ", ".join(_THEMES_JA.get(t, t) for t in c["themes"])
        lines.append(f"{i}. **{c['title']}**  [{agency}]  {th}")
        if sat:
            lines.append(f"   衛星: {sat}")
        if c.get("subtitle"):
            lines.append(f"   {c['subtitle'][:70]}")
    lines.append("出典: github.com/ESA-eodashboards/eodashboard-catalog ／ 詳細は eodashboard_detail ツールで。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"themes": themes, "agency": agency, "keyword": keyword,
                           "total": total, "shown": len(results), "results": results},
    )


@ttl_cache(TTL_HOURLY, maxsize=64, skip_if=is_error_result)
def eodashboard_detail(identifier: str, show_image: bool = True) -> CallToolResult:
    """EO Dashboard の1つのデータセットの詳細（衛星・センサー・説明・参照リンク・画像）を返す。

    例:「NO2データセットの詳細」「EO Dashboardの海面水温の情報」
    content に表示用サマリ＋サムネイル画像(インライン)、structuredContent に JSON を返す。

    Args:
        identifier: コレクションIDまたは名前（例 "N1_NO2", "NO2_daily", "sea_ice"）。
                    大まかな名前でも探す。
        show_image: サムネイルをチャットにインライン表示するか（既定 True）。
    """
    ident = (identifier or "").strip()
    if not ident:
        return CallToolResult(
            content=[TextContent(type="text", text="identifier を指定してください（例: N1_NO2, sea_surface_temperature）。")],
            structuredContent={"error": "empty identifier"},
        )
    cols = _fetch_collection_list()
    # 完全一致→部分一致の順で探す
    meta = next((c for c in cols if c["id"] == ident), None) or next(
        (c for c in cols if ident.lower() in c["id"].lower() or ident.lower() in c["name"].lower()), None)
    if meta is None:
        # 直接取得を試みる
        meta = _fetch_meta(ident)
    if meta is None:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{ident}' に一致するEO Dashboardデータセットが見つかりませんでした。eodashboard_collections で一覧を確認してください。")],
            structuredContent={"identifier": ident, "error": "not found"},
        )

    lines = [f"🌍 **{meta['title']}**  [{meta['id']}]"]
    if meta.get("subtitle"):
        lines.append(f"   {meta['subtitle']}")
    if meta.get("agency"):
        lines.append(f"   🏛 機関: {' / '.join(meta['agency'])}")
    if meta.get("satellites"):
        lines.append(f"   🛰 衛星: {', '.join(meta['satellites'])}")
        if meta.get("sensors"):
            lines.append(f"   🔬 センサー: {', '.join(meta['sensors'])}")
    th = ", ".join(_THEMES_JA.get(t, t) for t in meta["themes"])
    if th:
        lines.append(f"   📂 テーマ: {th}")
    if meta.get("tags"):
        lines.append(f"   🏷 タグ: {', '.join(meta['tags'][:8])}")
    if meta.get("references"):
        lines.append("   📚 参考リンク:")
        for ref in meta["references"][:5]:
            lines.append(f"      - [{ref.get('Name','?')}]({ref.get('Url','')})")
    lines.append("出典: github.com/ESA-eodashboards/eodashboard-catalog（EO Dashboard, NASA×ESA×JAXA）")
    content_blocks = [TextContent(type="text", text="\n".join(lines))]

    # サムネイル画像のインライン表示
    img_url = None
    if meta.get("image"):
        # "N1_NO2/N1_NO2.jpg" 形式 → eodash-assets の collections/<path>
        img_url = f"{ASSETS}/{meta['image']}"
    if show_image and img_url:
        try:
            r = requests.get(img_url, headers=UA, timeout=25)
            if r.status_code == 200 and len(r.content) <= 3_500_000:
                data = r.content
                mime = "image/jpeg"
                if img_url.lower().endswith(".png"):
                    mime = "image/png"
                content_blocks.append(ImageContent(
                    type="image", data=base64.b64encode(data).decode("ascii"),
                    mimeType=mime, altText=meta["title"]))
        except requests.RequestException:
            pass

    return CallToolResult(content=content_blocks, structuredContent={"identifier": meta["id"], "detail": meta})

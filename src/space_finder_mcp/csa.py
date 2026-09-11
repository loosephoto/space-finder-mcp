"""CSA（カナダ宇宙庁）オープンデータポータル（CKAN API、認証不要）。

出典: donnees-data.asc-csa.gc.ca（CSA オープンデータポータル）。
CKAN API: /api/3/action/{package_search|package_show}。
RADARSAT 等のカナダの宇宙データセットのメタデータを検索・表示する。

注: このサーバーは https で、送信チェーンが不完全なため certifi の静的バンドル
(cacert.pem) だけでは証明書検証に失敗することがある。安全のため OS の証明書
ストア（ssl.create_default_context）を使うアダプタで検証する（verify を無効化
しない）。
"""
from __future__ import annotations

import ssl
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, ttl_cache, is_error_result

CKAN = "https://donnees-data.asc-csa.gc.ca/api/3/action"
UA = {"User-Agent": "space-finder-mcp/0.2 (MCP; CSA Open Data)"}


class _SystemCAAdapter(HTTPAdapter):
    """OS の証明書ストア（システムCA）で TLS 検証を行うアダプタ。

    certifi バンドルに欠けている中間CAを解決するため、ssl.create_default_context()
    （Windows/Linux/macOS でシステムストアを参照）を使う。検証は無効化しない。
    """

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


# 共有セッション（システムCA検証）
_session = requests.Session()
_session.mount("https://", _SystemCAAdapter())


def _ckan(action: str, params: Optional[dict] = None) -> dict:
    r = _session.get(f"{CKAN}/{action}", headers=UA, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


@ttl_cache(TTL_DAILY, maxsize=64, skip_if=is_error_result)
def csa_dataset_search(query: str = "", limit: int = 10) -> CallToolResult:
    """CSA（カナダ宇宙庁）オープンデータポータルでデータセットを検索する（認証不要）。

    例:「カナダのRADARSATデータ」「CSAの宇宙データセット」
    content に表示用サマリ、structuredContent に JSON（ID/タイトル/概要/ライセンス/URL）を返す。

    Args:
        query: 検索語（例 "radarsat", "space", "earth observation"）。省略で先頭のデータセット。
        limit: 返す件数（既定 10、最大 20）。
    """
    limit = max(1, min(int(limit), 20))
    params = {"rows": limit}
    if query:
        params["q"] = query
    try:
        d = _ckan("package_search", params)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"CSA ポータルへの接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "donnees-data.asc-csa.gc.ca"},
        )
    if not d.get("success"):
        return CallToolResult(
            content=[TextContent(type="text", text="CSA ポータルの検索に失敗しました。")],
            structuredContent={"error": d.get("error", "unknown")},
        )
    result = d.get("result", {})
    total = result.get("count", 0)
    pkgs = result.get("results", [])
    records = []
    for p in pkgs:
        rec = {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "title": p.get("title", ""),
            "notes": (p.get("notes") or "")[:250],
            "license": p.get("license_title"),
            "organization": (p.get("organization") or {}).get("title"),
            "url": f"https://donnees-data.asc-csa.gc.ca/dataset/{p.get('name','')}",
            "resources": [{"name": res.get("name"), "format": res.get("format"), "url": res.get("url")}
                          for res in p.get("resources", [])[:5]],
        }
        records.append(rec)

    if not records:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{query}' に一致するCSAデータセットが見つかりませんでした。")],
            structuredContent={"query": query, "total": 0, "results": []},
        )
    lines = [f"CSA（カナダ宇宙庁）データセット検索（全 {total} 件中、先頭 {len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. **{r['title']}**" + (f"  [{r['organization']}]" if r.get("organization") else ""))
        if r.get("notes"):
            lines.append(f"   概要: {r['notes'][:90]}")
        lines.append(f"   URL: {r['url']}")
        if r.get("license"):
            lines.append(f"   ライセンス: {r['license']}")
    lines.append("出典: donnees-data.asc-csa.gc.ca (CSA Open Data Portal)")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": query, "total": total, "shown": len(records), "results": records},
    )

"""ISRO（インド宇宙研究機関）公式オープンデータ API（認証不要）。

出典: github.com/isro/api（公式リポジトリ、Vercel で配信）。
Vercel デプロイAPI: https://isro.vercel.app/api/{spacecrafts|launchers|customer_satellites|centres}
返すツールは content に人間向け表示、structuredContent に JSON を返す（tokyo-transit 方式）。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, ttl_cache, is_error_result
from .input_utils import as_int

BASE = "https://isro.vercel.app/api"
UA = {"User-Agent": "space-finder-mcp/0.2 (MCP; ISRO data)"}

# データ種別 -> (エンドポイント, データ内のキー名, 表示列)
_ENDPOINTS = {
    "spacecrafts": ("spacecrafts", "spacecrafts", "name"),          # 人工衛星一覧
    "launchers": ("launchers", "launchers", "id"),                  # 打ち上げ機(ロケット)一覧
    "customer_satellites": ("customer_satellites", "customer_satellites", "id"),  # 外国向け顧客衛星
    "centres": ("centres", "centres", "name"),                      # センター/施設
}


def _fetch(kind: str) -> list[dict]:
    ep, key, _ = _ENDPOINTS[kind]
    r = requests.get(f"{BASE}/{ep}", headers=UA, timeout=25)
    r.raise_for_status()
    d = r.json()
    return d.get(key, [])


@ttl_cache(TTL_DAILY, maxsize=64, skip_if=is_error_result)
def isro_data(kind: str = "spacecrafts", query: Optional[str] = None,
              limit: int = 20) -> CallToolResult:
    """ISRO（インド宇宙研究機関）の公式データを返す（認証不要）。

    「インドの人工衛星」「ISROのロケット」「インドの宇宙センター」など。
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        kind: データ種別
            - "spacecrafts": ISROが打ち上げた人工衛星一覧（既定）
            - "launchers": ISROのロケット(打ち上げ機)一覧
            - "customer_satellites": ISROが外国向けに打ち上げた顧客衛星一覧
            - "centres": ISROのセンター/施設一覧
        query: 部分一致検索語（例: "cartosat", "chandrayaan"）。省略で全件。
        limit: 返す件数（既定 20、最大 100）。
    """
    kind = (kind or "spacecrafts").strip().lower()
    if kind not in _ENDPOINTS:
        return CallToolResult(
            content=[TextContent(type="text", text=f"kind は {', '.join(_ENDPOINTS)} のいずれかを指定してください。")],
            structuredContent={"error": f"unknown kind: {kind}", "available": sorted(_ENDPOINTS)},
        )
    limit = as_int(limit, 20, 1, 100)
    try:
        rows = _fetch(kind)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ISRO API への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "isro.vercel.app"},
        )

    if query:
        q = query.strip().lower()
        def _match(r):
            hay = " ".join([
                str(r.get("name", "")).lower(),
                str(r.get("id", "")).lower(),
                str(r.get("Place", "")).lower(),
                str(r.get("country", "")).lower(),
            ])
            return q in hay
        rows = [r for r in rows if _match(r)]

    total = len(rows)
    rows = rows[:limit]
    labels = {"spacecrafts": "人工衛星", "launchers": "ロケット(打ち上げ機)",
              "customer_satellites": "外国向け顧客衛星", "centres": "センター/施設"}
    name = labels.get(kind, kind)
    lines = [f"ISRO {name}（全 {total} 件中、先頭 {len(rows)} 件）: 出典 github.com/isro/api"]
    for i, r in enumerate(rows, 1):
        if kind == "customer_satellites":
            lines.append(f"{i}. **{r.get('id','?')}**（国: {r.get('country','?')}, 打ち上げ: {r.get('launch_date','?')}, 質量: {r.get('mass','?')}kg, ロケット: {r.get('launcher','?')}）")
        elif kind == "centres":
            lines.append(f"{i}. **{r.get('name','?')}**（{r.get('Place','?')}, {r.get('State','?')}）")
        else:
            v = r.get("name") or r.get("id")
            lines.append(f"{i}. {v}")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"kind": kind, "total": total, "shown": len(rows), "results": rows},
    )

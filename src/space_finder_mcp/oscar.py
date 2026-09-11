"""WMO OSCAR — 世界気象機関の衛星カタログ（認証不要）。

WMO OSCAR/Space (space.oscar.wmo.int) は、気象・地球環境観測を行う全世界の
衛星（Meteor-M, Resurs-P, Kanopus, GOES, Himawari, Sentinel 等）の運用ステータス・
軌道・打ち上げ情報を収録した公式カタログ。APIは認証不要。

出典: space.oscar.wmo.int/api/v1
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, ttl_cache, is_error_result

OSCAR = "https://space.oscar.wmo.int/api/v1"
UA = {"User-Agent": "space-finder-mcp/0.9 (MCP; WMO OSCAR/Space)"}

# 衛星運用ステータスの日本語訳
_STATUS = {
    "operational": "運用中", "planned": "計画中", "extended": "延長運用",
    "decommissioned": "退役", "failed": "故障", "nominal": "正常",
}


@ttl_cache(TTL_DAILY, maxsize=64, skip_if=is_error_result)
def satellite_status(query: Optional[str] = None, agency: Optional[str] = None,
                     limit: int = 10) -> CallToolResult:
    """世界気象機関（WMO）OSCARの衛星カタログから、気象・地球観測衛星の運用ステータスを返す。

    ロシアのMeteor-M・Resurs・Kanopus等、世界中の気象衛星の運用状況・軌道・打ち上げ日を確認できる。
    例:「ロシアの気象衛星」「Meteor-Mの運用状況」「世界の気象衛星一覧」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        query: 検索語（衛星名・略称の部分一致。例 "meteor", "resurs", "goes", "kanopus"）。
        agency: 機関名（例 "Roscosmos", "NOAA", "EUMETSAT", "JAXA"）。
        limit: 返す件数（既定 10、最大 20）。
    """
    limit = max(1, min(int(limit), 20))
    # 注: OSCAR API の search/space_agency/status パラメータは現状動作しない
    # （常に全件を返す）。そのため全件をページングで取得し、クライアントサイドで
    # フィルタする。max_pages までスキャンして十分な件数を集める。
    query_l = (query or "").strip().lower()
    agency_l = (agency or "").strip().lower()
    max_pages = 10  # 最大300件スキャン

    sats_all = []
    total = 0
    try:
        for page in range(1, max_pages + 1):
            r = requests.get(f"{OSCAR}/satellites", params={"page": page}, headers=UA, timeout=30)
            r.raise_for_status()
            d = r.json()
            total = d.get("page", {}).get("totalElements", total)
            page_sats = d.get("_embedded", {}).get("satellites", [])
            if not page_sats:
                break
            sats_all.extend(page_sats)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"WMO OSCAR への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "space.oscar.wmo.int"},
        )

    # クライアントサイドフィルタ
    def _matches(s):
        hay = " ".join([
            str(s.get("acronym") or ""), str(s.get("fullname") or ""),
            str(s.get("slug") or ""), str(s.get("space_agency") or ""),
        ]).lower()
        if query_l and query_l not in hay:
            return False
        if agency_l and agency_l not in str(s.get("space_agency") or "").lower():
            return False
        return True

    sats = [s for s in sats_all if _matches(s)]
    matched = len(sats)
    if not sats:
        return CallToolResult(
            content=[TextContent(type="text", text=f"条件（query={query}, agency={agency}）に一致する衛星がスキャン範囲に見つかりませんでした。")],
            structuredContent={"query": query, "agency": agency, "total": 0, "results": []},
        )

    sats = sats[:limit]
    records = []
    for s in sats:
        status = s.get("status") or ""
        rec = {
            "id": s.get("id"),
            "slug": s.get("slug"),
            "acronym": s.get("acronym"),
            "fullname": s.get("fullname"),
            "agency": s.get("space_agency"),
            "status": status,
            "status_ja": _STATUS.get(status.lower(), status),
            "launch_date": s.get("launch_date"),
            "orbit": s.get("orbit"),
            "altitude_km": s.get("Altitude"),
            "end_of_life": s.get("EoL"),
        }
        records.append(rec)

    lines = [f"🛰 WMO OSCAR の気象・地球観測衛星（全 {total} 件中、先頭 {len(records)} 件）: 出典 WMO公式"]
    for i, s in enumerate(records, 1):
        mark = {"運用中": "🟢", "計画中": "🔵", "延長運用": "🟡", "退役": "🔴", "故障": "⛔"}.get(s["status_ja"], "•")
        launch = s["launch_date"] or "?"
        ag = s["agency"] or "?"
        lines.append(f"{i}. {mark} **{s['acronym']}**（{s['fullname']}）  [{ag}]")
        lines.append(f"   状態: {s['status_ja']} ・ 打ち上げ: {launch}")
        if s.get("orbit"):
            lines.append(f"   軌道: {s['orbit']}")
        if s.get("altitude_km"):
            lines.append(f"   高度: {s['altitude_km']}")
    lines.append("出典: space.oscar.wmo.int（WMO OSCAR/Space 公式カタログ）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": query, "agency": agency, "total": total,
                           "shown": len(records), "results": records},
    )

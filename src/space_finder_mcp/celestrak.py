"""CelesTrak — 全世界の人工衛星・デブリの軌道要素（TLE）API（認証不要）。

NORAD カタログ上の全ての衛星の Two-Line Element（軌道要素）を JSON で返す。
出典: celestrak.org/NORAD/elements/gp.php。name/group 検索可能。
TLE は位置計算・可視パス予測の基礎データ。
"""
from __future__ import annotations

from typing import Optional

import requests
from functools import lru_cache
from mcp.types import CallToolResult, TextContent

BASE = "https://celestrak.org/NORAD/elements/gp.php"
UA = {"User-Agent": "space-finder-mcp/0.3 (MCP; CelesTrak TLE)"}

# よく使う衛星の NORAD カタログ番号
WELL_KNOWN: dict[str, int] = {
    "iss": 25544, "hubble": 20580, "himawari-8": 40267, "himawari-9": 41836,
    "landsat-8": 39084, "landsat-9": 49260, "noaa-20": 43013, "noaa-21": 54234,
    "meteor-m2": 40069, "goes-16": 41866, "goes-17": 41868, "goes-18": 51850,
    "tiangong": 48274, "sentinel-2a": 40697, "sentinel-2b": 42063, "sentinel-1a": 39634,
    "kepu": 44414,
}


@lru_cache(maxsize=64)
def _fetch_tle_cached(params_tuple: tuple) -> tuple:
    """TLE を取得（同一セッション内で同じ問い合わせはキャッシュ）。TLE は数時間有効。"""
    p = dict(params_tuple)
    p["FORMAT"] = "JSON"
    r = requests.get(BASE, headers=UA, params=p, timeout=30)
    r.raise_for_status()
    return tuple(r.json())


def _fetch_tle(params: dict) -> list[dict]:
    # params(dict) をタプル化してキャッシュキーに。結果は tuple→list に戻す。
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    return list(_fetch_tle_cached(key))


def sat_tle(name: Optional[str] = None, norad_id: Optional[int] = None,
            group: Optional[str] = None, limit: int = 5) -> CallToolResult:
    """任意の人工衛星（ISS・ハッブル・気象衛星・中国宇宙ステーション等）の軌道要素(TLE)を返す。

    例:「ISSの軌道要素」「ハッブル宇宙望遠鏡のTLE」「気象衛星の軌道」
    認証不要。CelesTrak（NORADカタログ）から取得。
    content に表示用サマリ、structuredContent に JSON（軌道パラメータ）を返す。

    Args:
        name: 衛星名または省略名（例 "iss", "hubble", "tiangong", "goes-18"）。
        norad_id: NORAD カタログ番号（例 25544=ISS）。name より優先。
        group: CelesTrak の衛星グループ（例 "stations", "weather", "amateur", "science"）。
        limit: 返す件数（既定 5、最大 20）。
    """
    limit = max(1, min(int(limit), 20))
    params: dict = {}
    if norad_id:
        params["CATNR"] = norad_id
    elif name:
        nm = name.strip().lower()
        # 既知の衛星名をNORAD IDに解決（完全一致を最優先）
        for key, nid in WELL_KNOWN.items():
            if nm == key:
                params["CATNR"] = nid
                break
        else:
            # 部分一致は短い名前の誤マッチを避けるため、長い入力のみ許可
            matched = None
            for key, nid in WELL_KNOWN.items():
                if len(nm) >= 4 and (key in nm or nm in key):
                    matched = nid
                    break
            if matched:
                params["CATNR"] = matched
            else:
                params["NAME"] = name.strip()
    elif group:
        params["GROUP"] = group
    else:
        params["GROUP"] = "stations"  # 既定: 有人宇宙関連
    try:
        rows = _fetch_tle(params)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"CelesTrak への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "celestrak.org"},
        )
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="指定した衛星の軌道要素が見つかりませんでした。NORAD ID や別名をお試しください。")],
            structuredContent={"query": {"name": name, "norad_id": norad_id, "group": group}, "total": 0, "results": []},
        )
    rows = rows[:limit]
    records = []
    for r in rows:
        records.append({
            "object_name": r.get("OBJECT_NAME"),
            "norad_id": r.get("NORAD_CAT_ID"),
            "intl_designator": r.get("OBJECT_ID"),
            "epoch": (r.get("EPOCH") or "")[:19],
            "inclination_deg": r.get("INCLINATION"),
            "ra_of_asc_node_deg": r.get("RA_OF_ASC_NODE"),
            "eccentricity": r.get("ECCENTRICITY"),
            "arg_perigee_deg": r.get("ARG_OF_PERICENTER"),
            "mean_anomaly_deg": r.get("MEAN_ANOMALY"),
            "mean_motion_rev_day": r.get("MEAN_MOTION"),
            "tle": f"{r.get('TLE_LINE1','')}\n{r.get('TLE_LINE2','')}",
        })
    lines = [f"CelesTrak 衛星軌道要素（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. **{r['object_name']}** (NORAD {r['norad_id']})")
        lines.append(f"   軌道: 傾角 {r['inclination_deg']}°・離心率 {r['eccentricity']}・周回 {r['mean_motion_rev_day']}/日")
        lines.append(f"   エポック: {r['epoch']} UTC")
    lines.append("出典: celestrak.org（NORAD GP カタログ）／ TLE は軌道計算・可視パス予測の基礎データ。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": {"name": name, "norad_id": norad_id, "group": group},
                           "shown": len(records), "results": records},
    )

"""天宮（Tiangong）中国宇宙ステーションのリアルタイム位置。

CelesTrak（NORAD GP カタログ）から天宮（NORAD 48274）の最新 TLE を取得し、
SGP4 で現在の緯度・経度・高度を計算する。Open Notify の iss_now の天宮版。

出典: celestrak.org ／ 位置計算: python-sgp4 (SGP4/SDP4 軌道伝播)。
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests
from mcp.types import CallToolResult, TextContent
from sgp4.api import Satrec, jday

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=48274&FORMAT=JSON"
UA = {"User-Agent": "space-finder-mcp/0.11 (MCP; Tiangong live position)"}
TIANGONG_NORAD = 48274


def _compute_position(tle_line1: str, tle_line2: str) -> dict:
    """SGP4 で TLE から現在の TEME 位置を計算し、緯度経度高度に変換する。"""
    import math
    sat = Satrec.twoline2rv(tle_line1, tle_line2)
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second + now.microsecond / 1e6)
    e, r, v = sat.sgp4(jd, fr)  # r, v は km / km/s（TEME 直交座標）
    if e != 0:
        return {"error": f"SGP4 propagation error code {e}"}
    x, y, z = r
    re = 6378.137  # 地球半径 km
    lat = math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z)))
    lon = math.degrees(math.atan2(y, x))
    alt = math.sqrt(x * x + y * y + z * z) - re
    if lon > 180:
        lon -= 360
    if lon < -180:
        lon += 360
    speed = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return {"latitude": round(lat, 4), "longitude": round(lon, 4),
            "altitude_km": round(alt, 2), "speed_kms": round(speed, 3),
            "computed_utc": now.strftime("%Y-%m-%d %H:%M:%S")}


def tiangong_now() -> CallToolResult:
    """天宮（Tiangong）中国宇宙ステーションの現在位置を返す。

    例:「天宮の現在位置」「中国宇宙ステーションは今どこ？」
    CelesTrak の最新 TLE を SGP4 で伝播して現在の緯度・経度・高度・速度を計算。
    認証不要。content に表示用サマリ＋Googleマップリンク、structuredContent に JSON。

    Returns:
        CallToolResult: 表示用サマリ + JSON。
    """
    try:
        r = requests.get(TLE_URL, headers=UA, timeout=30)
        r.raise_for_status()
        rows = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"CelesTrak への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "celestrak.org"},
        )
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="天宮の軌道要素が見つかりませんでした。")],
            structuredContent={"total": 0},
        )
    row = rows[0]
    tle1 = row.get("TLE_LINE1", "")
    tle2 = row.get("TLE_LINE2", "")
    epoch = (row.get("EPOCH") or "")[:19]
    pos = _compute_position(tle1, tle2)
    if "error" in pos:
        return CallToolResult(
            content=[TextContent(type="text", text=f"位置計算に失敗しました: {pos['error']}")],
            structuredContent=pos,
        )
    lat, lon = pos["latitude"], pos["longitude"]
    area = _area_label(lat, lon)
    lines = [
        f"🛰 **天宮（Tiangong）中国宇宙ステーション 現在位置**（{pos['computed_utc']} UTC）",
        f"📍 緯度 {lat}° / 経度 {lon}°（{area}）",
        f"🛰 高度 約{pos['altitude_km']}km ・ 速度 約{round(pos['speed_kms']*3.6)}km/h（1周 約91分）",
        f"🗺 Googleマップ: https://www.google.com/maps?q={lat},{lon}&z=3",
        f"軌道要素エポック: {epoch} UTC（CelesTrak / NORAD 48274）",
        f"乗組員: 現在 3 名（天宮は運用中の常駐宇宙ステーション）",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"norad_id": TIANGONG_NORAD, "name": "Tiangong",
                           "epoch_utc": epoch, "position": pos,
                           "google_maps": f"https://www.google.com/maps?q={lat},{lon}&z=3"},
    )


def _area_label(lat: float, lon: float) -> str:
    """緯度経度から大まかな地球上のエリア名を返す（表示用の目安）。"""
    if 20 <= lat <= 46 and 100 <= lon <= 122:
        return "中国上空"
    if 34 <= lat <= 48 and 128 <= lon <= 146:
        return "日本上空"
    if -35 <= lat <= -10 and 110 <= lon <= 155:
        return "オーストラリア上空"
    if 5 <= lat <= 40 and 115 <= lon <= 155:
        return "西太平洋"
    if 10 <= lat <= 45 and -125 <= lon <= -65:
        return "北米上空"
    if -60 <= lat <= 60 and abs(lon) < 180:
        zone = "東半球" if lon >= 0 else "西半球"
        hemi = "北" if lat >= 0 else "南"
        return f"{hemi}{zone}"
    return "宇宙空間"

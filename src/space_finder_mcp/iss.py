"""ISS 現在位置 — Open Notify API（認証不要）。

Open Notify の iss-now.json から国際宇宙ステーションの現在の緯度経度を取得し、
Google マップ リンクとセットで返す。CelesTrak の sat_tle（軌道要素）とは異なり、
「いま ISS が地球上のどこを飛んでいるか」を直接返す。

出典: api.open-notify.org（Open Notify, 認証不要）
注: Open Notify は HTTP のみ（HTTPS は応答しない）。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

BASE = "http://api.open-notify.org"
UA = {"User-Agent": "space-finder-mcp/0.8 (MCP; Open Notify ISS)"}


def iss_now() -> CallToolResult:
    """ISS（国際宇宙ステーション）の現在位置を返し、Googleマップで表示できるURLを生成する。

    例:「ISSは今どこ?」「国際宇宙ステーションの現在位置」
    content に表示用サマリ＋Googleマップリンク、structuredContent に JSON（緯度経度）を返す。

    Returns:
        CallToolResult: ISS の現在位置情報。
    """
    try:
        r = requests.get(f"{BASE}/iss-now.json", headers=UA, timeout=20)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ISS 位置の取得に失敗しました（Open Notify は HTTP のみ対応）: {e}")],
            structuredContent={"error": str(e), "source": "api.open-notify.org"},
        )
    pos = d.get("iss_position", {})
    try:
        lat = float(pos.get("latitude"))
        lon = float(pos.get("longitude"))
    except (TypeError, ValueError):
        return CallToolResult(
            content=[TextContent(type="text", text="ISS 位置データの形式が不正です。")],
            structuredContent={"error": "bad position", "raw": d},
        )
    ts = d.get("timestamp")
    import datetime
    # utcfromtimestamp は Python 3.12 で非推奨（naive datetime を返す）。
    # タイムゾーンを明示した fromtimestamp を使う。
    tstr = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if ts else "?")

    # Google マップ リンク（衛星ビュー）
    gm_url = f"https://www.google.com/maps?q={lat},{lon}&z=3"
    # 地表の真下の点（ISS は約420km上空）
    lines = [
        f"🛰 **ISS（国際宇宙ステーション）現在位置**（{tstr}）",
        f"📍 緯度 {lat:.2f}°, 経度 {lon:.2f}°",
        f"🛰 高度 約420km / 速度 約27,700 km/h（地球1周 約90分）",
        f"🗺 Googleマップで見る: [ISS 現在位置を表示]({gm_url})",
        "",
        "🤖 【AIからのインテリジェントアドバイス】ISS は約90分で地球を1周します。",
        "5〜10分で大きく移動するため、観測や撮影の際は最新位置を再確認してください。",
        "出典: api.open-notify.org",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "latitude": lat, "longitude": lon,
            "timestamp": ts, "time_utc": tstr,
            "google_maps_url": gm_url,
            "altitude_km": 420, "speed_kmh": 27700, "source": "api.open-notify.org",
        },
    )

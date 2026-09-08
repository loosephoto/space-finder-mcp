"""NASA 火星探査ローバーの状況・天気（認証不要）。

Mars Weather API (mars.nasa.gov/rss/api) から、キュリオシティ(MSL) の火星天気・
状況（気温・気圧・風速・ソル・地球日付・季節）を認証不要で取得する。

※ NASA は Mars Rover Photos API（写真）をアーカイブ（廃止）済みのため写真は取得しない。
出典: mars.nasa.gov（Mars Weather API）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

# Mars Weather (認証不要)
MARS_WEATHER = "https://mars.nasa.gov/rss/api/"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# ローバー名 -> 日本語
ROVER_JA = {"curiosity": "キュリオシティ", "opportunity": "オポチュニティ",
            "spirit": "スピリット", "perseverance": "パーサヴィアランス"}
# ローバー名 -> 着陸地点（参照用）
LANDING = {
    "curiosity": "ゲール・クレーター（Gale Crater）",
    "perseverance": "ジェゼロ・クレーター（Jezero Crater）",
    "opportunity": "メリディアニ平原（Meridiani Planum）",
    "spirit": "グセフ・クレーター（Gusev Crater）",
}


# ローバー名 -> Mars Weather API の category（NASA はミッション識別子を使う）
# 現状 MSL(キュリオシティ) のみ天気フィードが稼働。mars2020 はフィードが空。
_ROVER_CATEGORY = {"curiosity": "msl", "perseverance": "mars2020"}


def _get_mars_weather(rover: str) -> dict:
    """Mars Weather API から指定ローバーの最新天気を取得（認証不要）。

    天気フィードが稼働していないローバーは空 dict を返す。
    """
    cat = _ROVER_CATEGORY.get(rover, rover)
    r = requests.get(MARS_WEATHER, params={
        "feed": "weather", "category": cat, "feedtype": "json", "ver": "1.0",
    }, headers=UA, timeout=25)
    r.raise_for_status()
    d = r.json()
    soles = d.get("soles", [])
    return soles[0] if soles else {}


def mars_rover_status(rover: str = "curiosity", show_photos: bool = True) -> CallToolResult:
    """NASA 火星探査ローバーの現在の状況・天気・最新写真を返す。

    「キュリオシティの現在の状況」「火星探査ローバーの今」「パーサヴィアランスの天気」
    など。火星の天気（気温・気圧・風速・ソル・地球日付・季節）を認証不要で取得。

    Args:
        rover: ローバー名（"curiosity"=キュリオシティ既定, "perseverance"=パーサヴィアランス）。
            天気フィードが稼働しているのは現状 MSL(キュリオシティ) のみ。
        show_photos: 保持（互換用。Mars Rover Photos API は廃止済みのため取得しない）。
    """
    rover = (rover or "curiosity").strip().lower()
    rover_ja = ROVER_JA.get(rover, rover)
    try:
        weather = _get_mars_weather(rover)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Mars Weather API への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "mars.nasa.gov/rss/api"},
        )

    lines = [f"🔴 **{rover_ja}（{rover}）火星探査ローバーの現在の状況**: 出典 mars.nasa.gov"]
    lines.append(f"📍 着陸地点: {LANDING.get(rover, '不明')}")
    weather_rec = {}
    if weather:
        sol = weather.get("sol")
        ter_date = weather.get("terrestrial_date")
        season = weather.get("season")
        atmo = weather.get("atmo_opacity")
        lines.append(f"🛸 **ソル {sol}**（火星日）・地球日付 {ter_date}")
        if season:
            lines.append(f"🌡 季節: {season}")
        lines.append(f"☀️ 天候: {atmo or '不明'}（大気の透明度）")
        for label, key, unit in [
            ("最高気温", "max_temp", "℃"), ("最低気温", "min_temp", "℃"),
            ("気圧", "pressure", "Pa"), ("風速", "wind_speed", "m/s"),
            ("風向", "wind_direction", ""),
        ]:
            v = weather.get(key)
            if v and str(v) != "--":
                lines.append(f"   {label}: {v} {unit}")
        weather_rec = {k: weather.get(k) for k in
                       ["sol", "terrestrial_date", "season", "min_temp", "max_temp",
                        "pressure", "wind_speed", "wind_direction", "atmo_opacity", "sunrise", "sunset"]}
    else:
        lines.append("⚠️ 天気データを取得できませんでした（このローバーの天気フィードが提供されていない可能性）。")

    lines.append("🤖 【AIからのインテリジェントアドバイス】ローバーの状況は地球の日付と火星の日（ソル）がずれる点に注意。気温・気圧は火星の季節変化を示し、天候（Sunny/Dusty等）が観測・走行計画の参考になります。")
    lines.append("出典: mars.nasa.gov（Mars Weather, 認証不要）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"rover": rover, "rover_ja": rover_ja, "landing": LANDING.get(rover),
                           "weather": weather_rec,
                           "source": "mars.nasa.gov/rss/api"},
    )

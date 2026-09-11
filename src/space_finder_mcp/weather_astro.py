"""天体観測に応用可能な世界の天気情報（Open-Meteo, 認証不要）。

Open-Meteo (api.open-meteo.com) は認証不要・無料(非商用1日10,000回)で、
全世界の任意地点の気象予報を提供する。天体観測に重要なパラメータ:
- 雲量 (cloud_cover) / 視程 (visibility) / 降水確率 / 天気コード
- 風速 / 湿度 / 昼夜判定 (is_day)
本ツールは「今夜・今後3日で天体観測に適した時間帯」を自動抽出し、
AIからのアドバイス形式で返す。
出典: open-meteo.com (CC BY 4.0)
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_FORECAST, ttl_cache, is_error_result
from .input_utils import as_float, as_int

API = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "space-finder-mcp/0.4 (MCP; Open-Meteo astronomy)"}


# 月相（Open-Meteo は 0=new 〜 0.5=full 〜 1=new の分数で返す）
def _moon_phase_ja(phase) -> str:
    """月相の分数(0-1)を日本語の月齢ステージに変換する。

    Open-Meteo の moon_phase は new moon=0, first quarter=0.25,
    full moon=0.5, last quarter=0.75, そして 1 に戻って new を表す。
    """
    if phase is None:
        return "不明"
    p = as_float(phase, None)
    if p is None:
        return "不明"
    # 照度（0=new 〜 100=満月）を基準にステージ判定
    illum = _moon_illumination(phase)
    if illum < 5:
        return "新月🌑（観測好機・月明かりなし）"
    if illum < 45:
        return "三日月〜半月🌒（月明かり弱・観測可）"
    if illum < 90:
        return "半月〜満月手前🌓（月明かりあり）"
    return "満月🌕（月明かり強・深宇宙天体は難）"


def _moon_illumination(phase) -> float:
    """月相の分数(0-1)を照度%に変換（0=new, 0.5=full）。"""
    if phase is None:
        return 0.0
    p = as_float(phase, 0.0)
    illum = abs(p - 0.5) * 2
    return round((1 - illum) * 100, 1)


def _moon_illumination_ja(phase) -> str:
    """月相の分数を照度の目安（%）に変換（0=new, 0.5=full）。"""
    return f"{_moon_illumination(phase)}%"


def _night_obs_windows(h: dict, daily: dict, max_cloud: float = 40.0,
                       max_wind: float = 15.0, min_vis: float = 10000.0) -> list[dict]:
    """夜間(is_day=0)かつ観測条件を満たす時間帯を抽出する。

    条件:
      - 夜間 (is_day == 0)
      - 雲量 <= max_cloud (%)
      - 風速 <= max_wind (km/h)
      - 視程 >= min_vis (m)
    """
    t = h["time"]; cloud = h["cloud_cover"]; vis = h["visibility"]
    wind = h.get("wind_speed_10m"); is_day = h.get("is_day"); pop = h.get("precipitation_probability")

    windows = []
    cur = None
    for i in range(len(t)):
        night = (is_day is None) or (is_day[i] == 0)
        c = cloud[i]
        w = wind[i] if wind is not None else 0.0
        v = vis[i] if vis is not None else min_vis
        popv = pop[i] if pop is not None else 0.0
        good = night and c <= max_cloud and w <= max_wind and v >= min_vis and popv <= 30
        if good and cur is None:
            cur = {"start": t[i], "hours": [], "min_cloud": c, "max_cloud": c}
        if good and cur is not None:
            cur["hours"].append(i)
            cur["min_cloud"] = min(cur["min_cloud"], c)
            cur["max_cloud"] = max(cur["max_cloud"], c)
            cur["end"] = t[i]
        elif not good and cur is not None:
            if len(cur["hours"]) >= 2:  # 最低2時間連続
                windows.append(cur)
            cur = None
    if cur is not None and len(cur["hours"]) >= 2:
        windows.append(cur)
    return windows


@ttl_cache(TTL_FORECAST, maxsize=64, skip_if=is_error_result)
def astronomy_weather(latitude: Optional[float] = None, longitude: Optional[float] = None,
                      place: Optional[str] = None, days: int = 3,
                      max_cloud: float = 40.0) -> CallToolResult:
    """天体観測に最適な時間帯を予報する（Open-Meteo, 認証不要・全世界対応）。

    指定した地点の今後数日間で「夜間かつ雲が少なく・視程が良い」時間帯を抽出し、
    観測のチャンスと注意点を AI からのアドバイス形式で返す。

    例:「東京で今夜天体観測できる?」「シドニーの晴れ予報」「富士山頂の雲量」
    場所は緯度経度(latitude/longitude)または地名(place)で指定。

    Args:
        latitude: 緯度（例 東京 35.68）。place 指定時は省略可。
        longitude: 経度（例 東京 139.69）。
        place: 地名（緯度経度より優先）。Open-Meteo のジオコーディングで解決。
        days: 予報日数（1〜7、既定 3）。
        max_cloud: 「観測可」とみなす雲量の上限%（既定 40）。
    """
    days = as_int(days, 3, 1, 7)
    max_cloud = as_float(max_cloud, 40.0, 0.0, 100.0)
    lat, lon = latitude, longitude
    if lat is None or lon is None:
        if not place:
            return CallToolResult(
                content=[TextContent(type="text", text="緯度経度 または 地名(place) のいずれかを指定してください。例: place='東京' または latitude=35.68, longitude=139.69")],
                structuredContent={"error": "location required"},
            )
        g = _geocode(place)
        if not g:
            return CallToolResult(
                content=[TextContent(type="text", text=f"地名 '{place}' を解決できませんでした。緯度経度を直接指定してください。")],
                structuredContent={"error": "geocode failed", "place": place},
            )
        lat, lon = g["latitude"], g["longitude"]
        place = g.get("name") or place

    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "cloud_cover,visibility,is_day,precipitation_probability,weather_code,wind_speed_10m",
        "daily": "sunrise,sunset,moonrise,moonset,moon_phase",
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        r = requests.get(API, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Open-Meteo への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "api.open-meteo.com"},
        )

    h = d.get("hourly", {})
    daily = d.get("daily", {})
    if not h:
        return CallToolResult(
            content=[TextContent(type="text", text="Open-Meteo から予報データを取得できませんでした。")],
            structuredContent={"error": "no hourly data"},
        )

    windows = _night_obs_windows(h, daily, max_cloud=max_cloud)
    name = place or f"({lat},{lon})"
    times = h["time"]

    # 月相・日月出没（観測可否の月明かり判断に使用）
    moon = None
    if daily.get("moon_phase"):
        dt_len = len(daily.get("time", [1]))
        moon = {
            "phase_fraction": daily["moon_phase"][0],
            "phase_ja": _moon_phase_ja(daily["moon_phase"][0]),
            "illumination_pct": _moon_illumination_ja(daily["moon_phase"][0]),
            "moonrise": (daily.get("moonrise") or [None] * dt_len)[0],
            "moonset": (daily.get("moonset") or [None] * dt_len)[0],
            "sunrise": (daily.get("sunrise") or [None] * dt_len)[0],
            "sunset": (daily.get("sunset") or [None] * dt_len)[0],
        }

    if not windows:
        lines = [f"🔭 **{name}** 今後{days}日間: 条件を満たす夜間の観測時間帯は見つかりませんでした（雲量≤{max_cloud:g}%、風速≤15km/h）。"]
        # 最も雲が少ない夜間を1つ提示
        night_clouds = [(times[i], h["cloud_cover"][i]) for i in range(len(times))
                        if h.get("is_day", [1]*len(times))[i] == 0]
        if night_clouds:
            best = min(night_clouds, key=lambda x: x[1])
            lines.append(f"最もマシな時間帯: {best[0]} 頃 雲量 {best[1]}%")
        if moon:
            lines.append(f"🌙 今夜の月: {moon['phase_ja']}（照度 {moon['illumination_pct']}） 月の出 {moon['moonrise']} / 月の入り {moon['moonset']}")
        lines.append("🤖 【AIからのインテリジェントアドバイス】今夜は観測に厳しい条件です。雲の少ない日を改めて確認するか、プラネタリウムや月面観察など曇天でも楽しめる対象を検討してください。")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structuredContent={"place": name, "lat": lat, "lon": lon, "days": days,
                               "max_cloud": max_cloud, "moon": moon, "windows": [], "note": "no suitable window"},
        )

    lines = [f"🔭 **{name}** 今後{days}日間の天体観測チャンス:"]
    if moon:
        lines.append(f"🌙 今夜の月: {moon['phase_ja']}（照度 {moon['illumination_pct']}） 月の出 {moon['moonrise']} / 月の入り {moon['moonset']}")
    for i, w in enumerate(windows[:6], 1):
        start = w["start"][5:16].replace("T", " ")
        end = w["end"][5:16].replace("T", " ")
        hours_n = len(w["hours"])
        lines.append(f"{i}. 🕐 {start} 〜 {end}（約{hours_n}時間）")
        lines.append(f"   雲量 {w['min_cloud']}〜{w['max_cloud']}%")
    lines.append("🤖 【AIからのインテリジェントアドバイス】上記の時間帯は夜間で雲が少なく観測に適しています。雲量が低いほど深宇宙天体(星雲・銀河)の観測に最適です。月明かりや光源の少ない観測地を選ぶとより鮮明に見えます。")
    lines.append("出典: open-meteo.com (CC BY 4.0)")

    # structuredContent: 各時間帯の詳細をJSONで
    details = []
    for w in windows[:10]:
        for i in w["hours"]:
            details.append({
                "time": times[i],
                "cloud_cover": h["cloud_cover"][i],
                "visibility_m": h["visibility"][i],
                "wind_speed_kmh": h.get("wind_speed_10m", [None]*len(times))[i],
                "precip_prob": h.get("precipitation_probability", [None]*len(times))[i],
                "weather_code": h.get("weather_code", [None]*len(times))[i],
            })
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"place": name, "lat": lat, "lon": lon, "days": days,
                           "max_cloud": max_cloud, "moon": moon, "windows_count": len(windows),
                           "windows": [{"start": w["start"], "end": w["end"], "hours": len(w["hours"]),
                                        "min_cloud": w["min_cloud"], "max_cloud": w["max_cloud"]} for w in windows[:10]],
                           "hourly": details},
    )


# 観測地の正確な緯度経度（ジオコーディングが誤る/不正確な著名天体観測地）
_KNOWN_COORDS: dict[str, dict] = {
    # 富士山頂（静岡/山梨県境, 標高3776m）
    "富士山": {"latitude": 35.3606, "longitude": 138.7274, "name": "富士山頂 (Mt. Fuji, 3776m)"},
    "富士山頂": {"latitude": 35.3606, "longitude": 138.7274, "name": "富士山頂 (Mt. Fuji, 3776m)"},
    "富士北麓": {"latitude": 35.4656, "longitude": 138.6083, "name": "富士北麓 (Fuji North Base)"},
    # 日本の代表的な星空観測地
    "美星": {"latitude": 34.667, "longitude": 133.55, "name": "美星天文台 (Bisei, 岡山)"},
    "美星天文台": {"latitude": 34.667, "longitude": 133.55, "name": "美星天文台 (Bisei, 岡山)"},
    "高村荘": {"latitude": 35.25, "longitude": 137.0, "name": "高村荘 (愛知)"},
    "阿智村": {"latitude": 35.444, "longitude": 137.742, "name": "阿智村 (長野, 日本一の星空)"},
    "南阿蘇": {"latitude": 32.80, "longitude": 131.05, "name": "南阿蘇 (熊本)"},
    "石垣島": {"latitude": 24.4, "longitude": 124.2, "name": "石垣島 (沖縄)"},
    "波照間島": {"latitude": 24.06, "longitude": 123.77, "name": "波照間島 (沖縄, 日本最南端)"},
    # 世界の主要天文台
    "マウナケア": {"latitude": 19.820, "longitude": -155.468, "name": "マウナケア天文台 (Mauna Kea, ハワイ)"},
    "サンペドロデアタカマ": {"latitude": -22.95, "longitude": -68.18, "name": "アタカマ砂漠 (ALMA近傍, チリ)"},
    "アタカマ": {"latitude": -22.95, "longitude": -68.18, "name": "アタカマ砂漠 (チリ)"},
    "ラパルマ": {"latitude": 28.76, "longitude": -17.89, "name": "ラ・パルマ島 (カナリア諸島)"},
    "ラ・パルマ": {"latitude": 28.76, "longitude": -17.89, "name": "ラ・パルマ島 (カナリア諸島)"},
    "アマチ": {"latitude": 24.59, "longitude": -70.19, "name": "アタカマ (アマチ山)"},
    "ナミブ": {"latitude": -23.0, "longitude": 16.0, "name": "ナミブ砂漠 (ナミビア)"},
    "ノース": {"latitude": 24.0, "longitude": -70.0, "name": "ノース, チリ"},
}


# 日本語地名 → 英語名（Open-Meteo のジオコーディングは英語に強い）
_JA_PLACES: dict[str, str] = {
    "東京": "Tokyo", "大阪": "Osaka", "名古屋": "Nagoya", "京都": "Kyoto",
    "札幌": "Sapporo", "福岡": "Fukuoka", "仙台": "Sendai", "広島": "Hiroshima",
    "沖縄": "Okinawa", "那覇": "Naha", "横浜": "Yokohama", "神戸": "Kobe",
    "富士山": "Mount Fuji", "軽井沢": "Karuizawa", "富士宮": "Fujinomiya",
    "ニューヨーク": "New York", "ロンドン": "London", "パリ": "Paris",
    "北京": "Beijing", "上海": "Shanghai", "ソウル": "Seoul",
    "シドニー": "Sydney", "ロサンゼルス": "Los Angeles", "サンフランシスコ": "San Francisco",
    "シンガポール": "Singapore", "バンコク": "Bangkok", "ハワイ": "Hawaii",
    "マウナケア": "Mauna Kea", "チリ": "Chile", "アタカマ": "Atacama",
    "リオデジャネイロ": "Rio de Janeiro", "モスクワ": "Moscow", "ケープタウン": "Cape Town",
    "ホノルル": "Honolulu", "アテネ": "Athens", "ローマ": "Rome",
    "アンカレジ": "Anchorage", "アブダビ": "Abu Dhabi", "ドバイ": "Dubai",
    "バンクーバー": "Vancouver", "トロント": "Toronto", "メキシコシティ": "Mexico City",
    "ブラジリア": "Brasilia", "ブエノスアイレス": "Buenos Aires", "リマ": "Lima",
    "カイロ": "Cairo", "ナイロビ": "Nairobi", "ヨハネスブルグ": "Johannesburg",
    "デリー": "New Delhi", "ムンバイ": "Mumbai", "ジャカルタ": "Jakarta",
    "マニラ": "Manila", "クアラルンプール": "Kuala Lumpur", "台北": "Taipei",
    "香港": "Hong Kong", "ソウル": "Seoul", "平壌": "Pyongyang", "ウラジオストク": "Vladivostok",
    "ストックホルム": "Stockholm", "オスロ": "Oslo", "ヘルシンキ": "Helsinki",
    "コペンハーゲン": "Copenhagen", "ベルリン": "Berlin", "ウィーン": "Vienna",
    "チューリッヒ": "Zurich", "ジュネーブ": "Geneva", "アムステルダム": "Amsterdam",
    "ブリュッセル": "Brussels", "マドリード": "Madrid", "バルセロナ": "Barcelona",
    "リスボン": "Lisbon", "アテネ": "Athens", "イスタンブール": "Istanbul",
    "モスクワ": "Moscow", "サンクトペテルブルク": "Saint Petersburg",
    "オークランド": "Auckland", "ウェリントン": "Wellington",
    "ホノルル": "Honolulu", "アンカレッジ": "Anchorage",
}


@ttl_cache(TTL_DAILY, maxsize=256, skip_if=lambda v: v is None)
def _geocode(place: str) -> Optional[dict]:
    """Open-Meteo のジオコーディングAPIで地名を緯度経度に解決する。

    日本語地名は英語名へマッピングして解決を試みる（Open-Meteo は英語に強い）。
    """
    q = place.strip()
    # 既知の観測地は正確な緯度経度を優先（ジオコーディングの誤りを回避）
    if q in _KNOWN_COORDS:
        return dict(_KNOWN_COORDS[q])
    # 日本語→英語変換
    if q in _JA_PLACES:
        q = _JA_PLACES[q]
    for name in (q, place.strip()):
        try:
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": name, "count": 1, "format": "json"},
                             headers=UA, timeout=20)
            r.raise_for_status()
            results = r.json().get("results") or []
            if results:
                it = results[0]
                return {"latitude": it["latitude"], "longitude": it["longitude"],
                        "name": f"{it.get('name')}, {it.get('country')}"}
        except requests.RequestException:
            continue
    # Open-Meteo で解決できなかった地名（施設・複合地名・曖昧な都市）は Nominatim へ
    return _geocode_nominatim(place)


def _geocode_nominatim(place: str) -> Optional[dict]:
    """Open-Meteo で解決できなかった地名・施設名・天文台名を Nominatim(OSM) で解決する。

    Open-Meteo は都市名に強いが、複合地名（"Santiago de Chile"）・同名都市の曖昧さ
    （"Greenwich" は米国に多数）・施設/天文台名（"グリニッジ天文台"）に弱い。
    Nominatim は建物・施設・日本語まで解決できるため、フォールバックとして使う。
    利用条件 (1 req/s) を守るため、前回呼び出しから 1.1s 以上あけて実行する。
    """
    import time as _time
    _last = getattr(_geocode_nominatim, "_last_call", 0.0)
    wait = _last + 1.1 - _time.time()
    if wait > 0:
        _time.sleep(wait)
    _geocode_nominatim._last_call = _time.time()
    q = place.strip()
    # Nominatim は日本語/英語どちらも処理できるが、Open-Meteo と同じ英語変換を先に試す
    query = _JA_PLACES.get(q, q)
    ua = {"User-Agent": "space-finder-mcp/0.22 (MCP; Open-Meteo/Nominatim geocode)"}
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": query, "format": "json", "limit": 1,
                                 "addressdetails": 1},
                         headers=ua, timeout=20)
        r.raise_for_status()
        res = r.json()
        if res:
            it = res[0]
            disp = it.get("display_name", it.get("name", q))
            return {"latitude": float(it["lat"]), "longitude": float(it["lon"]),
                    "name": disp}
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None

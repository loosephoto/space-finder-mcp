"""天体観測に応用可能な世界の天気情報（Open-Meteo, 認証不要）。

Open-Meteo (api.open-meteo.com) は認証不要・無料(非商用1日10,000回)で、
全世界の任意地点の気象予報を提供する。天体観測に重要なパラメータ:
- 雲量 (cloud_cover) / 視程 (visibility) / 降水確率 / 天気コード
- 風速 / 湿度 / 昼夜判定 (is_day)
- 太陽高度（薄明の明るい時間帯を観測時間帯から除く。Open-Meteo は hourly に
  太陽高度を返さないため、日付・経度・UTCオフセットから解析的に推定する）
本ツールは「今夜・今後3日で天体観測に適した時間帯」を自動抽出し、
AIからのアドバイス形式で返す。

日本国内の地点では、加えて気象庁の雨雲・降水画像（解析雨量・降水短時間予報の
パネルと、ナウキャスト＝雨雲の動き）を content に添付し、時刻や凡例・雷の有無を
structuredContent.weather_rain と structuredContent.figure（図の注記）に返す
（include_rain=False で無効化。取得失敗時は画像なしで予報のみを返す）。
図の描画は jma_rain.py（降水は雲量そのものではない旨の注記を必ず付ける）。
出典: open-meteo.com (CC BY 4.0) ／ 気象庁 ナウキャスト・解析雨量 (jma.go.jp)
"""
from __future__ import annotations

from typing import Optional

import base64
import datetime
import math

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import TTL_DAILY, TTL_FORECAST, ttl_cache, is_error_result
from .img_common import figure_text_block, media_link_line, save_output
from .input_utils import as_float, as_int
from .jma_rain import rain_images

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
        return "半月〜満月前🌔（月明かりあり）"
    return "満月🌕（月明かり強・深宇宙天体は難）"


def _moon_illumination(phase) -> float:
    """月相の分数(0-1)を照度%に変換（0=new, 0.5=full）。

    Open-Meteo の moon_phase は「朔望月の進み具合（0=新月, 0.25=上弦,
    0.5=満月, 0.75=下弦）」であって照度そのものではない。太陽との離角は
    360°×p なので、照度は (1 - cos(離角)) / 2 で求める。
    実測（2026-10-23・東京）: p=0.391 → 88.7%。timeanddate の同日の照度は
    88.6%、Skyfield(DE421) による同日 21:15 JST の照度は 91.3% で整合する。
    以前の線形近似 (1 - |p-0.5|×2) は同じ p を 78.2% と 10 ポイント以上低く
    見積もり、月明かりの判断（深宇宙天体の可否）を誤らせていた。
    """
    if phase is None:
        return 0.0
    p = as_float(phase, None)
    if p is None:
        return 0.0
    return round((1.0 - math.cos(2.0 * math.pi * p)) / 2.0 * 100.0, 1)


def _moon_illumination_ja(phase) -> str:
    """月相の分数を照度の目安（%）に変換（0=new, 0.5=full）。"""
    return f"{_moon_illumination(phase)}%"


def _parse_local(s):
    """'2026-10-23T17:00' 形式（Open-Meteo の時刻文字列）を datetime にする。"""
    if not s:
        return None
    try:
        dtm = datetime.datetime.fromisoformat(str(s).strip().replace("Z", ""))
    except ValueError:
        return None
    return dtm.replace(tzinfo=None)


def _fmt_day_hm(s) -> str:
    """'2026-10-24T03:30' → '10-24 03:30'（観測時間帯の表示と同じ書式）。"""
    dtm = _parse_local(s)
    return dtm.strftime("%m-%d %H:%M") if dtm else "—"


def _day_of_year(d) -> int:
    return d.timetuple().tm_yday


def _sun_declination_deg(d, hour_local: float = 12.0) -> float:
    """太陽赤緯(度)。低精度式（Spencer 1971。誤差 < 0.02°）。"""
    gamma = 2.0 * math.pi / 365.0 * (_day_of_year(d) - 1 + (hour_local - 12.0) / 24.0)
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2.0 * gamma) + 0.000907 * math.sin(2.0 * gamma)
            - 0.002697 * math.cos(3.0 * gamma) + 0.00148 * math.sin(3.0 * gamma))
    return math.degrees(decl)


def _equation_of_time_min(d) -> float:
    """均時差(分)。低精度式（NOAA。誤差 < 0.5 分 = 時角 0.13°）。"""
    gamma = 2.0 * math.pi / 365.0 * (_day_of_year(d) - 1)
    return 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                     - 0.014615 * math.cos(2.0 * gamma) - 0.040849 * math.sin(2.0 * gamma))


def sun_altitude_deg(when, latitude, longitude, tz_offset_seconds) -> Optional[float]:
    """太陽高度(度)を解析的に求める（Open-Meteo は hourly に太陽高度を返さない）。

    `sun_elevation` を要求すると API は 400 で拒否する（実測 2026-10-23:
    "Cannot initialize ... from invalid String value sun_elevation"）。時刻と
    緯度経度だけでは薄明の明るい時間帯を切り分けられない（実測: 東京
    2026-10-23 は日の入り 16:56 に対し天文薄明終 18:22 で 86 分の差がある）ため、
    日付・経度・UTCオフセットから視太陽時を出して時角に直し、
    sin(高度) = sinφ·sinδ + cosφ·cosδ·cos(時角) で求める。
    赤緯 δ は Spencer(1971)、均時差は NOAA の低精度式（太陽高度で 0.2° 未満の誤差。
    Skyfield/DE421 との実測比較は tests/test_weather_astro.py を参照）。
    推定できないとき（時刻が不正・緯度経度か UTC オフセットが無い）は None。
    """
    dtm = _parse_local(when)
    if dtm is None or latitude is None or longitude is None or tz_offset_seconds is None:
        return None
    try:
        phi = math.radians(float(latitude))
        lon = float(longitude)
        tz_h = float(tz_offset_seconds) / 3600.0
    except (TypeError, ValueError):
        return None
    clock_h = dtm.hour + dtm.minute / 60.0 + dtm.second / 3600.0
    # 視太陽時 = 地方平均時 + 均時差, 地方平均時 = 地方時 - UTCオフセット + 経度/15
    ast_h = (clock_h - tz_h + lon / 15.0 + _equation_of_time_min(dtm.date()) / 60.0)
    hour_angle = math.radians(15.0 * (ast_h - 12.0))
    decl = math.radians(_sun_declination_deg(dtm.date(), clock_h))
    sin_alt = (math.sin(phi) * math.sin(decl)
               + math.cos(phi) * math.cos(decl) * math.cos(hour_angle))
    return round(math.degrees(math.asin(max(-1.0, min(1.0, sin_alt)))), 1)


def sun_altitudes(times, latitude, longitude, tz_offset_seconds) -> list:
    """時刻の配列に対する太陽高度(度)の配列（推定できない要素は None）。"""
    return [sun_altitude_deg(t, latitude, longitude, tz_offset_seconds) for t in times]


def _is_dark(i: int, sun_alts: Optional[list], h: dict, sun_alt_max: float) -> bool:
    """その時刻が観測に十分暗いか（薄明を除く）。

    太陽高度が推定できていればそれを基準にし、推定できない地点（日の出/日の入りが
    欠ける極昼・極夜、時刻の欠測など）では is_day だけで判定する。
    """
    if sun_alts is not None and i < len(sun_alts) and sun_alts[i] is not None:
        return sun_alts[i] <= sun_alt_max
    is_day = h.get("is_day")
    return (is_day is None) or (i < len(is_day) and is_day[i] == 0)


def _sun_alt_range_ja(w: dict) -> str:
    """観測時間帯の太陽高度の範囲を「 ／ 太陽高度 -30〜-18°」の形で返す。"""
    lo, hi = w.get("min_sun_alt"), w.get("max_sun_alt")
    if lo is None or hi is None:
        return ""
    return f" ／ 太陽高度 {lo:g}〜{hi:g}°"


def _moon_line(moon: dict) -> str:
    """「今夜の月」の1行（月の出没は日付つき。月没は翌日になることが多い）。"""
    return (f"🌙 今夜の月: {moon['phase_ja']}（照度 {moon['illumination_pct']}）"
            f" 月の出 {_fmt_day_hm(moon.get('moonrise'))} /"
            f" 月の入り {_fmt_day_hm(moon.get('moonset'))}")


def _night_moon(daily: dict) -> Optional[dict]:
    """今夜（第1日）の月相と月の出没を返す。

    Open-Meteo の daily は「その暦日の中での月の出/月没」を返すため、
    daily.moonset[0] は既に終わった「その日未明の月没」であることが多い
    （実測: 東京 2026-10-23 は daily.moonset[0]=10-23T02:26・日の入り 16:56 で、
    今夜の月没は翌日 10-24T03:30）。日没より後の最初の月没を「今夜の月の入り」
    として選ぶ（月の出は当日の値をそのまま使う）。
    """
    if not daily.get("moon_phase"):
        return None
    n = len(daily.get("time") or [1])
    rises = list(daily.get("moonrise") or [None] * n)
    sets = list(daily.get("moonset") or [None] * n)
    sunsets = list(daily.get("sunset") or [None] * n)
    sunrises = list(daily.get("sunrise") or [None] * n)
    ref = _parse_local(sunsets[0] if sunsets else None)
    moonset, moonset_next_day = (sets[0] if sets else None), False
    if ref is not None:
        for j, s in enumerate(sets):
            dtm = _parse_local(s)
            if dtm is not None and dtm > ref:
                moonset, moonset_next_day = s, (j > 0)
                break
    return {
        "phase_fraction": daily["moon_phase"][0],
        "phase_ja": _moon_phase_ja(daily["moon_phase"][0]),
        "illumination_pct": _moon_illumination_ja(daily["moon_phase"][0]),
        "moonrise": rises[0] if rises else None,
        "moonset": moonset,
        "moonset_next_day": moonset_next_day,
        "sunrise": sunrises[0] if sunrises else None,
        "sunset": sunsets[0] if sunsets else None,
    }


def _night_obs_windows(h: dict, daily: dict, max_cloud: float = 40.0,
                       max_wind: float = 15.0, min_vis: float = 10000.0,
                       sun_alts: Optional[list] = None,
                       sun_alt_max: float = -15.0) -> list[dict]:
    """暗く（薄明を除く）観測条件を満たす時間帯を抽出する。

    条件:
      - 太陽高度 <= sun_alt_max（推定できない場合は is_day == 0）
      - 雲量 <= max_cloud (%)
      - 風速 <= max_wind (km/h)
      - 視程 >= min_vis (m)
      - 降水確率 <= 30 (%)
    """
    t = h["time"]; cloud = h["cloud_cover"]; vis = h["visibility"]
    wind = h.get("wind_speed_10m"); pop = h.get("precipitation_probability")

    windows = []
    cur = None
    for i in range(len(t)):
        sa = sun_alts[i] if (sun_alts is not None and i < len(sun_alts)) else None
        dark = _is_dark(i, sun_alts, h, sun_alt_max)
        c = cloud[i]
        w = wind[i] if wind is not None else 0.0
        v = vis[i] if vis is not None else min_vis
        popv = pop[i] if pop is not None else 0.0
        good = dark and c <= max_cloud and w <= max_wind and v >= min_vis and popv <= 30
        if good and cur is None:
            cur = {"start": t[i], "hours": [], "min_cloud": c, "max_cloud": c,
                   "min_sun_alt": sa, "max_sun_alt": sa}
        if good and cur is not None:
            cur["hours"].append(i)
            cur["min_cloud"] = min(cur["min_cloud"], c)
            cur["max_cloud"] = max(cur["max_cloud"], c)
            if sa is not None:
                cur["min_sun_alt"] = sa if cur["min_sun_alt"] is None else min(cur["min_sun_alt"], sa)
                cur["max_sun_alt"] = sa if cur["max_sun_alt"] is None else max(cur["max_sun_alt"], sa)
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
                      max_cloud: float = 40.0, sun_alt_max: float = -15.0,
                      include_rain: bool = True) -> CallToolResult:
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
        sun_alt_max: 観測時間帯とみなす太陽高度（度）の上限。既定 -15（薄明の
            明るい時間帯を除外）。-18 で天文薄明＝完全な暗夜のみ、-6 なら市民
            薄明が終わった後から。Open-Meteo は hourly に太陽高度を返さないため、
            日付・経度・UTCオフセットから解析的に推定する（極昼・極夜などで
            推定できない地点は is_day のみで判定）。
        include_rain: 日本国内の地点に限り、気象庁の雨雲・降水画像
            （解析雨量・降水短時間予報のパネル＋ナウキャスト＝雨雲の動き）を
            添付する（既定 True。取得失敗時は添付なしで続行）。
            ⚠️ structuredContent.figure.notes は要約せずそのまま引用すること。
            （添付した雨雲・降水画像は content に「🖼️ [◯◯を開く](URL)」のリンク付きで出します。
            インライン画像を描画しないハーネスでは、回答にこのリンクをそのまま提示してください。）
    """
    days = as_int(days, 3, 1, 7)
    max_cloud = as_float(max_cloud, 40.0, 0.0, 100.0)
    sun_alt_max = as_float(sun_alt_max, -15.0, -30.0, -1.0)
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
        r = requests.get(API, params=params, headers=UA, timeout=(30, 30))
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

    times = h["time"]
    sun_alts = sun_altitudes(times, lat, lon, d.get("utc_offset_seconds"))
    sun_alt_source = "sun_altitude" if any(a is not None for a in sun_alts) else "is_day"
    windows = _night_obs_windows(h, daily, max_cloud=max_cloud,
                                 sun_alts=sun_alts, sun_alt_max=sun_alt_max)
    name = place or f"({lat},{lon})"

    # 気象庁の雨雲・降水画像（日本国内のみ・取得失敗時は省略）
    image_blocks = []
    image_sc = {"source": "open-meteo.com (CC BY 4.0)"}
    if include_rain:
        _obs = [w["start"] for w in windows[:6]] if windows else []
        _ri = rain_images(lat, lon, name, _obs)
        if _ri:
            image_sc = {"weather_rain": _ri["payload"], "figure": _ri["figure"]}
            image_blocks.append(TextContent(type="text", text=figure_text_block(_ri["figure"])))
            for _img in _ri["images"]:
                _p = save_output(_img["bytes"], "astronomy_weather_jma", "jpg")
                _link = media_link_line(_img["label"],
                                        url=_img["page_url"], path=_p, kind="image")
                # 単一改行で繋ぐと Markdown が同一段落に畳み込み、画像を連続で
                # 返したときに caption とリンクが 1 行に融合する（クライアント描画で
                # 実測）。空行で段落を分け、末尾にも改行を残して次のブロックと
                # 連結されないようにする。
                _cap = "🌧 " + _img["label"] + (chr(10) + chr(10) + _link if _link else "")
                _img["path"] = _p
                image_blocks.append(TextContent(type="text", text=_cap + chr(10)))
                image_blocks.append(ImageContent(
                    type="image",
                    data=base64.b64encode(_img["bytes"]).decode("ascii"),
                    mimeType="image/jpeg", altText=_img["alt"]))
            # 保存した実パスと出典URLを structuredContent にも残す（LLM が再参照できるように）
            image_sc["images"] = [{"id": i["id"], "label": i["label"],
                                   "path": i.get("path"), "page_url": i.get("page_url")}
                                  for i in _ri["images"]]
            if image_sc["images"]:
                image_sc["image_path"] = image_sc["images"][0].get("path")
            image_sc.setdefault("source", "open-meteo.com (CC BY 4.0) + JMA")

    # 月相・月の出没（観測可否の月明かり判断に使用。月没は日没後の最初の値を選ぶ）
    moon = _night_moon(daily)

    if not windows:
        lines = [f"🔭 **{name}** 今後{days}日間: 条件を満たす観測時間帯は見つかりませんでした（太陽高度≤{sun_alt_max:g}°・雲量≤{max_cloud:g}%・風速≤15km/h）。"]
        # 最も雲が少ない「暗い」時間帯を1つ提示
        night_clouds = [(times[i], h["cloud_cover"][i]) for i in range(len(times))
                        if _is_dark(i, sun_alts, h, sun_alt_max)]
        if night_clouds:
            best = min(night_clouds, key=lambda x: x[1])
            lines.append(f"最もマシな時間帯: {_fmt_day_hm(best[0])} 頃 雲量 {best[1]}%")
        if moon:
            lines.append(_moon_line(moon))
        lines.append("🤖 【AIからのインテリジェントアドバイス】今夜は観測に厳しい条件です。雲の少ない日を改めて確認するか、プラネタリウムや月面観察など曇天でも楽しめる対象を検討してください。")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))] + image_blocks,
            structuredContent={"place": name, "lat": lat, "lon": lon, "days": days,
                               "max_cloud": max_cloud, "sun_alt_max": sun_alt_max,
                               "sun_alt_source": sun_alt_source,
                               "moon": moon, "windows": [], "note": "no suitable window", **image_sc},
        )

    lines = [f"🔭 **{name}** 今後{days}日間の天体観測チャンス（太陽高度 ≤ {sun_alt_max:g}° の暗い時間帯のみ）:"]
    if moon:
        lines.append(_moon_line(moon))
    for i, w in enumerate(windows[:6], 1):
        start = w["start"][5:16].replace("T", " ")
        end = w["end"][5:16].replace("T", " ")
        hours_n = len(w["hours"])
        lines.append(f"{i}. 🕐 {start} 〜 {end}（約{hours_n}時間）")
        lines.append(f"   雲量 {w['min_cloud']}〜{w['max_cloud']}%" + _sun_alt_range_ja(w))
    lines.append("🤖 【AIからのインテリジェントアドバイス】上記の時間帯は薄明を除いた暗い時間帯で雲が少なく観測に適しています。雲量が低いほど深宇宙天体(星雲・銀河)の観測に最適です。月明かりや光源の少ない観測地を選ぶとより鮮明に見えます。")
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
                "sun_alt_deg": sun_alts[i] if i < len(sun_alts) else None,
            })
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))] + image_blocks,
        structuredContent={"place": name, "lat": lat, "lon": lon, "days": days,
                           "max_cloud": max_cloud, "sun_alt_max": sun_alt_max,
                           "sun_alt_source": sun_alt_source,
                           "moon": moon, "windows_count": len(windows),
                           "windows": [{"start": w["start"], "end": w["end"], "hours": len(w["hours"]),
                                        "min_cloud": w["min_cloud"], "max_cloud": w["max_cloud"],
                                        "min_sun_alt": w["min_sun_alt"], "max_sun_alt": w["max_sun_alt"]}
                                       for w in windows[:10]],
                           "hourly": details, **image_sc},
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
                             headers=UA, timeout=(20, 20))
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
                         headers=ua, timeout=(20, 20))
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

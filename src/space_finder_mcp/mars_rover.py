"""NASA 火星探査ローバーの状況・天気・現在地マップ（認証不要）。

- mars_rover_status: Mars Weather API (mars.nasa.gov/rss/api) から火星天気・状況を取得。
- mars_rover_location_map: NASA MMGIS + Trek WMTS でローバー現在地を火星地図中心に示した
  画像を合成。走行経路・着陸地点・現在地(緯度経度)を描画。

※ NASA は Mars Rover Photos API（写真）をアーカイブ（廃止）済みのため写真は取得しない。
出典: mars.nasa.gov（Mars Weather / MMGIS / Trek WMTS, すべて認証不要）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

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


# =====================================================================
# 火星ローバー現在地マップ（NASA MMGIS + Trek WMTS 合成）
# =====================================================================
import base64
import concurrent.futures
import io
import math
import os

from PIL import Image, ImageDraw, ImageFont

# ローバー名 -> MMGIS ミッション識別子 / 日本語 / 着陸地点(度)
_MMGIS = {
    "perseverance": {"id": "M20", "land_ja": "ジェゼロ・クレーター"},
    "curiosity": {"id": "MSL", "land_ja": "ゲール・クレーター"},
}
# 火星地図タイル (NASA Trek WMTS, 等角図法・Vikingカラー)
_MARS_TILE = ("https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/"
              "1.0.0/default/default028mm/{z}/{row}/{col}.jpg")
# ズーム別の最大タイル (Viking層は z0..z7)
_MARS_MAXZOOM = 7


def _get_rover_waypoint(rover: str) -> Optional[dict]:
    """MMGIS からローバー現在地(最終waypoint)を取得。{lat, lon, sol, dist_km, rmc}"""
    mid = _MMGIS[rover]["id"]
    url = f"https://mars.nasa.gov/mmgis-maps/{mid}/Layers/json/{mid}_waypoints_current.json"
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        f = r.json().get("features", [{}])[0].get("properties", {})
        return {
            "lat": float(f["lat"]), "lon": float(f["lon"]),
            "sol": f.get("sol"), "dist_km": f.get("dist_km"),
            "rmc": f.get("RMC"), "note": f.get("Note", ""),
        }
    except (requests.RequestException, KeyError, ValueError) as e:
        return None


def _get_rover_route(rover: str) -> Optional[list]:
    """MMGIS からローバーの走行経路(全waypoint: (lon,lat) 列)を取得。"""
    mid = _MMGIS[rover]["id"]
    url = f"https://mars.nasa.gov/mmgis-maps/{mid}/Layers/json/{mid}_waypoints.json"
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        return [(f["properties"]["lon"], f["properties"]["lat"])
                for f in r.json().get("features", [])
                if "lat" in f["properties"] and "lon" in f["properties"]]
    except (requests.RequestException, KeyError, ValueError):
        return None


def _fetch_tiles_parallel(center_lon, center_lat, span_deg, zoom, max_workers=16):
    """等角図法で中心・画角を覆うタイルを並列取得。
    ローバーを常に中心に据えるため、中心タイル＋必要マージンを含む範囲を取得する。
    返り: (モザイクImage, 中心global px, モザイク左上global px, cols, rows)
    """
    cols = 2 ** (zoom + 1); rows = 2 ** zoom
    W = cols * 256; H = rows * 256
    # 中心のグローバル画素座標（ローバーがここに来る）
    xc = (center_lon + 180) / 360.0 * W
    yc = (90 - center_lat) / 180.0 * H
    # 画角(度) → 画素幅。正方形表示
    span_px = span_deg / (360.0 / W)
    # 中心タイル
    cc0 = int(xc // 256); rr0 = int(yc // 256)
    # 中心を覆うのに必要なタイル範囲：span_px を超えるよう両側へ広げる
    half_tiles = int(math.ceil((span_px / 2) / 256.0)) + 1  # +1 マージン
    c_lo = cc0 - half_tiles; c_hi = cc0 + half_tiles
    r_lo = rr0 - half_tiles; r_hi = rr0 + half_tiles
    # クランプ
    c_lo = max(0, c_lo); r_lo = max(0, r_lo)
    c_hi = min(cols - 1, c_hi); r_hi = min(rows - 1, r_hi)
    tiles = [(rr, cc) for rr in range(r_lo, r_hi + 1) for cc in range(c_lo, c_hi + 1)]

    def _one(tc):
        rr, cc = tc
        try:
            r = requests.get(_MARS_TILE.format(z=zoom, row=rr, col=cc), headers=UA, timeout=20)
            return rr, cc, (r.content if r.status_code == 200 else None)
        except requests.RequestException:
            return rr, cc, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, tiles))
    cw = c_hi - c_lo + 1; rh = r_hi - r_lo + 1
    mosaic = Image.new("RGB", (cw * 256, rh * 256), (40, 40, 50))
    for rr, cc, data in results:
        if data:
            try:
                mosaic.paste(Image.open(io.BytesIO(data)).convert("RGB"),
                             ((cc - c_lo) * 256, (rr - r_lo) * 256))
            except Exception:
                pass
    # モザイク内での中心座標(px)
    center_px = (xc - c_lo * 256, yc - r_lo * 256)
    return mosaic, center_px, (c_lo * 256, r_lo * 256), cols, rows


def _mars_font(sz, bold=True):
    for pth in ("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc",
                "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc"):
        try:
            return ImageFont.truetype(pth, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def mars_rover_location_map(rover: str = "perseverance", zoom: int = 7,
                            span_deg: float = 0.5, out_px: int = 1000) -> CallToolResult:
    """火星探査ローバーの現在地を火星の地図上に示した画像を返す（認証不要）。

    「パーサヴィアランスの現在地を火星地図で」「キュリオシティが今どこにいるか地図で」
    NASA MMGIS からローバーの現在地(緯度経度)と走行経路、NASA Trek WMTS から火星の
    等角地図タイルを取得し、走行経路(赤線)と現在地(赤●)・着陸地点(青●)を合成。
    ローバーの火星天気・ソル情報も併記。画像は content に base64(JPEG)で返す。

    Args:
        rover: ローバー名。"perseverance"(既定/パーサヴィアランス) か "curiosity"(キュリオシティ)。
        zoom: 地図ズーム 5〜7(既定7=最詳細, ~325m/px)。
        span_deg: 表示する画角(経度幅・度)。大きいほど広範囲・解像度低下。既定0.5。
        out_px: 出力画像の長辺ピクセル(既定1000)。小さいほど軽量。
    """
    rover = (rover or "perseverance").strip().lower()
    if rover not in _MMGIS:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="対応ローバーは perseverance / curiosity です（現在地データが公開されているローバー）。")],
            structuredContent={"error": "unsupported rover", "supported": list(_MMGIS.keys())},
        )
    rover_ja = ROVER_JA.get(rover, rover)
    zoom = max(5, min(int(zoom), _MARS_MAXZOOM))
    span_deg = max(0.05, min(float(span_deg), 30.0))
    out_px = max(300, min(int(out_px), 2000))

    wp = _get_rover_waypoint(rover)
    route = _get_rover_route(rover)
    if not wp:
        return CallToolResult(
            content=[TextContent(type="text", text="ローバーの現在地データ(MMGIS)を取得できませんでした。")],
            structuredContent={"error": "MMGIS fetch failed", "rover": rover},
        )
    clat, clon = wp["lat"], wp["lon"]
    try:
        mosaic, center_px, tl, cols, rows = _fetch_tiles_parallel(clon, clat, span_deg, zoom)
    except Exception as e:
        return CallToolResult(content=[TextContent(type="text", text=f"火星地図タイル取得に失敗: {e}")],
                              structuredContent={"error": str(e)})
    # --- ローバーを正確に画像中心にする: 中心から正方形(画角span_deg)を切り出す ---
    W = cols * 256; H = rows * 256
    # ローバーのグローバル画素座標
    # center_px はモザイク内座標。モザイク左上のグローバル座標 = tl
    gx_rover = tl[0] + center_px[0]
    gy_rover = tl[1] + center_px[1]
    # 画角(度) -> グローバル画素幅。等角図法で縦横同スケール
    span_global_px = span_deg / (360.0 / W)
    half = span_global_px / 2.0
    # グローバル座標でクロップ範囲（正方形・中心=ローバー）
    left = int(gx_rover - half); right = left + int(2 * half)
    top = int(gy_rover - half); bottom = top + int(2 * half)
    side = right - left
    # 全マップ境界へクランプ（ローバー中心は保てる範囲で）
    if left < 0:
        left = 0; right = side
    if top < 0:
        top = 0; bottom = side
    if right > W:
        right = W; left = W - side
    if bottom > H:
        bottom = H; top = H - side
    # モザイク内クロップ座標へ変換して切り出し
    m_left = left - tl[0]; m_top = top - tl[1]
    m_right = m_left + side; m_bottom = m_top + side
    # モザイク範囲にクランプ
    m_left = max(0, m_left); m_top = max(0, m_top)
    m_right = min(mosaic.size[0], m_right); m_bottom = min(mosaic.size[1], m_bottom)
    crop = mosaic.crop((m_left, m_top, m_right, m_bottom))
    # 出力へ縮小
    img = crop.resize((out_px, out_px), Image.LANCZOS)
    scale = out_px / float(side)

    def g2px(lon, lat):
        # グローバル画素座標 → クロップ後 px
        gx = (lon + 180) / 360.0 * W
        gy = (90 - lat) / 180.0 * H
        return (gx - left) * scale, (gy - top) * scale

    d = ImageDraw.Draw(img)
    # 走行経路
    if route:
        pts = [g2px(lo, la) for lo, la in route]
        # ズーム範囲内(負でない座標)だけ線に
        d.line([(x, y) for x, y in pts if 0 <= x <= img.size[0] and 0 <= y <= img.size[1]],
               fill=(255, 90, 40, 255), width=3, joint="curve")
        # 着陸地点 = 経路起点
        lx0, ly0 = g2px(route[0][0], route[0][1])
        if -200 <= lx0 <= img.size[0] + 200 and -200 <= ly0 <= img.size[1] + 200:
            d.ellipse([lx0 - 8, ly0 - 8, lx0 + 8, ly0 + 8], fill=(80, 160, 255, 255),
                      outline=(255, 255, 255, 255), width=3)
    # 現在地
    cx, cy = g2px(clon, clat)
    d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=(255, 40, 30, 255),
              outline=(255, 255, 255, 255), width=4)
    f_big = _mars_font(30, True); f_mid = _mars_font(22, True); f_sm = _mars_font(20, False)
    # 現在地ラベル
    d.rectangle([cx + 16, cy - 22, cx + 16 + 360, cy + 30], fill=(0, 0, 0, 220))
    d.text((cx + 22, cy - 16), f"{rover_ja} 現在地", font=f_big, fill=(255, 255, 255, 255))
    # 天気・情報を併記
    weather_txt = ""
    try:
        w = _get_mars_weather(rover)
        if w:
            weather_txt = (f"ソル{w.get('sol')} {w.get('terrestrial_date')}  "
                           f"{w.get('max_temp')}~{w.get('min_temp')}℃ {w.get('atmo_opacity','')}")
    except Exception:
        pass
    # 凡例パネル
    d.rounded_rectangle([8, 8, min(700, img.size[0] - 8), 150], radius=12, fill=(0, 0, 0, 215))
    d.text((22, 20), f"火星探査ローバー {rover_ja} の現在地マップ", font=f_big, fill=(255, 255, 255, 255))
    d.text((22, 64), f"座標: {clat:.3f}° {'N' if clat>=0 else 'S'}  {clon:.3f}°E  走行 {wp.get('dist_km')} km",
           font=f_mid, fill=(225, 228, 248, 255))
    line2 = f"着陸: {_MMGIS[rover]['land_ja']}  現在地waypoint: sol{wp.get('sol')} (RMC {wp.get('rmc')})"
    d.text((22, 98), line2, font=f_sm, fill=(200, 205, 235, 255))
    if weather_txt:
        d.text((22, 124), "天気: " + weather_txt, font=f_sm, fill=(200, 225, 205, 255))
    # 凡例
    d.text((max(700, img.size[0]-400), 14) if False else (8, img.size[0]-40), "", font=f_sm, fill=(255,255,255,255))
    # 凡例バー(下部)
    ly = img.size[1] - 44
    d.rounded_rectangle([8, ly, img.size[0] - 8, img.size[1] - 8], radius=10, fill=(0, 0, 0, 200))
    d.ellipse([20, ly + 12, 36, ly + 28], fill=(80, 160, 255, 255))
    d.ellipse([58, ly + 12, 74, ly + 28], fill=(255, 40, 30, 255))
    d.text((86, ly + 9), "青●着陸地点  赤●現在地  橙線=走行経路  NASA Trek 等角図法", font=f_sm, fill=(235, 238, 250, 255))
    # JPEG 化 (軽量化)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    jpeg = buf.getvalue()
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{rover_ja} の火星現在地マップ")
    lines = [
        f"🔴 **{rover_ja}（{rover}）の火星現在地マップ**:",
        f"📍 座標: {clat:.4f}° {'N' if clat>=0 else 'S'} / {clon:.4f}°E ・ sol {wp.get('sol')}",
        f"🛣 走行距離: {wp.get('dist_km')} km（RMC {wp.get('rmc')}）",
        "画像は上に表示。着陸地点(青)からの走行経路(橙線)と現在地(赤●)を火星地図に合成。",
        "出典: NASA MMGIS (mars.nasa.gov) + Trek WMTS（認証不要）",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={"rover": rover, "rover_ja": rover_ja,
                           "lat": clat, "lon": clon, "sol": wp.get("sol"),
                           "dist_km": wp.get("dist_km"), "rmc": wp.get("rmc"),
                           "zoom": zoom, "span_deg": span_deg,
                           "source": "NASA MMGIS + Trek WMTS"},
    )

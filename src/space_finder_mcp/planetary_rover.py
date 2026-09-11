"""汎用・天体面ローバー位置マップ — MMGIS位置データ + NASA Trek ベースマップ（認証不要）。

月面・火星面等の探査ローバー（地表を移動する探査車）の現在地・走行経路・着陸地点を、
その天体の局所地図に重ねて画像化する。planetary_orbiter_track（軌道周回機）とは別に、
「地表を移動するローバー」を扱う。

ベースマップ（タイル・天体テーブル）とタイル合成・フォントは planetary_map を再利用。
ローバーの現在位置データは各ローバーのデータ源（NASA MMGIS 等）から取得する。

※ 正直な制約: 現在、走行経路まで取得できるローバー位置データは NASA MMGIS（火星）のみ。
月面ローバー等の現役位置データは公開されていない。そのため実データは火星ローバー
（Perseverance / Curiosity）のみ。構造は天体・データ源を追加可能な汎用設計にしている。

出典: 位置 = NASA MMGIS (mars.nasa.gov) ／ 地図 = NASA Trek (WMTS 等角図法)
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

# planetary_map の汎用コアと画像共通ヘルパーを再利用
from .planetary_map import BODIES, _fetch_tiles
from .img_common import load_font
from .input_utils import as_float, as_int

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# ====================================================================
# ローバーテーブル — 新規ローバーはここに1行追加するだけ
#   body: 周回天体（planetary_map.BODIES のキー、ベースマップ・言語を決定）
#   pos:  現在位置データ源。dict: {type:"mmgis", id:<ミッションID>} など
#   land_ja: 着陸地点（日本語）
# ====================================================================
ROVERS: dict[str, dict] = {
    "perseverance": {
        "body": "mars", "ja": "パーサヴィアランス", "en": "Perseverance",
        "land_ja": "ジェゼロ・クレーター",
        "pos": {"type": "mmgis", "id": "M20"},
    },
    "curiosity": {
        "body": "mars", "ja": "キュリオシティ", "en": "Curiosity",
        "land_ja": "ゲール・クレーター",
        "pos": {"type": "mmgis", "id": "MSL"},
    },
}


def _mmgis_waypoint(mid: str) -> Optional[dict]:
    """MMGIS からローバー現在地(最終waypoint)を取得。{lat, lon, sol, dist_km, rmc}"""
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


def _mmgis_route(mid: str) -> Optional[list]:
    """MMGIS からローバーの走行経路(全waypoint: (lon,lat) 列)を取得。"""
    url = f"https://mars.nasa.gov/mmgis-maps/{mid}/Layers/json/{mid}_waypoints.json"
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        return [(f["properties"]["lon"], f["properties"]["lat"])
                for f in r.json().get("features", [])
                if "lat" in f["properties"] and "lon" in f["properties"]]
    except (requests.RequestException, KeyError, ValueError):
        return None


def planetary_rover_location_map(body: str = "mars", rover: str = "perseverance",
                                 zoom: Optional[int] = None, span_deg: float = 0.5,
                                 out_px: int = 1000) -> CallToolResult:
    """任意の天体面を移動する探査ローバーの現在地を、その天体の地図上に示した画像を返す（認証不要）。

    例:「パーサヴィアランスの現在地を火星地図で」「キュリオシティが今どこにいるか地図で」
    ローバーの現在地データ（現状は NASA MMGIS / 火星）と走行経路を取得し、NASA Trek の
    等角地図タイルに、走行経路(橙線)・現在地(赤●)・着陸地点(青●)を合成して描画。
    planetary_map.BODIES の天体テーブルを利用するため、ベースマップは天体を指定して切り替え可能。

    Args:
        body: 天体名（既定 "mars"。planetary_map の対応天体）。
        rover: ローバー名（"perseverance"=既定, "curiosity"）。
        zoom: 地図ズーム。省略時は天体の最大ズーム（火星7）。高倍率ほど詳細。
        span_deg: 表示する画角(経度幅・度)。大きいほど広範囲・解像度低下。既定0.5。
        out_px: 出力画像の長辺ピクセル(既定1000, 最大2000)。
    """
    from PIL import Image, ImageDraw

    b = str(body).strip().lower()
    if b not in BODIES:
        return CallToolResult(
            content=[TextContent(type="text", text=f"不明な天体 '{body}'。対応: {', '.join(sorted(BODIES.keys()))}")],
            structuredContent={"error": "unknown body", "body": body,
                               "known": sorted(BODIES.keys())},
        )
    body_cfg = BODIES[b]

    rv = str(rover).strip().lower()
    if rv not in ROVERS:
        return CallToolResult(
            content=[TextContent(type="text", text=f"対応ローバー: {', '.join(sorted(ROVERS.keys()))}（現在地データが公開されているローバー）")],
            structuredContent={"error": "unsupported rover", "supported": sorted(ROVERS.keys())},
        )
    rcfg = ROVERS[rv]
    if rcfg["body"] != b:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{rv}'（{rcfg['ja']}）は {BODIES[rcfg['body']]['ja']} のローバーです。天体 '{body}' と一致しません。")],
            structuredContent={"error": "body mismatch", "rover": rv, "body": body},
        )
    rover_ja = rcfg["ja"]

    zoom = as_int(zoom, None, 4, body_cfg["maxzoom"])
    if zoom is None:
        zoom = body_cfg["maxzoom"]
    span_deg = as_float(span_deg, 0.5, 0.05, 30.0)
    out_px = as_int(out_px, 1000, 300, 2000)

    # ---- 位置データ（データ源ごと）----
    ptype = rcfg["pos"]["type"]
    if ptype != "mmgis":
        return CallToolResult(
            content=[TextContent(type="text", text=f"ローバー '{rv}' のデータ源 '{ptype}' には未対応です。")],
            structuredContent={"error": "unsupported data source", "rover": rv, "type": ptype},
        )
    mid = rcfg["pos"]["id"]
    wp = _mmgis_waypoint(mid)
    route = _mmgis_route(mid)
    if not wp:
        return CallToolResult(
            content=[TextContent(type="text", text="ローバーの現在地データを取得できませんでした。")],
            structuredContent={"error": "position fetch failed", "rover": rv},
        )
    clat, clon = wp["lat"], wp["lon"]

    # ---- タイル合成（planetary_map 再利用）----
    try:
        mosaic, center_px, tl, cols, rows = _fetch_tiles(body_cfg, clon, clat, span_deg, zoom)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{body_cfg['ja']}地図タイル取得に失敗: {e}")],
            structuredContent={"error": str(e)},
        )
    W = cols * 256; H = rows * 256
    gx_rover = tl[0] + center_px[0]; gy_rover = tl[1] + center_px[1]
    span_global_px = span_deg / (360.0 / W)
    half = span_global_px / 2.0
    left = int(gx_rover - half); top = int(gy_rover - half)
    side = int(2 * half)
    right = left + side; bottom = top + side
    if left < 0:
        left, right = 0, side
    if top < 0:
        top, bottom = 0, side
    if right > W:
        right, left = W, W - side
    if bottom > H:
        bottom, top = H, H - side
    m_left = max(0, left - tl[0]); m_top = max(0, top - tl[1])
    m_right = min(mosaic.size[0], m_left + side); m_bottom = min(mosaic.size[1], m_top + side)
    crop = mosaic.crop((m_left, m_top, m_right, m_bottom))
    img = crop.resize((out_px, out_px), Image.LANCZOS)
    scale = out_px / float(side)

    def g2px(lon, lat):
        gx = (lon + 180) / 360.0 * W; gy = (90 - lat) / 180.0 * H
        return (gx - left) * scale, (gy - top) * scale

    d = ImageDraw.Draw(img)
    # 走行経路
    if route:
        pts = [g2px(lo, la) for lo, la in route]
        d.line([(x, y) for x, y in pts if 0 <= x <= img.size[0] and 0 <= y <= img.size[1]],
               fill=(255, 90, 40), width=3, joint="curve")
        lx0, ly0 = g2px(route[0][0], route[0][1])
        if -200 <= lx0 <= img.size[0] + 200 and -200 <= ly0 <= img.size[1] + 200:
            d.ellipse([lx0 - 8, ly0 - 8, lx0 + 8, ly0 + 8], fill=(80, 160, 255),
                      outline=(255, 255, 255), width=3)
    # 現在地
    cx, cy = g2px(clon, clat)
    d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=(255, 40, 30),
              outline=(255, 255, 255), width=4)
    # 旧 _font は常にメイリオ Bold 優先だったため bold=True で等価
    f_big = load_font(30, bold=True); f_mid = load_font(22, bold=True); f_sm = load_font(20, bold=True)
    # 現在地ラベル
    d.rectangle([cx + 16, cy - 22, cx + 16 + 380, cy + 30], fill=(0, 0, 0, 220))
    d.text((cx + 22, cy - 16), f"{rover_ja} 現在地", font=f_big, fill=(255, 255, 255))
    # 凡例パネル（テキストに合わせて高さを決める）
    iw, ih = img.size
    title = f"{body_cfg['ja']}面探査ローバー {rover_ja} の現在地マップ"
    row1 = f"座標: {clat:.3f}° {'N' if clat >= 0 else 'S'}  {clon:.3f}°E  走行 {wp.get('dist_km')} km"
    row2 = f"着陸: {rcfg['land_ja']}  現在地waypoint: sol{wp.get('sol')} (RMC {wp.get('rmc')})"
    pad = 6
    h_title = d.textbbox((0, 0), title, font=f_big)[3] - d.textbbox((0, 0), title, font=f_big)[1]
    h_row1 = d.textbbox((0, 0), row1, font=f_mid)[3] - d.textbbox((0, 0), row1, font=f_mid)[1]
    h_row2 = d.textbbox((0, 0), row2, font=f_sm)[3] - d.textbbox((0, 0), row2, font=f_sm)[1]
    panel_bottom = 12 + h_title + pad + h_row1 + pad + h_row2 + 20
    d.rounded_rectangle([8, 8, min(720, iw - 8), panel_bottom], radius=12, fill=(0, 0, 0, 215))
    y = 20
    d.text((22, y), title, font=f_big, fill=(255, 255, 255)); y += h_title + pad
    d.text((22, y), row1, font=f_mid, fill=(225, 228, 248)); y += h_row1 + pad
    d.text((22, y), row2, font=f_sm, fill=(200, 205, 235))
    # 凡例バー(下部)
    ly = ih - 44
    d.rounded_rectangle([8, ly, iw - 8, ih - 8], radius=10, fill=(0, 0, 0, 200))
    d.ellipse([20, ly + 12, 36, ly + 28], fill=(80, 160, 255))
    d.ellipse([58, ly + 12, 74, ly + 28], fill=(255, 40, 30))
    d.text((86, ly + 9), "青●着陸地点  赤●現在地  橙線=走行経路  NASA Trek 等角図法",
           font=f_sm, fill=(235, 238, 250))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    jpeg = buf.getvalue()
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{rover_ja} の{body_cfg['ja']}現在地マップ")
    lines = [
        f"🔴 **{rover_ja}（{rv}）の{body_cfg['ja']}現在地マップ**:",
        f"📍 座標: {clat:.4f}° {'N' if clat >= 0 else 'S'} / {clon:.4f}°E ・ sol {wp.get('sol')}",
        f"🛣 走行距離: {wp.get('dist_km')} km（RMC {wp.get('rmc')}）",
        "画像は上に表示。着陸地点(青)からの走行経路(橙線)と現在地(赤●)を地図に合成。",
        f"出典: NASA MMGIS (mars.nasa.gov) + Trek WMTS（認証不要）",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={"body": b, "rover": rv, "rover_ja": rover_ja,
                           "lat": clat, "lon": clon, "sol": wp.get("sol"),
                           "dist_km": wp.get("dist_km"), "rmc": wp.get("rmc"),
                           "zoom": zoom, "span_deg": span_deg,
                           "source": "NASA MMGIS + Trek WMTS"},
    )

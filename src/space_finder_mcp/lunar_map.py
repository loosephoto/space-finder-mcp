"""月周回衛星の地上軌道（月面）マップ — JPL Horizons + IAU月自転モデル（認証不要）。

指定した月周回機（LRO, ゲートウェイ等, JPL Horizons 天体ID）の月面での現在位置
（selenographic 緯度経度・高度）と、その前後の軌道トレイルを、NASA Trek の月面
タイル（LRO WAC モザイク）で描いた月面地図に重ねて画像化する。

アルテミス計画で月軌道を周回する機体（ゲートウェイ等）が増えた際に、地球周回
衛星(sat_ground_track)と同様に位置を追跡できる基盤。

精度の特徴:
- 位置: JPL Horizons が返す月中心・J2000赤道の状態ベクトルを、IAU 2015 月自転
  モデル（本初子午線角 W と月の北極方位）で月体固定座標へ回転して、
  selenographic 緯度経度・高度を計算。
- 軌道トレイル: 月周回機は約2時間で月を1周するため、指定時間帯を分刻みで
  伝播し、経度±180度境界で分割して描画。
- 月面地図: NASA Trek WMTS の等角図法月面タイル（LRO WAC モザイク, ズーム0-8）。

出典: 位置 = JPL Horizons (ssd.jpl.nasa.gov) ／ 地図 = NASA Trek (LRO WAC)
"""
from __future__ import annotations

import base64
import datetime
import io
import math
import os
import re
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

UA = {"User-Agent": "space-finder-mcp/0.24 (MCP; lunar satellite track)"}

# ---- JPL Horizons 天体ID ----
# 月周回機の既知 ID（負のIDは宇宙機）。将来のゲートウェイ等はここに追加。
MOON_CRAFT: dict[str, dict] = {
    "lro":      {"id": "-85", "ja": "LRO（月周回偵察衛星）", "en": "LRO (Lunar Reconnaissance Orbiter)"},
    "gateway":  {"id": "-228", "ja": "ゲートウェイ（Gateway）", "en": "Gateway (lunar orbital station)"},
}
# 過去ミッション（表示用・現在軌道上にない旨を併記）
PAST_MOON = {
    "kaguya":  {"id": "-55", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)", "note": "2009年に月面へ制御落下（運用終了）"},
    "selene":  {"id": "-55", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)", "note": "2009年に月面へ制御落下（運用終了）"},
}

# ---- NASA Trek 月面タイル（等角図法・LRO WAC モザイク）----
_MOON_TILE = ("https://trek.nasa.gov/tiles/Moon/EQ/"
              "LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/"
              "{z}/{row}/{col}.jpg")
_MOON_MAXZOOM = 8


# ---------- Horizons: 月中心 J2000 状態ベクトル ----------
def _horizons_state(cmd: str, jd: float) -> tuple:
    """月中心・J2000赤道系の状態ベクトル(km, km/s)。戻り: (x,y,z, vx,vy,vz)。"""
    params = {
        "format": "text", "COMMAND": "'" + cmd + "'", "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS",
        "CENTER": "'@301'", "REF_PLANE": "'FRAME'",
        "TLIST": "'{:.6f}'".format(jd), "VEC_TABLE": "'2'", "OUT_UNITS": "'KM-S'",
    }
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                     headers=UA, timeout=40)
    r.raise_for_status()
    txt = r.text
    i = txt.find("$$SOE"); j = txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に座標ブロックがありません")
    block = txt[i + 5:j]
    m = re.search(r"X\s*=\s*([-+0-9.Ee]+)\s+Y\s*=\s*([-+0-9.Ee]+)\s+Z\s*=\s*([-+0-9.Ee]+)", block)
    v = re.search(r"VX=\s*([-+0-9.Ee]+)\s+VY=\s*([-+0-9.Ee]+)\s+VZ=\s*([-+0-9.Ee]+)", block)
    if not m:
        raise ValueError("状態ベクトルを解析できません: " + block[:200])
    x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if v:
        vx, vy, vz = float(v.group(1)), float(v.group(2)), float(v.group(3))
    else:
        vx = vy = vz = None
    return x, y, z, vx, vy, vz


def _moon_name(cmd: str) -> str:
    """Horizons 天体の名前を取得。"""
    params = {"format": "text", "COMMAND": "'" + cmd + "'", "MAKE_EPHEM": "'NO'"}
    try:
        r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                         headers=UA, timeout=30)
        m = re.search(r"Revised:.*?([A-Za-z0-9 /().\-]+?)\s*\((-?[0-9]+)\)", r.text)
        if m:
            return m.group(1).strip()
    except requests.RequestException:
        pass
    return cmd


# ---------- IAU 2015 月自転モデル ----------
def _moon_pole_W(jd: float) -> tuple:
    """IAU 2015: 月の北極(J2000赤道, α,δ in deg)と本初子午線角 W(deg)。"""
    T = (jd - 2451545.0) / 36525.0
    a = 269.9949 + 0.0031 * T
    d = 66.5392 + 0.0130 * T
    W = 38.3213 + 13.17635815 * (jd - 2451545.0)
    return a, d, W


def _j2000_to_body(x, y, z, jd) -> tuple:
    """J2000状態ベクトル → 月体固定座標（IAU月自転で回転）。"""
    a, d, W = _moon_pole_W(jd)
    A = math.radians(a); D = math.radians(d); Wr = math.radians(W)
    # Rz(-α)
    x1 = x * math.cos(A) + y * math.sin(A)
    y1 = -x * math.sin(A) + y * math.cos(A)
    z1 = z
    # Ry(δ-90)
    t = D - math.pi / 2
    x2 = x1 * math.cos(t) + z1 * math.sin(t)
    y2 = y1
    z2 = -x1 * math.sin(t) + z1 * math.cos(t)
    # Rz(-W)
    x3 = x2 * math.cos(Wr) + y2 * math.sin(Wr)
    y3 = -x2 * math.sin(Wr) + y2 * math.cos(Wr)
    z3 = z2
    return x3, y3, z3


def _body_to_latlon(xb, yb, zb, moon_r=1737.4):
    r = math.sqrt(xb * xb + yb * yb + zb * zb)
    lat = math.degrees(math.asin(max(-1, min(1, zb / r))))
    lon = math.degrees(math.atan2(yb, xb)) % 360
    if lon > 180:
        lon -= 360
    alt = r - moon_r
    return lat, lon, alt


def _craft_state(cmd: str, jd: float):
    """月周回機の月面(selenographic)位置を返す。{lat, lon, alt_km, speed}"""
    x, y, z, vx, vy, vz = _horizons_state(cmd, jd)
    xb, yb, zb = _j2000_to_body(x, y, z, jd)
    lat, lon, alt = _body_to_latlon(xb, yb, zb)
    speed = None
    if vx is not None:
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    return {"lat": lat, "lon": lon, "alt_km": alt, "speed_kms": speed,
            "dist_km": math.sqrt(x * x + y * y + z * z)}


# ---------- 月面タイル合成 ----------
def _fetch_tiles_parallel(center_lon, center_lat, span_deg, zoom, max_workers=12):
    from PIL import Image
    import concurrent.futures
    cols = 2 ** (zoom + 1); rows = 2 ** zoom
    W = cols * 256; H = rows * 256
    xc = (center_lon + 180) / 360.0 * W
    yc = (90 - center_lat) / 180.0 * H
    span_px = span_deg / (360.0 / W)
    cc0 = int(xc // 256); rr0 = int(yc // 256)
    half = int(math.ceil((span_px / 2) / 256.0)) + 1
    c_lo = max(0, cc0 - half); r_lo = max(0, rr0 - half)
    c_hi = min(cols - 1, cc0 + half); r_hi = min(rows - 1, rr0 + half)
    tiles = [(rr, cc) for rr in range(r_lo, r_hi + 1) for cc in range(c_lo, c_hi + 1)]

    def _one(tc):
        rr, cc = tc
        try:
            r = requests.get(_MOON_TILE.format(z=zoom, row=rr, col=cc), headers=UA, timeout=15)
            return rr, cc, (r.content if r.status_code == 200 else None)
        except requests.RequestException:
            return rr, cc, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, tiles))
    cw = c_hi - c_lo + 1; rh = r_hi - r_lo + 1
    mosaic = Image.new("RGB", (cw * 256, rh * 256), (35, 35, 40))
    for rr, cc, data in results:
        if data:
            try:
                from PIL import Image as PImage
                mosaic.paste(PImage.open(io.BytesIO(data)).convert("RGB"),
                             ((cc - c_lo) * 256, (rr - r_lo) * 256))
            except Exception:
                pass
    center_px = (xc - c_lo * 256, yc - r_lo * 256)
    return mosaic, center_px, (c_lo * 256, r_lo * 256), cols, rows


# ---------- ツール ----------
def lunar_track(spacecraft: str = "lro", when: Optional[str] = None,
                minutes: int = 90, step: int = 5, zoom: int = 3,
                span_deg: float = 80.0, out_px: int = 900) -> CallToolResult:
    """月周回機の月面での現在位置と軌道トレイルを月面地図にプロットした画像を返す。

    例:「LROの現在位置を月面地図で」「月周回機の位置」「ゲートウェイの月面軌道」
    JPL Horizons が返す月中心の状態ベクトルを IAU 月自転モデルで月面座標
    (selenographic 緯度経度・高度) に変換し、NASA Trek の月面地図（LRO WAC モザイク）
    に重ねて描画。認証不要。

    アルテミス計画で月軌道を周回する機体（ゲートウェイ等）が増えた際、このツールで
    各機体の月面位置を追跡できる。機体は spacecraft 名（lro, gateway）または
    JPL Horizons 天体ID（負のID）で指定。過去ミッション（かぐや等）は軌道上にない。

    Args:
        spacecraft: 月周回機名（lro=推奨, gateway）または Horizons ID（例 "-85"）。
            過去機（kaguya/selene）は運用終了のため表示不可（その旨を返す）。
        when: 観測時刻 ISO8601（例 "2026-09-10T00:00:00Z"）。省略で現在。
        minutes: 現在位置の前後何分の軌道を表示するか（既定 90=約1周, 最大 720）。
        step: トレイルの時間刻み（分。既定 5、最小 1）。
        zoom: 月面地図のズーム 1〜8（既定 3、大域表示）。
        span_deg: 表示する経度幅（度。既定 80、大きいほど広域）。
        out_px: 出力画像の長辺ピクセル（既定 900）。
    """
    from PIL import Image, ImageDraw, ImageFont

    # ---- 引数解決 ----
    sc = str(spacecraft).strip().lower()
    info = MOON_CRAFT.get(sc) or {}
    if sc in PAST_MOON:
        note = PAST_MOON[sc]["note"]
        return CallToolResult(
            content=[TextContent(type="text", text=f"{PAST_MOON[sc]['ja']} は {note}。現在の月面位置を表示できません。現役の月周回機（lro, gateway）をご指定ください。")],
            structuredContent={"error": "past mission", "spacecraft": sc, "note": note,
                               "active": sorted(MOON_CRAFT.keys())},
        )
    if info:
        cmd = info["id"]; ja = info["ja"]; en = info["en"]
    elif sc.startswith("-") or sc.isdigit():
        cmd = sc; ja = sc; en = _moon_name(cmd)
    else:
        return CallToolResult(
            content=[TextContent(type="text", text=f"不明な月周回機 '{spacecraft}'。対応: {', '.join(sorted(MOON_CRAFT.keys()))}（または JPL Horizons の負の天体ID）")],
            structuredContent={"error": "unknown spacecraft", "spacecraft": spacecraft,
                               "known": sorted(MOON_CRAFT.keys())},
        )

    minutes = max(10, min(int(minutes), 720))
    step = max(1, min(int(step), 30))
    zoom = max(1, min(int(zoom), _MOON_MAXZOOM))
    span_deg = max(10.0, min(float(span_deg), 180.0))
    out_px = max(400, min(int(out_px), 1600))

    # ---- 時刻（JD）----
    if when:
        iso = str(when).strip().replace("Z", "+00:00")
        try:
            t0 = datetime.datetime.fromisoformat(iso)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return CallToolResult(
                content=[TextContent(type="text", text="when は ISO8601（YYYY-MM-DDTHH:MM[:SS]Z）で指定してください。")],
                structuredContent={"error": "bad time", "when": when},
            )
    else:
        t0 = datetime.datetime.now(datetime.timezone.utc)
    # 現在の JD(UTC)
    jd_now = 2451545.0 + (t0 - datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)).total_seconds() / 86400.0
    tstr = t0.strftime("%Y-%m-%d %H:%M UTC")

    # ---- 現在位置 ----
    try:
        cur = _craft_state(cmd, jd_now)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"JPL Horizons からの状態ベクトル取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "ssd.jpl.nasa.gov"},
        )
    except ValueError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"状態ベクトルの解析に失敗: {e}")],
            structuredContent={"error": str(e)},
        )

    lat0, lon0, alt0 = cur["lat"], cur["lon"], cur["alt_km"]
    speed_kms = cur["speed_kms"]

    # ---- 軌道トレイル ----
    trail = []
    n = max(3, int(minutes / step))
    for i in range(-n, n + 1):
        jd_i = jd_now + i * step / 1440.0
        try:
            st = _craft_state(cmd, jd_i)
            trail.append((st["lon"], st["lat"]))
        except Exception:
            continue

    # ---- 月面タイルを合成して描画 ----
    try:
        mosaic, center_px, tl, cols, rows = _fetch_tiles_parallel(lon0, lat0, span_deg, zoom)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"月面地図タイルの取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "trek.nasa.gov"},
        )
    W = cols * 256; H = rows * 256
    gx_rover = tl[0] + center_px[0]; gy_rover = tl[1] + center_px[1]
    span_global_px = span_deg / (360.0 / W)
    half = span_global_px / 2.0
    left = int(gx_rover - half); right = left + int(2 * half)
    top = int(gy_rover - half); bottom = top + int(2 * half)
    side = right - left
    # クランプ
    if left < 0: left = 0; right = side
    if top < 0: top = 0; bottom = side
    if right > W: right = W; left = W - side
    if bottom > H: bottom = H; top = H - side
    m_left = max(0, left - tl[0]); m_top = max(0, top - tl[1])
    m_right = min(mosaic.size[0], m_left + side); m_bottom = min(mosaic.size[1], m_top + side)
    crop = mosaic.crop((m_left, m_top, m_right, m_bottom))
    # 正方形に縮小
    img = crop.resize((out_px, out_px), Image.LANCZOS)
    img = img.point(lambda p: int(p * 0.7))
    scale = out_px / float(side)

    def g2px(lon, lat):
        gx = (lon + 180) / 360.0 * W
        gy = (90 - lat) / 180.0 * H
        return (gx - left) * scale, (gy - top) * scale

    d = ImageDraw.Draw(img)
    # 軌道トレイル（経度ラップ分割）
    def _drawseg(seg):
        if len(seg) >= 2:
            d.line([g2px(a, b) for a, b in seg], fill=(255, 140, 0), width=max(3, out_px // 300), joint="curve")
    if trail:
        seg = [trail[0]]
        for i in range(1, len(trail)):
            if abs(trail[i][0] - trail[i - 1][0]) > 150:
                _drawseg(seg); seg = [trail[i]]
            else:
                seg.append(trail[i])
        _drawseg(seg)

    # 現在位置マーカー
    cx, cy = g2px(lon0, lat0)
    r = max(9, out_px // 70)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 60, 30), outline=(255, 255, 255), width=max(4, out_px // 200))
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=(255, 255, 255))

    # フォント
    def font(sz):
        for p in ("C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc"):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                continue
        return ImageFont.load_default()

    f_big = font(int(out_px / 30)); f_mid = font(int(out_px / 42)); f_sm = font(int(out_px / 48))
    # 情報パネル
    d.rounded_rectangle([12, 12, int(out_px * 0.7), int(out_px * 0.24)], radius=12, fill=(0, 0, 0, 210))
    ns = "北緯" if lat0 >= 0 else "南緯"
    ew = "東経" if lon0 >= 0 else "西経"
    spd = f"{speed_kms:.2f} km/s" if speed_kms else "?"
    d.text((24, 20), f"🌙 {ja}（{en}）", font=f_big, fill=(255, 255, 255))
    d.text((24, 20 + int(out_px / 22)), f"月面: {ns} {abs(lat0):.2f}° / {ew} {abs(lon0):.2f}°  高度 {alt0:.1f} km", font=f_mid, fill=(225, 228, 248))
    d.text((24, 20 + int(out_px / 15)), f"速度 {spd} ・ 時刻 {tstr} UTC", font=f_sm, fill=(200, 210, 240))
    # 現在地ラベル（マーカーと重ならない位置に配置）
    lab_w = int(out_px * 0.32)
    lab_h = int(out_px / 16)
    margin = r * 5  # マーカーと十分離す
    if cx + margin + lab_w < out_px - 10:
        lx, ly = cx + margin, cy - lab_h // 2
    elif cx - margin - lab_w > 10:
        lx, ly = cx - margin - lab_w, cy - lab_h // 2
    else:
        lx, ly = cx - lab_w // 2, cy + margin
    ly = max(10, min(ly, out_px - lab_h - 10))
    d.rounded_rectangle([lx, ly, lx + lab_w, ly + lab_h], radius=8, fill=(200, 0, 0, 235))
    d.text((lx + 8, ly + 5), f"{ja} 現在位置", font=f_mid, fill=(255, 255, 255))

    # JPEG 化
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    jpeg = buf.getvalue()
    if len(jpeg) > 3_500_000:
        img = img.resize((int(out_px * 0.7), int(out_px * 0.7)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        jpeg = buf.getvalue()
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{ja} の月面位置")

    lines = [
        f"🌙 **{ja}** の月面位置（{tstr} UTC）:",
        f"📍 月面: {ns} {abs(lat0):.2f}° / {ew} {abs(lon0):.2f}°",
        f"🛰 高度 {alt0:.1f} km ・ 月中心距離 {cur['dist_km']:.1f} km ・ 速度 {spd}",
        f"🛤 軌道トレイル: 前後 {minutes} 分（{step} 分刻み）。オレンジ線=軌道。",
        "出典: JPL Horizons + IAU月自転モデル ／ 月面地図 NASA Trek LRO WAC",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "spacecraft": sc, "name_ja": ja, "name_en": en, "horizons_id": cmd,
            "time_utc": tstr,
            "latitude": round(lat0, 4), "longitude": round(lon0, 4),
            "altitude_km": round(alt0, 2), "speed_kms": round(speed_kms, 4) if speed_kms else None,
            "dist_from_moon_center_km": round(cur["dist_km"], 1),
            "trail_minutes": minutes, "step_min": step, "trail_points": len(trail),
            "source": "JPL Horizons + IAU moon rotation + NASA Trek",
        },
    )

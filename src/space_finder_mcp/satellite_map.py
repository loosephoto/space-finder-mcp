"""任意の人工衛星の地上軌道（ground track）を地球地図にプロット（認証不要）。

指定した人工衛星（NORAD ID または名前）の現在位置（真下の点）と、その前後の
軌道トレイルを、NASA Blue Marble の等角図法地球地図に重ねて画像化する。

精度の特徴:
- Skyfield の EarthSatellite（SGP4/SDP4 軌道伝播）+ subpoint() で計算。
  subpoint は地球の自転・歳差・極運動を考慮した「真の地上点」を返す。
- 軌道トレイルは 1 分刻み（デフォルト）で滑らかに描画。経度±180度境界で線を分割。
- 現在地の緯度経度・高度（km）・速度・軌道エポックを併記。

出典: 位置計算 = CelesTrak TLE + Skyfield(sgp4) ／ 地図 = NASA Blue Marble (eoimages.gsfc.nasa.gov)
"""
from __future__ import annotations

import base64
import datetime
import io
import os
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .celestrak import WELL_KNOWN, fetch_tle
from .img_common import encode_jpeg, load_font, split_at_antimeridian

# ---- NASA Blue Marble 用 UA（TLE 取得は celestrak.fetch_tle に共通化）----
UA = {"User-Agent": "space-finder-mcp/0.23 (MCP; satellite ground track)"}

# ---- NASA Blue Marble 等角図法 地球地図（キャッシュ）----
_EARTH_URL = ("https://eoimages.gsfc.nasa.gov/images/imagerecords/"
              "57000/57752/land_shallow_topo_2048.jpg")
_EARTH_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", "."),
                            "Temp", "space_finder_mcp", "earth_2048.jpg")


def _earth_image() -> bytes:
    """Blue Marble 地球地図（2048x1024 等角）を取得（ローカルキャッシュあり）。"""
    if os.path.exists(_EARTH_CACHE):
        with open(_EARTH_CACHE, "rb") as f:
            return f.read()
    os.makedirs(os.path.dirname(_EARTH_CACHE), exist_ok=True)
    r = requests.get(_EARTH_URL, headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    with open(_EARTH_CACHE, "wb") as f:
        f.write(r.content)
    return r.content


def _resolve_norad_candidates(name: str) -> list[tuple[str, int]]:
    """衛星名・省略名を (名前, NORAD ID) 候補に解決する（完全一致を最優先）。

    完全一致がなければ部分一致の候補を全件返す。曖昧な入力で先頭候補に
    黙って確定させないため、判定は呼び出し側で行う。
    """
    nm = name.strip().lower()
    exact = [(k, v) for k, v in WELL_KNOWN.items() if nm == k]
    if exact:
        return exact[:1]
    return [(k, v) for k, v in WELL_KNOWN.items()
            if len(nm) >= 4 and (k in nm or nm in k)]


def _fetch_tle2(norad_id: int) -> tuple[str, str, str]:
    """NORAD ID から TLE 2行を取得。戻り: (name, line1, line2)。"""
    tle = fetch_tle(norad_id=int(norad_id))
    if tle is None:
        raise ValueError(f"NORAD {norad_id} の TLE が見つかりません")
    return tle


def _sat_subpoint(sat, t) -> tuple[float, float, float]:
    """衛星の真下点（緯度, 経度, 高度km）と速度(km/s)を返す。

    geoc.velocity は3次元速度ベクトル(km/s)なので、その大きさ（ノルム）を
    衛星の対地速度として使う。
    """
    geoc = sat.at(t)
    sub = geoc.subpoint()
    v = geoc.velocity.km_per_s  # 3次元配列 (vx, vy, vz)
    import math
    speed = float(math.sqrt(sum(float(c) ** 2 for c in v)))
    return (float(sub.latitude.degrees), float(sub.longitude.degrees),
            float(sub.elevation.km), speed)


def sat_ground_track(norad_id: Optional[int] = None, name: Optional[str] = None,
                     when: Optional[str] = None,
                     minutes: int = 45, step: int = 1,
                     out_px: int = 1200) -> CallToolResult:
    """任意の人工衛星の現在位置と地上軌道を地球地図にプロットした画像を返す。

    例:「ISSの現在位置を地球地図で」「ひのでの位置を地図で」「ハッブルの軌道」
    CelesTrak の最新 TLE を Skyfield（SGP4）で伝播し、衛星の真下の点（緯度経度・高度）
    とその前後の軌道トレイルを NASA Blue Marble 地球地図に重ねる。認証不要。

    精度: 軌道トレイルは step 分刻み（デフォルト 1分）で描画し、経度±180度境界で
    線を分割するため、世界地図上で正確に表示される。subpoint() は地球自転・歳差・
    極運動を考慮した真の地上点を返す。

    Args:
        norad_id: NORAD カタログ番号（例 25544=ISS, 29479=ひので）。name より優先。
        name: 衛星名または省略名（例 "iss", "hubble", "hinode"）。既知の衛星は
            ID に解決、それ以外は TLE を直接検索。
        when: 観測時刻 ISO8601（例 "2026-09-10T03:30:00Z"）。省略で現在。
        minutes: 現在位置の前後何分の軌道を表示するか（既定 45、最大 1440=24時間）。
            準天頂衛星(みちびき)の8の字軌道を見るには 720 前後を指定。
        step: トレイルの時間刻み（分。既定 1、最小 0.5）。小さいほど滑らか。
        out_px: 出力画像の幅ピクセル（既定 1200、最大 2048）。
    """
    # ---- 引数解決 ----
    name = str(name).strip() if name is not None else ""
    if norad_id is None and not name:
        return CallToolResult(
            content=[TextContent(type="text", text="衛星を指定してください。norad_id（例 25544=ISS, 29479=ひので）または name（例 \"iss\", \"hubble\"）のいずれかが必要です。")],
            structuredContent={"error": "satellite not specified",
                               "known_names": sorted(WELL_KNOWN.keys())},
        )
    if norad_id is None and name:
        cands = _resolve_norad_candidates(name)
        uniq = {v for _, v in cands}
        if len(uniq) == 1:
            norad_id = uniq.pop()
        elif len(uniq) > 1:
            # 曖昧な名前は推測せず候補を提示して検索を止める
            return CallToolResult(
                content=[TextContent(type="text", text=f"衛星名 '{name}' は候補が複数あります: "
                                     + ", ".join(f"{k} (NORAD {v})" for k, v in cands)
                                     + "。NORAD ID か、より具体的な名前を指定してください。")],
                structuredContent={"error": "ambiguous satellite name", "name": name,
                                   "candidates": [{"name": k, "norad_id": v} for k, v in cands]},
            )
    if norad_id is None:
        # 名前で CelesTrak を直接検索（取得失敗と「該当なし」を区別する）
        try:
            found = fetch_tle(name=name)
        except requests.RequestException as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"衛星の軌道要素(TLE)取得に失敗しました: {e}")],
                structuredContent={"error": str(e), "source": "celestrak.org"},
            )
        if found is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"衛星 '{name}' を特定できませんでした。NORAD ID を直接指定するか、既知の衛星名（iss, hubble, hinode 等）をお使いください。")],
                structuredContent={"error": "unknown satellite", "name": name},
            )
        sat_name, tle1, tle2 = found
    else:
        try:
            sat_name, tle1, tle2 = _fetch_tle2(int(norad_id))
        except (requests.RequestException, ValueError) as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"衛星の軌道要素(TLE)取得に失敗しました: {e}")],
                structuredContent={"error": str(e), "source": "celestrak.org"},
            )

    minutes = max(5, min(int(minutes), 1440))
    step = max(0.5, min(float(step), 10.0))
    out_px = max(600, min(int(out_px), 2048))

    # ---- Skyfield ----
    try:
        from skyfield.api import EarthSatellite, load
    except ImportError:
        return CallToolResult(
            content=[TextContent(type="text", text="Skyfield が未インストールです（位置計算に必要）。")],
            structuredContent={"error": "skyfield missing"},
        )
    ts = load.timescale()

    # 時刻
    if when:
        iso = str(when).strip().replace("Z", "+00:00")
        try:
            t0 = datetime.datetime.fromisoformat(iso)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=datetime.timezone.utc)
            t = ts.from_datetime(t0)
        except ValueError:
            return CallToolResult(
                content=[TextContent(type="text", text="when は ISO8601（YYYY-MM-DDTHH:MM[:SS]Z）で指定してください。")],
                structuredContent={"error": "bad time", "when": when},
            )
    else:
        t = ts.now()
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")

    sat = EarthSatellite(tle1, tle2, sat_name, ts)

    # 現在位置
    lat0, lon0, alt0, speed0 = _sat_subpoint(sat, t)

    # 軌道トレイル（前後 minutes 分を step 刻み）
    n = max(3, int(minutes / step))
    trail = []
    for i in range(-n, n + 1):
        tt = ts.tt_jd(t.tt + i * step / 1440.0)  # step は分 → 日に変換 (1日=1440分)
        try:
            la, lo, al, sp = _sat_subpoint(sat, tt)
        except Exception:
            continue
        trail.append((lo, la))

    # ---- 地球地図を描画 ----
    try:
        from PIL import Image, ImageDraw
        earth = _earth_image()
        img = Image.open(io.BytesIO(earth)).convert("RGB")
        HH = out_px // 2
        img = img.resize((out_px, HH), Image.LANCZOS)
        img = img.point(lambda p: int(p * 0.72))  # 暗くしてマーカーを強調
    except (requests.RequestException, OSError) as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"地球地図の取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "eoimages.gsfc.nasa.gov"},
        )
    d = ImageDraw.Draw(img)
    W, HH = img.size

    def proj(lon, lat):
        return (lon + 180) / 360.0 * W, (90 - lat) / 180.0 * HH

    # 軌道トレイル（経度ラップで分割）
    def _draw_segment(seg):
        if len(seg) >= 2:
            d.line([proj(a, b) for a, b in seg], fill=(255, 140, 0), width=max(3, out_px // 350), joint="curve")

    for seg in split_at_antimeridian(trail):
        _draw_segment(seg)

    # 現在位置マーカー
    cx, cy = proj(lon0, lat0)
    r = max(10, out_px // 90)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 40, 30), outline=(255, 255, 255), width=max(4, out_px // 300))
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=(255, 255, 255))

    # フォント（img_common.load_font。旧実装はメイリオ Bold 優先だった）
    f_big = load_font(int(out_px / 40), bold=True)
    f_mid = load_font(int(out_px / 55), bold=True)
    f_sm = load_font(int(out_px / 62), bold=True)
    # 情報パネル
    panel_w = int(out_px * 0.52)
    d.rounded_rectangle([14, 14, panel_w, int(HH * 0.22)], radius=14, fill=(0, 0, 0, 210))
    ns = "北緯" if lat0 >= 0 else "南緯"
    ew = "東経" if lon0 >= 0 else "西経"
    lines_txt = [
        f"🛰 {sat_name}",
        f"現在地: {ns} {abs(lat0):.2f}度 / {ew} {abs(lon0):.2f}度（高度 {alt0:.0f} km）",
        f"速度 約{speed0*3600:.0f} km/h ・ 観測時刻 {tstr} UTC",
    ]
    d.text((28, 22), lines_txt[0], font=f_big, fill=(255, 255, 255))
    d.text((28, 22 + int(out_px / 26)), lines_txt[1], font=f_mid, fill=(225, 228, 248))
    d.text((28, 22 + int(out_px / 19)), lines_txt[2], font=f_sm, fill=(200, 210, 240))
    # 現在地ラベル（マーカーと重ならない位置に配置）
    # マーカーから十分離す：右側に余白がなければ左側に置く
    lab_w = int(out_px * 0.24)
    lab_h = int(out_px / 22)
    margin = r * 3  # マーカー半径の3倍以上離す
    if cx + margin + lab_w < W - 20:
        # 右側に配置
        lx, ly = cx + margin, cy - lab_h // 2
    elif cx - margin - lab_w > 20:
        # 左側に配置
        lx, ly = cx - margin - lab_w, cy - lab_h // 2
    else:
        # 上下に配置
        lx, ly = cx - lab_w // 2, cy + margin
    ly = max(20, min(ly, HH - lab_h - 20))
    d.rounded_rectangle([lx, ly, lx + lab_w, ly + lab_h], radius=8, fill=(200, 0, 0, 235))
    d.text((lx + 10, ly + 4), f"{sat_name} 現在位置", font=f_mid, fill=(255, 255, 255))

    # JPEG 化（3.5MB 超は縮小して再エンコード）
    jpeg = encode_jpeg(img)
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{sat_name} の地上軌道")

    text_lines = [
        f"🛰 **{sat_name}** の地上軌道（{tstr}）:",
        f"📍 現在地: {ns} {abs(lat0):.2f}度 / {ew} {abs(lon0):.2f}度（真下の点）",
        f"🛰 高度 {alt0:.0f} km ・ 速度 約{speed0*3600:.0f} km/h",
        f"🛤 軌道トレイル: 前後 {minutes} 分（{step} 分刻み）。オレンジ線=軌道。",
        "出典: CelesTrak TLE + Skyfield(SGP4) ／ 地図 NASA Blue Marble（認証不要）",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(text_lines)), imgc],
        structuredContent={
            "satellite": sat_name, "norad_id": norad_id, "time_utc": tstr,
            "latitude": round(lat0, 4), "longitude": round(lon0, 4),
            "altitude_km": round(alt0, 1), "speed_kmh": round(speed0 * 3600),
            "trail_minutes": minutes, "step_min": step, "trail_points": len(trail),
            "source": "CelesTrak + Skyfield + NASA Blue Marble",
        },
    )

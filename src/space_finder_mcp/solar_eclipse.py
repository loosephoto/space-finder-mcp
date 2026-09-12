"""日食の時系列パネル合成（太陽を月が欠く過程を画像化, 認証不要）。

指定した観測地・日付で、太陽と月の見かけの重なり（日食）を JPL DE421 + Skyfield で
実測計算し、食の始まり〜最大〜終わりを数枚のパネルに合成して返す。サンプル画像の
「部分日食が時系列で並ぶ」レイアウトを再現する。

- 天体位置は JPL DE421 暦表 + Skyfield（ローカル/認証不要）。
- 太陽・月の視角半径と角距離を観測地の視位置（topocentric apparent）で計算。
- 月の位置角（PA, 天の北から東回り）を求め、太陽円盤を正しく欠く位置に月を描画。
- 食の種類を食分から判定: >=0.995 かつ月>=太陽 なら皆既、月<太陽 なら金環、それ以外は部分。

返却: content に base64 画像(ImageContent)、structuredContent に食の種別・最大食分・場所JSON。
出典: JPL DE421 + Skyfield。
"""
from __future__ import annotations

import copy
import datetime
import io
import math
import os
import re
from typing import Optional

from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import TTL_DAILY, ttl_cache
from .img_common import (as_image, figure_notes, figure_payload,
                         figure_text_block, load_font, scale_spec, view_spec)
from .input_utils import as_float

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)

# 太陽・月の実半径 (km)
_R_SUN = 696000.0
_R_MOON = 1737.4

def _local_tz(lat, lon):
    """緯度経度から現地タイムゾーンの UTC オフセット(時間, DST込み)を Open-Meteo で取得。
    失敗時は経度/15 の概算にフォールバック。キャッシュして再呼び出しを避ける。
    """
    lat, lon = as_float(lat, 0.0, -90.0, 90.0), as_float(lon, 0.0, -180.0, 180.0)
    key = (round(lat, 1), round(lon, 1))
    cache = _local_tz.__dict__.setdefault("_c", {})
    if key in cache:
        return cache[key]
    off = float(round(lon / 15.0))
    try:
        import requests as _rq
        rr = _rq.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": lat, "longitude": lon, "timezone": "auto",
                             "current_weather": "false", "forecast_days": 1},
                     headers={"User-Agent": "space-finder-mcp/0.22 (MCP)"}, timeout=20)
        rr.raise_for_status()
        sec = rr.json().get("utc_offset_seconds")
        if sec is not None:
            off = sec / 3600.0
    except Exception:
        pass
    cache[key] = off
    return off


def _tz_label(off):
    return "JST" if abs(off - 9.0) < 0.01 else "UTC{:+g}".format(off)


_KNOWN_COORDS = {
    "東京": (35.68, 139.69), "tokyo": (35.68, 139.69),
    "大阪": (34.69, 135.50), "osaka": (34.69, 135.50),
    "札幌": (43.06, 141.35), "sapporo": (43.06, 141.35),
    "那覇": (26.21, 127.68), "naha": (26.21, 127.68),
    "ニューヨーク": (40.71, -74.01), "new york": (40.71, -74.01),
    "ロンドン": (51.51, -0.13), "london": (51.51, -0.13),
    "シドニー": (-33.87, 151.21), "sydney": (-33.87, 151.21),
    "ハワイ": (19.82, -155.47), "hawaii": (19.82, -155.47),
}


def _load():
    from skyfield.api import Loader
    loader = Loader(_DATA_DIR, verbose=False)
    eph = loader("de421.bsp")
    return loader, eph


def _resolve_place(place, lat, lon):
    """place 文字列 or lat/lon 数値から (lat, lon)。任意の地名は Open-Meteo でジオコーディング。

    lat/lon が数値として解釈できない場合は None を返す（呼び出し側でエラーにする）。
    """
    if lat is not None and lon is not None:
        la, lo = as_float(lat, None, -90.0, 90.0), as_float(lon, None, -180.0, 180.0)
        if la is None or lo is None:
            return None
        return la, lo
    if place:
        key = str(place).strip().lower()
        if key in _KNOWN_COORDS:
            return _KNOWN_COORDS[key]
        # 既知リストにない任意の地名 → オンラインで緯度経度を解決（キャッシュ込み）
        try:
            from .weather_astro import _geocode
            g = _geocode(place)
            if g:
                return float(g["latitude"]), float(g["longitude"])
        except Exception:
            pass
    return None


def _geom(site, t, sun, moon):
    """観測地・時刻で太陽・月の (視角半径deg, 角距離deg, 位置角PA) を返す。"""
    s = site.at(t).observe(sun).apparent()
    m = site.at(t).observe(moon).apparent()
    sep = s.separation_from(m).degrees
    rs = math.degrees(math.asin(_R_SUN / s.distance().km))
    rm = math.degrees(math.asin(_R_MOON / m.distance().km))
    # 位置角 PA(天の北=0 から東回り) で月が太陽のどちら側に見えるか
    s_ra = s.radec()[0]; s_dec = s.radec()[1]
    m_ra = m.radec()[0]; m_dec = m.radec()[1]
    dra = math.radians((m_ra.hours - s_ra.hours) * 15.0)
    sd = math.radians(s_dec.degrees); md = math.radians(m_dec.degrees)
    pa = math.atan2(math.sin(dra),
                    math.cos(sd) * math.tan(md) - math.sin(sd) * math.cos(dra))
    pa = math.degrees(pa) % 360
    return rs, rm, sep, pa


def _eclipse_type(mag, rs, rm):
    if mag >= 0.995:
        return "皆既日食" if rm >= rs else "金環日食"
    if mag >= 0.01:
        return "部分日食"
    return None


def _scan_day(eph, ts, site, sun, moon, y, mo, d, jst_offset):
    """指定日(現地時)に「その観測地で見える」日食を返す。見える食が無ければ None。

    食は現地の日付をまたぐ（例: 23:25 開始 → 翌 00:37 終了）ことがあるため、走査は
    指定日の前後6時間（計36時間）に広げ、可視区間が指定日にかかるものだけを採用する。
    さらに太陽が地平線下の時間帯は物理的に見えないので除外し（高度>0 の分のみ採用）、
    最大食分も可視区間の中で求める。全日食が地平線下なら {"visible": False, ...} を返す。
    """
    jst0 = datetime.datetime(y, mo, d, 0, 0)
    utc0 = jst0 - datetime.timedelta(hours=jst_offset)
    jd_start = ts.utc(utc0.year, utc0.month, utc0.day,
                      utc0.hour, utc0.minute).tt - 6 / 24.0
    occ = []
    for i in range(36 * 60):
        jd = jd_start + i / 1440.0
        tt = ts.tt_jd(jd)
        rs, rm, sep, pa = _geom(site, tt, sun, moon)
        if sep < (rs + rm):
            # 高度は食の時間帯だけ計算する（全36時間分ではコストが高い）
            alt, _az, _ = (sun - site).at(tt).altaz()
            occ.append({"jd": jd, "rs": rs, "rm": rm, "sep": sep, "pa": pa,
                        "alt": float(alt.degrees)})
    if not occ:
        return None
    vis = [o for o in occ if o["alt"] > 0.0]
    if not vis:
        b = min(occ, key=lambda o: o["sep"])
        return {"visible": False,
                "max_mag": max(0.0, (b["rs"] + b["rm"] - b["sep"]) / (2 * b["rs"])),
                "sun_alt_max_deg": round(max(o["alt"] for o in occ), 1),
                "frames": [], "jd_center": b["jd"]}
    first_jd, last_jd = vis[0]["jd"], vis[-1]["jd"]
    best = min(vis, key=lambda o: o["sep"])
    mag = max(0.0, (best["rs"] + best["rm"] - best["sep"]) / (2 * best["rs"]))
    kind = _eclipse_type(mag, best["rs"], best["rm"])
    if kind is None:
        return None
    duration = max(0.05, last_jd - first_jd)
    fracs = [0.0, 0.15, 0.35, 0.5, 0.65, 0.85, 1.0]
    frames = []
    for f in fracs:
        jd_f = first_jd + duration * f
        tt = ts.tt_jd(jd_f)
        rs_f, rm_f, sep_f, pa_f = _geom(site, tt, sun, moon)
        alt_f, _az_f, _ = (sun - site).at(tt).altaz()
        ud = tt.utc_datetime() + datetime.timedelta(hours=jst_offset)
        mag_f = max(0.0, (rs_f + rm_f - sep_f) / (2 * rs_f))
        frames.append({"jd": jd_f, "jst": ud.strftime("%H:%M"),
                       "rs": rs_f, "rm": rm_f, "sep": sep_f, "pa": pa_f,
                       "mag": mag_f, "alt": round(float(alt_f.degrees), 1)})
    return {"jd_center": best["jd"], "max_mag": mag, "kind": kind, "rs": best["rs"],
            "rm": best["rm"], "pa_center": best["pa"], "frames": frames,
            "start_jd": first_jd, "end_jd": last_jd, "visible": True,
            "sun_alt_deg": round(best["alt"], 1)}


# ---------- ツール ----------
@ttl_cache(TTL_DAILY, maxsize=128)
def _scan_day_cached(lat: float, lon: float, y: int, mo: int, d: int,
                     jst_offset: float):
    """指定日の日食判定（約7秒）を緯度経度丸め＋日付のキーでキャッシュする。

    同じ観測地・同じ日付なら結果は変わらない。戻り値は呼び出し側で書き換えるため
    使う側で deepcopy すること。日食が無い場合(None)も決定的なのでキャッシュする。
    """
    loader, eph = _load()
    ts = loader.timescale()
    from skyfield.api import wgs84
    site = eph["earth"] + wgs84.latlon(lat, lon)
    return _scan_day(eph, ts, site, eph["sun"], eph["moon"], y, mo, d, jst_offset)


@ttl_cache(TTL_DAILY, maxsize=64)
def _next_eclipse_search(lat: float, lon: float, start_iso: str, days: int,
                         jst_offset: float):
    """「この観測地でこれから起こる次の日食」探索の結果をキャッシュする。

    _next_eclipse_date は site（Skyfield オブジェクト）を引数に取るため呼び出しごとに
    新しいオブジェクトになり lru_cache が効かない。緯度経度を小数2桁に丸めた値と
    開始日だけをキーにして、約23秒かかる探索を再利用する（TTL 1日）。
    戻り値は呼び出し側で書き換えられるので、使う側で deepcopy すること。
    """
    loader, eph = _load()
    ts = loader.timescale()
    from skyfield.api import wgs84
    site = eph["earth"] + wgs84.latlon(lat, lon)
    return _next_eclipse_date(eph, ts, site, datetime.date.fromisoformat(start_iso),
                             days=days, jst_offset=jst_offset)


def solar_eclipse_series(date: Optional[str] = None, place: Optional[str] = None,
                         lat: Optional[float] = None, lon: Optional[float] = None,
                         max_magnitude: bool = False) -> CallToolResult:
    """日食の時系列パネル画像（太陽が月に欠ける過程）を返す（認証不要）。

    例:「2035年9月2日の皆既日食を画像で」「東京で見える次の日食」「2019年の部分日食の進行」
    指定した観測地・日付で太陽と月の視位置を JPL DE421 + Skyfield で実測計算し、
    食の始まり〜最大〜終わりを複数パネルに並べて合成。月の位置角も正確に反映。

    date を省略した場合は、その観測地で「これから起こる次の日食」を約2年（800日）先まで自動検索。
    描画するのは、その観測地で太陽が地平線より上にある時間帯だけ。最大食分も可視区間の中で
    求める。全日食が地平線下なら「見えません」と明示して図は返さない（見えない食を図にして
    誤解させないため。日付をまたぐ食も前後6時間まで含めて計算する）。
    max_magnitude=True で最大食のみの単一画像を返す（進行でなく最も欠けた瞬間だけ見たい時）。

    Args:
        date: 日付（例 "2035-09-02" / "2035/09/02" / "2035年9月2日"）。省略時は次の日食を検索。
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        max_magnitude: True で最大食のみの単一パネルを返す（既定 False=時系列）。
    """
    ll = _resolve_place(place, lat, lon)
    if ll is None and (lat is not None or lon is not None):
        return CallToolResult(
            content=[TextContent(type="text", text="lat（-90〜90）と lon（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "lat": str(lat), "lon": str(lon)},
        )
    if ll is None:
        ll = (35.68, 139.69)
    place_ja = place if place else "東京"
    # タイムゾーン: 緯度経度から現地の正確なオフセット(DST込み)を取得
    tz = _local_tz(ll[0], ll[1])
    tz_label = _tz_label(tz)

    loader, eph = _load()
    ts = loader.timescale()
    from skyfield.api import wgs84
    site = eph["earth"] + wgs84.latlon(ll[0], ll[1])
    sun = eph["sun"]; moon = eph["moon"]

    dates = []
    date_str = ""
    if date:
        m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(date).strip())
        if not m:
            return CallToolResult(content=[TextContent(type="text",
                text="date の形式が不正です（例: 2035-09-02 または 2035年9月2日）")],
                structuredContent={"error": "bad date"})
        dates = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))]
        date_str = "{}年{}月{}日".format(*dates[0])
        date_disp = date_str
    else:
        # 次の日食を自動検索（新月近傍のみ調べて高速化。約23秒かかるため
        # 緯度経度を丸めたキーで1日キャッシュし、戻り値は後段で書き換えるので deepcopy）
        days_win = 800
        res = copy.deepcopy(_next_eclipse_search(
            round(ll[0], 2), round(ll[1], 2),
            datetime.datetime.now().date().isoformat(), days_win, round(float(tz), 2)))
        day_found, found = res if res else (None, None)
        dy, dmo, dd = day_found if day_found else (None, None, None)
        if not found:
            return CallToolResult(content=[TextContent(type="text",
                text="今後約2年（{}日）にこの観測地で見える日食が見つかりませんでした。"
                 "特定の日付を調べるには date=\"2030-06-01\" のように指定してください".format(
                     days_win))],
                structuredContent={"error": "no eclipse found"})
        date_str = "{}年{}月{}日".format(dy, dmo, dd)
        date_disp = date_str
        found["date_str"] = date_str
        found["tz_label"] = tz_label
        if max_magnitude:
            c = found["frames"][len(found["frames"]) // 2]
            found["frames"] = [c]
        return _finalize(found, place_ja, date_disp, ll)
    ev = None
    for (y, mo, d) in dates:
        # 指定日の判定も約7秒かかるため、丸めた緯度経度＋日付のキーで再利用
        ev = copy.deepcopy(_scan_day_cached(
            round(ll[0], 2), round(ll[1], 2), y, mo, d, round(float(tz), 2)))
        if ev:
            break
    if ev is not None and not ev.get("visible", True):
        return CallToolResult(content=[TextContent(type="text",
            text="{} はこの観測地（緯度{:.2f}° 経度{:.2f}°）で日食がありますが、"
                 "太陽が地平線下（最大高度 {:.1f}°）のため見えません".format(
                     date_disp, ll[0], ll[1], ev.get("sun_alt_max_deg", 0.0)))],
            structuredContent={"error": "eclipse below horizon", "place": place_ja,
                               "date": date_disp, "lat": ll[0], "lon": ll[1],
                               "sun_alt_max_deg": ev.get("sun_alt_max_deg"),
                               "max_magnitude": ev.get("max_mag")})
    if ev is None:
        return CallToolResult(content=[TextContent(type="text",
            text="{} にこの観測地（緯度{:.2f}° 経度{:.2f}°）で日食はありません".format(
                date_disp, ll[0], ll[1]))],
            structuredContent={"error": "no eclipse on date", "place": place_ja,
                               "date": date_disp, "lat": ll[0], "lon": ll[1]})
    ev["date_str"] = date_str
    ev["tz_label"] = tz_label
    if max_magnitude:
        c = ev["frames"][len(ev["frames"]) // 2]
        ev["frames"] = [c]
    return _finalize(ev, place_ja, date_disp, ll)



# ---------- 描画 ----------
def _draw_sun_moon(dr, img, cx, cy, R, rs, rm, sep, pa, mag, kind):
    """太陽と月を1パネルに描く。R=太陽画素半径。"""
    from PIL import Image, ImageDraw, ImageFilter
    # 太陽の光背（文字は最前面レイヤーで後から描くので、光彩を大きく明るくしても隠れない）
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - R * 1.3, cy - R * 1.3, cx + R * 1.3, cy + R * 1.3],
               fill=(255, 205, 90, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    dr = ImageDraw.Draw(img)
    # コロナ（皆既近傍）
    if mag >= 0.99:
        for rr in (1.3, 1.55, 1.85):
            dr.ellipse([cx - R * rr, cy - R * rr, cx + R * rr, cy + R * rr],
                       outline=(255, 240, 210, 255) if rr < 1.4 else (255, 232, 200, 200), width=2)
    # 太陽円盤
    dr.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(255, 216, 84), outline=(246, 190, 55), width=4)
    # 粒状斑
    import random
    random.seed(int(cx))
    for _ in range(6):
        a = random.uniform(0, 2 * math.pi); rr = random.uniform(0, R * 0.55)
        px, py = cx + rr * math.cos(a), cy + rr * math.sin(a)
        dr.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(248, 198, 92))
    # 月の位置
    px_per_deg = R / rs
    mr = R * (rm / rs)
    off = sep * px_per_deg
    mx = cx + off * math.sin(math.radians(pa))
    my = cy - off * math.cos(math.radians(pa))
    dr.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(10, 10, 16))
    # 北マーク(小さく上)
    dr.polygon([(cx, cy - R - 40), (cx - 8, cy - R - 54), (cx + 8, cy - R - 54)], fill=(220, 225, 245))


def _render_panels(ev, place_ja, R=150):
    from PIL import Image, ImageDraw
    frames = ev["frames"]
    N = len(frames)
    PANEL = 430
    HDR = 150
    W = N * PANEL
    H = HDR + 520
    canvas = Image.new("RGB", (W, H), (7, 9, 22))
    dd = ImageDraw.Draw(canvas)
    # ---- 第1層: 背景の星（最初に撒き、太陽・月が後から覆う）----
    import random as _rnd
    _rnd.seed(1234)
    for _ in range(240):
        sx, sy = _rnd.randint(0, W - 1), _rnd.randint(130, H - 1)
        br = _rnd.randint(140, 235)
        dd.point((sx, sy), fill=(br, br, br + 10))
        if _rnd.random() < 0.25:
            dd.line([sx - 2, sy, sx + 2, sy], fill=(br, br, br + 10))
            dd.line([sx, sy - 2, sx, sy + 2], fill=(br, br, br + 10))
    # ---- 第2層: 各パネルのグラフィック（太陽・光背・月・北マーク）----
    Nn = len(frames)
    i_max = max(range(Nn), key=lambda k: frames[k]["mag"])   # 最大食分のパネル
    for i, fr in enumerate(frames):
        x0 = i * PANEL
        cy = HDR + 190
        # パネルごとにレイヤーへ描いてから切り貼りする。直接キャンバスに描くと
        # 月や光背が隣のパネルへはみ出し、隣の太陽を覆ってしまう（実測で欠けが出た）
        layer = Image.new("RGBA", (PANEL, H), (0, 0, 0, 0))
        _draw_sun_moon(ImageDraw.Draw(layer), layer, PANEL // 2, cy, R, fr["rs"], fr["rm"],
                       fr["sep"], fr["pa"], fr["mag"], ev["kind"])
        canvas.paste(layer, (x0, 0), layer)
        if i > 0:
            dd = ImageDraw.Draw(canvas)
            dd.line([x0, 128, x0, H], fill=(44, 54, 84), width=2)
    # ---- 第3層（最前面）: すべての文字・ラベルを最後に描く（グラフィックに隠れない）----
    dd = ImageDraw.Draw(canvas)
    # ヘッダー背景
    dd.rectangle([0, 0, W, 128], fill=(0, 0, 0, 235))
    # タイトル・説明
    dd.text((26, 16), "{}の進行  {}  {}".format(ev["kind"], ev["date_str"], place_ja),
            font=load_font(27, True), fill=(255, 255, 255, 255))
    dd.text((26, 68), "最大食分 {:.3f}（{}%）・時刻は現地時間 ・ ▲ は天の北方向".format(
        ev["max_mag"], int(ev["max_mag"] * 100)), font=load_font(19), fill=(205, 215, 240, 255))
    # 各パネルの下部キャプション帯（段階名 + 時刻・食分）を文字ごと最後に描く
    for i, fr in enumerate(frames):
        x0 = i * PANEL
        cx = x0 + PANEL // 2
        cy = HDR + 190
        if Nn == 1:
            stag = "最大食分"
        elif i == 0:
            stag = "始まり"
        elif i == i_max:
            stag = "最大食分"
        elif i == Nn - 1:
            stag = "終わり"
        else:
            stag = ""
        label = "{} {}".format(fr["jst"], ev.get("tz_label", ""))
        if fr["mag"] >= 0.005:
            label += "  食分 {:.2f}".format(fr["mag"])
        head_txt = "{}枚目".format(i + 1)
        if stag:
            head_txt += "・" + stag
        by0 = cy + R + 30
        dd.rectangle([x0 + 12, by0, x0 + PANEL - 12, by0 + 78], fill=(0, 0, 0, 210))
        dd.text((x0 + 20, by0 + 8), head_txt, font=load_font(18, True), fill=(170, 195, 235))
        dd.text((x0 + 20, by0 + 38), label, font=load_font(20, True), fill=(255, 255, 255, 255))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


# ---------- 描画の自己検証（figure/1 の verify 用）----------
_SUN_R_PX = 150          # _render_panels の既定 R（太陽の画素半径）
_PANEL_W = 430           # 1パネルの幅
_HDR_H = 150             # ヘッダー帯の高さ


def _eclipse_verify(png, ev, R=_SUN_R_PX):
    """各パネルの画素から食分を再計算し、そのパネルの報告値と突き合わせる。

    PA（位置角）軸に沿って太陽の明部の長さ L を実測すると
    L = R + d - Rm（d=中心間距離, Rm=月の画素半径）なので、描画側の定義
    食分 = (Rs + Rm - d) / (2Rs) は 1 - L / (2R) として厳密に逆算できる。
    月が描かれていなければ L=2R（食分0）、パネル欠けは明部ラン0本で落ちる。
    """
    img = as_image(png).convert("RGB")
    W, H = img.size
    frames = ev["frames"]
    n = len(frames)
    if not n:
        return {"ok": False, "panels": 0}

    def bright(x, y):
        if not (0 <= x < W and 0 <= y < H):
            return False
        r, g, b = img.getpixel((int(x), int(y)))
        return r > 200 and g > 160 and b < 150

    def axis_runs(idx, pa_deg):
        """idx 番目のパネルの太陽中心を通る PA 軸上の明部ラン（px）を返す。"""
        cx = idx * _PANEL_W + _PANEL_W // 2
        cy = _HDR_H + 190
        th = math.radians(pa_deg)
        dx, dy = math.sin(th), -math.cos(th)
        # 走査は太陽円盤の範囲（±R）に限る。円盤の外側には「光背」が
        # (255,205,90) で明るく描かれるため、外まで走査すると弦を長く測ってしまう。
        runs, start, t = [], None, -R
        while t <= R:
            if bright(cx + dx * t, cy + dy * t):
                if start is None:
                    start = t
            else:
                if start is not None:
                    runs.append(t - start)      # 画素数ではなく距離(px)で数える
                    start = None
            t += 0.5
        if start is not None:
            runs.append(R - start)
        return runs

    per = []
    for i, f in enumerate(frames):
        runs = axis_runs(i, f["pa"])
        L = max(runs) if runs else 0
        mag_px = max(0.0, min(1.0, 1.0 - L / (2.0 * R)))
        per.append({"panel": i, "runs": len(runs), "axis_bright_px": L,
                    "magnitude_from_pixels": round(mag_px, 3),
                    "magnitude_reported": round(float(f["mag"]), 3),
                    "error": round(abs(mag_px - float(f["mag"])), 4)})
    worst = max(p["error"] for p in per)
    data_err = abs(max(float(f["mag"]) for f in frames) - float(ev["max_mag"]))
    out = {"ok": False, "panel_count": n, "sun_radius_px": R,
           "worst_magnitude_error": worst,
           "max_magnitude_reported": round(float(ev["max_mag"]), 3),
           "frames_max_vs_reported_error": round(data_err, 4),
           "panel_max_index": max(range(n), key=lambda i: float(frames[i]["mag"])),
           "panels": per}
    out["ok"] = bool(all(p["runs"] == 1 for p in per) and worst <= 0.02 and data_err <= 0.02)
    return out


def _alt_note(ev):
    """太陽高度と継続時間の注記を数値から生成する（手書きすると図と食い違う）。"""
    frames = ev.get("frames") or []
    if not frames:
        return "パネルは可視区間のみ（この日食はこの観測地で見えません）"
    imax = max(range(len(frames)), key=lambda k: frames[k]["mag"])
    span = max(0.0, (ev.get("end_jd", 0.0) - ev.get("start_jd", 0.0)) * 1440.0)
    return ("太陽高度は 始まり {:.1f}° → 最大 {:.1f}° → 終わり {:.1f}°（{}枚目が最大食分）。"
            "可視区間 {:.0f} 分。地平線下の時間帯は描かず、見えない日食は返さない".format(
                frames[0].get("alt", 0.0), frames[imax].get("alt", 0.0),
                frames[-1].get("alt", 0.0), imax + 1, span))

def _finalize(ev, place_ja, date_disp, ll):
    import base64
    try:
        png = _render_panels(ev, place_ja)
    except Exception as e:
        return CallToolResult(content=[TextContent(type="text",
            text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]})
    img = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                       mimeType="image/png", altText="{} {}".format(ev["kind"], place_ja))
    fig = figure_payload(
        kind="eclipse_panels",
        title="{}（{}）の時系列パネル".format(ev["kind"], place_ja),
        view=view_spec("local_sky", "sun_disk_panels",
                       "観測地から見た太陽面を同一倍率で時系列に並べた図（食の始まり〜最大〜終わり）",
                       why="日食は観測地ごとに見え方が違う現象なので、空に見える太陽と月の"
                           "重なりとして描く"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["太陽・月は見かけの視角で描画（パネル間の距離スケールはない）"]),
        notes=figure_notes(extra=[
            "各パネルは同じ観測地・同じ倍率の太陽面。パネル下の時刻はその場所のタイムゾーン（DST込み）",
            "▲は天の北。月は位置角(PA)を反映した方向から太陽に重なる",
            "種別（皆既/金環/部分）は最大食分から判定（0.995以上で月視半径と太陽視半径を比較）",
            "食の見え方は観測地依存（この図は {} の値）。他の地点では食分・時刻が変わる".format(place_ja),
            "太陽・月以外（地球の影・軌道の形）は描いていない",
            _alt_note(ev),
        ]),
        caption="{} の {} における {}。最大食分 {:.3f}（{}%）、太陽高度 {:.0f}°（最大時）。".format(
            date_disp, place_ja, ev["kind"], ev["max_mag"], int(ev["max_mag"] * 100),
            ev.get("sun_alt_deg") or 0.0),
        verify=_eclipse_verify(png, ev),
    )
    lines = [
        "🌞 **{}（{} ・ {}）**".format(ev["kind"], place_ja, date_disp),
        "最大食分: {:.3f}（{}%）".format(ev["max_mag"], int(ev["max_mag"] * 100)),
        "",
        figure_text_block(fig),
        "画像は上に表示（base64 PNG）。出典: JPL DE421 + Skyfield",
    ]
    return CallToolResult(content=[TextContent(type="text", text="\n".join(lines)), img],
                          structuredContent={"kind": ev["kind"], "max_magnitude": ev["max_mag"],
                                             "date": date_disp, "place": place_ja,
                                             "lat": ll[0], "lon": ll[1],
                                             "figure": fig,
                                             "source": "JPL DE421+Skyfield"})




def _next_eclipse_date(eph, ts, site, start_date, days=800, jst_offset=9.0):
    """start_date から days 日先で日食が起こる最初の (date, event) を返す。無ければ (None,None)。

    日食は新月(朔)にしか起きない。Skyfield almanac で新月時刻を列挙し、各新月の前後6時間を
    10分刻みで高速判定して「太陽と月が重なる新月」を先に絞り、絞れた新月だけを精密
    _scan_day で処理する。新月全数の約1/3に満たない高速判定(0.5s/回)で済み、1年分でも速い。
    """
    from skyfield import almanac
    import datetime as _dt
    sun = eph["sun"]; moon = eph["moon"]
    rs0 = math.degrees(math.asin(_R_SUN / (149597870.0 - 384400)))   # ~0.266
    rm0 = math.degrees(math.asin(_R_MOON / 384400.0))                 # ~0.259
    touch = rs0 + rm0
    start = ts.utc(start_date.year, start_date.month, start_date.day, 0)
    end = ts.utc(start_date.year, start_date.month, start_date.day + days, 0)
    phase_fn = almanac.moon_phases(eph)
    try:
        times, phases = almanac.find_discrete(start, end, phase_fn)
    except Exception:
        return None, None
    for tt, p in zip(times, phases):
        is_new = (abs(p) < 1e-3) or (abs(p - 1.0) < 1e-3)
        if not is_new:
            continue
        jd = tt.tt
        # 高速判定: 前後6時間 10分刻み
        best = 9e9
        for k in range(-36, 37):
            tt2 = ts.tt_jd(jd + k * 10 / 1440.0)
            rs, rm, sep, pa = _geom(site, tt2, sun, moon)
            if sep < best:
                best = sep
        if best < touch:
            # 食の恐れ → 精密スキャン（新月の現地日付前後）
            ud = tt.utc_datetime()
            for off in (-1, 0, 1):
                dd = ud + _dt.timedelta(days=off)
                ev = _scan_day(eph, ts, site, sun, moon,
                               dd.year, dd.month, dd.day, jst_offset)
                if ev and not ev.get("visible", True):
                    ev = None      # 地平線下で見えない食は「次の日食」に数えない
                if ev:
                    return (dd.year, dd.month, dd.day), ev
    return None, None

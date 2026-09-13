"""星空マップ＋人工衛星オーバーレイ（描画エンジン選択式, 認証不要）。

指定した観測地・時刻の「空」を画像化し、その上に人工衛星の現在位置を重ねる。
天体位置は NASA JPL de421 + Skyfield、衛星位置は CelesTrak TLE + SGP4 で計算（全てローカル/認証不要）。

2 つの描画エンジンを実装し、呼び出し側(LLM)が目的に応じて選択できる:
- engine="accurate" (matplotlib): 正確な星空を科学プロットで描画。星・惑星・衛星を
  実際の座標に精確に配置し、方位・仰角グリッド、軌道予測線も表示。教育・科学的用途向け。
- engine="simple"   (Pillow): 実写(銀河等)を背景にした簡易・観賞用合成。見た目重視。
- engine="auto":     要求に応じて自動選択（位置の正確さを問うなら accurate、見た目なら simple）。

返却: content に base64 画像(ImageContent)、structuredContent に計算した座標JSON。
"""
from __future__ import annotations

import io
import math
import os

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import disk_get
from .celestrak import fetch_tle
from .img_common import (as_image, body_rgb, encode_jpeg, figure_notes,
                         figure_payload, figure_text_block, load_font,
                         media_link_line, pixel_near, primary_spec, rgb_hex,
                         save_output, scale_spec, symbol_rgb, view_spec)
from .input_utils import as_float

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)
UA = {"User-Agent": "space-finder-mcp/0.19 (MCP; sky overlay)"}

# 注目衛星 (LEOは動き・軌道線を見せる、静止は定点表示)
_SAT_CATALOG = [
    ("ISS (国際宇宙ステーション)", 25544, "leo"),
    ("天宮 (中国宇宙ステーション)", 48274, "leo"),
    ("ハッブル宇宙望遠鏡", 20580, "leo"),
    ("NOAA-20 (気象衛星)", 43013, "leo"),
    ("ひまわり9号 (静止気象衛星)", 41836, "geo"),
    ("みちびき4号 (準天頂衛星)", 42917, "geo"),
]
_BRIGHT_STARS = [
    ("シリウス", 6.7525, -16.716), ("カノープス", 6.3996, -52.696),
    ("アルクトゥルス", 14.2611, 19.182), ("ベガ", 18.6156, 38.784),
    ("カペラ", 5.2773, 45.998), ("リゲル", 5.2423, -8.202),
    ("プロキオン", 7.6550, 5.225), ("ベテルギウス", 5.9193, 7.407),
    ("アルタイル", 19.8464, 8.868), ("アルデバラン", 4.5984, 16.509),
    ("アンタレス", 16.4899, -26.432), ("スピカ", 13.4201, -11.161),
    ("ポルックス", 7.7553, 28.026), ("フォーマルハウト", 22.9610, -29.622),
    ("デネブ", 20.6919, 45.280), ("レグルス", 10.1396, 11.967),
]
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

# 惑星・月の見た目: 名前 -> (主色RGB, 分類)。色は img_common.BODY_COLORS（共通の
# 単一の出典）から引き、分類（表示サイズ用）だけをここで持つ。
_PLANET_VISUAL = {
    "月":   (body_rgb("月"),   "moon"),
    "水星": (body_rgb("水星"), "rocky"),
    "金星": (body_rgb("金星"), "rocky"),
    "火星": (body_rgb("火星"), "rocky"),
    "木星": (body_rgb("木星"), "gas"),
    "土星": (body_rgb("土星"), "gas"),
    "天王星": (body_rgb("天王星"), "gas"),
    "海王星": (body_rgb("海王星"), "gas"),
}
# 岩石惑星の種類別アイコン (火星はクレーター風・金星は雲、水星はクレーター) を色分けで表現するため
# 描画では color で区別 + 土星に環、木星に縞を描く。

_BG_CANDIDATES = [
    "https://images-assets.nasa.gov/image/PIA04921/PIA04921~medium.jpg",
    "https://images-assets.nasa.gov/image/PIA14102/PIA14102~medium.jpg",
    "https://images-assets.nasa.gov/image/54054828189_08b2de91bc_o/54054828189_08b2de91bc_o~medium.jpg",
]
# ---------- 共通データ層 ----------
def _load():
    from skyfield.api import Loader, Star, wgs84, EarthSatellite
    loader = Loader(_DATA_DIR, verbose=False)
    eph = loader("de421.bsp")
    return loader, eph, Star, wgs84, EarthSatellite


def _fetch_tle(catnr):
    """CelesTrak から TLE 2行 (line1, line2) を取得（失敗時は None）。"""
    try:
        tle = fetch_tle(norad_id=catnr)
    except requests.RequestException:
        return None
    return (tle[1], tle[2]) if tle else None


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


def _compute(lat, lon, when_iso=None):
    """観測地・時刻から、見える天体/恒星/衛星の (az, alt) と衛星の軌道を計算。"""
    _, eph, Star, wgs84, EarthSatellite = _load()
    import skyfield.api as sk
    ts = sk.load.timescale()
    topos = wgs84.latlon(lat, lon)

    if when_iso:
        # ISO8601 を UTC として解釈し ts.utc へ（Skyfield の ts に from_iso は無い版があるため）
        iso = str(when_iso).strip().replace("Z", "+00:00")
        import re
        m = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?",
                     iso)
        if not m:
            return {"time_utc": str(when_iso), "lat": lat, "lon": lon,
                    "planets": {}, "stars": {}, "satellites": {},
                    "error": "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"}
        y, mo, d, h, mi, s_ = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
        t = ts.utc(y, mo, d, h, mi, s_)
    else:
        t = ts.now()
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")

    def _body_aa(name_vec):
        p = (eph["earth"] + topos).at(t).observe(name_vec).apparent()
        alt, az, _ = p.altaz()
        return float(az.degrees), float(alt.degrees)

    bodies = {"月": eph["moon"], "水星": eph["mercury"], "金星": eph["venus"],
              "火星": eph["mars"], "木星": eph["jupiter barycenter"],
              "土星": eph["saturn barycenter"], "天王星": eph["uranus barycenter"],
              "海王星": eph["neptune barycenter"]}
    planets = {}
    body_errors = {}
    for nm, b in bodies.items():
        try:
            az, alt = _body_aa(b)
            if alt > 0:
                planets[nm] = {"az": az, "alt": alt}
        except Exception as e:
            # 黙って落とすと「今夜はその惑星が見えない」と誤解させる。理由を残して content に出す。
            body_errors[nm] = "{}: {}".format(type(e).__name__, str(e)[:100])

    stars = {}
    for nm, rah, decd in _BRIGHT_STARS:
        try:
            az, alt = _body_aa(Star(ra_hours=rah, dec_degrees=decd))
            if alt > 0:
                stars[nm] = {"az": az, "alt": alt}
        except Exception as e:
            body_errors[nm] = "{}: {}".format(type(e).__name__, str(e)[:100])

    sats = {}
    sat_errors = {}
    for nm, catnr, kind in _SAT_CATALOG:
        try:
            tle = _fetch_tle(catnr)
            if not tle:
                continue
            sat = EarthSatellite(*tle)

            def _sat_aa(tt):
                p = sat.at(tt) - topos.at(tt)
                alt, az, _ = p.altaz()
                return float(az.degrees), float(alt.degrees)

            az0, alt0 = _sat_aa(t)
            # 軌道: 観測時刻の前後 60 分を 3 分刻みで、地平線より上だけ記録
            # 先頭=昇ってくる側(過去)、後方=沈む側(未来)。ピーク時刻でもパス全体が見える。
            trail = []
            if kind == "leo":
                # 前後 _TRAIL_MINUTES 分を _TRAIL_STEP_MIN 分刻み。粗い刻みだと LEO の
                # 可視パスが数点しか取れず、破線が描かれていないように見えてしまう。
                for i in range(-_TRAIL_MINUTES, _TRAIL_MINUTES + 1, _TRAIL_STEP_MIN):
                    az, alt = _sat_aa(ts.tt_jd(t.tt + i * _TRAIL_STEP_MIN * 60.0 / 86400.0))
                    if alt > 0:
                        trail.append({"az": az, "alt": alt})
            if alt0 > 0:
                sats[nm] = {"catnr": catnr, "kind": kind, "az": az0, "alt": alt0, "trail": trail}
        except Exception as e:
            # 黙って落とすと「図に衛星が出ない」だけの症状になり原因が追えない。理由を残す。
            sat_errors[nm] = "{}: {}".format(type(e).__name__, str(e)[:120])

    return {"time_utc": tstr, "lat": lat, "lon": lon, "planets": planets,
            "stars": stars, "satellites": sats, "satellite_errors": sat_errors,
            "body_errors": body_errors}


# ---------- 色・記号の指定（両エンジン共通の単一の出典） ----------
# ここを直せば matplotlib 版と Pillow 版の両方に反映される。
# 手書きで色を散らすと「図の色と説明が食い違う」事故になる。
_C_STAR = (210, 214, 235)        # 恒星
_C_SAT = (255, 60, 50)           # 人工衛星のマーカー
_C_SAT_LABEL = (255, 176, 166)   # 人工衛星の名札
_C_TRAIL = (255, 138, 128)       # 軌道予測の破線
_C_GRID = (80, 90, 130)          # 方位線・仰角リング
_C_COMPASS = (210, 220, 245)     # 北/東/南/西の文字
_C_RING = symbol_rgb("ring")     # 土星の環（img_common の共通記号色）
_C_BAND = symbol_rgb("band")     # 木星の縞
_C_CAP = symbol_rgb("cap")       # 火星の極冠
_ACC_BG = (11, 16, 38)           # accurate: 空の地色
_ACC_FRAME = (4, 6, 15)          # accurate: 図の外枠
_ACC_GRID = (51, 51, 68)         # accurate: 仰角リング/目盛り
_ACC_RIM = (136, 153, 187)       # accurate: 地平線
# 人工衛星の軌道予測（破線）を描く範囲と刻み（分）
_TRAIL_MINUTES = 60
_TRAIL_STEP_MIN = 1


def _planet_hex(name):
    """惑星・月の指定色（img_common.BODY_COLORS）を #rrggbb で返す。

    matplotlib 版・Pillow 版・凡例・figure.markers・注記の色はすべてこの 1 か所から引く。
    色を手書きすると「accurate だけ全惑星が青」のような食い違いが生まれる。
    """
    return rgb_hex(body_rgb(name))


# accurate 版のマーカー径（simple 版の px 径 r0 を pt 面積へ換算したもの）
_PLANET_PT = {"moon": 490, "rocky": 650, "gas": 1045}   # simple: 26 / 30 / 38 px
_SAT_PT = 165                                           # simple: 15 px


def _render_accurate(scene):
    """matplotlib 版。座標は正確なまま、配色・記号・名札は simple 版と同一の指定にする。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    from matplotlib.lines import Line2D
    from matplotlib.transforms import offset_copy
    for f in font_manager.fontManager.ttflist:
        if f.name in ("Noto Sans JP", "Meiryo", "Yu Gothic"):
            plt.rcParams["font.family"] = f.name
            break
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(9.5, 9.5))
    ax = fig.add_subplot(111, polar=True)
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rlim(0, 90)
    ax.set_yticklabels([])
    for g in (30, 60):
        ax.plot(np.linspace(0, 2 * np.pi, 100), np.full(100, g), color=rgb_hex(_ACC_GRID), lw=0.6, ls=":")
    ax.plot(np.linspace(0, 2 * np.pi, 100), np.full(100, 89), color=rgb_hex(_ACC_RIM), lw=2)
    ax.set_facecolor(rgb_hex(_ACC_BG))
    fig.patch.set_facecolor(rgb_hex(_ACC_FRAME))

    drawn_trail = False
    for nm, s in scene["stars"].items():
        ax.scatter(math.radians(s["az"]), s["alt"], s=14, color=rgb_hex(_C_STAR),
                   marker="*", zorder=2, alpha=0.9)
    for nm, s in scene["planets"].items():
        th, r = math.radians(s["az"]), s["alt"]
        kind = _PLANET_VISUAL.get(nm, ((200, 200, 210), "rocky"))[1]
        size = _PLANET_PT.get(kind, _PLANET_PT["rocky"])
        # 光背（視認性）→ 本体 → 表面の特徴 → 名札 の順に重ねる（文字は最後＝隠れない）
        ax.scatter(th, r, s=size * 1.34, color=_planet_hex(nm), alpha=0.28,
                   edgecolor="none", zorder=4)
        ax.scatter(th, r, s=size, color=_planet_hex(nm), edgecolor="white", lw=0.8, zorder=5)
        if nm == "土星":                       # 環
            ax.scatter(th, r, s=size * 2.1, facecolor="none", edgecolor=rgb_hex(_C_RING),
                       lw=1.4, zorder=6)
        elif nm == "木星":                     # 縞（本体の上下に細線）
            for dy in (7, -7):
                tr = offset_copy(ax.transData, fig=fig, y=dy, units="points")
                ax.plot([th], [r], marker="_", ms=9, mew=2.6, color=rgb_hex(_C_BAND),
                        transform=tr, zorder=6)
        elif nm == "火星":                     # 極冠
            tr = offset_copy(ax.transData, fig=fig, y=7, units="points")
            ax.plot([th], [r], marker="o", ms=3.4, color=rgb_hex(_C_CAP), transform=tr, zorder=6)
        ax.annotate(nm, (th, r), textcoords="offset points", xytext=(11, 9), fontsize=11,
                    color="white", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="#00000099", ec="none"))
    for nm, s in scene["satellites"].items():
        az, alt = math.radians(s["az"]), s["alt"]
        ax.scatter(az, alt, s=_SAT_PT * 2.3, color=rgb_hex(_C_SAT), alpha=0.30,
                   edgecolor="none", zorder=7)
        ax.scatter(az, alt, s=_SAT_PT, color=rgb_hex(_C_SAT), edgecolor="white", lw=1.4, zorder=8)
        ax.annotate(nm, (az, alt), textcoords="offset points", xytext=(14, 15),
                    fontsize=10, color=rgb_hex(_C_SAT_LABEL), fontweight="bold", zorder=9,
                    bbox=dict(boxstyle="round,pad=0.22", fc="#4a0000d9", ec=rgb_hex(_C_SAT)))
        if s.get("trail"):
            drawn_trail = True
            ax.plot([math.radians(p["az"]) for p in s["trail"]], [p["alt"] for p in s["trail"]],
                    color=rgb_hex(_C_TRAIL), lw=2.2, alpha=0.95, ls="--", zorder=6)
    for lbl, deg in (("北", 0), ("東", 90), ("南", 180), ("西", 270)):
        ax.text(math.radians(deg), 99, lbl, ha="center", va="center", fontsize=15,
                color=rgb_hex(_C_COMPASS), fontweight="bold")
    # 凡例は実際に描いたものだけを指定色表から生成する（手書きだと図と食い違う）
    legend_items = [
        (Line2D([], [], marker="o", ls="", markersize=7, markerfacecolor=_planet_hex(nm),
                markeredgecolor="white"), "{} {}".format(nm, _planet_hex(nm)))
        for nm in scene["planets"]]
    legend_items.append(
        (Line2D([], [], marker="o", ls="", markersize=9, markerfacecolor=rgb_hex(_C_SAT),
                markeredgecolor="white"), "人工衛星"))
    legend_items.append(
        (Line2D([], [], marker="*", ls="", markersize=8, markerfacecolor=rgb_hex(_C_STAR),
                markeredgecolor="none"), "恒星"))
    if drawn_trail:
        legend_items.append(
            (Line2D([], [], ls="--", color=rgb_hex(_C_TRAIL)),
             "軌道予測（前後{}分・{}分刻み）".format(_TRAIL_MINUTES, _TRAIL_STEP_MIN)))
    lg = ax.legend([h for h, _ in legend_items], [t for _, t in legend_items],
                   loc="lower left", fontsize=8.5, facecolor=rgb_hex(_ACC_BG),
                   edgecolor=rgb_hex(_ACC_GRID), labelcolor="white", framealpha=0.85)
    lg.set_zorder(10)
    ax.set_title("{} ・ {} の空と人工衛星（正確な星図）".format(scene["lat"], scene["time_utc"]),
                 fontsize=12, color="white", pad=20)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------- 描画エンジン B: Pillow (簡易・実写合成) ----------
def _fetch_bg(url, max_bytes=3500000):
    """背景の実写画像を取得（ディスクキャッシュ経由。NASA 資産は不変）。"""
    return disk_get(url, subdir="bg", timeout=30, headers=UA, max_bytes=max_bytes)


def _render_simple(scene):
    """Pillow 簡易合成。惑星を種類別アイコンで、衛星を強調して描く（学生向け・視認性重視）。"""
    from PIL import Image, ImageDraw, ImageFilter
    # 惑星の見た目・恒星・人工衛星の色は _PLANET_VISUAL / _C_* の指定色（accurate 版と共通）。
    # 背景取得
    url = _BG_CANDIDATES[0]
    data = _fetch_bg(url)
    if not data:
        bg_img = Image.new("RGB", (1400, 1400), (4, 6, 15))
    else:
        bg_img = Image.open(io.BytesIO(data)).convert("RGB")
    sq = min(bg_img.size)
    bg_img = bg_img.crop(((bg_img.width - sq) // 2, (bg_img.height - sq) // 2,
                          (bg_img.width + sq) // 2, (bg_img.height + sq) // 2))
    W = H = 1400
    bg_img = bg_img.resize((W, H), Image.LANCZOS)
    bg_img = bg_img.point(lambda p: int(p * 0.32))  # 暗くして天体を際立たせる
    canvas = bg_img.convert("RGBA")
    dr = ImageDraw.Draw(canvas)
    CX = CY = W // 2
    R = 560

    def proj(az, alt):
        r = R * (90 - alt) / 90.0
        th = math.radians(az)
        return CX + r * math.sin(th), CY - r * math.cos(th)

    # 全天円（円外を黒）
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([CX - R, CY - R, CX + R, CY + R], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(2))
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    canvas.paste(black, (0, 0), Image.eval(mask, lambda v: 255 - v))

    # 仰角リング
    for g in (30, 60):
        r = R * (90 - g) / 90.0
        dr.ellipse([CX - r, CY - r, CX + r, CY + r], outline=_C_GRID + (255,), width=2)
    # 方位線 + N/E/S/W
    for az, lab in ((0, "北"), (90, "東"), (180, "南"), (270, "西")):
        x, y = proj(az, 0)
        dr.line([CX, CY, x, y], fill=_C_GRID + (255,), width=2)
        x2, y2 = CX + (x - CX) * 1.07, CY + (y - CY) * 1.07
        dr.text((x2 - 14, y2 - 12), lab, font=load_font(28, True), fill=_C_COMPASS + (255,))

    # 恒星（薄い点・小さめ）
    for nm, s in scene["stars"].items():
        x, y = proj(s["az"], s["alt"])
        dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=_C_STAR + (220,))

    # 惑星・月（種類別アイコン）
    def draw_planet(name, px, py, kind):
        color = body_rgb(name)   # 指定色（accurate 版と共通）
        if kind == "moon":
            r0 = 26
        elif kind == "rocky":
            r0 = 30
        else:
            r0 = 38  # ガス惑星を大きく
        # 光背（発光）で視認性UP
        dr.ellipse([px - r0 - 6, py - r0 - 6, px + r0 + 6, py + r0 + 6], fill=(color[0], color[1], color[2], 70))
        dr.ellipse([px - r0, py - r0, px + r0, py + r0], fill=color + (255,))
        # 土星: 環
        if name == "土星":
            dr.ellipse([px - r0 - 16, py - 8, px + r0 + 16, py + 8], outline=_C_RING + (255,), width=5)
        # 木星: 縞
        elif name == "木星":
            dr.line([px - r0, py - 8, px + r0, py - 8], fill=_C_BAND + (255,), width=3)
            dr.line([px - r0, py + 6, px + r0, py + 6], fill=_C_BAND + (255,), width=3)
        # 火星: 極冠
        elif name == "火星":
            dr.ellipse([px - 6, py - r0 + 2, px + 6, py - r0 + 14], fill=_C_CAP + (255,))
        # ラベル
        lx, ly = px + r0 + 10, py - 12
        dr.rectangle([lx - 4, ly, lx + 130, ly + 30], fill=(10, 12, 25, 215))
        dr.text((lx + 2, ly + 2), name, font=load_font(22, True), fill=(255, 255, 255, 255))

    for nm, s in scene["planets"].items():
        x, y = proj(s["az"], s["alt"])
        kind = _PLANET_VISUAL.get(nm, ((200,200,210), "rocky"))[1]
        draw_planet(nm, x, y, kind)

    # 衛星（赤・強調）: マーカーを全部描いてから、ラベルを重ならない位置に置く
    # （ラベル枠を先に描くと後続衛星のマーカーを覆い隠してしまう）
    sat_items = []
    for nm, s in scene["satellites"].items():
        x, y = proj(s["az"], s["alt"])
        sat_items.append((nm, s, x, y, 15 if s["kind"] == "leo" else 13))
    for nm, s, x, y, r0 in sat_items:
        # 光背
        dr.ellipse([x - r0 - 8, y - r0 - 8, x + r0 + 8, y + r0 + 8], fill=_C_SAT + (60,))
        dr.ellipse([x - r0, y - r0, x + r0, y + r0], fill=_C_SAT + (255,),
                   outline=(255, 255, 255, 255), width=3)
        if s["kind"] == "leo" and s.get("trail"):
            # 軌道予測を細い破線で
            prev = None
            for tp in s["trail"]:
                tx, ty = proj(tp["az"], tp["alt"])
                if prev is not None:
                    dr.line([prev[0], prev[1], tx, ty], fill=_C_TRAIL + (200,), width=3)
                prev = (tx, ty)
    bw, bh = 254, 32
    taken = [[x - r0 - 2, y - r0 - 2, x + r0 + 2, y + r0 + 2] for _, _, x, y, r0 in sat_items]
    for nm, s, x, y, r0 in sat_items:
        for ox, oy in ((r0 + 10, -16), (-(r0 + 14) - bw, -16),
                       (r0 + 10, r0 + 8), (-(r0 + 14) - bw, r0 + 8)):
            bx = min(max(8, x + ox), W - bw - 8)
            by = min(max(140, y + oy), H - 90 - bh)
            if all(bx + bw < t[0] or bx > t[2] or by + bh < t[1] or by > t[3] for t in taken):
                break
        else:
            bx, by = min(max(8, x + r0 + 10), W - bw - 8), min(max(140, y - 16), H - 90 - bh)
        taken.append([bx, by, bx + bw, by + bh])
        dr.rectangle([bx, by, bx + bw, by + bh], fill=(80, 0, 0, 230))
        dr.text((bx + 4, by + 2), nm, font=load_font(20, True), fill=_C_SAT_LABEL + (255,))

    # ヘッダバナー
    dr.rectangle([0, 16, W, 112], fill=(0, 0, 0, 210))
    dr.text((28, 24), "観測地の空 ・ 太陽系の惑星と人工衛星", font=load_font(36, True), fill=(255, 255, 255, 255))
    dr.text((28, 72), "{}（JST +9h） ・ 場所: 緯度{:.2f}° 経度{:.2f}°".format(
        scene["time_utc"], scene["lat"], scene["lon"]), font=load_font(22), fill=(200, 210, 240, 255))

    # 凡例（下部）
    leg_y = H - 56
    dr.rectangle([16, leg_y, W - 16, H - 12], fill=(0, 0, 0, 215))
    # 凡例内のアイコン説明
    dr.text((30, leg_y + 10), "● 惑星(色は実物の特徴)   ●赤 人工衛星   ●白 恒星   ― 軌道予測", font=load_font(20), fill=(230, 235, 250, 255))
    # 実写合成なので JPEG が適切（PNG 比 約1/5。3.5MB 超は縮小して再エンコード）
    return encode_jpeg(canvas)


# ---------- 選択ツール ----------

_SKY_R = 560            # 空の円（地平線）の半径(px)。_render_simple の R と一致させる

def _sky_verify(img_bytes, sats):
    """報告した人工衛星が、報告した方位・高度の位置に実際に描かれているかを検証する。

    描画側と同じ投影（中心=画像中心・半径=_SKY_R・動径 ∝ 90-alt）で画素位置を
    再計算し、マーカー色（赤）がそこにあるかを確かめる。縮尺・方位・中心のどれが
    ずれても落ちるので、図と数値の対応を丸ごと検査できる。
    可視衛星が0機のときは配置検査の対象がないため note を付けて ok とする。
    """
    img = as_image(img_bytes).convert("RGB")
    cx = cy = img.size[0] // 2
    out = {"ok": True, "satellites_reported": len(sats), "markers_at_expected_position": 0,
           "missing": [], "projection": {"center_px": [cx, cy], "radius_px": _SKY_R}}
    for nm, az, alt in sats:
        th = math.radians(az)
        rr = _SKY_R * (90.0 - alt) / 90.0
        x, y = cx + rr * math.sin(th), cy - rr * math.cos(th)
        n = pixel_near(img, (x, y), (255, 60, 50), tol=30, r=10)
        if n >= 40:
            out["markers_at_expected_position"] += 1
        else:
            out["missing"].append([nm, round(x), round(y), n])
    if not sats:
        out["note"] = "可視衛星なし（配置検査は対象外）"
    out["ok"] = not out["missing"]
    return out


def sky_map_with_satellites(place=None, lat=None, lon=None, when=None,
                            engine="simple") -> CallToolResult:
    """東京（または指定地）の空に太陽系の惑星と人工衛星を重ねた図を返す（認証不要）。

    例:「東京の空に惑星と人工衛星を重ねた図」「今見えるISSを星空マップで」「木星はどこに見える？」
    天体位置は JPL de421 + Skyfield、衛星位置は CelesTrak TLE + SGP4 で実測計算。

    学生・観賞用途では視認性の高い Pillow 版(既定)を推奨。正確な座標プロットが必要な場合は
    matplotlib 版も選択できる。

    engine で描画方法を選択:
      - "simple"(既定):   Pillow による実写背景の簡易合成。惑星を種類別の色アイコンで
        大きく・明瞭に描き、学生が見やすい見た目重視の画像。
      - "accurate":       matplotlib による正確な星図。座標グリッド・軌道予測線を精確表示
        （科学・教育の詳細用途向け）。マーカーの色・光背・土星の環・木星の縞・火星の極冠・
        名札・凡例は simple 版と同じ指定色（_PLANET_VISUAL / _C_*）から作る。
    画像は content に base64 インライン表示、座標は structuredContent に JSON。

    Args:
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        when: 観測時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        engine: "simple"(既定/Pillow) / "accurate"(matplotlib)。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く（…）](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は %LOCALAPPDATA%\\Temp\\space_finder_mcp\\out に
    保存し、同じパスを structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    """
    ll = _resolve_place(place, lat, lon)
    if ll is None and (lat is not None or lon is not None):
        return CallToolResult(
            content=[TextContent(type="text", text="lat（-90〜90）と lon（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "lat": str(lat), "lon": str(lon)},
        )
    if ll is None:
        ll = (35.68, 139.69)  # 東京
    eng = (engine or "simple").lower()
    if eng == "auto" or eng not in ("accurate", "simple"):
        eng = "simple"  # 既定は視認性重視の Pillow 版
    scene = _compute(ll[0], ll[1], when)
    use_acc = eng == "accurate"
    import base64
    try:
        if use_acc:
            img_bytes = _render_accurate(scene)      # matplotlib 出力は PNG
            mime = "image/png"
            eng_label = "accurate (matplotlib)"
            alt = "正確な星図で描画した惑星・人工衛星オーバーレイ"
        else:
            img_bytes = _render_simple(scene)        # 実写合成は JPEG（転送量削減）
            mime = "image/jpeg"
            eng_label = "simple (Pillow, 視認性重視)"
            alt = "実写背景に惑星と人工衛星を合成した学生向け図"
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]},
        )
    img = ImageContent(type="image", data=base64.b64encode(img_bytes).decode("ascii"),
                       mimeType=mime, altText=alt)
    # インライン画像を描けないハーネス向け: 保存してリンクを先頭に出す
    out_path = save_output(img_bytes, "sky_map_with_satellites",
                           "png" if mime == "image/png" else "jpg")
    lines = [
        media_link_line("生成した画像を開く（星空マップ・{}）".format(eng_label),
                        path=out_path, kind="figure"),
        "🗺️ **{} の空（惑星と人工衛星・{}）**".format(scene["time_utc"], eng_label),
        "場所: 緯度 {:.2f}° 経度 {:.2f}°".format(ll[0], ll[1]),
        "",
        "**見えている惑星/月**: " + (", ".join(scene["planets"]) if scene["planets"] else "なし"),
        "**見えている人工衛星**: " + (", ".join(scene["satellites"]) if scene["satellites"] else "なし(地平線下)"),
    ]
    for nm, s in scene["satellites"].items():
        lines.append("- {}: 方位 {:.0f}° 仰角 {:.0f}°".format(nm, s["az"], s["alt"]))
    for nm, msg in (scene.get("satellite_errors") or {}).items():
        lines.append("- ⚠️ {}: 位置を計算できませんでした（{}）".format(nm, msg))
    for nm, msg in (scene.get("body_errors") or {}).items():
        lines.append("- ⚠️ {}: 位置を計算できませんでした（{}）".format(nm, msg))
    fig = figure_payload(
        kind="sky_view",
        title="{} の空（惑星・人工衛星）".format(scene["time_utc"]),
        view=view_spec("local_sky", "altaz",
                       "観測地から見た空（高度・方位）に惑星・月・人工衛星を重ねた図",
                       why="日心/地心の配置ではなく、その地点から見た見かけの位置だから"),
        primary=primary_spec("地球", "observer",
                             note="観測地から見た空の図。地球や軌道の形は描いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["惑星・人工衛星のマーカー（実寸ではない）"]),
        markers=([{"id": nm, "label": nm, "kind": "planet", "color": _planet_hex(nm),
                   "az_deg": round(v["az"], 1), "alt_deg": round(v["alt"], 1)}
                  for nm, v in scene["planets"].items()]
                 + [{"id": k, "label": k, "kind": "satellite", "color": "#ff3b30",
                     "az_deg": round(v.get("az", 0.0), 1),
                     "alt_deg": round(v.get("alt", 0.0), 1)}
                    for k, v in list(scene["satellites"].items())[:8]]),
        notes=figure_notes(extra=[
            "この図は観測地の空（高度・方位）の見かけの位置。軌道の形や地球からの距離は分からない",
            "engine=simple は実写背景への合成（学生・観賞向け）、accurate は座標を正確に描いた星図",
            "人工衛星は CelesTrak の最新TLEをSGP4で伝播したその時刻の位置（予報ではない）",
            "地平線下の天体は描かれない（リストにも「なし(地平線下)」と出る）",
            "惑星・月の位置は JPL de421 + Skyfield（観測地の視位置）",
            "惑星・月のマーカー色は実物の見た目に合わせた指定色（{}）".format(
                ", ".join("{}={}".format(nm, _planet_hex(nm))
                          for nm in scene["planets"]) or "該当なし"),
            ("軌道予測の破線は {} の可視区間（前後{}分・{}分刻み）".format(
                ", ".join(k for k, v in scene["satellites"].items() if v.get("trail")),
                _TRAIL_MINUTES, _TRAIL_STEP_MIN)
             if any(v.get("trail") for v in scene["satellites"].values()) else
             "軌道予測の破線を描ける低軌道衛星がこの時刻は地平線上にないため、破線は描かれていない"
             "（静止衛星は動かないので破線を描かない）"),
        ]),
        caption="{:.2f}°N {:.2f}°E の {} の空。惑星: {} ／ 人工衛星: {}".format(
            ll[0], ll[1], scene["time_utc"],
            ", ".join(scene["planets"]) or "なし",
            ", ".join(scene["satellites"]) or "なし"),
        verify=(_sky_verify(img_bytes, [(nm, s["az"], s["alt"])
                                        for nm, s in scene["satellites"].items()])
                if not use_acc else
                {"ok": True, "engine": "accurate (matplotlib)",
                 "note": "極座標版は投影が異なるため配置検査は対象外（注記のみ）"}),
    )
    lines.append("")
    lines.append(figure_text_block(fig))
    lines.append("画像は上に表示（base64 " + ("PNG" if mime == "image/png" else "JPEG") +
                 "）。出典: JPL de421 + Skyfield / CelesTrak TLE + SGP4")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), img],
        structuredContent={"time_utc": scene["time_utc"], "lat": ll[0], "lon": ll[1],
                           "engine": eng_label, "planets": scene["planets"],
                           "stars": scene["stars"], "satellites": scene["satellites"],
                           "satellite_errors": scene.get("satellite_errors") or {},
                           "body_errors": scene.get("body_errors") or {},
                           "figure": fig, "image_path": out_path,
                           "source": "JPL de421+Skyfield / CelesTrak+SGP4"},
    )

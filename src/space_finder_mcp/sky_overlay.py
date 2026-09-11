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
from .img_common import load_font

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

# 惑星・月の見た目 (種類別アイコン用): 名前 -> (主色RGB, 分類, 半径比)
# 分類: rocky(岩石惑星) / gas(ガス惑星) / moon(月)
_PLANET_VISUAL = {
    "月":   ((224, 224, 226), "moon"),
    "水星": ((168, 168, 170), "rocky"),
    "金星": ((242, 226, 180), "rocky"),
    "火星": ((214, 96, 77),   "rocky"),
    "木星": ((212, 168, 118), "gas"),
    "土星": ((226, 196, 146), "gas"),
    "天王星": ((176, 224, 230), "gas"),
    "海王星": ((96, 140, 232), "gas"),
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
    """place 文字列 or lat/lon 数値から (lat, lon)。任意の地名は Open-Meteo でジオコーディング。"""
    if lat is not None and lon is not None:
        return float(lat), float(lon)
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
    for nm, b in bodies.items():
        try:
            az, alt = _body_aa(b)
            if alt > 0:
                planets[nm] = {"az": az, "alt": alt}
        except Exception:
            pass

    stars = {}
    for nm, rah, decd in _BRIGHT_STARS:
        try:
            az, alt = _body_aa(Star(ra_hours=rah, dec_degrees=decd))
            if alt > 0:
                stars[nm] = {"az": az, "alt": alt}
        except Exception:
            pass

    sats = {}
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
                for i in range(-20, 21, 1):  # -60分 〜 +60分（3分刻み）
                    az, alt = _sat_aa(ts.tt_jd(t.tt + i * 3.0 * 60.0 / 86400.0))
                    if alt > 0:
                        trail.append({"az": az, "alt": alt})
            if alt0 > 0:
                sats[nm] = {"catnr": catnr, "kind": kind, "az": az0, "alt": alt0, "trail": trail}
        except Exception:
            pass

    return {"time_utc": tstr, "lat": lat, "lon": lon, "planets": planets,
            "stars": stars, "satellites": sats}


# ---------- 描画エンジン A: matplotlib (正確) ----------
def _render_accurate(scene):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
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
        ax.plot(np.linspace(0, 2 * np.pi, 100), np.full(100, g), color="#334", lw=0.6, ls=":")
    ax.plot(np.linspace(0, 2 * np.pi, 100), np.full(100, 89), color="#8899bb", lw=2)
    ax.set_facecolor("#0b1026")
    fig.patch.set_facecolor("#04060f")

    for nm, s in scene["stars"].items():
        ax.scatter(math.radians(s["az"]), s["alt"], s=14, color="#d0d0e2", marker="*", zorder=2, alpha=0.85)
    for nm, s in scene["planets"].items():
        c = "#f4c542" if nm == "月" else ("#ffd66e" if nm == "金星" else "#7fd0ff")
        ax.scatter(math.radians(s["az"]), s["alt"], s=(70 if nm == "月" else 55),
                   color=c, edgecolor="white", lw=0.8, zorder=5)
        ax.annotate(nm, (math.radians(s["az"]), s["alt"]), textcoords="offset points",
                    xytext=(9, 7), fontsize=11, color="white", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.15", fc="#00000099", ec="none"))
    for nm, s in scene["satellites"].items():
        az, alt = s["az"], s["alt"]
        ax.scatter(math.radians(az), alt, s=150, color="#ff3b30", edgecolor="white", lw=1.4, zorder=7)
        ax.annotate(nm, (math.radians(az), alt), textcoords="offset points", xytext=(12, 13),
                    fontsize=10, color="#ffc2ba", fontweight="bold", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.22", fc="#4a0000d9", ec="#ff3b30"))
        if s.get("trail"):
            ax.plot([math.radians(p["az"]) for p in s["trail"]], [p["alt"] for p in s["trail"]],
                    color="#ff8a80", lw=1.6, alpha=0.9, ls="--", zorder=6)
    for lbl, deg in (("北", 0), ("東", 90), ("南", 180), ("西", 270)):
        ax.text(math.radians(deg), 99, lbl, ha="center", va="center", fontsize=15,
                color="#c8cdd8", fontweight="bold")
    ax.set_title("{} ・ {} の空と人工衛星（正確な星図）\n赤●=衛星, 破線=軌道予測, 青●=惑星, 黄=月, ＊=恒星".format(scene["lat"], scene["time_utc"]),
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
    # 惑星の見た目は既定色（実物らしい色）。背景の実写が写るため少しだけ明るめに。
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
        dr.ellipse([CX - r, CY - r, CX + r, CY + r], outline=(80, 90, 130, 255), width=2)
    # 方位線 + N/E/S/W
    for az, lab in ((0, "北"), (90, "東"), (180, "南"), (270, "西")):
        x, y = proj(az, 0)
        dr.line([CX, CY, x, y], fill=(80, 90, 130, 255), width=2)
        x2, y2 = CX + (x - CX) * 1.07, CY + (y - CY) * 1.07
        dr.text((x2 - 14, y2 - 12), lab, font=load_font(28, True), fill=(210, 220, 245, 255))

    # 恒星（薄い点・小さめ）
    for nm, s in scene["stars"].items():
        x, y = proj(s["az"], s["alt"])
        dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(210, 214, 235, 220))

    # 惑星・月（種類別アイコン）
    def draw_planet(name, px, py, kind):
        base = _PLANET_VISUAL.get(name, ((200, 200, 210), "rocky"))
        color = base[0]
        if kind == "moon":
            r0 = 26; color = (226, 226, 230)
        elif kind == "rocky":
            r0 = 30
        else:
            r0 = 38  # ガス惑星を大きく
        # 光背（発光）で視認性UP
        dr.ellipse([px - r0 - 6, py - r0 - 6, px + r0 + 6, py + r0 + 6], fill=(color[0], color[1], color[2], 70))
        dr.ellipse([px - r0, py - r0, px + r0, py + r0], fill=color + (255,))
        # 土星: 環
        if name == "土星":
            dr.ellipse([px - r0 - 16, py - 8, px + r0 + 16, py + 8], outline=(226, 206, 160, 255), width=5)
        # 木星: 縞
        elif name == "木星":
            dr.line([px - r0, py - 8, px + r0, py - 8], fill=(196, 148, 108, 255), width=3)
            dr.line([px - r0, py + 6, px + r0, py + 6], fill=(196, 148, 108, 255), width=3)
        # 火星: 極冠
        elif name == "火星":
            dr.ellipse([px - 6, py - r0 + 2, px + 6, py - r0 + 14], fill=(240, 240, 240, 255))
        # ラベル
        lx, ly = px + r0 + 10, py - 12
        dr.rectangle([lx - 4, ly, lx + 130, ly + 30], fill=(10, 12, 25, 215))
        dr.text((lx + 2, ly + 2), name, font=load_font(22, True), fill=(255, 255, 255, 255))

    for nm, s in scene["planets"].items():
        x, y = proj(s["az"], s["alt"])
        kind = _PLANET_VISUAL.get(nm, ((200,200,210), "rocky"))[1]
        draw_planet(nm, x, y, kind)

    # 衛星（赤・強調）
    for nm, s in scene["satellites"].items():
        x, y = proj(s["az"], s["alt"])
        r0 = 15 if s["kind"] == "leo" else 13
        # 光背
        dr.ellipse([x - r0 - 8, y - r0 - 8, x + r0 + 8, y + r0 + 8], fill=(255, 80, 60, 60))
        dr.ellipse([x - r0, y - r0, x + r0, y + r0], fill=(255, 60, 50, 255),
                   outline=(255, 255, 255, 255), width=3)
        if s["kind"] == "leo" and s.get("trail"):
            # 軌道予測を細い破線で
            prev = None
            for tp in s["trail"]:
                tx, ty = proj(tp["az"], tp["alt"])
                if prev is not None:
                    dr.line([prev[0], prev[1], tx, ty], fill=(255, 138, 128, 200), width=3)
                prev = (tx, ty)
        lx, ly = x + 20, y - 18
        dr.rectangle([lx - 4, ly, lx + 250, ly + 32], fill=(80, 0, 0, 230))
        dr.text((lx, ly + 2), nm, font=load_font(20, True), fill=(255, 176, 166, 255))

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
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


# ---------- 選択ツール ----------
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
        （科学・教育の詳細用途向け）。
    画像は content に base64 インライン表示、座標は structuredContent に JSON。

    Args:
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        when: 観測時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        engine: "simple"(既定/Pillow) / "accurate"(matplotlib)。
    """
    ll = _resolve_place(place, lat, lon)
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
            png = _render_accurate(scene)
            eng_label = "accurate (matplotlib)"
            alt = "正確な星図で描画した惑星・人工衛星オーバーレイ"
        else:
            png = _render_simple(scene)
            eng_label = "simple (Pillow, 視認性重視)"
            alt = "実写背景に惑星と人工衛星を合成した学生向け図"
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]},
        )
    img = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                       mimeType="image/png", altText=alt)
    lines = [
        "🗺️ **{} の空（惑星と人工衛星・{}）**".format(scene["time_utc"], eng_label),
        "場所: 緯度 {:.2f}° 経度 {:.2f}°".format(ll[0], ll[1]),
        "",
        "**見えている惑星/月**: " + (", ".join(scene["planets"]) if scene["planets"] else "なし"),
        "**見えている人工衛星**: " + (", ".join(scene["satellites"]) if scene["satellites"] else "なし(地平線下)"),
    ]
    for nm, s in scene["satellites"].items():
        lines.append("- {}: 方位 {:.0f}° 仰角 {:.0f}°".format(nm, s["az"], s["alt"]))
    lines.append("画像は上に表示（base64 PNG）。出典: JPL de421 + Skyfield / CelesTrak TLE + SGP4")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), img],
        structuredContent={"time_utc": scene["time_utc"], "lat": ll[0], "lon": ll[1],
                           "engine": eng_label, "planets": scene["planets"],
                           "stars": scene["stars"], "satellites": scene["satellites"],
                           "source": "JPL de421+Skyfield / CelesTrak+SGP4"},
    )

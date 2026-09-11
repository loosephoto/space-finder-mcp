"""太陽系俯瞰図（太陽を中心とした惑星・小惑星の現在位置合成, 認証不要）。

指定時刻の太陽を中心とした俯瞰図（黄道面を上から見た図）を画像化する。
- 惑星 (8惑星＋冥王星) は JPL DE421 暦表 + Skyfield で日心黄道座標を計算（ローカル/認証不要）。
- 任意の小惑星 (例: イトカワ25143, ベンヌ101955, アポフィス99942, リュウグウ162173) は
  JPL SBDB API の軌道要素を取得し、ケプラー2体問題で日心位置を伝播。

距離が 0.4〜40 AU と2桁超の差があるため、描画は対数縮尺を既定とし、全天体を視認できる
ようにする。惑星軌道円・小惑星帯(2.0-3.4AU)の目安も表示。

2つの描画エンジン:
- engine="simple"   (Pillow, 既定): 学生・観賞向け。惑星を色アイコンで大きく・明瞭に、
  小惑星を緑色の十字マーカーで強調。距離は対数縮尺。
- engine="accurate" (matplotlib): 線形距離の正確な相対距離俯瞰図。

返却: content に base64 画像(ImageContent)、structuredContent に座標JSON。
出典: JPL DE421 + Skyfield / JPL SBDB 軌道要素。
"""
from __future__ import annotations

import io
import math
import os
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)
UA = {"User-Agent": "space-finder-mcp/0.21 (MCP; solar system)"}

# よく使う小惑星のエイリアス -> JPL SBDB sstr（日本語名・英名・番号）
_ASTEROID_ALIASES = {
    "イトカワ": "25143", "itokawa": "25143", "25143": "25143",
    "ベンヌ": "101955", "bennu": "101955", "101955": "101955",
    "アポフィス": "99942", "apophis": "99942", "99942": "99942",
    "リュウグウ": "162173", "ryugu": "162173", "162173": "162173",
    "ツタティス": "4179", "toutatis": "4179", "4179": "4179",
    "エロス": "433", "eros": "433", "433": "433",
}
# SBDB の必須軌道要素名
_REQUIRED_ELEMS = ("e", "a", "i", "om", "w", "ma", "n")

# 惑星 (日本語名, de421天体名, 主色RGB, 軌道長半径AU)
_PLANETS = [
    ("水星", "mercury", (168, 168, 170), 0.387),
    ("金星", "venus", (242, 226, 180), 0.723),
    ("地球", "earth", (110, 150, 235), 1.000),
    ("火星", "mars", (214, 96, 77), 1.524),
    ("木星", "jupiter barycenter", (212, 168, 118), 5.20),
    ("土星", "saturn barycenter", (226, 196, 146), 9.58),
    ("天王星", "uranus barycenter", (150, 210, 220), 19.2),
    ("海王星", "neptune barycenter", (96, 140, 232), 30.1),
    ("冥王星", "pluto barycenter", (176, 140, 120), 39.5),
]


# 遠方探査機 (Horizons 天体ID, 表示色RGB)
# Horizons の COMMAND は負のIDで宇宙機を示す（-31=ボイジャー1, -32=ボイジャー2,
# -23=パイオニア10, -21=パイオニア11, -98=ニュー・ホライズンズ, -37=はやぶさ2）。
# 距離が数十〜百数十 AU に達し、黄緯も大きい（ボイジャー1は黄緯~35°）ため、
# 黄道面俯瞰図には「正射影距離」で描き、ラベルに真距離・黄緯を併記する。
_PROBE_ALIASES = {
    "ボイジャー1号": ("-31", (255, 214, 90)), "ボイジャー1": ("-31", (255, 214, 90)),
    "voyager1": ("-31", (255, 214, 90)), "voyager 1": ("-31", (255, 214, 90)),
    "ボイジャー2号": ("-32", (120, 200, 255)), "ボイジャー2": ("-32", (120, 200, 255)),
    "voyager2": ("-32", (120, 200, 255)), "voyager 2": ("-32", (120, 200, 255)),
    "パイオニア10号": ("-23", (255, 150, 120)), "パイオニア10": ("-23", (255, 150, 120)),
    "pioneer10": ("-23", (255, 150, 120)), "pioneer 10": ("-23", (255, 150, 120)),
    "パイオニア11号": ("-21", (220, 160, 220)), "パイオニア11": ("-21", (220, 160, 220)),
    "pioneer11": ("-21", (220, 160, 220)), "pioneer 11": ("-21", (220, 160, 220)),
    "ニューホライズンズ": ("-98", (140, 200, 140)), "ニュー・ホライズンズ": ("-98", (140, 200, 140)),
    "new horizons": ("-98", (140, 200, 140)), "pluto probe": ("-98", (140, 200, 140)),
    "はやぶさ2": ("-37", (255, 200, 60)), "はやぶさ２": ("-37", (255, 200, 60)),
    "hayabusa2": ("-37", (255, 200, 60)), "hayabusa 2": ("-37", (255, 200, 60)),
    "ハヤブサ2": ("-37", (255, 200, 60)),
}


def _horizons_position(cmd, jd):
    """JPL Horizons API から宇宙機の太陽中心状態ベクトルを取得（認証不要）。

    jd は TDB(≈TT)。返すのは (x,y,z, r, eclLon, eclLat) を AU / 度で。
    座標は ECLIPTIC 基準。r は真の日心距離、正射影距離は hypot(x,y)。
    """
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api",
                     params={"format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
                             "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS",
                             "CENTER": "'500@10'", "REF_PLANE": "ECLIPTIC",
                             "TLIST": "'{:.6f}'".format(jd), "VEC_TABLE": "'2'"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    txt = r.text
    i = txt.find("$$SOE"); j = txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に座標ブロックがありません")
    block = txt[i + 5:j]
    AU = 1.495978707e8  # km
    import re
    m = re.search(r"X\s*=\s*([-+0-9.Ee]+)\s+Y\s*=\s*([-+0-9.Ee]+)\s+Z\s*=\s*([-+0-9.Ee]+)", block)
    if not m:
        raise ValueError("状態ベクトルを解析できません")
    x = float(m.group(1)) / AU
    y = float(m.group(2)) / AU
    z = float(m.group(3)) / AU
    rr = math.hypot(x, y, z)
    lon = math.degrees(math.atan2(y, x)) % 360
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    return x, y, z, rr, lon, lat


# 彗星 (日本語名/英名/記号 -> (種別, 取得ID))
# 種別 "horizons": C/彗星（非周期・放物線/双曲線的新彗星）。Horizons 状態ベクトルで正確に取得
#                 （COMMAND に C/記号を渡すと一意解決される）。
# 種別 "sbdb":    周期彗星（e<1 の楕円軌道）。JPL SBDB 軌道要素 + 楕円ケプラーで伝播。
_COMET_ALIASES = {
    # 周期彗星 (SBDB)
    "ハレー彗星": ("sbdb", "1P"), "ハレー": ("sbdb", "1P"), "halley": ("sbdb", "1P"),
    "1P": ("sbdb", "1P"), "1p": ("sbdb", "1P"), "1P/Halley": ("sbdb", "1P"),
    "エンケ彗星": ("sbdb", "2P"), "エンケ": ("sbdb", "2P"), "encke": ("sbdb", "2P"),
    "2P": ("sbdb", "2P"), "2p": ("sbdb", "2P"),
    "チュリュモフ・ゲラシメンコ": ("sbdb", "67P"), "ロゼッタ彗星": ("sbdb", "67P"),
    "67P": ("sbdb", "67P"), "67p": ("sbdb", "67P"),
    "テンペル第1彗星": ("sbdb", "9P"), "テンペル": ("sbdb", "9P"), "tempel": ("sbdb", "9P"),
    "9P": ("sbdb", "9P"), "9p": ("sbdb", "9P"),
    "ヴィルト第2彗星": ("sbdb", "81P"), "ヴィルト2": ("sbdb", "81P"),
    "81P": ("sbdb", "81P"), "81p": ("sbdb", "81P"), "wild": ("sbdb", "81P"),
    "ボレリー彗星": ("sbdb", "19P"), "19P": ("sbdb", "19P"),
    # C/彗星 (Horizons 状態ベクトル)
    "紫金山・アトラス彗星": ("horizons", "C/2023 A3"), "紫金山アトラス": ("horizons", "C/2023 A3"),
    "tsuchinshan": ("horizons", "C/2023 A3"), "C/2023 A3": ("horizons", "C/2023 A3"),
    "ラブジョイ彗星": ("horizons", "C/2014 Q2"), "ラブジョイ": ("horizons", "C/2014 Q2"),
    "lovejoy": ("horizons", "C/2014 Q2"), "C/2014 Q2": ("horizons", "C/2014 Q2"),
}


def _comet_position(name, jd):
    """彗星の日心位置を取得（認証不要）。返すのは (x,y,z, r, eclLon, eclLat) AU/度。

    - C/彗星（非周期・放物線/双曲線的新彗星）: JPL Horizons 状態ベクトル（正確・双曲線対応）。
    - 周期彗星（e<1 楕円）: JPL SBDB 軌道要素のケプラー2体伝播。
    """
    typ, cid = ("sbdb", name)
    alias = _COMET_ALIASES.get(name) or _COMET_ALIASES.get(name.lower()) or _COMET_ALIASES.get(name.replace("彗星", ""))
    if alias:
        typ, cid = alias
    elif str(name).strip().upper().startswith("C/"):
        typ, cid = "horizons", str(name).strip()
    if typ == "horizons":
        return _horizons_position(cid, jd)
    el = _sbdb_elements(cid)
    return _kepler_position(el, jd)


# 彗星の描画色 (シアン系・彗星らしい) とマーカー指定
_COMET_COLOR = (150, 235, 255)


# ---------- 共通データ層 ----------
def _load():
    from skyfield.api import Loader
    loader = Loader(_DATA_DIR, verbose=False)
    eph = loader("de421.bsp")
    return loader, eph


def _resolve_when(when_iso, ts):
    """ISO8601 を UTC として解釈し Skyfield 時刻へ。省略時は現在。不正なら None。"""
    if when_iso:
        iso = str(when_iso).strip().replace("Z", "+00:00")
        import re
        m = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?",
                     iso)
        if not m:
            return None
        y, mo, d, h, mi, s_ = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
        return ts.utc(y, mo, d, h, mi, s_)
    return ts.now()


def _sbdb_elements(sstr):
    """JPL SBDB API から小惑星の軌道要素辞書を取得（認証不要）。"""
    r = requests.get("https://ssd-api.jpl.nasa.gov/sbdb.api", params={"sstr": sstr},
                     headers=UA, timeout=25)
    r.raise_for_status()
    d = r.json()
    if "orbit" not in d or "elements" not in d["orbit"]:
        raise ValueError("軌道要素が見つかりません")
    elems = {el["name"]: el["value"] for el in d["orbit"]["elements"]}
    if not all(k in elems for k in _REQUIRED_ELEMS):
        raise ValueError("SBDB 要素に必須キーが不足: {}".format(elems.keys()))
    fullname = d.get("object", {}).get("fullname") or sstr
    # epoch は orbit トップレベルにある（彗星では要素リストに無いため必須）
    epoch = float(d.get("orbit", {}).get("epoch") or elems.get("epoch", 2461200.5))
    return {
        "e": float(elems["e"]), "a": float(elems["a"]), "i": math.radians(float(elems["i"])),
        "node": math.radians(float(elems["om"])), "argp": math.radians(float(elems["w"])),
        "ma": math.radians(float(elems["ma"])), "n": float(elems["n"]),  # deg/day
        "epoch": epoch, "fullname": fullname,
    }


def _kepler_position(el, jd):
    """軌道要素 + 時刻JD -> (x, y, z, r, eclLon, eclLat)。ケプラー2体問題で伝播。"""
    e, a = el["e"], el["a"]
    i, node, argp = el["i"], el["node"], el["argp"]
    ma0, n, epoch = el["ma"], el["n"], el["epoch"]
    M = (ma0 + math.radians(n) * (jd - epoch)) % (2 * math.pi)
    E = M
    for _ in range(60):
        de = (M - (E - e * math.sin(E))) / (1 - e * math.cos(E))
        E += de
        if abs(de) < 1e-11:
            break
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2),
                        math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))
    u = argp + nu
    x = r * (math.cos(node) * math.cos(u) - math.sin(node) * math.sin(u) * math.cos(i))
    y = r * (math.sin(node) * math.cos(u) + math.cos(node) * math.sin(u) * math.cos(i))
    z = r * math.sin(u) * math.sin(i)
    lon = math.degrees(math.atan2(y, x)) % 360
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    return x, y, z, r, lon, lat


def _compute(when_iso=None, asteroids=None, probes=None, comets=None):
    """太陽を原点とした惑星・小惑星・探査機・彗星の日心黄道座標を計算。"""
    loader, eph = _load()
    ts = loader.timescale()
    t = _resolve_when(when_iso, ts)
    if t is None:
        return {"time_utc": str(when_iso), "planets": {}, "asteroids": {}, "probes": {}, "comets": {},
                "error": "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"}
    jd = t.tt
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")
    sun = eph["sun"]

    # 日心黄道座標フレーム
    from skyfield.framelib import ecliptic_frame
    planets = {}
    for jname, key, col, sma in _PLANETS:
        try:
            v = eph[key].at(t) - sun.at(t)
            au = v.distance().au
            lat, lon, _ = v.frame_latlon(ecliptic_frame)
            planets[jname] = {"name": jname, "au": au,
                              "eclLon": float(lon.degrees), "eclLat": float(lat.degrees),
                              "sma": sma}
        except Exception:
            pass

    asts = {}
    for a in (asteroids or []):
        name = str(a).strip()
        if not name:
            continue
        key = name.lower()
        sstr = _ASTEROID_ALIASES.get(key) or _ASTEROID_ALIASES.get(name) or name
        try:
            el = _sbdb_elements(sstr)
            x, y, z, r, lon, lat = _kepler_position(el, jd)
            asts[name] = {"name": name, "sstr": sstr, "fullname": el["fullname"],
                          "au": r, "eclLon": lon, "eclLat": lat,
                          "sma": el["a"], "e": el["e"]}
        except Exception as ex:
            asts[name] = {"name": name, "sstr": sstr, "error": str(ex)[:120]}

    prbs = {}
    for pr in (probes or []):
        name = str(pr).strip()
        if not name:
            continue
        key = name.lower()
        hit = _PROBE_ALIASES.get(name) or _PROBE_ALIASES.get(key) or _PROBE_ALIASES.get(name.replace("号", ""))
        if hit is None:
            prbs[name] = {"name": name, "error": "未知の探査機です"}
            continue
        cmd, col = hit
        try:
            x, y, z, rr, lon, lat = _horizons_position(cmd, jd)
            prbs[name] = {"name": name, "cmd": cmd, "color": col,
                          "au": rr, "proj_au": math.hypot(x, y),   # 黄道面正射影距離
                          "eclLon": lon, "eclLat": lat}
        except Exception as ex:
            prbs[name] = {"name": name, "cmd": cmd, "color": col, "error": str(ex)[:120]}

    coms = {}
    for co in (comets or []):
        name = str(co).strip()
        if not name:
            continue
        try:
            x, y, z, rr, lon, lat = _comet_position(name, jd)
            coms[name] = {"name": name, "color": _COMET_COLOR,
                          "au": rr, "proj_au": math.hypot(x, y),
                          "eclLon": lon, "eclLat": lat}
        except Exception as ex:
            coms[name] = {"name": name, "color": _COMET_COLOR, "error": str(ex)[:120]}

    return {"time_utc": tstr, "planets": planets, "asteroids": asts, "probes": prbs,
            "comets": coms, "asteroid_belt": True}


# ---------- 描画ヘルパー ----------
def _font(sz, bold=False):
    from PIL import ImageFont
    for p in ("C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc",
              "C:/Windows/Fonts/yugothb.ttc", "C:/Windows/Fonts/msgothic.ttc"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def _log_scale(dist, r0=620.0, lo=0.30, hi=60.0):
    """距離AU -> 画素距離（対数縮尺）。内惑星〜外惑星を1枚で視認可能に。"""
    return r0 * (math.log10(dist) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


# ---------- 描画エンジン B: Pillow (簡易・実写合成, 既定) ----------
def _render_simple(scene):
    """Pillow 対数縮尺俯瞰図。内惑星〜遠方探査機までを1枚に収める（視認性重視・既定）。

    表示範囲(対数の上限)は、惑星軌道だけでなく指定された探査機の正射影距離に応じて
    自動拡張する（例: ボイジャー1号 140AU まで広げる）。小惑星は緑十字、探査機は色付き
    菱形マーカーで強調。
    """
    from PIL import Image, ImageDraw, ImageFilter
    W = H = 1500
    CX = CY = W // 2

    hi = 60.0
    for pr in scene["probes"].values():
        if not pr.get("error") and pr["proj_au"] > 0:
            hi = max(hi, pr["proj_au"] * 1.15)
    for co in scene["comets"].values():
        if not co.get("error") and co["proj_au"] > 0:
            hi = max(hi, co["proj_au"] * 1.15)
    R0 = 620.0; lo = 0.30

    def scale(dist):
        return R0 * (math.log10(dist) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    img = Image.new("RGB", (W, H), (3, 4, 12))
    dr = ImageDraw.Draw(img)

    import random
    random.seed(7)
    for _ in range(420):
        x, y = random.randint(0, W), random.randint(0, H)
        b = random.randint(80, 220)
        if math.hypot(x - CX, y - CY) > 640:
            dr.point((x, y), fill=(b, b, b))

    for jname, _, _, sma in _PLANETS:
        r = scale(sma)
        dr.ellipse([CX - r, CY - r, CX + r, CY + r], outline=(110, 120, 170), width=1)

    rlo, rhi = scale(2.0), scale(3.4)
    for a in range(0, 360, 4):
        rr = random.uniform(rlo, rhi)
        th = math.radians(a + random.uniform(-2, 2))
        g = random.randint(90, 170)
        dr.point((int(CX + rr * math.cos(th)), int(CY + rr * math.sin(th))), fill=(g, g, g + 10))

    for p in scene["planets"].values():
        jname, au, lon, sma = p["name"], p["au"], p["eclLon"], p["sma"]
        col = dict((j, c) for j, _, c, _ in _PLANETS)[jname]
        ang = math.radians(lon)
        rr = scale(au)
        px, py = CX + rr * math.cos(ang), CY + rr * math.sin(ang)
        rad = max(8, min(20, int(5 + 28 * sma / 45)))
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([px - rad - 14, py - rad - 14, px + rad + 14, py + rad + 14], fill=col + (90,))
        halo = halo.filter(ImageFilter.GaussianBlur(8))
        img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(img)
        dr.ellipse([px - rad, py - rad, px + rad, py + rad], fill=col,
                   outline=tuple(min(255, c + 70) for c in col), width=2)
        if jname == "土星":
            dr.ellipse([px - rad - 14, py - 6, px + rad + 14, py + 6], outline=(226, 206, 160), width=5)
        elif jname == "木星":
            dr.line([px - rad, py - 8, px + rad, py - 8], fill=(196, 148, 108), width=3)
            dr.line([px - rad, py + 5, px + rad, py + 5], fill=(196, 148, 108), width=3)
        lx = px + rad + 8 if (px + rad + 170 < W) else px - rad - 178
        lx = max(lx, 10); ly = py - 10
        dr.rectangle([lx, ly, lx + 178, ly + 30], fill=(8, 10, 22, 230))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(jname, au), font=_font(17, True),
                fill=(255, 255, 255, 255))

    for name, d in scene["asteroids"].items():
        if d.get("error"):
            continue
        ang = math.radians(d["eclLon"])
        rr = scale(d["au"])
        px, py = CX + rr * math.cos(ang), CY + rr * math.sin(ang)
        rad = 10
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([px - rad - 12, py - rad - 12, px + rad + 12, py + rad + 12],
                   fill=(96, 230, 120, 160))
        halo = halo.filter(ImageFilter.GaussianBlur(6))
        img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(img)
        dr.line([px - rad - 7, py, px + rad + 7, py], fill=(200, 255, 215), width=2)
        dr.line([px, py - rad - 7, px, py + rad + 7], fill=(200, 255, 215), width=2)
        dr.ellipse([px - rad, py - rad, px + rad, py + rad], fill=(96, 200, 120),
                   outline=(220, 255, 230), width=2)
        rr2 = scale(d["sma"])
        dr.ellipse([CX - rr2, CY - rr2, CX + rr2, CY + rr2], outline=(96, 190, 120), width=2)
        lx, ly = px + 16, py - 14
        if lx + 240 > W:
            lx = px - rad - 250
        dr.rectangle([lx, ly, lx + 240, ly + 30], fill=(10, 40, 18, 235))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(d["name"], d["au"]), font=_font(17, True),
                fill=(200, 255, 215))

    for name, d in scene["probes"].items():
        if d.get("error"):
            continue
        col = tuple(d.get("color", (255, 214, 90)))
        ang = math.radians(d["eclLon"])
        rr = scale(d["proj_au"])
        px, py = CX + rr * math.cos(ang), CY + rr * math.sin(ang)
        rad = 13
        dr.line([CX, CY, px, py], fill=col + (70,), width=1)
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([px - rad - 14, py - rad - 14, px + rad + 14, py + rad + 14], fill=col + (170,))
        halo = halo.filter(ImageFilter.GaussianBlur(7))
        img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(img)
        dr.polygon([(px, py - rad), (px + rad, py), (px, py + rad), (px - rad, py)],
                   fill=col, outline=(255, 255, 255))
        lx, ly = px + rad + 12, py - 16
        if lx + 300 > W:
            lx = px - rad - 310
        lx = max(lx, 4)
        dr.rectangle([lx, ly, lx + 300, ly + 42], fill=(40, 20, 0, 235))
        dr.text((lx + 6, ly + 3), "{}  {:.0f}AU".format(d["name"], d["au"]), font=_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=_font(13), fill=(255, 235, 190, 255))

    # 彗星（シアン色の輝く核 + 太陽と反対方向に伸びる尾, 正射影位置に描画）
    for name, d in scene["comets"].items():
        if d.get("error"):
            continue
        col = tuple(d.get("color", _COMET_COLOR))
        ang = math.radians(d["eclLon"])
        rr = scale(d["proj_au"])
        px, py = CX + rr * math.cos(ang), CY + rr * math.sin(ang)
        # 尾の方向: 太陽から彗星へ向かう方向の外側（太陽と反対側）
        tx, ty = (px - CX), (py - CY)
        tl = math.hypot(tx, ty) or 1.0
        ux, uy = tx / tl, ty / tl
        # 尾（複数セグメントで放射状に広がる）: 彗星の尾は太陽光を反射し広がる
        for seg in range(3, 0, -1):
            tail_len = 34 + seg * 14
            tw = 6 + seg * 5
            half = 2 + seg * 2
            # 尾の先端と基端（彗星核側）
            ex = px + ux * tail_len; ey = py + uy * tail_len
            alpha = 60 + seg * 40
            halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            hd = ImageDraw.Draw(halo)
            # 尾を帯状に（太さを持たせて2点間に線→広がりは ellipse の組合せで表現）
            hd.line([px, py, ex, ey], fill=col + (alpha,), width=tw)
            halo = halo.filter(ImageFilter.GaussianBlur(3))
            img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
            dr = ImageDraw.Draw(img)
        # 核の光背
        rad = 11
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([px - rad - 16, py - rad - 16, px + rad + 16, py + rad + 16], fill=(230, 250, 255, 200))
        halo = halo.filter(ImageFilter.GaussianBlur(8))
        img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(img)
        dr.ellipse([px - rad, py - rad, px + rad, py + rad], fill=(225, 248, 255),
                   outline=(255, 255, 255))
        # ラベル（真距離・黄緯を併記）
        lx, ly = px + rad + 12, py - 16
        if lx + 320 > W:
            lx = px - rad - 330
        lx = max(lx, 4)
        dr.rectangle([lx, ly, lx + 320, ly + 42], fill=(0, 30, 45, 235))
        dr.text((lx + 6, ly + 3), "☄ {}  {:.1f}AU".format(d["name"], d["au"]), font=_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=_font(13), fill=(190, 235, 255, 255))

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([CX - 70, CY - 70, CX + 70, CY + 70], fill=(255, 230, 140, 220))
    glow = glow.filter(ImageFilter.GaussianBlur(30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse([CX - 26, CY - 26, CX + 26, CY + 26], fill=(255, 220, 120),
               outline=(255, 245, 200), width=2)
    dr.text((CX - 14, CY - 8), "太陽", font=_font(18, True), fill=(120, 80, 0))

    dr.rectangle([0, 0, W, 104], fill=(0, 0, 0, 230))
    title = "太陽系・現在の惑星位置（太陽を中心とした俯瞰図）"
    parts = []
    ok_asts = [k for k, v in scene["asteroids"].items() if not v.get("error")]
    if ok_asts:
        parts.append("小惑星" + "・".join(ok_asts))
    ok_prbs = [k for k, v in scene["probes"].items() if not v.get("error")]
    if ok_prbs:
        parts.append("探査機" + "・".join(ok_prbs))
    ok_coms = [k for k, v in scene["comets"].items() if not v.get("error")]
    if ok_coms:
        parts.append("彗星" + "・".join(ok_coms))
    if parts:
        title = "太陽系・現在の位置＋" + "／".join(parts) + "（太陽中心俯瞰図）"
    dr.text((26, 16), title, font=_font(30, True), fill=(255, 255, 255, 255))
    dr.text((26, 70), "{}（JST +9h）・数値=太陽からの距離AU ・ 円=惑星公転軌道(対数縮尺) ・ 補助線/菱形=探査機".format(scene["time_utc"]),
            font=_font(18), fill=(195, 205, 235, 255))

    dr.rectangle([14, H - 54, W - 14, H - 14], fill=(0, 0, 0, 225))
    has_probe = any(not v.get("error") for v in scene["probes"].values())
    has_ast = any(not v.get("error") for v in scene["asteroids"].values())
    has_com = any(not v.get("error") for v in scene["comets"].values())
    leg = "● 惑星位置（色は実物の特徴）"
    if has_ast:
        leg += "  ＋緑 小惑星"
    if has_com:
        leg += "  ☄彗星（シアン・尾）"
    if has_probe:
        leg += "  ◆ 探査機（遠方・星間空間）"
    leg += "   ✦帯 小惑星帯(2.0–3.4AU目安)  ☀太陽"
    dr.text((28, H - 40), leg + " ・ 出典: JPL DE421+SBDB+Horizons / Skyfield",
            font=_font(16), fill=(225, 232, 250, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()



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

    fig, ax = plt.subplots(figsize=(9.5, 9.5))
    ax.set_facecolor("#0b1026")
    fig.patch.set_facecolor("#04060f")
    ax.set_aspect("equal")

    # 惑星軌道（線形）
    for jname, _, _, sma in _PLANETS:
        th = np.linspace(0, 2 * np.pi, 360)
        ax.plot(sma * np.cos(th), sma * np.sin(th), color="#3a4466", lw=0.8)

    # 小惑星帯
    belt_r = np.random.uniform(2.0, 3.4, 900)
    belt_th = np.random.uniform(0, 2 * np.pi, 900)
    ax.scatter(belt_r * np.cos(belt_th), belt_r * np.sin(belt_th),
               s=2, color="#8a9ab8", alpha=0.5, zorder=1)

    for p in scene["planets"].values():
        jname, au, lon, sma = p["name"], p["au"], p["eclLon"], p["sma"]
        col = dict((j, c) for j, _, c, _ in _PLANETS)[jname]
        ang = math.radians(lon)
        ax.scatter(au * math.cos(ang), au * math.sin(ang), s=90,
                   color=tuple(c / 255 for c in col), edgecolor="white", lw=0.8, zorder=4)
        ax.annotate("{}\n{:.2f}AU".format(jname, au), (au * math.cos(ang), au * math.sin(ang)),
                    textcoords="offset points", xytext=(8, 6), fontsize=9, color="white",
                    zorder=5, bbox=dict(boxstyle="round,pad=0.15", fc="#00000099", ec="none"))

    for name, d in scene["asteroids"].items():
        if d.get("error"):
            continue
        ang = math.radians(d["eclLon"])
        ax.scatter(d["au"] * math.cos(ang), d["au"] * math.sin(ang),
                   color="#3ddc6a", marker="+", s=160, linewidths=2.5, zorder=6)
        ax.annotate("{}\n{:.2f}AU".format(name, d["au"]),
                    (d["au"] * math.cos(ang), d["au"] * math.sin(ang)),
                    textcoords="offset points", xytext=(8, 6), fontsize=10, color="#b6ffcf",
                    fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2", fc="#0a2b14d9", ec="#3ddc6a"))

    # 太陽
    ax.scatter(0, 0, s=300, color="#ffd86e", edgecolor="#fff5c2", lw=1.5, zorder=8)
    ax.annotate("太陽", (0, 0), textcoords="offset points", xytext=(-12, -26),
                fontsize=11, color="#ffe9a3", fontweight="bold", ha="center", zorder=9)

    ax.set_title("太陽系・惑星位置（線形距離の正確な俯瞰図）\n{} ・ 円=公転軌道(AU)".format(scene["time_utc"]),
                 fontsize=12, color="white", pad=15)
    ax.set_xlabel("X (AU)", color="#9aa")
    ax.set_ylabel("Y (AU)", color="#9aa")
    ax.tick_params(colors="#9aa")
    for sp in ax.spines.values():
        sp.set_color("#3a4466")
    ax.set_xlim(-45, 45); ax.set_ylim(-45, 45)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------- 選択ツール ----------
def solar_system_now(when=None, asteroid: Optional[str] = None,
                     asteroid2: Optional[str] = None, probe: Optional[str] = None,
                     probe2: Optional[str] = None, comet: Optional[str] = None,
                     comet2: Optional[str] = None, engine: str = "simple") -> CallToolResult:
    """太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置図を返す（認証不要）。

    例:「太陽系を上から見た図」「今の惑星の位置」「イトカワの今の位置を図で」
    「ボイジャー1号の現在位置を図で」「ハヤブサ2は今どこ？」「ハレー彗星は今どこ？」
    「紫金山・アトラス彗星の位置」
    惑星(8惑星＋冥王星)は JPL DE421 暦表、小惑星は JPL SBDB 軌道要素のケプラー伝播、
    遠方探査機(ボイジャー/パイオニア等)は JPL Horizons の状態ベクトル、
    彗星は周期彗星(ハレー等)を SBDB 軌道要素、非周期C/彗星を Horizons 状態ベクトルで計算。

    学生・観賞用途では視認性の高い Pillow 版(既定)を推奨。対数縮尺で内惑星から
    百数十AUの遠方天体までを一枚に収める。探査機・遠方彗星を指定すると表示範囲を
    自動拡張し、それぞれ色付き菱形マーカー・シアン色の尾を持つ彗星マーカーで強調する。
    遠方天体は線形の matplotlib 版では枠外のため、指定時は対数縮尺の Pillow 版を自動選択。

    engine で描画方法を選択:
      - "simple"(既定):   Pillow による視認性重視の合成。惑星を色アイコン、小惑星を緑十字、
        探査機を色付き菱形、彗星をシアンの核＋尾で強調。距離は対数縮尺。
      - "accurate":       matplotlib による線形距離の正確な俯瞰図（近距離のみ）。
    画像は content に base64 インライン表示、座標は structuredContent に JSON。

    Args:
        when: 時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        asteroid: 小惑星（例 "イトカワ"/"itokawa"/"25143", "ベンヌ", "アポフィス"）。
        asteroid2: 2つ目の小惑星。
        probe: 遠方探査機（例 "ボイジャー1号"/"voyager1"/"パイオニア10号"/"はやぶさ2"/
            "hayabusa2"）。はやぶさ2 は JPL Horizons ID -37。
        probe2: 2つ目の探査機。
        comet: 彗星（例 "ハレー彗星"/"halley"/"1P", "エンケ彗星", "67P",
             "紫金山・アトラス"/"C/2023 A3", "ラブジョイ"/"C/2014 Q2"）。
        comet2: 2つ目の彗星。
        engine: "simple"(既定/Pillow) / "accurate"(matplotlib)。
    """
    asts = [a for a in (asteroid, asteroid2) if a and str(a).strip()]
    prbs = [a for a in (probe, probe2) if a and str(a).strip()]
    coms = [a for a in (comet, comet2) if a and str(a).strip()]
    try:
        scene = _compute(when, asts, prbs, coms)
    except (OSError, KeyError, ValueError) as e:
        # de421.bsp の初回ダウンロード失敗・暦の読み込み失敗は例外が外へ漏れていた
        return CallToolResult(
            content=[TextContent(type="text", text="天体暦(JPL DE421)の読み込みに失敗しました: "
                                 + str(e)[:150] + "。初回はダウンロードが必要なため、ネットワーク接続をご確認ください。")],
            structuredContent={"error": str(e)[:200], "source": "JPL de421"},
        )
    if scene.get("error"):
        return CallToolResult(
            content=[TextContent(type="text", text=scene["error"])],
            structuredContent={"error": scene["error"]},
        )
    has_probe = any(not v.get("error") for v in scene["probes"].values())
    has_com = any(not v.get("error") for v in scene["comets"].values())
    eng = (engine or "simple").lower()
    if eng == "auto" or eng not in ("accurate", "simple"):
        eng = "simple"
    # 遠方探査機・彗星は線形(±45AU)では枠外 → 対数縮尺の Pillow 版へ
    if (has_probe or has_com) and eng == "accurate":
        eng = "simple"
    import base64
    try:
        if eng == "accurate":
            png = _render_accurate(scene)
            eng_label = "accurate (matplotlib, 線形距離)"
            alt = "太陽系の惑星・小惑星位置の線形距離俯瞰図"
        else:
            png = _render_simple(scene)
            eng_label = "simple (Pillow, 対数縮尺・視認性重視)"
            alt = "太陽を中心とした太陽系の惑星・小惑星・探査機・彗星位置の合成図"
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]},
        )
    img = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                       mimeType="image/png", altText=alt)
    lines = [
        "☀️ **太陽系俯瞰図（太陽中心・{}）**".format(eng_label),
        "時刻: {}".format(scene["time_utc"]),
        "**惑星位置（太陽からの距離AU）**: " + ", ".join(
            "{} {:.2f}AU".format(n, p["au"]) for n, p in scene["planets"].items()),
    ]
    if scene["asteroids"]:
        lines.append("**小惑星位置**（JPL SBDB 軌道要素 + ケプラー伝播）:")
        for n, d in scene["asteroids"].items():
            if d.get("error"):
                lines.append("- {}: 取得エラー（{}）".format(n, d["error"]))
            else:
                lines.append("- {}: {:.2f}AU・黄経 {:.1f}°・黄緯 {:.1f}°".format(
                    n, d["au"], d["eclLon"], d["eclLat"]))
    if scene["probes"]:
        lines.append("**遠方探査機位置**（JPL Horizons 状態ベクトル）:")
        for n, d in scene["probes"].items():
            if d.get("error"):
                lines.append("- {}: 取得エラー（{}）".format(n, d["error"]))
            else:
                lines.append("- {}: 真距離 {:.1f}AU・黄緯 {:.1f}°（黄道面投影 {:.1f}AU）".format(
                    n, d["au"], d["eclLat"], d["proj_au"]))
    if scene["comets"]:
        lines.append("**彗星位置**（周期=SBDB / C/=Horizons 状態ベクトル）:")
        for n, d in scene["comets"].items():
            if d.get("error"):
                lines.append("- {}: 取得エラー（{}）".format(n, d["error"]))
            else:
                lines.append("- {}: 真距離 {:.1f}AU・黄緯 {:.1f}°（黄道面投影 {:.1f}AU）".format(
                    n, d["au"], d["eclLat"], d["proj_au"]))
    lines.append("画像は上に表示（base64 PNG）。出典: JPL DE421+SBDB+Horizons / Skyfield")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), img],
        structuredContent={"time_utc": scene["time_utc"], "engine": eng_label,
                           "planets": scene["planets"], "asteroids": scene["asteroids"],
                           "probes": scene["probes"], "comets": scene["comets"],
                           "source": "JPL DE421+SBDB+Horizons / Skyfield"},
    )

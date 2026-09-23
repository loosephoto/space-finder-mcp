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

from .cache import TTL_DAILY, ttl_cache
from .name_common import split_names as _split_object_names
from .img_common import (RENDER_LOCK, apply_matplotlib_cjk_font, body_rgb,
                         conic_from_elements, figure_notes, figure_payload,
                         figure_text_block, load_font, media_link_line, primary_spec,
                         rgb_hex, save_output, scale_spec, symbol_rgb, verify_curve,
                         view_spec)

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
# 色は img_common.BODY_COLORS（共通の単一の出典）。sky_overlay と同じ値を使う
# （v0.27 で天王星だけ (150,210,220) と食い違っていたのを統一）。
_PLANETS = [
    ("水星", "mercury", body_rgb("水星"), 0.387),
    ("金星", "venus", body_rgb("金星"), 0.723),
    ("地球", "earth", body_rgb("地球"), 1.000),
    ("火星", "mars", body_rgb("火星"), 1.524),
    ("木星", "jupiter barycenter", body_rgb("木星"), 5.20),
    ("土星", "saturn barycenter", body_rgb("土星"), 9.58),
    ("天王星", "uranus barycenter", body_rgb("天王星"), 19.2),
    ("海王星", "neptune barycenter", body_rgb("海王星"), 30.1),
    ("冥王星", "pluto barycenter", body_rgb("冥王星"), 39.5),
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

    呼び出しごとに jd が変わる（when 省略時は現在時刻）ため、**分単位に丸めた
    時刻をキーにしてキャッシュ**する。探査機は1分で数km〜数十kmしか動かず、
    地図上は同一位置なので実用上問題ない。これで同一分内の再呼び出しは
    ネットワークへ出ない（実測 1.61s → 0.0Xs）。
    """
    jd_min = round(float(jd) * 1440.0) / 1440.0
    return _horizons_position_cached(str(cmd), jd_min)


@ttl_cache(TTL_DAILY, maxsize=256)
def _horizons_position_cached(cmd, jd):
    """分単位に丸めた jd をキーにした Horizons 取得（1分ごとに新キー＝自然に更新）。"""
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


def _comet_unknown_hint(name):
    """未知の彗星名に対する案内（指定できる名前の例と書式）。

    実測: この関数が未定義のまま呼ばれており、未知の彗星名を渡すと NameError が
    ツールの外へ漏れていた（規約1違反）。案内は「エイリアス表にある和名」＋
    「番号／仮符号の書式」を返す。
    """
    ja = sorted(k for k in _COMET_ALIASES if not k.isascii())
    return ("指定できる彗星の例: {}。周期彗星は番号（1P / 2P / 67P など）と和名、"
            "非周期彗星は \"C/2023 A3\" のような仮符号で指定します"
            "（JPL SBDB / Horizons の名称。{} は見つかりませんでした）").format(
                "、".join(ja), str(name)[:40])


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


# 彗星の描画色 (シアン系・彗星らしい) とマーカー指定（記号色は img_common が単一の出典）
_COMET_COLOR = symbol_rgb("comet")


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


@ttl_cache(TTL_DAILY, maxsize=256)
def _sbdb_elements(sstr):
    """JPL SBDB API から小惑星の軌道要素辞書を取得（認証不要）。"""
    # phys-par=true が要る: 彗星の全光度の式（M1/K1）は物理量側に入っており、
    # これが無いと光度チャートが予測光度を描けない（軌道要素だけでは足りない）。
    r = requests.get("https://ssd-api.jpl.nasa.gov/sbdb.api",
                     params={"sstr": sstr, "full-prec": "true", "phys-par": "true"},
                     headers=UA, timeout=25)
    r.raise_for_status()
    d = r.json()
    if "orbit" not in d or "elements" not in d["orbit"]:
        raise ValueError("軌道要素が見つかりません")
    elems = {el["name"]: el["value"] for el in d["orbit"]["elements"]}
    # 彗星は「近日点通過時刻 tp と近点距離 q」だけが与えられることがある（双曲線では
    # 半長軸 a・平均運動 n・平均近点角 ma が無い）。小惑星経路（a/ma/n でケプラー伝播）と
    # 彗星経路（q/tp から円錐曲線を解く＝comet_xyz_from_elements）の両方を受け付ける。
    if not all(k in elems for k in ("e", "i", "om", "w")) or not (
            all(k in elems for k in _REQUIRED_ELEMS) or ("q" in elems and "tp" in elems)):
        raise ValueError("SBDB 要素に必須キーが不足: {}".format(elems.keys()))
    fullname = d.get("object", {}).get("fullname") or sstr
    # epoch は orbit トップレベルにある（彗星では要素リストに無いため必須）
    epoch = float(d.get("orbit", {}).get("epoch") or elems.get("epoch", 2461200.5))
    out = {
        "e": float(elems["e"]), "i": math.radians(float(elems["i"])),
        "node": math.radians(float(elems["om"])), "argp": math.radians(float(elems["w"])),
        "epoch": epoch, "fullname": fullname,
    }
    for key in ("a", "ma", "n"):               # 楕円（小惑星・周期彗星）だけにある
        if elems.get(key) is not None:
            out[key] = math.radians(float(elems[key])) if key == "ma" else float(elems[key])
    # 彗星固有の量（近点距離・近日点通過時刻・周期・全光度の式）。ある場合だけ足すので、
    # 小惑星の呼び出し側の挙動は変わらない。軌道面ビューと光度チャートが同じ取得結果を共有する。
    for key, name_ in (("q", "q"), ("tp", "tp"), ("per", "period_days")):
        try:
            if elems.get(key) is not None:
                out[name_] = float(elems[key])
        except (TypeError, ValueError):
            continue
    kind = (d.get("object") or {}).get("kind")
    if kind:
        out["kind"] = kind
    phys = {p.get("name"): p.get("value") for p in (d.get("phys_par") or [])}
    for key in ("M1", "K1", "diameter"):
        try:
            if phys.get(key) is not None:
                out[key.lower()] = float(phys[key])
        except (TypeError, ValueError):
            continue
    return out


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


# ガウス重力定数（AU・日・太陽質量）。平均運動 n = k / a^1.5 [rad/day]
_K_GAUSS = 0.01720209895


def comet_xyz_from_elements(el, jd):
    """彗星の要素（e, q, tp）から日心黄道座標 (x,y,z,r,lon,lat) を返す。

    `_kepler_position` は SBDB の a/ma/n を使う**楕円専用**（1-e が負になる e>=1 や、
    半長軸の無い彗星では破綻する）で、小惑星の経路が使っている。彗星は SBDB/Horizons が
    **近日点通過時刻 tp と近点距離 q** を返すので、離心率で場合分けして一般の円錐曲線を解く
    （楕円＝ケプラー方程式／双曲線＝双曲線ケプラー方程式／e≈1＝バーカー式）。
    惑星摂動は入れない2体近似。i/node/argp は**ラジアン**（SBDB 形式）。
    """
    e = float(el["e"])
    q = el.get("q")
    tp = el.get("tp") if el.get("tp") is not None else el.get("tp_jd")
    if not q or tp is None:
        raise ValueError("彗星の要素に近点距離 q / 近日点通過時刻 tp がありません")
    q, tp = float(q), float(tp)
    dt = jd - tp
    if abs(e - 1.0) <= 1e-9:
        # 放物線（バーカー式）: tan(ν/2) + tan³(ν/2)/3 = D
        d_ = dt * _K_GAUSS / (math.sqrt(2.0) * q ** 1.5)
        t = math.copysign(max(abs(d_) ** (1.0 / 3.0), 1e-9), d_)
        for _ in range(80):
            step = (t + t ** 3 / 3.0 - d_) / (1.0 + t * t)
            t -= step
            if abs(step) < 1e-12:
                break
        nu = 2.0 * math.atan(t)
        r = q * (1.0 + t * t)
    elif e < 1.0:
        a = q / (1.0 - e)
        m = (_K_GAUSS / a ** 1.5) * dt
        ecc = m
        for _ in range(80):
            step = (m - (ecc - e * math.sin(ecc))) / (1.0 - e * math.cos(ecc))
            ecc += step
            if abs(step) < 1e-12:
                break
        nu = 2.0 * math.atan2(math.sqrt(1.0 + e) * math.sin(ecc / 2.0),
                              math.sqrt(1.0 - e) * math.cos(ecc / 2.0))
        r = a * (1.0 - e * math.cos(ecc))
    else:
        a = abs(q / (1.0 - e))                 # 双曲線は a<0。|a| を使う
        m = (_K_GAUSS / a ** 1.5) * dt
        hyp = math.asinh(m / e) if abs(m) > 1e-12 else 0.0
        for _ in range(80):
            fp = e * math.cosh(hyp) - 1.0
            if abs(fp) < 1e-12:
                break
            step = (e * math.sinh(hyp) - hyp - m) / fp
            hyp -= step
            if abs(step) < 1e-12:
                break
        nu = 2.0 * math.atan2(math.sqrt(e + 1.0) * math.sinh(hyp / 2.0),
                              math.sqrt(e - 1.0) * math.cosh(hyp / 2.0))
        r = a * (e * math.cosh(hyp) - 1.0)
    i, node, argp = el["i"], el["node"], el["argp"]
    u = argp + nu
    x = r * (math.cos(node) * math.cos(u) - math.sin(node) * math.sin(u) * math.cos(i))
    y = r * (math.sin(node) * math.cos(u) + math.cos(node) * math.sin(u) * math.cos(i))
    z = r * math.sin(u) * math.sin(i)
    lon = math.degrees(math.atan2(y, x)) % 360.0
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
    planet_errors = []
    for jname, key, col, sma in _PLANETS:
        try:
            v = eph[key].at(t) - sun.at(t)
            au = v.distance().au
            lat, lon, _ = v.frame_latlon(ecliptic_frame)
            planets[jname] = {"name": jname, "au": au,
                              "eclLon": float(lon.degrees), "eclLat": float(lat.degrees),
                              "sma": sma}
        except Exception as ex:
            planet_errors.append({"name": jname, "error": str(ex)[:120]})

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

    return {"time_utc": tstr, "planets": planets, "planet_errors": planet_errors,
            "asteroids": asts, "probes": prbs, "comets": coms, "asteroid_belt": True}


# ---------- 描画ヘルパー ----------
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
            dr.ellipse([px - rad - 14, py - 6, px + rad + 14, py + 6],
                       outline=symbol_rgb("ring"), width=5)
        elif jname == "木星":
            dr.line([px - rad, py - 8, px + rad, py - 8], fill=symbol_rgb("band"), width=3)
            dr.line([px - rad, py + 5, px + rad, py + 5], fill=symbol_rgb("band"), width=3)
        lx = px + rad + 8 if (px + rad + 170 < W) else px - rad - 178
        lx = max(lx, 10); ly = py - 10
        dr.rectangle([lx, ly, lx + 178, ly + 30], fill=(8, 10, 22, 230))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(jname, au), font=load_font(17, True),
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
        dr.ellipse([px - rad, py - rad, px + rad, py + rad], fill=symbol_rgb("asteroid"),
                   outline=(220, 255, 230), width=2)
        rr2 = scale(d["sma"])
        dr.ellipse([CX - rr2, CY - rr2, CX + rr2, CY + rr2], outline=symbol_rgb("asteroid"), width=2)
        lx, ly = px + 16, py - 14
        if lx + 240 > W:
            lx = px - rad - 250
        dr.rectangle([lx, ly, lx + 240, ly + 30], fill=(10, 40, 18, 235))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(d["name"], d["au"]), font=load_font(17, True),
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
        dr.text((lx + 6, ly + 3), "{}  {:.0f}AU".format(d["name"], d["au"]), font=load_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=load_font(13), fill=(255, 235, 190, 255))

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
        dr.text((lx + 6, ly + 3), "☄ {}  {:.1f}AU".format(d["name"], d["au"]), font=load_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=load_font(13), fill=(190, 235, 255, 255))

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([CX - 70, CY - 70, CX + 70, CY + 70], fill=(255, 230, 140, 220))
    glow = glow.filter(ImageFilter.GaussianBlur(30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse([CX - 26, CY - 26, CX + 26, CY + 26], fill=body_rgb("太陽"),
               outline=(255, 245, 200), width=2)
    dr.text((CX - 14, CY - 8), "太陽", font=load_font(18, True), fill=(120, 80, 0))

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
    dr.text((26, 16), title, font=load_font(30, True), fill=(255, 255, 255, 255))
    dr.text((26, 70), "{}（JST +9h）・数値=太陽からの距離AU ・ 円=惑星公転軌道(対数縮尺) ・ 補助線/菱形=探査機".format(scene["time_utc"]),
            font=load_font(18), fill=(195, 205, 235, 255))

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
            font=load_font(16), fill=(225, 232, 250, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()



def _render_accurate(scene):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    # 日本語フォントは OS 非依存の共通ヘルパーで設定する（Windows / macOS / Linux）。
    # 個別にフォント名を探すと macOS（ヒラギノ）・Linux（Noto CJK / IPA）で見つからない。
    apply_matplotlib_cjk_font()

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
        ast_rgb = symbol_rgb("asteroid")
        ax.scatter(d["au"] * math.cos(ang), d["au"] * math.sin(ang),
                   color=rgb_hex(ast_rgb), marker="+", s=160, linewidths=2.5, zorder=6)
        ax.annotate("{}\n{:.2f}AU".format(name, d["au"]),
                    (d["au"] * math.cos(ang), d["au"] * math.sin(ang)),
                    textcoords="offset points", xytext=(8, 6), fontsize=10,
                    color=rgb_hex(symbol_rgb("asteroid_label")),
                    fontweight="bold", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2", fc="#0a2b14d9",
                              ec=rgb_hex(ast_rgb)))

    # 太陽
    ax.scatter(0, 0, s=300, color=rgb_hex(body_rgb("太陽")), edgecolor="#fff5c2", lw=1.5, zorder=8)
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


# ---------- 彗星の軌道面ビュー（figure 注記つき） ----------
_COMET_ORBIT_COLOR = symbol_rgb("comet_orbit")   # 軌道線の色（verify_curve がこの色を画素から測る）


def _au_fmt(v):
    """AU 目盛・注記用の簡潔な表記。"""
    if v is None:
        return "-"
    fmt = "{:.3f}" if abs(v) < 1 else "{:.2f}"
    return fmt.format(v).rstrip("0").rstrip(".")


@ttl_cache(TTL_DAILY, maxsize=64)
def _horizons_elements(cmd):
    """JPL Horizons の円錐曲線要素（太陽中心・黄道面基準）を取得（認証不要）。

    C/彗星のような双曲線（半長軸 a が負）でも要素が取れる。
    """
    from datetime import datetime, timedelta, timezone
    d0 = datetime.now(timezone.utc)
    params = {
        "format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "ELEMENTS", "CENTER": "'500@10'",
        "START_TIME": "'{}'".format(d0.strftime("%Y-%m-%d")),
        "STOP_TIME": "'{}'".format((d0 + timedelta(days=1)).strftime("%Y-%m-%d")),
        "STEP_SIZE": "'1 d'", "REF_PLANE": "ECLIPTIC", "OUT_UNITS": "'AU-D'",
        "CSV_FORMAT": "'YES'",
    }
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                     headers=UA, timeout=30)
    r.raise_for_status()
    txt = r.text
    i, j = txt.find("$$SOE"), txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に要素ブロックがありません")
    rows = [ln for ln in txt[i + 6:j].strip().splitlines() if ln.strip()]
    if not rows:
        raise ValueError("Horizons の要素が空です")
    c = [v.strip() for v in rows[0].split(",")]
    # 列: JDTDB, 日付, EC, QR, IN, OM, W, Tp, N, MA, TA, A, AD, PR
    return {"e": float(c[2]), "q": float(c[3]), "i_deg": float(c[4]),
            "node_deg": float(c[5]), "argp_deg": float(c[6]), "tp_jd": float(c[7]),
            "a": float(c[11]), "period_days": float(c[13])}


def _hz_step(step_days):
    """Horizons の STEP_SIZE 文字列を作る。

    Horizons は小数付きの刻み（"1.5 d" や "6.5 h"）を受け付けず、整数＋単位のみ
    （"1 d" / "21 h" / "90 m"）を受け付ける。実測で小数は "No ephemeris" になったので、
    分へ丸めてから時間・分へ振り分ける。
    """
    minutes = max(1, int(round(float(step_days) * 1440.0)))
    if minutes % 60 == 0:
        return "{} h".format(minutes // 60)
    return "{} m".format(minutes)


@ttl_cache(TTL_DAILY, maxsize=64)
def _horizons_cmd_for(cid):
    """彗星 id から Horizons の一意なコマンド文字列を作る。

    ハレー彗星のように彗星が出現回（apparition）ごとに複数レコード登録されている場合、
    `DES=1P;` は "Matching small-bodies" の一覧を返すだけで状態ベクトルが得られない
    （実測）。`DES=1P;CAP` は現在の出現回を選ぶので一意に解決する（実測で
    "Target body name: 1P/Halley" を返す）。まず CAP なしを試し、駄目なら CAP を付ける。
    """
    last = None
    for cmd in ("DES={};".format(cid), "DES={};CAP".format(cid)):
        try:
            _horizons_vectors_range(cmd, 2461250.5, 2461250.6, 0.1)
            return cmd
        except Exception as e:                  # 解決できない候補は次へ
            last = e
    raise ValueError("Horizons が {} の状態ベクトルを返しません（{}）".format(cid, last))


@ttl_cache(TTL_DAILY, maxsize=64)
def _horizons_vectors_range(cmd, jd0, jd1, step_days):
    """JPL Horizons の太陽中心状態ベクトルを期間まとめて取得（黄道 J2000・AU/D）。

    1リクエストで期間全体が返るので、彗星の見え方チャート（comet_apparition）は
    SBDB 要素の2体近似ではなく **n 体解の位置** を使える。実測: 2体近似（SBDB の
    近日点通過時刻から解く）は JPL の n 体解と最大 0.0249 au（372万 km）ずれ、
    地球最接近の時刻も約1.2日ずれていた（169P/NEAT・2026年8月〜9月）。

    列: JDTDB, 日付, X, Y, Z, VX, VY, VZ（CSV_FORMAT=YES・VEC_TABLE=2）。
    """
    params = {
        "format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS", "CENTER": "'500@10'",
        "START_TIME": "'JD{:.6f}'".format(float(jd0)), "STOP_TIME": "'JD{:.6f}'".format(float(jd1)),
        "STEP_SIZE": "'{}'".format(_hz_step(step_days)),
        # REF_PLANE='FRAME'（ICRF）。'ECLIPTIC'（=黄道 J2000）だと DE421 の地球位置と
        # 0.003 au（45万 km）食い違い、地心距離が混ざる（実測）。ICRF なら 1e-7 au 一致。
        "REF_PLANE": "'FRAME'",
        "OUT_UNITS": "'AU-D'", "VEC_TABLE": "'2'", "CSV_FORMAT": "'YES'",
    }
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                     headers=UA, timeout=(10, 60))
    r.raise_for_status()
    txt = r.text
    i, j = txt.find("$$SOE"), txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に状態ベクトルのブロックがありません")
    out = []
    for ln in txt[i + 6:j].strip().splitlines():
        c = [v.strip() for v in ln.split(",")]
        if len(c) < 5:
            continue
        try:
            out.append((float(c[0]), float(c[2]), float(c[3]), float(c[4])))
        except ValueError:
            continue
    if len(out) < 2:
        raise ValueError("Horizons の状態ベクトルが不足しています（{} 行）".format(len(out)))
    return out


def _comet_elements(name):
    """彗星の軌道要素（太陽中心）を返す。返すのは (id, 要素辞書)。

    - 周期彗星（エイリアス/1P 等）: JPL SBDB（楕円。i/node/argp は度へ変換）
    - C/彗星: JPL Horizons（双曲線対応。a が負になり得る）
    """
    typ, cid = "sbdb", name
    alias = (_COMET_ALIASES.get(name) or _COMET_ALIASES.get(name.lower())
             or _COMET_ALIASES.get(str(name).replace("彗星", "")))
    if alias:
        typ, cid = alias
    elif str(name).strip().upper().startswith("C/"):
        typ, cid = "horizons", str(name).strip()
    if typ == "horizons":
        el = _horizons_elements(cid)
        el.update({"typ": "horizons", "fullname": cid,
                   "source": "JPL Horizons（太陽中心・黄道面要素）"})
        return cid, el
    el = _sbdb_elements(cid)                      # i/node/argp はラジアン
    if "a" not in el:
        # 双曲線（半長軸が無い）彗星。軌道面ビューは Horizons 経路で扱う。
        raise ValueError("SBDB に半長軸が無い軌道です（C/ 彗星は Horizons 経路を使用）")
    a, e = el["a"], el["e"]
    return cid, {"typ": "sbdb", "fullname": el.get("fullname") or cid, "e": e, "a": a,
                 "_raw": el,                       # ケプラー伝播（_kepler_position）用
                 "q": a * (1.0 - e) if e < 1.0 else abs(a) * (e - 1.0),
                 "i_deg": math.degrees(el["i"]), "node_deg": math.degrees(el["node"]),
                 "argp_deg": math.degrees(el["argp"]), "period_days": None,
                 "source": "JPL SBDB（楕円軌道要素 + ケプラー伝播）"}


def _conic_orbit_points(a, e, q, r_max, n=720):
    """軌道面内（近日点方向=+x, AU）の点列。楕円は閉曲線、放物線/双曲線は r_max で切る。"""
    p = (a * (1.0 - e * e)) if (a is not None and abs(e - 1.0) > 1e-9) else (2.0 * q)
    if e < 1.0:
        nu_lim = math.pi
    else:
        cosl = (p / max(r_max, 1e-9) - 1.0) / e
        nu_lim = math.acos(max(-1.0, min(1.0, cosl))) * 0.999
    pts = []
    for k in range(n + 1):
        nu = -nu_lim + 2.0 * nu_lim * k / n
        den = 1.0 + e * math.cos(nu)
        if abs(den) < 1e-12:
            continue
        rr = p / den
        pts.append((rr * math.cos(nu), rr * math.sin(nu)))
    return pts


def _ecliptic_to_perifocal(x, y, z, i_deg, node_deg, argp_deg):
    """日心黄道座標(AU) -> 軌道面内座標（近日点方向=+x, AU）。"""
    i, node, argp = math.radians(i_deg), math.radians(node_deg), math.radians(argp_deg)
    cn, sn = math.cos(node), math.sin(node)
    x1, y1 = cn * x + sn * y, -sn * x + cn * y
    ci, si = math.cos(i), math.sin(i)
    y2 = ci * y1 + si * z
    ca, sa = math.cos(argp), math.sin(argp)
    return ca * x1 + sa * y2, -sa * x1 + ca * y2


def _render_comet_orbit(cid, el, pos_xyz, when_str):
    """彗星の軌道を「彗星自身の軌道面を真横から見た図」として描く。

    太陽は円錐曲線の焦点（楕円の中心ではない）。e>=1 の C/彗星は双曲線の枝として
    近日点から有限距離(r_max)までを描く。返すのは (PIL画像, figure ブロック)。
    """
    from PIL import Image, ImageDraw, ImageFilter
    W, H = 1400, 900
    sun_r_px = 12.0                     # 誇張した太陽円盤の半径(px)。描画・注記・検証で共有
    a, e, q = el.get("a"), float(el["e"]), float(el["q"])
    incl = el.get("i_deg")
    if e < 1.0:
        apo = abs(a) * (1.0 + e) if a is not None else None
        r_max = max(apo or q * 4.0, q * 1.5) * 1.06
    else:
        apo = None
        r_max = min(20.0, max(2.0, 8.0 * q))
    pts = _conic_orbit_points(a, e, q, r_max)
    xp, yp = _ecliptic_to_perifocal(pos_xyz[0], pos_xyz[1], pos_xyz[2], incl or 0.0,
                                    el.get("node_deg", 0.0), el.get("argp_deg", 0.0))
    r_now = math.hypot(xp, yp)

    img = Image.new("RGB", (W, H), (10, 14, 26))
    dr = ImageDraw.Draw(img)
    f_t, f_s, f_m, f_b = load_font(26, True), load_font(17), load_font(20, True), load_font(15)

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bx, by = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    pl, ptop, pr, pb = 70, 150, W - 70, H - 150
    span_x, span_y = (max(xs) - min(xs)), (max(ys) - min(ys))
    ppau = min((pr - pl) / max(span_x, 1e-6), (pb - ptop) / max(span_y, 1e-6)) * 0.92
    X0 = (pl + pr) / 2.0 - bx * ppau          # 太陽＝焦点の画面x
    Y0 = (ptop + pb) / 2.0 + by * ppau        # 太陽＝焦点の画面y
    to_px = lambda x, y: (X0 + x * ppau, Y0 - y * ppau)
    boxes = []
    seq = list(pts) + ([pts[0]] if (e < 1.0 and pts) else [])
    pts_px = [to_px(px_, py_) for (px_, py_) in seq]

    mask_ink = None           # 曲線を膨張したマスク（label() が配置可否の判定に使う）
    skipped_labels: list = []  # どこにも置けなかったラベル（注記に出して情報を落とさない）

    def label(cands, text, font, fill):
        """曲線マスクと重ならない候補にだけ描く（候補: (x, y, anchor)）。

        どの候補に置いても曲線に載るなら**描かない**。曲線へ重ねて描くと図と注記が
        食い違い（自己検証の label_overlap_px も 0 にならない）、fallback で画面外へ
        はみ出すと描画自体が落ちる。置けなかった文字列は skipped_labels に残して注記に出す。
        """
        for (x, y, anc) in cands:
            bb = tuple(int(v) for v in dr.textbbox((x, y), text, font=font, anchor=anc))
            if bb[0] < 6 or bb[1] < 4 or bb[2] > W - 6 or bb[3] > H - 92:
                continue
            if mask_ink is not None and mask_ink.crop(
                    (bb[0] - 4, bb[1] - 4, bb[2] + 4, bb[3] + 4)).getbbox() is not None:
                continue
            if any(bb[0] - 8 <= qx <= bb[2] + 8 and bb[1] - 8 <= qy <= bb[3] + 8
                   for (qx, qy) in pts_px):
                continue
            boxes.append(bb)
            dr.text((x, y), text, font=font, fill=fill, anchor=anc)
            return True
        skipped_labels.append(text)
        return False

    dr.line(pts_px, fill=_COMET_ORBIT_COLOR, width=3)
    # ラベル配置用マスク: 描いた曲線を膨張させ、文字が線に載らない候補だけを採る
    # （720点の頂点サンプルだけでは、頂点間隔の粗い針状軌道で隙間をすり抜ける）
    _mask = Image.new("1", (W, H), 0)
    ImageDraw.Draw(_mask).line(pts_px, fill=1, width=5)
    mask_ink = _mask.filter(ImageFilter.MaxFilter(7))

    hx, hy = to_px(q, 0.0)
    dr.ellipse([hx - 5, hy - 5, hx + 5, hy + 5], fill=(255, 90, 90))
    label([(hx + 12, hy + 12, "la"), (hx + 12, hy - 34, "la"), (hx - 170, hy + 12, "la"),
           (hx - 170, hy - 34, "la"), (hx + 16, hy + 44, "la"), (hx - 190, hy + 44, "la")],
          "近日点", f_s, (255, 150, 150))
    ax_ = ay_ = None
    if apo:
        ax_, ay_ = to_px(-apo, 0.0)
        dr.ellipse([ax_ - 5, ay_ - 5, ax_ + 5, ay_ + 5], fill=(150, 190, 255))
        label([(ax_ - 12, ay_ + 12, "ra"), (ax_ - 12, ay_ - 34, "ra"),
               (ax_ + 14, ay_ + 12, "la"), (ax_ + 14, ay_ - 34, "la"),
               (ax_ - 12, ay_ + 44, "ra"), (ax_ + 14, ay_ + 44, "la")],
              "遠日点", f_s, (150, 190, 255))
    sr = sun_r_px
    dr.ellipse([X0 - sr, Y0 - sr, X0 + sr, Y0 + sr], fill=body_rgb("太陽"),
               outline=(255, 245, 200), width=2)
    label([(X0 + sr + 8, Y0 - sr - 26, "la"), (X0 + sr + 8, Y0 + sr + 6, "la"),
           (X0 - sr - 96, Y0 - sr - 26, "la"), (X0 - sr - 96, Y0 + sr + 6, "la"),
           (X0 + sr + 8, Y0 - 10, "la"), (X0 - sr - 96, Y0 - 10, "la"),
           (X0 - 46, Y0 + sr + 34, "la"), (X0 - 46, Y0 - sr - 54, "la")],
          "太陽＝焦点", f_m, (255, 235, 170))
    cx_, cy_ = to_px(xp, yp)
    dr.ellipse([cx_ - 7, cy_ - 7, cx_ + 7, cy_ + 7], fill=_COMET_COLOR,
               outline=(255, 255, 255), width=2)
    label([(cx_ + 14, cy_ - 40, "la"), (cx_ + 14, cy_ + 16, "la"),
           (cx_ - 250, cy_ - 40, "la"), (cx_ - 250, cy_ + 16, "la"),
           (cx_ + 14, cy_ + 66, "la"), (cx_ - 250, cy_ + 66, "la"),
           (cx_ + 14, cy_ - 90, "la"), (cx_ - 250, cy_ - 90, "la")],
          "現在位置", f_s, (170, 240, 255))
    nu_now = math.atan2(yp, xp)
    nu2 = nu_now + math.radians(0.8)
    p_ = (a * (1.0 - e * e)) if (a is not None and abs(e - 1.0) > 1e-9) else (2.0 * q)
    rr2 = p_ / max(1e-12, 1.0 + e * math.cos(nu2))
    q2 = to_px(rr2 * math.cos(nu2), rr2 * math.sin(nu2))
    ang_a = math.atan2(q2[1] - cy_, q2[0] - cx_)
    dr.line([cx_, cy_, q2[0], q2[1]], fill=(255, 255, 255), width=3)
    for sgn in (1, -1):
        dr.line([(q2[0], q2[1]),
                 (q2[0] - 14 * math.cos(ang_a - 0.45 * sgn),
                  q2[1] - 14 * math.sin(ang_a - 0.45 * sgn))], fill=(255, 255, 255), width=3)
    for au in (0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0):
        if au > r_max * 1.02:
            continue
        tx, ty = to_px(-au, 0.0)
        dr.line([(tx, ty - 8), (tx, ty + 8)], fill=(150, 160, 185), width=2)
        label([(tx - 20, ty + 12, "la"), (tx - 20, ty - 28, "la"),
               (tx + 8, ty + 12, "la"), (tx + 8, ty - 28, "la"),
               (tx - 20, ty + 42, "la"), (tx - 20, ty - 58, "la"),
               (tx + 8, ty + 42, "la"), (tx + 8, ty - 58, "la")], _au_fmt(au), f_b,
              (160, 172, 195))
        if abs(au - 1.0) < 1e-9:
            label([(tx - 66, ty - 36, "la"), (tx - 66, ty + 14, "la"),
                   (tx - 66, ty - 62, "la"), (tx + 26, ty - 36, "la"),
                   (tx + 26, ty + 14, "la")], "地球軌道 1 AU", f_b, (140, 175, 240))
    dr.line([(pl + 10, pb + 62), (pl + 10 + 1.0 * ppau, pb + 62)], fill=(210, 214, 230), width=3)
    dr.text((pl + 14, pb + 36), "1 AU", font=f_b, fill=(210, 214, 230))

    title = "{} の軌道（彗星自身の軌道面を真横から見た図）".format(el.get("fullname") or cid)
    dr.text((36, 26), title, font=f_t, fill=(238, 242, 255))
    dr.text((36, 66), "e={:.6f}・近日点 {} AU{}".format(
        e, _au_fmt(q), "・遠日点 {} AU".format(_au_fmt(apo)) if apo else "・遠日点なし（閉じない軌道）"),
        font=f_s, fill=(255, 200, 140))
    dr.text((36, 92), "観測時刻 {} ・ {}".format(when_str, el.get("source", "")),
            font=f_b, fill=(180, 190, 215))
    dr.rectangle([20, H - 74, W - 20, H - 16], fill=(0, 0, 0, 210))
    dr.text((34, H - 62),
            "☀ 太陽＝焦点（中心ではない）　● 現在位置　赤● 近日点　青● 遠日点　"
            "目盛=日心距離(AU)　太陽・マーカーは実寸ではありません",
            font=f_b, fill=(225, 232, 250))
    dr.text((34, H - 38), "出典: {}".format(el.get("source", "")), font=f_b, fill=(190, 200, 220))

    conic = conic_from_elements(a=a, e=e, q=q, incl_deg=incl)
    extra = [
        "●は{}時点の彗星位置（日心距離 {:.3f} AU・この軌道面内の実際の位置）".format(when_str, r_now),
        "太陽・マーカーの大きさは誇張している（軌道の縮尺と同一ではない）",
        "惑星や地球の位置は描いていない。目盛の 1 AU は地球軌道の半径（距離の目安）",
        "要素は指定時刻付近の接触軌道要素。惑星の摂動で実際の道は変わる",
    ]
    # 超長距離の楕円では近日点が誇張した太陽円盤の内側に入り、画素からは近点距離を
    # 確認できない（曲線の右端画素は円盤の縁になる）。「確認できない」ことを注記にも
    # 数値から生成して残す（黙って合格にしない）。
    if q * ppau < sun_r_px + 2.0:
        extra.append(
            "近日点 {} AU は画面上 {:.2f} px で、誇張した太陽の描画円盤（半径 {} px ＝ 約 {} AU）の"
            "内側にある。この縮尺では図から近点距離を確認できないため、自己検証は遠日点側と"
            "「近点側の上界」で行っている".format(
                _au_fmt(q), q * ppau, sun_r_px, _au_fmt(sun_r_px / max(ppau, 1e-12))))
    if skipped_labels:
        extra.append("図が混み合って図中に配置できなかったラベル（{}）。値は上の注記と見出しにある".format(
            "／".join(skipped_labels)))
    notes = figure_notes(
        conic, primary="太陽", unit="AU", periapsis_label="近日点", apoapsis_label="遠日点",
        extra=extra)
    caption = ("{} の軌道。太陽を焦点とする{}（e={:.6f}、近日点 {} AU{}）。{}時点の日心距離は {:.3f} AU。".format(
        el.get("fullname") or cid, "楕円" if e < 1.0 else "双曲線の枝", e, _au_fmt(q),
        "・遠日点 {} AU".format(_au_fmt(apo)) if apo else "・遠日点なし", when_str, r_now))
    markers = [{"id": "periapsis", "label": "近日点", "au": q, "px": [round(hx), round(hy)]},
               {"id": "current", "label": "現在位置", "au": r_now, "px": [round(cx_), round(cy_)]}]
    if apo:
        markers.append({"id": "apoapsis", "label": "遠日点", "au": apo,
                        "px": [round(ax_), round(ay_)]})
    fig = figure_payload(
        kind="orbit_plane", title=title,
        view=view_spec("orbital_plane", "side",
                       "彗星の軌道面を真横から見た図（太陽は円錐曲線の焦点）",
                       why="上から見た黄道面俯瞰では高傾斜・高離心率の彗星軌道が潰れて見え、"
                           "焦点と中心の違いも判別できないため"),
        primary=primary_spec("太陽", "focus", center_offset=conic.c, unit="AU"),
        scale=scale_spec("linear", to_scale=True, px_per_unit=ppau, unit="AU",
                         exaggerated=["太陽の円盤", "近日点・現在位置のマーカー"]),
        conic=conic, markers=markers, notes=notes, caption=caption,
        verify=verify_curve(img, color=_COMET_ORBIT_COLOR, focus_xy=(X0, Y0),
                            px_per_unit=ppau, periapsis=q, apoapsis=apo,
                            tol_ratio=0.06, label_boxes=boxes,
                            occluders=[(X0, Y0, sun_r_px)]),
    )
    return img, fig


def _comet_orbit_result(name, when_iso=None):
    """彗星の軌道面ビュー（figure 注記つき）を返す。"""
    import base64
    loader, _eph = _load()
    ts = loader.timescale()
    t = _resolve_when(when_iso, ts)
    if t is None:
        msg = "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg})
    jd = t.tt
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")
    try:
        cid, el = _comet_elements(name)
    except (requests.RequestException, ValueError, KeyError) as e:
        msg = "彗星の軌道要素を取得できませんでした（{}）: {}".format(name, str(e)[:150])
        if not str(name).isascii():
            msg += "\n" + _comet_unknown_hint(name)
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": str(name),
                                                 "source": "JPL SBDB / Horizons"})
    try:
        if el["typ"] == "horizons":
            x, y, z, rr, lon, lat = _horizons_position(cid, jd)
        else:
            x, y, z, rr, lon, lat = _kepler_position(el.get("_raw") or el, jd)
    except (requests.RequestException, ValueError, KeyError) as e:
        msg = "彗星の位置を取得できませんでした（{}）: {}".format(name, str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": str(name)})
    try:
        img, fig = _render_comet_orbit(cid, el, (x, y, z), tstr)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception as e:                      # 描画系の想定外もツール外へ漏らさない
        msg = "軌道図の生成に失敗しました: {}".format(str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg})
    imgc = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                        mimeType="image/png",
                        altText="{} の軌道（軌道面を真横から見た図）".format(el.get("fullname") or cid))
    # インライン画像を描けないハーネス向け: 保存してリンクを先頭に出す
    out_path = save_output(png, "solar_system_comet_orbit", "png")
    lines = [
        media_link_line("生成した画像を開く（{} の彗星軌道面ビュー）".format(
            el.get("fullname") or cid), path=out_path, kind="figure"),
        "☄️ **{} の軌道（彗星自身の軌道面を真横から見た図）**".format(el.get("fullname") or cid),
        "時刻: {}".format(tstr),
        "離心率 e={:.6f} ・ 近日点 {} AU ・ {}".format(
            el["e"], _au_fmt(el["q"]),
            "遠日点 {} AU".format(_au_fmt(fig["conic"].get("apo"))) if not fig["conic"]["closed"]
            else "遠日点 {} AU（閉じた楕円）".format(_au_fmt(fig["conic"].get("apo")))),
        "指定時刻の日心距離: {:.3f} AU ・ 黄経 {:.1f}° ・ 黄緯 {:.1f}°".format(rr, lon, lat),
        "",
        figure_text_block(fig),
        "出典: {} ／ 描画: Pillow".format(el.get("source", "")),
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "time_utc": tstr, "comet": el.get("fullname") or cid, "id": cid,
            "e": el["e"], "a_au": el.get("a"), "q_au": el["q"],
            "incl_deg": el.get("i_deg"), "typ": el["typ"],
            "au": rr, "eclLon": lon, "eclLat": lat,
            "figure": fig, "image_path": out_path, "source": el.get("source", ""),
        },
    )


# 1枚に並べるパネルの上限（文字が読めるサイズを保つため。超過分は描画せず、その旨を明示する）
_COMET_PANEL_MAX = 4


def _comet_multi_result(names, when_iso=None):
    """複数の彗星を「1彗星=1パネル」で縦に並べた1枚の画像を返す。

    彗星ごとに**軌道面も縮尺も違う**ため、1つの座標系に重ねると嘘になる
    （軌道面が違う＝真横から見た形は同時に成立しない／a が2桁違うと小さい軌道が点になる）。
    そこで各パネルは既存の単体描画（その彗星自身の軌道面・独自の縮尺）をフル解像度のまま
    並べ、figure には「パネルごとに縮尺が違う」ことを数値から生成した注記として入れる。
    1天体の取得・描画に失敗しても他パネルは描き、失敗理由を panels[].error に残す。
    """
    import base64
    loader, _eph = _load()
    ts = loader.timescale()
    t = _resolve_when(when_iso, ts)
    if t is None:
        msg = "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg})
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")
    jd = t.tt
    requested = [n for n in names if n]
    picked = requested[:_COMET_PANEL_MAX]
    imgs, panels, failed = [], [], []
    for nm in picked:
        try:
            cid, el = _comet_elements(nm)
            if el["typ"] == "horizons":
                x, y, z, rr, lon, lat = _horizons_position(cid, jd)
            else:
                x, y, z, rr, lon, lat = _kepler_position(el.get("_raw") or el, jd)
            img, fig = _render_comet_orbit(cid, el, (x, y, z), tstr)
        except (requests.RequestException, ValueError, KeyError) as e:
            failed.append({"comet": nm, "error": str(e)[:150]}); continue
        except Exception as e:                       # 描画系の想定外もこの彗星だけ落とす
            failed.append({"comet": nm, "error": "描画に失敗: " + str(e)[:120]}); continue
        imgs.append(img)
        panels.append({
            "markers": [dict(m, panel=len(imgs) + 1, px_in_panel=m.get("px"))
                        for m in (fig.get("markers") or [])],
            "name": el.get("fullname") or cid, "id": cid, "typ": el["typ"],
            "e": el["e"], "a_au": el.get("a"), "q_au": el["q"], "incl_deg": el.get("i_deg"),
            "au": rr, "px_per_AU": fig["scale"].get("px_per_AU"),
            "conic": fig["conic"], "verify": fig["verify"],
            "notes": fig["notes"], "source": el.get("source", ""),
        })
    if not imgs:
        msg = "指定された彗星の軌道要素を取得できませんでした: " + " / ".join(
            "{}（{}）".format(f["comet"], f["error"]) for f in failed)
        for f in failed:
            if not str(f["comet"]).isascii():
                msg += "\n" + _comet_unknown_hint(f["comet"])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "errors": failed,
                                                 "query": requested})
    # 縦に合成（各パネルはフル解像度のまま。縮小するとラベルが読めなくなる）
    from PIL import Image, ImageDraw
    gap = 10
    w, h = imgs[0].size
    canvas = Image.new("RGB", (w, h * len(imgs) + gap * (len(imgs) - 1)), (10, 14, 26))
    for i, im in enumerate(imgs):
        canvas.paste(im, (0, i * (h + gap)))
    png = None
    try:
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception as e:
        msg = "複数彗星の図の生成に失敗しました: {}".format(str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg})
    out_path = save_output(png, "solar_system_comet_orbit_multi", "png")

    # ---- 注記は数値から生成（パネル間で縮尺が違うことを必ず書く）----
    aa = [p_["a_au"] for p_ in panels if p_.get("a_au")]
    ratio_txt = ""
    if aa and min(abs(v) for v in aa) > 0:
        ratio = max(abs(v) for v in aa) / min(abs(v) for v in aa)
        if ratio >= 1.5:
            ratio_txt = "（a は {} 倍の開き）".format(_au_fmt(ratio))
    notes = [
        "この画像はパネルの並びで、**1パネル＝1彗星をその彗星自身の軌道面で真横から見た図**。"
        "太陽はどのパネルでも円錐曲線の焦点（楕円の中心ではない）",
        "**パネルごとに縮尺が違う**。同じ長さの線でも表す距離はパネル間で一致しない"
        "（内向きの目盛と 1 AU スケールバーはそのパネルの中だけで有効）" + ratio_txt,
        "軌道面の向き・傾斜は彗星ごとに違う。パネルをまたいで軌道の形や大きさを"
        "そのまま重ね合わせて比べることはできない（視線方向も縮尺も揃っていない）",
    ]
    for i, p_ in enumerate(panels, 1):
        apo = p_["conic"].get("apo")
        notes.append("パネル{}: {} ／ e={:.6f} ／ 近日点 {} AU ／ {}".format(
            i, p_["name"], p_["e"], _au_fmt(p_["q_au"]),
            "遠日点 {} AU（閉じた楕円）".format(_au_fmt(apo)) if p_["conic"].get("closed")
            else "遠日点なし（閉じない軌道）"))
        if not p_["verify"].get("periapsis_resolvable", True):
            notes.append("パネル{}（{}）: 近日点は画面上で分解できず、この縮尺では図から確認できない"
                         "（自己検証は遠日点側と近点側の上界）".format(i, p_["name"]))
    if failed:
        notes.append("取得できなかった天体: " + " / ".join(
            "{}（{}）".format(f["comet"], f["error"]) for f in failed))
    if len(requested) > len(picked):
        notes.append("要求 {} 件のうち {} 件を描画（1枚に並べられる上限 {} パネル）。"
                     "残り: {}".format(len(requested), len(picked), _COMET_PANEL_MAX,
                                     "、".join(requested[len(picked):])))
    title = "彗星{}天体の軌道（パネルごとにその彗星自身の軌道面・縮尺）".format(len(panels))
    caption = ("彗星{}天体（{}）の軌道を1彗星1パネルで並べた図。パネルごとに軌道面と縮尺が異なり、"
               "太陽は各パネルの焦点。{}時点の日心距離はパネル内に記載。").format(
        len(panels), "、".join(p_["name"] for p_ in panels), tstr)
    scale = scale_spec("per_panel_linear", to_scale=True, unit="AU",
                       exaggerated=["太陽の円盤", "近日点・現在位置のマーカー"])
    scale["per_panel_px_per_AU"] = {p_["name"]: p_["px_per_AU"] for p_ in panels}
    fig = figure_payload(
        kind="orbit_plane_set", title=title,
        view=view_spec("orbital_plane_per_panel", "side",
                       "各パネルはその彗星自身の軌道面を真横から見た図（パネルごとに独立）",
                       why="彗星ごとに軌道面が異なるため、1つの座標系に重ねると『真横から見た形』が"
                           "同時に成立しない。a（軌道長半径）が2桁以上違うと共通縮尺では小さい軌道が"
                           "点や線に潰れるため、縮尺もパネル独立にしている"),
        primary=primary_spec("太陽", "focus",
                             note="どのパネルでも太陽はそのパネルの円錐曲線の焦点"),
        scale=scale,
        markers=[m for p_ in panels for m in (p_.get("markers") or [])],
        notes=notes, caption=caption,
        verify={"ok": all(p_["verify"].get("ok") for p_ in panels),
                "panels": [{"name": p_["name"], "ok": p_["verify"].get("ok"),
                            "periapsis_check": p_["verify"].get("periapsis_check"),
                            "label_overlap_px": p_["verify"].get("label_overlap_px")}
                           for p_ in panels],
                "label_overlap_px": sum(p_["verify"].get("label_overlap_px") or 0 for p_ in panels)})
    fig["panels"] = [{"name": p_["name"], "conic": p_["conic"], "verify": p_["verify"],
                      "px_per_AU": p_["px_per_AU"], "notes": p_["notes"]} for p_ in panels]
    imgc = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                        mimeType="image/png",
                        altText="彗星{}天体の軌道（各パネルはその彗星自身の軌道面）".format(len(panels)))
    lines = [media_link_line("生成した画像を開く（彗星{}天体の軌道パネル）".format(len(panels)),
                             path=out_path, kind="figure"),
             "☄️ **彗星{}天体の軌道（1彗星=1パネル／各パネルはその彗星自身の軌道面）**".format(len(panels)),
             "時刻: {}".format(tstr)]
    for i, p_ in enumerate(panels, 1):
        lines.append("{}. {}: e={:.6f} ・ 近日点 {} AU ・ {} ・ 指定時刻の日心距離 {:.3f} AU".format(
            i, p_["name"], p_["e"], _au_fmt(p_["q_au"]),
            "遠日点 {} AU".format(_au_fmt(p_["conic"].get("apo"))) if p_["conic"].get("closed")
            else "遠日点なし（閉じない軌道）", p_["au"]))
    lines += ["", figure_text_block(fig),
              "出典: " + " / ".join(sorted({p_["source"] for p_ in panels if p_["source"]}))
              + " ／ 描画: Pillow"]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "time_utc": tstr, "requested": requested,
            "requested_count": len(requested), "drawn_count": len(panels),
            "comets": [{k: v for k, v in p_.items() if k not in ("notes",)} for p_ in panels],
            "errors": failed, "figure": fig, "image_path": out_path,
        },
    )


# ---------- 選択ツール ----------
def solar_system_now(when=None, asteroid: Optional[str] = None,
                     asteroid2: Optional[str] = None, probe: Optional[str] = None,
                     probe2: Optional[str] = None, comet: Optional[str] = None,
                     comet2: Optional[str] = None, engine: str = "simple",
                     view: str = "system", days: int = 180) -> CallToolResult:
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
    structuredContent.figure には「この図をどう描いたか」の注記（figure/1）が入る。
    ⚠️ figure.notes は図の誤読を防ぐための注記なので、要約・言い換えせずそのまま引用すること。

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
        view: "system"(既定)=太陽系俯瞰図 / "comet_orbit"=彗星の軌道面ビュー /
            "apparition"=彗星の見え方チャート（地心距離・日心距離・予想光度・太陽離角の
            推移。comet の指定が必須で、1天体ずつ）。
            comet_orbit は comet の指定が必須で、彗星自身の軌道面を真横から見た図
            （太陽＝円錐曲線の焦点）を返す。e>=1 の C/彗星は閉じない双曲線の枝として描く。
            **comet にカンマ区切りで複数（または comet2 を併用、最大4天体）指定すると、
            1彗星=1パネルで並べた1枚の画像**を返す（パネルごとに軌道面と縮尺が異なる。
            その旨は figure.notes に数値から生成して入る）。
        days: view="apparition" の表示日数（1〜3650、既定 180）。今日の 30 日前から
            days 日後までを描く（直前に過ぎた近日点・最接近も見えるようにするため）。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く（…）](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は %LOCALAPPDATA%\\Temp\\space_finder_mcp\\out に
    保存し、同じパスを structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    """
    vw = str(view or "system").strip().lower()
    if vw in ("apparition", "comet_apparition", "light_curve", "見え方"):
        from .comet_apparition import comet_apparition_result   # 循環 import を避ける
        first = comet or comet2
        if not first or not str(first).strip():
            known = "、".join(sorted(k for k in _COMET_ALIASES if not k.isascii())[:14])
            msg = ("view='apparition' には彗星の指定が必要です（例: comet='169P'）。"
                   "指定できる彗星の例: " + known)
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": msg,
                                                     "known_comets": sorted(_COMET_ALIASES)})
        names = _split_object_names(comet) + [n for n in _split_object_names(comet2)
                                             if n not in _split_object_names(comet)]
        if len(names) > 1:
            msg = ("view='apparition' は1天体ずつです（{} が指定されました）。"
                   "1つだけ指定して、彗星ごとに呼び出してください（並列呼び出し可）"
                   .format("、".join(names)))
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": msg, "comets": names})
        return comet_apparition_result(names[0] if names else str(first).strip(), when,
                                       days=days)
    if vw in ("comet_orbit", "comet", "orbit"):
        first = comet or comet2
        if not first or not str(first).strip():
            known = "、".join(sorted(k for k in _COMET_ALIASES if not k.isascii())[:14])
            msg = ("view='comet_orbit' には彗星の指定が必要です（例: comet='ハレー彗星'）。"
                   "指定できる彗星の例: " + known)
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": msg,
                                                     "known_comets": sorted(_COMET_ALIASES)})
        names = _split_object_names(comet) + [n for n in _split_object_names(comet2)
                                             if n not in _split_object_names(comet)]
        if not names:
            names = [str(first).strip()]
        if len(names) == 1:
            return _comet_orbit_result(names[0], when)
        return _comet_multi_result(names, when)
    # 複数指定は comet_orbit と同じ解釈（カンマ区切り＋2番目の引数）
    asts = _split_object_names(asteroid) + _split_object_names(asteroid2)
    prbs = _split_object_names(probe) + _split_object_names(probe2)
    coms = _split_object_names(comet) + _split_object_names(comet2)
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
            with RENDER_LOCK:                    # matplotlib はスレッド安全でないため直列化
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
    # インライン画像を描けないハーネス向け: 保存してリンクを先頭に出す
    out_path = save_output(png, "solar_system_now", "png")
    lines = [
        media_link_line("生成した画像を開く（太陽系俯瞰図・{}）".format(eng_label),
                        path=out_path, kind="figure"),
        "☀️ **太陽系俯瞰図（太陽中心・{}）**".format(eng_label),
        "時刻: {}".format(scene["time_utc"]),
        "**惑星位置（太陽からの距離AU）**: " + ", ".join(
            "{} {:.2f}AU".format(n, p["au"]) for n, p in scene["planets"].items()),
    ]
    if scene.get("planet_errors"):
        lines.append("⚠️ 惑星位置の一部を計算できませんでした:")
        for item in scene["planet_errors"]:
            lines.append("- {}: {}".format(item["name"], item["error"]))
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
    fig = figure_payload(
        kind="heliocentric_overview",
        title="太陽系の現在位置（太陽中心・黄道面俯瞰）",
        view=view_spec("ecliptic_plane", "top_down",
                       "黄道面を真上から見た日心俯瞰図（太陽は図の中心）",
                       why="太陽を図の中心に置く俯瞰図のため、楕円軌道の焦点は主天体ではなく、"
                           "各天体の軌道の形そのものは描いていない"),
        primary=primary_spec("太陽", "center"),
        scale=scale_spec("log" if eng == "simple" else "linear", to_scale=(eng != "simple"),
                         exaggerated=["惑星の色アイコン（実寸ではない）",
                                      "小惑星帯の帯（2.0-3.4AUの目安）"]),
        notes=figure_notes(extra=[
            "惑星軌道の円は" + ("対数縮尺の目安で、離心率（水星 e=0.206 など）は無視している"
                               if eng == "simple" else "公転長半径の円で、離心率は無視している"),
            "彗星・探査機のマーカーは黄道面への正射影位置。真の距離は structuredContent の au を参照",
            (f"惑星位置 {len(scene['planet_errors'])} 件の計算に失敗しました"
             "（structuredContent.planet_errors を参照）"
             if scene.get("planet_errors") else ""),
            "彗星の尾は反太陽方向に描いており、進行方向ではない",
        ]),
        caption="太陽を中心とした日心俯瞰図。数値は structuredContent の各値を参照。",
    )
    lines.append("")
    lines.append(figure_text_block(fig))
    lines.append("画像は上に表示（base64 PNG）。出典: JPL DE421+SBDB+Horizons / Skyfield")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), img],
        structuredContent={"time_utc": scene["time_utc"], "engine": eng_label,
                           "planets": scene["planets"],
                           "planet_errors": scene.get("planet_errors", []),
                           "asteroids": scene["asteroids"],
                           "probes": scene["probes"], "comets": scene["comets"],
                           "figure": fig, "image_path": out_path,
                           "source": "JPL DE421+SBDB+Horizons / Skyfield"},
    )

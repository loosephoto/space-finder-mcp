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
from .input_utils import as_float
from .name_common import split_names as _split_object_names
from .img_common import (RENDER_LOCK, apply_matplotlib_cjk_font, body_rgb,
                         conic_from_elements, figure_notes, figure_payload,
                         figure_text_block, load_font, media_link_line, primary_spec,
                         rgb_hex, save_output, scale_spec, symbol_rgb, verify_curve,
                         view_spec)

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)
UA = {"User-Agent": "space-finder-mcp/0.21 (MCP; solar system)"}

# 「1光日」= 光が 1日（86400 秒）で進む距離。天文単位に換算して 173.1446 AU。
#   299792.458 km/s × 86400 s = 25,902,068,371 km ÷ 149,597,870.7 km/AU
# 遠方探査機（ボイジャー等）が星間空間のどこまで来たかを示す目安として図に描く。
LIGHT_DAY_AU = 86400.0 * 299792.458 / 149597870.7
LIGHT_DAY_KM = LIGHT_DAY_AU * 149597870.7

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
    st = _horizons_state(cmd, jd)
    return st["x"], st["y"], st["z"], st["rr"], st["lon"], st["lat"]


def _horizons_state(cmd, jd):
    """位置と速度（太陽中心・黄道 J2000。AU と AU/日）を同じ応答から取り出す。

    速度は「1光日まであと何日か」の見積りに使う。VEC_TABLE=2 の応答は位置と速度を
    同居させているので、位置だけ使う場合と HTTP は1回で済む（追加の呼び出しをしない）。
    """
    jd_min = round(float(jd) * 1440.0) / 1440.0
    return _horizons_state_cached(str(cmd), jd_min)


@ttl_cache(TTL_DAILY, maxsize=256)
def _horizons_state_cached(cmd, jd):
    """分単位に丸めた jd をキーにした Horizons 取得（1分ごとに新キー＝自然に更新）。"""
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api",
                     params={"format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
                             "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS",
                             "CENTER": "'500@10'", "REF_PLANE": "ECLIPTIC",
                             "TLIST": "'{:.6f}'".format(jd), "VEC_TABLE": "'2'"},
                     headers=UA, timeout=(30, 30))
    r.raise_for_status()
    txt = r.text
    i = txt.find("$$SOE"); j = txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に座標ブロックがありません")
    block = txt[i + 5:j]
    AU = 1.495978707e8  # km
    import re
    m = re.search(r"X\s*=\s*([-+0-9.Ee]+)\s+Y\s*=\s*([-+0-9.Ee]+)\s+Z\s*=\s*([-+0-9.Ee]+)",
                  block)
    if not m:
        raise ValueError("状態ベクトルを解析できません")
    x = float(m.group(1)) / AU
    y = float(m.group(2)) / AU
    z = float(m.group(3)) / AU
    rr = math.hypot(x, y, z)
    lon = math.degrees(math.atan2(y, x)) % 360
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    # 速度は既定の単位系（KM-S）で返るので AU/日 に直す。無い応答でも位置は返す
    vx = vy = vz = 0.0
    mv = re.search(r"VX\s*=\s*([-+0-9.Ee]+)\s+VY\s*=\s*([-+0-9.Ee]+)\s+VZ\s*=\s*([-+0-9.Ee]+)",
                   block)
    if mv:
        vx = float(mv.group(1)) * 86400.0 / AU
        vy = float(mv.group(2)) * 86400.0 / AU
        vz = float(mv.group(3)) * 86400.0 / AU
    return {"x": x, "y": y, "z": z, "rr": rr, "lon": lon, "lat": lat,
            "vx": vx, "vy": vy, "vz": vz}


def _light_day_fields(st, jd):
    """「1光日」までの残りと到達予測日を**数値から**作る（図と文を食い違わせない）。

    - 基準は**日心距離（真距離）**。地心距離で見た1光日は地球の公転で最大 ±1 AU 変わり、
      「1光日まで何日」の答えが変わるため、記録する単位を日心距離に固定する。
    - 到達予測日は現在の日心視線速度による**線形外挿**（探査機は徐々に減速するので
      数日の幅を見る）。n体解で厳密に解いた日付が必要なら JPL Horizons で追うこと。
    """
    rr = float(st["rr"])
    v = math.sqrt(st["vx"] ** 2 + st["vy"] ** 2 + st["vz"] ** 2)                 # AU/日
    vr = ((st["x"] * st["vx"] + st["y"] * st["vy"] + st["z"] * st["vz"]) / rr) if rr else 0.0
    AU_KM = 149597870.7
    out = {"light_days": rr / LIGHT_DAY_AU, "light_hours": rr / LIGHT_DAY_AU * 24.0,
           "to_light_day_au": LIGHT_DAY_AU - rr,
           "speed_km_s": v * AU_KM / 86400.0,
           "radial_speed_km_s": vr * AU_KM / 86400.0}
    if rr >= LIGHT_DAY_AU:
        out["light_day_reached"] = True
        return out
    out["light_day_reached"] = False
    if vr > 0:
        eta_days = (LIGHT_DAY_AU - rr) / vr
        out["light_day_eta_days"] = eta_days
        out["light_day_eta_date"] = _jd_date(jd + eta_days)
        out["light_day_eta_method"] = (
            "現在の日心視線速度 {:.2f} km/s による線形外挿（日心距離が1光日に達する日）"
            .format(out["radial_speed_km_s"]))
    return out


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
                     headers=UA, timeout=(25, 25))
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


def norm_orbit_el(cid, el):
    """`_comet_elements` の戻り（SBDB 経路／Horizons 経路）を共通形へ正規化する。

    SBDB 経路は `_raw` に度→ラジアン済みの要素を持ち、Horizons 経路は
    `i_deg`/`node_deg`/`argp_deg`（度）で返す。両者を混ぜると角度が 57 倍ずれるので、
    使う側は必ずこれを通す（見え方チャート・通過経路の描画で共有）。
    """
    if el.get("typ") == "horizons":
        return {"e": float(el["e"]), "q": el.get("q"), "tp": el.get("tp_jd"),
                "i": math.radians(el["i_deg"]), "node": math.radians(el["node_deg"]),
                "argp": math.radians(el["argp_deg"]), "period_days": el.get("period_days"),
                "fullname": el.get("fullname") or cid, "source": el.get("source", "")}
    raw = el.get("_raw") or {}
    out = {"e": float(el["e"]), "q": el.get("q"), "tp": raw.get("tp"),
           "i": raw.get("i"), "node": raw.get("node"), "argp": raw.get("argp"),
           "period_days": raw.get("period_days"), "m1": raw.get("m1"), "k1": raw.get("k1"),
           "fullname": el.get("fullname") or cid, "source": el.get("source", "")}
    if el.get("a") is not None:                # 楕円（小惑星・周期彗星）は半長軸も持つ
        out["a"] = float(el["a"])
    return out


def peri_times(nel, jd_now):
    """近日点通過（前回・次回）の JD を返す。周期が分かる楕円でのみ算出できる。

    e>=1（放物線・双曲線）や、Horizons が周期に 1e99 の番兵を返す場合は
    「次回」が無い＝(tp, None)（見え方チャートと俯瞰図の経路で共有）。
    """
    tp, per = nel.get("tp"), nel.get("period_days")
    if tp is None:
        return None, None
    try:
        per_f = float(per) if per else 0.0
    except (TypeError, ValueError):
        per_f = 0.0
    if float(nel.get("e") or 0.0) >= 1.0 or not (1.0 <= per_f <= 1.0e7):
        return tp, None
    k = math.ceil((jd_now - tp) / per_f)
    return tp + (k - 1) * per_f, tp + k * per_f


def _jd_date(jd):
    """JD を YYYY-MM-DD にする（None・暦の範囲外・番兵は "-"）。"""
    if jd is None:
        return "-"
    try:
        jd = float(jd)
    except (TypeError, ValueError):
        return "-"
    if not (1000000.0 < jd < 4000000.0):
        return "-"
    from datetime import datetime, timedelta, timezone
    return (datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
            + timedelta(days=jd - 2451545.0)).strftime("%Y-%m-%d")


def _xyz_from_rnu(r, nu, i, node, argp):
    """軌道面内の (r, 真近点角 nu) を日心黄道座標へ回す（伝播と経路描画で共有）。"""
    u = argp + nu
    return (r * (math.cos(node) * math.cos(u) - math.sin(node) * math.sin(u) * math.cos(i)),
            r * (math.sin(node) * math.cos(u) + math.cos(node) * math.sin(u) * math.cos(i)),
            r * math.sin(u) * math.sin(i))


def orbit_path_xyz(el, r_cap=None, n=720):
    """軌道の形（日心黄道座標の点列, AU）を返す。俯瞰図に「通過経路」を重ねるため。

    - e<1（楕円）: 離心近点角 E を 0〜2π で回した閉曲線（先頭と末尾は同じ点）
    - e>=1（放物線・双曲線）: 真近点角 ν を ±ν_max まで。ν_max は r ≤ r_cap で決める
      （既定 r_cap = 8q）。閉じない軌道を「閉じた線」として描くと嘘になるので切る。
    """
    e = float(el["e"])
    i, node, argp = el["i"], el["node"], el["argp"]
    pts = []
    if e < 1.0:
        a = el.get("a")
        a = float(a) if a is not None else float(el["q"]) / (1.0 - e)
        for k in range(int(n) + 1):
            ecc = 2.0 * math.pi * k / int(n)
            nu = 2.0 * math.atan2(math.sqrt(1.0 + e) * math.sin(ecc / 2.0),
                                  math.sqrt(1.0 - e) * math.cos(ecc / 2.0))
            pts.append(_xyz_from_rnu(a * (1.0 - e * math.cos(ecc)), nu, i, node, argp))
        return pts
    q = el.get("q")
    if not q:
        raise ValueError("放物線/双曲線の経路には近点距離 q が必要です")
    q = float(q)
    r_cap = float(r_cap) if r_cap else q * 8.0
    cos_nu = max(-1.0, min(1.0, (q * (1.0 + e) / r_cap - 1.0) / max(e, 1e-12)))
    nu_max = math.acos(cos_nu)
    for k in range(int(n) + 1):
        nu = -nu_max + 2.0 * nu_max * k / int(n)
        pts.append(_xyz_from_rnu(q * (1.0 + e) / (1.0 + e * math.cos(nu)), nu, i, node, argp))
    return pts


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
    x, y, z = _xyz_from_rnu(r, nu, el["i"], el["node"], el["argp"])
    lon = math.degrees(math.atan2(y, x)) % 360.0
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    return x, y, z, r, lon, lat


def _compute(when_iso=None, asteroids=None, probes=None, comets=None, route=False,
             range_au=None):
    """太陽を原点とした惑星・小惑星・探査機・彗星の日心黄道座標を計算。

    route=True のときは、指定された彗星の**軌道（通過経路）の点列**も作る
    （俯瞰図に重ねるため。対数縮尺の図では形が歪むので、描く側が注記する）。
    """
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
            st = _horizons_state(cmd, jd)
            rec = {"name": name, "cmd": cmd, "color": col,
                   "au": st["rr"], "proj_au": math.hypot(st["x"], st["y"]),  # 黄道面正射影距離
                   "eclLon": st["lon"], "eclLat": st["lat"]}
            rec.update(_light_day_fields(st, jd))    # 1光日までの残り・到達予測（日心距離）
            prbs[name] = rec
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

    routes = {}
    if route:
        for name in coms:
            if coms[name].get("error"):
                continue
            try:
                routes[name] = _comet_route(name, jd)
            except Exception as ex:
                routes[name] = {"name": name, "error": str(ex)[:120]}

    return {"time_utc": tstr, "planets": planets, "planet_errors": planet_errors,
            "asteroids": asts, "probes": prbs, "comets": coms, "asteroid_belt": True,
            "routes": routes, "route": bool(route),
            # 表示範囲（太陽からの距離の上限 AU）。None なら自動（60 AU 起点＋遠方天体で拡張）
            "range_au": float(range_au) if range_au else None}


def _comet_route(name, jd):
    """彗星の通過経路（日心黄道座標の点列）と、近日点・遠日点の目印を作る（認証不要）。

    近日点は「次に来る日時」、遠日点は近日点＋半周期（楕円のみ）で日付を添える。
    e>=1（放物線・双曲線）は閉じないので遠日点は無く、経路も r_cap で切る。
    """
    cid, el = _comet_elements(name)
    nel = norm_orbit_el(cid, el)
    e = float(nel["e"])
    q = nel.get("q")
    r_cap = max(30.0, 8.0 * float(q)) if q else 30.0
    pts = orbit_path_xyz(nel, r_cap=r_cap)
    rs = [math.dist((0.0, 0.0, 0.0), p) for p in pts]
    i_p, i_a = rs.index(min(rs)), rs.index(max(rs))
    per, tp = nel.get("period_days"), nel.get("tp")
    tp_next = None
    try:
        if tp is not None:
            tp_next = peri_times(nel, jd)[1] if e < 1.0 else tp
    except Exception:
        tp_next = tp
    # 近日点通過時刻を JPL Horizons の n 体解でも求め、2体近似との差を注記に出す。
    # SBDB の要素は古いエポックの接触軌道なので、ハレー彗星のように摂動の大きい彗星では
    # 2体近似が数か月ずれる。取得できないとき（遮断・応答異常）は黙って 2体近似だけを返す。
    tp_nbody, nbody_error = None, None
    if e < 1.0 and tp_next and (el.get("typ") or "sbdb") == "sbdb":
        try:
            tp_nbody = _horizons_perihelion_jd(_horizons_cmd_for(cid), float(tp_next))
        except Exception as ex:
            tp_nbody, nbody_error = None, str(ex)[:120]
    marks = [{"id": "perihelion", "label": "近日点", "r_au": rs[i_p],
              "proj_au": math.hypot(pts[i_p][0], pts[i_p][1]),
              "lon_deg": math.degrees(math.atan2(pts[i_p][1], pts[i_p][0])) % 360.0,
              "jd": tp_next, "date": _jd_date(tp_next)}]
    if e < 1.0:
        apo = rs[i_a]
        marks.append({"id": "aphelion", "label": "遠日点", "r_au": apo,
                      "proj_au": math.hypot(pts[i_a][0], pts[i_a][1]),
                      "lon_deg": math.degrees(math.atan2(pts[i_a][1], pts[i_a][0])) % 360.0,
                      "jd": (tp_next + per / 2.0) if (tp_next and per) else None,
                      "date": _jd_date((tp_next + per / 2.0) if (tp_next and per) else None)})
    if tp_nbody is not None:
        marks[0].update({"jd_nbody": tp_nbody, "date_nbody": _jd_date(tp_nbody),
                         "nbody_diff_days": round(tp_nbody - float(tp_next), 1)})
    return {"name": nel.get("fullname") or cid, "color": _COMET_COLOR, "points": pts,
            "marks": marks, "period_days": per, "e": e, "q_au": q,
            "closed": e < 1.0, "r_cap_au": None if e < 1.0 else r_cap,
            # 近日点通過時刻の n 体解（Horizons）と、取得できなかった理由
            "tp_nbody_jd": tp_nbody, "tp_nbody_date": _jd_date(tp_nbody),
            "nbody_error": nbody_error,
            # 要素の出所（"sbdb"=2体近似 / "horizons"=n 体解の接触軌道）。注記の文言を分ける
            "typ": el.get("typ") or "sbdb",
            # 経路の最大半径（表示範囲に収まるかの判定に使う）と、既存の ±45AU 判定
            "r_max_au": max(max(abs(q[0]), abs(q[1])) for q in pts),
            "out_of_frame": bool(max(max(abs(q[0]), abs(q[1])) for q in pts) > 43.0)}


# ---------- 描画ヘルパー ----------
# ---------- 描画エンジン B: Pillow (簡易・実写合成, 既定) ----------
# 表示範囲の指定に使える天体名・キーワード（AU = 公転長半径・目安）。
# LLM は「火星まで」「木星まで」のように**判断して**範囲を選べる。数値も従来どおり使える。
_RANGE_TARGET_EN = {"水星": "mercury", "金星": "venus", "地球": "earth", "火星": "mars",
                    "木星": "jupiter", "土星": "saturn", "天王星": "uranus",
                    "海王星": "neptune", "冥王星": "pluto"}
_RANGE_TARGETS = {}
for _rn, _, _, _rs in _PLANETS:
    _RANGE_TARGETS[_rn] = (_rs, _rn)
    _RANGE_TARGETS[_RANGE_TARGET_EN[_rn]] = (_rs, _rn)
_RANGE_TARGETS.update({
    "小惑星帯": (2.7, "小惑星帯(2.0–3.4 AU)"), "asteroidbelt": (2.7, "小惑星帯(2.0–3.4 AU)"),
    "内惑星": (1.524, "内惑星(〜火星)"), "inner": (1.524, "内惑星(〜火星)"),
    "innerplanets": (1.524, "内惑星(〜火星)"),
    "外惑星": (30.1, "外惑星(〜海王星)"), "outer": (30.1, "外惑星(〜海王星)"),
    "outerplanets": (30.1, "外惑星(〜海王星)"),
    "太陽系": (45.0, "太陽系(±45 AU)"), "solarsystem": (45.0, "太陽系(±45 AU)"),
    "全体": (45.0, "太陽系全体(±45 AU)"), "太陽系全体": (45.0, "太陽系全体(±45 AU)"),
})


def _resolve_range(raw, scene):
    """表示範囲の指定（数値 / 天体名 / キーワード）を AU に解決する。

    - 数値: そのまま（例: 10 → 10 AU）
    - 天体名: 「その天体の軌道の円が入る」ように長半径 × 1.08（例: 木星 5.20 → 5.62 AU）
      「火星まで」「木星の軌道」のような言い回しは語尾を落として照合する。
    - "fit"（指定天体に合わせる）: その呼び出しで指定した小惑星・彗星・探査機・経路が
      すべて入る範囲（最大値 × 1.10、下限 1.2 AU）。
    戻り値: (AU or 0.0, 由来の説明 or None)。解決できない名前は (None, 説明) を返し、
    呼び出し側が候補を提示して停止する（推測して勝手な縮尺にしない）。
    """
    if raw is None:
        return 0.0, None
    num = as_float(raw, default=None, minimum=0.0, maximum=1.0e6)
    if num:
        return float(num), None
    s = str(raw).strip()
    if not s or s.lower() in ("auto", "none", "0"):
        return 0.0, None
    key = s.lower()
    for suf in ("まで", "以内", "圏", "の軌道", "軌道", "より内側", "より外側"):
        key = key.replace(suf, "")
    key = key.replace(" ", "").replace("　", "").replace("_", "")
    if key in ("fit", "指定天体", "指定した天体", "全部", "すべて", "合わせる", "収まる"):
        cands = []
        for coll, fld in (("asteroids", "sma"), ("comets", "au"), ("probes", "proj_au")):
            for d in (scene.get(coll) or {}).values():
                if not d.get("error"):
                    cands.append(float(d.get(fld) or d.get("au") or 0.0))
        for rt in (scene.get("routes") or {}).values():
            if not rt.get("error"):
                cands.append(float(rt.get("r_max_au") or 0.0))
        cands = [c for c in cands if c > 0]
        if not cands:
            return 0.0, "指定天体が無いため自動（45 AU 起点＋遠方天体で拡張）"
        v = round(max(max(cands) * 1.10, 1.2), 2)
        return v, "指定した天体が収まる範囲（最大 {:.2f} AU × 1.10 = {:.2f} AU）".format(max(cands), v)
    if key in _RANGE_TARGETS:
        _sma, _label = _RANGE_TARGETS[key]
        return round(_sma * 1.08, 2), "{}の軌道（長半径 {:.2f} AU × 1.08 = {:.2f} AU）".format(
            _label, _sma, _sma * 1.08)
    # 同じ呼び出しで指定した天体の名前（例: asteroid="イトカワ" に対して range_au="イトカワ"）
    for coll, fld in (("asteroids", "sma"), ("comets", "au"), ("probes", "proj_au")):
        for nm, d in (scene.get(coll) or {}).items():
            if not d.get("error") and (nm == s or str(d.get("name") or "") == s
                                       or nm.lower() == key):
                base = float(d.get(fld) or d.get("au") or 0.0)
                return round(max(base * 1.15, 0.6), 2), "{}（{:.2f} AU × 1.15 = {:.2f} AU）".format(
                    nm, base, max(base * 1.15, 0.6))
    return None, "解決できない表示範囲の指定: {!r}（使える指定: 数値 AU、{}、fit=指定天体に合わせる）".format(
        s, "／".join(sorted(set(_RANGE_TARGETS))))


def _scale_with_range(scale, scene):
    """figure.scale に表示範囲（range_au 指定時のみ）を数値で残す（図と文を食い違わせない）。"""
    if not scene.get("range_au"):
        return scale
    rp = scene.get("range_report") or {}
    scale["range_au"] = {
        "lo": float(rp.get("lo_au") or 0.0),
        "hi": float(scene["range_au"]),
        "out_of_range": [{"type": o.get("type"), "name": o.get("name"), "au": o.get("au")}
                         for o in (rp.get("out_of_range") or [])],
        "skipped_orbits": list(rp.get("skipped_orbits") or []),
    }
    return scale


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
    # 探査機を描く図では「1光日」リング（真距離 173.1446 AU）も枠に入るように広げる。
    # 広げないとリングが枠外になり、1光日の位置を示せない（実測: ボイジャー1号の投影は
    # 140.5 AU なので 161.6 AU までしか広がらず、173.14 AU のリングが描けなかった）。
    if any(not pr.get("error") for pr in scene["probes"].values()):
        hi = max(hi, LIGHT_DAY_AU * 1.02)
    for co in scene["comets"].values():
        if not co.get("error") and co["proj_au"] > 0:
            hi = max(hi, co["proj_au"] * 1.15)
    for rt in (scene.get("routes") or {}).values():   # 経路が枠外へ出ないよう上限を広げる
        for (rx, ry, _rz) in (rt.get("points") or []):
            pr_ = math.hypot(rx, ry)
            if pr_ > 0:
                hi = max(hi, pr_ * 1.02)
    R0 = 620.0; lo = 0.30
    # 表示範囲の指定（例: 土星より内側だけを見たい）。hi を固定し、狭い範囲では下限も下げる。
    rng_au = float(scene.get("range_au") or 0.0)
    if rng_au:
        hi = rng_au
        lo = min(lo, hi * 0.05)
    rng_report = {"range_au": hi, "lo_au": lo, "skipped_orbits": [], "out_of_range": []}

    def visible(dist):
        """太陽からの距離が表示範囲に入っているか（範囲外は描かず、後で注記に列挙する）。"""
        return (dist is not None) and (float(dist) > 0) and (lo <= float(dist) <= hi)

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

    # 太陽（グロー＋円盤）は**データより先に**描く。後に描くと経路の破線や惑星マーカーの上に
    # 薄いベールがかかり、画素検査で「破線が消えた」と判定される（実測: 代表点9点中2点）。
    # 表示範囲を絞ったときは最内惑星を覆わない大きさへ縮める（グローは半径70px＋ぼかし30。
    # 実測: range_au=10 で水星の純色画素が 1 px まで落ちた）。
    gr, sr = 70, 26
    if rng_au:
        _cands = [scale(float(p_["au"])) for p_ in scene["planets"].values()
                  if not p_.get("error") and 0 < float(p_.get("au") or 0.0) <= hi]
        if _cands:
            gr = int(min(gr, 0.90 * min(_cands)))
            sr = int(min(sr, 0.45 * min(_cands)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([CX - gr, CY - gr, CX + gr, CY + gr], fill=(255, 230, 140, 220))
    glow = glow.filter(ImageFilter.GaussianBlur(max(3, int(gr * 30 / 70))))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse([CX - sr, CY - sr, CX + sr, CY + sr], fill=body_rgb("太陽"),
               outline=(255, 245, 200), width=2)
    scene["sun_disk_px_simple"] = float(sr)

    for jname, _, _, sma in _PLANETS:
        if rng_au and not visible(sma):
            rng_report["skipped_orbits"].append({"name": jname, "sma_au": sma})
            continue
        r = scale(sma)
        dr.ellipse([CX - r, CY - r, CX + r, CY + r], outline=(110, 120, 170), width=1)

    # 「1光日」の目安リング（真距離 173.1446 AU = 25,902,068,371 km）。表示範囲に入って
    # いるときだけ描き、外なら理由を figure.notes に数値付きで出す（黙って消さない）。
    _ld_color = (255, 150, 160)
    ld_draw = {"au": LIGHT_DAY_AU, "km": LIGHT_DAY_KM, "color": list(_ld_color),
               "drawn": False, "why": "", "dash_samples": [], "proj": None,
               "label_box": None, "proj_label_box": None, "mark_px": None}
    _occ_rects = []                                   # 既に描いたラベル箱（後のラベルが避ける）
    _occ_pts = [(float(CX), float(CY), float(sr) * 1.4)]   # 太陽の描画円盤

    def _ring(radius_px, color, width=2, step=4, on=2, phase=0):
        """PIL に破線円が無いので、円弧を分割して1本おきに描く（代表点も残す）。"""
        pts = []
        if radius_px <= 1.0:
            return pts
        for segment in range(0, 360, step):
            if (segment // step) % 2:
                continue
            a_ = segment + phase
            t1, t2 = math.radians(a_), math.radians(a_ + on)
            p1 = (CX + radius_px * math.cos(t1), CY + radius_px * math.sin(t1))
            p2 = (CX + radius_px * math.cos(t2), CY + radius_px * math.sin(t2))
            dr.line([p1[0], p1[1], p2[0], p2[1]], fill=color, width=width)
            pts.append([round((p1[0] + p2[0]) / 2.0), round((p1[1] + p2[1]) / 2.0)])
        return pts

    if visible(LIGHT_DAY_AU):
        ld_draw["drawn"] = True
        ld_draw["r_px"] = scale(LIGHT_DAY_AU)
        ld_draw["dash_samples"] = _ring(ld_draw["r_px"], _ld_color)
    else:
        ld_draw["why"] = "表示範囲 {:.2f}–{:g} AU の外".format(lo, hi)

    # 1光日に達していない探査機があるときは、その探査機の**黄道面投影**での1光日リングも
    # 描く（俯瞰図は正射影なので、真距離のリング上に位置を置くと図と数値が食い違う）。
    projections = []
    for _idx, (_n0, _d0) in enumerate(scene["probes"].items()):
        if _d0.get("error") or _d0.get("light_days") is None or _d0.get("light_day_reached"):
            continue
        _pj_au = LIGHT_DAY_AU * math.cos(math.radians(float(_d0.get("eclLat") or 0.0)))
        if not visible(_pj_au):
            continue
        _r_pj = scale(_pj_au)
        _th0 = math.radians(float(_d0.get("eclLon") or 0.0))
        pj = {"name": _n0, "eclLat": float(_d0.get("eclLat") or 0.0),
              "eclLon": float(_d0.get("eclLon") or 0.0), "au": _pj_au,
              "color": list(_d0.get("color") or (255, 214, 90)),
              "eta": _d0.get("light_day_eta_date"),
              "eta_method": _d0.get("light_day_eta_method"), "r_px": _r_pj,
              "probe_marker_px": [round(CX + scale(float(_d0["proj_au"])) * math.cos(_th0)),
                                  round(CY + scale(float(_d0["proj_au"])) * math.sin(_th0))]}
        pj["dash_samples"] = _ring(_r_pj, tuple(pj["color"]), phase=2 * (_idx + 1))
        projections.append(pj)
        if ld_draw["proj"] is None:
            ld_draw["proj"] = pj
    ld_draw["projections"] = projections
    scene["light_day_draw"] = ld_draw

    rlo, rhi = (None, None) if (rng_au and not (visible(2.0) and visible(3.4))) else (scale(2.0), scale(3.4))
    if rlo is not None:
        for a in range(0, 360, 4):
            rr = random.uniform(rlo, rhi)
            th = math.radians(a + random.uniform(-2, 2))
            g = random.randint(90, 170)
            dr.point((int(CX + rr * math.cos(th)), int(CY + rr * math.sin(th))), fill=(g, g, g + 10))

    for p in scene["planets"].values():
        jname, au, lon, sma = p["name"], p["au"], p["eclLon"], p["sma"]
        if rng_au and not visible(au):
            rng_report["out_of_range"].append({"type": "planet", "name": jname, "au": au})
            continue
    # 惑星は「全マーカー → ラベル → マーカーを描き直す」の順で描く。ラベル箱は 178x30px と
    # 大きく、後から描くと隣の惑星マーカーを隠す（表示範囲を絞ると環の間隔より箱が大きくなる。
    # 実測: range_au=10 で内惑星の画素が 0 になった）。
    _planet_draw = []

    def _draw_planet_marker(px, py, rad, col, jname):
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        hd.ellipse([px - rad - 14, py - rad - 14, px + rad + 14, py + rad + 14], fill=col + (90,))
        halo = halo.filter(ImageFilter.GaussianBlur(8))
        img.paste(Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB"), (0, 0))
        dd = ImageDraw.Draw(img)
        dd.ellipse([px - rad, py - rad, px + rad, py + rad], fill=col,
                   outline=tuple(min(255, c + 70) for c in col), width=2)
        if jname == "土星":
            dd.ellipse([px - rad - 14, py - 6, px + rad + 14, py + 6],
                       outline=symbol_rgb("ring"), width=5)
        elif jname == "木星":
            dd.line([px - rad, py - 8, px + rad, py - 8], fill=symbol_rgb("band"), width=3)
            dd.line([px - rad, py + 5, px + rad, py + 5], fill=symbol_rgb("band"), width=3)

    _labels = []
    for p in scene["planets"].values():
        jname, au, lon, sma = p["name"], p["au"], p["eclLon"], p["sma"]
        if rng_au and not visible(au):
            rng_report["out_of_range"].append({"type": "planet", "name": jname, "au": au})
            continue
        col = dict((j, c) for j, _, c, _ in _PLANETS)[jname]
        ang = math.radians(lon)
        rr = scale(au)
        px, py = CX + rr * math.cos(ang), CY + rr * math.sin(ang)
        rad = max(8, min(20, int(5 + 28 * sma / 45)))
        _draw_planet_marker(px, py, rad, col, jname)
        _planet_draw.append((px, py, rad, col, jname))
        _labels.append((jname, au, px, py, rad, ang))
    # ラベルは太陽から外向きに置く（内向きだと内側の環のマーカーを覆う）
    for jname, au, px, py, rad, ang in _labels:
        ox, oy = int(math.cos(ang) * (rad + 12)), int(math.sin(ang) * (rad + 12))
        lx = px + ox + 8 if (px + ox + 186 < W) else px + ox - 186
        lx = max(10, min(lx, W - 190))
        ly = max(10, min(py + oy - 15, H - 40))
        dr = ImageDraw.Draw(img)
        dr.rectangle([lx, ly, lx + 178, ly + 30], fill=(8, 10, 22))
        _occ_rects.append((lx, ly, lx + 178, ly + 30))
        _occ_pts.append((px, py, rad + 10))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(jname, au), font=load_font(17, True),
                fill=(255, 255, 255))
    # マーカーを描き直して、どのラベル箱よりも上に置く（データを隠さない）
    for px, py, rad, col, jname in _planet_draw:
        _draw_planet_marker(px, py, rad, col, jname)

    for name, d in scene["asteroids"].items():
        if d.get("error"):
            continue
        if rng_au and not visible(d["au"]):
            rng_report["out_of_range"].append({"type": "asteroid", "name": d["name"], "au": d["au"]})
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
        _occ_rects.append((lx, ly, lx + 240, ly + 30))
        _occ_pts.append((px, py, rad + 10))
        dr.text((lx + 4, ly + 2), "{} {:.2f}AU".format(d["name"], d["au"]), font=load_font(17, True),
                fill=(200, 255, 215))

    for name, d in scene["probes"].items():
        if d.get("error"):
            continue
        if rng_au and not visible(d["proj_au"]):
            rng_report["out_of_range"].append({"type": "probe", "name": d["name"], "au": d["proj_au"]})
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
        _ld_extra = ""
        if d.get("light_days") is not None:
            if d.get("light_day_reached"):
                _ld_extra = "1光日 到達済み（{:.4f} 光日）".format(float(d["light_days"]))
            elif d.get("light_day_eta_date"):
                _ld_extra = "1光日(173.14AU)まで {:.2f}AU・到達予測 {}（線形外挿）".format(
                    float(d["to_light_day_au"]), d["light_day_eta_date"])
            else:
                _ld_extra = "1光日(173.14AU)まで {:.2f}AU".format(float(d["to_light_day_au"]))
        _bh = 60 if _ld_extra else 42
        lx, ly = px + rad + 12, py - 16
        if lx + 300 > W:
            lx = px - rad - 310
        lx = max(lx, 4); ly = max(112, min(ly, H - 62 - _bh))
        dr.rectangle([lx, ly, lx + 300, ly + _bh], fill=(40, 20, 0, 235))
        _occ_rects.append((lx, ly, lx + 300, ly + _bh))
        _occ_pts.append((px, py, rad + 12))
        dr.text((lx + 6, ly + 3), "{}  {:.0f}AU（{:.4f} 光日）".format(
            d["name"], d["au"], float(d.get("light_days") or 0.0)), font=load_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=load_font(13), fill=(255, 235, 190, 255))
        if _ld_extra:
            dr.text((lx + 6, ly + 41), _ld_extra, font=load_font(13), fill=(255, 200, 200, 255))

    # 彗星（シアン色の輝く核 + 太陽と反対方向に伸びる尾, 正射影位置に描画）
    for name, d in scene["comets"].items():
        if d.get("error"):
            continue
        if rng_au and not visible(d["proj_au"]):
            rng_report["out_of_range"].append({"type": "comet", "name": d["name"], "au": d["proj_au"]})
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
        _occ_rects.append((lx, ly, lx + 320, ly + 42))
        _occ_pts.append((px, py, rad + 12))
        dr.text((lx + 6, ly + 3), "☄ {}  {:.1f}AU".format(d["name"], d["au"]), font=load_font(16, True),
                fill=(255, 255, 255, 255))
        dr.text((lx + 6, ly + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(d["eclLat"], d["proj_au"]),
                font=load_font(13), fill=(190, 235, 255, 255))

    dr.text((CX - 14, CY - 8), "太陽", font=load_font(18, True), fill=(120, 80, 0))

    # 彗星の通過経路（黄道面への正射影・対数縮尺）。核マーカーより先に敷く（破線）。
    # 対数縮尺では線の長さ・曲率が実際の楕円と一致しないので、注記側で必ず明示する。
    route_draw = {}
    deferred_marks = []          # 目印は最後に描く（太陽の円盤/グローや彗星自身に隠されない）
    for rname, rt in (scene.get("routes") or {}).items():
        if rt.get("error") or not rt.get("points"):
            continue
        rcol = tuple(rt.get("color", _COMET_COLOR))
        segs, prev = [], None
        for (rx, ry, _rz) in rt["points"]:
            pr_ = math.hypot(rx, ry)
            if pr_ < lo * 1.02 or (rng_au and pr_ > hi):
                # 対数縮尺の下限（太陽円盤の内側）と表示範囲の外は線を切る
                prev = None
                continue
            ang_ = math.atan2(ry, rx)
            cur = (CX + scale(pr_) * math.cos(ang_), CY + scale(pr_) * math.sin(ang_))
            if prev is not None:
                segs.append((prev, cur))
            prev = cur
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(halo)
        dash_samples = []
        for k2, (p1, p2) in enumerate(segs):    # PIL に破線は無いので1本おきに描く
            if k2 % 2:
                continue
            hd.line([p1[0], p1[1], p2[0], p2[1]], fill=rcol + (170,), width=4)
            if (k2 // 2) % 40 == 0:             # 破線の実在検査用に代表点を残す
                dash_samples.append([round((p1[0] + p2[0]) / 2.0), round((p1[1] + p2[1]) / 2.0)])
        img.paste(Image.alpha_composite(img.convert("RGBA"),
                                        halo.filter(ImageFilter.GaussianBlur(1))).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(img)
        drawn = []
        # 太陽の描画円盤（既定は半径26px + グロー + 余白）。表示範囲を絞ると円盤も縮むので追従する
        _sr_px = float(scene.get("sun_disk_px_simple") or 26.0)
        disk_px = 34.0 if _sr_px >= 26.0 else max(6.0, _sr_px * 1.3)
        for mk in (rt.get("marks") or []):
            pr_ = float(mk.get("proj_au") or mk.get("r_au") or 0.0)   # 図に置くのは正射影距離
            ang_ = math.radians(float(mk.get("lon_deg") or 0.0))
            mx = CX + scale(pr_) * math.cos(ang_); my = CY + scale(pr_) * math.sin(ang_)
            rec = {"id": mk.get("id"), "label": mk.get("label"), "au": mk.get("r_au", pr_),
                   "proj_au": pr_, "lon_deg": float(mk.get("lon_deg") or 0.0),
                   "px": [round(mx), round(my)], "date": mk.get("date")}
            if rng_au and pr_ > hi:
                rec["drawn"] = False
                rec["reason"] = "out_of_range"
                drawn.append(rec)
                rng_report["out_of_range"].append({"type": "route_mark", "name": rt.get("name") or rname,
                                                   "label": mk.get("label"), "au": pr_})
                continue
            if pr_ < lo * 1.02 or math.hypot(mx - CX, my - CY) < disk_px:
                # 対数縮尺の下限／誇張した太陽の描画円盤の内側。描いても見えないので描かず、
                # 理由を残す（注記側で数値とともに「図からは確認できない」と明示する）。
                rec["drawn"] = False
                rec["reason"] = "sun_disk" if pr_ >= lo * 1.02 else "below_scale"
                drawn.append(rec)
                continue
            rec["drawn"] = True
            rec["deferred"] = True       # 実際の描画は最後（他の要素に隠されない位置で）
            drawn.append(rec)
            deferred_marks.append((mx, my, rcol))
        route_draw[rname] = {"name": rt.get("name") or rname, "color": list(rcol),
                             "segments": len(segs), "marks": drawn,
                             "closed": bool(rt.get("closed")),
                             "sun_disk_px": disk_px, "dash_samples": dash_samples,
                             # 太陽円盤が覆う距離（対数縮尺の逆算）。注記を数値から作るため
                             "sun_disk_au": 10.0 ** (math.log10(lo)
                                                     + (disk_px / R0) * (math.log10(hi) - math.log10(lo))),
                             "skipped_au_below": lo * 1.02 if (rt.get("q_au") or 9) < lo * 1.02 else None}
    scene["route_draw"] = route_draw
    scene["range_report"] = rng_report

    # 通過経路の目印（◇）は最後に描く: 太陽の円盤・光のにじみ・彗星自身のマーカーや尾の
    # 上に来る位置では、先に描くと見えなくなる（実測: ハレー彗星は現在位置＝遠日点付近で
    # 目印が尾に隠れ、最近点側は太陽のグローで色が沈んだ）。
    for (mx, my, rcol_) in deferred_marks:
        dr.polygon([(mx, my - 6), (mx + 6, my), (mx, my + 6), (mx - 6, my)],
                   fill=(10, 14, 24), outline=rcol_, width=2)

    # ---- 1光日のラベル。他のラベルを描いた後に、既存の箱・マーカーから最も遠い角度へ置く
    # （重ねると図と数値が食い違う。太陽・タイトル帯・下部の帯も避ける）。
    def _place_rect(w, h, r_px, prefer=None, avoid=()):
        """リングの外側に置くラベル箱の位置を、占有領域との重なりが最小の角度で選ぶ。"""
        best, best_pen = None, None
        order = ([float(prefer)] if prefer is not None else []) + list(range(0, 360, 6))
        for a_ in order:
            th = math.radians(a_)
            cxx = CX + (r_px + 18 + h / 2.0) * math.cos(th)
            cyy = CY + (r_px + 18 + h / 2.0) * math.sin(th)
            x0 = min(max(6, cxx - w / 2.0), W - w - 6)
            y0 = min(max(112, cyy - h / 2.0), H - 62 - h)
            pen = 0.0
            for (ox0, oy0, ox1, oy1) in list(avoid) + _occ_rects:
                _ox = min(x0 + w, ox1) - max(x0, ox0)
                _oy = min(y0 + h, oy1) - max(y0, oy0)
                if _ox > 0 and _oy > 0:
                    pen += _ox * _oy
            for (mx, my, mr) in _occ_pts:
                if (x0 - mr) <= mx <= (x0 + w + mr) and (y0 - mr) <= my <= (y0 + h + mr):
                    pen += 500.0 * mr
            if best is None or pen < best_pen - 1e-9:
                best, best_pen = (x0, y0, x0 + w, y0 + h), pen
        return best

    if ld_draw.get("drawn"):
        _pref = 180.0
        if ld_draw.get("projections"):
            _pref = (float(ld_draw["projections"][0]["eclLon"]) + 180.0) % 360.0    # 探査機の反対側を優先
        _b = _place_rect(520, 34, ld_draw["r_px"], prefer=_pref)
        dr.rectangle(list(_b), fill=(46, 12, 22, 240))
        dr.text((_b[0] + 8, _b[1] + 6),
                "1光日 {:.2f} AU（{:,.0f} km・太陽光の所要 24 時間）".format(LIGHT_DAY_AU, LIGHT_DAY_KM),
                font=load_font(16, True), fill=(255, 190, 200))
        ld_draw["label_box"] = [int(v) for v in _b]
    _projs = ld_draw.get("projections") or ([ld_draw["proj"]] if ld_draw.get("proj") else [])
    # ラベルがどの探査機の未来方向マーカーも隠さないよう、全座標を配置前に登録する。
    for pj in _projs:
        _th = math.radians(pj["eclLon"])
        _occ_pts.append((CX + pj["r_px"] * math.cos(_th),
                         CY + pj["r_px"] * math.sin(_th), 12.0))
    for pj in _projs:
        _col = tuple(pj["color"])
        _th = math.radians(pj["eclLon"])
        _mx = CX + pj["r_px"] * math.cos(_th); _my = CY + pj["r_px"] * math.sin(_th)
        _l1 = "1光日（{}の黄道面投影 {:.1f} AU）・◇=到達時の方向".format(pj["name"], pj["au"])
        _l2 = "黄経 {:.1f}°／黄緯 {:.1f}°{}".format(
            pj["eclLon"], pj["eclLat"], "・到達予測 {}".format(pj["eta"]) if pj.get("eta") else "")
        _b2 = _place_rect(560, 54, max(pj["r_px"], float(ld_draw.get("r_px") or 0.0)),
                          prefer=(pj["eclLon"] + 120.0) % 360.0,
                          avoid=([tuple(ld_draw["label_box"])] if ld_draw.get("label_box") else ()))
        dr.rectangle(list(_b2), fill=(24, 16, 44, 240))
        dr.polygon([(_mx, _my - 9), (_mx + 9, _my), (_mx, _my + 9), (_mx - 9, _my)],
                   fill=(10, 14, 24), outline=_col, width=2)
        dr.text((_b2[0] + 8, _b2[1] + 5), _l1, font=load_font(15, True), fill=(226, 214, 255))
        dr.text((_b2[0] + 8, _b2[1] + 28), _l2, font=load_font(15, True), fill=(226, 214, 255))
        pj["label_box"] = [int(v) for v in _b2]
        pj["mark_px"] = [round(_mx), round(_my)]
        _occ_rects.append(tuple(_b2))
    if _projs:
        ld_draw["proj_label_box"] = _projs[0].get("label_box")
        ld_draw["mark_px"] = _projs[0].get("mark_px")
    scene["light_day_draw"] = ld_draw

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
    if (scene.get("route_draw") or {}):
        leg += "  ┈┈彗星の通過経路（対数縮尺・正射影）"
    if (scene.get("light_day_draw") or {}).get("drawn"):
        leg += "  ⭘破線円=1光日 {:.2f}AU".format(LIGHT_DAY_AU)
    if rng_au:
        leg += "   表示範囲 {:.2f}–{:g} AU（範囲外の天体は描いていない）".format(lo, hi)
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

    rng_au = float(scene.get("range_au") or 0.0)     # 表示範囲の上限（0=自動）
    rng_report = {"range_au": None, "lo_au": 0.0, "skipped_orbits": [], "out_of_range": []}

    fig, ax = plt.subplots(figsize=(9.5, 9.5))
    ax.set_facecolor("#0b1026")
    fig.patch.set_facecolor("#04060f")
    ax.set_aspect("equal")

    # 惑星軌道（線形）。表示範囲の外は描かない（枠を超えた円は縮尺の意味を壊す）
    for jname, _, _, sma in _PLANETS:
        if rng_au and sma > rng_au:
            rng_report["skipped_orbits"].append({"name": jname, "sma_au": sma})
            continue
        th = np.linspace(0, 2 * np.pi, 360)
        ax.plot(sma * np.cos(th), sma * np.sin(th), color="#3a4466", lw=0.8)

    # 小惑星帯（表示範囲内の部分だけ）
    if not rng_au or rng_au >= 2.0:
        belt_r = np.random.uniform(2.0, min(3.4, rng_au) if rng_au else 3.4, 900)
        belt_th = np.random.uniform(0, 2 * np.pi, 900)
        ax.scatter(belt_r * np.cos(belt_th), belt_r * np.sin(belt_th),
                   s=2, color="#8a9ab8", alpha=0.5, zorder=1)

    # 彗星の通過経路（線形縮尺なので形は本当の軌道。対数縮尺の簡易版と違い歪まない）。
    # このエンジンは彗星マーカーを描かないので、経路を描くときは現在位置も併せて描く。
    route_draw = {}
    for rname, rt in (scene.get("routes") or {}).items():
        if rt.get("error") or not rt.get("points"):
            continue
        rcol = rgb_hex(tuple(rt.get("color", _COMET_COLOR)))
        ax.plot([q[0] for q in rt["points"]], [q[1] for q in rt["points"]],
                ls="--", lw=1.3, color=rcol, alpha=0.9, zorder=3)
        drawn = []
        for mk in (rt.get("marks") or []):
            ang = math.radians(float(mk.get("lon_deg") or 0.0))
            rr = float(mk.get("proj_au") or mk.get("r_au") or 0.0)   # 図に置くのは正射影距離
            if rng_au and rr > rng_au:
                drawn.append({"id": mk.get("id"), "label": mk.get("label"), "au": rr,
                              "lon_deg": float(mk.get("lon_deg") or 0.0),
                              "date": mk.get("date"), "drawn": False, "reason": "out_of_range"})
                rng_report["out_of_range"].append({"type": "route_mark",
                                                   "name": rt.get("name") or rname,
                                                   "label": mk.get("label"), "au": rr})
                continue
            mx, my = rr * math.cos(ang), rr * math.sin(ang)
            ax.scatter([mx], [my], marker="D", s=46, facecolor="none", edgecolor=rcol,
                       lw=1.3, zorder=12)      # ◇は最前面（太陽マーカーの上でも見える）
            ax.annotate("{} {}".format(mk.get("label"), mk.get("date") or "-"), (mx, my),
                        textcoords="offset points", xytext=(9, -13), fontsize=9, color=rcol,
                        # z=5: 惑星マーカー(z=6)より下。上に置くと縮尺を絞ったとき内側の
                        # 惑星マーカーを箱が覆う（実測: 金星の画素 255→13）。
                        zorder=5, bbox=dict(boxstyle="round,pad=0.15", fc="#00000099", ec="none"))
            drawn.append({"id": mk.get("id"), "label": mk.get("label"), "au": mk.get("r_au", rr),
                          "proj_au": rr, "lon_deg": float(mk.get("lon_deg") or 0.0),
                          "date": mk.get("date"), "drawn": True})
        co = (scene.get("comets") or {}).get(rname) or {}
        if co.get("eclLon") is not None and co.get("au") and (not rng_au or co["au"] <= rng_au):
            cxr = co["au"] * math.cos(math.radians(co["eclLon"]))
            cyr = co["au"] * math.sin(math.radians(co["eclLon"]))
            ax.scatter([cxr], [cyr], s=120, color=rcol, edgecolor="white", lw=1.0, zorder=8)
            ax.annotate("{}（現在 {:.2f}AU）".format(co.get("name") or rname, co["au"]),
                        (cxr, cyr), textcoords="offset points", xytext=(9, 7), fontsize=10,
                        color=rcol, fontweight="bold", zorder=9,
                        bbox=dict(boxstyle="round,pad=0.2", fc="#00131ad9", ec=rcol))
        route_draw[rname] = {"name": rt.get("name") or rname, "segments": len(rt["points"]) - 1,
                             "marks": drawn, "closed": bool(rt.get("closed")),
                             "scale": "linear"}
    scene["route_draw"] = route_draw

    for p in scene["planets"].values():
        jname, au, lon, sma = p["name"], p["au"], p["eclLon"], p["sma"]
        if rng_au and au > rng_au:
            rng_report["out_of_range"].append({"type": "planet", "name": jname, "au": au})
            continue
        col = dict((j, c) for j, _, c, _ in _PLANETS)[jname]
        ang = math.radians(lon)
        ax.scatter(au * math.cos(ang), au * math.sin(ang), s=90,
                   color=tuple(c / 255 for c in col), edgecolor="white", lw=0.8, zorder=6)   # ラベル箱(z=5)より上（下に描くと暗く沈む）
        ax.annotate("{}\n{:.2f}AU".format(jname, au), (au * math.cos(ang), au * math.sin(ang)),
                    textcoords="offset points", xytext=(12.0 * math.cos(ang), 12.0 * math.sin(ang)) if rng_au else (8, 6),
                    fontsize=9, color="white",
                    zorder=5, bbox=dict(boxstyle="round,pad=0.15", fc="#00000099", ec="none"))

    for name, d in scene["asteroids"].items():
        if d.get("error"):
            continue
        if rng_au and d["au"] > rng_au:
            rng_report["out_of_range"].append({"type": "asteroid", "name": d["name"], "au": d["au"]})
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
    # 太陽マーカーは最背面（zorder=2）。実測: 半径21px＝約1.7AU相当あり、前に描くと
    # 水星〜火星（zorder=4）と経路の◇が完全に隠れていた。
    _sun_marker_after_scale = True      # マーカー本体は縮尺(px/AU)が決まってから描く（下記）
    ax.annotate("太陽", (0, 0), textcoords="offset points", xytext=(-12, -26),
                fontsize=11, color="#ffe9a3", fontweight="bold", ha="center", zorder=9)

    ax.set_title("太陽系・惑星位置（線形距離の正確な俯瞰図）\n{} ・ 円=公転軌道(AU){}".format(
        scene["time_utc"], " ・ 破線=彗星の通過経路" if (scene.get("route_draw") or {}) else ""),
                 fontsize=12, color="white", pad=15)
    ax.set_xlabel("X (AU)", color="#9aa")
    ax.set_ylabel("Y (AU)", color="#9aa")
    ax.tick_params(colors="#9aa")
    for sp in ax.spines.values():
        sp.set_color("#3a4466")
    if rng_au:
        lim = float(rng_au)                            # 表示範囲の指定があれば固定
        rng_report["range_au"] = lim
        rng_report["lo_au"] = 0.0
    else:
        lim = 45.0
        for rt in (scene.get("routes") or {}).values():   # 経路が枠外へ出ないよう広げる
            for q in (rt.get("points") or []):
                lim = max(lim, abs(q[0]) * 1.05, abs(q[1]) * 1.05)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
    scene["range_report"] = rng_report
    # 太陽マーカー(s=300 pt²)の半径を AU に換算して残す。線形図は 12.3 px/AU 程度しかないので
    # マーカー半径は約1.7 AU＝内惑星や近日点がマーカーの下に隠れる大きさになる（実測）。
    try:
        w_px = ax.get_window_extent().width
        px_au = w_px / (2.0 * lim)
        scene["px_per_au"] = px_au                    # 縮尺の実測値（AU→px）
        scene["sun_marker_au"] = (math.sqrt(300.0 / math.pi) * (fig.dpi / 72.0)) / max(px_au, 1e-9)
    except Exception:
        scene["sun_marker_au"] = None
    # 太陽マーカーの本体をここで描く。既定は s=300（半径 9.8pt）。**表示範囲の指定時は
    # 最内惑星を覆わない大きさへ絞る**（実測: range_au=30 では s=300 で半径 0.6 AU となり
    # 水星 0.46 AU が隠れる。範囲を絞る目的と矛盾するため、数値から大きさを決める）。
    try:
        _px_au = float(scene.get("px_per_au") or 0.0)
        sun_px = math.sqrt(300.0 / math.pi) * (fig.dpi / 72.0)
        if rng_au and _px_au > 0:
            _inner = min([float(p_["au"]) for p_ in scene["planets"].values()
                          if not p_.get("error") and 0 < float(p_.get("au") or 0.0) <= rng_au]
                         or [float(rng_au)])
            sun_px = min(sun_px, 0.45 * _inner * _px_au)
        ax.scatter(0, 0, s=math.pi * (sun_px * 72.0 / fig.dpi) ** 2,
                   color=rgb_hex(body_rgb("太陽")), edgecolor="#fff5c2", lw=1.5, zorder=2)
        if _px_au > 0:
            scene["sun_marker_au"] = sun_px / _px_au
    except Exception:
        ax.scatter(0, 0, s=300, color=rgb_hex(body_rgb("太陽")), edgecolor="#fff5c2",
                   lw=1.5, zorder=2)

    # 目印の描画座標（データ→保存画像の px）を確定させる。検証が画素を測れるようにするため。
    # データ原点と bbox_inches="tight"（既定 pad_inches=0.1）の差を打ち消して保存画像と一致させる。
    try:
        fig.set_dpi(150)                      # 保存と同じ dpi で再描画し、座標系を一致させる
        fig.canvas.draw()
        tb = fig.get_tightbbox(fig.canvas.get_renderer())   # インチ（pad_inches は含まない）
        dpi_s = float(fig.dpi)
        x0, y0 = ax.transData.transform((0.0, 0.0))         # 表示 px（＝保存 px）
        ox = x0 - (tb.x0 - 0.1) * dpi_s
        oy = (tb.y1 + 0.1) * dpi_s - y0
        pu = float(ax.get_window_extent().width / (2.0 * lim))
        scene["origin_px"] = [round(ox, 2), round(oy, 2)]
        scene["px_per_au"] = pu
        scene["sun_marker_px"] = math.sqrt(300.0 / math.pi) * dpi_s / 72.0
        for rname, rd0 in (scene.get("route_draw") or {}).items():
            rt0 = (scene.get("routes") or {}).get(rname) or {}
            for i, mk in enumerate(rt0.get("marks") or []):
                ang = math.radians(float(mk.get("lon_deg") or 0.0))
                rr = float(mk.get("proj_au") or mk.get("r_au") or 0.0)
                rd0["marks"][i]["px"] = [int(round(ox + rr * math.cos(ang) * pu)),
                                         int(round(oy - rr * math.sin(ang) * pu))]
    except Exception:
        pass

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _verify_route_draw(png, scene):
    """俯瞰図に重ねた彗星の通過経路を自己検証する（画素を測り直す）。

    - 経路の線分が 0 なら不合格（線を引けていない）。
    - Pillow 版は目印(◇)の描画座標があるので、その周辺に彗星色の画素があるかを測る。
    - matplotlib 版は描画座標を持たないため、目印の (r, 黄経) が経路の点列に
      載っているか（データ座標で）検算する。どちらも「描いたつもり」を許さない。
    """
    rep = {"ok": True, "method": "overview_route_marks", "routes": [], "missing": [],
           "mark_window_px": 5}
    rd = scene.get("route_draw") or {}
    if not rd:
        return {"ok": False, "method": "overview_route_marks", "routes": [],
                "missing": ["経路の描画記録が無い（描画に到達していない）"]}
    px = None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        px = im.load()
        W, H = im.size
    except Exception:
        px = None
    paths = scene.get("routes") or {}
    for name, d in rd.items():
        col = tuple(d.get("color", _COMET_COLOR))
        row = {"name": d.get("name") or name, "segments": d.get("segments", 0), "marks": []}
        if not d.get("segments"):
            rep["ok"] = False
            rep["missing"].append("{}: 経路の線分が 0（線を引けていない）".format(name))
        hits = []
        if px is not None and d.get("dash_samples"):
            cx0 = cy0 = W / 2.0
            dsk = float(d.get("sun_disk_px") or 0.0)
            for sx, sy in d["dash_samples"]:
                if math.hypot(sx - cx0, sy - cy0) < dsk:
                    continue            # 太陽の描画円盤の内側は線が隠れて当然（検査しない）
                found = 0
                for yy in range(max(0, sy - 4), min(H, sy + 5)):
                    for xx in range(max(0, sx - 4), min(W, sx + 5)):
                        c = px[xx, yy]
                        # 破線は半透明＋ぼかしで背景と混ざる。彗星色に近い、または「青緑寄りに
                        # 持ち上がっている」画素なら描けているとみなす。背景(11,16,38)は
                        # c1-c0=5 で落ちるので、線が無ければヒットしない（実測で較正）。
                        if (abs(c[0] - col[0]) + abs(c[1] - col[1]) + abs(c[2] - col[2]) <= 60
                                or (c[1] - c[0] >= 18 and c[2] - c[0] >= 25 and c[2] >= 60
                                    and c[0] < 190)):
                            found += 1
                hits.append(found)
            row["dash_samples"] = len(hits)
            row["dash_hits"] = sum(1 for h in hits if h > 0)
            if row["dash_hits"] < max(1, int(0.9 * len(hits))):
                rep["ok"] = False
                rep["missing"].append("{}: 破線の代表点 {} 点のうち {} 点しか画素が無い".format(
                    name, len(hits), row["dash_hits"]))
        path = (paths.get(name) or {}).get("points") or []
        for mk in d.get("marks") or []:
            hit = None
            if mk.get("drawn") is False:
                reason = mk.get("reason")
                if reason == "sun_disk" and mk.get("px") and px is not None:
                    cx0 = cy0 = W / 2.0
                    dist0 = math.hypot(mk["px"][0] - cx0, mk["px"][1] - cy0)
                    # 「太陽円盤の内側だから描いていない」という主張自体を測って確かめる
                    row.setdefault("occluded", []).append(
                        {"label": mk.get("label"), "au": mk.get("au"),
                         "px_dist_from_sun": round(dist0, 1),
                         "sun_disk_px": d.get("sun_disk_px"), "verified": bool(
                             dist0 < float(d.get("sun_disk_px") or 0.0))})
                    if not row["occluded"][-1]["verified"]:
                        rep["ok"] = False
                        rep["missing"].append("{} {}: 太陽円盤の内側という説明が位置と合わない".format(
                            name, mk.get("label")))
                elif reason == "below_scale":
                    row.setdefault("occluded", []).append(
                        {"label": mk.get("label"), "au": mk.get("au"),
                         "reason": "対数縮尺の下限より内側"})
                row["marks"].append({"id": mk.get("id"), "label": mk.get("label"),
                                     "au": mk.get("au"), "date": mk.get("date"),
                                     "px": mk.get("px"), "pixels_found": None,
                                     "drawn": False, "reason": reason})
                continue
            if px is not None and mk.get("px"):
                mx, my = mk["px"]
                hit = 0
                for yy in range(max(0, int(my) - 5), min(H, int(my) + 6)):
                    for xx in range(max(0, int(mx) - 5), min(W, int(mx) + 6)):
                        c = px[xx, yy]
                        if abs(c[0] - col[0]) + abs(c[1] - col[1]) + abs(c[2] - col[2]) <= 40:
                            hit += 1
                if hit < 3:
                    rep["ok"] = False
                    rep["missing"].append("{} {}: 目印の位置に彗星色の画素が無い".format(
                        name, mk.get("label")))
            elif path:
                # 描画座標が無いエンジン: 目印の (r, 黄経) が経路の点列にあるかを検算
                ang = math.radians(float(mk.get("lon_deg") or 0.0))
                rp = float(mk.get("proj_au") or mk.get("au") or 0.0)
                tx, ty = rp * math.cos(ang), rp * math.sin(ang)
                hit = min(math.dist((tx, ty), (q[0], q[1])) for q in path)
                if hit > 1e-3:
                    rep["ok"] = False
                    rep["missing"].append("{} {}: 目印が経路の点列に無い（{:.4f} AU 離れ）".format(
                        name, mk.get("label"), hit))
            row["marks"].append({"id": mk.get("id"), "label": mk.get("label"),
                                 "au": mk.get("au"), "proj_au": mk.get("proj_au"),
                                 "date": mk.get("date"),
                                 "px": mk.get("px"), "pixels_found": hit})
        rep["routes"].append(row)
    return rep


def _verify_range_draw(png, scene):
    """表示範囲（range_au）の自己検証: 「範囲外は描いていない」「範囲内は実際に描けている」を画素で測る。

    - 範囲内の惑星は、その天体色の画素が画像中に一定数以上あること（ラベル箱の下に隠れていない）。
      実測: 範囲指定でラベル箱が隣のマーカーを覆っていたとき、地球の画素は 0 だった。
    - 範囲外の惑星は、その天体色の画素が 0 であること。
    - 太陽の描画マーカーの誇張半径が最内惑星より小さいこと（範囲を絞る意味を壊さない）。
    """
    hi = float(scene.get("range_au") or 0.0)
    if not hi:
        return None
    rep = {"ok": True, "method": "range_au_pixels", "range_au": hi,
           "planets_drawn": [], "out_of_range_zero_pixels": [],
           # 描画側は tol 12（マーカーは連続した塊）。範囲外側は**厳密一致(tol 0)**で判定する
           # （塗りは厳密一致するので、ぼかし・ハローの混色が土星色/天王星色に 20px ほど
           #  寄るのを「描いている」と誤判定しないため）
           "color_tol_drawn": 12, "color_tol_out_of_range": 0,
           "thresholds": {"drawn_min_px": 20, "out_of_range_max_px": 20}}
    cols = dict((j, c) for j, _, c, _ in _PLANETS)
    try:
        import numpy as np
        from PIL import Image
        arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    except Exception as e:                      # 画素を測れないときは「検証できていない」を明示
        return {"ok": False, "method": "range_au_pixels", "range_au": hi,
                "error": str(e)[:120]}
    for p_ in (scene.get("planets") or {}).values():
        nm = p_.get("name")
        if p_.get("error") or nm not in cols:
            continue
        tol = rep["color_tol_drawn"] if float(p_.get("au") or 0.0) <= hi             else 0        # 範囲外は厳密一致のみ（マーカーの塗りは厳密一致する。混色は数えない）
        n = int(((abs(arr - np.array(cols[nm])).max(axis=2)) <= tol).sum())
        au = float(p_.get("au") or 0.0)
        row = {"name": nm, "au": round(au, 3), "pixels_found": n}
        if au <= hi:
            row["ok"] = n >= 20                     # 描いたマーカーは実測 79px 以上
            rep["planets_drawn"].append(row)
        else:
            row["ok"] = n < 20                      # 偶発的な近似色（実測 2px 等）は許容
            rep["out_of_range_zero_pixels"].append(row)
    sun_au = scene.get("sun_marker_au")
    inner = min([c["au"] for c in rep["planets_drawn"]] or [hi])
    if sun_au:
        rep["sun_marker_vs_inner_planet"] = {
            "sun_marker_au": round(float(sun_au), 3), "inner_planet_au": round(inner, 3),
            "ok": float(sun_au) < inner * 0.75}
    rep["ok"] = (bool(rep["planets_drawn"])
                 and all(c["ok"] for c in rep["planets_drawn"])
                 and all(c["ok"] for c in rep["out_of_range_zero_pixels"])
                 and all((rep.get("sun_marker_vs_inner_planet") or {}).get("ok", True)
                         for _ in [0]))
    return rep


# ---------- 彗星の軌道面ビュー（figure 注記つき） ----------
def _verify_light_day_draw(png, scene):
    """1光日リング（破線円）と◇（到達時の方向）が実際に画素として在るかを測る自己検証。

    「描いたつもり」を許さないための検査。リングの破線はラベル箱に部分的に隠れ得るので
    過半数の代表点で判定し、探査機のマーカーは◇や箱に覆われていないこと（画素が残って
    いること）を別に見る。1光日を描いていない図では None を返す（検証対象外）。
    """
    ld = scene.get("light_day_draw") or {}
    projections = ld.get("projections") or ([ld["proj"]] if ld.get("proj") else [])
    if not ld.get("drawn") and not projections:
        return None
    rep = {"ok": True, "method": "light_day_ring_pixels", "au": ld.get("au"), "km": ld.get("km"),
           "rings": [], "missing": []}
    px = None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        px = im.load()
        W, H = im.size
    except Exception:
        px = None
    if px is None:
        rep["ok"] = False
        rep["missing"].append("画像を開けず画素を測れない")
        return rep

    def _near(c, target, tol=48):
        return all(abs(int(a) - int(b)) <= tol for a, b in zip(c[:3], target[:3]))

    def _hits(samples, target, skip_lon=None):
        """代表点の周囲 7x7 px に目標色があるかを数える（skip_lon は◇・マーカー周辺を除く）。"""
        hit, checked = 0, 0
        for sx, sy in samples:
            if skip_lon is not None:
                ang = math.degrees(math.atan2(sy - H / 2.0, sx - W / 2.0)) % 360.0
                if abs((ang - float(skip_lon) + 180.0) % 360.0 - 180.0) < 22.0:
                    continue
            checked += 1
            found = False
            for yy in range(max(0, sy - 3), min(H, sy + 4)):
                for xx in range(max(0, sx - 3), min(W, sx + 4)):
                    if _near(px[xx, yy], target):
                        found = True
                        break
                if found:
                    break
            hit += 1 if found else 0
        return hit, checked

    def _count(box, target, tol=60):
        """ラベル箱の中の文字画素を数える（箱だけ描けて文字が無い、を許さない）。"""
        n = 0
        for yy in range(max(0, int(box[1])), min(H, int(box[3]))):
            for xx in range(max(0, int(box[0])), min(W, int(box[2]))):
                if _near(px[xx, yy], target, tol):
                    n += 1
        return n

    if ld.get("drawn"):
        hit, checked = _hits(ld.get("dash_samples") or [], tuple(ld.get("color") or (255, 150, 160)))
        row = {"kind": "true_distance", "au": ld.get("au"), "r_px": round(float(ld.get("r_px") or 0.0), 1),
               "dash_hits": hit, "dash_checked": checked,
               "label_box": ld.get("label_box"), "ok": bool(checked and hit >= 0.6 * checked)}
        if not row["ok"]:
            rep["ok"] = False
            rep["missing"].append("1光日リングの破線が画素で見つからない（{}/{}）".format(hit, checked))
        row["label_text_px"] = _count(ld["label_box"], (255, 190, 200)) if ld.get("label_box") else 0
        if row["label_text_px"] < 600:
            row["ok"] = False
            rep["ok"] = False
            rep["missing"].append("1光日のラベル文字が画素で見つからない（{} px）"
                                  .format(row["label_text_px"]))
        rep["rings"].append(row)
    for pj in projections:
        hit, checked = _hits(pj.get("dash_samples") or [], tuple(pj.get("color") or (255, 214, 90)),
                             skip_lon=pj.get("eclLon"))
        label_box = pj.get("label_box") or ld.get("proj_label_box")
        row = {"kind": "probe_projection", "name": pj.get("name"), "au": pj.get("au"),
               "r_px": round(float(pj.get("r_px") or 0.0), 1), "dash_hits": hit,
               "dash_checked": checked, "mark_px": pj.get("mark_px") or ld.get("mark_px"),
               "label_box": label_box,
               "ok": bool(checked and hit >= 0.5 * checked)}
        if not row["ok"]:
            rep["ok"] = False
            rep["missing"].append("{} の投影1光日リングの破線が画素で見つからない（{}/{}）".format(
                pj.get("name"), hit, checked))
        row["label_text_px"] = _count(label_box, (226, 214, 255)) if label_box else 0
        if row["label_text_px"] < 600:
            row["ok"] = False
            rep["ok"] = False
            rep["missing"].append("{} の投影1光日ラベル文字が画素で見つからない（{} px）".format(
                pj.get("name"), row["label_text_px"]))
        mpx = pj.get("probe_marker_px")
        if mpx:
            found = 0
            for yy in range(max(0, int(mpx[1]) - 18), min(H, int(mpx[1]) + 19)):
                for xx in range(max(0, int(mpx[0]) - 18), min(W, int(mpx[0]) + 19)):
                    if _near(px[xx, yy], tuple(pj.get("color") or (255, 214, 90))):
                        found += 1
            row["probe_marker_px"] = int(found)
            if found < 60:
                row["ok"] = False
                rep["ok"] = False
                rep["missing"].append("{} の探査機マーカー画素が足りない（{} px）".format(
                    pj.get("name"), found))
        rep["rings"].append(row)
    return rep


_COMET_ORBIT_COLOR = symbol_rgb("comet_orbit")   # 軌道線の色（verify_curve がこの色を画素から測る）


def _au_fmt(v):
    """AU 目盛・注記用の簡潔な表記。"""
    if v is None:
        return "-"
    fmt = "{:.3f}" if abs(v) < 1 else "{:.2f}"
    return fmt.format(v).rstrip("0").rstrip(".")


def _mark_txt(m, with_proj=True):
    """経路の◇（近日点・遠日点）の1行表記を作る（本文と figure.notes で共有）。

    JPL Horizons の n 体解で近日点通過時刻が取れたときは並記する（SBDB の 2体近似だけを
    出して実際の回帰との差を隠さない）。黄道面投影距離は 5‰以上差があるときだけ出す
    （with_proj）。
    """
    r_t, r_p = m.get("r_au"), m.get("proj_au")
    extra = ""
    if (with_proj and r_t and r_p
            and abs(float(r_p) - float(r_t)) / max(float(r_t), 1e-9) > 0.005):
        extra = "・黄道面投影 {} AU".format(_au_fmt(r_p))
    dat = m.get("date") or "-"
    if m.get("date_nbody"):
        dat = "{}（SBDB 2体近似）／{}（Horizons n 体解）".format(dat, m["date_nbody"])
    return "{} {} AU{}（{}）".format(m.get("label"), _au_fmt(r_t), extra, dat)


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
                     headers=UA, timeout=(10, 30))
    r.raise_for_status()
    return _parse_hz_elements(r.text)


def _parse_hz_elements(txt):
    """Horizons の ELEMENTS 応答（CSV）から1行目の要素を取り出す。

    `_horizons_elements`（C/彗星の軌道）と `_horizons_perihelion_jd`（n 体解の近日点通過
    時刻）で共有する。列は JDTDB, 日付, EC, QR, IN, OM, W, Tp, N, MA, TA, A, AD, PR。
    """
    i, j = txt.find("$$SOE"), txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に要素ブロックがありません")
    rows = [ln for ln in txt[i + 6:j].strip().splitlines() if ln.strip()]
    if not rows:
        raise ValueError("Horizons の要素が空です")
    c = [v.strip() for v in rows[0].split(",")]
    if len(c) < 14:
        raise ValueError("Horizons 要素の列数が不足しています（{} 列）".format(len(c)))
    return {"e": float(c[2]), "q": float(c[3]), "i_deg": float(c[4]),
            "node_deg": float(c[5]), "argp_deg": float(c[6]), "tp_jd": float(c[7]),
            "a": float(c[11]), "period_days": float(c[13])}


@ttl_cache(TTL_DAILY, maxsize=64)
def _horizons_perihelion_jd(cmd, jd_guess):
    """JPL Horizons の n 体解による近日点通過時刻（JD）を返す（認証不要）。

    周期彗星の SBDB 要素は古いエポックの接触軌道なので、そこから2体近似で出した近日点は
    実際の回帰と大きくずれることがある（実測 1P/Halley: SBDB の2体解 2062-01-08 に対し、
    Horizons の n 体解は 2061-07-28.7 ＝ 約164日の差）。

    Horizons の ELEMENTS が出す Tp は「そのエポックでの接触軌道の近日点通過時刻」なので、
    エポックを直前の Tp に置き直して反復すると真の通過時刻へ収束する（実測: エポック
    2026-09-23 → 2061-08-04、エポック 2062-01-08 → 2061-07-28.7、次の反復で 0.01 日以内に
    安定）。無限ループにしないよう、反復は3回で打ち切る。
    """
    jd = float(jd_guess)
    tp = jd
    for _ in range(3):
        params = {
            "format": "text", "COMMAND": "'{}'".format(cmd), "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "'ELEMENTS'", "CENTER": "'500@10'",
            "START_TIME": "'JD{:.6f}'".format(jd), "STOP_TIME": "'JD{:.6f}'".format(jd + 10.0),
            "STEP_SIZE": "'10d'", "REF_PLANE": "'ECLIPTIC'", "OUT_UNITS": "'AU-D'",
            "CSV_FORMAT": "'YES'",
        }
        r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                         headers=UA, timeout=(10, 60))
        r.raise_for_status()
        tp = float(_parse_hz_elements(r.text)["tp_jd"])
        if not (1000000.0 < tp < 4000000.0):
            raise ValueError("Horizons の近日点通過時刻が暦の範囲外です（JD {}）".format(tp))
        if abs(tp - jd) <= 0.01:                 # 同じ値を返すようになった＝収束
            break
        jd = tp
    return tp


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
        media_link_line("{} の彗星軌道面ビュー".format(
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
    lines = [media_link_line("彗星{}天体の軌道パネル".format(len(panels)),
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
                     view: str = "system", days: int = 180,
                     route: bool = False, range_au: "float | str" = 0) -> CallToolResult:
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
    **「1光日」（光が24時間で進む距離＝173.1446 AU＝25,902,068,371 km）を図に重ねる**:
    真距離の目安として破線の1光日リングを描き、1光日に達していない探査機を指定したときは
    それぞれの探査機について**黄道面投影**での1光日リングと、到達時の方向（◇＝黄経/黄緯）・到達予測日を
    出す。全リングは structuredContent.light_day.projected_rings と figure.verify.light_day.rings に列挙する。
    俯瞰図は正射影なので真距離の円と投影の円は別物で、その旨は figure.notes に
    数値付きで入る（重ねて読むと誤る）。structuredContent.probes[] に light_days（何光日）・
    light_hours（光の所要時間）・to_light_day_au（1光日までの残り）・light_day_eta_date /
    light_day_eta_method（到達予測＝現在の日心視線速度による線形外挿・数日の幅あり）が入る。
    **距離の基準は日心距離（真距離）で統一**している（地心距離での1光日は地球の公転で
    最大 ±1 AU 変わり日付が異なる）。

    画像は content に base64 インライン表示、座標は structuredContent に JSON。
    structuredContent.figure には「この図をどう描いたか」の注記（figure/1）が入る。
    ⚠️ figure.notes は図の誤読を防ぐための注記なので、要約・言い換えせずそのまま引用すること。

    Args:
        when: 時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        asteroid: 小惑星（例 "イトカワ"/"itokawa"/"25143", "ベンヌ", "アポフィス"）。
        asteroid2: 2つ目の小惑星。
        probe: 遠方探査機（例 "ボイジャー1号"/"voyager1"/"パイオニア10号"/"はやぶさ2"/
            "hayabusa2"）。1光日に達していない探査機では、投影での1光日リング・到達時の
            方向（◇）・到達予測日も図と structuredContent に出る。
            はやぶさ2 は JPL Horizons ID -37。
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
        route: view="system" で、指定した彗星の**通過経路（軌道）を俯瞰図に重ねる**
            （破線）。近日点・遠日点には◇と日付を添える。**近日点の日付は SBDB の2体近似に
            加えて JPL Horizons の n 体解も併記する**（2体近似はずれることがある。ハレー
            彗星では約164日）。既定 False。
            線形の "accurate" では形は本当の軌道と一致するが、対数縮尺の "simple" では
            線の長さと曲率が実際の楕円と一致しない（その旨は figure.notes に入る）。
            形そのものを見たいときは view="comet_orbit"。
        range_au: view="system" の表示範囲（太陽からの距離の上限）。既定 0＝自動
            （対数版は 60 AU 起点、線形版は ±45 AU 起点で、遠方の天体・経路に合わせて拡張）。
            **数値（AU）でも天体名でも指定できる**（「火星まで」「木星まで」のように
            LLM が判断して縮尺を選ぶ用途を想定）。
            数値: 10=土星(9.58 AU)より内側／2=火星まで／5.62=木星まで／30=海王星まで。0.5 未満はエラー。
            天体名: "火星"（→1.65 AU）／"木星"（→5.62）／"土星"（→10.35）／"海王星"（→32.5）／
            "冥王星"／英語名（mars, jupiter…）／"小惑星帯"／"内惑星"／"外惑星"。
            その呼び出しで指定した小惑星・彗星・探査機の名前も使える（例: asteroid="イトカワ"
            に対して range_au="イトカワ"）。**天体名は「その天体の軌道の円が入る」ように
            長半径×1.08 で決める**（決め方は figure.notes と structuredContent.range_resolved に
            数値付きで出る）。
            "fit"（＝指定天体に合わせる）: その呼び出しで指定した天体・経路がすべて入る範囲
            （最大値×1.10、下限 1.2 AU）。天体を指定せず "fit" にすると自動（45 AU 起点）。
            解決できない名前は**推測せずエラーで候補一覧を返す**。
            **範囲外の天体・目印・軌道の円は描かず**、figure.notes と structuredContent
            に出典付きで列挙する（黙って消さない）。対数版は下限も範囲に合わせて下がる。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: `…`」という
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
        scene = _compute(when, asts, prbs, coms, route=bool(route))
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
    # 表示範囲の指定を解決する（数値 AU / 天体名「火星まで」「木星」/ "fit"=指定天体に合わせる）
    rng_au, rng_why = _resolve_range(range_au, scene)
    if rng_au is None:                       # 解決できない名前は推測せず候補を提示して停止する
        return CallToolResult(
            content=[TextContent(type="text", text=str(rng_why))],
            structuredContent={"error": rng_why, "range_targets": sorted(set(_RANGE_TARGETS))},
        )
    if rng_au and rng_au < 0.5:
        return CallToolResult(
            content=[TextContent(type="text", text="表示範囲は 0.5 AU 以上で指定してください（例: 2 で火星まで、"
                                 "5.62 で木星まで、10 で土星より内側、または range_au=\"木星\" のように天体名で）。"
                                 "0（既定）は自動です。")],
            structuredContent={"error": "表示範囲は 0.5 AU 以上（0=自動）"},
        )
    scene["range_au"] = rng_au or None
    has_probe = any(not v.get("error") for v in scene["probes"].values())
    has_com = any(not v.get("error") for v in scene["comets"].values())
    eng = (engine or "simple").lower()
    if eng == "auto" or eng not in ("accurate", "simple"):
        eng = "simple"
    # 遠方探査機・彗星は線形(±45AU)では枠外 → 対数縮尺の Pillow 版へ。
    # ただし route=True で経路が枠に収まるときは accurate を維持する（経路を重ねるなら
    # 形が本当の軌道と一致する線形版の方が良い。彗星が枠外なら対数版へ落とす）。
    if (has_probe or has_com) and eng == "accurate":
        rmax = rng_au or 43.0

        def _rt_fits(rt):
            rt = rt or {}
            if rt.get("error"):
                return False
            return (float(rt.get("r_max_au") or 0.0) <= rmax * 0.98) if rng_au                 else (not rt.get("out_of_frame"))

        drawn_coms = [co for co in scene["comets"].values()
                      if not co.get("error") and float(co.get("au") or 0.0) <= rmax]
        fits = (bool(route) and not has_probe and bool(drawn_coms)
                and all(_rt_fits(rt) for rt in (scene.get("routes") or {}).values())
                and all(float(co.get("au") or 0.0) <= rmax for co in drawn_coms))
        if not fits:
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
        media_link_line("太陽系俯瞰図・{}".format(eng_label),
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
                continue
            _lds = ""
            if d.get("light_days") is not None:
                _lds = "・{:.4f} 光日（光の所要 {:.1f} 時間）".format(
                    float(d["light_days"]), float(d.get("light_hours") or 0.0))
                if d.get("light_day_reached"):
                    _lds += "・1光日 到達済み"
                elif d.get("light_day_eta_date"):
                    _lds += "・1光日（173.14AU）まで {:.2f}AU（到達予測 {}／線形外挿）".format(
                        float(d["to_light_day_au"]), d["light_day_eta_date"])
                else:
                    _lds += "・1光日（173.14AU）まで {:.2f}AU".format(float(d["to_light_day_au"]))
            lines.append("- {}: 真距離 {:.1f}AU{}・黄緯 {:.1f}°（黄道面投影 {:.1f}AU）".format(
                n, d["au"], _lds, d["eclLat"], d["proj_au"]))
    if scene["comets"]:
        lines.append("**彗星位置**（周期=SBDB / C/=Horizons 状態ベクトル）:")
        for n, d in scene["comets"].items():
            if d.get("error"):
                lines.append("- {}: 取得エラー（{}）".format(n, d["error"]))
            else:
                lines.append("- {}: 真距離 {:.1f}AU・黄緯 {:.1f}°（黄道面投影 {:.1f}AU）".format(
                    n, d["au"], d["eclLat"], d["proj_au"]))
    if scene.get("routes"):
        lines.append("**彗星の通過経路**（図の破線＝黄道面への正射影）:")
        for n, rt in scene["routes"].items():
            if rt.get("error"):
                lines.append("- {}: 経路を取得できませんでした（{}）".format(n, rt["error"]))
                continue
            mk = "／".join(_mark_txt(m, with_proj=False) for m in (rt.get("marks") or []))
            lines.append("- {}: {}周期 {} 日（{}）".format(
                rt.get("name") or n, (mk + "・") if mk else "",
                int(rt["period_days"]) if rt.get("period_days") else "-",
                "閉じた楕円" if rt.get("closed") else "閉じない（双曲線/放物線のため打ち切り）"))
    # 線形(accurate)は縮尺が固定（実測 約12 px/AU）で、太陽マーカー(s=300)が半径 約1.7 AU 相当に
    # なる。内惑星や近日点はこのマーカーと重なるので、誇張であることを数値付きで明示する。
    exagg = ["惑星の色アイコン（実寸ではない）", "小惑星帯の帯（2.0-3.4AUの目安）"]
    sun_au = scene.get("sun_marker_au") if eng == "accurate" else None
    if sun_au:
        exagg.append("太陽の描画マーカー（半径 {:.1f} AU 相当・実寸ではない。太陽近傍の天体は"
                     "このマーカーと重なって見える）".format(float(sun_au)))

    # 通過経路を重ねたときの注記・検証（数値から生成する。図と文を食い違わせない）。
    route_verify = None
    route_notes = []
    if scene.get("routes"):
        names = "・".join((rt.get("name") or n) for n, rt in scene["routes"].items()
                          if not rt.get("error"))
        if eng == "simple":
            route_notes.append(
                "破線は{}の通過経路（黄道面への正射影）。この図は対数縮尺なので、線の長さと"
                "曲率は実際の楕円と一致しない。形そのものは view=\"comet_orbit\" の図を参照".format(names))
        else:
            route_notes.append(
                "破線は{}の通過経路（黄道面への正射影）。この図は線形距離なので、形は実際の"
                "楕円と一致する".format(names))
        for n, rt in scene["routes"].items():
            if rt.get("error"):
                route_notes.append("{} の経路を取得できませんでした（{}）".format(n, rt["error"]))
                continue
            mks = "／".join(_mark_txt(m) for m in (rt.get("marks") or []))
            if mks:
                route_notes.append("経路の◇は{}".format(mks))
                nb = [m for m in (rt.get("marks") or []) if m.get("date_nbody")]
                if nb:
                    route_notes.append(
                        "◇の日付は JPL SBDB の軌道要素（2体近似）から計算したもの。{} の近日点は"
                        " JPL Horizons の n 体解では {}（2体近似との差 {} 日）で、摂動の大きい"
                        "彗星では2体近似がこのようにずれる（ずれの大きさは彗星ごとに異なる）。"
                        "見え方チャートは距離の推移に Horizons の n 体解（位置）を使うが、"
                        "そこに出る前回・次回の近日点は同じ SBDB の2体近似からの概算".format(
                            rt.get("name") or n, nb[0].get("date_nbody") or "-",
                            _au_fmt(abs(float(nb[0].get("nbody_diff_days") or 0.0)))))
                elif rt.get("nbody_error"):
                    route_notes.append(
                        "◇の日付は JPL SBDB の軌道要素（2体近似）から計算している。JPL Horizons"
                        " の n 体解を取得できなかったため（{}）、実際の回帰とのずれは示せない"
                        "（ずれの大きさは彗星ごとに異なる）".format(rt["nbody_error"]))
                elif (rt.get("typ") or "sbdb") != "horizons":
                    route_notes.append(
                        "◇の日付は JPL SBDB の軌道要素（2体近似）から計算している。惑星の摂動で"
                        "実際の回帰はずれる（ずれの大きさは彗星ごとに異なる）")
                else:
                    route_notes.append(
                        "◇の日付は JPL Horizons の要素（n 体解の接触軌道）から計算している"
                        "（C/彗星は2体近似ではなく Horizons の要素を使っている）")
            if not rt.get("closed"):
                route_notes.append(
                    "{} は閉じない軌道（e={:.4f}）のため、経路は太陽から {:.0f} AU までで"
                    "打ち切って描いている（それ以遠は描いていない）".format(
                        rt.get("name") or n, float(rt.get("e") or 0.0),
                        float(rt.get("r_cap_au") or 0.0)))
        if sun_au:
            near = ["{} {} {} AU（{}）".format(rt.get("name") or n, m.get("label"),
                                             _au_fmt(m.get("r_au")), m.get("date") or "-")
                    for n, rt in scene["routes"].items()
                    for m in (rt.get("marks") or [])
                    if m.get("r_au") and float(m["r_au"]) < float(sun_au)]
            if near:
                route_notes.append(
                    "{} は誇張した太陽マーカー（半径 {:.1f} AU 相当）と重なる位置にあるが、◇は"
                    "マーカーの上に描いている。距離はマーカーの大きさから読み取らず、上の数値を"
                    "使うこと".format("／".join(near), float(sun_au)))
        low, hidden = [], []
        for n, d in (scene.get("route_draw") or {}).items():
            if d.get("skipped_au_below"):
                low.append("{}（{:.2f} AU 未満）".format(d.get("name") or n, d["skipped_au_below"]))
            for mk in (d.get("marks") or []):
                if mk.get("drawn") is False and mk.get("reason") == "sun_disk":
                    hidden.append("{} {} {} AU（{}）".format(
                        d.get("name") or n, mk.get("label"), _au_fmt(mk.get("au")),
                        mk.get("date") or "-"))
        if low:
            route_notes.append(
                "経路のうち太陽円盤に近い内側は描いていない: {}。対数縮尺の下限で、太陽に"
                "近い部分は線を切っている".format("／".join(low)))
        if hidden:
            d0 = (scene.get("route_draw") or {})
            dsk = max([float((v or {}).get("sun_disk_au") or 0.0) for v in d0.values()] or [0.0])
            dpx = max([float((v or {}).get("sun_disk_px") or 0.0) for v in d0.values()] or [0.0])
            route_notes.append(
                "{} は誇張した太陽の描画円盤（半径 {} px ＝ 約 {} AU 相当）の内側に入るため、"
                "この縮尺では図から位置を確認できない（目印は描いていない）。数値は上の注記の"
                "とおり（図から読み取らないこと）".format("／".join(hidden), int(dpx), _au_fmt(dsk)))
        route_verify = _verify_route_draw(png, scene)
    rng_verify = _verify_range_draw(png, scene)      # 表示範囲指定時のみ（None なら対象外）
    if rng_verify:
        if route_verify:
            rng_verify["route_ok"] = route_verify.get("ok")
            rng_verify["ok"] = bool(rng_verify.get("ok")) and bool(route_verify.get("ok"))
        stat_ok = bool((route_verify or {}).get("ok", True))
        verify_out = {"ok": bool(rng_verify.get("ok")) and stat_ok,
                      "range_au": rng_verify, "routes": (route_verify or {}).get("routes"),
                      "mark_window_px": (route_verify or {}).get("mark_window_px"),
                      "missing": (route_verify or {}).get("missing") or []}
        verify_out = {k: v for k, v in verify_out.items() if v is not None}
        verify_out["method"] = "range_au_pixels+overview_route_marks" if route_verify             else "range_au_pixels"
    else:
        verify_out = route_verify
    # 1光日リング／◇（到達時の方向）の画素検証。描いた図では figure.verify に必ず残す
    # （「描いたつもり」を許さない。リングがラベルに隠れていれば ok が偽になる）。
    _ldv = _verify_light_day_draw(png, scene)
    if _ldv:
        if verify_out:
            verify_out["light_day"] = _ldv
            verify_out["ok"] = bool(verify_out.get("ok")) and bool(_ldv.get("ok"))
            verify_out["method"] = "{}+light_day_ring".format(verify_out.get("method") or "pixels")
        else:
            verify_out = {"ok": bool(_ldv.get("ok")), "method": "light_day_ring", "light_day": _ldv}
    why_txt = ("太陽を図の中心に置く俯瞰図のため、楕円軌道の焦点は主天体ではなく、"
               "各天体の軌道の形そのものは描いていない")
    rng_rep = scene.get("range_report") or {}
    rng_notes = []
    if scene.get("range_au"):
        hi_r = float(scene["range_au"]); lo_r = float(rng_rep.get("lo_au") or 0.0)
        if rng_verify:
            _d = rng_verify.get("planets_drawn") or []
            _o = rng_verify.get("out_of_range_zero_pixels") or []
            rng_notes.append(
                "画素検証: 範囲内の惑星 {} 件すべてが画像中に描かれている（{}）／範囲外の惑星 {} 件は"
                "画素 0（描いていない）".format(
                    len(_d), "、".join("{} {}px".format(c["name"], c["pixels_found"]) for c in _d),
                    len(_o)) if rng_verify.get("ok") else
                "画素検証に失敗した項目がある（figure.verify.range_au を参照）")
        rng_notes.append("表示範囲は{}〜{:g} AU に固定（範囲外の天体は描いていない）".format(
            "太陽中心（下限 {:.2f} AU）".format(lo_r) if lo_r else "太陽中心", hi_r))
        if rng_why:                       # 名前や "fit" で指定されたときは決め方を数値付きで出す
            rng_notes.append("表示範囲の決め方: {}".format(rng_why))
        oob = rng_rep.get("out_of_range") or []
        names_oob = []
        for o in oob:
            if o.get("type") == "route_mark":
                continue
            nm = str(o.get("name") or "")
            if nm and nm not in names_oob:
                names_oob.append("{} {} AU".format(nm, _au_fmt(o.get("au"))))
        if names_oob:
            rng_notes.append("表示範囲{:g} AU の外にあるため描いていない: {}".format(hi_r, "、".join(names_oob)))
        rms = [o for o in oob if o.get("type") == "route_mark"]
        if rms:
            rng_notes.append("経路の目印 {} 件（{}）も表示範囲の外にあるため描いていない".format(
                len(rms), "、".join("{} {} AU".format(o.get("label"), _au_fmt(o.get("au"))) for o in rms)))
        sk = rng_rep.get("skipped_orbits") or []
        if sk:
            rng_notes.append("表示範囲の外の惑星軌道の円は描いていない: " + "、".join(
                "{} {} AU".format(s_.get("name"), _au_fmt(s_.get("sma_au"))) for s_ in sk))
        rmax = max([float((rt or {}).get("r_max_au") or 0.0)
                    for rt in (scene.get("routes") or {}).values()] or [0.0])
        if rmax > hi_r:
            rng_notes.append("経路は最大{:g} AU まで伸びるが、表示範囲{:g} AU の外は描いていない".format(rmax, hi_r))
    # 「1光日」の注記（数値から生成する。真距離の円と投影の円は別物であることを明示）
    ldd = scene.get("light_day_draw") or {}
    ld_notes = []
    if ldd.get("drawn"):
        ld_notes.append(
            "1光日リング（破線円）＝太陽から {:.4f} AU＝{:,} km（光が24時間で進む距離＝真距離）。"
            "探査機のマーカーは黄道面への正射影なので、円とマーカーの見た目の重なりで距離を"
            "読まず、structuredContent の au を使うこと".format(float(ldd["au"]), int(round(ldd["km"]))))
    elif ldd:
        ld_notes.append("1光日（{:.4f} AU）は{}にあるため、この図には描いていない".format(
            float(ldd["au"]), ldd.get("why") or "表示範囲の外"))
    _ld_projections = ldd.get("projections") or ([ldd["proj"]] if ldd.get("proj") else [])
    for pj in _ld_projections:
        ld_notes.append(
            "◇ は {} の1光日到達時の方向（黄経 {:.1f}°／黄緯 {:.1f}°・現在の黄経で描いている。"
            "到達までの間に方向はほとんど変わらない）。この方向では正射影の半径が "
            "{:.4f} AU×cos({:.1f}°)＝{:.1f} AU になるため、投影での1光日リングは真距離の円"
            "（{:.2f} AU）と別の円として描いている（重ねて読むと誤る）".format(
                pj["name"], pj["eclLon"], pj["eclLat"], float(ldd["au"]), pj["eclLat"], pj["au"],
                float(ldd["au"])))
        _dr = abs(float(ldd.get("r_px") or 0.0) - float(pj.get("r_px") or 0.0))
        if _dr < 8.0:
            ld_notes.append(
                "{} の黄緯 {:.1f}° では真距離の1光日（{:.2f} AU）と投影の1光日（{:.1f} AU）が"
                "図上でほぼ重なる（円の半径の差 {:.1f} px）。2本の円は見分けられないので、"
                "どちらの距離かは上の数値で区別すること".format(
                    pj["name"], pj["eclLat"], float(ldd["au"]), pj["au"], _dr))
        if pj.get("eta"):
            ld_notes.append(
                "{} の到達予測 {} は {}。探査機は徐々に減速するので数日の幅で見ること。"
                "地心距離（地球から）での1光日は地球の公転で最大 ±1 AU 変わるため日付が異なる"
                "（この図の数値は日心距離で統一している）".format(
                    pj["name"], pj["eta"], pj.get("eta_method") or "線形外挿"))
        else:
            ld_notes.append(
                "{} の1光日到達日は視線速度から算出できないため、到達予測を表示していない".format(
                    pj["name"]))
    for _n1, _d1 in (scene.get("probes") or {}).items():
        if not _d1.get("error") and _d1.get("light_day_reached"):
            ld_notes.append("{} は1光日（{:.4f} AU）に到達済み（現在 {:.4f} 光日）".format(
                _n1, float(ldd.get("au") or LIGHT_DAY_AU), float(_d1.get("light_days") or 0.0)))
    if eng != "simple":
        ld_notes.append("線形縮尺（accurate）の図には1光日リングを描いていない"
                        "（1光日は対数縮尺の simple 版でのみ描く）")
    if scene.get("routes"):
        why_txt = ("太陽を図の中心に置く俯瞰図のため、楕円軌道の焦点は主天体ではない"
                   "（惑星の軌道の形は描かず、指定された彗星の通過経路だけを破線で重ねている）")
    fig = figure_payload(
        kind="heliocentric_overview",
        title="太陽系の現在位置（太陽中心・黄道面俯瞰）",
        view=view_spec("ecliptic_plane", "top_down",
                       "黄道面を真上から見た日心俯瞰図（太陽は図の中心）",
                       why=why_txt),
        primary=primary_spec("太陽", "center"),
        scale=_scale_with_range(scale_spec("log" if eng == "simple" else "linear",
                                           to_scale=(eng != "simple"), exaggerated=exagg,
                                           px_per_unit=(scene.get("px_per_au")
                                                        if eng == "accurate" else None),
                                           unit="AU"),
                                scene),
        notes=figure_notes(extra=[
            "惑星軌道の円は" + ("対数縮尺の目安で、離心率（水星 e=0.206 など）は無視している"
                               if eng == "simple" else "公転長半径の円で、離心率は無視している"),
            "彗星・探査機のマーカーは黄道面への正射影位置。真の距離は structuredContent の au を参照",
            (f"惑星位置 {len(scene['planet_errors'])} 件の計算に失敗しました"
             "（structuredContent.planet_errors を参照）"
             if scene.get("planet_errors") else ""),
            "彗星の尾は反太陽方向に描いており、進行方向ではない",
        ] + rng_notes + route_notes + ld_notes),
        caption="太陽を中心とした日心俯瞰図。数値は structuredContent の各値を参照。"
                + ("破線＝彗星の通過経路。" if scene.get("routes") else ""),
        verify=verify_out,
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
                           "light_day": {"au": LIGHT_DAY_AU, "km": LIGHT_DAY_KM,
                                         "ring_drawn": bool(ldd.get("drawn")),
                                         "ring_why": ldd.get("why") or None,
                                         "ring_au_for_probe": (ldd.get("proj") or {}).get("au"),
                                         "ring_probe": (ldd.get("proj") or {}).get("name"),
                                         "projected_rings": [{"name": pj.get("name"), "au": pj.get("au"),
                                                                 "eclLon": pj.get("eclLon"), "eclLat": pj.get("eclLat"),
                                                                 "eta": pj.get("eta")} for pj in _ld_projections],
                                         "note": "1光日＝光が24時間で進む距離。距離は日心距離（真距離）"
                                                 "で統一。探査機ごとの light_days / light_day_eta_date は"
                                                 " structuredContent.probes を参照"},
                           "figure": fig, "image_path": out_path,
                           "range_au": (scene.get("range_au") or None),
                           "range_resolved": rng_why,
                           "out_of_range": (rng_rep.get("out_of_range") or []),
                           "comet_routes": {n: {k: v for k, v in (rt or {}).items()
                                                if k != "points"}
                                            for n, rt in (scene.get("routes") or {}).items()},
                           "source": "JPL DE421+SBDB+Horizons / Skyfield"},
    )

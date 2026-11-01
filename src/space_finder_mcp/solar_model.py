"""太陽系のデータモデルと軌道計算（描画を持たない。通信は SBDB 取得の 1 関数だけ）。

`solar_system.py` から切り出した。ここに置くのは:
- 惑星テーブル・光日の定数・彗星の色（複数の層が読む定数）
- JPL SBDB の軌道要素取得（`_sbdb_elements` のみ HTTP を出す。ディスク/メモリのキャッシュ付き）
- ケプラー2体問題の伝播と円錐曲線（楕円／放物線／双曲線）の点列

描画（`solar_render`）とは **JSON 化できる dict だけ**で受け渡す。PIL も Skyfield も
直接は使わない（色は `img_common` の表を読むだけ。PIL は同モジュール内で遅延 import）。
"""
from __future__ import annotations

import math

import requests

from .cache import TTL_DAILY, ttl_cache
from .img_common import body_rgb, symbol_rgb

# JPL SBDB への問い合わせに使う UA（データ源ごとに名乗りを分ける）
UA = {"User-Agent": "space-finder-mcp/0.21 (MCP; solar system)"}


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


# 「1光日」= 光が 1日（86400 秒）で進む距離。天文単位に換算して 173.1446 AU。
#   299792.458 km/s × 86400 s = 25,902,068,371 km ÷ 149,597,870.7 km/AU
# 遠方探査機（ボイジャー等）が星間空間のどこまで来たかを示す目安として図に描く。
LIGHT_DAY_AU = 86400.0 * 299792.458 / 149597870.7


LIGHT_DAY_KM = LIGHT_DAY_AU * 149597870.7


# 彗星の描画色 (シアン系・彗星らしい) とマーカー指定（記号色は img_common が単一の出典）
_COMET_COLOR = symbol_rgb("comet")


# SBDB の必須軌道要素名
_REQUIRED_ELEMS = ("e", "a", "i", "om", "w", "ma", "n")


# ガウス重力定数（AU・日・太陽質量）。平均運動 n = k / a^1.5 [rad/day]
_K_GAUSS = 0.01720209895


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


def _ecliptic_to_perifocal(x, y, z, i_deg, node_deg, argp_deg):
    """日心黄道座標(AU) -> 軌道面内座標（近日点方向=+x, AU）。"""
    i, node, argp = math.radians(i_deg), math.radians(node_deg), math.radians(argp_deg)
    cn, sn = math.cos(node), math.sin(node)
    x1, y1 = cn * x + sn * y, -sn * x + cn * y
    ci, si = math.cos(i), math.sin(i)
    y2 = ci * y1 + si * z
    ca, sa = math.cos(argp), math.sin(argp)
    return ca * x1 + sa * y2, -sa * x1 + ca * y2

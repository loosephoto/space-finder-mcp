"""太陽系俯瞰図（太陽を中心とした惑星・小惑星の現在位置合成, 認証不要）。

指定時刻の太陽を中心とした俯瞰図（黄道面を上から見た図）を画像化する。
- 惑星 (8惑星＋冥王星) は JPL DE421 暦表 + Skyfield で日心黄道座標を計算（ローカル/認証不要）。
- 任意の小惑星 (例: イトカワ25143, ベンヌ101955, アポフィス99942, リュウグウ162173) は
  JPL SBDB API の軌道要素を取得し、ケプラー2体問題で日心位置を伝播。

距離が 0.4〜40 AU と2桁超の差があるため、描画は対数縮尺を既定とし、全天体を視認できる
ようにする。惑星軌道円・小惑星帯(2.0-3.4AU)の目安も表示。

描画は `_render_oblique` の 1 本（黄道面を斜め上から見た俯瞰・弱い透視）。旧
engine="simple"（真上・Pillow）/ engine="accurate"（matplotlib・線形距離）/
view="comet_orbit" の描画関数は、見た目の要素をこの 1 本へ統合して廃止した。
`engine` / `projection` は互換のため残し、engine は動径の縮尺（対数/線形）だけを選ぶ。

返却: content に base64 画像(ImageContent)、structuredContent に座標JSON。
出典: JPL DE421 + Skyfield / JPL SBDB 軌道要素。
"""
from __future__ import annotations

import io
import math
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import TTL_DAILY, ttl_cache
from .input_utils import as_float
from .name_common import split_names as _split_object_names
from .img_common import (figure_notes, figure_payload, figure_text_block, media_link_line,
                         primary_spec, save_output, scale_spec, view_spec)

# データモデル（solar_model）と描画（solar_render）は層ごとに分割。
# ここでの import は既存の呼び出し元（comet_apparition・テスト）への再輸出も兼ねる。
from .solar_model import (LIGHT_DAY_AU, LIGHT_DAY_KM, _PLANETS, _COMET_COLOR, _jd_date,
                          _kepler_position, _sbdb_elements, comet_xyz_from_elements,
                          norm_orbit_el, orbit_path_xyz, peri_times)
from .solar_render import (_au_fmt, _render_oblique, _split_camera_arg,
                           _verify_light_day_draw, _verify_oblique, _verify_range_draw,
                           _verify_route_draw, oblique_camera)

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



# 遠方探査機 (Horizons 天体ID, 表示色RGB)
# Horizons の COMMAND は負のIDで宇宙機を示す（-31=ボイジャー1, -32=ボイジャー2,
# -23=パイオニア10, -24=パイオニア11, -98=ニュー・ホライズンズ, -37=はやぶさ2）。
# ※ -21 は SOHO なので使わない（実測で確認済み）。
# 距離が数十〜百数十 AU に達し、黄緯も大きい（ボイジャー1は黄緯~35°）ため、
# 黄道面俯瞰図には「正射影距離」で描き、ラベルに真距離・黄緯を併記する。
_PROBE_ALIASES = {
    "ボイジャー1号": ("-31", (255, 214, 90)), "ボイジャー1": ("-31", (255, 214, 90)),
    "voyager1": ("-31", (255, 214, 90)), "voyager 1": ("-31", (255, 214, 90)),
    "ボイジャー2号": ("-32", (120, 200, 255)), "ボイジャー2": ("-32", (120, 200, 255)),
    "voyager2": ("-32", (120, 200, 255)), "voyager 2": ("-32", (120, 200, 255)),
    "パイオニア10号": ("-23", (255, 150, 120)), "パイオニア10": ("-23", (255, 150, 120)),
    "pioneer10": ("-23", (255, 150, 120)), "pioneer 10": ("-23", (255, 150, 120)),
    "パイオニア11号": ("-24", (220, 160, 220)), "パイオニア11": ("-24", (220, 160, 220)),
        "pioneer11": ("-24", (220, 160, 220)), "pioneer 11": ("-24", (220, 160, 220)),
    "ニューホライズンズ": ("-98", (140, 200, 140)), "ニュー・ホライズンズ": ("-98", (140, 200, 140)),
    "new horizons": ("-98", (140, 200, 140)), "pluto probe": ("-98", (140, 200, 140)),
    # アポロの S-IVB 段（自由帰還軌道を実際に飛んだ機体。Horizons に実暦がある）。
    # 地球−月系の内側で完結するため太陽系図には描けないが、経路の有無を数値で返す。
    "アポロ11号 S-IVB": ("-399110", (255, 146, 120)), "アポロ11号S-IVB": ("-399110", (255, 146, 120)),
    "apollo11s4b": ("-399110", (255, 146, 120)), "アポロ8号 S-IVB": ("-399080", (150, 210, 255)),
    "アポロ8号S-IVB": ("-399080", (150, 210, 255)), "apollo8s4b": ("-399080", (150, 210, 255)),
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
    # 双曲線・放物線（e>=1）は `_kepler_position`（a/ma/n 前提の楕円専用）では
    # sqrt(1-e) が domain error になる。SBDB 要素の e を見て円錐曲線の一般解へ振る
    # （実測 2026-10-31: 2I/ボリソフ が「math domain error」で落ちていた）。
    try:
        if float(el.get("e") or 0.0) >= 1.0 and el.get("q") and (
                el.get("tp") is not None or el.get("tp_jd") is not None):
            return comet_xyz_from_elements(el, jd)
    except (TypeError, ValueError):
        pass
    return _kepler_position(el, jd)




# ---------- 共通データ層 ----------
def _load():
    """(loader シム, ephemeris) を返す（`ephemeris` の共有ローダ。1 度だけ開く）。"""
    from .ephemeris import timescale_shim
    return timescale_shim()


def _resolve_when(when_iso, ts):
    """ISO8601 を UTC として解釈し Skyfield 時刻へ。省略・空白のみは現在。不正なら None。"""
    txt = "" if when_iso is None else str(when_iso).strip()
    if txt:
        from .ground_view import _parse_when
        parsed = _parse_when(txt)
        if parsed == "bad":
            return None
        return ts.utc(*parsed)
    return ts.now()


def _compute(when_iso=None, asteroids=None, probes=None, comets=None, route=False,
             range_au=None):
    """太陽を原点とした惑星・小惑星・探査機・彗星の日心黄道座標を計算。

    route=True のときは、指定された彗星の**軌道（通過経路）の点列**と、指定された
    探査機の**スイングバイ航路（Horizons の実状態ベクトル点列＋実測の最接近点）**も作る
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
        # 探査機のスイングバイ航路（Horizons の実状態ベクトル点列＋距離の極小で実測した最接近点）。
        # 彗星と同じ scene["routes"] 形式なので、両エンジンの描画・自己検証がそのまま使える。
        if prbs:
            try:
                from .swingby import probe_route as _probe_route
                _planets = [(jn, key) for jn, key, _c, _s in _PLANETS]
                from .swingby import EARTH_MOON_ONLY as _EM_ONLY, SWINGBY_SPANS as _SPANS
                for name, pr in prbs.items():
                    # 現在位置の取得に失敗しても、スイングバイ航路の表に載っている機体なら
                    # 経路だけは描ける（実測: アポロの S-IVB は暦が1969年で終わるため
                    # 現在時刻の位置取得が失敗するが、当時の経路は Horizons に残っている）。
                    if pr.get("error") and str(pr.get("cmd")) not in _SPANS:
                        continue
                    try:
                        # 地球−月系で完結する機体（アポロの S-IVB 等）は月も最接近の対象にする。
                        # それ以外は惑星だけを見る（月は地球の近くに居るため、地球圏を離れた
                        # 探査機では「打ち上げ直後に月へ最接近」という誤検出になりやすい）。
                        _pl = _planets
                        if str(pr.get("cmd")) in _SPANS and str(pr.get("cmd")) in _EM_ONLY:
                            _pl = _planets + [("月", "moon")]
                        routes[name] = _probe_route(pr["cmd"], name, tuple(pr["color"]),
                                                    _pl, eph, sun, ts, when_jd=jd)
                    except Exception as ex:
                        routes[name] = {"name": name, "error": str(ex)[:160]}
            except Exception as ex:      # swingby モジュール自体の読み込み失敗
                for name in prbs:
                    routes.setdefault(name, {"name": name, "error": str(ex)[:160]})

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
            # 経路の最大半径（表示範囲に収まるかの判定に使う）と、既存の ±45AU 判定。
            # 軌道は傾いているので max|x,y| は半径の**下界**になる（実測 2026-10-20:
            # ハレー彗星で 27.69 AU に対し実際の描画半径 33.84 AU）。正射影半径の
            # 最大（hypot）で取る（表示範囲の判定と実際の描画を一致させる）。
            "r_max_au": max(math.hypot(q[0], q[1]) for q in pts),
            "out_of_frame": bool(max(math.hypot(q[0], q[1]) for q in pts) > 43.0)}


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




def _asteroid_orbit_result(name, when_iso=None):
    """JPL SBDB の軌道要素から小惑星の閉じた楕円軌道図を返す。"""
    import base64

    try:
        loader, _eph = _load()
        ts = loader.timescale()
        t = _resolve_when(when_iso, ts)
    except Exception as e:
        msg = "小惑星の軌道図を準備できませんでした（{}）".format(str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": str(name)})
    if t is None:
        msg = "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg})
    jd = t.tt
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")
    query = str(name).strip()
    sstr = _ASTEROID_ALIASES.get(query.lower()) or _ASTEROID_ALIASES.get(query) or query
    try:
        raw = _sbdb_elements(sstr)
        a, e = float(raw["a"]), float(raw["e"])
        if not math.isfinite(a) or not math.isfinite(e) or a <= 0.0 or not (0.0 <= e < 1.0):
            raise ValueError("閉じた小惑星楕円には a>0、0≤e<1 の要素が必要です")
        el = {
            "typ": "sbdb", "fullname": raw.get("fullname") or sstr,
            "e": e, "a": a, "_raw": raw, "q": a * (1.0 - e),
            "i_deg": math.degrees(raw["i"]),
            "node_deg": math.degrees(raw["node"]),
            "argp_deg": math.degrees(raw["argp"]),
            "source": "JPL SBDB（小惑星の楕円軌道要素 + ケプラー伝播）",
        }
        x, y, z, rr, lon, lat = _kepler_position(raw, jd)
    except Exception as e:
        msg = "小惑星の楕円軌道要素・位置を取得できませんでした（{}）: {}".format(
            query, str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": query,
                                                 "id": sstr, "source": "JPL SBDB"})
    try:
        img, fig = _render_oblique(body={"cid": sstr, "el": el, "pos_xyz": (x, y, z),
                                        "when": tstr, "kind": "asteroid"})
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception as e:
        msg = "小惑星軌道図の生成に失敗しました: {}".format(str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": query})
    fullname = el["fullname"]
    imgc = ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                        mimeType="image/png",
                        altText="{} の楕円軌道（軌道面を真横から見た図）".format(fullname))
    out_path = save_output(png, "solar_system_asteroid_orbit", "png")
    lines = [
        media_link_line("{} の小惑星軌道面ビュー".format(fullname),
                        path=out_path, kind="figure"),
        "🪨 **{} の軌道（小惑星自身の軌道面を真横から見た図）**".format(fullname),
        "時刻: {}".format(tstr),
        "離心率 e={:.6f} ・ 近日点 {:.6f} AU ・ 遠日点 {:.6f} AU（閉じた楕円）".format(
            e, el["q"], a * (1.0 + e)),
        "指定時刻の日心距離: {:.3f} AU ・ 黄経 {:.1f}° ・ 黄緯 {:.1f}°".format(rr, lon, lat),
        "", figure_text_block(fig),
        "出典: {} ／ 描画: Pillow".format(el["source"]),
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "time_utc": tstr, "asteroid": fullname, "query": query, "id": sstr,
            "e": e, "a_au": a, "q_au": el["q"], "apo_au": a * (1.0 + e),
            "incl_deg": el["i_deg"], "element_epoch_jd": raw.get("epoch"),
            "au": rr, "eclLon": lon, "eclLat": lat,
            "figure": fig, "image_path": out_path, "source": el["source"],
        },
    )


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
        elif el.get("e") is not None and float(el["e"]) >= 1.0:
            # 双曲線・放物線（e>=1）は楕円のケプラー式では解けない（sqrt(1-e) が
            # domain error）。SBDB 要素（q/tp・ラジアンの i/node/argp）で円錐曲線を解く。
            # 実測 2026-10-31: comet="2I"（ボリソフ）が math domain error で落ちていた。
            x, y, z, rr, lon, lat = comet_xyz_from_elements(el.get("_raw") or el, jd)
        else:
            x, y, z, rr, lon, lat = _kepler_position(el.get("_raw") or el, jd)
    except (requests.RequestException, ValueError, KeyError) as e:
        msg = "彗星の位置を取得できませんでした（{}）: {}".format(name, str(e)[:150])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": str(name)})
    try:
        img, fig = _render_oblique(body={"cid": cid, "el": el, "pos_xyz": (x, y, z),
                                        "when": tstr, "kind": "comet"})
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
            "遠日点 {} AU（閉じた楕円）".format(_au_fmt(fig["conic"].get("apo")))
            if fig["conic"]["closed"] else "遠日点なし（閉じない軌道）"),
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
            img, fig = _render_oblique(body={"cid": cid, "el": el, "pos_xyz": (x, y, z),
                                            "when": tstr, "kind": "comet"})
        except (requests.RequestException, ValueError, KeyError) as e:
            failed.append({"comet": nm, "error": str(e)[:150]}); continue
        except Exception as e:                       # 描画系の想定外もこの彗星だけ落とす
            failed.append({"comet": nm, "error": "描画に失敗: " + str(e)[:120]}); continue
        panel_no = len(imgs) + 1
        y_off = len(imgs) * (img.size[1] + 10)
        imgs.append(img)
        panels.append({
            "markers": [dict(m, panel=panel_no, px_in_panel=m.get("px"),
                             px=([m["px"][0], m["px"][1] + y_off]
                                 if isinstance(m.get("px"), list) and len(m["px"]) == 2
                                 else m.get("px")))
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
    from PIL import Image
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
                     comet2: Optional[str] = None, engine: str = "oblique",
                     view: str = "system", days: int = 180,
                     projection: str = "oblique",
                     route: bool = False, range_au: "float | str" = 0,
                     elevation=None, azimuth=None, dolly=None) -> CallToolResult:
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

    **描画は 1 本だけ**（黄道面を斜め上・仰角34°・方位28°・弱い透視で見た俯瞰）。
    旧 engine="simple"（真上・Pillow）と engine="accurate"（matplotlib・線形距離）、
    旧 projection="top"（真上図）は見た目の要素をこの 1 本へ統合して廃止した。
    engine / projection は互換のため残しており、engine="accurate" は**線形縮尺**を選び
    （相対距離が正確）、projection="top" を指定しても斜め俯瞰で描いてその旨を注記に出す。
    遠方天体は線形縮尺では枠外のため、指定時は自動で対数縮尺へ落とす。

    **「1光日」（光が24時間で進む距離＝173.1446 AU＝25,902,068,371 km）を図に重ねる**:
    真距離の目安として破線の1光日リングを描き、1光日に達していない探査機を指定したときは
    それぞれの探査機について**到達時の方向（◇＝現在の向きで 1 光日に達する点）**と到達予測日を
    出す（マーカーは 3D の実位置なので、リングは黄道面内の真距離の円、◇ は探査機の方向へ
    伸ばした点）。structuredContent.light_day.projected_rings と
    figure.verify.light_day.rings に列挙する。structuredContent.probes[] に light_days（何光日）・
    light_hours（光の所要時間）・to_light_day_au（1光日までの残り）・light_day_eta_date /
    light_day_eta_method（到達予測＝現在の日心視線速度による線形外挿・数日の幅あり）が入る。
    **距離の基準は日心距離（真距離）で統一**している（地心距離での1光日は地球の公転で
    最大 ±1 AU 変わり日付が異なる）。

    画像は content に base64 インライン表示、座標は structuredContent に JSON。
    structuredContent.figure には「この図をどう描いたか」の注記（figure/1）が入る。
    ⚠️ figure.notes は図の誤読を防ぐための注記なので、要約・言い換えせずそのまま引用すること。

    Args:
        when: 時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        asteroid: 小惑星（例 "イトカワ"/"itokawa"/"25143", "ベンヌ", "アポフィス"）。view="asteroid_orbit" では楕円軌道全体を描く。
        asteroid2: 2つ目の小惑星。
        probe: 遠方探査機（例 "ボイジャー1号"/"voyager1"/"パイオニア10号"/"はやぶさ2"/
            "hayabusa2"）。1光日に達していない探査機では、到達時の方向（◇）・到達予測日も
            図と structuredContent に出る。
            はやぶさ2 は JPL Horizons ID -37。
        probe2: 2つ目の探査機。
        comet: 彗星（例 "ハレー彗星"/"halley"/"1P", "エンケ彗星", "67P",
             "紫金山・アトラス"/"C/2023 A3", "ラブジョイ"/"C/2014 Q2"）。
        comet2: 2つ目の彗星。
        engine: 互換引数（描画は 1 本）。"accurate" を指定すると**線形縮尺**
            （相対距離が正確・枠 ±45 AU）で描く。それ以外は対数縮尺（既定）。
            旧 "simple" / "accurate"(matplotlib) は廃止済みで、指定しても同じ描画になる。
        projection: 互換引数。描画は常に斜め俯瞰（仰角34°・方位28°・弱い透視。奥ほど暗く
            細い線で描く。画面上の距離はAUに比例しない）。旧 "top" を指定しても斜め俯瞰で
            描き、その旨を figure.notes に出す。
        elevation: カメラの仰角（度, 0〜90。既定 34）。0 で真横（軌道が線に潰れる）、
            90 で真上（＝旧「真上図」と同じ構図）。注記・題名にも実値を出す。
        azimuth: カメラの方位（度, -360〜360。既定 28）。黄道面内の回転。
        dolly: 透視の強さ＝カメラ距離（画面半径の倍数, 0〜20。既定 4.2）。
            0 で完全な正射影（遠近が消える）。
        view: "system"(既定)=太陽系俯瞰図 / "comet_orbit"=彗星の軌道面ビュー /
            "asteroid_orbit"=小惑星の閉じた楕円軌道面ビュー /
            "apparition"=彗星の見え方チャート（地心距離・日心距離・予想光度・太陽離角の
            推移。comet の指定が必須で、1天体ずつ）。
            comet_orbit は comet の指定が必須で、彗星自身の軌道面を真横から見た図
            （太陽＝円錐曲線の焦点）を返す。e>=1 の C/彗星は閉じない双曲線の枝として描く。
            asteroid_orbit は asteroid の指定が必須で、JPL SBDB の osculating elements に基づく
            小惑星の楕円全体と指定時刻の現在位置を軌道面から描く（1天体ずつ）。
            **comet にカンマ区切りで複数（または comet2 を併用、最大4天体）指定すると、
            1彗星=1パネルで並べた1枚の画像**を返す（パネルごとに軌道面と縮尺が異なる。
            その旨は figure.notes に数値から生成して入る）。
        days: view="apparition" の表示日数（1〜3650、既定 180）。今日の 30 日前から
            days 日後までを描く（直前に過ぎた近日点・最接近も見えるようにするため）。
        route: view="system" で、**通過経路を俯瞰図に重ねる**（破線）。彗星は軌道全体を
            描き、近日点・遠日点には◇と日付を添える（**近日点の日付は SBDB の2体近似に
            加えて JPL Horizons の n 体解も併記**。2体近似はずれることがある。ハレー彗星では
            約164日）。**探査機（probe=）では Horizons の実状態ベクトル点列を描き、惑星との
            最接近（スイングバイ）を距離の局所極小として実測して◇＋日付＋距離を添える**
            （アポロ計画・ボイジャー計画などの重力アシスト航路。例:
            solar_system_now(probe="ボイジャー1号", route=True)）。探査機の航路はミッション
            期間（打ち上げ〜到着）を自動で引き、表に無い機体は指定時刻までの過去1年を描く。
            **アポロの月スイングバイのように地球−月系の内側だけで完結する航路は、太陽系図では
            太陽の描画円盤に隠れて読めないため描かず**、その旨と代替（月面地図の
            planetary_orbiter_track(flyby=True)）を figure.notes に出す。既定 False。
            線形の "accurate" では形は本当の軌道と一致するが、対数縮尺の "simple" では
            線の長さと曲率が実際の軌道と一致しない（その旨は figure.notes に入る）。
            彗星の形そのものを見たいときは view="comet_orbit"。
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
    if vw in ("asteroid_orbit", "asteroid-orbit", "小惑星軌道"):
        names = _split_object_names(asteroid) + _split_object_names(asteroid2)
        unique_names = []
        for name in names:
            if name not in unique_names:
                unique_names.append(name)
        if not unique_names:
            msg = "view='asteroid_orbit' には小惑星の指定が必要です（例: asteroid='ベンヌ'）。"
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": msg, "known_asteroids": sorted(_ASTEROID_ALIASES)})
        if len(unique_names) > 1:
            msg = "view='asteroid_orbit' は1天体ずつです（{} が指定されました）。".format(
                "、".join(unique_names))
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": msg, "asteroids": unique_names})
        return _asteroid_orbit_result(unique_names[0], when)
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
    if not scene.get("planets") and scene.get("planet_errors"):
        # 惑星が1つも計算できていない図を「正常な俯瞰図」として出さない
        # （実測 2026-10-31: when=2100 で planets=0 / error=None / verify.ok=True になっていた）。
        first = scene["planet_errors"][0]
        msg = ("惑星位置を1つも計算できませんでした（{}: {}）。"
               "when を天体暦（JPL DE421・1899-07-29〜2053-10-09）の範囲内で指定してください"
               .format(first.get("name"), first.get("error")))
        return CallToolResult(
            content=[TextContent(type="text", text=msg)],
            structuredContent={"error": "no_planets", "when": str(when),
                               "planet_errors": scene["planet_errors"],
                               "supported": ["1899-07-29", "2053-10-09"]},
        )
    # カメラ（仰角・方位・dolly）。範囲外は黙ってクランプせずエラー結果にする
    # （「指定した角度で描いた」と誤解されないため）。
    _el, _el_err = _split_camera_arg(elevation, "elevation", 0.0, 90.0)
    _az, _az_err = _split_camera_arg(azimuth, "azimuth", -360.0, 360.0)
    _dl, _dl_err = _split_camera_arg(dolly, "dolly", 0.0, 20.0)
    _cam_err = _el_err or _az_err or _dl_err
    if _cam_err:
        return CallToolResult(
            content=[TextContent(type="text", text=_cam_err)],
            structuredContent={"error": _cam_err, "elevation": str(elevation),
                               "azimuth": str(azimuth), "dolly": str(dolly)},
        )
    if _el is not None:
        scene["oblique_elevation"] = _el
    if _az is not None:
        scene["oblique_azimuth"] = _az % 360.0
    if _dl is not None:
        scene["oblique_dolly"] = _dl
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
    # engine は互換引数。描画は 1 本（斜め俯瞰）なので、値は**動径の縮尺だけ**を選ぶ:
    #   "accurate" → 線形縮尺（相対距離が正確。旧 matplotlib 版が持っていた情報）
    #   それ以外（simple / ground / auto / 未指定）→ 対数縮尺（既定）
    scale_kind = "linear" if str(engine or "").strip().lower() in ("accurate", "precise") else "log"
    # projection も互換引数。旧 "top"（真上からの正射影）は斜め俯瞰へ統合したので、
    # 指定されても同じ描画を使い、その旨を注記に残す（黙って無視しない）。
    proj = "oblique"
    compat_notes = []
    if str(projection or "").strip().lower() == "top":
        _el0, _az0, _ = oblique_camera(scene)
        compat_notes.append(
            "projection=\"top\"（黄道面を真上から見た正射影）は斜め俯瞰に統合したため、"
            "この図は仰角{:g}°・方位{:g}°・弱い透視の斜め俯瞰で描いている".format(_el0, _az0))
    # 線形縮尺は ±45 AU が枠。遠方の探査機・彗星は枠外になるので対数縮尺へ落とす
    # （経路を重ねるなら形が本当の軌道と一致する線形の方が良いので、収まるなら維持する）。
    if scale_kind == "linear" and (has_probe or has_com):
        rmax = rng_au or 43.0

        def _rt_fits(rt):
            rt = rt or {}
            if rt.get("error"):
                return False
            return (float(rt.get("r_max_au") or 0.0) <= rmax * 0.98) if rng_au             else (not rt.get("out_of_frame"))

        drawn_coms = [co for co in scene["comets"].values()
                      if not co.get("error") and float(co.get("au") or 0.0) <= rmax]
        fits = (bool(route) and not has_probe and bool(drawn_coms)
                and all(_rt_fits(rt) for rt in (scene.get("routes") or {}).values())
                and all(float(co.get("au") or 0.0) <= rmax for co in drawn_coms))
        if not fits:
            scale_kind = "log"
            compat_notes.append(
                "engine=\"accurate\"（線形縮尺）は ±45 AU が枠で、指定された探査機・彗星が"
                "枠外になるため、対数縮尺の斜め俯瞰で描いている")
    scene["radial_scale"] = scale_kind
    import base64
    try:
        png = _render_oblique(scene)
        oblique_verify = _verify_oblique(scene, png)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]},
        )
    eng_label = "oblique (Pillow, 斜め俯瞰・{}動径・弱い透視)".format(
        "線形" if scale_kind == "linear" else "対数")
    alt = "黄道面を斜め上から見た太陽系の惑星・小惑星・探査機・彗星位置の合成図"
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
        # 破線は彗星の経路にも探査機の航路にも使う。本文の見出しを固定すると
        # 「探査機なのに彗星の通過経路」と書いてしまう（実測 2026-10-31）。
        _rn = list(scene["routes"])
        _probes = [n for n in _rn
                   if (scene["routes"][n] or {}).get("kind") == "spacecraft"
                   or n in (scene.get("probes") or {})]
        if _probes and len(_probes) == len(_rn):
            route_label = "探査機の飛行経路"
        elif _probes:
            route_label = "通過経路（彗星・探査機）"
        else:
            route_label = "彗星の通過経路"
        lines.append("**{}**（図の破線＝黄道面への正射影）:".format(route_label))
        for n, rt in scene["routes"].items():
            if rt.get("error"):
                lines.append("- {}: 経路を取得できませんでした（{}）".format(n, rt["error"]))
                continue
            if rt.get("kind") == "spacecraft":
                # 探査機は周期・楕円の概念が無いので、観測区間と最接近だけを書く。
                _sp = rt.get("span") or []
                _enc = [m for m in (rt.get("marks") or []) if m.get("label")]
                _enc_txt = "／".join(
                    "{} {}".format(m.get("label"), (m.get("date") or "")[:10]) for m in _enc[:4])
                lines.append("- {}: {}〜{} の実測経路{}".format(
                    rt.get("name") or n,
                    (_sp[0][:10] if _sp else "?"), (_sp[1][:10] if len(_sp) > 1 else "?"),
                    ("／最接近 " + _enc_txt) if _enc_txt else "（最接近の記録なし）"))
                continue
            mk = "／".join(_mark_txt(m, with_proj=False) for m in (rt.get("marks") or []))
            lines.append("- {}: {}周期 {} 日（{}）".format(
                rt.get("name") or n, (mk + "・") if mk else "",
                int(rt["period_days"]) if rt.get("period_days") else "-",
                "閉じた楕円" if rt.get("closed") else "閉じない（双曲線/放物線のため打ち切り）"))
    # 線形(accurate)は縮尺が固定（実測 約12 px/AU）で、太陽マーカー(s=300)が半径 約1.7 AU 相当に
    # なる。内惑星や近日点はこのマーカーと重なるので、誇張であることを数値付きで明示する。
    exagg = ["惑星の色アイコン（実寸ではない）", "小惑星帯の帯（2.0-3.4AUの目安）"]
    sun_au = scene.get("sun_marker_au")
    if sun_au:
        exagg.append("太陽の描画マーカー（半径 {:.1f} AU 相当・実寸ではない。太陽近傍の天体は"
                     "このマーカーと重なって見える）".format(float(sun_au)))

    # 通過経路を重ねたときの注記・検証（数値から生成する。図と文を食い違わせない）。
    route_verify = None
    route_notes = []
    if scene.get("routes"):
        _comet_names = "・".join((rt.get("name") or n) for n, rt in scene["routes"].items()
                                if not rt.get("error") and rt.get("kind") != "spacecraft")
        _probe_names = "・".join((rt.get("name") or n) for n, rt in scene["routes"].items()
                                if not rt.get("error") and rt.get("kind") == "spacecraft")
        if _comet_names:
            if scale_kind == "log":
                route_notes.append(
                    "破線は{}の通過経路（黄道座標の3D位置を斜め俯瞰へ投影）。この図は対数縮尺なので、"
                    "線の長さと曲率は実際の楕円と一致しない。形そのものは view=\"comet_orbit\" の図を参照"
                    .format(_comet_names))
            else:
                route_notes.append(
                    "破線は{}の通過経路（黄道座標の3D位置を斜め俯瞰へ投影）。この図は線形縮尺なので、"
                    "形は実際の楕円と一致する".format(_comet_names))
        if _probe_names:
            route_notes.append(
                "破線は{}の飛行経路（JPL Horizons の実状態ベクトル点列）。矢印は進行方向。"
                "{}（探査機の経路は彗星の軌道要素を持たないため、彗星向けの近日点・遠日点の注記は"
                "適用しない）".format(_probe_names,
                                  "この図は対数縮尺なので、線の長さと曲率は実際の軌道と一致しない"
                                  if scale_kind == "log" else
                                  "この図は線形縮尺なので、相対距離は実際と一致する"))
        for n, rt in scene["routes"].items():
            if rt.get("error"):
                route_notes.append("{} の経路を取得できませんでした（{}）".format(n, rt["error"]))
                continue
            if rt.get("kind") == "spacecraft":
                # 探査機のスイングバイ航路は彗星の軌道要素（e/q/近日点）を持たない。
                # 彗星向けの注記を当てると「閉じない軌道 e=0.0000 のため 0 AU で打ち切り」の
                # ような誤読を生むので、実測した最接近点だけを数値で書く。
                sm = [m for m in (rt.get("marks") or [])]
                if sm:
                    route_notes.append("経路の◇は実際のスイングバイ（最接近）: " + "／".join(
                        "{} {}（{:,.0f} km）".format(m.get("body") or m.get("label"), m.get("date"),
                                                    float(m.get("dist_km") or 0.0))
                        for m in sm).replace(",", ""))
                else:
                    route_notes.append("この機体にはスイングバイ（最接近）は検出されなかった"
                                       "（惑星から十分離れた航路）。")
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
    # 表示範囲・経路の検証も、描画が記録した画素座標（dash_samples / mark px）で回す。
    rng_verify = _verify_range_draw(png, scene)
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
    if oblique_verify is not None:
        # 斜め俯瞰の検証（期待 px のマーカー）＋表示範囲・経路・1光日の検証を 1 つの
        # figure.verify にまとめる（どれか 1 つでも落ちたら ok=False）。
        _m = dict(verify_out or {})
        _m["markers"] = oblique_verify
        _m["checked"] = oblique_verify.get("checked")
        _m["ok"] = bool(oblique_verify.get("ok")) and bool((verify_out or {}).get("ok", True))
        _m["missing"] = (list((verify_out or {}).get("missing") or [])
                         + list(oblique_verify.get("missing") or []))
        _m["method"] = "+".join([p for p in ("oblique_expected_px+marker_core",
                                             (verify_out or {}).get("method")) if p])
        verify_out = _m
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
            "1光日リング（破線円）＝太陽から {:.4f} AU＝{:,} km（光が24時間で進む距離）。"
            "リングは黄道面内の真距離の円で、斜め俯瞰では楕円に見える。マーカーも3Dの実位置"
            "なので、距離は見た目の重なりではなく structuredContent の au で読むこと"
            .format(float(ldd["au"]), int(round(ldd["km"]))))
    elif ldd:
        ld_notes.append("1光日（{:.4f} AU）は{}にあるため、この図には描いていない".format(
            float(ldd["au"]), ldd.get("why") or "表示範囲の外"))
    _ld_projections = ldd.get("projections") or ([ldd["proj"]] if ldd.get("proj") else [])
    for pj in _ld_projections:
        ld_notes.append(
            "◇ は {} の現在の向きで 1 光日（{:.2f} AU）に達する点（黄経 {:.1f}°／黄緯 {:.1f}°）。"
            "到達までの間に方向はほとんど変わらないので、現在の向きで描いている".format(
                pj["name"], float(ldd["au"]), pj["eclLon"], pj["eclLat"]))
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
    if scene.get("routes"):
        why_txt = ("太陽を図の中心に置く俯瞰図のため、楕円軌道の焦点は主天体ではない"
                   "（惑星の軌道の形は描かず、指定された彗星・探査機の経路だけを破線で重ねている）")
    fig = figure_payload(
        kind="heliocentric_overview",
        title="太陽系の現在位置（太陽中心・黄道面俯瞰）",
        view=view_spec("ecliptic_plane", "oblique",
                       "黄道面を斜め上（仰角{:g}°・方位{:g}°・弱い透視）から見た日心俯瞰図。"
                       "太陽は図の中心、画面上の距離はAUに比例しない".format(
                           oblique_camera(scene)[0], oblique_camera(scene)[1]),
                       why=why_txt),
        primary=primary_spec("太陽", "center"),
        scale=_scale_with_range(scale_spec(scale_kind, to_scale=(scale_kind == "linear"),
                                           px_per_unit=(scene.get("px_per_au")
                                                        if scale_kind == "linear" else None),
                                           exaggerated=exagg, unit="AU"),
                                scene),
        notes=figure_notes(extra=[
            "惑星軌道の円は" + ("対数縮尺の目安で、離心率（水星 e=0.206 など）は無視している"
                               if scale_kind == "log" else "公転長半径の円で、離心率は無視している"),
            "マーカーは黄道座標の3Dの実位置（黄経・黄緯）。真の距離は structuredContent の au を参照",
            (f"惑星位置 {len(scene['planet_errors'])} 件の計算に失敗しました"
             "（structuredContent.planet_errors を参照）"
             if scene.get("planet_errors") else ""),
            "彗星の尾は反太陽方向に描いており、進行方向ではない",
            ("斜め俯瞰: 奥行き（カメラからの距離）で線の太さと明度を変えており、手前が明るい。"
             + ("" if scale_kind == "linear" else "画面上の距離はAUに比例しないので、距離は"
                                                  " structuredContent の au を参照。")
             + "彗星の尾は向き（反太陽方向）だけを示す模式表現で、長さ・明るさは実際の光度を反映しない"),
        ] + compat_notes + rng_notes + route_notes + ld_notes),
        caption="太陽を中心とした日心俯瞰図。数値は structuredContent の各値を参照。"
                + ("破線＝{}。".format(route_label) if scene.get("routes") else ""),
        verify=verify_out,
    )
    lines.append("")
    lines.append(figure_text_block(fig))
    lines.append("画像は上に表示（base64 PNG）。出典: JPL DE421+SBDB+Horizons / Skyfield")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), img],
        structuredContent={"time_utc": scene["time_utc"], "engine": eng_label,
                           "projection": proj,
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
                           "camera": {"elevation_deg": oblique_camera(scene)[0],
                                      "azimuth_deg": oblique_camera(scene)[1],
                                      "dolly": oblique_camera(scene)[2]},
                           "range_au": (scene.get("range_au") or None),
                           "range_resolved": rng_why,
                           "out_of_range": (rng_rep.get("out_of_range") or []),
                           "comet_routes": {n: {k: v for k, v in (rt or {}).items()
                                                if k != "points"}
                                            for n, rt in (scene.get("routes") or {}).items()},
                           "source": "JPL DE421+SBDB+Horizons / Skyfield"},
    )

"""彗星の見え方チャート（地心距離・予想光度・太陽離角の推移, 認証不要）。

`solar_system_now(view="apparition")` から呼ばれる描画ルーチン。軌道面ビュー
（`view="comet_orbit"`＝**軌道の形**）とは別に、「いつ・どれくらい地球に近づき・
どれくらい明るくなるか」＝**観測の可否**を時系列で示す。

- 要素・全光度の式（M1/K1）は JPL SBDB（認証不要）。位置は同じ要素からのケプラー2体伝播
  （`solar_system.comet_xyz_from_elements`：楕円／放物線／双曲線を離心率で場合分け）。
- 地球の日心黄道位置は JPL DE421 + Skyfield（ローカル計算）。Δ は地心距離（彗星核と
  地球中心の直線距離）。
- 予想光度は m1 = M1 + 5·log10(Δ) + K1·log10(r)（JPL SBDB の全光度の式）。**実測は式から
  数等ずれることがある**ため、注記に式と出典を数値から生成して明示する。
- 太陽離角は「地球から見た彗星と太陽の角」。小さいほど薄明の中に沈み、観測が難しくなる。

描いた折れ線の極値・マーカーの位置を**画素から測り直して自己検証**する（申告した日時・
距離に曲線色の画素が無ければ `verify.ok` は偽になる）。図の注記は数値から生成する。
"""
from __future__ import annotations

import base64
import datetime
import math
from typing import List, Optional, Sequence, Tuple

from mcp.types import CallToolResult, ImageContent, TextContent

from .img_common import (conic_from_elements, figure_notes, figure_payload,
                         figure_text_block, load_font, media_link_line, save_output,
                         scale_spec, view_spec)
from .input_utils import as_int
from .solar_system import (_comet_elements, _comet_unknown_hint, _horizons_cmd_for,
                           _horizons_vectors_range, _load, _resolve_when, _sbdb_elements,
                           comet_xyz_from_elements)

# 彗星の位置は JPL Horizons の n 体解（1リクエストで期間まとめて取得）、
# 地球の日心黄道位置は DE421 + Skyfield（ローカル）で出す。Horizons が
# 使えないときだけ SBDB 要素の2体近似へ退避する（_series 参照）。
_AU_KM = 149_597_870.7

_BG = (7, 9, 22)
_PANEL_BG = (13, 16, 30)
_GRID = (40, 46, 68)
_TEXT = (228, 234, 244)
_DIM = (150, 158, 180)

# 曲線の色。図と色申告が食い違わないよう、ここが単一の出典（検証もこの値で測る）。
_R_COLOR = (255, 214, 120)        # 日心距離 r（太陽からの距離）
_D_COLOR = (150, 235, 255)        # 地心距離 Δ（地球からの距離＝彗星色のシアン）
_MAG_COLOR = (255, 150, 60)       # 予想光度（軌道面ビューの軌道線と同じオレンジ）
_ELONG_COLOR = (170, 255, 170)    # 太陽離角
_TODAY_COLOR = (120, 200, 255)    # 今日
_PERI_COLOR = (255, 214, 120)     # 近日点（日心距離の最小＝r 曲線と同じ色）
_CLOSE_COLOR = (255, 90, 120)     # 地球最接近

_W, _H = 1060, 900
_ML, _MR = 118, 44                # 左右の余白（左は y 目盛のため広い）
_HDR = 96
_PH = 232                         # パネル高
_GAP = 34

# 位置の出典で注記の文面が変わる（2体近似と n 体解を混同させない）。
_SRC_HZ = ("JPL Horizons（N 体解の位置）＋ JPL SBDB（軌道要素・全光度の式 M1/K1）"
           "＋ JPL DE421 + Skyfield（地球位置）")
_SRC_KEP = ("JPL SBDB（軌道要素・全光度の式 M1/K1）＋ JPL DE421 + Skyfield"
            "（地球位置・ケプラー2体伝播）")


def _src_of(d: dict) -> str:
    return _SRC_HZ if d.get("pos_src") == "horizons" else _SRC_KEP


def _f(v: float, digits: int = 3) -> str:
    """注記・目盛用の数値表記。"""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1000:
        return "{:,.0f}".format(v)
    if a >= 10:
        return "{:.1f}".format(v)
    return ("{:." + str(digits) + "f}").format(v)


def _fmt_jd(jd) -> str:
    """JD を YYYY-MM-DD にする（None や暦の範囲外は "-"）。

    双曲線軌道では Horizons が周期を 1e99 のような番兵で返すことがあり、そのまま
    日付へ変換すると OverflowError になる。表示側は必ずこれを通す。
    """
    if jd is None or not (1000000.0 < float(jd) < 4000000.0):
        return "-"
    return _jd_to_utc(float(jd)).strftime("%Y-%m-%d")


def _jd_to_utc(jd: float) -> datetime.datetime:
    """JD(TT) を UTC の datetime へ（暦の差は数十秒＝図の分解能より小さい）。"""
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(days=float(jd) - 2440587.5))


def _norm_elements(cid: str, el: dict) -> dict:
    """`_comet_elements` の戻り（SBDB 経路／Horizons 経路）を共通形へ正規化する。"""
    if el.get("typ") == "horizons":
        return {"e": float(el["e"]), "q": el.get("q"), "tp": el.get("tp_jd"),
                "i": math.radians(el["i_deg"]), "node": math.radians(el["node_deg"]),
                "argp": math.radians(el["argp_deg"]), "period_days": el.get("period_days"),
                "fullname": el.get("fullname") or cid, "source": el.get("source", "")}
    raw = el.get("_raw") or {}
    return {"e": float(el["e"]), "q": el.get("q"), "tp": raw.get("tp"),
            "i": raw.get("i"), "node": raw.get("node"), "argp": raw.get("argp"),
            "period_days": raw.get("period_days"), "m1": raw.get("m1"), "k1": raw.get("k1"),
            "fullname": el.get("fullname") or cid, "source": el.get("source", "")}


def _magnitude_params(cid: str, el: dict) -> Tuple[Optional[float], Optional[float]]:
    """全光度の式の係数 (M1, K1) を返す。無ければ (None, None)（＝光度パネルは描かない）。"""
    m1, k1 = el.get("m1"), el.get("k1")
    if m1 is None or k1 is None:
        try:                                   # Horizons 経路の彗星は SBDB から補う
            raw = _sbdb_elements(cid)
            m1 = raw.get("m1") if m1 is None else m1
            k1 = raw.get("k1") if k1 is None else k1
        except Exception:
            pass
    return (m1, k1)


def _earth_xyz(eph, ts, jd: float, frame: str = "icrf") -> Tuple[float, float, float]:
    """地球の太陽中心位置ベクトル（AU）。

    frame="icrf" は Horizons の REF_PLANE='FRAME'（ICRF）と同じ座標系。実測: 169P の
    地心距離が Horizons の出力と 1e-7 au で一致する。frame="ecliptic" は黄道 J2000 で、
    SBDB の要素から2体伝播する彗星位置（黄道 J2000）と揃えるために使う。
    **Horizons の ECLIPTIC 出力は DE421 の地球位置と 0.003 au（45万 km）食い違う**ので、
    距離を混ぜて計算してはいけない（実測で確認）。
    """
    v = eph["earth"].at(ts.tt_jd(jd)) - eph["sun"].at(ts.tt_jd(jd))
    if frame == "ecliptic":
        from skyfield.framelib import ecliptic_frame
        lat, lon, dist = v.frame_latlon(ecliptic_frame)
        d, la, lo = dist.au, lat.radians, lon.radians
        return (d * math.cos(la) * math.cos(lo), d * math.cos(la) * math.sin(lo),
                d * math.sin(la))
    x, y, z = v.position.au
    return (float(x), float(y), float(z))


def _row_from_xyz(jd: float, cx: float, cy: float, cz: float, eph, ts,
                  m1: Optional[float], k1: Optional[float], frame: str = "icrf") -> dict:
    """位置ベクトル（太陽中心・AU）から 1 時刻の r / Δ / 光度 / 離角を作る。

    **cx/cy/cz と地球上の座標系は同じでなければならない**（Horizons の ICRF 出力には
    frame="icrf"、SBDB 要素の2体伝播＝黄道 J2000 には frame="ecliptic"）。

    Δ は**地球中心**（地心）と彗星核（天体中心）の距離。太陽離角は「地球→太陽」と
    「地球→彗星」のなす角で、小さいほど薄明の中に沈んで観測しにくい。
    """
    r = math.sqrt(cx * cx + cy * cy + cz * cz)
    ex, ey, ez = _earth_xyz(eph, ts, jd, frame)
    dx, dy, dz = cx - ex, cy - ey, cz - ez
    delta = math.sqrt(dx * dx + dy * dy + dz * dz)
    cos_e = (-(ex * dx + ey * dy + ez * dz)) / max(
        1e-12, math.sqrt(ex * ex + ey * ey + ez * ez) * delta)
    elong = math.degrees(math.acos(max(-1.0, min(1.0, cos_e))))
    mag = None
    if m1 is not None and k1 is not None and delta > 0 and r > 0:
        mag = float(m1) + 5.0 * math.log10(delta) + float(k1) * math.log10(r)
    return {"jd": float(jd), "utc": _jd_to_utc(jd).strftime("%Y-%m-%d %H:%M"),
            "r_au": r, "delta_au": delta, "elong_deg": elong, "mag": mag}


def _sample(el: dict, eph, ts, jd: float, m1: Optional[float], k1: Optional[float]) -> dict:
    """SBDB 要素からの2体伝播で1時刻を計算する（Horizons が使えないときの退避経路）。"""
    cx, cy, cz, _r, _lon, _lat = comet_xyz_from_elements(el, jd)
    return _row_from_xyz(jd, cx, cy, cz, eph, ts, m1, k1, "ecliptic")


def _series(cid: str, nel: dict, eph, ts, jd0: float, jd1: float, n: int,
            m1: Optional[float], k1: Optional[float]) -> Tuple[List[dict], str]:
    """期間の系列を返す（(行, 位置の出典)）。

    まず JPL Horizons の **n 体解**の位置を1リクエストでまとめて取り、失敗したら
    SBDB 要素の2体近似へ退避する（精度の差は注記に出す）。
    """
    try:
        step = max(1.0 / 1440.0, (jd1 - jd0) / (n - 1.0))
        rows = _horizons_vectors_range(_horizons_cmd_for(cid), jd0, jd1, step)
        return ([_row_from_xyz(jd, x, y, z, eph, ts, m1, k1, "icrf")
                 for jd, x, y, z in rows], "horizons")
    except Exception:
        return ([_sample(nel, eph, ts, jd0 + (jd1 - jd0) * i / (n - 1.0), m1, k1)
                 for i in range(int(n))], "kepler2body")


def _refine_rows(cid: str, nel: dict, eph, ts, jd_c: float, half: float,
                 m1: Optional[float], k1: Optional[float]) -> Tuple[List[dict], str]:
    """極値の前後を細かい刻みで取り直す（粗い格子だと最小値が丸まるため）。"""
    step = max(0.25 / 24.0, 2.0 * half / 120.0)
    try:
        rows = _horizons_vectors_range(_horizons_cmd_for(cid), jd_c - half, jd_c + half, step)
        return ([_row_from_xyz(jd, x, y, z, eph, ts, m1, k1, "icrf")
                 for jd, x, y, z in rows], "horizons")
    except Exception:
        nn = 120
        return ([_sample(nel, eph, ts, jd_c - half + 2.0 * half * i / (nn - 1.0), m1, k1)
                 for i in range(nn)], "kepler2body")


def _delta_of(row, eph, ts) -> float:
    """Horizons の1行（JD, X, Y, Z／ICRF 太陽中心）から地心距離を出す。"""
    return math.dist(row[1:4], _earth_xyz(eph, ts, row[0], "icrf"))


def _horizons_min_delta(cid: str, eph, ts, jd_from: float, jd_to: float) -> Tuple[float, float]:
    """Horizons の位置から最小地心距離を求める（粗い走査 → 区間を絞って15分刻み）。"""
    coarse = max(0.5, (jd_to - jd_from) / 240.0)
    rows = _horizons_vectors_range(_horizons_cmd_for(cid), jd_from, jd_to, coarse)
    best = min(rows, key=lambda r_: _delta_of(r_, eph, ts))
    fine = _horizons_vectors_range(_horizons_cmd_for(cid), best[0] - coarse * 1.5,
                                   best[0] + coarse * 1.5, 0.25 / 24.0)
    jd, d = min(((r_[0], _delta_of(r_, eph, ts)) for r_ in fine), key=lambda p: p[1])
    return jd, d


def _next_approach(cid: str, nel: dict, eph, ts, jd_from: float,
                   jd_to: float) -> Tuple[float, float, str]:
    """期間内の最小地心距離（n 体解を優先し、失敗時は2体近似へ退避）。"""
    try:
        return _horizons_min_delta(cid, eph, ts, jd_from, jd_to) + ("horizons",)
    except Exception:
        jd, d = _closest_approach(nel, eph, ts, jd_from, jd_to)
        return jd, d, "kepler2body"


# ---------- 描画 ----------
def _date_ticks(jd0: float, jd1: float, target: int = 8) -> List[float]:
    """x 軸の目盛時刻（JD）。日割りから粗い刻みへ広げて本数を抑える。"""
    span = max(1e-6, jd1 - jd0)
    step = 1825
    for s_ in (3, 7, 10, 14, 30, 60, 90, 180, 365, 730, 1825):
        if span / s_ <= target:
            step = s_
            break
    out, j = [], math.ceil(jd0 / step) * step
    while j <= jd1 + 1e-9:
        out.append(j)
        j += step
    return out


def _pixel_near(img, box, colors, tol: int = 60) -> int:
    """矩形内に指定色（曲線色）の画素がいくつあるかを数える。"""
    px = img.convert("RGB").load()
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    n = 0
    for y in range(max(0, y0), min(img.size[1], y1)):
        for x in range(max(0, x0), min(img.size[0], x1)):
            r, g, b = px[x, y]
            for c in colors:
                if abs(r - c[0]) + abs(g - c[1]) + abs(b - c[2]) < tol:
                    n += 1
                    break
    return n


def _place_label(img, dd, xy, text: str, font, fill, curve_colors,
                 tries=((0, -30), (0, 10), (-90, -30), (14, 10), (-150, 10), (0, -52),
                        (-60, -52), (20, -52), (-40, 10), (0, 26), (10, 26), (-110, 26))):
    """曲線に重ならない位置へラベルを置く。どこにも置けなければ描かず None を返す。

    重ねて描くと「図と注記が食い違う」ので、置けなかったラベルは呼び出し側が
    skipped_labels として注記に出す（AGENTS 規約12）。
    """
    w = dd.textlength(text, font=font)
    x0, y0 = xy
    best = None
    for ddx, ddy in tries:
        box = (x0 + ddx, y0 + ddy, x0 + ddx + w + 6, y0 + ddy + font.size + 4)
        if not (0 <= box[0] and box[2] < img.size[0] and 0 <= box[1] and box[3] < img.size[1]):
            continue
        hit = _pixel_near(img, box, curve_colors)
        if hit == 0:
            dd.text((x0 + ddx + 3, y0 + ddy + 2), text, font=font, fill=fill)
            return box
        if best is None or hit < best[0]:
            best = (hit, box)
    return None


def _vy(v: float, top: float, bottom: float, plot0: float, plot1: float) -> float:
    """値 -> 画素 y。top の値が上端・bottom の値が下端（光度パネルは小さい値が上）。"""
    if abs(bottom - top) < 1e-12:       # 退化した軸だけを広げる。bottom < top は
        bottom = top + 1e-6            # 「値が小さい方が下」という正しい逆向き軸
    return plot0 + (v - top) / (bottom - top) * (plot1 - plot0)


def _draw_chart(series: Sequence[dict], info: dict) -> dict:
    """3段パネル（距離／予想光度／太陽離角）の時系列図を描き、検証用の座標も返す。"""
    from PIL import Image, ImageDraw
    f_t, f_s, f_m = load_font(25, bold=True), load_font(17), load_font(15)
    img = Image.new("RGB", (_W, _H), _BG)
    dd = ImageDraw.Draw(img)
    x0, x1 = _ML, _W - _MR
    jd0, jd1 = info["jd0"], info["jd1"]
    span = max(1e-9, jd1 - jd0)

    def px(jd: float) -> float:
        return x0 + (jd - jd0) / span * (x1 - x0)

    dd.text((_ML, 16), info["title"], font=f_t, fill=_TEXT)
    dd.text((_ML, 48), info["sub"], font=f_s, fill=_DIM)
    dd.text((_ML, 70), info["sub2"], font=f_m, fill=_DIM)

    checks, label_boxes, skipped = [], [], []
    panels = info["panels"]
    ticks_x = _date_ticks(jd0, jd1)
    for i, p in enumerate(panels):
        py0 = _HDR + i * (_PH + _GAP)
        plot0, plot1 = py0 + 30, py0 + _PH - 16
        top, bottom = float(p["top"]), float(p["bottom"])
        dd.rectangle([x0, plot0, x1, plot1], fill=_PANEL_BG, outline=_GRID)
        dd.text((x0 + 8, py0 + 7), p["title"], font=f_s, fill=_TEXT)
        xoff = x0 + 8 + int(dd.textlength(p["title"], font=f_s)) + 14
        for c in p["series"]:
            dd.text((xoff, py0 + 8), "■ " + c["label"], font=f_m, fill=c["color"])
            xoff += int(dd.textlength("■ " + c["label"], font=f_m)) + 14
        for k in range(5):                      # y 目盛
            v = top + (bottom - top) * k / 4.0
            y = _vy(v, top, bottom, plot0, plot1)
            dd.line([(x0, y), (x1, y)], fill=_GRID)
            dd.text((14, y - 8), p["fmt"].format(v), font=f_m, fill=_DIM)
        for jd in ticks_x:                      # x 目盛（日付ラベルは最下段だけ）
            dd.line([(px(jd), plot0), (px(jd), plot1)], fill=_GRID)
            if i == len(panels) - 1:
                dd.text((px(jd) - 16, plot1 + 3), _jd_to_utc(jd).strftime("%m/%d"),
                        font=f_m, fill=_DIM)
        for c in p["series"]:                   # 折れ線
            pts = [(px(s["jd"]), _vy(s[c["key"]], top, bottom, plot0, plot1))
                   for s in series if s.get(c["key"]) is not None]
            if len(pts) >= 2:
                dd.line(pts, fill=c["color"], width=2, joint="curve")

    mark_x = {}
    for m in info["marks"]:                     # 縦マーカー（今日／近日点／地球最接近）
        xr = px(m["jd"])
        if xr < x0 - 1.0 or xr > x1 + 1.0:
            # 期間外のマーカーは描かない（端に張り付けない＝図と日付が食い違う）
            mark_x[m["label"]] = None
            continue
        # Horizons の格子は開始時刻が数秒ずれるので、端ちょうど（今日＝期間の初日）は
        # 1px 内側へ寄せる。それ以外は動かさない。
        x = min(max(xr, x0 + 1.0), x1 - 1.0)
        mark_x[m["label"]] = x
        for i in range(len(panels)):
            py0 = _HDR + i * (_PH + _GAP)
            for yy in range(int(py0 + 30), int(py0 + _PH - 16), 6):
                dd.line([(x, yy), (x, yy + 3)], fill=m["color"])
        dd.text((min(max(x - 20, 4), _W - 96), _HDR + 6), m["label"], font=f_m,
                fill=m["color"])

    # 極値は「点を全部打ってからラベルを置く」の2段階にする。先に点を打たないと、
    # 後から別系列の点が既存ラベルの矩形に入り、画素検査（ラベル重なり）が偽になる。
    placed = []
    for i, p in enumerate(panels):
        py0 = _HDR + i * (_PH + _GAP)
        plot0, plot1 = py0 + 30, py0 + _PH - 16
        top, bottom = float(p["top"]), float(p["bottom"])
        for c in p["series"]:
            if not c.get("extremum"):
                continue
            pool = [s for s in series if s.get(c["key"]) is not None]
            if not pool:
                continue
            fn = min if c["extremum"] == "min" else max
            tgt = fn(pool, key=lambda s: s[c["key"]])
            x = px(tgt["jd"])
            y = _vy(tgt[c["key"]], top, bottom, plot0, plot1)
            dd.ellipse([x - 4, y - 4, x + 4, y + 4], fill=c["color"])
            # 他系列とほぼ同じ値（r と Δ が並走する等）のときは、後に描いた曲線が先の
            # 曲線を塗り潰す。図の主張は「その位置に曲線がある」ことなので、検証では
            # 重なった系列の色も許容する（色の同定まで求めない）。
            tol_v = (bottom - top) * 0.005
            ok_colors = [c["color"]]
            for cc in p["series"]:
                if cc is not c and tgt.get(cc["key"]) is not None and                         abs(tgt[cc["key"]] - tgt[c["key"]]) <= tol_v:
                    ok_colors.append(cc["color"])
            placed.append((c, p, tgt, x, y, ok_colors))

    for c, p, tgt, x, y, ok_colors in placed:
        txt = "{} {} {}".format(c.get("short", c["label"]), p["fmt"].format(tgt[c["key"]]),
                                p["unit"])
        # ラベルの文字色は曲線色にせず白系にする（同じ色だと「ラベルが曲線に重なって
        # いる」と画素検査が誤検出し、自己検証が偽になる）
        # 避ける対象は「全パネルの曲線色＋マーカー線の色」。マーカー線はラベルより先に
        # 描かれるので、ここで含めておけばラベルが線に重ならない（実測で1px 重なった）。
        box = _place_label(img, dd, (x, y), txt, f_m, _TEXT,
                           [cc["color"] for pp in panels for cc in pp["series"]]
                           + [m["color"] for m in info["marks"]])
        if box:
            label_boxes.append(box)
        else:
            skipped.append("{}（{}）".format(txt, _jt_short(tgt["jd"])))
        checks.append({"x": x, "y": y, "colors": ok_colors,
                       "what": "{} の極値 {} {}".format(
                           c["label"], txt,
                           "（他系列と重なる）" if len(ok_colors) > 1 else "")})

    mark_px = {}
    for m in info["marks"]:                     # マーカー線が本当に描けたかを画素で測る
        x = mark_x.get(m["label"])
        if x is None:                           # 期間外（描かないのが正しい）
            mark_px[m["label"]] = -1
            continue
        cnt = 0
        for i in range(len(panels)):
            py0 = _HDR + i * (_PH + _GAP)
            cnt += _pixel_near(img, (x - 1, py0 + 30, x + 2, py0 + _PH - 16), [m["color"]])
        mark_px[m["label"]] = cnt
    # 同じ日（同じ x）に来るマーカーは、後に描いた線が先の線を塗り潰す。画素が 0 でも
    # 異常ではないので overlapped として検証側へ渡す（今日＝近日点＝最接近は起こり得る）。
    same_day = []
    for a in mark_x:
        for b in mark_x:
            if a < b and mark_x[a] and mark_x[b] and abs(mark_x[a] - mark_x[b]) <= 2.0:
                same_day.append(sorted([a, b]))

    dd.text((_ML, _H - 26), info["footer"], font=f_m, fill=_DIM)
    return {"img": img, "checks": checks, "label_boxes": label_boxes,
            "skipped_labels": skipped, "marker_pixels": mark_px, "mark_same_day": same_day}


def _jt_short(jd: float) -> str:
    return _jd_to_utc(jd).strftime("%m/%d")


def _verify_chart(img, checks: Sequence[dict], label_boxes: Sequence[Tuple],
                  curve_colors: Sequence[Tuple], tol_px: int = 7,
                  marker_pixels: Optional[dict] = None,
                  skipped: Sequence[str] = (),
                  mark_same_day: Optional[Sequence] = None) -> dict:
    """申告した極値の座標に曲線色の画素があるか、ラベルが曲線に被っていないかを検査する。"""
    px = img.convert("RGB").load()

    def near(x: float, y: float, colors) -> float:
        best = float(tol_px + 1)
        for dy in range(-tol_px, tol_px + 1):
            for dx in range(-tol_px, tol_px + 1):
                xx, yy = int(round(x)) + dx, int(round(y)) + dy
                if not (0 <= xx < img.size[0] and 0 <= yy < img.size[1]):
                    continue
                r, g, b = px[xx, yy]
                if any(abs(r - c[0]) + abs(g - c[1]) + abs(b - c[2]) < 60 for c in colors):
                    best = min(best, math.hypot(dx, dy))
        return best

    missing, worst = [], 0.0
    for ck in checks:
        d = near(ck["x"], ck["y"], ck["colors"])
        worst = max(worst, d)
        if d > tol_px:
            missing.append(ck["what"])
    overlap = sum(_pixel_near(img, b, curve_colors) for b in label_boxes)
    marks = dict(marker_pixels or {})
    outside = sorted(k for k, v in marks.items() if v < 0)
    drawn = [k for k, v in marks.items() if v > 0]
    overlapped = set()
    for pair in (mark_same_day or []):           # 重なった線は正常（後に描いた方が残る）
        if any(p_ in drawn for p_ in pair):
            overlapped.update(pair)
    empty_marks = [k for k, v in marks.items() if v == 0 and k not in overlapped]
    coincident = [ck["what"] for ck in checks if len(ck["colors"]) > 1]
    return {"ok": bool(not missing and overlap == 0 and not empty_marks),
            "coincident_points": coincident,
            "checked_points": len(checks), "missing": missing[:3],
            "worst_px_error": round(worst, 1), "tolerance_px": tol_px,
            "label_overlap_px": overlap, "labels_drawn": len(label_boxes),
            "labels_skipped": len(skipped), "marker_pixels": marks,
            "markers_missing": empty_marks, "markers_overlapped": sorted(overlapped),
            "markers_outside_window": outside,
            "method": "申告した極値の座標に曲線色の画素があるかを画素から測り直し、"
                      "描いたラベル矩形に曲線色が混入していないことも併せて検査"}


def _png_bytes(img) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------- 特徴点（図の範囲の内外を区別して返す） ----------
def _closest_approach(nel: dict, eph, ts, jd_from: float, jd_to: float,
                      coarse: float = 2.0) -> Tuple[float, float]:
    """[jd_from, jd_to] の最小地心距離とその時刻（粗い走査＋三分探索）。"""
    best_jd, best_d = jd_from, None
    j = jd_from
    while j <= jd_to + 1e-9:
        d = _sample(nel, eph, ts, j, None, None)["delta_au"]
        if best_d is None or d < best_d:
            best_d, best_jd = d, j
        j += coarse
    lo, hi = best_jd - coarse, best_jd + coarse
    for _ in range(40):
        m1_, m2_ = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        if _sample(nel, eph, ts, m1_, None, None)["delta_au"] < \
                _sample(nel, eph, ts, m2_, None, None)["delta_au"]:
            hi = m2_
        else:
            lo = m1_
    jc = (lo + hi) / 2.0
    return jc, _sample(nel, eph, ts, jc, None, None)["delta_au"]


def _peri_times(nel: dict, jd_now: float) -> Tuple[Optional[float], Optional[float]]:
    """近日点通過（前回・次回）。周期が分かる軌道（楕円）でのみ算出できる。"""
    tp, per = nel.get("tp"), nel.get("period_days")
    if tp is None:
        return None, None
    try:
        per_f = float(per) if per else 0.0
    except (TypeError, ValueError):
        per_f = 0.0
    # e>=1（放物線・双曲線）や、Horizons が周期に 1e99 の番兵を返す場合は「次回」が無い。
    if float(nel.get("e") or 0.0) >= 1.0 or not (1.0 <= per_f <= 1.0e7):
        return tp, None
    k = math.ceil((jd_now - tp) / per_f)
    return tp + (k - 1) * per_f, tp + k * per_f


def _apparition_data(name: str, when_iso, days, before, samples) -> dict:
    """要素解決 → 期間のサンプリング → 特徴点。失敗は {"error": ...} で返す。"""
    try:
        cid, el = _comet_elements(name)
    except Exception as e:
        return {"error": "彗星 {} の軌道要素を取得できませんでした（{}）".format(
            name, str(e)[:150]), "hint": _comet_unknown_hint(name)}
    nel = _norm_elements(cid, el)
    if not nel.get("q") or nel.get("tp") is None:
        return {"error": "{} は近日点通過時刻(tp)・近点距離(q)が取得できないため、"
                         "距離の推移を計算できません".format(nel.get("fullname") or cid)}
    m1, k1 = _magnitude_params(cid, el)
    try:
        loader, eph = _load()
        ts = loader.timescale()
        t = _resolve_when(when_iso, ts)
    except Exception as e:
        return {"error": "天体暦(JPL DE421)の読み込みに失敗しました（{}）。"
                         "初回はダウンロードが必要です".format(str(e)[:120])}
    if t is None:
        return {"error": "when の形式が不正です (ISO8601: YYYY-MM-DDTHH:MM[:SS])"}
    jd_now = float(t.tt)
    days, before = as_int(days, 180, 1, 3650), as_int(before, 30, 0, 3650)
    n = as_int(samples, 240, 40, 1200)
    jd0, jd1 = jd_now - before, jd_now + days
    series, pos_src = _series(cid, nel, eph, ts, jd0, jd1, n, m1, k1)
    jd0, jd1 = series[0]["jd"], series[-1]["jd"]   # 実際に取れた格子を図の期間にする
    # 粗い格子では最接近・近日点の値と時刻が丸まる（実測: Δ 0.1706 → 真値 0.1672 au）ので、
    # 極値の前後だけ細かい刻みで取り直し、系列へ足してから極値を決める。
    half = max(0.75, (jd1 - jd0) / max(1, len(series) - 1) * 1.5)
    coarse_d = min(range(len(series)), key=lambda i: series[i]["delta_au"])
    coarse_r = min(range(len(series)), key=lambda i: series[i]["r_au"])
    extra, ref_src = [], pos_src
    for ci in (coarse_d, coarse_r):
        add, src2 = _refine_rows(cid, nel, eph, ts, series[ci]["jd"], half, m1, k1)
        extra += [r_ for r_ in add if jd0 <= r_["jd"] <= jd1]
        if src2 == "kepler2body":
            ref_src = "kepler2body"
    series = sorted(series + extra, key=lambda r_: r_["jd"])
    cnt = len(series)
    pos_src = ref_src
    idx_d = min(range(cnt), key=lambda i: series[i]["delta_au"])
    idx_r = min(range(cnt), key=lambda i: series[i]["r_au"])
    idx_e = max(range(cnt), key=lambda i: series[i]["elong_deg"])
    has_mag = any(s["mag"] is not None for s in series)
    idx_m = min(range(cnt), key=lambda i: (series[i]["mag"] if series[i]["mag"] is not None
                                           else 1e9)) if has_mag else None
    ca_jd, ca_d, ca_src = _next_approach(cid, nel, eph, ts, jd_now,
                                         jd_now + max(730.0, float(days)))
    peri_prev, peri_next = _peri_times(nel, jd_now)
    return {"cid": cid, "el": el, "nel": nel, "m1": m1, "k1": k1, "ts": ts,
            "jd_now": jd_now, "now_str": t.utc_strftime("%Y-%m-%d %H:%M UTC"),
            "days": days, "before": before, "samples": cnt, "jd0": jd0, "jd1": jd1,
            "series": series, "idx_d": idx_d, "idx_r": idx_r, "idx_e": idx_e, "idx_m": idx_m,
            "has_mag": has_mag, "edge_d": idx_d in (0, cnt - 1),
            "edge_r": idx_r in (0, cnt - 1), "pos_src": pos_src, "ca_src": ca_src,
            "next_ca": (ca_jd, ca_d), "peri_prev": peri_prev, "peri_next": peri_next}


def _apparition_panels(d: dict) -> List[dict]:
    """3段パネルの仕様（距離／予想光度／太陽離角）。top が上端の値・bottom が下端の値。"""
    series = d["series"]
    dmax = max(max(s["r_au"] for s in series), max(s["delta_au"] for s in series))
    panels = [{"title": "距離", "unit": "AU", "fmt": "{:.2f}", "top": 0.0,
               "bottom": dmax * 1.08,
               "series": [
                   {"key": "r_au", "color": _R_COLOR, "label": "日心距離 r（太陽から）",
                    "short": "r 最小", "extremum": "min"},
                   {"key": "delta_au", "color": _D_COLOR, "label": "地心距離 Δ（地球から）",
                    "short": "Δ 最小", "extremum": "min"}]}]
    if d["has_mag"]:
        mags = [s["mag"] for s in series if s["mag"] is not None]
        panels.append({"title": "予想光度", "unit": "等", "fmt": "{:.1f}",
                       "top": min(mags) - 0.4, "bottom": max(mags) + 0.4,
                       "series": [{"key": "mag", "color": _MAG_COLOR,
                                   "label": "予想光度 m1（値が小さいほど明るい）",
                                   "short": "最明", "extremum": "min"}]})
    panels.append({"title": "太陽離角", "unit": "°", "fmt": "{:.0f}", "top": 180.0,
                   "bottom": 0.0,
                   "series": [{"key": "elong_deg", "color": _ELONG_COLOR,
                               "label": "太陽離角（0°＝太陽と同じ方向＝地球からは見えない）",
                               "short": "最大", "extremum": "max"}]})
    return panels


def _apparition_marks(d: dict) -> List[dict]:
    """縦マーカー（今日／近日点／地球最接近）。"""
    marks = [{"jd": d["jd_now"], "label": "今日", "color": _TODAY_COLOR}]
    peri_jd = d["series"][d["idx_r"]]["jd"]
    # 期間の端で r が最小のときは、それは**近日点ではなく期間内の最小値**（例: ハレー彗星
    # は 2061 年まで太陽へ近づき続けるので、どの期間でも端が最小になる）。誤った天体
    # イベントを書かないよう、内側で最小のときだけ「近日点」と呼ぶ。
    marks.append({"jd": peri_jd,
                  "label": ("近日点 " if not d["edge_r"] else "r 最小 ") + _jt_short(peri_jd),
                  "color": _PERI_COLOR})
    ca_jd = d["series"][d["idx_d"]]["jd"]
    marks.append({"jd": ca_jd,
                  "label": ("地球最接近 " if not d["edge_d"] else "Δ 最小 ") + _jt_short(ca_jd),
                  "color": _CLOSE_COLOR})
    return marks


def _apparition_notes(d: dict, verify: dict, skipped: Sequence[str]) -> List[str]:
    """図の注記を数値から生成する（手書きしない＝図と文がドリフトしない）。"""
    series, nel = d["series"], d["nel"]
    marks = _apparition_marks(d)
    same_day_marks = len({_jt_short(m["jd"]) for m in marks}) < len(marks)
    dmin, rmin, emax = series[d["idx_d"]], series[d["idx_r"]], series[d["idx_e"]]
    if d["edge_d"]:
        ca = d["next_ca"]
        ca_txt = "期間の端で最小になっているため、真の最接近はこの期間の外にある"
        if ca[0] > d["jd1"]:
            ca_txt += "（この先の最小は {} の {:.4f} AU）".format(
                _fmt_jd(ca[0]), ca[1])
    else:
        ca_txt = "期間の内側で最小＝これがこの期間の最接近"
    if d["edge_r"]:
        r_txt = ("期間の端で最小（真の近日点はこの期間の外。前回 {}／次回 {}＝軌道要素から"
                 "の概算なので数年ずれることがある）").format(
                     _fmt_jd(d["peri_prev"]),
                     _fmt_jd(d["peri_next"]) if d["peri_next"] else
                     "非周期（放物線/双曲線）軌道のため次回は無い")
    else:
        r_txt = "期間の内側で最小＝この期間に近日点を通過"
    if d["has_mag"]:
        mags = [s["mag"] for s in series if s["mag"] is not None]
        b = series[d["idx_m"]]
        mag_note = ("予想光度は m1 = M1 + 5·log10(Δ) + K1·log10(r)（M1={}・K1={}／JPL SBDB の"
                    "全光度の式）。期間内の最明は {:.1f} 等（{}）。**実測は式から数等ずれる"
                    "ことがある**（アウトバースト・分裂・活動低下）".format(
                        _f(d["m1"], 2), _f(d["k1"], 2), min(mags), b["utc"] + " UTC"))
    else:
        mag_note = ("JPL SBDB に全光度の式（M1/K1）が無いため、光度パネルは描いていない"
                    "（距離と太陽離角のみ）")
    extra = [
        "この図は**地球から見た見え方の時系列**（横軸＝UTC の日付、縦軸＝距離・光度・太陽離角）。"
        "軌道そのものの形（太陽＝焦点の楕円／閉じない双曲線）は view=\"comet_orbit\" の図で示す",
        "図の期間は {} 〜 {}（{} 日間・{} 点。現在 {}）".format(
            _fmt_jd(d["jd0"]), _fmt_jd(d["jd1"]),
            d["days"] + d["before"], d["samples"], d["now_str"]),
        ("距離は**彗星核と太陽・地球中心の直線距離**（AU）。位置は JPL Horizons の"
         "**N 体解**（ICRF）で、Δ は地心距離（地球中心から彗星核まで）。光行差は入れていない"
         if d.get("pos_src") == "horizons" else
         "距離は**彗星核と太陽・地球中心の直線距離**（AU）。位置は SBDB の軌道要素からの"
         "**ケプラー2体近似**（Horizons の取得に失敗したときの退避経路）で座標系は黄道 J2000。"
         "実測: JPL の N 体解と最大 0.025 au（372万 km）ずれ、地球最接近の時刻も 1 日以上"
         "ずれる（169P/NEAT・2026年8〜9月）"),
        "この期間の最小地心距離 Δ＝{:.4f} AU（約 {:.0f} 万 km）は {} UTC。{}".format(
            dmin["delta_au"], dmin["delta_au"] * _AU_KM / 1e4, dmin["utc"], ca_txt),
        "この期間の最小日心距離 r＝{:.4f} AU は {} UTC（近日点）。{}".format(
            rmin["r_au"], rmin["utc"], r_txt),
        mag_note,
        "太陽離角の最大は {:.0f}°（{} UTC{}）。**離角が小さいほど薄明の中に沈み、地球から"
        "観測しにくい**（目安として 30° 未満では事実上観測できない）".format(
            emax["elong_deg"], emax["utc"],
            "・期間の端なので実際の最大は期間の外の可能性がある"
            if d["idx_e"] in (0, len(series) - 1) else ""),
        "縦線のマーカー: " + "／".join(
            (m["label"] if _jt_short(m["jd"]) in m["label"] else
             "{}（{}）".format(m["label"], _jt_short(m["jd"]))) for m in marks) + (
            "。**同じ日付のマーカーは縦線が重なって描かれる**" if same_day_marks else ""),
        "縦軸は線形。**光度パネルだけ向きが逆で、値が小さい（明るい）方が上**",
        "描いた極値の座標に曲線色の画素があるかを画素から測り直して自己検証している"
        "（{} 点を検査・最大ずれ {} px・許容 {} px）".format(
            verify["checked_points"], verify["worst_px_error"], verify["tolerance_px"]),
        "曲線に重なる位置にはラベルを描いていない" + (
            "。置けなかったラベル: " + " / ".join(skipped) if skipped else "（この図では全て配置できた）"),
        "出典: " + _src_of(d),
    ]
    return figure_notes(extra=extra)


def _conic_note(c) -> str:
    """軌道の円錐曲線を1行で説明する（時間軸の図では形を描かないので、数値で補う）。"""
    if c.kind == "ellipse":
        per = "・周期 {:.0f} 日".format(c.period) if c.period else ""
        return ("軌道は**閉じた楕円**（離心率 e={:.6f}・近日点 q={:.4f} AU・"
                "遠日点 Q={:.4f} AU{}）。この図は時間軸のグラフなので、"
                "軌道の形（太陽＝焦点）は view=\"comet_orbit\" の図で示す".format(
                    c.e, c.q, c.apo, per))
    if c.kind == "parabola":
        return ("軌道は放物線に近く（e={:.7f}）、**閉じていない＝次回の接近は無い**。"
                "この図は時間軸のグラフで、軌道の形は描いていない".format(c.e))
    return ("軌道は**閉じない双曲線**（e={:.7f}・半長軸 a={:.4f} AU は負＝双曲線）。"
            "**遠日点は存在せず**、1回だけの接近で二度と戻らない。この図は時間軸のグラフで、"
            "軌道の形は描いていない".format(c.e, c.a if c.a is not None else 0.0))


def _apparition_content(d: dict, fig: dict, image_path, emax_note: str) -> List[str]:
    """content（人間向け）の行を組み立てる。メディアより先にリンクを出す（規約13）。"""
    series, nel = d["series"], d["nel"]
    dmin, rmin, emax = series[d["idx_d"]], series[d["idx_r"]], series[d["idx_e"]]
    span = "{} 〜 {}".format(_jd_to_utc(d["jd0"]).strftime("%Y-%m-%d"),
                             _jd_to_utc(d["jd1"]).strftime("%Y-%m-%d"))
    lines = [media_link_line("生成した画像を開く（{} の見え方 {}）".format(nel["fullname"], span),
                             path=image_path, kind="figure"),
             "### ☄️ {} の見え方（{}）".format(nel["fullname"], span)]
    if emax_note:
        lines.append("⚠️ " + emax_note)
    lines += ["", "| 項目 | 値 | 日時（UTC） |", "|---|---|---|",
              "| 最小地心距離 Δ | {:.4f} AU ＝ {:.0f} 万 km（光で約 {:.1f} 分） | {} |".format(
                  dmin["delta_au"], dmin["delta_au"] * _AU_KM / 1e4,
                  dmin["delta_au"] * 499.005 / 60.0, dmin["utc"]),
              "| 最小日心距離 r（近日点） | {:.4f} AU | {} |".format(rmin["r_au"], rmin["utc"])]
    if d["has_mag"]:
        b = series[d["idx_m"]]
        lines.append("| 最明の予想光度 | {:.1f} 等 | {} |".format(b["mag"], b["utc"]))
    lines.append("| 太陽離角の最大 | {:.0f}° | {} |".format(emax["elong_deg"], emax["utc"]))
    lines += ["", "期間内の推移（{} 点のサンプルから抜粋）:".format(d["samples"]), "",
              "| 日付(UTC) | r [AU] | Δ [AU] | 予想光度 | 太陽離角 |", "|---|---|---|---|---|"]
    for s in series[::max(1, len(series) // 12)]:
        lines.append("| {} | {:.3f} | {:.3f} | {} | {:.0f}° |".format(
            s["utc"][:10], s["r_au"], s["delta_au"],
            "-" if s["mag"] is None else "{:.1f}".format(s["mag"]), s["elong_deg"]))
    lines += ["", figure_text_block(fig),
              "画像は上に表示（base64 PNG）。出典: " + _SRC_HZ]
    return lines


def comet_apparition_result(name: str, when_iso=None, days=180, before=30,
                            samples=240) -> CallToolResult:
    '''彗星の「見え方」を時系列で描いた図を返す（地心距離・日心距離・予想光度・太陽離角）。

    例:「彗星はいつ地球に近づく？」「169P の光度の推移は？」「あと何日で最接近？」
    「この彗星は明るくなる？」「見ごろはいつ？」「彗星の増光と距離をグラフで」

    横軸＝UTC の日付、縦軸＝3段パネル（距離 r/Δ・予想光度 m1・太陽離角）。近日点・地球
    最接近・今日を縦線で示し、期間内の極値を数値ラベルで添えます。距離は JPL SBDB の
    軌道要素からのケプラー2体伝播（楕円／放物線／双曲線）と JPL DE421 + Skyfield の
    地球位置から算出（認証不要）。予想光度は SBDB の全光度の式 m1 = M1 + 5·log10(Δ)
    + K1·log10(r) で、**実測とは数等ずれることがあります**。

    Args:
        name: 彗星（例 \"169P\"/\"169P/NEAT\"、\"ハレー彗星\"、\"エンケ彗星\"、\"C/2023 A3\"）。
        when_iso: 起点時刻 ISO8601（省略で現在）。
        days: 今日から先の表示日数（1〜3650、既定 180）。
        before: 今日より前を何日ぶん描くか（0〜3650、既定 30。直前に過ぎた近日点や
            最接近を見えるようにするため）。
        samples: サンプル数（40〜1200、既定 240）。

    `figure.notes` は**要約・言い換えせず、そのまま引用してください**（図と数値の対応は
    注記が唯一の説明です）。インライン画像を表示できないハーネス（CLI系・Android系の
    codex / opencode など）向けに、content の先頭へ「🖼️ [生成した画像を開く（…）]
    (file:///…)」というアイコン付きリンクを必ず出します（同じパスを
    structuredContent.image_path にも入れます）。回答時はこのリンクをそのまま提示してください。
    '''
    try:
        d = _apparition_data(name, when_iso, days, before, samples)
        if d.get("error"):
            msg = d["error"] + (("\n" + d["hint"]) if d.get("hint") else "")
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": d["error"], "query": str(name)})
        nel, series = d["nel"], d["series"]
        conic = conic_from_elements(a=(nel["q"] / (1.0 - nel["e"]) if nel["e"] != 1.0 else None),
                                    e=nel["e"], q=nel["q"],
                                    period=nel.get("period_days"),
                                    incl_deg=math.degrees(nel["i"]))
        panels = _apparition_panels(d)
        span = "{} 〜 {}".format(_jd_to_utc(d["jd0"]).strftime("%Y-%m-%d"),
                                 _jd_to_utc(d["jd1"]).strftime("%Y-%m-%d"))
        title = "{} の見え方（{}）".format(nel["fullname"], span)
        mag_txt = ("光度式 m1 = M1 + 5·log10(Δ) + K1·log10(r)（M1={}・K1={}・JPL SBDB）".format(
            _f(d["m1"], 2), _f(d["k1"], 2)) if d["has_mag"] else
            "光度式（M1/K1）が SBDB に無いため光度パネルなし")
        info = {"jd0": d["jd0"], "jd1": d["jd1"], "title": title,
                "sub": "地心距離 Δ（地球から・シアン）／日心距離 r（太陽から・黄）／"
                       "予想光度（橙・上下反転）／太陽離角（緑）",
                "sub2": "現在 {}｜{}".format(d["now_str"], mag_txt),
                "panels": panels, "marks": _apparition_marks(d),
                "footer": "出典: " + _src_of(d) + "（横軸は UTC）"}
        draw = _draw_chart(series, info)
        img = draw["img"]
        ease = series[d["idx_e"]]
        emax_note = ("" if ease["elong_deg"] >= 30.0 else
                     "この期間の太陽離角は最大でも {:.0f}°（{}）で、**30° 未満＝薄明の中に"
                     "沈み地球からは事実上観測できない**期間です".format(
                         ease["elong_deg"], ease["utc"]))
        verify = _verify_chart(img, draw["checks"], draw["label_boxes"],
                               [c["color"] for p in panels for c in p["series"]]
                               + [m["color"] for m in _apparition_marks(d)],
                               marker_pixels=draw["marker_pixels"],
                               skipped=draw["skipped_labels"],
                               mark_same_day=draw["mark_same_day"])
        notes = [n for n in _apparition_notes(d, verify, draw["skipped_labels"])
                 if n] + [_conic_note(conic)]
        dmin, rmin = series[d["idx_d"]], series[d["idx_r"]]
        fig = figure_payload(
            kind="comet_apparition", title=title,
            view=view_spec("earth_centred", "time_series",
                           "横軸＝UTC の日付、縦軸＝距離/光度/太陽離角の時系列図",
                           why="観測の可否は軌道の形ではなく「地球からの距離と太陽からの"
                               "離角の時間変化」で決まるため、時間軸で描いている"),
            scale=scale_spec("linear", to_scale=True, unit="AU",
                             exaggerated=["縦軸の縮尺はパネルごとに独立",
                                          "光度パネルは値が小さい（明るい）方が上"]),
            conic=conic, markers=_apparition_marks(d), notes=notes,
            caption="{}。最小地心距離 {:.4f} AU（{}）、最小日心距離 {:.4f} AU（{}）{}".format(
                nel["fullname"], dmin["delta_au"], dmin["utc"], rmin["r_au"], rmin["utc"],
                "、最明 {:.1f} 等（{}）".format(series[d["idx_m"]]["mag"],
                                              series[d["idx_m"]]["utc"]) if d["has_mag"] else ""),
            verify=verify)
        png = _png_bytes(img)
        out_path = save_output(png, "solar_system_comet_apparition", "png")
        lines = _apparition_content(d, fig, out_path, emax_note)
        step = max(1, len(series) // 60)
        sc = {"comet": nel["fullname"], "id": d["cid"], "source": _src_of(d),
              "positions_source": d["pos_src"],
              "window": {"from": _jd_to_utc(d["jd0"]).strftime("%Y-%m-%d"),
                         "to": _jd_to_utc(d["jd1"]).strftime("%Y-%m-%d"),
                         "days": d["days"], "days_before": d["before"], "samples": d["samples"],
                         "now_utc": d["now_str"]},
              "elements": {"e": nel["e"], "q_au": nel["q"], "i_deg": math.degrees(nel["i"]),
                           "tp_jd": nel["tp"], "period_days": nel.get("period_days")},
              "brightness_model": ({"formula": "m1 = M1 + 5*log10(delta) + K1*log10(r)",
                                    "M1": d["m1"], "K1": d["k1"], "source": "JPL SBDB"}
                                   if d["has_mag"] else None),
              "features": {
                  "closest_approach": {"utc": dmin["utc"], "delta_au": dmin["delta_au"],
                                       "delta_km": dmin["delta_au"] * _AU_KM,
                                       "inside_window": not d["edge_d"]},
                  "perihelion": {"utc": rmin["utc"], "r_au": rmin["r_au"],
                                 "inside_window": not d["edge_r"]},
                  "brightest": ({"utc": series[d["idx_m"]]["utc"],
                                 "mag": series[d["idx_m"]]["mag"]} if d["has_mag"] else None),
                  "max_elongation_deg": ease["elong_deg"],
                  "next_close_approach": {"utc": _fmt_jd(d["next_ca"][0]),
                                          "delta_au": d["next_ca"][1]},
                  "next_perihelion_utc": _fmt_jd(d["peri_next"]) if d["peri_next"] else None},
              "series": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()}
                         for s in series[::step]],
              "figure": fig, "image_path": out_path, "verify": verify}
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines)),
                     ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                                  mimeType="image/png",
                                  altText="{} の見え方チャート".format(nel["fullname"]))],
            structuredContent=sc)
    except Exception as e:                     # 例外をツール外へ出さない（規約1）
        msg = "彗星の見え方チャートの作成に失敗しました: {}: {}".format(type(e).__name__, str(e)[:180])
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": msg, "query": str(name)})

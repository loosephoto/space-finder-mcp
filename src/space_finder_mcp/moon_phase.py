"""月齢マップ（月の満ち欠けを格子状/時系列に並べた図, 認証不要）。

日食の時系列パネル（solar_eclipse.py）の描画ルーチンを応用した姉妹ツール。
日食が「太陽円盤を月が欠く」過程を描くのに対し、こちらは同じ幾何（観測地から
見た太陽と月の視位置・位置角 PA）を使って「月円盤が太陽にどこから照らされるか」
＝輝面の向きと照度を描き、それを1か月ぶんの格子（月齢マップ）または
1朔望月ぶんの時系列パネルに並べる。

- 天体位置・月齢・照度は JPL DE421 暦表 + Skyfield（ローカル計算・認証不要）。
- 月齢 = 直前の朔（新月）からの経過日数。照度 = 円盤の輝面の割合（満月=100%）。
- 輝面の位置角（PA, 天の北から東回り）で太陽側を求め、円盤を正しい向きに欠かせる。
  欠けの境界（明暗境界線＝ターミネータ）は半楕円で、その半短軸は R·|2k−1|。
  この式は照度 k と厳密に整合する（円盤の輝面の面積比 = (1+cos i)/2 = k）。
- 描いた画素で明暗境界線の位置を測り直し、申告した照度と突き合わせて自己検証する
  （輝面の向き＝位置角の軸上を走査するので、向きが違えば一致しない）。

返却: content に base64 画像(ImageContent)、structuredContent に日別の月齢・照度・
月相と figure/1（注記・自己検証）。
出典: JPL DE421 + Skyfield。
"""
from __future__ import annotations

import base64
import datetime
import math
import random
import re
from typing import Optional

from mcp.types import CallToolResult, ImageContent, TextContent

from .img_common import (as_image, body_rgb, figure_notes, figure_payload,
                         figure_text_block, load_font, media_link_line,
                         save_output, scale_spec, view_spec)
from .input_utils import as_int
# 観測地の解決とタイムゾーン・暦のロードは日食ツールと同じ実装を共有する
# （同じ処理を書き写すと、片方だけ直して食い違う）。
from .solar_eclipse import _load, _local_tz, _resolve_place, _tz_label

# 月の色は img_common.BODY_COLORS の単一の出典を使う（図ごとに色が違わないように）
_MOON_LIT = body_rgb("月")            # 輝面
_MOON_MARIA = (196, 198, 206)         # 月の海（模式的な斑。実写ではない）
_MOON_DARK = (24, 26, 36)             # 夜側（地球照は描かない＝模式図）
_MOON_RIM = (72, 80, 104)
_BG = (7, 9, 22)
_ACCENT = (255, 214, 120)             # 朔・望の強調色
_WEEKDAYS = ("日", "月", "火", "水", "木", "金", "土")
# 月相の呼称（添字 = 月齢を四捨五入した 0〜29）。(短縮名, 正式名)
_AGE_NAMES = (
    ("新月", "新月（朔）"), ("二日月", "二日月"), ("三日月", "三日月"),
    ("三日月過ぎ", "三日月過ぎ"), ("上弦前", "上弦前の月"), ("上弦前", "上弦前の月"),
    ("上弦前", "上弦前の月"), ("上弦", "上弦の月（七日月）"), ("上弦過ぎ", "上弦の月を過ぎた月"),
    ("十日月", "十日月"), ("十日月", "十日月"), ("十一日月", "十一日月"),
    ("十二日月", "十二日月"), ("十三夜", "十三夜月"), ("小望月", "小望月（十四日月）"),
    ("満月", "満月（望）"), ("十六夜", "十六夜の月"), ("立待月", "立待月"),
    ("居待月", "居待月"), ("寝待月", "寝待月"), ("更待月", "更待月"),
    ("二十日月", "二十日月"), ("二十三夜前", "二十三夜前の月"), ("下弦", "下弦の月（二十三夜）"),
    ("下弦過ぎ", "下弦を過ぎた月"), ("有明月", "有明月"), ("有明月", "有明月"),
    ("有明月", "有明月"), ("晦日月", "晦日月"), ("晦日月", "晦日月（三十日月）"),
)
# パネル/セルの寸法（描画と自己検証が同じ値を使う＝検証が別レイアウトを測らない）
_CAL_CELL_W, _CAL_CELL_H, _CAL_HDR, _CAL_WD, _CAL_R = 208, 220, 126, 42, 46
_LUN_PANEL_W, _LUN_H, _LUN_HDR, _LUN_R = 372, 608, 126, 116


def _parse_date(value: str):
    """'2026-09' / '2026-09-16' / '2026年9月16日' を (年, 月, 日 or None) にする。

    解釈できない場合は None を返す（呼び出し側でエラーにする）。
    """
    s = re.sub(r"[年月日]", "-", str(value).strip())
    s = re.sub(r"日$", "", s)
    parts = [p for p in re.split(r"[-/]", s) if p.strip() != ""]
    if not parts or not re.fullmatch(r"\d{4}", parts[0].strip()):
        return None
    y = int(parts[0])
    mo = None
    d = None
    try:
        if len(parts) >= 2:
            mo = int(parts[1])
        if len(parts) >= 3:
            d = int(parts[2])
    except ValueError:
        return None
    if mo is not None and not (1 <= mo <= 12):
        return None
    if d is not None and not (1 <= d <= 31):
        return None
    return y, mo, d


def _phase_names(age: float):
    """月齢から (短縮名, 正式名) を返す。"""
    idx = int(round(float(age))) % 30
    return _AGE_NAMES[idx]


def _moon_events(ts, eph, t0, t1):
    """[t0, t1] の月相イベント [(Time, 0=朔/1=上弦/2=望/3=下弦)] を時刻順に返す。"""
    from skyfield import almanac
    try:
        times, phases = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    except Exception:
        return []
    return [(tt, int(p)) for tt, p in zip(times, phases)]


def _bright_limb_pa(sun, moon):
    """月から見た太陽の位置角（＝輝面の向き）を天の北=0 から東回りで返す（度）。"""
    s_ra, s_dec, _ = sun.radec()
    m_ra, m_dec, _ = moon.radec()
    dra = math.radians((s_ra.hours - m_ra.hours) * 15.0)
    sd = math.radians(s_dec.degrees)
    md = math.radians(m_dec.degrees)
    pa = math.atan2(math.sin(dra),
                    math.cos(md) * math.tan(sd) - math.sin(md) * math.cos(dra))
    return math.degrees(pa) % 360.0


def _entry(eph, ts, tt, tz, new_tt, events_by_date):
    """ある瞬間の月齢・照度・輝面位置角を1件の dict にする。"""
    from skyfield import almanac
    # 照度は Skyfield の実装（月の視位置から算出）をそのまま使う
    illum = float(almanac.fraction_illuminated(eph, "moon", tt))
    earth = eph["earth"]
    sun = earth.at(tt).observe(eph["sun"]).apparent()
    moon = earth.at(tt).observe(eph["moon"]).apparent()
    pa = _bright_limb_pa(sun, moon)
    # 月齢 = 直前の朔（新月）からの経過日数（TT の日数差がそのまま日齢になる）
    age = float(tt.tt - new_tt.tt)
    short, full = _phase_names(age)
    local = tt.utc_datetime() + datetime.timedelta(hours=float(tz or 0.0))
    d = local.date()
    return {
        "date": d,
        "date_str": "{}/{}".format(d.month, d.day),
        "time_str": local.strftime("%H:%M"),
        "weekday": _WEEKDAYS[(d.weekday() + 1) % 7],
        "moon_age": round(age, 2),
        "illumination": round(illum, 4),
        "illum_pct": int(round(illum * 100)),
        "bright_limb_pa_deg": round(pa, 1),
        "phase_short": short,
        "phase_ja": full,
        "events": [n for (n, _t) in events_by_date.get(d.isoformat(), [])],
        "event_times": [t for (_n, t) in events_by_date.get(d.isoformat(), [])],
    }


def _terminator_points(cx, cy, R, illum, pa_deg, n=48):
    """輝面（太陽に照らされた部分）の輪郭点列を返す。

    天球面の見え方（天の北=上・東=左）で、輝面側の半円 + 明暗境界線（半楕円）を
    つなぐ。半楕円の半短軸は R·|2k−1| で、照度 k と面積比が厳密に一致する
    （輝面 = 半円 ± 半楕円 → 面積比 = (1 + cos i)/2 = k）。
    """
    k = min(1.0, max(0.0, float(illum)))
    c = 2.0 * k - 1.0
    s = 1.0 if c >= 0.0 else -1.0
    th = math.radians(float(pa_deg))
    ux, uy = -math.sin(th), -math.cos(th)      # 輝面の向き（画面座標: y は下向き）
    vx, vy = math.cos(th), -math.sin(th)
    pts = []
    for i in range(n + 1):
        a = -math.pi / 2.0 + math.pi * i / n
        pts.append((cx + R * (math.cos(a) * ux + math.sin(a) * vx),
                    cy + R * (math.cos(a) * uy + math.sin(a) * vy)))
    for i in range(n + 1):
        t = math.pi / 2.0 - math.pi * i / n
        pts.append((cx - s * abs(c) * R * math.cos(t) * ux + R * math.sin(t) * vx,
                    cy - s * abs(c) * R * math.cos(t) * uy + R * math.sin(t) * vy))
    return pts


def _draw_moon(img, draw, cx, cy, R, illum, pa_deg, seed=0):
    """月円盤を満ち欠けどおりに1つ描く（輝面の向き = 位置角 pa_deg）。"""
    from PIL import Image, ImageDraw
    k = min(1.0, max(0.0, float(illum)))
    pts = _terminator_points(cx, cy, R, k, pa_deg)
    draw.ellipse([cx - R, cy - R, cx + R, cy + R], fill=_MOON_DARK,
                 outline=_MOON_RIM, width=2)
    if k <= 0.0005:
        return
    # 輝面は一時レイヤー+マスクで貼る。直接多角形を描くと、月の海の斑が
    # 明暗境界線の外（夜側）へはみ出して「照っているのに暗い」図になる。
    x0, y0 = int(cx - R) - 2, int(cy - R) - 2
    size = int(2 * R) + 5
    tex = Image.new("RGB", (size, size), _MOON_LIT)
    td = ImageDraw.Draw(tex)
    rnd = random.Random(int(seed))
    for _ in range(5):
        ang = rnd.uniform(0.0, 2.0 * math.pi)
        rr = rnd.uniform(0.0, R * 0.5)
        px_ = size / 2.0 + rr * math.cos(ang)
        py_ = size / 2.0 + rr * math.sin(ang)
        dd = rnd.uniform(R * 0.16, R * 0.30)
        td.ellipse([px_ - dd, py_ - dd, px_ + dd, py_ + dd], fill=_MOON_MARIA)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for (x, y) in pts], fill=255)
    img.paste(tex, (x0, y0), mask)


def _star_field(draw, w, h, y0, seed=20260916):
    """背景の星を撒く（文字より先に描く＝文字が星に隠れない）。"""
    rnd = random.Random(seed)
    for _ in range(int(w * (h - y0) / 4200.0)):
        sx, sy = rnd.randint(0, max(0, w - 1)), rnd.randint(y0, max(y0, h - 1))
        br = rnd.randint(140, 235)
        draw.point((sx, sy), fill=(br, br, br + 10))


# ---------- レイアウト（描画と自己検証が共有する唯一の出典） ----------
def _calendar_geometry(entries):
    """月齢カレンダーのセル座標を返す（verify も同じ座標を使って測る）。"""
    wd0 = (entries[0]["date"].weekday() + 1) % 7      # 左端の曜日（日曜=0）
    rows = max(1, int(math.ceil((wd0 + len(entries)) / 7.0)))
    cells = []
    for i, e in enumerate(entries):
        r_, c_ = divmod(wd0 + i, 7)
        cells.append({"index": i, "row": r_, "col": c_,
                      "cx": c_ * _CAL_CELL_W + _CAL_CELL_W // 2,
                      "cy": _CAL_HDR + _CAL_WD + r_ * _CAL_CELL_H + 98,
                      "illum": e["illumination"],
                      "pa": e["bright_limb_pa_deg"]})
    return cells, (7 * _CAL_CELL_W, _CAL_HDR + _CAL_WD + rows * _CAL_CELL_H), rows, wd0


def _lunation_geometry(entries):
    """朔望月パネルのセル座標を返す（verify も同じ座標を使って測る）。"""
    cells = []
    for i, e in enumerate(entries):
        cells.append({"index": i, "col": i,
                      "cx": i * _LUN_PANEL_W + _LUN_PANEL_W // 2,
                      "cy": _LUN_HDR + 196, "illum": e["illumination"],
                      "pa": e["bright_limb_pa_deg"]})
    return cells, (_LUN_PANEL_W * len(entries), _LUN_H)


# ---------- 描画 ----------
def _cell_text(dd, entries, cell, x0, y0):
    """セル/パネルの文字（月齢・照度・月相）を描く。"""
    e = entries[cell["index"]]
    # 時刻はイベントの実際の時刻を使う（各日の代表時刻＝正午ではない）
    pairs = list(zip(e["events"], e.get("event_times") or [e["time_str"]] * len(e["events"])))
    ev = "・".join("{} {}".format(n_, t_) for (n_, t_) in pairs)
    col = _ACCENT if e["events"] else (238, 242, 252)
    dd.text((x0, y0 + 150), "月齢 {:.1f} ・ 照度 {}%".format(e["moon_age"], e["illum_pct"]),
            font=load_font(16), fill=(205, 215, 240))
    dd.text((x0, y0 + 174), e["phase_short"], font=load_font(19, True), fill=col)
    if ev:
        dd.text((x0, y0 + 200), ev, font=load_font(15, True), fill=_ACCENT)
    elif e.get("event_label"):
        dd.text((x0, y0 + 200), e["event_label"], font=load_font(15, True), fill=_ACCENT)


def _render_calendar(entries, meta, cells, size):
    """1か月ぶんの日別月相を格子（月齢マップ）に並べる。"""
    from PIL import Image, ImageDraw
    W, H = size
    img = Image.new("RGB", (W, H), _BG)
    dd = ImageDraw.Draw(img)
    _star_field(dd, W, H, _CAL_HDR + _CAL_WD)
    # 第1層: グラフィック（月円盤・格子）。文字は最後に描いて隠れないようにする。
    for cell in cells:
        x0 = cell["col"] * _CAL_CELL_W
        y0 = _CAL_HDR + _CAL_WD + cell["row"] * _CAL_CELL_H
        dd.rectangle([x0 + 4, y0 + 4, x0 + _CAL_CELL_W - 4, y0 + _CAL_CELL_H - 4],
                     outline=(40, 50, 78), width=2)
        e = entries[cell["index"]]
        _draw_moon(img, dd, cell["cx"], cell["cy"], _CAL_R,
                   e["illumination"], e["bright_limb_pa_deg"], seed=cell["index"])
    # 第2層: 文字（ヘッダー・曜日・各セルの数値）
    dd.rectangle([0, 0, W, _CAL_HDR], fill=(0, 0, 0))
    dd.text((26, 16), meta["title"], font=load_font(30, True), fill=(255, 255, 255))
    dd.text((26, 62), meta["sub1"], font=load_font(18), fill=(205, 215, 240))
    dd.text((26, 92), meta["sub2"], font=load_font(18), fill=_ACCENT)
    for c_ in range(7):
        dd.text((c_ * _CAL_CELL_W + _CAL_CELL_W // 2 - 11, _CAL_HDR + 9), _WEEKDAYS[c_],
                font=load_font(20, True), fill=(170, 195, 235))
    for cell in cells:
        x0 = cell["col"] * _CAL_CELL_W
        y0 = _CAL_HDR + _CAL_WD + cell["row"] * _CAL_CELL_H
        e = entries[cell["index"]]
        dd.text((x0 + 14, y0 + 8), str(e["date"].day), font=load_font(24, True),
                fill=(232, 238, 252))
        dd.text((x0 + 52, y0 + 16), e["weekday"], font=load_font(15), fill=(150, 170, 205))
        _cell_text(dd, entries, cell, x0 + 14, y0)
    return img


def _render_lunation(entries, meta, cells, size):
    """1朔望月ぶんの月相を時系列パネルに並べる（日食パネルと同じ構成）。"""
    from PIL import Image, ImageDraw
    W, H = size
    img = Image.new("RGB", (W, H), _BG)
    dd = ImageDraw.Draw(img)
    _star_field(dd, W, H, _LUN_HDR)
    for cell in cells:
        x0 = cell["col"] * _LUN_PANEL_W
        if cell["col"] > 0:
            dd.line([x0, _LUN_HDR + 6, x0, H], fill=(44, 54, 84), width=2)
        e = entries[cell["index"]]
        _draw_moon(img, dd, cell["cx"], cell["cy"], _LUN_R,
                   e["illumination"], e["bright_limb_pa_deg"], seed=100 + cell["index"])
    dd.rectangle([0, 0, W, _LUN_HDR], fill=(0, 0, 0))
    dd.text((26, 16), meta["title"], font=load_font(30, True), fill=(255, 255, 255))
    dd.text((26, 62), meta["sub1"], font=load_font(18), fill=(205, 215, 240))
    dd.text((26, 92), meta["sub2"], font=load_font(18), fill=_ACCENT)
    for cell in cells:
        x0 = cell["col"] * _LUN_PANEL_W
        e = entries[cell["index"]]
        by0 = cell["cy"] + _LUN_R + 30
        dd.rectangle([x0 + 14, by0, x0 + _LUN_PANEL_W - 14, by0 + 140], fill=(0, 0, 0))
        head = "{}枚目".format(cell["index"] + 1)
        if e["events"]:
            head += "・" + "・".join(e["events"])
        elif e.get("event_label"):
            head += "・" + e["event_label"]
        dd.text((x0 + 24, by0 + 8), head, font=load_font(17, True), fill=(170, 195, 235))
        dd.text((x0 + 24, by0 + 36), "{} {} ({})".format(e["date_str"], e["time_str"], e["weekday"]),
                font=load_font(19, True), fill=(255, 255, 255))
        dd.text((x0 + 24, by0 + 66), "月齢 {:.1f} ・ 照度 {}%".format(e["moon_age"], e["illum_pct"]),
                font=load_font(18), fill=(205, 215, 240))
        dd.text((x0 + 24, by0 + 94), e["phase_ja"], font=load_font(19, True), fill=_ACCENT)
        dd.text((x0 + 24, by0 + 120), "輝面の位置角 {:.0f}°（天の北基準）".format(e["bright_limb_pa_deg"]),
                font=load_font(15), fill=(160, 180, 215))
    return img


# ---------- 描画の自己検証（figure/1 の verify 用） ----------
def _axis_illumination(px, cx, cy, R, pa_deg):
    """輝面の向き（位置角 PA）の軸上で明暗境界線の位置を測り、照度を復元する。

    照度 k の円盤では、輝面は軸方向に -R(2k-1) から +R まで（明部の長さ L = 2kR）。
    したがって「縁から内側へ走査して最後に明るかった位置」t_in から
    k = (R - t_in) / (2R) が厳密に戻る。円盤の外周（縁取り）やアンチエイリアスを
    使わない内側の境界だけを測るので、面積比を数える方式のような縮尺の偏りが出ない。
    走査軸は申告した PA 方向なので、向き（満ち欠けの方向）が違えば値が合わない。
    """
    th = math.radians(float(pa_deg))
    ux, uy = -math.sin(th), -math.cos(th)
    t_in = None
    t = float(R) - 0.5
    while t >= -float(R) + 0.5:
        x = int(round(cx + ux * t))
        y = int(round(cy + uy * t))
        r, g, b = px[x, y]
        if (r + g + b) / 3.0 >= 120.0:
            t_in = t
        elif t_in is not None:
            break
        t -= 0.5
    if t_in is None:
        return 0.0, 0
    return max(0.0, min(1.0, (float(R) - t_in) / (2.0 * float(R)))), int(R - t_in)


def _verify_moon_disks(img, cells, R, tol=0.05):
    """各円盤の明暗境界線の位置を画素から測り、申告した照度と突き合わせる。

    照度 k の月円盤は、輝面の向き（PA）の軸上で縁から内側へ 2kR の幅で明るい。
    したがって縁から走査して最後に明るかった位置が境界線で、そこから照度が戻る。
    この検査は「月を描き忘れた（全面が暗い）」図（k=0 として落ちる）と、
    「輝面の向きが PA と違う」図（走査軸が明部を通らず落ちる）の両方を検出する。
    """
    px_img = as_image(img).convert("RGB")
    px = px_img.load()
    w, h = px_img.size
    per = []
    for c in cells:
        cx, cy = float(c["cx"]), float(c["cy"])
        # 円盤の外へはみ出す走査はしない（隣のセルや文字を拾わない）
        cx = max(float(R), min(float(w) - float(R), cx))
        cy = max(float(R), min(float(h) - float(R), cy))
        frac, axis_px = _axis_illumination(px, cx, cy, R, c.get("pa", 0.0))
        rep = float(c["illum"])
        per.append({"cell": c["index"], "axis_bright_px": axis_px,
                    "illumination_from_pixels": round(frac, 4),
                    "illumination_reported": round(rep, 4),
                    "error": round(abs(frac - rep), 4)})
    worst = max((p["error"] for p in per), default=1.0)
    return {"ok": bool(per) and worst <= tol, "cells": len(per),
            "worst_error": round(worst, 4), "tolerance": tol, "per_cell": per}


# ---------- ツール ----------
_EVENT_NAMES = {0: "新月", 1: "上弦", 2: "満月", 3: "下弦"}


def _events_by_local_date(ts, eph, t_start_jd, t_end_jd, tz):
    """[t_start_jd, t_end_jd] の月相イベントを「現地日付 → [(名前, 時刻)]」にまとめる。"""
    by_date = {}
    events = []
    for tt, phase in _moon_events(ts, eph, ts.tt_jd(t_start_jd), ts.tt_jd(t_end_jd)):
        local = tt.utc_datetime() + datetime.timedelta(hours=float(tz or 0.0))
        name = _EVENT_NAMES.get(phase, "月相")
        by_date.setdefault(local.date().isoformat(), []).append((name, local.strftime("%H:%M")))
        events.append({"phase": name, "jd": float(tt.tt),
                       "datetime_local": local.strftime("%Y-%m-%d %H:%M")})
    return by_date, events


def _fmt_local(tt, tz):
    """時刻を現地時間の 'M/D HH:MM' 表記にする。"""
    local = tt.utc_datetime() + datetime.timedelta(hours=float(tz or 0.0))
    return "{}/{} {:02d}:{:02d}".format(local.month, local.day, local.hour, local.minute)


def _iso_to_jd(ts, dt_utc):
    """UTC の datetime を TT ユリウス日に変換する。"""
    return ts.utc(dt_utc.year, dt_utc.month, dt_utc.day,
                  dt_utc.hour, dt_utc.minute, dt_utc.second).tt


def moon_phase_map(date: Optional[str] = None, place: Optional[str] = None,
                   lat: Optional[float] = None, lon: Optional[float] = None,
                   layout: str = "calendar",
                   days: Optional[int] = None) -> CallToolResult:
    """月齢マップ（月の満ち欠けを1か月ぶん並べた図）を返す（認証不要・ローカル計算）。

    例:「今月の月齢マップを見せて」「2026年9月の月相カレンダー」「9月の満月はいつ？」

    日食の時系列パネル（solar_eclipse_series）と同じ幾何計算を使い、観測地から見た
    太陽と月の実位置から「輝面の向き（位置角 PA）」と照度を求め、月円盤を正しい向きに
    欠けさせて描きます（月齢から向きを決め打ちしない）。月齢は直前の朔（新月）からの
    経過日数、照度は円盤の輝面の割合です。描いた画素から輝面の面積比を測り直し、
    申告した照度と突き合わせて自己検証します（figure.verify）。

    layout="calendar"（既定）は指定した月の日別セル（日月火水木金土の格子）、
    layout="lunation" は1朔望月（朔→朔）を等間隔の時系列パネルで返します。

    月齢・照度は地球規模の見え方なので観測地には依存しませんが、日付・時刻・曜日は
    現地時間（緯度経度から取得した UTC オフセット）で表示します。

    Args:
        date: 年月（"2026-09" / "2026年9月"）または年月日（"2026-09-16"）。省略時は現在の月。
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        layout: "calendar"（既定・1か月の格子）または "lunation"（朔望月の時系列パネル）。
        days: layout="lunation" のパネル枚数（3〜12・既定8）。calendar では無視。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は出力ディレクトリに保存し、同じパスを
    structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    """
    try:
        return _moon_phase_map_impl(date, place, lat, lon, layout, days)
    except Exception as e:                     # 例外をツール外へ漏らさない
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="月齢マップの生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": "moon phase map failed", "detail": str(e)[:200]},
        )


def _moon_phase_map_impl(date, place, lat, lon, layout, days):
    ll = _resolve_place(place, lat, lon)
    if ll is None and (lat is not None or lon is not None):
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="lat（-90〜90）と lon（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "lat": str(lat), "lon": str(lon)},
        )
    if ll is None:
        ll = (35.68, 139.69)
    if place:
        place_ja = str(place)
    elif lat is not None:
        place_ja = "緯度{:.2f}°・経度{:.2f}°".format(ll[0], ll[1])
    else:
        place_ja = "東京"
    tz = _local_tz(ll[0], ll[1])
    tz_label = _tz_label(tz)

    lay = str(layout or "calendar").strip().lower()
    layout_note = ""
    if lay not in ("calendar", "lunation"):
        layout_note = "layout={} は未対応のため calendar を使いました（calendar / lunation）".format(lay)
        lay = "calendar"

    parsed = _parse_date(date) if date else None
    if date and parsed is None:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="date の形式が不正です（例: 2026-09 / 2026年9月16日）")],
            structuredContent={"error": "bad date", "date": str(date)},
        )
    loader, eph = _load()
    ts = loader.timescale()
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    now_local = now_utc + datetime.timedelta(hours=float(tz or 0.0))
    if parsed:
        y, mo, d = parsed
        mo = mo if mo is not None else now_local.month
        d = d if d is not None else 1
    else:
        y, mo, d = now_local.year, now_local.month, 1

    if lay == "lunation":
        # 指定日（なければ今日）を含む朔望月: 直前の朔から次の朔までを等間隔に分ける
        ref_utc = datetime.datetime(y, mo, min(d, 28), 12, 0) - datetime.timedelta(hours=float(tz or 0.0))
        ref_jd = _iso_to_jd(ts, ref_utc)
        by_date, events = _events_by_local_date(ts, eph, ref_jd - 45.0, ref_jd + 75.0, tz)
        news = [ev for ev in events if ev["phase"] == "新月" and ev["jd"] <= ref_jd]
        if not news:
            return CallToolResult(
                content=[TextContent(type="text", text="朔（新月）の時刻を計算できませんでした")],
                structuredContent={"error": "no new moon found"},
            )
        new_jd = news[-1]["jd"]
        afters = [ev for ev in events if ev["phase"] == "新月" and ev["jd"] > new_jd + 1.0]
        next_jd = afters[0]["jd"] if afters else new_jd + 29.53
        n = as_int(days, 8, 3, 12)
        step = (next_jd - new_jd) / float(n - 1) if n > 1 else 0.0
        entries = []
        for i in range(n):
            jd = new_jd + step * i
            prev = [ev for ev in events if ev["phase"] == "新月" and ev["jd"] <= jd]
            ref_jd = prev[-1]["jd"] if prev else new_jd
            e = _entry(eph, ts, ts.tt_jd(jd), tz, ts.tt_jd(ref_jd), by_date)
            near = [ev for ev in events if abs(ev["jd"] - jd) * 1440.0 <= 60.0]
            e["event_label"] = near[0]["phase"] if near else ""
            if e["event_label"]:
                # 朔・望・上弦・下弦の瞬間のパネルは、月齢を四捨五入した名（晦日月など）
                # ではなくそのイベント名で示す（食い違って見えるため）
                e["phase_short"] = e["event_label"]
                e["phase_ja"] = "{}（月齢 {:.1f}）".format(e["event_label"], e["moon_age"])
            entries.append(e)
        cells, size = _lunation_geometry(entries)
        span_days = float(next_jd - new_jd)
        start_str = _fmt_local(ts.tt_jd(new_jd), tz)
        end_str = _fmt_local(ts.tt_jd(next_jd), tz)
        title = "朔望月の月齢マップ（{}）".format(place_ja)
        sub1 = "朔 {} 〜 次の朔 {}（{:.1f} 日を {} 枚に等間隔で分割。時刻は現地時間 {}）".format(
            start_str, end_str, span_days, n, tz_label)
        sub2 = "月齢=直前の朔からの経過日数／照度=円盤の輝面の割合・輝面の向きは太陽の位置角から計算"
        render, render_args = _render_lunation, (entries, {
            "title": title, "sub1": sub1, "sub2": sub2}, cells, size)
        kind = "moon_phase_lunation"
        period = {"start": start_str, "end": end_str,
                  "span_days": round(span_days, 2), "panels": n}
        scope = "朔 {} からの {:.1f} 日間を {} 枚に等間隔で分割（各パネルの時刻は現地時間）".format(
            start_str, span_days, n)
        month_events = []
    else:
        first = datetime.date(y, mo, 1)
        last = datetime.date(y, mo, 28) + datetime.timedelta(days=4)
        last = last - datetime.timedelta(days=last.day)          # 月末（翌月0日で丸める）
        t0_jd = _iso_to_jd(ts, datetime.datetime(first.year, first.month, first.day)
                           - datetime.timedelta(hours=float(tz or 0.0)))
        t1_jd = _iso_to_jd(ts, datetime.datetime(last.year, last.month, last.day, 23, 0)
                           - datetime.timedelta(hours=float(tz or 0.0)))
        by_date, events = _events_by_local_date(ts, eph, t0_jd - 45.0, t1_jd + 45.0, tz)
        entries = []
        for i in range(last.day):
            day = first + datetime.timedelta(days=i)
            # 各日は「現地正午」の月齢・照度に代表させる（0時だと前日の夜の月齢になる）
            tt = ts.utc(day.year, day.month, day.day, 12.0 - float(tz or 0.0))
            prev = [ev for ev in events if ev["phase"] == "新月" and ev["jd"] <= float(tt.tt)]
            new_jd = prev[-1]["jd"] if prev else float(tt.tt)
            entries.append(_entry(eph, ts, tt, tz, ts.tt_jd(new_jd), by_date))
        cells, size, _rows, _wd0 = _calendar_geometry(entries)
        for e in entries:
            if e["events"]:
                # 例: 新月が 12:26 の日は正午時点では月齢 29.4（＝晦日月）になり、
                # 図の月相名と「新月」の行が食い違って見える。日を代表する月相は
                # その日のイベント名にし、正午時点の月齢は併記する。
                ev0 = e["events"][0]
                e["phase_short"] = ev0
                e["phase_ja"] = "{}（正午時点の月齢 {:.1f}）".format(ev0, e["moon_age"])
        month_events = [ev for ev in events
                        if ev["datetime_local"][:7] == "{:04d}-{:02d}".format(y, mo)]
        parts = []
        for ev in month_events:
            ld = datetime.date(*[int(x) for x in ev["datetime_local"][:10].split("-")])
            parts.append("{}({}) {}".format(ev["phase"], _WEEKDAYS[(ld.weekday() + 1) % 7],
                                            ev["datetime_local"][11:]))
        ev_line = " ／ ".join(parts) if parts else "この月に朔・望・上弦・下弦はありません"
        title = "月齢マップ {}年{}月（{}）".format(y, mo, place_ja)
        sub1 = "月齢=直前の朔（新月）からの経過日数／照度=円盤の輝面の割合（各日 {} 正午時点）".format(tz_label)
        sub2 = "{}　※時刻は現地時間（{}）".format(ev_line, tz_label)
        render, render_args = _render_calendar, (entries, {
            "title": title, "sub1": sub1, "sub2": sub2}, cells, size)
        kind = "moon_phase_calendar"
        period = {"month": "{:04d}-{:02d}".format(y, mo), "days": last.day}
        scope = "{}年{}月の {} 日分（各日 {} 正午時点の月齢・照度）".format(
            y, mo, last.day, tz_label)

    img = render(*render_args)
    verify = _verify_moon_disks(img, cells, _CAL_R if lay == "calendar" else _LUN_R)

    ages = [e["moon_age"] for e in entries]
    illums = [e["illumination"] for e in entries]
    pas = [e["bright_limb_pa_deg"] for e in entries]
    i_max = max(range(len(entries)), key=lambda i: illums[i])
    i_min = min(range(len(entries)), key=lambda i: illums[i])
    notes = figure_notes(extra=[
        _noon_vs_event_note(entries, tz_label) if lay == "calendar" else "",
        "月齢は「直前の朔（新月）からの経過日数」。{}".format(scope),
        "照度は円盤のうち太陽に照らされている部分の面積比（満月=100%）。図の輝面はこの照度から"
        "描いており、数値と図は同じ値（このマップの範囲 {:.0f}%〜{:.0f}%）".format(
            min(illums) * 100.0, max(illums) * 100.0),
        "満ち欠けの向きは月齢から決め打ちしていない。観測地から見た太陽と月の実位置から輝面の"
        "位置角（PA・天の北から東回り）を計算し、その向きに円盤を欠けさせている"
        "（このマップの範囲 {:.0f}°〜{:.0f}°）".format(min(pas), max(pas)),
        "円盤は天の北を上・東を左に置いた見え方（赤道座標の向き）。地平線から見た上下の向きは"
        "時刻と緯度で変わるため、目の前の空の傾きとは一致しない",
        "円盤は模式図（実写ではない）。月の海の斑は乱数による模様で、クレーター・地形は描いて"
        "いない。月の視直径の変化（±5%）・秤動・地球照も反映していない",
        "影になっている側は月面が見えないだけで、月そのものが欠けているわけではない",
        "各円盤で明暗境界線の位置を画素から測り直し、申告した照度と一致することを確認している"
        "（最大誤差 {:.4f}・許容 {:.2f}。細い三日月は境界線が1〜2画素幅になるため丸め誤差が残る）"
        .format(verify["worst_error"], verify["tolerance"]),
        "出典: JPL DE421 + Skyfield（ローカル計算・認証不要）",
    ])
    fig = figure_payload(
        kind=kind, title=title,
        view=view_spec("earth_observer", "sky_disk",
                       "観測地から見た月面（天の北を上・東を左に置いた円盤）",
                       why="満ち欠けは地球から見た見かけの形なので、地平座標ではなく太陽と月の"
                           "実位置（赤道座標）から輝面の向きを決めている"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["月の見かけの大きさは全パネルで同じ"
                                      "（実際の視直径の変化 ±5% は反映していない）"]),
        notes=notes,
        caption="{} の月相（{}）。照度は {:.0f}%（{}）〜 {:.0f}%（{}）、月齢 {:.1f}〜{:.1f}。".format(
            place_ja, scope, illums[i_min] * 100.0, entries[i_min]["date_str"],
            illums[i_max] * 100.0, entries[i_max]["date_str"], min(ages), max(ages)),
        verify=verify,
    )
    png = _png_bytes(img)
    out_path = save_output(png, "moon_phase_map", "png")
    label = period.get("month") or "{:.0f}日間".format(period.get("span_days") or 0.0)
    lines = [media_link_line("月齢マップ {}".format(label),
                            path=out_path, kind="figure"),
             "🌙 **{}**".format(title)]
    if layout_note:
        lines.append("⚠️ " + layout_note)
    if lay == "calendar":
        lines.append("朔・望・上弦・下弦（現地時間 {}）:".format(tz_label))
        for ev in month_events:
            lines.append("- {}  {}".format(ev["datetime_local"], ev["phase"]))
        lines += ["", "| 日付 | 曜 | 月齢 | 照度 | 月相 |", "|---|---|---|---|---|"]
        for e in entries:
            ev_s = "（" + "・".join(e["events"]) + "）" if e["events"] else ""
            if ev_s and e["phase_ja"].startswith(ev_s[1:-1]):
                ev_s = ""
            lines.append("| {} | {} | {:.1f} | {}% | {}{} |".format(
                e["date_str"], e["weekday"], e["moon_age"], e["illum_pct"], e["phase_ja"], ev_s))
    else:
        lines += ["", "| # | 日時（現地） | 月齢 | 照度 | 月相 |", "|---|---|---|---|---|"]
        for i, e in enumerate(entries):
            ev_s = "（{}）".format(e["event_label"]) if e.get("event_label") else ""
            lines.append("| {} | {} {} | {:.1f} | {}% | {}{} |".format(
                i + 1, e["date_str"], e["time_str"], e["moon_age"], e["illum_pct"],
                e["phase_ja"], ev_s))
    lines += ["", figure_text_block(fig), "画像は上に表示（base64 PNG）。出典: JPL DE421 + Skyfield"]
    safe_days = [{"date": e["date"].isoformat(), "weekday": e["weekday"],
                  "moon_age": e["moon_age"], "illumination": e["illumination"],
                  "illum_pct": e["illum_pct"], "phase_ja": e["phase_ja"],
                  "bright_limb_pa_deg": e["bright_limb_pa_deg"],
                  "time_local": e["time_str"], "events": e["events"]} for e in entries]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)),
                 ImageContent(type="image", data=base64.b64encode(png).decode("ascii"),
                              mimeType="image/png",
                              altText="月齢マップ {}".format(title))],
        structuredContent={"layout": lay, "place": place_ja, "lat": ll[0], "lon": ll[1],
                           "tz_label": tz_label, "period": period, "days": safe_days,
                           "events": events, "figure": fig, "image_path": out_path,
                           "source": "JPL DE421+Skyfield"},
    )


def _noon_vs_event_note(entries, tz_label):
    """カレンダーの正午時点の月齢と、朔・望の実際の時刻の食い違いを数値から説明する。

    手書きの注記だと、図の「新月」の行と正午時点の月齢（29.4 など）が食い違って
    見える理由をLLMが説明できない（誤読の元）。
    """
    # 朔の日が最も食い違いが大きく見える（新月の正午時点が月齢 29 台になる）ので優先する
    cands = [e for e in entries if "新月" in e["events"]] or [e for e in entries if e["events"]]
    for e in cands:
        return ("カレンダーの月齢・照度は各日の {} 正午時点の値なので、朔・望の日は正午時点では"
                "朔・望の前後になる。例: {} は正午時点の月齢が {:.1f} で、{} の時刻は {}".format(
                    tz_label, e["date_str"], e["moon_age"],
                    "・".join(e["events"]),
                    "・".join(e.get("event_times") or [e["time_str"]])))
    return ""


def _png_bytes(img):
    """PIL 画像を PNG バイト列にする。"""
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

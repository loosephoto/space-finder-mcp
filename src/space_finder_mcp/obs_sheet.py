"""観測判断シート（判定＋雲量タイムライン＋衛星＋雨雲を1枚に）。

天体観測の可否を「1枚で」判断できるようにする合成図。材料は 3 つの既存ルーチン:
- 雲量・月・薄明・観測窓: `weather_astro.astronomy_weather`（Open-Meteo + Skyfield）
- 衛星の実況: `weather_sat._latest_himawari_frame`（気象庁 MSC・赤外）
- 雨雲の実況/60分先: `jma_rain.rain_images`（気象庁ナウキャスト。日本国内のみ）

設計:
- **予報と実況を混ぜない**: 雲量の棒は「予報」、衛星・雨雲は「実況」とタグで明示する。
- **時間軸は1本**: 「今夜 18:00→翌06:00」「明日の夜」の2段。日付をまたぐ時刻の重複を避ける。
- **判定を先頭に**: ○/△/× と根拠（雲量の範囲・月照度・月の出・降水）を並べる。
- 雨雲が取れない（日本国外・気象庁の障害）ときは衛星だけの軽量版に自動で落とす。
"""
from __future__ import annotations

import datetime as dt
import io

from mcp.types import CallToolResult, ImageContent, TextContent

from .img_common import (encode_jpeg, figure_notes, figure_payload, figure_text_block,
                         load_font, media_link_line, save_output, scale_spec, view_spec)
from .input_utils import as_float

BG, PANEL, LINE = (12, 14, 22), (18, 20, 30), (58, 64, 82)
FG, DIM, ACC = (236, 240, 250), (150, 158, 176), (255, 214, 120)
OKC, WARNC, NGC, BLUE = (120, 230, 160), (255, 190, 90), (255, 120, 120), (150, 190, 255)
W = 1560


def _verdict(max_cloud, min_cloud=None, rain_near=False):
    """雲量（予報）から観測可否を決める。

    しきい値は「深宇宙観測の実用」を基準にした目安: ≤20% 可 / ≤60% 条件付き / それ以上は不向き。
    **判定は雲量だけで決める**のが既定で、雨雲（ナウキャスト＝実況）は判定に混ぜない
    （実況を混ぜると「雨雲データが取れただけで×」になり、図の理由と食い違う）。
    `rain_near=True` を渡したときだけ1段下げる（呼び出し側が実況を根拠に下げたい場合）。
    """
    if max_cloud is None:
        return "?", "判定不能", DIM
    if max_cloud <= 20 and not rain_near:
        return "○", "観測可", OKC
    if max_cloud <= 60 and not rain_near:
        return "△", "条件付きで可", WARNC
    return "×", "不向き（雲が多い）", NGC


def _hm(iso):
    return (iso or "")[11:16]


def _card(d, box, title, tag=None):
    d.rounded_rectangle(box, radius=14, fill=PANEL, outline=LINE)
    d.text((box[0] + 18, box[1] + 12), title, font=load_font(21, True), fill=FG)
    if tag:
        tw = d.textlength(tag, font=load_font(15, True))
        d.rounded_rectangle([box[2] - 18 - tw - 16, box[1] + 12, box[2] - 18, box[1] + 38],
                            radius=12, fill=(38, 44, 60))
        d.text((box[2] - 18 - tw - 8, box[1] + 16), tag, font=load_font(15, True), fill=ACC)


def _same_night(h, night_date):
    """h が night_date の夜（18:00→翌06:00）に属するか。

    時刻だけで選ぶと 3 夜ぶんが同じ行に重なる（実測: 今夜 9 時間なのに 27 本描かれて
    いた）。night_date の 18:00〜23:59 と、翌朝 00:00〜05:59 だけをその夜とする。
    """
    if night_date is None:
        return True
    hh = int(h["time"][11:13])
    if 6 <= hh < 18:                     # 昼間はその夜に属さない
        return False
    want = night_date if hh >= 18 else night_date + dt.timedelta(days=1)
    return h["time"][:10] == want.isoformat()


def _first_night_date(hours):
    """タイムラインの起点（= 予報初日 = 現地の今日）の日付を返す。

    「今夜」は窓の開始日ではなく**現地の今日** 18:00 起点で決める。旧実装は
    `windows[0]["start"]`（3日間で最初の観測好機）の発想で、今夜が曇天・明晩が
    晴天だと「明晩の窓」を今夜として描画していた（実測）。
    """
    for h in hours or []:
        t = str(h.get("time") or "")
        if len(t) >= 10:
            try:
                return dt.date.fromisoformat(t[:10])
            except ValueError:
                continue
    return None


def _night_row(d, box, hours, label, moon, start, end, night_date=None, k_offset=0):
    """1夜ぶん（18:00→翌06:00）の雲量バー。start/end は 18 時起点の通し時。

    night_date を渡すと「その日付の夜」の時間だけを選ぶ。k_offset は 2 段目
    （明日の夜）で 24 を足すために使う。戻り値は描いた棒の本数。
    """
    x0, y0, x1, y1 = box
    px0, py0, px1, py1 = x0 + 78, y0 + 10, x1 - 16, y1 - 26
    d.text((x0, y0), label, font=load_font(17, True), fill=FG)
    sel = []
    for h in hours:
        hh = int(h["time"][11:13])
        k = (hh if hh >= 18 else hh + 24) + k_offset
        if start <= k <= end and _same_night(h, night_date):
            sel.append((k, h))
    if not sel:
        d.text((px0, py0 + 20), "この夜の予報データがありません", font=load_font(15), fill=DIM)
        return 0
    for v in (0, 50, 100):
        yy = py1 - (py1 - py0) * v / 100.0
        d.line([px0, yy, px1, yy], fill=(44, 50, 66), width=1)
        # 左マージン（x0+12）に置くと夜行のラベルと重なる（実測 333px²）。右マージンへ。
        d.text((px1 + 4, yy - 8), "{}%".format(v), font=load_font(12), fill=DIM)
    # k は start〜end の両端を含む（13 個）ので span は +1 が正しい。+1 なしだと
    # 最後の棒がプロット右端をはみ出し、値ラベルが % 目盛と重なる（実測 78px²）。
    span = max(1, end - start + 1)
    step = (px1 - px0) / span
    for k, h in sel:
        c = h.get("cloud_cover")
        if c is None:                       # 欠測の時間は棒を作らない（None で TypeError にしない）
            continue
        bx = px0 + step * (k - start)
        bh = (py1 - py0) * c / 100.0
        col = OKC if c <= 20 else (ACC if c <= 50 else NGC)
        d.rectangle([bx + 1, py1 - bh, bx + step - 2, py1], fill=col)
        if (k - start) % 3 == 0:
            d.text((bx - 4, py1 + 4), "{:02d}".format(k % 24), font=load_font(13), fill=DIM)
        d.text((bx + 1, py1 - bh - 18), str(c), font=load_font(12, True), fill=col)
    mr = moon.get("moonrise") or ""
    if mr:
        mk = int(mr[11:13]) + (24 if int(mr[11:13]) < 18 else 0) + k_offset
        if start <= mk <= end:
            mx = px0 + step * (mk - start) + step * int(mr[14:16]) / 60.0
            d.line([mx, py0 - 4, mx, py1], fill=BLUE, width=2)
            d.text((mx + 4, py0 - 22), "月の出 {}".format(mr[11:16]), font=load_font(13), fill=BLUE)
    return len(sel)


def build_sheet(*, place, lat, lon, hours, windows, moon, sat=None, rain_panels=None,
                sat_obs=None, source_note=""):
    """判断シートを描く。戻り値: (PIL画像, 検証 dict)。

    sat: ひまわりのフルディスク PIL 画像（None なら衛星カードを出さない）
    rain_panels: ナウキャストのパネル画像 2 枚（None なら軽量版）
    """
    from PIL import Image, ImageDraw

    from .weather_sat import _geo_px, _japan_box

    # 判定は**今夜**の予報雲量で決める（雨雲＝実況は判定に混ぜない）。
    # 旧実装は `windows[0]`（3日間で最初の観測好機）を根拠にしていたため、今夜が
    # 曇天で明晩が晴天だと「明晩の窓」を今夜として描画し、○ を出していた（実測）。
    night_date = _first_night_date(hours)
    tonight = [h for h in (hours or []) if _same_night(h, night_date)]
    tonight_max = max([h["cloud_cover"] for h in tonight
                       if h.get("cloud_cover") is not None], default=None)
    v = _verdict(tonight_max)
    win = next((w for w in (windows or [])
                if _same_night({"time": str(w.get("start") or "")}, night_date)), {})
    has_rain = bool(rain_panels)
    H = 1010 if not (sat or has_rain) else (1180 if (sat and has_rain) else 1010)
    if sat and has_rain:
        H = 1180
    elif sat or has_rain:
        H = 1010
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((28, 16), "🔭 観測判断シート — {}（{:.2f}N {:.2f}E）".format(place, lat, lon),
           font=load_font(32, True), fill=FG)

    # --- 判定
    d.rounded_rectangle([28, 64, W - 28, 170], radius=16, fill=PANEL, outline=LINE)
    d.text((46, 74), v[0], font=load_font(56, True), fill=v[2])
    d.text((116, 74), v[1], font=load_font(30, True), fill=FG)
    if win.get("start"):
        _span = "{} 〜 {}・雲量 {}〜{}%".format(
            str(win["start"])[5:].replace("T", " "),
            str(win.get("end") or "")[5:].replace("T", " "),
            win.get("min_cloud"), win.get("max_cloud"))
    else:
        _span = "今夜の観測好機なし・雲量の最大 {}".format(
            "-" if tonight_max is None else "{}%".format(tonight_max))
    d.text((116, 116), "{}・月照度 {}・月の出 {}".format(
        _span, moon.get("illumination_pct"), _hm(moon.get("moonrise"))),
        font=load_font(18), fill=DIM)

    # --- 雲量タイムライン
    _card(d, [28, 184, W - 28, 470], "☁ 雲量の推移（予報・1時間ごと）")
    lx = W - 700
    for col, lab in ((OKC, "快晴寄り ≤20%"), (ACC, "注意 21–50%"), (NGC, "曇り >50%")):
        d.rectangle([lx, 200, lx + 12, 212], fill=col)
        d.text((lx + 18, 197), lab, font=load_font(14), fill=DIM)
        lx += 26 + d.textlength(lab, font=load_font(14))
    d.line([lx + 8, 198, lx + 8, 214], fill=BLUE, width=2)
    d.text((lx + 16, 197), "月の出", font=load_font(14), fill=DIM)
    bars = _night_row(d, [46, 226, W - 46, 330], hours, "今夜 18:00 → 翌 06:00",
                      moon, 18, 30, night_date)
    # 2 段目は「明日の夜」= night_date の翌日を 18:00 とする。k は 1 段目より 24 進める。
    second_date = night_date + dt.timedelta(days=1) if night_date else None
    has_second = bool(second_date) and any(_same_night(h, second_date)
                                           for h in (hours or []))
    if has_second:
        bars += _night_row(d, [46, 356, W - 46, 458], hours, "明日の夜 18:00 → 翌 06:00",
                           moon, 42, 54, second_date, k_offset=24)
    else:
        d.text((46, 366), "明日の夜", font=load_font(17, True), fill=FG)
        d.text((150, 368), "予報の取得範囲外（`days` を増やすと出ます）", font=load_font(15), fill=DIM)
        d.text((46, 398), "※ 今夜の判断には影響しません", font=load_font(14), fill=(120, 128, 146))

    # 雲量タイムラインは「その夜の時間」だけを描くので、描いた棒の本数を数える
    # （旧実装は hourly の全件数＝3 夜ぶんの 27 を数えていた）
    verify = {"verdict": v[0], "panels": [], "site_marker_px": None,
              "cloud_bars": bars}

    y = 484
    half = (W - 28 - 28 - 28) // 2
    if sat is not None:
        _card(d, [28, y, 28 + half, y + 516], "🛰 ひまわり9号 赤外（B13）", tag="実況")
        # カード標題（y+12・21px 太字）の下に置く（y+32 だと 281px² 重なっていた）
        d.text((46, y + 46), "観測 {} UTC ／ 明るい=雲頂が高い".format(
            (sat_obs or "").replace("T", " ").replace("Z", "")), font=load_font(14), fill=DIM)
        jb = _japan_box(sat.width)
        cw, ch = jb[2] - jb[0], jb[3] - jb[1]
        big = min(420, half - 90)
        dh = int(round(big * ch / max(1, cw)))
        crop = sat.crop(jb).resize((big, dh), Image.Resampling.LANCZOS)
        im.paste(crop, (46, y + 60))
        cx = (_geo_px(lat, lon, sat.width)[0] - jb[0]) * big / max(1, cw)
        cy = (_geo_px(lat, lon, sat.width)[1] - jb[1]) * dh / max(1, ch)
        d.ellipse([46 + cx - 9, y + 60 + cy - 9, 46 + cx + 9, y + 60 + cy + 9],
                  outline=OKC, width=3)
        d.text((46 + cx + 13, y + 60 + cy - 12), place, font=load_font(17, True), fill=OKC)
        verify["site_marker_px"] = [int(46 + cx), int(y + 60 + cy)]
        verify["panels"].append("satellite")
        d.text((46, y + 60 + dh + 8), "日本域（拡大）・地点マーカー付き", font=load_font(13), fill=DIM)
    if has_rain:
        x0 = 28 + half + 28
        _card(d, [x0, y, W - 28, y + 516], "🌧 雨雲（気象庁ナウキャスト・現在の実況）", tag="実況→60分")
        pw = (W - 28 - x0 - 36 - 16) // 2
        for i, p in enumerate(rain_panels[:2]):
            im.paste(p.resize((pw, pw), Image.Resampling.LANCZOS),
                     (x0 + 18 + i * (pw + 16), y + 60))
            d.text((x0 + 18 + i * (pw + 16), y + 60 + pw + 6),
                   "実況" if i == 0 else "+60分", font=load_font(15, True), fill=ACC)
        d.text((x0 + 18, y + 60 + pw + 34),
               "※ この2枚は「現在」の雨雲。今夜の降水は雲量の予報（上段）を見る",
               font=load_font(14), fill=(120, 128, 146))
        verify["panels"].append("rain")

    note_y = H - 96
    d.text((28, note_y), "出典: {} ／ 雲量・月・薄明 = Open-Meteo・Skyfield".format(
        source_note or "気象庁 ひまわり9号・ナウキャスト"), font=load_font(15), fill=DIM)
    d.text((28, note_y + 26),
           "※ 衛星と雨雲は「実況」、雲量の棒は「予報」。雨が降っていなくても雲はある（降水≠雲量）",
           font=load_font(15), fill=(120, 128, 146))
    verify["ok"] = bool(verify["cloud_bars"] > 0)
    return im, verify


def observing_sheet(place=None, lat=None, lon=None, band="infrared"):
    """天体観測の判断シート（判定＋雲量＋衛星＋雨雲）を1枚で返す。

    例:「今夜の観測は大丈夫? 図で」「雲の様子と雨雲を1枚で見せて」
    雲量・月・薄明・観測窓は Open-Meteo + Skyfield、衛星は気象庁ひまわり9号（赤外）、
    雨雲は気象庁ナウキャスト（日本国内のみ）。**予報（雲量）と実況（衛星・雨雲）を
    タグで区別**し、判定（○/△/×）を先頭に出す。

    インライン画像を表示できないハーネス向けに、content の先頭へ
    「🖼️ [生成した画像を開く: 観測判断シート（地点）](file:///…)」を出す。
    回答時はこのリンクをそのまま提示してください。

    Args:
        place: 観測地（例 "東京"）。**省略時はエラー**（地点は必須。lat/lon でも可）。
        lat: 緯度（-90〜90）。lon と併用。
        lon: 経度（-180〜180）。
        band: 衛星パネルの帯域（既定 "infrared"＝夜でも読める。可視は夜に真っ暗）。
    """
    from . import weather_astro as wa
    from . import weather_sat as wsat
    from .jma_rain import rain_images

    la = as_float(lat, None, -90.0, 90.0)
    lo = as_float(lon, None, -180.0, 180.0)
    # include_rain=False: 雨雲はこの関数の下で自分で取る（旧実装は
    # astronomy_weather に作らせた雨雲を捨てて rain_images をもう一度呼んでいた＝二重取得）。
    aw = wa.astronomy_weather(place=place, latitude=la, longitude=lo, include_rain=False)
    sc = aw.structuredContent or {}
    if sc.get("error"):
        return CallToolResult(content=[TextContent(type="text", text=str(sc["error"]))],
                              structuredContent={"error": sc["error"]})
    la, lo = float(sc["lat"]), float(sc["lon"])
    site = sc.get("place") or place or "{:.2f}N {:.2f}E".format(la, lo)

    band_key, band_spec = wsat._resolve_band(band)
    if not band_spec:
        band_key, band_spec = "infrared", wsat._HIMAWARI_BANDS["infrared"]
    sat_img, sat_obs, sat_note = None, None, ""
    try:
        obs, url, raw, q = wsat._latest_himawari_frame(band_spec["code"])
        from PIL import Image
        sat_img, sat_obs = Image.open(io.BytesIO(raw)).convert("RGB"), obs
    except Exception as e:
        sat_note = "衛星: 取得できませんでした（{}）".format(str(e)[:80])

    panels, rain_note = None, ""
    try:
        ri = rain_images(la, lo, site)
        if ri:
            src = Image.open(io.BytesIO(ri["images"][1]["bytes"])).convert("RGB")
            panels = [src.crop(b) for b in ((26, 144, 686, 804), (712, 144, 1372, 804))]
    except Exception as e:
        rain_note = "雨雲: 取得できませんでした（{}）".format(str(e)[:80])

    # hours は「暗い時間帯の全時刻」（night_hours）。`hourly` は観測好機の時間だけなので、
    # それを渡すと曇りの棒が描けない（旧実装）。古いサーバとの互換で hourly に落とす。
    img, verify = build_sheet(place=site, lat=la, lon=lo,
                              hours=sc.get("night_hours") or sc.get("hourly") or [],
                              windows=sc.get("windows") or [], moon=sc.get("moon") or {},
                              sat=sat_img, rain_panels=panels, sat_obs=sat_obs,
                              source_note="気象庁 ひまわり9号（MSC）" if sat_img else "")
    jpeg = encode_jpeg(img)
    out_path = save_output(jpeg, "observing_sheet", "jpg")
    notes = [
        "判定は「その夜の予報雲量の最大値」で決めている（≤20% 可 / ≤60% 条件付き / それ以上は不向き）。"
        "判定は予報雲量のみで決める（雨雲の有無では上下させない）。雨雲は実況の参考情報",
        "衛星・雨雲は実況、雲量の棒は予報。混同しないよう図にもタグを付けている",
        "雨が降っていなくても雲はある（降水≠雲量）。降水パネルは「雨」の情報",
        "衛星は 601px の全球画像から日本域を切り出して拡大しているため、細部は粗い",
        sat_note, rain_note,
    ]
    fig = figure_payload(
        kind="observing_sheet",
        title="観測判断シート（{}）".format(site),
        view=view_spec("composite_panel", "mixed",
                       "判定＋雲量の予報タイムライン＋ひまわり赤外（日本域）＋気象庁ナウキャストを1枚に合成",
                       why="観測可否の判断は「予報の雲量」と「実況の雲・雨」を別々の図で見比べる必要があり、"
                           "1枚に並べて初めて判断できるため"),
        primary={"name": "観測地点", "role": "site", "lat": la, "lon": lo},
        scale=scale_spec("mixed", to_scale=False, unit=""),
        notes=figure_notes(extra=notes),
        caption="{} の今夜の観測判断。数値は structuredContent を参照。".format(site),
        verify=verify,
    )
    text = "\n".join([
        media_link_line("観測判断シート（{}）".format(site), path=out_path, kind="figure"),
        "🔭 **{} の観測判断シート**".format(site),
        figure_text_block(fig),
    ])
    return CallToolResult(
        content=[TextContent(type="text", text=text),
                 ImageContent(type="image", data=__import__("base64").b64encode(jpeg).decode("ascii"),
                              mimeType="image/jpeg", altText="{} の観測判断シート".format(site))],
        structuredContent={
            "place": site, "lat": la, "lon": lo,
            "verdict": verify.get("verdict"),
            "band": band_key, "band_code": band_spec["code"],
            "satellite_observed_at_utc": sat_obs,
            "windows": sc["windows"], "moon": sc["moon"],
            "hourly": sc["hourly"], "rain_available": bool(panels),
            "image_path": out_path, "figure": fig,
            "source": "Open-Meteo + Skyfield / JMA ひまわり9号(MSC) / JMA ナウキャスト",
        },
    )

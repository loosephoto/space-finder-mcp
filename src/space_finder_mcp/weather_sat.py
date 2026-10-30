"""世界の気象観測衛星のリアルタイム実画像を返す（認証不要）。

Phase 0: 日本の静止気象衛星 ひまわり9号 のフルディスク実画像。プロバイダ抽象層を設け、
将来 GOES（AWS Open Data）や Meteosat 等を同インターフェースで追加できる構造にしている。

**取得元（2026-10-31 実測で確定）**
- 主: 気象庁 気象衛星センター（MSC）のリアルタイム画像。
  `img/fd_/fd__{element}_{HHMM}.jpg`（601×601 JPEG・10分間隔・約23時間保持）。
  帯域は全種そろう: b13=赤外 / b03=可視 / b08=水蒸気 / ngt=夜間マイクロフィジクスRGB /
  dms=昼間マイクロフィジクスRGB / dnc=自然色RGB。
  時刻一覧はページの `<option value=HHMM>` から取る（HHMM だけでは日付が曖昧なので、
  ラベルの「20:50 UTC 30 October 2026」から UTC 日時を作る）。
- 予備: NICT/Kochi ミラー（himawari.asia）の true color 550px PNG。`1d` のみ実画像で、
  `4d`/`8d` 等は **444 バイトの全黒プレースホルダ**が返る（`{N}d` は帯域ではなくタイル分割数）。
  旧実装はこれを帯域コードとして使っていたため、赤外と true_color が 404 / 全黒になっていた。

**実画像かどうかを必ず測る**: 配信元は実画像の代わりに全黒のプレースホルダを返すことがある
（実測: 550×550 1bit・444 バイト）。JPEG マジック・バイト数・色数を見て、駄目なら次の時刻へ
送る（黙って黒い画像を返さない）。

出典: 気象庁 ひまわり9号（JMA/MSC）／ 予備 = himawari.asia（NICT/Kochi ミラー、認証不要）
"""
from __future__ import annotations

import base64
import datetime
import io
import re

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import ttl_cache
from .img_common import encode_jpeg, media_link_line, save_output
from .input_utils import as_float, as_int

_JMA_PAGE = "https://www.data.jma.go.jp/mscweb/data/himawari/sat_img.php?area=se"
_JMA_IMG = "https://www.data.jma.go.jp/mscweb/data/himawari/img/fd_/fd__{elem}_{hhmm}.jpg"
_HIMAWARI_BASE = "https://himawari.asia/img/D531106/1d/{size}/{ts}_0_0.png"
_UA = {"User-Agent": "space-finder-mcp/0.44 (MCP; weather satellite imagery)"}

# 帯域 → 気象庁 MSC の element 名。name/note は本文と structuredContent にそのまま出る。
_HIMAWARI_BANDS = {
    "visible":     {"code": "b03", "name": "可視光", "note": "昼間の雲・台風の形状（夜間は真っ暗）",
                    "night_ok": False},
    "infrared":    {"code": "b13", "name": "赤外線（色付け）", "note": "昼夜問わず雲頂高度・温度",
                    "night_ok": True},
    "water_vapor": {"code": "b08", "name": "水蒸気", "note": "上中層の水蒸気・ジェット気流",
                    "night_ok": True},
    "night_rgb":   {"code": "ngt", "name": "夜間マイクロフィジクスRGB",
                    "note": "夜間の下層雲・霧・雨雲の判別（夜の観測可否判断向け）", "night_ok": True},
    "day_rgb":     {"code": "dms", "name": "昼間マイクロフィジクスRGB",
                    "note": "昼間の雲粒の大きさ・相（積雲と層雲の区別）", "night_ok": False},
    "true_color":  {"code": "dnc", "name": "自然色（RGB合成）", "note": "自然な見た目に近い合成",
                    "night_ok": False},
}
# 旧名・略号からの受け付け（互換）。"3d"/"8d" は旧実装が使っていた誤った帯域コード。
_BAND_ALIASES = {
    "vis": "visible", "b03": "visible", "1d": "visible", "truecolor": "true_color",
    "natural": "true_color", "natural_color": "true_color", "dnc": "true_color", "8d": "true_color",
    "ir": "infrared", "b13": "infrared", "3d": "infrared", "infra": "infrared",
    "wv": "water_vapor", "b08": "water_vapor", "vapor": "water_vapor",
    "night": "night_rgb", "ngt": "night_rgb", "night_microphysics": "night_rgb",
    "day": "day_rgb", "dms": "day_rgb", "day_microphysics": "day_rgb",
    "auto": "infrared",
}


def _resolve_himawari_name(satellite):
    s = str(satellite or "").strip().lower()
    s = s.replace("ひまわり", "himawari").replace("号", "").replace(" ", "").replace("-", "")
    if s in ("", "himawari", "himawari9", "himawari09"):
        return "himawari9"
    if s in ("himawari8", "himawari08"):
        return "himawari8"
    return ""


def _resolve_band(band):
    """帯域名（または旧名・略号）→ (正規キー, 仕様 dict)。未対応なら (None, None)。"""
    b = str(band or "visible").strip().lower().replace(" ", "_")
    key = _BAND_ALIASES.get(b, b)
    if key in _HIMAWARI_BANDS:
        return key, _HIMAWARI_BANDS[key]
    return None, None


def image_quality(data):
    """配信された画像が「実画像か」を測る（プレースホルダの全黒 PNG を弾くため）。

    戻り値: {"ok": bool, "bytes": int, "size": [w, h], "colors": int, "format": str, "why": str}
    実測の目安: 気象庁 MSC の実画像は 110〜190 KB・色数 2 万〜13 万。プレースホルダは
    444 バイト・1 ビット・単色なので、どちらの判定でも落ちる。
    """
    rep = {"ok": False, "bytes": len(data or b""), "size": None, "colors": 0,
           "format": "", "why": ""}
    if not data:
        rep["why"] = "応答が空"
        return rep
    if data[:2] == b"\xff\xd8":
        rep["format"] = "jpeg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        rep["format"] = "png"
    else:
        rep["why"] = "JPEG/PNG ではない（先頭バイト {}）".format(data[:4].hex())
        return rep
    if len(data) < 10_000:
        rep["why"] = "小さすぎる（{} バイト）＝配信元のプレースホルダの可能性".format(len(data))
        return rep
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        rep["size"] = [im.width, im.height]
        colors = im.convert("RGB").getcolors(maxcolors=300000)
        rep["colors"] = len(colors) if colors else 300000
    except Exception as e:                     # 壊れた画像は「実画像ではない」
        rep["why"] = "デコードできない（{}）".format(type(e).__name__)
        return rep
    if rep["colors"] < 500:
        rep["why"] = "色数が {} しかない（単色・全黒のプレースホルダ）".format(rep["colors"])
        return rep
    rep["ok"] = True
    return rep


@ttl_cache(seconds=60)      # 時刻一覧は10分ごとに増える。1分キャッシュで追随しつつページを叩きすぎない
def _himawari_times():
    """気象庁 MSC の画像ページから「取得可能な UTC 時刻の一覧（新しい順）」を取る。

    戻り値: [{"hhmm": "2050", "iso": "2026-10-30T20:50:00Z", "label": "20:50 UTC 30 October 2026"}]
    HHMM だけでは日付が決まらないので、ラベルの日付から ISO を作る（日付跨ぎで誤らない）。
    """
    r = requests.get(_JMA_PAGE, headers=_UA, timeout=(10, 25))
    r.raise_for_status()
    out = []
    for hhmm, label in re.findall(r"<option value=(\d{4})>([^<]+)</option>", r.text):
        iso = None
        try:
            dt = datetime.datetime.strptime(label.strip(), "%H:%M UTC %d %B %Y")
            iso = dt.replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
        out.append({"hhmm": hhmm, "iso": iso, "label": label.strip()})
    if not out:
        raise ValueError("気象庁の時刻一覧を解析できませんでした（ページ構造の変更？）")
    return out


@ttl_cache(seconds=30)      # 鮮度優先: 30秒間だけ同一スロットを再利用（配信遅延への追随を早める）
def _latest_himawari_frame(band_code):
    """指定 element の最新フレームを返す: (observed_iso, url, bytes, quality dict)。

    新しい時刻から順に試し、**実画像と確認できた最初のフレーム**を採る（プレースホルダや
    404 は次の時刻へ送る）。気象庁が落ちているときは可視光のみ NICT ミラーへ退避する。
    """
    errors = []
    try:
        times = _himawari_times()
    except (requests.RequestException, ValueError) as e:
        times = []
        errors.append("時刻一覧: {}".format(type(e).__name__))
    for t in times[:6]:
        url = _JMA_IMG.format(elem=band_code, hhmm=t["hhmm"])
        try:
            r = requests.get(url, headers=_UA, timeout=(10, 30))
        except requests.RequestException as e:
            errors.append("{}: {}".format(t["hhmm"], type(e).__name__))
            continue
        if r.status_code != 200 or not r.content:
            errors.append("{}: HTTP {}".format(t["hhmm"], r.status_code))
            continue
        q = image_quality(r.content)
        if not q["ok"]:
            errors.append("{}: {}".format(t["hhmm"], q["why"]))
            continue
        observed = t["iso"] or (datetime.datetime.now(datetime.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"))
        return observed, url, r.content, q
    # 予備: NICT/Kochi ミラー（true color のみ・10分刻みのスロットを遡って探す）
    if band_code in ("b03", "dnc"):
        now = datetime.datetime.now(datetime.timezone.utc)
        for back in range(0, 7):
            tt = (now - datetime.timedelta(minutes=back * 10)).replace(
                minute=(now.minute // 10) * 10, second=0, microsecond=0)
            ts = tt.strftime("%Y/%m/%d/%H%M%S")
            url = _HIMAWARI_BASE.format(size=550, ts=ts)
            try:
                r = requests.get(url, headers=_UA, timeout=(15, 20))
            except requests.RequestException as e:
                errors.append("nict {}: {}".format(tt.strftime("%H:%M"), type(e).__name__))
                continue
            q = image_quality(r.content) if r.status_code == 200 else {"ok": False, "why": "HTTP {}".format(r.status_code)}
            if r.status_code == 200 and q.get("ok"):
                return (tt.strftime("%Y-%m-%dT%H:%M:%SZ"), url, r.content, q)
            errors.append("nict {}: {}".format(tt.strftime("%H:%M"), q.get("why")))
    raise ValueError(
        "最新のひまわり画像を取得できませんでした（実画像と確認できたフレームが無い）。試行: {}"
        .format(" / ".join(errors[-5:]) or "なし"))


# ---------------------------------------------------------------- 衛星パネルの合成
# 配信画像は 601×601 の全球画像なので、そのまま出すと「日本がどこか」が分からない。
# 全円盤（左）＋日本域拡大（右）＋観測地点マーカーを 1 枚に焼き込む。
# 緯度経度→画素は静止軌道（東経 140.7°）からの透視投影の逆算で、解析値で検算できる
# （赤道・経度差 81.3° が円盤の縁 x=601、直下点が中央 x=300.5 になる）。
_GEO_R = 6.6107          # 静止軌道半径 / 地球半径
_GEO_TAN = 0.15305       # tan(地球の視半径)


def _geo_px(lat, lon, size, lon0=140.7):
    """緯度経度 → ひまわりフルディスク画像の画素座標。"""
    import math
    ph, dl = math.radians(float(lat)), math.radians(float(lon) - lon0)
    xe = math.cos(ph) * math.cos(dl)
    ye = math.cos(ph) * math.sin(dl)
    ze = math.sin(ph)
    k = _GEO_R - xe
    return (size / 2.0 + (ye / k) / _GEO_TAN * size / 2.0,
            size / 2.0 - (ze / k) / _GEO_TAN * size / 2.0)


def _japan_box(size, pad=6):
    """日本域（29–46.5N / 127–148E）の外接矩形（画素）。"""
    pts = [_geo_px(la, lo, size) for la, lo in ((29.0, 127.0), (46.5, 127.0),
                                                (46.5, 148.0), (29.0, 148.0))]
    x0 = max(0, int(min(p[0] for p in pts)) - pad)
    y0 = max(0, int(min(p[1] for p in pts)) - pad)
    x1 = min(size, int(max(p[0] for p in pts)) + pad)
    y1 = min(size, int(max(p[1] for p in pts)) + pad)
    return x0, y0, x1, y1


def compose_panel(disk, observed_iso, band_name, band_note, lat=None, lon=None,
                  place=None, source=""):
    """全円盤＋日本域拡大＋観測地点マーカーの 1 枚（1500×880）を作る。

    戻り値: (PIL画像, 検証 dict)。検証は「地点マーカーが描けたか」「日本域の切り出しが
    円盤内に収まっているか」「拡大画像が単色でない（実画像である）か」を画素で見る。
    """
    from PIL import Image, ImageDraw

    from .img_common import load_font, pixel_near

    BG, LINE = (12, 14, 22), (58, 64, 82)
    FG, DIM, ACC, OKC = (236, 240, 250), (150, 158, 176), (255, 214, 120), (120, 230, 160)
    W, H = 1500, 880
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((28, 20), "🛰 ひまわり9号 {}".format(band_name), font=load_font(32, True), fill=FG)
    d.text((28, 64), "観測 {} UTC ・{}".format(
        observed_iso.replace("T", " ").replace("Z", ""), band_note),
        font=load_font(17), fill=DIM)
    d.text((W - 120, 26), "実況", font=load_font(18, True), fill=ACC)

    jb = _japan_box(disk.width)
    S1 = 700
    im.paste(disk.resize((S1, S1), Image.Resampling.LANCZOS), (28, 104))
    d.rectangle([27, 103, 28 + S1, 104 + S1], outline=LINE)
    k = S1 / float(disk.width)
    d.rectangle([28 + jb[0] * k, 104 + jb[1] * k, 28 + jb[2] * k, 104 + jb[3] * k],
                outline=ACC, width=2)
    d.text((32, 104 + S1 + 8), "全円盤（東経140.7度の静止軌道から）・黄枠＝右の拡大範囲",
           font=load_font(15), fill=DIM)

    cw, ch = jb[2] - jb[0], jb[3] - jb[1]
    dw = 470
    dh = int(round(dw * ch / max(1, cw)))
    crop = disk.crop(jb).resize((dw, dh), Image.Resampling.LANCZOS)
    im.paste(crop, (768, 104))
    d.rectangle([767, 103, 768 + dw, 104 + dh], outline=LINE)
    d.text((770, 104 + dh + 8), "日本域（拡大）", font=load_font(15), fill=DIM)
    d.text((770, 104 + dh + 32),
           "※ 配信は 601px の全球画像のため、拡大すると粗くなる（高解像度は気象庁の地域画像）",
           font=load_font(13), fill=(120, 128, 146))

    marker_px = None
    if lat is not None and lon is not None:
        gx, gy = _geo_px(lat, lon, S1)
        d.ellipse([28 + gx - 9, 104 + gy - 9, 28 + gx + 9, 104 + gy + 9], outline=OKC, width=3)
        cx = (_geo_px(lat, lon, disk.width)[0] - jb[0]) * dw / max(1, cw)
        cy = (_geo_px(lat, lon, disk.width)[1] - jb[1]) * dh / max(1, ch)
        d.ellipse([768 + cx - 9, 104 + cy - 9, 768 + cx + 9, 104 + cy + 9], outline=OKC, width=3)
        d.text((768 + cx + 13, 104 + cy - 12),
               place or "{:.1f}N {:.1f}E".format(float(lat), float(lon)),
               font=load_font(17, True), fill=OKC)
        marker_px = [int(round(768 + cx)), int(round(104 + cy))]

    bar = Image.new("RGB", (24, 200))
    bd = ImageDraw.Draw(bar)
    for i in range(200):
        v = int(14 + 240 * i / 199)
        bd.line([(0, 199 - i), (23, 199 - i)], fill=(v, v, min(255, v + 6)))
    bx = 768 + dw + 28
    im.paste(bar, (bx, 104))
    d.text((bx + 34, 104), "明るい＝雲頂が高い（冷たい）", font=load_font(16), fill=FG)
    d.text((bx + 34, 130), "暗い＝海面・地表面", font=load_font(16), fill=DIM)

    d.text((28, H - 40), "出典: {} ／ 緯度経度→画素は静止軌道（東経140.7度）からの透視投影で換算".format(
        source or "気象庁 ひまわり9号"), font=load_font(14), fill=(120, 128, 146))

    verify = {"ok": True, "japan_box": list(jb), "crop_px": [dw, dh], "marker_px": marker_px}
    if marker_px:
        hit = pixel_near(im, tuple(marker_px), OKC, tol=40, r=6)
        verify["marker_pixels"] = int(hit)
        verify["ok"] = bool(hit >= 4)
    colors = crop.getcolors(maxcolors=20000)
    verify["crop_colors"] = len(colors) if colors else 20000
    verify["ok"] = bool(verify["ok"]) and verify["crop_colors"] >= 200
    return im, verify


def weather_satellite_now(satellite="himawari9", band="visible", size=550,
                          lat=None, lon=None, place=None):
    """世界の気象観測衛星のリアルタイム実画像を返す（認証不要）。

    例:「ひまわり9号の最新画像」「気象衛星の今の雲画像」「ひまわりの赤外線画像」
    現在は日本の静止気象衛星 ひまわり9号 のフルディスク実画像に対応（気象庁 MSC の
    リアルタイム画像。601×601 JPEG・10分間隔）。**夜の観測可否を判断する図**では
    可視光は真っ暗なので `band="infrared"`（または `"night_rgb"`）を使う。

    戻り: structuredContent に JSON（衛星・帯域・鮮度・画像URL/保存パス・実画像の検証結果）、
    content に表示用テキスト＋インライン画像。鮮度（観測時刻/取得時刻/遅延分）は必ず含める。
    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    画像本体より前に「🖼️ [生成した画像を開く: …](URL または file:///…)」という
    アイコン付きリンクを必ず出します。回答時はこのリンクをそのまま提示してください
    （画像が描画されない環境では唯一の導線）。

    Args:
        satellite: 衛星名。既定 himawari9。和名も可（ひまわり / ひまわり9号）。
            退役のひまわり8号は「表示できない」と正直に返す。
        band: 帯域。visible（可視光・昼のみ） / infrared（赤外・昼夜） /
            water_vapor（水蒸気） / night_rgb（夜間マイクロフィジクスRGB・夜の低層雲） /
            day_rgb（昼間マイクロフィジクスRGB） / true_color（自然色RGB）。
            旧名（"1d"/"3d"/"8d"/"vis"/"ir"/"wv" など）も受け付ける。
        size: 出力サイズpx（互換引数。気象庁 MSC は 601×601 固定、NICT は 550）。
            実際の大きさは structuredContent.size_px を見ること。
        lat: 観測地点の緯度（任意）。指定すると全円盤と日本域拡大の両方に地点マーカーを描く。
        lon: 観測地点の経度（任意。lat と併用）。
        place: 地点名（マーカーのラベル。任意。例 "東京"）。
    """
    size = as_int(size, 550, 200, 1100)
    try:
        lat = None if lat is None else as_float(lat, None, -90.0, 90.0)
        lon = None if lon is None else as_float(lon, None, -180.0, 180.0)
    except Exception:
        lat = lon = None
    if lat is None or lon is None:
        lat = lon = None

    band_key, band_spec = _resolve_band(band)
    if not band_spec:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "帯域 '{}' は対応していません。指定可能: ".format(band)
                + ", ".join("{}（{}）".format(k, _HIMAWARI_BANDS[k]["name"]) for k in _HIMAWARI_BANDS)
                + '。例: band="infrared"'))],
            structuredContent={"error": "invalid band", "band": band,
                               "available_bands": sorted(_HIMAWARI_BANDS.keys())},
        )

    canon = _resolve_himawari_name(satellite)
    if canon == "himawari8":
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "ひまわり8号は 2024年12月に運用を終了し、現在はひまわり9号が"
                '本運用中です。最新のリアルタイム画像をご希望なら satellite="himawari9"'
                "（または ひまわり9号）を指定してください。"))],
            structuredContent={"error": "retired satellite", "satellite": satellite,
                               "note": "Himawari-8 retired 2024-12; Himawari-9 is operational",
                               "active": "himawari9"},
        )
    if not canon:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "衛星 '{}' を特定できませんでした。現役のリアルタイム画像は"
                "ひまわり9号（himawari9 / ひまわり）のみ対応しています。".format(satellite)))],
            structuredContent={"error": "unknown satellite", "satellite": satellite,
                               "available": ["himawari9"]},
        )

    try:
        observed_iso, url, raw, quality = _latest_himawari_frame(band_spec["code"])
    except ValueError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=str(e))],
            structuredContent={"error": "frame not found", "source": "JMA MSC / himawari.asia",
                               "band": band_key},
        )
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text="ひまわり画像の取得に失敗しました: {}".format(e))],
            structuredContent={"error": str(e), "source": "JMA MSC"},
        )
    retrieved_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    provider = "JMA 気象衛星センター（MSC）" if "jma.go.jp" in url else "himawari.asia（NICT/Kochi ミラー）"
    try:
        from PIL import Image
        disk = Image.open(io.BytesIO(raw)).convert("RGB")
        panel, panel_verify = compose_panel(
            disk, observed_iso, band_spec["name"], band_spec["note"],
            lat=lat, lon=lon, place=place, source=provider)
        jpeg = encode_jpeg(panel)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像の変換に失敗しました: {}".format(e))],
            structuredContent={"error": str(e), "source": provider},
        )
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg",
                        altText="ひまわり9号 {} 最新画像（全円盤＋日本域）".format(band_spec["name"]))
    out_path = save_output(jpeg, "weather_satellite_now", "jpg")

    try:
        obs = datetime.datetime.fromisoformat(observed_iso.replace("Z", "+00:00"))
        ret = datetime.datetime.fromisoformat(retrieved_iso.replace("Z", "+00:00"))
        latency_min = round((ret - obs).total_seconds() / 60.0, 1)
    except Exception:
        latency_min = None
    obs_disp = observed_iso.replace("T", " ").replace("Z", "")

    latency_line = ("⏱ 配信遅延: 約 {} 分".format(latency_min)
                    if latency_min is not None else "⏱ 配信遅延: 測定不能")
    day_night = ("昼夜問わず使える（雲頂温度）" if band_spec["night_ok"]
                 else "昼間向け（夜は真っ暗になる。夜の観測可否は infrared / night_rgb を使う）")
    text_lines = [
        media_link_line("ひまわり9号 最新画像", path=out_path, kind="image"),
        "🛰 **ひまわり9号（HIMAWARI-9）** の最新実画像（{}）:".format(band_spec["name"]),
        "🕐 観測時刻: {}".format(obs_disp),
        latency_line,
        "🎛 帯域: {}（{}）・{}".format(band_spec["name"], band_spec["note"], day_night),
        "🗾 図: 左＝全円盤／右＝日本域拡大{}{}・配信画像 {}×{}px".format(
            "・地点マーカー {}".format(place or "{:.2f}N {:.2f}E".format(lat, lon))
            if lat is not None else "",
            "" if panel_verify.get("marker_pixels") is None
            else "（画素 {} 点）".format(panel_verify["marker_pixels"]),
            quality["size"][0] if quality.get("size") else "?",
            quality["size"][1] if quality.get("size") else "?"),
        "🛰 位置: 静止軌道 東経140.7度・赤道上（約35,800 km）",
        "✅ 実画像の検証: {}（{}・{}色）".format(
            "実画像" if quality.get("ok") else "プレースホルダの疑い",
            "{:,} バイト".format(quality.get("bytes") or 0), quality.get("colors") or 0),
        "",
        "### ⚠️ 鮮度の注記",
        "- 観測時刻は上記のとおり。ひまわり9号は10分ごとにフルディスクを撮影し、配信に数分かかります",
        "- 静止衛星のため日本付近（東経140.7度）を常時カバー。南北アメリカ・欧州等は他の衛星（GOES/Meteosat）の対象",
        "- 画像URL: {}（このURLは観測時刻固定のため、時間が経つと無効になります。最新を再取得するには再実行）".format(url),
        "出典: 気象庁 ひまわり9号（JMA）／ 配信 {}".format(provider),
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(text_lines)), imgc],
        structuredContent={
            "satellite": "HIMAWARI-9", "agency": "JMA/JAXA", "country": "日本",
            "band": band_key, "band_name": band_spec["name"], "band_code": band_spec["code"],
            "band_night_ok": bool(band_spec["night_ok"]),
            "region": "full-disk (geostationary 140.7E)",
            "observed_at_utc": observed_iso, "retrieved_at_utc": retrieved_iso,
            "latency_min": latency_min, "size_px": [panel.width, panel.height],
            "source_size_px": quality.get("size") or [size, size],
            "image_url": url, "image_path": out_path,
            "image_verified": quality, "panel": panel_verify,
            "site": ({"place": place, "lat": lat, "lon": lon}
                     if lat is not None else None),
            "provider": provider,
            "source": "JMA Himawari-9 (MSC real-time imagery)" if "jma.go.jp" in url
                      else "JMA Himawari-9 via himawari.asia (NICT/Kochi mirror, no auth)",
        },
    )

# GEO/LEO拡張プロバイダ。URLは実取得検証済み。
_EXT_UNAVAILABLE={"meteor-mn2-3":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-mn2-4":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-m-n2-3":("unavailable","Roscosmosの固定公開画像URLなし"),"meteor-m-n2-4":("unavailable","Roscosmosの固定公開画像URLなし"),"dmsp-f17":("restricted","DoD運用・公開画像なし"),"dmsp-f18":("restricted","DoD運用・公開画像なし"),"wsf-m1":("restricted","DoD運用・公開画像なし"),"cosmic-2":("non_image_product","GNSS-ROの数値プロファイルで画像なし"),"triton":("non_image_product","GNSS-ROの数値プロファイルで画像なし")}
_EXT_ALIAS={"ひまわり":"himawari9","ひまわり9号":"himawari9","gk2a":"gk2a","千里眼2a":"gk2a","しずく":"gcom-w","gcom-w1":"gcom-w"}
def _ext_canon(v):
 s=" ".join(str(v or "").lower().strip().split()).replace("号","").replace("_","-").replace(" ","");return {"goes-19":"goes19","goes-18":"goes18","noaa-20":"noaa20","noaa-21":"noaa21","snpp":"snpp","metop-b":"metopb","metop-c":"metopc","fy-3d":"fy3d","fy-3f":"fy3f","fy-4b":"fy4b","earth-care":"earthcare"}.get(s,_EXT_ALIAS.get(s,s))
def _ext_get(u,timeout=60):
 r=requests.get(u,headers=_UA,timeout=(10,timeout));return (u,r.content) if r.status_code==200 and "image" in r.headers.get("Content-Type","") and r.content else None
def _ext_gibs(layer,size=1000):
 now=datetime.datetime.now(datetime.timezone.utc)
 for back in range(4):
  day=(now-datetime.timedelta(days=back)).strftime("%Y-%m-%d");u="https://wvs.earthdata.nasa.gov/api/v1/snapshot?REQUEST=GetSnapshot&LAYERS="+layer+"&CRS=EPSG:4326&TIME="+day+"&WIDTH="+str(size)+"&HEIGHT="+str(size)+"&FORMAT=image/jpeg&BBOX=-180,-90,180,90";x=_ext_get(u,90)
  if x and len(x[1])>10000:return x[0],x[1],day
 raise ValueError("NASA GIBS画像なし")
def _ext_eum(layer,lon,size=1200):
 u="https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap&layers="+layer+",backgrounds:ne_10m_coastline&bbox=-6500000,-6500000,6500000,6500000&width="+str(size)+"&height="+str(size)+"&srs=AUTO:97004,9001,"+str(lon)+",0&styles=&format=image/jpeg&bgcolor=0xCCCCCC";x=_ext_get(u,90)
 if not x:raise ValueError("EUMETSAT画像なし")
 return x[0],x[1],None
def _ext_fetch(s,size):
 if s in {"goes19","goes18"}:
  sat="GOES19" if s=="goes19" else "GOES18";u="https://cdn.star.nesdis.noaa.gov/"+sat+"/ABI/FD/GEOCOLOR/1808x1808.jpg";x=_ext_get(u,60)
  if not x:raise ValueError("NOAA STAR画像なし")
  return x[0],x[1],None,"GeoColor","NOAA/NESDIS STAR"
 if s in {"noaa20","noaa21","snpp"}:
  layer={"noaa20":"VIIRS_NOAA20_CorrectedReflectance_TrueColor","noaa21":"VIIRS_NOAA21_CorrectedReflectance_TrueColor","snpp":"VIIRS_SNPP_CorrectedReflectance_TrueColor"}[s];u,b,d=_ext_gibs(layer,min(size,1000));return u,b,d,"VIIRS true color","NASA GIBS"
 if s in {"metopb","metopc"}:
  u,b,d=_ext_eum("eps:m02_rgb_124" if s=="metopb" else "eps:m03_rgb_124",0,min(size,1400));return u,b,d,"RGB 124","EUMETSAT EPS WMS"
 paths={"fy3d":"FY3D/MIPS/FY3D_MERSI_GLOBAL.jpg","fy3f":"FY3F/THUMBNAIL/FY3F_MERSI_TPW_ASC.jpg","fy4b":"FY4B/AGRI/THUMBNAIL/FY4B_AGRI_DISK_GCLR.jpg"}
 if s in paths:
  x=_ext_get("https://img.nsmc.org.cn/CLOUDIMAGE/"+paths[s],60)
  if not x:raise ValueError("NSMC画像なし")
  return x[0],x[1],None,s,"CMA/NSMC"
 if s=="earthcare":
  now=datetime.datetime.now(datetime.timezone.utc);day=now.strftime("%Y/%m/%d");ds=now.strftime("%Y%m%d")
  for orbit in ("13058B","13057B","13059B","13056B"):
   u="https://www.eorc.jaxa.jp/EARTHCARE/Quicklook/data/CPR_ECO/integrated_radar_reflectivity_10km/"+day+"/CPR_ECO_integrated_radar_reflectivity_10km_"+ds+"_"+orbit+".jpg";x=_ext_get(u,60)
   if x:return x[0],x[1],None,"CPR reflectivity swath","JAXA/EORC QuickLook"
  raise ValueError("EarthCARE QuickLookなし")
 raise ValueError("拡張プロバイダ未実装")
def weather_satellite_now_extended(satellite="himawari9",band="visible",size=550,lat=None,lon=None,place=None):
 size=as_int(size,550,200,1808);s=_ext_canon(satellite)
 if s in _EXT_UNAVAILABLE:
  code,msg=_EXT_UNAVAILABLE[s];return CallToolResult(content=[TextContent(type="text",text=msg)],structuredContent={"error":code,"satellite":satellite,"message":msg})
 if s=="himawari9":return weather_satellite_now(satellite=satellite,band=band,size=size,lat=lat,lon=lon,place=place)
 try:u,b,o,product,source=_ext_fetch(s,size);from PIL import Image;im=Image.open(io.BytesIO(b)).convert("RGB");jpg=encode_jpeg(im)
 except (ValueError,requests.RequestException) as e:return CallToolResult(content=[TextContent(type="text",text=str(e))],structuredContent={"error":"fetch_failed","satellite":satellite,"message":str(e)})
 except Exception as e:return CallToolResult(content=[TextContent(type="text",text=str(e))],structuredContent={"error":"image_decode_failed","satellite":satellite})
 retrieved=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z");path=save_output(jpg,"weather_satellite_now","jpg");text="\n".join([media_link_line("",url=u,kind="image"),"🛰 **"+str(satellite)+"**（"+product+"）","観測: "+str(o or "配信元非公開"),"取得: "+retrieved,"出典: "+source])
 payload={"satellite":satellite,"canonical":s,"product":product,"source":source,"observed_at_utc":o,"retrieved_at_utc":retrieved,"image_url":u,"image_path":path,"size_px":[im.width,im.height],"image_semantics":"image_product"}
 return CallToolResult(content=[TextContent(type="text",text=text),ImageContent(type="image",data=base64.b64encode(jpg).decode("ascii"),mimeType="image/jpeg",altText=str(satellite)+" 気象衛星画像")],structuredContent=payload)

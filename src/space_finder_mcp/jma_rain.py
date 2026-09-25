# -*- coding: utf-8 -*-
"""気象庁（JMA）の雨雲・降水画像（認証不要・タイル／ラスタ）。

天体観測の可否判断に「雨雲・降水の分布」を画像で添えるためのモジュール。
気象庁が公開する認証不要の2系統を使う:

1. ナウキャスト（雨雲の動き・雷・竜巻）
   .../jmatile/data/nowc/{basetime}/none/{validtime}/surf/{hrpns|thns|trns}/{z}/{x}/{y}.png
   5分更新・1時間先まで。背景地図は地理院タイル（気象庁のプロキシ経由）。
   タイルは **偶数ズームにのみ存在する**（気象庁仕様 zoomUse="even"）。
2. 解析雨量・降水短時間予報（rasrf ページと同一の画像ファイル）
   .../rain/data/{ra|ra30|srf|srf15}/{base}/rain01_{base}_f{ft:02d}_a{area}.png
   940x783。ft=0(実況) / 1〜6(6時間予報) / 7〜15(15時間予報)。
   base は **UTC 表記で、その数字をそのままファイル名に使う**（JST で組むと404）。

規約:
- 例外をツール外へ漏らさない（取得失敗は None。呼び出し側は画像なしで続行する）。
- 描画した図は structuredContent.figure（schema="figure/1"、注記は数値から生成）。
- **降水は「雲の有無」の目安であって雲量そのものではない**旨を注記に明示する。
- 一部のパネル／タイルだけ取得できなかった場合は黙って落とさず、件数を注記に出す。
"""
from __future__ import annotations

import datetime
import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence

import requests

from .cache import TTL_SHORT, ttl_cache
from .img_common import (encode_jpeg, figure_payload, load_font, pixel_near,
                         scale_spec, view_spec)

_UA = {"User-Agent": "space-finder-mcp/0.30 (MCP; JMA rain imagery)"}
_RAIN_ROOT = "https://www.jma.go.jp/bosai/rain/data"
_NOWC_ROOT = "https://www.jma.go.jp/bosai/jmatile/data/nowc"
_GSI_TILE = "https://www.jma.go.jp/tile/gsi/pale"
_RAIN_PAGE = "https://www.jma.go.jp/bosai/rain/rasrf.html"
_NOWC_PAGE = "https://www.jma.go.jp/bosai/nowc/"
_RAIN_W, _RAIN_H = 940, 783        # 解析雨量・降水予報の画像サイズ（全国・地方とも同一）
_NOWCAST_Z = 8                     # 偶数ズームのみ（気象庁仕様 zoomUse="even"）
_NOWCAST_TILES = 3                 # 地点を中心とした 3x3 タイル（768px）
_JST = datetime.timezone(datetime.timedelta(hours=9))
_FOOTER = ("出典: 気象庁 ナウキャスト／解析雨量・降水短時間予報 "
           "／ 背景地図: 地理院タイル（加工して利用）")

# 解析雨量・降水予報の地域画像（気象庁 rasrf ページ内の範囲表と同一。
# (id, 名称, (緯度min, 緯度max, 経度min, 経度max)) 。a00=全国のみ例外的に広い）
AREAS: tuple = (
    ("00", "全国", (21.424, 46.576, 114.773, 155.227)),
    ("05", "北海道地方（北西部）", (41.460, 45.958, 138.556, 146.022)),
    ("04", "北海道地方（東部）", (41.251, 45.749, 140.817, 148.257)),
    ("06", "北海道地方（南西部）", (40.334, 44.832, 137.613, 144.942)),
    ("17", "東北地方（北部）", (37.668, 42.166, 137.485, 144.521)),
    ("18", "東北地方（南部）", (35.751, 40.249, 137.063, 143.912)),
    ("09", "関東地方", (33.082, 37.582, 136.660, 143.276)),
    ("11", "甲信地方", (33.584, 38.082, 135.643, 142.300)),
    ("19", "東海地方", (32.334, 36.832, 135.102, 141.657)),
    ("07", "北陸地方（東部）", (35.334, 39.832, 135.662, 142.473)),
    ("08", "北陸地方（西部）", (34.082, 38.582, 133.792, 140.491)),
    ("10", "近畿地方", (32.418, 36.916, 132.349, 138.911)),
    ("02", "中国地方", (32.584, 37.084, 129.677, 136.252)),
    ("16", "四国地方", (31.501, 35.999, 130.462, 136.953)),
    ("12", "九州地方（北部）", (31.168, 35.666, 127.389, 133.855)),
    ("13", "九州地方（南部）", (29.001, 33.499, 127.953, 134.266)),
    ("01", "奄美地方", (26.168, 30.666, 126.860, 132.996)),
    ("14", "沖縄本島", (24.001, 28.499, 125.409, 131.427)),
    ("03", "大東島", (23.834, 28.332, 126.913, 132.922)),
    ("15", "宮古・八重山地方", (22.334, 26.832, 121.611, 127.545)),
)

# 降水強度の公式凡例（mm/h。タイルもラスタも同じパレット。弱い色から順に）
LEGEND: tuple = (
    ("〜1", (242, 242, 255)),
    ("1", (160, 210, 255)),
    ("5", (33, 140, 255)),
    ("10", (0, 65, 255)),
    ("20", (255, 245, 0)),
    ("30", (255, 153, 0)),
    ("50", (255, 40, 0)),
    ("80以上", (180, 0, 104)),
)


# ---------- 取得（例外を外へ漏らさない） ----------

def _http_bytes(url: str) -> Optional[bytes]:
    """GET して本文を返す（失敗・空は None）。"""
    try:
        r = requests.get(url, headers=_UA, timeout=(30, 30))
        if r.status_code != 200 or not r.content:
            return None
        return r.content
    except requests.RequestException:
        return None


def _http_json(url: str):
    """GET して JSON を返す（失敗は None）。"""
    b = _http_bytes(url)
    if not b:
        return None
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except ValueError:
        return None


@ttl_cache(TTL_SHORT, maxsize=128, skip_if=lambda v: v is None)
def _cached_image(url: str) -> Optional[bytes]:
    """画像として妥当なバイト列だけを返す（HTML エラーページを弾く）。

    URL 単位のキャッシュ（規約: ツール呼び出し単位ではなく取得単位）。
    エラー（None）はキャッシュしない。
    """
    b = _http_bytes(url)
    if not b:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(b)) as im:
            if im.size[0] < 8 or im.size[1] < 8:
                return None
        return b
    except (OSError, ValueError):
        return None


@ttl_cache(TTL_SHORT, maxsize=8, skip_if=lambda v: v is None)
def _rain_base(kind: str = "srf15") -> Optional[str]:
    """解析雨量・降水予報の最新の解析時刻（UTC桁 YYYYMMDDHHMMSS）。

    time.json の "time" は UTC 表記（+00:00）で、**ファイル名にはその数字を
    そのまま使う**（JST 桁で URL を組むと 404 になる）。
    """
    data = _http_json("{}/{}/time.json".format(_RAIN_ROOT, kind))
    if not isinstance(data, dict):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})",
                 str(data.get("time") or ""))
    return "".join(m.groups()) if m else None


@ttl_cache(TTL_SHORT, maxsize=8, skip_if=lambda v: v is None)
def _nowcast_times() -> Optional[dict]:
    """ナウキャストの最新解析時刻と、最長（+60分）予報時刻。"""
    n1 = _http_json("{}/targetTimes_N1.json".format(_NOWC_ROOT))
    n2 = _http_json("{}/targetTimes_N2.json".format(_NOWC_ROOT))
    if not isinstance(n1, list) or not n1:
        return None
    base = max(str(r.get("basetime") or "") for r in n1)
    if len(base) != 14:
        return None
    fc = sorted({str(r.get("validtime") or "") for r in (n2 or [])
                 if str(r.get("basetime") or "") == base and r.get("validtime")})
    return {"basetime": base, "forecast": fc[-1] if fc else base}


def _base_jst(base: str) -> datetime.datetime:
    """UTC桁の base を JST の datetime に変換する。"""
    d = datetime.datetime.strptime(base, "%Y%m%d%H%M%S")
    return d.replace(tzinfo=datetime.timezone.utc).astimezone(_JST)


def _parse_jst(value) -> Optional[datetime.datetime]:
    """観測時間帯の時刻文字列（ローカル＝JST とみなす）を datetime にする。"""
    try:
        d = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_JST)
    return d.astimezone(_JST)


# ---------- 地域画像（解析雨量・降水短時間予報） ----------

def _pick_area(lat: float, lon: float) -> Optional[tuple]:
    """地点を含む地方画像を選ぶ。範囲内になければ None（＝対応範囲外）。

    地方画像同士は重なるため「範囲中心が地点に最も近いもの」を選ぶ
    （面積最小で選ぶと、地点が画像の端に寄ってしまうことがある）。
    """
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    inside = [a for a in AREAS if a[0] != "00"
              and a[2][0] <= lat <= a[2][1] and a[2][2] <= lon <= a[2][3]]
    if inside:
        return min(inside, key=lambda a: abs((a[2][0] + a[2][1]) / 2 - lat)
                   + abs((a[2][2] + a[2][3]) / 2 - lon))
    z = AREAS[0]
    if z[2][0] <= lat <= z[2][1] and z[2][2] <= lon <= z[2][3]:
        return z
    return None


def _point_px(lat: float, lon: float, area: tuple) -> tuple:
    """気象庁公開ページと同一の緯度経度→画素の線形換算（940x783 基準）。"""
    lat_min, lat_max, lon_min, lon_max = area[2]
    x = (lon - lon_min) / (lon_max - lon_min) * _RAIN_W
    y = (lat_max - lat) / (lat_max - lat_min) * _RAIN_H
    return x, y


def _rain_panel_kind(ft: int) -> str:
    """予報ステップ（時間）から取得種別を決める。"""
    if ft <= 0:
        return "ra"
    return "srf" if ft <= 6 else "srf15"


def _rain_image_url(base: str, ft: int, area_id: str) -> str:
    return "{}/{}/{}/rain01_{}_f{:02d}_a{}.png".format(
        _RAIN_ROOT, _rain_panel_kind(ft), base, base, ft, area_id)


def _forecast_steps(base_jst: datetime.datetime,
                    targets: Sequence) -> tuple:
    """観測時間帯から予報ステップ（+1〜+15時間）を最大3つ選ぶ。

    戻り値: (steps, notes)。観測時間帯が予報範囲外のときは既定値で補い、
    その旨を注記（数値から生成）として返す。
    """
    steps: list = []
    out_of_range = 0
    for t in list(targets or []):
        d = _parse_jst(t)
        if d is None:
            continue
        ft = int(round((d - base_jst).total_seconds() / 3600.0))
        if 1 <= ft <= 15:
            if ft not in steps:
                steps.append(ft)
        else:
            out_of_range += 1
    notes: list = []
    if out_of_range:
        notes.append("観測時間帯のうち{}つは降水予報の対象範囲（+15時間先まで）を"
                     "超えているため、画像には含まれていません。".format(out_of_range))
    before = len(steps)
    for d in (6, 9, 12):
        if len(steps) >= 3:
            break
        if d not in steps:
            steps.append(d)
    if before == 0 and steps:
        notes.append("観測時間帯が降水予報の対象範囲（+15時間先まで）にないため、"
                     "予報時刻は既定の+6/+9/+12時間を示します。")
    elif before < 3 and len(steps) > before:
        notes.append("予報時刻は既定の+6/+9/+12時間で補いました（観測時間帯に対応"
                     "する時刻が{}つしかないため）。".format(before))
    return sorted(steps), notes


def _draw_marker(d, x: float, y: float, *, label: str = "", font=None) -> None:
    """観測地点の十字マーカー（白縁＋黒）を描く。"""
    r = 13
    d.line([(x - r, y), (x + r, y)], fill=(255, 255, 255), width=6)
    d.line([(x, y - r), (x, y + r)], fill=(255, 255, 255), width=6)
    d.line([(x - r + 1, y), (x + r - 1, y)], fill=(20, 20, 20), width=3)
    d.line([(x, y - r + 1), (x, y + r - 1)], fill=(20, 20, 20), width=3)
    if label and font is not None:
        d.text((x + r + 4, y - r - 14), label, font=font, fill=(20, 20, 20),
               stroke_width=3, stroke_fill=(255, 255, 255))


def _draw_legend(d, x: int, y: int, font, font_sm) -> None:
    """降水強度の凡例（気象庁公式の色と値）を描く。"""
    d.text((x, y - 26), "降水強度", font=font_sm, fill=(205, 210, 220))
    d.text((x + 56, y - 26), "mm/h", font=font_sm, fill=(150, 155, 168))
    for i, (lab, col) in enumerate(reversed(LEGEND)):
        yy = y + i * 30
        d.rectangle([x, yy, x + 44, yy + 27], fill=col, outline=(70, 70, 80))
        d.text((x + 52, yy + 4), lab, font=font_sm, fill=(225, 228, 238))


def _compose_rain(panels: list, px: tuple, name: str, area: tuple) -> tuple:
    """解析雨量・降水予報のパネルを1枚に合成し、(JPEGバイト, 検証dict) を返す。

    パネルは 940x783 → 470px に縮小して 2x2 に並べる。地点マーカーと公式凡例を
    重ねる。検証は合成後の画像に対して画素で行う（マーカー・非一様性）。
    """
    from PIL import Image, ImageDraw

    S, PAD, HEAD, LBL, FOOT, LEGW = 470, 24, 112, 42, 112, 112
    w = PAD * 3 + S * 2 + LEGW
    h = HEAD + PAD + (LBL + S + PAD) * 2 + FOOT
    cv = Image.new("RGB", (w, h), (17, 17, 21))
    d = ImageDraw.Draw(cv)
    f_l = load_font(23)
    f_f = load_font(18, bold=False)
    f_sm = load_font(16, bold=False)
    d.text((PAD, PAD - 6), "🌧 気象庁 解析雨量・降水短時間予報 — {}（{}）".format(
        name, area[1]), font=load_font(30), fill=(245, 245, 250))
    d.text((PAD, PAD + 48),
           "レーダー・アメダス解析による降水分布。実況と今後の予報（15時間先まで）。",
           font=f_f, fill=(165, 170, 182))
    k = S / _RAIN_W
    ok = True
    for i, p in enumerate(panels[:4]):
        r, c = divmod(i, 2)
        x = PAD + (S + PAD) * c
        y = HEAD + PAD + r * (LBL + S + PAD)
        d.text((x + 2, y + 10), "{} {}".format(p["title"], p["disp"]),
               font=f_l, fill=(255, 214, 120))
        im = Image.open(io.BytesIO(p["bytes"])).convert("RGBA")
        rs = im.resize((S, S), Image.Resampling.LANCZOS)
        cv.paste(rs, (x, y + LBL), rs)   # マスクは貼る画像自身（縮小後）
        d.rectangle([x - 1, y + LBL - 1, x + S, y + LBL + S], outline=(72, 72, 84))
        mx, my = x + px[0] * k, y + LBL + px[1] * k
        _draw_marker(d, mx, my, label=name, font=f_sm)
        # 自己検証: マーカー近傍に白十字があるか・パネルが一様でないか
        if pixel_near(cv, (mx, my), (255, 255, 255), tol=60, r=6) < 8:
            ok = False
        if len(set(im.resize((64, 64)).convert("RGB").getdata())) < 16:
            ok = False
    _draw_legend(d, PAD + S + PAD + S + PAD, HEAD + PAD + LBL, load_font(20), f_sm)
    d.text((PAD, h - FOOT + 14), _FOOTER, font=f_f, fill=(160, 165, 178))
    d.text((PAD, h - FOOT + 40),
           "※ 降水は「雲がある」ことの目安であり、雲量そのものではありません。雲量は予報本文を参照してください。",
           font=f_f, fill=(160, 165, 178))
    d.text((PAD, h - FOOT + 66),
           "※ 地点マーカーは気象庁公開ページと同一の緯度経度→画素換算（簡易換算）。",
           font=f_f, fill=(160, 165, 178))
    verify = {"ok": bool(ok), "panels": len(panels[:4])}
    return encode_jpeg(cv), verify


# ---------- ナウキャスト（雨雲の動き・雷） ----------

def _tile_xy(lat: float, lon: float, z: int) -> tuple:
    """緯度経度 → Webメルカトルのタイル座標（連続値）。"""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _nowcast_mosaic(lat: float, lon: float, validtime: str, basetime: str,
                    element: str, base_img=None, n: int = _NOWCAST_TILES) -> tuple:
    """地点まわりのタイルを合成し、(背景画像, 要素画像, マーカー画素, 失敗数) を返す。

    base_img を渡すと背景地図（時刻で変わらない）を再利用する。
    """
    from PIL import Image
    z = _NOWCAST_Z
    xf, yf = _tile_xy(lat, lon, z)
    x0, y0 = int(xf) - n // 2, int(yf) - n // 2
    jobs = [(x, y) for x in range(x0, x0 + n) for y in range(y0, y0 + n)]
    elem_urls = ["{}/{}/none/{}/surf/{}/{}/{}/{}.png".format(
        _NOWC_ROOT, basetime, validtime, element, z, x, y) for x, y in jobs]
    with ThreadPoolExecutor(8) as ex:
        elem = list(ex.map(lambda u: _cached_image(u), elem_urls))
    if base_img is None:
        base_urls = ["{}/{}/{}/{}.png".format(_GSI_TILE, z, x, y) for x, y in jobs]
        with ThreadPoolExecutor(8) as ex:
            base = list(ex.map(lambda u: _cached_image(u), base_urls))
        base_img = Image.new("RGB", (256 * n, 256 * n), (235, 235, 235))
        for (x, y), b in zip(jobs, base):
            if b:
                base_img.paste(Image.open(io.BytesIO(b)).convert("RGB"),
                               ((x - x0) * 256, (y - y0) * 256))
    elem_img = Image.new("RGBA", (256 * n, 256 * n), (0, 0, 0, 0))
    failed = 0
    for (x, y), b in zip(jobs, elem):
        if not b:
            failed += 1
            continue
        elem_img.alpha_composite(Image.open(io.BytesIO(b)).convert("RGBA"),
                                 ((x - x0) * 256, (y - y0) * 256))
    return base_img, elem_img, ((xf - x0) * 256, (yf - y0) * 256), failed


def _alpha_px(im) -> int:
    """RGBA画像の不透明画素数（降水・雷の有無の判定に使う）。"""
    try:
        return sum(1 for v in im.getchannel("A").getdata() if v > 0)
    except (OSError, ValueError):
        return 0


def _compose_nowcast(panels: list, px: tuple, name: str) -> tuple:
    """ナウキャストの2パネル（実況／+60分）を1枚に合成し、(JPEG, 検証dict) を返す。"""
    from PIL import Image, ImageDraw, ImageOps

    S, PAD, HEAD, LBL, FOOT, LEGW = 660, 26, 118, 44, 112, 104
    w = PAD * 3 + S * 2 + LEGW
    h = HEAD + PAD + LBL + S + PAD + FOOT
    cv = Image.new("RGB", (w, h), (17, 17, 21))
    d = ImageDraw.Draw(cv)
    f_l = load_font(24)
    f_f = load_font(18, bold=False)
    f_sm = load_font(16, bold=False)
    d.text((PAD, PAD - 6), "🌧 気象庁ナウキャスト（雨雲の動き） — {}".format(name),
           font=load_font(34), fill=(245, 245, 250))
    d.text((PAD, PAD + 52), "高解像度降水ナウキャスト：5分ごと更新・1時間先まで。",
           font=f_f, fill=(165, 170, 182))
    k = S / (256 * _NOWCAST_TILES)
    ok = True
    for i, p in enumerate(panels[:2]):
        x = PAD + (S + PAD) * i
        y = HEAD + PAD
        d.text((x + 2, y + 12), p["title"], font=f_l, fill=(255, 214, 120))
        base = p["base"].resize((S, S), Image.Resampling.LANCZOS)
        rain = p["rain"].resize((S, S), Image.Resampling.LANCZOS)
        b = ImageOps.grayscale(base).point(lambda v: int(v * 0.62 + 40)).convert("RGB")
        a = rain.getchannel("A").point(lambda v: int(v * 0.85))
        rain.putalpha(a)
        b.paste(rain, (0, 0), rain)
        cv.paste(b, (x, y + LBL))
        d.rectangle([x - 1, y + LBL - 1, x + S, y + LBL + S], outline=(72, 72, 84))
        mx, my = x + px[0] * k, y + LBL + px[1] * k
        _draw_marker(d, mx, my, label=name, font=f_sm)
        if pixel_near(cv, (mx, my), (255, 255, 255), tol=60, r=6) < 8:
            ok = False
        if len(set(b.resize((64, 64)).convert("RGB").getdata())) < 16:
            ok = False
    _draw_legend(d, PAD + S + PAD + S + PAD, HEAD + PAD + LBL, load_font(19), f_sm)
    d.text((PAD, h - FOOT + 14), _FOOTER, font=f_f, fill=(160, 165, 178))
    d.text((PAD, h - FOOT + 40),
           "※ 地点の十字マーカーが観測予定地。タイルは気象庁の仕様により偶数ズーム(z=8)を使用。",
           font=f_f, fill=(160, 165, 178))
    d.text((PAD, h - FOOT + 66),
           "※ 雲量そのものではありません（降水の有無・強度）。快晴でも雲量が多ければ観測はできません。",
           font=f_f, fill=(160, 165, 178))
    verify = {"ok": bool(ok), "panels": len(panels[:2])}
    return encode_jpeg(cv), verify


# ---------- 公開エントリ ----------

def rain_images(lat: float, lon: float, name: str,
                obs_times: Sequence = ()) -> Optional[dict]:
    """日本国内の地点の雨雲・降水画像を2枚作る。

    戻り値: {"images": [{"id","label","alt","bytes","page_url"}...],
             "payload": {...}, "figure": {...}}
    対応範囲外（日本国内でない）または必要な画像が1枚も取れないときは None。
    呼び出し側は None のとき画像なしで続行する。

    obs_times: 観測時間帯の時刻文字列（ローカル=JST）。予報パネルの時刻を
               これに合わせて選ぶ（+1〜+15時間の範囲で）。
    """
    area = _pick_area(lat, lon)
    if area is None:
        return None
    base = _rain_base("srf15")
    if not base:
        return None
    base_jst = _base_jst(base)
    steps, notes = _forecast_steps(base_jst, obs_times)

    # --- 画像1: 解析雨量（実況）＋ 降水短時間予報 ---
    spec = [(0, "解析雨量（実況）", base_jst)]
    for ft in steps:
        t = base_jst + datetime.timedelta(hours=ft)
        title = ("降水短時間予報 +{}h" if ft <= 6 else "降水15時間予報 +{}h").format(ft)
        spec.append((ft, title, t))
    with ThreadPoolExecutor(4) as ex:
        blobs = list(ex.map(lambda s: _cached_image(
            _rain_image_url(base, s[0], area[0])), spec))
    panels = []
    missing = 0
    for (ft, title, t), b in zip(spec, blobs):
        if not b:
            missing += 1
            continue
        panels.append({"bytes": b, "title": title,
                       "disp": t.strftime("%m/%d %H:%M"), "ft": ft})
    if not panels:
        return None
    if missing:
        notes.append("降水画像のうち{}枚を取得できませんでした（提供元の更新待ちの"
                     "可能性があります）。".format(missing))
    px = _point_px(lat, lon, area)
    rain_jpeg, rain_verify = _compose_rain(panels, px, str(name or ""), area)

    # --- 画像2: ナウキャスト（雨雲の動き・実況／+60分・雷の確認） ---
    nowc = _nowcast_times()
    nowc_payload: dict = {}
    nowc_entry = None
    if nowc:
        bt, vt = nowc["basetime"], nowc["forecast"]
        try:
            b0, r0, mpx, f0 = _nowcast_mosaic(lat, lon, bt, bt, "hrpns")
            b1, r1, _, f1 = _nowcast_mosaic(lat, lon, vt, bt, "hrpns", base_img=b0)
            # 雷(thns)は 10分に丸めた basetime にのみ存在する（5分刻みは404）
            bt10 = bt[:10] + "{:02d}".format(int(bt[10:12]) // 10 * 10) + bt[12:]
            _, th, _, fth = _nowcast_mosaic(lat, lon, bt10, bt10, "thns", base_img=b0)
            if f0 + f1 == 0:
                dvt = _base_jst(vt)
                nowc_panels = [
                    {"base": b0, "rain": r0,
                     "title": "実況（解析） {}".format(base_jst.strftime("%m/%d %H:%M"))},
                    {"base": b1, "rain": r1,
                     "title": "予測（+60分） {}".format(dvt.strftime("%m/%d %H:%M"))},
                ]
                nowc_jpeg, nowc_verify = _compose_nowcast(
                    nowc_panels, mpx, str(name or ""))
                thunder_px = _alpha_px(th) if th is not None else 0
                nowc_entry = {"id": "nowcast", "bytes": nowc_jpeg,
                              "label": "気象庁ナウキャスト（雨雲の動き）",
                              "alt": "気象庁ナウキャスト（雨雲の動き・{}付近）".format(name),
                              "page_url": _NOWC_PAGE, "verify": nowc_verify}
                nowc_payload = {
                    "basetime": bt, "forecast": vt, "thunder_px": thunder_px,
                    "thunder_tiles_failed": fth,
                    "source": "JMA nowcast (hrpns/thns) + GSI tiles",
                }
                if fth:
                    notes.append("雷活動度（雷ナウキャスト）のタイル{}枚を取得できなかった"
                                 "ため、雷の有無は確認できていません。".format(fth))
                elif thunder_px > 0:
                    notes.append("⚠️ この範囲で雷活動度が検出されています（実況、"
                                 "不透明画素{}px）。画像には重ねていません。屋外観測の際は"
                                 "気象庁の公式ページでも雷の状況を確認してください。"
                                 .format(thunder_px))
        except (OSError, ValueError):
            nowc_entry = None

    span_lat = area[2][1] - area[2][0]
    span_lon = area[2][3] - area[2][2]
    if area[0] == "00":
        notes.insert(0, "地点マーカーは気象庁公開ページと同一の緯度経度→画素換算"
                        "（全国画像・緯度{:.1f}°×経度{:.1f}°）。投影の簡易換算のため"
                        "誤差は数十km程度。地点周辺はナウキャスト画像を参照してください。"
                        .format(span_lat, span_lon))
    else:
        notes.insert(0, "地点マーカーは気象庁公開ページと同一の緯度経度→画素換算"
                        "（{}・緯度{:.1f}°×経度{:.1f}°）。誤差は十数km程度です。"
                        .format(area[1], span_lat, span_lon))
    notes.append("降水強度の色は気象庁公式の凡例（mm/h）。降水は「雲がある」ことの"
                 "目安であり、雲量そのものではありません（無降水でも曇る）。")
    notes.append("解析雨量は {} JST 時点。降水予報は +1〜+15時間先（{}〜{} JST）まで。"
                 .format(base_jst.strftime("%m/%d %H:%M"),
                         (base_jst + datetime.timedelta(hours=1)).strftime("%m/%d %H:%M"),
                         (base_jst + datetime.timedelta(hours=15)).strftime("%m/%d %H:%M")))
    figure = figure_payload(
        kind="weather_rain_panels",
        title="気象庁の雨雲・降水（{}）".format(name),
        view=view_spec("地上降水量の分布（レーダー・アメダス解析と予報）",
                       "緯度経度→画素の線形換算（気象庁公開ページと同一）",
                       "解析雨量・降水短時間予報（rasrf）の地方画像を並べたもの"),
        scale=scale_spec("regional_raster", to_scale=True,
                         px_per_unit=round(_RAIN_W / span_lon, 2), unit="deg"),
        markers=[{"name": str(name or ""), "lat": round(float(lat), 4),
                  "lon": round(float(lon), 4),
                  "note": "黒十字マーカー（簡易換算）"}],
        notes=notes,
        caption="解析雨量（実況）と降水短時間予報のパネルに観測地点のマーカーを重ねた図。",
        verify=rain_verify,
    )
    payload = {
        "area_id": area[0], "area_name": area[1],
        "base_utc": base, "base_jst": base_jst.strftime("%Y-%m-%d %H:%M"),
        "panels": [{"title": p["title"], "time_jst": p["disp"], "ft_hours": p["ft"]}
                   for p in panels],
        "nowcast": nowc_payload or None,
        "legend_mm_h": [lab for lab, _ in LEGEND],
        "source": "JMA 解析雨量・降水短時間予報 + ナウキャスト (jma.go.jp)",
        "page": _RAIN_PAGE,
    }
    images = [{"id": "rain", "bytes": rain_jpeg,
               "label": "気象庁 解析雨量・降水短時間予報（{}）".format(area[1]),
               "alt": "気象庁 解析雨量・降水予報（{}・{}付近）".format(area[1], name),
               "page_url": _RAIN_PAGE, "verify": rain_verify}]
    if nowc_entry:
        images.append(nowc_entry)
    return {"images": images, "payload": payload, "figure": figure}

"""惑星表面（等角図法）の地図描画を1か所に集約する。

周回機（`planetary_map`）とローバー（`planetary_rover`）はタイル取得こそ共有していたが、
「切り出し＋投影(g2px)＋マーカー描画＋画素検証」をそれぞれ持っていた。同じ図を同じ投影で
描く以上、投影と検証が2か所にあると必ず食い違いが出る（実測: ローバー側は投影の逆変換まで
検査していたのに、周回機側はラベル被りしか見ていなかった）。

ここに集約し、任意の**地点マーカー**（落下地点・着陸地点・観測点など）を同じ投影で描ける
ようにする。軌道要素・天体暦・タイルURLの定義は呼び出し側に残す（この module は
「地図の上に何かを描く」ことだけを担当する）。
"""
from __future__ import annotations

import concurrent.futures
import io
import math
import time
from typing import Optional, Tuple

from .cache import disk_get
from .img_common import load_font, pixel_near

UA = {"User-Agent": "space-finder-mcp/0.27 (MCP; planetary surface map)"}


def fetch_tiles(body: dict, center_lon, center_lat, span_deg, zoom, max_workers: int = 16):
    """中心付近のタイルをまとめて取得する（戻り: mosaic, center_px, 左上グローバルpx, cols, rows）。"""
    from PIL import Image
    zoom = min(int(zoom), body["maxzoom"])
    cols = 2 ** (zoom + 1); rows = 2 ** zoom
    W = cols * 256; H = rows * 256
    xc = (center_lon + 180) / 360.0 * W
    yc = (90 - center_lat) / 180.0 * H
    span_px = span_deg / (360.0 / W)
    cc0 = int(xc // 256); rr0 = int(yc // 256)
    half = int(math.ceil((span_px / 2) / 256.0)) + 1
    c_lo = max(0, cc0 - half); r_lo = max(0, rr0 - half)
    c_hi = min(cols - 1, cc0 + half); r_hi = min(rows - 1, rr0 + half)
    tiles = [(rr, cc) for rr in range(r_lo, r_hi + 1) for cc in range(c_lo, c_hi + 1)]
    url = body["tile"]

    def _one(tc):
        # タイルは内容が変わらない資産なのでディスクキャッシュ経由（TTL_ASSET）。
        # 2回目以降は同一タイルを再ダウンロードしない（LRO全面表示で54枚/回）。
        rr, cc = tc
        return rr, cc, disk_get(url.format(z=zoom, row=rr, col=cc),
                                subdir="trek", timeout=20, headers=UA)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, tiles))
    cw = c_hi - c_lo + 1; rh = r_hi - r_lo + 1
    mosaic = Image.new("RGB", (cw * 256, rh * 256), (30, 30, 35))
    failed = 0
    for rr, cc, data in results:
        # 取得できなかったタイルは**黙って捨てず数える**（図に穴が空くため。
        # `except: pass` だけだと「地図に一部が無い」ことに誰も気づけない）。
        if not data:
            failed += 1
            continue
        try:
            from PIL import Image as PImage
            mosaic.paste(PImage.open(io.BytesIO(data)).convert("RGB"),
                         ((cc - c_lo) * 256, (rr - r_lo) * 256))
        except Exception:
            failed += 1            # デコード不能なタイルも穴になるので同じ扱い
    center_px = (xc - c_lo * 256, yc - r_lo * 256)
    return mosaic, center_px, (c_lo * 256, r_lo * 256), cols, rows, failed, len(tiles)


def surface_view(body: dict, lon: float, lat: float, span_deg: float, zoom: int,
                 out_px: int, *, whole_dim: Optional[float] = 0.75,
                 regional_dim: Optional[float] = 0.7) -> dict:
    """指定地点を中心にした等角図法の地図ビューを作る。

    戻り値:
      img      : 描画先の画像
      g2px(lon, lat) -> (x, y)   緯度経度→画像内座標
      inv(x, y) -> (lon, lat)   その逆変換（描いた画素から座標を逆算して検証する）
      mode / W / H / left / top / scale(_x,_y) : 検証・注記に使う幾何情報
    span_deg>=360 は天体全面（縦横比そのまま）、それ未満は指定地点中心の正方形切り出し。
    *dim=None は減光しない（呼び出し側の見た目を変えないため）。
    """
    from PIL import Image
    mosaic, center_px, tl, cols, rows, t_failed, t_total = fetch_tiles(body, lon, lat, span_deg, zoom)
    W = cols * 256; H = rows * 256
    geom: dict = {"W": W, "H": H, "cols": cols, "rows": rows}
    if span_deg >= 360.0:
        img = mosaic.resize((out_px, int(out_px * H / W)), Image.LANCZOS)
        if whole_dim is not None:
            img = img.point(lambda p: int(p * whole_dim))
        sx = out_px / float(W); sy = img.size[1] / float(H)

        def g2px(lo, la):
            return ((lo + 180) / 360.0 * W * sx, (90 - la) / 180.0 * H * sy)

        def inv(x, y):
            return ((x / sx) / W * 360.0 - 180.0, 90.0 - (y / sy) / H * 180.0)

        geom.update({"mode": "whole", "left": 0, "top": 0, "scale_x": sx, "scale_y": sy,
                     "side": None, "span_px": None,
                     "deg_per_px": 360.0 / (W * sx), "deg_per_px_y": 180.0 / (H * sy)})
    else:
        gx0 = tl[0] + center_px[0]; gy0 = tl[1] + center_px[1]
        span_px = span_deg / (360.0 / W)
        half = span_px / 2.0
        left = int(gx0 - half); top = int(gy0 - half)
        side = int(2 * half)
        right = left + side; bottom = top + side
        if left < 0: left, right = 0, side
        if top < 0: top, bottom = 0, side
        if right > W: right, left = W, W - side
        if bottom > H: bottom, top = H, H - side
        m_l = max(0, left - tl[0]); m_t = max(0, top - tl[1])
        m_r = min(mosaic.size[0], m_l + side); m_b = min(mosaic.size[1], m_t + side)
        deg_per_px_y = 180.0 / (H * (out_px / float(side)))
        crop = mosaic.crop((m_l, m_t, m_r, m_b))
        img = crop.resize((out_px, out_px), Image.LANCZOS)
        if regional_dim is not None:
            img = img.point(lambda p: int(p * regional_dim))
        scale = out_px / float(side)

        def g2px(lo, la):
            return ((lo + 180) / 360.0 * W - left) * scale, ((90 - la) / 180.0 * H - top) * scale


        def inv(x, y):
            return (((x / scale) + left) / W * 360.0 - 180.0,
                    90.0 - ((((y / scale) + top) / H) * 180.0))

        geom.update({"mode": "regional", "left": left, "top": top, "scale": scale,
                     "side": side, "span_px": span_px,
                     "deg_per_px": 360.0 / (W * scale), "deg_per_px_y": deg_per_px_y})
    return {"img": img, "g2px": g2px, "inv": inv, "mosaic": mosaic, "center_px": center_px,
            "tl": tl, "zoom": min(int(zoom), body["maxzoom"]),
            "tiles_failed": t_failed, "tiles_total": t_total, **geom}


def tile_failure_note(sv: dict) -> Optional[str]:
    """地図タイルの欠けを注記文にする（数値から生成）。欠けが無ければ None。

    タイルが取れないと図の該当領域は背景色のまま残る。注記に出さないと
    「地図に無い＝そこに何も無い」と誤読される（実測: 提供元にタイルが無い
    領域＝404 は正常応答として返るため、失敗と区別がつかない）。
    """
    failed = int(sv.get("tiles_failed") or 0)
    total = int(sv.get("tiles_total") or 0)
    if failed <= 0 or total <= 0:
        return None
    return ("地図タイル {}/{} 枚を取得できませんでした（提供元にタイルが無い＝404、"
            "または一時的な取得失敗）。図の該当領域は地図ではなく背景色のままです。"
            .format(failed, total))


def draw_marker(draw, xy: Tuple[float, float], radius: int, *, fill=(255, 60, 30),
                outline=(255, 255, 255), outline_w: int = 4,
                inner_ratio: Optional[float] = None) -> None:
    """地点マーカー（外周円＋任意で中心の白点）。周回機・ローバー・落下地点で共通。"""
    x, y = xy
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill,
                 outline=outline, width=outline_w)
    if inner_ratio:
        r2 = max(1, int(radius * inner_ratio))
        draw.ellipse([x - r2, y - r2, x + r2, y + r2], fill=(255, 255, 255))


def marker_scan_radius(marker_r: int) -> int:
    """マーカー色を数える走査半径。中心には白点（inner_ratio）を描くので、
    中心そのものを走査すると 0 ピクセルになる（実測で踏んだ）。外周リングの内側を狙う。
    """
    return max(3, int(marker_r * 0.75))


def draw_markers(draw, points, *, radius: int, fill=(255, 60, 30),
                 outline=(255, 255, 255), outline_w: int = 4,
                 inner_ratio: Optional[float] = None) -> None:
    """複数地点のマーカーをまとめて描く（`points` は (x, y) のイテラブル）。

    1地点＝1マーカーの描き方をそのまま繰り返す。図中のラベルは重なるので、
    複数地点のときは番号だけを描き、凡例を別パネルに置くのが読みやすい。
    """
    for (x, y) in points:
        draw_marker(draw, (x, y), radius, fill=fill, outline=outline,
                    outline_w=outline_w, inner_ratio=inner_ratio)


def verify_marker(img, xy: Tuple[float, float], lon: float, lat: float, inv,
                  colors=((255, 40, 30), (255, 60, 30)), tol: int = 120, r: int = 5,
                  tol_deg: float = 0.01) -> dict:
    """描いたマーカーから緯度経度を逆算し、投影とマーカー描画の両方を検証する。

    等角図法の逆変換なので「地図のどこに描いたか」と「報告した座標」が食い違えば落ちる。
    併せて、その画素に実際にマーカー色が乗っているかも数える（マーカーを描き忘れた
    図・別の色で塗った図を落とすため）。落下地点などの地点マーカーでも同じ検査を使う。

    `r` は走査半径（px）。中心に白点のあるマーカーでは `marker_scan_radius(半径)` を渡す。
    """
    inv_lon, inv_lat = inv(xy[0], xy[1])
    err = max(abs(inv_lon - lon), abs(inv_lat - lat))
    painted = max(pixel_near(img, xy, c, tol=tol, r=r) for c in colors)
    return {"ok": bool(err <= tol_deg and painted > 0),
            "latlon_from_px": [round(inv_lon, 4), round(inv_lat, 4)],
            "latlon_from_px_error_deg": round(err, 4),
            "marker_pixels": int(painted)}


def verify_markers(img, items, inv, *, colors=((255, 40, 30), (255, 60, 30)),
                   tol: int = 120, tol_deg: float = 0.01) -> dict:
    """複数の地点マーカーを1つずつ検証し、集約結果を返す。

    items: [{"id","label","lat","lon","px", "radius"?}, ...]
      - 各地点で投影の逆変換（描いた画素→緯度経度）とマーカー画素数を検査する
      - 走査半径はマーカー半径から決める（中心の白点を数えないため）
    戻り: {"ok": 全地点が合格, "markers": [各地点の結果...], "marker_pixels_min": 最小値}
    """
    per = []
    for it in items:
        v = verify_marker(img, tuple(it["px"]), float(it["lon"]), float(it["lat"]), inv,
                          colors=colors, tol=tol,
                          r=marker_scan_radius(int(it.get("radius") or 8)), tol_deg=tol_deg)
        per.append(dict(v, id=it.get("id"), label=it.get("label")))
    return {"ok": bool(per) and all(x["ok"] for x in per), "markers": per,
            "marker_pixels_min": min([x["marker_pixels"] for x in per] or [0]),
            "latlon_from_px_error_deg_max": max([x["latlon_from_px_error_deg"] for x in per] or [0.0])}


def fit_font_for_width(width: int, lines, sizes, *, margin: int = 24):
    """与えた文字列が幅に収まる最大のフォントを選ぶ（文字が画像からはみ出すのを防ぐ）。

    sizes は大きい順。どれでも収まらなければ最小のものを返す。
    """
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    chosen = None
    for sz in sizes:
        f = load_font(max(9, int(sz)))
        if all(probe.textbbox((0, 0), t, font=f)[2] <= width - margin for t in lines if t):
            chosen = f
            break
    if chosen is None:
        chosen = load_font(max(9, int(sizes[-1])))
    return chosen


def add_legend_band(img, lines, *, font, title_font=None, pad: int = 10, line_gap: int = 6,
                    bg=(10, 14, 26), fg=(226, 232, 248), title_fg=(255, 255, 255)):
    """画像の**下**に文字帯を足した画像を返す（地図の上に文字を重ねない）。

    実測の失敗: 凡例・タイトルを地図に重ねて描いたら、地点マーカーの上に文字が乗って
    マーカーが隠れた（アポロ6地点の図で 1 地点は中心が黒く潰れ、他の地点も文字が被った）。
    「文字は必ず地図の外」を型で保証するため、キャンバスを下に伸ばして帯を作る。

    戻り: (新しい画像, 地図領域の高さ)。マーカーを描く座標は地図領域のまま使える。
    """
    from PIL import Image, ImageDraw
    w, h = img.size
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def _lh(f, t):
        bb = probe.textbbox((0, 0), t, font=f)
        return bb[3] - bb[1]

    hts = [(_lh(title_font or font, t) if (i == 0 and title_font) else _lh(font, t))
           for i, t in enumerate(lines)]
    band_h = pad * 2 + sum(hts) + line_gap * max(0, len(lines) - 1)
    canvas = Image.new("RGB", (w, h + band_h), bg)
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    y = h + pad
    for i, t in enumerate(lines):
        f = title_font if (i == 0 and title_font) else font
        d.text((12, y), t, font=f, fill=title_fg if (i == 0 and title_font) else fg)
        y += hts[i] + line_gap
    return canvas, h


def graticule_view(lon: float, lat: float, span_deg: float, out_px: int,
                   body_ja: str = "", grid_deg: int = 30,
                   bg=(10, 14, 26), grid=(70, 84, 110), eq=(150, 170, 200),
                   fg=(170, 185, 210)) -> dict:
    """全球地形画像が無い天体向けの**緯度経度グリッド図**（座標系だけで描く）を作る。

    ガス惑星や小型天体は「全球等角図」の画像が存在しない（実測: NASA Trek に
    Jupiter は無い＝404）。それでも公表された座標（例: 木星衝突地点）を地図上に
    置きたいので、`surface_view` と同じ契約（img / g2px / inv / deg_per_px）で
    座標グリッドだけの図を返す。地形画像ではないことを注記で必ず明示すること。

    span_deg>=360 は全周（経度 ±180°・緯度 ±90°、2:1）、それ未満は指定地点中心の正方形。
    """
    from PIL import Image, ImageDraw
    whole = span_deg >= 360.0
    if whole:
        img = Image.new("RGB", (out_px, out_px // 2), bg)
    else:
        img = Image.new("RGB", (out_px, out_px), bg)
    iw, ih = img.size
    d = ImageDraw.Draw(img)
    f_sm = load_font(max(10, iw // 90), bold=True)

    def g2px(lo, la):
        if whole:
            return ((lo + 180.0) / 360.0 * iw, (90.0 - la) / 180.0 * ih)
        half = span_deg / 2.0
        return ((lo - lon + half) / span_deg * iw, (lat + half - la) / span_deg * ih)

    def inv(x, y):
        if whole:
            return (x / iw * 360.0 - 180.0, 90.0 - y / ih * 180.0)
        half = span_deg / 2.0
        return (lon - half + x / iw * span_deg, lat + half - y / ih * span_deg)

    # 緯線・経線（30°ごと。赤道・本初子午線は強調）
    for la in range(-90, 91, grid_deg):
        x0, y0 = g2px(-180.0 if whole else lon - span_deg / 2.0, float(la))
        x1, y1 = g2px(180.0 if whole else lon + span_deg / 2.0, float(la))
        c = eq if la == 0 else grid
        if -20 <= y0 <= ih + 20:
            d.line([(x0, y0), (x1, y1)], fill=c, width=2 if la == 0 else 1)
        if y0 >= 0 or whole:
            d.text((4, min(max(2, y0 + 2), ih - 14)), "%+d°" % la, font=f_sm, fill=fg)
    lo_start, lo_end, lo_step = (-180, 181, grid_deg) if whole else (
        int(lon - span_deg / 2 // grid_deg * grid_deg), int(lon + span_deg / 2) + 1, max(grid_deg // 3, 5))
    for lo in range(int(lo_start), int(lo_end), int(lo_step)):
        x0, y0 = g2px(float(lo), -90.0 if whole else lat - span_deg / 2.0)
        x1, y1 = g2px(float(lo), 90.0 if whole else lat + span_deg / 2.0)
        c = eq if lo == 0 else grid
        if -20 <= x0 <= iw + 20:
            d.line([(x0, y0), (x1, y1)], fill=c, width=2 if lo == 0 else 1)
            d.text((min(max(2, x0 + 2), iw - 34), 2), "%+d°" % lo, font=f_sm, fill=fg)
    return {"img": img, "g2px": g2px, "inv": inv, "mode": "graticule",
            "W": iw, "H": ih, "left": 0, "top": 0,
            "deg_per_px": 360.0 / iw if whole else span_deg / iw,
            "deg_per_px_y": 180.0 / ih if whole else span_deg / ih,
            "whole": whole, "zoom": None, "attrib": None}


def _regional_view(src, tl, gx, gy, W, H, span_deg, out_px, dim):
    """天体面の全体画像 `src` の全天体px座標 (gx, gy) を中心に span_deg 四方を切り出す。

    `tl` は `src` の左上が全天体座標でどこか（1枚画像なら (0, 0)）。
    戻り値の契約は surface_view と同じ（img / g2px / inv / deg_per_px …）。

    注: `surface_view` 側の同等処理は**あえて共通化していない**（タイル合成版は既存の
    出力等価＝バイト一致を検証済みで、触ると図が変わるリスクがある）。この関数は
    1枚画像ベースマップ（image_view）用の新しい経路として使う。
    """
    from PIL import Image
    span_px = span_deg / (360.0 / W)
    half = span_px / 2.0
    left = int(gx - half); top = int(gy - half)
    side = int(2 * half)
    right = left + side; bottom = top + side
    if left < 0: left, right = 0, side
    if top < 0: top, bottom = 0, side
    if right > W: right, left = W, W - side
    if bottom > H: bottom, top = H, H - side
    m_l = max(0, left - tl[0]); m_t = max(0, top - tl[1])
    m_r = min(src.size[0], m_l + side); m_b = min(src.size[1], m_t + side)
    crop = src.crop((m_l, m_t, m_r, m_b))
    img = crop.resize((out_px, out_px), Image.LANCZOS)
    if dim is not None:
        img = img.point(lambda p: int(p * dim))
    scale = out_px / float(side)

    def g2px(lo, la):
        return ((lo + 180) / 360.0 * W - left) * scale, ((90 - la) / 180.0 * H - top) * scale

    def inv(x, y):
        return (((x / scale) + left) / W * 360.0 - 180.0,
                90.0 - ((((y / scale) + top) / H) * 180.0))

    return {"img": img, "g2px": g2px, "inv": inv, "mode": "regional", "W": W, "H": H,
            "left": left, "top": top, "scale": scale, "side": side, "span_px": span_px,
            "deg_per_px": 360.0 / (W * scale), "deg_per_px_y": 180.0 / (H * scale)}


def image_view(url: str, lon: float, lat: float, span_deg: float, out_px: int, *,
               credit: str = "", subdir: str = "basemap", headers: Optional[dict] = None,
               whole_dim: Optional[float] = None, regional_dim: Optional[float] = None) -> dict:
    """全球等角図（画像1枚）をベースマップにしたビューを作る（Trek と同じ投影契約）。

    タイル化された全球データが無い天体でも、**1枚の全球図**があれば同じ描画・検証の
    経路に乗せられる。アスペクト比が 2:1 でない等角図は **2:1 に補正**する
    （「どのあたりか」が分かればよい用途では、厳密な幾何より可用性を優先。補正の有無と
    元の AR は戻り値に入れ、呼び出し側が注記に出す）。画像は不変資産なのでディスク
    キャッシュ経由（2回目以降は再取得しない）。
    """
    import io as _io
    from PIL import Image
    # 画像ホスト（Wikimedia など）は短時間の連続取得を制限するので、1回だけ間を置いて再試行する
    # （実測: 3枚連続で取得したとき 1枚だけ失敗した）。UA は呼び出し側が差し替えられる。
    hdr = headers or UA
    data = disk_get(url, subdir=subdir, timeout=180, headers=hdr)
    if not data:
        time.sleep(1.5)
        data = disk_get(url, subdir=subdir, timeout=180, headers=hdr)
    if not data:
        raise ValueError("basemap 画像を取得できません: " + url[:80])
    src = Image.open(_io.BytesIO(data)).convert("RGB")
    w0, h0 = src.size
    ar0 = w0 / float(h0)
    W = w0
    H = max(1, int(round(W / 2.0)))
    img2 = src if abs(ar0 - 2.0) < 1e-6 else src.resize((W, H), Image.LANCZOS)
    gx = (lon + 180.0) / 360.0 * W
    gy = (90.0 - lat) / 180.0 * H
    if span_deg >= 360.0:
        out = img2.resize((out_px, max(1, out_px // 2)), Image.LANCZOS)
        if whole_dim is not None:
            out = out.point(lambda p: int(p * whole_dim))
        sx = out_px / float(W); sy = out.size[1] / float(H)

        def g2px(lo, la):
            return (lo + 180) / 360.0 * W * sx, (90 - la) / 180.0 * H * sy

        def inv(x, y):
            return (x / sx) / W * 360.0 - 180.0, 90.0 - (y / sy) / H * 180.0

        res = {"img": out, "g2px": g2px, "inv": inv, "mode": "image_whole", "W": W, "H": H,
               "left": 0, "top": 0, "scale_x": sx, "scale_y": sy, "side": None, "span_px": None,
               "deg_per_px": 360.0 / (W * sx), "deg_per_px_y": 180.0 / (H * sy)}
    else:
        res = _regional_view(img2, (0, 0), gx, gy, W, H, span_deg, out_px, regional_dim)
        res["mode"] = "image_regional"
    res.update({"source_ar": round(ar0, 4), "ar_corrected": bool(abs(ar0 - 2.0) > 1e-6),
                "pixels": [w0, h0], "credit": credit, "zoom": None})
    return res

"""太陽系の斜め俯瞰レンダラ（Pillow）。入力は scene dict、出力は PNG か (画像, figure)。

`solar_system.py` から切り出した描画専用の層。**通信をしない**（Horizons/SBDB は
`solar_system` / `solar_model` 側）。scene は JSON 化できる dict で、キーの一次情報は
`solar_system._compute()` の docstring と `docs/oblique-view-spec.md`。

画素の自己検証（`_verify_*`）も同じ層に置く（描いた本人が描いた画素を測る）。
"""
from __future__ import annotations

import io
import math

from .img_common import (body_rgb, conic_from_elements, figure_notes, figure_payload,
                         load_font, primary_spec, scale_spec, symbol_rgb, verify_curve,
                         view_spec)
from .solar_model import (LIGHT_DAY_AU, LIGHT_DAY_KM, _COMET_COLOR, _PLANETS,
                          _ecliptic_to_perifocal)


def _glow(img, box, radius, fill, line_width=0):
    """マーカーの光暈を「その周囲だけのタイル」で作って合成する。

    以前はマーカー1個ごとに**全画面サイズの RGBA キャンバス**を作り、そこへ小さな
    楕円を描いて `GaussianBlur` し、さらに `img.convert("RGBA")` → `alpha_composite`
    → `convert("RGB")` で全画面を往復していた（実測: 俯瞰図1枚で 27 個 = 2.3 秒。
    `solar_system_now` の描画時間の 7 割）。Pillow の GaussianBlur は有限の影響半径
    なので、**半径の 4 倍＋4px** だけ広げたタイルでぼかしても全画面をぼかした結果と
    画素単位で一致する（実測: 画素差 >10 が 0/2,250,000）。

    box: 楕円なら [x0,y0,x1,y1]、`line_width > 0` なら (x0,y0)-(x1,y1) の線分。
    """
    from PIL import Image, ImageDraw, ImageFilter     # 遅延 import（全ツール共通の流儀）
    W, H = img.size
    pad = radius * 4 + 4
    x0 = max(0, int(math.floor(min(box[0], box[2]))) - pad)
    y0 = max(0, int(math.floor(min(box[1], box[3]))) - pad)
    x1 = min(W, int(math.ceil(max(box[0], box[2]))) + pad)
    y1 = min(H, int(math.ceil(max(box[1], box[3]))) + pad)
    if x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    local = [box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0]
    if line_width > 0:
        td.line(local, fill=fill, width=line_width)
    else:
        td.ellipse(local, fill=fill)
    tile = tile.filter(ImageFilter.GaussianBlur(radius))
    # 元実装と同じ合成式（透明タイルを RGBA で alpha_composite してから RGB へ戻す）を
    # その領域だけで行う＝画素は変わらない。
    region = img.crop((x0, y0, x1, y1)).convert("RGBA")
    img.paste(Image.alpha_composite(region, tile).convert("RGB"), (x0, y0))


# ---------------------------------------------------------------- 斜め俯瞰（唯一の描画面）
# 黄道面を斜め上から見た構図。投影・円錐曲線・深度の計算は orbit_view に委ね、
# **描画はこの 1 本に一本化**した。旧 engine="simple"（真上・対数）/ engine="accurate"
# （matplotlib・線形）/ view="comet_orbit" の _render_orbit_plane は見た目の要素を
# ここへ取り込んで廃止した（引数は互換のため残す）。
OBLIQUE_W = 1500


OBLIQUE_EL = 34.0


OBLIQUE_AZ = 28.0


OBLIQUE_DOLLY = 4.2          # カメラ距離 = dolly × 画面半径（弱い透視）


def oblique_camera(scene=None):
    """scene のカメラ設定（仰角・方位・dolly）を返す。

    `solar_system_now(elevation=, azimuth=, dolly=)` が scene に入れた値を使う。
    未指定（キーなし・None）は既定（34°・28°・4.2）。描画と注記が同じ値を見るよう、
    **角度は必ずこの関数から取る**（片方だけ既定値で書くと図と注記が食い違う）。
    """
    s = scene or {}
    el = s.get("oblique_elevation")
    az = s.get("oblique_azimuth")
    dl = s.get("oblique_dolly")
    return (OBLIQUE_EL if el is None else float(el),
            OBLIQUE_AZ if az is None else float(az),
            OBLIQUE_DOLLY if dl is None else float(dl))


def _split_camera_arg(value, name, lo, hi):
    """カメラ引数を検証する。戻り: (値 or None, エラー文 or None)。

    範囲外を黙ってクランプすると「指定した角度で描いた」と誤解されるので、
    数値でない・範囲外はエラー文を返して呼び側がエラー結果にする。
    実装は `orbit_view.one_camera_arg` に 1 本化してある（`planetary_orbiter_track` と共有）。
    """
    from .orbit_view import one_camera_arg
    return one_camera_arg(value, name, lo, hi)


def _obl_rgba(color, alpha):
    """色＋不透明度 → RGBA（Pillow 用）。"""
    a = int(max(0, min(255, round(float(alpha) * 255))))
    return (int(color[0]), int(color[1]), int(color[2]), a)


def _oblique_hi(scene):
    """表示範囲 (hi_au, lo_au, kind) を決める。

    kind は動径の写像で、"log"（既定・0.3〜190 AU を 1 枚に）／"linear"
    （旧 engine="accurate" 互換＝相対距離が正確）。range_au 指定時は hi を固定する。
    """
    kind = str(scene.get("radial_scale") or "log")
    rng = float(scene.get("range_au") or 0.0)
    if kind == "linear":
        hi = rng if rng else 45.0
        for rt in (scene.get("routes") or {}).values():      # 経路が枠外へ出ないよう広げる
            for pt in (rt.get("points") or []):
                hi = max(hi, math.hypot(pt[0], pt[1]) * 1.03)
        return hi, 0.0, "linear"
    if rng:
        return rng, min(0.30, rng * 0.05), "log"
    hi = 60.0
    for p in (scene.get("planets") or {}).values():
        hi = max(hi, float(p.get("sma") or 0.0) * 1.05)
    for group in ("asteroids", "probes", "comets"):
        for d in (scene.get(group) or {}).values():
            if not d.get("error") and d.get("au"):
                hi = max(hi, float(d["au"]) * 1.12)
    if any(not d.get("error") for d in (scene.get("probes") or {}).values()):
        # 探査機を描く図では「1光日」リング（真距離 173.14 AU）も枠に入れる
        hi = max(hi, LIGHT_DAY_AU * 1.02)
    for rt in (scene.get("routes") or {}).values():
        for pt in (rt.get("points") or []):
            hi = max(hi, math.hypot(pt[0], pt[1]) * 1.02)
    return hi, 0.30, "log"


def _oblique_view(scene):
    """scene → orbit_view.View（斜め俯瞰・対数/線形動径・弱い透視）。"""
    from .orbit_view import Camera, RadialScale, View
    hi, lo, kind = _oblique_hi(scene)
    el, az, dl = oblique_camera(scene)
    return View(Camera(el, az, dl),
                RadialScale(kind, lo_au=lo, hi_au=hi, r_px=620.0),
                OBLIQUE_W / 2.0, OBLIQUE_W / 2.0)


def _oblique_visible(view, au):
    """太陽からの距離 au が図の表示範囲に入っているか（描画と検証で同じ規則）。

    対数縮尺は `lo <= r <= hi`、線形縮尺は `r <= hi`（下限なし）。ここを描画と検証で
    別々に書くと、範囲の縁の天体（例: range_au=5 の木星 5.32 AU）で
    「描いていないのに検証は探す」という食い違いが出る。
    """
    if au is None or float(au) <= 0:
        return False
    r = float(au)
    if view.scale.kind == "linear":
        return r <= float(view.scale.hi_au)
    return float(view.scale.lo_au) <= r <= float(view.scale.hi_au)


def _xyz_from_au_lonlat(au, lon_deg, lat_deg):
    """日心距離・黄経・黄緯 → 黄道 J2000 の直交座標 [AU]。"""
    au = float(au)
    lam, bet = math.radians(float(lon_deg)), math.radians(float(lat_deg))
    return (au * math.cos(bet) * math.cos(lam),
            au * math.cos(bet) * math.sin(lam),
            au * math.sin(bet))


def _obl_draw_orbit(draw, segments, view, color, base_width=1.0, kind="planet", chunk=8):
    """軌道を線分列で描く（奥ほど細く暗く、手前ほど太く明るい）。描いた区間数を返す。

    旧 `orbit_view.draw_orbit` をここへ統合した（太陽周回の図を描くのは本モジュールだけなので、
    投影部品と描画部品を分けておく理由が無くなった）。`draw` は RGBA モードの ImageDraw。
    """
    from .orbit_view import depth_alpha, depth_range, depth_width
    dmin, dmax = depth_range(segments, view)
    drawn = 0
    for seg in segments:
        pts = [view.project(p) for p in seg]
        for s0 in range(0, len(pts) - 1, chunk):
            part = pts[s0:s0 + chunk + 1]
            if len(part) < 2:
                continue
            depth = sum(p[2] for p in part) / len(part)
            w = max(1, int(round(depth_width(base_width, depth, dmin, dmax))))
            a = depth_alpha(depth, dmin, dmax, kind)
            draw.line([(p[0], p[1]) for p in part], fill=_obl_rgba(color, a),
                      width=w, joint="curve")
            drawn += 1
    return drawn


def _render_oblique(scene=None, body=None):
    """斜め俯瞰の描画（唯一の入口）。

    - `scene` を渡す: 太陽系の現在位置の俯瞰図（PNG bytes を返す）。
    - `body` を渡す: 1 天体の軌道面図（`(PIL画像, figure ブロック)` を返す）。
      軌道面を「真横から」見る構図は変えない（斜め俯瞰へ投影し直すと楕円が潰れて
      軌道の形＝離心率・焦点が読めなくなる）。描画コードだけをここへ統合した。
    """
    if body is not None:
        return _oblique_body(body)
    return _oblique_scene(scene)


def _oblique_scene(scene):
    """太陽系の現在位置（斜め俯瞰・対数/線形動径・弱い透視）を描いて PNG bytes を返す。

    描くもの: 恒星背景 / 惑星軌道（深度で太さ・明度を変える）/ 惑星・小惑星・彗星・探査機の
    マーカーと名札 / 通過経路（破線＋進行方向の矢印）/ 太陽 / 1光日リング / 凡例。
    名札は「全ラベル → マーカー描き直し」の二段構えで、データが箱に隠れないことを
    配置の巧さに依存させない。
    """
    from PIL import Image, ImageDraw, ImageFilter

    from .orbit_view import draw_track, orbit_segments

    view = _oblique_view(scene)
    W = H = OBLIQUE_W
    CX = CY = W / 2.0
    hi, lo = float(view.scale.hi_au), float(view.scale.lo_au)
    linear = view.scale.kind == "linear"
    rng_au = float(scene.get("range_au") or 0.0)
    if linear:                      # 線形のときだけ「1 AU が何 px か」が意味を持つ
        scene["px_per_au"] = float(view.scale.r_px) / max(1e-9, hi)

    def radius_px(r_au):
        return float(view.scale.radius_px(float(r_au)))

    def visible(r_au):
        """太陽からの距離が表示範囲に入っているか（範囲外は描かず、後で注記に列挙する）。"""
        return _oblique_visible(view, r_au)

    def au_at_px(px_):
        """画面半径 [px] → 太陽からの距離 [AU]（縮尺の逆算。注記を数値から作るため）。"""
        if linear:
            return float(px_) * hi / max(1e-9, float(view.scale.r_px))
        frac = float(px_) / max(1e-9, float(view.scale.r_px))
        return 10.0 ** (math.log10(lo) + frac * (math.log10(hi) - math.log10(lo)))

    def at(au, lon, lat=0.0):
        return view.project(_xyz_from_au_lonlat(au, lon, lat))

    def mark_xyz(mk):
        """経路の目印の 3D 位置（xyz が無い古い経路は正射影＋黄経で代用）。"""
        xyz = mk.get("xyz")
        if xyz:
            return (float(xyz[0]), float(xyz[1]), float(xyz[2]))
        return _xyz_from_au_lonlat(float(mk.get("proj_au") or mk.get("r_au") or 0.0),
                                   float(mk.get("lon_deg") or 0.0), 0.0)

    # ---- ラベル配置の共通機構 --------------------------------------------------
    _occ_rects = []          # 既に描いたラベル箱（後のラベルが避ける）
    _occ_pts = []            # マーカー・太陽円盤（ラベルが覆うと図と数値が食い違う）

    def place_rect(w, h, r_px, prefer=None, avoid=(), ignore_pts=()):
        """リングの外側に置くラベル箱の位置を、占有領域との重なりが最小の角度で選ぶ。

        `ignore_pts` は「そのラベル自身のマーカー」の座標（自分の真横に置くのが正しいので、
        除外しないと自分から最も遠い角度へ逃げてしまう）。
        """
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
                if any(abs(mx - ix) < 1e-6 and abs(my - iy) < 1e-6 for (ix, iy) in ignore_pts):
                    continue
                if (x0 - mr) <= mx <= (x0 + w + mr) and (y0 - mr) <= my <= (y0 + h + mr):
                    pen += 500.0 * mr
            if best is None or pen < best_pen - 1e-9:
                best, best_pen = (x0, y0, x0 + w, y0 + h), pen
        if best is None:                       # 候補が尽きることは無いが、None は返さない
            best = (6.0, 112.0, 6.0 + w, 112.0 + h)
        return best

    # ---- 背景 ----------------------------------------------------------------
    import random
    img = Image.new("RGB", (W, H), (3, 4, 12))
    dr = ImageDraw.Draw(img, "RGBA")
    rnd = random.Random(20261031)
    for _ in range(1300):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        m = rnd.random() ** 3.0
        dr.ellipse([x, y, x + 0.6 + 1.6 * m, y + 0.6 + 1.6 * m],
                   fill=(255, 255, 255, int(24 + 150 * m)))

    # ---- 太陽（グロー＋円盤）はデータより先に描く ------------------------------
    # 後に描くと経路の破線や惑星マーカーの上に薄いベールがかかり、画素検査で
    # 「破線が消えた」と判定される（実測: 代表点9点中2点）。表示範囲を絞ったときは
    # 最内惑星を覆わない大きさへ縮める（実測: range_au=10 で水星の純色画素が 1px）。
    gr, sr = 70, 26
    _cands = [radius_px(float(p_["au"])) for p_ in (scene.get("planets") or {}).values()
              if not p_.get("error") and 0 < float(p_.get("au") or 0.0) <= hi]
    if _cands:
        gr = int(min(gr, 0.90 * min(_cands)))
        sr = int(min(sr, 0.45 * min(_cands)))
    gr, sr = max(6, gr), max(3, sr)
    sun_glow = Image.new("RGBA", (gr * 8 + 16,) * 2, (0, 0, 0, 0))
    ImageDraw.Draw(sun_glow).ellipse([sun_glow.size[0] / 2 - gr, sun_glow.size[1] / 2 - gr,
                                     sun_glow.size[0] / 2 + gr, sun_glow.size[1] / 2 + gr],
                                    fill=(255, 230, 140, 220))
    sun_glow = sun_glow.filter(ImageFilter.GaussianBlur(max(3, int(gr * 30 / 70))))
    img.paste(sun_glow, (int(CX - sun_glow.size[0] / 2), int(CY - sun_glow.size[1] / 2)),
              sun_glow)
    dr = ImageDraw.Draw(img, "RGBA")
    dr.ellipse([CX - sr, CY - sr, CX + sr, CY + sr], fill=body_rgb("太陽") + (255,),
               outline=(255, 245, 200, 255), width=2)
    scene["sun_disk_px"] = float(sr)
    scene["sun_disk_px_simple"] = float(sr)      # 旧キー（注記が参照している）
    if _cands:
        scene["inner_planet_px"] = float(min(_cands))
    if linear:                                   # 線形縮尺のときだけ「AU 相当」が意味を持つ
        scene["sun_marker_au"] = au_at_px(sr)
    _occ_pts.append((CX, CY, sr * 1.4))

    # ---- 惑星軌道 ------------------------------------------------------------
    rng_report = {"range_au": hi, "lo_au": lo, "skipped_orbits": [], "out_of_range": []}
    for name, p in (scene.get("planets") or {}).items():
        sma = float(p.get("sma") or 0.0)
        if sma <= 0:
            continue
        if not visible(sma):
            rng_report["skipped_orbits"].append({"name": name, "sma_au": sma})
            continue
        el = {"a": sma, "e": 0.0, "i": 0.0, "om": 0.0, "w": 0.0, "q": sma}
        segs = orbit_segments(el, view, hi * 1.02)
        _obl_draw_orbit(dr, segs, view, body_rgb(name), base_width=1.0, kind="planet")

    # ---- 線形縮尺のときは AU 目盛の同心円を敷く（旧 accurate の距離の読み取り） ----
    if linear:
        f_b = load_font(13)
        for au in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0):
            if au > hi * 0.995 or au < hi * 0.02:
                continue
            segs = orbit_segments({"a": au, "e": 0.0, "i": 0.0, "om": 0.0, "w": 0.0, "q": au},
                                  view, hi * 1.02)
            for seg in segs:
                pts = [view.project(pt) for pt in seg]
                for i in range(0, len(pts) - 1, 8):
                    if (i // 8) % 3:
                        continue
                    dr.line([(pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])],
                            fill=(150, 160, 185, 90), width=1)
            _tx, _ty = view.orbit_ring_px(au, 0.0)
            if 0 < _ty < H - 80:
                dr.text((_tx + 4, _ty - 16), "{} AU".format(_au_fmt(au)), font=f_b,
                        fill=(160, 172, 195, 220))

    # ---- 1光日リング（真距離の円を黄道面に描く） -------------------------------
    _ld_color = (255, 150, 160)
    ld_draw = {"au": LIGHT_DAY_AU, "km": LIGHT_DAY_KM, "color": list(_ld_color),
               "drawn": False, "why": "", "dash_samples": [], "proj": None,
               "label_box": None, "proj_label_box": None, "mark_px": None}

    def ring_px(r_au, n=360):
        """黄道面内の円（半径 r_au）を投影した画素列。"""
        segs = orbit_segments({"a": r_au, "e": 0.0, "i": 0.0, "om": 0.0, "w": 0.0,
                               "q": r_au}, view, r_au * 1.0001)
        if not segs:
            return []
        return [(view.project(p)[0], view.project(p)[1]) for p in segs[0]]

    def dashed_ring(r_au, color, width=2, step=6, on=1, phase=0, record=True):
        """PIL に破線円が無いので、円弧を分割して1本おきに描く（代表点も残す）。"""
        pts = ring_px(r_au)
        samples = []
        if len(pts) < 2:
            return samples
        n = len(pts) - 1
        for i in range(0, n, step):
            if ((i // step) + phase) % 2:
                continue
            for k in range(on):
                j = i + k
                if j + 1 >= len(pts):
                    break
                dr.line([pts[j], pts[j + 1]], fill=color, width=width)
            if record and (i // step) % 40 == 0:
                j = min(i + 1, n)
                samples.append([round((pts[i][0] + pts[j][0]) / 2.0),
                                round((pts[i][1] + pts[j][1]) / 2.0)])
        return samples

    if visible(LIGHT_DAY_AU):
        ld_draw["drawn"] = True
        ld_draw["r_px"] = radius_px(LIGHT_DAY_AU)
        ld_draw["dash_samples"] = dashed_ring(LIGHT_DAY_AU, _ld_color + (235,))
    else:
        ld_draw["why"] = "表示範囲 {:.2f}–{:g} AU の外".format(lo, hi)

    # 1光日に達していない探査機の「到達時の方向」＝現在の向きで 1 光日先の点
    projections = []
    for _n0, _d0 in (scene.get("probes") or {}).items():
        if _d0.get("error") or _d0.get("light_days") is None or _d0.get("light_day_reached"):
            continue
        _au0 = float(_d0.get("au") or 0.0)
        if _au0 <= 0:
            continue
        _k = LIGHT_DAY_AU / _au0                     # 現在の向きで 1 光日まで伸ばす
        _pos = _xyz_from_au_lonlat(_au0, _d0.get("eclLon") or 0.0, _d0.get("eclLat") or 0.0)
        _pos = (_pos[0] * _k, _pos[1] * _k, _pos[2] * _k)
        _px, _py, _d = view.project(_pos)
        if not (0 <= _px <= W and 0 <= _py <= H):
            continue
        _cpx, _cpy, _cd = view.project(_xyz_from_au_lonlat(
            _au0, _d0.get("eclLon") or 0.0, _d0.get("eclLat") or 0.0))
        # 探査機の現在位置マーカーは表示範囲内のときだけ描いている。範囲外では
        # **描いていない**ので、検証に「そこに画素がある」と期待させない（同じ規則を共有する）。
        _marker_drawn = _oblique_visible(view, _au0)
        projections.append({
            "name": _n0, "eclLat": float(_d0.get("eclLat") or 0.0),
            "eclLon": float(_d0.get("eclLon") or 0.0), "au": LIGHT_DAY_AU,
            "probe_marker_px": [round(_cpx), round(_cpy)] if _marker_drawn else None,
            "probe_marker_drawn": bool(_marker_drawn),
            "probe_au": float(_au0),
            "color": list(_d0.get("color") or (255, 214, 90)),
            "eta": _d0.get("light_day_eta_date"),
            "eta_method": _d0.get("light_day_eta_method"),
            "r_px": math.hypot(_px - CX, _py - CY),
            "mark_px": [round(_px), round(_py)]})
        if ld_draw["proj"] is None:
            ld_draw["proj"] = projections[-1]
    ld_draw["projections"] = projections
    scene["light_day_draw"] = ld_draw

    # ---- 小惑星帯（2.0–3.4 AU の目安）のきらめき -------------------------------
    if not rng_au or visible(2.7):
        for a in range(0, 360, 4):
            rr = rnd.uniform(2.0, 3.4)
            th = math.radians(a + rnd.uniform(-2, 2))
            g = rnd.randint(90, 170)
            px_, py_, _d = view.project(_xyz_from_au_lonlat(rr, math.degrees(th), 0.0))
            if 0 <= px_ < W and 0 <= py_ < H:
                dr.point((int(px_), int(py_)), fill=(g, g, min(255, g + 10), 230))

    # ---- マーカー描画の部品 ----------------------------------------------------
    _planet_draw = []          # 最終パスで描き直す惑星
    _marker_redraws = []       # ラベル箱に隠されないよう、最後にもう一度描くマーカー

    def _core(draw, px, py):
        """白点（背景の星と区別する。画素検証もこの白点を探す）。"""
        draw.ellipse([px - 3.2, py - 3.2, px + 3.2, py + 3.2], fill=(255, 255, 255, 240))

    def draw_planet(px, py, rad, col, name, pos):
        _glow(img, [px - rad - 14, py - rad - 14, px + rad + 14, py + rad + 14],
              8, tuple(col) + (90,))
        dd = ImageDraw.Draw(img, "RGBA")
        dd.ellipse([px - rad, py - rad, px + rad, py + rad], fill=tuple(col) + (255,),
                   outline=tuple(min(255, c + 70) for c in col) + (255,), width=2)
        if name == "土星":                       # 環
            dd.ellipse([px - rad - 14, py - 7, px + rad + 14, py + 7],
                       outline=tuple(symbol_rgb("ring")) + (255,), width=3)
        elif name == "木星":                     # 縞
            for dy in (-7, 5):
                dd.line([px - rad + 1, py + dy, px + rad - 1, py + dy],
                        fill=tuple(symbol_rgb("band")) + (235,), width=3)
        elif name == "火星":                     # 極冠
            dd.ellipse([px - 4, py - rad + 2, px + 4, py - rad + 9],
                       fill=tuple(symbol_rgb("cap")) + (235,))
        _core(dd, px, py)

    def draw_asteroid(px, py, rad, pos):
        col = symbol_rgb("asteroid")
        _glow(img, [px - rad - 12, py - rad - 12, px + rad + 12, py + rad + 12],
              6, (96, 230, 120, 160))
        dd = ImageDraw.Draw(img, "RGBA")
        dd.line([px - rad - 7, py, px + rad + 7, py], fill=(200, 255, 215, 255), width=2)
        dd.line([px, py - rad - 7, px, py + rad + 7], fill=(200, 255, 215, 255), width=2)
        dd.ellipse([px - rad, py - rad, px + rad, py + rad], fill=tuple(col) + (255,),
                   outline=(220, 255, 230, 255), width=2)
        _core(dd, px, py)

    def draw_comet_core(px, py, rad, pos):
        _glow(img, [px - rad - 16, py - rad - 16, px + rad + 16, py + rad + 16],
              8, (230, 250, 255, 200))
        dd = ImageDraw.Draw(img, "RGBA")
        dd.ellipse([px - rad, py - rad, px + rad, py + rad], fill=(225, 248, 255, 255),
                   outline=(255, 255, 255, 255), width=1)
        _core(dd, px, py)

    def draw_comet_tail(px, py, ux, uy, col):
        """彗星の尾（反太陽方向）。ラベル箱より後に描く（箱に隠れると尾が消える）。"""
        for seg in range(3, 0, -1):
            tail_len = 34 + seg * 14
            tw = 6 + seg * 5
            alpha = 60 + seg * 40
            _glow(img, [px, py, px + ux * tail_len, py + uy * tail_len], 3,
                  tuple(col) + (alpha,), line_width=tw)

    def draw_probe(px, py, rad, col, pos):
        _glow(img, [px - rad - 14, py - rad - 14, px + rad + 14, py + rad + 14],
              7, tuple(col) + (170,))
        dd = ImageDraw.Draw(img, "RGBA")
        dd.polygon([(px, py - rad), (px + rad, py), (px, py + rad), (px - rad, py)],
                   fill=tuple(col) + (255,), outline=(255, 255, 255, 255))
        _core(dd, px, py)

    def draw_route_mark(mx, my, col):
        dd = ImageDraw.Draw(img, "RGBA")
        dd.polygon([(mx, my - 6), (mx + 6, my), (mx, my + 6), (mx - 6, my)],
                   fill=(10, 14, 24, 255), outline=tuple(col) + (255,), width=2)

    # ---- 惑星 ----------------------------------------------------------------
    _labels = []
    for p in (scene.get("planets") or {}).values():
        jname = p["name"]
        au, lon = float(p["au"]), float(p["eclLon"])
        lat = float(p.get("eclLat") or 0.0)
        if not visible(au):
            rng_report["out_of_range"].append({"type": "planet", "name": jname, "au": au})
            continue
        col = dict((j, c) for j, _, c, _ in _PLANETS)[jname]
        px, py, _d = at(au, lon, lat)
        rad = max(8, min(20, int(5 + 28 * float(p.get("sma") or 0.0) / 45)))
        draw_planet(float(px), float(py), rad, col, jname, None)
        _planet_draw.append((float(px), float(py), rad, col, jname, None))
        _labels.append((jname, au, float(px), float(py), rad))
    for jname, au, px, py, rad in _labels:
        _ang = math.degrees(math.atan2(py - CY, px - CX)) % 360.0
        _rr = math.hypot(px - CX, py - CY)
        # 線形縮尺では内惑星が中心に密集し、名札が重なって読めなくなる（実測: 線形 45 AU 枠で
        # 水星〜火星の半径は 5〜22 px）。名札のリングに下限を置き、離れた分は引き出し線で結ぶ。
        _b = place_rect(178, 30, max(_rr, 150.0) if linear else _rr, prefer=_ang,
                        ignore_pts=((px, py),))
        _bx, _by = (_b[0] + _b[2]) / 2.0, (_b[1] + _b[3]) / 2.0
        if math.hypot(_bx - px, _by - py) > 46.0:
            dr.line([px, py, _bx, _by], fill=tuple(
                dict((j, c) for j, _, c, _ in _PLANETS)[jname]) + (110,), width=1)
        dr.rectangle(list(_b), fill=(8, 10, 22, 235))
        _occ_rects.append(tuple(_b))
        _occ_pts.append((px, py, rad + 10))
        dr.text((_b[0] + 4, _b[1] + 2), "{} {:.2f}AU".format(jname, au),
                font=load_font(17, True), fill=(255, 255, 255, 255))
    for px, py, rad, col, jname, pos in _planet_draw:      # ラベル箱より上へ描き直す
        draw_planet(px, py, rad, col, jname, pos)

    # ---- 小惑星 --------------------------------------------------------------
    for name, d in (scene.get("asteroids") or {}).items():
        if d.get("error"):
            continue
        au = float(d["au"])
        if not visible(au):
            rng_report["out_of_range"].append({"type": "asteroid", "name": d["name"], "au": au})
            continue
        pos = _xyz_from_au_lonlat(au, d["eclLon"], d.get("eclLat") or 0.0)
        px, py, _d = view.project(pos)
        draw_asteroid(float(px), float(py), 10, pos)
        _marker_redraws.append(("asteroid", (float(px), float(py), 10, pos)))
        _ang = float(d["eclLon"])
        _sma = float(d.get("sma") or 0.0)          # 軌道長半径が分かれば軌道の円も描く
        if _sma > 0 and visible(_sma):
            segs = orbit_segments({"a": _sma, "e": 0.0, "i": 0.0, "om": 0.0,
                                   "w": 0.0, "q": _sma}, view, hi * 1.02)
            for seg in segs:
                _obl_draw_orbit(dr, [seg], view, symbol_rgb("asteroid"),
                                base_width=1.5, kind="tno")
        _b = place_rect(240, 30, math.hypot(px - CX, py - CY), prefer=_ang,
                        ignore_pts=((px, py),))
        d["label_box"] = [int(v) for v in _b]
        dr.rectangle(list(_b), fill=(10, 40, 18, 235))
        _occ_rects.append(tuple(_b))
        _occ_pts.append((px, py, 20))
        dr.text((_b[0] + 4, _b[1] + 2), "{} {:.2f}AU".format(d["name"], au),
                font=load_font(17, True), fill=(200, 255, 215, 255))

    # ---- 探査機 --------------------------------------------------------------
    for name, d in (scene.get("probes") or {}).items():
        if d.get("error"):
            continue
        au = float(d.get("au") or 0.0)
        if not visible(au):
            rng_report["out_of_range"].append({"type": "probe", "name": d["name"], "au": au})
            continue
        col = tuple(d.get("color", (255, 214, 90)))
        pos = _xyz_from_au_lonlat(au, d.get("eclLon") or 0.0, d.get("eclLat") or 0.0)
        px, py, _d = view.project(pos)
        dr.line([CX, CY, px, py], fill=col + (70,), width=1)
        draw_probe(float(px), float(py), 13, col, pos)
        _marker_redraws.append(("probe", (float(px), float(py), 13, col, pos)))
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
        _b = place_rect(300, _bh, math.hypot(px - CX, py - CY),
                        prefer=math.degrees(math.atan2(py - CY, px - CX)) % 360.0,
                        ignore_pts=((px, py),))
        d["label_box"] = [int(v) for v in _b]
        dr.rectangle(list(_b), fill=(40, 20, 0, 235))
        _occ_rects.append(tuple(_b))
        _occ_pts.append((px, py, 25))
        dr.text((_b[0] + 6, _b[1] + 3), "{}  {:.0f}AU（{:.4f} 光日）".format(
            d["name"], au, float(d.get("light_days") or 0.0)), font=load_font(16, True),
            fill=(255, 255, 255, 255))
        dr.text((_b[0] + 6, _b[1] + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(
            float(d.get("eclLat") or 0.0), float(d.get("proj_au") or 0.0)),
            font=load_font(13), fill=(255, 235, 190, 255))
        if _ld_extra:
            dr.text((_b[0] + 6, _b[1] + 41), _ld_extra, font=load_font(13),
                    fill=(255, 200, 200, 255))

    # ---- 彗星（核＋尾。マーカーは黄道面ではなく 3D の実位置） --------------------
    for name, d in (scene.get("comets") or {}).items():
        if d.get("error"):
            continue
        au = float(d.get("au") or 0.0)
        if not visible(au):
            rng_report["out_of_range"].append({"type": "comet", "name": d["name"], "au": au})
            continue
        col = tuple(d.get("color", _COMET_COLOR))
        pos = _xyz_from_au_lonlat(au, d.get("eclLon") or 0.0, d.get("eclLat") or 0.0)
        px, py, _d = view.project(pos)
        vx, vy = px - CX, py - CY
        n_ = math.hypot(vx, vy) or 1.0
        ux, uy = vx / n_, vy / n_          # 太陽→彗星 = 反太陽方向
        draw_comet_tail(float(px), float(py), ux, uy, col)
        draw_comet_core(float(px), float(py), 11, pos)
        _marker_redraws.append(("comet", (float(px), float(py), 11, pos)))
        _marker_redraws.append(("comettail", (float(px), float(py), ux, uy, col)))
        _b = place_rect(320, 42, math.hypot(px - CX, py - CY), prefer=None,
                        ignore_pts=((px, py),))
        d["label_box"] = [int(v) for v in _b]
        dr.rectangle(list(_b), fill=(0, 30, 45, 235))
        _occ_rects.append(tuple(_b))
        _occ_pts.append((px, py, 23))
        dr.text((_b[0] + 6, _b[1] + 3), "☄ {}  {:.1f}AU".format(d["name"], au),
                font=load_font(16, True), fill=(255, 255, 255, 255))
        dr.text((_b[0] + 6, _b[1] + 22), "黄緯 {:.0f}°（黄道面投影 {:.0f}AU）".format(
            float(d.get("eclLat") or 0.0), float(d.get("proj_au") or 0.0)),
            font=load_font(13), fill=(190, 235, 255, 255))


    # ---- 通過経路（破線＋進行方向の矢印） --------------------------------------
    route_draw = {}
    deferred_marks = []
    for rname, rt in (scene.get("routes") or {}).items():
        if rt.get("error") or not rt.get("points"):
            continue
        rcol = tuple(rt.get("color", _COMET_COLOR))
        # 対数縮尺の下限（太陽円盤の内側）より内側の点は線を切る。
        runs, cur = [], []
        for pt in rt["points"]:
            r_ = math.sqrt(sum(float(v) ** 2 for v in pt))
            if r_ < max(lo * 1.02, 1e-6) or (rng_au and r_ > hi):
                if len(cur) > 1:
                    runs.append(cur)
                cur = []
                continue
            cur.append((float(pt[0]), float(pt[1]), float(pt[2])))
        if len(cur) > 1:
            runs.append(cur)
        dash_samples = []
        for run in runs:
            draw_track(dr, run, view, rcol, base_width=2.0, arrows=(0.25, 0.55, 0.85),
                       dashed=True)
            step = max(1, len(run) // 120)
            for i in range(0, len(run) - 1, step):
                if (i // step) % 2:
                    continue
                p1, p2 = view.project(run[i]), view.project(run[i + 1])
                dash_samples.append([round((p1[0] + p2[0]) / 2.0),
                                     round((p1[1] + p2[1]) / 2.0)])
        dr = ImageDraw.Draw(img, "RGBA")
        drawn = []
        disk_px = float(scene.get("sun_disk_px") or 34.0) * 1.3
        for mk in (rt.get("marks") or []):
            pos = mark_xyz(mk)
            mx, my, _d = view.project(pos)
            r_true = float(mk.get("r_au") or mk.get("proj_au") or 0.0)
            rec = {"id": mk.get("id"), "label": mk.get("label"), "au": mk.get("r_au", r_true),
                   "proj_au": float(mk.get("proj_au") or 0.0),
                   "lon_deg": float(mk.get("lon_deg") or 0.0),
                   "px": [round(mx), round(my)], "date": mk.get("date")}
            if rng_au and r_true > hi:
                rec["drawn"] = False
                rec["reason"] = "out_of_range"
                drawn.append(rec)
                rng_report["out_of_range"].append(
                    {"type": "route_mark", "name": rt.get("name") or rname,
                     "label": mk.get("label"), "au": r_true})
                continue
            if r_true < max(lo * 1.02, 1e-6) or math.hypot(mx - CX, my - CY) < disk_px:
                rec["drawn"] = False
                rec["reason"] = "sun_disk" if r_true >= max(lo * 1.02, 1e-6) else "below_scale"
                drawn.append(rec)
                continue
            rec["drawn"] = True
            rec["deferred"] = True
            drawn.append(rec)
            deferred_marks.append((mx, my, rcol))
        route_draw[rname] = {"name": rt.get("name") or rname, "color": list(rcol),
                             "segments": sum(max(0, len(run) - 1) for run in runs),
                             "marks": drawn, "closed": bool(rt.get("closed")),
                             "sun_disk_px": disk_px, "dash_samples": dash_samples,
                             "sun_disk_au": au_at_px(disk_px),
                             "skipped_au_below": (max(lo * 1.02, 1e-6)
                                                  if (rt.get("q_au") or 9) < lo * 1.02 else None)}
    scene["route_draw"] = route_draw

    for (mx, my, rcol_) in deferred_marks:
        draw_route_mark(mx, my, rcol_)
        _marker_redraws.append(("routemark", (mx, my, rcol_)))

    # ---- 1光日のラベル（他のラベルを描いた後に、最も空いている角度へ） -----------
    if ld_draw.get("drawn"):
        _pref = 180.0
        if projections:
            _pref = (projections[0]["eclLon"] + 180.0) % 360.0
        _b = place_rect(520, 34, float(ld_draw.get("r_px") or 0.0), prefer=_pref)
        dr.rectangle(list(_b), fill=(46, 12, 22, 240))
        dr.text((_b[0] + 8, _b[1] + 6),
                "1光日 {:.2f} AU（{:,.0f} km・太陽光の所要 24 時間）".format(
                    LIGHT_DAY_AU, LIGHT_DAY_KM),
                font=load_font(16, True), fill=(255, 190, 200, 255))
        ld_draw["label_box"] = [int(v) for v in _b]
        _occ_rects.append(tuple(_b))
    for pj in projections:
        _occ_pts.append((pj["mark_px"][0], pj["mark_px"][1], 12.0))
    for pj in projections:
        _col = tuple(pj["color"])
        _mx, _my = pj["mark_px"]
        _l1 = "1光日（{}の現在の向きで {:.1f} AU）・◇=到達時の方向".format(pj["name"], pj["au"])
        _l2 = "黄経 {:.1f}°／黄緯 {:.1f}°{}".format(
            pj["eclLon"], pj["eclLat"],
            "・到達予測 {}".format(pj["eta"]) if pj.get("eta") else "")
        _b2 = place_rect(560, 54, float(pj.get("r_px") or 0.0),
                         prefer=(pj["eclLon"] + 120.0) % 360.0,
                         avoid=([tuple(ld_draw["label_box"])] if ld_draw.get("label_box") else ()))
        dr.rectangle(list(_b2), fill=(24, 16, 44, 240))
        dr.polygon([(_mx, _my - 9), (_mx + 9, _my), (_mx, _my + 9), (_mx - 9, _my)],
                   fill=(10, 14, 24, 255), outline=_col + (255,), width=2)
        dr.text((_b2[0] + 8, _b2[1] + 5), _l1, font=load_font(15, True),
                fill=(226, 214, 255, 255))
        dr.text((_b2[0] + 8, _b2[1] + 28), _l2, font=load_font(15, True),
                fill=(226, 214, 255, 255))
        pj["label_box"] = [int(v) for v in _b2]
        _occ_rects.append(tuple(_b2))
    if projections:
        ld_draw["proj_label_box"] = projections[0].get("label_box")
        ld_draw["mark_px"] = projections[0].get("mark_px")
    scene["light_day_draw"] = ld_draw

    # ---- 最終パス: すべてのラベル箱の後にマーカーを描き直す ---------------------
    for _p in _planet_draw:
        draw_planet(*_p)
    for _k, _args in _marker_redraws:
        if _k == "asteroid":
            draw_asteroid(*_args)
        elif _k == "comet":
            draw_comet_core(*_args)
        elif _k == "comettail":
            draw_comet_tail(*_args)
        elif _k == "probe":
            draw_probe(*_args)
        elif _k == "routemark":
            draw_route_mark(*_args)

    # ---- 太陽の名札（最後に、他の名札箱と重ならない位置にだけ置く） --------------
    _f_sun = load_font(18, True)
    for (_sx, _sy) in ((CX + sr + 8, CY + sr - 6), (CX - 62, CY - sr - 26),
                       (CX + sr + 8, CY - sr - 26), (CX - 62, CY + sr + 2)):
        _bb = (int(_sx), int(_sy), int(_sx + 40), int(_sy + 22))
        if _bb[0] < 4 or _bb[2] > W - 4 or _bb[1] < 4 or _bb[3] > H - 60:
            continue
        if any(min(_bb[2], o[2]) - max(_bb[0], o[0]) > 0
               and min(_bb[3], o[3]) - max(_bb[1], o[1]) > 0 for o in _occ_rects):
            continue
        dr.rectangle([_bb[0] - 2, _bb[1] - 1, _bb[2] + 2, _bb[3] + 1], fill=(20, 12, 0, 220))
        dr.text((_sx, _sy), "太陽", font=_f_sun, fill=(255, 233, 184, 255))
        break

    # ---- ヘッダ・フッタ -------------------------------------------------------
    dr.rectangle([0, 0, W, 104], fill=(0, 0, 0, 230))
    parts = []
    for key, label in (("asteroids", "小惑星"), ("probes", "探査機"), ("comets", "彗星")):
        ok = [k for k, v in (scene.get(key) or {}).items() if not v.get("error")]
        if ok:
            parts.append(label + "・".join(ok))
    _cam_el, _cam_az, _cam_dl = oblique_camera(scene)
    title = "太陽系・現在位置（斜め俯瞰・黄道面を仰角{:g}°・方位{:g}°から）".format(_cam_el, _cam_az)
    if parts:
        title += "＋" + "／".join(parts)
    dr.text((26, 16), title, font=load_font(30, True), fill=(255, 255, 255, 255))
    sub = ("{} ・{}・数値=太陽からの距離AU ・弱い透視（カメラ距離 {:.1f}×画面半径）".format(
        scene.get("time_utc", ""),
        "線形縮尺（相対距離は正確）" if linear else "対数縮尺（内惑星〜星間空間を1枚に）",
        _cam_dl))
    if not linear:
        sub += " ・画面上の距離はAUに比例しない"
    dr.text((26, 70), sub, font=load_font(18), fill=(195, 205, 235, 255))

    dr.rectangle([14, H - 54, W - 14, H - 14], fill=(0, 0, 0, 225))
    leg = ("● 惑星（環・縞・極冠は種類別アイコン）  ⬥ 小惑星  ☄彗星（尾は反太陽方向）  "
           "◆探査機  ┈ 通過経路（破線・矢印は進行方向）")
    if ld_draw.get("drawn"):
        leg += "  ○破線円=1光日 {:.2f}AU".format(LIGHT_DAY_AU)
    if rng_report["skipped_orbits"]:
        leg += "  表示範囲外の惑星軌道 {} 本は描いていない".format(
            len(rng_report["skipped_orbits"]))
    leg += "  奥ほど暗く細い（手前が明るい）"
    dr.text((28, H - 40), leg + " ・ 出典: JPL DE421+SBDB+Horizons / Skyfield",
            font=load_font(16), fill=(225, 232, 250, 255))

    scene["range_report"] = rng_report
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _oblique_body(body):
    """1 天体の軌道面図（太陽＝焦点の円錐曲線）を斜め俯瞰の描画系で描く。

    `body` = {"cid", "el", "pos_xyz", "when", "kind"}。軌道面を真横から見る構図は
    変えない（斜め俯瞰へ投影し直すと楕円が潰れ、離心率・焦点が読めなくなる）ため、
    投影は「軌道面を画面に一致させた View」（仰角 90°・線形動径・透視なし）を使う。
    返すのは (PIL画像, figure ブロック)。
    """
    from PIL import Image, ImageDraw, ImageFilter

    from .orbit_view import Camera, RadialScale, View, orbit_segments

    cid = body.get("cid")
    el = body.get("el") or {}
    pos_xyz = body.get("pos_xyz") or (0.0, 0.0, 0.0)
    when_str = body.get("when") or ""
    body_kind = body.get("kind") or "comet"
    body_labels = {"comet": "彗星", "asteroid": "小惑星"}
    if body_kind not in body_labels:
        raise ValueError("未知の軌道天体種別です")
    body_label = body_labels[body_kind]
    body_color = _COMET_COLOR if body_kind == "comet" else symbol_rgb("asteroid_label")
    orbit_color = _COMET_ORBIT_COLOR if body_kind == "comet" else symbol_rgb("asteroid_label")
    W, H = 1400, 900
    sun_r_px = 12.0
    a, e, q = el.get("a"), float(el["e"]), float(el["q"])
    incl = el.get("i_deg")
    if e < 1.0:
        apo = abs(a) * (1.0 + e) if a is not None else None
        r_max = max(apo or q * 4.0, q * 1.5) * 1.06
    else:
        apo = None
        r_max = min(20.0, max(2.0, 8.0 * q))
    xp, yp = _ecliptic_to_perifocal(pos_xyz[0], pos_xyz[1], pos_xyz[2], incl or 0.0,
                                    el.get("node_deg", 0.0), el.get("argp_deg", 0.0))
    r_now = math.hypot(xp, yp)
    # 図の範囲に**現在位置を含める**（実測 2026-10-20: C/2023 A3 は範囲 3.13 AU に対し
    # 現在位置 8.73 AU で、マーカーが画面外 px=[-233,-34] に落ちていた）
    if r_now > r_max * 0.98:
        r_max = r_now * 1.15

    # 軌道面内座標（近日点方向=+x）をそのまま黄道座標として渡し、仰角 90° で投影する
    # ＝ 画面が軌道面に一致する（旧 _render_orbit_plane の to_px と同じ写像）。
    pf_el = {"a": a, "e": e, "i": 0.0, "om": 0.0, "w": 0.0, "q": q}
    pl, ptop, pr, pb = 70, 150, W - 70, H - 150
    X0, Y0 = (pl + pr) / 2.0, (ptop + pb) / 2.0
    for _ in range(3):                       # 縮尺 → 点列 → 最大半径 の不動点を取る
        tmp = View(Camera(90.0, 0.0, 0.0), RadialScale("linear", lo_au=0.0,
                                                       hi_au=max(r_max, 1e-6), r_px=1.0),
                   X0, Y0)
        segs = orbit_segments(pf_el, tmp, r_max)
        r_pts = max([math.hypot(p[0], p[1]) for seg in segs for p in seg] or [r_max])
        r_span = max(r_pts, r_now)
        r_max = max(r_max, r_span * 1.0001)
    ppau = min((pr - pl) / max(2.0 * r_span, 1e-6),
               (pb - ptop) / max(2.0 * r_span, 1e-6)) * 0.92
    view = View(Camera(90.0, 0.0, 0.0),
                RadialScale("linear", lo_au=0.0, hi_au=max(r_span, 1e-6),
                            r_px=ppau * max(r_span, 1e-6)),
                X0, Y0)
    segs = orbit_segments(pf_el, view, r_span * 1.02)

    img = Image.new("RGB", (W, H), (10, 14, 26))
    dr = ImageDraw.Draw(img, "RGBA")
    f_t, f_s, f_m, f_b = load_font(26, True), load_font(17), load_font(20, True), load_font(15)
    boxes = []
    skipped_labels = []
    mask_ink = None
    pts_px = [[view.project(p)[0], view.project(p)[1]] for seg in segs for p in seg]

    def label(cands, text, font, fill):
        """曲線マスクと重ならない候補にだけ描く（どの候補も駄目なら描かず記録する）。"""
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

    # 軌道（深度は使わない: この図は平面を真横から見るので前後が無い）
    for seg in segs:
        dr.line([(view.project(p)[0], view.project(p)[1]) for p in seg],
                fill=tuple(orbit_color) + (255,), width=3)
    _mask = Image.new("1", (W, H), 0)
    _md = ImageDraw.Draw(_mask)
    for seg in segs:
        _md.line([(view.project(p)[0], view.project(p)[1]) for p in seg], fill=1, width=5)
    mask_ink = _mask.filter(ImageFilter.MaxFilter(7))

    hx, hy = view.project((q, 0.0, 0.0))[:2]
    dr.ellipse([hx - 5, hy - 5, hx + 5, hy + 5], fill=(255, 90, 90, 255))
    label([(hx + 12, hy + 12, "la"), (hx + 12, hy - 34, "la"), (hx - 170, hy + 12, "la"),
           (hx - 170, hy - 34, "la"), (hx + 16, hy + 44, "la"), (hx - 190, hy + 44, "la")],
          "近日点", f_s, (255, 150, 150))
    ax_ = ay_ = None
    if apo:
        ax_, ay_ = view.project((-apo, 0.0, 0.0))[:2]
        dr.ellipse([ax_ - 5, ay_ - 5, ax_ + 5, ay_ + 5], fill=(150, 190, 255, 255))
        label([(ax_ - 12, ay_ + 12, "ra"), (ax_ - 12, ay_ - 34, "ra"),
               (ax_ + 14, ay_ + 12, "la"), (ax_ + 14, ay_ - 34, "la"),
               (ax_ - 12, ay_ + 44, "ra"), (ax_ + 14, ay_ + 44, "la")],
              "遠日点", f_s, (150, 190, 255))
    sr = sun_r_px
    # 太陽の光暈は**描かない**。ぼかした光暈を軌道線の上に重ねると、近日点側の曲線色が
    # 薄まって画素から近点距離を測れなくなる（実測: 近点 4.5 px の曲線が光暈で潰れ、
    # verify_curve の測定値が 0.57 → 2.77 AU に跳ねた）。この図は形を読むための図なので
    # 円盤だけを描く（旧 _render_orbit_plane と同じ）。
    dr.ellipse([X0 - sr, Y0 - sr, X0 + sr, Y0 + sr], fill=body_rgb("太陽") + (255,),
               outline=(255, 245, 200, 255), width=2)
    label([(X0 + sr + 8, Y0 - sr - 26, "la"), (X0 + sr + 8, Y0 + sr + 6, "la"),
           (X0 - sr - 96, Y0 - sr - 26, "la"), (X0 - sr - 96, Y0 + sr + 6, "la"),
           (X0 + sr + 8, Y0 - 10, "la"), (X0 - sr - 96, Y0 - 10, "la"),
           (X0 - 46, Y0 + sr + 34, "la"), (X0 - 46, Y0 - sr - 54, "la")],
          "太陽＝焦点", f_m, (255, 235, 170))
    cx_, cy_ = view.project((xp, yp, 0.0))[:2]
    _glow(img, [cx_ - 7, cy_ - 7, cx_ + 7, cy_ + 7], 7, tuple(body_color) + (170,))
    dr = ImageDraw.Draw(img, "RGBA")
    dr.ellipse([cx_ - 7, cy_ - 7, cx_ + 7, cy_ + 7], fill=tuple(body_color) + (255,),
               outline=(255, 255, 255, 255), width=2)
    dr.ellipse([cx_ - 2.6, cy_ - 2.6, cx_ + 2.6, cy_ + 2.6], fill=(255, 255, 255, 240))
    label([(cx_ + 14, cy_ - 40, "la"), (cx_ + 14, cy_ + 16, "la"),
           (cx_ - 250, cy_ - 40, "la"), (cx_ - 250, cy_ + 16, "la"),
           (cx_ + 14, cy_ + 66, "la"), (cx_ - 250, cy_ + 66, "la"),
           (cx_ + 14, cy_ - 90, "la"), (cx_ - 250, cy_ - 90, "la")],
          "現在位置", f_s, (170, 240, 255))
    nu_now = math.atan2(yp, xp)
    nu2 = nu_now + math.radians(0.8)
    p_ = (a * (1.0 - e * e)) if (a is not None and abs(e - 1.0) > 1e-9) else (2.0 * q)
    rr2 = p_ / max(1e-12, 1.0 + e * math.cos(nu2))
    q2 = view.project((rr2 * math.cos(nu2), rr2 * math.sin(nu2), 0.0))[:2]
    ang_a = math.atan2(q2[1] - cy_, q2[0] - cx_)
    dr.line([cx_, cy_, q2[0], q2[1]], fill=(255, 255, 255, 255), width=3)
    for sgn in (1, -1):
        dr.line([(q2[0], q2[1]),
                 (q2[0] - 14 * math.cos(ang_a - 0.45 * sgn),
                  q2[1] - 14 * math.sin(ang_a - 0.45 * sgn))], fill=(255, 255, 255, 255),
                width=3)
    for au in (0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0):
        if au > r_max * 1.02:
            continue
        tx, ty = view.project((-au, 0.0, 0.0))[:2]
        dr.line([(tx, ty - 8), (tx, ty + 8)], fill=(150, 160, 185, 255), width=2)
        label([(tx - 20, ty + 12, "la"), (tx - 20, ty - 28, "la"),
               (tx + 8, ty + 12, "la"), (tx + 8, ty - 28, "la"),
               (tx - 20, ty + 42, "la"), (tx - 20, ty - 58, "la"),
               (tx + 8, ty + 42, "la"), (tx + 8, ty - 58, "la")], _au_fmt(au), f_b,
              (160, 172, 195))
        if abs(au - 1.0) < 1e-9:
            label([(tx - 66, ty - 36, "la"), (tx - 66, ty + 14, "la"),
                   (tx - 66, ty - 62, "la"), (tx + 26, ty - 36, "la"),
                   (tx + 26, ty + 14, "la")], "地球軌道 1 AU", f_b, (140, 175, 240))
    dr.line([(pl + 10, pb + 62), (pl + 10 + 1.0 * ppau, pb + 62)], fill=(210, 214, 230, 255),
            width=3)
    dr.text((pl + 14, pb + 36), "1 AU", font=f_b, fill=(210, 214, 230, 255))

    title = "{} の軌道（{}自身の軌道面を真横から見た図）".format(
        el.get("fullname") or cid, body_label)
    dr.text((36, 26), title, font=f_t, fill=(238, 242, 255, 255))
    dr.text((36, 66), "e={:.6f}・近日点 {} AU{}".format(
        e, _au_fmt(q), "・遠日点 {} AU".format(_au_fmt(apo)) if apo else "・遠日点なし（閉じない軌道）"),
        font=f_s, fill=(255, 200, 140, 255))
    dr.text((36, 92), "観測時刻 {} ・ {}".format(when_str, el.get("source", "")),
            font=f_b, fill=(180, 190, 215, 255))
    dr.rectangle([20, H - 74, W - 20, H - 16], fill=(0, 0, 0, 210))
    dr.text((34, H - 62),
            "☀ 太陽＝焦点（中心ではない）　● 現在位置　赤● 近日点　青● 遠日点　"
            "目盛=日心距離(AU)　太陽・マーカーは実寸ではありません",
            font=f_b, fill=(225, 232, 250, 255))
    dr.text((34, H - 38), "出典: {}".format(el.get("source", "")), font=f_b,
            fill=(190, 200, 220, 255))

    conic = conic_from_elements(a=a, e=e, q=q, incl_deg=incl)
    extra = [
        "●は{}時点の{}位置（日心距離 {:.3f} AU・この軌道面内の実際の位置）".format(
            when_str, body_label, r_now),
        "太陽・マーカーの大きさは誇張している（軌道の縮尺と同一ではない）",
        "惑星や地球の位置は描いていない。目盛の 1 AU は地球軌道の半径（距離の目安）",
    ]
    if body_kind == "asteroid":
        epoch = (el.get("_raw") or {}).get("epoch")
        extra.append("JPL SBDB の osculating elements（epoch JD {}）をケプラー二体で伝播。"
                     "惑星の摂動は含まない".format("不明" if epoch is None else "{:.5f}".format(epoch)))
    else:
        extra.append("要素は指定時刻付近の接触軌道要素。惑星の摂動で実際の道は変わる")
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
        orbiting_body=body_label, extra=extra)
    caption = ("{} の軌道。太陽を焦点とする{}（e={:.6f}、近日点 {} AU{}）。{}時点の日心距離は {:.3f} AU。".format(
        el.get("fullname") or cid, "楕円" if e < 1.0 else "双曲線の枝", e, _au_fmt(q),
        "・遠日点 {} AU".format(_au_fmt(apo)) if apo else "・遠日点なし", when_str, r_now))
    view_description = "{}自身の軌道面を真横から見た図（太陽は円錐曲線の焦点）".format(body_label)

    def _in_frame(px_, py_):
        return bool(6 <= px_ <= W - 6 and 4 <= py_ <= H - 92)

    markers = [{"id": "periapsis", "label": "近日点", "au": q, "px": [round(hx), round(hy)],
                "in_frame": _in_frame(hx, hy)},
               {"id": "current", "label": "現在位置", "au": r_now, "px": [round(cx_), round(cy_)],
                "in_frame": _in_frame(cx_, cy_)}]
    if apo:
        markers.append({"id": "apoapsis", "label": "遠日点", "au": apo,
                        "px": [round(ax_), round(ay_)], "in_frame": _in_frame(ax_, ay_)})
    fig = figure_payload(
        kind="orbit_plane", title=title,
        view=view_spec("orbital_plane", "side", view_description,
                       why="上から見た黄道面俯瞰では高傾斜・高離心率の軌道が潰れて見え、"
                           "焦点と中心の違いも判別できないため"),
        primary=primary_spec("太陽", "focus", center_offset=conic.c, unit="AU"),
        scale=scale_spec("linear", to_scale=True, px_per_unit=ppau, unit="AU",
                         exaggerated=["太陽の円盤", "近日点・現在位置のマーカー"]),
        conic=conic, markers=markers, notes=notes, caption=caption,
        verify=verify_curve(img, color=orbit_color, focus_xy=(X0, Y0),
                            px_per_unit=ppau, periapsis=q, apoapsis=apo,
                            tol_ratio=0.06, label_boxes=boxes,
                            occluders=[(X0, Y0, sun_r_px)]),
    )
    return img, fig


def _verify_oblique(scene, png):
    """斜め俯瞰図の画素検証: 各天体の「期待 px」にマーカー（白点）があるかを確かめる。

    期待値は描画と同じ `orbit_view.View` から出す（図と検証で別の式を使わない）。
    戻り値は既存の figure.verify と同じ流儀（ok / checked / missing / method）。
    """
    import io as _io

    from PIL import Image

    from .orbit_view import expected_px

    view = _oblique_view(scene)
    hi, lo = float(view.scale.hi_au), float(view.scale.lo_au)
    img = Image.open(_io.BytesIO(png)).convert("RGB")
    W, H = img.size
    checked, missing, found = 0, [], []
    for group in ("planets", "asteroids", "comets", "probes"):
        for name, val in (scene.get(group) or {}).items():
            if val.get("error") or not val.get("au"):
                continue
            au = float(val["au"])
            if not _oblique_visible(view, au):
                continue
            pos = _xyz_from_au_lonlat(au, val.get("eclLon") or 0.0, val.get("eclLat") or 0.0)
            px, py = expected_px(view, pos)
            checked += 1
            best = 0
            for dx in range(-6, 7):
                for dy in range(-6, 7):
                    x, y = px + dx, py + dy
                    if 0 <= x < W and 0 <= y < H:
                        r, g, b = img.getpixel((x, y))
                        best = max(best, min(r, g, b))
            if best >= 200:
                found.append({"name": name, "pixels_found": 1, "at": [px, py]})
            else:
                missing.append({"name": name, "at": [px, py], "max_rgb": best})
    return {"ok": not missing, "checked": checked, "method": "oblique_expected_px+marker_core",
            "found": found, "missing": missing}


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
                        # 破線は半透明＋ぼかしで背景と混ざる。彗星色（シアン系）に近い、
                        # または「青緑寄り」／探査機の黄色・橙色系に持ち上がっている画素なら
                        # 描けているとみなす。背景(11,16,38)はどの判定にも落ちる。
                        _near = abs(c[0] - col[0]) + abs(c[1] - col[1]) + abs(c[2] - col[2]) <= 60
                        _cyan = (c[1] - c[0] >= 18 and c[2] - c[0] >= 25 and c[2] >= 60
                                 and c[0] < 190)
                        # 探査機の経路は黄色〜橙色（例: ボイジャー1号 (255,214,90)）。
                        # 半透明＋ぼかしで背景と混ざるので、赤が青より明確に高い画素を拾う。
                        _warm = (c[0] - c[2] >= 40 and c[0] >= 120 and c[1] >= 100)
                        if _near or _cyan or _warm:
                            found += 1
                hits.append(found)
            row["dash_samples"] = len(hits)
            row["dash_hits"] = sum(1 for h in hits if h > 0)
            # 長距離の探査機（ボイジャー等）は経路が画面の外へ大きく出るため、
            # 代表点の全部が画素を持つとは限らない。90%は厳しすぎる（実測: 18/115）。
            # 少なくとも一部が実在すれば「線を引けている」と判定する。
            _need = max(1, int(0.5 * len(hits))) if len(hits) <= 20 else max(3, int(0.2 * len(hits)))
            if row["dash_hits"] < _need:
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
    # 誇張した太陽の円盤が最内惑星を覆っていないかは **px 空間**で比べる（描画は px で
    # 大きさを決めているので、対数縮尺で「AU 相当」に直すと縮尺に依存して判定が壊れる）。
    _sun_px, _inner_px = scene.get("sun_disk_px"), scene.get("inner_planet_px")
    if _sun_px and _inner_px:
        rep["sun_disk_vs_inner_planet"] = {
            "sun_disk_px": round(float(_sun_px), 1),
            "inner_planet_px": round(float(_inner_px), 1),
            "ok": float(_sun_px) < float(_inner_px) * 0.75}
    rep["ok"] = (bool(rep["planets_drawn"])
                 and all(c["ok"] for c in rep["planets_drawn"])
                 and all(c["ok"] for c in rep["out_of_range_zero_pixels"])
                 and all((rep.get("sun_disk_vs_inner_planet") or {}).get("ok", True)
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
        # 1光日到達時の方向の◇（マーカー）を画素で確かめる。輪郭は探査機色なので、
        # その色の画素が◇の位置に残っているかを見る（ラベル箱に隠れていれば落ちる）。
        mpx_ = pj.get("mark_px") or ld.get("mark_px") or [W // 2, H // 2]
        hit, checked = 0, 1
        for yy in range(max(0, int(mpx_[1]) - 12), min(H, int(mpx_[1]) + 13)):
            for xx in range(max(0, int(mpx_[0]) - 12), min(W, int(mpx_[0]) + 13)):
                if _near(px[xx, yy], tuple(pj.get("color") or (255, 214, 90)), 70):
                    hit += 1
        label_box = pj.get("label_box") or ld.get("proj_label_box")
        row = {"kind": "probe_projection", "name": pj.get("name"), "au": pj.get("au"),
               "r_px": round(float(pj.get("r_px") or 0.0), 1), "mark_hits": hit,
               "mark_px": mpx_, "label_box": label_box, "ok": hit >= 8}
        if not row["ok"]:
            rep["ok"] = False
            rep["missing"].append("{} の◇（1光日到達時の方向）の画素が見つからない（{} px）".format(
                pj.get("name"), hit))
        row["label_text_px"] = _count(label_box, (226, 214, 255)) if label_box else 0
        if row["label_text_px"] < 600:
            row["ok"] = False
            rep["ok"] = False
            rep["missing"].append("{} の1光日到達方向ラベル文字が画素で見つからない（{} px）".format(
                pj.get("name"), row["label_text_px"]))
        mpx = pj.get("probe_marker_px")
        if not pj.get("probe_marker_drawn", True):
            # 表示範囲外で**描いていない**マーカーは検査しない（描画と同じ可視判定）。
            row["probe_marker_checked"] = False
            row["probe_marker_skip"] = "探査機 {:.2f} AU は表示範囲外のため描いていない".format(
                float(pj.get("probe_au") or 0.0))
        elif mpx:
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

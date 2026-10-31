"""天体中心 3D の斜め俯瞰（周回機の軌道面・高度・傾斜を見せる）。

地表図（`ground_track_map`）は「真下の点の軌跡」なので、軌道面の傾き・高度・
どちら向きに回っているかが読み取れない。このモジュールは Horizons の状態ベクトルから
天体固定座標系の 3D 位置を作り、`orbit_view` と同じ弱い透視で斜めから描く。

座標系: 天体固定（x=経度0°, y=東経90°, z=北極）。単位は km。
投影: `orbit_view.View` を**線形・km**で使う（動径圧縮は r_px/hi_km の一様係数になり、
       形は本当の軌道と一致する）。カメラ（仰角・方位・dolly）は `orbit_view.Camera` と同一。

検証思想: 描画と検証が同じ `View` から期待 px を出す（`_verify` の盲目化を防ぐ）。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .img_common import BODY_COLORS, load_font
from .orbit_view import Camera, RadialScale, View

Point3 = Tuple[float, float, float]


def to_xyz(lat_deg: float, lon_deg: float, alt_km: float, r_body_km: float) -> Point3:
    """天体固定の緯度経度高度 → 3D [km]。"""
    r = float(r_body_km) + float(alt_km)
    la, lo = math.radians(float(lat_deg)), math.radians(float(lon_deg))
    return (r * math.cos(la) * math.cos(lo), r * math.cos(la) * math.sin(lo), r * math.sin(la))


def orbit_normal(points: Sequence[Point3]) -> Point3:
    """点列から軌道面の法線（面積ベクトルの和）を求める。"""
    nx = ny = nz = 0.0
    for i in range(len(points) - 1):
        (x1, y1, z1), (x2, y2, z2) = points[i], points[i + 1]
        nx += y1 * z2 - z1 * y2
        ny += z1 * x2 - x1 * z2
        nz += x1 * y2 - y1 * x2
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm <= 0.0:
        return (0.0, 0.0, 1.0)
    return (nx / norm, ny / norm, nz / norm)


def inclination_from_normal(normal: Point3) -> Tuple[float, bool]:
    """軌道傾斜角（0–180°）と逆行かどうか。

    `normal` は**天体の自転軸と同じ座標系**で表した軌道面の法線。0°=赤道面順行、
    90°=極軌道、180°=赤道面逆行。90° を超えると逆行。
    """
    nz = max(-1.0, min(1.0, normal[2]))
    ang = math.degrees(math.acos(nz))
    return ang, ang > 90.0


def _plane_basis(normal: Point3) -> Tuple[Point3, Point3]:
    """軌道面内の正規直交基底。"""
    nx, ny, nz = normal
    ref = (0.0, 0.0, 1.0) if abs(nz) < 0.9 else (1.0, 0.0, 0.0)
    ux = ny * ref[2] - nz * ref[1]
    uy = nz * ref[0] - nx * ref[2]
    uz = nx * ref[1] - ny * ref[0]
    un = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    u = (ux / un, uy / un, uz / un)
    w = (ny * u[2] - nz * u[1], nz * u[0] - nx * u[2], nx * u[1] - ny * u[0])
    return u, w


def _shade(col: Tuple[int, int, int], depth: float, span: float) -> Tuple[int, int, int]:
    """奥（depth<0）ほど暗く。span は depth の目安レンジ（px）。

    コントラストは強めにする（弱いと「円盤の上に線を引いた」ように見える）。
    """
    t = 0.5 if span <= 0 else max(0.0, min(1.0, (depth / span + 1.0) / 2.0))
    k = 0.28 + 0.72 * t
    return tuple(int(max(0, min(255, c * k))) for c in col)


def _tag(draw, xy: Tuple[float, float], text: str, colour, font) -> None:
    """背景付きの小ラベル（図中の文字がグリッドに埋もれるのを防ぐ）。"""
    x, y = xy
    bb = draw.textbbox((0, 0), text, font=font)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    draw.rectangle([x - 4, y - 2, x + w + 4, y + h + 4], fill=(8, 10, 18), outline=None)
    draw.text((x, y), text, font=font, fill=colour)


def _dashed(draw, pts: List[Tuple[float, float]], colour, width: int, on: int = 7, off: int = 6):
    """点列を破線で描く（長さで on/off を切り替える）。"""
    acc, lit = 0.0, True
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg <= 0:
            continue
        t = 0.0
        while t < seg:
            step = min((on if lit else off) - acc, seg - t)
            a = (x1 + (x2 - x1) * t / seg, y1 + (y2 - y1) * t / seg)
            t += step
            b = (x1 + (x2 - x1) * t / seg, y1 + (y2 - y1) * t / seg)
            if lit:
                draw.line([a, b], fill=colour, width=width)
            acc += step
            if acc >= (on if lit else off) - 1e-9:
                acc, lit = 0.0, not lit


def render(body_cfg: Dict, trail: Sequence[Dict], cur: Dict, *, ja: str, en: str = "",
           when_str: str = "", elevation: float = 34.0, azimuth: float = 28.0,
           dolly: float = 4.2, out_px: int = 1000, minutes: float = 90.0,
           frame_note: str = "", inclination_deg: Optional[float] = None,
           retrograde: Optional[bool] = None,
           inclination_source: str = "inertial_pole") -> Dict:
    """天体中心 3D 斜め俯瞰を描く。

    `trail` は時系列の [{"lon", "lat", "alt_km"}, ...]（現在位置を含む）。
    `cur` は現在（または最接近）の {"lon", "lat", "alt_km", "dist_km", "speed_kms"}。

    `inclination_deg` / `retrograde` は**呼び側が慣性系で求めた値**を渡す（推奨）。
    渡されないときは天体固定座標の点列から求めるが、天体の自転で座標系が回るため
    **数度ずれる**（火星で 90 分あたり約 22°回る）。その場合は `inclination_method` に
    "body_fixed" と記録し、注記にも概算である旨を出す。
    """
    R = float(body_cfg["radius"])
    body_ja = body_cfg.get("ja", "")
    pts = [to_xyz(p["lat"], p["lon"], p["alt_km"], R) for p in trail]
    if len(pts) < 3:
        raise ValueError("軌道点が 3 点未満です（minutes を広げてください）")
    radii = [math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in pts]
    r_min, r_max = min(radii), max(radii)
    hi_km = max(r_max * 1.10, R * 1.05)

    W = int(max(600, min(1600, out_px)))
    r_px = W * 0.40
    view = View(Camera(float(elevation), float(azimuth), float(dolly)),
                RadialScale(kind="linear", lo_au=0.0, hi_au=hi_km, r_px=r_px),
                W / 2.0, W / 2.0)
    depth_span = r_px * (r_max / hi_km)

    img = Image.new("RGB", (W, W), (6, 8, 14))
    d = ImageDraw.Draw(img)
    rnd = random.Random(20261031)
    for _ in range(340):                            # 背景の星（決定論的）
        x, y = rnd.randrange(W), rnd.randrange(W)
        v = rnd.randrange(40, 120)
        d.point((x, y), fill=(v, v, min(255, v + 12)))

    # 天体の円盤（実寸）＋縁の暗さ
    cx, cy = view.cx, view.cy
    r_body_px = view.scale.radius_px(R)
    base = BODY_COLORS.get(body_ja) if isinstance(BODY_COLORS.get(body_ja), tuple) else None
    base = base or (120, 118, 112)
    d.ellipse([cx - r_body_px, cy - r_body_px, cx + r_body_px, cy + r_body_px],
              fill=tuple(int(c * 0.55) for c in base))
    d.ellipse([cx - r_body_px * 0.97, cy - r_body_px * 0.97,
               cx + r_body_px * 0.97, cy + r_body_px * 0.97],
              outline=tuple(int(c * 0.95) for c in base), width=max(2, W // 320))

    # 緯度経度（見えている半球だけ濃く）
    def _grat(lat: Optional[float] = None, lon: Optional[float] = None):
        seg_vis, seg_hid = [], []
        n = 145
        for k in range(n):
            if lat is not None:
                lo = 360.0 * k / (n - 1)
                la = lat
            else:
                la = -90.0 + 180.0 * k / (n - 1)
                lo = lon
            px, py, dep = view.project(to_xyz(la, lo, 0.0, R))
            (seg_vis if dep > 0 else seg_hid).append((px, py))
        if len(seg_hid) > 1:
            d.line(seg_hid, fill=(24, 30, 42), width=1)
        if len(seg_vis) > 1:
            d.line(seg_vis, fill=(52, 62, 82), width=max(1, W // 900))

    for la in (-60, -30, 0, 30, 60):
        _grat(lat=la)
    for lo in range(0, 360, 45):
        _grat(lon=lo)

    # 軌道面の法線から傾斜を出す。慣性系で求めた値が渡されていればそちらを使う
    # （天体固定座標は自転で回るため、長い時間窓では平面フィットが数度ずれる）。
    nrm = orbit_normal(pts)
    if inclination_deg is not None:
        incl = float(inclination_deg)
        retro = bool(retrograde) if retrograde is not None else incl > 90.0
        incl_method = inclination_source
    else:
        incl, retro = inclination_from_normal(nrm)
        incl_method = "body_fixed_approx"

    # 遠点・近点リング（軌道面内・破線）＋赤道面リング（基準面・実線）
    u, w = _plane_basis(nrm)

    def _ring3d(r_ring: float, basis_u, basis_w) -> List[Tuple[float, float]]:
        pts_ring = []
        for k in range(121):
            th = 2 * math.pi * k / 120
            p = (r_ring * (basis_u[0] * math.cos(th) + basis_w[0] * math.sin(th)),
                 r_ring * (basis_u[1] * math.cos(th) + basis_w[1] * math.sin(th)),
                 r_ring * (basis_u[2] * math.cos(th) + basis_w[2] * math.sin(th)))
            px, py, _ = view.project(p)
            pts_ring.append((px, py))
        return pts_ring

    # 近点と遠点が画面上で重なる（高度差が小さい）ときは輪が読めないので、
    # 軌道上の目盛り（垂直の短線＋数値）で示す。
    sep_px = abs(view.scale.radius_px(r_max) - view.scale.radius_px(r_min))
    rings_visible = sep_px >= 8.0
    f_lab = load_font(max(13, W // 56), bold=True)
    if rings_visible:
        _dashed(d, _ring3d(r_max, u, w), (110, 145, 190), max(1, W // 700))
        _dashed(d, _ring3d(r_min, u, w), (120, 185, 135), max(1, W // 700))
    else:
        for r_target, col, lab in ((r_min, (120, 200, 140), "近点"),
                                   (r_max, (130, 165, 205), "遠点")):
            k_idx = min(range(len(radii)), key=lambda i: abs(radii[i] - r_target))
            tx, ty, _ = view.project(pts[k_idx])
            dx, dy = tx - cx, ty - cy
            nn = math.hypot(dx, dy) or 1.0
            px_out = (tx + dx / nn * 13, ty + dy / nn * 13)
            px_in = (tx - dx / nn * 13, ty - dy / nn * 13)
            d.line([px_in, px_out], fill=col, width=max(3, W // 300))
            d.ellipse([tx - 4, ty - 4, tx + 4, ty + 4], fill=col)
            _tag(d, (tx + dx / nn * 18, ty + dy / nn * 18),
                 "{} {:,.0f} km".format(lab, r_target - R), col, f_lab)
    # 赤道面（自転軸に直交する面）は基準なので実線＋シアンで明示する
    eq_ring = _ring3d(R * 1.0, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    d.line(eq_ring, fill=(70, 190, 220), width=max(2, W // 520))
    eq_lab = "赤道面"
    ex, ey = eq_ring[0]
    _tag(d, (ex + 6, ey - 22), eq_lab, (110, 205, 230), f_lab)
    # 軌道面のリング（遠点リングと同じ半径）に名札
    op_ring = _ring3d(r_max, u, w)
    ox, oy = op_ring[len(op_ring) // 2]
    _tag(d, (ox + 6, oy + 4), "軌道面", (150, 175, 215), f_lab)

    # 傾斜角の弧（赤道面 ↔ 軌道面）。両平面の共通軸まわりに短い弧を描く。
    try:
        ax = (nrm[1] * 1.0 - nrm[2] * 0.0, nrm[2] * 0.0 - nrm[0] * 1.0, 0.0)  # n_eq × n_orb
        axn = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
        if axn > 1e-6:
            ax = (ax[0] / axn, ax[1] / axn, ax[2] / axn)
            d1 = (ax[1] * 0.0 - ax[2] * 1.0, ax[2] * 0.0 - ax[0] * 0.0, 0.0)  # ax × n_eq
            n1 = math.sqrt(sum(c * c for c in d1)) or 1.0
            d1 = tuple(c / n1 for c in d1)
            d2 = (ax[1] * nrm[2] - ax[2] * nrm[1], ax[2] * nrm[0] - ax[0] * nrm[2],
                  ax[0] * nrm[1] - ax[1] * nrm[0])                              # ax × n_orb
            n2 = math.sqrt(sum(c * c for c in d2)) or 1.0
            d2 = tuple(c / n2 for c in d2)
            r_arc = R * 1.32
            arc_pts = []
            for k in range(41):
                t = k / 40.0
                cth, sth = math.cos(t * math.pi / 2), math.sin(t * math.pi / 2)
                v = tuple(d1[i] * cth + d2[i] * sth for i in range(3))
                nv = math.sqrt(sum(c * c for c in v)) or 1.0
                p = tuple(r_arc * c / nv for c in v)
                px, py, _ = view.project(p)
                arc_pts.append((px, py))
            d.line(arc_pts, fill=(255, 235, 150), width=max(2, W // 420))
            mx, my = arc_pts[len(arc_pts) // 2]
            f_arc = load_font(max(17, W // 44), bold=True)
            _tag(d, (mx + 10, my - 14), "i={:.1f}°".format(incl), (255, 245, 190), f_arc)
            for dd in (d1, d2):
                p0 = tuple(R * 1.05 * c for c in dd)
                p1 = tuple(R * 1.40 * c for c in dd)
                x0, y0, _ = view.project(p0)
                x1, y1, _ = view.project(p1)
                d.line([(x0, y0), (x1, y1)], fill=(120, 130, 150), width=max(1, W // 900))
    except Exception:
        pass

    # 軌道（奥は暗く、天体の陰に入る部分はさらに暗く）
    orbit_col = (255, 150, 60)
    xy, deps = [], []
    for p in pts:
        px, py, dep = view.project(p)
        xy.append((px, py))
        deps.append(dep)
    behind = 0
    for i in range(len(xy) - 1):
        x1, y1 = xy[i]
        hidden = (deps[i] < 0 and (x1 - cx) ** 2 + (y1 - cy) ** 2 < r_body_px ** 2)
        if hidden:
            behind += 1
        col = _shade(orbit_col, deps[i], depth_span)
        if hidden:
            col = tuple(int(c * 0.35) for c in col)
        d.line([xy[i], xy[i + 1]], fill=col, width=max(2, W // 260))

    # 現在位置（白核＋色リング＝画素検証の的）
    cxyz = to_xyz(cur["lat"], cur["lon"], cur["alt_km"], R)
    mpx, mpy, mdep = view.project(cxyz)
    rr = max(6, W // 110)
    d.ellipse([mpx - rr - 3, mpy - rr - 3, mpx + rr + 3, mpy + rr + 3], outline=(255, 90, 60), width=3)
    d.ellipse([mpx - rr, mpy - rr, mpx + rr, mpy + rr], fill=(255, 255, 255))

    # 北極軸（自転軸＝z）を見せる短い線
    px_n, py_n, _ = view.project((0.0, 0.0, R * 1.35))
    px_s, py_s, _ = view.project((0.0, 0.0, -R * 1.35))
    d.line([(px_s, py_s), (px_n, py_n)], fill=(70, 82, 104), width=max(1, W // 700))
    f_tiny = load_font(max(12, W // 62))
    d.text((px_n + 6, py_n - 16), "自転軸", font=f_tiny, fill=(96, 108, 130))
    d.text((cx - d.textlength(body_ja, font=load_font(max(14, W // 50), bold=True)) / 2,
            cy + r_body_px + 8), body_ja, font=load_font(max(14, W // 50), bold=True),
           fill=(200, 208, 226))

    # 見出し
    f_big = load_font(max(17, W // 36), bold=True)
    f_mid = load_font(max(13, W // 48))
    title = f"🛰 {ja}（{en}）の{body_ja}周回軌道 — 斜め俯瞰 3D"
    d.text((18, 14), title, font=f_big, fill=(240, 244, 252))
    cam_line = "カメラ: 仰角{:g}°・方位{:g}°・{}／奥ほど暗い".format(
        float(elevation), float(azimuth),
        "透視 dolly={:g}".format(float(dolly)) if float(dolly) > 0 else "正射影")
    d.text((18, 14 + (f_big.getbbox(title)[3] - f_big.getbbox(title)[1]) + 8), cam_line,
           font=f_mid, fill=(150, 162, 186))
    if when_str:
        d.text((18, 14 + 2 * ((f_big.getbbox(title)[3] - f_big.getbbox(title)[1]) + 8)),
               f"時刻 {when_str} ・トレイル 前後 {minutes:g} 分", font=f_mid, fill=(150, 162, 186))

    # 数値パネル（左下）
    lines = [
        f"軌道傾斜 i = {incl:.1f}°{'（逆行）' if retro else '（順行）'}  ※{body_ja}の赤道面に対して",
        f"高度 {min(radii) - R:.0f} 〜 {max(radii) - R:.0f} km（中心距離 {r_min:.0f} 〜 {r_max:.0f} km）",
        f"現在 {cur['alt_km']:.1f} km ・ {body_ja}中心から {cur['dist_km']:.1f} km"
        + (f" ・ {cur['speed_kms']:.2f} km/s" if cur.get("speed_kms") else ""),
        (f"{body_ja}半径 {R:.1f} km・円盤は実寸／破線の輪＝軌道面内の近点（緑）と遠点（青）"
         if rings_visible else
         f"{body_ja}半径 {R:.1f} km・円盤は実寸／近点（緑）と遠点（青）は軌道上の目盛り"),
    ]
    if frame_note:
        lines.append(frame_note)
    f_note = load_font(max(13, W // 54))
    lh = f_note.getbbox("あ")[3] + 8
    legend_h = 28
    y0 = W - 18 - lh * len(lines)
    d.rounded_rectangle([12, y0 - 10 - legend_h, W - 12, W - 10], radius=10, fill=(8, 10, 18))
    # 凡例（色と意味の対応。図だけでは意味が取れないため必須）
    f_leg = load_font(max(12, W // 60), bold=True)
    lx, ly = 22, y0 - 8 - legend_h + 4
    for col, txt in (((70, 190, 220), "赤道面"), ((255, 150, 60), "軌道"),
                     ((255, 255, 255), "現在位置"), ((255, 235, 150), "傾斜角"),
                     ((120, 185, 135), "近点"), ((110, 145, 190), "遠点")):
        if col == (255, 255, 255):
            # 実描画のマーカー（白丸＋橙リング）と同じ見た目にする
            d.ellipse([lx, ly + 2, lx + 15, ly + 15], fill=col, outline=(255, 90, 60), width=2)
        else:
            d.rectangle([lx, ly + 5, lx + 16, ly + 13], fill=col)
        d.text((lx + 22, ly), txt, font=f_leg, fill=(195, 205, 225))
        lx += 22 + d.textlength(txt, font=f_leg) + 18
    for i, t in enumerate(lines):
        d.text((22, y0 + i * lh), t, font=f_note,
               fill=(190, 200, 220) if i else (255, 210, 150))

    # ---- 自己検証: 期待 px と実際の画素 ----
    px0, py0 = mpx, mpy
    core = 0
    r_check = int(rr + 2)
    for xx in range(max(0, int(px0) - r_check), min(W, int(px0) + r_check + 1)):
        for yy in range(max(0, int(py0) - r_check), min(W, int(py0) + r_check + 1)):
            c = img.getpixel((xx, yy))
            if c[0] > 235 and c[1] > 235 and c[2] > 235:
                core += 1
    ring_px = 0
    for xx in range(max(0, int(px0) - r_check - 8), min(W, int(px0) + r_check + 9)):
        for yy in range(max(0, int(py0) - r_check - 8), min(W, int(py0) + r_check + 9)):
            c = img.getpixel((xx, yy))
            if c[0] > 200 and c[1] < 130 and c[2] < 110:
                ring_px += 1
    orbit_px = 0
    step = max(1, W // 220)
    for i in range(0, W, step):
        for j in range(0, W, step):
            c = img.getpixel((i, j))
            if c[0] > 150 and 90 < c[1] < 200 and c[2] < 120:
                orbit_px += 1
    verify = {
        "method": "orbit3d_expected_px+marker_core",
        "current_px": [round(px0, 1), round(py0, 1)],
        "marker_core_pixels": core,
        "marker_ring_pixels": ring_px,
        "orbit_pixels_sampled": orbit_px,
        "segments_behind_body": behind,
        "body_radius_px": round(r_body_px, 2),
        "expected_body_radius_px": round(view.scale.radius_px(R), 2),
        "ok": bool(core > 20 and ring_px > 30 and orbit_px > 10),
    }
    return {
        "img": img,
        "verify": verify,
        "inclination_deg": round(incl, 2),
        "inclination_method": incl_method,
        "retrograde": bool(retro),
        "altitude_min_km": round(r_min - R, 1),
        "altitude_max_km": round(r_max - R, 1),
        "center_distance_min_km": round(r_min, 1),
        "center_distance_max_km": round(r_max, 1),
        "px_per_km": round(view.scale.radius_px(1.0), 5),
        "camera": {"elevation_deg": float(elevation), "azimuth_deg": float(azimuth),
                   "dolly": float(dolly)},
        "view": view,
    }

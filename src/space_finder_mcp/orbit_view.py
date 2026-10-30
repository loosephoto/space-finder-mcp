"""斜め俯瞰ビューの投影と描画部品（正射影・対数動径）。

太陽を中心とする軌道図を「黄道面の斜め上から見た」構図で描くための共通部品。
既存の `solar_system._render_simple`（真上・対数動径）を置き換えるものではなく、
**投影だけを差し替える**ために使う（`view="oblique"` を足すときの下請け）。

## 投影（正射影）

    ρ(r) = r_px·(log10 r − log10 lo)/(log10 hi − log10 lo)      # 対数動径（既定）
    ρ(r) = r_px·r/hi                                            # 線形（近距離の拡大図）
    p′   = p·ρ(|p|)/|p|                                          # 動径圧縮（3D のまま）
    xr   = p′x·cosAZ − p′y·sinAZ
    yr   = p′x·sinAZ + p′y·cosAZ
    S    = yr·sinEL + p′z·cosEL                                  # 画面上方向の成分
    depth = p′z·sinEL − yr·cosEL                                 # + が手前（カメラ側）
    f    = D/(D − depth)    where D = dolly·r_px                 # 弱い透視（dolly≤0 なら f=1）
    px   = cx + f·xr
    py   = cy − f·S

- **弱い透視**（`Camera.dolly`）。`f = D/(D−depth)`, `D = dolly·r_px`。
- 仰角 34°・方位 28° が既定。35.264°（= arcsin 1/√3）＋方位 45° にすると等角投影になり、
  3 軸の投影長がすべて √(2/3) で揃う（見た目は 34° とほぼ同じ）。
- 対数動径では **画面上の距離は AU に比例しない**。凡例に実距離を併記すること。
- 対数動径は形状を保存しない（楕円が楕円でなくなる）。形状を見せたい図では線形を使う。

## 使い方

    from .orbit_view import Camera, RadialScale, View, orbit_segments, draw_orbit
    view = View(Camera(), RadialScale(hi_au=190.0), cx=750, cy=750)
    segs = orbit_segments({"a": 17.9, "e": 0.9679, "i": 162.2, "om": 58.4, "w": 111.3,
                           "q": 0.575}, view, r_max_au=190.0)
    draw_orbit(draw, segs, view, (95, 224, 208), kind="comet")
    draw_marker(draw, pos_xyz, view, (95, 224, 208))

探査機の経路（Horizons の点列）・スケールバー・凡例は Phase 2 で `solar_system` に接続するときに
このモジュールへ戻す（試作は `hermes-figs/render_v6.py`）。

出典: JPL SBDB（要素）／ JPL Horizons（探査機の点列）。座標は黄道 J2000・AU。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

AU_KM = 1.495978707e8


Point3 = Tuple[float, float, float]
Segment = List[Point3]


# --------------------------------------------------------------------------- 設定
@dataclass(frozen=True)
class Camera:
    """視点。黄道面からの仰角と、黄道面内の方位（度）。

    `dolly` はカメラ距離を「画面半径の何倍か」で表したもの（既定 4.2 = 弱い透視）。
    0 以下にすると完全な正射影になる（`verify` の期待 px を解析値で固定したいとき用）。
    """

    elevation_deg: float = 34.0
    azimuth_deg: float = 28.0
    dolly: float = 4.2


@dataclass(frozen=True)
class RadialScale:
    """動径の写像。kind="log"（既定）は 0.3〜190 AU のような広い範囲を 1 枚に収める。

    `lo_au` は log の下限。`range_au` を絞る図では `lo_au=min(lo, hi*0.05)` を呼び側で決める
    （既存 `_render_simple` と同じ規則）。
    """

    kind: str = "log"
    lo_au: float = 0.30
    hi_au: float = 190.0
    r_px: float = 620.0

    def radius_px(self, r_au: float) -> float:
        """太陽からの距離 [AU] → 画面半径 [px]。"""
        r = float(r_au)
        if self.kind == "linear":
            return self.r_px * r / max(1e-12, self.hi_au)
        lo = max(1e-12, self.lo_au)
        hi = max(lo * 1.000001, self.hi_au)
        # lo より内側（近日点が lo 未満の彗星など）は lo に丸める。log(0) を避けるため。
        r = max(lo, r)
        return self.r_px * (math.log10(r) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))

    def compress(self, p: Point3) -> Point3:
        """動径だけを圧縮し、向きは保つ（3D ベクトルに係数を掛ける）。"""
        r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
        if r <= 0.0:
            return (0.0, 0.0, 0.0)
        k = self.radius_px(r) / r
        return (p[0] * k, p[1] * k, p[2] * k)


@dataclass(frozen=True)
class View:
    """カメラ＋動径写像＋画面中心。project() が唯一の「AU→px」経路。"""

    camera: Camera = Camera()
    scale: RadialScale = RadialScale()
    cx: float = 750.0
    cy: float = 750.0

    @property
    def _el(self) -> float:
        return math.radians(self.camera.elevation_deg)

    @property
    def _az(self) -> float:
        return math.radians(self.camera.azimuth_deg)

    def project(self, p: Point3) -> Tuple[float, float, float]:
        """黄道 J2000 の位置 [AU] → (px, py, depth_px)。depth は + が手前。

        `Camera.dolly` による弱い透視。`dolly<=0` のときは正射影（f=1）。
        """
        x, y, z = self.scale.compress(p)
        xr = x * math.cos(self._az) - y * math.sin(self._az)
        yr = x * math.sin(self._az) + y * math.cos(self._az)
        # カメラ方向の単位ベクトル v = (−sinAZ·cosEL, −cosAZ·cosEL, sinEL) への射影。
        # p·v が大きいほど手前（北から見れば +z が手前）。符号を逆にすると奥が明るくなる。
        depth = z * math.sin(self._el) - yr * math.cos(self._el)
        s_up = yr * math.sin(self._el) + z * math.cos(self._el)
        f = 1.0
        if self.camera.dolly > 0:
            dist = self.camera.dolly * self.scale.r_px
            if depth < 0.9 * dist:          # カメラに近づき過ぎたら透視を切る（発散防止）
                f = dist / (dist - depth)
        return self.cx + f * xr, self.cy - f * s_up, depth

    def ring_point(self, r_au: float, theta_deg: float) -> Point3:
        """ラベル配置用: 半径 r_au の円環上の点（斜め投影で楕円になる）。"""
        th = math.radians(theta_deg)
        return (r_au * math.cos(th), r_au * math.sin(th), 0.0)

    def orbit_ring_px(self, r_au: float, theta_deg: float) -> Tuple[float, float]:
        """`ring_point` を投影した (px, py)。ラベルの走査半径に使う。"""
        px, py, _ = self.project(self.ring_point(r_au, theta_deg))
        return px, py


# --------------------------------------------------------------------------- 円錐曲線
def conic_kind(e: float) -> str:
    """離心率から軌道の種類を返す（"ellipse" / "parabola" / "hyperbola"）。"""
    e = float(e)
    if e < 1.0 - 1e-9:
        return "ellipse"
    if abs(e - 1.0) <= 1e-9:
        return "parabola"
    return "hyperbola"


def conic_radius(e: float, nu_deg: float, a: Optional[float] = None,
                 q: Optional[float] = None) -> float:
    """真近点角 ν [度] における動径 [AU]。

    楕円は `a, e` から `r = a(1−e²)/(1+e·cosν)`。e≥1（と a 欠損）は近日点距離 q から
    `r = q(1+e)/(1+e·cosν)` を使う（SBDB は e>1 で a を負・`per`/`ad` を null で返す）。
    """
    e = float(e)
    nu = math.radians(float(nu_deg))
    den = 1.0 + e * math.cos(nu)
    if e < 1.0 - 1e-9 and a is not None:
        num = float(a) * (1.0 - e * e)
    else:
        if q is None:
            if a is None:
                raise ValueError("e>=1 では q（近日点距離）が必要です")
            q = float(a) * (1.0 - e)
        num = float(q) * (1.0 + e)
    if den <= 1e-12:
        return float("inf")
    return num / den


def nu_limit_deg(e: float) -> float:
    """双曲線で描ける真近点角の上限（漸近線の手前 0.4°）。"""
    e = float(e)
    if conic_kind(e) != "hyperbola":
        return 180.0
    return math.degrees(math.acos(-1.0 / e)) - 0.4


def _xyz(r: float, nu_deg: float, i_deg: float, om_deg: float, w_deg: float) -> Point3:
    nu = math.radians(nu_deg)
    u = math.radians(w_deg) + nu
    ci, si = math.cos(math.radians(i_deg)), math.sin(math.radians(i_deg))
    co, so = math.cos(math.radians(om_deg)), math.sin(math.radians(om_deg))
    return (r * (co * math.cos(u) - so * math.sin(u) * ci),
            r * (so * math.cos(u) + co * math.sin(u) * ci),
            r * math.sin(u) * si)


def orbit_segments(el: Dict[str, float], view: View, r_max_au: float,
                   tol_px: float = 0.3, max_points: int = 40000) -> List[Segment]:
    """軌道要素 → 黄道座標の点列（連続セグメントのリスト）。

    - **適応二分細分**: 弦の中点と真の曲線のずれ（サジッタ）が `tol_px` を超える区間を分割する。
      一律 720 点だと C/2014 UN271（e=0.9994）を 35,730 AU 枠で描いたとき最大 27 px の折れが出る。
    - **双曲線・長周期対応**: `ν` は `±nu_limit_deg(e)` に切り、`r > r_max_au` の点でセグメントを切る。
    - 楕円は閉じた 1 セグメント（始点と終点が一致）を返す。
    """
    e = float(el.get("e", 0.0))
    a = el.get("a")
    a = None if a is None else float(a)
    q = el.get("q")
    q = None if q is None else float(q)
    i_deg = float(el.get("i", 0.0))
    om_deg = float(el.get("om", el.get("node", 0.0)))
    w_deg = float(el.get("w", el.get("argp", 0.0)))

    limit = nu_limit_deg(e)
    closed = conic_kind(e) == "ellipse"
    lo, hi = (0.0, 360.0) if closed else (-limit, limit)

    def radius(nu_deg: float) -> float:
        return conic_radius(e, nu_deg, a=a, q=q)

    def point(nu_deg: float) -> Optional[Point3]:
        r = radius(nu_deg)
        if not math.isfinite(r) or r <= 0.0:
            return None
        return _xyz(r, nu_deg, i_deg, om_deg, w_deg)

    def sagitta_px(a0: float, a1: float) -> float:
        """弦の中点と真の曲線のずれを**投影後の px** で測る（対数動径でも正しく効く）。"""
        p0, p1 = point(a0), point(a1)
        if p0 is None or p1 is None:
            return 0.0
        pm = point((a0 + a1) / 2.0)
        if pm is None:
            return 0.0
        mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0, (p0[2] + p1[2]) / 2.0)
        mx, my, _ = view.project(mid)
        px_, py_, _ = view.project(pm)
        return math.hypot(mx - px_, my - py_)

    n0 = 240
    dense: List[Tuple[float, Point3]] = []

    def refine(a0: float, a1: float, depth: int) -> None:
        p0 = point(a0)
        if p0 is None:
            return
        if (depth >= 12 or len(dense) >= max_points
                or sagitta_px(a0, a1) <= float(tol_px)):
            dense.append((a0, p0))
            return
        mid = (a0 + a1) / 2.0
        refine(a0, mid, depth + 1)
        refine(mid, a1, depth + 1)

    for k in range(n0):
        refine(lo + (hi - lo) * k / n0, lo + (hi - lo) * (k + 1) / n0, 0)
    p_end = point(hi)
    if p_end is not None and dense and dense[-1][0] != hi:
        dense.append((hi, p_end))

    # r で切って連続セグメントに分ける
    segs: List[Segment] = []
    cur: Segment = []
    for _nu, p in dense:
        r = math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)
        if r > float(r_max_au):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
            continue
        cur.append(p)
    if len(cur) > 1:
        segs.append(cur)
    if closed and len(segs) > 1 and math.dist(segs[0][0], segs[-1][-1]) < r_max_au * 0.02:
        segs[0] = segs[-1] + segs[0]
        segs.pop()
    return segs


# --------------------------------------------------------------------------- 深度
def depth_range(segments: Iterable[Segment], view: View) -> Tuple[float, float]:
    """点列全体の depth の (min, max)。固定値を使うと広い枠で奥行きが消える。"""
    vals = [view.project(p)[2] for seg in segments for p in seg]
    if not vals:
        return -1.0, 1.0
    return min(vals), max(vals)


def depth_fraction(depth: float, dmin: float, dmax: float) -> float:
    span = dmax - dmin
    if span <= 1e-9:
        return 1.0
    return max(0.0, min(1.0, (depth - dmin) / span))


def depth_alpha(depth: float, dmin: float, dmax: float, kind: str = "tno") -> float:
    """奥ほど薄く。`kind="planet"` は全体に控えめにする。"""
    t = depth_fraction(depth, dmin, dmax)
    base = 0.58 if kind == "planet" else 0.82
    return (0.26 + 0.74 * t) * base


def depth_width(base_w: float, depth: float, dmin: float, dmax: float) -> float:
    """奥ほど細く。手前で 1.24 倍、奥で 0.62 倍。"""
    t = depth_fraction(depth, dmin, dmax)
    return base_w * (0.62 + 0.62 * t)


# --------------------------------------------------------------------------- 描画（Pillow）
def _rgba(color: Sequence[int], alpha: float) -> Tuple[int, int, int, int]:
    r, g, b = color[0], color[1], color[2]
    return (int(r), int(g), int(b), int(max(0, min(255, round(alpha * 255)))))


def draw_orbit(draw, segments: List[Segment], view: View, color: Sequence[int],
               base_width: float = 1.0, kind: str = "tno",
               chunk: int = 8, dmin: Optional[float] = None,
               dmax: Optional[float] = None) -> int:
    """軌道をアンチエイリアス無しの線分列で描く（Pillow）。描いたセグメント数を返す。

    深度で不透明度と線幅を連続変化させる。`draw` は RGBA モードの ImageDraw を想定。
    """
    if dmin is None or dmax is None:
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
            xy = [(p[0], p[1]) for p in part]
            draw.line(xy, fill=_rgba(color, a), width=w, joint="curve")
            drawn += 1
    return drawn


def draw_track(draw, points: Sequence[Point3], view: View, color: Sequence[int],
               base_width: float = 2.0, arrows: Sequence[float] = (0.25, 0.55, 0.85),
               arrow_px: float = 9.0, dashed: bool = True) -> int:
    """探査機・彗星の経路（Horizons の点列）を描き、進行方向の矢印を打つ。

    点列は黄道 J2000 の (x, y, z) [AU]。`dashed=True` で破線（既存図の破線経路と同じ扱い）。
    """
    if len(points) < 2:
        return 0
    pts = [view.project(p) for p in points]
    w = max(1, int(round(base_width)))
    for i in range(len(pts) - 1):
        if dashed and (i // 4) % 2:
            continue
        draw.line([(pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])],
                  fill=_rgba(color, 0.9), width=w)
    for frac in arrows:
        k = int(len(pts) * frac)
        if k < 1 or k >= len(pts):
            continue
        x0, y0 = pts[k - 1][0], pts[k - 1][1]
        x1, y1 = pts[k][0], pts[k][1]
        ang = math.atan2(y1 - y0, x1 - x0)
        for d_a in (150, -150):
            a2 = ang + math.radians(d_a)
            draw.line([(x0, y0), (x0 + arrow_px * math.cos(a2), y0 + arrow_px * math.sin(a2))],
                      fill=_rgba(color, 0.9), width=w)
    return len(pts)


def draw_marker(draw, pos: Point3, view: View, color: Sequence[int],
                ring_px: float = 9.5, cross_px: float = 13.0) -> Tuple[int, int]:
    """現在位置マーカー（白点＋色リング＋破線ハロー＋十字）。戻り値は (px, py)。

    背景の恒星と区別するため、白点だけにしない（実測: 点だけだと星と見分けられない）。
    """
    px, py = expected_px(view, pos)          # verify と同じ整数 px を使う
    draw.ellipse([px - 7.5, py - 7.5, px + 7.5, py + 7.5], fill=_rgba(color, 0.22))
    draw.ellipse([px - 3.2, py - 3.2, px + 3.2, py + 3.2], fill=(255, 255, 255, 235))
    draw.ellipse([px - ring_px, py - ring_px, px + ring_px, py + ring_px],
                 outline=_rgba(color, 0.55), width=1)
    for dx, dy in ((-cross_px, 0.0), (cross_px, 0.0), (0.0, -cross_px), (0.0, cross_px)):
        draw.line([(px + dx * 0.45, py + dy * 0.45), (px + dx, py + dy)],
                  fill=_rgba(color, 0.7), width=1)
    return px, py


def expected_px(view: View, pos: Point3) -> Tuple[int, int]:
    """`verify` 用: 黄道座標から期待される画素位置（四捨五入）。"""
    px, py, _ = view.project(pos)
    return int(round(px)), int(round(py))

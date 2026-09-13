"""画像合成の共通ヘルパー（ツール定義は持たない）。

各マップツール（planetary_map / planetary_rover / satellite_map / sky_overlay /
solar_system / solar_eclipse）が個別に持っていた以下を1箇所に集約する:

- 日本語フォント探索（Windows のメイリオ等 → 無ければ PIL 既定フォント）
- JPEG エンコード（上限バイト数を超えたら縮小して再エンコード）
- 経度±180°（アンチメリジアン）をまたぐ軌道線の分割

PIL は重いので各関数内で遅延 import する（既存の各ツールと同じ方針）。
"""
from __future__ import annotations

import io
import math
import os
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

from .cache import CACHE_ROOT

# フォント探索順（先頭ほど優先）。bold はメイリオ Bold を最優先にする。
_FONT_CANDIDATES = {
    False: ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/yugothb.ttc",
            "C:/Windows/Fonts/msgothic.ttc"),
    True: ("C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/yugothb.ttc",
           "C:/Windows/Fonts/msgothic.ttc"),
}


# ---------- 天体・記号の表示色（描画系ツール共通の単一の出典） ----------
# 「実物の見た目に寄せた色」を 1 か所で決める。ここを直せば sky_overlay /
# solar_system の Pillow 版・matplotlib 版・凡例・figure 注記にすべて反映される
# （モジュールごとに色を手書きすると、同じ天王星が図ごとに違う色になる）。
BODY_COLORS = {
    "太陽": (255, 220, 120),
    "月": (224, 224, 226),
    "水星": (168, 168, 170),
    "金星": (242, 226, 180),
    "地球": (110, 150, 235),
    "火星": (214, 96, 77),
    "木星": (212, 168, 118),
    "土星": (226, 196, 146),
    "天王星": (176, 224, 230),   # 淡い青緑（v0.27 で sky_overlay 側の値に統一）
    "海王星": (96, 140, 232),
    "冥王星": (176, 140, 120),
}

# 天体そのものではないが複数ツールで重複していた記号色
SYMBOL_COLORS = {
    "ring": (226, 206, 160),          # 土星の環
    "band": (196, 148, 108),          # 木星の縞
    "cap": (240, 240, 240),           # 火星の極冠
    "asteroid": (96, 200, 120),       # 小惑星マーカー（緑の十字）
    "asteroid_label": (182, 255, 207),
    "comet": (150, 235, 255),         # 彗星（シアンの核）
    "comet_orbit": (255, 150, 60),    # 彗星の軌道面ビューの軌道線
}


def body_rgb(name: str, default: Tuple[int, int, int] = (200, 200, 210)) -> Tuple[int, int, int]:
    """天体名（日本語）→ 表示色RGB。未登録は default。"""
    return BODY_COLORS.get(str(name), default)


def symbol_rgb(key: str, default: Tuple[int, int, int] = (200, 200, 210)) -> Tuple[int, int, int]:
    """記号色（ring/band/cap/asteroid/comet 等）→ RGB。"""
    return SYMBOL_COLORS.get(str(key), default)


def rgb_hex(rgb) -> str:
    """(r, g, b) → #rrggbb（matplotlib 用）。"""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


@lru_cache(maxsize=128)
def load_font(sz: int, bold: bool = False):
    """日本語 TrueType フォントを返す（見つからなければ PIL 既定フォント）。

    各ツールが個別実装していたフォント探索の共通版。サイズ・太さ単位で
    lru_cache するため、同一描画内での再ロードが発生しない。
    """
    from PIL import ImageFont
    for p in _FONT_CANDIDATES[bool(bold)]:
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def encode_jpeg(img, max_bytes: int = 3_500_000) -> bytes:
    """PIL 画像を JPEG バイト列にする。max_bytes 超過時は縮小して再エンコードする。"""
    from PIL import Image
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    if len(data) > max_bytes:
        w, h = img.size
        small = img.convert("RGB").resize(
            (max(1, int(w * 0.7)), max(1, int(h * 0.7))), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
    return data


def split_at_antimeridian(points: Iterable[Tuple[float, float]],
                          threshold: float = 150.0) -> List[list]:
    """経度±180°をまたぐ線を分割してセグメントのリストを返す（点列は (lon, lat)）。

    等角図法の地図では ±180° の継ぎ目で線が画面を横切ってしまうため、
    直前点との経度差が threshold を超えた位置で分割する。
    """
    segs: List[list] = []
    seg: list = []
    for p in points:
        if seg and abs(p[0] - seg[-1][0]) > threshold:
            segs.append(seg)
            seg = [p]
        else:
            seg.append(p)
    if seg:
        segs.append(seg)
    return segs


# ---------------------------------------------------------------------------
# 図の注記（figure manifest）
#
# 描画系ツールは「画像バイト列」だけを返すと、ホスト LLM はピクセルから描画規約を
# 推測するしかなく、誤読する（例: 高離心率軌道を「主天体の周りを回る円」と説明する、
# 対数縮尺の円を実軌道と説明する）。そこで各ツールは structuredContent に
# figure ブロック（何をどう描いたかの自己申告）を返し、content には同じ注記を
# そのまま引用できる形で入れる。
#
# 注記は手書きせず、必ず数値から生成する（図と文が食い違わないようにするため）。
# ---------------------------------------------------------------------------

FIGURE_SCHEMA = "figure/1"


def _fmt_num(v: Optional[float], digits: int = 2) -> str:
    """注記用の数値表記（大きな値は3桁区切り、小さい値は有効数字を確保）。"""
    if v is None:
        return "-"
    av = abs(v)
    if av >= 1000:
        return "{:,.0f}".format(v)
    if av >= 1:
        return "{:,.{}f}".format(v, digits)
    return "{:.4f}".format(v).rstrip("0").rstrip(".")


@dataclass(frozen=True)
class Conic:
    """軌道の円錐曲線（楕円／放物線／双曲線）。焦点は主天体。

    e>=1（C/彗星など）や a<0（SBDB の双曲線表現）も扱う。
    """

    kind: str                          # "ellipse" / "parabola" / "hyperbola"
    e: float
    a: Optional[float] = None          # 半長軸（双曲線では負値になり得る）
    q: Optional[float] = None          # 近点距離（焦点から）
    b: Optional[float] = None          # 半短軸（楕円のみ）
    c: Optional[float] = None          # 焦点と楕円中心のズレ = a·e（楕円のみ）
    apo: Optional[float] = None        # 遠点距離（楕円のみ）
    period: Optional[float] = None
    incl_deg: Optional[float] = None
    closed: bool = True

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "e": round(self.e, 7), "closed": self.closed}
        for k in ("a", "q", "b", "c", "apo", "period", "incl_deg"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v if abs(v) < 1000 else round(v, 1)
        return d


def conic_from_elements(a: Optional[float] = None, e: Optional[float] = None, *,
                        q: Optional[float] = None, period: Optional[float] = None,
                        incl_deg: Optional[float] = None) -> Conic:
    """軌道要素から Conic を作る。e と a の符号から楕円／放物線／双曲線を判定する。"""
    e = float(e)
    a = None if a is None else float(a)
    if q is None and a is not None and e < 1.0:
        q = a * (1.0 - e)
    if e < 1.0 - 1e-9:
        aa = None if a is None else abs(a)
        return Conic("ellipse", e, a, q,
                     None if aa is None else aa * math.sqrt(max(0.0, 1.0 - e * e)),
                     None if aa is None else aa * e,
                     None if aa is None else aa * (1.0 + e),
                     period, incl_deg, True)
    if abs(e - 1.0) <= 1e-9:
        return Conic("parabola", e, a, q, None, None, None, period, incl_deg, False)
    return Conic("hyperbola", e, a, q, None,
                 None if a is None else abs(a) * e, None, period, incl_deg, False)


def figure_notes(conic: Optional[Conic] = None, *, primary: str = "", unit: str = "km",
                 periapsis_label: str = "近点", apoapsis_label: str = "遠点",
                 extra: Iterable[str] = ()) -> List[str]:
    """図の注記を数値から生成する（手書きしない＝図と文がドリフトしない）。"""
    notes: List[str] = []
    if conic is not None:
        if conic.kind == "ellipse":
            notes.append(
                "主天体（{}）は楕円の焦点に置いている。楕円の中心は {} から a·e＝{} {} "
                "ずれている（{} {} {} ／ {} {} {}）".format(
                    primary, primary, _fmt_num(conic.c), unit,
                    periapsis_label, _fmt_num(conic.q), unit,
                    apoapsis_label, _fmt_num(conic.apo), unit))
            notes.append(
                "離心率 e={:.6f}。軌道は閉じている（{}は存在する）".format(conic.e, apoapsis_label))
        elif conic.kind == "hyperbola":
            notes.append(
                "この軌道は閉じていない（e={:.7f} > 1、半長軸 a が負＝双曲線）。"
                "{}は存在しないので、楕円として a(1+e) を計算すると負値になる".format(
                    conic.e, apoapsis_label))
            notes.append(
                "図は{}から有限距離までを描いた双曲線の枝。実際の軌道はさらに遠方へ開いていく".format(
                    periapsis_label))
        else:
            notes.append(
                "放物線に近い軌道（e={:.7f}）。閉じた楕円として描いてはいけない".format(conic.e))
        if conic.incl_deg is not None:
            retro = conic.incl_deg > 90.0
            notes.append(
                "黄道面基準の軌道傾斜角 {:.2f}°{}。この図は彗星自身の軌道面を真横から見た模式図で、"
                "傾斜は別の見方になる".format(conic.incl_deg, "（逆行軌道）" if retro else ""))
    notes.extend(n for n in extra if n)
    return notes


def view_spec(frame: str, projection: str, description: str,
              why: Optional[str] = None) -> dict:
    """図の視点（どの面を・どう見たか）。"""
    v = {"frame": frame, "projection": projection, "description": description}
    if why:
        v["why"] = why
    return v


def primary_spec(name: str, at: str = "focus", *, center_offset: Optional[float] = None,
                 unit: str = "km", note: Optional[str] = None) -> dict:
    """主天体の置き方（焦点か中心か）。"""
    p = {"name": name, "at": at}
    if center_offset is not None:
        p["center_offset_" + unit] = center_offset
    if note:
        p["note"] = note
    return p


def scale_spec(type_: str, *, to_scale: bool = True,
               px_per_unit: Optional[float] = None, unit: str = "km",
               exaggerated: Iterable[str] = ()) -> dict:
    """縮尺の種類と、実寸でない要素の明示。"""
    s = {"type": type_, "to_scale": bool(to_scale)}
    if px_per_unit:
        s["px_per_" + unit] = px_per_unit
    ex = list(exaggerated)
    if ex:
        s["exaggerated"] = ex
    return s


def figure_payload(*, kind: str, title: str, view: Optional[dict] = None,
                   primary: Optional[dict] = None, scale: Optional[dict] = None,
                   conic: Optional[Conic] = None, markers: Optional[Iterable[dict]] = None,
                   notes: Iterable[str] = (), caption: Optional[str] = None,
                   verify: Optional[dict] = None) -> dict:
    """structuredContent に入れる figure ブロックを組み立てる。"""
    fig: dict = {"schema": FIGURE_SCHEMA, "kind": kind, "title": title}
    if view:
        fig["view"] = view
    if primary:
        fig["primary"] = primary
    if scale:
        fig["scale"] = scale
    if conic is not None:
        fig["conic"] = conic.to_dict()
    mk = list(markers or ())
    if mk:
        fig["markers"] = mk
    fig["notes"] = [n for n in notes if n]
    if caption:
        fig["caption"] = caption
    if verify:
        fig["verify"] = verify
    return fig


def figure_text_block(figure: dict) -> str:
    """content 用の注記ブロック（LLM が要約せずそのまま引用できる形）。"""
    lines = ["### ⚠️ 図の注記（要約・言い換えせず、そのまま引用してください）"]
    lines += ["- " + t for t in figure.get("notes", [])]
    if figure.get("caption"):
        lines.append("**図の説明**: " + figure["caption"])
    return "\n".join(lines)


def verify_curve(image, *, color: Tuple[int, int, int], focus_xy: Tuple[float, float],
                 px_per_unit: float, periapsis: float, apoapsis: Optional[float] = None,
                 tol_ratio: float = 0.05, tol_px: float = 4.0,
                 label_boxes: Iterable[Tuple] = (),
                 tol_color: int = 60, axis: str = "x") -> dict:
    """描いた曲線の画素から近点／遠点距離を逆算し、幾何が数値どおりか検証する。

    高離心率の楕円は「主天体を中心に描いてしまう」誤りが起きやすいため、
    主天体＝焦点からの距離を画素から測って a(1-e) / a(1+e) と突き合わせる。
    併せて、ラベル矩形に曲線色が混入していないこと（文字と線の重なり）も検査する。
    """
    from PIL import Image
    img = image if hasattr(image, "size") else Image.open(image)
    px = img.convert("RGB").load()
    w, h = img.size
    hits: List[Tuple[int, int]] = []
    step = 1 if max(w, h) <= 1200 else 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if abs(r - color[0]) + abs(g - color[1]) + abs(b - color[2]) < tol_color:
                hits.append((x, y))
    if not hits:
        return {"ok": False, "reason": "曲線色の画素が見つかりません"}
    fx, fy = focus_xy
    if axis == "x":
        near = max(hits, key=lambda p: p[0])
        far = min(hits, key=lambda p: p[0])
    else:
        near = max(hits, key=lambda p: p[1])
        far = min(hits, key=lambda p: p[1])
    d_near = math.hypot(near[0] - fx, near[1] - fy) / px_per_unit
    d_far = math.hypot(far[0] - fx, far[1] - fy) / px_per_unit
    # 画素の分解能ぶんの許容（近点は画面上で短いため、比だけで判定すると誤検知する）
    def _near(a_, b_):
        return abs(a_ - b_) <= max(tol_ratio * abs(b_), tol_px / max(px_per_unit, 1e-12))

    def _rd(v):
        """量に応じた丸め（km は整数、AU は小数を残す）。"""
        return int(round(v)) if abs(v) >= 100 else round(v, 4)

    res = {"periapsis_measured": _rd(d_near), "periapsis_expected": _rd(periapsis),
           "periapsis_error_pct": round(abs(d_near - periapsis) / max(1e-9, periapsis) * 100, 2),
           "resolution_per_px": round(1.0 / max(px_per_unit, 1e-12), 6)}
    ok = _near(d_near, periapsis)
    if apoapsis:
        res["apoapsis_measured"] = _rd(d_far)
        res["apoapsis_expected"] = _rd(apoapsis)
        res["apoapsis_error_pct"] = round(abs(d_far - apoapsis) / max(1e-9, apoapsis) * 100, 2)
        ok = ok and _near(d_far, apoapsis)
    overlap = 0
    for (x0, y0, x1, y1) in label_boxes:
        for y in range(int(y0), int(min(y1, h))):
            for x in range(int(x0), int(min(x1, w))):
                r, g, b = px[x, y]
                if abs(r - color[0]) + abs(g - color[1]) + abs(b - color[2]) < tol_color:
                    overlap += 1
    res["label_overlap_px"] = overlap
    res["ok"] = bool(ok and overlap == 0)
    return res


def as_image(image):
    """PIL画像 / パス / bytes のいずれでも PIL 画像にする。"""
    from PIL import Image
    if hasattr(image, "size"):
        return image
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image)))
    return Image.open(image)


def pixel_near(image, xy: Tuple[float, float], color: Tuple[int, int, int],
               tol: int = 90, r: int = 3) -> int:
    """指定画素の近傍(±r)に指定色が何画素あるかを返す（マーカー描画の確認用）。"""
    import numpy as np
    img = as_image(image).convert("RGB")
    a = np.asarray(img, dtype=np.int16)
    x, y = int(xy[0]), int(xy[1])
    x0, x1 = max(0, x - r), min(img.size[0], x + r + 1)
    y0, y1 = max(0, y - r), min(img.size[1], y + r + 1)
    if x0 >= x1 or y0 >= y1:
        return 0
    d = (abs(a[y0:y1, x0:x1, 0] - color[0]) + abs(a[y0:y1, x0:x1, 1] - color[1])
         + abs(a[y0:y1, x0:x1, 2] - color[2]))
    return int((d < tol).sum())


# ---------- 生成物の保存と「メディアより前のリンク」 ----------
# Hermes 等のリッチなクライアントは ImageContent をそのまま描画できるが、CLI 系・
# Android 系のハーネス（codex / opencode など）は画像ブロックを無視するため、
# 「画像が生成されたのに何も表示されない」ように見える。そこで content の
# 先頭側に、アイコン付きのクリック可能なリンク（URL、無ければ保存したファイル）を
# 必ず置く。順序は「メディア本体より前」を守る。
OUTPUT_DIR = os.path.join(os.path.dirname(CACHE_ROOT), "out")

_MEDIA_ICONS = {"image": "🖼️", "figure": "🖼️", "audio": "🎧", "video": "🎬", "file": "📄"}


def file_uri(path: str) -> str:
    """ローカルパスを file:// URI に変換する（Windows の C:\\... も可）。"""
    p = os.path.abspath(str(path)).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + urllib.parse.quote(p, safe="/:")


def save_output(data: bytes, tool: str, ext: str = "png", keep: int = 200) -> Optional[str]:
    """生成した画像をディスクに保存して絶対パスを返す（失敗時 None）。

    インライン表示できないハーネスでもユーザーが開けるようにするための出力。
    keep 件を超えた古いファイルは削除する（無制限に溜めない）。
    """
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "{}_{}_{}.{}".format(tool, stamp, uuid.uuid4().hex[:6], ext.lstrip("."))
        path = os.path.join(OUTPUT_DIR, name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)            # 途中読み込みを避けるため置換で確定
        try:
            files = sorted((os.path.join(OUTPUT_DIR, n) for n in os.listdir(OUTPUT_DIR)),
                           key=os.path.getmtime, reverse=True)
            for old in files[keep:]:
                os.remove(old)
        except OSError:
            pass
        return path
    except OSError:
        return None


def markdown_link_url(url: str) -> str:
    """URL を markdown リンク内で安全に使える形にする。

    生の空白・括弧はリンクの終端と紛らわしく、リンクが壊れて切れる（NASA の
    アセットURLには空白入り動画名がある）。既存の %XX は壊さないよう、
    問題になる文字だけをパーセントエンコードする。
    """
    out = []
    for ch in str(url or ""):
        if ch in " <>\"`()":
            out.append("%{:02X}".format(ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def media_link_line(label: str, *, url: Optional[str] = None, path: Optional[str] = None,
                    kind: str = "image", note: Optional[str] = None) -> str:
    """メディア本体より前に置く「アイコン付きリンク行」を作る。

    url があればそれを、無ければ保存した path を file:// URI にしてリンクにする。
    リンク先を作れないときは空文字を返す（呼び出し側で行ごと落とせる）。
    """
    icon = _MEDIA_ICONS.get(kind, "🔗")
    target = markdown_link_url(url) if url else (file_uri(path) if path else "")
    if not target:
        return ""
    line = "{} [{}]({})".format(icon, label, target)
    if path:
        line += " ｜ 保存先: `{}`".format(path)
    if note:
        line += " ｜ {}".format(note)
    return line

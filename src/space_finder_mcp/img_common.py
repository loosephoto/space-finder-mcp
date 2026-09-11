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
from functools import lru_cache
from typing import Iterable, List, Tuple

# フォント探索順（先頭ほど優先）。bold はメイリオ Bold を最優先にする。
_FONT_CANDIDATES = {
    False: ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/yugothb.ttc",
            "C:/Windows/Fonts/msgothic.ttc"),
    True: ("C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/yugothb.ttc",
           "C:/Windows/Fonts/msgothic.ttc"),
}


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

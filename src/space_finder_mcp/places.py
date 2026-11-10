"""地名 → 緯度経度の解決（複数モジュールが使う唯一の実装）。

なぜ集約するか
--------------
`sky_overlay` と `solar_eclipse` は同じ `_KNOWN_COORDS`（8都市）と `_resolve_place`
（23行）を**一字違いなく二重に持っていた**。`skyfield_pos` はさらに別の表（福岡入り
9都市）を持ち、同じ「福岡」が `constellation_now` では解決でき、`sky_map_with_satellites`
ではオンライン解決に落ちる（＝同じ入力がツールで違う結果になる）。表と解決規則を
ここ 1 か所に置き、各ツールは `resolve_place()` を呼ぶだけにする。

解決の順序（従来と同じ）
------------------------
1. `lat`/`lon` が両方指定されていれば、それを検証して使う（`as_float` で範囲外は None）。
2. `place` が既知テーブルにあればその座標（大文字小文字・前後空白を無視）。
3. それ以外の地名は `weather_astro._geocode`（Open-Meteo → Nominatim、キャッシュ付き）へ。
4. 解決できなければ `None`（呼び出し側が既定値かエラーかを決める）。
"""
from __future__ import annotations

from typing import Optional

from .input_utils import as_float
from .name_common import ResolverUnavailable

#: 都市名 → (緯度, 経度)。キーは小文字（日本語はそのまま）。
CITY_COORDS = {
    "東京": (35.68, 139.69), "tokyo": (35.68, 139.69),
    "大阪": (34.69, 135.50), "osaka": (34.69, 135.50),
    "札幌": (43.06, 141.35), "sapporo": (43.06, 141.35),
    "福岡": (33.59, 130.40), "fukuoka": (33.59, 130.40),
    "那覇": (26.21, 127.68), "naha": (26.21, 127.68),
    "ニューヨーク": (40.71, -74.01), "new york": (40.71, -74.01),
    "ロンドン": (51.51, -0.13), "london": (51.51, -0.13),
    "シドニー": (-33.87, 151.21), "sydney": (-33.87, 151.21),
    "ハワイ": (19.82, -155.47), "hawaii": (19.82, -155.47),
}


def resolve_place(place, lat, lon) -> Optional[tuple]:
    """`place` 文字列 or `lat`/`lon` 数値から `(lat, lon)` を返す（解決不能なら None）。

    `lat`/`lon` が数値として解釈できない場合は None（呼び出し側でエラーにする）。
    """
    if lat is not None and lon is not None:
        la = as_float(lat, None, -90.0, 90.0)
        lo = as_float(lon, None, -180.0, 180.0)
        if la is None or lo is None:
            return None
        return la, lo
    if place:
        key = str(place).strip().lower()
        if key in CITY_COORDS:
            return CITY_COORDS[key]
        # 既知リストにない任意の地名 → オンラインで緯度経度を解決（キャッシュ込み）
        try:
            from .weather_astro import _geocode
            g = _geocode(place)
            if g:
                return float(g["latitude"]), float(g["longitude"])
        except ResolverUnavailable:
            # 「地名が見つからない（None）」と「地名解決サービスに繋がらない」を混ぜない
            # （混ぜると呼び出し側が東京へ黙って落ちる）。
            raise
        except Exception:
            pass
    return None

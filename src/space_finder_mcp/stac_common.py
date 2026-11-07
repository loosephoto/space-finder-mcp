"""STAC 系ツール共通の入力検証ヘルパー（ツール定義は持たない）。

bbox と雲量上限(%)のパースを1箇所に集約する。従来は各ツールが float() を
無防備に呼んでいたため、数値でない入力で ValueError が MCP 呼び出しごと
外へ漏れ、また要素数や範囲の検証が無いままAPIへ送って 500 を誘発していた。

戻り値は (値, エラーメッセージ) のタプル。エラー時は値が None になる。
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re
from typing import Optional


def parse_bbox(bbox) -> tuple[Optional[list], Optional[str]]:
    """'lon_min,lat_min,lon_max,lat_max' を [float, float, float, float] に変換する。

    戻り: (bbox, エラーメッセージ)。エラー時は (None, 理由)。
    """
    if bbox is None or str(bbox).strip() == "":
        return None, None
    try:
        vals = [float(x) for x in str(bbox).replace(",", " ").split()]
    except (TypeError, ValueError):
        return None, "bbox は 'lon_min,lat_min,lon_max,lat_max'（数値4つ）で指定してください。"
    if len(vals) != 4:
        return None, ("bbox は数値4つ（lon_min,lat_min,lon_max,lat_max）で指定してください"
                      "（指定された数: " + str(len(vals)) + "）。")
    lon_min, lat_min, lon_max, lat_max = vals
    if not (-180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0):
        return None, "bbox の経度は -180〜180 の範囲で指定してください。"
    if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
        return None, "bbox の緯度は -90〜90 の範囲で指定してください。"
    if lon_min > lon_max or lat_min > lat_max:
        return None, "bbox は min <= max の順（lon_min,lat_min,lon_max,lat_max）で指定してください。"
    return vals, None


def parse_cloud_cover(value) -> tuple[Optional[float], Optional[str]]:
    """雲量上限(%)を float に変換する。戻り: (値, エラーメッセージ)。"""
    if value is None:
        return None, None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, "max_cloud_cover は数値(%)で指定してください。"
    if not (0.0 <= v <= 100.0):
        return None, "max_cloud_cover は 0〜100(%)の範囲で指定してください。"
    return v, None


def expand_datetime(value) -> str:
    """日付のみの指定（YYYY-MM-DD / YYYY-MM / YYYY、"/" 区切りの start/end）を
    RFC3339 の full timestamp へ展開する。

    CDSE（Copernicus）と AWS Earth Search は date-only を 400 で拒否する（実測:
    "invalid datetime separator, expected `T`" / "does not match RFC3339 format"）。
    INPE / DLR / UK は date-only も受理するが full timestamp も受理するため、
    どの STAC 系ツールで通してもよい。既に "T" を含む値は書き換えない。
    """
    v = str(value or "").strip()
    if not v:
        return v
    parts = [p.strip() for p in v.split("/")]

    def _bounds(part):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", part)
        if m:
            try:
                _dt.date(*(int(x) for x in m.groups()))
            except ValueError:
                return None
            return part + "T00:00:00Z", part + "T23:59:59Z"
        m = re.match(r"^(\d{4})-(\d{2})$", part)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            try:
                last = calendar.monthrange(y, mo)[1]
            except ValueError:
                return None
            return f"{part}-01T00:00:00Z", f"{part}-{last:02d}T23:59:59Z"
        m = re.match(r"^(\d{4})$", part)
        if m:
            return part + "-01-01T00:00:00Z", part + "-12-31T23:59:59Z"
        return None

    if len(parts) == 1:
        b = _bounds(parts[0])
        if b:
            return f"{b[0]}/{b[1]}"
        return v
    b1, b2 = _bounds(parts[0]), _bounds(parts[1])
    a = b1[0] if b1 else parts[0]
    b = b2[1] if b2 else parts[1]
    return f"{a}/{b}"

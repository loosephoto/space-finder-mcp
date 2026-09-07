"""NASA の公開データ（APOD・小惑星 NEO 等）。api.nasa.gov の API キーを使用。"""
from __future__ import annotations

from typing import Optional

import requests

NASA = "https://api.nasa.gov"

def _get(path: str, key: str, params: Optional[dict] = None, timeout: int = 25) -> dict:
    p = dict(params or {})
    p["api_key"] = key
    r = requests.get(f"{NASA}/{path}", params=p, timeout=timeout)
    r.raise_for_status()
    return r.json()


def apod(key: str, date: Optional[str] = None) -> str:
    """今日（または指定日）の Astronomy Picture of the Day（今日の天文写真）を返す。

    Args:
        key: NASA Open API キー（DEMO_KEY または無料開発者キー）。
        date: YYYY-MM-DD。省略時は今日。
    """
    params = {}
    if date:
        params["date"] = date
    d = _get("planetary/apod", key, params)
    return (f"APOD {d.get('date','')} - {d.get('title','')}\n"
            f"{d.get('explanation','')}\n"
            f"画像: {d.get('hdurl') or d.get('url')} (Copyright: {d.get('copyright','不明')})")


def neo_today(key: str) -> str:
    """今日地球に接近する小惑星（Near Earth Object）の一覧を返す。

    Args:
        key: NASA Open API キー。
    """
    import datetime
    today = datetime.date.today().isoformat()
    d = _get("neo/rest/v1/feed", key, {"start_date": today, "end_date": today})
    lines = [f"今日（{today}）地球に接近する小惑星:"]
    cnt = 0
    for day, objs in d.get("near_earth_objects", {}).items():
        for o in objs:
            cnt += 1
            close = o.get("close_approach_data", [{}])[0]
            dia = o.get("estimated_diameter", {}).get("meters", {}).get("estimated_diameter_max", "?")
            lines.append(f"- {o['name']}（直径約{dia:.0f}m, 接近距離{float(close.get('miss_distance',{}).get('kilometers',0)):.0f}km, 速度{float(close.get('relative_velocity',{}).get('kilometers_per_hour',0)):.0f}km/h）")
    if cnt == 0:
        return f"今日（{today}）地球接近する小惑星はありません。"
    lines.insert(1, f"合計 {cnt} 個:")
    return "\n".join(lines)

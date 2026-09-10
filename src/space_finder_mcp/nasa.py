"""NASA の公開データ（APOD・小惑星 NEO 等）。api.nasa.gov の API キーを使用。"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

NASA = "https://api.nasa.gov"

def _get(path: str, key: str, params: Optional[dict] = None, timeout: int = 25) -> dict:
    p = dict(params or {})
    p["api_key"] = key
    r = requests.get(f"{NASA}/{path}", params=p, timeout=timeout)
    r.raise_for_status()
    return r.json()


def apod(key: str, date: Optional[str] = None) -> CallToolResult:
    """今日（または指定日）の Astronomy Picture of the Day（今日の天文写真）を返す。

    認証不要（DEMO_KEY）または無料開発者キー。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        key: NASA Open API キー（DEMO_KEY または無料開発者キー）。
        date: YYYY-MM-DD。省略時は今日。
    """
    params = {}
    if date:
        params["date"] = date
    try:
        d = _get("planetary/apod", key, params)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"NASA APOD の取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "api.nasa.gov"},
        )
    text = (f"APOD {d.get('date','')} - {d.get('title','')}\n"
            f"{d.get('explanation','')}\n"
            f"画像: {d.get('hdurl') or d.get('url')} (Copyright: {d.get('copyright','不明')})")
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={
            "date": d.get("date", ""), "title": d.get("title", ""),
            "explanation": d.get("explanation", ""),
            "media_type": d.get("media_type", "image"),
            "image_url": d.get("hdurl") or d.get("url"),
            "copyright": d.get("copyright", "不明"),
        },
    )


def neo_today(key: str) -> CallToolResult:
    """今日地球に接近する小惑星（Near Earth Object）の一覧を返す。

    認証不要（DEMO_KEY）または無料開発者キー。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        key: NASA Open API キー。
    """
    import datetime
    today = datetime.date.today().isoformat()
    try:
        d = _get("neo/rest/v1/feed", key, {"start_date": today, "end_date": today})
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"NASA NEO の取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "api.nasa.gov"},
        )
    lines = [f"今日（{today}）地球に接近する小惑星:"]
    records = []
    for day, objs in d.get("near_earth_objects", {}).items():
        for o in objs:
            close = (o.get("close_approach_data") or [{}])[0]
            dia_raw = (o.get("estimated_diameter", {}).get("meters", {}) or {}).get("estimated_diameter_max")
            dist_raw = ((close.get("miss_distance") or {}).get("kilometers"))
            vel_raw = ((close.get("relative_velocity") or {}).get("kilometers_per_hour"))
            try:
                dia = round(float(dia_raw), 1) if dia_raw is not None else None
            except (TypeError, ValueError):
                dia = None
            try:
                dist = round(float(dist_raw)) if dist_raw is not None else None
            except (TypeError, ValueError):
                dist = None
            try:
                vel = round(float(vel_raw)) if vel_raw is not None else None
            except (TypeError, ValueError):
                vel = None
            name = o.get("name", "?")
            records.append({
                "name": name,
                "nasa_id": o.get("neo_reference_id"),
                "hazardous": o.get("is_potentially_hazardous_asteroid"),
                "estimated_diameter_max_m": dia,
                "miss_distance_km": dist,
                "relative_velocity_kmh": vel,
            })
            def _fmt(v):
                return "?" if v is None else f"{v:g}"
            lines.append(f"- {name}（直径約{_fmt(dia)}m, 接近距離{_fmt(dist)}km, 速度{_fmt(vel)}km/h）")
    if not records:
        return CallToolResult(
            content=[TextContent(type="text", text=f"今日（{today}）地球接近する小惑星はありません。")],
            structuredContent={"date": today, "total": 0, "results": []},
        )
    lines.insert(1, f"合計 {len(records)} 個:")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"date": today, "shown": len(records), "results": records},
    )

"""NASA の公開データ（APOD・小惑星 NEO 等）。api.nasa.gov の API キーを使用。"""
from __future__ import annotations

import datetime
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from . import nasa_budget
from .cache import TTL_HOURLY, ttl_cache, is_error_result

NASA = "https://api.nasa.gov"


def _rate_limit_text(e: "requests.RequestException") -> str:
    """429(レート制限)なら対処方法を含む案内文を返す。それ以外は空文字。"""
    if isinstance(e, nasa_budget.BudgetExceeded):
        # 自前の予算管理で「投げずに」止めた場合（429 を踏みに行かない）
        return str(e)
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 429:
        return ("NASA API のレート制限に達しました。DEMO_KEY は 30リクエスト/時/IP の共有枠で、"
                "apod / neo_today / space_weather(DONKI) が同じ枠を取り合います。"
                "しばらく待つか、環境変数 NASA_API_KEY に無料の開発者キーを設定してください。")
    return ""

def _get(path: str, key: str, params: Optional[dict] = None, timeout: int = 25) -> dict:
    # 予算を使い切っていたら HTTP を出さずに止める（無駄な 429 を発生させない）
    ok, wait, used = nasa_budget.check(key)
    if not ok:
        raise nasa_budget.BudgetExceeded(nasa_budget.blocked_message(key), wait)
    p = dict(params or {})
    p["api_key"] = key
    nasa_budget.record(key)
    try:
        r = requests.get(f"{NASA}/{path}", params=p, timeout=timeout)
        r.raise_for_status()
        # 残量0の合図（X-RateLimit-Remaining: 0）があれば、次を 429 にしないよう先に待機する
        nasa_budget.note_response_headers(key, r.headers)
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        if getattr(resp, "status_code", None) == 429:
            # 実際に 429 を受けたら Retry-After を尊重して以降は投げない
            nasa_budget.note_429(key, (getattr(resp, "headers", None) or {}).get("Retry-After"))
        raise
    try:
        return r.json()
    except ValueError as e:
        # エラー時に HTML を返すことがある。呼び出し側は requests.RequestException を
        # 捕まえているので、JSON 解析失敗も同型の例外に正規化して漏らさない。
        raise requests.RequestException(
            f"応答が JSON ではありません (HTTP {r.status_code}): {str(e)[:80]}") from e


@ttl_cache(TTL_HOURLY, maxsize=64, skip_if=is_error_result)
def apod(key: str, date: Optional[str] = None) -> CallToolResult:
    """今日（または指定日）の Astronomy Picture of the Day（今日の天文写真）を返す。

    認証不要（DEMO_KEY）または無料開発者キー。content に表示用サマリ、structuredContent に JSON を返す。

    date 省略時は「今日」を明示指定して取得する。NASA の APOD API は date を省略すると
    500 を返すことがあり（実測）、また当日分は公開前だと 404 になるため、その場合は
    直近の公開分（前日）へ自動フォールバックする。date を明示した場合はその日のみを取得する。

    Args:
        key: NASA Open API キー（DEMO_KEY または無料開発者キー）。
        date: YYYY-MM-DD。省略時は今日（未公開なら前日）。
    """
    today = datetime.date.today()
    candidates = [str(date)] if date else [
        today.isoformat(), (today - datetime.timedelta(days=1)).isoformat()]
    d = None
    used = None
    last_err = None
    for cand in candidates:
        try:
            d = _get("planetary/apod", key, {"date": cand})
            used = cand
            break
        except requests.RequestException as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status not in (404, 500):
                break  # レート制限(429)等は日付を変えても無駄なので即中断
    if d is None:
        status = getattr(getattr(last_err, "response", None), "status_code", None)
        if status == 404:
            text = ("APOD はまだ公開されていません（試行日: " + ", ".join(candidates) +
                    "）。NASA 側の当日分公開は米国東部時間の夜になることがあります。")
        else:
            text = _rate_limit_text(last_err) or "NASA APOD の取得に失敗しました: " + nasa_budget.redact(last_err)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent={"error": nasa_budget.redact(last_err), "status": status,
                               "tried_dates": candidates, "source": "api.nasa.gov",
                               "budget": nasa_budget.status(key)},
        )
    text = (f"APOD {d.get('date','')} - {d.get('title','')}\n"
            f"{d.get('explanation','')}\n"
            f"画像: {d.get('hdurl') or d.get('url')} (Copyright: {d.get('copyright','不明')})")
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={
            "date": d.get("date", ""), "title": d.get("title", ""),
            "requested_date": str(date) if date else today.isoformat(),
            "fallback_to_previous_day": bool(date is None and used != today.isoformat()),
            "explanation": d.get("explanation", ""),
            "media_type": d.get("media_type", "image"),
            "image_url": d.get("hdurl") or d.get("url"),
            "copyright": d.get("copyright", "不明"),
        },
    )


@ttl_cache(TTL_HOURLY, maxsize=64, skip_if=is_error_result)
def neo_today(key: str) -> CallToolResult:
    """今日地球に接近する小惑星（Near Earth Object）の一覧を返す。

    認証不要（DEMO_KEY）または無料開発者キー。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        key: NASA Open API キー。
    """
    today = datetime.date.today().isoformat()
    try:
        d = _get("neo/rest/v1/feed", key, {"start_date": today, "end_date": today})
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        return CallToolResult(
            content=[TextContent(type="text",
                                 text=_rate_limit_text(e) or f"NASA NEO の取得に失敗しました: {e}")],
            structuredContent={"error": nasa_budget.redact(e), "status": status, "source": "api.nasa.gov",
                               "budget": nasa_budget.status(key)},
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

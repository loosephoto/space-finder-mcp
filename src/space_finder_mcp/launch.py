"""ロケット打ち上げ情報 (Launch Library 2 / The Space Devs, 認証なし)。"""
from __future__ import annotations

import threading
import time
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache, is_error_result
from .input_utils import as_int

LL2 = "https://ll.thespacedevs.com/2.3.0"

# ---- LL2 のレート制限（匿名は約 15 req/h）を記憶して fail fast する ----
# 実測: 429 の Retry-After は 104 秒。遮断中に素の呼び出しを投げると、そのツールが
# そのぶん待たされ、並列で走っている他のツール呼び出しも巻き込まれる。
# カレンダー（space_calendar）と同じ匿名枠を共有するので、記憶は 1 箇所に置く。
_LOCK = threading.Lock()
_STATE = {"blocked_until": 0.0, "reason": ""}


def blocked_left() -> float:
    """LL2 が 429 で遮断されている残り秒（0 なら送信してよい）。"""
    with _LOCK:
        return max(0.0, _STATE["blocked_until"] - time.time())


def note_429(retry_after=None) -> None:
    """429 を受けたことを記録する（Retry-After を尊重。過大な値は丸める）。"""
    try:
        wait = float(retry_after)
    except (TypeError, ValueError):
        wait = 60.0
    wait = min(max(wait, 5.0), 900.0)
    with _LOCK:
        _STATE["blocked_until"] = time.time() + wait
        _STATE["reason"] = "Launch Library 2 が 429（Retry-After={:.0f}秒・匿名は約15req/h）".format(wait)


def reset_blocked() -> None:
    """遮断の記憶を消す（主にテスト用）。"""
    with _LOCK:
        _STATE["blocked_until"] = 0.0
        _STATE["reason"] = ""


def blocked_message() -> str:
    """遮断中の説明文（残り秒つき）。"""
    left = blocked_left()
    with _LOCK:
        return "{}（あと {:.0f} 秒で解除）".format(_STATE["reason"], left)

@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def upcoming_launches(limit: int = 5) -> CallToolResult:
    """今後予定されているロケット打ち上げの一覧を返す。

    認証不要。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 5、最大 15）。
    """
    limit = as_int(limit, 5, 1, 15)
    if blocked_left() > 0:                      # 遮断中は送信しない（枠を無駄にしない）
        return CallToolResult(
            content=[TextContent(type="text", text=blocked_message() + "。しばらく待って再試行してください。")],
            structuredContent={"error": blocked_message(), "source": "ll.thespacedevs.com",
                               "retry_after_sec": round(blocked_left())})
    params = {"limit": min(limit, 30), "ordering": "window_start"}
    try:
        r = requests.get(f"{LL2}/launches/upcoming/", params=params, timeout=(10, 25))
        if r.status_code == 429:
            note_429(r.headers.get("Retry-After"))
            raise requests.HTTPError("429 Too Many Requests（Retry-After={}）".format(
                r.headers.get("Retry-After")))
        r.raise_for_status()
        reset_blocked()                          # 成功したら遮断の記憶を解除する
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Launch Library 2 への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "ll.thespacedevs.com"},
        )
    rows = d.get("results", [])
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="予定されている打ち上げが見つかりませんでした。")],
            structuredContent={"total": 0, "results": []},
        )
    rows = rows[:limit]
    records = []
    lines = ["今後のロケット打ち上げ:"]
    for i, l in enumerate(rows, 1):
        name = l.get("name", "?")
        window = (l.get("window_start") or "")[:16].replace("T", " ")
        status = (l.get("status") or {}).get("name", "?")
        pad = l.get("pad", {}) or {}
        site = (pad.get("location", {}) or {}).get("name", "?")
        launcher = (pad.get("launcher") or {}).get("name") or (l.get("rocket") or {}).get("configuration", {}).get("name", "?")
        mission = (l.get("mission") or {}).get("description", "")
        rec = {"name": name, "window_start_utc": window, "status": status,
               "launch_site": site, "rocket": launcher}
        records.append(rec)
        lines.append(f"{i}. **{name}**  {window} UTC  [{status}]")
        lines.append(f"   ロケット: {launcher} ／ 射場: {site}")
        if mission:
            lines.append(f"   ミッション: {mission[:90]}")
    lines.append("出典: Launch Library 2 (ll.thespacedevs.com)")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"shown": len(records), "results": records},
    )


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def china_launches(limit: int = 8, status: Optional[str] = None) -> CallToolResult:
    """中国のロケット打ち上げ予定（長征シリーズ・LandSpace等の民間企業を含む）を返す。

    Launch Library 2 を「search=China」で絞り込み、長征（Long March）シリーズや
    Shenzhou（有人）/Tianzhou（補給）、Hyperbola-3（LandSpace）等を取得する。
    認証不要。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 8、最大 15）。
        status: 状態で絞り込み（例 "Go for Launch", "To Be Determined"）。省略で全状態。
    """
    limit = as_int(limit, 8, 1, 15)
    if blocked_left() > 0:
        return CallToolResult(
            content=[TextContent(type="text", text=blocked_message() + "。しばらく待って再試行してください。")],
            structuredContent={"error": blocked_message(), "source": "ll.thespacedevs.com",
                               "retry_after_sec": round(blocked_left())})
    params = {"limit": min(limit * 2, 30), "search": "China", "ordering": "window_start"}
    try:
        r = requests.get(f"{LL2}/launches/upcoming/", params=params, timeout=(10, 25))
        if r.status_code == 429:
            note_429(r.headers.get("Retry-After"))
            raise requests.HTTPError("429 Too Many Requests（Retry-After={}）".format(
                r.headers.get("Retry-After")))
        r.raise_for_status()
        reset_blocked()                          # 成功したら遮断の記憶を解除する
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Launch Library 2 への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "ll.thespacedevs.com"},
        )
    rows = d.get("results", [])
    if status:
        st = status.strip().lower()
        rows = [x for x in rows if (x.get("status") or {}).get("name", "").strip().lower() == st]
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="中国の打ち上げ予定が見つかりませんでした（直近の日程確定前の可能性あり）。")],
            structuredContent={"country": "China", "total": 0, "results": []},
        )
    rows = rows[:limit]
    records = []
    lines = [f"中国のロケット打ち上げ予定（{len(rows)} 件）:"]
    for i, l in enumerate(rows, 1):
        name = l.get("name", "?")
        window = (l.get("window_start") or "")[:16].replace("T", " ")
        st = (l.get("status") or {}).get("name", "?")
        pad = l.get("pad", {}) or {}
        loc = pad.get("location", {}) or {}
        site = loc.get("name", "?")
        launcher = (pad.get("launcher") or {}).get("name") or (l.get("rocket") or {}).get("configuration", {}).get("name", "?")
        mission = (l.get("mission") or {}).get("description", "")
        rec = {"name": name, "window_start_utc": window, "status": st,
               "launch_site": site, "rocket": launcher}
        records.append(rec)
        lines.append(f"{i}. **{name}**  {window} UTC  [{st}]")
        lines.append(f"   ロケット: {launcher} ／ 射場: {site}")
        if mission:
            lines.append(f"   ミッション: {mission[:90]}")
    lines.append("出典: Launch Library 2 (ll.thespacedevs.com) ／ 射場は酒泉・西昌・太原・文昌・海上等。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"country": "China", "shown": len(records), "results": records},
    )


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def russia_launches(limit: int = 8, status: Optional[str] = None) -> CallToolResult:
    """ロシア（Roscosmos）のロケット打ち上げ予定を返す（ソユーズ・プロトン・アンガラ等）。

    Launch Library 2 を「search=Roscosmos」で絞り込み、ソユーズ（Soyuz）による
    Progress（補給）・Soyuz MS（有人）・Luna 等の打ち上げを取得する。
    ロシア直のオープンAPIは存在しないため（Roscosmos REST は404）、グローバル集約API経由。
    認証不要。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 8、最大 15）。
        status: 状態で絞り込み（例 "Go for Launch"）。省略で全状態。
    """
    limit = as_int(limit, 8, 1, 15)
    if blocked_left() > 0:
        return CallToolResult(
            content=[TextContent(type="text", text=blocked_message() + "。しばらく待って再試行してください。")],
            structuredContent={"error": blocked_message(), "source": "ll.thespacedevs.com",
                               "retry_after_sec": round(blocked_left())})
    params = {"limit": min(limit * 2, 30), "search": "Roscosmos", "ordering": "window_start"}
    try:
        r = requests.get(f"{LL2}/launches/upcoming/", params=params, timeout=(10, 25))
        if r.status_code == 429:
            note_429(r.headers.get("Retry-After"))
            raise requests.HTTPError("429 Too Many Requests（Retry-After={}）".format(
                r.headers.get("Retry-After")))
        r.raise_for_status()
        reset_blocked()                          # 成功したら遮断の記憶を解除する
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Launch Library 2 への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "ll.thespacedevs.com"},
        )
    rows = d.get("results", [])
    if status:
        st = status.strip().lower()
        rows = [x for x in rows if (x.get("status") or {}).get("name", "").strip().lower() == st]
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="ロシア（Roscosmos）の打ち上げ予定が見つかりませんでした。")],
            structuredContent={"country": "Russia", "total": 0, "results": []},
        )
    rows = rows[:limit]
    records = []
    lines = [f"🇷🇺 ロシア（Roscosmos）のロケット打ち上げ予定（{len(rows)} 件）:"]
    for i, l in enumerate(rows, 1):
        name = l.get("name", "?")
        window = (l.get("window_start") or "")[:16].replace("T", " ")
        st = (l.get("status") or {}).get("name", "?")
        pad = l.get("pad", {}) or {}
        loc = pad.get("location", {}) or {}
        site = loc.get("name", "?")
        launcher = (pad.get("launcher") or {}).get("name") or (l.get("rocket") or {}).get("configuration", {}).get("name", "?")
        mission = (l.get("mission") or {}).get("description", "")
        rec = {"name": name, "window_start_utc": window, "status": st,
               "launch_site": site, "rocket": launcher}
        records.append(rec)
        lines.append(f"{i}. **{name}**  {window} UTC  [{st}]")
        lines.append(f"   ロケット: {launcher} ／ 射場: {site}")
        if mission:
            lines.append(f"   ミッション: {mission[:90]}")
    lines.append("出典: Launch Library 2 (ll.thespacedevs.com) ／ 射場はバイコヌール・ボストチヌイ・プレセツク等。")
    lines.append("🤖 【AIからのインテリジェントアドバイス】ロシア直のオープンAPIは公開されていないため、グローバル集約API（Launch Library 2）経由で取得しています。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"country": "Russia", "shown": len(records), "results": records},
    )

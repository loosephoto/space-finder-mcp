"""NASA DONKI — 宇宙天気（スペースウェザー）予報・観測データ（api.nasa.gov）。

DONKI (Database Of Notifications, Knowledge, Information) は太陽活動に伴う
宇宙環境の乱れ（太陽フレア・CME・地磁気嵐・太陽粒子現象）を観測・警報するAPI。
天体観測・通信障害・衛星運用・航空運航などの影響評価に使える。

認証: api.nasa.gov の無料キー（環境変数 NASA_API_KEY）。未設定時は DEMO_KEY
（レート制限 30 req/hr/IP）。キーはサーバー側でのみ保持。
出典: api.nasa.gov（NASA Space Weather）
"""
from __future__ import annotations

import os
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache, is_error_result
from .input_utils import as_int

DONKI = "https://api.nasa.gov/DONKI"
UA = {"User-Agent": "space-finder-mcp/0.6 (MCP; NASA DONKI space weather)"}

# フレア規模の説明
_FLARE_CLASS = {
    "A": "微小(観測機器でしか検知されない)", "B": "微弱", "C": "小規模（地球への大きな影響は通常なし）",
    "M": "中規模（高緯度でオーロラ・短波通信障害の可能性）", "X": "大規模（広域通信障害・放射線被ばくの可能性）",
}
# 地磁気嵐 Kp 指数の説明
_KP_LEVEL = {
    (0, 2): "静穏", (3, 4): "やや活発", (5, 5): "磁気嵐(小)", (6, 6): "磁気嵐(中)",
    (7, 7): "磁気嵐(強)", (8, 9): "磁気嵐(激甚)",
}


def _kp_label(kp: float) -> str:
    for (lo, hi), lab in _KP_LEVEL.items():
        if lo <= kp <= hi:
            return lab
    return ""


def _get(endpoint: str, params: dict, timeout: int = 30) -> list:
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    p = dict(params)
    p["api_key"] = key
    r = requests.get(f"{DONKI}/{endpoint}", params=p, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def space_weather(kind: str = "all", start_date: Optional[str] = None,
                  end_date: Optional[str] = None, limit: int = 10) -> CallToolResult:
    """NASA DONKI の宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象）を返す。

    天体観測や通信・衛星運用に影響する太陽活動を確認できる。
    例:「最近の太陽フレア」「CME(コロナ質量放出)の情報」「地磁気嵐は起きてる?」
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        kind: データ種別
            - "all": 太陽フレア・CME・地磁気嵐・太陽粒子をまとめて表示（既定）
            - "flare": 太陽フレア(FLR)
            - "cme": コロナ質量放出(CME)
            - "gst": 地磁気嵐(GST)
            - "sep": 太陽高エネルギー粒子現象(SEP)
        start_date: 開始日（YYYY-MM-DD）。省略時は既定（最近）。
        end_date: 終了日（YYYY-MM-DD）。省略時は既定。
        limit: 各カテゴリの返す件数（既定 10、最大 20）。
    """
    limit = as_int(limit, 10, 1, 20)
    kind = (kind or "all").strip().lower()
    params = {}
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date

    result_map = {}  # カテゴリ -> 表示行リスト
    result_json = {}
    errors = []

    def _handle(cat: str, label: str, parse):
        try:
            data = _get(cat, params)
            rows, jrows = parse(data, limit)
            result_map[label] = rows
            result_json[label] = jrows
        except requests.RequestException as e:
            errors.append(f"{label}: {str(e)[:80]}")

    def _parse_flare(data, lim):
        rows, jrows = [], []
        for r in data[:lim]:
            ct = r.get("classType", "?")
            cls = ct[0] if ct and ct[0] in "ABCMX" else "?"
            desc = _FLARE_CLASS.get(cls, "")
            row = f"- **{ct}** フレア  開始 {r.get('beginTime','')[:16].replace('T',' ')}  "
            if r.get("sourceLocation"):
                row += f"位置 {r['sourceLocation']}  "
            row += f"({desc})" if desc else ""
            rows.append(row)
            jrows.append({"id": r.get("flrID"), "class": ct,
                          "begin": r.get("beginTime"), "peak": r.get("peakTime"),
                          "end": r.get("endTime"), "source": r.get("sourceLocation")})
        return rows, jrows

    def _parse_cme(data, lim):
        rows, jrows = [], []
        for r in data[:lim]:
            speed_raw = (r.get("cmeAnalyses") or [{}])[0].get("speed") if r.get("cmeAnalyses") else None
            try:
                speed = float(speed_raw) if speed_raw is not None else None
            except (TypeError, ValueError):
                speed = None
            row = f"- CME  開始 {r.get('startTime','')[:16].replace('T',' ')}"
            if r.get("sourceLocation"):
                row += f"  太陽面位置 {r['sourceLocation']}"
            if speed is not None:
                row += f"  速度 {speed:.0f} km/s"
            rows.append(row)
            jrows.append({"id": r.get("activityID"), "start": r.get("startTime"),
                          "source": r.get("sourceLocation"), "speed_km_s": speed,
                          "instruments": [i.get("displayName") for i in r.get("instruments", [])]})
        return rows, jrows

    def _parse_gst(data, lim):
        rows, jrows = [], []
        for r in data[:lim]:
            kp_list = r.get("allKpIndex") or []
            max_kp = max((k.get("kpIndex", 0) for k in kp_list), default=0)
            lab = _kp_label(max_kp)
            row = f"- 地磁気嵐  開始 {r.get('startTime','')[:16].replace('T',' ')}  Kp最大 {max_kp}"
            row += f"（{lab}）" if lab else ""
            rows.append(row)
            jrows.append({"id": r.get("gstID"), "start": r.get("startTime"),
                          "max_kp": max_kp, "kp_level": lab,
                          "kp_series": [{"time": k.get("observedTime"), "kp": k.get("kpIndex")} for k in kp_list]})
        return rows, jrows

    def _parse_sep(data, lim):
        rows, jrows = [], []
        for r in data[:lim]:
            ev = r.get("eventTime") or ""
            row = f"- 太陽粒子現象  発生 {ev[:16].replace('T',' ')}  "
            if r.get("instruments"):
                row += "(" + ", ".join(i.get("displayName") for i in r["instruments"][:2]) + ")"
            rows.append(row)
            jrows.append({"id": r.get("sepID"), "event_time": ev,
                          "instruments": [i.get("displayName") for i in r.get("instruments", [])],
                          "linked_events": [e.get("activityID") for e in r.get("linkedEvents", [])]})
        return rows, jrows

    if kind in ("all", "flare"):
        _handle("FLR", "太陽フレア", _parse_flare)
    if kind in ("all", "cme"):
        _handle("CME", "コロナ質量放出(CME)", _parse_cme)
    if kind in ("all", "gst"):
        _handle("GST", "地磁気嵐", _parse_gst)
    if kind in ("all", "sep"):
        _handle("SEP", "太陽粒子現象", _parse_sep)

    if not result_map:
        valid = {"flare", "cme", "gst", "sep", "all"}
        if kind not in valid:
            return CallToolResult(
                content=[TextContent(type="text", text="kind は flare/cme/gst/sep/all のいずれかを指定してください。")],
                structuredContent={"error": "bad kind", "kind": kind, "valid": sorted(valid)},
            )
        # kind は正しいが取得失敗（レート制限など）
        return CallToolResult(
            content=[TextContent(type="text", text="宇宙天気データを取得できませんでした（NASA API のレート制限や一時的障害の可能性）。NASA_API_KEY を設定すると制限が緩和されます。")],
            structuredContent={"error": "fetch failed", "kind": kind, "detail": errors},
        )

    lines = ["☀️ **NASA 宇宙天気（DONKI）** 出典: api.nasa.gov（NASA Space Weather）"]
    for label, rows in result_map.items():
        lines.append(f"\n### {label}")
        if rows:
            lines.extend(rows)
        else:
            lines.append("（期間内の記録なし）")
    if errors:
        lines.append("\n⚠️ 一部カテゴリの取得に失敗: " + "; ".join(errors))
    if kind == "all":
        lines.append("\n🤖 【AIからのインテリジェントアドバイス】宇宙天気は天体観測（オーロラ・電波）や通信・衛星運用に影響します。M級以上のフレアや地磁気嵐が発生している間は、高緯度での短波通信障害や衛星測位誤差が起きやすくなります。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"kind": kind, "start": start_date, "end": end_date,
                           "source": "api.nasa.gov/DONKI", "data": result_json,
                           "errors": errors},
    )

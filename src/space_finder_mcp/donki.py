"""NASA DONKI — 宇宙天気（スペースウェザー）予報・観測データ（NASA CCMC）。

DONKI (Database Of Notifications, Knowledge, Information) は太陽活動に伴う
宇宙環境の乱れ（太陽フレア・CME・地磁気嵐・太陽粒子現象）を観測・警報するAPI。
天体観測・通信障害・衛星運用・航空運航などの影響評価に使える。

認証: **不要**。2026-09-30 に DONKI の URL が移転し、旧 `api.nasa.gov/DONKI` と
`kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get` は 301 で CCMC のお知らせページ（HTML）へ
転送される。新エンドポイント `ccmc.gsfc.nasa.gov/DONKI-API/get` は API キーを
受け付けず（実測: 200/application/json、`X-Rate-Limit-Remaining: 9999`）、
NASA の DEMO_KEY（30リクエスト/時/IP）とも別枠なので、`nasa_budget` を通さず
`api_key` も送らない。**キーを持たないホストへ利用者のキーを渡さない。**
出典: ccmc.gsfc.nasa.gov/DONKI-API（NASA CCMC / M2M-SWAO）
"""
from __future__ import annotations

import datetime
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from . import nasa_budget
from .cache import TTL_SHORT, ttl_cache, is_error_result
from .input_utils import as_int

# 2026-09-30 の移転後のベース（末尾は `/get`。呼び出し側が `/{endpoint}` を足す）
DONKI = "https://ccmc.gsfc.nasa.gov/DONKI-API/get"
UA = {"User-Agent": "space-finder-mcp/0.6 (MCP; NASA DONKI space weather)"}
CONNECT_TIMEOUT = 10        # 外部 HTTP は (connect, read) で必ず打ち切る
READ_TIMEOUT = 30
CME_DEFAULT_DAYS = 30       # CME は日付が必須。無指定時は他のカテゴリの既定（直近30日）に合わせる


def _default_window(days: int = CME_DEFAULT_DAYS) -> dict:
    """日付が必須のエンドポイントへ渡す既定の期間（直近 days 日）。

    移転先の CME は `startDate`/`endDate` を省くと **400 Bad Request** になる
    （実測。FLR/GST/SEP は省略してもサーバ側の既定＝直近30日を返す）。旧 URL は
    301 で到達していなかったため気付かなかった潜在バグ。
    """
    end = datetime.date.today()
    return {"startDate": (end - datetime.timedelta(days=days)).isoformat(),
            "endDate": end.isoformat()}

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


def _get(endpoint: str, params: dict, timeout: int = READ_TIMEOUT) -> list:
    """DONKI の1エンドポイントを取得して JSON を返す（認証不要・予算管理なし）。

    URL 移転（301）と「JSON でない応答」を**明示的に検知**する。追跡先の HTML を
    そのまま `r.json()` に渡すと `Expecting value: line 1 column 1 (char 0)` という
    原因の分からない文言になり、実測では「NASA API の一時的障害」と誤診した
    （旧 `api.nasa.gov/DONKI/*` が CCMC のお知らせページへ 301 していた）。
    """
    r = requests.get(f"{DONKI}/{endpoint}", params=dict(params), headers=UA,
                     timeout=(CONNECT_TIMEOUT, timeout))
    if r.history:
        # 301/302 を追跡した＝API の URL が移転した
        raise requests.RequestException(
            "DONKI の URL が転送されました（HTTP {} → {}）。API の移転先を確認してください"
            .format(r.history[0].status_code, r.url))
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        raise requests.RequestException(
            "DONKI が JSON を返しませんでした（HTTP {}・{} bytes・Content-Type {}）"
            .format(r.status_code, len(r.content or b""),
                    (r.headers.get("Content-Type") or "?").split(";")[0]))


@ttl_cache(TTL_SHORT, maxsize=64)
def _get_cached(endpoint: str, start_date: Optional[str] = None,
                end_date: Optional[str] = None) -> list:
    """DONKI 1エンドポイントを**カテゴリ単位**でキャッシュして取得する。

    `space_weather(kind="all")` は FLR/CME/GST/SEP の4エンドポイントを叩く。カテゴリ
    単位でキャッシュすると、続けて kind を変えたとき（all → flare 等）や同じ kind の
    再呼び出しで同じカテゴリを取り直さず、外部への往復を減らせる（移転後は認証不要なので
    `DEMO_KEY` の共有枠は消費しない）。
    例外（通信失敗・URL 移転・非 JSON 等）はそのまま伝播しキャッシュされない（失敗を固定化しない）。
    """
    p = {}
    if start_date:
        p["startDate"] = start_date
    if end_date:
        p["endDate"] = end_date
    return _get(endpoint, p)


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def space_weather(kind: str = "all", start_date: Optional[str] = None,
                  end_date: Optional[str] = None, limit: int = 10) -> CallToolResult:
    """NASA DONKI の宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象）を返す。

    天体観測や通信・衛星運用に影響する太陽活動を確認できる。

    **DONKI（ccmc.gsfc.nasa.gov/DONKI-API・認証不要・専用のレート枠）が障害のときは、認証不要の NOAA SWPC（Kp・NOAA スケール・

    GOES X線・フレアイベント（直近7日）・太陽風・陽子・警報・黒点相対数）に自動で切り替えて返す**（どちらの出典かを

    content と structuredContent.source に明記する）。API キーは不要（`NASA_API_KEY` は apod / neo_today のみで使用）。
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

    def _handle(cat: str, label: str, parse, extra_params: Optional[dict] = None):
        try:
            p = extra_params if extra_params is not None else params
            # カテゴリ単位のキャッシュ経由（同じカテゴリを取り直さない）
            data = _get_cached(cat, p.get("startDate"), p.get("endDate"))
            rows, jrows = parse(data, limit)
            result_map[label] = rows
            result_json[label] = jrows
        except requests.RequestException as e:
            errors.append("{}: {}".format(label, nasa_budget.redact(e)[:80]))

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

    # CME だけは日付が必須（無指定は 400）。無指定の側だけ直近30日で埋める
    # （片方だけ指定されたときは、指定を尊重して欠けた側だけ補う）。
    cme_params = dict(params)
    if not cme_params.get("startDate") or not cme_params.get("endDate"):
        _d = _default_window()
        cme_params.setdefault("startDate", _d["startDate"])
        cme_params.setdefault("endDate", _d["endDate"])

    if kind in ("all", "flare"):
        _handle("FLR", "太陽フレア", _parse_flare)
    if kind in ("all", "cme"):
        _handle("CME", "コロナ質量放出(CME)", _parse_cme, extra_params=cme_params)
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
        # kind は正しいが取得失敗（DONKI 側の障害・URL 移転・JSON でない応答など）。
        # DONKI は認証不要でレート枠も専用（`X-Rate-Limit-Remaining: 9999`）なので、
        # ここで nasa_budget（DEMO_KEY の 30リクエスト/時/IP 共有枠）に触れない。
        # 触れると、apod/neo_today が使い切っただけの枠で**呼び出し前に**止まり、
        # 生きている DONKI まで返せなくなる（移転前の実装にあった二重の害）。
        nasa_reason = ("DONKI の取得に失敗: " + "; ".join(errors)) if errors else "DONKI の取得に失敗"
        nasa_reason = nasa_reason[:300]
        # DONKI が使えないときは**認証不要の NOAA SWPC にフォールバック**する。
        # どちらのデータを返したかは content / structuredContent の両方に明記する（出所の取り違え防止）。
        swpc_error = ""
        try:
            from . import swpc
            fallback = swpc.space_weather_now(kind, nasa_reason=nasa_reason)
        except Exception as e:                      # SWPC 側も例外を漏らさない
            fallback, swpc_error = None, str(e)[:150]
        if fallback is not None:
            return fallback
        return CallToolResult(
            content=[TextContent(type="text", text="宇宙天気データを取得できませんでした（NASA DONKI の取得失敗）。")],
            structuredContent={"error": "fetch failed", "kind": kind, "detail": errors,
                               "swpc_error": swpc_error},
        )

    lines = ["☀️ **NASA 宇宙天気（DONKI）** 出典: ccmc.gsfc.nasa.gov/DONKI-API（NASA CCMC）"]
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
                           "source": "ccmc.gsfc.nasa.gov/DONKI-API", "data": result_json,
                           "errors": errors},
    )

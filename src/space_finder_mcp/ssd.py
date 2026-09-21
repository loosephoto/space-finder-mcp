"""JPL SSD / CNEOS — 天体異常系（火球・小惑星等の接近・将来の衝突リスク）の公開API。

「天体異常系」として次の3種類を扱う（いずれも `https://ssd-api.jpl.nasa.gov` の
公開 API で **APIキー不要**。したがって api.nasa.gov の DEMO_KEY
（30リクエスト/時/IP の共有枠）を消費せず、`apod` / `neo_today` /
`space_weather`(DONKI) の枠争いを悪化させない）。

- 火球（大気圏突入）: Fireball Data API (v1.2) — 放射エネルギー・衝突エネルギー・
  突入高度・緯度経度・突入速度成分
- 地球接近（接近イベント）: SBDB Close-Approach Data (CAD, v1.5) — 地心距離・
  相対速度・絶対等級 H・既知の直径
- 将来の衝突リスク: Sentry (v2.0) — 累積衝突確率(ip)・パレルモスケール・
  想定時期・推定直径

用語の区別（取り違えない。誤った天体を出さないための規約）:
- 火球(fireball)/流星(meteor) = 大気圏に突入して光る**現象**。隕石(meteorite)の
  回収・落下物とは別物（本モジュールは落下物の推定をしない）
- 小惑星の接近(close approach) = 天体が地球の近くを**通る**こと（衝突ではない）
- 衝突リスク(Sentry) = 今後100年規模での**確率**の推算。観測が増えると大半は
  消える（確率が 0 になれば Sentry から削除される）

データは NASA/JPL CNEOS（Center for Near Earth Object Studies）が運用。
出典: ssd-api.jpl.nasa.gov（NASA/JPL CNEOS）
"""
from __future__ import annotations

import datetime as _dt
import math
import threading
import time
from typing import Optional
from urllib.parse import quote as _url_quote

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_HOURLY, ttl_cache, is_error_result
from .input_utils import as_float, as_int

BASE = "https://ssd-api.jpl.nasa.gov"
UA = {"User-Agent": "space-finder-mcp/0.33 (MCP; JPL SSD/CNEOS)"}
TIMEOUT = (10, 30)              # (connect, read) 秒。connect を短くして固まりを防ぐ
AU_KM = 149_597_870.7           # 1 au（IAU 2012 定義）
LD_KM = 384_400.0               # 月平均距離（1 LD）— 接近距離の慣例的なものさし
HIROSHIMA_KT = 15.0             # 広島型原爆のエネルギー（TNT換算の目安）
J_PER_TON = 4.184e9             # 1 t TNT = 4.184e9 J
ALBEDO_ASSUMED = 0.14           # H から直径を換算するときの仮定アルベド（NEO の慣例値）

# 遮断（403・TCP blackhole）を記憶して fail fast する。遮断中に素の GET を投げると
# 1回の呼び出しが分単位で固まり、LLM が並行に投げた他のツール呼び出しまで待たされる
# （celestrak.py / nasa_budget.py と同じ方針）。
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0
_COOLDOWN_LAST = ""
_COOLDOWN_SECONDS_BLOCKED = 300.0
_COOLDOWN_SECONDS_NET = 60.0


def _blocked_status() -> tuple:
    """遮断中なら (残り秒, 理由)、そうでなければ (0.0, "")。"""
    with _COOLDOWN_LOCK:
        return (max(0.0, _COOLDOWN_UNTIL - time.time()), _COOLDOWN_LAST)


def _note_blocked(seconds: float, reason: str) -> None:
    global _COOLDOWN_UNTIL, _COOLDOWN_LAST
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + seconds)
        _COOLDOWN_LAST = reason


def _blocked_message(remaining: float, reason: str) -> str:
    return ("JPL SSD へのアクセスが一時的に遮断されています（{}）。"
            "約 {:.0f} 分後に再試行してください。".format(
                reason or "接続不可", max(1.0, remaining) / 60.0))


def _get_json(endpoint: str, params: Optional[dict] = None) -> dict:
    """JPL SSD のエンドポイントを取得して JSON を返す（例外は requests 系に正規化）。

    403・接続不可は「一時的な遮断」として記憶し、以後は HTTP を投げずに即失敗する。
    400（引数の誤り）は API が JSON に message を入れて返すので、そのまま伝播させて
    呼び出し側が利用者向けの文言に変換する（`_http_detail`）。
    """
    remaining, reason = _blocked_status()
    if remaining > 0:
        raise requests.ConnectionError(_blocked_message(remaining, reason))
    try:
        r = requests.get("{}/{}".format(BASE, endpoint), params=params or {},
                         headers=UA, timeout=TIMEOUT)
    except (requests.ConnectionError, requests.Timeout) as e:
        _note_blocked(_COOLDOWN_SECONDS_NET, "接続できません（遮断またはネットワーク断）")
        raise requests.ConnectionError(
            "JPL SSD に接続できませんでした（一時的な遮断またはネットワーク断）。"
            "約 {:.0f} 分後に再試行してください。".format(
                _COOLDOWN_SECONDS_NET / 60.0)) from e
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code == 403:
            _note_blocked(_COOLDOWN_SECONDS_BLOCKED, "403 Forbidden（IP 単位の遮断）")
            raise requests.HTTPError(
                "アクセスが一時的に拒否されました（403 Forbidden）。数分待つと解除されます。",
                response=getattr(e, "response", None)) from e
        raise
    try:
        return r.json()
    except ValueError as e:
        raise requests.RequestException(
            "応答が JSON ではありません (HTTP {})".format(r.status_code)) from e


def _http_detail(e: Exception) -> str:
    """HTTP エラー本文の message（API が引数の誤りを説明している）を取り出す。"""
    resp = getattr(e, "response", None)
    if resp is None:
        return ""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or "")[:200]
    return ""


def _api_error(payload) -> Optional[str]:
    """HTTP 200 で返る「エラー本文」を検出する。

    Sentry は **削除済み/未登録の天体でも 200** を返し、本文の error キーで知らせる
    （実測: {"error":"specified object removed","removed":"2021-02-21 08:22:28"}）。
    `raise_for_status()` だけでは成功と区別できないため、必ずここを通す。
    """
    if not isinstance(payload, dict):
        return "応答の形が想定外です"
    if payload.get("error"):
        msg = str(payload["error"])
        if payload.get("removed"):
            msg += "（Sentry からの削除日時: {}）".format(payload["removed"])
        return msg
    code = payload.get("code")
    if code is not None and str(code) not in ("200", "0"):
        return str(payload.get("message") or code)
    return None


def _records(payload: dict) -> list:
    """JSON の data を「フィールド名で引ける辞書」の配列に正規化する。

    - fields で列名が与えられる（**順序は将来変わりうる**ので位置で決め打ちしない）
    - 条件が厳しすぎて 0 件のときは **data キー自体が無い**（実測。doc にも明記）
    - Sentry の summary モードは最初から dict の配列
    """
    rows = payload.get("data") or []
    fields = payload.get("fields") or []
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({str(f): (row[i] if i < len(row) else None)
                        for i, f in enumerate(fields)})
    return out


def _num(value) -> Optional[float]:
    """数値化（失敗・null・NaN・無限大は None）。API の値は文字列で来る。"""
    return as_float(value, None)


def _au_to_km(au) -> Optional[float]:
    v = _num(au)
    return None if v is None else v * AU_KM


def _lunar_distances(km: Optional[float]) -> Optional[float]:
    return None if km is None else km / LD_KM


def _diameter_from_h(h, albedo: float = ALBEDO_ASSUMED) -> Optional[float]:
    """絶対等級 H から推定直径(m)を換算する（D[km] = 1329 × 10^(-H/5) / √p）。

    H は観測量（明るさ）で、直径はアルベド p の仮定に依存する。**推定値**であることを
    表示側で必ず明示する（既知の直径が得られる場合はそちらを優先する）。
    """
    f = _num(h)
    if f is None or f <= 0.0:
        return None
    return 1329.0 * math.pow(10.0, -f / 5.0) / math.sqrt(albedo) * 1000.0


def _fmt_num(v, unit: str = "", digits: int = 3) -> str:
    """数値を読みやすく整形する（None は「不明」）。

    digits<=0 か 1000 以上は桁区切りの整数、それ以外は有効数字 digits 桁で表す
    （"{:.0g}" は指数表記になり「300 → 3e+02」と読みにくくなるため使わない）。
    """
    f = _num(v)
    if f is None:
        return "不明"
    fmt = "{:,.0f}" if (digits <= 0 or abs(f) >= 1000.0) else "{:." + str(digits) + "g}"
    return fmt.format(f) + unit


def _diameter_text(km) -> str:
    """推定直径の表示（1 km 未満はメートルも併記する）。

    Sentry の `diameter` は km 単位（例 0.0071）。小天体は km だけだと大きさが
    直感に合わないため、1 km 未満では m 換算を数値から生成して添える。
    """
    f = _num(km)
    if f is None:
        return "不明"
    if f < 1.0:
        return "{:g} km（約 {:,.0f} m）".format(f, f * 1000.0)
    return "{:,.3g} km".format(f)


def _fmt_prob(ip) -> str:
    """衝突確率を「確率表記＋何回に1回」で表す（0 や不明も扱う）。"""
    f = _num(ip)
    if f is None:
        return "不明"
    if f <= 0.0:
        return "0（現時点で衝突は予測されていない）"
    return "{:.3g}（{:.4g}%・約 {:,.0f} 回に1回）".format(f, f * 100.0, 1.0 / f)


def _uncertainty_text(raw) -> str:
    """接近時刻の不確かさ（CAD の t_sigma_f: "< 00:01" / "2_06:31"）を日本語にする。"""
    s = str(raw or "").strip()
    if not s:
        return "不明"
    less_than = s.startswith("<")
    body = s.lstrip("<>").strip()
    try:
        if "_" in body:                       # 日_時:分
            dpart, tpart = body.split("_", 1)
            hh, mm = (tpart.split(":") + ["0"])[:2]
            days, hours, mins = int(float(dpart)), int(hh), int(mm)
            if less_than and days == 0 and hours == 0 and mins <= 1:
                return "1分未満"
            if days == 0:
                text = "{}時間{}分".format(hours, mins)
            else:
                text = "{}日 {}時間{}分".format(days, hours, mins)
        else:                                 # 時:分
            hh, mm = (body.split(":") + ["0"])[:2]
            hours, mins = int(hh), int(mm)
            if less_than and hours == 0 and mins <= 1:
                return "1分未満"
            text = "{}時間{}分".format(hours, mins)
    except (TypeError, ValueError):
        return s
    return ("約 " if less_than else "± ") + text


def _energy_compare(kt) -> str:
    """衝突エネルギー(kt)を広島型原爆（約15kt）との比に言い換える（数値から生成）。"""
    f = _num(kt)
    if f is None or f <= 0.0:
        return ""
    ratio = f / HIROSHIMA_KT
    if ratio >= 1.0:
        return "（広島型原爆 約15kt の約 {:.3g} 倍）".format(ratio)
    return "（広島型原爆 約15kt の約 {:.3g}%）".format(ratio * 100.0)


def _entry_speed(rec: dict) -> Optional[float]:
    """突入速度(km/s)を速度成分 vx,vy,vz から算出する（各成分は欠測しうる）。"""
    comps = [_num(rec.get("v" + a)) for a in ("x", "y", "z")]
    if any(c is None for c in comps):
        return None
    return math.sqrt(sum(c * c for c in comps if c is not None))


def _location_text(rec: dict) -> str:
    """緯度・経度を日本語の方位付きで表す（位置が未報告の記録は「位置不明」）。"""
    lat, lon = _num(rec.get("lat")), _num(rec.get("lon"))
    lat_dir, lon_dir = str(rec.get("lat-dir") or ""), str(rec.get("lon-dir") or "")
    if lat is None or lon is None or not lat_dir or not lon_dir:
        return "位置不明（観測地点が未報告）"
    return "{}緯 {:.1f}° {}経 {:.1f}°".format(
        {"N": "北", "S": "南"}.get(lat_dir, ""), abs(lat),
        {"E": "東", "W": "西"}.get(lon_dir, ""), abs(lon))


def _sbdb_url(des) -> Optional[str]:
    """JPL SBDB の小天体ページ（確認用リンク）。designation が無ければ None。"""
    s = str(des or "").strip()
    if not s:
        return None
    return "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=" + _url_quote(s)


def _error_result(text: str, **extra) -> CallToolResult:
    """エラー応答（例外をツール外へ漏らさないための共通形・規約1）。"""
    payload = {"error": text, "source": "NASA/JPL CNEOS (ssd-api.jpl.nasa.gov)"}
    payload.update(extra)
    return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=payload)


@ttl_cache(TTL_HOURLY, maxsize=32, skip_if=is_error_result)
def fireball_reports(days: int = 30, min_impact_energy_kt: Optional[float] = None,
                     limit: int = 10, require_location: bool = False) -> CallToolResult:
    """JPL CNEOS の火球（大気圏突入）観測記録を返す（認証不要・APIキー不要）。

    火球とは**大気圏に突入して光った現象**の観測記録です（NASA/JPL CNEOS の
    Fireball Data）。米国政府のセンサ（衛星・赤外）や地上観測の報告に基づきます。
    隕石（地上に落下した石）の回収情報ではないため、**落下物の推定はしません**。

    例:「最近の火球」「今月の大きな火球」「火球はどこで観測された?」「隕石は落ちた?」

    content に表示用サマリ、structuredContent に JSON を返します。

    Args:
        days: 何日前までをさかのぼるか（1〜1825、既定 30）。UTC の日付で絞り込みます。
        min_impact_energy_kt: 衝突エネルギーの下限（kt TNT 換算。例 0.5）。省略時は全件。
        limit: 表示する最大件数（1〜50、既定 10）。新しい順。
        require_location: True なら緯度経度が報告されている記録だけを返します。

    放射エネルギー(J)・衝突エネルギー(kt)・突入高度(km)・緯度経度・突入速度
    （速度成分 vx,vy,vz から算出。**欠測が多い**）を返します。位置が未報告の記録は
    「位置不明」と明示します。緯度経度・高度は**最大光度（ピーク）時点**の値で、
    落下地点ではありません（落下物の推定はしません）。

    出典: NASA/JPL CNEOS Fireball Data API（ssd-api.jpl.nasa.gov）
    """
    limit = as_int(limit, 10, 1, 50) or 10
    days = as_int(days, 30, 1, 1825) or 30
    min_kt = as_float(min_impact_energy_kt, None, 0.0)

    today = _dt.datetime.now(_dt.timezone.utc).date()
    start = today - _dt.timedelta(days=days - 1)
    params = {"date-min": start.isoformat(), "date-max": today.isoformat(),
              "sort": "-date", "limit": limit, "vel-comp": "true"}
    if min_kt is not None:
        params["impact-e-min"] = "{:g}".format(min_kt)      # kt（doc: impact-e-min は kt）
    if require_location:
        params["req-loc"] = "true"

    try:
        payload = _get_json("fireball.api", params)
    except requests.RequestException as e:
        detail = _http_detail(e)
        return _error_result(
            "JPL CNEOS の火球データを取得できませんでした: {}{}".format(
                str(e)[:150], "（{}）".format(detail) if detail else ""),
            endpoint="fireball.api", params=params)
    err = _api_error(payload)
    if err:
        return _error_result("火球データの取得に失敗しました: {}".format(err),
                             endpoint="fireball.api", params=params)

    results, lines = [], []
    for rec in _records(payload):
        impact_kt = _num(rec.get("impact-e"))
        radiated = _num(rec.get("energy"))
        # doc: energy は 10^10 J 単位（kt ではない）。衝突エネルギーは impact-e(kt)。
        radiated_j = None if radiated is None else radiated * 1e10
        alt = _num(rec.get("alt"))
        speed = _entry_speed(rec)
        date_utc = str(rec.get("date") or "").strip()
        row = "- **{} UTC** ／ {} ／ 高度 {} ／ 衝突エネルギー {}{}".format(
            date_utc, _location_text(rec),
            _fmt_num(alt, " km") if alt is not None else "不明",
            _fmt_num(impact_kt, " kt"), _energy_compare(impact_kt))
        if radiated_j is not None:
            row += "／ 放射エネルギー {:,.1e} J（TNT 換算 約 {:.3g} t）".format(
                radiated_j, radiated_j / J_PER_TON)
        if speed is not None:
            row += "／ 突入速度 約 {:.1f} km/s".format(speed)
        lines.append(row)
        results.append({
            "date_utc": date_utc,
            "impact_energy_kt": impact_kt,
            "radiated_energy_j": radiated_j,
            "altitude_km": alt,
            "latitude": _num(rec.get("lat")),
            "latitude_dir": rec.get("lat-dir"),
            "longitude": _num(rec.get("lon")),
            "longitude_dir": rec.get("lon-dir"),
            "entry_speed_km_s": speed,
            "location_reported": bool(rec.get("lat-dir") and rec.get("lon-dir")),
        })

    head = "🪨 **JPL CNEOS 火球（大気圏突入）観測** 直近{}日（{}〜{} UTC）: {}件".format(
        days, start.isoformat(), today.isoformat(), len(results))
    if min_kt is not None:
        head += "（衝突エネルギー {} kt 以上）".format(_fmt_num(min_kt))
    notes = [
        "",
        "ℹ️ 火球＝**大気圏に突入して光った現象**の観測記録です"
        "（隕石の回収・落下物の推定ではありません）。",
        "ℹ️ 緯度経度・高度は**最大光度（ピーク）時点**の値で、落下地点ではありません。",
        "ℹ️ 元データは NASA/JPL CNEOS Fireball Data（米国政府センサ・地上観測の報告）。"
        "位置が未報告の記録は「位置不明」とし、突入速度は速度成分が揃った記録のみ算出します。",
        "出典: NASA/JPL CNEOS（ssd-api.jpl.nasa.gov）",
    ]
    if not results:
        lines.append("この期間・条件に該当する火球はありませんでした（`days` を広げてください）。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join([head] + lines + notes))],
        structuredContent={
            "source": "NASA/JPL CNEOS Fireball Data API",
            "days": days, "start_date": start.isoformat(), "end_date": today.isoformat(),
            "min_impact_energy_kt": min_kt, "shown": len(results), "results": results,
        })


@ttl_cache(TTL_HOURLY, maxsize=32, skip_if=is_error_result)
def neo_close_approach(days: int = 7, max_distance_au: float = 0.05, limit: int = 10,
                       hazardous_only: bool = False) -> CallToolResult:
    """小惑星・彗星の地球接近（近地球天体の接近イベント）を返す（認証不要・APIキー不要）。

    JPL SBDB の接近データ（CAD）から、これから接近する小天体を返します。
    **接近は衝突ではありません**。距離は地球中心からの距離（地心距離）で、慣例的に
    月距離（1 LD = 384,400 km）も併記します。`neo_today`（今日の接近を api.nasa.gov
    から取得）より広い期間・距離を扱え、DEMO_KEY の共有枠も消費しません。

    例:「今日/今週地球に接近する小惑星」「危険な小惑星の接近予報」「月距離以内に来る天体」

    content に表示用サマリ、structuredContent に JSON を返します。

    Args:
        days: 何日先までを見るか（1〜365、既定 7）。
        max_distance_au: 接近距離の上限（au、既定 0.05 ≒ 19.5 月距離。0.00256 au ≒ 1 LD）。
        limit: 表示する最大件数（1〜50、既定 10）。接近時刻の早い順。
        hazardous_only: True なら「潜在的に危険な小惑星(PHA)」だけに絞ります。

    既知の直径がある天体はそれを、無い天体は絶対等級 H から**推定**した直径
    （アルベド0.14 仮定）を返します。接近時刻の不確かさ（3σ）も返します。

    出典: NASA/JPL CNEOS SBDB Close-Approach Data API（ssd-api.jpl.nasa.gov）
    """
    limit = as_int(limit, 10, 1, 50) or 10
    days = as_int(days, 7, 1, 365) or 7
    dist = as_float(max_distance_au, 0.05, 0.0001, 5.0)
    if dist is None:
        dist = 0.05

    params = {"date-min": "now", "date-max": "+{}".format(days),
              "dist-max": "{:g}".format(dist), "sort": "date", "limit": limit,
              "diameter": "true", "fullname": "true"}
    if hazardous_only:
        params["pha"] = "true"

    try:
        payload = _get_json("cad.api", params)
    except requests.RequestException as e:
        detail = _http_detail(e)
        return _error_result(
            "JPL の接近データを取得できませんでした: {}{}".format(
                str(e)[:150], "（{}）".format(detail) if detail else ""),
            endpoint="cad.api", params=params)
    err = _api_error(payload)
    if err:
        return _error_result("接近データの取得に失敗しました: {}".format(err),
                             endpoint="cad.api", params=params)

    records = _records(payload)
    total = as_int(payload.get("total"), len(records), 0)
    if total is None:
        total = len(records)
    results, lines = [], []
    for rec in records:
        des = str(rec.get("des") or "?").strip()
        km = _au_to_km(rec.get("dist"))
        lunar = _lunar_distances(km)
        dia_km = _num(rec.get("diameter"))
        if dia_km is not None:
            dia_m = dia_km * 1000.0
            dia_text = "既知の直径 約 {:.3g} m".format(dia_m)
        else:
            dia_m = _diameter_from_h(rec.get("h"))
            dia_text = ("推定直径 約 {:.3g} m（H={} から換算・アルベド{} 仮定）".format(
                dia_m, _fmt_num(rec.get("h"), digits=4), ALBEDO_ASSUMED)
                if dia_m is not None else "直径 不明")
        row = "- **{}** ／ {} TDB ／ 地心距離 {} au（{:,.0f} km・{:.2f} 月距離）／ " \
              "相対速度 {} ／ {}".format(
                  des, str(rec.get("cd") or "").strip(), _fmt_num(rec.get("dist"), digits=5),
                  km or 0.0, lunar or 0.0, _fmt_num(rec.get("v_rel"), " km/s"), dia_text)
        if hazardous_only:
            row += "／ ⚠️ 潜在的に危険な小惑星(PHA)"
        row += "／ 接近時刻の不確かさ {}".format(_uncertainty_text(rec.get("t_sigma_f")))
        lines.append(row)
        results.append({
            "designation": des,
            "fullname": str(rec.get("fullname") or "").strip() or None,
            "orbit_id": rec.get("orbit_id"),
            "close_approach": str(rec.get("cd") or "").strip(),
            "distance_au": _num(rec.get("dist")),
            "distance_km": None if km is None else round(km, 1),
            "distance_lunar": None if lunar is None else round(lunar, 3),
            "distance_min_au": _num(rec.get("dist_min")),
            "distance_max_au": _num(rec.get("dist_max")),
            "relative_velocity_km_s": _num(rec.get("v_rel")),
            "v_infinity_km_s": _num(rec.get("v_inf")),
            "uncertainty": _uncertainty_text(rec.get("t_sigma_f")),
            "h": _num(rec.get("h")),
            "diameter_km": dia_km,
            "diameter_sigma_km": _num(rec.get("diameter_sigma")),
            "estimated_diameter_m": None if dia_m is None else round(dia_m, 3),
            "diameter_is_estimate": dia_km is None,
            "url": _sbdb_url(des),
        })

    head = "☄️ **JPL CNEOS 小惑星・彗星の地球接近** {}日以内・{} au 以内: 全{}件中{}件".format(
        days, "{:g}".format(dist), total, len(results))
    notes = [
        "",
        "ℹ️ **接近＝衝突ではありません**。距離は地球中心からの距離（地心距離）で、"
        "1 月距離(LD) = 384,400 km です。",
        "ℹ️ 直径は観測からの**推定**です（既知の直径があればそれを、無ければ絶対等級 H と"
        "アルベド{} の仮定から換算）。接近時刻は TDB（力学時）で表示しています。".format(
            ALBEDO_ASSUMED),
        "出典: NASA/JPL CNEOS（ssd-api.jpl.nasa.gov）／ 各天体の詳細は JPL SBDB で確認できます。",
    ]
    if not results:
        lines.append("この期間・距離に該当する接近はありませんでした"
                     "（`days` か `max_distance_au` を広げてください）。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join([head] + lines + notes))],
        structuredContent={
            "source": "NASA/JPL CNEOS SBDB Close-Approach Data API",
            "days": days, "max_distance_au": dist, "hazardous_only": hazardous_only,
            "total": total, "shown": len(results),
            "truncated": total > len(results), "results": results,
        })


def _risk_notes() -> list:
    """衝突リスクの読み方（数値から生成した注記）。

    確率を「恐怖」として受け取られないよう、算出根拠と変化しやすさを必ず添える
    （教育・学生向けの用途を想定）。
    """
    return [
        "",
        "ℹ️ これは JPL CNEOS Sentry による**衝突確率の推算**です"
        "（進路が確定した衝突予報ではありません）。観測が増えると確率は下がり、"
        "0 になると Sentry から**削除**されます（例: アポフィスは 2021-02-21 に削除）。",
        "ℹ️ パレルモスケール(PS) は「背景リスクと比べて何桁危険か」の指標で、"
        "**負の値は背景より低い**ことを示します。",
        "出典: NASA/JPL CNEOS Sentry（ssd-api.jpl.nasa.gov）",
    ]


@ttl_cache(TTL_HOURLY, maxsize=32, skip_if=is_error_result)
def impact_risk(designation: Optional[str] = None, min_probability: float = 1e-3,
                limit: int = 10) -> CallToolResult:
    """JPL Sentry による「将来の衝突リスク」を返す（認証不要・APIキー不要）。

    小天体が将来の100年規模で地球に衝突する**確率の推算**（CNEOS Sentry）です。
    確率は観測が増えると下がり、0 になれば Sentry から削除されます。数値はあくまで
    推算で、進路が確定した「衝突予報」ではありません。

    例:「衝突確率の高い小惑星」「Sentry に載っている天体」「2024 YR4 の衝突リスク」

    content に表示用サマリ、structuredContent に JSON を返します。

    Args:
        designation: 小惑星の仮符号または番号（例 "2024 YR4"、"2000 SG344"、"99942"）。
            指定すると、その天体の詳細（累積確率・想定時期・仮想衝突(VI)の一覧）を返します。
            **和名・愛称は JPL 側が受け付けません**。推測せず仮符号で指定してください。
        min_probability: 一覧の下限（累積衝突確率 ip。既定 1e-3 = 0.1%）。1e-10〜1。
        limit: 一覧で表示する最大件数（1〜50、既定 10）。確率の高い順。

    出典: NASA/JPL CNEOS Sentry（ssd-api.jpl.nasa.gov）
    """
    limit = as_int(limit, 10, 1, 50) or 10
    ip_min = as_float(min_probability, 1e-3, 1e-10, 1.0)
    if ip_min is None:
        ip_min = 1e-3
    des = " ".join(str(designation).split()) if designation is not None else ""

    params = {"des": des} if des else {"ip-min": "{:g}".format(ip_min)}
    try:
        payload = _get_json("sentry.api", params)
    except requests.RequestException as e:
        detail = _http_detail(e)
        status = getattr(getattr(e, "response", None), "status_code", None)
        if des and status == 400:
            return _error_result(
                "「{}」は JPL Sentry が受け付ける指定ではありません（{}）。"
                "**小惑星の仮符号か番号**で指定してください"
                "（例: \"2024 YR4\" / \"2000 SG344\" / \"99942\"）。"
                "和名・愛称（例「アポフィス」）は受け付けません。".format(
                    des, detail or "invalid designation"),
                endpoint="sentry.api", params=params, designation=des)
        return _error_result(
            "JPL Sentry の衝突リスクを取得できませんでした: {}{}".format(
                str(e)[:150], "（{}）".format(detail) if detail else ""),
            endpoint="sentry.api", params=params)
    err = _api_error(payload)
    if err:
        return _error_result(
            "Sentry に「{}」のデータがありません: {}。"
            "確率が 0 になった天体は Sentry から削除されるため、"
            "「リスクなし（監視対象から外れた）」を意味することがあります。".format(des, err),
            endpoint="sentry.api", params=params, designation=des, not_found=True)

    if des:
        return _sentry_object_result(des, payload)

    rows = sorted(_records(payload),
                  key=lambda r: _num(r.get("ip")) or 0.0, reverse=True)
    shown_rows = rows[:limit]
    results, lines = [], []
    for rec in shown_rows:
        ip = _num(rec.get("ip"))
        dia_km = _num(rec.get("diameter"))
        lines.append("- **{}** ／ 累積衝突確率 {} ／ 推定直径 {} ／ 想定時期 {} ／ "
                     "想定衝突数 {} ／ PS(累積) {}".format(
                         str(rec.get("des") or "?").strip(), _fmt_prob(ip),
                         _diameter_text(dia_km), rec.get("range") or "不明",
                         _fmt_num(rec.get("n_imp"), digits=0), rec.get("ps_cum") or "不明"))
        results.append({
            "designation": rec.get("des"), "fullname": rec.get("fullname"),
            "impact_probability": ip,
            "palermo_scale_cumulative": _num(rec.get("ps_cum")),
            "palermo_scale_max": _num(rec.get("ps_max")),
            "estimated_diameter_km": dia_km, "h": _num(rec.get("h")),
            "expected_impacts": as_int(rec.get("n_imp"), None, 0),
            "range": rec.get("range"), "last_obs": rec.get("last_obs"),
            "url": _sbdb_url(rec.get("des")),
        })
    top_ip = results[0]["impact_probability"] if results else None
    head = "☄️ **JPL Sentry 衝突リスク（確率の高い順）** 累積確率 {:g} 以上: 全{}件中{}件".format(
        ip_min, len(rows), len(results))
    if top_ip:
        head += "（最高 {:.4g}%＝約 {:,.0f} 回に1回）".format(top_ip * 100.0, 1.0 / top_ip)
    if not results:
        lines.append("この確率（{:g}）以上の天体はありません"
                     "（＝現在の監視対象に大きなリスクは見つかっていません）。".format(ip_min))
    lines += _risk_notes()
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join([head] + lines))],
        structuredContent={
            "source": "NASA/JPL CNEOS Sentry", "mode": "summary",
            "min_probability": ip_min, "total": len(rows), "shown": len(results),
            "truncated": len(rows) > len(results),
            "highest_probability": top_ip, "results": results,
        })


def _sentry_object_result(des: str, payload: dict) -> CallToolResult:
    """Sentry の object モード（designation 指定）の応答を組み立てる。"""
    summary = payload.get("summary") or {}
    vis = _records(payload)
    ip = _num(summary.get("ip"))
    dia_km = _num(summary.get("diameter"))
    energy_kt = _num(summary.get("energy"))
    lines = [
        "☄️ **JPL Sentry 衝突リスク詳細: {}**".format(
            str(summary.get("fullname") or summary.get("des") or des).strip()),
        "- 累積衝突確率(ip) {} ／ 想定衝突 {} 件（仮想衝突 {} 件を計算）".format(
            _fmt_prob(ip), _fmt_num(summary.get("n_imp"), digits=0), len(vis)),
        "- 推定直径 {} ／ 観測弧 {} ／ 初観測 {} ／ 最終観測 {} ／ 解析手法 {}".format(
            _diameter_text(dia_km), summary.get("darc") or "不明",
            summary.get("first_obs") or "不明", summary.get("last_obs") or "不明",
            summary.get("method") or "不明"),
        "- パレルモスケール PS(累積) {} ／ PS(最大) {}".format(
            summary.get("ps_cum") or "不明", summary.get("ps_max") or "不明"),
        "- 衝突エネルギー {}".format(
            _fmt_num(energy_kt, " kt") + _energy_compare(energy_kt)
            if energy_kt is not None else "不明"),
    ]
    for vi in vis[:5]:
        lines.append("- 仮想衝突: {} ／ 確率 {} ／ PS {} ／ エネルギー {}{}".format(
            vi.get("date") or "不明", _fmt_prob(_num(vi.get("ip"))),
            vi.get("ps") or "不明", _fmt_num(vi.get("energy"), " kt"),
            _energy_compare(vi.get("energy"))))
    if len(vis) > 5:
        lines.append("- …ほか {} 件の仮想衝突（`structuredContent.results` に全件）".format(
            len(vis) - 5))
    lines += _risk_notes()
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "source": "NASA/JPL CNEOS Sentry", "mode": "object", "designation": des,
            "summary": summary, "virtual_impactors": len(vis),
            "shown": min(len(vis), 5), "results": vis,
        })

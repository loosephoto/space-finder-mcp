"""CelesTrak — 全世界の人工衛星・デブリの軌道要素（TLE）API（認証不要）。

NORAD カタログ上の全ての衛星の Two-Line Element（軌道要素）を JSON で返す。
出典: celestrak.org/NORAD/elements/gp.php。name/group 検索可能。
TLE は位置計算・可視パス予測の基礎データ。

CelesTrak が IP 単位で遮断（403）／接続不可のときは、認証不要の公開ミラー
（tle.ivanstanojevic.me / db.satnogs.org）へ自動で切り替える。どちらから取ったかは
fetch_tle_ex の出典で返すので、利用側は「CelesTrak の TLE」と誤って書かないこと。
グループ検索（GROUP=）は代替源に無いため CelesTrak 専用。
"""
from __future__ import annotations

from typing import Optional

import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from functools import lru_cache
from mcp.types import CallToolResult, TextContent
from .input_utils import as_int
from .name_common import expand_terms

BASE = "https://celestrak.org/NORAD/elements/gp.php"
UA = {"User-Agent": "space-finder-mcp/0.3 (MCP; CelesTrak TLE)"}

# よく使う衛星の NORAD カタログ番号（satellite_map と共用。定義はここ1箇所）
WELL_KNOWN: dict[str, int] = {
    "iss": 25544, "hubble": 20580, "himawari-8": 40267, "himawari-9": 41836,
    "landsat-8": 39084, "landsat-9": 49260, "noaa-20": 43013, "noaa-21": 54234,
    "meteor-m2": 40069, "goes-16": 41866, "goes-17": 41868, "goes-18": 51850,
    "tiangong": 48274, "sentinel-2a": 40697, "sentinel-2b": 42063, "sentinel-1a": 39634,
    "kepu": 44414, "hinode": 29479, "aqua": 27424, "terra": 25994, "suomi-npp": 37849,
    # 以下は CelesTrak GROUP=active（16,563 機）の OBJECT_NAME と突合して確認した現役機
    # （name_common.JA_ALIASES の英語名と同じ綴りで引けるようにしてある）
    "himawari-8": 40267, "himawari-9": 41836,
    "alos-2": 39766, "alos-4": 60182, "gosat": 33492, "gosat-2": 43672,
    "gcom-w1": 38337, "gcom-c1": 43065, "ajisai": 16908,
    "arase": 41896, "reimei": 28810, "hisaki": 39253,
    "tianhe": 48274, "wentian": 53239, "mengtian": 54216,
}


# CelesTrak が IP 単位で遮断している間（403）や、接続が blackhole された（TCP が
# 返ってこない）間は、**毎回 HTTP を投げに行かず**その場で失敗させる。遮断中に投げると
# 1回の呼び出しが分単位で固まり、LLM のツール呼び出し（並列に走っている他のツールも
# 含む）が待たされる（実測: CelesTrak が blackhole のとき connect が返らず 120 秒以上
# 無応答。nasa_budget と同じ「投げる前に止める」方針）。
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0
_COOLDOWN_LAST = ""
_COOLDOWN_SECONDS_BLOCKED = 300.0     # 403（IP 単位の遮断）: 数分待つ
_COOLDOWN_SECONDS_NET = 60.0          # 接続不可・タイムアウト: 短めに再試行
CONNECT_TIMEOUT = (10, 25)            # (connect, read) 秒。connect を短くして固まりを防ぐ


def blocked_status() -> tuple:
    """遮断中なら (残り秒, 理由)、そうでなければ (0.0, "")。"""
    with _COOLDOWN_LOCK:
        return (max(0.0, _COOLDOWN_UNTIL - time.time()), _COOLDOWN_LAST)


def _note_blocked(seconds: float, reason: str) -> None:
    global _COOLDOWN_UNTIL, _COOLDOWN_LAST
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + seconds)
        _COOLDOWN_LAST = reason


def _blocked_message(remaining: float, reason: str) -> str:
    return ("CelesTrak へのアクセスが一時的に遮断されています（{}）。"
            "約 {:.0f} 分後に再試行してください（短時間に多数のリクエストを送ると"
            " IP 単位で遮断されます）。".format(reason or "接続不可", max(1.0, remaining) / 60.0))


def _get(params: dict, timeout=None) -> requests.Response:
    """CelesTrak へ GET する。403・接続不可は「一時的な遮断」として案内文付きに正規化する。

    短時間に多数のリクエストを送ると CelesTrak は **IP 単位で 403 Forbidden** を返す
    （UA を変えても解除されない。実測 2026-09: curl/Mozilla UA でも 403）。生の
    "403 Client Error" では原因が分からないため、待てば直ることを明示する。
    遮断中は HTTP を投げずに即座に同じ案内を返す（fail fast）。
    """
    remaining, reason = blocked_status()
    if remaining > 0:
        raise requests.ConnectionError(_blocked_message(remaining, reason))
    try:
        r = requests.get(BASE, headers=UA, params=params,
                         timeout=timeout or CONNECT_TIMEOUT)
    except (requests.ConnectionError, requests.Timeout) as e:
        _note_blocked(_COOLDOWN_SECONDS_NET, "接続できません（遮断またはネットワーク断）")
        raise requests.ConnectionError(
            "CelesTrak に接続できませんでした（一時的な遮断またはネットワーク断）。"
            "約 {:.0f} 分後に再試行してください。".format(_COOLDOWN_SECONDS_NET / 60.0)) from e
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            _note_blocked(_COOLDOWN_SECONDS_BLOCKED, "403 Forbidden（IP 単位の遮断）")
            raise requests.HTTPError(
                "アクセスが一時的に拒否されました（403 Forbidden）。"
                "短時間に多数のリクエストを送ると CelesTrak 側で IP 単位に遮断されます"
                "（数分〜しばらく待つと解除されます）。",
                response=getattr(e, "response", None)) from e
        raise
    return r


# ---------------------------------------------------------------------------
# 代替 TLE 源（CelesTrak 遮断時のフォールバック）
#
# CelesTrak は IP 単位で 403 を返す／TCP を blackhole する（実測 2026-09: gp.php が
# 20 秒以上無応答で curl も返らない）ことがあり、その間は位置系ツールが全滅する。
# TLE は NORAD カタログ番号で引く「同じ衛星の軌道要素」なので、遮断中は認証不要の
# 公開ミラーから取る。
#   1. tle.ivanstanojevic.me — TLE API（JSON。NORAD ID と名前検索に対応）
#   2. db.satnogs.org        — SatNOGS API（JSON。NORAD ID に対応）
# 遮断の記憶はホスト単位で持つ（CelesTrak 用の _COOLDOWN_* とは別。片方が落ちても
# もう片方は試せるようにするため）。グループ検索（GROUP=）は代替源に無い。
# ---------------------------------------------------------------------------
CELESTRAK_SOURCE = "celestrak.org"
FALLBACK_HOSTS = ("tle.ivanstanojevic.me", "db.satnogs.org")
TLE_SOURCE_ORDER = (CELESTRAK_SOURCE,) + FALLBACK_HOSTS
FALLBACK_CACHE_MAXSIZE = 256
FALLBACK_TIMEOUT = (6, 20)            # (connect, read) 秒。connect を短くして固まりを防ぐ
FALLBACK_COOLDOWN_NET = 60.0          # 接続不可・タイムアウト: 短めに再試行
FALLBACK_COOLDOWN_BLOCKED = 300.0     # 403/429（IP 単位の遮断）: 数分待つ
FALLBACK_UA = {"User-Agent": "space-finder-mcp/0.37 (MCP; TLE fallback)"}
FALLBACK_STALE_DAYS = 30   # これより古いエポックは「古い TLE」として注記する

_FALLBACK_LOCK = threading.Lock()
_FALLBACK_DOWN_UNTIL: dict = {}        # host -> 遮断解除の時刻
_FALLBACK_DOWN_REASON: dict = {}
_FALLBACK_CACHE: dict = {}             # ("catnr", id) / ("name", 名前, limit) -> 取得結果
# キャッシュはセッション内有効のまま（CelesTrak 経路の lru_cache と同じ粒度。TLE 自体が
# 数時間有効な軌道要素なので、位置計算は常に「そのときの最新 TLE」で行われる）。


def _store_fallback(key: tuple, value) -> None:
    """フォールバック結果を上限付きキャッシュに格納する。"""
    with _FALLBACK_LOCK:
        _FALLBACK_CACHE[key] = value
        while len(_FALLBACK_CACHE) > FALLBACK_CACHE_MAXSIZE:
            del _FALLBACK_CACHE[next(iter(_FALLBACK_CACHE))]


def _fallback_remaining(host: str) -> float:
    with _FALLBACK_LOCK:
        return max(0.0, _FALLBACK_DOWN_UNTIL.get(host, 0.0) - time.time())


def _note_fallback_down(host: str, seconds: float, reason: str) -> None:
    with _FALLBACK_LOCK:
        _FALLBACK_DOWN_UNTIL[host] = max(_FALLBACK_DOWN_UNTIL.get(host, 0.0), time.time() + seconds)
        _FALLBACK_DOWN_REASON[host] = reason


def fallback_status() -> dict:
    """代替源ごとの遮断状況（残り秒・理由）。診断とエラー応答のために返す。"""
    with _FALLBACK_LOCK:
        now = time.time()
        return {h: {"remaining_s": round(max(0.0, t - now), 1),
                    "reason": _FALLBACK_DOWN_REASON.get(h, "")}
                for h, t in sorted(_FALLBACK_DOWN_UNTIL.items()) if t > now}


def source_label(source: str) -> str:
    """出典の表示名。代替源は「代替源」と分かる形にする（CelesTrak と誤認させない）。"""
    if not source:
        return "出典不明"
    if source == CELESTRAK_SOURCE:
        return "CelesTrak（celestrak.org）"
    return "代替源 {}".format(source)


def _get_json(url: str, host: str, params: Optional[dict] = None) -> object:
    """代替源へ GET して JSON を返す。404 は None（該当なし＝遮断ではない）。

    遮断（403/429）と接続不可はホスト単位で記憶し、以降は HTTP を出さずに即失敗する
    （規約14: 遮断は fail fast。黒穴ホストへ投げると並列で走る他のツールまで待たされる）。
    """
    remaining = _fallback_remaining(host)
    if remaining > 0:
        raise requests.ConnectionError(
            "{} は一時的に遮断中です（約 {:.0f} 秒後に再試行）".format(host, remaining))
    try:
        r = requests.get(url, headers=FALLBACK_UA, params=params, timeout=FALLBACK_TIMEOUT)
    except (requests.ConnectionError, requests.Timeout) as e:
        _note_fallback_down(host, FALLBACK_COOLDOWN_NET, "接続できません")
        raise requests.ConnectionError(
            "{} に接続できませんでした（一時的な遮断またはネットワーク断）".format(host)) from e
    if r.status_code == 404:
        return None
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (403, 429):
            _note_fallback_down(host, FALLBACK_COOLDOWN_BLOCKED,
                                "HTTP {}（レート制限・遮断）".format(status))
        raise
    try:
        return r.json()
    except ValueError as e:
        raise requests.RequestException(
            "{} の応答が JSON ではありません".format(host)) from e


def tle_epoch_iso(line1: str) -> str:
    """TLE 1行目のエポック(YYDDD.DDDDDDDD)を ISO8601(UTC) 文字列に変換する。

    tiangong と代替源経路で共用する（同じ変換を2箇所に書かない）。
    """
    try:
        raw = line1[18:32].strip()
        yy, doy = int(raw[:2]), float(raw[2:])
        year = 2000 + yy if yy < 57 else 1900 + yy
        dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError, TypeError):
        return ""


def looks_like_tle(line1: str, line2: str) -> bool:
    """TLE 2行らしいか（行頭の "1 "/"2 " と NORAD の固定長）。代替源の応答検証に使う。"""
    return (isinstance(line1, str) and isinstance(line2, str)
            and line1.startswith("1 ") and line2.startswith("2 ")
            and len(line1.strip()) >= 60 and len(line2.strip()) >= 60)


def tle_elements(line1: str, line2: str) -> dict:
    """TLE 2行から sat_tle と同じ形の軌道要素を取り出す（列位置は NORAD GP 固定長）。

    代替源は要素の JSON を返さないため TLE 本体から切り出す。取れない項目は None。
    """
    def cell(ln: str, a: int, b: int) -> Optional[str]:
        txt = (ln[a:b] or "").strip()
        return txt or None

    ecc = cell(line2, 26, 33)
    return {
        "object_name": None,
        "norad_id": cell(line1, 2, 7),
        "intl_designator": cell(line1, 9, 17),
        "epoch": tle_epoch_iso(line1),
        "inclination_deg": cell(line2, 8, 16),
        "ra_of_asc_node_deg": cell(line2, 17, 25),
        "eccentricity": ("0." + ecc) if ecc else None,
        "arg_perigee_deg": cell(line2, 34, 42),
        "mean_anomaly_deg": cell(line2, 43, 51),
        "mean_motion_rev_day": cell(line2, 52, 63),
    }


def _fallback_catnr_one(host: str, norad_id: int) -> Optional[tuple]:
    """1つの代替源から (name, line1, line2, host) を取る（該当なしは None）。"""
    if host == "tle.ivanstanojevic.me":
        j = _get_json("https://tle.ivanstanojevic.me/api/tle/{}".format(int(norad_id)), host)
        if isinstance(j, dict) and looks_like_tle(j.get("line1"), j.get("line2")):
            nm = str(j.get("name") or "NORAD {}".format(norad_id)).strip()
            return (nm, j["line1"].strip(), j["line2"].strip(), host)
        return None
    if host == "db.satnogs.org":
        j = _get_json("https://db.satnogs.org/api/tle/", host,
                      params={"norad_cat_id": int(norad_id), "format": "json"})
        for d in (j if isinstance(j, list) else []):
            if not isinstance(d, dict):
                continue
            l1, l2 = (d.get("tle1") or "").strip(), (d.get("tle2") or "").strip()
            if looks_like_tle(l1, l2):
                nm = (d.get("tle0") or "").lstrip("0 ").strip() or "NORAD {}".format(norad_id)
                return (nm, l1, l2, host)
        return None
    return None


def _fallback_catnr(norad_id: int) -> Optional[tuple]:
    """NORAD ID で代替源から (name, line1, line2, host)。該当なしは None。

    全源が接続不可なら最後の接続例外を送出する（呼び出し側のエラー文言に残す）。
    """
    nid = as_int(norad_id)
    if nid is None:
        return None
    key = ("catnr", nid)
    with _FALLBACK_LOCK:
        if key in _FALLBACK_CACHE:
            return _FALLBACK_CACHE[key] or None
    found, last_err = None, None
    for host in FALLBACK_HOSTS:
        try:
            got = _fallback_catnr_one(host, nid)
        except requests.RequestException as e:
            last_err = e
            continue
        if got:
            found = got
            break
    if found is None and last_err is not None:
        raise last_err
    if found is not None:
        _store_fallback(key, found)
    return found


def _fallback_name(name: str, limit: int = 10) -> list:
    """名前で代替源を検索し [(name, line1, line2, host)] を返す（0件は空リスト）。

    名前検索に対応するのは TLE API のみ（SatNOGS は NORAD ID 検索だけ）。
    """
    nm = str(name).strip()
    if not nm:
        return []
    key = ("name", nm.lower(), int(limit))
    with _FALLBACK_LOCK:
        if key in _FALLBACK_CACHE:
            return list(_FALLBACK_CACHE[key])
    host = "tle.ivanstanojevic.me"
    if _fallback_remaining(host) > 0:
        # クールダウン中の空結果は「該当なし」ではなく「未検索」。復旧後に再試行できるよう
        # 負の結果としてキャッシュしない。
        return []
    out: list = []
    j = _get_json("https://tle.ivanstanojevic.me/api/tle", host,
                  params={"search": nm, "page_size": max(1, min(int(limit), 20))})
    members = j.get("member") if isinstance(j, dict) else None
    for m in members or []:
        if not isinstance(m, dict):
            continue
        l1, l2 = (m.get("line1") or "").strip(), (m.get("line2") or "").strip()
        if looks_like_tle(l1, l2):
            out.append((str(m.get("name") or "").strip(), l1, l2, host))
    if not out:
        return []
    _store_fallback(key, tuple(out))
    return out


def _fallback_fetch_ex(norad_id: Optional[int], name: Optional[str]) -> tuple:
    """代替源から ((name, line1, line2) または None, 出典)。該当なしは (None, "")。"""
    if norad_id is not None:
        got = _fallback_catnr(as_int(norad_id))
        if got:
            return ((got[0], got[1], got[2]), got[3])
        return (None, "")
    rows = _fallback_name(str(name), limit=10)
    if rows:
        return ((rows[0][0], rows[0][1], rows[0][2]), rows[0][3])
    return (None, "")


@lru_cache(maxsize=64)
def _fetch_tle_cached(params_tuple: tuple) -> tuple:
    """TLE を取得（同一セッション内で同じ問い合わせはキャッシュ）。TLE は数時間有効。

    CelesTrak は該当なし・制限時に **リスト以外の JSON**（例 {"Error": ...}）を返すことが
    ある。tuple(dict) はキー列（文字列のタプル）になり、利用側で
    `'str' object has no attribute 'get'` として **例外がツール外へ漏れる**（実測 2026-09）。
    リスト以外・辞書以外の要素は「該当なし」に正規化する（規約1: 例外を漏らさない）。
    """
    p = dict(params_tuple)
    p["FORMAT"] = "JSON"
    data = _get(p).json()
    if not isinstance(data, list):
        return ()
    return tuple(d for d in data if isinstance(d, dict))


def _fetch_tle(params: dict) -> list[dict]:
    # params(dict) をタプル化してキャッシュキーに。結果は tuple→list に戻す。
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    return list(_fetch_tle_cached(key))


@lru_cache(maxsize=128)
def _tle_text_cached(params_tuple: tuple) -> tuple:
    """生 TLE テキスト行を取得（FORMAT=TLE。JSON は TLE 2行を返さないため）。"""
    p = dict(params_tuple)
    p["FORMAT"] = "TLE"
    return tuple(_get(p).text.splitlines())


def fetch_tle_text(**params) -> list[str]:
    """CelesTrak から生 TLE テキスト行を取得する（CATNR / NAME / GROUP など）。

    セッション内キャッシュ付き（TLE は数時間有効）。requests の例外は送出する。
    """
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    return list(_tle_text_cached(key))


def fetch_tle_ex(norad_id: Optional[int] = None,
                 name: Optional[str] = None) -> tuple:
    """TLE と出典を返す: ((name, line1, line2) または None, 出典文字列 または "")。

    CelesTrak → 遮断・接続不可なら代替源（tle.ivanstanojevic.me → db.satnogs.org）の順に
    試す。出典は celestrak.org か代替源のホスト名で、これを使って content /
    structuredContent に**どちらから取ったかを明示**する（代替源の TLE を「CelesTrak の
    TLE」と書くと出典が嘘になる）。該当なしは (None, "")。
    requests の例外は送出する（CelesTrak と代替源の両方が駄目なときだけ）。
    """
    if norad_id is None and not name:
        return (None, "")
    params = {"CATNR": norad_id} if norad_id is not None else {"NAME": name}
    try:
        lines = [ln.strip() for ln in fetch_tle_text(**params)]
    except requests.HTTPError as e:
        # 該当なしのとき CelesTrak は 404 を返す。404 以外（403 遮断・5xx）は代替源へ
        if getattr(getattr(e, "response", None), "status_code", None) != 404:
            return _fallback_fetch_ex(norad_id, name)
        lines = []
    except requests.RequestException:
        # 遮断（403）・接続不可（blackhole）→ 代替源へ切り替える
        return _fallback_fetch_ex(norad_id, name)
    pair = [ln for ln in lines if ln.startswith(("1 ", "2 "))]
    if len(pair) < 2:
        # CelesTrak は応答したが該当 TLE が無い → 代替源でも探す
        return _fallback_fetch_ex(norad_id, name)
    return ((lines[0].strip() if lines else ""), pair[0], pair[1]), CELESTRAK_SOURCE


def fetch_tle(norad_id: Optional[int] = None,
              name: Optional[str] = None) -> Optional[tuple]:
    """NORAD ID または名前から (name, line1, line2) を返す。該当なしは None。

    satellite_map / sky_overlay / tiangong が各自持っていた TLE 取得の共通版。
    出典も必要なときは fetch_tle_ex を使う（content / structuredContent にどちらから
    取ったかを出すため）。requests の例外はそのまま送出する。
    """
    return fetch_tle_ex(norad_id=norad_id, name=name)[0]


def _fetch_tle_lines(params: dict) -> dict:
    """{NORAD_CAT_ID: (line1, line2)} を返す。

    CelesTrak の FORMAT=JSON は軌道要素のみで TLE_LINE1/TLE_LINE2 を返さないため、
    生 TLE は FORMAT=TLE で別途取得して突き合わせる。取得失敗時は空 dict（呼び出し側で空文字）。
    """
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    out: dict = {}
    try:
        lines = list(_tle_text_cached(key))
    except requests.RequestException:
        return out
    line1 = None
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("1 ") and len(ln) >= 60:
            line1 = ln
        elif ln.startswith("2 ") and line1 is not None:
            try:
                out[int(ln[2:7])] = (line1, ln)
            except ValueError:
                pass
            line1 = None
    return out


# ---- 代替源からの sat_tle 用レコード（CelesTrak 遮断時） ----

def _fallback_note(host: str, reason: str = "unavailable") -> str:
    if reason == "not_found":
        lead = "CelesTrakは応答しましたが該当データが見つからず、代替源へ切り替えました。"
    else:
        lead = "CelesTrakから取得できなかったため、代替源へ切り替えました。"
    return (lead + "出典: {}。同じNORAD IDの衛星ですが、TLEの軌道要素はエポックや更新時点で変わります。"
            "位置計算では epoch を確認してください。".format(host))


def _record_from_tle(name: str, line1: str, line2: str) -> dict:
    """TLE 2行から sat_tle のレコード（CelesTrak 経路と同じ形）を組み立てる。"""
    rec = tle_elements(line1, line2)
    rec["object_name"] = name or rec.get("object_name")
    rec["tle"] = "{}\n{}".format(line1, line2)
    return rec


def stale_epoch_note(records: list) -> str:
    """代替TLEの古いエポックを数値から示す（該当しなければ空文字列）。"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=FALLBACK_STALE_DAYS)
    stale, oldest = 0, None
    for record in records:
        try:
            dt = datetime.strptime(str(record.get("epoch")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if oldest is None or dt < oldest:
            oldest = dt
        if dt < cutoff:
            stale += 1
    if not stale or oldest is None:
        return ""
    return ("代替源のTLEに古いエポックが含まれます（{} 件中 {} 件が {} 日より古く、最古は {}）。"
            "位置計算に使う前に epoch を確認してください。"
            .format(len(records), stale, FALLBACK_STALE_DAYS, oldest.strftime("%Y-%m-%d")))


def _fallback_sat_tle(params: dict, limit: int, reason: str = "unavailable") -> Optional[tuple]:
    """CelesTrakの取得が不調なとき代替源から (records, 出典, 注記) を返す。"""
    try:
        catnr = params.get("CATNR")
        if catnr is not None:
            got = _fallback_catnr(catnr)
            if not got:
                return None
            nm, line1, line2, host = got
            records = [_record_from_tle(nm, line1, line2)]
            note = _fallback_note(host, reason)
            stale = stale_epoch_note(records)
            if stale:
                note += " " + stale
            return (records, host, note)
        name = params.get("NAME")
        if name:
            rows = _fallback_name(str(name), limit)
            if not rows:
                return None
            records = [_record_from_tle(row[0], row[1], row[2]) for row in rows[:limit]]
            note = _fallback_note(rows[0][3], reason)
            stale = stale_epoch_note(records)
            if stale:
                note += " " + stale
            return (records, rows[0][3], note)
    except requests.RequestException:
        return None
    return None


def _sat_tle_result(records: list, query: dict, source: str, note: str = "") -> CallToolResult:
    """sat_tle の content / structuredContent を組み立てる（CelesTrak 経路と共通）。"""
    lines = ["{}{} 衛星軌道要素（{} 件）:".format(
        "⚠️ " if note else "", source_label(source), len(records))]
    if note:
        lines.append(note)
    for i, r in enumerate(records, 1):
        lines.append("{}. **{}** (NORAD {})".format(i, r.get("object_name"), r.get("norad_id")))
        lines.append("   軌道: 傾角 {}°・離心率 {}・周回 {}/日".format(
            r.get("inclination_deg"), r.get("eccentricity"), r.get("mean_motion_rev_day")))
        lines.append("   エポック: {} UTC".format(r.get("epoch")))
    lines.append("出典: {}（NORAD GP カタログ）／ TLE は軌道計算・可視パス予測の基礎データ。"
                 .format(source_label(source)))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": query, "source": source, "note": note,
                           "shown": len(records), "results": records},
    )


def _sat_tle_unavailable(e: Exception, params: dict, query: dict) -> CallToolResult:
    """CelesTrakと代替源の両方が使えないときの応答（例外は漏らさない）。"""
    group = params.get("GROUP")
    why = ("グループ検索（GROUP={}）は代替源に無いため、CelesTrakからの応答が必要です。"
           .format(group) if group else
           "代替源（{}）からも取得できませんでした。".format("、".join(FALLBACK_HOSTS)))
    return CallToolResult(
        content=[TextContent(type="text",
                             text="TLEの取得に失敗しました（CelesTrak／公開代替源）: {}\n{}".format(e, why))],
        structuredContent={"error": str(e), "query": query,
                           "sources_tried": list(TLE_SOURCE_ORDER),
                           "fallback": fallback_status()},
    )


def sat_tle(name: Optional[str] = None, norad_id: Optional[int] = None,
            group: Optional[str] = None, limit: int = 5) -> CallToolResult:
    """任意の人工衛星（ISS・ハッブル・気象衛星・中国宇宙ステーション等）の軌道要素(TLE)を返す。

    例:「ISSの軌道要素」「ハッブル宇宙望遠鏡のTLE」「気象衛星の軌道」
    認証不要。CelesTrak（NORADカタログ）から取得。**CelesTrak が遮断中（403・接続不可）の
    ときは認証不要の公開ミラー（tle.ivanstanojevic.me → db.satnogs.org）へ自動で切り替え**、
    実際の出典を structuredContent.source / 本文の「出典:」行に出します（`group=` の
    グループ検索は代替源に無いため、遮断中はその旨を案内します）。
    content に表示用サマリ、structuredContent に JSON（軌道パラメータ）を返す。

    Args:
        name: 衛星名または省略名（例 "iss", "hubble", "tiangong", "goes-18"）。
            **和名は英語名/既知名へ自動解決**する（"ひので"→29479, "ひまわり9号"→41836,
            "宇宙ステーション"→iss）。解決できない場合は既知の名前を提示して停止する。
        norad_id: NORAD カタログ番号（例 25544=ISS）。name より優先。
        group: CelesTrak の衛星グループ（例 "stations", "weather", "amateur", "science"）。
        limit: 返す件数（既定 5、最大 20）。
    """
    limit = as_int(limit, 5, 1, 20)
    params: dict = {}
    terms: list = []      # 和名→英語名の展開結果（404 案内で参照するため先に初期化）
    if norad_id:
        params["CATNR"] = norad_id
    elif name:
        nm = name.strip().lower()
        # 和名（ひので / ひまわり9号 等）は英語名へ展開してから照合する
        terms = [t.lower() for t in expand_terms(name)]
        # 既知の衛星名をNORAD IDに解決（完全一致を最優先）
        exact = next((WELL_KNOWN[t] for t in terms if t in WELL_KNOWN), None)
        if exact is not None:
            params["CATNR"] = exact
        else:
            # 部分一致は短い名前の誤マッチを避けるため、長い入力のみ許可
            cands = [(key, nid) for key, nid in WELL_KNOWN.items()
                     if any(len(t) >= 4 and (key in t or t in key) for t in terms)]
            uniq = {nid for _, nid in cands}
            if len(uniq) == 1:
                params["CATNR"] = uniq.pop()
            elif len(uniq) > 1:
                # 曖昧な名前は先頭候補に黙って確定させず、候補を提示して止める
                return CallToolResult(
                    content=[TextContent(type="text", text=f"衛星名 '{name}' は候補が複数あります: "
                                         + ", ".join(f"{k} (NORAD {v})" for k, v in cands)
                                         + "。NORAD ID か、より具体的な名前を指定してください。")],
                    structuredContent={"error": "ambiguous satellite name", "name": name,
                                       "candidates": [{"name": k, "norad_id": v} for k, v in cands]},
                )
            else:
                # 解決できない名前は、和名の英語名があればそれで CelesTrak の名前検索へ
                params["NAME"] = terms[1] if len(terms) > 1 else name.strip()
    elif group:
        params["GROUP"] = group
    else:
        params["GROUP"] = "stations"  # 既定: 有人宇宙関連
    query = {"name": name, "norad_id": norad_id, "group": group}
    try:
        rows = _fetch_tle(params)
    except requests.HTTPError as e:
        # 該当なしのとき CelesTrak は 404 を返す（接続障害と区別して案内する）
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            # 該当なしでも代替源に有ることがある（CelesTrak の名前検索は取りこぼす）
            fb = (_fallback_sat_tle(params, limit, reason="not_found")
                  if (params.get("CATNR") or params.get("NAME")) else None)
            if fb is not None:
                records, source, note = fb
                return _sat_tle_result(records, query, source, note)
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "指定した衛星の軌道要素が見つかりませんでした。\n"
                    + ("和名→英語名の展開: " + ", ".join(terms) + "\n" if len(terms) > 1 else "")
                    + "既知の名前: " + ", ".join(sorted(WELL_KNOWN))
                    + "\nNORAD ID（例 25544=ISS）か、上記の別名をお試しください。"))],
                structuredContent={"error": "not found", "query": query,
                                   "total": 0, "results": []},
            )
        # 403（遮断）・5xx・接続不可 → 代替源へ切り替える
        fb = _fallback_sat_tle(params, limit)
        if fb is None:
            return _sat_tle_unavailable(e, params, query)
        records, source, note = fb
        return _sat_tle_result(records, query, source, note)
    except requests.RequestException as e:
        fb = _fallback_sat_tle(params, limit)
        if fb is None:
            return _sat_tle_unavailable(e, params, query)
        records, source, note = fb
        return _sat_tle_result(records, query, source, note)
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="指定した衛星の軌道要素が見つかりませんでした。NORAD ID や別名をお試しください。")],
            structuredContent={"query": query, "total": 0, "results": []},
        )
    rows = rows[:limit]
    tle_lines = _fetch_tle_lines(params)
    records = []
    for r in rows:
        try:
            pair = tle_lines.get(int(r.get("NORAD_CAT_ID")))
        except (TypeError, ValueError):
            pair = None
        records.append({
            "object_name": r.get("OBJECT_NAME"),
            "norad_id": r.get("NORAD_CAT_ID"),
            "intl_designator": r.get("OBJECT_ID"),
            "epoch": (r.get("EPOCH") or "")[:19],
            "inclination_deg": r.get("INCLINATION"),
            "ra_of_asc_node_deg": r.get("RA_OF_ASC_NODE"),
            "eccentricity": r.get("ECCENTRICITY"),
            "arg_perigee_deg": r.get("ARG_OF_PERICENTER"),
            "mean_anomaly_deg": r.get("MEAN_ANOMALY"),
            "mean_motion_rev_day": r.get("MEAN_MOTION"),
            "tle": "{}\n{}".format(pair[0], pair[1]) if pair else "",
        })
    return _sat_tle_result(records, query, CELESTRAK_SOURCE)

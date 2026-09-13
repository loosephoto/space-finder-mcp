"""CelesTrak — 全世界の人工衛星・デブリの軌道要素（TLE）API（認証不要）。

NORAD カタログ上の全ての衛星の Two-Line Element（軌道要素）を JSON で返す。
出典: celestrak.org/NORAD/elements/gp.php。name/group 検索可能。
TLE は位置計算・可視パス予測の基礎データ。
"""
from __future__ import annotations

from typing import Optional

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


def _get(params: dict, timeout: int = 30) -> requests.Response:
    """CelesTrak へ GET する。403 は「一時的な遮断」として案内文付きに正規化する。

    短時間に多数のリクエストを送ると CelesTrak は **IP 単位で 403 Forbidden** を返す
    （UA を変えても解除されない。実測 2026-09: curl/Mozilla UA でも 403）。
    生の "403 Client Error" では原因が分からないため、待てば直ることを明示する。
    """
    r = requests.get(BASE, headers=UA, params=params, timeout=timeout)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            raise requests.HTTPError(
                "アクセスが一時的に拒否されました（403 Forbidden）。"
                "短時間に多数のリクエストを送ると CelesTrak 側で IP 単位に遮断されます"
                "（数分〜しばらく待つと解除されます）。",
                response=getattr(e, "response", None)) from e
        raise
    return r


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


def fetch_tle(norad_id: Optional[int] = None,
              name: Optional[str] = None) -> Optional[tuple]:
    """NORAD ID または名前から (name, line1, line2) を返す。該当なしは None。

    satellite_map / sky_overlay / tiangong が各自持っていた CelesTrak TLE 取得の
    共通版。requests の例外はそのまま送出する（呼び出し側で処理する）。
    """
    if norad_id is not None:
        params = {"CATNR": norad_id}
    elif name:
        params = {"NAME": name}
    else:
        return None
    try:
        lines = [ln.strip() for ln in fetch_tle_text(**params)]
    except requests.HTTPError as e:
        # 該当なしのとき CelesTrak は 404 を返す → 「見つからない」として扱う
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            return None
        raise
    pair = [ln for ln in lines if ln.startswith(("1 ", "2 "))]
    if len(pair) < 2:
        return None
    return (lines[0].strip() if lines else ""), pair[0], pair[1]


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


def sat_tle(name: Optional[str] = None, norad_id: Optional[int] = None,
            group: Optional[str] = None, limit: int = 5) -> CallToolResult:
    """任意の人工衛星（ISS・ハッブル・気象衛星・中国宇宙ステーション等）の軌道要素(TLE)を返す。

    例:「ISSの軌道要素」「ハッブル宇宙望遠鏡のTLE」「気象衛星の軌道」
    認証不要。CelesTrak（NORADカタログ）から取得。
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
    try:
        rows = _fetch_tle(params)
    except requests.HTTPError as e:
        # 該当なしのとき CelesTrak は 404 を返す（接続障害と区別して案内する）
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "指定した衛星の軌道要素が見つかりませんでした。\n"
                    + ("和名→英語名の展開: " + ", ".join(terms) + "\n" if len(terms) > 1 else "")
                    + "既知の名前: " + ", ".join(sorted(WELL_KNOWN))
                    + "\nNORAD ID（例 25544=ISS）か、上記の別名をお試しください。"))],
                structuredContent={"error": "not found",
                                   "query": {"name": name, "norad_id": norad_id, "group": group},
                                   "total": 0, "results": []},
            )
        return CallToolResult(
            content=[TextContent(type="text", text=f"CelesTrak への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "celestrak.org"},
        )
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"CelesTrak への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "celestrak.org"},
        )
    if not rows:
        return CallToolResult(
            content=[TextContent(type="text", text="指定した衛星の軌道要素が見つかりませんでした。NORAD ID や別名をお試しください。")],
            structuredContent={"query": {"name": name, "norad_id": norad_id, "group": group}, "total": 0, "results": []},
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
            "tle": f"{pair[0]}\n{pair[1]}" if pair else "",
        })
    lines = [f"CelesTrak 衛星軌道要素（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. **{r['object_name']}** (NORAD {r['norad_id']})")
        lines.append(f"   軌道: 傾角 {r['inclination_deg']}°・離心率 {r['eccentricity']}・周回 {r['mean_motion_rev_day']}/日")
        lines.append(f"   エポック: {r['epoch']} UTC")
    lines.append("出典: celestrak.org（NORAD GP カタログ）／ TLE は軌道計算・可視パス予測の基礎データ。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": {"name": name, "norad_id": norad_id, "group": group},
                           "shown": len(records), "results": records},
    )

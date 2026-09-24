"""NASA Exoplanet Archive（系外惑星アーカイブ, NExScI/Caltech）の TAP 検索（認証不要）。

`https://exoplanetarchive.ipac.caltech.edu/TAP/sync` に ADQL を投げて JSON を得る。
VO TAP と違い `format=json` は**メタデータ無しの素の配列**を返す（`format=csv` も可）。

pscomppars（惑星の複合パラメータ表）は 2026-09-24 時点で **6,366 惑星**、
1惑星あたり **703 列**（軌道周期・軌道長半径・半径・質量・平衡温度・恒星放射・
恒星型・距離・TIC/Gaia ID など）を持つ。ps 表は default_flag=1 で同じ 6,366 件。

実測（2026-09-24）:
- `select count(*) from pscomppars` = 6366、応答 1.0〜1.4 秒。
- `lower(hostname) LIKE '%trappist%'` は使える（列名・値は大文字小文字を区別するため
  本体は必ず lower() で比較する）。
- `TRAPPIST-1` は 7 惑星すべてが返り、pl_orbper / pl_rade / pl_eqt / pl_insol /
  sy_dist / st_spectype が揃う。
- 地球型の目安（0.5〜2.0 地球半径 かつ 平衡温度 200〜320 K）は **61 件**。

出典表示が必要: NASA Exoplanet Archive（DOI: 10.26133/NEA12, NExScI/Caltech）。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, is_error_result, ttl_cache
from .input_utils import as_float, as_int
from .name_common import JA_ALIASES, expand_terms

TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
UA = {"User-Agent": "space-finder-mcp/0.38 (MCP; NASA Exoplanet Archive TAP)"}
TIMEOUT = (10, 45)

# pscomppars から取る列（実測で存在を確認済み）
COLS = ("pl_name,hostname,discoverymethod,disc_year,disc_facility,pl_orbper,"
        "pl_orbsmax,pl_rade,pl_bmasse,pl_eqt,pl_insol,sy_dist,sy_pnum,"
        "st_spectype,st_teff,tic_id,gaia_dr2_id")

# 発見手法の日本語 → アーカイブの英語値（部分一致で使う）
METHODS = {
    "トランジット": "transit", "transit": "transit",
    "視線速度": "radial velocity", "ドップラー": "radial velocity",
    "マイクロレンズ": "microlensing",
    "直接撮像": "imaging", "撮像": "imaging",
    "アストロメトリ": "astrometry",
    "パルサー": "pulsar",
    "食": "eclipse",
}


def _lit(v) -> str:
    """ADQL の文字列リテラル（クォートと % を除去して注入を防ぐ）。"""
    return "'" + str(v).replace("'", "").replace("%", "") + "'"


def _tap(adql: str) -> tuple:
    """TAP に ADQL を投げ (JSON, エラー) を返す。"""
    try:
        r = requests.get(TAP, params={"query": adql, "format": "json"},
                         headers=UA, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, "Exoplanet Archive に接続できません: {}".format(str(e)[:120])
    if r.status_code != 200:
        detail = " ".join((r.text or "").split())[:160]
        return None, "Exoplanet Archive が status={} を返しました（{}）".format(
            r.status_code, detail or "詳細なし")
    try:
        return r.json(), None
    except ValueError:
        return None, "Exoplanet Archive の応答が JSON ではありません"


def _name_terms(obj: str) -> list:
    """天体名を検索語に展開する（和名 → 英語名 ＋ 番号を連結した形も作る）。

    例: "ケプラー22" → ["ケプラー22", "kepler", "kepler22", "kepler-22", "kepler 22"]
    アーカイブの hostname は "Kepler-22" 形式なので、番号を付け直さないと 0 件になる。
    """
    o = " ".join(str(obj).split())
    terms = list(expand_terms(o))
    for key, ens in JA_ALIASES.items():
        if not key or key not in o:
            continue
        digits = "".join(ch for ch in o.split(key, 1)[1] if ch.isdigit())
        if not digits:
            continue
        for en in ens:
            terms += [en + digits, en + "-" + digits, en + " " + digits]
    seen, out = set(), []
    for t in terms:
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl)
            out.append(t)
    return out


def _where(object_name, method, max_distance_pc, since_year, earth_like) -> tuple:
    """検索条件から (WHERE 句, 条件の説明, エラー) を作る。"""
    conds, notes = [], []
    if object_name:
        o = " ".join(str(object_name).split())
        like = " OR ".join(
            "(lower(hostname) LIKE {} OR lower(pl_name) LIKE {})".format(
                _lit("%" + t.lower() + "%"), _lit("%" + t.lower() + "%"))
            for t in _name_terms(o))
        conds.append("(" + like + ")")
        notes.append("天体名 {}".format(o))
    if method:
        m = " ".join(str(method).split()).lower()
        m = METHODS.get(m, m)
        conds.append("lower(discoverymethod) LIKE {}".format(_lit("%" + m + "%")))
        notes.append("発見手法 {}".format(m))
    d = as_float(max_distance_pc, None, 0.0, 1e9)
    if max_distance_pc is not None and d is not None:
        conds.append("sy_dist <= {:.3f}".format(d))
        notes.append("距離 {:.1f} pc 以内".format(d))
    y = as_int(since_year, None, 1900, 2100)
    if since_year is not None and y is not None:
        conds.append("disc_year >= {}".format(y))
        notes.append("発見年 {} 以降".format(y))
    if earth_like:
        conds.append("pl_rade BETWEEN 0.5 AND 2.0")
        conds.append("pl_eqt BETWEEN 200 AND 320")
        notes.append("地球サイズ（0.5〜2.0 地球半径）かつ平衡温度 200〜320 K")
    return (" AND ".join(conds) if conds else "1=1"), notes, None


def _num(v):
    """文字列・数値を float に（失敗は None）。"""
    return as_float(v, None)


def _candidates(prefix: str, limit: int = 12) -> list:
    """該当なしのときに提示するホスト星名の候補（推測せず選択させる）。"""
    p = "".join(ch for ch in str(prefix) if ch.isalnum() or ch in " -")
    if not p:
        return []
    data, err = _tap("SELECT DISTINCT TOP {} hostname FROM pscomppars WHERE "
                     "lower(hostname) LIKE {}".format(limit, _lit(p.lower() + "%")))
    if err or not isinstance(data, list):
        return []
    return [r.get("hostname") for r in data if r.get("hostname")]


@ttl_cache(TTL_DAILY, maxsize=128, skip_if=is_error_result)
def exoplanet_search(object_name: Optional[str] = None,
                     discovery_method: Optional[str] = None,
                     max_distance_pc: Optional[float] = None,
                     since_year: Optional[int] = None,
                     earth_like: bool = False,
                     limit: int = 10) -> CallToolResult:
    """太陽系外惑星を NASA Exoplanet Archive から検索する（認証不要）。

    系外惑星の確定カタログ（複合パラメータ表 pscomppars）を検索し、軌道周期・
    軌道長半径・半径・質量・平衡温度・恒星放射・主星の型・距離・TIC/Gaia ID を返す。

    例:「TRAPPIST-1 の惑星は？」「地球くらいの温度の系外惑星を探す」「2020年以降に
    見つかった近い系外惑星」「トランジット法で見つかった惑星」

    Args:
        object_name: 主星名または惑星名（例 "TRAPPIST-1", "Proxima Cen", "Kepler-22",
            和名 "ケプラー22"）。部分一致で探し、和名は英語名＋番号に展開して試す。
        discovery_method: 発見手法で絞り込み（部分一致。例 "transit", "radial velocity",
            "microlensing"、和名は トランジット / 視線速度 / マイクロレンズ など）。
        max_distance_pc: 主星系までの距離の上限（パーセク）。
        since_year: 発見年（disc_year）の下限。
        earth_like: True で地球サイズ（0.5〜2.0 地球半径）かつ平衡温度 200〜320 K に絞る
            （2026-09 時点で 61 件）。
        limit: 返す惑星数（既定 10, 最大 50）。

    0 件のときは推測せず、主星名の候補を提示して停止する。数値引数は不正でも既定値に落ちる。
    出典: NASA Exoplanet Archive（NExScI/Caltech, DOI: 10.26133/NEA12）。
    """
    n = as_int(limit, 10, 1, 50)
    where, notes, _ = _where(object_name, discovery_method, max_distance_pc,
                             since_year, earth_like)
    order = "pl_orbper ASC" if object_name else "sy_dist ASC"
    data, err = _tap("SELECT TOP {} {} FROM pscomppars WHERE {} ORDER BY {}".format(
        n, COLS, where, order))
    if err:
        return CallToolResult(
            content=[TextContent(type="text", text="系外惑星の検索に失敗しました。{}".format(err))],
            structuredContent={"error": err, "source": "NASA Exoplanet Archive"})
    total = None
    cnt, cerr = _tap("SELECT count(*) FROM pscomppars WHERE {}".format(where))
    if not cerr and isinstance(cnt, list) and cnt and isinstance(cnt[0], dict):
        vals = list(cnt[0].values())
        total = as_int(vals[0], None) if vals else None

    lines = []
    records = []
    candidates = []
    if not isinstance(data, list) or not data:
        prefix = next((t for t in _name_terms(object_name or "") if t.isascii()), "")
        if object_name and prefix:
            candidates = _candidates(prefix)
        lines.append("条件に一致する系外惑星は見つかりませんでした。")
        if notes:
            lines.append("条件: {}".format(" / ".join(notes)))
        if candidates:
            lines.append("")
            lines.append("主星名の候補を提示します（推測はしません）:")
            lines += ["- {}".format(c) for c in candidates]
            lines.append("")
            lines.append("候補の名前をそのまま object_name に渡すか、条件を緩めてください。")
        else:
            lines.append("条件（距離・発見年・発見手法・earth_like）を緩めてお試しください。")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structuredContent={"count": 0, "candidates": candidates, "filters": notes,
                               "object_name": object_name,
                               "source": "NASA Exoplanet Archive (NExScI/Caltech)"})

    hosts = {}
    for row in data:
        rec = {"pl_name": row.get("pl_name"), "hostname": row.get("hostname"),
               "discovery_method": row.get("discoverymethod"),
               "discovery_year": as_int(row.get("disc_year"), None),
               "discovery_facility": row.get("disc_facility"),
               "orbital_period_days": _num(row.get("pl_orbper")),
               "semi_major_axis_au": _num(row.get("pl_orbsmax")),
               "radius_earth": _num(row.get("pl_rade")),
               "mass_earth": _num(row.get("pl_bmasse")),
               "equilibrium_temp_k": _num(row.get("pl_eqt")),
               "insolation_earth": _num(row.get("pl_insol")),
               "distance_pc": _num(row.get("sy_dist")),
               "planets_in_system": as_int(row.get("sy_pnum"), None),
               "star_spectral_type": row.get("st_spectype"),
               "star_teff_k": _num(row.get("st_teff")),
               "tic_id": row.get("tic_id"), "gaia_dr2_id": row.get("gaia_dr2_id")}
        records.append(rec)
        hosts[rec["hostname"]] = hosts.get(rec["hostname"], 0) + 1

    lines.append("NASA Exoplanet Archive: 系外惑星 {}件".format(len(records)))
    if notes:
        lines.append("条件: {}".format(" / ".join(notes)))
    if total is not None and total > len(records):
        lines.append("この条件に一致する惑星は全部で {}件（上位 {}件を表示）".format(total, len(records)))
    lines.append("")
    for rec in records:
        parts = []
        if rec["orbital_period_days"] is not None:
            parts.append("周期 {:.3f} 日".format(rec["orbital_period_days"]))
        if rec["semi_major_axis_au"] is not None:
            parts.append("軌道長半径 {:.4f} AU".format(rec["semi_major_axis_au"]))
        if rec["radius_earth"] is not None:
            parts.append("半径 {:.2f} 地球半径".format(rec["radius_earth"]))
        if rec["mass_earth"] is not None:
            parts.append("質量 {:.2f} 地球質量".format(rec["mass_earth"]))
        if rec["equilibrium_temp_k"] is not None:
            parts.append("平衡温度 {:.1f} K".format(rec["equilibrium_temp_k"]))
        if rec["insolation_earth"] is not None:
            parts.append("恒星放射 {:.2f} 地球".format(rec["insolation_earth"]))
        lines.append("- **{}**（{}）— {}".format(
            rec["pl_name"], rec["hostname"], " / ".join(parts) or "主要パラメータ未収録"))
        sub = []
        if rec["discovery_year"]:
            sub.append("{}年発見".format(rec["discovery_year"]))
        if rec["discovery_method"]:
            sub.append(rec["discovery_method"])
        if rec["distance_pc"] is not None:
            sub.append("距離 {:.2f} pc（{:.1f} 光年）".format(
                rec["distance_pc"], rec["distance_pc"] * 3.26156))
        if rec["star_spectral_type"]:
            sub.append("主星 {}".format(rec["star_spectral_type"]))
        if rec["planets_in_system"]:
            sub.append("この星系の惑星 {}個".format(rec["planets_in_system"]))
        if sub:
            lines.append("    - " + " / ".join(sub))

    advice = ("AIからのインテリジェントアドバイス: 平衡温度（pl_eqt）は恒星放射を惑星が"
              "一様に再放射すると仮定した値で、大気の温室効果は含みません（地球は 255 K に"
              "対して実測の平均気温は約 288 K）。「液体の水が可能」の目安は 200〜320 K 程度"
              "ですが、大気・磁場・自転の影響が大きいため、温度だけで居住可能性は決まりません。"
              "また pl_rade / pl_bmasse は測定手法により上限・下限しか無い場合があります。")
    lines.append("")
    lines.append(advice)
    if len(hosts) > 1:
        lines.append("")
        lines.append("主星の内訳: " + ", ".join(
            "{}×{}".format(k, v) for k, v in sorted(hosts.items(), key=lambda x: -x[1])))
    lines.append("")
    lines.append("出典: NASA Exoplanet Archive（NExScI/Caltech, DOI: 10.26133/NEA12）")
    lines.append("https://exoplanetarchive.ipac.caltech.edu/")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"count": len(records), "total_matching": total,
                           "records": records, "hosts": hosts, "filters": notes,
                           "object_name": object_name,
                           "archive_url": "https://exoplanetarchive.ipac.caltech.edu/",
                           "citation": "NASA Exoplanet Archive, DOI: 10.26133/NEA12",
                           "source": "NASA Exoplanet Archive (NExScI/Caltech)"})

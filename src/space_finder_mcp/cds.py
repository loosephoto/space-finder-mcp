"""CDS（ストラスブール天文データセンター）の天体カタログ検索（認証不要）。

2つの公開 VO サービスを1モジュールにまとめる（どちらも CDS が運用し、天体名の解決と
円錐検索という同じ段取りを使うため。AGENTS 規約5）。

1. SIMBAD TAP (simbad.cds.unistra.fr/simbad/sim-tap) — 天体の同定。
   天体種別・スペクトル型・年周視差・固有運動・視線速度と、周辺天体の円錐検索。
   既存の Sesame（name_common.resolve_object）は座標しか返さないので、
   こちらは「その天体が何か」を補う役割。
2. VizieR ASU (vizier.cds.unistra.fr/viz-bin/asu-tsv) — 観測星表の円錐検索
   （2MASS / Gaia DR3 / AllWISE / unWISE / Pan-STARRS1 / SDSS / ヒッパルコス / NGC）。

実測（2026-09-24, 本リポジトリの検証環境）:
- SIMBAD TAP は 0.9 秒で応答。basic テーブルの列は main_id / oid / otype_txt /
  sp_type / plx_value / plx_err / pmra / pmdec / rvz_radvel / ra / dec / nbref。
  等級列は basic に無い（旧 V を書くと HTTP 400）＝等級は allfluxes を
  JOIN ... ON allfluxes.oidref=basic.oid で取る。
  ident JOIN basic ON ident.oidref=basic.oid WHERE ident.id='Sirius' で
  別名（Sirius / M 31 等）から主名（* alf CMa）へ解決できる。
  注意: ORDER BY DISTANCE(...) は 400（SIMBAD の ADQL は DISTANCE 未対応）＝
  距離順はクライアント側で計算する。
- VizieR ASU は -source を省略すると世界中の星表を走査して 57 MB を返す（実測）。
  そのため本ツールは星表名を実質必須（既定 2mass）にし、未知の名前は推測せず
  候補一覧を返して停止する（AGENTS 規約8）。
  -c.rm の単位は分（arcmin）（10s のような秒指定は無視され0件になるのを実測）。
  0.1 分でも絞り込めるが、それ未満は既定値に落ちるため 0.1 を下限にする。
  応答は「# のメタ情報 → 列名 → 単位 → ダッシュ行 → データ」の TSV。

出典: CDS / SIMBAD（https://simbad.cds.unistra.fr）, VizieR（https://vizier.cds.unistra.fr）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_FORECAST, is_error_result, ttl_cache
from .input_utils import as_float, as_int
from .name_common import expand_terms, resolve_object

SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
VIZIER_ASU = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
UA = {"User-Agent": "space-finder-mcp/0.38 (MCP; CDS SIMBAD/VizieR)"}
TIMEOUT = (10, 45)

# SIMBAD basic から取る列（oid は続けて allfluxes を引くために必要）
_BASIC_COLS = ("main_id,oid,otype_txt,sp_type,plx_value,plx_err,pmra,pmdec,"
               "rvz_radvel,ra,dec,nbref")

# VizieR の星表（2026-09-24 に実データが返ることを実測で確認したものだけを載せる）。
# 表名は VizieR の "<カタログ>/<表>" 形式。生の表名も受け付けるが、既定で使うのは
# 検証済みのこの集合。
CATALOGS = {
    "2mass": ("II/246/out", "2MASS 赤外全天カタログ（J/H/Ks 等級）"),
    "gaia": ("I/355/gaiadr3", "Gaia DR3 主源カタログ（位置・年周視差・固有運動）"),
    "gaia-edr3": ("I/350/gaiaedr3", "Gaia EDR3 主源カタログ"),
    "hipparcos": ("I/239/hip_main", "ヒッパルコス星表（位置・年周視差・固有運動）"),
    "allwise": ("II/328/allwise", "AllWISE 中間赤外カタログ"),
    "unwise": ("II/363/unwise", "unWISE 中間赤外カタログ"),
    "ps1": ("II/349/ps1", "Pan-STARRS1 可視測光（DR1）"),
    "sdss16": ("V/154/sdss16", "SDSS DR16 天体カタログ"),
    "sdss12": ("V/147/sdss12", "SDSS DR12 天体カタログ"),
    "ngc": ("VII/118/ngc2000", "NGC 2000.0（銀河・星雲・星団）"),
}

# 座標列の候補（星表によって名前が違う。VizieR は円錐検索時に _RA.icrs を付ける）
_RA_NAMES = ("_RA.icrs", "_RAJ2000", "RA_ICRS", "RAJ2000", "RA_2000", "RAJ2000.0", "RAB2000")
_DE_NAMES = ("_DE.icrs", "_DEJ2000", "DE_ICRS", "DEJ2000", "DE_2000", "DEJ2000.0", "DEB2000")


def _lit(v) -> str:
    """ADQL / SQL の文字列リテラル（クォートと LIKE ワイルドカードの % を除去）。"""
    return "'" + str(v).replace("'", "").replace("%", "") + "'"


def _sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """2点の角距離（秒角）。小角近似（1度未満の円錐検索で十分な精度）。"""
    import math
    d = math.radians(dec2 - dec1)
    a = math.radians(ra2 - ra1) * math.cos(math.radians((dec1 + dec2) / 2.0))
    return math.hypot(a, d) * 206264.806


def _hms(ra_deg: float) -> str:
    """赤経（度）→ 時分秒表記。"""
    h = ra_deg / 15.0
    hh = int(h)
    mm = int((h - hh) * 60)
    ss = ((h - hh) * 60 - mm) * 60
    return "{:02d}h{:02d}m{:04.1f}s".format(hh, mm, ss)


def _dms(dec_deg: float) -> str:
    """赤緯（度）→ 度分秒表記。"""
    sign = "-" if dec_deg < 0 else "+"
    d = abs(dec_deg)
    dd = int(d)
    mm = int((d - dd) * 60)
    ss = ((d - dd) * 60 - mm) * 60
    return "{}{:02d}d{:02d}m{:04.1f}s".format(sign, dd, mm, ss)


def _sim_query(adql: str) -> tuple:
    """SIMBAD TAP に ADQL を投げ (列名, 行, エラー) を返す。"""
    try:
        r = requests.get(SIMBAD_TAP, params={"request": "doQuery", "lang": "adql",
                                             "format": "json", "query": adql},
                         headers=UA, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, None, "SIMBAD TAP に接続できません: {}".format(str(e)[:120])
    if r.status_code != 200:
        body = r.text or ""
        if "<?xml" in body[:80].upper() or "VOTABLE" in body[:400].upper():
            detail = "SIMBAD が ADQL を受け付けませんでした（列名・書式をご確認ください）"
        else:
            detail = " ".join(body.split())[:120] or "詳細なし"
        return None, None, "SIMBAD TAP が status={} を返しました（{}）".format(
            r.status_code, detail)
    try:
        payload = r.json()
    except ValueError:
        return None, None, "SIMBAD TAP の応答が JSON ではありません"
    cols = [c.get("name") for c in (payload.get("metadata") or [])]
    rows = payload.get("data") or []
    return cols, [dict(zip(cols, row)) for row in rows if isinstance(row, list)], None


def _simbad_by_ident(name: str) -> tuple:
    """別名（Sirius / M 31 / 和名の英語名）から SIMBAD の天体属性を引く。"""
    adql = ("SELECT {} FROM basic JOIN ident ON ident.oidref=basic.oid WHERE ident.id={}"
            .format(_BASIC_COLS, _lit(name)))
    return _sim_query(adql)


def _simbad_cone(ra: float, dec: float, radius_deg: float, limit: int) -> tuple:
    """座標の周辺天体を SIMBAD から引く（距離順はクライアント側で付ける）。"""
    adql = ("SELECT TOP {} {} FROM basic WHERE CONTAINS(POINT('ICRS',ra,dec),"
            "CIRCLE('ICRS',{:.6f},{:.6f},{:.6f}))=1").format(
        limit, _BASIC_COLS, ra, dec, radius_deg)
    return _sim_query(adql)


def _sim_flux(oid) -> dict:
    """allfluxes から等級（V/B/R/I/J/H/K）を取る。oid が無い場合は空。"""
    n = as_int(oid, None)
    if n is None:
        return {}
    _, rows, err = _sim_query(
        "SELECT V,B,R,I,J,H,K FROM allfluxes WHERE allfluxes.oidref={}".format(n))
    if err or not rows:
        return {}
    return {k: v for k, v in rows[0].items() if v is not None}


def _vizier_cone(catalog: str, ra: float, dec: float, radius_arcmin: float,
                 max_rows: int) -> tuple:
    """VizieR ASU の円錐検索。(列名, 単位, 行, メタ情報, エラー) を返す。"""
    params = {"-source": catalog, "-c": "{:.6f} {:.6f}".format(ra, dec),
              "-c.rm": "{:.3f}".format(radius_arcmin), "-out.max": str(max_rows)}
    try:
        r = requests.get(VIZIER_ASU, params=params, headers=UA, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, None, None, {}, "VizieR に接続できません: {}".format(str(e)[:120])
    if r.status_code != 200:
        return None, None, None, {}, "VizieR が status={} を返しました".format(r.status_code)
    if "<VOTABLE" in r.text[:400].upper():
        return None, None, None, {}, "VizieR が VOTable（エラー）を返しました。星表名を確認してください。"
    meta = {}
    for line in r.text.splitlines():
        if line.startswith("#Title:"):
            meta.setdefault("title", line[7:].strip())
        elif line.startswith("#Name:"):
            meta.setdefault("name", line[6:].strip())
        elif line.startswith("#RESOURCE"):
            break
    lines = [ln for ln in r.text.splitlines() if ln.strip() and not ln.startswith("#")]
    sep = None
    for i, ln in enumerate(lines):
        if set(ln.strip()) <= set("-\t"):
            sep = i
            break
    if sep is None or sep < 2:
        return None, None, [], meta, None            # 円錐内に天体なし（正常）
    header = [c.strip() for c in lines[sep - 2].split("\t")]
    units = [c.strip() for c in lines[sep - 1].split("\t")]
    rows = [ln.split("\t") for ln in lines[sep + 1:] if ln.strip()]
    return header, units, rows, meta, None


def _coord_pair(header: list) -> tuple:
    """星表の列名から座標列（赤経・赤緯）を推定する。無ければ (None, None)。"""
    return (next((c for c in _RA_NAMES if c in header), None),
            next((c for c in _DE_NAMES if c in header), None))


def _resolve_position(object_name, ra, dec) -> tuple:
    """天体名または座標から (ra, dec, 由来の説明, エラー応答) を作る。"""
    if object_name:
        hit = resolve_object(object_name)
        if not hit:
            return None, None, "", _err(
                "「{}」の座標を解決できませんでした（Sesame/CDS）。座標（ra, dec）を直接指定するか、"
                "別の表記（Messier 番号・英語名）でお試しください。".format(object_name),
                {"error": "name unresolved", "object_name": str(object_name)})
        return hit["ra_deg"], hit["dec_deg"], "{}（Sesame/CDS 経由: {}）".format(
            object_name, hit.get("resolver") or "Sesame"), None
    ra_v = as_float(ra, None, -360.0, 360.0)
    dec_v = as_float(dec, None, -90.0, 90.0)
    if ra_v is None or dec_v is None:
        return None, None, "", _err(
            "object_name（天体名）か、ra と dec（度）の両方を指定してください。",
            {"error": "object_name or (ra, dec) required", "ra": str(ra), "dec": str(dec)})
    return ra_v, dec_v, "座標指定", None


def _err(text: str, extra: dict) -> CallToolResult:
    """エラー応答（例外をツール外へ出さない。AGENTS 規約1）。"""
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structuredContent=dict(extra, source="CDS"))


def _catalog_candidates() -> list:
    """未知の星表名に対して提示する候補（推測せず選択させる）。"""
    return [{"key": k, "table": v[0], "description": v[1]} for k, v in CATALOGS.items()]


def _candidate_text(cands: list, header: str) -> str:
    """候補一覧の人間向け表示。"""
    lines = [header, ""]
    for c in cands:
        lines.append("- **{}** — {}（生の表名: {}）".format(
            c["key"], c["description"], c["table"]))
    return "\n".join(lines)


@ttl_cache(TTL_DAILY, maxsize=128, skip_if=is_error_result)
def object_lookup(object_name: str, neighbors: int = 0,
                  radius_arcmin: float = 2.0) -> CallToolResult:
    """天体の基本情報を SIMBAD（CDS）から取得する（認証不要）。

    SIMBAD は天文で最も使われる天体同定データベースで、天体の種別・スペクトル型・
    年周視差（距離）・固有運動・視線速度と、周辺天体（円錐検索）を返す。
    Sesame（planetary_evidence / mast_observations 等が使う名前解決）は
    「名前 → 座標」しか返さないため、このツールは「その天体が何か」を補う。

    例:「M31 ってどんな天体？」「シリウスの距離とスペクトル型」「プレアデス星団の周辺天体」

    Args:
        object_name: 天体名（Messier 番号・英語名・和名・星表名。例 "M31", "Sirius",
            "オリオン大星雲", "HD 48915"）。和名は英語名に展開して試す。
        neighbors: 0 より大きいと、その天体の周辺天体をこの件数だけ円錐検索して返す。
        radius_arcmin: 周辺天体を探す半径（分角, 0.1〜30, 既定 2.0）。

    数値引数は不正な値でも既定値に落ちる（例外は返さない）。0 件のときは推測せず、
    SIMBAD が持つ候補を提示して停止する。
    """
    count = as_int(neighbors, 0, 0, 50)
    radius = as_float(radius_arcmin, 2.0, 0.1, 30.0)
    name = " ".join(str(object_name or "").split())
    if not name:
        return _err("object_name（天体名）を指定してください。",
                    {"error": "object_name required"})

    row, matched_term = None, None
    # SIMBAD の識別子表は ASCII 名のみ（和名をそのまま投げると 400 になるのを実測）。
    # 和名は name_common.expand_terms が英語名へ展開するので、その英語名だけを試す。
    terms = [t for t in expand_terms(name) if t.isascii()][:4]
    for term in terms:
        _, rows, err = _simbad_by_ident(term)
        if err:
            return _err("SIMBAD の検索に失敗しました。{}".format(err),
                        {"error": err, "object_name": name})
        if rows:
            row, matched_term = rows[0], term
            break

    candidates = []
    if row is None:
        # 別名一致で引けない → Sesame で座標を出し SIMBAD を円錐検索（候補提示つき）
        hit = resolve_object(name)
        if hit:
            _, rows, err = _simbad_cone(hit["ra_deg"], hit["dec_deg"], 0.05, 12)
            if err:
                return _err("SIMBAD の検索に失敗しました。{}".format(err),
                            {"error": err, "object_name": name})
            rows = [r for r in rows if r.get("ra") is not None and r.get("dec") is not None]
            rows.sort(key=lambda r: _sep_arcsec(hit["ra_deg"], hit["dec_deg"],
                                                r["ra"], r["dec"]))
            if rows and _sep_arcsec(hit["ra_deg"], hit["dec_deg"],
                                    rows[0]["ra"], rows[0]["dec"]) <= 30.0:
                row = rows[0]          # 30 秒角以内なら同一天体とみなす
            else:
                candidates = rows[:8]
        if row is None:
            if candidates:
                lines = ["「{}」に対応する SIMBAD の天体を1つに決められませんでした。"
                         "候補を提示します（推測はしません）:".format(name), ""]
                for c in candidates:
                    lines.append("- {} — {} ({}, {})".format(
                        c.get("main_id"), c.get("otype_txt") or "種別不明",
                        _hms(c["ra"]), _dms(c["dec"])))
                lines.append("")
                lines.append("候補の名前をそのまま object_name に渡すか、座標を指定してください。")
                return CallToolResult(
                    content=[TextContent(type="text", text="\n".join(lines))],
                    structuredContent={"count": 0, "candidates": candidates,
                                       "object_name": name, "source": "SIMBAD (CDS)"})
            return _err(
                "「{}」を SIMBAD で解決できませんでした。Messier 番号や英語名・星表名"
                "（例 \"M31\", \"Sirius\", \"HD 48915\"）でお試しください。".format(name),
                {"error": "not found in SIMBAD", "object_name": name})

    oid = row.get("oid")
    flux = _sim_flux(oid)
    main_id = row.get("main_id") or name
    plx = as_float(row.get("plx_value"), None)
    plx_err = as_float(row.get("plx_err"), None)
    dist_pc = 1000.0 / plx if (plx is not None and plx > 0) else None
    ra_v, dec_v = as_float(row.get("ra"), None), as_float(row.get("dec"), None)

    rec = {"main_id": main_id, "object_type": row.get("otype_txt"),
           "spectral_type": row.get("sp_type"), "parallax_mas": plx,
           "parallax_err_mas": plx_err,
           "distance_pc": round(dist_pc, 3) if dist_pc is not None else None,
           "distance_ly": round(dist_pc * 3.26156, 2) if dist_pc is not None else None,
           "pm_ra_mas_yr": as_float(row.get("pmra"), None),
           "pm_dec_mas_yr": as_float(row.get("pmdec"), None),
           "radial_velocity_km_s": as_float(row.get("rvz_radvel"), None),
           "ra_deg": ra_v, "dec_deg": dec_v,
           "n_references": as_int(row.get("nbref"), None),
           "magnitudes": flux, "matched_term": matched_term, "oid": as_int(oid, None)}

    lines = ["SIMBAD: {} （SIMBAD / CDS）".format(main_id)]
    if matched_term and matched_term.lower() != name.lower():
        lines.append("_入力「{}」→ SIMBAD では「{}」として一致_".format(name, matched_term))
    lines.append("")
    lines.append("- 天体種別: {}".format(row.get("otype_txt") or "不明"))
    if row.get("sp_type"):
        lines.append("- スペクトル型: {}".format(row["sp_type"]))
    if plx is not None:
        if dist_pc is not None:
            lines.append("- 年周視差: {:.3f} mas → 距離 {:.2f} pc（{:.1f} 光年）{}".format(
                plx, dist_pc, dist_pc * 3.26156,
                "" if plx_err is None else " ±{:.3f} mas".format(plx_err)))
        else:
            lines.append("- 年周視差: {:.3f} mas（0 以下＝距離は未確定）".format(plx))
    if rec["pm_ra_mas_yr"] is not None or rec["pm_dec_mas_yr"] is not None:
        lines.append("- 固有運動: pmRA {:.2f} / pmDE {:.2f} mas/yr".format(
            rec["pm_ra_mas_yr"] or 0.0, rec["pm_dec_mas_yr"] or 0.0))
    if rec["radial_velocity_km_s"] is not None:
        lines.append("- 視線速度: {:.1f} km/s".format(rec["radial_velocity_km_s"]))
    if ra_v is not None and dec_v is not None:
        lines.append("- 座標 (ICRS): {} {} （{:.5f}°, {:.5f}°）".format(
            _hms(ra_v), _dms(dec_v), ra_v, dec_v))
    if flux:
        lines.append("- 等級: " + " / ".join(
            "{}={}".format(k, v) for k, v in sorted(flux.items())))
    if rec["n_references"] is not None:
        lines.append("- SIMBAD の参考文献数: {}".format(rec["n_references"]))

    neighbors_block = []
    if count > 0 and ra_v is not None and dec_v is not None:
        _, rows, err = _simbad_cone(ra_v, dec_v, radius / 60.0, count + 8)
        if err:
            lines.append("")
            lines.append("_周辺天体の取得に失敗しました（{}）_".format(err))
        else:
            rows = [r for r in rows if r.get("main_id") != main_id
                    and r.get("ra") is not None and r.get("dec") is not None]
            rows.sort(key=lambda r: _sep_arcsec(ra_v, dec_v, r["ra"], r["dec"]))
            rows = rows[:count]
            if rows:
                lines.append("")
                lines.append("周辺天体（半径 {:.1f} 分角以内, {}件）:".format(radius, len(rows)))
                for r in rows:
                    d = _sep_arcsec(ra_v, dec_v, r["ra"], r["dec"])
                    label = "{}{}".format(
                        r.get("main_id"),
                        "（{}）".format(r["sp_type"]) if r.get("sp_type") else "")
                    lines.append("- {} ｜ {} ｜ 離角 {:.1f} 秒角 ({:.2f} 分角)".format(
                        label, r.get("otype_txt") or "種別不明", d, d / 60.0))
                    neighbors_block.append({
                        "main_id": r.get("main_id"), "object_type": r.get("otype_txt"),
                        "spectral_type": r.get("sp_type"),
                        "ra_deg": as_float(r.get("ra"), None),
                        "dec_deg": as_float(r.get("dec"), None),
                        "separation_arcsec": round(d, 2)})
            else:
                lines.append("")
                lines.append("_半径 {:.1f} 分角以内に他の SIMBAD 天体はありませんでした"
                             "（星の密集度によっては空になります）_".format(radius))

    link = ("https://simbad.cds.unistra.fr/simbad/sim-id?Ident="
            + requests.utils.quote(main_id))
    lines.append("")
    lines.append("出典: SIMBAD（CDS, ストラスブール天文データセンター）")
    lines.append("SIMBAD でこの天体を開く: {}".format(link))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"object": rec, "neighbors": neighbors_block,
                           "neighbors_radius_arcmin": radius if count > 0 else None,
                           "simbad_url": link, "source": "SIMBAD (CDS)"})


@ttl_cache(TTL_FORECAST, maxsize=128, skip_if=is_error_result)
def catalog_search(object_name: Optional[str] = None, ra: Optional[float] = None,
                   dec: Optional[float] = None, catalog: str = "2mass",
                   radius_arcmin: float = 1.0, max_rows: int = 10) -> CallToolResult:
    """VizieR（CDS）の観測星表を「その位置にある天体」で検索する（認証不要）。

    VizieR は世界最大の天文観測星表アーカイブで、2MASS（赤外）・Gaia DR3・AllWISE・
    Pan-STARRS1・SDSS・ヒッパルコス・NGC などを、指定した天体のまわりで円錐検索できる。
    値は星表ごとの生の列名で返し、列名と単位も同時に返す（LLM が意味を推定せずに済む）。

    例:「M31 のまわりの 2MASS の天体」「シリウスの Gaia DR3 の値」「NGC カタログで
    オリオン大星雲を調べる」

    Args:
        object_name: 天体名（和名可。Sesame/CDS で座標へ解決してから検索する）。
        ra: 赤経（度）。object_name 省略時に dec と併用。
        dec: 赤緯（度）。
        catalog: 星表（既定 2mass）。指定できる名前は 2mass / gaia / gaia-edr3 /
            hipparcos / allwise / unwise / ps1 / sdss16 / sdss12 / ngc、または
            VizieR の生の表名（例 "II/246/out"）。未知の名前は候補一覧を返して停止する。
        radius_arcmin: 円錐検索の半径（分角, 0.1〜60, 既定 1.0）。VizieR の c.rm は
            分角単位（秒角指定は無視されるため 0.1 未満は 0.1 に丸める）。
        max_rows: 返す行数（既定 10, 最大 50）。

    注意: VizieR の ASU は星表名を省略すると世界中の全星表を走査して 57 MB を返すため、
    このツールは星表名の指定を前提にしている。
    """
    rows_n = as_int(max_rows, 10, 1, 50)
    radius = as_float(radius_arcmin, 1.0, 0.1, 60.0)
    key = " ".join(str(catalog or "").split()).lower()
    table, label = None, ""
    if key in CATALOGS:
        table, label = CATALOGS[key]
    elif key:
        # VizieR の生の表名（"II/246/out" や "J/A+A/649/A59/table1"）を許可する
        parts = key.split("/")
        if (len(parts) >= 2 and parts[0]
                and all(ch.isalnum() or ch in "._+-" for ch in parts[0])):
            table, label = str(catalog).strip(), "VizieR の星表（生の表名指定）"
    if not table:
        cands = _catalog_candidates()
        text = _candidate_text(cands,
                               "星表名「{}」は登録されていません。候補を提示します"
                               "（推測はしません）:".format(catalog))
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent={"count": 0, "candidates": cands,
                               "catalog": str(catalog), "source": "VizieR (CDS)"})

    ra_v, dec_v, how, error = _resolve_position(object_name, ra, dec)
    if error is not None:
        return error
    header, units, rows, meta, err = _vizier_cone(table, ra_v, dec_v, radius, rows_n)
    if err:
        return _err("VizieR の検索に失敗しました。{}".format(err),
                    {"error": err, "catalog": table, "object_name": object_name})
    ra_col, de_col = _coord_pair(header or [])

    lines = ["VizieR 星表検索: {}（{}）".format(label, table)]
    lines.append("位置: {}（{:.5f}°, {:.5f}°）／半径 {:.2f} 分角".format(how, ra_v, dec_v, radius))
    if meta.get("title"):
        lines.append("星表: {}".format(meta["title"]))
    lines.append("")
    records = []
    if not rows:
        lines.append("この星表には指定位置（半径 {:.2f} 分角）に天体がありませんでした。".format(radius))
        lines.append("半径を広げる（radius_arcmin）か、別の星表（catalog）を指定してください。")
        lines.append("")
        lines.append("_他の星表の候補:_ " + " / ".join(sorted(CATALOGS)))
    else:
        for row in rows:
            rec = {}
            for i, col in enumerate(header or []):
                rec[col] = row[i].strip() if i < len(row) else None
            if ra_col and de_col:
                try:
                    sep = _sep_arcsec(ra_v, dec_v, float(rec[ra_col]), float(rec[de_col]))
                    rec["_separation_arcsec"] = round(sep, 2)
                except (TypeError, ValueError):
                    pass
            records.append(rec)
        if records and "_separation_arcsec" in records[0]:
            records.sort(key=lambda r: r.get("_separation_arcsec") or 1e9)
        lines.append("{} 行を取得（{:.0f} 行を要求）:".format(len(records), rows_n))
        for rec in records:
            sep = rec.get("_separation_arcsec")
            lines.append("- **{}**{}".format(
                rec.get((header or [None])[0]) or "(1列目なし)",
                "" if sep is None else " ｜ 離角 {:.2f} 秒角".format(sep)))
            shown = 0
            for idx, col in enumerate((header or [])[1:], start=1):
                v = rec.get(col)
                if v in (None, ""):
                    continue
                unit = units[idx] if idx < len(units) else ""
                lines.append("    - {}: {}{}".format(col, v, " {}".format(unit) if unit else ""))
                shown += 1
                if shown >= 6:
                    break
        lines.append("")
        lines.append("_列名と単位は星表の定義そのままです（改変していません）。_")
    link = "https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=" + requests.utils.quote(table)
    lines.append("")
    lines.append("出典: VizieR（CDS）")
    lines.append("VizieR でこの星表を開く: {}".format(link))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"catalog": table, "catalog_label": label,
                           "catalog_title": meta.get("title"),
                           "columns": header or [], "units": units or [],
                           "count": len(records), "records": records,
                           "ra_deg": ra_v, "dec_deg": dec_v,
                           "radius_arcmin": radius, "position_from": how,
                           "vizier_url": link, "source": "VizieR (CDS)"})

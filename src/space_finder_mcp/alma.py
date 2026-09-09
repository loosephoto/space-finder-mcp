"""ALMA Science Archive（電波干渉計観測データ）検索（認証不要）。

ALMA（アタカマ大型ミリ波サブミリ波干渉計）は、国立天文台(NAOJ)が東アジア
担当機関として運営に深く関わる世界最大級の電波望遠鏡。その観測データの
メタデータは国際標準の VO TAP（Table Access Protocol）で公開されており、
天体名や座標から検索できる。

東アジア鏡（almascience.nao.ac.jp）は NAOJ が運用。astroquery.alma が内部で
使うのと同じ CADC 方式の二段階プロトコル:
  1) POST /tap/sync（REQUEST=doQuery, LANG=ADQL, FORMAT=csv, QUERY=...）→ HTTP 303
  2) 応答ヘッダ Location を GET すると CSV が返る
メタデータ検索は認証不要。FITS 本体のダウンロード（access_url 経由）は
要ログインの場合がある。
出典: https://almascience.nao.ac.jp/tap/sync（ALMA Science Archive, NAOJ）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

TAP = "https://almascience.nao.ac.jp/tap/sync"
UA = {"User-Agent": "space-finder-mcp/0.18 (MCP; ALMA Science Archive TAP)"}
_C = 299792458.0  # 光速 m/s


def _ghz(wavelength_m: float) -> float:
    """波長(m) → 周波数(GHz)。"""
    return _C / wavelength_m / 1e9


def _do_query(query: str, limit: int) -> tuple[Optional[list], Optional[str]]:
    """ADQL を二段階 TAP で実行し CSV 行（先頭=列名）を返す。失敗時 (None, err)。"""
    import csv as _csv
    import io
    try:
        # step1: ジョブ作成（リダイレクトを辿らない）
        r = requests.post(TAP, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query,
        }, headers=UA, timeout=40, allow_redirects=False)
    except requests.RequestException as e:
        return None, f"ALMA TAP 接続失敗: {str(e)[:120]}"
    loc = r.headers.get("Location") if r.status_code in (200, 303) else None
    if not loc:
        return None, f"ALMA TAP 応答異常（status={r.status_code}）。クエリの列・書式を確認してください。"
    try:
        # step2: 発行されたジョブ URL を GET して CSV を取得
        g = requests.get(loc, headers=UA, timeout=60)
        g.raise_for_status()
    except requests.RequestException as e:
        return None, f"ALMA TAP 結果取得失敗: {str(e)[:120]}"
    text = g.text
    if text.strip().upper().startswith("<?XML") or "<votable" in text[:200].lower():
        return None, "ALMA TAP が VOTable（エラー）を返しました。クエリを確認してください。"
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return [], None
    reader = _csv.reader(io.StringIO("\n".join(lines)))
    rows = [row for row in reader if row]
    if not rows:
        return [], None
    header = rows[0]
    return [header] + rows[1:limit + 1], None


def alma_search(object_name: Optional[str] = None,
                ra: Optional[float] = None, dec: Optional[float] = None,
                radius: float = 0.3, band: Optional[int] = None,
                limit: int = 8) -> CallToolResult:
    """ALMA（アルマ望遠鏡, 電波干渉計）の観測データを検索する（認証不要）。

    ALMA Science Archive（NAOJ 東アジア鏡, IVOA TAP）の観測メタデータを、
    天体名または座標(ra/dec)で ADQL 検索する。mm/サブmm電波観測なので、
    赤外・可視の望遠鏡とは異なるガス・塵の観測が特徴。

    例:「アルマで観測されたM100」「Orion 星雲のALMA観測」「アルマのバンド7のデータ」
    メタデータ検索は認証不要。FITS 本体のダウンロードは data_rights=Public のみ可
    （Restricted はプロポーザル権限が必要）。

    Args:
        object_name: 天体名（例 "M100", "Orion", "HL Tau", "TW Hya"）。座標指定より優先。
        ra: 赤経（度）。object_name 省略時、dec と併用（円錐検索）。
        dec: 赤緯（度）。
        radius: 座標検索の半径（度, 既定 0.3）。
        band: ALMA 受信バンドで絞り込み（1〜10。例 3=約100GHz帯, 6=約230GHz帯, 7=約345GHz帯）。
        limit: 返す件数（既定 8、最大 20）。
    """
    limit = max(1, min(int(limit), 20))
    cols = ("obs_publisher_did,target_name,s_ra,s_dec,band_list,em_min,em_max,"
            "proposal_id,data_rights")
    where = "obs_collection='ALMA'"
    if object_name:
        obj = object_name.strip()
        where += f" AND target_name='{obj.replace(chr(39), '')}'"
    else:
        if ra is None or dec is None:
            return CallToolResult(
                content=[TextContent(type="text", text="object_name か、ra と dec の両方を指定してください。")],
                structuredContent={"error": "object_name or (ra, dec) required"},
            )
        where += (f" AND 1=CONTAINS(POINT('ICRS',s_ra,s_dec),"
                  f"CIRCLE('ICRS',{float(ra):.6f},{float(dec):.6f},{float(radius):.6f}))")
    if band is not None:
        b = int(band)
        if 1 <= b <= 10:
            where += f" AND band_list LIKE '%{b}%'"
    q = f"SELECT TOP {limit} {cols} FROM ivoa.obscore WHERE {where}"
    rows, err = _do_query(q, limit)
    if err:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ALMA 観測データ検索に失敗しました。{err}")],
            structuredContent={"error": err, "source": "almascience.nao.ac.jp/tap"},
        )
    if len(rows) <= 1:
        target = object_name or f"{ra:.3f}, {dec:.3f}"
        return CallToolResult(
            content=[TextContent(type="text",
                                 text=f"「{target}」に一致する ALMA 観測データは見つかりませんでした。")],
            structuredContent={"count": 0, "source": "almascience.nao.ac.jp/tap"},
        )
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}

    def _get(row, key, default=""):
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else default

    lines = [f"📡 **ALMA 科学アーカイブ 観測データ**"
             f"（{object_name or f'ra={ra:.3f}° dec={dec:.3f}° 半径{radius}°'}）:"]
    records = []
    public = restricted = 0
    for row in rows[1:]:
        did = _get(row, "obs_publisher_did")
        target = _get(row, "target_name")
        s_ra = _get(row, "s_ra"); s_dec = _get(row, "s_dec")
        bl = _get(row, "band_list"); emin = _get(row, "em_min"); emax = _get(row, "em_max")
        proj = _get(row, "proposal_id"); dr = _get(row, "data_rights")
        if dr == "Public":
            public += 1
        else:
            restricted += 1
        freq = ""
        try:
            fh = _ghz(float(emin)); fl = _ghz(float(emax))  # 短波長=高周波
            freq = f"{min(fh, fl):.0f}–{max(fh, fl):.0f} GHz"
        except (TypeError, ValueError):
            pass
        rec = {"obs_publisher_did": did, "target_name": target,
               "ra_deg": _num(s_ra), "dec_deg": _num(s_dec),
               "band": bl, "frequency_ghz": freq, "proposal_id": proj,
               "data_rights": dr}
        records.append(rec)
        try:
            coord = f"({float(s_ra):.3f}°, {float(s_dec):.3f}°)" if s_ra and s_dec else ""
        except ValueError:
            coord = ""
        right = "🔓 公開" if dr == "Public" else "🔒 要権限"
        band_txt = f"バンド{bl} " if bl else ""
        lines.append(f"- **{target or did}** {band_txt}{freq}（{right}）{coord}  ※{did}")
    summary = f"{len(records)}件 表示（公開 {public} / 要権限 {restricted}）"
    advice = ("🤖 【AIからのインテリジェントアドバイス】ALMA はミリ波〜サブミリ波の電波干渉計で、"
              "低温のガス・塵や星・惑星系形成領域を観測します。バンド3≈100GHz, バンド6≈230GHz, "
              "バンド7≈345GHz が主力。data_rights=Public の FITS は ALMA アーカイブから取得可能ですが、"
              "Restricted（🔒）は元の観測プロポーザル権限者しか取得できません。")
    lines.append(f"_{summary}_")
    lines.append(advice)
    lines.append("出典: https://almascience.nao.ac.jp（ALMA Science Archive, NAOJ / 東アジア鏡）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"observatory": "ALMA (Atacama, Chile)", "count": len(records),
                           "public": public, "restricted": restricted,
                           "records": records, "source": "almascience.nao.ac.jp/tap"},
    )


def _num(v: str):
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None

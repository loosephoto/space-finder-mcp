"""CADC（カナダ天文データセンター）TAP 観測データ検索（認証不要）。

Canadian Astronomy Data Centre (CADC) はハッブル宇宙望遠鏡(HST)・ジェミニ望遠鏡・
NEOSSat 等の観測データを集約する世界規模の天文データセンター。
TAP (Table Access Protocol) で CAOM2 観測メタデータを ADQL クエリで検索できる。

パブリックメタデータは認証不要。画像・FITS ファイルのダウンロードは一部要認証。
出典: ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync（CADC TAP, パブリック）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_FORECAST, ttl_cache, is_error_result
from .input_utils import as_int

TAP = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync"
UA = {"User-Agent": "space-finder-mcp/0.13 (MCP; CADC TAP)"}


def _do_query(query: str, limit: int = 10) -> tuple[Optional[list[list]], Optional[str]]:
    """ADQL クエリを実行し、CSV 行を返す。エラー時は (None, err)。"""
    q = query
    if "TOP" not in q.split("SELECT", 1)[-1][:60].upper():
        # TOP 指定がなければ追加
        q = q.replace("SELECT", f"SELECT TOP {limit}", 1)
    try:
        r = requests.post(TAP, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
            "QUERY": q,
        }, headers=UA, timeout=40)
        r.raise_for_status()
    except requests.RequestException as e:
        return None, f"CADC TAP 接続失敗: {str(e)[:120]}"
    if "VOTABLE" in r.text[:200] or r.text.strip().startswith("<"):
        # エラー VOTable
        if "ERROR" in r.text[:300]:
            return None, "ADQL クエリエラー（列名・テーブル名を確認してください）"
        return None, "応答を解析できませんでした"
    lines = [l for l in r.text.strip().splitlines() if l.strip()]
    if not lines:
        return [], None
    rows = []
    for l in lines[1:]:
        rows.append(l.split(","))
    return rows[:limit], None


@ttl_cache(TTL_FORECAST, maxsize=64, skip_if=is_error_result)
def cadc_observations(object_name: Optional[str] = None,
                      ra: Optional[float] = None, dec: Optional[float] = None,
                      radius: float = 0.5, telescope: Optional[str] = None,
                      limit: int = 8) -> CallToolResult:
    """CADC（カナダ天文データセンター）の観測データを検索する（認証不要）。

    HST(ハッブル)・ジェミニ望遠鏡・NEOSSat 等の観測メタデータを、
    天体名または座標（ra/dec）で ADQL 検索する。

    例:「ハッブルが撮ったNGC 300」「オリオン星雲の観測データ」「ジェミニの観測一覧」
    パブリックメタデータは認証不要。画像ダウンロードは一部要認証。

    Args:
        object_name: 天体名（例 "M31", "Orion", "NGC 300"）。座標指定より優先。
        ra: 赤経（度）。object_name 省略時、dec と併用。
        dec: 赤緯（度）。
        radius: 検索半径（度, 既定 0.5）。
        telescope: 望遠鏡で絞り込み（例 "HST", "Gemini-North", "TESS"）。
        limit: 返す件数（既定 8、最大 20）。
    """
    limit = as_int(limit, 8, 1, 20)
    # 天体名 -> 座標（簡易既知テーブル）
    _KNOWN = {
        "m31": (10.68, 41.27), "andromeda": (10.68, 41.27), "andromeda galaxy": (10.68, 41.27),
        "orion": (83.82, -5.39), "orion nebula": (83.82, -5.39), "m42": (83.82, -5.39),
        "crab": (83.63, 22.01), "m1": (83.63, 22.01), "crab nebula": (83.63, 22.01),
        "pleiades": (56.75, 24.12), "m45": (56.75, 24.12),
        "ngc 300": (13.72, -37.68), "ngc300": (13.72, -37.68),
        "ngc 2808": (138.01, -64.86), "ngc2808": (138.01, -64.86),
        "cartwheel": (6.06, -33.71), "cartwheel galaxy": (6.06, -33.71),
    }
    if object_name:
        key = object_name.strip().lower()
        if key in _KNOWN:
            ra, dec = _KNOWN[key]
            object_name = object_name.strip()
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"天体 '{object_name}' の座標を内蔵テーブルで解決できませんでした。ra/dec を直接指定してください。")],
                structuredContent={"error": "unknown object", "object": object_name,
                                   "note": "内蔵の座標テーブル（M31/M42/オリオン/かに星雲/プレアデス等）にありません"},
            )
    elif ra is None or dec is None:
        return CallToolResult(
            content=[TextContent(type="text", text="object_name か、ra(赤経)とdec(赤緯)を指定してください。")],
            structuredContent={"error": "location required"},
        )

    # ADQL クエリ構築（位置列は caom2.Plane にあるため JOIN が必要）
    tel_cond = ""
    if telescope:
        tel_cond = f" AND UPPER(o.telescope_name) LIKE '%{telescope.upper()}%'"

    query = (
        f"SELECT TOP {limit} o.obsID, o.observationID, o.telescope_name, "
        f"o.instrument_name, o.target_name, o.type "
        f"FROM caom2.Observation o JOIN caom2.Plane p ON o.obsID=p.obsID "
        f"WHERE INTERSECTS(p.position_bounds, CIRCLE('ICRS', {ra}, {dec}, {radius})) = 1{tel_cond}"
    )
    rows, err = _do_query(query, limit)
    if err:
        return CallToolResult(
            content=[TextContent(type="text", text=f"CADC 検索に失敗しました: {err}")],
            structuredContent={"error": err, "source": "cadc TAP"},
        )
    if rows is None:
        return CallToolResult(content=[TextContent(type="text", text=f"CADC 検索エラー: {err}")], structuredContent={"error": err})
    if not rows:
        target = object_name or f"RA {ra}° Dec {dec}° (r={radius}°)"
        return CallToolResult(
            content=[TextContent(type="text", text=f"指定位置に観測データが見つかりませんでした（{target}）。")],
            structuredContent={"object": object_name, "ra": ra, "dec": dec, "radius": radius, "total": 0, "results": []},
        )
    lines = [f"🔭 CADC 観測データ（{'天体' if object_name else f'RA {ra}° Dec {dec}° r={radius}°'}）: {len(rows)} 件"]
    records = []
    for i, r in enumerate(rows, 1):
        # CSV は列順: obsID, observationID, telescope, instrument, target, type, date
        rec = {"obsID": r[0] if len(r) > 0 else "", "observationID": r[1] if len(r) > 1 else "",
               "telescope": r[2] if len(r) > 2 else "", "instrument": r[3] if len(r) > 3 else "",
               "target": r[4] if len(r) > 4 else "", "obs_type": r[5] if len(r) > 5 else "",
               "date": r[6] if len(r) > 6 else ""}
        records.append(rec)
        lines.append(f"{i}. **{rec['target'] or '?'}**（{rec['telescope']} / {rec['instrument']}）")
        lines.append(f"   {rec['observationID']}  {rec['date'][:10]}  [{rec['obs_type']}]")
    lines.append("出典: ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca（CADC TAP, パブリックメタデータ）／ 画像DLは一部要認証。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"object": object_name, "ra": ra, "dec": dec, "radius": radius,
                           "shown": len(records), "results": records},
    )

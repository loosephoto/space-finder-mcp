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

from .cache import TTL_DAILY, TTL_FORECAST, is_error_result, ttl_cache
from .img_common import media_link_line
from .input_utils import as_float, as_int
from .name_common import first_token, like_patterns, name_variants

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


# obscore から取得する列（図の自己申告 figure/1 とは別に、科学判定に効く列を並べる）
_COLS = ("obs_publisher_did,target_name,s_ra,s_dec,band_list,em_min,em_max,proposal_id,"
         "data_rights,dataproduct_type,s_resolution,t_exptime,pwv,qa2_passed,"
         "obs_release_date,pi_name,pub_title,publication_year,access_url,calib_level")


def _sql_str(v) -> str:
    """ADQL の文字列リテラル（シングルクォートは除去して注入を防ぐ）。"""
    return "'" + str(v).replace("'", "") + "'"


# 天体名の表記ゆれ（空白 / アンダースコア / 連結 / 大小）は name_common に集約した。
# アーカイブの target_name は観測者の入力そのままで "HL Tau"/"HL_Tau"/"HLTau" が併存する。
@ttl_cache(TTL_DAILY, maxsize=128, skip_if=is_error_result)
def _datalink_products(access_url: str, max_items: int = 12) -> list:
    """datalink（obscore の access_url）から実データ製品の一覧を取る。

    校正済み製品 tar / 生データ ASDM / auxiliary / README / 外部パイプライン製品が
    semantics 付きで返る。取得できない場合（要権限・断線）は空リスト。
    """
    import xml.etree.ElementTree as ET
    try:
        r = requests.get(access_url, headers=UA, timeout=45)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []

    def _tag(e):
        return e.tag.split("}")[-1]

    cols = [f.get("name") for f in root.iter() if _tag(f) == "FIELD"]
    cols = [c for c in cols if c]
    out = []
    for tr in root.iter():
        if _tag(tr) != "TR":
            continue
        row = dict(zip(cols, [(td.text or "") for td in tr if _tag(td) == "TD"]))
        url = row.get("access_url")
        if not url:
            continue
        cl = row.get("content_length", "")
        out.append({"url": url,
                    "semantics": row.get("semantics", ""),
                    "content_type": row.get("content_type", ""),
                    "size_mb": round(int(cl) / 1e6, 1) if cl.isdigit() else None,
                    "authorized": row.get("link_authorized", ""),
                    "description": (row.get("description") or "")[:90]})
        if len(out) >= max_items:
            break
    return out


def _semantics_ja(sem: str, url: str) -> str:
    """datalink semantics → 日本語ラベル（README/校正済/生データ/外部製品）。"""
    if "documentation" in sem:
        return "説明(README)"
    if "progenitor" in sem:
        return "生データ(ASDM)"
    if "auxiliary" in sem:
        return "付帯情報(auxiliary)"
    if "package" in sem:
        return "パイプライン製品"
    if "this" in sem:
        return "校正済み製品"
    return "製品"


def _candidate_names(first_token: str, limit: int = 8) -> list:
    """該当なしのときに「候補を提示して停止」するための target_name 候補。"""
    q = ("SELECT DISTINCT target_name FROM ivoa.obscore WHERE obs_collection='ALMA' "
         "AND target_name LIKE {}").format(_sql_str(first_token + "%"))
    rows, err = _do_query(q, limit)
    if err or not rows or len(rows) <= 1:
        return []
    return [r[0] for r in rows[1:] if r and r[0]]


@ttl_cache(TTL_FORECAST, maxsize=64, skip_if=is_error_result)
def alma_search(object_name: Optional[str] = None,
                ra: Optional[float] = None, dec: Optional[float] = None,
                radius: float = 0.3, band: Optional[int] = None,
                product_type: Optional[str] = None, public_only: bool = False,
                with_products: bool = False, limit: int = 8) -> CallToolResult:
    """ALMA（アルマ望遠鏡, 電波干渉計）の観測データを検索する（認証不要）。

    ALMA Science Archive（NAOJ 東アジア鏡, IVOA TAP）の観測メタデータを ADQL で検索する。
    mm/サブmm電波観測なので、赤外・可視の望遠鏡とは異なる低温ガス・塵の観測が特徴。

    例:「アルマで観測されたM100」「HL Tau のALMA観測（バンド7）」「アルマのキューブデータ」

    天体名は **表記ゆれを自動で吸収**する（"HL Tau" / "HL_Tau" / "HLTau" 等。アーカイブは
    観測者の入力そのままの名前を持つため完全一致だけでは 0 件になりやすい）。それでも
    0 件のときは推測せず、アーカイブにある候補名を提示して停止する。

    Args:
        object_name: 天体名（例 "M100", "HL Tau", "TW Hya", "Orion"）。
        ra: 赤経（度）。object_name 省略時、dec と併用（円錐検索）。
        dec: 赤緯（度）。
        radius: 座標検索の半径（度, 既定 0.3）。
        band: ALMA 受信バンドで絞り込み（1〜10。3≈100GHz帯, 6≈230GHz帯, 7≈345GHz帯）。
        product_type: "cube"（三次元分光データ）/ "image"（二次元画像）/ "visibility" /
            "all"（既定）。obscore は校正済み製品のみを収録する。
        public_only: True で公開データ（data_rights=Public）だけに絞る。
        with_products: True で datalink を引き、実データ製品（校正済み tar・生データ ASDM・
            README・パイプライン製品）の URL 一覧を先頭3件まで添える。
            content にはアイコン付きのクリック可能リンクとして出す。
        limit: 返す件数（既定 8、最大 20）。

    メタデータ検索は認証不要。FITS 本体のダウンロードは data_rights=Public のみ可
    （Restricted はプロポーザル権限が必要）。
    """
    limit = as_int(limit, 8, 1, 20)
    where = "obs_collection='ALMA'"
    matched_via = "target_name（完全一致）"
    like_used = []
    first_word = ""
    if object_name:
        obj = " ".join(str(object_name).split())
        first_word = first_token(obj)
        variants = name_variants(obj)
        where += " AND target_name IN ({})".format(",".join(_sql_str(v) for v in variants))
    else:
        if ra is None or dec is None:
            return CallToolResult(
                content=[TextContent(type="text", text="object_name か、ra と dec の両方を指定してください。")],
                structuredContent={"error": "object_name or (ra, dec) required"},
            )
        ra_v, dec_v = as_float(ra, None, -360.0, 360.0), as_float(dec, None, -90.0, 90.0)
        if ra_v is None or dec_v is None:
            return CallToolResult(
                content=[TextContent(type="text", text="ra（赤経）と dec（赤緯）は数値（度）で指定してください。")],
                structuredContent={"error": "ra/dec must be numbers", "ra": str(ra), "dec": str(dec)},
            )
        radius_v = as_float(radius, 0.3, 0.0, 10.0)
        where += (f" AND 1=CONTAINS(POINT('ICRS',s_ra,s_dec),"
                  f"CIRCLE('ICRS',{ra_v:.6f},{dec_v:.6f},{radius_v:.6f}))")
        matched_via = f"座標（半径 {radius_v}° の円錐検索）"
    pt = str(product_type or "all").strip().lower()
    if pt not in ("all", "cube", "image", "visibility"):
        return CallToolResult(
            content=[TextContent(type="text", text=f"product_type は all / cube / image / visibility "
                                 f"のいずれかで指定してください。受け取った値: {product_type!r}")],
            structuredContent={"error": "invalid product_type", "product_type": str(product_type)},
        )
    if pt != "all":
        where += f" AND dataproduct_type={_sql_str(pt)}"
    if band is not None:
        b = as_int(band, None)
        if b is None or not (1 <= b <= 10):
            return CallToolResult(
                content=[TextContent(type="text", text=f"band は 1〜10 の整数で指定してください"
                                     f"（例 band=6 は約230GHz帯）。受け取った値: {band!r}")],
                structuredContent={"error": "invalid band", "band": str(band)},
            )
        where += f" AND band_list LIKE '%{b}%'"
    if public_only:
        where += " AND data_rights='Public'"

    def _run(cond: str):
        q = f"SELECT TOP {limit} {_COLS} FROM ivoa.obscore WHERE {cond}"
        return _do_query(q, limit)

    rows, err = _run(where)
    if err:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ALMA 観測データ検索に失敗しました。{err}")],
            structuredContent={"error": err, "source": "almascience.nao.ac.jp/tap"},
        )
    # 完全一致で 0 件 → 表記ゆれをワイルドカードで拾い直す（大小・区切り・部分名）
    if object_name and len(rows) <= 1:
        pats = like_patterns(object_name)
        if pats:
            cond = where + " AND (" + " OR ".join(
                f"target_name LIKE {_sql_str(x)}" for x in pats) + ")"
            rows2, err2 = _run(cond)
            if not err2 and len(rows2) > 1:
                rows, like_used = rows2, pats
                matched_via = "target_name（表記ゆれを許容した部分一致）"
    if len(rows) <= 1:
        cands = _candidate_names(first_word) if first_token else []
        target = object_name or f"{ra:.3f}, {dec:.3f}"
        msg = [f"「{target}」に一致する ALMA 観測データは見つかりませんでした。"]
        if cands:
            msg.append("")
            msg.append("**アーカイブにある候補名**（推測せず候補を提示します。この中から選び直してください）:")
            msg += [f"- {c}" for c in cands]
            msg.append("")
            msg.append("候補が違う場合は座標検索（ra, dec, radius）か、band / product_type の指定を変えてください。")
        else:
            msg.append("座標検索（ra, dec, radius）をお試しください。")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(msg))],
            structuredContent={"count": 0, "candidates": cands, "matched_via": matched_via,
                               "source": "almascience.nao.ac.jp/tap"},
        )

    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}

    def _get(row, key, default=""):
        k = idx.get(key)
        return row[k] if k is not None and k < len(row) else default

    lines = [f"📡 **ALMA 科学アーカイブ 観測データ**"
             f"（{object_name or f'ra={ra:.3f}° dec={dec:.3f}° 半径{radius}°'}）:"]
    if like_used:
        lines.append(f"_※ 完全一致では見つからず、表記ゆれ（{' / '.join(like_used)}）で一致しました_")
    records = []
    public = restricted = 0
    spellings = {}
    products_block = []
    for row in rows[1:]:
        did = _get(row, "obs_publisher_did")
        target = _get(row, "target_name")
        s_ra = _get(row, "s_ra"); s_dec = _get(row, "s_dec")
        bl = _get(row, "band_list"); emin = _get(row, "em_min"); emax = _get(row, "em_max")
        proj = _get(row, "proposal_id"); dr = _get(row, "data_rights")
        dpt = _get(row, "dataproduct_type")
        res = _num(_get(row, "s_resolution"))
        texp = _num(_get(row, "t_exptime"))
        pwv = _num(_get(row, "pwv"))
        qa2 = _get(row, "qa2_passed")
        rel = _get(row, "obs_release_date")[:10]
        pi = _get(row, "pi_name")
        ptitle = _get(row, "pub_title")
        pyear = _get(row, "publication_year")
        access = _get(row, "access_url")
        if dr == "Public":
            public += 1
        else:
            restricted += 1
        spellings[target] = spellings.get(target, 0) + 1
        freq = ""
        try:
            fh = _ghz(float(emin)); fl = _ghz(float(emax))  # 短波長=高周波
            freq = f"{min(fh, fl):.0f}–{max(fh, fl):.0f} GHz"
        except (TypeError, ValueError):
            pass
        rec = {"obs_publisher_did": did, "target_name": target,
               "ra_deg": _num(s_ra), "dec_deg": _num(s_dec),
               "band": bl, "frequency_ghz": freq,
               "dataproduct_type": dpt,
               "spatial_resolution_arcsec": round(res, 3) if res else None,
               "exptime_s": round(texp, 1) if texp else None,
               "pwv_mm": round(pwv, 2) if pwv else None,
               "qa2_passed": qa2, "release_date": rel,
               "proposal_id": proj, "pi_name": pi,
               "publication": ptitle or None, "publication_year": pyear or None,
               "data_rights": dr, "access_url": access}
        records.append(rec)
        try:
            coord = f"({float(s_ra):.3f}°, {float(s_dec):.3f}°)" if s_ra and s_dec else ""
        except ValueError:
            coord = ""
        right = "🔓 公開" if dr == "Public" else "🔒 要権限"
        band_txt = f"バンド{bl} " if bl else ""
        res_txt = f" 分解能{res:.2f}″" if res else ""
        qa_txt = " QA2✓" if str(qa2).lower() in ("t", "true", "1") else ""
        lines.append(f"- **{target or did}** {dpt or '製品'} {band_txt}{freq}{res_txt}"
                     f"（{right}{qa_txt}）{coord}  ※{did}")
    summary = f"{len(records)}件 表示（公開 {public} / 要権限 {restricted}）"
    lines.append(f"_{summary}_")
    if len(spellings) > 1:
        lines.append("_アーカイブ上の表記: " + ", ".join(
            f"{k}×{v}" for k, v in sorted(spellings.items(), key=lambda x: -x[1])) + "_")
    advice = ("🤖 【AIからのインテリジェントアドバイス】ALMA はミリ波〜サブミリ波の電波干渉計で、"
              "低温のガス・塵や星・惑星系形成領域を観測します。バンド3≈100GHz, バンド6≈230GHz, "
              "バンド7≈345GHz が主力。obscore に入るのは校正済み製品（calib_level=2）で、"
              "空間分解能・可視降水量(pwv)・QA2 合否が付くので、分解能や天候条件で選ぶと実用的です。"
              "data_rights=Public の FITS は ALMA アーカイブから取得できますが、"
              "Restricted（🔒）は元の観測プロポーザル権限者しか取得できません"
              "（実データURLは with_products=True で一覧できます）。")
    lines.append(advice)
    if with_products:
        shown = 0
        for rec in records:
            if shown >= 3 or not rec.get("access_url"):
                break
            items = _datalink_products(rec["access_url"])
            if not items:
                continue
            lines.append("")
            lines.append(f"🗂 **{rec['target_name']}（{rec['obs_publisher_did']}）の実データ製品**"
                         f" {len(items)}件:")
            for it in items:
                label = "{}（{}, {}）".format(
                    _semantics_ja(it["semantics"], it["url"]),
                    it["content_type"] or "?", 
                    "{} MB".format(it["size_mb"]) if it["size_mb"] is not None else "サイズ不明")
                line = media_link_line(label, url=it["url"], kind="file")
                if line:
                    lines.append("   " + line)
            products_block.append({"obs_publisher_did": rec["obs_publisher_did"],
                                   "target_name": rec["target_name"], "products": items})
            shown += 1
    lines.append("出典: https://almascience.nao.ac.jp（ALMA Science Archive, NAOJ / 東アジア鏡）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"observatory": "ALMA (Atacama, Chile)", "count": len(records),
                           "public": public, "restricted": restricted,
                           "matched_via": matched_via,
                           "matched_target_names": sorted(spellings),
                           "records": records, "products": products_block,
                           "source": "almascience.nao.ac.jp/tap"},
    )


def _num(v: str):
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None

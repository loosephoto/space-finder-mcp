"""MAST（Mikulski Archive for Space Telescopes）の観測データ検索（認証不要）。

NASA/STScI が運用する MAST には、JWST・ハッブル(HST)・TESS・Kepler・GALEX・IUE 等の
**科学アーカイブ**（観測ログ・校正レベル・データ製品）が入っている。Mashup API
（`/api/v0/invoke` に JSON を POST）で、天体名・座標コーン・装置・観測日で検索できる。

- 位置検索は `Mast.Caom.Filtered.Position`（コーン＋絞り込み）。`Mast.Caom.Cone` は
  filters を受け付けない（実測: filters を付けても無視され TESS が返った）。
- 連続値の絞り込みは `values: [{"min": …, "max": …}]` の形で渡す（`[a, b]` では 0 件になる。実測）。
- 文字列の曖昧一致は `values: []` ＋ `freeText: "%NIRCAM%"`（`%` は明示が必要）。
- `Mast.Caom.Products` は **CAOM の obsid（整数）** で引く（obs_id 文字列では ERROR。実測）。
- MAST は遅い（実測 45 秒超のことがある）。タイムアウトは (connect, read) で必ず指定し、
  接続不可・遮断は記憶して以降は fail fast する（並列で走る他ツールを道連れにしない）。

出典: mast.stsci.edu/api/v0（MAST Mashup API, メタデータは認証不要・ダウンロードも公開分は不要）。
`preview_image=true` では観測のプレビュー画像（.jpg）を1枚だけ取得して content にインライン表示する
（画像の前に🖼️リンクを置く: 規約13。取得失敗時はテキストのみに落ちる）。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import threading
import time
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .cache import TTL_FORECAST, ttl_cache, is_error_result
from .img_common import media_link_line, save_output
from .input_utils import as_float, as_int
from .name_common import resolve_object

INVOKE = "https://mast.stsci.edu/api/v0/invoke"
DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
PORTAL = "https://mast.stsci.edu/search/ui/#/?obsid="
UA = {"User-Agent": "space-finder-mcp/0.32 (MCP; MAST CAOM)"}

# MAST は遅い（実測 45 秒超）。connect は短く、read は長めに取る。
CONNECT_TIMEOUT = (10, 90)
_COOLDOWN_SECONDS = 45
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0
_COOLDOWN_LAST = ""

# カタログの列（CAOM）。観測の素性が分かる最小限＋フィルタ情報。
COLS = ("obs_id,obsid,obs_collection,instrument_name,target_name,t_min,t_max,"
        "s_ra,s_dec,calib_level,dataproduct_type,proposal_id,filters,intentType")

# ミッション名の解決（日本語・通称 → CAOM の obs_collection）。
MISSIONS = {
    "jwst": "JWST", "webb": "JWST", "ジェイムズウェッブ": "JWST", "ウェッブ": "JWST",
    "hst": "HST", "hubble": "HST", "ハッブル": "HST", "ハッブル宇宙望遠鏡": "HST",
    "tess": "TESS", "テス": "TESS",
    "kepler": "Kepler", "ケプラー": "Kepler",
    "galex": "GALEX",
    "iue": "IUE", "fuse": "FUSE", "hla": "HLA", "スピッツァー": "Spitzer", "spitzer": "Spitzer",
}
# データ種別（dataproduct_type）
PRODUCT_TYPES = ("image", "spectrum", "cube", "timeseries", "catalog")
_MJD_EPOCH = dt.date(1858, 11, 17)


def _cooldown_status() -> tuple:
    """遮断中なら (残り秒, 理由)。"""
    with _COOLDOWN_LOCK:
        return (max(0.0, _COOLDOWN_UNTIL - time.time()), _COOLDOWN_LAST)


def _note_unreachable() -> None:
    global _COOLDOWN_UNTIL, _COOLDOWN_LAST
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + _COOLDOWN_SECONDS)
        _COOLDOWN_LAST = "接続不可（タイムアウトまたはネットワーク断）"


def reset_blocked() -> None:
    """遮断の記憶を消す（検証・ゲート用。実運用では呼ばない）。"""
    global _COOLDOWN_UNTIL, _COOLDOWN_LAST
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL, _COOLDOWN_LAST = 0.0, ""


def _blocked_message(remaining: float) -> str:
    return ("MAST に接続できませんでした（直近の取得がタイムアウトまたは接続不可）。"
            "約 {:.0f} 秒後に再試行してください（MAST は混雑時に応答が遅くなります）。"
            .format(max(1.0, remaining)))


def _invoke(service: str, params: dict, pagesize: int = 10,
            page: int = 1) -> tuple[Optional[dict], Optional[str]]:
    """Mashup API を呼ぶ。戻り: (レスポンス dict, エラー文字列)。例外は外へ出さない。"""
    remaining, _reason = _cooldown_status()
    if remaining > 0:
        return None, _blocked_message(remaining)
    body = {"request": json.dumps({"service": service, "params": params, "format": "json",
                                   "pagesize": pagesize, "page": page})}
    try:
        r = requests.post(INVOKE, data=body, headers=UA, timeout=CONNECT_TIMEOUT)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout):
        _note_unreachable()
        return None, _blocked_message(_COOLDOWN_SECONDS)
    except requests.HTTPError as ex:
        return None, "MAST API エラー（HTTP {}）".format(
            getattr(getattr(ex, "response", None), "status_code", "?"))
    except requests.RequestException as ex:
        return None, "MAST へのリクエストに失敗しました: {}".format(str(ex)[:120])
    try:
        data = r.json()
    except ValueError:
        return None, "MAST の応答を解析できませんでした（JSON 以外）"
    if str(data.get("status") or "").upper() == "ERROR":
        return None, "MAST がクエリを拒否しました: {}".format(str(data.get("msg") or "")[:120])
    return data, None


def _norm_space(s) -> str:
    """表示用に空白を1つへ畳む（Sesame の canonical name は "M  31" のように空白が入る）。"""
    return " ".join(str(s or "").split())


def _mjd_to_iso(v) -> str:
    """MAST の観測時刻（MJD, 日単位）を ISO 日時にする。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if f <= 0 or f > 200000:                 # 異常値は空にする（架空の日付を作らない）
        return ""
    return (dt.datetime(1858, 11, 17) + dt.timedelta(days=f)).strftime("%Y-%m-%d %H:%M")


def _mjd_from_date(s) -> Optional[float]:
    """'2024-01-31' / '2024/01/31' を MJD へ。解釈できない値は None（例外にしない）。"""
    t = str(s or "").strip().replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            d = dt.datetime.strptime(t, fmt).date()
        except ValueError:
            continue
        return (d - _MJD_EPOCH).days
    return None


def _resolve_mission(mission) -> Optional[str]:
    """ミッション名（和名・通称・大文字小文字）を CAOM の値へ寄せる。未知はそのまま返す。"""
    m = str(mission or "").strip()
    if not m:
        return None
    key = m.lower().replace(" ", "").replace("-", "")
    if key in MISSIONS:
        return MISSIONS[key]
    return m.upper() if m.isascii() else m


def _instrument_filter(inst: str) -> dict:
    """装置名のフィルタ。装置コード（WFPC2・NIRCAM/IMAGE 等）は完全一致、それ以外は部分一致。

    完全一致は速いが 'NIRCam' のような書き方では引けない（実測: values は完全一致のみ）。
    逆に freeText（%NIRCam%）は全走査になり MAST 側が数十秒かかることがあるので、
    「装置コードらしい入力（英数字と / のみ・大文字小文字を問わず短い）」だけ完全一致にする。
    """
    code = " ".join(str(inst or "").split())
    # 装置コード（WFPC2 / nircam-image 等）は「/ を含む」か「すでに大文字」のものだけ。
    # 'NirCam' のような書き方ゆれは freeText へ回す（casesensitive な完全一致では引けない）。
    looks_like_code = (bool(code) and len(code) <= 24
                       and all(c.isalnum() or c in "/-" for c in code)
                       and ("/" in code or code == code.upper()))
    if looks_like_code:
        return {"paramName": "instrument_name", "values": [code.upper()]}
    return {"paramName": "instrument_name", "values": [], "freeText": "%{}%".format(code.upper())}


def _build_filters(mission, instrument, product_type, start_date, end_date, target_text,
                   include_calibration: bool = False):
    """CAOM の filters を組み立てる（連続値は {"min","max"}、部分一致は freeText）。"""
    filters = []
    if not include_calibration:
        # BIAS/DARK 等の校正用露出を既定で除外する（M42 のコーン検索が BIAS/DARK だらけになる実測）
        filters.append({"paramName": "intentType", "values": ["science"]})
    m = _resolve_mission(mission)
    if m:
        filters.append({"paramName": "obs_collection", "values": [m]})
    if instrument:
        filters.append(_instrument_filter(instrument))
    pt = str(product_type or "").strip().lower()
    if pt:
        if pt in PRODUCT_TYPES:
            filters.append({"paramName": "dataproduct_type", "values": [pt]})
        else:
            filters.append({"paramName": "dataproduct_type", "values": [],
                            "freeText": "%{}%".format(pt.upper())})
    lo, hi = _mjd_from_date(start_date), _mjd_from_date(end_date)
    if lo is not None or hi is not None:
        rng = {}
        if lo is not None:
            rng["min"] = lo
        if hi is not None:
            rng["max"] = hi
        filters.append({"paramName": "t_min", "values": [rng]})
    if target_text:
        filters.append({"paramName": "target_name", "values": [], "freeText": target_text})
    return filters


def _preview_content(products, alt):
    """データ製品のうちプレビュー画像（.jpg/.png）を1枚だけ取り込む。

    戻り: (ImageContent or None, 画像URL or None, 保存パス or None)。
    画像本体より前に必ずリンクを置く（規約13）。取得に失敗しても結果は落とさない。
    """
    # ファイル名で判定する（MAST のダウンロードURLは ?uri=… の中に拡張子が入るため、
    # URL を "?" で切ると拡張子が消える）
    url = next((p.get("url") for p in products or []
                if str(p.get("filename") or p.get("url") or "").lower()
                .endswith((".jpg", ".jpeg", ".png"))), None)
    if not url:
        return None, None, None
    try:
        r = requests.get(url, headers=UA, timeout=(10, 60))
        r.raise_for_status()
        data = r.content
    except requests.RequestException:
        return None, url, None
    if not data or len(data) > 12 * 1024 * 1024:
        return None, url, None
    is_png = url.lower().endswith(".png")
    path = save_output(data, "mast_preview", "png" if is_png else "jpg")
    return (ImageContent(type="image", data=base64.b64encode(data).decode("ascii"),
                         mimeType="image/png" if is_png else "image/jpeg",
                         altText=alt), url, path)


def _products(obsid, limit: int = 5):
    """1観測のデータ製品（ファイル名・サイズ・ダウンロードURL）を返す。"""
    data, err = _invoke("Mast.Caom.Products", {"obsid": str(obsid)}, pagesize=limit)
    if err or data is None:
        return [], err
    out = []
    for p in (data.get("data") or [])[:limit]:
        uri = str(p.get("dataURI") or "")
        out.append({"filename": p.get("productFilename") or "",
                    "description": (p.get("description") or "")[:80],
                    "size_bytes": p.get("size"), "calib_level": p.get("calib_level"),
                    "data_rights": p.get("dataRights") or "",
                    "url": (DOWNLOAD + uri) if uri else ""})
    return out, None


@ttl_cache(TTL_FORECAST, maxsize=32, skip_if=is_error_result)
def mast_observations(object_name: Optional[str] = None, ra: Optional[float] = None,
                      dec: Optional[float] = None, radius: float = 0.05,
                      mission: Optional[str] = None, instrument: Optional[str] = None,
                      product_type: Optional[str] = None, start_date: Optional[str] = None,
                      end_date: Optional[str] = None, include_products: bool = False,
                      include_calibration: bool = False, preview_image: bool = False,
                      limit: int = 10) -> CallToolResult:
    """MAST（NASA/STScI の宇宙望遠鏡アーカイブ）の観測データを検索する（認証不要）。

    JWST・ハッブル(HST)・TESS・Kepler・GALEX などの**科学アーカイブ**を、天体名（和名可）・
    座標・装置・観測日で横断検索し、観測ID・装置・観測日・校正レベル・データ製品（FITS の
    ダウンロードURL）まで返します。NASA の広報画像を探す search_space_images とは別物で、
    「その天体がどの装置でいつ観測されたか」を調べる用途です。

    例:「M31 を JWST が観測したデータ」「ハッブルが撮ったオリオン大星雲の観測ログ」
    「2026年に NIRCam で撮られた画像」「TESS の観測一覧」

    Args:
        object_name: 天体名（例 "M31", "Orion Nebula", "NGC 300", "アンドロメダ"）。
            内蔵/SIMBAD/NED（Sesame）で座標解決し、その周囲をコーン検索します。座標より優先。
            解決できない名前は target_name のワイルドカード検索（`*` は `%` 扱い）に切り替えます。
        ra: 赤経（度）。object_name 省略時、dec と併用。
        dec: 赤緯（度）。
        radius: コーン検索の半径（度, 既定 0.05・最大 5）。
        mission: ミッション/望遠鏡（例 "JWST", "HST", "TESS", "ハッブル", "ウェッブ", "Kepler"）。
        instrument: 装置名の**部分一致**（例 "NIRCam", "MIRI", "WFPC2", "NIRSPEC"）。
        product_type: データ種別（image / spectrum / cube / timeseries / catalog、部分一致可）。
        start_date: 観測日の下限（例 "2024-01-31"、"2024"）。t_min（MJD）に変換して絞り込み。
        end_date: 観測日の上限。
        include_products: true で上位3観測のデータ製品（ファイル名・サイズ・DLリンク）も返す。
        include_calibration: true で校正用露出（BIAS/DARK 等の intentType=calibration）も含める。
            既定 false（科学観測だけ）。
        preview_image: true で先頭観測のプレビュー画像（.jpg/.png）を content にインライン表示する
            （データ製品を取得し、画像1枚を保存してリンクと併せて返す）。
        limit: 返す観測件数（既定 10、最大 50）。
    """
    limit = as_int(limit, 10, 1, 50) or 10
    radius = as_float(radius, 0.05, 0.0001, 5.0) or 0.05
    ra_f = as_float(ra, None, -360.0, 360.0)
    dec_f = as_float(dec, None, -90.0, 90.0)

    resolved, target_text = None, None
    if object_name:
        key = " ".join(str(object_name).split())
        hit = resolve_object(key)
        if hit:
            ra_f, dec_f = as_float(hit.get("ra_deg"), None, -360.0, 360.0), \
                as_float(hit.get("dec_deg"), None, -90.0, 90.0)
            resolved = {"input": key, "matched": hit.get("matched_name"),
                        "via": hit.get("resolver"), "otype": hit.get("otype"),
                        "ra_deg": ra_f, "dec_deg": dec_f,
                        "other_resolvers": [p.get("resolver") for p in hit.get("all_positions", [])[1:]]}
        else:
            # 星表の内部名（例 'UDS_cat_visit1_EOSA'）は Sesame で解けないので target 検索に回す
            target_text = "%{}%".format(key.replace("*", "%"))
    if ra_f is None or dec_f is None:
        if not target_text:
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "天体名（object_name）か ra/dec（度）を指定してください。"
                    "座標を解決できない名前は target_name のワイルドカード検索で拾います"
                    "（例 object_name='UDS_cat*'）。"))],
                structuredContent={"error": "location required",
                                   "note": "object_name か ra/dec が必要"})

    filters = _build_filters(mission, instrument, product_type, start_date, end_date, target_text,
                             include_calibration=bool(include_calibration))
    params = {"columns": COLS, "filters": filters}
    service = "Mast.Caom.Filtered"
    if ra_f is not None and dec_f is not None:
        service = "Mast.Caom.Filtered.Position"
        params["position"] = "{}, {}, {}".format(ra_f, dec_f, radius)
    data, err = _invoke(service, params, pagesize=limit)
    rows = ((data or {}).get("data") or []) if data else []
    if err:
        return CallToolResult(content=[TextContent(type="text", text="MAST 検索に失敗しました: " + err)],
                              structuredContent={"error": err, "source": "MAST Mashup API",
                                                 "service": service,
                                                 "note": "MAST は混雑時に応答が遅くなります（自動遮断あり）"})
    records = [{
        "obs_id": r.get("obs_id"), "obsid": r.get("obsid"),
        "mission": r.get("obs_collection"), "instrument": r.get("instrument_name"),
        "target": _norm_space(r.get("target_name")),
        "date_utc": _mjd_to_iso(r.get("t_min") or r.get("t_max")),
        "end_utc": _mjd_to_iso(r.get("t_max")), "ra": r.get("s_ra"), "dec": r.get("s_dec"),
        "calib_level": r.get("calib_level"), "product_type": r.get("dataproduct_type"),
        "proposal_id": r.get("proposal_id"), "filters": r.get("filters"),
        "intent": r.get("intentType"),
        "portal_url": PORTAL + str(r.get("obsid") or ""),
    } for r in rows if isinstance(r, dict)]
    products = {}
    prod_err = None
    if include_products or preview_image:
        for rec in records[:3]:
            if rec.get("obsid") is None:
                continue
            got, perr = _products(rec["obsid"])
            products[str(rec["obs_id"])] = got
            prod_err = prod_err or perr

    lines_extra = []
    image_block, image_url, image_path = None, None, None
    if preview_image and records:
        head = records[0]
        image_block, image_url, image_path = _preview_content(
            products.get(str(head["obs_id"]), []),
            "{} の観測プレビュー（{} / {}）".format(head["target"] or head["obs_id"],
                                                    head["mission"], head["instrument"]))
        if image_url:
            # メディア本体より前に必ずリンクを置く（規約13）
            link = media_link_line("観測プレビュー画像を開く（{}）".format(head["obs_id"]),
                                   url=image_url, path=image_path, kind="image")
            if link:
                lines_extra.append(link)
    if resolved:
        where = _norm_space(resolved["matched"])
    elif target_text:
        where = target_text.strip("%")
    else:
        where = "RA {:.4f}° Dec {:.4f}°".format(ra_f, dec_f)
    conds = []
    if _resolve_mission(mission):
        conds.append("ミッション {}".format(_resolve_mission(mission)))
    if instrument:
        conds.append("装置 {}（部分一致）".format(instrument))
    if product_type:
        conds.append("種別 {}".format(product_type))
    if start_date or end_date:
        conds.append("観測日 {}〜{}".format(start_date or "?", end_date or "?"))
    lines = ["🔭 MAST 観測データ（{}・{} 件{}）".format(
        where, len(records), "／" + "、".join(conds) if conds else "")]
    if resolved:
        lines.append("ℹ️ 名前解決: 「{}」→ {}（{}） RA {:.4f}° Dec {:.4f}° r={}°".format(
            resolved["input"], resolved["matched"], resolved["via"], ra_f, dec_f, radius))
    if not records:
        lines.append("条件に合う観測が見つかりませんでした"
                     "（期日・装置・ミッションの指定を緩めてみてください）。")
    for i, rec in enumerate(records, 1):
        lines.append("{}. **{}**（{} / {}）".format(i, rec["target"] or "?", rec["mission"],
                                                    rec["instrument"]))
        lines.append("   {}  `{}`  {}  calib {}  {}".format(
            (rec["date_utc"] or "日付不明"), rec["obs_id"], rec["product_type"] or "",
            rec["calib_level"], "[MAST で開く]({})".format(rec["portal_url"])))
        if rec["filters"]:
            lines.append("   フィルタ: {}".format(rec["filters"]))
        for p in products.get(str(rec["obs_id"]), [])[:5]:
            lines.append("   - 📄 [{}]({}){}{}".format(
                p["filename"], p["url"] or rec["portal_url"],
                "  {}".format(p["description"]) if p["description"] else "",
                "  ※要認証" if p["data_rights"] and p["data_rights"] != "PUBLIC" else ""))
    if prod_err:
        lines.append("⚠️ データ製品の取得に失敗: {}".format(prod_err))
    lines += lines_extra
    lines.append("出典: MAST（mast.stsci.edu/api/v0, Mashup API）／ メタデータは認証不要・"
                 "公開データの FITS はダウンロードも認証不要。")
    blocks = [TextContent(type="text", text="\n".join(lines))]
    if image_block is not None:
        blocks.append(image_block)
    return CallToolResult(
        content=blocks,
        structuredContent={
            "object": object_name, "ra": ra_f, "dec": dec_f, "radius": radius,
            "name_resolution": resolved, "target_search": target_text,
            "query": {"mission": _resolve_mission(mission), "instrument": instrument,
                      "product_type": product_type, "start_date": start_date, "end_date": end_date},
            "source": "MAST Mashup API", "service": service,
            "shown": len(records), "results": records,
            "products": products if (include_products or preview_image) else None,
            "image_url": image_url, "image_path": image_path,
        })

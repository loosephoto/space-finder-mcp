"""DLR（ドイツ航空宇宙センター）EOC 地球観測センター STAC API（認証不要）。

ドイツ航空宇宙センター（DLR）の地球観測センター（EOC / DFD）が公開する
STAC 1.0.0 カタログから、Sentinel-2（MAJA 大気補正・WASP 合成）・EnMAP・
DESIS・Sentinel-5P/TROPOMI・MetOp/GOME-2・全球居住域（WSF）・ドイツ国内
プロダクトなどを検索する。出典: geoservice.dlr.de/eoc/ogc/stac/v1

実測（2026-10）: `GET /collections` は 69 コレクション、`POST /search` は
`numberMatched`（総件数）を返す。CQL2 の `filter` は未対応（400）なので、
雲量の絞り込みは取得後にクライアント側で行う（サーバー側フィルタを装わない）。
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, TTL_SHORT, ttl_cache, is_error_result
from .img_common import media_link_line
from .input_utils import as_int
from .stac_common import parse_bbox, parse_cloud_cover

STAC = "https://geoservice.dlr.de/eoc/ogc/stac/v1"
SOURCE = "geoservice.dlr.de/eoc/ogc/stac/v1"
UA = {"User-Agent": "space-finder-mcp/0.39 (MCP; DLR EOC STAC)"}

# よく使うコレクション（ID -> 説明）。一覧の ★ 表示に使う。
COMMON_COLLECTIONS = {
    "S2_L2A_MAJA": "Sentinel-2 L2A（MAJA 大気補正・欧州）",
    "S2_L2A_MAJA_v2": "Sentinel-2 L2A（MAJA v2）",
    "S2_L3A_WASP": "Sentinel-2 L3A（WASP 週次合成）",
    "ENMAP_HSI_L2A": "EnMAP ハイパースペクトル L2A",
    "DESIS_HSI_L2A": "DESIS（ISS 搭載）ハイパースペクトル L2A",
    "S5P_TROPOMI_L3_P1D_NO2_v2": "Sentinel-5P TROPOMI 二酸化窒素（日次）",
    "S5P_TROPOMI_L3_P1D_O3_v2": "Sentinel-5P TROPOMI オゾン（日次）",
    "S5P_TROPOMI_L3_P1D_AOD_v2": "Sentinel-5P TROPOMI エアロゾル光学厚（日次）",
    "S5P_TROPOMI_L4_P1D_NO2surf": "Sentinel-5P TROPOMI 地上 NO2 濃度（同化・日次）",
    "METOP_GOME2_L3_P1D_NO2": "MetOp GOME-2 二酸化窒素（日次・全球）",
    "WSF_2019": "世界居住域 World Settlement Footprint 2019",
    "WSF_Evolution": "世界居住域の経年変化（1975〜）",
    "TDM_DEM_90": "全球 DEM 90m（TanDEM-X）",
    "TIMELINE_AVHRR_P1M_LSTD": "AVHRR 日中の地表温度（月別・長期）",
    "LST_Germany": "ドイツ 地表面温度",
    "CROPTYPES_DE_P1Y_V02": "ドイツ 作物分類（年次）",
}


def _pick_asset(item: dict, roles: tuple = ("thumbnail",), types: tuple = ("image/",)) -> Optional[str]:
    """アイテムの assets から用途（roles / type）に合う URL を1つ選ぶ。"""
    for v in (item.get("assets") or {}).values():
        if not isinstance(v, dict) or not v.get("href"):
            continue
        item_roles = v.get("roles") or []
        mime = v.get("type") or ""
        if any(r in item_roles for r in roles) or (mime and any(mime.startswith(t) for t in types)):
            return v["href"]
    return None


@ttl_cache(TTL_DAILY, maxsize=32, skip_if=is_error_result)
def dlr_collections(limit: int = 40) -> CallToolResult:
    """DLR（ドイツ航空宇宙センター）EOC の地球観測コレクション一覧を返す（認証不要）。

    例:「ドイツの地球観測データ」「DLRのSentinel-2コレクション」
    content に表示用サマリ、structuredContent に JSON（id/説明/ライセンス）を返す。

    Args:
        limit: 返す件数（既定 40、最大 100）。
    """
    limit = as_int(limit, 40, 1, 100)
    try:
        r = requests.get(f"{STAC}/collections", headers=UA, timeout=(30, 30))
        r.raise_for_status()
        cols = r.json().get("collections", [])
    except (requests.RequestException, ValueError) as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"DLR EOC STAC への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": SOURCE},
        )
    total = len(cols)
    shown = cols[:limit]
    records = []
    for c in shown:
        cid = c.get("id", "?")
        records.append({
            "id": cid,
            "title": c.get("title", ""),
            "description": (c.get("description") or "")[:200],
            "license": c.get("license"),
            "note": COMMON_COLLECTIONS.get(cid, ""),
        })
    lines = [f"DLR（ドイツ航空宇宙センター）EOC の地球観測コレクション（全 {total} 件中、先頭 {len(records)} 件）:"]
    for i, c in enumerate(records, 1):
        mark = f"  ★ {c['note']}" if c["note"] else ""
        lines.append(f"{i}. **{c['id']}**{mark}")
        if c.get("title"):
            lines.append(f"   {c['title']}")
        if c.get("description"):
            lines.append(f"   {c['description'][:100]}")
    lines.append("出典: " + SOURCE + " ／ 検索は dlr_search ツールで。")
    lines.append("※ ライセンスはコレクションごとの DLR 申告値（CC-BY-4.0 / CC-BY-NC-4.0 / proprietary が混在）。"
                 "利用時は各コレクションの license を確認してください。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"total": total, "shown": len(records), "results": records,
                           "source": SOURCE},
    )


@ttl_cache(TTL_SHORT, maxsize=64, skip_if=is_error_result)
def dlr_search(collection: str = "S2_L2A_MAJA", bbox: Optional[str] = None,
               datetime: Optional[str] = None, max_cloud_cover: Optional[float] = None,
               limit: int = 5) -> CallToolResult:
    """DLR（ドイツ）の地球観測データを STAC で検索する（認証不要）。

    例:「ドイツのSentinel-2画像」「ベルリン周辺のEnMAPデータ」「TROPOMIの二酸化窒素」
    content に表示用サマリとプレビュー/メタデータのリンク、structuredContent に JSON
    （id/日時/雲量/bbox/プラットフォーム/処理レベル/ライセンス/アセットURL）を返す。

    Args:
        collection: STAC コレクションID（既定 "S2_L2A_MAJA"）。dlr_collections で確認可。
        bbox: 空間範囲（"lon_min,lat_min,lon_max,lat_max"）。
        datetime: 時間範囲（"YYYY-MM-DD" または "start/end"）。⚠️ DLR の API は `sortby` を
            無視する（実測: `sortby=-datetime` を送っても並びが変わらない）ため、新しい順に
            得たい場合は `datetime` で期間を指定してください。
        max_cloud_cover: 雲量上限（%）。⚠️ DLR の API はサーバー側フィルタ未対応（実測 400）のため、
            取得後にクライアント側で絞り込む（雲量情報の無いアイテムは除外し、件数を返す）。
        limit: 返す件数（既定 5、最大 10）。
    """
    limit = as_int(limit, 5, 1, 10)
    body: dict = {"limit": limit, "collections": [collection]}
    bbox_vals, bbox_err = parse_bbox(bbox)
    if bbox_err:
        return CallToolResult(
            content=[TextContent(type="text", text=bbox_err)],
            structuredContent={"error": "bad bbox", "bbox": bbox},
        )
    if bbox_vals:
        body["bbox"] = bbox_vals
    if datetime:
        body["datetime"] = datetime
    cloud, cloud_err = parse_cloud_cover(max_cloud_cover)
    if cloud_err:
        return CallToolResult(
            content=[TextContent(type="text", text=cloud_err)],
            structuredContent={"error": "bad max_cloud_cover", "max_cloud_cover": max_cloud_cover},
        )
    # 雲量で絞るときは多めに取ってから選ぶ（サーバー側フィルタが無いため）
    if cloud is not None:
        body["limit"] = min(max(limit * 4, limit), 40)
    try:
        r = requests.post(f"{STAC}/search", headers=UA, json=body, timeout=(35, 35))
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"DLR EOC STAC 検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": SOURCE},
        )
    matched = d.get("numberMatched")
    feats = d.get("features", []) or []
    fetched = len(feats)
    without_cloud = 0
    if cloud is not None:
        kept = []
        for it in feats:
            cc = (it.get("properties") or {}).get("eo:cloud_cover")
            if cc is None:
                without_cloud += 1
                continue
            try:
                if float(cc) <= cloud:
                    kept.append(it)
            except (TypeError, ValueError):
                without_cloud += 1
        feats = kept
    feats = feats[:limit]
    if not feats:
        msg = f"コレクション '{collection}' に条件一致のデータがありませんでした。"
        if cloud is not None:
            msg += f"（雲量 {cloud}% 以下で絞り込んだ結果 0 件。取得 {fetched} 件・雲量情報なし {without_cloud} 件）"
        return CallToolResult(
            content=[TextContent(type="text", text=msg)],
            structuredContent={"collection": collection, "matched": matched, "total": 0,
                               "results": [], "source": SOURCE},
        )
    records = []
    lines = [f"DLR {collection} の地球観測データ（表示 {len(feats)} 件／条件一致 全 {matched} 件）:"]
    for i, it in enumerate(feats, 1):
        props = it.get("properties") or {}
        rec = {
            "id": it.get("id", ""),
            "datetime": (props.get("datetime") or props.get("start_datetime") or "")[:19],
            "cloud_cover": props.get("eo:cloud_cover"),
            "platform": props.get("platform"),
            "instruments": props.get("instruments"),
            "processing_level": props.get("processing:level"),
            "gsd_m": props.get("gsd"),
            "license": props.get("license"),
            "bbox": it.get("bbox"),
            "collection": it.get("collection") or collection,
            "thumbnail_url": _pick_asset(it, ("thumbnail",), ("image/",)),
            "metadata_url": _pick_asset(it, ("metadata",), ("application/xml", "application/json")),
        }
        records.append(rec)
        cc = rec["cloud_cover"]
        cc_s = f" 雲量 {cc}%" if cc is not None else ""
        extra = []
        if rec["platform"]:
            extra.append(str(rec["platform"]))
        if rec["processing_level"]:
            extra.append(str(rec["processing_level"]))
        lines.append(f"{i}. **{str(rec['id'])[:48]}**  {rec['datetime']} UTC{cc_s}"
                     + ("  " + " / ".join(extra) if extra else ""))
        if rec["bbox"]:
            lines.append(f"   領域 bbox: {[round(float(x), 2) for x in rec['bbox'][:4]]}")
        if rec["thumbnail_url"]:
            lines.append(media_link_line(f"{str(rec['id'])[:40]} のサムネイル",
                                         url=rec["thumbnail_url"], kind="image"))
        if rec["metadata_url"]:
            lines.append(media_link_line(f"{str(rec['id'])[:40]} のメタデータ",
                                         url=rec["metadata_url"], kind="file"))
    lines.append("出典: " + SOURCE + "（DLR Earth Observation Center / ドイツ航空宇宙センター）")
    lines.append("※ ライセンスはアイテム/コレクションの DLR 申告値です（CC-BY-4.0 / CC-BY-NC-4.0 / proprietary が混在）。"
                 "実データの取得は各コレクションの条件に従います。")
    lines.append("※ DLR の STAC は `sortby` を無視するため（実測）、並び順は日時順とは限りません。"
                 "期間を絞るには `datetime` を指定してください。")
    if cloud is not None:
        lines.append(f"※ 雲量 {cloud}% 以下の絞り込みは DLR の API がサーバー側フィルタ非対応（実測 400）のため、"
                     f"取得した {fetched} 件からクライアント側で行いました（除外 {fetched - len(feats) - without_cloud} 件・"
                     f"雲量情報なし {without_cloud} 件）。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"collection": collection, "matched": matched,
                           "returned": d.get("numberReturned"), "fetched": fetched,
                           "shown": len(records),
                           "excluded_by_cloud": (fetched - len(feats) - without_cloud) if cloud is not None else 0,
                           "without_cloud_info": without_cloud,
                           "results": records, "source": SOURCE},
    )

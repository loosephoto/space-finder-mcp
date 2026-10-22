"""汎用・天体面ローバー位置マップ — MMGIS位置データ + NASA Trek ベースマップ（認証不要）。

月面・火星面等の探査ローバー（地表を移動する探査車）の現在地・走行経路・着陸地点を、
その天体の局所地図に重ねて画像化する。planetary_orbiter_track（軌道周回機）とは別に、
「地表を移動するローバー」を扱う。

ベースマップ（タイル・天体テーブル）とタイル合成・フォントは planetary_map を再利用。
ローバーの現在位置データは各ローバーのデータ源（NASA MMGIS 等）から取得する。

※ 正直な制約: 現在、走行経路まで取得できるローバー位置データは NASA MMGIS（火星）のみ。
月面ローバー等の現役位置データは公開されていない。そのため実データは火星ローバー
（Perseverance / Curiosity）のみ。構造は天体・データ源を追加可能な汎用設計にしている。

MMX（フォボス探査）: 2026-10-20（JST）に打ち上げられた JAXA の MMX 探査機と、
DLR/CNES が **MMX のコスト削減のために特別に設計した**小型ローバー
**IDEFIX（イデフィクス）**。IDEFIX は 2028 年度末〜2029 年頭にフォボスへ着陸する
（MMX 本体は 2030 年頃に着陸して 10 g の試料を採取し、2031 年に地球へ持ち帰る）。
着陸地点は 2027〜2028 年に選定されるため**未公表**。
そのため本モジュールは
  - 「着陸地点の公表値を待つ」のではなく `site_lat`/`site_lon`（または `site_lon_w`）で
    任意の地点の座標を**着陸予定点として**描けるようにし、
  - IAU/U.S. Gazetteer（一次情報）が着陸地点を公表したらそれを**自動で拾う**経路と、
  - 選定条件として公表されている**緯度帯（±30°）**を「制約帯」として描けるようにした。
座標は**東向き正（本リポジトリの内部規約）**で保持し、フォボス固有の西向き正（IAU 表記）
は `site_lon_w` で受け付ける。

出典: 位置 = NASA MMGIS (mars.nasa.gov) ／ 地図 = NASA Trek (WMTS 等角図法) ／
フォボス = USGS 公式全球等角図 + IAU/U.S. Gazetteer of Planetary Nomenclature
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

# planetary_map の汎用コアと画像共通ヘルパーを再利用
from .planetary_map import BODIES
from .surface_map import (draw_marker, fit_font_for_width, marker_scan_radius,
                         panel_placement, surface_view, tile_failure_note,
                         verify_marker)
from .img_common import (figure_notes, figure_payload, figure_text_block,
                         load_font, media_link_line, primary_spec,
                         save_output, scale_spec, view_spec)
from .input_utils import as_float, as_int

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
# 画像・配布物ホスト（Wikimedia）は短時間の連続取得を制限するので識別可能な UA を付ける
BASEMAP_UA = {"User-Agent": "space-finder-mcp/0.39 (planetary basemap; contact: dev)"}

# ====================================================================
# ローバーテーブル — 新規ローバーはここに1行追加するだけ
#   body: 周回天体（planetary_map.BODIES のキー、ベースマップ・言語を決定）
#   pos:  現在位置データ源。dict: {type:"mmgis", id:<ミッションID>} など
#   land_ja: 着陸地点（日本語）
# ====================================================================
ROVERS: dict[str, dict] = {
    "perseverance": {
        "body": "mars", "ja": "パーサヴィアランス", "en": "Perseverance",
        "land_ja": "ジェゼロ・クレーター",
        "pos": {"type": "mmgis", "id": "M20"},
    },
    "curiosity": {
        "body": "mars", "ja": "キュリオシティ", "en": "Curiosity",
        "land_ja": "ゲール・クレーター",
        "pos": {"type": "mmgis", "id": "MSL"},
    },
    # MMX 同梱ローバー IDEFIX（DLR/CNES 開発・製造）。2028 年度末〜2029 年頭にフォボス着陸予定。
    # 着陸地点は未公表（2027〜2028 年に選定）。座標は IAU/U.S. Gazetteer 公表値を待つ。
    "idefix": {
        "body": "phobos", "ja": "イデフィクス（IDEFIX）", "en": "IDEFIX",
        "land_ja": "フォボス（着陸地点は未公表）",
        "pos": {"type": "iau_landing_site", "target": "PHOBOS"},
    },
}

# 月面ローバー（site_plan.py の一次情報データから読んで ROVERS と同じ形にする）。
# 走行経路が機械可読で公開されているのは「最終駐車地点」だけなので、描けるのは
# 公開されている端点間（LM → 最終駐車地点）に限る（全体は推測で埋めない）。
LUNAR_ROVER_KEYS = {
    # LRV を搭載したアポロ15〜17〜17
    "apollo15": "LRV（Apollo 15）",
    "apollo16": "LRV（Apollo 16）",
    "apollo17": "LRV（Apollo 17）",
    # LRV は無いが LM / 旗 / ALSEP / LRRR の座標・公開値がある
    "apollo11": "アポロ11号（LRV なし・旗と LRRR）",
    "apollo12": "アポロ12号（LRV なし・旗）",
    "apollo14": "アポロ14号（LRV なし・旗と LRRR）",
    # ソ連・中国の月面車
    "lunokhod1": "ルノホート1号",
    "lunokhod2": "ルノホート2号",
    "yutu": "玉兔号（嫦娥3号）",
    "yutu2": "玉兔2号（嫦娥4号）",
}

# ベースマップの種類: "trek"（タイル合成）か "image"（1枚の全球等角図）か
ROVER_BODIES: dict[str, dict] = {
    "mars": {"ja": "火星", "en": "Mars", "basemap": "trek"},
    "phobos": {"ja": "フォボス", "en": "Phobos", "basemap": "image"},
    "moon": {"ja": "月", "en": "Moon", "basemap": "trek"},
}

# ====================================================================
# フォボス（MMX の着陸天体）のベースマップ設定
#   image: USGS 公式の全球等角図（Shaded Relief）。
#     左端は西経 180°（＝東経 180°）で、このリポジトリの投影前提
#     （左端 = -180°E・右へ東へ増加）と一致するため**横回転は不要**。
#     ※ 印字された目盛ラベルは「西向き正」の大きさで並ぶが、地図自体は
#       標準の東向き正（経度が右へ増加）。IAU 地名 20 点の実測で検証済み。
#   crop: 配布用 PNG は地形ラスタの外に白地の余白・題字・目盛ラベル・出典表記がある。
#     地形だけの範囲を切り出して使う（実測: 5499x2999 px のうち地形は
#     cols 249..5250 / rows 248..2750）。
#   lat_band: MMX/IDEFIX の着陸地点選定条件として公表されている緯度制約（±30°）。
#     **着陸地点そのものではない**（地点は未公表）。
# ====================================================================
PHOBOS_BASEMAP = {
    # Special:FilePath は 429（レート制限）が出にくい経路として使う。
    # 実測: upload.wikimedia.org への直接リンクは同一ファイルでも 429 になるが、
    # commons.wikimedia.org/wiki/Special:FilePath は 200 で取れる
    # （width=2400 を付けると 3840x2094 px のサムネイルが返る）。
    "url": "https://commons.wikimedia.org/wiki/Special:FilePath/USGS-Phobos-MarsMoon-Map.png?width=2400",
    "credit": "USGS 公式全球等角図（Shaded Relief / Phobos）",
    "left_edge_lon": -180.0,
    "crop": (249 / 5499.0, 248 / 2999.0, 5251 / 5499.0, 2751 / 2999.0),
    "lat_band_deg": 30.0,
}

# IAU/U.S. Gazetteer の機械可読な地名-feed（フォボスの地名中心点）。
# 着陸地点（type に "Landing Site" が含まれる）が公表されたら自動で拾う。
IAU_NOM_KMZ = ("https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/"
               "{target}_nomenclature_center_pts.kmz")
# Gazetteer の center_lon は「0〜360 の東向き正」（実測: Stickney=311 は 49°W、
# Wikipedia/IAU Feature 5707 の 1°N 49°W と一致、min_lon=290/max_lon=332 の
# WKT とも整合）。西向き正（+West 0-360）で公有値を出す場合は 360 - center_lon。
IAU_LON_EAST_POSITIVE = True


def _mmgis_waypoint(mid: str) -> Optional[dict]:
    """MMGIS からローバー現在地(最終waypoint)を取得。{lat, lon, sol, dist_km, rmc}"""
    url = f"https://mars.nasa.gov/mmgis-maps/{mid}/Layers/json/{mid}_waypoints_current.json"
    try:
        r = requests.get(url, headers=UA, timeout=(25, 25))
        r.raise_for_status()
        f = r.json().get("features", [{}])[0].get("properties", {})
        return {
            "lat": float(f["lat"]), "lon": float(f["lon"]),
            "sol": f.get("sol"), "dist_km": f.get("dist_km"),
            "rmc": f.get("RMC"), "note": f.get("Note", ""),
        }
    except (requests.RequestException, KeyError, ValueError) as e:
        return None


def _mmgis_route(mid: str) -> Optional[list]:
    """MMGIS からローバーの走行経路(全waypoint: (lon,lat) 列)を取得。"""
    url = f"https://mars.nasa.gov/mmgis-maps/{mid}/Layers/json/{mid}_waypoints.json"
    try:
        r = requests.get(url, headers=UA, timeout=(30, 30))
        r.raise_for_status()
        return [(f["properties"]["lon"], f["properties"]["lat"])
                for f in r.json().get("features", [])
                if "lat" in f["properties"] and "lon" in f["properties"]]
    except (requests.RequestException, KeyError, ValueError):
        return None


def _iau_landing_sites(target: str = "PHOBOS") -> Optional[list]:
    """IAU/U.S. Gazetteer の地名 feed から着陸地点（type に Landing を含む）を抽出する。

    戻り: [{"name", "lat", "lon", "lon_w", "diameter", "link"}...]（無ければ空リスト）
    取得失敗は None（=「判定できない」であって「着陸地点が無い」ではない）。
    """
    import io as _io
    import re
    import zipfile
    from .cache import disk_get
    url = IAU_NOM_KMZ.format(target=target.upper())
    try:
        data = disk_get(url, subdir="iau_nomenclature", timeout=60, headers=BASEMAP_UA)
    except Exception:
        return None
    if not data:
        return None
    try:
        z = zipfile.ZipFile(_io.BytesIO(data))
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        kml = z.read(name).decode("utf-8", "replace")
    except Exception:
        return None
    out = []
    for blk in re.findall(r"<Placemark.*?</Placemark>", kml, re.S):
        nm = re.search(r"<name>(.*?)</name>", blk, re.S)
        if not nm:
            continue
        fields = dict(re.findall(r'<SimpleData name="([^"]+)">(.*?)</SimpleData>', blk, re.S))
        typ = (fields.get("type") or "")
        if "landing" not in typ.lower():
            continue

        def _f(k):
            try:
                return float(fields.get(k, ""))
            except (TypeError, ValueError):
                return None
        lon = _f("center_lon")
        lat = _f("center_lat")
        if lon is None or lat is None:
            continue
        # Gazetteer の center_lon は 0〜360 の東向き正。内部規約も同じ（東向き正）。
        lon_e = lon if IAU_LON_EAST_POSITIVE else (360.0 - lon) % 360.0
        out.append({"name": nm.group(1).strip(), "lat": lat, "lon": lon_e,
                    "lon_w": (360.0 - lon_e) % 360.0,
                    "diameter": _f("diameter"), "type": typ,
                    "link": fields.get("link", "")})
    return out




def _fmt_lon(lon_e: float) -> str:
    """経度を「東経/西経」の日本語表記にする（本リポジトリの内部値は東向き正）。"""
    v = ((float(lon_e) + 180.0) % 360.0) - 180.0
    if abs(v) < 1e-9:
        return "経度 0°（子午線）"
    return ("東経 {:.4f}°" if v > 0 else "西経 {:.4f}°").format(abs(v))



def _lunar_rover_result(site: dict, rv: str, rover_ja: str, body_cfg: dict,
                        *, zoom: Optional[int] = None, span_deg: float = 1.0,
                        out_px: int = 1200) -> CallToolResult:
    """月面ローバーの 2 段構え図（位置図 + 局地サイトプラン）を返す。

    位置図 = 天体面地図（等角図法、着陸地点と近隣の着陸地点）
    局地サイトプラン = 一次情報が公開する距離＋方位から求めたメートル単位の配置
    """
    from . import site_plan as _sp
    from .planetary_map import BODIES as _BODIES

    body_tiles = _BODIES.get("moon")
    if body_tiles is None:
        return CallToolResult(
            content=[TextContent(type="text", text="月面のタイル設定がありません。")],
            structuredContent={"error": "no tile config", "body": "moon"})

    lat0, lon0 = _sp.site_center(site)
    lon0 = ((float(lon0) + 180.0) % 360.0) - 180.0

    # 近隣の着陸地点（同じ天体の別ミッション。planetary_map の SITES = 一次情報）
    nearby = []
    try:
        from .planetary_map import SITES as _SITES
        for key, st in (_SITES.get("moon") or {}).items():
            if key in ("mcgetchin", "falcon9"):
                continue
            nearby.append({"ja": st.get("ja", key), "lat": st["lat"], "lon": st["lon"],
                           "key": key})
    except Exception:
        pass

    # 位置図の画角: 近隣の着陸地点が収まるように決める。
    # 例: アポロ12号の LM とサーベイヤー3号は約 0.4 km 離れている（同一クレーター内）。
    # 近傍の着陸地点があればそれを、なければ既定値（1°）を使う。
    sp = max(0.05, float(span_deg))
    _near = []
    for nb in nearby:
        _dlon = abs(((float(nb["lon"]) - lon0 + 180.0) % 360.0) - 180.0)
        _dlat = abs(float(nb["lat"]) - lat0)
        # **自分自身は近傍から除外する**（含めると画角が 0 に潰れる。実測）。
        if _dlon < 1e-4 and _dlat < 1e-4:
            continue
        if _dlon < 1.0 and _dlat < 1.0:
            _near.append(nb)
    if _near:
        # 近傍の着陸地点がすべて収まる最小の画角（余裕 1.6 倍・下限 0.05°・上限 5°）
        _need = max(max(abs(((float(n["lon"]) - lon0 + 180.0) % 360.0) - 180.0),
                         abs(float(n["lat"]) - lat0)) for n in _near) * 1.6
        sp = max(0.05, min(_need, 5.0))
    nearby = _near

    # ズームは画角に合わせる（0.05° のような狭い画角で zoom 6 だと地形が 1 画素に
    # 潰れて地図が無地味になる。実測）。月面の Trek は 1 タイル 0.703125° なので、
    # 画角 / 0.703125 がその画角を満たす最小タイル段になる。
    z = as_int(zoom, None, 4, body_tiles["maxzoom"])
    if z is None:
        _sp0 = max(0.05, float(span_deg))
        z = 0
        while z < body_tiles["maxzoom"] and (360.0 / (2 ** (z + 1))) > _sp0:
            z += 1
        z = max(4, min(int(z), body_tiles["maxzoom"]))

    try:
        comp = _sp.render_lunar_site_figure(
            site, body_cfg=body_tiles, zoom=z, span_deg=sp, out_px=out_px,
            radius_m=_sp.MOON_RADIUS_M, nearby=nearby)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"月面遺跡の図の生成に失敗: {e}")],
            structuredContent={"error": str(e), "rover": rv})

    img = comp["img"]
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    jpeg = buf.getvalue()
    out_path = save_output(jpeg, "planetary_rover_location_map", "jpg")
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg",
                        altText="{} の月面遺跡マップ".format(site.get("ja", rv)))

    plan_res = comp["plan_res"]
    drawn = plan_res["drawn"]
    not_drawn = plan_res["not_drawn"]

    # --- 注記（数値から生成する）---
    notes = [
        "**左が位置図、右が局地サイトプラン**（メートル単位・北が上・縮尺バーつき）。"
        "等角図法の天体面地図は 1px あたり数百 m〜20 km なので、"
        "数十 m から数百 m 離れた機材は、どのズームでも 1 画素に収まらない。"
        "そのため公開値から局地座標を計算して別パネルに描いている",
        "局地プランの位置は**一次情報が公開する値だけ**から求めた。"
        "距離・方位の公開値がない機材は凡例に「位置の公開値なし」と記して**図には描かない**",
        "LM / ALSEP / LRRR の座標は NASA NSSDCA「Apollo Landing Site Coordinates」"
        "（planetocentric Mean Earth/Polar Axis・DE421・Wagner et al., Icarus 283 (2017)）",
        "旗・LRV 最終駐車地点は NASA Apollo Lunar Surface Journal（ALSJ）",
    ]
    if drawn:
        for a in drawn:
            if a.get("kind") == "rover":
                notes.append("橙●の{}は{}。LM からの{}".format(
                    "LRV 最終駐車地点",
                    "ALSJ が公開した端点（LM から {}・方位 {} 度）".format(
                        _sp.fmt_m(a["dist_m"]), a.get("az_deg")),
                    "直線で結んだのは公開されている端点間だけで、パス全体ではない"))
                break
    for a in not_drawn:
        notes.append("{}: {}".format(a.get("ja", ""), a.get("note", "")))
    if site.get("note"):
        notes.append(site["note"])
    if site.get("dist_m") and not any(a.get("kind") == "rover" for a in drawn):
        notes.append("このローバーの走行距離は {}。経路の座標が機械可読で公開されておらず、"
                     "距離のみ.".format(_sp.fmt_m(site["dist_m"])))
    notes.append(comp.get("tile_failure") or "")

    caption = (f"{site.get('ja', rv)}（{rv}）の月面遺跡。着陸 {site.get('landed', '')}・"
               f"座標 {lat0:.4f}°N / {lon0:.4f}°E。")

    fig = figure_payload(
        kind="ground_track_map",
        title="{} の月面遺跡マップ（{}）".format(site.get("ja", rv), body_tiles["attrib"]),
        view=view_spec("body_surface", "equirectangular",
                       "左: 月面地図（等角図法）に着陸地点と近隣の着陸地点。"
                       "右: 一次情報が公開する距離＋方位から求めた局地サイトプラン（m・北が上）",
                       why="月面の機材は 8〜165 m の範囲に収まり等角図法では 1 画素に収まらないため、"
                           "位置図と局地計画を分けて示す"),
        primary=primary_spec("月", "surface_map",
                             note="天体面の地図なので主天体は図の中心に置いていない"),
        scale=scale_spec("linear", to_scale=True, unit="m",
                         exaggerated=["マーカーの大きさは実寸ではない",
                                      "左の位置図は縮尺指定なし（等角図法）"]),
        markers=[{"id": a["id"], "label": a.get("ja", a["id"]),
                  "east_m": round(a["east_m"], 2), "north_m": round(a["north_m"], 2),
                  "kind": a.get("kind"), "position_status": a.get("position_status"),
                  "derived": bool(a.get("derived"))}
                 for a in drawn],
        notes=notes,
        caption=caption,
        verify={"ok": bool(drawn) or bool(not_drawn) or bool(site.get("dist_m")),
                "site_plan_drawn": len(drawn),
                "site_plan_not_drawn": len(not_drawn),
                "site_plan_has_local_markers": bool(drawn),
                "scale_bar_m": plan_res["scale_bar_m"],
                "px_per_m": round(plan_res["px_per_m"], 4),
                "note": "局地サイトプランの機材数と縮尺（位置図側は `--figures` の "
                        "basemap 色数検査と同じ扱い）"},
    )
    lines = [
        media_link_line("{} の月面遺跡マップ".format(site.get("ja", rv)),
                        path=out_path, kind="figure"),
        f"🔴 **{site.get('ja', rv)}（{rv}）の月面遺跡マップ**:",
        f"📍 座標: {lat0:.4f}° {'N' if lat0 >= 0 else 'S'} / {lon0:.4f}°E ・ 着陸 {site.get('landed', '')}",
        f"🗺 局地サイトプラン: 機材 {len(drawn)} 点を描画（縮尺バー {plan_res['scale_bar_m']:.0f} m）",
    ]
    if not_drawn:
        lines.append("⚠️ 位置の公開値がない機材: " + "、".join(a.get("ja", "") for a in not_drawn))
    lines += ["", figure_text_block(fig),
              "出典: NASA NSSDCA（着陸地点座標）／NASA Apollo Lunar Surface Journal（旗・LRV 駐車地点）"
              "／NASA Trek LRO WAC（地図）"]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={"body": "moon", "rover": rv, "rover_ja": rover_ja,
                           "lat": lat0, "lon": lon0,
                           "site_ja": site.get("ja"),
                           "site_en": site.get("en"),
                           "place": site.get("place"),
                           "landed": site.get("landed"),
                           "site_plan_markers": len(drawn),
                           "site_plan_markers_without_position": [a.get("ja") for a in not_drawn],
                           "scale_bar_m": plan_res["scale_bar_m"],
                           "zoom": z, "span_deg": sp,
                           "figure": fig, "image_path": out_path,
                           "source": "NASA NSSDCA + Apollo Lunar Surface Journal + NASA Trek"},
    )

def planetary_rover_location_map(body: str = "mars", rover: str = "perseverance",
                                 zoom: Optional[int] = None, span_deg: float = 0.5,
                                 out_px: int = 1000, site_lat: Optional[float] = None,
                                 site_lon: Optional[float] = None,
                                 site_lon_w: Optional[float] = None,
                                 show_lat_band: bool = True,
                                 show_sites: bool = True) -> CallToolResult:
    """任意の天体面を移動する探査ローバーの現在地を、その天体の地図上に示した画像を返す（認証不要）。

    例:「パーサヴィアランスの現在地を火星地図で」「キュリオシティが今どこにいるか地図で」
    「MMX の IDEFIX ローバーはフォボスのどこに着陸するのか」
    「アポロ15号の月面遺跡の地図」で呼びます。

    火星ローバーは NASA MMGIS の現在地・走行経路を取得し、NASA Trek の等角地図に
    走行経路(橙線)・現在地(赤●)・着陸地点(青●)を合成して描画します。
    同じ天体にある**ほかの人工物着陸地点**は黄枠●で重ねます
    （例: 火星のマルス2/3号・バイキング、月ではサーベイヤー3号など）。

    **月面ローバー**（アポロ11〜17号の LRV、ルノホート1/2号、玉兔号・玉兔2号）は
    2 段構えの図で描きます。
    1. 左: NASA Trek の月面地図（等角図法）に着陸地点と近隣の着陸地点
    2. 右: **局地サイトプラン**（メートル単位・北が上・縮尺バーつき）
       LM・ALSEP・LRRR・LRV 最終駐車地点・アメリカ国旗を、一次情報が公開する
       「距離＋方位」だけから求めた位置で描きます。
    等角図法の天体面地図は 1px あたり数百 m〜20 km なので、数十 m 離れていても
    月面機材はどのズームでも 1 画素に収まりません。だから位置図と局地計画を分けます。
    位置の公開値がない機材（例: アポロ11号の旗）は**図には描かず**、
    凡例に「位置の公開値なし」と書いてテキストで伝えます。

    **フォボス（MMX / IDEFIX）**は着陸地点がまだ未公表（2027〜2028 年に選定）のため、
    現時点では「制約帯（緯度 ±30°）」と、地図上の任意の場所を指定する
    `site_lat`/`site_lon`（または西向き正の `site_lon_w`）で描きます。
    IAU/U.S. Gazetteer に着陸地点が公表されれば自動で検出します。

    Args:
        body: 天体名（既定 "mars"。"mars" / "phobos" / "moon"）。
        rover: ローバー名（"perseverance"=既定, "curiosity", "idefix"、
            "apollo11"〜"apollo17", "lunokhod1", "lunokhod2", "yutu", "yutu2"）。
        zoom: 地図ズーム（タイル系のみ）。省略時は天体の最大ズーム。
        span_deg: 表示する画角(経度幅・度)。大きいほど広範囲・解像度低下。既定0.5。
        out_px: 出力画像の長辺ピクセル(既定1000, 最大2000)。
        site_lat: 着陸地点の纬度(度・北が正)。指定するとその地点を「着陸予定点」として描く。
        site_lon: 着陸地点の経度(度・**東向き正**)。`site_lon_w` とは排他。
        site_lon_w: 着陸地点の経度(度・**西向き正**。IAU/フォボスの慣用表記)。
        show_lat_band: 着陸地点の緯度制約帯（フォボスでは ±30°）を描くか。既定 True。
        show_sites: 同じ天体にある**ほかの人工物の着陸地点**を地図に重ねて描くか。既定 True。
            例: 火星ではマルス2/3号・バイキング・フェニックスなど、月では
            サーベイヤー3号（Apollo 12 号の LM のすぐ隣）など。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は %LOCALAPPDATA%\\Temp\\space_finder_mcp\\out に
    保存し、同じパスを structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    """
    from PIL import ImageDraw          # 画像の生成は surface_map 側で行う

    b = str(body).strip().lower()
    if b not in ROVER_BODIES:
        return CallToolResult(
            content=[TextContent(type="text", text=f"不明な天体 '{body}'。対応: {', '.join(sorted(ROVER_BODIES.keys()))}")],
            structuredContent={"error": "unknown body", "body": body,
                               "known": sorted(ROVER_BODIES.keys())},
        )
    body_cfg = ROVER_BODIES[b]

    rv = str(rover).strip().lower()
    # 月面ローバーは site_plan.py のデータから解決する（ROVERS には無い）
    lunar_site = None
    if rv in LUNAR_ROVER_KEYS:
        from . import site_plan as _sp
        _entries = _sp.lunar_rover_entries()
        if rv not in _entries:
            return CallToolResult(
                content=[TextContent(type="text", text=f"月面ローバー '{rv}' のデータがありません。")],
                structuredContent={"error": "unknown lunar rover", "rover": rv},
            )
        lunar_site = _entries[rv]
    if rv not in ROVERS and lunar_site is None:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "対応ローバー: " + ", ".join(sorted(list(ROVERS.keys()) + list(LUNAR_ROVER_KEYS.keys())))
                + "（位置データが公開されている、または着陸予定の表示ができるローバー）"))],
            structuredContent={"error": "unsupported rover",
                               "supported": sorted(list(ROVERS.keys()) + list(LUNAR_ROVER_KEYS.keys()))},
        )
    if lunar_site is not None:
        if b != "moon":
            return CallToolResult(
                content=[TextContent(type="text", text=f"'{rv}'（{LUNAR_ROVER_KEYS[rv]}）は月のローバーです。天体 '{body}' と一致しません。")],
                structuredContent={"error": "body mismatch", "rover": rv, "body": body},
            )
        rcfg = {"body": "moon", "ja": LUNAR_ROVER_KEYS[rv], "en": lunar_site.get("en", rv),
                "land_ja": lunar_site.get("place", "")}
    else:
        rcfg = ROVERS[rv]
        if rcfg["body"] != b:
            return CallToolResult(
                content=[TextContent(type="text", text=f"'{rv}'（{rcfg['ja']}）は {ROVER_BODIES[rcfg['body']]['ja']} のローバーです。天体 '{body}' と一致しません。")],
                structuredContent={"error": "body mismatch", "rover": rv, "body": body},
            )
    rover_ja = rcfg["ja"]

    # ================= 月面ローバー =================
    # 月面の各機材は 8〜165 m の範囲に収まるが、等角図法の天体面地図は
    # 1px あたり数百 m〜20 km なので**どのズームでも 1 画素に収まらない**。
    # そのため (1) 位置図 と (2) メートル単位の局地サイトプラン の 2 枚を合成して 1 枚にする。
    if lunar_site is not None:
        return _lunar_rover_result(lunar_site, rv, rover_ja, body_cfg,
                                   zoom=zoom, span_deg=span_deg, out_px=out_px)

    # 座標指定の相互排他チェック（東向き正と西向き正を同時に受けない）
    site_lon = as_float(site_lon, None, -360.0, 360.0)
    site_lon_w = as_float(site_lon_w, None, -360.0, 360.0)
    site_lat = as_float(site_lat, None, -90.0, 90.0)
    if site_lon is not None and site_lon_w is not None:
        return CallToolResult(
            content=[TextContent(type="text", text="site_lon（東向き正）と site_lon_w（西向き正）は同時に指定できません。どちらか一方を指定してください。")],
            structuredContent={"error": "conflicting longitude", "rover": rv},
        )
    if site_lat is not None and (site_lon is None and site_lon_w is None):
        return CallToolResult(
            content=[TextContent(type="text", text="site_lat を指定したら site_lon（東向き正）または site_lon_w（西向き正）も指定してください。")],
            structuredContent={"error": "incomplete site coordinates", "rover": rv},
        )
    # 内部値は東向き正に揃える
    if site_lon_w is not None:
        site_lon = (360.0 - site_lon_w) % 360.0
    span_deg = as_float(span_deg, 0.5, 0.05, 30.0)
    out_px = as_int(out_px, 1000, 300, 2000)

    # ---- 位置データ（データ源ごと）----
    ptype = rcfg["pos"]["type"]
    wp = None
    route = None
    landing_site = None          # {"lat", "lon", "name", "source"}
    published_status = None      # "published" / "not_published" / "unknown" / "user_specified"

    if ptype == "mmgis":
        body_tiles = BODIES.get(b)
        if body_tiles is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"天体 '{b}' のタイル設定がありません。")],
                structuredContent={"error": "no tile config", "body": b},
            )
        zoom = as_int(zoom, None, 4, body_tiles["maxzoom"])
        if zoom is None:
            zoom = body_tiles["maxzoom"]
        mid = rcfg["pos"]["id"]
        wp = _mmgis_waypoint(mid)
        route = _mmgis_route(mid)
        if not wp:
            return CallToolResult(
                content=[TextContent(type="text", text="ローバーの現在地データを取得できませんでした。")],
                structuredContent={"error": "position fetch failed", "rover": rv},
            )
        clat, clon = wp["lat"], wp["lon"]
    elif ptype == "iau_landing_site":
        # 着陸地点の公表を IAU/U.S. Gazetteer（一次情報）から自動検出する。
        # 公表されていればそれを、していなければ利用者が渡した座標を使う。
        sites = _iau_landing_sites(rcfg["pos"].get("target", "PHOBOS"))
        if sites is None:
            published_status = "unknown"
        elif sites:
            published_status = "published"
            s0 = sites[0]
            landing_site = {"lat": float(s0["lat"]), "lon": float(s0["lon"]),
                            "name": s0["name"], "source": s0.get("link") or "IAU/U.S. Gazetteer",
                            "lon_w": s0.get("lon_w")}
            clat, clon = landing_site["lat"], landing_site["lon"]
        else:
            published_status = "not_published"
        # 利用者が明示した座標は公表値より優先する（公表済みの位置を確認したい用途）。
        if site_lat is not None and site_lon is not None:
            clat, clon = site_lat, site_lon
            landing_site = {"lat": clat, "lon": clon, "name": "指定された地点",
                            "source": "利用者が指定した座標"}
            published_status = "user_specified"
        elif published_status != "published":
            # 座標が未公表で指定もない: 制約帯だけの図を描く（フォボス全体が見える縮尺）
            clat, clon = 0.0, 0.0
    else:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ローバー '{rv}' のデータ源 '{ptype}' には未対応です。")],
            structuredContent={"error": "unsupported data source", "rover": rv, "type": ptype},
        )

    # ---- ベースマップ（タイル合成 or 1枚の全球等角図）----
    mode = body_cfg.get("basemap", "trek")
    # 投影は -180〜180° を前提にするので、経度は必ず折り返してから使う
    # （0〜360°E の公表値 311° をそのまま渡すと画像の外へ出る。実測）。
    clat = float(clat)
    clon = ((float(clon) + 180.0) % 360.0) - 180.0


    if mode == "trek":
        # ---- タイル合成・切り出し・投影は共通ルーチン（surface_map）----
        try:
            sv = surface_view(body_tiles, clon, clat, span_deg, zoom, out_px,
                              regional_dim=None)      # ローバー図は減光しない（従来の見た目を維持）
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"{body_cfg['ja']}地図タイル取得に失敗: {e}")],
                structuredContent={"error": str(e)},
            )
        attrib = body_tiles["attrib"]
    else:
        # 1枚の全球等角図（フォボス）
        from .surface_map import image_view
        img_cfg = PHOBOS_BASEMAP
        try:
            sv = image_view(img_cfg["url"], clon, clat, 360.0, out_px,
                            credit=img_cfg.get("credit", ""), headers=BASEMAP_UA,
                            whole_dim=0.88, regional_dim=0.88,
                            left_edge_lon=img_cfg.get("left_edge_lon"),
                            crop=img_cfg.get("crop"))
        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"{body_cfg['ja']}の全球地図の取得に失敗: {e}")],
                structuredContent={"error": str(e), "source": img_cfg.get("credit", "")},
            )
        zoom = None
        attrib = img_cfg.get("credit", "全球等角図")

    img = sv["img"]
    g2px = sv["g2px"]
    iw, ih = img.size

    d = ImageDraw.Draw(img)
    # フォボス: 着陸地点の緯度制約帯（±30°）を「制約」として描く。
    # 着陸地点そのものではない（地点は未公表）ことを凡例と注記で明示する。
    band_drawn = False
    band_deg = float(PHOBOS_BASEMAP.get("lat_band_deg") or 0.0)
    band_y = (None, None)
    if mode == "image" and show_lat_band and band_deg > 0:
        y_n = g2px(0.0, band_deg)[1]
        y_s = g2px(0.0, -band_deg)[1]
        if 0 <= y_n <= ih and 0 <= y_s <= ih:
            d.rectangle([0, min(y_n, y_s), iw - 1, max(y_n, y_s)],
                        outline=(90, 230, 190), width=3)
            band_drawn = True
            band_y = (min(y_n, y_s), max(y_n, y_s))

    # 走行経路（火星ローバーのみ）
    if route:
        pts = [g2px(lo, la) for lo, la in route]
        d.line([(x, y) for x, y in pts if 0 <= x <= img.size[0] and 0 <= y <= img.size[1]],
               fill=(255, 90, 40), width=3, joint="curve")
        lx0, ly0 = g2px(route[0][0], route[0][1])
        if -200 <= lx0 <= img.size[0] + 200 and -200 <= ly0 <= img.size[1] + 200:
            d.ellipse([lx0 - 8, ly0 - 8, lx0 + 8, ly0 + 8], fill=(80, 160, 255),
                      outline=(255, 255, 255), width=3)
    # 着陸地点（青●）: 火星は走行の起点、フォボスは公表値/指定値
    if mode == "trek" and route:
        lx0, ly0 = g2px(route[0][0], route[0][1])
        if -200 <= lx0 <= img.size[0] + 200 and -200 <= ly0 <= img.size[1] + 200:
            d.ellipse([lx0 - 8, ly0 - 8, lx0 + 8, ly0 + 8], fill=(80, 160, 255),
                      outline=(255, 255, 255), width=3)

    # 同じ天体にある**ほかの人工物着陸地点**（黄枠●）:
    # 火星の Parsons/Clint/ Viking…、月の Surveyor 3 など。
    # 出典は planetary_map.SITES（各点の一次出典を明記済み）。
    other_sites_drawn = []
    if show_sites and mode == "trek":
        from .planetary_map import SITES as _SITES
        for _k, _st in (_SITES.get(b) or {}).items():
            if _st.get("kind") in ("crater", "impact"):
                continue          # 自然衝突・人工物衝突は「着陸地点」ではない
            _sla, _slo = float(_st["lat"]), float(_st["lon"])
            _sx, _sy = g2px(_slo, _sla)
            if not (-200 <= _sx <= img.size[0] + 200 and -200 <= _sy <= img.size[1] + 200):
                continue
            d.ellipse([_sx - 7, _sy - 7, _sx + 7, _sy + 7], outline=(255, 210, 60), width=3)
            _f_other = load_font(15, bold=True)
            d.text((_sx + 10, _sy - 9), _st.get("ja", _k), font=_f_other, fill=(255, 225, 120))
            other_sites_drawn.append({"id": _k, "ja": _st.get("ja", _k),
                                      "lat": _sla, "lon": _slo})

    # 現在地／着陸予定点（赤●）
    cx, cy = g2px(clon, clat)
    has_point = bool(wp or landing_site)
    if has_point:
        draw_marker(d, (cx, cy), 13, fill=(255, 40, 30), outline_w=4)
    # 旧 _font は常にメイリオ Bold 優先だったため bold=True で等価
    f_big = load_font(30, bold=True); f_mid = load_font(22, bold=True); f_sm = load_font(20, bold=True)
    # ラベル（地点が無いときは描かない＝地図中央に「ここにいる」ように見えるのを避ける）
    if has_point:
        lab = f"{rover_ja} 着陸予定点" if landing_site else f"{rover_ja} 現在地"
        d.rectangle([cx + 16, cy - 22, cx + 16 + 380, cy + 30], fill=(0, 0, 0, 220))
        d.text((cx + 22, cy - 16), lab, font=f_big, fill=(255, 255, 255))
    # 凡例パネル（テキストに合わせて高さを決める）
    title = f"{body_cfg['ja']}面探査ローバー {rover_ja} の位置マップ"
    if wp:
        row1 = f"座標: {clat:.3f}° {'N' if clat >= 0 else 'S'}  {clon:.3f}°E  走行 {wp.get('dist_km')} km"
        row2 = f"着陸: {rcfg['land_ja']}  現在地waypoint: sol{wp.get('sol')} (RMC {wp.get('rmc')})"
    elif landing_site:
        row1 = f"座標: {clat:.3f}° {'N' if clat >= 0 else 'S'}  {_fmt_lon(clon)}"
        row2 = f"着陸予定: {rcfg['land_ja']}"
    else:
        row1 = "着陸地点は未公表（MMX の着陸地点選定は 2027〜2028 年）"
        row2 = f"制約: 緯度 ±{band_deg:.0f}° 以内（着陸地点そのものではない）"
    pad = 6
    # パネル幅は画像幅に収め、タイトルと各行は実文字幅で**パネル内に収まる**サイズに
    # する。文字がパネルからはみ出すと、地図の上に黒字が乗って視認性が落ちる（実測）。
    panel_w = min(900, iw - 16)
    f_title = fit_font_for_width(panel_w, [title], [30, 27, 24, 21, 18])
    f_row1 = fit_font_for_width(panel_w, [row1], [22, 20, 18, 16, 14])
    f_row2 = fit_font_for_width(panel_w, [row2], [20, 18, 16, 14, 12])
    h_title = d.textbbox((0, 0), title, font=f_title)[3] - d.textbbox((0, 0), title, font=f_title)[1]
    h_row1 = d.textbbox((0, 0), row1, font=f_row1)[3] - d.textbbox((0, 0), row1, font=f_row1)[1]
    h_row2 = d.textbbox((0, 0), row2, font=f_row2)[3] - d.textbbox((0, 0), row2, font=f_row2)[1]
    panel_h = 12 + h_title + pad + h_row1 + pad + h_row2 + 20
    # 情報パネルは現在地マーカーを隠さない位置へ（隠れると画素検査が落ちる）
    px0, py0, panel_hidden = panel_placement(iw, ih, panel_w, panel_h,
                                             avoid=[(cx, cy)], margin=8, gap=20)
    d.rounded_rectangle([px0, py0, px0 + panel_w, py0 + panel_h], radius=12,
                        fill=(0, 0, 0, 215))
    y = py0 + 12
    d.text((px0 + 14, y), title, font=f_title, fill=(255, 255, 255)); y += h_title + pad
    d.text((px0 + 14, y), row1, font=f_row1, fill=(225, 228, 248)); y += h_row1 + pad
    d.text((px0 + 14, y), row2, font=f_row2, fill=(200, 205, 235))
    if panel_hidden and has_point:
        # どの隅でも重なる場合は、隠れるマーカーをパネルの上に描き直す
        draw_marker(d, (cx, cy), 13, fill=(255, 40, 30), outline_w=4)
    # 凡例バー(下部)
    ly = ih - 44
    d.rounded_rectangle([8, ly, iw - 8, ih - 8], radius=10, fill=(0, 0, 0, 200))
    if mode == "trek":
        d.ellipse([20, ly + 12, 36, ly + 28], fill=(80, 160, 255))
        d.ellipse([58, ly + 12, 74, ly + 28], fill=(255, 40, 30))
        _leg_x = 86
        if other_sites_drawn:
            d.ellipse([_leg_x, ly + 12, _leg_x + 16, ly + 28], outline=(255, 210, 60), width=3)
            d.text((_leg_x + 22, ly + 9), "黄枠●ほかの着陸地点", font=f_sm, fill=(235, 238, 250))
            _leg_x += 22 + int(d.textlength("黄枠●ほかの着陸地点", font=f_sm)) + 20
        d.text((_leg_x, ly + 9), "青●着陸地点  赤●現在地  橙線=走行経路  NASA Trek 等角図法",
               font=f_sm, fill=(235, 238, 250))
    else:
        if band_drawn:
            d.rectangle([20, ly + 12, 36, ly + 28], outline=(90, 230, 190), width=3)
        if has_point:
            d.ellipse([58, ly + 12, 74, ly + 28], fill=(255, 40, 30))
        legend = ("赤●着陸予定点  " if has_point else "")
        if band_drawn:
            legend += f"緑枠=緯度±{band_deg:.0f}°の制約帯（着陸地点ではない）  "
        legend += "USGS 全球等角図"
        d.text((86, ly + 9), legend, font=f_sm, fill=(235, 238, 250))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    jpeg = buf.getvalue()
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{rover_ja} の{body_cfg['ja']}位置マップ")
    # インライン画像を描けないハーネス向け: 保存してリンクを先頭に出す
    out_path = save_output(jpeg, "planetary_rover_location_map", "jpg")

    # ---- 注記（数値から生成する）----
    if mode == "trek":
        notes = figure_notes(extra=[
            "橙線は NASA MMGIS の waypoint 列（通過点）を結んだもので、実際の細かい走行軌跡ではない",
            "青●は着陸地点、赤●は現在地。走行距離は着陸地点からの累積 km",
            "地図は NASA Trek の等角図法のため、高緯度ほど東西方向が圧縮されて見える",
            "座標・sol・RMC は NASA MMGIS (mars.nasa.gov) の公開 waypoint データ",
            (("黄枠●は同じ天体にあるほかの人工物着陸地点（{} 件）。"
              "自然衝突クレーター・人工物衝突は含まない。出典は地点ごとに planetary_map.SITES に記載"
              ).format(len(other_sites_drawn)) if other_sites_drawn else ""),
            ("" if (px0, py0) == (8, 8) and not panel_hidden else
             "情報パネルは現在地マーカー（cx={:.0f}, cy={:.0f}px）と重ならない隅に配置した".format(cx, cy)),
            ("" if not panel_hidden else
             "図が小さくパネルと重なるため、現在地マーカーはパネルの上に描き直した"),
            tile_failure_note(sv),
        ])
        caption = (f"{rover_ja}（{rv}）の{body_cfg['ja']}現在地。"
                   f"座標 {clat:.4f}°{'N' if clat >= 0 else 'S'} / {clon:.4f}°E、sol {wp.get('sol')}、"
                   f"着陸地点から {wp.get('dist_km')} km。")
        source_txt = "出典: NASA MMGIS (mars.nasa.gov) + Trek WMTS（認証不要）"
        markers = [{"id": "current", "label": f"{rover_ja} 現在地", "lat": clat, "lon": clon,
                    "sol": wp.get("sol"), "rover_distance_km": wp.get("dist_km")}]
        markers += [{"id": o["id"], "label": o["ja"], "lat": o["lat"], "lon": o["lon"],
                     "kind": "landing_site"} for o in other_sites_drawn]
        verify = dict(verify_marker(img, (cx, cy), clon, clat, sv["inv"],
                                    r=marker_scan_radius(13)),
                      panel_overlaps_marker=bool(panel_hidden))
        head = [
            f"🔴 **{rover_ja}（{rv}）の{body_cfg['ja']}現在地マップ**:",
            f"📍 座標: {clat:.4f}° {'N' if clat >= 0 else 'S'} / {clon:.4f}°E ・ sol {wp.get('sol')}",
            f"🛣 走行距離: {wp.get('dist_km')} km（RMC {wp.get('rmc')}）",
            "画像は上に表示。着陸地点(青)からの走行経路(橙線)と現在地(赤●)を地図に合成。",
        ]
        sc_extra = {"sol": wp.get("sol"), "dist_km": wp.get("dist_km"), "rmc": wp.get("rmc")}
    else:
        # フォボス（MMX / IDEFIX）
        _ar_pct = float(sv.get("ar_correction_percent") or 0.0)
        if sv.get("ar_corrected"):
            _ar_note = ("出典画像の地形範囲のアスペクト比は 2:1 ではないため **2:1 に補正**して描画している"
                        "（元 AR={}、{}×{} px、補正 {:.2f}%）".format(
                            sv.get("source_ar"), (sv.get("pixels") or ["?", "?"])[0],
                            (sv.get("pixels") or ["?", "?"])[1], _ar_pct))
        else:
            _ar_note = "出典画像の地形範囲は 2:1 のため補正なし"
        _band_note = (("緑枠は着陸地点の**緯度制約（±{:.0f}°）**で、**着陸地点そのものではない**"
                       "（候補地選定の拘束条件として公表されているもの）".format(band_deg))
                      if band_drawn else
                      "緯度制約帯（±{:.0f}°）は show_lat_band=False で非表示".format(band_deg))
        notes = figure_notes(extra=[
            "**着陸地点は未公表**。MMX は 2027〜2028 年に QSO 観測で着陸候補を絞り込み、"
            "2028 年度末〜2029 年頭に IDEFIX ローバーを先に、2030 年頃に本体を着陸させる計画",
            _band_note,
            ("赤●は着陸予定点（公表済みの座標または利用者が指定した座標）。着陸前は「現在地」ではない"
             if has_point else
             "この図には着陸予定点（赤●）がない＝着陸地点がまだ公表されていないため"),
            "地図は USGS 公式全球等角図（Shaded Relief）。等角図法のため高緯度ほど東西方向が圧縮されて見える",
            "フォボスの経度は IAU/U.S. Gazetteer で**西向き正（+West 0-360）**が慣用。本図の座標は"
            "内部的に**東向き正**で保持し、西向き正の値は 360°-経度 で併記する",
            _ar_note,
            ("" if (px0, py0) == (8, 8) and not panel_hidden else
             "情報パネルは着陸予定点マーカー（cx={:.0f}, cy={:.0f}px）と重ならない隅に配置した".format(cx, cy)),
        ])
        if published_status == "published":
            caption = (f"{rover_ja}の{body_cfg['ja']}着陸地点（IAU/U.S. Gazetteer 公表）。"
                       f"座標 {clat:.4f}°{'N' if clat >= 0 else 'S'} / {_fmt_lon(clon)}。")
            head = [f"🔴 **{rover_ja}（{rv}）の{body_cfg['ja']}着陸地点（公表済み）**:",
                    f"📍 座標: {clat:.4f}° {'N' if clat >= 0 else 'S'} / {_fmt_lon(clon)}"]
        elif published_status == "user_specified":
            caption = (f"{rover_ja}の{body_cfg['ja']}上の指定地点。"
                       f"座標 {clat:.4f}°{'N' if clat >= 0 else 'S'} / {_fmt_lon(clon)}。")
            head = [f"🔴 **{rover_ja}（{rv}）の{body_cfg['ja']}上の指定地点**:",
                    f"📍 座標: {clat:.4f}° {'N' if clat >= 0 else 'S'} / {_fmt_lon(clon)}"]
        else:
            caption = (f"{rover_ja}（{rv}）の{body_cfg['ja']}着陸地点選定域。"
                       f"着陸地点は未公表のため、制約帯（緯度 ±{band_deg:.0f}°）と全球図を示した。")
            head = [f"🔴 **{rover_ja}（{rv}）の{body_cfg['ja']}着陸地点（未公表）**:",
                    "📍 着陸地点は未公表（MMX の選定は 2027〜2028 年）",
                    f"🧭 制約: 緯度 ±{band_deg:.0f}° 以内（これは候補条件であって着陸地点ではない）"]
        source_txt = ("出典: USGS 公式全球等角図（Phobos / Shaded Relief）＋ "
                      "IAU/U.S. Gazetteer of Planetary Nomenclature（認証不要）")
        markers = ([{"id": "landing", "label": f"{rover_ja} 着陸予定点", "lat": clat, "lon": clon}]
                    if landing_site else [])
        if has_point:
            # 全球図は 1px≒0.36° なので、固定の 0.01° 許容では必ず落ちる（実測）。
            # 許容量は画素分解能から決める（ planetary_map の同じ考え方）。
            _tol_deg = max(0.01, 1.6 * float(sv.get("deg_per_px") or 0.0))
            verify = dict(verify_marker(img, (cx, cy), clon, clat, sv["inv"],
                                        r=marker_scan_radius(13), tol_deg=_tol_deg),
                          panel_overlaps_marker=bool(panel_hidden),
                          tol_deg=round(_tol_deg, 4))
        else:
            # 地点未指定: ベースマップが描けているか（色数）と制約帯の描画を検査する
            _rgb = img.convert("RGB")
            _pv = _rgb.load()
            uniq = len({_pv[i, j] for i in range(0, iw, max(1, iw // 60))
                        for j in range(0, ih, max(1, ih // 60))})
            band_px = 0
            if band_drawn:
                band_px = sum(1 for i in range(0, iw, 4)
                              for j in range(int(band_y[0]), int(band_y[1]) + 1, 4)
                              if _rgb.getpixel((i, j))[:3] == (90, 230, 190))
            verify = {"ok": bool(uniq > 50 and (band_drawn or not show_lat_band)),
                      "basemap_mode": sv.get("mode"),
                      "basemap_unique_colors_sampled": uniq,
                      "constraint_band_drawn": bool(band_drawn),
                      "constraint_band_pixels_sampled": band_px,
                      "note": "着陸地点未公表（ベースマップと制約帯のみ）"}
        sc_extra = {"landing_site_status": published_status,
                    "landing_site_name": (landing_site or {}).get("name"),
                    "landing_site_source": (landing_site or {}).get("source"),
                    "constraint_lat_band_deg": band_deg,
                    "constraint_band_drawn": bool(band_drawn),
                    "basemap": attrib}

    fig = figure_payload(
        kind="ground_track_map",
        title=f"{rover_ja} の{body_cfg['ja']}位置マップ（{attrib}）",
        view=view_spec("body_surface", "equirectangular",
                       f"{body_cfg['ja']}面の地図（等角図法）に着陸地点・走行経路・現在地を合成",
                       why="移動の履歴を地図上で見るための図。天体の形や軌道は描いていない"),
        primary=primary_spec(body_cfg["ja"], "surface_map",
                             note="天体面の地図なので主天体は図の中心に置いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["着陸地点(青)・現在地(赤)のマーカーは実寸ではない"]),
        markers=markers,
        notes=notes,
        caption=caption,
        verify=verify,
    )
    lines = [
        media_link_line(f"{rover_ja} の{body_cfg['ja']}位置マップ",
                        path=out_path, kind="figure"),
    ] + head + [
        "",
        figure_text_block(fig),
        source_txt,
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={"body": b, "rover": rv, "rover_ja": rover_ja,
                           "lat": clat, "lon": clon,
                           "zoom": zoom, "span_deg": span_deg,
                           "figure": fig, "image_path": out_path,
                           "source": attrib, **sc_extra},
    )

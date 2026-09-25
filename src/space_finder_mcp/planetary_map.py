from __future__ import annotations

import base64
import datetime
import math
import re
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

from .surface_map import (add_legend_band, draw_marker, draw_markers, fit_font_for_width,
                         graticule_view, image_view, marker_scan_radius, panel_placement,
                         surface_view, tile_failure_note, verify_marker, verify_markers)
from .name_common import split_names
from .img_common import (encode_jpeg, figure_notes, figure_payload,
                         figure_text_block, load_font, media_link_line,
                         primary_spec, save_output, scale_spec,
                         split_at_antimeridian, view_spec)
from .input_utils import as_float, as_int

UA = {"User-Agent": "space-finder-mcp/0.25 (MCP; planetary orbiter track)"}
# 全球画像の取得用 UA（Wikimedia は連絡先つきの識別可能な UA を求める。無いと 429/403 になる）
BASEMAP_UA = {"User-Agent": "space-finder-mcp/0.30 (planetary basemap; contact: dev)"}

BODIES = {
    "moon": {
        "ja": "月", "en": "Moon", "center": "@301", "radius": 1737.4,
        "a0": 269.9949, "da": 0.0031, "d0": 66.5392, "dd": 0.0130,
        "W0": 38.3213, "Wd": 13.17635815,
        "tile": ("https://trek.nasa.gov/tiles/Moon/EQ/"
                 "LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 8, "attrib": "NASA Trek LRO WAC",
    },
    "mars": {
        "ja": "火星", "en": "Mars", "center": "@499", "radius": 3396.2,
        "a0": 317.68143, "da": -0.1061, "d0": 52.88650, "dd": -0.0609,
        "W0": 176.630, "Wd": 350.89198226,
        "tile": ("https://trek.nasa.gov/tiles/Mars/EQ/"
                 "Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 7, "attrib": "NASA Trek Viking MDIM2.1",
    },
    "mercury": {
        "ja": "水星", "en": "Mercury", "center": "@199", "radius": 2439.7,
        "a0": 281.0097, "da": -0.0328, "d0": 61.4143, "dd": -0.0049,
        "W0": 329.5988, "Wd": 6.1385108,
        "tile": ("https://trek.nasa.gov/tiles/Mercury/EQ/"
                 "MESSENGER_USGS_Mosaic_ClrMosaic_global_665m/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 7, "attrib": "NASA Trek MESSENGER USGS",
    },
    "titan": {
        "ja": "タイタン", "en": "Titan", "center": "@606", "radius": 2574.7,
        "a0": 36.41, "da": -0.036, "d0": 83.94, "dd": -0.004,
        "W0": 189.64, "Wd": 22.5769768,
        "tile": ("https://trek.nasa.gov/tiles/Titan/EQ/"
                 "Cassini_SAR_Global_ClrMosaic_global_500m/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 7, "attrib": "NASA Trek Cassini SAR",
    },
    "vesta": {
        "ja": "ベスタ", "en": "Vesta", "center": "@4", "radius": 262.7,
        "a0": 301.0, "da": 0.0, "d0": 41.6, "dd": 0.0,
        "W0": 285.0, "Wd": 1617.3327233,
        "tile": ("https://trek.nasa.gov/tiles/Vesta/EQ/"
                 "Dawn_FC_HAMO_ClrMosaic_global_200m/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 7, "attrib": "NASA Trek Dawn FC",
    },
    "ceres": {
        "ja": "ケレス", "en": "Ceres", "center": "@2000001", "radius": 469.7,
        "a0": 291.42744, "da": 0.0, "d0": 66.76033, "dd": 0.0,
        "W0": 344.1540, "Wd": 952.1532635,
        "tile": ("https://trek.nasa.gov/tiles/Ceres/EQ/"
                 "Dawn_FC_HAMO_ClrMosaic_global_200m/1.0.0/default/default028mm/"
                 "{z}/{row}/{col}.jpg"),
        "maxzoom": 7, "attrib": "NASA Trek Dawn FC",
    },
}

ORBITERS = {
    "lro": {"body": "moon", "id": "-85", "ja": "LRO", "en": "Lunar Reconnaissance Orbiter"},
    "gateway": {"body": "moon", "id": "-228", "ja": "ゲートウェイ", "en": "Gateway"},
    "mro": {"body": "mars", "id": "-74", "ja": "MRO", "en": "Mars Reconnaissance Orbiter"},
    "odyssey": {"body": "mars", "id": "-53", "ja": "Mars Odyssey", "en": "Mars Odyssey"},
}
# かぐや（SELENE）の制御落下地点。JAXA プレスリリース 2009-06-11 が公表した値そのもの
# （南緯65.5度・東経80.4度 Gill クレータ付近／落下は月面の日影部分＝夜側）。
# 「落点は判明しているか」に MCP から答えられるよう数値と出典をここに持つ。
_KAGUYA_IMPACT = {
    "lat": -65.5, "lon": 80.4, "place": "Gill クレータ付近",
    "when_utc": "2009-06-10T18:25:00Z", "when_jst": "2009-06-11 03:25 JST",
    "note": "落下地点は月面の日影部分（夜側）。衝突閃光は AAT（英豪天文台・近赤外）と "
            "Mount Abu 天文台（インド, 2.12µm）が観測を報告し、JAXA は落下地点・時刻との"
            "比較から『かぐやの閃光を捉えた可能性が高い』としている",
    "source": "JAXA プレスリリース 2009-06-11「月周回衛星『かぐや（SELENE）』の制御落下結果について」"
              "／同プロジェクトサイト（落下前の地形カメラ画像に星印で落下地点を掲載）",
}
PAST = {
    "kaguya": {"body": "moon", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)",
               "note": "2009年に月面へ制御落下（運用終了）", "impact": _KAGUYA_IMPACT},
    "selene": {"body": "moon", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)",
               "note": "2009年に月面へ制御落下（運用終了）", "impact": _KAGUYA_IMPACT},
}
_UNKNOWN_GUARD = {
    "maven": "MAVEN（火星大気探査機）は2025年12月6日に火星の裏側で交信が途絶え、NASA が2026年6月3日に運用終了を宣言しました（NASA Science / プレスリリース）。JPL Horizons 側のエフェメリスも 2026年3月1日までで、現在位置は計算できません。",
    "akatsuki": "あかつき（金星探査機）は2026年に運用終了しており、現在位置を計算できるデータ源がありません。",
}


def _horizons_states(cmd: str, center: str, jds: list) -> list:
    """複数時刻の状態ベクトルを取得（TLIST を POST + 分割バッチで送信）。

    大量の時刻をURLクエリに入れると 502/414(Request-URI Too Large) になるため、
    POST body で送り、かつ1リクエストあたり _HZ_BATCH 点（~50）に分割する。
    Horizons は時刻ごとに X= を2回出力する点に注意（時間行＋座標行）。
    戻り: [(x,y,z,vx,vy,vz), ...]（入力 jds と同じ順・数）
    """
    if len(jds) == 1:
        return [_horizons_state_single(cmd, center, jds[0])]

    out = {}
    # 分割送信
    for start in range(0, len(jds), _HZ_BATCH):
        chunk = jds[start:start + _HZ_BATCH]
        tl = ",".join("'{:.6f}'".format(j) for j in chunk)
        data = {
            "format": "text", "COMMAND": "'" + cmd + "'", "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS",
            "CENTER": "'" + center + "'", "REF_PLANE": "'FRAME'",
            "TLIST": tl, "VEC_TABLE": "'2'", "OUT_UNITS": "'KM-S'",
        }
        r = requests.post("https://ssd.jpl.nasa.gov/api/horizons.api", data=data,
                          headers=UA, timeout=(60, 60))
        r.raise_for_status()
        txt = r.text
        if "No ephemeris" in txt or "No such record" in txt:
            raise ValueError(txt.strip().splitlines()[-1][:200])
        i, j = txt.find("$$SOE"), txt.find("$$EOE")
        if i < 0 or j < 0:
            raise ValueError("Horizons 応答に座標ブロックがありません")
        blk = txt[i + 5:j]
        # 各時刻ブロックを「<JD> = A.D. ...」行で区切る（時刻ごとに2つめが座標）
        chunks = re.split(r"(?m)^\s*\d+\.\d+ =", blk)
        got = 0
        for ch in chunks:
            if "X =" not in ch:
                continue
            m = re.search(r"X\s*=\s*([-+0-9.Ee]+)\s+Y\s*=\s*([-+0-9.Ee]+)\s+Z\s*=\s*([-+0-9.Ee]+)", ch)
            if not m:
                continue
            v = re.search(r"VX=\s*([-+0-9.Ee]+)\s+VY=\s*([-+0-9.Ee]+)\s+VZ=\s*([-+0-9.Ee]+)", ch)
            x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if v:
                vx, vy, vz = float(v.group(1)), float(v.group(2)), float(v.group(3))
            else:
                vx = vy = vz = None
            out[start + got] = (x, y, z, vx, vy, vz)
            got += 1
    # 順序保証
    result = []
    for k in sorted(out.keys()):
        result.append(out[k])
    if len(result) != len(jds):
        raise ValueError(f"Horizons が {len(jds)} 時刻中 {len(result)} 個しか返しません")
    return result


# 1リクエストあたりの最大時刻数（Horizons が返答ブロックで省略し始める前に分割）
_HZ_BATCH = 50


def _horizons_state_single(cmd: str, center: str, jd: float) -> tuple:
    """単一時刻の状態ベクトルを GET で取得。"""
    params = {
        "format": "text", "COMMAND": "'" + cmd + "'", "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "VECTORS",
        "CENTER": "'" + center + "'", "REF_PLANE": "'FRAME'",
        "TLIST": "'{:.6f}'".format(jd), "VEC_TABLE": "'2'", "OUT_UNITS": "'KM-S'",
    }
    r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                     headers=UA, timeout=(40, 40))
    r.raise_for_status()
    txt = r.text
    if "No ephemeris" in txt or "No such record" in txt:
        raise ValueError(txt.strip().splitlines()[-1][:200])
    i, j = txt.find("$$SOE"), txt.find("$$EOE")
    if i < 0 or j < 0:
        raise ValueError("Horizons 応答に座標ブロックがありません")
    blk = txt[i + 5:j]
    m = re.search(r"X\s*=\s*([-+0-9.Ee]+)\s+Y\s*=\s*([-+0-9.Ee]+)\s+Z\s*=\s*([-+0-9.Ee]+)", blk)
    v = re.search(r"VX=\s*([-+0-9.Ee]+)\s+VY=\s*([-+0-9.Ee]+)\s+VZ=\s*([-+0-9.Ee]+)", blk)
    if not m:
        raise ValueError("状態ベクトルを解析できません: " + blk[:200])
    x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if v:
        vx, vy, vz = float(v.group(1)), float(v.group(2)), float(v.group(3))
    else:
        vx = vy = vz = None
    return x, y, z, vx, vy, vz


def _horizons_state(cmd: str, center: str, jd: float) -> tuple:
    return _horizons_state_single(cmd, center, jd)


def _body_name(cmd: str) -> str:
    params = {"format": "text", "COMMAND": "'" + cmd + "'", "MAKE_EPHEM": "'NO'"}
    try:
        r = requests.get("https://ssd.jpl.nasa.gov/api/horizons.api", params=params,
                         headers=UA, timeout=(30, 30))
        m = re.search(r"Revised:.*?([A-Za-z0-9 /().\-']+?)\s*\((-?[0-9]+)\)", r.text)
        if m:
            return m.group(1).strip()
    except requests.RequestException:
        pass
    return cmd


def _pole_W(body: dict, jd: float) -> tuple:
    T = (jd - 2451545.0) / 36525.0
    a = body["a0"] + body["da"] * T
    d = body["d0"] + body["dd"] * T
    W = body["W0"] + body["Wd"] * (jd - 2451545.0)
    return a, d, W


def _j2000_to_body(body: dict, x, y, z, jd) -> tuple:
    a, d, W = _pole_W(body, jd)
    A, D, Wr = math.radians(a), math.radians(d), math.radians(W)
    x1 = x * math.cos(A) + y * math.sin(A)
    y1 = -x * math.sin(A) + y * math.cos(A)
    z1 = z
    t = D - math.pi / 2
    x2 = x1 * math.cos(t) + z1 * math.sin(t)
    y2 = y1
    z2 = -x1 * math.sin(t) + z1 * math.cos(t)
    x3 = x2 * math.cos(Wr) + y2 * math.sin(Wr)
    y3 = -x2 * math.sin(Wr) + y2 * math.cos(Wr)
    z3 = z2
    return x3, y3, z3


def _body_to_latlon(body: dict, xb, yb, zb):
    r = math.sqrt(xb * xb + yb * yb + zb * zb)
    lat = math.degrees(math.asin(max(-1, min(1, zb / r))))
    lon = math.degrees(math.atan2(yb, xb)) % 360
    if lon > 180:
        lon -= 360
    alt = r - body["radius"]
    return lat, lon, alt


def _state(body: dict, cmd: str, jd: float) -> dict:
    x, y, z, vx, vy, vz = _horizons_state(cmd, body["center"], jd)
    return _vec_to_state(body, x, y, z, vx, vy, vz, jd)


def _vec_to_state(body: dict, x, y, z, vx, vy, vz, jd) -> dict:
    xb, yb, zb = _j2000_to_body(body, x, y, z, jd)
    lat, lon, alt = _body_to_latlon(body, xb, yb, zb)
    speed = None
    if vx is not None:
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    return {"lat": lat, "lon": lon, "alt_km": alt, "speed_kms": speed,
            "dist_km": math.sqrt(x * x + y * y + z * z)}


# 月面の着陸地点（地点マーカー用）。座標は NASA NSSDC が公表した月着陸船(LM)の値
# （LRO 画像から決定・惑星中心 Mean Earth/Polar Axis 座標系、Wagner et al., Icarus 283 (2017)）。
# **旗そのものの座標は公開表に無い**ため、マーカーは着陸地点を指し、旗の状態は
# NASA ALSJ「Six Flags on the Moon」＋ LROC の時系列画像（影が回るか）による。
# ============================================================
# 地点マーカー図が使う天体表（全球地図の入手可否で basemap を切り替える）
#   basemap="trek"      : NASA Trek の全球等角図タイル（実測で層IDが通る天体のみ）
#   basemap="graticule" : 全球地形画像が無い天体 → 緯度経度グリッドに座標だけ描く
#                        （実測: NASA Trek に Jupiter は無い＝404。ガス惑星・小型天体は
#                          全球画像が無いことが多いので、座標は落とせるようにしておく）
# ============================================================
# ============================================================
# 地点マーカー図が使う天体表（全球地図の入手可否で basemap を切り替える）
#   basemap="trek"      : NASA Trek の全球等角図タイル（実測で層IDが通る天体のみ）
#   basemap="graticule" : 全球地形画像が無い天体 → 緯度経度グリッドに座標だけ描く
#                        （実測: NASA Trek に Jupiter は無い＝404。ガス惑星・小型天体は
#                          全球画像が無いことが多いので、座標は落とせるようにしておく）
# ============================================================
# basemap の3種:
#   "trek"      = NASA Trek の全球等角図タイル（層IDを検証済みの天体のみ）
#   "image"     = **1枚の全球等角図**（例: USGS/NASA の全球モザイク、Commons 経由の NASA 画像）。
#                 2:1 でない等角図は 2:1 に補正して使う（「どのあたりか」が分かればよい用途
#                 では厳密な幾何より可用性を優先。補正率は注記に数値で残す）。
#   "graticule" = 全球画像が無い天体 → 緯度経度グリッドに座標だけ描く
MAP_BODIES: dict = {
    "moon": {"ja": "月", "en": "Moon", "basemap": "trek"},
    "mars": {"ja": "火星", "en": "Mars", "basemap": "trek"},
    "mercury": {"ja": "水星", "en": "Mercury", "basemap": "trek"},
    # 金星: USGS/NASA の Magellan 全球モザイク。左端 -180°E は USGS の .lbl で確認
    # （CenterLongitude=0.0・最小経度 -180.0・最大 180.0・PositiveEast）＝この図の前提と同じなので
    # 回転は不要（left_edge_lon=-180.0 を明示しておく）。
    "venus": {"ja": "金星", "en": "Venus", "basemap": "image",
              "image": {"url": "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/"
                               "Venus_Magellan_C3-MDIR_ClrTopo_Global_Mosaic_6600m.tif",
                        "credit": "USGS/NASA Magellan C3-MDIR 全球モザイク（6600m/px・地形で彩色）",
                        "src_ar": 2.0, "left_edge_lon": -180.0}},
    "titan": {"ja": "タイタン", "en": "Titan", "basemap": "image",
              "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/b/bc/"
                               "PIA22770-SaturnMoon-Titan-Surface-20181206.jpg",
                        "credit": "NASA/JPL-Caltech/SSI（Cassini 全球図 PIA22770）", "src_ar": 2.0}},
    "vesta": {"ja": "ベスタ", "en": "Vesta", "basemap": "trek"},
    "ceres": {"ja": "ケレス", "en": "Ceres", "basemap": "trek"},
    "io": {"ja": "イオ", "en": "Io", "basemap": "image",
           "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/a/a5/"
                            "First_Geologic_Map_of_Jupiter%E2%80%99s_Moon_Io.jpg",
                     "credit": "USGS（イオ全球地質図・下地は Voyager/Galileo 画像）", "src_ar": 1.951}},
    "europa": {"ja": "エウロパ", "en": "Europa", "basemap": "image",
               "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/2/26/"
                                "Europa_Voyager_GalileoSSI_global_mosaic.jpg",
                         "credit": "NASA/USGS（Voyager + Galileo SSI 全球モザイク）", "src_ar": 2.0}},
    "ganymede": {"ja": "ガニメデ", "en": "Ganymede", "basemap": "image",
                 "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/4/4b/"
                                  "Ganymede_Global_Geologic_Map_and_Global_Image_Mosaic.jpg",
                           "credit": "USGS/NASA（ガニメデ全球地質図＋画像モザイク）", "src_ar": 1.883}},
    "callisto": {"ja": "カリスト", "en": "Callisto", "basemap": "image",
                 "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/9/96/"
                                  "Callisto_USGS_global_small.jpg",
                           "credit": "USGS（カリスト全球モザイク・縮小版）", "src_ar": 2.06}},
    # 気体惑星4天体: **Hubble OPAL** の全球等角図（MAST/STScI 公開。DOI 10.17909/T9G593）。
    # OPAL は「left edge = 0 System III W, decreasing to the right」＝東向き正で左端 0°E
    # なので left_edge_lon=0.0。実測: 木星 19.4MB(3600x1800)／土星 4.9MB(1800x900)
    # ／天王星・海王星 0.79MB(721x361)。**その年の大気**の図なので地点の年代とは一致しない。
    "jupiter": {"ja": "木星", "en": "Jupiter", "basemap": "image",
                "image": {"url": "https://archive.stsci.edu/hlsps/opal/cycle32/jupiter/"
                                 "hlsp_opal_hst_wfc3-uvis_jupiter-2025a_f395n-f502n-f631n"
                                 "_v1_globalmap.tif",
                          "credit": "Hubble OPAL 2025a 全球図（NASA/ESA/STScI）",
                          "src_ar": 2.0, "left_edge_lon": 0.0, "epoch": "2025-12-11〜12"}},
    "saturn": {"ja": "土星", "en": "Saturn", "basemap": "image",
               "image": {"url": "https://archive.stsci.edu/hlsps/opal/cycle32/saturn/"
                                "hlsp_opal_hst_wfc3-uvis_saturn-2025a_f395n-f502n-f631n"
                                "_v1_globalmap.tif",
                         "credit": "Hubble OPAL 2025a 全球図（NASA/ESA/STScI）",
                         "src_ar": 2.0, "left_edge_lon": 0.0, "epoch": "2025年（OPAL 最新回）"}},
    "uranus": {"ja": "天王星", "en": "Uranus", "basemap": "image",
               "image": {"url": "https://archive.stsci.edu/hlsps/opal/cycle33/uranus/"
                                "hlsp_opal_hst_wfc3-uvis_uranus-2025a_f657n-f547m-f467m"
                                "_v1_globalmap.tif",
                         "credit": "Hubble OPAL 2025a 全球図（NASA/ESA/STScI）",
                         "src_ar": 2.0, "left_edge_lon": 0.0, "epoch": "2025年（OPAL）"}},
    "neptune": {"ja": "海王星", "en": "Neptune", "basemap": "image",
                "image": {"url": "https://archive.stsci.edu/hlsps/opal/cycle32/neptune/"
                                 "hlsp_opal_hst_wfc3-uvis_neptune-2025b_f467m-f547m-f657n"
                                 "_v1_globalmap.tif",
                          "credit": "Hubble OPAL 2025b 全球図（NASA/ESA/STScI）",
                          "src_ar": 2.0, "left_edge_lon": 0.0, "epoch": "2025年（OPAL）"}},
    # 冥王星: USGS/NASA の New Horizons 全球カラー図（Commons 経由）。左端 0°E は USGS の
    # .lbl（CenterLongitude=180・最小経度 0.0）と、スプートニク平原が図のほぼ中央に来ること
    # （実測 f=0.499≒180°E）の二重の確認による。
    "pluto": {"ja": "冥王星", "en": "Pluto", "basemap": "image",
              "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/a/ad/"
                               "Pluto_color_mapmosaic.jpg",
                        "credit": "NASA/JHUAPL/SwRI（New Horizons 全球カラー図）",
                        "src_ar": 2.0, "left_edge_lon": 0.0}},
    # ベンヌ: NASA GSFC/アリゾナ大の OSIRIS-REx 全球モザイク（縮小版）。USGS/PDS の同モザイクは
    # 左端 0°E（.lbl: CenterLongitude=180・最小経度 0.0）＝ Equirectangular。
    "bennu": {"ja": "ベンヌ", "en": "Bennu", "basemap": "image",
              "image": {"url": "https://upload.wikimedia.org/wikipedia/commons/b/b8/"
                               "Bennu_global_mosaic_reduced_size.png",
                        "credit": "NASA/GSFC/アリゾナ大（OSIRIS-REx 全球モザイク）",
                        "src_ar": 2.0, "left_edge_lon": 0.0}},
    # リュウグウ: 全球等角図が公開されていない（USGS の全球モザイク倉庫に該当キー0件・実測、
    # NASA Trek もベースマップ層を配る索引サービスが HTTP 500 で層IDを取れない）。座標グリッドで示す。
    "ryugu": {"ja": "リュウグウ", "en": "Ryugu", "basemap": "graticule"},
}

# 天体ごとの地点表。新しい地点は該当天体の dict に1行追加するだけ。
# 座標は公開表の値をそのまま使う（基準面・誤差は note/出典に書く）。
SITES: dict = {
    "moon": {
        # アポロ: NASA NSSDC（LRO 画像・Wagner et al., Icarus 283 (2017)）
        "apollo11": {"ja": "アポロ11号", "en": "Apollo 11", "lat": 0.67416, "lon": 23.4731,
                     "place": "静かの海", "landed": "1969-07-20",
                     "flag": "倒れている（帰還時の上昇エンジン噴射で倒れた。LROC 画像に旗の影なし）"},
        "apollo12": {"ja": "アポロ12号", "en": "Apollo 12", "lat": -3.0128, "lon": -23.4219,
                     "place": "嵐の大洋", "landed": "1969-11-19",
                     "flag": "立っている（LROC 時系列5枚で旗の強い影）"},
        "apollo14": {"ja": "アポロ14号", "en": "Apollo 14", "lat": -3.64589, "lon": -17.4719,
                     "place": "フラ・マウロ", "landed": "1971-02-05",
                     "flag": "LROC では旗の影が不明瞭（Sバンドアンテナ・MET の影は見える）"},
        "apollo15": {"ja": "アポロ15号", "en": "Apollo 15", "lat": 26.13239, "lon": 3.6333,
                     "place": "ハドリー・リル", "landed": "1971-07-30",
                     "flag": "LM 離陸の数時間後も立っていた（LRV の TV カメラ）。LROC では旗の影が不明瞭"},
        "apollo16": {"ja": "アポロ16号", "en": "Apollo 16", "lat": -8.9734, "lon": 15.5011,
                     "place": "デカルト高地", "landed": "1972-04-20",
                     "flag": "立っている（LROC 時系列7枚で旗の強い影）"},
        "apollo17": {"ja": "アポロ17号", "en": "Apollo 17", "lat": 20.1911, "lon": 30.7723,
                     "place": "タウルス・リトロー", "landed": "1972-12-11",
                     "flag": "立っている（LROC 時系列6枚で強い影。LRO を太陽側へ19°振って撮影）"},
        # その他の月面機材: LROC 2016 座標表（平均実測・不確かさ 0.3〜4 m）
        "change3": {"ja": "嫦娥3号", "en": "Chang'e 3", "lat": 44.1214, "lon": -19.5117,
                    "place": "雨の海（虹の入り江）", "landed": "2013-12-14", "flag": "月面車「玉兔」を展開"},
        "lunokhod1": {"ja": "ルノホート1号（ルナ17号）", "en": "Luna 17 / Lunokhod 1",
                      "lat": 38.23764, "lon": -35.0016, "place": "雨の海",
                      "landed": "1970-11-17", "flag": "初の月面車（1971年に運用終了）"},
        "lunokhod2": {"ja": "ルノホート2号（ルナ21号）", "en": "Luna 21 / Lunokhod 2",
                      "lat": 25.8323, "lon": 30.9222, "place": "ル・モニエ・クレーター",
                      "landed": "1973-01-15", "flag": "月面車（1973年に運用終了）"},
        "surveyor3": {"ja": "サーベイヤー3号", "en": "Surveyor 3", "lat": -3.0162, "lon": -23.418,
                      "place": "嵐の大洋", "landed": "1967-04-20",
                      "flag": "アポロ12号の乗員が訪れた（1969）"},
        # 新しく形成されたクレーター（LROC が LRO 運用中に before/after 比較で確認した地形）。
        # 座標は LROC 公表値（月面座標系・東向き正）。kind="crater"=自然衝突、"impact"=人工物。
        "mcgetchin": {"ja": "マクゲッチン", "en": "McGetchin", "lat": 1.3536, "lon": 67.1765,
                      "place": "月の東端（Dubyago N の南東・海と高地の境界）",
                      "landed": "2024-04-11〜05-22", "kind": "crater",
                      "event": "2024-04-11〜2024-05-22 の間",
                      "status": "自然衝突でできた最新のクレーター（直径 222 m・深さ 43 m。"
                                "LROC が 2025-10-24 に発見し、IAU 命名は 2026-05-04）",
                      "note": "**マクゲッチン・クレーター**は LROC が前後比較で確認した自然形成の"
                              "クレーター。直径 222 m・深さ 43 m・リムは平均 8 m 盛り上がり、"
                              "壁の傾斜は平均 24°（最大 40°近く）。衝突天体は 3〜6 階建て相当で、"
                              "この規模の衝突は月で平均 132 年に1回。形成時期は前後画像から "
                              "2024-04-11〜2024-05-22 の間に絞られ、LROC が 2025-10-24 に発見、"
                              "IAU が 2026-05-04 に命名。出典: LROC Featured Image 1501"
                              "（M1542395927）／Science Advances aeh7812／IAU Gazetteer 16447"},
        "falcon9": {"ja": "ファルコン9上段", "en": "Falcon 9 upper stage", "lat": 19.4759,
                    "lon": 266.7138, "place": "月の表側西の縁（アインシュタイン・クレーター付近）",
                    "landed": "2026-08-05", "kind": "impact",
                    "event": "2026-08-05 06:35 UTC",
                    "status": "人工物の衝突でできた痕跡（直径 18 m・深さ 3 m 未満）",
                    "note": "**ファルコン9上段の衝突痕**は、確認された人工物の月面衝突では最も"
                            "新しいもの。SpaceX ファルコン9の上段が 2026-08-05 06:35 UTC に"
                            "水平から約 31° で衝突し、直径 18 m・深さ 3 m 未満のクレーターを"
                            "作った。衝突地点は JPL CNEOS が予測し、KPLO（Danuri）が数時間後に、"
                            "LRO が 6 日後に撮影して確認。出典: LROC Featured Image 1499"
                            "（M1541091337）／JPL CNEOS 2025-010D"},
    },
    "venus": {
        # ヴェネラ計画の着陸地点（出典: Wikipedia「Venera program」の飛行データ表＝NSSDC 準拠）。
        # 注意: 8/9/10号は「半径150km以内」として公表されており、点ではなく範囲の精度。
        "venera7": {"ja": "ベネラ7号", "en": "Venera 7", "lat": -5.0, "lon": -9,
                    "place": "金星面（昼側低地）", "landed": "1970-12-15",
                    "status": "他天体への初の軟着陸・地表からの初通信（23分で途絶）"},
        "venera8": {"ja": "ベネラ8号", "en": "Venera 8", "lat": -10.70, "lon": -24.75,
                    "place": "昼側の高地寄り", "landed": "1972-07-22",
                    "status": "着陸地点は**半径150km以内**として公表（50分運用）"},
        "venera9": {"ja": "ベネラ9号", "en": "Venera 9", "lat": 31.01, "lon": -68.36,
                    "place": "ベータ地域の斜面", "landed": "1975-10-22",
                    "status": "他天体表面の初の画像（**半径150km以内**として公表／53分運用）"},
        "venera10": {"ja": "ベネラ10号", "en": "Venera 10", "lat": 15.42, "lon": -68.49,
                     "place": "ベータ地域", "landed": "1975-10-25",
                     "status": "**半径150km以内**として公表（65分運用）"},
        "venera11": {"ja": "ベネラ11号", "en": "Venera 11", "lat": -14.0, "lon": -61,
                     "place": "金星面", "landed": "1978-12-25", "status": "着陸成功（撮像系は失敗）"},
        "venera12": {"ja": "ベネラ12号", "en": "Venera 12", "lat": -7.0, "lon": -66,
                     "place": "金星面", "landed": "1978-12-21", "status": "雷とみられる信号を記録"},
        "venera13": {"ja": "ベネラ13号", "en": "Venera 13", "lat": -7.083, "lon": -57,
                     "place": "フォエベ地域の東", "landed": "1982-03-01",
                     "status": "初のカラー画像・土壌分析（127分運用）"},
        "venera14": {"ja": "ベネラ14号", "en": "Venera 14", "lat": -13.417, "lon": -50,
                     "place": "フォエベ地域の東（13号の南西約950km）", "landed": "1982-03-05",
                     "status": "土壌分析（ソレアイト質玄武岩／57分運用）"},
        "vega2": {"ja": "ベガ2号", "en": "Vega 2", "lat": -8.083, "lon": 177.117,
                  "place": "アフロディーテ大陸の北", "landed": "1985-06-15",
                  "status": "ハレー彗星探査と兼用の着陸機（56分運用）"},
    },
    "mars": {
        # 火星の着陸地点（出典: The Planetary Society「Map of all Mars landing sites」が
        # 各地点に付した一次出典＝NSSDC／Arvidson et al. 2006／Squyres et al. 2006／
        # Vasavada et al. 2014／Golombek et al. 2019／HiRISE チーム提供図）。
        # 経度は東向き正に統一（元が西経の場合は変換）。
        "mars2": {"ja": "マルス2号", "en": "Mars 2", "lat": -45.0, "lon": 47,
                  "place": "ヘラス平原", "landed": "1971-11-27",
                  "status": "着陸失敗（衝突。火星表面に到達した初の人工物）"},
        "mars3": {"ja": "マルス3号", "en": "Mars 3", "lat": -45.0, "lon": -158,
                  "place": "シレーヌス台地", "landed": "1971-12-02",
                  "status": "初の軟着陸だが14.5秒で通信途絶"},
        "mars6": {"ja": "マルス6号", "en": "Mars 6", "lat": -23.90, "lon": -19.42,
                  "place": "マルガリティフェル・テラ", "landed": "1974-03-12",
                  "status": "降下中は通信、着陸後に途絶"},
        "viking1": {"ja": "バイキング1号", "en": "Viking 1", "lat": 22.48, "lon": -49.97,
                    "place": "クリュセ平原", "landed": "1976-07-20",
                    "status": "初の火星での長期運用（6年）"},
        "pathfinder": {"ja": "マーズ・パスファインダー", "en": "Mars Pathfinder",
                       "lat": 19.33, "lon": -33.55, "place": "アレス谷", "landed": "1997-07-04",
                       "status": "ローバー「ソジャーナ」を初運用（エアバッグ着陸）"},
        "beagle2": {"ja": "ビーグル2号", "en": "Beagle 2", "lat": 10.6, "lon": 90,
                    "place": "イシディス平原", "landed": "2003-12-25",
                    "status": "着陸したが通信せず（2015年に画像で発見）"},
        "spirit": {"ja": "スピリット", "en": "Spirit (MER-A)", "lat": -14.571892, "lon": 175.478,
                   "place": "グセフクレーター", "landed": "2004-01-04",
                   "status": "MER-A。2010年に砂に埋まり通信途絶"},
        "opportunity": {"ja": "オポチュニティ", "en": "Opportunity (MER-B)", "lat": -1.9462,
                        "lon": -5.5266, "place": "メリディアニ平原", "landed": "2004-01-25",
                        "status": "MER-B。14年以上運用（2018年の砂嵐で終了）"},
        "phoenix": {"ja": "フェニックス", "en": "Phoenix", "lat": 68.218830, "lon": -125.749,
                    "place": "北極地域（ヴァスティタス・ボレアリス）", "landed": "2008-05-25",
                    "status": "北極域に軟着陸し水氷を確認"},
        "curiosity": {"ja": "キュリオシティ", "en": "Curiosity (MSL)", "lat": -4.5895,
                      "lon": 137.442, "place": "ゲールクレーター", "landed": "2012-08-06",
                      "status": "運用継続中（MMGIS で走行経路も取得可能）"},
        "insight": {"ja": "インサイト", "en": "InSight", "lat": 4.502, "lon": 135.623,
                    "place": "エリシウム平原", "landed": "2018-11-26",
                    "status": "固定式着陸機・初の火星地震観測（2022年に運用終了）"},
        "perseverance": {"ja": "パーサヴィアランス", "en": "Perseverance (Mars 2020)",
                         "lat": 18.4447, "lon": 77.4508, "place": "ジェゼロクレーター",
                         "landed": "2021-02-18",
                         "status": "運用継続中（実測位置は NASA MMGIS。サンプル採取を継続）"},
    },
    "titan": {
        # ホイヘンス着陸点。出典: Karkoschka et al. 2011（再決定値 192.335°W / 10.573°S）。
        # タイタンの経度は 0〜360°W の慣例で公表 → ここでは東向き正（167.665°E）に変換。
        "huygens": {"ja": "ホイヘンス", "en": "Huygens", "lat": -10.573, "lon": 167.665,
                    "place": "アディリ地域", "landed": "2005-01-14",
                    "status": "他天体で最も遠い着陸（ESA。経度は System 慣例 192.335°W を変換）"},
    },
    "jupiter": {
        # シューメーカー・レヴィ第9彗星の衝突地点（全23破片）。
        # 出典: PDS Atmospheres（Chodas & Yeomans 1996, IAU Colloq.156 の Table 5）
        # 緯度=木星中心緯度 / 経度=System III（**西向き**）→ 東向き正に変換して保持。
        # 衝突は 1994-07-16〜22（UTC、光行時を含む）。衝突面は 100 mbar 面。
        **{
            "sl9_a": {"ja": "SL9 破片 A", "en": "SL9 fragment A",
                       "lat": -43.35, "lon": 176, "place": "衝突 20:10:40 UTC（100 mbar 面）",
                       "landed": "1994-07-16",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_b": {"ja": "SL9 破片 B", "en": "SL9 fragment B",
                       "lat": -43.22, "lon": -67, "place": "衝突 02:50:00 UTC（100 mbar 面）",
                       "landed": "1994-07-17",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_c": {"ja": "SL9 破片 C", "en": "SL9 fragment C",
                       "lat": -43.47, "lon": 138, "place": "衝突 07:10:50 UTC（100 mbar 面）",
                       "landed": "1994-07-17",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_d": {"ja": "SL9 破片 D", "en": "SL9 fragment D",
                       "lat": -43.53, "lon": -33, "place": "衝突 11:52:30 UTC（100 mbar 面）",
                       "landed": "1994-07-17",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_e": {"ja": "SL9 破片 E", "en": "SL9 fragment E",
                       "lat": -43.54, "lon": -153, "place": "衝突 15:11:40 UTC（100 mbar 面）",
                       "landed": "1994-07-17",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_f": {"ja": "SL9 破片 F", "en": "SL9 fragment F",
                       "lat": -43.68, "lon": -135, "place": "衝突 00:35:45 UTC（100 mbar 面）",
                       "landed": "1994-07-18",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_g": {"ja": "SL9 破片 G", "en": "SL9 fragment G",
                       "lat": -43.66, "lon": -26, "place": "衝突 07:33:33 UTC（100 mbar 面）",
                       "landed": "1994-07-18",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_h": {"ja": "SL9 破片 H", "en": "SL9 fragment H",
                       "lat": -43.79, "lon": -99, "place": "衝突 19:31:59 UTC（100 mbar 面）",
                       "landed": "1994-07-18",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_j": {"ja": "SL9 破片 J", "en": "SL9 fragment J",
                       "lat": -43.75, "lon": 44, "place": "衝突 01:35:00 UTC（100 mbar 面）",
                       "landed": "1994-07-19",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_k": {"ja": "SL9 破片 K", "en": "SL9 fragment K",
                       "lat": -43.86, "lon": 82, "place": "衝突 10:24:17 UTC（100 mbar 面）",
                       "landed": "1994-07-19",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_l": {"ja": "SL9 破片 L", "en": "SL9 fragment L",
                       "lat": -43.96, "lon": 12, "place": "衝突 22:16:49 UTC（100 mbar 面）",
                       "landed": "1994-07-19",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_m": {"ja": "SL9 破片 M", "en": "SL9 fragment M",
                       "lat": -43.93, "lon": 96, "place": "衝突 06:00:00 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_n": {"ja": "SL9 破片 N", "en": "SL9 fragment N",
                       "lat": -44.31, "lon": -71, "place": "衝突 10:29:20 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_p2": {"ja": "SL9 破片 P2", "en": "SL9 fragment P2",
                       "lat": -44.69, "lon": 111, "place": "衝突 15:21:11 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_p1": {"ja": "SL9 破片 P1", "en": "SL9 fragment P1",
                       "lat": -45.02, "lon": 67, "place": "衝突 16:32:35 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_q2": {"ja": "SL9 破片 Q2", "en": "SL9 fragment Q2",
                       "lat": -44.32, "lon": -46, "place": "衝突 19:44:00 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_q1": {"ja": "SL9 破片 Q1", "en": "SL9 fragment Q1",
                       "lat": -44.0, "lon": -63, "place": "衝突 20:13:53 UTC（100 mbar 面）",
                       "landed": "1994-07-20",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_r": {"ja": "SL9 破片 R", "en": "SL9 fragment R",
                       "lat": -44.1, "lon": -42, "place": "衝突 05:34:57 UTC（100 mbar 面）",
                       "landed": "1994-07-21",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_s": {"ja": "SL9 破片 S", "en": "SL9 fragment S",
                       "lat": -44.22, "lon": -33, "place": "衝突 15:16:30 UTC（100 mbar 面）",
                       "landed": "1994-07-21",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_t": {"ja": "SL9 破片 T", "en": "SL9 fragment T",
                       "lat": -45.01, "lon": -141, "place": "衝突 18:09:56 UTC（100 mbar 面）",
                       "landed": "1994-07-21",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_u": {"ja": "SL9 破片 U", "en": "SL9 fragment U",
                       "lat": -44.48, "lon": 82, "place": "衝突 22:00:02 UTC（100 mbar 面）",
                       "landed": "1994-07-21",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_v": {"ja": "SL9 破片 V", "en": "SL9 fragment V",
                       "lat": -44.47, "lon": -149, "place": "衝突 04:23:20 UTC（100 mbar 面）",
                       "landed": "1994-07-22",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
            "sl9_w": {"ja": "SL9 破片 W", "en": "SL9 fragment W",
                       "lat": -44.13, "lon": 77, "place": "衝突 08:06:16 UTC（100 mbar 面）",
                       "landed": "1994-07-22",
                       "status": "衝突痕（数日〜数週間で大気に流されて消えた）"},
        },
    },
}
# 地点マーカー図に必ず添える天体ごとの但し書き（固体表面の有無・座標の基準面）
_BODY_MAP_NOTES = {
    "venus": "金星の全球図は USGS/NASA の **Magellan C3-MDIR 合成図**（レーダー＋地形の彩色）。"
             "着陸地点の多くは**半径150km以内**として公表されており、点ではなく範囲の精度（ベネラ8・9・10号）。"
             "地表は約460℃・90気圧で、機体は数十分〜2時間で停止した",
    "titan": "タイタンの全球図は NASA/JPL（Cassini）の全球図。タイタンの経度は**0〜360°W の慣例**で"
             "公表されるため、この図では東向き正に変換している（ホイヘンスは 192.335°W＝167.665°E）",
    "io": "イオに着陸機は無い（Voyager/Galileo などの周回機による観測のみ）。全球図は USGS の地質図"
          "（下地が観測画像）＝概要画像",
    "europa": "エウロパに着陸機は無い（周回機観測のみ）。全球図は USGS/NASA の Voyager+Galileo モザイク",
    "ganymede": "ガニメデに着陸機は無い。全球図は USGS/NASA の地質図＋画像モザイク（概要画像）",
    "callisto": "カリストに着陸機は無い。全球図は USGS の全球モザイク（縮小版）",
    "mars": "火星の座標は東向き正に統一（元が西経の値は変換）。着陸地点は公表値で、"
            "マーズ2号・3号・6号・ビーグル2号は着陸に失敗または通信が確立していない",
    "jupiter": "木星は**固体表面が無い**ガス惑星。SL9 衝突地点の緯度は木星中心緯度、経度は"
               "System III（**西向き**で公表）→この図では東向き正に変換。衝突面は 100 mbar 面で、"
               "衝突痕は数日〜数週間で大気に流されて消えた（恒久的な地形ではない）。"
               "**ベースマップは Hubble OPAL の全球図＝その年の大気**なので1994年の衝突当時の"
               "模様ではなく、衝突痕そのものも写っていない",
    "saturn": "土星は**固体表面が無い**ガス惑星。座標は大気の緯度経度（雲頂基準）。"
              "ベースマップは Hubble OPAL の全球図（その年の大気）",
    "uranus": "天王星は**固体表面が無い**ガス惑星。ベースマップは Hubble OPAL の全球図"
              "（その年の大気。縞は淡い）",
    "neptune": "海王星は**固体表面が無い**ガス惑星。ベースマップは Hubble OPAL の全球図"
               "（その年の大気。大暗斑は数年で消える）",
    "pluto": "冥王星の全球図は New Horizons（2015年7月の接近時）の**モザイク**。"
             "左端が東経 0° の図なので半周回して描いている（緯度経度は東向き正）",
    "bennu": "ベンヌの全球図は OSIRIS-REx の**全球モザイク**。直径約 490 m の小型天体で、"
             "着陸候補地点（Nightingale 等）は公表座標に基づく",
}
# 地点が着陸地点でない場合（新クレーター等）の出典。着陸地点の出典一覧を出すと図と文が食い違う
_SITES_SOURCE_CRATER = ("座標: LROC 公表値（月面座標系・東向き正）／地図: NASA Trek LRO WAC"
                        "（全球画像が無い天体は緯度経度グリッド）")
_SITES_SOURCE = ("座標: NASA NSSDC（アポロ・LRO 画像 Wagner+2017）／LROC 2016 座標表（嫦娥3・ルノホート・"
                 "サーベイヤー）／金星は Wikipedia「Venera program」飛行データ表（NSSDC 準拠）／"
                 "火星は The Planetary Society の着陸地点一覧が付す一次出典（NSSDC・Arvidson+2006・"
                 "Squyres+2006・Vasavada+2014・Golombek+2019・HiRISE 提供図）／SL9 は PDS Atmospheres"
                 "（Chodas & Yeomans 1996, IAU Colloq.156）／地図: NASA Trek の全球タイル（月・火星・水星・ベスタ・ケレス）、各天体の1枚の"
                 "全球図（Hubble OPAL・USGS/NASA モザイク・New Horizons・OSIRIS-REx 等。credit は"
                 "注記のとおり）、全球画像が無い天体は緯度経度グリッド）")


def _impact_site_result(body_cfg: dict, info: dict, span_deg: float = 60.0,
                         zoom: int = 3, out_px: int = 900):
    """落点（地点マーカー）を天体面地図に描く。落点が公表されている機体で使う。

    地図の上に何かを描く処理は surface_map に集約済みなので、ここは
    「どの座標に・どんなラベルで・どんな注記を付けるか」だけを持つ。
    タイル取得に失敗した場合は None を返し、呼び出し側が座標だけのテキスト応答に落とす。
    """
    from PIL import ImageDraw
    imp = info.get("impact") or {}
    lat, lon = float(imp["lat"]), float(imp["lon"])
    try:
        sv = surface_view(body_cfg, lon, lat, span_deg, zoom, out_px,
                          whole_dim=0.75, regional_dim=0.7)
    except Exception:
        return None
    img = sv["img"]
    g2px = sv["g2px"]
    cx, cy = g2px(lon, lat)
    d = ImageDraw.Draw(img)
    iw, ih = img.size
    r = max(11, iw // 55)
    draw_marker(d, (cx, cy), r, fill=(255, 60, 30),
                outline_w=max(3, int(r * 0.25)), inner_ratio=0.5)
    f_big = load_font(max(18, iw // 32), bold=True)
    f_mid = load_font(max(14, iw // 44), bold=True)
    f_sm = load_font(max(12, iw // 56), bold=True)
    ns = "北緯" if lat >= 0 else "南緯"; ew = "東経" if lon >= 0 else "西経"
    title = f"{info['ja']} の{body_cfg['ja']}面落下地点"
    row1 = f"座標: {ns} {abs(lat):.2f}° / {ew} {abs(lon):.2f}°（{imp.get('place', '')}）"
    row2 = f"落下: {imp.get('when_utc', '')}（{imp.get('when_jst', '')}）  地図: {body_cfg['attrib']}"
    # 文字は地図の**外**（下の帯）に置く＝マーカーが文字で隠れない（実測の失敗の型を潰す）
    band_rows = [title, row1, row2]
    img, map_h = add_legend_band(
        img, band_rows,
        font=fit_font_for_width(iw, band_rows[1:], [iw // 44, iw // 52, iw // 62, iw // 72]),
        title_font=fit_font_for_width(iw, band_rows[:1], [iw // 30, iw // 34, iw // 40]))
    d = ImageDraw.Draw(img)
    # マーカーラベル（マーカーを覆わない位置に置く。覆ったら検証で落とす）
    lab_w, lab_h = int(iw * 0.30), ih // 14
    margin = r * 3
    if cx + margin + lab_w < iw - 10:
        lx, ly = cx + margin, cy - lab_h // 2
    elif cx - margin - lab_w > 10:
        lx, ly = cx - margin - lab_w, cy - lab_h // 2
    else:
        lx, ly = max(10, cx - lab_w // 2), min(ih - lab_h - 10, cy + margin)
    d.rounded_rectangle([lx, ly, lx + lab_w, ly + lab_h], radius=8, fill=(200, 0, 0, 235))
    d.text((lx + 8, ly + 5), f"{info['ja']} 落下地点", font=f_mid, fill=(255, 255, 255))
    marker_visible = not (lx <= cx <= lx + lab_w and ly <= cy <= ly + lab_h)

    mv = verify_marker(img, (cx, cy), lon, lat, sv["inv"],
                       r=marker_scan_radius(r),
                       tol_deg=max(0.01, 1.6 * float(sv.get("deg_per_px") or 0.0)))
    mv["marker_visible"] = bool(marker_visible)
    mv["legend_band_below_map"] = True          # 文字は地図の外（下の帯）にある
    mv["map_height_px"] = int(map_h)
    mv["ok"] = bool(marker_visible and mv["ok"])
    jpeg = encode_jpeg(img)
    out_path = save_output(jpeg, "planetary_impact_site", "jpg")
    fig = figure_payload(
        kind="impact_site_map",
        title=f"{info['ja']} の{body_cfg['ja']}面落下地点（{body_cfg['attrib']}）",
        view=view_spec("body_surface", "equirectangular",
                       f"{body_cfg['ja']}面の緯度経度図（等角図法）。赤●＝公表された落下地点",
                       why="落下地点は天体面の座標なので、天体面の地図に置くのが最も誤読が少ない"
                           "（軌道図や全天図では位置を表せない）"),
        primary=primary_spec(body_cfg["ja"], "surface_map",
                             note="天体面の地図なので主天体は図の中心に置いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["落下地点マーカー（実寸ではない）"]),
        markers=[{"id": "impact", "label": f"{info['ja']} 落下地点",
                  "lat": round(lat, 4), "lon": round(lon, 4), "px": [round(cx), round(cy)],
                  "radius": int(r)}],
        notes=figure_notes(extra=[
            "赤●は JAXA が公表した落下地点の座標に置いたマーカー。"
            "**衝突でできた新しいクレータを画像から同定したものではない**（この地図の撮像時期とも異なる）",
            "等角図法のため高緯度ほど東西方向が圧縮されて見える",
            f"表示は局所 {span_deg:.0f}°（ズーム {sv['zoom']}）。地形の陰影・解像度は地図タイル"
            f"（NASA Trek の{body_cfg['attrib']}）に依存する",
            f"落下時刻 {imp.get('when_utc', '')}（{imp.get('when_jst', '')}）",
            imp.get("note", ""),
            "出典: " + imp.get("source", ""),
            tile_failure_note(sv),          # タイルが欠けたら「背景色のまま」と明示する
        ]),
        caption=f"{info['ja']} の{body_cfg['ja']}面落下地点（{ns} {abs(lat):.2f}° / {ew} {abs(lon):.2f}°、"
                f"{imp.get('place', '')}、{imp.get('when_utc', '')}）。"
                f"NASA Trek の{body_cfg['ja']}面図（等角図法）に公表座標をマーカーで示した図。",
        verify=mv,
    )
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg",
                        altText=f"{info['ja']} の{body_cfg['ja']}面落下地点")
    lines = [
        media_link_line(f"{info['ja']} の{body_cfg['ja']}面 落下地点マップ",
                        path=out_path, kind="figure"),
        f"📍 **{info['ja']} の{body_cfg['ja']}面落下地点（判明・JAXA 公表）**",
        f"座標: {ns} {abs(lat):.1f}° / {ew} {abs(lon):.1f}°（{imp.get('place', '')}）",
        f"落下時刻: {imp.get('when_utc', '')}（{imp.get('when_jst', '')}）",
        "🌑 " + imp.get("note", ""),
        "",
        figure_text_block(fig),
        "出典: " + imp.get("source", "") + f" ／ 地図 {body_cfg['attrib']}",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "body": body_cfg["ja"], "spacecraft": info["ja"], "name_en": info["en"],
            "status": "past mission", "note": info["note"],
            "impact_site": dict(imp), "latitude": round(lat, 4), "longitude": round(lon, 4),
            "figure": fig, "image_path": out_path,
            "source": imp.get("source", ""),
        },
    )


def _sites_result(body: str, key, out_px: int = 1000):
    """天体面の地点マーカー図を返す（**任意の天体**に対応）。

    全球等角図がある天体（NASA Trek）は地形画像の上に、無い天体（ガス惑星など）は
    緯度経度グリッドの上に、公表された座標をそのまま置く。
    1地点なら局所図（名前つきマーカー）、2地点以上なら全面図に番号＋凡例。
    """
    from PIL import ImageDraw
    b = str(body or "").strip().lower()
    mb = MAP_BODIES.get(b)
    if mb is None:
        msg = ("この天体 '{}' は地点マーカー図に未対応です。対応: {}").format(
            body, ", ".join(sorted(MAP_BODIES)))
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": "unknown body", "body": body,
                                                 "known_bodies": sorted(MAP_BODIES)})
    table = SITES.get(b, {})
    body_ja = mb["ja"]
    keys = split_names(key) or ["all"]
    map_only = len(keys) == 1 and keys[0].strip().lower() in ("map", "地図", "only")
    _APOLLO = ["apollo11", "apollo12", "apollo14", "apollo15", "apollo16", "apollo17"]
    # 月に新しく作られたクレーター（人工物の衝突を含む・新しい順）
    _NEW_CRATERS = ["falcon9", "mcgetchin"]
    _NEW_CRATER_KEYS = ("newcrater", "newcraters", "new_crater", "new_craters",
                        "新クレーター", "新しいクレーター", "最新クレーター")
    if len(keys) == 1 and keys[0].strip().lower() in _NEW_CRATER_KEYS:
        if b != "moon":
            msg = "'{}' は月面専用の指定です。天体 {} の地点: {}".format(
                keys[0], body_ja, ", ".join(sorted(table)) or "なし")
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": "wrong body", "body": b})
        keys = list(_NEW_CRATERS)
    if len(keys) == 1 and keys[0].lower() in ("all", "apollo", "アポロ"):
        if keys[0].lower() in ("apollo", "アポロ") and b != "moon":
            msg = "'{}' は月面専用の指定です。天体 {} の地点: {}".format(
                keys[0], body_ja, ", ".join(sorted(table)) or "なし")
            return CallToolResult(content=[TextContent(type="text", text=msg)],
                                  structuredContent={"error": "wrong body", "body": b})
        keys = (_APOLLO if keys[0].lower() in ("apollo", "アポロ") else list(table))
    if not table and not map_only:
        msg = ("天体 '{}' には登録された地点がありません。地点がある天体: {}"
               "（地図だけ見たい場合は sites=\"map\"）").format(
            body_ja, ", ".join(k for k, v in SITES.items() if v))
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": "no sites", "body": b,
                                                 "known_bodies": [k for k, v in SITES.items() if v]})
    unknown = [] if map_only else [k for k in keys if k.strip().lower() not in table]
    if unknown:
        msg = "不明な地点 '{}'。天体 {} で指定できる地点: {}".format(
            "、".join(unknown), body_ja, ", ".join(sorted(table)))
        return CallToolResult(content=[TextContent(type="text", text=msg)],
                              structuredContent={"error": "unknown site", "unknown": unknown,
                                                 "known": sorted(table)})
    picked = [] if map_only else [dict(table[k.strip().lower()]) for k in keys]
    # 地図の種類（タイル / 1枚画像 / 座標グリッド）と帰属表示
    mode = mb.get("basemap", "graticule")
    img_cfg = mb.get("image") if mode == "image" else None
    cfg = BODIES.get(b) if mode == "trek" else None
    if mode == "trek" and cfg is None:
        return CallToolResult(
            content=[TextContent(type="text", text="天体 '{}' の地図設定がありません".format(b))],
            structuredContent={"error": "no tile config", "body": b})
    if mode == "image" and not img_cfg:
        return CallToolResult(
            content=[TextContent(type="text", text="天体 '{}' の全球画像設定がありません".format(b))],
            structuredContent={"error": "no image config", "body": b})
    attrib = (cfg["attrib"] if cfg else
              (img_cfg.get("credit", "全球画像") if img_cfg else "緯度経度グリッド（地形画像なし）"))
    single = len(picked) == 1
    span_deg = 10.0 if single else 360.0
    zoom = 4 if single else 2
    # 地点指定なし（sites="map"）でもベースマップ図を返せるよう、既定地点を持たせる
    site = picked[0] if picked else {"lat": 0.0, "lon": 0.0, "ja": body_ja, "en": body_ja,
                                     "place": "", "landed": "", "status": ""}
    # 複数地点: 天体全面図では 1° = out_px/360 px なので、**最小間隔から半径を決める**。
    # 実測: アポロ12号と14号は 15px しか離れておらず、半径15px のマーカーが重なって
    # 互いの白い縁が中心を貫いた（文字を帯へ移しただけでは直らない第2の欠陥）。
    requested_out_px = out_px
    r_gap = 8                      # 地点なし（地図のみ）でも半径計算が落ちないよう既定値
    if len(picked) > 1:
        def _min_sep(px_w: float) -> float:
            k = px_w / 360.0
            def _delta_lon(a, b):
                return ((float(a["lon"]) - float(b["lon"]) + 180.0) % 360.0) - 180.0
            return min(math.hypot(_delta_lon(a, b) * k, (a["lat"] - b["lat"]) * k)
                       for i, a in enumerate(picked) for b in picked[i + 1:])
        for cand in (out_px, 1200, 1600):
            out_px = max(out_px, cand)
            if max(6.0, _min_sep(out_px) / 2.0 - 3.0) >= 7.0 or out_px >= 1600:
                break
        r_gap = max(6, int(_min_sep(out_px) / 2.0 - 3.0))
        upscaled = out_px if out_px == requested_out_px else out_px  # 下で注記に使う
    # 経度は公表値が 0〜360°E のことがある（例: ファルコン9上段 266.7138°E）。地図の投影は
    # -180〜180° を前提とするため、ここで折り返す（折り返さないと画像サイズが負になる：実測）。
    _site_lon = ((float(site["lon"]) + 180.0) % 360.0) - 180.0
    basemap_fallback = None
    try:
        if mode == "trek":
            sv = surface_view(cfg, _site_lon, float(site["lat"]), span_deg, zoom, out_px,
                              whole_dim=0.80, regional_dim=0.80)
        elif mode == "image":
            sv = image_view(img_cfg["url"], _site_lon, float(site["lat"]), span_deg,
                            out_px, credit=img_cfg.get("credit", ""), headers=BASEMAP_UA,
                            whole_dim=0.88, regional_dim=0.88,
                            left_edge_lon=img_cfg.get("left_edge_lon"))
        else:
            # 全球地形画像が無い天体（ガス惑星・小型天体）: 緯度経度グリッドに座標だけ描く
            sv = graticule_view(_site_lon, float(site["lat"]), span_deg, out_px,
                                body_ja=body_ja)
    except Exception as e:
        if mode == "image":
            # 画像が取れないときは座標グリッドへ落として描く（図を返さずに終わらせない）
            basemap_fallback = "{}: {}".format(type(e).__name__, str(e)[:120])
            try:
                sv = graticule_view(_site_lon, float(site["lat"]), span_deg, out_px,
                                    body_ja=body_ja)
                mode, attrib = "graticule", "緯度経度グリッド（全球画像を取得できず代替）"
            except Exception as e2:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"{body_ja}の地図の生成に失敗しました: {e2}")],
                    structuredContent={"error": str(e2), "source": attrib})
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"{body_ja}の地図の生成に失敗しました: {e}")],
                structuredContent={"error": str(e), "source": attrib})
    img = sv["img"]
    g2px = sv["g2px"]
    d = ImageDraw.Draw(img)
    iw, ih = img.size
    r = max(7, iw // 60) if single else min(max(6, iw // 90), max(6, int(r_gap)))
    f_big = load_font(max(18, iw // 30), bold=True)
    f_mid = load_font(max(14, iw // 44), bold=True)
    f_sm = load_font(max(12, iw // 56), bold=True)
    marks = []
    skipped_numerals: list = []
    for i, st in enumerate(picked, 1):
        # 表記ゆれ（0〜360°E や西経の値）で画面外に描かないよう、必ず折り返してから投影する。
        # 実測: 金星の 351°E をそのまま渡して x=1770px（画像幅1200）になり画面外に出た。
        _lon = ((float(st["lon"]) + 180.0) % 360.0) - 180.0
        x, y = g2px(_lon, float(st["lat"]))
        marks.append({"id": st["en"].lower().replace(" ", ""), "label": st["ja"],
                      "lat": round(float(st["lat"]), 5), "lon": round(_lon, 5),
                      "px": [round(x), round(y)], "radius": r,
                      "numeral": "①②③④⑤⑥⑦⑧⑨⑩"[i - 1] if i <= 10 else str(i)})
    # マーカーの半径は**画像サイズ基準**で決める（最小間隔に合わせると全部が点になる。
    # 実測: 半径を最小間隔まで縮めたら r=3px になった）。近すぎて重なる地点は縮めず、
    # 「近傍の1点」にまとめて、まとめた相手を注記に列挙する（重ねて描かない）。
    merged: dict = {}
    if len(marks) > 1:
        r = max(5, min(10, iw // 140))
        keep_m, keep_p = [], []
        for m2, st2 in zip(marks, picked):
            host = next((k for k in keep_m
                         if math.hypot(m2["px"][0] - k["px"][0], m2["px"][1] - k["px"][1])
                         <= 2 * r + 2), None)
            if host is None:
                keep_m.append(m2); keep_p.append(st2)
            else:
                merged.setdefault(host["label"], []).append(m2["label"])
        for m2 in keep_m:
            m2["radius"] = r
        marks, picked = keep_m, keep_p
    if not marks:
        pass                                   # 地点指定なし（sites="map"）: ベースマップのみ
    elif single:
        draw_marker(d, tuple(marks[0]["px"]), r, fill=(255, 60, 30),
                    outline_w=max(3, int(r * 0.25)), inner_ratio=0.5)
    else:
        # 複数地点は共通ルーチンでまとめて描き、番号だけを各マーカー脇に置く
        # 縁の太さは半径に比例（画像幅に比例させると小さいマーカーが白い輪だけになる）
        draw_markers(d, [tuple(m["px"]) for m in marks], radius=r, fill=(255, 60, 30),
                     outline_w=max(2, int(r * 0.25)))
        # 番号は「他のマーカーに載らない位置」へ機械的に置く（実測: 隣のマーカーに番号が
        # 載って赤いコアが半分隠れた）。どこにも置けなければ描かず、凡例の番号に任せる。
        margin = r + 4
        placed_boxes = []
        for m in marks:
            mx, my = m["px"]
            cands = [(mx + margin, my - margin, "la"), (mx - margin - 24, my - margin, "la"),
                     (mx + margin, my + 2, "la"), (mx - margin - 24, my + 2, "la"),
                     (mx - 12, my - margin - 26, "la"), (mx - 12, my + margin + 4, "la")]
            for (tx, ty, anc) in cands:
                bb = tuple(int(v) for v in d.textbbox((tx, ty), m["numeral"], font=f_sm, anchor=anc))
                if bb[0] < 2 or bb[1] < 2 or bb[2] > iw - 2 or bb[3] > ih - 2:
                    continue
                # 矩形と円の衝突（中心→矩形の最短距離で判定。角だけで見ると
                # 隣のマーカーが矩形の「中」に入るケースを見逃す＝実測で見逃した）
                def _hits(bx, cy2):
                    dx = max(bx[0] - cy2[0], 0, cy2[0] - bx[2])
                    dy = max(bx[1] - cy2[1], 0, cy2[1] - bx[3])
                    return dx * dx + dy * dy <= (r + 3) ** 2

                # 自分のマーカーにも載せない（候補の左側配置はグリフ右端が自分の円へ
                # 食い込む＝実測で 3 地点に文字が乗った）+ 他のマーカーにも載せない
                clash = _hits(bb, m["px"]) or any(_hits(bb, m2["px"])
                                                 for m2 in marks if m2 is not m)
                if clash or any(bb[0] <= b[2] and b[0] <= bb[2] and bb[1] <= b[3] and b[1] <= bb[3]
                                for b in placed_boxes):
                    continue
                d.text((tx, ty), m["numeral"], font=f_sm, fill=(255, 235, 140), anchor=anc)
                placed_boxes.append(bb)
                break
            else:
                skipped_numerals.append(m["label"])
    # 地点ごとの種別を使い、混在する指定では特定種別に誤分類しない。
    _KIND_LABEL = {"landing": ("着陸地点", "着陸"), "crater": ("クレーター", "形成"),
                   "impact": ("衝突地点", "衝突")}
    _kinds = {st.get("kind", "impact" if b == "jupiter" else "landing")
              for st in picked}
    _kind1 = (next(iter(_kinds)) if len(_kinds) == 1 else
              ("impact" if b == "jupiter" else ("mixed" if _kinds else "landing")))
    _mixed_labels = list(dict.fromkeys(_KIND_LABEL.get(k, _KIND_LABEL["landing"])[0]
                                       for k in (st.get("kind", "landing") for st in picked)))
    pt_label = (_KIND_LABEL.get(_kind1, ("地点", "イベント"))[0]
                if _kind1 != "mixed" else "地点（{}）".format("・".join(_mixed_labels)))
    def _terms(st):
        default_kind = "impact" if b == "jupiter" else "landing"
        return _KIND_LABEL.get(st.get("kind", default_kind), _KIND_LABEL["landing"])
    def _ev(st):
        """表示用の時刻。形成時期の範囲や時刻つきの衝突は event を使う"""
        return st.get("event") or (str(st["landed"]) + " UTC")
    def _lon_txt(lon):
        """経度の表示。公表値が 180° を超える場合は折り返した値（西経）も併記する"""
        v = float(lon)
        if v > 180.0:
            return "東経 {:.5f}°（＝西経 {:.5f}°）".format(v, 360.0 - v)
        if v < -180.0:
            return "西経 {:.5f}°（＝東経 {:.5f}°）".format(abs(v), 360.0 + v)
        return "{} {:.5f}°".format("東経" if v >= 0 else "西経", abs(v))
    def _sttxt(st):
        return st.get("flag") or st.get("status") or "データあり"
    if single:
        ns = "北緯" if site["lat"] >= 0 else "南緯"
        rows = [f"{site['ja']}（{site['place']}）の{pt_label}",
                f"座標: {ns} {abs(site['lat']):.5f}° / {_lon_txt(site['lon'])}　"
                f"{_terms(site)[1]} {_ev(site)}",
                f"{'米国旗' if site.get('flag') else '状態'}: {_sttxt(site)}",
                f"地図: {attrib}"]
    elif not picked:
        rows = ["{} の全球図（{}）".format(body_ja, attrib)]
    else:
        rows = ["{}の地点（{} 件・公表座標）".format(body_ja, len(picked))]
        for m, st in zip(marks, picked):
            ns = "N" if st["lat"] >= 0 else "S"; ew = "E" if st["lon"] >= 0 else "W"
            _merge = merged.get(m["label"]) or []
            rows.append("{}{} {}（{}） {:.3f}°{} {:.3f}°{} {}｜{}".format(
                m["numeral"], "＊" if _merge else "", st["ja"], st["place"], abs(st["lat"]), ns,
                abs(st["lon"]), ew, _ev(st), _sttxt(st))
                + ("　（この付近に{}点をまとめて表示）".format(len(_merge) + 1) if _merge else ""))
    # 凡例・タイトルは地図の**下の帯**に置く（地図に重ねると地点マーカーが文字で隠れる。
    # 実測: アポロ6地点の図で 1 地点が中心まで黒く潰れ、他の地点も文字が被った）
    img, map_h = add_legend_band(
        img, rows,
        font=fit_font_for_width(iw, rows[1:], [iw // 40, iw // 46, iw // 54, iw // 62, iw // 72]),
        title_font=fit_font_for_width(iw, rows[:1], [iw // 28, iw // 32, iw // 38]))
    d = ImageDraw.Draw(img)
    # 許容は画像の画素分解能に合わせる（全球図は 1px≒0.4° なので 0.01° 固定では必ず落ちる）
    tol_deg = max(0.01, 1.6 * float(sv.get("deg_per_px") or 0.0))
    if not marks:
        # 地点なし（sites="map"）: ベースマップが本当に描けているかだけを検査する
        _pv = img.convert("RGB").load()          # ベースマップが実際に描けているかの検査用
        uniq = len({_pv[i, j] for i in range(0, iw, max(1, iw // 60))
                    for j in range(0, map_h, max(1, map_h // 60))})
        grid = sum(1 for i in range(0, iw, 6) for j in range(0, map_h, 6)
                   if _pv[i, j] in ((70, 84, 110), (150, 170, 200)))
        # 画像なら色数（>50）、座標グリッドなら格子線の画素（>200）で「描けている」を判定する
        ok_bm = bool(uniq > 50) if sv.get("mode", "").startswith("image") else bool(grid > 200)
        mv = {"ok": ok_bm, "markers": [], "basemap_mode": sv.get("mode"),
              "basemap_ar_corrected": bool(sv.get("ar_corrected")),
              "basemap_source_ar": sv.get("source_ar"),
              "basemap_unique_colors_sampled": uniq, "basemap_grid_pixels_sampled": grid,
              "note": "地点指定なし（地図のみ）"}
    else:
        mv = verify_markers(img, marks, sv["inv"], tol_deg=tol_deg)
        mv["basemap_mode"] = sv.get("mode")
        mv["basemap_ar_corrected"] = bool(sv.get("ar_corrected"))
    mv["tol_deg"] = round(tol_deg, 4)
    mv["legend_band_below_map"] = True           # 文字は地図の外（下の帯）にある
    mv["markers_inside_map"] = bool(all(0 <= m["px"][0] < iw and 0 <= m["px"][1] < map_h
                                        for m in marks))
    if len(marks) > 1:
        sep = min(math.hypot(a["px"][0] - b["px"][0], a["px"][1] - b["px"][1])
                  for i, a in enumerate(marks) for b in marks[i + 1:])
        mv["min_marker_separation_px"] = round(sep, 1)
        # 半径の和より離れていること＝マーカー同士が重ならない
        mv["markers_do_not_overlap"] = bool(sep >= 2 * r)
        mv["ok"] = bool(mv["ok"] and mv["markers_do_not_overlap"])
    mv["numerals_placed_without_overlap"] = bool(not skipped_numerals)
    mv["merged_clusters"] = {k: v for k, v in merged.items()}
    if skipped_numerals:
        mv["numerals_skipped"] = skipped_numerals
    mv["map_height_px"] = int(map_h)
    mv["ok"] = bool(mv["ok"] and mv["markers_inside_map"])
    # 名前ラベルは実文字幅で配置し、マーカー非重複とキャンバス内を検証する。
    skipped_labels = []
    label_overlap = False
    labels_inside = True
    if single:
        x, y0 = marks[0]["px"]
        site_label = _terms(site)[0]
        label_text = f"{site['ja']} {site_label}"
        left, top, right, bottom = f_mid.getbbox(label_text)
        text_w, text_h = max(1, right - left), max(1, bottom - top)
        pad_x, pad_y = 8, 6
        box_w, box_h = text_w + 2 * pad_x, text_h + 2 * pad_y
        candidates = [(x + 3 * r, y0 - box_h // 2),
                      (x - 3 * r - box_w, y0 - box_h // 2)]
        chosen = None
        for lx, ly in candidates:
            rx, by = lx + box_w, ly + box_h
            if not (0 <= lx and rx < iw and 0 <= ly and by < map_h):
                continue
            near_x = min(max(x, lx), rx)
            near_y = min(max(y0, ly), by)
            if (near_x - x) ** 2 + (near_y - y0) ** 2 < r ** 2:
                continue
            chosen = (lx, ly, rx, by)
            break
        if chosen:
            lx, ly, rx, by = chosen
            d.rounded_rectangle([lx, ly, rx, by], radius=8, fill=(200, 0, 0, 235))
            d.text((lx + pad_x - left, ly + pad_y - top), label_text, font=f_mid,
                   fill=(255, 255, 255))
            label_overlap = ((min(max(x, lx), rx) - x) ** 2
                             + (min(max(y0, ly), by) - y0) ** 2 < r ** 2)
            labels_inside = bool(0 <= lx <= rx <= iw and 0 <= ly <= by <= map_h)
        else:
            skipped_labels.append(label_text)
        mv["labels_overlap_marker"] = label_overlap
        mv["labels_inside_canvas"] = labels_inside
        mv["label_bounds_px"] = list(chosen) if chosen else None
        mv["ok"] = bool(mv["ok"] and labels_inside and not label_overlap)
    jpeg = encode_jpeg(img)
    out_path = save_output(jpeg, "planetary_landing_sites", "jpg")
    notes = []
    _site_notes = [st["note"] for st in picked if st.get("note")]
    if b == "moon":
        notes += [
            "マーカーは **NASA NSSDC / LROC が公表した座標**（アポロは月着陸船(LM)の値。LRO 画像から"
            "決定・惑星中心 Mean Earth/Polar Axis 座標系。他は LROC 2016 座標表＝平均実測、不確かさ 0.3〜4 m）",
            "**月面の米国旗そのものの座標は公開表に無い**（機材の座標表に旗の行は無い）ため、"
            "アポロのマーカーが指すのは着陸地点。旗は着陸地点から数 m〜十数 m 離れた場所にあり、"
            "状態は NASA ALSJ と LROC の時系列画像（影が回るか）で判定されている",
        ]
    else:
        notes += [
            "マーカーは公表された地点座標そのもの（出典は下記）。座標の基準面・基準系は天体ごとに異なる",
        ]
    # 各地点固有の出典・解説は共通の座標系注記を残したうえで追加する。
    notes.extend(_site_notes)
    if skipped_labels:
        notes.append("画面内に配置できず省略した地点ラベル: " + "、".join(skipped_labels))
    if mode == "image":
        notes.append("ベースマップは**1枚の全球等角図**（{}）。**地形図ではなく概要画像**で、"
                     "おおよその位置把握用".format(attrib))
        if sv.get("ar_corrected"):
            notes.append("出典画像のアスペクト比は 2:1 ではないため **2:1 に補正**して描画している"
                         "（元 AR={}、{}×{} px）。この補正ぶんの位置ずれ（数%）は残る".format(
                             sv.get("source_ar"), (sv.get("pixels") or ["?", "?"])[0],
                             (sv.get("pixels") or ["?", "?"])[1]))
        if sv.get("lon_roll_deg"):
            notes.append("出典画像の左端は東経 0°（{}）で、この図の投影（左端 西経 180°）とは"
                         "180° ずれているため、**横に半周（{}°＝{} px 分）回して**描画している".format(
                             sv.get("left_edge_lon"), sv.get("lon_roll_deg"),
                             int(round(float(sv.get("lon_roll_deg") or 0.0) / 360.0
                                       * ((sv.get("pixels") or [0, 0])[0] or 0)))))
        if img_cfg and img_cfg.get("epoch"):
            notes.append("ベースマップは **{} の観測**の全球図（{}）で、**大気の模様はその時点の"
                         "もの**。地点マーカーの年代とは一致しない".format(
                             img_cfg["epoch"], attrib))
    if basemap_fallback:
        notes.append("全球画像を取得できなかったため、**緯度経度グリッドで代替**している（{}）".format(
            basemap_fallback))
    if not picked:
        notes.append("この図には**地点マーカーを描いていない**（sites=\"map\"＝地図のみ）")
    notes += [
        "等角図法のため高緯度ほど東西方向が圧縮されて見える。"
        "表示は{}。{}".format(
            '局所 {:.0f}°'.format(span_deg) if single else "天体全面",
            {"trek": "地形は地図タイル（{}）に依存する".format(attrib),
             "image": "地形は1枚の全球画像（{}）に依存する".format(attrib)}.get(
                mode, "**地形画像は無い**（全球等角図が公開されていない天体）。"
                      "緯度経度グリッドと座標だけの模式図")),
    ]
    if _BODY_MAP_NOTES.get(b):
        notes.append(_BODY_MAP_NOTES[b])
    for m, st in zip(marks, picked):
        _label, _event_word = _terms(st)
        notes.append("{} {}（{}）: 緯度 {:.5f}° / 経度 {}・{} {}・{}".format(
            m["numeral"], st["ja"], st["place"], st["lat"], _lon_txt(st["lon"]), _event_word, _ev(st),
            _sttxt(st)))
    for _host, _members in merged.items():
        notes.append("{} の位置に**まとめて1点で表示**した地点（重なるため。座標は "
                     "structuredContent に記載）: {}".format(_host, "、".join(_members)))
    if len(picked) > 1 and out_px != requested_out_px:
        notes.append("地点マーカーが重ならないよう、出力を {}px から {}px に拡大している"
                     "（間隔 {}px・半径 {}px）".format(requested_out_px, out_px, round(_min_sep(out_px), 1), r))
    if skipped_numerals:
        notes.append("図中に番号を置けなかった地点（凡例の番号で参照）: " + "、".join(skipped_numerals))
    _wrapped = [st for st in picked if float(st["lon"]) > 180.0]
    if _wrapped:
        notes.append("経度は東向き正の値で公表されている（{}）。図の投影は -180〜180° なので"
                     "折り返して描いており、同一の地点を指す".format(
                         "、".join("{} {:.4f}°E＝西経 {:.4f}°".format(
                             st["ja"], float(st["lon"]), 360.0 - float(st["lon"]))
                             for st in _wrapped)))
    _sources = []
    _has_crater_source = b == "moon" and any(
        st.get("kind") in {"crater", "impact"} for st in picked)
    if not _has_crater_source or "landing" in _kinds:
        _sources.append(_SITES_SOURCE)
    if _has_crater_source:
        _sources.append(_SITES_SOURCE_CRATER)
    _src = "／".join(_sources) if _sources else _SITES_SOURCE
    notes.append(_src)
    title = (f"{site['ja']}（{site['place']}）の{pt_label}（{attrib}）" if single else
             (f"{body_ja}の{pt_label} {len(picked)} 地点（{attrib}）" if picked else
              f"{body_ja}の全球図（{attrib}）"))
    fig = figure_payload(
        kind=(("body_map" if not picked else
               ("landing_site_map" if _kinds == {"landing"} else
                ("impact_site_map" if not (_kinds & {"landing"}) else "site_map")))), title=title,
        view=view_spec("body_surface", "equirectangular",
                       f"{body_ja}面の緯度経度図（等角図法）。赤●＝{pt_label}",
                       why="地点は天体面の座標なので、天体面の地図に置くのが最も誤読が少ない"),
        primary=primary_spec(body_ja, "surface_map",
                             note="天体面の地図なので主天体は図の中心に置いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=[f"{pt_label}マーカー（実寸ではない）"]),
        markers=marks, notes=figure_notes(extra=list(notes) + [tile_failure_note(sv)]),
        caption="{}。マーカーは公表された地点座標に置いたもの{}。".format(
            title + "。" + "、".join("{} {}°{} {}°{}".format(
                m["numeral"], abs(st["lat"]), "N" if st["lat"] >= 0 else "S",
                abs(m["lon"]), "E" if m["lon"] >= 0 else "W") for m, st in zip(marks, picked))
            , ("（旗の位置は公開表に無い）"
               if (b == "moon" and _kind1 == "landing") else "")),
        verify=mv)
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=title)
    lines = [media_link_line("{}の{}マップ".format(body_ja, pt_label),
                             path=out_path, kind="figure"),
        "🌕 **{}**".format(title)]
    for m, st in zip(marks, picked):
        ns = "北緯" if st["lat"] >= 0 else "南緯"
        _label, _event_word = _terms(st)
        lines.append("{} {}（{}）: {} {:.5f}° / {}・{} {} ・ {}".format(
            m["numeral"] if not single else "📍", st["ja"], st["place"], ns, abs(st["lat"]),
            _lon_txt(st["lon"]), _event_word, _ev(st), _sttxt(st)))
    lines += ["", figure_text_block(fig), "出典: " + _src]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "body": body_ja, "sites": [dict(m, place=st["place"], landed=st["landed"],
                                                event=_ev(st), kind=st.get(
                                                    "kind", "impact" if b == "jupiter" else "landing"),
                                                status=_sttxt(st), en=st["en"])
                                              for m, st in zip(marks, picked)],
            "count": len(picked), "span_deg": span_deg, "zoom": sv["zoom"],
            "figure": fig, "image_path": out_path, "source": _src,
        })


def planetary_orbiter_track(body: str = "moon", orbiter: str = "lro",
                            when: Optional[str] = None,
                            minutes: int = 90, step: int = 5,
                            zoom: Optional[int] = None, span_deg: float = 120.0,
                            out_px: int = 900,
                            sites: Optional[str] = None) -> CallToolResult:
    '''任意の天体（月・火星・水星・タイタン等）を周回する探査機の現在位置と軌道を、その天体の地図にプロットした画像を返す。

    例:「LROの現在位置を月面地図で」「MROの火星での現在地」「火星周回機の位置」
    「かぐやの月面落下地点は？」（過去機は運用終了を案内し、落点が公表されていれば
    その地点を月面図にマーカーで示した図を返す）
    「アポロの着陸地点を月面図で」「アポロ11号の着陸地点は？」（sites 指定＝地点マーカー図）
    「月に出来た一番新しいクレーターの位置は？」（sites="newcrater"＝新クレーター2地点）
    JPL Horizons が返す中心天体の状態ベクトルを IAU 自転モデルで天体固定座標
    (緯度経度・高度) に変換し、NASA Trek の等角図法地図に重ねて描画。認証不要。

    全面表示にするには span_deg=360（既定 120）。zoom 省略時は全面(1)に自動設定。

    Args:
        body: 天体名（moon, mars, mercury, venus, titan, vesta, ceres, io, europa,
            ganymede, callisto, jupiter, saturn, uranus, neptune, pluto, bennu, ryugu）。
            地点が登録されていない天体（ガス惑星・小天体）は sites="map" で全球図を返す。
        orbiter: 周回機名（月: lro, gateway／火星: mro, odyssey）または
            JPL Horizons の負の天体ID（例 "-74"）。和名（"かぐや"/"あかつき" 等）も可。
            過去機（kaguya 等）は運用終了のため現在位置は返さず、**落点が公表されている
            機体（かぐや）は落点の緯度経度・時刻・出典を structuredContent.impact_site に返す**。
        when: 観測時刻 ISO8601（例 "2026-09-10T00:00:00Z"）。省略で現在。
        minutes: 現在位置の前後何分の軌道を表示するか（既定 90, 最大 1440）。
        step: トレイルの時間刻み（分。既定 5, 最小 1）。
        zoom: 地図ズーム 1〜最大（既定: span_deg>=360 なら1=全面, それ未満は3）。
        span_deg: 表示する経度幅（度。360=天体全面, 既定 120, 最大 360）。
        out_px: 出力画像の長辺ピクセル（既定 900, 最大 1600）。
        sites: **地点マーカー図**にする場合の地点指定（例 "apollo"=アポロ6地点すべて、
            "apollo11"="apollo17"=個別、"map"=地図のみ、"newcrater"=月に新しくできた
            クレーター2点）。指定すると周回機の計算を行わず、公表座標にマーカーを置いた図を
            返す（1地点なら局所図、複数なら天体全面図＋凡例）。旗の位置は公開表に無いため、
            注記でその旨を明示する。sites="newcrater" は **マクゲッチン**（2024-04-11〜
            05-22 に自然衝突で形成。直径 222 m・1.3536°N 67.1765°E）と **ファルコン9上段の
            衝突痕**（2026-08-05 06:35 UTC。直径 18 m・19.4759°N 266.7138°E）を返す。
            記録上いちばん新しいのは人工物の衝突（ファルコン9上段, 2026-08-05）、
            **自然衝突ではマクゲッチンが最新**（この規模は月で平均 132 年に1回）。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は %LOCALAPPDATA%\\Temp\\space_finder_mcp\\out に
    保存し、同じパスを structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    '''
    from PIL import ImageDraw          # 画像の生成は surface_map 側で行う

    b = str(body).strip().lower()
    if sites and str(sites).strip():
        # 地点マーカー図（着陸地点・衝突地点など）。周回機の計算は行わない。
        # 全球画像を持たない天体（木星など）でも描けるよう MAP_BODIES で解決する。
        return _sites_result(b, str(sites).strip(), as_int(out_px, 900, 400, 1600))

    if b not in BODIES:
        return CallToolResult(
            content=[TextContent(type="text", text=f"不明な天体 '{body}'。対応: {', '.join(sorted(BODIES.keys()))}（地点マーカー図は sites= で MAP_BODIES の天体に対応）")],
            structuredContent={"error": "unknown body", "body": body, "known": sorted(BODIES.keys())},
        )
    body_cfg = BODIES[b]

    ob = str(orbiter).strip().lower()
    # 和名・表記ゆれを展開してから判定する（「かぐや」→ kaguya、「あかつき」→ akatsuki）。
    # 英語キーだけで判定すると、和名で『不明な周回機』になり運用終了の案内に届かない
    # （実測: orbiter="かぐや" は unknown、orbiter="kaguya" は運用終了の案内）。
    cands = []
    try:
        from .name_common import expand_terms, name_variants
        for t in list(expand_terms(orbiter)) + list(name_variants(orbiter)):
            tl = t.strip().lower()
            if tl and tl not in cands:
                cands.append(tl)
    except Exception:      # 名前解決の失敗でツールを落とさない（英名キーだけで判定を続ける）
        pass
    if ob not in cands:
        cands.insert(0, ob)

    def _pick(table):
        return next((c for c in cands if c in table), None)

    guard = _pick(_UNKNOWN_GUARD)
    if guard:
        return CallToolResult(
            content=[TextContent(type="text", text=_UNKNOWN_GUARD[guard])],
            structuredContent={"error": "past/inactive mission", "orbiter": guard,
                               "input": orbiter},
        )
    past = _pick(PAST)
    if past:
        info = PAST[past]
        imp = info.get("impact")
        lines = [f"{info['ja']} は {info['note']}。現在の位置を表示できません。"]
        if imp:
            ns = "南緯" if imp["lat"] < 0 else "北緯"
            ew = "西経" if imp["lon"] < 0 else "東経"
            lines += [
                "📍 落下地点（判明・JAXA 公表）: {} {:.1f}° / {} {:.1f}°（{}）".format(
                    ns, abs(imp["lat"]), ew, abs(imp["lon"]), imp["place"]),
                "🕒 落下時刻: {}（{}）".format(imp["when_utc"], imp["when_jst"]),
                "🌑 " + imp["note"],
                "出典: " + imp["source"],
            ]
        if imp:
            # 落点が公表されている機体は、天体面地図に地点マーカーを描いて返す
            mapped = _impact_site_result(body_cfg, info)
            if mapped is not None:
                return mapped
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structuredContent={"error": "past mission", "orbiter": past, "note": info["note"],
                               "impact_site": imp, "input": orbiter},
        )
    live = _pick(ORBITERS)
    if live:
        ob = live
    if ob in ORBITERS:
        oinfo = ORBITERS[ob]
        if oinfo["body"] != b:
            return CallToolResult(
                content=[TextContent(type="text", text=f"'{ob}'（{oinfo['ja']}）は {BODIES[oinfo['body']]['ja']} の周回機です。天体 '{body}' と一致しません。")],
                structuredContent={"error": "body mismatch", "orbiter": ob, "body": body},
            )
        cmd = oinfo["id"]; ja = oinfo["ja"]; en = oinfo["en"]
    elif ob.startswith("-") or ob.isdigit():
        cmd = ob; ja = _body_name(cmd); en = ""
    else:
        return CallToolResult(
            content=[TextContent(type="text", text="不明な周回機 '{}'。天体 {} の対応: {}（過去機: {}）"
                                 "または JPL Horizons の負の天体ID".format(
                                     orbiter, body_cfg["ja"],
                                     ", ".join(sorted([k for k, v in ORBITERS.items() if v["body"] == b])),
                                     ", ".join(sorted([k for k, v in PAST.items() if v["body"] == b]))))],
            structuredContent={"error": "unknown orbiter", "orbiter": orbiter, "body": body,
                               "known": sorted([k for k, v in ORBITERS.items() if v["body"] == b]),
                               "past": sorted([k for k, v in PAST.items() if v["body"] == b])},
        )

    minutes = as_int(minutes, 90, 10, 1440)
    step = as_float(step, 5, 1.0 / 60.0, 30.0)
    span_deg = as_float(span_deg, 120.0, 10.0, 360.0)
    whole = span_deg >= 360.0
    zoom = as_int(zoom, None, 1, body_cfg["maxzoom"])
    if zoom is None:
        zoom = 1 if whole else 3
    out_px = as_int(out_px, 900, 400, 1600)

    if when:
        iso = str(when).strip().replace("Z", "+00:00")
        try:
            t0 = datetime.datetime.fromisoformat(iso)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return CallToolResult(
                content=[TextContent(type="text", text="when は ISO8601（YYYY-MM-DDTHH:MM[:SS]Z）で指定してください。")],
                structuredContent={"error": "bad time", "when": when},
            )
    else:
        t0 = datetime.datetime.now(datetime.timezone.utc)
    jd_now = 2451545.0 + (t0 - datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)).total_seconds() / 86400.0
    tstr = t0.strftime("%Y-%m-%d %H:%M UTC")

    try:
        cur = _state(body_cfg, cmd, jd_now)
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"JPL Horizons からの状態ベクトル取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "ssd.jpl.nasa.gov"},
        )
    except ValueError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"状態ベクトルの解析に失敗: {e}")],
            structuredContent={"error": str(e)},
        )
    lat0, lon0, alt0 = cur["lat"], cur["lon"], cur["alt_km"]
    speed_kms = cur["speed_kms"]

    # トレイル点（現在位置を含む）を1リクエストの TLIST バッチで取得（高精度・高速）
    n = max(3, int(minutes / step))
    jds = [jd_now + i * step / 1440.0 for i in range(-n, n + 1)]
    trail = []
    trail_errors = []
    try:
        vecs = _horizons_states(cmd, body_cfg["center"], jds)
        for jd, (x, y, z, vx, vy, vz) in zip(jds, vecs):
            st = _vec_to_state(body_cfg, x, y, z, vx, vy, vz, jd)
            trail.append((st["lon"], st["lat"]))
    except Exception:
        # バッチ失敗時は逐次フォールバック
        for i in range(-n, n + 1):
            try:
                st = _state(body_cfg, cmd, jd_now + i * step / 1440.0)
                trail.append((st["lon"], st["lat"]))
            except Exception as ex:
                trail_errors.append({"offset_min": i * step, "error": str(ex)[:120]})

    try:
        # タイル取得・切り出し・投影は共通ルーチン（surface_map）。投影と検証が
        # 2か所にあると必ず食い違うため、地図の上に描く処理はここに集約する。
        sv = surface_view(body_cfg, lon0, lat0, span_deg, zoom, out_px,
                          whole_dim=0.75, regional_dim=0.7)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{body_cfg['ja']}地図タイルの取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "trek.nasa.gov"},
        )
    img = sv["img"]
    g2px = sv["g2px"]

    d = ImageDraw.Draw(img)

    def _drawseg(seg):
        if len(seg) >= 2:
            d.line([g2px(a, b) for a, b in seg], fill=(255, 140, 0),
                   width=max(3, img.size[0] // 300), joint="curve")

    for seg in split_at_antimeridian(trail):
        _drawseg(seg)

    cx, cy = g2px(lon0, lat0)
    r = max(9, img.size[0] // 70)
    draw_marker(d, (cx, cy), r, fill=(255, 60, 30),
                outline_w=max(4, img.size[0] // 200), inner_ratio=0.5)

    iw, ih = img.size
    # 旧 _font は常にメイリオ Bold 優先だったため bold=True で等価
    f_big = load_font(max(16, iw // 34), bold=True)
    f_mid = load_font(max(13, iw // 46), bold=True)
    f_sm = load_font(max(11, iw // 54), bold=True)
    ns = "北緯" if lat0 >= 0 else "南緯"; ew = "東経" if lon0 >= 0 else "西経"
    spd = f"{speed_kms:.2f} km/s" if speed_kms else "?"
    # 各行はフォントの実高さ + 余白で累積配置（高さ基準だと全面表示(縦が半分)で行が重なる）
    pad = 8
    def _fh(font, text):
        bb = d.textbbox((0, 0), text, font=font)
        return bb[3] - bb[1]
    line1 = f"🛰 {ja}（{en}）"
    line2 = f"{body_cfg['ja']}面: {ns} {abs(lat0):.2f}° / {ew} {abs(lon0):.2f}°  高度 {alt0:.1f} km"
    line3 = f"速度 {spd} ・ 時刻 {tstr} UTC"
    # 情報パネルは現在位置マーカーを隠さない位置へ（隠れると画素検査が落ちる）
    panel_w = int(iw * 0.72)
    panel_h = (_fh(f_big, line1) + pad) + (_fh(f_mid, line2) + pad) + _fh(f_sm, line3) + 28
    px0, py0, panel_hidden = panel_placement(iw, ih, panel_w, panel_h,
                                             avoid=[(cx, cy)], margin=12, gap=r + 6)
    y1 = py0 + 8
    y2 = y1 + _fh(f_big, line1) + pad
    y3 = y2 + _fh(f_mid, line2) + pad
    panel_bottom = y3 + _fh(f_sm, line3) + 16
    d.rounded_rectangle([px0, py0, px0 + panel_w, panel_bottom], radius=12, fill=(0, 0, 0, 210))
    d.text((px0 + 12, y1), line1, font=f_big, fill=(255, 255, 255))
    d.text((px0 + 12, y2), line2, font=f_mid, fill=(225, 228, 248))
    d.text((px0 + 12, y3), line3, font=f_sm, fill=(200, 210, 240))
    if panel_hidden:
        # どの隅でも重なる小さな図では、隠れるマーカーをパネルの上に描き直す
        draw_marker(d, (cx, cy), r, fill=(255, 60, 30),
                    outline_w=max(4, img.size[0] // 200), inner_ratio=0.5)
    lab_w = int(iw * 0.32); lab_h = ih // 16
    margin = r * 5
    if cx + margin + lab_w < iw - 10:
        lx, ly = cx + margin, cy - lab_h // 2
    elif cx - margin - lab_w > 10:
        lx, ly = cx - margin - lab_w, cy - lab_h // 2
    else:
        lx, ly = cx - lab_w // 2, cy + margin
    ly = max(10, min(ly, ih - lab_h - 10))
    d.rounded_rectangle([lx, ly, lx + lab_w, ly + lab_h], radius=8, fill=(200, 0, 0, 235))
    d.text((lx + 8, ly + 5), f"{ja} 現在位置", font=f_mid, fill=(255, 255, 255))

    jpeg = encode_jpeg(img)   # 3.5MB 超は縮小して再エンコード（img_common）
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{ja} の{body_cfg['ja']}面位置")
    # インライン画像を描けないハーネス向け: 保存してリンクを先頭に出す
    out_path = save_output(jpeg, "planetary_orbiter_track", "jpg")

    view = "全面" if whole else f"局所 {span_deg:.0f}°"
    step_disp = step if step >= 1 else f"{step * 60:.0f} 秒"
    # 図の注記（figure/1）: 図の誤読を防ぐための自己申告。content にも同じ注記を出す。
    marker_visible = not (lx <= cx <= lx + lab_w and ly <= cy <= ly + lab_h)
    # 投影の逆変換（描いた画素→緯度経度）と、その画素にマーカー色が乗っていることまで検査
    mv = verify_marker(img, (cx, cy), lon0, lat0, sv["inv"],
                       r=marker_scan_radius(r))
    mv["marker_visible"] = bool(marker_visible)
    mv["panel_overlaps_marker"] = bool(panel_hidden)
    mv["ok"] = bool(marker_visible and mv["ok"])
    fig = figure_payload(
        kind="ground_track_map",
        title=f"{ja} の{body_cfg['ja']}面位置（{body_cfg['attrib']}）",
        view=view_spec("body_surface", "equirectangular",
                       f"{body_cfg['ja']}面の緯度経度図（等角図法）。オレンジ線＝真下の点の軌跡",
                       why="3Dの軌道ではなく天体面への投影。軌道面や傾斜はこの図からは"
                           "読み取れない（表示範囲は地図ズームに依存）"),
        primary=primary_spec(body_cfg["ja"], "surface_map",
                             note="天体面の地図なので主天体は図の中心に置いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["現在位置マーカー（実寸ではない）"]),
        markers=[{"id": "current", "label": f"{ja} 現在位置", "lat": round(lat0, 4),
                  "lon": round(lon0, 4), "altitude_km": round(alt0, 2),
                  "px": [round(cx), round(cy)]}],
        notes=figure_notes(extra=[
            f"オレンジ線は指定時刻の前後 {minutes} 分の{body_cfg['ja']}面軌道＝真下の点の軌跡。"
            "3Dの軌道の形ではない",
            "等角図法のため高緯度ほど東西方向が圧縮されて見える",
            f"表示は「{view}」（span_deg={span_deg:g}）。地図は NASA Trek の{body_cfg['attrib']}",
            f"マーカーは指定時刻の真下の点。高度 {alt0:.1f} km・{body_cfg['ja']}中心距離 "
            f"{cur['dist_km']:.1f} km・速度 {spd}",
            "過去の探査機（かぐや等）は運用終了済みで、位置は軌道要素からの外挿になる場合がある",
            (f"トレイル {len(trail_errors)} 点の計算に失敗しました。該当部分は表示されていません。"
             if trail_errors else ""),
            ("" if (px0, py0) == (12, 12) and not panel_hidden else
             "情報パネルは現在位置マーカー（cx={:.0f}, cy={:.0f}px）と重ならない隅に配置した".format(cx, cy)),
            ("" if not panel_hidden else
             "図が小さくパネルと重なるため、現在位置マーカーはパネルの上に描き直した"),
            tile_failure_note(sv),
        ]),
        caption=f"{ja} の{body_cfg['ja']}面位置（{tstr}）。真下の点は {ns} {abs(lat0):.2f}° / "
                f"{ew} {abs(lon0):.2f}°、高度 {alt0:.1f} km。",
        verify=mv,
    )
    lines = [
        media_link_line(f"{ja} の{body_cfg['ja']}面軌道マップ",
                        path=out_path, kind="figure"),
        f"🛰 **{ja}** の{body_cfg['ja']}面位置（{tstr}）:",
        f"📍 {body_cfg['ja']}面: {ns} {abs(lat0):.2f}° / {ew} {abs(lon0):.2f}°",
        f"🛰 高度 {alt0:.1f} km ・ {body_cfg['ja']}中心距離 {cur['dist_km']:.1f} km ・ 速度 {spd}",
        f"🛤 軌道トレイル: 前後 {minutes} 分（{step_disp} 刻み）。オレンジ線=軌道。表示: {view}",
        (f"⚠️ トレイル {len(trail_errors)} 点の計算に失敗しました。該当部分は表示されていません。"
         if trail_errors else ""),
        "",
        figure_text_block(fig),
        f"出典: JPL Horizons + IAU{body_cfg['ja']}自転モデル ／ 地図 {body_cfg['attrib']}",
    ]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), imgc],
        structuredContent={
            "body": b, "spacecraft": ob, "name_ja": ja, "name_en": en, "horizons_id": cmd,
            "time_utc": tstr,
            "latitude": round(lat0, 4), "longitude": round(lon0, 4),
            "altitude_km": round(alt0, 2), "speed_kms": round(speed_kms, 4) if speed_kms else None,
            "dist_from_body_center_km": round(cur["dist_km"], 1),
            "trail_minutes": minutes, "step_min": round(step, 4), "trail_points": len(trail),
            "trail_errors": trail_errors,
            "view": view, "span_deg": span_deg, "zoom": zoom,
            "figure": fig, "image_path": out_path,
            "source": f"JPL Horizons + IAU rotation + NASA Trek {body_cfg['attrib']}",
        },
    )

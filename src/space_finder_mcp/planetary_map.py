from __future__ import annotations

import base64
import concurrent.futures
import datetime
import io
import math
import re
from typing import Optional

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

UA = {"User-Agent": "space-finder-mcp/0.25 (MCP; planetary orbiter track)"}

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
PAST = {
    "kaguya": {"body": "moon", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)",
               "note": "2009年に月面へ制御落下（運用終了）"},
    "selene": {"body": "moon", "ja": "かぐや（SELENE）", "en": "Kaguya (SELENE)",
               "note": "2009年に月面へ制御落下（運用終了）"},
}
_UNKNOWN_GUARD = {
    "maven": "MAVEN（火星大気探査機）は2026年3月に運用終了し、JPL Horizons のエフェメリスがありません。",
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
                          headers=UA, timeout=60)
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
                     headers=UA, timeout=40)
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
                         headers=UA, timeout=30)
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


def _fetch_tiles(body: dict, center_lon, center_lat, span_deg, zoom, max_workers=16):
    from PIL import Image
    zoom = min(zoom, body["maxzoom"])
    cols = 2 ** (zoom + 1); rows = 2 ** zoom
    W = cols * 256; H = rows * 256
    xc = (center_lon + 180) / 360.0 * W
    yc = (90 - center_lat) / 180.0 * H
    span_px = span_deg / (360.0 / W)
    cc0 = int(xc // 256); rr0 = int(yc // 256)
    half = int(math.ceil((span_px / 2) / 256.0)) + 1
    c_lo = max(0, cc0 - half); r_lo = max(0, rr0 - half)
    c_hi = min(cols - 1, cc0 + half); r_hi = min(rows - 1, rr0 + half)
    tiles = [(rr, cc) for rr in range(r_lo, r_hi + 1) for cc in range(c_lo, c_hi + 1)]
    url = body["tile"]

    def _one(tc):
        rr, cc = tc
        try:
            r = requests.get(url.format(z=zoom, row=rr, col=cc), headers=UA, timeout=20)
            return rr, cc, (r.content if r.status_code == 200 else None)
        except requests.RequestException:
            return rr, cc, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, tiles))
    cw = c_hi - c_lo + 1; rh = r_hi - r_lo + 1
    mosaic = Image.new("RGB", (cw * 256, rh * 256), (30, 30, 35))
    for rr, cc, data in results:
        if data:
            try:
                from PIL import Image as PImage
                mosaic.paste(PImage.open(io.BytesIO(data)).convert("RGB"),
                             ((cc - c_lo) * 256, (rr - r_lo) * 256))
            except Exception:
                pass
    center_px = (xc - c_lo * 256, yc - r_lo * 256)
    return mosaic, center_px, (c_lo * 256, r_lo * 256), cols, rows


def _font(sz: int):
    from PIL import ImageFont
    for p in ("C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/meiryo.ttc",
              "C:/Windows/Fonts/msgothic.ttc"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def planetary_orbiter_track(body: str = "moon", orbiter: str = "lro",
                            when: Optional[str] = None,
                            minutes: int = 90, step: int = 5,
                            zoom: Optional[int] = None, span_deg: float = 120.0,
                            out_px: int = 900) -> CallToolResult:
    '''任意の天体（月・火星・水星・タイタン等）を周回する探査機の現在位置と軌道を、その天体の地図にプロットした画像を返す。

    例:「LROの現在位置を月面地図で」「MROの火星での現在地」「火星周回機の位置」
    JPL Horizons が返す中心天体の状態ベクトルを IAU 自転モデルで天体固定座標
    (緯度経度・高度) に変換し、NASA Trek の等角図法地図に重ねて描画。認証不要。

    全面表示にするには span_deg=360（既定 120）。zoom 省略時は全面(1)に自動設定。

    Args:
        body: 天体名（moon, mars, mercury, titan, vesta, ceres）。
        orbiter: 周回機名（月: lro, gateway／火星: mro, odyssey）または
            JPL Horizons の負の天体ID（例 "-74"）。過去機（kaguya 等）は運用終了。
        when: 観測時刻 ISO8601（例 "2026-09-10T00:00:00Z"）。省略で現在。
        minutes: 現在位置の前後何分の軌道を表示するか（既定 90, 最大 1440）。
        step: トレイルの時間刻み（分。既定 5, 最小 1）。
        zoom: 地図ズーム 1〜最大（既定: span_deg>=360 なら1=全面, それ未満は3）。
        span_deg: 表示する経度幅（度。360=天体全面, 既定 120, 最大 360）。
        out_px: 出力画像の長辺ピクセル（既定 900, 最大 1600）。
    '''
    from PIL import Image, ImageDraw, ImageFont

    b = str(body).strip().lower()
    if b not in BODIES:
        return CallToolResult(
            content=[TextContent(type="text", text=f"不明な天体 '{body}'。対応: {', '.join(sorted(BODIES.keys()))}")],
            structuredContent={"error": "unknown body", "body": body,
                               "known": sorted(BODIES.keys())},
        )
    body_cfg = BODIES[b]

    ob = str(orbiter).strip().lower()
    if ob in _UNKNOWN_GUARD:
        return CallToolResult(
            content=[TextContent(type="text", text=_UNKNOWN_GUARD[ob])],
            structuredContent={"error": "past/inactive mission", "orbiter": ob},
        )
    if ob in PAST:
        info = PAST[ob]
        return CallToolResult(
            content=[TextContent(type="text", text=f"{info['ja']} は {info['note']}。現在の位置を表示できません。")],
            structuredContent={"error": "past mission", "orbiter": ob, "note": info["note"]},
        )
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
            content=[TextContent(type="text", text=f"不明な周回機 '{orbiter}'。天体 {body_cfg['ja']} の対応: {', '.join(sorted([k for k, v in ORBITERS.items() if v['body'] == b]))}（または JPL Horizons の負の天体ID）")],
            structuredContent={"error": "unknown orbiter", "orbiter": orbiter, "body": body,
                               "known": sorted([k for k, v in ORBITERS.items() if v["body"] == b])},
        )

    minutes = max(10, min(int(minutes), 1440))
    step = max(1.0/60.0, min(float(step), 30.0))
    span_deg = max(10.0, min(float(span_deg), 360.0))
    whole = span_deg >= 360.0
    if zoom is None:
        zoom = 1 if whole else 3
    zoom = max(1, min(int(zoom), body_cfg["maxzoom"]))
    out_px = max(400, min(int(out_px), 1600))

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
            except Exception:
                continue

    try:
        mosaic, center_px, tl, cols, rows = _fetch_tiles(
            body_cfg, lon0, lat0, span_deg, zoom)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{body_cfg['ja']}地図タイルの取得に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "trek.nasa.gov"},
        )
    W = cols * 256; H = rows * 256

    if whole:
        img = mosaic.resize((out_px, int(out_px * H / W)), Image.LANCZOS)
        img = img.point(lambda p: int(p * 0.75))
        scale_x = out_px / float(W); scale_y = img.size[1] / float(H)

        def g2px(lon, lat):
            gx = (lon + 180) / 360.0 * W; gy = (90 - lat) / 180.0 * H
            return (gx * scale_x, gy * scale_y)
    else:
        gx_rover = tl[0] + center_px[0]; gy_rover = tl[1] + center_px[1]
        span_px = span_deg / (360.0 / W)
        half = span_px / 2.0
        left = int(gx_rover - half); top = int(gy_rover - half)
        side = int(2 * half)
        right = left + side; bottom = top + side
        if left < 0: left, right = 0, side
        if top < 0: top, bottom = 0, side
        if right > W: right, left = W, W - side
        if bottom > H: bottom, top = H, H - side
        m_l = max(0, left - tl[0]); m_t = max(0, top - tl[1])
        m_r = min(mosaic.size[0], m_l + side); m_b = min(mosaic.size[1], m_t + side)
        crop = mosaic.crop((m_l, m_t, m_r, m_b))
        img = crop.resize((out_px, out_px), Image.LANCZOS)
        img = img.point(lambda p: int(p * 0.7))
        scale = out_px / float(side)

        def g2px(lon, lat):
            gx = (lon + 180) / 360.0 * W; gy = (90 - lat) / 180.0 * H
            return (gx - left) * scale, (gy - top) * scale

    d = ImageDraw.Draw(img)

    def _drawseg(seg):
        if len(seg) >= 2:
            d.line([g2px(a, b) for a, b in seg], fill=(255, 140, 0),
                   width=max(3, img.size[0] // 300), joint="curve")

    if trail:
        seg = [trail[0]]
        for i in range(1, len(trail)):
            if abs(trail[i][0] - trail[i - 1][0]) > 150:
                _drawseg(seg); seg = [trail[i]]
            else:
                seg.append(trail[i])
        _drawseg(seg)

    cx, cy = g2px(lon0, lat0)
    r = max(9, img.size[0] // 70)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 60, 30),
              outline=(255, 255, 255), width=max(4, img.size[0] // 200))
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=(255, 255, 255))

    iw, ih = img.size
    f_big = _font(max(16, iw // 34)); f_mid = _font(max(13, iw // 46)); f_sm = _font(max(11, iw // 54))
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
    y1 = 20
    y2 = y1 + _fh(f_big, line1) + pad
    y3 = y2 + _fh(f_mid, line2) + pad
    panel_bottom = y3 + _fh(f_sm, line3) + 16
    d.rounded_rectangle([12, 12, int(iw * 0.72), panel_bottom], radius=12, fill=(0, 0, 0, 210))
    d.text((24, y1), line1, font=f_big, fill=(255, 255, 255))
    d.text((24, y2), line2, font=f_mid, fill=(225, 228, 248))
    d.text((24, y3), line3, font=f_sm, fill=(200, 210, 240))
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

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    jpeg = buf.getvalue()
    if len(jpeg) > 3_500_000:
        img = img.resize((int(iw * 0.7), int(ih * 0.7)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        jpeg = buf.getvalue()
    imgc = ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"),
                        mimeType="image/jpeg", altText=f"{ja} の{body_cfg['ja']}面位置")

    view = "全面" if whole else f"局所 {span_deg:.0f}°"
    step_disp = step if step >= 1 else f"{step * 60:.0f} 秒"
    lines = [
        f"🛰 **{ja}** の{body_cfg['ja']}面位置（{tstr}）:",
        f"📍 {body_cfg['ja']}面: {ns} {abs(lat0):.2f}° / {ew} {abs(lon0):.2f}°",
        f"🛰 高度 {alt0:.1f} km ・ {body_cfg['ja']}中心距離 {cur['dist_km']:.1f} km ・ 速度 {spd}",
        f"🛤 軌道トレイル: 前後 {minutes} 分（{step_disp} 刻み）。オレンジ線=軌道。表示: {view}",
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
            "view": view, "span_deg": span_deg, "zoom": zoom,
            "source": f"JPL Horizons + IAU rotation + NASA Trek {body_cfg['attrib']}",
        },
    )

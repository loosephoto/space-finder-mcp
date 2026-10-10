"""地上から見上げた星空のパノラマ（正距円筒・方位-高度）。

従来の `sky_map_with_satellites` は天頂中心の円形図（魚眼）で、恒星は16個の
固定リストだった。このモジュールはそれを「地上から見上げた空」に置き換える:

- 投影: 地平線が下・方位が横軸の正距円筒パノラマ（`span` で 30〜360° を切り出す）
- 恒星: Hipparcos 実星表（Vmag<8.5, 約6.1万星）を Skyfield で alt/az に。
        等級・色（B-V→色温度）・大気減光（Kasten-Young）を反映
- 時間帯: 太陽高度で空の色・限界等級・天の川の見え方が変わる（昼/朝焼け/夕方/薄明/夜）
- 天の川: 各画素の銀経・銀緯から面輝度を近似（暗黒帯・大裂溝・塊状構造つき）。
          昼・薄明は銀河面を破線で示す
- 月: 位相（照度）と距離を反映し、明るい側が太陽の方向を向く
- 見えていない天体: 惑星・主要恒星・銀河は○の輪郭で「位置」を示す（昼でも分かる）
- 下部: 手続き生成の街並み（建物が低空の天体を隠す）＋方位目盛り（左右端に方位）
- 人工衛星: CelesTrak の TLE を SGP4 で伝播（低軌道は前後の軌道予測を破線で描く）

暦は既存ツールと同じ JPL de421 + Skyfield。星表は初回だけ VizieR から取得して
キャッシュし、取得できない環境では既存の明るい星リストで代用する（描画は続く）。
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import math
import os
import random
import re

import numpy as np
from mcp.types import CallToolResult, ImageContent, TextContent

import base64

from .cache import TTL_DAILY, disk_get, ttl_cache
from .img_common import (figure_notes, figure_payload, figure_text_block, load_font,
                         media_link_line, pixel_near, primary_spec, save_output,
                         scale_spec, view_spec)
from .input_utils import as_float

_LOCAL = os.environ.get("LOCALAPPDATA", ".")
CACHE_DIR = os.path.join(_LOCAL, "Temp", "space_finder_mcp")
CATALOG_CSV = os.path.join(CACHE_DIR, "stars_hip85.csv")
SKYFIELD_DIR = os.path.join(_LOCAL, "Temp", "skyfield_data")
CATALOG_URL = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=I/239/hip_main"
               "&-out=HIP,_RA,_DE,Vmag,B-V&-out.max=unlimited&Vmag=%3C8.5")
CATALOG_LABEL = "Hipparcos 実星表（Vmag<8.5）"

# ---------- 見た目の指定（描画と説明が食い違わないよう1か所にまとめる） ----------
CITY = (7, 8, 13)                 # 夜の建物のシルエット（draw_ground が昼色へ混ぜる）
CITY_FAR = (22, 19, 23)
CITY_TOP = (26, 28, 38)
POLLUTION = np.array([62, 42, 26], np.float32) / 255.0   # 街あかり（暖色）
AIRGLOW = np.array([10, 20, 18], np.float32) / 255.0     # 大気光（淡い緑）
AZ_CITY = 170.0                                          # 街あかりが最も強い方位（南＝都心方向）
CITY_DAY = (54, 58, 66)           # 昼の建物（明るい空に対するシルエット）
CITY_FAR_DAY = (86, 92, 102)
CITY_TOP_DAY = (108, 114, 124)
WINDOW = (255, 214, 138)                                 # 窓あかり
LAMP = (255, 196, 120)                                   # 街灯
SIGN = ((220, 70, 70), (90, 170, 255), (110, 220, 150), (255, 190, 90))
RULER_BG = (10, 11, 17)
RULER_FG = (200, 209, 232)
RULER_DIM = (110, 120, 146)
HDR_BG = (6, 7, 12)
HDR_FG = (234, 240, 252)
HDR_DIM = (152, 164, 192)
SAT_RGB = (255, 60, 50)           # 人工衛星のマーカー（検証で色を探すので固定）
SAT_LABEL = (255, 176, 166)
SAT_TRAIL = (255, 138, 128)

GAIN = 0.105           # 星の明るさの基準（mag 6.5 の星が淡い点になる程度）
BG_DIM = 0.55          # 背景の星の減光（淡い星ほど落として、注目天体を目立たせる）
BLDG_H = 0.50          # 建物の高さの倍率（1.0=従来、0.5=半分）
FOCUS_BOOST = 1.30     # 注目天体（惑星・主要恒星・銀河）のマーカーの強調
EXT_K = 0.22           # 大気減光係数 (mag/airmass, Vバンド)
SAT_MAG = 1.10         # これより明るい星は芯を大きく＋スパイクを描く
BLOOM = (2, 2, 0.55)   # 光背: ぼかし半径2px×2回、元画像へ 0.55 倍で加算
TRAIL_MINUTES = 60     # 低軌道衛星の軌道予測（前後この分数・1分刻み）
TRAIL_STEP_MIN = 1

# 光害の程度。限界等級・天の川の見え方・空の明るさをまとめて切り替える。
LP_MODES = {
    "city":   dict(lim_z=4.6, lim_h=3.2, mw=0.012, poll=2.00, name="都心（光害 強）"),
    "suburb": dict(lim_z=7.2, lim_h=4.8, mw=0.065, poll=0.95, name="郊外（光害 中）"),
    "dark":   dict(lim_z=8.6, lim_h=5.4, mw=0.105, poll=0.45, name="山間・離島（光害 弱）"),
}
LP = dict(LP_MODES["suburb"])


def _set_lp(mode):
    global LP
    LP = dict(LP_MODES.get(str(mode or "").lower(), LP_MODES["suburb"]))
    return LP


# 太陽高度ごとの空の色（昼→夕焼け→薄明→夜）。太陽高度で線形補間する。
# (太陽高度, 天頂色, 反対側の地平線色, 太陽側の焼け色, 焼けの強さ, 天頂限界等級, 地平線限界等級, 天の川)
SKY_STATES = [
    (-30.0, (3, 4, 11), (7, 7, 13), (22, 16, 20), 0.10, 8.6, 5.4, 1.00),
    (-18.0, (4, 6, 22), (10, 10, 19), (35, 25, 30), 0.16, 6.5, 4.6, 0.85),
    (-15.0, (6, 9, 30), (15, 15, 28), (55, 35, 40), 0.24, 5.5, 4.0, 0.60),
    (-12.0, (13, 18, 50), (30, 30, 52), (100, 56, 54), 0.40, 4.5, 3.0, 0.35),
    (-8.0, (26, 34, 80), (68, 64, 88), (185, 92, 66), 0.66, 2.5, 1.5, 0.10),
    (-4.0, (38, 52, 112), (118, 104, 122), (245, 120, 72), 1.00, 0.5, 0.0, 0.00),
    (0.0, (52, 72, 138), (188, 160, 152), (255, 150, 80), 1.25, -1.5, -1.5, 0.00),
    (3.0, (72, 100, 170), (205, 190, 178), (255, 190, 130), 0.80, -4.6, -4.6, 0.00),
    (10.0, (95, 140, 215), (195, 210, 228), (255, 225, 195), 0.35, -4.6, -4.6, 0.00),
    (40.0, (105, 150, 220), (190, 210, 230), (255, 240, 220), 0.15, -4.6, -4.6, 0.00),
]

# 依頼表示の対象にする代表的な銀河・星団・星雲（J2000 の位置と代表等級）
_DSO = [
    ("M31 アンドロメダ銀河", 0.7122, 41.269, 3.4, "galaxy"),
    ("M33 さんかく座銀河", 1.5642, 30.660, 5.7, "galaxy"),
    ("M42 オリオン大星雲", 5.5881, -5.391, 4.0, "nebula"),
    ("M45 プレアデス星団", 3.7900, 24.117, 1.6, "cluster"),
    ("M8 干潟星雲", 18.0633, -24.383, 6.0, "nebula"),
    ("M13 ヘルクレス座球状星団", 16.6947, 36.460, 5.8, "cluster"),
    ("M27 あれい星雲", 19.9933, 22.721, 7.4, "nebula"),
]
_DSO_COLOR = {"galaxy": (206, 186, 255), "nebula": (186, 255, 226), "cluster": (255, 226, 186)}
# 見えるかどうかの判定に使う代表的な実効等級（実際の等級は時々で変わる近似値）
PLANET_MAG = {"太陽": -26.7, "月": -12.7, "水星": -0.5, "金星": -4.4, "火星": 0.5,
              "木星": -2.5, "土星": 0.7, "天王星": 5.7, "海王星": 7.8}
_PLANET_COLOR = {"太陽": (1.00, 0.97, 0.88), "月": (0.95, 0.95, 0.96), "水星": (0.80, 0.78, 0.74),
                 "金星": (1.00, 0.94, 0.78), "火星": (1.00, 0.62, 0.45), "木星": (1.00, 0.93, 0.80),
                 "土星": (0.96, 0.90, 0.70), "天王星": (0.70, 0.90, 0.95),
                 "海王星": (0.55, 0.70, 0.98)}
_PLANET_ORDER = ("太陽", "月", "水星", "金星", "火星", "木星", "土星", "天王星", "海王星")
_CARD = {0: "北", 45: "北東", 90: "東", 135: "南東", 180: "南", 225: "南西", 270: "西", 315: "北西"}


# ---------- データ（星表・暦） ----------
def _load_ephemeris():
    from skyfield.api import Loader
    load = Loader(SKYFIELD_DIR, verbose=False)
    return load.timescale(), load("de421.bsp")


def _read_catalog_csv(path):
    ra, dec, mag, bv = [], [], [], []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ra.append(float(row["ra_deg"])); dec.append(float(row["dec_deg"]))
                mag.append(float(row["vmag"])); bv.append(float(row["bv"]))
            except (KeyError, ValueError):
                continue
    return dict(ra_deg=np.array(ra, np.float64), dec_deg=np.array(dec, np.float64),
                vmag=np.array(mag, np.float32), bv=np.array(bv, np.float32))


def _parse_vizier_tsv(text):
    """VizieR の asu-tsv（HIP, _RAJ2000, _DEJ2000, Vmag, B-V）を CSV に直す。"""
    out = ["hip,ra_deg,dec_deg,vmag,bv"]
    started = False
    for line in str(text).splitlines():
        if line.startswith("------"):
            started = True
            continue
        if not started:
            continue
        p = line.split("\t")
        if len(p) < 5:
            continue
        try:
            int(p[0]); float(p[1]); float(p[2]); float(p[3]); float(p[4])
        except ValueError:
            continue
        out.append(",".join(x.strip() for x in p[:5]))
    return "\n".join(out) + "\n"


@ttl_cache(TTL_DAILY, maxsize=2)
def _star_catalog():
    """Hipparcos 実星表（Vmag<8.5）を返す。

    初回だけ VizieR から取得して CSV にキャッシュする（de421.bsp と同じ扱い）。
    取得できない環境では既存の明るい星リストで代用し、描画自体は続ける。
    戻り値の "source" は実際に使った出典（偽らない）。
    """
    if os.path.exists(CATALOG_CSV) and os.path.getsize(CATALOG_CSV) > 100000:
        try:
            cat = _read_catalog_csv(CATALOG_CSV)
            if len(cat["vmag"]) > 100:
                cat["source"] = CATALOG_LABEL
                cat["fallback"] = False
                return cat
        except Exception:
            pass
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        raw = disk_get(CATALOG_URL, subdir="stars", ttl=TTL_DAILY, timeout=90,
                       max_bytes=20_000_000)
        if raw:
            csv_text = _parse_vizier_tsv(raw.decode("utf-8", "replace"))
            tmp = CATALOG_CSV + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
            os.replace(tmp, CATALOG_CSV)
            cat = _read_catalog_csv(CATALOG_CSV)
            if len(cat["vmag"]) > 100:
                cat["source"] = CATALOG_LABEL
                cat["fallback"] = False
                return cat
    except Exception:
        pass
    from .sky_overlay import _BRIGHT_STARS                    # 星表が取れないときの代用
    ra = np.array([r for _n, r, _d in _BRIGHT_STARS], np.float64)
    dec = np.array([d for _n, _r, d in _BRIGHT_STARS], np.float64)
    return dict(ra_deg=ra * 15.0, dec_deg=dec, vmag=np.full(len(ra), 1.0, np.float32),
                bv=np.full(len(ra), 0.6, np.float32),
                source="名のある明るい恒星のみ（星表を取得できませんでした）", fallback=True)


# ---------- 天文計算 ----------
def airmass(alt_deg):
    """Kasten & Young (1989) の大気質量。"""
    alt = np.clip(np.asarray(alt_deg, np.float64), 0.0, 90.0)
    return 1.0 / (np.sin(np.radians(alt)) + 0.50572 * (alt + 6.07995) ** -1.6364)


def bv_to_rgb(bv):
    """B-V 色指数 → 星の色（Ballesteros の Teff 近似 → 黒体色）。彩度は落とす。"""
    bv = np.clip(np.asarray(bv, np.float32), -0.40, 2.00)
    t = 4600.0 * (1.0 / (0.92 * bv + 1.70) + 1.0 / (0.92 * bv + 0.62))   # K
    tt = np.clip(t, 1000.0, 40000.0) / 100.0
    r = np.where(tt <= 66, 255.0, 329.698727446 * np.maximum(tt - 60.0, 1e-3) ** -0.1332047592)
    g = np.where(tt <= 66,
                 99.4708025861 * np.log(np.maximum(tt, 1e-3)) - 161.1195681661,
                 288.1221695283 * np.maximum(tt - 60.0, 1e-3) ** -0.0755148492)
    b = np.where(tt >= 66, 255.0,
                 np.where(tt <= 19, 0.0,
                          138.5177312231 * np.log(np.maximum(tt - 10.0, 1e-3)) - 305.0447927307))
    rgb = np.clip(np.stack([r, g, b], -1) / 255.0, 0.0, 1.0)
    rgb /= np.maximum(rgb.max(-1, keepdims=True), 1e-6)          # 明るさを正規化
    rgb = rgb * 0.85 + 0.15                                      # 白へ寄せる（実視の星はほぼ白）
    return (rgb / np.maximum(rgb.max(-1, keepdims=True), 1e-6)).astype(np.float32)


def box_blur(a, r):
    """分離ボックスぼかし（cumsum のみ。scipy 非依存）。"""
    out = a.astype(np.float32, copy=True)
    k = 2 * r + 1
    for ax in (0, 1):
        n = out.shape[ax]
        pad = [(0, 0)] * out.ndim
        pad[ax] = (r, r)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=ax)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
        out = (np.take(c, np.arange(k, k + n), axis=ax) - np.take(c, np.arange(n), axis=ax)) / k
    return out


def _parse_when(when):
    """when（ISO8601, UTC）→ (y, mo, d, h, mi, s)。不正・省略なら現在時刻。"""
    if when:
        m = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?",
                     str(when).strip().replace("Z", "+00:00"))
        if m:
            g = [int(x) if x else 0 for x in m.groups()]
            return (g[0], g[1], g[2], g[3], g[4], g[5])
        return None if str(when).strip() == "" else "bad"
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now.year, now.month, now.day, now.hour, now.minute, now.second)


def _sky_state(sun_alt):
    """太陽高度 → 空の色・限界等級・天の川の見え方（表を線形補間）。"""
    alts = np.array([s[0] for s in SKY_STATES], np.float64)

    def col(idx):                       # 色はチャンネルごとに補間する
        return [int(round(float(np.interp(sun_alt, alts, [s[idx][c] for s in SKY_STATES]))))
                for c in range(3)]

    def sc(i):
        return float(np.interp(sun_alt, alts, [s[i] for s in SKY_STATES]))

    return dict(zenith=col(1), horizon=col(2), glow=col(3), glow_strength=sc(4),
                lim_z=sc(5), lim_h=sc(6), mw=sc(7))


def phase_name(sun_alt, rising):
    """表示用の時間帯名。"""
    if sun_alt > 3.0:
        return "昼"
    if sun_alt > -0.5:
        return "朝焼け" if rising else "夕方"
    if sun_alt > -6.0:
        return "薄明（市民）" + ("・明け" if rising else "・暮れ")
    if sun_alt > -12.0:
        return "薄明（航海）" + ("・明け" if rising else "・暮れ")
    if sun_alt > -18.0:
        return "薄明（天文）" + ("・明け" if rising else "・暮れ")
    return "夜"


def _sun_state(eph, ts, t, lat, lon):
    """太陽の高度・方位と、昇っているか沈んでいるか（15分後と比べる）。"""
    from skyfield.api import wgs84
    where = eph["earth"] + wgs84.latlon(lat, lon)
    alt0, az0, _ = where.at(t).observe(eph["sun"]).apparent().altaz()
    alt1, _, _ = where.at(ts.tt_jd(t.tt + 15.0 / 1440.0)).observe(eph["sun"]).apparent().altaz()
    return dict(alt=float(alt0.degrees), az=float(az0.degrees),
                rising=bool(alt1.degrees > alt0.degrees))


def _sky_background(sky_h, W, span, az0, sun_alt, sun_az):
    """太陽高度で変わる空の地色。焼けは太陽の方位に出る。夜は街あかり（光害）を重ねる。"""
    st = _sky_state(sun_alt)
    rows = np.arange(sky_h, dtype=np.float32)
    alt = 90.0 * (1.0 - rows / float(sky_h))
    t = (alt / 90.0)[:, None, None]
    zen = np.array(st["zenith"], np.float32) / 255.0
    hor = np.array(st["horizon"], np.float32) / 255.0
    glo = np.array(st["glow"], np.float32) / 255.0
    sky = zen[None, None, :] + (hor - zen)[None, None, :] * (1.0 - t) ** 2.0
    az_cols = az0 + (np.arange(W, dtype=np.float32) + 0.5) / W * span
    daz_sun = np.abs(((az_cols - sun_az + 180.0) % 360.0) - 180.0)
    sunside = np.exp(-(daz_sun / 75.0) ** 2)[None, :, None] * np.exp(-alt / 17.0)[:, None, None]
    sky = sky + glo[None, None, :] * st["glow_strength"] * sunside
    night = float(np.clip(-(sun_alt + 6.0) / 6.0, 0.0, 1.0))     # 太陽が低いほど街あかりが効く
    if night > 0.0:
        daz_city = np.abs(((az_cols - AZ_CITY + 180.0) % 360.0) - 180.0)
        sky = sky + POLLUTION[None, None, :] * night * LP["poll"] * (
            0.34 * np.exp(-alt / 11.0)[:, None, None]
            + 0.78 * np.exp(-(daz_city / 78.0) ** 2)[None, :, None] * np.exp(-alt / 9.0)[:, None, None])
        sky = sky + AIRGLOW[None, None, :] * night * np.exp(-alt / 26.0)[:, None, None]
    return np.clip(sky, 0.0, 1.0), st


def galactic_lb(sky_h, W, span, az0, lat, lon, lst_hours):
    """各画素（高度・方位）→ 銀経 l・銀緯 b（J2000）。"""
    rows = np.arange(sky_h, dtype=np.float64)
    alt = np.repeat((90.0 * (1.0 - rows / sky_h))[:, None], W, 1)
    az = np.repeat((az0 + (np.arange(W) + 0.5) / W * span)[None, :], sky_h, 0)
    a, d, phi = np.radians(az), np.radians(alt), math.radians(lat)
    sin_dec = np.sin(d) * math.sin(phi) + np.cos(d) * math.cos(phi) * np.cos(a)
    dec = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
    ha = np.arctan2(-np.sin(a) * np.cos(d),
                    np.sin(d) * math.cos(phi) - np.cos(d) * math.sin(phi) * np.cos(a))
    ra = np.radians(((lst_hours - np.degrees(ha) / 15.0) % 24.0) * 15.0)
    aG, dG = math.radians(192.85948), math.radians(27.12825)
    y = np.cos(dec) * np.sin(ra - aG)
    x = np.sin(dec) * math.cos(dG) - np.cos(dec) * math.sin(dG) * np.cos(ra - aG)
    b = np.degrees(np.arcsin(np.clip(np.sin(dec) * math.sin(dG)
                                     + np.cos(dec) * math.cos(dG) * np.cos(ra - aG), -1.0, 1.0)))
    l = (122.93192 - np.degrees(np.arctan2(y, x))) % 360.0
    return l, b, alt


def milky_way_layer(l, b, alt):
    """銀経・銀緯から天の川の面輝度を近似（銀河中心側で明るく細い＋暗黒帯＋斑状構造）。"""
    dl = ((l + 180.0) % 360.0) - 180.0                           # -180..180（0=銀河中心）
    bw = 6.0 + 5.5 * (1.0 - np.exp(-(dl / 70.0) ** 2))           # 幅（中心側は細い）
    amp = 0.50 + 1.25 * np.exp(-(dl / 52.0) ** 2) + 0.60 * np.exp(-((l - 78.0) / 26.0) ** 2)
    band = amp * np.exp(-(b / bw) ** 2)
    lane = 1.0 - 0.55 * np.exp(-(b / 1.5) ** 2) * (0.5 + 0.5 * np.sin(np.radians(l * 3.7) + 1.1))
    rift = 1.0 - 0.45 * np.exp(-(b / 3.0) ** 2) * np.exp(-((l - 45.0) / 28.0) ** 2)   # 大裂溝
    tex = 0.66 + 0.38 * (0.5 + 0.5 * np.sin(np.radians(l * 5.1 + 30.0))
                         * np.sin(np.radians(b * 7.3 + 12.0)))
    d_car = ((l - 305.0 + 180.0) % 360.0) - 180.0
    clump = (1.0 + 0.85 * np.exp(-(dl / 9.0) ** 2 - (b / 5.0) ** 2)          # いて座の星雲
             + 0.70 * np.exp(-((l - 80.0) / 12.0) ** 2 - (b / 6.0) ** 2)     # はくちょう座
             + 0.55 * np.exp(-(d_car / 16.0) ** 2 - (b / 7.0) ** 2))         # りゅうこつ座
    mw = band * lane * rift * tex * clump
    mw = mw * 10.0 ** (-0.4 * EXT_K * (airmass(alt) - 1.0))      # 大気減光
    mw = mw * np.clip(alt / 12.0, 0.0, 1.0)                      # 低空は光害で見えない
    warm = 0.30 + 0.70 * np.exp(-(dl / 60.0) ** 2)               # 銀河中心側は赤っぽい
    col = np.stack([np.full_like(mw, 1.00), 1.00 - 0.05 * warm, 1.00 - 0.14 * warm], -1)
    # 未分解の星の粒（帯のざらつき。星の密集そのものは星表の淡い層が担う）
    g = np.random.default_rng(20261010)
    prob = np.clip(band / max(1e-9, float(band.max())) * 0.30, 0.0, 0.45)
    grain = (g.random(mw.shape) < prob) * g.uniform(0.35, 1.0, mw.shape)
    grain = grain * np.clip(alt / 20.0, 0.0, 1.0) * 10.0 ** (-0.4 * EXT_K * (airmass(alt) - 1.0))
    return mw, col, grain * 0.014


def mw_guide_points(b, alt_g, sky_h, header, band=7.0):
    """銀河面（銀緯 0）と帯の縁（±band）の画面上の点列。昼でも天の川の位置が分かるように。"""
    rows = []
    for bb in (0.0, -band, band):
        pts = []
        for x in range(b.shape[1]):
            col = b[:, x]
            i = int(np.argmin(np.abs(col - bb)))
            if abs(float(col[i]) - bb) > 2.0 or float(alt_g[i, x]) <= 1.0:
                pts.append(None)
            else:
                pts.append((x, header + (90.0 - float(alt_g[i, x])) / 90.0 * sky_h))
        rows.append(pts)
    return rows


# ---------- 描画の部品 ----------
def _gauss(r):
    """中心が最大のガウス（マーカー用）。"""
    rad = int(math.ceil(3.2 * r))
    gx = np.arange(-rad, rad + 1, dtype=np.float32)
    return np.exp(-(gx[None, :] ** 2 + gx[:, None] ** 2) / (2.0 * r * r)).astype(np.float32)


def _ring_patch(r, w):
    """中心が空いたリング（「今は見えないが見える位置」を示すマーカー）。"""
    rad = int(math.ceil(r + 3.0 * w))
    gx = np.arange(-rad, rad + 1, dtype=np.float32)
    d = np.sqrt(gx[None, :] ** 2 + gx[:, None] ** 2)
    return np.exp(-((d - r) / w) ** 2).astype(np.float32)


def _add_patch(lin, patch, x, y):
    """patch（H×W か H×W×3）を (x,y) 中心に加算する（画面外は切る）。"""
    if patch.ndim == 2:
        patch = patch[:, :, None]
    h, w = patch.shape[:2]
    x0, y0 = int(round(x - (w - 1) / 2.0)), int(round(y - (h - 1) / 2.0))
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(lin.shape[1], x0 + w), min(lin.shape[0], y0 + h)
    if xb > xa and yb > ya:
        lin[ya:yb, xa:xb] += patch[ya - y0:yb - y0, xa - x0:xb - x0]


def _moon_shade(size, illum, angle):
    """Lambert 球の位相マスク（0=新月, 1=満月）。

    angle は「太陽がどの向きにあるか」を画像座標のラジアンで表したもの。
    欠けの形は位相角（照度）から、明るい側の向きは太陽の方向から決まる。
    """
    r = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size]
    u = (xx - (size - 1) / 2.0) / r
    v = (yy - (size - 1) / 2.0) / r
    d2 = u * u + v * v
    w = np.sqrt(np.clip(1.0 - d2, 0.0, 1.0))
    uu = u * math.cos(angle) + v * math.sin(angle)          # 太陽方向へ回した座標
    ca = 2.0 * float(illum) - 1.0                           # 位相角の余弦
    sa = math.sqrt(max(0.0, 1.0 - ca * ca))
    return np.where(d2 <= 1.0, np.clip(uu * sa + w * ca, 0.0, 1.0), 0.0).astype(np.float32)


def draw_stars(lin, cat, alt, az, span, az0, sky_h, W, header, lim_z, lim_h):
    """恒星を「加算光」として lin（線形RGB）に積む。等級・色・減光を反映。"""
    alt = np.asarray(alt, np.float64); az = np.asarray(az, np.float64)
    up = alt > 0.4
    x_all = ((az - az0) % 360.0) / span * W
    keep = up & (x_all >= -20) & (x_all <= W + 20)
    idx = np.nonzero(keep)[0]
    x, alt, m0, bv = x_all[idx], alt[idx], cat["vmag"][idx].astype(np.float64), cat["bv"][idx]

    m = m0 + EXT_K * (airmass(alt) - 1.0)                        # 大気減光
    lim = lim_h + (lim_z - lim_h) * (alt / 90.0) ** 0.5
    vis = m < lim
    x, alt, m, bv = x[vis], alt[vis], m[vis], bv[vis]

    y = header + (90.0 - alt) / 90.0 * sky_h
    amp = (10.0 ** (-0.4 * (m - 6.5)) * GAIN).astype(np.float32)
    rc = (0.45 + 0.62 * np.maximum(0.0, 6.5 - m) ** 0.80).astype(np.float32)
    rgb = bv_to_rgb(bv)
    deep = m > 6.1                                               # 淡い星＝天の川の粒
    amp[deep] *= 1.6
    rc[deep] = np.maximum(rc[deep], 0.9)
    bgdim = np.clip((m - 2.5) / 3.5, 0.0, 1.0)                   # 背景の星は淡いものほど落とす
    amp *= (1.0 - (1.0 - BG_DIM) * bgdim).astype(np.float32)

    H, Wd = lin.shape[0], lin.shape[1]
    for i in range(len(x)):
        xi, yi, r, a = float(x[i]), float(y[i]), float(rc[i]), float(amp[i])
        if a < 0.0018 or yi < -6 or yi > H + 6:
            continue
        rad = max(1, int(math.ceil(3.0 * r)))
        x0, x1 = int(xi) - rad, int(xi) + rad + 1
        y0, y1 = int(yi) - rad, int(yi) + rad + 1
        if x1 <= 0 or x0 >= Wd or y1 <= 0 or y0 >= H:
            continue
        gx = np.arange(max(0, x0), min(Wd, x1), dtype=np.float32) - xi
        gy = np.arange(max(0, y0), min(H, y1), dtype=np.float32) - yi
        g = np.exp(-(gx[None, :] ** 2 + gy[:, None] ** 2) / (2.0 * r * r)).astype(np.float32) * a
        sx0, sy0 = max(0, x0), max(0, y0)
        lin[sy0:sy0 + g.shape[0], sx0:sx0 + g.shape[1]] += g[:, :, None] * rgb[i][None, None, :]
        if m[i] < SAT_MAG:                                       # 明るい星は4方向の淡いスパイク
            sp = min(a, 3.0) * 0.055
            span_k = int(4 * r) + 8
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                for k in range(1, span_k):
                    px, py = int(round(xi + dx * k)), int(round(yi + dy * k))
                    if 0 <= px < Wd and 0 <= py < H:
                        lin[py, px] += sp * rgb[i] * (1.0 - k / float(span_k)) ** 1.6
    return dict(stars_total=int(len(cat["vmag"])), stars_up=int(up.sum()),
                stars_in_view=int(keep.sum()), stars_drawn=int(len(x)),
                faint_drawn=int(deep.sum()))


def draw_planets(lin, pl, span, az0, sky_h, W, header, lim_fn, sun_az, sun_alt, in_focus=None):
    """太陽系天体を描く。空が明るくて見えなくても「位置」は示す（白い輪郭マーカー）。

    見えている天体は実物に近い色の点＋リング。月は位相と距離を反映する。
    """
    drawn, labels = {}, []
    for nm in _PLANET_ORDER:
        v = pl.get(nm)
        if not v or v["alt"] <= (-1.5 if nm == "太陽" else 0.6):
            continue
        x = ((v["az"] - az0) % 360.0) / span * W
        if x < -30 or x > W + 30:
            continue
        y = header + (90.0 - v["alt"]) / 90.0 * sky_h
        rgb = np.array(_PLANET_COLOR.get(nm, (1, 1, 1)), np.float32)
        vis = PLANET_MAG.get(nm, 5.0) < lim_fn(float(v["alt"]))
        inf = True if in_focus is None else in_focus(nm)
        label_txt = nm
        if nm == "月":
            illum = float(v.get("illum", 1.0))
            label_txt = "月（照度 {:.0f}%）".format(illum * 100)
            d_az = ((sun_az - v["az"] + 180.0) % 360.0) - 180.0
            # 画像は x=方位の増加方向, y=高度の減少方向。太陽のいる向きへ欠けの明るい側を向ける
            ang = math.atan2(-(float(sun_alt) - float(v["alt"])),
                             d_az * math.cos(math.radians(float(v["alt"]))))
            r_moon = 13.5 * min(1.08, max(0.92, 384400.0 / max(1.0, float(v.get("dist_km", 384400.0)))))
            if illum < 0.03:                              # 新月前後は円盤が暗いので位置だけ示す
                _add_patch(lin, (_ring_patch(12.0, 1.3) * 0.85)[:, :, None] * np.ones(3, np.float32), x, y)
                labels.append((label_txt, float(x), float(y), False))
                drawn[nm] = dict(az=round(v["az"], 1), alt=round(v["alt"], 1), visible=False,
                                 illum=round(illum, 4), dist_km=round(float(v.get("dist_km", 0)), 0))
                continue
            _add_patch(lin, (_moon_shade(int(round(r_moon * 2)) + 1, illum, ang) * 0.95)[:, :, None]
                       * rgb[None, None, :], x, y)
        elif nm == "太陽":
            g = _gauss(46.0) * 1.20                       # 芯＋広い光背（眩しさ）
            c = _gauss(13.0) * 2.5
            o = (g.shape[0] - c.shape[0]) // 2
            g[o:o + c.shape[0], o:o + c.shape[1]] += c
            _add_patch(lin, np.clip(g, 0.0, 1.0)[:, :, None] * 0.95 * rgb[None, None, :], x, y)
        elif not inf:
            if vis:
                _add_patch(lin, _gauss(3.4)[:, :, None] * 0.9 * rgb[None, None, :], x, y)
            continue
        elif vis:                                         # 見えている: 実物に近い点＋細いリング
            _add_patch(lin, (_gauss(5.0) * FOCUS_BOOST)[:, :, None] * rgb[None, None, :], x, y)
            _add_patch(lin, (_ring_patch(10.0, 1.0) * 0.40)[:, :, None] * np.ones(3, np.float32), x, y)
        else:                                             # 昼・薄明: 位置だけを示す
            _add_patch(lin, (_ring_patch(10.0, 1.3) * 0.85)[:, :, None] * np.ones(3, np.float32), x, y)
        labels.append((label_txt, float(x), float(y), bool(vis)))
        drawn[nm] = dict(az=round(v["az"], 1), alt=round(v["alt"], 1), visible=bool(vis),
                         illum=round(float(v.get("illum", 0.0)), 4) if nm == "月" else None,
                         dist_km=round(float(v.get("dist_km", 0.0)), 0) if nm == "月" else None)
    return drawn, labels


def dso_altaz(ts, eph, when_utc, lat, lon):
    """代表的な銀河・星団・星雲の alt/az。"""
    from skyfield.api import Star, wgs84
    t = ts.utc(*when_utc)
    pos = Star(ra_hours=np.array([d[1] for d in _DSO]), dec_degrees=np.array([d[2] for d in _DSO]))
    app = (eph["earth"] + wgs84.latlon(lat, lon)).at(t).observe(pos).apparent()
    alt, az, _ = app.altaz()
    return alt.degrees, az.degrees


def draw_focus_points(lin, items, span, az0, sky_h, W, header, lim_fn, in_focus=None):
    """依頼表示の天体（主要恒星・銀河・星団）の位置を示す。

    見えている天体は色付きの点＋リング、今は見えない天体は輪郭だけ（位置ガイド）。
    """
    out = []
    for nm, alt_d, az_d, mag, rgb, kind in items:
        if alt_d <= 0.8 or (in_focus is not None and not in_focus(nm)):
            continue
        x = ((az_d - az0) % 360.0) / span * W
        if x < -30 or x > W + 30:
            continue
        y = header + (90.0 - alt_d) / 90.0 * sky_h
        col = np.array(rgb, np.float32)
        vis = float(mag) < lim_fn(float(alt_d))
        if vis:
            _add_patch(lin, (_gauss(4.2) * FOCUS_BOOST)[:, :, None] * col[None, None, :], x, y)
            _add_patch(lin, (_ring_patch(9.0, 1.0) * 0.40)[:, :, None] * np.ones(3, np.float32), x, y)
        else:
            _add_patch(lin, (_ring_patch(9.0, 1.3) * 0.80)[:, :, None] * np.ones(3, np.float32), x, y)
        out.append((nm, float(x), float(y), bool(vis), kind))
    return out


def _skyline_row(dr, W, H, y_horizon, ground_h, rng, near=True, lit=True):
    """建物を1列描く（near=手前の列: 窓あかり・看板あり / 奥の列: 一段暗い）。"""
    out, x = [], -30
    pxdeg = ground_h / 14.0
    while x < W + 30:
        w = rng.randint(26, 116) if near else rng.randint(40, 150)
        deg = (rng.uniform(1.0, 3.6) if near else rng.uniform(0.7, 2.4)) * BLDG_H
        if rng.random() < (0.24 if near else 0.06):
            deg = rng.uniform(4.5, 9.5) * BLDG_H
        h = max(8, min(int(deg * pxdeg), int(ground_h * (0.92 if near else 0.62) * BLDG_H)))
        top = y_horizon - h
        body = CITY if near else CITY_FAR
        dr.rectangle([x, top, x + w, H], fill=body + (255,))
        if near:
            dr.line([x, top, x + w, top], fill=CITY_TOP + (255,), width=2)
            if rng.random() < 0.30:                                   # 段状の上層部
                w2 = int(w * rng.uniform(0.35, 0.65))
                x2 = x + (w - w2) // 2
                dr.rectangle([x2, top - int(h * 0.35), x2 + w2, top + 2], fill=body + (255,))
                dr.line([x2, top - int(h * 0.35), x2 + w2, top - int(h * 0.35)],
                        fill=CITY_TOP + (255,), width=2)
            if rng.random() < 0.18:                                   # 屋上のアンテナ
                ax = x + w // 2
                dr.line([ax, top, ax, top - rng.randint(5, 17)], fill=CITY_TOP + (255,), width=2)
            dens = rng.uniform(0.04, 0.14) if lit else 0.0            # 建物ごとに灯り密度を変える
            for wy in range(top + 10, min(H - 4, y_horizon + ground_h - 8), 11):   # 窓あかり
                for wx in range(x + 5, x + w - 5, 9):
                    if rng.random() < dens:
                        u = rng.random()
                        c = WINDOW if u < 0.55 else ((190, 210, 255) if u < 0.85 else (255, 246, 226))
                        dr.rectangle([wx, wy, wx + 3, wy + 4], fill=c + (rng.randint(140, 255),))
            if lit and rng.random() < 0.12:                           # 看板
                sx = x + rng.randint(4, max(5, w - 34))
                sy = top + rng.randint(14, 40)
                dr.rectangle([sx, sy, sx + 26, sy + 9], fill=SIGN[rng.randrange(4)] + (225,))
        out.append((x, x + w, top))
        x += w + (rng.randint(2, 10) if near else rng.randint(6, 26))
    return out


def draw_ground(img, y_horizon, ground_h, W, seed=20261010, day_mix=0.0):
    """画面下部の街並み（手続き生成）。建物が低空の星を隠す。昼は明るいシルエットで灯りなし。"""
    from PIL import Image, ImageDraw, ImageFilter
    rng = random.Random(seed)
    H = img.size[1]
    day_mix = float(np.clip(day_mix, 0.0, 1.0))

    def _mix(a, b):
        return tuple(int(round(a[i] + (b[i] - a[i]) * day_mix)) for i in range(3))

    global CITY, CITY_FAR, CITY_TOP
    CITY, CITY_FAR, CITY_TOP = _mix((7, 8, 13), CITY_DAY), _mix((22, 19, 23), CITY_FAR_DAY), \
        _mix((26, 28, 38), CITY_TOP_DAY)
    lit = day_mix < 0.55
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    dr.rectangle([0, y_horizon, W, H], fill=_mix((4, 5, 8), (46, 50, 56)) + (255,))   # 地面
    far_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))              # 奥の列（霞ませる）
    far = _skyline_row(ImageDraw.Draw(far_layer), W, H, y_horizon, ground_h, rng, near=False, lit=lit)
    layer.alpha_composite(far_layer.filter(ImageFilter.GaussianBlur(1.6)))
    if lit:
        haze = Image.new("L", (W, 150), 0)                           # 奥の列と手前の列の間の街あかり
        hd = ImageDraw.Draw(haze)
        for x in range(0, W, 4):
            hd.line([x, 150, x, 150 - int(rng.uniform(24, 62))], fill=76)
        layer.paste(Image.new("RGBA", (W, 150), (132, 86, 44, 255)),
                    (0, y_horizon - 150), haze.filter(ImageFilter.GaussianBlur(26)))
    near = _skyline_row(dr, W, H, y_horizon, ground_h, rng, near=True, lit=lit)   # 手前の列

    tx, th = int(W * 0.62), int(ground_h * 1.02 * BLDG_H)            # 塔（東京タワー風）
    dr.polygon([(tx - 16, y_horizon), (tx + 16, y_horizon), (tx + 5, y_horizon - th),
                (tx - 5, y_horizon - th)], fill=CITY + (255,))
    dr.line([tx - 20, y_horizon - th + 12, tx + 20, y_horizon - th + 12],
            fill=CITY_TOP + (255,), width=2)
    dr.ellipse([tx - 4, y_horizon - th - 8, tx + 4, y_horizon - th], fill=(255, 90, 70, 235))
    for _ in range(max(6, W // 220) if lit else 0):                  # 街灯
        lx = rng.randint(10, W - 10)
        ly = y_horizon + ground_h + rng.randint(4, 22)
        g = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([52, 52, 88, 88], fill=LAMP + (150,))
        layer.alpha_composite(g.filter(ImageFilter.GaussianBlur(18)), (lx - 70, ly - 70))
        dr.ellipse([lx - 2, ly - 2, lx + 2, ly + 2], fill=LAMP + (255,))
    return layer, near + far


def draw_scale(dr, W, H, top, span, az0, font_s, font_m, font_b):
    """下部の方位目盛り。5°刻みの目盛り、30°ごとに数値、45°ごとに漢字（2段に分ける）。"""
    dr.rectangle([0, top, W, H], fill=RULER_BG)
    dr.line([0, top, W, top], fill=(60, 66, 88), width=2)
    for a in range(int(math.floor(az0 / 5.0) * 5), int(math.ceil((az0 + span) / 5.0) * 5) + 1, 5):
        x = (a - az0) / span * W
        if x < 0 or x > W:
            continue
        a360 = a % 360
        major, mid = (a360 % 45 == 0), (a360 % 30 == 0)
        ln = 18 if major else (12 if mid else 6)
        dr.line([x, top + 3, x, top + 3 + ln], fill=(RULER_FG if major else RULER_DIM),
                width=2 if major else 1)
        lx = min(max(x, 30), W - 30)          # 端のラベルが切れないよう内側へ寄せる
        if mid and not major:
            dr.text((lx, top + 26), "{}°".format(a360), font=font_s, fill=RULER_DIM, anchor="ma")
        if major:
            txt = _CARD[a360]
            tw = dr.textlength(txt, font=font_m)
            dr.rectangle([lx - tw / 2 - 7, top + 42, lx + tw / 2 + 7, top + 80], fill=(22, 24, 34))
            dr.text((lx, top + 61), txt, font=font_m, fill=RULER_FG, anchor="mm")
    right = az0 + span
    right = right if right <= 360.0 else right - 360.0
    for xx, aa, anchor in ((12, az0 % 360, "la"), (W - 12, right, "ra")):
        dr.text((xx, top - 38), "方位 {:03.0f}° {}".format(aa, _CARD.get(int(round(aa)) % 360, "")),
                font=font_b, fill=HDR_FG, anchor=anchor)


# ---------- 人工衛星（既存ツールと同じ TLE + SGP4） ----------
def _satellites(ts, eph, t, lat, lon):
    """重点衛星の alt/az と低軌道の軌道予測。出典は取得できたホスト名を返す。"""
    from skyfield.api import EarthSatellite, wgs84
    from .sky_overlay import _SAT_CATALOG, _fetch_tle
    topos = wgs84.latlon(lat, lon)
    sats, errors, sources = {}, {}, {}
    for nm, catnr, kind in _SAT_CATALOG:
        try:
            tle, src = _fetch_tle(catnr)
            if not tle:
                continue
            sources[nm] = src
            sat = EarthSatellite(tle[0], tle[1], ts=ts)

            def _aa(tt):
                alt, az, _ = (sat.at(tt) - topos.at(tt)).altaz()
                return float(az.degrees), float(alt.degrees)

            az0_, alt0_ = _aa(t)
            trail = []
            if kind == "leo":
                for i in range(-TRAIL_MINUTES, TRAIL_MINUTES + 1, TRAIL_STEP_MIN):
                    a, h = _aa(ts.tt_jd(t.tt + i * TRAIL_STEP_MIN * 60.0 / 86400.0))
                    if h > 0:
                        trail.append({"az": a, "alt": h})
            if alt0_ > 0:
                sats[nm] = {"catnr": catnr, "kind": kind, "az": az0_, "alt": alt0_, "trail": trail}
        except Exception as e:
            errors[nm] = "{}: {}".format(type(e).__name__, str(e)[:100])
    return sats, errors, sources


def _tle_source_summary(sources):
    hosts = sorted({v for v in sources.values() if v})
    if not hosts:
        return "TLE未取得"
    from .celestrak import CELESTRAK_SOURCE
    return "CelesTrak TLE" if hosts == [CELESTRAK_SOURCE] else "TLE出典: {}".format("、".join(hosts))


def draw_satellites(img, sats, span, az0, sky_h, header, font):
    """人工衛星を赤いマーカー＋名札＋軌道予測の破線で描く（色は検証で探すので固定）。"""
    from PIL import ImageDraw
    dr = ImageDraw.Draw(img)
    W = img.size[0]
    pts = {}
    for nm, s in sats.items():
        x = ((s["az"] - az0) % 360.0) / span * W
        y = header + (90.0 - s["alt"]) / 90.0 * sky_h
        pts[nm] = (x, y)
        if s.get("trail"):                                        # 軌道予測（破線＝各区間の6割だけ描く）
            prev = None
            for p in s["trail"]:
                px = ((p["az"] - az0) % 360.0) / span * W
                py = header + (90.0 - p["alt"]) / 90.0 * sky_h
                if prev is not None and abs(px - prev[0]) < W / 4:
                    dr.line([prev[0], prev[1], prev[0] + (px - prev[0]) * 0.6,
                             prev[1] + (py - prev[1]) * 0.6], fill=SAT_TRAIL, width=3)
                prev = (px, py)
    for nm, s in sats.items():
        x, y = pts[nm]
        dr.ellipse([x - 14, y - 14, x + 14, y + 14], fill=(120, 20, 18))
        dr.ellipse([x - 9, y - 9, x + 9, y + 9], fill=SAT_RGB, outline=(255, 255, 255), width=3)
    for nm, s in sats.items():
        x, y = pts[nm]
        dr.text((x + 14, y - 30), nm, font=font, fill=SAT_LABEL,
                stroke_width=3, stroke_fill=(0, 0, 0))
    return pts


def _ground_verify(img_bytes, sats, span, az0, sky_h, header, occl):
    """報告した人工衛星が、報告した方位・高度の画素に実際に描かれているかを検証する。

    描画側と同じ投影（x=方位, y=高度）で画素位置を再計算し、マーカー色（赤）が
    そこにあるかを確かめる。建物に隠れている衛星は対象から外す（隠れて見えないのが
    正しい姿なので、欠落として数えない）。
    """
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    out = {"ok": True, "satellites_reported": len(sats), "markers_at_expected_position": 0,
           "occluded": [], "missing": [],
           "projection": {"kind": "equirectangular", "span_deg": span, "az0_deg": az0,
                          "sky_height_px": sky_h}}
    for nm, s in sats.items():
        x = ((s["az"] - az0) % 360.0) / span * W
        y = header + (90.0 - s["alt"]) / 90.0 * sky_h
        if not (0 <= x < W and 0 <= y < H):
            out["missing"].append([nm, round(x), round(y), -1])
            continue
        if y > float(occl[min(int(x), W - 1)]) - 4.0:
            out["occluded"].append(nm)                 # 街並みの後ろ（描かれていないのが正しい）
            continue
        n = pixel_near(img, (x, y), SAT_RGB, tol=40, r=12)
        if n >= 40:
            out["markers_at_expected_position"] += 1
        else:
            out["missing"].append([nm, round(x), round(y), n])
    if not sats:
        out["note"] = "可視衛星なし（配置検査は対象外）"
    out["ok"] = not out["missing"]
    return out


# ---------- 本体 ----------
def render(scene, *, span, az0, sky_h, seed=20261010, label="", focus=None):
    """地上視点の星空パノラマを描いて (JPEG bytes, 描画情報) を返す。"""
    from PIL import Image, ImageDraw
    cat = scene["catalog"]
    alt, az = scene["star_alt"], scene["star_az"]
    pl, sun = scene["planets"], scene["sun"]

    pxdeg = sky_h / 90.0
    W = int(round(pxdeg * span))
    header, ground_h, ruler = 116, int(pxdeg * 14.0), 100
    H = header + sky_h + ground_h + ruler
    y_horizon = header + sky_h

    sky, st = _sky_background(sky_h, W, span, az0, sun["alt"], sun["az"])
    phase = phase_name(sun["alt"], sun["rising"])
    lz, lh = min(LP["lim_z"], st["lim_z"]), min(LP["lim_h"], st["lim_h"])
    lim_fn = lambda a: lh + (lz - lh) * (a / 90.0) ** 0.5          # その高度で見える限界等級

    lin = np.zeros((H, W, 3), np.float32)                        # 天体の光（線形・加算）
    stats = draw_stars(lin, cat, alt, az, span, az0, sky_h, W, header, lz, lh)
    mw_stats, mw_guide = {}, None
    l, b, alt_g = galactic_lb(sky_h, W, span, az0, scene["lat"], scene["lon"],
                              float(scene["t"].gmst) + scene["lon"] / 15.0)
    k = LP["mw"] * st["mw"]
    if k > 0.001:
        mw, col, grain = milky_way_layer(l, b, alt_g)
        lin[header:y_horizon] += mw[:, :, None] * col * k + grain[:, :, None] * k
        mw_stats = dict(peak=round(float(mw.max()), 4),
                        b_center_mean=round(float(mw[np.abs(b) < 8].mean()), 4),
                        b_high_mean=round(float(mw[np.abs(b) > 45].mean()), 4))
    if st["mw"] < 0.85:                                          # 昼・薄明は銀河面を破線で示す
        mw_guide = mw_guide_points(b, alt_g, sky_h, header)
    planets, labels = draw_planets(lin, pl, span, az0, sky_h, W, header, lim_fn,
                                   sun["az"], sun["alt"],
                                   (lambda n: _in_focus(n, focus)) if focus else None)
    focus_items = []                                             # 主要恒星（名のある星）
    for nm, rah, decd in _named_stars():
        i = _nearest(cat, rah, decd)
        if i is None or cat["vmag"][i] > 2.5 or alt[i] <= 0.8:
            continue
        c = np.atleast_1d(bv_to_rgb(cat["bv"][i]))
        focus_items.append((nm, float(alt[i]), float(az[i]), float(cat["vmag"][i]),
                            (0.45 + 0.55 * float(c[0]), 0.45 + 0.55 * float(c[1]),
                             0.45 + 0.55 * float(c[2])), "star"))
    for (nm, _ra, _de, mag, kind), a_d, z_d in zip(_DSO, scene["dso_alt"], scene["dso_az"]):
        focus_items.append((nm, float(a_d), float(z_d), float(mag), _DSO_COLOR[kind], kind))
    focus_labels = draw_focus_points(lin, focus_items, span, az0, sky_h, W, header, lim_fn,
                                     (lambda n: _in_focus(n, focus)) if focus else None)
    if BLOOM[0] > 0:                                             # 光背（にじみ）
        bl = lin
        for _ in range(BLOOM[1]):
            bl = box_blur(bl, BLOOM[0])
        lin = lin + bl * BLOOM[2]

    lum_c = np.clip(lin.max(-1, keepdims=True), 0.0, None)       # 最大チャンネルで圧縮し、
    disp = (1.0 - np.exp(-lum_c)) * (lin / np.maximum(lum_c, 1e-6))   # 色度は保つ
    bg = np.zeros((H, W, 3), np.float32)
    bg[header:y_horizon] = sky
    img = Image.fromarray((np.clip(1.0 - (1.0 - bg) * (1.0 - disp), 0.0, 1.0) * 255.0
                           + 0.5).astype(np.uint8), "RGB")
    f_h1, f_h2, f_s, f_m, f_b = (load_font(40, True), load_font(24), load_font(19),
                                 load_font(26, True), load_font(24, True))
    sat_pts = {}
    if scene["satellites"]:                                      # 街並みの下に描く（建物が隠す）
        sat_pts = draw_satellites(img, scene["satellites"], span, az0, sky_h, header, f_m)

    day_mix = float(np.clip((sun["alt"] + 4.0) / 10.0, 0.0, 1.0))
    ground, skyline = draw_ground(img, y_horizon, ground_h, W, seed, day_mix)
    img = Image.alpha_composite(img.convert("RGBA"), ground).convert("RGB")
    occl = np.full(W, float(y_horizon), np.float32)              # 列ごとの建物の高さ
    for x0, x1, top in skyline:
        xa, xb = max(0, int(x0)), min(W, int(x1))
        if xb > xa:
            occl[xa:xb] = np.minimum(occl[xa:xb], float(top))

    def _hidden(x, y):
        return y > float(occl[min(max(int(x), 0), W - 1)]) - 4.0

    dr = ImageDraw.Draw(img)
    if mw_guide:                                                 # 天の川（銀河面）の位置ガイド
        mixv = float(np.clip(st["mw"], 0.0, 1.0))
        lc = np.array([205, 218, 244], np.float32) * mixv + np.array([34, 42, 70], np.float32) * (1 - mixv)
        for j, pts in enumerate(mw_guide):
            col = tuple(int(v) for v in (lc if j == 0 else lc * 0.45))
            prev = None
            for p in pts:
                if p is None:
                    prev = None
                    continue
                on = (int(p[0]) % 16) < 9 if j == 0 else (int(p[0]) % 22) < 8
                if prev is not None and on:
                    dr.line([prev[0], prev[1], p[0], p[1]], fill=col, width=2 if j == 0 else 1)
                prev = p
        for p in mw_guide[0]:
            if p and 60 < p[0] < W - 60:
                dr.text((p[0] + 10, p[1] - 32), "天の川（銀河面）", font=f_s,
                        fill=tuple(int(v) for v in lc),
                        stroke_width=3, stroke_fill=(255, 255, 255) if mixv < 0.5 else (0, 0, 0))
                break
    for nm, x, y, vis in labels:                                 # 月・惑星の名札
        if x < 10 or x > W - 10 or _hidden(x, y):
            continue
        dr.text((x + 12, y - 22), nm, font=f_m,
                fill=(255, 250, 214) if vis else (198, 210, 234),
                stroke_width=3, stroke_fill=(0, 0, 0))
    for nm, x, y, vis, kind in focus_labels:                     # 主要恒星・銀河の名札
        if x < 10 or x > W - 10 or _hidden(x, y):
            continue
        col = (216, 226, 252) if kind == "star" else _DSO_COLOR[kind]
        if not vis:
            col = tuple(int(v * 0.78) for v in col)
        dr.text((x + 11, y + 8), nm, font=f_s, fill=col,
                stroke_width=3, stroke_fill=(0, 0, 0))

    jst = _dt.datetime(*[int(v) for v in scene["when_utc"][:5]]) + _dt.timedelta(hours=9)
    dr.rectangle([0, 0, W, header], fill=HDR_BG)
    dr.text((18, 8), "地上から見上げた星空", font=f_h1, fill=HDR_FG)
    dr.text((18, 58),
            "{} JST ／ 緯度{:.2f}° 経度{:.2f}° ／ 視野 {:.0f}°×90° ／ {}".format(
                jst.strftime("%Y-%m-%d %H:%M"), scene["lat"], scene["lon"], span, phase),
            font=f_h2, fill=HDR_DIM)
    dr.text((W - 18, 10), "恒星: {}".format(cat["source"]), font=f_h2, fill=HDR_DIM, anchor="ra")
    dr.text((W - 18, 40), "暦: JPL de421 + Skyfield ／ 投影: 正距円筒{}".format(
        " ／ 光害: " + LP["name"] if st["mw"] > 0.3 else ""), font=f_h2, fill=HDR_DIM, anchor="ra")
    if label:
        dr.text((W - 18, 70), label, font=f_h2, fill=(120, 210, 165), anchor="ra")
    dr.text((18, 88), "● 見えている天体 ／ ○ 今は見えないが見える位置（惑星・主要恒星・銀河）"
                      " ／ 背景の星は実際の見え方（淡い星は減光）",
            font=f_s, fill=(128, 140, 166))
    draw_scale(dr, W, H, H - ruler, span, az0, f_s, f_m, f_b)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93, subsampling=0, optimize=True)
    info = dict(size=[W, H], sky_h=sky_h, header=header, ground_h=ground_h, ruler=ruler,
                y_horizon=y_horizon, phase=phase, sun=scene["sun"], planets=planets,
                star_stats=stats, milky_way=mw_stats, buildings=len(skyline), sky_state=st,
                focus=dict(visible=[n for n, _x, _y, v, _k in focus_labels if v],
                           position_only=[n for n, _x, _y, v, _k in focus_labels if not v]),
                occl=occl, focus_labels=focus_labels, labels=labels, satellite_px=sat_pts)
    return buf.getvalue(), info


def _in_focus(name, want):
    """依頼表示（show）の一致判定。完全一致のほか前方一致・部分一致も許す（例: M31）。"""
    return want is None or any(w == name or name.startswith(w) or w in name for w in want)


def _named_stars():
    from .sky_overlay import _BRIGHT_STARS
    return _BRIGHT_STARS


def _nearest(cat, rah, decd):
    d = ((cat["ra_deg"] / 15.0 - rah) * 15.0 * math.cos(math.radians(decd))) ** 2 \
        + (cat["dec_deg"] - decd) ** 2
    i = int(np.argmin(d))
    return i if d[i] < 0.5 ** 2 else None


def sky_ground(place=None, lat=None, lon=None, when=None, span=360.0, az0=0.0,
               lp="suburb", show=None):
    """地上から見上げた星空のパノラマ（正距円筒・方位-高度）を返す。

    例:「東京の空を地上から見上げた図」「今見える惑星と人工衛星」「昼の空の惑星の位置」
    恒星は Hipparcos 実星表（Vmag<8.5）を Skyfield で計算、天の川は銀経・銀緯から描画、
    人工衛星は CelesTrak TLE + SGP4（すべて認証不要）。

    Args:
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        when: 観測時刻 ISO8601（例 "2026-10-10T12:00:00Z"）。省略時は現在時刻。
        span: 横方向の視野（度, 30〜360）。360 で全天。
        az0: 左端の方位（度）。span=180, az0=225 なら南西→北東。
        lp: 光害の程度 "city"（都心）/ "suburb"（郊外, 既定）/ "dark"（山間・離島）。
        show: 依頼表示する天体名（カンマ区切り。例 "木星,ベガ,M31"）。省略時は全部。
    """
    from .sky_overlay import _resolve_place
    span = as_float(span, 360.0, 30.0, 360.0) or 360.0
    az0 = as_float(az0, 0.0, -360.0, 360.0) or 0.0
    focus = [f.strip() for f in str(show).split(",") if f.strip()] if show else None
    lpinfo = _set_lp(lp)

    ll = _resolve_place(place, lat, lon)
    if ll is None and (lat is not None or lon is not None):
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="lat（-90〜90）と lon（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "lat": str(lat), "lon": str(lon)})
    if ll is None:
        ll = (35.68, 139.69)                                     # 東京
    when_utc = _parse_when(when)
    if when_utc == "bad":
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="when は ISO8601（例 2026-10-10T12:00:00Z）で指定してください。")],
            structuredContent={"error": "bad when", "when": str(when)})
    lat_v, lon_v = float(ll[0]), float(ll[1])

    cat = _star_catalog()
    ts, eph = _load_ephemeris()
    from skyfield.api import Star, wgs84
    from skyfield import almanac
    t = ts.utc(*when_utc)
    topos = wgs84.latlon(lat_v, lon_v)
    where = (eph["earth"] + topos).at(t)
    try:
        app = where.observe(Star(ra_hours=cat["ra_deg"] / 15.0, dec_degrees=cat["dec_deg"])).apparent()
        star_alt, star_az, _ = app.altaz()
        star_alt, star_az = star_alt.degrees, star_az.degrees
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="恒星位置の計算に失敗しました: " + str(e)[:150])],
            structuredContent={"error": str(e)[:200]})

    pl = {}
    for nm, key in (("太陽", "sun"), ("月", "moon"), ("水星", "mercury"), ("金星", "venus"),
                    ("火星", "mars"), ("木星", "jupiter barycenter"),
                    ("土星", "saturn barycenter"), ("天王星", "uranus barycenter"),
                    ("海王星", "neptune barycenter")):
        p = where.observe(eph[key]).apparent()
        a, z, _ = p.altaz()
        pl[nm] = dict(alt=float(a.degrees), az=float(z.degrees))
        if nm == "月":
            pl[nm]["dist_km"] = float(p.distance().km)
            pl[nm]["illum"] = float(almanac.fraction_illuminated(eph, "moon", t))
    sun = _sun_state(eph, ts, t, lat_v, lon_v)
    sats, sat_errors, tle_sources = _satellites(ts, eph, t, lat_v, lon_v)
    dso_a, dso_z = dso_altaz(ts, eph, when_utc, lat_v, lon_v)
    tstr = t.utc_strftime("%Y-%m-%d %H:%M UTC")

    sky_h = int(min(1000, 3400 * 90.0 / max(30.0, span)))
    scene = dict(catalog=cat, star_alt=star_alt, star_az=star_az, planets=pl, sun=sun,
                 satellites=sats, dso_alt=dso_a, dso_az=dso_z, lat=lat_v, lon=lon_v,
                 t=t, when_utc=when_utc)
    try:
        img_bytes, info = render(scene, span=span, az0=az0, sky_h=sky_h, focus=focus)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="画像生成に失敗しました: {}".format(str(e)[:150]))],
            structuredContent={"error": str(e)[:200]})

    out_path = save_output(img_bytes, "sky_map_with_satellites", "jpg")
    verify = _ground_verify(img_bytes, sats, span, az0, sky_h, info["header"], info["occl"])
    phase = info["phase"]

    lines = [media_link_line("星空マップ・地上視点パノラマ", path=out_path, kind="figure"),
             "🗺️ **{} の空（地上視点・{}）**".format(tstr, phase),
             "場所: 緯度 {:.2f}° 経度 {:.2f}° ／ 視野 {:.0f}°×90°（左端 方位 {:.0f}°）".format(
                 lat_v, lon_v, span, az0 % 360),
             "**時間帯**: {}（太陽高度 {:.1f}°・方位 {:.0f}°）".format(phase, sun["alt"], sun["az"]),
             "**恒星**: {}（{:,} 星／地平線上 {:,} 星／この視野に {:,} 星を描画）".format(
                 cat["source"], info["star_stats"]["stars_total"], info["star_stats"]["stars_up"],
                 info["star_stats"]["stars_drawn"]),
             ""]
    vis_pl = [n for n, v in info["planets"].items() if v["visible"]]
    pos_pl = [n for n, v in info["planets"].items() if not v["visible"]]
    lines.append("**見えている天体**: " + (", ".join(vis_pl) if vis_pl else "なし"))
    if info["focus"]["visible"]:
        lines.append("**見えている主要恒星・銀河**: " + ", ".join(info["focus"]["visible"]))
    if pos_pl or info["focus"]["position_only"]:
        lines.append("**今は見えないが見える位置（○）**: "
                     + ", ".join(pos_pl + info["focus"]["position_only"]))
    lines.append("**見えている人工衛星**: " + (", ".join(sats) if sats else "なし(地平線下)"))
    for nm, s in sats.items():
        lines.append("- {}: 方位 {:.0f}° 仰角 {:.0f}°".format(nm, s["az"], s["alt"]))
    for nm, msg in sat_errors.items():
        lines.append("- ⚠️ {}: 位置を計算できませんでした（{}）".format(nm, msg))
    if info["milky_way"]:
        lines.append("- 天の川: 銀河面の帯を描画（{:.0f}°×90° の視野内）".format(span))
    elif info["star_stats"]["stars_drawn"]:
        lines.append("- 天の川: この時間帯は太陽が高く、銀河面の位置のみ破線で表示")

    fig = figure_payload(
        kind="sky_view",
        title="{} の空（地上視点・惑星と人工衛星）".format(tstr),
        view=view_spec("local_sky", "altaz",
                       "観測地から見上げた空（方位-高度の正距円筒）に恒星・惑星・天の川・人工衛星を重ねた図",
                       why="地上から見上げた見え方（方位と高度）をそのまま示す投影だから"),
        primary=primary_spec("地球", "observer",
                             note="観測地から見た空の図。地球や軌道の形は描いていない"),
        scale=scale_spec("linear", to_scale=True, unit="deg",
                         exaggerated=["惑星・人工衛星のマーカー（実寸ではない）",
                                      "月の円盤（実際の視直径より大きい）"]),
        markers=([{"id": nm, "label": nm, "kind": "planet", "color": "#{:02x}{:02x}{:02x}".format(
            *[int(round(c * 255)) for c in _PLANET_COLOR.get(nm, (1, 1, 1))]),
            "az_deg": round(v["az"], 1), "alt_deg": round(v["alt"], 1), "visible": v["visible"]}
            for nm, v in info["planets"].items()]
            + [{"id": nm, "label": nm, "kind": "satellite", "color": "#ff3b30",
                "az_deg": round(v["az"], 1), "alt_deg": round(v["alt"], 1)}
               for nm, v in list(sats.items())[:8]]),
        notes=figure_notes(extra=[
            "この図は観測地の空（方位・高度）の見かけの位置。地平線より下の天体は描かれない",
            "恒星は{}の実測位置。等級・色（B-V）・大気減光を反映し、淡い星ほど減光している".format(
                cat["source"]),
            "時間帯は太陽高度で決まる（現在 {}・高度 {:.1f}°）。昼や薄明は空が明るく、見えない天体は"
            "○の輪郭で位置だけを示す".format(phase, sun["alt"]),
            "天の川は銀経・銀緯から面輝度を近似した描画（実写ではない）。"
            "昼・薄明は銀河面を破線で示す",
            "月は位相（照度 {:.0f}%）と距離を反映し、明るい側が太陽の方向を向く。"
            "円盤は実際の視直径より大きい（マーカーとしての誇張）".format(
                float(pl["月"].get("illum", 0.0)) * 100),
            "惑星の代表等級は固定値の近似（実際の等級変動は反映しない）",
            ("人工衛星は最新TLEをSGP4で伝播したその時刻の位置（予報ではない）。"
             "低軌道衛星 {} の可視区間（前後{}分・{}分刻み）を破線で描く".format(
                 ", ".join(k for k, v in sats.items() if v.get("trail")),
                 TRAIL_MINUTES, TRAIL_STEP_MIN)
             if any(v.get("trail") for v in sats.values()) else
             "人工衛星は最新TLEをSGP4で伝播したその時刻の位置（予報ではない）。"
             "軌道予測の破線を描ける低軌道衛星がこの時刻は地平線上にないため、破線は描かれていない"
             "（静止衛星は動かないので破線を描かない）"),
            "街並みは手続き生成（実際の都市景観ではない）。建物が低空の天体を隠す",
            "光害の設定: {}（限界等級 {:.1f}〜{:.1f} 等）".format(lpinfo["name"], lh_of(info, "h"),
                                                                  lh_of(info, "z")),
        ]),
        caption="{:.2f}°N {:.2f}°E の {}（{}）の空。惑星: {} ／ 人工衛星: {}".format(
            lat_v, lon_v, tstr, phase,
            ", ".join(info["planets"]) or "なし", ", ".join(sats) or "なし"),
        verify=verify,
    )
    lines.append("")
    lines.append(figure_text_block(fig))
    lines.append("画像は上に表示（base64 JPEG）。出典: JPL de421 + Skyfield ／ {} ／ {}".format(
        cat["source"], _tle_source_summary(tle_sources)))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)),
                 ImageContent(type="image", data=base64.b64encode(img_bytes).decode("ascii"),
                              mimeType="image/jpeg",
                              altText="地上から見上げた空（正距円筒）に恒星・惑星・天の川・人工衛星を重ねた図")],
        structuredContent={"time_utc": tstr, "lat": lat_v, "lon": lon_v, "span": span, "az0": az0,
                           "engine": "ground (Pillow, 地上視点パノラマ)", "phase": phase,
                           "sun": {k: (round(v, 1) if k != "rising" else v) for k, v in sun.items()},
                           "planets": info["planets"], "stars": info["focus"],
                           "star_catalog": {"source": cat["source"], "stars": info["star_stats"]["stars_total"],
                                            "fallback": bool(cat.get("fallback")),
                                            "drawn": info["star_stats"]["stars_drawn"]},
                           "satellites": sats, "satellite_errors": sat_errors,
                           "milky_way": info["milky_way"], "light_pollution": lpinfo,
                           "figure": fig, "image_path": out_path, "tle_sources": tle_sources,
                           "source": "JPL de421+Skyfield / {} / {}".format(
                               cat["source"], _tle_source_summary(tle_sources))})


def lh_of(info, which):
    """図の注記用に、実際に使った限界等級（天頂/地平線）を返す。"""
    st = info.get("sky_state") or {}
    v = st.get("lim_z" if which == "z" else "lim_h", 0.0)
    return float(min(LP["lim_z" if which == "z" else "lim_h"], v))

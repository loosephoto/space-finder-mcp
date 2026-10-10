#!/usr/bin/env python3
"""試作: 「地上から見上げた星空」パノラマ（実星表 + 天の川 + 街並み + 方位目盛り）。

既存の sky_map_with_satellites(engine=simple/accurate) は天頂中心の円形図で、
恒星は16個の固定リスト（`_BRIGHT_STARS`）だけだった。この試作は表示方法を
次のように変える案の見本:

  - 投影: 地平線が下・方位が横軸の正距円筒パノラマ（魚眼ではない）
  - 星  : Hipparcos 実星表 (Vmag<8.5, 61,221星) を Skyfield で alt/az に
  - 見た目: B-V→色温度→星色 / 等級→芯の大きさと輝度 / 大気減光 (Kasten-Young)
           / 明るい星は光背（ブルーム）と淡いスパイク / 地平線付近は光害で淡い星が消える
  - 天の川: 各画素の銀経・銀緯から面輝度をモデル化（銀河中心側で明るく、暗黒帯あり）
  - 下部: 手続き生成の街並み（実写不要・建物が低空の星を隠す）
  - 左右/下部: 方位目盛り（5°刻み、30°ごとに数値、45°ごとに漢字）

使い方:
  python scripts/prototype_ground_sky.py --span 180 --out sample_180.jpg
  python scripts/prototype_ground_sky.py --span 360 --sky-h 820 --out sample_360.jpg
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import math
import os
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
from space_finder_mcp.img_common import load_font, save_output  # noqa: E402  (CJKフォント共通ヘルパー)

_LOCAL = os.environ.get("LOCALAPPDATA", ".")
CACHE_DIR = os.path.join(_LOCAL, "Temp", "space_finder_mcp")
CATALOG_PATH = os.path.join(CACHE_DIR, "stars_hip85.csv")
SKYFIELD_DIR = os.path.join(_LOCAL, "Temp", "skyfield_data")

# ---------- 見た目の指定（1か所にまとめる。描画と説明が食い違わないため） ----------
ZENITH = np.array([5, 7, 17], np.float32) / 255.0        # 天頂の空色（ほぼ黒）
HORIZON = np.array([20, 19, 27], np.float32) / 255.0     # 地平線の素の空色
POLLUTION = np.array([62, 42, 26], np.float32) / 255.0   # 街あかり（暖色）
AIRGLOW = np.array([10, 20, 18], np.float32) / 255.0     # 大気光（淡い緑）
AZ_CITY = 170.0                                          # 街あかりが最も強い方位（南＝都心方向）
CITY = (7, 8, 13)                                        # 建物のシルエット
CITY_FAR = (22, 19, 23)                                  # 奥の建物（霞んで一段明るい＝大気遠近）
CITY_TOP = (26, 28, 38)                                  # 建物の輪郭
WINDOW = (255, 214, 138)                                 # 窓あかり
LAMP = (255, 196, 120)                                   # 街灯
SIGN = ((220, 70, 70), (90, 170, 255), (110, 220, 150), (255, 190, 90))
RULER_BG = (10, 11, 17)
RULER_FG = (200, 209, 232)
RULER_DIM = (110, 120, 146)
HDR_BG = (6, 7, 12)
HDR_FG = (234, 240, 252)
HDR_DIM = (152, 164, 192)

GAIN = 0.105           # 星の明るさの基準（mag 6.5 の星が淡い点になる程度）
BG_DIM = 0.55          # 背景の星の減光（淡い星ほど落として、注目天体を目立たせる）
BLDG_H = 0.50          # 建物の高さの倍率（1.0=従来、0.5=半分。低くすると低空の星が見える）
FOCUS_BOOST = 1.30     # 注目天体（惑星・主要恒星・銀河）のマーカーの強調

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
EXT_K = 0.22           # 大気減光係数 (mag/airmass, Vバンド)
SAT_MAG = 1.10         # これより明るい星は芯を大きく＋スパイクを描く
BLOOM = (2, 2, 0.55)   # 光背: ぼかし半径2px×2回、元画像へ 0.55 倍で加算

# 光害の程度。限界等級・天の川の見え方・空の明るさをまとめて切り替える
# （同じ観測地でも「都心の空」と「山間の空」は別物なので、そこを1つの軸にする）
LP_MODES = {
    "city":   dict(lim_z=4.6, lim_h=3.2, mw=0.012, poll=2.00, name="都心（光害 強）"),
    "suburb": dict(lim_z=7.2, lim_h=4.8, mw=0.065, poll=0.95, name="郊外（光害 中）"),
    "dark":   dict(lim_z=8.6, lim_h=5.4, mw=0.105, poll=0.45, name="山間・離島（光害 弱）"),
}
LP = dict(LP_MODES["suburb"])


def set_lp(mode):
    global LP
    LP = dict(LP_MODES.get(mode, LP_MODES["suburb"]))
    return LP


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


def load_catalog(path=CATALOG_PATH):
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


def load_ephemeris():
    from skyfield.api import Loader
    load = Loader(SKYFIELD_DIR, verbose=False)
    return load, load.timescale(), load("de421.bsp")


def star_altaz(cat, ts, eph, when_utc, lat, lon):
    """実星表を Skyfield でベクトル計算（暦は既存ツールと同じ JPL de421）。"""
    from skyfield.api import Star, wgs84
    t = ts.utc(*when_utc)
    topos = wgs84.latlon(lat, lon)
    pos = Star(ra_hours=cat["ra_deg"] / 15.0, dec_degrees=cat["dec_deg"])
    app = (eph["earth"] + topos).at(t).observe(pos).apparent()
    alt, az, _ = app.altaz()
    return alt.degrees, az.degrees, t


def planet_altaz(eph, t, lat, lon):
    """太陽系天体（月・惑星）の alt/az と月の照度。"""
    from skyfield.api import wgs84
    from skyfield import almanac
    where = (eph["earth"] + wgs84.latlon(lat, lon)).at(t)
    out = {}
    for nm, key in (("太陽", "sun"), ("月", "moon"), ("水星", "mercury"), ("金星", "venus"), ("火星", "mars"),
                    ("木星", "jupiter barycenter"), ("土星", "saturn barycenter"),
                    ("天王星", "uranus barycenter"), ("海王星", "neptune barycenter")):
        app = where.observe(eph[key]).apparent()
        alt, az, _ = app.altaz()
        out[nm] = dict(alt=float(alt.degrees), az=float(az.degrees))
        if nm == "月":
            out[nm]["dist_km"] = float(app.distance().km)     # 見かけの大きさに使う
    out["月"]["illum"] = float(almanac.fraction_illuminated(eph, "moon", t))
    return out


def sky_background(sky_h, W, span, az0):
    """空の地色（天頂→地平線のグラデ＋光害ドーム＋大気光）。行=高度、列=方位。"""
    rows = np.arange(sky_h, dtype=np.float32)
    alt = 90.0 * (1.0 - rows / float(sky_h))                     # 上=90°, 下=0°
    t = (alt / 90.0)[:, None, None]
    base = ZENITH[None, None, :] + (HORIZON - ZENITH)[None, None, :] * (1.0 - t) ** 2.0
    sky = (base + POLLUTION[None, None, :] * np.exp(-alt / 13.0)[:, None, None] * 0.75 * LP["poll"]
           + AIRGLOW[None, None, :] * np.exp(-alt / 26.0)[:, None, None])
    az_cols = az0 + (np.arange(W, dtype=np.float32) + 0.5) / W * span
    daz = np.abs(((az_cols - AZ_CITY + 180.0) % 360.0) - 180.0)
    sky = sky + POLLUTION[None, None, :] * 1.5 * LP["poll"] * np.exp(-(daz / 78.0) ** 2)[None, :, None] \
        * np.exp(-alt / 10.5)[:, None, None]
    return np.clip(sky, 0.0, 1.0)


# ---------- 時間帯（昼・夕方・薄明・夜）で変わる空 ----------
# 太陽高度で線形補間する。値は (太陽高度, 天頂色, 反対側の地平線色, 太陽側の焼け色, 焼けの強さ,
# 天頂限界等級, 地平線限界等級, 天の川の見え方)。限界等級 99 は「光害モードの値を使う」の意味。
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
CITY_DAY = (54, 58, 66)          # 昼の建物（明るい空に対するシルエット）
CITY_FAR_DAY = (86, 92, 102)
CITY_TOP_DAY = (108, 114, 124)


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


def sun_state(eph, ts, t, lat, lon):
    """太陽の高度・方位と、昇っているか沈んでいるか（15分後と比べる）。"""
    from skyfield.api import wgs84
    where = eph["earth"] + wgs84.latlon(lat, lon)
    alt0, az0, _ = where.at(t).observe(eph["sun"]).apparent().altaz()
    alt1, _, _ = where.at(ts.tt_jd(t.tt + 15.0 / 1440.0)).observe(eph["sun"]).apparent().altaz()
    return dict(alt=float(alt0.degrees), az=float(az0.degrees), rising=bool(alt1.degrees > alt0.degrees))


def sky_background_tod(sky_h, W, span, az0, sun_alt, sun_az):
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


def draw_stars(lin, cat, alt, az, span, az0, sky_h, W, header, lim_z=None, lim_h=None):
    """恒星を「加算光」として lin（線形RGB）に積む。等級・色・減光を反映。"""
    alt = np.asarray(alt, np.float64); az = np.asarray(az, np.float64)
    up = alt > 0.4
    x_all = ((az - az0) % 360.0) / span * W
    keep = up & (x_all >= -20) & (x_all <= W + 20)
    idx = np.nonzero(keep)[0]
    x, alt, m0, bv = x_all[idx], alt[idx], cat["vmag"][idx].astype(np.float64), cat["bv"][idx]

    m = m0 + EXT_K * (airmass(alt) - 1.0)                        # 大気減光
    lz = LP["lim_z"] if lim_z is None else lim_z
    lh = LP["lim_h"] if lim_h is None else lim_h
    lim = lh + (lz - lh) * (alt / 90.0) ** 0.5
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


_PLANET_COLOR = {"太陽": (1.00, 0.97, 0.88), "月": (0.95, 0.95, 0.96), "水星": (0.80, 0.78, 0.74), "金星": (1.00, 0.94, 0.78),
                 "火星": (1.00, 0.62, 0.45), "木星": (1.00, 0.93, 0.80), "土星": (0.96, 0.90, 0.70),
                 "天王星": (0.70, 0.90, 0.95), "海王星": (0.55, 0.70, 0.98)}
# 見えるかどうかの判定に使う代表的な実効等級（実際の等級は時々で変わる近似値）
PLANET_MAG = {"太陽": -26.7, "月": -12.7, "水星": -0.5, "金星": -4.4, "火星": 0.5,
              "木星": -2.5, "土星": 0.7, "天王星": 5.7, "海王星": 7.8}


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
    欠けの形は位相角（照度）から、明るい側の向きは太陽の方向から決まる。"""
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


def draw_planets(lin, pl, span, az0, sky_h, W, header, lim_at=None, sun_az=0.0, in_focus=None,
                 sun_alt=0.0):
    """太陽系天体を描く。空が明るくて見えなくても「位置」は示す（白い輪郭マーカー）。
    見えている天体は実物に近い色の点＋リング。月は位相を反映。focus 指定時はその天体だけ。"""
    drawn, labels = {}, []
    for nm, v in pl.items():
        if v["alt"] <= (-1.5 if nm == "太陽" else 0.6):
            continue
        x = ((v["az"] - az0) % 360.0) / span * W
        if x < -30 or x > W + 30:
            continue
        y = header + (90.0 - v["alt"]) / 90.0 * sky_h
        rgb = np.array(_PLANET_COLOR.get(nm, (1, 1, 1)), np.float32)
        vis = True if lim_at is None else (PLANET_MAG.get(nm, 5.0) < lim_at(float(v["alt"])))
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
    見えている天体は色付きの点＋リング、今は見えない天体は輪郭だけ（位置ガイド）。"""
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


_CARD = {0: "北", 45: "北東", 90: "東", 135: "南東", 180: "南", 225: "南西", 270: "西", 315: "北西"}


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


def render(lat, lon, when_utc, az0, span, sky_h=1000, out=None, seed=20261010, label="",
           milky_way=True, lp="suburb", focus=None):
    from PIL import Image, ImageDraw
    lpinfo = set_lp(lp)
    focus = [f.strip() for f in focus.split(",") if f.strip()] if focus else None

    def _in_focus(name, want):
        return want is None or any(w == name or name.startswith(w) or w in name for w in want)
    cat = load_catalog()
    _, ts, eph = load_ephemeris()
    alt, az, t = star_altaz(cat, ts, eph, when_utc, lat, lon)
    pl = planet_altaz(eph, t, lat, lon)

    pxdeg = sky_h / 90.0
    W = int(round(pxdeg * span))
    header, ground_h, ruler = 116, int(pxdeg * 14.0), 100
    H = header + sky_h + ground_h + ruler
    y_horizon = header + sky_h

    sun = sun_state(eph, ts, t, lat, lon)                        # 太陽高度で空の色が決まる
    sky, st = sky_background_tod(sky_h, W, span, az0, sun["alt"], sun["az"])
    phase = phase_name(sun["alt"], sun["rising"])
    lin = np.zeros((H, W, 3), np.float32)                        # 星の光（線形・加算）
    lz, lh = min(LP["lim_z"], st["lim_z"]), min(LP["lim_h"], st["lim_h"])
    lim_fn = lambda a: lh + (lz - lh) * (a / 90.0) ** 0.5          # その高度で見える限界等級
    stats = draw_stars(lin, cat, alt, az, span, az0, sky_h, W, header, lz, lh)
    mw_stats, mw_guide = {}, None
    if milky_way:                                                # 天の川（銀経・銀緯から）
        l, b, alt_g = galactic_lb(sky_h, W, span, az0, lat, lon, float(t.gmst) + lon / 15.0)
        k = LP["mw"] * st["mw"]
        if k > 0.001:
            mw, col, grain = milky_way_layer(l, b, alt_g)
            lin[header:y_horizon] += mw[:, :, None] * col * k + grain[:, :, None] * k
            mw_stats = dict(peak=round(float(mw.max()), 4),
                            b_center_mean=round(float(mw[np.abs(b) < 8].mean()), 4),
                            b_high_mean=round(float(mw[np.abs(b) > 45].mean()), 4))
        if st["mw"] < 0.85:                                      # 昼・薄明は銀河面を破線で示す
            mw_guide = mw_guide_points(b, alt_g, sky_h, header)
    planets, labels = draw_planets(lin, pl, span, az0, sky_h, W, header, lim_fn, sun["az"],
                                   (lambda n: _in_focus(n, focus)) if focus else None, sun["alt"])
    focus_items = []                                             # 主要恒星（名のある星）
    for nm, rah, decd in _named_stars():
        i = _nearest(cat, rah, decd)
        if i is None or cat["vmag"][i] > 2.5 or alt[i] <= 0.8:
            continue
        c = np.atleast_1d(bv_to_rgb(cat["bv"][i]))
        focus_items.append((nm, float(alt[i]), float(az[i]), float(cat["vmag"][i]),
                            (0.45 + 0.55 * float(c[0]), 0.45 + 0.55 * float(c[1]),
                             0.45 + 0.55 * float(c[2])), "star"))
    dso_a, dso_z = dso_altaz(ts, eph, when_utc, lat, lon)        # 銀河・星団・星雲
    for (nm, _ra, _de, mag, kind), a_d, z_d in zip(_DSO, dso_a, dso_z):
        focus_items.append((nm, float(a_d), float(z_d), float(mag), _DSO_COLOR[kind], kind))
    focus_labels = draw_focus_points(lin, focus_items, span, az0, sky_h, W, header, lim_fn,
                                     (lambda n: _in_focus(n, focus)) if focus else None)
    if BLOOM[0] > 0:                                             # 光背（にじみ）
        bl = lin
        for _ in range(BLOOM[1]):
            bl = box_blur(bl, BLOOM[0])
        lin = lin + bl * BLOOM[2]

    lum_c = np.clip(lin.max(-1, keepdims=True), 0.0, None)       # 最大チャンネルで圧縮し、
    disp = (1.0 - np.exp(-lum_c)) * (lin / np.maximum(lum_c, 1e-6))   # 色度は保つ（白飛びでも色が残る）
    bg = np.zeros((H, W, 3), np.float32)
    bg[header:y_horizon] = sky
    img = Image.fromarray((np.clip(1.0 - (1.0 - bg) * (1.0 - disp), 0.0, 1.0) * 255.0
                           + 0.5).astype(np.uint8), "RGB")

    day_mix = float(np.clip((sun["alt"] + 4.0) / 10.0, 0.0, 1.0))
    ground, skyline = draw_ground(img, y_horizon, ground_h, W, seed, day_mix)
    img = Image.alpha_composite(img.convert("RGBA"), ground).convert("RGB")
    occl = np.full(W, float(y_horizon), np.float32)              # 列ごとの建物の高さ（名札用）
    for x0, x1, top in skyline:
        xa, xb = max(0, int(x0)), min(W, int(x1))
        if xb > xa:
            occl[xa:xb] = np.minimum(occl[xa:xb], float(top))

    def _hidden(x, y):
        return y > float(occl[min(max(int(x), 0), W - 1)]) - 4.0

    dr = ImageDraw.Draw(img)
    f_h1, f_h2, f_s, f_m, f_b = (load_font(40, True), load_font(24), load_font(19),
                                 load_font(26, True), load_font(24, True))
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
    dr.rectangle([0, 0, W, header], fill=HDR_BG)
    dr.text((18, 8), "地上から見上げた星空", font=f_h1, fill=HDR_FG)
    jst = _dt.datetime(*[int(v) for v in when_utc[:5]]) + _dt.timedelta(hours=9)
    dr.text((18, 58),
            "{} JST ／ 緯度{:.2f}° 経度{:.2f}° ／ 視野 {:.0f}°×90° ／ {}".format(
                jst.strftime("%Y-%m-%d %H:%M"), lat, lon, span, phase), font=f_h2, fill=HDR_DIM)
    dr.text((W - 18, 10), "恒星: Hipparcos 実星表（Vmag<8.5, {:,}星）".format(stats["stars_total"]),
            font=f_h2, fill=HDR_DIM, anchor="ra")
    dr.text((W - 18, 40), "暦: JPL de421 + Skyfield ／ 投影: 正距円筒{}".format(
        " ／ 光害: " + lpinfo["name"] if st["mw"] > 0.3 else ""),
            font=f_h2, fill=HDR_DIM, anchor="ra")
    if label:
        dr.text((W - 18, 70), label, font=f_h2, fill=(120, 210, 165), anchor="ra")
    dr.text((18, 88), "● 見えている天体 ／ ○ 今は見えないが見える位置（惑星・主要恒星・銀河）"
                      " ／ 背景の星は実際の見え方（淡い星は減光）",
            font=f_s, fill=(128, 140, 166))

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

    draw_scale(dr, W, H, H - ruler, span, az0, f_s, f_m, f_b)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93, subsampling=0, optimize=True)
    data = buf.getvalue()
    if out:
        with open(out, "wb") as f:
            f.write(data)
        path = os.path.abspath(out)
    else:
        path = save_output(data, "prototype_ground_sky", "jpg")
    info = dict(lat=lat, lon=lon, when_utc=list(when_utc), az0=az0, span=span, size=[W, H],
                sky_h=sky_h, header=header, ground_h=ground_h, ruler=ruler, y_horizon=y_horizon,
                planets=planets, star_stats=stats, milky_way=mw_stats, buildings=len(skyline),
                focus=dict(visible=[n for n, _x, _y, v, _k in focus_labels if v],
                           position_only=[n for n, _x, _y, v, _k in focus_labels if not v]),
                light_pollution=lpinfo, phase=phase, jst=jst.strftime("%Y-%m-%d %H:%M"),
                sun={k: (round(v, 1) if k != "rising" else v) for k, v in sun.items()},
                max_building_px=max((y_horizon - top for _, _, top in skyline), default=0),
                image_path=path, bytes=len(data))
    return img, info


def _named_stars():
    """既存ツールと同じ「名のある明るい星」リスト（名札用）。"""
    from space_finder_mcp.sky_overlay import _BRIGHT_STARS
    return _BRIGHT_STARS


def _nearest(cat, rah, decd):
    d = ((cat["ra_deg"] / 15.0 - rah) * 15.0 * math.cos(math.radians(decd))) ** 2 \
        + (cat["dec_deg"] - decd) ** 2
    i = int(np.argmin(d))
    return i if d[i] < 0.5 ** 2 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=35.68)
    ap.add_argument("--lon", type=float, default=139.69)
    ap.add_argument("--when", default="2026-10-10 12:00", help="UTC 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--az0", type=float, default=225.0)
    ap.add_argument("--span", type=float, default=180.0)
    ap.add_argument("--sky-h", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--info", default=None, help="描画条件のJSONを書き出す")
    ap.add_argument("--no-mw", action="store_true", help="天の川の層を描かない（比較検証用）")
    ap.add_argument("--lp", default="suburb", choices=["city", "suburb", "dark"],
                    help="光害の程度（限界等級・天の川の見え方・空の明るさ）")
    ap.add_argument("--show", default=None,
                    help="依頼表示する天体名（カンマ区切り。例 木星,ベガ,M31）。省略時は全部")
    a = ap.parse_args()
    y, mo, d = (int(v) for v in a.when.split()[0].split("-"))
    hh, mi = (int(v) for v in a.when.split()[1].split(":"))
    t0 = time.time()
    _, info = render(a.lat, a.lon, (y, mo, d, hh, mi, 0), a.az0, a.span, a.sky_h,
                     out=a.out, seed=a.seed, label=a.label, milky_way=not a.no_mw, lp=a.lp,
                     focus=a.show)
    info["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if a.info:
        with open(a.info, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

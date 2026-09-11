"""Skyfield による天体位置・星座計算（認証不要・ローカル計算）。

NASA JPL の天体暦（de421.bsp）に基づき、指定した観測地・日時で太陽・月・惑星の
高度/方位角・見えているか・現在属する星座を計算する。
Web API（Astronomy API / API Ninjas）はキー登録が必要なため不使用。
"""
from __future__ import annotations

import os

from mcp.types import CallToolResult, TextContent
from .input_utils import as_float

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)
os.environ.setdefault("HOME", _DATA_DIR)

BODY_JA = {
    "sun": "太陽", "moon": "月", "mercury": "水星", "venus": "金星",
    "mars": "火星", "jupiter_barycenter": "木星", "saturn_barycenter": "土星",
    "uranus_barycenter": "天王星", "neptune_barycenter": "海王星",
}
BODIES = ["sun", "moon", "mercury", "venus", "mars",
          "jupiter_barycenter", "saturn_barycenter", "uranus_barycenter", "neptune_barycenter"]

_KNOWN_COORDS = {
    "東京": (35.68, 139.69), "tokyo": (35.68, 139.69),
    "大阪": (34.69, 135.50), "osaka": (34.69, 135.50),
    "札幌": (43.06, 141.35), "sapporo": (43.06, 141.35),
    "福岡": (33.59, 130.40), "fukuoka": (33.59, 130.40),
    "那覇": (26.21, 127.68), "naha": (26.21, 127.68),
    "ニューヨーク": (40.71, -74.01), "new york": (40.71, -74.01),
    "ロンドン": (51.51, -0.13), "london": (51.51, -0.13),
    "シドニー": (-33.87, 151.21), "sydney": (-33.87, 151.21),
    "ハワイ": (19.82, -155.47), "hawaii": (19.82, -155.47),
}

CONSTELLATION_JA = {
    "And": "アンドロメダ座", "Ant": "ポンプ座", "Aps": "ふうちょう座", "Aqr": "みずがめ座",
    "Aql": "わし座", "Ara": "さいだん座", "Ari": "おひつじ座", "Aur": "ぎょしゃ座",
    "Boo": "うしかい座", "Cae": "ちょうこくぐ座", "Cam": "きりん座", "Cnc": "かに座",
    "CVn": "りょうけん座", "CMa": "おおいぬ座", "CMi": "こいぬ座", "Cap": "やぎ座",
    "Car": "りゅうこつ座", "Cas": "カシオペヤ座", "Cen": "ケンタウルス座", "Cep": "ケフェウス座",
    "Cet": "くじら座", "Cha": "カメレオン座", "Cir": "コンパス座", "Col": "はと座",
    "Com": "かみのけ座", "CrA": "みなみのかんむり座", "CrB": "かんむり座", "Crv": "からす座",
    "Crt": "コップ座", "Cru": "みなみじゅうじ座", "Cyg": "はくちょう座", "Del": "いるか座",
    "Dor": "かじき座", "Dra": "りゅう座", "Equ": "こうま座", "Eri": "エリダヌス座",
    "For": "ろ座", "Gem": "ふたご座", "Gru": "つる座", "Her": "ヘルクレス座",
    "Hor": "とけい座", "Hya": "うみへび座", "Hyi": "みずへび座", "Ind": "インディアン座",
    "Lac": "とかげ座", "Leo": "しし座", "LMi": "こじし座", "Lep": "うさぎ座",
    "Lib": "てんびん座", "Lup": "おおかみ座", "Lyn": "やまねこ座", "Lyr": "こと座",
    "Men": "テーブルさん座", "Mic": "けんびきょう座", "Mon": "いっかくじゅう座", "Mus": "はえ座",
    "Nor": "じょうぎ座", "Oct": "はちぶんぎ座", "Oph": "へびつかい座", "Ori": "オリオン座",
    "Pav": "くじゃく座", "Peg": "ペガスス座", "Per": "ペルセウス座", "Phe": "ほうおう座",
    "Pic": "がか座", "Psc": "うお座", "PsA": "みなみのうお座", "Pup": "とも座",
    "Pyx": "らしんばん座", "Ret": "レチクル座", "Sge": "や座", "Sgr": "いて座",
    "Sco": "さそり座", "Scl": "ちょうこくしつ座", "Sct": "たて座", "Ser": "へび座",
    "Sex": "ろくぶんぎ座", "Tau": "おうし座", "Tel": "ぼうえんきょう座", "Tri": "さんかく座",
    "TrA": "みなみのさんかく座", "Tuc": "きょしちょう座", "UMa": "おおぐま座",
    "UMi": "こぐま座", "Vel": "ほ座", "Vir": "おとめ座", "Vol": "とびうお座",
    "Vul": "こぎつね座",
}


def _constellation_ja(abbr: str) -> str:
    return CONSTELLATION_JA.get(abbr, abbr)


def _load_skyfield():
    from skyfield.api import Loader, position_of_radec, load_constellation_map, wgs84
    # データをキャッシュディレクトリから読み込む（なければDL）
    loader = Loader(_DATA_DIR, verbose=False)
    ts = loader.timescale()
    eph = loader("de421.bsp")
    constellation_at = load_constellation_map()
    return ts, eph, constellation_at, wgs84


def constellation_now(latitude=None, longitude=None, place=None, time_utc=None):
    """指定した観測地・日時で太陽・月・惑星の位置（高度・方位角・星座）を返す。

    「今夜東京で見える惑星は?」など。NASA JPL 天体暦 + Skyfield でローカル計算（認証不要）。

    Args:
        latitude: 観測地の緯度（例 東京 35.68）。place 指定時は省略可。
        longitude: 観測地の経度（例 東京 139.69）。
        place: 観測地名（既知テーブル）。緯度経度より優先。
        time_utc: 観測時刻 "YYYY-MM-DD HH:MM"（UTC）。省略で現在。
    """
    if place:
        key = place.strip().lower()
        match = None
        for k, v in _KNOWN_COORDS.items():
            if key == k.lower():
                match = v
                break
        if match:
            latitude, longitude = match
        else:
            # 既知テーブルにない任意の地名・施設 → Open-Meteo/Nominatim でジオコーディング
            try:
                from .weather_astro import _geocode
                g = _geocode(place)
            except Exception:
                g = None
            if g:
                latitude, longitude = g["latitude"], g["longitude"]
            else:
                return CallToolResult(
                    content=[TextContent(type="text", text="地名 " + place + " を解決できませんでした。緯度経度を直接指定してください。")],
                    structuredContent={"error": "unknown place", "place": place,
                                       "known": sorted(set(k for k in _KNOWN_COORDS))},
                )
    if latitude is None or longitude is None:
        return CallToolResult(
            content=[TextContent(type="text", text="観測地を指定してください。place（例 東京/Tokyo）または latitude/longitude。")],
            structuredContent={"error": "location required"},
        )
    lat = as_float(latitude, None, -90.0, 90.0)
    lon = as_float(longitude, None, -180.0, 180.0)
    if lat is None or lon is None:
        return CallToolResult(
            content=[TextContent(type="text", text="latitude（-90〜90）と longitude（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "latitude": str(latitude),
                               "longitude": str(longitude)},
        )

    from skyfield.api import position_of_radec
    try:
        ts, eph, constellation_at, wgs84 = _load_skyfield()
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text="Skyfield 天体暦の読み込みに失敗しました: " + str(e))],
            structuredContent={"error": str(e)},
        )

    import datetime as dt
    if time_utc:
        try:
            t0 = dt.datetime.strptime(time_utc, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return CallToolResult(content=[TextContent(type="text", text="time_utc は YYYY-MM-DD HH:MM（UTC）形式で指定してください。")], structuredContent={"error": "bad time"})
    else:
        t0 = dt.datetime.now(dt.timezone.utc)
    t = ts.from_datetime(t0)
    observer = wgs84.latlon(lat, lon)
    where = eph["earth"] + observer

    lines = ["🌌 **天体の現在位置**（" + (place or f"{lat},{lon}") + "・" + t0.strftime("%Y-%m-%d %H:%M") + " UTC）:"]
    results = []
    visible_any = False
    for name in BODIES:
        try:
            body = eph[name]
        except KeyError:
            continue
        apparent = where.at(t).observe(body).apparent()
        alt, az, _ = apparent.altaz()
        ra, dec, _ = apparent.radec()
        c_abbr = constellation_at(position_of_radec(ra.hours, dec.degrees))
        c_ja = _constellation_ja(c_abbr)
        alt_d = alt.degrees
        visible = alt_d > 0
        visible_any = visible_any or visible
        rec = {"body": BODY_JA.get(name, name), "altitude_deg": round(alt_d, 1),
               "azimuth_deg": round(az.degrees, 1), "constellation": c_abbr,
               "constellation_ja": c_ja, "visible": bool(visible),
               "ra_hours": round(ra.hours, 3), "dec_deg": round(dec.degrees, 2)}
        results.append(rec)
        vis = "見えています▲" if visible else "地平線下"
        lines.append("- **" + rec["body"] + "**: 高度 " + str(rec["altitude_deg"]) + "° / 方位 " + str(rec["azimuth_deg"]) + "° ／ " + rec["constellation_ja"] + "（" + c_abbr + "）" + vis)

    if not visible_any:
        lines.append("🤖 【AIからのインテリジェントアドバイス】現在この観測地からは主要な天体がすべて地平線下です。日の入り後や日の出前に再確認するか、観測地を変更してください。")
    else:
        lines.append("🤖 【AIからのインテリジェントアドバイス】高度30°以上の天体は観測に適しています。空が暗く雲が少ないほど、惑星・月の観察がしやすいです。")
    lines.append("出典: NASA JPL 天体暦 (de421.bsp) + Skyfield ／ 星座判定は IAU 星座境界。")
    return CallToolResult(
        content=[TextContent(type="text", text=chr(10).join(lines))],
        structuredContent={"place": place, "lat": lat, "lon": lon, "time_utc": t0.strftime("%Y-%m-%d %H:%M"),
                           "bodies": results, "source": "Skyfield + JPL de421"},
    )

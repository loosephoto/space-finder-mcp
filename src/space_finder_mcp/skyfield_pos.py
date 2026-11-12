"""Skyfield による天体位置・星座計算（認証不要・ローカル計算）。

NASA JPL の天体暦（de421.bsp）に基づき、指定した観測地・日時で太陽・月・惑星の
高度/方位角・見えているか・現在属する星座を計算する。
Web API（Astronomy API / API Ninjas）はキー登録が必要なため不使用。
"""
from __future__ import annotations

from mcp.types import CallToolResult, TextContent

from .name_common import ResolverUnavailable
from .input_utils import as_float
from .places import resolve_place

# 天体暦・星座データの置き場は `ephemeris` が持つ（cwd 非依存）。
# 以前は import 時に `os.environ.setdefault("HOME", ...)` でプロセス環境変数を
# 書き換えていたが、星座境界は Skyfield 同梱データなので HOME は不要（実測）。

BODY_JA = {
    "sun": "太陽", "moon": "月", "mercury": "水星", "venus": "金星",
    "mars": "火星", "jupiter_barycenter": "木星", "saturn_barycenter": "土星",
    "uranus_barycenter": "天王星", "neptune_barycenter": "海王星",
}
BODIES = ["sun", "moon", "mercury", "venus", "mars",
          "jupiter_barycenter", "saturn_barycenter", "uranus_barycenter", "neptune_barycenter"]

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
    """(timescale, ephemeris, 星座判定, wgs84) を返す（暦は共有ローダ）。"""
    from skyfield.api import wgs84
    from . import ephemeris
    ts, eph = ephemeris.load()
    return ts, eph, ephemeris.constellation_map(), wgs84


def constellation_now(latitude=None, longitude=None, place=None, time_utc=None):
    """指定した観測地・日時で太陽・月・惑星の位置（高度・方位角・星座）を返す。

    「今夜東京で見える惑星は?」など。NASA JPL 天体暦 + Skyfield でローカル計算（認証不要）。
    地名の解決は `places.resolve_place`（他ツールと同じ唯一の実装）を通すので、
    地名解決サービスに繋がらないときは `error="resolver_unavailable"`、
    地名が無いときは `error="place not resolved"` を返します（東京で代用しない）。

    Args:
        latitude: 観測地の緯度（例 東京 35.68）。place 指定時は省略可。
        longitude: 観測地の経度（例 東京 139.69）。
        place: 観測地名（既知テーブル）。緯度経度より優先。
        time_utc: 観測時刻。ISO8601（"2026-10-31T12:00:00Z" / "2026-10-31 12:00"、
            ±hh:mm のオフセットも可）。省略で現在。天体暦（DE421）の収録期間
            1899-07-29〜2053-10-09 の外はエラー結果を返します。
    """
    from .ground_view import EPH_MAX_UTC, EPH_MIN_UTC, _parse_when, ephemeris_range_error

    # 範囲外の緯度経度は `places.resolve_place`（= as_float）が**クランプ**する契約なので
    # （tests/test_places_and_split.py が既存挙動として固定）、ツール入口で明示的に弾く。
    # 実測: latitude=999 が北極 90.0 に化けて「その座標で計算した」と誤認させていた。
    if not place and (latitude is not None or longitude is not None):
        _la = as_float(latitude, None)
        _lo = as_float(longitude, None)
        if (_la is None or _lo is None
                or not (-90.0 <= _la <= 90.0) or not (-180.0 <= _lo <= 180.0)):
            return CallToolResult(
                content=[TextContent(type="text",
                                     text="latitude（-90〜90）と longitude（-180〜180）は数値（度）で"
                                          "指定してください。受け取った値: latitude={!r} / longitude={!r}"
                                          .format(latitude, longitude))],
                structuredContent={"error": "invalid coordinates", "latitude": str(latitude),
                                   "longitude": str(longitude)},
            )

    try:
        ll = resolve_place(place, latitude, longitude)
    except ResolverUnavailable as ex:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="観測地 '{}' を解決できませんでした（地名解決サービスに接続できません: {}）"
                                      "。時間をおくか latitude/longitude を指定してください。".format(
                                          place, str(ex)[:120]))],
            structuredContent={"error": "resolver_unavailable", "place": str(place)},
        )
    if ll is None and (latitude is not None or longitude is not None):
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="latitude（-90〜90）と longitude（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "latitude": str(latitude),
                               "longitude": str(longitude)},
        )
    if ll is None:
        if place:
            return CallToolResult(
                content=[TextContent(type="text",
                                     text="観測地 '{}' を解決できませんでした。地名を変えるか "
                                          "lat/lon を数値で指定してください。".format(place))],
                structuredContent={"error": "place not resolved", "place": str(place)},
            )
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="観測地を指定してください。place（例 東京/Tokyo）または latitude/longitude。")],
            structuredContent={"error": "location required"},
        )
    lat, lon = ll

    parsed = _parse_when(time_utc)
    if parsed == "bad":
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="time_utc は ISO8601（例 \"2026-10-31T12:00:00Z\" / "
                                      "\"2026-10-31 12:00\"）で指定してください。")],
            structuredContent={"error": "bad time", "time_utc": str(time_utc)},
        )
    _range = ephemeris_range_error(parsed)
    if _range:
        return CallToolResult(
            content=[TextContent(type="text", text=_range)],
            structuredContent={"error": "ephemeris_range", "time_utc": str(time_utc),
                               "supported": "{} 〜 {}".format(
                                   EPH_MIN_UTC.strftime("%Y-%m-%d"), EPH_MAX_UTC.strftime("%Y-%m-%d"))},
        )
    try:
        ts, eph, constellation_at, wgs84 = _load_skyfield()
    except Exception as e:                     # 天体暦の読み込み失敗を外へ漏らさない
        return CallToolResult(
            content=[TextContent(type="text", text="Skyfield 天体暦の読み込みに失敗しました: " + str(e))],
            structuredContent={"error": str(e)},
        )

    from skyfield.api import position_of_radec
    t = ts.utc(*parsed)
    tstr = t.utc_strftime("%Y-%m-%d %H:%M")
    observer = wgs84.latlon(lat, lon)
    where = eph["earth"] + observer

    lines = ["🌌 **天体の現在位置**（" + (place or f"{lat},{lon}") + "・" + tstr + " UTC）:"]
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
        lines.append("- **" + rec["body"] + "**: 高度 " + str(rec["altitude_deg"]) + "° / 方位 "
                     + str(rec["azimuth_deg"]) + "° ／ " + rec["constellation_ja"] + "（" + c_abbr + "）" + vis)

    if not visible_any:
        lines.append("🤖 【AIからのインテリジェントアドバイス】現在この観測地からは主要な天体がすべて"
                     "地平線下です。日の入り後や日の出前に再確認するか、観測地を変更してください。")
    else:
        lines.append("🤖 【AIからのインテリジェントアドバイス】高度30°以上の天体は観測に適しています。"
                     "空が暗く雲が少ないほど、惑星・月の観察がしやすいです。")
    lines.append("出典: NASA JPL 天体暦 (de421.bsp) + Skyfield ／ 星座判定は IAU 星座境界。")
    return CallToolResult(
        content=[TextContent(type="text", text=chr(10).join(lines))],
        structuredContent={"place": place, "lat": lat, "lon": lon, "time_utc": tstr,
                           "bodies": results, "source": "Skyfield + JPL de421"},
    )

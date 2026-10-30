"""星空マップ＋人工衛星オーバーレイ（描画は ground_view に統合, 認証不要）。

指定した観測地・時刻の「空」を画像化し、その上に人工衛星の現在位置を重ねる。
天体位置は NASA JPL de421 + Skyfield、衛星位置は CelesTrak TLE + SGP4 で計算（全てローカル/認証不要）。

**描画は `ground_view.render`（地上から見上げた正距円筒パノラマ）に一本化した。**
旧 `engine="simple"`（Pillow・実写背景の合成）と `engine="accurate"`（matplotlib 極座標星図）は
この描画へ統合して廃止した。見た目の要素（惑星の種類別アイコン＝土星の環・木星の縞・火星の極冠、
人工衛星の赤マーカー＋白フチ＋赤い名札、軌道予測の破線、恒星の実測位置・色・大気減光、
太陽高度で変わる空色）は `ground_view` 側へ取り込んである。
`engine` 引数は互換のために残し、どの値でも同じ描画（ground）を使う。

このモジュールが担うのは、観測地の解決・TLE 取得（並列・出典つき）・出典の要約と、公開ツール
`sky_map_with_satellites` の入口だけ。星表・暦の読み込み・解析・描画は `ground_view` が持つ。

返却: content に base64 画像(ImageContent)、structuredContent に計算した座標JSON。
"""
from __future__ import annotations

import os

import requests

from .celestrak import CELESTRAK_SOURCE, fetch_tle_ex
from .input_utils import as_float

_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "skyfield_data")
os.makedirs(_DATA_DIR, exist_ok=True)

# 注目衛星 (LEOは動き・軌道線を見せる、静止は定点表示)
_SAT_CATALOG = [
    ("ISS (国際宇宙ステーション)", 25544, "leo"),
    ("天宮 (中国宇宙ステーション)", 48274, "leo"),
    ("ハッブル宇宙望遠鏡", 20580, "leo"),
    ("NOAA-20 (気象衛星)", 43013, "leo"),
    ("ひまわり9号 (静止気象衛星)", 41836, "geo"),
    ("みちびき4号 (準天頂衛星)", 42917, "geo"),
]
_BRIGHT_STARS = [
    ("シリウス", 6.7525, -16.716), ("カノープス", 6.3996, -52.696),
    ("アルクトゥルス", 14.2611, 19.182), ("ベガ", 18.6156, 38.784),
    ("カペラ", 5.2773, 45.998), ("リゲル", 5.2423, -8.202),
    ("プロキオン", 7.6550, 5.225), ("ベテルギウス", 5.9193, 7.407),
    ("アルタイル", 19.8464, 8.868), ("アルデバラン", 4.5984, 16.509),
    ("アンタレス", 16.4899, -26.432), ("スピカ", 13.4201, -11.161),
    ("ポルックス", 7.7553, 28.026), ("フォーマルハウト", 22.9610, -29.622),
    ("デネブ", 20.6919, 45.280), ("レグルス", 10.1396, 11.967),
]
_KNOWN_COORDS = {
    "東京": (35.68, 139.69), "tokyo": (35.68, 139.69),
    "大阪": (34.69, 135.50), "osaka": (34.69, 135.50),
    "札幌": (43.06, 141.35), "sapporo": (43.06, 141.35),
    "那覇": (26.21, 127.68), "naha": (26.21, 127.68),
    "ニューヨーク": (40.71, -74.01), "new york": (40.71, -74.01),
    "ロンドン": (51.51, -0.13), "london": (51.51, -0.13),
    "シドニー": (-33.87, 151.21), "sydney": (-33.87, 151.21),
    "ハワイ": (19.82, -155.47), "hawaii": (19.82, -155.47),
}


def _fetch_tle(catnr):
    """TLE 2行 (line1, line2) と出典を取得（失敗時は (None, "")）。

    CelesTrak が遮断中なら代替源から取るので、出典を一緒に返して図の出典表示に反映する
    （代替源の TLE を「CelesTrak の TLE」と書かないため）。
    """
    try:
        tle, source = fetch_tle_ex(norad_id=catnr)
    except requests.RequestException:
        return (None, "")
    return ((tle[1], tle[2]), source) if tle else (None, "")


def _fetch_tles_parallel(catnrs) -> dict:
    """複数の NORAD ID の TLE を**並列に**取る（戻りは {catnr: (tle, 出典)}）。

    CelesTrak（または代替源）の1件あたりの応答は 1.5〜3.5 秒（実測）。直列で 6 衛星を
    取ると 20.4 秒かかり、`sky_map_with_satellites` の所要時間の 8 割を占めていた。
    TLE は衛星ごとに独立なので、まとめて取りに行けば「最も遅い1件」の時間で済む。
    `celestrak` 側の ttl_cache は single-flight なので、同じ ID を二重に叩かない。
    """
    from concurrent.futures import ThreadPoolExecutor
    ids = list(dict.fromkeys(c for c in catnrs if c is not None))   # 重複 ID は1回だけ

    def _one(catnr):
        try:
            return catnr, _fetch_tle(catnr)
        except Exception:                     # 個別の失敗は呼び出し側が errors に残す
            return catnr, (None, "")

    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
        return dict(ex.map(_one, ids))


def _tle_source_summary(sources: dict) -> str:
    """実際に位置計算へ使ったTLEの出典を、取得失敗や混在を偽らずに示す。"""
    hosts = sorted({value for value in sources.values() if value})
    if not hosts:
        return "TLE未取得"
    if hosts == [CELESTRAK_SOURCE]:
        return "CelesTrak TLE"
    return "TLE出典: {}".format("、".join(hosts))


def _resolve_place(place, lat, lon):
    """place 文字列 or lat/lon 数値から (lat, lon)。任意の地名は Open-Meteo でジオコーディング。

    lat/lon が数値として解釈できない場合は None を返す（呼び出し側でエラーにする）。
    """
    if lat is not None and lon is not None:
        la, lo = as_float(lat, None, -90.0, 90.0), as_float(lon, None, -180.0, 180.0)
        if la is None or lo is None:
            return None
        return la, lo
    if place:
        key = str(place).strip().lower()
        if key in _KNOWN_COORDS:
            return _KNOWN_COORDS[key]
        # 既知リストにない任意の地名 → オンラインで緯度経度を解決（キャッシュ込み）
        try:
            from .weather_astro import _geocode
            g = _geocode(place)
            if g:
                return float(g["latitude"]), float(g["longitude"])
        except Exception:
            pass
    return None


def sky_map_with_satellites(place=None, lat=None, lon=None, when=None,
                            engine="ground", span: float = 360.0, az0: float = 0.0,
                            lp="suburb", show=None):
    """東京（または指定地）の空を地上から見上げた図（恒星・惑星・天の川・人工衛星）を返す。

    例:「東京の空に惑星と人工衛星を重ねた図」「今見えるISSを星空マップで」「木星はどこに見える？」
    天体位置は JPL de421 + Skyfield、衛星位置は CelesTrak TLE + SGP4 で実測計算。

    描画は `ground_view.render`（地上視点パノラマ・方位-高度の正距円筒）に一本化した。
    地平線が下・方位が横軸で、下部に街並みと方位目盛りを描く。恒星は Hipparcos 実星表
    （Vmag<8.5）、天の川は銀経・銀緯から描く。太陽高度で昼/夕方/朝焼け/薄明/夜の空色と
    見える限界等級が変わる。今は見えない惑星・主要恒星・銀河は○の輪郭で位置だけを示す
    （昼でも位置が分かる）。惑星は種類別アイコン（土星＝環・木星＝縞・火星＝極冠）、
    人工衛星は赤いマーカー＋白フチ＋赤い名札＋軌道予測の破線で描く。

    画像は content に base64 インライン表示、座標は structuredContent に JSON。

    Args:
        place: 観測地（例 "東京","大阪","new york"）。lat/lon 指定時は無視。省略時は東京。
        lat: 観測地の緯度。lon と併用時は place より優先。
        lon: 観測地の経度。
        when: 観測時刻 ISO8601（例 "2026-09-09T11:00:00Z"）。省略時は現在時刻。
        engine: **廃止（互換のため残置）**。旧 "ground"/"simple"/"accurate" の3エンジンは
            地上視点パノラマ（ground_view）へ統合した。どの値を渡しても同じ描画になる
            （旧 simple/accurate の見た目の要素は ground に取り込み済み）。
        span: 横方向の視野（度, 30〜360）。360 で全天。
        az0: 左端の方位（度）。span=180, az0=225 なら南西→北東。
        lp: 光害の程度 "city"（都心）/ "suburb"（郊外, 既定）/ "dark"（山間・離島）。
            未対応の値は既定（郊外）に落とし、その旨を本文に明示する。
        show: 依頼表示する天体名（カンマ区切り。例 "木星,ベガ,M31"）。省略時は主要天体すべて。

    インライン画像を表示できないハーネス（CLI系・Android系の codex / opencode など）向けに、
    content の先頭へ「🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: `…`」という
    アイコン付きリンクを必ず出します（画像は %LOCALAPPDATA%\\Temp\\space_finder_mcp\\out に
    保存し、同じパスを structuredContent.image_path にも入れます）。
    回答時はこのリンクをそのまま提示してください（画像が描画されない環境では唯一の導線）。
    """
    from . import ground_view
    return ground_view.sky_ground(place=place, lat=lat, lon=lon, when=when,
                                  span=span, az0=az0, lp=lp, show=show)

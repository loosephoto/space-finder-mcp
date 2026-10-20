"""天体面遺跡の「局地サイトプラン」描画と、月面遺跡の一次情報データ。

**なぜ局地プランが要るのか（実測に基づく）**

月面の各遺跡は、数十 m から数百 m の範囲に次の機材が散らばっている。
LM（着陸船）、ALSEP（実験パッケージ）、LRRR（レーザー反射器）、
LRV（月面車）とその最終駐車地点、そしてアメリカ国旗。
一方、天体面地図（NASA Trek の LRO WAC / Viking MDIM グローバルモザイク）は
1px あたり数百 m から 20 km 程度しかなく、**どの実用的なズームでも
これらの機材は 1 画素に収まらない**（同じ点の重なりになる）。
つまり等角図法だけの図では「旗が LM から何 m・何方位にあるか」を示せない。

そこで本モジュールは 2 段構えの図にする。

1. **位置図（等角図法）** — 着陸地点の住所と、同じ天体・近隣の
   別の着陸地点（例: アポロ12号の LM とサーベイヤー3号は約 0.4 km 離れている）。
2. **局地サイトプラン（メートル単位・北が上）** — 一次情報が公開している
   「距離＋方位」から各機材の位置を算出し、縮尺バーつきで描く。
   NASA が PSR（Preliminary Science Report）で使う site plan と同じ考え方。

**誠実性のルール**

- 位置を描くのは **距離と方位の両方が一次情報で公開されている機材だけ**。
  片方しかない（例: アポロ11号の旗は距離 8.2 m は分かるが方位の公開値がない）は
  **図には描かず**、凡例と注記に「公開値なし」と書いてテキストで伝える。
- 推定した位置は position_status="derived" として区別し、推定根拠を必ず添える。
- 走行経路のうち**公開されているのは端点（最終駐車地点）だけ**の場合がある。
  そのときは端点間の直線を引き「公開されている端点間」と明記する
  （全経路のように描かない）。

出典:
- LM / ALSEP / LRRR 座標 = NASA NSSDCA「Apollo Landing Site Coordinates」
  （planetary 中心 Mean Earth/Polar Axis 座標系・DE421・Wagner et al., Icarus 283 (2017)）
- 旗・LRV 最終駐車地点 = NASA Apollo Lunar Surface Journal（ALSJ, James Fincannon / Eric M. Jones）
- 月面車の走行距離 = 各ミッションの一次情報（下記データ表の note に明記）
"""
from __future__ import annotations

import math
import re

# 天体半径（m）。局地平面への投影で使う。
MOON_RADIUS_M = 1737.4e3

# 機材の種類別の色
KIND_COLORS = {
    "lm": (80, 160, 255),        # 着陸船: 青
    "rover": (255, 90, 40),      # ローバー: 橙
    "alsep": (140, 230, 120),    # 実験装置: 緑
    "lrrr": (255, 220, 90),      # レーザー反射器: 黄
    "flag": (250, 90, 200),      # 国旗: 桃
    "other": (170, 180, 200),    # その他: 灰
}
KIND_LABELS = {
    "lm": "着陸船 (LM)",
    "rover": "ローバー",
    "alsep": "実験装置 (ALSEP)",
    "lrrr": "レーザー反射器 (LRRR)",
    "flag": "国旗",
    "other": "その他",
}


def local_offset_m(lat: float, lon: float, lat0: float, lon0: float,
                   radius_m: float) -> tuple:
    """基準点 (lat0, lon0) から見た点の局地平面座標 (東, 北) を m で返す。

    経度は東向き正。月の座標は planetocentric（IAU 2000 系）を使う。
    """
    la = math.radians((float(lat) + float(lat0)) / 2.0)
    east = math.radians(float(lon) - float(lon0)) * math.cos(la) * radius_m
    north = math.radians(float(lat) - float(lat0)) * radius_m
    return east, north


def azimuth_to_xy(dist_m: float, az_deg: float) -> tuple:
    """距離(m)と方位角(度・北が 0 で時計回り)から局地平面座標 (東, 北) を m で返す。"""
    a = math.radians(float(az_deg))
    return float(dist_m) * math.sin(a), float(dist_m) * math.cos(a)


def fmt_m(v: float) -> str:
    """距離を人が読みやすい形にする（m / km）。"""
    v = float(v)
    if abs(v) >= 1000:
        return "{:.2f} km".format(v / 1000.0)
    return "{:.0f} m".format(v)


# ====================================================================
# 月面遺跡の一次情報データ
#   距離・方位は北が 0・時計回り（ALSJ の方位角と同じ定義）。
#   position_status:
#     "published" = 距離と方位の公開値が両方ある
#     "derived"   = 公開関係からの推定
#     "relation"  = 関係性の記述のみ（図には描かない）
# ====================================================================

# 一次情報: NSSDCA「Apollo Landing Site Coordinates」
#   LM / ALSEP / LRRR の緯度経度（degrees N / degrees E, planetocentric ME/PA, DE421）
#
# ALSJ の記述:
#   A11 旗 = LM の中心線から約 8.2 m（27 ft）。方位の公開値はない。
#   A12 旗 = MESA から約 7 m、+Z(西) strut と +Y(北) strut の中間。LM は時計回り約10度回転。
#   A14 旗 = 前脚と同じ視線上（より西）。S-band の影中心は三脚から 6.8 m。
#   A15 旗 = LM 中心から約 11 m、方位約 30 度（PSR Fig 5-52）。
#   A16 旗 = やや北西を向く（距離・方位の公開値はない）。
#   A17 旗 = EVA-2 時点では東向き、帰投後は北寄り（距離・方位の公開値はない）。
#
# LRV 最終駐車地点（ALSJ「Final LRV Parking Places」・不確かさ約 10 m）:
#   A15 165 m / 102 度、A16 81 m / 88 度、A17 158 m / 94 度。
APOLLO_SITES: dict = {
    "apollo11": {
        "ja": "アポロ11号", "en": "Apollo 11", "place": "静かの海",
        "landed": "1969-07-20",
        "lat": 0.67416, "lon": 23.47314,
        "artifacts": [
            {"id": "lm", "ja": "LM（エーグル）", "en": "Lunar Module", "kind": "lm",
             "lat": 0.67416, "lon": 23.47314, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "lrrr", "ja": "LRRR（レーザー反射器）", "en": "LRRR", "kind": "lrrr",
             "lat": 0.67345, "lon": 23.47307, "position_status": "published",
             "note": "NSSDCA 公表座標。現在も laser ranging が使用中"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "dist_m": 8.2, "az_deg": None, "position_status": "relation",
             "note": "LM の中心線から約 8.2 m（27 ft・Aldrin の証言）。"
                     "方位の公開値がないため位置は図に描かない。"
                     "上昇エンジンの噴射で倒れたとされる（LROC に旗の影なし）"},
        ],
    },
    "apollo12": {
        "ja": "アポロ12号", "en": "Apollo 12", "place": "嵐の大洋",
        "landed": "1969-11-19",
        "lat": -3.0128, "lon": -23.4219,
        "artifacts": [
            {"id": "lm", "ja": "LM（イントレピッド）", "en": "Lunar Module", "kind": "lm",
             "lat": -3.0128, "lon": -23.4219, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "alsep", "ja": "ALSEP", "en": "ALSEP", "kind": "alsep",
             "lat": -3.0098, "lon": -23.4249, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "dist_m": 9.5, "az_deg": 315.0, "position_status": "derived",
             "note": "ALSJ: MESA から約 7 m、+Z(西) strut と +Y(北) strut の中間。"
                     "LM は時計回り約10度回転。方位315度・距離9.5 m は"
                     "「西 strut と北 strut の中間」という記述からの推定（MESA が LM の西側にあるため）"},
        ],
    },
    "apollo14": {
        "ja": "アポロ14号", "en": "Apollo 14", "place": "フラ・マウロ",
        "landed": "1971-02-05",
        "lat": -3.64589, "lon": -17.47194,
        "artifacts": [
            {"id": "lm", "ja": "LM（アルキット）", "en": "Lunar Module", "kind": "lm",
             "lat": -3.64589, "lon": -17.47194, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "lrrr", "ja": "LRRR", "en": "LRRR", "kind": "lrrr",
             "lat": -3.64417, "lon": -17.47865, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "alsep", "ja": "ALSEP", "en": "ALSEP", "kind": "alsep",
             "lat": -3.64419, "lon": -17.47768, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "position_status": "relation",
             "note": "ALSJ: 前脚と同じ視線上にあり LM より西。"
                     "S-band アンテナの影の中心は三脚から 6.8 m、旗はその約 4 m 手前。"
                     "距離・方位の公開値がないため位置は図に描かない"},
        ],
    },
    "apollo15": {
        "ja": "アポロ15号", "en": "Apollo 15", "place": "ハドリー・リル",
        "landed": "1971-07-30",
        "lat": 26.13239, "lon": 3.63330,
        "artifacts": [
            {"id": "lm", "ja": "LM（エンデバー）", "en": "Lunar Module", "kind": "lm",
             "lat": 26.13239, "lon": 3.63330, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "dist_m": 11.0, "az_deg": 30.0, "position_status": "published",
             "note": "ALSJ / Apollo 15 Preliminary Science Report Fig 5-52: "
                     "LM 中心から約11 m、方位約30度"},
            {"id": "alsep", "ja": "ALSEP", "en": "ALSEP", "kind": "alsep",
             "lat": 26.13406, "lon": 3.62991, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "lrrr", "ja": "LRRR", "en": "LRRR", "kind": "lrrr",
             "lat": 26.13341, "lon": 3.62850, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "lrv", "ja": "LRV（最終駐車地点）", "en": "LRV final parking", "kind": "rover",
             "dist_m": 165.0, "az_deg": 102.0, "position_status": "published",
             "note": "ALSJ「Final LRV Parking Places」: LM から約165 m、方位102度（不確かさ約10 m）"},
        ],
    },
    "apollo16": {
        "ja": "アポロ16号", "en": "Apollo 16", "place": "デカルト高地",
        "landed": "1972-04-20",
        "lat": -8.9734, "lon": 15.5011,
        "artifacts": [
            {"id": "lm", "ja": "LM（オリオン）", "en": "Lunar Module", "kind": "lm",
             "lat": -8.9734, "lon": 15.5011, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "alsep", "ja": "ALSEP", "en": "ALSEP", "kind": "alsep",
             "lat": -8.9759, "lon": 15.4986, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "position_status": "relation",
             "note": "ALSJ: やや北西を向く。旗竿は局所垂直から約30度傾いている。"
                     "距離・方位の公開値がないため位置は図に描かない"},
            {"id": "lrv", "ja": "LRV（最終駐車地点）", "en": "LRV final parking", "kind": "rover",
             "dist_m": 81.0, "az_deg": 88.0, "position_status": "published",
             "note": "ALSJ「Final LRV Parking Places」: LM から約81 m、方位88度（不確かさ約10 m）"},
        ],
    },
    "apollo17": {
        "ja": "アポロ17号", "en": "Apollo 17", "place": "タウルス・リトロー",
        "landed": "1972-12-11",
        "lat": 20.1911, "lon": 30.7723,
        "artifacts": [
            {"id": "lm", "ja": "LM（アメリカ）", "en": "Lunar Module", "kind": "lm",
             "lat": 20.1911, "lon": 30.7723, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "alsep", "ja": "ALSEP", "en": "ALSEP", "kind": "alsep",
             "lat": 20.1923, "lon": 30.7655, "position_status": "published",
             "note": "NSSDCA 公表座標"},
            {"id": "flag", "ja": "アメリカ国旗", "en": "US flag", "kind": "flag",
             "position_status": "relation",
             "note": "ALSJ: EVA-2 時点では東向き、帰投後は北寄り。旗は他ミッションより約20%大きい。"
                     "距離・方位の公開値がないため位置は図に描かない"},
            {"id": "lrv", "ja": "LRV（最終駐車地点）", "en": "LRV final parking", "kind": "rover",
             "dist_m": 158.0, "az_deg": 94.0, "position_status": "published",
             "note": "ALSJ「Final LRV Parking Places」: LM から約158 m、方位94度（不確かさ約10 m）"},
        ],
    },
}

# ソ連の月面車（ルノホート）。走行距離は一次情報で公表。
# 経路全体の座標が機械可読で公開されているわけではないため、走行距離だけを記す。
LUNOKHOD_SITES: dict = {
    "lunokhod1": {
        "ja": "ルノホート1号（ルナ17号）", "en": "Luna 17 / Lunokhod 1",
        "place": "雨の海", "landed": "1970-11-17",
        "lat": 38.23764, "lon": -35.0016,
        "rover": "ルノホート1号", "rover_ja": "ルノホート1号",
        "dist_km": 10.5,
        "note": "初の月面車。1971年に運用終了。"
                "NASA の月面遺跡保全指針は LRRR（レーザー反射器）を持つとして Sites 5 の1つとして挙げる。"
                "出典: NASA「Recommendations to Space-Faring Entities」App. C / LPI",
    },
    "lunokhod2": {
        "ja": "ルノホート2号（ルナ21号）", "en": "Luna 21 / Lunokhod 2",
        "place": "ル・モニエ・クレーター", "landed": "1973-01-15",
        "lat": 25.8323, "lon": 30.9222,
        "rover": "ルノホート2号", "rover_ja": "ルノホート2号",
        "dist_km": 39.0,
        "note": "約39 km を走行（当時の記録では4か月で42 kmとも）。1973年に運用終了。出典: LPI",
    },
}

# 中国の月面車（玉兔号）。着陸座標は LROC 画像から確定したもの。
CHANG_E_ROVERS: dict = {
    "yutu": {
        "ja": "嫦娥3号（玉兔号）", "en": "Chang'e 3 / Yutu",
        "place": "雨の海（虹の入り江）", "landed": "2013-12-14",
        "lat": 44.1214, "lon": -19.5116,
        "rover": "玉兔号", "rover_ja": "玉兔号（Yutu）",
        "dist_m": 114.8,
        "note": "走行距離 114.8 m（cross-site visual localization）。"
                "着陸座標は LROC 画像から 44.1214 deg N 340.4884 deg E として確定。"
                "出典: LPSC 2014-1859 / Liu, Di & Peng 2015 / Wikipedia「Chang'e 3」",
    },
    "yutu2": {
        "ja": "嫦娥4号（玉兔2号）", "en": "Chang'e 4 / Yutu-2",
        "place": "Von Karman クレーター（南極・Aitken 盆地・月裏側）",
        "landed": "2019-01-03",
        "lat": 45.4446, "lon": 177.5991,
        "rover": "玉兔2号", "rover_ja": "玉兔2号（Yutu-2）",
        "dist_m": 318.62,
        "note": "月裏側で初の軟着陸。着陸座標は 177.5991 deg E 45.4446 deg S（標高 −5935 m・"
                "descent camera の写真測量）。LROC 由来の値は 177.5885 deg E 45.4561 deg S。"
                "走行距離は12月間で300 m 超（73 waypoints）。"
                "出典: Nature Communications 10:4229 (2019) / CLEP 2019",
    },
}


def lunar_rover_entries() -> dict:
    """月面ローバーとして表示できる全遺跡をまとめて返す。"""
    out: dict = {}
    for key, s in APOLLO_SITES.items():
        entry = dict(s)
        has_lrv = any(a.get("kind") == "rover" for a in s["artifacts"])
        entry["rover_ja"] = "LRV（月面車）" if has_lrv else None
        entry["rover"] = "LRV" if has_lrv else None
        entry["kind"] = "apollo"
        out[key] = entry
    for key, s in LUNOKHOD_SITES.items():
        entry = dict(s)
        entry["kind"] = "lunokhod"
        entry["dist_m"] = s.get("dist_km", 0.0) * 1000.0
        out[key] = entry
    for key, s in CHANG_E_ROVERS.items():
        entry = dict(s)
        entry["kind"] = "change"
        out[key] = entry
    return out


def site_center(site: dict) -> tuple:
    """遺跡の基準点（LM など、着陸地点そのもの）を (lat, lon) で返す。"""
    return float(site["lat"]), float(site["lon"])


def site_artifacts_with_xy(site: dict, radius_m: float) -> list:
    """各機材の局地平面座標 (東, 北) を m で求め、描くものだけを返す。

    戻り: [{"id","ja","kind","east_m","north_m","position_status","note","derived"}...]
    位置を出せないもの（position_status="relation"）は "no_position": True として返す。
    """
    lat0, lon0 = site_center(site)
    out = []
    for a in site.get("artifacts", []):
        item = dict(a)
        if a.get("position_status") == "relation":
            item["no_position"] = True
            out.append(item)
            continue
        if "lat" in a and "lon" in a:
            east, north = local_offset_m(a["lat"], a["lon"], lat0, lon0, radius_m)
            item["derived"] = False
        else:
            east, north = azimuth_to_xy(a["dist_m"], a["az_deg"])
            item["derived"] = (a.get("position_status") == "derived")
        item["east_m"] = east
        item["north_m"] = north
        out.append(item)
    return out


# ====================================================================
# 局地サイトプランの描画
# ====================================================================

def _nice_scale_m(span_m: float) -> float:
    """図幅に収まる「きれいな」縮尺（m）を返す（1/2/5 x 10^n）。"""
    import math as _m
    if span_m <= 0:
        return 1.0
    raw = span_m / 4.0
    exp = _m.floor(_m.log10(raw))
    base = raw / (10 ** exp)
    for cand in (1.0, 2.0, 5.0, 10.0):
        if base <= cand:
            return cand * (10 ** exp)
    return 10 ** (exp + 1)


def _short_label(a: dict) -> str:
    """機材名を短くする（"LM（エーグル）" -> "LM"）。"""
    raw = str(a.get("short") or a.get("ja") or a.get("id") or "")
    head = re.split(r"[（(]", raw)[0].strip()
    return head[:12] if head else str(a.get("id", ""))


def render_site_plan(site: dict, *, radius_m: float = MOON_RADIUS_M,
                     out_px: int = 900, title: str = "",
                     subtitle: str = "") -> dict:
    """遺跡の局地サイトプラン（メートル単位・北が上）を描く。

    一次情報が公開している「距離＋方位」だけから各機材の位置を確定して描く。
    公開値がない機材は凡例に「位置の公開値なし」と書いて**図には描かない**。

    **レイアウト**: 見出し・凡例は上部の帯、縮尺バーと方位は下部の帯に置き、
    図体（マップ本体）とは**重ならない**領域に確保する。図体に重なると
    機材が凡例に隠れて「消えて」見える（実測: A12 の ALSEP がその状態）。

    戻り: {"img": PIL.Image, "scale_bar_m": float, "px_per_m": float,
           "drawn": [...], "not_drawn": [...], "center": (lat, lon)}
    """
    from PIL import Image, ImageDraw
    from .img_common import load_font

    lat0, lon0 = site_center(site)
    arts = site_artifacts_with_xy(site, radius_m)
    drawn = [a for a in arts if not a.get("no_position")]
    not_drawn = [a for a in arts if a.get("no_position")]

    # --- 図に収める範囲（m） ---
    if drawn:
        max_e = max(abs(a["east_m"]) for a in drawn) or 1.0
        max_n = max(abs(a["north_m"]) for a in drawn) or 1.0
    else:
        max_e = max_n = 10.0
    half = max(max_e, max_n) * 1.25 + 5.0
    span = _nice_scale_m(half * 2.0)
    half = max(span / 2.0, max_e * 1.1, max_n * 1.1)

    w = h = int(out_px)
    img = Image.new("RGB", (w, h), (12, 14, 22))
    d = ImageDraw.Draw(img)

    f_title = load_font(max(13, w // 34), bold=True)
    f_sm = load_font(max(11, w // 52))
    f_tiny = load_font(max(9, w // 64))

    # --- 凡例項目（横幅に並べる） ---
    legend_items = []
    seen = set()
    for a in drawn:
        k = a.get("kind")
        if k in seen:
            continue
        seen.add(k)
        legend_items.append(("dot", k, KIND_LABELS.get(k, k)))
    for a in not_drawn:
        legend_items.append(("hollow", a.get("ja", ""),
                             "{}（位置の公開値なし）".format(a.get("ja", ""))))

    def legend_width(items):
        total = 0
        for _, _, lab in items:
            total += 16 + len(lab) * max(6, f_tiny.size // 2 + 3) + 18
        return total

    # --- 見出し帯・凡例帯・下部帯の高さを確定 ---
    head_h = 12 + f_title.size + 6
    if subtitle:
        head_h += f_sm.size + 4
    # 凡例を 1 行に収めるか折り返すか
    if legend_items and legend_width(legend_items) > w - 32:
        # 折り返し
        rows, cur = [], []
        for it in legend_items:
            if legend_width(cur + [it]) > w - 32 and cur:
                rows.append(cur)
                cur = [it]
            else:
                cur.append(it)
        if cur:
            rows.append(cur)
    else:
        rows = [legend_items] if legend_items else []
    legend_row_h = f_tiny.size + 12
    head_h += max(1, len(rows)) * legend_row_h + 10
    foot_h = 52

    # --- 図体領域 ---
    top = head_h
    bot = h - foot_h
    body_h = max(60, bot - top)
    cx, cy = w / 2.0, top + body_h / 2.0
    px_per_m = (min(w, body_h) * 0.90) / (2.0 * half)

    def to_px(east, north):
        return cx + east * px_per_m, cy - north * px_per_m

    # --- グリッド ---
    step = _nice_scale_m(half * 2.0 / 4.0)
    if step <= 0:
        step = 10.0
    n = int(half / step) + 1
    for i in range(-n, n + 1):
        gx = cx + i * step * px_per_m
        gy = cy + i * step * px_per_m
        if 0 <= gx < w:
            d.line([(gx, top), (gx, bot)], fill=(34, 40, 56), width=1)
        if top <= gy <= bot:
            d.line([(0, gy), (w, gy)], fill=(34, 40, 56), width=1)
    d.line([(0, cy), (w, cy)], fill=(70, 84, 110), width=1)
    d.line([(cx, top), (cx, bot)], fill=(70, 84, 110), width=1)

    # --- 方位（北が上） ---
    nx, ny = w - 46, top + 30
    d.polygon([(nx, ny - 20), (nx - 8, ny + 6), (nx, ny), (nx + 8, ny + 6)], fill=(240, 240, 250))
    d.text((nx, ny + 8), "N", font=f_sm, fill=(240, 240, 250), anchor="ma")

    # --- 縮尺バー（下部帯） ---
    bar_m = _nice_scale_m(half * 2.0 / 4.0)
    bar_px = bar_m * px_per_m
    bx, by = 24, h - 30
    d.rectangle([bx, by, bx + bar_px, by + 8], fill=(235, 238, 250))
    d.rectangle([bx, by, bx + bar_px, by + 8], outline=(20, 24, 34), width=1)
    d.text((bx, by - 20), fmt_m(bar_m), font=f_sm, fill=(235, 238, 250))
    d.text((bx + bar_px + 14, by - 8), "北が上・縮尺バーつき。位置は一次情報が公開する値のみ。",
           font=f_tiny, fill=(160, 170, 195))

    # --- LM とローバーを結ぶ（公開されている端点間だけ） ---
    lm = next((a for a in drawn if a.get("kind") == "lm"), None)
    rover = next((a for a in drawn if a.get("kind") == "rover"), None)
    if lm and rover:
        p1 = to_px(lm["east_m"], lm["north_m"])
        p2 = to_px(rover["east_m"], rover["north_m"])
        d.line([p1, p2], fill=(255, 140, 70), width=3)

    # --- 機材 ---
    # 近い機材どうしが重ならないよう、種類ごとに半径を変える（実測）。
    # **LM とローバーはローバーが無い任務でも必ず描く**（実測: A12 で LM が消えていた）。
    _RAD = {"lm": 11, "rover": 10, "alsep": 8, "lrrr": 8, "flag": 6, "other": 7}
    placed = []
    for a in drawn:
        px, py = to_px(a["east_m"], a["north_m"])
        col = KIND_COLORS.get(a.get("kind"), KIND_COLORS["other"])
        rr = _RAD.get(a.get("kind"), 7)
        d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=col, outline=(255, 255, 255), width=2)
        placed.append((px, py, rr, col, a))

    # --- 機材名ラベル（重ならないよう、左右交互にずらす） ---
    labels = []
    for (px, py, rr, col, a) in placed:
        lab = _short_label(a)
        tw = d.textlength(lab, font=f_tiny)
        side = 1
        for (ox, oy, ow, oh) in labels:
            if abs(px + side * (rr + 6) - ox) < max(ow, tw) and abs(py - oy) < oh:
                side = -side
                break
        lx = px + side * (rr + 6)
        anchor = "lm" if side > 0 else "rm"
        d.text((lx, py), lab, font=f_tiny, fill=(228, 234, 248), anchor=anchor)
        labels.append((lx, py, tw, f_tiny.size))

    # --- 見出し・凡例（上部の帯。図体とは重ならない） ---
    ly = 12
    d.text((16, ly), title or site.get("ja", ""), font=f_title, fill=(255, 255, 255))
    ly += f_title.size + 6
    if subtitle:
        d.text((16, ly), subtitle, font=f_sm, fill=(190, 198, 220))
        ly += f_sm.size + 4
    for row in rows:
        lx = 16
        for shape, key, lab in row:
            col = KIND_COLORS.get(key, KIND_COLORS["other"])
            if shape == "dot":
                d.ellipse([lx, ly + 2, lx + 11, ly + 13], fill=col, outline=(255, 255, 255), width=1)
            else:
                d.rectangle([lx, ly + 2, lx + 11, ly + 13], outline=col, width=2)
            d.text((lx + 17, ly), lab, font=f_tiny, fill=(215, 222, 240))
            lx += 17 + d.textlength(lab, font=f_tiny) + 16
        ly += legend_row_h

    return {"img": img, "scale_bar_m": bar_m, "px_per_m": px_per_m,
            "body_box": (0, int(top), w, int(bot)),
            "legend_box": (0, 0, w, int(head_h)),
            "drawn": drawn, "not_drawn": not_drawn, "center": (lat0, lon0),
            "grid_step_m": step}


def render_lunar_site_figure(site: dict, *, body_cfg: dict, zoom: int = 6,
                              span_deg: float = 1.0, out_px: int = 1200,
                              radius_m: float = MOON_RADIUS_M,
                              nearby: Optional[list] = None) -> dict:
    """月面遺跡の 2 段構え図（位置図 + 局地サイトプラン）を 1 枚の画像にする。

    左: 天体面地図（等角図法）— 着陸地点の住所と近隣の着陸地点
    右: 局地サイトプラン（メートル単位・北が上）— 一次情報が公開する
        「距離＋方位」だけから各機材の位置を確定して描く

    戻り: {"img","locator","plan","notes","markers"}
    """
    from PIL import Image, ImageDraw
    from .surface_map import surface_view, tile_failure_note
    from .img_common import load_font

    lat0, lon0 = site_center(site)
    # --- 1) 位置図 ---
    sv = surface_view(body_cfg, lon0, lat0, span_deg, zoom, out_px, regional_dim=None)
    loc = sv["img"]
    ld = ImageDraw.Draw(loc)
    lf_mid = load_font(max(14, out_px // 60), bold=True)
    lf_sm = load_font(max(11, out_px // 90))

    # 近隣の着陸地点（例: サーベイヤー3号など）を faint な点で描く
    for nb in (nearby or []):
        nx, ny = sv["g2px"](float(nb["lon"]), float(nb["lat"]))
        if 0 <= nx < loc.size[0] and 0 <= ny < loc.size[1]:
            ld.ellipse([nx - 5, ny - 5, nx + 5, ny + 5], outline=(255, 220, 90), width=2)
            ld.text((nx + 8, ny - 8), nb.get("ja", ""), font=lf_sm, fill=(255, 220, 90))
    # 主地点
    mx, my = sv["g2px"](lon0, lat0)
    ld.ellipse([mx - 9, my - 9, mx + 9, my + 9], fill=(255, 40, 30), outline=(255, 255, 255), width=3)
    ld.text((mx + 12, my - 12), site.get("ja", ""), font=lf_mid, fill=(255, 255, 255))

    # --- 2) 局地サイトプラン ---
    # 位置図と同じ高さ（= 幅）に合わせる。短いと合成後に黒い帯が残る（実測）。
    plan_res = render_site_plan(
        site, radius_m=radius_m, out_px=out_px,
        title=site.get("ja", "") + " 局地サイトプラン",
        subtitle="{} / {}".format(site.get("place", ""), site.get("landed", "")))
    plan = plan_res["img"]

    # --- 合成（左右 2 枚）---
    gap = 8
    total_w = loc.size[0] + plan.size[0] + gap
    total_h = max(loc.size[1], plan.size[1])
    canvas = Image.new("RGB", (total_w, total_h), (8, 10, 16))
    canvas.paste(loc, (0, 0))
    canvas.paste(plan, (loc.size[0] + gap, 0))
    return {"img": canvas, "locator": loc, "plan": plan,
            "locator_view": sv, "plan_res": plan_res,
            "tile_failure": tile_failure_note(sv)}

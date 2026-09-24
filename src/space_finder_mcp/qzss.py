"""みちびき（準天頂衛星システム QZSS, 内閣府）の公開アーカイブ API（認証不要）。

`https://sys.qzss.go.jp/dod/api/` の `search/<種別>`（XML の ID 一覧）と
`get/<種別>[?id=]`（生データ本体。id を省略すると最新）を使う。

**⚠️ 提供元が「提供する全てのAPIは非商用の目的のみご利用頂けます」と明記**しており、
API の動作の完全性・データの正確性も保証していない。本ツールは応答にその旨と出典を
必ず含める（商用利用の判断は利用者側の責任）。

実測（2026-09-24）:
- `search/*` は ID を**新しい順**に最大 100 件返す。`since_datetime`（UTC）等で絞れる。
- `get/ultra-rapid-sp3` は 536 KB の SP3（192 エポック × 900 秒 ＝ 48 時間分）。
  収録は GPS 29〜31 機（実測 29 機）＋ **QZSS 5 機（J02/J03/J04/J07/J08）**。
- SP3 の「J + 番号」は QZSS の PRN 192+n に対応する。公表の PRN 表（内閣府
  qzss.go.jp/en/technical/satellites/）と突き合わせると
  J02=194→みちびき2号, J03=195→みちびき4号, J04=196→みちびき1号後継機,
  J07=199→みちびき3号, J08=200→みちびき6号。
  実測でも裏づく: J07/J08 は地心距離が 42158〜42172 km でほぼ一定＝**静止軌道**で、
  公表表で静止軌道なのは 199(QZS03) と 200(QZS06) だけ。J02/J03/J04 は
  39005〜45331 km で変動＝準天頂軌道（8 の字）。

出典: 内閣府 準天頂衛星システム（https://sys.qzss.go.jp/dod/api.html）
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache
from .input_utils import as_int

BASE = "https://sys.qzss.go.jp/dod/api"
UA = {"User-Agent": "space-finder-mcp/0.38 (MCP; QZSS archive API)"}
TIMEOUT = (10, 45)

# 種別 → (API のパス要素, 表示名, 更新間隔)
PRODUCTS = {
    "orbit": ("ultra-rapid-sp3", "QZU 超速報 軌道（SP3）", "3時間ごと"),
    "rapid-orbit": ("rapid-sp3", "QZR 速報 軌道（SP3）", "日次"),
    "final-orbit": ("final-sp3", "QZF 最終 軌道（SP3）", "週次"),
    "clock": ("rapid-clk", "QZR 速報 クロック", "日次"),
    "erp": ("ultra-rapid-erp", "QZU 超速報 地球回転パラメータ", "3時間ごと"),
    "almanac": ("almanac", "アルマナック（QZSS+GPS）", "日次"),
    "ephemeris": ("ephemeris", "エフェメリス（QZS+GPS, RINEX）", "日次"),
    "ephemeris-qzss": ("ephemeris-qzss", "エフェメリス（QZS のみ, RINEX）", "日次"),
    "l1s": ("l1s", "L1S サブメートル級補強", "日次"),
    "l6": ("l6", "L6 センチメートル級補強", "日次"),
    "anpi": ("anpi", "安否確認サービス（Q-ANPI）", "1時間ごと"),
    "naqu": ("naqu", "NAQU 情報", "日次"),
}

# SP3 の衛星 ID → (PRN, 機体名, 形式名, 軌道種別)。内閣府の PRN 表と対応（上記 docstring 参照）
QZSS_SATS = {
    "J01": ("193", "みちびき1号", "QZS-1", "準天頂軌道（退役）"),
    "J02": ("194", "みちびき2号", "QZS-2", "準天頂軌道"),
    "J03": ("195", "みちびき4号", "QZS-4", "準天頂軌道"),
    "J04": ("196", "みちびき1号後継機", "QZS-1R", "準天頂軌道"),
    "J07": ("199", "みちびき3号", "QZS-3", "静止軌道"),
    "J08": ("200", "みちびき6号", "QZS-6", "静止軌道"),
    "J09": ("201", "みちびき7号", "QZS-7", "準静止軌道"),
}

# WGS84（km）
_A = 6378.137
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)
TOKYO = (35.68, 139.69)

_EPOCH_RE = re.compile(
    r"^\*\s+(\d{4})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)")


def _fetch(path: str, params: Optional[dict] = None) -> tuple:
    """QZSS API を叩いて (本文, エラー) を返す。"""
    try:
        r = requests.get("{}/{}".format(BASE, path), params=params or {},
                         headers=UA, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, "QZSS API に接続できません: {}".format(str(e)[:120])
    if r.status_code != 200:
        return None, "QZSS API が status={} を返しました".format(r.status_code)
    return r.text, None


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=lambda r: not r)
def _search_ids(segment: str, days: int = 7) -> list:
    """公開アーカイブの ID 一覧を新しい順に取る（days で期間を絞る）。"""
    since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    text, err = _fetch("search/{}".format(segment), {"since_datetime": since})
    if err or not text:
        text, err = _fetch("search/{}".format(segment))       # 期間指定が効かない種別の保険
    if err or not text:
        return []
    return re.findall(r"<id>([^<]+)</id>", text)


@ttl_cache(TTL_SHORT, maxsize=16, skip_if=lambda r: not r)
def _product_text(segment: str, product_id: Optional[str]) -> str:
    """製品本体を取得する（product_id を省略すると最新）。SP3/RINEX などのテキスト。"""
    text, err = _fetch("get/{}".format(segment),
                       {"id": product_id} if product_id else None)
    return text or ""


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple:
    """ECEF（km）→ (緯度°, 経度°, 楕円体高 km)。反復法（WGS84）。"""
    lon = math.degrees(math.atan2(y, x))
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - _E2))
    h = 0.0
    for _ in range(6):
        s = math.sin(lat)
        n = _A / math.sqrt(1.0 - _E2 * s * s)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - _E2 * n / (n + h)))
    s = math.sin(lat)
    n = _A / math.sqrt(1.0 - _E2 * s * s)
    return math.degrees(lat), lon, p / math.cos(lat) - n


def _look_angles(x: float, y: float, z: float, lat0: float, lon0: float) -> tuple:
    """観測点（緯度・経度, 楕円体高0）から見た (仰角°, 方位角°, 距離 km)。"""
    slat, clat = math.sin(math.radians(lat0)), math.cos(math.radians(lat0))
    slon, clon = math.sin(math.radians(lon0)), math.cos(math.radians(lon0))
    n = _A / math.sqrt(1.0 - _E2 * slat * slat)
    dx = x - n * clat * clon
    dy = y - n * clat * slon
    dz = z - n * (1.0 - _E2) * slat
    e = -slon * dx + clon * dy
    nn = -slat * clon * dx - slat * slon * dy + clat * dz
    u = clat * clon * dx + clat * slon * dy + slat * dz
    rng = math.sqrt(e * e + nn * nn + u * u)
    el = math.degrees(math.asin(u / rng)) if rng else 0.0
    az = math.degrees(math.atan2(e, nn)) % 360.0
    return el, az, rng


def _parse_sp3(text: str) -> dict:
    """SP3 を解析して「エポック一覧」と「衛星ごとの ECEF 系列」を返す。

    SP3 は「ヘッダ（#/%% 行）→ エポック行（*）→ そのエポックの全衛星（P）→ 次の
    エポック…」の順で並ぶ。ヘッダには P で始まる行が無いので、最初の * 行より前は
    読まない（#cP…ORBIT 行の列位置は製品によって揺れるため当てにしない）。
    """
    epochs, sats, in_data = [], {}, False
    for line in text.splitlines():
        if line.startswith("*"):
            m = _EPOCH_RE.match(line)
            if m:
                y, mo, d, hh, mi = (int(m.group(i)) for i in range(1, 6))
                epochs.append(_dt.datetime(y, mo, d, hh, mi,
                                           int(float(m.group(6))), tzinfo=_dt.timezone.utc))
                in_data = True
            continue
        if in_data and line.startswith("P") and len(line) >= 46:
            sats.setdefault(line[1:4], []).append(
                (float(line[4:18]), float(line[18:32]), float(line[32:46])))
    return {"epochs": epochs, "sats": sats}


def _fmt_epoch(t: _dt.datetime) -> str:
    """エポック（UTC）を表示用に整形する（JST も併記）。"""
    jst = t + _dt.timedelta(hours=9)
    return "{} UTC（{} JST）".format(t.strftime("%Y-%m-%d %H:%M"), jst.strftime("%m-%d %H:%M"))


# 表示用の記号（ソースを ASCII 安全に保つためエスケープで書く）
WARN = "\u26a0\ufe0f "


# 現在位置（取得時点のエポック）を返すツールなので、結果はキャッシュしない
# （規約6）。ネットワーク往復は `_search_ids` / `_product_text` が製品 ID 単位で
# キャッシュするため、再呼び出しは再取得にならない。
def qzss_status(kind: str = "orbit", count: int = 5,
                product_id: Optional[str] = None) -> CallToolResult:
    """みちびき（準天頂衛星システム QZSS）の軌道・補強信号データを取得する（認証不要）。

    内閣府の公開アーカイブ API を使う。既定（kind="orbit"）では最新の超速報 SP3
    （48 時間分・15 分間隔の精密軌道）を解析し、みちびき各機の現在位置（緯度・経度・
    高度）と準天頂軌道の 8 の字の振れ幅、東京からの仰角・方位を返す。

    例:「みちびきは今どのあたりにある？」「準天頂衛星の軌道データ」「みちびきの
    アルマナック」「安否確認サービス（Q-ANPI）の最新ファイル」

    Args:
        kind: データ種別。既定 "orbit"（QZU 超速報 軌道 SP3）。他に rapid-orbit /
            final-orbit / clock / erp / almanac / ephemeris / ephemeris-qzss /
            l1s / l6 / anpi（安否確認サービス）/ naqu。
            orbit 以外は最新ファイルの一覧とダウンロードURLを返す（本体は取得しない）。
            未知の種別は候補一覧を返して停止する。
        count: 一覧に出すファイル数（既定 5, 最大 20）。orbit 以外で使う。
        product_id: 特定のファイル ID（例 "qzu24373_18.sp3"）。省略時は最新。

    注意: この API は非商用利用のみ（提供元の明記）で、データの正確性は保証されない。
    出典: 内閣府 準天頂衛星システム（https://sys.qzss.go.jp/dod/api.html）。
    """
    n = as_int(count, 5, 1, 20)
    pid = " ".join(str(product_id or "").split()) or None
    key = " ".join(str(kind or "").split()).lower()
    if key not in PRODUCTS:
        seg_to_key = {v[0]: k for k, v in PRODUCTS.items()}
        key = seg_to_key.get(key, "")
    if not key:
        cands = [{"kind": k, "label": v[1], "update": v[2]} for k, v in PRODUCTS.items()]
        lines = ["データ種別「{}」は用意されていません。候補を提示します"
                 "（推測はしません）:".format(kind), ""]
        lines += ["- **{}** — {}（{}）".format(c["kind"], c["label"], c["update"])
                  for c in cands]
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structuredContent={"count": 0, "candidates": cands, "kind": str(kind),
                               "source": "QZSS (Cabinet Office, Japan)"})

    segment, label, cadence = PRODUCTS[key]
    ids = _search_ids(segment)
    license_note = ("この API は提供元（内閣府）が非商用利用のみと明記しており、"
                    "データの正確性・API の完全性は保証されません。")
    header = ["みちびき（QZSS）アーカイブ: {} — {}".format(label, segment)]
    links = []
    for fid in ids[:n]:
        links.append({"id": fid, "url": "{}/get/{}?id={}".format(BASE, segment, fid)})

    if key != "orbit":
        lines = list(header)
        if not ids:
            lines.append("")
            lines.append("最近のファイルが見つかりませんでした（期間・種別をご確認ください）。")
        else:
            lines.append("最新ファイル: {}".format(ids[0]))
            lines.append("")
            lines.append("最近のファイル（{}件 / 全{}件）:".format(len(links), len(ids)))
            for item in links:
                lines.append("- {} ｜ ダウンロード: {}".format(item["id"], item["url"]))
            lines.append("")
            lines.append("最新版の URL は {}/get/{} です（id を省略すると最新）。".format(
                BASE, segment))
        lines.append("")
        lines.append("更新間隔の目安: {}".format(cadence))
        lines.append(WARN + license_note)
        lines.append("出典: 内閣府 準天頂衛星システム 公開アーカイブ API")
        lines.append("https://sys.qzss.go.jp/dod/api.html")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structuredContent={"kind": key, "segment": segment, "label": label,
                               "update_interval": cadence, "files": links,
                               "count": len(links), "latest_id": ids[0] if ids else None,
                               "license": license_note,
                               "source": "QZSS (Cabinet Office, Japan)",
                               "api_url": "https://sys.qzss.go.jp/dod/api.html"})

    product = pid or (ids[0] if ids else None)
    text = _product_text(segment, product)
    if not text:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="SP3（精密軌道）を取得できませんでした。" + license_note)],
            structuredContent={"error": "sp3 unavailable", "kind": key, "segment": segment,
                               "product_id": product,
                               "source": "QZSS (Cabinet Office, Japan)"})
    sp3 = _parse_sp3(text)
    epochs, sats = sp3["epochs"], sp3["sats"]
    if not epochs or not sats:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="SP3 の解析に失敗しました（想定と違う形式です）。")],
            structuredContent={"error": "sp3 parse failed", "kind": key, "segment": segment,
                               "product_id": product,
                               "source": "QZSS (Cabinet Office, Japan)"})

    now = _dt.datetime.now(_dt.timezone.utc)
    idx = min(range(len(epochs)), key=lambda i: abs((epochs[i] - now).total_seconds()))
    epoch_used = epochs[idx]
    delta_min = (epoch_used - now).total_seconds() / 60.0
    gap = int((epochs[1] - epochs[0]).total_seconds()) if len(epochs) > 1 else 0
    qzss = sorted(s for s in sats if s.startswith("J"))
    gps = sorted(s for s in sats if s.startswith("G"))

    lines = list(header)
    lines.append("製品: {}（{} エポック × {} 秒 ≒ {:.0f} 時間分）".format(
        product or "最新", len(epochs), gap, (epochs[-1] - epochs[0]).total_seconds() / 3600.0))
    lines.append("有効期間: {} 〜 {}".format(_fmt_epoch(epochs[0]), _fmt_epoch(epochs[-1])))
    lines.append("採用エポック: {}（現在との差 {:.0f} 分）".format(
        _fmt_epoch(epoch_used), delta_min))
    lines.append("収録衛星: QZSS {}機 / GPS {}機".format(len(qzss), len(gps)))
    lines.append("")

    records = []
    for sat in qzss:
        series = sats.get(sat) or []
        if not series:
            continue
        prn, ja, formal, orbit = QZSS_SATS.get(
            sat, ("不明", "QZSS {}".format(sat), sat, "公表の PRN 表に無い ID"))
        mapped = "公表の PRN 表に無い ID" not in orbit
        geo_all = [_ecef_to_geodetic(*p) for p in series]
        lats = [g[0] for g in geo_all]
        alts = [g[2] for g in geo_all]
        radii = [math.sqrt(sum(c * c for c in p)) for p in series]
        j = min(idx, len(series) - 1)
        lat, lon, alt = _ecef_to_geodetic(*series[j])
        el, az, _rng = _look_angles(series[j][0], series[j][1], series[j][2],
                                    TOKYO[0], TOKYO[1])
        lines.append("{} {}（{}）— PRN {}".format(ja, formal, sat, prn))
        lines.append("    - 軌道種別: {}".format(orbit))
        if not mapped:
            lines.append("    - " + WARN + "SP3 の ID は公表の PRN 表と対応づけられていないため、"
                         "推測せず ID のまま表示します")
        lines.append("    - 現在位置（採用エポック）: 緯度 {:.2f}° / 経度 {:.2f}° / "
                     "高度 {:.0f} km（地心距離 {:.0f} km）".format(lat, lon, alt, radii[j]))
        lines.append("    - 東京からの見え方: 仰角 {:.1f}° / 方位 {:.1f}°{}".format(
            el, az, "（地平線より下）" if el < 0 else ""))
        lines.append("    - 期間内の振れ幅: 緯度 {:.1f}°〜{:.1f}° / 高度 {:.0f}〜{:.0f} km".format(
            min(lats), max(lats), min(alts), max(alts)))
        records.append({"sp3_id": sat, "prn": prn, "name": ja, "formal_name": formal,
                        "orbit_type": orbit, "mapped_to_official_table": mapped,
                        "latitude_deg": round(lat, 3), "longitude_deg": round(lon, 3),
                        "altitude_km": round(alt, 1),
                        "geocentric_radius_km": round(radii[j], 1),
                        "elevation_from_tokyo_deg": round(el, 1),
                        "azimuth_from_tokyo_deg": round(az, 1),
                        "latitude_range_deg": [round(min(lats), 2), round(max(lats), 2)],
                        "altitude_range_km": [round(min(alts), 1), round(max(alts), 1)]})

    conse = [r["name"] for r in records if r["orbit_type"].startswith("静止")]
    zeni = [r["name"] for r in records if r["orbit_type"].startswith("準天頂")]
    lines.append("")
    lines.append("読み方: 準天頂軌道の機体は 8 の字を描くため緯度が大きく振れ、日本の真上に"
                 "長く留まります。静止軌道の機体（{}）は緯度がほぼ 0° で経度も一定です。".format(
                     "・".join(conse) if conse else "該当なし"))
    if zeni:
        lines.append("この製品に含まれる準天頂軌道の機体: " + "・".join(zeni))
    lines.append("")
    lines.append(WARN + license_note)
    lines.append("出典: 内閣府 準天頂衛星システム 公開アーカイブ API"
                 "（SP3 = 精密軌道。座標系は ECEF、単位 km）")
    lines.append("製品のダウンロード: {}/get/{}".format(BASE, segment))
    lines.append("https://sys.qzss.go.jp/dod/api.html")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"kind": key, "segment": segment, "label": label,
                           "product_id": product, "epoch_count": len(epochs),
                           "epoch_interval_s": gap or None,
                           "epoch_used": epoch_used.isoformat(),
                           "epoch_used_delta_minutes": round(delta_min, 1),
                           "epoch_used_is_within_product": epochs[0] <= now <= epochs[-1],
                           "product_epoch_start": epochs[0].isoformat(),
                           "product_epoch_end": epochs[-1].isoformat(),
                           "satellites": records, "qzss_count": len(qzss),
                           "gps_count": len(gps),
                           "look_angles_from": {"place": "東京", "lat": TOKYO[0],
                                                "lon": TOKYO[1]},
                           "files": links, "license": license_note,
                           "source": "QZSS (Cabinet Office, Japan)",
                           "api_url": "https://sys.qzss.go.jp/dod/api.html"}
)

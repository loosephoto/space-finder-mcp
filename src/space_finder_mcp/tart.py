"""TART（Transient Array Radio Telescope, オープンソース教育用電波望遠鏡）。

ニュージーランド・ダニーデンのオタゴ大学を中心に開発された、オープンソースの
合成開口電波干渉計。誰でも認証なしで、観測地（地点）ごとに「その電波望遠鏡で
いま観測できる電波源（主に GNSS・静止衛星などの人工衛星）」を JSON API で得られる。

可視電波源カタログ API（検証済み・認証不要）:
  GET https://tart.elec.ac.nz/catalog/catalog?lat=..&lon=..&alt=..&ele=..
  -> [{name, el(仰角°), az(方位角°), r(距離m), jy(フラックス密度)}]
※ 電波強度（可視性）API（api.elec.ac.nz/tart/...）は 2026-09 時点で応答なしのため未使用。
出典: https://tart.elec.ac.nz（TART project, オープンソース）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent
from .input_utils import as_float, as_int

CATALOG = "https://tart.elec.ac.nz/catalog/catalog"
UA = {"User-Agent": "space-finder-mcp/0.18 (MCP; TART source catalog)"}

# TART 本体があるダニーデン近郊（NZ）の座標（既定の観測地）
DEFAULT_LAT = -45.87
DEFAULT_LON = 170.60
DEFAULT_ALT = 100


def _cardinal(az: float) -> str:
    names = ["北", "北東", "東", "南東", "南", "南西", "西", "北西", "北"]
    return names[int((az + 22.5) // 45) % 9]


def radio_sources_now(lat: Optional[float] = None,
                      lon: Optional[float] = None,
                      alt: float = DEFAULT_ALT,
                      elevation: float = 10.0,
                      limit: int = 15) -> CallToolResult:
    """TART オープン電波望遠鏡が「いま観測できる電波源」を返す（認証不要）。

    教育・研究用にオープンソース公開されている電波干渉計 TART の可視電波源
    カタログから、指定した観測地点の地平線より上にある電波源（GNSS・静止衛星等の
    人工衛星）をリアルタイムに取得する。電波天文・衛星追尾の学習用。

    例:「いま上空に何が見える？」「ダニーデンの電波望遠鏡が見ている衛星」
    「いま観測できる電波源」 認証不要。

    Args:
        lat: 観測地点の緯度（度）。省略時は TART 本体のある NZ（-45.87）。
        lon: 観測地点の経度（度）。省略時は NZ（170.60）。
        alt: 観測地点の標高（m, 既定 100）。
        elevation: 地平線からの最小仰角（度, 既定 10。高いほど地平線近くを除外）。
        limit: 返す件数（既定 15、最大 50）。
    """
    if (lat is not None and as_float(lat) is None) or (lon is not None and as_float(lon) is None):
        return CallToolResult(
            content=[TextContent(type="text", text="lat（-90〜90）と lon（-180〜180）は数値（度）で指定してください。")],
            structuredContent={"error": "invalid coordinates", "lat": str(lat), "lon": str(lon)},
        )
    lat = as_float(lat, DEFAULT_LAT, -90.0, 90.0)
    lon = as_float(lon, DEFAULT_LON, -180.0, 180.0)
    limit = as_int(limit, 15, 1, 50)
    try:
        r = requests.get(CATALOG, params={"lat": lat, "lon": lon,
                                          "alt": as_float(alt, DEFAULT_ALT, -500.0, 9000.0),
                                          "ele": as_float(elevation, 10.0, 0.0, 90.0)},
                         headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text=f"TART 電波源カタログ取得に失敗しました。{str(e)[:100]}")],
            structuredContent={"error": str(e)[:200], "source": "tart.elec.ac.nz/catalog"},
        )
    except ValueError:
        return CallToolResult(
            content=[TextContent(type="text", text="TART の応答を解析できませんでした。")],
            structuredContent={"error": "invalid json", "source": "tart.elec.ac.nz/catalog"},
        )
    if not isinstance(data, list) or not data:
        return CallToolResult(
            content=[TextContent(type="text", text="指定条件では観測できる電波源が見つかりませんでした（仰角を下げてください）。")],
            structuredContent={"count": 0, "source": "tart.elec.ac.nz/catalog"},
        )
    # 仰角の高い順に並べ替え
    srcs = sorted(data, key=lambda s: float(s.get("el", 0)), reverse=True)[:limit]
    lines = [f"📡 **TART 電波望遠鏡 可視電波源**"
             f"（観測地 {lat:.2f}°, {lon:.2f}° ・仰角{elevation}°以上・{len(srcs)}件）:"]
    records = []
    for s in srcs:
        name = s.get("name", "不明")
        el = _f(s.get("el")); az = _f(s.get("az")); rng = _f(s.get("r")); jy = _f(s.get("jy"))
        dist_km = f"{float(s['r']) / 1000:,.0f} km" if isinstance(s.get("r"), (int, float)) else ""
        rec = {"name": name, "elevation_deg": el, "azimuth_deg": az,
               "direction": _cardinal(az) if isinstance(az, (int, float)) else "",
               "range_km": (round(float(s["r"]) / 1000) if isinstance(s.get("r"), (int, float)) else None),
               "flux_density_jy": jy}
        records.append(rec)
        jy_txt = f", {jy:,.0f} Jy" if isinstance(jy, (int, float)) else ""
        extra = "".join(x for x in [(" " + dist_km) if dist_km else "", jy_txt])
        lines.append(f"- **{name}** 仰角 {el}°・{_cardinal(az) if isinstance(az,(int,float)) else ''}方位 {az}°{extra}")
    advice = ("🤖 【AIからのインテリジェントアドバイス】TART は教育用に公開された電波干渉計で、"
              "返る電波源は主に GNSS・放送・静止通信衛星です（人工衛星は地上の電波望遠鏡に強い信号を"
              "届けます）。フラックス密度 Jy が大きいほど受信しやすい、仰角が低いほど大気の影響を受けます。"
              "学習や自作電波望遠鏡の動作確認に最適なオープンデータです。")
    lines.append(advice)
    lines.append("出典: https://tart.elec.ac.nz/catalog（TART project, オープンソース）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"observatory": "TART (Dunedin, NZ, open-source)",
                           "observer_lat": lat, "observer_lon": lon,
                           "min_elevation_deg": elevation, "count": len(records),
                           "sources": records, "source": "tart.elec.ac.nz/catalog"},
    )


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

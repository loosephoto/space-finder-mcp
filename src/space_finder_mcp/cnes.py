"""CNES（フランス国立宇宙研究センター）地球観測データ。

THEIA ランドデータセンター（theia.cnes.fr）の Sentinel・Venμs 等のデータと
GEODES ポータル（geodes-portal.cnes.fr）の概要を返す。
画像ダウンロードには CNES アカウント登録が必要。本モジュールはポータル到達性と
データセット概要の確認を提供する。
"""
from __future__ import annotations

import requests
from mcp.types import CallToolResult, TextContent

THEIA = "https://theia.cnes.fr"
GEODES = "https://geodes-portal.cnes.fr"
UA = {"User-Agent": "space-finder-mcp/0.3 (MCP; CNES THEIA/GEODES)"}


def cnes_status() -> CallToolResult:
    """フランス CNES の地球観測データポータル（THEIA / GEODES）の到達状態と概要を返す。

    例:「フランスのCNES衛星データ」「THEIAポータル」
    画像ダウンロードには CNES アカウント登録が必要。ここではポータルの状態と
    提供データの概要を確認する。

    Returns:
        CallToolResult: 表示用サマリ + JSON。
    """
    report = {}
    for name, url in (("THEIA", THEIA), ("GEODES", GEODES)):
        try:
            r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
            report[name] = {"reachable": True, "http": r.status_code, "url": r.url}
        except requests.RequestException as e:
            report[name] = {"reachable": False, "error": str(e)[:120]}
    all_ok = all(v.get("reachable") for v in report.values())
    lines = [f"フランス CNES 地球観測ポータル（{'利用可' if all_ok else '一部利用不可'}）:"]
    lines.append("- **THEIA ランドデータセンター** (theia.cnes.fr): Sentinel-2 高解像度土壌・土地被覆・植生データ、Venμs")
    lines.append("- **GEODES ポータル** (geodes-portal.cnes.fr): CNES の地球観測衛星データ（Sentinel ほか）の一括アクセス")
    for name, v in report.items():
        status = f"HTTP {v['http']}" if v.get("reachable") else "不通"
        lines.append(f"  {name}: {status}")
    lines.append("注意: 画像のダウンロードには CNES アカウント登録（無料）が必要です。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"source": "cnes.fr", "portals": report},
    )

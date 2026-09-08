"""中国（CNSA）衛星データポータルの到達状態と概要。

風雲（FengYun/FY）気象衛星（NSMC 中国国家衛星気象センター）・高分（Gaofen）/CBERS
地球観測（CNSA-GEO / CRESDA）等、中国の宇宙機関系データポータルの利用可否を確認する。

※ 中国側の各ポータルは一般に画像・データのダウンロードに無料アカウント登録が必要で、
   認証不要の公開 REST API は原則提供されていない。本モジュールは CNES と同様に
   ポータル到達性と提供データ概要の確認を提供する。第三者製「中国衛星API」は
   偽装・フェイクデータのリスクがあるため使わない（詳細は space-apis スキル）。
"""
from __future__ import annotations

import requests
from mcp.types import CallToolResult, TextContent

UA = {"User-Agent": "space-finder-mcp/0.10 (MCP; CNSA portal status)"}

# ポータル名 -> (URL, 提供内容の概要, 認証の要否)
PORTALS = {
    "FengYun_NSMC": {
        "url": "https://satellite.nsmc.org.cn",
        "about": "風雲（FengYun/FY）気象衛星（FY-3/FY-4 ほか）の雲画像・気象データ。中国国家衛星気象センター（NSMC）公式ポータル。画像ダウンロードは登録制。",
        "download_auth": True,
    },
    "CNSA_GEO": {
        "url": "https://www.cnsageo.com",
        "about": "高分（Gaofen）地球観測衛星のデータ共有プラットフォーム（中国国家航天局 CNSA 運営）。データ検索・品質評価・ダウンロード。利用にはアカウント登録が必要。",
        "download_auth": True,
    },
    "CRESDA": {
        "url": "https://data.cresda.cn",
        "about": "CBERS（中国・ブラジル地球資源衛星）・環境・災害観測衛星（HJ シリーズ）のデータ管理プラットフォーム（中国資源衛星応用センター CRESDA）。アジア太平洋宇宙協力機構（APSCO）・ASEAN 向け共有も。登録制。",
        "download_auth": True,
    },
}

# 認証不要で中国の宇宙・衛星データに触れる現実的な代替ルート（スキルで検証済み）
NO_AUTH_ALTERNATIVES = [
    ("Open Notify /astros", "中国宇宙ステーション（天宮/Tiangong）乗組員を含む「宇宙にいる人々」一覧（api.open-notify.org）。CNSA 公式の人員情報に登録不要で触れる現実的な方法。"),
    ("WMO OSCAR/Space", "世界気象機関の衛星カタログ。FY-3/FY-4 等の中国気象衛星の運用ステータス（space.oscar.wmo.int）。"),
    ("CelesTrak", "全衛星の軌道要素（TLE）。天宮宇宙ステーション等の中国宇宙機の軌道データ（celestrak.org）。"),
    ("Launch Library 2", "中国のロケット打ち上げ予定・実績の集約データ（thespacedevs.com）。"),
]


def cnsa_status() -> CallToolResult:
    """中国 CNSA 系衛星データポータル（風雲/NSMC・高分/CNSA-GEO・CBERS/CRESDA）の到達状態と概要を返す。

    例:「中国の衛星データ」「風雲衛星」「高分」「CBERS」
    画像ダウンロードには各ポータルへのアカウント登録が必要。ここではポータルの状態と
    提供データの概要、および認証不要の代替データ源を確認する。

    Returns:
        CallToolResult: 表示用サマリ + JSON。
    """
    report = {}
    for name, cfg in PORTALS.items():
        try:
            r = requests.get(cfg["url"], headers=UA, timeout=25, allow_redirects=True)
            report[name] = {"reachable": True, "http": r.status_code, "url": r.url,
                            "about": cfg["about"], "download_auth": cfg["download_auth"]}
        except requests.RequestException as e:
            report[name] = {"reachable": False, "error": str(e)[:120],
                            "about": cfg["about"], "download_auth": cfg["download_auth"]}

    ok_count = sum(1 for v in report.values() if v.get("reachable"))
    lines = [f"中国 CNSA 系衛星データポータル（{ok_count}/{len(report)} 到達可）:"]
    for name, cfg in PORTALS.items():
        v = report[name]
        status = f"HTTP {v['http']}" if v.get("reachable") else "不通（登録制またはアクセス制限）"
        auth = "ダウンロード要登録" if cfg["download_auth"] else "登録不要"
        lines.append(f"- **{name}** ({cfg['url']}): {status} ／ {auth}")
        lines.append(f"    {cfg['about']}")
        if not v.get("reachable") and v.get("error"):
            lines.append(f"    接続エラー: {v['error']}")
    lines.append("注意: 中国側ポータルは画像ダウンロードに無料アカウント登録が必要です。")
    lines.append("認証不要の代替（中国の宇宙・衛星データを確認する現実的な経路）:")
    for label, desc in NO_AUTH_ALTERNATIVES:
        lines.append(f"- {label}: {desc}")
    lines.append("出典: 各公式ポータル（satellite.nsmc.org.cn / cnsageo.com / data.cresda.cn）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"source": "CNSA portals", "reachable": ok_count, "total": len(report),
                           "portals": report,
                           "no_auth_alternatives": [{"label": l, "desc": d} for l, d in NO_AUTH_ALTERNATIVES]},
    )

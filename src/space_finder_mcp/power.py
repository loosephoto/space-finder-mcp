"""NASA POWER API — 過去の気候・太陽エネルギー統計（認証不要）。

NASA Langley Research Center が提供する POWER (Prediction Of Worldwide Energy
Resources) API。地球の任意地点・期間の気温・日射量・風速など、気象学と
太陽エネルギー工学に特化したデータを返す。

- 日射量: ALLSKY_SFC_SW_DWN（全天日射量）、CLRSKY_SFC_SW_DWN（快晴時日射量）
- 気温: T2M（地上2m気温）、T2M_MAX、T2M_MIN
- その他: 湿度（RH2M）、風速（WS2M）、気圧（PS）、降水（PRECTOTCORR）

※ 過去実績データ（通常 ~数日〜数ヶ月前まで）が対象。未来予報は含まれない。
※ 欠損値は -999.0 で返る。
出典: power.larc.nasa.gov（NASA/POWER, CC BY 4.0 準拠のNASAデータ利用条件）
"""
from __future__ import annotations

from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

API = "https://power.larc.nasa.gov/api/temporal/daily/point"
UA = {"User-Agent": "space-finder-mcp/0.12 (MCP; NASA POWER climate)"}

# パラメータ名 -> (日本語表示名, 単位)
PARAMS = {
    "T2M": ("地上2m気温", "℃"),
    "T2M_MAX": ("最高気温(2m)", "℃"),
    "T2M_MIN": ("最低気温(2m)", "℃"),
    "ALLSKY_SFC_SW_DWN": ("全天日射量", "kWh/m²/日"),
    "CLRSKY_SFC_SW_DWN": ("快晴時日射量", "kWh/m²/日"),
    "RH2M": ("相対湿度(2m)", "%"),
    "WS2M": ("風速(2m)", "m/s"),
    "PS": ("気圧", "kPa"),
    "PRECTOTCORR": ("降水量(補正)", "mm/日"),
}

# 既定で取得する主要パラメータ
_DEFAULT = "T2M,T2M_MAX,T2M_MIN,ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,WS2M"


def _stat(values) -> dict:
    """欠損値(-999)を除外した統計（平均・最大・最小・日数）。

    POWER API は data[name] が {日付: 値} の dict を返す（値は数値または文字列）。
    リストにも対応。
    """
    raw = values.values() if isinstance(values, dict) else values
    vals = []
    for v in raw:
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == -999.0:
            continue
        vals.append(fv)
    if not vals:
        return {"avg": None, "max": None, "min": None, "days": 0}
    return {"avg": round(sum(vals) / len(vals), 2),
            "max": max(vals), "min": min(vals), "days": len(vals)}


def power_climate(latitude: float, longitude: float,
                  start: Optional[str] = None, end: Optional[str] = None,
                  parameters: Optional[str] = None) -> CallToolResult:
    """NASA POWER API で任意地点の過去の気候・太陽エネルギー統計を返す（認証不要）。

    例:「東京の過去1年間の日射量」「ハワイの気温データ」「太陽光発電の立地評価」
    過去の日別データ（通常数日〜数ヶ月前まで）の期間平均を計算して返す。

    Args:
        latitude: 緯度（例 東京 35.68）。
        longitude: 経度（例 東京 139.69）。
        start: 開始日 "YYYY-MM-DD"。省略で1年前。
        end: 終了日 "YYYY-MM-DD"。省略で直近データ。
        parameters: カンマ区切りのパラメータ（例 "T2M,ALLSKY_SFC_SW_DWN"）。
            省略で主要パラメータ（気温・日射量・風速）。
    """
    import datetime as dt
    if latitude is None or longitude is None:
        return CallToolResult(
            content=[TextContent(type="text", text="緯度(latitude)と経度(longitude)を指定してください。例: latitude=35.68, longitude=139.69")],
            structuredContent={"error": "location required"},
        )
    # 日付のデフォルト（過去1年分）
    today = dt.date.today()
    if end:
        try:
            end_d = dt.date.fromisoformat(end)
        except ValueError:
            return CallToolResult(content=[TextContent(type="text", text="end は 'YYYY-MM-DD' 形式で指定してください。")], structuredContent={"error": "bad end"})
    else:
        end_d = today
    if start:
        try:
            start_d = dt.date.fromisoformat(start)
        except ValueError:
            return CallToolResult(content=[TextContent(type="text", text="start は 'YYYY-MM-DD' 形式で指定してください。")], structuredContent={"error": "bad start"})
    else:
        start_d = end_d - dt.timedelta(days=365)

    params_str = parameters or _DEFAULT
    params = {"latitude": latitude, "longitude": longitude,
              "parameters": params_str, "community": "RE",
              "start": start_d.strftime("%Y%m%d"), "end": end_d.strftime("%Y%m%d"),
              "format": "JSON"}
    try:
        r = requests.get(API, params=params, headers=UA, timeout=40)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"NASA POWER API への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "power.larc.nasa.gov"},
        )
    props = d.get("properties", {})
    data = props.get("parameter", {})
    if not data:
        return CallToolResult(
            content=[TextContent(type="text", text="POWER API からデータを取得できませんでした（範囲が最新すぎる可能性。過去の日付を指定してください）。")],
            structuredContent={"error": "no data", "start": str(start_d), "end": str(end_d)},
        )

    lines = [f"☀️ NASA POWER 気候・太陽エネルギー統計（{start_d} 〜 {end_d}, {latitude},{longitude}）:"]
    results = {}
    for name in params_str.split(","):
        name = name.strip()
        if name not in data:
            continue
        label, unit = PARAMS.get(name, (name, ""))
        st = _stat(data[name])
        results[name] = {"label": label, "unit": unit, **st}
        if st["days"] == 0:
            lines.append(f"- **{label}**: データなし（期間が最新すぎる可能性）")
            continue
        avg_s = f"{st['avg']} {unit}".strip()
        lines.append(f"- **{label}**: 平均 {avg_s}（最大 {st['max']} / 最小 {st['min']} / {st['days']}日分）")

    lines.append("🤖 【AIからのインテリジェントアドバイス】日射量(ALLSKY_SFC_SW_DWN)は太陽光発電・天文観測の日照計画に、気温・風速は屋外観測の装備選びに活用できます。過去の傾向から観測・撮影の時期を計画できます。")
    lines.append("出典: power.larc.nasa.gov（NASA POWER, 認証不要）／ 未来予報は含まれず過去実績のみ・欠損は-999。")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"latitude": latitude, "longitude": longitude,
                           "start": str(start_d), "end": str(end_d),
                           "parameters": results,
                           "source": "power.larc.nasa.gov"},
    )

"""ESO Paranal Observatory の大気・シーイング観測データ（認証不要）。

欧州南天天文台（ESO）が運用する VLT（超大型望遠鏡）の観測地であるチリ・
パラナル天文台の、リアルタイム大気環境データ（シーイング・水蒸気・気象）を返す。

出典: eso.org/asm/api（Paranal ASM Database, 公開API）
シーイング(seeing)は大気の揺らぎで、小さいほど望遠鏡の解像度が良い。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache, is_error_result
from .input_utils import as_float, as_int

API = "https://www.eso.org/asm/api/"
UA = {"User-Agent": "space-finder-mcp/0.13 (MCP; ESO Paranal ASM)"}

# フィールド名 -> (日本語表示名, 単位, 説明)
FIELDS = {
    "dimm_paranal-fwhm": ("シーイング(天頂,500nm)", "arcsec", "DIMM 大気揺らぎ。小さいほど高解像度"),
    "mass_paranal-fwhm": ("自由大気シーイング", "arcsec", "自由大気層の揺らぎ"),
    "mass_paranal-tau0": ("コヒーレンス時間", "ms", "大気の揺らぎの時定数"),
    "lhatpro_paranal-pwv0": ("可降水量(PWV)", "mm", "天頂方向の水蒸気量。赤外線観測の指標"),
    "meteo_paranal-temp1": ("気温(30m)", "℃", "観測台気温"),
    "meteo_paranal-rhum1": ("相対湿度(30m)", "%", "観測台湿度"),
    "meteo_paranal-wind_speed1": ("風速(30m)", "m/s", "観測台風速"),
    "meteo_paranal-wind_dir1": ("風向(30m)", "°", "観測台風向"),
    "meteo_paranal-press_inst": ("気圧", "hPa", "観測台気圧"),
}

# 既定で取得する主要フィールド
_DEFAULT_FIELDS = "dimm_paranal-fwhm,lhatpro_paranal-pwv0,meteo_paranal-temp1,meteo_paranal-rhum1,meteo_paranal-wind_speed1,meteo_paranal-press_inst"


def _parse_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


@ttl_cache(TTL_SHORT, maxsize=32, skip_if=is_error_result)
def eso_seeing(hours: int = 12, fields: str = _DEFAULT_FIELDS) -> CallToolResult:
    """ESO パラナル天文台（チリ, VLT）のリアルタイム大気・シーイング観測データを返す。

    例:「パラナル天文台のシーイング」「VLTの観測コンディション」「赤外線観測の水蒸気」
    認証不要。シーイング(arcsec)・可降水量(PWV)・気温・湿度・風速を返す。

    Args:
        hours: 過去何時間分を取得するか（既定 12、最大 48）。
        fields: カンマ区切りの観測フィールド。省略で主要フィールド。
    """
    hours = as_int(hours, 12, 1, 48)
    now = datetime.now(timezone.utc)
    fr = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    to = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    # 全フィールド取得してフィルタ（APIは複数fieldを,区切りで受けるが、1つずつの方が安定）
    results = {}
    errs = []
    for f in field_list:
        try:
            r = requests.get(API, params={"from": fr, "to": to, "fields": f}, headers=UA, timeout=30)
            r.raise_for_status()
            d = r.json()
            if f in d:
                results[f] = d[f]
        except requests.RequestException as e:
            errs.append(f"{f}: {str(e)[:60]}")
    if not results:
        return CallToolResult(
            content=[TextContent(type="text", text=f"ESO ASM データ取得に失敗しました。{' '.join(errs)}")],
            structuredContent={"error": errs, "source": "eso.org/asm/api"},
        )
    label, unit, desc = FIELDS.get(field_list[0], (field_list[0], "", ""))
    lines = [f"🔭 **ESO パラナル天文台（チリ, VLT） 大気コンディション**（過去{hours}時間, UTC）:"]
    summaries = {}
    for f in field_list:
        if f not in results:
            continue
        rows = results[f]
        if not rows:
            lines.append(f"- **{FIELDS.get(f, (f,'',''))[0]}**: データなし")
            continue
        label, unit, desc = FIELDS.get(f, (f, "", ""))
        vals = [x for x in (as_float(v) for _, v in rows) if x is not None]
        if vals:
            avg = sum(vals) / len(vals)
            best = min(vals) if "fwhm" in f or "pwv" in f else max(vals)
            summaries[f] = {"avg": round(avg, 3), "samples": len(vals), "unit": unit}
            lines.append(f"- **{label}**: 平均 {avg:.3f} {unit}（{len(vals)}サンプル）")
        latest_ts = _parse_ts(rows[-1][0]) if rows else ""
        lines.append(f"   最新: {rows[-1][1]} {unit}（{latest_ts} UTC）")
    # シーイング評価
    if "dimm_paranal-fwhm" in summaries:
        s = summaries["dimm_paranal-fwhm"]["avg"]
        grade = ("◎ 極上" if s < 0.6 else "○ 良好" if s < 0.9 else "△ 普通" if s < 1.2 else "× 悪い")
        lines.append(f"🤖 【AIからのインテリジェントアドバイス】現在の平均シーイングは {s:.2f} arcsec（{grade}）。0.6以下なら高分解能観測に最適、1.2超ではシーイング制限の観測は困難。可降水量(PWV)が低いほど赤外線観測に有利です。")
    lines.append("出典: eso.org/asm/api（ESO Paranal ASM 公開データ）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"observatory": "Paranal (VLT, Chile)", "hours": hours,
                           "from_utc": fr, "to_utc": to, "fields": summaries,
                           "source": "eso.org/asm/api"},
    )

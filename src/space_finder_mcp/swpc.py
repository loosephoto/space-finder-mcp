"""NOAA SWPC — 認証不要の宇宙天気リアルタイム観測（DONKI のフォールバック）。

NASA DONKI（api.nasa.gov）は DEMO_KEY が **1時間30リクエスト/IP の共有枠**で、
他のクライアントと同じ枠を取り合うため 429 になりやすい（実測: 枠を使い切ると
cooldown 中は宇宙天気が一切返せない）。NOAA の SWPC（Space Weather Prediction
Center）は **認証不要** で、現在の Kp・GOES X線フラックス・フレアイベント（直近7日）・太陽風（RTSW）・
陽子フラックス・警報・黒点相対数を JSON で公開している。そこで DONKI が
使えないときのフォールバックとして本モジュールを使う。

- 取得できなかった項目は**黙って落とさず**、どの項目が取れなかったかを返す
- 値は SWPC の生データをそのまま使い、時刻は UTC で明示する
- 出典（NOAA SWPC）とフォールバックした事実は必ず表示する

出典: services.swpc.noaa.gov（NOAA SWPC）
"""
from __future__ import annotations

import re
import time

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache

SWPC = "https://services.swpc.noaa.gov"
UA = {"User-Agent": "space-finder-mcp/0.30 (MCP; NOAA SWPC space weather)"}

# X線フラックスのクラス境界（W/m^2）。大きい方から評価する
_XRAY_DECADES = ((1e-4, "X"), (1e-5, "M"), (1e-6, "C"), (1e-7, "B"))
_FLARE_JA = {
    "A": "微小（観測機器でしか検知されない）", "B": "微弱",
    "C": "小規模（地球への大きな影響は通常なし）",
    "M": "中規模（高緯度でオーロラ・短波通信障害の可能性）",
    "X": "大規模（広域通信障害・放射線被ばくの可能性）",
}
# 地磁気嵐スケール（NOAA G スケール）
_G_SCALE_JA = {
    "0": "なし", "1": "小規模", "2": "中規模", "3": "強い", "4": "激甚", "5": "極端",
}


def _get_json(path: str):
    """SWPC の JSON エンドポイントを取得する（例外はそのまま伝播）。"""
    r = requests.get(SWPC + path, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def _xray_class(flux) -> str:
    """X線フラックス(W/m^2) を A/B/C/M/X クラス表記にする（例: 2.37e-7 → 'B2.4'）。"""
    try:
        f = float(flux)
    except (TypeError, ValueError):
        return "?"
    if f != f or f <= 0.0:            # NaN / 0 以下
        return "?"
    for base, letter in _XRAY_DECADES:
        if f >= base:
            return "{}{:.1f}".format(letter, f / base)
    return "A{:.1f}".format(f / 1e-8)


def _flare_rank(cls) -> float:
    """フレア規模（"M2.1" / "X1.0" / "?"）を比較用の数値にする（大きいほど大規模）。

    A/B/C/M/X の順に 100 点刻みで重み付けし、同じ級の中は数値で比較する。
    不明値は -1（どの級よりも小さい）として扱い、並べ替えで先頭に来ないようにする。
    """
    s = str(cls or "").strip().upper()
    if not s or s[0] not in "ABCMX":
        return -1.0
    try:
        return "ABCMX".index(s[0]) * 100.0 + float(s[1:] or 0)
    except (TypeError, ValueError):
        return "ABCMX".index(s[0]) * 100.0


def _last(rows, key=None):
    """時系列 JSON の最後（最新）の行を返す。壊れた行は無視する。"""
    for row in reversed(rows or []):
        if isinstance(row, dict) and (key is None or key in row):
            return row
    return None


def _within_hours(time_tag: str, hours: float, now=None) -> bool:
    """'2026-09-16T07:33:00' が現在から hours 時間以内かどうか。"""
    import datetime
    try:
        t = datetime.datetime.fromisoformat(str(time_tag)[:19])
    except ValueError:
        return False
    ref = now or datetime.datetime.utcnow()
    delta = (ref - t).total_seconds()
    return 0 <= delta <= hours * 3600.0


@ttl_cache(TTL_SHORT, maxsize=4)
def fetch_all() -> dict:
    """SWPC の各項目を取得して1つにまとめる。

    1項目の失敗で全体を落とさず、取れなかった項目名を failed に残す
    （黙って欠けさせると「データが無い＝静穏」と誤読される）。
    """
    sections, failed = {}, []

    def add(key, loader):
        try:
            sections[key] = loader()
        except Exception as e:            # noqa: BLE001 — 項目単位の失敗は記録して継続
            failed.append("{}: {}".format(key, str(e)[:90]))

    def scales():
        d = _get_json("/products/noaa-scales.json")

        def one(block):
            b = block or {}
            out = {}
            for k in ("R", "S", "G"):
                cell = b.get(k) or {}
                out[k] = {"scale": cell.get("Scale"), "text": cell.get("Text")}
            return out

        return {"current": one(d.get("0")),
                "forecast": {k: one(d.get(k)) for k in ("1", "2", "3") if k in d}}

    def kp():
        rows = _get_json("/json/planetary_k_index_1m.json")
        vals = [(r.get("time_tag"), r.get("kp_index")) for r in rows
                if r.get("kp_index") is not None]
        if not vals:
            raise ValueError("Kp の行が空")
        vals.sort(key=lambda x: x[0])
        last3 = [v for ts, v in vals if _within_hours(ts, 3)]
        last24 = [v for ts, v in vals if _within_hours(ts, 24)]
        return {"time_tag": vals[-1][0], "current": vals[-1][1],
                "max_3h": max(last3) if last3 else vals[-1][1],
                "max_24h": max(last24) if last24 else vals[-1][1]}

    def xray():
        rows = _get_json("/json/goes/primary/xrays-6-hour.json")
        rows = [r for r in rows if r.get("energy") == "0.1-0.8nm" and r.get("flux")]
        if not rows:
            raise ValueError("0.1-0.8nm の行が空")
        peak = max(rows, key=lambda r: r["flux"])
        return {"latest_flux": rows[-1]["flux"], "latest_class": _xray_class(rows[-1]["flux"]),
                "latest_time": rows[-1].get("time_tag"),
                "peak_flux": peak["flux"], "peak_class": _xray_class(peak["flux"]),
                "peak_time": peak.get("time_tag")}

    def wind():
        w = _get_json("/json/rtsw/rtsw_wind_1m.json")
        b = _get_json("/json/rtsw/rtsw_mag_1m.json")
        w = sorted([r for r in w if r.get("proton_speed")], key=lambda r: r.get("time_tag") or "")
        b = sorted([r for r in b if r.get("bt") is not None], key=lambda r: r.get("time_tag") or "")
        if not w or not b:
            raise ValueError("太陽風の行が空")
        tail_w, tail_b = w[-60:], b[-60:]
        return {"time_tag": w[-1].get("time_tag"), "speed_km_s": w[-1]["proton_speed"],
                "density_cm3": w[-1].get("proton_density"),
                "speed_mean_60m": round(sum(r["proton_speed"] for r in tail_w) / len(tail_w), 1),
                "bt_nt": b[-1]["bt"], "bz_gsm_nt": b[-1].get("bz_gsm"),
                "bt_mean_60m": round(sum(r["bt"] for r in tail_b) / len(tail_b), 1)}

    def proton():
        rows = _get_json("/json/goes/primary/integral-protons-1-day.json")
        f = [r for r in rows if r.get("energy") == ">=10 MeV" and r.get("flux") is not None]
        if not f:
            raise ValueError(">=10MeV の行が空")
        return {"energy": ">=10 MeV", "flux_pfu": f[-1]["flux"], "time_tag": f[-1].get("time_tag"),
                "peak_pfu": max(r["flux"] for r in f)}

    def alerts():
        rows = _get_json("/products/alerts.json")
        out = []
        for a in rows[:5]:
            msg = re.sub(r"\s+", " ", str(a.get("message") or "")).strip()
            out.append({"issued": a.get("issue_datetime"), "summary": msg[:240]})
        return out

    def flares():
        """GOES X線のフレアイベント一覧（直近7日）。

        別ファイル xray-flares-7-day.json は **フレアごとに1行**（開始/最大/終了の時刻と
        級）を返す（実測 31 行）。時間変化ではなく「発生した事象」が欲しいのでこちらを使う。
        **空配列は「7日間フレアなし」という正当な値**なので failed にしない
        （形が配列でないときだけ失敗として扱う）。
        """
        rows = _get_json("/json/goes/primary/xray-flares-7-day.json")
        if not isinstance(rows, list):
            raise ValueError("フレアイベントの応答が配列ではありません")
        events = []
        for r in rows:
            if not isinstance(r, dict) or not r.get("max_time"):
                continue
            events.append({"begin": r.get("begin_time"), "max": r.get("max_time"),
                           "end": r.get("end_time"), "max_class": r.get("max_class"),
                           "begin_class": r.get("begin_class"),
                           "satellite": r.get("satellite")})
        events.sort(key=lambda e: e.get("max") or "")
        counts = {}
        for e in events:
            letter = str(e.get("max_class") or "?")[:1]
            counts[letter] = counts.get(letter, 0) + 1
        top = max(events, key=lambda e: _flare_rank(e.get("max_class")), default={})
        return {"events": events, "count": len(events), "counts_by_class": counts,
                "max_class": top.get("max_class"), "max_time": top.get("max"),
                "satellite": top.get("satellite")}

    def cycle():
        rows = _get_json("/json/solar-cycle/observed-solar-cycle-indices.json")
        last = _last(rows, key="ssn")
        if not last:
            raise ValueError("黒点相対数の行が空")
        return {"month": last.get("time-tag"), "ssn": last.get("ssn"), "f10_7": last.get("f10.7")}

    for key, loader in (("scales", scales), ("kp", kp), ("xray", xray), ("flares", flares),
                        ("wind", wind), ("proton", proton), ("alerts", alerts),
                        ("cycle", cycle)):
        add(key, loader)
    return {"sections": sections, "failed": failed, "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# kind ごとに表示する項目（NASA DONKI の kind と同じ語彙で受ける）
_SECTIONS_BY_KIND = {
    "all": ("scales", "kp", "xray", "flares", "wind", "proton", "alerts", "cycle"),
    "flare": ("xray", "flares"),
    "gst": ("scales", "kp", "wind"),
    "sep": ("proton", "scales"),
    "cme": ("wind",),          # CME のイベント一覧はこの経路では提供していない（注記する）
}


def _r3(v) -> str:
    """小さい値（陽子フラックス等）を読みやすく丸める。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "?"
    if f >= 10:
        return "{:.0f}".format(f)
    if f >= 1:
        return "{:.1f}".format(f)
    return "{:.2f}".format(f)


def _sec_lines(key: str, sec: dict) -> list:
    """SWPC の1セクションを日本語の箇条書きにする（値は数値から生成）。"""
    if key == "scales":
        cur = {k: (sec.get("current") or {}).get(k) or {} for k in ("R", "S", "G")}
        out = ["### NOAA スケール（現在値）",
               "- R{} / S{} / G{}　（R=無線通信障害, S=放射線嵐, G=地磁気嵐）".format(
                   cur["R"].get("scale") or 0, cur["S"].get("scale") or 0, cur["G"].get("scale") or 0)]
        for day, block in sorted((sec.get("forecast") or {}).items()):
            g = str((block.get("G") or {}).get("scale") or 0)
            r = str((block.get("R") or {}).get("scale") or 0)
            s = str((block.get("S") or {}).get("scale") or 0)
            if g != "0" or r != "0" or s != "0":
                out.append("- {}日先の予測: G{}（{}）/ R{} / S{}".format(
                    day, g, _G_SCALE_JA.get(g, "-"), r, s))
        return out
    if key == "kp":
        return ["### 地磁気活動（Kp 指数）",
                "- 現在 Kp {}（{} UTC）／ 3時間最大 {} ／ 24時間最大 {}".format(
                    sec.get("current"), sec.get("time_tag"), sec.get("max_3h"), sec.get("max_24h"))]
    if key == "xray":
        cls = str(sec.get("latest_class") or "?")
        return ["### 太陽フレア（GOES X線 0.1–0.8nm）",
                "- 現在 **{}** ／ 直近6時間の最大 **{}**（{}）".format(
                    cls, sec.get("peak_class"), str(sec.get("peak_time") or "").rstrip("Z")),
                "  - {} クラス: {}".format(cls[0], _FLARE_JA.get(cls[0], "?"))]
    if key == "flares":
        out = ["### 太陽フレア（GOES X線フレアイベント・直近7日）"]
        n = int(sec.get("count") or 0)
        if not n:
            out.append("- GOES が記録したフレアイベントはありません（この7日間は静穏）。")
            return out
        counts = sec.get("counts_by_class") or {}
        breakdown = " / ".join("{}級 {}件".format(k, counts[k])
                               for k in sorted(counts, key=_flare_rank))
        out.append("- 直近7日で **{}件**（{}）／ 最大 **{}**（{} UTC）".format(
            n, breakdown, sec.get("max_class") or "?",
            str(sec.get("max_time") or "").replace("T", " ").rstrip("Z")))
        ranked = sorted((sec.get("events") or []),
                        key=lambda e: _flare_rank(e.get("max_class")), reverse=True)
        for e in ranked[:5]:
            out.append("- **{}** 最大 {} UTC（開始 {} / 終了 {}）".format(
                e.get("max_class") or "?",
                str(e.get("max") or "").replace("T", " ").rstrip("Z"),
                str(e.get("begin") or "").replace("T", " ").rstrip("Z"),
                str(e.get("end") or "").replace("T", " ").rstrip("Z")))
        if n > 5:
            out.append("- …ほか {} 件（規模の大きい順に表示）".format(n - 5))
        return out
    if key == "wind":
        return ["### 太陽風（RTSW: DSCOVR/ACE）",
                "- 速度 {:.0f} km/s（60分平均 {:.0f}）／ 密度 {} /cm³".format(
                    sec.get("speed_km_s") or 0.0, sec.get("speed_mean_60m") or 0.0,
                    sec.get("density_cm3")),
                "- 磁場 Bt {:.1f} nT（60分平均 {:.1f}）／ Bz(GSM) {} nT"
                "　※Bz が南向き（負）で大きいほど地磁気嵐が起きやすい".format(
                    sec.get("bt_nt") or 0.0, sec.get("bt_mean_60m") or 0.0, sec.get("bz_gsm_nt"))]
    if key == "proton":
        return ["### 放射線（陽子フラックス）",
                "- {}: {} pfu（直近24時間の最大 {}）".format(
                    sec.get("energy"), _r3(sec.get("flux_pfu")), _r3(sec.get("peak_pfu")))]
    if key == "alerts":
        out = ["### 警報・速報（直近）"]
        for a in (sec or [])[:3]:
            out.append("- [{}] {}".format(str(a.get("issued"))[:16], a.get("summary")))
        if not sec:
            out.append("（直近の速報はありません）")
        return out
    if key == "cycle":
        return ["### 太陽活動指数",
                "- {} の黒点相対数 {} ／ F10.7 {} sfu".format(
                    sec.get("month"), sec.get("ssn"), sec.get("f10_7"))]
    return []


def _advice(sections: dict) -> str:
    """現在値・予測値から助言文を組み立てる（手書きの固定文にしない）。"""
    kp = (sections.get("kp") or {}).get("current") or 0
    cur_g = str((((sections.get("scales") or {}).get("current") or {}).get("G") or {}).get("scale") or "0")
    fc = ((sections.get("scales") or {}).get("forecast") or {})
    g_max = max([int(str(((b.get("G") or {}).get("scale") or 0))) for b in fc.values()] or [0])
    if cur_g not in ("0", "None", "") or float(kp) >= 5:
        return ("地磁気嵐が進行中です（Kp {}）。高緯度ではオーロラ、衛星運用・GNSS 測位・"
                "短波通信に軽微な影響が出ることがあります。".format(kp))
    if g_max >= 1:
        return ("現在は静穏ですが、今後1〜3日に G{}（{}）の地磁気嵐が予測されています。"
                "高緯度域の観測・衛星運用は該当時間帯の変動に注意してください。".format(
                    g_max, _G_SCALE_JA.get(str(g_max), "小規模")))
    # 地磁気嵐が無くても、直近のフレア規模は観測・通信へ影響しうる（数値から生成する）
    fl = sections.get("flares") or {}
    if _flare_rank(fl.get("max_class")) >= _flare_rank("M1.0"):
        return ("直近7日に **{}** フレア（{} UTC）が発生しています。"
                "M級以上では高緯度域の短波通信や衛星測位に乱れが出ることがあり、"
                "太陽電波を観測する場合は該当時刻の影響に注意してください。".format(
                    fl.get("max_class"),
                    str(fl.get("max_time") or "").replace("T", " ").rstrip("Z")))
    return "現在は静穏です。フレア・地磁気嵐とも目立った活動はありません。"


def space_weather_now(kind: str = "all", nasa_reason: str = "") -> CallToolResult:
    """NOAA SWPC（認証不要）による宇宙天気の現況を返す（DONKI のフォールバック）。

    NASA DONKI がレート制限や障害で使えないときに呼ぶ。Kp・NOAA スケール・
    GOES X線・フレアイベント（7日）・太陽風（RTSW）・陽子フラックス・警報・
    黒点相対数を返し、
    **どの項目が取得できなかったか**も数値で示す。出典は SWPC と明記し、
    NASA から切り替えた事実と理由も content に出す（出所の取り違えを防ぐ）。
    """
    kind = (kind or "all").strip().lower()
    if kind not in _SECTIONS_BY_KIND:
        kind = "all"
    try:
        data = fetch_all()
    except Exception as e:                       # 例外をツール外へ漏らさない
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="NOAA SWPC からも宇宙天気を取得できませんでした: {}".format(str(e)[:150]))],
            structuredContent={"error": "swpc fetch failed", "source": "NOAA SWPC",
                               "detail": str(e)[:200], "nasa_reason": nasa_reason},
        )
    sections = data.get("sections") or {}
    if not sections:
        # 1項目も取れなかった＝「データが無い」ではなく「取得できていない」。
        # 空のまま「現在は静穏です」と返すと、障害を静穏と誤読させる。
        return CallToolResult(
            content=[TextContent(type="text", text=(
                "宇宙天気を取得できませんでした（NASA に続き NOAA SWPC への取得も失敗）。理由: "
                + "; ".join(data.get("failed") or ["不明"]))[:400])],
            structuredContent={"error": "no space weather data", "source": "NOAA SWPC",
                               "failed": data.get("failed") or [], "nasa_reason": nasa_reason},
        )
    keys = _SECTIONS_BY_KIND.get(kind, ())
    lines = ["☀️ **宇宙天気（NOAA SWPC・リアルタイム）** 出典: services.swpc.noaa.gov（認証不要）"]
    if nasa_reason:
        lines.append("⚠️ NASA DONKI（api.nasa.gov）が使えないため、認証不要の NOAA SWPC で"
                     "代替しています。理由: " + nasa_reason)
    if kind == "cme":
        lines.append("※ この経路では CME のイベント一覧は提供していません"
                     "（太陽風の値から間接的に判断できます。DONKI 復旧後に取得してください）")
    for key in keys:
        if key in sections:
            lines.extend(_sec_lines(key, sections[key]))
    if data.get("failed"):
        lines.append("⚠️ 取得できなかった項目: " + "; ".join(data["failed"]))
    if kind == "all":
        lines.append("🤖 【AIからのインテリジェントアドバイス】" + _advice(sections))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"kind": kind, "fallback": True, "source": "NOAA SWPC",
                           "nasa_reason": nasa_reason,
                           "data": {k: sections[k] for k in keys if k in sections},
                           "failed": data.get("failed") or [], "fetched_utc": data.get("fetched_utc")},
    )

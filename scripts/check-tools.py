#!/usr/bin/env python3
"""全ツールの回帰検証スクリプト（依存追加なし・標準ライブラリのみ）。

使い方（リポジトリ直下で実行）:
    uv run python scripts/check-tools.py              # 全47ツールを実呼び出し
    uv run python scripts/check-tools.py --offline    # ネットワーク全断を注入して例外漏れを検査
    uv run python scripts/check-tools.py --dead-code  # 未参照定義・未使用importの走査
    uv run python scripts/check-tools.py --only sat_tle,apod
    uv run python scripts/check-tools.py --stdio      # 実クライアント経路（stdio）で代表ツールが応答するか
    uv run python scripts/check-tools.py --concurrency # 並列呼び出し（single-flight・スレッド逃がし）
    uv run python scripts/check-tools.py --json       # 結果をJSONで出力（CI向け）

終了コード: 0=すべて正常 / 1=異常あり（例外漏れ・structuredContent欠落・タイムアウト・デッドコード）
"""
from __future__ import annotations

import argparse
import ast
import collections
import inspect
import json
import os
import re
import sys
import threading
import time
import traceback
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# ツールを「正常系」で実行するためのダミー引数。
# 既定値が None の任意引数（place / object_name / name などは排他グループ）にも入れる。
# PRIMARY が1つでも埋まったツールには座標系（COORDS）を入れない（本来の経路を試すため）。
PRIMARY = {
    "name": "iss", "object_name": "M31", "query": "sentinel", "place": "東京",
    "location": "東京", "identifier": "N1_NO2",
    "category": "人工衛星", "from_port": "東京", "to_port": "大島",
    "telescope": "JCMT", "band": 6, "limit": 3, "max_results": 3,
}
COORDS = {"latitude": 35.68, "lat": 35.68, "longitude": 139.69, "lon": 139.69,
          "ra": 83.82, "dec": -5.39}


# 引数が必須のツールは None を渡すと「引数を指定してください」で終わり、経路が検証されない。
# ゲート専用の安全な引数をここで与える（存在しない ID を渡す削除は冪等なので副作用なし）。
GATE_ARGS = {
    "calendar_event_add": {"title": "ゲート検証用の予定", "date": "2026-10-24", "time": "19:30"},
    "calendar_event_remove": {"id": "user:gate-does-not-exist"},
}
# カレンダーのストアに書き込むツールは、ゲートでは一時ストアへ逃がす（利用者の実データを汚さない）
STORE_WRITE_TOOLS = ("calendar_event_add",)


def _fill_kwargs(fn) -> dict:
    params = [p for p in inspect.signature(fn).parameters.values()
              if p.default is inspect.Parameter.empty or p.default is None]
    kw = {}
    for p in params:
        if p.name in PRIMARY:
            kw[p.name] = PRIMARY[p.name]
    if not any(p.name in PRIMARY for p in params):
        for p in params:
            if p.name in COORDS:
                kw[p.name] = COORDS[p.name]
    for p in params:
        if p.default is inspect.Parameter.empty and p.name not in kw:
            kw[p.name] = None
    return kw


def sync_fn(tool):
    """ツールの「元の同期関数」を返す。

    server.py は同期ツールを `anyio.to_thread` の async ラッパーとして登録している
    （LLM の並列ツール呼び出しを実際に並行実行するため）。ゲートはネットワークを
    差し替えたりスレッドで直接呼ぶので、ラッパーが持つ `.sync_fn` を使う。
    """
    fn = getattr(tool, "fn", None)
    return getattr(fn, "sync_fn", fn)


def _is_error(result) -> bool:
    sc = getattr(result, "structuredContent", None)
    return isinstance(sc, dict) and bool(sc.get("error"))


# 自前で図を描くツール（figure/1 の注記が必須）。第三者の画像を返すだけの
# ツール（NASA画像検索・EOダッシュボード等）は対象外。
DRAWN_FIGURE_TOOLS = (
    "solar_system_now", "sat_ground_track", "planetary_orbiter_track",
    "planetary_rover_location_map", "sky_map_with_satellites", "solar_eclipse_series",
    "astronomy_weather", "moon_phase_map", "space_calendar",
)


def figure_issues(tool: str, result) -> list:
    """figure/1（図の注記）の検証。

    LLM はピクセルから描画規約を推測できないため、描画系ツールは
    structuredContent.figure（注記・視点・縮尺・自己検証）を必ず返す。
    """
    sc = getattr(result, "structuredContent", None)
    if not isinstance(sc, dict):
        return []
    fig = sc.get("figure")
    if not isinstance(fig, dict):
        if tool in DRAWN_FIGURE_TOOLS:
            return ["figure/1 未対応（描画系なのに structuredContent.figure がありません）"]
        return []
    out = []
    if fig.get("schema") != "figure/1":
        out.append("figure.schema が figure/1 ではありません")
    notes = fig.get("notes")
    if not isinstance(notes, list) or not notes or not all(
            isinstance(n, str) and n.strip() for n in notes):
        out.append("figure.notes が空です（図の注記は必須）")
    cap = fig.get("caption")
    if not isinstance(cap, str) or not cap.strip():
        out.append("figure.caption がありません")
    ver = fig.get("verify")
    if ver is not None:
        if not (isinstance(ver, dict) and ver.get("ok")):
            out.append("figure.verify.ok が真ではありません: {}".format(str(ver)[:120]))
        elif ver.get("periapsis_resolvable") is False:
            # 近点が画素から確認できないと申告した図は、その旨を注記に必ず書いていること
            joined = "".join(fig.get("notes") or [])
            if "内側" not in joined and "確認できない" not in joined:
                out.append("verify.periapsis_resolvable が偽なのに、注記に説明がありません")
    return out



# メディア（画像/音声/動画）を返すツールは、インライン描画できないハーネス
# （CLI系・Android系の codex / opencode など）向けに「アイコン付きリンク」を
# メディア本体より前に必ず出す。ここではその徹底を機械的に検査する。
MEDIA_LINK_RE = re.compile(
    r"(?:🖼|🎧|🎬|📄)" + chr(0xFE0F) + "?"
    + r"\s*\[[^\]]+\]\((?:https?://|file://)")


def _has_media_ref(obj) -> bool:
    """structuredContent のどこかに画像/音声/動画のURLか保存パスがあるか。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v and isinstance(k, str) and (
                    k.endswith("_url") or k.endswith("_path") or k in ("images", "path", "url")):
                return True
            if isinstance(v, list) and v and k == "images":
                return True
            if _has_media_ref(v):
                return True
    elif isinstance(obj, list):
        return any(_has_media_ref(v) for v in obj[:5])
    return False


def _has_key(obj, suffixes) -> bool:
    """structuredContent のどこかに指定の接尾辞のキーがあるか（音声/動画URLの検出）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.endswith(tuple(suffixes)) and isinstance(v, str) and v:
                return True
            if _has_key(v, suffixes):
                return True
    elif isinstance(obj, list):
        return any(_has_key(v, suffixes) for v in obj[:5])
    return False


def _icon_link(text: str, icon: str) -> bool:
    """指定アイコンの直後（VS16・空白を挟んで可）に markdown リンクが続いているか。"""
    for i, ch in enumerate(text):
        if ch != icon:
            continue
        rest = text[i + 1:]
        if rest.startswith(chr(0xFE0F)):      # 異体字セレクタ（絵文字表示）は読み飛ばす
            rest = rest[1:]
        if re.match(r"\s*\[[^\]]+\]\((?:https?://|file://)", rest):
            return True
    return False


def media_issues(tool, result) -> list:
    """メディアを含む結果の「リンク先行」検査。

    画像ブロックを返すツールは content の画像より前にアイコン付きリンク
    （🖼️/🎧/🎬/📄 [◯◯を開く](URL または file:///…)）を必ず置く。インライン画像を
    描かないツール（apod / eodashboard_detail / search_space_audio 等）も、
    structuredContent に image_url / audio_url / video_url を載せるなら
    そのアイコンのリンクが content に必要。structuredContent には再参照できるよう
    URLか保存パスを入れ、image_path がある場合は実ファイルの存在まで確かめる。
    """
    blocks = list(getattr(result, "content", None) or [])
    kinds = [getattr(c, "type", "?") for c in blocks]
    sc = getattr(result, "structuredContent", None)
    has_image = _has_key(sc, ("image_url",))
    has_audio = _has_key(sc, ("audio_url",))
    has_video = _has_key(sc, ("video_url",))
    if "image" not in kinds and not has_image and not has_audio and not has_video:
        return []
    out = []
    text = chr(10).join(getattr(c, "text", "") for c in blocks if getattr(c, "type", "") == "text")
    if "image" in kinds:
        idx = kinds.index("image")
        before = chr(10).join(getattr(c, "text", "") for c in blocks[:idx])
        if not MEDIA_LINK_RE.search(before):
            out.append("画像より前にアイコン付きリンク（🖼️ [◯◯を開く](URL)）がありません")
    if has_image and not _icon_link(text, "🖼"):
        out.append("画像URLがあるのにアイコン付きリンク（🖼️ [◯◯を開く](URL)）が content にありません")
    if has_audio and not _icon_link(text, "🎧"):
        out.append("音声のアイコン付きリンク（🎧 [◯◯を開く](URL)）が content にありません")
    if has_video and not _icon_link(text, "🎬"):
        out.append("動画のアイコン付きリンク（🎬 [◯◯を開く](URL)）が content にありません")
    if not isinstance(sc, dict) or not _has_media_ref(sc):
        out.append("structuredContent に画像のURL/保存パスがありません（image_url / image_path 等）")
    if isinstance(sc, dict):
        path = sc.get("image_path")
        if isinstance(path, str) and path and not os.path.exists(path):
            out.append("structuredContent.image_path のファイルが存在しません: {}".format(path))
    return out


# 引数を変えないと通らない経路（彗星の軌道面ビュー）を明示的に叩く。
# (表示名, 引数, 期待する円錐曲線の種別)
FIGURES_EXTRA_CALLS = (
    ("solar_system_now[comet_orbit/ellipse]", "solar_system_now",
     {"comet": "ハレー彗星", "view": "comet_orbit"}, {"conic_kind": "ellipse"}),
    ("solar_system_now[comet_orbit/hyperbola]", "solar_system_now",
     {"comet": "C/2023 A3", "view": "comet_orbit"}, {"conic_kind": "hyperbola"}),
    # 超長距離の楕円（a≳10^4 AU）: 近日点が画面で数px以下になり、誇張した太陽円盤の
    # 内側に入る。近点の等値検査はできず「注記で明示＋上界検査」になる経路を固定する。
    ("solar_system_now[comet_orbit/超長距離楕円]", "solar_system_now",
     {"comet": "C/2004 R2", "view": "comet_orbit"}, {"conic_kind": "ellipse"}),
    # 複数パネル（1彗星=1パネル）: conic は panels[] 側に入るので kind だけ検査する
    ("solar_system_now[comet_orbit/複数パネル]", "solar_system_now",
     {"comet": "ハレー彗星,C/2004 R2", "view": "comet_orbit"}, {"figure_kind": "orbit_plane_set"}),
    # 日食は既定引数だと「その観測地で見える食」が無い場合があるため、可視の例で叩く
    # 地点マーカー（落点マップ）: 過去機の公表落点を天体面地図に描く経路
    ("planetary_orbiter_track[かぐや落点]", "planetary_orbiter_track",
     {"body": "moon", "orbiter": "かぐや"}, {"figure_kind": "impact_site_map"}),
    # 地点マーカーの複数描画（着陸地点6地点＝番号＋凡例、単独＝局所図）
    ("planetary_orbiter_track[アポロ6地点]", "planetary_orbiter_track",
     {"body": "moon", "sites": "apollo"}, {"figure_kind": "landing_site_map"}),
    ("planetary_orbiter_track[アポロ11号]", "planetary_orbiter_track",
     {"body": "moon", "sites": "apollo11"}, {"figure_kind": "landing_site_map"}),
    # 全球地形画像が無い天体（木星）: 座標グリッドに衝突地点を描く経路
    ("planetary_orbiter_track[木星SL9衝突地点]", "planetary_orbiter_track",
     {"body": "jupiter", "sites": "all"}, {"figure_kind": "impact_site_map"}),
    # 他惑星: 金星（座標グリッド・9地点）と火星（画像地図・12地点）
    ("planetary_orbiter_track[金星着陸9地点]", "planetary_orbiter_track",
     {"body": "venus", "sites": "all"}, {"figure_kind": "landing_site_map"}),
    ("planetary_orbiter_track[火星着陸12地点]", "planetary_orbiter_track",
     {"body": "mars", "sites": "all"}, {"figure_kind": "landing_site_map"}),
    # 1枚全球画像ベースマップ（タイタン＝Cassini 全球図にホイヘンス着陸点）
    ("planetary_orbiter_track[タイタン着陸点]", "planetary_orbiter_track",
     {"body": "titan", "sites": "all"}, {"figure_kind": "landing_site_map"}),
    # 地点なしの地図のみ（全球画像を表示するだけの経路）
    ("planetary_orbiter_track[エウロパ全球図]", "planetary_orbiter_track",
     {"body": "europa", "sites": "map"}, {"figure_kind": "body_map"}),
    ("solar_eclipse_series[可視の日食]", "solar_eclipse_series",
     {"place": "ロンドン"}, {"figure_kind": "eclipse_panels"}),
    ("solar_eclipse_series[深い部分食]", "solar_eclipse_series",
     {"place": "東京", "date": "2035-09-02"}, {"figure_kind": "eclipse_panels"}),
    # 雨雲・降水画像（日本国内のみ添付される）: figure.kind を検査
    ("astronomy_weather[雨雲・降水画像]", "astronomy_weather",
     {"place": "東京"}, {"figure_kind": "weather_rain_panels"}),
    # 月齢マップ: 月齢カレンダー・朔の日（照度0で輝面を描かない経路）・朔望月パネル。
    ("moon_phase_map[月齢カレンダー]", "moon_phase_map",
     {"date": "2026-09", "place": "東京"}, {"figure_kind": "moon_phase_calendar"}),
    ("moon_phase_map[朔の日]", "moon_phase_map",
     {"date": "2026-09-11", "place": "東京"}, {"figure_kind": "moon_phase_calendar"}),
    ("moon_phase_map[朔望月パネル]", "moon_phase_map",
     {"layout": "lunation", "place": "東京", "days": 8},
     {"figure_kind": "moon_phase_lunation"}),
)


def figure_extra_rows(timeout=180.0) -> list:
    """figure/1 の追加経路（view="comet_orbit"）を実行して検証する。

    全ツール実行は既定引数で呼ぶため、view を変えないと通らない経路は
    ここで明示的にカバーする（e>=1 の閉じない軌道の扱いも検査する）。
    """
    from space_finder_mcp.server import mcp as _mcp
    rows = []
    for label, tool_name, kw, expect in FIGURES_EXTRA_CALLS:
        # 登録済みツールは anyio スレッド実行の async ラッパーなので、元の同期関数を取る
        fn = sync_fn(_mcp._tool_manager._tools[tool_name])
        out = {}

        def call():
            try:
                out["r"] = fn(**kw)
            except Exception as e:
                out["exc"] = "{}: {}".format(type(e).__name__, e)

        th = threading.Thread(target=call, daemon=True)
        t0 = time.perf_counter()
        th.start()
        th.join(timeout)
        dt = time.perf_counter() - t0
        if th.is_alive():
            rows.append({"tool": label, "status": "TIMEOUT", "seconds": round(dt, 2)})
            continue
        if "exc" in out:
            rows.append({"tool": label, "status": "LEAKED_EXCEPTION", "seconds": round(dt, 2),
                         "detail": str(out["exc"])[:200]})
            continue
        r = out["r"]
        sc = getattr(r, "structuredContent", None)
        fig = (sc or {}).get("figure") if isinstance(sc, dict) else None
        conic = (fig or {}).get("conic") or {}
        issues = figure_issues(tool_name, r)
        want_kind = expect.get("conic_kind")
        if want_kind and conic.get("kind") != want_kind:
            issues.append("conic.kind={} を期待（実際 {}）".format(want_kind, conic.get("kind")))
        if want_kind in ("hyperbola", "parabola") and conic.get("closed"):
            issues.append("閉じない軌道なのに conic.closed が真")
        want_fig = expect.get("figure_kind")
        if want_fig and (fig or {}).get("kind") != want_fig:
            issues.append("figure.kind={} を期待（実際 {}）".format(want_fig, (fig or {}).get("kind")))
        if not ((fig or {}).get("verify") or {}).get("ok"):
            issues.append("figure.verify.ok が真ではありません")
        rows.append({"tool": label, "status": "FIGURE_INVALID" if issues else "OK",
                     "seconds": round(dt, 2), "figure_issues": issues, "conic": conic,
                     "figure": {"kind": (fig or {}).get("kind"),
                                "notes": len((fig or {}).get("notes") or []),
                                "verify_ok": ((fig or {}).get("verify") or {}).get("ok")}})
    return rows

# 画像を返す条件が既定引数では満たされないツールの追加経路（規約13のリンク先行を機械的に検査する）。
MEDIA_EXTRA_CALLS = (
    # MAST は preview_image のときだけプレビュー画像を返す（既定はテキストのみ）
    ("mast_observations[preview_image]", "mast_observations",
     {"ra": 189.9976, "dec": -11.6231, "radius": 0.02, "mission": "JWST",
      "preview_image": True, "limit": 1}),
)


def media_extra_rows(timeout=180.0) -> list:
    """メディア（画像）を返す追加経路を実行し、リンク先行（規約13）を検査する。"""
    from space_finder_mcp.server import mcp as _mcp
    rows = []
    for label, tool_name, kw in MEDIA_EXTRA_CALLS:
        fn = sync_fn(_mcp._tool_manager._tools[tool_name])
        out = {}

        def call():
            try:
                out["r"] = fn(**kw)
            except Exception as e:
                out["exc"] = "{}: {}".format(type(e).__name__, e)

        th = threading.Thread(target=call, daemon=True)
        t0 = time.perf_counter()
        th.start()
        th.join(timeout)
        dt = time.perf_counter() - t0
        if th.is_alive():
            rows.append({"tool": label, "status": "TIMEOUT", "seconds": round(dt, 2)})
            continue
        if "exc" in out:
            rows.append({"tool": label, "status": "LEAKED_EXCEPTION", "seconds": round(dt, 2),
                         "detail": str(out["exc"])[:200]})
            continue
        r = out["r"]
        blocks = [getattr(c, "type", "?") for c in (getattr(r, "content", None) or [])]
        issues = media_issues(tool_name, r)
        rows.append({"tool": label, "status": "MEDIA_INVALID" if issues else "OK",
                     "seconds": round(dt, 2), "blocks": blocks, "media_issues": issues})
    return rows


def run_all(only=None, timeout=180, offline=False) -> list:
    """全ツールを実行して結果を返す。offline=True なら requests を必ず失敗させる。"""
    import requests
    from space_finder_mcp.server import mcp

    orig_get, orig_post = requests.get, requests.post
    if offline:
        def boom(*a, **k):
            raise requests.ConnectionError("check-tools: simulated network failure")
        requests.get, requests.post = boom, boom

    import os
    import tempfile
    from space_finder_mcp import calendar_store

    rows = []
    orig_store_path = calendar_store.STORE_PATH
    names = sorted(mcp._tool_manager._tools)
    if only:
        names = [n for n in names if n in only]
    for name in names:
        fn = sync_fn(mcp._tool_manager._tools[name])
        out = {}

        tmp_store = None
        if name in STORE_WRITE_TOOLS:                   # 実ストアを汚さないよう一時ファイルへ
            fd, tmp_store = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            os.remove(tmp_store)
            calendar_store.STORE_PATH = tmp_store

        def call():
            try:
                kw = _fill_kwargs(fn)
                kw.update(GATE_ARGS.get(name, {}))
                out["result"] = fn(**kw)
            except Exception as e:                      # 例外が外へ漏れた = 異常
                out["exc"] = "{}: {}".format(type(e).__name__, e)

        th = threading.Thread(target=call, daemon=True)
        t0 = time.perf_counter()
        th.start()
        th.join(timeout)
        dt = time.perf_counter() - t0
        if th.is_alive():
            rows.append({"tool": name, "status": "TIMEOUT", "seconds": round(dt, 2)})
            continue
        if "exc" in out:
            rows.append({"tool": name, "status": "LEAKED_EXCEPTION", "seconds": round(dt, 2),
                         "detail": str(out["exc"])[:200]})
            continue
        r = out.get("result")
        sc = getattr(r, "structuredContent", None)
        kinds = [getattr(c, "type", "?") for c in getattr(r, "content", [])]
        fig = sc.get("figure") if isinstance(sc, dict) else None
        fissues = figure_issues(name, r) if "image" in kinds else []
        missues = media_issues(name, r)
        # 「未対応（figure なし）」は移行中のため警告どまり、内容が不正なら FAIL。
        invalid = [i for i in fissues if not i.startswith("figure/1 未対応")]
        fsum = ({"kind": fig.get("kind"), "notes": len(fig.get("notes") or []),
                 "verify_ok": (fig.get("verify") or {}).get("ok")}
                if isinstance(fig, dict) else None)
        rows.append({"tool": name,
                     "status": "ERROR_RESULT" if _is_error(r)
                               else ("MEDIA_INVALID" if missues
                                     else ("FIGURE_INVALID" if invalid else "OK")),
                     "seconds": round(dt, 2), "blocks": kinds,
                     "has_structured_content": isinstance(sc, dict),
                     "figure": fsum, "figure_issues": fissues,
                     "media_issues": missues,
                     "detail": (sc.get("error") if _is_error(r) else None)})

    requests.get, requests.post = orig_get, orig_post
    return rows


def stdio_report(timeout: float = 90.0) -> list:
    """stdio（実際の MCPクライアント経路）で代表ツールが応答するかを検査する。

    起動方法はドキュメントと同じ `python -c "from space_finder_mcp import main; main()"`。
    mcp の stdio サーバーが動き出した後に numpy / matplotlib / skyfield を import すると
    **import が返らずツール呼び出しが無応答になる**（実測: Windows 11 + Python 3.11。
    numpy・matplotlib.pyplot・skyfield.api が HANG、PIL.Image・sgp4・requests は OK）。
    server.py が起動時に numpy を事前 import して回避しているが、再発はここで検出する
    （関数を直接呼ぶ他の検査では絶対に見つからない種類の不具合）。
    """
    import json as _json
    import subprocess
    import threading

    calls = [
        ("constellation_now", {"place": "東京"}, "numpy をツール実行時に import する経路"),
        ("moon_phase_map", {"place": "東京"}, "skyfield + numpy の計算経路"),
        ("sky_map_with_satellites", {"place": "東京", "engine": "accurate"},
         "matplotlib の描画経路（pyplot）"),
        ("solar_system_now", {"engine": "accurate"}, "matplotlib の描画経路（pyplot）"),
        ("sat_ground_track", {"name": "iss"}, "sgp4 + Pillow の描画経路"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-c", "from space_finder_mcp import main; main()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=ROOT, env=env, text=True, encoding="utf-8", bufsize=1)
    lines = []
    threading.Thread(target=lambda: [lines.append(x) for x in proc.stdout], daemon=True).start()

    def send(obj):
        proc.stdin.write(_json.dumps(obj) + chr(10))
        proc.stdin.flush()

    def wait_for(msg_id, limit):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < limit:
            for ln in lines:
                if '"id": {}'.format(msg_id) in ln or '"id":{}'.format(msg_id) in ln:
                    return time.perf_counter() - t0
            time.sleep(0.2)
        return None

    rows = []
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "check-tools", "version": "1"}}})
        if wait_for(1, 30) is None:
            rows.append({"tool": "(handshake)", "status": "TIMEOUT", "seconds": 0.0,
                         "detail": "initialize に応答がありません"})
            return rows
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        dt = wait_for(2, 30)
        rows.append({"tool": "(tools/list)", "status": "OK" if dt is not None else "TIMEOUT",
                     "seconds": round(dt or 0.0, 2),
                     "detail": "ツール一覧が返るか（stdio ハンドシェイク）"})
        for i, (name, args, why) in enumerate(calls, start=3):
            send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                  "params": {"name": name, "arguments": args}})
            dt = wait_for(i, timeout)
            rows.append({"tool": name, "status": "OK" if dt is not None else "TIMEOUT",
                         "seconds": round(dt or 0.0, 2), "detail": why})
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    return rows


def concurrency_report(timeout: float = 120.0) -> list:
    """並列ツール呼び出しの検査（LLM が複数ツールを同時に投げる前提の検証）。

    1. **single-flight**: 同じ引数の並行呼び出しは1回の実行にまとまる（ttl_cache）。
       まとめが無いと N 並列＝N 回の API 呼び出しになり、共有枠（NASA DEMO_KEY 等）を
       無駄に食う。
    2. **実際に並行に走る**: 別々の引数の並行呼び出しは wall ≈ 最大値（合計にならない）。
       server.py は同期ツールを `anyio.to_thread` に逃がしているので、イベントループを
       塞がない（逃がす前は wall = 合計だった）。
    3. **並行でも壊れない**: 全ツールが CallToolResult（structuredContent 付き）を返し、
       例外を外へ漏らさない。
    """
    import concurrent.futures as cf
    import requests
    from space_finder_mcp.cache import ttl_cache
    from space_finder_mcp.server import mcp

    rows = []

    # --- 1) single-flight（合成関数で決定的に検査。ネットワーク不要）---
    calls = []

    @ttl_cache(60, maxsize=4)
    def _probe(x):
        calls.append(x)
        time.sleep(0.3)
        return {"v": x}

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        t0 = time.perf_counter()
        outs = list(ex.map(lambda _: _probe(11), range(8)))
        dt = time.perf_counter() - t0
    issues = []
    if len(calls) != 1:
        issues.append("同じ引数の8並列で実行が {} 回（single-flight 未実装）".format(len(calls)))
    if dt > 1.5:
        issues.append("single-flight でも wall {:.2f}s（1回の実行 0.3s を大きく超える）".format(dt))
    if any(o != {"v": 11} for o in outs):
        issues.append("並行呼び出しの戻り値が一致しません")
    rows.append({"check": "single-flight（同一引数の並行呼び出しを1回に集約）",
                 "status": "OK" if not issues else "NG", "detail": " / ".join(issues),
                 "seconds": round(dt, 2), "executions": len(calls)})

    # --- 2) サーバーが同期ツールをワーカースレッドへ逃がしているか（I/O 待ちで決定的に検査）---
    # ここが本題: FastMCP は同期関数をイベントループ上でそのまま呼ぶため、逃がしていないと
    # 1つのツールの API 待ちが他のツール呼び出しを全部止める（実測 wall = 合計）。
    # CPU 律速のツール（Skyfield/Pillow の計算）は GIL で並列化しないので、I/O 待ちで測る。
    import asyncio
    from space_finder_mcp.server import _threaded
    issues = []

    @_threaded
    def _sleepy(tag: str) -> str:
        time.sleep(0.4)
        return tag

    async def _gather3():
        # ラッパー自身がワーカースレッドへ逃がすので、そのまま gather する
        return await asyncio.gather(*[_sleepy(t) for t in ("a", "b", "c")])

    t0 = time.perf_counter()
    got = asyncio.run(_gather3())
    dt = time.perf_counter() - t0
    dur = 0.4 * 3
    if got != ["a", "b", "c"]:
        issues.append("戻り値が不一致: {}".format(got))
    if dt > dur * 0.75:
        issues.append("I/O待ち3並列の wall {:.2f}s ≒ 逐次合計 {:.2f}s（スレッドへ逃がしていない）".format(dt, dur))
    # 登録済みツールが「スレッド実行の async ラッパー」になっていること（.sync_fn が目印）
    from space_finder_mcp.server import mcp as _mcp
    not_wrapped = [n for n, tl in _mcp._tool_manager._tools.items() if not hasattr(tl.fn, "sync_fn")]
    if not_wrapped:
        issues.append("スレッド実行ラッパーでないツール: {}".format(", ".join(sorted(not_wrapped)[:5])))
    rows.append({"check": "並行実行（同期ツールを anyio のワーカースレッドへ逃がしている）",
                 "status": "OK" if not issues else "NG",
                 "detail": " / ".join(issues) or "I/O待ち3並列 {:.2f}s < 逐次合計 {:.2f}s".format(dt, dur),
                 "seconds": round(dt, 2), "executions": 3})

    # --- 3) 実ツールを混ぜて並行に呼び、例外・structuredContent 欠落が無いこと ---
    batch = [("constellation_now", {"place": "東京"}),
             ("stac_collections", {}),
             ("iss_now", {}),
             ("satellite_status", {"query": "ひまわり"})]
    issues = []
    t0 = time.perf_counter()

    def _call(item):
        name, kw = item
        tl = mcp._tool_manager._tools.get(name)
        if tl is None:
            return name, "MISSING", None
        try:
            return name, "OK", sync_fn(tl)(**kw)
        except Exception as e:                      # 例外が外へ漏れた = 異常
            return name, "LEAKED_EXCEPTION", "{}: {}".format(type(e).__name__, e)

    with cf.ThreadPoolExecutor(max_workers=len(batch)) as ex:
        for name, st, r in ex.map(_call, batch):
            if st != "OK":
                issues.append("{}: {}".format(name, st))
            elif not isinstance(getattr(r, "structuredContent", None), dict):
                issues.append("{}: structuredContent 無し".format(name))
    rows.append({"check": "混在4ツールの並行呼び出し（例外漏れ・structuredContent欠落なし）",
                 "status": "OK" if not issues else "NG", "detail": " / ".join(issues),
                 "seconds": round(time.perf_counter() - t0, 2), "executions": len(batch)})
    return rows


def fuzz_args(only=None, timeout=60, live=False) -> list:
    """数値引数へ不正値（"abc" / None / []）を入れて例外が漏れないか検査する。

    MCPクライアントは型を保証しないため、int()/float() を引数へ直に適用すると
    ValueError がツール外へ漏れる。全ツール入口で input_utils を通していることを機械的に確認する。
    """
    import inspect as _inspect
    import requests
    from space_finder_mcp.server import mcp

    if not live:
        def boom(*a, **k):
            raise requests.ConnectionError("check-tools: simulated network failure")
        requests.get, requests.post = boom, boom

    names = sorted(mcp._tool_manager._tools)
    if only:
        names = [n for n in names if n in only]
    rows = []
    for name in names:
        # 登録済みツールは async ラッパーなので、元の同期関数を直接呼ぶ（sync_fn）
        fn = sync_fn(mcp._tool_manager._tools[name])
        base = _fill_kwargs(fn)
        for p in _inspect.signature(fn).parameters.values():
            ann = str(p.annotation)
            if not any(k in ann for k in ("int", "float")):
                continue
            for bad in ("abc", "5件", [], {}):
                kw = dict(base)
                kw[p.name] = bad
                out = {}

                def call():
                    try:
                        out["r"] = fn(**kw)
                    except Exception as e:
                        out["exc"] = "{}: {}".format(type(e).__name__, e)

                th = threading.Thread(target=call, daemon=True)
                th.start()
                th.join(timeout)
                if th.is_alive():
                    rows.append({"tool": name, "arg": p.name, "value": repr(bad), "status": "TIMEOUT"})
                    continue
                if "exc" in out:
                    rows.append({"tool": name, "arg": p.name, "value": repr(bad),
                                 "status": "LEAKED_EXCEPTION", "detail": str(out["exc"])[:160]})
                else:
                    rows.append({"tool": name, "arg": p.name, "value": repr(bad), "status": "OK"})
    return rows


def fonts_report() -> list:
    """日本語フォントの解決結果を検査する（Windows / macOS / Linux 共通の探索）。

    画像内の日本語が豆腐（□）にならないよう、解決したフォントが実際に日本語グリフを
    持つこと（cmap を直接確認）と、matplotlib 経路（accurate 版）が同じフォントを
    掴むことを確認する。日本語フォントが無い環境では SPACE_FINDER_FONT で指定する。
    """
    from space_finder_mcp import img_common as ic
    rows = []
    status = ic.font_status()
    for key, label in (("regular", "標準"), ("bold", "太字")):
        info = status.get(key) or {}
        ok = info.get("japanese_glyphs") is True
        rows.append({
            "check": "フォント({})".format(label),
            "status": "OK" if ok else "NG",
            "path": info.get("path"),
            "source": info.get("source"),
            "detail": "" if ok else (
                "日本語フォントを解決できません（画像の日本語が豆腐になります）。"
                "そのOSの日本語フォントを入れるか、環境変数 SPACE_FINDER_FONT で指定してください"),
        })
    family = ic.apply_matplotlib_cjk_font()
    path = None
    try:
        from matplotlib import font_manager, rcParams
        fp = font_manager.FontProperties(family=rcParams["font.sans-serif"])
        path = font_manager.findfont(fp, fallback_to_default=False)
    except Exception:
        path = None
    ok = bool(path) and ic._cmap_has_glyphs(path, ic._CJK_PROBE) is True
    rows.append({
        "check": "matplotlib(accurate版)",
        "status": "OK" if ok else "NG",
        "path": path,
        "source": family,
        "detail": "" if ok else (
            "matplotlib の日本語フォントを解決できません"
            "（sky_overlay / solar_system の accurate 版が豆腐になります）"),
    })
    return rows


def scan_dead_code() -> list:
    """未参照の定義・未使用 import を返す（__future__ は除外）。"""
    files = sorted(f for f in os.listdir(SRC + os.sep + "space_finder_mcp") if f.endswith(".py"))
    texts = {f: open(os.path.join(SRC, "space_finder_mcp", f), encoding="utf-8").read() for f in files}
    findings = []
    defs = []
    for f in files:
        for node in ast.parse(texts[f]).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append((f, node.name, "def", node.lineno))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defs.append((f, t.id, "const", node.lineno))
    for f, name, kind, ln in defs:
        if name == "main":
            continue
        total = sum(len(re.findall(r"\b" + re.escape(name) + r"\b", t)) for t in texts.values()) - 1
        if total <= 0:
            findings.append({"file": f, "line": ln, "kind": "unreferenced_" + kind, "name": name})
    for f in files:
        src = texts[f]
        imported = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported += [(a.asname or a.name.split(".")[0], node.lineno) for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imported += [(a.asname or a.name, node.lineno) for a in node.names if a.name != "*"]
        for name, ln in imported:
            if len(re.findall(r"\b" + re.escape(name) + r"\b", src)) <= 1:
                findings.append({"file": f, "line": ln, "kind": "unused_import", "name": name})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="space-finder-mcp 回帰検証")
    ap.add_argument("--offline", action="store_true", help="ネットワーク全断を注入して例外漏れを検査")
    ap.add_argument("--dead-code", action="store_true", help="未参照定義・未使用importの走査のみ")
    ap.add_argument("--fuzz", action="store_true", help="数値引数へ不正値を入れて例外漏れを検査（ネットワーク断で高速）")
    ap.add_argument("--fuzz-live", action="store_true", help="--fuzz を実APIに対して実行する")
    ap.add_argument("--figures", action="store_true",
                    help="描画系ツールの figure/1（図の注記・自己検証）を検査")
    ap.add_argument("--stdio", action="store_true",
                    help="実クライアント経路（stdio）で代表ツールが応答するか検査（固定の代表セット。--only は効かない）")
    ap.add_argument("--concurrency", action="store_true",
                    help="並列ツール呼び出し（single-flight・実際に並行・例外漏れなし）を検査（固定の代表セット）")
    ap.add_argument("--media-links", action="store_true",
                    help="メディア（画像/音声/動画）を返すツールの「アイコン付きリンク先行」を検査")
    ap.add_argument("--fonts", action="store_true",
                    help="日本語フォントの解決（Windows/macOS/Linux 共通）を検査。解決したフォントが日本語グリフを持つか（豆腐回避）")
    ap.add_argument("--only", help="カンマ区切りのツール名で絞り込み")
    ap.add_argument("--timeout", type=float, default=180.0, help="1ツールあたりの制限秒（既定180）")
    ap.add_argument("--json", action="store_true", help="JSONで出力")
    args = ap.parse_args()

    if args.dead_code:
        found = scan_dead_code()
        if args.json:
            print(json.dumps({"dead_code": found}, ensure_ascii=False, indent=2))
        else:
            print("=== デッドコード走査 ===")
            for x in found:
                print("  {file}:{line} {kind}: {name}".format(**x))
            print("  検出数:", len(found))
        return 1 if found else 0

    if args.fonts:
        rows = fonts_report()
        bad = [r for r in rows if r["status"] != "OK"]
        if args.json:
            print(json.dumps({"mode": "fonts", "rows": rows, "problems": bad},
                             ensure_ascii=False, indent=2))
        else:
            print("=== 日本語フォントの検査（Windows / macOS / Linux 共通）===")
            for r in rows:
                print("  {m} {c:24s} path={p} source={s}".format(
                    m="ok " if r["status"] == "OK" else "NG ",
                    c=r["check"], p=r["path"], s=r["source"]))
                if r["detail"]:
                    print("      ! " + r["detail"])
            print("  検査項目:", len(rows), "/ 問題:", len(bad))
        return 1 if bad else 0

    only = set(x.strip() for x in args.only.split(",")) if args.only else None

    if args.fuzz or args.fuzz_live:
        rows = fuzz_args(only=only, timeout=min(args.timeout, 60.0), live=args.fuzz_live)
        bad = [r for r in rows if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION")]
        if args.json:
            print(json.dumps({"mode": "fuzz", "rows": rows, "problems": bad}, ensure_ascii=False, indent=2))
        else:
            print("=== 不正引数の注入テスト（数値引数 × 4種）===")
            for r in bad:
                # 行によっては detail が無い（TIMEOUT など）ので .get で埋める
                print("  EXC {tool} {arg}={value}  {detail}".format(
                    tool=r.get("tool"), arg=r.get("arg"), value=r.get("value"),
                    detail=r.get("detail", "")))
            print("  検査した組み合わせ:", len(rows), "／ 例外漏れ:", len(bad))
        return 1 if bad else 0

    if args.stdio:
        rows = stdio_report(timeout=max(30.0, min(args.timeout, 120.0)))
        bad = [r for r in rows if r["status"] != "OK"]
        if args.json:
            print(json.dumps({"mode": "stdio", "rows": rows, "problems": bad},
                             ensure_ascii=False, indent=2))
        else:
            print("=== stdio（実クライアント経路）の応答検査 ===")
            for r in rows:
                print("  {m} {t:26s} {s:6.2f}s  {d}".format(
                    m="ok " if r["status"] == "OK" else "NG ", t=r["tool"],
                    s=r.get("seconds") or 0.0, d=r.get("detail") or ""))
            print("  検査項目:", len(rows), "／ 無応答:", len(bad))
        return 1 if bad else 0

    if args.concurrency:
        rows = concurrency_report(timeout=args.timeout)
        bad = [r for r in rows if r["status"] != "OK"]
        if args.json:
            print(json.dumps({"mode": "concurrency", "rows": rows, "problems": bad},
                             ensure_ascii=False, indent=2))
        else:
            print("=== 並列処理の検査（LLM が複数ツールを同時に投げる前提）===")
            for r in rows:
                print("  {m} {c}  [{s:.2f}s]".format(
                    m="ok " if r["status"] == "OK" else "NG ", c=r["check"], s=r.get("seconds") or 0.0))
                if r.get("detail"):
                    print("      " + r["detail"])
            print("  検査項目:", len(rows), "／ 問題:", len(bad))
        return 1 if bad else 0

    if args.media_links:
        rows = run_all(only=only, timeout=args.timeout, offline=False)
        out = [r for r in rows if "image" in (r.get("blocks") or [])]
        out = out + (media_extra_rows(timeout=args.timeout) if not only else [])
        bad = [r for r in out if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION", "MEDIA_INVALID")
               or r.get("media_issues")]
        if args.json:
            print(json.dumps({"mode": "media-links", "rows": out, "problems": bad},
                             ensure_ascii=False, indent=2))
        else:
            print("=== メディアのリンク先行検査（画像・音声・動画）===")
            for r in out:
                print("  {m} {t:30s} images={n} blocks={b}".format(
                    m="ok " if not r.get("media_issues") else "NG ",
                    t=r["tool"], n=(r.get("blocks") or []).count("image"),
                    b="+".join(r.get("blocks") or [])))
                for i in r.get("media_issues") or []:
                    print("      ! " + i)
            print("  検査した画像系ツール:", len(out), "／ 問題:", len(bad))
        return 1 if bad else 0

    if args.figures:
        rows = run_all(only=only, timeout=args.timeout, offline=False)
        out = [r for r in rows if r["tool"] in DRAWN_FIGURE_TOOLS]
        extra_rows = figure_extra_rows(timeout=args.timeout) if not only else []
        bad = [r for r in out + extra_rows
               if r["status"] in ("FIGURE_INVALID", "TIMEOUT", "LEAKED_EXCEPTION")
               or r.get("figure_issues")]
        if args.json:
            print(json.dumps({"mode": "figures", "rows": out, "problems": bad},
                             ensure_ascii=False, indent=2))
        else:
            print("=== 図の注記（figure/1）検査 ===")
            for r in out:
                f = r.get("figure")
                if f:
                    print("  ok  {t:30s} kind={k} notes={n} verify_ok={v}".format(
                        t=r["tool"], k=f.get("kind"), n=f.get("notes"), v=f.get("verify_ok")))
                else:
                    print("  --  {t:30s} figure 未対応".format(t=r["tool"]))
                for i in r.get("figure_issues", []):
                    print("      ! " + i)
            for r in extra_rows:
                f = r.get("figure") or {}
                print("  ok  {t:36s} kind={k} notes={n} verify_ok={v} conic={c}".format(
                    t=r["tool"], k=f.get("kind"), n=f.get("notes"), v=f.get("verify_ok"),
                    c=(r.get("conic") or {}).get("kind")))
                for i in r.get("figure_issues", []):
                    print("      ! " + i)
            print("  描画系:", len(out), "／ figure 未対応:",
                  sum(1 for r in out if not r.get("figure")),
                  "／ 追加経路:", len(extra_rows))
        return 1 if bad else 0

    rows = run_all(only=only, timeout=args.timeout, offline=args.offline)

    bad = [r for r in rows if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION", "FIGURE_INVALID",
                                              "MEDIA_INVALID")
           or (not r.get("has_structured_content") and r["status"] != "TIMEOUT")]
    if args.json:
        print(json.dumps({"mode": "offline" if args.offline else "live", "rows": rows,
                          "problems": bad}, ensure_ascii=False, indent=2))
    else:
        mode = "オフライン（ネットワーク全断）" if args.offline else "通常"
        print("=== 全ツール検証（{}）===".format(mode))
        counter = collections.Counter(r["status"] for r in rows)
        for r in rows:
            mark = {"OK": "ok ", "ERROR_RESULT": "err", "TIMEOUT": "T/O",
                    "LEAKED_EXCEPTION": "EXC", "FIGURE_INVALID": "FIG",
                    "MEDIA_INVALID": "MED"}[r["status"]]
            extra = "  " + str(r.get("detail"))[:60] if r.get("detail") else ""
            blocks = r.get("blocks", "")
            print("  {mark} {tool:30s} {sec:6.2f}s {blocks}{extra}".format(
                mark=mark, tool=r["tool"], sec=r.get("seconds", 0.0),
                blocks=blocks, extra=extra))
        for r in rows:
            for i in r.get("media_issues") or []:
                print("      ! {}: {}".format(r["tool"], i))
        print("  内訳:", dict(counter))
        if bad:
            print("  ★ 要修正:", [(r["tool"], r["status"]) for r in bad])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

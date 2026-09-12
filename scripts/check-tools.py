#!/usr/bin/env python3
"""全ツールの回帰検証スクリプト（依存追加なし・標準ライブラリのみ）。

使い方（リポジトリ直下で実行）:
    uv run python scripts/check-tools.py              # 全45ツールを実呼び出し
    uv run python scripts/check-tools.py --offline    # ネットワーク全断を注入して例外漏れを検査
    uv run python scripts/check-tools.py --dead-code  # 未参照定義・未使用importの走査
    uv run python scripts/check-tools.py --only sat_tle,apod
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


def _is_error(result) -> bool:
    sc = getattr(result, "structuredContent", None)
    return isinstance(sc, dict) and bool(sc.get("error"))


# 自前で図を描くツール（figure/1 の注記が必須）。第三者の画像を返すだけの
# ツール（NASA画像検索・EOダッシュボード等）は対象外。
DRAWN_FIGURE_TOOLS = (
    "solar_system_now", "sat_ground_track", "planetary_orbiter_track",
    "planetary_rover_location_map", "sky_map_with_satellites", "solar_eclipse_series",
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
    if ver is not None and not (isinstance(ver, dict) and ver.get("ok")):
        out.append("figure.verify.ok が真ではありません: {}".format(str(ver)[:120]))
    return out



# 引数を変えないと通らない経路（彗星の軌道面ビュー）を明示的に叩く。
# (表示名, 引数, 期待する円錐曲線の種別)
FIGURES_EXTRA_CALLS = (
    ("solar_system_now[comet_orbit/ellipse]", "solar_system_now",
     {"comet": "ハレー彗星", "view": "comet_orbit"}, {"conic_kind": "ellipse"}),
    ("solar_system_now[comet_orbit/hyperbola]", "solar_system_now",
     {"comet": "C/2023 A3", "view": "comet_orbit"}, {"conic_kind": "hyperbola"}),
    # 日食は既定引数だと「その観測地で見える食」が無い場合があるため、可視の例で叩く
    ("solar_eclipse_series[可視の日食]", "solar_eclipse_series",
     {"place": "ロンドン"}, {"figure_kind": "eclipse_panels"}),
    ("solar_eclipse_series[深い部分食]", "solar_eclipse_series",
     {"place": "東京", "date": "2035-09-02"}, {"figure_kind": "eclipse_panels"}),
)


def figure_extra_rows(timeout=180.0) -> list:
    """figure/1 の追加経路（view="comet_orbit"）を実行して検証する。

    全ツール実行は既定引数で呼ぶため、view を変えないと通らない経路は
    ここで明示的にカバーする（e>=1 の閉じない軌道の扱いも検査する）。
    """
    from space_finder_mcp.server import mcp as _mcp
    rows = []
    for label, tool_name, kw, expect in FIGURES_EXTRA_CALLS:
        fn = _mcp._tool_manager._tools[tool_name].fn
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

def run_all(only=None, timeout=180, offline=False) -> list:
    """全ツールを実行して結果を返す。offline=True なら requests を必ず失敗させる。"""
    import requests
    from space_finder_mcp.server import mcp

    orig_get, orig_post = requests.get, requests.post
    if offline:
        def boom(*a, **k):
            raise requests.ConnectionError("check-tools: simulated network failure")
        requests.get, requests.post = boom, boom

    rows = []
    names = sorted(mcp._tool_manager._tools)
    if only:
        names = [n for n in names if n in only]
    for name in names:
        fn = mcp._tool_manager._tools[name].fn
        out = {}

        def call():
            try:
                out["result"] = fn(**_fill_kwargs(fn))
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
        # 「未対応（figure なし）」は移行中のため警告どまり、内容が不正なら FAIL。
        invalid = [i for i in fissues if not i.startswith("figure/1 未対応")]
        fsum = ({"kind": fig.get("kind"), "notes": len(fig.get("notes") or []),
                 "verify_ok": (fig.get("verify") or {}).get("ok")}
                if isinstance(fig, dict) else None)
        rows.append({"tool": name,
                     "status": "ERROR_RESULT" if _is_error(r)
                               else ("FIGURE_INVALID" if invalid else "OK"),
                     "seconds": round(dt, 2), "blocks": kinds,
                     "has_structured_content": isinstance(sc, dict),
                     "figure": fsum, "figure_issues": fissues,
                     "detail": (sc.get("error") if _is_error(r) else None)})

    requests.get, requests.post = orig_get, orig_post
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
        fn = mcp._tool_manager._tools[name].fn
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

    only = set(x.strip() for x in args.only.split(",")) if args.only else None

    if args.fuzz or args.fuzz_live:
        rows = fuzz_args(only=only, timeout=min(args.timeout, 60.0), live=args.fuzz_live)
        bad = [r for r in rows if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION")]
        if args.json:
            print(json.dumps({"mode": "fuzz", "rows": rows, "problems": bad}, ensure_ascii=False, indent=2))
        else:
            print("=== 不正引数の注入テスト（数値引数 × 4種）===")
            for r in bad:
                print("  EXC {tool} {arg}={value}  {detail}".format(**r))
            print("  検査した組み合わせ:", len(rows), "／ 例外漏れ:", len(bad))
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

    bad = [r for r in rows if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION", "FIGURE_INVALID")
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
                    "LEAKED_EXCEPTION": "EXC", "FIGURE_INVALID": "FIG"}[r["status"]]
            extra = "  " + str(r.get("detail"))[:60] if r.get("detail") else ""
            blocks = r.get("blocks", "")
            print("  {mark} {tool:30s} {sec:6.2f}s {blocks}{extra}".format(
                mark=mark, tool=r["tool"], sec=r.get("seconds", 0.0),
                blocks=blocks, extra=extra))
        print("  内訳:", dict(counter))
        if bad:
            print("  ★ 要修正:", [(r["tool"], r["status"]) for r in bad])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

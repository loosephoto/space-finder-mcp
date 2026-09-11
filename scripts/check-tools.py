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
        rows.append({"tool": name, "status": "ERROR_RESULT" if _is_error(r) else "OK",
                     "seconds": round(dt, 2), "blocks": kinds,
                     "has_structured_content": isinstance(sc, dict),
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

    rows = run_all(only=only, timeout=args.timeout, offline=args.offline)

    bad = [r for r in rows if r["status"] in ("TIMEOUT", "LEAKED_EXCEPTION")
           or (not r.get("has_structured_content") and r["status"] != "TIMEOUT")]
    if args.json:
        print(json.dumps({"mode": "offline" if args.offline else "live", "rows": rows,
                          "problems": bad}, ensure_ascii=False, indent=2))
    else:
        mode = "オフライン（ネットワーク全断）" if args.offline else "通常"
        print("=== 全ツール検証（{}）===".format(mode))
        counter = collections.Counter(r["status"] for r in rows)
        for r in rows:
            mark = {"OK": "ok ", "ERROR_RESULT": "err", "TIMEOUT": "T/O", "LEAKED_EXCEPTION": "EXC"}[r["status"]]
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

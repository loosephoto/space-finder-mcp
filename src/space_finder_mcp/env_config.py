"""リポジトリ直下の .env を読み込む（標準ライブラリのみ・依存追加なし）。

MCPクライアントの設定ファイルにAPIキーを書きたくない場合に、リポジトリ直下の
`.env` に置けるようにする（tokyo-transit-mcp と同じ使い勝手）。

優先順位: すでに設定済みの環境変数（MCPクライアントの env）> リポジトリ直下の .env。
.env は未設定のキーだけを補う。値は決してログ・例外メッセージへ出さない。
"""
from __future__ import annotations

import os
import re

_LOADED = False


def _candidate_paths() -> list:
    """探索する .env の候補（パッケージ位置基準 → カレントディレクトリ）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))          # <repo>/src/pkg/ -> <repo>
    return [os.path.join(repo, ".env"), os.path.join(os.getcwd(), ".env")]


def parse_dotenv(text: str) -> dict:
    """`.env` の内容を辞書にする（KEY=VALUE / 空行 / #コメント / export 接頭辞 / 引用符）。"""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # インラインコメント（` #...`）は**引用符の外**にあるものだけ切る
        # （python-dotenv と同じ。実測: 'NASA_API_KEY=*** # comment' が値にコメントを
        # 含んでいた）。引用符で囲んだ値は囲みの外のコメントを落とす。
        if value[:1] in ("\"", "'"):
            q = value[0]
            end = value.find(q, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            m = re.search(r"\s#", value)
            if m:
                value = value[:m.start()].rstrip()
        if key:
            out[key] = value
    return out


def load_dotenv(path: str = None, override: bool = False) -> dict:
    """`.env` を読み込んで os.environ へ反映する。戻り: 反映したキーと値の辞書。

    - override=False（既定）では、既存の環境変数を上書きしない（クライアント側 env を優先）
    - ファイルが無い場合は何もしない（例外も出さない）
    """
    global _LOADED
    applied = {}
    targets = [path] if path else _candidate_paths()
    for p in targets:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                values = parse_dotenv(fh.read())
        except OSError:
            continue
        for key, value in values.items():
            if override or key not in os.environ:
                os.environ[key] = value
                applied[key] = value
        if applied:
            break
    _LOADED = True
    return applied


"""ツール引数の防御的な数値変換（値が不正でも例外をツール外へ漏らさない）。

MCPクライアントは型を保証しない（``"limit": "5件"`` / ``null`` / ``true`` なども届く）。
``int()`` / ``float()`` を引数へ直接適用すると ValueError/TypeError が
ツール呼び出しごと例外となって漏れるため、ツールの入口では必ず本モジュールを通す。

失敗した場合は既定値（``default``）へ落とし、``minimum``/``maximum`` でクランプする。
``default`` が ``None`` の場合は ``None`` を返すので、呼び出し側で「指定なし」として扱える。
"""

from __future__ import annotations


def as_int(value, default=None, minimum=None, maximum=None):
    """整数へ防御的に変換する（失敗・None・空文字・NaN・無限大は default）。"""
    n = None
    if isinstance(value, bool):
        n = int(value)
    elif isinstance(value, int):
        n = value
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            n = None
        else:
            try:
                n = int(value)
            except (TypeError, ValueError, OverflowError):
                n = None
    else:
        try:
            f = float(str(value).strip())
            if f != f or f in (float("inf"), float("-inf")):
                n = None
            else:
                n = int(f)
        except (TypeError, ValueError, OverflowError):
            n = None
    if n is None:
        n = default
    if n is None:
        return None
    try:
        n = int(n)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum is not None or maximum is not None:
        try:
            n = int(n)
            if minimum is not None:
                n = max(int(minimum), n)     # inf / nan の境界は int() が投げる
            if maximum is not None:
                n = min(int(maximum), n)
        except (TypeError, ValueError, OverflowError):
            pass
    return n


def as_float(value, default=None, minimum=None, maximum=None):
    """実数へ防御的に変換する（失敗・None・空文字・NaN・無限大は default）。"""
    n = None
    if isinstance(value, bool):
        n = float(value)
    elif isinstance(value, (int, float)):
        try:
            n = float(value)          # 10**400 のような巨大 int は OverflowError
        except (TypeError, ValueError, OverflowError):
            n = None
    else:
        try:
            n = float(str(value).strip())
        except (TypeError, ValueError):
            n = None
    if n is None or n != n or n in (float("inf"), float("-inf")):
        n = default
    if n is None:
        return None
    n = float(n)
    if minimum is not None:
        n = max(float(minimum), n)
    if maximum is not None:
        n = min(float(maximum), n)
    return n


def as_list(value):
    """API 応答の「配列であるべき値」を安全に list にする（dict/その他は空リスト）。

    外部 API は HTTP 200 で `{"items": null}` や文字列を返すことがある。`data.get(k, [])`
    だと None でも文字列でも `for` で落ちる（実測 2026-10-31: JAXA links=null →
    `TypeError: 'NoneType' is not iterable`、media/CSA/INPE/ISRO も同型で例外が
    ツール外へ漏れた）。応答形状の変化で落とさないために入口で正規化する。
    """
    return value if isinstance(value, list) else []

def json_object(resp, default=None):
    """HTTP 応答の JSON を dict として取り出す（dict でなければ default）。

    200 でも中身が dict でないことがある（`null` / `[]` / HTML のエラーページ /
    レート制限のメッセージ）。そのまま `.get()` を呼ぶと AttributeError が
    ツール外へ漏れる（実測: Open-Meteo が `null` を返したときに
    `'NoneType' object has no attribute 'get'`）。
    """
    try:
        data = resp.json()
    except Exception:                 # JSON でない本文（HTML 等）
        return {} if default is None else default
    if isinstance(data, dict):
        return data
    return {} if default is None else default


def error_brief(exc) -> str:
    """例外（または既に文字列化されたエラー文）を人間向けの短い説明に変える。

    `content` は人間が読むチャネル。requests の例外は urllib3 の長い repr
    （``HTTPSConnectionPool(host='…', port=443): Max retries exceeded …``）
    を抱えているため、そのまま本文へ埋めると表示が機械のログになる
    （実測: DLR の読み込みタイムアウトで 200 字超、SIMBAD の 404 で HTML の断片）。
    `content` にはこの関数の戻り値を入れ、元の文字列は
    ``structuredContent.error``（機械向けチャネル）に残すこと。

    文字列を渡したときは「1 行目・空白の圧縮・120 字で打ち切り」だけ行う
    （`_do_query` のように呼び出し側で文字列化済みのエラーにも使える）。
    """
    import re as _re

    if isinstance(exc, str):
        return _brief_text(exc)
    if exc is None:
        return "原因不明のエラー"

    try:                                    # requests は遅延 import（規約: 起動を速く保つ）
        import requests
    except Exception:                       # pragma: no cover - 依存は必ず入っている
        requests = None
    if requests is not None and isinstance(exc, requests.RequestException):
        raw = str(exc)
        m = _re.search(r"host='([^']+)'", raw)
        host = m.group(1) if m else None
        if host is None:
            url = getattr(getattr(exc, "response", None), "url", None) \
                or getattr(getattr(exc, "request", None), "url", None)
            if isinstance(url, str) and "://" in url:
                host = url.split("://", 1)[1].split("/", 1)[0]
        mt = _re.search(r"(?:connect|read) timeout=([\d.]+)", raw)
        secs = f"（タイムアウト {mt.group(1)} 秒）" if mt else ""
        where = f"（{host}）" if host else ""
        # 継承順に注意: ConnectTimeout は ConnectionError と Timeout の両方を継承する。
        if isinstance(exc, requests.HTTPError):
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code:
                return f"サーバーが HTTP {code} を返しました{where}"
            return f"HTTP エラー{where}"
        if isinstance(exc, requests.ConnectTimeout):
            return f"{host or 'サーバー'} への接続がタイムアウトしました{secs}"
        if isinstance(exc, requests.ReadTimeout):
            return f"{host or 'サーバー'} からの応答がタイムアウトしました{secs}"
        if isinstance(exc, requests.ConnectionError):
            return f"{host or 'サーバー'} に接続できませんでした"
        if isinstance(exc, requests.Timeout):
            return f"{host or 'サーバー'} との通信がタイムアウトしました{secs}"
        return f"通信エラー（{type(exc).__name__}）{where}"
    return _brief_text(str(exc) or type(exc).__name__)


def _brief_text(text: str, limit: int = 120) -> str:
    """エラー文の表示用整形（1 行目・空白圧縮・HTML の断片化・打ち切り）。"""
    import re as _re

    s = " ".join(str(text).split())
    if not s:
        return "詳細なし"
    if s[:1] == "<" or s[:9].lower() == "<!doctype":
        return "HTML の応答が返りました（サーバー側のエラーページの可能性）"
    if len(s) > limit:
        s = s[:limit - 1] + "…"
    return s

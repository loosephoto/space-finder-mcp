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

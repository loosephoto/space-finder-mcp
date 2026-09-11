"""ツール引数の防御的な数値変換（値が不正でも例外をツール外へ漏らさない）。

MCPクライアントは型を保証しない（``"limit": "5件"`` / ``null`` / ``true`` なども届く）。
``int()`` / ``float()`` を引数へ直接適用すると ValueError/TypeError が
ツール呼び出しごと例外となって漏れるため、ツールの入口では必ず本モジュールを通す。

失敗した場合は既定値（``default``）へ落とし、``minimum``/``maximum`` でクランプする。
``default`` が ``None`` の場合は ``None`` を返すので、呼び出し側で「指定なし」として扱える。
"""

from __future__ import annotations


def as_int(value, default=None, minimum=None, maximum=None):
    """整数へ防御的に変換する（失敗・None・空文字は default）。"""
    n = None
    if isinstance(value, bool):
        n = int(value)
    elif isinstance(value, int):
        n = value
    else:
        try:
            n = int(float(str(value).strip()))
        except (TypeError, ValueError):
            n = None
    if n is None:
        n = default
    if n is None:
        return None
    n = int(n)
    if minimum is not None:
        n = max(int(minimum), n)
    if maximum is not None:
        n = min(int(maximum), n)
    return n


def as_float(value, default=None, minimum=None, maximum=None):
    """実数へ防御的に変換する（失敗・None・空文字・NaN・無限大は default）。"""
    n = None
    if isinstance(value, bool):
        n = float(value)
    elif isinstance(value, (int, float)):
        n = float(value)
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

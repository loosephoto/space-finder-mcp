"""api.nasa.gov の呼び出し予算を管理する（ツール定義は持たない）。

DEMO_KEY は **1時間あたり 30リクエスト/IP** の共有枠で、``apod``・``neo_today``・
``space_weather``(DONKI) が同じ枠を取り合う。実測では3ツールを続けて呼ぶと 429 になり、
それ以降は回復するまで毎回 429 を踏み続けていた（エラーはキャッシュしない設計のため）。

そこで「投げる前に自前で数える」:

- 直近1時間の呼び出し回数が上限に達していたら、HTTP を出さずに待ち時間を案内する
  （無駄な 429 を発生させず、LLMにも回復目安が伝わる）
- 実際に 429 を受けたら ``Retry-After`` の間は呼び出さない。ただし NASA は
  **約19時間**（実測 69306 秒）を告知することがあり、そのまま従うと全ツールが
  半日止まるため ``MAX_COOLDOWN``（1時間）で頭打ちにする
- 成功応答でも ``X-RateLimit-Remaining: 0`` なら「次は 429」なので先回りして待機する

状態はプロセス内のみ（MCPサーバーは常駐するのでセッション中有効）。
"""

from __future__ import annotations

import collections
import math
import os
import threading
import time
from typing import Optional

import requests

DEMO_KEY = "DEMO_KEY"
DEMO_LIMIT = 30            # DEMO_KEY: 1時間あたりの上限（NASA公式）
KEY_LIMIT = 1000           # 無料の開発者キー: 1時間あたりの上限（NASA公式）
WINDOW = 3600.0            # 上限の判定窓（秒）
RETRY_AFTER_FALLBACK = 60.0  # 429 に Retry-After が無い場合の待機（秒）
MAX_COOLDOWN = WINDOW        # 実際に待つ上限（秒）。NASA の Retry-After は約19時間を
                             # 告知することがあり（実測 69306）、そのまま従うと全ツールが
                             # 半日停止してしまうため上限を設けて「1時間後に再試行」する。

_lock = threading.Lock()
_calls: "collections.deque[float]" = collections.deque()
_cooldown_until = 0.0      # 429 を受けた場合の再開時刻（epoch秒）
_announced_wait = 0.0      # NASA が告知した待機秒（参考表示用。実際の待機は MAX_COOLDOWN で上限）


class BudgetExceeded(requests.RequestException):
    """自前の予算管理で拒否した（APIへは到達していない）。

    ``requests.RequestException`` を継承しているので、各ツールの
    ``except requests.RequestException`` でそのまま拾える。
    """

    def __init__(self, message: str, wait_seconds: float = 0.0):
        super().__init__(message)
        self.wait_seconds = float(wait_seconds or 0.0)


def current_key() -> str:
    """環境変数（MCPクライアントの env / リポジトリ直下の .env）から API キーを取る。"""
    return os.environ.get("NASA_API_KEY") or DEMO_KEY


def limit_for(key: Optional[str]) -> int:
    """キーごとの1時間あたり上限。DEMO_KEY は 30、それ以外（開発者キー）は 1000。"""
    return DEMO_LIMIT if (key or DEMO_KEY) == DEMO_KEY else KEY_LIMIT


def _prune(now: float) -> None:
    while _calls and _calls[0] <= now - WINDOW:
        _calls.popleft()


def check(key: Optional[str] = None) -> tuple:
    """呼んでよいか判定する。戻り: (可否, 待つべき秒数, 直近1時間の使用数)。"""
    global _cooldown_until
    k = key or current_key()
    now = time.time()
    with _lock:
        _prune(now)
        used = len(_calls)
        if now < _cooldown_until:
            return False, _cooldown_until - now, used
        if used >= limit_for(k):
            # 最も古い呼び出しが窓から外れるまで待てば枠が空く
            wait = max(0.0, (_calls[0] + WINDOW) - now) if _calls else 0.0
            return False, wait, used
        return True, 0.0, used


def record(key: Optional[str] = None) -> None:
    """これから1回投げる（投げた）ことを記録する。"""
    now = time.time()
    with _lock:
        _prune(now)
        _calls.append(now)


def note_429(key: Optional[str] = None, retry_after=None) -> float:
    """429 を受けたことを記録し、実際に待つ秒数を返す。

    ``Retry-After`` は秒数（）で返る。NASA は **約19時間**（実測 69306 秒）のような
    大きな値を告知することがあるが、そのまま従うと全ツールが半日停止するため
    ``MAX_COOLDOWN``（=1時間）で頭打ちにする。告知値は ``_announced_wait`` に残して
    案内文に含める（APIキーを設定すべき根拠になる）。
    """
    global _cooldown_until, _announced_wait
    wait = RETRY_AFTER_FALLBACK
    try:
        if retry_after is not None:
            v = float(str(retry_after).strip())
            if v > 0:
                _announced_wait = v
                wait = min(v, MAX_COOLDOWN)
    except (TypeError, ValueError):
        pass
    now = time.time()
    with _lock:
        _cooldown_until = max(_cooldown_until, now + wait)
    return wait


def note_response_headers(key: Optional[str] = None, headers=None) -> None:
    """NASA 応答ヘッダの残量を見て、残り0なら先回りして待機に入る。

    ``X-RateLimit-Remaining: 0`` は「次は 429 になる」という確実な合図なので、
    429 を踏んでからではなくこの時点で待機に入る。
    """
    h = headers or {}
    try:
        rem = str(h.get("X-RateLimit-Remaining", "")).strip()
    except AttributeError:
        return
    if rem == "0":
        note_429(key, h.get("Retry-After"))


def human_wait(seconds: float) -> str:
    """待ち時間を日本語にする（例: 「約30秒」「約2分」「約1時間」）。

    切り上げ（ceil）で丸める。int(s//60)+1 のような書き方だと 3600 秒が
    「約2時間」と過大表示されるため。
    """
    s = max(0.0, float(seconds or 0.0))
    if s < 60:
        return f"約{math.ceil(s)}秒"
    if s < 3600:
        return f"約{math.ceil(s / 60)}分"
    return f"約{math.ceil(s / 3600)}時間"


def blocked_message(key: Optional[str] = None) -> str:
    """呼べない理由に応じた案内文。呼べる場合は空文字。

    「429 を受けたための待機（cooldown）」と「1時間の枠を使い切った」は
    原因も対処も違うため、文言を分ける。
    """
    ok, wait, used = check(key)
    if ok:
        return ""
    k = key or current_key()
    if time.time() < _cooldown_until:
        extra = ""
        if _announced_wait > MAX_COOLDOWN * 1.5:
            extra = (f"NASA 側は{human_wait(_announced_wait)}の待機を告知していますが、"
                     "まず上限の1時間後に再試行します。")
        return (f"NASA API から一時的な制限（429）を受けました。{human_wait(wait)}後に自動で再試行します。"
                f"（DEMO_KEY は1時間 {DEMO_LIMIT}リクエスト/IP の共有枠です。NASA_API_KEY に"
                f"無料の開発者キーを設定すると緩和されます。）{extra}")
    return wait_text(wait, used, k)


def wait_text(seconds: float, used: int, key: Optional[str] = None) -> str:
    """呼ばずに待つ場合の案内文。"""
    k = key or current_key()
    limit = limit_for(k)
    if k == DEMO_KEY:
        return (f"NASA API の呼び出し枠（DEMO_KEY: 1時間 {limit}リクエスト/IP の共有枠）を"
                f"使い切りました（直近1時間 {used}回）。{human_wait(seconds)}後に自動的に回復します。"
                "NASA_API_KEY に無料の開発者キー（1時間1,000リクエスト）を設定すると枠が広がります。")
    return (f"NASA API の呼び出し枠（1時間 {limit}リクエスト）を使い切りました（直近1時間 {used}回）。"
            f"{human_wait(seconds)}後に回復します。")


def redact(text) -> str:
    """エラー文言などから API キーの値を伏せる（キーを応答に載せない）。

    requests の例外文字列は URL 全体を含むため `api_key=<実際のキー>` がそのまま
    structuredContent に載ってしまう（実測: apod の 429 応答）。DEMO_KEY 以外の
    キーを設定している利用者で秘密が漏れるので、外へ出す文字列はここを通す。
    """
    import re as _re
    return _re.sub(r"(api_key=)[^&\s\"']+", r"\1***", str(text))


def status(key: Optional[str] = None) -> dict:
    """LLM向けの予算状況（structuredContent に添える用）。"""
    k = key or current_key()
    now = time.time()
    with _lock:
        _prune(now)
        used = len(_calls)
        cool = max(0.0, _cooldown_until - now)
    return {"key": "DEMO_KEY" if k == DEMO_KEY else "custom",
            "limit_per_hour": limit_for(k), "used_last_hour": used,
            "cooldown_seconds": round(cool, 1),
            "remaining": max(0, limit_for(k) - used)}

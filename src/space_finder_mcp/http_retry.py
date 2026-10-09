"""HTTP GET の薄い再試行ラッパ（一時的な接続失敗だけを再試行する）。

背景: 外部 API を叩く各所は `requests.get` を直接呼ぶ。稀に **一時的な名前解決失敗**
（`Temporary failure in name resolution`）で 1 回のツール呼び出しが失敗するが、これは
待てば直る類の失敗で、再試行すれば成功する（実測: 国立天文台イベント一覧の取得が
DNS 解決失敗で落ち、再実行では成功した）。

設計上の約束:
- **再試行するのは `requests.ConnectionError` だけ**（DNS 解決失敗・接続拒否・接続リセット）。
  これらは即座に失敗を返すので、短い間隔での再試行は安い。
- **HTTP エラー（4xx/5xx）やタイムアウトは再試行しない**。遮断（403 等）は各モジュールが
  記憶して fail fast する方針（`celestrak` / `nasa_budget` 参照）を壊さないため。タイムアウトを
  再試行すると connect/read の上限を二重・三重に待つことになり、並列実行の他ツールまで
  待たせる（`tests/test_network_resilience.py` の教訓）。
- モジュール属性 `requests.get` を**呼び出し時に**参照する（`check-tools.py --offline` が
  `requests.get` を差し替えて全断を注入するため、束縛してしまうと差し替えが効かない）。
"""
from __future__ import annotations

import time
from typing import Optional

import requests

RETRIES = 2          # 初回 + 再試行 2 回 = 最大 3 回
BACKOFF = 0.5        # 秒。試行ごとに指数的に増やす（0.5 → 1.0）


def get(url: str, *, retries: int = RETRIES, backoff: float = BACKOFF,
        timeout=None, headers: Optional[dict] = None, **kwargs) -> requests.Response:
    """`requests.get` の再試行付き版（`ConnectionError` のみ再試行）。

    再試行を使い切ったら最後の `ConnectionError` を送出する（呼び出し側の既存の
    `except requests.RequestException` でそのまま受けられる）。
    """
    attempt = 0
    while True:
        try:
            return requests.get(url, timeout=timeout, headers=headers, **kwargs)
        except requests.ConnectionError:
            if attempt >= retries:
                raise
            time.sleep(backoff * (2 ** attempt))
            attempt += 1

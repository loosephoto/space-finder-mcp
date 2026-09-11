"""キャッシュ基盤（ツール定義は持たない）。

space-finder-mcp の呼び出しを「3層」で軽くするための共通部品:

1. 不変アセット（Trek タイル / NASA 画像資産 / Blue Marble 等）
   → disk_get() でディスクに保存。URL が同じならネットワークへ出ない。
2. 揮発データ（RSS / 打ち上げ / DONKI / STAC 検索 / 天気 / ジオコーディング等）
   → @ttl_cache(秒) でプロセス内に TTL 付きで保持。
3. 高コスト計算（例: 「その観測地の次の日食」探索 23秒）
   → 引数オブジェクトをキーにせず、丸めた値（緯度経度・日付）をキーにして
     @ttl_cache を使う（呼び出し側でキーを作る）。

設計上の約束:
- 例外はキャッシュしない（一時的な失敗が TTL の間ずっと固定化するのを防ぐ）。
- エラー応答もキャッシュしない（skip_if=is_error_result を各所で指定）。
- 取得に失敗した場合は、期限切れでもディスクの古い内容を返す（stale-if-error）。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from functools import wraps
from typing import Callable, Optional

# ディスクキャッシュのルート（既存の Blue Marble キャッシュと同じ場所の流儀）
CACHE_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp",
                          "space_finder_mcp", "cache")

# 既定 TTL（秒）。データの性質に合わせて呼び出し側で上書きする。
TTL_TLE = 6 * 3600          # 軌道要素（既存 celestrak と同じ考え方）
TTL_DAILY = 24 * 3600       # データセット一覧・ジオコーディングなど準静的なもの
TTL_HOURLY = 3600           # 日次データ（APOD/NEO）・EO Dashboard
TTL_FORECAST = 1800         # 天気予報・メディア検索
TTL_SHORT = 600             # ニュース・打ち上げ・宇宙天気・STAC 検索
TTL_ASSET = 30 * 86400      # 不変の画像・タイル

_is_lock = threading.Lock()


def is_error_result(result) -> bool:
    """CallToolResult（または同等の dict）がエラーかどうか。

    structuredContent.error があればエラー扱い。dict を直接キャッシュする
    ヘルパでも使えるように両方を受け付ける。
    """
    if isinstance(result, dict):
        return bool(result.get("error"))
    sc = getattr(result, "structuredContent", None)
    return isinstance(sc, dict) and bool(sc.get("error"))


def ttl_cache(seconds: float, maxsize: int = 128,
              skip_if: Optional[Callable] = None):
    """関数の戻り値をプロセス内に TTL 付きでキャッシュするデコレータ。

    - 引数がハッシュ可能でない場合はキャッシュせず素通しする（誤ったキー衝突を避ける）
    - 呼び出しが例外を投げたらキャッシュしない
    - skip_if(戻り値) が True なら保存しない（エラー応答を固定化しないため）
    """
    def deco(fn):
        store: dict = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                key = (args, tuple(sorted(kwargs.items())))
                hash(key)
            except TypeError:
                return fn(*args, **kwargs)   # キーにできない引数はキャッシュ対象外
            now = time.time()
            with _is_lock:
                hit = store.get(key)
                if hit is not None and hit[0] > now:
                    return hit[1]
            value = fn(*args, **kwargs)      # 例外はそのまま伝播
            if skip_if is None or not skip_if(value):
                with _is_lock:
                    if len(store) >= maxsize:
                        for k in [k for k, v in store.items() if v[0] <= now]:
                            store.pop(k, None)
                        if len(store) >= maxsize:
                            store.pop(min(store, key=lambda k: store[k][0]), None)
                    store[key] = (now + seconds, value)
            return value

        def cache_clear():
            with _is_lock:
                store.clear()

        def cache_info():
            return {"entries": len(store), "ttl_seconds": seconds, "maxsize": maxsize}

        wrapper.cache_clear = cache_clear
        wrapper.cache_info = cache_info
        return wrapper
    return deco


def disk_cache_path(key: str, subdir: str = "asset") -> str:
    """URL などから決まるディスクキャッシュのパスを返す（sha1 名）。"""
    name = hashlib.sha1(str(key).encode("utf-8")).hexdigest()
    return os.path.join(CACHE_ROOT, subdir, name)


def disk_get(url: str, *, subdir: str = "asset", ttl: float = TTL_ASSET,
             timeout: int = 30, headers: Optional[dict] = None,
             max_bytes: int = 0) -> Optional[bytes]:
    """バイナリ資産をディスクキャッシュ経由で取得する。戻り: bytes / None。

    - TTL 内のキャッシュがあればネットワークへ出ない
    - 404（恒久的に存在しない）は 0 バイトのマーカーを置き、TTL 内は再要求しない
    - 取得失敗時は期限切れでも古い内容を返す（stale-if-error）
    - max_bytes > 0 なら上限超過は None（呼び出し側の常識的なサイズ制限を尊重）
    """
    import requests
    path = disk_cache_path(url, subdir)
    now = time.time()
    if os.path.exists(path) and (now - os.path.getmtime(path)) < ttl:
        with open(path, "rb") as fh:
            data = fh.read()
        if not data:                      # 0 バイト = 「無し」のマーカー
            return None
        return None if (max_bytes and len(data) > max_bytes) else data
    got = None
    missing = False
    try:
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        if r.status_code == 200 and r.content:
            got = r.content
        elif r.status_code == 404:
            missing = True
    except requests.RequestException:
        got = None
    if got is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "{}.{}.tmp".format(path, os.getpid())
        with open(tmp, "wb") as fh:       # 部分書き込みを見せないよう atomic replace
            fh.write(got)
        os.replace(tmp, path)
        return None if (max_bytes and len(got) > max_bytes) else got
    if missing:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb"):
            pass
        return None
    if os.path.exists(path):              # stale-if-error
        with open(path, "rb") as fh:
            data = fh.read()
        if data:
            return None if (max_bytes and len(data) > max_bytes) else data
    return None


def disk_cache_stats() -> dict:
    """ディスクキャッシュの使用量（デバッグ・運用確認用）。"""
    total = 0
    files = 0
    if os.path.isdir(CACHE_ROOT):
        for root, _dirs, names in os.walk(CACHE_ROOT):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                    files += 1
                except OSError:
                    pass
    return {"root": CACHE_ROOT, "files": files, "bytes": total}

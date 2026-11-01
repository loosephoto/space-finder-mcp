"""JSON ネイティブ化の唯一の境界（`structuredContent` の受け渡し規約）。

なぜ必要か
----------
このサーバーは JPL/Skyfield の計算結果をそのまま `structuredContent` に載せている。
Skyfield は `numpy.float64` / `numpy.str_` / `ndarray` を返すので、値をそのまま
JSON-RPC へ流すと「Python では通るが JSON ではない」値が混ざる（実測 2026-11-01:
`constellation_now` の `bodies[]` に 45 個の `float64`、`solar_system_now` の
`planets[].au` に 9 個の `float64`。`json.dumps` がたまたま float の派生として
通るだけで、クライアント側の実装によっては型が変わる）。

守る規約
--------
1. `structuredContent` は **JSON の7型（object/array/string/number/boolean/null）だけ**で
   構成する。変換は `server._delivered` の 1 か所で行い、各ツールには書かせない。
2. `NaN` / `Infinity` は JSON に存在しないので `null` にする（計算不能を「値」として
   流さない。`--fuzz` が 0 件でも、外部データ由来で混入し得る）。
3. 変換は**新しいコンテナを作る**（ttl_cache が共有している戻り値を書き換えない）。
4. 数値の丸めはしない（丸めは値の意味を変えるので、JSON 化の責務ではない）。
"""
from __future__ import annotations

import datetime as _dt
import decimal
import math
from typing import Any

def _from_numpy(value: Any) -> Any:
    """numpy のスカラー／配列を Python の型へ（numpy を import しない）。"""
    mod = type(value).__module__ or ""
    if not mod.startswith("numpy"):
        return _MISS
    if hasattr(value, "tolist"):          # ndarray / matrix
        return value.tolist()
    if hasattr(value, "item"):            # numpy スカラー
        return value.item()
    return str(value)


class _Missing:
    __slots__ = ()


_MISS = _Missing()


def to_json(value: Any, *, _depth: int = 0) -> Any:
    """値を JSON ネイティブな構造へ変換する（再帰）。

    dict のキーは文字列にする（JSON のキーは文字列のみ。`int` キーをそのまま
    渡すとクライアント側で "1" と 1 が混在する）。
    """
    if _depth > 64:                       # 循環参照の保険（外部データでも安全側）
        return None
    # numpy の float64 / str_ は Python の float / str の**派生クラス**なので、
    # isinstance の判定より先に外す（後ろで判定すると numpy のまま素通りする。
    # 実測 2026-11-01: np.float64 が float として通っていた）。
    if (type(value).__module__ or "").startswith("numpy"):
        converted = _from_numpy(value)
        if converted is not _MISS:
            return to_json(converted, _depth=_depth + 1)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k if isinstance(k, str) else str(k)] = to_json(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = value
        if isinstance(value, (set, frozenset)):
            items = sorted(value, key=repr)   # 出力を決定的にする（テスト・差分用）
        return [to_json(v, _depth=_depth + 1) for v in items]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if hasattr(value, "value"):            # Enum など
        return to_json(value.value, _depth=_depth + 1)
    if hasattr(value, "__fspath__"):       # pathlib.Path
        return str(value)
    return str(value)


def is_json_native(value: Any) -> bool:
    """JSON ネイティブだけで構成されているか（ゲート・テスト用の検査）。"""
    if (type(value).__module__ or "").startswith("numpy"):
        return False                      # float64 / str_ は float / str の派生
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and is_json_native(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(is_json_native(v) for v in value)
    return False

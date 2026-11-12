"""JPL de421 天体暦の共有ローダ（プロセス内で 1 度だけ開く）。

なぜ集約するか
--------------
`ground_view` / `skyfield_pos` / `solar_eclipse` / `solar_system` が、それぞれ
呼び出しのたびに `Loader(dir)("de421.bsp")` を実行していた。そのため

(a) 16MB の SPK を毎回開き直し、ファイル記述子が解放されない
    （実測: 3 回ずつ呼ぶと `ResourceWarning: unclosed file ... de421.bsp` が 9 件）、
(b) 時刻系（timescale）の初期化も毎回走る、

という 2 つの無駄があった。天体暦は読み取り専用の不変アセットなので、1 度だけ
開いて全モジュールで共有する。星座境界（IAU）も同様に 1 度だけ読む
（Skyfield 同梱データを使うのでダウンロードは発生しない）。
"""
from __future__ import annotations

import atexit
import os
import threading

from .cache import local_appdata

#: 天体暦・星座データの置き場（cache.local_appdata() 経由＝cwd 非依存）
DATA_DIR = os.path.join(local_appdata(), "Temp", "skyfield_data")
os.makedirs(DATA_DIR, exist_ok=True)

_LOCK = threading.Lock()
_TS = None
_EPH = None
_MAP = None


def load():
    """(timescale, ephemeris) を返す（初回だけ読み込み、以後は同じ実体）。"""
    global _TS, _EPH
    with _LOCK:
        if _EPH is None:
            from skyfield.api import Loader       # 遅延 import（起動を遅くしない）
            loader = Loader(DATA_DIR, verbose=False)
            _TS = loader.timescale()
            _EPH = loader("de421.bsp")
        return _TS, _EPH


def constellation_map():
    """IAU 星座境界の判定関数を返す（Skyfield 同梱データ。通信しない）。"""
    global _MAP
    with _LOCK:
        if _MAP is None:
            from skyfield.api import load_constellation_map
            _MAP = load_constellation_map()
        return _MAP


class _TimescaleOnly:
    """`(loader, eph)` 形式の戻り値を使う既存呼び出し元のための互換シム。"""

    def timescale(self):
        """共有の timescale を返す（新規生成しない）。"""
        return load()[0]


def timescale_shim():
    """`(loader_shim, ephemeris)` を返す（`loader.timescale()` だけを使う既存コード向け）。"""
    _ts, eph = load()
    return _TimescaleOnly(), eph


def _close():
    """終了時に SPK のファイルハンドルを閉じる（ResourceWarning を残さない）。"""
    global _TS, _EPH, _MAP
    with _LOCK:
        eph, _EPH, _TS, _MAP = _EPH, None, None, None
    try:
        if eph is not None and hasattr(eph, "close"):
            eph.close()
    except Exception:                             # 終了処理はベストエフォート
        pass


atexit.register(_close)

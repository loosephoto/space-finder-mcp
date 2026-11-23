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

初回ダウンロードの扱い（issue #2）
----------------------------------
de421.bsp は初回だけ取得が必要で、Skyfield の `download()` は urllib で取りに行く。
TLS 検証を差し替えるプロキシ配下では `CERTIFICATE_VERIFY_FAILED` で失敗し、
「なぜ落ちたか・どこへ置けばよいか」がエラー文面から読み取れなかった。そこで
**取得だけを自前で行い**、

1. `SPACE_FINDER_CA_BUNDLE` / `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`
   で明示された CA（プロキシの自己署名ルートなど）
2. OS の証明書ストア（`ssl.create_default_context()`）
3. certifi のバンドル（インストールされていれば）

の順に試し、取得元も JPL 本家 → NAIF の旧版アーカイブへフォールバックする。
**証明書の検証は一切無効化しない**（CA を渡してもらう）。全滅したときは
`EphemerisUnavailable` を送出し、その文面に

- 配置先の絶対パス（`SPACE_FINDER_DATA_DIR` で差し替え可）
- 手動取得のコマンド（`curl -L -o <配置先> <URL>`）
- CA の指定方法・取得済みファイルの指定方法（`SPACE_FINDER_DE421`）

を入れる。ツール側は `error_result()` でこれを `structuredContent.error =
"ephemeris_unavailable"` の結果に変換する（例外をプロトコルへ漏らさない）。
"""
from __future__ import annotations

import atexit
import os
import ssl
import threading
import urllib.request

from .cache import local_appdata

#: 取得元（JPL 本家 → NAIF の旧版アーカイブ。どちらも 200 を実測）
DE421_URLS = (
    "https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp",
    "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp",
)
DE421_NAME = "de421.bsp"
#: 壊れた・途中までのファイルを掴まないための下限（実体は約 16.8MB）
DE421_MIN_BYTES = 5 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30.0


def data_dir():
    """天体暦・星座データの置き場を返す（`SPACE_FINDER_DATA_DIR` で差し替え可）。"""
    d = (os.environ.get("SPACE_FINDER_DATA_DIR") or "").strip()
    if not d:
        d = os.path.join(local_appdata(), "Temp", "skyfield_data")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:                              # 読めない場所でも import は通す
        pass
    return d


#: 天体暦・星座データの置き場（cache.local_appdata() 経由＝cwd 非依存）
DATA_DIR = data_dir()


def de421_path():
    """de421.bsp の実体パス（`SPACE_FINDER_DE421` で明示指定できる）。"""
    explicit = (os.environ.get("SPACE_FINDER_DE421") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    return os.path.join(data_dir(), DE421_NAME)


class EphemerisUnavailable(RuntimeError):
    """de421.bsp を用意できない（初回ダウンロード失敗・ファイル破損）。

    文面に「配置先・手動取得のコマンド・CA の指定方法」を含める（利用者が
    エラーメッセージだけで対処できるようにする）。`reason` は原因の短い要約で、
    `structuredContent.detail` に入れる。
    """

    def __init__(self, reason, path=None, errors=None):
        self.reason = _short(reason)[:400]
        self.path = path or de421_path()
        self.data_dir = os.path.dirname(self.path)
        self.errors = list(errors or [])
        super().__init__(self._build_message())

    def _build_message(self):
        return (
            "天体暦 de421.bsp を用意できませんでした（初回のみダウンロードが必要です）。\n"
            "配置先: {path}\n"
            "対処1（最も確実）: 次のコマンドで手動取得し、上のパスに置いてください\n"
            "  curl -L -o \"{path}\" {url}\n"
            "対処2: プロキシ等の CA 証明書を環境変数で指定してください"
            "（SPACE_FINDER_CA_BUNDLE=/path/to/ca.pem。SSL_CERT_FILE / REQUESTS_CA_BUNDLE も可）\n"
            "対処3: 取得済みのファイルがあるなら SPACE_FINDER_DE421=/path/to/de421.bsp で指定できます\n"
            "原因: {reason}"
        ).format(path=self.path, url=DE421_URLS[0], reason=self.reason)


def _short(value):
    """例外・文字列を 1 行の短い説明にする（改行と連続空白を畳む）。"""
    if not isinstance(value, str):
        value = "{}: {}".format(type(value).__name__, value)
    return " ".join(str(value).split())


def _is_cert_failure(exc):
    """証明書検証の失敗か（＝CA を替えれば直る類か）を判定する。"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError):
            return True
        if isinstance(exc, ssl.SSLError):
            return True
        txt = str(exc).lower()
        if "certificate" in txt or "ssl" in txt:
            return True
        exc = getattr(exc, "reason", None) or getattr(exc, "__cause__", None)
    return False


def _ca_contexts():
    """(ラベル, SSLContext) を「明示 CA → OS → certifi」の順に返す（検証は無効化しない）。"""
    out, seen = [], set()

    def add(label, cafile=None):
        key = cafile or label
        if key in seen:
            return
        try:
            ctx = (ssl.create_default_context(cafile=cafile) if cafile
                   else ssl.create_default_context())
        except (OSError, ssl.SSLError, ValueError):
            return
        seen.add(key)
        out.append((label, ctx))

    for env in ("SPACE_FINDER_CA_BUNDLE", "SSL_CERT_FILE",
                "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        val = (os.environ.get(env) or "").strip()
        if val and os.path.isfile(val):
            add(env, val)
    add("OS 証明書ストア")
    try:
        import certifi                              # noqa: F401 - 任意依存
        add("certifi", certifi.where())
    except Exception:                               # 未インストールなら OS だけで試す
        pass
    return out


def _fetch(url, dest, ctx):
    """`url` を `dest` へ落とす（`.part` に書いてから置換・バイト数も検査）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "space-finder-mcp (de421 download)"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, context=ctx, timeout=DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
            if got < DE421_MIN_BYTES or (total and got != total):
                raise OSError("受信が不完全です（{} / {} バイト）".format(got, total or "?"))
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _complete(path):
    """そのサイズなら「取得済み」と見なせるか。"""
    try:
        return os.path.getsize(path) >= DE421_MIN_BYTES
    except OSError:
        return False


def ensure_de421(path=None):
    """de421.bsp の実体パスを返す（無ければ取得する）。失敗は `EphemerisUnavailable`。"""
    dest = path or de421_path()
    if _complete(dest):
        return dest
    if os.path.exists(dest):                        # 途中までのファイルは使わない
        try:
            os.remove(dest)
        except OSError:
            pass
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)

    contexts = _ca_contexts()
    errors = []
    for url in DE421_URLS:
        for label, ctx in contexts:
            try:
                _fetch(url, dest, ctx)
                if _complete(dest):                 # 落ちたファイルを「取得済み」にしない
                    return dest
                errors.append("{} [{}] 取得後のサイズが不足しています（{} バイト）"
                              .format(url, label, os.path.getsize(dest)
                                      if os.path.exists(dest) else 0))
                continue
            except Exception as e:                  # noqa: BLE001 - 理由を集めて文面に出す
                errors.append("{} [{}] {}".format(url, label, _short(e)))
                if not _is_cert_failure(e):
                    break                           # 証明書以外は次のミラーへ
    reason = errors[-1] if errors else "取得元に接続できませんでした"
    raise EphemerisUnavailable(reason, dest, errors)


def error_result(exc, source="JPL de421"):
    """`EphemerisUnavailable` をツールのエラー結果にする（例外を外へ漏らさない）。"""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=str(exc))],
        structuredContent={"error": "ephemeris_unavailable",
                           "detail": getattr(exc, "reason", str(exc))[:400],
                           "de421_path": getattr(exc, "path", de421_path()),
                           "data_dir": getattr(exc, "data_dir", data_dir()),
                           "source": source},
    )


_LOCK = threading.Lock()
_TS = None
_EPH = None
_MAP = None


def load():
    """(timescale, ephemeris) を返す（初回だけ読み込み、以後は同じ実体）。"""
    global _TS, _EPH
    with _LOCK:
        if _EPH is None:
            path = ensure_de421()
            try:
                from skyfield.api import Loader       # 遅延 import（起動を遅くしない）
                loader = Loader(os.path.dirname(path), verbose=False)
                ts = loader.timescale()
                eph = loader(os.path.basename(path))
            except EphemerisUnavailable:
                raise
            except Exception as e:                    # 壊れたファイル等も対処手順つきで返す
                raise EphemerisUnavailable(e, path) from e
            _TS, _EPH = ts, eph
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

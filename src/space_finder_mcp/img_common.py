"""画像合成の共通ヘルパー（ツール定義は持たない）。

各マップツール（planetary_map / planetary_rover / satellite_map / sky_overlay /
solar_system / solar_eclipse）が個別に持っていた以下を1箇所に集約する:

- 日本語フォント探索（Windows / macOS / Linux の標準日本語フォント → 無ければ PIL 既定フォント）
- JPEG エンコード（上限バイト数を超えたら縮小して再エンコード）
- 経度±180°（アンチメリジアン）をまたぐ軌道線の分割

PIL は重いので各関数内で遅延 import する（既存の各ツールと同じ方針）。
"""
from __future__ import annotations

import glob as _glob
import io
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

from .cache import CACHE_ROOT

# matplotlib はプロセス全体の状態（rcParams・現在の figure・フォントキャッシュ）を共有するため
# スレッド安全ではない。並列ツール呼び出しで図が混ざらないよう、matplotlib で描く経路だけを
# このロックで直列化する（Pillow 合成だけの経路はロック不要なので触らない）。
RENDER_LOCK = threading.RLock()

# ---------- 日本語フォント探索（Windows / macOS / Linux 共通） ----------
# 画像内の日本語が「豆腐（□）」になるのを防ぐため、OS ごとの標準日本語フォントを順に探す。
# Windows 固定パスだけを見ると macOS / Linux では PIL 既定フォントに落ちて日本語が全部化ける。
#
# 探索順（先頭ほど優先）:
#   1. 環境変数 SPACE_FINDER_FONT / SPACE_FINDER_FONT_BOLD（明示指定は無条件で尊重）
#   2. OS 標準パス（下表）
#   3. 標準フォントディレクトリの走査（ディストリビューション差を吸収）
# 候補は「実際に日本語グリフを持っているか」を cmap で検証してから採用する
# （名前だけでは判定できない。例: DejaVu Sans は日本語なし）。
_FONT_ENV = {False: "SPACE_FINDER_FONT", True: "SPACE_FINDER_FONT_BOLD"}
_FONT_ENV_COMMON = "SPACE_FINDER_FONT"
# 日本語グリフの有無を判定する代表文字（cmap を直接調べる）
_CJK_PROBE = ("日", "曜", "語")

_WIN_FONTS = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")

_FONT_CANDIDATES = {
    False: (
        # Windows
        os.path.join(_WIN_FONTS, "meiryo.ttc"),
        os.path.join(_WIN_FONTS, "YuGothR.ttc"),
        os.path.join(_WIN_FONTS, "yugothb.ttc"),
        os.path.join(_WIN_FONTS, "msgothic.ttc"),
        # macOS（ヒラギノ角ゴシック。10.13 以降は日本語ファイル名）
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴ ProN W3.otf",
        "/System/Library/Fonts/Hiragino Sans W3.ttc",
        "/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/Library/Fonts/ヒラギノ角ゴ ProN W3.otf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        # Linux（Noto CJK → IPAex → IPA → VL/Takao）
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
        "/usr/share/fonts/truetype/ipaexfont-gothic/ipaexg.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/truetype/takao-gothic/TakaoPGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # 日本語なし（最後の保険）
    ),
    True: (
        # Windows
        os.path.join(_WIN_FONTS, "meiryob.ttc"),
        os.path.join(_WIN_FONTS, "YuGothB.ttc"),
        os.path.join(_WIN_FONTS, "yugothb.ttc"),
        os.path.join(_WIN_FONTS, "msgothic.ttc"),
        # macOS（W6 が太字相当）
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴ ProN W6.otf",
        "/System/Library/Fonts/Hiragino Sans W6.ttc",
        "/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/Library/Fonts/ヒラギノ角ゴ ProN W6.otf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",   # 太字が無ければ W3 で代用
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
        "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
        "/usr/share/fonts/truetype/ipaexfont-gothic/ipaexg.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
        "/usr/share/fonts/truetype/takao-gothic/TakaoPGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # 日本語なし（最後の保険）
    ),
}

# 標準フォントディレクトリを走査するときのファイル名パターン（(標準, 太字) の優先順）。
# パッケージ構成・配置はディストリビューションごとに変わるので、既知パスだけで判定しない。
_FONT_FAMILY_GLOBS = (
    ("ヒラギノ角ゴシック W3.ttc", "ヒラギノ角ゴシック W6.ttc"),
    ("ヒラギノ角ゴ ProN W3.otf", "ヒラギノ角ゴ ProN W6.otf"),
    ("Hiragino*W3.ttc", "Hiragino*W6.ttc"),
    ("NotoSansCJKjp-Regular.otf", "NotoSansCJKjp-Bold.otf"),
    ("NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc"),
    ("NotoSansJP-Regular.otf", "NotoSansJP-Bold.otf"),
    ("NotoSansJP-Regular.ttf", "NotoSansJP-Bold.ttf"),
    ("NotoSansCJKjp*.otf", "NotoSansCJKjp*.otf"),
    ("NotoSansCJK*.ttc", "NotoSansCJK*.ttc"),
    ("ipaexg.ttf", "ipaexg.ttf"),
    ("ipag.ttf", "ipagp.ttf"),
    ("fonts-japanese-gothic.ttf", "fonts-japanese-gothic.ttf"),
    ("VL-Gothic-Regular.ttf", "VL-Gothic-Regular.ttf"),
    ("TakaoPGothic.ttf", "TakaoPGothic.ttf"),
    ("meiryo.ttc", "meiryob.ttc"),
    ("YuGothR.ttc", "YuGothB.ttc"),
    ("msgothic.ttc", "msmincho.ttc"),
    ("Arial Unicode.ttf", "Arial Unicode.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),   # 日本語なし（最後の保険）
)

# matplotlib に渡すフォントファミリ名の候補（先頭ほど優先。PIL 側で見つからない場合の保険）
_MPL_FAMILY_CANDIDATES = (
    "Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans JP", "Hiragino Sans",
    "Hiragino Kaku Gothic ProN", "ヒラギノ角ゴシック", "Yu Gothic", "Meiryo",
    "IPAexGothic", "IPAGothic", "TakaoPGothic", "VL Gothic", "Arial Unicode MS",
)
def _font_search_dirs() -> List[str]:
    """OS 標準のフォントディレクトリ（存在しないものは無視してよい）。"""
    if os.name == "nt":
        return [os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    if sys.platform == "darwin":
        return ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
                "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts", "/usr/share/fonts/truetype",
            "/usr/share/fonts/opentype", os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts")]


@lru_cache(maxsize=4)
def _scan_font_dirs(bold: bool) -> Tuple[str, ...]:
    """標準フォントディレクトリを走査して日本語フォント候補を返す（環境差の吸収）。"""
    out: List[str] = []
    dirs = [d for d in _font_search_dirs() if d and os.path.isdir(d)]
    for reg, bld in _FONT_FAMILY_GLOBS:
        pat = bld if bold else reg
        if not pat:
            continue
        for d in dirs:
            try:
                hits = sorted(_glob.glob(os.path.join(d, "**", pat), recursive=True))
            except Exception:
                continue
            for p in hits:
                if p not in out:
                    out.append(p)
    return tuple(out)


def _cmap_subtable_has(fh, off: int, chars) -> Optional[bool]:
    """cmap サブテーブル（format 0/4/6/12）に chars が揃っているか。None=判定不能。"""
    fh.seek(off)
    fmt_b = fh.read(2)
    if len(fmt_b) < 2:
        return None
    fmt = int.from_bytes(fmt_b, "big")
    cps = [ord(c) for c in chars]

    if fmt == 4:
        fh.seek(off + 2)
        length = int.from_bytes(fh.read(2), "big")
        if length < 16 or length > 8_000_000:
            return None
        fh.seek(off)
        data = fh.read(length)
        if len(data) < length:
            return None
        n = int.from_bytes(data[6:8], "big") // 2
        if n <= 0:
            return None
        ends_b, starts_b = 14, 14 + n * 2 + 2
        delta_b, ro_b = starts_b + n * 2, starts_b + n * 4
        for cp in cps:
            seg = None
            for k in range(n):
                if int.from_bytes(data[ends_b + k * 2: ends_b + k * 2 + 2], "big") >= cp:
                    seg = k
                    break
            if seg is None:
                return False
            start = int.from_bytes(data[starts_b + seg * 2: starts_b + seg * 2 + 2], "big")
            if cp < start:
                return False
            ro = int.from_bytes(data[ro_b + seg * 2: ro_b + seg * 2 + 2], "big")
            if ro == 0:
                delta = int.from_bytes(data[delta_b + seg * 2: delta_b + seg * 2 + 2], "big",
                                       signed=True)
                if ((cp + delta) & 0xFFFF) == 0:
                    return False
            else:
                addr = ro_b + seg * 2 + ro + (cp - start) * 2
                if addr + 2 > len(data):
                    return False
                if int.from_bytes(data[addr: addr + 2], "big") == 0:
                    return False
        return True

    if fmt == 12:
        fh.seek(off + 12)
        ng_b = fh.read(4)
        if len(ng_b) < 4:
            return None
        ng = int.from_bytes(ng_b, "big")
        if ng <= 0 or ng > 500_000:
            return None
        fh.seek(off + 16)
        groups = fh.read(ng * 12)
        if len(groups) < ng * 12:
            return None
        for cp in cps:
            lo, hi, ok = 0, ng - 1, False
            while lo <= hi:
                mid = (lo + hi) // 2
                s = int.from_bytes(groups[mid * 12: mid * 12 + 4], "big")
                e = int.from_bytes(groups[mid * 12 + 4: mid * 12 + 8], "big")
                if cp < s:
                    hi = mid - 1
                elif cp > e:
                    lo = mid + 1
                else:
                    ok = int.from_bytes(groups[mid * 12 + 8: mid * 12 + 12], "big") != 0
                    break
            if not ok:
                return False
        return True

    if fmt == 6:
        fh.seek(off + 6)
        hdr = fh.read(4)
        if len(hdr) < 4:
            return None
        first, count = int.from_bytes(hdr[0:2], "big"), int.from_bytes(hdr[2:4], "big")
        fh.seek(off + 10)
        gids = fh.read(count * 2)
        if len(gids) < count * 2:
            return None
        for cp in cps:
            idx = cp - first
            if idx < 0 or idx >= count or int.from_bytes(gids[idx * 2: idx * 2 + 2], "big") == 0:
                return False
        return True

    if fmt == 0:
        fh.seek(off + 6)
        data = fh.read(256)
        if len(data) < 256:
            return None
        for cp in cps:
            if cp > 255 or data[cp] == 0:
                return False
        return True

    return None

def _cmap_has_glyphs(path: str, chars) -> Optional[bool]:
    """フォントファイルが chars のグリフを全て持つか調べる（True/False/None=判定不能）。

    True=あり / False=欠けている（その文字は豆腐になる）/ None=未対応形式・読めない。
    fontTools 等の追加依存を足さず、cmap テーブルだけを最小限読む。
    """
    if not path:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12:
                return None
            if head[:4] == b"ttcf":                      # TrueType Collection
                fh.seek(12)
                sfnt = int.from_bytes(fh.read(4), "big")
            elif head[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                sfnt = 0
            else:
                return None
            fh.seek(sfnt + 4)
            num_tables = int.from_bytes(fh.read(2), "big")
            fh.seek(sfnt + 12)
            cmap_off = None
            for _ in range(num_tables):
                rec = fh.read(16)
                if len(rec) < 16:
                    return None
                if rec[:4] == b"cmap":
                    cmap_off = int.from_bytes(rec[8:12], "big")
                    break
            if cmap_off is None:
                return None
            fh.seek(cmap_off + 2)
            n_sub = int.from_bytes(fh.read(2), "big")
            subs = []
            for _ in range(n_sub):
                rec = fh.read(8)
                if len(rec) < 8:
                    break
                subs.append((int.from_bytes(rec[0:2], "big"),
                             int.from_bytes(rec[2:4], "big"),
                             int.from_bytes(rec[4:8], "big")))
            # Unicode サブテーブルを優先（(3,10)=BMP 外も含む → (3,1) → (0,*)）
            prefer = {(3, 10): 0, (3, 1): 1}
            subs.sort(key=lambda s: prefer.get((s[0], s[1]), 2 if s[0] == 0 else 3))
            for _pid, _eid, off in subs:
                res = _cmap_subtable_has(fh, cmap_off + off, chars)
                if res is not None:
                    return res
            return None
    except Exception:
        return None

@lru_cache(maxsize=4)
def _resolve_font(bold: bool) -> Tuple[Optional[str], str]:
    """日本語フォントのパスと入手元（env / platform / scan / none）を返す。

    日本語グリフを持たないフォントは後回しにし、他に候補が無い場合だけ最後の保険として
    使う（例: 日本語フォント未導入の Linux で DejaVu Sans）。
    """
    env = os.environ.get(_FONT_ENV[bool(bold)]) or os.environ.get(_FONT_ENV_COMMON)
    if env and os.path.isfile(env):
        return os.path.abspath(env), "env"           # 明示指定は無条件で尊重する
    fallback: Optional[Tuple[str, str]] = None
    for src, cands in (("platform", _FONT_CANDIDATES[bool(bold)]),
                       ("scan", tuple(_scan_font_dirs(bool(bold))))):
        for p in cands:
            if not p or not os.path.isfile(p):
                continue
            has = _cmap_has_glyphs(p, _CJK_PROBE)
            if has:
                return p, src
            if fallback is None:
                fallback = (p, src)
    return fallback if fallback else (None, "none")


def font_status() -> dict:
    """この環境で解決した日本語フォントの状態を返す（トラブルシュート・検証用）。

    japanese_glyphs が True でない場合、画像内の日本語は豆腐（□）になる。
    環境変数 SPACE_FINDER_FONT / SPACE_FINDER_FONT_BOLD で明示指定できる
    （検証は scripts/check-tools.py --fonts）。
    """
    out = {
        "platform": sys.platform,
        "env": {k: v for k, v in (("SPACE_FINDER_FONT", os.environ.get("SPACE_FINDER_FONT")),
                                  ("SPACE_FINDER_FONT_BOLD",
                                   os.environ.get("SPACE_FINDER_FONT_BOLD"))) if v},
        "regular": None,
        "bold": None,
    }
    for bold in (False, True):
        path, src = _resolve_font(bool(bold))
        out["bold" if bold else "regular"] = {
            "path": path,
            "source": src,
            "japanese_glyphs": _cmap_has_glyphs(path, _CJK_PROBE) if path else False,
        }
    return out

def apply_matplotlib_cjk_font() -> Optional[str]:
    """matplotlib の rcParams に日本語フォントを設定する（Windows / macOS / Linux 共通）。

    sky_overlay / solar_system の accurate 版（matplotlib）が使う。Pillow 側と
    同じ探索結果（_resolve_font）を最優先にし、無ければ matplotlib のフォント一覧から
    日本語ファミリを選ぶ。戻り値は設定したファミリ名（見つからなければ None）。

    matplotlib.pyplot は import しない（rcParams は pyplot 無しでも触れる。stdio 起動後の
    pyplot import はこの環境で HANG するため、呼び出し側が import 済みの rcParams を共有する）。
    """
    from matplotlib import rcParams
    try:
        from matplotlib import font_manager
    except Exception:                               # pragma: no cover - matplotlib 欠如時
        return None

    family: Optional[str] = None
    path, _src = _resolve_font(False)
    if path:
        try:
            font_manager.fontManager.addfont(path)   # matplotlib に登録（重複は無視される）
            family = font_manager.FontProperties(fname=path).get_name()
        except Exception:
            family = None
    if not family:                                   # PIL 側で見つからない環境の保険
        have = {f.name for f in font_manager.fontManager.ttflist}
        family = next((n for n in _MPL_FAMILY_CANDIDATES if n in have), None)

    chain = ([family] if family else []) + [n for n in _MPL_FAMILY_CANDIDATES if n != family]
    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = chain + ["DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    return family


# ---------- 天体・記号の表示色（描画系ツール共通の単一の出典） ----------
# 「実物の見た目に寄せた色」を 1 か所で決める。ここを直せば sky_overlay /
# solar_system の Pillow 版・matplotlib 版・凡例・figure 注記にすべて反映される
# （モジュールごとに色を手書きすると、同じ天王星が図ごとに違う色になる）。
BODY_COLORS = {
    "太陽": (255, 220, 120),
    "月": (224, 224, 226),
    "水星": (168, 168, 170),
    "金星": (242, 226, 180),
    "地球": (110, 150, 235),
    "火星": (214, 96, 77),
    "木星": (212, 168, 118),
    "土星": (226, 196, 146),
    "天王星": (176, 224, 230),   # 淡い青緑（v0.27 で sky_overlay 側の値に統一）
    "海王星": (96, 140, 232),
    "冥王星": (176, 140, 120),
}

# 天体そのものではないが複数ツールで重複していた記号色
SYMBOL_COLORS = {
    "ring": (226, 206, 160),          # 土星の環
    "band": (196, 148, 108),          # 木星の縞
    "cap": (240, 240, 240),           # 火星の極冠
    "asteroid": (96, 200, 120),       # 小惑星マーカー（緑の十字）
    "asteroid_label": (182, 255, 207),
    "comet": (150, 235, 255),         # 彗星（シアンの核）
    "comet_orbit": (255, 150, 60),    # 彗星の軌道面ビューの軌道線
}


def body_rgb(name: str, default: Tuple[int, int, int] = (200, 200, 210)) -> Tuple[int, int, int]:
    """天体名（日本語）→ 表示色RGB。未登録は default。"""
    return BODY_COLORS.get(str(name), default)


def symbol_rgb(key: str, default: Tuple[int, int, int] = (200, 200, 210)) -> Tuple[int, int, int]:
    """記号色（ring/band/cap/asteroid/comet 等）→ RGB。"""
    return SYMBOL_COLORS.get(str(key), default)


def rgb_hex(rgb) -> str:
    """(r, g, b) → #rrggbb（matplotlib 用）。"""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


@lru_cache(maxsize=128)
def load_font(sz: int, bold: bool = False):
    """日本語 TrueType フォントを返す（Windows / macOS / Linux 共通）。

    各ツールが個別実装していたフォント探索の共通版。探索順は `_resolve_font`
    （環境変数 → OS 標準パス → 標準フォントディレクトリ走査）で、日本語グリフの
    有無を cmap で確認してから採用する。サイズ・太さ単位で lru_cache するため、
    同一描画内での再ロードが発生しない。

    どの OS でも日本語フォントが見つからない環境では PIL 既定フォントに落ちる
    （その場合日本語は豆腐になる。状態は `font_status()` で確認できる）。
    """
    from PIL import ImageFont
    path, _src = _resolve_font(bool(bold))
    if path:
        try:
            return ImageFont.truetype(path, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def encode_jpeg(img, max_bytes: int = 3_500_000) -> bytes:
    """PIL 画像を JPEG バイト列にする。max_bytes 超過時は縮小して再エンコードする。"""
    from PIL import Image
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    if len(data) > max_bytes:
        w, h = img.size
        small = img.convert("RGB").resize(
            (max(1, int(w * 0.7)), max(1, int(h * 0.7))), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
    return data


def split_at_antimeridian(points: Iterable[Tuple[float, float]],
                          threshold: float = 150.0) -> List[list]:
    """経度±180°をまたぐ線を分割してセグメントのリストを返す（点列は (lon, lat)）。

    等角図法の地図では ±180° の継ぎ目で線が画面を横切ってしまうため、
    直前点との経度差が threshold を超えた位置で分割する。
    """
    segs: List[list] = []
    seg: list = []
    for p in points:
        if seg and abs(p[0] - seg[-1][0]) > threshold:
            segs.append(seg)
            seg = [p]
        else:
            seg.append(p)
    if seg:
        segs.append(seg)
    return segs


# ---------------------------------------------------------------------------
# 図の注記（figure manifest）
#
# 描画系ツールは「画像バイト列」だけを返すと、ホスト LLM はピクセルから描画規約を
# 推測するしかなく、誤読する（例: 高離心率軌道を「主天体の周りを回る円」と説明する、
# 対数縮尺の円を実軌道と説明する）。そこで各ツールは structuredContent に
# figure ブロック（何をどう描いたかの自己申告）を返し、content には同じ注記を
# そのまま引用できる形で入れる。
#
# 注記は手書きせず、必ず数値から生成する（図と文が食い違わないようにするため）。
# ---------------------------------------------------------------------------

FIGURE_SCHEMA = "figure/1"

# structuredContent.figure 側にだけ書く LLM 向けの指示（content＝人間向け表示には出さない）
FIGURE_NOTES_USAGE = ("figure.notes は図を誤読しないための注記なので、要約・言い換えせず"
                      "そのまま回答に引用すること。")


def _fmt_num(v: Optional[float], digits: int = 2) -> str:
    """注記用の数値表記（大きな値は3桁区切り、小さい値は有効数字を確保）。"""
    if v is None:
        return "-"
    av = abs(v)
    if av >= 1000:
        return "{:,.0f}".format(v)
    if av >= 1:
        return "{:,.{}f}".format(v, digits)
    return "{:.4f}".format(v).rstrip("0").rstrip(".")


@dataclass(frozen=True)
class Conic:
    """軌道の円錐曲線（楕円／放物線／双曲線）。焦点は主天体。

    e>=1（C/彗星など）や a<0（SBDB の双曲線表現）も扱う。
    """

    kind: str                          # "ellipse" / "parabola" / "hyperbola"
    e: float
    a: Optional[float] = None          # 半長軸（双曲線では負値になり得る）
    q: Optional[float] = None          # 近点距離（焦点から）
    b: Optional[float] = None          # 半短軸（楕円のみ）
    c: Optional[float] = None          # 焦点と楕円中心のズレ = a·e（楕円のみ）
    apo: Optional[float] = None        # 遠点距離（楕円のみ）
    period: Optional[float] = None
    incl_deg: Optional[float] = None
    closed: bool = True

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "e": round(self.e, 7), "closed": self.closed}
        for k in ("a", "q", "b", "c", "apo", "period", "incl_deg"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v if abs(v) < 1000 else round(v, 1)
        return d


def conic_from_elements(a: Optional[float] = None, e: Optional[float] = None, *,
                        q: Optional[float] = None, period: Optional[float] = None,
                        incl_deg: Optional[float] = None) -> Conic:
    """軌道要素から Conic を作る。e と a の符号から楕円／放物線／双曲線を判定する。"""
    e = float(e)
    a = None if a is None else float(a)
    if q is None and a is not None and e < 1.0:
        q = a * (1.0 - e)
    if e < 1.0 - 1e-9:
        aa = None if a is None else abs(a)
        return Conic("ellipse", e, a, q,
                     None if aa is None else aa * math.sqrt(max(0.0, 1.0 - e * e)),
                     None if aa is None else aa * e,
                     None if aa is None else aa * (1.0 + e),
                     period, incl_deg, True)
    if abs(e - 1.0) <= 1e-9:
        return Conic("parabola", e, a, q, None, None, None, period, incl_deg, False)
    return Conic("hyperbola", e, a, q, None,
                 None if a is None else abs(a) * e, None, period, incl_deg, False)


def figure_notes(conic: Optional[Conic] = None, *, primary: str = "", unit: str = "km",
                 periapsis_label: str = "近点", apoapsis_label: str = "遠点",
                 extra: Iterable[str] = ()) -> List[str]:
    """図の注記を数値から生成する（手書きしない＝図と文がドリフトしない）。"""
    notes: List[str] = []
    if conic is not None:
        if conic.kind == "ellipse":
            notes.append(
                "主天体（{}）は楕円の焦点に置いている。楕円の中心は {} から a·e＝{} {} "
                "ずれている（{} {} {} ／ {} {} {}）".format(
                    primary, primary, _fmt_num(conic.c), unit,
                    periapsis_label, _fmt_num(conic.q), unit,
                    apoapsis_label, _fmt_num(conic.apo), unit))
            notes.append(
                "離心率 e={:.6f}。軌道は閉じている（{}は存在する）".format(conic.e, apoapsis_label))
        elif conic.kind == "hyperbola":
            notes.append(
                "この軌道は閉じていない（e={:.7f} > 1、半長軸 a が負＝双曲線）。"
                "{}は存在しないので、楕円として a(1+e) を計算すると負値になる".format(
                    conic.e, apoapsis_label))
            notes.append(
                "図は{}から有限距離までを描いた双曲線の枝。実際の軌道はさらに遠方へ開いていく".format(
                    periapsis_label))
        else:
            notes.append(
                "放物線に近い軌道（e={:.7f}）。閉じた楕円として描いてはいけない".format(conic.e))
        if conic.incl_deg is not None:
            retro = conic.incl_deg > 90.0
            notes.append(
                "黄道面基準の軌道傾斜角 {:.2f}°{}。この図は彗星自身の軌道面を真横から見た模式図で、"
                "傾斜は別の見方になる".format(conic.incl_deg, "（逆行軌道）" if retro else ""))
    notes.extend(n for n in extra if n)
    return notes


def view_spec(frame: str, projection: str, description: str,
              why: Optional[str] = None) -> dict:
    """図の視点（どの面を・どう見たか）。"""
    v = {"frame": frame, "projection": projection, "description": description}
    if why:
        v["why"] = why
    return v


def primary_spec(name: str, at: str = "focus", *, center_offset: Optional[float] = None,
                 unit: str = "km", note: Optional[str] = None) -> dict:
    """主天体の置き方（焦点か中心か）。"""
    p = {"name": name, "at": at}
    if center_offset is not None:
        p["center_offset_" + unit] = center_offset
    if note:
        p["note"] = note
    return p


def scale_spec(type_: str, *, to_scale: bool = True,
               px_per_unit: Optional[float] = None, unit: str = "km",
               exaggerated: Iterable[str] = ()) -> dict:
    """縮尺の種類と、実寸でない要素の明示。"""
    s = {"type": type_, "to_scale": bool(to_scale)}
    if px_per_unit:
        s["px_per_" + unit] = px_per_unit
    ex = list(exaggerated)
    if ex:
        s["exaggerated"] = ex
    return s


def figure_payload(*, kind: str, title: str, view: Optional[dict] = None,
                   primary: Optional[dict] = None, scale: Optional[dict] = None,
                   conic: Optional[Conic] = None, markers: Optional[Iterable[dict]] = None,
                   notes: Iterable[str] = (), caption: Optional[str] = None,
                   verify: Optional[dict] = None) -> dict:
    """structuredContent に入れる figure ブロックを組み立てる。"""
    fig: dict = {"schema": FIGURE_SCHEMA, "kind": kind, "title": title}
    if view:
        fig["view"] = view
    if primary:
        fig["primary"] = primary
    if scale:
        fig["scale"] = scale
    if conic is not None:
        fig["conic"] = conic.to_dict()
    mk = list(markers or ())
    if mk:
        fig["markers"] = mk
    fig["notes"] = [n for n in notes if n]
    # 注記の扱い（LLM 向けの指示。content＝人間向け表示には出さない）
    fig["notes_usage"] = FIGURE_NOTES_USAGE
    if caption:
        fig["caption"] = caption
    if verify:
        fig["verify"] = verify
    return fig


def figure_text_block(figure: dict) -> str:
    """content 用の注記ブロック（**人間が読む表示**。指示文は入れない）。

    注記を要約せず引用すべきことはホスト LLM への指示であって、content は人間が
    読むチャネルなので、ここには書かない（表示に指示文が混ざると読み手に
    意味不明な文が出る）。指示は各ツールの docstring と
    `structuredContent.figure.notes_usage`（LLM が読む JSON 側）に置く。
    """
    lines = ["### ⚠️ 図の注記"]
    lines += ["- " + t for t in figure.get("notes", [])]
    if figure.get("caption"):
        lines.append("**図の説明**: " + figure["caption"])
    return "\n".join(lines)


def verify_curve(image, *, color: Tuple[int, int, int], focus_xy: Tuple[float, float],
                 px_per_unit: float, periapsis: float, apoapsis: Optional[float] = None,
                 tol_ratio: float = 0.05, tol_px: float = 4.0,
                 label_boxes: Iterable[Tuple] = (),
                 tol_color: int = 60, axis: str = "x",
                 occluders: Iterable[Tuple[float, float, float]] = (),
                 min_feature_px: float = 3.0) -> dict:
    """描いた曲線の画素から近点／遠点距離を逆算し、幾何が数値どおりか検証する。

    高離心率の楕円は「主天体を中心に描いてしまう」誤りが起きやすいため、
    主天体＝焦点からの距離を画素から測って a(1-e) / a(1+e) と突き合わせる。
    併せて、ラベル矩形に曲線色が混入していないこと（文字と線の重なり）も検査する。

    超長距離の楕円では近日点が画面で数 px 以下になり、しかも**曲線の上に描いた
    主天体の円盤が近点付近を上書きする**ため、「曲線色の右端画素」は近点ではなく
    円盤の縁になる（測っても無意味）。そこで近点が分解できない場合は等値検査を
    やめ、上界検査だけを行う（可視の曲線が焦点から 円盤半径+tol_px を超えて
    近点側へ伸びていないこと）。主天体を楕円の中心に置く誤りはこの上界だけで
    検出でき、遠点側の等値検査も併せて効く。判定は `periapsis_resolvable` と
    `periapsis_check`（equality / upper_bound）に残すので、呼び出し側は
    「この縮尺では図から確認できない」旨を注記に**数値から生成**して明示すること。

    Args:
        occluders: 曲線の上に描いた円盤 [(中心x, 中心y, 半径px), ...]（主天体の円盤など）。
        min_feature_px: これ未満の大きさの特徴は画素から確認できないとみなす下限（px）。
    """
    from PIL import Image
    img = image if hasattr(image, "size") else Image.open(image)
    px = img.convert("RGB").load()
    w, h = img.size
    hits: List[Tuple[int, int]] = []
    step = 1 if max(w, h) <= 1200 else 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if abs(r - color[0]) + abs(g - color[1]) + abs(b - color[2]) < tol_color:
                hits.append((x, y))
    if not hits:
        return {"ok": False, "reason": "曲線色の画素が見つかりません"}
    fx, fy = focus_xy
    if axis == "x":
        near = max(hits, key=lambda p: p[0])
        far = min(hits, key=lambda p: p[0])
    else:
        near = max(hits, key=lambda p: p[1])
        far = min(hits, key=lambda p: p[1])
    d_near = math.hypot(near[0] - fx, near[1] - fy) / px_per_unit
    d_far = math.hypot(far[0] - fx, far[1] - fy) / px_per_unit
    # 画素の分解能ぶんの許容（近点は画面上で短いため、比だけで判定すると誤検知する）
    def _near(a_, b_):
        return abs(a_ - b_) <= max(tol_ratio * abs(b_), tol_px / max(px_per_unit, 1e-12))

    def _rd(v):
        """量に応じた丸め（km は整数、AU は小数を残す）。"""
        return int(round(v)) if abs(v) >= 100 else round(v, 4)

    res = {"periapsis_measured": _rd(d_near), "periapsis_expected": _rd(periapsis),
           "periapsis_error_pct": round(abs(d_near - periapsis) / max(1e-9, periapsis) * 100, 2),
           "resolution_per_px": round(1.0 / max(px_per_unit, 1e-12), 6)}
    # 近点が「主天体の円盤の内側」か「画素の下限未満」なら、等値検査はできない
    occ_r = max([float(r_) for (_, _, r_) in occluders] or [0.0])
    peri_px = abs(float(periapsis)) * max(px_per_unit, 1e-12)
    resolvable = peri_px >= max(float(min_feature_px), occ_r + 2.0)
    if resolvable:
        res["periapsis_resolvable"] = True
        res["periapsis_check"] = "equality"
        ok = _near(d_near, periapsis)
    else:
        limit = (occ_r + tol_px) / max(px_per_unit, 1e-12)
        res["periapsis_resolvable"] = False
        res["periapsis_check"] = "upper_bound"
        res["periapsis_occluder_px"] = round(occ_r, 1)
        res["periapsis_upper_bound"] = _rd(limit)
        res["periapsis_unresolved_reason"] = (
            "近点は画面上 {:.2f} px（描いた円盤の半径 {:.1f} px／分解能下限 {} px）で、"
            "画素からは確認できない".format(peri_px, occ_r, min_feature_px))
        ok = d_near <= limit          # 円盤半径を超えて近点側へ伸びていないこと
    if apoapsis:
        res["apoapsis_measured"] = _rd(d_far)
        res["apoapsis_expected"] = _rd(apoapsis)
        res["apoapsis_error_pct"] = round(abs(d_far - apoapsis) / max(1e-9, apoapsis) * 100, 2)
        ok = ok and _near(d_far, apoapsis)
    overlap = 0
    for (x0, y0, x1, y1) in label_boxes:
        # 画面外を通る矩形でも例外を出さない（両端をクランプする。片側だけだと
        # 負の y で IndexError になり、描画中のツール呼び出しが丸ごと失敗する）
        xa, xb = max(0, int(x0)), min(w, int(x1))
        ya, yb = max(0, int(y0)), min(h, int(y1))
        for y in range(ya, yb):
            for x in range(xa, xb):
                r, g, b = px[x, y]
                if abs(r - color[0]) + abs(g - color[1]) + abs(b - color[2]) < tol_color:
                    overlap += 1
    res["label_overlap_px"] = overlap
    res["ok"] = bool(ok and overlap == 0)
    return res


def as_image(image):
    """PIL画像 / パス / bytes のいずれでも PIL 画像にする。"""
    from PIL import Image
    if hasattr(image, "size"):
        return image
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image)))
    return Image.open(image)


def pixel_near(image, xy: Tuple[float, float], color: Tuple[int, int, int],
               tol: int = 90, r: int = 3) -> int:
    """指定画素の近傍(±r)に指定色が何画素あるかを返す（マーカー描画の確認用）。"""
    import numpy as np
    img = as_image(image).convert("RGB")
    a = np.asarray(img, dtype=np.int16)
    x, y = int(xy[0]), int(xy[1])
    x0, x1 = max(0, x - r), min(img.size[0], x + r + 1)
    y0, y1 = max(0, y - r), min(img.size[1], y + r + 1)
    if x0 >= x1 or y0 >= y1:
        return 0
    d = (abs(a[y0:y1, x0:x1, 0] - color[0]) + abs(a[y0:y1, x0:x1, 1] - color[1])
         + abs(a[y0:y1, x0:x1, 2] - color[2]))
    return int((d < tol).sum())


# ---------- 生成物の保存と「メディアより前のリンク」 ----------
# Hermes 等のリッチなクライアントは ImageContent をそのまま描画できるが、CLI 系・
# Android 系のハーネス（codex / opencode など）は画像ブロックを無視するため、
# 「画像が生成されたのに何も表示されない」ように見える。そこで content の
# 先頭側に、アイコン付きのクリック可能なリンク（URL、無ければ保存したファイル）を
# 必ず置く。順序は「メディア本体より前」を守る。
OUTPUT_DIR = os.path.join(os.path.dirname(CACHE_ROOT), "out")

_MEDIA_ICONS = {"image": "🖼️", "figure": "🖼️", "audio": "🎧", "video": "🎬", "file": "📄"}

# リンクの文言（動詞）は kind から機械的に決める。ツールごとに手書きすると
# 「画像を開く: 」「サムネイル画像を開く: 」「動画を再生: 」のようにばらついて
# 見た目が揃わないため、呼び出し側は「対象」だけを渡す（統一はここで担保する）。
_MEDIA_VERBS = {
    "image": "画像を開く",
    "figure": "生成した画像を開く",
    "audio": "音声を開く",
    "video": "動画を開く",
    "file": "ファイルを開く",
}

# 旧来の呼び出し（動詞込みのラベル）が混ざっても「画像を開く: 画像を開く: …」に
# ならないよう、先頭の動詞だけ落として対象名に正規化する。
_MEDIA_VERB_PREFIX_RE = re.compile(
    r"^(?:生成した画像|サムネイル画像|ポスター画像|観測プレビュー画像|画像|音声|動画|ファイル|文書)"
    r"を(?:開く|再生)\s*[:：]?\s*")

# 「アイコン + markdownリンク」の行かどうか（先頭の空白＝リスト内の継続行は許容）。
# ラベル（[] の中身）は見た目の統一検査（canonical_media_label）で使うため捕捉する。
_MEDIA_LINK_LINE_RE = re.compile(
    r"^(?:🖼|🎧|🎬|📄)\uFE0F?\s*\[([^\]]+)\]\((?:https?://|file://)")


def file_uri(path: str) -> str:
    """ローカルパスを file:// URI に変換する（Windows の C:\\... も可）。"""
    p = os.path.abspath(str(path)).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + urllib.parse.quote(p, safe="/:")


def save_output(data: bytes, tool: str, ext: str = "png", keep: int = 200) -> Optional[str]:
    """生成した画像をディスクに保存して絶対パスを返す（失敗時 None）。

    インライン表示できないハーネスでもユーザーが開けるようにするための出力。
    keep 件を超えた古いファイルは削除する（無制限に溜めない）。
    """
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "{}_{}_{}.{}".format(tool, stamp, uuid.uuid4().hex[:6], ext.lstrip("."))
        path = os.path.join(OUTPUT_DIR, name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)            # 途中読み込みを避けるため置換で確定
        try:
            files = sorted((os.path.join(OUTPUT_DIR, n) for n in os.listdir(OUTPUT_DIR)),
                           key=os.path.getmtime, reverse=True)
            for old in files[keep:]:
                os.remove(old)
        except OSError:
            pass
        return path
    except OSError:
        return None


def markdown_link_url(url: str) -> str:
    """URL を markdown リンク内で安全に使える形にする。

    生の空白・括弧はリンクの終端と紛らわしく、リンクが壊れて切れる（NASA の
    アセットURLには空白入り動画名がある）。既存の %XX は壊さないよう、
    問題になる文字だけをパーセントエンコードする。
    """
    out = []
    for ch in str(url or ""):
        if ch in " <>\"`()":
            out.append("%{:02X}".format(ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def media_link_line(subject: str = "", *, url: Optional[str] = None, path: Optional[str] = None,
                    kind: str = "image", note: Optional[str] = None) -> str:
    """メディア本体より前に置く「アイコン付きリンク行」を作る（形式はここで統一）。

    返すのは `🖼️ [画像を開く: 対象](URL)` の1行。動詞は kind から決まる
    （image→画像を開く / figure→生成した画像を開く / audio→音声を開く /
    video→動画を開く / file→ファイルを開く）ので、呼び出し側は**対象名だけ**を渡す。
    旧来の「◯◯を開く: …」を渡しても先頭の動詞は落として二重にしない。

    url があればそれを、無ければ保存した path を file:// URI にしてリンクにする。
    リンク先を作れないときは空文字を返す（呼び出し側で行ごと落とせる）。
    """
    icon = _MEDIA_ICONS.get(kind, "🔗")
    verb = _MEDIA_VERBS.get(kind, "開く")
    target = markdown_link_url(url) if url else (file_uri(path) if path else "")
    if not target:
        return ""
    subject_text = _MEDIA_VERB_PREFIX_RE.sub("", str(subject or "")).strip()
    # 旧式の「◯◯を開く（対象）」は動詞を落とすと丸括弧だけが残るので外す。
    if (subject_text.startswith("（") and subject_text.endswith("）")
            and subject_text.count("（") == 1):
        subject_text = subject_text[1:-1].strip()
    label = "{}: {}".format(verb, subject_text) if subject_text else verb
    line = "{} [{}]({})".format(icon, label, target)
    if path:
        line += " ｜ 保存先: `{}`".format(path)
    if note:
        line += " ｜ {}".format(note)
    return line


def media_link_label(line: str) -> Optional[str]:
    """その行が「アイコン付きリンク行」ならラベル（[] の中身）を返す。違えば None。"""
    m = _MEDIA_LINK_LINE_RE.match(str(line or "").lstrip())
    return m.group(1) if m else None


def is_media_link_line(line: str) -> bool:
    """その行が「アイコン付きリンク行」か（先頭の空白＝リスト内の継続行は無視）。"""
    return media_link_label(line) is not None


def canonical_media_label(label: str) -> bool:
    """ラベルが統一形式（`<動詞>` または `<動詞>: 対象`）かどうか。

    `<動詞>` は kind から決まる `_MEDIA_VERBS` の5種のみ。ゲート
    （`scripts/check-tools.py --media-links`）がこれを使って見た目の統一を検査する。
    """
    text = str(label or "")
    return any(text == v or text.startswith(v + ": ") for v in _MEDIA_VERBS.values())


def layout_media_links(text: str) -> str:
    """メディアのリンク行を独立した段落にする（前後に空行を入れる）。

    Markdown の単一改行は「ソフト改行」で、描画時に同一段落へ畳み込まれる。そのため
    画像を複数返すとリンク行どうしや直前の caption と融合して1行になる（クライアント
    描画で実測）。ここで空行を補って段落を分ける。行頭の空白（番号付きリストの継続行）
    はそのまま残すので、リスト内のリンクはその項目に属したままになる。冪等。
    """
    lines = str(text or "").split("\n")
    out: List[str] = []
    for i, ln in enumerate(lines):
        if not is_media_link_line(ln):
            out.append(ln)
            continue
        if out and out[-1].strip():
            out.append("")                      # 直前の行と融合させない
        out.append(ln)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.strip():
            out.append("")                      # 次の行（リンクでも本文でも）と融合させない
    return "\n".join(out)

"""宇宙・天文イベントカレンダー（月グリッド図）＋ユーザー予定の読み書き。

1か月を格子で描き、そこに次の4層を重ねる（すべて認証不要）。

| 層 | データ源 | 取り方 |
|---|---|---|
| 打ち上げ | Launch Library 2 | **API**（月範囲クエリ・1ヶ月=1リクエスト）。`net_precision` で日付精度を判定し、日付が確定した行だけを日付セルに置く |
| 天文現象 | JPL DE421 + Skyfield | **ローカル計算**（月相・二十四節気・惑星の衝/合/内合/最大離角・日食月食・流星群） |
| 公開・イベント | 国立天文台（NINS のイベント一覧） | HTML（構造化された `news-item` ブロック。開催日の範囲が取れる） |
| JAXA 施設公開 | ファン!ファン!JAXA!（施設見学）＋ 宇宙科学研究所のイベント表 | HTML（`<li>` / `<tr>` 単位の小さな正規表現）。**告知済みのみ**のスナップショット（アーカイブ無し）なので、取得日を窓にして毎日取り直す |
| ユーザー予定 | ローカルの蓄積ストア | `calendar_event_add` / `remove` / `events` |

蓄積（`calendar_store.py`）を一次ソースにした **read-through**: 要求月 M に対して窓 [M-1, M+2] を確保し、
未取得・期限切れの月だけ取りに行く（月が進むと差分は1ヶ月になる）。計算値は決定的なので月をキーに
永久キャッシュする（Skyfield の計算が1回の描画で最も高コスト＝実測 4.5 秒/月）。

打ち上げの照合: NASA の WordPress REST（`event-type=12929`）を API で引き、LL2 の行と突き合わせて
`corroborated` と公式ページURLを付ける（nasa.gov は別ホストなので NASA_API_KEY の予算を消費しない）。

図は `structuredContent.figure`（schema: figure/1）を返し、`figure.notes` は**要約せず引用**してください。
出典: Launch Library 2 (thespacedevs.com) ／ JPL DE421 + Skyfield ／ 国立天文台 イベント情報 ／
JAXA（ファン!ファン!JAXA!・宇宙科学研究所） ／ nasa.gov。
"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import html
import re
import time
from typing import Optional
from urllib.parse import urljoin

import requests
from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image, ImageDraw

from . import calendar_store as store
from .img_common import (figure_notes, figure_payload, figure_text_block, load_font,
                         media_link_line, save_output)
from .input_utils import as_int
from .launch import blocked_left, blocked_message, note_429, reset_blocked
from .solar_eclipse import _load, _local_tz, _resolve_place, _tz_label

LL2 = "https://ll.thespacedevs.com/2.3.0"
NASA_EVENT_API = "https://www.nasa.gov/wp-json/wp/v2/event"
NASA_LAUNCH_TERM = 12929          # "Launch Schedule" ターム（実測: 69件）
NINS_EVENTS = "https://www.nins.jp/event/cat72/"
# JAXA の施設公開。fanfun の一覧は「近日開催」だけ（過去分のアーカイブ・ページングは無い。
# ?page=2 も同一内容を返す実測）、ISAS のイベント表は月ごとの表で過去1年ぶんが残る。
FANFUN_VISIT = "https://fanfun.jaxa.jp/visit/"
ISAS_EVENTS = "https://www.isas.jaxa.jp/outreach/events/"
# 施設名ラベル（fanfun の <span data-icn-color="03">）。これだけが付いた行が施設の公開イベントで、
# 「お知らせ」「休館案内」が付く行はイベントではない（実測: 臨時休館・見学中止の告知）。
JAXA_FACILITIES = ("種子島", "内之浦", "筑波", "調布", "相模原", "地球観測", "角田",
                   "勝浦", "増田", "沖縄", "臼田", "大樹", "能代")
_JAXA_HINT = re.compile(r"公開|施設紹介")
_JAXA_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
UA = {"User-Agent": "curl/8.5.0 (compatible; space-finder-mcp/0.31; "
                    "+https://github.com/loosephoto/space-finder-mcp)"}

# 格子の寸法（描画と自己検証が同じ値を使う＝検証が別レイアウトを測らない）
CELL_W, CELL_H, HDR, WD, FOOT = 320, 190, 190, 46, 116
_WEEKDAYS = ("日", "月", "火", "水", "木", "金", "土")
_BG, _FG, _DIM = (8, 10, 22), (232, 236, 244), (150, 158, 175)
_LIT, _DARK, _GRID = (232, 236, 248), (26, 30, 46), (44, 50, 68)
_PHASE_ILLUM = {"新月": 0.0, "上弦": 0.5, "満月": 1.0, "下弦": 0.5}
_SOLID_PRECISION = ("Day", "Hour", "Minute", "Second")

# 流星群（静的表・極大日は年により変動するため「目安」として返す）
METEOR_SHOWERS = [
    (1, 4, "しぶんぎ座流星群", "ZHR≈80・三大流星群"),
    (4, 22, "こと座流星群", "ZHR≈18"),
    (5, 6, "みずがめ座η流星群", "ZHR≈50（母天体 ハレー彗星）"),
    (7, 30, "みずがめ座δ流星群", "ZHR≈25"),
    (8, 13, "ペルセウス座流星群", "ZHR≈100・三大流星群"),
    (10, 8, "ジャコビニ・ツィナー（10月りゅう座）流星群", "ZHR≈10"),
    (10, 21, "オリオン座流星群", "ZHR≈20（母天体 ハレー彗星）"),
    (11, 5, "おうし座南流星群", "ZHR≈5（火球が多い）"),
    (12, 2, "ほうおう座流星群", "ZHR≈変動"),
    (12, 14, "ふたご座流星群", "ZHR≈150・三大流星群"),
]

def _ts_utc(ts, d: dt.datetime):
    """tz naive な UTC datetime → Skyfield Time（この版の ts.utc は tz 必須）。"""
    from skyfield.api import utc
    return ts.from_datetime(d.replace(tzinfo=utc))


def _iso_z(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso_z(s: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


# ── 取得層（すべて例外を外へ出さず、戻り値は (records, error)） ──────────────
def _month_span(year: int, month: int):
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
    return first, nxt


def _ll2_fetch(first: dt.date, nxt: dt.date):
    """LL2 の打ち上げを [first, nxt) の範囲で取る（最大2ページ）。戻り: (rows, error)。

    429 は**待たずに** fail fast する（実測 Retry-After は 104 秒あり、ツール呼び出しをその間
    ブロックするのは損。遮断は記憶して以降は HTTP を出さない）。
    """
    if blocked_left() > 0:                       # 遮断中は送信しない（枠を無駄にしない）
        return [], blocked_message() + "。蓄積済みの打ち上げで描画します"
    params = {"window_start__gte": first.isoformat(), "window_start__lte": nxt.isoformat(),
              "limit": 100, "ordering": "window_start", "mode": "normal"}
    rows, pages = [], 0
    while pages < 2:
        try:
            r = requests.get("{}/launches/".format(LL2), params=params, timeout=(10, 40))
        except requests.RequestException as ex:
            return [], "Launch Library 2 への接続に失敗しました: {}".format(ex)
        if r.status_code == 429:
            note_429(r.headers.get("Retry-After"))
            return [], "Launch Library 2 が 429（Retry-After={}）".format(r.headers.get("Retry-After"))
        if r.status_code != 200:
            return [], "Launch Library 2 が HTTP {} を返しました".format(r.status_code)
        try:
            body = r.json()
        except ValueError as ex:
            return [], "Launch Library 2 の応答を解釈できません: {}".format(ex)
        reset_blocked()                          # 成功したら遮断の記憶を解除する
        rows += [x for x in (body.get("results") or []) if isinstance(x, dict)]
        pages += 1
        if not body.get("next"):
            break
        params = dict(params, offset=pages * 100)
    return rows, None


def _ll2_records(rows: list) -> list:
    """LL2 の行を正規化レコードにする（日付精度は net_precision で判定）。

    日付が確定した行だけを日付セルに置く。Day/Hour/Minute/Second 以外（Month/Quarter/Year/Half）は
    「日付未定」の投入りへ回す（実測: 3ヶ月窓の 122 件中、日付精度があるのは 4 件だけだった）。
    """
    out = []
    for l in rows:
        precision = ""
        if isinstance(l.get("net_precision"), dict):
            precision = str(l["net_precision"].get("name") or "")
        start = l.get("window_start")
        pad = l.get("pad") if isinstance(l.get("pad"), dict) else {}
        loc = pad.get("location") if isinstance(pad.get("location"), dict) else {}
        site = str(loc.get("name") or pad.get("name") or "?")
        solid = (precision in _SOLID_PRECISION) and bool(start) and not store.is_year_end_placeholder(start)
        raw = str(l.get("name") or "?")
        mission = raw.split(" | ", 1)[1] if " | " in raw else raw
        rk = l.get("rocket") if isinstance(l.get("rocket"), dict) else {}
        rocket = (rk.get("configuration") or {}).get("name") if isinstance(rk.get("configuration"), dict) else None
        st = l.get("status") if isinstance(l.get("status"), dict) else {}
        status = st.get("name") or ("To Be Determined" if st.get("abbrev") == "TBD" else "?")
        out.append({
            "key": "ll2:{}".format(l.get("id")), "kind": "launch", "title": mission[:60],
            "detail": "{} ／ {} [{}]".format(rocket or "機体不明", site, status),
            "start_utc": str(start)[:19] + "Z" if start else None, "solid": solid,
            "precision": precision or "Unknown", "venue": site, "source": "Launch Library 2",
            "url": "https://ll.thespacedevs.com/2.3.0/launches/{}/".format(l.get("id")),
            "certainty": "confirmed" if solid else "date-unknown",
        })
    return out


def _ll2_month(year: int, month: int):
    """1ヶ月分の打ち上げ（API・1リクエスト）。"""
    first, nxt = _month_span(year, month)
    rows, err = _ll2_fetch(first, nxt)
    if err:
        return [], err
    return _ll2_records(rows), None


def _norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(s).lower())


def _nasa_launch_list():
    """NASA 公式の打ち上げリスト（WP REST・API・認証不要・NASA_API_KEY の予算を消費しない）。

    日付は REST には露出していない（`/wp-json/` の全ルートを走査して確認）ので、
    **照合と公式リンクの供給**にだけ使う。
    """
    cached = store.kv_get("nasa_launch_list", store.TTL_NASA_LIST)
    if cached is not None:
        return cached, None
    try:
        r = requests.get(NASA_EVENT_API, params={"event-type": NASA_LAUNCH_TERM, "per_page": 100,
                                                "_fields": "id,slug,link,title"},
                         timeout=(10, 30), headers=UA)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError) as ex:
        return store.kv_get("nasa_launch_list", None, allow_stale=True) or [], \
            "NASA の打ち上げリストを取得できませんでした: {}".format(ex)
    items = [{"title": (x.get("title") or {}).get("rendered") or "", "slug": x.get("slug") or "",
              "link": x.get("link") or ""} for x in rows if isinstance(x, dict)]
    store.kv_put("nasa_launch_list", items)
    return items, None


def _corroboration_map(launch_records: list, nasa_items: list) -> dict:
    """LL2 の打ち上げ行を NASA 公式リストと突き合わせた対応表を作る。

    レコード自体は書き換えない（読み出し時に被せる）。NASA 側は日付を持たないので
    **照合と公式ページURLの供給**だけに使う＝LL2 の行に権威付けを足す。
    """
    mapping = {}
    for rec in launch_records:
        mt = _norm_text(rec.get("title") or "")
        if len(mt) < 4:
            continue
        for item in nasa_items:
            ns = _norm_text(item.get("slug") or "")
            nt = _norm_text(item.get("title") or "")
            if mt and (mt in ns or (ns and ns.endswith(mt)) or mt == nt):
                mapping[str(rec.get("key"))] = {"nasa_url": item.get("link") or "",
                                                "nasa_title": item.get("title") or ""}
                break
    return mapping


def _shift(year: int, month: int, delta: int):
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _computed_month(year: int, month: int, off: float) -> None:
    """その月の天文現象をローカル計算して蓄積する（決定的なので期限なしでキャッシュ）。"""
    src = "computed:{:04d}-{:02d}".format(year, month)
    if store.source_is_fresh(src, store.TTL_NEVER, window="v1"):
        return
    loader, eph = _load()
    ts = loader.timescale()
    first, nxt = _month_span(year, month)
    wt0 = _ts_utc(ts, dt.datetime.combine(first, dt.time(0)) - dt.timedelta(hours=off + 24))
    wt1 = _ts_utc(ts, dt.datetime.combine(nxt, dt.time(0)) - dt.timedelta(hours=off - 24))
    recs = []

    def add(t, kind, title, detail, cert, tag):
        utc = t.utc_datetime()
        recs.append({
            "key": "computed:{}:{}".format(tag, utc.strftime("%Y%m%dT%H%M%S")),
            "kind": kind, "title": title, "detail": detail,
            "start_utc": _iso_z(utc), "solid": True, "source": "JPL DE421 + Skyfield",
            "url": "", "certainty": cert,
        })

    for t, nm in _phases(eph, ts, wt0, wt1):
        add(t, "sky", nm, "月相（ローカル計算）", "computed", "phase")
    for t, nm in _solar_terms(eph, ts, wt0, wt1):
        add(t, "holiday", nm, "二十四節気（太陽黄経15°毎）", "computed", "sekki")
    for t, title, detail in _planet_events(eph, ts, wt0, wt1):
        add(t, "sky", title, detail, "computed", "planet")
    for t, title, detail in _eclipses(eph, ts, wt0, wt1):
        add(t, "sky", title, detail, "computed", "eclipse")
    for m, day, nm, note in METEOR_SHOWERS:
        if m == month:
            add(_ts_utc(ts, dt.datetime(year, m, day, 15) - dt.timedelta(hours=off)),
                "sky", "{} 極大".format(nm), note + "／極大日は年により変動（目安）", "approx", "meteor")
    store.upsert(recs, src, window="v1", complete=True, ttl=store.TTL_NEVER)


def _ensure_coverage(year: int, month: int, off: float) -> list:
    """窓 [M-1, M+2] を read-through で確保する（未取得・期限切れの月だけ取りに行く）。

    戻り: 失敗したソースの説明リスト（警告として content / structuredContent に出す）。
    """
    warnings = []
    missing = []
    for i in (-1, 0, 1, 2):
        y, m = _shift(year, month, i)
        src = "ll2:{:04d}-{:02d}".format(y, m)
        ttl = store.TTL_LAUNCH_CURRENT if i == 0 else store.TTL_LAUNCH_FUTURE
        if not store.source_is_fresh(src, ttl, window=src):
            missing.append((y, m, i, src, ttl))
    if len(missing) >= 2:
        # 複数月が欠けているときは1クエリでまとめて取る（実測: 3ヶ月ぶん = 2リクエスト）。
        # 月が進んで差分が1ヶ月になったら下の単月経路（1リクエスト）になる。
        y0, m0 = missing[0][0], missing[0][1]
        y1, m1 = missing[-1][0], missing[-1][1]
        rows, err = _ll2_fetch(_month_span(y0, m0)[0], _month_span(y1, m1)[1])
        if err:
            warnings.append("打ち上げ（{}年{}月〜{}年{}月）: {}".format(y0, m0, y1, m1, err))
        else:
            buckets = {}
            for rec in _ll2_records(rows):
                buckets.setdefault((rec.get("start_utc") or "")[:7], []).append(rec)
            for (y, m, _i, src, ttl) in missing:
                store.upsert(buckets.pop("{:04d}-{:02d}".format(y, m), []), src,
                             window=src, complete=True, ttl=ttl)
            rest = [r for v in buckets.values() for r in v]
            if rest:                      # 窓の境界で漏れた行は最初の欠落月に入れて失わない
                _y, _m, _i, src, ttl = missing[0]
                store.upsert(rest, src, window=src, complete=True, ttl=ttl)
    else:
        for (y, m, _i, src, ttl) in missing:
            recs, err = _ll2_month(y, m)
            if err:
                warnings.append("打ち上げ（{}年{}月）: {}".format(y, m, err))
                continue
            store.upsert(recs, src, window=src, complete=True, ttl=ttl)
    pub, err = _public_events(year, month)
    if err:
        warnings.append("公開・イベント: {}".format(err))
    else:
        store.upsert(pub, "public:{}".format(year), window=str(year), complete=True,
                     ttl=store.TTL_PUBLIC)
    jaxa_err = _jaxa_public_events()
    if jaxa_err:
        warnings.append("JAXA 施設公開: {}".format(jaxa_err))
    _computed_month(year, month, off)
    nasa_items = store.kv_get("corroboration_map_v1", store.TTL_NASA_LIST)
    if nasa_items is None:
        items, err = _nasa_launch_list()
        if err:
            warnings.append("NASA 照合: {}".format(err))
        else:
            mapping = _corroboration_map(store.records(kinds={"launch"}), items)
            mapping["_source_count"] = {"nasa_items": len(items)}
            store.kv_put("corroboration_map_v1", mapping)
    return warnings


def _assemble(year: int, month: int, off: float, kinds, corr: dict):
    """その月（観測地のローカル月）のイベントを蓄積ストアから組み立てる。

    - start_local（予定・公開イベント）はフローティングなローカルとしてそのまま使う
      （place を変えても自分の予定が動かない）。start_utc は観測地の現地時刻へ変換する。
    - 日付が確定していない打ち上げ（solid=False）は日付セルに置かず tray に回す
      （年末のプレースホルダを日付セルに描くと架空の予定になる: LL2 実測 88 件）。
    """
    first, nxt = _month_span(year, month)
    want = None if not kinds or "all" in kinds else set(kinds)
    drawn, tray = [], []
    for rec in store.records():
        kind = rec.get("kind")
        if want is not None and kind not in want:
            continue
        cases = []
        if kind == "user":
            for s, e in store.expand_user_occurrences(rec, year, month):
                # all_day は保存値を使う（ここで True 固定にすると時刻付きの予定が「終日」になる）
                cases.append((s, e, bool(rec.get("all_day"))))
        else:
            start = rec.get("start_local")
            if start:
                try:
                    s = dt.datetime.strptime(str(start)[:16], "%Y-%m-%dT%H:%M")
                except ValueError:
                    continue
                e = None
                if rec.get("end_local"):
                    try:
                        e = dt.datetime.combine(dt.date.fromisoformat(str(rec["end_local"])[:10]), s.time())
                    except ValueError:
                        e = None
                cases.append((s, e, bool(rec.get("all_day"))))
            elif rec.get("start_utc"):
                s = _from_iso_z(str(rec["start_utc"]))
                if s is None:
                    continue
                cases.append((s + dt.timedelta(hours=off), None, False))
        for s, e, all_day in cases:
            if not (first <= s.date() < nxt):
                continue
            item = {"dt": s, "date": s.date(), "end": e.date() if e else None, "all_day": all_day,
                    "kind": kind, "title": rec.get("title") or "", "detail": rec.get("detail") or "",
                    "venue": rec.get("venue") or "", "url": rec.get("url") or "",
                    "source": rec.get("source") or "", "certainty": rec.get("certainty") or "",
                    "precision": rec.get("precision") or "", "key": rec.get("key") or "",
                    "repeat": rec.get("repeat") or "none"}
            hit = corr.get(item["key"])
            if hit:
                item["corroborated"] = True
                item["nasa_url"] = hit.get("nasa_url") or ""
            if kind == "launch" and not rec.get("solid", True):
                tray.append(item)
            else:
                drawn.append(item)
    order = {k: i for i, k in enumerate(store.KIND_ORDER)}
    drawn.sort(key=lambda x: (x["dt"], order.get(x["kind"], 9)))
    return drawn, tray


def _moon_icon(d, cx, cy, r, illum, waxing):
    """輝面比 illum の月円盤。ターミネータの半短軸 = r*|2k-1|（moon_phase.py と同じ式）。"""
    b = max(0.0, r * abs(2 * illum - 1))
    box = [cx - r, cy - r, cx + r, cy + r]
    d.ellipse(box, fill=_DARK)
    d.pieslice(box, -90, 90, fill=_LIT) if waxing else d.pieslice(box, 90, 270, fill=_LIT)
    d.ellipse([cx - r, cy - b, cx + r, cy + b], fill=_LIT if illum >= 0.5 else _DARK)


def _wrap(d, text, fnt, max_w, limit=2):
    lines, cur = [], ""
    for ch in str(text):
        if d.textlength(cur + ch, font=fnt) > max_w:
            lines.append(cur)
            cur = ch
            if len(lines) >= limit:
                cur = ""
                break
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines[:limit]


def _render(year: int, month: int, place: str, tz_label: str, off: float,
            drawn: list, tray: list, warnings: list):
    """月グリッドを描き、(画像, 自己検証) を返す。セルに描いた件を画素で確認する。"""
    ndays = _cal.monthrange(year, month)[1]
    wd0 = (dt.date(year, month, 1).weekday() + 1) % 7
    rows = (wd0 + ndays + 6) // 7
    W, H = 7 * CELL_W, HDR + WD + rows * CELL_H + FOOT
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    f_title, f_sub = load_font(44, bold=True), load_font(20)
    f_wd, f_day, f_ev, f_sm = load_font(22, bold=True), load_font(26, bold=True), load_font(16), load_font(14)

    d.text((28, 24), "{}年{}月 — 宇宙・天文イベントカレンダー".format(year, month), font=f_title, fill=_FG)
    tzdisp = tz_label if tz_label.startswith("UTC") else "{}（UTC{:+g}）".format(tz_label, off)
    d.text((28, 82), "基準地: {} ／ 時刻は {}／ 外部APIのUTCは現地時刻へ変換／ 予定・公開イベントは暦日のまま"
           .format(place, tzdisp), font=f_sub, fill=_DIM)
    x = 28
    for k in store.KIND_ORDER:
        nm, color = store.KINDS[k]
        d.rectangle([x, 126, x + 20, 146], fill=color)
        d.text((x + 28, 126), nm, font=f_sm, fill=_DIM)
        x += 34 + d.textlength(nm, font=f_sm) + 22
    d.line([28, HDR - 6, W - 28, HDR - 6], fill=_GRID, width=2)
    for c in range(7):
        cx = c * CELL_W + CELL_W // 2
        d.text((cx - d.textlength(_WEEKDAYS[c], font=f_wd) / 2, HDR + 8), _WEEKDAYS[c], font=f_wd,
               fill=(232, 150, 150) if c == 0 else (150, 190, 232) if c == 6 else _FG)

    byday = {}
    for ev in drawn:
        byday.setdefault(ev["date"].day, []).append(ev)
    placed, more = [], 0
    for i in range(1, ndays + 1):
        r_, c_ = divmod(wd0 + i - 1, 7)
        x0, y0 = c_ * CELL_W, HDR + WD + r_ * CELL_H
        d.rectangle([x0 + 4, y0 + 4, x0 + CELL_W - 4, y0 + CELL_H - 4], outline=_GRID, width=1)
        d.text((x0 + 12, y0 + 8), str(i), font=f_day, fill=_FG)
        items = byday.get(i, [])
        for ev in items:
            if ev["title"] in _PHASE_ILLUM:
                _moon_icon(d, x0 + CELL_W - 32, y0 + 26, 15, _PHASE_ILLUM[ev["title"]],
                           ev["title"] in ("上弦", "満月"))
        yy = y0 + 50
        for ev in items[:3]:
            color = store.KINDS.get(ev["kind"], store.KINDS["sky"])[1]
            d.rectangle([x0 + 10, yy, x0 + 16, yy + 44], fill=color)
            for j, ln in enumerate(_wrap(d, ev["title"], f_ev, CELL_W - 46, 2)):
                d.text((x0 + 24, yy + j * 20), ln, font=f_ev, fill=color)
            stamp = "目安" if ev["certainty"] == "approx" else (
                "" if ev["all_day"] else ev["dt"].strftime("%H:%M"))
            span = ""
            if ev.get("end") and ev["end"] != ev["date"]:
                span = " 〜{:02d}/{:02d}".format(ev["end"].month, ev["end"].day)
            label = "{} {}".format(stamp, store.KINDS.get(ev["kind"], ("",))[0]).strip()
            d.text((x0 + 24, yy + 42), (label + span)[:34], font=f_sm, fill=_DIM)
            placed.append({"key": ev["key"], "title": ev["title"], "kind": ev["kind"], "color": color,
                           "date": ev["date"].isoformat(),
                           "rect": (x0 + 10, yy - 2, x0 + CELL_W - 14, yy + 40)})
            yy += 62
        if len(items) > 3:
            more += len(items) - 3
            d.text((x0 + 24, yy), "＋{} 件".format(len(items) - 3), font=f_sm, fill=_DIM)
    d.text((28, H - FOOT + 16),
           "出典: Launch Library 2 (thespacedevs.com) ／ JPL DE421 + Skyfield（月相・二十四節気・惑星・食）"
           "／ 国立天文台 イベント情報 ／ NASA 公式リスト（照合用）", font=f_sm, fill=_DIM)
    warn = "／".join(warnings)[:140] if warnings else ""
    d.text((28, H - FOOT + 42),
           "※ 流星群の極大日は目安。公開イベントの開催日は告知ページからの抽出（要確認）。"
           "日付未定の打ち上げ {} 件・セルに入りきらない予定 {} 件は本文に列挙。".format(len(tray), more)
           + (("／⚠ " + warn) if warn else ""), font=f_sm, fill=_DIM)

    px = img.load()
    missing = []
    for p in placed:
        x1, y1, x2, y2 = [int(v) for v in p["rect"]]
        hit = sum(1 for y in range(max(0, y1), min(H, y2)) for xx in range(max(0, x1), min(W, x2))
                  if sum(abs(px[xx, y][k] - p["color"][k]) for k in range(3)) < 30)
        if hit < 30:
            missing.append({"title": p["title"], "date": p["date"], "pixels": hit})
    return img, {"checked": len(placed), "missing": missing, "ok": not missing}


def _err(message: str, **extra) -> CallToolResult:
    """エラーを CallToolResult で返す（例外をツール外へ出さない）。"""
    payload = {"error": message}
    payload.update(extra)
    return CallToolResult(content=[TextContent(type="text", text="⚠️ " + message)], structuredContent=payload)


def _parse_kinds(kinds):
    """kinds 引数を検証する。戻り: (集合 or None, エラー文字列 or None)。"""
    s = str(kinds or "all").strip().lower()
    if s in ("", "all", "*"):
        return None, None
    parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
    bad = [p for p in parts if p not in store.KINDS]
    if bad:
        return None, "kinds に未知の値があります: {}（指定できるのは {} または all）".format(
            ", ".join(bad), "/".join(store.KIND_ORDER))
    return set(parts), None


def _png_bytes(img) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _event_line(ev: dict) -> str:
    """本文（テキスト）に並べる1行。画像を描画しないハーネスでも予定が読めるようにする。"""
    stamp = "終日" if ev["all_day"] else ev["dt"].strftime("%H:%M")
    span = (" 〜{:02d}/{:02d}".format(ev["end"].month, ev["end"].day)
            if ev.get("end") and ev["end"] != ev["date"] else "")
    bits = ["| {} {} | {} | {} |".format(ev["date"].isoformat(), stamp, store.KINDS.get(ev["kind"], ("?",))[0],
                                         ev["title"].replace("|", "/"))]
    tail = []
    if ev.get("venue"):
        tail.append(ev["venue"])
    if ev.get("corroborated") and ev.get("nasa_url"):
        tail.append("[NASA 公式ページ]({})".format(ev["nasa_url"]))
    elif ev.get("url"):
        tail.append("[詳細]({})".format(ev["url"]))
    if ev.get("repeat") and ev["repeat"] != "none":
        # 繰り返しは1件の予定（どの occurrence からでも同じ id で消える）
        tail.append("（{}・1件の予定として削除されます）".format(
            {"daily": "毎日", "weekly": "毎週", "monthly": "毎月", "yearly": "毎年"}.get(ev["repeat"], ev["repeat"])))
    if ev["certainty"] in ("unverified", "date-unknown", "approx"):
        tail.append("（{}）".format({"unverified": "要確認", "date-unknown": "日付未定",
                                    "approx": "目安"}.get(ev["certainty"], ev["certainty"])))
    if tail:
        bits.append(" " + " ／ ".join(tail) + span)
    return "".join(bits)


def space_calendar(year: Optional[int] = None, month: Optional[int] = None, place: str = "東京",
                   kinds: str = "all", lat: Optional[float] = None, lon: Optional[float] = None,
                   ) -> CallToolResult:
    """宇宙・天文イベントの月間カレンダー（打ち上げ・天文現象・公開イベント・自分の予定）を画像とJSONで返す。

    打ち上げは Launch Library 2 の API（月範囲）から、日付が確定した行だけを日付セルに置きます。
    天文現象は JPL DE421 + Skyfield のローカル計算（月相・二十四節気・惑星の衝/合/内合・最大離角・
    日食月食・流星群）、公開・イベントは国立天文台のイベント一覧と JAXA の施設公開（一般公開・特別公開）、
    自分の予定はローカルの蓄積ストア
    （`calendar_event_add` で追加）から取ります。取得結果は蓄積ストアに溜め、要求月 M に対して
    窓 [M-1, M+2] を確保して**未取得・期限切れの月だけ**取りに行きます（月が進むと差分は1ヶ月）。

    **回答時は content に含まれる画像リンク（🖼️ [◯◯を開く](file:///…)）をそのまま提示してください。**
    **`figure.notes` は要約・言い換えせず、そのまま引用してください。**

    Args:
        year: 対象年（既定: 観測地の今年）。
        month: 対象月 1〜12（既定: 観測地の今月）。
        place: 基準地（既定 東京）。任意の地名をジオコーダで解決し、その現地時刻で描きます。
        kinds: 表示するカテゴリ（カンマ区切り）: user/launch/sky/public/holiday。all で全部。
        lat: 緯度（place の代わりに数値で指定）。
        lon: 経度（同上）。
    """
    year_i = as_int(year, None, 1900, 2200)
    month_i = as_int(month, None, 1, 12)
    res = _resolve_place(place, lat, lon)
    if res is None:
        return _err("観測地を解決できませんでした: {!r}。地名を変えるか lat/lon を数値で指定してください".format(place))
    la, lo = res
    off = _local_tz(la, lo)
    today = (dt.datetime.utcnow() + dt.timedelta(hours=off)).date()
    year_i = year_i or today.year
    month_i = month_i or today.month
    kinds_set, kerr = _parse_kinds(kinds)
    if kerr:
        return _err(kerr)

    warnings = _ensure_coverage(year_i, month_i, off)
    store.prune()
    corr = store.kv_get("corroboration_map_v1", None, allow_stale=True) or {}
    drawn, tray = _assemble(year_i, month_i, off, kinds_set, corr)
    tz_label = _tz_label(off)
    try:
        img, verify = _render(year_i, month_i, place, tz_label, off, drawn, tray, warnings)
    except Exception as ex:                     # 描画そのものの失敗はエラー結果にする
        return _err("カレンダーの描画に失敗しました: {}".format(ex))
    png = _png_bytes(img)
    out_path = save_output(png, "space_calendar", "png")

    counts = {k: sum(1 for e in drawn if e["kind"] == k) for k in store.KIND_ORDER}
    nasa_hits = sum(1 for e in drawn + tray if e.get("corroborated"))
    notes = figure_notes(extra=[
        "対象: {}年{}月 ／ 基準地 {}（{}）".format(year_i, month_i, place, tz_label),
        "日付セルに描いたイベント {} 件: ".format(len(drawn)) + "、".join(
            "{} {} 件".format(store.KINDS[k][0], counts[k]) for k in store.KIND_ORDER),
        "日付が確定していない打ち上げ {} 件は日付セルに置かず本文に列挙（LL2 の net_precision が"
        "Day/Hour/Minute/Second 以外、または年末のプレースホルダ）".format(len(tray)),
        "NASA 公式リスト（WP REST・event-type=12929）と照合できた打ち上げは {} 件（公式ページURLを付与）".format(nasa_hits)
        if nasa_hits else "NASA 公式リストとの照合は 0 件（照合できた行のみ公式URLを付けます）",
        "予定・公開イベントは現地の暦日のまま（時刻のタイムゾーン変換をしない）。打ち上げ・天文現象は"
        "UTC から基準地の現地時刻へ変換している",
        "JAXA の施設一般公開・特別公開は告知済みのぶんだけを載せる（一覧に過去分のアーカイブが無い"
        "ため、未告知の月はセルが空になる＝開催なしではない）",
        "自己検証: セルに描いた {} 件のうち、自カテゴリ色を画素で確認できたのは {} 件".format(
            verify["checked"], verify["checked"] - len(verify["missing"])),
        "蓄積ストアは過去 {} 日ぶんの API 由来データを保持し、それより古いものは呼び出し時に"
        "自動削除する（有効なユーザー予定は対象外・削除した予定は tombstone として残る）".format(
            store.KEEP_PAST_DAYS),
        "出典: Launch Library 2 (thespacedevs.com) ／ JPL DE421 + Skyfield ／ 国立天文台 イベント情報"
        " ／ JAXA（ファン!ファン!JAXA!・宇宙科学研究所） ／ nasa.gov（照合用）",
    ])
    fig = figure_payload(
        kind="calendar", title="{}年{}月 宇宙・天文イベントカレンダー".format(year_i, month_i),
        view={"frame": "calendar_grid", "projection": "none",
              "description": "{}年{}月（日曜始まり・{}）の暦に各イベントを配置".format(year_i, month_i, tz_label)},
        notes=notes,
        caption="{}年{}月の宇宙・天文カレンダー（基準地 {}・{}）。日付セルに描いたイベント {} 件"
                "（{}）、日付が確定していない打ち上げ {} 件。".format(
                    year_i, month_i, place, tz_label, len(drawn),
                    "、".join("{} {} 件".format(store.KINDS[k][0], counts[k]) for k in store.KIND_ORDER),
                    len(tray)),
        verify=verify)
    lines = [media_link_line("生成した画像を開く（{}年{}月の宇宙・天文カレンダー）".format(year_i, month_i),
                             path=out_path, kind="figure"),
             "🪐 **{}年{}月 宇宙・天文イベントカレンダー**（基準地 {}・{}）".format(year_i, month_i, place, tz_label)]
    if warnings:
        lines.append("⚠️ " + " ／ ".join(warnings))
    lines += ["", "| 日付 | 時刻 | カテゴリ | 内容 |", "|---|---|---|---|"]
    lines += [_event_line(e) for e in drawn]
    if tray:
        lines += ["", "**日付が確定していない打ち上げ（{} 件・日付セルには置いていません）**:".format(len(tray))]
        lines += ["- {}（{}）".format(e["title"], e["detail"] or "日付未定") for e in tray]
    if not drawn and not tray:
        lines.append("表示できるイベントがありませんでした（`kinds` の指定を確認してください）。")
    lines += ["", figure_text_block(fig)]
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines)),
                 ImageContent(type="image", data=__import__("base64").b64encode(png).decode("ascii"),
                              mimeType="image/png",
                              altText="{}年{}月 宇宙・天文イベントカレンダー（{}）".format(year_i, month_i, place))],
        structuredContent={
            "year": year_i, "month": month_i, "place": place, "lat": la, "lon": lo,
            "tz_label": tz_label, "tz_offset": off, "kinds": sorted(kinds_set) if kinds_set else "all",
            "events": [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v)
                        for k, v in e.items()} for e in drawn],
            "undated_launches": [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v)
                                  for k, v in e.items()} for e in tray],
            "counts": counts, "warnings": warnings, "figure": fig, "verify": verify,
            "image_path": out_path, "store": store.stats(),
            "source": "Launch Library 2 / JPL DE421+Skyfield / 国立天文台 / JAXA / nasa.gov",
        },
    )


def calendar_event_add(title: str, date: str, time: Optional[str] = None, date_end: Optional[str] = None,
                       repeat: Optional[str] = None, note: Optional[str] = None) -> CallToolResult:
    """カレンダーに自分の予定を追加する（ローカルの蓄積ストアに保存。外部へは送信しません）。

    追加した予定は `space_calendar` の図と本文、`calendar_events` の一覧に現れます。予定は
    **フローティングなローカル日時**で保存するので、`place` を変えても予定の日付は動きません
    （海外の予定を入れるときは note に現地時刻を書いてください）。

    Args:
        title: 予定の名前（例: 「すばる望遠鏡 観測提案の締切」）。
        date: 開始日。"2026-10-24" / "2026/10/24" / "2026年10月24日" / "10/24"（年は今年と解釈）。
        time: 開始時刻（例 "10:30" / "10時30分"）。省略すると終日予定になります。
        date_end: 終了日（複数日の予定・展覧会など）。省略すると1日。
        repeat: 繰り返し: none / daily / weekly / monthly / yearly（既定 none）。
        note: 補足メモ（任意）。
    """
    name = str(title or "").strip()
    if not name:
        return _err("title（予定の名前）を指定してください")
    start = store.parse_local_date(date)
    if start is None:
        return _err("日付を解釈できませんでした: {!r}。年を明記した形式（2026-10-24 / 2026年10月24日）で"
                    "指定してください。年の無い「10/24」は、今年の日付が今日より半年以上過ぎていると"
                    "推測せずにエラーにします（例: 3/1 は年を付けてください）".format(date))
    end = None
    if date_end:
        end = store.parse_local_date(date_end)
        if end is None:
            return _err("終了日を解釈できませんでした: {!r}".format(date_end))
    rec, err = store.user_add(name, start, time_value=time, end_date=end, repeat=repeat or "none",
                              note=note or "")
    if err:
        return _err(err)
    when = "{} {}".format(rec["start_local"].replace("T", " "),
                          "（〜{}）".format(rec["end_local"]) if rec.get("end_local") else "").strip()
    repeat_ja = {"none": "", "daily": "・毎日", "weekly": "・毎週", "monthly": "・毎月",
                 "yearly": "・毎年"}.get(rec.get("repeat"), "")
    lines = ["📌 予定を追加しました: **{}**".format(rec["title"]),
             "- 日時: {}{}".format(when, repeat_ja),
             "- 保存先: `{}`（ローカルのみ・外部送信なし）".format(store.STORE_PATH),
             "- ID: `{}`（削除するときは calendar_event_remove(id=...) に渡してください）".format(rec["key"])]
    if rec.get("detail"):
        lines.append("- メモ: " + rec["detail"])
    lines.append("`space_calendar` で今月のカレンダーを描くと、この予定も表示されます。")
    return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))],
                          structuredContent={"event": rec, "store_path": store.STORE_PATH})


def calendar_event_remove(id: Optional[str] = None, title: Optional[str] = None,
                          date: Optional[str] = None) -> CallToolResult:
    """カレンダーの自分の予定を削除する（id か title[+date] で指定）。

    既に無い場合や既に削除済みの場合はエラーにせず「該当なし」を返します（冪等）。
    title だけで複数該当したときは**削除せず候補を提示**します（推測して消さない）。
    """
    if not id and not title:
        return _err("id か title を指定してください（`calendar_events` で一覧と ID を確認できます）")
    d = str(date)[:10] if date else None
    removed, cands, err = store.user_remove(key=id, title=title, date=d)
    if err:
        return _err(err)
    if cands:
        lines = ["複数の予定が該当したため削除していません。id を指定してください:"]
        for c in cands:
            lines.append("- `{}`  {}  {}".format(c.get("key"), c.get("start_local", "").replace("T", " "),
                                                c.get("title", "")))
        return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))],
                              structuredContent={"removed": False, "candidates": cands})
    if not removed:
        return CallToolResult(
            content=[TextContent(type="text", text="該当する予定がありません（既に削除済み、または未登録）。")],
            structuredContent={"removed": False, "candidates": []})
    rep = removed.get("repeat") or "none"
    note = ""
    if rep != "none":
        note = "（{}の繰り返し予定をまとめて削除しました）".format(
            {"daily": "毎日", "weekly": "毎週", "monthly": "毎月", "yearly": "毎年"}.get(rep, rep))
    return CallToolResult(
        content=[TextContent(type="text", text="🗑️ 予定を削除しました: **{}**（{}）{}".format(
            removed.get("title", ""), removed.get("start_local", "").replace("T", " "), note))],
        structuredContent={"removed": True, "event": removed})


def calendar_events(year: Optional[int] = None, month: Optional[int] = None, place: str = "東京",
                    kinds: str = "all") -> CallToolResult:
    """蓄積済みのイベント一覧を返す（図を描かず、外部APIも呼ばない軽い経路）。

    蓄積ストア（`space_calendar` が取得したものと `calendar_event_add` で入れた予定）だけを読みます。
    呼び出し時に保持期限（カレンダーと同じ日数＝過去データの自動削除）も適用します。
    「今月の予定は？」「10月の公開イベントは？」のように、まず一覧だけ見たいときに使ってください。
    打ち上げ・天文現象の鮮度は `structuredContent.sources` に取得時刻として入ります。

    Args:
        year: 対象年（既定: 基準地の今年）。
        month: 対象月（既定: 基準地の今月）。
        place: タイムゾーン決定のための基準地（既定 東京）。
        kinds: カテゴリの絞り込み: user/launch/sky/public/holiday（カンマ区切り）。all で全部。
    """
    res = _resolve_place(place, None, None)
    if res is None:
        return _err("観測地を解決できませんでした: {!r}".format(place))
    off = _local_tz(res[0], res[1])
    today = (dt.datetime.utcnow() + dt.timedelta(hours=off)).date()
    year_i = as_int(year, today.year, 1900, 2200)
    month_i = as_int(month, today.month, 1, 12)
    kinds_set, kerr = _parse_kinds(kinds)
    if kerr:
        return _err(kerr)
    # 一覧だけを使い続けても古いデータが残らないように、ここでも保持期限を適用する
    # （space_calendar と同じ KEEP_PAST_DAYS。有効なユーザー予定は対象外）
    store.prune()
    corr = store.kv_get("corroboration_map_v1", None, allow_stale=True) or {}
    drawn, tray = _assemble(year_i, month_i, off, kinds_set, corr)
    counts = {k: sum(1 for e in drawn if e["kind"] == k) for k in store.KIND_ORDER}
    lines = ["📅 **{}年{}月のイベント一覧**（蓄積ストア・基準地 {}）".format(year_i, month_i, place)]
    if not drawn and not tray:
        lines.append("該当するイベントがありません（`space_calendar` で取得するか、kinds を確認してください）。")
    if drawn:
        lines += ["", "| 日付 | 時刻 | カテゴリ | 内容 |", "|---|---|---|---|"]
        lines += [_event_line(e) for e in drawn]
    if tray:
        lines += ["", "日付が確定していない打ち上げ（{} 件）:".format(len(tray))]
        lines += ["- {}（{}）".format(e["title"], e["detail"] or "日付未定") for e in tray]
    lines.append("")
    lines.append("出典: Launch Library 2 ／ JPL DE421 + Skyfield ／ 国立天文台 イベント情報（カテゴリ別 {}）"
                 .format("、".join("{} {} 件".format(store.KINDS[k][0], counts[k]) for k in store.KIND_ORDER)))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "year": year_i, "month": month_i, "place": place, "counts": counts,
            "events": [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v)
                        for k, v in e.items()} for e in drawn],
            "undated_launches": [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v)
                                  for k, v in e.items()} for e in tray],
            "sources": store.stats().get("sources", {}),
            "source": "蓄積ストア（ローカル）",
        },
    )


# 告知ページのドメインから開催地を推定する（一覧に開催地の列が無いため）
_VENUE_HOSTS = (
    ("miz.nao.ac.jp", "水沢（岩手・国立天文台）"), ("nro.nao.ac.jp", "野辺山（長野・国立天文台）"),
    ("nao.ac.jp", "国立天文台"), ("kwasan.kyoto-u.ac.jp", "花山/飛騨/岡山（京都大学）"),
    ("city.ishigaki.okinawa.jp", "石垣島（沖縄）"), ("nins.jp", "自然科学研究機構"),
)
_NINS_RANGE = re.compile(r"開催日\s*[:：]\s*(\d{4})\.(\d{1,2})\.(\d{1,2})"
                          r"(?:\s*[〜~]\s*(\d{4})\.(\d{1,2})\.(\d{1,2}))?")
_NINS_POSTED = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_NINS_HREF = re.compile(r'href="([^"]+)"')


def _public_events(year: int, month: int):
    """国立天文台の公開・特別公開（NINS のイベント一覧・HTML）。

    一覧は開催日の**範囲**を持つので、期間イベントは end_local に入れて「開始日に置いて
    〜MM/DD を添える」描画に渡す。抽出値は unverified（要確認）扱いにする。

    実装: まず `news-item` ブロックに分割し、その中で小さな正規表現を当てる。
    1つの大きな正規表現で `.*?` を入れ子にすると、この 165KB のページでは
    壊滅的バックトラックで**ハングする**（実測: 4分以上返らない）。
    """
    src = "public:{}".format(year)
    if store.source_is_fresh(src, store.TTL_PUBLIC, window=str(year)):
        return [], None
    try:
        r = requests.get(NINS_EVENTS, timeout=(10, 25), headers=UA)
        r.encoding = "utf-8"
        r.raise_for_status()
    except requests.RequestException as ex:
        return [], "国立天文台のイベント一覧を取得できませんでした: {}".format(ex)
    out, seen = [], set()
    for blk in r.text.split('<div class="news-item">')[1:]:
        rng = _NINS_RANGE.search(blk)
        href = _NINS_HREF.search(blk)
        if not (rng and href):
            continue
        url = href.group(1)
        if url in seen:
            continue
        seen.add(url)
        posted = _NINS_POSTED.search(blk[:400])
        label = ""
        lab = re.search(r'<div class="news-label">\s*(.*?)\s*</div>', blk, re.S)
        if lab:
            label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", lab.group(1))).strip()
        ttl_m = re.search(r'class="press-item"[^>]*>\s*(.*?)\s*<br', blk, re.S)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ttl_m.group(1))).strip() if ttl_m else ""
        if not title:
            continue
        sy, sm, sd = int(rng.group(1)), int(rng.group(2)), int(rng.group(3))
        ey, em, ed = (int(rng.group(4)), int(rng.group(5)), int(rng.group(6))) if rng.group(4) else (sy, sm, sd)
        try:
            start, end = dt.date(sy, sm, sd), dt.date(ey, em, ed)
        except ValueError:
            continue
        if end < start:
            start, end = end, start
        venue = next((v for h, v in _VENUE_HOSTS if h in url), label)
        out.append({
            "key": "public:{}".format(url), "kind": "public", "title": title[:70],
            "detail": "{} ／ 告知 {}".format(label, "-".join(posted.groups()) if posted else "?"),
            "start_local": start.isoformat() + "T00:00", "end_local": end.isoformat(),
            "all_day": True, "venue": venue, "source": "国立天文台 イベント情報", "url": url,
            "certainty": "unverified",
        })
    return out, None



# ---- JAXA 施設公開（一般公開・特別公開）----------------------------------------
def _strip_tags(s: str) -> str:
    """HTML 断片からタグを落として空白を畳む（この用途だけの小さな補助）。"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def _jaxa_dates(when: str):
    """告知文から日付を拾う。戻り: (開始日, 終了日 or None)。

    1件に複数の日付が並ぶ（実測: 「【特別公開】11/7…【オンライン】11/8…」）ので、最初の日付を
    開始、最後の日付を終了に寄せる（現地／オンラインの2日間は範囲として描く）。
    """
    ds = []
    for m in _JAXA_DATE.finditer(when or ""):
        try:
            ds.append(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    if not ds:
        return None, None
    return ds[0], (ds[-1] if ds[-1] > ds[0] else None)


def _jaxa_record(venue: str, title: str, when: str, start, end, url: str, source: str) -> dict:
    """1件の施設公開レコードへ正規化する（時刻は告知文のまま detail に残す）。"""
    title = re.sub(r"のお知らせ$", "", title).strip() or title
    return {
        "key": "jaxa:" + url, "kind": "public", "title": title[:70],
        "detail": "{} ／ {}".format(venue or "JAXA", when),
        "start_local": start.isoformat() + "T00:00",
        "end_local": end.isoformat() if end else None,
        "all_day": True, "venue": venue, "source": source, "url": url,
        # 「（予定）」付きの告知は確定前（実測: 相模原の【オンライン】行）
        "certainty": "unverified" if "予定" in when else "confirmed",
    }


def _jaxa_fanfun_visit() -> list:
    """ファン!ファン!JAXA! の「施設見学」ページから日付付きの公開イベントを抽出する。

    一覧は「近日開催」だけのスナップショット（過去分のアーカイブは無い）。実測では `<li>` の中に
    施設名の `<span>` ＋ `<time>日付</time>` ＋ `<a href>` が揃う行だけが公開イベントで、
    「お知らせ」「休館案内」の行にはカテゴリの `<span>` が付く。大きな正規表現は使わない
    （大きな HTML では壊滅的バックトラックでハングする実測がある: NINS の一覧参照）。
    """
    r = requests.get(FANFUN_VISIT, timeout=(10, 25), headers=UA)
    r.encoding = "utf-8"
    r.raise_for_status()
    out = []
    for raw_li in r.text.split("<li>")[1:]:
        seg = raw_li.split("</li>")[0]
        tm = re.search(r"<time[^>]*>(.*?)</time>", seg, re.S)
        a = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', seg, re.S)
        if not (tm and a):
            continue
        labels = [_strip_tags(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", seg, re.S)]
        if not labels or any(l not in JAXA_FACILITIES for l in labels):
            continue                       # 施設名以外のラベル＝お知らせ・休館案内など（イベントではない）
        when, title = _strip_tags(tm.group(1)), _strip_tags(a.group(2))
        if not _JAXA_HINT.search(title + when):
            continue                       # 公開・施設紹介以外（見学規制の告知など）は採らない
        start, end = _jaxa_dates(when)
        if start is None:
            continue
        out.append(_jaxa_record(labels[0], title, when, start, end, a.group(1),
                                "ファン!ファン!JAXA!（施設見学）"))
    return out


def _jaxa_isas_events() -> list:
    """JAXA 宇宙科学研究所のイベント表から「公開」イベントを抽出する（過去1年ぶんが残る）。

    fanfun の一覧が「近日開催」しか持たないのに対し、こちらは月ごとの表で過去分も並ぶので、
    過去月の補完に使う。表は `<th>月</th>` ＋ `<time>` ＋ `<a>` の素直な構造。
    """
    r = requests.get(ISAS_EVENTS, timeout=(10, 25), headers=UA)
    r.encoding = "utf-8"
    r.raise_for_status()
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", r.text, re.S):
        for blk in re.split(r'<div class="events-table__blc"', tr)[1:]:
            tm = re.search(r"<time[^>]*>(.*?)</time>", blk, re.S)
            a = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
            if not (tm and a):
                continue
            title = _strip_tags(a.group(2))
            if "公開" not in title:
                continue                   # 一般公開・特別公開だけ（講演会・ライブ配信は対象外）
            when = _strip_tags(tm.group(1))
            start, end = _jaxa_dates(when)
            if start is None:
                continue
            venue = next((v for v in JAXA_FACILITIES if v in title), "")
            out.append(_jaxa_record(venue, title, when, start, end,
                                    urljoin(ISAS_EVENTS, a.group(1)),
                                    "JAXA 宇宙科学研究所 イベント情報"))
    return out


def _jaxa_public_events() -> Optional[str]:
    """JAXA の施設公開（fanfun の近日開催＋ISAS の過去分）を取り込む。戻り: 警告 or None。

    fanfun はアーカイブを持たないスナップショットなので、月キーの read-through（窓 [M-1, M+2]）には
    載せず、**取得日を窓にして毎日取り直す**。窓を年や月で固定すると「まだ告知されていない月」を
    「取得済み＝予定なし」と凍結してしまう（未告知の月が空なのは開催なしではない）。
    片方の取得に失敗したら complete=False にして、当日中は再取得を許す。
    """
    src = "jaxa_public:v1"
    window = dt.date.today().isoformat()
    if store.source_is_fresh(src, store.TTL_PUBLIC, window=window):
        return None
    recs, errs = [], []
    for fn, label in ((_jaxa_fanfun_visit, "ファン!ファン!JAXA!"),
                      (_jaxa_isas_events, "宇宙科学研究所")):
        try:
            recs.extend(fn())
        except requests.RequestException as ex:
            errs.append("{}: {}".format(label, ex))
    seen, uniq = set(), []
    for rec in recs:
        norm = re.sub(r"\s+", "", rec["title"])
        if norm in seen:                   # 相模原の特別公開は両方に載る（先勝ちで1件に寄せる）
            continue
        seen.add(norm)
        uniq.append(rec)
    store.upsert(uniq, src, window=window, complete=not errs, failed=errs, ttl=store.TTL_PUBLIC)
    return " ／ ".join(errs) if errs else None


def _phases(eph, ts, t0, t1):
    from skyfield import almanac
    names = {0: "新月", 1: "上弦", 2: "満月", 3: "下弦"}
    tt, ph = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    return [(t, names[p]) for t, p in zip(tt, ph)]


def _solar_terms(eph, ts, t0, t1):
    """二十四節気 = 太陽の視黄経が 15° の倍数を跨ぐ瞬間（暦要項の JST 値と一致する）。"""
    import numpy as np
    from skyfield import almanac
    from skyfield.framelib import ecliptic_frame
    names = ["春分", "清明", "穀雨", "立夏", "小満", "芒種", "夏至", "小暑", "大暑", "立秋", "処暑", "白露",
             "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至", "小寒", "大寒", "立春", "雨水", "啓蟄"]

    def f(t):
        # find_discrete は Time の配列を渡し、関数に step_days 属性を要求する
        lon = eph["earth"].at(t).observe(eph["sun"]).apparent().frame_latlon(ecliptic_frame)[1].degrees
        return (np.floor(np.asarray(lon) % 360.0 / 15.0).astype(int)) % 24

    f.step_days = 1.0
    tt, idx = almanac.find_discrete(t0, t1, f)
    return [(t, names[i]) for t, i in zip(tt, idx)]


def _planet_events(eph, ts, t0, t1):
    """外惑星=衝/合。**内惑星は Skyfield の「衝」スロットが内合を返す**ので読み替え、
    太陽離角の極大（最大離角）を1時間刻みで探して「夕方の西空/明け方の東空」まで出す。"""
    from skyfield import almanac
    from skyfield.framelib import ecliptic_frame
    out, e = [], eph["earth"]
    inner_bodies = (("水星", "MERCURY BARYCENTER"), ("金星", "VENUS BARYCENTER"))
    for name, key, is_inner in (("水星", "MERCURY BARYCENTER", True), ("金星", "VENUS BARYCENTER", True),
                                ("火星", "MARS BARYCENTER", False), ("木星", "JUPITER BARYCENTER", False),
                                ("土星", "SATURN BARYCENTER", False), ("天王星", "URANUS BARYCENTER", False),
                                ("海王星", "NEPTUNE BARYCENTER", False)):
        tt, ph = almanac.find_discrete(t0, t1, almanac.oppositions_conjunctions(eph, eph[key]))
        for t, p in zip(tt, ph):
            if is_inner:
                label = "内合（太陽の手前・観測不向き）" if p == 0 else "外合（太陽の向こう・観測不向き）"
            else:
                label = "衝（観測好機）" if p == 0 else "合（太陽と同方向・観測不向き）"
            out.append((t, "{}が{}".format(name, label), "地心での太陽との離角が極値"))
    steps = int((t1.tt - t0.tt) * 24) + 1
    series = {k: [] for _, k in inner_bodies}
    sun_lon = []
    for i in range(steps):
        t = ts.tt_jd(t0.tt + i / 24.0)
        sun_pos = e.at(t).observe(eph["sun"]).apparent()
        sun_lon.append(sun_pos.frame_latlon(ecliptic_frame)[1].degrees)
        for _, k in inner_bodies:
            ap = e.at(t).observe(eph[k]).apparent()
            series[k].append((ap.separation_from(sun_pos).degrees,
                              ap.frame_latlon(ecliptic_frame)[1].degrees))
    for nm, k in inner_bodies:
        v = series[k]
        for i in range(1, len(v) - 1):
            if v[i][0] > v[i - 1][0] and v[i][0] >= v[i + 1][0] and v[i][0] > 15.0:
                side = ("夕方の西空（東方最大離角）" if (v[i][1] - sun_lon[i]) % 360.0 < 180.0
                        else "明け方の東空（西方最大離角）")
                out.append((ts.tt_jd(t0.tt + i / 24.0),
                            "{}が最大離角 {:.1f}°・{}".format(nm, v[i][0], side), "太陽離角の極大（観測好機）"))
    return out


def _eclipses(eph, ts, t0, t1):
    """日食・月食の有無（地球のどこかで起こるか。観測地での可視は solar_eclipse_series 側）。"""
    out, e = [], eph["earth"]
    for t, nm in _phases(eph, ts, t0, t1):
        sep = e.at(t).observe(eph["moon"]).apparent().separation_from(
            e.at(t).observe(eph["sun"]).apparent()).degrees
        if nm == "新月" and sep < 1.6:
            out.append((t, "日食（地球のどこかで・離角 {:.2f}°）".format(sep), "観測地での可視は別判定"))
        if nm == "満月" and abs(180.0 - sep) < 1.6:
            out.append((t, "月食（地球のどこかで・離角 {:.2f}°）".format(abs(180.0 - sep)), "観測地での可視は別判定"))
    return out

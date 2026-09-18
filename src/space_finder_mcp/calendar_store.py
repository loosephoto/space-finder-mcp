"""宇宙・天文カレンダーの蓄積ストア（正規化レコード＋来歴／ユーザー予定の読み書き）。

設計の位置づけ:
- 保存先は ``%LOCALAPPDATA%\space-finder-mcp\calendar_store.json``。``cache.CACHE_ROOT`` は
  ``%LOCALAPPDATA%\Temp\space_finder_mcp`` で Windows のディスククリーンアップ対象なので流用しない
  （ユーザーの予定が消えてはいけない）。``img_common.save_output()`` も keep 超過分を削除するため不可。
- 書き込みは tmp ファイル＋``os.replace``（部分書き込みを見せない）＋ ``threading.RLock``。
  LLM は1ターンで複数のツールを並行に呼ぶので、このファイルは同一プロセスの複数スレッドから触られる。
- レコードはキーでマージする（後勝ち・``first_seen`` は保持）。API 由来は prune の対象、
  ``user:`` の予定は永久に残し、削除は tombstone で行う。
- **エラーは蓄積しない**。取れなかったソースは ``sources[dst]["failed"]`` に残して ``complete=false`` にし、
  その状態では「窓を覆った」と見なさない（穴を凍結しない）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import uuid
from typing import Iterable, Optional

STORE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "space-finder-mcp")
STORE_PATH = os.path.join(STORE_DIR, "calendar_store.json")
STORE_VERSION = 1

# ソース別 TTL（秒）。打ち上げは窓が動く（TBD→Go・日付変更）ので現在月は短く、未来月は長め。
TTL_LAUNCH_CURRENT = 30 * 60
TTL_LAUNCH_FUTURE = 6 * 3600
TTL_PUBLIC = 24 * 3600
TTL_NASA_LIST = 24 * 3600
TTL_NEVER = None          # 計算値は決定的なので期限なし（キーに年月が入る）

# カテゴリ（表示名と色）。図の描画と figure.notes の双方がこの1つの表を出典にする。
KINDS = {
    "user":    ("ユーザー予定", (255, 138, 196)),
    "launch":  ("打ち上げ", (110, 168, 254)),
    "sky":     ("天文現象", (245, 200, 107)),
    "public":  ("公開・イベント", (126, 224, 168)),
    "holiday": ("暦・祝日", (154, 164, 178)),
}
KIND_ORDER = ("user", "launch", "sky", "public", "holiday")

_LOCK = threading.RLock()
_REPEATS = ("none", "daily", "weekly", "monthly", "yearly")
# 年末に置かれる「日付未定」のプレースホルダ（LL2 と NASA の双方で実測）。
_YEAR_END = re.compile(r"-12-(30|31)$|-01-01$")


def _now() -> float:
    return time.time()


def _blank() -> dict:
    return {"version": STORE_VERSION, "sources": {}, "events": {}, "kv": {}}


def load() -> dict:
    """ストアを読む（無い・壊れている場合は空で開始。例外を外へ出さない）。"""
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _blank()
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        return _blank()
    data.setdefault("sources", {})
    data.setdefault("events", {})
    data.setdefault("kv", {})
    return data


def save(data: dict) -> bool:
    """ストアを原子的に書く（tmp に書いて os.replace）。成功したら True。"""
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        tmp = "{}.{}.tmp".format(STORE_PATH, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, STORE_PATH)      # 途中書きを見せない
        return True
    except OSError:
        return False


def is_year_end_placeholder(start_iso: Optional[str]) -> bool:
    """日付が年末（12/30〜01/01）なら「日付未定」のプレースホルダと見なす。

    LL2 は net_precision が Year/Quarter の行を 2026-12-31 に置き、NASA のイベントも
    年末に吹き溜まる（実測）。両源に共通の判定を入れて、日付セルに架空の予定を描かない。
    """
    return bool(start_iso and _YEAR_END.search(str(start_iso)[:10]))


def upsert(records: Iterable[dict], source: str, *, window: Optional[str] = None,
           complete: bool = True, failed: Iterable[str] = (), ttl: Optional[float] = None) -> int:
    """正規化レコードを取り込み、ソースの来歴（取得時刻・窓・失敗）を記録する。

    - 同一キーは後勝ちで更新し、``first_seen`` と ``revision`` の履歴は保持する。
    - ``complete=False`` でもレコードは入れるが、``sources[source]["complete"]`` を False にして
      「窓を覆った」とは見なさない（呼び出し側が再取得を判断できるようにする）。
    - **エラーそのものは蓄積しない**（呼び出し側がエラー結果を records に入れない）。
    """
    rows = [r for r in records if isinstance(r, dict) and r.get("key") and r.get("title")]
    with _LOCK:
        data = load()
        now = _now()
        for r in rows:
            key = str(r["key"])
            old = data["events"].get(key)
            rec = dict(r)
            rec.setdefault("kind", "sky")
            rec["last_seen"] = now
            if old:
                rec["first_seen"] = old.get("first_seen", now)
                rec["revision"] = int(old.get("revision", 1)) + 1
                # 削除済み（tombstone）は API 側の再取得で復活させない
                if old.get("deleted"):
                    rec["deleted"] = True
            else:
                rec["first_seen"] = now
                rec["revision"] = 1
            data["events"][key] = rec
        data["sources"][source] = {
            "fetched_at": now, "window": window, "complete": bool(complete),
            "failed": sorted({str(f) for f in failed}), "count": len(rows), "ttl": ttl,
        }
        save(data)
    return len(rows)


def source_meta(source: str) -> dict:
    """ソースの来歴（取得時刻・窓・失敗・TTL）を返す。無ければ空 dict。"""
    return copy.deepcopy(load().get("sources", {}).get(source, {}) or {})


def source_is_fresh(source: str, ttl: Optional[float], window: Optional[str] = None) -> bool:
    """ソースが「TTL 内」かつ「同じ窓」かつ「完全に取得できている」か。"""
    meta = source_meta(source)
    if not meta or meta.get("complete") is False:
        return False
    if window is not None and meta.get("window") != window:
        return False
    if ttl is None:
        return bool(meta)
    return (_now() - float(meta.get("fetched_at") or 0)) < float(ttl)


def records(*, kinds: Optional[Iterable[str]] = None, sources: Optional[Iterable[str]] = None,
            include_deleted: bool = False) -> list:
    """蓄積レコードを返す（コピーを返す＝呼び出し側がストアの中身を書き換えない）。"""
    want_k = set(kinds) if kinds else None
    want_s = set(sources) if sources else None
    out = []
    for rec in load().get("events", {}).values():
        if not isinstance(rec, dict):
            continue
        if rec.get("deleted") and not include_deleted:
            continue
        if want_k is not None and rec.get("kind") not in want_k:
            continue
        if want_s is not None and rec.get("source") not in want_s:
            continue
        out.append(copy.deepcopy(rec))
    return out


def prune(keep_past_days: int = 45, tombstone_days: int = 180) -> int:
    """古い API 由来レコードと古い tombstone を消す（``user:`` の予定は消さない）。"""
    now = _now()
    removed = 0
    with _LOCK:
        data = load()
        keep_past = now - keep_past_days * 86400
        for key, rec in list(data["events"].items()):
            if not isinstance(rec, dict):
                data["events"].pop(key, None)
                removed += 1
                continue
            kind = rec.get("kind")
            if kind == "user" and not rec.get("deleted"):
                continue                      # ユーザーの予定は永久に残す
            stamp = rec.get("start_local") or rec.get("start_utc") or ""
            if rec.get("deleted"):
                if now - float(rec.get("deleted_at") or now) > tombstone_days * 86400:
                    data["events"].pop(key, None)
                    removed += 1
                continue
            try:
                when = time.mktime(time.strptime(str(stamp)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                try:
                    when = time.mktime(time.strptime(str(stamp)[:10], "%Y-%m-%d"))
                except ValueError:
                    continue
            if when < keep_past:
                data["events"].pop(key, None)
                removed += 1
        if removed:
            save(data)
    return removed


# ---- 日付・時刻の解析（ツール入口の防御的変換。テスト可能な純関数としてここに置く）----
_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$"),
    re.compile(r"^(\d{1,2})[-/月](\d{1,2})日?$"),
)


def parse_local_date(value, *, today=None):
    """'2026-10-24' / '2026/10/24' / '2026年10月24日' / '10/24' / '10月24日' を date にする。

    年の無い入力は**今年**と解釈するが、その結果が今日より 180 日以上過去になる場合は
    推測せず None を返す（呼び出し側が「年を指定してください」と候補を提示する）。
    解釈できない場合も None。
    """
    import datetime as dt
    s = str(value or "").strip()
    if not s:
        return None
    m = _DATE_PATTERNS[0].match(s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_PATTERNS[1].match(s)
    if m:
        base = today or dt.date.today()
        try:
            cand = dt.date(base.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if (base - cand).days > 180:
            return None
        return cand
    return None


def parse_local_time(value):
    """'10:30' / '10時30分' / '10時' を (時, 分) にする。解釈できなければ None。"""
    s = str(value or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})\s*[:：時]\s*(\d{1,2})?\s*分?$", s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    return (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def _fmt_local(date_obj, time_obj) -> str:
    return date_obj.isoformat() + "T" + time_obj.strftime("%H:%M")


def user_add(title: str, start_date, *, time_value=None, end_date=None, repeat: str = "none",
             note: str = "") -> tuple:
    """ユーザー予定を追加する。戻り: (record, error)。予定は**フローティングなローカル日時**で持つ。

    UTC に正規化すると、``place`` を変えて見たときに自分の予定が別の日に移る（設計判断）。
    削除は tombstone で行う（他のプロセスが持つ古いコピーで復活しないように）。
    """
    import datetime as dt
    rep = str(repeat or "none").strip().lower() or "none"
    if rep not in _REPEATS:
        return None, "repeat は {} のいずれかにしてください: {!r}".format("/".join(_REPEATS), repeat)
    if start_date is None:
        return None, "日付を解釈できませんでした（例: 2026-10-24 / 10/24 / 2026年10月24日）"
    hhmm = parse_local_time(time_value)
    if time_value and hhmm is None:
        return None, "時刻を解釈できませんでした（例: 10:30 / 10時30分）"
    if end_date is not None and end_date < start_date:
        return None, "終了日が開始日より前です: {} < {}".format(end_date, start_date)
    start_local = _fmt_local(start_date, dt.time(*hhmm) if hhmm else dt.time(0, 0))
    end_local = end_date.isoformat() if end_date else None
    key = "user:" + uuid.uuid4().hex[:12]
    rec = {"key": key, "kind": "user", "title": str(title).strip()[:120],
           "detail": str(note or "")[:400], "start_local": start_local, "end_local": end_local,
           "all_day": hhmm is None, "repeat": rep, "source": "ユーザー予定",
           "certainty": "user", "url": "", "venue": ""}
    upsert([rec], "user", complete=True)
    return copy.deepcopy(rec), None


def user_remove(*, key=None, title=None, date=None) -> tuple:
    """ユーザー予定を削除する（tombstone）。戻り: (removed_record, candidates, error)。

    - 既に無い/既に削除済みなら ``(None, [], None)``＝エラーにしない（冪等）。
    - 題名＋日付で複数該当したら **削除せず候補を返す**（推測して消さない）。
    """
    with _LOCK:
        data = load()
        cands = []
        for k, rec in data["events"].items():
            if not isinstance(rec, dict) or rec.get("deleted") or rec.get("kind") != "user":
                continue
            if key and k != str(key):
                continue
            if title and str(rec.get("title", "")).strip() != str(title).strip():
                continue
            if date and not str(rec.get("start_local", "")).startswith(str(date)[:10]):
                continue
            cands.append(k)
        if not cands:
            return None, [], None
        if len(cands) > 1 and not key:
            return None, [copy.deepcopy(data["events"][c]) for c in cands], None
        target = str(key) if key else cands[0]
        rec = data["events"].get(target)
        if not rec:
            return None, [], None
        rec["deleted"] = True
        rec["deleted_at"] = _now()
        rec["last_seen"] = _now()
        save(data)
        return copy.deepcopy(rec), [], None


def expand_user_occurrences(rec: dict, year: int, month: int) -> list:
    """ユーザー予定を指定月の occurrence に展開する（repeat 対応・上限つき）。"""
    import calendar as _cal
    import datetime as dt
    out = []
    raw = str(rec.get("start_local") or "")
    try:
        base = dt.datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return out
    rep = str(rec.get("repeat") or "none")
    days = _cal.monthrange(year, month)[1]
    first, last = dt.date(year, month, 1), dt.date(year, month, days)
    span_days = 1
    if rec.get("end_local"):
        try:
            span_days = max(1, (dt.date.fromisoformat(str(rec["end_local"])[:10])
                                - dt.date(base.year, base.month, base.day)).days + 1)
        except ValueError:
            span_days = 1

    def emit(day_start: dt.date):
        if first <= day_start <= last:
            s = dt.datetime.combine(day_start, base.time())
            e = (dt.datetime.combine(day_start + dt.timedelta(days=span_days - 1), base.time())
                 if span_days > 1 else None)
            out.append((s, e))

    if rep == "none":
        emit(dt.date(base.year, base.month, base.day))
    elif rep == "daily":
        for i in range(days):
            emit(first + dt.timedelta(days=i))
    elif rep == "weekly":
        for i in range(days):
            d = first + dt.timedelta(days=i)
            if d.weekday() == dt.date(base.year, base.month, base.day).weekday():
                emit(d)
    elif rep == "monthly":
        if base.day <= days:
            emit(dt.date(year, month, base.day))
    elif rep == "yearly":
        if base.month == month and base.day <= days:
            emit(dt.date(year, month, base.day))
    return out[:62]


def stats() -> dict:
    """ストアの要約（件数・ソース別の取得状況）。"""
    data = load()
    counts = {}
    for rec in data.get("events", {}).values():
        if isinstance(rec, dict) and not rec.get("deleted"):
            counts[rec.get("kind")] = counts.get(rec.get("kind"), 0) + 1
    return {"path": STORE_PATH, "events": counts, "total": sum(counts.values()),
            "sources": copy.deepcopy(data.get("sources", {})), "size_bytes": (
                os.path.getsize(STORE_PATH) if os.path.exists(STORE_PATH) else 0)}


def kv_put(key: str, value) -> bool:
    """小さな付随データ（NASA の打ち上げリスト等）を TTL 付きで保存する。"""
    with _LOCK:
        data = load()
        data.setdefault("kv", {})[str(key)] = {"at": _now(), "value": value}
        return save(data)


def kv_get(key: str, ttl: Optional[float] = None, *, allow_stale: bool = False):
    """付随データを読む（TTL 切れなら None。allow_stale なら期限切れでも返す）。"""
    item = (load().get("kv", {}) or {}).get(str(key)) or {}
    if not isinstance(item, dict) or "value" not in item:
        return None
    if ttl is not None and (_now() - float(item.get("at") or 0)) >= float(ttl) and not allow_stale:
        return None
    return copy.deepcopy(item["value"])

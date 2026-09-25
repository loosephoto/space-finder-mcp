"""WMO OSCAR — 世界気象機関の衛星カタログ（認証不要）。

WMO OSCAR/Space (space.oscar.wmo.int) は、気象・地球環境観測を行う全世界の
衛星（Meteor-M, Resurs-P, Kanopus, GOES, Himawari, Sentinel 等）の運用ステータス・
軌道・打ち上げ情報を収録した公式カタログ。APIは認証不要。

API の search / space_agency / status パラメータは現状動作しない（常に全件を返す）ため、
**カタログ全件（約1,000件・35ページ）を並列取得**してクライアントサイドで絞り込む。
以前は先頭10ページ（300件）だけを走査していたため、カタログ後半に固まっている国の
衛星を取りこぼしていた（実測: 「ロシアの気象衛星」で Meteor-M が出てこない）。
取得結果は1日キャッシュする。

絞り込みは**一致度で順位付け**する。単純な部分一致では query="meteor" が
「**Meteor**ological（気象）」を含む DMSP・COSMIC 等の名称を大量に拾い、
本来の Meteor-M が埋もれる（実測: 92件の一致に埋没）。そこで acronym の完全一致・
前方一致を最優先し、名称は語境界で照合、名称の途中に含まれるだけの一致は
低順位として件数を分けて表示する。

出典: space.oscar.wmo.int/api/v1
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import os
import re
import time
from typing import Optional

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_DAILY, disk_cache_path, ttl_cache
from .input_utils import as_int
from .name_common import expand_terms

OSCAR = "https://space.oscar.wmo.int/api/v1"
UA = {"User-Agent": "space-finder-mcp/0.30 (MCP; WMO OSCAR/Space)"}
_PAGE_WORKERS = 12         # ページ取得の並列度（1ページ約4.5秒 × 35ページ）
_CACHE_KEY = "oscar:catalogue"   # カタログをディスクにも保存するキー

# 衛星運用ステータスの日本語訳
_STATUS = {
    "operational": "運用中", "planned": "計画中", "extended": "延長運用",
    "decommissioned": "退役", "failed": "故障", "nominal": "正常",
}
# 同じ一致度なら「運用中 → 調整中 → 計画中 → 退役」の順に見せる（読み手の関心順）
_STATUS_RANK = {"operational": 0, "commissioning": 1, "planned": 2,
                "considered": 3, "back-up": 4}


def _fetch_page(page: int) -> dict:
    """OSCAR の衛星一覧を1ページ取得する（1ページ=30件）。"""
    r = requests.get(f"{OSCAR}/satellites", params={"page": page}, headers=UA, timeout=(30, 30))
    r.raise_for_status()
    return r.json()


def _disk_read(ttl: float):
    """ディスクキャッシュからカタログを読む（TTL 内のみ。壊れていれば None）。

    OSCAR は **1ページ30件固定・1ページあたり約4.5秒**で、全35ページの取得に
    40秒近くかかる。プロセス内キャッシュだけだと MCP サーバーを再起動するたびに
    この待ち時間を毎回払うため、カタログをディスクにも残して2回目以降を即時にする。
    """
    path = disk_cache_path(_CACHE_KEY, subdir="catalogue")
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as f:
            cat = json.load(f)
        return cat if isinstance(cat, dict) and cat.get("satellites") else None
    except (OSError, ValueError):
        return None


def _disk_write(cat: dict) -> None:
    """カタログをディスクへ保存する（書き込み失敗は無視して動作は続ける）。"""
    path = disk_cache_path(_CACHE_KEY, subdir="catalogue")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cat, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


@ttl_cache(TTL_DAILY, maxsize=4)
def _catalogue() -> dict:
    """OSCAR の全衛星（全ページ）を取得してキャッシュする。

    1ページ目で総ページ数を得てから残りを並列に取得する（実測: 35ページ・約1,000件）。
    取得に失敗したページは **failed_pages に残して必ず呼び出し側で報告する**
    （黙って欠けさせると「カタログに無い＝存在しない」と誤読される）。
    **不完全なカタログ（失敗ページあり）はディスクに固定しない**（欠けたまま1日
    使われないように）。戻り値はキャッシュを共有するので書き換えないこと。
    """
    from_disk = _disk_read(TTL_DAILY)
    if from_disk is not None:
        return from_disk
    first = _fetch_page(1)
    meta = first.get("page") or {}
    total = int(meta.get("totalElements") or 0)
    pages = max(1, int(meta.get("totalPages") or 1))
    sats = list((first.get("_embedded") or {}).get("satellites") or [])
    failed = []
    if pages > 1:
        with cf.ThreadPoolExecutor(max_workers=_PAGE_WORKERS) as ex:
            futures = {ex.submit(_fetch_page, p): p for p in range(2, pages + 1)}
            for fut in cf.as_completed(futures):
                try:
                    d = fut.result()
                except Exception:
                    failed.append(futures[fut])
                    continue
                sats.extend((d.get("_embedded") or {}).get("satellites") or [])
    cat = {"satellites": sats, "total": total, "pages": pages,
           "failed_pages": sorted(failed)}
    if not cat["failed_pages"]:
        _disk_write(cat)
    return cat


def _launch_sort_key(s: dict) -> int:
    """打ち上げ日の降順ソート用キー（'27 Jun 2023' / '≥2026' / '?' を扱う）。"""
    raw = str(s.get("launch_date") or "").strip()
    try:
        return datetime.datetime.strptime(raw, "%d %b %Y").toordinal()
    except ValueError:
        m = re.search(r"(\d{4})", raw)
        return int(m.group(1)) * 366 if m else 0


def match_score(s: dict, q_terms) -> Optional[int]:
    """query の一致度（0〜100）を返す。一致しなければ None。

    部分一致だけでは「Meteorological」に "meteor" が入る DMSP 等が上位に来て、
    本来の Meteor-M が埋もれる（実測 92件中）。そこで

    100 acronym 完全一致 / 90 slug 完全一致 / 80 acronym 前方一致
     70 acronym 部分 / 65 slug 前方 / 60 slug 部分
     50 名称の**語境界**一致（"meteor" が "Meteor-M" に当たる）
     10 名称の途中に含まれるだけ（"meteor" が "Meteorological" に含まれる等）

    と段階を付け、10（弱い一致）は件数を分けて表示する。利用者の入力は略称
    （Meteor-M / FY-3 / INSAT-3DR 等）であることが多いので acronym を最優先する。
    """
    if not q_terms:
        return 0
    acronym = str(s.get("acronym") or "").lower()
    fullname = str(s.get("fullname") or "").lower()
    slug = str(s.get("slug") or "").lower()
    best = None
    for term in q_terms:
        if not term:
            continue
        if acronym == term:
            score = 100
        elif slug == term:
            score = 90
        elif acronym.startswith(term):
            score = 80
        elif term in acronym:
            score = 70
        elif slug.startswith(term):
            score = 65
        elif term in slug:
            score = 60
        elif re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", fullname):
            score = 50
        elif term in fullname:
            score = 10
        else:
            score = None
        if score is not None:
            best = score if best is None else max(best, score)
    return best


@ttl_cache(TTL_DAILY, maxsize=64)
def satellite_status(query: Optional[str] = None, agency: Optional[str] = None,
                     limit: int = 10) -> CallToolResult:
    """世界気象機関（WMO）OSCARの衛星カタログから、気象・地球観測衛星の運用ステータスを返す。

    ロシアのMeteor-M・Resurs・Kanopus等、世界中の気象衛星の運用状況・軌道・打ち上げ日を確認できる。
    例:「ロシアの気象衛星」「Meteor-Mの運用状況」「世界の気象衛星一覧」
    content に表示用サマリ、structuredContent に JSON を返す。

    カタログ**全件（約1,000件）を走査**し、query は一致度順（acronym の完全/前方一致 →
    名称の語境界一致 → 名称の部分一致のみ）に並べて返す。`query="meteor"` のような入力でも
    Meteor-M が「Meteorological」を含む他衛星に埋もれない。一致件数の内訳
    （acronym・語境界 / 部分一致のみ）と、取得できなかったページ数も数値で返す。

    初回の呼び出しは全件取得のため40秒ほどかかります（1ページ30件固定・約4.5秒 ×
    35ページ）。取得したカタログはディスクにも保存するため、以後はプロセスを
    再起動しても即時に応答します（24時間で更新）。

    Args:
        query: 検索語（衛星名・略称）。**和名は英語名に自動展開**する
            （"ひまわり"→himawari, "だいち"→alos, "宇宙ステーション"→iss）。
            例 "meteor", "resurs", "goes", "himawari", "だいち"。
        agency: 機関名（例 "Roscosmos", "NOAA", "EUMETSAT", "JAXA"）。
        limit: 返す件数（既定 10、最大 20）。
    """
    limit = as_int(limit, 10, 1, 20)
    query_l = (query or "").strip().lower()
    # 和名（ひまわり / だいち 等）は英語名へ展開してから照合する（OSCAR は英語のみ）
    q_terms = [t.lower() for t in expand_terms(query)] if query else []
    agency_l = (agency or "").strip().lower()

    try:
        cat = _catalogue()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"WMO OSCAR への接続に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "space.oscar.wmo.int"},
        )

    sats_all = cat["satellites"]           # 読み取り専用（キャッシュを共有している）
    total = cat["total"]
    failed_pages = cat["failed_pages"]
    scan_note = ""
    if failed_pages:
        shown_pages = ",".join(str(p) for p in failed_pages[:8])
        if len(failed_pages) > 8:
            shown_pages += "…"
        scan_note = " ／ ⚠️ ページ {} の取得に失敗（その範囲は走査できていません）".format(shown_pages)

    scored = []
    for s in sats_all:
        if agency_l and agency_l not in str(s.get("space_agency") or "").lower():
            continue
        score = match_score(s, q_terms)
        if score is None:
            continue
        scored.append((score, s))

    matched = len(scored)
    strong = sum(1 for sc, _ in scored if sc >= 50)
    weak_only = matched - strong

    if not scored:
        return CallToolResult(
            content=[TextContent(type="text", text=(
                f"条件（query={query}, agency={agency}）に一致する衛星がカタログ"
                f"（{len(sats_all)}/{total} 件）に見つかりませんでした。" + scan_note + "\n"
                "和名→英語名の展開: " + (", ".join(q_terms) if q_terms else "（指定なし）")
                + "\nヒント: 英語の略称（himawari / alos / gcom / goes / meteor 等）や "
                  "agency=JAXA, NOAA, EUMETSAT, Roscosmos で絞ってみてください。"))],
            structuredContent={"query": query, "agency": agency, "total": total,
                               "scanned": len(sats_all), "failed_pages": failed_pages,
                               "query_terms": q_terms, "results": []},
        )

    # 一致度 → 運用状態 → 打ち上げ日の新しい順
    scored.sort(key=lambda x: (-x[0], _STATUS_RANK.get(str(x[1].get("status") or "").lower(), 5),
                               -_launch_sort_key(x[1])))
    records = []
    for score, s in scored[:limit]:
        status = s.get("status") or ""
        records.append({
            "id": s.get("id"),
            "slug": s.get("slug"),
            "acronym": s.get("acronym"),
            "fullname": s.get("fullname"),
            "agency": s.get("space_agency"),
            "status": status,
            "status_ja": _STATUS.get(status.lower(), status),
            "launch_date": s.get("launch_date"),
            "orbit": s.get("orbit"),
            "altitude_km": s.get("Altitude"),
            "end_of_life": s.get("EoL"),
            "match_score": score,
        })

    lines = [f"🛰 WMO OSCAR の気象・地球観測衛星（カタログ全 {total} 件を全件走査／"
             f"{matched} 件一致〔acronym・語境界 {strong} 件・部分一致のみ {weak_only} 件〕、"
             f"先頭 {len(records)} 件）: 出典 WMO公式{scan_note}"]
    if q_terms and query_l not in q_terms:
        lines.append(f"_※ 和名「{query}」を英語名（{' / '.join(q_terms)}）に展開して検索しました_")
    if weak_only:
        lines.append("_※ 名称の途中に含まれるだけの一致（例: \"meteor\" が \"Meteorological\" に"
                     f"含まれる）が {weak_only} 件あり、低順位に置いています_")
    for s in records:
        mark = {"運用中": "🟢", "計画中": "🔵", "延長運用": "🟡", "退役": "🔴", "故障": "⛔"}.get(s["status_ja"], "•")
        lines.append(f"- {mark} **{s['acronym']}**（{s['fullname']}）  [{s['agency'] or '?'}]")
        lines.append(f"   状態: {s['status_ja']} ・ 打ち上げ: {s['launch_date'] or '?'}")
        if s.get("orbit"):
            lines.append(f"   軌道: {s['orbit']}")
        if s.get("altitude_km"):
            lines.append(f"   高度: {s['altitude_km']}")
    lines.append("出典: space.oscar.wmo.int（WMO OSCAR/Space 公式カタログ）")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": query, "agency": agency, "total": total,
                           "scanned": len(sats_all), "failed_pages": failed_pages,
                           "matched": matched, "strong_matches": strong,
                           "weak_only_matches": weak_only, "query_terms": q_terms,
                           "shown": len(records), "results": records},
    )

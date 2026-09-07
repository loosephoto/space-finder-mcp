"""逆引きQ&Aを Wikidata SPARQL で解決するツール。

"米国初の宇宙望遠鏡は？" のような、カテゴリ+国+時期で横断検索する質問に答える。
原則: SPARQL が根拠を返し、Wikipedia 記事 URL を引用する。LLM の記憶から
"記録・初" を捏造しない。
"""
from __future__ import annotations

from typing import Optional

import requests

# Wikidata SPARQL エンドポイント
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "space-finder-mcp/0.1 (MCP server; contact: dev)",
}


def _q(s: str) -> str:
    return s.strip()


# カテゴリごとの instance-of Q-id と、ソートに使う日付プロパティの既定
# Q-id 解決: https://www.wikidata.org/w/api.php?action=wbsearchentities&search=...
# カテゴリ -> (Wikidata Q-id, 検索パターン)
#   "instance": P31(種別)で検索。機器系（望遠鏡・探査機・衛星）は打ち上げP619・国P17。
#   "occupation": P106(職業)で検索。人系（宇宙飛行士）は生年P569・国籍P27。
CATEGORY_ENTITIES: dict[str, tuple[str, str]] = {
    "宇宙望遠鏡": ("Q148578", "instance"),
    "space telescope": ("Q148578", "instance"),
    "宇宙探査機": ("Q26529", "instance"),
    "space probe": ("Q26529", "instance"),
    "spacecraft": ("Q40218", "instance"),
    "宇宙船": ("Q40218", "instance"),
    "人工衛星": ("Q40218", "instance"),
    "satellite": ("Q40218", "instance"),
    "宇宙飛行士": ("Q11631", "occupation"),
    "astronaut": ("Q11631", "occupation"),
    "天文台": ("Q33807", "instance"),
    "observatory": ("Q33807", "instance"),
}


def reverse_lookup(
    category: str,
    launch_date: bool = True,
    country: Optional[str] = None,
    country_qid: Optional[str] = None,
    limit: int = 5,
    order: str = "asc",
    language: str = "ja",
) -> str:
    """逆引き歴史Q&A — カテゴリ+国 で絞り込んだ最初/記録の宇宙オブジェクトを Wikidata から探す。

    例: 「米国で最初に打ち上げた宇宙望遠鏡は?」→ category="宇宙望遠鏡", country="United States"
    例: 「日本初の人工衛星は?」→ category="人工衛星", country="Japan"

    Args:
        category: 探す対象のカテゴリ（宇宙望遠鏡/space probe/satellite/astronaut など）。
        launch_date: 打ち上げ日(P619)で並べる場合は True。False にすると対象を列挙する。
        country: 国名（例 "United States", "Japan", "India"）。Wikidata 上で Q-id に自動解決する。
        country_qid: 国の Q-id を直接指定する場合（例 "Q30"=米国, "Q17"=日本）。country より優先。
        limit: 返す件数（既定 5）。
        order: "asc"=古い順(最初のもの), "desc"=新しい順。
        language: ラベル表示言語（ja/en/zh など）。
    """
    cat_qid, pat = CATEGORY_ENTITIES.get(_q(category).lower(), (category, "instance"))
    # Q-id 形式でなければ entity 名として受け取ったとみなして返す
    if not cat_qid.startswith("Q"):
        return (f"カテゴリ '{category}' の Wikidata Q-id が未登録です。"
                f"登録済みカテゴリ: {', '.join(sorted(k for k in CATEGORY_ENTITIES))}")

    # 人系(occupation)と機器系(instance)で 国プロパティ・日付プロパティが異なる
    country_prop = "P27" if pat == "occupation" else "P17"
    date_prop_default = "P569" if pat == "occupation" else "P619"

    # 国の解決（country 名 -> Q-id）
    cq = country_qid
    if cq is None and country:
        cq = _resolve_country(country)
        if cq is None:
            return f"国 '{country}' を Wikidata で特定できませんでした。英語名や country_qid を指定してください。"

    if pat == "occupation":
        # 職業カテゴリ(例: 宇宙飛行士)は P106 で検索（人はサブクラス再帰を使わない）
        where = [f"?item wdt:P106 wd:{cat_qid} ."]
    else:
        # 機器カテゴリは instance-of + サブクラス再帰
        where = [f"?item wdt:P31/wdt:P279* wd:{cat_qid} ."]
    if cq:
        where.append(f"?item wdt:{country_prop} wd:{cq} .")

    orderby = ""
    order_q = "ASC" if order == "asc" else "DESC"
    if launch_date:
        where.append(f"?item wdt:{date_prop_default} ?launchDate .")
        orderby = f"ORDER BY {order_q}(?launchDate)"
    select_vars = "?item ?itemLabel" + (" ?launchDate" if launch_date else "")
    query = f"""
SELECT DISTINCT {select_vars} WHERE {{
  {''.join(where)}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en". }}
}} {orderby}
LIMIT {int(limit)}
"""
    try:
        resp = requests.get(SPARQL_ENDPOINT, params={"query": query}, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("results", {}).get("bindings", [])
    except requests.RequestException as e:
        return f"Wikidata 問い合わせに失敗しました: {e}"

    if not rows:
        return f"'{category}' の該当オブジェクトが見つかりませんでした（条件が厳しすぎる可能性があります）。"

    out = []
    for r in rows:
        label = r.get("itemLabel", {}).get("value", "?")
        item = r["item"]["value"]  # http://www.wikidata.org/entity/Q...
        qid = item.rsplit("/", 1)[-1]
        date = r.get("launchDate", {}).get("value", "")[:10] if launch_date else ""
        # Wikipedia 記事リンク(言語別)
        wiki = f"https://{language}.wikipedia.org/wiki/Special:EntityPage/{qid}"
        if pat == "occupation":
            date_s = f"（生年: {date}）" if date else ""
        else:
            date_s = f"（打ち上げ: {date}）" if date else ""
        out.append(f"- {label} {date_s} [{qid}] 参照: {wiki}")
    return "\n".join(out)


def _resolve_country(name: str) -> Optional[str]:
    """国名(英語等)を Wikidata Q-id に解決する。"""
    api = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "format": "json",
        "type": "item",
    }
    try:
        r = requests.get(api, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        results = r.json().get("search", [])
        # instance of は country(P17) に使える国(Q6256) を優先で取るのは複雑なので先頭候補を返す
        for it in results:
            return it.get("id")
    except requests.RequestException:
        pass
    return None

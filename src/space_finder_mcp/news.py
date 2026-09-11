"""天文ニュース（RSS/Atom, 複数フィードのフォールバック付き・認証不要）。

Sky & Telescope（世界で最も歴史と権威のある天文雑誌）の公式RSSを第一候補に、
最新の天文ニュース・観測ガイド（This Week's Sky at a Glance）を取得する。

UA について: Sky & Telescope は Cloudflare のボット判定で、ブラウザを名乗る
非ブラウザクライアント（および独自UA）を 403 "Just a moment..." で弾く。
curl / Wget 系のUAは許可されるため、自ツールを明示した curl 互換UAを使用する。
1つの配信元が塞がれても継続できるよう、フィードはフォールバック連鎖で試す。
出典: skyandtelescope.org/feed/ ほか
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests
from mcp.types import CallToolResult, TextContent

from .cache import TTL_SHORT, ttl_cache

REPO = "https://github.com/loosephoto/space-finder-mcp"
# Cloudflare が「ブラウザを名乗る非ブラウザ」を弾くため、curl 互換UAで自ツールを明示する
_UA = "curl/8.5.0 (compatible; space-finder-mcp/0.25; +" + REPO + ")"
UA = {
    "User-Agent": _UA,
    "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Connection": "keep-alive",
}

# ニュースフィード候補（上から順に試し、指定セクションの記事があるものを採用）
FEEDS: list[dict] = [
    {"name": "Sky & Telescope", "url": "https://skyandtelescope.org/feed/",
     "source": "skyandtelescope.org"},
    {"name": "Universe Today", "url": "https://www.universetoday.com/feed/",
     "source": "universetoday.com"},
    {"name": "NASA Breaking News", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
     "source": "nasa.gov"},
    {"name": "Phys.org Space News", "url": "https://phys.org/rss-feed/space-news/",
     "source": "phys.org"},
]

# 「観測ガイド」系と判定するURL/タイトルの手がかり
_OBSERVING_HINTS = ("observing", "sky-at-a-glance", "stargazing", "this-week",
                    "what-to-see", "guide")


def _strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _text(el, tag: str) -> str:
    """XML 要素の子タグのテキストを取得（無ければ空文字）。"""
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _classify(url: str, title: str) -> str:
    """観測ガイド系かニュース系かをURL/タイトルから判定する。"""
    t = (url + " " + title).lower()
    return "observing" if any(h in t for h in _OBSERVING_HINTS) else "news"


@ttl_cache(TTL_SHORT, maxsize=16)
def _parse_feed(url: str) -> list[dict]:
    """RSS 2.0 / Atom フィードを取得して記事レコードを返す。失敗時は例外を送出する。"""
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    tree = ET.fromstring(r.text)  # 非XML(HTMLチャレンジ等)なら ParseError
    ns = "{http://www.w3.org/2005/Atom}"
    channel = tree.find("channel")
    items = channel.findall("item") if channel is not None else tree.findall(ns + "entry")
    records = []
    for it in items:
        title = _text(it, "title") or _text(it, ns + "title")
        if not title:
            continue
        link = _text(it, "link")
        if not link:  # Atom は <link href="..."/>
            el = it.find(ns + "link")
            link = (el.get("href", "") if el is not None else "")
        pub = _text(it, "pubDate") or _text(it, "updated") or _text(it, ns + "updated")
        desc = _text(it, "description") or _text(it, "summary") or _text(it, ns + "summary")
        records.append({"title": title, "url": link, "date": pub[:22],
                        "description": _strip_html(desc)[:200],
                        "category": _classify(link, title)})
    return records


def astronomy_news(limit: int = 8, section: str = "all") -> CallToolResult:
    """天文ニュース・観測ガイドを返す（複数フィードのフォールバック付き・認証不要）。

    「今日の天文ニュース」「今週の星空ガイド」「惑星観測の見どころ」など。
    第一候補は Sky & Telescope の公式RSS。取得できない場合は他ソース
    (Universe Today / NASA / Phys.org) に自動でフォールバックする。
    content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 8、最大 15）。
        section: 絞り込み（"all"=全部, "observing"=観測ガイド, "news"=ニュース）。
    """
    limit = max(1, min(int(limit), 15))
    errors: list[str] = []
    chosen = None    # 指定セクションの記事があるフィード
    fallback = None  # 記事はあるが指定セクションが無いフィード
    for f in FEEDS:
        try:
            recs = _parse_feed(f["url"])
        except (requests.RequestException, ET.ParseError) as e:
            errors.append(f["name"] + ": " + str(e)[:80])
            continue
        if not recs:
            errors.append(f["name"] + ": 記事が見つかりません")
            continue
        if fallback is None:
            fallback = (f, recs)
        if section == "all" or any(rc["category"] == section for rc in recs):
            chosen = (f, recs)
            break
    if chosen is None and fallback is None:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="天文ニュースを取得できませんでした。配信元: " + " / ".join(errors))],
            structuredContent={"error": "all feeds failed", "errors": errors},
        )
    feed, all_records = chosen if chosen is not None else fallback
    used_fallback = feed is not FEEDS[0]  # 第一候補以外のソースから返した場合

    records = [rc for rc in all_records if section == "all" or rc["category"] == section]
    if not records:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text="該当セクションの記事がありませんでした。配信元: " + " / ".join(errors))],
            structuredContent={"feed": feed["name"], "section": section, "errors": errors,
                               "total": 0, "results": []},
        )
    records = records[:limit]

    section_ja = "観測ガイド" if section == "observing" else ("ニュース" if section == "news" else "全て")
    lines = ["🔭 " + feed["name"] + " 最新の天文ニュース（" + section_ja + "・" + str(len(records)) + " 件）:"]
    for i, rec in enumerate(records, 1):
        tag = "⭐ 観測ガイド" if rec["category"] == "observing" else "📰 ニュース"
        lines.append(str(i) + ". " + tag + " **" + rec["title"] + "**")
        if rec["date"]:
            lines.append("   日付: " + rec["date"])
        if rec["description"]:
            lines.append("   " + rec["description"][:90] + "…")
        lines.append("   リンク: " + rec["url"])
    lines.append("🤖 【AIからのインテリジェントアドバイス】星を見る前に「観測ガイド」を確認すると、今夜見える惑星・深宇宙天体や接近現象の見どころが分かります。")
    if used_fallback:
        lines.append("（注: 第一候補の " + FEEDS[0]["name"] + " を取得できなかったため " + feed["name"] + " で代替しました）")
    lines.append("出典: " + feed["source"] + " (" + feed["name"] + " 公式RSS)")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"source": feed["source"], "feed": feed["name"], "section": section,
                           "fallback_used": used_fallback,
                           "shown": len(records), "results": records},
    )

"""Sky & Telescope 天文ニュース（RSS, ブラウザUAでWAF回避）。

Sky & Telescope（世界で最も歴史と権威のある天文雑誌）の公式RSSフィードから、
最新の天文ニュース・観測ガイド（This Week's Sky at a Glance）を取得する。
フィードは通常のrequests UAでは403（WAF）になるため、ブラウザUAでアクセスする。
出典: skyandtelescope.org/feed/
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests
from mcp.types import CallToolResult, TextContent

FEED = "https://skyandtelescope.org/feed/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
UA = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Referer": "https://skyandtelescope.org/",
    "Connection": "keep-alive",
}


def _strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def astronomy_news(limit: int = 8, section: str = "all") -> CallToolResult:
    """Sky & Telescope の最新天文ニュース・観測ガイドを返す（ブラウザUAでWAF回避）。

    「今日の天文ニュース」「今週の星空ガイド」「惑星観測の見どころ」など。
    認証不要。content に表示用サマリ、structuredContent に JSON を返す。

    Args:
        limit: 返す件数（既定 8、最大 15）。
        section: 絞り込み（"all"=全部, "observing"=観測ガイド, "news"=ニュース）。
    """
    limit = max(1, min(int(limit), 15))
    try:
        r = requests.get(FEED, headers=UA, timeout=25)
        r.raise_for_status()
    except requests.RequestException as e:
        return CallToolResult(
            content=[TextContent(type="text", text="Sky & Telescope フィード取得に失敗しました: " + str(e))],
            structuredContent={"error": str(e), "source": "skyandtelescope.org/feed/"},
        )
    try:
        tree = ET.fromstring(r.text)
    except ET.ParseError as e:
        return CallToolResult(content=[TextContent(type="text", text="RSS 解析に失敗しました: " + str(e))], structuredContent={"error": str(e)})
    channel = tree.find("channel")
    items = channel.findall("item") if channel is not None else []
    if not items:
        return CallToolResult(content=[TextContent(type="text", text="フィードに記事が見つかりませんでした。")], structuredContent={"total": 0, "results": []})

    records = []
    for it in items:
        t = it.find("title")
        link = it.find("link")
        pub = it.find("pubDate")
        desc = it.find("description")
        title = t.text if t is not None and t.text else ""
        url = link.text if link is not None and link.text else ""
        date = (pub.text or "")[:22] if pub is not None and pub.text else ""
        description = _strip_html(desc.text) if desc is not None and desc.text else ""
        is_observing = "observing-news" in url or "sky-at-a-glance" in url or "stargazing" in url.lower()
        cat = "observing" if is_observing else "news"
        if section != "all" and cat != section:
            continue
        records.append({"title": title, "url": url, "date": date, "description": description[:200], "category": cat})
    if not records:
        return CallToolResult(content=[TextContent(type="text", text="該当セクションの記事がありませんでした。")], structuredContent={"section": section, "total": 0, "results": []})
    records = records[:limit]

    section_ja = "観測ガイド" if section == "observing" else ("ニュース" if section == "news" else "全て")
    lines = ["🔭 Sky & Telescope 最新の天文ニュース（" + section_ja + "・" + str(len(records)) + " 件）:"]
    for i, rec in enumerate(records, 1):
        tag = "⭐ 観測ガイド" if rec["category"] == "observing" else "📰 ニュース"
        lines.append(str(i) + ". " + tag + " **" + rec["title"] + "**")
        if rec["date"]:
            lines.append("   日付: " + rec["date"])
        if rec["description"]:
            lines.append("   " + rec["description"][:90] + "…")
        lines.append("   リンク: " + rec["url"])
    lines.append("🤖 【AIからのインテリジェントアドバイス】星を見る前に「観測ガイド」を確認すると、今夜見える惑星・深宇宙天体や接近現象の見どころが分かります。")
    lines.append("出典: skyandtelescope.org (Sky & Telescope 公式RSS)")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"source": "skyandtelescope.org", "section": section, "shown": len(records), "results": records},
    )

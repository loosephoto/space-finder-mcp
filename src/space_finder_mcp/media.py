"""NASA Image and Video Library の検索・表示 (images-api.nasa.gov, 認証不要)。

tokyo-transit-mcp と同じ応答方式:
- content に「人間向け表示コンテンツ」を返す（画像は ImageContent としてインライン表示、
  テキストは表示用サマリ）。
- structuredContent に「LLM向けの純粋JSON」を返す（検索結果を構造化して渡す）。
これによりホストLLMが情報を失わず、チャットには画像+サマリが出る。
"""
from __future__ import annotations

from typing import Optional
import base64

import requests
from mcp.types import CallToolResult, ImageContent, TextContent

# NASA Sounds from Beyond / 各ミッションの「宇宙の音」短尺キュレーション
# (NASA公式が配布する効果音。ストリーミング/ダウンロード用の直接URL)。
# keywords で検索マッチさせる。source は出典ページ。
_SOUND_EFFECTS: list[dict] = [
    {
        "title": "Juno: Ganymede Flyby (電波音)",
        "keywords": ["juno", "ganymede", "jupiter", "flyby", "電波", "ガニメデ", "radio", "moon"],
        "url": "https://www.nasa.gov/wp-content/uploads/2024/05/e2-wave-ganymede-flyby-compressed.wav",
        "mime": "audio/wav", "credit": "NASA/JPL-Caltech/SwRI/Univ of Iowa",
    },
    {
        "title": "Saturn: Cassini Radio Emissions #1 (土星の電波音)",
        "keywords": ["saturn", "cassini", "radio", "土星", "カッシーニ", "電波"],
        "url": "https://soundcloud.com/nasa/cassini-saturn-radio-emissions-1",
        "mime": "audio", "credit": "NASA/JPL",
    },
    {
        "title": "Sputnik: Beep (スプートニクのビープ音)",
        "keywords": ["sputnik", "beep", "スプートニク", "衛星", "satellite"],
        "url": "https://soundcloud.com/nasa/sputnik-beep",
        "mime": "audio", "credit": "NASA",
    },
    {
        "title": "Voyager: Lightning on Jupiter (木星の雷)",
        "keywords": ["voyager", "jupiter", "lightning", "ボイジャー", "木星", "雷"],
        "url": "https://soundcloud.com/nasa/voyager-lightning-on-jupiter",
        "mime": "audio", "credit": "NASA/JPL",
    },
    {
        "title": "Cassini: Enceladus Sound (エンケラドゥスの音)",
        "keywords": ["cassini", "enceladus", "saturn", "エンケラドゥス", "土星"],
        "url": "https://soundcloud.com/nasa/cassini-enceladus-sound",
        "mime": "audio", "credit": "NASA/ESA/ASI",
    },
    {
        "title": "Quindar: Sound #1 (アポロ時代のキンダー音)",
        "keywords": ["quindar", "apollo", "キンダー", "アポロ", "通信"],
        "url": "https://soundcloud.com/nasa/quindar-sound-1",
        "mime": "audio", "credit": "NASA",
    },
    {
        "title": "Chorus Radio Waves within Earth's Atmosphere (地球大気のコーラス電波)",
        "keywords": ["chorus", "earth", "radio", "コーラス", "地球", "大気", "radio wave"],
        "url": "https://soundcloud.com/nasa/chorus-radio-waves-within-earths-atmosphere",
        "mime": "audio", "credit": "NASA",
    },
    {
        "title": "Artemis I: SLS Liftoff (月ロケット打ち上げ音)",
        "keywords": ["artemis", "launch", "rocket", "打ち上げ", "liftoff", "sls", "ロケット"],
        "url": "https://www.nasa.gov/wp-content/uploads/2023/02/liftoff1.mp3",
        "mime": "audio/mpeg", "credit": "NASA",
    },
]

# sound effect 検索に使う出典ラベル
_SOUND_FX_SOURCE = "NASA Sounds from Beyond (nasa.gov/sounds-from-beyond) + NASA SoundCloud"

IMAGES_API = "https://images-api.nasa.gov"
UA = {"User-Agent": "space-finder-mcp/0.1 (MCP media search)"}


def _search(q: str, media_type: str, limit: int) -> dict:
    """NASA Image & Video Library を検索する。失敗時は例外を送出する（呼び出し側で処理）。

    生の requests.RequestException / JSON 解析失敗をそのまま通すため、必ず
    _search_or_error() か try/except で受け止めること
    （素通しすると MCP 呼び出しごと例外終了し、structuredContent も返らない）。
    """
    r = requests.get(
        f"{IMAGES_API}/search",
        params={"q": q, "media_type": media_type, "page_size": min(limit, 100)},
        headers=UA,
        timeout=25,
    )
    r.raise_for_status()
    return r.json()


def _search_or_error(q: str, media_type: str, limit: int):
    """検索を実行し、失敗時は (エラーCallToolResult, None)、成功時は (None, dict) を返す。

    他ツールと同じく「例外を漏らさず CallToolResult で返す」方針を守るためのラッパ。
    """
    try:
        return None, _search(q, media_type, limit)
    except (requests.RequestException, ValueError) as e:
        return CallToolResult(
            content=[TextContent(type="text",
                                 text=f"NASA Image & Video Library の検索に失敗しました: {e}")],
            structuredContent={"error": str(e), "source": "images-api.nasa.gov"},
        ), None


def _asset_hrefs(nasa_id: str) -> list[str]:
    """nasa_id の実ファイルURL(群)を取得する。"""
    try:
        r = requests.get(f"{IMAGES_API}/asset/{nasa_id}", headers=UA, timeout=20)
        r.raise_for_status()
        return [i.get("href") for i in r.json().get("collection", {}).get("items", [])
                if isinstance(i, dict) and isinstance(i.get("href"), str)]
    except requests.RequestException:
        return []


def _fetch_image_bytes(url: str, max_bytes: int = 3_500_000) -> Optional[bytes]:
    """画像URLを取得して bytes で返す。大きすぎる場合は None。"""
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        data = r.content
        if len(data) > max_bytes:
            return None
        return data
    except requests.RequestException:
        return None


def _first_image_url(item: dict, nasa_id: str) -> Optional[str]:
    """item から表示用画像URL（できるだけ大きく、大きすぎないもの）を選ぶ。"""
    urls = [l.get("href", "") for l in item.get("links", []) if isinstance(l, dict)]
    # 大きい→小さいの順。~orig は避け、~medium / ~large / ~small の順で選ぶ
    for size in ("~large.jpg", "~medium.jpg", "~small.jpg", "~thumb.jpg"):
        for u in urls:
            if size in u:
                return u
    # フォールバック: asset から最初の画像
    for u in _asset_hrefs(nasa_id):
        if u.lower().endswith((".jpg", ".jpeg", ".png")):
            return u
    return None


def _image_content(url: str, alt: str) -> Optional[ImageContent]:
    """画像URLを base64 化して ImageContent にする。失敗時 None。"""
    data = _fetch_image_bytes(url)
    if not data:
        return None
    mime = "image/jpeg"
    if url.lower().endswith(".png"):
        mime = "image/png"
    return ImageContent(
        type="image",
        data=base64.b64encode(data).decode("ascii"),
        mimeType=mime,
        altText=alt,
    )


# ---------- 画像検索 ----------
def search_space_images(query: str, limit: int = 3, show_inline: bool = True) -> CallToolResult:
    """惑星・人工衛星・宇宙ミッションの画像を NASA Image & Video Library から検索し表示する。

    LLM向けに structuredContent へJSON（title/date/nasa_id/image_url など）を返し、
    content にはチャット表示用のサマリ+インライン画像を返す。

    例: 「火星の画像を見せて」「ハッブルの写真」「アポロの画像」

    Args:
        query: 検索語（mars, jupiter, hubble, apollo など英語が確実）。
        limit: 返す画像件数（既定 3、最大 10）。
        show_inline: 画像をチャットにインライン表示するか（既定 True）。
    """
    limit = max(1, min(int(limit), 10))
    err, d = _search_or_error(query, "image", limit)
    if err is not None:
        return err
    items = d.get("collection", {}).get("items", [])
    total = d.get("collection", {}).get("metadata", {}).get("total_hits", 0)

    records = []
    content_blocks = []
    for it in items:
        data = it.get("data", [{}])[0]
        rec = {
            "title": data.get("title", ""),
            "nasa_id": data.get("nasa_id", ""),
            "date": data.get("date_created", "")[:10],
            "description": (data.get("description") or "")[:200],
            "image_url": _first_image_url(it, data.get("nasa_id", "")),
            "keywords": data.get("keywords", [])[:8],
        }
        records.append(rec)

    if not records:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{query}' の画像は見つかりませんでした。英語の検索語をお試しください。")],
            structuredContent={"query": query, "total": 0, "results": []},
        )

    # 表示用テキスト（サマリ）
    lines = [f"'{query}' のNASA画像（全 {total} 件中、先頭 {len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. **{r['title']}** ({r['date']})  NASA_ID: {r['nasa_id']}")
        if r.get("description"):
            lines.append(f"   {r['description'][:80]}")
    lines.append("出典: NASA Image and Video Library (images.nasa.gov) ／ このMCPは画像をJSON+インライン表示で返します。")
    content_blocks.append(TextContent(type="text", text="\n".join(lines)))

    # インライン画像（最大1枚=先頭、または show_inline 時は全件試みる）
    if show_inline:
        img_count = 0
        for r in records:
            if r.get("image_url"):
                ic = _image_content(r["image_url"], r.get("title", "NASA image"))
                if ic:
                    content_blocks.append(ic)
                    img_count += 1
        # 画像URL情報は構造化に残す（contentには出さない）

    return CallToolResult(content=content_blocks, structuredContent={
        "query": query,
        "total": total,
        "shown": len(records),
        "inline_images": img_count if show_inline else 0,
        "results": records,
    })


# ---------- 音声検索 ----------
def _match_keywords(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def _list_sound_effects(query: str, limit: int) -> list[dict]:
    """キュレーション済みの短尺『宇宙の音』から、クエリに合うものを返す。"""
    out = []
    for se in _SOUND_EFFECTS:
        if _match_keywords(se["title"] + " " + " ".join(se["keywords"]), [query]):
            out.append(se)
    return out[:limit]


def search_space_audio(query: str, limit: int = 3, kind: str = "auto") -> CallToolResult:
    """宇宙関連の音声を検索し、再生可能な音声URLを返す。

    content には表示用サマリ、structuredContent にはJSON（タイトル/再生URL/種別/尺など）を返す。

    「宇宙の音・惑星の音・打ち上げの効果音」のような短い音声を探すなら kind="sound_effect"、
    「ポッドキャスト・解説・飛行士インタビュー」のような長い音声なら kind="podcast" を指定。
    既定 kind="auto" は両方を探す。

    例:
      - 「スプートニクの音を聞きたい」→ query="sputnik", kind="sound_effect"
      - 「アポロのポッドキャスト」→ query="apollo", kind="podcast"
      - 「火星の音」→ query="mars", kind="auto"

    Args:
        query: 検索語（juno, saturn, sputnik, launch, apollo など）。
        limit: 返す件数（既定 3、最大 10）。
        kind: "auto"(両方) / "podcast"(NASA Image Library の長尺トーク・解説) /
              "sound_effect"(短い宇宙の音・効果音)。
    """
    limit = max(1, min(int(limit), 10))
    q = (query or "").strip()
    kind = (kind or "auto").lower()

    want_podcast = kind in ("auto", "podcast")
    want_fx = kind in ("auto", "sound_effect")

    records = []
    seen_urls = set()

    # 1) 長尺ポッドキャスト（NASA Image & Video Library）
    if want_podcast:
        try:
            d = _search(q, "audio", limit * 3)
            for it in d.get("collection", {}).get("items", []):
                data = it.get("data", [{}])[0]
                nid = data.get("nasa_id", "")
                hrefs = _asset_hrefs(nid)
                audio_url = next((u for u in hrefs if u.lower().endswith((".mp3", ".m4a", ".wav"))),
                                 hrefs[0] if hrefs else None)
                if not audio_url or audio_url in seen_urls:
                    continue
                seen_urls.add(audio_url)
                records.append({
                    "title": data.get("title", ""),
                    "nasa_id": nid,
                    "date": data.get("date_created", "")[:10],
                    "description": (data.get("description") or "")[:200],
                    "audio_url": audio_url,
                    "kind": "podcast",
                    "estimated": "long (podcast, minutes-tens of MB)",
                })
                if len(records) >= limit:
                    break
        except (requests.RequestException, ValueError):
            # 検索失敗時は下のキュレーション済み「宇宙の音」にフォールバックする
            pass

    # 2) 短尺の宇宙の音（キュレーション）
    if want_fx:
        for se in _list_sound_effects(q, limit):
            if se["url"] in seen_urls:
                continue
            seen_urls.add(se["url"])
            records.append({
                "title": se["title"],
                "nasa_id": "",
                "date": "",
                "description": se.get("credit", ""),
                "audio_url": se["url"],
                "mime": se.get("mime", "audio"),
                "kind": "sound_effect",
                "estimated": "short (seconds to ~1 min, <1MB if mp3)",
            })
            if len(records) >= limit:
                break

    if not records:
        hint = "英語の検索語（juno, saturn, sputnik, launch 等）をお試しください。"
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{q}' の音声は見つかりませんでした。{hint}")],
            structuredContent={"query": q, "kind": kind, "total": 0, "results": []},
        )

    lines = [f"'{q}' のNASA音声（{len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        k = "📻 ポッドキャスト" if r["kind"] == "podcast" else "🔊 宇宙の音"
        lines.append(f"{i}. {k} **{r['title']}**")
        if r.get("description"):
            lines.append(f"   説明: {r['description'][:60]}")
        if r.get("audio_url"):
            lines.append(f"   再生: {r['audio_url']}")
    lines.append(f"出典: {_SOUND_FX_SOURCE} / NASA Image and Video Library")
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={"query": q, "kind": kind, "shown": len(records), "results": records},
    )


# ---------- 動画（映像）検索 ----------
def _pick_video_urls(nasa_id: str) -> dict:
    """nasa_id の video asset から再生用URL群を選ぶ。{preview, medium, mobile, orig, poster} を返す。"""
    hrefs = _asset_hrefs(nasa_id)
    out = {"preview": None, "medium": None, "mobile": None, "orig": None, "poster": None, "subtitle": None}
    for u in hrefs:
        l = u.lower()
        if "~preview.mp4" in l:
            out["preview"] = u
        elif "~medium.mp4" in l:
            out["medium"] = u
        elif "~mobile.mp4" in l:
            out["mobile"] = u
        elif "~orig.mov" in l or "~orig.mp4" in l:
            out["orig"] = u
        elif l.endswith(".srt"):
            out["subtitle"] = u
        elif "~small_1.jpg" in l or "~small.jpg" in l:
            out["poster"] = out["poster"] or u
    return out


def _first_poster_url(item: dict) -> Optional[str]:
    for l in item.get("links", []):
        if isinstance(l, dict):
            h = l.get("href", "")
            if h.lower().endswith((".jpg", ".jpeg", ".png")):
                return h
    return None


def search_space_videos(query: str, limit: int = 3, show_poster: bool = True) -> CallToolResult:
    """宇宙関連の動画（打ち上げ・ミッション映像・解説など）を検索し、再生用MP4 URLを返す。

    content には表示用サマリ＋ポスター画像(インライン)を返し、
    structuredContent にはJSON（タイトル/再生URL/解像度別URL/字幕など）を返す。
    動画自体は巨大なのでbase64埋め込みせず、クライアント/LLMがURLから再生する。

    例: 「火星の動画」「ロケット打ち上げ映像」「ハッブルの映像」

    Args:
        query: 検索語（mars, launch, hubble, artemis など英語が確実）。
        limit: 返す動画件数（既定 3、最大 10）。
        show_poster: ポスター画像をチャットにインライン表示するか（既定 True）。
    """
    limit = max(1, min(int(limit), 10))
    err, d = _search_or_error(query, "video", limit)
    if err is not None:
        return err
    items = d.get("collection", {}).get("items", [])
    total = d.get("collection", {}).get("metadata", {}).get("total_hits", 0)

    records = []
    for it in items:
        data = it.get("data", [{}])[0]
        nid = data.get("nasa_id", "")
        urls = _pick_video_urls(nid)
        rec = {
            "title": data.get("title", ""),
            "nasa_id": nid,
            "date": data.get("date_created", "")[:10],
            "description": (data.get("description") or "")[:250],
            "video_url": urls["preview"] or urls["medium"] or urls["mobile"],  # 推奨再生URL
            "urls": {k: v for k, v in urls.items() if k != "poster"},          # 解像度別
            "poster_url": _first_poster_url(it),
            "keywords": data.get("keywords", [])[:8],
        }
        records.append(rec)

    if not records:
        return CallToolResult(
            content=[TextContent(type="text", text=f"'{query}' の動画は見つかりませんでした。英語の検索語をお試しください。")],
            structuredContent={"query": query, "total": 0, "results": []},
        )

    lines = [f"'{query}' のNASA動画（全 {total} 件中、先頭 {len(records)} 件）:"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. **{r['title']}** ({r['date']})  NASA_ID: {r['nasa_id']}")
        if r.get("description"):
            lines.append(f"   {r['description'][:90]}")
        if r.get("video_url"):
            lines.append(f"   再生URL: {r['video_url']}")
    lines.append("出典: NASA Image and Video Library (images.nasa.gov) ／ 動画はURLから再生（base64埋め込みは行わない）。")
    content_blocks = [TextContent(type="text", text="\n".join(lines))]

    # ポスター画像をインライン表示（先頭の何件か）
    if show_poster:
        pcount = 0
        for r in records:
            if r.get("poster_url"):
                ic = _image_content(r["poster_url"], r.get("title", "NASA video poster"))
                if ic:
                    content_blocks.append(ic)
                    pcount += 1
            if pcount >= min(limit, 3):
                break

    return CallToolResult(content=content_blocks, structuredContent={
        "query": query,
        "total": total,
        "shown": len(records),
        "inline_posters": pcount if show_poster else 0,
        "results": records,
    })

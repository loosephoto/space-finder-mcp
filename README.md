# Space Finder MCP 🚀

**宇宙・天文データを横断検索し、画像・動画・音声までチャット上で表示できる** Model Context Protocol (MCP) サーバーです。

惑星・人工衛星・宇宙ミッションのメディア検索、ロケット打ち上げ、小惑星接近情報、「史上初の宇宙望遠鏡は？」といった逆引き歴史Q&Aを、AIエージェント（Claude / Cursor / Hermes 等）から自然言語で呼び出せます。

> 「Space Finder」は**宇宙(Space)に関する情報を探す**MCPです。「物理的な空間＝オフィス・駐車場を探す」同名サービスとは無関係です。

---

## ✨ 特徴

- **画像・動画・音声をそのまま返せる** — 画像はチャット内にインライン表示、音声・動画は再生URLを返却
- **tokyo-transit方式のJSON応答** — 人間向け表示（`content`）とLLM向け純粋JSON（`structuredContent`）を分離し、情報を失わずに構造化データを渡せる
- **認証不要のツールが大半** — APIキーの管理なしですぐ動く（NASAの一部ツールのみ任意キー）
- **引用元を明示** — 科学的な内容には必ずデータソースへのリンクを併記

## 📦 インストール

### 前提
- [uv](https://docs.astral.sh/uv/)（Python 3.11+）

### インストール

```bash
# リポジトリをクローンして依存インストール
git clone https://github.com/loosephoto/space-finder-mcp.git
cd space-finder-mcp
uv sync
```

### 実行

```bash
# MCP サーバー起動（stdio）
uv run space-finder-mcp
```

> ローカルで `uv run` を使うため、`uv` が `PATH` にある必要があります。

### （任意）NASA APIキー

`apod`・`neo_today` は [api.nasa.gov](https://api.nasa.gov) の無料キーを使います。未設定でも `DEMO_KEY` で動作しますが、**レート制限 30 req/hr/IP** と低いため、実用にはキーを推奨します。

```bash
export NASA_API_KEY="your_key_here"
```

---

## 🔌 MCPクライアントへの登録

### Claude Desktop / Cursor / Claude Code（`claude_desktop_config.json` / `mcp.json`）

```json
{
  "mcpServers": {
    "space-finder-mcp": {
      "command": "uv",
      "args": ["run", "--project", "/絶対パス/space-finder-mcp", "space-finder-mcp"],
      "env": { "NASA_API_KEY": "your_key_here" }
    }
  }
}
```

### Hermes Agent

```bash
hermes config set mcp_servers.space-finder-mcp.command uv
hermes config set 'mcp_servers.space-finder-mcp.args' '["run", "--project", "/絶対パス/space-finder-mcp", "space-finder-mcp"]'
# 反映には再起動
```

---

## 🛠️ ツール一覧

登録ツールは **7本**。すべて動作検証済みです。

| ツール | できること | データ源 | 認証 |
|--------|-----------|---------|------|
| `reverse_lookup` | 「史上初の宇宙望遠鏡は？」等をカテゴリ+国+時期から解決 | Wikidata SPARQL | 不要 |
| `upcoming_launches` | 今後のロケット打ち上げ予定（日時・機体・射場・状態） | Launch Library 2 | 不要 |
| `apod` | 今日（指定日）の天文写真 | NASA Open API | キー(任意) |
| `neo_today` | 今日地球に接近する小惑星 | NASA Open API | キー(任意) |
| `search_space_images` | 惑星・衛星の画像検索＋**チャット内インライン表示** | NASA Image & Video Library | 不要 |
| `search_space_audio` | 宇宙音声の検索（`kind`で効果音/ポッドキャスト切替） | NASA / Sounds from Beyond | 不要 |
| `search_space_videos` | 宇宙動画の検索＋再生URL（解像度別・字幕付き） | NASA Image & Video Library | 不要 |

---

## 📖 ツール詳細

### 🔍 `reverse_lookup` — 逆引き歴史Q&A

「初めて」「記録」「〜による発見」など、カテゴリを横断して**最初のもの・記録**を Wikidata の構造化データから解決します。検索対象の種別を自動判別します。

- **機器系**（宇宙望遠鏡・探査機・人工衛星・天文台）: `instance-of + 打ち上げ日 + 国` で検索
- **人系**（宇宙飛行士）: `職業(P106) + 生年 + 国籍` で検索

```text
Q: 米国で最初に打ち上げた宇宙望遠鏡は？
A: → 1966年の OAO 系観測機が最古として返る

Q: 日本の最初の宇宙飛行士は？
A: 秋山豊寛（1942年生・日本人初）
```

**設計方針**: 「記録・初」の答えは LLM の記憶から捏造せず、**Wikidata SPARQL が根拠を返し、Wikipedia 記事 URL を引用**します。

**対応カテゴリ**:

| 日本語 | 英語 | 種別 |
|--------|------|------|
| 宇宙望遠鏡 | space telescope | 機器 |
| 宇宙探査機 | space probe | 機器 |
| 人工衛星 / 宇宙船 | satellite / spacecraft | 機器 |
| 天文台 | observatory | 機器 |
| 宇宙飛行士 | astronaut | 人 |

### 🚀 `upcoming_launches` — ロケット打ち上げ

認証なしの公開API（[Launch Library 2](https://thespacedevs.com/llapi)）から今後の打ち上げを返します。

### 🌌 `apod` / `neo_today` — NASA日次データ

- `apod`: 今日（または指定日）の [Astronomy Picture of the Day](https://apod.nasa.gov)
- `neo_today`: 今日地球に接近する小惑星（直径・接近距離・速度）

キーは**サーバー側でのみ保持**し、クライアントへ晒しません（公開デプロイ時は環境変数・シークレット管理を推奨）。

### 🖼️ `search_space_images` — 画像検索・表示

NASA Image & Video Library から惑星・人工衛星の画像を検索し、**チャットにインライン表示**します。

- `content`: テキストサマリ + base64の画像（`ImageContent`）
- `structuredContent`: `{title, date, nasa_id, image_url, keywords}` のJSON

```text
Q: 木星の画像を見せて → 木星の写真がチャットに表示される
```

### 🔊 `search_space_audio` — 音声検索

宇宙関連の音声を検索します。**`kind` 引数で用途を選べます**。

| kind | 対象 | 例 |
|------|------|-----|
| `sound_effect` | 短い宇宙の音（惑星の電波・Sputnikのビープ・打ち上げ音） | 「スプートニクの音」 |
| `podcast` | 長尺の解説・インタビュー | 「アポロのポッドキャスト」 |
| `auto` | 両方から探す（既定） | |

各結果に再生URL＋尺の目安をJSONで返します。

### 🎬 `search_space_videos` — 動画検索

宇宙動画（打ち上げ・ミッション映像・解説）を検索し、再生用MP4 URLを返します。

- `structuredContent`: `{title, video_url, urls:{preview/medium/mobile/orig/subtitle}, poster_url}` — **解像度別URLと字幕(.srt)を分離**
- `content`: テキストサマリ + ポスター画像（インライン）

> 動画は巨大なため base64 埋め込みは行わず、`video_url` を返してクライアント/LLMが URL から再生します。

---

## 🔐 応答方式（tokyo-transit 方式）

全ツールは **`CallToolResult`** を使い、次の2層で応答します。

- **`content`** — 人間向け表示（テキストサマリ、画像は `ImageContent`）
- **`structuredContent`** — LLM向けの**純粋JSON**（メディアURL・メタデータを構造化）

これにより、ホストLLMが表示テキストを要約しても**元データ（URL等）を失わず**、正確な情報を保持できます。

---

## 🧪 開発・検証

```bash
# 構文チェック
uv run python -m py_compile src/space_finder_mcp/*.py

# MCPエンドツーエンド確認（ツール一覧＋実呼び出し）
uv run python -c "from space_finder_mcp.server import mcp; print(sorted(t.name for t in mcp._tool_manager._tools.values()))"

# Hermes で接続確認
hermes mcp test space-finder-mcp
```

### プロジェクト構成

```
src/space_finder_mcp/
├── __init__.py          # main() → mcp.run()
├── server.py            # FastMCP サーバー定義・7ツール登録
├── wikidata_lookup.py   # reverse_lookup（逆引き歴史Q&A）
├── launch.py            # upcoming_launches（ロケット打ち上げ）
├── nasa.py              # apod / neo_today（NASA日次）
└── media.py             # search_space_images / audio / videos（メディア検索）
```

---

## 📄 ライセンス / 注意

- **ライセンス**: MIT License（本リポジトリの `LICENSE` を参照）
- **データソース**: NASA・NASA Image & Video Library・Launch Library 2・Wikidata(Wikimedia) は、それぞれの利用条件・ライセンスに従います。
- 宇宙データは科学的な内容を含みます。応答時は**引用元（Wikipedia / NASA 等）へのリンクを必ず表示**してください。
- 画像・動画・音声の著作権・クレジット表記は各ソースの指示に従ってください（NASA素材は NASA Media Usage Guidelines を参照）。

---

## 🗺️ ロードマップ

- [x] 逆引き歴史Q&A（Wikidata）
- [x] ロケット打ち上げ・NASA日次
- [x] 画像・音声・動画の検索・表示
- [ ] 地球リアルタイム画像（EPIC/DSCOVR）
- [ ] 惑星の3D地図（NASA Trek WMTS）
- [ ] 多言語（en/zh）応答の全面対応

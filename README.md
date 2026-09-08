# Space Finder MCP 🚀

**宇宙・天文データを横断検索し、画像・動画・音声までチャット上で表示できる** Model Context Protocol (MCP) サーバーです。

惑星・人工衛星・宇宙ミッションのメディア検索、ロケット打ち上げ、小惑星接近情報、「史上初の宇宙望遠鏡は？」といった逆引き歴史Q&Aを、AIエージェント（Claude / Cursor / Hermes 等）から自然言語で呼び出せます。

> 「Space Finder」は**宇宙(Space)に関する情報を探す**MCPです。「物理的な空間＝オフィス・駐車場を探す」同名サービスとは無関係です。

---

## ✨ 特徴

- **画像・動画・音声をそのまま返せる** — 画像はチャット内にインライン表示、音声・動画は再生URLを返却
- **tokyo-transit方式のJSON応答** — 人間向け表示（`content`）とLLM向け純粋JSON（`structuredContent`）を分離し、情報を失わずに構造化データを渡せる
- **認証不要のツールが大半** — APIキーの管理なしですぐ動く（NASAの一部ツールのみ任意キー、ESA/Copernicus のダウンロードは任意のOAuth2クレデンシャル）
- **他国の宇宙機関データに対応** — インド ISRO・欧州 ESA/Copernicus・日本 JAXA・カナダ CSA・ブラジル INPE・英国 EO DataHub・フランス CNES・全衛星軌道(CelesTrak)・EO Dashboard(NASA/ESA/JAXA共同) を横断検索
- **引用元を明示** — 科学的な内容には必ずデータソースへのリンクを併記

## 🆕 直近の更新内容（v0.9.0）

**世界の宇宙機関・地球観測データを横断検索できるよう拡充**（v0.1.0 → v0.9.0）。

- **他国の宇宙機関データ**: インド ISRO・欧州 ESA/Copernicus・日本 JAXA・カナダ CSA・ブラジル INPE・英国 EO DataHub・フランス CNES
- **衛星軌道・位置**: CelesTrak（全衛星TLE）・Open Notify（ISS現在位置＋Googleマップ）
- **地球観測カタログ**: EO Dashboard（NASA/ESA/JAXA共同 173データセット）・AWS Earth Search STAC（Sentinel/Landsat/NAIP）
- **宇宙天気**: NASA DONKI（太陽フレア・CME・地磁気嵐・太陽粒子現象）
- **衛星運用情報**: WMO OSCAR（世界の気象・地球観測衛星の運用ステータス）
- **天体観測サポート**: Open-Meteo（観測に最適な夜間時間帯の予報）

登録ツールは **27本**。認証不要のツールが大半です。

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

**キーの取得方法（無料・即時発行）**: [api.nasa.gov](https://api.nasa.gov) にアクセスし、**メールアドレスを登録するだけで** 無料の API キーが即時発行されます。無料開発者キーのレート制限は **1時間あたり 1,000 リクエスト** です（実用に十分な容量）。登録時に入力したメール宛てに確認が来ます。

```bash
export NASA_API_KEY="your_key_here"
```

### （任意）ESA Copernicus OAuth2（ダウンロード用）

`copernicus_search` の検索・プレビュー取得は認証不要で動きます。画像**ダウンロード**のみ必要です。利用時は [Copernicus Data Space](https://dataspace.copernicus.eu) で無料登録し、OAuth2 クライアント情報を環境変数に設定します。

```bash
export CDSE_CLIENT_ID="your_client_id"
export CDSE_CLIENT_SECRET="your_client_secret"
```

> この MCP は認証情報を**サーバー側でのみ保持**し、クライアントへ渡しません。ダウンロードURL生成時にのみ使用します。

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

登録ツールは **27本**（他国の宇宙機関データ 12本＋天体観測用天気 1本＋EO Dashboard 2本＋宇宙天気 1本＋AWS STAC 2本＋ISS位置 1本＋WMO OSCAR 1本）。すべて動作検証済みです。

| ツール | できること | データ源 | 認証 |
|--------|-----------|---------|------|
| `reverse_lookup` | 「史上初の宇宙望遠鏡は？」等をカテゴリ+国+時期から解決 | Wikidata SPARQL | 不要 |
| `upcoming_launches` | 今後のロケット打ち上げ予定（日時・機体・射場・状態） | Launch Library 2 | 不要 |
| `apod` | 今日（指定日）の天文写真 | NASA Open API | キー(任意) |
| `neo_today` | 今日地球に接近する小惑星 | NASA Open API | キー(任意) |
| `search_space_images` | 惑星・衛星の画像検索＋**チャット内インライン表示** | NASA Image & Video Library | 不要 |
| `search_space_audio` | 宇宙音声の検索（`kind`で効果音/ポッドキャスト切替） | NASA / Sounds from Beyond | 不要 |
| `search_space_videos` | 宇宙動画の検索＋再生URL（解像度別・字幕付き） | NASA Image & Video Library | 不要 |
| `isro_data` | インドISROの人工衛星・ロケット・顧客衛星・センター一覧/検索 | ISRO公式API (github.com/isro/api) | 不要 |
| `copernicus_collections` | ESA Copernicus の衛星データコレクション一覧 | Copernicus Data Space (STAC) | 不要 |
| `copernicus_search` | ESA Copernicus の衛星画像を STAC で検索（領域・日時・雲量） | Copernicus Data Space (STAC) | 検索は不要 / ダウンロードは任意OAuth2 |
| `jaxa_datasets` | JAXA Earth の地球観測データセット一覧（ALOS/GSMaP/GCOM等） | JAXA Earth API (STAC COG) | 不要 |
| `jaxa_dataset_search` | JAXA Earth データセットをキーワード検索 | JAXA Earth API (STAC COG) | 不要 |
| `csa_dataset_search` | カナダCSAオープンデータ（RADARSAT等）を検索 | CSA Open Data Portal (CKAN) | 不要 |
| `inpe_collections` | ブラジルINPEの衛星データコレクション一覧 | INPE BDC STAC | 不要 |
| `inpe_search` | ブラジルINPEの衛星画像（CBERS・Amazonia-1等）をSTAC検索 | INPE BDC STAC | 不要 |
| `sat_tle` | 全世界の衛星の軌道要素(TLE)を取得（ISS・ハッブル・気象衛星等） | CelesTrak (NORAD) | 不要 |
| `uk_stac_collections` | 英国EO DataHubのコレクション一覧 | UK EO DataHub STAC | 不要 |
| `uk_stac_search` | 英国EO DataHubの衛星・気候データをSTAC検索 | UK EO DataHub STAC | 不要 |
| `cnes_status` | フランスCNESのポータル（THEIA/GEODES）到達状態・概要 | CNES THEIA/GEODES | 不要(ダウンロードは要登録) |
| `astronomy_weather` | 天体観測に最適な夜間の時間帯を予報（雲量・視程・風速・降水から判断） | Open-Meteo | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | NASA宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象） | NASA DONKI | キー(任意/DEMO_KEY可) |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等） | WMO OSCAR | 不要 |

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

### 🇮🇳 `isro_data` — インド ISRO の公式データ

[ISRO公式オープンAPI](https://github.com/isro/api)（Vercel配信・認証不要）から、ISROが打ち上げた人工衛星・ロケット・外国向け顧客衛星・センター/施設の一覧を返します。`query` で部分一致検索（例 "cartosat", "chandrayaan"）も可能。

```text
Q: インドの人工衛星を教えて
A: → Aryabhata, Bhaskara-I, ... 全112件から返る
Q: ISROが外国向けに打ち上げた衛星は?
A: kind="customer_satellites" で 国・打ち上げ日・質量・ロケット 付きで返る
```

### 🇪🇺 `copernicus_collections` / `copernicus_search` — 欧州 ESA Copernicus

[Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu) の **STAC API**（`stac.dataspace.copernicus.eu/v1`）を利用。Sentinel-1/2/3 ほか地球観測衛星のメタデータを検索できます。

- `copernicus_collections`: 利用可能なコレクション一覧（Sentinel-2 光学・Sentinel-1 SAR・Sentinel-3 海洋/大気 等）
- `copernicus_search`: コレクション・空間範囲(`bbox`)・日時・雲量上限で衛星画像を検索。`structuredContent` に id/日時/雲量/bbox/プレビューURL を返す

**認証**: 検索とプレビューURL取得は認証不要。画像**ダウンロード**のみ Copernicus Data Space の無料アカウント（OAuth2 client_credentials）が必要です。設定時は環境変数 `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` をサーバー側に設定します（キーはクライアントへ晒さない）。

```text
Q: 東京周辺の最近の Sentinel-2 画像を雲が少ない順に見たい
A: copernicus_search(collection="sentinel-2-l2a", bbox="139.6,35.5,139.9,35.8", max_cloud_cover=20)
```

### 🇯🇵 `jaxa_datasets` / `jaxa_dataset_search` — 日本 JAXA Earth

[JAXA Earth API](https://data.earth.jaxa.jp) の **STAC COG カタログ**（認証不要）から、地球観測データセットを探索します。ALOS 標高(AW3D30)・GSMaP 降水・GCOM-C/W・PALSAR-2 森林(FFN) など 60+ データセット。

- `jaxa_datasets`: データセット一覧（ID・タイトル・アセット）
- `jaxa_dataset_search`: キーワード検索（"GSMaP", "ALOS", "GCOM", "precip", "FNF" 等）

```text
Q: JAXAの降雨データセットは?
A: jaxa_dataset_search("GSMaP") → GSMaP の正規・日次データセットが返る
```

### 🇨🇦 `csa_dataset_search` — カナダ CSA オープンデータ

[CSA Open Data Portal](https://donnees-data.asc-csa.gc.ca) の **CKAN API**（認証不要）で、RADARSAT ほかカナダの宇宙データセットを検索します。CSAは中間CAチェーンが不完全なサーバーのため、**OS の証明書ストアでTLS検証**するアダプタを使用（検証を無効化しない安全な方式）。

```text
Q: カナダの RADARSAT データは?
A: csa_dataset_search("radarsat") → RADARSAT-1 Archive ほか10件が返る
```

### 🇧🇷 `inpe_collections` / `inpe_search` — ブラジル INPE

[INPE BDC](https://data.inpe.br/bdc/stac/v1)（ブラジル宇宙研究所）の **STAC API**（認証不要）で、CBERS-4/4A・Amazonia-1・GOES-19 等の衛星画像を検索します。

- `inpe_collections`: 79コレクション一覧
- `inpe_search`: コレクション・領域(`bbox`)・日時・雲量で画像検索

```text
Q: ブラジル周辺のCBERS衛星画像
A: inpe_search("CB4-WFI-L4-SR-1", bbox="-46.6,-23.6,-46.5,-23.5")
```

### 🛰️ `sat_tle` — CelesTrak（全世界の衛星軌道）

[CelesTrak](https://celestrak.org)（NORADカタログ・認証不要）から、**任意の衛星の軌道要素(TLE)** を取得します。ISS・ハッブル・気象衛星・中国宇宙ステーション等、`name`・`norad_id`・`group` で指定可能。

```text
Q: ISSの現在の軌道要素は?
A: sat_tle("iss") → 傾角・離心率・周回数・エポック
Q: 中国宇宙ステーションのTLE
A: sat_tle("tiangong") → CSS(TIANHE) の軌道要素
```

### 🇬🇧 `uk_stac_collections` / `uk_stac_search` — 英国 EO DataHub

[UK EO DataHub](https://eodatahub.org.uk) の **STAC API**（公開カタログは認証不要）で、Sentinel-2 ARD・UKCP気候モデル・EOCIS 等の英国データを検索します。

```text
Q: 英国のSentinel画像
A: uk_stac_search("sentinel2_ard", bbox="-2.9,49.5,-2.8,49.6")
```
※商用データ（Airbus/Planet）は要アカウント。

### 🇫🇷 `cnes_status` — フランス CNES

CNES の地球観測ポータル **THEIA** と **GEODES** の到達状態と提供データ概要を返します。画像ダウンロードには無料アカウント登録が必要です。

```text
Q: フランスのCNES衛星データは?
A: cnes_status() → THEIA / GEODES の状態と概要
```

### 🔭 `astronomy_weather` — 天体観測用天気（Open-Meteo）

[Open-Meteo](https://open-meteo.com)（認証不要・無料・全世界対応）から、指定地点の今後数日間で**天体観測に適した夜間の時間帯**を自動抽出します。雲量・視程・風速・降水確率・昼夜判定を総合して判断し、AIからのアドバイスを返します。

- 場所は緯度経度（`latitude`/`longitude`）または地名（`place`）で指定
- 富士山・マウナケア・アタカマ・阿智村などの**著名観測地は正確な緯度経度を内蔵**
- `max_cloud` で観測可否の雲量基準を調整可能（既定 40%）
- `structuredContent` に各時間帯の雲量・視程・風速・降水確率をJSONで返す

```text
Q: マウナケアで今夜天体観測できる?
A: astronomy_weather(place="マウナケア") → 夜間の観測チャンス時間帯と雲量
Q: 東京で明日の星空は?
A: astronomy_weather(place="東京") → 雲が少ない夜間を抽出
```

出典: open-meteo.com（CC BY 4.0 データ）

### 🌍 `eodashboard_collections` / `eodashboard_detail` — EO Dashboard（NASA×ESA×JAXA共同）

[EO Dashboard](https://eodashboard.org/)（NASA・ESA・JAXA 3機関共同の地球観測ダッシュボード）のデータカタログを検索・表示します。データは GitHub リポジトリで無料公開されており、**173件のデータセット**（大気・海洋・陸域・雪氷・農業・社会経済）を認証不要で探索できます。

- `eodashboard_collections`: テーマ（atmosphere/oceans/agriculture/cryosphere等）・機関（NASA/ESA/JAXA）・キーワードで検索
- `eodashboard_detail`: 1データセットの詳細（衛星・センサー・説明・参照リンク・サムネイル画像）を返し、画像をインライン表示

```text
Q: NASAの大気データを教えて
A: eodashboard_collections(agency="NASA", themes="atmosphere")
Q: 海氷のデータセットは?
A: eodashboard_collections(keyword="sea ice")
Q: NO2データセットの詳細と画像を見せて
A: eodashboard_detail("N1_NO2") → 詳細+サムネイル画像
```

> 時系列データ（Sentinel Hub統計API）は別途キーが必要ですが、**メタデータ・説明・画像・参照リンクの取得はすべて認証不要**です。検索・概要・学習用途に最適です。

出典: github.com/ESA-eodashboards/eodashboard-catalog ／ github.com/eurodatacube/eodash-assets

### ☀️ `space_weather` — NASA 宇宙天気（DONKI）

[NASA DONKI](https://api.nasa.gov/)（Database Of Notifications, Knowledge, Information）から、太陽活動に伴う宇宙環境の乱れを取得します。天体観測（オーロラ・電波）や通信・衛星運用への影響評価に使えます。

- `kind` で取得対象を選択: `all`（既定）/ `flare`（太陽フレア）/ `cme`（コロナ質量放出）/ `gst`（地磁気嵐）/ `sep`（太陽粒子現象）
- 期間は `start_date` / `end_date`（YYYY-MM-DD）で指定
- 認証: 環境変数 `NASA_API_KEY`（`apod` と同じキーを使用）。未設定時は `DEMO_KEY`（低レート）

```text
Q: 最近の太陽フレアは?
A: space_weather(kind="flare")
Q: 地磁気嵐が起きているか確認
A: space_weather(kind="gst", start_date="2026-08-01")
Q: 宇宙天気の全体状況
A: space_weather() → フレア・CME・地磁気嵐・粒子現象をまとめて表示
```

出典: api.nasa.gov（NASA Space Weather）

### 🌍 `stac_collections` / `stac_search` — AWS Earth Search STAC

[AWS Earth Search](https://earth-search.aws.element84.com)（STAC規格の衛星データカタログ）で、**Sentinel-2 / Landsat 8/9 / NAIP / Copernicus DEM** を認証不要で検索します。

- `stac_collections`: 利用可能な9コレクション一覧
- `stac_search`: コレクション・場所（bbox/地名）・日時・**雲量上限**で検索。`structuredContent` に**実際のデータURL**（COG等）を含む

```text
Q: 東京のLandsat画像を探して
A: stac_search("landsat-c2-l2", place="東京")
Q: 関東の雲が少ないSentinel-2画像
A: stac_search("sentinel-2-l2a", bbox="139.6,35.5,139.9,35.8", max_cloud_cover=20)
Q: 利用できる衛星データを一覧
A: stac_collections()
```

> Copernicus Data Space（`copernicus_search`）が全Sentinelを扱うのに対し、こちらは**LandsatやNAIPも検索可能**。雲量フィルタは STAC の `query` パラメータを使用します（Earth Search は `filter` 非対応）。

出典: earth-search.aws.element84.com

### 🛰 `iss_now` — ISS 現在位置（Googleマップ表示）

[Open Notify](http://open-notify.org) の `iss-now.json` から、**ISS（国際宇宙ステーション）の現在位置**（緯度経度）を取得し、**Googleマップリンク**で表示します。

```text
Q: ISSは今どこを飛んでる?
A: iss_now() → 現在の緯度経度 + Googleマップリンク
```

- 高度約420km・速度約27,700 km/h・地球1周約90分
- `structuredContent` に緯度経度・タイムスタンプ・GoogleマップURLを返す
- 認証不要

> 出典: api.open-notify.org（Open Notify）。**HTTP のみ対応**（HTTPS では応答しない）。

### 🛰 `satellite_status` — WMO OSCAR（世界気象機関）衛星カタログ

[WMO OSCAR/Space](https://space.oscar.wmo.int)（世界気象機関の公式カタログ）から、気象・地球観測衛星の**運用ステータス・軌道・打ち上げ日**を取得します。認証不要で **1041衛星** を収録。

```text
Q: ロシアの気象衛星（Meteor-M）の運用状況は?
A: satellite_status(query="Meteor-M", agency="Roscosmos")
Q: 世界の気象衛星一覧
A: satellite_status()
```

- `query`: 衛星名の部分一致（meteor, resurs, goes, kanopus, himawari 等）
- `agency`: 機関名（Roscosmos, NOAA, EUMETSAT, JAXA 等）
- 運用中🟢 / 計画中🔵 / 延長🟡 / 退役🔴 を色付きで表示
- Roscosmos の気象衛星（Meteor-M・Resurs-P・Kanopus等）も詳細に収録

> **実装メモ**: OSCAR API の `search` / `space_agency` / `status` パラメータは現在**機能しません**（常に全件を返す）。そのため全件をページング取得し、クライアントサイドでフィルタしています。

出典: space.oscar.wmo.int（WMO OSCAR/Space）

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
├── server.py            # FastMCP サーバー定義・27ツール登録
├── wikidata_lookup.py   # reverse_lookup（逆引き歴史Q&A）
├── launch.py            # upcoming_launches（ロケット打ち上げ）
├── nasa.py              # apod / neo_today（NASA日次）
├── media.py             # search_space_images / audio / videos（メディア検索）
├── isro.py              # isro_data（インド ISRO）
├── copernicus.py        # copernicus_collections / copernicus_search（欧州 ESA）
├── jaxa.py              # jaxa_datasets / jaxa_dataset_search（日本 JAXA）
├── csa.py               # csa_dataset_search（カナダ CSA, システムCA検証アダプタ）
├── inpe.py              # inpe_collections / inpe_search（ブラジル INPE STAC）
├── celestrak.py         # sat_tle（CelesTrak 全衛星軌道TLE）
├── uk_datahub.py        # uk_stac_collections / uk_stac_search（英国 EO DataHub）
├── cnes.py              # cnes_status（フランス CNES THEIA/GEODES）
├── weather_astro.py     # astronomy_weather（天体観測用天気, Open-Meteo）
├── eodashboard.py       # eodashboard_collections / detail（EO Dashboard, NASA/ESA/JAXA）
├── donki.py             # space_weather（NASA 宇宙天気 DONKI）
├── stac_search.py       # stac_collections / stac_search（AWS Earth Search STAC）
├── iss.py               # iss_now（ISS 現在位置, Open Notify）
└── oscar.py             # satellite_status（WMO OSCAR 衛星カタログ）
```

---

## 📄 ライセンス / 注意

- **ライセンス**: MIT License（本リポジトリの `LICENSE` を参照）
- **データソース**: NASA・NASA Image & Video Library・Launch Library 2・Wikidata(Wikimedia)・ISRO・ESA Copernicus Data Space・JAXA Earth API・CSA Open Data・INPE BDC・CelesTrak・UK EO DataHub・CNES THEIA/GEODES・Open-Meteo(CC BY 4.0)・EO Dashboard・NASA DONKI・AWS Earth Search STAC・Open Notify・WMO OSCAR は、それぞれの利用条件・ライセンスに従います。
- 宇宙データは科学的な内容を含みます。応答時は**引用元（Wikipedia / NASA / ISRO / ESA / JAXA / CSA 等）へのリンクを必ず表示**してください。
- 画像・動画・音声の著作権・クレジット表記は各ソースの指示に従ってください（NASA素材は NASA Media Usage Guidelines を参照）。

---

## 🗺️ ロードマップ

- [x] 逆引き歴史Q&A（Wikidata）
- [x] ロケット打ち上げ・NASA日次
- [x] 画像・音声・動画の検索・表示
- [x] 他国の宇宙機関データ（ISRO・ESA/Copernicus・JAXA・CSA・INPE・CelesTrak・UK EO DataHub・CNES）
- [x] 天体観測用天気（Open-Meteo）
- [x] EO Dashboard（NASA/ESA/JAXA 共同地球観測カタログ）
- [x] 宇宙天気（NASA DONKI: 太陽フレア・CME・地磁気嵐）
- [x] AWS Earth Search STAC（Sentinel/Landsat/NAIP 検索）
- [x] ISS 現在位置（Open Notify + Googleマップ表示）
- [x] WMO OSCAR 衛星カタログ（運用ステータス）
- [ ] 地球リアルタイム画像（EPIC/DSCOVR）
- [ ] 惑星の3D地図（NASA Trek WMTS）
- [ ] 多言語（en/zh）応答の全面対応

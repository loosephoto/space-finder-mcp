# Space Finder MCP 🚀

**宇宙・天文データを横断検索し、画像・動画・音声までチャット上で表示できる** Model Context Protocol (MCP) サーバーです。

惑星・人工衛星・宇宙ミッションのメディア検索、ロケット打ち上げ、小惑星接近情報、「史上初の宇宙望遠鏡は？」といった逆引き歴史Q&Aを、AIエージェント（Claude / Cursor / Hermes 等）から自然言語で呼び出せます。

> 「Space Finder」は**宇宙(Space)に関する情報を探す**MCPです。「物理的な空間＝オフィス・駐車場を探す」同名サービスとは無関係です。

---

## ✨ 特徴

- **画像・動画・音声をそのまま返せる** — 画像はチャット内にインライン表示、音声・動画は再生URLを返却
- **tokyo-transit方式のJSON応答** — 人間向け表示（`content`）とLLM向け純粋JSON（`structuredContent`）を分離し、情報を失わずに構造化データを渡せる
- **認証不要のツールが大半** — APIキーの管理なしですぐ動く（NASAの一部ツールのみ任意キー、ESA/Copernicus のダウンロードは任意のOAuth2クレデンシャル）
- **他国の宇宙機関データに対応** — インド ISRO・欧州 ESA/Copernicus・日本 JAXA・カナダ CSA・ブラジル INPE・英国 EO DataHub・フランス CNES・中国 CNSA 系ポータル・全衛星軌道(CelesTrak)・EO Dashboard(NASA/ESA/JAXA共同) を横断検索
- **引用元を明示** — 科学的な内容には必ずデータソースへのリンクを併記

## 🆕 直近の更新内容（v0.24.0）

**月周回機の月面位置・軌道を月面地図に表示するツール `lunar_track` を追加**（v0.24.0）。

- **`lunar_track`**: 月周回機（LRO・ゲートウェイ等）の**月面での現在位置と軌道トレイル**を、NASA Trek の月面地図（LRO WAC モザイク）に重ねて画像化。JPL Horizons が返す月中心の状態ベクトルを **IAU 2015 月自転モデル**で月体固定座標（selenographic 緯度経度・高度）に変換して正確に計算。認証不要。
  - 例:「LROの現在位置を月面地図で」「月周回機の位置」「ゲートウェイの月面軌道」
  - **アルテミス計画対応**: 今後月軌道を周回する機体（ゲートウェイ等）が増えた際に、`MOON_CRAFT` テーブルへ JPL Horizons 天体IDを追加するだけで追跡可能。
  - 過去ミッション（かぐや等）は運用終了のため丁寧に案内。
- **`sat_ground_track` のトレイル上限を拡張**: 準天頂衛星（みちびき）の**8の字（アナレンマ）軌道**を表示できるよう、`minutes` の上限を24時間（1440分）に拡張。

登録ツールは **45本**。認証不要のツールが大半です。

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

登録ツールは **45本**（他国の宇宙機関データ 15本＋火星探査ローバー 2本＋天文観測(ESO/CADC/ALMA) 3本＋電波望遠鏡(TART) 1本＋天文ニュース 1本＋天体観測用天気 1本＋天体位置・星座 1本＋星図合成 1本＋太陽系俯瞰 1本＋日食時系列 1本＋NASA POWER気候 1本＋EO Dashboard 2本＋宇宙天気 1本＋AWS STAC 2本＋ISS位置 1本＋衛星地上軌道 1本＋月周回機 1本＋WMO OSCAR 1本＋中国/ロシア打ち上げ・天宮 3本＋メディア/逆引き 4本）。すべて動作検証済みです。

| ツール | できること | データ源 | 認証 |
|--------|-----------|---------|------|
| `reverse_lookup` | 「史上初の宇宙望遠鏡は？」等をカテゴリ+国+時期から解決 | Wikidata SPARQL | 不要 |
| `upcoming_launches` | 今後のロケット打ち上げ予定（日時・機体・射場・状態） | Launch Library 2 | 不要 |
| `china_launches` | 中国のロケット打ち上げ予定（長征・Shenzhou・LandSpace等・射場・ミッション） | Launch Library 2 | 不要 |
| `russia_launches` | ロシア（Roscosmos）のロケット打ち上げ予定（ソユーズ・Progress・Luna等・射場・ミッション） | Launch Library 2 | 不要 |
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
| `astronomy_weather` | 天体観測に最適な夜間の時間帯を予報（雲量・視程・風速・降水・**月相・月明かり**から判断） | Open-Meteo | 不要 |
| `constellation_now` | 指定地点・時刻で太陽・月・惑星の高度・方位・星座を計算（観測可否判断） | Skyfield + JPL de421 | 不要 |
| `astronomy_news` | Sky & Telescope の最新天文ニュース・「今週の星空ガイド」を取得（観測/ニュース絞込可） | Sky & Telescope RSS | 不要 |
| `mars_rover_status` | 火星探査ローバー（キュリオシティ等）の現在の状況・天気・ソルを表示 | NASA Mars Weather | 不要 |
| `power_climate` | 任意地点の過去の気候・太陽エネルギー統計（気温・日射量・風速） | NASA POWER | 不要 |
| `eso_seeing` | ESO パラナル天文台（チリ, VLT）のリアルタイム大気コンディション（シーイング・可降水量・気象） | ESO ASM API | 不要 |
| `cadc_observations` | CADC（カナダ天文データセンター）の観測データ検索（HST・ジェミニ等） | CADC TAP | 不要(画像DLは一部要登録) |
| `alma_search` | ALMA（アルマ望遠鏡）科学アーカイブの観測データ検索（観測対象・座標・周波数帯・公開/要権限） | ALMA Science Archive (NAOJ, IVOA TAP) | 不要 |
| `radio_sources_now` | TART オープン電波望遠鏡が「いま観測できる電波源」（GNSS・静止衛星等）を仰角順に表示 | TART source catalog (NZ) | 不要 |
| `sky_map_with_satellites` | 指定地の空に太陽系の惑星と人工衛星を重ねた画像（matplotlib正確版/Pillow簡易版を選択） | JPL de421+Skyfield / CelesTrak+SGP4 | 不要 |
| `mars_rover_location_map` | 火星探査ローバーの現在地を火星地図中心に示した画像（走行経路・着陸点） | NASA MMGIS + Trek WMTS | 不要 |
| `solar_system_now` | 太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置俯瞰図（ハレー等の周期彗星とC/彗星・ボイジャー等の遠方天体まで対数縮尺で自動拡張表示） | JPL DE421+Skyfield / JPL SBDB / JPL Horizons | 不要 |
| `solar_eclipse_series` | 日食（太陽が月に欠ける過程）の時系列パネル画像（食の始まり〜最大〜終わり7枚・次回日食の自動検索・max_magnitude対応） | JPL DE421+Skyfield | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | NASA宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象） | NASA DONKI | キー(任意/DEMO_KEY可) |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `sat_ground_track` | 任意の人工衛星（ISS・ひので・ハッブル等）の現在位置と地上軌道を地球地図にプロットした画像を返す。CelesTrak TLE + Skyfield(SGP4) で真下の点・高度・速度を計算し、NASA Blue Marble 地図に軌道トレイルを重ねる | CelesTrak + Skyfield + Blue Marble | 不要 |
| `lunar_track` | 月周回機（LRO・ゲートウェイ等）の月面での現在位置と軌道トレイルを月面地図にプロットした画像を返す。JPL Horizons の状態ベクトルを IAU 月自転モデルで月面座標（selenographic 緯度経度・高度）に変換し、NASA Trek の月面タイル（LRO WAC）に重ねる。アルテミス計画の月軌道機追跡に対応 | JPL Horizons + NASA Trek | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等） | WMO OSCAR | 不要 |
| `cnsa_status` | 中国CNSA系衛星データポータル（風雲/NSMC・高分/CNSA-GEO・CBERS/CRESDA）の到達状態・概要＋認証不要の代替経路 | CNSA各公式ポータル | 不要(ダウンロードは要登録) |
| `tiangong_now` | 天宮（Tiangong）中国宇宙ステーションの現在位置（SGP4伝播＋Googleマップ表示） | CelesTrak TLE + SGP4 | 不要 |

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



### 📡 `alma_search` — ALMA 電波観測データ検索

ALMA（アタカマ大型ミリ波サブミリ波干渉計）の科学アーカイブを、NAOJ が運用する東アジア鏡の IVOA TAP 経由で検索。`object_name`（例 `M100`, `HL Tau`）か `ra`/`dec`+`radius`、受信 `band` で絞り込み、観測メタデータ（観測対象・座標・周波数帯・プロポーザルID・公開/要権限）を返す。mm/サブmm の電波観測のため低温ガス・塵や惑星系形成領域が対象。

> メタデータ検索は認証不要。`data_rights=Public` の FITS は ALMA アーカイブから取得可（`Restricted` は元プロポーザル権限者のみ）。

```json
{"observatory": "ALMA", "count": 8, "public": 8, "restricted": 0,
 "records": [{"target_name": "M100", "band": "3", "frequency_ghz": "100–104 GHz",
              "data_rights": "Public", "proposal_id": "2011.0.00004.SV", ...}]}
```

### 📡 `radio_sources_now` — TART 電波望遠鏡の可視電波源

オープンソースで開発公開されている教育用電波干渉計 **TART**（NZ）の可視電波源カタログから、指定した観測地（`lat`/`lon`）で地平線より上にある電波源を取得。返るのは主に GNSS・放送・静止通信衛星で、`name`・仰角`el`・方位角`az`・距離・フラックス密度`jy` を持つ。電波天文・衛星追尾の学習に最適。

> `lat`/`lon` 省略時は TART 本体のある NZ（ダニーデン）基準。可視性（強度）API は 2026-09 時点で応答停止のため未使用。

```json
{"observatory": "TART", "count": 15,
 "sources": [{"name": "GSAT0232 (GALILEO 32)", "elevation_deg": 77.9,
              "azimuth_deg": 63.1, "range_km": 23342, "flux_density_jy": 1500000}, ...]}
```


### 🗺️ `sky_map_with_satellites` — 星空マップ＋人工衛星（描画エンジン選択式）

指定した観測地・時刻の空に、太陽系の惑星・月と人工衛星の現在位置を重ねた**画像**を返す。天体位置は JPL de421 + Skyfield、衛星位置は CelesTrak TLE + SGP4 で実測計算（全てローカル/認証不要）。

`engine` で描画を選択:
- `"simple"`（既定）: **Pillow** による実写背景の簡易合成。惑星を種類別アイコン（岩石惑星=各色、木星=縞、土星=環）、衛星を赤い発光マーカー＋軌道予測線で描く。**学生・観賞用途で視認性重視**。
- `"accurate"`: **matplotlib** による正確な星図。方位・仰角グリッド、軌道予測線を精確表示（科学・詳細用途）。

画像は content に base64 でインライン表示、座標一覧は structuredContent に JSON。

```json
{"time_utc": "...", "engine": "simple (Pillow)",
 "planets": {"月": {"az":..,"alt":..}, ...},
 "satellites": {"ISS (国際宇宙ステーション)": {"az":..,"alt":..,"trail":[...]}, ...}}
```

### 🔴 `mars_rover_location_map` — 火星ローバー現在地マップ

火星探査ローバー（パーサヴィアランス/キュリオシティ）の**現在地を火星地図の中心に示した画像**を返す。NASA MMGIS から現在地(緯度経度)と走行経路、NASA Trek WMTS（等角図法）から火星の地図タイルを取得し、走行経路(橙線)・着陸地点(青●)・現在地(赤●)を合成。**ローバーを常に画像中心**に配置。

`zoom`(5-7)・`span_deg`(画角)・`out_px`(出力サイズ) で精度と軽量さを調整できる（例: `zoom=6` で高速・軽量）。タイルは並列取得で高速化。天気・ソル情報も併記。認証不要。

```json
{"rover": "perseverance", "lat": 18.437, "lon": 77.232, "sol": 1965,
 "dist_km": 45.11, "source": "NASA MMGIS + Trek WMTS"}
```

### ☀️ `solar_system_now` — 太陽系俯瞰図（太陽中心の惑星・小惑星・探査機・彗星位置）

「太陽系を上から見た図」「今の惑星の位置」「イトカワの今の位置を図で」などに応答。太陽を中心とした黄道面俯瞰図を画像化し、惑星(8惑星＋冥王星)は **JPL DE421 暦表 + Skyfield** で日心黄道座標を計算。任意の小惑星は **JPL SBDB API** の軌道要素を取得し**ケプラー2体問題**で日心位置へ伝播する（`asteroid`/`asteroid2` で複数指定可）。

- 距離が 0.4〜40 AU と2桁超のため、**Pillow版(既定)は対数縮尺**で全天体を一枚に表示。惑星を実物色アイコン＋距離ラベル、小惑星を**緑の十字マーカー**＋緑軌道リングで強調。惑星軌道円・小惑星帯(2.0-3.4AU目安)も併記。学生・観賞向けに視認性優先。
- `engine="accurate"` で **matplotlib 版**（線形距離の正確な相対距離俯瞰図）も選択可。科学的な相対距離比較用途向け。
- 対応小惑星エイリアス: イトカワ(itokawa/25143)・ベンヌ(101955)・アポフィス(99942)・リュウグウ(162173)・ツタティス(4179)・エロス(433)。SBDBの`sstr`（番号・名前）なら任意の小惑星を指定可能。認証不要。
- **遠方探査機**: `probe`/`probe2` 引数に ボイジャー1号/2号・パイオニア10号/11号・ニュー・ホライズンズ（日英名対応）を指定。JPL Horizons 状態ベクトル（ECLIPTIC, 太陽中心）から日心位置を取得。ボイジャー等は**黄緯が大きい（~35°）ため、黄道面への正射影距離**で描画し、ラベルに真距離と黄緯を併記。表示スケールは探査機の距離まで自動拡張し、色付き菱形マーカー＋太陽からの補助線で強調。遠方のため線形 matplotlib 版は使わず Pillow（対数縮尺）で表示。認証不要。
- **彗星（周期・C/）**: `comet`/`comet2` 引数に ハレー彗星(halley/1P)・エンケ彗星(2P)・チュリュモフ・ゲラシメンコ(67P)・テンペル第1(9P)・ヴィルト第2(81P) 等の**周期彗星**、または 紫金山・アトラス(C/2023 A3)・ラブジョイ(C/2014 Q2) 等の**C/彗星**を指定。周期彗星は JPL SBDB 軌道要素のケプラー伝播、C/彗星（非周期・放物線/双曲線軌道）は JPL Horizons 状態ベクトルで 日心位置を計算。彗星は**シアン色の輝く核＋太陽と反対方向に伸びる尾**で描画し、黄緯が大きいものは 黄道面への正射影距離で配置・ラベルに真距離と黄緯を併記。遠方彗星は表示スケールを自動拡張。認証不要。

```json
{"time_utc": "2026-09-09 05:32 UTC", "engine": "simple (Pillow, 対数縮尺)",
 "asteroids": {"イトカワ": {"au": 1.60, "eclLon": 82.9}},
 "probes": {"ボイジャー1号": {"au": 171.7, "proj_au": 140.4, "eclLat": 35.2}},
 "comets": {"ハレー彗星": {"au": 35.1, "proj_au": 33.7, "eclLat": -16.6}}}  // 惑星は全9天体
```


### 🌞 `solar_eclipse_series` — 日食の時系列パネル（太陽が月に欠ける過程）

「2035年9月2日の皆既日食を画像で」「東京で見える次の日食」「2019年の部分日食の進行」などに応答。指定した観測地・日付で、太陽と月の見かけの重なりを **JPL DE421 + Skyfield** で実測計算し、**食の始まり〜最大〜終わりを7枚のパネル**に並べて合成。サンプル画像（部分日食の時系列）と同じレイアウト。

- 太陽・月の**視角半径と角距離**を観測地の視位置(topocentric apparent)で計算し、**月の位置角(PA)**を反映して正しく欠ける位置に月を描画。パネル上部に▲（天の北）、各パネル下に現地時刻・食分。
- 最大食分から種別を自動判定：0.995以上は**皆既**(月視半径≥太陽)／**金環**(月<太陽)、それ以外は**部分**。
- `date` 省略時は**「これから起こる次の日食」を自動検索**（Skyfield almanac で新月を列挙し、食のありうる新月だけ高速絞り込み → 精密計算）。例: 2026年9月時点で東京の次回は **2028-01-26 部分日食（食分0.63）**。
- `max_magnitude=True` で最大食のみの単一画像を返す。`place`/`lat`/`lon` で**世界中の任意の観測地**を指定可能（東京・ベルリン・ケープタウン・グリニッジ天文台・昭和基地 等。Open-Meteo + Nominatim で自動解決）。日食の現地時刻はその場所のタイムゾーン（DST込み）で表示。認証不要。

```json
{"kind": "部分日食", "max_magnitude": 0.63, "date": "2028年1月26日",
 "place": "東京", "lat": 35.68, "lon": 139.69}  // 7パネル時系列画像を content に返す
```

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
├── launch.py            # upcoming_launches / china_launches / russia_launches（ロケット打ち上げ・中国・ロシア）
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
├── weather_astro.py     # astronomy_weather（天体観測用天気, Open-Meteo, 月相対応）
├── power.py             # power_climate（NASA POWER 気候・太陽エネルギー統計）
├── eodashboard.py       # eodashboard_collections / detail（EO Dashboard, NASA/ESA/JAXA）
├── donki.py             # space_weather（NASA 宇宙天気 DONKI）
├── stac_search.py       # stac_collections / stac_search（AWS Earth Search STAC）
├── iss.py               # iss_now（ISS 現在位置, Open Notify）
├── oscar.py             # satellite_status（WMO OSCAR 衛星カタログ）
├── cnsa.py              # cnsa_status（中国 CNSA 系衛星データポータル到達状態）
├── tiangong.py          # tiangong_now（天宮 中国宇宙ステーション位置, CelesTrak TLE + SGP4）
├── eso.py               # eso_seeing（ESO パラナル大気・シーイング）
├── cadc.py              # cadc_observations（CADC カナダ天文観測データ）
├── alma.py              # alma_search（ALMA 電波観測データ, Science Archive TAP）
├── tart.py              # radio_sources_now（TART オープン電波望遠鏡 可視電波源）
├── skyfield_pos.py      # constellation_now（天体位置・星座, Skyfield）
├── news.py               # astronomy_news（Sky & Telescope 天文ニュース）
├── sky_overlay.py        # sky_map_with_satellites（星図+人工衛星, matplotlib/Pillow）
├── solar_system.py        # solar_system_now（太陽系俯瞰図, JPL DE421+SBDB / matplotlib+Pillow）
├── solar_eclipse.py       # solar_eclipse_series（日食の時系列パネル, JPL DE421+Skyfield）
└── mars_rover.py         # mars_rover_status / mars_rover_location_map（火星ローバー）
```

---

## 📄 ライセンス / 注意

- **ライセンス**: MIT License（本リポジトリの `LICENSE` を参照）
- **データソース**: NASA・NASA Image & Video Library・Launch Library 2・Wikidata(Wikimedia)・ISRO・ESA Copernicus Data Space・JAXA Earth API・CSA Open Data・INPE BDC・CelesTrak・UK EO DataHub・CNES THEIA/GEODES・CNSA（NSMC/CNSA-GEO/CRESDA）・CelesTrak（天宮TLE）・ESO ASM・ALMA Science Archive(NAOJ)・TART・CADC・JPL/Skyfield(de421)・NASA MMGIS・NASA Trek WMTS・Open-Meteo(CC BY 4.0)・EO Dashboard・NASA DONKI・NASA POWER・AWS Earth Search STAC・Open Notify・WMO OSCAR は、それぞれの利用条件・ライセンスに従います。
- 宇宙データは科学的な内容を含みます。応答時は**引用元（Wikipedia / NASA / ISRO / ESA / JAXA / CSA / CNSA 等）へのリンクを必ず表示**してください。
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
- [x] 中国 CNSA 系衛星データポータル（風雲/高分/CBERS 到達状態）
- [x] 中国のロケット打ち上げ（china_launches）・天宮リアルタイム位置（tiangong_now）
- [x] 天体観測の強化（astronomy_weather に月相・月明かり統合）・NASA POWER 気候データ（power_climate）
- [x] 世界の天文観測データ（ESO パラナルシーイング・CADC カナダ天文データ）
- [x] ロシア（Roscosmos）の打ち上げデータ（russia_launches）
- [x] 天体位置・星座計算（constellation_now, Skyfield + JPL）
- [x] 天文ニュース（astronomy_news, Sky & Telescope RSS）
- [x] 火星探査ローバー状況（mars_rover_status, Mars Weather）
- [ ] 地球リアルタイム画像（EPIC/DSCOVR）
- [ ] 惑星の3D地図（NASA Trek WMTS）
- [ ] 多言語（en/zh）応答の全面対応


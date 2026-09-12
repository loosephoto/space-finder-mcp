---
name: space-finder-mcp
description: 宇宙・天文・地球観測データを横断検索するMCPサーバー。NASA/ESA/JAXA/ISRO/CSA/INPE/UK/CNSA/CelesTrak/JPL/Wikidata/Open-Meteo を統合した45ツール。衛星画像・軌道マップ・日食パネルを画像で返し、構造化JSONも同時に提供。
category: space
---

# Space Finder MCP Server

## 目的

宇宙・天文・地球観測の**公開API（大半は認証不要）**を1つの MCP サーバーで横断検索するツール群です。画像生成（地球地図上の衛星軌道、天体地図上の周回機・ローバー、太陽系俯瞰、日食時系列パネル、星空マップ）を含み、各ツールは人間向け表示（`content`）と LLM向け純粋JSON（`structuredContent`）を同時に返します。

## 前提条件

- **Python 3.11+** / **uv**（`uv sync` で依存解決。Node.js 不要）
- 初回起動時に Skyfield が JPL 天体暦 `de421.bsp`（約16MB）を自動取得
- APIキーは**すべて任意**（`NASA_API_KEY` 未設定でも `DEMO_KEY` で動作。30リクエスト/時/IP の共有枠）

## 対応データソース

- 宇宙機関: NASA（APOD/NEO/DONKI/POWER/Image&Video Library）、ESA Copernicus、JAXA Earth、ISRO、CSA、INPE、UK EO DataHub、CNES、CNSA（NSMC/CNSA-GEO/CRESDA）、EO Dashboard（NASA×ESA×JAXA）
- 天文・軌道: JPL（DE421暦/SBDB/Horizons）、CelesTrak（NORAD TLE）、WMO OSCAR、ESO ASM、ALMA Science Archive、CADC、TART、Wikidata（SPARQL）、Open-Meteo、NASA MMGIS / NASA Trek（天体地図）
- 打ち上げ: Launch Library 2
- 図法・画像素材: NASA Blue Marble、NASA Trek WMTS（月・火星・水星・タイタン・ベスタ・ケレス）

## ツール一覧（45種）

| ツール | できること | データ源 | 認証 |
|:--|:--|:--|:--|
| `reverse_lookup` | 「史上初の宇宙望遠鏡は？」等をカテゴリ+国+時期から解決 | Wikidata SPARQL | 不要 |
| `upcoming_launches` | 今後のロケット打ち上げ予定（日時・機体・射場・状態） | Launch Library 2 | 不要 |
| `china_launches` | 中国のロケット打ち上げ予定（長征・Shenzhou・LandSpace等・射場・ミッション） | Launch Library 2 | 不要 |
| `russia_launches` | ロシア（Roscosmos）のロケット打ち上げ予定（ソユーズ・Progress・Luna等・射場・ミッション） | Launch Library 2 | 不要 |
| `apod` | 今日（指定日）の天文写真 | NASA Open API | キー(任意) |
| `neo_today` | 今日地球に接近する小惑星 | NASA Open API | キー(任意) |
| `search_space_images` | 惑星・衛星の画像検索＋**チャット内インライン表示**（既定は先頭1枚・`inline_max` で増減） | NASA Image & Video Library | 不要 |
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
| `astronomy_news` | 最新の天文ニュース・「今週の星空ガイド」を取得（観測/ニュース絞込可）。取得不可時は他ソースへ自動フォールバック | Sky & Telescope / Universe Today / NASA / Phys.org RSS | 不要 |
| `mars_rover_status` | 火星探査ローバー（キュリオシティ等）の現在の状況・天気・ソルを表示 | NASA Mars Weather | 不要 |
| `power_climate` | 任意地点の過去の気候・太陽エネルギー統計（気温・日射量・風速） | NASA POWER | 不要 |
| `eso_seeing` | ESO パラナル天文台（チリ, VLT）のリアルタイム大気コンディション（シーイング・可降水量・気象） | ESO ASM API | 不要 |
| `cadc_observations` | CADC（カナダ天文データセンター）の観測データ検索（HST・ジェミニ等） | CADC TAP | 不要(画像DLは一部要登録) |
| `alma_search` | ALMA（アルマ望遠鏡）科学アーカイブの観測データ検索（観測対象・座標・周波数帯・公開/要権限） | ALMA Science Archive (NAOJ, IVOA TAP) | 不要 |
| `radio_sources_now` | TART オープン電波望遠鏡が「いま観測できる電波源」（GNSS・静止衛星等）を仰角順に表示 | TART source catalog (NZ) | 不要 |
| `sky_map_with_satellites` | 指定地の空に太陽系の惑星と人工衛星を重ねた画像（matplotlib正確版=PNG/Pillow簡易版=JPEGを選択） | JPL de421+Skyfield / CelesTrak+SGP4 | 不要 |
| `solar_system_now` | 太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置俯瞰図（ハレー等の周期彗星とC/彗星・ボイジャー等の遠方天体まで対数縮尺で自動拡張表示）。`view="comet_orbit"` で彗星の軌道面ビュー（太陽＝焦点の楕円／e≥1 は双曲線の枝） | JPL DE421+Skyfield / JPL SBDB / JPL Horizons | 不要 |
| `solar_eclipse_series` | 日食（太陽が月に欠ける過程）の時系列パネル画像（7枚・食分と太陽高度・次回日食の自動検索・max_magnitude対応。**地平線下で見えない食は返さない**） | JPL DE421+Skyfield | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | NASA宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象） | NASA DONKI | キー(任意/DEMO_KEY可) |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `sat_ground_track` | 任意の人工衛星（ISS・ひので・ハッブル等）の現在位置と地上軌道を地球地図にプロットした画像を返す。CelesTrak TLE + Skyfield(SGP4) で真下の点・高度・速度を計算し、NASA Blue Marble 地図に軌道トレイルを重ねる | CelesTrak + Skyfield + Blue Marble | 不要 |
| `planetary_orbiter_track` | 任意の天体（月・火星・水星・タイタン等）を周回する探査機の現在位置と軌道トレイルを、その天体の地図にプロットした画像を返す。JPL Horizons の状態ベクトルを IAU 自転モデルで天体固定座標（緯度経度・高度）に変換し、NASA Trek の等角図法地図に重ねる。`span_deg=360`で天体全面表示にも対応 | JPL Horizons + NASA Trek | 不要 |
| `planetary_rover_location_map` | 任意の天体面を移動する探査ローバーの現在地をその天体の地図中心に示した画像（走行経路・着陸点）。NASA MMGIS の位置データと NASA Trek の等角地図を合成。現状データは火星ローバー（Perseverance/Curiosity） | NASA MMGIS + Trek WMTS | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等） | WMO OSCAR | 不要 |
| `cnsa_status` | 中国CNSA系衛星データポータル（風雲/NSMC・高分/CNSA-GEO・CBERS/CRESDA）の到達状態・概要＋認証不要の代替経路 | CNSA各公式ポータル | 不要(ダウンロードは要登録) |
| `tiangong_now` | 天宮（Tiangong）中国宇宙ステーションの現在位置（SGP4伝播＋Googleマップ表示） | CelesTrak TLE + SGP4 | 不要 |

## セットアップ

### 1. キー（任意）

```bash
cp .env.example .env     # NASA_API_KEY=... を記入（.gitignore 済み・コミット禁止）
```
優先順位は **MCPクライアントの env > リポジトリ直下の .env**。どちらも無ければ `DEMO_KEY`。

### 2. 実行

```bash
uv sync
uv run space-finder-mcp          # stdio サーバーとして起動
```

### 3. MCPクライアント設定

`mcp.json` 形式（Claude Desktop / Hermes / その他）:

```json
{
  "mcpServers": {
    "space-finder": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/space-finder-mcp", "run", "space-finder-mcp"]
    }
  }
}
```

- **Claude Code**: `claude mcp add -s project space-finder -- uv --directory "$(pwd)" run space-finder-mcp`
- **Codex**: `codex mcp add space-finder --env NASA_API_KEY=<key> -- uv --directory <ABS> run space-finder-mcp`
- Windows で `uv` を介したくない場合: `.venv/Scripts/python.exe -c "from space_finder_mcp import main; main()"`

## 使用例

```
「ISSの現在位置を地球地図で」                → sat_ground_track(name="iss")
「ハッブルの軌道」「ひのでの位置」            → sat_ground_track(name="hubble"/"hinode")
「みちびきの8の字軌道」                      → sat_ground_track(norad_id=49336, minutes=720)
「LROの現在位置を月面地図で」                → planetary_orbiter_track(body="moon", orbiter="lro")
「MROは火星のどこ？」                        → planetary_orbiter_track(body="mars", orbiter="mro")
「パーサヴィアランスの現在地を火星地図で」    → planetary_rover_location_map(body="mars", rover="perseverance")
「太陽系を上から見た図」「はやぶさ2は今どこ」 → solar_system_now(probe="はやぶさ2")
「ハレー彗星の軌道を見せて」                  → solar_system_now(comet="ハレー彗星", view="comet_orbit")
「紫金山・アトラスの軌道は？」                → solar_system_now(comet="C/2023 A3", view="comet_orbit")  # e>1 は双曲線の枝
「東京で見える次の日食を画像で」              → solar_eclipse_series(place="東京")  # 可視の食が無ければ「見つかりません」＋date 案内
「2030年の日食を東京で」                      → solar_eclipse_series(place="東京", date="2030-06-01")
「今夜の観測に向く時間帯は？」                → astronomy_weather(place="東京")
「東京の今夜の空に何が見える？」              → sky_map_with_satellites(place="東京")
「火星の画像を見せて」                        → search_space_images(query="mars")
「米国初の宇宙望遠鏡は？」                    → reverse_lookup(category="宇宙望遠鏡", country="United States")
「天宮は今どこ？」                            → tiangong_now()
```

## 図の注記 figure/1（描画系ツールの応答）

描画系ツール（`solar_system_now` / `sat_ground_track` / `planetary_orbiter_track` / `planetary_rover_location_map` / `sky_map_with_satellites` / `solar_eclipse_series`）は、`structuredContent.figure` に **視点(view)・主天体の置き方(primary: 楕円は焦点であって中心ではない)・縮尺(scale)・円錐曲線(conic)・注記(notes)・自己検証(verify)・説明(caption)** を返します。`content` にも同じ注記が `### ⚠️ 図の注記` として入ります。

- **回答に図を説明するときは `figure.notes` を要約・言い換えせず、そのまま引用する**（「主天体は焦点」「対数縮尺」「地上軌道は投影」等の但し書きを落とすと図の誤読を招く）。
- **`conic.kind` が `hyperbola`/`parabola` のとき、その軌道は閉じていない**（遠日点なし）。「周回軌道」と説明しないこと。
- `verify.ok` が真なら、描いた画素から測った近点/遠点距離が数値と一致し、ラベルが線に被っていないことを意味します。
- `solar_eclipse_series` は**その観測地で太陽が地平線より上にある時間帯だけ**を描きます。全日食が地平線下なら図を返さず「見えません（最大高度 −67°）」と明示します（見えない食を図にすると誤解させるため）。
- 検査: `scripts/check-tools.py --figures`（注記が空・`verify.ok` が偽なら exit 1）。既定引数では通らない経路（`view="comet_orbit"`、可視の日食）も明示的に叩きます。

## 開発ワークフロー（検証ゲート）

```bash
uv run python -m compileall -q src/space_finder_mcp   # 構文
uv run python scripts/check-tools.py --dead-code      # デッドコード（0件を維持）
uv run python scripts/check-tools.py --offline        # ネットワーク全断で例外漏れ検査
uv run python scripts/check-tools.py --figures        # 描画系の図の注記(figure/1)を検査
uv run python scripts/check-tools.py                  # 全45ツール実呼び出し（数分、exit 1 で失敗）
```

**MCPサーバーはホットリロードなし** — `src/` 変更後はクライアント再起動が必要です。

## キャッシュ（API呼び出しと処理の削減）

| 層 | 対象 | 方式 | TTL |
|:--|:--|:--|:--|
| 不変アセット | Trekタイル / NASA画像資産 / 星空背景 | ディスク（`%LOCALAPPDATA%\Temp\space_finder_mcp\cache`） | 30日 |
| 揮発データ | RSS・打ち上げ・DONKI・STAC検索・ESO | メモリ | 10分 |
| 〃 | 天体観測用天気・メディア検索・ALMA/CADC | メモリ | 30分 |
| 〃 | APOD / NEO / EO Dashboard | メモリ | 1時間 |
| 〃 | データセット一覧・ジオコーディング・POWER・Wikidata | メモリ | 24時間 |
| 高コスト計算 | 日食（次の日食探索23秒／指定日7秒） | 緯度経度丸めキーでメモリ | 24時間 |
| 高コスト計算 | 探査機・彗星の Horizons 状態ベクトル（分単位キー）/ SBDB 軌道要素 | メモリ | 24時間（キーが1分ごとに更新） |

実測効果（1回目→2回目）: `planetary_orbiter_track`(LRO) 4.46s/73req → 1.07s/1req、`solar_eclipse_series` 26.08s → 0.69s、`satellite_status` 50.28s → 0.00s、`sky_map` 画像 992KB → 188KB（JPEG化）。

**エラー応答はキャッシュしません**（レート制限429等が固定化しない）。**現在位置系はキャッシュ対象外**です。

## 注意事項

1. **出典表示**: 結果には出典URLが含まれます。回答時は必ず引用元を表示してください（NASA / ESA / JAXA / ISRO / CSA / INPE / UK / CNSA / Wikidata など）。
2. **レート制限**: `DEMO_KEY` は 30リクエスト/時/IP の共有枠（`apod`・`neo_today`・`space_weather` で共有）。サーバー側で使用数を数えており、枠を使い切ると HTTP を出さずに回復目安を返し、429 を受けた場合は `Retry-After` を尊重します（`structuredContent.budget` に上限・使用数・残りを添付）。
3. **曖昧入力**: 衛星名などで候補が複数ある場合は推測せず、NORAD ID 付きの候補を提示して停止します。
4. **過去ミッション**: かぐや（SELENE）・あかつき等は「現在位置を表示できない」と正直に返します。
5. **描画エンジン**: `simple`（Pillow合成・学生向け視認性重視・JPEG・既定）と `accurate`（matplotlib・正確座標・PNG）。遠方探査機・彗星は線形縮尺では枠外のため自動的に `simple` を使用します。
6. **認証付きダウンロード非対応**: ESA Copernicus は検索とプレビューURLのみ（OAuth2 ダウンロードは行いません）。

## 参考リンク

- NASA Open APIs: https://api.nasa.gov/
- JPL Horizons: https://ssd.jpl.nasa.gov/horizons/
- CelesTrak: https://celestrak.org/
- ESA Copernicus Data Space: https://dataspace.copernicus.eu/
- JAXA Earth API: https://data.earth.jaxa.jp/
- リポジトリ: https://github.com/loosephoto/space-finder-mcp

## 更新履歴

- v0.26.0 — 描画系6ツールに `structuredContent.figure`（`figure/1`: 視点・主天体の置き方・縮尺・円錐曲線・注記・自己検証）を導入し、注記を数値から生成（LLM は要約せず引用）。彗星の軌道面ビュー `solar_system_now(view="comet_orbit")` を追加（e>1 は双曲線として描き分け）。`check-tools.py --figures` を新設（注記なし・`verify.ok` 偽で exit 1、既定引数で通らない経路も実行）。`verify` が画素から描画を測り直す仕組み（彗星=近点/遠点、日食=PA軸の明部長から食分、ローバー=マーカー画素から緯度経度、衛星=az/alt から画素位置）で実バグ3件を修正：日食図のパネルはみ出し・「最大食分」ラベルの固定・衛星ラベル枠による他衛星マーカーの隠れ。日食は現地日付をまたぐ食の切り詰めと、地平線下の見えない食の誤報告も修正（可視区間のみ描画、高度を注記）。

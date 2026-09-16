---
name: space-finder-mcp
description: 宇宙・天文・地球観測データを横断検索するMCPサーバー。NASA/ESA/JAXA/ISRO/CSA/INPE/UK/CNSA/CelesTrak/JPL/Wikidata/Open-Meteo を統合した47ツール。衛星画像・軌道マップ・日食パネル・月齢マップを画像で返し、構造化JSONも同時に提供。
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

## ツール一覧（47種）

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
| `astronomy_weather` | 天体観測に最適な夜間の時間帯を予報（雲量・視程・風速・降水・**月相・月明かり**）。日本国内は気象庁の**雨雲・降水画像**（解析雨量＋降水予報パネル＋ナウキャスト、雷の有無も確認）を添付（figure/1 対応） | Open-Meteo + JMA | 不要 |
| `constellation_now` | 指定地点・時刻で太陽・月・惑星の高度・方位・星座を計算（観測可否判断） | Skyfield + JPL de421 | 不要 |
| `astronomy_news` | 最新の天文ニュース・「今週の星空ガイド」を取得（観測/ニュース絞込可）。取得不可時は他ソースへ自動フォールバック | Sky & Telescope / Universe Today / NASA / Phys.org RSS | 不要 |
| `mars_rover_status` | 火星探査ローバー（キュリオシティ等）の現在の状況・天気・ソルを表示 | NASA Mars Weather | 不要 |
| `power_climate` | 任意地点の過去の気候・太陽エネルギー統計（気温・日射量・風速） | NASA POWER | 不要 |
| `eso_seeing` | ESO パラナル天文台（チリ, VLT）のリアルタイム大気コンディション（シーイング・可降水量・気象） | ESO ASM API | 不要 |
| `cadc_observations` | CADC（カナダ天文データセンター）の観測データ検索（HST・ジェミニ等） | CADC TAP | 不要(画像DLは一部要登録) |
| `alma_search` | ALMA（アルマ望遠鏡）科学アーカイブの観測データ検索（観測対象・座標・周波数帯・種別/分解能/QA・公開/要権限・実データ製品） | ALMA Science Archive (NAOJ, IVOA TAP) | 不要 |
| `radio_sources_now` | TART オープン電波望遠鏡が「いま観測できる電波源」（GNSS・静止衛星等）を仰角順に表示 | TART source catalog (NZ) | 不要 |
| `sky_map_with_satellites` | 指定地の空に太陽系の惑星と人工衛星を重ねた画像（matplotlib正確版=PNG/Pillow簡易版=JPEGを選択） | JPL de421+Skyfield / CelesTrak+SGP4 | 不要 |
| `solar_system_now` | 太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置俯瞰図（ハレー等の周期彗星とC/彗星・ボイジャー等の遠方天体まで対数縮尺で自動拡張表示）。`view="comet_orbit"` で彗星の軌道面ビュー（太陽＝焦点の楕円／e≥1 は双曲線の枝）。`comet` にカンマ区切りで複数（または `comet2`、最大4天体）指定すると **1彗星=1パネルの1枚画像**（パネルごとに軌道面・縮尺が異なる＝`figure.notes` に自動生成、`figure.kind=orbit_plane_set`、パネル別は `figure.panels[]`。一部の惑星の位置計算に失敗した場合は `structuredContent.planet_errors` に理由を記録し、`content` と `figure.notes` に失敗数を出します） | JPL DE421+Skyfield / JPL SBDB / JPL Horizons | 不要 |
| `solar_eclipse_series` | 日食（太陽が月に欠ける過程）の時系列パネル画像（7枚・食分と太陽高度・次回日食の自動検索=約4年(1400日)先まで・max_magnitude対応。**地平線下で見えない食は返さない**） | JPL DE421+Skyfield | 不要 |
| `moon_phase_map` | **月齢マップ**。`layout="calendar"`（既定）=1か月の日別格子、`layout="lunation"`=1朔望月（朔→朔）の時系列パネル（`days` 3〜12枚）。輝面の向きは太陽の位置角から計算（月齢からの決め打ちをしない）。朔・望・上弦・下弦の時刻を現地時間で併記 | JPL DE421+Skyfield | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | 宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象）。**NASA が枠切れ/障害なら認証不要の NOAA SWPC へ自動切替**（Kp・NOAAスケール・GOES X線・太陽風・陽子・警報・黒点。出典と切替理由を明記） | NASA DONKI → **NOAA SWPC** | 不要（SWPC）/キー任意（DONKI） |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `sat_ground_track` | 任意の人工衛星（ISS・ひので・ハッブル等）の現在位置と地上軌道を地球地図にプロットした画像を返す。CelesTrak TLE + Skyfield(SGP4) で真下の点・高度・速度を計算し、NASA Blue Marble 地図に軌道トレイルを重ねる | CelesTrak + Skyfield + Blue Marble | 不要 |
| `planetary_orbiter_track` | 任意の天体（月・火星・水星・タイタン等）を周回する探査機の現在位置と軌道トレイルを、その天体の地図にプロットした画像を返す。JPL Horizons の状態ベクトルを IAU 自転モデルで天体固定座標（緯度経度・高度）に変換し、NASA Trek の等角図法地図に重ねる。`span_deg=360`で天体全面表示にも対応。トレイルの計算に失敗した点は `structuredContent.trail_errors` に記録し、`content` と `figure.notes` に失敗数を出します | JPL Horizons + NASA Trek | 不要 |
| `planetary_rover_location_map` | 任意の天体面を移動する探査ローバーの現在地をその天体の地図中心に示した画像（走行経路・着陸点）。NASA MMGIS の位置データと NASA Trek の等角地図を合成。現状データは火星ローバー（Perseverance/Curiosity） | NASA MMGIS + Trek WMTS | 不要 |
| `weather_satellite_now` | GEO/LEO気象衛星19機の公開画像（ひまわり9号・GOES-18/19・Meteosat-12/11/10/9・FY-4B/2H/2G・GK-2A・INSAT-3DR/3DS・NOAA-20/-21・SNPP・Metop-B/C・FY-3D）。GEO=最新フレーム/LEO=日次全球合成、取得不可は理由コード | JMA/NOAA STAR/EUMETSAT WMS/NSMC/KMA/IMD/NASA GIBS | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等）。**全1,044件を走査**し query は一致度順（acronym 完全/前方一致 → 語境界 → 部分一致のみ）。失敗ページは failed_pages で報告 | WMO OSCAR | 不要 |
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
「ひまわり9号の最新雲画像」                → weather_satellite_now(satellite="ひまわり9号", band="visible")
「ひまわりの赤外線画像」                    → weather_satellite_now(band="infrared")  # 昼夜問わず雲頂
「世界の気象衛星の画像」                    → weather_satellite_now(satellite="NOAA-20")  # GEO/LEOプロバイダを衛星名で選択
「LROの現在位置を月面地図で」                → planetary_orbiter_track(body="moon", orbiter="lro")
「MROは火星のどこ？」                        → planetary_orbiter_track(body="mars", orbiter="mro")
「パーサヴィアランスの現在地を火星地図で」    → planetary_rover_location_map(body="mars", rover="perseverance")
「太陽系を上から見た図」「はやぶさ2は今どこ」 → solar_system_now(probe="はやぶさ2")
「ハレー彗星の軌道を見せて」                  → solar_system_now(comet="ハレー彗星", view="comet_orbit")
「紫金山・アトラスの軌道は？」                → solar_system_now(comet="C/2023 A3", view="comet_orbit")  # e>1 は双曲線の枝
「ハレーとC/2004 R2の軌道を並べて」            → solar_system_now(comet="ハレー彗星,C/2004 R2", view="comet_orbit")  # 1彗星=1パネル
「かぐやの月面落下地点は？」                      → planetary_orbiter_track(orbiter="かぐや", body="moon")  # 落点マップ（figure.kind=impact_site_map）
「アポロの着陸地点を月面図で」                    → planetary_orbiter_track(body="moon", sites="apollo")   # 6地点＋凡例（figure.kind=landing_site_map）
「アポロ11号の着陸地点は？」                      → planetary_orbiter_track(body="moon", sites="apollo11") # 局所図
「木星に衝突した彗星の地点は？」                  → planetary_orbiter_track(body="jupiter", sites="all")   # SL9 23破片（座標グリッド）
「金星の着陸地点は？」                            → planetary_orbiter_track(body="venus", sites="all")     # USGS/NASA Magellan 全球図（2:1 補正）
「タイタンのホイヘンス着陸点は？」                → planetary_orbiter_track(body="titan", sites="all")     # Cassini 全球図の局所図
「イオの地図を見せて」                            → planetary_orbiter_track(body="io", sites="map")        # 地点なし＝全球図のみ（kind=body_map）
「東京で見える次の日食を画像で」              → solar_eclipse_series(place="東京")  # 約4年先まで検索（東京の次は2030-06-01）。それでも無ければ date 案内
「2030年の日食を東京で」                      → solar_eclipse_series(place="東京", date="2030-06-01")
「今月の月齢マップを見せて」              → moon_phase_map(place="東京")  # 1か月の月齢カレンダー（既定・layout="calendar"）
「次の朔望月の満ち欠けを画像で」          → moon_phase_map(layout="lunation", days=8)  # 朔→朔の8枚パネル
「2026年9月の満月はいつ？」              → moon_phase_map(date="2026-09")  # 朔・望・上弦・下弦の時刻を現地時間で返す
「9年前のあの日の月齢は？」              → moon_phase_map(date="2017-08-06")  # 日付だけで過去も可（暦はローカル計算）
「今夜の観測に向く時間帯は？」                → astronomy_weather(place="東京")  # 日本国内は気象庁の雨雲・降水画像も同時に返る（figure.notes を要約せず引用）
「東京の今夜の空に何が見える？」              → sky_map_with_satellites(place="東京")
「いま宇宙天気はどう？」                      → space_weather()  # NASA が枠切れなら NOAA SWPC に自動切替（出典が変わる）
「最近の太陽フレアは？」                      → space_weather(kind="flare")
「ロシアの気象衛星の運用状況は？」            → satellite_status(query="meteor")  # 全件走査＋一致度順で Meteor-M が上位に来る
「火星の画像を見せて」                        → search_space_images(query="mars")
「米国初の宇宙望遠鏡は？」                    → reverse_lookup(category="宇宙望遠鏡", country="United States")
「天宮は今どこ？」                            → tiangong_now()
```

## 図の注記 figure/1（描画系ツールの応答）

描画系ツール（`solar_system_now` / `sat_ground_track` / `planetary_orbiter_track` / `planetary_rover_location_map` / `sky_map_with_satellites` / `solar_eclipse_series` / `moon_phase_map` / `astronomy_weather`〔雨雲・降水画像を返すとき〕）は、`structuredContent.figure` に **視点(view)・主天体の置き方(primary: 楕円は焦点であって中心ではない)・縮尺(scale)・円錐曲線(conic)・注記(notes)・自己検証(verify)・説明(caption)** を返します。`content` にも同じ注記が `### ⚠️ 図の注記` として入ります。

- **回答に図を説明するときは `figure.notes` を要約・言い換えせず、そのまま引用する**（「主天体は焦点」「対数縮尺」「地上軌道は投影」等の但し書きを落とすと図の誤読を招く）。
- **`conic.kind` が `hyperbola`/`parabola` のとき、その軌道は閉じていない**（遠日点なし）。「周回軌道」と説明しないこと。
- `verify.ok` が真なら、描いた画素から測った近点/遠点距離が数値と一致し、ラベルが線に被っていないことを意味します。
- 地図タイルが取れなかった場合は `figure.notes` に「地図タイル N/M 枚を取得できませんでした（図の該当領域は背景色のまま）」が**数値から生成**されます（欠けを黙って捨てると「地図に無い＝何も無い」と誤読されるため）。
- `figure.kind` は描画の種類を示します（`heliocentric_overview` / `orbit_plane` / `orbit_plane_set` / `ground_track_map` / `sky_view` / `eclipse_panels` / **`moon_phase_calendar`** / **`moon_phase_lunation`** / **`impact_site_map`**（落点などの地点マーカーを天体面地図に描いた図） / **`landing_site_map`**（着陸地点。複数地点は番号＋凡例、`verify.markers[]` に地点ごとの検証））。
- `verify.periapsis_check` は `equality`（近点距離を等値で検査）か `upper_bound`（**近点が画面上で分解できない**ため上界だけを検査）です。**超長距離の楕円では近日点が「誇張した主天体の円盤」の内側に入り、曲線の端の画素は近点ではなく円盤の縁になる**ので、`upper_bound` に切り替わります（`periapsis_resolvable: false` ＋ `periapsis_unresolved_reason`）。この場合も主天体を楕円の中心に置く誤りは検出できます（上界＋遠点側の等値検査）。**`periapsis_resolvable` が偽の図を説明するときは、注記にある「この縮尺では図から確認できない」旨を落とさないこと。**
- `moon_phase_map` は**月齢・照度・満ち欠けの向きを自称する図**です。`verify` は**位置角（PA）軸上で明暗境界線の位置を測り直した照度**との一致（許容 0.05。細い三日月は境界線が1〜2画素幅になり丸め誤差が残る）で、走査軸が申告した PA 方向なので**向きが違えば一致しません**。図の月は模式図（実写ではなく、月の海は乱数・クレーターや秤動・地球照は描かない）で、**天の北を上・東を左に置いた見え方**（地平線からの見え方ではない）です。「朔・望の日は正午時点では前後になる」等の食い違いは `figure.notes` に**数値から生成**してあります。
- `solar_eclipse_series` は**その観測地で太陽が地平線より上にある時間帯だけ**を描きます。全日食が地平線下なら図を返さず「見えません（最大高度 −67°）」と明示します（見えない食を図にすると誤解させるため）。
- 検査: `scripts/check-tools.py --figures`（注記が空・`verify.ok` が偽なら exit 1）。既定引数では通らない経路（`view="comet_orbit"`、可視の日食）も明示的に叩きます。

## 開発ワークフロー（検証ゲート）

```bash
uv run python -m compileall -q src/space_finder_mcp   # 構文
uv run python scripts/check-tools.py --dead-code      # デッドコード（0件を維持）
uv run python scripts/check-tools.py --offline        # ネットワーク全断で例外漏れ検査
uv run python scripts/check-tools.py --figures        # 描画系の図の注記(figure/1)を検査
uv run python scripts/check-tools.py                  # 全47ツール実呼び出し（数分、exit 1 で失敗）
```

**MCPサーバーはホットリロードなし** — `src/` 変更後はクライアント再起動が必要です。

## キャッシュ（API呼び出しと処理の削減）

| 層 | 対象 | 方式 | TTL |
|:--|:--|:--|:--|
| 不変アセット | Trekタイル / NASA画像資産 / 星空背景 | ディスク（`%LOCALAPPDATA%\Temp\space_finder_mcp\cache`） | 30日 |
| 揮発データ | RSS・打ち上げ・STAC検索・ESO | メモリ | 10分 |
| 〃 | DONKI（**カテゴリ単位**＝種別を変えても取り直さない。`DEMO_KEY` は4エンドポイント/回を消費）/ NOAA SWPC（フォールバック時に7項目） | メモリ | 10分 |
| 〃 | 天体観測用天気・メディア検索・ALMA/CADC | メモリ | 30分 |
| 〃 | APOD / NEO / EO Dashboard | メモリ | 1時間 |
| 〃 | データセット一覧・ジオコーディング・POWER・Wikidata | メモリ | 24時間 |
| 高コスト計算 | 日食（次の日食探索28秒＝1400日窓／指定日1秒） | 緯度経度丸めキーでメモリ | 24時間 |
| 高コスト計算 | OSCAR 全カタログ（35ページ・初回38.5秒。1ページ30件固定のAPI） | **メモリ＋ディスク**（プロセス再起動後も即時） | 24時間 |
| 高コスト計算 | 探査機・彗星の Horizons 状態ベクトル（分単位キー）/ SBDB 軌道要素 | メモリ | 24時間（キーが1分ごとに更新） |

実測効果（1回目→2回目）: `planetary_orbiter_track`(LRO) 4.46s/73req → 1.07s/1req、`solar_eclipse_series` 28.4s → 0.7s、`satellite_status` 50.28s → 0.00s、`sky_map` 画像 992KB → 188KB（JPEG化）。

**エラー応答はキャッシュしません**（レート制限429等が固定化しない）。**現在位置系はキャッシュ対象外**です。

## 注意事項

1. **出典表示**: 結果には出典URLが含まれます。回答時は必ず引用元を表示してください（NASA / ESA / JAXA / ISRO / CSA / INPE / UK / CNSA / Wikidata など）。
2. **レート制限**: `DEMO_KEY` は 30リクエスト/時/IP の共有枠（`apod`・`neo_today`・`space_weather` で共有）。`space_weather` は枠切れ時に**認証不要の NOAA SWPC へ自動切替**するので、宇宙天気だけは 429 中でも返ります（`space_weather` 以外の NASA 系は待機が必要）。サーバー側で使用数を数えており、枠を使い切ると HTTP を出さずに回復目安を返し、429 を受けた場合は `Retry-After` を尊重します（`structuredContent.budget` に上限・使用数・残りを添付）。
3. **曖昧入力**: 衛星名などで候補が複数ある場合は推測せず、NORAD ID 付きの候補を提示して停止します。
4. **過去ミッション**: かぐや（SELENE）・あかつき等は「現在位置を表示できない」と正直に返します。落点が公表されている機体（かぐや＝南緯65.5°／東経80.4° Gill クレータ付近、2009-06-10 18:25 UTC）は落点を `structuredContent.impact_site` に出典つきで返します（「落点は判明しているか」に MCP だけで答えられます）。和名（かぐや/あかつき）も英語キーへ展開してから判定します。
5. **描画エンジン**: `simple`（Pillow合成・学生向け視認性重視・JPEG・既定）と `accurate`（matplotlib・正確座標・PNG）。天体・記号の色は `img_common.BODY_COLORS`（惑星・月・太陽の実物色）/ `SYMBOL_COLORS`（環・縞・極冠・小惑星・彗星）が単一の出典で、`sky_map_with_satellites` と `solar_system_now` の両エンジンが同じ値を参照し、`accurate` の凡例は実際に描いたマーカーだけを色コード付きで出します。遠方探査機・彗星は線形縮尺では枠外のため自動的に `simple` を使用します。
6. **名前解決のフォールバック**: 天体名・衛星名は `name_common.py` の共通段階で解決します（内蔵テーブル → 表記ゆれ → 和名→英語名 → Sesame/CDS で名前→座標 → 候補提示して停止）。`cadc_observations` は内蔵テーブルに無い名前（M104/Sombrero/HL Tau/和名）を SIMBAD で解決、`satellite_status` と `sat_tle` は和名（ひまわり/ひので/だいち/宇宙ステーション等、`JA_ALIASES` 103キー）を英語名・NORAD ID に展開します（現役機は `celestrak.WELL_KNOWN` に実測 NORAD ID 登録済み＝オフライン解決、番号なしファミリー名は候補提示）。解決できないときは推測せず候補を提示します。
7. **応答にキーを載せない**: `apod`/`neo_today`/`space_weather` のエラー文字列は requests 由来で `api_key=<値>` を含むため、外向けテキストは `nasa_budget.redact()` を通して伏せ字化します（`budget.key` は `DEMO_KEY`/`custom` のみ）。
8. **メディアのリンク**: 生成画像は `%LOCALAPPDATA%\Temp\space_finder_mcp\out` に保存し、`content` の**先頭行**に `🖼️ [生成した画像を開く（…）](file:///…)` を出します（`structuredContent.image_path` に実パス）。検索系は各項目の直後に `🖼️/🎧/🎬 [◯◯を開く: タイトル](URL)` を出します。CLI系・Android系ハーネス（codex / opencode）はインライン画像を描画しないため、回答時はこのリンクを必ず提示してください（アイコン: 🖼️画像 / 🎧音声 / 🎬動画）。
7. **認証付きダウンロード非対応**: ESA Copernicus は検索とプレビューURLのみ（OAuth2 ダウンロードは行いません）。

## 参考リンク

- NASA Open APIs: https://api.nasa.gov/
- JPL Horizons: https://ssd.jpl.nasa.gov/horizons/
- CelesTrak: https://celestrak.org/
- ESA Copernicus Data Space: https://dataspace.copernicus.eu/
- JAXA Earth API: https://data.earth.jaxa.jp/
- リポジトリ: https://github.com/loosephoto/space-finder-mcp

## 更新履歴

- v0.30.1 — **衛星カタログの全件走査・一致度順と宇宙天気のフォールバック**: `satellite_status` は先頭300件しか見ておらず、`query="meteor"` が「Meteorological（気象）」を含む DMSP/COSMIC 等を92件拾って Meteor-M が埋もれていた。**全1,044件（35ページ）を並列取得**してacronym の完全/前方一致 → 名称の語境界 → 部分一致のみ、の**一致度順**に並べ替え、弱い一致は件数を分けて表示。失敗ページは `failed_pages` で報告し、不完全なカタログはディスクに固定しない（初回38.5秒 → 以後0.07秒、プロセス再起動後も即時）。`space_weather` は NASA が 429/障害のとき**認証不要の NOAA SWPC へ自動切替**（Kp・NOAAスケール・GOES X線・太陽風・陽子・警報・黒点。`source`=`NOAA SWPC`・`fallback`・`nasa_reason` で出典を明記し、取れなかった項目は `failed`）。検証: 全47ツール exit 0／--dead-code 0／--fuzz 例外漏れ0／--offline exit 0／--figures 描画系8／unittest 15件 OK。

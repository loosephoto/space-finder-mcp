---
name: space-finder-mcp
description: 宇宙・天文・地球観測データを横断検索するMCPサーバー。NASA/ESA/JAXA/ISRO/CSA/INPE/UK/CNSA/CelesTrak/JPL/Wikidata/Open-Meteo と MAST（JWST・ハッブル等の観測アーカイブ）・NASA GCN（過渡天体速報）・JPL CNEOS（火球・小惑星接近・衝突リスク）および学術文献（OpenAlex/Crossref/NTRS/JAXAリポジトリ/J-STAGE/CiNii）および SIMBAD / VizieR（CDS の天体カタログ）・NASA Exoplanet Archive（系外惑星）・みちびき（QZSS の精密軌道）を統合した66ツール。衛星画像・軌道マップ・日食パネル・月齢マップを画像で返し、構造化JSONも同時に提供。
category: space
---

# Space Finder MCP Server

## 目的

宇宙・天文・地球観測の**公開API（大半は認証不要）**を1つの MCP サーバーで横断検索するツール群です。画像生成（地球地図上の衛星軌道、天体地図上の周回機・ローバー、太陽系俯瞰、日食時系列パネル、星空マップ）を含み、各ツールは人間向け表示（`content`）と LLM向け純粋JSON（`structuredContent`）を同時に返します。

> ⚠️ **出力のうち「保証される部分」と「モデル次第の部分」** — `structuredContent`（JSON の数値・座標・出典・`figure.notes`）と `content` の画像・リンクは**サーバー側で決まり、モデルには依存しません**（同じ引数・同じ時刻ならどのモデルから呼んでも同じ）。それを**読みやすい回答文に組み立てる工程はあなた（ホスト側モデル）の裁量**で、構成・語り口・要約の粒度はモデルや設定で変わります。回答を書くときは常に JSON を根拠にしてください（詳細は「注意事項 10」）。

## 前提条件

- **Python 3.11+** / **uv**（`uv sync` で依存解決。Node.js 不要）
- 初回起動時に Skyfield が JPL 天体暦 `de421.bsp`（約16MB）を自動取得
- APIキーは**すべて任意**（`NASA_API_KEY` 未設定でも `DEMO_KEY` で動作。30リクエスト/時/IP の共有枠）

## 対応データソース

- 宇宙機関: NASA（APOD/NEO/DONKI/POWER/Image&Video Library）、ESA Copernicus、JAXA Earth、ISRO、CSA、INPE、UK EO DataHub、CNES、DLR EOC（ドイツ EOC/DFD STAC）、Roscosmos（ロシア オープンデータ STAC）、CNSA（NSMC/CNSA-GEO/CRESDA）、EO Dashboard（NASA×ESA×JAXA）
- 天文・軌道: JPL（DE421暦/SBDB/Horizons）、JPL CNEOS（火球・小惑星接近・Sentry 衝突リスク / ssd-api.jpl.nasa.gov）、CelesTrak（NORAD TLE。遮断時は公開ミラー tle.ivanstanojevic.me / db.satnogs.org へ自動フォールバック）、WMO OSCAR、ESO ASM、ALMA Science Archive、CADC、TART、Wikidata（SPARQL）、Open-Meteo、NASA MMGIS / NASA Trek（天体地図）
- 打ち上げ: Launch Library 2
- 図法・画像素材: NASA Blue Marble、NASA Trek WMTS（月・火星・水星・タイタン・ベスタ・ケレス）

## ツール一覧（66種）

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
| `dlr_collections` | ドイツDLRの地球観測コレクション一覧（Sentinel-2 MAJA・EnMAP・TROPOMI等） | DLR EOC STAC | 不要 |
| `dlr_search` | ドイツDLRの地球観測データをSTAC検索（領域・日時・雲量） | DLR EOC STAC | 不要（検索・プレビューのみ） |
| `roscosmos_collections` | ロシアRoscosmosオープンデータのコレクション一覧（Elektro-L・Arktika-M・Meteor-M） | Roscosmos open data STAC | 不要 |
| `roscosmos_search` | ロシアの気象衛星の全球画像（Elektro-L・Meteor-Mモザイク等）を検索 | Roscosmos open data STAC | 不要 |
| `sat_tle` | 全世界の衛星の軌道要素(TLE)を取得（ISS・ハッブル・気象衛星等） | CelesTrak (NORAD) | 不要 |
| `uk_stac_collections` | 英国EO DataHubのコレクション一覧 | UK EO DataHub STAC | 不要 |
| `uk_stac_search` | 英国EO DataHubの衛星・気候データをSTAC検索 | UK EO DataHub STAC | 不要 |
| `cnes_status` | フランスCNESのポータル（THEIA/GEODES）到達状態・概要 | CNES THEIA/GEODES | 不要(ダウンロードは要登録) |
| `astronomy_weather` | 天体観測に最適な**暗い**時間帯を予報（**薄明＝太陽高度 `sun_alt_max`（既定 -15°）で切る**・雲量・視程・風速・降水・**月相・月明かり**）。日本国内は気象庁の**雨雲・降水画像**（解析雨量＋降水予報パネル＋ナウキャスト、雷の有無も確認）を添付（figure/1 対応） | Open-Meteo + JMA | 不要 |
| `constellation_now` | 指定地点・時刻で太陽・月・惑星の高度・方位・星座を計算（観測可否判断） | Skyfield + JPL de421 | 不要 |
| `astronomy_news` | 最新の天文ニュース・「今週の星空ガイド」を取得（観測/ニュース絞込可）。取得不可時は他ソースへ自動フォールバック | Sky & Telescope / Universe Today / NASA / Phys.org RSS | 不要 |
| `mars_rover_status` | 火星探査ローバー（キュリオシティ等）の現在の状況・天気・ソルを表示 | NASA Mars Weather | 不要 |
| `power_climate` | 任意地点の過去の気候・太陽エネルギー統計（気温・日射量・風速） | NASA POWER | 不要 |
| `eso_seeing` | ESO パラナル天文台（チリ, VLT）のリアルタイム大気コンディション（シーイング・可降水量・気象） | ESO ASM API | 不要 |
| `cadc_observations` | CADC（カナダ天文データセンター）の観測データ検索（HST・ジェミニ等） | CADC TAP | 不要(画像DLは一部要登録) |
| `gcn_alerts` | **NASA GCN（General Coordinates Network）の過渡天体速報**。GRB・X線新星・重力波などの **GCN Circular** を期間（`days` 1〜60）／キーワード（`query`）で新しい順に一覧し、`circular_id` で1件の本文（投稿者・観測時刻・本文）まで返す。一覧は公開アーカイブの HTML が既定（サイト内部の JSON ルートは 403 を返すことがあるためフォールバック）。⚠️ 機械可読の Notices は Kafka 配信のため対象外 | NASA GCN (gcn.nasa.gov) | 不要 |
| `mast_observations` | **MAST 観測データ検索**（JWST・ハッブル・TESS 等の科学アーカイブ）。JWST・ハッブル(HST)・TESS・Kepler・GALEX 等を、天体名（和名可・Sesame で座標解決）／座標コーン／装置（部分一致）／データ種別／観測日で検索。観測ID・装置・観測日・校正レベル・フィルタ・`Mast.Caom.Products` による FITS のダウンロードURL（`include_products=true`）まで返す。`preview_image=true` で先頭観測のプレビュー画像をインライン表示（🖼️リンク先行）。校正用露出（BIAS/DARK）は既定で除外。⚠️ MAST は混雑時に1クエリ 60 秒級（実測）＝結果は30分キャッシュ | MAST Mashup API | 不要 |
| `alma_search` | ALMA（アルマ望遠鏡）科学アーカイブの観測データ検索（観測対象・座標・周波数帯・種別/分解能/QA・公開/要権限・実データ製品） | ALMA Science Archive (NAOJ, IVOA TAP) | 不要 |
| `space_literature_search` | **惑星科学・宇宙の一次文献（論文・技術報告）を横断検索**して根拠（DOI付き）を返す。OpenAlex（要旨・被引用数・OAリンク）・Crossref・NASA NTRS（技術報告＋PDF）・JAXAリポジトリ・J-STAGE・CiNii Research・Zenodo・DataCite を1回で横断し、DOI/タイトルの重複を統合。**日本語クエリは語彙辞書＋和名テーブルで英語語へ置換してから英語圏ソースへ投げる**（実測: OpenAlex/Crossref は日本語クエリだと無関係な文献を返すため）。`sort`（関連度/被引用数/年）・`year_from`/`year_to`・`min_citations`・`open_access_only`・`planetary_only`（惑星科学概念に限定）・`sources` で絞り込み。⚠️ 返すのは**文献（書誌）**で観測データではない（観測は `mast_observations` / `alma_search` / `cadc_observations`） | OpenAlex / Crossref / NASA NTRS / JAXAリポジトリ / J-STAGE / CiNii / Zenodo / DataCite | 不要（`ADS_API_KEY`・`S2_API_KEY`・`WOS_API_KEY` を設定すると ADS・Semantic Scholar・WoS も使う） |
| `planetary_evidence` | **天体（惑星・衛星・小天体・探査機）の文献的な裏づけをまとめて返す**。①名前解決（Sesame/CDS で和名→英語名→座標）と、②その天体の文献を**英語圏＋日本語の両方**から集めて提示（既定は被引用数順。**惑星科学概念で絞った OpenAlex を上位に置く**＝実測「火星」で MOLA / OMEGA-Mars Express / ALH84001 が上位に来る）。⚠️ 太陽系天体は時刻で位置が変わるため固定座標を持たない旨を `caveats` に明記 | OpenAlex / Crossref / NASA NTRS / JAXAリポジトリ / J-STAGE / CiNii ＋ Sesame/CDS | 不要 |
| `radio_sources_now` | TART オープン電波望遠鏡が「いま観測できる電波源」（GNSS・静止衛星等）を仰角順に表示 | TART source catalog (NZ) | 不要 |
| `sky_map_with_satellites` | 指定地の空を**地上から見上げた**画像（既定=地上視点パノラマ: Hipparcos実星表の恒星・天の川・街並み・方位目盛り・惑星・人工衛星。太陽高度で昼/夕方/朝焼け/薄明/夜の空色が変わり、**今は見えない天体は○で位置を示す**。`span`/`az0` で視野、`lp` で光害、`show` で依頼表示。従来の天頂中心図は `engine="simple"`/`"accurate"`） | JPL de421+Skyfield / Hipparcos(VizieR) / CelesTrak+SGP4 | 不要 |
| `solar_system_now` | 太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置俯瞰図（ハレー等の周期彗星とC/彗星・ボイジャー等の遠方天体まで対数縮尺で自動拡張表示）。`view="comet_orbit"` で彗星の軌道面ビュー（太陽＝焦点の楕円／e≥1 は双曲線の枝）、`view="asteroid_orbit"` で指定した小惑星の閉じた楕円全体と現在位置を表示（SBDBの軌道要素によるケプラー二体伝播・1天体ずつ）、`view="apparition"` で彗星の**見え方チャート**（日心/地心距離・予想光度・太陽離角の時系列。`days` で期間、1天体ずつ）、`route=True` で**彗星の通過経路（軌道）を俯瞰図へ破線で重ねる**（近日点・遠日点は◇＋日付。**近日点の日付は SBDB の2体近似と JPL Horizons の n 体解を併記**し＝`marks[0].date_nbody`、差は `nbody_diff_days`（SBDB の要素は古いエポックの接触軌道なので摂動の大きい彗星では数か月ずれる。実測 ハレー彗星 163.8日）。n 体解が取れないときは `nbody_error` に理由を入れ、注記にも出す。対数縮尺では線の形が実際の楕円と一致しない旨を注記に自動生成、`engine="accurate"` の線形版は枠に収まれば自動維持＝形が本当の軌道。近日点が誇張した太陽円盤の内側なら目印を描かず「図からは確認できない」を注記・`verify` は破線と◇の画素を測り直す）。`range_au` で**表示範囲**を指定でき（**数値 AU・天体名・"fit" のいずれも可**。名は長半径×1.08 で解決し `structuredContent.range_resolved` と `figure.notes` に決め方を数値で出す。"fit" は指定天体が全部入る範囲）、`range_au=10` なら土星より内側だけを拡大表示（0.5 AU 未満はエラー、0=自動）。範囲外の天体・目印・軌道の円は描かず `figure.notes` と`structuredContent.out_of_range` に数値付きで列挙し、線形版の実測縮尺は `figure.scale.px_per_AU` に出る。`comet` にカンマ区切りで複数（または `comet2`、最大4天体）指定すると **1彗星=1パネルの1枚画像**（パネルごとに軌道面・縮尺が異なる＝`figure.notes` に自動生成、`figure.kind=orbit_plane_set`、パネル別は `probe` を指定した図では**「1光日」（光が24時間で進む距離＝173.1446 AU＝25,902,068,371 km）を破線の円で描き**（探査機の図では表示範囲を1光日まで自動拡張）、1光日に達していない探査機**ごとに投影での1光日リング・到達時の方向（◇＝黄経/黄緯）・到達予測日**を出す（`light_day.projected_rings` と `figure.verify.light_day.rings` に各リングを列挙）（俯瞰図は正射影なので真距離の円と投影の円は別物＝両者の半径を `figure.notes` に数値で出し、黄緯が小さいときは「図上でほぼ重なる」旨も出す。`probes[].light_days` / `light_hours` / `to_light_day_au` / `light_day_eta_date` / `light_day_eta_method`（＝日心視線速度による線形外挿。実測 ボイジャー1号 0.9925 光日・2027-02-03＝Horizons の n 体解と一致）は日心距離ベースで統一。範囲外なら `light_day.ring_why` に理由、`figure.verify.light_day` に破線・ラベル文字・マーカーの画素検証）。`figure.panels[]`。一部の惑星の位置計算に失敗した場合は `structuredContent.planet_errors` に理由を記録し、`content` と `figure.notes` に失敗数を出します） | JPL DE421+Skyfield / JPL SBDB / JPL Horizons | 不要 |
| `solar_eclipse_series` | 日食（太陽が月に欠ける過程）の時系列パネル画像（7枚・食分と太陽高度・次回日食の自動検索=約4年(1400日)先まで・max_magnitude対応。**地平線下で見えない食は返さない**） | JPL DE421+Skyfield | 不要 |
| `moon_phase_map` | **月齢マップ**。`layout="calendar"`（既定）=1か月の日別格子、`layout="lunation"`=1朔望月（朔→朔）の時系列パネル（`days` 3〜12枚）。輝面の向きは太陽の位置角から計算（月齢からの決め打ちをしない）。朔・望・上弦・下弦の時刻を現地時間で併記 | JPL DE421+Skyfield | 不要 |
| `space_calendar` | **宇宙・天文イベントカレンダー**（月グリッドの画像＋`structuredContent.events`）。打ち上げ（LL2・`net_precision` が Day/Hour/Minute/Second の行だけを日付セルに置き、日付未定は本文へ）・天文現象（Skyfield ローカル計算）・公開イベント（国立天文台＋JAXA の施設一般公開・特別公開＝ファン!ファン!JAXA! と宇宙科学研究所。告知済みのみ・取得日を窓に毎日取り直し）・自分の予定を重ねる。**read-through**: 要求月 M に対して窓 [M-1, M+2] を蓄積ストアで確保し、未取得・期限切れの月だけ取得（2回目以降は API 0 回・実測 0.4 秒） | Launch Library 2 + Skyfield + 国立天文台 + JAXA + nasa.gov | 不要 |
| `calendar_events` | 蓄積済みイベント一覧（図もAPIも無しの軽い経路） | 蓄積ストア | 不要 |
| `calendar_event_add` | 自分の予定を追加（`2026-10-24`/`10月24日`・時刻・終了日・毎日/毎週/毎月/毎年）。**フローティングなローカル日時**で保存（place を変えても動かない・外部送信なし） | ローカル（`%LOCALAPPDATA%\space-finder-mcp`） | 不要 |
| `calendar_event_remove` | 予定の削除（冪等・同名複数は候補提示で停止） | ローカル | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | 宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象）と**予報**（`kind="forecast"`: フレア発生確率 C/M/X・活動領域別確率・NOAA 3日予報。**DONKI は予報を持たないので常に SWPC**）。**DONKI が障害/URL変更なら認証不要の NOAA SWPC へ自動切替**（Kp・NOAAスケール・GOES X線・フレアイベント（直近7日）・太陽風・陽子・警報・黒点。出典と切替理由を明記） | NASA DONKI（CCMC）→ **NOAA SWPC**／予報は **NOAA SWPC** | 不要 |
| `fireball_reports` | **火球（大気圏突入）の観測記録**（`days` 1〜1825・`min_impact_energy_kt`・`limit`・`require_location`）。緯度経度・突入高度・衝突エネルギー(kt)・放射エネルギー(10^10 J)・突入速度（vx,vy,vz から算出）。広島型原爆(約15kt)との比を併記。⚠️ 隕石の回収ではなく**突入の観測**。**呼ぶと結果がカレンダーへ蓄積**（`calendar_stored`） | NASA/JPL CNEOS Fireball Data | 不要 |
| `neo_close_approach` | **小惑星・彗星の地球接近**（`days` 1〜365・`max_distance_au`・`hazardous_only`・`kind`＝`"all"`(既定＝小惑星＋彗星)/`"asteroid"`/`"comet"`）。**CAD の既定 `neo=true` は小惑星のみ**なので、彗星は `comet=true` の別照会で取り日付順に統合する（`totals_by_kind`・`results[].object_type`）。地心距離を au / km / 月距離(LD) で併記、既知の直径（無ければ H とアルベド0.14 から**推定**。彗星は H が無く不明）と不確かさ(3σ)。⚠️ 接近≠衝突・時刻は **TDB**（UTC と最大約1分差）。**呼ぶと結果がカレンダーへ蓄積**（`calendar_stored`） | NASA/JPL CNEOS SBDB CAD | 不要 |
| `impact_risk` | **将来の衝突リスク**（JPL Sentry）。`min_probability` 以上の天体を確率順に（確率＋「何回に1回」・パレルモスケール・想定時期）。`designation`（**仮符号/番号のみ・和名は400で拒否**）で VI 一覧まで。**削除済みは HTTP 200 + `error`**。日付が無い（数十年〜百年の幅）ので**カレンダーには置かない** | NASA/JPL CNEOS Sentry | 不要 |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `sat_ground_track` | 任意の人工衛星（ISS・ひので・ハッブル等）の現在位置と地上軌道を地球地図にプロットした画像を返す。CelesTrak TLE + Skyfield(SGP4) で真下の点・高度・速度を計算し、NASA Blue Marble 地図に軌道トレイルを重ねる | CelesTrak + Skyfield + Blue Marble | 不要 |
| `planetary_orbiter_track` | 任意の天体（月・火星・水星・金星・タイタン・ベスタ・ケレス・イオ・エウロパ・ガニメデ・カリスト・木星・土星・天王星・海王星・冥王星・ベンヌ・リュウグウの18天体）を周回する探査機の現在位置と軌道トレイルを、その天体の地図にプロットした画像を返す。JPL Horizons の状態ベクトルを IAU 自転モデルで天体固定座標（緯度経度・高度）に変換し、NASA Trek の等角図法タイル／**各天体の1枚の全球等角図**（USGS/NASA モザイク・**Hubble OPAL**（気体惑星・観測年を注記）・New Horizons・OSIRIS-REx）／全球画像が無い天体は緯度経度グリッドに重ねる（`sites="map"` で全球図のみも返す）。`span_deg=360`で天体全面表示にも対応。トレイルの計算に失敗した点は `structuredContent.trail_errors` に記録し、`content` と `figure.notes` に失敗数を出します | JPL Horizons + NASA Trek | 不要 |
| `planetary_rover_location_map` | 任意の天体面を移動する探査ローバーの現在地をその天体の地図中心に示した画像（走行経路・着陸点）。火星は NASA MMGIS の位置データと NASA Trek の等角地図を合成。**同じ天体にあるほかの人工物着陸地点は黄枠●で重ねる**（`show_sites=false` で非表示。衝突クレーター・衝突痕は着陸地点ではないので重ねない）。**フォボス（MMX / IDEFIX）にも対応** — 着陸地点は未公表（2027〜2028 年に選定）なので `site_lat`/`site_lon`（東向き正）または `site_lon_w`（西向き正・IAU 表記）で任意の場所を描き、未指定なら**着陸候補条件の緯度帯 ±30°**を緑枠で明示（着陸地点そのものではない）。IAU/U.S. Gazetteer に着陸地点が公表されれば自動検出（`landing_site_status`）。**月にも対応** — `body="moon"` でアポロ11〜17号・ルノホート1/2号・玉兔号/玉兔2号を **2 段構え**（左: Trek の月面地図に着陸地点と近傍着陸地点／右: **メートル単位の局地サイトプラン**）で表示。LM・ALSEP・LRRR・LRV 最終駐車地点・アメリカ国旗を**一次情報が公開する距離＋方位だけ**から求めた位置で描く。**位置の公開値がない機材（例: アポロ11号の旗）は図に描かず**凡例に「位置の公開値なし」と記す | NASA MMGIS + Trek WMTS ＋ USGS 公式全球図 + IAU Gazetteer ＋ **NASA NSSDCA（月面遺跡座標）** ＋ **NASA ALSJ（旗・LRV 最終駐車地点）** | 不要 |
| `weather_satellite_now` | GEO/LEO気象衛星19機の公開画像（ひまわり9号・GOES-18/19・Meteosat-12/11/10/9・FY-4B/2H/2G・GK-2A・INSAT-3DR/3DS・NOAA-20/-21・SNPP・Metop-B/C・FY-3D）。GEO=最新フレーム/LEO=日次全球合成、取得不可は理由コード | JMA/NOAA STAR/EUMETSAT WMS/NSMC/KMA/IMD/NASA GIBS | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等）。**全1,044件を走査**し query は一致度順（acronym 完全/前方一致 → 語境界 → 部分一致のみ）。失敗ページは failed_pages で報告 | WMO OSCAR | 不要 |
| `qzss_status` | みちびき（QZSS）の精密軌道・補強信号データ。SP3の無効座標は除外し、`structuredContent.invalid_satellites` に記録 | 内閣府 QZSS 公開アーカイブ API | 不要（非商用利用のみ） |
| `cnsa_status` | 中国CNSA系衛星データポータル（風雲/NSMC・高分/CNSA-GEO・CBERS/CRESDA）の到達状態・概要＋認証不要の代替経路 | CNSA各公式ポータル | 不要(ダウンロードは要登録) |
| `tiangong_now` | 天宮（Tiangong）中国宇宙ステーションの現在位置（SGP4伝播＋Googleマップ表示） | CelesTrak TLE + SGP4 | 不要 |

## セットアップ

### 1. キー（任意）

```bash
cp .env.example .env     # NASA_API_KEY=... を記入（.gitignore 済み・コミット禁止）
```
優先順位は **MCPクライアントの env > リポジトリ直下の .env**。どちらも無ければ `DEMO_KEY`。

**学術文献の任意キー**（`space_literature_search` / `planetary_evidence`）:
`ADS_API_KEY`（NASA ADS・無料トークン）/ `S2_API_KEY`（Semantic Scholar）/ `WOS_API_KEY`（Clarivate Web of Science。
無料枠は 1req/s・50req/日・被引用数なし）。**未設定でも OpenAlex / Crossref / NTRS / JAXAリポジトリ /
J-STAGE / CiNii / Zenodo / DataCite は動きます**（未設定のソースは `sources[].status=skipped` と理由を返す）。

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
「MMXのローバーはフォボスのどこに着陸する？」 → planetary_rover_location_map(body="phobos", rover="idefix")  # 着陸地点未公表＋候補の緯度帯±30°
「アポロ15号の月面遺跡の地図」            → planetary_rover_location_map(body="moon", rover="apollo15")  # 位置図＋局地サイトプラン（LM・旗・ALSEP・LRRR・LRV）
「月の月面車ルノホート1号はどこを走った？」  → planetary_rover_location_map(body="moon", rover="lunokhod1")
「火星の着陸地点を全部地図で」              → planetary_rover_location_map(body="mars", rover="perseverance", span_deg=20)  # ほかの着陸地点も黄枠で重なる
「太陽系を上から見た図」「はやぶさ2は今どこ」 → solar_system_now(probe="はやぶさ2")
「169P はいつ地球に近づく？光度は？」          → solar_system_now(comet="169P", view="apparition", days=120)  # 見え方チャート
「太陽系マップでエンケ彗星のルートを表示して」  → solar_system_now(comet="エンケ彗星", route=True)  # 通過経路を重ねる
「ボイジャー1号はいつ1光日に達する？」          → solar_system_now(probe="ボイジャー1号")  # 1光日リング・◇=到達時の方向・到達予測日
「土星より内側だけ見せて」                  → solar_system_now(range_au=10, engine="accurate")  # 内側を拡大（範囲外は注記へ）
「ハレー彗星の軌道を見せて」                  → solar_system_now(comet="ハレー彗星", view="comet_orbit")
「ベンヌの楕円軌道を図で見せて」                → solar_system_now(asteroid="ベンヌ", view="asteroid_orbit")
「紫金山・アトラスの軌道は？」                → solar_system_now(comet="C/2023 A3", view="comet_orbit")  # e>1 は双曲線の枝
「ハレーとC/2004 R2の軌道を並べて」            → solar_system_now(comet="ハレー彗星,C/2004 R2", view="comet_orbit")  # 1彗星=1パネル
「かぐやの月面落下地点は？」                      → planetary_orbiter_track(orbiter="かぐや", body="moon")  # 落点マップ（figure.kind=impact_site_map）
「アポロの着陸地点を月面図で」                    → planetary_orbiter_track(body="moon", sites="apollo")   # 6地点＋凡例（figure.kind=landing_site_map）
「アポロ11号の着陸地点は？」                      → planetary_orbiter_track(body="moon", sites="apollo11") # 局所図
「月に出来た一番新しいクレーターの位置は？」      → planetary_orbiter_track(body="moon", sites="newcrater") # 新クレーター2点（自然衝突の最新＝マクゲッチン／人工物の最新＝ファルコン9上段）
地点の種別が混在する `sites="all"` は、各行で着陸・形成・衝突を区別します。図の `figure.kind` は混在図=`site_map`、着陸地点のみ=`landing_site_map`、衝突・クレーターのみ=`impact_site_map` です。
「木星に衝突した彗星の地点は？」                  → planetary_orbiter_track(body="jupiter", sites="all")   # SL9 23破片（Hubble OPAL 2025a の全球図に描画。ベースマップの観測年は注記に明示）
「金星の着陸地点は？」                            → planetary_orbiter_track(body="venus", sites="all")     # USGS/NASA Magellan 全球図（2:1 補正）
「タイタンのホイヘンス着陸点は？」                → planetary_orbiter_track(body="titan", sites="all")     # Cassini 全球図の局所図
「イオの地図を見せて」                            → planetary_orbiter_track(body="io", sites="map")        # 地点なし＝全球図のみ（kind=body_map）
「東京で見える次の日食を画像で」              → solar_eclipse_series(place="東京")  # 約4年先まで検索（東京の次は2030-06-01）。それでも無ければ date 案内
「2030年の日食を東京で」                      → solar_eclipse_series(place="東京", date="2030-06-01")
「今月の月齢マップを見せて」              → moon_phase_map(place="東京")  # 1か月の月齢カレンダー（既定・layout="calendar"）
「次の朔望月の満ち欠けを画像で」          → moon_phase_map(layout="lunation", days=8)  # 朔→朔の8枚パネル
「2026年9月の満月はいつ？」              → moon_phase_map(date="2026-09")  # 朔・望・上弦・下弦の時刻を現地時間で返す
「9年前のあの日の月齢は？」              → moon_phase_map(date="2017-08-06")  # 日付だけで過去も可（暦はローカル計算）
「今月の宇宙・天文イベントカレンダー」      → space_calendar(place="東京")  # 打ち上げ・天文現象・公開イベント・自分の予定（既定は今月）
「10/24に観望会を入れて」                → calendar_event_add(title="観望会", date="10-24", time="19:30")  # 予定はローカルのみ
「今月の予定だけ一覧で」                  → calendar_events()  # 図を描かない軽い経路
「今夜の観測に向く時間帯は？」                → astronomy_weather(place="東京")  # 日本国内は気象庁の雨雲・降水画像も同時に返る（figure.notes を要約せず引用）
「東京の今夜の空に何が見える？」              → sky_map_with_satellites(place="東京")
「いま宇宙天気はどう？」                      → space_weather()  # DONKI が障害なら NOAA SWPC に自動切替（出典が変わる）
「最近の太陽フレアは？」                      → space_weather(kind="flare")
「今後の太陽フレアは？」                      → space_weather(kind="forecast")  # 発生確率（C/M/X・1〜3日）・活動領域別確率・NOAA 3日予報（DONKI に予報は無いので SWPC）
「最近の火球は？」                            → fireball_reports(days=30)
「今週地球に接近する小惑星は？」              → neo_close_approach(days=7)  # 呼ぶと結果がカレンダーにも蓄積される
「地球に接近する彗星は？」                    → neo_close_approach(days=365, max_distance_au=0.3, kind="comet")  # 彗星は既定の照会には出ない（別照会）
「この接近をカレンダーに入れて」              → neo_close_approach(days=7) の蓄積結果を space_calendar(kinds="neo") で表示（カレンダー自身は JPL を叩かない）
「衝突確率の高い小惑星は？」                  → impact_risk()
「ロシアの気象衛星の運用状況は？」            → satellite_status(query="meteor")  # 全件走査＋一致度順で Meteor-M が上位に来る
「火星の画像を見せて」                        → search_space_images(query="mars")
「米国初の宇宙望遠鏡は？」                    → reverse_lookup(category="宇宙望遠鏡", country="United States")
「天宮は今どこ？」                            → tiangong_now()
```

## 並列ツール呼び出し（LLM が複数ツールを同時に投げる）

**独立した複数ツールは、1ターンでまとめて投げて構いません。** サーバー側が同期ツールを
ワーカースレッドで実行するため、並行に呼んだ分だけ実際に速くなります（直列のラウンドトリップを
繰り返す必要はありません）。実測:

| 例 | 逐次（合計） | 並列（wall） | 倍率 |
|:--|--:|--:|--:|
| `moon_phase_map(place=東京)` を4並列（同一引数） | 2.55s | **2.47s** | 4.0× |
| `astronomy_weather` を3都市（東京/大阪/札幌）同時 | 11.72s | **3.93s** | 3.0× |

投げ方の例: 「ISSの今の位置」「東京の今夜の天気」「最新の宇宙天気」を別々に聞かれたら、
`sat_ground_track(name="iss")` ＋ `astronomy_weather(place="東京")` ＋ `space_weather()` を
**同時に呼ぶ**のが正解です（1つずつ待つと待ち時間がそのまま積み上がります）。

並列で投げるときの前提（サーバー側の保証）:

- **同じ引数の並行呼び出しは1回の実行にまとまります**（single-flight）。8並列で投げても
  実行は1回で、結果は全員に同じものが返ります。`DEMO_KEY` のような共有レート枠を
  N倍消費しません。
- **1つが失敗しても他は落ちません**。失敗は例外ではなく `CallToolResult` の
  `structuredContent.error` として返るので、並列呼び出しのうち1本がレート制限や
  一時遮断でも、残りの結果はそのまま使えます。
- **レート制限のあるソースは同時に呼んでも枠は共有**です（NASA `DEMO_KEY` 30 req/h/IP・
  Launch Library 2 の per-IP 制限・CelesTrak の IP 遮断）。CelesTrak は遮断を検知すると
  **以降 fail fast**（HTTP を出さず即エラー）なので、呼び出しは固まりません（`sat_tle` / `sat_ground_track` / `tiangong_now` / `sky_map_with_satellites` が該当）。**TLE は遮断中も代替源（tle.ivanstanojevic.me → db.satnogs.org）へ自動で切り替わる**ので、位置系ツールは遮断中でも動きます（出典は `tle_source` に実際の取得元を明示）。
- **CPU 律速の計算は並列化しません**（GIL）。Skyfield の計算や Pillow/matplotlib の描画は
  1枚ずつ処理されます（matplotlib はプロセス全体の状態を持つため、サーバー側で
  `RENDER_LOCK` により直列化）。重い図を何枚も同時に投げても、速くはなりません
  （速いのは API 待ち＝I/O の部分です）。
- 応答の`content`には、インライン画像を描画できないクライアント向けに
  `🖼️ [生成した画像を開く](file:///…)` のリンクが先頭に入ります（回答時はそのまま提示）。

実装メモ（このサーバーを改修する場合）: 全66ツールは `server.py` の `_reg()` で登録し、
本体は `anyio.to_thread` のワーカースレッドで実行します（元の同期関数は
`tool.fn.sync_fn` に残るので、検証スクリプトは同期呼び出しのまま使えます）。検査は
`scripts/check-tools.py --concurrency`（single-flight・スレッド逃がし・混在並列）と
`scripts/check-tools.py --stdio`（実クライアント経路で無応答にならないか）です。
同時実行の上限は anyio のワーカースレッドプール（既定 40 本）で、それを超える並列呼び出しは
順番待ちになります（通常の使い方では問題になりません）。
**遅延 import するネイティブ拡張は起動前にまとめて import しておくこと**（`server.py` 冒頭の
`import numpy`）。stdio サーバーが動き出した後に numpy / matplotlib / skyfield を import すると、
この環境では import が返らず**ツールが無応答**になります（実測。PIL.Image・sgp4・requests は問題なし）。

## 図の注記 figure/1（描画系ツールの応答）

描画系ツール（`solar_system_now` / `sat_ground_track` / `planetary_orbiter_track` / `planetary_rover_location_map` / `sky_map_with_satellites` / `solar_eclipse_series` / `moon_phase_map` / `astronomy_weather`〔雨雲・降水画像を返すとき〕 / `space_calendar`）は、`structuredContent.figure` に **視点(view)・主天体の置き方(primary: 楕円は焦点であって中心ではない)・縮尺(scale)・円錐曲線(conic)・注記(notes)・自己検証(verify)・説明(caption)** を返します。`content` にも同じ注記が `### ⚠️ 図の注記` として入ります。

- **`content` には人間向けの表示だけを入れる**（「要約せず引用してください」等の LLM への指示文は `content` に書かない。読むのは人間で、意味不明な文になる）。指示は各ツールの docstring と `structuredContent.figure.notes_usage` に置く。
- **回答に図を説明するときは `figure.notes` を要約・言い換えせず、そのまま引用する**（「主天体は焦点」「対数縮尺」「地上軌道は投影」等の但し書きを落とすと図の誤読を招く）。
- **`conic.kind` が `hyperbola`/`parabola` のとき、その軌道は閉じていない**（遠日点なし）。「周回軌道」と説明しないこと。
- `verify.ok` が真なら、描いた画素から測った近点/遠点距離が数値と一致し、ラベルが線に被っていないことを意味します。
- 地図タイルが取れなかった場合は `figure.notes` に「地図タイル N/M 枚を取得できませんでした（図の該当領域は背景色のまま）」が**数値から生成**されます（欠けを黙って捨てると「地図に無い＝何も無い」と誤読されるため）。
- `figure.kind` は描画の種類を示します（`heliocentric_overview` / `orbit_plane` / `orbit_plane_set` / `ground_track_map` / `sky_view` / `eclipse_panels` / **`moon_phase_calendar`** / **`moon_phase_lunation`** / **`impact_site_map`**（落点などの地点マーカーを天体面地図に描いた図） / **`landing_site_map`**（着陸地点。複数地点は番号＋凡例、`verify.markers[]` に地点ごとの検証））。
- `verify.periapsis_check` は `equality`（近点距離を等値で検査）か `upper_bound`（**近点が画面上で分解できない**ため上界だけを検査）です。**超長距離の楕円では近日点が「誇張した主天体の円盤」の内側に入り、曲線の端の画素は近点ではなく円盤の縁になる**ので、`upper_bound` に切り替わります（`periapsis_resolvable: false` ＋ `periapsis_unresolved_reason`）。この場合も主天体を楕円の中心に置く誤りは検出できます（上界＋遠点側の等値検査）。**`periapsis_resolvable` が偽の図を説明するときは、注記にある「この縮尺では図から確認できない」旨を落とさないこと。**
- `moon_phase_map` は**月齢・照度・満ち欠けの向きを自称する図**です。`verify` は**位置角（PA）軸上で明暗境界線の位置を測り直した照度**との一致（許容 0.05。細い三日月は境界線が1〜2画素幅になり丸め誤差が残る）で、走査軸が申告した PA 方向なので**向きが違えば一致しません**。図の月は模式図（実写ではなく、月の海は乱数・クレーターや秤動・地球照は描かない）で、**天の北を上・東を左に置いた見え方**（地平線からの見え方ではない）です。「朔・望の日は正午時点では前後になる」等の食い違いは `figure.notes` に**数値から生成**してあります。
- `solar_eclipse_series` は**その観測地で太陽が地平線より上にある時間帯だけ**を描きます。全日食が地平線下なら図を返さず「見えません（最大高度 −67°）」と明示します（見えない食を図にすると誤解させるため）。
- **情報パネルは現在位置マーカーを隠さない隅に置きます**（`surface_map.panel_placement()`）。パネルを後から描くと地図の暗幕がマーカーを覆い、画素検査（`verify.marker_pixels`）が 0 になって落ちます（実測: 月面の LRO が北緯82°＝図の上端に来たケース）。`verify.panel_overlaps_marker` に結果が入り、隅で避けられない小さい図ではマーカーをパネルの上に描き直して注記にその旨を出します。
- 検査: `scripts/check-tools.py --figures`（注記が空・`verify.ok` が偽なら exit 1）。既定引数では通らない経路（`view="comet_orbit"`・`view="apparition"`＝期間の端/双曲線の経路を含む、可視の日食）も明示的に叩きます。

## 開発ワークフロー（検証ゲート）

```bash
uv run python -m compileall -q src/space_finder_mcp   # 構文
uv run python scripts/check-tools.py --dead-code      # デッドコード（0件を維持）
uv run python scripts/check-tools.py --offline        # ネットワーク全断で例外漏れ検査
uv run python scripts/check-tools.py --fuzz           # 数値引数へ不正値を注入（例外漏れ0を維持）
uv run python scripts/check-tools.py --figures        # 描画系の図の注記(figure/1)を検査
uv run python scripts/check-tools.py --media-links    # 画像/音声/動画の「リンク先行」を検査
uv run python scripts/check-tools.py --concurrency    # 並列ツール呼び出し（single-flight・スレッド逃がし）を検査
uv run python scripts/check-tools.py --stdio          # 実クライアント経路(stdio)で代表ツールが応答するか検査
uv run python scripts/check-tools.py                  # 全66ツール実呼び出し（数分、exit 1 で失敗）
```

**MCPサーバーはホットリロードなし** — `src/` 変更後はクライアント再起動が必要です。

## キャッシュ（API呼び出しと処理の削減）

| 層 | 対象 | 方式 | TTL |
|:--|:--|:--|:--|
| 不変アセット | Trekタイル / NASA画像資産 / 星空背景 | ディスク（`%LOCALAPPDATA%\Temp\space_finder_mcp\cache`） | 30日 |
| 揮発データ | RSS・打ち上げ・STAC検索・ESO | メモリ | 10分 |
| 〃 | DONKI（**カテゴリ単位**＝種別を変えても取り直さない。認証不要・`DEMO_KEY` の枠を消費しない）/ NOAA SWPC（フォールバック時に7項目） | メモリ | 10分 |
| 〃 | 天体観測用天気・メディア検索・ALMA/CADC | メモリ | 30分 |
| 〃 | APOD / NEO / EO Dashboard | メモリ | 1時間 |
| 〃 | データセット一覧・ジオコーディング・POWER・Wikidata | メモリ | 24時間 |
| 高コスト計算 | 日食（次の日食探索28秒＝1400日窓／指定日1秒） | 緯度経度丸めキーでメモリ | 24時間 |
| 高コスト計算 | OSCAR 全カタログ（35ページ・初回38.5秒。1ページ30件固定のAPI） | **メモリ＋ディスク**（プロセス再起動後も即時） | 24時間 |
| 高コスト計算 | 探査機・彗星の Horizons 状態ベクトル（分単位キー）/ SBDB 軌道要素 | メモリ | 24時間（キーが1分ごとに更新） |

実測効果（1回目→2回目）: `planetary_orbiter_track`(LRO) 4.46s/73req → 1.07s/1req、`solar_eclipse_series` 28.4s → 0.7s、`satellite_status` 50.28s → 0.00s、`sky_map` 画像 992KB → 188KB（JPEG化）。

**同じ引数の並行呼び出しは1回の実行にまとめます**（single-flight）。**エラー応答はキャッシュしません**（レート制限429等が固定化しない）。**現在位置系はキャッシュ対象外**です。

## 注意事項

1. **出典表示**: 結果には出典URLが含まれます。回答時は必ず引用元を表示してください（NASA / ESA / JAXA / ISRO / CSA / INPE / UK / DLR / Roscosmos / CNSA / Wikidata など）。
2. **レート制限**: `DEMO_KEY` は 30リクエスト/時/IP の共有枠（`apod`・`neo_today` で共有）。`space_weather`(DONKI) は 2026-09-30 の URL 移転で**認証不要の別ホスト**（`ccmc.gsfc.nasa.gov/DONKI-API`）になり、この枠を消費しません（`nasa_budget` を通しません。**キーを持たないホストへ `api_key` を渡さない**）。DONKI が障害のときは**認証不要の NOAA SWPC へ自動切替**するので、宇宙天気だけは止まりません（`space_weather` 以外の NASA 系は待機が必要）。**予報（`kind="forecast"`）も SWPC（認証不要）**なのでこの枠を消費しません。サーバー側で使用数を数えており、枠を使い切ると HTTP を出さずに回復目安を返し、429 を受けた場合は `Retry-After` を尊重します（`structuredContent.budget` に上限・使用数・残りを添付）。**CelesTrak** も短時間の連続リクエストで IP 単位に遮断され（403、または TCP が返らない blackhole）、その間は 1 回の呼び出しが分単位で固まります。接続は (connect 10 秒, read 25 秒) で打ち切り、遮断を受けたら**セッション内で記憶して以降は HTTP を出さずに即座に案内**を返します（`sat_tle` / `sat_ground_track` / `tiangong_now` / `sky_map_with_satellites` が該当）。 さらに **TLE は代替源へ自動フォールバック**します — CelesTrak 遮断中は認証不要の公開ミラー（tle.ivanstanojevic.me → db.satnogs.org）から同じ衛星の TLE を取得し、**content / structuredContent にどちらから取ったかを明示**します（`tle_source`）。グループ検索（`group=`）は代替源に無いので、遮断中はその旨を案内します。
3. **曖昧入力**: 衛星名などで候補が複数ある場合は推測せず、NORAD ID 付きの候補を提示して停止します。
4. **過去ミッション**: かぐや（SELENE）・あかつき等は「現在位置を表示できない」と正直に返します。落点が公表されている機体（かぐや＝南緯65.5°／東経80.4° Gill クレータ付近、2009-06-10 18:25 UTC）は落点を `structuredContent.impact_site` に出典つきで返します（「落点は判明しているか」に MCP だけで答えられます）。和名（かぐや/あかつき）も英語キーへ展開してから判定します。
5. **描画エンジン**: `sky_map_with_satellites` は `ground`（**既定**・地上視点の正距円筒パノラマ・実星表＋天の川＋街並み＋方位目盛り・JPEG。`span`/`az0`/`lp`/`show` が使える）、`simple`（Pillow合成・天頂中心の円形図・JPEG）、`accurate`（matplotlib・正確座標・PNG）。`solar_system_now` は `simple`（既定）と `accurate`。天体・記号の色は `img_common.BODY_COLORS`（惑星・月・太陽の実物色）/ `SYMBOL_COLORS`（環・縞・極冠・小惑星・彗星）が単一の出典で、`sky_map_with_satellites` と `solar_system_now` の両エンジンが同じ値を参照し、`accurate` の凡例は実際に描いたマーカーだけを色コード付きで出します。遠方探査機・彗星は線形縮尺では枠外のため自動的に `simple` を使用します。
6. **名前解決のフォールバック**: 天体名・衛星名は `name_common.py` の共通段階で解決します（内蔵テーブル → 表記ゆれ → 和名→英語名 → Sesame/CDS で名前→座標 → 候補提示して停止）。`cadc_observations` は内蔵テーブルに無い名前（M104/Sombrero/HL Tau/和名）を SIMBAD で解決、`satellite_status` と `sat_tle` は和名（ひまわり/ひので/だいち/宇宙ステーション等、`JA_ALIASES` 103キー）を英語名・NORAD ID に展開します（現役機は `celestrak.WELL_KNOWN` に実測 NORAD ID 登録済み＝オフライン解決、番号なしファミリー名は候補提示）。解決できないときは推測せず候補を提示します。
7. **応答にキーを載せない**: `apod`/`neo_today`/`space_weather` のエラー文字列は requests 由来で `api_key=<値>` を含むため、外向けテキストは `nasa_budget.redact()` を通して伏せ字化します（`budget.key` は `DEMO_KEY`/`custom` のみ）。
8. **メディアのリンク**: （違反は `scripts/check-tools.py --media-links` が exit 1 で検出します）生成画像は `%LOCALAPPDATA%\Temp\space_finder_mcp\out` に保存し、`content` の**先頭行**に `🖼️ [生成した画像を開く: …](file:///…) ｜ 保存先: …` を出します（`structuredContent.image_path` に実パス）。検索系は各項目の直後に `🖼️/🎧/🎬 [◯◯を開く: タイトル](URL)` を出します。**文言は `img_common.media_link_line(対象, kind=…)` が生成します**（動詞は kind から決まる＝`画像を開く`/`生成した画像を開く`/`音声を開く`/`動画を開く`/`ファイルを開く`。ツール側で動詞を書くと「サムネイル画像を開く: 」「動画を再生: 」のようにばらつき、ゲートが `img_common.canonical_media_label()` で検出します）。CLI系・Android系ハーネス（codex / opencode）はインライン画像を描画しないため、回答時はこのリンクを必ず提示してください（アイコン: 🖼️画像 / 🎧音声 / 🎬動画）。**メディアを連続で返すときは caption とリンクの間、およびリンクどうしの間を空行で区切ってください**（単一改行は Markdown の同一段落に畳み込まれ、隣り合う画像の caption / リンクが1行に融合する。1枚だけのときは段落が分かれて見えるため気付きにくい。実測: `astronomy_weather` の気象庁画像2枚、複数画像を並べた回答）。配信時に `server._delivered` が `img_common.layout_media_links` で空行を補うので、ツール側で空行を入れ忘れても融合はしません（ゲートの融合検査は**整形後の配信形**で行い、整形そのものの動作は `delivery_issues()` が確かめます。`--media-links` は「リンク先行・形式の統一・空行区切り・structuredContent・配信時の整形」を確認します）。**複数の画像を返すときはリンクをまとめて先に、画像は連続で**出してください（回答でも同じ）。
9. **認証付きダウンロード非対応**: ESA Copernicus は検索とプレビューURLのみ（OAuth2 ダウンロードは行いません）。

10. **ツール出力と回答文の関係（どこまでが保証か）**: ツールが返す `structuredContent`（数値・座標・出典・`figure.notes`・`verify`）と `content` の画像・リンクは、**同じ引数・同じ時刻に呼べばどのモデルからでも同じ**内容です（データ取得と計算はサーバー側で完結し、ホスト側モデルには依存しません）。一方、**それを回答文に組み立てる工程はホスト側モデルの裁量**で、説明の順序・語り口・要約の粒度・強調点はモデルや設定によって変わり、同じツール結果でも書き上がりは同一にはなりません。したがって (a) **数値・出典・割合の根拠は常に `structuredContent` に置く**、(b) 自分の文章が JSON と食い違って見えるときは **JSON を正として書き直す**、(c) JSON に無い数値を補ったり、丸め・単位換算で意味を変えたりしない、こと。表示文（`content`）は「人間向けの要約」であり、`figure.notes` のような注記は**要約せずそのまま引用**してください。

## 参考リンク

- NASA Open APIs: https://api.nasa.gov/
- JPL Horizons: https://ssd.jpl.nasa.gov/horizons/
- CelesTrak: https://celestrak.org/
- ESA Copernicus Data Space: https://dataspace.copernicus.eu/
- JAXA Earth API: https://data.earth.jaxa.jp/
- DLR EOC STAC: https://geoservice.dlr.de/eoc/ogc/stac/v1
- Roscosmos オープンデータ STAC: https://s3.gptl.ru/stac-web-free/catalog.json
- リポジトリ: https://github.com/loosephoto/space-finder-mcp

## 更新履歴
- v0.41.5 — **地上視点パノラマ（`sky_map_with_satellites(engine="ground")`）の名札が狭い視野で右端から切れる不具合の修正**: 月・惑星の名札は `x+12`、主要恒星・銀河と天の川（銀河面）の名札は `x+11` に描くだけで**右端のクランプが無く**（`x < 10 or x > W - 10` で端のマーカーごと省くガードだけ）、`span` を狭めて描画幅が小さくなる図で名札が切れていた（実測: W=444・span=40 で「フォーマルハウト」が **+122px**、W=333・span=30 で「月（照度 96%）」が **+52px**、W=444 の合成シーンで「M31 アンドロメダ銀河」が **+185px** はみ出し）。`_fit_label()` を追加し、**① マーカーの右に左揃え → ② 入らなければ左へ回して右揃え → ③ どちらも狭ければ余裕のある側でフォントを縮小（`LABEL_MIN_SIZE=13`）→ ④ それでも入らなければ描かない**の順で決める（図の端に残す余白は `LABEL_PAD=6`）。余白のある図は位置・サイズ・アンカーが従来と同一で、`anchor="la"` が PIL の既定と同じであることは画素（差分 0）で確認した。描いた名札の占有範囲を `info["label_boxes"]` に残し、**描画経路の値**で検証・回帰テストができるようにした。**見た目が変わらないことの実測**: 同一の合成シーン（ネットワーク不要）を `git stash` で前後比較し、W=1333 の図は**画素差 最大0・差分数 0 / 1,827,543px**、狭い図は右端6px帯の名札画素が 6px → **0px**（移動先に 240px の名札色画素＝縮小して再配置）。実引数（東京 2026-10-24 22:00 JST・`lp="city"`）でも span=30/az0=165 が `size=19 anchor=ra`（x0=6.4〜155.4）、span=40/az0=165 の「フォーマルハウト」が `anchor=ra`（239.8〜391.8）で**はみ出し 0**、span=120/az0=120 は修正前と同一座標。回帰テスト **448件**（新規 8件 `tests/test_ground_view.py::LabelFitTests` / `LabelFitRenderTests` ＝右に余白があれば従来どおり／右端では左へ回して右揃え／どちらも狭ければフォント縮小／どこにも入らなければ描かない／描いた箱が必ず W 内／広い図は従来のオフセットのまま。**旧実装では 8件すべて RED**）。`--figures`（50行 ok・`verify_ok=False` 0）・`--offline`・`--fuzz`（460組・例外漏れ 0）・`--dead-code`・`--stdio`・`--concurrency`・`--media-links`（22ツール・問題 0）・`--fonts` はすべて exit 0。
- v0.41.4 — **`astronomy_weather` の3件の不具合修正（月の照度・今夜の月没の日付またぎ・薄明を無視した観測時間帯）**: いずれも東京 2026-10-23 の実測で確認した。**(1) 月の照度** — Open-Meteo の `moon_phase` は朔望月の進み具合（0=新月, 0.25=上弦, 0.5=満月）であって照度ではないのに、`(1-|p-0.5|×2)` の線形近似で照度を出していた（p=0.391 → **78.2%**）。太陽との離角 360°×p から `(1-cos(離角))/2` に修正（同 p → **88.7%**。timeanddate の同日 88.6%、Skyfield/DE421 による同日 21:15 JST の実測 91.3% と整合）。位相ラベルも照度 88.7% を「半月〜満月手前🌓」と呼ばないよう 🌔 に直した。**(2)「今夜の月の入り」** — `daily.moonset[0]` は**日没より前に終わったその日未明の月没**（東京 2026-10-23 は 02:26）で、それを「今夜の月没」として提示していた（今夜の月没は翌日 03:30。Skyfield の月没 03:29・timeanddate の Oct 24 3:30 am と一致）。**日没より後の最初の月没**を選ぶ `_night_moon()` を追加し、本文も日付つき（`月の入り 10-24 03:30`）に変更。**(3) 薄明を無視した観測時間帯** — 判定が `is_day` だけだったため、日の入り直後の**まだ明るい**時間帯（東京 2026-10-23 は日の入り 16:56 に対し天文薄明終 18:22 ＝86 分差）を「観測に最適な夜間」として 17:00〜18:00 と提示していた。Open-Meteo は hourly に太陽高度を返さない（`sun_elevation` は 400 で拒否）ので、**日付・経度・UTCオフセットから視太陽時を出して解析的に太陽高度を推定**する `sun_altitude_deg()`（赤緯 Spencer 1971・均時差 NOAA。依存追加なし）を実装し、`sun_alt_max`（既定 -15°。`-18` で天文薄明＝完全な暗夜のみ、`-6` なら市民薄明後）で観測時間帯を切る。推定できない地点（極昼・極夜）は `is_day` にフォールバックし `structuredContent.sun_alt_source` で明示。`structuredContent` の各時間帯に `min_sun_alt`/`max_sun_alt`、各時間に `sun_alt_deg` を追加。**解析式の妥当性は Skyfield/DE421（幾何高度）と実測で突き合わせた**: 東京・シドニー・ストックホルム・トロムソ × 2026-06-21/10-23/12-21 の 24 時間 30 分刻み（48点×12＝576点）で**最大差 0.41°**、薄明（-6/-12/-18°）の交差時刻は**±1 分**。**同梱の修正**: `tests/test_celestrak_fallback.py` の日付依存を解消（フィクスチャ `TLE1` のエポック 26265 = 2026-09-22 が 2026-10-23 に 31 日前となり `FALLBACK_STALE_DAYS=30` を超え、「2 件中 1 件」の期待が実時間で壊れていた。「新しい TLE」は実行時にエポックを生成しチェックサムも計算し直す）。回帰テスト **440件**（新規 26件 `tests/test_weather_astro.py`＝照度式・今夜の月没の選択・解析式 vs Skyfield の固定値（東京5点/シドニー4点）・極昼極夜・薄明の除外と緩和閾値・スタブ応答での統合・ネットワーク断の契約）。`--dead-code`・`--fonts`・`--fuzz`・`--stdio`・`--concurrency`・`--figures`・`--offline`・`--media-links` はすべて exit 0。
- v0.41.3 — **MMX の打上げ日とフォボス記述的一次情報への統一＋ローバーのタイトル表記の回帰テスト**: MMX の打上げ日を **JAXA 一次情報（JST 2026-10-20 04:41:03、H3ロケット10号機）**に統一した（旧: 2026-10-19 は仏・中央ヨーロッパ表記で、JAXA の JST 表記とは1日ずれる）。あわせて README / `planetary_rover.py` の MMX 説明から「その前身降低成本の小型ローバー」という誤った表現を「**MMX のコスト削減のために特別に設計された**小型ローバー」に直し、MMX 本体の試料採取（10 g 以上）と 2031 年の地球帰還を明記した。また `planetary_rover_location_map` の `figure.title` 2 箇所の表記をrepo の多数派ルール「◯◯ の△△」（空白あり）に揃え、**同じ名前が 4 つの別リテラル（描画表題 / `figure.title` / `media_link_line` の対象 / 本文見出し）に散らばる**状態を固定する回帰テストを `tests/test_rover_title_convention.py` に追加した（火星・月・フォボスの 3 経路で『空白なしの の が無い』『空白ありの の がある』『リンク行の対象と `figure.title` の主語が一致する』『`verify.ok=True`』を検証。旧実装では **6 件失敗**することを確認済み）。表示への影響がないことは実測で確かめた: 変更前後で同じ引数の図（火星/月的アポロ15号/フォボス IDEFIX）を作り `ImageChops.difference` を取った結果、**差分画素 0・最大差 0・JPEG バイト数一致**（3図すべて）。回帰テスト **414件**（既存 411 ＋ 新規 3）、品質ゲート 8 種すべて exit 0。
- v0.41.2 — **`tools/list` の軽量化（毎ターン載るツール定義を 42% 削減）**: MCP クライアントは `tools/list` を**毎ターンのプロンプトに載せる**ので、66本ぶんの description（実測 44,963 文字）がそのまま入力トークンになり、生成速度とコンテキスト消費を押し下げていた（実測 `tools/list` 118,677 バイト ≈ 約39,600 トークン）。`tool_docs.py` を追加し、**配信する description だけ**を「要約（先頭段落・長ければ2文）＋例の行＋`Args:` ブロック＋契約文（メディアのリンクの『そのまま提示』／`figure.notes` の『要約せず引用』）」に縮める（`server._reg()` が `mcp.tool(description=…)` で渡す）。**`Args:` は引数の一次情報なので一字も変えない** — 機械的に途中で切ると括弧が閉じない壊れた文になる（実測）ので、引数ごとの継続行もそのまま残す。**関数の docstring は完全なまま**（リポジトリを読む人・エージェントの説明は減らない）。加えて `tools/list` の `inputSchema` から**意味のない `title`（＝プロパティ名と同じ）と `anyOf: [X, {type: null}]`** を落とす（`tool_docs.slim_schema` が `type: [X, "null"]` に畳む。`number|string` のような本物の union は畳まない）。差し替えは `mcp._mcp_server.list_tools()` の再登録で、**失敗したら素通しして従来どおり動く**ように try で囲む。実測 `tools/list` **118,677 → 69,077 バイト（−42%）・約39,600 → 約23,000 トークン**。回帰テスト **411件**（新規 10件 `tests/test_tool_docs.py`＝要約/例/Args/契約文が残る・Args を切らない・短い docstring は不変・docstring を書き換えない・title と null 枝の除去・本物の union は保持・登録済みツールの実物で契約文が残ること）。`--stdio`（実クライアント経路 7 項目・無応答0）・`--offline`・`--figures`（50行 ok）・`--fuzz`・`--fonts`・`--concurrency`・`--dead-code`・`--media-links` はすべて exit 0。
- v0.41.1 — **生成速度の改善（描画・探索・取得の3系統）**: 「肥大化で遅い」を推測で直さず、ツールごとに cProfile で内訳を実測してから手を入れた（実測前: `solar_eclipse_series` 24.2s・`sky_map_with_satellites` 24.1s・`solar_system_now` 2.9s）。**(1) 日食の探索をベクトル化** — `_geom`（1時刻ずつ Skyfield の `observe().apparent()` を呼ぶ）を `_geom_many`（時刻の**配列**を渡す）に置き換えた。1点ずつ計算すると JPL 天体暦の補間を点の数だけやり直す（実測: 5152点で `spk.compute_and_differentiate` 129k 回＝19秒）。配列なら補間が1回にまとまり **24.2s → 2.2s**。**戻り値は 4 ケース（東京2026-08-12／東京2026-10-21／マドリードの次の日食＝2027-08-02 部分日食 6 フレーム／東京2024-04-08）で改修前と完全一致**、図の画素差も 0。**(2) マーカーの光暈をタイル化** — `solar_system.py` の `_draw_*_marker` は1個ごとに**全画面サイズの RGBA** を作って `GaussianBlur` し、`convert`→`alpha_composite`→`convert` で全画面を往復していた（27個で 2.3秒＝俯瞰図の描画時間の7割）。`_glow(img, box, radius, fill, line_width=0)` に集約し、**半径の4倍＋4px だけ広げたタイル**でぼかして同領域にだけ合成する。Pillow の GaussianBlur は影響半径が有限なので画素は変わらない（**画素差 >10 が 0/2,250,000・最大差 0** を実測）。`solar_system_now` **2.9s → 0.2s**。**(3) 重点衛星の TLE を並列取得** — `ground_view._satellites` / `sky_overlay` が 6 衛星を直列に取り、1件 1.5〜3.5秒＝20.4秒かかっていたのを `sky_overlay._fetch_tles_parallel`（ThreadPoolExecutor・重複 ID は畳む・1件の失敗はその1件だけ `(None, "")` に正規化）に置き換え。**24.1s → 4.5s**（取得 20.4s → 1.1s）。**(4) 遮断状態をプロセスをまたいで記憶** — CelesTrak が blackhole のとき connect タイムアウト 10 秒を**プロセスを立てるたびに**払っていたので、`celestrak_block.json`（`%LOCALAPPDATA%\Temp\space_finder_mcp\cache`）に残す。クールダウン（60/300秒）で自動失効するので固定化しない。テストは `setUp` でメモリとディスクの両方をリセットする（`tests/test_celestrak_fallback.py` も追随）。**(5) Horizons の状態ベクトルを1分丸めキーでキャッシュ**（`planetary_map._horizons_state_cached`。規約6の範囲内）→ `planetary_orbiter_track` 2.1s → 1.4s。回帰テスト **401件**（新規 9件 `tests/test_perf_regressions.py`＝配列化の値一致・並列取得の対応表と失敗の持ち方・光暈の画素一致（楕円/線分）・遮断記憶の往復と失効・壊れたファイルの無視）。`--figures`(50行 ok)・`--offline`・`--fuzz`・`--fonts`・`--stdio`・`--concurrency`・`--dead-code`・`--media-links` はすべて exit 0。
- v0.41.0 — **ローバー航跡の月面対応と、人工物着陸地点の重ね表示**: `planetary_rover_location_map` に **(1) 月** と **(2) ほかの人工物着陸地点の重ね表示** を追加。**(1) 月（`body="moon"`）** — アポロ11〜17号（LRV ありは 15/16/17号）・ルノホート1/2号・玉兔号（嫦娥3号）・玉兔2号（嫦娥4号）を表示。等角図法の天体面地図は 1px あたり数百 m〜20 km なので月面機材（旗 8〜11 m・LRV 81〜165 m・ALSEP 75〜200 m）は**どのズームでも 1 画素に収まらない**（実測）。そこで **2 段構え**にした: 左＝Trek の月面地図に着陸地点と近傍着陸地点、右＝**メートル単位・北が上の局地サイトプラン**（NASA PSR と同じ考え方）。LM・ALSEP・LRRR・LRV 最終駐車地点・アメリカ国旗を**一次情報が公開する距離＋方位だけ**から求めた位置で描く。**アポロの LRV 走行経路は公開済み**（ALSJ「Final LRV Parking Places」＋LROC の traverse map/shapefile＋bearing/range の Technical Note）で、公開されているのは端点（LM → 最終駐車地点）だけなので**端点間の直線**を引き「全経路ではない」と明記した。**位置の公開値がない機材は図に描かない**（アポロ11号の旗は距離 8.2 m は公開されるが方位の公開値が無い／アポロ12号の旗は「西 strut と北 strut の中間」から 9.5 m・315° と推定し `position_status="derived"` で区別）。**(2) `show_sites`** — 火星・月の地図に同じ天体にあるほかの人工物着陸地点を黄枠●で重ねる（月のサーベイヤー3号はアポロ12号の LM の約 0.4 km 先など）。自然衝突クレーターと人工物衝突（`kind="crater"`/`"impact"`）は**着陸地点ではないので重ねない**。出典: NASA NSSDCA「Apollo Landing Site Coordinates」（DE421・Wagner et al., Icarus 283 (2017)）／NASA Apollo Lunar Surface Journal／LPI／NASA Trek LRO WAC。回帰テスト 392 件（既存 366 ＋ 新規 26）、8 ゲートすべて通過。
- v0.40.0 — **ローバー航跡のフォボス（MMX / IDEFIX）対応 — 着陸地点の公表に備えて拡張**: `planetary_rover_location_map` に**フォボス**と新ローバー **IDEFIX**（MMX 同梱の小型ローバー・DLR/CNES 製）を追加。MMX は 2026-10-20（JST）打ち上げ済み、IDEFIX は 2028 年度末〜2029 年頭にフォボス着陸予定だが、**着陸地点は 2027〜2028 年に選定されるため未公表**。そこで3つの入口を用意した: **(1) 公表値の自動検出** — IAU/U.S. Gazetteer の機械可読な地名 feed（`PHOBOS_nomenclature_center_pts.kmz`）を毎回読み、`type` に "Landing Site" を含む地名を着陸地点として採用（未公表なら `landing_site_status="not_published"` と明示）、**(2) 任意の地点の指定** — `site_lat`/`site_lon`（東向き正）または `site_lon_w`（西向き正・IAU/フォボスの慣用表記。排他）を渡すと着陸予定点として描画、**(3) 未公表時は制約帯** — 着陸候補条件として公表されている**緯度 ±30°**を緑枠で描き、「着陸地点そのものではない」ことを凡例と注記に明記。ベースマップは USGS 公式全球等角図（Shaded Relief）。**この図の罠を実測で潰した**: 配布 PNG の印字目盛は「西向き正」で並ぶが地図自体は**東向き正**（IAU 地名 20 点の実測・Stickney=49°W が図の左 36.39% に一致）＝左右反転すると全マーカーが狂う。また配布 PNG には地形ラスタ外の白地・目盛ラベル・題字・クレジットがあるので、`surface_map.image_view(crop=…)` に**相対範囲**の切り出しを追加（解像度非依存）。あわせて `upload.wikimedia.org` の直接リンクが 429 になる件を回避し `commons.wikimedia.org/wiki/Special:FilePath/…?width=2400` を使う。実装中に見つけた不具合も修正: **0〜360°E の公表値（例 311°E）をそのまま投影すると画像外に出る**（±180° に折り返す）、**全球図の画素検証の許容 0.01° が小さすぎる**（1px≒0.36° なので画素分解能から決める）、**地点が無いときにも地図中央にラベル箱を置いて「ここにいる」ように見えた**（地点があるときだけ描く）、**情報パネルのタイトルがパネル外へはみ出していた**（`fit_font_for_width` で収める）。検証は IAU Stickney（1°N 49°W）を指定して**描画されたマーカー中心が px (436.1, 296.1)＝期待値 (436.7, 296.7)（誤差 0.57px / 0.17°）**、制約帯が実測で **+30.0°/-30.0°**、目視でもマーカーが Stickney クレーター上に載ることを確認。回帰テスト **383件**（新規 17 件 `tests/test_planetary_rover_phobos.py`）、`--figures`（48行すべて ok・新建 2 経路を含む）・`--dead-code`・`--fonts`・`--fuzz`・`--stdio`・`--concurrency` はすべて exit 0。
- v0.39.6 — **彗星の描画ルーチンの不具合修正（`solar_system_now` / 彗星の軌道面ビュー・見え方チャート）**: 実測（画素計測・合成シーン・目視）で確認した 5 点を修正。**(1) simple エンジンの彗星の尾が自分のラベル箱に隠れていた**（実測: ハレー彗星で尾 77px のうち 51px が 320×42px の不透明ラベル箱 `(0,30,45,235)` の内側に入り、箱内で尾の色が箱色に潰れていた。67P 55%・C/2023 A3 55%）。尾の描画を `_draw_comet_tail` に切り出し、**すべてのラベル箱を描いた後の最終パスで尾も描き直す**二段構えにした（回帰テストで「箱内の尾の点が1 つもシアン色でない」＝箱に隠れている、を検出）。**(2) accurate エンジンの彗星マーカーが真距離 `au` に置かれ、黄緯の分だけ黄道面から外れて彗星自身の軌道線から外れていた**（実測: C/2023 A3 で au=8.729 / proj_au=6.743 AU → 24.2 px ずれ、simple エンジンと位置が食い違っていた。注記は「正射影位置」と書いていた）。`proj_au` に統一し、ラベルにも投影距離を併記（軌道線までの距離 1.3 px）。**(3) 彗星の軌道面ビューで「現在位置」マーカーが画面外に落ちていた**（実測: C/2023 A3 は図の範囲 3.13 AU に対し現在位置 8.73 AU → px=[-233,-34]）。図の範囲に現在位置を含め、**座標系を「太陽（円錐曲線の焦点）＝画面中央」に変更**（軌道点列の bbox 中心基準だと太陽が端へ寄り、ハレー彗星で現在位置が 2398px＝画面外になっていた）。`figure.markers` に `in_frame` を付与。**(4) `_comet_route` の `r_max_au` が `max|x,y|` で計算されていて軌道傾斜により 18% 過小**（ハレー: 27.69 AU に対し実際 33.84 AU。`range_au="fit"` の判定に使われ、経路末端が静かに切れていた）→ 正射影半径の最大（hypot）に修正。**(5) 見え方チャートの content 末尾の出典がハードコードで、2体近似へ退避したときも Horizons の n 体解だと名乗っていた**→ 実際に使った出典（`_src_of`）に。加えて、期間の端で r が最小のとき表が「最小日心距離 r（近日点）」と書いて`figure.notes` と食い違っていた（実測: ハレー彗星 days=400 で `features.perihelis.inside_window=False`）→ 端のときは「（近日点）」を書かない。回帰テスト **349件**（新規 7 件 `tests/test_regressions.py::CometRenderRegressionTests`）、`--figures`（46行すべて ok）・`--offline`・`--fuzz`・`--fonts`・`--stdio`・`--concurrency`・`--dead-code`・`--media-links` はすべて exit 0。
- v0.39.5 — **日食の探索開始日を UTC 基準に統一＋オフライン回帰テストの補強**: `solar_eclipse_series` の「次の日食を自動検索」経路が `datetime.now()`（ローカル時刻）で開始日を決めていたのを `datetime.now(timezone.utc)` に統一（ホスト TZ が JST 以外で窓が 1 日ずれる可能性を解消）。`tests/test_network_resilience.py` が `requests.get` だけを遮断していたため `csa_dataset_search`（`requests.Session()` 経由）が実通信のまま通っていた穴を、`requests.Session.request` も遮断対象に含めることで閉じ、csa のオフライン回帰テストを追加。`SKILL.md` のツール本数表記を 56 → 66 に是正。回帰テスト 345 件、`--figures`・`--offline`・`--fuzz`・`--fonts`・`--stdio`・`--concurrency`・`--dead-code`・`--media-links` はすべて exit 0。

- v0.39.4 — **俯瞰図（`solar_system_now`）のラベル箱が惑星マーカーを覆う不具合の修正**: `range_au="fit"` の彗星指定で、彗星のラベル箱（320×42px）が右端からあふれて**左へ回り込むと内側の惑星マーカーを覆い**、`figure.verify.range_au.ok` が偽になっていた（実測 2026-10-18: 地球の画素 0px・`--figures` が exit 1。惑星と彗星の位置は日付で動くため、いつでも発現しうる）。原因は「マーカーの右 → はみ出したら左」という素朴な配置で、**惑星のラベル箱だけがマーカーの描き直しで守られていた**こと（彗星・小惑星・探査機の箱はマーカーの描き直しより後に描かれていた）。対策は二段: **(a) 彗星・小惑星・探査機のラベルも既存の `_place_rect`（1光日のラベルで実績のある「占有領域との重なりが最小の角度を選ぶ」関数）に置かせ、`ignore_pts` で自分のマーカーだけを採点から除外**する（除外しないと自分の真横から最も遠い角度へ逃げる）、**(b) すべてのラベル箱を描いた後に惑星・小惑星・彗星核・探査機・経路の目印（◇）を描き直す**（「天体が箱に隠れない」ことを配置の巧さに依存させない保険）。併せて彗星・小惑星・探査機の `label_box` を `structuredContent` に残す（回帰テストと切り分けのため）。回帰テスト **341件**（新規 3 件 `tests/test_regressions.py::OverviewLabelBoxTests`。**変更を `git stash` すると 3 件とも失敗**＝地球の画素 0px になることを確認済み）。`--only solar_system_now`・`--figures`・`--offline`・`--fuzz`（444組・例外漏れ0）・`--fonts`・`--stdio`・`--concurrency`・`--dead-code`・`--media-links` はすべて exit 0。
- v0.39.3 — **`neo_close_approach` に彗星を追加（`kind`＝`"all"`（既定）/`"asteroid"`/`"comet"`）**: JPL CNEOS CAD の**既定 `neo=true` は小惑星だけ**を返す（実測 2026-10-15: 彗星 169P/NEAT が 0.17 au まで接近した 2026-08 の窓でも彗星 0 件。`nea-comet=true` も同じ 243 件で彗星を含まなかった）ため、彗星が返る **`comet=true` の照会**を別に投げ、`kind="all"` は**接近時刻（`jd`）の早い順に統合**します（`structuredContent.kind` / `totals_by_kind`、各行に `object_type`。混在時だけ本文に「（小惑星）」「（彗星）」を付け、彗星は ☄️ を冠する）。`limit` は照会ごとに効くので統合後に切ります。彗星は `h`・`diameter` が null なので**直径を推定しません**（「直径 不明」・`diameter_is_estimate=False`）。`hazardous_only`（PHA）は**小惑星の区分**なので彗星の照会には付けず、`kind="all"` との併用時は**彗星を照会しない**旨を本文に出します。`kind` は別名（`asteroids`/`c`/`nea`/「小惑星」「彗星」等）を受け付け、解釈できない値は既定の `all` に落として**注記**します（黙って別の種別を返さない）。カレンダー蓄積の「種類」は**行ごとの `object_type`** から取ります（呼び出し全体の `kind` で判定すると `kind="all"` の彗星を「小惑星」と記録してしまう）。回帰テスト **338件**（新規 8 件＝`CloseApproachTests` 7件・`AnomalyCalendarStoreTests` 1件）、`--only neo_close_approach`・`--dead-code`・`--fonts`・`--fuzz`（444組・例外漏れ0）・`--stdio`・`--concurrency`・`--offline`・`--media-links` は exit 0。`--figures` は `solar_system_now[range_au="fit"]` の 1 件が `verify.ok=False`（**彗星のラベル箱 320×42px が内側の惑星マーカーを覆う**既存の不具合。実測: 本日は地球が 0px。`git stash` して HEAD でも同じ結果になるため本変更とは無関係）。
- v0.39.2 — **日時解釈・エラー応答の握り潰し・`@ttl_cache` キャッシュ汚染・カレンダー境界処理・検証ゲートの一括修正**: 全モジュール精査と実測で特定した不具合を修正。**(1) `sky_map_with_satellites(engine="simple"|"accurate")` が不正な `when` のエラーを無視して空の星図を描いていた**のを、`scene["error"]` で即エラー応答を返すよう修正。併せて TLE 全機未取得時に「なし(地平線下)」と誤表示していたのを `ground` エンジンと同じく `satellite_errors` に理由を記録し「TLE を取得できなかったため」と明示。**(2) `solar_system_now` / `comet_apparition` / `sky_overlay` の `when` 解釈を `ground_view._parse_when` に統一**し、数値オフセット（`+09:00` 等）の UTC 変換・空白のみ文字列の「未指定＝現在時刻」正規化・存在しない暦日（`2026-02-30` 等）の拒否を全描画ツールで揃えた。**(3) `planetary_orbiter_track` の図内パネルで時刻が `"UTC UTC"` と二重表記され、`+09:00` 指定時に現地時刻の数字のまま `"UTC"` と表示されていた**のを、`astimezone(timezone.utc)` 変換＋末尾の重複 `" UTC"` 除去・空白 `when` の正規化で修正。**(4) 複数彗星パネル（`solar_system_now(view="comet_orbit")` で複数指定）の `figure.markers`** で、`imgs.append` 後に `panel=len(imgs)+1` としていたためパネル番号が 2 始まり（1つずれ）になり、2枚目以降のマーカー `px` に縦オフセット `i*(h+gap)` が加算されていなかったのを修正。**(5) 一時的な通信障害時の `@ttl_cache` キャッシュ汚染を解消**: `oscar.satellite_status`（エラー応答や `failed_pages` ありを24時間キャッシュしていた）・`oscar._catalogue`（`failed_pages` ありをメモリに24時間キャッシュ）・`jaxa.jaxa_dataset_search`（カタログ取得失敗時の空配列を「該当0件」の正常応答として10分間キャッシュ、および空クエリ時にカタログ走査を先に実行していた問題）・`swpc.fetch_all` / `swpc.fetch_forecast`（全項目失敗時の空 `sections` を10分/30分キャッシュ）・`ground_view._star_catalog`（VizieR 失敗時の16星 `fallback=True` を24時間キャッシュ）にそれぞれ `skip_if` とエラー分岐を追加。**(6) カレンダー・蓄積ストアの修正**: Windows CRT の `time.mktime` が 1970 年以前の日付で `OverflowError` を投げて `calendar_store.prune()` が落ちる問題を `calendar.timegm` 化で解消、月をまたぐ複数日の単発予定（`repeat="none"`）が翌月側で消える問題を修正、`space_calendar._computed_month` の流星群極大日を `off` 依存の `start_utc` から現地暦日の `start_local` に変更（最初に呼んだ都市のタイムゾーンで他都市の極大日が前後にずれる問題を解消）、Python 3.12+ で非推奨の `datetime.utcnow()` を `datetime.now(timezone.utc)` に統一。**(7) `scripts/check-tools.py` の `--offline` / `--fuzz` が `requests.Session.request` を遮断しておらず `csa_dataset_search` が実通信していた問題**と、`run_all()` で `calendar_store.STORE_PATH` の未復元・一時ファイル残留があった問題を修正。回帰テスト **330件**（新規 10 件 `tests/test_regressions.py::BugSweepV0392Tests`）、`--figures`・`--offline`・`--fuzz`・`--fonts`・`--stdio`・`--concurrency`・`--dead-code` すべて exit 0。
- v0.39.1 — **地上視点パノラマ（`sky_map_with_satellites(engine="ground")`）の不具合修正**: 実機で再現・画素計測して直した 11 点。**(1) 遮蔽高さ `occl` を「描いた地面レイヤの alpha」から測る**ようにした（建物矩形の top だけだと**塔・段状上層部・アンテナが入らず**、実シルエットは最大 70px＝6.3° 上・span=30 では 37% の列で上回っていた。塔の陰の衛星を `verify` が `missing` として `ok=False` にしていた＝正しい図がゲートを落ちる）。**(2) `when` の空白のみで TypeError がツール外へ漏れていた**のを「未指定＝現在時刻」に正規化。**(3) `when` の数値オフセット（`+09:00` 等）を無視して UTC として読んでいた**のを解釈して変換（9時間ずれた図が無警告で出ていた）。**(4) `show=` が月・太陽に効かなかった**（月が常に円盤＋名札で描かれていた）。**(5) TLE 取得失敗を無言で落として本文が「なし(地平線下)」と嘘を書いていた**のを、`satellite_errors` に理由を残し全機未取得なら「TLE を取得できなかったため」と明示（根因は `celestrak._fallback_catnr` が「1ホストの一時 5xx＋他ホスト該当なし」で全体を諦めていたこと。5xx は1回再試行・**全ホストが例外のときだけ送出**に変更 → 実測で TLE 6/6 取得）。**(6) ヘッダが画像幅に追従せず**、span を狭めると題名と右寄せ行が重なり両端が切れていた（W=333 で全幅にインク）→ 幅を測って左右2段／縦積み＋折り返しを選択。**(7) 「淡い星＝天の川の粒」を減光後の等級で判定**していたため地平線近くの明るい星が過剰に強調されていた（描画星の 3.59% が該当）→ 減光前の等級で判定。**(8) 視野内外の判定が描画（±30px 許容）・本文・verify で食い違い**、図に部分的に描かれる衛星を「図には描かれない」と書いていた → 単一の述語 `_in_frame` に統一し、**視野外の天体は本文に「地平線上だがこの視野の外」として列挙**・`figure.markers` に `in_frame` を付与。**(9) 銀河面が視野に入らないとき `milky_way` の平均が NaN** になり `structuredContent` が不正 JSON になっていた → 0.0 を返し本文も「銀河面はこの視野の外」と明示。**(10) 衛星の破線が 2 つの可視区間を繋ぎうる**（判定は `|Δx| < W/4` の経験則で、3日スキャンで最小 879px 対 閾値 850px＝余裕3%）→ 可視区間ごとに分割。**(11) `draw_ground` / `_set_lp` がモジュールグローバルを書き換えていた**（並行呼び出しで色・注記が混ざる）→ 色はパレット引数・光害は引数で伝搬。併せて `_nearest` の RA 0h/24h 巻き戻し、地平線閾値の統一（0.6°）、月の注記を月を描いたときだけに、`lp` 未対応値の本文明示、`show` 不一致の警告を追加。回帰テスト **320件**（新規 23 件 `tests/test_ground_view.py`。ネットワーク断でも検証できる契約テストを含む）、`--figures`・`--offline`・`--fuzz`（444 組・例外漏れ0）・`--fonts`・`--stdio`・`--concurrency`・`--dead-code` すべて exit 0。
- v0.39.0 — **ドイツ DLR（EOC）とロシア Roscosmos の地球観測データを追加（`dlr_collections` / `dlr_search` / `roscosmos_collections` / `roscosmos_search`）**: どちらも**認証不要の STAC** です。DLR EOC（`geoservice.dlr.de/eoc/ogc/stac/v1`・69 コレクション）は Sentinel-2 MAJA/WASP・EnMAP・DESIS・TROPOMI・GOME-2・WSF・TanDEM-X DEM 等を `POST /search` で検索します（`filter`(CQL2) は 400・`sortby` は無視されるので、雲量は**取得後にクライアント側で絞り**、`excluded_by_cloud` / `without_cloud_info` を分けて返す。ライセンスはコレクションごとの申告値＝CC-BY-4.0 / CC-BY-NC-4.0 / proprietary 混在）。Roscosmos（`s3.gptl.ru/stac-web-free`・200TB 超）は **`/search` を持たない静的 STAC** なので、`catalog.json → コレクション → 年 → 月 → 日` の木を辿って `rel=item` を列挙します（日ノードは親を辿らず直接 GET 可＝1 リクエスト。日付省略時はデータがある最新日まで `lookback` 日さかのぼる）。ホストは `s3.gptl.ru` / `s3ext.gptl.ru` の 2 つで、到達不可の側は記憶して切替（両方だめならエラー結果）。クイックルック（PNG）と GeoTIFF（EPSG は `proj:epsg` が無ければアセットキー `image.tif.ms.4326._` から読む）のリンクを返します。回帰テスト **294件**（新規 11 件 `tests/test_dlr_roscosmos.py`）、`--dead-code` 0件、`--offline`・`--fuzz`（444 組）・`--media-links`（問題 0）・`--only` 4 ツール exit 0。
- v0.38.4 — **宇宙天気に予報を追加（`space_weather(kind="forecast")`）**: DONKI は観測・警報の記録で**予報を持たない**（フレア発生確率・3日予報・活動領域別確率は NOAA SWPC のプロダクト）ため、`kind="forecast"` は DONKI を経由せず **SWPC を通常経路**として使う（障害時のフォールバックとは別物。`donki.space_weather` が `swpc.space_weather_forecast` へ振り分ける）。`solar_probabilities.json`（C/M/X 級と 10MeV 陽子の1〜3日確率）／`solar_regions.json`（活動領域ごとの確率・面積（太陽半球の百万分率）・黒点型・磁場型。**最新の観測日のみ**）／`3-day-forecast.txt`（予想最大 Kp と NOAA スケール、S1以上・R1-R2・R3以上の確率、**公式の根拠文**）を1回で返し、**30分キャッシュ**（`TTL_FORECAST`）。予報テキストは**折り返された行を繋いで**1文にする（実測: 「...likely on 09 / Oct due to ...」「...is 5.67 (NOAA Scale / G2).」）。欠測は `failed` に残し、1項目も取れなければエラー（空を「予報なし＝静穏」と誤読させない）。回帰テスト **256件**（新規8件 `tests/test_ssd.py::SwpcForecastTests`。`NewCraterSiteTests` 4件は変更前からの失敗で本変更と無関係）、`--stdio` に forecast 経路を追加、`--dead-code` 0件、全62ツール exit 0。実 API で `failed=[]`・M級 40%（1日）・領域 4549 の M 35%・予想最大 Kp 5.67（G2）・根拠3文を確認。

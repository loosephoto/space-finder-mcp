# Space Finder MCP 🚀

**宇宙・天文データを横断検索し、画像・動画・音声までチャット上で表示できる** Model Context Protocol (MCP) サーバーです。

惑星・人工衛星・宇宙ミッションのメディア検索、ロケット打ち上げ、小惑星接近情報、「史上初の宇宙望遠鏡は？」といった逆引き歴史Q&Aを、AIエージェント（Claude / Cursor / Hermes 等）から自然言語で呼び出せます。

> 「Space Finder」は**宇宙(Space)に関する情報を探す**MCPです。「物理的な空間＝オフィス・駐車場を探す」同名サービスとは無関係です。

---

## ✨ 特徴

- **画像・動画・音声をそのまま返せる** — 画像はチャット内にインライン表示、音声・動画は再生URLを返却
- **tokyo-transit方式のJSON応答** — 人間向け表示（`content`）とLLM向け純粋JSON（`structuredContent`）を分離し、情報を失わずに構造化データを渡せる
- **認証不要のツールが大半** — APIキーの管理なしですぐ動く（NASA の一部ツールのみ任意キー。ESA/Copernicus は検索・プレビューのみで、認証付きダウンロードは非対応）
- **他国の宇宙機関データに対応** — インド ISRO・欧州 ESA/Copernicus・日本 JAXA・カナダ CSA・ブラジル INPE・英国 EO DataHub・フランス CNES・中国 CNSA 系ポータル・全衛星軌道(CelesTrak)・EO Dashboard(NASA/ESA/JAXA共同) を横断検索
- **引用元を明示** — 科学的な内容には必ずデータソースへのリンクを併記
- **図の注記を機械可読で返す（figure/1）** — 描画系ツールは「どの面を・どの縮尺で・主天体を焦点に置いたか」と注記・自己検証を JSON で返し、LLM が図を誤読しないようにする

> ⚠️ **注（出力のどこまでが保証か）** — ツールが返す**`structuredContent` の JSON・数値・画像・リンクは、同じ引数で同じ時刻に呼べばどのモデルからでも同じ**内容です（データ取得と計算はサーバー側で完結し、モデルには依存しません）。一方、それを**読みやすい文章に組み立てる工程はホスト側の AI モデルに委ねられます**。説明の順序・語り口・要約の粒度・強調する点はモデルや設定によって変わるため、同じツール結果でも書き上がりは同一にはなりません。**数値や出典の最終的な根拠は `structuredContent` に置いてください**（表示文と食い違って見える場合は JSON 側が正で、その差は「モデルの文章化」のばらつきです）。

## 🧭 図の注記（figure/1）— 描画系ツールの自己申告

描画系ツール（`solar_system_now` / `sat_ground_track` / `planetary_orbiter_track` / `planetary_rover_location_map` / `sky_map_with_satellites` / `solar_eclipse_series` / `moon_phase_map` / `astronomy_weather`〔雨雲・降水画像を返すとき〕 / `space_calendar`）は、画像を返すだけでなく **`structuredContent.figure`（`schema: "figure/1"`）** に「その図をどう描いたか」を自己申告します。LLM はピクセルから描画規約を推測できないため（高離心率の軌道を「主天体の周りを回る円」と説明してしまう等）、注記をデータとして渡す設計です。

- **`view`** — どの面を（`frame`）どの投影で（`projection`）見た図か、なぜその視点なのか（`why`）
- **`primary`** — 主天体を**焦点**（`at: "focus"`）に置いたか**中心**（`"center"`）に置いたか、焦点と中心のズレ（`center_offset`）
- **`scale`** — 線形／対数（`type`）、実寸か（`to_scale`）、誇張している要素（`exaggerated`）
- **`conic`** — 軌道の円錐曲線（`kind`: ellipse / parabola / hyperbola、a / e / q / apo / c）
- **`notes`** — 図の誤読を防ぐ注記。**数値から生成**するので図と文が食い違いません
  - 地図タイルが取れなかった場合は「地図タイル N/M 枚を取得できませんでした（図の該当領域は背景色のまま）」も**数値から生成**します（欠けを黙って捨てると「地図に無い＝何も無い」と誤読されるため）
- **`verify`** — 図の自己検証（描いた画素から測った近点／遠点距離、ラベルの線被り画素数、`periapsis_check`: `equality` / `upper_bound`）
  - **近点が画面上で分解できない図**（超長距離の楕円では近日点が「誇張した主天体の円盤」の内側に入る）は、画素から近点距離を測っても意味がないため等値検査をせず、`periapsis_resolvable: false` ＋ 理由 ＋ **上界検査**（曲線が焦点から円盤半径以上に近点側へ伸びていないこと＝主天体を楕円の中心に置く誤りは依然として検出）に切り替えます。この場合の注記には「この縮尺では図から確認できない」旨が**数値から生成**されて入ります
- **`verify.panel_overlaps_marker`** — 地図の上に置く情報パネルが現在位置マーカーを隠していないか。パネルは `surface_map.panel_placement()` が**マーカーを隠さない隅**へ置き、どの隅でも重なる小さい図ではマーカーをパネルの上に描き直します（描いた後にパネルを重ねるとマーカーが消えるため。実測: 月面の LRO が図の上端に来ると左上のパネルの下に入り、画素検査 `marker_pixels` が 0 になりました）
- **`notes_usage`** — 注記の扱いをホスト LLM に伝える指示文（「要約・言い換えせず引用すること」）。**`structuredContent` 側にだけ入れます**
- **`caption`** — そのまま使える1〜2文の説明

`content`（人間向け表示）にも同じ注記を `### ⚠️ 図の注記` として出すので、`structuredContent` を使わないクライアントでも注記は失われません。なお **`content` は人間が読むチャネルなので、LLM への指示文（「要約せず引用してください」等）は入れません**（表示に指示文が混ざると読み手に意味不明な文が出るため。指示は docstring と `figure.notes_usage` ＝ JSON 側に置きます）。

```bash
uv run python scripts/check-tools.py --figures   # 描画系の figure/1 を検査（注記が空・verify.ok が偽なら exit 1）
uv run python scripts/check-tools.py --media-links # 画像/音声/動画のリンク先行を検査（画像より前にリンクが無い等で exit 1）
uv run python scripts/check-tools.py --concurrency # 並列ツール呼び出し（single-flight／スレッド逃がし／例外漏れ）を検査
uv run python scripts/check-tools.py --stdio       # 実クライアント経路（stdio）で代表ツールが無応答にならないか検査
```

## 🔀 並行ツール呼び出し（LLM が複数ツールを同時に投げる）

LLM は1ターンで複数のツールを並行に呼びます。**サーバー側がそれに応えられるかは別問題**で、FastMCP は同期関数をイベントループ上でそのまま呼ぶため、素のままだと1つのツールが API 待ちをしている間、他のツール呼び出しは1つも動き出しません（実測: 同一ツール4並列で wall = 各呼び出しの合計 2.55s）。

- **同期ツールは `anyio` のワーカースレッドで実行**（`server.py` の `_threaded` / `_reg`）。同じ4並列が **wall 2.47s・4.0×** になりました。実ネットワークでも、別々の地名の `astronomy_weather` 3並列が **3.93s**（逐次合計 11.72s・3.0×）です。
- **同じ引数の並行呼び出しは1回に集約**（`cache.ttl_cache` の single-flight）。8並列の同一呼び出しでも実行は1回・結果は共有なので、NASA の DEMO_KEY のような共有枠を N 倍消費しません。
- **matplotlib で描く経路は `img_common.RENDER_LOCK` で直列化**。pyplot はプロセス全体の状態（rcParams・現在の figure）を持つため、並列に描くと図が混ざります。
- **共有状態はロックで保護**（`cache` / `nasa_budget`）。キャッシュされた戻り値は呼び出し側で書き換えません（並行時に他人の結果を壊すため。書き換えるなら `deepcopy`）。
- **CPU 律速の計算は GIL で並列化しません**（Skyfield・Pillow の計算）。並列化が効くのは I/O 待ち（API 呼び出し）で、それが並列ツール呼び出しの主目的です。
- 検査: `uv run python scripts/check-tools.py --concurrency`（single-flight／ワーカースレッドへの逃がし／混在4ツールの並列呼び出しで例外漏れ・`structuredContent` 欠落が無いこと）。
- ⚠️ **遅延 import するネイティブ拡張は起動前に import 済みにしておきます**（`server.py` 冒頭の `import numpy`）。stdio サーバーが動き出した後に numpy / matplotlib / skyfield を import すると、**import が返らずツール呼び出しが無応答**になります（実測: numpy・matplotlib.pyplot・skyfield.api が HANG。PIL.Image・sgp4・requests は問題なし）。`scripts/check-tools.py --stdio` が実際に子プロセスを起動して代表ツールの応答を確認します。

## 🧭 名前解決のフォールバック（表記ゆれ・和名・名前→座標）

「自然な入力をしたのに 0 件／エラー」を防ぐため、名前の解決を **`name_common.py` の共通段階**に集約しています（各ツールが個別に対処すると必ず穴が残るため）。

| 段階 | 内容 | 実例（実測） |
|---|---|---|
| 1. 内蔵テーブル / 既知名 | 速い・オフライン | `M31`, `iss`, `hubble` |
| 2. **表記ゆれ**（空白/アンダースコア/連結/大小） | アーカイブは観測者の入力そのままを持つ | `HL Tau` → `HL_Tau`/`HLTau` も一致（ALMA の `target_name`） |
| 3. **和名 → 英語名** | `JA_ALIASES`（衛星・深宇宙天体） | 「ひまわり」→himawari、「ひので」→29479、「オリオン大星雲」→Orion Nebula |
| 4. **名前 → 座標** | Sesame/CDS（SIMBAD・NED 横断・認証不要） | `M104`/`Sombrero`/`HL Tau` → 座標 → 円錐検索（CADC） |
| 5. それでも駄目なら **候補を提示して停止** | 推測で検索しない | ALMA はアーカイブ内の候補名、CelesTrak は既知の名前一覧を提示 |

- **`JA_ALIASES` は 103 キー**: 日本の気象・地球観測・科学衛星（ひまわり/だいち/いぶき/しずく/しきさい/みちびき/ひので/あかつき/はやぶさ2…）、有人・輸送（宇宙ステーション/きぼう/こうのとり）、宇宙望遠鏡（ハッブル/JWST/スピッツァー/ケプラー/ガイア…）、中国機（天宮/天和/問天/夢天/神舟/嫦娥…）、深宇宙天体（銀河・星雲・星団17件）、明るい恒星16件。
- **現役衛星は `WELL_KNOWN` に NORAD ID を実測登録**（CelesTrak `GROUP=active` の OBJECT_NAME と突合）: ひまわり8/9号、だいち2/4号、いぶき/いぶき2号、しずく、しきさい、あじさい、あらせ、れいめい、ひさき、天和/問天/夢天、ISS、ハッブル、ひので 等 → **和名から NORAD ID までオフラインで解決**（衛星系キーの 21/61。残りは過去機・番号なしのファミリー名で、曖昧なら候補提示、退役機は「表示できない」と正直に返す）。
- 適用先: `alma_search`（表記ゆれ＋候補提示）/ `cadc_observations`（名前→座標のフォールバック）/ `satellite_status`（和名展開＋一致件数の明示）/ `sat_tle`（和名→NORAD ID 解決＋候補提示）
- ⚠️ 修正の根拠は実測: 以前は `cadc_observations("M104")` が即エラー、`satellite_status(query="ひまわり")` と `sat_tle(name="ひので")` が 0 件、`alma_search("HL Tau", band=7)` が 0 件でした（すべて現在は解決）。

## 🔗 メディアのリンク — インライン表示できないクライアント向け

Hermes Agent のようなリッチなクライアントは `ImageContent` をそのまま描画しますが、**CLI系・Android系のハーネス（codex / opencode など）は画像ブロックを無視**するため「画像が生成されたのに何も表示されない」状態になります。そこで **メディア本体より前に、アイコン付きの markdown リンク**を必ず出します。

| 種別 | 出る場所 | 形 |
|------|----------|-----|
| **生成画像**（描画系8ツール） | `content` の**先頭行** | `🖼️ [生成した画像を開く（…）](file:///…) ｜ 保存先: \`…\`` |
| **検索した画像**（`search_space_images`） | 各項目の説明の直後 | `🖼️ [画像を開く: タイトル](URL)` |
| **検索した音声**（`search_space_audio`） | 各項目の直後 | `🎧 [音声を開く: タイトル](URL)` |
| **検索した動画**（`search_space_videos`） | 各項目の直後 | `🎬 [動画を再生: タイトル](URL)` ＋ `🖼️ [ポスター画像を開く: …](URL)` |
| **気象衛星の実画像**（`weather_satellite_now`） | `content` の**先頭行** | `🖼️ [生成した画像を開く](画像URL)` ＋ `file://` の保存先 |
| **気象庁の雨雲・降水画像**（`astronomy_weather`） | 各画像の直前行 | `🖼️ [◯◯を開く: ラベル](気象庁のページURL)` |
| **EO Dashboard のサムネイル**（`eodashboard_detail`） | 説明の直後・画像の直前 | `🖼️ [サムネイル画像を開く: タイトル](URL)` |
| **APOD の画像URL**（`apod`） | タイトルの直後 | `🖼️ [画像を開く: タイトル](URL)`（動画の日は `🎬`） |

- 生成画像は `%LOCALAPPDATA%\Temp\space_finder_mcp\out\<tool>_<日時>_<乱数>.<ext>` に**生バイトのまま保存**し、`file://` URI と実パスの両方を出します（最新200件を残して自動整理）。
- 同じパスを `structuredContent.image_path` にも入れるので、LLM はファイルを再参照できます。
- URL 内の空白・括弧は `%20` / `%28` にエスケープします（NASA のアセットURLには空白入りの動画名があり、生のままだとリンクが途中で切れます）。
- `content` の並びは常に「リンクを含むテキスト → `ImageContent`」なので、**画像を描けないクライアントでもリンクは必ず見えます**。
- **機械的に検査します** — `scripts/check-tools.py --media-links`（画像ブロックより前にアイコン付きリンクが無い／`structuredContent` にURL・保存パスが無い／`image_path` のファイルが存在しない場合は exit 1。実測: 画像を返す12ツールすべて OK）。生成系の docstring には「回答時はこのリンクをそのまま提示してください」と明記しています。
- **複数の画像を続けて返すときは、caption とリンクの間を空行にします** — Markdown は同一段落内の単一改行をスペースに畳み込むため、単一改行で組むと隣り合う画像の caption / リンクが **1行に融合**します（1枚だけ返すときは段落が分かれるので気付きにくい。実測: `astronomy_weather` の気象庁画像2枚）。

### 🆕 直近の更新内容（v0.37.1）

**「彗星の通過経路の近日点日付に JPL Horizons の n 体解を併記する（SBDB の2体近似が実際の回帰と最大 164 日ずれていた）」**（v0.37.1）。

- **☄️ 何が変わるか** — `solar_system_now(comet=..., route=True)` の◇近日点の日付は、これまで **JPL SBDB の2体近似だけ**でした。SBDB の要素は古いエポックの接触軌道なので、摂動の大きい彗星ではこれが実際の回帰と大きく食い違います（実測 1P/Halley: 2体近似 2062-01-08 に対し **Horizons の n 体解 2061-07-28 ＝ 163.8 日の差**。67P 82.6日、エンケ彗星 0.8日）。本文と `figure.notes` に**両方の日付と差の日数**を出します（`marks[0].date_nbody` / `nbody_diff_days` / `comet_routes[].tp_nbody_date`）。
- **🔎 求め方** — Horizons の `ELEMENTS` が出す Tp は「そのエポックでの接触軌道の近日点通過時刻」なので、**エポックを直前の Tp に置き直して反復**して収束させます（実測: エポック 2026-09-23 → 2061-08-04、エポック 2062-01-08 → 2061-07-28.7、次の反復で 0.01 日以内に安定。1日1回キャッシュ）。
- **🛡️ 取れないときは黙らない** — Horizons が遮断・応答異常のときは `nbody_error` に理由を入れ、注記に「n 体解を取得できなかったためずれは示せない」と出します（2体近似だけを黙って出さない）。C/彗星（Horizons 要素＝すでに n 体解）は分岐して2体近似と混同しません。
- **✏️ 誤記の修正** — 旧注記の「惑星の摂動で実際の回帰は**数日**ずれる」は言い切りが誤り（実測 0.8〜164 日）でした。また「見え方チャートの近日点は Horizons の n 体解」も誤り（チャートで n 体解なのは**位置**で、前回・次回の近日点は同じ SBDB 2体近似の概算）なので、実装に合わせて直しました。
- **🧪 検証** — `--dead-code`（0件）／`--figures`（描画系9・`verify_ok`）／`--offline`／`--fuzz`（372組合せ・例外漏れ0）／`--media-links`（15ツール・0）／`--fonts`／`--concurrency`／`--stdio`（すべて exit 0）／回帰テスト **174件 OK**（新規2件・ネットワーク不要のスタブ）／全58ツール実呼び出し（exit 0・51 OK・7 ERROR は既知の外部要因＝NASA DEMO_KEY 429×2・CelesTrak 遮断×3・TART タイムアウト・Wikidata タイムアウト）。

登録ツールは **58本**。

### 以前の更新

- **v0.37.0** — **惑星の記述を一次文献（DOI 付き）で裏づける学術文献ツールを追加（`space_literature_search` / `planetary_evidence`、56→58ツール）**。OpenAlex / Crossref / NTRS / JAXAリポジトリ / J-STAGE / CiNii / Zenodo / DataCite を横断し DOI 重複を統合。日本語クエリは英語語へ置換してから英語圏ソースへ投げる（置換不能ならスキップ理由を返す）。任意キー（`ADS_API_KEY` / `S2_API_KEY` / `WOS_API_KEY`）未設定でも全機能が動きます。

## 📦 インストール

### 前提
- [uv](https://docs.astral.sh/uv/)（Python 3.11+）
- 画像を返すツールの日本語表示には OS の日本語フォントを使います（Windows: メイリオ / macOS: ヒラギノ / Linux: Noto CJK 等。無い環境では下記「日本語フォント」参照）

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

### 日本語フォント（画像内の文字）

画像を返すツール（`sat_ground_track` / `moon_phase_map` / `solar_eclipse_series` /
`sky_map_with_satellites` 等）は、図中の日本語を描くために **Windows / macOS / Linux それぞれの
標準日本語フォント** を自動で探します（`img_common.py`）。探索順は
**(1) 環境変数 → (2) OS 標準パス → (3) 標準フォントディレクトリの走査** で、
採用する前に **そのフォントが実際に日本語グリフを持っているかを cmap で検証**します
（名前だけでは判定できないため。例: DejaVu Sans は日本語なし）。

| OS | 主な採用フォント |
|---|---|
| Windows | メイリオ（`meiryo.ttc`）/ 太字はメイリオ Bold（`meiryob.ttc`）→ Yu Gothic → MS Gothic |
| macOS | ヒラギノ角ゴシック W3（太字は W6）→ Arial Unicode MS |
| Linux | Noto Sans CJK JP → IPAex ゴシック → IPA ゴシック → VL ゴシック / Takao ゴシック |

日本語フォントが1つも入っていない環境（最小構成の Linux 等）では画像の日本語が**豆腐（□）**に
なります。その場合は日本語フォント（例: `fonts-noto-cjk` / `fonts-ipaexfont-gothic`）を入れるか、
環境変数で明示指定してください。

```bash
export SPACE_FINDER_FONT="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
export SPACE_FINDER_FONT_BOLD="/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
```

現在の解決結果は検証ゲートで確認できます（日本語グリフを持たないフォントを掴んでいれば exit 1）。

```bash
uv run python scripts/check-tools.py --fonts
```

`engine="accurate"`（matplotlib）の `sky_map_with_satellites` / `solar_system_now` も同じフォントを使います
（`img_common.apply_matplotlib_cjk_font()`）。

### （任意）NASA APIキー

`apod`・`neo_today` は [api.nasa.gov](https://api.nasa.gov) の無料キーを使います。未設定でも `DEMO_KEY` で動作しますが、**レート制限 30 req/hr/IP** と低く、`space_weather`(DONKI) も同じ枠を共有するため、キー未設定だと3ツールが枠を取り合います。実用にはキーを推奨します。なお **`fireball_reports` / `neo_close_approach` / `impact_risk`（JPL CNEOS）と `space_weather` の SWPC フォールバックは認証不要**で、この枠を消費しません。

**呼び出し回数はサーバー側で管理しています**（`nasa_budget.py`）— 直近1時間の使用数を数え、枠を使い切っていたら**HTTP を投げずに**回復までの目安（例:「約30分後に自動的に回復します」）を返します。実際に 429 を受けた場合は `Retry-After` を尊重し、その間は再試行しません（同じ 429 を繰り返し踏みに行かない）。**CelesTrak** も短時間の連続リクエストで IP 単位に遮断され（403、または TCP が返らない blackhole）、その間は 1 回の呼び出しが分単位で固まります。接続は (connect 10 秒, read 25 秒) で打ち切り、遮断を受けたら**セッション内で記憶して以降は HTTP を出さずに即座に案内**を返します（`sat_tle` / `sat_ground_track` / `tiangong_now` / `sky_map_with_satellites` が該当）。 さらに **TLE は代替源へ自動フォールバック**します — CelesTrak 遮断中は認証不要の公開ミラー（tle.ivanstanojevic.me → db.satnogs.org）から同じ衛星の TLE を取得し、**content / structuredContent にどちらから取ったかを明示**します（`tle_source`。代替源の TLE を「CelesTrak の TLE」と書かないため）。グループ検索（`group=`）は代替源に無いので、遮断中はその旨を案内します。

**キーの取得方法（無料・即時発行）**: [api.nasa.gov](https://api.nasa.gov) にアクセスし、**メールアドレスを登録するだけで** 無料の API キーが即時発行されます。無料開発者キーのレート制限は **1時間あたり 1,000 リクエスト** です（実用に十分な容量）。登録時に入力したメール宛てに確認が来ます。

```bash
# (a) 環境変数で渡す
export NASA_API_KEY="your_key_here"

# (b) リポジトリ直下の .env に書く（MCPクライアントの設定にキーを書きたくない場合）
cp .env.example .env   # 編集して NASA_API_KEY=... を記入（.gitignore 済み・コミット禁止）
```
優先順位は **MCPクライアントの env > リポジトリ直下の .env** です（未設定なら `DEMO_KEY`）。


> **キーの取り扱い**: 応答に含まれる API キーは伏せ字化します（`nasa_budget.redact()` → `api_key=***`、`budget.key` は `DEMO_KEY` / `custom` のみ）。requests の例外文字列が URL ごとキーを含むため、そのまま返すと利用者のキーが漏れるためです。

### ESA Copernicus について（認証付きダウンロードは非対応）

`copernicus_search` の**検索とプレビューURL取得は認証不要**で動作します。本サーバーは **OAuth2 による認証付きダウンロードは行いません**（`CDSE_CLIENT_ID` 等のクレデンシャルは使用しません）。実際の画像ダウンロードが必要な場合は [Copernicus Data Space](https://dataspace.copernicus.eu) で取得してください。

---

## 🔌 MCPクライアントへの登録

### Claude Code

```bash
cd /絶対パス/space-finder-mcp
claude mcp add -s project space-finder -- uv --directory "$(pwd)" run space-finder-mcp
claude mcp list   # 確認（space-finder が表示されればOK）
```

`-s project` はリポジトリ直下に `.mcp.json` を作ります（共有向け）。個人利用なら `-s user` を指定します。

プロジェクトガイドは [`CLAUDE.md`](CLAUDE.md)、開発規約は [`.claude/rules/`](.claude/rules/) にあります（Claude Code が自動で読み込みます）。

### Codex

```bash
codex mcp add space-finder --env NASA_API_KEY=<your_key> -- uv --directory <絶対パス>/space-finder-mcp run space-finder-mcp
codex mcp list
```

`--env` は省略可（その場合は `.env` か `DEMO_KEY` を使用）。エージェント向けガイドは [`AGENTS.md`](AGENTS.md) です。

### Claude Desktop / Cursor / その他（`claude_desktop_config.json` / `mcp.json`）

同梱の [`mcp.json`](mcp.json) を参考に設定してください（`args` のパスは環境に合わせて書き換えます）。

```json
{
  "mcpServers": {
    "space-finder-mcp": {
      "command": "uv",
      "args": ["--directory", "/絶対パス/space-finder-mcp", "run", "space-finder-mcp"]
    }
  }
}
```

> `env` を書く場合は**空文字を入れない**でください（空だと `DEMO_KEY` にフォールバックしません）。キーは `env` か `.env` のどちらか一方に置けば十分です。

### エージェント向けドキュメント

| ファイル | 用途 |
|:--|:--|
| [`CLAUDE.md`](CLAUDE.md) | Claude Code 用プロジェクトガイド（セットアップ・規約・検証・リリース手順） |
| [`AGENTS.md`](AGENTS.md) | Codex など `AGENTS.md` を読むエージェント向けガイド |
| [`SKILL.md`](SKILL.md) | エージェント向けスキル定義（53ツールの仕様・使用例・キャッシュ・注意事項） |
| [`.claude/rules/`](.claude/rules/) | 開発規約（コーディング・検証ゲート・データ出典） |

### Hermes Agent

```bash
hermes config set mcp_servers.space-finder-mcp.command uv
hermes config set 'mcp_servers.space-finder-mcp.args' '["run", "--project", "/絶対パス/space-finder-mcp", "space-finder-mcp"]'
# 反映には再起動
```

---

## 🛠️ ツール一覧

登録ツールは **58本**（宇宙・天文イベントカレンダー 4本（`space_calendar` / `calendar_events` / `calendar_event_add` / `calendar_event_remove`）＋ロケット打ち上げ・逆引き 4本＋NASA日次・宇宙天気 3本＋メディア 3本＋各国宇宙機関・地球観測 16本＋衛星・軌道 5本＋天体位置・画像合成 7本＋観測支援・天文データ 10本＋気象衛星リアルタイム画像 1本＋天体異常系 3本＋**学術文献 2本**）。全58ツールを実呼び出しで検証済みです（外部APIの障害・レート制限時は、例外ではなく CallToolResult のエラーとして返します）。

| ツール | できること | データ源 | 認証 |
|--------|-----------|---------|------|
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
| `copernicus_search` | ESA Copernicus の衛星画像を STAC で検索（領域・日時・雲量） | Copernicus Data Space (STAC) | 不要（検索・プレビューのみ） |
| `jaxa_datasets` | JAXA Earth の地球観測データセット一覧（ALOS/GSMaP/GCOM等） | JAXA Earth API (STAC COG) | 不要 |
| `jaxa_dataset_search` | JAXA Earth データセットをキーワード検索 | JAXA Earth API (STAC COG) | 不要 |
| `csa_dataset_search` | カナダCSAオープンデータ（RADARSAT等）を検索 | CSA Open Data Portal (CKAN) | 不要 |
| `inpe_collections` | ブラジルINPEの衛星データコレクション一覧 | INPE BDC STAC | 不要 |
| `inpe_search` | ブラジルINPEの衛星画像（CBERS・Amazonia-1等）をSTAC検索 | INPE BDC STAC | 不要 |
| `sat_tle` | 全世界の衛星の軌道要素(TLE)を取得（ISS・ハッブル・気象衛星等） | CelesTrak (NORAD) | 不要 |
| `uk_stac_collections` | 英国EO DataHubのコレクション一覧 | UK EO DataHub STAC | 不要 |
| `uk_stac_search` | 英国EO DataHubの衛星・気候データをSTAC検索 | UK EO DataHub STAC | 不要 |
| `cnes_status` | フランスCNESのポータル（THEIA/GEODES）到達状態・概要 | CNES THEIA/GEODES | 不要(ダウンロードは要登録) |
| `astronomy_weather` | 天体観測に最適な夜間の時間帯を予報（雲量・視程・風速・降水・**月相・月明かり**から判断）。**日本国内の地点では気象庁の雨雲・降水画像（解析雨量・降水短時間予報パネル＋ナウキャスト、雷の有無も確認）を画像で同時に返す**（`include_rain=false` で無効化） | Open-Meteo + 気象庁（ナウキャスト・解析雨量） | 不要 |
| `constellation_now` | 指定地点・時刻で太陽・月・惑星の高度・方位・星座を計算（観測可否判断） | Skyfield + JPL de421 | 不要 |
| `astronomy_news` | 最新の天文ニュース・「今週の星空ガイド」を取得（観測/ニュース絞込可）。取得不可時は他ソースへ自動フォールバック | Sky & Telescope / Universe Today / NASA / Phys.org RSS | 不要 |
| `mars_rover_status` | 火星探査ローバー（キュリオシティ等）の現在の状況・天気・ソルを表示 | NASA Mars Weather | 不要 |
| `power_climate` | 任意地点の過去の気候・太陽エネルギー統計（気温・日射量・風速） | NASA POWER | 不要 |
| `eso_seeing` | ESO パラナル天文台（チリ, VLT）のリアルタイム大気コンディション（シーイング・可降水量・気象） | ESO ASM API | 不要 |
| `cadc_observations` | CADC（カナダ天文データセンター）の観測データ検索（HST・ジェミニ等） | CADC TAP | 不要(画像DLは一部要登録) |
| `gcn_alerts` | **NASA GCN（General Coordinates Network）の過渡天体速報**。GRB・X線新星・重力波などの **GCN Circular** を期間（`days` 1〜60）／キーワード（`query`）で新しい順に一覧し、`circular_id` で1件の本文（投稿者・観測時刻・本文）まで返す。一覧は公開アーカイブの HTML が既定（サイト内部の JSON ルートは 403 を返すことがあるためフォールバック）。⚠️ 機械可読の Notices は Kafka 配信のため対象外 | NASA GCN (gcn.nasa.gov) | 不要 |
| `mast_observations` | **MAST（NASA/STScI の宇宙望遠鏡アーカイブ）の観測データ検索**。JWST・ハッブル(HST)・TESS・Kepler・GALEX 等を、天体名（和名可・Sesame で座標解決）／座標コーン／装置（部分一致）／データ種別／観測日で検索。観測ID・装置・観測日・校正レベル・フィルタ・`Mast.Caom.Products` による FITS のダウンロードURL（`include_products=true`）まで返す。`preview_image=true` なら先頭観測のプレビュー画像を**チャットにインライン表示**（🖼️リンク先行＋保存パス）。校正用露出（BIAS/DARK）は既定で除外。⚠️ MAST は混雑時に1クエリ 60 秒級（実測）＝結果は30分キャッシュ | MAST Mashup API | 不要 |
| `alma_search` | ALMA（アルマ望遠鏡）科学アーカイブの観測データ検索（観測対象・座標・周波数帯・公開/要権限） | ALMA Science Archive (NAOJ, IVOA TAP) | 不要 |
| `space_literature_search` | **惑星科学・宇宙の一次文献（論文・技術報告）を横断検索**して根拠（DOI付き）を返す。OpenAlex（要旨・被引用数・OAリンク）・Crossref・NASA NTRS（技術報告＋PDF）・JAXAリポジトリ・J-STAGE・CiNii Research・Zenodo・DataCite を1回で横断し、DOI/タイトルの重複を統合。**日本語クエリは語彙辞書＋和名テーブルで英語語へ置換してから英語圏ソースへ投げる**（実測: OpenAlex/Crossref は日本語クエリだと無関係な文献を返すため）。`sort`（関連度/被引用数/年）・`year_from`/`year_to`・`min_citations`・`open_access_only`・`planetary_only`（惑星科学概念に限定）・`sources` で絞り込み。⚠️ 返すのは**文献（書誌）**で観測データではない（観測は `mast_observations` / `alma_search` / `cadc_observations`） | OpenAlex / Crossref / NASA NTRS / JAXAリポジトリ / J-STAGE / CiNii / Zenodo / DataCite | 不要（`ADS_API_KEY`・`S2_API_KEY`・`WOS_API_KEY` を設定すると ADS・Semantic Scholar・WoS も使う） |
| `planetary_evidence` | **天体（惑星・衛星・小天体・探査機）の文献的な裏づけをまとめて返す**。①名前解決（Sesame/CDS で和名→英語名→座標）と、②その天体の文献を**英語圏＋日本語の両方**から集めて提示（既定は被引用数順。**惑星科学概念で絞った OpenAlex を上位に置く**＝実測「火星」で MOLA / OMEGA-Mars Express / ALH84001 が上位に来る）。⚠️ 太陽系天体は時刻で位置が変わるため固定座標を持たない旨を `caveats` に明記 | OpenAlex / Crossref / NASA NTRS / JAXAリポジトリ / J-STAGE / CiNii ＋ Sesame/CDS | 不要 |
| `radio_sources_now` | TART オープン電波望遠鏡が「いま観測できる電波源」（GNSS・静止衛星等）を仰角順に表示 | TART source catalog (NZ) | 不要 |
| `sky_map_with_satellites` | 指定地の空に太陽系の惑星と人工衛星を重ねた画像（matplotlib正確版=PNG/Pillow簡易版=JPEGを選択） | JPL de421+Skyfield / CelesTrak+SGP4 | 不要 |
| `solar_system_now` | 太陽を中心とした太陽系の惑星・小惑星・探査機・彗星の現在位置俯瞰図（ハレー等の周期彗星とC/彗星・ボイジャー等の遠方天体まで対数縮尺で自動拡張表示）。`view="comet_orbit"` で彗星の軌道面ビュー（太陽＝焦点の楕円／e≥1 は双曲線の枝）。**`comet` にカンマ区切りで複数（または `comet2`、最大4天体）指定すると 1彗星=1パネルで1枚に並べる**（パネルごとに軌道面と縮尺が違う旨は `figure.notes` に自動生成）。`view="apparition"` で彗星の**見え方チャート**（地心距離・日心距離・予想光度・太陽離角の時系列、`days` で期間指定。1天体ずつ）。`route=True` で**彗星の通過経路（軌道）を俯瞰図に破線で重ねる**（近日点・遠日点は◇＋日付。**近日点の日付は SBDB の2体近似と JPL Horizons の n 体解を併記**し、差を日数で注記。対数縮尺では線の形は実際の楕円と一致しない旨を注記に自動生成し、線形版は形が本当の軌道）。**`range_au` で表示範囲（太陽からの距離の上限 AU）を指定でき、`range_au=10` なら土星(9.5 AU)より内側だけを表示して内惑星を大きく見せられる**（`2`=火星まで／`5.2`=木星まで／`30`=海王星まで。**範囲外の天体・目印・軌道の円は描かず**、`figure.notes` と `structuredContent.out_of_range` に数値付きで列挙。0.5 AU 未満はエラー）。**`probe` 指定時は「1光日」（173.1446 AU＝25,902,068,371 km）を破線の円で描き、1光日に達していない探査機**ごとに**投影での1光日リング・到達時の方向（◇）・到達予測日（日心距離ベースの線形外挿＝`probes[].light_day_eta_date`）を出す（`light_day.projected_rings` と `figure.verify.light_day.rings` に各リングを列挙）**） | JPL DE421+Skyfield / JPL SBDB / JPL Horizons | 不要 |
| `solar_eclipse_series` | 日食（太陽が月に欠ける過程）の時系列パネル画像（7枚・食分と太陽高度・次回日食の自動検索=約4年(1400日)先まで・max_magnitude対応。**地平線下で見えない食は返さない**） | JPL DE421+Skyfield | 不要 |
| `moon_phase_map` | **月齢マップ**（月の満ち欠け）。`layout="calendar"`（既定）で1か月の日別格子（日月火水木金土・月齢・照度・月相）、`layout="lunation"` で1朔望月（朔→朔）の時系列パネル。輝面の向きは太陽の位置角から計算（月齢からの決め打ちをしない）。朔・望・上弦・下弦の時刻を現地時間で併記 | JPL DE421+Skyfield | 不要 |
| `eodashboard_collections` | EO Dashboard（NASA×ESA×JAXA共同）の173データセットをテーマ・機関・キーワードで検索 | EO Dashboard (GitHub catalog) | 不要 |
| `eodashboard_detail` | EO Dashboardの1データセットの詳細（衛星・センサー・説明・画像・参照リンク） | EO Dashboard (GitHub catalog) | 不要 |
| `space_weather` | 宇宙天気（太陽フレア・CME・地磁気嵐・太陽粒子現象）。**NASA が枠切れ・障害のときは認証不要の NOAA SWPC（Kp・NOAAスケール・GOES X線・フレアイベント（直近7日）・太陽風・陽子・警報・黒点）へ自動切替**（出典を明記） | NASA DONKI → **NOAA SWPC（フォールバック）** | 不要（SWPC）/キー任意（DONKI） |
| `fireball_reports` | **火球（大気圏突入）の観測記録**（直近1〜1825日・衝突エネルギーの下限指定可）。緯度経度・突入高度・**衝突エネルギー(kt)**・放射エネルギー(J)・突入速度（成分から算出）。広島型原爆（約15kt）との比を併記。⚠️ 隕石の回収情報ではなく**大気圏突入の観測**。**呼ぶと結果がカレンダーの蓄積ストアに入り、後日 `space_calendar` / `calendar_events` に出ます**（`calendar_stored` に件数） | NASA/JPL CNEOS Fireball Data | 不要 |
| `neo_close_approach` | **小惑星・彗星の地球接近**（1〜365日先・距離上限 au）。地心距離を **au / km / 月距離**で併記し、既知の直径（無ければ絶対等級 H から**推定**・アルベド0.14 仮定）と接近時刻の不確かさを返す。`hazardous_only` で PHA のみ。⚠️ 接近は衝突ではない。**呼ぶと結果がカレンダーに蓄積され、後日 `space_calendar` / `calendar_events` に反映されます**（接近時刻は TDB・UTC とは最大約1分差） | NASA/JPL CNEOS SBDB CAD | 不要 |
| `impact_risk` | **将来の衝突リスク**（JPL Sentry）。累積衝突確率を**確率＋「何回に1回」**で表示し、パレルモスケール・想定時期・想定衝突数を返す。`designation`（**仮符号/番号のみ・和名不可**）を指定すると仮想衝突(VI)の一覧まで。⚠️ 確率は特定の日付ではなく**数十年〜百年の幅**に対する値なので**カレンダーには置きません** | NASA/JPL CNEOS Sentry | 不要 |
| `stac_collections` | AWS Earth Searchの衛星データコレクション一覧 | AWS Earth Search STAC | 不要 |
| `stac_search` | Sentinel-2 / Landsat / NAIP / DEM をSTAC検索（場所・日時・雲量） | AWS Earth Search STAC | 不要 |
| `iss_now` | ISS（国際宇宙ステーション）の現在位置を取得し Googleマップリンクで表示 | Open Notify | 不要 |
| `sat_ground_track` | 任意の人工衛星（ISS・ひので・ハッブル等）の現在位置と地上軌道を地球地図にプロットした画像を返す。CelesTrak TLE + Skyfield(SGP4) で真下の点・高度・速度を計算し、NASA Blue Marble 地図に軌道トレイルを重ねる | CelesTrak + Skyfield + Blue Marble | 不要 |
| `planetary_orbiter_track` | 任意の天体（月・火星・水星・タイタン等）を周回する探査機の現在位置と軌道トレイルを、その天体の地図にプロットした画像を返す。JPL Horizons の状態ベクトルを IAU 自転モデルで天体固定座標（緯度経度・高度）に変換し、NASA Trek の等角図法地図に重ねる。`span_deg=360`で天体全面表示にも対応。**過去機（かぐや等）は運用終了を案内し、落点が公表されている機体は落点を地点マーカーで描いた地図（`figure.kind="impact_site_map"`）を返す**。`sites="apollo"`（または `"apollo11"`〜`"apollo17"`, `"all"`）で **地点マーカー図**（`figure.kind="landing_site_map"`／木星は `impact_site_map`）も返す。**全球等角図がある天体（月・火星・水星・タイタン・ベスタ・ケレス）は地形画像の上に、無い天体（木星・土星・冥王星・金星・ガリレオ衛星など）は緯度経度グリッドの上に**公表座標を描く | JPL Horizons + NASA Trek / NASA NSSDC / PDS | 不要 |
| `planetary_rover_location_map` | 任意の天体面を移動する探査ローバーの現在地をその天体の地図中心に示した画像（走行経路・着陸点）。NASA MMGIS の位置データと NASA Trek の等角地図を合成。現状データは火星ローバー（Perseverance/Curiosity） | NASA MMGIS + Trek WMTS | 不要 |
| `weather_satellite_now` | GEO/LEO気象衛星19機（ひまわり9号・GOES-18/19・Meteosat・FY・GK-2A・INSAT・NOAA-20/-21・SNPP・Metop-B/C・FY-3D）の公開画像。GEOは最新フレーム、LEOは日次全球合成。取得不可は `restricted`/`unavailable`/`non_image_product` の理由コードで返す | JMA(himawari.asia)/NOAA STAR/EUMETSAT WMS/CMA-NSMC/KMA/IMD/NASA GIBS | 不要 |
| `space_calendar` | **宇宙・天文イベントカレンダー**（月グリッド図＋JSON）。打ち上げ（LL2 の月範囲クエリ・日付精度 `net_precision` が Day/Hour/Minute/Second の行だけを日付セルに配置）・天文現象（Skyfield のローカル計算: 月相・二十四節気・惑星の衝/合/内合・最大離角・日食月食・流星群）・公開イベント（国立天文台＋**JAXA の施設一般公開・特別公開**＝ファン!ファン!JAXA! の施設見学ページと宇宙科学研究所のイベント表。告知済みのみを載せ、取得日を窓にして毎日取り直します）・**小天体イベント（小惑星接近・火球観測。`neo_close_approach` / `fireball_reports` の呼び出し結果を蓄積したもの＝カレンダー自身は JPL を叩かないので未蓄積の月は空）**・自分の予定を重ねて描く。取得結果は**蓄積ストア**に溜め、要求月 M に対して窓 [M-1, M+2] を確保し**未取得・期限切れの月だけ**取りに行く（2回目は API 0 回）。NASA の公式リスト（WP REST）と照合した行には公式URLを付与 | Launch Library 2 + JPL DE421/Skyfield + 国立天文台 + JAXA + nasa.gov | 不要 |
| `calendar_events` | 蓄積済みイベントの一覧（**図を描かず外部APIも呼ばない**軽い経路）。打ち上げ・天文現象・**小惑星接近・火球観測（`neo_close_approach` / `fireball_reports` の呼び出しで蓄積されたぶん）**・公開イベント・自分の予定を日付順に返す | 蓄積ストア（ローカル） | 不要 |
| `calendar_event_add` | **自分の予定をカレンダーに追加**（ローカル保存・外部送信なし）。日付は `2026-10-24` / `10月24日` 形式、時刻・終了日・繰り返し（毎日/毎週/毎月/毎年）対応。**フローティングなローカル日時**なので `place` を変えても予定の日付は動かない | ローカル（`%LOCALAPPDATA%\space-finder-mcp`） | 不要 |
| `calendar_event_remove` | 自分の予定を削除（id か title[+date]）。冪等（既に無ければエラーにしない）。同名が複数あるときは**削除せず候補を提示**して停止 | ローカル | 不要 |
| `satellite_status` | 世界中の気象・地球観測衛星の運用ステータス・軌道・打ち上げ日（Roscosmos等）。**カタログ全1,000件超を走査**し、query は一致度順（acronym 完全/前方一致 → 名称の語境界 → 部分一致のみ）で提示 | WMO OSCAR | 不要 |
| `cnsa_status` | 中国CNSA系衛星データポータル（風雲/NSMC・高分/CNSA-GEO・CBERS/CRESDA）の到達状態・概要＋認証不要の代替経路 | CNSA各公式ポータル | 不要(ダウンロードは要登録) |
| `tiangong_now` | 天宮（Tiangong）中国宇宙ステーションの現在位置（SGP4伝播＋Googleマップ表示） | CelesTrak TLE + SGP4 | 不要 |

---

## 📚 学術文献（惑星の根拠）— 使えるAPIの実測結果

惑星・衛星の説明を Wikipedia / Wikidata と LLM の既存知識だけで書くと**根拠（一次文献）を示せません**。
そこで公開の書誌 API を実測し、**認証不要で実際に使えたもの**だけを `space_literature_search` /
`planetary_evidence` に組み込みました（2026-09 実測）。

| API | 実測結果 | 本サーバーでの扱い |
|:--|:--|:--|
| **OpenAlex** | 200。要旨（abstract_inverted_index を復元）・被引用数・OAリンクを返す。⚠️ **`search=`（全文）は日本語クエリを解釈できず無関係な文献を返す**（実測:「月 永久影 水氷」→ IPBES 生物多様性評価レポート、「火星 大気脱出」→ 草津白根火山）。裸の天体名は**惑星科学概念（`concepts.id:C152551177`）で絞ると精度が上がる**（実測:「Mars」単独で R 言語マニュアルが1位 → 絞ると THEMIS/MSL/MAVEN） | 既定ソース（`title_and_abstract.search` を使用） |
| **Crossref** | 200。⚠️ `select` に `language` を入れると **HTTP 400**（select-not-available）。裸の天体名で `query.bibliographic` を使うと無関係な高被引用論文を返す（実測:"Mars" → 遺伝学論文） | 既定ソース（**2語以下は `query.title`**、3語以上は `query.bibliographic`） |
| **NASA NTRS** | 200。技術報告＋PDF リンク（ミッション設計・探査計画に強い） | 既定ソース |
| **JAXAリポジトリ（WEKO3）** | 200。**日本語クエリが効く**（「はやぶさ2 リュウグウ」で9件） | 日本語ソース |
| **J-STAGE** | 200（Atom）。⚠️ **`ERR_001` は「該当なし」でエラーではない**（"大気流出" は ERR_001、"火星" は status=0 で159件）。`WARN_002` は結果を伴う警告。`material`/`article_title` は使えない→**`keyword` を使う**。応答に charset が無く `r.text` は文字化けするので bytes を UTF-8 で解釈する | 日本語ソース（利用規約に従い「表示情報提供元: J-STAGE」＋リンクを表示） |
| **CiNii Research** | 200（OpenSearch JSON） | 日本語ソース |
| **Zenodo / DataCite** | 200。データセット・プレプリントの DOI | `sources="all"` で使用 |
| **NASA ADS** | トークン無しは **401**。無料トークン（要登録・5,000req/日）で使える | `ADS_API_KEY` を設定したときだけ使う |
| **Semantic Scholar** | キー無しは **429**（共有枠。待っても回復しなかった） | `S2_API_KEY` を設定したときだけ使う |
| **Web of Science（Starter）** | キー無しは **401**。無料枠は登録制・1req/s・50req/日・被引用数なし | `WOS_API_KEY` を設定したときだけ使う |
| **arXiv API** | 本環境からは UA を変えても **HTTP 406** | **実装しない**（取得できないものを載せない） |
| **JDreamIII / J-GLOBAL** | JDreamIII は JST の**有償**サービス（要契約・API も別契約）。J-GLOBAL WebAPI も MyJ-GLOBAL 登録＋キー交付が必要 | **実装しない**（日本語文献は **J-STAGE / CiNii / JAXAリポジトリ**で代替） |

### （任意）学術APIキー

`ADS_API_KEY`（NASA ADS・無料登録）/ `S2_API_KEY`（Semantic Scholar）/ `WOS_API_KEY`（Clarivate）は
**未設定でも全機能が動きます**（未設定のソースは**スキップして理由を `sources[].note` に返します**。
「全ソース失敗」とは区別されます）。設定する場合は MCPクライアントの env か、リポジトリ直下の
`.env`（`.env.example` 参照）に置いてください。キーはサーバー側でのみ保持し、クライアントへ晒しません。

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

- `apod`: 今日（または指定日）の [Astronomy Picture of the Day](https://apod.nasa.gov)。
  `date` 省略時は「今日」を明示指定して取得し、当日分が未公開（404）や API が 500 を返す場合は
  直近の公開分（前日）へ自動フォールバックします（`fallback_to_previous_day` で判定可能）。
- `neo_today`: 今日地球に接近する小惑星（直径・接近距離・速度）

キーは**サーバー側でのみ保持**し、クライアントへ晒しません（公開デプロイ時は環境変数・シークレット管理を推奨）。

### 🖼️ `search_space_images` — 画像検索・表示

NASA Image & Video Library から惑星・人工衛星の画像を検索し、**チャットにインライン表示**します。

- `content`: テキストサマリ + base64の画像（`ImageContent`）。**各項目の直後に `🖼️ [画像を開く: タイトル](URL)` のリンク**を出す（インライン描画できないクライアント向け）
- `structuredContent`: `{title, date, nasa_id, image_url, keywords}` のJSON
- **転送量**: インラインに埋め込む画像は既定で**先頭1枚**（`inline_max=1`〜`5` で増やせる）。
  高画質URLは `structuredContent.results[].image_url` に必ず入るため、必要なら再取得できます。
  インライン画像は一段小さいバリエーション（`~large`→`~medium`）を使い、画像資産は
  ディスクキャッシュされます（同じ画像を再ダウンロードしません）。

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

各結果に再生URL＋尺の目安をJSONで返し、`content` には **`🎧 [音声を開く: タイトル](URL)` のアイコン付きリンク**を出します。

### 🎬 `search_space_videos` — 動画検索

宇宙動画（打ち上げ・ミッション映像・解説）を検索し、再生用MP4 URLを返します。

- `structuredContent`: `{title, video_url, urls:{preview/medium/mobile/orig/subtitle}, poster_url}` — **解像度別URLと字幕(.srt)を分離**
- `content`: テキストサマリ + ポスター画像（インライン）。**`🎬 [動画を再生: タイトル](MP4 URL)` と `🖼️ [ポスター画像を開く: …](URL)` のリンク**を先に出します

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

[CelesTrak](https://celestrak.org)（NORADカタログ・認証不要）から、**任意の衛星の軌道要素(TLE)** を取得します。ISS・ハッブル・気象衛星・中国宇宙ステーション等、`name`・`norad_id`・`group` で指定可能。**CelesTrak が遮断中（403 / 接続不可）のときは公開ミラー（tle.ivanstanojevic.me、予備 db.satnogs.org）へ自動で切り替え**、`structuredContent.source` に実際の出典（`celestrak.org` か代替源のホスト名）を入れます。

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

### 🔭 `astronomy_weather` — 天体観測用天気（Open-Meteo ＋ 気象庁の雨雲・降水画像）

[Open-Meteo](https://open-meteo.com)（認証不要・無料・全世界対応）から、指定地点の今後数日間で**天体観測に適した夜間の時間帯**を自動抽出します。雲量・視程・風速・降水確率・昼夜判定を総合して判断し、AIからのアドバイスを返します。

- 場所は緯度経度（`latitude`/`longitude`）または地名（`place`）で指定
- 富士山・マウナケア・アタカマ・阿智村などの**著名観測地は正確な緯度経度を内蔵**
- `max_cloud` で観測可否の雲量基準を調整可能（既定 40%）
- `structuredContent` に各時間帯の雲量・視程・風速・降水確率をJSONで返す

```text
Q: マウナケアで今夜天体観測できる?
A: astronomy_weather(place="マウナケア") → 夜間の観測チャンス時間帯と雲量
Q: 東京で明日の星空は?
A: astronomy_weather(place="東京") → 雲が少ない夜間を抽出＋気象庁の雨雲・降水画像（解析雨量＋今夜の予報＋ナウキャスト）を添付
A: astronomy_weather(place="東京", include_rain=False) → 雨雲画像なしで予報のみ
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
- **フォールバック**: NASA が枠切れ（429）や障害のときは、**認証不要の NOAA SWPC**（`services.swpc.noaa.gov`）へ自動で切り替えます。Kp・NOAAスケール（R/S/G の現在値と1〜3日予測）・GOES X線クラス・**フレアイベント（直近7日・発生時刻と級の内訳）**・太陽風（速度／密度／Bt／Bz）・陽子フラックス・警報・黒点相対数を返し、**どちらの出典で答えたか**を `content` と `structuredContent.source`（`NOAA SWPC` ＋ `fallback: true` ＋ NASA 側の理由 `nasa_reason`）に明記します。SWPC 側でも取得できなかった項目は `failed` に残します
- **枠の使い方**: 1回の呼び出しで4エンドポイント（FLR/CME/GST/SEP）を叩くため `DEMO_KEY` を4消費します。**カテゴリ単位でキャッシュ**するので、`kind` を変えた呼び出しで同じカテゴリを取り直しません（実測: `all` の直後の `flare` は HTTP 0回）。429 時は「429 後の待機」か「枠切れ」かを区別した案内を返します（自分の使用数だけを出すと、IP を共有する DEMO_KEY では数字と矛盾して見えるため）

```text
Q: 最近の太陽フレアは?
A: space_weather(kind="flare")
Q: 地磁気嵐が起きているか確認
A: space_weather(kind="gst", start_date="2026-08-01")
Q: 宇宙天気の全体状況
A: space_weather() → フレア・CME・地磁気嵐・粒子現象をまとめて表示
```

出典: api.nasa.gov（NASA Space Weather）

### ☄️ `fireball_reports` / `neo_close_approach` / `impact_risk` — 天体異常系（JPL CNEOS）

[NASA/JPL CNEOS](https://cneos.jpl.nasa.gov/)（Center for Near Earth Object Studies）の公開 API （`ssd-api.jpl.nasa.gov`）から、**火球・小惑星等の接近・将来の衝突リスク**を取得します。**3ツールとも認証不要（APIキー不要）**で、`DEMO_KEY`（30 req/h/IP の共有枠）を消費しません。

- **`fireball_reports`** — 火球（fireball）は**大気圏に突入して光った現象**の観測記録です（米国政府センサ・地上観測の報告）。`days`（1〜1825）・`min_impact_energy_kt`（kt TNT 換算の下限）・`limit`（1〜50）・`require_location`（位置が報告された記録のみ）を指定できます。火球の**ゼロ件**は `data` キー自体が返らない（`count: 0` のみ）ため、その場合も「該当なし」として扱います。**隕石の回収・落下物の推定はしません**（落下物の情報が欲しい場合は別の情報源が必要です）。
- **`neo_close_approach`** — `days`（1〜365）・`max_distance_au`（既定 0.05 ≒ 19.5 月距離。1 LD ≒ 0.00256 au）・`limit`・`hazardous_only`（PHA のみ）。距離は **au / km / 月距離(LD)** の3通りで併記します。直径は**既知の値があればそれを、無ければ絶対等級 H とアルベド 0.14 の仮定から換算**した推定値で、推定の場合はその旨を表示と `structuredContent`（`diameter_is_estimate`）に残します。**接近時刻は TDB**（力学時）で、`t_sigma_f`（3σ の不確かさ）も返します。`neo_today` との違いは「今日だけ・api.nasa.gov のキー枠」ではなく、**任意の期間・距離**を扱えることです。
- **カレンダーへの反映（v0.34.0）** — `neo_close_approach` / `fireball_reports` を呼ぶと、返した行がそのまま**蓄積ストア**（`%LOCALAPPDATA%\space-finder-mcp\calendar_store.json`）の `neo` / `fireball` レコードになり、**後日 `space_calendar` / `calendar_events` に表示**されます（`structuredContent.calendar_stored` に件数、`content` にも「◯件を反映しました」と出ます。書けなかった場合はその旨を出し、例外にはしません）。接近は**日付セルに置ける**（時刻が確定した計算値。蓄積時に TDB である旨を併記）一方、火球は**過去の観測記録**として置き、落下地点ではない旨を併記します。**`impact_risk` は日付が無い**（数十年〜百年の幅の確率）ため蓄積しません。保持期限は既存の規則どおり **45日**（古い API 由来レコードは prune。ユーザー予定は対象外）。

- **`impact_risk`** — Sentry の**衝突確率の推算**です（進路が確定した衝突予報ではありません）。`min_probability`（既定 1e-3 = 0.1%）以上の天体を確率の高い順に返し、`designation` を指定すると1天体の詳細（累積確率・想定時期・仮想衝突(VI)・観測弧・パレルモスケール）を返します。`designation` は**仮符号または番号**（例 `"2024 YR4"` / `"2000 SG344"` / `"99942"`）で、**和名・愛称は JPL 側が受け付けません**（推測せず指定方法を案内します）。**確率が 0 になった天体は Sentry から削除され、HTTP 200 + `error` で返る**ため、それを検査して「リスクなし（監視対象から外れた）」を意味しうる旨を案内します。
- **数値の読み方** — 衝突エネルギーは**広島型原爆（約15kt）との比**、距離は**月距離**、直径は**推定であるかどうか**を必ず併記します（数値から生成し、書き手によって変わらないようにしています）。

```text
Q: 最近の火球は?                  A: fireball_reports(days=30)
Q: 大きな火球だけ                   A: fireball_reports(days=365, min_impact_energy_kt=1)
Q: 今週接近する小惑星               A: neo_close_approach(days=7)
Q: 月距離以内に来る天体             A: neo_close_approach(days=30, max_distance_au=0.0026)
Q: 危険な小惑星は?                  A: impact_risk()
Q: 2024 YR4 の衝突リスク            A: impact_risk(designation="2024 YR4")
```

出典: NASA/JPL CNEOS（ssd-api.jpl.nasa.gov）/ Fireball Data API・SBDB Close-Approach Data API・Sentry

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

- `query`: 衛星名（meteor, resurs, goes, kanopus, himawari 等）。**一致度順に並べ替えて返します** — acronym の完全/前方一致を最優先し、名称は語境界で照合、名称の途中に含まれるだけの一致（例: "meteor" が "Meteorological" に含まれる DMSP/COSMIC）は**低順位**に置き、件数を分けて表示します（以前は 92件の誤ヒットに Meteor-M が埋もれていました）
- **カタログ全件（約1,044件・35ページ）を走査**します。1ページ30件固定の API なので初回は40秒ほどかかりますが、取得結果を**ディスクにも保存**するため以後はプロセスを再起動しても即時です（24時間で更新）。取得できなかったページがある場合は `structuredContent.failed_pages` と本文に明示します（欠けを黙って捨てません）
- `agency`: 機関名（Roscosmos, NOAA, EUMETSAT, JAXA 等）
- 運用中🟢 / 計画中🔵 / 延長🟡 / 退役🔴 を色付きで表示
- Roscosmos の気象衛星（Meteor-M・Resurs-P・Kanopus等）も詳細に収録

> **実装メモ**: OSCAR API の `search` / `space_agency` / `status` パラメータは現在**機能しません**（常に全件を返す）。そのため全件をページング取得し、クライアントサイドでフィルタしています。

出典: space.oscar.wmo.int（WMO OSCAR/Space）

---



### 📡 `alma_search` — ALMA 電波観測データ検索

ALMA（アタカマ大型ミリ波サブミリ波干渉計）の科学アーカイブを、NAOJ が運用する東アジア鏡の IVOA TAP 経由で検索。`object_name`（例 `M100`, `HL Tau`）か `ra`/`dec`+`radius`、受信 `band`（1〜10）、`product_type`（`cube`/`image`/`visibility`/`all`）、`public_only` で絞り込む。mm/サブmm の電波観測のため低温ガス・塵や惑星系形成領域が対象。

- **天体名は表記ゆれを自動吸収**（`HL Tau`→`HL_Tau`/`HLTau` 等）。0件のときはアーカイブ内の候補名を提示して停止（推測しない）。
- 返る情報: 観測対象・座標・周波数帯・**データ種別（cube/image）・空間分解能（″）・露出時間・可視降水量 pwv・QA2 合否・公開/要権限**・プロポーザルID・PI名・関連論文。
- `with_products=True` で **datalink を引いて実データ製品**（校正済み tar・生データ ASDM・README・パイプライン製品）をアイコン付きリンクで列挙（先頭3件）。

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
- `"accurate"`: **matplotlib** による正確な星図。方位・仰角グリッド、軌道予測線を精確表示（科学・詳細用途）。**マーカーの配色・光背・土星の環・木星の縞・火星の極冠・名札・凡例は `simple` と同一の指定色**で、色は天体・記号の共通パレット `img_common.BODY_COLORS` / `SYMBOL_COLORS` の 1 か所から生成します（凡例も実際に描いたものだけを色コード付きで表示）。

画像は content に base64 でインライン表示（`simple`=**JPEG** / `accurate`=PNG）、座標一覧は structuredContent に JSON。
`simple` は実写合成のため JPEG が適切で、PNG 比で約1/5の転送量になります（1400×1400 で 992KB → 188KB）。

```json
{"time_utc": "...", "engine": "simple (Pillow)",
 "planets": {"月": {"az":..,"alt":..}, ...},
 "satellites": {"ISS (国際宇宙ステーション)": {"az":..,"alt":..,"trail":[...]}, ...}}
```

### ☀️ `solar_system_now` — 太陽系俯瞰図（太陽中心の惑星・小惑星・探査機・彗星位置）

「太陽系を上から見た図」「今の惑星の位置」「イトカワの今の位置を図で」などに応答。太陽を中心とした黄道面俯瞰図を画像化し、惑星(8惑星＋冥王星)は **JPL DE421 暦表 + Skyfield** で日心黄道座標を計算。任意の小惑星は **JPL SBDB API** の軌道要素を取得し**ケプラー2体問題**で日心位置へ伝播する（`asteroid`/`asteroid2` で複数指定可）。

- 距離が 0.4〜40 AU と2桁超のため、**Pillow版(既定)は対数縮尺**で全天体を一枚に表示。惑星を実物色アイコン＋距離ラベル、小惑星を**緑の十字マーカー**＋緑軌道リングで強調。惑星軌道円・小惑星帯(2.0-3.4AU目安)も併記。学生・観賞向けに視認性優先。
- `engine="accurate"` で **matplotlib 版**（線形距離の正確な相対距離俯瞰図）も選択可。科学的な相対距離比較用途向け。
- 惑星・太陽・小惑星・彗星の色は `img_common.BODY_COLORS` / `SYMBOL_COLORS` の共通パレット（`sky_map_with_satellites` と同一の値）を両エンジンで共有します。
- **一部の惑星の位置を計算できなかった場合**は、その旨（失敗数）を `content` と `figure.notes` に出し、`structuredContent.planet_errors` に天体名と理由を記録します（黙って欠落させません）。
- 対応小惑星エイリアス: イトカワ(itokawa/25143)・ベンヌ(101955)・アポフィス(99942)・リュウグウ(162173)・ツタティス(4179)・エロス(433)。SBDBの`sstr`（番号・名前）なら任意の小惑星を指定可能。認証不要。
- **遠方探査機**: `probe`/`probe2` 引数に ボイジャー1号/2号・パイオニア10号/11号・ニュー・ホライズンズ・はやぶさ2（日英名対応）を指定。JPL Horizons 状態ベクトル（ECLIPTIC, 太陽中心）から日心位置を取得。ボイジャー等は**黄緯が大きい（~35°）ため、黄道面への正射影距離**で描画し、ラベルに真距離と黄緯を併記。表示スケールは探査機の距離まで自動拡張し、色付き菱形マーカー＋太陽からの補助線で強調。遠方のため線形 matplotlib 版は使わず Pillow（対数縮尺）で表示。認証不要。
- **「1光日」を図に重ねる（`probe` 指定時）**: 光が24時間で進む距離＝**173.1446 AU＝25,902,068,371 km** を**破線の円**で描きます（探査機を描く図では表示範囲を1光日まで自動で広げます）。1光日に達していない探査機では、**その探査機の黄道面投影での1光日リング**と**到達時の方向（◇＝黄経/黄緯）**・**到達予測日**も出します。俯瞰図は黄道面への正射影なので**真距離の円と投影の円は別物**で、その旨と両者の半径を数値付きで `figure.notes` に出します（実測: 黄緯 35.2° のボイジャー1号は真距離 173.14 AU に対し投影 141.6 AU＝別の円。黄緯 3.0° のパイオニア10号は 172.9 AU で図上ほぼ重なるため「見分けられない」旨を注記）。`structuredContent.probes[]` に **`light_days`**（何光日）・**`light_hours`**（光の所要時間）・**`to_light_day_au`**（1光日までの残り）・**`light_day_eta_date` / `light_day_eta_method`**（到達予測＝現在の日心視線速度による線形外挿）が入ります（実測: ボイジャー1号 0.9925 光日・残り 1.30 AU・**2027-02-03**＝JPL Horizons の n 体解で厳密に解いた日付と一致）。**距離の基準は日心距離（真距離）に統一**しています（地心距離での1光日は地球の公転で最大 ±1 AU 変わり、日付が違うため）。1光日が表示範囲の外（例: `range_au=10`）なら描かず、理由を注記と `structuredContent.light_day.ring_why` に出します。リングの破線・ラベル文字・探査機マーカーの画素を測り直して `figure.verify.light_day` に残します（ラベルがマーカーを覆っていれば `ok=false`）。
- **彗星の軌道面ビュー（`view="comet_orbit"`）**: 単体なら `comet` と併せて指定すると、その彗星の軌道を**彗星自身の軌道面を真横から見た図**で返します。太陽は**円錐曲線の焦点**（楕円の中心ではない）に置き、近日点・遠日点・現在位置・日心距離目盛(AU)を描画します。**e≥1 の C/彗星**（例: 紫金山・アトラス＝SBDB の a が負）は**閉じない双曲線の枝**として近日点から有限距離までを描きます（楕円として描くと `a(1+e)` が負になり破綻するため）。ハレー彗星（e=0.968・逆行 i=162°）のような極端な楕円でも、**焦点と中心のズレ a·e** が図と `figure.notes` の両方に出ます。
- **表示範囲を絞って内側を拡大する（`range_au`）**: **数値（AU）でも天体名でも指定できます**（`range_au="火星"` → 1.65 AU／`"木星まで"` → 5.62 AU／`"saturn"` → 10.35 AU／`"小惑星帯"`／`"内惑星"`／`"外惑星"`。決め方は `figure.notes` と `structuredContent.range_resolved` に「木星の軌道（長半径 5.20 AU × 1.08 = 5.62 AU）」の形で出ます）。`range_au="fit"` は**その呼び出しで指定した天体（小惑星・彗星・探査機・経路）が全部入る範囲**を自動で選びます（最大値×1.10、下限 1.2 AU。例: `asteroid="イトカワ", range_au="fit"` → 1.52 AU）。解決できない名前は**推測せず候補一覧つきでエラーを返します**（`range_targets`）。`solar_system_now(range_au=10)` で**太陽から 10 AU まで＝土星より内側だけ**を表示します。線形版（`engine="accurate"`）は縮尺が固定（実測 約12 px/AU）で、既定の ±45 AU では内惑星が太陽マーカー（半径 1.7 AU 相当）と重なって潰れていました。`range_au` を指定すると `lim` を固定し、**px/AU が約4.5倍**（10 AU なら 約51 px/AU）になり、太陽マーカーの誇張半径も 0.4 AU まで下がって**水星〜土星が読める大きさ**になります（実測値は `figure.scale.px_per_AU`）。**範囲外の天体は描かず**、「表示範囲10 AU の外にあるため描いていない: 天王星 19.44 AU、海王星 29.88 AU…」のように`figure.notes` と `structuredContent.out_of_range` へ数値付きで列挙します（黙って消しません）。経路が範囲外へ出る彗星は対数版へ自動で落とし、その旨と範囲外の目印も注記します。
- **彗星の通過経路を俯瞰図に重ねる（`route=True`）**: `solar_system_now(comet="エンケ彗星", route=True)` で、太陽系俯瞰図にその彗星の**軌道（通過経路）を破線で重ねます**（黄道面への正射影）。**近日点・遠日点**には◇と日付を添え、本文と `figure.notes` にも SBDB 軌道要素から数値で出します（**近日点の日付は SBDB の2体近似に加えて JPL Horizons の n 体解も併記**し、両者の差を日数で注記します。SBDB の要素は古いエポックの接触軌道なので、摂動の大きい彗星では2体近似が数か月ずれます＝実測: ハレー彗星 163.8日・67P 82.6日・エンケ彗星 0.8日。n 体解を取得できないときは**取得できなかった旨と理由**を注記し、2体近似だけを黙って出すことはしません）。**対数縮尺の `simple`（既定）では線の長さと曲率は実際の楕円と一致しません**（注記に自動生成。形そのものは `view="comet_orbit"`）。`engine="accurate"`（線形±45 AU）は経路が枠に収まる彗星なら自動で維持され、**形が本当の軌道**になります（収まらないときは対数版へ自動で落とします）。**近日点が誇張した太陽の描画円盤の内側に来る彗星では目印を描かず**、「この縮尺では図から位置を確認できない（数値は 0.339 AU）」を注記に数値から生成します。認証不要。
- **彗星の見え方チャート（`view="apparition"`）**: `comet` と併せて指定すると、その彗星の**観測の可否を時系列で示す3段パネル**（横軸＝UTC の日付）を返します。上から **距離**（日心距離 r＝黄／地心距離 Δ＝シアン、AU）・**予想光度 m1**（橙。JPL SBDB の全光度の式 `m1 = M1 + 5·log10(Δ) + K1·log10(r)`。**値が小さい＝明るい方が上**）・**太陽離角**（緑。0°＝太陽と同じ方向＝地球からは見えない）。**今日・近日点・地球最接近**を縦線で示し、期間内の極値は数値ラベル付きの点で示します（曲線や線に重なる位置にはラベルを描かず、描けなかったラベルは `figure.notes` に列挙）。位置は **JPL Horizons の N 体解**（期間ぶんを1リクエストでまとめて取得）、地球位置は JPL DE421+Skyfield。Horizons が使えないときだけ SBDB 要素の2体近似へ退避し、**精度差を数値で注記**します（実測: 169P/NEAT で N 体解と最大 0.0249 au・地球最接近の時刻が約1.2日ずれる）。**期間の端で最小のときは「近日点」「地球最接近」とは呼びません**（ハレー彗星は 2061 年まで太陽へ近づき続けるので、どの期間でも最小は端に来る）＝「期間の端で最小＝真の極値はこの期間の外」と数値付きで注記し、マーカーも「r 最小」「Δ 最小」に切り替えます。`days`（既定180日）で長さを変えられ、**今日の30日前から**描くので直前に過ぎた近日点・最接近も見えます（1天体ずつ。複数指定は「1天体ずつ指定してください」と返して停止）。
- **複数彗星を1枚に（1彗星=1パネル）**: `comet="ハレー彗星,C/2004 R2"` のように**カンマ区切り**（`、`/`;` も可）または `comet2` を併用すると、最大4天体を**縦に並べた1枚の画像**で返します。彗星ごとに**軌道面も縮尺も違う**ため、1つの座標系に重ねると嘘になる（＝軌道面が違えば「真横から見た形」は同時に成立せず、a が2桁違うと小さい軌道が点に潰れる）ので、**パネルは独立した軌道面・独立した縮尺**です。その旨（同じ長さの線でも距離が一致しないこと）と `a` の開き（例: 229倍）は `figure.notes` に数値から生成して入ります。1天体の取得に失敗しても他のパネルは描き、`structuredContent.errors` と注記に理由を残します（上限4件を超える要求も「要求N件のうちM件を描画」と明示）。`figure.kind` は `orbit_plane_set` で、パネルごとの円錐曲線・自己検証は `figure.panels[]`（`conic` / `verify` / `px_per_AU` / `notes`）、`figure.verify` は全パネルの集約です。
- **彗星（周期・C/）**: `comet`/`comet2` 引数に ハレー彗星(halley/1P)・エンケ彗星(2P)・チュリュモフ・ゲラシメンコ(67P)・テンペル第1(9P)・ヴィルト第2(81P) 等の**周期彗星**、または 紫金山・アトラス(C/2023 A3)・ラブジョイ(C/2014 Q2) 等の**C/彗星**を指定。周期彗星は JPL SBDB 軌道要素のケプラー伝播、C/彗星（非周期・放物線/双曲線軌道）は JPL Horizons 状態ベクトルで 日心位置を計算。彗星は**シアン色の輝く核＋太陽と反対方向に伸びる尾**で描画し、黄緯が大きいものは 黄道面への正射影距離で配置・ラベルに真距離と黄緯を併記。遠方彗星は表示スケールを自動拡張。認証不要。

```json
// route=True のときは彗星の通過経路（点列は入れず、目印と要素だけ）が付く
// "comet_routes": {"エンケ彗星": {"name": "2P/Encke", "e": 0.8470, "q_au": 0.3395,
//   "period_days": 1207.0, "closed": true, "marks": [{"id": "perihelion", "label": "近日点",
//   "r_au": 0.3395, "proj_au": 0.3393, "date": "2027-02-11", "px": [736, 755], "drawn": false}]}}
{"time_utc": "2026-09-09 05:32 UTC", "engine": "simple (Pillow, 対数縮尺)",
 "asteroids": {"イトカワ": {"au": 1.60, "eclLon": 82.9}},
 "probes": {"ボイジャー1号": {"au": 171.7, "proj_au": 140.4, "eclLat": 35.2}},
 "comets": {"ハレー彗星": {"au": 35.1, "proj_au": 33.7, "eclLat": -16.6}}}  // 惑星は全9天体
```

```json
// view="apparition" の応答例（169P/NEAT・120日＋直前60日）。Δ の最小値は JPL の公表値と一致
{"comet": "169P/NEAT", "positions_source": "horizons",
 "window": {"from": "2026-07-24", "to": "2027-01-19", "days": 120, "days_before": 60, "samples": 437},
 "features": {"closest_approach": {"utc": "2026-08-11 19:19", "delta_au": 0.167172, "inside_window": true},
              "perihelion": {"utc": "2026-09-21 09:16", "r_au": 0.604348, "inside_window": true},
              "brightest": {"utc": "2026-08-14 04:21", "mag": 13.6},
              "next_close_approach": {"utc": "2026-09-17", "delta_au": 0.5634}},
 "verify": {"ok": true, "label_overlap_px": 0, "worst_px_error": 0.0,
            "marker_pixels": {"今日": 367, "近日点 09/21": 391, "地球最接近 08/11": 366}}}
```

```json
{"comet": "1P/Halley", "e": 0.967936, "q_au": 0.5749, "typ": "sbdb",
 "figure": {"schema": "figure/1", "kind": "orbit_plane",
            "primary": {"name": "太陽", "at": "focus", "center_offset_AU": 17.3537},
            "conic": {"kind": "ellipse", "e": 0.967936, "q": 0.5748638313743413,
                      "apo": 35.282406265764116, "c": 17.35377121719489},
            "verify": {"periapsis_error_pct": 7.13, "apoapsis_error_pct": 0.04,
                       "periapsis_check": "equality", "label_overlap_px": 0, "ok": true}}}
 // view="comet_orbit" の応答例。超長距離の桁（C/2004 R2, a=4.1e3 AU）では
 // {"periapsis_resolvable": false, "periapsis_check": "upper_bound", "periapsis_upper_bound": 4.0, "ok": true}
```


### 🌞 `solar_eclipse_series` — 日食の時系列パネル（太陽が月に欠ける過程）

「2035年9月2日の皆既日食を画像で」「東京で見える次の日食」「2019年の部分日食の進行」などに応答。指定した観測地・日付で、太陽と月の見かけの重なりを **JPL DE421 + Skyfield** で実測計算し、**食の始まり〜最大〜終わりを7枚のパネル**に並べて合成。サンプル画像（部分日食の時系列）と同じレイアウト。

- 太陽・月の**視角半径と角距離**を観測地の視位置(topocentric apparent)で計算し、**月の位置角(PA)**を反映して正しく欠ける位置に月を描画。パネル上部に▲（天の北）、各パネル下に現地時刻・食分。
- 最大食分から種別を自動判定：0.995以上は**皆既**(月視半径≥太陽)／**金環**(月<太陽)、それ以外は**部分**。
- `figure.verify` は**描いた画素から測り直した食分**を各パネルで報告値と突き合わせます（PA軸の明部長 L から 食分 = 1 − L/2R）。実測誤差は 0.004 以内、パネル欠けやはみ出しはここで落ちます。
- `date` 省略時は**「これから起こる次の日食」を約4年（1400日）先まで自動検索**（Skyfield almanac で新月を列挙し、食のありうる新月だけ高速絞り込み → 精密計算）。例: 東京の次に**見える**食は **2030-06-01 部分日食（食分0.80・太陽高度+19°）** で、**既定の引数だけでこれが返ります**（800日＝約2年では可視の食が無く、常に「見つかりません」しか返せませんでした）。走査は「30分刻みで食の時間帯を特定 → その窓内だけ2分刻みで精密化」の2段構えで、初回は約28秒（同じ観測地・同じ日なら1日キャッシュで以後は即時）です。
- **見えない食は返しません** — 描くのはその観測地で太陽が**地平線より上**にある時間帯だけで、最大食分も可視区間の中で求めます。日食が現地の日付をまたぐ場合（例: 23:25 開始 → 翌 00:37 終了）も前後6時間まで含めて計算します。全日食が地平線下なら「太陽が地平線下（最大高度 −67°）のため見えません」と明示します（例: 東京2028-01-26）。
- `max_magnitude=True` で最大食のみの単一画像を返す。`place`/`lat`/`lon` で**世界中の任意の観測地**を指定可能（東京・ベルリン・ケープタウン・グリニッジ天文台・昭和基地 等。Open-Meteo + Nominatim で自動解決）。日食の現地時刻はその場所のタイムゾーン（DST込み）で表示。認証不要。

```json
{"kind": "部分日食", "max_magnitude": 0.63, "date": "2028年1月26日",
 "place": "東京", "lat": 35.68, "lon": 139.69}  // 7パネル時系列画像を content に返す
```

### 🌙 `moon_phase_map` — 月齢マップ（月の満ち欠けの格子／朔望月パネル）

「今月の月齢マップを見せて」「2026年9月の月相カレンダー」「次の朔望月の満ち欠けを画像で」などに応答。**日食の時系列パネルと同じ幾何計算**（JPL DE421 + Skyfield・認証不要）を使い、観測地から見た太陽と月の実位置から**輝面の向き（位置角 PA）と照度**を求めて月円盤を正しい向きに欠けさせます（月齢から向きを決め打ちしません）。

- `layout="calendar"`（既定）— 指定した月を**日月火水木金土の格子**に並べ、各日に月齢・照度・月相（新月／三日月／上弦／十三夜／満月／下弦／有明月 etc）を描きます。朔・望・上弦・下弦が起きる日はイベント名と現地時刻を強調表示。
- `layout="lunation"` — **1朔望月（朔→朔）を等間隔の時系列パネル**（`days` で3〜12枚・既定8枚）に並べ、各パネルに日時・月齢・照度・月相・輝面の位置角を出します。
- 月齢=**直前の朔（新月）からの経過日数**、照度=**円盤の輝面の割合**（満月=100%）。カレンダーは各日**現地正午**時点、パネルは各パネルの現地時刻時点の値です。
- `figure.verify` は**描いた画素から明暗境界線の位置を測り直し**、申告した照度と突き合わせます（照度 k の円盤は位置角軸上で縁から 2kR の幅が明るい）。走査軸が申告した位置角なので、**満ち欠けの向きが違えば一致しません**（実測誤差 0.02 以内）。
- **図と数値の食い違いを注記で明示** — 「円盤は模式図（実写ではなく月の海は乱数）」「天の北を上・東を左に置いた見え方で地平線からの見え方ではない」「朔・望の日は正午時点では前後になる（例: 9/11 は正午時点の月齢 29.4・新月の時刻は 12:26）」等を**数値から生成**します（`figure.notes` は要約せず引用してください）。
- `date`（`"2026-09"` / `"2026年9月16日"`）と `place`/`lat`/`lon` で観測地・期間を指定。日付・時刻・曜日は現地時間（緯度経度から取得した UTC オフセット）で表示します。

```json
{"layout": "calendar", "period": {"month": "2026-09", "days": 30},
 "days": [{"date": "2026-09-11", "moon_age": 29.39, "illum_pct": 0, "phase_ja": "新月（正午時点の月齢 29.4）",
           "bright_limb_pa_deg": 31.1, "events": ["新月"]}, "..."],
 "events": [{"phase": "新月", "datetime_local": "2026-09-11 12:26"}, "..."],
 "figure": {"kind": "moon_phase_calendar", "verify": {"ok": true, "worst_error": 0.0121}}}
```


### 🛰 `planetary_orbiter_track` — 汎用・天体周回機マップ

月・火星・水星・タイタン・ベスタ・ケレス等、NASA Trek が等角図法グローバル画像を持つ**任意の天体**を周回する探査機の**現在位置と軌道トレイル**を画像化します。JPL Horizons の状態ベクトルを **IAU 自転モデル**で天体固定座標（緯度経度・高度）に変換して正確に計算。認証不要。

- **対応天体（BODIES テーブル）**: moon（LRO・ゲートウェイ）、mars（MRO・Mars Odyssey）等。新規天体は「中心天体ID・IAU回転定数・TrekタイルURL・半径」を1行追加するだけで対応可能。
- **`span_deg=360` で天体全面表示**（2:1グローバルビュー）、既定 `120` で現在位置中心の局所表示。
- **高精度トレイル**: `step` を秒単位（1/60分）まで細分化可能。JPL Horizons の TLIST を POST + 分割バッチで送るため、1400点超のトレイルも高速取得。
- **トレイルの一部が失敗した場合**: 計算できなかった点数を `content` と `figure.notes` に出し、`structuredContent.trail_errors` に `offset_min` と理由を記録します（図に無い点を「無い」と誤読させないため）。
- 運用終了機（かぐや・MAVEN・あかつき等）は推測せず丁寧に案内（かぐやは JAXA 公表の落点＝南緯65.5°／東経80.4° Gill クレータ付近を出典つきで返す）。和名・英名・別名（かぐや/kaguya/selene）は同じ案内に着地。
- **着陸地点マップ（`figure.kind="landing_site_map"`）**: `sites="apollo"` で**アポロ6地点**を天体全面図に番号付きマーカー＋凡例で、`sites="apollo11"` のように1地点を指定するとその地点中心の局所図で返します。座標は **NASA NSSDC が公表した月着陸船(LM)の値**（LRO 画像から決定・惑星中心 Mean Earth/Polar Axis、`Wagner et al., Icarus 283 (2017)`）で、**旗そのものの座標は公開表に無い**ため「マーカーは着陸地点を指す」旨と各地点の**旗の状態**（下記）を注記に自動生成して入れます。地点マーカーは共通ルーチン `surface_map.py` の `draw_markers` / `verify_markers`（投影の逆変換＋全地点の画素検証）を使います。**文字（タイトル・凡例・番号）は地図の外＝下の帯に置き、地図の上に重ねません**（重ねるとマーカーが文字で隠れる）。複数地点では**地点間の最小間隔からマーカー半径を決め**、離隔が足りなければキャンバスを自動拡大します（`markers_do_not_overlap` を検証）。番号は他のマーカーに載らない位置へ機械配置し、置けなければ省略して注記に残します。
- **対応天体（`sites=` の地点マーカー図）**: `MAP_BODIES` の16天体（月・火星・水星・金星・タイタン・ベスタ・ケレス・イオ・エウロパ・ガニメデ・カリスト・木星・土星・冥王星・ベンヌ・リュウグウ）。**ベースマップは3種類を天体ごとに自動選択**します。
  1. **`trek`** … NASA Trek の全球等角図タイル（層IDを検証済みの6天体: 月・火星・水星・タイタン※・ベスタ・ケレス。※タイタンは画像方式に切替済み）
  2. **`image`** … **1枚の全球等角図**（金星＝USGS/NASA Magellan 合成図、タイタン＝NASA Cassini 全球図、イオ/エウロパ/ガニメデ/カリスト＝USGS/NASA のモザイク）。**2:1 でない画像はアスペクト比を補正して描画**（補正率と元の AR を注記に明示）
  3. **`graticule`** … 全球画像が無い天体（木星・土星・冥王星・ベンヌ・リュウグウ）は緯度経度グリッド。注記に「地形画像は無い」と明示
  実測: NASA Trek に Jupiter は無い（404）。画像が取得できないときは自動でグリッドに落ち、その旨を注記に残します。
- **`sites="map"`（地点なし・地図のみ）**: 地点マーカーを描かず**全球図そのもの**を返します（イオ・エウロパ・ガニメデ・カリストのように着陸機が無い天体の全球図表示に使用。`figure.kind="body_map"`）。
- **地点データの内訳（2026-09 時点）**: 月10（アポロ6＋嫦娥3号＋ルノホート1/2＋サーベイヤー3号）／**タイタン1**（ホイヘンス 10.573°S・192.335°W＝東向き正 167.665°E。旧 ESA 表記の 0〜360°W 慣例を変換）／**金星9**（ベネラ7・8・9・10・11・12・13・14号＋ベガ2号。出典: Venera 飛行データ表＝NSSDC 準拠。8/9/10号は「半径150km以内」の精度）／**火星12**（マルス2・3・6号、バイキング1号、パスファインダー、ビーグル2号、スピリット、オポチュニティ、フェニックス、キュリオシティ、インサイト、パーサヴィアランス。出典: The Planetary Society の着陸地点一覧が付す一次出典）／木星23（SL9）。**経度は東向き正（−180〜180）に統一**し、0〜360°表記や西経の値は自動で折り返します（実測: 金星351°Eをそのまま渡すと画面外に描かれた）。
- **木星の SL9 衝突地点**: `planetary_orbiter_track(body="jupiter", sites="all")` で**シューメーカー・レヴィ第9彗星の衝突地点23破片**（1994-07-16〜22）を木星の緯度経度グリッドに描きます。座標は **PDS Atmospheres が公開する Chodas & Yeomans (1996) の表**（緯度＝木星中心緯度、経度＝System III の**西向き**→東向き正に変換、衝突面は 100 mbar 面）。注記に「**木星は固体表面が無い**ガス惑星で、衝突痕は数日〜数週間で大気に流されて消えた」ことを明示します。近すぎて重なる地点（例: 破片 D と S は同じ 33°W）は**重ねて描かず**、図示しなかった地点として注記と `structuredContent` に残します。
- **米国旗の状態（NASA ALSJ「Six Flags on the Moon」＋ LROC 時系列画像）**: アポロ11号は**倒れている**（帰還時の上昇エンジン噴射で倒れた／LROC に旗の影なし）。12号・16号・17号は**立っている**（LROC 時系列で強い影）。14号・15号は LROC では旗の影が不明瞭（15号は LRV の TV カメラで離陸後も立っていたことを確認）。
- **落点マップ（`figure.kind="impact_site_map"`）**: 落点が公表されている過去機は、その**天体面の地図に地点マーカーを描いた図**を返します（かぐや＝月面図）。注記には「公表座標に置いたマーカーであり、衝突でできた新クレータを画像から同定したものではない」旨を数値から生成して入れます。地点マーカーの描画・画素検証は共通ルーチン `surface_map.py`（`draw_marker` / `verify_marker`）を使い、**投影の逆変換で座標を再計算＋マーカー色の画素数**まで検査します。

### 🗺 `planetary_rover_location_map` — 汎用・天体面ローバー位置マップ

任意の天体面を移動する探査ローバーの**現在地・走行経路・着陸地点**を、その天体の局所地図に重ねて画像化します。ベースマップ・タイル合成・切り出し・投影・マーカー・画素検証は共通ルーチン `surface_map.py` を再利用（周回機・ローバー・落点マップで同じ投影・同じ検証）。

- 走行経路(橙線)・現在地(赤●)・着陸地点(青●)を合成し、ローバーを画像中心に配置。
- 対応ローバー（ROVERS テーブル）: perseverance（パーサヴィアランス）・curiosity（キュリオシティ）。
- **正直な制約**: ローバー位置データは NASA MMGIS（火星専用）のみ。月面ローバー等の現役位置データは公開されておらず、データ源ができればテーブル追加で対応可能。

## ⚡ キャッシュ（API呼び出しと処理の削減）

外部APIへの呼び出し回数と再計算を抑えるため、データの性質ごとに3層でキャッシュします（`cache.py`）。

| 層 | 対象 | 方式 | TTL |
|----|------|------|-----|
| ① 不変アセット | Trek 地図タイル / NASA 画像資産 / 星空マップ背景 | **ディスク**（`%LOCALAPPDATA%\Temp\space_finder_mcp\cache`） | 30日（実質無期限） |
| ② 揮発データ | RSS・打ち上げ(LL2)・STAC検索・天気・ESO | プロセス内メモリ | **10分** |
| | 宇宙天気(DONKI) — **カテゴリ単位**（種別を変えても取り直さない。`DEMO_KEY` は1回4エンドポイントを消費するため） | プロセス内メモリ | **10分** |
| | 天体観測用天気・メディア検索・ALMA/CADC | プロセス内メモリ | **30分** |
| | APOD・NEO・EO Dashboard（日次データ。`DEMO_KEY` 消費も抑制） | プロセス内メモリ | **1時間** |
| 高コスト計算 | 探査機・彗星の Horizons 状態ベクトル（分単位でキー化）／SBDB 軌道要素 | プロセス内メモリ | 24時間（キーが1分ごとに変わる＝実質1分） |
| | データセット一覧・ジオコーディング・NASA POWER・Wikidata逆引き | プロセス内メモリ | **24時間** |
| ③ 高コスト計算 | 日食（次の日食探索＝約28秒／指定日の判定＝約1秒。探索窓は約4年＝1400日） | **緯度経度を丸めたキー**でメモリ | 24時間 |

**実測効果**（1回目 → 2回目）
- `planetary_orbiter_track`(LRO): **4.46s / 73リクエスト → 1.07s / 1リクエスト**（タイル72枚を再取得しない）
- `solar_eclipse_series`(東京): **28.4s → 0.7s**（探索窓を1400日に拡大しても初回のみ）／ 指定日 0.4〜1.2s → 0.7s
- `satellite_status`: 50.28s → 0.00s ／ `astronomy_weather` 2.56s → 0.00s
- `satellite_status`（全1,044件の走査）: **初回 38.5s → 以後 0.07s**（カタログをディスクにも保存し、プロセス再起動後も即時）
- `sky_map_with_satellites`(simple) の画像: **992KB → 188KB**（JPEG化）
- `search_space_images`: インライン画像を3枚 → 1枚（`inline_max` で増やせる）

**設計上の約束**
- **同じ引数の並行呼び出しは1回の実行にまとめます**（single-flight）。8並列で投げても実行は1回で、結果は全員に同じものが返ります（共有レート枠を N 倍消費しない）
- エラー応答はキャッシュしません（一時的な障害やレート制限(429)が固定化しない）
- 取得失敗時は期限切れでもディスクの古い内容を返します（stale-if-error）
- 現在位置系（`iss_now` / `tiangong_now` / 各位置計算）は**リアルタイム性を優先しキャッシュ対象外**です（TLEは従来どおり6時間キャッシュ）

---

## 🔐 応答方式（tokyo-transit 方式）

全ツールは **`CallToolResult`** を使い、次の2層で応答します。

- **`content`** — 人間向け表示（テキストサマリ、画像は `ImageContent`）
- **`structuredContent`** — LLM向けの**純粋JSON**（メディアURL・メタデータを構造化）

これにより、ホストLLMが表示テキストを要約しても**元データ（URL等）を失わず**、正確な情報を保持できます。

---

## 🧪 開発・検証

変更後は次の検証ゲートを通してください（`scripts/check-tools.py` は依存追加なしで同梱）。

```bash
# 1. 構文チェック
uv run python -m compileall -q src/space_finder_mcp

# 2. 全53ツールを実呼び出し（例外漏れ・structuredContent欠落・タイムアウトを検出。数分）
uv run python scripts/check-tools.py

# 3. ネットワーク全断を注入して「例外が外へ漏れないか」を検査
uv run python scripts/check-tools.py --offline

# 4. 未参照定義・未使用import の走査（0件を維持）
uv run python scripts/check-tools.py --dead-code

# 5. 数値引数へ不正値（"abc" など）を注入して例外漏れを検査
uv run python scripts/check-tools.py --fuzz

# 5a. 並列ツール呼び出し（LLM が同時に複数ツールを投げる前提）を検査
uv run python scripts/check-tools.py --concurrency

# 5a2. 実クライアント経路（stdio）で代表ツールが応答するか検査
uv run python scripts/check-tools.py --stdio

# 5b. 画像/音声/動画を返すツールが「リンク先行」を守っているか検査（0件を維持）
uv run python scripts/check-tools.py --media-links

# 5c. 描画系ツールの figure/1（図の注記・自己検証）を検査
uv run python scripts/check-tools.py --figures

# 6. 変更したツールだけ先に確認 / CI向けJSON出力
uv run python scripts/check-tools.py --only sat_tle,apod
uv run python scripts/check-tools.py --json
```

終了コードは 0=正常 / 1=異常です（例外漏れ・`structuredContent` 欠落・タイムアウト・デッドコード・figure/1 の検証失敗 `--figures`・メディアのリンク先行違反 `--media-links`）。MCPサーバーは**ホットリロードがない**ため、`src/` を変更したらクライアントを再起動してください。

### エージェントから使う場合

Claude Code は [`CLAUDE.md`](CLAUDE.md) と [`.claude/rules/`](.claude/rules/)、Codex は [`AGENTS.md`](AGENTS.md)、その他のエージェントは [`SKILL.md`](SKILL.md) を参照します。

```bash
# Hermes で接続確認
hermes mcp test space-finder-mcp
```

### プロジェクト構成

```
src/space_finder_mcp/
├── __init__.py          # main() → mcp.run()
├── __main__.py          # python -m space_finder_mcp 用の入口
├── server.py            # FastMCP サーバー定義・53ツール登録
├── stac_common.py       # STAC系共通の入力検証ヘルパー（bbox/雲量。ツール定義なし）
├── input_utils.py       # 引数の防御的数値変換 as_int/as_float（不正値でも例外を漏らさない）
├── img_common.py        # 画像合成の共通ヘルパー（フォント探索/JPEG化/アンチメリジアン分割。ツール定義なし）
├── name_common.py       # 天体名・衛星名の解決（表記ゆれ/和名→英語/Sesame で名前→座標。ツール定義なし）
├── nasa_budget.py       # api.nasa.gov のレート枠管理（投げる前の check/429 の記録/キーの伏せ字化）
├── satellite_map.py     # sat_ground_track（衛星の地上軌道マップ, CelesTrak+SGP4+Blue Marble）
├── cache.py             # キャッシュ基盤（TTLメモリ/ディスク資産キャッシュ。ツール定義なし）
├── env_config.py        # リポジトリ直下 .env の読み込み（標準ライブラリのみ。ツール定義なし）
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
├── weather_astro.py     # astronomy_weather（天体観測用天気, Open-Meteo, 月相＋気象庁雨雲画像）
├── jma_rain.py          # 気象庁の雨雲・降水画像の合成（解析雨量・降水予報＋ナウキャスト/雷）
├── weather_sat.py       # weather_satellite_now（GEO/LEO気象衛星の公開画像, 19機＋理由コード）
├── power.py             # power_climate（NASA POWER 気候・太陽エネルギー統計）
├── eodashboard.py       # eodashboard_collections / detail（EO Dashboard, NASA/ESA/JAXA）
├── donki.py             # space_weather（NASA 宇宙天気 DONKI）
├── swpc.py              # NOAA SWPC（宇宙天気のフォールバック・フレアイベント・認証不要）
├── ssd.py               # fireball_reports / neo_close_approach / impact_risk（JPL CNEOS, 認証不要）
├── stac_search.py       # stac_collections / stac_search（AWS Earth Search STAC）
├── iss.py               # iss_now（ISS 現在位置, Open Notify）
├── oscar.py             # satellite_status（WMO OSCAR 衛星カタログ）
├── cnsa.py              # cnsa_status（中国 CNSA 系衛星データポータル到達状態）
├── tiangong.py          # tiangong_now（天宮 中国宇宙ステーション位置, CelesTrak TLE + SGP4）
├── eso.py               # eso_seeing（ESO パラナル大気・シーイング）
├── cadc.py              # cadc_observations（CADC カナダ天文観測データ）
├── mast.py              # mast_observations（MAST: JWST/HST/TESS の観測アーカイブ検索）
├── gcn.py               # gcn_alerts（NASA GCN: 過渡天体速報 GCN Circulars）
├── literature.py        # 学術文献（OpenAlex/Crossref/NTRS/JAXAリポジトリ/J-STAGE/CiNii/Zenodo/DataCite ＋ 任意キー ADS/S2/WoS）
├── alma.py              # alma_search（ALMA 電波観測データ, Science Archive TAP）
├── tart.py              # radio_sources_now（TART オープン電波望遠鏡 可視電波源）
├── skyfield_pos.py      # constellation_now（天体位置・星座, Skyfield）
├── news.py               # astronomy_news（Sky & Telescope 天文ニュース）
├── sky_overlay.py        # sky_map_with_satellites（星図+人工衛星, matplotlib/Pillow）
├── solar_system.py        # solar_system_now（太陽系俯瞰図, JPL DE421+SBDB / matplotlib+Pillow）
├── solar_eclipse.py       # solar_eclipse_series（日食の時系列パネル, JPL DE421+Skyfield）
├── moon_phase.py          # moon_phase_map（月齢マップ: 月齢カレンダー/朔望月パネル, JPL DE421+Skyfield）
├── mars_rover.py         # mars_rover_status（火星ローバー状況・天気）
├── surface_map.py        # 天体面地図の共通描画（タイル合成・等角投影・地点マーカー・画素検証）
├── planetary_map.py      # planetary_orbiter_track（汎用・天体周回機マップ／落点マップ）
├── calendar_store.py     # 蓄積ストア（正規化レコード＋来歴／ユーザー予定・原子書き込み・月別TTL・prune）
├── space_calendar.py     # space_calendar / calendar_events / calendar_event_add / calendar_event_remove
└── planetary_rover.py    # planetary_rover_location_map（汎用・ローバー位置マップ）
```

リポジトリ直下（エージェント・クライアント向け）:

```
space-finder-mcp/
├── CLAUDE.md                # Claude Code 用プロジェクトガイド
├── AGENTS.md                # Codex など AGENTS.md を読むエージェント向け
├── SKILL.md                 # エージェント向けスキル定義（53ツール仕様）
├── mcp.json                 # MCPクライアント設定の例
├── .env.example             # 環境変数の例（NASA_API_KEY は任意）
├── .claude/rules/           # 開発規約（coding-conventions / testing-and-verification / data-and-sources）
├── scripts/check-tools.py   # 回帰検証ゲート（全ツール実行 / --offline / --dead-code / --fuzz
│                            #   / --figures / --media-links / --concurrency / --stdio）
├── tests/                   # 回帰テスト（unittest discover -s tests。並列・起動import・遮断時の挙動）
├── pyproject.toml           # 依存・バージョン（uv 管理）
└── README.md / LICENSE
```

---

## 📄 ライセンス / 注意

- **ライセンス**: MIT License（本リポジトリの `LICENSE` を参照）
- **データソース**: NASA・NASA Image & Video Library・Launch Library 2・Wikidata(Wikimedia)・ISRO・ESA Copernicus Data Space・JAXA Earth API・CSA Open Data・INPE BDC・CelesTrak（遮断時は tle.ivanstanojevic.me / SatNOGS へフォールバック）・UK EO DataHub・CNES THEIA/GEODES・CNSA（NSMC/CNSA-GEO/CRESDA）・CelesTrak（天宮TLE）・ESO ASM・ALMA Science Archive(NAOJ)・TART・CADC・JPL/Skyfield(de421)・NASA MMGIS・NASA Trek WMTS・Open-Meteo(CC BY 4.0)・EO Dashboard・NASA DONKI・NASA POWER・AWS Earth Search STAC・Open Notify・WMO OSCAR は、それぞれの利用条件・ライセンスに従います。
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
- [x] 世界の天文観測データ（ESO パラナルシーイング・CADC カナダ天文データ・MAST/JWST・HST アーカイブ）
- [x] ロシア（Roscosmos）の打ち上げデータ（russia_launches）
- [x] 天体位置・星座計算（constellation_now, Skyfield + JPL）
- [x] 天文ニュース（astronomy_news, Sky & Telescope RSS）
- [x] 火星探査ローバー状況（mars_rover_status, Mars Weather）
- [ ] 地球リアルタイム画像（EPIC/DSCOVR）
- [ ] 惑星の3D地図（NASA Trek WMTS）
- [ ] 多言語（en/zh）応答の全面対応


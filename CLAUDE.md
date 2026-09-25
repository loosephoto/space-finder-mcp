# Space Finder MCP Server — Claude Code 用プロジェクトガイド

宇宙・天文・地球観測の公開データを **62ツール**で横断検索する MCP サーバー（Python / uv 管理）です。NASA・ESA Copernicus・JAXA・ISRO・CSA・INPE・UK EO DataHub・CNSA・CelesTrak・JPL（DE421/SBDB/Horizons）・Wikidata・Open-Meteo などを統合し、衛星画像・軌道マップ・日食パネル・月齢マップを**画像で返しつつ**、LLM向けに構造化JSONも同時に返します。

**開発規約は分割ルールにあります**: `.claude/rules/`（コーディング規約・検証ゲート・データ出典）を参照してください。

---

## 前提条件

- **Python 3.11+** と **uv**（`pip install` は不要。依存は `uv sync` が解決）
- 依存: `mcp<2` / `requests` / `sgp4` / `skyfield` / `matplotlib` / `pillow`（すべて `pyproject.toml`）
- Windows / macOS / Linux で動作。初回は Skyfield が JPL 天体暦（de421.bsp, 約16MB）を自動ダウンロードします

## セットアップ（Claude Code から利用する場合）

### 1. APIキー（任意）

NASA Open API のキーは **任意**です。未設定でも `DEMO_KEY` で動作しますが、`DEMO_KEY` は **30リクエスト/時/IP の共有枠**で `apod`・`neo_today`・`space_weather` が取り合うため、実用時は無料キー（https://api.nasa.gov/）を推奨します。

キーの置き場所は2通り（**優先順位: MCPクライアントの env > リポジトリ直下の .env**）:

```bash
# (a) リポジトリ直下の .env（クライアント設定にキーを書きたくない場合）
cp .env.example .env
#   .env を編集して NASA_API_KEY=... を記入（.gitignore 済み・絶対にコミットしない）

# (b) MCPクライアントの env（claude mcp add の --env / mcp.json の env）
```

### 2. Claude Code に MCP サーバーを登録

リポジトリ直下で実行（プロジェクトスコープ推奨）:

```bash
claude mcp add -s project space-finder -- uv --directory "$(pwd)" run space-finder-mcp
claude mcp list   # 確認
```

- `-s project` はリポジトリ直下に `.mcp.json` を作ります（チーム共有向け）。個人利用だけで済ませるなら `-s user`（全プロジェクトで有効）を使います。

検証済みの代替コマンド（いずれも 62ツールを返して起動します）:

```bash
uv --directory /abs/path/to/space-finder-mcp run space-finder-mcp
uv run --project /abs/path/to/space-finder-mcp space-finder-mcp
python -m space_finder_mcp                                                                              # パッケージ起動
/abs/path/to/space-finder-mcp/.venv/Scripts/python.exe -c "from space_finder_mcp import main; main()"   # Windows
```

- サーバーは **stdio** です。起動時のカレントディレクトリに依存しません（`.env` はパッケージ位置から解決）。
- 別ディレクトリに clone した場合はパスを clone 先に合わせてください。

### 3. 動作確認

```
ISSの現在位置を地球地図で →  mcp__space-finder__sat_ground_track
東京で見える次の日食を画像で →  mcp__space-finder__solar_eclipse_series
今月の月齢マップを見せて →  mcp__space-finder__moon_phase_map
今夜の天体観測に向く時間帯は？ →  mcp__space-finder__astronomy_weather
```

---

## 利用可能なツール（58種）

| カテゴリ | 本数 | ツール |
|:--|:--|:--|
| 打ち上げ・逆引き | 4 | `reverse_lookup` `upcoming_launches` `china_launches` `russia_launches` |
| NASA 日次・宇宙天気 | 3 | `apod` `neo_today` `space_weather` |
| メディア（画像/音声/動画） | 3 | `search_space_images` `search_space_audio` `search_space_videos` |
| 各国宇宙機関・地球観測 | 16 | `isro_data` `copernicus_collections` `copernicus_search` `jaxa_datasets` `jaxa_dataset_search` `csa_dataset_search` `inpe_collections` `inpe_search` `uk_stac_collections` `uk_stac_search` `cnes_status` `cnsa_status` `stac_collections` `stac_search` `eodashboard_collections` `eodashboard_detail` |
| 衛星・軌道 | 5 | `sat_tle` `satellite_status` `sat_ground_track` `iss_now` `tiangong_now` |
| 天体位置・画像合成 | 7 | `constellation_now` `sky_map_with_satellites` `solar_system_now` `solar_eclipse_series` `moon_phase_map` `planetary_orbiter_track` `planetary_rover_location_map` |
| 観測支援・天文データ | 8 | `astronomy_weather` `astronomy_news` `eso_seeing` `cadc_observations` `alma_search` `radio_sources_now` `power_climate` `mars_rover_status` |
| 気象衛星の実画像 | 1 | `weather_satellite_now` |
| 宇宙・天文カレンダー | 4 | `space_calendar` `calendar_events` `calendar_event_add` `calendar_event_remove` |

各ツールの引数・データ源の詳細は `README.md` のツール一覧、エージェント向け仕様は `SKILL.md` を参照。

## 利用パターン（ハマりどころ）

- **描画エンジン**: `sky_map_with_satellites` / `solar_system_now` は `engine="simple"`（既定・Pillow合成・学生向け視認性重視）と `engine="accurate"`（matplotlib・正確座標）。`simple` は JPEG、`accurate` は PNG を返します。
- **レート制限**: api.nasa.gov を使うツール（`apod`・`neo_today`・`space_weather`）は `nasa_budget` を通して**投げる前に**枠を確認し、429 は `Retry-After` を尊重します（同じ 429 を繰り返し踏みに行かない）。
- **キャッシュ**: データの性質ごとに3層（不変アセット=ディスク / 揮発データ=TTLメモリ 10分〜24時間 / 高コスト計算=日食探索など）。**同じ質問を繰り返しても外部APIを叩き直しません**。現在位置系（`iss_now`・`tiangong_now`・各位置計算）はリアルタイム性を優先しキャッシュ対象外です。
- **レート制限**: `DEMO_KEY` 使用時は 429 になり得ます。エラー応答は対処方法込みで返し、キャッシュもしません（再試行されます）。
- **曖昧な衛星名**: `sentinel` のように候補が複数ある場合は推測せず、NORAD ID 付きの候補を提示して停止します。
- **過去ミッション**: かぐや（SELENE）等は「運用終了のため現在位置を表示できない」と正直に返します（誤った天体を出さない）。
- **出典**: 結果には出典URL・クレジットが含まれます。回答時に**引用元を必ず表示**してください。

## 開発ワークフロー

変更後は必ず検証（詳細は `.claude/rules/testing-and-verification.md`）:

```bash
uv run python -m compileall -q src/space_finder_mcp   # 構文チェック
uv run python scripts/check-tools.py --dead-code      # デッドコード走査（0件を維持）
uv run python scripts/check-tools.py --offline        # ネットワーク全断で例外漏れを検査
uv run python scripts/check-tools.py --fuzz           # 数値引数へ不正値を注入（例外漏れ0を維持）
uv run python scripts/check-tools.py --media-links    # 画像/音声/動画のリンク先行（0件を維持）
uv run python scripts/check-tools.py --fonts          # 日本語フォントの解決（Win/mac/Linux 共通・豆腐回避。日本語グリフ無しで exit 1）
uv run python scripts/check-tools.py --concurrency    # 並列実行（single-flight・スレッド逃がし・例外漏れ）
uv run python scripts/check-tools.py --stdio          # 実クライアント経路(stdio)で代表ツールが応答するか
uv run python -m unittest discover -s tests          # 回帰テスト（対応済みの実バグの再発防止）
uv run python scripts/check-tools.py                  # 全62ツール実呼び出し（数分）
uv run python scripts/check-tools.py --only sat_tle,apod   # 特定ツールのみ
```

**重要**: MCP サーバーは**ホットリロードがありません**。`src/` を変更したらクライアント（Claude Code / Codex 等）を再起動してください。

## コーディング規約（要点）

- 各ツールは必ず `CallToolResult` を返す（**例外を外へ漏らさない**）。`content` に人間向け表示（画像は `ImageContent`）、`structuredContent` に LLM向け純粋JSON。
- 数値変換は防御的に。**ツール入口の数値引数は必ず `input_utils.as_int` / `as_float` を通す**（不正値 `"5件"` を `int()` へ直に渡すと例外が外へ漏れる）。API応答値も同様。`d[key]` より `.get()`。
- キャッシュは `cache.py` の `ttl_cache` / `disk_get` を使い、**エラー応答はキャッシュしない**（`skip_if=is_error_result`）。キャッシュ値を呼び出し側で書き換えるなら `deepcopy`。
- **日本語フォントは OS 非依存で解決する**（画像内の日本語が豆腐=□になるのを防ぐ）。`img_common.load_font()` が *環境変数 `SPACE_FINDER_FONT`(`_BOLD`) → OS 標準パス（Windows メイリオ / macOS ヒラギノ / Linux Noto CJK・IPA・VL・Takao）→ 標準フォントディレクトリの走査* の順に探し、採用前に **cmap を読んで日本語グリフの有無を検証**する（名前では判定できない。例: DejaVu Sans は日本語なし）。Windows のパスだけを列挙すると macOS / Linux で PIL 既定フォントに落ちて日本語が全部化ける。matplotlib 経路（accurate 版）は各ツールでフォント名を列挙せず `img_common.apply_matplotlib_cjk_font()` を使う（pyplot を import せず rcParams を設定する）。検査は `scripts/check-tools.py --fonts`。
- 共通処理は `img_common.py`（フォント/JPEG/アンチメリジアン）・`surface_map.py`（天体面地図のタイル合成・等角投影・地点マーカー・画素検証）・`stac_common.py`（bbox/雲量検証）・`name_common.py`（表記ゆれ・和名→英語名・Sesame による名前→座標）・`cache.py` に集約。同じ処理を各モジュールに重複実装しない。
- 依存追加は最小限（標準ライブラリを優先）。Pillow / sgp4 / requests は関数内で遅延 import（起動を速く保つ）。**ただし numpy / matplotlib / skyfield は起動時に import**（遅延 import すると stdio で無応答。下記「並列」の項を参照）。
- ドキュメント・コメントは日本語。ツールの docstring は**クライアント向け仕様**（例文・引数・認証要否を書く）。
- **描画系ツールは `structuredContent.figure`（`schema: "figure/1"`）を返す**。視点(`view`)・主天体の置き方(`primary`：楕円は**焦点**であって中心ではない)・縮尺(`scale`)・円錐曲線(`conic`)・注記(`notes`)・自己検証(`verify`)を含め、注記は `img_common` の `figure_notes` 等で**数値から生成**する（手書きは図と文が食い違う）。`content` にも `figure_text_block()` で同じ注記を出し、docstring に「`figure.notes` は要約せず引用する」と明記。**LLM 向けの指示文（「要約せず引用する」等）は `content` に書かない**（`content` は人間が読むチャネルで、指示文が混ざると読み手に意味不明な文が表示される）。指示は docstring と `structuredContent.figure.notes_usage`（`figure_text_block()` の見出しは `### ⚠️ 図の注記` のみ）に置く。**閉じない軌道（e≥1 / a<0）を楕円として描かない**。近点が画面上で分解できない場合（超長距離の楕円の近日点が誇張した主天体円盤の内側に入る等）は `verify_curve(occluders=[(x, y, r_px)])` で上界検査へ自動切替し、その旨を注記に数値から生成する（`periapsis_resolvable: false`）。曲線に重なるラベルは描かず注記に回す。検査は `scripts/check-tools.py --figures`（超長距離楕円の経路も叩く）。加えて、**地図の上に置く情報パネルは `surface_map.panel_placement()` で現在位置マーカーを隠さない隅へ置く**（描いた後にパネルを重ねると、地図の暗幕でマーカーが消える：月面の LRO が北緯82°＝図の上端に来た実測で、左上のパネルの下に入り`marker_pixels: 0` になった）。`verify` に `panel_overlaps_marker` を残し、どの隅でも重なる小さい図ではマーカーをパネルの上に描き直して、その旨を注記に出す。
- **メディア（画像/音声/動画）を含む応答は、メディア本体より前にアイコン付きリンクを必ず出す**（`🖼️/🎧/🎬/📄 [◯◯を開く](URL または file:///…)`）。CLI系・Android系ハーネスは `ImageContent` を描画しないため、このリンクが唯一の導線。**リンク行は `img_common.media_link_line(対象, kind=…)` で作る**（動詞は kind から決まる＝`画像を開く`/`生成した画像を開く`/`音声を開く`/`動画を開く`/`ファイルを開く`。手書きすると表記がばらつく）。生成画像は `save_output()` で保存し `structuredContent.image_path` にも実パスを入れる。docstring に「回答時はこのリンクをそのまま提示してください」と明記。**複数の画像を返すときはリンクをまとめて先に出し、各リンク行を空行で区切る**（単一改行は Markdown のソフト改行で同一段落に畳み込まれ、リンクが1行に融合する。配信時に `server._delivered` が `img_common.layout_media_links` で補うが、ツール側でも空行を入れる）。検査は `scripts/check-tools.py --media-links`。

- **宇宙・天文カレンダーの蓄積ストアは `%LOCALAPPDATA%\space-finder-mcp\` に置く**（`cache` の Temp 配下・`save_output` の出力先とは別。ユーザーの予定が消えてはいけない）。書き込みは tmp＋`os.replace` の原子置換＋`Lock`、API 由来レコードは prune するが **`user:` の予定は prune しない**（削除は tombstone）。打ち上げは `net_precision` が Day/Hour/Minute/Second 以外・または年末（12/30〜01/01）の行を**日付セルに置かない**（LL2 と NASA の双方で実測したプレースホルダ）。小天体イベント（`calendar_store.KINDS` の `neo` / `fireball`）は **`neo_close_approach` / `fireball_reports` の呼び出し結果を蓄積するだけ**で、カレンダー自身は JPL を取得しない（未蓄積の月は空＝「イベント無し」ではない旨を `figure.notes` に出す）。接近時刻は TDB・火球は観測記録である旨を本文に明記し、**日付が無い `impact_risk`（数十年〜百年の幅の確率）は置かない**。

- **LLM は1ターンで複数ツールを並行に呼ぶ前提で書く**。全ツールは `server.py` の `_reg()` で登録し、同期関数の本体は `anyio.to_thread` のワーカースレッドで実行する（FastMCP は同期関数をイベントループ上でそのまま呼ぶので、逃がさないと1つの API 待ちが他を全部止める。実測: 4並列で wall = 合計 → 逃がした後 4.0×）。`ttl_cache` は single-flight（同一引数の並行呼び出しを1回に集約）、matplotlib の経路は `img_common.RENDER_LOCK` で直列化、モジュール可変状態は `threading.Lock`、キャッシュされた戻り値は書き換えない。検査は `scripts/check-tools.py --concurrency`。CPU 律速は GIL で並列化しない（効くのは I/O 待ち）。加えて、**遅延 import するネイティブ拡張は起動前にまとめて import する**（`server.py` 冒頭の `import numpy`）。stdio サーバーが起動した後に numpy / matplotlib / skyfield を import すると、この環境では import が完了せず**ツール呼び出しが無応答になる**（実測: numpy・matplotlib.pyplot・skyfield.api が HANG。PIL.Image・sgp4・requests は問題なし）。numpy を起動時に1回 import しておけば、その後の matplotlib/skyfield も通る（起動 +0.1 秒）。新しい遅延 import を足すときは事前 import 側にも加え、`scripts/check-tools.py --stdio` で確認する。外部 API を叩くときは **(connect, read) のタイムアウトを必ず指定**し、遮断（403・接続不可）は**記憶して以降は fail fast** する（`celestrak.blocked_status` / `nasa_budget` 参照）。**TLE のように代替源があるものは遮断時に代替源へ切り替える**（`celestrak.fetch_tle_ex`。出典を返し、代替源のデータを一次源の名前で書かない）。TCP が blackhole したホストへ素の呼び出しを投げると1回の呼び出しが分単位で固まり、並列で走っている他のツール呼び出しまで待たされる（実測: CelesTrak 遮断中に120秒以上無応答 → connect タイムアウト＋遮断記憶で 10 秒→0 秒）。
## リリース手順

1. `README.md` のツール表・「直近の更新内容」と `SKILL.md` を**同一変更内で**更新（ツール追加/削除/仕様変更時）
2. `pyproject.toml` の `version` を更新
3. `uv run python scripts/check-tools.py` で全62ツールが正常なことを確認
4. `uv build` でパッケージ作成を確認 → `dist/` `build/` を削除

## ライセンス・データ出典

- MIT License（`LICENSE`）
- データは各提供元の利用条件に従います（NASA / ESA / JAXA / ISRO / CSA / INPE / UK EO DataHub / CNSA / CelesTrak / JPL / Wikidata / Open-Meteo(CC BY 4.0) / WMO OSCAR など）。**回答には出典を必ず表示**してください。

## 補足

- `ssd.py` — 天体異常系（`fireball_reports` / `neo_close_approach` / `impact_risk`）。JPL CNEOS（`ssd-api.jpl.nasa.gov`）は**認証不要**で、`DEMO_KEY` の 30 req/h/IP の枠を消費しない。

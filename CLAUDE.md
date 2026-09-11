# Space Finder MCP Server — Claude Code 用プロジェクトガイド

宇宙・天文・地球観測の公開データを **45ツール**で横断検索する MCP サーバー（Python / uv 管理）です。NASA・ESA Copernicus・JAXA・ISRO・CSA・INPE・UK EO DataHub・CNSA・CelesTrak・JPL（DE421/SBDB/Horizons）・Wikidata・Open-Meteo などを統合し、衛星画像・軌道マップ・日食パネルを**画像で返しつつ**、LLM向けに構造化JSONも同時に返します。

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

検証済みの代替コマンド（いずれも 45ツールを返して起動します）:

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
今夜の天体観測に向く時間帯は？ →  mcp__space-finder__astronomy_weather
```

---

## 利用可能なツール（45種）

| カテゴリ | 本数 | ツール |
|:--|:--|:--|
| 打ち上げ・逆引き | 4 | `reverse_lookup` `upcoming_launches` `china_launches` `russia_launches` |
| NASA 日次・宇宙天気 | 3 | `apod` `neo_today` `space_weather` |
| メディア（画像/音声/動画） | 3 | `search_space_images` `search_space_audio` `search_space_videos` |
| 各国宇宙機関・地球観測 | 16 | `isro_data` `copernicus_collections` `copernicus_search` `jaxa_datasets` `jaxa_dataset_search` `csa_dataset_search` `inpe_collections` `inpe_search` `uk_stac_collections` `uk_stac_search` `cnes_status` `cnsa_status` `stac_collections` `stac_search` `eodashboard_collections` `eodashboard_detail` |
| 衛星・軌道 | 5 | `sat_tle` `satellite_status` `sat_ground_track` `iss_now` `tiangong_now` |
| 天体位置・画像合成 | 6 | `constellation_now` `sky_map_with_satellites` `solar_system_now` `solar_eclipse_series` `planetary_orbiter_track` `planetary_rover_location_map` |
| 観測支援・天文データ | 8 | `astronomy_weather` `astronomy_news` `eso_seeing` `cadc_observations` `alma_search` `radio_sources_now` `power_climate` `mars_rover_status` |

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
uv run python scripts/check-tools.py                  # 全45ツール実呼び出し（数分）
uv run python scripts/check-tools.py --only sat_tle,apod   # 特定ツールのみ
```

**重要**: MCP サーバーは**ホットリロードがありません**。`src/` を変更したらクライアント（Claude Code / Codex 等）を再起動してください。

## コーディング規約（要点）

- 各ツールは必ず `CallToolResult` を返す（**例外を外へ漏らさない**）。`content` に人間向け表示（画像は `ImageContent`）、`structuredContent` に LLM向け純粋JSON。
- 数値変換は防御的に。**ツール入口の数値引数は必ず `input_utils.as_int` / `as_float` を通す**（不正値 `"5件"` を `int()` へ直に渡すと例外が外へ漏れる）。API応答値も同様。`d[key]` より `.get()`。
- キャッシュは `cache.py` の `ttl_cache` / `disk_get` を使い、**エラー応答はキャッシュしない**（`skip_if=is_error_result`）。キャッシュ値を呼び出し側で書き換えるなら `deepcopy`。
- 共通処理は `img_common.py`（フォント/JPEG/アンチメリジアン）・`stac_common.py`（bbox/雲量検証）・`cache.py` に集約。同じ処理を各モジュールに重複実装しない。
- 依存追加は最小限（標準ライブラリを優先）。画像は Pillow/matplotlib を関数内で遅延 import。
- ドキュメント・コメントは日本語。ツールの docstring は**クライアント向け仕様**（例文・引数・認証要否を書く）。

## リリース手順

1. `README.md` のツール表・「直近の更新内容」と `SKILL.md` を**同一変更内で**更新（ツール追加/削除/仕様変更時）
2. `pyproject.toml` の `version` を更新
3. `uv run python scripts/check-tools.py` で全45ツールが正常なことを確認
4. `uv build` でパッケージ作成を確認 → `dist/` `build/` を削除

## ライセンス・データ出典

- MIT License（`LICENSE`）
- データは各提供元の利用条件に従います（NASA / ESA / JAXA / ISRO / CSA / INPE / UK EO DataHub / CNSA / CelesTrak / JPL / Wikidata / Open-Meteo(CC BY 4.0) / WMO OSCAR など）。**回答には出典を必ず表示**してください。

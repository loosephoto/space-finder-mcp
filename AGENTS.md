# AGENTS.md — Space Finder MCP Server（Codex / 汎用コーディングエージェント向け）

宇宙・天文・地球観測の公開データを **45ツール**で横断検索する MCP サーバー（Python / uv 管理）。NASA・ESA・JAXA・ISRO・CSA・INPE・UK・CNSA・CelesTrak・JPL・Wikidata・Open-Meteo 等を統合し、画像（衛星軌道マップ・日食パネル等）と構造化JSONを同時に返します。

`CLAUDE.md` は同じ内容を Claude Code 向けに書いたものです（本ファイルは Codex など AGENTS.md を読むエージェント向け）。詳細なツール仕様は `SKILL.md`、引数一覧は `README.md` を参照してください。

## 前提条件

- Python 3.11+、`uv`（依存は `uv sync` が解決。`pyproject.toml` 参照）
- 初回は Skyfield が JPL 天体暦 de421.bsp（約16MB）を自動ダウンロード

## セットアップ

```bash
uv sync                                    # 依存の解決
uv run space-finder-mcp                    # 単体起動（stdio。MCPクライアントから使う）
```

### APIキー（任意）

`NASA_API_KEY` は任意（未設定なら `DEMO_KEY`。ただし **30リクエスト/時/IP の共有枠**を `apod`・`neo_today`・`space_weather` で取り合います）。置き場所は **MCPクライアントの env**（優先）または **リポジトリ直下の `.env`**:

```bash
cp .env.example .env    # NASA_API_KEY=... を記入（.gitignore 済み・コミット禁止）
```

### Codex に MCP サーバーを登録

```bash
codex mcp add space-finder --env NASA_API_KEY=<your_key> -- uv --directory <ABS_PATH>/space-finder-mcp run space-finder-mcp
codex mcp list
```

`--env` は不要なら省けます（その場合 `.env` か DEMO_KEY を使用）。検証済みの代替起動コマンド: `uv run --project <ABS_PATH> space-finder-mcp` / `<ABS_PATH>/.venv/Scripts/python.exe -c "from space_finder_mcp import main; main()"`。

## 変更時に必ず実行する検証（ゲート）

```bash
uv run python -m compileall -q src/space_finder_mcp   # 構文
uv run python scripts/check-tools.py --dead-code      # 未参照定義・未使用import（0件を維持）
uv run python scripts/check-tools.py --offline        # ネットワーク全断で例外漏れを検査
uv run python scripts/check-tools.py --fuzz           # 数値引数へ不正値を注入（例外漏れ0を維持）
uv run python scripts/check-tools.py                  # 全45ツール実呼び出し（数分・終了コード1で失敗）
```

- **MCP はホットリロードなし**。`src/` を変更したらクライアントを再起動。
- 終了コード: 0=正常 / 1=異常（例外漏れ・structuredContent欠落・タイムアウト・デッドコード）。

## 守るべき規約

1. **全ツールは `CallToolResult` を返す**。例外をツール外へ漏らさない（外部API障害時も `structuredContent.error` を返す）。
2. `content` = 人間向け表示（画像は `ImageContent`）、`structuredContent` = LLM向け純粋JSON。両方返すのが基本。
3. 数値変換は防御的に。ツール入口の数値引数は必ず `input_utils.as_int` / `as_float` を通す（`float("?")` 等で例外を外へ漏らさない）。`d[key]` ではなく `.get()`。
4. キャッシュは `cache.py` の `ttl_cache` / `disk_get` を使う。**エラー応答はキャッシュしない**。キャッシュ値を書き換えるなら `deepcopy`。
5. 共通処理は `img_common.py` / `stac_common.py` / `cache.py` に集約し、重複実装しない。
6. 現在位置系（`iss_now`・`tiangong_now`・位置計算）は**キャッシュしない**（リアルタイム性優先）。
7. 曖昧な入力（複数候補の衛星名など）は推測せず**候補を提示して停止**する。
8. 過去ミッション（かぐや等）は正確に「表示できない」と返す（誤った天体を出さない）。
9. 依存追加は最小限。Pillow / matplotlib は遅延 import。ドキュメント・コメントは日本語。
10. **ツールを追加・削除・変更したら `README.md` のツール表と `SKILL.md` を同一変更内で更新**。

## 主要ファイル

```
src/space_finder_mcp/
├── server.py        # FastMCP サーバー定義・45ツール登録
├── cache.py         # キャッシュ基盤（TTLメモリ / ディスク資産）
├── img_common.py    # 画像共通（フォント探索 / JPEG化 / アンチメリジアン分割）
├── stac_common.py   # STAC系の入力検証（bbox / 雲量）
├── input_utils.py   # 引数の防御的数値変換（as_int / as_float）
├── env_config.py    # リポジトリ直下 .env の読み込み（標準ライブラリのみ）
├── *_map.py         # 画像生成系（satellite_map / planetary_map / planetary_rover / sky_overlay）
└── <データ源>.py     # 各APIツール（nasa / launch / media / celestrak / jaxa / ...）
scripts/check-tools.py   # 回帰検証ゲート
```

## ライセンス

MIT。データは各提供元（NASA / ESA / JAXA / ISRO / CSA / INPE / UK / CNSA / CelesTrak / JPL / Wikidata / Open-Meteo 等）の条件に従い、回答には出典を表示すること。

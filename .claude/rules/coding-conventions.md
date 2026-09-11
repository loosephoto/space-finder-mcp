# コーディング規約（space-finder-mcp）

## 応答形式（最重要）

- すべての公開ツールは **`CallToolResult` を返す**。`str` を返すツールは作らない。
- `content` = 人間向け表示（テキストサマリ＋画像は `ImageContent`）
- `structuredContent` = LLM向け純粋JSON（LLMが表示文を要約しても元データを失わない）
- ツールの docstring は**クライアント向け仕様**として書く（例文・引数・認証要否・返却形式）。

## 例外・入力の防御

- **例外をツールの外へ漏らさない**。外部API呼び出しは `try/except requests.RequestException` で囲み、`structuredContent={"error": ..., "source": ...}` を返す。
- ネットワーク失敗と「該当なし」を区別する（例: CelesTrak の 404 は「見つかりません」、接続失敗は「取得に失敗」）。
- 数値は防御的に変換する: `float(raw)` を直接使わず `try/except (TypeError, ValueError)`。`d[key]` より `.get()`。
- 必須引数が `None`/空の場合も例外を出さず、検証メッセージ（候補一覧付き）を返す。
- 曖昧入力（複数候補）は**推測せず候補を提示して停止**する。

## キャッシュ（`cache.py`）

- 不変アセット（タイル・画像資産）は `disk_get(url, subdir=...)`。書き込みは `tmp + os.replace` でアトミック。
- 揮発データは `@ttl_cache(TTL_*, skip_if=is_error_result)`。TTL はデータの性質で選ぶ（`TTL_SHORT` 10分 / `TTL_FORECAST` 30分 / `TTL_HOURLY` 1時間 / `TTL_DAILY` 24時間）。
- **エラー応答はキャッシュしない**。`None`/`[]` を返す失敗系にも `skip_if` を付ける。
- キャッシュされた戻り値を呼び出し側で書き換えるなら **`deepcopy`** する（共有エントリを汚染しない）。
- キーに **引数オブジェクトを使わない**（Skyfield の site などは毎回別物でキャッシュが効かない）。丸めたスカラー（緯度経度2桁・日付）をキーにする。
- ライブ性が重要なツール（`iss_now`・`tiangong_now`・位置計算）はキャッシュしない。

## 構造

- 共通処理は `img_common.py`（フォント探索/JPEG化/アンチメリジアン分割）、`stac_common.py`（bbox/雲量検証）、`cache.py`、`env_config.py` に集約し、各ツールへ重複実装しない。
- 依存方向は一方向（例: `planetary_rover → planetary_map → img_common/cache`）。循環 import を作らない。
- 依存追加は最小限（標準ライブラリ優先）。Pillow / matplotlib は関数内で遅延 import（起動を速く保つ）。

## ドキュメント

- ドキュメント・コメント・エラーメッセージは日本語。
- ツールを追加/削除/変更したら **`README.md` のツール表と `SKILL.md` を同一変更内で更新**する。

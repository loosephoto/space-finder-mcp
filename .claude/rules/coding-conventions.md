# コーディング規約（space-finder-mcp）

## 応答形式（最重要）

- すべての公開ツールは **`CallToolResult` を返す**。`str` を返すツールは作らない。
- `content` = 人間向け表示（テキストサマリ＋画像は `ImageContent`）
- `structuredContent` = LLM向け純粋JSON（LLMが表示文を要約しても元データを失わない）
- ツールの docstring は**クライアント向け仕様**として書く（例文・引数・認証要否・返却形式）。

## 例外・入力の防御

- **例外をツールの外へ漏らさない**。外部API呼び出しは `try/except requests.RequestException` で囲み、`structuredContent={"error": ..., "source": ...}` を返す。
- ネットワーク失敗と「該当なし」を区別する（例: CelesTrak の 404 は「見つかりません」、接続失敗は「取得に失敗」）。
- 数値は防御的に変換する。**ツール入口の数値引数（limit/minutes/step/lat/lon/band 等）は必ず `input_utils.as_int` / `as_float` を通す**（MCPクライアントは `"5件"` のような値も送る。`int()` へ直に渡すと ValueError がツール外へ漏れる）。API応答値も同様に `as_float` 等で守る。`d[key]` より `.get()`。
- 必須引数が `None`/空の場合も例外を出さず、検証メッセージ（候補一覧付き）を返す。
- 曖昧入力（複数候補）は**推測せず候補を提示して停止**する。

## キャッシュ（`cache.py`）

- 不変アセット（タイル・画像資産）は `disk_get(url, subdir=...)`。書き込みは `tmp + os.replace` でアトミック。
- 揮発データは `@ttl_cache(TTL_*, skip_if=is_error_result)`。TTL はデータの性質で選ぶ（`TTL_SHORT` 10分 / `TTL_FORECAST` 30分 / `TTL_HOURLY` 1時間 / `TTL_DAILY` 24時間）。
- **エラー応答はキャッシュしない**。`None`/`[]` を返す失敗系にも `skip_if` を付ける。
- キャッシュされた戻り値を呼び出し側で書き換えるなら **`deepcopy`** する（共有エントリを汚染しない）。
- キーに **引数オブジェクトを使わない**（Skyfield の site などは毎回別物でキャッシュが効かない）。丸めたスカラー（緯度経度2桁・日付）をキーにする。
- ライブ性が重要なツール（`iss_now`・`tiangong_now`・位置計算）はキャッシュしない。

## 図の注記（figure/1・描画系ツールのみ）

自前で図を描くツール（solar_system_now / sat_ground_track / planetary_orbiter_track /
planetary_rover_location_map / sky_map_with_satellites / solar_eclipse_series）は、
`structuredContent.figure` に「どう描いたか」を自己申告する。

- 主天体は**円錐曲線の焦点**に置く（`primary.at = "focus"`）。楕円の中心に置くと、高離心率の
  軌道が「主天体の周りを回る丸い軌道」に見えてしまう（実際に誤描画した事例あり）。
- **e ≥ 1 / 半長軸 a < 0 の軌道（C/彗星など）を楕円として描かない**。閉じていない＝遠点は
  存在せず、`a(1+e)` は負になる。`conic_from_elements` が kind を ellipse/parabola/hyperbola
  に判定するので、双曲線は枝として有限距離まで描く。
- 注記(`notes`)は `figure_notes` などで**数値から生成**する。手書きにすると図と注記がドリフトする。
- `content` にも `figure_text_block()` で同じ注記を出し、docstring に
  「`figure.notes` は要約・言い換えせず、そのまま引用すること」と書く（LLM 向けの指示）。
- 図の自己検証(`verify`)を入れる: 描いた画素から近点/遠点距離を逆算して a(1±e) と照合
  （`verify_curve`）、ラベル矩形に曲線色が混入していないこと（文字と線の重なり）も見る。
- 検査: `uv run python scripts/check-tools.py --figures`（注記が空・`verify.ok` が偽なら exit 1）。
## 構造

- 共通処理は `img_common.py`（フォント探索/JPEG化/アンチメリジアン分割）、`stac_common.py`（bbox/雲量検証）、`cache.py`、`env_config.py` に集約し、各ツールへ重複実装しない。
- 依存方向は一方向（例: `planetary_rover → planetary_map → img_common/cache`）。循環 import を作らない。
- 依存追加は最小限（標準ライブラリ優先）。Pillow / matplotlib は関数内で遅延 import（起動を速く保つ）。

## ドキュメント

- ドキュメント・コメント・エラーメッセージは日本語。
- ツールを追加/削除/変更したら **`README.md` のツール表と `SKILL.md` を同一変更内で更新**する。

# 効率化・最適化（モジュール分割と JSON 受け渡し）— 現行仕様

最終更新: v0.49.0（2026-11-01 実測）

## 目的

1. **JSON の受け渡しを 1 か所に集約する** — `structuredContent` は LLM が読む唯一の
   機械可読チャネルなのに、型の正規化が各ツールに散っている（numpy 型の混入）。
2. **巨大モジュールを層で分ける** — `solar_system.py` 1 本に「通信・データモデル・
   軌道計算・描画・画素検証・ツールハンドラ」が同居していた。
3. **同じ処理の二重実装をなくす** — 地名解決が 3 モジュールに複製されていた。

## 実測（変更前）

| 項目 | 実測値 | 出し方 |
|:--|--:|:--|
| `tools/list` のサイズ | 97,429 バイト（≒32,500 トークン） | `mcp.list_tools()` を `json.dumps` |
| うち description | 56,747 バイト | 同上 |
| うち inputSchema | 27,908 バイト | 同上 |
| `structuredContent` の numpy 型 | `constellation_now` 45 個 / `solar_system_now` 9 個 | 型を再帰走査 |
| `solar_system.py` | 3,376 行 | `wc -l` |
| `planetary_map.py` | 2,244 行 | 同上 |
| `_resolve_place` の複製 | 2 か所（sky_overlay / solar_eclipse・23 行ずつ完全一致） | AST の正規化ハッシュ |
| `_KNOWN_COORDS` の複製 | 3 か所（+ `weather_astro` は別型の天文台表） | grep |
| `compact_description` の契約文 | 折り返し行が孤立断片として 11 行 / 4 ツールに混入 | 断片の正規表現走査 |

`_KNOWN_COORDS` の複製は**挙動の食い違い**も生んでいた: 「福岡」は `skyfield_pos`
（`constellation_now`）だけが解決でき、`sky_map_with_satellites` / `solar_eclipse_series`
ではオンライン解決へ落ちる。

## 変更後の構成

### JSON 境界（`jsonio.py`）

- `to_json(value)` — numpy スカラー/配列 → Python 型、`datetime` → ISO8601、
  `Decimal` → number、`set` → ソート済み配列、`NaN`/`Infinity` → `null`。
  **dict のキーは必ず文字列**。
- `is_json_native(value)` — 検査用（JSON の7型だけで構成されているか）。
- 呼び出しは `server._delivered()` の **1 か所だけ**。各ツールは何も書かない
  （＝受け渡しの契約を分散させない）。`to_json` は新しいコンテナを作るので
  `ttl_cache` が共有している戻り値は汚さない。

### 地名解決（`places.py`）

- `CITY_COORDS`（都市名 → 緯度経度、小文字キー）と `resolve_place(place, lat, lon)`。
- `sky_overlay` / `solar_eclipse` / `skyfield_pos` はここを参照する（表と規則の一本化）。

### 太陽系の層分割

| モジュール | 行数 | 責務 | 依存 |
|:--|--:|:--|:--|
| `solar_model.py` | 300 | 惑星表・光日の定数・SBDB 取得・ケプラー伝播・円錐曲線 | requests / cache / img_common（色） |
| `solar_render.py` | 1,413 | 斜め俯瞰の描画と画素検証（scene dict → PNG / (画像, figure)） | Pillow / img_common / orbit_view |
| `solar_system.py` | 1,766 | Horizons・探査機・彗星の取得、scene の組み立て、ツールハンドラ | 上記 2 つ |

`scene` は **JSON 化できる dict**（キーの一次情報は `_compute()` の docstring と
`docs/oblique-view-spec.md`）。描画層は scene を読むだけで通信しない。移動は純移動で、
画素・値は 1 バイトも変えない（回帰テストと `--figures` で確認）。

## 決定事項

- `tools/list` は **Args ブロックを一字も削らない**（引数の一次情報。機械的に切ると
  括弧が閉じない文になる）。削るのは (a) 折り返し行の孤立断片 (b) 重複した契約文だけ。
  今回の実測では 97,429 → 96,746 バイト（−683）。**これ以上は精度とのトレードオフ**で、
  情報を落とさない限り縮まない（Args が description の 6 割を占める）。
- 数値の**丸めはしない**（丸めは JSON 化の責務ではなく、値の意味を変える）。
- `NaN`/`Inf` は `null` にする（JSON に存在しない。計算不能を「値」として流さない）。

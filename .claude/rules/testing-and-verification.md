# 検証ゲート（space-finder-mcp）

変更後は必ず以下をリポジトリ直下で実行する。**MCPサーバーはホットリロードなし**（`src/` 変更後はクライアント再起動）。

```bash
uv run python -m compileall -q src/space_finder_mcp   # 1. 構文
uv run python scripts/check-tools.py --dead-code      # 2. デッドコード走査（0件を維持）
uv run python scripts/check-tools.py --offline        # 3. ネットワーク全断で例外漏れ0
uv run python scripts/check-tools.py --fuzz           # 3'. 数値引数へ不正値（"abc" 等）を注入して例外漏れ0
uv run python scripts/check-tools.py                  # 4. 全45ツール実呼び出し（数分）
uv run python scripts/check-tools.py --only sat_tle,apod   # 変更したツールだけ先に確認
```

`check-tools.py` の終了コードは 0=正常 / 1=異常（例外漏れ・structuredContent欠落・タイムアウト・デッドコード）。CI では `--json` を使う。

## 変更内容ごとの追加確認
- **描画系を変更したとき**: `uv run python scripts/check-tools.py --figures` で figure/1（注記・caption・verify.ok）を検査する。

| 変更 | 追加で確認すること |
|:--|:--|
| ツール追加/削除 | 全件実行（`check-tools.py`）／`README.md` ツール表・`SKILL.md`・`server.py` の登録を同期 |
| 画像生成の変更 | 画像が出ること（`blocks` に `image` がある）＋デコード可能・非単色。**リファクタなら旧実装とバイト比較**して等価性を確認 |
| キャッシュの変更 | 2回目がキャッシュヒットすること（リクエスト数0）／**エラーが固定化しないこと**／キャッシュ値の書き換えが他呼び出しへ漏れないこと |
| 外部API仕様の変更 | 実際に叩いて確認（例: CelesTrak の `FORMAT=JSON` は TLE行を返さない → `FORMAT=TLE`） |
| リリース | `pyproject.toml` の version、`uv build`、`dist/`・`build/` の削除 |

## 実測スナップショット（ベースライン）

| ツール | cold | warm（キャッシュ後） |
|:--|--:|--:|
| `planetary_orbiter_track`(LRO) | 3.77s / 65req | 1.20s / 2req |
| `solar_eclipse_series`(東京) | 23.02s | 0.63s |
| `satellite_status`(OSCAR) | 50.20s / 10req | 0.00s / 0req |
| `astronomy_news` / `stac_search` / `upcoming_launches` | 1.0–1.8s | 0.00s |
| `sky_map` simple の画像 | — | 992KB → 188KB（JPEG化） |

ディスクキャッシュは `%LOCALAPPDATA%\Temp\space_finder_mcp\cache`（消せば cold に戻る。次回呼び出しで再生成）。

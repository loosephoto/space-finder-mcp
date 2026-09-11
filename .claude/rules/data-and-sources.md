# データソースと出典（space-finder-mcp）

## 方針

- **認証不要の公開APIのみ**で構成する（例外は NASA の任意キー・Copernicus の認証付きダウンロードで、後者は本サーバーでは扱わない）。
- 回答には**出典URL（markdownリンク）を必ず表示**する。データは各提供元の利用条件に従う。
- 実在しないデータを補完しない。取得できない場合は「取得できない理由」を返す（過去ミッション・運用終了など）。

## 主要ソースと注意点

| ソース | 注意点 |
|:--|:--|
| NASA Open API (api.nasa.gov) | `DEMO_KEY` は **30req/時/IP** の共有枠（`apod`・`neo_today`・`space_weather` で共有）。`nasa_budget.check()` を投げる前に通し（枠切れなら HTTP を出さない）、429 は `note_429()` で `Retry-After` を記録する。APOD は `date` 省略で 500 を返す事象があるため**日付を明示**し、当日未公開(404)なら前日へフォールバックする |
| CelesTrak (gp.php) | `FORMAT=JSON` は**軌道要素のみで TLE行を返さない**。SGP4 に渡す生 TLE は `FORMAT=TLE` で取得する（JSON由来だと `SGP4 error code 2` で常時失敗する）。404 は「該当なし」 |
| JPL Horizons / SBDB / DE421 | Horizons の宇宙機は**負のID**（例: はやぶさ2 = -37）。SBDB は小惑星のみ（探査機は不可） |
| NASA MMGIS / NASA Trek / Blue Marble | 位置データは火星ローバーのみ公開。天体地図は Trek WMTS（等角図法、`cols=2^(z+1)`, `rows=2^z`）。タイルはディスクキャッシュ対象 |
| Sky & Telescope ほかRSS | Cloudflare がブラウザ偽装UAを弾く（curl/Wget系UAは許可）。`astronomy_news` は**複数フィードのフォールバック連鎖** |
| Open-Meteo / Nominatim | Open-Meteo は都市名（英語強め・ja→enエイリアス付き）。Nominatim は識別可能なUA必須（403 になるUAあり）。結果は 24時間キャッシュ |
| Wikidata SPARQL | 「初・記録」の回答は SPARQL の結果に基づく（記憶から捏造しない）。必ず Wikipedia 記事を引用する |
| WMO OSCAR | 衛星カタログは約1000件・取得が重い（実測50秒）→ 24時間キャッシュ |
| ESO / ALMA / CADC / TART | 観測データ・大気コンディション。ESO/TART は短め（10分）、ALMA/CADC は30分のTTL |

## 画像の扱い

- 画像は base64 で `content` にインライン表示（JPEG/PNG）。**クライアントへ送る量**に注意: 実写合成は JPEG、座標図は PNG が適切。インライン枚数は既定で絞る（`inline_max`）。
- 画像の著作権・クレジット表記は各ソースの指示に従う（NASA素材は NASA Media Usage Guidelines）。

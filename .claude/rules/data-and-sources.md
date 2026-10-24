# データソースと出典（space-finder-mcp）

## 方針

- **認証不要の公開APIのみ**で構成する（例外は NASA の任意キー・Copernicus の認証付きダウンロードで、後者は本サーバーでは扱わない）。
- 回答には**出典URL（markdownリンク）を必ず表示**する。データは各提供元の利用条件に従う。
- 実在しないデータを補完しない。取得できない場合は「取得できない理由」を返す（過去ミッション・運用終了など）。

## 主要ソースと注意点

| ソース | 注意点 |
|:--|:--|
| NASA Open API (api.nasa.gov) | `DEMO_KEY` は **30req/時/IP** の共有枠（`apod`・`neo_today` で共有。`space_weather`(DONKI) と SWPC フォールバック、および**予報**（`kind="forecast"`）は認証不要の別ホストなので消費しない）。`nasa_budget.check()` を投げる前に通し（枠切れなら HTTP を出さない）、429 は `note_429()` で `Retry-After` を記録する。APOD は `date` 省略で 500 を返す事象があるため**日付を明示**し、当日未公開(404)なら前日へフォールバックする |
| CelesTrak (gp.php) | `FORMAT=JSON` は**軌道要素のみで TLE行を返さない**。SGP4 に渡す生 TLE は `FORMAT=TLE` で取得する（JSON由来だと `SGP4 error code 2` で常時失敗する）。404 は「該当なし」。**短時間の連続リクエストで IP 単位に遮断（403／TCP blackhole）**され、素の呼び出しは分単位で固まるため、接続は (connect 10s, read 25s) で打ち切り、遮断を記憶して以降は fail fast する（`celestrak.blocked_status`） |
| JPL Horizons / SBDB / DE421 | Horizons の宇宙機は**負のID**（例: はやぶさ2 = -37）。SBDB は小惑星のみ（探査機は不可） |
| JPL Horizons の時刻スケール | **数値 JD は TDB**（実測 2026-10-24: 数値JD 2460683.750000 と暦文字列 `'2025-01-08 06:00'` が同一の状態ベクトルを返し、見出しは `A.D. 2025-Jan-08 06:00:00.0000 TDB`。同じ数値を `TIME_TYPE='UT'` で送ると水星中心距離が 9.8 km 変わる）。**UTC の暦から作った JD をそのまま送ると 69.184 秒（TT−UTC）ずれる**ので `planetary_map._jd_from_utc()` で補正する（69 秒 × 6 km/s ≒ 400 km、最接近時刻も 69 秒ずれる。補正で ESA 公表値と 1 秒一致）。宇宙機の名前は `OBJ_DATA='YES'` の `Revised:` 行から取る（`MAKE_EPHEM='NO'` だけだと "No output possible for request." で名前が取れない） |
| フライバイ機の Horizons ID | `planetary_map.SPACECRAFT` に集約（Europa Clipper -159 / JUICE -28 / BepiColombo -121 / Hera -91 / Psyche -255 / Lucy -49 / New Horizons -98 / はやぶさ2 -37 / Juno -61 / Parker Solar Probe -96 / Solar Orbiter -144 / MMX -239。すべて Horizons に問い合わせて確認した） |
| IAU 自転モデル（ガリレオ衛星） | NAIF `pck00011.tpc` の `BODY50x_POLE_RA/POLE_DEC/PM`（実測で取得）を `BODIES` へ転記。極の歳差は赤経 -0.009°/世紀・赤緯 +0.003°/世紀、自転は同期回転（周期項は省略）。半径は平均半径（イオ 1821.5／エウロパ 1560.8／ガニメデ 2631.2／カリスト 2410.3 km。エウロパ 1560.8 は Nature Astronomy の Juno フライバイ論文と同じ値で、実測の最接近高度差 2.6 km の一因はこの丸め） |
| JPL CNEOS CAD（接近データ） | **既定の `neo=true` は小惑星だけを返す**（実測 2026-10-15: 彗星 169P が 0.17 au まで接近した 2026-08 の窓で彗星 0 件）。`nea-comet=true` も同じ 243 件で彗星を含まなかったため、彗星は **`comet=true` の別照会**で取り、`kind="all"` は2つの照会を日付順に統合する。彗星は `h`・`diameter` が null（直径は推定しない）。**`pha` は小惑星の区分**なので彗星の照会には付けない |
| NASA MMGIS / NASA Trek / Blue Marble | 位置データは火星ローバーのみ公開。天体地図は Trek WMTS（等角図法、`cols=2^(z+1)`, `rows=2^z`）。タイルはディスクキャッシュ対象。**Trek にフォボスの層は無い**（実測: 層名候補8種すべて 404）ので、フォボスは 1 枚の全球図を使う |
| IAU 自転モデル（天体固定座標への変換） | **標準式は `r_body = Rz(W)·Rx(90°−δ)·Rz(α+90°)·r_ICRF`**（IAU WGCCRE レポート／NAIF `pck00011.tpc` の α=POLE_RA・δ=POLE_DEC・W=PM。`Rz(θ)=[[c,s,0],[-s,c,0],[0,0,1]]`、`Rx(θ)=[[1,0,0],[0,c,s],[0,-s,c]]`）。**この式は物理で検算できる**: 同期回転衛星の木星直下の経度 ≈ 0°、月の地球直下の経度は光学秤動 ±7.9° の中、太陽直下と地球直下の角距離 = 位相角。旧実装 `Rz(W)·Ry(δ−90°)·Rz(α)` は**経度が +90.00° ずれていた**（実測 2026-10-24 の月・火星・水星・ガニメデで 経度差 +90.00°・緯度差 0.00°＝ずれは経度だけの定数）ため v0.41.7 で標準式に置換。検算値は Horizons の生ベクトルごと `tests/test_body_fixed_frame.py` に固定（月の地球直下 −5.63°／ガニメデの木星直下 −2.63°／Juno PJ34 の探査機 57.90°W・太陽 41.31°E = 論文公表値 56.8°W・318°W と 1° 以内） |
| USGS 公式全球等角図（フォボス等） | Commons 経由。**`upload.wikimedia.org` の直リンクは 429（レート制限）になる**（実測: 既存のタイタン・エウロパ図も同じ扱い）ので `commons.wikimedia.org/wiki/Special:FilePath/<file>?width=2400` を使う。配布 PNG は地形ラスタの外に白地・目盛ラベル・題字・クレジットがあるので `image_view(crop=…)` で**相対範囲**を切り出す（解像度非依存）。**フォボス図の印字目盛は西向き正だが地図自体は東向き正**（IAU 地名20点で実測）＝`left_edge_lon=-180`（横回転なし） |
| NASA NSSDCA「Apollo Landing Site Coordinates」 | 月面遺跡の LM / ALSEP / LRRR の座標。**planetocentric・Mean Earth/Polar Axis 座標系・DE421**（Wagner et al., Icarus 283 (2017)）。経度は**東向き正**。NSSDCA の一覧表で纬度・経度・半径をそのまま使う |
| NASA Apollo Lunar Surface Journal (ALSJ) | 月面兵材の**距離＋方位**の一次情報。旗（A11=8.2 m の Relation 記録／A12=約7 m・方位不確定／A15=11 m・方位約30°）と LRV 最終駐車地点（A15=165 m・102°／A16=81 m・88°／A17=158 m・94°・不確かさ約10 m）。**公開値は端点だけ**なので全経路を描かない。**距離のみ・方位なしの機材は図に描かない**（推測で埋めない） |
| IAU/U.S. Gazetteer（地名 feed） | `asc-planetarynames-data.s3.us-west-2.amazonaws.com/<TARGET>_nomenclature_center_pts.kmz`（zip 内 KML）。**`center_lon` は 0〜360 の東向き正**（Stickney=311 は 49°W・IAU Feature 5707 と一致）。種別は英字の `type` 文字列で判別する（"Landing Site" を含むものが着陸地点）。座標系は `GCS_<body>_2000` |
| 月面遺跡の図（局地サイトプラン） | 等角図法の天体面地図は 1px 数百 m〜20 km なので月面機材は 1 画素に収まらない＝**位置図（等角図法）と局地プラン（m 単位）の 2 段構え**にする。**凡例・見出しは図体と重ならない帯**に置く（重なると機材が「消えて」見える。実測: A12 の ALSEP が実際にその状態になった）。**Trek の月面タイルは 1 タイル 0.703125°** なので画角 0.05° だと地形が潰れる＝ズームは画角から決める。隣接する着陸地点（例: サーベイヤー3号とアポロ12号の LM は約 0.4 km）を含めるなら画角をその距離に合わせて広げる。**近傍の判定に自分自身を含めると画角が 0 に潰れる**ので必ず除外する |
| Sky & Telescope ほかRSS | Cloudflare がブラウザ偽装UAを弾く（curl/Wget系UAは許可）。`astronomy_news` は**複数フィードのフォールバック連鎖** |
| Open-Meteo / Nominatim | Open-Meteo は都市名（英語強め・ja→enエイリアス付き）。Nominatim は識別可能なUA必須（403 になるUAあり）。結果は 24時間キャッシュ。**hourly に太陽高度は無い**（`sun_elevation` は 400 「Cannot initialize … from invalid String value」）ので、薄明の判定は日付・経度・`utc_offset_seconds` から自前で解析計算する（`weather_astro.sun_altitude_deg`）。`daily.moon_phase` は**朔望月の進み具合**（0=新月, 0.5=満月）で照度ではない＝照度は `(1-cos(2πp))/2`（実測 2026-10-23: p=0.391 → 88.7%。timeanddate 88.6%）。`daily.moonrise/moonset` は**暦日単位**なので日没前の値を「今夜の月没」として使わない（東京 2026-10-23 の `moonset[0]` は 02:26、今夜の月没は翌日 03:30） |
| Wikidata SPARQL | 「初・記録」の回答は SPARQL の結果に基づく（記憶から捏造しない）。必ず Wikipedia 記事を引用する |
| WMO OSCAR | 衛星カタログは約1000件・取得が重い（実測50秒）→ 24時間キャッシュ |
| DLR EOC STAC | `POST /search` は `numberMatched` を返す。**`filter`（CQL2）は 400・`sortby` は無視**（実測）＝雲量の絞り込みは取得後にクライアント側で行い、`excluded_by_cloud` / `without_cloud_info` を分けて返す。ライセンスはコレクションごとの申告値（CC-BY-4.0 / CC-BY-NC-4.0 / proprietary 混在）なので利用条件を明示する |
| Roscosmos オープンデータ STAC | **静的 STAC**（`/search`・`/collections` は 404）。`catalog.json → コレクション → 年 → 月 → 日` を辿り日ノードの `rel=item` を列挙する（日ノードは親を辿らず直接 GET 可）。ホストは `s3.gptl.ru` / `s3ext.gptl.ru` の 2 つで、接続不可の側は記憶して切替。GeoTIFF の EPSG は `proj:epsg` が無ければアセットキー `image.tif.ms.4326._` から読む |
| ESO / ALMA / CADC / TART | 観測データ・大気コンディション。ESO/TART は短め（10分）、ALMA/CADC は30分のTTL |

## 画像の扱い

- 画像は base64 で `content` にインライン表示（JPEG/PNG）。**クライアントへ送る量**に注意: 実写合成は JPEG、座標図は PNG が適切。インライン枚数は既定で絞る（`inline_max`）。
- 画像の著作権・クレジット表記は各ソースの指示に従う（NASA素材は NASA Media Usage Guidelines）。
- **メディア本体より前にアイコン付きリンク**を出す（生成画像は `save_output()` で保存し `file:///` リンク＋`structuredContent.image_path`、検索系は各項目の直後に `🖼️/🎧/🎬 [◯を開く](URL)`）。検査は `--media-links`。

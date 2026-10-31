# 共通描画モジュール仕様 — 斜め俯瞰ビュー（試作 v0.1）

対象: `solar_system_now` / `comet_apparition` / `_render_orbit_plane` / `planetary_orbiter_track` など
「太陽の周りを回る」図を出すツール群。地表図（`ground_view`）・空図（`sky_overlay`）・
地球観測画像は対象外。

**確定した方針（2026-10-30 改訂）**: ①**弱い透視を入れる**（`Camera.dolly=4.2`。
カメラ距離＝4.2×画面半径。`dolly<=0` で正射影に戻せる） ②**対数動径は維持**
（0.3〜190 AU を 1 枚に収める現行の性質を守る。線形は近距離の拡大図だけに使う）。

試作実装: `C:\Users\oresama\hermes-figs\render_oblique_v5.py`（軌道）と `render_v6.py`（探査機経路）。
検証データ: ハレー・ヘールボップ・C/2014 UN271・ボリソフ・ボイジャー1/2・ニューホライズンズ・MMX。

---

## 0. 方針（変えるもの／変えないもの）

- ~~**置き換えない**。現在の `_render_simple`（Pillow・真上・対数動径）はそのまま残し、
  **投影だけを差し替え可能**にする。`engine=` と同じ流儀で `view="top"(既定) | "oblique"` を足す。~~
  **→ Phase 4（下記 §9）で方針を変更**: 既定を oblique にしたうえで、`_render_simple` /
  `_render_accurate` / `_render_orbit_plane` を `_render_oblique` へ統合し、3 関数を廃止した。
- 幾何（`_compute` の scene 構築）と検証（`_verify_*`）は `solar_system.py` に残す。
  新しい共通モジュールは「3D(AU) → 画面(px)」の写像と描画部品だけを持つ。
- 新モジュール: `src/space_finder_mcp/orbit_view.py`。

## 1. 現行実装との差分（実測値）

| 項目 | 現行 `_render_simple` | 斜め俯瞰 |
|---|---|---|
| 画面 | 1500×1500、`Image.new("RGB", …)` | 同寸で可（試作は 1280²） |
| 半径写像 | `r_px = 620·(log10 r − log10 lo)/(log10 hi − log10 lo)`、`lo=0.30` | 同じ式を**3D ベクトルのノルムに適用**してから投影 |
| 視点 | 真上（黄道面を北から） | 仰角 34°・方位 28°（既定案） |
| ラベル配置 | `_place_rect` が円環上を走査 | 円環が**楕円**になるので走査半径を方向依存にする（§4） |
| 奥行き | 無し | `depth` で不透明度・線幅を連続変化 |
| 検証 | `_verify_range_draw` 等が画素で位置照合 | 同じ関数が使える（期待 px を `project()` で出す） |

斜めにすると **画面上の距離は AU に比例しない**。ボイジャー1号（172 AU）が
ニューホライズンズ（66 AU）より中心寄りに見えることがある（実測）。凡例に実距離を必ず併記する。

## 2. データモデル

```python
@dataclass(frozen=True)
class Camera:
    elevation_deg: float = 34.0     # 黄道面からの仰角。35.264°(=arcsin 1/√3)+方位45° で等角投影
    azimuth_deg: float = 28.0      # 黄道面内の回転
    dolly: float = 4.2              # カメラ距離／画面半径。0 以下で正射影（検証用）

@dataclass(frozen=True)
class RadialScale:
    kind: str = "log"               # "log" | "linear"
    lo_au: float = 0.30             # log のときの下限（現行と同じ）
    hi_au: float = 190.0            # 上限。range_au 指定時は hi を固定し lo=min(lo, hi*0.05)
    r_px: float = 620.0             # 画面半径（現行 R0 と同義）
```

**カメラの公開（v0.47.0）**: `solar_system_now(elevation=, azimuth=, dolly=)` が
`scene["oblique_elevation" | "oblique_azimuth" | "oblique_dolly"]` に入れ、
`solar_system.oblique_camera(scene)` が**唯一の取得口**になる。範囲は仰角 **0–90**（既定 34）／
方位 **-360–360**（内部で 0–360 に正規化、既定 28）／dolly **0–20**（既定 4.2）。
範囲外・非数値は**クランプせずエラー結果**（`elevation=120` → 「0〜90 の範囲で指定してください」、
`dolly="abc"` → 「数値で指定してください」）。描画・題名・`figure.view.description`・
`structuredContent.camera` は**すべてこの関数から取る**（定数 `OBLIQUE_EL` の直書き禁止＝
片方だけ既定値で残ると、指定した角度と注記の角度が食い違う）。

写像（すべて AU 入力・px 出力）:

```
ρ(r) = r_px·(log10 r − log10 lo)/(log10 hi − log10 lo)      # kind="log"
ρ(r) = r_px·r/hi                                            # kind="linear"
p′   = p·ρ(|p|)/|p|                                         # 動径圧縮（3D のまま）
xr = p′x·cosAZ − p′y·sinAZ
yr = p′x·sinAZ + p′y·cosAZ
depth = p′z·sinEL − yr·cosEL                                # + が手前
f  = D/(D − depth)    where D = dolly·r_px                   # 弱い透視（dolly≤0 なら f=1）
px = CX + f·xr
py = CY − f·(yr·sinEL + p′z·cosEL)
```

- `depth` は AU ではなく **px 相当の長さ**で返す（`±0.6·r_px` 程度のレンジ）。
- 不透明度 `α = 0.26 + 0.74·t`、線幅 `w = w0·(0.62 + 0.62·t)`、`t = (depth − dmin)/(dmax − dmin)`。
  **`dmin/dmax` は固定値にしない**（枠が 35,730 AU になると全点が同深度になる）。
- `e≥1` の双曲線は `r = q(1+e)/(1+e·cosν)`、`ν_max = acos(−1/e) − 0.4°`。
- サンプリングは**適応二分細分**（サジッタ ≤ 0.3 px）。一律 720 点だと
  C/2014 UN271 を 35,730 AU 枠で描いたとき最大 27 px の折れが出る（実測）。

## 3. 描画要素（レイヤ）

| 要素 | シグネチャ（案） | 備考 |
|---|---|---|
| 軌道 | `draw_orbit(dr, el, cam, scale, color, kind)` | `kind="planet"/"tno"/"comet"` で太さ・不透明度を変える |
| 経路 | `draw_track(dr, pts, color, arrows=3, events=())` | 探査機。矢印は 1/4・1/2・3/4 点に |
| マーカー | `draw_marker(dr, pos, color, style="cross+ring")` | 白点＋色リング＋破線ハロー＋十字。星と区別するため必須 |
| ラベル | 既存 `_place_rect` を拡張 | §4 |
| 凡例 | `draw_legend(...)` | 色見本＋名前＋ `i/e/q/Q/P`。逆行は「／逆行」 |
| 付帯 | `draw_scalebar/credits` | スケールバーは枠/5。0.1 AU 未満は万 km |

軌道と経路の色は既存パレットを流用し、**惑星=淡青／TNO=淡金／彗星=青緑**を基本にする。

## 4. ラベル配置（`_place_rect` の拡張）

現行は「中心から半径 `r_px+18+h/2` の円環を 6° 刻みで走査し、占有矩形との重なりが最小の角度を選ぶ」。
斜め投影では円環が楕円になるため、**走査式を `project()` に置き換える**:

```
for θ in range(0, 360, 6):
    cand_au = (cos θ, sin θ, 0) を r_ring でスケールした 3D 点
    px, py = project(cand_au)          # 楕円環になる
```

- `r_ring` は対象の軌道半径（対数なら `ρ(|p|)`）から取る。
- 追加ルール: **中心から 95 px 以内の遭遇ラベルは省略**（実測で密集して読めなくなる）。
- 既存の「全ラベル箱を描いた後にマーカーを描き直す」二段構えは**そのまま維持**（v0.39.4 の回帰）。

## 5. 各ツールとの接続

| ツール | 入力 | 推奨 `RadialScale` | 備考 |
|---|---|---|---|
| `solar_system_now` | 惑星＋探査機＋彗星＋小惑星 | `log`, `hi=auto`（現行同じ自動拡張） | 既定 oblique（Phase 3/4 で切替済み）。**カメラは `elevation`/`azimuth`/`dolly` で公開**（v0.47.0） |
| `_render_orbit_plane`（彗星・小惑星） | 1天体＋現在位置 | `linear`, `hi=fit` | 二重円錐の全景。`_verify_range_draw` を流用 |
| `comet_apparition` | 彗星の日付列 | 変更なし | 別種の図（光度曲線） |
| `planetary_orbiter_track` | 天体中心・km | `linear`, km→AU 換算 | Phase 3 以降 |
| `solar_system_now(probe=…, route=True)` | Horizons 点列 | `log` | 破線経路＋最接近◇。刻みは §7(l) |

互換性のため、`range_au` / `engine` / `route` / `probes` の意味は変えない。
`projection` は**互換引数のみ**（`"top"` を渡しても斜め俯瞰で描き、その旨を `figure.notes` に出す）。
視点は `elevation` / `azimuth` / `dolly` で指定する（v0.47.0）。

## 6. 性能（実測）

- 幾何＋SVG 生成: **0.07 s**（9天体・190 AU 線形）／**0.13 s**（12天体・log）
- ラスタライズ: **0.6〜0.9 s**（1280²・Chrome headless）。Pillow 直描きならこの分は不要
- PNG: **820〜930 KB**（1280²、星 2,800〜3,200 個）
- 参考: 現行 `solar_system_now` は 2.9 s のうち 2.3 s がマーカーごとの全画面ブラー（skill 実測）

→ Pillow に移植する場合も、**深度ごとに線を分割して `ImageDraw.line` を呼ぶ**実装なら
1 図あたり 1,000〜3,000 セグメントで、描画は 0.1 s 台に収まる見込み。

## 7. 落とし穴（すべて実測で踏んだもの）

0. **`depth` の符号を逆にすると「奥が明るい」図になる**（試作で実際に踏んだ）。カメラ方向の
   単位ベクトルは `v = (−sinAZ·cosEL, −cosAZ·cosEL, sinEL)` で、`depth = p·v`。
   `yr·cosEL − z·sinEL` と書くと符号が逆になり、奥の弧が手前より太く明るく描かれる（気づきにくい）。
   真上視点（EL=90°）で `+z` が手前になることを 1 本テストで固定する。
1. **`e≥1` で `a` が負・`per`/`ad` が `None`** → `normalize_orbit_el` で `float(None)`。双曲線は `q` と `e` だけで描く。
2. **0〜360° の閉曲線サンプリングは双曲線で破綻**（r が負・発散）。
3. **深度の正規化を固定値にしない**（枠 35,730 AU で奥行きが消える）。
4. **一律サンプル数では極端な e で折れる**（適応細分にする）。
5. **楕円用 Kepler 反復は e>1 で発散**（`e·sinh H − H = M`、初期値 `asinh(M/e)`）。SBDB の `n` は
   双曲線では 1 AU 基準なので、**現在位置は Horizons から取る**（ハレーでも 40 年伝播で 0.4% ずれる）。
6. **逆行（i>90°）は形では分からない** → 進行方向の矢印＋凡例「／逆行」。
7. **枠外は黙って切らない** → 凡例行に「（枠外へ続く）」。
8. **斜め投影では画面距離 ≠ AU** → 凡例に実距離。
9. **対数動径は形状が非等角**（楕円が楕円でなくなる）→ 図中に「形状比較用ではない」と明記。
10. **中心部のラベル密集** → 95 px 以内は省略。
11. **MMX は Horizons に 2026-10-19〜2026-12-02 しか無い**。打ち上げ直後は実データで描けるが、
    火星到着（2027）以降は描けない（架空の経路を描かない）。
12. **探査機の刻みは遭遇期間で別に取る**。40 日刻みだと木星近傍の点間隔が 0.558 AU
    （枠 12 AU で 22 px）でスイングバイが多角形になる。6 日刻みなら 0.085 AU（3.3 px）。

## 7.6 実装状況（Phase 2 完了・当時の記録）

- `solar_system_now(..., projection="top"|"oblique")` を追加。`oblique` は `_render_oblique(scene)` で描き、
  `_verify_oblique(scene, png)` が **描画と同じ `orbit_view.View`** から期待 px を出してマーカー（白点）の
  有無を画素で確かめる（`figure.verify.method = "oblique_expected_px+marker_core"`）。
- `figure.view.projection` は `"oblique"`、`figure.notes` に「奥行きで太さ・明度が変わる／画面上の距離は
  AU に比例しない」を自動で入れる。`structuredContent.projection` も返す。
- 実測（2026-10-30 03:00 UTC・惑星のみ）: 9 天体すべて白点を検出して `verify.ok=true`。
  探査機（ボイジャー1号 172.20 AU）を足した場合も 10 天体で `ok=true`。
- 未対応（Phase 3 以降）: 1光日リング・彗星の尾・小惑星帯の帯は oblique では描いていない
  （`figure.notes` の既存文と矛盾しないよう、oblique では該当する注記を出さない扱いにする）。
- テスト: `tests/test_oblique_render.py` 7 件（描画＋画素検証＋「描いていない天体を足すと
  verify が落ちる」＋範囲固定＋経路＋尾の向き）。`unittest discover` 全 511 件 OK、`--dead-code` 0 件、
  `--figures` 43 経路すべて `verify_ok=True`（`solar_system_now` の全ケースが既定 oblique で通過）。
- 既存の回帰テストのうち simple 固有の画素検証（経路の破線・720 分割）を見る 3 件は
  `projection="top"` を明示する形に変更した（simple 経路の検証はそのまま残す）。
  **※ Phase 4 で `_render_simple` 自体を廃止したため、この記述は当時のもの。現在 `projection="top"` は
  斜め俯瞰を描く（真上図が欲しいときは `elevation=90`）。**
- `_render_oblique` は `scene["range_report"]`（`lo_au`/`hi_au`/`skipped_orbits`/`out_of_range`。
  経路の目印が範囲外なら `type="route_mark"`）を simple 版と同じ形で埋める。

## 7.5 実装状況（Phase 1 完了）

- `src/space_finder_mcp/orbit_view.py` を追加（**既存描画は未接続＝出力は不変**）。
  含むもの: `Camera` / `RadialScale` / `View`（project・ring_point・orbit_ring_px）/
  `conic_kind`・`conic_radius`・`nu_limit_deg` / `orbit_segments`（適応細分・双曲線対応）/
  `depth_range`・`depth_fraction`・`depth_alpha`・`depth_width` /
  `draw_orbit`・`draw_marker` / `expected_px`（verify 用）。
- `tests/test_orbit_view.py` 15 件（投影の解析値・log 単調性・q/Q 一致・双曲線の開枝・
  サジッタ・深度の向き・マーカー画素）。`unittest discover` 全 502 件 OK、`--dead-code` 0 件。
- 探査機経路（`draw_track`）・スケールバー・凡例は **Phase 2 で `solar_system` に接続する際**に追加する
  （未接続のまま置くと `--dead-code` が落ちるため。試作は `hermes-figs/render_v6.py`）。

## 8. 移行計画（各段でゲート）

- **Phase 1 — 部品だけ足す**（既存出力は不変）
  `orbit_view.py`（Camera / RadialScale / project / draw_*）＋ `tests/test_orbit_view.py`。
  投影は既知ベクトルの解析値で assert。`--figures` は触らない。
- **Phase 2 — 選択肢として公開**
  `solar_system_now(view="oblique")` と `_render_orbit_plane(view=…)`。
  `scripts/check-tools.py` に `view="oblique"` のケースを 2 件追加（ハレー、ボイジャー）。
  既定は `top` のまま。→ **完了（2026-10-30、既定 oblique へ切替済み）**
- **Phase 3 — 既定を切り替える**（要ユーザー判断）
  既定 `oblique` にし、`top` を残す。README / SKILL.md / CLAUDE.md のファイル構成に
  `orbit_view.py` を追記（**同一コミット**）。→ **完了（2026-10-30）**。`top` は Phase 4 で
  「斜め俯瞰を描く互換引数」になった（`elevation=90` が実質の真上図）。

## 9. テスト計画

- 投影: `(1,0,0)`・`(0,1,0)`・`(0,0,1)`・`(0,0,0)` の px を解析値で assert。log の単調性、depth の符号、正射影/透視の一致。
- 円錐曲線: `q`/`Q` が距離の最小/最大に一致（±0.1%）。サジッタ ≤ 0.3 px。`e≥1` で開曲線（端点が無限遠側で切れる）。
- 画素プローブ: マーカー色クラスタの重心 vs `project()` の期待 px（±3 px）。奥行きの順序（手前が後ろを覆う）。
- 回帰: 既存 `unittest` 411 件、`--figures`（追加 2 件）、`--offline`、`--media-links`。
  期待 px が変わる既存ケースは `top` のまま通ることを先に確認する。

## 10. 今回の範囲外

- **火星中心・km 単位**（フォボス 9,376 km／ダイモス 23,463 km、MMX の周回フェーズ）。
  AU スケールではフォボス軌道は `6.3e-5 AU` でサブピクセル。`RadialScale(kind="linear", unit="km")`
  と中心天体の切り替えが別途必要。
- 地表図・空図・地球観測画像（既存の別系統）。

## 11. 決定事項と残る確認

- 決定: **弱い透視あり（dolly=4.2）** / **対数動径を維持**。
- 実装上の注意: 引数名は `projection`（`view` は既存の表示モード
  `system`/`comet_orbit`/`apparition` が使っているため）。
- 決定: **既定 `projection="oblique"` に切替済み**（2026-10-30。`engine="accurate"` 明示時は accurate 優先）。
  **`projection="top"` は Phase 4 で真上図を廃止した**ので、現在は指定しても斜め俯瞰で描き、
  その旨を `figure.notes` に出す。真上図が欲しいときは **`elevation=90`** を使う（v0.47.0）。
- `engine="accurate"`（matplotlib・線形距離）を明示したときは **accurate を優先**し、斜め俯瞰は
  使わない（勝手に描画方式を変えない）。その旨を `figure.notes` に自動で出す。
  `engine="auto"` 等で accurate が自動降格した場合は oblique のまま描く。


---

## 9. Phase 4 — 統合と廃止（2026-10-31 決定・実装済み）

ユーザー評価の高かった 2 つの図（`sky_map_with_satellites` の地上視点パノラマと、
`solar_system_now` の斜め俯瞰）に**描画ルーチンを一本化**した。並列に持っていた実装は廃止する。
「引数で選ばせる」から「1 本の描画に統合し、引数は互換のためだけに残す」への転換。

| 廃止した関数 | 統合先 | 引数の扱い |
|---|---|---|
| `sky_overlay._render_simple`（`engine="simple"`） | `ground_view.render` | `engine` は互換（どの値でも ground） |
| `sky_overlay._render_accurate`（`engine="accurate"`） | `ground_view.render` | 同上 |
| `solar_system._render_simple`（`projection="top"`） | `solar_system._render_oblique` | `projection` は互換（`top` でも斜め俯瞰で描き、注記に出す） |
| `solar_system._render_accurate`（`engine="accurate"`） | `solar_system._render_oblique` | `engine="accurate"` は**線形縮尺**を選ぶ |
| `solar_system._render_orbit_plane`（`view="comet_orbit"`） | `solar_system._render_oblique(body=…)` | `view="comet_orbit"` は従来どおり |
| `orbit_view.draw_orbit` | `solar_system._obl_draw_orbit` | 呼び出し側は変更なし（内部） |

### 決定事項

1. **軌道面図（`view="comet_orbit"`）の構図は変えない。** 「真横から見た楕円＋太陽＝焦点」は
   形を読むための図で、斜め俯瞰へ投影し直すと楕円が潰れて離心率・焦点が読めなくなる
   （実測: Q/q 3.32 → 2.26、楕円の縦横比 1.11 → 0.58）。統合するのは**描画コード**で、
   投影は「軌道面を画面に一致させた View（仰角 90°・線形動径・透視なし）」を使う。
   これで `orbit_segments` / 名札配置 / 図の契約（`figure.kind="orbit_plane"`・`verify_curve`）を
   俯瞰図と共有できる。
2. **`engine="accurate"` は「線形縮尺」として残す。** 旧 matplotlib 版の価値は「相対距離が正確」
   という情報で、見た目ではない。`RadialScale(kind="linear")` で同じ情報を斜め俯瞰で出し、
   内惑星が中心に密集する分は**名札リングの下限（150 px）＋引き出し線**で補う。
3. **`projection="top"` は廃止。** 指定されたら斜め俯瞰で描き、「統合したので真上図ではない」旨を
   `figure.notes` に自動生成する（黙って無視しない）。
4. **マーカーは 3D の実位置**（黄経・黄緯）に置く。旧 simple/accurate は黄道面への正射影だったが、
   斜め俯瞰は 3D をそのまま投影するので、経路（Horizons の 3D 点列）とマーカーが同じ座標系になる。
5. **matplotlib への依存を削除**（`RENDER_LOCK` / `apply_matplotlib_cjk_font` / `rgb_hex`、
   `pyproject.toml` の `matplotlib`）。描画は Pillow のみになり、直列化ロックは不要。

### 統合で見つかって直した不具合（実測）

- 対数縮尺の「太陽円盤 vs 最内惑星」検証が、px で決めた円盤半径を `au_at_px` で逆算していたため
  縮尺依存で誤判定（実測: `range_au=10` で 0.314 AU vs 0.335 AU → `ok=False`）。判定を **px 空間**へ。
- `_verify_oblique` の可視判定が `hi*1.2` まで許容していたため、`range_au=5` の木星（5.32 AU）を
  「描いていないのに探す」誤検出。描画と同じ `_oblique_visible(view, au)` に統一。
- 彗星の尾が自分のラベル箱に隠れる問題（v0.39.6 の回帰）は、**全ラベル箱の後に尾を描き直す**
  最終パスを統合先でも維持して防いでいる。
- 旧 `_render_orbit_plane` に無かった太陽の光暈を追加したところ、近日点側の曲線色が薄まり
  `verify_curve` の測定値が 0.57 → 2.77 AU に跳ねた（`ok=False`）。**軌道面図には光暈を描かない**。

---

## 12. Phase 5 — カメラの引数化（v0.47.0・実装済み）

**目的**: 視点を**呼び出し側の変数**にする。固定の 1 枚は「宇宙は平面」「軌道は同じ面」という
誤概念を作るため、同じ瞬間を真上・斜め・真横で出せることを仕様に含める（教育用途の要件）。

| 引数 | 範囲 | 既定 | 効果 |
|:--|:--|--:|:--|
| `elevation` | 0–90 | 34 | 0=真横（軌道が線に潰れる）／90=真上（旧 真上図の構図） |
| `azimuth` | -360–360 | 28 | 黄道面内の回転（内部で 0–360 に正規化） |
| `dolly` | 0–20 | 4.2 | 透視の強さ＝カメラ距離（画面半径の倍数）。0 で完全な正射影 |

- 実装: `solar_system.oblique_camera(scene)` が唯一の取得口。`_oblique_view` / 題名 / 互換注記 /
  `view_spec.description` / `structuredContent.camera` の 5 か所がここを通る。
  検証は `solar_system._split_camera_arg()`（非数値・範囲外はエラー結果）。
- 実測: `{"elevation":74,"azimuth":210,"dolly":1.5}` → `camera` が同値で返り `verify.ok=True`。
  `elevation=120` → `elevation は 0〜90 の範囲で指定してください（受け取った値: 200）`。
  既定 vs `elevation=90` の画素差 `diff_bbox=(169,23,1407,1322)`・平均絶対差 3.94/255。
  dolly=0 では反対側の点が中心対称（正射影）／dolly=4.2 では非対称（透視）。
- テスト: `tests/test_camera_args.py` 9 件。全体 555 件 OK。ゲート 8 本 exit 0。
- **未対応（次の Phase）**: `planetary_orbiter_track`（月・衛星・周回機の天体中心図）は視点固定。
  衛星軌道の傾斜（木星の衛星で 0.0°〜164.9°）を見せる主戦場はこちらなので、同じ引数を
  天体中心スケール（km）に載せ替える必要がある（§10 の「火星中心・km」と同じ課題）。
  → **解消（v0.48.0）**: `planetary_orbiter_track(mode="orbit3d", elevation=, azimuth=, dolly=)`
  として実装した（`src/space_finder_mcp/orbit3d.py`）。`View` を**線形・km**で使い、投影の
  実装は 1 本のまま。傾斜角は Horizons の接触軌道要素（`REF_PLANE=BODY EQUATOR` の IN）を
  使う（天体固定の点列フィットは自転で数度ずれる。実測: LRO で −1.2°、MRO で −0.02°）。

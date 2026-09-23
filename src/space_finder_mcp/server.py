"""FastMCP サーバー定義。各カテゴリのツールを登録する。

認証方針:
- Launch Library 2 (打ち上げ) / NASA Image & Video Library / Wikidata はキー不要。
- NASA (APOD/NEO) は api.nasa.gov のキーが別途必要。環境変数 NASA_API_KEY
  があれば引数省略時に自動で使う。キーはサーバー側でのみ保持し、クライアントへ晒さない。
"""
import functools
from typing import Optional

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from .wikidata_lookup import reverse_lookup
from . import nasa as _nasa
from . import nasa_budget as _nasa_budget
from . import launch as _launch
from . import media as _media
from . import isro as _isro
from . import copernicus as _copernicus
from . import jaxa as _jaxa
from . import csa as _csa
from . import inpe as _inpe
from . import celestrak as _celestrak
from . import uk_datahub as _uk
from . import cnes as _cnes
from . import weather_astro as _weather
from . import eodashboard as _eodash
from . import donki as _donki
from . import stac_search as _stac
from . import iss as _iss
from . import oscar as _oscar
from . import cnsa as _cnsa
from . import tiangong as _tiangong
from . import power as _power
from . import eso as _eso
from . import cadc as _cadc
from . import mast as _mast
from . import gcn as _gcn
from . import ssd as _ssd
from . import skyfield_pos as _sky
from . import news as _news
from . import mars_rover as _mars
from . import sky_overlay as _skyover
from . import alma as _alma
from . import tart as _tart
from . import solar_eclipse as _eclipse
from . import moon_phase as _moon_phase
from . import solar_system as _solarsys
from . import planetary_rover as _prover
from . import planetary_map as _pmap
from . import satellite_map as _satmap
from . import weather_sat as _wsat
from . import space_calendar as _calendar
from . import literature as _lit

# ---- ネイティブ拡張は「起動前に」import しておく（重要） ----
# numpy / matplotlib / skyfield を**ツール実行時**（イベントループが動き出した後）に import
# すると、この環境では import が完了せずツール呼び出しが無応答になる（実測: Windows +
# Python 3.11 + mcp の stdio。numpy・matplotlib.pyplot・skyfield.api が HANG、PIL.Image・
# sgp4・requests は問題なし。numpy を起動時に1回 import しておけば、その後の
# matplotlib/skyfield の遅延 import も通る）。起動コストは約 0.1 秒。
try:
    import numpy  # noqa: F401  （skyfield/matplotlib が依存。事前 import が必須）
except Exception:  # pragma: no cover - numpy が無い環境でもサーバー自体は起動させる
    pass

mcp = FastMCP("Space Finder MCP")


def _threaded(fn):
    """同期ツールを「ワーカースレッドで実行する async ラッパー」に変換する。

    mcp の FastMCP は**同期関数をイベントループ上でそのまま呼ぶ**（`func_metadata.
    call_fn_with_arg_validation` の `return fn(**kwargs)`）。そのため1つのツールが
    ネットワーク待ちや描画でブロックしている間、LLM が並行に投げた他のツール呼び出しは
    1つも動き出せない（実測: 同じツールを4並列で呼ぶと wall = 各呼び出しの合計）。
    anyio のワーカースレッドへ逃がすと、MCPサーバー自体は `tg.start_soon` で
    メッセージごとにタスクを作っているので、そのまま本当に並行実行される。

    ゲート（`scripts/check-tools.py`）は同期関数を直接呼ぶ必要があるため、
    元の関数を `wrapper.sync_fn` に残す。引数・docstring は `functools.wraps` で
    そのまま引き継がれ、ツールのスキーマは元の関数から生成される。
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))

    wrapper.sync_fn = fn
    return wrapper


def _reg(fn, name: Optional[str] = None):
    """ツールを登録する（同期ツールは自動的にワーカースレッド実行になる）。"""
    wrapped = _threaded(fn)
    if name:
        mcp.tool(name=name)(wrapped)
    else:
        mcp.tool()(wrapped)
    return wrapped


# ---- 逆引き歴史Q&A (Wikidata SPARQL) ----
_reg(reverse_lookup)


# ---- ロケット打ち上げ (Launch Library 2, 認証なし) ----
_reg(_launch.upcoming_launches)
_reg(_launch.china_launches)
_reg(_launch.russia_launches)

# ---- 天宮 中国宇宙ステーション位置 (CelesTrak TLE + SGP4, 認証不要) ----
_reg(_tiangong.tiangong_now)


# ---- NASA POWER 気候・太陽エネルギー (認証不要) ----
_reg(_power.power_climate)


# ---- 天文観測データ (ESO パラナル / CADC カナダ) ----
_reg(_eso.eso_seeing)
_reg(_cadc.cadc_observations)
_reg(_mast.mast_observations)
_reg(_gcn.gcn_alerts)

# ---- 電波天文 (ALMA Science Archive / TART, 認証不要) ----
_reg(_alma.alma_search)
_reg(_tart.radio_sources_now)

# ---- 学術文献（惑星科学の根拠: OpenAlex / Crossref / NTRS / JAXAリポジトリ / J-STAGE / CiNii） ----
_reg(_lit.space_literature_search)
_reg(_lit.planetary_evidence)

# ---- 星空マップ＋人工衛星オーバーレイ (matplotlib/Pillow 選択式, 認証不要) ----
_reg(_skyover.sky_map_with_satellites)

# ---- 太陽系俯瞰図（太陽中心の惑星・小惑星位置合成, 認証不要） ----
_reg(_solarsys.solar_system_now)

# ---- 日食時系列パネル（太陽を月が欠く過程, JPL DE421+Skyfield, 認証不要） ----
_reg(_eclipse.solar_eclipse_series)


# ---- 月齢マップ（月の満ち欠けを格子/朔望月パネルで描く, JPL DE421+Skyfield, 認証不要） ----
_reg(_moon_phase.moon_phase_map)


# ---- 天体位置・星座 (Skyfield, 認証不要・ローカル計算) ----
_reg(_sky.constellation_now)


# ---- 天文ニュース (Sky & Telescope RSS, ブラウザUAでWAF回避) ----
_reg(_news.astronomy_news)


# ---- 火星探査ローバー状況 (Mars Weather, 認証不要) ----
_reg(_mars.mars_rover_status)

# ---- NASA (APIキー要。環境変数 NASA_API_KEY があれば使う) ----
def _nasa_apod(date: Optional[str] = None) -> CallToolResult:
    """今日（または指定日）の NASA の今日の天文写真(APOD)を返す。NASA_API_KEY が必要。"""
    return _nasa.apod(_nasa_budget.current_key(), date)

def _nasa_neo_today() -> CallToolResult:
    """今日地球に接近する小惑星(NEO)を返す。NASA_API_KEY が必要。"""
    return _nasa.neo_today(_nasa_budget.current_key())

_reg(_nasa_apod, name="apod")
_reg(_nasa_neo_today, name="neo_today")

# ---- NASA Image & Video Library (画像/音声/動画, 認証不要) ----
_reg(_media.search_space_images)
_reg(_media.search_space_audio)
_reg(_media.search_space_videos)


# ---- 他国の宇宙機関 (ISRO/ESA-Copernicus/JAXA/CSA) ----
# ISRO (インド, 認証不要)
_reg(_isro.isro_data)
# ESA Copernicus (欧州, 検索は認証不要。ダウンロードは OAuth2 環境変数)
_reg(_copernicus.copernicus_collections)
_reg(_copernicus.copernicus_search)
# JAXA Earth (日本, 認証不要)
_reg(_jaxa.jaxa_datasets)
_reg(_jaxa.jaxa_dataset_search)
# CSA (カナダ, 認証不要)
_reg(_csa.csa_dataset_search)


# ---- さらに調査で確認した他国の宇宙機関 ----
# ブラジル INPE (BDC STAC, 認証不要)
_reg(_inpe.inpe_collections)
_reg(_inpe.inpe_search)
# CelesTrak (全世界の衛星軌道要素TLE, 認証不要)
_reg(_celestrak.sat_tle)
# 英国 EO DataHub (STAC, 公開カタログは認証不要)
_reg(_uk.uk_stac_search)
_reg(_uk.uk_stac_collections)
# フランス CNES (THEIA/GEODES ポータル到達確認)
_reg(_cnes.cnes_status)


# ---- 天体観測用天気 (Open-Meteo, 認証不要) ----
_reg(_weather.astronomy_weather)


# ---- EO Dashboard (NASA/ESA/JAXA 共同地球観測カタログ, 認証不要) ----
_reg(_eodash.eodashboard_collections)
_reg(_eodash.eodashboard_detail)


# ---- 宇宙天気 (NASA DONKI, キーは NASA_API_KEY 任意) ----
_reg(_donki.space_weather)


# ---- 天体異常系 (JPL SSD/CNEOS: 火球・小惑星接近・衝突リスク, 認証不要・APIキー不要) ----
# api.nasa.gov のキー枠（DEMO_KEY 30 req/h/IP）を消費しないため、apod / neo_today /
# space_weather の枠争いに影響しない。
_reg(_ssd.fireball_reports)
_reg(_ssd.neo_close_approach)
_reg(_ssd.impact_risk)


# ---- AWS Earth Search STAC (Sentinel/Landsat/NAIP, 認証不要) ----
_reg(_stac.stac_collections)
_reg(_stac.stac_search)


# ---- ISS 現在位置 (Open Notify, 認証不要) ----
_reg(_iss.iss_now)

# ---- 任意衛星の地上軌道マップ（CelesTrak TLE + SGP4 + Blue Marble, 認証不要）----
_reg(_satmap.sat_ground_track)


# ---- 世界の気象観測衛星 リアルタイム実画像（ひまわり9号, NICT/Kochi ミラー, 認証不要）----
_reg(_wsat.weather_satellite_now_extended, name="weather_satellite_now")


# ---- 汎用・天体周回機マップ（JPL Horizons + IAU自転 + NASA Trek, 認証不要）----
_reg(_pmap.planetary_orbiter_track)

# ---- 汎用・天体面ローバー位置マップ（MMGIS + NASA Trek, 認証不要）----
_reg(_prover.planetary_rover_location_map)


# ---- WMO OSCAR 衛星カタログ (認証不要) ----
_reg(_oscar.satellite_status)


# ---- 中国 CNSA 系衛星データポータル (NSMC/CNSA-GEO/CRESDA, 到達性+概要) ----
_reg(_cnsa.cnsa_status)


# ---- 宇宙・天文イベントカレンダー（LL2 + Skyfield + 国立天文台 + ローカル予定, 認証不要）----
_reg(_calendar.space_calendar)
_reg(_calendar.calendar_events)
_reg(_calendar.calendar_event_add)
_reg(_calendar.calendar_event_remove)

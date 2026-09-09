"""FastMCP サーバー定義。各カテゴリのツールを登録する。

認証方針:
- Launch Library 2 (打ち上げ) / NASA Image & Video Library / Wikidata はキー不要。
- NASA (APOD/NEO) は api.nasa.gov のキーが別途必要。環境変数 NASA_API_KEY
  があれば引数省略時に自動で使う。キーはサーバー側でのみ保持し、クライアントへ晒さない。
"""
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .wikidata_lookup import reverse_lookup
from . import nasa as _nasa
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
from . import skyfield_pos as _sky
from . import news as _news
from . import mars_rover as _mars
from . import sky_overlay as _skyover
from . import alma as _alma
from . import tart as _tart
from . import solar_eclipse as _eclipse
from . import solar_system as _solarsys

mcp = FastMCP("Space Finder MCP")

# ---- 逆引き歴史Q&A (Wikidata SPARQL) ----
mcp.tool()(reverse_lookup)


# ---- ロケット打ち上げ (Launch Library 2, 認証なし) ----
mcp.tool()(_launch.upcoming_launches)
mcp.tool()(_launch.china_launches)
mcp.tool()(_launch.russia_launches)

# ---- 天宮 中国宇宙ステーション位置 (CelesTrak TLE + SGP4, 認証不要) ----
mcp.tool()(_tiangong.tiangong_now)


# ---- NASA POWER 気候・太陽エネルギー (認証不要) ----
mcp.tool()(_power.power_climate)


# ---- 天文観測データ (ESO パラナル / CADC カナダ) ----
mcp.tool()(_eso.eso_seeing)
mcp.tool()(_cadc.cadc_observations)

# ---- 電波天文 (ALMA Science Archive / TART, 認証不要) ----
mcp.tool()(_alma.alma_search)
mcp.tool()(_tart.radio_sources_now)

# ---- 星空マップ＋人工衛星オーバーレイ (matplotlib/Pillow 選択式, 認証不要) ----
mcp.tool()(_skyover.sky_map_with_satellites)

# ---- 太陽系俯瞰図（太陽中心の惑星・小惑星位置合成, 認証不要） ----
mcp.tool()(_solarsys.solar_system_now)

# ---- 日食時系列パネル（太陽を月が欠く過程, JPL DE421+Skyfield, 認証不要） ----
mcp.tool()(_eclipse.solar_eclipse_series)


# ---- 天体位置・星座 (Skyfield, 認証不要・ローカル計算) ----
mcp.tool()(_sky.constellation_now)


# ---- 天文ニュース (Sky & Telescope RSS, ブラウザUAでWAF回避) ----
mcp.tool()(_news.astronomy_news)


# ---- 火星探査ローバー状況 (Mars Weather, 認証不要) ----
mcp.tool()(_mars.mars_rover_status)
mcp.tool()(_mars.mars_rover_location_map)

# ---- NASA (APIキー要。環境変数 NASA_API_KEY があれば使う) ----
def _nasa_apod(date: Optional[str] = None) -> str:
    """今日（または指定日）の NASA の今日の天文写真(APOD)を返す。NASA_API_KEY が必要。"""
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    return _nasa.apod(key, date)

def _nasa_neo_today() -> str:
    """今日地球に接近する小惑星(NEO)を返す。NASA_API_KEY が必要。"""
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    return _nasa.neo_today(key)

mcp.tool(name="apod")(_nasa_apod)
mcp.tool(name="neo_today")(_nasa_neo_today)

# ---- NASA Image & Video Library (画像/音声/動画, 認証不要) ----
mcp.tool()(_media.search_space_images)
mcp.tool()(_media.search_space_audio)
mcp.tool()(_media.search_space_videos)


# ---- 他国の宇宙機関 (ISRO/ESA-Copernicus/JAXA/CSA) ----
# ISRO (インド, 認証不要)
mcp.tool()(_isro.isro_data)
# ESA Copernicus (欧州, 検索は認証不要。ダウンロードは OAuth2 環境変数)
mcp.tool()(_copernicus.copernicus_collections)
mcp.tool()(_copernicus.copernicus_search)
# JAXA Earth (日本, 認証不要)
mcp.tool()(_jaxa.jaxa_datasets)
mcp.tool()(_jaxa.jaxa_dataset_search)
# CSA (カナダ, 認証不要)
mcp.tool()(_csa.csa_dataset_search)


# ---- さらに調査で確認した他国の宇宙機関 ----
# ブラジル INPE (BDC STAC, 認証不要)
mcp.tool()(_inpe.inpe_collections)
mcp.tool()(_inpe.inpe_search)
# CelesTrak (全世界の衛星軌道要素TLE, 認証不要)
mcp.tool()(_celestrak.sat_tle)
# 英国 EO DataHub (STAC, 公開カタログは認証不要)
mcp.tool()(_uk.uk_stac_search)
mcp.tool()(_uk.uk_stac_collections)
# フランス CNES (THEIA/GEODES ポータル到達確認)
mcp.tool()(_cnes.cnes_status)


# ---- 天体観測用天気 (Open-Meteo, 認証不要) ----
mcp.tool()(_weather.astronomy_weather)


# ---- EO Dashboard (NASA/ESA/JAXA 共同地球観測カタログ, 認証不要) ----
mcp.tool()(_eodash.eodashboard_collections)
mcp.tool()(_eodash.eodashboard_detail)


# ---- 宇宙天気 (NASA DONKI, キーは NASA_API_KEY 任意) ----
mcp.tool()(_donki.space_weather)


# ---- AWS Earth Search STAC (Sentinel/Landsat/NAIP, 認証不要) ----
mcp.tool()(_stac.stac_collections)
mcp.tool()(_stac.stac_search)


# ---- ISS 現在位置 (Open Notify, 認証不要) ----
mcp.tool()(_iss.iss_now)


# ---- WMO OSCAR 衛星カタログ (認証不要) ----
mcp.tool()(_oscar.satellite_status)


# ---- 中国 CNSA 系衛星データポータル (NSMC/CNSA-GEO/CRESDA, 到達性+概要) ----
mcp.tool()(_cnsa.cnsa_status)

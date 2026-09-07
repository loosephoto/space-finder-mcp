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

mcp = FastMCP("Space Finder MCP")

# ---- 逆引き歴史Q&A (Wikidata SPARQL) ----
mcp.tool()(reverse_lookup)


# ---- ロケット打ち上げ (Launch Library 2, 認証なし) ----
mcp.tool()(_launch.upcoming_launches)

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

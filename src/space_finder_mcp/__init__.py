"""Space Finder MCP — 宇宙・天文情報を横断検索するMCPサーバー。

ツール:
- space_history_reverse_lookup: 逆引きQ&A（"米国初の宇宙望遠鏡は？"等）を
  Wikidata SPARQL で解決。根拠はSPARQL結果＋Wikipedia記事引用。
"""

from .env_config import load_dotenv

# MCPクライアントの env を優先しつつ、リポジトリ直下の .env があれば未設定キーを補う
load_dotenv()

from .server import mcp

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()

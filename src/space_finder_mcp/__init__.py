"""Space Finder MCP — 宇宙・天文情報を横断検索するMCPサーバー。

ツール:
- space_history_reverse_lookup: 逆引きQ&A（"米国初の宇宙望遠鏡は？"等）を
  Wikidata SPARQL で解決。根拠はSPARQL結果＋Wikipedia記事引用。
"""

from .server import mcp

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()

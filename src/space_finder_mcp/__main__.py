"""``python -m space_finder_mcp`` でも起動できるようにする（stdio サーバー）。

MCPクライアントによっては ``python -m <package>`` 形式で起動するため、
``space-finder-mcp``（uv のスクリプト）と同じエントリを提供する。
"""

from . import main

if __name__ == "__main__":
    main()

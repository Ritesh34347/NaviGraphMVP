"""Entry point for running the NaviGraph MCP server over stdio -- the
transport an MCP client (Claude Desktop, another agent framework) spawns
this process with. Run via the `navigraph-mcp-server` console script
(see pyproject.toml's `[project.scripts]`) or `python -m navigraph_mcp_server`.
"""

from __future__ import annotations

from navigraph_mcp_server.server import build_server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()

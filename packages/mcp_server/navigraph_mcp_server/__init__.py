"""NaviGraph's agentic tool-surface API (Phase 14.2): an MCP server
wrapping the gateway's `/ask` so external agentic clients can call
NaviGraph as a tool. See `server.py` for the implementation."""

from navigraph_mcp_server.server import build_server

__all__ = ["build_server"]

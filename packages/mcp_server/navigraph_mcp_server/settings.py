"""Settings for the MCP tool-surface server.

Mirrors `navigraph_gateway.settings.GatewaySettings`'s identical pattern:
one field, defaulted to the docker-compose service name/port so this
server works with zero configuration inside the compose network, with an
env var override (`GATEWAY_BASE_URL`) for local (non-compose) runs.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class McpServerSettings(NaviGraphSettings):
    """Settings for the NaviGraph MCP tool-surface server."""

    gateway_base_url: str = "http://gateway:8000"


def get_mcp_server_settings() -> McpServerSettings:
    return McpServerSettings()

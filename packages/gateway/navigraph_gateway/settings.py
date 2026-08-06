"""Gateway-specific settings, extending the shared `NaviGraphSettings` base."""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings
from pydantic import Field


class GatewaySettings(NaviGraphSettings):
    """Settings for the gateway service.

    `agent_runtime_base_url` is the base URL the gateway uses to reach the
    agent-runtime service's `/agents/.../invoke` endpoints. Defaults to the
    docker-compose service name/port (`agent-runtime:8001`); override with
    the `AGENT_RUNTIME_BASE_URL` env var for local (non-compose) runs, e.g.
    `http://localhost:8001`.

    `web_origin` is the one browser origin allowed to call `/ask` directly
    (the real `web` app's public hostname -- see `main.py`'s CORS setup).
    Defaults to the real deployed `dev` hostname; override with the
    `WEB_ORIGIN` env var if the domain ever changes (see LIMITATIONS.md's
    nip.io item).

    `mcp_allowed_hosts`/`mcp_allowed_origins` feed the MCP server's
    (`mcp_tools.py`) DNS-rebinding-protection allowlist -- REAL, found
    live while designing the integration: `FastMCP`'s
    `TransportSecuritySettings` defaults to only `127.0.0.1`/`localhost`/
    `[::1]`, so mounting it unconfigured would reject every real request
    at the actual deployed hostname with a 421. Defaults include the real
    `dev` gateway hostname plus localhost variants for local dev/tests;
    override via `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` (JSON array
    syntax, matching `pydantic-settings`' default `list[str]` env-var
    parsing) if the domain ever changes.
    """

    agent_runtime_base_url: str = "http://agent-runtime:8001"
    web_origin: str = "https://app.navigraph.51-8-46-125.nip.io"
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: [
            "api.navigraph.51-8-46-125.nip.io",
            "localhost:*",
            "127.0.0.1:*",
            "testserver",
        ]
    )
    mcp_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "https://api.navigraph.51-8-46-125.nip.io",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
    )


def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()

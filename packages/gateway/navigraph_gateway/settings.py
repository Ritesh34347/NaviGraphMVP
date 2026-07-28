"""Gateway-specific settings, extending the shared `NaviGraphSettings` base."""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class GatewaySettings(NaviGraphSettings):
    """Settings for the gateway service.

    `agent_runtime_base_url` is the base URL the gateway uses to reach the
    agent-runtime service's `/agents/.../invoke` endpoints. Defaults to the
    docker-compose service name/port (`agent-runtime:8001`); override with
    the `AGENT_RUNTIME_BASE_URL` env var for local (non-compose) runs, e.g.
    `http://localhost:8001`.
    """

    agent_runtime_base_url: str = "http://agent-runtime:8001"


def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()

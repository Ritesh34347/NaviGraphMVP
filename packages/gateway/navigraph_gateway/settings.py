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

    `web_origin` is the one browser origin allowed to call `/ask` directly
    (the real `web` app's public hostname -- see `main.py`'s CORS setup).
    Defaults to the real deployed `dev` hostname; override with the
    `WEB_ORIGIN` env var if the domain ever changes (see LIMITATIONS.md's
    nip.io item).
    """

    agent_runtime_base_url: str = "http://agent-runtime:8001"
    web_origin: str = "https://app.navigraph.51-8-46-125.nip.io"


def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()

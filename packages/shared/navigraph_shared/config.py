"""Central settings for every NaviGraph Python service.

Every field has a default so that importing this module and constructing
`NaviGraphSettings()` never crashes -- even with a completely empty `.env` or
no environment variables set at all. Real values are supplied via env vars
(or a `.env` file) in every real deployment; the defaults here only exist to
keep local dev and the test suite runnable with zero configuration.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class NaviGraphSettings(BaseSettings):
    """Base settings shared across all NaviGraph Python services.

    Service-specific settings (e.g. `navigraph_gateway.settings.GatewaySettings`)
    subclass this to add their own fields while inheriting these defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    environment: str = "local"


def get_settings() -> NaviGraphSettings:
    """Construct settings from the environment. Never raises."""

    return NaviGraphSettings()

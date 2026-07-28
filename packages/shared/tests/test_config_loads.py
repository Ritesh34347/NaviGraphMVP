"""Settings must load without error from a completely empty environment."""

from __future__ import annotations

import os

from navigraph_shared.config import NaviGraphSettings, get_settings


def test_settings_load_with_no_env_vars(monkeypatch) -> None:
    # Strip every env var this settings class cares about to simulate a
    # fresh machine with no .env file and nothing exported.
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = NaviGraphSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.anthropic_api_key == ""
    assert settings.anthropic_model == "claude-sonnet-5"
    assert settings.otel_exporter_otlp_endpoint == "http://otel-collector:4317"
    assert settings.environment == "local"


def test_get_settings_helper_never_raises() -> None:
    settings = get_settings()
    assert isinstance(settings, NaviGraphSettings)


def test_settings_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("ENVIRONMENT", "staging")

    settings = NaviGraphSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.anthropic_api_key == "sk-test-123"
    assert settings.environment == "staging"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-123"

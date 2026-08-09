"""Settings for the Slack bot service.

Mirrors `navigraph_gateway.settings.GatewaySettings`'s pattern. Every
field defaults to something that lets the module import and
`SlackBotSettings()` construct without crashing (`slack_signing_secret`/
`slack_bot_token` default to empty strings) -- `main.py` checks for real
values at startup and refuses to verify/post without them, rather than
this settings class raising, so the service's own test suite never needs
real Slack credentials to run.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class SlackBotSettings(NaviGraphSettings):
    """Settings for the NaviGraph Slack bot service."""

    gateway_base_url: str = "http://gateway:8000"

    # Real Slack app credentials -- from https://api.slack.com/apps, the
    # "Basic Information" (signing secret) and "OAuth & Permissions" (bot
    # token, starts with "xoxb-") pages respectively. Required to verify
    # incoming requests and to post messages back; see main.py.
    slack_signing_secret: str = ""
    slack_bot_token: str = ""

    # One NaviGraph tenant per Slack app deployment -- see this package's
    # own README/DECISIONS.md note for why there is no real mapping yet
    # from a Slack workspace/team to a NaviGraph tenant_id.
    default_tenant_id: str = "navikenz-poc"


def get_slack_bot_settings() -> SlackBotSettings:
    return SlackBotSettings()

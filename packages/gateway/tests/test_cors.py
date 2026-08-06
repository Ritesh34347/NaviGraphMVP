"""Real test proving the gateway allows the real `web` app's origin to call
`/ask` cross-origin (the browser demo UI's actual real-world usage), and
rejects an arbitrary, unrelated origin.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigraph_gateway.main import app
from navigraph_gateway.settings import get_gateway_settings


def test_preflight_allows_the_real_web_origin() -> None:
    web_origin = get_gateway_settings().web_origin

    with TestClient(app) as client:
        response = client.options(
            "/ask",
            headers={
                "Origin": web_origin,
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == web_origin


def test_preflight_rejects_an_unrelated_origin() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/ask",
            headers={
                "Origin": "https://not-navigraph.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert "access-control-allow-origin" not in response.headers

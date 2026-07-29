"""Real test for the agent-runtime's /healthz endpoint using FastAPI's TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient
from navigraph_agents.main import app


def test_healthz_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_ok_and_lists_registered_agents() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    expected_agents = {
        "understanding.intent_understanding",
        "understanding.conversation",
        "understanding.metadata_discovery",
        "understanding.ontology",
        "understanding.semantic_retrieval",
        "understanding.schema_mapping",
        "query.data_source_discovery",
        "query.sql_generation",
        "query.sql_optimization",
        "query.execution_planning",
        "query.data_federation",
        "query.caching",
        "guardrail.schema_constraint_validator",
        "guardrail.policy_authorization",
        "guardrail.query_cost_estimator",
        "guardrail.pii_exposure_checker",
        "insight.chart_selection",
        "insight.anomaly_outlier_highlighter",
        "insight.grounded_narrative_generation",
        "insight.follow_up_suggestion",
        "ops.lineage_recorder",
        "ops.evaluation_judge",
    }
    assert expected_agents.issubset(set(body["registered_agents"]))


def test_metrics_endpoint_is_exposed() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]

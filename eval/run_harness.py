"""LLM-as-judge evaluation harness.

Runs every real golden-set question (`eval/golden_set/*.yaml`) through the
real, full pipeline (`eval.pipeline_chain.run_full_pipeline`) against the
live docker-compose Postgres/Neo4j/OPA stack and a real Snowflake account,
using a real Anthropic LLM client for every LLM-backed step -- then scores
each real answer with the real `ops.evaluation_judge` agent. Writes a
report to `eval/results/<run_id>.json`.

REQUIRES LIVE, REACHABLE POSTGRES, NEO4J, OPA, A REAL SNOWFLAKE ACCOUNT, AND
`ANTHROPIC_API_KEY` -- same live-dependency stance as every
`tests/integration/` test: does not skip gracefully. Not wired into CI
(`.github/workflows/ci.yml` runs only `pytest packages/` and has no
Anthropic/Snowflake secrets configured) -- see `LIMITATIONS.md`/`DECISIONS.md`
for why that's a deliberate, logged deferral, not an oversight.

Usage (run as a module, from the repo root, so `eval` resolves as a real
package -- `python eval/run_harness.py` directly would NOT put the repo
root on `sys.path` and `import eval.pipeline_chain` would fail):

    python -m eval.run_harness
    python -m eval.run_harness --compare-to eval/results/baseline.json
    python -m eval.run_harness --limit 3          # run only the first N golden questions

Point this at the real services via the same env-var convention every other
NaviGraph integration test uses: `POSTGRES_HOST`/`POSTGRES_PORT`,
`NEO4J_URI`/`NEO4J_PASSWORD`, `OPA_URL`, the real `SNOWFLAKE_*` credentials,
and `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from navigraph_agents.ops.evaluation_judge.agent import EvaluationJudgeAgent
from navigraph_agents.ops.evaluation_judge.contracts import (
    AnomalyFinding as JudgeAnomalyFinding,
)
from navigraph_agents.ops.evaluation_judge.contracts import (
    ChartSpec as JudgeChartSpec,
)
from navigraph_agents.ops.evaluation_judge.contracts import (
    EvaluationJudgeInput,
    EvaluationJudgePayload,
)
from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings
from navigraph_shared.config import get_settings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import AnthropicLLMClient, LLMClient
from navigraph_shared.opa import HttpOpaClient, OpaSettings

from eval.pipeline_chain import run_full_pipeline

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_GOLDEN_SET_DIR = Path(__file__).parent / "golden_set"
_RESULTS_DIR = Path(__file__).parent / "results"

# Real, not validated against a human-graded calibration set yet -- see
# LIMITATIONS.md item 34. A drop of this size (on the 1-5 scale) or an
# intent_match flip is the smallest change implausible as pure judge-model
# noise on an otherwise-unchanged pipeline.
_REGRESSION_SCORE_DROP_THRESHOLD = 2


def _load_golden_set() -> list[dict[str, Any]]:
    questions = []
    for path in sorted(_GOLDEN_SET_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            questions.append(yaml.safe_load(f))
    return questions


def _build_llm_client() -> LLMClient:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY is not set. The evaluation harness requires "
            "a real Anthropic API key -- it does not run against FakeLLMClient "
            "(see this file's module docstring: the whole point is scoring the "
            "REAL system's real behavior).",
            file=sys.stderr,
        )
        sys.exit(1)
    return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)


async def _run_one_question(
    question_spec: dict[str, Any],
    *,
    data_source_id: str,
    catalog_session_factory: Any,
    neo4j_client: Neo4jClient,
    opa_client: HttpOpaClient,
    llm_client: LLMClient,
    judge_agent: EvaluationJudgeAgent,
) -> dict[str, Any]:
    question_id = question_spec["question_id"]
    trace_id = f"eval-harness-{question_id}-{uuid.uuid4().hex[:8]}"

    print(f"[{question_id}] running: {question_spec['question']!r}")

    pipeline_result = await run_full_pipeline(
        question=question_spec["question"],
        tenant_id=_TENANT_ID,
        data_source_id=data_source_id,
        catalog_session_factory=catalog_session_factory,
        neo4j_client=neo4j_client,
        opa_client=opa_client,
        llm_client=llm_client,
        trace_id=trace_id,
    )

    if not pipeline_result.succeeded:
        print(
            f"[{question_id}] PIPELINE FAILED at {pipeline_result.failure_stage}: "
            f"{pipeline_result.failure_reason}"
        )
        return {
            "question_id": question_id,
            "question": question_spec["question"],
            "pipeline_succeeded": False,
            "failure_stage": pipeline_result.failure_stage,
            "failure_reason": pipeline_result.failure_reason,
        }

    intent_match_expected_vs_actual = (
        pipeline_result.actual_intent == question_spec["expected_intent"]
    )

    judge_input = EvaluationJudgeInput(
        request_context=RequestContext(
            tenant_id=_TENANT_ID,
            user_id="eval-harness",
            trace_id=trace_id,
            roles=["analyst"],
        ),
        payload=EvaluationJudgePayload(
            question=question_spec["question"],
            expected_intent=question_spec["expected_intent"],
            expected_entities=question_spec.get("expected_entities", []),
            actual_intent=pipeline_result.actual_intent,
            actual_narrative=pipeline_result.narrative or "",
            final_columns=pipeline_result.final_columns,
            final_rows=pipeline_result.final_rows,
            chart=JudgeChartSpec(**(pipeline_result.chart or {})),
            anomalies=[
                JudgeAnomalyFinding(**a) for a in pipeline_result.anomalies
            ],
        ),
    )
    judge_output = await judge_agent.run(judge_input)
    judge_result = judge_output.result

    print(
        f"[{question_id}] correctness={judge_result.correctness.score} "
        f"groundedness={judge_result.groundedness.score} "
        f"narrative_quality={judge_result.narrative_quality.score} "
        f"intent_match={judge_result.intent_match}"
    )

    return {
        "question_id": question_id,
        "question": question_spec["question"],
        "pipeline_succeeded": True,
        "resolved_question": pipeline_result.resolved_question,
        "expected_intent": question_spec["expected_intent"],
        "actual_intent": pipeline_result.actual_intent,
        # Real, deterministic (Python-computed) check against the golden
        # spec's own expectation -- separate from EvaluationJudgeResult's
        # own `intent_match` field, which compares expected vs actual
        # intent, the same computation, exposed here for the report too.
        "intent_match": intent_match_expected_vs_actual,
        "unmapped_terms": pipeline_result.unmapped_terms,
        "final_row_count": pipeline_result.final_row_count,
        "narrative": pipeline_result.narrative,
        "narrative_errors": pipeline_result.narrative_errors,
        "follow_up_suggestions": pipeline_result.follow_up_suggestions,
        "correctness": judge_result.correctness.model_dump(),
        "groundedness": judge_result.groundedness.model_dump(),
        "narrative_quality": judge_result.narrative_quality.model_dump(),
    }


def _summarize(questions: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = [q for q in questions if q.get("pipeline_succeeded")]
    if not succeeded:
        return {
            "count": len(questions),
            "pipeline_success_rate": 0.0,
            "intent_match_rate": None,
            "avg_correctness": None,
            "avg_groundedness": None,
            "avg_narrative_quality": None,
        }

    return {
        "count": len(questions),
        "pipeline_success_rate": len(succeeded) / len(questions),
        "intent_match_rate": sum(1 for q in succeeded if q["intent_match"]) / len(succeeded),
        "avg_correctness": sum(q["correctness"]["score"] for q in succeeded) / len(succeeded),
        "avg_groundedness": sum(q["groundedness"]["score"] for q in succeeded) / len(succeeded),
        "avg_narrative_quality": (
            sum(q["narrative_quality"]["score"] for q in succeeded) / len(succeeded)
        ),
    }


def _compare_to_baseline(
    current_questions: list[dict[str, Any]], baseline_path: Path
) -> list[str]:
    """Real regression check: compare each question's scores against a
    prior saved run. Returns a list of human-readable regression messages
    (empty if none found)."""

    with baseline_path.open(encoding="utf-8") as f:
        baseline = json.load(f)
    baseline_by_id = {q["question_id"]: q for q in baseline["questions"]}

    regressions = []
    for question in current_questions:
        question_id = question["question_id"]
        baseline_question = baseline_by_id.get(question_id)
        if baseline_question is None or not baseline_question.get("pipeline_succeeded"):
            continue
        if not question.get("pipeline_succeeded"):
            regressions.append(f"{question_id}: pipeline now FAILS (previously succeeded)")
            continue

        if baseline_question["intent_match"] and not question["intent_match"]:
            regressions.append(f"{question_id}: intent_match flipped true -> false")

        for dimension in ("correctness", "groundedness", "narrative_quality"):
            baseline_score = baseline_question[dimension]["score"]
            current_score = question[dimension]["score"]
            if baseline_score - current_score >= _REGRESSION_SCORE_DROP_THRESHOLD:
                regressions.append(
                    f"{question_id}: {dimension} dropped from {baseline_score} to "
                    f"{current_score} (>= {_REGRESSION_SCORE_DROP_THRESHOLD}-point threshold)"
                )

    return regressions


async def _main(limit: int | None, compare_to: Path | None) -> None:
    llm_client = _build_llm_client()

    catalog_settings = MetadataCatalogSettings()
    catalog_engine = get_engine(catalog_settings)
    catalog_session_factory = get_session_factory(catalog_engine)

    with session_scope(catalog_session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        if not matching:
            print(
                f"ERROR: No data source named {_DATA_SOURCE_NAME!r} for tenant "
                f"{_TENANT_ID!r}.",
                file=sys.stderr,
            )
            sys.exit(1)
        data_source_id = str(matching[0].id)

    neo4j_client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = neo4j_client.test_connection()
    if not connectivity.success:
        print(f"ERROR: Neo4j unreachable: {connectivity.message}", file=sys.stderr)
        sys.exit(1)

    opa_client = HttpOpaClient(OpaSettings())
    judge_agent = EvaluationJudgeAgent(llm_client=llm_client)

    golden_set = _load_golden_set()
    if limit is not None:
        golden_set = golden_set[:limit]
    print(f"Loaded {len(golden_set)} golden question(s) from {_GOLDEN_SET_DIR}")

    results = []
    for question_spec in golden_set:
        result = await _run_one_question(
            question_spec,
            data_source_id=data_source_id,
            catalog_session_factory=catalog_session_factory,
            neo4j_client=neo4j_client,
            opa_client=opa_client,
            llm_client=llm_client,
            judge_agent=judge_agent,
        )
        results.append(result)

    neo4j_client.close()

    summary = _summarize(results)
    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))

    if compare_to is not None:
        print(f"\n=== Regression check against {compare_to} ===")
        regressions = _compare_to_baseline(results, compare_to)
        if regressions:
            for message in regressions:
                print(f"REGRESSION: {message}")
        else:
            print("No regressions found.")

    _RESULTS_DIR.mkdir(exist_ok=True)
    # A plain wall-clock timestamp for the run_id -- safe here (unlike
    # inside a Workflow script's durable-replay context) since this is a
    # one-shot CLI invocation, never replayed; see this file's own
    # docstring if this logic is ever ported into an orchestrated workflow.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = _RESULTS_DIR / f"{run_id}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "questions": results, "summary": summary}, f, indent=2)
    print(f"\nWrote report to {report_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N golden questions (default: all).",
    )
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="Path to a prior run's JSON report to check for regressions against.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_main(limit=args.limit, compare_to=args.compare_to))

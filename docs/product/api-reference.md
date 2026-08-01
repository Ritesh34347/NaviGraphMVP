# API Reference

Curated reference for the two real, deployed HTTP services. Both are
FastAPI apps — each also auto-serves a live, complete OpenAPI spec at
`/openapi.json` and interactive docs at `/docs` (agent-runtime's is
internal-only, not exposed through the public ingress; gateway's is
reachable at `https://api.navigraph.51-8-46-125.nip.io/docs`). This
document is a curated companion, not a duplicate of that generated spec.

## `gateway` — the one real public entry point

Base URL (live): `https://api.navigraph.51-8-46-125.nip.io`

### `POST /ask`

The one real way to ask a question. Forwards to agent-runtime's
`orchestrator.request_orchestrator` over a real internal HTTP hop.

**Request** (real shape, `AskRequest` in `packages/gateway/navigraph_gateway/main.py`):

```json
{
  "question": "How many transactions has each customer made?",
  "tenant_id": "navikenz-poc",
  "user_id": "demo-user",
  "session_id": null,
  "data_source_id": null,
  "roles": ["analyst"],
  "claims": {"tenant_id": "navikenz-poc"}
}
```

`roles`/`claims` are caller-supplied and not yet cryptographically
verified (`LIMITATIONS.md` item 23) — `claims.tenant_id` must match the
top-level `tenant_id` for OPA's `authz.rego` to authorize the request.
`session_id`/`data_source_id` are optional; the orchestrator mints/
resolves them when omitted.

**Response** — the real `RequestOrchestratorResult`, wrapped in the
standard `AgentOutput` envelope:

```json
{
  "result": {
    "outcome": "answered",
    "session_id": "sess_...",
    "resolved_question": "How many transactions has each customer made?",
    "actual_intent": "metric_lookup",
    "final_columns": ["CUSTOMERID", "RECORD_COUNT"],
    "final_rows": [{"CUSTOMERID": 1001, "RECORD_COUNT": 40}, "..."],
    "final_row_count": 10000,
    "chart": {"chart_type": "table", "x_column": null, "y_column": null, "rationale": "..."},
    "anomalies": [],
    "narrative": null,
    "follow_up_suggestions": [
      "Who are the top 20 customers by transaction count?",
      "What is the distribution of transaction counts across all customers (e.g., one-time vs. repeat buyers)?",
      "How does each customer's transaction count trend over time (monthly or quarterly)?"
    ],
    "clarifying_question": null,
    "failure_stage": null,
    "failure_reason": null
  },
  "confidence": 1.0,
  "lineage_events": ["..."],
  "errors": [],
  "metadata": {"latency_ms": 42000.0, "model_version": null, "prompt_version": null, "tokens_input": null, "tokens_output": null}
}
```

`result.outcome` is exactly one of three real values:

| `outcome` | When | Relevant fields |
|---|---|---|
| `"answered"` | Full pipeline completed | `final_columns`/`final_rows`/`chart`/`narrative`/`anomalies`/`follow_up_suggestions` |
| `"needs_clarification"` | Schema Mapping resolved zero tables | `clarifying_question` |
| `"failed"` | A structured, specific failure (e.g. PII denial, OPA unreachable) | `failure_stage`, `failure_reason` |

Real example of a `"failed"` response body (confirmed live, `analyst`
role querying a real tagged PII column):

```json
{
  "result": {
    "outcome": "failed",
    "failure_stage": "guardrail.pii_exposure_checker",
    "failure_reason": "role(s) ['analyst'] not authorized for PII column(s) in data_source_id=6251ba29-d554-4de9-b049-dc1f3f45658c"
  }
}
```

**A real question can take up to ~90 seconds** — several sequential LLM
calls plus a live Snowflake query. Gateway's own `httpx` client timeout
and the ingress's `proxy-read-timeout` are both set to 120s to
accommodate this (`LIMITATIONS.md` item 75).

### `GET /healthz`, `GET /readyz`

Both return `{"status": "ok"}`. `/readyz` is currently identical to
`/healthz` (no real dependency check yet).

### `GET /metrics`

Prometheus-format metrics (via `prometheus-fastapi-instrumentator`).

## `agent-runtime` — internal only, one route per agent

Base pattern: `POST /agents/{domain}/{agent_name}/invoke`, internal-only
(not exposed through the public ingress — reachable via
`kubectl port-forward svc/agent-runtime 8001:8001` for direct debugging,
which is how several real bugs this project found were root-caused).

Every route accepts the agent's own real `{request_context, payload}`
shape and returns its `AgentOutput` envelope (`{result, confidence,
lineage_events, errors, metadata}`) — see
[`agent-contract.md`](../architecture/agent-contract.md) for the formal
shape every agent implements.

**One real worked example per domain** (all captured live this session):

**Understanding** — `POST /agents/understanding/intent_understanding/invoke`

```json
// request
{"request_context": {"tenant_id": "navikenz-poc", "user_id": "debug", "trace_id": "t1", "roles": ["analyst"], "claims": {"tenant_id": "navikenz-poc"}},
 "payload": {"question": "How many transactions has each customer made?"}}
// response.result
{"intent": "metric_lookup", "entities": ["transactions", "each customer"], "raw_question": "..."}
```

**Query** — `POST /agents/query/sql_generation/invoke` — takes the real
`schema_mapping_result`/`resolved_data_sources`/`original_question`;
returns `GeneratedSql` statements with bind-parameterized predicates.

**Guardrail** — `POST /agents/guardrail/policy_authorization/invoke` —
real `POST` to OPA under the hood; returns `authorized`/`decisions`/
`rejected` split. Fails closed (`opa_unreachable`, non-recoverable,
`authorized=[]`) if OPA can't be reached.

**Insight** — `POST /agents/insight/chart_selection/invoke` — pure
function, no LLM; returns a `ChartSpec{chart_type, x_column, y_column,
rationale}`.

**Ops** — `POST /agents/ops/lineage_recorder/invoke` — takes a real
agent's own `lineage_events` list; `INSERT ... ON CONFLICT (event_id) DO
NOTHING` under the hood, so re-recording the same events is a real,
tested no-op.

**Orchestrator** — `POST /agents/orchestrator/request_orchestrator/invoke`
— the one route `gateway`'s `/ask` actually calls; everything else above
is reachable directly for isolated testing/debugging but isn't part of
the real end-user request path.

### `GET /lineage/{trace_id}?tenant_id=...`

Plain read endpoint (not agent-shaped, matches `navigraph_catalog.api`'s
read convention) returning the full, ordered lineage trace for one real
request.

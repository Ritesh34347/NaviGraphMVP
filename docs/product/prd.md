# Product Requirements Document

**Status**: Reflects the real, shipped product as of 2026-08-01 (all 10
build phases complete). Written after the fact, from the real decisions
already recorded in `DECISIONS.md`/`BUILD_LOG.md`, rather than as a
speculative up-front spec — this is deliberate: every requirement below
traces to something actually built and verified, not aspirational.

## 1. Problem statement

Business analysts and stakeholders need answers to real questions about
their data — transaction volumes, customer risk profiles, market
activity, anomalies — without writing SQL or waiting on a data team.
Generic LLM chatbots answer fluently but ungrounded: they can fabricate
numbers, ignore access controls, or query the wrong table entirely.
NaviGraph exists to close that gap: a conversational interface that is
**schema-grounded** (every generated query is validated against a real,
crawled catalog before it can run), **policy-governed** (every request
passes through real RBAC/ABAC/PII enforcement before data is touched),
and **auditable** (every stage of reasoning is recorded to a queryable
lineage trail) — trustworthy enough to hand to a real business user, not
just a technical demo.

## 2. Target users

- **Business/data analysts** — the primary user. Asks natural-language
  questions, gets back a chart, a grounded narrative, and follow-up
  suggestions. Represented in the real system today by the `analyst`
  role.
- **Privileged/compliance viewers** — need access to sensitive fields
  (e.g. customer risk classification) that `analyst` is deliberately
  denied. Represented today by the `pii_viewer` and `admin` roles in
  `guardrail.pii_exposure_checker`'s `PII_AUTHORIZED_ROLES`.
- **Platform engineers** — extend the agent pipeline, add data sources,
  operate the deployment. Served by `docs/architecture/agent-contract.md`,
  `ONBOARDING.md`, and the runbooks.

## 3. Real target dataset

The platform's one real, registered Snowflake data source is
`FIDELITY_POC` — a retail brokerage/wealth-management dataset (customers,
assets, markets, transactions, prices), crawled via the real, least-
privilege `FIDELITY_ANALYST_ROLE` (confirmed read-only via a live `SHOW
GRANTS` check — `DECISIONS.md`'s Phase 5 entry). This is real data
shaping real design decisions throughout the build (e.g. the knowledge
graph's node taxonomy in `data-model.md` was designed *after*, and
grounded in, this real schema — not a generic ontology invented up
front).

## 4. Functional requirements

The controlled vocabulary of question types the platform is built to
answer — the real `IntentLabel` values every question is classified into
(`understanding.intent_understanding`) — defines the functional scope:

| Intent | What it means | Example (real, golden-set) |
|---|---|---|
| `metric_lookup` | A single aggregate or breakdown | "How many transactions has each customer made?" |
| `comparison` | Compare a metric across a dimension | "Which markets have the highest transaction volume?" |
| `trend_analysis` | A metric's change over time | "Show me the trend of transaction volume over time." |
| `anomaly_investigation` | Detect/explain unusual values | "Are there any unusual spikes in units traded by market?" |
| `unknown` | Safe fallback — never silently guessed | (malformed/unclassifiable input) |

Every question additionally produces, when answered:

- A **chart** appropriate to the result shape (`insight.chart_selection`)
- A **grounded narrative** whose numeric claims are validated against the
  real result set, not hallucinated (`insight.grounded_narrative_generation`)
- **Anomaly detection** where applicable (`insight.anomaly_outlier_highlighter`)
- **Follow-up question suggestions** (`insight.follow_up_suggestion`)
- A **full lineage trail** from question to final answer (`ops.lineage_recorder`)

And, when a question can't be answered as asked, one of two honest,
structured outcomes instead of a wrong guess:

- `needs_clarification` — the schema mapping resolved zero tables; the
  platform asks a real clarifying question rather than failing silently
- `failed` — a specific, real reason (e.g. `pii_column_access_denied`)

## 5. Non-functional requirements (all real, all enforced in code today)

- **Multi-tenant isolation** — every request carries a `tenant_id`;
  every cache key, catalog row, and lineage record is scoped to it.
- **RBAC/ABAC authorization** — real OPA policy (`authz.rego`)
  evaluated per request, fail-closed if OPA is unreachable.
- **PII protection** — columns are tagged `is_pii` in the catalog; a
  dedicated agent (`guardrail.pii_exposure_checker`) denies unauthorized
  roles, independent of the RBAC layer.
- **SQL injection safety** — bind-parameterized values only, plus a real
  SQL-parse safety gate (`query.execution_planning`) rejecting anything
  but a single SELECT statement.
- **Auditability** — every agent's lineage events are persisted,
  queryable per `trace_id`.
- **Explainability** — narrative claims are checked against real result
  cells; fabricated citations are dropped, not silently trusted.
- **Compliance posture** — built toward a **SOC 2 Type II** target from
  day one (audit logging, change management via CODEOWNERS/required CI
  checks, access-control posture) — see `security-compliance.md` for the
  full controls mapping.

## 6. Explicitly out of scope (real, current, honestly logged — not silently missing)

Pulled directly from `LIMITATIONS.md` rather than restated speculatively:

- **No cryptographic identity verification yet** — `roles`/`claims` are
  caller-supplied; real Azure AD JWT verification is deferred (item 23).
- **Single data source** — only Snowflake (`FIDELITY_POC`) is connected;
  the connector SDK is source-agnostic but Postgres/REST connectors were
  never built (item 1).
- **No real domain name** — the live deployment uses `nip.io`, not a
  registered domain (item 52).
- **No real chat login** — the demo chat UI uses a fixed demo
  tenant/role, not a real sign-in flow (item 77).
- **10-question golden set**, not the originally-scoped 50+ (a real,
  confirmed scope reduction — `BUILD_LOG.md`'s Phase 8 entry).
- **No mid-pipeline crash recovery/checkpointing** (item 41) — a
  consequence of the real, deliberate decision not to use LangGraph.

## 7. Success criteria (as actually measured)

- The real 10-question golden set, run through `eval/run_harness.py`
  against the live Snowflake/Anthropic/Neo4j/OPA stack, scored by a real
  LLM-as-judge (`ops.evaluation_judge`) on `correctness`/`groundedness`/
  `narrative_quality` (1-5 scale) plus a deterministic `intent_match`
  check.
- The real adversarial security suite (`tests/security/`,
  `tests/security/cloud/`) passing against the live, non-allow-all OPA
  policy and the live AKS cluster.
- A real, end-to-end browser round-trip through the live chat UI
  producing a correct `answered`/`needs_clarification`/`failed` outcome
  for each of the three real cases (verified live, 2026-08-01).

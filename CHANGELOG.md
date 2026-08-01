# Changelog

Release notes, one entry per build phase, reframed for an external/
stakeholder audience — what shipped, not the internal build narrative.
For the full internal build narrative (what was built, what was tested,
what broke and how it was fixed), see [`BUILD_LOG.md`](./BUILD_LOG.md).
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## 2026-07-31 — Phase 10b closeout

### Fixed
- CI turned fully green across every workflow.
- `cd-deploy.yml` proven end to end for real against the live cluster.
- A deferred large-result-set bug in the eval harness fixed and
  re-verified.

## 2026-07-30 — Phase 10b: cluster bootstrap, real data, adversarial security review

### Added
- The real target dataset (`FIDELITY_POC`) crawled and ingested for
  real.
- A full adversarial security review run against the live cloud
  environment.

## 2026-07-30 — Phase 10b: real Azure infrastructure created

### Added
- Real, billable Azure infrastructure applied via Terraform: AKS, ACR,
  Key Vault, Postgres Flexible Server, networking, Entra app
  registration.

## 2026-07-30 — Phase 10a: real Kubernetes manifests, weighted-canary CD, adversarial cloud security tests

### Added
- Full `infra/k8s/` Kustomize manifest tree (base + `kind`/`dev`
  overlays).
- A real, weighted-canary CD pipeline (`cd-deploy.yml`) — proven with
  zero Azure cost first, against a local `kind` cluster.
- The cloud-focused half of the adversarial security suite
  (`tests/security/cloud/`).

## 2026-07-29 — Phase 9: Orchestrator domain

### Added
- 3 new agents: Request Orchestrator, Session/Context Manager,
  Multi-turn Clarification Coordinator.
- Every hand-threaded pipeline chain replaced by one real, callable
  Request Orchestrator agent.

### Changed
- Reversed Phase 1's original LangGraph decision — the orchestrator is a
  plain Python async function (see `DECISIONS.md`).

## 2026-07-29 — Phase 8: Lineage Recorder + LLM-as-judge evaluation harness

### Added
- The `ops.lineage_recorder` and `ops.evaluation_judge` agents.
- The real 10-question golden set and `eval/run_harness.py`.
- The first real end-to-end run against a genuine Anthropic model.

## 2026-07-29 — Phase 7: Insight domain

### Added
- 4 new agents: Chart Selection, Anomaly/Outlier Highlighter, Grounded
  Narrative Generation, Follow-up Suggestion.
- The first fully real end-to-end chain, from raw question through a
  grounded narrative.

## 2026-07-29 — Phase 6: Guardrail domain + real OPA policy

### Added
- 4 new agents: Schema Constraint Validator, Policy Authorization, Query
  Cost/Row-Limit Estimator, PII Exposure Checker.
- A real, non-allow-all OPA Rego policy, replacing Phase 1's placeholder.
- The first adversarial security test suite (`tests/security/`).

### Fixed
- Closed the real compensating-controls gap Phase 5 had explicitly
  logged as temporary.

## 2026-07-29 — Phase 5: Query domain + Trino/Snowflake federation

### Added
- 6 new agents: Data Source Discovery, SQL Generation, SQL Optimization,
  Execution Planning, Data Federation, Caching.
- Real SQL executed against a live Snowflake account for the first time.

## 2026-07-30 — Phase 4: 5 remaining Understanding-domain agents

### Added
- Conversation, Metadata Discovery, Ontology, Semantic Retrieval, Schema
  Mapping agents — verified end to end against live Postgres + Neo4j.

## 2026-07-29 — Phase 3: Knowledge graph / ontology

### Added
- The real two-tier knowledge graph (`packages/knowledge_graph`),
  verified against real Neo4j + real Snowflake reference data.

## 2026-07-29 — Phase 2: Metadata catalog + connector SDK

### Added
- `packages/metadata_catalog` and `packages/connector_sdk`, verified
  against a real Snowflake account.

## 2026-07-29 — Phase 1: end-to-end verification

### Fixed
- Real bugs found and fixed during first end-to-end verification of the
  Phase 1 scaffold.

## 2026-07-28 — Phase 1: initial scaffold

### Added
- Repo scaffold, CI skeleton, Terraform skeleton, local docker-compose
  dev environment, and the first real agent (Intent Understanding) as a
  reference implementation of the agent contract pattern.

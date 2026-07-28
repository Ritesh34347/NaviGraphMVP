# Limitations

This document is a deliberately honest, living record of what NaviGraph does **not**
do yet, and why. It is started on **2026-07-28** during the Phase 1 infra scaffold
and is expected to shrink over time as later phases close these gaps. Anything not
listed here is not a known limitation as of the date below — it may still be
incomplete, but nobody has recorded it as an intentional deferral.

---

### 1. Only a Snowflake connector is implemented

**What's deferred**: Postgres and generic REST reference connectors.

**Why**: Phase 1/2 scope is one real, production-quality data source end-to-end
rather than several shallow ones. Snowflake is the customer's actual warehouse.

**What full version requires**: The data-source SDK interface is already written to
be source-agnostic (connection lifecycle, schema introspection, query execution,
credential handling are all behind an interface), but that interface is unproven
against a second, differently-shaped source. A Postgres connector should be built
next specifically to pressure-test the abstraction, followed by a generic REST/API
connector for sources with no SQL surface at all.

### 2. Neo4j runs as a single local instance

**What's deferred**: High-availability Neo4j clustering / Neo4j Aura Enterprise.

**Why**: Local dev and early cloud phases don't need HA; a single instance is
sufficient to validate the knowledge-graph query patterns.

**What full version requires**: Migrating to Aura Enterprise (or a self-managed
causal cluster) as part of the cloud deployment phase, including backup/restore,
read-replica routing, and failover testing.

### 3. Trino has zero real catalogs registered

**What's deferred**: Wiring an actual Snowflake catalog (or any catalog) into Trino.

**Why**: Phase 1 stands up the coordinator/worker topology and proves the compose
stack forms a working cluster. Real catalog wiring depends on Snowflake credentials
and network access that belong to a later phase.

**What full version requires**: A real `snowflake.properties` catalog file (see
`infra/trino/catalog/placeholder.properties.example` for the intended shape),
Snowflake network policy/firewall coordination, and a validation pass confirming
federated queries return correct results end-to-end.

### 4. OPA runs an allow-all placeholder policy

**What's deferred**: Real RBAC/ABAC and row-/column-level authorization Rego
policies.

**Why**: The policy engine needs to be wired into the request path structurally
before the real policy logic is written and tested — otherwise policy changes have
no enforcement point to land in.

**What full version requires**: A dedicated later phase to author tenant-, role-,
and attribute-aware Rego policies, plus an adversarial test suite (see
`tests/security/`) that must pass before the placeholder is removed. This is
explicitly not to be marked done without that adversarial test coverage.

### 5. Terraform for Azure is a validated skeleton only

**What's deferred**: Any actually-applied Azure infrastructure.

**Why**: Local-first development via docker-compose is the primary inner loop for
as long as possible. Terraform exists now so the eventual cloud target is designed
deliberately rather than retrofitted, but it is intentionally never run against a
real subscription during this phase.

**What full version requires**: A real Azure subscription, a remote state backend,
a human sign-off step in front of any `terraform apply`, and CI that only ever runs
`fmt`, `validate`, and `plan` (never `apply`) — see `terraform/README.md`.

### 6. SOC 2 Type II controls are scaffolded, not audited

**What's deferred**: Formal documentation and an actual SOC 2 Type II audit.

**Why**: The engineering controls that an audit would check (CI security-scan gate,
CODEOWNERS-enforced review, required-check branch protection) are put in place from
day one so evidence starts accumulating immediately, but "scaffolded" is not
"compliant."

**What full version requires**: Formal policy documentation, a designated
compliance owner, evidence collection over an observation window, and an
independent auditor engagement. This repository's controls are necessary
supporting infrastructure, not sufficient proof of compliance on their own.

### 7. Only one real agent exists (Intent Understanding)

**What's deferred**: The remaining ~24 agents across the Query, Insight, Guardrail,
Ops, and Orchestrator domains.

**Why**: This repo (Phase 1) is infra scaffolding only. Application agents are
being built by a parallel workstream, starting with Intent Understanding as the
proof-of-pattern implementation.

**What full version requires**: Each remaining agent implemented against the
formal contract in `docs/architecture/agent-contract.md`, with its own unit tests
and, where relevant, `@pytest.mark.llm_integration` tests. See
`docs/architecture/overview.md` for the full named list and current status.

### 8. Local tooling installation is not fully tracked

**What's deferred**: A precise record of every tool version installed on the
scaffolding machine.

**Why**: This machine had no Docker, Node.js, Terraform, or WSL installed at the
time scaffolding began. Tooling was installed via `winget` mid-session to unblock
work, but this agent does not have visibility into the exact commands or versions
used beyond that fact.

**What full version requires**: See `BUILD_LOG.md` for whatever tooling install
details were captured; going forward, tool versions should be pinned (e.g. via a
`.tool-versions` file or documented `winget`/`brew` manifest) so environment setup
is reproducible across machines.

### 9. LICENSE terms are a placeholder pending legal sign-off

**What's deferred**: Real, legally-reviewed proprietary licensing terms.

**Why**: `LICENSE` currently states a short "all rights reserved" notice under a
placeholder company name (`Navikenz`, matching the deploying organization's email
domain) so the repository isn't left with no license statement at all.

**What full version requires**: Review and sign-off from legal/counsel on the
actual entity name, copyright holder, permitted-use terms for contractors or
partners, and any export-control or data-residency clauses relevant to a
multi-tenant BI product handling customer data.

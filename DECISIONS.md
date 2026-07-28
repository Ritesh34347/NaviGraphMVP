# Decisions

Architecture-decision-record style log of the significant calls made while
scaffolding NaviGraph. Each entry is written in the first person plural, as of the
date it was made, with the alternative we considered noted briefly. See also
`docs/adr/` for selected decisions written up as formal ADRs.

---

## 2026-07-28 — Python 3.12 + FastAPI + Pydantic v2 + LangGraph for the agent runtime

We chose Python 3.12 with FastAPI, Pydantic v2, and LangGraph as the stack for
`packages/agent_runtime`, rather than a TypeScript-only stack across the whole
backend. LangGraph's graph-of-agents model maps directly onto our ~25-agent,
multi-domain architecture (Understanding, Query, Insight, Guardrail, Ops,
Orchestrator), and Python has the deepest ecosystem for LLM tooling, data/schema
introspection libraries, and the eventual Snowflake/Trino client libraries our
agents depend on. Pydantic v2 gives us fast, strict validation for the
`AgentInput`/`AgentOutput` contract every agent must honor. We considered a
TypeScript-only stack (Next.js API routes or a Node backend end-to-end) for
language uniformity with the web UI, but rejected it because the LLM-orchestration
and data-engineering ecosystems we depend on most heavily are materially more
mature in Python, and we're not willing to trade that maturity for one-language
convenience this early.

## 2026-07-28 — Modular monolith for the 25-agent runtime

We chose to run all ~25 agents inside a single `agent_runtime` FastAPI service (a
modular monolith, with each agent as an isolated module exposing both an
in-process LangGraph node and a thin HTTP wrapper) rather than deploying one
microservice per agent. At our current scale, one-microservice-per-agent would
multiply deployment, networking, and observability overhead by 25x before we have
evidence any individual agent needs independent scaling or an independent release
cadence. The dual invocation pattern (in-process call for the orchestrator's hot
path, HTTP wrapper for isolated testing and the eval harness) gives us the ability
to peel any agent out into its own service later without a rewrite, since the
contract boundary is already service-shaped. We considered full microservices from
day one and rejected it as premature operational complexity for a system whose
agent boundaries are still being tuned.

## 2026-07-28 — Local-first, Azure-targeted Terraform that is never applied

We chose to make docker-compose the everyday inner loop for local development,
while writing Terraform for Azure now as a validated skeleton that is deliberately
never applied during this phase. This lets every engineer iterate fast on a laptop
without cloud credentials or cost, while still forcing us to think through the real
target topology (AKS, ACR, Key Vault, managed Postgres, networking, Entra app
registration) early enough that the eventual cloud migration is a deployment
exercise rather than a design exercise. CI runs `terraform fmt`, `validate`, and
`plan` (gated behind credential presence) to keep the skeleton honest, but `apply`
must never appear in CI — only a human, later, with real sign-off. We considered
deferring Terraform entirely until a cloud deployment was imminent, and rejected
that because retrofitting infra-as-code onto an already-running system tends to
produce Terraform that doesn't match reality; writing it alongside the local stack
keeps the two honest with each other.

## 2026-07-28 — Trino stood up for real federation despite one registered source

We chose to stand up a real Trino coordinator/worker cluster in the local compose
stack now, even though zero real catalogs are registered yet (Snowflake catalog
wiring is a later phase). Federation is a core product promise — "multi-source" —
and proving the cluster topology, health-checking, and worker-joins-coordinator
behavior early means the only remaining work later is catalog configuration, not
debugging a distributed system we've never run. We considered deferring Trino
entirely until Snowflake credentials were available and querying Snowflake
directly in the interim, and rejected that because it would let single-source
assumptions leak into the Query agents' generated SQL in ways that would be
expensive to unwind later.

## 2026-07-28 — Anthropic Claude as default LLM provider behind a provider-agnostic client

We chose Anthropic's Claude, configured via an `ANTHROPIC_API_KEY` environment
variable, as the default LLM provider for all agents, accessed exclusively through
a provider-agnostic client abstraction (being built as shared infrastructure
elsewhere in `packages/`). Every agent codes against that abstraction, never
against the Anthropic SDK directly, so swapping providers or running comparative
evals later is a configuration change, not a rewrite. We considered hard-coding
directly against the Anthropic SDK for simplicity, and rejected it because the
agent contract (`docs/architecture/agent-contract.md`) commits to
provider-swappable model metadata (`model_version`, `prompt_version`) as first-class
output fields, which only makes sense if the provider boundary is real from the
start.

## 2026-07-28 — Next.js for the web UI

We chose Next.js for `web/`. It gives us server-rendered pages for fast initial
load of conversational BI sessions, a mature React ecosystem for the chart and
chat-style components the product needs, and a deployment story (static + server
functions) that maps cleanly onto both local docker-compose and an eventual Azure
target. We considered a plain client-side React SPA (e.g. Vite + React Router) and
rejected it because we want server-side rendering and API-route colocation
available without adopting a second framework later if/when we need them.

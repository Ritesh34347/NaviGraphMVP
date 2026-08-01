# Onboarding Guide

Welcome to NaviGraph. This is the fastest path from "just cloned the
repo" to "shipped a real change." Read this once, then keep the three
living process logs open in a tab while you work.

## 1. Orient yourself (15 minutes)

1. Read the root [`README.md`](./README.md) — one paragraph on what the
   product does, the repo layout, and the current real status.
2. Read [`docs/architecture/overview.md`](./docs/architecture/overview.md)
   — the 25 real agents, grouped by domain, and the real request
   lifecycle diagram.
3. Skim [`docs/architecture/data-flow.md`](./docs/architecture/data-flow.md)
   — one real question traced through every stage, with real lineage
   events. This is the fastest way to see how the pieces actually fit
   together.

## 2. The three logs you'll use constantly

- **[`LIMITATIONS.md`](./LIMITATIONS.md)** — every known gap and every
  real bug found, numbered. Before you assume something is broken,
  search here — it's very likely already investigated, with a root
  cause and a fix (or an explicit decision not to fix it yet).
- **[`DECISIONS.md`](./DECISIONS.md)** — every real architecture/
  implementation decision, dated, with the actual reasoning. Before you
  second-guess a design choice ("why isn't this using X?"), check here.
- **[`BUILD_LOG.md`](./BUILD_LOG.md)** — the phase-by-phase build
  narrative, if you want the "why did we build it in this order"
  context.

These three files are living documents. **Update them as you go** — a PR
that fixes a real bug or makes a real decision should add an entry to
the relevant log, in the same style as the existing entries (read a few
first to match the voice/format).

## 3. Local dev setup

Follow [`docs/runbooks/local-dev-smoke-test.md`](./docs/runbooks/local-dev-smoke-test.md)
— copy the env template, `docker compose up`, run the smoke test. If you
want to validate `infra/k8s/` changes before they touch the real
deployment, use [`docs/runbooks/k8s-local-validation.md`](./docs/runbooks/k8s-local-validation.md)
(a local `kind` cluster, zero Azure cost).

## 4. The agent contract pattern — the one thing to internalize

Every one of the 25 agents follows the exact same shape:
`{agent.py, contracts.py, tests/}` (+ `prompts/` if LLM-backed). The
formal spec, with real code blocks, is
[`docs/architecture/agent-contract.md`](./docs/architecture/agent-contract.md)
— read it before writing your first agent. In short:

- `contracts.py` defines a `Payload`, an `Input(AgentInput)` wrapping it
  plus a mandatory `RequestContext`, a `Result`, and an
  `Output(AgentOutput)` wrapping that — every `AgentOutput` carries
  `lineage_events`, `errors`, and `metadata`.
- Unit tests use `FakeLLMClient` by default — no network, no API key
  needed to run `pytest packages/`.
- Every agent is invocable both in-process (the real request path) and
  via a thin `POST /agents/{domain}/{name}/invoke` HTTP wrapper (for
  isolated testing/debugging — see `docs/product/api-reference.md`).

To scaffold a new agent, use `tools/scripts/new-agent.py` against
`tools/templates/agent_template/` — don't hand-copy an existing agent's
files.

## 5. Where to find things

| Question | Answer |
|---|---|
| "What does agent X actually do?" | `docs/architecture/overview.md`'s per-domain table |
| "How does a request flow end to end?" | `docs/architecture/data-flow.md` |
| "What's the schema/data model?" | `docs/architecture/data-model.md` |
| "What API endpoints exist?" | `docs/product/api-reference.md` |
| "Is this a known issue?" | `LIMITATIONS.md` (search by keyword first) |
| "Why was it built this way?" | `DECISIONS.md` |
| "How do I test my change?" | `docs/testing/test-strategy.md` |
| "Something's wrong in production" | `docs/runbooks/operations-runbook.md` |
| "What are the compliance/security controls?" | `docs/security/security-compliance.md` |
| "What does term X mean?" | `docs/product/glossary.md` |

## 6. A good first task

Pick a real, already-logged item from `LIMITATIONS.md` that's marked
open (not `RESOLVED`) and small in scope — e.g. item 80 (a real,
reproducible SUM-vs-COUNT bug in a specific phrasing) is a good example
of a scoped, well-documented, real bug with a clear repro already on
record. Fixing one of these teaches you the full loop: read the real
symptom → reproduce it live → find the root cause in real code → fix it
→ add a regression test → update `LIMITATIONS.md` with the resolution —
exactly the discipline this whole codebase was built with.

## 7. Ground rules

- Every real bug fix or design decision gets logged in the relevant
  process file, in the same session/PR as the fix — not "later."
- Prefer fixing the real root cause over adding a workaround, especially
  for anything touching security/auth (see `SECURITY.md`).
- If you're not sure whether something is a real gap or already a
  documented, deliberate limitation — check `LIMITATIONS.md` before
  assuming it's a bug.

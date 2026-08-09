# Contributing to NaviGraph

## Branching

- Branch off `main`. Name branches `<type>/<short-description>`, e.g.
  `feat/query-agent-sql-validation` or `fix/opa-healthcheck-timeout`.
- Keep branches short-lived and scoped to one logical change.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <short summary>

<optional body>

<optional footer>
```

Common `<type>` values: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`,
`build`, `perf`. Example:

```
feat(agent-runtime): add row-count validation to query guardrail

Rejects generated SQL that would scan more than the configured row limit
before execution, rather than relying on the warehouse to time out.
```

## Pull requests

- Every PR must pass the required CI checks (see below) before it can merge.
- PRs touching directories with a `CODEOWNERS` entry require review from the
  owning team.
- Keep PRs reviewable: prefer several small PRs over one large one where
  possible.
- Describe *why*, not just *what*, in the PR description — reviewers can read
  the diff for "what."

### Required CI checks

All of the following must pass on every PR (see `.github/workflows/`):

- `python-lint-test` / `node-lint-test` (`ci.yml`) — lint, type-check, and unit
  tests for whichever parts of the tree changed.
- `security-scan` (`security-scan.yml`) — dependency vulnerability scanning
  (`pip-audit`, `npm audit`) and static analysis (`semgrep`).
- `terraform-plan` (`terraform-plan.yml`) — `terraform fmt`/`validate` on any
  PR touching `terraform/**` (plan only runs if Azure credentials are
  configured; `apply` never runs in CI).
- **`adversarial-tests` (`adversarial-tests.yml`)** — runs `tests/security/`,
  which now contains real adversarial tests (tenant isolation, OPA policy,
  PII exposure, plus a `cloud/` suite for the real AKS deployment). This is
  a required check and cannot be skipped. Do not disable or bypass this
  check to get a PR through.

## Adding a new agent

Every agent in the architecture (see `docs/architecture/overview.md` for the
full map of domains and agent names) follows the same contract, formally
defined in [`docs/architecture/agent-contract.md`](./docs/architecture/agent-contract.md).
To add a new one:

1. Copy the scaffold from `tools/templates/agent_template/` into the correct
   domain directory under `packages/agent_runtime/navigraph_agents/<domain>/<agent_name>/`.
2. Implement `agent.py` (the agent logic) and `contracts.py` (its
   `AgentInput`/`AgentOutput` Pydantic models), following the shape described in
   the agent contract doc.
3. Write unit tests under that agent's `tests/` directory using a
   fake/mocked LLM client by default. Mark any test that calls a real LLM with
   `@pytest.mark.llm_integration` so it can be excluded from the default fast
   test run.
4. Register the agent in `packages/agent_runtime/navigraph_agents/main.py`'s
   `lifespan()` (a direct construct-and-`register()` call — the real Request
   Orchestrator is a plain Python async function that calls sub-agents
   directly, not a LangGraph graph; see `DECISIONS.md`'s Phase 9 entry), and
   confirm its thin HTTP wrapper (`POST /agents/<domain>/<agent_name>/invoke`)
   responds correctly for isolated testing and the eval harness. If the new
   agent belongs in the live request lifecycle, also wire it into
   `RequestOrchestratorAgent.run()`
   (`orchestrator/request_orchestrator/agent.py`) in the right sequence
   position.
5. Update `docs/architecture/overview.md`'s status table and
   `docs/architecture/single-stage-mvp.md`'s sequence (if wired into the live
   pipeline) to reflect the new agent.

For a concrete, real example, see any of the 25 real agents under
`packages/agent_runtime/navigraph_agents/` — e.g.
`understanding/intent_understanding/` for an LLM-backed agent, or
`guardrail/schema_constraint_validator/` for a deterministic one.

## Code style

- Python: formatted and linted with `ruff`, type-checked with `mypy`. Both run
  in CI (`ci.yml`).
- TypeScript/Next.js: linted with the project's `eslint` config and
  type-checked with `tsc`, both run via `npm run lint` / `npm run typecheck` in
  CI.
- Follow `.editorconfig` for whitespace/indentation regardless of language.

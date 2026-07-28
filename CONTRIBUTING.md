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
- **`adversarial-tests` (`adversarial-tests.yml`)** — runs `tests/security/`.
  This is a required check and cannot be skipped, even though it currently
  passes vacuously (an empty test directory) until later phases add real
  security-relevant components. Do not disable or bypass this check to get a
  PR through.

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
4. Wire the agent into the LangGraph orchestrator graph for in-process
   invocation, and confirm its thin HTTP wrapper
   (`POST /agents/<domain>/<agent_name>/invoke`) responds correctly for
   isolated testing and the eval harness.
5. Update `docs/architecture/overview.md`'s status table so the agent moves
   from "designed" to "built."

For a concrete, real example, see the one agent that is fully implemented today:
`packages/agent_runtime/navigraph_agents/understanding/intent_understanding/`.

## Code style

- Python: formatted and linted with `ruff`, type-checked with `mypy`. Both run
  in CI (`ci.yml`).
- TypeScript/Next.js: linted with the project's `eslint` config and
  type-checked with `tsc`, both run via `npm run lint` / `npm run typecheck` in
  CI.
- Follow `.editorconfig` for whitespace/indentation regardless of language.

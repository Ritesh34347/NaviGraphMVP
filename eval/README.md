# Evaluation Harness

Real, built in Phase 8. Runs a golden set of real business questions through
the entire real NaviGraph pipeline (Understanding -> Query -> all four
Guardrail gates -> real Data Federation against live Snowflake -> Insight),
using a real Anthropic LLM client at every step, and scores each real answer
with a real LLM-as-judge agent (`ops.evaluation_judge`).

## Layout

- `pipeline_chain.py` -- `run_full_pipeline(...)`, the shared, real
  chain-runner every golden question is threaded through. Not reused by
  `tests/integration/insight_pipeline/test_pipeline_chain.py` -- see that
  module's docstring and `DECISIONS.md` for why (that test needs
  deterministic, per-step canned LLM responses to reliably exercise
  specific mechanics; this harness needs a real LLM at every step to
  evaluate real behavior across arbitrary questions).
- `golden_set/*.yaml` -- one real, schema-grounded question per file
  (`question_id`, `question`, `expected_intent`, `expected_entities`,
  `expected_tables`, `expected_columns`). Currently 10 questions, covering
  all four real `IntentLabel` values and four real tables
  (`STAGING_TRANSACTIONS`, `STAGING_CUSTOMER_INFORMATION`,
  `STAGING_ASSET_INFORMATION`, `STAGING_MARKETS`) -- see `LIMITATIONS.md`
  item 33 for why 10, not the 50+ originally targeted.
- `run_harness.py` -- the real CLI runner. Loads every golden question,
  runs it through `run_full_pipeline`, scores the result with
  `ops.evaluation_judge`, and writes `results/<run_id>.json`.
- `results/` -- real run reports (gitignored; each report is a real, dated
  JSON file, not committed).

## Running it

Requires live Postgres/Neo4j/OPA, a real Snowflake account, and
`ANTHROPIC_API_KEY` -- does not skip gracefully if any are unreachable, same
stance as every `tests/integration/` test. Must be run as a module (`python
-m eval.run_harness`, not `python eval/run_harness.py` directly) so `eval`
resolves as a real importable package:

```bash
POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... \
OPA_URL=http://localhost:8181 \
SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=... \
ANTHROPIC_API_KEY=... \
python -m eval.run_harness
```

Optional flags: `--limit N` (run only the first N golden questions),
`--compare-to eval/results/<prior-run>.json` (regression check -- flags a
>=2-point score drop on any dimension, or an `intent_match` flip from
`true` to `false`; see `LIMITATIONS.md` item 34 for why that threshold is a
real, not-yet-validated placeholder).

## Not wired into CI

`.github/workflows/ci.yml` runs only `pytest packages/` and has no
Anthropic/Snowflake secrets configured -- this harness needs both. A
deliberate, logged deferral (`DECISIONS.md`), not an oversight: `eval/`
being real and runnable satisfies this repo's original bar ("runnable in CI
once enough of the pipeline exists"), not automatic CI execution without
real secrets provisioned.

# Runbook: Onboarding a New Data Source

This runbook walks through onboarding a brand-new data source end to end:
registering it, crawling its schema, drafting a candidate Semantic Model
with the Ontology Drafting agent, having a human review that draft, then
compiling and activating it. All five steps are driven by
`tools/scripts/onboard_data_source.py`.

This is Phase 13's real onboarding tooling — see `LIMITATIONS.md` items 38
and 61 for the structural gaps this closes (a re-crawl/drift signal, an
LLM-assisted first draft, and a real path from "raw crawled catalog" to
"an activated, catalog-validated `SemanticModel`" that doesn't require
hand-writing YAML from scratch).

## Prerequisites

- A real, reachable Postgres metadata catalog (`POSTGRES_HOST`/`_PORT`/
  `_USER`/`_PASSWORD`/`_DB` — see `MetadataCatalogSettings`), migrated to
  `head` (`alembic upgrade head` from `packages/metadata_catalog`).
- Real credentials for the source system you're onboarding, exported as
  that connector's own global environment variables (e.g.
  `SnowflakeSettings`'s `SNOWFLAKE_*` vars) — see each connector's own
  `*Settings` class in `navigraph_connectors`. **Known, documented
  limitation, found live wiring Phase 2:** `crawl` constructs a connector
  via `get_connector_class(source_type)()` with NO arguments, matching
  every other real caller in this codebase (Data Source Discovery, Data
  Federation) — it does NOT read `DataSource.connection_ref` or route
  through a per-source `SecretsProvider`. Two `DataSource` rows of the
  same `source_type` are indistinguishable to it; both resolve to a
  connector reading the same global env vars. There is no
  per-`DataSource` credential-routing layer anywhere in this codebase
  yet — `--connection-ref-json` below is stored for a future one, not
  consumed by `crawl` today.
- `ANTHROPIC_API_KEY` for the `draft` step. Without it, `draft` still runs
  but falls back to `FakeLLMClient` (a loud warning is printed) and
  produces an empty draft — fine for rehearsing the pipeline's plumbing,
  useless for a real proposal.
- A real, reachable OPA server for the `activate` step (`OPA_URL`).
- All five `navigraph_*` packages installed (`pip install -e packages/...`
  for `metadata_catalog`, `connector_sdk`, `semantic_model`, `shared`, and
  `agent_runtime` — the last one only `draft` needs).

## Steps

### 1. Register the data source

```bash
python tools/scripts/onboard_data_source.py register \
  --tenant-id acme-corp \
  --name acme_prod_snowflake \
  --source-type snowflake \
  --connection-ref-json '{"secret_scope": "acme-corp-prod"}' \
  --set-default
```

`--connection-ref-json` is that `DataSource`'s own opaque credential
pointer (e.g. `{"secret_scope": "acme-corp-prod"}`) — stored for a future
per-source credential-routing layer, but not read by `crawl` today (see
the Prerequisites section above). `--set-default` is optional; omit it
for a second/third data source on an already-onboarded tenant (see
`set_default_data_source` if you need to change the default later).

### 2. Crawl its schema

```bash
python tools/scripts/onboard_data_source.py crawl \
  --tenant-id acme-corp \
  --data-source-name acme_prod_snowflake
```

Prints which tables are new and which changed since the last crawl (real
schema-hash drift detection, Phase 13.1 — see
`navigraph_catalog.drift`). Re-run this any time; it's the same command a
re-crawl scheduler would call on a timer (see
`navigraph_catalog.api.list_stale_data_sources` for the query such a
scheduler would use to decide *which* data sources are due).

### 3. Draft a candidate Semantic Model

```bash
python tools/scripts/onboard_data_source.py draft \
  --tenant-id acme-corp \
  --data-source-name acme_prod_snowflake \
  --out acme-draft.json
```

Runs the Ontology Drafting agent
(`navigraph_agents.understanding.ontology_drafting`) against the schema
just crawled. It has no access to real data values — only structural
metadata (table/column names, types, nullability, and any existing
business-glossary entries) — and every table/column it references is
validated against the real, closed catalog inventory; a hallucinated
reference is dropped and reported to stderr, never silently kept. Prints
a warning to stderr for every entry it couldn't validate.

### 4. Review the draft BY HAND — do not skip this

Open `acme-draft.json`. Every entity, relationship, sensitive-column, and
metric proposal carries a `rationale` field specifically so you can judge
it. For each proposal:

- **Keep it as-is** if it looks right.
- **Edit it** (rename an entity, fix a `predicate`, correct an
  `aggregation`) if it's close but not quite right — the file is plain
  JSON, hand-editable.
- **Delete the entry entirely** if it's wrong or you're not confident in
  it. A weak proposal is easy to omit later, but a real relationship the
  agent never proposed can't be reviewed at all — when in doubt, the agent
  is instructed to still propose it with a rationale saying so; you decide
  whether that's good enough to keep.

This step has no tooling and is not meant to — a human decision, not an
automated gate, is what makes the result trustworthy enough to compile.

### 5. Compile the reviewed draft into a SemanticModel

```bash
python tools/scripts/onboard_data_source.py compile \
  --draft acme-draft.json \
  --tenant-id acme-corp \
  --data-source-name acme_prod_snowflake \
  --out acme-semantic-model.yaml \
  --version 1
```

Fully offline — no database or network access, so this step can run
anywhere, including on your own laptop after reviewing the draft. Converts
your (edited) draft into a real `SemanticModel` YAML document
(`navigraph_semantic_model.onboarding.compile_draft_to_semantic_model`).
Anything it can't safely place — a sensitive column naming a table no
approved entity binds, a metric naming an entity you deleted in step 4 —
is dropped with a warning printed to stderr, never silently kept or
guessed at. Open `acme-semantic-model.yaml` afterward; it's the same
format `navigraph_semantic_model.load_semantic_model` accepts, so it's
worth a final read before activating.

### 6. Activate it

```bash
python tools/scripts/onboard_data_source.py activate \
  --model acme-semantic-model.yaml
```

Three real steps, in order, against live infrastructure:

1. **Catalog validation** — every `(data_source, table, column)` triple
   the document names is confirmed against the real, currently-crawled
   catalog (`validate_semantic_model_against_catalog`). Any issue aborts
   activation with the full list printed — nothing partially applies.
2. **PII compilation** — every `Entity.sensitive_columns` entry is applied
   as a real `is_pii=true` flag (`compile_sensitivity`). Additive only:
   this never clears an existing PII flag a prior version had that this
   one dropped — see that function's own docstring for why.
3. **OPA policy sync** — `policy_bindings.allowed_roles` is pushed to a
   real per-tenant OPA data document (`sync_policy_bindings`). **Not yet
   read by `authz.rego` as of Phase 2** — that policy still has a static
   `allowed_roles` literal; making it read this per-tenant document is
   Phase 3's job (see `DECISIONS.md`).

Steps 5 and 6 above can also be run as one step, skipping the intermediate
YAML file, via `tools/scripts/navigraph_admin.py`:

```bash
python tools/scripts/navigraph_admin.py semantic-model compile-and-activate \
  --draft acme-draft.json \
  --tenant-id acme-corp \
  --data-source-name acme_prod_snowflake \
  --version 1
```

Use `onboard_data_source.py compile`/`activate` instead when you want to
hand-edit the compiled YAML before activating it; use
`navigraph_admin.py`'s combined command for the common case where the
draft itself (step 4) was your only edit point.

## What this runbook does NOT yet cover

- **Re-validation on a schedule.** `list_stale_data_sources` gives a
  scheduler the query it needs ("which data sources haven't been crawled
  recently"), but no scheduler/cron job actually calls `crawl` on a timer
  yet — this is still a manually-run step.
- **Request-time Semantic Model resolution for SQL generation.** As of
  Phase 1, activating a Semantic Model IS automatically picked up by
  `navigraph_kg`'s ingestion pipeline (`_sync_relationship_concepts` reads
  a tenant's activated model, falling back to the hardcoded
  `ontology.RELATIONSHIP_CONCEPTS` list otherwise) — re-run `crawl`+the
  real ingestion job to see it take effect. The live Request Orchestrator
  still has no mechanism to feed a Semantic Model's `metrics` into
  `SqlGenerationPayload.metric_aggregations` directly, though — see
  `LIMITATIONS.md` item 61's "still open" list. Wiring that up is a
  separate, later step (Phase 5), not part of onboarding itself.
- **`authz.rego` still ignores the synced policy document.** See step 6's
  OPA policy sync note above — Phase 3.
- **No live end-to-end run of this exact runbook has been performed** —
  this sandbox has no reachable Postgres/Snowflake/OPA/Anthropic access to
  run `register`/`crawl`/`activate` against real infrastructure. Each
  subcommand's own logic is covered by real unit tests (crawl via
  `tests/integration/metadata_catalog/test_schema_drift.py`'s live-Postgres
  proof, drafting via `ontology_drafting`'s own test suite, compiling via
  `navigraph_semantic_model`'s `test_onboarding.py`, the real
  validate → tag PII → persist → mark active → sync OPA sequence via
  `test_activation.py`), and the `compile` step specifically has been run
  for real end-to-end in this sandbox (fully offline, no infra required)
  with its output round-tripped back through `load_semantic_model` to
  confirm the YAML it writes is valid. Whoever runs this against a real
  tenant for the first time should treat it as the first genuine
  end-to-end proof of the full chain, not assume one already happened.
  This is also when `crawl`'s connector-construction fix (see
  Prerequisites above) gets its first real exercise -- it was previously
  calling a function that never existed in `connector_sdk` at all and had
  never been run.

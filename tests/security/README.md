# Security Tests

A real adversarial test suite, built in Phase 6 alongside the Guardrail
domain and the real `infra/opa/policies/authz.rego` policy that replaced
the Phase 1 allow-all placeholder. This directory previously stated
explicitly that no adversarial tests could exist yet -- see the bottom of
this file for that original text, kept for history.

## What's here and what it proves

Every test below runs against the real, live docker-compose stack (real
Postgres, real OPA running the real non-allow-all policy) -- never a
`FakeOpaClient`, never a mocked catalog session, unless a file's own
docstring says otherwise.

- **`test_tenant_isolation.py`** -- README requirement #1 (below). A
  `RequestContext` whose `claims.tenant_id` names a *different* tenant than
  the request itself is denied by the real Policy Authorization agent
  calling the real OPA service; a matching-claim control case is allowed.
- **`test_insufficient_roles_fail_closed.py`** -- README requirement #2.
  Empty/unrecognized roles are denied (deny-by-default) even with a
  genuinely matching tenant claim; separately, `PolicyAuthorizationAgent`
  pointed at an unreachable OPA instance fails CLOSED
  (`opa_unreachable`/`recoverable=False`/`authorized=[]`), the deliberate
  opposite of `CachingAgent`'s fail-open convention.
- **`test_opa_policy_adversarial.py`** -- README requirement #3. Calls
  `HttpOpaClient.evaluate` directly against the real policy with a
  parametrized table of adversarial inputs (empty/malformed tenant IDs,
  missing/null claims, empty roles, a tenant-mismatched role-escalation
  attempt) -- every one denied. Also includes a control case proving valid
  input is allowed, and an explicit test documenting a real, deliberately
  out-of-scope gap: a self-declared `roles=["admin"]` WITH a genuinely
  matching tenant claim IS allowed, because this policy has no
  cryptographic identity to check role provenance against (closing that
  requires real Azure AD JWT verification -- see `LIMITATIONS.md`).
- **`test_pii_exposure_denied.py`** -- beyond the three OPA-specific
  minimums (the PII Exposure Checker is its own, separate enforcement
  layer -- see `DECISIONS.md`): an `analyst` role is denied access to the
  real, catalog-tagged PII column (`CUSTOMER_INFORMATION.CUSTOMERID`,
  tagged via `tools/scripts/tag_pii_columns.py`'s Phase 6 backfill), a
  `pii_viewer` role is cleared for the identical statement.

Two real bugs were found and fixed while writing these tests against the
real policy (not assumed correct from reading the Rego):

1. `input.claims` being `null` (missing/malformed) correctly denied via
   `allow=false`, but silently produced an EMPTY `deny_reasons` list --
   `object.get(null, ...)` errors internally in Rego, dropping that
   specific rule instance. Fixed with `default claims := {}` /
   `claims := input.claims if is_object(input.claims)` so a real,
   readable deny reason is always produced.
2. An empty-string `tenant_id` matching an equally empty-string
   `claims.tenant_id` was structurally `==` and therefore **allowed** --
   a real gap. Fixed by requiring `input.tenant_id != ""` explicitly in
   `tenant_claim_matches`, not just equality.

## Running these tests

```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=navigraph \
  POSTGRES_PASSWORD=<local dev password> POSTGRES_DB=navigraph \
  OPA_URL=http://localhost:8181 \
  pytest tests/security -q
```

Like every other `tests/integration/`-style suite in this repo, these do
NOT skip gracefully if Postgres/OPA are unreachable -- they are meant to
run against the actual docker-compose stack, including in CI's
`adversarial-tests` required check.

## The rule this file enforces (unchanged since Phase 1)

**A real adversarial test suite -- at minimum, a tenant isolation / RBAC
bypass test that attempts to read or act on another tenant's data and
asserts it is rejected -- is REQUIRED before any Guardrail work (the OPA
policy engine gaining real Rego rules, the Guardrail agent domain) is ever
marked "done."** That requirement is satisfied as of Phase 6, by the tests
above.

---

<details>
<summary>Original Phase 1 text (kept for history -- the gap it described is closed)</summary>

**This is a marker file, not a stub to quietly delete later.**

### Current state: no adversarial tests exist, and that is expected

As of this phase, there is no security-relevant component in this repository
that an adversarial test could meaningfully exercise:

- The OPA policy engine (`infra/opa/`) runs an **allow-all placeholder
  policy**. There is no real RBAC/ABAC logic to try to bypass yet -- writing
  a "tenant isolation bypass" test against an allow-all policy would pass
  trivially and create false confidence.
- No real authentication is wired into the gateway or agent-runtime. `/ask`
  and `/agents/.../invoke` accept a `tenant_id`/`RequestContext` supplied
  directly by the caller with no verification against an identity provider.
- Only one agent (Intent Understanding) exists, and it does not touch any
  tenant data, schema, or query execution -- there is nothing at the data
  layer yet for a cross-tenant test to probe.

See `LIMITATIONS.md` at the repo root, item 4 ("OPA runs an allow-all
placeholder policy") and item 7 ("Only one real agent exists"), for the
state this rule was guarding against prematurely calling "secure" at the
time this file was originally written.

</details>

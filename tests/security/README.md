# Security Tests

**This is a marker file, not a stub to quietly delete later.**

## Current state: no adversarial tests exist, and that is expected

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

Given all of that, this directory is intentionally empty of test files
right now. An empty `tests/security/` with no explanation would look like an
oversight; this README exists so it reads as a deliberate, tracked gap
instead.

## The rule this file enforces

**A real adversarial test suite -- at minimum, a tenant isolation / RBAC
bypass test that attempts to read or act on another tenant's data and
asserts it is rejected -- is REQUIRED before any Phase 5/6 guardrail work
(the OPA policy engine gaining real Rego rules, the Guardrail agent domain)
is ever marked "done."**

Concretely, before that work is considered complete, this directory must
contain tests that:

1. Construct a `RequestContext` for tenant A and attempt to access, query,
   or influence agent output scoped to tenant B, and assert it is rejected.
2. Attempt to invoke an agent or gateway route with insufficient
   `roles`/`claims` and assert authorization fails closed (deny by default),
   not open.
3. Exercise the real (non-allow-all) OPA policy with adversarial inputs --
   malformed tenant IDs, missing claims, role escalation attempts -- and
   assert every one is denied.

See `LIMITATIONS.md` at the repo root, item 4 ("OPA runs an allow-all
placeholder policy") and item 7 ("Only one real agent exists"), for the
current state this rule is guarding against prematurely calling "secure."

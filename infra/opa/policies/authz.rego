package navigraph.authz

import rego.v1

# Phase 6 real policy -- replaces the Phase 1 allow-all placeholder
# (`placeholder.rego`, deleted; two `default allow` rules for the same
# package across two files is an OPA compile error, so this isn't
# additive). Queried by `HttpOpaClient`/`PolicyAuthorizationAgent` via
# `POST /v1/data/navigraph/authz/decision` -- see
# packages/agent_runtime/navigraph_agents/guardrail/policy_authorization/.
#
# Deliberately RBAC + tenant ABAC ONLY. Column-level PII enforcement is a
# SEPARATE enforcement layer -- the PII Exposure Checker agent, which
# checks `CatalogColumn.is_pii` directly against the live Postgres
# catalog. This policy has no live-data-API integration to pull catalog
# facts into OPA's `data` document (infra/opa/conf/config.yaml runs
# bundle-less, policies-only), so it never sees column-level detail at
# all -- see DECISIONS.md for the full rationale.
#
# Deny-by-default: nothing is authorized unless `allow` fires below.
default allow := false

# RBAC: which application-level roles may query at all, for THIS request's
# tenant specifically. Phase 3 of the configurable-platform build plan:
# reads the real per-tenant document `navigraph_semantic_model.opa_sync
# .sync_policy_bindings` writes via `OpaClient.set_data` at
# `navigraph/tenants/<tenant_id>` -- previously a static literal here,
# identical for every tenant regardless of what (if anything) it had
# activated.
#
# FAIL-CLOSED, DELIBERATELY: a tenant with no synced document at all
# (never onboarded, or `sync_policy_bindings` hasn't run yet) resolves to
# an EMPTY set via the `default` below, not a fallback allow-list --
# `data.navigraph.tenants[input.tenant_id].allowed_roles` is simply
# undefined for an unknown tenant, which makes the non-default rule body
# below undefined too, so the default fires. An explicit empty
# `policy_bindings.allowed_roles` (a deliberate lockout, see
# `navigraph_semantic_model.contracts.PolicyBindings`'s own docstring) is
# indistinguishable from "never synced" here, and that is correct: both
# really do mean "no role is currently authorized for this tenant."
#
# ROLLOUT REQUIREMENT, NOT OPTIONAL: every tenant that was relying on the
# old static `{"analyst", "pii_viewer", "admin"}` literal needs a real
# `sync_policy_bindings` call (with that same role set, to change nothing
# for them) BEFORE this policy is deployed live, or they lock themselves
# out at deploy time -- see `tools/scripts/seed_semantic_model_from_ontology.py`,
# extended in this same phase to do exactly that for `navikenz-poc`/
# `ecommerce-poc`. See DECISIONS.md for why this is fail-closed rather
# than falling back to a default allow-list.
default allowed_roles := []

allowed_roles := data.navigraph.tenants[input.tenant_id].allowed_roles

# `input.roles` may be missing, null, or not an array on a malformed
# request -- default to an empty set rather than letting `some role in
# input.roles` error or silently produce undefined for the whole rule.
default roles := []

roles := input.roles if is_array(input.roles)

role_allowed if {
	some role in roles
	role in allowed_roles
}

# `input.claims` may be missing or null on a malformed/adversarial request
# (confirmed live via tests/security/test_opa_policy_adversarial.py's
# `claims_is_missing_entirely` case) -- default to an empty object rather
# than letting `object.get(input.claims, ...)` below error on a non-object
# value. Without this, a null `claims` still correctly denied (dot-access
# on null is merely undefined in Rego), but produced NO deny_reason at all
# -- a real, if minor, audit-trail gap this fix closes so every real
# denial has an explanatory reason.
default claims := {}

claims := input.claims if is_object(input.claims)

# ABAC: tenant isolation. The caller's identity claim must match the
# tenant_id the request is actually scoped to -- this is the literal
# target of tests/security/test_tenant_isolation.py.
#
# `input.tenant_id != ""` is required explicitly, not just implied by the
# equality check below: an empty-string tenant_id matching an equally
# empty-string claim would otherwise structurally satisfy `==` and be
# allowed -- a real gap caught live via
# tests/security/test_opa_policy_adversarial.py's `empty_tenant_id` case
# before this policy shipped.
#
# KNOWN, DOCUMENTED GAP (see LIMITATIONS.md): `input.claims` is whatever
# RequestContext.claims the caller supplied -- there is no real Azure AD
# token verification yet populating it from a cryptographically verified
# identity. This policy can only check that a claimed tenant_id matches;
# it cannot detect a caller lying about their own claims. Real Azure AD
# JWT validation (a separate, deferred phase) is what makes this claim
# trustworthy, not this policy.
tenant_claim_matches if {
	input.tenant_id != ""
	claims.tenant_id == input.tenant_id
}

allow if {
	role_allowed
	tenant_claim_matches
}

deny_reasons contains reason if {
	not role_allowed
	reason := sprintf("no role in %v is authorized (allowed: %v)", [roles, allowed_roles])
}

deny_reasons contains reason if {
	not tenant_claim_matches
	reason := sprintf(
		"claims.tenant_id (%v) does not match request tenant_id (%v)",
		[object.get(claims, "tenant_id", null), input.tenant_id],
	)
}

decision := {"allow": allow, "deny_reasons": deny_reasons}

package navigraph.authz

# PLACEHOLDER POLICY — allow-all.
#
# This exists so the OPA policy-decision-point is wired into the request
# path (gateway/agent-runtime call out to OPA for every authorization
# decision) from day one. The actual RBAC/ABAC and row-/column-level
# authorization logic is a dedicated later phase, and per LIMITATIONS.md
# item 4, that phase is not considered done until it ships with an
# adversarial test suite (see tests/security/) that exercises attempted
# tenant-isolation and privilege-escalation bypasses.
#
# Do NOT deploy this policy anywhere real data is reachable.

default allow = true

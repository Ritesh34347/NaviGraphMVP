# Security Policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in NaviGraph, please
report it privately rather than opening a public issue.

**Contact**: `security@navigraph.internal`

> This address is a placeholder created during initial repository scaffolding.
> It does not yet route to a real, monitored inbox — a real security contact
> (mailbox, on-call rotation, or ticketing alias) must be provisioned before
> this policy can be relied upon in practice. Until then, report suspected
> vulnerabilities directly to a member of `@navigraph/security-team` (see
> `CODEOWNERS`).

Please include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof-of-concept if available.
- The affected component (e.g. gateway, agent runtime, a specific agent,
  infra configuration).
- Whether the issue involves cross-tenant data exposure — flag this
  explicitly, as it is treated with the highest priority (see below).

We will acknowledge reports and work with you on a coordinated disclosure
timeline. Please do not publicly disclose a vulnerability until we've had a
reasonable opportunity to address it.

## Severity policy

NaviGraph is a multi-tenant platform handling customer business data. As such:

- **Any bug that allows one tenant to access another tenant's data, credentials,
  or query results is treated as P0**, regardless of how it was
  discovered or how narrow the reproduction conditions are.
- **Any bug that allows a user to bypass RBAC/ABAC authorization
  (including the OPA policy layer) to perform an action or read data they
  should not be able to is treated as P0.**
- P0 issues are addressed ahead of all other in-flight work, including
  feature work and other bug fixes.

## Scope

This policy covers this repository and the services it defines: the gateway,
the agent runtime and its agents, the web UI, and the infra/Terraform
configuration used to deploy them. It does not cover third-party services we
depend on (Anthropic, Snowflake, Azure) — please report issues in those
directly to their respective vendors.

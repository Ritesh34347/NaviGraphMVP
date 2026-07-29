"""Execution Planning agent: the safety gate between optimized SQL and
actual execution. Validates that every statement is a single, read-only
`SELECT`/`WITH ... SELECT` before it is allowed to become an
`ExecutionPlan` -- anything that fails that check is routed to
`ExecutionPlanningResult.rejected` and structurally cannot reach
`ExecutionPlanningResult.plans`. Fully deterministic (pure function of its
input) -- see agent.py for the implementation."""

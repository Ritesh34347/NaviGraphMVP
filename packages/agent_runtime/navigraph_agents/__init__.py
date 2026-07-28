"""NaviGraph agent-runtime service.

Hosts the agent registry (see `registry.py`) and serves each registered
agent over HTTP (see `main.py`). Currently exactly one agent is registered:
`understanding.intent_understanding`. The remaining ~24 planned agents
across the Query, Insight, Guardrail, Ops, and Orchestrator domains are
deliberately out of scope for this phase -- see LIMITATIONS.md at the repo
root.
"""

__version__ = "0.1.0"

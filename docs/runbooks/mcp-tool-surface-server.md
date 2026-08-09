# Runbook: NaviGraph MCP Tool-Surface Server

`packages/mcp_server` (`navigraph-mcp-server`) is Phase 14.2's "agentic
tool-surface API": a real [MCP](https://modelcontextprotocol.io) server
wrapping the gateway's `POST /ask` (and `GET /healthz`) as tools, so an
external agentic client — Claude Desktop, another agent framework that
speaks MCP — can call NaviGraph exactly like any other tool.

## What it exposes

- **`ask_navigraph(question, tenant_id, user_id, session_id=None, roles=None)`**
  — asks NaviGraph a real question and returns the gateway's actual
  `RequestOrchestratorOutput` JSON. Pass back the `session_id` from a
  previous call to continue the same conversation (needed to answer a
  `needs_clarification` follow-up).
- **`check_navigraph_health()`** — a lightweight reachability check.

Both tools return a structured `{"ok": false, "error": ...}` result on a
gateway/network failure rather than raising — a calling agent always gets
back *something* it can reason about, never a raw transport exception.

## Running it

This server speaks MCP over **stdio** — it's meant to be *spawned* by an
MCP client as a subprocess, not run as a standalone network service (no
Dockerfile/compose entry exists for it, unlike this repo's other
services, for exactly that reason).

```bash
pip install -e packages/shared
pip install -e packages/mcp_server
GATEWAY_BASE_URL=http://localhost:8000 navigraph-mcp-server
```

`GATEWAY_BASE_URL` defaults to `http://gateway:8000` (the docker-compose
in-network address) — override it for a local, non-compose gateway. The
console script above is registered by `pyproject.toml`'s
`[project.scripts]`; `python -m navigraph_mcp_server` works identically.

### Configuring Claude Desktop (or another MCP client) to use it

Add an entry to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "navigraph": {
      "command": "navigraph-mcp-server",
      "env": { "GATEWAY_BASE_URL": "http://localhost:8000" }
    }
  }
}
```

Restart Claude Desktop; `ask_navigraph`/`check_navigraph_health` should
appear as available tools. Any MCP-speaking client that can spawn a stdio
subprocess works the same way — the config shape above is Claude
Desktop's specifically, but the server itself is client-agnostic.

## What has and hasn't been verified

- **Verified for real, in this sandbox**: the server was spawned as a real
  subprocess and driven over a genuine stdio MCP transport by the `mcp`
  SDK's own client (`mcp.client.stdio.stdio_client` + `ClientSession`) —
  a real `initialize` handshake followed by a real `list_tools` call
  correctly returned both `ask_navigraph` and `check_navigraph_health`.
  11 unit tests cover both tools' request-forwarding and error-handling
  logic against a faked gateway (`httpx.MockTransport`, not a stub of the
  tool functions themselves).
- **NOT verified**: an actual `ask_navigraph` call against a real,
  running gateway (this sandbox has none reachable) — that HTTP path
  itself is exactly the same one `web/src/app/api/ask/route.ts` and the
  gateway's own test suite already prove separately (see Phase 11's
  multi-client-isolation integration test and the gateway's own
  `test_ask.py`), so this server is real, tested glue over an
  already-real, already-tested boundary, not a duplicate of that
  coverage.
- **NOT verified**: Claude Desktop's config shape above against a real
  Claude Desktop install (no GUI in this sandbox) — it follows Anthropic's
  documented `mcpServers` config shape exactly, but hasn't been clicked
  through by a human.

## A real, deliberate dependency pin

`pyproject.toml` pins `mcp>=1.9,<2`, not an unbounded `mcp` dependency.
While building this, `pip install mcp` (no version pin) resolved to
`mcp==2.0.0` — a **different, unrelated package** that isn't the real
Anthropic Model Context Protocol SDK at all (it imports a nonexistent
`mcp_types` module and fails at import). The real SDK (`Home-page`: none,
`Author: Anthropic, PBC.`) only goes up to `1.29.x` on PyPI as of this
writing. This is a live example of PyPI name-squatting risk, caught by
actually trying to import the package rather than trusting a version
number — see `pyproject.toml`'s own comment and `DECISIONS.md` for the
full note.

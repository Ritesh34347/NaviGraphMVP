"""Real unit tests for the Session/Context Manager agent -- no real Redis,
ever.

`_FakeCacheClient` is a plain in-memory dict-backed stand-in satisfying
`CacheClientProtocol` (`get`/`set`), exactly the shape a real `redis.Redis`
instance has -- see `agent.py`'s module docstring, and mirroring
`query.caching`'s own unit-test convention exactly.

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import builtins

from navigraph_shared.contracts import RequestContext

from navigraph_agents.orchestrator.session_context_manager.agent import (
    SessionContextManagerAgent,
    build_cache_key,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    ConversationTurn,
    SessionContextManagerInput,
    SessionContextManagerPayload,
)


class _FakeCacheClient:
    """In-memory dict-backed stand-in for a real `redis.Redis` client."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._store[key] = value
        self.set_calls.append((key, value, ex))

    @property
    def stored_keys(self) -> builtins.set[str]:
        return builtins.set(self._store.keys())


class _RaisingCacheClient:
    """A cache client that always fails -- simulates Redis being
    unreachable."""

    def get(self, key: str) -> bytes | None:
        raise ConnectionError("redis unreachable")

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        raise ConnectionError("redis unreachable")


def _request_context(tenant_id: str = "tenant-acme") -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _turn(turn_id: str, question: str = "How many units sold?") -> ConversationTurn:
    return ConversationTurn(
        turn_id=turn_id,
        raw_question=question,
        resolved_question=question,
        intent="metric_lookup",
        entities=[],
    )


def _get_input(
    *, tenant_id: str = "tenant-acme", session_id: str = "session-1"
) -> SessionContextManagerInput:
    return SessionContextManagerInput(
        request_context=_request_context(tenant_id),
        payload=SessionContextManagerPayload(session_id=session_id, operation="get"),
    )


def _append_input(
    *,
    tenant_id: str = "tenant-acme",
    session_id: str = "session-1",
    new_turn: ConversationTurn | None,
) -> SessionContextManagerInput:
    return SessionContextManagerInput(
        request_context=_request_context(tenant_id),
        payload=SessionContextManagerPayload(
            session_id=session_id,
            operation="append_turn",
            new_turn=new_turn,
        ),
    )


async def test_get_on_missing_key_returns_empty_history_with_no_error() -> None:
    agent = SessionContextManagerAgent(cache_client=_FakeCacheClient())

    output = await agent.run(_get_input())

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.conversation_history == []
    assert output.result.turn_count == 0


async def test_append_turn_on_empty_session_persists_and_is_visible_to_a_later_get() -> None:
    client = _FakeCacheClient()
    agent = SessionContextManagerAgent(cache_client=client)

    turn = _turn("turn-1")
    append_output = await agent.run(_append_input(new_turn=turn))

    assert append_output.errors == []
    assert append_output.confidence == 1.0
    assert append_output.result.turn_count == 1
    assert append_output.result.conversation_history == [turn]

    # The same fake cache client, queried again for a fresh "get", must see
    # the turn that was actually written -- not merely trust the first
    # call's own claimed result.
    get_output = await agent.run(_get_input())

    assert get_output.errors == []
    assert get_output.result.turn_count == 1
    assert get_output.result.conversation_history == [turn]


async def test_append_turn_truncates_to_max_stored_turns_keeping_the_most_recent() -> None:
    client = _FakeCacheClient()
    agent = SessionContextManagerAgent(cache_client=client)

    # Mirrors agent.py's private `_MAX_STORED_TURNS = 20` cap -- not
    # imported (this codebase's tests hardcode a private module constant's
    # literal value rather than reaching into it, exactly like
    # follow_up_suggestion's tests hardcode its private `_MAX_SUGGESTIONS`
    # cap of 3 rather than importing it).
    max_stored_turns = 20
    total_turns = max_stored_turns + 3
    last_output = None
    for i in range(total_turns):
        last_output = await agent.run(_append_input(new_turn=_turn(f"turn-{i}")))
        assert last_output.errors == []

    assert last_output is not None
    assert last_output.result.turn_count == max_stored_turns
    stored_ids = [t.turn_id for t in last_output.result.conversation_history]
    expected_ids = [f"turn-{i}" for i in range(total_turns - max_stored_turns, total_turns)]
    assert stored_ids == expected_ids

    # Confirm against a fresh "get" too, not just the append call's own
    # returned result.
    get_output = await agent.run(_get_input())
    assert [t.turn_id for t in get_output.result.conversation_history] == expected_ids


async def test_append_turn_with_no_new_turn_is_a_non_recoverable_invalid_payload_error() -> None:
    agent = SessionContextManagerAgent(cache_client=_FakeCacheClient())

    output = await agent.run(_append_input(new_turn=None))

    assert output.result.conversation_history == []
    assert output.result.turn_count == 0
    assert len(output.errors) == 1
    assert output.errors[0].code == "invalid_append_turn_payload"
    assert output.errors[0].recoverable is False
    assert output.confidence == 0.0


async def test_backend_raising_on_get_becomes_recoverable_error_with_empty_fallback() -> None:
    agent = SessionContextManagerAgent(cache_client=_RaisingCacheClient())

    output = await agent.run(_get_input())

    assert output.result.conversation_history == []
    assert output.result.turn_count == 0
    assert len(output.errors) == 1
    assert output.errors[0].code == "session_backend_unavailable"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_backend_raising_on_append_becomes_recoverable_error_with_empty_fallback() -> None:
    agent = SessionContextManagerAgent(cache_client=_RaisingCacheClient())

    output = await agent.run(_append_input(new_turn=_turn("turn-1")))

    assert output.result.conversation_history == []
    assert output.result.turn_count == 0
    assert len(output.errors) == 1
    assert output.errors[0].code == "session_backend_unavailable"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


def test_tenant_prefix_is_literally_present_and_correct_in_cache_key() -> None:
    """Direct, structural assertion on the real tenant-isolation design
    point: `tenant_id` is a literal prefix SEGMENT of the key, not merely
    folded into a hash alongside everything else."""

    key = build_cache_key(tenant_id="tenant-acme", session_id="session-1")
    parts = key.split(":")
    assert parts[0] == "navigraph"
    assert parts[1] == "v1"
    assert parts[2] == "tenant-acme"
    assert parts[3] == "session"
    assert parts[4] == "session-1"


async def test_different_tenants_with_same_session_id_isolate_cache_keys_and_history() -> None:
    """Real, direct proof of tenant isolation: two tenants using the exact
    same (client-generated) `session_id` must never collide on the same
    cache key or see each other's conversation history."""

    client = _FakeCacheClient()
    agent = SessionContextManagerAgent(cache_client=client)

    await agent.run(
        _append_input(tenant_id="tenant-a", session_id="shared-session", new_turn=_turn("a-turn-1"))
    )
    await agent.run(
        _append_input(tenant_id="tenant-b", session_id="shared-session", new_turn=_turn("b-turn-1"))
    )

    key_a = build_cache_key(tenant_id="tenant-a", session_id="shared-session")
    key_b = build_cache_key(tenant_id="tenant-b", session_id="shared-session")

    assert key_a != key_b
    # Inspect the fake cache's own key set directly, not just the agent's
    # returned results.
    assert key_a in client.stored_keys
    assert key_b in client.stored_keys

    get_a = await agent.run(_get_input(tenant_id="tenant-a", session_id="shared-session"))
    get_b = await agent.run(_get_input(tenant_id="tenant-b", session_id="shared-session"))

    assert [t.turn_id for t in get_a.result.conversation_history] == ["a-turn-1"]
    assert [t.turn_id for t in get_b.result.conversation_history] == ["b-turn-1"]

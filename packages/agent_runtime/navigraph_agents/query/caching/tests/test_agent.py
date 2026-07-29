"""Real unit tests for the Caching agent -- no real Redis, ever.

`_FakeCacheClient` is a plain in-memory dict-backed stand-in satisfying
`CacheClientProtocol` (`get`/`set`), exactly the shape a real `redis.Redis`
instance has -- see `agent.py`'s module docstring for why this agent takes
that protocol via dependency injection instead of depending on `redis`
directly.

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext

from navigraph_agents.query.caching.agent import CachingAgent, build_cache_key
from navigraph_agents.query.caching.contracts import CachingInput, CachingPayload


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


def _lookup_input(*, tenant_id: str = "tenant-acme", data_source_id: str = "ds-1") -> CachingInput:
    return CachingInput(
        request_context=_request_context(tenant_id),
        payload=CachingPayload(
            operation="lookup",
            sql="SELECT * FROM SALES.REVENUE",
            params={"region": "APAC"},
            data_source_id=data_source_id,
        ),
    )


def _store_input(
    *,
    tenant_id: str = "tenant-acme",
    data_source_id: str = "ds-1",
    value: dict | None = None,
    ttl_seconds: int = 300,
) -> CachingInput:
    return CachingInput(
        request_context=_request_context(tenant_id),
        payload=CachingPayload(
            operation="store",
            sql="SELECT * FROM SALES.REVENUE",
            params={"region": "APAC"},
            data_source_id=data_source_id,
            value=value if value is not None else {"columns": ["id"], "rows": [{"id": 1}]},
            ttl_seconds=ttl_seconds,
        ),
    )


async def test_store_then_lookup_round_trip_returns_hit_with_correct_value() -> None:
    client = _FakeCacheClient()
    agent = CachingAgent(cache_client=client)

    store_value = {"columns": ["id", "amount"], "rows": [{"id": 1, "amount": 100}]}
    store_output = await agent.run(_store_input(value=store_value))

    assert store_output.errors == []
    assert store_output.confidence == 1.0
    assert store_output.result.stored is True
    assert store_output.result.hit is False

    # The store call actually reached the injected client with the real
    # TTL, not just a "stored=True" claim.
    assert len(client.set_calls) == 1
    _, serialized_value, ex = client.set_calls[0]
    assert ex == 300
    assert json.loads(serialized_value) == store_value

    lookup_output = await agent.run(_lookup_input())

    assert lookup_output.errors == []
    assert lookup_output.confidence == 1.0
    assert lookup_output.result.hit is True
    assert lookup_output.result.cached_value == store_value
    # Store and lookup for the identical (sql, params, data_source_id,
    # tenant_id) fingerprint must land on the exact same cache key.
    assert lookup_output.result.cache_key == store_output.result.cache_key


async def test_lookup_on_empty_cache_returns_miss() -> None:
    agent = CachingAgent(cache_client=_FakeCacheClient())

    output = await agent.run(_lookup_input())

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.hit is False
    assert output.result.cached_value is None
    assert output.result.stored is False


def test_tenant_prefix_is_literally_present_and_correct_in_cache_key() -> None:
    """Direct, structural assertion on the real tenant-isolation design
    point: `tenant_id` is a literal prefix SEGMENT of the key, not merely
    folded into the hash alongside everything else."""

    key = build_cache_key(
        tenant_id="tenant-acme",
        policy_version="none",
        sql="SELECT * FROM SALES.REVENUE",
        params={},
        data_source_id="ds-1",
    )

    parts = key.split(":")
    assert parts[0] == "navigraph"
    assert parts[1] == "v1"
    assert parts[2] == "tenant-acme"
    assert parts[3] == "query_cache"
    assert parts[4] == "policy=none"
    # The final segment is the sha256 hex digest of the canonical
    # fingerprint -- 64 lowercase hex characters.
    digest = parts[5]
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


async def test_different_tenants_produce_different_cache_keys_for_identical_query() -> None:
    """Real, direct proof of tenant isolation at the cache-key level: two
    tenants issuing the byte-for-byte identical (sql, params,
    data_source_id) must never collide on the same cache key."""

    agent = CachingAgent(cache_client=_FakeCacheClient())

    output_tenant_a = await agent.run(_lookup_input(tenant_id="tenant-a", data_source_id="ds-1"))
    output_tenant_b = await agent.run(_lookup_input(tenant_id="tenant-b", data_source_id="ds-1"))

    assert output_tenant_a.result.cache_key != output_tenant_b.result.cache_key
    # Same query fingerprint, different tenant -- confirm it's specifically
    # the tenant segment that differs, not something else incidental.
    assert "tenant-a" in output_tenant_a.result.cache_key
    assert "tenant-b" in output_tenant_b.result.cache_key
    assert output_tenant_a.result.cache_key.replace("tenant-a", "tenant-b") == (
        output_tenant_b.result.cache_key
    )


async def test_client_raising_on_lookup_becomes_recoverable_error_not_a_crash() -> None:
    agent = CachingAgent(cache_client=_RaisingCacheClient())

    output = await agent.run(_lookup_input())

    assert output.result.hit is False
    assert output.result.cached_value is None
    assert len(output.errors) == 1
    assert output.errors[0].code == "cache_backend_unavailable"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_client_raising_on_store_becomes_recoverable_error_not_a_crash() -> None:
    agent = CachingAgent(cache_client=_RaisingCacheClient())

    output = await agent.run(_store_input())

    assert output.result.stored is False
    assert len(output.errors) == 1
    assert output.errors[0].code == "cache_backend_unavailable"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.5


async def test_store_without_value_is_a_non_recoverable_invalid_payload_error() -> None:
    """`value` is required for `operation="store"` -- omitting it is a
    caller-side contract violation, not a cache-backend failure, so it is
    NOT the recoverable `cache_backend_unavailable` code."""

    agent = CachingAgent(cache_client=_FakeCacheClient())

    # Built directly (not via `_store_input`, which substitutes a default
    # value when `value` is falsy) so `value` is genuinely `None` here.
    from navigraph_agents.query.caching.contracts import CachingPayload as _Payload

    input_ = CachingInput(
        request_context=_request_context(),
        payload=_Payload(
            operation="store",
            sql="SELECT * FROM SALES.REVENUE",
            params={},
            data_source_id="ds-1",
            value=None,
        ),
    )
    output = await agent.run(input_)

    assert output.result.stored is False
    assert len(output.errors) == 1
    assert output.errors[0].code == "invalid_store_payload"
    assert output.errors[0].recoverable is False
    assert output.confidence == 0.0

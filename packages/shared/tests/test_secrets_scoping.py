"""Unit tests for `build_secret_scope`.

The real property under test: the same guarantee `DataSource`'s
`UniqueConstraint("tenant_id", "name")` already gives the catalog -- no two
distinct (tenant_id, data_source_name) pairs may ever build the same scope
string -- must hold for every input this function is likely to see,
including inputs that would collide under a naive single-character
separator.
"""

from __future__ import annotations

from navigraph_shared.secrets.scoping import build_secret_scope


def test_build_secret_scope_basic_shape() -> None:
    assert (
        build_secret_scope(tenant_id="navikenz-poc", data_source_name="fidelity_snowflake")
        == "navikenz-poc__fidelity_snowflake"
    )


def test_build_secret_scope_lowercases_input() -> None:
    assert (
        build_secret_scope(tenant_id="Acme-Corp", data_source_name="Snowflake_Prod")
        == "acme-corp__snowflake_prod"
    )


def test_build_secret_scope_is_deterministic() -> None:
    a = build_secret_scope(tenant_id="acme-corp", data_source_name="snowflake_prod")
    b = build_secret_scope(tenant_id="acme-corp", data_source_name="snowflake_prod")
    assert a == b


def test_build_secret_scope_different_tenants_never_collide() -> None:
    a = build_secret_scope(tenant_id="tenant-a", data_source_name="snowflake")
    b = build_secret_scope(tenant_id="tenant-b", data_source_name="snowflake")
    assert a != b


def test_build_secret_scope_different_names_for_same_tenant_never_collide() -> None:
    a = build_secret_scope(tenant_id="acme-corp", data_source_name="snowflake_prod")
    b = build_secret_scope(tenant_id="acme-corp", data_source_name="snowflake_dev")
    assert a != b


def test_build_secret_scope_underscore_in_component_does_not_cause_collision() -> None:
    """Without a separator wider than a single underscore, tenant="a_b"
    name="c" and tenant="a" name="b_c" would both naively join to "a_b_c".
    The double-underscore separator must keep these distinct."""
    first = build_secret_scope(tenant_id="a_b", data_source_name="c")
    second = build_secret_scope(tenant_id="a", data_source_name="b_c")
    assert first != second


def test_build_secret_scope_sanitizes_unsafe_characters() -> None:
    result = build_secret_scope(tenant_id="acme corp!", data_source_name="prod (eu)")
    assert result == "acme_corp__prod_eu"

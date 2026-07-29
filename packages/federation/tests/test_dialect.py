"""Real tests for `navigraph_federation.dialect` -- no mocking, since these
are pure string/regex functions with no external dependency."""

from __future__ import annotations

import pytest
from navigraph_federation.dialect import rewrite_sql_for_trino, to_trino_qualified_name


def test_to_trino_qualified_name_lowercases_and_prefixes_catalog() -> None:
    assert (
        to_trino_qualified_name("ANALYTICS.CUSTOMERS", catalog="snowflake")
        == "snowflake.analytics.customers"
    )


def test_to_trino_qualified_name_rejects_wrong_part_count() -> None:
    with pytest.raises(ValueError, match="SCHEMA.TABLE"):
        to_trino_qualified_name("CUSTOMERS", catalog="snowflake")

    with pytest.raises(ValueError, match="SCHEMA.TABLE"):
        to_trino_qualified_name("A.B.C", catalog="snowflake")


def test_rewrite_sql_for_trino_simple_from_clause() -> None:
    sql = "SELECT id, amount FROM ANALYTICS.REVENUE"
    rewritten = rewrite_sql_for_trino(sql, catalog="snowflake")

    assert rewritten == "SELECT id, amount FROM snowflake.analytics.revenue"


def test_rewrite_sql_for_trino_join_clause_with_aliases() -> None:
    sql = (
        "SELECT c.id, r.amount "
        "FROM ANALYTICS.CUSTOMERS c "
        "JOIN ANALYTICS.REVENUE r ON c.id = r.customer_id"
    )
    rewritten = rewrite_sql_for_trino(sql, catalog="snowflake")

    assert (
        rewritten
        == "SELECT c.id, r.amount "
        "FROM snowflake.analytics.customers c "
        "JOIN snowflake.analytics.revenue r ON c.id = r.customer_id"
    )
    # The alias-qualified column references must survive untouched -- they
    # share the same two-part dotted shape as a table reference, but only
    # occurrences right after FROM/JOIN are rewritten.
    assert "c.id" in rewritten
    assert "r.customer_id" in rewritten


def test_rewrite_sql_for_trino_comma_separated_from_list() -> None:
    sql = "SELECT * FROM SALES.ORDERS, SALES.CUSTOMERS"
    rewritten = rewrite_sql_for_trino(sql, catalog="snowflake")

    assert rewritten == "SELECT * FROM snowflake.sales.orders, snowflake.sales.customers"


def test_rewrite_sql_for_trino_does_not_touch_string_literal_that_looks_like_schema_table() -> None:
    """Edge case: a WHERE clause comparing a column to a string literal that
    happens to look exactly like a SCHEMA.TABLE reference must not be
    rewritten -- only real, unquoted table references after FROM/JOIN are."""

    sql = "SELECT * FROM ANALYTICS.REVENUE WHERE label = 'ANALYTICS.REVENUE'"
    rewritten = rewrite_sql_for_trino(sql, catalog="snowflake")

    assert rewritten == (
        "SELECT * FROM snowflake.analytics.revenue WHERE label = 'ANALYTICS.REVENUE'"
    )


def test_rewrite_sql_for_trino_leaves_already_qualified_names_untouched() -> None:
    sql = "SELECT * FROM snowflake.analytics.revenue"
    rewritten = rewrite_sql_for_trino(sql, catalog="snowflake")

    assert rewritten == sql


def test_rewrite_sql_for_trino_is_idempotent() -> None:
    sql = "SELECT * FROM ANALYTICS.REVENUE r JOIN ANALYTICS.CUSTOMERS c ON r.id = c.id"
    once = rewrite_sql_for_trino(sql, catalog="snowflake")
    twice = rewrite_sql_for_trino(once, catalog="snowflake")

    assert once == twice

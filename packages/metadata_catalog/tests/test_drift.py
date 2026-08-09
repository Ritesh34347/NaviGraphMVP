"""Unit tests for `navigraph_catalog.drift.compute_table_schema_hash`.

Pure function, no session/database involved.
"""

from __future__ import annotations

from navigraph_catalog.drift import compute_table_schema_hash
from navigraph_connectors.base import ColumnDescriptor, TableDescriptor


def _table(**overrides: object) -> TableDescriptor:
    defaults: dict[str, object] = dict(
        name="orders",
        row_count_estimate=100,
        columns=[
            ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
            ColumnDescriptor(name="total", data_type="NUMBER", nullable=True, ordinal_position=2),
        ],
    )
    defaults.update(overrides)
    return TableDescriptor(**defaults)


def test_identical_tables_hash_identically() -> None:
    assert compute_table_schema_hash(_table()) == compute_table_schema_hash(_table())


def test_a_renamed_table_hashes_differently() -> None:
    assert compute_table_schema_hash(_table(name="orders")) != compute_table_schema_hash(
        _table(name="orders_v2")
    )


def test_an_added_column_hashes_differently() -> None:
    base = compute_table_schema_hash(_table())
    with_new_column = compute_table_schema_hash(
        _table(
            columns=[
                ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
                ColumnDescriptor(name="total", data_type="NUMBER", nullable=True, ordinal_position=2),
                ColumnDescriptor(name="status", data_type="TEXT", nullable=True, ordinal_position=3),
            ]
        )
    )
    assert base != with_new_column


def test_a_retyped_column_hashes_differently() -> None:
    base = compute_table_schema_hash(_table())
    retyped = compute_table_schema_hash(
        _table(
            columns=[
                ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
                # NUMBER -> FLOAT: a real, meaningful type change.
                ColumnDescriptor(name="total", data_type="FLOAT", nullable=True, ordinal_position=2),
            ]
        )
    )
    assert base != retyped


def test_a_nullability_change_hashes_differently() -> None:
    base = compute_table_schema_hash(_table())
    changed_nullability = compute_table_schema_hash(
        _table(
            columns=[
                ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
                ColumnDescriptor(name="total", data_type="NUMBER", nullable=False, ordinal_position=2),
            ]
        )
    )
    assert base != changed_nullability


def test_column_order_in_the_input_list_does_not_affect_the_hash() -> None:
    """Sorted by ordinal_position before hashing -- the crawl order a
    connector happens to return columns in must never matter."""

    forward = _table(
        columns=[
            ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
            ColumnDescriptor(name="total", data_type="NUMBER", nullable=True, ordinal_position=2),
        ]
    )
    reversed_input = _table(
        columns=[
            ColumnDescriptor(name="total", data_type="NUMBER", nullable=True, ordinal_position=2),
            ColumnDescriptor(name="id", data_type="INTEGER", nullable=False, ordinal_position=1),
        ]
    )
    assert compute_table_schema_hash(forward) == compute_table_schema_hash(reversed_input)


def test_row_count_estimate_never_affects_the_hash() -> None:
    """Row counts grow every day without the table's real STRUCTURE
    changing -- including this in the hash would make every crawl look
    like a structural change even when nothing structural did."""

    assert compute_table_schema_hash(_table(row_count_estimate=100)) == compute_table_schema_hash(
        _table(row_count_estimate=999999)
    )


def test_column_description_never_affects_the_hash() -> None:
    """A DB comment being edited is not a structural change."""

    with_description = _table(
        columns=[
            ColumnDescriptor(
                name="id",
                data_type="INTEGER",
                nullable=False,
                ordinal_position=1,
                description="primary key",
            ),
            ColumnDescriptor(name="total", data_type="NUMBER", nullable=True, ordinal_position=2),
        ]
    )
    assert compute_table_schema_hash(_table()) == compute_table_schema_hash(with_description)

"""Unit tests for `SnowflakeConnector` and `build_connect_kwargs`.

`snowflake.connector.connect` is mocked throughout via `unittest.mock.patch`
so these tests never touch a real Snowflake account. `patch()` imports
`snowflake.connector` itself as needed to resolve the dotted path, so no
explicit import of it is required here. The production code imports
`snowflake.connector` lazily inside `SnowflakeConnector._connect()`; tests
aren't bound by that discipline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_private_key
from navigraph_connectors.base import ConnectorCapabilities
from navigraph_connectors.snowflake.auth import build_connect_kwargs
from navigraph_connectors.snowflake.connector import SnowflakeConnector
from navigraph_connectors.snowflake.settings import SnowflakeSettings


def _password_settings() -> SnowflakeSettings:
    return SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_warehouse="wh-1",
        snowflake_database="db-1",
        snowflake_role="role-1",
        snowflake_auth_method="password",
        snowflake_password="hunter2",
    )


def test_build_connect_kwargs_password_auth() -> None:
    kwargs = build_connect_kwargs(_password_settings())

    assert kwargs == {
        "account": "acct-1",
        "user": "user-1",
        "warehouse": "wh-1",
        "database": "db-1",
        "role": "role-1",
        "password": "hunter2",
    }


def test_build_connect_kwargs_password_auth_omits_empty_optional_fields() -> None:
    settings = SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_auth_method="password",
        snowflake_password="hunter2",
    )

    kwargs = build_connect_kwargs(settings)

    assert kwargs == {
        "account": "acct-1",
        "user": "user-1",
        "password": "hunter2",
    }


def test_build_connect_kwargs_password_auth_missing_password_raises() -> None:
    settings = SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_auth_method="password",
    )

    with pytest.raises(ValueError, match="snowflake_password"):
        build_connect_kwargs(settings)


def test_build_connect_kwargs_key_pair_auth(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "rsa_key.p8"
    key_path.write_bytes(pem_bytes)

    settings = SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_auth_method="key_pair",
        snowflake_private_key_path=str(key_path),
    )

    kwargs = build_connect_kwargs(settings)

    assert kwargs["account"] == "acct-1"
    assert kwargs["user"] == "user-1"
    assert isinstance(kwargs["private_key"], bytes)

    # Round-trip: the DER bytes we produced must load back as an equivalent
    # RSA private key, proving the key-pair path actually works end-to-end.
    reloaded = load_der_private_key(kwargs["private_key"], password=None)
    assert reloaded.private_numbers() == private_key.private_numbers()


def test_build_connect_kwargs_key_pair_auth_with_passphrase(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"s3cret-pass"),
    )
    key_path = tmp_path / "rsa_key_encrypted.p8"
    key_path.write_bytes(pem_bytes)

    settings = SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_auth_method="key_pair",
        snowflake_private_key_path=str(key_path),
        snowflake_private_key_passphrase="s3cret-pass",
    )

    kwargs = build_connect_kwargs(settings)

    reloaded = load_der_private_key(kwargs["private_key"], password=None)
    assert reloaded.private_numbers() == private_key.private_numbers()


def test_build_connect_kwargs_key_pair_auth_missing_path_raises() -> None:
    settings = SnowflakeSettings(
        snowflake_account="acct-1",
        snowflake_user="user-1",
        snowflake_auth_method="key_pair",
    )

    with pytest.raises(ValueError, match="snowflake_private_key_path"):
        build_connect_kwargs(settings)


def _mock_cursor(fetch_results: list[tuple], description: list[tuple] | None = None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results
    cursor.description = description
    return cursor


def test_test_connection_success() -> None:
    connector = SnowflakeConnector(settings=_password_settings())
    mock_cursor = _mock_cursor([(1,)])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("snowflake.connector.connect", return_value=mock_conn) as mock_connect:
        result = connector.test_connection()

    mock_connect.assert_called_once()
    assert result.success is True
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
    mock_conn.close.assert_called_once()


def test_test_connection_failure_returns_result_not_exception() -> None:
    connector = SnowflakeConnector(settings=_password_settings())

    with patch("snowflake.connector.connect", side_effect=RuntimeError("account locked")):
        result = connector.test_connection()

    assert result.success is False
    assert "account locked" in result.message


def test_test_connection_failure_when_cursor_execute_raises() -> None:
    connector = SnowflakeConnector(settings=_password_settings())
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("network unreachable")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("snowflake.connector.connect", return_value=mock_conn):
        result = connector.test_connection()

    assert result.success is False
    assert "network unreachable" in result.message


def test_introspect_schema_assembles_descriptors_from_cursor_rows() -> None:
    connector = SnowflakeConnector(settings=_password_settings())

    tables_cursor_rows = [
        ("PUBLIC", "REVENUE", 1000),
        ("PUBLIC", "CUSTOMERS", 50),
    ]
    columns_cursor_rows = [
        ("PUBLIC", "REVENUE", "ID", "NUMBER", "NO", 1, None),
        ("PUBLIC", "REVENUE", "AMOUNT", "NUMBER", "YES", 2, "transaction amount"),
        ("PUBLIC", "CUSTOMERS", "ID", "NUMBER", "NO", 1, None),
    ]

    mock_cursor = MagicMock()
    # execute() is called twice (tables query, then columns query);
    # fetchall() must return the matching result set each time.
    mock_cursor.fetchall.side_effect = [tables_cursor_rows, columns_cursor_rows]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("snowflake.connector.connect", return_value=mock_conn):
        schemas = connector.introspect_schema()

    assert len(schemas) == 1
    schema = schemas[0]
    assert schema.name == "PUBLIC"
    assert {t.name for t in schema.tables} == {"REVENUE", "CUSTOMERS"}

    revenue = next(t for t in schema.tables if t.name == "REVENUE")
    assert revenue.row_count_estimate == 1000
    assert [c.name for c in revenue.columns] == ["ID", "AMOUNT"]
    assert revenue.columns[0].nullable is False
    assert revenue.columns[1].nullable is True
    assert revenue.columns[1].description == "transaction amount"
    assert revenue.columns[1].data_type == "NUMBER"

    customers = next(t for t in schema.tables if t.name == "CUSTOMERS")
    assert customers.row_count_estimate == 50
    assert [c.name for c in customers.columns] == ["ID"]

    mock_conn.close.assert_called_once()


def test_execute_query_returns_rows_as_dicts() -> None:
    connector = SnowflakeConnector(settings=_password_settings())

    mock_cursor = _mock_cursor(
        [("Acme", 100), ("Globex", 200)],
        description=[("NAME", None), ("AMOUNT", None)],
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch("snowflake.connector.connect", return_value=mock_conn):
        result = connector.execute_query("SELECT name, amount FROM revenue")

    assert result.columns == ["NAME", "AMOUNT"]
    assert result.rows == [
        {"NAME": "Acme", "AMOUNT": 100},
        {"NAME": "Globex", "AMOUNT": 200},
    ]
    assert result.row_count == 2
    mock_cursor.execute.assert_called_once_with("SELECT name, amount FROM revenue", None)


def test_capabilities_reflect_real_snowflake_support() -> None:
    connector = SnowflakeConnector(settings=_password_settings())

    assert connector.capabilities() == ConnectorCapabilities(
        supports_row_level_security=True,
        supports_column_masking=True,
        supports_query_pushdown=True,
    )


def test_required_settings_declares_the_real_fields() -> None:
    settings = {s.field: s for s in SnowflakeConnector.required_settings()}

    assert settings["snowflake_account"].required is True
    assert settings["snowflake_user"].required is True
    assert settings["snowflake_warehouse"].required is True
    assert settings["snowflake_database"].required is True
    assert settings["snowflake_role"].required is False
    assert settings["snowflake_password"].condition is not None
    assert settings["snowflake_private_key_path"].required is False
    assert settings["snowflake_private_key_path"].condition is not None


def test_required_settings_env_var_is_the_uppercased_field_name() -> None:
    setting = next(
        s for s in SnowflakeConnector.required_settings() if s.field == "snowflake_account"
    )

    assert setting.env_var == "SNOWFLAKE_ACCOUNT"


def test_required_settings_is_callable_without_an_instance() -> None:
    """A manifest is a fact about the connector TYPE, not a specific
    configured instance -- callable as a classmethod, no `settings`
    needed."""

    assert SnowflakeConnector.required_settings() != []

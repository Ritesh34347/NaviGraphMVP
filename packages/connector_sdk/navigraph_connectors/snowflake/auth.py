"""Builds `snowflake.connector.connect(**kwargs)` keyword arguments.

`cryptography` is a normal, always-needed dependency for this specific
module (unlike `snowflake.connector`, which is lazily imported only inside
`connector.py`), so a top-level import here is fine -- it's lightweight and
this module cannot do its job without it.
"""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization

from navigraph_connectors.snowflake.settings import SnowflakeSettings


def build_connect_kwargs(settings: SnowflakeSettings) -> dict[str, Any]:
    """Build the kwargs for `snowflake.connector.connect()` from `settings`.

    Raises:
        ValueError: if required fields for the selected
            `snowflake_auth_method` are missing.
    """

    if not settings.snowflake_account:
        raise ValueError("snowflake_account is required")
    if not settings.snowflake_user:
        raise ValueError("snowflake_user is required")

    kwargs: dict[str, Any] = {
        "account": settings.snowflake_account,
        "user": settings.snowflake_user,
    }
    if settings.snowflake_warehouse:
        kwargs["warehouse"] = settings.snowflake_warehouse
    if settings.snowflake_database:
        kwargs["database"] = settings.snowflake_database
    if settings.snowflake_role:
        kwargs["role"] = settings.snowflake_role

    if settings.snowflake_auth_method == "password":
        if not settings.snowflake_password:
            raise ValueError(
                "snowflake_auth_method is 'password' but snowflake_password is empty"
            )
        kwargs["password"] = settings.snowflake_password
        return kwargs

    if settings.snowflake_auth_method == "key_pair":
        if not settings.snowflake_private_key_path:
            raise ValueError(
                "snowflake_auth_method is 'key_pair' but snowflake_private_key_path is empty"
            )

        with open(settings.snowflake_private_key_path, "rb") as key_file:
            key_bytes = key_file.read()

        passphrase = (
            settings.snowflake_private_key_passphrase.encode("utf-8")
            if settings.snowflake_private_key_passphrase
            else None
        )
        private_key = serialization.load_pem_private_key(key_bytes, password=passphrase)

        # snowflake.connector's `private_key` connect parameter expects the
        # key serialized as unencrypted DER bytes (PKCS8), not the
        # cryptography library's key object itself.
        private_key_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        kwargs["private_key"] = private_key_der
        return kwargs

    # Unreachable given `SnowflakeSettings.snowflake_auth_method`'s Literal
    # type, but keeps mypy/readers honest about exhaustiveness.
    raise ValueError(f"Unknown snowflake_auth_method: {settings.snowflake_auth_method!r}")

"""Runtime registry mapping a `source_type` string to a `Connector` class.

There is no hardcoded enum of source types anywhere in this SDK. A future
`DataSource.source_type` field (owned elsewhere, e.g. a metadata catalog
package) is validated at runtime against whatever has been registered here --
importing a connector's package (e.g. `navigraph_connectors.snowflake`)
registers it as a side effect of that import.

Also holds an optional per-`source_type` SETTINGS FACTORY (LIMITATIONS.md
item 21): previously, every caller constructed a connector with
`get_connector_class(source_type)()` -- zero arguments -- which meant every
connector of a given `source_type` in the whole process shared one
process-wide, env-var-backed credential set, regardless of which real
`DataSource` row the call was actually for. A caller that has a real
`DataSource.connection_ref` carrying a `secret_scope` can now resolve
`get_settings_factory(source_type)` and build that `DataSource`'s own real
`Settings` instance from a `navigraph_shared.secrets.SecretsProvider`, so
two `DataSource` rows of the same `source_type` can hold genuinely distinct
real credentials. A `connection_ref` with no `secret_scope` (or a
`source_type` with no registered factory) is unaffected -- callers keep
falling back to `get_connector_class(source_type)()`'s original,
global-env-var-backed construction exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from navigraph_connectors.base import Connector

if TYPE_CHECKING:
    from navigraph_shared.secrets import SecretsProvider

_REGISTRY: dict[str, type[Connector]] = {}

# connection_ref (a DataSource's own opaque JSON pointer) + a
# SecretsProvider -> a real, connector-specific Settings instance (e.g.
# SnowflakeSettings, PostgresSettings). Deliberately `Any` for the settings
# return type, not a shared base class -- every connector's Settings class
# already has its own distinct fields (mirrors how `Connector.__init__`
# itself takes each connector's own `Settings` type, never a common one).
SettingsFactory = Callable[[dict[str, Any], "SecretsProvider"], Any]

_SETTINGS_FACTORIES: dict[str, SettingsFactory] = {}


def register_connector(
    source_type: str,
    connector_cls: type[Connector],
    settings_factory: SettingsFactory | None = None,
) -> None:
    """Register `connector_cls` as the implementation for `source_type`,
    optionally with a `settings_factory` for real per-`DataSource`
    credential resolution (see `get_settings_factory`).

    Re-registering the same `source_type` overwrites the previous entry
    (useful for tests that register a fake connector under a throwaway
    name). `settings_factory` is optional -- a `source_type` registered
    without one has no per-`DataSource` credential resolution available;
    every real connector this SDK ships (Snowflake, Postgres, Databricks)
    registers one.
    """

    _REGISTRY[source_type] = connector_cls
    if settings_factory is not None:
        _SETTINGS_FACTORIES[source_type] = settings_factory


def get_connector_class(source_type: str) -> type[Connector]:
    """Look up the registered `Connector` class for `source_type`.

    Raises:
        ValueError: if no connector has been registered for `source_type`.
    """

    try:
        return _REGISTRY[source_type]
    except KeyError as exc:
        raise ValueError(
            f"No connector registered for source_type={source_type!r}. "
            f"Registered types: {sorted(_REGISTRY)}"
        ) from exc


def list_registered_source_types() -> list[str]:
    """Return every currently-registered `source_type`, sorted."""

    return sorted(_REGISTRY)


def get_settings_factory(source_type: str) -> SettingsFactory | None:
    """Return `source_type`'s registered `SettingsFactory`, or `None` if
    none was registered (either an unregistered `source_type`, or one
    registered without per-`DataSource` credential support).

    Never raises for an unregistered `source_type` -- unlike
    `get_connector_class`, callers use this to decide WHETHER real
    per-`DataSource` credential resolution is available, not to validate
    that `source_type` itself.
    """

    return _SETTINGS_FACTORIES.get(source_type)

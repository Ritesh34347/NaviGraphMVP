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
`DataSource` row the call was actually for. `build_connector` below
resolves a connector's real, per-`DataSource` `Settings` object from that
`DataSource`'s own `connection_ref` plus an injected
`navigraph_shared.secrets.SecretsProvider`, so two `DataSource` rows of the
same `source_type` can now hold genuinely distinct real credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from navigraph_connectors.base import Connector

if TYPE_CHECKING:
    from navigraph_shared.secrets import SecretsProvider

_REGISTRY: dict[str, type[Connector]] = {}

# connection_ref (this DataSource's own opaque JSON pointer) + a
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
    credential resolution (see `build_connector`).

    Re-registering the same `source_type` overwrites the previous entry
    (useful for tests that register a fake connector under a throwaway
    name). `settings_factory` is optional -- a `source_type` registered
    without one falls back to that connector's own zero-argument,
    global-env-var-backed construction in `build_connector`, matching this
    module's pre-item-21 behavior exactly; every connector this SDK ships
    (Snowflake, Postgres) registers a real one.
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


def build_connector(
    source_type: str,
    *,
    connection_ref: dict[str, Any],
    secrets: "SecretsProvider",
) -> Connector:
    """Construct a real `Connector` instance for one specific `DataSource`.

    This is the real fix for LIMITATIONS.md item 21: `connection_ref`
    (that `DataSource`'s own opaque credential pointer) and `secrets` (an
    injected `SecretsProvider`) together resolve that `DataSource`'s own
    real `Settings`, rather than every connector of this `source_type`
    reading the same process-wide env vars.

    Falls back to `get_connector_class(source_type)()` (this SDK's
    pre-item-21 behavior, unchanged) when no `settings_factory` was
    registered for `source_type` -- a deliberate compatibility path for a
    connector type that hasn't opted in yet, not a silent gap: every
    connector this SDK ships registers a real factory.

    Raises:
        ValueError: if no connector has been registered for `source_type`
            (via `get_connector_class`).
    """

    connector_cls = get_connector_class(source_type)
    settings_factory = _SETTINGS_FACTORIES.get(source_type)
    if settings_factory is None:
        return connector_cls()

    settings = settings_factory(connection_ref, secrets)
    return connector_cls(settings=settings)

"""Runtime registry mapping a `source_type` string to a `Connector` class.

There is no hardcoded enum of source types anywhere in this SDK. A future
`DataSource.source_type` field (owned elsewhere, e.g. a metadata catalog
package) is validated at runtime against whatever has been registered here --
importing a connector's package (e.g. `navigraph_connectors.snowflake`)
registers it as a side effect of that import.
"""

from __future__ import annotations

from navigraph_connectors.base import Connector

_REGISTRY: dict[str, type[Connector]] = {}


def register_connector(source_type: str, connector_cls: type[Connector]) -> None:
    """Register `connector_cls` as the implementation for `source_type`.

    Re-registering the same `source_type` overwrites the previous entry
    (useful for tests that register a fake connector under a throwaway
    name).
    """

    _REGISTRY[source_type] = connector_cls


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

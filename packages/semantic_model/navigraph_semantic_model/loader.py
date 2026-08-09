"""Load a `SemanticModel` from YAML/JSON, and validate it against a real,
live metadata catalog.

Two separate steps, deliberately: `load_semantic_model` only ever does
offline, catalog-free Pydantic validation (can a human-authored or LLM-
drafted document even be parsed into a structurally valid `SemanticModel`
at all -- see `contracts.py`'s own `_internal_references_resolve`).
`validate_semantic_model_against_catalog` is the separate, catalog-aware
step that confirms every `(data_source, table, column)` triple a document
NAMES actually exists as a currently-crawled row in `navigraph_catalog` --
this is the check that makes a Semantic Model trustworthy enough to
compile into a knowledge graph, an OPA data document, or a live SQL
Generation lookup, rather than merely well-formed YAML.

`load_and_validate_semantic_model` composes both and raises
`SemanticModelValidationError` (carrying every issue found, not just the
first) if catalog validation fails -- the fail-closed entry point most
real callers (ingestion, onboarding tooling) should use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from navigraph_catalog.api import (
    find_column,
    get_table,
    list_data_sources,
    mark_columns_pii,
)
from navigraph_catalog.models import DataSource
from sqlalchemy.orm import Session

from navigraph_semantic_model.contracts import SemanticModel


class SemanticModelValidationError(Exception):
    """Raised when a structurally-valid `SemanticModel` fails catalog
    validation. `.issues` carries every problem found, not just the
    first -- a real onboarding reviewer needs the full list in one pass,
    not one exception per re-attempt."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__(
            f"Semantic Model failed catalog validation with {len(issues)} issue(s):\n"
            + "\n".join(f"  - {issue}" for issue in issues)
        )


def load_semantic_model(source: str | Path | dict[str, Any]) -> SemanticModel:
    """Parse a `SemanticModel` from a YAML/JSON file path, or an
    already-loaded dict (e.g. from a future admin API's request body).

    Offline only -- never touches a database. Raises `pydantic.ValidationError`
    (propagated unchanged, not re-wrapped) for anything structurally
    invalid: unknown fields, a relationship naming an undeclared entity, a
    non-COUNT metric with no column, etc.
    """

    if isinstance(source, dict):
        return SemanticModel.model_validate(source)

    path = Path(source)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return SemanticModel.model_validate(raw)


def _split_table_ref(table_ref: str) -> tuple[str, str] | None:
    if "." not in table_ref:
        return None
    schema_name, table_name = table_ref.split(".", 1)
    return schema_name, table_name


def validate_semantic_model_against_catalog(model: SemanticModel, session: Session) -> list[str]:
    """Check every binding in `model` against the real, live catalog for
    `model.tenant_id`. Returns a list of human-readable issues -- empty
    means valid. Never raises for a validation failure (only for a genuine
    catalog/DB error, which is allowed to propagate) -- see
    `load_and_validate_semantic_model` for the fail-closed wrapper most
    callers actually want.
    """

    issues: list[str] = []
    data_sources_by_name: dict[str, DataSource] = {
        ds.name: ds for ds in list_data_sources(session, tenant_id=model.tenant_id)
    }

    def _resolve_table(
        data_source_name: str, table_ref: str, *, context: str | None = None
    ) -> tuple[DataSource, str] | None:
        """Returns `(DataSource, table_name)` if `table_ref` resolves to a
        real, currently-crawled table. If `context` is given, records a
        `context`-prefixed issue on failure; if `context` is `None`, fails
        silently (used by "at least one binding must match" checks, where
        an individual binding's own resolution failure is already reported
        separately -- see `_column_exists_on_any_binding`)."""

        data_source = data_sources_by_name.get(data_source_name)
        if data_source is None:
            if context is not None:
                issues.append(
                    f"{context}: no DataSource named {data_source_name!r} registered "
                    f"for tenant {model.tenant_id!r}"
                )
            return None

        split = _split_table_ref(table_ref)
        if split is None:
            if context is not None:
                issues.append(f"{context}: table {table_ref!r} must be given as 'SCHEMA.TABLE'")
            return None
        schema_name, table_name = split

        table = get_table(
            session, data_source_id=data_source.id, schema_name=schema_name, table_name=table_name
        )
        if table is None:
            if context is not None:
                issues.append(
                    f"{context}: table {table_ref!r} not found in data source "
                    f"{data_source_name!r} (not yet crawled?)"
                )
            return None

        return data_source, table_name

    def _check_binding(context: str, data_source_name: str, table_ref: str, columns: list[str]) -> None:
        resolved = _resolve_table(data_source_name, table_ref, context=context)
        if resolved is None:
            return
        data_source, table_name = resolved
        for column_name in columns:
            column = find_column(
                session,
                data_source_id=data_source.id,
                table_name=table_name,
                column_name=column_name,
            )
            if column is None:
                issues.append(f"{context}: column {column_name!r} not found on {table_ref!r}")

    def _column_exists_on_any_binding(entity_name: str, column_name: str) -> bool:
        entity = model.get_entity(entity_name)
        for binding in entity.bindings:
            # No context -- a binding that itself fails to resolve is
            # already reported separately by the per-binding
            # `_check_binding` call in the main entity loop below, so this
            # "at least one must match" check must not double-report.
            resolved = _resolve_table(binding.data_source, binding.table)
            if resolved is None:
                continue
            data_source, table_name = resolved
            column = find_column(
                session,
                data_source_id=data_source.id,
                table_name=table_name,
                column_name=column_name,
            )
            if column is not None:
                return True
        return False

    for entity in model.entities:
        for binding in entity.bindings:
            _check_binding(
                f"entity {entity.name!r} binding ({binding.data_source}, {binding.table})",
                binding.data_source,
                binding.table,
                [binding.key],
            )
        for sensitivity in entity.sensitive_columns:
            if not _column_exists_on_any_binding(entity.name, sensitivity.column):
                issues.append(
                    f"entity {entity.name!r}: sensitive column {sensitivity.column!r} "
                    "not found on any of this entity's bindings"
                )

    for relationship in model.relationships:
        via = relationship.via
        _check_binding(
            f"relationship {relationship.name!r}",
            via.data_source,
            via.table,
            [via.subject_key, via.object_key],
        )

    for metric in model.metrics:
        if metric.column is None:
            continue
        if not _column_exists_on_any_binding(metric.entity, metric.column):
            issues.append(
                f"metric {metric.name!r}: column {metric.column!r} not found on any "
                f"binding of entity {metric.entity!r}"
            )

    return issues


def load_and_validate_semantic_model(
    source: str | Path | dict[str, Any], session: Session
) -> SemanticModel:
    """Compose `load_semantic_model` + `validate_semantic_model_against_catalog`,
    raising `SemanticModelValidationError` if catalog validation fails.
    The fail-closed entry point real callers (ingestion, onboarding
    tooling) should use rather than calling the two steps separately."""

    model = load_semantic_model(source)
    issues = validate_semantic_model_against_catalog(model, session)
    if issues:
        raise SemanticModelValidationError(issues)
    return model


def compile_sensitivity(model: SemanticModel, session: Session) -> int:
    """Apply every `Entity.sensitive_columns` declaration in `model` as a
    real `is_pii=true` flag via `navigraph_catalog.api.mark_columns_pii` --
    replaces manually running `tools/scripts/tag_pii_columns.py` per
    column list for every entity a Semantic Model already declares.

    Deliberately additive-only: a column this Semantic Model doesn't
    mention is never un-flagged, even across a re-compile that removed a
    `sensitive_columns` entry present in a prior version -- clearing a
    real PII flag is a decision serious enough that it should be a
    deliberate, reviewed action (a real `set_default_data_source`-style
    explicit call), not an automatic side effect of a routine re-compile
    silently dropping one line from a YAML file.

    Returns the total number of `(entity, binding)` column-tag operations
    applied (mirrors `mark_columns_pii`'s own "rows matched" return
    convention) -- callers that want to know exactly what changed should
    inspect the catalog directly, not infer it from this count.
    """

    data_sources_by_name = {
        ds.name: ds for ds in list_data_sources(session, tenant_id=model.tenant_id)
    }

    total_matched = 0
    for entity in model.entities:
        if not entity.sensitive_columns:
            continue
        for binding in entity.bindings:
            split = _split_table_ref(binding.table)
            if split is None:
                continue
            _schema_name, table_name = split
            data_source = data_sources_by_name.get(binding.data_source)
            if data_source is None:
                continue
            total_matched += mark_columns_pii(
                session,
                data_source_id=data_source.id,
                table_name=table_name,
                column_names=[sc.column for sc in entity.sensitive_columns],
            )
    return total_matched

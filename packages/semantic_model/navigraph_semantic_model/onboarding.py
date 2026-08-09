"""Compile a human-reviewed Ontology Drafting proposal into a real
`SemanticModel` -- the "onboarding tooling" conversion step
`contracts.py`'s module docstring names as deliberately separate from
drafting itself: a draft is never auto-compiled, only a document a human
has already reviewed, edited, and approved.

Takes the SAME plain JSON/dict shape `navigraph_agents.understanding
.ontology_drafting.contracts.OntologyDraftingResult` serializes to
(`{"entities": [...], "relationships": [...], "sensitive_columns": [...],
"metrics": [...]}`), not that module's Pydantic types directly -- this
package has no dependency on `navigraph_agents`, and per this codebase's
"no cross-agent-package contract imports" convention, never should. A
plain dict is also exactly the artifact shape a human reviewer actually
edits by hand between drafting and compiling (see
`tools/scripts/onboard_data_source.py` and
`docs/runbooks/data-source-onboarding.md`).

`compile_draft_to_semantic_model` is deliberately lossy in one direction:
anything it cannot safely place into a real `SemanticModel` (a metric
naming an entity that was rejected/renamed, a sensitive column on a table
no approved entity binds) is DROPPED with a human-readable warning rather
than raising -- an onboarding operator should see every drop in one pass,
same as `navigraph_semantic_model.loader.SemanticModelValidationError`
carrying every catalog issue at once rather than failing on the first.
"""

from __future__ import annotations

from typing import Any

from navigraph_semantic_model.contracts import (
    ColumnSensitivity,
    Entity,
    EntityBinding,
    Metric,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)


def _table_ref(schema_name: str, table_name: str) -> str:
    return f"{schema_name}.{table_name}"


def compile_draft_to_semantic_model(
    draft: dict[str, Any],
    *,
    tenant_id: str,
    data_source_name: str,
    version: int = 1,
) -> tuple[SemanticModel, list[str]]:
    """Convert an approved draft dict into a `SemanticModel`.

    `data_source_name` is the real `DataSource.name` the draft's columns
    were crawled from -- every `EntityBinding`/`RelationshipBinding` this
    draft produces points at that one data source, matching how
    `OntologyDraftingAgent` only ever drafts against a single
    `data_source_id` per run (see that agent's own docstring).

    Returns `(model, warnings)`. `warnings` lists every entry this
    function had to drop (an unmapped sensitive column, a metric naming
    an entity that isn't in `draft["entities"]`) -- never raised, always
    reported, so a human reviewing the compile step sees the full picture
    in one pass. Still raises `pydantic.ValidationError` for a
    structurally malformed draft (e.g. a metric with a non-COUNT
    aggregation and no column) -- those are the same *constructor*
    validations a hand-authored `SemanticModel` YAML would fail on, not
    something this function should paper over silently.
    """

    warnings: list[str] = []

    entities: list[Entity] = []
    for raw_entity in draft.get("entities", []):
        bindings = [
            EntityBinding(
                data_source=data_source_name,
                table=_table_ref(binding["schema_name"], binding["table_name"]),
                key=binding["key_column"],
            )
            for binding in raw_entity["bindings"]
        ]
        entities.append(
            Entity(
                name=raw_entity["name"],
                bindings=bindings,
                synonyms=raw_entity.get("synonyms", []),
                description=raw_entity.get("description"),
            )
        )

    entities_by_name = {entity.name: entity for entity in entities}

    for raw_sensitive in draft.get("sensitive_columns", []):
        table_name = raw_sensitive["table_name"]
        column_name = raw_sensitive["column_name"]
        # Matched by bare table name only, not (schema, table) -- the
        # draft's own `DraftSensitiveColumn` shape doesn't carry a schema
        # (see `ontology_drafting.contracts`), so a table name that
        # exists in more than one schema is a known, named ambiguity here;
        # an onboarding operator working with such a source should confirm
        # the resulting `SemanticModel` YAML by hand before activating it.
        matching_entities = [
            entity
            for entity in entities
            if any(binding.table.split(".", 1)[-1] == table_name for binding in entity.bindings)
        ]
        if not matching_entities:
            warnings.append(
                f"sensitive column {table_name}.{column_name}: no approved entity binds "
                f"table {table_name!r} -- dropped, tag it manually with "
                "tools/scripts/tag_pii_columns.py if it should still be marked PII"
            )
            continue
        for entity in matching_entities:
            entity.sensitive_columns.append(ColumnSensitivity(column=column_name))

    relationships: list[Relationship] = []
    for raw_relationship in draft.get("relationships", []):
        relationships.append(
            Relationship(
                name=raw_relationship["name"],
                subject=raw_relationship["subject"],
                predicate=raw_relationship["predicate"],
                object=raw_relationship["object"],
                via=RelationshipBinding(
                    data_source=data_source_name,
                    table=_table_ref(
                        raw_relationship["realizing_schema"], raw_relationship["realizing_table"]
                    ),
                    subject_key=raw_relationship["subject_key_column"],
                    object_key=raw_relationship["object_key_column"],
                ),
            )
        )

    metrics: list[Metric] = []
    for raw_metric in draft.get("metrics", []):
        entity_name = raw_metric["entity"]
        if entity_name not in entities_by_name:
            warnings.append(
                f"metric {raw_metric['name']!r}: entity {entity_name!r} is not one of this "
                "draft's approved entities -- dropped"
            )
            continue
        metrics.append(
            Metric(
                name=raw_metric["name"],
                entity=entity_name,
                aggregation=raw_metric["aggregation"],
                column=raw_metric.get("column"),
            )
        )

    model = SemanticModel(
        tenant_id=tenant_id,
        version=version,
        entities=entities,
        relationships=relationships,
        metrics=metrics,
    )
    return model, warnings

"""The Semantic Model: a versioned, per-tenant configuration artifact
replacing hardcoded per-tenant ontology (`navigraph_kg.ontology
.RELATIONSHIP_CONCEPTS`), reference-data SQL (`reference_data_queries.py`),
SQL Generation's aggregation heuristic, and OPA's hardcoded role set."""

from navigraph_semantic_model.contracts import (
    Aggregation,
    ColumnSensitivity,
    Entity,
    EntityBinding,
    Metric,
    PolicyBindings,
    Relationship,
    RelationshipBinding,
    SemanticModel,
)
from navigraph_semantic_model.loader import (
    SemanticModelValidationError,
    compile_sensitivity,
    load_and_validate_semantic_model,
    load_semantic_model,
    validate_semantic_model_against_catalog,
)

__all__ = [
    "Aggregation",
    "ColumnSensitivity",
    "Entity",
    "EntityBinding",
    "Metric",
    "PolicyBindings",
    "Relationship",
    "RelationshipBinding",
    "SemanticModel",
    "SemanticModelValidationError",
    "compile_sensitivity",
    "load_and_validate_semantic_model",
    "load_semantic_model",
    "validate_semantic_model_against_catalog",
]

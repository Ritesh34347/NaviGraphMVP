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
    ReferenceLookup,
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
from navigraph_semantic_model.onboarding import compile_draft_to_semantic_model
from navigraph_semantic_model.opa_sync import (
    compile_policy_bindings_document,
    sync_policy_bindings,
)

__all__ = [
    "Aggregation",
    "ColumnSensitivity",
    "Entity",
    "EntityBinding",
    "Metric",
    "PolicyBindings",
    "ReferenceLookup",
    "Relationship",
    "RelationshipBinding",
    "SemanticModel",
    "SemanticModelValidationError",
    "compile_draft_to_semantic_model",
    "compile_policy_bindings_document",
    "compile_sensitivity",
    "load_and_validate_semantic_model",
    "load_semantic_model",
    "sync_policy_bindings",
    "validate_semantic_model_against_catalog",
]

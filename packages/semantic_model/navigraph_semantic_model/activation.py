"""The real activation sequence for a compiled `SemanticModel`: validate
against the live catalog, tag PII, persist + mark active in
`metadata_catalog`, and sync `policy_bindings` to OPA.

Defined once, here, rather than hand-copied across every CLI that needs
it (`tools/scripts/onboard_data_source.py`'s `activate` command,
`tools/scripts/navigraph_admin.py`'s `semantic-model compile-and-activate`
command) -- the real invariant this sequence exists to enforce ("never
persist, tag, or sync an unvalidated model") should live in one place,
not risk drifting between two copies.
"""

from __future__ import annotations

from dataclasses import dataclass

from navigraph_catalog.api import activate_semantic_model as _mark_semantic_model_active
from navigraph_catalog.api import save_semantic_model
from navigraph_shared.opa import OpaClient
from sqlalchemy.orm import Session

from navigraph_semantic_model.contracts import SemanticModel
from navigraph_semantic_model.loader import (
    SemanticModelValidationError,
    compile_sensitivity,
    validate_semantic_model_against_catalog,
)
from navigraph_semantic_model.opa_sync import sync_policy_bindings


@dataclass(frozen=True)
class ActivationResult:
    tagged_pii_columns: int


async def activate_semantic_model(
    model: SemanticModel, catalog_session: Session, opa_client: OpaClient
) -> ActivationResult:
    """Validate `model` against the live catalog, then -- only if it
    passes -- tag PII, persist it, mark it the one active version for
    `model.tenant_id`, and sync its `policy_bindings` to OPA.

    Raises `SemanticModelValidationError` (carrying every catalog issue
    found, via `validate_semantic_model_against_catalog`) if validation
    fails. Nothing is persisted, tagged, or synced in that case --
    fail-closed, matching `load_and_validate_semantic_model`'s own
    contract.
    """

    issues = validate_semantic_model_against_catalog(model, catalog_session)
    if issues:
        raise SemanticModelValidationError(issues)

    tagged = compile_sensitivity(model, catalog_session)
    save_semantic_model(
        catalog_session,
        tenant_id=model.tenant_id,
        version=model.version,
        compiled_json=model.model_dump(mode="json"),
    )
    _mark_semantic_model_active(catalog_session, tenant_id=model.tenant_id, version=model.version)
    await sync_policy_bindings(opa_client, model)

    return ActivationResult(tagged_pii_columns=tagged)

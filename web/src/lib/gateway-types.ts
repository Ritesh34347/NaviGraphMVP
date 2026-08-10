/**
 * TypeScript mirror of the gateway's `/ask` request/response shapes.
 *
 * Hand-kept in sync with `packages/gateway/navigraph_gateway/main.py`'s
 * `AskRequest` and
 * `packages/agent_runtime/navigraph_agents/orchestrator/request_orchestrator
 * /contracts.py`'s `RequestOrchestratorResult` -- there is no shared-schema
 * codegen between the Python backend and this Next.js frontend, so a change
 * to either Python contract needs a matching edit here. `outcome` is a
 * closed, three-way discriminant in the real backend (`answered` |
 * `needs_clarification` | `failed`); this type keeps that same shape rather
 * than loosening it to `string`, so a UI branch missing a case is a real
 * TypeScript error, not a silent runtime fallthrough.
 */

export interface AskRequestBody {
  question: string;
  tenant_id: string;
  user_id: string;
  session_id?: string | null;
  data_source_id?: string | null;
  roles?: string[];
  // Self-declared, matching the real per-tenant OPA policy's
  // `tenant_claim_matches` check (`input.claims.tenant_id ==
  // input.tenant_id`) -- required for `/ask` to pass the real Guardrail
  // domain's Policy Authorization agent while Azure AD verification
  // stays feature-flagged off. See LIMITATIONS.md item 23.
  claims?: Record<string, unknown>;
}

export interface AskResult {
  outcome: 'answered' | 'needs_clarification' | 'failed';
  session_id: string;
  resolved_question?: string | null;
  actual_intent?: string | null;
  unmapped_terms?: string[];

  // outcome === "answered"
  final_columns?: string[];
  final_rows?: Record<string, unknown>[];
  final_row_count?: number;
  chart?: Record<string, unknown> | null;
  anomalies?: Record<string, unknown>[];
  narrative?: string | null;
  narrative_errors?: string[];
  follow_up_suggestions?: string[];

  // outcome === "needs_clarification"
  clarifying_question?: string | null;

  // outcome === "failed"
  failure_stage?: string | null;
  failure_reason?: string | null;
}

export interface AskResponse {
  result: AskResult;
  confidence?: number;
  errors?: { code: string; message: string; recoverable: boolean }[];
}

/**
 * Mirrors `navigraph_agents.main`'s `GET /lineage` (search) and
 * `GET /lineage/{trace_id}` (detail) response shapes -- see
 * `navigraph_lineage.api.TraceSummary`/`get_trace` for the Python side.
 */

export interface TraceSummary {
  trace_id: string;
  first_event_at: string;
  last_event_at: string;
  event_count: number;
  agent_names: string[];
}

export interface LineageSearchResponse {
  tenant_id: string;
  traces: TraceSummary[];
}

export interface LineageEventRecord {
  event_id: string;
  agent_name: string;
  timestamp: string;
  input_summary: string;
  output_summary: string;
  tenant_id: string;
  trace_id: string;
}

export interface LineageTraceResponse {
  trace_id: string;
  tenant_id: string;
  events: LineageEventRecord[];
}

/**
 * Mirrors `navigraph_agents.onboarding_contracts`/`onboarding_routes`'s
 * self-service data source onboarding shapes, proxied through the gateway's
 * `/admin/data-sources/*` and `/admin/semantic-models/compile-and-activate`
 * routes. See `navigraph_connectors.base.RequiredSetting`/
 * `ConnectorCapabilities` for the Python side of the connector-manifest
 * types, and `understanding.ontology_drafting.contracts` for the `Draft*`
 * shapes -- the ontology draft itself is never redefined server-side by
 * this feature, so these mirror that agent's contracts exactly, not a new
 * shape invented for this UI.
 */

export interface RequiredSetting {
  field: string;
  description: string;
  required: boolean;
  condition?: string | null;
}

export interface ConnectorCapabilities {
  supports_row_level_security: boolean;
  supports_column_masking: boolean;
  supports_query_pushdown: boolean;
}

export interface ConnectorTypeInfo {
  source_type: string;
  required_settings: RequiredSetting[];
  capabilities: ConnectorCapabilities;
}

export interface ConnectorTypesResponse {
  source_types: ConnectorTypeInfo[];
}

export interface TestConnectionRequestBody {
  source_type: string;
  credential_fields: Record<string, string>;
}

export interface ConnectionTestResult {
  success: boolean;
  message: string;
  latency_ms?: number | null;
}

export interface RegisterDataSourceRequestBody {
  tenant_id: string;
  name: string;
  source_type: string;
  is_default?: boolean;
  credential_fields: Record<string, string>;
}

export interface RegisteredDataSource {
  id: string;
  tenant_id: string;
  name: string;
  source_type: string;
  is_default: boolean;
}

export interface AdminDataSourceSummary {
  id: string;
  name: string;
  source_type: string;
  is_default: boolean;
  last_crawled_at?: string | null;
}

export interface AdminDataSourcesResponse {
  tenant_id: string;
  semantic_model_active_version?: number | null;
  data_sources: AdminDataSourceSummary[];
}

export interface CrawlRequestBody {
  tenant_id: string;
}

export interface CrawlResponse {
  data_source_id: string;
  tables_synced: number;
  new_table_names: string[];
}

export interface DraftOntologyRequestBody {
  tenant_id: string;
  user_id: string;
  roles?: string[];
  claims?: Record<string, unknown>;
}

// Mirrors `understanding.ontology_drafting.contracts`'s `Draft*` models
// field-for-field. Every proposal carries a `rationale` so a human
// reviewing it can see WHY the agent made it, not just the proposal
// itself -- these fields are shown, not just stored, in the review step.
export interface DraftEntityBinding {
  table_name: string;
  schema_name: string;
  key_column: string;
}

export interface DraftEntity {
  name: string;
  bindings: DraftEntityBinding[];
  synonyms: string[];
  description?: string | null;
  rationale: string;
}

export interface DraftRelationship {
  name: string;
  subject: string;
  predicate: string;
  object: string;
  realizing_table: string;
  realizing_schema: string;
  subject_key_column: string;
  object_key_column: string;
  rationale: string;
}

export interface DraftSensitiveColumn {
  table_name: string;
  column_name: string;
  rationale: string;
}

export type DraftAggregation = 'SUM' | 'COUNT' | 'AVG' | 'MIN' | 'MAX';

export interface DraftMetric {
  name: string;
  entity: string;
  aggregation: DraftAggregation;
  column?: string | null;
  rationale: string;
}

export interface OntologyDraftingResult {
  data_source_id: string;
  entities: DraftEntity[];
  relationships: DraftRelationship[];
  sensitive_columns: DraftSensitiveColumn[];
  metrics: DraftMetric[];
}

export interface OntologyDraftingResponse {
  result: OntologyDraftingResult;
  confidence?: number | null;
}

export interface CompileAndActivateRequestBody {
  tenant_id: string;
  data_source_name: string;
  version?: number;
  draft: OntologyDraftingResult;
}

export interface CompileAndActivateResponse {
  tenant_id: string;
  version: number;
  tagged_pii_columns: number;
  compile_warnings: string[];
}

// The gateway forwards agent-runtime's 422 body verbatim for this one
// route -- see `packages/gateway/navigraph_gateway/main.py`'s
// `compile_and_activate_semantic_model` docstring for why.
export interface CompileAndActivateValidationError {
  issues: string[];
}

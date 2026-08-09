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

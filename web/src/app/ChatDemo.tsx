"use client";

import { useState } from "react";

/**
 * Demo trust model: roles/claims are caller-supplied here, exactly like
 * every real curl/Postman call this project has used for verification --
 * see LIMITATIONS.md's Azure AD token verification item (23). This is not
 * a shortcut specific to this UI; it is the same trust boundary the whole
 * platform has today. A future real sign-in screen would replace these
 * constants with values from a verified session, not add a new pattern.
 */
const DEMO_TENANT_ID = "navikenz-poc";
const DEMO_USER_ID = "demo-user";
const DEMO_ROLES = ["analyst"];

interface ChartSpec {
  chart_type: "bar" | "line" | "table" | "single_value";
  x_column?: string | null;
  y_column?: string | null;
  rationale: string;
}

interface AskResult {
  outcome: "answered" | "needs_clarification" | "failed";
  session_id: string;
  final_columns?: string[];
  final_rows?: Record<string, unknown>[];
  final_row_count?: number;
  chart?: ChartSpec | null;
  anomalies?: unknown[];
  narrative?: string | null;
  follow_up_suggestions?: string[];
  clarifying_question?: string | null;
  failure_reason?: string | null;
}

interface AskResponse {
  result: AskResult;
}

type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; kind: "answered"; result: AskResult }
  | { id: string; role: "assistant"; kind: "clarification"; text: string }
  | { id: string; role: "assistant"; kind: "error"; text: string };

function toNumber(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function BarChart({ chart, columns, rows }: { chart: ChartSpec; columns: string[]; rows: Record<string, unknown>[] }) {
  if (!chart.x_column || !chart.y_column || !columns.includes(chart.x_column) || !columns.includes(chart.y_column)) {
    return null;
  }
  const values = rows.map((row) => toNumber(row[chart.y_column as string]));
  const max = Math.max(1, ...values);
  return (
    <div className="chart">
      {rows.slice(0, 12).map((row, i) => {
        const value = values[i];
        return (
          <div className="bar-row" key={i}>
            <span className="label">{String(row[chart.x_column as string])}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(value / max) * 100}%` }} />
            </span>
            <span>{value.toLocaleString()}</span>
          </div>
        );
      })}
    </div>
  );
}

function LineChart({ chart, columns, rows }: { chart: ChartSpec; columns: string[]; rows: Record<string, unknown>[] }) {
  if (!chart.y_column || !columns.includes(chart.y_column)) return null;
  const values = rows.map((row) => toNumber(row[chart.y_column as string]));
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const width = 400;
  const height = 120;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * width},${height - ((v - min) / range) * height}`)
    .join(" ");
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
        <polyline fill="none" stroke="#5b8def" strokeWidth="2" points={points} />
      </svg>
    </div>
  );
}

function SingleValue({ chart, columns, rows }: { chart: ChartSpec; columns: string[]; rows: Record<string, unknown>[] }) {
  if (!chart.y_column || !columns.includes(chart.y_column) || rows.length === 0) return null;
  return <div className="single-value">{String(rows[0][chart.y_column])}</div>;
}

function DataTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (columns.length === 0 || rows.length === 0) return null;
  return (
    <details className="data-table">
      <summary>View data ({rows.length} row{rows.length === 1 ? "" : "s"})</summary>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col}>{String(row[col] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function AnsweredBubble({ result, onSuggestionClick }: { result: AskResult; onSuggestionClick: (q: string) => void }) {
  const columns = result.final_columns ?? [];
  const rows = result.final_rows ?? [];
  const chart = result.chart;
  const anomalyCount = result.anomalies?.length ?? 0;

  return (
    <div className="bubble assistant">
      {anomalyCount > 0 && <div className="badge">{anomalyCount} anomal{anomalyCount === 1 ? "y" : "ies"} detected</div>}
      {result.narrative && <p className="narrative">{result.narrative}</p>}
      {chart?.chart_type === "bar" && <BarChart chart={chart} columns={columns} rows={rows} />}
      {chart?.chart_type === "line" && <LineChart chart={chart} columns={columns} rows={rows} />}
      {chart?.chart_type === "single_value" && <SingleValue chart={chart} columns={columns} rows={rows} />}
      <DataTable columns={columns} rows={rows} />
      {(result.follow_up_suggestions ?? []).length > 0 && (
        <div className="suggestions">
          {result.follow_up_suggestions!.map((q, i) => (
            <button key={i} className="suggestion-chip" onClick={() => onSuggestionClick(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatDemo({ gatewayUrl }: { gatewayUrl: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text: trimmed }]);
    setLoading(true);

    try {
      const response = await fetch(`${gatewayUrl}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          tenant_id: DEMO_TENANT_ID,
          user_id: DEMO_USER_ID,
          session_id: sessionId,
          roles: DEMO_ROLES,
          claims: { tenant_id: DEMO_TENANT_ID },
        }),
      });

      if (!response.ok) {
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", kind: "error", text: `Gateway returned ${response.status}.` },
        ]);
        return;
      }

      const data: AskResponse = await response.json();
      const result = data.result;
      setSessionId(result.session_id);

      if (result.outcome === "answered") {
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "assistant", kind: "answered", result }]);
      } else if (result.outcome === "needs_clarification") {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            kind: "clarification",
            text: result.clarifying_question ?? "Could you clarify your question?",
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            kind: "error",
            text: result.failure_reason ?? "The request failed for an unspecified reason.",
          },
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "assistant", kind: "error", text: "Could not reach the gateway. Is it running?" },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat">
      <div className="demo-notice">
        Demo mode: questions run against the real live pipeline (Snowflake + Anthropic + Neo4j + OPA), fixed to
        tenant &quot;{DEMO_TENANT_ID}&quot; with an &quot;analyst&quot; role. No sign-in yet.
      </div>
      <div className="chat-log">
        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div className="bubble user" key={m.id}>
                {m.text}
              </div>
            );
          }
          if (m.kind === "answered") {
            return <AnsweredBubble key={m.id} result={m.result} onSuggestionClick={ask} />;
          }
          if (m.kind === "clarification") {
            return (
              <div className="bubble assistant clarification" key={m.id}>
                {m.text}
              </div>
            );
          }
          return (
            <div className="bubble assistant error" key={m.id}>
              {m.text}
            </div>
          );
        })}
        {loading && (
          <div className="bubble assistant">
            Thinking&hellip; (real questions can take up to a minute or two)
          </div>
        )}
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your data..."
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Ask
        </button>
      </form>
    </div>
  );
}

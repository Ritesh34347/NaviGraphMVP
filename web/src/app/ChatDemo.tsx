"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Demo trust model: roles/claims are caller-supplied here, exactly like
 * every real curl/Postman call this project has used for verification --
 * see LIMITATIONS.md's Azure AD token verification item (23). This is not
 * a shortcut specific to this UI; it is the same trust boundary the whole
 * platform has today. A future real sign-in screen would replace these
 * constants with values from a verified session, not add a new pattern.
 */
const DEMO_USER_ID = "demo-user";
const DEMO_ROLES = ["analyst"];

interface DataSourceOption {
  tenantId: string;
  label: string;
  description: string;
  exampleQuestions: string[];
}

/**
 * The two real, registered NaviGraph data sources -- `tenant_id` is the
 * only thing the gateway's `/ask` contract needs to route a question to
 * the right one (see `AskRequest.tenant_id` in
 * `navigraph_gateway/main.py`), so switching here needs no gateway/agent
 * change at all. See LIMITATIONS.md item 42 for why these stay two
 * separate tenants rather than one tenant with two data sources.
 */
const DATA_SOURCES: DataSourceOption[] = [
  {
    tenantId: "navikenz-poc",
    label: "Fidelity Brokerage",
    description: "Transactions, customers, assets, and markets",
    exampleQuestions: [
      "What is the total transaction volume by market?",
      "How many transactions has each customer made?",
      "Are there any unusual spikes in units traded by market?",
      "Which markets have the highest transaction volume?",
    ],
  },
  {
    tenantId: "ecommerce-poc",
    label: "E-commerce Demo",
    description: "Orders, customers, products, and channels",
    exampleQuestions: [
      "What is the total revenue by channel?",
      "What are the top 10 products by revenue?",
      "Compare total revenue between the Website and Mobile App channels.",
      "How has total revenue trended month over month?",
    ],
  },
];

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
  generated_sql?: string | null;
  sql_params?: Record<string, unknown>;
}

interface AskResponse {
  result: AskResult;
}

type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; kind: "answered"; result: AskResult }
  | { id: string; role: "assistant"; kind: "clarification"; text: string }
  | { id: string; role: "assistant"; kind: "error"; text: string };

const CACHED_REPLAY_MARKER = "[Cached demo replay";

function toNumber(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M4 20L20 12L4 4L4 10L14 12L4 14L4 20Z"
        fill="currentColor"
      />
    </svg>
  );
}

function SparkleIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M12 3L13.6 9.2L20 11L13.6 12.8L12 19L10.4 12.8L4 11L10.4 9.2L12 3Z"
        fill="currentColor"
      />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M12 3C7.03 3 3 6.58 3 11C3 13.11 3.92 15.02 5.46 16.44C5.32 17.34 4.87 18.6 3.9 19.68C3.75 19.85 3.9 20.11 4.12 20.06C5.87 19.68 7.19 18.94 7.95 18.4C9.19 18.79 10.55 19 12 19C16.97 19 21 15.42 21 11C21 6.58 16.97 3 12 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
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
        <polyline fill="none" stroke="var(--accent)" strokeWidth="2.5" points={points} strokeLinejoin="round" strokeLinecap="round" />
      </svg>
    </div>
  );
}

function SingleValue({ chart, columns, rows }: { chart: ChartSpec; columns: string[]; rows: Record<string, unknown>[] }) {
  if (!chart.y_column || !columns.includes(chart.y_column) || rows.length === 0) return null;
  return <div className="single-value">{String(rows[0][chart.y_column])}</div>;
}

function SqlView({ sql, params }: { sql: string; params: Record<string, unknown> }) {
  const paramEntries = Object.entries(params);
  return (
    <details className="sql-view">
      <summary>View SQL query</summary>
      <pre className="sql-code">{sql}</pre>
      {paramEntries.length > 0 && (
        <div className="sql-params">
          Bound parameters:{" "}
          {paramEntries.map(([key, value], i) => (
            <span key={key}>
              <code>{key} = {JSON.stringify(value)}</code>
              {i < paramEntries.length - 1 ? ", " : ""}
            </span>
          ))}
        </div>
      )}
    </details>
  );
}

function DataTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (columns.length === 0 || rows.length === 0) return null;
  return (
    <details className="data-table">
      <summary>View data ({rows.length.toLocaleString()} row{rows.length === 1 ? "" : "s"})</summary>
      <div className="table-scroll">
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
      </div>
    </details>
  );
}

function AnsweredBubble({ result, onSuggestionClick }: { result: AskResult; onSuggestionClick: (q: string) => void }) {
  const columns = result.final_columns ?? [];
  const rows = result.final_rows ?? [];
  const chart = result.chart;
  const anomalyCount = result.anomalies?.length ?? 0;
  const narrative = result.narrative ?? "";
  const isReplay = narrative.startsWith(CACHED_REPLAY_MARKER);
  const narrativeEnd = isReplay ? narrative.indexOf("]") + 1 : -1;
  const displayNarrative = isReplay ? narrative.slice(narrativeEnd).trim() : narrative;

  return (
    <>
      {isReplay && (
        <span className="replay-badge">
          <SparkleIcon /> Cached replay
        </span>
      )}
      {anomalyCount > 0 && <div className="badge">{anomalyCount} anomal{anomalyCount === 1 ? "y" : "ies"} detected</div>}
      {displayNarrative && <p className="narrative">{displayNarrative}</p>}
      {chart?.chart_type === "bar" && <BarChart chart={chart} columns={columns} rows={rows} />}
      {chart?.chart_type === "line" && <LineChart chart={chart} columns={columns} rows={rows} />}
      {chart?.chart_type === "single_value" && <SingleValue chart={chart} columns={columns} rows={rows} />}
      <DataTable columns={columns} rows={rows} />
      {result.generated_sql && <SqlView sql={result.generated_sql} params={result.sql_params ?? {}} />}
      {(result.follow_up_suggestions ?? []).length > 0 && (
        <div className="suggestions">
          {result.follow_up_suggestions!.map((q, i) => (
            <button key={i} className="suggestion-chip" onClick={() => onSuggestionClick(q)}>
              <SparkleIcon />
              {q}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function MessageRow({ message, onSuggestionClick }: { message: ChatMessage; onSuggestionClick: (q: string) => void }) {
  const isUser = message.role === "user";
  return (
    <div className={`msg-row ${isUser ? "user" : "assistant"}`}>
      <div className={`avatar ${isUser ? "user" : "assistant"}`}>{isUser ? "You" : "N"}</div>
      {isUser ? (
        <div className="bubble">{message.text}</div>
      ) : message.kind === "answered" ? (
        <div className="bubble">
          <AnsweredBubble result={message.result} onSuggestionClick={onSuggestionClick} />
        </div>
      ) : message.kind === "clarification" ? (
        <div className="bubble clarification">{message.text}</div>
      ) : (
        <div className="bubble error">{message.text}</div>
      )}
    </div>
  );
}

export default function ChatDemo({ gatewayUrl }: { gatewayUrl: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const [dataSource, setDataSource] = useState<DataSourceOption>(DATA_SOURCES[0]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  function switchDataSource(next: DataSourceOption) {
    if (next.tenantId === dataSource.tenantId || loading) return;
    // A conversation's session history and follow-up context are meaningless
    // once the underlying schema/tenant changes, so start fresh rather than
    // carrying stale context across data sources.
    setDataSource(next);
    setMessages([]);
    setSessionId(undefined);
    setInput("");
  }

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
          tenant_id: dataSource.tenantId,
          user_id: DEMO_USER_ID,
          session_id: sessionId,
          roles: DEMO_ROLES,
          claims: { tenant_id: dataSource.tenantId },
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
        { id: crypto.randomUUID(), role: "assistant", kind: "error", text: "Could not reach the server. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-card">
      <div className="data-source-bar">
        <label htmlFor="data-source-select">Data source</label>
        <select
          id="data-source-select"
          value={dataSource.tenantId}
          disabled={loading}
          onChange={(e) => {
            const next = DATA_SOURCES.find((d) => d.tenantId === e.target.value);
            if (next) switchDataSource(next);
          }}
        >
          {DATA_SOURCES.map((d) => (
            <option key={d.tenantId} value={d.tenantId}>
              {d.label}
            </option>
          ))}
        </select>
        <span className="data-source-description">{dataSource.description}</span>
      </div>
      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">
              <ChatIcon />
            </div>
            <h2>Start a conversation</h2>
            <p>Try one of these, or ask your own question about {dataSource.description.toLowerCase()}.</p>
            <div className="example-grid">
              {dataSource.exampleQuestions.map((q) => (
                <button key={q} className="example-card" onClick={() => void ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <MessageRow key={m.id} message={m} onSuggestionClick={(q) => void ask(q)} />
        ))}
        {loading && (
          <div className="msg-row assistant">
            <div className="avatar assistant">N</div>
            <div className="bubble">
              <div className="typing">
                <span />
                <span />
                <span />
              </div>
              <div className="typing-caption">Thinking — complex questions can take up to a minute</div>
            </div>
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
          autoFocus
        />
        <button type="submit" disabled={loading || !input.trim()} aria-label="Send">
          <SendIcon />
        </button>
      </form>
    </div>
  );
}

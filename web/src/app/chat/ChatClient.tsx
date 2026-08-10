'use client';

import { useState } from 'react';
import type { ReactElement } from 'react';
import type { AskResponse, AskResult } from '@/lib/gateway-types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  result?: AskResult;
  isError?: boolean;
}

interface ChatClientProps {
  tenantId: string;
  userId: string;
}

const MAX_TABLE_ROWS = 50;

function nextId(): string {
  // crypto.randomUUID is available in every browser this app targets and
  // in the Node runtime `next dev`/`next start` run under -- no extra
  // dependency needed just to make list keys unique.
  return crypto.randomUUID();
}

function summarizeAssistantText(result: AskResult): string {
  if (result.outcome === 'needs_clarification') {
    return result.clarifying_question ?? 'Could you clarify your question?';
  }
  if (result.outcome === 'failed') {
    return (
      result.failure_reason ?? `Something went wrong (stage: ${result.failure_stage ?? 'unknown'}).`
    );
  }
  return result.narrative ?? 'No narrative was generated for this answer.';
}

function ResultTable({ result }: { result: AskResult }): ReactElement | null {
  const columns = result.final_columns ?? [];
  const rows = result.final_rows ?? [];
  if (columns.length === 0 || rows.length === 0) {
    return null;
  }

  const shown = rows.slice(0, MAX_TABLE_ROWS);
  return (
    <div style={{ overflowX: 'auto', marginTop: 8 }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col}
                style={{ border: '1px solid #ccc', padding: '4px 8px', textAlign: 'left' }}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col} style={{ border: '1px solid #ccc', padding: '4px 8px' }}>
                  {String(row[col] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_TABLE_ROWS && (
        <p style={{ fontSize: 12, color: '#666' }}>
          Showing {MAX_TABLE_ROWS} of {result.final_row_count ?? rows.length} row(s).
        </p>
      )}
    </div>
  );
}

export default function ChatClient({ tenantId, userId }: ChatClientProps): ReactElement {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function sendQuestion(question: string): Promise<void> {
    const trimmed = question.trim();
    if (!trimmed || isLoading) {
      return;
    }

    setMessages((prev) => [...prev, { id: nextId(), role: 'user', text: trimmed }]);
    setInput('');
    setIsLoading(true);

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: trimmed,
          tenant_id: tenantId,
          user_id: userId,
          session_id: sessionId,
          // Self-declared, matching the real per-tenant OPA policy's
          // tenant_claim_matches check -- without this every request was
          // silently denied by guardrail.policy_authorization (found live:
          // claims.tenant_id defaults to null server-side when omitted,
          // which never matches a real tenant_id). See LIMITATIONS.md item
          // 23 for why self-declaration is acceptable while Azure AD
          // verification stays off.
          roles: ['analyst'],
          claims: { tenant_id: tenantId },
        }),
      });

      const body = await res.json();

      if (!res.ok) {
        const detail = typeof body?.error === 'string' ? body.error : 'the request failed';
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: 'assistant', text: detail, isError: true },
        ]);
        return;
      }

      const askResponse = body as AskResponse;
      setSessionId(askResponse.result.session_id);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          text: summarizeAssistantText(askResponse.result),
          result: askResponse.result,
          isError: askResponse.result.outcome === 'failed',
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          text: 'Could not reach the server. Please try again.',
          isError: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 16 }}>
      <h1>NaviGraph Chat</h1>
      <p style={{ fontSize: 13, color: '#666' }}>
        Tenant: <code>{tenantId}</code>
      </p>

      <div
        data-testid="messages"
        style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}
      >
        {messages.map((message) => (
          <div
            key={message.id}
            data-testid={`message-${message.role}`}
            style={{
              alignSelf: message.role === 'user' ? 'flex-end' : 'flex-start',
              background: message.isError
                ? '#fdecea'
                : message.role === 'user'
                  ? '#e8f0fe'
                  : '#f1f1f1',
              borderRadius: 8,
              padding: '8px 12px',
              maxWidth: '90%',
            }}
          >
            <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{message.text}</p>
            {message.result && <ResultTable result={message.result} />}
            {message.result?.follow_up_suggestions &&
              message.result.follow_up_suggestions.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                  {message.result.follow_up_suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => void sendQuestion(suggestion)}
                      disabled={isLoading}
                      style={{
                        fontSize: 12,
                        padding: '4px 8px',
                        borderRadius: 12,
                        border: '1px solid #999',
                        background: 'white',
                      }}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
          </div>
        ))}
        {isLoading && <p data-testid="loading-indicator">Thinking…</p>}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void sendQuestion(input);
        }}
        style={{ display: 'flex', gap: 8 }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your data…"
          disabled={isLoading}
          style={{ flex: 1, padding: 8 }}
          aria-label="Question"
        />
        <button type="submit" disabled={isLoading || input.trim().length === 0}>
          Send
        </button>
      </form>
    </div>
  );
}

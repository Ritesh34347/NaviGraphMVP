'use client';

import { Fragment, useState } from 'react';
import type { ReactElement } from 'react';
import type {
  LineageEventRecord,
  LineageSearchResponse,
  LineageTraceResponse,
  TraceSummary,
} from '@/lib/gateway-types';

interface LineageSearchClientProps {
  defaultTenantId: string;
}

export default function LineageSearchClient({
  defaultTenantId,
}: LineageSearchClientProps): ReactElement {
  const [tenantId, setTenantId] = useState(defaultTenantId);
  const [agentName, setAgentName] = useState('');
  const [searchText, setSearchText] = useState('');
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const [expandedEvents, setExpandedEvents] = useState<LineageEventRecord[]>([]);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  async function runSearch(): Promise<void> {
    if (!tenantId.trim()) {
      setError('"tenant_id" is required');
      return;
    }

    setIsLoading(true);
    setError(null);
    setExpandedTraceId(null);

    const params = new URLSearchParams({ tenant_id: tenantId.trim() });
    if (agentName.trim()) params.set('agent_name', agentName.trim());
    if (searchText.trim()) params.set('search_text', searchText.trim());

    try {
      const res = await fetch(`/api/admin/lineage?${params.toString()}`);
      const body = await res.json();
      if (!res.ok) {
        setError(typeof body?.error === 'string' ? body.error : 'the search failed');
        setTraces([]);
        return;
      }
      setTraces((body as LineageSearchResponse).traces);
    } catch {
      setError('Could not reach the server. Please try again.');
      setTraces([]);
    } finally {
      setIsLoading(false);
    }
  }

  async function toggleExpand(traceId: string): Promise<void> {
    if (expandedTraceId === traceId) {
      setExpandedTraceId(null);
      return;
    }

    setExpandedTraceId(traceId);
    setIsLoadingDetail(true);
    setExpandedEvents([]);

    try {
      const res = await fetch(
        `/api/admin/lineage/${encodeURIComponent(traceId)}?tenant_id=${encodeURIComponent(tenantId.trim())}`,
      );
      const body = await res.json();
      if (res.ok) {
        setExpandedEvents((body as LineageTraceResponse).events);
      }
    } finally {
      setIsLoadingDetail(false);
    }
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: 16 }}>
      <h1>Lineage Search</h1>
      <p style={{ fontSize: 13, color: '#666' }}>
        Admin-only in intent, not yet in enforcement -- no real role-based gating exists on this
        page today (see LIMITATIONS.md item 63). Anyone who can reach this URL can search any
        tenant&apos;s lineage.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch();
        }}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}
      >
        <input
          type="text"
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
          placeholder="tenant_id"
          aria-label="Tenant ID"
          style={{ padding: 8, minWidth: 160 }}
        />
        <input
          type="text"
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
          placeholder="agent_name (optional)"
          aria-label="Agent name filter"
          style={{ padding: 8, minWidth: 200 }}
        />
        <input
          type="text"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          placeholder="search text (optional)"
          aria-label="Search text filter"
          style={{ padding: 8, minWidth: 200 }}
        />
        <button type="submit" disabled={isLoading}>
          Search
        </button>
      </form>

      {error && <p style={{ color: '#b00020' }}>{error}</p>}

      {traces.length === 0 && !isLoading && !error && (
        <p style={{ color: '#666' }}>No traces yet -- run a search above.</p>
      )}

      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 14 }}>
        <thead>
          <tr>
            {['Trace', 'First event', 'Last event', 'Events', 'Agents'].map((heading) => (
              <th
                key={heading}
                style={{ border: '1px solid #ccc', padding: '6px 10px', textAlign: 'left' }}
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {traces.map((trace) => (
            <Fragment key={trace.trace_id}>
              <tr
                key={trace.trace_id}
                onClick={() => void toggleExpand(trace.trace_id)}
                style={{ cursor: 'pointer' }}
                data-testid="trace-row"
              >
                <td
                  style={{ border: '1px solid #ccc', padding: '6px 10px', fontFamily: 'monospace' }}
                >
                  {trace.trace_id}
                </td>
                <td style={{ border: '1px solid #ccc', padding: '6px 10px' }}>
                  {trace.first_event_at}
                </td>
                <td style={{ border: '1px solid #ccc', padding: '6px 10px' }}>
                  {trace.last_event_at}
                </td>
                <td style={{ border: '1px solid #ccc', padding: '6px 10px' }}>
                  {trace.event_count}
                </td>
                <td style={{ border: '1px solid #ccc', padding: '6px 10px' }}>
                  {trace.agent_names.join(', ')}
                </td>
              </tr>
              {expandedTraceId === trace.trace_id && (
                <tr key={`${trace.trace_id}-detail`}>
                  <td
                    colSpan={5}
                    style={{ border: '1px solid #ccc', padding: '6px 10px', background: '#fafafa' }}
                  >
                    {isLoadingDetail ? (
                      <p data-testid="detail-loading">Loading…</p>
                    ) : (
                      <div
                        data-testid="trace-detail"
                        style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                      >
                        {expandedEvents.map((event) => (
                          <div key={event.event_id}>
                            <strong>
                              [{event.timestamp}] {event.agent_name}
                            </strong>
                            <div>input: {event.input_summary}</div>
                            <div>output: {event.output_summary}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

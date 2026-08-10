'use client';

import type { ReactElement } from 'react';
import type { AdminDataSourceSummary } from '@/lib/gateway-types';
import * as s from './styles';

interface DataSourceListClientProps {
  dataSources: AdminDataSourceSummary[];
  semanticModelActiveVersion: number | null;
  isLoading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export default function DataSourceListClient({
  dataSources,
  semanticModelActiveVersion,
  isLoading,
  error,
  onRefresh,
}: DataSourceListClientProps): ReactElement {
  return (
    <div style={s.card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.05rem' }}>Your data sources</h2>
          <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            {semanticModelActiveVersion != null
              ? `Semantic model version ${semanticModelActiveVersion} is live.`
              : 'No semantic model activated yet.'}
          </p>
        </div>
        <button type="button" onClick={onRefresh} disabled={isLoading} style={s.secondaryButton}>
          {isLoading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error ? (
        <p style={s.errorBanner}>{error}</p>
      ) : dataSources.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          No data sources registered yet — connect one below.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.85rem' }}>
            <thead>
              <tr>
                {['Name', 'Type', 'Default', 'Last crawled'].map((heading) => (
                  <th
                    key={heading}
                    style={{
                      textAlign: 'left',
                      padding: '0.5rem 0.65rem',
                      borderBottom: '1px solid var(--border)',
                      color: 'var(--text-muted)',
                      fontWeight: 600,
                    }}
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dataSources.map((ds) => (
                <tr key={ds.id}>
                  <td style={{ padding: '0.5rem 0.65rem', borderBottom: '1px solid var(--border)' }}>
                    {ds.name}
                  </td>
                  <td
                    style={{
                      padding: '0.5rem 0.65rem',
                      borderBottom: '1px solid var(--border)',
                      textTransform: 'capitalize',
                    }}
                  >
                    {ds.source_type}
                  </td>
                  <td style={{ padding: '0.5rem 0.65rem', borderBottom: '1px solid var(--border)' }}>
                    {ds.is_default ? '✓' : ''}
                  </td>
                  <td style={{ padding: '0.5rem 0.65rem', borderBottom: '1px solid var(--border)' }}>
                    {ds.last_crawled_at ?? 'never'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

'use client';

import type { ReactElement } from 'react';
import type { ConnectorTypeInfo } from '@/lib/gateway-types';
import * as s from '../styles';

interface ChooseConnectorTypeStepProps {
  connectorTypes: ConnectorTypeInfo[];
  isLoading: boolean;
  error: string | null;
  selected: string | null;
  onSelect: (sourceType: string) => void;
  onNext: () => void;
}

export default function ChooseConnectorTypeStep({
  connectorTypes,
  isLoading,
  error,
  selected,
  onSelect,
  onNext,
}: ChooseConnectorTypeStepProps): ReactElement {
  return (
    <div style={s.card}>
      <div>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>1. Choose a data source type</h2>
        <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Every registered connector type on this platform. Connecting a source that isn&apos;t
          listed here requires a new connector to be added to the platform first.
        </p>
      </div>

      {error && <p style={s.errorBanner}>{error}</p>}
      {isLoading && <p style={{ color: 'var(--text-muted)' }}>Loading connector types…</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {connectorTypes.map((info) => {
          const isSelected = selected === info.source_type;
          return (
            <button
              key={info.source_type}
              type="button"
              onClick={() => onSelect(info.source_type)}
              style={{
                textAlign: 'left',
                padding: '0.85rem 1rem',
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                background: isSelected ? 'var(--accent-soft)' : 'transparent',
                cursor: 'pointer',
                color: 'var(--text)',
              }}
            >
              <div style={{ fontWeight: 600, textTransform: 'capitalize' }}>
                {info.source_type}
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                {info.required_settings.length} configuration field
                {info.required_settings.length === 1 ? '' : 's'}
                {info.capabilities.supports_row_level_security && ' · row-level security'}
                {info.capabilities.supports_column_masking && ' · column masking'}
                {info.capabilities.supports_query_pushdown && ' · query pushdown'}
              </div>
            </button>
          );
        })}
      </div>

      <div>
        <button
          type="button"
          onClick={onNext}
          disabled={!selected}
          style={{ ...s.primaryButton, ...(selected ? {} : s.disabledButton) }}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

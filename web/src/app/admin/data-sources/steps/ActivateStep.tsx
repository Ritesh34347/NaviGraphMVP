'use client';

import type { ReactElement } from 'react';
import type { CompileAndActivateResponse } from '@/lib/gateway-types';
import * as s from '../styles';

interface ActivateStepProps {
  dataSourceName: string;
  isActivating: boolean;
  result: CompileAndActivateResponse | null;
  issues: string[] | null;
  error: string | null;
  onActivate: () => void;
  onStartOver: () => void;
}

export default function ActivateStep({
  dataSourceName,
  isActivating,
  result,
  issues,
  error,
  onActivate,
  onStartOver,
}: ActivateStepProps): ReactElement {
  return (
    <div style={s.card}>
      <div>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>5. Activate</h2>
        <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Compiles your approved selections into a real semantic model for{' '}
          <strong>{dataSourceName}</strong> and makes it the live version questions are answered
          against.
        </p>
      </div>

      {error && <p style={s.errorBanner}>{error}</p>}

      {issues && issues.length > 0 && (
        <div style={s.errorBanner}>
          <strong>The draft failed validation against the live catalog:</strong>
          <ul style={{ margin: '0.4rem 0 0', paddingLeft: '1.2rem' }}>
            {issues.map((issue) => (
              <li key={issue} style={{ fontSize: '0.82rem' }}>
                {issue}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!result && (
        <button
          type="button"
          onClick={onActivate}
          disabled={isActivating}
          style={{ ...s.primaryButton, ...(isActivating ? s.disabledButton : {}) }}
        >
          {isActivating ? 'Activating…' : 'Activate'}
        </button>
      )}

      {result && (
        <>
          <div style={s.successBanner}>
            ✓ Semantic model version {result.version} is now live for {dataSourceName}.
            {result.tagged_pii_columns > 0 &&
              ` ${result.tagged_pii_columns} column(s) tagged as sensitive.`}
          </div>
          {result.compile_warnings.length > 0 && (
            <div style={{ fontSize: '0.8rem', color: 'var(--warning)' }}>
              <strong>Compile warnings:</strong>
              <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>
                {result.compile_warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <button type="button" onClick={onStartOver} style={s.secondaryButton}>
              Connect another data source
            </button>
          </div>
        </>
      )}
    </div>
  );
}

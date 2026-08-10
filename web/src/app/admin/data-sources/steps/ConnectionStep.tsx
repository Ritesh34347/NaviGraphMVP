'use client';

import type { ReactElement } from 'react';
import type { ConnectionTestResult, RequiredSetting } from '@/lib/gateway-types';
import * as s from '../styles';

interface ConnectionStepProps {
  sourceType: string;
  requiredSettings: RequiredSetting[];
  name: string;
  onNameChange: (value: string) => void;
  credentialFields: Record<string, string>;
  onFieldChange: (field: string, value: string) => void;
  onTestConnection: () => void;
  isTesting: boolean;
  testResult: ConnectionTestResult | null;
  onRegisterAndContinue: () => void;
  isRegistering: boolean;
  error: string | null;
  onBack: () => void;
}

export default function ConnectionStep({
  sourceType,
  requiredSettings,
  name,
  onNameChange,
  credentialFields,
  onFieldChange,
  onTestConnection,
  isTesting,
  testResult,
  onRegisterAndContinue,
  isRegistering,
  error,
  onBack,
}: ConnectionStepProps): ReactElement {
  const isPasswordField = (field: string): boolean =>
    /password|secret|passphrase|key/i.test(field);

  return (
    <div style={s.card}>
      <div>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>
          2. Configure your <span style={{ textTransform: 'capitalize' }}>{sourceType}</span>{' '}
          connection
        </h2>
        <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Credentials are written directly to the platform&apos;s secrets backend and never stored
          as plain text in this data source&apos;s configuration.
        </p>
      </div>

      {error && <p style={s.errorBanner}>{error}</p>}

      <label style={s.label}>
        Data source name
        <input
          style={s.input}
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="e.g. acme-snowflake-prod"
        />
      </label>

      {requiredSettings.map((setting) => (
        <label key={setting.field} style={s.label}>
          {setting.field}
          {setting.required && <span style={{ color: 'var(--danger)' }}> *</span>}
          <input
            style={s.input}
            type={isPasswordField(setting.field) ? 'password' : 'text'}
            value={credentialFields[setting.field] ?? ''}
            onChange={(e) => onFieldChange(setting.field, e.target.value)}
            placeholder={setting.description}
          />
          <span style={{ fontWeight: 400, fontSize: '0.76rem', color: 'var(--text-faint, var(--text-muted))' }}>
            {setting.description}
            {setting.condition && ` (${setting.condition})`}
          </span>
        </label>
      ))}

      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={onTestConnection}
          disabled={isTesting}
          style={{ ...s.secondaryButton, ...(isTesting ? s.disabledButton : {}) }}
        >
          {isTesting ? 'Testing…' : 'Test connection'}
        </button>
        {testResult && (
          <span
            style={{
              fontSize: '0.82rem',
              color: testResult.success ? 'var(--success)' : 'var(--danger)',
            }}
          >
            {testResult.success ? '✓ ' : '✗ '}
            {testResult.message}
            {testResult.latency_ms != null && ` (${testResult.latency_ms.toFixed(0)}ms)`}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', gap: '0.6rem' }}>
        <button type="button" onClick={onBack} style={s.secondaryButton}>
          Back
        </button>
        <button
          type="button"
          onClick={onRegisterAndContinue}
          disabled={isRegistering || !name.trim()}
          style={{
            ...s.primaryButton,
            ...(isRegistering || !name.trim() ? s.disabledButton : {}),
          }}
        >
          {isRegistering ? 'Registering…' : 'Save and continue'}
        </button>
      </div>
    </div>
  );
}

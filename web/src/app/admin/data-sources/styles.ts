import type { CSSProperties } from 'react';

/**
 * Shared inline-style building blocks for the data source onboarding
 * wizard, built from the same CSS custom properties `globals.css` already
 * defines (`--surface`, `--border`, `--accent`, `--radius-*`, `--danger`,
 * `--success`) -- not new literal colors, unlike `LineageSearchClient.tsx`'s
 * older `#666`/`#b00020`/`#ccc` inline styles. Factored into one module
 * once six step components needed the identical card/button/input shapes,
 * same rationale as `gateway-proxy.ts` on the API side.
 */

export const card: CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  padding: '1.25rem 1.5rem',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.9rem',
};

export const input: CSSProperties = {
  padding: '0.6rem 0.75rem',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border)',
  background: 'var(--surface-raised, var(--surface))',
  color: 'var(--text)',
  fontSize: '0.9rem',
  outline: 'none',
  width: '100%',
};

export const label: CSSProperties = {
  fontSize: '0.82rem',
  fontWeight: 600,
  color: 'var(--text-muted)',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.3rem',
};

export const primaryButton: CSSProperties = {
  background: 'var(--accent)',
  color: 'var(--accent-text)',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '0.65rem 1.1rem',
  fontSize: '0.88rem',
  fontWeight: 600,
  cursor: 'pointer',
};

export const secondaryButton: CSSProperties = {
  background: 'transparent',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '0.65rem 1.1rem',
  fontSize: '0.88rem',
  fontWeight: 600,
  cursor: 'pointer',
};

export const disabledButton: CSSProperties = {
  opacity: 0.5,
  cursor: 'default',
};

export const errorBanner: CSSProperties = {
  color: 'var(--danger)',
  background: 'var(--danger-soft)',
  border: '1px solid var(--danger)',
  borderRadius: 'var(--radius-sm)',
  padding: '0.65rem 0.85rem',
  fontSize: '0.85rem',
};

export const successBanner: CSSProperties = {
  color: 'var(--success)',
  background: 'var(--accent-soft)',
  border: '1px solid var(--success)',
  borderRadius: 'var(--radius-sm)',
  padding: '0.65rem 0.85rem',
  fontSize: '0.85rem',
};

export const stepIndicator: CSSProperties = {
  display: 'flex',
  gap: '0.5rem',
  flexWrap: 'wrap',
  marginBottom: '0.25rem',
};

export function stepPill(active: boolean, done: boolean): CSSProperties {
  return {
    fontSize: '0.72rem',
    fontWeight: 600,
    padding: '0.3rem 0.65rem',
    borderRadius: 999,
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
    color: active ? 'var(--accent)' : done ? 'var(--success)' : 'var(--text-muted)',
    background: active ? 'var(--accent-soft)' : 'transparent',
  };
}

export const rationale: CSSProperties = {
  fontSize: '0.78rem',
  color: 'var(--text-muted)',
  fontStyle: 'italic',
};

export const monospace: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  fontSize: '0.82rem',
};

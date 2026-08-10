'use client';

import { useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import type { OntologyDraftingResult } from '@/lib/gateway-types';
import * as s from '../styles';

interface ReviewDraftStepProps {
  isDrafting: boolean;
  draft: OntologyDraftingResult | null;
  error: string | null;
  onRequestDraft: () => void;
  onContinue: (approvedDraft: OntologyDraftingResult) => void;
}

/**
 * The human-review gate: every entity/relationship/metric/sensitive-column
 * the agent proposed is shown WITH its `rationale`, checked-in by default,
 * and can be individually excluded before the draft is compiled. Nothing
 * here is auto-published -- `onContinue` only fires once the reviewer
 * explicitly submits, and only the still-checked items are included.
 */
export default function ReviewDraftStep({
  isDrafting,
  draft,
  error,
  onRequestDraft,
  onContinue,
}: ReviewDraftStepProps): ReactElement {
  const [excludedEntities, setExcludedEntities] = useState<Set<number>>(new Set());
  const [excludedRelationships, setExcludedRelationships] = useState<Set<number>>(new Set());
  const [excludedSensitiveColumns, setExcludedSensitiveColumns] = useState<Set<number>>(new Set());
  const [excludedMetrics, setExcludedMetrics] = useState<Set<number>>(new Set());

  function toggle(set: Set<number>, setSet: (s: Set<number>) => void, index: number): void {
    const next = new Set(set);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    setSet(next);
  }

  const approvedDraft = useMemo<OntologyDraftingResult | null>(() => {
    if (!draft) return null;
    return {
      data_source_id: draft.data_source_id,
      entities: draft.entities.filter((_, i) => !excludedEntities.has(i)),
      relationships: draft.relationships.filter((_, i) => !excludedRelationships.has(i)),
      sensitive_columns: draft.sensitive_columns.filter(
        (_, i) => !excludedSensitiveColumns.has(i),
      ),
      metrics: draft.metrics.filter((_, i) => !excludedMetrics.has(i)),
    };
  }, [draft, excludedEntities, excludedRelationships, excludedSensitiveColumns, excludedMetrics]);

  return (
    <div style={s.card}>
      <div>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>4. Review the drafted ontology</h2>
        <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Every item below was proposed by NaviGraph&apos;s Ontology Drafting agent from the crawled
          schema. Uncheck anything that&apos;s wrong before activating — nothing is published until
          you approve it.
        </p>
      </div>

      {error && <p style={s.errorBanner}>{error}</p>}

      {!draft && (
        <button
          type="button"
          onClick={onRequestDraft}
          disabled={isDrafting}
          style={{ ...s.primaryButton, ...(isDrafting ? s.disabledButton : {}) }}
        >
          {isDrafting ? 'Drafting… (this calls a real model, may take a moment)' : 'Draft ontology'}
        </button>
      )}

      {draft && (
        <>
          <Section title={`Entities (${draft.entities.length})`}>
            {draft.entities.map((entity, i) => (
              <ReviewRow
                key={entity.name}
                checked={!excludedEntities.has(i)}
                onToggle={() => toggle(excludedEntities, setExcludedEntities, i)}
                title={entity.name}
                subtitle={
                  entity.bindings
                    .map((b) => `${b.schema_name}.${b.table_name} (${b.key_column})`)
                    .join(', ')
                }
                rationale={entity.rationale}
              />
            ))}
            {draft.entities.length === 0 && <Empty />}
          </Section>

          <Section title={`Relationships (${draft.relationships.length})`}>
            {draft.relationships.map((rel, i) => (
              <ReviewRow
                key={rel.name}
                checked={!excludedRelationships.has(i)}
                onToggle={() => toggle(excludedRelationships, setExcludedRelationships, i)}
                title={`${rel.subject} —${rel.predicate}→ ${rel.object}`}
                subtitle={`via ${rel.realizing_schema}.${rel.realizing_table}`}
                rationale={rel.rationale}
              />
            ))}
            {draft.relationships.length === 0 && <Empty />}
          </Section>

          <Section title={`Metrics (${draft.metrics.length})`}>
            {draft.metrics.map((metric, i) => (
              <ReviewRow
                key={metric.name}
                checked={!excludedMetrics.has(i)}
                onToggle={() => toggle(excludedMetrics, setExcludedMetrics, i)}
                title={metric.name}
                subtitle={`${metric.aggregation}${metric.column ? `(${metric.column})` : '()'} on ${metric.entity}`}
                rationale={metric.rationale}
              />
            ))}
            {draft.metrics.length === 0 && <Empty />}
          </Section>

          <Section title={`Flagged as sensitive (${draft.sensitive_columns.length})`}>
            {draft.sensitive_columns.map((col, i) => (
              <ReviewRow
                key={`${col.table_name}.${col.column_name}`}
                checked={!excludedSensitiveColumns.has(i)}
                onToggle={() => toggle(excludedSensitiveColumns, setExcludedSensitiveColumns, i)}
                title={`${col.table_name}.${col.column_name}`}
                rationale={col.rationale}
              />
            ))}
            {draft.sensitive_columns.length === 0 && <Empty />}
          </Section>

          <div>
            <button
              type="button"
              onClick={() => approvedDraft && onContinue(approvedDraft)}
              style={s.primaryButton}
            >
              Approve and continue
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): ReactElement {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <h3 style={{ margin: 0, fontSize: '0.82rem', color: 'var(--text-muted)' }}>{title}</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>{children}</div>
    </div>
  );
}

function Empty(): ReactElement {
  return <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-faint, var(--text-muted))' }}>None proposed.</p>;
}

function ReviewRow({
  checked,
  onToggle,
  title,
  subtitle,
  rationale,
}: {
  checked: boolean;
  onToggle: () => void;
  title: string;
  subtitle?: string;
  rationale: string;
}): ReactElement {
  return (
    <label
      style={{
        display: 'flex',
        gap: '0.6rem',
        alignItems: 'flex-start',
        padding: '0.6rem 0.75rem',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border)',
        opacity: checked ? 1 : 0.5,
        cursor: 'pointer',
      }}
    >
      <input type="checkbox" checked={checked} onChange={onToggle} style={{ marginTop: '0.2rem' }} />
      <div>
        <div style={{ fontWeight: 600, fontSize: '0.88rem' }}>{title}</div>
        {subtitle && (
          <div style={{ ...s.monospace, color: 'var(--text-muted)', marginTop: '0.15rem' }}>
            {subtitle}
          </div>
        )}
        <div style={{ ...s.rationale, marginTop: '0.2rem' }}>{rationale}</div>
      </div>
    </label>
  );
}

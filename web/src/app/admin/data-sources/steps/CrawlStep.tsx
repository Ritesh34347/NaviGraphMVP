'use client';

import type { ReactElement } from 'react';
import type { CrawlResponse } from '@/lib/gateway-types';
import * as s from '../styles';

interface CrawlStepProps {
  dataSourceName: string;
  onCrawl: () => void;
  isCrawling: boolean;
  crawlResult: CrawlResponse | null;
  error: string | null;
  onContinue: () => void;
}

export default function CrawlStep({
  dataSourceName,
  onCrawl,
  isCrawling,
  crawlResult,
  error,
  onContinue,
}: CrawlStepProps): ReactElement {
  return (
    <div style={s.card}>
      <div>
        <h2 style={{ margin: 0, fontSize: '1.05rem' }}>3. Crawl the schema</h2>
        <p style={{ margin: '0.35rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Discovers every table and column in <strong>{dataSourceName}</strong> and stores them in
          the catalog. This is what the next step&apos;s ontology draft is built from.
        </p>
      </div>

      {error && <p style={s.errorBanner}>{error}</p>}

      {!crawlResult && (
        <button
          type="button"
          onClick={onCrawl}
          disabled={isCrawling}
          style={{ ...s.primaryButton, ...(isCrawling ? s.disabledButton : {}) }}
        >
          {isCrawling ? 'Crawling…' : 'Crawl now'}
        </button>
      )}

      {crawlResult && (
        <div style={s.successBanner}>
          Discovered {crawlResult.tables_synced} table
          {crawlResult.tables_synced === 1 ? '' : 's'}
          {crawlResult.new_table_names.length > 0 && (
            <> — new: {crawlResult.new_table_names.join(', ')}</>
          )}
        </div>
      )}

      {crawlResult && (
        <div>
          <button type="button" onClick={onContinue} style={s.primaryButton}>
            Draft an ontology
          </button>
        </div>
      )}
    </div>
  );
}

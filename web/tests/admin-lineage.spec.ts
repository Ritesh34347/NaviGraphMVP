import { test, expect } from '@playwright/test';
import type { LineageSearchResponse, LineageTraceResponse } from '@/lib/gateway-types';

/**
 * Real Playwright tests for the admin lineage search page (Phase 15.3),
 * mirroring `chat.spec.ts`'s convention: `page.route` intercepts the
 * browser's same-origin requests to `/api/admin/lineage*` (the proxy
 * routes in `src/app/api/admin/lineage/`), so these tests exercise the
 * UI's actual search/expand-detail logic, not a mock of the UI itself.
 */

async function mockSearchResponse(
  page: import('@playwright/test').Page,
  response: LineageSearchResponse,
): Promise<void> {
  await page.route('**/api/admin/lineage?*', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
  });
}

async function mockDetailResponse(
  page: import('@playwright/test').Page,
  traceId: string,
  response: LineageTraceResponse,
): Promise<void> {
  await page.route(`**/api/admin/lineage/${traceId}?*`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
  });
}

test('searching renders a table of trace summaries', async ({ page }) => {
  await mockSearchResponse(page, {
    tenant_id: 'tenant-acme',
    traces: [
      {
        trace_id: 'trace-1',
        first_event_at: '2026-08-09T10:00:00',
        last_event_at: '2026-08-09T10:05:00',
        event_count: 3,
        agent_names: ['understanding.conversation', 'query.sql_generation'],
      },
    ],
  });

  await page.goto('/admin/lineage');
  await page.getByLabel('Tenant ID').fill('tenant-acme');
  await page.getByRole('button', { name: 'Search' }).click();

  const row = page.getByTestId('trace-row');
  await expect(row).toContainText('trace-1');
  await expect(row).toContainText('understanding.conversation, query.sql_generation');
  await expect(row).toContainText('3');
});

test('clicking a trace row expands its full event chain', async ({ page }) => {
  await mockSearchResponse(page, {
    tenant_id: 'tenant-acme',
    traces: [
      {
        trace_id: 'trace-1',
        first_event_at: '2026-08-09T10:00:00',
        last_event_at: '2026-08-09T10:05:00',
        event_count: 1,
        agent_names: ['understanding.conversation'],
      },
    ],
  });
  await mockDetailResponse(page, 'trace-1', {
    trace_id: 'trace-1',
    tenant_id: 'tenant-acme',
    events: [
      {
        event_id: 'lineage_abc',
        agent_name: 'understanding.conversation',
        timestamp: '2026-08-09T10:00:00',
        input_summary: 'question=what is our revenue',
        output_summary: 'resolved_question=what is our revenue',
        tenant_id: 'tenant-acme',
        trace_id: 'trace-1',
      },
    ],
  });

  await page.goto('/admin/lineage');
  await page.getByLabel('Tenant ID').fill('tenant-acme');
  await page.getByRole('button', { name: 'Search' }).click();
  await page.getByTestId('trace-row').click();

  const detail = page.getByTestId('trace-detail');
  await expect(detail).toContainText('understanding.conversation');
  await expect(detail).toContainText('question=what is our revenue');

  // Clicking again collapses it.
  await page.getByTestId('trace-row').click();
  await expect(page.getByTestId('trace-detail')).not.toBeVisible();
});

test('shows an inline error when the gateway is unreachable', async ({ page }) => {
  await page.route('**/api/admin/lineage?*', async (route) => {
    await route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'gateway is unreachable -- is it running and is GATEWAY_URL correct?' }),
    });
  });

  await page.goto('/admin/lineage');
  await page.getByLabel('Tenant ID').fill('tenant-acme');
  await page.getByRole('button', { name: 'Search' }).click();

  await expect(page.locator('body')).toContainText('gateway is unreachable');
});

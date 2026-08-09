import { test, expect } from '@playwright/test';
import type { AskResponse } from '@/lib/gateway-types';

/**
 * Real Playwright tests for the chat UI (Phase 14.1), driven entirely
 * through the browser -- no live gateway needed. `page.route` intercepts
 * the browser's same-origin request to `/api/ask` (the proxy route in
 * `src/app/api/ask/route.ts`) and returns a canned `AskResponse` shaped
 * exactly like a real `RequestOrchestratorOutput`, so these tests exercise
 * the UI's actual rendering logic for each of the three real `outcome`
 * values, not a mock of the UI itself.
 */

async function mockAskResponse(
  page: import('@playwright/test').Page,
  response: AskResponse,
): Promise<void> {
  await page.route('**/api/ask', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
}

test('renders a narrative, a result table, and follow-up suggestions for outcome=answered', async ({
  page,
}) => {
  await mockAskResponse(page, {
    result: {
      outcome: 'answered',
      session_id: 'session-123',
      narrative: 'Revenue grew 12% quarter over quarter.',
      final_columns: ['quarter', 'revenue'],
      final_rows: [
        { quarter: 'Q1', revenue: 1000 },
        { quarter: 'Q2', revenue: 1120 },
      ],
      final_row_count: 2,
      follow_up_suggestions: ['What drove the Q2 growth?'],
    },
    confidence: 0.9,
  });

  await page.goto('/chat');
  await page.getByLabel('Question').fill('What was our revenue by quarter?');
  await page.getByRole('button', { name: 'Send' }).click();

  const assistantMessage = page.getByTestId('message-assistant').last();
  await expect(assistantMessage).toContainText('Revenue grew 12%');
  await expect(assistantMessage.locator('table')).toContainText('quarter');
  await expect(assistantMessage.locator('table')).toContainText('1120');
  await expect(
    assistantMessage.getByRole('button', { name: 'What drove the Q2 growth?' }),
  ).toBeVisible();
});

test('renders a clarifying question for outcome=needs_clarification', async ({ page }) => {
  await mockAskResponse(page, {
    result: {
      outcome: 'needs_clarification',
      session_id: 'session-456',
      clarifying_question: 'Which region did you mean?',
    },
  });

  await page.goto('/chat');
  await page.getByLabel('Question').fill('What was revenue?');
  await page.getByRole('button', { name: 'Send' }).click();

  await expect(page.getByTestId('message-assistant').last()).toContainText(
    'Which region did you mean?',
  );
});

test('renders a failure reason for outcome=failed', async ({ page }) => {
  await mockAskResponse(page, {
    result: {
      outcome: 'failed',
      session_id: 'session-789',
      failure_stage: 'sql_generation',
      failure_reason: 'Could not resolve a valid SQL statement for this question.',
    },
  });

  await page.goto('/chat');
  await page.getByLabel('Question').fill('gibberish query');
  await page.getByRole('button', { name: 'Send' }).click();

  await expect(page.getByTestId('message-assistant').last()).toContainText(
    'Could not resolve a valid SQL statement',
  );
});

test('carries the session_id from the first response into the next request', async ({ page }) => {
  const seenSessionIds: (string | null)[] = [];

  await page.route('**/api/ask', async (route) => {
    const body = route.request().postDataJSON() as { session_id?: string | null };
    seenSessionIds.push(body.session_id ?? null);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        result: { outcome: 'answered', session_id: 'stable-session-id', narrative: 'ok' },
      } satisfies AskResponse),
    });
  });

  await page.goto('/chat');
  await page.getByLabel('Question').fill('first question');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByTestId('message-assistant').last()).toContainText('ok');

  await page.getByLabel('Question').fill('second question');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByTestId('message-assistant').last()).toContainText('ok');

  expect(seenSessionIds).toEqual([null, 'stable-session-id']);
});

test('shows an inline error when the gateway is unreachable', async ({ page }) => {
  await page.route('**/api/ask', async (route) => {
    await route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({
        error: 'gateway is unreachable -- is it running and is GATEWAY_URL correct?',
      }),
    });
  });

  await page.goto('/chat');
  await page.getByLabel('Question').fill('anything');
  await page.getByRole('button', { name: 'Send' }).click();

  await expect(page.getByTestId('message-assistant').last()).toContainText(
    'gateway is unreachable',
  );
});

import { NextResponse } from 'next/server';
import { env } from '@/lib/env';

/**
 * Shared implementation behind every `/api/admin/data-sources/*` and
 * `/api/semantic-models/compile-and-activate` route: forward to the
 * gateway, keep `GATEWAY_URL` server-only, and normalize a non-2xx
 * response into `{error, status, detail}` -- the exact shape
 * `api/admin/lineage/route.ts`/`api/ask/route.ts` already established by
 * hand in each of their own route files. Factored out once six new routes
 * needed the identical wiring, mirroring `_invoke_agent`'s own "was
 * written inline, one-off, before this needed six copies" rationale on
 * the Python side.
 */
export async function proxyToGateway(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<NextResponse> {
  const { timeoutMs = 30_000, ...requestInit } = init ?? {};

  let gatewayResponse: Response;
  try {
    gatewayResponse = await fetch(new URL(path, env.GATEWAY_URL), {
      ...requestInit,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    return NextResponse.json(
      { error: 'gateway is unreachable -- is it running and is GATEWAY_URL correct?' },
      { status: 502 },
    );
  }

  let body: unknown;
  try {
    body = await gatewayResponse.json();
  } catch {
    body = await gatewayResponse.text();
  }

  if (!gatewayResponse.ok) {
    // Preserve the real status code (notably 422 for compile-and-activate's
    // structured validation issues) and the gateway's own response body
    // under `detail` -- callers that need to render `detail.issues`
    // (ActivateStep.tsx) read it from there, not from a re-shaped `error`
    // string.
    return NextResponse.json(
      { error: 'gateway returned an error', status: gatewayResponse.status, detail: body },
      { status: gatewayResponse.status },
    );
  }

  return NextResponse.json(body);
}

export async function proxyJsonPostToGateway(
  path: string,
  body: unknown,
  opts?: { timeoutMs?: number },
): Promise<NextResponse> {
  return proxyToGateway(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    timeoutMs: opts?.timeoutMs,
  });
}

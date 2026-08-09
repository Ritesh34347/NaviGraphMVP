import { NextResponse } from 'next/server';
import { env } from '@/lib/env';
import type { AskRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's `POST /ask`.
 *
 * Two reasons this exists rather than having the browser call the gateway
 * directly: `GATEWAY_URL` (the server-only, docker-compose-internal address)
 * stays out of the browser bundle, and this same-origin route sidesteps
 * needing CORS configuration on the gateway for a browser client that, in
 * a real deployment, may not even share a network path to it directly.
 *
 * Forwards the request body largely as-is, adding nothing the browser
 * didn't already send -- this route does not itself decide `tenant_id`/
 * `user_id`; see `ChatClient.tsx`'s own note on why those are still a
 * fixed dev-mode value rather than coming from a real signed-in session.
 */
export async function POST(request: Request): Promise<NextResponse> {
  let body: AskRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.question !== 'string' || body.question.trim().length === 0) {
    return NextResponse.json({ error: '"question" is required' }, { status: 400 });
  }
  if (typeof body.tenant_id !== 'string' || body.tenant_id.trim().length === 0) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }
  if (typeof body.user_id !== 'string' || body.user_id.trim().length === 0) {
    return NextResponse.json({ error: '"user_id" is required' }, { status: 400 });
  }

  let gatewayResponse: Response;
  try {
    gatewayResponse = await fetch(`${env.GATEWAY_URL}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      // The full pipeline (Understanding -> Query -> Guardrail -> execution
      // -> Insight) can genuinely take a while against a real model/warehouse;
      // this is deliberately generous rather than the browser's own default.
      signal: AbortSignal.timeout(60_000),
    });
  } catch {
    return NextResponse.json(
      { error: 'gateway is unreachable -- is it running and is GATEWAY_URL correct?' },
      { status: 502 },
    );
  }

  if (!gatewayResponse.ok) {
    let detail: unknown;
    try {
      detail = await gatewayResponse.json();
    } catch {
      detail = await gatewayResponse.text();
    }
    return NextResponse.json(
      { error: 'gateway returned an error', status: gatewayResponse.status, detail },
      { status: gatewayResponse.status },
    );
  }

  const payload = await gatewayResponse.json();
  return NextResponse.json(payload);
}

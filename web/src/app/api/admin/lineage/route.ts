import { NextResponse } from 'next/server';
import { env } from '@/lib/env';

/**
 * Server-side proxy for the gateway's `GET /lineage` search route
 * (Phase 15.3). Same rationale as `/api/ask/route.ts`: keeps
 * `GATEWAY_URL` server-only and sidesteps CORS. Forwards every search
 * query param the browser sent verbatim -- this route makes no filtering
 * decisions of its own.
 */
export async function GET(request: Request): Promise<NextResponse> {
  const incoming = new URL(request.url);
  const tenantId = incoming.searchParams.get('tenant_id');
  if (!tenantId) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }

  const target = new URL('/lineage', env.GATEWAY_URL);
  target.search = incoming.search;

  let gatewayResponse: Response;
  try {
    gatewayResponse = await fetch(target, { signal: AbortSignal.timeout(30_000) });
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

  return NextResponse.json(await gatewayResponse.json());
}

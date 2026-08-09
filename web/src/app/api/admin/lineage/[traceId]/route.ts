import { NextResponse } from 'next/server';
import { env } from '@/lib/env';

/**
 * Server-side proxy for the gateway's `GET /lineage/{trace_id}` detail
 * route (Phase 15.3). See `../route.ts`'s identical rationale.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ traceId: string }> },
): Promise<NextResponse> {
  const { traceId } = await params;
  const incoming = new URL(request.url);
  const tenantId = incoming.searchParams.get('tenant_id');
  if (!tenantId) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }

  const target = new URL(`/lineage/${encodeURIComponent(traceId)}`, env.GATEWAY_URL);
  target.searchParams.set('tenant_id', tenantId);

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

import { NextResponse } from 'next/server';
import { proxyJsonPostToGateway, proxyToGateway } from '@/lib/gateway-proxy';
import type { RegisterDataSourceRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's `GET /admin/data-sources` (enriched
 * listing) and `POST /admin/data-sources` (registration, writes real
 * credentials -- see LIMITATIONS.md item 113 for this route's v1 security
 * posture).
 */
export async function GET(request: Request): Promise<NextResponse> {
  const incoming = new URL(request.url);
  const tenantId = incoming.searchParams.get('tenant_id');
  if (!tenantId) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }

  return proxyToGateway(`/admin/data-sources?tenant_id=${encodeURIComponent(tenantId)}`);
}

export async function POST(request: Request): Promise<NextResponse> {
  let body: RegisterDataSourceRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.tenant_id !== 'string' || body.tenant_id.trim().length === 0) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }
  if (typeof body.name !== 'string' || body.name.trim().length === 0) {
    return NextResponse.json({ error: '"name" is required' }, { status: 400 });
  }
  if (typeof body.source_type !== 'string' || body.source_type.trim().length === 0) {
    return NextResponse.json({ error: '"source_type" is required' }, { status: 400 });
  }

  return proxyJsonPostToGateway('/admin/data-sources', body);
}

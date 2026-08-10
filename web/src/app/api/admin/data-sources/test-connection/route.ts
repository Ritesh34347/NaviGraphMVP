import { NextResponse } from 'next/server';
import { proxyJsonPostToGateway } from '@/lib/gateway-proxy';
import type { TestConnectionRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's `POST /admin/data-sources/test-connection`.
 * Stateless dry run -- nothing is persisted regardless of outcome, on
 * either side of this proxy.
 */
export async function POST(request: Request): Promise<NextResponse> {
  let body: TestConnectionRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.source_type !== 'string' || body.source_type.trim().length === 0) {
    return NextResponse.json({ error: '"source_type" is required' }, { status: 400 });
  }

  return proxyJsonPostToGateway('/admin/data-sources/test-connection', body);
}

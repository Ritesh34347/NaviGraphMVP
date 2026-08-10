import { NextResponse } from 'next/server';
import { proxyJsonPostToGateway } from '@/lib/gateway-proxy';
import type { CrawlRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's
 * `POST /admin/data-sources/{id}/crawl`. A generous timeout: introspecting
 * a real schema can genuinely take a while for a large warehouse, same
 * rationale as `api/ask/route.ts`'s own 60s timeout for the full
 * Understanding->Insight pipeline.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;

  let body: CrawlRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.tenant_id !== 'string' || body.tenant_id.trim().length === 0) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }

  return proxyJsonPostToGateway(`/admin/data-sources/${encodeURIComponent(id)}/crawl`, body, {
    timeoutMs: 60_000,
  });
}

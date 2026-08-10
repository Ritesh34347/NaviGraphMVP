import { NextResponse } from 'next/server';
import { proxyJsonPostToGateway } from '@/lib/gateway-proxy';
import type { DraftOntologyRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's
 * `POST /admin/data-sources/{id}/draft-ontology`, which itself proxies to
 * agent-runtime's EXISTING ontology drafting agent -- a real LLM call, so
 * this uses the same generous timeout as `api/ask/route.ts`.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;

  let body: DraftOntologyRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.tenant_id !== 'string' || body.tenant_id.trim().length === 0) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }
  if (typeof body.user_id !== 'string' || body.user_id.trim().length === 0) {
    return NextResponse.json({ error: '"user_id" is required' }, { status: 400 });
  }

  return proxyJsonPostToGateway(
    `/admin/data-sources/${encodeURIComponent(id)}/draft-ontology`,
    body,
    { timeoutMs: 60_000 },
  );
}

import { NextResponse } from 'next/server';
import { proxyJsonPostToGateway } from '@/lib/gateway-proxy';
import type { CompileAndActivateRequestBody } from '@/lib/gateway-types';

/**
 * Server-side proxy for the gateway's
 * `POST /admin/semantic-models/compile-and-activate`. A 422 (structured
 * validation `issues`) passes straight through `proxyJsonPostToGateway`'s
 * normal non-2xx handling -- the response body's `detail.issues` is what
 * `ActivateStep.tsx` reads to render exactly what failed.
 */
export async function POST(request: Request): Promise<NextResponse> {
  let body: CompileAndActivateRequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'request body must be valid JSON' }, { status: 400 });
  }

  if (typeof body.tenant_id !== 'string' || body.tenant_id.trim().length === 0) {
    return NextResponse.json({ error: '"tenant_id" is required' }, { status: 400 });
  }
  if (typeof body.data_source_name !== 'string' || body.data_source_name.trim().length === 0) {
    return NextResponse.json({ error: '"data_source_name" is required' }, { status: 400 });
  }
  if (body.draft == null) {
    return NextResponse.json({ error: '"draft" is required' }, { status: 400 });
  }

  return proxyJsonPostToGateway('/admin/semantic-models/compile-and-activate', body);
}

import type { NextResponse } from 'next/server';
import { proxyToGateway } from '@/lib/gateway-proxy';

/**
 * Server-side proxy for the gateway's `GET /admin/data-sources/connector-types`.
 * No tenant scope to validate -- this is a static, source-type-level
 * manifest (which connectors are registered and what fields they need).
 */
export async function GET(): Promise<NextResponse> {
  return proxyToGateway('/admin/data-sources/connector-types');
}

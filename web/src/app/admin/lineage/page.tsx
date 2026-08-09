import type { Metadata } from 'next';
import type { ReactElement } from 'react';
import { env } from '@/lib/env';
import LineageSearchClient from './LineageSearchClient';

export const metadata: Metadata = {
  title: 'NaviGraph Admin — Lineage Search',
};

export default function AdminLineagePage(): ReactElement {
  return <LineageSearchClient defaultTenantId={env.NEXT_PUBLIC_DEFAULT_TENANT_ID} />;
}

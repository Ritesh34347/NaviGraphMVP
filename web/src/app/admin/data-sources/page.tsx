import type { Metadata } from 'next';
import type { ReactElement } from 'react';
import { env } from '@/lib/env';
import DataSourceWizard from './DataSourceWizard';

export const metadata: Metadata = {
  title: 'NaviGraph Admin — Data Sources',
};

export default function AdminDataSourcesPage(): ReactElement {
  return <DataSourceWizard defaultTenantId={env.NEXT_PUBLIC_DEFAULT_TENANT_ID} />;
}

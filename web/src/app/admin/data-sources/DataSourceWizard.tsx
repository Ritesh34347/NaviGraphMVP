'use client';

import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import type {
  AdminDataSourcesResponse,
  CompileAndActivateResponse,
  ConnectionTestResult,
  ConnectorTypeInfo,
  ConnectorTypesResponse,
  CrawlResponse,
  OntologyDraftingResult,
  RegisteredDataSource,
} from '@/lib/gateway-types';
import ChooseConnectorTypeStep from './steps/ChooseConnectorTypeStep';
import ConnectionStep from './steps/ConnectionStep';
import CrawlStep from './steps/CrawlStep';
import ReviewDraftStep from './steps/ReviewDraftStep';
import ActivateStep from './steps/ActivateStep';
import DataSourceListClient from './DataSourceListClient';
import * as s from './styles';

type WizardStep = 'choose-type' | 'connection' | 'crawl' | 'review' | 'activate';

const STEPS: { key: WizardStep; label: string }[] = [
  { key: 'choose-type', label: 'Type' },
  { key: 'connection', label: 'Connect' },
  { key: 'crawl', label: 'Crawl' },
  { key: 'review', label: 'Review' },
  { key: 'activate', label: 'Activate' },
];

interface DataSourceWizardProps {
  defaultTenantId: string;
}

export default function DataSourceWizard({ defaultTenantId }: DataSourceWizardProps): ReactElement {
  const [tenantId, setTenantId] = useState(defaultTenantId);
  const [userId, setUserId] = useState('onboarding-admin');

  const [listResponse, setListResponse] = useState<AdminDataSourcesResponse | null>(null);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [step, setStep] = useState<WizardStep>('choose-type');

  const [connectorTypes, setConnectorTypes] = useState<ConnectorTypeInfo[]>([]);
  const [isLoadingConnectorTypes, setIsLoadingConnectorTypes] = useState(false);
  const [connectorTypesError, setConnectorTypesError] = useState<string | null>(null);
  const [selectedSourceType, setSelectedSourceType] = useState<string | null>(null);

  const [dataSourceName, setDataSourceName] = useState('');
  const [credentialFields, setCredentialFields] = useState<Record<string, string>>({});
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [registeredDataSource, setRegisteredDataSource] = useState<RegisteredDataSource | null>(
    null,
  );

  const [isCrawling, setIsCrawling] = useState(false);
  const [crawlResult, setCrawlResult] = useState<CrawlResponse | null>(null);
  const [crawlError, setCrawlError] = useState<string | null>(null);

  const [isDrafting, setIsDrafting] = useState(false);
  const [draft, setDraft] = useState<OntologyDraftingResult | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [approvedDraft, setApprovedDraft] = useState<OntologyDraftingResult | null>(null);

  const [isActivating, setIsActivating] = useState(false);
  const [activateResult, setActivateResult] = useState<CompileAndActivateResponse | null>(null);
  const [activateIssues, setActivateIssues] = useState<string[] | null>(null);
  const [activateError, setActivateError] = useState<string | null>(null);

  async function fetchDataSources(): Promise<void> {
    if (!tenantId.trim()) return;
    setIsLoadingList(true);
    setListError(null);
    try {
      const res = await fetch(`/api/admin/data-sources?tenant_id=${encodeURIComponent(tenantId.trim())}`);
      const body = await res.json();
      if (!res.ok) {
        // Distinct from "genuinely zero data sources" -- a failed fetch
        // must never render as an empty state, or a real outage looks
        // identical to a brand-new tenant.
        setListError(typeof body?.error === 'string' ? body.error : 'failed to load data sources');
        setListResponse(null);
        return;
      }
      setListResponse(body as AdminDataSourcesResponse);
    } catch {
      setListError('Could not reach the server. Please try again.');
      setListResponse(null);
    } finally {
      setIsLoadingList(false);
    }
  }

  async function fetchConnectorTypes(): Promise<void> {
    setIsLoadingConnectorTypes(true);
    setConnectorTypesError(null);
    try {
      const res = await fetch('/api/admin/data-sources/connector-types');
      const body = await res.json();
      if (!res.ok) {
        setConnectorTypesError(
          typeof body?.error === 'string' ? body.error : 'failed to load connector types',
        );
        return;
      }
      setConnectorTypes((body as ConnectorTypesResponse).source_types);
    } catch {
      setConnectorTypesError('Could not reach the server. Please try again.');
    } finally {
      setIsLoadingConnectorTypes(false);
    }
  }

  useEffect(() => {
    void fetchConnectorTypes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void fetchDataSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);

  const selectedConnectorInfo = connectorTypes.find((c) => c.source_type === selectedSourceType);

  async function handleTestConnection(): Promise<void> {
    if (!selectedSourceType) return;
    setIsTesting(true);
    setTestResult(null);
    setConnectionError(null);
    try {
      const res = await fetch('/api/admin/data-sources/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_type: selectedSourceType, credential_fields: credentialFields }),
      });
      const body = await res.json();
      if (!res.ok) {
        setConnectionError(typeof body?.error === 'string' ? body.error : 'connection test failed');
        return;
      }
      setTestResult(body as ConnectionTestResult);
    } catch {
      setConnectionError('Could not reach the server. Please try again.');
    } finally {
      setIsTesting(false);
    }
  }

  async function handleRegisterAndContinue(): Promise<void> {
    if (!selectedSourceType || !dataSourceName.trim()) return;
    setIsRegistering(true);
    setConnectionError(null);
    try {
      const res = await fetch('/api/admin/data-sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: tenantId.trim(),
          name: dataSourceName.trim(),
          source_type: selectedSourceType,
          credential_fields: credentialFields,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setConnectionError(
          typeof body?.error === 'string' ? body.error : 'failed to register data source',
        );
        return;
      }
      setRegisteredDataSource(body as RegisteredDataSource);
      setStep('crawl');
    } catch {
      setConnectionError('Could not reach the server. Please try again.');
    } finally {
      setIsRegistering(false);
    }
  }

  async function handleCrawl(): Promise<void> {
    if (!registeredDataSource) return;
    setIsCrawling(true);
    setCrawlError(null);
    try {
      const res = await fetch(`/api/admin/data-sources/${registeredDataSource.id}/crawl`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId.trim() }),
      });
      const body = await res.json();
      if (!res.ok) {
        setCrawlError(typeof body?.error === 'string' ? body.error : 'crawl failed');
        return;
      }
      setCrawlResult(body as CrawlResponse);
    } catch {
      setCrawlError('Could not reach the server. Please try again.');
    } finally {
      setIsCrawling(false);
    }
  }

  async function handleRequestDraft(): Promise<void> {
    if (!registeredDataSource) return;
    setIsDrafting(true);
    setDraftError(null);
    try {
      const res = await fetch(`/api/admin/data-sources/${registeredDataSource.id}/draft-ontology`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId.trim(), user_id: userId.trim() || 'onboarding-admin' }),
      });
      const body = await res.json();
      if (!res.ok) {
        setDraftError(typeof body?.error === 'string' ? body.error : 'ontology drafting failed');
        return;
      }
      setDraft(body.result as OntologyDraftingResult);
    } catch {
      setDraftError('Could not reach the server. Please try again.');
    } finally {
      setIsDrafting(false);
    }
  }

  function handleReviewContinue(finalDraft: OntologyDraftingResult): void {
    setApprovedDraft(finalDraft);
    setStep('activate');
  }

  async function handleActivate(): Promise<void> {
    if (!approvedDraft || !registeredDataSource) return;
    setIsActivating(true);
    setActivateError(null);
    setActivateIssues(null);
    try {
      const res = await fetch('/api/semantic-models/compile-and-activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: tenantId.trim(),
          data_source_name: registeredDataSource.name,
          draft: approvedDraft,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        const issues = body?.detail?.issues;
        if (Array.isArray(issues)) {
          setActivateIssues(issues);
        } else {
          setActivateError(typeof body?.error === 'string' ? body.error : 'activation failed');
        }
        return;
      }
      setActivateResult(body as CompileAndActivateResponse);
      void fetchDataSources();
    } catch {
      setActivateError('Could not reach the server. Please try again.');
    } finally {
      setIsActivating(false);
    }
  }

  function handleStartOver(): void {
    setStep('choose-type');
    setSelectedSourceType(null);
    setDataSourceName('');
    setCredentialFields({});
    setTestResult(null);
    setConnectionError(null);
    setRegisteredDataSource(null);
    setCrawlResult(null);
    setCrawlError(null);
    setDraft(null);
    setDraftError(null);
    setApprovedDraft(null);
    setActivateResult(null);
    setActivateIssues(null);
    setActivateError(null);
  }

  const currentStepIndex = STEPS.findIndex((s2) => s2.key === step);

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '1.5rem 1rem 3rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <div>
        <h1 style={{ margin: 0, fontSize: '1.4rem' }}>Data Sources</h1>
        <p style={{ margin: '0.4rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Connect, configure, and crawl your own data sources, then review and activate an
          AI-drafted ontology — no platform engineer required.
        </p>
      </div>

      <div style={s.card}>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <label style={{ ...s.label, flex: 1, minWidth: 180 }}>
            Tenant ID
            <input style={s.input} value={tenantId} onChange={(e) => setTenantId(e.target.value)} />
          </label>
          <label style={{ ...s.label, flex: 1, minWidth: 180 }}>
            Your user ID
            <input style={s.input} value={userId} onChange={(e) => setUserId(e.target.value)} />
          </label>
        </div>
      </div>

      <DataSourceListClient
        dataSources={listResponse?.data_sources ?? []}
        semanticModelActiveVersion={listResponse?.semantic_model_active_version ?? null}
        isLoading={isLoadingList}
        error={listError}
        onRefresh={() => void fetchDataSources()}
      />

      <div style={s.stepIndicator}>
        {STEPS.map((s2, i) => (
          <span key={s2.key} style={s.stepPill(s2.key === step, i < currentStepIndex)}>
            {i + 1}. {s2.label}
          </span>
        ))}
      </div>

      {step === 'choose-type' && (
        <ChooseConnectorTypeStep
          connectorTypes={connectorTypes}
          isLoading={isLoadingConnectorTypes}
          error={connectorTypesError}
          selected={selectedSourceType}
          onSelect={setSelectedSourceType}
          onNext={() => setStep('connection')}
        />
      )}

      {step === 'connection' && selectedSourceType && (
        <ConnectionStep
          sourceType={selectedSourceType}
          requiredSettings={selectedConnectorInfo?.required_settings ?? []}
          name={dataSourceName}
          onNameChange={setDataSourceName}
          credentialFields={credentialFields}
          onFieldChange={(field, value) =>
            setCredentialFields((prev) => ({ ...prev, [field]: value }))
          }
          onTestConnection={() => void handleTestConnection()}
          isTesting={isTesting}
          testResult={testResult}
          onRegisterAndContinue={() => void handleRegisterAndContinue()}
          isRegistering={isRegistering}
          error={connectionError}
          onBack={() => setStep('choose-type')}
        />
      )}

      {step === 'crawl' && registeredDataSource && (
        <CrawlStep
          dataSourceName={registeredDataSource.name}
          onCrawl={() => void handleCrawl()}
          isCrawling={isCrawling}
          crawlResult={crawlResult}
          error={crawlError}
          onContinue={() => setStep('review')}
        />
      )}

      {step === 'review' && (
        <ReviewDraftStep
          isDrafting={isDrafting}
          draft={draft}
          error={draftError}
          onRequestDraft={() => void handleRequestDraft()}
          onContinue={handleReviewContinue}
        />
      )}

      {step === 'activate' && registeredDataSource && (
        <ActivateStep
          dataSourceName={registeredDataSource.name}
          isActivating={isActivating}
          result={activateResult}
          issues={activateIssues}
          error={activateError}
          onActivate={() => void handleActivate()}
          onStartOver={handleStartOver}
        />
      )}
    </div>
  );
}

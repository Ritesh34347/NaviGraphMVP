import Link from "next/link";

import { env } from "@/lib/env";

import ChatDemo from "./ChatDemo";

interface GatewayStatus {
  reachable: boolean;
  detail: string;
}

async function getGatewayStatus(): Promise<GatewayStatus> {
  const gatewayUrl = env.GATEWAY_URL;

  try {
    const res = await fetch(`${gatewayUrl}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });

    if (!res.ok) {
      return { reachable: false, detail: `gateway responded with status ${res.status}` };
    }

    return { reachable: true, detail: `gateway reachable at ${gatewayUrl}` };
  } catch {
    // The gateway service may not be up yet (e.g. during local/dev
    // bring-up before all docker-compose services are running) - render a
    // graceful fallback instead of letting the page render fail.
    return { reachable: false, detail: "gateway unreachable" };
  }
}

export default async function HomePage() {
  const status = await getGatewayStatus();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">N</span>
          NaviGraph
        </div>
        <div className="header-badges">
          <span className="pill" title={status.detail}>
            <span
              className="pill-dot"
              style={!status.reachable ? { background: "var(--danger)" } : undefined}
            />
            {status.reachable ? "Live" : "Reconnecting"}
          </span>
          <span className="pill">Analyst view</span>
          <Link href="/chat" className="pill" style={{ textDecoration: "none" }}>
            Chat
          </Link>
          <Link href="/admin/lineage" className="pill" style={{ textDecoration: "none" }}>
            Admin: Lineage
          </Link>
        </div>
      </header>

      <main>
        <div className="page-inner">
          <div className="intro">
            <h1>Ask anything about your data</h1>
            <p>
              NaviGraph turns natural-language questions into governed, explainable answers —
              grounded in your real schema, checked against access policy, and cited against the
              actual numbers behind every chart.
            </p>
          </div>
          <ChatDemo gatewayUrl={env.NEXT_PUBLIC_GATEWAY_URL} />
          <p className="app-footer">
            Demo workspace · responses are generated live from your connected data source
          </p>
        </div>
      </main>
    </div>
  );
}

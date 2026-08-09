import Link from "next/link";
import { env } from "@/lib/env";

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
    <main>
      <h1>NaviGraph</h1>
      <p>
        NaviGraph is a multi-tenant conversational BI platform that lets teams ask questions of
        their data in natural language and get back trustworthy, explainable answers.
      </p>
      <p>Gateway status: {status.reachable ? "reachable" : "gateway unreachable"}</p>
      <p>{status.detail}</p>
      <p>
        <Link href="/chat">Open the chat UI</Link>
      </p>
    </main>
  );
}

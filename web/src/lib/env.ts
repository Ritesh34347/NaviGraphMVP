/**
 * Centralized, typed access to the environment variables used by the web
 * app.
 *
 * Phase 1 note: this is intentionally permissive. None of the Azure AD /
 * NextAuth variables are required just to start the dev server or render
 * the placeholder page - they only matter once someone actually exercises
 * sign-in, which will not work until a real Entra ID (Azure AD) app
 * registration exists and its values are supplied. Until then we fall back
 * to obviously-fake placeholder values rather than throwing, so `next dev`
 * / `next build` never crashes just because an Azure AD var is unset.
 */

function optional(name: string, fallback: string): string {
  const value = process.env[name];
  return value && value.length > 0 ? value : fallback;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value || value.length === 0) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const env = {
  // Server-side base URL for the gateway API, e.g. "http://gateway:8000"
  // inside docker-compose. Falls back to localhost for bare `next dev`.
  GATEWAY_URL: optional("GATEWAY_URL", "http://localhost:8000"),

  // Public, browser-visible gateway URL (must be set at build time to be
  // inlined for client components). Falls back to the same default as
  // GATEWAY_URL since, for local/dev use, the gateway is reachable at the
  // same address from both server and browser.
  NEXT_PUBLIC_GATEWAY_URL: optional("NEXT_PUBLIC_GATEWAY_URL", "http://localhost:8000"),

  // Azure AD (Entra ID) / NextAuth configuration. These are not real
  // credentials yet - see the note in
  // src/app/api/auth/[...nextauth]/route.ts. They are deliberately
  // defaulted rather than required so the app doesn't crash before a real
  // Entra app registration exists.
  AZURE_AD_CLIENT_ID: optional("AZURE_AD_CLIENT_ID", "not-configured"),
  AZURE_AD_CLIENT_SECRET: optional("AZURE_AD_CLIENT_SECRET", "not-configured"),
  AZURE_AD_TENANT_ID: optional("AZURE_AD_TENANT_ID", "common"),

  // Used by NextAuth to encrypt session/JWT data. Safe to default in
  // Phase 1 since no real auth happens yet, but production deployments
  // MUST set a strong, random NEXTAUTH_SECRET (e.g. `openssl rand -hex 32`).
  NEXTAUTH_SECRET: optional("NEXTAUTH_SECRET", "phase-1-dev-secret-not-for-production"),

  // Used by NextAuth to build callback URLs. Genuinely required once auth
  // is actually exercised, but still defaulted here to keep Phase 1
  // permissive.
  NEXTAUTH_URL: optional("NEXTAUTH_URL", "http://localhost:3000"),
};

/**
 * Throws a clear error if a variable that is truly required *at the point
 * of use* is missing. Not called anywhere in Phase 1 - kept as the escape
 * hatch for later phases that genuinely cannot proceed without a value
 * (e.g. a database connection string before running migrations).
 */
export function requireEnv(name: string): string {
  return required(name);
}

import NextAuth from "next-auth";
import AzureADProvider from "next-auth/providers/azure-ad";
import { env } from "@/lib/env";

// Phase 1 stub: this wires the real NextAuth (v4) App Router route handler
// shape with the Azure AD (Entra ID) provider, but sign-in will NOT
// actually succeed until a real Entra ID app registration's
// AZURE_AD_CLIENT_ID, AZURE_AD_CLIENT_SECRET, and AZURE_AD_TENANT_ID are
// supplied via environment variables (e.g. in a `.env` file). Until then
// env.ts supplies placeholder values so the app can build and run. This is
// expected for Phase 1 - the goal here is to have the auth plumbing
// (route, callback URLs, session handling) in place and exercisable end to
// end once real credentials land.
const handler = NextAuth({
  providers: [
    AzureADProvider({
      clientId: env.AZURE_AD_CLIENT_ID,
      clientSecret: env.AZURE_AD_CLIENT_SECRET,
      tenantId: env.AZURE_AD_TENANT_ID,
    }),
  ],
  secret: env.NEXTAUTH_SECRET,
});

export { handler as GET, handler as POST };

import type { Metadata } from 'next';
import type { ReactElement } from 'react';
import { env } from '@/lib/env';
import ChatClient from './ChatClient';

export const metadata: Metadata = {
  title: 'NaviGraph Chat',
};

// Phase 14 note: there is no real mapping yet from a signed-in NextAuth
// session to a NaviGraph `tenant_id`/`user_id` -- see `env.ts`'s
// `NEXT_PUBLIC_DEFAULT_TENANT_ID` for why this still uses a fixed dev-mode
// tenant. `user_id` is similarly a placeholder until that wiring exists;
// both flow straight through to the gateway's `/ask`, which treats them
// exactly like any other caller-supplied identity today.
export default function ChatPage(): ReactElement {
  return <ChatClient tenantId={env.NEXT_PUBLIC_DEFAULT_TENANT_ID} userId="web-user" />;
}

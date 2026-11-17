import { useEffect, useState } from 'react';
import { getSessionState, SESSION_CHANGED_EVENT } from '../../services/session';

export function SandboxMarkings({ markings, label = 'Effective markings' }: { markings?: string[]; label?: string }) {
  if (!markings?.length) return null;
  return <span>{label}: {markings.map((mark, index) => <span key={mark}>{index > 0 && ', '}<code>{mark}</code></span>)}</span>;
}

export const sandboxScopeDenied = (failure: unknown) => {
  const status = (failure as { response?: { status?: number } })?.response?.status;
  return status === 401 || status === 403 || status === 404;
};

function currentIdentity() {
  const session = getSessionState();
  return session.authenticated && !session.locked ? session.identity : '';
}

/** Drop scoped sandbox UI state on sign-out, lock or identity replacement. */
export function useSandboxIdentity() {
  const [identity, setIdentity] = useState(currentIdentity);
  useEffect(() => {
    const update = () => setIdentity(currentIdentity());
    window.addEventListener(SESSION_CHANGED_EVENT, update);
    window.addEventListener('storage', update);
    update();
    return () => {
      window.removeEventListener(SESSION_CHANGED_EVENT, update);
      window.removeEventListener('storage', update);
    };
  }, []);
  return identity;
}

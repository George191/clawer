import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { fetchBatchSourceIdentity, verifyBatchSourceIdentity } from '../../services/api';
import type { BatchSourceIdentity, DataSource } from '../../services/api';

// Keep sub-millisecond precision when comparing PostgreSQL revisions.
export const sameSourceRevision = (a: string | null | undefined, b: string) =>
  a != null && a.replace(/\+00:00$/, 'Z') === b.replace(/\+00:00$/, 'Z');
const messages = {
  unverified: 'Not verified',
  blocked: 'Source identity policy is blocked',
  paused: 'Connection paused',
  verified: 'Verified for this saved revision',
  stale: 'Requires review',
  unknown: 'Identity could not be confirmed',
  loading: 'Loading identity status…',
};

export default function SourceIdentity({ source, identity, onIdentity, onVerificationStarted, onVerificationSettled, disabled }: {
  source: DataSource; identity: BatchSourceIdentity | undefined;
  onIdentity: (status: BatchSourceIdentity | undefined) => void; onVerificationStarted?: () => void | (() => void);
  onVerificationSettled?: () => void; disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  const load = () => {
    const request = new AbortController();
    pending.current?.abort(); pending.current = request;
    setError('');
    setBusy(true);
    void fetchBatchSourceIdentity(source.id, request.signal).then(value => {
      if (!request.signal.aborted) onIdentity(value);
    }).catch(() => {
      if (!request.signal.aborted) {
        onIdentity(undefined);
        setError('Could not load the saved source identity status.');
      }
    }).finally(() => { if (!request.signal.aborted) setBusy(false); });
  };
  useEffect(() => {
    load();
    return () => pending.current?.abort();
  }, [source.id, source.updatedAt]);
  if (identity?.state === 'disabled') return null;
  const status = identity?.state === 'stale' ? 'stale' : error ? 'unknown' : !identity ? 'loading' : identity.state === 'verified' && !sameSourceRevision(identity.sourceUpdatedAt, source.updatedAt) ? 'stale' : identity.state;
  const checked = status === 'verified' ? identity?.verifiedAt : null;
  const verify = () => {
    if (disabled || busy || !identity || identity.state === 'blocked' || identity.state === 'paused') return;
    const request = new AbortController();
    pending.current?.abort(); pending.current = request;
    setError(''); setBusy(true);
    const release = onVerificationStarted?.();
    if (release) request.signal.addEventListener('abort', release, { once: true });
    onIdentity({ state: 'unverified', sourceUpdatedAt: null, verifiedAt: null });
    void verifyBatchSourceIdentity(source.id, source.updatedAt, request.signal).then(value => {
      if (!request.signal.aborted) onIdentity(value);
    }).catch((failure: unknown) => {
      if (request.signal.aborted) return;
      const code = (failure as { response?: { status?: number } })?.response?.status;
      if (code === 409) {
        onIdentity({ state: 'stale', sourceUpdatedAt: null, verifiedAt: null });
        setError('Review the saved revision and the configured identity before retrying.');
      } else {
        setError('The verification did not establish this source’s identity. Retry or reload the saved source.');
      }
    }).finally(() => {
      if (!request.signal.aborted) {
        if (release) request.signal.removeEventListener('abort', release);
        setBusy(false); onVerificationSettled?.();
      }
    });
  };
  return <section className="ontology-source-catalog ontology-source-sync" aria-label="Source identity" aria-busy={busy}>
    <header><div><h3>Source identity</h3><p>A connectivity test proves access, not the identity of the reviewed source.</p></div>
      <Button size="small" onClick={identity && status !== 'blocked' && status !== 'paused' ? verify : load} disabled={disabled || busy}>{identity && status !== 'blocked' && status !== 'paused' ? 'Verify source identity' : 'Refresh identity status'}</Button>
    </header>
    <div className="ontology-source-sync__body">
      <p role="status"><strong>{busy ? 'Checking source identity…' : messages[status]}</strong>{checked && Number.isFinite(Date.parse(checked)) && <> · Checked {new Date(checked).toLocaleString()}</>}</p>
      {error && <p role="alert">{error}</p>}
      {status === 'blocked' && <p>This source was verified under an identity policy that is currently unavailable. Saving syncs, starting new runs and arming schedules are blocked. Existing history, exact receipt recovery and schedule pausing remain available.</p>}
      <p>Verification reads identity metadata only. It does not read business rows or run a sync. A recorded check does not certify the source between runs.</p>
    </div>
  </section>;
}

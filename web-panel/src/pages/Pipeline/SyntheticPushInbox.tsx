import { useEffect, useId, useState } from 'react';
import { Button } from '@/components/ui';
import { fetchSyntheticPushInbox } from '../../services/api';
import type { SyntheticPushInboxStatus } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';
import SyntheticPushApply from './SyntheticPushApply';

export default function SyntheticPushInbox() {
  const identity = useSandboxIdentity();
  return identity ? <PushInboxStatus key={identity} identity={identity} /> : null;
}

function AcceptedAt({ value }: { value?: string }) {
  return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : <>Not recorded</>;
}

function PushInboxStatus({ identity }: { identity: string }) {
  const title = useId();
  const [snapshot, setSnapshot] = useState<SyntheticPushInboxStatus>();
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const request = new AbortController();
    setRefreshing(true);
    void fetchSyntheticPushInbox(request.signal).then(value => {
      if (!request.signal.aborted) setSnapshot(value);
    }).catch(() => {
      if (!request.signal.aborted) setSnapshot(undefined);
    }).finally(() => {
      if (!request.signal.aborted) setRefreshing(false);
    });
    return () => request.abort();
  }, [revision]);

  if (!snapshot?.available || snapshot.state === 'disabled' || snapshot.state === 'unconfigured') return null;
  const [latest, ...earlier] = snapshot.recent;
  return <section className="ontology-source-catalog ontology-source-sync" aria-labelledby={title} aria-busy={refreshing}>
    <header>
      <div><h3 id={title}>Synthetic Push inbox</h3><p>Read-only intake status for local synthetic events; separate from CDC replay.</p></div>
      <Button size="small" loading={refreshing} onClick={() => setRevision(value => value + 1)}>Refresh inbox status</Button>
    </header>
    <div className="ontology-source-sync__body">
      <p role="status">{snapshot.state === 'full' ? 'Inbox full' : 'Inbox available'} · Acceptance only</p>
      <dl className="ontology-connections-summary">
        <div><dt>Accepted events</dt><dd>{snapshot.acceptedCount.toLocaleString()}</dd></div>
        <div><dt>Latest accepted receipt</dt><dd>{latest ? <code>{latest.id}</code> : 'None recorded'}</dd></div>
        <div><dt>Accepted at</dt><dd><AcceptedAt value={latest?.acceptedAt} /></dd></div>
      </dl>
      {earlier.length > 0 && <details>
        <summary>Earlier accepted receipts</summary>
        <ol className="ontology-source-sync__items">{earlier.map(receipt => <li key={receipt.id}>
          <span>Receipt <code>{receipt.id}</code></span><span>Accepted at <AcceptedAt value={receipt.acceptedAt} /></span>
        </li>)}</ol>
      </details>}
      <p><strong>Acceptance is not application.</strong> Inbox acceptance alone does not show that any target changed or data flowed downstream.</p>
      <SyntheticPushApply identity={identity} version={revision} />
    </div>
  </section>;
}

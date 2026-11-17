import { useState } from 'react';
import { Button, Modal } from '@/components/ui';
import type { AssetFreshness, AssetQualityIncident } from '../../services/api';

const kindLabels: Record<AssetQualityIncident['kind'], string> = {
  freshness: 'Transfer freshness', schema: 'Schema check', heap_size: 'Heap size check', daily_job: 'Scheduled transfer outcome',
};
const statusLabels: Record<AssetQualityIncident['status'], string> = {
  open: 'Open', acknowledged: 'Acknowledged · still open', recovered: 'Recovered', superseded: 'Superseded',
};
const reasonLabels: Record<AssetQualityIncident['reason'], string> = {
  write_overdue: 'No confirmed write within the configured threshold.',
  schema_drift: 'The catalog schema differs from the reviewed baseline.',
  below_minimum: 'The heap is smaller than the configured minimum.',
  above_maximum: 'The heap exceeds the configured maximum.',
  target_rejected: 'The reviewed scheduled job was definitively rejected by its target.',
};
const closeLabels: Record<NonNullable<AssetQualityIncident['closeReason']>, string> = {
  healthy: 'A later evaluation confirmed recovery.',
  policy_paused: 'Policy paused. This does not confirm recovery.',
  policy_reviewed: 'Policy changed. This does not confirm recovery.',
  schema_baseline_captured: 'A new schema baseline was captured. This does not confirm recovery.',
  schedule_changed: 'The reviewed schedule changed. This does not confirm recovery.',
};

function Time({ value }: { value: string }) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}

function WarningDetails({ incident }: { incident: AssetQualityIncident }) {
  return <>
    <div><strong>{kindLabels[incident.kind]}</strong><span>{statusLabels[incident.status]}</span></div>
    <p>{reasonLabels[incident.reason]}</p>
    <div><span>Policy generation</span><span>{incident.generation}</span></div>
    <div><span>Opened</span><Time value={incident.openedAt} /></div>
    <div><span>Last confirmed failure</span><Time value={incident.lastConfirmedAt} /></div>
    {incident.acknowledgedAt && <div><span>Acknowledged at</span><Time value={incident.acknowledgedAt} /></div>}
    {incident.closedAt && <div><span>{incident.status === 'recovered' ? 'Recovered at' : 'Superseded at'}</span><Time value={incident.closedAt} /></div>}
    {incident.closeReason && <p>{closeLabels[incident.closeReason]}</p>}
  </>;
}

export default function ExplorerQualityWarnings({ incidents, generation, busy, onAcknowledge }: {
  incidents: AssetFreshness['incidents']; generation: number | undefined; busy: boolean;
  onAcknowledge: (incident: AssetQualityIncident) => Promise<void>;
}) {
  const [review, setReview] = useState<AssetQualityIncident>();
  return <section className="flow-evidence-quality-warnings" aria-label="Quality warnings">
    <h5>Quality warnings</h5>
    <p>Warnings are recorded here only. Acknowledgment does not clear the issue. An unknown or expired check does not resolve an open warning.</p>
    <h6>Current warnings</h6>
    {incidents.active.length ? <ul className="flow-evidence-receipts" aria-label="Current warnings">
      {incidents.active.map(incident => <li key={incident.id}>
        <WarningDetails incident={incident} />
        {incident.status === 'open' && <Button size="small" disabled={busy || incident.generation !== generation} onClick={() => setReview(incident)}>Acknowledge warning</Button>}
      </li>)}
    </ul> : <p>No active warnings recorded.</p>}
    <details>
      <summary>Warning history</summary>
      {incidents.history.length ? <ol className="flow-evidence-receipts" aria-label="Resolved or superseded warnings">
        {incidents.history.map(incident => <li key={incident.id}><WarningDetails incident={incident} /></li>)}
      </ol> : <p>No recovered or superseded warnings recorded.</p>}
    </details>
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title="Acknowledge quality warning?"
      onCancel={() => { if (!busy) setReview(undefined); }} confirmLoading={busy} okText="Confirm acknowledgement"
      onOk={async () => { if (review && !busy) { await onAcknowledge(review); setReview(undefined); } }}>
      {review && <>
        <p><strong>{kindLabels[review.kind]}</strong>: {reasonLabels[review.reason]}</p>
        <p>Opened <Time value={review.openedAt} />. Acknowledging records your review and keeps the warning open.</p>
      </>}
    </Modal>
  </section>;
}

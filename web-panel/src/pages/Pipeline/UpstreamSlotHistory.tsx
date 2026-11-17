import { useEffect, useState } from 'react';
import { fetchTransferUpstreamSlots } from '../../services/api';
import type { TransferUpstreamSlot } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';

export function UpstreamUtcTime({ value }: { value: string }) {
  const time = new Date(value);
  return Number.isFinite(time.getTime()) ? <time dateTime={value}>{time.toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time> : <>Not recorded</>;
}

export default function UpstreamSlotHistory({ planId, version }: { planId: string; version: number }) {
  const identity = useSandboxIdentity();
  return identity ? <History key={`${identity}:${planId}:${version}`} planId={planId} /> : null;
}

function History({ planId }: { planId: string }) {
  const [slots, setSlots] = useState<TransferUpstreamSlot[]>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const read = new AbortController();
    void fetchTransferUpstreamSlots(planId, read.signal).then(value => { if (!read.signal.aborted) setSlots(value); })
      .catch(() => { if (!read.signal.aborted) { setSlots(undefined); setFailed(true); } });
    return () => read.abort();
  }, [planId]);
  return <section aria-label="UTC gate decisions">
    <h4>UTC gate decisions</h4>
    <p>A slot without a matching pre-slot witness is not a run. A recorded admission does not prove a child write.</p>
    {failed ? <p role="status">Gate decision history is unavailable.</p>
      : !slots ? <p role="status">Loading UTC gate decisions…</p>
        : !slots.length ? <p>No UTC gate decisions recorded.</p>
          : <ol className="flow-transfer-receipts">{slots.map(slot => <li key={`${slot.slot}:${slot.decidedAt}`}>
            <strong>{slot.state === 'no_witness' ? 'No matching witness · no run' : 'Matching witness admitted · job recorded'}</strong>
            <div>UTC slot: <UpstreamUtcTime value={slot.slot} /></div>
            <div>Decision recorded at: <UpstreamUtcTime value={slot.decidedAt} /></div>
            {slot.witnessObservedAt && <div>Witness observed at: <UpstreamUtcTime value={slot.witnessObservedAt} /></div>}
            {slot.state === 'admitted' && slot.jobId && <div>Job ID: <code>{slot.jobId}</code></div>}
          </li>)}</ol>}
  </section>;
}

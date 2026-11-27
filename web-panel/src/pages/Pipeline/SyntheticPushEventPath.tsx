import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { checkSyntheticPushEventPath } from '../../services/api';
import type { SyntheticPushAcceptedReceipt, SyntheticPushEventPath as EventPath } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt } from './transferReceiptFocus';

const isV4 = (id: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id);
const validTime = (value?: string): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
function Time({ value }: { value: string }) {
  return <time dateTime={value}>{new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time>;
}

function matches(value: EventPath, receipt: SyntheticPushAcceptedReceipt) {
  if (value.receiptId !== receipt.id || value.ordinal !== receipt.ordinal || !/^[1-9]\d{0,18}$/.test(value.ordinal) ||
    value.acceptedAt !== receipt.acceptedAt || !validTime(value.acceptedAt) || !validTime(value.observedAt) ||
    Date.parse(value.observedAt) < Date.parse(value.acceptedAt)) return false;
  if (value.status === 'accepted_not_applied_at_observation') return value.applyReceiptId == null && value.appliedAt == null;
  return value.status === 'applied_recorded' && typeof value.applyReceiptId === 'string' && canonicalTransferReceipt(value.applyReceiptId) &&
    validTime(value.appliedAt) && Date.parse(value.observedAt) >= Date.parse(value.appliedAt) &&
    Date.parse(value.appliedAt) >= Date.parse(value.acceptedAt);
}

export default function SyntheticPushEventPath({ identity, receipt }: { identity: string; receipt: SyntheticPushAcceptedReceipt }) {
  const [open, setOpen] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [evidence, setEvidence] = useState<EventPath>();
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const close = () => {
    pending.current?.abort(); pending.current = undefined;
    setOpen(false); setUnavailable(false); setEvidence(undefined);
  };
  const check = async () => {
    if (pending.current || !isV4(receipt.id) || !live()) return;
    const read = new AbortController(); pending.current = read;
    setOpen(true); setEvidence(undefined); setUnavailable(false);
    try {
      const result = await checkSyntheticPushEventPath(receipt.id, read.signal);
      if (live(read.signal)) { if (matches(result, receipt)) setEvidence(result); else setUnavailable(true); }
    } catch {
      if (live(read.signal)) setUnavailable(true);
    } finally {
      if (pending.current === read) pending.current = undefined;
    }
  };

  if (!isV4(receipt.id)) return null;
  return <>
    <Button size="small" onClick={() => void check()}>Check application evidence</Button>
    <Modal open={open} destroyOnHidden className="flow-surface-modal" title="Application evidence" footer={null} onCancel={close}>
      {unavailable ? <p role="status">Application evidence is unavailable.</p> : !evidence ? <p role="status"><LoadingIndicator size={20}>Checking recorded application evidence…</LoadingIndicator></p> : <>
        <strong role="status">{evidence.status === 'applied_recorded' ? 'Application receipt verified.' : 'Accepted, with no application recorded at observation.'}</strong>
        <p>This is a point-in-time check of recorded evidence for this receipt only, not a live delivery status. It does not apply an event.</p>
        <dl className="ontology-connections-summary">
          <div><dt>Acceptance receipt</dt><dd><code>{evidence.receiptId}</code></dd></div>
          <div><dt>Inbox ordinal</dt><dd><code>{evidence.ordinal}</code></dd></div>
          <div><dt>Accepted at (UTC)</dt><dd><Time value={evidence.acceptedAt} /></dd></div>
          <div><dt>Observed at (UTC)</dt><dd><Time value={evidence.observedAt} /></dd></div>
          {evidence.status === 'applied_recorded' && evidence.applyReceiptId && evidence.appliedAt && <>
            <div><dt>Application receipt</dt><dd><code>{evidence.applyReceiptId}</code></dd></div>
            <div><dt>Applied at (UTC)</dt><dd><Time value={evidence.appliedAt} /></dd></div>
          </>}
        </dl>
      </>}
    </Modal>
  </>;
}

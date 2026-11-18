import { useEffect, useRef, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { checkSnapshotDeliveryEvidence, fetchSnapshotDeliveryCapability } from '../../services/api';
import type { SnapshotDeliveryEvidence } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt } from './transferReceiptFocus';

function current(identity: string, signal?: AbortSignal) {
  const state = getSessionState();
  return !signal?.aborted && state.authenticated && !state.locked && state.identity === identity;
}

export default function TableExportSnapshotDelivery({ identity }: { identity: string }) {
  const [revision, setRevision] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const read = new AbortController();
    void fetchSnapshotDeliveryCapability(read.signal).then(value => {
      if (current(identity, read.signal)) setRevision(value.available && typeof value.configRevision === 'string' &&
        canonicalTransferReceipt(value.configRevision) ? value.configRevision : undefined);
    }).catch(() => { if (current(identity, read.signal)) setRevision(undefined); });
    return () => read.abort();
  }, [identity, refresh]);
  return revision ? <DeliveryForm key={revision} identity={identity} revision={revision}
    onRefresh={() => { setRevision(undefined); setRefresh(value => value + 1); }} /> : null;
}

const statuses = {
  not_admitted: 'Not admitted as of this check. This is not a prediction about a later check.',
  unconfirmed: 'Delivery is unconfirmed. This does not mean it failed or is in progress.',
  confirmed_recorded_receipt: 'Recorded delivery receipt verified. This is historical evidence, not proof of current receiver contents.',
} as const;

function DeliveryForm({ identity, revision, onRefresh }: { identity: string; revision: string; onRefresh: () => void }) {
  const [intent, setIntent] = useState('');
  const [request, setRequest] = useState('');
  const [result, setResult] = useState<SnapshotDeliveryEvidence>();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const clear = () => {
    pending.current?.abort(); pending.current = undefined;
    setResult(undefined); setFailed(false); setBusy(false);
  };
  const intentId = intent.trim().toLowerCase();
  const requestId = request.trim().toLowerCase();
  const valid = canonicalTransferReceipt(intentId) && canonicalTransferReceipt(requestId);
  const check = async () => {
    if (!valid || pending.current || !current(identity)) return;
    clear();
    const read = new AbortController(); pending.current = read;
    setBusy(true);
    try {
      const value = await checkSnapshotDeliveryEvidence({ intentId, requestId, expectedConfigRevision: revision }, read.signal);
      if (!current(identity, read.signal)) return;
      if (!Object.prototype.hasOwnProperty.call(statuses, value.state) ||
        (value.state === 'confirmed_recorded_receipt' && (typeof value.generation !== 'number' ||
          !Number.isSafeInteger(value.generation) || value.generation < 1 || typeof value.rowCount !== 'number' ||
          !Number.isSafeInteger(value.rowCount) || value.rowCount < 0 || !value.recordedAt || !Number.isFinite(Date.parse(value.recordedAt))))) {
        setFailed(true); return;
      }
      setResult(value);
    } catch {
      if (current(identity, read.signal)) setFailed(true);
    } finally {
      if (pending.current === read) {
        pending.current = undefined;
        if (current(identity, read.signal)) setBusy(false);
      }
    }
  };

  return <section className="ontology-source-sync__body ontology-table-export__mapping" aria-label="Snapshot delivery record">
    <h4>Snapshot delivery record</h4>
    <p>Check one known delivery intent and original request. This read-only check does not contact a receiver, send data, or reconcile a delivery.</p>
    <form className="ontology-source-sync__form" onSubmit={event => { event.preventDefault(); void check(); }}>
      <label>Delivery intent ID<Input aria-label="Delivery intent ID" autoComplete="off" maxLength={80} value={intent}
        onChange={event => { clear(); setIntent(event.target.value); }} /></label>
      <label>Original request ID<Input aria-label="Original request ID" autoComplete="off" maxLength={80} value={request}
        onChange={event => { clear(); setRequest(event.target.value); }} /></label>
      <div className="ontology-source-sync__actions">
        <Button htmlType="submit" size="small" loading={busy} disabled={!valid || busy}>Check delivery record</Button>
        {failed && <Button size="small" onClick={() => { clear(); onRefresh(); }}>Recheck availability</Button>}
      </div>
    </form>
    {failed && <p role="status">The delivery record could not be verified.</p>}
    {result && <p role="status">{statuses[result.state]}</p>}
    {result?.state === 'confirmed_recorded_receipt' && result.recordedAt && <dl className="ontology-connections-summary">
      <div><dt>Recorded generation</dt><dd>{result.generation}</dd></div>
      <div><dt>Rows on recorded receipt</dt><dd>{result.rowCount?.toLocaleString()}</dd></div>
      <div><dt>Receipt recorded at (UTC)</dt><dd><time dateTime={result.recordedAt}>
        {new Date(result.recordedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
      </time></dd></div>
    </dl>}
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { executeFrozenDelivery, fetchFrozenDeliveryCapability, reviewFrozenDelivery } from '../../services/api';
import type { FrozenDeliveryIds, FrozenDeliveryReceipt, FrozenDeliveryReview } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt as uuid } from './transferReceiptFocus';

function live(identity: string, signal?: AbortSignal) {
  const session = getSessionState();
  return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
}

export default function TableExportFrozenDelivery({ identity }: { identity: string }) {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    const read = new AbortController();
    void fetchFrozenDeliveryCapability(read.signal).then(value => {
      if (live(identity, read.signal)) setAvailable(value.available === true);
    }).catch(() => { if (live(identity, read.signal)) setAvailable(false); });
    return () => read.abort();
  }, [identity]);
  const subject = getSessionState().subject;
  return available && subject && live(identity) ? <DeliveryForm identity={identity} subject={subject} /> : null;
}

const normalize = (value: string) => value.trim().toLowerCase();
const rowCount = (value: number) => Number.isSafeInteger(value) && value >= 1 && value <= 100;
const keyFor = (subject: string, ids: FrozenDeliveryIds) =>
  `ontology-frozen-delivery-once:${JSON.stringify([subject, ids.intentId, ids.requestId])}`;
function attemptStatus(key: string): 'none' | 'attempted' | 'unknown' {
  try { return sessionStorage.getItem(key) == null ? 'none' : 'attempted'; }
  catch { return 'unknown'; }
}

function DeliveryForm({ identity, subject }: { identity: string; subject: string }) {
  const [inputIntent, setIntent] = useState('');
  const [inputRequest, setRequest] = useState('');
  const [review, setReview] = useState<FrozenDeliveryReview>();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<'review' | 'send'>();
  const [notice, setNotice] = useState('');
  const [receipt, setReceipt] = useState<FrozenDeliveryReceipt>();
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);

  const ids = { intentId: normalize(inputIntent), requestId: normalize(inputRequest) };
  const validIds = uuid(ids.intentId) && uuid(ids.requestId);
  const key = keyFor(subject, ids);
  const attempt = validIds ? attemptStatus(key) : 'none';
  const clear = () => {
    pending.current?.abort(); pending.current = undefined;
    setReview(undefined); setConfirm(false); setBusy(undefined); setNotice(''); setReceipt(undefined);
  };

  const inspect = async () => {
    if (!validIds || !live(identity) || pending.current || attemptStatus(key) !== 'none') return;
    clear();
    const read = new AbortController(); pending.current = read; setBusy('review');
    try {
      const value = await reviewFrozenDelivery(ids, read.signal);
      if (!live(identity, read.signal)) return;
      if (!rowCount(value.rowCount) || !Number.isSafeInteger(value.expectedPreviousGeneration) ||
          value.expectedPreviousGeneration < 0 || value.expectedPreviousGeneration >= 16 ||
          typeof value.webRevision !== 'string' || !uuid(value.webRevision)) throw new Error('Unverified delivery review');
      setReview(value);
    } catch {
      if (live(identity, read.signal)) setNotice('Frozen delivery could not be reviewed for these IDs. No delivery attempt was sent.');
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(identity, read.signal)) setBusy(undefined); }
    }
  };

  const send = async () => {
    if (!confirm || !validIds || !review || pending.current || !live(identity) || getSessionState().subject !== subject) return;
    if (attemptStatus(key) !== 'none') { setConfirm(false); setReview(undefined); return; }
    const exact = { ...ids, expectedWebRevision: review.webRevision };
    const expected = review;
    try {
      // Keep the one-shot fence even when the response is lost or the session changes.
      sessionStorage.setItem(key, JSON.stringify(exact));
      if (attemptStatus(key) !== 'attempted') throw new Error('Cannot retain one-shot attempt');
    } catch {
      setNotice('The one-shot attempt could not be retained in this tab. No delivery attempt was sent.');
      setConfirm(false); return;
    }
    const read = new AbortController(); pending.current = read;
    setConfirm(false); setReview(undefined); setBusy('send'); setNotice(''); setReceipt(undefined);
    try {
      const value = await executeFrozenDelivery(exact, read.signal);
      if (!live(identity, read.signal)) return;
      if (value.intentId !== exact.intentId || value.requestId !== exact.requestId ||
          !rowCount(value.rowCount) || value.rowCount !== expected.rowCount ||
          value.generation !== expected.expectedPreviousGeneration + 1 ||
          typeof value.recordedAt !== 'string' || !Number.isFinite(Date.parse(value.recordedAt))) throw new Error('Unverified delivery receipt');
      setReceipt(value);
    } catch {
      if (live(identity, read.signal)) setNotice('Delivery outcome was not confirmed. Do not resend. Check the recorded evidence separately.');
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(identity, read.signal)) setBusy(undefined); }
    }
  };

  return <section className="ontology-source-sync__body ontology-table-export__mapping" aria-label="Deliver frozen snapshot once">
    <h4>Deliver frozen snapshot once</h4>
    <p>Send a previously prepared synthetic snapshot to the fixed isolated loopback recipient. Reviewing the IDs reads preparation metadata only and does not contact the recipient.</p>
    <form className="ontology-source-sync__form" onSubmit={event => { event.preventDefault(); void inspect(); }}>
      <label>Frozen snapshot intent ID<Input aria-label="Frozen snapshot intent ID" autoComplete="off" maxLength={80} value={inputIntent}
        disabled={busy === 'send'} onChange={event => { clear(); setIntent(event.target.value); }} /></label>
      <label>Prepared original request ID<Input aria-label="Prepared original request ID" autoComplete="off" maxLength={80} value={inputRequest}
        disabled={busy === 'send'} onChange={event => { clear(); setRequest(event.target.value); }} /></label>
      <div className="ontology-source-sync__actions">
        <Button htmlType="submit" size="small" disabled={!validIds || Boolean(busy || confirm) || attempt !== 'none'}
          loading={busy === 'review'}>Review frozen delivery</Button>
      </div>
    </form>
    {review && attempt === 'none' && <>
      <p>Prepared rows: {review.rowCount} · Reviewed prior generation: {review.expectedPreviousGeneration}.</p>
      <Button size="small" onClick={() => setConfirm(true)}>Review one-shot delivery</Button>
    </>}
    {busy === 'send' && <p role="status">Waiting for verified delivery evidence…</p>}
    {notice && <p role="status">{notice}</p>}
    {attempt === 'unknown' && <p role="status">This tab cannot retain a one-shot attempt. No delivery will be sent.</p>}
    {attempt === 'attempted' && !busy && !receipt && <p role="status">This tab recorded a delivery attempt for these exact IDs. It cannot be sent again here.</p>}
    {(attempt === 'attempted' || receipt) && <p>Use the same intent ID and original request ID in <strong>Snapshot delivery record</strong>, if you have access, to check historical evidence without sending anything.</p>}
    {receipt && <dl className="ontology-connections-summary" aria-label="Verified frozen delivery receipt">
      <div><dt>Recorded generation</dt><dd>{receipt.generation}</dd></div>
      <div><dt>Rows on recorded receipt</dt><dd>{receipt.rowCount}</dd></div>
      <div><dt>Recorded at (UTC)</dt><dd><time dateTime={receipt.recordedAt}>
        {new Date(receipt.recordedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
      </time></dd></div>
    </dl>}
    <Modal open={confirm} destroyOnHidden className="flow-surface-modal" title="Send this frozen snapshot once?"
      okText="Confirm one delivery attempt" onCancel={() => setConfirm(false)} onOk={() => void send()}>
      {review && <>
        <p>Make one delivery attempt to the fixed isolated loopback recipient. If it succeeds, generation {review.expectedPreviousGeneration + 1} becomes active on the synthetic receiver; earlier generations are retained and no existing data is deleted. There is no automatic retry.</p>
        <p>Frozen intent <code>{ids.intentId}</code> · Original request <code>{ids.requestId}</code>.</p>
        <p>Prepared rows: {review.rowCount} · Reviewed prior generation: {review.expectedPreviousGeneration}.</p>
      </>}
    </Modal>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import { applySandboxCdc, fetchSandboxCdc, fetchSandboxCdcReceipts, reviewSandboxCdc } from '../../services/api';
import type { SandboxCdcReceipt, SandboxCdcRunRequest, SandboxCdcStatus } from '../../services/api';
import { getSessionState } from '../../services/session';
import SandboxCdcVersions from './SandboxCdcVersions';
import { SandboxMarkings, sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';

const states: Record<SandboxCdcStatus['state'], string> = {
  unreviewed: 'Not reviewed', armed: 'Reviewed · manual replay only', blocked: 'Blocked',
};
type Decision = 'review' | 'apply' | 'retry';
const literalCursor = (value: string | null): value is string => value !== null && /^\d{1,19}$/.test(value);
function readPending(key: string): SandboxCdcRunRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as SandboxCdcRunRequest | null;
    if (value && typeof value.requestId === 'string' && Number.isSafeInteger(value.expectedGeneration) &&
      typeof value.expectedAppliedThrough === 'string' && literalCursor(value.expectedAppliedThrough)) return value;
  } catch { /* Stored requests are optional until a replay is submitted. */ }
}
function clearPending(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* Keep the current result usable if storage was lost. */ }
}

export default function SandboxCdcReplay() {
  const [open, setOpen] = useState(false);
  const section = useRef<HTMLElement>(null);
  const identity = useSandboxIdentity();
  useEffect(() => { if (open) section.current?.scrollIntoView({ block: 'start' }); }, [open]);
  return <section ref={section} className={`ontology-source-catalog ontology-source-sync ontology-transfer-tool${open ? ' is-open' : ''}`} aria-label="Sandbox CDC replay">
    <header>
      <div className="ontology-transfer-tool__description">
        <span className="ontology-transfer-tool__kind">Change data capture · CDC</span>
        <h3>Replay test changes</h3>
        <p>Apply a batch of sample inserts and updates to a test table, then verify what was applied.</p>
        <ol className="ontology-transfer-tool__steps" aria-label="Replay workflow">
          <li><span>1</span>Review source &amp; target</li><li><span>2</span>Apply next batch</li><li><span>3</span>Check receipt</li>
        </ol>
      </div>
      <Button size="small" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? 'Hide change replay' : 'Review test replay'}</Button>
    </header>
    {open && identity && <CdcReplayWorkspace key={identity} />}
  </section>;
}

function CdcReplayWorkspace() {
  const identity = useRef(getSessionState().identity).current;
  const storageKey = `ontology-cdc-sandbox:${identity}`;
  const [pendingRequest, setPendingRequest] = useState(() => readPending(storageKey));
  const [status, setStatus] = useState<SandboxCdcStatus>();
  const [receipts, setReceipts] = useState<SandboxCdcReceipt[]>();
  const [loadFailed, setLoadFailed] = useState(false);
  const [receiptsFailed, setReceiptsFailed] = useState(false);
  const [notice, setNotice] = useState<{ message: string; error?: boolean }>();
  const [revision, setRevision] = useState(0);
  const [decision, setDecision] = useState<Decision>();
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(true);
  const [scopeDenied, setScopeDenied] = useState(false);
  const read = useRef<AbortController>();
  const scopeEpoch = useRef(0);
  const active = useRef(false);
  const working = useRef(false);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const invalidate = () => {
    scopeEpoch.current++;
    read.current?.abort();
    setStatus(undefined); setReceipts(undefined); setNotice(undefined); setDecision(undefined);
    setLoadFailed(true); setReceiptsFailed(true); setRefreshing(false); setScopeDenied(true);
  };
  const refresh = () => { setScopeDenied(false); setRefreshing(true); setRevision(value => value + 1); };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    read.current = controller;
    setRefreshing(true); setLoadFailed(false); setReceiptsFailed(false);
    const failed = (failure: unknown, receiptsOnly: boolean) => {
      if (!live(controller.signal)) return;
      if (sandboxScopeDenied(failure)) { invalidate(); return; }
      if (receiptsOnly) { setReceipts(undefined); setReceiptsFailed(true); }
      else { setStatus(undefined); setLoadFailed(true); }
    };
    const statusRead = fetchSandboxCdc(controller.signal).then(value => { if (live(controller.signal)) setStatus(value); })
      .catch(failure => failed(failure, false));
    const receiptsRead = fetchSandboxCdcReceipts(controller.signal).then(value => {
      if (!live(controller.signal)) return;
      setReceipts(value);
      const saved = readPending(storageKey);
      const original = saved && value.find(receipt => receipt.requestId === saved.requestId);
      if (original) {
        clearPending(storageKey); setPendingRequest(undefined);
        setNotice({ message: `Original receipt confirmed: ${original.id}.` });
      }
    }).catch(failure => failed(failure, true));
    void Promise.all([statusRead, receiptsRead]).then(() => { if (live(controller.signal)) setRefreshing(false); });
    return () => controller.abort();
  }, [revision, identity]);

  const loaded = Boolean(status?.available);
  const reviewable = loaded && status?.state === 'unreviewed' && status.generation === null;
  const armed = loaded && status?.state === 'armed' && status.generation !== null;
  const hasChanges = status && literalCursor(status.sourceHighWater) && literalCursor(status.appliedThrough)
    && BigInt(status.sourceHighWater) > BigInt(status.appliedThrough);
  const canRetry = armed && pendingRequest && pendingRequest.expectedGeneration === status?.generation;
  const canApply = armed && !pendingRequest && hasChanges;

  const submit = async () => {
    if (!status || !decision || refreshing || loadFailed || working.current || !live()) return;
    if ((decision === 'review' && !reviewable) || (decision === 'apply' && !canApply) || (decision === 'retry' && !canRetry)) return;
    const epoch = scopeEpoch.current;
    const current = () => live() && epoch === scopeEpoch.current;
    let request = pendingRequest;
    if (decision === 'apply' && status.generation !== null) {
      try {
        request = { requestId: crypto.randomUUID(), expectedGeneration: status.generation, expectedAppliedThrough: status.appliedThrough };
        sessionStorage.setItem(storageKey, JSON.stringify(request)); setPendingRequest(request);
      } catch {
        setNotice({ message: 'The original request could not be kept in this session. No replay started.', error: true });
        setDecision(undefined); return;
      }
    }
    working.current = true; setBusy(true); setNotice(undefined);
    try {
      if (decision === 'review') {
        await reviewSandboxCdc();
        if (current()) setNotice({ message: 'The fixed route was reviewed. No changes were applied.' });
      } else if (request) {
        const receipt = await applySandboxCdc(request);
        clearPending(storageKey);
        if (current()) {
          setPendingRequest(undefined);
          setNotice({ message: `${decision === 'retry' ? 'Original' : 'Committed'} receipt confirmed: ${receipt.id}.` });
        }
      }
    } catch (failure) {
      if (current()) {
        if (sandboxScopeDenied(failure)) { invalidate(); return; }
        const code = (failure as { response?: { status?: number } })?.response?.status;
        if (decision !== 'review' && (code === 409 || code === 422)) {
          clearPending(storageKey); setPendingRequest(undefined);
          setNotice({ message: 'Replay was rejected. Review the current checkpoint and bounded source range before retrying.', error: true });
        } else {
          setNotice({ message: decision === 'review' ? 'Review could not be confirmed. Refresh the fixed route before retrying.'
            : 'The outcome could not be confirmed. Keep the original request and retry it rather than starting a new replay.', error: true });
        }
      }
    } finally {
      working.current = false;
      if (live()) { setBusy(false); setDecision(undefined); if (epoch === scopeEpoch.current) refresh(); }
    }
  };

  return <div className="ontology-source-sync__body ontology-cdc-replay__body" aria-busy={refreshing}>
    <div className="ontology-source-sync__actions"><Button size="small" disabled={busy || refreshing} onClick={refresh}>Refresh replay status</Button></div>
    {notice && <p role={notice.error ? 'alert' : 'status'} className={notice.error ? 'is-error' : undefined}>{notice.message}</p>}
    {loadFailed ? <p role="alert">Sandbox replay status could not be loaded.</p> : !status ? <p role="status">Loading sandbox replay status…</p> : <>
      <strong role="status">{states[status.state]}</strong>
      {!loaded && <p>This test is not ready. Ask an administrator to enable it and configure its sample source and target.</p>}
      <div className="ontology-source-sync__route"><code>{status.source}</code><ArrowRightOutlined /><code>{status.target}</code></div>
      <dl className="ontology-cdc-replay__facts">
        <div><dt>Reviewed generation</dt><dd>{status.generation === null ? 'Not reviewed' : status.generation}</dd></div>
        <div><dt>Latest source event</dt><dd>{status.sourceHighWater !== null ? <code>{status.sourceHighWater}</code> : 'Not verified'}</dd></div>
        <div><dt>Last applied event</dt><dd>{status.generation !== null ? <code>{status.appliedThrough}</code> : 'Not reviewed'}</dd></div>
      </dl>
      <p>Compare the latest source event with the last applied event to see what is pending. Check the receipt below to confirm a batch was applied. Each batch can contain up to {status.maxRows} events ({status.maxBytes.toLocaleString()} bytes).</p>
      {pendingRequest && (canRetry ? <p>Original request retained: <code>{pendingRequest.requestId}</code>.</p>
        : <p>Replay currently unavailable for the retained request.</p>)}
      <div className="ontology-source-sync__actions">
        {reviewable && <Button size="small" disabled={busy || refreshing} onClick={() => setDecision('review')}>Review source and target</Button>}
        {armed && !pendingRequest && <Button size="small" type="primary" disabled={busy || refreshing || !canApply} onClick={() => setDecision('apply')}>Apply next change batch</Button>}
        {canRetry && <Button size="small" type="primary" disabled={busy || refreshing} onClick={() => setDecision('retry')}>Retry original request</Button>}
      </div>
    </>}
    <section aria-label="Committed CDC receipts">
      <h4>Committed CDC receipts</h4>
      {receiptsFailed ? <p role="alert">Committed receipts could not be loaded.</p> : !receipts ? <p role="status">Loading CDC receipts…</p>
        : receipts.length ? <ol className="ontology-source-sync__items">{receipts.map(receipt => <li key={receipt.id}>
          <strong>Committed target receipt</strong>
          <span>Range: after <code>{receipt.fromExclusive}</code> through <code>{receipt.appliedThrough}</code></span>
          <span>Rows affected: {receipt.rowsAffected.toLocaleString()}{receipt.rowsAffected === 0 ? ' · No observed movement' : ''}</span>
          <span>Recorded at: <time dateTime={receipt.recordedAt}>{new Date(receipt.recordedAt).toLocaleString()}</time></span>
          <SandboxMarkings markings={receipt.markings} />
          <span>Receipt ID: <code>{receipt.id}</code></span><span>Original request ID: <code>{receipt.requestId}</code></span>
        </li>)}</ol> : <p>No committed CDC receipts.</p>}
    </section>
    {!scopeDenied && <SandboxCdcVersions revision={revision} onScopeDenied={invalidate} />}
    <Modal open={Boolean(decision)} destroyOnHidden className="flow-surface-modal"
      title={decision === 'review' ? 'Review sandbox CDC route?' : decision === 'retry' ? 'Retry original CDC request?' : 'Apply next sandbox CDC batch?'}
      onCancel={() => { if (!busy) setDecision(undefined); }} onOk={submit} confirmLoading={busy}
      okText={decision === 'review' ? 'Confirm review' : decision === 'retry' ? 'Confirm retry' : 'Confirm apply'}>
      {decision === 'review' ? <p>Review the fixed synthetic stream <code>{status?.source}</code> and destination <code>{status?.target}</code>. This does not apply any changes.</p>
        : decision === 'retry' ? <p>Reuse request <code>{pendingRequest?.requestId}</code>. If already committed, return the original receipt. Otherwise only its reviewed generation and checkpoint can be applied.</p>
          : <p>Apply only the next bounded events after checkpoint <code>{status?.appliedThrough}</code> for reviewed generation {status?.generation}. Insert or update synthetic rows in <code>{status?.target}</code>; no deletes.</p>}
    </Modal>
  </div>;
}

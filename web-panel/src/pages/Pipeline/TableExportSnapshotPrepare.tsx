import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { checkSnapshotIntentHead, fetchSnapshotIntentCapability, prepareSnapshotIntent } from '../../services/api';
import type { SnapshotIntentRequest, SnapshotIntentResult } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt as uuid } from './transferReceiptFocus';

const validGeneration = (value: number) => Number.isSafeInteger(value) && value >= 0 && value <= 16;
const live = (identity: string, signal?: AbortSignal) => {
  const session = getSessionState();
  return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
};

function load(key: string): SnapshotIntentRequest | undefined {
  const raw = sessionStorage.getItem(key);
  if (raw === null) return;
  const request = JSON.parse(raw) as SnapshotIntentRequest;
  if (!request || typeof request.requestId !== 'string' || !uuid(request.requestId) ||
    typeof request.expectedConfigRevision !== 'string' || !uuid(request.expectedConfigRevision) ||
    !validGeneration(request.expectedPreviousGeneration) || request.expectedPreviousGeneration === 16) throw new Error('Invalid retained request');
  return request;
}

export default function TableExportSnapshotPrepare({ identity }: { identity: string }) {
  const [generation, setGeneration] = useState(0);
  const [revision, setRevision] = useState<string>();
  useEffect(() => {
    const read = new AbortController();
    void fetchSnapshotIntentCapability(read.signal).then(value => {
      if (live(identity, read.signal)) setRevision(value.available && typeof value.configRevision === 'string' &&
        uuid(value.configRevision) ? value.configRevision : undefined);
    }).catch(() => { if (live(identity, read.signal)) setRevision(undefined); });
    return () => read.abort();
  }, [identity, generation]);
  const session = getSessionState();
  return revision && live(identity) && session.subject ? <Prepare key={revision} identity={identity} subject={session.subject} revision={revision} refresh={() => {
    setRevision(undefined); setGeneration(value => value + 1);
  }} /> : null;
}

function Prepare({ identity, subject, revision, refresh }: { identity: string; subject: string; revision: string; refresh: () => void }) {
  const key = `ontology-snapshot-intent:${subject}`;
  const [retained, setRetained] = useState<SnapshotIntentRequest>();
  const [storageError, setStorageError] = useState(false);
  const [head, setHead] = useState<number>();
  const [decision, setDecision] = useState<'new' | 'original'>();
  const [busy, setBusy] = useState<'head' | 'prepare'>();
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState<SnapshotIntentResult & { requestId: string }>();
  const pending = useRef<AbortController>();
  useEffect(() => {
    try { setRetained(load(key)); } catch { setStorageError(true); }
    return () => pending.current?.abort();
  }, [key]);

  const checkHead = async () => {
    if (pending.current || retained || storageError || !live(identity)) return;
    const read = new AbortController(); pending.current = read;
    setBusy('head'); setHead(undefined); setResult(undefined); setNotice('');
    try {
      const value = await checkSnapshotIntentHead(revision, read.signal);
      if (live(identity, read.signal)) {
        if (validGeneration(value.generation)) setHead(value.generation);
        else setNotice('The publication head could not be verified.');
      }
    } catch {
      if (live(identity, read.signal)) setNotice('The publication head could not be verified. Recheck availability before trying again.');
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(identity, read.signal)) setBusy(undefined); }
    }
  };

  const prepare = async () => {
    if (!decision || pending.current || storageError || !live(identity)) return;
    let request: SnapshotIntentRequest;
    try {
      const original = load(key);
      if (decision === 'original' && original && original.expectedConfigRevision === revision &&
        original.requestId === retained?.requestId && original.expectedPreviousGeneration === retained.expectedPreviousGeneration) {
        request = original;
      } else if (decision === 'new' && !original && head !== undefined && head < 16) {
        request = { requestId: crypto.randomUUID(), expectedConfigRevision: revision, expectedPreviousGeneration: head };
        sessionStorage.setItem(key, JSON.stringify(request));
        setRetained(request);
      } else {
        setNotice('The reviewed request changed. No preparation was sent.');
        setRetained(original); setDecision(undefined); setHead(undefined); return;
      }
    } catch {
      setStorageError(true); setDecision(undefined); return;
    }
    const read = new AbortController(); pending.current = read;
    setBusy('prepare'); setDecision(undefined); setNotice(''); setResult(undefined); setHead(undefined);
    try {
      const value = await prepareSnapshotIntent(request, read.signal);
      if (!live(identity, read.signal)) return;
      if (typeof value.id !== 'string' || !uuid(value.id) || value.expectedPreviousGeneration !== request.expectedPreviousGeneration ||
        !Number.isSafeInteger(value.rowCount) || value.rowCount < 1 || value.rowCount > 100 ||
        !value.createdAt || !Number.isFinite(Date.parse(value.createdAt))) throw new Error('Unverified preparation');
      try { sessionStorage.removeItem(key); setRetained(undefined); } catch { setStorageError(true); }
      setResult({ ...value, requestId: request.requestId });
    } catch (failure) {
      if (live(identity, read.signal)) {
        const response = (failure as { response?: { status?: number; data?: { detail?: { code?: string }; errors?: { code?: string } } } })?.response;
        const code = response?.data?.errors?.code ?? response?.data?.detail?.code;
        if (response?.status === 409 && code === 'predecessor_changed') {
          try { sessionStorage.removeItem(key); setRetained(undefined); } catch { setStorageError(true); }
          setNotice('The publication generation changed and this preparation was not saved. Check the publication head again.');
        } else setNotice('Preparation was not confirmed. Only the exact original request may be retried.');
      }
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(identity, read.signal)) setBusy(undefined); }
    }
  };

  return <section className="ontology-source-sync__body ontology-table-export__mapping" aria-label="Prepare frozen snapshot">
    <h4>Prepare frozen snapshot</h4>
    <p>Store a frozen synthetic snapshot for the fixed pilot. Preparation does not send anything. The publication record is capped at 16 retained generations.</p>
    <div className="ontology-source-sync__actions">
      <Button size="small" disabled={Boolean(busy || retained || storageError)} loading={busy === 'head'} onClick={() => void checkHead()}>Check publication head</Button>
      <Button size="small" disabled={Boolean(busy || decision)} onClick={refresh}>Recheck preparation availability</Button>
      {!retained && head !== undefined && head < 16 && <Button size="small" disabled={Boolean(busy || decision || storageError)}
        onClick={() => setDecision('new')}>Review frozen snapshot preparation</Button>}
      {retained?.expectedConfigRevision === revision && <Button size="small" disabled={Boolean(busy || decision || storageError)}
        onClick={() => setDecision('original')}>Review original preparation retry</Button>}
    </div>
    {storageError && <p role="alert">The original request could not be read or retained in this session. No new preparation can be sent.</p>}
    {notice && <p role="status">{notice}</p>}
    {head !== undefined && <p role="status">Observed publication generation: <strong>{head}</strong>.{head === 16 ? ' The publication limit is reached. Preparation is blocked.' : ''}</p>}
    {retained && busy !== 'prepare' && <p>Original request ID: <code>{retained.requestId}</code> · Reviewed publication generation: {retained.expectedPreviousGeneration}.
      {retained.expectedConfigRevision !== revision && ' The preparation profile changed. The original cannot be retried under a different profile.'}</p>}
    {result && <dl className="ontology-connections-summary" aria-label="Prepared frozen snapshot">
      <div><dt>Prepared intent ID</dt><dd><code>{result.id}</code></dd></div>
      <div><dt>Original request ID</dt><dd><code>{result.requestId}</code></dd></div>
      <div><dt>Frozen rows</dt><dd>{result.rowCount}</dd></div>
      <div><dt>Prepared at (UTC)</dt><dd><time dateTime={result.createdAt}>{new Date(result.createdAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time></dd></div>
    </dl>}
    <Modal open={Boolean(decision)} destroyOnHidden className="flow-surface-modal" onCancel={() => setDecision(undefined)}
      title={decision === 'original' ? 'Retry the original snapshot preparation?' : 'Prepare this frozen snapshot?'}
      okText={decision === 'original' ? 'Confirm original preparation retry' : 'Confirm snapshot preparation'} onOk={() => void prepare()}>
      <p>Only save a frozen synthetic snapshot. Nothing will be sent. 仅保存合成冻结快照，不发送。</p>
      <p>Reviewed publication generation: {decision === 'original' ? retained?.expectedPreviousGeneration : head}.</p>
      {decision === 'original' && <p>Reuse exact original request <code>{retained?.requestId}</code>.</p>}
    </Modal>
  </section>;
}

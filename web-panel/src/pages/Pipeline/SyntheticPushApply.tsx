import { useEffect, useId, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchPushApplyStatus, reviewPushApplyTarget, runPushApply } from '../../services/api';
import type { PushApplyRequest, PushApplyResult, PushApplyStatus } from '../../services/api';
import { getSessionState } from '../../services/session';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const POSITION = /^(0|[1-9][0-9]{0,18})$/;
const position = (value?: string | null): value is string => value != null && POSITION.test(value);
type Preview = Omit<PushApplyRequest, 'requestId'> & { maxEvents: number; through: string | null };
type Decision = { kind: 'review' } | { kind: 'apply' | 'retry'; preview: Preview };

function readPending(key: string): PushApplyRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as PushApplyRequest | null;
    if (value && value.expectedGeneration === 1 && UUID.test(value.requestId) &&
      position(value.expectedAppliedThrough) && position(value.expectedAcceptedThrough)) return value;
  } catch { /* Storage must be available before a new apply is submitted. */ }
}

function clearPending(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* The exact retained request is safe to replay. */ }
}

function RecordedAt({ value }: { value?: string | null }) {
  return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : <>Not recorded</>;
}

function previewRange(generation: 1, applied: string, accepted: string, maxEvents: number): Preview {
  const capped = BigInt(applied) + BigInt(maxEvents);
  const through = BigInt(accepted) > BigInt(applied) ? (BigInt(accepted) < capped ? accepted : String(capped)) : null;
  return { expectedGeneration: generation, expectedAppliedThrough: applied, expectedAcceptedThrough: accepted, maxEvents, through };
}

export default function SyntheticPushApply({ identity, version }: { identity: string; version: number }) {
  const heading = useId();
  const storageKey = `ontology-push-apply:${identity}`;
  const [status, setStatus] = useState<PushApplyStatus>();
  const [notice, setNotice] = useState<{ text: string; error?: boolean }>();
  const [pending, setPending] = useState(() => readPending(storageKey));
  const [decision, setDecision] = useState<Decision>();
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef(false);
  const working = useRef(false);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const request = new AbortController();
    setRefreshing(true);
    void fetchPushApplyStatus(request.signal).then(value => {
      if (live(request.signal)) setStatus(value);
    }).catch(() => {
      if (live(request.signal)) setStatus(undefined);
    }).finally(() => {
      if (live(request.signal)) setRefreshing(false);
    });
    return () => request.abort();
  }, [identity, version, revision]);

  const ready = status?.available && status.state === 'ready' && status.generation === 1;
  const reviewable = status?.available && status.state === 'unreviewed' && status.generation == null;
  const canReview = reviewable && !pending && !refreshing;
  const nextPreview = ready && status && position(status.acceptedThrough) && position(status.appliedThrough) &&
    BigInt(status.acceptedThrough) >= BigInt(status.appliedThrough) && Number.isSafeInteger(status.maxEvents) && status.maxEvents > 0
    ? previewRange(1, status.appliedThrough, status.acceptedThrough, status.maxEvents) : undefined;
  const retryPreview = pending && status && Number.isSafeInteger(status.maxEvents) && status.maxEvents > 0
    ? previewRange(1, pending.expectedAppliedThrough, pending.expectedAcceptedThrough, status.maxEvents) : undefined;
  const canRecover = Boolean(status?.available && status.canReplay && pending && retryPreview && !refreshing);

  const submit = async () => {
    if (!decision || working.current || !live()) return;
    if (decision.kind === 'review' && !canReview || decision.kind === 'apply' && (!ready || pending || refreshing)
      || decision.kind === 'retry' && !canRecover) return;
    let request = pending;
    if (decision.kind === 'apply') {
      const reviewed = decision.preview;
      try {
        request = { requestId: crypto.randomUUID(), expectedGeneration: reviewed.expectedGeneration,
          expectedAppliedThrough: reviewed.expectedAppliedThrough, expectedAcceptedThrough: reviewed.expectedAcceptedThrough };
        sessionStorage.setItem(storageKey, JSON.stringify(request));
        setPending(request);
      } catch {
        setNotice({ text: 'The original request could not be retained. No apply was sent.', error: true });
        setDecision(undefined); return;
      }
    }
    working.current = true; setBusy(true); setNotice(undefined);
    try {
      if (decision.kind === 'review') {
        const reviewed = await reviewPushApplyTarget();
        if (live()) {
          setStatus(reviewed);
          setNotice({ text: 'The fixed target was reviewed. No event was applied by the review.' });
        }
      } else if (request) {
        const result = await runPushApply(request);
        clearPending(storageKey);
        if (live()) {
          setPending(undefined);
          setNotice({ text: result.status === 'no_new_data' ? 'No new data. No events were applied and the checkpoint is unchanged.'
            : `${decision.kind === 'retry' ? 'Original apply receipt confirmed' : 'Apply receipt confirmed'}: ${result.id}. ${result.eventsApplied} events applied.` });
        }
      }
    } catch (failure) {
      const code = (failure as { response?: { status?: number } })?.response?.status;
      if (decision.kind !== 'review' && (code === 409 || code === 422 || code === 429)) {
        clearPending(storageKey);
        if (live()) {
          setPending(undefined);
          setNotice({ text: code === 429 ? 'The retained empty-check limit was reached. No events were applied.'
            : 'The reviewed request was rejected. Refresh the target checkpoint and accepted limit before reviewing another range.', error: true });
        }
      } else if (live()) {
        setNotice({ text: decision.kind === 'review' ? 'The review could not be confirmed. Refresh the saved target before retrying.'
          : 'The apply outcome could not be confirmed. Retry the retained original request, rather than starting a new apply.', error: true });
      }
    } finally {
      working.current = false;
      if (live()) { setBusy(false); setDecision(undefined); setRevision(value => value + 1); }
    }
  };

  if (!status || status.state === 'disabled' || status.state === 'unconfigured' || !status.available && status.state !== 'blocked') return null;
  const latest = status.recent.find(result => result.status === 'applied');
  const latestCheck = status.recent.find(result => result.status === 'no_new_data');
  const earlier = status.recent.filter(result => result.id !== latest?.id && result.id !== latestCheck?.id);
  const historical = status.state === 'blocked';
  return <section className="ontology-source-catalog ontology-source-sync" aria-labelledby={heading} aria-busy={refreshing || busy}>
    <header><div><h4 id={heading}>Manual Push apply</h4><p>Explicit review and a bounded one-time application to a fixed synthetic target. This is not CDC or a schedule.</p></div></header>
    <div className="ontology-source-sync__body">
      <p role="status"><strong>{historical ? 'Reviewed target unavailable or replaced' : ready ? 'Fixed target reviewed' : 'Fixed target not reviewed'}</strong></p>
      <p>Fixed target: <code>{status?.target}</code></p>
      {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
      <dl className="ontology-connections-summary">
        <div><dt>Accepted through</dt><dd>{status.acceptedThrough ?? 'Not verified'}</dd></div>
        <div><dt>Applied through</dt><dd>{ready ? status.appliedThrough ?? 'Not verified' : 'Not verified for a current target'}</dd></div>
        <div><dt>Total applied events</dt><dd>{ready ? status.appliedThrough ?? 'Not verified' : 'Not verified for a current target'}</dd></div>
        <div><dt>Reviewed at</dt><dd><RecordedAt value={status.reviewedAt} /></dd></div>
      </dl>
      {historical && <p>New application is blocked. Earlier apply receipts, if present, do not describe a current target.</p>}
      {latest && <><h4>{historical ? 'Latest historical apply receipt' : 'Latest applied receipt'}</h4><ApplyResult result={latest} /></>}
      {latestCheck && <><h4>{historical ? 'Latest historical empty check' : 'Latest empty check'}</h4><ApplyResult result={latestCheck} /></>}
      {earlier.length > 0 && <details><summary>Earlier apply results</summary><ol className="ontology-source-sync__items">
        {earlier.map(item => <li key={item.id}><ApplyResult result={item} /></li>)}
      </ol></details>}
      {pending && <p>Original apply request retained. No new apply can start until this outcome is resolved.
        {!refreshing && !canRecover && ' Recovery is currently unavailable for this request.'}</p>}
      <div className="ontology-source-sync__actions">
        {canReview && <Button size="small" disabled={busy} onClick={() => setDecision({ kind: 'review' })}>Review fixed target</Button>}
        {ready && !pending && <Button type="primary" size="small" disabled={busy || refreshing || !nextPreview}
          onClick={() => nextPreview && setDecision({ kind: 'apply', preview: nextPreview })}>Apply accepted events</Button>}
        {canRecover && <Button type="primary" size="small" disabled={busy}
          onClick={() => retryPreview && setDecision({ kind: 'retry', preview: retryPreview })}>Retry original request</Button>}
      </div>
    </div>
    <Modal open={Boolean(decision)} destroyOnHidden className="flow-surface-modal" confirmLoading={busy} closable={!busy} keyboard={!busy} maskClosable={false}
      onCancel={() => { if (!busy) setDecision(undefined); }} onOk={submit}
      title={decision?.kind === 'review' ? 'Review fixed Push target?' : decision?.kind === 'retry' ? 'Retry original Push apply?' : 'Apply accepted Push events?'}
      okText={decision?.kind === 'review' ? 'Confirm target review' : decision?.kind === 'retry' ? 'Retry same request' : 'Confirm apply'}>
      {decision?.kind === 'review' ? <p>Review the existing synthetic target <code>{status?.target}</code> for this inbox. This does not create or replace a target, and no accepted events are applied.</p>
        : decision && <>
          {decision.kind === 'retry' && <p>Use the original request ID, generation, checkpoint, and accepted limit. A confirmed portion is not applied twice.</p>}
          <p>Reviewed generation: {decision.preview.expectedGeneration} · Maximum events: {decision.preview.maxEvents}</p>
          <p>Previously applied through: <code>{decision.preview.expectedAppliedThrough}</code> · Reviewed accepted ceiling: <code>{decision.preview.expectedAcceptedThrough}</code></p>
          <p>{decision.preview.through === null ? 'No new data in this reviewed range; no events can be applied.' : <>Bounded range: after <code>{decision.preview.expectedAppliedThrough}</code> through <code>{decision.preview.through}</code>, inclusive at the end.</>}</p>
          <p>Events accepted after the reviewed ceiling are excluded.</p>
        </>}
    </Modal>
  </section>;
}

function ApplyResult({ result }: { result: PushApplyResult }) {
  if (!result.id) return null;
  if (result.status === 'no_new_data') return <>
    <p><strong>No new data</strong> · No events applied; the target checkpoint did not change.</p>
    <dl className="ontology-connections-summary">
      <div><dt>Check record</dt><dd><code>{result.id}</code></dd></div>
      <div><dt>Checked at</dt><dd><RecordedAt value={result.checkedAt} /></dd></div>
      <div><dt>Reviewed accepted ceiling</dt><dd><code>{result.reviewedAcceptedThrough}</code></dd></div>
    </dl>
  </>;
  return <dl className="ontology-connections-summary">
    <div><dt>Applied receipt</dt><dd><code>{result.id}</code></dd></div>
    <div><dt>Applied events</dt><dd>{result.eventsApplied.toLocaleString()}</dd></div>
    <div><dt>Applied at</dt><dd><RecordedAt value={result.appliedAt} /></dd></div>
    <div><dt>Exact applied range</dt><dd>After <code>{result.fromExclusive}</code> through <code>{result.throughInclusive}</code></dd></div>
    <div><dt>Reviewed accepted ceiling</dt><dd><code>{result.reviewedAcceptedThrough}</code></dd></div>
    {result.hasMoreReviewed && <div><dt>Remaining</dt><dd>More events within this reviewed ceiling were not applied by this action.</dd></div>}
  </dl>;
}

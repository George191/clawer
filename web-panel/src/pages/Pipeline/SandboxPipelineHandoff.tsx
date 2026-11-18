import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { checkSandboxHandoffEligibility, copySandboxHandoff } from '../../services/api';
import type { SandboxHandoffCopyReceipt, SandboxHandoffCopyRequest, SandboxHandoffEligibility, SandboxHandoffSink, SandboxPipelineOutput } from '../../services/api';
import { getSessionState } from '../../services/session';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';

const v4 = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
type Binding = Omit<SandboxHandoffCopyRequest, 'requestId'>;
function existing(key: string, binding: Binding): SandboxHandoffCopyRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as SandboxHandoffCopyRequest | null;
    if (value && typeof value.requestId === 'string' && v4.test(value.requestId) && value.outputId === binding.outputId &&
      value.expectedConfigHash === binding.expectedConfigHash && value.expectedResultHash === binding.expectedResultHash &&
      value.expectedSinkGeneration === binding.expectedSinkGeneration) return { ...binding, requestId: value.requestId };
  } catch { /* This is a tab-scoped recovery aid; no new request can start without it. */ }
}

export default function SandboxPipelineHandoff({ output, sink, copyAvailable, scopeKey, onClose, onScopeDenied }: {
  output: SandboxPipelineOutput; sink: SandboxHandoffSink; copyAvailable: boolean; scopeKey?: string; onClose: () => void; onScopeDenied: () => void;
}) {
  const [busy, setBusy] = useState<'check' | 'copy'>();
  const [result, setResult] = useState<SandboxHandoffEligibility>();
  const [failed, setFailed] = useState(false);
  const [copyReview, setCopyReview] = useState<'new' | 'retry'>();
  const [receipt, setReceipt] = useState<SandboxHandoffCopyReceipt>();
  const [copyNotice, setCopyNotice] = useState('');
  const [blocked, setBlocked] = useState(false);
  const request = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const key = `ontology-local-handoff:${JSON.stringify([identity, scopeKey, output.id, sink.id, sink.generation])}`;
  const binding = { outputId: output.id, expectedConfigHash: output.configHash, expectedResultHash: output.resultHash, expectedSinkGeneration: sink.generation };
  const [pending, setPending] = useState(() => existing(key, binding));
  const isCurrent = (read: AbortController) => {
    const session = getSessionState();
    return !read.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const sinkMatches = (other: SandboxHandoffSink) => other.id === sink.id && other.generation === sink.generation;
  const forget = () => {
    try { sessionStorage.removeItem(key); } catch { /* Original request stays idempotent. */ }
    setPending(undefined);
  };
  useEffect(() => () => request.current?.abort(), []);
  const qualify = async () => {
    if (request.current) return;
    const read = new AbortController();
    request.current = read; setBusy('check'); setResult(undefined); setFailed(false); setCopyReview(undefined);
    try {
      const value = await checkSandboxHandoffEligibility(binding, read.signal);
      if (!isCurrent(read)) return;
      if (value.outputId === output.id && sinkMatches(value.sink)) setResult(value);
      else setFailed(true);
    } catch (failure) {
      if (isCurrent(read)) { if (sandboxScopeDenied(failure)) onScopeDenied(); else setFailed(true); }
    } finally {
      if (request.current === read && isCurrent(read)) { request.current = undefined; setBusy(undefined); }
    }
  };
  const runCopy = async () => {
    if (!copyAvailable || request.current || !copyReview || blocked || receipt) return;
    let exact = pending;
    if (copyReview === 'new') {
      if (!result?.eligible || exact) return;
      try {
        exact = { ...binding, requestId: crypto.randomUUID() };
        sessionStorage.setItem(key, JSON.stringify(exact)); setPending(exact);
      } catch {
        setCopyNotice('Could not keep this exact request for recovery. No copy was sent.'); setCopyReview(undefined); return;
      }
    }
    if (!exact) return;
    const read = new AbortController(); request.current = read; setBusy('copy'); setCopyNotice(''); setCopyReview(undefined);
    try {
      const committed = await copySandboxHandoff(exact, read.signal);
      if (!isCurrent(read)) return;
      if (committed.requestId !== exact.requestId || committed.outputId !== output.id || !sinkMatches(committed.sink) ||
        !Number.isSafeInteger(committed.rowsCopied) || committed.rowsCopied < 0) throw new Error('Unverified local copy');
      forget(); setReceipt(committed);
    } catch (failure) {
      if (!isCurrent(read)) return;
      if (sandboxScopeDenied(failure)) onScopeDenied();
      else {
        const status = (failure as { response?: { status?: number } })?.response?.status;
        if (status === 409 || status === 422) {
          forget(); setBlocked(true);
          setCopyNotice('This exact request was rejected. No new local copy was confirmed.');
        } else setCopyNotice('The copy outcome could not be confirmed. Retry the original request, not a new copy.');
      }
    } finally {
      if (request.current === read && isCurrent(read)) { request.current = undefined; setBusy(undefined); }
    }
  };
  return <Modal open destroyOnHidden footer={null} className="flow-surface-modal" title="Check governed handoff?"
    onCancel={() => { request.current?.abort(); onClose(); }}>
    <section className="flow-sandbox-builder flow-sandbox-builder__compare" aria-label="Governed handoff eligibility">
      <p>Check whether the sealed output may be considered for the fixed local review sink <strong>{sink.label}</strong>.</p>
      <p>Committed output <code>{output.id}</code> · reviewed configuration {output.reviewedGeneration}.</p>
      <SandboxMarkings markings={output.markings} label="Output markings" />
      <p>The check evaluates the output and inherited source markings. Qualification alone does not authorize or send a copy or produce a delivery receipt.</p>
      <div className="flow-sandbox-builder__actions"><Button size="small" type="primary" loading={busy === 'check'} disabled={Boolean(busy || copyReview)}
        onClick={() => void qualify()}>Confirm eligibility check</Button></div>
      {failed && <p role="status">Eligibility could not be verified for this sealed output and sink review.</p>}
      {result && <>
        <strong role="status">{result.eligible ? 'Eligible for separate review' : 'Not eligible for this review sink'}</strong>
        <SandboxMarkings markings={result.markings} />
        {!result.eligible && result.reason === 'clearance_mismatch' && <p>The sink’s reviewed clearance does not cover the output’s effective markings.</p>}
        {!receipt && !pending && <p>No data was sent by this eligibility check.</p>}
      </>}
      {copyAvailable && !blocked && !receipt && !copyReview && <div className="flow-sandbox-builder__actions">
        {pending ? <Button size="small" disabled={Boolean(busy)} onClick={() => setCopyReview('retry')}>Retry original local copy</Button>
          : result?.eligible && <Button size="small" disabled={Boolean(busy)} onClick={() => setCopyReview('new')}>Review local copy</Button>}
      </div>}
      {copyReview && copyAvailable && <section className="flow-sandbox-builder__compare-result" aria-label="Review local copy">
        <h2>{copyReview === 'retry' ? 'Recover original copy?' : 'Copy this exact sealed output locally?'}</h2>
        <p>{copyReview === 'retry' ? 'This reuses the original request ID, exact output seals and reviewed sink.' :
          <>Copy only this sealed output to the fixed private local sink <strong>{sink.label}</strong>. Its markings remain unchanged. No external delivery occurs.</>}</p>
        <div className="flow-sandbox-builder__actions"><Button size="small" type="primary" onClick={() => void runCopy()}>
          {copyReview === 'retry' ? 'Confirm original copy retry' : 'Confirm local copy'}
        </Button><Button size="small" onClick={() => setCopyReview(undefined)}>Back</Button></div>
      </section>}
      {busy === 'copy' && <p role="status">Checking the exact local copy outcome…</p>}
      {copyNotice && <p role="status">{copyNotice}</p>}
      {receipt && <section aria-label="Committed local handoff receipt" className="flow-sandbox-builder__compare-result">
        <h2>Committed local handoff receipt</h2>
        <span>Receipt: <code>{receipt.id}</code></span>
        <span>Rows copied: {receipt.rowsCopied}</span>
        <span>Recorded at: <time dateTime={receipt.recordedAt}>{new Date(receipt.recordedAt).toLocaleString()}</time></span>
        <SandboxMarkings markings={receipt.markings} label="Copied markings" />
        {receipt.rowsCopied === 0 && <p>Empty committed copy. No rows moved.</p>}
      </section>}
    </section>
  </Modal>;
}

import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchSandboxHandoffCapability, commitSandboxPipelineOutput, fetchSandboxPipelineOutput, fetchSandboxPipelineOutputs } from '../../services/api';
import type {
  SandboxHandoffCapability, SandboxPipelineOutput, SandboxPipelineOutputDetail, SandboxPipelineOutputList, SandboxPipelineOutputRequest, SandboxPipelineRevision,
} from '../../services/api';
import { getSessionState } from '../../services/session';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';
import SandboxPipelineOutputComparison from './SandboxPipelineOutputComparison';
import SandboxPipelineRecordHistory from './SandboxPipelineRecordHistory';
import SandboxPipelineHandoff from './SandboxPipelineHandoff';

type Confirmation = { revision: SandboxPipelineRevision } | { previous: SandboxPipelineOutputRequest };
const timeText = (value: string) => new Date(value).toLocaleString();
function readPending(key: string): SandboxPipelineOutputRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as SandboxPipelineOutputRequest | null;
    if (value && typeof value.requestId === 'string' && Number.isSafeInteger(value.expectedGeneration) &&
      typeof value.expectedConfigHash === 'string' && /^[0-9a-f]{64}$/.test(value.expectedConfigHash)) return value;
  } catch { /* New commits require storage before sending a request. */ }
}
function clearPending(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* Keep the committed receipt visible if storage is lost. */ }
}

function ReceiptFacts({ item }: { item: SandboxPipelineOutput }) {
  return <>
    <strong>Committed output receipt</strong>
    <span>Output rows: {item.outputRows.toLocaleString()}{item.outputRows === 0 ? ' · No observed movement' : ''}</span>
    <span>Input rows: {item.inputRows.toLocaleString()} · Reviewed configuration revision {item.reviewedGeneration}</span>
    <span>{item.matchVersionId ? 'Left version receipt' : 'Source snapshot receipt'}: <code>{item.versionId}</code></span>
    {item.matchVersionId && <span>Matching version receipt: <code>{item.matchVersionId}</code></span>}
    <span>Output receipt ID: <code>{item.id}</code> · Original request ID: <code>{item.requestId}</code></span>
    <span>Recorded at: <time dateTime={item.recordedAt}>{timeText(item.recordedAt)}</time></span>
    <SandboxMarkings markings={item.markings} />
  </>;
}

export default function SandboxPipelineOutputs({ reviewed, scopeKey, onScopeDenied }: {
  reviewed?: SandboxPipelineRevision; scopeKey?: string; onScopeDenied?: () => void;
}) {
  const identity = useRef(getSessionState().identity).current;
  const storageKey = `ontology-pipeline-output:${identity}${scopeKey ? `:${scopeKey}` : ''}`;
  const active = useRef(false);
  const pendingMutation = useRef(false);
  const [pendingRequest, setPendingRequest] = useState(() => readPending(storageKey));
  const [list, setList] = useState<SandboxPipelineOutputList>();
  const [loadFailed, setLoadFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState<{ text: string; error?: boolean }>();
  const [confirm, setConfirm] = useState<Confirmation>();
  const [busy, setBusy] = useState(false);
  const [inspecting, setInspecting] = useState<SandboxPipelineOutput>();
  const [detail, setDetail] = useState<SandboxPipelineOutputDetail>();
  const [detailFailed, setDetailFailed] = useState(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [recordHistoryOpen, setRecordHistoryOpen] = useState(false);
  const [handoff, setHandoff] = useState<SandboxHandoffCapability>();
  const handoffSink = handoff?.sink;
  const [handoffOutput, setHandoffOutput] = useState<SandboxPipelineOutput>();
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const invalidate = () => {
    setList(undefined); setLoadFailed(true); setInspecting(undefined); setDetail(undefined);
    setConfirm(undefined); setPendingRequest(undefined); setNotice(undefined);
    setComparisonOpen(false); setRecordHistoryOpen(false); setHandoffOutput(undefined); setHandoff(undefined);
    onScopeDenied?.();
  };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setList(undefined); setLoadFailed(false); setComparisonOpen(false); setRecordHistoryOpen(false);
    void fetchSandboxPipelineOutputs(controller.signal).then(value => {
      if (!live(controller.signal)) return;
      setList(value);
      const saved = readPending(storageKey);
      const original = saved && value.outputs.find(item => item.requestId === saved.requestId &&
        item.reviewedGeneration === saved.expectedGeneration && item.configHash === saved.expectedConfigHash);
      if (original) {
        clearPending(storageKey); setPendingRequest(undefined);
        setNotice({ text: `Original output receipt confirmed: ${original.id}.` });
      }
    }).catch(() => { if (live(controller.signal)) setLoadFailed(true); });
    return () => controller.abort();
  }, [identity, refresh]);

  useEffect(() => {
    const read = new AbortController();
    setHandoffOutput(undefined); setHandoff(undefined);
    if (scopeKey && list?.available && list.outputs.length) {
      void fetchSandboxHandoffCapability(read.signal).then(value => {
        if (live(read.signal) && value.available && value.sink?.id === 'synthetic-review-sink') setHandoff(value);
      }).catch(() => { if (live(read.signal)) setHandoff(undefined); });
    }
    return () => read.abort();
  }, [scopeKey, list]);

  useEffect(() => {
    if (!inspecting) return;
    const controller = new AbortController();
    void fetchSandboxPipelineOutput(inspecting.id, controller.signal).then(value => {
      if (!live(controller.signal)) return;
      if (value.id === inspecting.id) setDetail(value); else setDetailFailed(true);
    }).catch(failure => {
      if (live(controller.signal)) {
        if (scopeKey && sandboxScopeDenied(failure)) invalidate(); else setDetailFailed(true);
      }
    });
    return () => controller.abort();
  }, [identity, inspecting]);

  const submit = async () => {
    if (!confirm || !list?.available || pendingMutation.current || !live()) return;
    let request: SandboxPipelineOutputRequest;
    const retry = 'previous' in confirm;
    if ('previous' in confirm) request = confirm.previous;
    else {
      const { revision } = confirm;
      if (!reviewed || revision.generation !== reviewed.generation || revision.configHash !== reviewed.configHash) {
        setNotice({ text: 'The reviewed configuration changed. Save and review the current configuration before committing.', error: true });
        setConfirm(undefined); return;
      }
      try {
        request = { requestId: crypto.randomUUID(), expectedGeneration: revision.generation, expectedConfigHash: revision.configHash };
        sessionStorage.setItem(storageKey, JSON.stringify(request)); setPendingRequest(request);
      } catch {
        setNotice({ text: 'Could not retain the original request ID in this session. No commit was sent.', error: true });
        setConfirm(undefined); return;
      }
    }
    pendingMutation.current = true; setBusy(true); setNotice(undefined);
    try {
      const receipt = await commitSandboxPipelineOutput(request);
      if (receipt.requestId !== request.requestId || receipt.reviewedGeneration !== request.expectedGeneration ||
        receipt.configHash !== request.expectedConfigHash) throw new Error('Mismatched receipt');
      clearPending(storageKey);
      if (live()) {
        setPendingRequest(undefined);
        setNotice({ text: `${retry ? 'Original' : 'Committed'} output receipt confirmed: ${receipt.id}.` });
      }
    } catch (failure) {
      if (live()) {
        const status = (failure as { response?: { status?: number } })?.response?.status;
        if (scopeKey && sandboxScopeDenied(failure)) {
          invalidate();
        } else if (status === 409 || status === 422) {
          clearPending(storageKey); setPendingRequest(undefined);
          setNotice({ text: 'Output commit rejected. Review the latest saved configuration and its bounds before submitting.', error: true });
        } else setNotice({ text: 'The outcome could not be confirmed. Retry the original request instead of creating a new one.', error: true });
      }
    } finally {
      pendingMutation.current = false;
      if (live()) { setBusy(false); setConfirm(undefined); setRefresh(value => value + 1); }
    }
  };

  return <section className="flow-sandbox-builder__history" aria-label="Committed sandbox outputs">
    <h2>Committed sandbox outputs</h2>
    <p>Only an explicit commit writes immutable sandbox output rows and a receipt. Previewing or saving a configuration does not write output data.</p>
    <div className="flow-sandbox-builder__actions"><Button size="small" disabled={busy} onClick={() => { setComparisonOpen(false); setRecordHistoryOpen(false); setRefresh(value => value + 1); }}>Refresh output history</Button>
      {list?.available && list.outputs.length >= 2 && <Button size="small" disabled={busy} onClick={() => setComparisonOpen(true)}>Compare committed outputs</Button>}
      {list?.available && list.outputs.length >= 2 && <Button size="small" disabled={busy} onClick={() => setRecordHistoryOpen(true)}>Record history</Button>}
    </div>
    {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    {loadFailed ? <p role="alert">Sandbox output status could not be loaded.</p>
      : !list ? <p role="status">Loading sandbox outputs…</p>
        : !list.available ? <p>Sandbox output commit is disabled in this environment.</p>
          : <>
            {pendingRequest ? <><p>Original request retained: <code>{pendingRequest.requestId}</code>.</p>
              <Button size="small" disabled={busy} onClick={() => setConfirm({ previous: pendingRequest })}>Retry original output request</Button></>
              : <><Button size="small" type="primary" disabled={busy || !reviewed} onClick={() => { if (reviewed) setConfirm({ revision: reviewed }); }}>
                Commit reviewed output
              </Button>
              {!reviewed && <p>Save the exact current configuration revision before committing output.</p>}</>}
            {list.outputs.length ? <ol aria-label="Committed output receipts" className="flow-sandbox-builder__output-list">
              {list.outputs.map(item => <li key={item.id}>
                <ReceiptFacts item={item} />
                {handoffSink && <Button size="small" onClick={() => setHandoffOutput(item)}>Check handoff eligibility</Button>}
                <Button size="small" onClick={() => { setDetail(undefined); setDetailFailed(false); setInspecting(item); }}>Inspect committed output</Button>
              </li>)}
            </ol> : <p>No committed sandbox output receipts.</p>}
          </>}
    {handoffOutput && handoffSink && list?.available && <SandboxPipelineHandoff key={`${handoffOutput.id}:${handoffSink.generation}`}
      output={handoffOutput} sink={handoffSink} copyAvailable={handoff?.copyAvailable === true} scopeKey={scopeKey} onClose={() => setHandoffOutput(undefined)} onScopeDenied={invalidate} />}
    {comparisonOpen && list?.available && <SandboxPipelineOutputComparison outputs={list.outputs}
      onClose={() => setComparisonOpen(false)} onScopeDenied={invalidate} />}
    {recordHistoryOpen && list?.available && <SandboxPipelineRecordHistory key={JSON.stringify([identity, scopeKey, refresh])} outputs={list.outputs}
      onClose={() => setRecordHistoryOpen(false)} onScopeDenied={invalidate} />}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" confirmLoading={busy}
      title={confirm && 'previous' in confirm ? 'Retry original output request?' : 'Commit reviewed sandbox output?'}
      okText={confirm && 'previous' in confirm ? 'Confirm original retry' : 'Confirm output commit'}
      onCancel={() => { if (!busy) setConfirm(undefined); }} onOk={submit}>
      {confirm && ('previous' in confirm ? <p>Reuse request <code>{confirm.previous.requestId}</code> and exact revision {confirm.previous.expectedGeneration}.
        If already committed, this returns the original receipt.</p> : <>
        <p>Write immutable sandbox output from saved configuration revision <strong>{confirm.revision.generation}</strong> and {confirm.revision.config.matchVersionId ? 'left version' : 'captured source'} receipt <code>{confirm.revision.config.versionId}</code>.</p>
        {confirm.revision.config.matchVersionId && <p>Matching version receipt: <code>{confirm.revision.config.matchVersionId}</code>. Its IDs control membership; values come from the left.</p>}
        <p>Reviewed configuration hash: <code>{confirm.revision.configHash}</code>. No live source or arbitrary destination is used.</p>
        <SandboxMarkings markings={confirm.revision.markings} />
      </>)}
    </Modal>
    <Modal open={Boolean(inspecting)} destroyOnHidden className="flow-surface-modal" title="Immutable committed output"
      onCancel={() => setInspecting(undefined)} footer={null} width={720}>
      {detailFailed ? <p role="alert">The stored output could not be verified.</p>
        : !detail ? <p role="status">Loading stored output…</p> : <>
          <div className="flow-sandbox-builder__output-detail"><ReceiptFacts item={detail} />
            <span>Reviewed configuration hash: <code>{detail.configHash}</code></span>
            <span>Stored result seal: <code>{detail.resultHash}</code></span>
          </div>
          <p>These are the immutable rows for this receipt, not a live table or a new preview.</p>
          {detail.rows.length ? <div className="flow-sandbox-builder__table"><table aria-label="Committed output rows">
            <thead><tr>{detail.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
            <tbody>{detail.rows.map((row, index) => <tr key={index}>{row.map((value, position) =>
              <td key={position}><code>{value === '' ? '""' : value}</code></td>)}</tr>)}</tbody>
          </table></div> : <p>This committed output contains no rows.</p>}
        </>}
    </Modal>
  </section>;
}

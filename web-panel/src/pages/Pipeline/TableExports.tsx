import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import {
  fetchTableExportAttempts, fetchTableExportCapabilities, fetchTableExportPlan, fetchTableExportReceipts,
  runTableExport, saveTableExportPlan,
} from '../../services/api';
import type { TableExportAttempt, TableExportCapabilities, TableExportPlan, TableExportReceipt } from '../../services/api';
import { getSessionState } from '../../services/session';

type RunRequest = { requestId: string; expectedUpdatedAt: string };
const dateText = (timestamp: string) => new Date(timestamp).toLocaleString();
const recoverable = new Set(['started', 'retryable', 'outcome_unknown']);
const attemptLabel: Record<TableExportAttempt['status'], string> = {
  started: 'Request recorded', retryable: 'Retry available', outcome_unknown: 'Receiver outcome unknown',
  rejected: 'Rejected', committed: 'Receiver confirmed', replayed: 'Existing receipt returned',
};

function readPending(key: string): RunRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as RunRequest | null;
    if (value && typeof value.requestId === 'string' && typeof value.expectedUpdatedAt === 'string') return value;
  } catch { /* A new run requires usable client storage before it sends. */ }
}

function rememberPending(key: string, request: RunRequest) {
  sessionStorage.setItem(key, JSON.stringify(request));
}

function forgetPending(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* The same request ID is safe to replay. */ }
}

function exportFailure(failure: unknown) {
  const response = (failure as { response?: { status?: number; data?: { detail?: unknown; errors?: unknown } } })?.response;
  const detail = response?.data?.detail ?? response?.data?.errors;
  const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string' ? detail.code : '';
  return { status: response?.status, code };
}

export default function TableExports({ openFromLink, onLinkClose }: { openFromLink: boolean; onLinkClose: () => void }) {
  const [open, setOpen] = useState(openFromLink);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    setOpen(openFromLink);
    if (openFromLink) heading.current?.focus();
  }, [openFromLink]);
  return <section className="ontology-source-catalog ontology-source-sync" aria-label="Table exports">
    <header>
      <div><h3 ref={heading} tabIndex={-1}>Table exports</h3><p>Bounded outbound pilot from a dedicated table to a fixed approved receiver.</p></div>
      <Button size="small" aria-expanded={open} onClick={() => {
        if (open && openFromLink) onLinkClose();
        setOpen(!open);
      }}>{open ? 'Close table exports' : 'Open table exports'}</Button>
    </header>
    {open && <TableExportWorkspace />}
  </section>;
}

function TableExportWorkspace() {
  const [capabilities, setCapabilities] = useState<TableExportCapabilities>();
  const [plan, setPlan] = useState<TableExportPlan | null>();
  const [name, setName] = useState('');
  const [loadingError, setLoadingError] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [notice, setNotice] = useState<{ message: string; kind: 'status' | 'alert' }>();
  const [revision, setRevision] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [view, setView] = useState<'receipts' | 'attempts'>();
  const [receipts, setReceipts] = useState<TableExportReceipt[]>();
  const [attempts, setAttempts] = useState<TableExportAttempt[]>();
  const [confirm, setConfirm] = useState<{ expectedUpdatedAt: string; previous?: RunRequest }>();
  const [operation, setOperation] = useState<'save' | 'run'>();
  const active = useRef(false);
  const pending = useRef(false);
  const identity = useRef(getSessionState().identity).current;
  const storageKey = `ontology-table-export:${identity}`;
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setLoadingError(false);
    void Promise.all([fetchTableExportCapabilities(read.signal), fetchTableExportPlan(read.signal)]).then(([flags, saved]) => {
      if (live(read.signal)) { setCapabilities(flags); setPlan(saved); setName(saved?.name ?? ''); }
    }).catch(() => { if (live(read.signal)) setLoadingError(true); });
    return () => read.abort();
  }, [revision, identity]);
  useEffect(() => {
    if (!view) return;
    const read = new AbortController();
    setHistoryError(false);
    const fail = () => { if (live(read.signal)) setHistoryError(true); };
    if (view === 'receipts') {
      void fetchTableExportReceipts(read.signal).then(value => { if (live(read.signal)) setReceipts(value); }).catch(fail);
    } else {
      void fetchTableExportAttempts(read.signal).then(value => { if (live(read.signal)) setAttempts(value); }).catch(fail);
    }
    return () => read.abort();
  }, [view, historyRevision, identity]);

  const changeView = (next: 'receipts' | 'attempts') => {
    if (next !== view) { setHistoryError(false); setView(next); }
  };
  const refreshPlan = () => { setPlan(undefined); setRevision(value => value + 1); };
  const save = async () => {
    if (!capabilities?.provisioned || plan === undefined || !name.trim() || pending.current || !live()) return;
    pending.current = true; setOperation('save'); setNotice(undefined);
    try {
      const saved = await saveTableExportPlan({ name: name.trim(), expectedUpdatedAt: plan?.updatedAt ?? null });
      if (live()) { setPlan(saved); setName(saved.name); setNotice({ kind: 'status', message: 'Export draft saved. No data was sent.' }); }
    } catch (failure) {
      if (live()) {
        const { status } = exportFailure(failure);
        setNotice({ kind: 'alert', message: status === 409 ? 'The export draft has changed. Refresh and review it again.' : 'Could not save the export draft.' });
      }
    } finally { pending.current = false; if (live()) setOperation(undefined); }
  };

  const askRun = (previous?: RunRequest) => {
    if (!plan || !capabilities?.enabled || !capabilities.provisioned || pending.current || !live()) return;
    const localRequest = readPending(storageKey);
    if (previous && localRequest && previous.requestId !== localRequest.requestId) {
      setNotice({ kind: 'alert', message: 'Resolve your pending export request before retrying a different request.' });
      return;
    }
    const request = previous ?? localRequest;
    setConfirm({ expectedUpdatedAt: request?.expectedUpdatedAt ?? plan.updatedAt, previous: request });
  };
  const run = async () => {
    if (!confirm || !capabilities?.enabled || !capabilities.provisioned || pending.current || !live()) return;
    let request: RunRequest;
    const retrying = Boolean(confirm.previous);
    try {
      request = confirm.previous ?? { requestId: crypto.randomUUID(), expectedUpdatedAt: confirm.expectedUpdatedAt };
      rememberPending(storageKey, request);
    } catch {
      setConfirm(undefined); setNotice({ kind: 'alert', message: 'Could not save a retry ID in this tab. No export request was sent.' });
      return;
    }
    pending.current = true; setOperation('run'); setNotice(undefined);
    try {
      const receipt = await runTableExport(request);
      forgetPending(storageKey);
      if (live()) {
        setReceipts(items => [receipt, ...(items?.filter(item => item.id !== receipt.id) ?? [])]);
        setView('receipts');
        setNotice({ kind: 'status', message: retrying ? 'The receiver-confirmed receipt is available.' : 'The receiver confirmed this export.' });
      }
    } catch (failure) {
      const { status, code } = exportFailure(failure);
      const retryable = ['target_busy', 'receiver_unavailable', 'receiver_timeout', 'execution_unavailable'].includes(code);
      const uncertain = code === 'outcome_unknown' || !status || status === 408 || status >= 500 && !retryable;
      const noReceiptConfirmed = status === 409 && code === 'payload_changed_no_receipt';
      if (noReceiptConfirmed || !retryable && !uncertain && !retrying) forgetPending(storageKey);
      if (live()) {
        setView('attempts');
        setNotice({ kind: 'alert', message: noReceiptConfirmed
          ? 'The source changed and the receiver confirmed there is no receipt for this request. Review the export before starting a new request.'
          : retryable || uncertain || retrying
            ? 'The receiver outcome is not confirmed. Review attempts or retry the same request.'
            : 'The export request was rejected. Review the attempt before making changes.' });
      }
    } finally {
      pending.current = false;
      if (live()) { setOperation(undefined); setConfirm(undefined); setHistoryRevision(value => value + 1); }
    }
  };

  const localRequest = readPending(storageKey);
  const available = Boolean(capabilities?.enabled && capabilities.provisioned);
  const route = capabilities && <div className="ontology-source-sync__route">
    <code>{capabilities.sourceSchema}.{capabilities.sourceTable}</code><ArrowRightOutlined /><code>{capabilities.targetSchema}.{capabilities.targetTable}</code>
  </div>;
  const seenRequests = new Set<string>();

  return <div className="ontology-source-sync__body">
    {loadingError ? <p role="alert" className="is-error">Could not load the table export configuration. <Button size="small" onClick={refreshPlan}>Refresh export</Button></p>
      : !capabilities || plan === undefined ? <p role="status">Loading table export…</p> : <>
        {!capabilities.enabled && <p role="status">Outbound execution is disabled. Saving a draft does not send data.</p>}
        {!capabilities.provisioned && <p role="status">The approved pilot receiver is not provisioned. A draft cannot be saved yet.</p>}
        <div className="ontology-table-export__mapping">
          <h4>Fixed source and approved destination</h4>
          {route}
          <p>Use only synthetic pilot records. The destination and columns are fixed; no custom endpoint, schedule, or target deletion.</p>
          <div className="ontology-source-catalog__columns"><table aria-label="Export column mapping">
            <thead><tr><th scope="col">Source column</th><th scope="col">Receiver column</th><th scope="col">Type</th></tr></thead>
            <tbody>{capabilities.mapping.map(field => <tr key={field.targetColumn}><th scope="row"><code>{field.sourceColumn}</code></th><td><code>{field.targetColumn}</code></td><td><code>{field.type}</code></td></tr>)}</tbody>
          </table></div>
          <p>Transfer limit: {capabilities.maxRows} rows and {capabilities.maxCaptionBytes.toLocaleString()} bytes per caption.</p>
        </div>
        <form className="ontology-source-sync__form" aria-label="Export draft" onSubmit={event => { event.preventDefault(); void save(); }}>
          <label>Export name<Input aria-label="Export name" required maxLength={120} value={name} disabled={Boolean(operation) || !capabilities.provisioned} onChange={event => setName(event.target.value)} /></label>
          <div className="ontology-source-sync__actions">
            <Button htmlType="submit" size="small" loading={operation === 'save'} disabled={!capabilities.provisioned || Boolean(operation) || !name.trim()}>Save export draft</Button>
            <Button size="small" onClick={refreshPlan} disabled={Boolean(operation)}>Refresh export</Button>
            {plan && available && <Button size="small" type="primary" loading={operation === 'run'} disabled={Boolean(operation) || (!localRequest && name.trim() !== plan.name)} onClick={() => askRun()}>
              {localRequest ? 'Retry same export request' : 'Run sandbox export'}
            </Button>}
          </div>
        </form>
      </>}
    {notice && <p role={notice.kind} className={notice.kind === 'alert' ? 'is-error' : undefined}>{notice.message}</p>}
    {capabilities && plan !== undefined && <section aria-label="Table export history">
      <div className="ontology-source-sync__actions" role="group" aria-label="Export history view">
        <Button size="small" aria-pressed={view === 'receipts'} onClick={() => changeView('receipts')}>Receiver receipts</Button>
        <Button size="small" aria-pressed={view === 'attempts'} onClick={() => changeView('attempts')}>Attempts</Button>
        {view && <Button size="small" onClick={() => setHistoryRevision(value => value + 1)}>Refresh history</Button>}
      </div>
      {historyError ? <p role="alert" className="is-error">Could not load export history.</p>
        : view === 'receipts' ? !receipts ? <p role="status">Loading receiver receipts…</p> : receipts.length ? <ul className="ontology-source-sync__items">
          {receipts.map(receipt => <li key={receipt.id}>
            <strong>Receiver confirmed</strong>
            <span>Rows affected: {receipt.rowsAffected}</span>
            <span>Receiver recorded at: <time dateTime={receipt.recordedAt}>{dateText(receipt.recordedAt)}</time></span>
            <span>Request ID: <code>{receipt.requestId}</code></span>
            <span>Receipt: <code>{receipt.id}</code></span>
          </li>)}
        </ul> : <p>No receiver-confirmed receipts.</p>
          : view === 'attempts' && <>
            <p>A request was not necessarily accepted or committed by the receiver. Verify the receipt before treating an export as complete.</p>
            {!attempts ? <p role="status">Loading export attempts…</p> : attempts.length ? <ul className="ontology-source-sync__items">
              {attempts.map(attempt => {
                const canRetry = !seenRequests.has(attempt.requestId) && recoverable.has(attempt.status);
                seenRequests.add(attempt.requestId);
                return <li key={attempt.id}>
                  <strong>{attemptLabel[attempt.status]}</strong>
                  <span>Request ID: <code>{attempt.requestId}</code></span>
                  <span>Requested at: <time dateTime={attempt.startedAt}>{dateText(attempt.startedAt)}</time></span>
                  {attempt.finishedAt && <span>Finished at: <time dateTime={attempt.finishedAt}>{dateText(attempt.finishedAt)}</time></span>}
                  {attempt.receiptId && <span>Receipt: <code>{attempt.receiptId}</code></span>}
                  {attempt.message && <p>{attempt.message}</p>}
                  {plan && available && canRetry && <div><Button size="small" disabled={Boolean(operation)} onClick={() => askRun({ requestId: attempt.requestId, expectedUpdatedAt: attempt.planUpdatedAt })}>Retry attempt</Button></div>}
                </li>;
              })}
            </ul> : <p>No export attempts recorded.</p>}
          </>}
    </section>}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm?.previous ? 'Retry previous export?' : 'Run sandbox table export?'}
      onCancel={() => setConfirm(undefined)} onOk={run} confirmLoading={operation === 'run'} okText={confirm?.previous ? 'Retry same request' : 'Confirm sandbox export'}>
      {confirm?.previous ? <>
        <p>Reuse the original request ID and saved export revision. If the receiver already committed it, the same request cannot apply it twice.</p>
        <p>Request ID: <code>{confirm.previous.requestId}</code></p>
      </> : <>
        <p>Send up to {capabilities?.maxRows} synthetic records from this saved revision to the fixed approved receiver. Only the listed columns are sent. No target rows are deleted.</p>
        {route}
        {capabilities && <p>Mapped columns: {capabilities.mapping.map(field => `${field.sourceColumn} → ${field.targetColumn}`).join(', ')}.</p>}
      </>}
    </Modal>
  </div>;
}

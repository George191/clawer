import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, NativeSelect } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import {
  fetchTableExportAttempts, fetchTableExportCapabilities, fetchTableExportGrant, fetchTableExportGrantHistory,
  fetchTableExportPlan, fetchTableExportReceipts, runTableExport, saveTableExportPlan, updateTableExportGrant,
} from '../../services/api';
import type {
  TableExportAttempt, TableExportCapabilities, TableExportDestination, TableExportGrant, TableExportGrantChange, TableExportGrantEvent,
  TableExportPlan, TableExportReceipt, TableExportWriteMode,
} from '../../services/api';
import { getSessionState } from '../../services/session';
import TableExportAuthorization from './TableExportAuthorization';
import { useSandboxIdentity } from './SandboxMarkings';
import { exportMode, exportModeDescription, exportModeLabel } from './tableExportWriteModes';

import TableExportSnapshotComparison from './TableExportSnapshotComparison';
import TableExportSnapshotDelivery from './TableExportSnapshotDelivery';
import TableExportSnapshotPrepare from './TableExportSnapshotPrepare';
import TableExportFrozenDelivery from './TableExportFrozenDelivery';

type RunRequest = { requestId: string; expectedUpdatedAt: string; expectedGrantGeneration: number; writeMode?: TableExportWriteMode };
type DraftReview = { name: string; expectedUpdatedAt: string | null; writeMode: TableExportWriteMode };
const dateText = (timestamp: string) => new Date(timestamp).toLocaleString();
const recoverable = new Set(['started', 'retryable', 'outcome_unknown']);
const attemptLabel: Record<TableExportAttempt['status'], string> = {
  started: 'Request recorded', retryable: 'Retry available', outcome_unknown: 'Receiver outcome unknown',
  rejected: 'Rejected', committed: 'Receiver confirmed', replayed: 'Existing receipt returned',
};

function readPending(key: string, destination: TableExportDestination, writeMode: TableExportWriteMode): RunRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as (RunRequest & { destination?: TableExportDestination }) | null;
    if (value && (value.destination === destination || (destination === 'primary' && value.destination === undefined)) &&
      exportMode(value) === writeMode && typeof value.requestId === 'string' && typeof value.expectedUpdatedAt === 'string' && Number.isSafeInteger(value.expectedGrantGeneration)) {
      return { requestId: value.requestId, expectedUpdatedAt: value.expectedUpdatedAt, expectedGrantGeneration: value.expectedGrantGeneration,
        ...(writeMode === 'append' ? { writeMode } : {}) };
    }
  } catch { /* A new run requires usable client storage before it sends. */ }
}

function rememberPending(key: string, request: RunRequest, destination: TableExportDestination) {
  sessionStorage.setItem(key, JSON.stringify({ ...request, destination, writeMode: exportMode(request) }));
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
  const identity = useSandboxIdentity();
  const [open, setOpen] = useState(openFromLink);
  const [working, setWorking] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { setWorking(false); }, [identity]);
  useEffect(() => {
    setOpen(openFromLink);
    if (openFromLink) heading.current?.focus();
  }, [openFromLink]);
  return <section className={`ontology-source-catalog ontology-source-sync ontology-transfer-tool${open ? ' is-open' : ''}`} aria-label="Table exports">
    <header>
      <div className="ontology-transfer-tool__description">
        <span className="ontology-transfer-tool__kind">Table export</span>
        <h3 ref={heading} tabIndex={-1}>Send test data</h3>
        <p>Send sample table rows to an approved test database, then check the delivery result.</p>
        <ol className="ontology-transfer-tool__steps" aria-label="Export workflow">
          <li><span>1</span>Review destination</li><li><span>2</span>Configure &amp; approve</li><li><span>3</span>Send &amp; check receipt</li>
        </ol>
      </div>
      <Button size="small" aria-expanded={open} disabled={working} onClick={() => {
        if (open && openFromLink) onLinkClose();
        setOpen(!open);
      }}>{open ? 'Hide export setup' : 'Set up test export'}</Button>
    </header>
    {open && identity && <>
      <ReceiverSelection key={identity} working={working} onWorkingChange={setWorking} />
      <TableExportSnapshotComparison key={`snapshot:${identity}`} identity={identity} />
      <TableExportSnapshotPrepare key={`prepare:${identity}`} identity={identity} />
      <TableExportFrozenDelivery key={`frozen-delivery:${identity}`} identity={identity} />
      <TableExportSnapshotDelivery key={`delivery:${identity}`} identity={identity} />
    </>}
  </section>;
}

function ReceiverSelection({ working, onWorkingChange }: { working: boolean; onWorkingChange: (working: boolean) => void }) {
  const [catalog, setCatalog] = useState<TableExportCapabilities>();
  const [failed, setFailed] = useState(false);
  const [version, setVersion] = useState(0);
  const [selected, setSelected] = useState<TableExportDestination>();
  useEffect(() => {
    const read = new AbortController();
    setFailed(false);
    void fetchTableExportCapabilities(read.signal).then(value => { if (!read.signal.aborted) setCatalog(value); })
      .catch(() => { if (!read.signal.aborted) { setCatalog(undefined); setFailed(true); } });
    return () => read.abort();
  }, [version]);
  if (failed) return <div className="ontology-source-sync__body"><p role="alert">Could not load the approved export receivers.
    {' '}<Button size="small" onClick={() => setVersion(value => value + 1)}>Refresh export</Button></p></div>;
  if (!catalog) return <div className="ontology-source-sync__body"><p role="status">Loading export configuration…</p></div>;
  const choices = catalog.approvedDestinations;
  if (!choices?.some(option => option.id === 'secondary')) return <TableExportWorkspace initialCapabilities={catalog} onWorkingChange={onWorkingChange} />;
  const receiver = choices.find(option => option.id === selected);
  return <>
    <div className="ontology-source-sync__body">
      <label className="ontology-receiver-selection">Approved receiver
        <NativeSelect aria-label="Approved receiver" value={receiver?.id ?? ''} disabled={working}
          onChange={event => { if (!working) setSelected(choices.find(option => option.id === event.target.value)?.id); }}>
          <option value="">Select one approved receiver</option>
          {choices.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
        </NativeSelect>
      </label>
      {!receiver && <p>Choose one receiver before inspecting its export draft, approval, or history.</p>}
    </div>
    {receiver && <TableExportWorkspace key={receiver.id} destination={receiver.id} label={receiver.label} onWorkingChange={onWorkingChange} />}
  </>;
}

function TableExportWorkspace({ destination, label, initialCapabilities, onWorkingChange }: {
  destination?: TableExportDestination; label?: string; initialCapabilities?: TableExportCapabilities;
  onWorkingChange: (working: boolean) => void;
}) {
  const [capabilities, setCapabilities] = useState<TableExportCapabilities>();
  const [plan, setPlan] = useState<TableExportPlan | null>();
  const [grant, setGrant] = useState<TableExportGrant>();
  const [name, setName] = useState('');
  const [writeMode, setWriteMode] = useState<TableExportWriteMode>('upsert');
  const [loadingError, setLoadingError] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [notice, setNotice] = useState<{ message: string; kind: 'status' | 'alert' }>();
  const [revision, setRevision] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [view, setView] = useState<'receipts' | 'attempts' | 'authorization'>();
  const [receipts, setReceipts] = useState<TableExportReceipt[]>();
  const [attempts, setAttempts] = useState<TableExportAttempt[]>();
  const [grantHistory, setGrantHistory] = useState<TableExportGrantEvent[]>();
  const [confirm, setConfirm] = useState<Omit<RunRequest, 'requestId'> & { previous?: RunRequest }>();
  const [draftReview, setDraftReview] = useState<DraftReview>();
  const [operation, setOperation] = useState<'save' | 'run' | 'grant'>();
  const active = useRef(false);
  const pending = useRef(false);
  const identity = useRef(getSessionState().identity).current;
  const savedMode = exportMode(plan);
  const activeGeneration = grant?.effective && plan && grant.planUpdatedAt === plan.updatedAt && exportMode(grant) === savedMode ? grant.generation : null;
  const available = Boolean(capabilities?.enabled && capabilities.provisioned && activeGeneration != null);
  const modeAvailable = savedMode === 'upsert' || capabilities?.appendAvailable === true;
  const receiverScope = destination ?? 'primary';
  // Primary retains the existing storage namespace; an untagged legacy request could only have been primary.
  const storageKey = `ontology-table-export:${identity}:${receiverScope === 'secondary' ? 'secondary:' : ''}${activeGeneration ?? 'inactive'}`;
  const localRequest = readPending(storageKey, receiverScope, savedMode);
  const dirty = name.trim() !== (plan?.name ?? '') || writeMode !== savedMode;
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setLoadingError(false);
    void Promise.all([
      revision === 0 && initialCapabilities ? Promise.resolve(initialCapabilities) : fetchTableExportCapabilities(read.signal, destination),
      fetchTableExportPlan(read.signal, destination), fetchTableExportGrant(read.signal, destination),
    ]).then(([flags, saved, authorization]) => {
      if (live(read.signal)) {
        setCapabilities(flags); setPlan(saved); setName(saved?.name ?? ''); setWriteMode(exportMode(saved)); setGrant(authorization);
      }
    }).catch(() => { if (live(read.signal)) setLoadingError(true); });
    return () => read.abort();
  }, [revision, identity, destination, initialCapabilities]);
  useEffect(() => {
    if (!view) return;
    const read = new AbortController();
    setHistoryError(false);
    const fail = () => { if (live(read.signal)) setHistoryError(true); };
    if (view === 'authorization') {
      void fetchTableExportGrantHistory(read.signal, destination)
        .then(value => { if (live(read.signal)) setGrantHistory(value); }).catch(fail);
    } else if (activeGeneration == null) {
      setReceipts(undefined); setAttempts(undefined);
    } else if (view === 'receipts') {
      void fetchTableExportReceipts(read.signal, destination).then(value => { if (live(read.signal)) setReceipts(value); }).catch(fail);
    } else {
      void fetchTableExportAttempts(read.signal, destination).then(value => { if (live(read.signal)) setAttempts(value); }).catch(fail);
    }
    return () => read.abort();
  }, [view, historyRevision, identity, activeGeneration, destination]);

  const changeView = (next: 'receipts' | 'attempts' | 'authorization') => {
    if (next !== view) { setHistoryError(false); setView(next); }
  };
  const refreshPlan = () => {
    setPlan(undefined); setGrant(undefined); setConfirm(undefined); setDraftReview(undefined); setReceipts(undefined); setAttempts(undefined);
    setRevision(value => value + 1);
  };
  const save = async (reviewed: DraftReview) => {
    if (!capabilities?.provisioned || plan === undefined || !reviewed.name || pending.current || !live() ||
      reviewed.expectedUpdatedAt !== (plan?.updatedAt ?? null) || (reviewed.writeMode === 'append' || reviewed.writeMode !== savedMode) && !capabilities.appendAvailable ||
      Boolean(localRequest && reviewed.writeMode !== savedMode)) return;
    pending.current = true; onWorkingChange(true); setOperation('save'); setNotice(undefined);
    try {
      const saved = await saveTableExportPlan({ name: reviewed.name, expectedUpdatedAt: reviewed.expectedUpdatedAt,
        ...(reviewed.writeMode === 'append' || reviewed.writeMode !== savedMode ? { writeMode: reviewed.writeMode } : {}) }, destination);
      if (exportMode(saved) !== reviewed.writeMode) throw new Error('Unverified export mode');
      if (live()) {
        setPlan(saved); setName(saved.name); setWriteMode(exportMode(saved)); setGrant(undefined); setReceipts(undefined); setAttempts(undefined);
        setRevision(value => value + 1);
        setNotice({ kind: 'status', message: reviewed.writeMode !== savedMode
          ? 'Export mode saved. Prior approval does not apply to this revision. No data was sent.'
          : 'Export draft saved. No approval was granted and no data was sent.' });
      }
    } catch (failure) {
      if (live()) {
        const { status } = exportFailure(failure);
        setNotice({ kind: 'alert', message: status === 409 ? 'The export draft has changed. Refresh and review it again.' : 'Could not save the export draft.' });
      }
    } finally { pending.current = false; if (live()) { setOperation(undefined); setDraftReview(undefined); onWorkingChange(false); } }
  };
  const requestSave = () => {
    if (!capabilities?.provisioned || plan === undefined || !name.trim() || pending.current || !live() ||
      (writeMode === 'append' || writeMode !== savedMode) && !capabilities.appendAvailable ||
      Boolean(localRequest && writeMode !== savedMode)) return;
    const reviewed = { name: name.trim(), expectedUpdatedAt: plan?.updatedAt ?? null, writeMode };
    if (writeMode === 'append' || writeMode !== savedMode) setDraftReview(reviewed);
    else void save(reviewed);
  };

  const changeGrant = async (request: TableExportGrantChange) => {
    if (pending.current || !live()) return;
    pending.current = true; onWorkingChange(true); setOperation('grant'); setNotice(undefined);
    try {
      const authorization = await updateTableExportGrant(request, destination);
      if (live()) {
        setGrant(authorization); setReceipts(undefined); setAttempts(undefined); setConfirm(undefined);
        setHistoryRevision(value => value + 1);
        setNotice({ kind: 'status', message: request.approved
          ? 'Approval recorded. No data was sent.' : 'Approval revoked. No previous delivery was undone.' });
      }
    } catch (failure) {
      if (live()) {
        const { status, code } = exportFailure(failure);
        refreshPlan();
        setNotice({ kind: 'alert', message: status === 409
          ? code === 'target_busy'
            ? 'Another export is settling. Review the authorization status and try again.'
            : 'Authorization could not be changed. Review the refreshed status before trying again.'
          : 'The authorization change could not be confirmed. Check the refreshed status.' });
      }
    } finally {
      pending.current = false;
      if (live()) { setOperation(undefined); onWorkingChange(false); }
    }
  };

  const askRun = (previous?: RunRequest) => {
    if (!plan || !available || activeGeneration == null || pending.current || !live()) return;
    if (previous && (previous.expectedGrantGeneration !== activeGeneration || exportMode(previous) !== savedMode)) return;
    if (previous && localRequest && previous.requestId !== localRequest.requestId) {
      setNotice({ kind: 'alert', message: 'Resolve your pending export request before retrying a different request.' });
      return;
    }
    const request = previous ?? localRequest;
    if (!request && (!modeAvailable || dirty)) return;
    setConfirm({ expectedUpdatedAt: request?.expectedUpdatedAt ?? plan.updatedAt,
      expectedGrantGeneration: activeGeneration, previous: request, ...(savedMode === 'append' ? { writeMode: savedMode } : {}) });
  };
  const run = async () => {
    if (!confirm || !available || confirm.expectedGrantGeneration !== activeGeneration || exportMode(confirm) !== savedMode ||
      (!confirm.previous && (!modeAvailable || dirty)) || pending.current || !live()) return;
    let request: RunRequest;
    const retrying = Boolean(confirm.previous);
    try {
      request = confirm.previous ?? { requestId: crypto.randomUUID(), expectedUpdatedAt: confirm.expectedUpdatedAt,
        expectedGrantGeneration: confirm.expectedGrantGeneration, ...(confirm.writeMode ? { writeMode: confirm.writeMode } : {}) };
      rememberPending(storageKey, request, receiverScope);
    } catch {
      setConfirm(undefined); setNotice({ kind: 'alert', message: 'Could not save a retry ID in this tab. No export request was sent.' });
      return;
    }
    pending.current = true; onWorkingChange(true); setOperation('run'); setNotice(undefined);
    try {
      const receipt = await runTableExport(request, destination);
      if (receipt.requestId !== request.requestId || receipt.grantGeneration !== request.expectedGrantGeneration ||
        exportMode(receipt) !== exportMode(request)) throw new Error('Unverified export receipt');
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
      const duplicate = status === 409 && code === 'append_duplicate' && exportMode(request) === 'append';
      if (duplicate || noReceiptConfirmed || !retryable && !uncertain && !retrying) forgetPending(storageKey);
      if (live()) {
        if (status === 403 || code.startsWith('grant_')) refreshPlan();
        setView('attempts');
        setNotice({ kind: 'alert', message: duplicate
          ? 'An existing key rejected the entire append batch. No existing rows were changed and no receipt was created.'
          : noReceiptConfirmed
          ? 'The source changed and the receiver confirmed there is no receipt for this request. Review the export before starting a new request.'
          : retryable || uncertain || retrying
            ? 'The receiver outcome is not confirmed. Review attempts or retry the same request.'
            : 'The export request was rejected. Review the attempt before making changes.' });
      }
    } finally {
      pending.current = false;
      if (live()) { setOperation(undefined); onWorkingChange(false); setConfirm(undefined); setHistoryRevision(value => value + 1); }
    }
  };

  const receiverLabel = capabilities?.destinationLabel ?? label;
  const route = capabilities && <div className="ontology-source-sync__route">
    <code>{capabilities.sourceSchema}.{capabilities.sourceTable}</code><ArrowRightOutlined /><code>{capabilities.targetSchema}.{capabilities.targetTable}</code>
  </div>;
  const currentReceipts = receipts?.filter(receipt => receipt.grantGeneration === activeGeneration);
  const currentAttempts = attempts?.filter(attempt => attempt.grantGeneration === activeGeneration);
  const seenRequests = new Set<string>();

  return <div className="ontology-source-sync__body">
    {loadingError ? <p role="alert" className="is-error">Could not load the table export configuration. <Button size="small" onClick={refreshPlan}>Refresh export</Button></p>
      : !capabilities || !grant || plan === undefined ? <p role="status">Loading table export…</p> : <>
        {!capabilities.enabled && <p role="status">Sending is not enabled for this test. You can review the setup before an administrator enables it.</p>}
        {!capabilities.provisioned && <p role="status">This test needs a destination. Ask an administrator to configure the approved test database before saving an export.</p>}
        <div className="ontology-table-export__mapping">
          <h4>Source and destination</h4>
          {receiverLabel && <p>Receiver: <strong>{receiverLabel}</strong></p>}
          {route}
          <p>Review the sample source, test destination, and columns below. Only the mapped columns are sent.</p>
          {plan && (capabilities.appendAvailable || plan.writeMode) && <p>Saved write mode: <strong>{exportModeLabel(savedMode)}</strong>.</p>}
          {!modeAvailable && <p>Append only is unavailable for new exports. An exact retained request may still recover its recorded result.</p>}
          <div className="ontology-source-catalog__columns"><table aria-label="Export column mapping">
            <thead><tr><th scope="col">Source column</th><th scope="col">Receiver column</th><th scope="col">Type</th></tr></thead>
            <tbody>{capabilities.mapping.map(field => <tr key={field.targetColumn}><th scope="row"><code>{field.sourceColumn}</code></th><td><code>{field.targetColumn}</code></td><td><code>{field.type}</code></td></tr>)}</tbody>
          </table></div>
          <p>Transfer limit: {capabilities.maxRows} rows and {capabilities.maxCaptionBytes.toLocaleString()} bytes per caption.</p>
        </div>
        <form className="ontology-source-sync__form" aria-label="Export draft" onSubmit={event => { event.preventDefault(); requestSave(); }}>
          <label>Export name<Input aria-label="Export name" required maxLength={120} value={name} disabled={Boolean(operation) || !capabilities.provisioned} onChange={event => setName(event.target.value)} /></label>
          {capabilities.appendAvailable && <><label>Export write mode
            <NativeSelect aria-label="Export write mode" value={writeMode} disabled={Boolean(operation) || !capabilities.provisioned || Boolean(localRequest)}
              onChange={event => setWriteMode(event.target.value === 'append' ? 'append' : 'upsert')}>
              <option value="upsert">Upsert</option><option value="append">Append only</option>
            </NativeSelect>
          </label><p>{exportModeDescription(writeMode)}</p></>}
          <div className="ontology-source-sync__actions">
            <Button htmlType="submit" size="small" loading={operation === 'save'} disabled={!capabilities.provisioned || Boolean(operation) || !name.trim() ||
              (writeMode === 'append' && !capabilities.appendAvailable) || Boolean(localRequest && writeMode !== savedMode)}>Save export draft</Button>
            <Button size="small" onClick={refreshPlan} disabled={Boolean(operation)}>Refresh export</Button>
            {plan && available && (modeAvailable || localRequest) && <Button size="small" type="primary" loading={operation === 'run'} disabled={Boolean(operation) || (!localRequest && dirty)} onClick={() => askRun()}>
              {localRequest ? 'Retry same export request' : 'Run sandbox export'}
            </Button>}
          </div>
        </form>
        <TableExportAuthorization grant={grant} plan={plan} scope={capabilities} receiverLabel={receiverLabel} busy={Boolean(operation)}
          saving={operation === 'grant'} dirty={dirty} onChange={changeGrant} />
      </>}
    {notice && <p role={notice.kind} className={notice.kind === 'alert' ? 'is-error' : undefined}>{notice.message}</p>}
    {capabilities && plan !== undefined && <section aria-label="Table export history">
      {receiverLabel && <p>History for <strong>{receiverLabel}</strong> only.</p>}
      <div className="ontology-source-sync__actions" role="group" aria-label="Export history view">
        <Button size="small" aria-pressed={view === 'receipts'} onClick={() => changeView('receipts')}>Receiver receipts</Button>
        <Button size="small" aria-pressed={view === 'attempts'} onClick={() => changeView('attempts')}>Attempts</Button>
        <Button size="small" aria-pressed={view === 'authorization'} onClick={() => changeView('authorization')}>Authorization history</Button>
        {view && <Button size="small" onClick={() => setHistoryRevision(value => value + 1)}>Refresh history</Button>}
      </div>
      {historyError ? <p role="alert" className="is-error">Could not load export history.</p>
        : view === 'authorization' ? !grantHistory ? <p role="status">Loading authorization history…</p>
          : grantHistory.length ? <ul className="ontology-source-sync__items">
            {grantHistory.map(event => <li key={event.id}>
              <strong>{event.action === 'approved' ? 'Approved' : 'Revoked'} · Generation {event.generation}</strong>
              <span>{event.marking} · <time dateTime={event.recordedAt}>{dateText(event.recordedAt)}</time></span>
              <span>Reviewed revision: <time dateTime={event.planUpdatedAt}>{dateText(event.planUpdatedAt)}</time></span>
              {(capabilities.appendAvailable || event.writeMode) && <span>Recorded write mode: {exportModeLabel(exportMode(event))}</span>}
              <span>Reason: {event.reason}</span>
            </li>)}
          </ul> : <p>No export authorization decisions recorded.</p>
        : view && activeGeneration == null ? <p>Delivery history is not available without an effective approval.</p>
        : view === 'receipts' ? !currentReceipts ? <p role="status">Loading receiver receipts…</p> : currentReceipts.length ? <ul className="ontology-source-sync__items">
          {currentReceipts.map(receipt => <li key={receipt.id}>
            <strong>Receiver confirmed</strong>
            {(capabilities.appendAvailable || receipt.writeMode) && <span>Recorded write mode: {exportModeLabel(exportMode(receipt))}</span>}
            <span>Rows affected: {receipt.rowsAffected}</span>
            <span>Receiver recorded at: <time dateTime={receipt.recordedAt}>{dateText(receipt.recordedAt)}</time></span>
            <span>Request ID: <code>{receipt.requestId}</code></span>
            <span>Receipt: <code>{receipt.id}</code></span>
          </li>)}
        </ul> : <p>No receiver-confirmed receipts.</p>
          : view === 'attempts' && <>
            <p>A request was not necessarily accepted or committed by the receiver. Verify the receipt before treating an export as complete.</p>
            {!currentAttempts ? <p role="status">Loading export attempts…</p> : currentAttempts.length ? <ul className="ontology-source-sync__items">
              {currentAttempts.map(attempt => {
                const canRetry = !seenRequests.has(attempt.requestId) && recoverable.has(attempt.status) && exportMode(attempt) === savedMode;
                seenRequests.add(attempt.requestId);
                return <li key={attempt.id}>
                  <strong>{attemptLabel[attempt.status]}</strong>
                  {(capabilities.appendAvailable || attempt.writeMode) && <span>Recorded write mode: {exportModeLabel(exportMode(attempt))}</span>}
                  <span>Request ID: <code>{attempt.requestId}</code></span>
                  <span>Requested at: <time dateTime={attempt.startedAt}>{dateText(attempt.startedAt)}</time></span>
                  {attempt.finishedAt && <span>Finished at: <time dateTime={attempt.finishedAt}>{dateText(attempt.finishedAt)}</time></span>}
                  {attempt.receiptId && <span>Receipt: <code>{attempt.receiptId}</code></span>}
                  {attempt.message && <p>{attempt.message}</p>}
                  {plan && available && canRetry && <div><Button size="small" disabled={Boolean(operation)} onClick={() => askRun({
                    requestId: attempt.requestId, expectedUpdatedAt: attempt.planUpdatedAt, expectedGrantGeneration: attempt.grantGeneration, writeMode: attempt.writeMode,
                  })}>Retry attempt</Button></div>}
                </li>;
              })}
            </ul> : <p>No export attempts recorded.</p>}
          </>}
    </section>}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm?.previous ? 'Retry previous export?' : 'Run sandbox table export?'}
      onCancel={() => setConfirm(undefined)} onOk={run} confirmLoading={operation === 'run'} okText={confirm?.previous ? 'Retry same request' : 'Confirm sandbox export'}>
      {confirm?.previous ? <>
        <p>Reuse the original request ID and saved export revision. If the receiver already committed it, the same request cannot apply it twice.</p>
        {receiverLabel && <p>Original receiver: <strong>{receiverLabel}</strong>.</p>}
        {(capabilities?.appendAvailable || confirm.writeMode) && <p>Original write mode: <strong>{exportModeLabel(exportMode(confirm))}</strong>. This will not change the mode.</p>}
        <p>Request ID: <code>{confirm.previous.requestId}</code></p>
      </> : <>
        <p>Send up to {capabilities?.maxRows} synthetic records from this saved revision to the fixed approved receiver. Only the listed columns are sent. No target rows are deleted.</p>
        {receiverLabel && <p>Destination: <strong>{receiverLabel}</strong>. No other receiver will be contacted.</p>}
        {confirm && (capabilities?.appendAvailable || confirm.writeMode) && <p>Write mode: <strong>{exportModeLabel(exportMode(confirm))}</strong>. {exportModeDescription(exportMode(confirm))}</p>}
        {route}
        {capabilities && <p>Mapped columns: {capabilities.mapping.map(field => `${field.sourceColumn} → ${field.targetColumn}`).join(', ')}.</p>}
      </>}
    </Modal>
    <Modal open={Boolean(draftReview)} destroyOnHidden className="flow-surface-modal" title="Review export write mode?"
      onCancel={() => { if (operation !== 'save') setDraftReview(undefined); }} onOk={() => draftReview ? save(draftReview) : undefined}
      confirmLoading={operation === 'save'} okText="Confirm export draft">
      {draftReview && <>
        <p>Draft: <strong>{draftReview.name}</strong>. {receiverLabel && <>Receiver: <strong>{receiverLabel}</strong>. </>}
          Write mode: <strong>{exportModeLabel(draftReview.writeMode)}</strong>.</p>
        <p>{exportModeDescription(draftReview.writeMode)}</p>
        <p>Saving this revision does not send data or carry forward a previous approval.</p>
      </>}
    </Modal>
  </div>;
}

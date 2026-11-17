import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import {
  fetchBatchSyncAttempts, fetchBatchSyncCapabilities, fetchBatchSyncReceipts, fetchBatchSyncSchedule, fetchBatchSyncScheduleJobs, fetchBatchSyncs, fetchDataSourceColumns,
  fetchEtlObjectTypes, fetchEtlTableColumns, runBatchSync, saveBatchSync, updateBatchSyncSchedule,
} from '../../services/api';
import type { BatchSourceIdentity, BatchSyncAttempt, BatchSyncCapabilities, BatchSyncInput, BatchSyncPlan, BatchSyncReceipt, BatchSyncSchedule, DataSource, DataSourceColumns, TransferScheduleJob } from '../../services/api';
import type { EtlTableColumn } from '../../services/types';
import { getSessionState } from '../../services/session';
import { sameSourceRevision } from './SourceIdentity';
import { nextLocalTimeForUtcClock } from '../../services/scheduleTime';

export type BatchSyncSource = { schema: string; table: string };
type RunRequest = { requestId: string; expectedUpdatedAt: string };
type Draft = { id?: string; name: string; key: string; target: string; mapping: Record<string, string> } & BatchSyncSource;
const dateText = (value: string) => new Date(value).toLocaleString();
const sameRevision = (left: string, right: string) =>
  left.replace(/\+00:00$/, 'Z') === right.replace(/\+00:00$/, 'Z');
const simpleTarget = /^[a-z_][a-z0-9_]{0,62}$/;
const supportedKey = /^(smallint|integer|bigint|uuid)$/;
const temporarySyncFailures: Record<string, string> = {
  target_busy: 'The destination is busy.',
  source_timeout: 'The source request timed out.',
  source_unavailable: 'The source could not be reached.',
  execution_unavailable: 'The executor is temporarily unavailable.',
};

function syncFailure(failure: unknown) {
  const response = (failure as { response?: { status?: number; data?: { detail?: unknown; errors?: unknown } } })?.response;
  const detail = response?.data?.detail ?? response?.data?.errors;
  const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string' ? detail.code : '';
  return { status: response?.status, detail, code };
}

function syncError(failure: unknown) {
  const { status, detail } = syncFailure(failure);
  // Do not display network/library errors or structured values containing untrusted source details.
  if (status && status < 500) {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'code' in detail && 'message' in detail && typeof detail.message === 'string') return detail.message;
  }
  return 'The batch sync request could not be confirmed.';
}

function storedRun(key: string): RunRequest | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as RunRequest | null;
    if (value && typeof value.requestId === 'string' && typeof value.expectedUpdatedAt === 'string') return value;
  } catch { /* Storage can be unavailable. No new run is sent without a retry ID. */ }
}

function forgetRun(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* Retained ID is safe to replay. */ }
}

function ReceiptList({ receipts }: { receipts: BatchSyncReceipt[] }) {
  return receipts.length ? <ul className="ontology-source-sync__items">{receipts.map(receipt => <li key={receipt.id}>
    <strong>Committed</strong>{receipt.sourceIdentityVerified && <span>Source identity verified for this run</span>}<span>Rows affected: {receipt.rowsAffected}</span><span>Destination: <code>{receipt.targetTable}</code></span>
    <span>Recorded at: <time dateTime={receipt.recordedAt}>{dateText(receipt.recordedAt)}</time></span><span>Receipt: <code>{receipt.id}</code></span>
  </li>)}</ul> : <p>No committed sync receipts.</p>;
}

const attemptLabels: Record<BatchSyncAttempt['status'], string> = {
  started: 'Request recorded',
  committed: 'Commit recorded',
  replayed: 'Existing receipt returned',
  rejected: 'Rejected',
  retryable: 'Retry available',
  outcome_unknown: 'Outcome unknown',
};

function AttemptList({ attempts, canRetry, onRetry }: { attempts: BatchSyncAttempt[]; canRetry: boolean; onRetry: (attempt: BatchSyncAttempt) => void }) {
  const seen = new Set<string>();
  return attempts.length ? <ul className="ontology-source-sync__items">{attempts.map(attempt => {
    const recoverable = attempt.scheduledJobId === null && !seen.has(attempt.requestId) && ['started', 'retryable', 'outcome_unknown'].includes(attempt.status);
    seen.add(attempt.requestId);
    return <li key={attempt.id}>
      <strong>{attemptLabels[attempt.status]}</strong>
      <span>Requested at: <time dateTime={attempt.startedAt}>{dateText(attempt.startedAt)}</time></span>
      {attempt.finishedAt && <span>Finished at: <time dateTime={attempt.finishedAt}>{dateText(attempt.finishedAt)}</time></span>}
      <span>Request ID: <code>{attempt.requestId}</code></span>
      {attempt.scheduledJobId && <span>Scheduled job: <code>{attempt.scheduledJobId}</code></span>}
      {attempt.receiptId && <span>Receipt: <code>{attempt.receiptId}</code></span>}
      {attempt.message && <p>{attempt.message}</p>}
      {canRetry && recoverable && <div><Button size="small" onClick={() => onRetry(attempt)}>Retry attempt</Button></div>}
    </li>;
  })}</ul> : <p>No batch sync attempts recorded.</p>;
}

const scheduleLabels: Record<TransferScheduleJob['state'], string> = {
  pending: 'Pending dispatch', dispatched: 'Dispatched', committed: 'Committed', rejected: 'Rejected',
  outcome_unknown: 'Outcome unknown', expired: 'Expired',
};
const utcClock = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const utcText = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });

function BatchSyncSchedulePanel({ plan, source, capability, sourceReviewNeeded }: { plan: BatchSyncPlan; source: DataSource; capability: BatchSyncCapabilities; sourceReviewNeeded: boolean }) {
  const [schedule, setSchedule] = useState<BatchSyncSchedule>();
  const [jobs, setJobs] = useState<TransferScheduleJob[]>();
  const [plannedTime, setPlannedTime] = useState('');
  const [stateError, setStateError] = useState('');
  const [jobError, setJobError] = useState('');
  const [requestError, setRequestError] = useState('');
  const [confirm, setConfirm] = useState<'arm' | 'pause'>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const active = useRef(false);
  const title = useRef<HTMLHeadingElement>(null);
  const identity = useRef(getSessionState().identity).current;
  const current = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { active.current = true; title.current?.focus(); return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setSchedule(undefined); setJobs(undefined); setStateError(''); setJobError('');
    void fetchBatchSyncSchedule(plan.id, read.signal).then(result => {
      if (current(read.signal)) { setSchedule(result); setPlannedTime(result.plannedTimeUtc ?? ''); }
    }).catch(failure => { if (current(read.signal)) setStateError(syncError(failure)); });
    void fetchBatchSyncScheduleJobs(plan.id, read.signal).then(result => {
      if (current(read.signal)) setJobs(result);
    }).catch(failure => { if (current(read.signal)) setJobError(syncError(failure)); });
    return () => read.abort();
  }, [plan.id, revision, identity]);
  const sourceChanged = !sameRevision(plan.sourceUpdatedAt, source.updatedAt);
  const canArm = Boolean(capability.scheduledBatchSync && schedule?.available && !schedule.configuredEnabled && !sourceChanged && !sourceReviewNeeded && utcClock.test(plannedTime));
  const changeSchedule = async () => {
    if (!confirm || !schedule || !current() || pending.current) return;
    const enabled = confirm === 'arm';
    const time = enabled ? plannedTime : schedule.plannedTimeUtc;
    if (!time || (enabled ? !canArm : !schedule.configuredEnabled)) return;
    setRequestError(''); setBusy(true); pending.current = true;
    try {
      await updateBatchSyncSchedule(plan.id, { enabled, plannedTimeUtc: time, expectedUpdatedAt: plan.updatedAt, expectedGeneration: schedule.generation });
    } catch (failure) {
      if (current()) setRequestError(`${syncError(failure)} The registered schedule is being refreshed before another change can be made.`);
    } finally {
      pending.current = false;
      if (current()) { setConfirm(undefined); setBusy(false); setRevision(value => value + 1); }
    }
  };

  return <section className="ontology-source-sync__schedule" aria-label="Batch sync schedule">
    <div className="ontology-source-sync__schedule-heading"><h4 ref={title} tabIndex={-1}>Batch sync schedule</h4><Button size="small" disabled={busy} onClick={() => { setRequestError(''); setRevision(value => value + 1); }}>Refresh schedule</Button></div>
    <p>Automation runs this bounded sandbox snapshot Upsert at the same UTC time each day. It does not delete target rows.</p>
    {requestError && <p role="alert" className="is-error">{requestError}</p>}
    {stateError ? <p role="alert" className="is-error">{stateError}</p> : !schedule ? <p role="status">Loading schedule…</p> : <>
      <p role="status"><strong>{schedule.configuredEnabled ? (schedule.available && schedule.enabled && !sourceReviewNeeded ? 'Schedule armed' : 'Schedule armed · blocked') : schedule.generation == null ? 'Schedule not armed' : 'Schedule paused'}</strong>
        {schedule.plannedTimeUtc && <> · Daily at {schedule.plannedTimeUtc} UTC · local time {nextLocalTimeForUtcClock(schedule.plannedTimeUtc)}</>}</p>
      {sourceChanged && <p>Review and save the changed source revision before arming a schedule.</p>}
      {sourceReviewNeeded && <p>A current verified source identity and freshly reviewed sync are required to arm a schedule.</p>}
      {!capability.scheduledBatchSync || !schedule.available ? <p>Scheduled execution is unavailable for this plan or environment.</p> : null}
      {schedule.configuredEnabled ? <Button size="small" disabled={busy || !schedule.plannedTimeUtc} onClick={() => setConfirm('pause')}>Pause schedule</Button> :
        <div className="ontology-source-sync__actions">
          <label>Daily time (UTC) <Input aria-label="Daily time (UTC)" type="time" step={60} disabled={busy || !capability.scheduledBatchSync || !schedule.available || sourceChanged || sourceReviewNeeded} value={plannedTime} onChange={event => setPlannedTime(event.target.value)} /></label>
          <Button size="small" type="primary" disabled={busy || !canArm} onClick={() => setConfirm('arm')}>Arm schedule</Button>
        </div>}
    </>}
    <h4>Schedule jobs</h4>
    <p>Dispatch is not a commit. Only a committed receipt verifies the transfer outcome.</p>
    {jobError ? <p role="alert" className="is-error">{jobError}</p> : !jobs ? <p role="status">Loading schedule jobs…</p> :
      jobs.length ? <ul className="ontology-source-sync__items">{jobs.map(job => <li key={job.id}>
        <strong>{scheduleLabels[job.state]}</strong>
        <span>Scheduled slot: <time dateTime={job.slot}>{utcText(job.slot)}</time></span>
        {job.dispatchedAt && <span>Dispatched at: <time dateTime={job.dispatchedAt}>{utcText(job.dispatchedAt)}</time></span>}
        {job.finishedAt && <span>Finished at: <time dateTime={job.finishedAt}>{utcText(job.finishedAt)}</time></span>}
        {job.receiptId && <span>Receipt: <code>{job.receiptId}</code></span>}
        {job.message && <p>{job.message}</p>}
      </li>)}</ul> : <p>No schedule jobs recorded.</p>}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm === 'pause' ? 'Pause batch sync schedule?' : 'Arm batch sync schedule?'}
      onCancel={() => setConfirm(undefined)} onOk={changeSchedule} confirmLoading={busy} okText={confirm === 'pause' ? 'Confirm pause' : 'Confirm arm schedule'}>
      {confirm === 'pause' ? <p>Stop future dispatches without reversing committed transfers. A transfer that has already started may still finish.</p> : <>
        <p>Run this saved plan and source revision daily at {plannedTime} UTC. Each run can read up to {capability.maxRows} rows and {capability.maxBytes.toLocaleString()} bytes into the sandbox.</p>
        <div className="ontology-source-sync__route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{capability.targetSchema}.{plan.targetTable}</code></div>
      </>}
    </Modal>
  </section>;
}

export default function SourceBatchSyncs({ source, sourceProof, selection, selectedSchedule, onComposerClosed }: {
  source: DataSource; sourceProof: BatchSourceIdentity | undefined; selection: BatchSyncSource | undefined; selectedSchedule?: string; onComposerClosed: () => void;
}) {
  const [capability, setCapability] = useState<BatchSyncCapabilities>();
  const [plans, setPlans] = useState<BatchSyncPlan[]>();
  const [plansForProof, setPlansForProof] = useState<string>();
  const [draft, setDraft] = useState<Draft>();
  const [sourceColumns, setSourceColumns] = useState<DataSourceColumns>();
  const [targets, setTargets] = useState<string[]>();
  const [targetColumns, setTargetColumns] = useState<EtlTableColumn[]>();
  const [stateError, setStateError] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [destinationError, setDestinationError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const [metadataRevision, setMetadataRevision] = useState(0);
  const [historyId, setHistoryId] = useState('');
  const [scheduleId, setScheduleId] = useState(selectedSchedule);
  const [historyView, setHistoryView] = useState<'receipts' | 'attempts'>('receipts');
  const [receiptList, setReceiptList] = useState<BatchSyncReceipt[]>();
  const [attemptList, setAttemptList] = useState<BatchSyncAttempt[]>();
  const [historyError, setHistoryError] = useState('');
  const [confirm, setConfirm] = useState<BatchSyncPlan>();
  const [retryAttempt, setRetryAttempt] = useState<RunRequest>();
  const [busy, setBusy] = useState<'save' | 'run'>();
  const pending = useRef(false);
  const active = useRef(false);
  const composer = useRef<HTMLHeadingElement>(null);
  const identity = useRef(getSessionState().identity).current;
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const proofCurrent = sourceProof?.state === 'verified' && sameSourceRevision(sourceProof.sourceUpdatedAt, source.updatedAt);
  const proofVersion = `${sourceProof?.state ?? ''}:${sourceProof?.verifiedAt ?? ''}`;
  const sourceReviewNeeded = (plan: BatchSyncPlan) => (source.engine === 'postgresql' && (!sourceProof || sourceProof.state === 'blocked')) || Boolean(capability?.sourceIdentityRequired &&
    (!proofCurrent || plansForProof !== proofVersion || !plan.sourceIdentityReviewed));
  const storageKey = (planId: string) => `ontology-batch-sync:${identity}:${planId}`;

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { setScheduleId(selectedSchedule); }, [selectedSchedule]);
  useEffect(() => {
    const read = new AbortController();
    setStateError('');
    void Promise.all([fetchBatchSyncCapabilities(read.signal), fetchBatchSyncs(source.id, read.signal)]).then(async ([flags, saved]) => {
      if (!live(read.signal)) return;
      setCapability(flags);
      if (selectedSchedule && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(selectedSchedule) && !saved.some(plan => plan.id === selectedSchedule)) {
        try {
          const exact = await fetchBatchSyncs(source.id, read.signal, selectedSchedule);
          if (live(read.signal)) { setPlans([...exact, ...saved]); setPlansForProof(proofVersion); }
        } catch (failure) {
          if (live(read.signal)) { setPlans(saved); setPlansForProof(proofVersion); setStateError(syncError(failure)); }
        }
      } else { setPlans(saved); setPlansForProof(proofVersion); }
    }).catch(failure => { if (live(read.signal)) setStateError(syncError(failure)); });
    return () => read.abort();
  }, [source.id, selectedSchedule, revision, identity, proofVersion]);
  useEffect(() => {
    if (!selection) return;
    if (draft) {
      setNotice('Save or cancel the current draft before configuring another sync.');
      onComposerClosed();
      composer.current?.focus();
      return;
    }
    setDraft({ ...selection, name: '', target: '', mapping: {}, key: '' });
    setSourceColumns(undefined); setTargetColumns(undefined); setSourceError(''); setDestinationError('');
    setSaveError(''); setNotice('');
  }, [selection]);
  useEffect(() => {
    if (!draft || !capability) return;
    const read = new AbortController();
    setTargets(undefined); setCatalogError('');
    const load = async () => {
      const names: string[] = [];
      let offset = 0;
      while (live(read.signal)) {
        const page = await fetchEtlObjectTypes({ schema: capability.targetSchema, limit: 100, offset }, read.signal);
        names.push(...page.items.map(item => item.table).filter(name => simpleTarget.test(name)));
        offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      }
      if (live(read.signal)) setTargets(names);
    };
    void load().catch(failure => { if (live(read.signal)) setCatalogError(syncError(failure)); });
    return () => read.abort();
  }, [Boolean(draft), capability?.targetSchema, metadataRevision]);
  const schema = draft?.schema;
  const table = draft?.table;
  const target = draft?.target;
  useEffect(() => {
    if (schema && table) composer.current?.focus();
  }, [schema, table, draft?.id]);
  useEffect(() => {
    if (!schema || !table) return;
    const read = new AbortController();
    setSourceColumns(undefined); setSourceError('');
    void fetchDataSourceColumns(source.id, schema, table, read.signal).then(result => {
      if (live(read.signal)) setSourceColumns(result);
    }).catch(failure => { if (live(read.signal)) setSourceError(syncError(failure)); });
    return () => read.abort();
  }, [source.id, source.updatedAt, schema, table, metadataRevision]);
  useEffect(() => {
    if (!capability || !target) return;
    const read = new AbortController();
    setTargetColumns(undefined); setDestinationError('');
    void fetchEtlTableColumns(capability.targetSchema, target, read.signal).then(result => {
      if (live(read.signal)) setTargetColumns(result);
    }).catch(failure => { if (live(read.signal)) setDestinationError(syncError(failure)); });
    return () => read.abort();
  }, [capability?.targetSchema, target, metadataRevision]);
  useEffect(() => {
    if (!historyId) return;
    const read = new AbortController();
    setHistoryError('');
    const onFailure = (failure: unknown) => { if (live(read.signal)) setHistoryError(syncError(failure)); };
    if (historyView === 'attempts') {
      setAttemptList(undefined);
      void fetchBatchSyncAttempts(historyId, read.signal).then(result => {
        if (live(read.signal)) setAttemptList(result);
      }).catch(onFailure);
    } else {
      setReceiptList(undefined);
      void fetchBatchSyncReceipts(historyId, read.signal).then(result => {
        if (live(read.signal)) setReceiptList(result);
      }).catch(onFailure);
    }
    return () => read.abort();
  }, [historyId, historyView, revision, identity]);

  const candidates = sourceColumns?.columns.filter(column => column.primaryKey) ?? [];
  const sourceKey = sourceColumns && !sourceColumns.hasMore && candidates.length === 1 && supportedKey.test(candidates[0].type) ? candidates[0].name : undefined;
  const targetKeys = targetColumns?.filter(column => column.primary) ?? [];
  const mappingRows = targetColumns?.map(column => ({ column, options: sourceColumns?.columns.filter(item => item.type === column.type && (!column.primary || item.name === sourceKey)) ?? [] })) ?? [];
  const change = (patch: Partial<Draft>) => { setSaveError(''); setDraft(value => value && { ...value, ...patch }); };
  const closeDraft = () => { setDraft(undefined); onComposerClosed(); };
  const edit = (plan: BatchSyncPlan) => {
    onComposerClosed(); setNotice(''); setSaveError('');
    setSourceColumns(undefined); setTargetColumns(undefined); setSourceError(''); setDestinationError('');
    setDraft({ id: plan.id, schema: plan.sourceSchema, table: plan.sourceTable, name: plan.name, key: plan.sourceKeyColumn,
      target: plan.targetTable, mapping: Object.fromEntries(plan.mapping.map(item => [item.targetColumn, item.sourceColumn])) });
  };
  const fullyMapped = Boolean(draft && sourceKey && draft.key === sourceKey && targets?.includes(draft.target) && targetKeys.length === 1 &&
    mappingRows.length && mappingRows.every(({ column, options }) => options.some(item => item.name === draft.mapping[column.name])) &&
    new Set(mappingRows.map(({ column }) => draft.mapping[column.name])).size === mappingRows.length);
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft || pending.current || !fullyMapped || !live() || !draft.name.trim() || sourceProof?.state === 'blocked') return;
    const payload: BatchSyncInput = {
      name: draft.name.trim(), connectionId: source.id, expectedSourceUpdatedAt: source.updatedAt,
      sourceSchema: draft.schema, sourceTable: draft.table, sourceKeyColumn: draft.key, targetTable: draft.target,
      mapping: mappingRows.map(({ column }) => ({ targetColumn: column.name, sourceColumn: draft.mapping[column.name] })),
    };
    pending.current = true; setBusy('save'); setSaveError('');
    try {
      const saved = await saveBatchSync(payload, draft.id);
      if (live()) {
        setPlans(items => [saved, ...(items?.filter(item => item.id !== saved.id) ?? [])]);
        closeDraft(); setNotice('Batch sync draft saved. No rows were copied.');
      }
    } catch (failure) { if (live()) setSaveError(syncError(failure)); }
    finally { pending.current = false; if (live()) setBusy(undefined); }
  };
  const retry = confirm && (retryAttempt ?? storedRun(storageKey(confirm.id)));
  const cancelRun = () => { setConfirm(undefined); setRetryAttempt(undefined); };
  const askRun = (plan: BatchSyncPlan, attempt?: BatchSyncAttempt) => {
    const pendingRequest = storedRun(storageKey(plan.id));
    if (attempt && pendingRequest && pendingRequest.requestId !== attempt.requestId) {
      setNotice('Resolve your pending request for this sync before retrying a different attempt.');
      return;
    }
    setRetryAttempt(attempt ? { requestId: attempt.requestId, expectedUpdatedAt: attempt.planUpdatedAt } : undefined);
    setConfirm(plan);
  };
  const run = async () => {
    if (!confirm || !capability?.enabled || pending.current || !live()) return;
    const plan = confirm;
    const key = storageKey(plan.id);
    const previous = retryAttempt ?? storedRun(key);
    if (!previous && (!sameRevision(plan.sourceUpdatedAt, source.updatedAt) || sourceReviewNeeded(plan))) return;
    let request: RunRequest;
    try {
      request = previous ?? { requestId: crypto.randomUUID(), expectedUpdatedAt: plan.updatedAt };
      sessionStorage.setItem(key, JSON.stringify(request));
    } catch { setNotice('Could not retain a retry ID. No request was sent.'); cancelRun(); return; }
    pending.current = true; setBusy('run'); setNotice('');
    try {
      const receipt = await runBatchSync(plan.id, request);
      forgetRun(key);
      if (live()) {
        setHistoryId(plan.id); setHistoryView('receipts'); setReceiptList([receipt]);
        setNotice(`${previous ? 'Committed receipt verified.' : 'Sandbox sync committed.'} Rows affected: ${receipt.rowsAffected}.`);
      }
    } catch (failure) {
      const { status, code } = syncFailure(failure);
      const temporary = Object.prototype.hasOwnProperty.call(temporarySyncFailures, code) ? temporarySyncFailures[code] : undefined;
      const uncertain = code === 'outcome_unknown' || !status || status === 408 || status >= 500 && !temporary;
      if (!temporary && !uncertain && !previous) forgetRun(key);
      if (live()) {
        setHistoryId(plan.id); setHistoryView('attempts');
        setNotice(temporary ? `${temporary} Retry the same request.`
          : uncertain || previous ? `Outcome not verified. Review attempts or retry the same request. ${syncError(failure)}` : syncError(failure));
      }
    } finally { pending.current = false; if (live()) { setBusy(undefined); setRevision(value => value + 1); cancelRun(); } }
  };

  return <section className="ontology-source-catalog ontology-source-sync" aria-label="Batch syncs">
    <header><div><h3>Batch syncs</h3><p>Snapshot Upsert into the local sandbox only, run manually or on an armed daily schedule. No deletes or incremental reads.</p></div></header>
    <div className="ontology-source-sync__body">
      {sourceProof?.state === 'blocked' && <p role="status">Saving syncs, starting new runs and arming schedules are blocked by the saved source identity policy.</p>}
      {capability?.sourceIdentityRequired && !proofCurrent && sourceProof?.state !== 'blocked' && <p role="status">Verify the current saved source identity before new manual or scheduled sync runs.</p>}
      {capability && !capability.enabled && <p role="status">Sandbox sync execution is disabled. Drafts can still be configured.</p>}
      {stateError && <p className="is-error" role="alert">{stateError} <Button size="small" onClick={() => setRevision(value => value + 1)}>Retry</Button></p>}
      {selectedSchedule && plans && !plans.some(plan => plan.id === selectedSchedule) && <p role="status">The linked batch sync is not available under this source.</p>}
      {notice && <p role="status">{notice}</p>}
      {draft ? <form aria-label="Configure batch sync" className="ontology-source-sync__form" onSubmit={event => void save(event)}>
        <h4 ref={composer} tabIndex={-1}>Configure batch sync</h4>
        <p>Source: <code>{draft.schema}.{draft.table}</code>{capability && <> · Destination schema: <code>{capability.targetSchema}</code></>}</p>
        <fieldset disabled={Boolean(busy)}>
          <label>Sync name<Input aria-label="Sync name" required maxLength={120} value={draft.name} onChange={event => change({ name: event.target.value })} /></label>
          <label>Destination table<select value={draft.target} onChange={event => { setTargetColumns(undefined); setDestinationError(''); change({ target: event.target.value, mapping: {} }); }}>
            <option value="">{targets ? 'Select a sandbox table' : 'Loading destination tables…'}</option>
            {draft.target && !targets?.includes(draft.target) && <option value={draft.target} disabled>{draft.target} (saved)</option>}
            {targets?.map(name => <option key={name} value={name}>{name}</option>)}
          </select></label>
          {targets?.length === 0 && <p>No destination tables are available in the local sandbox.</p>}
          <label>Source primary key<select disabled={!sourceKey} value={draft.key} onChange={event => change({ key: event.target.value })}>
            <option value="">Select the source primary key</option>
            {draft.key && draft.key !== sourceKey && <option value={draft.key} disabled>{draft.key} (saved)</option>}
            {sourceKey && <option value={sourceKey}>{sourceKey}</option>}
          </select></label>
          {sourceColumns && !sourceKey && <p>Requires complete source metadata and a single integer or UUID primary key.</p>}
          {targetColumns && targetKeys.length !== 1 && <p>Choose a destination table with one primary key column.</p>}
          {mappingRows.length > 0 && <section className="ontology-source-sync__mapping" aria-label="Column mapping">
            <h4>Column mapping</h4><p>Map every destination column to one same-type source column. The source key must map to the destination primary key.</p>
            {mappingRows.map(({ column, options }) => <label key={column.name}><span><code>{column.name}</code> · {column.type}{column.primary ? ' · Primary key' : ''}</span>
              <select aria-label={`Source column for ${column.name}`} value={draft.mapping[column.name] ?? ''} onChange={event => change({ mapping: { ...draft.mapping, [column.name]: event.target.value } })}>
                <option value="">{options.length ? 'Select source column' : 'No matching source column'}</option>
                {options.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
              </select>
            </label>)}
          </section>}
          {(catalogError || sourceError || destinationError) && <p className="is-error" role="alert">{[catalogError, sourceError, destinationError].filter(Boolean).join(' ')} <Button size="small" onClick={() => setMetadataRevision(value => value + 1)}>Retry metadata</Button></p>}
        </fieldset>
        {saveError && <p role="alert" className="is-error">{saveError}</p>}
        <div className="ontology-source-sync__actions"><Button disabled={Boolean(busy)} onClick={closeDraft}>Cancel</Button><Button type="primary" htmlType="submit" loading={busy === 'save'} disabled={!fullyMapped || !draft.name.trim() || sourceProof?.state === 'blocked'}>Save draft</Button></div>
      </form> : !plans ? !stateError && <p role="status">Loading batch syncs…</p> : plans.length ? <ul className="ontology-source-sync__items">{plans.map(plan => <li key={plan.id}>
        <div><strong>{plan.name}</strong> · Draft</div>
        <div className="ontology-source-sync__route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{capability?.targetSchema}.{plan.targetTable}</code></div>
        {capability?.sourceIdentityRequired && proofCurrent && plansForProof === proofVersion && !plan.sourceIdentityReviewed && <p>Requires review. Review and save this sync again to bind it to the verified source identity.</p>}
        {!sameRevision(plan.sourceUpdatedAt, source.updatedAt) && <p>This source has changed. Review and save the mapping before running.</p>}
        <div className="ontology-source-sync__actions">
          <Button size="small" disabled={Boolean(busy)} onClick={() => edit(plan)}>Edit sync</Button>
          <Button size="small" aria-expanded={scheduleId === plan.id} onClick={() => setScheduleId(value => value === plan.id ? undefined : plan.id)}>Schedule</Button>
          <Button size="small" aria-expanded={historyId === plan.id} onClick={() => { setReceiptList(undefined); setAttemptList(undefined); setHistoryError(''); setHistoryView('receipts'); setHistoryId(value => value === plan.id ? '' : plan.id); }}>Sync history</Button>
          {capability?.enabled && (sameRevision(plan.sourceUpdatedAt, source.updatedAt) && !sourceReviewNeeded(plan) || storedRun(storageKey(plan.id))) &&
            <Button size="small" type="primary" disabled={Boolean(busy)} onClick={() => askRun(plan)}>{storedRun(storageKey(plan.id)) ? 'Retry same sync request' : 'Run sandbox sync'}</Button>}
        </div>
        {scheduleId === plan.id && capability && <BatchSyncSchedulePanel key={`${plan.id}:${plan.updatedAt}:${source.updatedAt}:${sourceReviewNeeded(plan)}`} plan={plan} source={source} capability={capability} sourceReviewNeeded={sourceReviewNeeded(plan)} />}
        {historyId === plan.id && <section aria-label="Batch sync history">
          <div className="ontology-source-sync__actions" role="group" aria-label="History view">
            <Button size="small" aria-pressed={historyView === 'receipts'} onClick={() => { if (historyView !== 'receipts') { setReceiptList(undefined); setHistoryError(''); setHistoryView('receipts'); } }}>Committed receipts</Button>
            <Button size="small" aria-pressed={historyView === 'attempts'} onClick={() => { if (historyView !== 'attempts') { setAttemptList(undefined); setHistoryError(''); setHistoryView('attempts'); } }}>Attempts</Button>
          </div>
          {historyView === 'attempts' && <p>Attempts record execution requests and outcomes. A recorded request is not proof that a worker is active or that any rows committed.</p>}
          {historyError ? <p role="alert" className="is-error">{historyError} <Button size="small" onClick={() => setRevision(value => value + 1)}>Retry history</Button></p>
            : historyView === 'attempts'
              ? attemptList ? <AttemptList attempts={attemptList.filter(attempt => attempt.planId === plan.id)} canRetry={Boolean(capability?.enabled && !busy)} onRetry={attempt => askRun(plan, attempt)} /> : <p role="status">Loading attempts…</p>
              : receiptList ? <ReceiptList receipts={receiptList.filter(receipt => receipt.planId === plan.id)} /> : <p role="status">Loading sync history…</p>}
        </section>}
      </li>)}</ul> : <p>No saved batch syncs. Select a table in Explore source to set one up.</p>}
    </div>
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={retry ? 'Retry previous sandbox sync?' : 'Run sandbox batch sync?'}
      onCancel={cancelRun} onOk={run} confirmLoading={busy === 'run'} okText={retry ? 'Retry same request' : 'Confirm sandbox sync'} okButtonProps={{ disabled: Boolean(confirm && !retry && sourceReviewNeeded(confirm)) }}>
      {confirm && (retry ? <>
        <p>This uses the original request ID and saved revision. A committed sync is not applied again.</p>
        <p>Request ID: <code>{retry.requestId}</code></p>
        {retry.expectedUpdatedAt !== confirm.updatedAt && <p>The plan has changed. This request can recover an existing receipt, but cannot run a different revision.</p>}
      </> : <>
        <p>Upsert a snapshot of the saved source into the local sandbox, limited to {capability?.maxRows} rows and {capability?.maxBytes.toLocaleString()} bytes. This does not delete target rows or change the external source.</p>
        <div className="ontology-source-sync__route"><code>{confirm.sourceSchema}.{confirm.sourceTable}</code><ArrowRightOutlined /><code>{capability?.targetSchema}.{confirm.targetTable}</code></div>
        <p>Source key: <code>{confirm.sourceKeyColumn}</code>. This confirmation applies only to the current saved revision.</p>
      </>)}
    </Modal>
  </section>;
}

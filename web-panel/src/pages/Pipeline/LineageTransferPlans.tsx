import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Modal, NativeSelect } from '@/components/ui';
import { ArrowRightOutlined, PlusOutlined } from '@/components/ui/icons';
import { checkTransferPlan, fetchEtlObjectTypes, fetchEtlTableColumns, fetchTransferPlans, fetchTransferRunAttempts, fetchTransferRunCapabilities, fetchTransferRunHistory, fetchTransferSchedule, fetchTransferScheduleJobs, runSandboxTransfer, saveTransferPlan, updateTransferSchedule } from '../../services/api';
import type { TransferPlan, TransferPlanInput, TransferPlanPreflight, TransferRunAttempt, TransferRunCapabilities, TransferRunReceipt, TransferSchedule, TransferScheduleJob } from '../../services/api';
import { isUtcMonthDay, isoWeekdayName, isoWeekdaysUtc } from '../../services/scheduleTime';
import { getSessionState } from '../../services/session';
import LineageUpstreamTrigger from './LineageUpstreamTrigger';

function errorMessage(error: unknown) {
  const response = (error as { response?: { data?: { detail?: unknown; errors?: unknown; message?: unknown } } })?.response?.data;
  const detail = response?.detail ?? response?.errors ?? response?.message;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).join(' ') || 'Check the plan settings and try again.';
  }
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    const issues = 'issues' in detail && Array.isArray(detail.issues) ? detail.issues : [];
    return [detail.message, ...issues.flatMap(issue => typeof issue?.message === 'string'
      ? [typeof issue.column === 'string' ? `${issue.column}: ${issue.message}` : issue.message] : [])].join(' ');
  }
  return 'The request failed. Try again.';
}

const emptyDraft = (schema: string): TransferPlanInput => ({
  name: '', sourceSchema: schema, sourceTable: '', targetSchema: '', targetTable: '',
  writeMode: 'append', trigger: 'manual', scheduleTime: null,
});

function validateDraft(draft: TransferPlanInput, weeklyAvailable: boolean, monthlyAvailable: boolean) {
  if (!draft.name.trim() || !draft.sourceTable || !draft.targetTable) return 'Enter a name and select both tables.';
  if (draft.sourceSchema === draft.targetSchema && draft.sourceTable === draft.targetTable) return 'The source and destination must be different tables.';
  if (draft.trigger === 'scheduled' && !draft.scheduleTime) return 'Enter the daily time in UTC.';
  if (draft.trigger === 'weekly') {
    if (!weeklyAvailable) return 'Weekly scheduling is unavailable in this environment.';
    if (draft.writeMode !== 'upsert' || draft.sourceSchema !== 'ts_transfer_sandbox' || draft.targetSchema !== 'ts_transfer_sandbox')
      return 'Weekly UTC requires Upsert between two transfer sandbox tables.';
    if (!isoWeekdayName(draft.scheduleWeekdayUtc) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.scheduleTime ?? ''))
      return 'Select a weekday and enter the weekly time in UTC.';
  }
  if (draft.trigger === 'monthly') {
    if (!monthlyAvailable) return 'Monthly scheduling is unavailable in this environment.';
    if (draft.writeMode !== 'upsert' || draft.sourceSchema !== 'ts_transfer_sandbox' || draft.targetSchema !== 'ts_transfer_sandbox')
      return 'Monthly UTC requires Upsert between two transfer sandbox tables.';
    if (!isUtcMonthDay(draft.scheduleDayOfMonthUtc) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.scheduleTime ?? ''))
      return 'Select a day from 1 through 28 and enter the monthly time in UTC.';
  }
  if (draft.trigger === 'upstream' && (draft.writeMode !== 'upsert' || draft.sourceSchema !== 'ts_transfer_sandbox' || draft.targetSchema !== 'ts_transfer_sandbox')) {
    return 'On upstream commit requires Upsert between two transfer sandbox tables.';
  }
  return '';
}

type PendingRun = { requestId: string; expectedUpdatedAt: string };

function savedRun(key: string): PendingRun | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as PendingRun | null;
    if (value && typeof value.requestId === 'string' && typeof value.expectedUpdatedAt === 'string') return value;
  } catch { /* Storage can be unavailable. A new run then fails before sending. */ }
}

function clearSavedRun(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* A retained request ID can only replay the same run. */ }
}

function TransferReceipts({ receipts }: { receipts: TransferRunReceipt[] }) {
  if (!receipts.length) return <p>No committed sandbox runs recorded.</p>;
  return <ol className="flow-transfer-receipts">{receipts.map(receipt => <li key={receipt.id}>
    <div><strong>Committed</strong> · {receipt.writeMode === 'upsert' ? 'Upsert' : 'Append'}</div>
    <div className="flow-transfer-route"><code>{receipt.sourceSchema}.{receipt.sourceTable}</code><ArrowRightOutlined /><code>{receipt.targetSchema}.{receipt.targetTable}</code></div>
    <div>{receipt.rowsAffected === 0 ? 'No new rows · ' : ''}Rows affected: {receipt.rowsAffected}</div>
    {receipt.cursorThrough != null && <div>Checkpoint through: <code>{receipt.cursorThrough}</code></div>}
    {receipt.hasMore === true && <div>More rows were available. Start another run to continue.</div>}
    <div>Recorded at: <time dateTime={receipt.recordedAt}>{new Date(receipt.recordedAt).toLocaleString()}</time></div>
    <div>Receipt: <code>{receipt.id}</code></div>
  </li>)}</ol>;
}

const attemptLabels: Record<TransferRunAttempt['status'], string> = {
  started: 'Request recorded',
  committed: 'Commit recorded',
  replayed: 'Existing receipt returned',
  rejected: 'Rejected',
  outcome_unknown: 'Outcome unknown',
};

function TransferAttempts({ attempts }: { attempts: TransferRunAttempt[] }) {
  if (!attempts.length) return <p>No sandbox attempts recorded.</p>;
  return <ol className="flow-transfer-receipts">{attempts.map(attempt => <li key={attempt.id}>
    <strong>{attemptLabels[attempt.status]}</strong>
    <div>Requested at: <time dateTime={attempt.startedAt}>{new Date(attempt.startedAt).toLocaleString()}</time></div>
    {attempt.finishedAt && <div>Finished at: <time dateTime={attempt.finishedAt}>{new Date(attempt.finishedAt).toLocaleString()}</time></div>}
    {attempt.receiptId && <div>Receipt: <code>{attempt.receiptId}</code></div>}
    {attempt.upstreamJobId && <div>Upstream job: <code>{attempt.upstreamJobId}</code></div>}
    {attempt.message && <div>{attempt.message}</div>}
  </li>)}</ol>;
}

const scheduleJobLabels: Record<TransferScheduleJob['state'], string> = {
  pending: 'Pending dispatch',
  dispatched: 'Dispatched',
  committed: 'Committed',
  rejected: 'Rejected',
  outcome_unknown: 'Outcome unknown',
  expired: 'Expired',
  canceled: 'Canceled',
};

function UtcTime({ value }: { value: string }) {
  return <time dateTime={value}>{new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time>;
}

function TransferSchedulePanel({ plan, maxRows }: { plan: TransferPlan; maxRows: number }) {
  const [schedule, setSchedule] = useState<TransferSchedule>();
  const [jobs, setJobs] = useState<TransferScheduleJob[]>();
  const [scheduleError, setScheduleError] = useState('');
  const [jobsError, setJobsError] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [revision, setRevision] = useState(0);
  const [confirm, setConfirm] = useState<'arm' | 'pause'>();
  const [submitting, setSubmitting] = useState(false);
  const requestPending = useRef(false);
  const active = useRef(false);
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => {
    const read = new AbortController();
    setSchedule(undefined);
    setJobs(undefined);
    setScheduleError('');
    setJobsError('');
    void fetchTransferSchedule(plan.id, read.signal).then(result => {
      if (!read.signal.aborted && current()) setSchedule(result);
    }).catch(failure => { if (!read.signal.aborted && current()) setScheduleError(errorMessage(failure)); });
    void fetchTransferScheduleJobs(plan.id, read.signal).then(result => {
      if (!read.signal.aborted && current()) setJobs(result);
    }).catch(failure => { if (!read.signal.aborted && current()) setJobsError(errorMessage(failure)); });
    return () => read.abort();
  }, [plan.id, plan.updatedAt, revision, identity]);

  const draftWeekly = plan.trigger === 'weekly';
  const draftMonthly = plan.trigger === 'monthly';
  const planWeekday = isoWeekdayName(plan.scheduleWeekdayUtc);
  const draftArmReady = (!draftWeekly || Boolean(planWeekday && plan.scheduleTime)) &&
    (!draftMonthly || Boolean(isUtcMonthDay(plan.scheduleDayOfMonthUtc) && plan.scheduleTime));
  const submit = async () => {
    if (!confirm || !schedule || requestPending.current || !current()) return;
    const enabled = confirm === 'arm';
    if (enabled ? !schedule.available || schedule.enabled || !draftArmReady : !schedule.enabled) return;
    requestPending.current = true;
    setSubmitting(true);
    setMutationError('');
    try {
      const updated = await updateTransferSchedule(plan.id, { enabled, expectedUpdatedAt: plan.updatedAt });
      if (current()) setSchedule(updated);
    } catch (failure) {
      if (current()) setMutationError(`${errorMessage(failure)} Refresh to verify the registered schedule before retrying.`);
    } finally {
      requestPending.current = false;
      if (current()) {
        setConfirm(undefined);
        setSubmitting(false);
        setRevision(value => value + 1);
      }
    }
  };
  const plannedTime = schedule?.plannedTimeUtc || plan.scheduleTime;
  const weekday = schedule?.plannedTimeUtc ? isoWeekdayName(schedule.plannedWeekdayUtc) : planWeekday;
  const weekly = schedule?.plannedTimeUtc ? Boolean(weekday) : draftWeekly;
  const monthDay = schedule?.plannedTimeUtc ? schedule.plannedDayOfMonthUtc : plan.scheduleDayOfMonthUtc;
  const monthly = schedule?.plannedTimeUtc ? isUtcMonthDay(monthDay) : draftMonthly;
  const armedCadenceBlocked = (weekly || monthly) && schedule?.enabled && !schedule.available;
  const scheduleClock = monthly ? isUtcMonthDay(monthDay) && plannedTime ? `Monthly on day ${monthDay} at ${plannedTime} UTC` : undefined : weekly ? weekday && plannedTime ? `Weekly on ${weekday} at ${plannedTime} UTC` : undefined :
    plannedTime ? `Daily at ${plannedTime} UTC` : undefined;

  return <section className="flow-transfer-schedule" aria-label="Transfer schedule">
    <div className="flow-transfer-heading">
      <h4>Transfer schedule</h4>
      <button type="button" className="flow-lineage-action" disabled={submitting} onClick={() => { setMutationError(''); setRevision(value => value + 1); }}>Refresh schedule</button>
    </div>
    <p>{scheduleClock ? `${scheduleClock}.` : monthly ? 'No monthly UTC time is configured.' : weekly ? 'No weekly UTC time is configured.' : 'No daily UTC time is configured.'} This is a sandbox schedule, not a production or CDC job.</p>
    {mutationError && <div role="alert" className="flow-transfer-error">{mutationError}</div>}
    {scheduleError ? <div role="alert" className="flow-transfer-error">{scheduleError}</div> : !schedule ? <p role="status">Loading schedule…</p> : <>
      <strong role="status">{armedCadenceBlocked ? 'Schedule armed · blocked' : schedule.enabled ? 'Schedule armed' : schedule.generation != null ? 'Schedule paused' : 'Schedule not armed'}</strong>
      {!schedule.available && <p>Scheduled execution is unavailable for this plan or environment.</p>}
      <div className="flow-transfer-run-actions">
        {schedule.enabled
          ? <button type="button" className="flow-lineage-action" disabled={submitting} onClick={() => { setMutationError(''); setConfirm('pause'); }}>Pause schedule</button>
          : schedule.available && draftArmReady && <button type="button" className="flow-lineage-action is-primary" disabled={submitting} onClick={() => { setMutationError(''); setConfirm('arm'); }}>Arm schedule</button>}
      </div>
    </>}
    <h4>Schedule jobs</h4>
    <p>Dispatch means a job was sent to Automation. Only a committed job with a receipt confirms a transfer.</p>
    {jobsError ? <div role="alert" className="flow-transfer-error">{jobsError}</div> : !jobs ? <p role="status">Loading schedule jobs…</p>
      : !jobs.length ? <p>No schedule jobs recorded.</p> : <ol className="flow-transfer-receipts">
        {jobs.map(job => <li key={job.id}>
          <strong>{scheduleJobLabels[job.state]}</strong>
          {isoWeekdayName(job.scheduleWeekdayUtc) && <div>Recorded mode: Weekly UTC · {isoWeekdayName(job.scheduleWeekdayUtc)}</div>}
          {isUtcMonthDay(job.scheduleDayOfMonthUtc) && <div>Recorded mode: Monthly UTC · day {job.scheduleDayOfMonthUtc}</div>}
          <div>Scheduled slot: <UtcTime value={job.slot} /></div>
          {job.dispatchedAt && <div>Dispatched at: <UtcTime value={job.dispatchedAt} /></div>}
          {job.state === 'canceled' ? job.canceledAt && <div>Canceled at: <UtcTime value={job.canceledAt} /></div>
            : job.finishedAt && <div>Finished at: <UtcTime value={job.finishedAt} /></div>}
          {job.receiptId && <div>Receipt: <code>{job.receiptId}</code></div>}
          {job.message && <div>{job.message}</div>}
        </li>)}
      </ol>}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm === 'pause' ? 'Pause sandbox schedule?' : draftMonthly ? 'Arm monthly sandbox schedule?' : draftWeekly ? 'Arm weekly sandbox schedule?' : 'Arm sandbox schedule?'}
      onCancel={() => setConfirm(undefined)} onOk={submit} confirmLoading={submitting} okText={confirm === 'pause' ? 'Confirm pause' : 'Confirm arm schedule'}>
      {confirm === 'pause' ? <p>This stops future dispatches. It does not reverse a committed transfer or cancel a transfer that has already started.</p>
        : <><p>Allow Automation to run this saved Upsert revision {draftMonthly ? `monthly on day ${plan.scheduleDayOfMonthUtc} at ${plan.scheduleTime} UTC` : draftWeekly ? `weekly on ${planWeekday} at ${plan.scheduleTime} UTC` : `daily at ${plan.scheduleTime} UTC`}. A run can write up to {maxRows} rows into the destination sandbox table.</p>
          <div className="flow-transfer-route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{plan.targetSchema}.{plan.targetTable}</code></div></>}
    </Modal>
  </section>;
}

function IncrementalCursorPicker({ schema, table, cursor, onChange }: {
  schema: string; table: string; cursor?: string | null; onChange: (column: string | null) => void;
}) {
  const [candidate, setCandidate] = useState<string | null>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!table) return;
    const request = new AbortController();
    setCandidate(undefined);
    setError('');
    void fetchEtlTableColumns(schema, table, request.signal).then(columns => {
      if (request.signal.aborted) return;
      const key = columns.filter(column => column.primary);
      setCandidate(key.length === 1 && /^(smallint|integer|bigint)$/.test(key[0].type) ? key[0].name : null);
    }).catch(failure => { if (!request.signal.aborted) setError(errorMessage(failure)); });
    return () => request.abort();
  }, [schema, table, revision]);

  return <div className="flow-transfer-cursor">
    <label>Source selection<NativeSelect value={cursor ?? ''} onChange={event => onChange(event.target.value || null)}>
      <option value="">One-time append</option>
      {cursor && cursor !== candidate && <option value={cursor} disabled>Incremental by {cursor} (saved)</option>}
      {candidate && <option value={candidate}>Incremental by {candidate}</option>}
    </NativeSelect></label>
    {error ? <div role="alert" className="flow-transfer-error">{error} <button type="button" className="flow-lineage-action" onClick={() => setRevision(value => value + 1)}>Retry key lookup</button></div>
      : <p>{!table ? 'Choose a source table to check for an incremental key.' : candidate === undefined ? 'Checking the source primary key…' : candidate === null ? 'Incremental append requires a single integer source primary key.' : null}</p>}
    {cursor && <p>New keys must always be higher than all previously committed source keys. Rows inserted late at or below the checkpoint are not copied; updates and deletions of processed rows are not tracked. This does not backfill a completed one-time append.</p>}
  </div>;
}

function TablePicker({ label, schema, table, setTable }: {
  label: string; schema: string; table: string; setTable: (table: string) => void;
}) {
  const [options, setOptions] = useState<string[]>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!schema) return;
    const request = new AbortController();
    setOptions(undefined);
    setError('');
    const load = async () => {
      const names: string[] = [];
      let offset = 0;
      while (!request.signal.aborted) {
        const page = await fetchEtlObjectTypes({ schema, limit: 500, offset }, request.signal);
        names.push(...page.items.map(asset => asset.table));
        if (!page.items.length || offset + page.items.length >= page.total) break;
        offset += page.items.length;
      }
      if (!request.signal.aborted) setOptions(names);
    };
    void load().catch(failure => { if (!request.signal.aborted) setError(errorMessage(failure)); });
    return () => { request.abort(); };
  }, [schema, revision]);

  return <div className="flow-transfer-picker">
    <label>{label}<NativeSelect required disabled={!options?.length} value={table} onChange={event => setTable(event.target.value)}>
      <option value="">{!schema ? 'Choose a schema first' : error ? 'Tables unavailable' : !options ? 'Loading tables…' : !options.length ? 'No visible tables' : 'Select a table'}</option>
      {table && !options?.includes(table) && <option value={table} disabled>{table}{options ? ' (unavailable)' : ''}</option>}
      {options?.map(option => <option value={option} key={option}>{option}</option>)}
    </NativeSelect></label>
    {error && <div role="alert">{error} <button className="flow-lineage-action" type="button" onClick={() => setRevision(value => value + 1)}>Retry {label.toLowerCase()} lookup</button></div>}
  </div>;
}

export default function LineageTransferPlans({ schema, schemas, selectedSchedule, selectedUpstream }: { schema: string; schemas: string[]; selectedSchedule?: string; selectedUpstream?: boolean }) {
  const titleId = useId();
  const [plans, setPlans] = useState<TransferPlan[]>();
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const [draft, setDraft] = useState<TransferPlanInput>();
  const [editingId, setEditingId] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [preflight, setPreflight] = useState<TransferPlanPreflight>();
  const [preflightError, setPreflightError] = useState('');
  const [capabilities, setCapabilities] = useState<TransferRunCapabilities>();
  const [capabilityError, setCapabilityError] = useState('');
  const [confirmPlan, setConfirmPlan] = useState<TransferPlan>();
  const [runningPlan, setRunningPlan] = useState<string>();
  const [scheduleFor, setScheduleFor] = useState<string | undefined>(selectedSchedule);
  const [runError, setRunError] = useState<{ planId: string; message: string }>();
  const [runReceipt, setRunReceipt] = useState<TransferRunReceipt>();
  const [historyFor, setHistoryFor] = useState<string>();
  const [history, setHistory] = useState<TransferRunReceipt[]>();
  const [historyView, setHistoryView] = useState<'receipts' | 'attempts'>('receipts');
  const [attempts, setAttempts] = useState<TransferRunAttempt[]>();
  const [historyError, setHistoryError] = useState('');
  const [historyRevision, setHistoryRevision] = useState(0);
  const active = useRef(false);
  const pending = useRef(false);
  const runPending = useRef(false);
  const preflightRequest = useRef<AbortController>();
  const sessionIdentity = useRef(getSessionState().identity).current;
  const isCurrent = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && session.identity === sessionIdentity;
  };

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      preflightRequest.current?.abort();
    };
  }, [schema]);
  useEffect(() => {
    const request = new AbortController();
    setPlans(undefined);
    setLoadError('');
    fetchTransferPlans(schema, request.signal).then(items => {
      if (!request.signal.aborted && isCurrent()) setPlans(items);
    }).catch(error => {
      if (!request.signal.aborted && isCurrent()) setLoadError(errorMessage(error));
    });
    return () => { request.abort(); };
  }, [revision, schema, sessionIdentity]);
  useEffect(() => {
    const request = new AbortController();
    fetchTransferRunCapabilities(request.signal).then(result => {
      if (!request.signal.aborted && isCurrent()) setCapabilities(result);
    }).catch(error => {
      if (!request.signal.aborted && isCurrent()) setCapabilityError(errorMessage(error));
    });
    return () => request.abort();
  }, [schema, sessionIdentity]);
  useEffect(() => { setScheduleFor(selectedSchedule); }, [selectedSchedule]);
  useEffect(() => {
    if (!historyFor) return;
    const request = new AbortController();
    setHistoryError('');
    const onFailure = (error: unknown) => { if (!request.signal.aborted && isCurrent()) setHistoryError(errorMessage(error)); };
    if (historyView === 'attempts') {
      setAttempts(undefined);
      void fetchTransferRunAttempts(historyFor, request.signal).then(result => {
        if (!request.signal.aborted && isCurrent()) setAttempts(result);
      }).catch(onFailure);
    } else {
      setHistory(undefined);
      void fetchTransferRunHistory(historyFor, request.signal).then(result => {
        if (!request.signal.aborted && isCurrent()) setHistory(result);
      }).catch(onFailure);
    }
    return () => request.abort();
  }, [historyFor, historyRevision, historyView, sessionIdentity]);

  const storageKey = (planId: string) => `ontology-transfer-run:${sessionIdentity}:${planId}`;
  const eligible = (plan: TransferPlan) => capabilities?.enabled && plan.trigger === 'manual' &&
    plan.sourceSchema === capabilities.sourceSchema && plan.targetSchema === capabilities.targetSchema;
  const confirmRun = async () => {
    if (!confirmPlan || runPending.current || !isCurrent() || !capabilities?.enabled) return;
    const plan = confirmPlan;
    const key = storageKey(plan.id);
    const previous = savedRun(key);
    if (!previous && !eligible(plan)) return;
    let request: PendingRun;
    setConfirmPlan(undefined);
    setRunError(undefined);
    setRunReceipt(undefined);
    try {
      request = previous ?? { requestId: crypto.randomUUID(), expectedUpdatedAt: plan.updatedAt };
      sessionStorage.setItem(key, JSON.stringify(request));
    } catch {
      setRunError({ planId: plan.id, message: 'Cannot retain a retry ID in this browser. No sandbox run was sent.' });
      return;
    }
    runPending.current = true;
    setRunningPlan(plan.id);
    try {
      const receipt = await runSandboxTransfer(plan.id, request);
      clearSavedRun(key);
      if (isCurrent()) {
        setRunReceipt(receipt);
      }
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      const unknownOutcome = !status || status === 408 || status >= 500;
      // Never discard an already-uncertain request on a later retry error.
      if (!unknownOutcome && !previous) clearSavedRun(key);
      if (isCurrent()) {
        const detail = errorMessage(error);
        setRunError({ planId: plan.id, message: unknownOutcome || previous
          ? `Run outcome not verified. Check history or retry the same request. ${detail}`
          : detail });
      }
    } finally {
      runPending.current = false;
      if (isCurrent()) {
        setRunningPlan(undefined);
        setHistoryRevision(value => value + 1);
      }
    }
  };

  const resetPreflight = () => {
    preflightRequest.current?.abort();
    preflightRequest.current = undefined;
    setChecking(false);
    setPreflight(undefined);
    setPreflightError('');
  };
  const closeDraft = () => {
    resetPreflight();
    setDraft(undefined);
  };
  const change = (patch: Partial<TransferPlanInput>) => {
    resetPreflight();
    setSaveError('');
    setDraft(current => current && { ...current, ...patch });
  };
  const edit = (plan?: TransferPlan) => {
    resetPreflight();
    setNotice('');
    setSaveError('');
    setEditingId(plan?.id);
    setDraft(plan ? {
      name: plan.name, sourceSchema: plan.sourceSchema, sourceTable: plan.sourceTable,
      targetSchema: plan.targetSchema, targetTable: plan.targetTable, writeMode: plan.writeMode,
      incrementalCursor: plan.incrementalCursor,
      trigger: plan.trigger, scheduleTime: plan.scheduleTime,
      scheduleWeekdayUtc: plan.trigger === 'weekly' ? plan.scheduleWeekdayUtc : undefined,
      scheduleDayOfMonthUtc: plan.trigger === 'monthly' ? plan.scheduleDayOfMonthUtc : undefined,
    } : emptyDraft(schema));
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft || pending.current || !isCurrent()) return;
    const validation = validateDraft(draft, capabilities?.weeklyUpsert === true, capabilities?.monthlyUpsert === true);
    if (validation) {
      setSaveError(validation);
      return;
    }
    setSaveError('');
    pending.current = true;
    setSaving(true);
    try {
      const plan = await saveTransferPlan({ ...draft, name: draft.name.trim() }, editingId);
      if (!isCurrent()) return;
      const matches = plan.sourceSchema === schema || plan.targetSchema === schema;
      closeDraft();
      setNotice(matches ? 'Draft saved. No data was transferred.' : 'Draft saved under the new source and destination schemas. No data was transferred.');
      setRevision(value => value + 1);
    } catch (error) {
      if (isCurrent()) setSaveError(errorMessage(error));
    } finally {
      pending.current = false;
      if (isCurrent()) setSaving(false);
    }
  };
  const checkCompatibility = async () => {
    if (!draft || preflightRequest.current || pending.current || !isCurrent()) return;
    resetPreflight();
    const validation = validateDraft(draft, capabilities?.weeklyUpsert === true, capabilities?.monthlyUpsert === true);
    if (validation) {
      setPreflightError(validation);
      return;
    }
    const request = new AbortController();
    preflightRequest.current = request;
    setChecking(true);
    try {
      const result = await checkTransferPlan({ ...draft, name: draft.name.trim() }, request.signal);
      if (!request.signal.aborted && isCurrent()) setPreflight(result);
    } catch (error) {
      if (!request.signal.aborted && isCurrent()) setPreflightError(errorMessage(error));
    } finally {
      if (preflightRequest.current === request) {
        preflightRequest.current = undefined;
        if (isCurrent()) setChecking(false);
      }
    }
  };
  const schemaOptions = (selected: string) => [...new Set([...schemas, ...(selected ? [selected] : [])])].sort()
    .map(item => <option key={item} value={item}>{item}</option>);
  const retrying = confirmPlan ? savedRun(storageKey(confirmPlan.id)) : undefined;
  const cursorEligible = draft && capabilities?.incrementalAppend && draft.writeMode === 'append' &&
    draft.trigger === 'manual' && draft.sourceSchema === capabilities.sourceSchema && draft.targetSchema === capabilities.targetSchema;
  const scheduleEligible = (plan: TransferPlanInput) => capabilities && (plan.trigger === 'scheduled' || plan.trigger === 'weekly' || plan.trigger === 'monthly' || plan.trigger === 'upstream') && plan.writeMode === 'upsert' &&
    plan.sourceSchema === capabilities.sourceSchema && plan.targetSchema === capabilities.targetSchema;
  const canSelectUpstream = draft && capabilities && draft.writeMode === 'upsert' &&
    draft.sourceSchema === capabilities.sourceSchema && draft.targetSchema === capabilities.targetSchema;

  return <section className="flow-transfer" aria-labelledby={titleId}>
    <div className="flow-transfer-heading"><h3 id={titleId}>Transfer plans</h3>{!draft && <button type="button" className="flow-lineage-action is-primary" disabled={Boolean(runningPlan)} onClick={() => edit()}><PlusOutlined /> New plan</button>}</div>
    <p>Plan transfers between internal PostgreSQL tables. Saving a draft does not run it, schedule jobs, or change the lineage graph.</p>
    {!draft && capabilityError && <p role="alert" className="flow-transfer-error">Could not check whether sandbox runs are available. {capabilityError}</p>}
    {!draft && selectedSchedule && plans && !plans.some(plan => plan.id === selectedSchedule) && <p role="status">The linked transfer plan is not in this schema.</p>}
    {notice && <p role="status" className="flow-transfer-notice">{notice}</p>}
    {draft ? <form onSubmit={submit} aria-label={editingId ? 'Edit transfer draft' : 'New transfer draft'}>
      <fieldset disabled={saving}>
        <legend>{editingId ? 'Edit draft' : 'New draft'}</legend>
        <label>Plan name<input autoFocus required maxLength={120} value={draft.name} onChange={event => change({ name: event.target.value })} /></label>
        <label>Source schema<NativeSelect required value={draft.sourceSchema} onChange={event => change({ sourceSchema: event.target.value, sourceTable: '', incrementalCursor: null })}><option value="">Select a schema</option>{schemaOptions(draft.sourceSchema)}</NativeSelect></label>
        <TablePicker key={`source:${draft.sourceSchema}`} label="Source table" schema={draft.sourceSchema} table={draft.sourceTable} setTable={sourceTable => change({ sourceTable, incrementalCursor: null })} />
        <label>Destination schema<NativeSelect required value={draft.targetSchema} onChange={event => change({ targetSchema: event.target.value, targetTable: '', incrementalCursor: null })}><option value="">Select a schema</option>{schemaOptions(draft.targetSchema)}</NativeSelect></label>
        <TablePicker key={`target:${draft.targetSchema}`} label="Destination table" schema={draft.targetSchema} table={draft.targetTable} setTable={targetTable => change({ targetTable, incrementalCursor: null })} />
        <label>Write mode<NativeSelect value={draft.writeMode} onChange={event => change({ writeMode: event.target.value === 'upsert' ? 'upsert' : 'append', incrementalCursor: null })}><option value="append">Append</option><option value="upsert">Upsert by destination primary key</option></NativeSelect></label>
        <label>Intended trigger<NativeSelect value={draft.trigger} onChange={event => {
          const trigger = event.target.value;
          change({ trigger: trigger === 'scheduled' || trigger === 'upstream' || trigger === 'weekly' || trigger === 'monthly' ? trigger : 'manual', scheduleTime: null, scheduleWeekdayUtc: undefined, scheduleDayOfMonthUtc: undefined, incrementalCursor: null });
        }}><option value="manual">Manual</option><option value="scheduled">Daily</option>
          {(capabilities?.weeklyUpsert === true || draft.trigger === 'weekly') &&
            <option value="weekly" disabled={!capabilities?.weeklyUpsert || !canSelectUpstream}>Weekly UTC</option>}
          {(capabilities?.monthlyUpsert === true || draft.trigger === 'monthly') &&
            <option value="monthly" disabled={!capabilities?.monthlyUpsert || !canSelectUpstream}>Monthly UTC</option>}
          <option value="upstream" disabled={!canSelectUpstream}>On upstream commit</option></NativeSelect></label>
        {draft.trigger === 'scheduled' && <label>Daily time (UTC)<input type="time" required step={60} value={draft.scheduleTime ?? ''} onChange={event => change({ scheduleTime: event.target.value || null })} /></label>}
        {draft.trigger === 'weekly' && <>
          <label>Weekday (UTC)<NativeSelect required value={draft.scheduleWeekdayUtc ?? ''} disabled={!capabilities?.weeklyUpsert}
            onChange={event => change({ scheduleWeekdayUtc: event.target.value ? Number(event.target.value) : undefined })}>
            <option value="">Select a weekday</option>{isoWeekdaysUtc.map((day, index) => <option key={day} value={index + 1}>{day}</option>)}
          </NativeSelect></label>
          <label>Weekly time (UTC)<input type="time" required step={60} value={draft.scheduleTime ?? ''} disabled={!capabilities?.weeklyUpsert}
            onChange={event => change({ scheduleTime: event.target.value || null })} /></label>
          <p>{capabilities?.weeklyUpsert ? 'Saving this draft does not arm the schedule. Review and arm the saved revision separately.' :
            'Weekly scheduling is unavailable. Existing schedule history remains in the saved plan.'}</p>
        </>}
        {draft.trigger === 'monthly' && <>
          <label>Day of month (UTC)<NativeSelect required value={draft.scheduleDayOfMonthUtc ?? ''} disabled={!capabilities?.monthlyUpsert}
            onChange={event => change({ scheduleDayOfMonthUtc: event.target.value ? Number(event.target.value) : undefined })}>
            <option value="">Select a day</option>{Array.from({ length: 28 }, (_, index) => index + 1).map(day =>
              <option key={day} value={day}>{day}</option>)}
          </NativeSelect></label>
          <label>Monthly time (UTC)<input type="time" required step={60} value={draft.scheduleTime ?? ''} disabled={!capabilities?.monthlyUpsert}
            onChange={event => change({ scheduleTime: event.target.value || null })} /></label>
          <p>{capabilities?.monthlyUpsert ? 'Choose day 1 through 28. Saving this draft does not arm the schedule. Review and arm the saved revision separately.' :
            'Monthly scheduling is unavailable. Existing schedule history remains in the saved plan.'}</p>
        </>}
        {draft.trigger === 'upstream' && <p>Save this draft, then open Schedule to select an eligible manual producer. {capabilities && !capabilities.upstreamUpsert && 'Upstream execution is disabled in this environment.'}</p>}
        {cursorEligible && <IncrementalCursorPicker key={`${draft.sourceSchema}:${draft.sourceTable}`} schema={draft.sourceSchema} table={draft.sourceTable} cursor={draft.incrementalCursor} onChange={incrementalCursor => change({ incrementalCursor })} />}
      </fieldset>
      <section className="flow-transfer-preflight" aria-label="Compatibility check">
        <div className="flow-transfer-heading">
          <h4>Compatibility</h4>
          <button type="button" className="flow-lineage-action" disabled={checking || saving} onClick={() => void checkCompatibility()}>{checking ? 'Checking…' : 'Check compatibility'}</button>
        </div>
        <p>Checks PostgreSQL catalog metadata only. No rows are scanned or moved. This does not guarantee a transfer will succeed.</p>
        {checking && <div role="status">Checking table compatibility…</div>}
        {preflightError && <div role="alert" className="flow-transfer-error">{preflightError}</div>}
        {preflight && <div role="status" className="flow-transfer-preflight-result">
          <strong>{preflight.status === 'compatible' ? 'Catalog checks passed' : 'Compatibility issues found'}</strong>
          <small>{preflight.checkedColumns} {preflight.checkedColumns === 1 ? 'column' : 'columns'} checked</small>
          {preflight.issues.length > 0 && <ul>{preflight.issues.map(({ code, message, column }) => <li key={`${code}:${column ?? ''}:${message}`}>{column && <><code>{column}</code>: </>}{message}</li>)}</ul>}
        </div>}
      </section>
      {saveError && <p role="alert" className="flow-transfer-error">{saveError}</p>}
      <div className="flow-transfer-actions"><button type="button" className="flow-lineage-action" disabled={saving} onClick={closeDraft}>Cancel</button><button type="submit" className="flow-lineage-action is-primary" disabled={saving}>{saving ? 'Saving draft…' : 'Save draft'}</button></div>
    </form> : loadError ? <div role="alert" className="flow-transfer-error">Unable to load transfer plans. {loadError} <button type="button" className="flow-lineage-action" onClick={() => setRevision(value => value + 1)}>Retry</button></div>
      : !plans ? <p role="status">Loading transfer plans…</p>
        : plans.length ? <ul className="flow-transfer-list">{plans.map(plan => <li key={plan.id}>
          <div><strong>{plan.name}</strong><span className="flow-transfer-badge">Draft</span></div>
          <div className="flow-transfer-route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{plan.targetSchema}.{plan.targetTable}</code></div>
          <div className="flow-transfer-meta"><small>{plan.writeMode === 'upsert' ? 'Upsert' : plan.incrementalCursor ? `Incremental append by ${plan.incrementalCursor}` : 'One-time append'} · {plan.trigger === 'scheduled' ? `Daily at ${plan.scheduleTime} UTC` : plan.trigger === 'weekly' ? `Weekly on ${isoWeekdayName(plan.scheduleWeekdayUtc) ?? 'unrecorded weekday'} at ${plan.scheduleTime ?? 'unrecorded time'} UTC` : plan.trigger === 'monthly' ? `Monthly on day ${isUtcMonthDay(plan.scheduleDayOfMonthUtc) ? plan.scheduleDayOfMonthUtc : 'not recorded'} at ${plan.scheduleTime ?? 'unrecorded time'} UTC` : plan.trigger === 'upstream' ? 'On upstream commit' : 'Manual'} (intended)</small><button type="button" className="flow-lineage-action" aria-label={`Edit ${plan.name}`} disabled={Boolean(runningPlan)} onClick={() => edit(plan)}>Edit</button></div>
          <div className="flow-transfer-run-actions">
            <button type="button" className="flow-lineage-action" aria-expanded={historyFor === plan.id} onClick={() => { setHistory(undefined); setAttempts(undefined); setHistoryError(''); setHistoryView('receipts'); setHistoryFor(historyFor === plan.id ? undefined : plan.id); }}>Run history</button>
            {(scheduleEligible(plan) || selectedSchedule === plan.id) && <button type="button" className="flow-lineage-action" aria-expanded={scheduleFor === plan.id} onClick={() => setScheduleFor(scheduleFor === plan.id ? undefined : plan.id)}>Schedule</button>}
            {(eligible(plan) || capabilities?.enabled && savedRun(storageKey(plan.id))) && <button type="button" className="flow-lineage-action is-primary" disabled={Boolean(runningPlan)} onClick={() => setConfirmPlan(plan)}>{runningPlan === plan.id ? 'Running…' : savedRun(storageKey(plan.id)) ? 'Retry same request' : 'Run sandbox transfer'}</button>}
          </div>
          {scheduleFor === plan.id && capabilities && (plan.trigger === 'upstream' || (selectedUpstream && selectedSchedule === plan.id)
            ? <LineageUpstreamTrigger key={`${plan.id}:${plan.updatedAt}`} plan={plan} maxRows={capabilities.maxRows} formatError={errorMessage} />
            : <TransferSchedulePanel key={`${plan.id}:${plan.updatedAt}`} plan={plan} maxRows={capabilities.maxRows} />)}
          {runError?.planId === plan.id && <p role="alert" className="flow-transfer-error">{runError.message}</p>}
          {runReceipt?.planId === plan.id && !(historyFor === plan.id && historyView === 'receipts' && history?.some(receipt => receipt.id === runReceipt.id)) && <div role="status"><TransferReceipts receipts={[runReceipt]} /></div>}
          {historyFor === plan.id && <section aria-label="Sandbox run history">
            <div className="flow-transfer-history-switch" role="group" aria-label="Run history view">
              <button type="button" className="flow-lineage-action" aria-pressed={historyView === 'receipts'} onClick={() => { if (historyView !== 'receipts') { setHistory(undefined); setHistoryError(''); setHistoryView('receipts'); } }}>Committed receipts</button>
              <button type="button" className="flow-lineage-action" aria-pressed={historyView === 'attempts'} onClick={() => { if (historyView !== 'attempts') { setAttempts(undefined); setHistoryError(''); setHistoryView('attempts'); } }}>Attempts</button>
            </div>
            {historyView === 'attempts' && <p>Attempts record requests and outcomes. A recorded request may no longer be active and does not prove that data was committed.</p>}
            {historyError ? <div role="alert" className="flow-transfer-error">{historyError} <button type="button" className="flow-lineage-action" onClick={() => setHistoryRevision(value => value + 1)}>Retry history</button></div>
              : historyView === 'attempts'
                ? attempts ? <TransferAttempts attempts={attempts} /> : <p role="status">Loading attempts…</p>
                : history ? <TransferReceipts receipts={history} /> : <p role="status">Loading run history…</p>}
          </section>}
        </li>)}</ul> : <p>No draft uses <code>{schema}</code> as a source or destination yet.</p>}
    <Modal open={Boolean(confirmPlan)} destroyOnHidden className="flow-surface-modal" title={retrying ? 'Retry previous sandbox request?' : 'Run sandbox transfer?'}
      onCancel={() => setConfirmPlan(undefined)} onOk={() => void confirmRun()} okText={retrying ? 'Retry same request' : 'Confirm sandbox run'}>
      {confirmPlan && (retrying
        ? <p>This uses the same request ID and saved-plan revision. It will not reapply a committed run or run a changed revision.</p>
        : <><p>This writes up to {capabilities?.maxRows} rows to the destination sandbox table immediately. It does not create a schedule.</p>
          <div className="flow-transfer-route"><code>{confirmPlan.sourceSchema}.{confirmPlan.sourceTable}</code><ArrowRightOutlined /><code>{confirmPlan.targetSchema}.{confirmPlan.targetTable}</code></div>
          <p>Write mode: {confirmPlan.writeMode === 'upsert' ? 'Upsert by destination primary key' : confirmPlan.incrementalCursor ? `Incremental append by ${confirmPlan.incrementalCursor}` : 'One-time append'}.</p>
          {confirmPlan.incrementalCursor && <p>Copies only rows above the last committed checkpoint after the first run. Late keys, updates, and deletes at or below the checkpoint are not processed.</p>}</>)}
    </Modal>
  </section>;
}

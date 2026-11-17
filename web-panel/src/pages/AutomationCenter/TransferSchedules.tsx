import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Modal } from '@/components/ui';
import { FieldTimeOutlined, ReloadOutlined } from '@/components/ui/icons';
import { cancelTransferScheduleJob, fetchBatchSyncScheduleJobs, fetchBatchSyncSchedules, fetchCurrentUserContext, fetchTransferScheduleJobs, fetchTransferSchedules, fetchTransferUpstreamJobs, fetchTransferUpstreamTriggers, type BatchSyncScheduleSummary, type BatchSyncScheduleJob, type TransferScheduleJob, type TransferScheduleSummary, type TransferUpstreamJob, type TransferUpstreamSummary } from '@/services/api';
import { getSessionState } from '@/services/session';
import { isoWeekdayName, nextLocalTimeForUtcClock } from '@/services/scheduleTime';
import { recordedUpstreamRule, upstreamExpression } from '../Pipeline/upstreamRules';
import UpstreamSlotHistory, { UpstreamUtcTime } from '../Pipeline/UpstreamSlotHistory';
import { useSandboxIdentity } from '../Pipeline/SandboxMarkings';

type Props = { keyword: string; filter: 'all' | 'enabled' | 'paused' };
type ScheduleItem = ({ kind: 'transfer' } & TransferScheduleSummary) | ({ kind: 'batch' } & BatchSyncScheduleSummary) | ({ kind: 'upstream' } & TransferUpstreamSummary);
type ScheduleJob = BatchSyncScheduleJob | TransferScheduleJob | TransferUpstreamJob;
type Cancellation = { schedule: ScheduleItem & { kind: 'transfer' }; job: TransferScheduleJob & { generation: number } };
const canCancel = (item: ScheduleItem, job: ScheduleJob): job is TransferScheduleJob & { generation: number } =>
  item.kind === 'transfer' && !isoWeekdayName(item.plannedWeekdayUtc) && item.jobCancelAvailable === true && 'slot' in job && 'cancelable' in job && job.cancelable === true && !isoWeekdayName(job.scheduleWeekdayUtc) &&
  (job.state === 'pending' || job.state === 'dispatched') && typeof job.generation === 'number' &&
  Number.isSafeInteger(job.generation) && job.generation === item.generation;
const itemKey = (item: ScheduleItem) => `${item.kind}:${item.planId}`;
const managePath = (item: ScheduleItem) => item.kind === 'batch'
  ? `/ontology/connections?${new URLSearchParams({ sourceId: item.connectionId, batchSync: item.planId })}`
  : `/ontology/lineage?${new URLSearchParams({ schema: item.kind === 'upstream' ? item.sourceSchema : 'ts_transfer_sandbox', transferPlan: item.planId,
    ...(item.kind === 'upstream' ? { scheduleKind: 'upstream' } : {}) })}`;

const configured = (item: ScheduleItem) => item.kind === 'transfer' ? item.enabled : item.configuredEnabled;
const upstreamLabel = (item: TransferUpstreamSummary) => {
  const trigger = item.mixedTimeUtc ? `Daily at ${item.mixedTimeUtc} UTC AND upstream witness` : 'On upstream commit';
  return item.group ? `${trigger} · ${upstreamExpression(item.operator,
    item.parents.map(parent => ({ id: parent.planId, label: parent.planName })), item.group)}`
    : `${trigger} · ${item.operator}: ${item.parents.map(parent => parent.planName).join(', ')}`;
};

export default function TransferSchedules({ keyword, filter }: Props) {
  const identity = useSandboxIdentity();
  return identity ? <TransferSchedulesWorkspace key={identity} keyword={keyword} filter={filter} identity={identity} /> : null;
}

function TransferSchedulesWorkspace({ keyword, filter, identity }: Props & { identity: string }) {
  const [authorized, setAuthorized] = useState(false);
  const [items, setItems] = useState<ScheduleItem[]>();
  const [failed, setFailed] = useState<string[]>([]);
  const [version, setVersion] = useState(0);
  const [historyFor, setHistoryFor] = useState<ScheduleItem>();
  const [history, setHistory] = useState<ScheduleJob[]>();
  const [historyError, setHistoryError] = useState(false);
  const [confirmation, setConfirmation] = useState<Cancellation>();
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelNotice, setCancelNotice] = useState<{ text: string; error?: boolean }>();
  const active = useRef(false);
  const pendingCancel = useRef(false);
  const live = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const request = new AbortController();
    setFailed([]);
    fetchCurrentUserContext().then(context => {
      if (request.signal.aborted || !context.user.is_superuser) return;
      setAuthorized(true);
      return Promise.allSettled([fetchTransferSchedules(request.signal), fetchBatchSyncSchedules(request.signal), fetchTransferUpstreamTriggers(request.signal)]).then(([transfers, batches, upstream]) => {
        if (request.signal.aborted) return;
        setItems([
          ...(transfers.status === 'fulfilled' ? transfers.value.map(item => ({ ...item, kind: 'transfer' as const })) : []),
          ...(batches.status === 'fulfilled' ? batches.value.map(item => ({ ...item, kind: 'batch' as const })) : []),
          ...(upstream.status === 'fulfilled' ? upstream.value.map(item => ({ ...item, kind: 'upstream' as const })) : []),
        ]);
        setFailed([...(transfers.status === 'rejected' ? ['transfer'] : []), ...(batches.status === 'rejected' ? ['batch sync'] : []), ...(upstream.status === 'rejected' ? ['upstream trigger'] : [])]);
      });
    }).catch(() => { /* The transfer schedule overview is administrator-only. */ });
    return () => request.abort();
  }, [version]);
  useEffect(() => {
    if (!historyFor || !authorized) return;
    const request = new AbortController();
    setHistory(undefined);
    setHistoryError(false);
    (historyFor.kind === 'batch' ? fetchBatchSyncScheduleJobs(historyFor.planId, request.signal)
      : historyFor.kind === 'upstream' ? fetchTransferUpstreamJobs(historyFor.planId, request.signal) : fetchTransferScheduleJobs(historyFor.planId, request.signal))
      .then(value => { if (!request.signal.aborted) setHistory(value); })
      .catch(() => { if (!request.signal.aborted) setHistoryError(true); });
    return () => request.abort();
  }, [authorized, historyFor, version]);

  if (!authorized) return null;
  const query = keyword.trim().toLowerCase();
  const matching = items?.filter(item => (filter === 'all' || configured(item) === (filter === 'enabled')) &&
    (!query || `${item.planName} ${item.sourceTable} ${item.targetTable} ${item.kind === 'upstream' ? item.parents.map(parent => parent.planName).join(' ') : ''}`.toLowerCase().includes(query)));
  const submitCancel = async () => {
    if (!confirmation || pendingCancel.current || !live()) return;
    const schedule = matching?.find(item => itemKey(item) === itemKey(confirmation.schedule));
    const current = history?.find(job => job.id === confirmation.job.id);
    if (!schedule || !current || !historyFor || itemKey(historyFor) !== itemKey(schedule) || !canCancel(schedule, current)
      || current.slot !== confirmation.job.slot || current.generation !== confirmation.job.generation) {
      setConfirmation(undefined);
      setCancelNotice({ text: 'The selected waiting job changed. Rechecking its current status.', error: true });
      setVersion(value => value + 1); return;
    }
    pendingCancel.current = true; setCancelBusy(true); setCancelNotice(undefined);
    try {
      const canceled = await cancelTransferScheduleJob(schedule.planId, current.id, {
        expectedGeneration: current.generation, expectedSlot: current.slot,
      });
      if (canceled.id !== current.id || Date.parse(canceled.slot) !== Date.parse(current.slot) || canceled.state !== 'canceled') {
        throw new Error('Unverified cancellation');
      }
      if (live()) {
        setHistory(value => value?.map(job => job.id === canceled.id ? canceled : job));
        setCancelNotice({ text: 'The selected waiting job was canceled. The schedule and later slots are unchanged.' });
      }
    } catch (failure) {
      if (live()) {
        const response = (failure as { response?: { status?: number; data?: { errors?: { code?: string }; detail?: { code?: string } } } })?.response;
        const error = response?.data?.errors?.code ?? response?.data?.detail?.code;
        setCancelNotice({ error: true, text: error === 'job_busy'
          ? 'The selected job is busy. Cancellation was not confirmed. Rechecking its current status.'
          : 'Cancellation was not confirmed. Rechecking the selected job’s current status.' });
      }
    } finally {
      pendingCancel.current = false;
      if (live()) { setCancelBusy(false); setConfirmation(undefined); setVersion(value => value + 1); }
    }
  };

  return <section className="automation-transfer-schedules" aria-label="Ontology transfer schedules">
    <header><h3><FieldTimeOutlined />Transfer schedules</h3>
      <Button size="small" icon={<ReloadOutlined />} aria-label="Refresh transfer schedules" disabled={cancelBusy || Boolean(confirmation)} onClick={() => setVersion(value => value + 1)} />
    </header>
    {failed.length > 0 && <p role="alert">Unable to load {failed.join(' and ')} schedules.</p>}
    {cancelNotice && <p role={cancelNotice.error ? 'alert' : 'status'}>{cancelNotice.text}</p>}
    {!matching ? <p role="status">Loading transfer schedules…</p>
      : matching.length ? <ul>{matching.map(item => <li key={itemKey(item)}>
        <div><strong>{item.planName}</strong>{item.kind === 'batch' && <span>Batch sync</span>}{item.kind === 'upstream' && <span>Upstream trigger</span>}<code>{item.sourceTable} → {item.targetTable}</code></div>
        <div>
          <span>{item.kind === 'upstream' ? upstreamLabel(item)
            : item.kind === 'transfer' && isoWeekdayName(item.plannedWeekdayUtc) ? `Weekly on ${isoWeekdayName(item.plannedWeekdayUtc)} at ${item.plannedTimeUtc ?? 'unrecorded time'} UTC`
            : item.plannedTimeUtc ? `Daily at ${item.plannedTimeUtc} UTC · local time ${nextLocalTimeForUtcClock(item.plannedTimeUtc)}` : 'No daily time configured'}</span>
          <span className={`automation-schedule-status ${item.enabled && item.available ? 'is-enabled' : ''}`}><i />{configured(item) ? (item.enabled && item.available ? 'Armed' : 'Armed · blocked') : 'Paused'}</span>
          <Button size="small" aria-expanded={historyFor && itemKey(historyFor) === itemKey(item)} disabled={cancelBusy || Boolean(confirmation)} onClick={() => {
            setHistory(undefined);
            setHistoryError(false);
            setHistoryFor(value => value && itemKey(value) === itemKey(item) ? undefined : item);
          }}>Dispatch history</Button>
          <Link to={managePath(item)}>{item.kind === 'batch' ? 'Manage in Data Connections' : 'Manage in Lineage'}</Link>
        </div>
        {historyFor && itemKey(historyFor) === itemKey(item) && <section aria-label={item.kind === 'batch' ? 'Batch sync dispatch history' : item.kind === 'upstream' ? 'Upstream dispatch history' : 'Transfer dispatch history'}>
          <p>Dispatch does not confirm that rows committed.</p>
          {historyError ? <p role="alert">Dispatch history could not be loaded. Refresh schedules to retry.</p>
            : !history ? <p role="status">Loading dispatch history…</p>
              : history.length ? <ol>{history.map(job => {
                const timestamp = 'slot' in job ? job.slot : job.events.reduce<string | undefined>((latest, event) =>
                  !latest || Date.parse(event.eventRecordedAt) > Date.parse(latest) ? event.eventRecordedAt : latest, undefined);
                const recorded = 'events' in job ? recordedUpstreamRule(job) : undefined;
                return <li key={job.id}>
                  {'events' in job && job.mixedSlot ? <span>UTC slot: <UpstreamUtcTime value={job.mixedSlot} /></span>
                    : timestamp && (item.kind === 'transfer' ? <span>UTC slot: <UpstreamUtcTime value={timestamp} /></span>
                      : <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time>)}
                  {'scheduleWeekdayUtc' in job && isoWeekdayName(job.scheduleWeekdayUtc) &&
                    <span>Recorded mode: Weekly UTC · {isoWeekdayName(job.scheduleWeekdayUtc)}</span>}
                  <strong>{job.state === 'canceled' ? 'Canceled' : job.state === 'no_new_data' ? 'No new data' : job.state.replace(/_/g, ' ')}{recorded && ` · ${recorded.expression}`}</strong>
                  {job.state === 'canceled' && 'canceledAt' in job && job.canceledAt && <span>Canceled at: <UpstreamUtcTime value={job.canceledAt} /></span>}
                  {'events' in job && job.mixedSlot && <><span>Recorded mode: UTC time gate AND upstream witness</span>
                    {job.witnessObservedAt && <span>Witness observed at: <UpstreamUtcTime value={job.witnessObservedAt} /></span>}</>}
                  {recorded?.parents.map(parent => <span key={parent.id}>Rule producer {parent.label}: <code>{parent.id}</code></span>)}
                  {'events' in job && job.events.map(event => <span key={event.eventId}>Parent receipt: <code>{event.parentReceiptId}</code></span>)}
                  {job.receiptId && ('events' in job ? <span>Child receipt: <code>{job.receiptId}</code></span> : <code title={job.state === 'no_new_data' ? 'No new data record' : 'Committed receipt'}>{job.receiptId}</code>)}
                  {item.kind === 'transfer' && canCancel(item, job) && <Button size="small" danger disabled={cancelBusy || Boolean(confirmation)}
                    onClick={() => { setCancelNotice(undefined); setConfirmation({ schedule: item, job }); }}>Cancel waiting job</Button>}
                </li>;
              })}</ol> : <p>No scheduled jobs have been recorded.</p>}
          {item.kind === 'upstream' && (item.mixedTimeUtc || item.mixedHasHistory ||
            history?.some(job => 'events' in job && Boolean(job.mixedSlot))) &&
            <UpstreamSlotHistory planId={item.planId} version={version} />}
        </section>}
      </li>)}</ul> : failed.length ? null : <p>{query || filter !== 'all' ? 'No transfer schedules match the filters.' : 'No schedules are bound to a transfer plan or batch sync.'}</p>}
    <Modal open={Boolean(confirmation)} destroyOnHidden className="flow-surface-modal" title="Cancel this waiting job?"
      onCancel={() => { if (!cancelBusy) setConfirmation(undefined); }} onOk={submitCancel} confirmLoading={cancelBusy}
      closable={!cancelBusy} keyboard={!cancelBusy} maskClosable={false} okText="Confirm job cancellation">
      {confirmation && <>
        <p>Transfer plan: <strong>{confirmation.schedule.planName}</strong>.</p>
        <p>UTC slot: <UpstreamUtcTime value={confirmation.job.slot} /> · Schedule generation {confirmation.job.generation}.</p>
        <p>Job ID: <code>{confirmation.job.id}</code></p>
        <p>This applies only to the listed waiting job. It does not stop running work, pause the schedule, or change later slots.</p>
      </>}
    </Modal>
  </section>;
}

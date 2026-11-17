import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui';
import { FieldTimeOutlined, ReloadOutlined } from '@/components/ui/icons';
import { fetchBatchSyncScheduleJobs, fetchBatchSyncSchedules, fetchCurrentUserContext, fetchTransferScheduleJobs, fetchTransferSchedules, fetchTransferUpstreamJobs, fetchTransferUpstreamTriggers, type BatchSyncScheduleSummary, type BatchSyncScheduleJob, type TransferScheduleJob, type TransferScheduleSummary, type TransferUpstreamJob, type TransferUpstreamSummary } from '@/services/api';
import { nextLocalTimeForUtcClock } from '@/services/scheduleTime';
import { recordedUpstreamRule, upstreamExpression } from '../Pipeline/upstreamRules';
import UpstreamSlotHistory, { UpstreamUtcTime } from '../Pipeline/UpstreamSlotHistory';

type Props = { keyword: string; filter: 'all' | 'enabled' | 'paused' };
type ScheduleItem = ({ kind: 'transfer' } & TransferScheduleSummary) | ({ kind: 'batch' } & BatchSyncScheduleSummary) | ({ kind: 'upstream' } & TransferUpstreamSummary);
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
  const [authorized, setAuthorized] = useState(false);
  const [items, setItems] = useState<ScheduleItem[]>();
  const [failed, setFailed] = useState<string[]>([]);
  const [version, setVersion] = useState(0);
  const [historyFor, setHistoryFor] = useState<ScheduleItem>();
  const [history, setHistory] = useState<(BatchSyncScheduleJob | TransferScheduleJob | TransferUpstreamJob)[]>();
  const [historyError, setHistoryError] = useState(false);
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

  return <section className="automation-transfer-schedules" aria-label="Ontology transfer schedules">
    <header><h3><FieldTimeOutlined />Transfer schedules</h3>
      <Button size="small" icon={<ReloadOutlined />} aria-label="Refresh transfer schedules" onClick={() => setVersion(value => value + 1)} />
    </header>
    {failed.length > 0 && <p role="alert">Unable to load {failed.join(' and ')} schedules.</p>}
    {!matching ? <p role="status">Loading transfer schedules…</p>
      : matching.length ? <ul>{matching.map(item => <li key={itemKey(item)}>
        <div><strong>{item.planName}</strong>{item.kind === 'batch' && <span>Batch sync</span>}{item.kind === 'upstream' && <span>Upstream trigger</span>}<code>{item.sourceTable} → {item.targetTable}</code></div>
        <div>
          <span>{item.kind === 'upstream' ? upstreamLabel(item)
            : item.plannedTimeUtc ? `Daily at ${item.plannedTimeUtc} UTC · local time ${nextLocalTimeForUtcClock(item.plannedTimeUtc)}` : 'No daily time configured'}</span>
          <span className={`automation-schedule-status ${item.enabled && item.available ? 'is-enabled' : ''}`}><i />{configured(item) ? (item.enabled && item.available ? 'Armed' : 'Armed · blocked') : 'Paused'}</span>
          <Button size="small" aria-expanded={historyFor && itemKey(historyFor) === itemKey(item)} onClick={() => {
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
                    : timestamp && <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time>}
                  <strong>{job.state === 'no_new_data' ? 'No new data' : job.state.replace(/_/g, ' ')}{recorded && ` · ${recorded.expression}`}</strong>
                  {'events' in job && job.mixedSlot && <><span>Recorded mode: UTC time gate AND upstream witness</span>
                    {job.witnessObservedAt && <span>Witness observed at: <UpstreamUtcTime value={job.witnessObservedAt} /></span>}</>}
                  {recorded?.parents.map(parent => <span key={parent.id}>Rule producer {parent.label}: <code>{parent.id}</code></span>)}
                  {'events' in job && job.events.map(event => <span key={event.eventId}>Parent receipt: <code>{event.parentReceiptId}</code></span>)}
                  {job.receiptId && ('events' in job ? <span>Child receipt: <code>{job.receiptId}</code></span> : <code title={job.state === 'no_new_data' ? 'No new data record' : 'Committed receipt'}>{job.receiptId}</code>)}
                </li>;
              })}</ol> : <p>No scheduled jobs have been recorded.</p>}
          {item.kind === 'upstream' && (item.mixedTimeUtc || item.mixedHasHistory ||
            history?.some(job => 'events' in job && Boolean(job.mixedSlot))) &&
            <UpstreamSlotHistory planId={item.planId} version={version} />}
        </section>}
      </li>)}</ul> : failed.length ? null : <p>{query || filter !== 'all' ? 'No transfer schedules match the filters.' : 'No schedules are bound to a transfer plan or batch sync.'}</p>}
  </section>;
}

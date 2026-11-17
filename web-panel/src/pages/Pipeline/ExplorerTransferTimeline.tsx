import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchTransferTimeline, fetchTransferTimelineDetail } from '../../services/api';
import type { TransferRunReceipt, TransferTimelineDetail, TransferTimelineEntry, TransferTimelineFilters } from '../../services/api';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';
import { recordedUpstreamRule } from './upstreamRules';

const physicalLabels: Record<TransferTimelineEntry['physicalScope'], string> = {
  current: 'Current table identity',
  historical: 'Prior table identity',
  unknown: 'Physical identity unverified',
};
const triggerLabels: Record<NonNullable<TransferTimelineEntry['trigger']>, string> = {
  manual: 'Manual', daily_utc: 'Daily UTC', upstream: 'Upstream commit',
};
const statusLabels: Record<string, string> = {
  started: 'Request recorded', rejected: 'Rejected', outcome_unknown: 'Outcome unknown',
  committed: 'Committed', replayed: 'Existing receipt returned', pending: 'Pending dispatch',
  dispatched: 'Dispatched', expired: 'Expired',
};
const labelStatus = (status: string) => statusLabels[status] ?? status.replace(/_/g, ' ');
const titleFor = (entry: TransferTimelineEntry) => entry.kind === 'receipt' ? 'Committed receipt' : `Attempt · ${labelStatus(entry.status)}`;
const keyFor = (entry: TransferTimelineEntry) => `${entry.kind}:${entry.id}`;
const timeText = (value: string) => new Date(value).toLocaleString();

function TimelineTime({ value }: { value: string }) {
  return <time dateTime={value}>{timeText(value)}</time>;
}

function TimelineEntryFacts({ entry }: { entry: TransferTimelineEntry }) {
  return <>
    <div><strong>{titleFor(entry)}</strong><span>{physicalLabels[entry.physicalScope]}</span></div>
    <div><span>{entry.kind === 'receipt' ? 'Receipt recorded' : 'Attempt recorded'}</span><TimelineTime value={entry.occurredAt} /></div>
    {entry.finishedAt && <div><span>Attempt finished</span><TimelineTime value={entry.finishedAt} /></div>}
    {entry.sourceSchema && entry.sourceTable && <div><span>Source table</span><code>{entry.sourceSchema}.{entry.sourceTable}</code></div>}
    <div><span>Trigger</span><span>{entry.trigger ? triggerLabels[entry.trigger] : 'Not verified'}</span></div>
    {entry.kind === 'receipt' && entry.rowsAffected !== null && <>
      <p>{entry.rowsAffected.toLocaleString()} rows affected{entry.rowsAffected === 0 ? ' · No observed movement' : ''}</p>
      {entry.physicalScope !== 'current' && <p>This receipt is not evidence for the current physical table.</p>}
    </>}
    {entry.receiptId && <div><span>{entry.kind === 'receipt' ? 'Receipt' : 'Linked receipt'} ID</span><code>{entry.receiptId}</code></div>}
    {entry.message && <p>{entry.message}</p>}
  </>;
}

type Props = { schema: string; table: string; onLatestCurrentReceipt: (receipt: TransferRunReceipt | null) => void };
export default function ExplorerTransferTimeline(props: Props) {
  const identity = useSandboxIdentity();
  return identity ? <TimelineWorkspace key={JSON.stringify([identity, props.schema, props.table])} {...props} /> : null;
}

function TimelineWorkspace({ schema, table, onLatestCurrentReceipt }: Props) {
  const [entries, setEntries] = useState<TransferTimelineEntry[]>();
  const [cursor, setCursor] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<TransferTimelineEntry>();
  const [detail, setDetail] = useState<TransferTimelineDetail>();
  const [detailFailed, setDetailFailed] = useState(false);
  const olderRequest = useRef<AbortController>();
  const firstRequest = useRef<AbortController>();
  const detailRequest = useRef<AbortController>();
  const [filters, setFilters] = useState<TransferTimelineFilters>({});
  const [view, setView] = useState<NonNullable<TransferTimelineFilters['view']> | ''>('');
  const [from, setFrom] = useState('');
  const [before, setBefore] = useState('');
  const [filterError, setFilterError] = useState('');
  const filtered = Boolean(filters.view || filters.from);

  useEffect(() => {
    onLatestCurrentReceipt(null);
    return () => onLatestCurrentReceipt(null);
  }, [onLatestCurrentReceipt]);

  const clearScoped = () => {
    firstRequest.current?.abort(); olderRequest.current?.abort(); detailRequest.current?.abort();
    firstRequest.current = undefined; olderRequest.current = undefined; detailRequest.current = undefined;
    setEntries(undefined); setCursor(null); setSelected(undefined); setDetail(undefined);
    setLoadingMore(false); setMoreFailed(false); setDetailFailed(false); setFailed(false);
  };
  const apply = () => {
    const start = from ? Date.parse(`${from}Z`) : NaN;
    const end = before ? Date.parse(`${before}Z`) : NaN;
    if ((from || before) && (!Number.isFinite(start) || !Number.isFinite(end) || start >= end || end - start > 31 * 86_400_000)) {
      setFilterError('Choose both UTC times, with To after From and no more than 31 days apart.'); return;
    }
    clearScoped(); setFilterError('');
    setFilters({ ...(view ? { view } : {}), ...(from && before ? { from: new Date(start).toISOString(), before: new Date(end).toISOString() } : {}) });
  };
  const reset = () => {
    clearScoped(); setView(''); setFrom(''); setBefore(''); setFilterError(''); setFilters({});
  };

  useEffect(() => {
    const controller = new AbortController();
    firstRequest.current = controller;
    setEntries(undefined); setCursor(null); setFailed(false); setMoreFailed(false); setLoadingMore(false);
    void fetchTransferTimeline(schema, table, undefined, controller.signal, filters).then(page => {
      if (controller.signal.aborted) return;
      setEntries(page.entries); setCursor(page.nextCursor);
      onLatestCurrentReceipt(page.latestCurrentReceipt);
    }).catch(failure => {
      if (!controller.signal.aborted) {
        if (sandboxScopeDenied(failure)) onLatestCurrentReceipt(null);
        setFailed(true);
      }
    });
    return () => {
      controller.abort();
      olderRequest.current?.abort();
      olderRequest.current = undefined;
    };
  }, [schema, table, revision, onLatestCurrentReceipt, filters]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    detailRequest.current = controller;
    setDetail(undefined); setDetailFailed(false);
    void fetchTransferTimelineDetail(schema, table, selected.kind, selected.id, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDetail(value); })
      .catch(failure => {
        if (controller.signal.aborted) return;
        if (sandboxScopeDenied(failure)) {
          clearScoped();
          onLatestCurrentReceipt(null);
          setFailed(true);
        } else setDetailFailed(true);
      });
    return () => controller.abort();
  }, [schema, table, selected]);

  const loadOlder = async () => {
    if (!cursor || olderRequest.current) return;
    const controller = new AbortController();
    olderRequest.current = controller; setLoadingMore(true); setMoreFailed(false);
    try {
      const page = await fetchTransferTimeline(schema, table, cursor, controller.signal, filters);
      if (controller.signal.aborted) return;
      setEntries(previous => {
        const seen = new Set(previous?.map(keyFor));
        return [...(previous ?? []), ...page.entries.filter(entry => !seen.has(keyFor(entry)))];
      });
      setCursor(page.nextCursor);
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (sandboxScopeDenied(failure)) { clearScoped(); onLatestCurrentReceipt(null); setFailed(true); }
        else setMoreFailed(true);
      }
    } finally {
      if (olderRequest.current === controller) { olderRequest.current = undefined; setLoadingMore(false); }
    }
  };

  return <section className="flow-evidence-card flow-evidence-timeline" aria-label="Transfer timeline">
    <header><h4>Transfer timeline</h4><span>Sandbox destination · newest first</span></header>
    <p>Attempts and dispatches do not prove a write. Receipts record committed transfers and identify whether the physical destination matches this table.</p>
    <form className="flow-evidence-freshness-form" aria-label="Transfer timeline filters" onSubmit={event => { event.preventDefault(); apply(); }}>
      <label>Timeline view<select aria-label="Timeline view" value={view} onChange={event => setView(event.target.value as typeof view)}>
        <option value="">All</option><option value="receipts">Committed receipts</option>
        <option value="rejected_attempts">Rejected attempts</option><option value="unconfirmed_attempts">Outcome unconfirmed</option>
      </select></label>
      <label>From (UTC)<input type="datetime-local" aria-label="From (UTC)" value={from} onChange={event => setFrom(event.target.value)} /></label>
      <label>To (UTC)<input type="datetime-local" aria-label="To (UTC)" value={before} onChange={event => setBefore(event.target.value)} /></label>
      <p>Enter both times in UTC. From is included and To is excluded; maximum 31 days. Entry times are shown in your local time.</p>
      {filterError && <p role="alert">{filterError}</p>}
      <div className="flow-evidence-freshness-actions">
        <Button size="small" htmlType="submit">Apply timeline filters</Button>
        <Button size="small" onClick={reset}>Reset timeline filters</Button>
      </div>
      {filtered && <p role="status">Showing filtered timeline entries.</p>}
    </form>
    {failed ? <><p role="alert">Transfer timeline unavailable.</p><Button size="small" onClick={() => setRevision(value => value + 1)}>Retry timeline</Button></>
      : !entries ? <p role="status">Loading transfer timeline…</p> : entries.length ? <>
        <ol className="flow-evidence-receipts" aria-label="Transfer timeline entries">
          {entries.map(entry => <li key={keyFor(entry)}>
            <TimelineEntryFacts entry={entry} />
            <Button size="small" onClick={() => { setDetail(undefined); setDetailFailed(false); setSelected(entry); }}>Inspect timeline entry</Button>
          </li>)}
        </ol>
        {moreFailed && <p role="alert">Older entries could not be loaded.</p>}
        {cursor && <Button size="small" loading={loadingMore} onClick={loadOlder}>{moreFailed ? 'Retry older entries' : 'Load older entries'}</Button>}
      </> : <p>{filtered ? 'No scoped transfer activity matches these filters.' : 'No scoped transfer activity recorded for this destination.'}</p>}
    <Modal open={Boolean(selected)} destroyOnHidden className="flow-surface-modal" title="Transfer timeline detail"
      onCancel={() => setSelected(undefined)} footer={null}>
      {detailFailed ? <p role="alert">This scoped entry could not be verified. Close the detail and refresh Evidence.</p>
        : !detail ? <p role="status">Loading timeline detail…</p> : <div className="flow-evidence-timeline-detail">
          <TimelineEntryFacts entry={detail} />
          <div><span>Plan ID</span> <code>{detail.planId}</code></div>
          <div><span>Plan revision</span> {detail.planUpdatedAt ? <TimelineTime value={detail.planUpdatedAt} /> : 'Not verified'}</div>
          {detail.job ? <section aria-label="Dispatch provenance">
            <h5>Dispatch provenance</h5>
            <p>{triggerLabels[detail.job.kind]} · {labelStatus(detail.job.state)}</p>
            {detail.job.group && detail.job.operator && <p>Recorded condition: <strong>{recordedUpstreamRule({
              operator: detail.job.operator, group: detail.job.group, ruleParentPlanIds: detail.job.ruleParentPlanIds,
            }).expression}</strong></p>}
            <p>Job ID: <code>{detail.job.id}</code></p>
            {detail.job.dueAt && <p>{detail.job.kind === 'daily_utc' ? 'Scheduled for' : 'Event set ready at'}: <TimelineTime value={detail.job.dueAt} /></p>}
            {detail.job.parentEvents.length > 0 && <ul className="flow-evidence-receipts" aria-label="Parent events">
              {detail.job.parentEvents.map(event => <li key={event.eventId}>
                <div><span>Parent receipt ID</span><code>{event.parentReceiptId}</code></div>
                <div><span>Parent plan ID</span><code>{event.parentPlanId}</code></div>
                <div><span>Parent event recorded</span><TimelineTime value={event.eventRecordedAt} /></div>
              </li>)}
            </ul>}
            <p>Dispatch and parent receipts are not evidence of child writes.</p>
          </section> : <p>No verified scheduler job is linked to this entry.</p>}
        </div>}
    </Modal>
  </section>;
}

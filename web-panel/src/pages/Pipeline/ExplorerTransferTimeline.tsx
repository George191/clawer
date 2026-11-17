import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchTransferTimeline, fetchTransferTimelineDetail } from '../../services/api';
import type { TransferRunReceipt, TransferTimelineDetail, TransferTimelineEntry } from '../../services/api';

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

export default function ExplorerTransferTimeline({ schema, table, onLatestCurrentReceipt }: {
  schema: string; table: string; onLatestCurrentReceipt: (receipt: TransferRunReceipt | null) => void;
}) {
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

  useEffect(() => {
    const controller = new AbortController();
    setEntries(undefined); setCursor(null); setFailed(false); setMoreFailed(false);
    onLatestCurrentReceipt(null);
    void fetchTransferTimeline(schema, table, undefined, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setEntries(page.entries); setCursor(page.nextCursor);
      onLatestCurrentReceipt(page.latestCurrentReceipt);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => {
      controller.abort();
      olderRequest.current?.abort();
      olderRequest.current = undefined;
    };
  }, [schema, table, revision, onLatestCurrentReceipt]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetail(undefined); setDetailFailed(false);
    void fetchTransferTimelineDetail(schema, table, selected.kind, selected.id, controller.signal)
      .then(value => { if (!controller.signal.aborted) setDetail(value); })
      .catch(() => { if (!controller.signal.aborted) setDetailFailed(true); });
    return () => controller.abort();
  }, [schema, table, selected]);

  const loadOlder = async () => {
    if (!cursor || olderRequest.current) return;
    const controller = new AbortController();
    olderRequest.current = controller; setLoadingMore(true); setMoreFailed(false);
    try {
      const page = await fetchTransferTimeline(schema, table, cursor, controller.signal);
      if (controller.signal.aborted) return;
      setEntries(previous => {
        const seen = new Set(previous?.map(keyFor));
        return [...(previous ?? []), ...page.entries.filter(entry => !seen.has(keyFor(entry)))];
      });
      setCursor(page.nextCursor);
    } catch {
      if (!controller.signal.aborted) setMoreFailed(true);
    } finally {
      if (olderRequest.current === controller) { olderRequest.current = undefined; setLoadingMore(false); }
    }
  };

  return <section className="flow-evidence-card flow-evidence-timeline" aria-label="Transfer timeline">
    <header><h4>Transfer timeline</h4><span>Sandbox destination · newest first</span></header>
    <p>Attempts and dispatches do not prove a write. Receipts record committed transfers and identify whether the physical destination matches this table.</p>
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
      </> : <p>No scoped transfer activity recorded for this destination.</p>}
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

import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRightOutlined, CloseOutlined, DatabaseOutlined } from '@/components/ui/icons';
import { fetchCommittedBatchLineage, fetchCommittedBatchLineageEntry } from '../../services/api';
import type { CommittedBatchLineageDetail, CommittedBatchLineageEntry, CommittedBatchLineagePage, ManagedBatchLineageAsset } from '../../services/api';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';

export const MANAGED_BATCH_ASSET = 'committed-batch';
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const statusNames: Record<CommittedBatchLineageEntry['status'], string> = {
  started: 'Request recorded',
  committed: 'Committed',
  no_new_data: 'No new data',
  replayed: 'Existing result reused',
  rejected: 'Rejected',
  retryable: 'Retry available',
  outcome_unknown: 'Outcome unknown',
};
const summaries = {
  unreviewed: 'No current reviewed upstream',
  reviewed: 'Reviewed upstream · no positive current receipt',
  observed: 'Verified current transfer',
  archived: 'Archived evidence · no current verified transfer',
};
type Selection = Pick<CommittedBatchLineageEntry, 'kind' | 'id'>;
type Range = { from: string; to: string };
const dateText = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });
const Time = ({ value }: { value?: string | null }) => value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{dateText(value)}</time> : <>Not recorded</>;
const scopeName = (entry: CommittedBatchLineageEntry) => entry.physicalScope === 'current' ? 'Current asset'
  : entry.physicalScope === 'archived' ? 'Archived asset' : 'Unverified asset identity';
const entryKey = (entry: Selection) => `${entry.kind}:${entry.id}`;

type Props = {
  asset: ManagedBatchLineageAsset;
  selected: boolean; exactReceipt: string | null; version: number; onSelect: () => void; onClose: () => void;
};

export default function ManagedBatchLineage(props: Props) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, props.asset.schema, props.asset.table])} {...props} /> : null;
}

function Workspace({ asset, selected, exactReceipt, version, onSelect, onClose }: Props) {
  const [page, setPage] = useState<CommittedBatchLineagePage>();
  const [available, setAvailable] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [rangeError, setRangeError] = useState('');
  const [showUpstream, setShowUpstream] = useState(false);
  const [detailItem, setDetailItem] = useState<Selection>();
  const [detail, setDetail] = useState<CommittedBatchLineageDetail>();
  const [detailError, setDetailError] = useState(false);
  const [fromLocal, setFromLocal] = useState('');
  const [toLocal, setToLocal] = useState('');
  const [range, setRange] = useState<Range>();
  const [requestVersion, setRequestVersion] = useState(0);
  const listRequest = useRef<AbortController>();
  const node = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const selectedItem = detailItem ?? (selected && exactReceipt && uuid.test(exactReceipt) ? { kind: 'receipt' as const, id: exactReceipt } : undefined);
  const detailKey = selectedItem ? entryKey(selectedItem) : '';
  const currentDetail = detail && entryKey(detail) === detailKey ? detail : undefined;
  const load = (next?: { range: Range; cursor: string }) => {
    const request = new AbortController();
    listRequest.current?.abort(); listRequest.current = request;
    const append = next != null;
    setLoading(true); setLoadError(false);
    if (!append) setPage(undefined);
    void fetchCommittedBatchLineage(asset, { ...(next?.range ?? range), cursor: next?.cursor, limit: 20 }, request.signal).then(value => {
      if (!request.signal.aborted) {
        setAvailable(true); setMissing(false);
        setPage(previous => ({
          ...value, entries: append && previous ? [...previous.entries, ...value.entries] : value.entries,
        }));
      }
    }).catch(failure => {
      if (!request.signal.aborted) {
        if (sandboxScopeDenied(failure)) {
          setPage(undefined); setDetail(undefined); setAvailable(false); setMissing(true); setShowUpstream(false);
        } else setLoadError(true);
      }
    }).finally(() => { if (!request.signal.aborted) setLoading(false); });
  };

  useEffect(() => { load(); return () => listRequest.current?.abort(); }, [range, version, requestVersion]);
  useEffect(() => { setDetailItem(undefined); setShowUpstream(false); }, [selected, exactReceipt]);
  useEffect(() => { if (selected && available) heading.current?.focus(); }, [selected, available]);
  useEffect(() => {
    const request = new AbortController();
    setDetail(undefined); setDetailError(false);
    if (selected && available && selectedItem) {
      void fetchCommittedBatchLineageEntry(asset, selectedItem.kind, selectedItem.id, request.signal).then(value => {
        if (!request.signal.aborted) setDetail(value);
      }).catch(failure => {
        if (!request.signal.aborted) {
          const status = (failure as { response?: { status?: number } })?.response?.status;
          if (status === 401 || status === 403) {
            listRequest.current?.abort();
            setAvailable(false); setMissing(true); setPage(undefined); setShowUpstream(false); setLoading(false);
          } else setDetailError(true);
        }
      });
    }
    return () => request.abort();
  }, [selected, detailKey, available, version, requestVersion]);

  const close = () => { onClose(); requestAnimationFrame(() => node.current?.focus()); };
  const changeRange = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const from = Date.parse(fromLocal), to = Date.parse(toLocal);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from || to - from > 31 * 86_400_000) {
      setRangeError('Choose a time range of 31 days or less. The end must be after the start.'); return;
    }
    setRangeError('');
    setRange({ from: new Date(from).toISOString(), to: new Date(to).toISOString() });
  };
  if (!available) return selected && (loading || missing || loadError)
    ? <div className="flow-lineage-managed-unavailable" role="status">{loading ? 'Loading managed asset…' : 'Managed asset unavailable.'}</div> : null;
  const facts = page?.lineage;
  const showEdge = showUpstream && facts && (facts.state === 'reviewed' || facts.state === 'observed' || facts.state === 'archived');
  const verifiedEdge = Boolean(facts?.state === 'observed' && facts.latestPositiveReceiptId && facts.latestPositiveRecordedAt);
  const showDashedEdge = Boolean(facts?.state === 'reviewed');
  return <section className={`flow-lineage-managed${selected ? ' is-selected' : ''}`} aria-label="Managed committed batch lineage">
    <div className="flow-lineage-managed__graph">
      <span className="flow-lineage-managed__eyebrow">Managed asset · Synthetic</span>
      <div className="flow-lineage-managed__nodes">
        {selected && showEdge && <>
          <span className="flow-lineage-managed__source"><DatabaseOutlined /> <code>{facts.sourceSchema}.{facts.sourceTable}</code></span>
          {(verifiedEdge || showDashedEdge) && <span className={`flow-lineage-managed__edge${verifiedEdge ? ' is-observed' : ' is-reviewed'}`}
            role="img" aria-label={verifiedEdge ? 'Solid verified transfer edge' : 'Dashed reviewed relationship'}><ArrowRightOutlined /></span>}
          {!verifiedEdge && !showDashedEdge && <span className="flow-lineage-managed__edge-label">Historical only</span>}
        </>}
        <button ref={node} type="button" className="flow-lineage-action" aria-pressed={selected} onClick={onSelect}><DatabaseOutlined /> Committed batch asset</button>
      </div>
    </div>
    {selected && <aside className="flow-lineage-inspector flow-lineage-managed__inspector" aria-label="Managed asset details"
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close(); } }}>
      <header><span>SELECTED MANAGED ASSET</span><button type="button" aria-label="Close managed asset details" onClick={close}><CloseOutlined /></button></header>
      <h2 tabIndex={-1} ref={heading}>Committed batch asset</h2>
      {facts && <>
        <code>{facts.targetSchema}.{facts.targetTable}</code>
        <p role="status">{facts.state === 'observed' && !verifiedEdge ? 'Current transfer not verified' : summaries[facts.state]}</p>
        <p>Committed through: <code>{facts.appliedThrough ?? 'Not recorded'}</code></p>
        {verifiedEdge && <p>Latest positive receipt: <button type="button" className="flow-lineage-action" onClick={() => setDetailItem({ kind: 'receipt', id: facts.latestPositiveReceiptId! })}>Inspect receipt</button> · <Time value={facts.latestPositiveRecordedAt} /></p>}
        {facts.state !== 'unreviewed' && <button type="button" className="flow-lineage-action" aria-expanded={showUpstream} onClick={() => setShowUpstream(value => !value)}>
          {showUpstream ? 'Hide reviewed upstream' : 'Reveal reviewed upstream'}
        </button>}
      </>}
      <section aria-label="Run timeline" className="flow-lineage-managed__timeline">
        <h3>Run timeline</h3>
        <p>Receipts and attempts are separate. A request or an empty check does not verify data movement.</p>
        <form onSubmit={changeRange} aria-label="Timeline time range">
          <label>Start (local) <input type="datetime-local" value={fromLocal} onChange={event => setFromLocal(event.target.value)} /></label>
          <label>End (local) <input type="datetime-local" value={toLocal} onChange={event => setToLocal(event.target.value)} /></label>
          <div className="flow-lineage-managed__controls">
            <button type="submit" className="flow-lineage-action" disabled={loading}>Apply time range</button>
            {range && <button type="button" className="flow-lineage-action" disabled={loading} onClick={() => { setRange(undefined); setRangeError(''); setFromLocal(''); setToLocal(''); }}>Recent 30 days</button>}
          </div>
          {rangeError && <p role="alert">{rangeError}</p>}
        </form>
        {detailKey && <section className="flow-lineage-managed__entry" aria-label="Timeline entry details">
          <h4>Timeline entry details</h4>
          {currentDetail ? <EntryDetails entry={currentDetail} /> : <p role={detailError ? 'alert' : 'status'}>{detailError ? 'This timeline entry is unavailable.' : 'Loading timeline entry…'}</p>}
        </section>}
        {loadError && <p role="alert">Could not load the timeline. <button type="button" className="flow-lineage-action" onClick={() => setRequestVersion(value => value + 1)}>Retry timeline</button></p>}
        {loading && <p role="status">Loading run timeline…</p>}
        {page && <>
          <p>From <Time value={page.from} /> to <Time value={page.to} /></p>
          {page.entries.length ? <ol>
            {page.entries.map(entry => <li key={entryKey(entry)}>
              <span><strong>{entry.kind === 'receipt' ? entry.status === 'no_new_data' ? 'Check record' : 'Receipt' : 'Attempt'}</strong> · {statusNames[entry.status]} · {scopeName(entry)}</span>
              <Time value={entry.occurredAt} />
              <button type="button" className="flow-lineage-action" aria-pressed={entryKey(entry) === detailKey} onClick={() => setDetailItem(entry)}>Inspect {entry.kind}</button>
            </li>)}
          </ol> : <p>No scoped receipts or attempts in this time range.</p>}
          {page.nextCursor && <button type="button" className="flow-lineage-action" disabled={loading} onClick={() => load({
            range: { from: page.from, to: page.to }, cursor: page.nextCursor!,
          })}>Load earlier entries</button>}
        </>}
      </section>
    </aside>}
  </section>;
}

function EntryDetails({ entry }: { entry: CommittedBatchLineageDetail }) {
  const duration = entry.kind === 'attempt' && entry.finishedAt ? Date.parse(entry.finishedAt) - Date.parse(entry.occurredAt) : NaN;
  return <>
    <p>{statusNames[entry.status]} · {scopeName(entry)}{entry.status === 'no_new_data' && ' · Checkpoint unchanged; no data moved.'}</p>
    {entry.status === 'started' && <p>A request was recorded. This is not evidence that a worker is running.</p>}
    <dl>
      <dt>{entry.kind === 'receipt' ? 'Recorded at' : 'Requested at'}</dt><dd><Time value={entry.occurredAt} /></dd>
      {entry.finishedAt && <><dt>Finished at</dt><dd><Time value={entry.finishedAt} /></dd></>}
      {Number.isFinite(duration) && duration >= 0 && <><dt>Elapsed</dt><dd>{duration.toLocaleString()} ms</dd></>}
      <dt>Entry ID</dt><dd><code>{entry.id}</code></dd>
      {entry.kind === 'attempt' && entry.receiptId && <><dt>Result ID</dt><dd><code>{entry.receiptId}</code></dd></>}
      {entry.rowsAffected != null && <><dt>Rows affected</dt><dd>{entry.rowsAffected}</dd></>}
      {entry.eventsRead != null && <><dt>Event rows read</dt><dd>{entry.eventsRead}</dd></>}
      {entry.fromExclusive != null && <><dt>Prior checkpoint</dt><dd><code>{entry.fromExclusive}</code></dd></>}
      {entry.throughInclusive != null && <><dt>Committed through</dt><dd><code>{entry.throughInclusive}</code></dd></>}
      {entry.sourceHighWater != null && <><dt>Observed source head</dt><dd><code>{entry.sourceHighWater}</code></dd></>}
      {entry.trigger && <><dt>Trigger</dt><dd>{entry.trigger === 'daily_utc' ? 'Daily UTC schedule' : 'Manual'}</dd></>}
      {entry.code && <><dt>Diagnostic</dt><dd><code>{entry.code}</code></dd></>}
      {entry.job && <><dt>Schedule outcome</dt><dd>{entry.job.state.replace(/_/g, ' ')}</dd><dt>Scheduled slot</dt><dd><Time value={entry.job.slot} /></dd>
        {entry.job.dispatchedAt && <><dt>Dispatched at</dt><dd><Time value={entry.job.dispatchedAt} /></dd></>}</>}
    </dl>
  </>;
}

import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { canonicalTransferReceipt } from './transferReceiptFocus';
import { fetchObservedTableLineage } from '../../services/api';
import type { ObservedTableLineage as Observation, TableLineageDirection, TableLineageIdentity } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';

const horizonMs = 7 * 86_400_000;
const name = ({ schema, table }: TableLineageIdentity) => `${schema}.${table}`;

export default function ObservedTableLineage({ table, version }: { table: string; version: number }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, table, version])} table={table} /> : null;
}

function Workspace({ table }: { table: string }) {
  const title = useId();
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState<TableLineageDirection>('incoming');
  return <section className="flow-lineage-reference-path" aria-labelledby={title}>
    <h3 id={title}>Observed transfers</h3>
    <p>Verified local sandbox evidence of past transfers. Declared relationships and foreign-key references do not prove that data moved. This is not a live or production map.</p>
    <button type="button" className="flow-lineage-action" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? 'Hide observed transfers' : 'Show observed transfers'}
    </button>
    {open && <>
      <label>Transfer direction<select aria-label="Transfer direction" value={direction} onChange={event =>
        setDirection(event.target.value === 'outgoing' ? 'outgoing' : 'incoming')}>
        <option value="incoming">Incoming</option><option value="outgoing">Outgoing</option>
      </select></label>
      <ObservedDirection key={direction} table={table} direction={direction} />
    </>}
  </section>;
}

function ObservedDirection({ table, direction }: { table: string; direction: TableLineageDirection }) {
  const source = { schema: 'ts_transfer_sandbox', table };
  const [snapshot, setSnapshot] = useState<Observation>();
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const request = new AbortController();
    void fetchObservedTableLineage(source, direction, request.signal).then(result => {
      if (request.signal.aborted) return;
      if (result.direction === direction) { setSnapshot(result); setNow(Date.now()); }
      else setFailed(true);
    }).catch(() => { if (!request.signal.aborted) setFailed(true); });
    return () => request.abort();
  }, [table, direction]);

  useEffect(() => {
    if (!snapshot?.available) return;
    const expirations = snapshot.neighbors.map(item => Date.parse(item.receiptRecordedAt) + horizonMs)
      .filter(expiration => Number.isFinite(expiration) && expiration > now);
    const timer = expirations.length ? window.setTimeout(() => setNow(Date.now()), Math.max(1, Math.min(...expirations) - Date.now())) : undefined;
    const update = () => setNow(Date.now());
    document.addEventListener('visibilitychange', update);
    return () => { if (timer != null) window.clearTimeout(timer); document.removeEventListener('visibilitychange', update); };
  }, [snapshot, now]);

  if (failed || snapshot && !snapshot.available) return <p role="status">No current observed transfer evidence is available.</p>;
  if (!snapshot) return <p role="status">Checking observed sandbox receipts…</p>;
  const rows = snapshot.neighbors.filter(item => item.schema === source.schema && Number.isSafeInteger(item.rowsAffected) && item.rowsAffected > 0 &&
    Number.isFinite(Date.parse(item.receiptRecordedAt)) && Date.parse(item.receiptRecordedAt) <= now && Date.parse(item.receiptRecordedAt) + horizonMs > now);
  return <>
    <p>Only committed receipts that affected rows in the last seven days and can be verified for these physical tables are shown. Older or unverified evidence is not shown.</p>
    {rows.length ? <ol className="flow-lineage-table-references__list">{rows.map(item => <li key={JSON.stringify([item.schema, item.table, item.receiptId])}>
      <strong>Verified positive receipt</strong>
      <span className="flow-lineage-table-references__route"><code>{name(direction === 'incoming' ? item : source)}</code>
        <span aria-label="transferred to"> → </span><code>{name(direction === 'incoming' ? source : item)}</code></span>
      <span>Trigger: {item.trigger === 'daily_utc' ? 'Daily UTC' : item.trigger === 'weekly_utc' ? 'Weekly UTC' : item.trigger == null ? 'Manual' : 'Not verified'}</span>
      <span>Rows affected: {item.rowsAffected.toLocaleString()}</span>
      <span>Receipt: <code>{item.receiptId}</code></span>
      {canonicalTransferReceipt(item.receiptId) && <Link className="flow-evidence-lineage-link" to={`/ontology/explorer?${new URLSearchParams({
        schema: source.schema, table: direction === 'incoming' ? table : item.table, view: 'evidence', transferReceipt: item.receiptId,
      })}`}>Open receipt in Explorer</Link>}
      <span>Receipt recorded at: <time dateTime={item.receiptRecordedAt}>{new Date(item.receiptRecordedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time></span>
    </li>)}</ol> : <p role="status">No current observed transfers in this direction.</p>}
    {snapshot.truncated && rows.length > 0 && <p>Only the first bounded set of observed neighbors is shown.</p>}
  </>;
}

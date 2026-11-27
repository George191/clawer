import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, NativeSelect } from '@/components/ui';
import { fetchIndexedProjectionCapability, queryIndexedProjection } from '../../services/api';
import type { IndexedIdSamplePredicate, IndexedProjectionField, IndexedProjectionResult } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt as uuid } from './transferReceiptFocus';

type Scope = { schema: string; table: string; identity: string };
const integer = (value: unknown): value is string => typeof value === 'string' && /^(0|-?[1-9]\d{0,18})$/.test(value) &&
  BigInt(value) >= -9223372036854775808n && BigInt(value) <= 9223372036854775807n;
const current = (identity: string, signal?: AbortSignal) => {
  const session = getSessionState();
  return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
};
const approved = (fields?: IndexedProjectionField[]): fields is IndexedProjectionField[] =>
  Array.isArray(fields) && fields.length >= 2 && fields.length <= 3 && fields[0]?.type === 'bigint' &&
  fields.every((field, index) => field && typeof field.name === 'string' && /^[a-z_][a-z0-9_]{0,62}$/.test(field.name) &&
    (index === 0 || field.type === 'boolean' || field.type === 'varchar(32)')) &&
  new Set(fields.map(field => field.name)).size === fields.length;

function matches(result: IndexedProjectionResult, fields: IndexedProjectionField[], predicate: IndexedIdSamplePredicate, projectionRevision: string) {
  if (result.state !== 'sampled' || result.reason !== 'index_path' || result.projectionRevision !== projectionRevision || !approved(result.fields) ||
      result.fields.length !== fields.length || !result.fields.every((field, index) => field.name === fields[index].name && field.type === fields[index].type) ||
      !Array.isArray(result.rows) || result.rows.length > 20 ||
      typeof result.hasMore !== 'boolean' || (result.hasMore && result.rows.length !== 20)) return false;
  let last: bigint | undefined;
  const lower = BigInt('idEquals' in predicate ? predicate.idEquals : predicate.idFrom);
  const upper = BigInt('idEquals' in predicate ? predicate.idEquals : predicate.idTo);
  return result.rows.every(row => {
    if (!Array.isArray(row) || row.length !== fields.length || !integer(row[0])) return false;
    const id = BigInt(row[0]);
    if (id < lower || id > upper || (last !== undefined && id <= last)) return false;
    last = id;
    return row.slice(1).every((value, index) =>
      value === null || (fields[index + 1].type === 'boolean' ? typeof value === 'boolean' :
        typeof value === 'string' && [...value].length <= 32));
  });
}

export default function ExplorerIndexedProjection(props: Scope) {
  const [profile, setProfile] = useState<{ fields: IndexedProjectionField[]; projectionRevision: string }>();
  const [loading, setLoading] = useState(true);
  const { schema, table, identity } = props;
  useEffect(() => {
    const read = new AbortController();
    setProfile(undefined); setLoading(true);
    void fetchIndexedProjectionCapability(schema, table, read.signal).then(value => {
      if (current(identity, read.signal) && value.available && value.limit === 20 && approved(value.fields) &&
          typeof value.projectionRevision === 'string' && uuid(value.projectionRevision))
        setProfile({ fields: value.fields, projectionRevision: value.projectionRevision });
    }).catch(() => { /* Unavailable without a fallback. */ }).finally(() => {
      if (current(identity, read.signal)) setLoading(false);
    });
    return () => read.abort();
  }, [schema, table, identity]);
  if (loading) return <p role="status"><LoadingIndicator size={20}>Checking approved projection metadata…</LoadingIndicator></p>;
  if (!profile) return <p role="status">Verified indexed projection is unavailable. No preview query was run.</p>;
  return <ProjectionFilter key={profile.projectionRevision} {...props} {...profile} />;
}

function ProjectionFilter({ fields, projectionRevision, ...scope }: Scope & { fields: IndexedProjectionField[]; projectionRevision: string }) {
  const [mode, setMode] = useState<'eq' | 'range'>('eq');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const predicate = integer(start) ?
    mode === 'eq' ? { idEquals: start } : integer(end) && BigInt(start) <= BigInt(end) ? { idFrom: start, idTo: end } : undefined : undefined;
  return <>
    <p>Approved fields: {fields.map(field => <code key={field.name}>{field.name} ({field.type}){' '}</code>)}</p>
    <p>Enter a signed 64-bit integer for the reviewed key <code>{fields[0].name}</code>. Only these approved fields can be returned.</p>
    <label>Comparison <NativeSelect aria-label="Comparison" value={mode} onChange={event => setMode(event.target.value === 'range' ? 'range' : 'eq')}>
      <option value="eq">Equals</option><option value="range">Inclusive range</option>
    </NativeSelect></label>
    <label>{mode === 'eq' ? 'Key value' : 'From key'} <Input aria-label={mode === 'eq' ? 'Key value' : 'From key'} value={start}
      maxLength={64} onChange={event => setStart(event.target.value)} /></label>
    {mode === 'range' && <label>Through key <Input aria-label="Through key" value={end} maxLength={64}
      onChange={event => setEnd(event.target.value)} /></label>}
    <ProjectionAction key={JSON.stringify([mode, start, end])} {...scope} fields={fields} projectionRevision={projectionRevision} predicate={predicate} />
  </>;
}

function ProjectionAction({ schema, table, identity, fields, projectionRevision, predicate }: Scope & {
  fields: IndexedProjectionField[]; projectionRevision: string; predicate?: IndexedIdSamplePredicate;
}) {
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IndexedProjectionResult>();
  const [notice, setNotice] = useState('');
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const run = async () => {
    if (!review || !predicate || pending.current || !current(identity)) return;
    const read = new AbortController(); pending.current = read;
    setReview(false); setBusy(true); setResult(undefined); setNotice('');
    try {
      const value = await queryIndexedProjection(schema, table, predicate, projectionRevision, read.signal);
      if (!current(identity, read.signal)) return;
      if (matches(value, fields, predicate, projectionRevision)) setResult(value);
      else setNotice(value.reason === 'unsupported_plan' ?
        'The covering index path could not be verified. No fallback query was used.' :
        'Verified indexed projection is unavailable. No fallback query was used.');
    } catch {
      if (current(identity, read.signal)) setNotice('Verified indexed projection is unavailable. No fallback query was used.');
    } finally {
      if (pending.current === read) { pending.current = undefined; if (current(identity, read.signal)) setBusy(false); }
    }
  };
  return <section className="flow-filter-index-coverage" aria-label="Index projection action">
    <Button size="small" disabled={!predicate || busy} loading={busy} onClick={() => setReview(true)}>Review projected preview</Button>
    {busy && <Button size="small" onClick={() => {
      pending.current?.abort(); pending.current = undefined; setBusy(false); setResult(undefined);
      setNotice('Stopped waiting. No preview is shown. Server work may take time to stop.');
    }}>Stop projected preview</Button>}
    {notice && <p role="status">{notice}</p>}
    {result && <>
      <p role="status">{result.rows.length} approved rows returned. {result.hasMore ? 'More rows match; only the first 20 are shown.' : ''}</p>
      {result.rows.length > 0 && <div className="flow-schema-column-grid"><table aria-label="Approved projected rows">
        <thead><tr>{fields.map(field => <th key={field.name} scope="col">{field.name}</th>)}</tr></thead>
        <tbody>{result.rows.map(row => <tr key={String(row[0])}>{row.map((cell, index) =>
          <td key={fields[index].name}><code>{cell === null ? 'NULL' : cell === '' ? '""' : String(cell)}</code></td>)}</tr>)}</tbody>
      </table></div>}
    </>}
    <Modal open={review} destroyOnHidden className="flow-surface-modal" title="Review projected preview?"
      onCancel={() => setReview(false)} onOk={run} okText="Confirm projected preview">
      <p>Read up to 20 rows from <code>{schema}.{table}</code> using a verified covering index path. Only the listed fields can be returned by this preview. No fallback query is used.</p>
      <p>Approved fields: {fields.map(field => <code key={field.name}>{field.name}{' '}</code>)}</p>
      {predicate && <p><code>{fields[0].name}</code>: {'idEquals' in predicate ? <>equals <code>{predicate.idEquals}</code></> :
        <><code>{predicate.idFrom}</code> through <code>{predicate.idTo}</code>, inclusive</>}.</p>}
    </Modal>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchIndexedCompoundSample, queryIndexedCompoundSample } from '../../services/api';
import type { IndexedCompoundSamplePredicate, IndexedIdSampleResult } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { ExplorerFilter } from './explorerFilters';
import { sandboxScopeDenied } from './SandboxMarkings';

const int64 = (value: string) => typeof value === 'string' && /^(0|-?[1-9]\d{0,18})$/.test(value) &&
  BigInt(value) >= -9223372036854775808n && BigInt(value) <= 9223372036854775807n;

export function compoundSamplePredicate(rules: ExplorerFilter[], types: (string | undefined)[]): IndexedCompoundSamplePredicate | undefined {
  if (rules.length !== 3 || !rules.every((rule, index) => rule.active && rule.join !== 'or' &&
    !rule.id.startsWith('group-') && (types[index] === 'bigint' || types[index] === 'int8'))) return;
  const tenant = rules.find(rule => rule.field === 'tenant_id' && rule.operator === '=');
  const group = rules.find(rule => rule.field === 'group_id' && rule.operator === '=');
  const range = rules.find(rule => rule.field === 'id' && rule.operator === '介于');
  if (!tenant || !group || !range || !int64(tenant.value) || !int64(group.value)) return;
  const parts = range.value.split(',').map(part => part.trim());
  if (parts.length !== 2 || !parts.every(int64) || BigInt(parts[0]) > BigInt(parts[1])) return;
  return { tenantId: tenant.value, groupId: group.value, idFrom: parts[0], idTo: parts[1] };
}

type Scope = { schema: string; table: string; identity: string };
function live(identity: string, signal?: AbortSignal) {
  const session = getSessionState();
  return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
}

export default function ExplorerIndexedCompoundSample({ schema, table, identity, predicate, reviewKey }: Scope & {
  predicate: IndexedCompoundSamplePredicate; reviewKey: string;
}) {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    const read = new AbortController();
    void fetchIndexedCompoundSample(schema, table, read.signal).then(result => {
      if (live(identity, read.signal)) setAvailable(result.available === true && result.limit === 20);
    }).catch(() => { if (live(identity, read.signal)) setAvailable(false); });
    return () => read.abort();
  }, [schema, table, identity]);
  return available ? <SampleAction key={reviewKey} schema={schema} table={table} identity={identity} predicate={predicate}
    onScopeDenied={() => setAvailable(false)} /> : null;
}

function matches(result: IndexedIdSampleResult, predicate: IndexedCompoundSamplePredicate) {
  if (result.state !== 'sampled' || result.reason !== 'index_path' || !Array.isArray(result.ids) || result.ids.length > 20 ||
    typeof result.hasMore !== 'boolean' || result.hasMore && result.ids.length !== 20) return false;
  const from = BigInt(predicate.idFrom), to = BigInt(predicate.idTo);
  let previous: bigint | undefined;
  return result.ids.every(value => {
    if (!int64(value)) return false;
    const id = BigInt(value);
    const ok = id >= from && id <= to && (previous === undefined || id > previous);
    previous = id;
    return ok;
  });
}

function SampleAction({ schema, table, identity, predicate, onScopeDenied }: Scope & {
  predicate: IndexedCompoundSamplePredicate; onScopeDenied: () => void;
}) {
  const pending = useRef<AbortController>();
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [result, setResult] = useState<IndexedIdSampleResult>();
  const [notice, setNotice] = useState('');
  useEffect(() => () => pending.current?.abort(), []);

  const sample = async () => {
    if (!review || pending.current || blocked || !live(identity)) return;
    const read = new AbortController();
    pending.current = read;
    setReview(false); setResult(undefined); setNotice(''); setBusy(true);
    try {
      const value = await queryIndexedCompoundSample(schema, table, predicate, read.signal);
      if (!live(identity, read.signal)) return;
      if (matches(value, predicate)) setResult(value);
      else {
        if (value.reason === 'scope_unavailable') setBlocked(true);
        setNotice(value.reason === 'unsupported_plan' ? 'Compound ID sample blocked: the required index path could not be verified. No fallback query was used.'
          : 'No indexed compound ID sample is available. No fallback query was used.');
      }
    } catch (failure) {
      if (!live(identity, read.signal)) return;
      if (sandboxScopeDenied(failure)) { onScopeDenied(); return; }
      setNotice('No indexed compound ID sample is available. No fallback query was used.');
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(identity, read.signal)) setBusy(false); }
    }
  };
  const stop = () => {
    pending.current?.abort(); pending.current = undefined;
    setBusy(false); setResult(undefined);
    setNotice('Stopped waiting. No sample is shown. The server may take time to stop.');
  };
  return <section className="flow-filter-index-coverage" aria-label="Indexed compound ID sample">
    <strong>Indexed compound ID sample</strong>
    <Button size="small" disabled={busy || blocked || review} onClick={() => setReview(true)}>Review indexed compound sample</Button>
    {busy && <Button size="small" onClick={stop}>Stop compound ID sample</Button>}
    {busy && <p role="status">Verifying the index path for up to 20 returned IDs…</p>}
    {notice && <p role="status">{notice}</p>}
    {result && <><p role="status">{result.ids.length} verified compound sample IDs returned. {result.hasMore ? 'More IDs match; only the first 20 are shown.' : ''}</p>
      {result.ids.length > 0 && <ul aria-label="Compound sampled IDs">{result.ids.map(id => <li key={id}><code>{id}</code></li>)}</ul>}
    </>}
    <Modal open={review} destroyOnHidden className="flow-surface-modal" title="Review indexed compound ID sample?"
      onCancel={() => setReview(false)} onOk={sample} okText="Confirm compound ID sample">
      <p>Read up to 20 IDs from <code>{schema}.{table}</code> using a verified compound index path. No other columns or fallback query are used.</p>
      <p><code>tenant_id</code> = <code>{predicate.tenantId}</code> and <code>group_id</code> = <code>{predicate.groupId}</code>;
        <code>id</code> from <code>{predicate.idFrom}</code> through <code>{predicate.idTo}</code>, inclusive.</p>
    </Modal>
  </section>;
}

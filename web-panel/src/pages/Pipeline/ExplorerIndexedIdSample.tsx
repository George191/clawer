import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, NativeSelect } from '@/components/ui';
import { fetchIndexedIdSample, queryIndexedIdSample } from '../../services/api';
import type { IndexedIdSampleCapability, IndexedIdSamplePredicate, IndexedIdSampleResult } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { ExplorerFilter } from './explorerFilters';

const int64 = (value: string) => /^(0|-?[1-9]\d{0,18})$/.test(value) &&
  BigInt(value) >= -9223372036854775808n && BigInt(value) <= 9223372036854775807n;
type Candidate = Pick<ExplorerFilter, 'field' | 'operator' | 'value'>;

function predicateFor(candidate?: Candidate): IndexedIdSamplePredicate | undefined {
  if (!candidate) return;
  if (candidate.operator === '=' && int64(candidate.value)) return { idEquals: candidate.value };
  if (candidate.operator === '介于') {
    const parts = candidate.value.split(',').map(value => value.trim());
    if (parts.length === 2 && parts.every(int64) && BigInt(parts[0]) <= BigInt(parts[1]))
      return { idFrom: parts[0], idTo: parts[1] };
  }
}

export default function ExplorerIndexedIdSample({ schema, table, candidate, required = false, identity }: {
  schema: string; table: string; candidate?: Candidate; required?: boolean; identity?: string;
}) {
  const [capability, setCapability] = useState<IndexedIdSampleCapability>();
  useEffect(() => {
    const read = new AbortController();
    void fetchIndexedIdSample(schema, table, read.signal).then(value => {
      if (!read.signal.aborted && value.available && value.keyColumn && value.limit === 20) setCapability(value);
    }).catch(() => { if (!read.signal.aborted) setCapability(undefined); });
    return () => read.abort();
  }, [schema, table]);
  if (!capability?.available || !capability.keyColumn) return required
    ? <p role="status">Verified-index ID sampling is unavailable. No preview query was run.</p> : null;
  if (required && identity) return <RequiredSample schema={schema} table={table} field={capability.keyColumn} identity={identity} />;
  return <SampleAction key={JSON.stringify(candidate ?? null)} schema={schema} table={table} field={capability.keyColumn}
    predicate={candidate?.field === capability.keyColumn ? predicateFor(candidate) : undefined} />;
}

function RequiredSample({ schema, table, field, identity }: { schema: string; table: string; field: string; identity: string }) {
  const [operator, setOperator] = useState<'=' | '介于'>('=');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const candidate = { field, operator, value: operator === '=' ? start : `${start},${end}` };
  return <>
    <p>Reviewed key: <code>{field}</code>. Enter signed 64-bit integers without leading zeros.</p>
    <label>Comparison <NativeSelect aria-label="Comparison" value={operator} onChange={event => setOperator(event.target.value === '介于' ? '介于' : '=')}>
      <option value="=">Equals</option><option value="介于">Inclusive range</option>
    </NativeSelect></label>
    <label>{operator === '=' ? 'Key value' : 'From key'} <Input aria-label={operator === '=' ? 'Key value' : 'From key'} value={start}
      maxLength={64} onChange={event => setStart(event.target.value)} /></label>
    {operator === '介于' && <label>Through key <Input aria-label="Through key" value={end} maxLength={64}
      onChange={event => setEnd(event.target.value)} /></label>}
    <SampleAction key={JSON.stringify([operator, start, end])} schema={schema} table={table} field={field}
      predicate={predicateFor(candidate)} identity={identity} />
  </>;
}

function SampleAction({ schema, table, field, predicate, identity }: { schema: string; table: string; field: string; predicate?: IndexedIdSamplePredicate; identity?: string }) {
  const request = useRef<AbortController>();
  const [review, setReview] = useState<IndexedIdSamplePredicate>();
  const [sample, setSample] = useState<IndexedIdSampleResult>();
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [denied, setDenied] = useState(false);
  useEffect(() => () => request.current?.abort(), []);
  const current = () => {
    if (!identity) return true;
    const session = getSessionState();
    return session.authenticated && !session.locked && session.identity === identity;
  };
  const run = async () => {
    if (!review || request.current || denied || !current()) return;
    const confirmed = review;
    setReview(undefined); setSample(undefined); setNotice(''); setBusy(true);
    const read = new AbortController(); request.current = read;
    try {
      const result = await queryIndexedIdSample(schema, table, confirmed, read.signal);
      if (read.signal.aborted || !current()) return;
      if (result.state === 'sampled' && result.reason === 'index_path' && result.ids.length <= 20 && result.ids.every(int64)) setSample(result);
      else {
        if (result.reason === 'scope_unavailable') setDenied(true);
        setNotice(result.reason === 'unsupported_plan' ? 'The ID sample was blocked because the required index path could not be verified. No alternative query was used.'
          : 'No verified ID sample is available. No alternative query was used.');
      }
    } catch (failure) {
      if (!read.signal.aborted && current()) {
        const status = (failure as { response?: { status?: number } })?.response?.status;
        if (status === 401 || status === 403 || status === 404) setDenied(true);
        setNotice('No verified ID sample is available. No alternative query was used.');
      }
    } finally {
      if (request.current === read) { request.current = undefined; setBusy(false); }
    }
  };
  const stop = () => {
    request.current?.abort(); request.current = undefined; setBusy(false); setSample(undefined);
    setNotice('Stopped waiting. No ID sample is shown. Server work may take time to stop.');
  };
  return <section className="flow-filter-index-coverage" aria-label="Verified-index ID sample">
    <strong>Verified-index ID sample</strong>
    <Button size="small" disabled={!predicate || busy || denied} onClick={() => setReview(predicate)}>Review ID sample</Button>
    {busy && <Button size="small" onClick={stop}>Stop ID sample</Button>}
    {!predicate && <p>Use one equality or inclusive Between condition on the reviewed key <code>{field}</code>.</p>}
    {busy && <p role="status">Verifying index path and reading at most 20 IDs…</p>}
    {notice && <p role="status">{notice}</p>}
    {sample && <>
      <p role="status">{sample.ids.length} verified IDs returned. {sample.hasMore ? 'More matches exist; this sample is limited.' : ''}</p>
      {sample.ids.length > 0 && <ul aria-label="Sampled IDs">{sample.ids.map(id => <li key={id}><code>{id}</code></li>)}</ul>}
    </>}
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title="Review verified-index ID sample?"
      onCancel={() => setReview(undefined)} onOk={run} okText="Confirm ID sample">
      {review && <>
        <p>Read up to 20 IDs from <code>{schema}.{table}</code>. PostgreSQL must verify a supported index path before reading any IDs. No other columns or fallback query are used.</p>
        <p>Reviewed key <code>{field}</code>: {'idEquals' in review ? <>equals <code>{review.idEquals}</code></>
          : <><code>{review.idFrom}</code> through <code>{review.idTo}</code>, inclusive</>}.</p>
      </>}
    </Modal>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { fetchIndexPreviewSandbox, queryIndexPreviewSandbox } from '../../services/api';
import type { SandboxIndexPreviewQuery, SandboxIndexPreviewResult } from '../../services/api';
import './sandbox-index-preview.css';

export const INDEX_PREVIEW_SCHEMA = 'ts_index_preview_sandbox';
export const INDEX_PREVIEW_TABLE = 'records';

const isBigint = (value: string) => /^(0|-?[1-9]\d{0,18})$/.test(value) &&
  BigInt(value) >= -9223372036854775808n && BigInt(value) <= 9223372036854775807n;
const failureLabels: Record<SandboxIndexPreviewResult['reason'], string> = {
  index_path: 'Index path verified',
  table_unavailable: 'The reviewed table could not be verified.',
  primary_index_required: 'The leading primary-key index does not meet the preview requirements.',
  unsupported_plan: 'PostgreSQL did not choose a supported index path.',
  query_unavailable: 'The bounded query could not be completed.',
};

export default function SandboxIndexPreview() {
  const [available, setAvailable] = useState<boolean>();
  const [availabilityError, setAvailabilityError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [predicate, setPredicate] = useState<'equals' | 'range'>('equals');
  const [equals, setEquals] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [maximum, setMaximum] = useState('25');
  const [plan, setPlan] = useState<SandboxIndexPreviewResult>();
  const [preview, setPreview] = useState<SandboxIndexPreviewResult>();
  const [requestError, setRequestError] = useState('');
  const [pending, setPending] = useState<SandboxIndexPreviewQuery['mode']>();
  const request = useRef<AbortController>();

  useEffect(() => {
    const controller = new AbortController();
    setAvailabilityError(false); setAvailable(undefined);
    void fetchIndexPreviewSandbox(controller.signal).then(value => {
      if (!controller.signal.aborted) setAvailable(value.available);
    }).catch(() => { if (!controller.signal.aborted) setAvailabilityError(true); });
    return () => { controller.abort(); request.current?.abort(); request.current = undefined; };
  }, [revision]);

  const clear = () => {
    request.current?.abort(); request.current = undefined;
    setPending(undefined); setPlan(undefined); setPreview(undefined); setRequestError('');
  };
  const validLimit = /^[1-9]\d{0,2}$/.test(maximum) && Number(maximum) <= 100;
  const validKey = predicate === 'equals' ? isBigint(equals)
    : Boolean(from || to) && (!from || isBigint(from)) && (!to || isBigint(to)) && (!from || !to || BigInt(from) <= BigInt(to));
  const valid = available && validLimit && validKey;
  const mayPreview = plan?.status === 'allowed' && plan.plan !== null && preview?.status !== 'blocked';
  const run = async (mode: SandboxIndexPreviewQuery['mode']) => {
    if (!valid || request.current || (mode === 'preview' && !mayPreview)) return;
    const controller = new AbortController();
    request.current = controller; setPending(mode); setRequestError(''); setPreview(undefined);
    if (mode === 'explain') setPlan(undefined);
    try {
      const result = await queryIndexPreviewSandbox({
        mode, limit: Number(maximum),
        ...(predicate === 'equals' ? { idEquals: equals } : { idFrom: from || undefined, idTo: to || undefined }),
      }, controller.signal);
      if (controller.signal.aborted) return;
      if (result.mode !== mode) { setRequestError('The response does not match the requested mode.'); return; }
      if (mode === 'explain') setPlan(result); else setPreview(result);
    } catch {
      if (!controller.signal.aborted) setRequestError(mode === 'explain' ? 'The plan check could not be completed.'
        : 'Preview could not be verified. No alternative query was used.');
    } finally {
      if (request.current === controller) { request.current = undefined; setPending(undefined); }
    }
  };

  return <section className="flow-schema-details flow-index-preview" aria-label="Index-aware sandbox preview">
    <header><h3>Index-aware sandbox preview</h3><p>Fixed relation: <code>{INDEX_PREVIEW_SCHEMA}.{INDEX_PREVIEW_TABLE}</code>. No generic preview fallback.</p></header>
    {availabilityError ? <><p role="alert">Sandbox preview availability could not be checked.</p>
      <Button size="small" onClick={() => setRevision(value => value + 1)}>Retry availability</Button></>
      : available === undefined ? <p role="status">Checking sandbox preview availability…</p>
        : !available ? <p>Index-aware sandbox preview is disabled in this environment.</p> : <>
    <section className="flow-index-preview__query" aria-label="Primary key query">
      <label>ID predicate
        <select aria-label="ID predicate" value={predicate} onChange={event => {
          clear(); setPredicate(event.target.value === 'range' ? 'range' : 'equals');
        }}>
          <option value="equals">Equals</option><option value="range">Inclusive range</option>
        </select>
      </label>
      {predicate === 'equals' ? <label>ID equals<Input aria-label="ID equals" value={equals} maxLength={20}
        onChange={event => { clear(); setEquals(event.target.value); }} /></label>
        : <><label>ID from<Input aria-label="ID from" value={from} maxLength={20} placeholder="No lower bound"
          onChange={event => { clear(); setFrom(event.target.value); }} /></label>
          <label>ID to<Input aria-label="ID to" value={to} maxLength={20} placeholder="No upper bound"
            onChange={event => { clear(); setTo(event.target.value); }} /></label></>}
      <label>Maximum rows<Input aria-label="Maximum rows" value={maximum} maxLength={3} inputMode="numeric"
        onChange={event => { clear(); setMaximum(event.target.value); }} /></label>
    </section>
    <p>Use an exact signed 64-bit ID, or an ordered range with at least one inclusive bound. Preview returns up to 100 rows in ascending ID order.</p>
    {!validLimit && <p role="status">Maximum rows must be between 1 and 100.</p>}
    <div className="flow-index-preview__actions">
      <Button size="small" disabled={!valid || Boolean(pending)} loading={pending === 'explain'} onClick={() => run('explain')}>Check index plan</Button>
      <Button size="small" type="primary" disabled={!valid || !mayPreview || Boolean(pending)}
        loading={pending === 'preview'} onClick={() => run('preview')}>Run indexed preview</Button>
    </div>
    {requestError && <p role="alert">{requestError}</p>}
    <section className="flow-evidence-card" aria-label="Index plan">
      <header><h4>Index plan</h4><span>Plan-only · no business rows read</span></header>
      {!plan ? <p>No plan check yet.</p> : <PlanStatus result={plan} />}
    </section>
    <section className="flow-evidence-card" aria-label="Indexed preview rows">
      <header><h4>Indexed preview rows</h4><span>Explicit bounded query</span></header>
      {!preview ? <p>No data query run.</p> : preview.status === 'blocked' ? <PlanStatus result={preview} />
        : preview.rows === null || !preview.plan ? <p role="alert">The preview returned no verified row result.</p> : <>
          <p role="status">{preview.rows.length} rows returned · Ascending ID</p>
          {preview.rows.length > 0 && <div className="flow-index-preview__table"><table aria-label="Indexed preview results">
            <thead><tr><th scope="col">id</th><th scope="col">label</th></tr></thead>
            <tbody>{preview.rows.map(row => <tr key={row.id}><th scope="row"><code>{row.id}</code></th><td><code>{row.label}</code></td></tr>)}</tbody>
          </table></div>}
          {preview.hasMore && <p>More matching rows exist. Narrow the ID range before running another preview.</p>}
        </>}
    </section>
    </>}
  </section>;
}

function PlanStatus({ result }: { result: SandboxIndexPreviewResult }) {
  return result.status === 'allowed' && result.plan ? <>
    <strong role="status">Index path verified</strong>
    <p>{result.plan.scan === 'index_only' ? 'Index-only scan' : 'Index scan'} · Leading key <code>{result.plan.key}</code> · Ascending order</p>
  </> : <>
    <strong role="status">Blocked</strong>
    <p>{failureLabels[result.reason]} {result.guidance}</p>
    <p>{result.mode === 'explain' || result.reason !== 'query_unavailable'
      ? 'No business-row query was run. No alternative query was used.'
      : 'No verified preview rows were returned, and no alternative query was used.'}</p>
  </>;
}

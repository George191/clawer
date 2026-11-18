import { useEffect, useState } from 'react';
import { Button, Input } from '@/components/ui';
import type { AssetFreshness, AssetHeapBounds, AssetQualityCheck, AssetSchemaColumn, AssetSchemaDrift } from '../../services/api';

const resultLabels: Record<AssetQualityCheck['state'], string> = {
  not_configured: 'Not configured', not_checked: 'Not checked', healthy: 'Pass', unhealthy: 'Fail',
  unknown: 'Unknown', paused: 'Paused',
};
const resultReasons: Record<string, string> = {
  checking_disabled: 'Scheduled evaluation is disabled.',
  plan_changed: 'The reviewed transfer plan changed.',
  target_changed: 'The physical destination table changed.',
  evaluation_stale: 'This evaluation has expired. Refresh Evidence after the scheduled evaluator runs.',
  schema_baseline_required: 'Capture a reviewed schema baseline before checking for drift.',
  schema_drift: 'The catalog schema differs from the reviewed baseline.',
  schema_unavailable: 'The catalog schema could not be verified.',
  heap_size_unavailable: 'The PostgreSQL main heap size could not be verified.',
  below_minimum: 'The heap size is below the configured minimum.',
  above_maximum: 'The heap size exceeds the configured maximum.',
};

export type QualityDraft = { schema: boolean; heap: boolean; min: string; max: string };
export const readQualityDraft = (policy: AssetFreshness['policy']): QualityDraft => ({
  schema: policy?.schemaCheckEnabled ?? false, heap: Boolean(policy?.heapSize),
  min: policy?.heapSize?.minBytes ?? '', max: policy?.heapSize?.maxBytes ?? '',
});

export function qualityBounds(draft: QualityDraft): { valid: boolean; value: AssetHeapBounds | null } {
  if (!draft.heap) return { valid: true, value: null };
  const byteLimit = 9223372036854775807n;
  const bound = (input: string) => /^\d{1,19}$/.test(input) && BigInt(input) <= byteLimit ? BigInt(input) : null;
  const min = bound(draft.min);
  const max = bound(draft.max);
  const valid = (!draft.min || min !== null) && (!draft.max || max !== null) &&
    (min !== null || max !== null) && (min === null || max === null || min <= max);
  return { valid, value: valid ? { minBytes: min?.toString() ?? null, maxBytes: max?.toString() ?? null } : null };
}

const dateText = (value: string) => new Date(value).toLocaleString();
function byteText(value: string | null) {
  return value !== null ? `${BigInt(value).toLocaleString()} bytes` : 'Not recorded';
}

export function AssetSchemaColumns({ columns }: { columns: AssetSchemaColumn[] }) {
  return <div className="flow-evidence-quality-columns">
    <table><caption>Schema columns in catalog order</caption>
      <thead><tr><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Nullable</th><th scope="col">Primary key position</th></tr></thead>
      <tbody>{columns.map(column => <tr key={column.name}>
        <th scope="row"><code>{column.name}</code></th>
        <td>{column.type}</td><td>{column.nullable ? 'Yes' : 'No'}</td><td>{column.primaryKeyPosition ?? '—'}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

const driftLabels = { added: 'Added', removed: 'Removed', changed: 'Changed' } as const;

function SchemaShape({ column }: { column?: AssetSchemaColumn }) {
  if (!column) return <>Not present</>;
  return <><code>{column.type}</code> · Type modifier {column.typmod} · {column.nullable ? 'Nullable' : 'Not nullable'}
    {' · '}{column.primaryKeyPosition == null ? 'Not in primary key' : `Primary key position ${column.primaryKeyPosition}`}</>;
}

function SchemaDriftDetails({ drift }: { drift: AssetSchemaDrift }) {
  if (!Array.isArray(drift.changes) || drift.changes.length > 20 || typeof drift.relativeOrderChanged !== 'boolean' ||
      typeof drift.truncated !== 'boolean' || (!drift.changes.length && !drift.relativeOrderChanged && !drift.truncated)) return null;
  return <details>
    <summary>Recorded schema drift details</summary>
    <p>Compared the reviewed baseline with the catalog observed by this evaluation. This is not a live scan; additions and removals do not imply renames.</p>
    <p>Relative order of retained columns: <strong>{drift.relativeOrderChanged ? 'Changed' : 'Unchanged'}</strong>.</p>
    {drift.truncated && <p>Only the first {drift.changes.length} recorded field changes are shown. Additional changes were omitted.</p>}
    {drift.changes.length > 0 && <div className="flow-evidence-quality-columns">
      <table><caption>Recorded schema changes</caption>
        <thead><tr><th scope="col">Column</th><th scope="col">Change</th><th scope="col">Reviewed baseline</th><th scope="col">Observed catalog</th></tr></thead>
        <tbody>{drift.changes.map(change => <tr key={change.column}>
          <th scope="row"><code>{change.column}</code></th><td>{driftLabels[change.kind]}</td>
          <td><SchemaShape column={change.before} /></td><td><SchemaShape column={change.after} /></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </details>;
}

function CheckResult({ kind, check, allowDrift = false }: { kind: 'schema' | 'heap'; check: AssetQualityCheck; allowDrift?: boolean }) {
  const [expired, setExpired] = useState<AssetQualityCheck>();
  const evaluated = check.state === 'healthy' || check.state === 'unhealthy';
  const until = check.validUntil ? Date.parse(check.validUntil) : NaN;
  const stale = evaluated && (check === expired || !Number.isFinite(until) || until <= Date.now());
  useEffect(() => {
    if (!evaluated || !Number.isFinite(until) || until <= Date.now()) return;
    const expire = () => setExpired(check);
    const timer = window.setTimeout(expire, until - Date.now());
    const onVisibility = () => { if (Date.now() >= until) expire(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [check, evaluated, until]);

  const reason = stale ? 'evaluation_stale' : check.reason;
  return <>
    <div className="flow-evidence-quality-result">
      <strong role="status">{resultLabels[stale ? 'unknown' : check.state]}</strong>
      {check.checkedAt && <span>Last evaluation: <time dateTime={check.checkedAt}>{dateText(check.checkedAt)}</time></span>}
    </div>
    {reason && <p>{resultReasons[reason] ?? reason}</p>}
    {kind === 'schema' && allowDrift && !stale && check.state === 'unhealthy' && check.reason === 'schema_drift' &&
      check.schemaDrift && <SchemaDriftDetails key={check.checkedAt ?? ''} drift={check.schemaDrift} />}
    {kind === 'heap' && <p>Latest evaluated heap size: <strong>{byteText(stale ? null : check.heapBytes)}</strong></p>}
    <details>
      <summary>{kind === 'schema' ? 'Schema history' : 'Heap size history'}</summary>
      {check.history.length ? <ol className="flow-evidence-receipts" aria-label={kind === 'schema' ? 'Schema transitions' : 'Heap size transitions'}>
        {check.history.map(item => <li key={item.id}>
          <div><strong>{resultLabels[item.state]}</strong><span>Policy generation {item.generation}</span></div>
          {item.checkedAt && <div><span>Evaluated</span><time dateTime={item.checkedAt}>{dateText(item.checkedAt)}</time></div>}
          {kind === 'heap' && <div><span>Heap size</span><strong>{byteText(item.heapBytes)}</strong></div>}
          {item.reason && <p>{resultReasons[item.reason] ?? item.reason}</p>}
        </li>)}
      </ol> : <p>No transitions recorded.</p>}
    </details>
  </>;
}

export default function ExplorerAssetQualityChecks({ snapshot, draft, onDraft, onReview, busy, canReview }: {
  snapshot: AssetFreshness; draft: QualityDraft; onDraft: (draft: QualityDraft) => void; onReview: () => void;
  busy: boolean; canReview: boolean;
}) {
  const baseline = snapshot.policy?.schemaBaseline;
  const size = snapshot.policy?.heapSize;
  const formDisabled = busy || !snapshot.available;
  return <div className="flow-evidence-quality-checks">
    <section aria-label="Schema check">
      <h5>Schema check</h5>
      <p>Compare catalog columns, types, nullability and primary key order with an explicitly reviewed baseline.</p>
      <CheckResult kind="schema" check={snapshot.checks.schema} allowDrift={Boolean(snapshot.available && snapshot.policy?.enabled && snapshot.policy.schemaCheckEnabled && baseline)} />
      <label className="flow-evidence-quality-toggle"><input type="checkbox" checked={draft.schema} disabled={formDisabled}
        onChange={event => onDraft({ ...draft, schema: event.target.checked })} />Enable schema check</label>
      {baseline && <details><summary>Reviewed schema baseline</summary>
        <p>Captured at: <time dateTime={baseline.capturedAt}>{dateText(baseline.capturedAt)}</time></p>
        <AssetSchemaColumns columns={baseline.columns} />
      </details>}
      <Button size="small" disabled={!canReview || busy} onClick={onReview}>Review current schema</Button>
      {!snapshot.policy?.schemaCheckEnabled && draft.schema && <p>Save the policy before reviewing a baseline.</p>}
    </section>
    <section aria-label="Heap size check">
      <h5>Heap size check</h5>
      <p>Exact PostgreSQL main heap bytes. Excludes indexes and TOAST; not a row count or estimate.</p>
      <CheckResult kind="heap" check={snapshot.checks.heapSize} />
      {size && <p>Saved bounds: {size.minBytes === null ? 'No minimum' : byteText(size.minBytes)}
        {' · '}{size.maxBytes === null ? 'No maximum' : byteText(size.maxBytes)}</p>}
      <label className="flow-evidence-quality-toggle"><input type="checkbox" checked={draft.heap} disabled={formDisabled}
        onChange={event => onDraft({ ...draft, heap: event.target.checked })} />Enable heap size check</label>
      {draft.heap && <div className="flow-evidence-quality-bounds">
        <label>Minimum heap bytes
          <Input aria-label="Minimum heap bytes" inputMode="numeric" value={draft.min} disabled={formDisabled}
            onChange={event => onDraft({ ...draft, min: event.target.value })} maxLength={19} placeholder="No minimum" />
        </label>
        <label>Maximum heap bytes
          <Input aria-label="Maximum heap bytes" inputMode="numeric" value={draft.max} disabled={formDisabled}
            onChange={event => onDraft({ ...draft, max: event.target.value })} maxLength={19} placeholder="No maximum" />
        </label>
        {!qualityBounds(draft).valid && <p role="status">Enter at least one valid byte limit. The minimum cannot exceed the maximum.</p>}
      </div>}
    </section>
  </div>;
}

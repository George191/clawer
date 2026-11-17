import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { compareSandboxPipelineOutputs } from '../../services/api';
import type { SandboxPipelineColumn, SandboxPipelineOutput, SandboxPipelineOutputComparison as Comparison } from '../../services/api';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';

const labels = { added: 'Added on right', removed: 'Removed from left', updated: 'Changed' };
const sameColumns = (left: SandboxPipelineColumn[], right: SandboxPipelineColumn[]) =>
  left.length === right.length && left.every((column, index) => column === right[index]);
const int64 = (id: string) => /^(0|-?[1-9]\d{0,18})$/.test(id) && BigInt(id) >= -9223372036854775808n && BigInt(id) <= 9223372036854775807n;

function matchesReview(result: Comparison, left: SandboxPipelineOutput, right: SandboxPipelineOutput) {
  return result.fromOutputId === left.id && result.toOutputId === right.id && sameColumns(result.projection, left.columns) &&
    [result.added, result.removed, result.updated, result.unchanged].every(count => Number.isSafeInteger(count) && count >= 0) &&
    result.changes.every(change => {
      const matches = (row: string[] | null) => row?.length === result.projection.length && row[result.projection.indexOf('id')] === change.id;
      return int64(change.id) && (change.kind === 'added' ? change.before === null && matches(change.after)
        : change.kind === 'removed' ? matches(change.before) && change.after === null
          : change.kind === 'updated' && matches(change.before) && matches(change.after));
    });
}

export default function SandboxPipelineOutputComparison({ outputs, onClose, onScopeDenied }: {
  outputs: SandboxPipelineOutput[]; onClose: () => void; onScopeDenied: () => void;
}) {
  const [leftId, setLeftId] = useState('');
  const [rightId, setRightId] = useState('');
  const [result, setResult] = useState<Comparison>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<AbortController>();
  useEffect(() => () => active.current?.abort(), []);
  const clear = () => {
    active.current?.abort(); active.current = undefined;
    setResult(undefined); setBusy(false); setError('');
  };
  const left = outputs.find(item => item.id === leftId);
  const right = outputs.find(item => item.id === rightId);
  const bothHaveId = Boolean(left?.columns.includes('id') && right?.columns.includes('id'));
  const consistent = Boolean(left && right && sameColumns(left.columns, right.columns));
  const compatible = Boolean(left && right && left.id !== right.id && bothHaveId && consistent);
  const compare = async () => {
    if (!left || !right || !compatible || active.current) return;
    clear();
    const request = new AbortController(); active.current = request;
    setBusy(true);
    try {
      const response = await compareSandboxPipelineOutputs({ fromOutputId: left.id, toOutputId: right.id }, request.signal);
      if (request.signal.aborted) return;
      if (matchesReview(response, left, right)) setResult(response);
      else setError('The exact committed comparison result could not be verified.');
    } catch (failure) {
      if (request.signal.aborted) return;
      if (sandboxScopeDenied(failure)) { clear(); onScopeDenied(); return; }
      setError('The exact committed comparison result could not be verified.');
    } finally {
      if (active.current === request) { active.current = undefined; setBusy(false); }
    }
  };
  const label = (item: SandboxPipelineOutput) =>
    `Revision ${item.reviewedGeneration} · ${new Date(item.recordedAt).toLocaleString()} · ${item.id}`;
  return <Modal open destroyOnHidden footer={null} width={780} className="flow-surface-modal" title="Compare committed sandbox outputs"
    onCancel={() => { clear(); onClose(); }}>
    <div className="flow-sandbox-builder flow-sandbox-builder__compare">
      <p>Compare two immutable output receipts by exact ID. This does not rerun a pipeline or write data.</p>
      <p>Added exists only on the right. Removed exists only on the left. Changed compares the stored values from left to right.</p>
      <div className="flow-sandbox-builder__compare-choices">
        <label>Left output<select aria-label="Left output" value={leftId} onChange={event => { clear(); setLeftId(event.target.value); }}>
          <option value="">Select a committed output</option>
          {outputs.map(item => <option key={item.id} value={item.id} disabled={item.id === rightId}>{label(item)}</option>)}
        </select></label>
        <label>Right output<select aria-label="Right output" value={rightId} onChange={event => { clear(); setRightId(event.target.value); }}>
          <option value="">Select a committed output</option>
          {outputs.map(item => <option key={item.id} value={item.id} disabled={item.id === leftId}>{label(item)}</option>)}
        </select></label>
      </div>
      {left && right && (!bothHaveId ? <p role="status">Both outputs must include the exact id column.</p>
        : !consistent ? <p role="status">Both outputs must use the same ordered projection.</p>
          : <p>Compatible ordered projection: <code>{left.columns.join(', ')}</code>.</p>)}
      <div className="flow-sandbox-builder__actions"><Button size="small" type="primary" loading={busy} disabled={!compatible}
        onClick={() => void compare()}>Compare outputs</Button></div>
      {error && <p role="alert">{error}</p>}
      {result && <section aria-label="Committed output comparison result" className="flow-sandbox-builder__compare-result">
        <h2>Comparison result</h2>
        <dl className="flow-sandbox-builder__counts">
          <div><dt>Added on right</dt><dd>{result.added}</dd></div>
          <div><dt>Removed from left</dt><dd>{result.removed}</dd></div>
          <div><dt>Changed</dt><dd>{result.updated}</dd></div>
          <div><dt>Unchanged</dt><dd>{result.unchanged}</dd></div>
        </dl>
        <SandboxMarkings markings={result.markings ?? undefined} />
        {result.markings?.length ? <p>Effective markings account for both receipts and their authorized inputs.</p> : null}
        {result.truncated && <p>Counts cover both committed outputs. Only the first {result.changes.length} changed IDs are shown.</p>}
        {result.changes.length ? <div className="flow-sandbox-builder__table"><table aria-label="Output comparison changes">
          <thead><tr><th scope="col">ID</th><th scope="col">Result</th><th scope="col">Left values</th><th scope="col">Right values</th></tr></thead>
          <tbody>{result.changes.map(change => <tr key={change.id}>
            <th scope="row"><code>{change.id}</code></th><td>{labels[change.kind]}</td>
            <td><StoredValues row={change.before} projection={result.projection} /></td>
            <td><StoredValues row={change.after} projection={result.projection} /></td>
          </tr>)}</tbody>
        </table></div> : <p>No differing rows are shown.</p>}
      </section>}
    </div>
  </Modal>;
}

function StoredValues({ row, projection }: { row: string[] | null; projection: SandboxPipelineColumn[] }) {
  if (row === null) return <>Not present</>;
  return <div className="flow-sandbox-builder__compare-values">{projection.map((column, index) =>
    <span key={column}>{column}: <code>{row[index] === '' ? '""' : row[index]}</code></span>)}</div>;
}

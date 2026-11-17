import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal } from '@/components/ui';
import { readSandboxPipelineRecordHistory } from '../../services/api';
import type { SandboxPipelineOutput, SandboxPipelineRecordHistory as History } from '../../services/api';
import { getSessionState } from '../../services/session';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';

const int64 = (id: string) => /^(0|-?[1-9]\d{0,18})$/.test(id) &&
  BigInt(id) >= -9223372036854775808n && BigInt(id) <= 9223372036854775807n;
const same = (left: string[], right: string[]) => left.length === right.length && left.every((value, index) => value === right[index]);
const labels: Record<History['steps'][number]['kind'], string> = {
  baseline: 'Present · baseline', added: 'Present · added', changed: 'Present · changed',
  removed: 'Not present · removed', unchanged: 'Present · unchanged', absent: 'Not present',
};

// Preserve sub-millisecond recorded order without inferring execution or commit order.
function instant(value: string) {
  const parts = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts) return;
  const epoch = Date.parse(parts[1] + parts[3]);
  if (Number.isFinite(epoch)) return BigInt(epoch) * 1_000_000n + BigInt((parts[2] || '').padEnd(9, '0'));
}

function verify(result: History, outputs: SandboxPipelineOutput[], recordId: string) {
  const expected = new Map(outputs.map(output => [output.id, output]));
  const expectedMarkings = Array.from(new Set(outputs.flatMap(output => output.markings ?? []))).sort();
  if (result.recordId !== recordId || !Array.isArray(result.projection) || !result.projection.includes('id') ||
    !Array.isArray(result.steps) || result.steps.length !== outputs.length ||
    !same([...(result.markings ?? [])].sort(), expectedMarkings)) return false;
  const idIndex = result.projection.indexOf('id');
  let previousAt: bigint | undefined;
  let previous: string[] | null = null;
  return result.steps.every((step, index) => {
    const expectedOutput = expected.get(step.outputId);
    if (!expectedOutput || !same(result.projection, expectedOutput.columns)) return false;
    expected.delete(step.outputId);
    const recordedAt = instant(step.recordedAt);
    if (recordedAt === undefined || recordedAt !== instant(expectedOutput.recordedAt) ||
      (previousAt !== undefined && recordedAt <= previousAt)) return false;
    const row = step.row;
    if (row !== null && (!Array.isArray(row) || row.length !== result.projection.length ||
      row.some(value => typeof value !== 'string') || row[idIndex] !== recordId)) return false;
    const kind = row === null ? previous === null ? 'absent' : 'removed' :
      previous === null ? index === 0 ? 'baseline' : 'added' : same(previous, row) ? 'unchanged' : 'changed';
    previous = row;
    previousAt = recordedAt;
    return step.kind === kind;
  });
}

export default function SandboxPipelineRecordHistory({ outputs, onClose, onScopeDenied }: {
  outputs: SandboxPipelineOutput[]; onClose: () => void; onScopeDenied: () => void;
}) {
  const [ids, setIds] = useState<string[]>([]);
  const [recordId, setRecordId] = useState('');
  const [history, setHistory] = useState<History>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const pending = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => () => pending.current?.abort(), []);
  const clear = () => {
    pending.current?.abort(); pending.current = undefined;
    setHistory(undefined); setBusy(false); setError(false);
  };
  const selected = outputs.filter(output => ids.includes(output.id));
  const projection = selected[0]?.columns;
  const compatible = Boolean(projection?.includes('id') && selected.every(output => same(output.columns, projection)));
  const valid = selected.length === ids.length && ids.length >= 2 && ids.length <= 5 && compatible && int64(recordId);
  const query = async () => {
    if (!valid || pending.current || !live()) return;
    clear();
    const read = new AbortController(); pending.current = read; setBusy(true);
    try {
      const result = await readSandboxPipelineRecordHistory({ outputIds: ids, recordId }, read.signal);
      if (live(read.signal)) {
        if (verify(result, selected, recordId)) setHistory(result); else setError(true);
      }
    } catch (failure) {
      if (!live(read.signal)) return;
      if (sandboxScopeDenied(failure)) { clear(); onScopeDenied(); return; }
      setError(true);
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(read.signal)) setBusy(false); }
    }
  };
  return <Modal open destroyOnHidden footer={null} width={780} className="flow-surface-modal" title="Committed output record history"
    onCancel={() => { clear(); onClose(); }}>
    <div className="flow-sandbox-builder flow-sandbox-builder__compare">
      <p>Select two to five committed outputs with the same ordered projection including <code>id</code>. This does not rerun the pipeline or write data.</p>
      <label>Record ID <Input aria-label="Record ID" maxLength={64} value={recordId}
        onChange={event => { clear(); setRecordId(event.target.value); }} /></label>
      {recordId && !int64(recordId) && <p role="status">Enter a signed 64-bit integer without spaces or leading zeros.</p>}
      <fieldset>
        <legend>Committed outputs · {ids.length} selected</legend>
        {outputs.map(output => <div key={output.id}>
          <Checkbox checked={ids.includes(output.id)} disabled={ids.length >= 5 && !ids.includes(output.id)} onChange={event => {
            clear(); setIds(values => event.target.checked ? values.includes(output.id) || values.length >= 5 ? values : [...values, output.id]
              : values.filter(value => value !== output.id));
          }}>Revision {output.reviewedGeneration} · {output.recordedAt} · {output.id}</Checkbox>
        </div>)}
      </fieldset>
      {selected.length >= 2 && !compatible && <p role="status">Choose outputs with the same ordered projection including the exact id column.</p>}
      <Button size="small" type="primary" loading={busy} disabled={!valid} onClick={() => void query()}>Check record history</Button>
      {error && <p role="status">The exact committed record history could not be verified for this selection.</p>}
      {history && <section aria-label="Record history result" className="flow-sandbox-builder__compare-result">
        <h2>Record history result</h2>
        <p>Recorded UTC order for the chosen outputs only. Absence from an output does not prove deletion from the source. Omitted outputs are not checked.</p>
        <SandboxMarkings markings={history.markings ?? undefined} />
        <div className="flow-sandbox-builder__table"><table aria-label="Record history steps">
          <thead><tr><th scope="col">Recorded at (UTC)</th><th scope="col">Output receipt</th><th scope="col">Result</th>
            {history.projection.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
          <tbody>{history.steps.map(step => <tr key={step.outputId}>
            <td><time dateTime={step.recordedAt}>{step.recordedAt}</time></td><th scope="row"><code>{step.outputId}</code></th><td>{labels[step.kind]}</td>
            {history.projection.map((column, index) => <td key={column}>{step.row === null ? '—' : <code>{step.row[index] === '' ? '""' : step.row[index]}</code>}</td>)}
          </tr>)}</tbody>
        </table></div>
      </section>}
    </div>
  </Modal>;
}

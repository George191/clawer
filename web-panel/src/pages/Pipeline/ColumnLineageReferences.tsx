import { useEffect, useState } from 'react';
import { fetchColumnLineageReferences } from '../../services/api';
import type { ColumnLineageReferences as References, TableLineageDirection, TableLineageIdentity } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';

export const canonColumn = (value: string) => /^[a-z_][a-z0-9_]{0,62}$/.test(value);
const name = (item: TableLineageIdentity & { column: string }) => `${item.schema}.${item.table}.${item.column}`;
const types = { foreign_key: 'Foreign key', object_relation: 'Declared object relation' };
type Scope = TableLineageIdentity & { column: string };

export default function ColumnLineageReferences({ schema, table, column, version }: Scope & { version: number }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, schema, table, column, version])} schema={schema} table={table} column={column} /> : null;
}
function Workspace(scope: Scope) {
  const [direction, setDirection] = useState<TableLineageDirection>('incoming');
  return <section className="flow-lineage-reference-path" aria-label="Column references">
    <h3>Column references</h3>
    <p>Direct column references only. They do not verify data movement or expose column values.</p>
    {canonColumn(scope.column) ? <>
      <label>Column reference direction<select aria-label="Column reference direction" value={direction}
        onChange={event => setDirection(event.target.value === 'outgoing' ? 'outgoing' : 'incoming')}>
        <option value="incoming">Incoming</option><option value="outgoing">Outgoing</option>
      </select></label>
      <Results key={direction} scope={scope} direction={direction} />
    </> : <p role="status">Column references are unavailable for this selection.</p>}
  </section>;
}
function Results({ scope, direction }: { scope: Scope; direction: TableLineageDirection }) {
  const { schema, table, column } = scope;
  const [result, setResult] = useState<References>();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const read = new AbortController();
    void fetchColumnLineageReferences({ schema, table }, column, direction, read.signal).then(value => {
      if (!read.signal.aborted) {
        if (value.direction === direction && value.column === column) setResult(value);
        else setUnavailable(true);
      }
    }).catch(() => { if (!read.signal.aborted) setUnavailable(true); });
    return () => read.abort();
  }, [schema, table, column, direction]);
  if (unavailable) return <p role="status">Column references are unavailable for this selection.</p>;
  if (!result) return <p role="status">Loading visible column references…</p>;
  return <>
    <p>Selected column: <code>{name(scope)}</code></p>
    {result.references.length ? <ol className="flow-lineage-table-references__list">{result.references.map(peer => <li key={name(peer)}>
      <span className="flow-lineage-table-references__route"><code>{name(direction === 'incoming' ? peer : scope)}</code>
        <span aria-label="references"> → </span><code>{name(direction === 'incoming' ? scope : peer)}</code></span>
      <span>{peer.kinds.map(kind => types[kind]).filter(Boolean).join(' · ')}</span>
    </li>)}</ol> : <p role="status">No visible column references in this direction.</p>}
    {result.truncated && <p>Only the first bounded set of visible column references is shown.</p>}
  </>;
}

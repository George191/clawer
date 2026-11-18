import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { getSessionState } from '../../services/session';
import { fetchColumnLineageReferences, traceColumnMappingPaths } from '../../services/api';
import type { ColumnLineageReferences as References, ColumnMappingPaths, TableLineageDirection, TableLineageIdentity } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';

export const canonColumn = (value: string) => /^[a-z_][a-z0-9_]{0,62}$/.test(value);
const name = (item: TableLineageIdentity & { column: string }) => `${item.schema}.${item.table}.${item.column}`;
const types = { foreign_key: 'Foreign key', object_relation: 'Declared object relation' };
type Scope = TableLineageIdentity & { column: string };

export default function ColumnLineageReferences({ schema, table, column, version, canTraceMappings }: Scope & { version: number; canTraceMappings?: boolean }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, schema, table, column, version])} schema={schema} table={table} column={column} canTraceMappings={canTraceMappings} identity={identity} /> : null;
}
function Workspace({ canTraceMappings, identity, ...scope }: Scope & { canTraceMappings?: boolean; identity: string }) {
  const [direction, setDirection] = useState<TableLineageDirection>('incoming');
  return <section className="flow-lineage-reference-path" aria-label="Column references">
    <h3>Column references</h3>
    <p>Direct column references do not verify data movement or expose column values.</p>
    {canonColumn(scope.column) ? <>
      <label>Column reference direction<select aria-label="Column reference direction" value={direction}
        onChange={event => setDirection(event.target.value === 'outgoing' ? 'outgoing' : 'incoming')}>
        <option value="incoming">Incoming</option><option value="outgoing">Outgoing</option>
      </select></label>
      <Results key={direction} scope={scope} direction={direction} />
      {canTraceMappings && <MappingTrace key={`trace:${direction}`} scope={scope} direction={direction} identity={identity} />}
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


const sameNode = (left: Scope, right: Scope) =>
  left.schema === right.schema && left.table === right.table && left.column === right.column;
const validNode = (node: Scope) => node && typeof node.schema === 'string' && /^ts_[a-z0-9_]{1,60}$/.test(node.schema) &&
  typeof node.table === 'string' && canonColumn(node.table) && typeof node.column === 'string' && canonColumn(node.column);

function validTrace(result: ColumnMappingPaths, scope: Scope, direction: TableLineageDirection) {
  return result.direction === direction && (result.status === 'complete' || result.status === 'incomplete' || result.status === 'unavailable') &&
    Array.isArray(result.paths) && result.paths.length <= 19 && (result.status !== 'unavailable' || result.paths.length === 0) &&
    result.paths.every(path => Array.isArray(path.nodes) && path.nodes.length >= 2 && path.nodes.length <= 4 && path.nodes.every(validNode) &&
      new Set(path.nodes.map(name)).size === path.nodes.length &&
      sameNode(direction === 'outgoing' ? path.nodes[0] : path.nodes[path.nodes.length - 1], scope)) &&
    new Set(result.paths.map(path => name(direction === 'outgoing' ? path.nodes[path.nodes.length - 1] : path.nodes[0]))).size === result.paths.length;
}

function MappingTrace({ scope, direction, identity }: { scope: Scope; direction: TableLineageDirection; identity: string }) {
  const [result, setResult] = useState<ColumnMappingPaths>();
  const [loading, setLoading] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const current = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const trace = async () => {
    if (pending.current || !current()) return;
    const read = new AbortController(); pending.current = read;
    setLoading(true); setResult(undefined);
    const unavailable = { status: 'unavailable' as const, direction, paths: [] };
    try {
      const value = await traceColumnMappingPaths(scope, direction, read.signal);
      if (current(read.signal)) setResult(validTrace(value, scope, direction) ? value : unavailable);
    } catch {
      if (current(read.signal)) setResult(unavailable);
    } finally {
      if (pending.current === read) { pending.current = undefined; if (current(read.signal)) setLoading(false); }
    }
  };

  return <section aria-label="Declared column mappings">
    <h4>Declared column mappings</h4>
    <p>Trace enabled declarations with up to 64 field pairs for <code>{name(scope)}</code>. Shows at most one path per reachable column within the trace limits. The arrows do not imply an observed transfer, reveal values, or match columns by name.</p>
    <Button size="small" loading={loading} disabled={loading} onClick={() => void trace()}>Trace declared column mappings</Button>
    {loading && <p role="status">Tracing declared mappings…</p>}
    {result?.status === 'unavailable' && <p role="status">Declared column mapping paths are unavailable.</p>}
    {result?.status === 'incomplete' && <p role="status">Mapping trace is incomplete. Additional visible paths may exist beyond this bounded result.</p>}
    {result?.status === 'complete' && !result.paths.length && <p role="status">No visible supported declared paths in this direction.</p>}
    {Boolean(result?.paths.length) && <ol className="flow-lineage-table-references__list" aria-label="Declared column mapping paths">
      {result?.paths.map(path => <li key={JSON.stringify(path.nodes.map(name))}>
        <span className="flow-lineage-table-references__route">{path.nodes.map((node, index) =>
          <span key={name(node)}>{index > 0 && <span aria-label="maps to"> → </span>}<code>{name(node)}</code></span>)}</span>
      </li>)}
    </ol>}
  </section>;
}

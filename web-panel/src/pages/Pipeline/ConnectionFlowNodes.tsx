import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchCurrentUserContext, fetchDataSourceNodes, fetchDataSources } from '../../services/api';
import type { DataSource, StorageNode } from '../../services/api';
import { getSessionState } from '../../services/session';
import ConnectorMark from './ConnectorMark';
import { useSandboxIdentity } from './SandboxMarkings';
import LineageTransferPlans from './LineageTransferPlans';
import './connection-flow-nodes.css';

const connectionHref = (source: Pick<DataSource, 'id'>, node?: StorageNode) => {
  const params = new URLSearchParams({ sourceId: source.id, sourcePanel: node ? 'sync' : 'overview' });
  if (node) { params.set('sourceSchema', node.name); params.set('sourceKind', node.kind); }
  return `/ontology/connections?${params}`;
};
const currentIdentity = (identity: string) => {
  const session = getSessionState();
  return session.authenticated && !session.locked && session.identity === identity;
};

export default function ConnectionFlowNodes({ version, schemas }: { version: number; schemas: string[] }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={identity} identity={identity} version={version} schemas={schemas} /> : null;
}

function Workspace({ identity, version, schemas }: { identity: string; version: number; schemas: string[] }) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'denied' | 'error'>('loading');
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<StorageNode>();
  useEffect(() => {
    const request = new AbortController();
    const current = () => !request.signal.aborted && currentIdentity(identity);
    setSources([]); setSelected(undefined); setStatus('loading');
    void fetchCurrentUserContext(request.signal).then(async context => {
      if (!current()) return;
      if (!context.user.is_superuser) { setStatus('denied'); return; }
      const rows = await fetchDataSources(request.signal);
      if (current()) { setSources(rows); setStatus('ready'); }
    }).catch(() => { if (current()) setStatus('error'); });
    return () => request.abort();
  }, [identity, version, retry]);

  return <section className={`connection-flow-nodes${selected ? ' is-selected' : ''}`} aria-label="Connection storage nodes">
    <header><strong>Connection storage nodes</strong>{status === 'ready' && <span>{sources.length} connections</span>}</header>
    <p>Browse each connection's storage scopes to configure syncs and transfer plans.</p>
    {status === 'loading' ? <p role="status">Loading connection nodes…</p>
      : status === 'error' ? <p role="alert">Unable to load connection nodes or verify access. <button type="button" className="flow-lineage-action" onClick={() => setRetry(value => value + 1)}>Retry connections</button></p>
        : status === 'denied' ? <p>Connection storage nodes are available to administrators.</p>
          : !sources.length ? <p>No saved connections. <Link to="/ontology/connections">Add a connection</Link></p>
            : <div className="connection-flow-nodes__layout"><div className="connection-flow-nodes__sources">{sources.map(source => <ConnectionNode key={JSON.stringify([source.id, source.updatedAt, version, retry])} source={source} identity={identity} selected={selected?.id} onSelect={setSelected} />)}</div>
              {selected && <aside className="connection-flow-nodes__inspector" aria-label="Connection node transfer plans"><header><strong>{selected.connectionName} · {selected.name}</strong><button type="button" className="flow-lineage-action" onClick={() => setSelected(undefined)}>Close node</button></header>
                <p>{selected.kind} · {selected.engine}</p><Link to={connectionHref({ id: selected.connectionId }, selected)}>Manage syncs for this node</Link>
                <LineageTransferPlans key={selected.id} schema={selected.name} schemas={schemas} sourceNode={selected} />
              </aside>}
            </div>}
  </section>;
}

function ConnectionNode({ source, identity, selected, onSelect }: { source: DataSource; identity: string; selected?: string; onSelect: (node: StorageNode) => void }) {
  const [storageNodes, setStorageNodes] = useState<StorageNode[]>();
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState('');
  const requestRef = useRef<AbortController>();
  useEffect(() => () => requestRef.current?.abort(), []);
  const paused = Boolean(source.pausedAt);
  const browse = (force = false) => {
    if (loading || paused) return;
    if (!force && expanded && !failed) { setExpanded(false); return; }
    setExpanded(true);
    if (!force && storageNodes && !failed) return;
    const request = new AbortController(); requestRef.current?.abort(); requestRef.current = request;
    const current = () => !request.signal.aborted && currentIdentity(identity);
    setLoading(true); setFailed(false);
    void fetchDataSourceNodes(source.id, request.signal, search.trim()).then(value => {
      if (current()) { setStorageNodes(value.nodes); setHasMore(value.hasMore); }
    }).catch(() => { if (current()) setFailed(true); })
      .finally(() => { if (current()) setLoading(false); });
  };

  return <article className="connection-flow-nodes__source" data-connection-id={source.id}>
    <div className="connection-flow-nodes__heading"><ConnectorMark engine={source.engine} size="small" />
      <Link className="connection-flow-nodes__node" data-node-id={JSON.stringify([source.id])} to={connectionHref(source)} aria-label={`Manage ${source.name}`}><strong>{source.name}</strong><span>{source.config.database || source.config.bucket || source.engine}</span></Link>
      {paused && <span>Paused</span>}
      {!paused && <button type="button" className="flow-lineage-action" aria-expanded={expanded} disabled={loading} onClick={() => browse()}>{loading ? 'Loading nodes…' : failed ? 'Retry nodes' : expanded ? 'Hide nodes' : 'Browse nodes'}</button>}
    </div>
    {expanded && <div className="connection-flow-nodes__schemas">
      <form className="connection-flow-nodes__search" onSubmit={event => { event.preventDefault(); browse(true); }}><input aria-label={`Search storage scopes in ${source.name}`} placeholder="Search storage scopes" maxLength={100} value={search} onChange={event => setSearch(event.target.value)} /><button className="flow-lineage-action" type="submit" disabled={loading}>Search scopes</button></form>
      {loading ? <p role="status">Loading storage nodes for {source.name}…</p>
        : failed ? <p role="alert">Unable to load storage scopes for {source.name}. Retry to verify the source catalog.</p>
          : storageNodes && (storageNodes.length ? storageNodes.map(node => <button type="button" className="connection-flow-nodes__node" key={node.id} data-node-id={node.id} aria-pressed={selected === node.id} onClick={() => onSelect(node)} aria-label={`Select ${source.name}: ${node.kind} ${node.name}`}><strong>{node.name}</strong><span>{node.kind} · {source.name}</span></button>) : <p>No visible storage scopes in this connection.</p>)}
      {!loading && !failed && hasMore && <p role="status">More storage scopes are available. Search to find scopes outside this limited catalog.</p>}
    </div>}
  </article>;
}

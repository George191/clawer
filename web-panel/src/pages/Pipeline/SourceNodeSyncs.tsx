import { useEffect, useState } from 'react';
import { Button, NativeSelect } from '@/components/ui';
import { fetchCurrentUserContext, fetchDataSourceNodes, fetchLayers } from '../../services/api';
import type { DataSource, StorageNode } from '../../services/api';
import { getSessionState } from '../../services/session';
import { useSandboxIdentity } from './SandboxMarkings';
import LineageTransferPlans from './LineageTransferPlans';

type Props = { source: DataSource; kind?: string; scope?: string };
const errorText = (failure: unknown) => {
  const data = (failure as { response?: { data?: { detail?: unknown; errors?: unknown } } })?.response?.data;
  const detail = data?.errors ?? data?.detail;
  return typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string' ? detail.message : 'The catalog request failed. Verify the connection and try again.';
};

export default function SourceNodeSyncs(props: Props) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, props.source.id, props.source.updatedAt])} {...props} identity={identity} /> : null;
}

function Workspace({ source, kind, scope, identity }: Props & { identity: string }) {
  const [nodes, setNodes] = useState<StorageNode[]>();
  const [schemas, setSchemas] = useState<string[]>();
  const [selectedId, setSelectedId] = useState('');
  const [access, setAccess] = useState<'loading' | 'allowed' | 'denied' | 'error'>('loading');
  const [nodeError, setNodeError] = useState('');
  const [targetError, setTargetError] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState(scope || '');
  const [appliedSearch, setAppliedSearch] = useState(scope || '');
  const paused = Boolean(source.pausedAt);
  useEffect(() => { setSearch(scope || ''); setAppliedSearch(scope || ''); }, [kind, scope]);
  useEffect(() => {
    const request = new AbortController();
    const current = () => {
      const session = getSessionState();
      return !request.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
    };
    setNodes(undefined); setSchemas(undefined); setSelectedId(''); setNodeError(''); setTargetError(''); setHasMore(false); setAccess('loading');
    if (paused) return () => request.abort();
    void fetchCurrentUserContext(request.signal).then(async context => {
      if (!current()) return;
      if (!context.user.is_superuser) { setAccess('denied'); return; }
      setAccess('allowed');
      const [sourceResult, targetResult] = await Promise.allSettled([fetchDataSourceNodes(source.id, request.signal, appliedSearch), fetchLayers(request.signal)]);
      if (!current()) return;
      if (sourceResult.status === 'fulfilled') {
        setNodes(sourceResult.value.nodes); setHasMore(sourceResult.value.hasMore);
        const match = scope !== undefined ? sourceResult.value.nodes.find(node => node.name === scope && (!kind || node.kind === kind)) : sourceResult.value.nodes[0];
        setSelectedId(match?.id || '');
      } else setNodeError(errorText(sourceResult.reason));
      if (targetResult.status === 'fulfilled') {
        setSchemas(targetResult.value.map(layer => layer.schemaName || (layer.key.startsWith('ts_') ? layer.key : `ts_${layer.key}`)).filter(schema => /^ts_[a-z0-9_]{1,60}$/.test(schema) && !['ts_meta', 'ts_meta_hist'].includes(schema)));
      } else setTargetError(errorText(targetResult.reason));
    }).catch(() => { if (current()) setAccess('error'); });
    return () => request.abort();
  }, [source.id, identity, paused, kind, scope, revision, appliedSearch]);
  const selected = nodes?.find(node => node.id === selectedId);

  return <section className="ontology-source-catalog" aria-label="Source node syncs">
    <header><div><h3>Storage node syncs</h3><p>Choose a storage scope in this connection to define a transfer plan and preview its source data.</p></div><Button size="small" disabled={paused} onClick={() => setRevision(value => value + 1)}>Refresh storage nodes</Button></header>
    {paused ? <p role="status">This connection is paused. Resume it before discovering nodes or reading source data.</p>
      : access === 'loading' ? <p role="status">Checking access…</p>
        : access === 'denied' ? <p role="alert">Only administrators can manage source node syncs.</p>
          : access === 'error' ? <p role="alert">Unable to verify access. Refresh storage nodes to try again.</p>
            : <>
              <form className="ontology-connections-source-actions" onSubmit={event => { event.preventDefault(); setAppliedSearch(search.trim()); setRevision(value => value + 1); }}><input aria-label="Search source storage scopes" placeholder="Search storage scopes" maxLength={100} value={search} onChange={event => setSearch(event.target.value)} /><Button htmlType="submit">Search scopes</Button></form>
              {nodeError ? <p role="alert">Unable to discover storage nodes. {nodeError}</p> : !nodes ? <p role="status">Loading storage nodes…</p> : !nodes.length ? <p>No visible storage scopes in this connection.</p> : <label>Source storage node<NativeSelect aria-label="Source storage node" value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="">Select a storage node</option>{nodes.map(node => <option key={node.id} value={node.id}>{node.kind} · {node.name}</option>)}</NativeSelect></label>}
              {nodes && scope !== undefined && !nodes.some(node => node.name === scope && (!kind || node.kind === kind)) && <p role="alert">The requested storage node is not visible in this connection. Select another node or check the source permissions.</p>}
              {hasMore && <p role="status">Additional storage scopes are not listed in this limited catalog. Search to find another scope.</p>}
              {targetError && <p role="alert">Unable to load destination schemas. {targetError}</p>}
              {selected && !schemas && !targetError && <p role="status">Loading destination schemas…</p>}
              {selected && schemas && <LineageTransferPlans key={JSON.stringify([selected.id, selected.sourceUpdatedAt])} schema={selected.name} schemas={schemas} sourceNode={selected} />}
            </>}
  </section>;
}

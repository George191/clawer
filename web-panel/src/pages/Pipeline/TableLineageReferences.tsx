import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useId, useRef, useState } from 'react';
import { fetchTableLineageNeighbors } from '../../services/api';
import type { TableLineageDirection, TableLineageIdentity, TableLineageNeighborhood, TableLineageReferenceKind } from '../../services/api';
import { getSessionState } from '../../services/session';
import { useSandboxIdentity } from './SandboxMarkings';
import TableLineagePath from './TableLineagePath';

const MAX_NODES = 16;
const MAX_EDGES = 24;
const directions = ['incoming', 'outgoing'] as const;
const kinds: Record<TableLineageReferenceKind, string> = { object_relation: 'Declared object relation', foreign_key: 'Foreign key' };
const key = ({ schema, table }: TableLineageIdentity) => JSON.stringify([schema, table]);
const name = ({ schema, table }: TableLineageIdentity) => `${schema}.${table}`;
const statusCode = (error: unknown) => (error as { response?: { status?: number } })?.response?.status;
type Reference = { key: string; peer: TableLineageIdentity; direction: TableLineageDirection; kinds: TableLineageReferenceKind[] };
type View = { references: Reference[]; truncated: boolean };

function boundedView(root: TableLineageIdentity, pages: TableLineageNeighborhood[], prior: Reference[] = [], ignore = new Set<string>(), anchor = root): View {
  const references: Reference[] = [];
  const nodeKeys = new Set([key(anchor), key(root), ...prior.map(item => key(item.peer))]);
  const edgeKeys = new Set(prior.map(item => item.key));
  let truncated = pages.some(page => page.truncated);
  // The server's catalog order is stable. Direction order is fixed as well.
  for (const direction of directions) {
    const page = pages.find(value => value.direction === direction);
    for (const peer of page?.neighbors ?? []) {
      const peerKey = key(peer);
      if (ignore.has(peerKey)) continue;
      const id = JSON.stringify(direction === 'incoming' ? [peerKey, key(root)] : [key(root), peerKey]);
      if (edgeKeys.has(id)) continue;
      if (edgeKeys.size >= MAX_EDGES || (!nodeKeys.has(peerKey) && nodeKeys.size >= MAX_NODES)) {
        truncated = true;
        continue;
      }
      nodeKeys.add(peerKey);
      edgeKeys.add(id);
      references.push({ key: id, peer, direction, kinds: peer.kinds });
    }
  }
  return { references, truncated };
}

const readPeers = ({ schema, table }: TableLineageIdentity, signal: AbortSignal) =>
  Promise.all(directions.map(direction => fetchTableLineageNeighbors(schema, table, direction, signal)));

type Props = TableLineageIdentity & { version: number };
export default function TableLineageReferences(props: Props) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, props.schema, props.table, props.version])} {...props} identity={identity} /> : null;
}

function Workspace({ schema, table, identity }: Props & { identity: string }) {
  const label = useId();
  const root = { schema, table };
  const [result, setResult] = useState<View>();
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [blockedPeers, setBlockedPeers] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<TableLineageIdentity>();
  const [branch, setBranch] = useState<View>();
  const [branchError, setBranchError] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef(false);
  const branchRequest = useRef<AbortController>();
  const currentButton = useRef<HTMLButtonElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const expandedHeading = useRef<HTMLHeadingElement>(null);
  const focusExpansion = useRef(false);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { active.current = true; heading.current?.focus(); return () => { active.current = false; branchRequest.current?.abort(); }; }, []);
  useEffect(() => {
    const request = new AbortController();
    void readPeers(root, request.signal).then(pages => {
      if (live(request.signal)) { setResult(boundedView(root, pages)); setStatus('ready'); }
    }).catch(() => { if (live(request.signal)) { setResult(undefined); setStatus('unavailable'); } });
    return () => request.abort();
  }, [schema, table, revision, identity]);
  useEffect(() => {
    if (selected && focusExpansion.current) {
      focusExpansion.current = false;
      expandedHeading.current?.focus();
    }
  }, [selected]);

  const references = result?.references.filter(reference => !blockedPeers.has(key(reference.peer))) ?? [];
  const collapse = () => {
    branchRequest.current?.abort(); setSelected(undefined); setBranch(undefined); setBranchError(false);
    requestAnimationFrame(() => { if (currentButton.current?.isConnected) currentButton.current.focus(); else heading.current?.focus(); });
  };
  const expand = (peer: TableLineageIdentity, trigger: HTMLButtonElement) => {
    if (key(peer) === key(root) || blockedPeers.has(key(peer)) || !live()) return;
    if (selected && key(selected) === key(peer)) { collapse(); return; }
    branchRequest.current?.abort();
    const request = new AbortController(); branchRequest.current = request;
    currentButton.current = trigger; focusExpansion.current = true;
    setSelected(peer); setBranch(undefined); setBranchError(false);
    void readPeers(peer, request.signal).then(pages => {
      if (live(request.signal)) setBranch(boundedView(peer, pages, references, blockedPeers, root));
    }).catch(failure => {
      if (!live(request.signal)) return;
      const code = statusCode(failure);
      if (code === 401 || code === 403) {
        setResult(undefined); setSelected(undefined); setBranch(undefined); setStatus('unavailable');
      } else if (code === 404) {
        setBlockedPeers(value => new Set([...value, key(peer)])); setSelected(undefined); setBranch(undefined); setBranchError(true);
        requestAnimationFrame(() => heading.current?.focus());
      } else setBranchError(true);
    });
  };
  const retry = () => {
    branchRequest.current?.abort(); setSelected(undefined); setBranch(undefined); setBranchError(false);
    setBlockedPeers(new Set()); setStatus('loading'); setResult(undefined); setRevision(value => value + 1);
  };
  const visibleEdges = [...references, ...(branch?.references ?? [])];
  const nodeCount = new Set([key(root), ...visibleEdges.map(item => key(item.peer)), ...(selected ? [key(selected)] : [])]).size;

  return <section className="flow-lineage-table-references" aria-labelledby={label}>
    <h3 ref={heading} tabIndex={-1} id={label}>Table lineage references</h3>
    <p>Direction follows declarations and foreign keys. References are not verified data flow.</p>
    {status === 'loading' ? <p role="status"><LoadingIndicator size={20}>Loading visible table references…</LoadingIndicator></p>
      : status === 'unavailable' ? <><p role="status">Table references are unavailable.</p><button type="button" className="flow-lineage-action" onClick={retry}>Retry table references</button></>
      : <>
        <p role="status">{nodeCount} / {MAX_NODES} tables · {visibleEdges.length} / {MAX_EDGES} directed references · maximum depth 2</p>
        <TableLineagePath source={root} onEndpointUnavailable={() => {
          branchRequest.current?.abort(); setResult(undefined); setSelected(undefined); setBranch(undefined); setBranchError(false);
          setBlockedPeers(new Set()); setRevision(value => value + 1);
        }} onScopeDenied={() => {
          branchRequest.current?.abort(); setResult(undefined); setSelected(undefined); setBranch(undefined); setStatus('unavailable');
        }} />
        {result ? <ReferenceLists parent={root} references={references} selected={selected} onExpand={expand} />
          : <p role="status"><LoadingIndicator size={20}>Rechecking visible table references…</LoadingIndicator></p>}
        {result?.truncated && <p>Only the first visible references within this view’s limits are shown.</p>}
        {branchError && !selected && <p role="status">The selected table is no longer available.</p>}
        {selected && <section className="flow-lineage-table-references__expansion" aria-label="Selected neighbor expansion">
          <h4 ref={expandedHeading} tabIndex={-1}>References for {name(selected)}</h4>
          <button type="button" className="flow-lineage-action" onClick={collapse}>Clear selected expansion</button>
          {branchError ? <p role="status">The selected references could not be loaded.</p> :
            !branch ? <p role="status"><LoadingIndicator size={20}>Loading selected neighbor’s references…</LoadingIndicator></p> :
            <><ReferenceLists parent={selected} references={branch.references} />
              {branch.truncated && <p>This expansion reached the graph or visible relationship limit.</p>}
            </>}
        </section>}
      </>}
  </section>;
}

function ReferenceLists({ parent, references, selected, onExpand }: {
  parent: TableLineageIdentity; references: Reference[]; selected?: TableLineageIdentity;
  onExpand?: (peer: TableLineageIdentity, trigger: HTMLButtonElement) => void;
}) {
  return <div className="flow-lineage-node-relations">
    {directions.map(direction => {
      const title = direction === 'incoming' ? 'Incoming references' : 'Outgoing references';
      const items = references.filter(reference => reference.direction === direction);
      return <section key={direction} aria-label={title}>
        <h4>{title}</h4>
        {items.length ? <ul className="flow-lineage-table-references__list">{items.map(item => <li key={item.key}>
          <span className="flow-lineage-table-references__route"><code>{name(direction === 'incoming' ? item.peer : parent)}</code>
            <span aria-label="references"> → </span><code>{name(direction === 'incoming' ? parent : item.peer)}</code></span>
          <span>{item.kinds.map(kind => kinds[kind]).filter(Boolean).join(' · ')}</span>
          {onExpand && key(item.peer) !== key(parent) && <button type="button" className="flow-lineage-action"
            aria-pressed={Boolean(selected && key(selected) === key(item.peer))}
            onClick={event => onExpand(item.peer, event.currentTarget)}>Expand {name(item.peer)}</button>}
        </li>)}</ul> : <p>{onExpand ? 'No visible references in this direction.' : 'No additional visible references in this direction.'}</p>}
      </section>;
    })}
  </div>;
}

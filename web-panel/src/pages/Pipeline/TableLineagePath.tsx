import { isPhysicalSchema } from '../../services/explorerNavigation';
import { useEffect, useRef, useState } from 'react';
import { NativeSelect } from '@/components/ui';
import type { FormEvent } from 'react';
import { fetchTableLineageCommonDownstream, fetchTableLineageCommonUpstream, fetchTableLineagePath, fetchTableLineageTargets } from '../../services/api';
import type { TableLineageCommonUpstream, TableLineageIdentity, TableLineagePathEdge, TableLineagePathResult, TableLineageReferenceKind } from '../../services/api';

const CATALOG_LIMIT = 20;
const kinds: Record<TableLineageReferenceKind, string> = { object_relation: 'Declared object relation', foreign_key: 'Foreign key' };
const name = ({ schema, table }: TableLineageIdentity) => `${schema}.${table}`;
const code = (error: unknown) => (error as { response?: { status?: number } })?.response?.status;
const ordinary = ({ schema, table }: TableLineageIdentity) =>
  isPhysicalSchema(schema) && /^[a-z_][a-z0-9_]{0,62}$/i.test(table);

function hasWitness(edges: TableLineagePathEdge[], source: TableLineageIdentity, target: TableLineageIdentity) {
  return edges.length >= 1 && edges.length <= 4 && name(edges[0].from) === name(source) &&
    name(edges[edges.length - 1].to) === name(target) && edges.every((edge, index) =>
      edge.kinds.some(kind => Object.prototype.hasOwnProperty.call(kinds, kind)) &&
      (index === 0 || name(edges[index - 1].to) === name(edge.from)));
}

type Props = { source: TableLineageIdentity; onScopeDenied: () => void; onEndpointUnavailable: () => void };
type Mode = 'directed' | 'upstream' | 'downstream';
type SharedResult = Omit<TableLineageCommonUpstream, 'ancestor'> & { mode: Exclude<Mode, 'directed'>; shared: TableLineageIdentity | null };
export default function TableLineagePath({ source, onScopeDenied, onEndpointUnavailable }: Props) {
  const [open, setOpen] = useState(false);
  return <section className="flow-lineage-reference-path" aria-label="Find a reference path">
    <button type="button" className="flow-lineage-action" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? 'Close reference path search' : 'Find a reference path'}
    </button>
    {open && <PathSearch source={source} onScopeDenied={onScopeDenied} onEndpointUnavailable={onEndpointUnavailable} />}
  </section>;
}

function PathSearch({ source, onScopeDenied, onEndpointUnavailable }: Props) {
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<TableLineageIdentity[]>();
  const [limited, setLimited] = useState(false);
  const [target, setTarget] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [searching, setSearching] = useState<Mode>();
  const [error, setError] = useState('');
  const [result, setResult] = useState<TableLineagePathResult>();
  const [common, setCommon] = useState<SharedResult>();
  const catalogRequest = useRef<AbortController>();
  const pathRequest = useRef<AbortController>();
  useEffect(() => () => { catalogRequest.current?.abort(); pathRequest.current?.abort(); }, []);
  const clearPath = () => {
    pathRequest.current?.abort(); pathRequest.current = undefined; setResult(undefined); setCommon(undefined); setSearching(undefined);
  };
  const clearCatalog = () => {
    catalogRequest.current?.abort(); catalogRequest.current = undefined; setCatalogLoading(false);
    setOptions(undefined); setTarget(''); setLimited(false); clearPath(); setError('');
  };
  const searchCatalog = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = search.trim();
    if (!query) return;
    clearCatalog();
    const request = new AbortController(); catalogRequest.current = request;
    setCatalogLoading(true);
    void fetchTableLineageTargets(source, query, request.signal).then(page => {
      if (request.signal.aborted) return;
      const candidates = new Map<string, TableLineageIdentity>();
      for (const item of page.items.slice(0, CATALOG_LIMIT)) {
        const identity = { schema: item.schema, table: item.table };
        if (ordinary(identity) && name(identity) !== name(source)) candidates.set(name(identity), identity);
      }
      setOptions([...candidates.values()]); setLimited(page.truncated);
    }).catch(failure => {
      if (request.signal.aborted) return;
      if (code(failure) === 401 || code(failure) === 403 || code(failure) === 404) onScopeDenied();
      else setError('Visible destination tables could not be loaded.');
    }).finally(() => { if (!request.signal.aborted) setCatalogLoading(false); });
  };
  const find = async (mode: Mode) => {
    const destination = options?.find(option => name(option) === target);
    if (!destination || searching === mode) return;
    clearPath(); setError('');
    const request = new AbortController(); pathRequest.current = request;
    const invalid = mode === 'directed' ? 'The directed reference path could not be verified.' : `The common ${mode} reference could not be verified.`;
    setSearching(mode);
    try {
      if (mode !== 'directed') {
        const value = mode === 'upstream' ? await fetchTableLineageCommonUpstream(source, destination, request.signal)
          : await fetchTableLineageCommonDownstream(source, destination, request.signal);
        if (request.signal.aborted) return;
        const shared = 'ancestor' in value ? value.ancestor : value.descendant;
        if (value.state === 'found' && (!shared || !ordinary(shared) ||
          name(shared) === name(source) || name(shared) === name(destination) ||
          !(mode === 'upstream' ? hasWitness(value.leftEdges, shared, source) && hasWitness(value.rightEdges, shared, destination)
            : hasWitness(value.leftEdges, source, shared) && hasWitness(value.rightEdges, destination, shared)))) {
          setError(invalid);
        } else setCommon({ state: value.state, searchTruncated: value.searchTruncated, mode, shared,
          leftEdges: value.leftEdges, rightEdges: value.rightEdges });
      } else {
        const value = await fetchTableLineagePath(source, destination, request.signal);
        if (request.signal.aborted) return;
        if (value.state === 'found' && !hasWitness(value.edges, source, destination)) setError(invalid);
        else setResult(value);
      }
    } catch (failure) {
      if (request.signal.aborted) return;
      const status = code(failure);
      if (status === 401 || status === 403) { clearCatalog(); onScopeDenied(); }
      else if (status === 404) {
        clearCatalog();
        setError('The selected path endpoints are no longer available. Search visible tables again.');
        onEndpointUnavailable();
      }
      else setError(invalid);
    } finally {
      if (pathRequest.current === request) { pathRequest.current = undefined; setSearching(undefined); }
    }
  };

  return <div className="flow-lineage-reference-path__body">
    <p>Source: <code>{name(source)}</code>. Find an outgoing path, a table that references both selections, or a table both selections reference. References are not evidence of data movement.</p>
    <form onSubmit={searchCatalog} aria-label="Search for a destination">
      <label>Search destination tables<input type="search" aria-label="Search destination tables" required maxLength={100}
        value={search} onChange={event => { setSearch(event.target.value); clearCatalog(); }} /></label>
      <button type="submit" className="flow-lineage-action" disabled={!search.trim() || catalogLoading}>Search visible tables</button>
    </form>
    {catalogLoading && <p role="status">Searching visible tables…</p>}
    {options && <>
      {limited && <p>Showing at most the first {CATALOG_LIMIT} visible matches. Refine the search to see a different set.</p>}
      {!options.length ? <p role="status">No eligible visible destination in these results.</p> :
        <form onSubmit={event => { event.preventDefault(); void find('directed'); }} aria-label="Find a directed reference path">
          <label>Destination table<NativeSelect aria-label="Destination table" value={target} onChange={event => {
            clearPath(); setError(''); setTarget(event.target.value);
          }}>
            <option value="">Select a visible destination</option>
            {options.map(option => <option key={name(option)} value={name(option)}>{name(option)}</option>)}
          </NativeSelect></label>
          <div className="flow-lineage-reference-path__actions">
            <button type="submit" className="flow-lineage-action" disabled={!target || searching === 'directed'}>Find reference path</button>
            <button type="button" className="flow-lineage-action" disabled={!target || searching === 'upstream'}
              onClick={() => void find('upstream')}>Find common upstream reference</button>
            <button type="button" className="flow-lineage-action" disabled={!target || searching === 'downstream'}
              onClick={() => void find('downstream')}>Find common downstream reference</button>
          </div>
        </form>}
    </>}
    {searching && <p role="status">{searching === 'directed' ? 'Searching for a directed reference path…' : `Searching for a common ${searching} reference…`}</p>}
    {error && <p role="status">{error}</p>}
    {result?.state === 'found' && <section aria-label="Directed reference path">
      <h4>Directed reference path</h4>
      <p role="status">Found a visible directed reference path ({result.edges.length} {result.edges.length === 1 ? 'hop' : 'hops'}).</p>
      {result.searchTruncated && <p>The search was limited. Other paths may exist.</p>}
      <PathEdges edges={result.edges} />
    </section>}
    {result?.state === 'not_found_within_limit' && !result.searchTruncated && <p role="status">No visible reference path within four hops. This does not mean the tables are disconnected.</p>}
    {(result?.state === 'incomplete' || result?.state === 'not_found_within_limit' && result.searchTruncated) &&
      <p role="status">Search incomplete. A visible path could not be verified within the search budget.</p>}
    {common?.state === 'found' && common.shared && <section className="flow-lineage-reference-path__witness" aria-label={`Common ${common.mode} reference`}>
      <h4>Common {common.mode} reference</h4>
      <p role="status">Found a visible common {common.mode} reference: <code>{name(common.shared)}</code>.</p>
      <p>These two directed catalog paths do not verify data flow. They do not establish operational impact. Other common references may exist.</p>
      {common.searchTruncated && <p>The search was limited; only the verified paths below are shown.</p>}
      <section aria-label={common.mode === 'upstream' ? 'Path to current table' : 'Path from current table'}><h4>Path {common.mode === 'upstream' ? 'to' : 'from'} current table</h4><PathEdges edges={common.leftEdges} /></section>
      <section aria-label={common.mode === 'upstream' ? 'Path to selected table' : 'Path from selected table'}><h4>Path {common.mode === 'upstream' ? 'to' : 'from'} selected table</h4><PathEdges edges={common.rightEdges} /></section>
    </section>}
    {common?.state === 'none_within_limit' && !common.searchTruncated &&
      <p role="status">No common {common.mode} reference found within the search limits. This does not rule out other references.</p>}
    {(common?.state === 'incomplete' || common?.state === 'none_within_limit' && common.searchTruncated) &&
      <p role="status">Common {common.mode} search incomplete. No shared reference could be verified within the search budget.</p>}
  </div>;
}

function PathEdges({ edges }: { edges: TableLineagePathEdge[] }) {
  return <ol className="flow-lineage-table-references__list">{edges.map((edge, index) => <li key={`${index}:${name(edge.from)}:${name(edge.to)}`}>
    <span className="flow-lineage-table-references__route"><code>{name(edge.from)}</code><span aria-label="references"> → </span><code>{name(edge.to)}</code></span>
    <span>{edge.kinds.map(kind => kinds[kind]).filter(Boolean).join(' · ')}</span>
  </li>)}</ol>;
}

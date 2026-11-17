import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { fetchTableLineagePath, fetchTableLineageTargets } from '../../services/api';
import type { TableLineageIdentity, TableLineagePathResult, TableLineageReferenceKind } from '../../services/api';
import { INDEX_PREVIEW_SCHEMA, INDEX_PREVIEW_TABLE } from './SandboxIndexPreview';

const CATALOG_LIMIT = 20;
const kinds: Record<TableLineageReferenceKind, string> = { object_relation: 'Declared object relation', foreign_key: 'Foreign key' };
const name = ({ schema, table }: TableLineageIdentity) => `${schema}.${table}`;
const code = (error: unknown) => (error as { response?: { status?: number } })?.response?.status;
const ordinary = ({ schema, table }: TableLineageIdentity) =>
  /^ts_[a-z0-9_]{1,60}$/.test(schema) && /^[a-z_][a-z0-9_]{0,62}$/.test(table) && !schema.endsWith('_hist') &&
  !(schema === INDEX_PREVIEW_SCHEMA && table === INDEX_PREVIEW_TABLE);

function hasWitness(result: TableLineagePathResult, source: TableLineageIdentity, target: TableLineageIdentity) {
  const { edges } = result;
  return edges.length >= 1 && edges.length <= 4 && name(edges[0].from) === name(source) &&
    name(edges[edges.length - 1].to) === name(target) && edges.every((edge, index) =>
      edge.kinds.some(kind => Object.prototype.hasOwnProperty.call(kinds, kind)) &&
      (index === 0 || name(edges[index - 1].to) === name(edge.from)));
}

type Props = { source: TableLineageIdentity; onScopeDenied: () => void; onEndpointUnavailable: () => void };
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
  const [pathLoading, setPathLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<TableLineagePathResult>();
  const catalogRequest = useRef<AbortController>();
  const pathRequest = useRef<AbortController>();
  useEffect(() => () => { catalogRequest.current?.abort(); pathRequest.current?.abort(); }, []);
  const clearPath = () => { pathRequest.current?.abort(); pathRequest.current = undefined; setResult(undefined); setPathLoading(false); };
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
  const find = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const destination = options?.find(option => name(option) === target);
    if (!destination || pathLoading) return;
    clearPath(); setError('');
    const request = new AbortController(); pathRequest.current = request;
    setPathLoading(true);
    void fetchTableLineagePath(source, destination, request.signal).then(value => {
      if (request.signal.aborted) return;
      if (value.state === 'found' && !hasWitness(value, source, destination)) {
        setError('The directed reference path could not be verified.');
      } else setResult(value);
    }).catch(failure => {
      if (request.signal.aborted) return;
      const status = code(failure);
      if (status === 401 || status === 403) { clearCatalog(); onScopeDenied(); }
      else if (status === 404) {
        clearCatalog();
        setError('The selected path endpoints are no longer available. Search visible tables again.');
        onEndpointUnavailable();
      }
      else setError('The directed reference path could not be verified.');
    }).finally(() => { if (!request.signal.aborted) setPathLoading(false); });
  };

  return <div className="flow-lineage-reference-path__body">
    <p>Source: <code>{name(source)}</code>. Search outgoing references for up to four hops; they are not evidence of data movement.</p>
    <form onSubmit={searchCatalog} aria-label="Search for a destination">
      <label>Search destination tables<input type="search" aria-label="Search destination tables" required maxLength={100}
        value={search} onChange={event => { setSearch(event.target.value); clearCatalog(); }} /></label>
      <button type="submit" className="flow-lineage-action" disabled={!search.trim() || catalogLoading}>Search visible tables</button>
    </form>
    {catalogLoading && <p role="status">Searching visible tables…</p>}
    {options && <>
      {limited && <p>Showing at most the first {CATALOG_LIMIT} visible matches. Refine the search to see a different set.</p>}
      {!options.length ? <p role="status">No eligible visible destination in these results.</p> :
        <form onSubmit={find} aria-label="Find a directed reference path">
          <label>Destination table<select aria-label="Destination table" value={target} onChange={event => {
            clearPath(); setError(''); setTarget(event.target.value);
          }}>
            <option value="">Select a visible destination</option>
            {options.map(option => <option key={name(option)} value={name(option)}>{name(option)}</option>)}
          </select></label>
          <button type="submit" className="flow-lineage-action" disabled={!target || pathLoading}>Find reference path</button>
        </form>}
    </>}
    {pathLoading && <p role="status">Searching for a directed reference path…</p>}
    {error && <p role="status">{error}</p>}
    {result?.state === 'found' && <section aria-label="Directed reference path">
      <h4>Directed reference path</h4>
      <p role="status">Found a visible directed reference path ({result.edges.length} {result.edges.length === 1 ? 'hop' : 'hops'}).</p>
      {result.searchTruncated && <p>The search was limited. Other paths may exist.</p>}
      <ol className="flow-lineage-table-references__list">{result.edges.map((edge, index) => <li key={`${index}:${name(edge.from)}:${name(edge.to)}`}>
        <span className="flow-lineage-table-references__route"><code>{name(edge.from)}</code><span aria-label="references"> → </span><code>{name(edge.to)}</code></span>
        <span>{edge.kinds.map(kind => kinds[kind]).filter(Boolean).join(' · ')}</span>
      </li>)}</ol>
    </section>}
    {result?.state === 'not_found_within_limit' && !result.searchTruncated && <p role="status">No visible reference path within four hops. This does not mean the tables are disconnected.</p>}
    {(result?.state === 'incomplete' || result?.state === 'not_found_within_limit' && result.searchTruncated) &&
      <p role="status">Search incomplete. A visible path could not be verified within the search budget.</p>}
  </div>;
}

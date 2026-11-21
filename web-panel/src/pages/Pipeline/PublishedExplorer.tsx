import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui';
import { CloseOutlined, FilterOutlined, ReloadOutlined } from '@/components/ui/icons';
import { fetchPublishedOntologyType, ontologyError, type PublishedOntologyType } from '../../services/ontology';
import { fetchCurrentUserContext } from '../../services/api';
import { openSaved, publishedSavedPath, type SavedDetail } from '../../services/explorerSaved';
import { loadPublishedSavedSet, queryPublishedExplorer, type PublishedExplorerRecord, type PublishedExplorerResult } from '../../services/publishedExplorer';
import { parsePublishedExplorerEntry, publishedExplorerHref, validatePublishedExplorerQuery, type PublishedExploration, type PublishedExplorerEntry, type PublishedExplorerQuery } from '../../services/publishedExplorerHandoff';
import { getSessionState } from '../../services/session';
import ExplorerWorkbenchHeader, { type ResourceView } from './ExplorerWorkbenchHeader';
import { explorerResourceCapabilities } from '../../services/explorerCapabilities';
import PublishedSourceTools from './PublishedSourceTools';
import { usePublishedObjectTools } from './PublishedObjectTools';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import { parsePropertyValue } from './ontologyPropertyValue';
import './published-results.css';
import './published-explorer.css';

const pageSize = 25;
const labels = { eq: 'Equals', contains: 'Contains', gt: 'Greater than', lt: 'Less than' };
const display = (value: unknown): string => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const identityKey = (row: PublishedExplorerRecord) => row.identity && JSON.stringify(Object.entries(row.identity).sort(([a], [b]) => a.localeCompare(b)));
type Scope = { key: string; query: PublishedExplorerQuery; saved?: SavedDetail<PublishedExploration> };

export default function PublishedExplorer() {
  const location = useLocation();
  const parsed = useMemo(() => {
    try { return { entry: parsePublishedExplorerEntry(location.search, location.hash), error: '' }; }
    catch (error) { return { entry: undefined, error: (error as Error).message }; }
  }, [location.search, location.hash]);
  if (!parsed.entry) return <section className="flow-exploration-empty" aria-label="Invalid published exploration"><h2>Cannot open this object set</h2><p role="alert">{parsed.error}</p><Link to="/ontology?catalog=published">Browse Published object types</Link></section>;
  return <Workspace key={parsed.entry.definitionId} entry={parsed.entry} routeKey={location.search + location.hash} />;
}

function Workspace({ entry, routeKey }: { entry: PublishedExplorerEntry; routeKey: string }) {
  const navigate = useNavigate();
  const [objectType, setObjectType] = useState<PublishedOntologyType>();
  const [canManage, setCanManage] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [scope, setScope] = useState<Scope>();
  const [scopeError, setScopeError] = useState('');
  const [read, setRead] = useState<{ key: string; page?: PublishedExplorerResult; error?: string }>();
  const [offset, setOffset] = useState(0);
  const [version, retry] = useState(0);
  const [mode, setMode] = useState<'results' | 'explore'>('results');
  const [selected, setSelected] = useState<Record<string, PublishedExplorerRecord>>({});
  const [preview, setPreview] = useState<PublishedExplorerRecord>();
  const [previewTab, setPreviewTab] = useState<'properties' | 'history'>('properties');
  const [resource, setResource] = useState<ResourceView>('preview');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [field, setField] = useState(''), [operator, setOperator] = useState<keyof typeof labels>('eq'), [raw, setRaw] = useState('');
  const [filterError, setFilterError] = useState('');
  const [sortField, setSortField] = useState(''), [direction, setDirection] = useState<'asc' | 'desc'>('asc');
  const sessionIdentity = getSessionState().identity;
  const scopeKey = JSON.stringify([routeKey, version]);
  useEffect(() => {
    const controller = new AbortController();
    setMetadataError('');
    Promise.all([fetchPublishedOntologyType(entry.definitionId, controller.signal), fetchCurrentUserContext(controller.signal)]).then(([result, context]) => {
      if (!controller.signal.aborted && getSessionState().identity === sessionIdentity) { setObjectType(result); setCanManage(!!context.user.is_superuser); }
    }, error => { if (!controller.signal.aborted) setMetadataError(ontologyError(error, 'Unable to load this published object type.')); });
    return () => controller.abort();
  }, [entry.definitionId, sessionIdentity, version]);
  useEffect(() => {
    const controller = new AbortController();
    setScopeError(''); setOffset(0); setSelected({}); setPreview(undefined); setRead(undefined);
    async function load() {
      let query = entry.query, saved: SavedDetail<PublishedExploration> | undefined;
      if (entry.saved) {
        saved = await openSaved<PublishedExploration>(publishedSavedPath(entry.definitionId), entry.saved, controller.signal);
        query = saved.kind === 'list' ? { filters: [], keys: saved.content.keys ?? [] } : validatePublishedExplorerQuery(saved.content.query);
      } else if (entry.savedSet) {
        const result = await loadPublishedSavedSet(entry.definitionId, entry.savedSet, controller.signal);
        query = validatePublishedExplorerQuery(result.query);
      }
      if (!controller.signal.aborted && getSessionState().identity === sessionIdentity) {
        setScope({ key: scopeKey, query, saved });
        setMode(saved?.kind === 'exploration' ? saved.content.view : 'results');
        setSortField(query.sorts?.[0]?.field || ''); setDirection(query.sorts?.[0]?.direction || 'asc');
      }
    }
    void load().catch(error => { if (!controller.signal.aborted) setScopeError(ontologyError(error, 'Unable to open this object set.')); });
    return () => controller.abort();
  }, [entry, scopeKey, sessionIdentity]);
  const activeScope = scope?.key === scopeKey ? scope : undefined;
  const query = activeScope?.query;
  const requestKey = JSON.stringify([routeKey, offset, version]);
  useEffect(() => {
    if (!query || !objectType) return;
    const controller = new AbortController();
    queryPublishedExplorer(entry.definitionId, query, pageSize, offset, controller.signal).then(page => {
      if (!controller.signal.aborted && getSessionState().identity === sessionIdentity) setRead({ key: requestKey, page });
    }, error => { if (!controller.signal.aborted) setRead({ key: requestKey, error: ontologyError(error, 'Unable to query this object set.') }); });
    return () => controller.abort();
  }, [entry.definitionId, query, objectType, offset, requestKey, sessionIdentity]);
  const currentRead = read?.key === requestKey ? read : undefined;
  const result = currentRead?.page;
  const busy = !metadataError && !scopeError && (!objectType || !activeScope || !currentRead);
  const rows = result?.items || [], properties = objectType?.definition.properties || [];
  const columns = properties.filter(property => property.id !== objectType?.definition.titleKey);
  const chosen = Object.values(selected);
  const selectable = rows.filter(row => row.identity);
  const currentAllSelected = selectable.length > 0 && selectable.every(row => !!selected[identityKey(row)!]);
  const pageExceedsSelectionLimit = chosen.length + selectable.filter(row => !selected[identityKey(row)!]).length > 100;
  const fieldType = properties.find(property => property.id === field)?.type;
  const operators: (keyof typeof labels)[] = fieldType === 'string' ? ['eq', 'contains'] : fieldType === 'integer' || fieldType === 'number' ? ['eq', 'gt', 'lt'] : ['eq'];
  function changeQuery(nextQuery: PublishedExplorerQuery) {
    setFilterError('');
    navigate(publishedExplorerHref(entry.definitionId, { query: nextQuery, returnTo: entry.returnTo }), { replace: true });
  }
  function openSavedItem(id: string) {
    navigate(publishedExplorerHref(entry.definitionId, { ...(entry.savedSet ? { savedSet: entry.savedSet } : { query: entry.query }), saved: id, returnTo: entry.returnTo }));
  }
  function toggle(row: PublishedExplorerRecord, checked: boolean) {
    const key = identityKey(row);
    if (!key) return;
    setSelected(previous => { if (checked && !previous[key] && Object.keys(previous).length >= 100) return previous; const next = { ...previous }; if (checked) next[key] = row; else delete next[key]; return next; });
  }
  function addFilter() {
    if (!query || !field) return;
    try {
      const value = fieldType === 'datetime' && objectType?.definition.source.mode === 'existing' ? raw.trim() : parsePropertyValue(raw, fieldType || 'string');
      changeQuery({ ...query, filters: [...query.filters, { field, op: operator, value }] });
      setRaw('');
    } catch (error) { setFilterError((error as Error).message); }
  }
  const error = metadataError || scopeError || currentRead?.error;
  const objectTools = usePublishedObjectTools({ objectType, actions: result?.actions || [], record: preview, returnTo: entry.returnTo, busy: busy || !!error,
    onChanged: () => { setPreview(undefined); setSelected({}); retry(value => value + 1); } });
  return <section className={`published-explorer${mode === 'explore' ? ' is-exploring' : ''}`} aria-label="Published Object Explorer" aria-busy={busy}>
    <fieldset className="wb-shell" disabled={busy || !!error}>
      <ExplorerWorkbenchHeader<PublishedExploration> source={{ kind: 'published', definitionId: entry.definitionId }} label={objectType?.definition.name || entry.definitionId}
        available={!!objectType} glyph={objectType && <SemanticTypeGlyph definition={objectType.definition} />}
        fields={properties.map(property => property.id)} names={Object.fromEntries(properties.map(property => [property.id, property.name]))}
        rows={rows.map(row => row.properties)} selected={chosen.map(row => ({ identity: row.identity!, record: row.properties, title: display(row.title) }))}
        query={{ query: query || { filters: [] } }} resource={resource} admin={canManage} layoutControl={objectTools.createButton} selectResource={setResource}
        availableResources={objectType ? explorerResourceCapabilities(objectType.definition.source, canManage) : []} additionalTools={objectTools.tools}
        saved={activeScope?.saved} savedLoading={!activeScope && !scopeError} savedError={scopeError}
        openSavedItem={openSavedItem} closeSavedItem={() => navigate(publishedExplorerHref(entry.definitionId, { ...(entry.savedSet ? { savedSet: entry.savedSet } : { query: entry.query }), returnTo: entry.returnTo }))}
        saveCompleted={detail => openSavedItem(detail.id)} updateSavedItem={detail => setScope(previous => previous?.key === scopeKey ? { ...previous, saved: detail } : previous)}
        addFilter={id => { setField(id); setOperator('eq'); setFiltersOpen(true); }} backToTypes={() => navigate(entry.returnTo)}
        toggleCatalog={() => navigate('/ontology?catalog=published')} closeCatalog={() => {}} catalogOpen={false}
        mode={mode} setMode={setMode} clearSelection={() => setSelected({})}
        filterButton={<button type="button" aria-expanded={filtersOpen} onClick={() => setFiltersOpen(value => !value)}><FilterOutlined /> Filter &amp; sort{query && query.filters.length + (query.sorts?.length || 0) > 0 ? ` (${query.filters.length + (query.sorts?.length || 0)})` : ''}</button>} />
    </fieldset>
    {error && <div className="published-explorer-state" role="alert">{error}<Button size="small" icon={<ReloadOutlined />} onClick={() => retry(value => value + 1)}>Retry</Button></div>}
    {busy && <div className="published-explorer-state" role="status">Loading published object set…</div>}
    {filtersOpen && objectType && query && <section className="published-explorer-filters" aria-label="Published object filters">
      <header><strong>Filter &amp; sort</strong><button type="button" aria-label="Close filters" onClick={() => setFiltersOpen(false)}><CloseOutlined /></button></header>
      <div className="published-explorer-filter-fields"><label>Property<select aria-label="Filter property" value={field} onChange={event => { setField(event.target.value); setOperator('eq'); }}><option value="">Select property</option>{properties.map(property => <option key={property.id} value={property.id}>{property.name}</option>)}</select></label>
        <label>Operator<select aria-label="Filter operator" value={operator} onChange={event => setOperator(event.target.value as typeof operator)}>{operators.map(op => <option key={op} value={op}>{labels[op]}</option>)}</select></label>
        <label>Value<input aria-label="Filter value" value={raw} onChange={event => setRaw(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') addFilter(); }} /></label>
        <Button size="small" disabled={busy || !field || query.filters.length >= 20} onClick={addFilter}>Add filter</Button>
      </div>
      {filterError && <p role="alert">{filterError}</p>}
      {query.filters.length > 0 && <ul>{query.filters.map((filter, index) => <li key={index}><span>{properties.find(property => property.id === filter.field)?.name || filter.field} {labels[filter.op]} {display(filter.value)}</span><button type="button" aria-label={'Remove filter ' + (index + 1)} disabled={busy} onClick={() => changeQuery({ ...query, filters: query.filters.filter((_, item) => item !== index) })}><CloseOutlined /></button></li>)}</ul>}
      <div className="published-explorer-filter-fields"><label>Sort property<select aria-label="Sort property" value={sortField} onChange={event => setSortField(event.target.value)}><option value="">No explicit sort</option>{properties.filter(property => property.type !== 'json').map(property => <option key={property.id} value={property.id}>{property.name}</option>)}</select></label><label>Direction<select aria-label="Sort direction" value={direction} onChange={event => setDirection(event.target.value as typeof direction)}><option value="asc">Ascending</option><option value="desc">Descending</option></select></label><Button size="small" disabled={busy} onClick={() => changeQuery({ ...query, sorts: sortField ? [{ field: sortField, direction }] : [] })}>Apply sort</Button></div>
    </section>}
    {objectType && query && <div className="published-explorer-scope"><span>Published · {objectType.definition.id}</span><span>{query.keys !== undefined ? `${query.keys.length} object references` : 'Dynamic object set'}{result ? ` · ${result.count.toLocaleString()} matching objects` : ''}{result && query.sumField ? ` · Sum: ${display(result.sum)}` : ''}</span></div>}
    {mode === 'results' && result && objectType && <div className={`flow-published-results-body published-explorer-body${preview ? ' has-selection' : ''}`}>
      <div className="flow-published-record-pane"><div className="flow-published-record-scroll" tabIndex={0} role="region" aria-label="Published Explorer results">
        <table style={{ width: `max(100%, ${276 + columns.length * 164}px)` }}><colgroup><col className="flow-published-select-column" /><col className="flow-published-title-column" />{columns.map(property => <col key={property.id} />)}</colgroup>
          <thead><tr><th scope="col"><input type="checkbox" aria-label="Select all objects on this page" title={pageExceedsSelectionLimit ? 'Select up to 100 objects.' : undefined} disabled={!selectable.length || (!currentAllSelected && pageExceedsSelectionLimit)} checked={currentAllSelected} onChange={event => { const checked = event.target.checked; setSelected(previous => { const next = { ...previous }; for (const row of selectable) { const key = identityKey(row)!; if (checked) next[key] = row; else delete next[key]; } return next; }); }} /></th><th scope="col">Title</th>{columns.map(property => <th scope="col" key={property.id}>{property.name}</th>)}</tr></thead>
          <tbody>{rows.map((row, index) => <tr key={identityKey(row) || index} className={row === preview ? 'is-selected' : undefined}><td><input type="checkbox" aria-label={`Select ${display(row.title)}`} title={!row.identity ? 'A complete authorized object identity is required to save a list.' : chosen.length >= 100 && !selected[identityKey(row)!] ? 'Select up to 100 objects.' : undefined} disabled={!row.identity || (chosen.length >= 100 && !selected[identityKey(row)!])} checked={!!selected[identityKey(row) || '']} onChange={event => toggle(row, event.target.checked)} /></td><th scope="row"><button type="button" className="flow-published-record-title" onClick={() => { setPreviewTab('properties'); setPreview(row); }}><SemanticTypeGlyph definition={objectType.definition} /><span>{display(row.title)}</span></button></th>{columns.map(property => <td key={property.id} title={display(row.properties[property.id])}>{display(row.properties[property.id]).slice(0, 120)}</td>)}</tr>)}{!rows.length && <tr><td className="flow-published-results-empty" colSpan={columns.length + 2}>No objects match this set.</td></tr>}</tbody>
        </table>
      </div><footer className="flow-published-results-pagination"><span>{rows.length ? `${offset + 1}–${offset + rows.length} of ${result.count.toLocaleString()}` : '0 shown'}</span><div><Button size="small" disabled={busy || !offset} onClick={() => { setPreview(undefined); setOffset(value => Math.max(0, value - pageSize)); }}>Previous page</Button><Button size="small" disabled={busy || !result.hasMore || offset + pageSize > 100000} onClick={() => { setPreview(undefined); setOffset(value => value + pageSize); }}>Next page</Button></div></footer></div>
      {preview && <aside className="flow-published-selection" aria-label="Selection Preview"><header><h3>Selection Preview</h3><Button size="small" type="text" aria-label="Close selection preview" icon={<CloseOutlined />} onClick={() => setPreview(undefined)} /></header><div className="flow-published-selection-content"><div className="flow-published-selection-title"><SemanticTypeGlyph definition={objectType.definition} size="preview" /><div><span>{objectType.definition.name}</span><h4>{display(preview.title)}</h4></div>{objectTools.recordActions}</div>
        <nav className="flow-published-selection-tabs" aria-label="Selection view"><button type="button" aria-pressed={previewTab === 'properties'} onClick={() => setPreviewTab('properties')}>Properties</button>{objectTools.history && <button type="button" aria-pressed={previewTab === 'history'} onClick={() => setPreviewTab('history')}>History</button>}</nav>
        {previewTab === 'history' && objectTools.history ? objectTools.history : <section className="flow-published-selection-properties"><h4>Properties</h4><dl>{properties.filter(property => Object.prototype.hasOwnProperty.call(preview.properties, property.id)).map(property => <div key={property.id}><dt>{property.name}</dt><dd>{display(preview.properties[property.id])}</dd></div>)}</dl></section>}</div></aside>}
    </div>}
    {mode === 'results' && result && query?.groupBy && <section className="published-explorer-aggregation" aria-label="Object set aggregation"><h3>{properties.find(property => property.id === query.groupBy)?.name || query.groupBy}</h3><table><thead><tr><th>Value</th><th>Count</th>{query.sumField && <th>Sum</th>}</tr></thead><tbody>{result.groups.map((group, index) => <tr key={index}><td>{display(group.value)}</td><td>{group.count}</td>{query.sumField && <td>{display(group.sum)}</td>}</tr>)}</tbody></table>{result.groupsHasMore && <p>Showing the first 50 groups.</p>}</section>}
    {objectType && <PublishedSourceTools objectType={objectType} resource={resource} canManage={canManage} onClose={() => setResource('preview')} />}
    {objectTools.dialogs}
  </section>;
}

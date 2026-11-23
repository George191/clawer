import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { message } from '@/components/ui';
import { fetchPublishedOntologyType, ontologyError, type PublishedOntologyType } from '../../services/ontology';
import { fetchCurrentUserContext } from '../../services/api';
import { openSaved, publishedSavedPath, type SavedDetail } from '../../services/explorerSaved';
import { loadPublishedSavedSet, queryPublishedExplorer, type PublishedExplorerRecord, type PublishedExplorerResult } from '../../services/publishedExplorer';
import { parsePublishedExplorerEntry, publishedExplorerHref, validatePublishedExplorerQuery, type PublishedExploration, type PublishedExplorerEntry, type PublishedExplorerQuery } from '../../services/publishedExplorerHandoff';
import { publishedColumnTypes, publishedPresentation } from '../../services/publishedExplorerPresentation';
import { getSessionState } from '../../services/session';
import type { ExplorerSort } from '../../services/explorerLayouts';
import type { ResourceView } from './ExplorerWorkbenchHeader';
import type { ExplorerPreviewRow } from './ExplorerSelection';
import ExplorerWorkbench, { ExplorerFilterButton, ExplorerResults, ExplorerResultsFooter, explorerResultColumns } from './ExplorerWorkbench';
import ExplorerFilterEditor, { normalizeFilterDepth, randomId } from './ExplorerFilterEditor';
import { firstIncompleteExplorerFilter } from './explorerFilterDraft';
import ExplorerFilterIndexNotice, { ExplorerSortIndexNotice } from './ExplorerFilterIndexNotice';
import { LayoutToolbar, useResultLayout } from './ExplorerResultLayout';
import { activeExplorerFilters, appendAndFilters, blankExplorerFilter, cellFilterRows, countFilterTerms, exactCellValue, explorerFilterTerms, hasExplorerFilterInput, type ExplorerFilter } from './explorerFilters';
import { explorerResourceCapabilities } from '../../services/explorerCapabilities';
import PublishedSourceTools from './PublishedSourceTools';
import PublishedExplorerRelationships from './PublishedExplorerRelationships';
import { usePublishedObjectTools } from './PublishedObjectTools';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import ExplorerRecordLink from './ExplorerRecordLink';
import ExplorerCatalog from './ExplorerCatalog';
import './published-explorer.css';
import useExplorerErrorToast from './useExplorerErrorToast';

const display = (value: unknown): string => value == null ? 'null' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const identityKey = (row: PublishedExplorerRecord, index: number) => row.identity ? JSON.stringify(Object.entries(row.identity).sort(([a], [b]) => a.localeCompare(b))) : 'row:' + index;
type Scope = { key: string; query: PublishedExplorerQuery; saved?: SavedDetail<PublishedExploration> };

function commonFilters(query: PublishedExplorerQuery, objectType: PublishedOntologyType): ExplorerFilter[] {
  const jsonFields = new Set(objectType.definition.properties.filter(property => property.type === 'json').map(property => property.id));
  const inherited = query.filters.flatMap((filter, index) => {
    const id = 'semantic-' + index;
    if (filter.op === 'eq' && (filter.value === null || filter.value === '')) return cellFilterRows(filter.field, filter.value, false, id);
    return [{ ...blankExplorerFilter(id), field: filter.field,
      operator: filter.op === 'contains' && filter.value === '' ? '不是  null' : { eq: '=', contains: '包含（不区分大小写）', gt: '>', lt: '<' }[filter.op],
      value: jsonFields.has(filter.field) && filter.op === 'eq' || typeof filter.value === 'object' ? JSON.stringify(filter.value) : String(filter.value) }];
  });
  return normalizeFilterDepth(appendAndFilters(query.workbenchFilters || [], inherited, 'semantic-scope'));
}
const wireFilters = (rules: ExplorerFilter[]) => rules.filter(rule => hasExplorerFilterInput(rule) || /^group-(open|close)-/.test(rule.id))
  .map(({ id, field, operator, value, active, join }) => ({ id, field, operator, value, active, join }));

export default function PublishedExplorer() {
  const location = useLocation();
  const parsed = useMemo(() => {
    try { return { entry: parsePublishedExplorerEntry(location.search, location.hash), error: '' }; }
    catch (error) { return { entry: undefined, error: (error as Error).message }; }
  }, [location.search, location.hash]);
  useExplorerErrorToast(parsed.error, 'Cannot open this object set');
  if (!parsed.entry) return <section className="flow-exploration-empty" aria-label="Invalid published exploration"><h2>Cannot open this object set</h2><Link to="/ontology?catalog=published">Browse Published object types</Link></section>;
  return <Workspace key={parsed.entry.definitionId} entry={parsed.entry} routeKey={location.search + location.hash} />;
}

function Workspace({ entry, routeKey }: { entry: PublishedExplorerEntry; routeKey: string }) {
  const navigate = useNavigate();
  const [objectType, setObjectType] = useState<PublishedOntologyType>();
  const [canManage, setCanManage] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [scope, setScope] = useState<Scope>();
  const [scopeError, setScopeError] = useState('');
  const [scopeRetry, retryScope] = useState(0);
  const [read, setRead] = useState<{ key: string; page?: PublishedExplorerResult; error?: string }>();
  const [offset, setOffset] = useState(0), [pageSize, setPageSize] = useState(20);
  const [version, retry] = useState(0);
  const [mode, setMode] = useState<'results' | 'explore'>('results');
  const [chosen, setChosen] = useState<ExplorerPreviewRow[]>([]);
  const [activeRow, setActiveRow] = useState<ExplorerPreviewRow>();
  const [resource, setResource] = useState<ResourceView>('preview');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [filterRules, setFilterRules] = useState<ExplorerFilter[]>([blankExplorerFilter('rule-1')]);
  const [filterValidationRequest, setFilterValidationRequest] = useState(0);
  useEffect(() => setFilterValidationRequest(0), [filterRules]);
  const [draftSorts, setDraftSorts] = useState<ExplorerSort[]>([]);
  const navigationDraft = useRef<{ route: string; mode: typeof mode; rules?: ExplorerFilter[] }>();
  const initialLayoutSorts = useRef(!entry.saved && !entry.savedSet && !entry.query.sorts?.length).current;
  const originals = useRef(new Map<string, PublishedExplorerRecord>());
  const sessionIdentity = getSessionState().identity;
  const current = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === sessionIdentity;
  };
  const scopeKey = JSON.stringify([routeKey, sessionIdentity]);
  const definitionLoaded = objectType?.id === entry.definitionId;
  useEffect(() => {
    const controller = new AbortController();
    setMetadataError('');
    Promise.all([fetchPublishedOntologyType(entry.definitionId, controller.signal), fetchCurrentUserContext(controller.signal)]).then(([result, context]) => {
      if (current(controller.signal)) { setObjectType(result); setCanManage(!!context.user.is_superuser); }
    }, error => { if (current(controller.signal) && error?.code !== 'ERR_CANCELED') {
      const detail = ontologyError(error, 'Unable to load this published object type.'); setMetadataError(detail);
    } });
    return () => controller.abort();
  }, [entry.definitionId, sessionIdentity, version]);
  useEffect(() => {
    if (!definitionLoaded) return;
    const controller = new AbortController();
    setScopeError(''); setOffset(0); setChosen([]); setActiveRow(undefined); setRead(undefined); originals.current.clear();
    async function load() {
      let query = entry.query, saved: SavedDetail<PublishedExploration> | undefined;
      if (entry.saved) {
        saved = await openSaved<PublishedExploration>(publishedSavedPath(entry.definitionId), entry.saved, controller.signal);
        query = saved.kind === 'list' ? { filters: [], keys: saved.content.keys ?? [] } : validatePublishedExplorerQuery(saved.content.query);
      } else if (entry.savedSet) {
        const result = await loadPublishedSavedSet(entry.definitionId, entry.savedSet, controller.signal);
        query = validatePublishedExplorerQuery(result.query);
      }
      if (current(controller.signal)) {
        const draft = navigationDraft.current?.route === routeKey ? navigationDraft.current : undefined;
        navigationDraft.current = undefined;
        setScope({ key: scopeKey, query, saved });
        setMode(draft?.mode ?? (saved?.kind === 'exploration' ? saved.content.view : 'results'));
        const rules = draft?.rules ?? commonFilters(query, objectType!);
        setFilterRules(rules.length ? rules : [blankExplorerFilter('rule-1')]);
        setDraftSorts(query.sorts || []);
      }
    }
    void load().catch(error => { if (current(controller.signal) && error?.code !== 'ERR_CANCELED') {
      const detail = ontologyError(error, 'Unable to open this object set.'); setScopeError(detail);
    } });
    return () => controller.abort();
  }, [entry, scopeKey, sessionIdentity, definitionLoaded, scopeRetry]);
  const activeScope = scope?.key === scopeKey ? scope : undefined;
  const layoutIdentity = useRef('');
  if (activeScope) layoutIdentity.current = sessionIdentity;
  const query = activeScope?.query;
  const requestKey = JSON.stringify([scopeKey, offset, pageSize, version]);
  useEffect(() => {
    if (!query || !objectType) return;
    const controller = new AbortController();
    queryPublishedExplorer(entry.definitionId, query, pageSize, offset, controller.signal).then(page => {
      if (current(controller.signal)) setRead({ key: requestKey, page });
    }, error => { if (current(controller.signal) && error?.code !== 'ERR_CANCELED') {
      const detail = ontologyError(error, 'Unable to query this object set.'); setRead({ key: requestKey, error: detail });
    } });
    return () => controller.abort();
  }, [entry.definitionId, query, objectType, offset, pageSize, requestKey, sessionIdentity]);
  const currentRead = read?.key === requestKey ? read : undefined;
  const result = currentRead?.page;
  const fatalError = metadataError || scopeError;
  const error = fatalError || currentRead?.error;
  useExplorerErrorToast(metadataError, 'Unable to load object type', () => retry(value => value + 1));
  useExplorerErrorToast(scopeError, 'Unable to open object set', () => { retryScope(value => value + 1); retry(value => value + 1); });
  useExplorerErrorToast(currentRead?.error, 'Unable to query object set', () => retry(value => value + 1));
  const busy = !error && (!objectType || !activeScope || !currentRead);
  const properties = objectType?.definition.properties || [];
  const fields = useMemo(() => properties.map(property => property.id), [objectType]);
  const sortFields = useMemo(() => properties.filter(property => property.type !== 'json').map(property => property.id), [objectType]);
  const names = useMemo(() => Object.fromEntries(properties.map(property => [property.id, property.name])), [objectType]);
  const types = useMemo(() => objectType ? publishedColumnTypes(objectType) : {}, [objectType]);
  const metadata = useMemo(() => objectType ? { presentation: publishedPresentation(objectType) } : undefined, [objectType]);
  const rows = useMemo(() => result?.items.map(row => row.properties) || [], [result]);
  const previewRows = useMemo(() => result?.items.map((record, index) => {
    const key = identityKey(record, offset + index); originals.current.set(key, record);
    return { key, record: record.properties, identity: record.identity || {}, title: display(record.title), columnTypes: types, version: record.revision == null ? undefined : String(record.revision) };
  }) || [], [result, types, offset]);
  useEffect(() => {
    if (!result) return;
    const current = new Map(previewRows.map(row => [row.key, row]));
    setChosen(previous => previous.map(row => current.get(row.key) || row));
  }, [previewRows, result]);
  const original = (row?: ExplorerPreviewRow) => row ? originals.current.get(row.key) : undefined;
  const objectTools = usePublishedObjectTools({ objectType, actions: result?.actions || [], record: original(activeRow), returnTo: entry.returnTo, busy: busy || !!error,
    onChanged: () => { setActiveRow(undefined); setChosen([]); retry(value => value + 1); } });
  const activeFilters = useMemo(() => query && objectType ? commonFilters(query, objectType) : [], [query, objectType]);
  const sorts = query?.sorts || [];
  const filterCount = countFilterTerms(explorerFilterTerms(activeFilters)) + Number(Boolean(query?.search?.trim()));
  function changeQuery(next: PublishedExplorerQuery, keepDraft = false) {
    let href: string;
    try { href = publishedExplorerHref(entry.definitionId, { query: next, returnTo: entry.returnTo }); }
    catch (error) { message.error((error as Error).message); return false; }
    const url = new URL(href, window.location.origin);
    navigationDraft.current = { route: url.search + url.hash, mode, ...(keepDraft ? { rules: filterRules } : {}) };
    navigate(href, { replace: true });
    return true;
  }
  const canChange = () => !!query && !busy && !fatalError && !objectTools.mutationOpen;
  function requireDraftApplied(sorting = false) {
    if (!canChange()) return false;
    const pending = sorting ? JSON.stringify(draftSorts.filter(item => item.field)) !== JSON.stringify(sorts)
      : JSON.stringify(wireFilters(filterRules)) !== JSON.stringify(wireFilters(activeFilters));
    if (!pending) return true;
    setFiltersOpen(true); message.warning(sorting ? 'Apply your pending sort edits before using this shortcut' : 'Apply your pending column filter edits before using this shortcut');
    return false;
  }
  function applyRules(rules: ExplorerFilter[]) {
    if (!canChange()) return;
    if (rules.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
    changeQuery({ ...query!, filters: [], workbenchFilters: wireFilters(normalizeFilterDepth(rules)) });
  }
  function updateSorts(next: ExplorerSort[]) {
    if (query && !fatalError && !objectTools.mutationOpen) changeQuery({ ...query, sorts: next.filter(item => item.field) }, true);
  }
  function addFilter(field: string) {
    if (!canChange() || !canAppendFilter()) return;
    const id = randomId(), next = appendAndFilters(wireFilters(filterRules), [{ ...blankExplorerFilter(id), field }], randomId());
    if (next.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
    setFilterRules(normalizeFilterDepth(next)); setFiltersOpen(true);
  }
  function canAppendFilter() {
    if ((!filtersOpen && !filterRules.some(rule => rule.field || rule.value)) || !firstIncompleteExplorerFilter(filterRules)) return true;
    setMode('results'); setFiltersOpen(true); setFilterValidationRequest(value => value + 1);
    return false;
  }
  function openColumnFilter(field: string) {
    if (!canChange()) return;
    const applied = activeExplorerFilters(activeFilters).find(rule => rule.field === field);
    const row = filterRules.find(rule => rule.id === applied?.id) || filterRules.find(rule => rule.field === field);
    setMode('results'); setFiltersOpen(true);
    if (row) window.requestAnimationFrame(() => {
      const element = document.getElementById(`explorer-filter-${row.id}`);
      element?.scrollIntoView({ block: 'nearest' });
      element?.querySelector<HTMLElement>('[role="button"]')?.focus();
    });
  }
  function openSavedItem(id: string) {
    navigate(publishedExplorerHref(entry.definitionId, { ...(entry.savedSet ? { savedSet: entry.savedSet } : { query: entry.query }), saved: id, returnTo: entry.returnTo }));
  }
  const resultLayout = useResultLayout(layoutIdentity.current, '', '', fields, sorts, updateSorts, initialLayoutSorts, entry.definitionId);
  const dataColumns = explorerResultColumns({ layout: resultLayout, sorts, sortFields, filters: activeFilters, openFilter: openColumnFilter, types, names, addFilter,
    setSort: (field, direction) => { if (requireDraftApplied(true)) updateSorts([{ field, direction }, ...sorts.filter(item => item.field !== field)]); },
    clearSorts: () => { if (requireDraftApplied(true)) updateSorts([]); },
    filterValue: (field, value, exclude) => { if (canChange() && canAppendFilter() && requireDraftApplied()) applyRules(appendAndFilters(activeFilters, cellFilterRows(field, value, exclude, randomId()), randomId())); },
  });
  const selectionSource = { kind: 'published' as const, definitionId: entry.definitionId };
  return <div className={`flow-layers flow-layers--tagged flow-schema-workbench explorer-object-workspace has-asset${catalogOpen ? ' is-catalog-open' : ''}${mode === 'explore' && resource === 'preview' ? ' is-exploring' : ''}`}>
    <div className="flow-layer-layout"><ExplorerCatalog published={{ selectedId: entry.definitionId, onSelect: id => { if (canChange()) navigate(publishedExplorerHref(id)); } }} overlayOpen={catalogOpen} onDismiss={() => setCatalogOpen(false)} /><section className="flow-layer-main">
      <ExplorerWorkbench<PublishedExploration> locked={busy || !!fatalError || objectTools.mutationOpen} header={{ source: selectionSource,
        label: objectType?.definition.name || entry.definitionId, available: !!objectType, glyph: objectType && <SemanticTypeGlyph definition={objectType.definition} />,
        fields, names, types, rows, appliedSearch: query?.search,
        onClearSearch: () => { if (query && canChange()) changeQuery({ ...query, search: '' }); },
        onSearchValues: value => {
          if (!query || !requireDraftApplied() || !requireDraftApplied(true)) return false;
          return changeQuery({ ...query, search: value.trim() }, true);
        },
        onExploreFilterValue: (field, value) => {
          if (!requireDraftApplied()) return;
          const scalar = exactCellValue(value, types[field]);
          if (scalar !== undefined) applyRules(appendAndFilters(activeFilters, cellFilterRows(field, scalar, false, randomId()), randomId()));
        },
        analysisRevision: version,
        aggregate: query ? (field, offset, signal) => queryPublishedExplorer(entry.definitionId, { ...query, groupBy: field, sumField: undefined }, 1, 0, signal, offset) : undefined,
        selected: chosen.filter(row => Object.keys(row.identity).length), query: { query: query || { filters: [] } }, resource, admin: canManage,
        layoutControl: <LayoutToolbar controls={resultLayout} admin={canManage} types={types} />, createControl: objectTools.createButton, selectResource: setResource,
        availableResources: objectType ? explorerResourceCapabilities(objectType.definition.source, canManage) : [], additionalTools: objectTools.tools,
        saved: activeScope?.saved, savedLoading: !activeScope && !scopeError, savedError: scopeError, openSavedItem,
        closeSavedItem: () => navigate(publishedExplorerHref(entry.definitionId, { ...(entry.savedSet ? { savedSet: entry.savedSet } : { query: entry.query }), returnTo: entry.returnTo })),
        saveCompleted: detail => openSavedItem(detail.id), updateSavedItem: detail => setScope(previous => previous?.key === scopeKey ? { ...previous, saved: detail } : previous),
        addFilter, backToTypes: () => navigate(entry.returnTo), toggleCatalog: () => setCatalogOpen(value => !value), closeCatalog: () => setCatalogOpen(false), catalogOpen,
        mode, setMode, clearSelection: () => setChosen([]),
        filterButton: <ExplorerFilterButton open={filtersOpen} count={filterCount + sorts.length} onClick={() => { if (mode === 'explore') { setMode('results'); setFiltersOpen(true); } else setFiltersOpen(value => !value); }} />,
      }}>
        <ExplorerResults results={objectType && query && !error ? { source: selectionSource, schema: '', table: objectType.definition.id, rows, columns: fields,
          columnTypes: types, metadata, dataColumns, selectedRows: chosen, selectionLimit: 100, onSelectedRowsChange: setChosen, onActiveRowChange: setActiveRow,
          rowKey: (record, index) => previewRows[index]?.key || 'row:' + (offset + index),
          rowIdentity: record => previewRows[rows.indexOf(record)]?.identity || {}, rowVersion: record => previewRows[rows.indexOf(record)]?.version,
          loading: busy, locked: objectTools.mutationOpen, onRefresh: () => retry(value => value + 1),
          presentations: Object.fromEntries(resultLayout.columns.map(column => [column.field, column.presentation])),
          renderGlyph: compact => <SemanticTypeGlyph definition={objectType.definition} size={compact ? 'row' : 'preview'} />,
          renderTitleLink: row => <ExplorerRecordLink definitionId={entry.definitionId} identity={row.identity}>{row.title}</ExplorerRecordLink>,
          renderFullDetails: row => <ExplorerRecordLink definitionId={entry.definitionId} identity={row.identity} />,
          rowActions: row => objectTools.actionsForRecord(original(row)),
          renderMutation: row => objectTools.renderMutation(original(row)),
          extraViews: [{ key: 'history', label: 'History', available: row => !!original(row)?.hasHistory, render: row => objectTools.renderHistory(original(row)) }],
          renderPublishedRelationships: objectType.definition.source.mode === 'none' ? row => <PublishedExplorerRelationships definitionId={entry.definitionId} recordId={String(row.identity.$recordId)} sourceTitle={row.title} refreshToken={objectTools.relationsVersion} /> : undefined,
          footer: <>{result && query.groupBy && <section className="published-explorer-aggregation" aria-label="Object set aggregation"><h3>{names[query.groupBy] || query.groupBy}</h3>
            <p>{result.count.toLocaleString()} matching objects{query.sumField ? ` · Sum: ${display(result.sum)}` : ''}</p>
            <table><thead><tr><th>Value</th><th>Count</th>{query.sumField && <th>Sum</th>}</tr></thead><tbody>{result.groups.map((group, index) => <tr key={index}><td>{display(group.value)}</td><td>{group.count}</td>{query.sumField && <td>{display(group.sum)}</td>}</tr>)}</tbody></table>
          </section>}<ExplorerResultsFooter offset={offset} rowCount={rows.length} pageSize={pageSize} loading={busy} locked={objectTools.mutationOpen} canPageFurther={!!result?.hasMore && offset + pageSize <= 100000}
            onPageSize={size => { if (canChange()) { setPageSize(size); setOffset(0); } }} onPrevious={() => { if (canChange()) setOffset(value => Math.max(0, value - pageSize)); }} onNext={() => { if (canChange()) setOffset(value => value + pageSize); }} /></>,
        } : undefined}>
          {busy && !objectType && <div className="flow-schema-state" role="status">Loading published object set…</div>}
          {query?.keys && <div className="flow-schema-applied flow-query-chips" aria-label="Object set scope"><span>{query.keys.length} object references{result ? ` · ${result.count} matching objects` : ''}</span></div>}
          {filtersOpen && query && <ExplorerFilterEditor rules={filterRules} setRules={setFilterRules} sorts={draftSorts}
            validationRequest={filterValidationRequest}
            renderRuleNotice={objectType?.definition.source.mode === 'existing' && canManage ? rule => {
              const property = properties.find(property => property.id === rule.field);
              if (!property?.sourceColumn || !activeExplorerFilters(filterRules).some(item => item.id === rule.id)) return null;
              const source = objectType.definition.source;
              return <ExplorerFilterIndexNotice schema={source.schema!} table={source.table!} rule={{ ...rule, field: property.sourceColumn }}
                columnTypes={{ [property.sourceColumn]: types[rule.field] }} />;
            } : undefined}
            renderSortNotice={objectType?.definition.source.mode === 'existing' && canManage ? sorts => {
              const source = objectType.definition.source;
              const mapped = sorts.filter(sort => sort.field).map(sort => ({ ...sort, field: properties.find(property => property.id === sort.field)?.sourceColumn || '' }));
              if (mapped.some(sort => !sort.field)) return <span className="explorer-filter-index-notice" role="status">Index not assessed</span>;
              return <ExplorerSortIndexNotice schema={source.schema!} table={source.table!} sorts={mapped} />;
            } : undefined}
            onSortsChange={(next, apply) => { setDraftSorts(next); if (apply) updateSorts(next); }} fields={fields} sortFields={sortFields} rows={rows} formatValue={display}
            onApply={() => { if (canChange()) changeQuery({ ...query, filters: [], workbenchFilters: wireFilters(normalizeFilterDepth(filterRules)), sorts: draftSorts.filter(item => item.field) }); }} />}
        </ExplorerResults>
      </ExplorerWorkbench>
    </section></div>
    {objectType && <PublishedSourceTools objectType={objectType} resource={resource} canManage={canManage} onClose={() => setResource('preview')} />}
    {objectTools.dialogs}
  </div>;
}

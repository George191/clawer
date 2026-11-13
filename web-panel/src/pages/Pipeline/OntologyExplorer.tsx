import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Modal, Select } from '@/components/ui';
import {
  ApartmentOutlined, AppstoreOutlined, ArrowLeftOutlined, ArrowRightOutlined, BarsOutlined,
  CloseOutlined, DatabaseOutlined, EyeOutlined, InfoCircleOutlined,
  PlusOutlined, ReloadOutlined, SearchOutlined, StarFilled, StarOutlined, TableOutlined,
} from '@/components/ui/icons';
import { fetchCurrentUserContext, fetchEtlObjectPreview, fetchEtlObjectTypes, fetchLayers } from '../../services/api';
import type { EtlObjectCatalog, EtlObjectPreview, EtlObjectType } from '../../services/types';
import { layerColors } from '../../theme/tokens';
import './ontology-explorer.css';

type Group = 'all' | 'favorites' | 'ingestion' | 'processing' | 'serving' | 'other';
const groups: Array<{ key: Group; label: string }> = [
  { key: 'all', label: 'All object types' }, { key: 'favorites', label: 'Favorites' },
  { key: 'ingestion', label: 'Ingestion' }, { key: 'processing', label: 'Processing' },
  { key: 'serving', label: 'Serving' }, { key: 'other', label: 'Other object types' },
];
const groupFor = (schema: string): Group => {
  const key = schema.replace(/_hist$/, '');
  if (['ts_rds', 'ts_ods'].includes(key)) return 'ingestion';
  if (['ts_task', 'ts_dwd', 'ts_dws', 'ts_dim'].includes(key)) return 'processing';
  return key === 'ts_ads' ? 'serving' : 'other';
};
const favoriteStorageKey = 'flow-favorite-object-types';
const pageSize = 60;
const emptyCatalog: EtlObjectCatalog = { items: [], total: 0, limit: pageSize, offset: 0, schemaCounts: {}, groupCounts: {} };
const accentStyle = (id: string) => ({ '--asset-color': schemaColor(id) } as React.CSSProperties);
const number = (value: number) => value.toLocaleString('en-US');
function PropertyIcon({ type }: { type: string }) {
  const value = type.toLowerCase();
  const glyph = /^(date|time|timestamp|interval)(\s|\(|$)/.test(value)
    ? <><circle cx="8" cy="8" r="6" /><path d="M8 4v4l2.5 1.5" /></>
    : /^(smallint|int[248]?|integer|bigint|numeric|decimal|real|double precision|float[48]?|smallserial|serial|bigserial|money)\b/.test(value)
      ? <text x="8" y="11" textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="9.5" fill="currentColor" stroke="none">123</text>
      : /^bool/.test(value) ? <path d="m3 8 3.2 3.2L13 4.5" />
        : /^json/.test(value) ? <path d="M6 2.5H5v3L3.5 8 5 10.5v3h1m4-11h1v3L12.5 8 11 10.5v3h-1" />
          : <path fill="currentColor" stroke="none" d="M2 4h5v4c0 2.1-1.4 3.5-4 4l-.5-1.3C4 10.2 4.7 9.4 4.7 8H2V4Zm7 0h5v4c0 2.1-1.4 3.5-4 4l-.5-1.3c1.5-.5 2.2-1.3 2.2-2.7H9V4Z" />;
  return <span className="flow-ontology__property-icon" title={type} aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" focusable="false">{glyph}</svg></span>;
}

export function schemaColor(key: string): string {
  const normalized = key.trim().toLowerCase();
  const known = normalized.replace(/^ts_/, '').toUpperCase();
  if (layerColors[known]) return layerColors[known];
  if (known === 'DIM') return '#38BDF8';
  let hash = 2166136261;
  for (const character of normalized) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  hash = Math.imul(hash ^ (hash >>> 16), 0x7feb352d);
  hash = Math.imul(hash ^ (hash >>> 15), 0x846ca68b);
  hash ^= hash >>> 16;
  return `hsl(${((hash >>> 0) / 4294967296 * 360).toFixed(3)} 68% 66%)`;
}

export default function OntologyExplorer({ refreshToken = 0 }: { refreshToken?: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const keyword = params.get('q') || '';
  const group: Group = groups.some((item) => item.key === params.get('group')) ? params.get('group') as Group : 'all';
  const view = params.get('view') === 'list' ? 'list' : 'grid';
  const selectedSchema = params.get('schema') || '';
  const selectedTable = params.get('table') || '';
  const selectedId = selectedSchema && selectedTable ? `${selectedSchema}.${selectedTable}` : '';
  const source = params.get('source') || (!selectedTable ? selectedSchema : '');
  const allProperties = params.get('tab') === 'properties';
  const page = Math.max(1, Math.min(16667, Number.parseInt(params.get('page') || '1', 10) || 1));
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [mobilePreview, setMobilePreview] = useState(() => window.matchMedia('(max-width: 1150px)').matches);
  const [catalog, setCatalog] = useState<EtlObjectCatalog>(emptyCatalog);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [schemaNames, setSchemaNames] = useState<string[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createSchema, setCreateSchema] = useState<string>();
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(favoriteStorageKey) || '[]');
      return Array.isArray(saved) ? [...new Set(saved.filter((id): id is string => typeof id === 'string' && /^ts_[a-z0-9_]+\.[a-z_][a-z0-9_]*$/.test(id) && !['ts_meta', 'ts_meta_hist'].includes(id.split('.')[0])))] : [];
    } catch { return []; }
  });
  const [preview, setPreview] = useState<{ id: string; data?: EtlObjectPreview; loading: boolean; error: string }>({ id: '', loading: false, error: '' });
  const [previewVersion, setPreviewVersion] = useState(0);
  const favoriteQuery = group === 'favorites' ? favorites.join(',') : '';

  useEffect(() => {
    rootRef.current?.querySelector<HTMLElement>('.flow-ontology__body')?.scrollTo({ top: 0 });
  }, [page, group, source, keyword, view]);

  useEffect(() => {
    let active = true;
    Promise.allSettled([fetchLayers(), fetchCurrentUserContext()]).then(([layers, context]) => {
      if (!active) return;
      if (layers.status === 'fulfilled') setSchemaNames(layers.value.filter((item) => item.status === 'running').map((item) => item.schemaName || (item.key.startsWith('ts_') ? item.key : `ts_${item.key}`)).filter((name) => !['ts_meta', 'ts_meta_hist'].includes(name)));
      if (context.status === 'fulfilled') setCanManage(!!context.value.user.is_superuser);
    });
    return () => { active = false; };
  }, [version, refreshToken]);
  useEffect(() => {
    try { localStorage.setItem(favoriteStorageKey, JSON.stringify(favorites)); } catch { /* Browsing remains available without persistent storage. */ }
  }, [favorites]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    const timer = window.setTimeout(async () => {
      try {
        const query = { schema: source || undefined, search: keyword.trim() || undefined };
        let result: EtlObjectCatalog;
        if (group === 'favorites') {
          const ids = favoriteQuery ? favoriteQuery.split(',') : [];
          const items: EtlObjectType[] = [];
          const schemaCounts: Record<string, number> = {};
          const groupCounts: Record<string, number> = {};
          for (let start = 0; start < ids.length; start += 40) {
            const part = await fetchEtlObjectTypes({ ...query, ids: ids.slice(start, start + 40).join(','), limit: 40 });
            if (!active) return;
            items.push(...part.items);
            Object.entries(part.schemaCounts).forEach(([key, count]) => { schemaCounts[key] = (schemaCounts[key] || 0) + count; });
            Object.entries(part.groupCounts).forEach(([key, count]) => { groupCounts[key] = (groupCounts[key] || 0) + count; });
          }
          result = { items: items.slice((page - 1) * pageSize, page * pageSize), total: items.length, limit: pageSize, offset: (page - 1) * pageSize, schemaCounts, groupCounts };
        } else result = await fetchEtlObjectTypes({ ...query, group: group === 'all' ? undefined : group, limit: pageSize, offset: (page - 1) * pageSize });
        if (!active) return;
        setCatalog(result);
        const lastPage = Math.max(1, Math.ceil(result.total / pageSize));
        if (page > lastPage) setParams((current) => { const next = new URLSearchParams(current); if (lastPage === 1) next.delete('page'); else next.set('page', String(lastPage)); return next; }, { replace: true });
      } catch { if (active) setError('Unable to load object types. Refresh to try again.'); }
      finally { if (active) setLoading(false); }
    }, keyword ? 200 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [source, keyword, group, page, favoriteQuery, version, refreshToken, setParams]);
  useEffect(() => {
    if (!selectedId) { setPreview({ id: '', loading: false, error: '' }); return; }
    let active = true;
    setPreview({ id: selectedId, loading: true, error: '' });
    fetchEtlObjectPreview(selectedSchema, selectedTable).then((data) => {
      if (active) setPreview({ id: selectedId, data, loading: false, error: '' });
    }).catch(() => { if (active) setPreview({ id: selectedId, loading: false, error: 'Unable to load this object type. Try again.' }); });
    return () => { active = false; };
  }, [selectedId, selectedSchema, selectedTable, previewVersion, version, refreshToken]);

  const updateQuery = (changes: Record<string, string | null>, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    return next;
  }, { replace });
  const selectObject = (object: { schema: string; table: string }) => updateQuery({ schema: object.schema, table: object.table, tab: null });
  const closeInspector = () => {
    updateQuery({ schema: null, table: null, tab: null }, true);
    requestAnimationFrame(() => (cardRefs.current[selectedId] || searchRef.current)?.focus());
  };
  const openWorkspace = () => {
    if (selectedId) navigate(`/flow/layers?${new URLSearchParams({ schema: selectedSchema, table: selectedTable })}`);
  };
  const startNewObject = () => {
    const destination = source || selectedSchema;
    if (destination && schemaNames.includes(destination)) navigate(`/flow/layers?${new URLSearchParams({ schema: destination, create: 'table' })}`);
    else { setCreateSchema(undefined); setCreateOpen(true); }
  };
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1150px)');
    const resize = () => setMobilePreview(query.matches);
    query.addEventListener('change', resize);
    return () => query.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); searchRef.current?.focus(); searchRef.current?.select(); }
      else if (event.key === 'Escape' && selectedId && ![...document.querySelectorAll<HTMLElement>('[data-ui-popup],.ui-modal-wrap')].some((element) => element.getClientRects().length > 0)) { event.preventDefault(); closeInspector(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [selectedId, params]);
  useEffect(() => {
    if (!selectedId) return;
    inspectorRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    if (!mobilePreview) return;
    const background = [rootRef.current?.querySelector<HTMLElement>('.flow-ontology__workspace'), document.querySelector<HTMLElement>('.ui-layout-header'), document.querySelector<HTMLElement>('[data-testid="project-sidebar"]')].filter((element): element is HTMLElement => !!element);
    const before = background.map((element) => element.inert);
    background.forEach((element) => { element.inert = true; });
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const controls = [...(inspectorRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), [tabindex="0"]') || [])].filter((element) => element.getClientRects().length > 0);
      const first = controls[0]; const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', trapFocus);
    return () => { document.removeEventListener('keydown', trapFocus); background.forEach((element, index) => { element.inert = before[index]; }); };
  }, [selectedId, mobilePreview]);

  const sections = useMemo(() => view === 'list' || group !== 'all' || keyword.trim() || source
    ? [{ key: group, label: groups.find((item) => item.key === group)!.label, items: catalog.items }]
    : groups.filter((item) => item.key !== 'all' && item.key !== 'favorites').map((item) => ({ key: item.key, label: item.label, items: catalog.items.filter((object) => groupFor(object.schema) === item.key) })).filter((section) => section.items.length), [catalog.items, view, group, keyword, source]);
  const renderedObjects = sections.flatMap((section) => section.items);
  const moveCardFocus = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const current = event.currentTarget.getBoundingClientRect();
      const candidates = renderedObjects.map((object) => cardRefs.current[object.id]).filter((button): button is HTMLButtonElement => !!button).map((button) => ({ button, rect: button.getBoundingClientRect() }))
        .filter(({ rect }) => event.key === 'ArrowDown' ? rect.top > current.top + 1 : rect.top < current.top - 1)
        .sort((a, b) => Math.abs(a.rect.top - current.top) - Math.abs(b.rect.top - current.top) || Math.abs(a.rect.left - current.left) - Math.abs(b.rect.left - current.left));
      candidates[0]?.button.focus(); return;
    }
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? renderedObjects.length - 1 : Math.max(0, Math.min(renderedObjects.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
    cardRefs.current[renderedObjects[next]?.id]?.focus();
  };
  const data = preview.id === selectedId ? preview.data : undefined;
  const identity = data || catalog.items.find((object) => object.id === selectedId);
  const selectedColor = accentStyle(selectedId);
  const knownSchemas = [...new Set([...schemaNames, ...Object.keys(catalog.schemaCounts)])].filter((name) => !['ts_meta', 'ts_meta_hist'].includes(name)).sort();
  const groupTotal = Object.values(catalog.groupCounts).reduce((sum, count) => sum + count, 0);
  const visibleProperties = data ? allProperties ? data.properties : data.properties.slice(0, 6) : [];

  return <div ref={rootRef} className="flow-ontology" data-testid="ontology-explorer">
    <div className="flow-ontology__workspace">
      <header className="flow-ontology__hero" aria-label="Object type catalog">
        <div className="flow-ontology__topbar">
          <div className="flow-ontology__search" role="search">
            <select aria-label="Filter schema" value={source} onChange={(event) => updateQuery({ source: event.target.value || null, page: null, ...(!selectedTable ? { schema: null } : {}) })}><option value="">All schemas</option>{knownSchemas.map((schema) => <option key={schema} value={schema}>{schema}</option>)}</select>
            <SearchOutlined /><input ref={searchRef} data-testid="ontology-search" aria-label="Search object types" value={keyword} onChange={(event) => updateQuery({ q: event.target.value || null, page: null }, true)} onKeyDown={(event) => { if (event.key === 'ArrowDown' && renderedObjects.length) { event.preventDefault(); cardRefs.current[renderedObjects[0].id]?.focus(); } }} placeholder="Search object types..." /><kbd className="flow-ontology__search-shortcut">Ctrl K</kbd>{keyword && <button type="button" aria-label="Clear search" onClick={() => { updateQuery({ q: null, page: null }, true); searchRef.current?.focus(); }}><CloseOutlined /></button>}
            <button type="button" className="flow-ontology__search-refresh" aria-label="Refresh catalog" title="Refresh catalog" disabled={loading} onClick={() => setVersion((value) => value + 1)}><ReloadOutlined spin={loading} /></button>
          </div>
          <div className="flow-ontology__view-switch" aria-label="Catalog view"><button type="button" aria-label="Grid view" aria-pressed={view === 'grid'} className={view === 'grid' ? 'is-active' : ''} onClick={() => updateQuery({ view: null })}><AppstoreOutlined /><span>Grid</span></button><button type="button" aria-label="List view" aria-pressed={view === 'list'} className={view === 'list' ? 'is-active' : ''} onClick={() => updateQuery({ view: 'list' })}><BarsOutlined /><span>List</span></button></div>
          <span className="flow-ontology__summary">{number(catalog.total)} object types</span>
          {canManage && <button type="button" className="flow-ontology__button" disabled={!schemaNames.length} onClick={startNewObject}><PlusOutlined />New object type</button>}
        </div>
      </header>
      <div className="flow-ontology__body">
        <aside className="flow-ontology__sidebar" aria-label="Object type groups"><nav>
          {groups.map((item) => <React.Fragment key={item.key}>{item.key === 'ingestion' && <div className="flow-ontology__sidebar-title">Object type groups</div>}<button type="button" data-group={item.key} aria-pressed={group === item.key} className={group === item.key ? 'is-active' : ''} onClick={() => updateQuery({ group: item.key === 'all' ? null : item.key, page: null })}><span>{item.label}</span><small>{number(item.key === 'all' ? groupTotal : item.key === 'favorites' ? favorites.length : catalog.groupCounts[item.key] || 0)}</small></button></React.Fragment>)}
        </nav><button type="button" className="flow-ontology__sidebar-link" onClick={() => navigate('/flow/lineage')}><ApartmentOutlined /><span>Lineage &amp; impact</span><ArrowRightOutlined /></button></aside>
        <main className="flow-ontology__main" aria-label="Object types" aria-busy={loading}>
          {error && <div className="flow-ontology__notice" role="alert"><span>{error}</span><button type="button" onClick={() => setVersion((value) => value + 1)}>Retry</button></div>}
          {loading && !catalog.items.length ? <div className="flow-ontology__empty" role="status"><ReloadOutlined spin /><span>Loading object types</span></div> : !catalog.items.length ? <div className="flow-ontology__empty"><DatabaseOutlined /><strong>{group === 'favorites' ? 'No favorite object types' : 'No object types found'}</strong><span>{group === 'favorites' ? 'Star an object type to keep it here.' : 'Try a different schema or search.'}</span></div> : <div className={`flow-ontology__collections is-${view}`} data-testid="ontology-assets">
            {sections.map((section) => <section key={section.key} className="flow-ontology__collection"><header className="flow-ontology__collection-heading"><h2>{section.label}</h2></header><div className={`flow-ontology__assets is-${view}`}>
              {section.items.map((object) => <article key={object.id} data-testid="ontology-object-card" data-object-id={object.id} data-schema={object.schema} className={`flow-ontology__asset${selectedId === object.id ? ' is-selected' : ''}`} style={accentStyle(object.id)}>
                <button ref={(element) => { cardRefs.current[object.id] = element; }} type="button" className="flow-ontology__asset-main" aria-label={`Preview ${object.id}`} aria-pressed={selectedId === object.id} onKeyDown={(event) => moveCardFocus(event, renderedObjects.findIndex((item) => item.id === object.id))} onClick={() => selectObject(object)}>
                  <span className="flow-ontology__asset-icon"><TableOutlined /></span><span className="flow-ontology__asset-name"><span><strong title={object.displayName}>{object.displayName}</strong><small className="flow-ontology__table-count" title={object.objectCountEstimated ? 'Estimated object count' : 'Object count'}>{object.objectCountEstimated ? '~' : ''}{number(object.objectCount)}</small></span><code>{object.schema}</code></span><span className="flow-ontology__preview-action" aria-hidden="true"><InfoCircleOutlined /></span>
                </button><button type="button" className={`flow-ontology__favorite${favorites.includes(object.id) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(object.id) ? 'Unstar' : 'Star'} ${object.id}`} aria-pressed={favorites.includes(object.id)} onClick={() => setFavorites((current) => current.includes(object.id) ? current.filter((id) => id !== object.id) : [...current, object.id])}>{favorites.includes(object.id) ? <StarFilled /> : <StarOutlined />}</button>
              </article>)}
            </div></section>)}
          </div>}
          <footer className="flow-ontology__catalog-footer"><span>{loading ? 'Updating catalog…' : `${number(catalog.total)} object types`}</span><div className="flow-ontology__pagination"><button type="button" className="flow-ontology__button" aria-label="Previous catalog page" disabled={loading || page <= 1} onClick={() => updateQuery({ page: page === 2 ? null : String(page - 1) })}><ArrowLeftOutlined /></button><span>Page {page} of {Math.max(1, Math.ceil(catalog.total / pageSize))}</span><button type="button" className="flow-ontology__button" aria-label="Next catalog page" disabled={loading || page * pageSize >= catalog.total} onClick={() => updateQuery({ page: String(page + 1) })}><ArrowRightOutlined /></button></div></footer>
        </main>
      </div>
    </div>
    {selectedId && mobilePreview && <button type="button" className="flow-ontology__preview-backdrop" aria-label="Dismiss asset preview" tabIndex={-1} onClick={closeInspector} />}
    {selectedId && <aside ref={inspectorRef} className="flow-ontology__inspector" data-testid="ontology-inspector" data-object-id={selectedId} style={selectedColor} role={mobilePreview ? 'dialog' : undefined} aria-modal={mobilePreview ? true : undefined} aria-label={`${selectedId} Object type preview`}>
      <header className="flow-ontology__inspector-toolbar"><span className="flow-ontology__asset-icon"><TableOutlined /></span><div className="flow-ontology__preview-identity"><h2 title={selectedId}>{identity?.displayName || selectedTable}</h2><span>Object Type</span></div><button type="button" className="flow-ontology__icon-button" aria-label="Close asset preview" onClick={closeInspector}><CloseOutlined /></button></header>
      <div className="flow-ontology__inspector-content" aria-busy={preview.loading}>
        {preview.loading || preview.id !== selectedId ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>Loading object type</span></div> : preview.error ? <div className="flow-ontology__notice" role="alert"><span>{preview.error}</span><button type="button" onClick={() => setPreviewVersion((value) => value + 1)}>Retry</button></div> : data && <>
          <section className="flow-ontology__preview-section"><h3>Description</h3><p className="flow-ontology__muted">{data.description || 'No description provided.'}</p></section>
          <section className="flow-ontology__preview-section"><div className="flow-ontology__visibility"><h3>Visibility</h3><span className="flow-ontology__visibility-badge is-unconfigured" title="Object-level visibility is not configured. This resource uses the existing signed-in ETL access policy."><EyeOutlined />Not configured</span></div></section>
          <section className="flow-ontology__preview-section"><header><h3>Properties <small>({number(data.properties.length)})</small></h3>{allProperties ? <button type="button" className="flow-ontology__preview-back" onClick={() => updateQuery({ tab: null })}><ArrowLeftOutlined />Back</button> : <button type="button" onClick={() => updateQuery({ tab: 'properties' })}>View <ArrowRightOutlined /></button>}</header>
            <div className="flow-ontology__property-list">{visibleProperties.map((property) => <div className="flow-ontology__property-row" key={property.name} title={`${property.type}${property.nullable ? ' · Nullable' : ' · Required'}${property.comment ? ` · ${property.comment}` : ''}`}><PropertyIcon type={property.type} /><span className="flow-ontology__property-name">{property.displayName}</span>{data.titleProperty === property.name && <span className="flow-ontology__property-badge is-title">Title</span>}{property.primaryKey && <span className="flow-ontology__property-badge is-primary">Primary key</span>}</div>)}</div>
            {!data.properties.length && <p className="flow-ontology__muted">No properties defined.</p>}
          </section>
          <section className="flow-ontology__preview-section"><header><h3>Linked object types <small>({number(data.linkedObjectTypes.length)})</small></h3></header><div className="flow-ontology__linked-list">{data.linkedObjectTypes.map((linked) => <button type="button" className="flow-ontology__linked-row" key={linked.id} style={accentStyle(linked.id)} title={`${linked.id} · ${linked.direction}\n${linked.relationships.map((relation) => `${relation.sourceKind === 'logical' ? 'Logical relationship' : 'Foreign key'}: ${relation.sourceSchema}.${relation.sourceTable} (${relation.columns.join(', ')}) → ${relation.targetSchema}.${relation.targetTable} (${relation.referencedColumns.join(', ')})${relation.cardinality ? ` · ${relation.cardinality.replace(/_/g, ' ')}` : ''}${relation.description ? ` · ${relation.description}` : ''}`).join('\n')}`} onClick={() => selectObject(linked)}><span className="flow-ontology__linked-icon"><TableOutlined /></span><span>{linked.displayName}</span></button>)}</div>{!data.linkedObjectTypes.length && <p className="flow-ontology__muted">No relationships defined.</p>}</section>
        </>}
      </div>
      <footer className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" disabled={!data || preview.loading} onClick={openWorkspace}>Start exploring <ArrowRightOutlined /></button></footer>
    </aside>}
    <Modal className="flow-surface-modal" title="Choose a schema" open={createOpen} onCancel={() => setCreateOpen(false)} okText="Continue" okButtonProps={{ disabled: !createSchema }} onOk={() => { if (createSchema) { setCreateOpen(false); navigate(`/flow/layers?${new URLSearchParams({ schema: createSchema, create: 'table' })}`); } }}><p>Choose where to create the new object type. The existing data table editor opens next.</p><Select aria-label="New object type schema" popupClassName="flow-select-popup" placeholder="Select a schema" value={createSchema} onChange={setCreateSchema} options={schemaNames.map((schema) => ({ value: schema, label: schema }))} style={{ width: '100%' }} /></Modal>
  </div>;
}

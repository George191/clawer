import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ApartmentOutlined, AppstoreOutlined, ArrowRightOutlined, BarsOutlined,
  CloseOutlined, DatabaseOutlined, FolderOutlined, InfoCircleOutlined, ReloadOutlined,
  SearchOutlined, StarFilled, StarOutlined, TableOutlined,
} from '@/components/ui/icons';
import { fetchLayers, fetchLayerTables } from '../../services/api';
import type { LayerNode, LayerTable } from '../../services/types';
import { layerColors } from '../../theme/tokens';
import './ontology-explorer.css';

type Group = 'all' | 'favorites' | 'ingestion' | 'processing' | 'serving' | 'other';
const groups: Array<{ key: Group; label: string; description: string }> = [
  { key: 'all', label: 'All assets', description: 'Browse the schemas available in your databases.' },
  { key: 'favorites', label: 'Favorites', description: 'Quick access to the schemas you follow.' },
  { key: 'ingestion', label: 'Ingestion', description: 'Raw ingestion and standardized schemas.' },
  { key: 'processing', label: 'Processing', description: 'Task, detail, aggregate, and dimension schemas.' },
  { key: 'serving', label: 'Serving', description: 'Schemas published for downstream applications.' },
  { key: 'other', label: 'Other schemas', description: 'Additional schemas discovered in the catalog.' },
];
const favoriteStorageKey = 'flow-favorite-schemas';
const groupFor = (key: string): Group => {
  if (['ts_rds', 'ts_ods'].includes(key)) return 'ingestion';
  if (['ts_task', 'ts_dwd', 'ts_dws', 'ts_dim'].includes(key)) return 'processing';
  return key === 'ts_ads' ? 'serving' : 'other';
};

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

const schemaTitle = (schema: LayerNode) => schema.alias?.trim() || schema.label || schema.key;
const schemaStatus = (schema: LayerNode) => schema.status === 'running' ? 'Available' : schema.status === 'error' ? 'Connection error' : 'Not found';
const tableCount = (count?: number) => count === undefined ? '—' : count.toLocaleString('en-US');
const accentStyle = (key: string) => ({ '--asset-color': schemaColor(key) } as React.CSSProperties);

export default function OntologyExplorer({ refreshToken = 0 }: { refreshToken?: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const keyword = params.get('q') || '';
  const group: Group = groups.some((item) => item.key === params.get('group')) ? params.get('group') as Group : 'all';
  const view = params.get('view') === 'list' ? 'list' : 'grid';
  const selectedKey = params.get('schema');
  const tab = params.get('tab') === 'tables' ? 'tables' : 'overview';
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [mobilePreview, setMobilePreview] = useState(() => window.matchMedia('(max-width: 1150px)').matches);
  const [schemas, setSchemas] = useState<LayerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(favoriteStorageKey) || '[]');
      return Array.isArray(saved) ? saved.filter((key): key is string => typeof key === 'string' && key !== 'ts_meta') : [];
    } catch { return []; }
  });
  const [tableState, setTableState] = useState<{ schema: string; rows: LayerTable[]; loading: boolean; error: string }>({ schema: '', rows: [], loading: false, error: '' });
  const [tableVersion, setTableVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    fetchLayers().then((layers) => {
      if (!active) return;
      const next = layers.map((layer) => ({ ...layer, key: layer.schemaName || (layer.key.startsWith('ts_') ? layer.key : `ts_${layer.key}`) }))
        .filter((layer) => layer.key !== 'ts_meta');
      setSchemas(next);
    }).catch(() => { if (active) setError('Unable to load the catalog. Refresh to try again.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [version, refreshToken]);

  useEffect(() => {
    try { localStorage.setItem(favoriteStorageKey, JSON.stringify(favorites)); } catch { /* Browsing remains available when storage is unavailable. */ }
  }, [favorites]);

  const selected = schemas.find((item) => item.key === selectedKey);
  useEffect(() => {
    if (!selected) return;
    const key = selected.key;
    let active = true;
    setTableState({ schema: key, rows: [], loading: true, error: '' });
    fetchLayerTables(key).then((rows) => {
      if (active) setTableState({ schema: key, rows, loading: false, error: '' });
    }).catch(() => {
      if (active) setTableState({ schema: key, rows: [], loading: false, error: 'Unable to load tables. Try again.' });
    });
    return () => { active = false; };
  }, [selected?.key, tableVersion, version, refreshToken]);

  const updateQuery = (changes: Record<string, string | null>, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    return next;
  }, { replace });
  const setKeyword = (value: string) => updateQuery({ q: value || null }, true);
  const setGroup = (value: Group) => updateQuery({ group: value === 'all' ? null : value });
  const setView = (value: 'grid' | 'list') => updateQuery({ view: value === 'grid' ? null : value });
  const setTab = (value: 'overview' | 'tables') => updateQuery({ tab: value === 'overview' ? null : value });
  const closeInspector = () => {
    updateQuery({ schema: null, tab: null }, true);
    requestAnimationFrame(() => (selectedKey && cardRefs.current[selectedKey] || searchRef.current)?.focus());
  };
  useEffect(() => {
    if (!loading && !error && selectedKey && !selected) {
      setParams((current) => { const next = new URLSearchParams(current); next.delete('schema'); next.delete('tab'); return next; }, { replace: true });
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [loading, error, selectedKey, selected, setParams]);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1150px)');
    const resize = () => setMobilePreview(query.matches);
    query.addEventListener('change', resize);
    return () => query.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); searchRef.current?.focus(); searchRef.current?.select();
      } else if (event.key === 'Escape' && selected) {
        event.preventDefault(); closeInspector();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [selected, selectedKey, params]);
  useEffect(() => {
    if (!selected) return;
    inspectorRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    if (!mobilePreview) return;
    const background = [rootRef.current?.querySelector<HTMLElement>('.flow-ontology__hero'), rootRef.current?.querySelector<HTMLElement>('.flow-ontology__sidebar'), rootRef.current?.querySelector<HTMLElement>('.flow-ontology__main'), document.querySelector<HTMLElement>('.flow-domain-nav'), document.querySelector<HTMLElement>('.ui-layout-header'), document.querySelector<HTMLElement>('[data-testid="project-sidebar"]')].filter((element): element is HTMLElement => !!element);
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
  }, [selected?.key, mobilePreview]);
  const groupCounts = useMemo(() => {
    const counts: Record<Group, number> = { all: schemas.length, favorites: 0, ingestion: 0, processing: 0, serving: 0, other: 0 };
    schemas.forEach((schema) => {
      counts[groupFor(schema.key)]++;
      if (favorites.includes(schema.key)) counts.favorites++;
    });
    return counts;
  }, [schemas, favorites]);
  const visible = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    return schemas.filter((schema) => (group === 'all' || (group === 'favorites' ? favorites.includes(schema.key) : groupFor(schema.key) === group))
      && `${schema.key} ${schema.label} ${schema.alias || ''} ${schema.database || ''}`.toLowerCase().includes(query));
  }, [schemas, keyword, group, favorites]);
  const tablesTotal = schemas.every((schema) => schema.tables !== undefined) ? schemas.reduce((sum, schema) => sum + (schema.tables || 0), 0) : undefined;
  const activeGroup = groups.find((item) => item.key === group)!;
  const sections = useMemo(() => view === 'list' || group !== 'all' || keyword.trim()
    ? [{ key: group, label: activeGroup.label, schemas: visible }]
    : groups.filter((item) => item.key !== 'all' && item.key !== 'favorites').map((item) => ({ key: item.key, label: item.label, schemas: visible.filter((schema) => groupFor(schema.key) === item.key) })).filter((section) => section.schemas.length), [view, group, keyword, activeGroup, visible]);
  const renderedSchemas = sections.flatMap((section) => section.schemas);
  const openWorkspace = (schema?: string, table?: string) => {
    const params = new URLSearchParams({ resource: 'dw_etl' });
    if (schema) params.set('schema', schema);
    if (table) params.set('table', table);
    navigate(`/flow/layers?${params}`);
  };
  const selectSchema = (key: string) => updateQuery({ schema: key, tab: null });
  const moveCardFocus = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const current = event.currentTarget.getBoundingClientRect();
      const candidates = renderedSchemas.map((schema) => cardRefs.current[schema.key]).filter((button): button is HTMLButtonElement => !!button).map((button) => ({ button, rect: button.getBoundingClientRect() }))
        .filter(({ rect }) => event.key === 'ArrowDown' ? rect.top > current.top + 1 : rect.top < current.top - 1)
        .sort((a, b) => Math.abs(a.rect.top - current.top) - Math.abs(b.rect.top - current.top) || Math.abs(a.rect.left - current.left) - Math.abs(b.rect.left - current.left));
      candidates[0]?.button.focus();
      return;
    }
    const offset = event.key === 'ArrowLeft' ? -1 : 1;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? renderedSchemas.length - 1 : Math.max(0, Math.min(renderedSchemas.length - 1, index + offset));
    cardRefs.current[renderedSchemas[next]?.key]?.focus();
  };
  const toggleFavorite = (key: string) => setFavorites((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);

  const renderTables = (limit?: number) => !selected ? null : tableState.schema !== selected.key || tableState.loading ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>Loading tables</span></div> : tableState.error ? <div className="flow-ontology__notice" role="alert"><span>{tableState.error}</span><button type="button" onClick={() => setTableVersion((current) => current + 1)}>Retry</button></div> : !tableState.rows.length ? <div className="flow-ontology__empty is-compact"><TableOutlined /><strong>No tables in this schema</strong></div> : <div className="flow-ontology__table-list">{(limit ? tableState.rows.slice(0, limit) : tableState.rows).map((table) => <button type="button" key={table.name} data-testid="ontology-table-row" onClick={() => openWorkspace(selected.key, table.name)}><TableOutlined /><span><strong>{table.name}</strong><small>{table.dataType || (table.tableRole === 'history' ? 'History table' : 'Tables')}{table.size ? ` · ${table.size}` : ''}</small></span><ArrowRightOutlined /></button>)}</div>;

  return <div ref={rootRef} className="flow-ontology" data-testid="ontology-explorer">
    <div className="flow-ontology__workspace">
      <header className="flow-ontology__hero">
        <div className="flow-ontology__intro"><h1>Explore your data</h1><p>Select a schema from the catalog to <button type="button" onClick={() => openWorkspace()}>explore tables</button> or <button type="button" onClick={() => setView('list')}>view all schemas</button>.</p></div>
        <div className="flow-ontology__topbar">
          <div className="flow-ontology__search" role="search">
            <select className={group === 'all' ? undefined : 'is-filtered'} aria-label="Filter asset group" value={group} onChange={(event) => setGroup(event.target.value as Group)}>{groups.map((item) => <option key={item.key} value={item.key}>{item.key === 'all' ? 'All' : item.label}</option>)}</select>
            <SearchOutlined /><input ref={searchRef} data-testid="ontology-search" aria-label="Search data assets" value={keyword} onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => { if (event.key === 'ArrowDown' && renderedSchemas.length) { event.preventDefault(); cardRefs.current[renderedSchemas[0].key]?.focus(); } }} placeholder="Search schemas, names, and databases..." /><kbd className="flow-ontology__search-shortcut">Ctrl K</kbd>{keyword && <button type="button" aria-label="Clear search" onClick={() => { setKeyword(''); searchRef.current?.focus(); }}><CloseOutlined /></button>}
            <button type="button" className="flow-ontology__search-refresh" aria-label="Refresh catalog" title="Refresh catalog" disabled={loading} onClick={() => setVersion((current) => current + 1)}><ReloadOutlined spin={loading} /></button>
          </div>
        </div>
        <nav className="flow-ontology__catalog-tabs" aria-label="Catalog view"><button type="button" aria-label="Catalog overview" aria-current={view === 'grid' ? 'page' : undefined} className={view === 'grid' ? 'is-active' : ''} onClick={() => setView('grid')}>Overview</button><button type="button" aria-label="All schemas view" aria-current={view === 'list' ? 'page' : undefined} className={view === 'list' ? 'is-active' : ''} onClick={() => setView('list')}>All schemas</button><span className="flow-ontology__summary" aria-label="Catalog summary">{schemas.length} schemas <i /> {tableCount(tablesTotal)} tables</span></nav>
      </header>
      <div className="flow-ontology__body">
        <aside className="flow-ontology__sidebar" aria-label="Asset groups">
          <nav>{groups.map((item, index) => <React.Fragment key={item.key}>{index === 2 && <div className="flow-ontology__sidebar-title">Schema groups</div>}<button type="button" data-group={item.key} className={group === item.key ? 'is-active' : ''} aria-pressed={group === item.key} onClick={() => setGroup(item.key)}><span>{item.key === 'all' ? 'All' : item.label}</span><small>{groupCounts[item.key]}</small></button></React.Fragment>)}</nav>
          <button type="button" className="flow-ontology__sidebar-link" onClick={() => navigate('/flow/lineage')}><ApartmentOutlined /><span>Lineage &amp; impact</span><ArrowRightOutlined /></button>
        </aside>
        <main className="flow-ontology__main" aria-busy={loading}>
          {error && <div className="flow-ontology__notice" role="alert"><span>{error}{schemas.length > 0 ? ' Showing the last available catalog.' : ''}</span><button type="button" onClick={() => setVersion((current) => current + 1)}>Retry</button></div>}
          {view === 'grid' && group === 'all' && !keyword && <section className="flow-ontology__favorites-section" aria-label="Favorite schemas"><h2>Favorites <small>{groupCounts.favorites}</small></h2><button type="button" className="flow-ontology__favorites-entry" onClick={() => setGroup('favorites')}><span className="flow-ontology__collection-icon"><StarFilled /></span><span><strong>Favorite schemas</strong><small>{groupCounts.favorites ? `${groupCounts.favorites} starred ${groupCounts.favorites === 1 ? 'schema' : 'schemas'}` : 'Star schemas to keep them close at hand'}</small></span><ArrowRightOutlined /></button><div className="flow-ontology__section-divider"><span>Schemas</span></div></section>}
          <div className="flow-ontology__collection-toolbar"><span>{keyword ? `Results for “${keyword.trim()}”` : activeGroup.description}</span><div className="flow-ontology__view-switch" aria-label="Asset view"><button type="button" aria-label="Grid view" aria-pressed={view === 'grid'} className={view === 'grid' ? 'is-active' : ''} onClick={() => setView('grid')}><AppstoreOutlined /><span>Grid</span></button><button type="button" aria-label="List view" aria-pressed={view === 'list'} className={view === 'list' ? 'is-active' : ''} onClick={() => setView('list')}><BarsOutlined /><span>List</span></button></div></div>
          {loading && !schemas.length ? <div className="flow-ontology__empty" role="status"><ReloadOutlined spin /><strong>Loading catalog</strong><span>Loading the schemas available to you.</span></div> : !visible.length ? <div className="flow-ontology__empty"><FolderOutlined /><strong>{!schemas.length && !error ? 'No assets discovered yet' : 'No matching assets'}</strong><span>{group === 'favorites' && !keyword ? 'Star a schema to add it to your favorites.' : 'Try another search or browse all assets.'}</span>{(keyword || group !== 'all') && <button type="button" className="flow-ontology__button" onClick={() => updateQuery({ q: null, group: null })}>Browse all assets</button>}</div> : <div className={`flow-ontology__collections is-${view}`} data-testid="ontology-assets">
            {sections.map((section) => <section className="flow-ontology__collection" key={section.key}><div className="flow-ontology__collection-heading"><h2>{section.label}<small>{section.schemas.length}</small></h2></div><div className={`flow-ontology__assets is-${view}`}>
              {section.schemas.map((schema) => <article key={schema.key} data-testid="ontology-schema-card" data-schema={schema.key} className={`flow-ontology__asset${selectedKey === schema.key ? ' is-selected' : ''}`} style={accentStyle(schema.key)}>
                <button ref={(element) => { cardRefs.current[schema.key] = element; }} type="button" className="flow-ontology__asset-main" aria-label={`Preview ${schema.key}`} aria-pressed={selectedKey === schema.key} onKeyDown={(event) => moveCardFocus(event, renderedSchemas.findIndex((item) => item.key === schema.key))} onClick={() => selectSchema(schema.key)}>
                  <span className="flow-ontology__asset-icon"><DatabaseOutlined /></span><span className="flow-ontology__asset-name"><span><strong title={schemaTitle(schema)}>{schemaTitle(schema)}</strong><small className="flow-ontology__table-count" title={`${tableCount(schema.tables)} tables`}>{tableCount(schema.tables)}</small></span><code>{schema.key}{schema.database ? ` · ${schema.database}` : ''}</code></span>
                  <span className={`flow-ontology__status is-${schema.status}`} title={schemaStatus(schema)} aria-label={schemaStatus(schema)}><i /></span><span className="flow-ontology__preview-action" aria-hidden="true"><InfoCircleOutlined /></span>
                  {schema.metricsAvailable && schema.rateOut > 0 && <span className="flow-ontology__stream" aria-hidden="true"><i /><b>›</b></span>}
                </button>
                <button type="button" className={`flow-ontology__favorite${favorites.includes(schema.key) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(schema.key) ? 'Unstar' : 'Star'} ${schema.key}`} aria-pressed={favorites.includes(schema.key)} onClick={() => toggleFavorite(schema.key)}>{favorites.includes(schema.key) ? <StarFilled /> : <StarOutlined />}</button>
              </article>)}
            </div></section>)}
          </div>}
          <footer className="flow-ontology__catalog-footer"><span>POSTGRESQL CATALOG</span><span>Schema availability and task runtime are reported separately.</span></footer>
        </main>
      </div>
    </div>

      {selected && mobilePreview && <button type="button" className="flow-ontology__preview-backdrop" aria-label="Dismiss asset preview" tabIndex={-1} onClick={closeInspector} />}
      {selected && <aside ref={inspectorRef} className="flow-ontology__inspector" data-testid="ontology-inspector" data-schema={selected.key} style={accentStyle(selected.key)} role={mobilePreview ? 'dialog' : undefined} aria-modal={mobilePreview ? true : undefined} aria-label={`${selected.key} Asset details`}>
        <div className="flow-ontology__inspector-toolbar"><span className="flow-ontology__asset-icon"><DatabaseOutlined /></span><div className="flow-ontology__preview-identity"><h2>{schemaTitle(selected)}</h2><code>{selected.key}</code></div><button type="button" className="flow-ontology__icon-button" aria-label="Close asset preview" onClick={closeInspector}><CloseOutlined /></button></div>
        <div className="flow-ontology__inspector-heading"><div><span className={`flow-ontology__status is-${selected.status}`}><i />{schemaStatus(selected)}</span><button type="button" className={`flow-ontology__favorite${favorites.includes(selected.key) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(selected.key) ? 'Unstar' : 'Star'} ${selected.key}`} aria-pressed={favorites.includes(selected.key)} onClick={() => toggleFavorite(selected.key)}>{favorites.includes(selected.key) ? <StarFilled /> : <StarOutlined />}</button></div></div>
        <div className="flow-ontology__inspector-tabs" role="tablist" aria-label="Asset details" onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 'overview' : event.key === 'End' ? 'tables' : tab === 'overview' ? 'tables' : 'overview';
          setTab(next);
          requestAnimationFrame(() => inspectorRef.current?.querySelector<HTMLButtonElement>(`#ontology-${next}-tab`)?.focus());
        }}><button type="button" role="tab" tabIndex={tab === 'overview' ? 0 : -1} id="ontology-overview-tab" aria-controls="ontology-overview-panel" aria-selected={tab === 'overview'} className={tab === 'overview' ? 'is-active' : ''} onClick={() => setTab('overview')}>Overview</button><button type="button" role="tab" tabIndex={tab === 'tables' ? 0 : -1} id="ontology-tables-tab" aria-controls="ontology-tables-panel" aria-selected={tab === 'tables'} className={tab === 'tables' ? 'is-active' : ''} onClick={() => setTab('tables')}>Tables <small>{tableCount(selected.tables)}</small></button></div>
        <div className="flow-ontology__inspector-content" role="tabpanel" id={tab === 'overview' ? 'ontology-overview-panel' : 'ontology-tables-panel'} aria-labelledby={tab === 'overview' ? 'ontology-overview-tab' : 'ontology-tables-tab'}>
          {tab === 'overview' ? <>
            <section className="flow-ontology__preview-section"><h3>Description</h3><p className="flow-ontology__muted">{selected.alias?.trim() || 'No description provided.'}</p></section>
            <section className="flow-ontology__preview-section"><h3>Resource details</h3><dl><div><dt>Schema</dt><dd>{selected.key}</dd></div><div><dt>Database</dt><dd>{selected.database || '—'}</dd></div><div><dt>Asset group</dt><dd>{groups.find((item) => item.key === groupFor(selected.key))!.label}</dd></div></dl></section>
            <section className="flow-ontology__preview-section"><header><h3>Tables <small>({tableCount(selected.tables)})</small></h3><button type="button" onClick={() => setTab('tables')}>View <ArrowRightOutlined /></button></header>{renderTables(6)}</section>
            <section className="flow-ontology__preview-section"><h3>Throughput</h3>{selected.metricsAvailable ? <div className="flow-ontology__metrics"><div><span>IN</span><strong>{selected.rateIn.toLocaleString('en-US')}<small>/s</small></strong></div><ArrowRightOutlined /><div><span>OUT</span><strong>{selected.rateOut.toLocaleString('en-US')}<small>/s</small></strong></div></div> : <p className="flow-ontology__muted">Runtime metrics are not available.</p>}</section>
            <section className="flow-ontology__preview-section"><h3>Relationships</h3><p className="flow-ontology__muted">Discovered relationships are not available from the current catalog API.</p><button type="button" className="flow-ontology__text-button" onClick={() => navigate(`/flow/lineage?schema=${encodeURIComponent(selected.key)}`)}>Open contract preview <ArrowRightOutlined /></button></section>
          </> : renderTables()}
        </div>
        <div className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" onClick={() => openWorkspace(selected.key)}>Open workspace <ArrowRightOutlined /></button></div>
      </aside>}
  </div>;
}

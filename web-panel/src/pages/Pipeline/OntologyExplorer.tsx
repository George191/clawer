import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ApartmentOutlined, AppstoreOutlined, ArrowRightOutlined, BarsOutlined,
  CloseOutlined, DatabaseOutlined, FolderOutlined, ReloadOutlined,
  SearchOutlined, StarFilled, StarOutlined, TableOutlined,
} from '@ant-design/icons';
import { fetchLayers, fetchLayerTables } from '../../services/api';
import type { LayerNode, LayerTable } from '../../services/types';
import { layerColors } from '../../theme/tokens';
import './ontology-explorer.css';

type Group = 'all' | 'favorites' | 'ingestion' | 'processing' | 'serving' | 'other';
const groups: Array<{ key: Group; label: string; description: string }> = [
  { key: 'all', label: '全部资产', description: '浏览当前数据库中的数据架构' },
  { key: 'favorites', label: '我的收藏', description: '快速访问你关注的数据架构' },
  { key: 'ingestion', label: '采集与标准化', description: '原始数据接入与标准化数据架构' },
  { key: 'processing', label: '加工与建模', description: '任务、明细、汇总及维度数据架构' },
  { key: 'serving', label: '发布与应用', description: '面向应用的数据架构' },
  { key: 'other', label: '其他架构', description: '自动发现的其他数据架构' },
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
const schemaStatus = (schema: LayerNode) => schema.status === 'running' ? '可访问' : schema.status === 'error' ? '连接异常' : '未发现';
const tableCount = (count?: number) => count === undefined ? '—' : count.toLocaleString();
const accentStyle = (key: string) => ({ '--asset-color': schemaColor(key) } as React.CSSProperties);

export default function OntologyExplorer() {
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
  const [mobilePreview, setMobilePreview] = useState(() => window.matchMedia('(max-width: 900px)').matches);
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
    }).catch(() => { if (active) setError('数据目录读取失败，请刷新重试。'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [version]);

  useEffect(() => {
    try { localStorage.setItem(favoriteStorageKey, JSON.stringify(favorites)); } catch { /* Browsing remains available when storage is unavailable. */ }
  }, [favorites]);

  const selected = schemas.find((item) => item.key === selectedKey);
  useEffect(() => {
    if (!selected || tab !== 'tables') return;
    const key = selected.key;
    let active = true;
    setTableState({ schema: key, rows: [], loading: true, error: '' });
    fetchLayerTables(key).then((rows) => {
      if (active) setTableState({ schema: key, rows, loading: false, error: '' });
    }).catch(() => {
      if (active) setTableState({ schema: key, rows: [], loading: false, error: '数据表读取失败，请重试。' });
    });
    return () => { active = false; };
  }, [selected?.key, tab, tableVersion, version]);

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
    const query = window.matchMedia('(max-width: 900px)');
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
    const background = [rootRef.current?.querySelector<HTMLElement>('.flow-ontology__topbar'), rootRef.current?.querySelector<HTMLElement>('.flow-ontology__sidebar'), rootRef.current?.querySelector<HTMLElement>('.flow-ontology__main'), document.querySelector<HTMLElement>('.flow-domain-nav'), document.querySelector<HTMLElement>('.ant-layout-header')].filter((element): element is HTMLElement => !!element);
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
    const container = event.currentTarget.closest('.flow-ontology__assets');
    const columns = view === 'list' || !container ? 1 : getComputedStyle(container).gridTemplateColumns.split(' ').length;
    const offset = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 : Math.max(0, Math.min(visible.length - 1, index + offset));
    cardRefs.current[visible[next]?.key]?.focus();
  };
  const toggleFavorite = (key: string) => setFavorites((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);

  return <div ref={rootRef} className="flow-ontology" data-testid="ontology-explorer">
    <header className="flow-ontology__topbar">
      <div className="flow-ontology__breadcrumb"><span className="flow-ontology__mark"><ApartmentOutlined /></span><span>Flow</span><span className="flow-ontology__slash">/</span><strong>Discover</strong></div>
      <label className="flow-ontology__search"><SearchOutlined /><input ref={searchRef} data-testid="ontology-search" aria-label="搜索数据资产" value={keyword} onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => { if (event.key === 'ArrowDown' && visible.length) { event.preventDefault(); cardRefs.current[visible[0].key]?.focus(); } }} placeholder="搜索名称、Schema 或数据库" /><kbd className="flow-ontology__search-shortcut">Ctrl K</kbd>{keyword && <button type="button" aria-label="清空搜索" onClick={() => { setKeyword(''); searchRef.current?.focus(); }}><CloseOutlined /></button>}</label>
      <div className="flow-ontology__top-actions"><button type="button" className="flow-ontology__icon-button" aria-label="刷新数据目录" title="刷新数据目录" disabled={loading} onClick={() => setVersion((current) => current + 1)}><ReloadOutlined spin={loading} /></button><button type="button" className="flow-ontology__button flow-ontology__button--primary" onClick={() => openWorkspace()}><span>打开工作区</span><ArrowRightOutlined /></button></div>
    </header>

    <div className="flow-ontology__body">
      <aside className="flow-ontology__sidebar" aria-label="资产分组">
        <div className="flow-ontology__sidebar-title">探索 <span>EXPLORE</span></div>
        <nav>{groups.map((item) => <button type="button" key={item.key} data-group={item.key} className={group === item.key ? 'is-active' : ''} aria-pressed={group === item.key} onClick={() => setGroup(item.key)}>{item.key === 'favorites' ? <StarOutlined /> : item.key === 'all' ? <AppstoreOutlined /> : <FolderOutlined />}<span>{item.label}</span><small>{groupCounts[item.key]}</small></button>)}</nav>
        <div className="flow-ontology__sidebar-note"><DatabaseOutlined /><span>数据目录<small>{new Set(schemas.map((schema) => schema.database).filter(Boolean)).size} 个数据库 · {schemas.length} 个架构</small></span></div>
        <button type="button" className="flow-ontology__sidebar-link" onClick={() => navigate('/flow/lineage')}><ApartmentOutlined /><span>数据关系</span><ArrowRightOutlined /></button>
      </aside>

      <main className="flow-ontology__main" aria-busy={loading}>
        <div className="flow-ontology__intro"><span className="flow-ontology__eyebrow">DATA DISCOVERY</span><h1>发现数据资产</h1><p>从数据架构开始，探索数据表与连接信息。</p></div>
        <div className="flow-ontology__summary" aria-label="目录概况"><span><strong>{schemas.length}</strong> 数据架构</span><span><strong>{tableCount(tablesTotal)}</strong> 数据表</span><span><i />{schemas.filter((schema) => schema.status === 'running').length} 个架构可访问</span></div>
        {error && <div className="flow-ontology__notice" role="alert"><span>{error}{schemas.length > 0 ? ' 当前展示上次读取的目录。' : ''}</span><button type="button" onClick={() => setVersion((current) => current + 1)}>重试</button></div>}
        <div className="flow-ontology__collection-heading"><div><h2>{activeGroup.label}<small>{visible.length}</small></h2><p>{keyword ? `搜索 “${keyword.trim()}”` : activeGroup.description}</p></div><div className="flow-ontology__view-switch" aria-label="资产展示方式"><button type="button" aria-label="网格视图" aria-pressed={view === 'grid'} className={view === 'grid' ? 'is-active' : ''} onClick={() => setView('grid')}><AppstoreOutlined /></button><button type="button" aria-label="列表视图" aria-pressed={view === 'list'} className={view === 'list' ? 'is-active' : ''} onClick={() => setView('list')}><BarsOutlined /></button></div></div>
        {loading && !schemas.length ? <div className="flow-ontology__empty" role="status"><ReloadOutlined spin /><strong>正在读取数据目录</strong><span>获取当前可见的数据架构。</span></div> : !visible.length ? <div className="flow-ontology__empty"><FolderOutlined /><strong>{!schemas.length && !error ? '尚未发现数据资产' : '没有匹配的资产'}</strong><span>{group === 'favorites' && !keyword ? '点击资产卡片上的星标，将常用架构加入收藏。' : '尝试其他关键词或浏览全部资产。'}</span>{(keyword || group !== 'all') && <button type="button" className="flow-ontology__button" onClick={() => updateQuery({ q: null, group: null })}>浏览全部资产</button>}</div> : <div className={`flow-ontology__assets is-${view}`} data-testid="ontology-assets">
          {visible.map((schema, index) => <article key={schema.key} data-testid="ontology-schema-card" data-schema={schema.key} className={`flow-ontology__asset${selectedKey === schema.key ? ' is-selected' : ''}`} style={accentStyle(schema.key)}>
            <button ref={(element) => { cardRefs.current[schema.key] = element; }} type="button" className="flow-ontology__asset-main" aria-label={`查看 ${schema.key}`} aria-pressed={selectedKey === schema.key} onKeyDown={(event) => moveCardFocus(event, index)} onClick={() => selectSchema(schema.key)}>
              <span className="flow-ontology__asset-icon"><DatabaseOutlined /></span><span className="flow-ontology__asset-name"><strong title={schemaTitle(schema)}>{schemaTitle(schema)}</strong><code>{schema.key}</code></span>
              <span className="flow-ontology__asset-description">{schema.database || '数据库信息未提供'}</span>
              <span className="flow-ontology__asset-meta"><span><TableOutlined />{tableCount(schema.tables)} 张表</span><span className={`flow-ontology__status is-${schema.status}`}><i />{schemaStatus(schema)}</span></span>
              {schema.metricsAvailable && <span className="flow-ontology__asset-rates">IN {schema.rateIn.toLocaleString()}/s <span>→</span> OUT {schema.rateOut.toLocaleString()}/s</span>}
              {schema.metricsAvailable && schema.rateOut > 0 && <span className="flow-ontology__stream" aria-hidden="true"><i /><b>›</b></span>}
            </button>
            <button type="button" className={`flow-ontology__favorite${favorites.includes(schema.key) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(schema.key) ? '取消收藏' : '收藏'} ${schema.key}`} aria-pressed={favorites.includes(schema.key)} onClick={() => toggleFavorite(schema.key)}>{favorites.includes(schema.key) ? <StarFilled /> : <StarOutlined />}</button>
          </article>)}
        </div>}
        <footer className="flow-ontology__catalog-footer"><span>POSTGRESQL CATALOG</span><span>架构可访问状态与任务运行状态分别查看</span></footer>
      </main>

      {selected && mobilePreview && <button type="button" className="flow-ontology__preview-backdrop" aria-label="关闭资产预览遮罩" tabIndex={-1} onClick={closeInspector} />}
      {selected && <aside ref={inspectorRef} className="flow-ontology__inspector" data-testid="ontology-inspector" data-schema={selected.key} style={accentStyle(selected.key)} role={mobilePreview ? 'dialog' : undefined} aria-modal={mobilePreview ? true : undefined} aria-label={`${selected.key} 资产详情`}>
        <div className="flow-ontology__inspector-toolbar"><span>资产预览</span><button type="button" className="flow-ontology__icon-button" aria-label="关闭资产预览" onClick={closeInspector}><CloseOutlined /></button></div>
        <div className="flow-ontology__inspector-heading"><span className="flow-ontology__asset-icon"><DatabaseOutlined /></span><span className="flow-ontology__resource-type">DATA SCHEMA</span><h2>{schemaTitle(selected)}</h2><code>{selected.key}</code><div><span className={`flow-ontology__status is-${selected.status}`}><i />{schemaStatus(selected)}</span><button type="button" className={`flow-ontology__favorite${favorites.includes(selected.key) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(selected.key) ? '取消收藏' : '收藏'} ${selected.key}`} aria-pressed={favorites.includes(selected.key)} onClick={() => toggleFavorite(selected.key)}>{favorites.includes(selected.key) ? <StarFilled /> : <StarOutlined />}</button></div></div>
        <div className="flow-ontology__inspector-tabs" role="tablist" aria-label="资产详情" onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 'overview' : event.key === 'End' ? 'tables' : tab === 'overview' ? 'tables' : 'overview';
          setTab(next);
          requestAnimationFrame(() => inspectorRef.current?.querySelector<HTMLButtonElement>(`#ontology-${next}-tab`)?.focus());
        }}><button type="button" role="tab" tabIndex={tab === 'overview' ? 0 : -1} id="ontology-overview-tab" aria-controls="ontology-overview-panel" aria-selected={tab === 'overview'} className={tab === 'overview' ? 'is-active' : ''} onClick={() => setTab('overview')}>概览</button><button type="button" role="tab" tabIndex={tab === 'tables' ? 0 : -1} id="ontology-tables-tab" aria-controls="ontology-tables-panel" aria-selected={tab === 'tables'} className={tab === 'tables' ? 'is-active' : ''} onClick={() => setTab('tables')}>数据表 <small>{tableCount(selected.tables)}</small></button></div>
        <div className="flow-ontology__inspector-content" role="tabpanel" id={tab === 'overview' ? 'ontology-overview-panel' : 'ontology-tables-panel'} aria-labelledby={tab === 'overview' ? 'ontology-overview-tab' : 'ontology-tables-tab'}>
          {tab === 'overview' ? <><h3>基本信息</h3><dl><div><dt>Schema</dt><dd>{selected.key}</dd></div><div><dt>数据库</dt><dd>{selected.database || '—'}</dd></div><div><dt>显示名称</dt><dd>{selected.alias || selected.label || '—'}</dd></div><div><dt>资产分组</dt><dd>{groups.find((item) => item.key === groupFor(selected.key))!.label}</dd></div><div><dt>数据表</dt><dd>{tableCount(selected.tables)}</dd></div></dl><h3>数据流量</h3>{selected.metricsAvailable ? <div className="flow-ontology__metrics"><div><span>IN</span><strong>{selected.rateIn.toLocaleString()}<small>/s</small></strong></div><ArrowRightOutlined /><div><span>OUT</span><strong>{selected.rateOut.toLocaleString()}<small>/s</small></strong></div></div> : <p className="flow-ontology__muted">当前未提供流量指标。</p>}<h3>数据关系</h3><p className="flow-ontology__muted">在关系视图中查看已确认的数据处理链路。</p><button type="button" className="flow-ontology__text-button" onClick={() => navigate(`/flow/lineage?schema=${encodeURIComponent(selected.key)}`)}>打开数据关系 <ArrowRightOutlined /></button></> : tableState.schema !== selected.key || tableState.loading ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>正在读取数据表</span></div> : tableState.error ? <div className="flow-ontology__notice" role="alert"><span>{tableState.error}</span><button type="button" onClick={() => setTableVersion((current) => current + 1)}>重试</button></div> : !tableState.rows.length ? <div className="flow-ontology__empty is-compact"><TableOutlined /><strong>此架构暂无数据表</strong></div> : <div className="flow-ontology__table-list">{tableState.rows.map((table) => <button type="button" key={table.name} data-testid="ontology-table-row" onClick={() => openWorkspace(selected.key, table.name)}><TableOutlined /><span><strong>{table.name}</strong><small>{table.dataType || (table.tableRole === 'history' ? '历史数据' : '数据表')}{table.size ? ` · ${table.size}` : ''}</small></span><ArrowRightOutlined /></button>)}</div>}
        </div>
        <div className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" onClick={() => openWorkspace(selected.key)}>探索数据表 <ArrowRightOutlined /></button></div>
      </aside>}
    </div>
  </div>;
}

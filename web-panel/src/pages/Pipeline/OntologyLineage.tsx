import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowDownOutlined, ArrowLeftOutlined, ArrowRightOutlined, ArrowUpOutlined, BarsOutlined, CheckOutlined, CloseOutlined, CopyOutlined, DatabaseOutlined, ExportOutlined, EyeOutlined, FullscreenOutlined, MinusOutlined, PauseOutlined, PlayFilled, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { Spin } from '@/components/ui';
import { fetchLayers } from '../../services/api';
import type { LayerNode } from '../../services/types';
import { schemaColor } from './OntologyExplorer';
import './ontology-lineage.css';

// Current ETL processor contracts: TsOds, TsTask, TsDim, TsDwd and TsDws.
// A schema's presence alone does not establish a processing relationship.
const relationships = [
  ['ts_rds', 'ts_ods'], ['ts_ods', 'ts_task'], ['ts_ods', 'ts_dim'],
  ['ts_ods', 'ts_dwd'], ['ts_task', 'ts_dwd'], ['ts_dwd', 'ts_dws'],
] as const;
const stages: Record<string, { column: number; y: number }> = {
  ts_rds: { column: 0, y: 200 }, ts_ods: { column: 1, y: 200 },
  ts_task: { column: 2, y: 280 }, ts_dim: { column: 2, y: 350 },
  ts_dwd: { column: 3, y: 200 }, ts_dws: { column: 4, y: 200 },
};

export default function OntologyLineage({ refreshToken = 0 }: { refreshToken?: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const search = params.get('q') || '';
  const selected = params.get('schema') || '';
  const paused = params.get('motion') === 'off';
  const viewportRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<SVGSVGElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const pointer = useRef<{ x: number; y: number; left: number; top: number }>();
  const [manualZoom, setManualZoom] = useState(1);
  const [autoFit, setAutoFit] = useState(true);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [panning, setPanning] = useState(false);
  const [tool, setTool] = useState<'pan' | 'select'>('pan');
  const [legendOpen, setLegendOpen] = useState(() => window.matchMedia('(min-width: 761px)').matches);
  const [inspectorOpen, setInspectorOpen] = useState(Boolean(selected));
  const [panel, setPanel] = useState<'details' | 'upstream' | 'downstream'>('details');
  const [copyStatus, setCopyStatus] = useState<'copied' | 'manual' | ''>('');
  const id = useId().replace(/:/g, '');
  const [schemas, setSchemas] = useState<LayerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [version, setVersion] = useState(0);
  const updateQuery = (changes: Record<string, string | null>, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    return next;
  }, { replace });
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    fetchLayers().then((layers) => {
      if (active) setSchemas(layers.map((layer) => ({ ...layer, key: layer.schemaName || (layer.key.startsWith('ts_') ? layer.key : `ts_${layer.key}`) })).filter((layer) => layer.key !== 'ts_meta'));
    }).catch(() => { if (active) { setError(true); setSchemas([]); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [version, refreshToken]);
  useEffect(() => {
    if (!loading && !error && selected && !schemas.some((schema) => schema.key === selected)) {
      setParams((current) => { const next = new URLSearchParams(current); next.delete('schema'); return next; }, { replace: true });
    }
  }, [schemas, loading, error, selected, setParams]);
  const visible = schemas.filter((schema) => `${schema.key} ${schema.label} ${schema.alias || ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  const independent = visible.filter((schema) => !stages[schema.key]).sort((a, b) => a.key.localeCompare(b.key));
  const hasPipeline = visible.some((schema) => stages[schema.key]);
  const width = Math.max(hasPipeline ? 1120 : 256, viewportSize.width);
  const columns = Math.max(1, Math.floor((width - 40) / 216));
  const independentRows = Math.ceil(independent.length / columns);
  const pipelineHeight = hasPipeline ? Math.max(400, viewportSize.height - (independentRows ? independentRows * 72 + 106 : 70)) : 0;
  const independentTop = hasPipeline ? pipelineHeight + 60 : 100;
  const height = Math.max(viewportSize.height, independentRows ? independentTop + independentRows * 72 + 32 : pipelineHeight);
  const zoom = autoFit && viewportSize.width && viewportSize.height ? Math.max(.25, Math.min(1, viewportSize.width / width, viewportSize.height / height)) : manualZoom;
  // Compare ratios before multiplication: ceil(467 / 1860 * 1860) can be 468.
  const extentWidth = zoom <= viewportSize.width / width ? viewportSize.width : Math.ceil(width * zoom);
  const extentHeight = zoom <= viewportSize.height / height ? viewportSize.height : Math.ceil(height * zoom);
  const insetX = Math.max(0, (viewportSize.width - width * zoom) / 2);
  const insetY = Math.max(0, (viewportSize.height - height * zoom) / 2);
  const stageStep = (width - 256) / 4;
  const independentStep = columns > 1 ? (width - 256) / (columns - 1) : 0;
  const nodes = visible.map((schema) => {
    const stage = stages[schema.key];
    const index = independent.findIndex((item) => item.key === schema.key);
    return { ...schema, x: Math.round(stage ? 40 + stage.column * stageStep : 40 + (index % columns) * independentStep), y: Math.round(stage ? stage.y / 440 * pipelineHeight : independentTop + Math.floor(index / columns) * 72) };
  });
  const links = relationships.flatMap(([source, target]) => {
    const from = nodes.find((node) => node.key === source);
    const to = nodes.find((node) => node.key === target);
    if (!from || !to) return [];
    const startX = from.x + 176;
    const endX = to.x;
    const startY = from.y + 13;
    const endY = to.y + 13;
    const mid = (startX + endX) / 2;
    return [{ source, target, from, color: schemaColor(source), d: `M${startX},${startY} C${mid},${startY} ${mid},${endY} ${endX},${endY}` }];
  });
  const asset = schemas.find((schema) => schema.key === selected);
  const liveLinks = links.filter((link) => link.from.metricsAvailable && link.from.rateOut > 0).length;
  const directionLinks = links.filter((link) => !link.from.metricsAvailable).length;
  const selectedNode = nodes.find((node) => node.key === selected);
  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const measure = (entry?: ResizeObserverEntry) => {
      const width = Math.floor(entry?.contentRect.width ?? viewport.clientWidth);
      const height = Math.floor(entry?.contentRect.height ?? viewport.clientHeight);
      setViewportSize((current) => current.width === width && current.height === height ? current : { width, height });
    };
    measure();
    const observer = new ResizeObserver(([entry]) => measure(entry));
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [loading, error, visible.length]);
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  useEffect(() => { if (selected) setInspectorOpen(true); }, [selected]);
  useEffect(() => setCopyStatus(''), [params]);
  const selectNode = (key: string) => {
    setInspectorOpen(true);
    updateQuery({ schema: key, ...(nodes.some((node) => node.key === key) ? {} : { q: null }) });
  };
  const closeInspector = () => {
    setInspectorOpen(false);
    updateQuery({ schema: null }, true);
    requestAnimationFrame(() => nodeRefs.current[selected]?.focus());
  };
  const changeZoom = (value: number) => {
    setAutoFit(false);
    const next = Math.max(.25, Math.min(1.75, Math.round(value * 100) / 100));
    const viewport = viewportRef.current;
    const left = viewport ? (viewport.scrollLeft + viewport.clientWidth / 2 - insetX) * next / zoom - viewport.clientWidth / 2 + Math.max(0, (viewport.clientWidth - width * next) / 2) : 0;
    const top = viewport ? (viewport.scrollTop + viewport.clientHeight / 2 - insetY) * next / zoom - viewport.clientHeight / 2 + Math.max(0, (viewport.clientHeight - height * next) / 2) : 0;
    setManualZoom(next);
    requestAnimationFrame(() => viewport?.scrollTo({ left: Math.max(0, left), top: Math.max(0, top) }));
  };
  const fitGraph = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    setAutoFit(true);
    requestAnimationFrame(() => viewport.scrollTo({ left: 0, top: 0 }));
  };
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    if (paused || reducedMotion) graph.pauseAnimations(); else graph.unpauseAnimations();
  }, [paused, reducedMotion, loading, links.length]);
  useEffect(() => {
    if (!selectedNode || loading || !viewportRef.current) return;
    const viewport = viewportRef.current;
    viewport.scrollTo({ left: Math.max(0, insetX + (selectedNode.x + 88) * zoom - viewport.clientWidth / 2), top: Math.max(0, insetY + (selectedNode.y + 13) * zoom - viewport.clientHeight / 2) });
  }, [selectedNode?.key, selectedNode?.x, selectedNode?.y, loading]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && selected) { event.preventDefault(); closeInspector(); }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [selected, params]);
  const openPanel = (next: typeof panel) => { setPanel(next); setInspectorOpen(true); };
  const openWorkspace = () => asset && navigate(`/flow/layers?schema=${encodeURIComponent(asset.key)}`);
  const copyLink = async () => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(window.location.href);
      else {
        const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const field = document.createElement('textarea');
        field.value = window.location.href;
        field.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.appendChild(field);
        field.select();
        let copied = false;
        try { copied = document.execCommand('copy'); }
        finally { field.remove(); focused?.focus(); }
        if (!copied) throw new Error('Clipboard unavailable');
      }
      setCopyStatus('copied');
    } catch { setCopyStatus('manual'); }
  };
  const canvasReady = !loading && !error && nodes.length > 0;
  const motionStatus = reducedMotion ? 'Animations are off because reduced motion is enabled.' : paused ? 'Animation paused' : liveLinks ? `${liveLinks} measured schema outputs${directionLinks ? ` · ${directionLinks} direction previews` : ''}` : directionLinks ? `${directionLinks} direction previews · Output metrics unavailable` : 'No measured output · Motion stopped';
  return <div className="flow-lineage" data-testid="ontology-lineage">
    <header className="flow-lineage-context">
      <div className="flow-lineage-context-title"><button type="button" className="flow-lineage-back" aria-label="Back to object types" title="Back to object types" onClick={() => navigate('/flow')}><ArrowLeftOutlined /></button><h1>Data lineage</h1><span>ETL contract preview</span></div>
      <div className="flow-lineage-context-actions"><button type="button" className="flow-lineage-action" aria-label="Refresh lineage" title="Refresh schemas and output metrics" disabled={loading} onClick={() => setVersion(value => value + 1)}><ReloadOutlined spin={loading} /><span>Refresh</span></button><button type="button" className="flow-lineage-action is-primary" onClick={() => void copyLink()}>{copyStatus === 'copied' ? <CheckOutlined /> : <CopyOutlined />}<span>{copyStatus === 'copied' ? 'Link copied' : 'Copy link'}</span></button></div>
    </header>
    {copyStatus === 'manual' && <div className="flow-lineage-copy-fallback"><span>Copy this view link:</span><input aria-label="View link to copy" readOnly value={window.location.href} autoFocus onFocus={event => event.currentTarget.select()} /></div>}
    <div className="flow-lineage-toolbar" role="toolbar" aria-label="Lineage tools">
      <div className="flow-lineage-tool-group"><button type="button" aria-label="Pan canvas" disabled={!canvasReady} title="Drag empty canvas to pan" aria-pressed={tool === 'pan'} onClick={() => setTool('pan')}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 2v20M2 12h20M8 6l4-4 4 4M8 18l4 4 4-4M6 8l-4 4 4 4m12-8 4 4-4 4" /></svg><span>Pan</span></button><button type="button" aria-label="Select nodes" disabled={!canvasReady} title="Select a schema without dragging the canvas" aria-pressed={tool === 'select'} onClick={() => setTool('select')}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m5 3 14 10-7 1-3 7Z" /></svg><span>Select</span></button></div>
      <div className="flow-lineage-tool-group"><button type="button" aria-label="Fit layout" disabled={!canvasReady} title="Fit all schemas to the canvas" onClick={fitGraph}><FullscreenOutlined /><span>Layout</span></button><button type="button" aria-label="Reset layout" disabled={!canvasReady} title="Reset to 100%" onClick={() => { setAutoFit(false); setManualZoom(1); viewportRef.current?.scrollTo({ left: 0, top: 0 }); }}><ReloadOutlined /><span>Reset</span></button></div>
      <div className="flow-lineage-tool-group"><button type="button" disabled={!canvasReady} aria-label={paused ? 'Play flow animation' : 'Pause flow animation'} title={paused ? 'Play animation' : 'Pause animation'} aria-pressed={paused} onClick={() => updateQuery({ motion: paused ? null : 'off' }, true)}>{paused ? <PlayFilled /> : <PauseOutlined />}<span>Motion</span></button><button type="button" aria-label="Toggle schema color legend" disabled={!canvasReady} aria-pressed={legendOpen} aria-expanded={legendOpen} onClick={() => setLegendOpen(value => !value)}><EyeOutlined /><span>Legend</span></button></div>
      <label className="flow-lineage-search"><SearchOutlined /><input ref={searchRef} aria-label="Search lineage nodes" placeholder="Find schemas" value={search} onChange={event => updateQuery({ q: event.target.value || null }, true)} />{search && <button type="button" aria-label="Clear lineage search" onClick={() => { updateQuery({ q: null }, true); searchRef.current?.focus(); }}><CloseOutlined /></button>}</label>
      <span className="flow-lineage-notice" title="Relationships are predefined ETL processor contracts, not discovered data lineage.">Relationships predefined</span>
    </div>
    {error ? <div className="flow-lineage-state" role="alert">Unable to load lineage assets.<button type="button" className="flow-lineage-action" onClick={() => setVersion(value => value + 1)}>Retry</button></div>
      : loading ? <div className="flow-lineage-state"><Spin /></div>
        : !nodes.length ? <div className="flow-lineage-state">{search ? 'No matching assets' : 'No schemas to display'}</div>
          : <div className="flow-lineage-body">
            <div className="flow-lineage-canvas-shell">
              <div className="flow-lineage-viewport" ref={viewportRef} tabIndex={0} aria-label="Lineage canvas. Drag empty space to pan and use the toolbar to zoom." style={{ cursor: panning ? 'grabbing' : tool === 'pan' ? 'grab' : 'default' }} onPointerDown={event => {
                if (tool !== 'pan' || event.button !== 0 || event.pointerType !== 'mouse' || (event.target as Element).closest('button,input,a')) return;
                pointer.current = { x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop };
                event.currentTarget.setPointerCapture(event.pointerId); setPanning(true);
              }} onPointerMove={event => {
                if (pointer.current) event.currentTarget.scrollTo({ left: pointer.current.left - (event.clientX - pointer.current.x), top: pointer.current.top - (event.clientY - pointer.current.y) });
              }} onPointerUp={() => { pointer.current = undefined; setPanning(false); }} onPointerCancel={() => { pointer.current = undefined; setPanning(false); }} onKeyDown={event => {
                if (event.target !== event.currentTarget) return;
                if (event.key === '+' || event.key === '=') { event.preventDefault(); changeZoom(zoom + .1); }
                if (event.key === '-') { event.preventDefault(); changeZoom(zoom - .1); }
                if (event.key === 'Home') { event.preventDefault(); fitGraph(); }
              }}>
                <div className="flow-lineage-extent" style={{ width: extentWidth, height: extentHeight }}><div className="flow-lineage-stage" style={{ width, height, left: insetX, top: insetY, minWidth: 0, minHeight: 0, transform: zoom === 1 ? undefined : `scale(${zoom})`, transformOrigin: 'top left' }}>
                  {hasPipeline && <div className="flow-lineage-stage-labels">{['Ingestion', 'Standardization', 'Tasks & dimensions', 'Detail', 'Aggregation'].map((label, index) => <span key={label} style={{ left: 40 + index * stageStep }}>{label}</span>)}</div>}
                  {independent.length > 0 && <div className="flow-lineage-independent" style={{ top: independentTop - 54 }}>Unmapped preview assets <span>{independent.length} schemas · No predefined upstream or downstream relationships</span></div>}
                  <svg ref={graphRef} className="flow-lineage-edges" width={width} height={height} aria-label="Static processor contract preview">
                    <defs>{links.map((link, index) => <marker key={link.source + link.target} id={`${id}-arrow-${index}`} markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6Z" fill="var(--lineage-edge)" /></marker>)}</defs>
                    {links.map((link, index) => <g key={link.source + link.target} data-source={link.source} data-target={link.target} data-color={link.color}>
                      <path data-testid="ontology-lineage-edge" data-motion={link.from.metricsAvailable ? 'measured' : 'direction'} d={link.d} fill="none" stroke="var(--lineage-edge)" strokeOpacity=".85" strokeWidth="1" strokeDasharray={link.from.metricsAvailable ? undefined : '5 7'} markerEnd={`url(#${id}-arrow-${index})`}>
                        {!link.from.metricsAvailable && <animate data-testid="ontology-direction-demo" attributeName="stroke-dashoffset" from="24" to="0" dur="1.6s" repeatCount="indefinite" />}
                      </path>
                      {link.from.metricsAvailable && link.from.rateOut > 0 && <circle r="2.7" fill={link.color} data-testid="ontology-flow-packet"><animateMotion dur="3.8s" repeatCount="indefinite" path={link.d} /></circle>}
                    </g>)}
                  </svg>
                  {nodes.map(node => <button ref={element => { nodeRefs.current[node.key] = element; }} key={node.key} type="button" data-testid="ontology-lineage-node" data-schema={node.key} className={`flow-lineage-node${selected === node.key ? ' is-selected' : ''}`} style={{ left: node.x, top: node.y, '--schema-color': schemaColor(node.key) } as React.CSSProperties} title={`${node.alias || node.label} · ${node.tables ?? '—'} tables · ${node.status === 'running' ? 'Available' : 'Unavailable'}`} aria-label={`${node.key}, ${node.alias || node.label}`} onClick={() => selectNode(node.key)}><DatabaseOutlined /><strong>{node.key}</strong><ArrowRightOutlined /></button>)}
                </div></div>
              </div>
              {legendOpen && <aside className="flow-lineage-legend" aria-label="Schema color legend"><header><strong>Schema colors</strong><button type="button" aria-label="Close schema color legend" onClick={() => setLegendOpen(false)}><CloseOutlined /></button></header><div>{schemas.map(schema => <span key={schema.key}><i style={{ background: schemaColor(schema.key) }} />{schema.key}</span>)}</div><p>Node colors identify schemas. Gray arrows show predefined contracts; colored dots show measured output.</p></aside>}
              <div className="flow-lineage-canvas-tools" aria-label="Lineage canvas tools"><button type="button" aria-label="Fit to view" title="Fit to view (Home)" onClick={fitGraph}><FullscreenOutlined /></button><button type="button" aria-label="Zoom in" title="Zoom in (+)" disabled={zoom >= 1.75} onClick={() => changeZoom(zoom + .1)}><PlusOutlined /></button><button type="button" aria-label="Zoom out" title="Zoom out (-)" disabled={zoom <= .25} onClick={() => changeZoom(zoom - .1)}><MinusOutlined /></button><button type="button" aria-label="Reset view" title="Reset to 100%" onClick={() => { setAutoFit(false); setManualZoom(1); viewportRef.current?.scrollTo({ left: 0, top: 0 }); }}><ReloadOutlined /></button><span className="flow-lineage-zoom-label" aria-live="polite">{Math.round(zoom * 100)}%</span></div>
              <div className="flow-lineage-dock" role="toolbar" aria-label="Selected schema panels">{([{ key: 'details', label: 'Details', icon: <BarsOutlined /> }, { key: 'upstream', label: 'Upstream', icon: <ArrowUpOutlined /> }, { key: 'downstream', label: 'Downstream', icon: <ArrowDownOutlined /> }] as const).map(item => <button key={item.key} type="button" disabled={!asset} aria-pressed={Boolean(asset && inspectorOpen && panel === item.key)} onClick={() => openPanel(item.key)}>{item.icon}<span>{item.label}</span></button>)}</div>
              <div className="flow-lineage-status"><span>{nodes.length} schemas · {links.length} predefined relationships</span><small data-testid="ontology-lineage-motion-status">{motionStatus}</small></div>
              {asset && inspectorOpen && <aside className="flow-lineage-inspector" aria-label="Node details"><header><span>SELECTED SCHEMA</span><button type="button" aria-label="Close node details" onClick={closeInspector}><CloseOutlined /></button></header><h2>{asset.alias || asset.label}</h2><code>{asset.key}</code><p className="flow-lineage-inspector-disclosure">ETL contract preview. Relationships are predefined, not discovered data lineage.</p>
                {panel === 'details' && <dl><dt>Database</dt><dd>{asset.database || '—'}</dd><dt>Tables</dt><dd>{asset.tables ?? '—'}</dd><dt>Input rate</dt><dd>{asset.metricsAvailable ? `${asset.rateIn.toLocaleString('en-US')} /s` : 'Metrics unavailable'}</dd><dt>Output rate</dt><dd>{asset.metricsAvailable ? `${asset.rateOut.toLocaleString('en-US')} /s` : 'Metrics unavailable'}</dd><dt>Relationships</dt><dd>{stages[asset.key] ? 'Predefined processor contract' : 'Unmapped in preview'}</dd></dl>}
                <div className="flow-lineage-node-relations">{(['upstream', 'downstream'] as const).filter(direction => panel === 'details' || panel === direction).map(direction => {
                  const related = relationships.filter(([source, target]) => direction === 'upstream' ? target === asset.key : source === asset.key).map(([source, target]) => schemas.find(schema => schema.key === (direction === 'upstream' ? source : target))).filter((schema): schema is LayerNode => !!schema);
                  return <section key={direction}><h3>{direction === 'upstream' ? 'Upstream assets' : 'Downstream assets'}</h3>{related.length ? related.map(schema => <button type="button" key={schema.key} onClick={() => selectNode(schema.key)}><i style={{ background: schemaColor(schema.key) }} /><span>{schema.key}</span><ArrowRightOutlined /></button>) : <p>No predefined {direction === 'upstream' ? 'upstream' : 'downstream'} relationships in this preview</p>}</section>;
                })}</div><button type="button" className="flow-lineage-action flow-lineage-open-workspace" onClick={openWorkspace}>Open data workspace <ArrowRightOutlined /></button>
              </aside>}
            </div>
            <aside className="flow-lineage-tool-rail" aria-label="Lineage side tools"><button type="button" aria-label="Find schemas" title="Find schemas" onClick={() => { searchRef.current?.focus(); searchRef.current?.select(); }}><SearchOutlined /></button><button type="button" aria-label="Show schema details" title="Schema details" disabled={!asset} aria-pressed={Boolean(asset && inspectorOpen && panel === 'details')} onClick={() => openPanel('details')}><BarsOutlined /></button><button type="button" aria-label="Show upstream assets" title="Upstream assets" disabled={!asset} aria-pressed={Boolean(asset && inspectorOpen && panel === 'upstream')} onClick={() => openPanel('upstream')}><ArrowUpOutlined /></button><button type="button" aria-label="Show downstream assets" title="Downstream assets" disabled={!asset} aria-pressed={Boolean(asset && inspectorOpen && panel === 'downstream')} onClick={() => openPanel('downstream')}><ArrowDownOutlined /></button><hr /><button type="button" aria-label="Open selected data workspace" title="Open data workspace" disabled={!asset} onClick={openWorkspace}><DatabaseOutlined /></button><button type="button" aria-label="Open runtime" title="Open runtime" onClick={() => navigate('/flow/runtime')}><ExportOutlined /></button></aside>
          </div>}
  </div>;
}

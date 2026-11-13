import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowRightOutlined, DatabaseOutlined, FullscreenOutlined, MinusOutlined, PauseOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { Button, Input, Spin } from '@/components/ui';
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
  ts_rds: { column: 0, y: 100 }, ts_ods: { column: 1, y: 100 },
  ts_task: { column: 2, y: 220 }, ts_dim: { column: 2, y: 330 },
  ts_dwd: { column: 3, y: 100 }, ts_dws: { column: 4, y: 100 },
};

export default function OntologyLineage({ refreshToken = 0 }: { refreshToken?: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const search = params.get('q') || '';
  const selected = params.get('schema') || '';
  const paused = params.get('motion') === 'off';
  const viewportRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<SVGSVGElement>(null);
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const pointer = useRef<{ x: number; y: number; left: number; top: number }>();
  const [zoom, setZoom] = useState(1);
  const [autoFit, setAutoFit] = useState(true);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [panning, setPanning] = useState(false);
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
  const pipelineHeight = hasPipeline ? Math.max(440, viewportSize.height - (independentRows ? independentRows * 130 + 90 : 0)) : 0;
  const independentTop = hasPipeline ? pipelineHeight + 60 : 100;
  const height = Math.max(viewportSize.height, independentRows ? independentTop + independentRows * 130 + 32 : pipelineHeight);
  const stageStep = (width - 256) / 4;
  const independentStep = columns > 1 ? (width - 256) / (columns - 1) : 0;
  const nodes = visible.map((schema) => {
    const stage = stages[schema.key];
    const index = independent.findIndex((item) => item.key === schema.key);
    return { ...schema, x: stage ? 40 + stage.column * stageStep : 40 + (index % columns) * independentStep, y: stage ? stage.y / 440 * pipelineHeight : independentTop + Math.floor(index / columns) * 130 };
  });
  const links = relationships.flatMap(([source, target]) => {
    const from = nodes.find((node) => node.key === source);
    const to = nodes.find((node) => node.key === target);
    if (!from || !to) return [];
    const startX = from.x + 176;
    const endX = to.x;
    const startY = from.y + 43;
    const endY = to.y + 43;
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
    const measure = () => setViewportSize((current) => current.width === viewport.clientWidth && current.height === viewport.clientHeight ? current : { width: viewport.clientWidth, height: viewport.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [loading, error, visible.length]);
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!autoFit || !viewportSize.width || !viewportSize.height) return;
    setZoom(Math.max(.25, Math.min(1, viewportSize.width / width, viewportSize.height / height)));
  }, [autoFit, viewportSize.width, viewportSize.height, width, height]);
  const selectNode = (key: string) => updateQuery({ schema: key, ...(nodes.some((node) => node.key === key) ? {} : { q: null }) });
  const closeInspector = () => {
    updateQuery({ schema: null }, true);
    requestAnimationFrame(() => nodeRefs.current[selected]?.focus());
  };
  const changeZoom = (value: number) => {
    setAutoFit(false);
    const next = Math.max(.25, Math.min(1.75, Math.round(value * 100) / 100));
    const viewport = viewportRef.current;
    const left = viewport ? (viewport.scrollLeft + viewport.clientWidth / 2) * next / zoom - viewport.clientWidth / 2 : 0;
    const top = viewport ? (viewport.scrollTop + viewport.clientHeight / 2) * next / zoom - viewport.clientHeight / 2 : 0;
    setZoom(next);
    requestAnimationFrame(() => viewport?.scrollTo({ left: Math.max(0, left), top: Math.max(0, top) }));
  };
  const fitGraph = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    setAutoFit(true);
    setZoom(Math.max(.25, Math.min(1, viewport.clientWidth / width, viewport.clientHeight / height)));
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
    viewport.scrollTo({ left: Math.max(0, (selectedNode.x + 88) * zoom - viewport.clientWidth / 2), top: Math.max(0, (selectedNode.y + 43) * zoom - viewport.clientHeight / 2) });
  }, [selectedNode?.key, selectedNode?.x, selectedNode?.y, loading]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && selected) { event.preventDefault(); closeInspector(); }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [selected, params]);
  return <div className="flow-lineage" data-testid="ontology-lineage">
    <header className="flow-lineage-toolbar"><div><span>DATA RELATIONSHIPS</span><h1>Data lineage</h1></div><Input aria-label="Search lineage nodes" prefix={<SearchOutlined />} placeholder="Search schemas" allowClear value={search} onChange={(event) => updateQuery({ q: event.target.value || null }, true)} /><Button icon={<ReloadOutlined />} loading={loading} onClick={() => setVersion((value) => value + 1)}>Refresh</Button></header>
    <div className="flow-lineage-notice"><ArrowRightOutlined /><span>One-way processing dependencies. Dots show measured output; moving dashes illustrate processing direction.</span><small data-testid="ontology-lineage-motion-status">{reducedMotion ? 'Animations are off because reduced motion is enabled.' : paused ? 'Animation paused' : liveLinks ? `${liveLinks} live outputs${directionLinks ? ` · ${directionLinks} direction previews` : ''}` : directionLinks ? `${directionLinks} direction previews · Live metrics unavailable` : 'No measured output · Live motion stopped'}</small></div>
    {error ? <div className="flow-lineage-state" role="alert">Unable to load lineage assets.<Button onClick={() => setVersion((value) => value + 1)}>Retry</Button></div>
      : loading ? <div className="flow-lineage-state"><Spin /></div>
        : !nodes.length ? <div className="flow-lineage-state">{search ? 'No matching assets' : 'No schemas to display'}</div>
          : <div className="flow-lineage-body"><div className="flow-lineage-viewport" ref={viewportRef} tabIndex={0} aria-label="Lineage canvas. Drag empty space to pan and use the toolbar to zoom." style={{ cursor: panning ? 'grabbing' : 'grab' }} onPointerDown={(event) => {
            if (event.button !== 0 || event.pointerType !== 'mouse' || (event.target as Element).closest('button,input,a')) return;
            pointer.current = { x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop };
            event.currentTarget.setPointerCapture(event.pointerId); setPanning(true);
          }} onPointerMove={(event) => {
            if (pointer.current) event.currentTarget.scrollTo({ left: pointer.current.left - (event.clientX - pointer.current.x), top: pointer.current.top - (event.clientY - pointer.current.y) });
          }} onPointerUp={() => { pointer.current = undefined; setPanning(false); }} onPointerCancel={() => { pointer.current = undefined; setPanning(false); }} onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.key === '+' || event.key === '=') { event.preventDefault(); changeZoom(zoom + .1); }
            if (event.key === '-') { event.preventDefault(); changeZoom(zoom - .1); }
            if (event.key === 'Home') { event.preventDefault(); fitGraph(); }
          }}><div className="flow-lineage-extent" style={{ width: Math.ceil(width * zoom), height: Math.ceil(height * zoom) }}><div className="flow-lineage-stage" style={{ width, height, minWidth: 0, minHeight: 0, transform: `scale(${zoom})`, transformOrigin: 'top left' }}>
            {hasPipeline && <div className="flow-lineage-stage-labels">{['Ingestion', 'Standardization', 'Tasks & dimensions', 'Detail', 'Aggregation'].map((label, index) => <span key={label} style={{ left: 40 + index * stageStep }}>{label}</span>)}</div>}
            {independent.length > 0 && <div className="flow-lineage-independent" style={{ top: independentTop - 54 }}>Independent assets <span>{independent.length} schemas · No registered upstream or downstream relationships</span></div>}
            <svg ref={graphRef} className="flow-lineage-edges" width={width} height={height} aria-label="Registered processing dependencies">
              <defs>{links.map((link, index) => <marker key={link.source + link.target} id={`${id}-arrow-${index}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,1 L7,4 L0,7" fill="none" stroke={link.color} strokeWidth="1.2" /></marker>)}</defs>
              {links.map((link, index) => <g key={link.source + link.target} data-source={link.source} data-target={link.target} data-color={link.color}>
                <path data-testid="ontology-lineage-edge" data-motion={link.from.metricsAvailable ? 'measured' : 'direction'} d={link.d} fill="none" stroke={link.color} strokeOpacity=".55" strokeWidth="1.4" strokeDasharray={link.from.metricsAvailable ? undefined : '5 7'} markerEnd={`url(#${id}-arrow-${index})`}>
                  {!link.from.metricsAvailable && <animate data-testid="ontology-direction-demo" attributeName="stroke-dashoffset" from="24" to="0" dur="1.6s" repeatCount="indefinite" />}
                </path>
                {link.from.metricsAvailable && link.from.rateOut > 0 && <circle r="3.5" fill={link.color} data-testid="ontology-flow-packet"><animateMotion dur="3.8s" repeatCount="indefinite" path={link.d} /></circle>}
              </g>)}
            </svg>
            {nodes.map((node) => <button ref={(element) => { nodeRefs.current[node.key] = element; }} key={node.key} type="button" data-testid="ontology-lineage-node" data-schema={node.key} className={`flow-lineage-node${selected === node.key ? ' is-selected' : ''}`} style={{ left: node.x, top: node.y, '--schema-color': schemaColor(node.key) } as React.CSSProperties} onClick={() => selectNode(node.key)}><DatabaseOutlined /><span><strong>{node.alias || node.label}</strong><small>{node.key}</small><em>{node.tables ?? '—'} tables · {node.status === 'running' ? 'Available' : 'Unavailable'}</em></span></button>)}
          </div></div></div>
          <div className="flow-lineage-canvas-tools" aria-label="Lineage canvas tools"><button type="button" aria-label="Zoom out" title="Zoom out (-)" disabled={zoom <= .25} onClick={() => changeZoom(zoom - .1)}><MinusOutlined /></button><span className="flow-lineage-zoom-label" aria-live="polite">{Math.round(zoom * 100)}%</span><button type="button" aria-label="Zoom in" title="Zoom in (+)" disabled={zoom >= 1.75} onClick={() => changeZoom(zoom + .1)}><PlusOutlined /></button><button type="button" aria-label="Fit to view" title="Fit to view (Home)" onClick={fitGraph}><FullscreenOutlined /></button><button type="button" aria-label="Reset view" title="Reset to 100%" onClick={() => { setAutoFit(false); setZoom(1); viewportRef.current?.scrollTo({ left: 0, top: 0 }); }}><ReloadOutlined /></button><button type="button" aria-label={paused ? 'Play flow animation' : 'Pause flow animation'} title={paused ? 'Play animation' : 'Pause animation'} aria-pressed={paused} onClick={() => updateQuery({ motion: paused ? null : 'off' }, true)}>{paused ? <PlayCircleOutlined /> : <PauseOutlined />}</button></div>
          {asset && <aside className="flow-lineage-inspector" aria-label="Node details"><span className="flow-lineage-inspector-eyebrow">SELECTED RESOURCE</span><h2>{asset.alias || asset.label}</h2><code>{asset.key}</code><dl><dt>Database</dt><dd>{asset.database || '—'}</dd><dt>Tables</dt><dd>{asset.tables ?? '—'}</dd><dt>Input rate</dt><dd>{asset.metricsAvailable ? `${asset.rateIn.toLocaleString('en-US')} /s` : 'Metrics unavailable'}</dd><dt>Output rate</dt><dd>{asset.metricsAvailable ? `${asset.rateOut.toLocaleString('en-US')} /s` : 'Metrics unavailable'}</dd><dt>Relationships</dt><dd>{stages[asset.key] ? 'ETL processor contract' : 'Not registered'}</dd></dl><div className="flow-lineage-node-relations">{(['upstream', 'downstream'] as const).map((direction) => {
            const related = relationships.filter(([source, target]) => direction === 'upstream' ? target === asset.key : source === asset.key).map(([source, target]) => schemas.find((schema) => schema.key === (direction === 'upstream' ? source : target))).filter((schema): schema is LayerNode => !!schema);
            return <section key={direction}><h3>{direction === 'upstream' ? 'Upstream assets' : 'Downstream assets'}</h3>{related.length ? related.map((schema) => <button type="button" key={schema.key} onClick={() => selectNode(schema.key)}><i style={{ background: schemaColor(schema.key) }} /><span>{schema.key}</span><ArrowRightOutlined /></button>) : <p>No registered {direction === 'upstream' ? 'upstream' : 'downstream'} relationships</p>}</section>;
          })}</div><Button onClick={() => navigate(`/flow/layers?schema=${encodeURIComponent(asset.key)}`)}>Open data workspace <ArrowRightOutlined /></Button><button type="button" className="flow-lineage-close" aria-label="Close node details" onClick={closeInspector}>Close details</button></aside>}
          </div>}
  </div>;
}

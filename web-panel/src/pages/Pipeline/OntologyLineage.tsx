import { useEffect, useId, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowRightOutlined, DatabaseOutlined, FullscreenOutlined, MinusOutlined, PauseOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Input, Spin } from 'antd';
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

export default function OntologyLineage() {
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
  }, [version]);
  useEffect(() => {
    if (!loading && !error && selected && !schemas.some((schema) => schema.key === selected)) {
      setParams((current) => { const next = new URLSearchParams(current); next.delete('schema'); return next; }, { replace: true });
    }
  }, [schemas, loading, error, selected, setParams]);
  const visible = schemas.filter((schema) => `${schema.key} ${schema.label} ${schema.alias || ''}`.toLowerCase().includes(search.trim().toLowerCase()));
  const independent = visible.filter((schema) => !stages[schema.key]).sort((a, b) => a.key.localeCompare(b.key));
  const hasPipeline = visible.some((schema) => stages[schema.key]);
  const independentTop = hasPipeline ? 540 : 100;
  const columns = Math.max(4, Math.min(6, Math.ceil(Math.sqrt(independent.length))));
  const width = Math.max(1240, columns * 240 + 80);
  const height = independent.length ? independentTop + Math.ceil(independent.length / columns) * 130 : 480;
  const nodes = visible.map((schema) => {
    const stage = stages[schema.key];
    const index = independent.findIndex((item) => item.key === schema.key);
    return { ...schema, x: stage ? 40 + stage.column * 240 : 40 + (index % columns) * 240, y: stage ? stage.y : independentTop + Math.floor(index / columns) * 130 };
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
  const selectedNode = nodes.find((node) => node.key === selected);
  const selectNode = (key: string) => updateQuery({ schema: key, ...(nodes.some((node) => node.key === key) ? {} : { q: null }) });
  const closeInspector = () => {
    updateQuery({ schema: null }, true);
    requestAnimationFrame(() => nodeRefs.current[selected]?.focus());
  };
  const changeZoom = (value: number) => {
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
    setZoom(Math.max(.25, Math.min(1.5, (viewport.clientWidth - 32) / width, (viewport.clientHeight - 32) / height)));
    requestAnimationFrame(() => viewport.scrollTo({ left: 0, top: 0 }));
  };
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    if (paused) graph.pauseAnimations(); else graph.unpauseAnimations();
  }, [paused, loading, links.length]);
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
    <header className="flow-lineage-toolbar"><div><span>DATA RELATIONSHIPS</span><h1>数据血缘</h1></div><Input aria-label="搜索血缘节点" prefix={<SearchOutlined />} placeholder="搜索 schema" allowClear value={search} onChange={(event) => updateQuery({ q: event.target.value || null }, true)} /><Button icon={<ReloadOutlined />} loading={loading} onClick={() => setVersion((value) => value + 1)}>刷新</Button></header>
    <div className="flow-lineage-notice"><ArrowRightOutlined /><span>单向处理依赖 · 按当前 ETL 处理器定义展示。流动效果仅在该节点具有有效输出速率时显示。</span></div>
    {error ? <div className="flow-lineage-state" role="alert">血缘资产读取失败。<Button onClick={() => setVersion((value) => value + 1)}>重试</Button></div>
      : loading ? <div className="flow-lineage-state"><Spin /></div>
        : !nodes.length ? <div className="flow-lineage-state">{search ? '没有匹配的资产' : '尚未发现可展示的 schema'}</div>
          : <div className="flow-lineage-body"><div className="flow-lineage-viewport" ref={viewportRef} tabIndex={0} aria-label="血缘画布，拖动空白处平移，使用工具栏缩放" style={{ cursor: panning ? 'grabbing' : 'grab' }} onPointerDown={(event) => {
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
            {hasPipeline && <div className="flow-lineage-stage-labels">{['接入', '标准化', '任务与维度', '明细', '聚合'].map((label, index) => <span key={label} style={{ left: 40 + index * 240 }}>{label}</span>)}</div>}
            {independent.length > 0 && <div className="flow-lineage-independent" style={{ top: independentTop - 77 }}>独立资产 <span>{independent.length} 个 schema · 尚未登记上下游关系</span></div>}
            <svg ref={graphRef} className="flow-lineage-edges" width={width} height={height} aria-label="已定义的数据处理依赖">
              <defs>{links.map((link, index) => <marker key={link.source + link.target} id={`${id}-arrow-${index}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,1 L7,4 L0,7" fill="none" stroke={link.color} strokeWidth="1.2" /></marker>)}</defs>
              {links.map((link, index) => <g key={link.source + link.target} data-source={link.source} data-target={link.target} data-color={link.color}>
                <path data-testid="ontology-lineage-edge" d={link.d} fill="none" stroke={link.color} strokeOpacity=".55" strokeWidth="1.4" markerEnd={`url(#${id}-arrow-${index})`} />
                {link.from.metricsAvailable && link.from.rateOut > 0 && <circle r="3.5" fill={link.color} data-testid="ontology-flow-packet"><animateMotion dur="3.8s" repeatCount="indefinite" path={link.d} /></circle>}
              </g>)}
            </svg>
            {nodes.map((node) => <button ref={(element) => { nodeRefs.current[node.key] = element; }} key={node.key} type="button" data-testid="ontology-lineage-node" data-schema={node.key} className={`flow-lineage-node${selected === node.key ? ' is-selected' : ''}`} style={{ left: node.x, top: node.y, '--schema-color': schemaColor(node.key) } as React.CSSProperties} onClick={() => selectNode(node.key)}><DatabaseOutlined /><span><strong>{node.alias || node.label}</strong><small>{node.key}</small><em>{node.tables ?? '—'} tables · {node.status === 'running' ? '可访问' : '不可访问'}</em></span></button>)}
          </div></div></div>
          <div className="flow-lineage-canvas-tools" aria-label="血缘画布工具栏"><button type="button" aria-label="缩小画布" title="缩小 (-)" disabled={zoom <= .25} onClick={() => changeZoom(zoom - .1)}><MinusOutlined /></button><span className="flow-lineage-zoom-label" aria-live="polite">{Math.round(zoom * 100)}%</span><button type="button" aria-label="放大画布" title="放大 (+)" disabled={zoom >= 1.75} onClick={() => changeZoom(zoom + .1)}><PlusOutlined /></button><button type="button" aria-label="适应画布" title="适应画布 (Home)" onClick={fitGraph}><FullscreenOutlined /></button><button type="button" aria-label="重置画布" title="重置为100%" onClick={() => { setZoom(1); viewportRef.current?.scrollTo({ left: 0, top: 0 }); }}><ReloadOutlined /></button><button type="button" aria-label={paused ? '播放流动动画' : '暂停流动动画'} title={paused ? '播放动画' : '暂停动画'} aria-pressed={paused} onClick={() => updateQuery({ motion: paused ? null : 'off' }, true)}>{paused ? <PlayCircleOutlined /> : <PauseOutlined />}</button></div>
          {asset && <aside className="flow-lineage-inspector" aria-label="节点详情"><span className="flow-lineage-inspector-eyebrow">SELECTED RESOURCE</span><h2>{asset.alias || asset.label}</h2><code>{asset.key}</code><dl><dt>数据库</dt><dd>{asset.database || '—'}</dd><dt>表数量</dt><dd>{asset.tables ?? '—'}</dd><dt>输入速率</dt><dd>{asset.metricsAvailable ? `${asset.rateIn.toLocaleString()} /s` : '尚无运行指标'}</dd><dt>输出速率</dt><dd>{asset.metricsAvailable ? `${asset.rateOut.toLocaleString()} /s` : '尚无运行指标'}</dd><dt>关系</dt><dd>{stages[asset.key] ? 'ETL 处理器定义' : '尚未登记'}</dd></dl><div className="flow-lineage-node-relations">{(['upstream', 'downstream'] as const).map((direction) => {
            const related = relationships.filter(([source, target]) => direction === 'upstream' ? target === asset.key : source === asset.key).map(([source, target]) => schemas.find((schema) => schema.key === (direction === 'upstream' ? source : target))).filter((schema): schema is LayerNode => !!schema);
            return <section key={direction}><h3>{direction === 'upstream' ? '上游资产' : '下游资产'}</h3>{related.length ? related.map((schema) => <button type="button" key={schema.key} onClick={() => selectNode(schema.key)}><i style={{ background: schemaColor(schema.key) }} /><span>{schema.key}</span><ArrowRightOutlined /></button>) : <p>暂无已登记的{direction === 'upstream' ? '上游' : '下游'}关系</p>}</section>;
          })}</div><Button onClick={() => navigate(`/flow/layers?schema=${encodeURIComponent(asset.key)}`)}>打开数据工作区 <ArrowRightOutlined /></Button><button type="button" className="flow-lineage-close" aria-label="关闭节点详情" onClick={closeInspector}>关闭详情</button></aside>}
          </div>}
  </div>;
}

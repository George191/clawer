import { useEffect, useId, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeftOutlined, ArrowRightOutlined, CloseOutlined, DatabaseOutlined, ReloadOutlined } from '@/components/ui/icons';
import { Spin } from '@/components/ui';
import { fetchWorkerLineage, type WorkerLineageGraph } from '../../services/api';

const NODE_WIDTH = 216;
const NODE_HEIGHT = 64;
const COLUMN_STEP = 292;
const ROW_STEP = 100;
const PADDING = 32;

function layout(graph: WorkerLineageGraph) {
  const names = new Set(graph.layers.map(layer => layer.layer));
  const dependencies = graph.dependencies.filter(edge => names.has(edge.sourceLayer) && names.has(edge.targetLayer));
  const incoming = new Map(graph.layers.map(layer => [layer.layer, 0]));
  const outgoing = new Map<string, string[]>();
  for (const { sourceLayer, targetLayer } of dependencies) {
    incoming.set(targetLayer, incoming.get(targetLayer)! + 1);
    const targets = outgoing.get(sourceLayer) ?? [];
    targets.push(targetLayer);
    outgoing.set(sourceLayer, targets);
  }
  const ranks = new Map<string, number>();
  const ready = graph.layers.filter(layer => incoming.get(layer.layer) === 0).map(layer => layer.layer);
  for (let index = 0; index < ready.length; index++) {
    const source = ready[index];
    for (const target of outgoing.get(source) ?? []) {
      ranks.set(target, Math.max(ranks.get(target) ?? 0, (ranks.get(source) ?? 0) + 1));
      const remaining = incoming.get(target)! - 1;
      incoming.set(target, remaining);
      if (remaining === 0) ready.push(target);
    }
  }
  const rows = new Map<number, number>();
  // Put connected roots above isolated layers so a simple chain runs across one row.
  const ordered = [...graph.layers].sort((a, b) => Number(outgoing.has(b.layer)) - Number(outgoing.has(a.layer)));
  const nodes = new Map(ordered.map(layer => {
    const rank = ranks.get(layer.layer) ?? 0;
    const row = rows.get(rank) ?? 0;
    rows.set(rank, row + 1);
    return [layer.layer, { ...layer, x: PADDING + rank * COLUMN_STEP, y: PADDING + row * ROW_STEP }];
  }));
  const width = PADDING * 2 + NODE_WIDTH + Math.max(0, ...ranks.values()) * COLUMN_STEP;
  const height = PADDING * 2 + NODE_HEIGHT + (Math.max(1, ...rows.values()) - 1) * ROW_STEP;
  return { nodes, dependencies, width, height };
}

export default function WorkerLineage({ refreshToken }: { refreshToken: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const selectedLayer = params.get('workerLayer');
  const [version, setVersion] = useState(0);
  const [graph, setGraph] = useState<WorkerLineageGraph>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const marker = useId().replace(/:/g, '');
  const drawing = useMemo(() => graph && layout(graph), [graph]);
  const selected = selectedLayer ? drawing?.nodes.get(selectedLayer) : undefined;
  const refresh = () => setVersion(value => value + 1);

  useEffect(() => {
    const request = new AbortController();
    setLoading(true);
    setError(false);
    fetchWorkerLineage(request.signal).then(result => {
      if (!request.signal.aborted) setGraph(result);
    }).catch(() => {
      if (!request.signal.aborted) setError(true);
    }).finally(() => {
      if (!request.signal.aborted) setLoading(false);
    });
    return () => request.abort();
  }, [refreshToken, version]);

  return <>
    <header className="flow-lineage-context">
      <div className="flow-lineage-context-title">
        <button type="button" className="flow-lineage-back" aria-label="Back to object types" onClick={() => navigate('/ontology')}><ArrowLeftOutlined /></button>
        <h1>Data lineage</h1>
      </div>
      <div className="flow-lineage-context-actions">
        <button type="button" className="flow-lineage-action" aria-label="Refresh worker flow" disabled={loading} onClick={refresh}><ReloadOutlined spin={loading} /><span>Refresh</span></button>
      </div>
    </header>
    <p className="flow-lineage-worker-caption">Arrows show configured worker dependencies. Schema references and observed transfer evidence are in Schema references.</p>
    {loading ? <div className="flow-lineage-state" role="status" aria-label="Loading worker flow"><Spin /></div>
      : error ? <div className="flow-lineage-state" role="alert">Unable to load worker flow.<button type="button" className="flow-lineage-action" onClick={refresh}>Retry</button></div>
      : !drawing?.nodes.size ? <div className="flow-lineage-state" role="status">No warehouse layers configured.</div>
      : <div className="flow-lineage-worker-body">
        <div className="flow-lineage-viewport" tabIndex={0} aria-label="Worker flow graph">
          <div className="flow-lineage-worker-stage" style={{ width: drawing.width, height: drawing.height }}>
            <svg className="flow-lineage-edges" width={drawing.width} height={drawing.height} aria-hidden="true">
              <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0 0 7 3.5 0 7Z" fill="var(--lineage-edge)" /></marker></defs>
              {drawing.dependencies.map(({ sourceLayer, targetLayer }) => {
                const from = drawing.nodes.get(sourceLayer)!;
                const to = drawing.nodes.get(targetLayer)!;
                const start = from.x + NODE_WIDTH;
                const end = to.x;
                const mid = (start + end) / 2;
                const y1 = from.y + NODE_HEIGHT / 2;
                const y2 = to.y + NODE_HEIGHT / 2;
                const d = from.x < to.x
                  ? `M${start},${y1} C${mid},${y1} ${mid},${y2} ${end},${y2}`
                  : `M${start},${y1} h16 V${to.y - 16} H${to.x - 16} V${y2} H${end}`;
                return <path key={JSON.stringify([sourceLayer, targetLayer])} d={d} fill="none" stroke="var(--lineage-edge)" strokeWidth="1.5" markerEnd={`url(#${marker})`} />;
              })}
            </svg>
            {[...drawing.nodes.values()].map(node => <button type="button" id={marker + node.layer} key={node.layer}
              className={`flow-lineage-node flow-lineage-worker-node${selected?.layer === node.layer ? ' is-selected' : ''}`}
              style={{ left: node.x, top: node.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
              aria-pressed={selected?.layer === node.layer} onClick={() => setParams({ workerLayer: node.layer }, { replace: true })}>
              <DatabaseOutlined aria-hidden="true" />
              <span><strong>{node.layer}</strong><small title={node.sourceName ?? undefined}>{node.connectionId ? node.sourceName || node.connectionId : 'Unbound'}</small></span>
              <ArrowRightOutlined aria-hidden="true" />
            </button>)}
          </div>
        </div>
        {selected && <aside className="flow-lineage-inspector flow-lineage-worker-inspector" aria-label="Worker layer details">
          <header><span>SELECTED LAYER</span><button type="button" aria-label="Close worker layer details" onClick={() => { setParams({}, { replace: true }); document.getElementById(marker + selected.layer)?.focus(); }}><CloseOutlined /></button></header>
          <h2>{selected.layer}</h2>
          <dl>
            <dt>Connection</dt><dd>{selected.sourceName || selected.connectionId || 'Unbound'}</dd>
            {selected.engine && <><dt>Engine</dt><dd>{selected.engine}</dd></>}
            {selected.database && <><dt>Database</dt><dd>{selected.database}</dd></>}
            <dt>Objects</dt><dd>{selected.objectCount}</dd>
          </dl>
          <div className="flow-lineage-node-relations">
            {(['Upstream', 'Downstream'] as const).map(direction => {
              const peers = drawing.dependencies.flatMap(edge => direction === 'Upstream'
                ? edge.targetLayer === selected.layer ? [edge.sourceLayer] : []
                : edge.sourceLayer === selected.layer ? [edge.targetLayer] : []);
              return <section key={direction}><h3>{direction}</h3>{peers.length
                ? peers.map(peer => <button type="button" key={peer} onClick={() => setParams({ workerLayer: peer }, { replace: true })}><span>{peer}</span><ArrowRightOutlined /></button>)
                : <p>No configured dependencies</p>}</section>;
            })}
            <section><h3>Schemas</h3>{selected.schemas.length ? selected.schemas.map(schema => <Link key={schema.schemaName}
              className="flow-lineage-action" to={{ search: new URLSearchParams({
                view: 'references', schema: schema.schemaName, ...(selected.connectionId ? { connectionId: selected.connectionId } : {}),
              }).toString() }}>{schema.schemaName} · {schema.tables} tables <ArrowRightOutlined /></Link>) : <p>No registered schemas</p>}</section>
          </div>
        </aside>}
      </div>}
  </>;
}

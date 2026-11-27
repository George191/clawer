import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { fetchDataSourceReferences } from '../../services/api';
import type { DataSourceReferences, SourceReferenceDirection } from '../../services/api';
import { getSessionState } from '../../services/session';

export default function SourceCatalogReferences({ sourceId, schema, table, sourceRevision }: { sourceId: string; schema: string; table: string; sourceRevision: string }) {
  const [direction, setDirection] = useState<SourceReferenceDirection>('outgoing');
  const [result, setResult] = useState<DataSourceReferences>();
  const [status, setStatus] = useState<'idle' | 'loading' | 'error' | 'stale'>('idle');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  const changeDirection = (value: SourceReferenceDirection) => {
    if (direction === value) return;
    request.current?.abort();
    setResult(undefined);
    setStatus('idle');
    setDirection(value);
  };
  const load = async () => {
    const initial = getSessionState();
    if (!initial.authenticated || initial.locked) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const current = () => {
      const session = getSessionState();
      return !controller.signal.aborted && session.authenticated && !session.locked && session.identity === initial.identity;
    };
    setResult(undefined);
    setStatus('loading');
    try {
      const value = await fetchDataSourceReferences(sourceId, schema, table, direction, sourceRevision, controller.signal);
      if (!current()) return;
      if (value.direction !== direction) { setStatus('error'); return; }
      setResult(value);
      setStatus('idle');
    } catch (failure) {
      if (current()) setStatus((failure as { response?: { status?: number } })?.response?.status === 409 ? 'stale' : 'error');
    }
  };

  const root = `${schema}.${table}`;
  return <section className="ontology-source-catalog__references" aria-label="Foreign key references">
    <h4>Foreign key references</h4>
    <p>Direct PostgreSQL references visible to this saved account. References describe constraints, not data movement.</p>
    <div className="ontology-source-catalog__pager" aria-label="Reference direction" role="group">
      <Button size="small" aria-pressed={direction === 'outgoing'} onClick={() => changeDirection('outgoing')}>Outgoing</Button>
      <Button size="small" aria-pressed={direction === 'incoming'} onClick={() => changeDirection('incoming')}>Incoming</Button>
      <Button size="small" disabled={status === 'loading'} onClick={() => void load()}>Load references</Button>
    </div>
    {status === 'loading' && <p role="status"><LoadingIndicator size={20}>Loading references…</LoadingIndicator></p>}
    {status === 'stale' && <p role="alert">This saved connection changed. Reload Data Connections before loading references again.</p>}
    {status === 'error' && <p role="alert">Could not load foreign key references for this table.</p>}
    {result && (result.references.length ? <ul>
      {result.references.map(reference => {
        const peer = `${reference.schema}.${reference.table}`;
        const [from, to] = result.direction === 'outgoing' ? [root, peer] : [peer, root];
        return <li key={JSON.stringify([reference.schema, reference.table, reference.pairs])}>
          <strong><code>{peer}</code></strong>
          <ol>{reference.pairs.map(({ fromColumn, toColumn }, index) => <li key={index}>
            <code>{from}.{fromColumn}</code> → <code>{to}.{toColumn}</code>
          </li>)}</ol>
        </li>;
      })}
    </ul> : <p role="status">No visible {direction} foreign key references.</p>)}
    {result?.hasMore && <p role="status">Showing up to 12 visible references.</p>}
  </section>;
}

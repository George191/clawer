import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import { LinkOutlined } from '@/components/ui/icons';
import { fetchPublishedRelationships, type PublishedRelationships } from '../../services/publishedExplorerDetails';
import { fetchPublishedOntologyType, ontologyError, type OntologyRecord, type PublishedOntologyType } from '../../services/ontology';
import { publishedLayoutPath, readLayouts, type Layouts } from '../../services/explorerLayouts';
import { publishedColumnTypes, publishedPresentation } from '../../services/publishedExplorerPresentation';
import { PropertyLabel } from './PropertyTypeIcon';
import { ExplorerFieldValue, explorerFields } from './ExplorerObjectOverview';
import ExplorerRecordLink from './ExplorerRecordLink';
import { ExplorerValueProvider } from './ExplorerValue';
import './explorer-row-links.css';

function LinkedProperties({ definitionId, record }: { definitionId: string; record: OntologyRecord }) {
  const [read, setRead] = useState<{ type?: PublishedOntologyType; layouts?: Layouts; error?: string }>();
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([fetchPublishedOntologyType(definitionId, controller.signal), readLayouts(publishedLayoutPath(definitionId), controller.signal)]).then(
      ([type, layouts]) => { if (!controller.signal.aborted) setRead({ type, layouts }); },
      error => { if (!controller.signal.aborted) setRead({ error: ontologyError(error, 'Unable to read object properties.') }); },
    );
    return () => controller.abort();
  }, [definitionId]);
  if (read?.error) return <p role="alert">{read.error}</p>;
  if (!read?.type || !read.layouts) return <p role="status">Loading object properties…</p>;
  const row = { key: record.id!, record: record.properties, identity: { $recordId: record.id! }, title: String(record.title ?? 'Untitled object'), columnTypes: publishedColumnTypes(read.type) };
  const layout = read.layouts.personal.layout ?? read.layouts.default.layout;
  const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
  return <ExplorerValueProvider source={{ kind: 'published', definitionId }}><dl className="explorer-linked-properties" aria-label="Related record properties">
    {explorerFields(row, Object.keys(record.properties), publishedPresentation(read.type), presentations).map(field => <div key={field.key}>
      <dt><PropertyLabel type={field.type}>{field.label}</PropertyLabel></dt><dd><ExplorerFieldValue row={row} field={field} /></dd>
    </div>)}
  </dl></ExplorerValueProvider>;
}

type Props = { definitionId: string; recordId: string };
function RelationshipPage({ definitionId, recordId }: Props) {
  const [relation, setRelation] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [version, reload] = useState(0);
  const [read, setRead] = useState<{ key: string; page?: PublishedRelationships; error?: string }>();
  const [expanded, setExpanded] = useState('');
  const key = JSON.stringify([definitionId, recordId, relation, offset, version]);
  useEffect(() => {
    const controller = new AbortController();
    setExpanded('');
    fetchPublishedRelationships(definitionId, recordId, relation, offset, controller.signal).then(
      page => { if (!controller.signal.aborted) setRead({ key, page }); },
      error => { if (!controller.signal.aborted) setRead({ key, error: ontologyError(error, 'Unable to read relationships.') }); },
    );
    return () => controller.abort();
  }, [key, definitionId, recordId, relation, offset, version]);
  const current = read?.key === key ? read : undefined;
  const loading = !current;
  const page = current?.page ?? read?.page;
  const selected = page?.relations.find(item => item.id + ':' + item.direction === page.selected);
  if (current?.error) return <p role="alert">{current.error} <Button size="small" onClick={() => reload(value => value + 1)}>Retry</Button></p>;
  if (!page) return <p role="status">Loading relationships…</p>;
  if (!selected) return <section className="explorer-row-links explorer-links-empty" role="status"><LinkOutlined /><strong>No relationships</strong><p>No declared relationships are available for this object.</p></section>;
  return <section className="explorer-row-links" aria-label="Related records" aria-busy={loading}>
    <header className="explorer-links-heading"><LinkOutlined /><label><span>Relationships</span>
      <select aria-label="Relationship" value={page.selected} disabled={loading} onChange={event => { setRelation(event.target.value); setOffset(0); }}>
        {page.relations.map(item => <option key={item.id + ':' + item.direction} value={item.id + ':' + item.direction}>{item.name} · {item.direction === 'incoming' ? 'Incoming' : 'Outgoing'}</option>)}
      </select>
    </label></header>
    <div className="explorer-links-mapping"><div><span className="explorer-links-badge">{selected.direction === 'incoming' ? 'Incoming' : 'Outgoing'}</span><strong>{selected.name}</strong></div></div>
    {page.items.length ? <ul>{page.items.map(record => <li key={record.id}>
      <ExplorerRecordLink definitionId={selected.targetDefinitionId} identity={{ $recordId: record.id! }}>{String(record.title ?? 'Untitled object')}</ExplorerRecordLink>
      <Button size="small" disabled={loading} aria-expanded={expanded === record.id} onClick={() => setExpanded(value => value === record.id ? '' : record.id!)}>{expanded === record.id ? 'Hide properties' : 'Show properties'}</Button>
      {expanded === record.id && <LinkedProperties definitionId={selected.targetDefinitionId} record={record} />}
    </li>)}</ul> : <div className="explorer-links-empty" role="status"><strong>No matching objects</strong><p>No visible objects match this relationship.</p></div>}
    {(offset > 0 || page.hasMore) && <nav aria-label="Related records pages">
      <Button size="small" disabled={loading || !offset} onClick={() => { setRelation(page.selected); setOffset(value => value - 10); }}>Previous</Button><span>Page {1 + offset / 10}</span>
      <Button size="small" disabled={loading || !page.hasMore || offset >= 100_000} onClick={() => { setRelation(page.selected); setOffset(value => value + 10); }}>Next</Button>
    </nav>}
  </section>;
}

export default function PublishedExplorerRelationships(props: Props) {
  return <RelationshipPage key={JSON.stringify([props.definitionId, props.recordId])} {...props} />;
}

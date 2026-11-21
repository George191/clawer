import { useEffect, useState } from 'react';
import { fetchPublishedRelationships, type PublishedRelationships } from '../../services/publishedExplorerDetails';
import { fetchPublishedOntologyType, ontologyError, type OntologyRecord, type PublishedOntologyType } from '../../services/ontology';
import { publishedLayoutPath, readLayouts, type Layouts } from '../../services/explorerLayouts';
import { publishedColumnTypes, publishedPresentation } from '../../services/publishedExplorerPresentation';
import { explorerFields } from './ExplorerObjectOverview';
import ExplorerRecordLink from './ExplorerRecordLink';
import { ExplorerValueProvider } from './ExplorerValue';
import ExplorerRelationshipsView, { ExplorerLinkedProperties } from './ExplorerRelationshipsView';
import useExplorerErrorToast from './useExplorerErrorToast';
import { getSessionState } from '../../services/session';

const live = (signal: AbortSignal, identity: string) => {
  const session = getSessionState();
  return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
};

function LinkedProperties({ definitionId, record }: { definitionId: string; record: OntologyRecord }) {
  const [read, setRead] = useState<{ type?: PublishedOntologyType; layouts?: Layouts; error?: string }>();
  const identity = getSessionState().identity;
  useEffect(() => {
    const controller = new AbortController();
    setRead(undefined);
    Promise.all([fetchPublishedOntologyType(definitionId, controller.signal), readLayouts(publishedLayoutPath(definitionId), controller.signal)]).then(
      ([type, layouts]) => { if (live(controller.signal, identity)) setRead({ type, layouts }); },
      error => { if (live(controller.signal, identity) && error?.code !== 'ERR_CANCELED') setRead({ error: ontologyError(error, 'Unable to read object properties.') }); },
    );
    return () => controller.abort();
  }, [definitionId, identity]);
  useExplorerErrorToast(read?.error, 'Related object properties unavailable');
  if (read?.error) return null;
  if (!read?.type || !read.layouts) return <p role="status">Loading object properties…</p>;
  const row = { key: record.id!, record: record.properties, identity: { $recordId: record.id! }, title: String(record.title ?? 'Untitled object'), columnTypes: publishedColumnTypes(read.type) };
  const layout = read.layouts.personal.layout ?? read.layouts.default.layout;
  const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
  return <ExplorerValueProvider source={{ kind: 'published', definitionId }}>
    <ExplorerLinkedProperties row={row} fields={explorerFields(row, Object.keys(record.properties), publishedPresentation(read.type), presentations)} />
  </ExplorerValueProvider>;
}

type Props = { definitionId: string; recordId: string };
function RelationshipPage({ definitionId, recordId }: Props) {
  const [relation, setRelation] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [version, reload] = useState(0);
  const [read, setRead] = useState<{ key: string; page?: PublishedRelationships; error?: string }>();
  const [expanded, setExpanded] = useState('');
  const identity = getSessionState().identity;
  const key = JSON.stringify([definitionId, recordId, relation, offset, version, identity]);
  useEffect(() => {
    const controller = new AbortController();
    setExpanded('');
    fetchPublishedRelationships(definitionId, recordId, relation, offset, controller.signal).then(
      page => { if (live(controller.signal, identity)) setRead({ key, page }); },
      error => { if (live(controller.signal, identity) && error?.code !== 'ERR_CANCELED') setRead({ key, error: ontologyError(error, 'Unable to read relationships.') }); },
    );
    return () => controller.abort();
  }, [key, definitionId, recordId, relation, offset, version]);
  const current = read?.key === key ? read : undefined;
  const loading = !current;
  const page = current?.page ?? read?.page;
  const selected = page?.relations.find(item => item.id + ':' + item.direction === page.selected);
  useExplorerErrorToast(current?.error, 'Unable to read relationships', () => reload(value => value + 1));
  if (current?.error) return null;
  if (!page) return <p role="status">Loading relationships…</p>;
  return <ExplorerRelationshipsView loading={loading} selectedId={page.selected}
    relations={page.relations.map(item => ({ id: item.id + ':' + item.direction, name: item.name, direction: item.direction }))}
    records={selected ? page.items.map(record => ({ key: record.id!,
      link: <ExplorerRecordLink definitionId={selected.targetDefinitionId} identity={{ $recordId: record.id! }}>{String(record.title ?? 'Untitled object')}</ExplorerRecordLink>,
      properties: <LinkedProperties definitionId={selected.targetDefinitionId} record={record} /> })) : []}
    expanded={expanded} onToggle={key => setExpanded(value => value === key ? '' : key)}
    onSelect={id => { setRelation(id); setOffset(0); }} offset={offset} hasMore={page.hasMore} pageLimit={100_000}
    onPage={offset => { setRelation(page.selected); setOffset(offset); }} />;
}

export default function PublishedExplorerRelationships(props: Props) {
  return <RelationshipPage key={JSON.stringify([props.definitionId, props.recordId, getSessionState().identity])} {...props} />;
}

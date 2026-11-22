import { useEffect, useState } from 'react';
import { fetchPublishedRelationships, unlinkPublishedRelationship, type PublishedRelationships } from '../../services/publishedExplorerDetails';
import { fetchPublishedOntologyType, ontologyError, type OntologyRecord, type PublishedOntologyType } from '../../services/ontology';
import { publishedLayoutPath, readLayouts, type Layouts } from '../../services/explorerLayouts';
import { publishedColumnTypes, publishedPresentation } from '../../services/publishedExplorerPresentation';
import { explorerFields } from './ExplorerObjectOverview';
import ExplorerRecordLink from './ExplorerRecordLink';
import { ExplorerValueProvider } from './ExplorerValue';
import ExplorerRelationshipsView, { ExplorerLinkedProperties } from './ExplorerRelationshipsView';
import useExplorerErrorToast from './useExplorerErrorToast';
import { getSessionState } from '../../services/session';
import SemanticTypeGlyph from './SemanticTypeGlyph';

const live = (signal: AbortSignal, identity: string) => {
  const session = getSessionState();
  return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
};

function LinkedProperties({ definitionId, record, type, layouts }: { definitionId: string; record: OntologyRecord; type: PublishedOntologyType; layouts: Layouts }) {
  const row = { key: record.id!, record: record.properties, identity: { $recordId: record.id! }, title: String(record.title ?? 'Untitled object'), columnTypes: publishedColumnTypes(type) };
  const layout = layouts.personal.layout ?? layouts.default.layout;
  const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
  return <ExplorerValueProvider source={{ kind: 'published', definitionId }}>
    <ExplorerLinkedProperties row={row} fields={explorerFields(row, Object.keys(record.properties), publishedPresentation(type), presentations)} />
  </ExplorerValueProvider>;
}

type Props = { definitionId: string; recordId: string };
function RelationshipPage({ definitionId, recordId }: Props) {
  const [relation, setRelation] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [version, reload] = useState(0);
  const [read, setRead] = useState<{ key: string; page?: PublishedRelationships; type?: PublishedOntologyType; layouts?: Layouts; error?: string }>();
  const [expanded, setExpanded] = useState('');
  const identity = getSessionState().identity;
  const key = JSON.stringify([definitionId, recordId, relation, offset, version, identity]);
  useEffect(() => {
    const controller = new AbortController();
    setExpanded('');
    fetchPublishedRelationships(definitionId, recordId, relation, offset, controller.signal).then(async page => {
      if (!live(controller.signal, identity)) return;
      const target = page.relations.find(item => item.id + ':' + item.direction === page.selected);
      if (!target || !page.items.length) return { key, page };
      const [type, layouts] = await Promise.all([fetchPublishedOntologyType(target.targetDefinitionId, controller.signal), readLayouts(publishedLayoutPath(target.targetDefinitionId), controller.signal)]);
      return { key, page, type, layouts };
    }).then(value => { if (value && live(controller.signal, identity)) setRead(value); },
      error => { if (live(controller.signal, identity) && error?.code !== 'ERR_CANCELED') setRead({ key, error: ontologyError(error, 'Unable to read relationships.') }); });
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
    records={selected ? page.items.map(record => ({ key: record.linkId, typeName: read!.type!.definition.name, icon: <SemanticTypeGlyph definition={read!.type!.definition} />,
      link: <ExplorerRecordLink definitionId={selected.targetDefinitionId} identity={{ $recordId: record.id! }}>{String(record.title ?? 'Untitled object')}</ExplorerRecordLink>,
      properties: <LinkedProperties definitionId={selected.targetDefinitionId} record={record} type={read!.type!} layouts={read!.layouts!} />,
      unlink: record.canUnlink ? {
        label: String(record.title ?? 'Untitled object'),
        description: <>Remove the “{selected.name}” relationship between this object and <strong>{String(record.title ?? 'Untitled object')}</strong>? Both objects remain available.</>,
        run: async signal => {
          await unlinkPublishedRelationship(selected, recordId, record, signal);
          if (live(signal, identity)) { setExpanded(''); setOffset(0); reload(value => value + 1); }
        },
      } : undefined,
    })) : []}
    expanded={expanded} onToggle={key => setExpanded(value => value === key ? '' : key)}
    onSelect={id => { setRelation(id); setOffset(0); }} offset={offset} hasMore={page.hasMore} pageLimit={100_000}
    onPage={offset => { setRelation(page.selected); setOffset(offset); }} />;
}

export default function PublishedExplorerRelationships(props: Props) {
  return <RelationshipPage key={JSON.stringify([props.definitionId, props.recordId, getSessionState().identity])} {...props} />;
}

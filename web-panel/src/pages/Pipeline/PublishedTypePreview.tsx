import { useEffect, useState } from 'react';
import { fetchPublishedOntologyType, ontologyError, type PublishedCatalogItem, type PublishedOntologyType } from '../../services/ontology';
import { graphRequest, type LinkType, type Page } from '../../services/ontology_graph';
import { getSessionState } from '../../services/session';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import ObjectTypePreview from './ObjectTypePreview';

type Props = {
  id: string; initial?: PublishedCatalogItem; allProperties: boolean;
  onPropertiesToggle: () => void; onClose: () => void; onExplore: () => void; onSelect: (id: string) => void;
};
type LinkedType = { object: PublishedOntologyType; relationships: string[] };

async function readLinkedTypes(object: PublishedOntologyType, signal: AbortSignal): Promise<LinkedType[]> {
  const links: LinkType[] = [];
  let hasMore = true;
  while (hasMore) {
    const page = await graphRequest<Page<LinkType>>('get', '/object-types/' + encodeURIComponent(object.id) + '/link-types', { params: { limit: 100, offset: links.length }, signal });
    links.push(...page.items); hasMore = page.hasMore;
  }
  const targets = [...new Set(links.map(link => link.sourceDefinitionId === object.id ? link.targetDefinitionId : link.sourceDefinitionId))];
  return Promise.all(targets.map(async id => ({
    object: id === object.id ? object : await fetchPublishedOntologyType(id, signal),
    relationships: links.filter(link => link.sourceDefinitionId === id || link.targetDefinitionId === id).map(link => link.name),
  })));
}

export default function PublishedTypePreview({ id, initial, allProperties, onPropertiesToggle, onClose, onExplore, onSelect }: Props) {
  const [version, reload] = useState(0);
  const [linkedVersion, reloadLinks] = useState(0);
  const [read, setRead] = useState<{ key: string; object?: PublishedOntologyType; error?: string }>();
  const [linkedRead, setLinkedRead] = useState<{ key: string; items?: LinkedType[]; error?: string }>();
  const identity = getSessionState().identity;
  const key = JSON.stringify([identity, id, version]);
  const current = read?.key === key ? read : undefined;
  const object = current?.error ? undefined : current?.object ?? (initial?.id === id ? initial : undefined);
  const linkedKey = JSON.stringify([key, linkedVersion]);
  const linked = linkedRead?.key === linkedKey ? linkedRead : undefined;
  const verified = !!current?.object;
  useEffect(() => {
    const controller = new AbortController();
    fetchPublishedOntologyType(id, controller.signal).then(
      object => { if (!controller.signal.aborted && identity === getSessionState().identity) setRead({ key, object }); },
      error => { if (!controller.signal.aborted && identity === getSessionState().identity) setRead({ key, error: ontologyError(error, 'Unable to load this object type.') }); },
    );
    return () => controller.abort();
  }, [id, key, identity]);
  useEffect(() => {
    if (!current?.object) return;
    const controller = new AbortController();
    const request = current.object.definition.source.mode === 'none' ? readLinkedTypes(current.object, controller.signal) : Promise.resolve([]);
    request.then(
      items => { if (!controller.signal.aborted && identity === getSessionState().identity) setLinkedRead({ key: linkedKey, items }); },
      error => { if (!controller.signal.aborted && identity === getSessionState().identity) setLinkedRead({ key: linkedKey, error: ontologyError(error, 'Unable to load linked object types.') }); },
    );
    return () => controller.abort();
  }, [current?.object, linkedKey, identity]);
  const definition = object?.definition;
  const properties = definition?.properties ?? [];
  return <ObjectTypePreview open id={id} name={definition?.name || 'Object type preview'}
    icon={definition && <SemanticTypeGlyph definition={definition} />} testId="published-type-inspector"
    loading={!definition && !current?.error} busy={!current} error={current?.error} onRetry={() => reload(value => value + 1)}
    onClose={onClose} onExplore={onExplore} exploreDisabled={!verified} description={definition?.description}
    summary={definition && [
      { label: 'API ID', value: definition.id },
      { label: 'Datasource', value: definition.source.mode === 'existing' ? `${definition.source.schema}.${definition.source.table}` : 'Object-managed records' },
      { label: 'Published revision', value: object!.revision },
    ]}
    properties={properties.map(property => ({
      id: property.id, name: property.name, type: property.type, required: property.required,
      primaryKey: definition!.primaryKey.includes(property.id), title: definition!.titleKey === property.id,
    }))} allProperties={allProperties} onPropertiesToggle={onPropertiesToggle}
    linkedTypes={{ loading: !linked, error: linked?.error, onRetry: () => reloadLinks(value => value + 1), items: linked?.items?.map(item => ({
      id: item.object.id, name: item.object.definition.name, tooltip: item.relationships.join(' · '),
      icon: <SemanticTypeGlyph definition={item.object.definition} />, iconStyle: { background: 'transparent' },
      onSelect: () => {
        setRead({ key: JSON.stringify([identity, item.object.id, version]), object: item.object });
        onSelect(item.object.id);
      },
    })) }} />;
}

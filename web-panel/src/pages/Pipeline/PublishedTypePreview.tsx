import { useEffect, useId, useState } from 'react';
import { Drawer } from '@/components/ui';
import { ArrowRightOutlined, CloseOutlined, ReloadOutlined, RightOutlined } from '@/components/ui/icons';
import { fetchPublishedOntologyType, ontologyError, type PublishedCatalogItem, type PublishedOntologyType } from '../../services/ontology';
import { graphRequest, type LinkType, type Page } from '../../services/ontology_graph';
import { getSessionState } from '../../services/session';
import PropertyTypeIcon from './PropertyTypeIcon';
import SemanticTypeGlyph from './SemanticTypeGlyph';

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
  const propertiesId = useId();
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
  const visibleProperties = allProperties ? properties : properties.slice(0, 6);
  return <Drawer open onClose={onClose} width="100%" title={`${definition?.name || id} Object type preview`} closable={false} destroyOnHidden rootClassName="flow-ontology flow-ontology__drawer"
    styles={{ header: { display: 'none' }, body: { display: 'flex', padding: 0, overflow: 'hidden' } }}>
    <aside className="flow-ontology__inspector" data-testid="published-type-inspector" data-object-id={id}>
      <header className="flow-ontology__inspector-toolbar">
        {definition && <SemanticTypeGlyph definition={definition} />}
        <div className="flow-ontology__preview-identity"><h2 title={definition?.name || id}>{definition?.name || 'Object type preview'}</h2><span>Object Type</span></div>
        <button type="button" className="flow-ontology__icon-button" aria-label="Close asset preview" onClick={onClose}><CloseOutlined /></button>
      </header>
      <div className="flow-ontology__inspector-content" aria-busy={!current}>
        {current?.error ? <div className="flow-ontology__notice" role="alert"><span>{current.error}</span><button type="button" onClick={() => reload(value => value + 1)}>Retry</button></div>
          : !definition ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>Loading object type</span></div> : <>
            <section className="flow-ontology__preview-section"><h3>Description</h3><p className="flow-ontology__muted">{definition.description || 'No description provided.'}</p></section>
            <section className="flow-ontology__preview-section" aria-label="Object type summary">
              <h3>API ID</h3><p>{definition.id}</p><h3>Datasource</h3><p>{definition.source.mode === 'existing' ? `${definition.source.schema}.${definition.source.table}` : 'Object-managed records'}</p><h3>Published revision</h3><p>{object!.revision}</p>
            </section>
            <section className="flow-ontology__preview-section"><header><h3>Properties <small>({properties.length.toLocaleString('en-US')})</small></h3>
              {properties.length > 6 && <button type="button" className="flow-ontology__properties-toggle" aria-expanded={allProperties} aria-controls={propertiesId} onClick={onPropertiesToggle}>{allProperties ? <><RightOutlined rotate={180} /><span>Back</span></> : <><span>View</span><RightOutlined /></>}</button>}
            </header><div id={propertiesId} className="flow-ontology__property-list" role="region" aria-label="Properties" tabIndex={allProperties && properties.length > 6 ? 0 : undefined}>
              {visibleProperties.map(property => <div className="flow-ontology__property-row" key={property.id} title={`${property.id} · ${property.type}${property.required ? ' · Required' : ' · Nullable'}`}>
                <PropertyTypeIcon type={property.type} /><span className="flow-ontology__property-name">{property.name}</span>
                {definition.titleKey === property.id && <span className="flow-ontology__property-badge is-title">Title</span>}
                {definition.primaryKey.includes(property.id) && <span className="flow-ontology__property-badge is-primary">Primary key</span>}
              </div>)}
            </div>{!properties.length && <p className="flow-ontology__muted">No properties defined.</p>}</section>
            <section className="flow-ontology__preview-section"><header><h3>Linked object types {linked?.items && <small>({linked.items.length.toLocaleString('en-US')})</small>}</h3></header>
              {linked?.error ? <div className="flow-ontology__notice" role="alert"><span>{linked.error}</span><button type="button" onClick={() => reloadLinks(value => value + 1)}>Retry linked types</button></div>
                : !linked?.items ? <p role="status">Loading linked object types…</p>
                  : linked.items.length ? <div className="flow-ontology__linked-list">{linked.items.map(item => <button type="button" key={item.object.id} className="flow-ontology__linked-row" title={item.relationships.join(' · ')} onClick={() => {
                    setRead({ key: JSON.stringify([identity, item.object.id, version]), object: item.object });
                    onSelect(item.object.id);
                  }}>
                    <SemanticTypeGlyph definition={item.object.definition} /><span>{item.object.definition.name}</span>
                  </button>)}</div> : <p className="flow-ontology__muted">No relationships defined.</p>}
            </section>
          </>}
      </div>
      <footer className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" disabled={!verified} onClick={onExplore}>Start exploring <ArrowRightOutlined /></button></footer>
    </aside>
  </Drawer>;
}

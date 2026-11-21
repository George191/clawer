import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui';
import { AppstoreOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import { listPublishedOntologyTypes, ontologyError as describe, type PublishedOntologyPage } from '../../services/ontology';
import { PublishedTypeActions } from './PublishedTypeActions';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import OntologyCatalogSkeleton from './OntologyCatalogSkeleton';
import './ontology-create.css';
import './published-results.css';

const pageSize = 25;
const maxOffset = 100_000;
export type PublishedCatalogPage = PublishedOntologyPage & { offset: number; keyword: string; identity: string; includeHidden: boolean; includeDeleted: boolean };
type Props = { view: 'grid' | 'list'; includeHidden: boolean; includeDeleted: boolean; managedProjects: string[]; catalogPage?: PublishedCatalogPage; onCatalogPage: (page?: PublishedCatalogPage) => void; onSelect: (id: string) => void; onClearSearch: () => void };

export default function PublishedOntology({ view, includeHidden, includeDeleted, managedProjects, catalogPage, onCatalogPage, onSelect, onClearSearch }: Props) {
  const [params, setParams] = useSearchParams();
  const keyword = (params.get('publishedQ') || '').trim();
  const requestedOffset = Number(params.get('publishedOffset'));
  const catalogOffset = Number.isInteger(requestedOffset) && requestedOffset >= 0 && requestedOffset <= maxOffset ? requestedOffset : 0;
  const sameCatalogQuery = catalogPage?.keyword === keyword && catalogPage.offset === catalogOffset;
  const sameFilters = catalogPage?.includeHidden === includeHidden && catalogPage.includeDeleted === includeDeleted;
  const [catalogError, setCatalogError] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  const reload = () => setVersion(value => value + 1);
  const changeCatalogOffset = (value: number) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set('publishedOffset', String(value)); else next.delete('publishedOffset');
    return next;
  });
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const request = new AbortController();
    setCatalogLoading(true); setCatalogError('');
    const timer = window.setTimeout(() => {
      listPublishedOntologyTypes(pageSize, catalogOffset, keyword, undefined, request.signal, includeHidden, includeDeleted)
        .then(data => {
          if (!request.signal.aborted && current()) {
            onCatalogPage({ ...data, offset: catalogOffset, keyword, identity, includeHidden, includeDeleted });
          }
        }, failure => {
          if (!request.signal.aborted && current()) {
            setCatalogError(describe(failure, 'Unable to load published object types.'));
          }
        })
        .finally(() => { if (!request.signal.aborted && current()) setCatalogLoading(false); });
    }, keyword ? 250 : 0);
    return () => { request.abort(); window.clearTimeout(timer); };
  }, [catalogOffset, keyword, version, identity, onCatalogPage, includeHidden, includeDeleted]);
  const loading = catalogLoading || (!catalogError && (!catalogPage || !sameCatalogQuery || !sameFilters));
  return <main className="flow-ontology__published flow-foundry-workbench flow-published-catalog" data-testid="published-ontology" aria-label="Published object types" aria-busy={loading}>
    <header className="flow-ontology__published-header"><h2>Published object types</h2></header>
    <section className="flow-create-section">
      {catalogError && <p className="flow-create-error" role="alert">{catalogError}<Button size="small" icon={<ReloadOutlined />} onClick={reload}>Retry</Button></p>}
      {loading && !catalogPage && <OntologyCatalogSkeleton view={view} label="Loading published object types" />}
      {catalogPage && <>
        {!!catalogPage.items.length && view === 'grid' && <div className="flow-published-catalog-scroll flow-published-catalog-cards" role="region" aria-label="Published type cards" ref={element => { if (element) element.inert = loading; }}>
          <div className="flow-ontology__assets">
            {catalogPage.items.map(item => <article className="flow-ontology__asset" key={item.id}>
              <button type="button" className="flow-ontology__asset-main" data-published-type-link aria-label={item.definition.name} disabled={item.deleted} onClick={() => onSelect(item.id)}>
                <SemanticTypeGlyph definition={item.definition} />
                <span className="flow-ontology__asset-name">
                  <span className="flow-ontology__asset-heading"><strong title={item.definition.name}>{item.definition.name}</strong>{(item.hidden || item.deleted) && <small className="flow-ontology__asset-visibility">{item.deleted ? 'Deleted' : 'Hidden'}</small>}</span>
                  <span className="flow-ontology__asset-description" title={item.definition.description || item.definition.id}>{item.definition.description || item.definition.id}</span>
                </span>
              </button>
              {managedProjects.includes(item.definition.projectId) && <PublishedTypeActions item={item} onChanged={reload} />}
            </article>)}
          </div>
        </div>}
        {!!catalogPage.items.length && view === 'list' && <div className="flow-published-catalog-scroll" role="region" aria-label="Published types table" tabIndex={0} ref={element => { if (element) element.inert = loading; }}>
          <table className="flow-published-catalog-table">
            <thead><tr><th scope="col">Object type</th><th scope="col">API ID</th><th scope="col">Datasource</th><th scope="col">Revision</th>{managedProjects.length > 0 && <th scope="col" className="flow-published-actions-heading"><span className="flow-published-sr-only">Manage type</span></th>}</tr></thead>
            <tbody>{catalogPage.items.map(item => <tr key={item.id} onClick={() => { if (!item.deleted) onSelect(item.id); }}>
              <th scope="row"><div className="flow-published-catalog-identity"><SemanticTypeGlyph definition={item.definition} /><div><button type="button" data-published-type-link disabled={item.deleted} onClick={event => { event.stopPropagation(); onSelect(item.id); }} title={item.definition.name}>{item.definition.name}</button>{(item.deleted || item.hidden) && <small>{item.deleted ? 'Deleted' : 'Hidden'}</small>}{item.definition.description && <small title={item.definition.description}>{item.definition.description}</small>}</div></div></th>
              <td><code title={item.definition.id}>{item.definition.id}</code></td>
              <td>{item.definition.source.mode === 'existing' ? <code title={`${item.definition.source.schema}.${item.definition.source.table}`}>{item.definition.source.schema}.{item.definition.source.table}</code> : 'Object-managed records'}</td>
              <td>{item.revision}</td>{managedProjects.length > 0 && <td>{managedProjects.includes(item.definition.projectId) && <PublishedTypeActions item={item} onChanged={reload} />}</td>}
            </tr>)}</tbody>
          </table>
        </div>}
        {!catalogPage.items.length && <div className="flow-published-catalog-empty">
          {catalogPage.keyword ? <SearchOutlined /> : <AppstoreOutlined />}
          <h3>{catalogPage.offset ? 'No published object types on this page.' : catalogPage.keyword ? 'No published object types match your search.' : 'No published object types are available to this account.'}</h3>
          {catalogPage.keyword ? <Button size="small" onClick={onClearSearch}>Clear search</Button> : !catalogPage.offset && <p>Object types appear here after publication when your project grants you access.</p>}
        </div>}
        {(catalogPage.offset > 0 || catalogPage.hasMore) && <footer className="flow-published-results-pagination" aria-label="Published object types pagination">
          <span>{catalogPage.items.length ? `${catalogPage.offset + 1}–${catalogPage.offset + catalogPage.items.length} shown` : '0 shown'}</span>
          <div><Button size="small" disabled={loading || !catalogPage.offset} onClick={() => changeCatalogOffset(Math.max(0, catalogPage.offset - pageSize))}>Previous page</Button><Button size="small" disabled={loading || !catalogPage.hasMore || catalogPage.offset + pageSize > maxOffset} onClick={() => changeCatalogOffset(catalogPage.offset + pageSize)}>Next page</Button></div>
        </footer>}
      </>}
    </section>
  </main>;
}

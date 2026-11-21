import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Checkbox, Input, Modal } from '@/components/ui';
import { AppstoreOutlined, ArrowLeftOutlined, BarsOutlined, CloseOutlined, DatabaseOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  fetchPublishedOntologyType, listPublishedOntologyTypes, listOntologyRecords, listOntologyRecordHistory, ontologyError as describe, submitOntologyAction, undoOntologyEdit,
  type PublishedOntologyType, type PublishedOntologyPage, type SemanticAction, type OntologyRecord, type OntologyRecordPage, type OntologyRecordHistoryPage,
} from '../../services/ontology';
import OntologyGraph from './OntologyGraph';
import OntologyFunctions from './OntologyFunctions';
import OntologyRevisions from './OntologyRevisions';
import OntologySdkDownload from './OntologySdkDownload';
import OntologyTransactions from './OntologyTransactions';
import { parsePropertyValue } from './ontologyPropertyValue';
import { PublishedTypeActions } from './PublishedTypeActions';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import OntologyCatalogSkeleton from './OntologyCatalogSkeleton';
import './ontology-create.css';
import './published-results.css';
import './published-tools.css';
import { publishedExplorerHref, publishedRecordIdentity, type PublishedIdentity } from '../../services/publishedExplorerHandoff';
import { queryPublishedExplorer } from '../../services/publishedExplorer';

const pageSize = 25;
const maxOffset = 100_000;
type RecordQuery = { deleted: boolean; offset: number };
type PublishedRecordPage = Omit<OntologyRecordPage, 'items'> & { items: (OntologyRecord & { identity?: PublishedIdentity | null })[] };
type ActionInputs = Record<string, { text: string; isNull: boolean }>;
type Operation = { kind: SemanticAction['kind']; fields: string[]; record?: OntologyRecord; initialInputs: ActionInputs };
export type PublishedCatalogPage = PublishedOntologyPage & { offset: number; keyword: string; identity: string; includeHidden: boolean; includeDeleted: boolean };
type Props = { selection: string; view: 'grid' | 'list'; includeHidden: boolean; includeDeleted: boolean; managedProjects: string[]; catalogPage?: PublishedCatalogPage; onCatalogPage: (page?: PublishedCatalogPage) => void; onSelect: (id: string) => void; onClearSearch: () => void };
function show(value: unknown): string {
  if (value == null) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function RecordHistory({ objectType, recordId, onChanged }: { objectType: string; recordId: string; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<OntologyRecordHistoryPage>();
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [undoing, setUndoing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const identity = getSessionState().identity;
    const current = () => !controller.signal.aborted && identity === getSessionState().identity;
    setPage(undefined); setError('');
    listOntologyRecordHistory(objectType, recordId, pageSize, offset, controller.signal)
      .then(result => { if (current()) setPage(result); }, failure => { if (current()) setError(describe(failure, 'Unable to load this record’s action history.')); });
    return () => controller.abort();
  }, [objectType, recordId, offset, version]);

  return <section className="flow-published-selection-properties" aria-label="Record action history">
    <h4>Action history</h4>
    {error ? <p role="alert">{error} <Button size="small" onClick={() => setVersion(value => value + 1)}>Retry</Button></p>
      : !page ? <p role="status">Loading action history…</p>
        : <>
          {page.items.length ? <dl>{page.items.map(event => <div key={event.revision}>
            <dt>{{ create: 'Created', modify: 'Modified', delete: 'Deleted' }[event.kind]} · Revision {event.revision}</dt>
            <dd><time dateTime={event.createdAt}>{new Date(event.createdAt).toLocaleString()}</time>
              {event.changes && <ul>{Object.entries(event.changes).map(([field,delta]) => <li key={field}>
                {field}: {Object.prototype.hasOwnProperty.call(delta,'before') ? JSON.stringify(delta.before) : 'Absent'} → {Object.prototype.hasOwnProperty.call(delta,'after') ? JSON.stringify(delta.after) : 'Absent'}
              </li>)}</ul>}
              {event.canUndo && <Button disabled={undoing} size="small" onClick={async () => {
                setUndoing(true);
                try {await undoOntologyEdit(objectType,recordId,event.revision);onChanged();}
                catch (e) {setError(describe(e,'Unable to undo this edit.'));}
                finally {setUndoing(false);}
              }}>Undo edit</Button>}</dd>
          </div>)}</dl> : <p>No action history on this page.</p>}
          {(offset > 0 || page.hasMore) && <footer className="flow-published-results-pagination" aria-label="Action history pagination"><div>
            <Button size="small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous page</Button>
            <Button size="small" disabled={!page.hasMore || offset + pageSize > maxOffset} onClick={() => setOffset(offset + pageSize)}>Next page</Button>
          </div></footer>}
        </>}
  </section>;
}

export default function PublishedOntology({ selection, view, includeHidden, includeDeleted, managedProjects, catalogPage: retainedPage, onCatalogPage, onSelect, onClearSearch }: Props) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const search = params.get('publishedQ') || '';
  const keyword = search.trim();
  const requestedOffset = Number(params.get('publishedOffset'));
  const catalogOffset = Number.isInteger(requestedOffset) && requestedOffset >= 0 && requestedOffset <= maxOffset ? requestedOffset : 0;
  const sameCatalogQuery = retainedPage?.keyword === keyword && retainedPage.offset === catalogOffset;
  const sameFilters = retainedPage?.includeHidden === includeHidden && retainedPage.includeDeleted === includeDeleted;
  const catalogPage = retainedPage;
  const [metadata, setMetadata] = useState<{ version: number; object?: PublishedOntologyType; error?: string }>();
  const [query, setQuery] = useState<RecordQuery>({ deleted: false, offset: 0 });
  const { deleted, offset } = query;
  const [recordRead, setRecordRead] = useState<{ query: RecordQuery; version: number; page?: PublishedRecordPage; error?: string }>();
  const [chosenRecord, setChosenRecord] = useState<OntologyRecord>();
  const recordTitles = useRef<Record<number, HTMLButtonElement | null>>({});
  const [previewTab, setPreviewTab] = useState<'overview' | 'properties' | 'history'>('overview');
  const [catalogError, setCatalogError] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [tool, setTool] = useState('');
  const [functionResult, setFunctionResult] = useState('');
  const [operation, setOperation] = useState<Operation>();
  const [inputs, setInputs] = useState<ActionInputs>({});
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState('');
  const actionLocked = useRef(false);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  const reload = () => setVersion(value => value + 1);
  const reloadCatalog = () => { reload(); };
  const changeCatalogOffset = (value: number) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set('publishedOffset', String(value)); else next.delete('publishedOffset');
    return next;
  });
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (selection) return;
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
  }, [selection, catalogOffset, keyword, version, identity, onCatalogPage, includeHidden, includeDeleted]);

  const object = metadata?.object;
  const pageRead = recordRead?.query === query && recordRead.version === version ? recordRead : undefined;
  const records = pageRead?.page ?? recordRead?.page;
  const metadataError = metadata?.version === version ? metadata.error : undefined;
  const loading = selection ? !metadataError && !pageRead : catalogLoading || (!catalogError && (!catalogPage || !sameCatalogQuery || !sameFilters));
  const error = selection ? metadataError : catalogError;
  useEffect(() => {
    if (!selection) return;
    const request = new AbortController();
    fetchPublishedOntologyType(selection, request.signal).then(data => {
      if (!request.signal.aborted && current()) setMetadata({ version, object: data });
    }, failure => {
      if (!request.signal.aborted && current()) setMetadata({ version, error: describe(failure, 'Unable to load this published object type.') });
    });
    return () => request.abort();
  }, [selection, version, identity]);
  useEffect(() => {
    if (!selection || metadata?.version !== version || !object) return;
    const request = new AbortController();
    const read = query.deleted
      ? listOntologyRecords(selection, pageSize, query.offset, object.definition.source.mode === 'none', request.signal)
      : queryPublishedExplorer(selection, { filters: [] }, pageSize, query.offset, request.signal);
    read.then(page => {
      if (!request.signal.aborted && current()) setRecordRead({ query, version, page });
    }, failure => {
      if (!request.signal.aborted && current()) setRecordRead({ query, version, error: describe(failure, 'Unable to load records.') });
    });
    return () => request.abort();
  }, [selection, query, metadata, object, version, identity]);
  const changePage = (nextOffset: number, nextDeleted = deleted) => {
    setChosenRecord(undefined);
    setPreviewTab('overview');
    setQuery({ offset: nextOffset, deleted: nextDeleted });
  };

  const startAction = (kind: SemanticAction['kind'], fields: string[], record?: OntologyRecord) => {
    setActionError('');
    const initialInputs = Object.fromEntries(fields.filter(id => record && Object.prototype.hasOwnProperty.call(record.properties, id)).map(id => {
      const val = record!.properties[id];
      const type = object?.definition.properties.find(prop => prop.id === id)?.type;
      return [id, { text: val == null ? '' : type === 'json' ? JSON.stringify(val) : String(val), isNull: val === null }];
    }));
    setInputs(initialInputs);
    setOperation({ kind, fields, record, initialInputs });
  };
  const applyAction = async () => {
    if (!current() || !selection || !operation || !object || actionLocked.current) return;
    actionLocked.current = true; setActionPending(true); setActionError('');
    try {
      const properties = object.definition.properties;
      const values: Record<string, unknown> = {};
      for (const id of operation.fields) {
        const item = properties.find(p => p.id === id);
        if (!item) throw new Error('The selected property is unavailable.');
        const input = inputs[id];
        if (!input) { if (operation.kind === 'create' && item.required) throw new Error(item.name + ' is required.'); continue; }
        const initial = operation.initialInputs[id];
        if (initial && input.text === initial.text && input.isNull === initial.isNull) continue;
        try { values[id] = input.isNull ? null : parsePropertyValue(input.text, item.type); }
        catch (failure) { throw new Error(item.name + ': ' + (failure instanceof Error ? failure.message : 'invalid value')); }
      }
      const record = operation.record;
      if (operation.kind !== 'create' && (!record?.id || record.revision == null)) throw new Error('This record cannot be changed.');
      if (operation.kind === 'modify' && !Object.keys(values).length) throw new Error('Change at least one property.');
      await submitOntologyAction(selection, operation.kind,
        operation.kind === 'create' ? { values } : operation.kind === 'modify' ? { recordId: record!.id!, expectedRevision: record!.revision!, values } : { recordId: record!.id!, expectedRevision: record!.revision! });
      if (current()) { setOperation(undefined); if (offset) changePage(0); reload(); }
    } catch (failure) {
      if (current()) setActionError(describe(failure, failure instanceof Error ? failure.message : 'The action failed.'));
    } finally { actionLocked.current = false; if (current()) setActionPending(false); }
  };
  const available = records?.actions || [];
  const createAction = available.find(item => item.kind === 'create');
  const selectedRow = chosenRecord && records ? records.items.findIndex(record => chosenRecord.id ? record.id === chosenRecord.id : record === chosenRecord) : -1;
  const selectedRecord = loading || selectedRow < 0 ? undefined : records?.items[selectedRow];
  useEffect(() => {
    if (records && chosenRecord && !selectedRecord) setChosenRecord(undefined);
  }, [records, chosenRecord, selectedRecord]);
  const properties = object?.definition.properties ?? [];
  const resultColumns = properties.filter(prop => prop.id !== object?.definition.titleKey);
  const visibleSelectedProperties = selectedRecord
    ? properties.filter(prop => Object.prototype.hasOwnProperty.call(selectedRecord.properties, prop.id))
    : [];
  const keyProperties = visibleSelectedProperties.filter(prop => object?.definition.primaryKey.includes(prop.id));
  const summaryProperties = [...keyProperties, ...visibleSelectedProperties.filter(prop => !keyProperties.includes(prop))]
    .filter(prop => selectedRecord?.properties[prop.id] != null).slice(0, 4);
  const chooseRow = (index: number) => {
    if (selectedRow !== index) setPreviewTab('overview');
    setChosenRecord(records?.items[index]);
  };
  const closePreview = () => {
    if (selectedRow >= 0) recordTitles.current[selectedRow]?.focus();
    setChosenRecord(undefined);
  };
  const recordActions = !deleted && selectedRecord?.id ? available.filter(action => action.kind !== 'create') : [];
  const returnToTypes = () => { if (!actionLocked.current) onSelect(''); };
  return <>
    <main className={`flow-ontology__published flow-foundry-workbench ${selection ? 'flow-ontology__published--results' : 'flow-published-catalog'}`} data-testid="published-ontology" aria-label={selection ? 'Published object results' : 'Published object types'} aria-busy={loading}>
      <header className="flow-ontology__published-header">
        {selection ? <div className="flow-published-results-context">
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={returnToTypes}>Back to published types</Button>
          {object && <div className="flow-published-type-title"><SemanticTypeGlyph definition={object.definition} /><h2>{object.definition.name}</h2><code>{object.definition.id}</code></div>}
        </div> : <h2>Published object types</h2>}
      </header>
        <section className="flow-create-section">
          {error && <p className="flow-create-error" role="alert">{error}<Button size="small" icon={<ReloadOutlined />} onClick={reload}>Retry</Button></p>}
          {loading && selection && !object && <p role="status">Loading published object type…</p>}
          {loading && !selection && !catalogPage && <OntologyCatalogSkeleton view={view} label="Loading published object types" />}
          {!selection && catalogPage && <>
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
                  {managedProjects.includes(item.definition.projectId) && <PublishedTypeActions item={item} onChanged={reloadCatalog} />}
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
                  <td>{item.revision}</td>{managedProjects.length > 0 && <td>{managedProjects.includes(item.definition.projectId) && <PublishedTypeActions item={item} onChanged={reloadCatalog} />}</td>}
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
          {object && <div className="flow-published-results">
            <div className="flow-published-results-toolbar">
              <h3><BarsOutlined /> Results</h3>
              {object.definition.source.mode === 'none' && <div className="flow-published-status-filter" role="group" aria-label="Object status">
                <button type="button" aria-label="Show active objects" aria-pressed={!deleted} onClick={() => { if (deleted) changePage(0, false); }}>Active</button>
                <button type="button" aria-label="Show deleted objects" aria-pressed={deleted} onClick={() => { if (!deleted) changePage(0, true); }}>Deleted</button>
              </div>}
              {selectedRecord && <Button size="small" icon={<CloseOutlined />} onClick={closePreview}>1 selected</Button>}
              <div className="flow-published-results-toolbar-end">
                <span title={object.definition.description || undefined}>{object.definition.source.mode === 'existing' ? `${object.definition.source.schema}.${object.definition.source.table} · Read only` : 'Object-managed records'} · Revision {object.revision}</span>
                {!deleted && <Button size="small" disabled={loading} onClick={() => navigate(publishedExplorerHref(object.id, { returnTo: '/ontology?' + params.toString() }))}>Open in Object Explorer</Button>}
                {!deleted && selectedRecord && publishedRecordIdentity(selectedRecord) && <Button size="small" disabled={loading} onClick={() => navigate(publishedExplorerHref(object.id, { query: { filters: [], keys: [publishedRecordIdentity(selectedRecord)!] }, returnTo: '/ontology?' + params.toString() }))}>Open selected in Object Explorer</Button>}
                {!deleted && createAction && <Button size="small" type="primary" onClick={() => startAction('create', createAction.fields)}>Create object</Button>}
              </div>
            </div>
            <nav className="flow-ontology-tools" aria-label="Object type tools">
              {(object.definition.source.mode === 'none' ? ['Relationships and sets','Transactions','Functions','Model revisions'] : ['Model revisions']).map(label =>
                <Button key={label} type="text" size="small" className="flow-published-tool-tab" aria-pressed={tool === label} onClick={() => {if(tool === label){setTool('');reload();}else setTool(label);}}>{label}</Button>)}
              <OntologySdkDownload key={object.id} definitionId={object.id} />
            </nav>
            {tool && <div className="flow-ontology-tool-content">
              {tool === 'Relationships and sets' && <OntologyGraph key={object.id} objectType={object} recordId={selectedRecord?.id || undefined} />}
              {tool === 'Transactions' && <OntologyTransactions key={object.id} objectType={object} />}
              {tool === 'Functions' && <>
                {functionResult && <p role="status">{functionResult}</p>}
                <OntologyFunctions key={object.id} id={object.id} record={deleted ? undefined : selectedRecord}
                  onStarted={() => setFunctionResult('')} onCommitted={name => {setFunctionResult('Committed: '+name);reload();}} />
              </>}
              {tool === 'Model revisions' && <OntologyRevisions key={object.id} definitionId={object.id} onMerged={reload} />}
            </div>}
            <div className={`flow-published-results-body${selectedRecord ? ' has-selection' : ''}`}>
              <div className="flow-published-record-pane">
                <div className="flow-published-record-scroll" tabIndex={0} role="region" aria-label="Object results table" aria-busy={loading} ref={element => { if (element) element.inert = loading; }}>
                  <table style={{ width: `max(100%, ${276 + resultColumns.length * 164}px)` }}>
                    <colgroup><col className="flow-published-select-column" /><col className="flow-published-title-column" />
                      {resultColumns.map(prop => <col key={prop.id} />)}</colgroup>
                    <thead><tr><th scope="col"><span className="flow-published-sr-only">Select</span></th><th scope="col">Title</th>{resultColumns.map(prop => <th scope="col" key={prop.id} title={prop.name}>{prop.name}</th>)}</tr></thead>
                    <tbody>
                      {records?.items.map((record, index) => <tr key={record.id ?? index} className={selectedRow === index ? 'is-selected' : undefined} onClick={() => chooseRow(index)}>
                        <td><input type="checkbox" aria-label={`Select ${show(record.title)}`} checked={selectedRow === index} onClick={event => event.stopPropagation()} onChange={event => event.target.checked ? chooseRow(index) : setChosenRecord(undefined)} /></td>
                        <th scope="row"><button type="button" className="flow-published-record-title" ref={element => { recordTitles.current[index] = element; }} title={show(record.title)} onClick={event => { event.stopPropagation(); chooseRow(index); }}><SemanticTypeGlyph definition={object.definition} /><span>{show(record.title)}</span></button></th>
                        {resultColumns.map(prop => {
                          const value = Object.prototype.hasOwnProperty.call(record.properties, prop.id) ? show(record.properties[prop.id]).slice(0, 120) : '—';
                          return <td key={prop.id} title={value}>{value}</td>;
                        })}
                      </tr>)}
                      {!records?.items.length && <tr><td className="flow-published-results-empty" colSpan={resultColumns.length + 2}>
                        {loading ? <span role="status">Loading {deleted ? 'deleted' : 'active'} objects…</span> :
                          pageRead?.error ? <span role="alert">{pageRead.error} <Button size="small" onClick={() => changePage(offset)}>Retry</Button></span> :
                          deleted ? 'No deleted objects are available to this account.' : 'No records are available to this account.'}
                      </td></tr>}
                    </tbody>
                  </table>
                </div>
                <footer className="flow-published-results-pagination">
                  <span>{records?.items.length ? `${offset + 1}–${offset + records.items.length} shown` : records ? '0 shown' : ''}</span>
                  {(offset > 0 || records?.hasMore) && <div><Button size="small" disabled={!offset || loading} onClick={() => changePage(Math.max(0, offset - pageSize))}>Previous page</Button><Button size="small" disabled={!records?.hasMore || offset + pageSize > maxOffset || loading} onClick={() => changePage(offset + pageSize)}>Next page</Button></div>}
                </footer>
              </div>
              {selectedRecord && <aside className="flow-published-selection" aria-label="Selection Preview">
                <header><BarsOutlined /><h3>Selection Preview</h3><Button size="small" type="text" aria-label="Close selection preview" icon={<CloseOutlined />} onClick={closePreview} /></header>
                <div className="flow-published-selection-content">
                  <div className="flow-published-selection-title"><SemanticTypeGlyph definition={object.definition} size="preview" /><div><span>{object.definition.name}</span><h4>{show(selectedRecord.title)}</h4></div>
                    {!!recordActions.length && <details key={selectedRow} className="flow-published-record-actions"><summary>Actions</summary><div>{recordActions.map(action => <Button key={action.kind} size="small" onClick={() => startAction(action.kind, action.fields, selectedRecord)}>{action.kind === 'modify' ? 'Modify' : 'Delete'}</Button>)}</div></details>}
                  </div>
                  <nav className="flow-published-selection-tabs" aria-label="Selection view">
                    <button type="button" aria-pressed={previewTab === 'overview'} onClick={() => setPreviewTab('overview')}>Overview</button>
                    <button type="button" aria-pressed={previewTab === 'properties'} onClick={() => setPreviewTab('properties')}>Properties</button>
                    {selectedRecord.id && object.definition.source.mode === 'none' && <button type="button" aria-pressed={previewTab === 'history'} onClick={() => setPreviewTab('history')}>History</button>}
                  </nav>
                  {previewTab === 'overview' && !!summaryProperties.length && <dl className="flow-published-selection-summary">{summaryProperties.map(prop => <div key={prop.id}><dt>{prop.name}</dt><dd>{show(selectedRecord.properties[prop.id])}</dd></div>)}</dl>}
                  {previewTab === 'history' && selectedRecord.id ? <RecordHistory key={selectedRecord.id} objectType={selection} recordId={selectedRecord.id} onChanged={reload} /> : <section className="flow-published-selection-properties">
                    <h4><DatabaseOutlined />{previewTab === 'overview' ? 'Key properties' : 'Properties'}</h4>
                    <dl>{(previewTab === 'overview' ? visibleSelectedProperties.slice(0, 8) : visibleSelectedProperties).map(prop => <div key={prop.id}><dt>{prop.name}</dt><dd>{selectedRecord.properties[prop.id] == null ? <i>No value</i> : show(selectedRecord.properties[prop.id])}</dd></div>)}</dl>
                    {!visibleSelectedProperties.length && <p>No properties are available for this record.</p>}
                  </section>}
                </div>
              </aside>}
            </div>
          </div>}
        </section>
    </main>
      {operation && <Modal open centered width={520} className="flow-surface-modal flow-published-dialog" title={operation.kind === 'create' ? 'Create object' : operation.kind === 'modify' ? 'Modify object' : 'Delete object'} okText={operation.kind === 'delete' ? 'Delete object' : 'Submit action'} onOk={applyAction} onCancel={() => { if (!actionLocked.current) setOperation(undefined); }} confirmLoading={actionPending} maskClosable={!actionPending} closable={!actionPending} keyboard={!actionPending}>
      {actionError && <p role="alert" className="flow-create-error">{actionError}</p>}
      {operation.kind === 'delete' && <p>Delete this object record? The source table, if any, is not modified.</p>}
      {operation.fields.map(id => {
        const property = object?.definition.properties.find(item => item.id === id);
        const input = inputs[id];
        return <div key={id} className="flow-semantic-config"><label className="flow-create-field"><span>{property?.name || id} ({property?.type || 'value'})</span><Input aria-label={property?.name || id} value={input?.text || ''} disabled={actionPending || input?.isNull} onChange={event => { const text = event.target.value; setInputs(prev => ({ ...prev, [id]: { text, isNull: false } })); }} /></label>
          {!property?.required && <Checkbox checked={!!input?.isNull} disabled={actionPending} onChange={event => { const isNull = event.target.checked; setInputs(prev => ({ ...prev, [id]: { text: prev[id]?.text || '', isNull } })); }}>Set to null</Checkbox>}</div>;
      })}
    </Modal>}
  </>;
}

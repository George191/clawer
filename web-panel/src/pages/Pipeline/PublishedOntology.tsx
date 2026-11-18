import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button, Checkbox, Input, Modal, Table } from '@/components/ui';
import { ArrowLeftOutlined, BarsOutlined, CloseOutlined, DatabaseOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  fetchPublishedOntologyType, listPublishedOntologyTypes, listOntologyRecords, listOntologyRecordHistory, ontologyError as describe, submitOntologyAction, undoOntologyEdit,
  type PublishedOntologyType, type PublishedOntologyPage, type SemanticAction, type OntologyRecord, type OntologyRecordPage, type OntologyRecordHistoryPage,
} from '../../services/ontology';
import OntologyGraph from './OntologyGraph';
import OntologyRevisions from './OntologyRevisions';
import OntologyTransactions from './OntologyTransactions';
import { parsePropertyValue } from './ontologyPropertyValue';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import './ontology-create.css';
import './published-results.css';

const pageSize = 25;
const maxOffset = 100_000;
type ActionInputs = Record<string, { text: string; isNull: boolean }>;
type Operation = { kind: SemanticAction['kind']; fields: string[]; record?: OntologyRecord; initialInputs: ActionInputs };
type Props = { selection: string; onSelect: (id: string) => void };
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

export default function PublishedOntology({ selection, onSelect }: Props) {
  const [params, setParams] = useSearchParams();
  const search = params.get('publishedQ') || '';
  const keyword = search.trim();
  const requestedOffset = Number(params.get('publishedOffset'));
  const catalogOffset = Number.isInteger(requestedOffset) && requestedOffset >= 0 && requestedOffset <= maxOffset ? requestedOffset : 0;
  const [catalogPage, setCatalogPage] = useState<PublishedOntologyPage>();
  const [object, setObject] = useState<PublishedOntologyType>();
  const [deleted, setDeleted] = useState(false);
  const [records, setRecords] = useState<OntologyRecordPage>();
  const [selectedRow, setSelectedRow] = useState<number>();
  const recordTitles = useRef<Record<number, HTMLButtonElement | null>>({});
  const [previewTab, setPreviewTab] = useState<'overview' | 'properties' | 'history'>('overview');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [tool, setTool] = useState('');
  const [operation, setOperation] = useState<Operation>();
  const [inputs, setInputs] = useState<ActionInputs>({});
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState('');
  const actionLocked = useRef(false);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  const reload = () => setVersion(value => value + 1);
  const changeCatalogSearch = (value: string) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set('publishedQ', value); else next.delete('publishedQ');
    next.delete('publishedOffset');
    return next;
  }, { replace: true });
  const changeCatalogOffset = (value: number) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set('publishedOffset', String(value)); else next.delete('publishedOffset');
    return next;
  });
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setObject(undefined); setRecords(undefined); setSelectedRow(undefined); setPreviewTab('overview');
    if (!selection) {
      setCatalogPage(undefined);
      const timer = window.setTimeout(() => {
        listPublishedOntologyTypes(pageSize, catalogOffset, keyword)
          .then(data => { if (alive && current()) setCatalogPage(data); }, failure => { if (alive && current()) setError(describe(failure, 'Unable to load published object types.')); })
          .finally(() => { if (alive && current()) setLoading(false); });
      }, keyword ? 250 : 0);
      return () => { alive = false; window.clearTimeout(timer); };
    } else {
      fetchPublishedOntologyType(selection).then(async data => {
        if (!alive || !current()) return;
        setObject(data);
        const page = await listOntologyRecords(selection, pageSize, offset, deleted && data.definition.source.mode === 'none');
        if (alive && current()) setRecords(page);
      }).catch(failure => { if (alive && current()) setError(describe(failure, 'Unable to load this published object type or its permitted records.')); })
        .finally(() => { if (alive && current()) setLoading(false); });
    }
    return () => { alive = false; };
  }, [selection, offset, catalogOffset, keyword, version, identity, deleted]);

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
      if (current()) { setOperation(undefined); setOffset(0); reload(); }
    } catch (failure) {
      if (current()) setActionError(describe(failure, failure instanceof Error ? failure.message : 'The action failed.'));
    } finally { actionLocked.current = false; if (current()) setActionPending(false); }
  };
  const available = records?.actions || [];
  const createAction = available.find(item => item.kind === 'create');
  const selectedRecord = selectedRow == null ? undefined : records?.items[selectedRow];
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
    setSelectedRow(index);
  };
  const closePreview = () => {
    if (selectedRow != null) recordTitles.current[selectedRow]?.focus();
    setSelectedRow(undefined);
  };
  const recordActions = selectedRecord?.id ? available.filter(action => action.kind !== 'create') : [];
  useEffect(() => {setTool('');setDeleted(false);}, [selection]);
  const returnToTypes = () => { if (!actionLocked.current) { setOffset(0); setSelectedRow(undefined); onSelect(''); } };
  return <>
    <main className={`flow-ontology__published flow-foundry-workbench${selection ? ' flow-ontology__published--results' : ''}`} data-testid="published-ontology" aria-label={selection ? 'Published object results' : 'Published object types'} aria-busy={loading}>
      <header className="flow-ontology__published-header">
        {selection ? <div className="flow-published-results-context">
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={returnToTypes}>Back to published types</Button>
          {object && <div className="flow-published-type-title"><SemanticTypeGlyph definition={object.definition} /><h2>{object.definition.name}</h2><code>{object.definition.id}</code></div>}
        </div> : <><h2>Published object types</h2><p>Browse published definitions and access the records and actions available to your account.</p></>}
      </header>
        <section className="flow-create-section">
          {!selection && <div className="flow-ontology__published-search"><Input size="small" prefix={<SearchOutlined />} aria-label="Search published types" placeholder="Search published types" value={search} maxLength={200} onChange={event => changeCatalogSearch(event.target.value)} allowClear /></div>}
          {error && <p className="flow-create-error" role="alert">{error}<Button size="small" icon={<ReloadOutlined />} onClick={reload}>Retry</Button></p>}
          {loading && <p role="status">{selection ? 'Loading published object type and records…' : 'Loading published object types…'}</p>}
          {!selection && !loading && catalogPage && <>
            <Table<PublishedOntologyType> className="flow-create-table flow-create-review-table" size="small" rowKey="id" pagination={false} dataSource={catalogPage.items} locale={{ emptyText: catalogOffset ? 'No published object types on this page.' : keyword ? 'No published object types match your search.' : 'No published object types are available to this account.' }} columns={[
            { title: 'Object type', render: (_, item) => <Button type="link" onClick={() => onSelect(item.id)}><SemanticTypeGlyph definition={item.definition} />{item.definition.name}</Button> },
            { title: 'API ID', render: (_, item) => item.definition.id },
            { title: 'Datasource', render: (_, item) => item.definition.source.mode === 'existing' ? item.definition.source.schema + '.' + item.definition.source.table : 'No backing table' },
            { title: 'Revision', dataIndex: 'revision' },
            ]} />
            {(catalogOffset > 0 || catalogPage.hasMore) && <footer className="flow-published-results-pagination" aria-label="Published object types pagination">
              <span>{catalogPage.items.length ? `${catalogOffset + 1}–${catalogOffset + catalogPage.items.length} shown` : '0 shown'}</span>
              <div><Button size="small" disabled={!catalogOffset} onClick={() => changeCatalogOffset(Math.max(0, catalogOffset - pageSize))}>Previous page</Button><Button size="small" disabled={!catalogPage.hasMore || catalogOffset + pageSize > maxOffset} onClick={() => changeCatalogOffset(catalogOffset + pageSize)}>Next page</Button></div>
            </footer>}
          </>}
          {object && records && <div className="flow-published-results">
            <div className="flow-published-results-toolbar">
              <h3><BarsOutlined /> {deleted ? 'Deleted objects' : 'Results'}</h3>
              {object.definition.source.mode === 'none' && <Button size="small" onClick={() => {setOffset(0);setDeleted(!deleted);}}>{deleted ? 'Show active objects' : 'Show deleted objects'}</Button>}
              {selectedRecord && <Button size="small" icon={<CloseOutlined />} onClick={closePreview}>1 selected</Button>}
              <div className="flow-published-results-toolbar-end">
                <span title={object.definition.description || undefined}>{object.definition.source.mode === 'existing' ? `${object.definition.source.schema}.${object.definition.source.table} · Read only` : 'Object-managed records'} · Revision {object.revision}</span>
                {createAction && <Button size="small" type="primary" onClick={() => startAction('create', createAction.fields)}>Create object</Button>}
              </div>
            </div>
            <nav className="flow-ontology-tools" aria-label="Object type tools">
              {(object.definition.source.mode === 'none' ? ['Relationships and sets','Transactions','Model revisions'] : ['Model revisions']).map(label =>
                <Button key={label} size="small" aria-pressed={tool === label} onClick={() => {if(tool === label){setTool('');reload();}else setTool(label);}}>{label}</Button>)}
            </nav>
            {tool && <div className="flow-ontology-tool-content">
              {tool === 'Relationships and sets' && <OntologyGraph key={object.id} objectType={object} recordId={selectedRecord?.id || undefined} />}
              {tool === 'Transactions' && <OntologyTransactions key={object.id} objectType={object} />}
              {tool === 'Model revisions' && <OntologyRevisions key={object.id} definitionId={object.id} onMerged={reload} />}
            </div>}
            <div className={`flow-published-results-body${selectedRecord ? ' has-selection' : ''}`}>
              <div className="flow-published-record-pane">
                <div className="flow-published-record-scroll" tabIndex={0} role="region" aria-label="Object results table">
                  <table>
                    <thead><tr><th scope="col"><span className="flow-published-sr-only">Select</span></th><th scope="col">Title</th>{resultColumns.map(prop => <th scope="col" key={prop.id}>{prop.name}</th>)}</tr></thead>
                    <tbody>
                      {records.items.map((record, index) => <tr key={record.id ?? index} className={selectedRow === index ? 'is-selected' : undefined} onClick={() => chooseRow(index)}>
                        <td><input type="checkbox" aria-label={`Select ${show(record.title)}`} checked={selectedRow === index} onClick={event => event.stopPropagation()} onChange={event => event.target.checked ? chooseRow(index) : setSelectedRow(undefined)} /></td>
                        <th scope="row"><button type="button" className="flow-published-record-title" ref={element => { recordTitles.current[index] = element; }} title={show(record.title)} onClick={event => { event.stopPropagation(); chooseRow(index); }}><SemanticTypeGlyph definition={object.definition} /><span>{show(record.title)}</span></button></th>
                        {resultColumns.map(prop => {
                          const value = Object.prototype.hasOwnProperty.call(record.properties, prop.id) ? show(record.properties[prop.id]).slice(0, 120) : '—';
                          return <td key={prop.id} title={value}>{value}</td>;
                        })}
                      </tr>)}
                      {!records.items.length && <tr><td className="flow-published-results-empty" colSpan={resultColumns.length + 2}>No records are available to this account.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <footer className="flow-published-results-pagination">
                  <span>{records.items.length ? `${offset + 1}–${offset + records.items.length} shown` : '0 shown'}</span>
                  {(offset > 0 || records.hasMore) && <div><Button size="small" disabled={!offset || loading} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>Previous page</Button><Button size="small" disabled={!records.hasMore || loading} onClick={() => setOffset(value => value + pageSize)}>Next page</Button></div>}
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
    {operation && <Modal open centered width={520} title={operation.kind === 'create' ? 'Create object' : operation.kind === 'modify' ? 'Modify object' : 'Delete object'} okText={operation.kind === 'delete' ? 'Delete object' : 'Submit action'} onOk={applyAction} onCancel={() => { if (!actionLocked.current) setOperation(undefined); }} confirmLoading={actionPending} maskClosable={!actionPending} closable={!actionPending} keyboard={!actionPending}>
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

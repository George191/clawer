import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, message, type MenuItem } from '@/components/ui';
import { BranchesOutlined, CodeOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, HistoryOutlined, PlusOutlined, SyncOutlined, UndoOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  listOntologyRecords, listOntologyRecordHistory, ontologyError as describe, submitOntologyAction, undoOntologyEdit,
  type OntologyRecord, type OntologyRecordHistoryPage, type OntologyRecordPage, type PublishedOntologyType, type SemanticAction,
} from '../../services/ontology';
import type { ExplorerToolAction } from './ExplorerWorkbenchHeader';
import OntologyGraph from './OntologyGraph';
import OntologyFunctions from './OntologyFunctions';
import OntologyRevisions from './OntologyRevisions';
import OntologySdkDownload from './OntologySdkDownload';
import OntologyTransactions from './OntologyTransactions';
import { parsePropertyValue } from './ontologyPropertyValue';
import './published-tools.css';
import './published-results.css';
import './explorer-history.css';

const pageSize = 25;
const maxOffset = 100_000;
type ActionInputs = Record<string, { text: string; isNull: boolean }>;
type Operation = { kind: SemanticAction['kind']; fields: string[]; record?: OntologyRecord; initialInputs: ActionInputs };
type Props = { objectType?: PublishedOntologyType; actions: NonNullable<OntologyRecordPage['actions']>; record?: OntologyRecord; returnTo: string; onChanged: () => void; busy?: boolean };
const title = (value: unknown) => value == null ? 'Untitled object' : typeof value === 'object' ? JSON.stringify(value) : String(value);

function historyValue(delta: { before?: unknown; after?: unknown }, side: 'before' | 'after') {
  if (!Object.prototype.hasOwnProperty.call(delta, side)) return <span className="explorer-history-unset">Not set</span>;
  const value = delta[side];
  return value === null ? <span className="explorer-history-unset">null</span>
    : typeof value === 'object' ? <code>{JSON.stringify(value, null, 2)}</code> : <span>{String(value)}</span>;
}

function RecordHistory({ objectType, recordId, onChanged }: { objectType: string; recordId: string; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<OntologyRecordHistoryPage>();
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [undoing, setUndoing] = useState(false);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setPage(undefined); setError('');
    listOntologyRecordHistory(objectType, recordId, pageSize, offset, controller.signal).then(
      result => { if (!controller.signal.aborted && current()) setPage(result); },
      failure => { if (!controller.signal.aborted && current()) setError(describe(failure, 'Unable to load this record’s action history.')); },
    );
    return () => controller.abort();
  }, [objectType, recordId, offset, version, identity]);
  return <section className="explorer-history" aria-label="Record action history">
    <header><HistoryOutlined /><h4>Edit history</h4></header>
    {error ? <p role="alert">{error} <Button size="small" onClick={() => setVersion(value => value + 1)}>Retry</Button></p>
      : !page ? <p role="status">Loading action history…</p> : <>
        {page.items.length ? <ol className="explorer-history-events">{page.items.map(event => <li key={event.revision}>
          <span className="explorer-history-marker" aria-hidden="true">{event.kind === 'modify' ? <EditOutlined /> : event.kind === 'delete' ? <DeleteOutlined /> : <PlusOutlined />}</span>
          <details open><summary><span><strong>{{ create: 'Created', modify: 'Modified', delete: 'Deleted' }[event.kind]}</strong><small>Revision {event.revision}</small></span>
            <time dateTime={event.createdAt}>{new Date(event.createdAt).toLocaleString()}</time></summary>
            {event.changes && Object.keys(event.changes).length ? <div className="explorer-history-changes"><table><thead><tr><th>Property</th><th>Previous value</th><th>Updated value</th></tr></thead>
              <tbody>{Object.entries(event.changes).map(([field, delta]) => <tr key={field}><th scope="row">{field}</th><td>{historyValue(delta, 'before')}</td><td>{historyValue(delta, 'after')}</td></tr>)}</tbody></table></div>
              : <p>{event.changes ? 'No readable property changes.' : 'Property changes are restricted by the current access policy.'}</p>}
            {event.canUndo && <footer><Button icon={<UndoOutlined />} disabled={undoing} size="small" onClick={async () => {
              if (!current() || undoing) return;
              setUndoing(true);
              try { await undoOntologyEdit(objectType, recordId, event.revision); if (current()) { message.success('Edit undone.'); setVersion(value => value + 1); onChanged(); } }
              catch (failure) { if (current()) message.error(describe(failure, 'Unable to undo this edit.')); }
              finally { if (current()) setUndoing(false); }
            }}>Undo edit</Button></footer>}
          </details>
        </li>)}</ol> : <p>No action history on this page.</p>}
        {(offset > 0 || page.hasMore) && <footer className="flow-published-results-pagination" aria-label="Action history pagination"><div>
          <Button size="small" disabled={!offset || undoing} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous page</Button>
          <Button size="small" disabled={!page.hasMore || offset + pageSize > maxOffset || undoing} onClick={() => setOffset(offset + pageSize)}>Next page</Button>
        </div></footer>}
      </>}
  </section>;
}

function DeletedObjects({ objectType, onChanged }: { objectType: PublishedOntologyType; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [page, setPage] = useState<OntologyRecordPage>();
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<OntologyRecord>();
  useEffect(() => {
    const controller = new AbortController();
    const identity = getSessionState().identity;
    const current = () => !controller.signal.aborted && identity === getSessionState().identity;
    setPage(undefined); setError('');
    listOntologyRecords(objectType.id, pageSize, offset, true, controller.signal).then(
      result => { if (current()) setPage(result); },
      failure => { if (current()) setError(describe(failure, 'Unable to load deleted objects.')); },
    );
    return () => controller.abort();
  }, [objectType.id, offset, version]);
  return <section className="flow-tool-section" aria-label="Deleted objects">
    {error ? <p role="alert">{error} <Button size="small" onClick={() => setVersion(value => value + 1)}>Retry</Button></p>
      : !page ? <p role="status">Loading deleted objects…</p>
        : page.items.length ? <ul className="flow-published-deleted-objects">{page.items.map(record => <li key={record.id}>
          <Button size="small" type="text" aria-pressed={selected?.id === record.id} onClick={() => setSelected(record)}>{title(record.title)}</Button>
          <span>Revision {record.revision}</span>
        </li>)}</ul> : <p>No deleted objects are available to this account.</p>}
    {(offset > 0 || page?.hasMore) && <footer className="flow-published-results-pagination" aria-label="Deleted objects pagination"><div>
      <Button size="small" disabled={!offset || !page} onClick={() => { setSelected(undefined); setOffset(Math.max(0, offset - pageSize)); }}>Previous page</Button>
      <Button size="small" disabled={!page?.hasMore || offset + pageSize > maxOffset} onClick={() => { setSelected(undefined); setOffset(offset + pageSize); }}>Next page</Button>
    </div></footer>}
    {selected?.id && <RecordHistory key={selected.id} objectType={objectType.id} recordId={selected.id} onChanged={() => {
      setSelected(undefined); setVersion(value => value + 1); onChanged();
    }} />}
  </section>;
}

export function usePublishedObjectTools({ objectType, actions, record, returnTo, onChanged, busy = false }: Props) {
  const [tool, setTool] = useState('');
  const [sdkOpen, setSdkOpen] = useState(false);
  const [operation, setOperation] = useState<Operation>();
  const [inputs, setInputs] = useState<ActionInputs>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  const active = useRef(true);
  const typeId = useRef(objectType?.id);
  typeId.current = objectType?.id;
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const native = objectType?.definition.source.mode === 'none';
  const startAction = (kind: SemanticAction['kind'], fields: string[], selected?: OntologyRecord) => {
    if (!objectType || busy || locked.current || !current()) return;
    setError('');
    const initialInputs = Object.fromEntries(fields.filter(id => selected && Object.prototype.hasOwnProperty.call(selected.properties, id)).map(id => {
      const value = selected!.properties[id];
      const type = objectType.definition.properties.find(property => property.id === id)?.type;
      return [id, { text: value == null ? '' : type === 'json' ? JSON.stringify(value) : String(value), isNull: value === null }];
    }));
    setInputs(initialInputs); setOperation({ kind, fields, record: selected, initialInputs });
  };
  const applyAction = async () => {
    if (!objectType || !current() || !operation || locked.current) return;
    const definitionId = objectType.id;
    locked.current = true; setPending(true); setError('');
    try {
      const values: Record<string, unknown> = {};
      for (const id of operation.fields) {
        const property = objectType.definition.properties.find(item => item.id === id);
        if (!property) throw new Error('The selected property is unavailable.');
        const input = inputs[id];
        if (!input) { if (operation.kind === 'create' && property.required) throw new Error(property.name + ' is required.'); continue; }
        const initial = operation.initialInputs[id];
        if (initial && input.text === initial.text && input.isNull === initial.isNull) continue;
        try { values[id] = input.isNull ? null : parsePropertyValue(input.text, property.type); }
        catch (failure) { throw new Error(property.name + ': ' + (failure instanceof Error ? failure.message : 'invalid value')); }
      }
      const selected = operation.record;
      if (operation.kind !== 'create' && (!selected?.id || selected.revision == null)) throw new Error('This record cannot be changed.');
      if (operation.kind === 'modify' && !Object.keys(values).length) throw new Error('Change at least one property.');
      await submitOntologyAction(objectType.id, operation.kind,
        operation.kind === 'create' ? { values } : operation.kind === 'modify'
          ? { recordId: selected!.id!, expectedRevision: selected!.revision!, values }
          : { recordId: selected!.id!, expectedRevision: selected!.revision! });
      if (current() && typeId.current === definitionId) {
        message.success({ create: 'Object created.', modify: 'Object updated.', delete: 'Object deleted.' }[operation.kind]);
        setOperation(undefined); onChanged();
      }
    } catch (failure) { if (current()) {
      const detail = describe(failure, failure instanceof Error ? failure.message : 'The action failed.');
      setError(detail); message.error(detail);
    } }
    finally { locked.current = false; if (current()) setPending(false); }
  };
  const tools: ExplorerToolAction[] = objectType ? [
    ...(native ? [
      { key: 'relationships', label: 'Relationships and sets', icon: <BranchesOutlined />, group: 'analyze' as const, run: () => setTool('Relationships and sets') },
      { key: 'transactions', label: 'Transactions', icon: <SyncOutlined />, group: 'analyze' as const, run: () => setTool('Transactions') },
      { key: 'functions', label: 'Functions', icon: <CodeOutlined />, group: 'analyze' as const, run: () => setTool('Functions') },
      { key: 'deleted', label: 'Deleted objects', icon: <DeleteOutlined />, group: 'inspect' as const, run: () => setTool('Deleted objects') },
    ] : []),
    { key: 'revisions', label: 'Model revisions', icon: <HistoryOutlined />, group: 'inspect', run: () => setTool('Model revisions') },
    { key: 'sdk', label: 'TypeScript SDK', icon: <DownloadOutlined />, group: 'inspect', run: () => setSdkOpen(true) },
  ] : [];
  const create = native ? actions.find(action => action.kind === 'create') : undefined;
  const actionsForRecord = (selected?: OntologyRecord): MenuItem[] => native && selected?.id ? actions.filter(action => action.kind !== 'create').map(action => ({
    key: action.kind, label: action.kind === 'modify' ? 'Modify' : 'Delete', danger: action.kind === 'delete',
    icon: action.kind === 'modify' ? <EditOutlined /> : <DeleteOutlined />,
    disabled: busy || pending, onClick: () => startAction(action.kind, action.fields, selected),
  })) : [];
  const renderHistory = (selected?: OntologyRecord) => objectType && native && selected?.id
    ? <RecordHistory key={selected.id} objectType={objectType.id} recordId={selected.id} onChanged={onChanged} /> : null;
  return {
    tools,
    createButton: create ? <Button size="small" icon={<PlusOutlined />} disabled={busy || pending} onClick={() => startAction('create', create.fields)}>Create object</Button> : null,
    actionsForRecord, renderHistory,
    dialogs: objectType ? <>
      <Modal open={!!tool} centered width={1040} className="flow-surface-modal flow-published-dialog" title={tool} onCancel={() => setTool('')} footer={<Button onClick={() => setTool('')}>Close</Button>}>
        <div className="flow-ontology-tool-content">
          {tool === 'Relationships and sets' && <OntologyGraph key={objectType.id} objectType={objectType} recordId={record?.id || undefined} returnTo={returnTo} onOpenExplorer={() => setTool('')} />}
          {tool === 'Transactions' && <OntologyTransactions key={objectType.id} objectType={objectType} onCommitted={onChanged} />}
          {tool === 'Functions' && <OntologyFunctions key={objectType.id} id={objectType.id} record={record} onCommitted={name => { message.success('Committed: ' + name); onChanged(); }} />}
          {tool === 'Model revisions' && <OntologyRevisions key={objectType.id} definitionId={objectType.id} onMerged={onChanged} />}
          {tool === 'Deleted objects' && <DeletedObjects key={objectType.id} objectType={objectType} onChanged={onChanged} />}
        </div>
      </Modal>
      <OntologySdkDownload key={objectType.id} definitionId={objectType.id} open={sdkOpen} onClose={() => setSdkOpen(false)} />
      {operation && <Modal open centered width={520} className="flow-surface-modal flow-published-dialog" title={operation.kind === 'create' ? 'Create object' : operation.kind === 'modify' ? 'Modify object' : 'Delete object'} okText={operation.kind === 'delete' ? 'Delete object' : 'Submit action'} onOk={applyAction} onCancel={() => { if (!locked.current) setOperation(undefined); }} confirmLoading={pending} maskClosable={!pending} closable={!pending} keyboard={!pending}>
        {error && <p role="alert" className="flow-create-error">{error}</p>}
        {operation.kind === 'delete' && <p>Delete this object record? The source table, if any, is not modified.</p>}
        {operation.fields.map(id => {
          const property = objectType.definition.properties.find(item => item.id === id);
          const input = inputs[id];
          return <div key={id} className="flow-semantic-config"><label className="flow-create-field"><span>{property?.name || id} ({property?.type || 'value'})</span><Input aria-label={property?.name || id} value={input?.text || ''} disabled={pending || input?.isNull} onChange={event => { const text = event.target.value; setInputs(previous => ({ ...previous, [id]: { text, isNull: false } })); }} /></label>
            {!property?.required && <Checkbox checked={!!input?.isNull} disabled={pending} onChange={event => { const isNull = event.target.checked; setInputs(previous => ({ ...previous, [id]: { text: previous[id]?.text || '', isNull } })); }}>Set to null</Checkbox>}</div>;
        })}
      </Modal>}
    </> : null,
  };
}

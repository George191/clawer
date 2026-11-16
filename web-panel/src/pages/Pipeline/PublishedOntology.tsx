import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Table } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  fetchPublishedOntologyType, listPublishedOntologyTypes, listOntologyRecords, submitOntologyAction,
  type PublishedOntologyType, type SemanticAction, type OntologyRecord, type OntologyRecordPage,
} from '../../services/ontology';
import './ontology-create.css';

const pageSize = 25;
type Operation = { kind: SemanticAction['kind']; fields: string[]; record?: OntologyRecord };
type Props = { initialId?: string; onCancel: () => void };
function show(value: unknown): string {
  if (value == null) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function describe(error: unknown, fallback: string) {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}
function parse(value: string, type: string) {
  if (type === 'integer') {
    if (!/^-?\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('Enter a whole number within the safe integer range.');
    return Number(value);
  }
  if (type === 'number') {
    if (!value.trim() || !Number.isFinite(Number(value))) throw new Error('Enter a finite number.');
    return Number(value);
  }
  if (type === 'boolean') {
    if (value !== 'true' && value !== 'false') throw new Error('Enter true or false.');
    return value === 'true';
  }
  if (type === 'json') { try { return JSON.parse(value); } catch { throw new Error('Enter valid JSON.'); } }
  return value;
}

export default function PublishedOntology({ initialId = '', onCancel }: Props) {
  const [selection, setSelection] = useState(initialId);
  const [items, setItems] = useState<PublishedOntologyType[]>();
  const [object, setObject] = useState<PublishedOntologyType>();
  const [records, setRecords] = useState<OntologyRecordPage>();
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [operation, setOperation] = useState<Operation>();
  const [inputs, setInputs] = useState<Record<string, { text: string; isNull: boolean }>>({});
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState('');
  const actionLocked = useRef(false);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && identity === getSessionState().identity;
  const close = () => { if (!actionLocked.current) onCancel(); };
  const reload = () => setVersion(value => value + 1);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setObject(undefined); setRecords(undefined);
    if (!selection) {
      listPublishedOntologyTypes().then(data => { if (alive && current()) setItems(data); }, failure => { if (alive && current()) setError(describe(failure, 'Unable to load published object types.')); })
        .finally(() => { if (alive && current()) setLoading(false); });
    } else {
      fetchPublishedOntologyType(selection).then(async data => {
        if (!alive || !current()) return;
        setObject(data);
        const page = await listOntologyRecords(selection, pageSize, offset);
        if (alive && current()) setRecords(page);
      }).catch(failure => { if (alive && current()) setError(describe(failure, 'Unable to load this published object type or its permitted records.')); })
        .finally(() => { if (alive && current()) setLoading(false); });
    }
    return () => { alive = false; };
  }, [selection, offset, version, identity]);

  const startAction = (kind: SemanticAction['kind'], fields: string[], record?: OntologyRecord) => {
    setActionError('');
    setInputs(Object.fromEntries(fields.filter(id => record && Object.prototype.hasOwnProperty.call(record.properties, id)).map(id => {
      const val = record!.properties[id];
      return [id, { text: val == null ? '' : typeof val === 'object' ? JSON.stringify(val) : String(val), isNull: val === null }];
    })));
    setOperation({ kind, fields, record });
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
        try { values[id] = input.isNull ? null : parse(input.text, item.type); }
        catch (failure) { throw new Error(item.name + ': ' + (failure instanceof Error ? failure.message : 'invalid value')); }
      }
      const record = operation.record;
      if (operation.kind !== 'create' && (!record?.id || record.revision == null)) throw new Error('This record cannot be changed.');
      await submitOntologyAction(selection, operation.kind,
        operation.kind === 'create' ? { values } : operation.kind === 'modify' ? { recordId: record!.id!, expectedRevision: record!.revision!, values } : { recordId: record!.id!, expectedRevision: record!.revision! });
      if (current()) { setOperation(undefined); setOffset(0); reload(); }
    } catch (failure) {
      if (current()) setActionError(describe(failure, failure instanceof Error ? failure.message : 'The action failed.'));
    } finally { actionLocked.current = false; if (current()) setActionPending(false); }
  };
  const available = records?.actions || [];
  const createAction = available.find(item => item.kind === 'create');
  const select = (id: string) => { setOffset(0); setSelection(id); };
  return <>
    <Modal open centered className="flow-surface-modal flow-create-modal flow-create-object flow-published-ontology" width={1160} title="Published object types" onCancel={close} footer={<>
      <Button onClick={close}>Close</Button>{selection && <Button onClick={() => select('')}>Back to object types</Button>}
    </>}>
      <div className="flow-create-step">
        <section className="flow-create-section">
          {error && <p className="flow-create-error" role="alert">{error}<Button size="small" icon={<ReloadOutlined />} onClick={reload}>Retry</Button></p>}
          {loading && <p role="status">Loading permitted object types and records…</p>}
          {!selection && !loading && !error && <Table<PublishedOntologyType> className="flow-create-table flow-create-review-table" size="small" rowKey="id" pagination={false} dataSource={items || []} locale={{ emptyText: 'No published object types are available to this account.' }} columns={[
            { title: 'Object type', render: (_, item) => <Button type="link" onClick={() => select(item.id)}>{item.definition.name}</Button> },
            { title: 'API ID', render: (_, item) => item.definition.id },
            { title: 'Datasource', render: (_, item) => item.definition.source.mode === 'existing' ? item.definition.source.schema + '.' + item.definition.source.table : 'No backing table' },
            { title: 'Revision', dataIndex: 'revision' },
          ]} />}
          {object && <>
            <header className="flow-create-heading"><h3>{object.definition.name}</h3><p>{object.definition.description}</p></header>
            <dl className="flow-create-summary"><dt>API ID</dt><dd>{object.definition.id}</dd><dt>Datasource</dt><dd>{object.definition.source.mode === 'existing' ? object.definition.source.schema + '.' + object.definition.source.table : 'Object-managed records'}</dd><dt>Revision</dt><dd>{object.revision}</dd></dl>
            <div className="flow-create-property-heading"><strong>Visible properties</strong></div>
            <Table<PublishedOntologyType['definition']['properties'][number]> className="flow-create-table flow-create-review-table" size="small" rowKey="id" pagination={false} dataSource={object.definition.properties} columns={[
              { title: 'Property', dataIndex: 'name' }, { title: 'ID', dataIndex: 'id' }, { title: 'Type', dataIndex: 'type' },
              { title: 'Keys', render: (_, prop) => [object.definition.primaryKey.includes(prop.id) ? 'Primary key' : '', object.definition.titleKey === prop.id ? 'Title' : ''].filter(Boolean).join(' · ') || '—' },
            ]} />
            {records && <div className="flow-semantic-saved">
              <div className="flow-create-property-heading"><strong>Visible records</strong>{createAction && <Button size="small" type="primary" onClick={() => startAction('create', createAction.fields)}>Create object</Button>}</div>
              <Table<OntologyRecord> className="flow-create-table flow-create-review-table" size="small" rowKey={(record, index) => record.id ?? index ?? 0} pagination={false} dataSource={records.items} columns={[
                { title: 'Title', render: (_, record) => show(record.title) },
                ...object.definition.properties.map(prop => ({ title: prop.name, render: (_: unknown, record: OntologyRecord) => <span title={show(record.properties[prop.id])}>{show(record.properties[prop.id]).slice(0, 120)}</span> })),
                ...(available.some(action => action.kind !== 'create') ? [{ title: 'Actions', render: (_: unknown, record: OntologyRecord) => record.id && <div className="flow-semantic-buttons">{available.filter(action => action.kind !== 'create').map(action => <Button key={action.kind} size="small" onClick={() => startAction(action.kind, action.fields, record)}>{action.kind === 'modify' ? 'Modify' : 'Delete'}</Button>)}</div> }] : []),
              ]} />
              {(offset > 0 || records.hasMore) && <div className="flow-semantic-buttons flow-semantic-saved"><Button size="small" disabled={!offset || loading} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>Previous page</Button><Button size="small" disabled={!records.hasMore || loading} onClick={() => setOffset(value => value + pageSize)}>Next page</Button></div>}
            </div>}
          </>}
        </section>
      </div>
    </Modal>
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

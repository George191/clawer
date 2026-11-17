import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select, Table } from '@/components/ui';
import { ArrowDownOutlined, ArrowUpOutlined, CheckOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from '@/components/ui/icons';
import { createEtlTable, fetchLayerTables, fetchEtlObjectTypes, fetchEtlObjectPreview } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { LayerNode, LayerTable, EtlObjectType, EtlObjectPreview, EtlObjectProperty } from '../../services/types';

type Property = { id: number; name: string; type: string; length: string; nullable: boolean; primary: boolean; comment: string };
type Index = { id: number; name: string; type: string; unique: boolean; fields: string[]; comment: string };
type Relation = { id: number; name: string; targetId: string; sourceFields: string[]; targetFields: string[]; cardinality: string; description: string };
type Check = { id: number; name: string; expression: string; comment: string };
type Draft = { schema: string; name: string; dataType: string; description: string; fields: Property[]; indexes: Index[]; relations: Relation[]; checks: Check[] };
type Props = { variant?: 'object' | 'storage'; schemas: LayerNode[]; initialSchema?: string; onCancel: () => void; onOpenExisting: (object: EtlObjectPreview) => void; onCreated: (schema: string, table: LayerTable) => void };
const steps = ['Datasource', 'Metadata', 'Properties', 'Actions', 'Save location', 'Security'] as const;
const identifier = /^[a-z_][a-z0-9_]{0,62}$/;
const fieldTypes = ['TEXT', 'VARCHAR', 'CHAR', 'BIGINT', 'INTEGER', 'SMALLINT', 'BOOLEAN', 'TIMESTAMPTZ', 'TIMESTAMP', 'DATE', 'TIME', 'JSONB', 'JSON', 'UUID', 'NUMERIC', 'DECIMAL', 'REAL', 'DOUBLE PRECISION', 'BYTEA', 'SERIAL', 'BIGSERIAL'];
const lengthTypes = ['VARCHAR', 'CHAR', 'NUMERIC', 'DECIMAL'];
const relationIdentifier = /^[a-z_][a-z0-9_]{0,127}$/;
const cardinalities = [{ value: 'one_to_one', label: 'One to one' }, { value: 'one_to_many', label: 'One to many' }, { value: 'many_to_one', label: 'Many to one' }, { value: 'many_to_many', label: 'Many to many' }];
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };
const options = (values: string[]) => values.map((value) => ({ value, label: value }));
function errorMessage(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
  return typeof detail === 'string' ? detail : 'Unable to create the object type. Your changes are retained. Try again.';
}

export default function NewObjectType({ variant = 'object', schemas, initialSchema, onCancel, onOpenExisting, onCreated }: Props) {
  const schemaNames = schemas.map((schema) => schema.schemaName || schema.key);
  const [draft, setDraft] = useState<Draft>(() => ({ schema: initialSchema && schemaNames.includes(initialSchema) ? initialSchema : '', name: '', dataType: '', description: '', fields: [{ id: 0, name: 'record_id', type: 'TEXT', length: '', nullable: false, primary: true, comment: 'Business record identifier' }], indexes: [], relations: [], checks: [] }));
  const original = useRef(JSON.stringify(draft));
  const nextId = useRef(1);
  const [step, setStep] = useState(0);
  const stepPanel = useRef<HTMLDivElement>(null);
  const previousStep = useRef(step);
  const [sourceMode, setSourceMode] = useState<'new' | 'existing'>('new');
  const [existingSchema, setExistingSchema] = useState(draft.schema);
  const activeSchema = sourceMode === 'existing' ? existingSchema : draft.schema;
  const [existingTable, setExistingTable] = useState('');
  const [previewVersion, setPreviewVersion] = useState(0);
  const [existingPreview, setExistingPreview] = useState<{ key: string; data?: EtlObjectPreview; error?: string }>({ key: '' });
  const sourceKey = sourceMode === 'existing' && existingSchema && existingTable ? JSON.stringify([existingSchema, existingTable, previewVersion]) : '';
  const visiblePreview = sourceKey && existingPreview.key === sourceKey ? existingPreview : undefined;
  const preview = visiblePreview?.data;
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const completed = useRef(false);
  const discardModal = useRef<ReturnType<typeof Modal.confirm> | null>(null);
  const active = useRef(true);
  const sessionIdentity = useRef(getSessionState().identity).current;
  const [tableVersion, setTableVersion] = useState(0);
  const [tableState, setTableState] = useState<{ schema: string; loading: boolean; items: LayerTable[]; error: string }>({ schema: '', loading: false, items: [], error: '' });
  const [objectVersion, setObjectVersion] = useState(0);
  const [objectState, setObjectState] = useState<{ loading: boolean; items: EtlObjectType[]; error: string }>({ loading: true, items: [], error: '' });
  const [references, setReferences] = useState<Record<string, { loading: boolean; columns: string[]; error: string }>>({});
  const referenceRequests = useRef(new Map<string, number>());
  const dirty = JSON.stringify(draft) !== original.current;
  const tablesReady = tableState.schema === activeSchema && !tableState.loading && !tableState.error;
  const tables = tableState.schema === activeSchema ? tableState.items : [];
  const current = () => active.current && getSessionState().identity === sessionIdentity;
  const change = (values: Partial<Draft>) => { setDraft((value) => ({ ...value, ...values })); setError(''); };
  useEffect(() => { active.current = true; return () => { active.current = false; discardModal.current?.destroy(); }; }, []);
  useEffect(() => {
    if (previousStep.current === step) return;
    previousStep.current = step;
    const panel = stepPanel.current;
    if (panel) { panel.scrollTop = 0; panel.querySelector<HTMLElement>('h3')?.focus({ preventScroll: true }); }
  }, [step]);
  useEffect(() => {
    let valid = true;
    if (sourceKey) {
      fetchEtlObjectPreview(existingSchema, existingTable, true).then((data) => {
        if (valid && current()) setExistingPreview({ key: sourceKey, data });
      }).catch(() => {
        if (valid && current()) setExistingPreview({ key: sourceKey, error: 'Unable to load the existing object type.' });
      });
    }
    return () => { valid = false; };
  }, [sourceKey, existingSchema, existingTable, sessionIdentity]);
  useEffect(() => {
    if (!dirty && !pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty, pending]);
  useEffect(() => {
    let valid = true;
    setTableState({ schema: activeSchema, loading: !!activeSchema, items: [], error: '' });
    if (activeSchema) fetchLayerTables(activeSchema).then((items) => { if (valid && current()) setTableState({ schema: activeSchema, loading: false, items, error: '' }); }).catch(() => { if (valid && current()) setTableState({ schema: activeSchema, loading: false, items: [], error: 'Unable to load tables in this schema.' }); });
    return () => { valid = false; };
  }, [activeSchema, tableVersion, sessionIdentity]);
  useEffect(() => {
    let valid = true;
    setObjectState({ loading: true, items: [], error: '' });
    const load = async () => {
      const items: EtlObjectType[] = [];
      try {
        while (true) {
          const part = await fetchEtlObjectTypes({ limit: 500, offset: items.length, includeHidden: true });
          if (!valid || !current()) return;
          items.push(...part.items);
          if (items.length >= part.total) break;
          if (!part.items.length) throw new Error('Object catalog changed while loading');
        }
        setObjectState({ loading: false, items, error: '' });
      } catch { if (valid && current()) setObjectState({ loading: false, items: [], error: 'Unable to load ontology object types.' }); }
    };
    void load();
    return () => { valid = false; };
  }, [objectVersion, sessionIdentity]);
  const loadReference = async (id: string) => {
    const object = objectState.items.find((item) => item.id === id);
    if (!object) return;
    const request = (referenceRequests.current.get(id) || 0) + 1;
    referenceRequests.current.set(id, request);
    setReferences((value) => ({ ...value, [id]: { loading: true, columns: [], error: '' } }));
    try {
      const preview = await fetchEtlObjectPreview(object.schema, object.table, true);
      if (current() && referenceRequests.current.get(id) === request) setReferences((value) => ({ ...value, [id]: { loading: false, columns: preview.properties.map((property) => property.name), error: '' } }));
    } catch { if (current() && referenceRequests.current.get(id) === request) setReferences((value) => ({ ...value, [id]: { loading: false, columns: [], error: 'Unable to load target object properties.' } })); }
  };
  const close = () => {
    if (submitting.current || discardModal.current) return;
    if (!dirty || completed.current) { onCancel(); return; }
    discardModal.current = Modal.confirm({ className: 'flow-surface-modal flow-create-confirmation', title: 'Discard this object type?', content: 'Your object type has not been created. Unsaved changes will be lost.', okText: 'Discard changes', onCancel: () => { discardModal.current = null; }, onOk: () => { discardModal.current = null; if (current() && !submitting.current) onCancel(); } });
  };
  const updateProperty = (id: number, values: Partial<Property>) => change({ fields: draft.fields.map((field) => field.id === id ? { ...field, ...values } : field) });
  const moveProperty = (index: number, direction: number) => { const next = [...draft.fields], target = index + direction; if (target < 0 || target >= next.length) return; [next[index], next[target]] = [next[target], next[index]]; change({ fields: next }); };
  const propertyOptions = options(draft.fields.filter((field) => identifier.test(field.name)).map((field) => field.name));
  const validate = (stage: 'source' | 'metadata' | 'properties') => {
    if (!draft.schema || !schemaNames.includes(draft.schema)) return 'Choose an available schema.';
    if (!tablesReady) return 'Wait for the schema to load successfully before continuing.';
    if (stage === 'source') return '';
    if (!identifier.test(draft.name)) return 'Object type ID must use lowercase letters, numbers and underscores, begin with a letter or underscore, and contain at most 63 characters.';
    if (tables.some((table) => table.name === draft.name)) return 'An object type with this ID already exists in the selected schema.';
    if (!identifier.test(draft.dataType)) return 'Data type must begin with a lowercase letter or underscore and use at most 63 lowercase letters, numbers or underscores.';
    if (stage === 'metadata') return '';
    if (!draft.fields.length || draft.fields.length > 200) return 'Define between 1 and 200 properties.';
    if (draft.fields.some((field) => !identifier.test(field.name)) || new Set(draft.fields.map((field) => field.name)).size !== draft.fields.length) return 'Property names must be valid, distinct lowercase identifiers.';
    if (!draft.fields.some((field) => field.primary)) return 'Choose at least one primary key property.';
    if (draft.fields.some((field) => field.length && (!lengthTypes.includes(field.type) || !/^[1-9][0-9]{0,5}$/.test(field.length)))) return 'Property length must be 1–999999 and is supported only for VARCHAR, CHAR, NUMERIC or DECIMAL.';
    const known = new Set(draft.fields.map((field) => field.name));
    if (draft.indexes.some((item) => !identifier.test(item.name) || !item.fields.length || item.fields.some((field) => !known.has(field)) || item.unique && item.type !== 'BTREE')) return 'Each index needs a valid name, existing properties and a supported method. Unique indexes require BTREE.';
    if (new Set(draft.indexes.map((item) => item.name)).size !== draft.indexes.length) return 'Index names must be distinct.';
    if (draft.indexes.some((item) => new Set(item.fields).size !== item.fields.length || item.type === 'HASH' && item.fields.length !== 1)) return 'Index properties must be distinct. HASH indexes support exactly one property.';
    if (new Set(draft.indexes.map((item) => JSON.stringify([item.type, item.fields, item.unique]))).size !== draft.indexes.length) return 'Duplicate index definitions are not allowed. Choose a different method, ordered properties or uniqueness.';
    if (draft.relations.length && (objectState.loading || objectState.error)) return 'Load the ontology object catalog successfully before saving relations.';
    if (draft.relations.some((item) => !relationIdentifier.test(item.name) || !item.sourceFields.length || new Set(item.sourceFields).size !== item.sourceFields.length || item.sourceFields.some((field) => !known.has(field)) || !objectState.items.some((object) => object.id === item.targetId) || !item.targetFields.length || new Set(item.targetFields).size !== item.targetFields.length || item.sourceFields.length !== item.targetFields.length || !references[item.targetId] || references[item.targetId].loading || references[item.targetId].error || item.targetFields.some((field) => !references[item.targetId].columns.includes(field)))) return 'Each relation needs a name, an ontology object and matching, distinct source and target properties.';
    if (draft.relations.some((item) => item.description.length > 4000 || !cardinalities.some((value) => value.value === item.cardinality))) return 'Choose a relation cardinality and a description of at most 4000 characters.';
    if (new Set(draft.relations.map((item) => item.name)).size !== draft.relations.length) return 'Relation names must be distinct.';
    const mappings = draft.relations.map((item) => JSON.stringify([item.targetId, item.sourceFields.map((field, index) => [field, item.targetFields[index]]).sort()]));
    if (new Set(mappings).size !== mappings.length) return 'Duplicate relation property mappings are not allowed.';
    if (draft.checks.some((item) => !identifier.test(item.name) || !item.expression.trim() || item.expression.length > 4000)) return 'Each check needs a valid name and an expression of 1–4000 characters.';
    const constraints = draft.checks.map((item) => item.name);
    if (new Set(constraints).size !== constraints.length) return 'Check constraint names must be distinct.';
    if ([draft.indexes, draft.relations, draft.checks].some((items) => items.length > 200)) return 'A maximum of 200 entries is supported for each advanced constraint type.';
    return '';
  };
  const next = () => {
    const validation = validate(step === 0 ? 'source' : step === 1 ? 'metadata' : 'properties');
    if (validation) { setError(validation); return; }
    setError(''); setStep((value) => value + 1);
  };
  const openExisting = () => {
    if (!preview || !current()) return;
    if (!dirty) { onOpenExisting(preview); return; }
    if (discardModal.current) return;
    discardModal.current = Modal.confirm({
      className: 'flow-surface-modal flow-create-confirmation',
      title: 'Open an existing object type?',
      content: 'Your new object type draft will be discarded. The existing object type will not be changed.',
      okText: 'Discard and open',
      onCancel: () => { discardModal.current = null; },
      onOk: () => { discardModal.current = null; if (current()) onOpenExisting(preview); },
    });
  };
  const submit = async () => {
    if (sourceMode !== 'new' || submitting.current || completed.current || !current()) return;
    const validation = validate('properties'); if (validation) { setError(validation); return; }
    const session = getSessionState(); if (!session.authenticated || session.locked) { setError('Sign in and unlock your session before creating this object type.'); return; }
    submitting.current = true; setPending(true); setError('');
    let created: LayerTable;
    try { created = await createEtlTable(draft.schema, { name: draft.name, dataType: draft.dataType, description: draft.description, fields: draft.fields.map(({ id: _id, ...field }) => field), indexes: draft.indexes.map(({ id: _id, ...index }) => ({ ...index, fields: index.fields.join(',') })), relations: draft.relations.map(({ id: _id, targetId, ...relation }) => { const target = objectState.items.find((object) => object.id === targetId)!; return { ...relation, targetSchema: target.schema, targetTable: target.table }; }), checks: draft.checks.map(({ id: _id, ...check }) => check), options: { comment: draft.description } }); }
    catch (failure) { submitting.current = false; if (current()) { setPending(false); setError(errorMessage(failure)); } return; }
    completed.current = true;
    if (current()) onCreated(draft.schema, created);
  };

  const schemaOptions = schemas.map((schema) => ({
    value: schema.schemaName || schema.key,
    label: schema.alias ? `${schema.alias} · ${schema.schemaName || schema.key}` : schema.schemaName || schema.key,
  }));
  const existing = sourceMode === 'existing';
  const qualifiedName = `${draft.schema}.${draft.name}`;
  const sourcePending = !!activeSchema && (tableState.loading || tableState.schema !== activeSchema);
  return <Modal open centered className="flow-surface-modal flow-create-modal flow-create-object" title={variant === 'storage' ? 'Create a storage table' : 'Create a new object type'} width={1160} onCancel={close} maskClosable={!pending} closable={!pending} keyboard={!pending} footer={<>
    <Button disabled={pending} onClick={close}>Cancel</Button>
    {step > 0 && <Button disabled={pending} onClick={() => { setStep((value) => value - 1); setError(''); }}>Back</Button>}
    {existing ? <Button type="primary" disabled={!preview} onClick={openExisting}>{variant === 'storage' ? 'Open existing table' : 'Open existing object type'}</Button> : step < steps.length - 1
      ? <Button type="primary" onClick={next} disabled={!draft.schema || !tablesReady}>Next</Button>
      : <Button type="primary" loading={pending} onClick={() => void submit()}>{variant === 'storage' ? 'Create storage table' : 'Create object type'}</Button>}
  </>}>
    <div className="flow-create-layout">
      <ol className="flow-create-steps" aria-label="Create object type steps">{(existing ? steps.slice(0, 1) : steps).map((label, index) => (
        <li key={label} className={step === index ? 'is-current' : step > index ? 'is-complete' : ''} aria-current={step === index ? 'step' : undefined}>
          <span aria-hidden="true">{step > index ? <CheckOutlined /> : index + 1}</span><span>{label}</span>
        </li>
      ))}</ol>
      <div className="flow-create-step" ref={stepPanel} role="region" aria-label={`${steps[step]} step`}>
        {error && <div className="flow-create-error" role="alert">{error}</div>}
        <p className="flow-create-kicker">Step {step + 1}{!existing && ` of ${steps.length}`}</p>
        {step === 0 && <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Choose a datasource</h3><p>Start with an existing PostgreSQL table, or define a new backing table.</p></header>
          <fieldset className="flow-create-source">
            <legend className="flow-create-label">Datasource option</legend>
            <label className="flow-create-source-option"><input type="radio" name="object-datasource" checked={existing} onChange={() => { setSourceMode('existing'); setError(''); }} /><span><strong>Use an existing table</strong><small>Preview and open an object type already in the catalog.</small></span></label>
            <label className="flow-create-source-option"><input type="radio" name="object-datasource" checked={!existing} onChange={() => { setSourceMode('new'); setError(''); }} /><span><strong>Create a backing table</strong><small>Define a new object type and its PostgreSQL storage.</small></span></label>
          </fieldset>
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Schema</span><Select {...selectStyle} aria-label="Object type schema" placeholder="Select a schema" value={activeSchema || undefined} options={schemaOptions} onChange={(schema: string) => { if (schema !== activeSchema) { if (existing) { setExistingTable(''); setExistingSchema(schema); } else change({ schema }); } }} /></label>
            {existing ? <label className="flow-create-field"><span>Existing table</span><Select {...selectStyle} aria-label="Existing table" showSearch disabled={!activeSchema || !tablesReady} placeholder={sourcePending ? 'Loading tables…' : 'Select a table'} value={existingTable || undefined} options={options(tables.map((table) => table.name))} onChange={(table: string) => { setExistingTable(table); setPreviewVersion((version) => version + 1); }} /></label>
              : <div className="flow-create-field"><span>Connection type</span><div className="flow-create-value">PostgreSQL</div></div>}
          </div>
          {activeSchema && <p className="flow-create-help" role="status">{sourcePending ? 'Loading schema…' : tablesReady ? `${tables.length} existing tables in ${activeSchema}` : ''}</p>}
          {tableState.schema === activeSchema && tableState.error && <div className="flow-create-error" role="alert">{tableState.error}<Button size="small" onClick={() => setTableVersion((value) => value + 1)} icon={<ReloadOutlined />}>Retry</Button></div>}
          {existing && <div className="flow-create-notice">Existing tables already appear as object types. Opening one does not create a table, copy data, or change property mappings.</div>}
          {existing && sourceKey && !visiblePreview && <p role="status" className="flow-create-help">Loading object type properties…</p>}
          {visiblePreview?.error && <div className="flow-create-error" role="alert">{visiblePreview.error}<Button size="small" onClick={() => setPreviewVersion((value) => value + 1)}>Retry</Button></div>}
          {preview && <>
            <h4 className="flow-create-subheading">{preview.id}</h4>
            <p className="flow-create-help">Primary key: {preview.primaryKey.join(', ') || 'None declared'}</p>
            <Table<EtlObjectProperty> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey="name" dataSource={preview.properties} columns={[{ title: 'Property', dataIndex: 'name' }, { title: 'Storage type', dataIndex: 'type' }, { title: 'Key', render: (_, field) => field.primaryKey ? 'Primary key' : field.nullable ? 'Nullable' : 'Required' }]} />
          </>}
        </section>}
        {step === 1 && <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Define object type metadata</h3><p>Choose the identifier and data type for the new backing table.</p></header>
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Object type ID</span><Input aria-label="Object type ID" value={draft.name} maxLength={63} placeholder="company" onChange={(event) => change({ name: event.target.value })} /><small>Used as the PostgreSQL table name.</small></label>
            <label className="flow-create-field"><span>Data type</span><Input aria-label="Object data type" value={draft.dataType} maxLength={63} placeholder="company" onChange={(event) => change({ dataType: event.target.value })} /></label>
            <label className="flow-create-field is-wide"><span>Description</span><Input.TextArea aria-label="Object type description" value={draft.description} rows={3} onChange={(event) => change({ description: event.target.value })} /><small>Saved as the object description and table comment.</small></label>
          </div>
        </section>}
        {step === 2 && <>
        <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Create properties</h3><p>Define the fields stored in the backing table.</p></header>
          <div className="flow-create-grid flow-create-keys">
            <label className="flow-create-field"><span>Primary key</span><Select {...selectStyle} aria-label="Primary key properties" mode="multiple" value={draft.fields.filter((field) => field.primary && identifier.test(field.name)).map((field) => field.name)} options={propertyOptions} placeholder="Choose one or more properties" onChange={(names: string[]) => change({ fields: draft.fields.map((field) => ({ ...field, primary: names.includes(field.name), nullable: names.includes(field.name) ? false : field.nullable })) })} /><small>One or more properties that uniquely identify an object.</small></label>
            <div className="flow-create-field"><span>Title property</span><div className="flow-create-value">Not configurable in this wizard</div><small>An object type uses its table name as its display name.</small></div>
          </div>
          <div className="flow-create-property-heading"><strong>Properties <small>({draft.fields.length})</small></strong><Button size="small" icon={<PlusOutlined />} disabled={draft.fields.length >= 200} onClick={() => change({ fields: [...draft.fields, { id: nextId.current++, name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' }] })}>Add property</Button></div>
          <Table<Property> className="flow-create-table" pagination={false} size="small" rowKey="id" dataSource={draft.fields} scroll={{ x: 835 }} columns={[
          { title: 'Property', width: 170, render: (_, field, index) => <Input aria-label={`Property ${index + 1} name`} value={field.name} maxLength={63} onChange={(event) => updateProperty(field.id, { name: event.target.value })} /> },
          { title: 'Storage type', width: 160, render: (_, field, index) => <Select {...selectStyle} aria-label={`Property ${index + 1} type`} value={field.type} options={options(fieldTypes)} onChange={(type: string) => updateProperty(field.id, { type, length: lengthTypes.includes(type) ? field.length : '' })} /> },
          { title: 'Length', width: 80, render: (_, field, index) => <Input aria-label={`Property ${index + 1} length`} value={field.length} inputMode="numeric" disabled={!lengthTypes.includes(field.type)} onChange={(event) => updateProperty(field.id, { length: event.target.value })} /> },
          { title: 'Primary key', width: 85, render: (_, field, index) => <Checkbox aria-label={`Property ${index + 1} primary key`} checked={field.primary} onChange={(event) => updateProperty(field.id, { primary: event.target.checked, ...(event.target.checked ? { nullable: false } : {}) })} /> },
          { title: 'Nullable', width: 70, render: (_, field, index) => <Checkbox aria-label={`Property ${index + 1} nullable`} checked={field.nullable} disabled={field.primary} onChange={(event) => updateProperty(field.id, { nullable: event.target.checked })} /> },
          { title: 'Description', width: 180, render: (_, field, index) => <Input aria-label={`Property ${index + 1} description`} value={field.comment} onChange={(event) => updateProperty(field.id, { comment: event.target.value })} /> },
          { title: '', width: 102, render: (_, field, index) => <span className="flow-create-row-actions"><button type="button" aria-label={`Move property ${index + 1} up`} disabled={index === 0} onClick={() => moveProperty(index, -1)}><ArrowUpOutlined /></button><button type="button" aria-label={`Move property ${index + 1} down`} disabled={index === draft.fields.length - 1} onClick={() => moveProperty(index, 1)}><ArrowDownOutlined /></button><button type="button" aria-label={`Remove property ${index + 1}`} onClick={() => change({ fields: draft.fields.filter((item) => item.id !== field.id) })}><DeleteOutlined /></button></span> },
        ]} /></section>
        <details className="flow-create-advanced"><summary>Advanced storage settings</summary>
          <section className="flow-create-section"><header className="flow-create-heading"><h3>Indexes</h3><Button size="small" icon={<PlusOutlined />} onClick={() => change({ indexes: [...draft.indexes, { id: nextId.current++, name: '', type: 'BTREE', unique: false, fields: [], comment: '' }] })}>Add index</Button></header>{draft.indexes.map((index, position) => <div className="flow-create-grid" key={index.id}><label className="flow-create-field"><span>Index name</span><Input aria-label={`Index ${position + 1} name`} value={index.name} onChange={(event) => change({ indexes: draft.indexes.map((item) => item.id === index.id ? { ...item, name: event.target.value } : item) })} /></label><label className="flow-create-field"><span>Method</span><Select {...selectStyle} aria-label={`Index ${position + 1} method`} value={index.type} options={options(['BTREE', 'HASH', 'GIN', 'GIST', 'BRIN'])} onChange={(type: string) => change({ indexes: draft.indexes.map((item) => item.id === index.id ? { ...item, type, unique: type === 'BTREE' && item.unique } : item) })} /></label><label className="flow-create-field"><span>Properties</span><Select {...selectStyle} aria-label={`Index ${position + 1} properties`} mode="multiple" value={index.fields} options={propertyOptions} onChange={(fields: string[]) => change({ indexes: draft.indexes.map((item) => item.id === index.id ? { ...item, fields } : item) })} /></label><label className="flow-create-field"><span>Description</span><Input aria-label={`Index ${position + 1} description`} value={index.comment} onChange={(event) => change({ indexes: draft.indexes.map((item) => item.id === index.id ? { ...item, comment: event.target.value } : item) })} /></label><Checkbox checked={index.unique} disabled={index.type !== 'BTREE'} onChange={(event) => change({ indexes: draft.indexes.map((item) => item.id === index.id ? { ...item, unique: event.target.checked } : item) })}>Unique index</Checkbox><Button size="small" onClick={() => change({ indexes: draft.indexes.filter((item) => item.id !== index.id) })}>Remove index</Button></div>)}</section>
          <section className="flow-create-section"><header className="flow-create-heading"><h3>Relationships</h3><Button size="small" icon={<PlusOutlined />} onClick={() => change({ relations: [...draft.relations, { id: nextId.current++, name: '', targetId: '', sourceFields: [], targetFields: [], cardinality: 'many_to_one', description: '' }] })}>Add relation</Button></header>
            <p className="flow-create-help">Map properties between ontology object types in selection order. Relations are metadata only and do not create database foreign keys or scan records.</p>
            {objectState.error && <div className="flow-create-error" role="alert">{objectState.error}<Button size="small" onClick={() => setObjectVersion((value) => value + 1)}>Retry object types</Button></div>}
            {draft.relations.map((relation, position) => <div className="flow-create-grid" key={relation.id}>
              <label className="flow-create-field"><span>Relation name</span><Input aria-label={`Relation ${position + 1} name`} maxLength={128} value={relation.name} onChange={(event) => change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, name: event.target.value } : item) })} /></label>
              <label className="flow-create-field"><span>Cardinality</span><Select {...selectStyle} aria-label={`Relation ${position + 1} cardinality`} value={relation.cardinality} options={cardinalities} onChange={(cardinality: string) => change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, cardinality } : item) })} /></label>
              <label className="flow-create-field"><span>Source object type</span><Input aria-label={`Relation ${position + 1} source object`} readOnly value={`${draft.schema}.${draft.name || 'new_object_type'}`} /></label>
              <label className="flow-create-field"><span>Target object type</span><Select {...selectStyle} aria-label={`Relation ${position + 1} target object`} showSearch optionFilterProp="searchText" disabled={objectState.loading || !!objectState.error} placeholder={objectState.loading ? 'Loading object types…' : 'Select an ontology object type'} value={relation.targetId || undefined} options={objectState.items.map((object) => ({ value: object.id, label: `${object.displayName} · ${object.id}`, searchText: `${object.displayName} ${object.id}` }))} onChange={(targetId: string) => { change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, targetId, targetFields: [] } : item) }); void loadReference(targetId); }} /></label>
              <label className="flow-create-field"><span>Source properties</span><Select {...selectStyle} aria-label={`Relation ${position + 1} source properties`} mode="multiple" value={relation.sourceFields} options={propertyOptions} onChange={(sourceFields: string[]) => change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, sourceFields } : item) })} /></label>
              <label className="flow-create-field"><span>Target properties</span><Select {...selectStyle} aria-label={`Relation ${position + 1} target properties`} mode="multiple" disabled={!relation.targetId || !references[relation.targetId] || references[relation.targetId].loading || !!references[relation.targetId].error} value={relation.targetFields} options={options(references[relation.targetId]?.columns || [])} onChange={(targetFields: string[]) => change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, targetFields } : item) })} /></label>
              {relation.targetId && references[relation.targetId]?.error && <div className="flow-create-error" role="alert">{references[relation.targetId].error}<Button size="small" onClick={() => void loadReference(relation.targetId)}>Retry properties</Button></div>}
              <label className="flow-create-field is-wide"><span>Description</span><Input aria-label={`Relation ${position + 1} description`} value={relation.description} maxLength={4000} onChange={(event) => change({ relations: draft.relations.map((item) => item.id === relation.id ? { ...item, description: event.target.value } : item) })} /></label>
              <Button size="small" onClick={() => change({ relations: draft.relations.filter((item) => item.id !== relation.id) })}>Remove relation</Button>
            </div>)}
          </section>
          <section className="flow-create-section"><header className="flow-create-heading"><h3>Checks</h3><Button size="small" icon={<PlusOutlined />} onClick={() => change({ checks: [...draft.checks, { id: nextId.current++, name: '', expression: '', comment: '' }] })}>Add check</Button></header>{draft.checks.map((check, position) => <div className="flow-create-grid" key={check.id}><label className="flow-create-field"><span>Constraint name</span><Input aria-label={`Check ${position + 1} name`} value={check.name} onChange={(event) => change({ checks: draft.checks.map((item) => item.id === check.id ? { ...item, name: event.target.value } : item) })} /></label><label className="flow-create-field"><span>Description</span><Input aria-label={`Check ${position + 1} description`} value={check.comment} onChange={(event) => change({ checks: draft.checks.map((item) => item.id === check.id ? { ...item, comment: event.target.value } : item) })} /></label><label className="flow-create-field is-wide"><span>Expression</span><Input aria-label={`Check ${position + 1} expression`} value={check.expression} maxLength={4000} placeholder="record_id <> ''" onChange={(event) => change({ checks: draft.checks.map((item) => item.id === check.id ? { ...item, expression: event.target.value } : item) })} /></label><Button size="small" onClick={() => change({ checks: draft.checks.filter((item) => item.id !== check.id) })}>Remove check</Button></div>)}</section>
        </details>
      </>}
        {step === 3 && <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Actions</h3><p>Review what will be created with this object type.</p></header>
          <div className="flow-create-notice"><strong>No action types will be generated</strong><p>This wizard creates table storage and ontology metadata. Business actions and their permissions are not configured here.</p></div>
        </section>}
        {step === 4 && <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Choose where to save</h3><p>The destination comes from the datasource step. To change the schema, return to Datasource.</p></header>
          <dl className="flow-create-summary">
            <dt>Connection</dt><dd>PostgreSQL</dd><dt>Schema</dt><dd>{draft.schema}</dd><dt>Backing table</dt><dd>{qualifiedName}</dd>
          </dl>
        </section>}
        {step === 5 && <section className="flow-create-section">
          <header className="flow-create-heading"><h3 tabIndex={-1}>Review security and create</h3><p>Check the destination and properties before creating the backing table.</p></header>
          <div className="flow-create-notice"><strong>Existing access controls apply</strong><p>This does not add or change object or property access policies.</p></div>
          <dl className="flow-create-summary">
            <dt>Backing table</dt><dd>{qualifiedName}</dd><dt>Data type</dt><dd>{draft.dataType}</dd><dt>Description</dt><dd>{draft.description || 'No description provided.'}</dd><dt>Primary key</dt><dd>{draft.fields.filter((field) => field.primary).map((field) => field.name).join(', ')}</dd><dt>Storage settings</dt><dd>{draft.indexes.length} indexes · {draft.relations.length} relations · {draft.checks.length} checks</dd>
          </dl>
          <Table<Property> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey="id" dataSource={draft.fields} columns={[{ title: 'Property', dataIndex: 'name' }, { title: 'Storage type', render: (_, field) => `${field.type}${field.length ? `(${field.length})` : ''}` }, { title: 'Key', render: (_, field) => field.primary ? 'Primary key' : field.nullable ? 'Nullable' : 'Required' }, { title: 'Description', dataIndex: 'comment' }]} />
        </section>}
      </div>
    </div>
  </Modal>;
}

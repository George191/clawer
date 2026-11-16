import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select, Table } from '@/components/ui';
import { CheckOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from '@/components/ui/icons';
import { fetchEtlObjectPreview, fetchLayerTables, fetchTableData } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { EtlObjectPreview, LayerNode, LayerTable, QueryResult } from '../../services/types';
import {
  createOntologyDraft, fetchOntologyDraft, listOntologyDrafts, listOntologyPrincipals, listOntologyProjects,
  publishOntologyDraft, testOntologyPolicy, updateOntologyDraft, validateOntologyDraft,
  type OntologyDraft, type OntologyIssue, type OntologyPrincipal, type OntologyProject, type OntologyValidation,
  type SemanticDefinition, type SemanticProperty, type SemanticAction, type SemanticSource, type Principal, type PublishedOntologyType,
} from '../../services/ontology';
import './ontology-create.css';

const steps = ['Datasource', 'Metadata', 'Properties', 'Actions', 'Save location', 'Security'] as const;
const idPattern = /^[a-z][a-z0-9_-]{0,62}$/;
const warehouseSchemas = new Set(['ts_ods', 'ts_dwd']);
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };
const typeOptions = ['string', 'integer', 'number', 'boolean', 'date', 'datetime', 'json'];
const actionTypes: SemanticAction['kind'][] = ['create', 'modify', 'delete'];
const principalKey = (p: Principal) => p.kind + ':' + p.id;
const opt = (values: readonly string[]) => values.map(value => ({ value, label: value }));
const path = (schema: string, table: string) => [schema, table].filter(Boolean).join('.');
const emptyDefinition = (schema = '', allowedSource = true): SemanticDefinition => ({
  id: '', name: '', pluralName: '', description: '', icon: '', color: '', groups: [], projectId: '',
  source: allowedSource ? { mode: 'existing', schema: warehouseSchemas.has(schema) ? schema : '', table: '' } : { mode: 'none' }, properties: [], primaryKey: [], titleKey: '', actions: [],
  security: { mode: allowedSource ? 'inherit' : 'explicit', objectReaders: [], fieldReaders: {} },
});
function problem(failure: unknown, fallback: string) {
  const detail = (failure as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'errors' in detail && Array.isArray(detail.errors)) return detail.errors.map(item => [item.path, item.message].filter(Boolean).join(': ')).join('\n') || fallback;
  if (Array.isArray(detail)) return detail.map(item => typeof item.msg === 'string' ? [...(item.loc || []).slice(1), item.msg].join(' · ') : '').filter(Boolean).join('\n') || fallback;
  return fallback;
}
const fromError = (failure: unknown) => (failure as { response?: { status?: number } }).response?.status;
function display(v: unknown) { if (v == null) return '—'; return typeof v === 'object' ? JSON.stringify(v) : String(v); }
function propertyType(dbType: string) {
  if (/bool/i.test(dbType)) return 'boolean';
  if (/int|serial/i.test(dbType)) return 'integer';
  if (/numeric|decimal|float|double|real/i.test(dbType)) return 'number';
  if (/timestamp/i.test(dbType)) return 'datetime';
  if (/^date$/i.test(dbType)) return 'date';
  if (/json/i.test(dbType)) return 'json';
  return 'string';
}
function makeProperty(column: EtlObjectPreview['properties'][number]): SemanticProperty {
  return { id: column.name, name: column.displayName || column.name, type: propertyType(column.type), sourceColumn: column.name, required: !column.nullable };
}
type Load<T> = { data?: T; error?: string; loading?: boolean };
function IssueList({ title, issues }: { title: string; issues: OntologyIssue[] }) {
  return issues.length ? <section className="flow-semantic-issues"><h4>{title} ({issues.length})</h4><ul>{issues.map((item, index) => <li key={index}><strong>{item.path || 'Definition'}</strong> {item.message}</li>)}</ul></section> : null;
}
const Heading = ({ title, detail }: { title: string; detail: string }) => <header className="flow-create-heading"><h3 tabIndex={-1}>{title}</h3><p>{detail}</p></header>;

type Props = {
  allowedSource: boolean; schemas: LayerNode[]; initialSchema?: string; onCancel: () => void; onPublished: (definition: PublishedOntologyType) => void;
};
export default function SemanticObjectWizard({ allowedSource, schemas, initialSchema, onCancel, onPublished }: Props) {
  const initial = useRef(emptyDefinition(initialSchema, allowedSource));
  const [definition, setDefinition] = useState(initial.current);
  const [saved, setSaved] = useState<OntologyDraft>();
  const [stage, setStage] = useState<'wizard' | 'review'>('wizard');
  const [step, setStep] = useState(0);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const stageContent = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const outstanding = useRef(false);
  const discard = useRef<ReturnType<typeof Modal.confirm> | null>(null);
  const active = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => active.current && getSessionState().identity === identity;
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  const [projects, setProjects] = useState<Load<OntologyProject[]>>({ loading: true });
  const [principals, setPrincipals] = useState<Load<OntologyPrincipal[]>>({});
  const [drafts, setDrafts] = useState<Load<OntologyDraft[]>>({ loading: true });
  const [retry, setRetry] = useState(0);
  const [tables, setTables] = useState<Load<LayerTable[]> & { key?: string }>({});
  const [sourcePreview, setSourcePreview] = useState<Load<EtlObjectPreview> & { key?: string }>({});
  const [rows, setRows] = useState<Load<QueryResult>>({});
  const [validation, setValidation] = useState<OntologyValidation>();
  const [checkedRevision, setCheckedRevision] = useState<number>();
  const [testPrincipal, setTestPrincipal] = useState('');
  const [policyResult, setPolicyResult] = useState<{ visible: boolean; visibleFields: string[] }>();
  const existing = definition.source.mode === 'existing';
  const sourceKey = existing && definition.source.schema && definition.source.table ? path(definition.source.schema, definition.source.table) : '';
  const preview = sourceKey && sourcePreview.key === sourceKey ? sourcePreview.data : undefined;
  const dirty = JSON.stringify(saved?.definition ?? initial.current) !== JSON.stringify(definition);
  const structuralReady = !!definition.name.trim() && !definition.actions.some(item => !item.submitters.length || (item.kind !== 'delete' && !item.fields.length)) && (definition.security.mode !== 'explicit' || !!definition.security.objectReaders.length);
  const canPublish = !!saved && !dirty && checkedRevision === saved.revision && !!validation && validation.errors.length === 0;
  const set = (values: Partial<SemanticDefinition>) => {
    setDefinition(prev => ({ ...prev, ...values })); setError(''); setStatus(''); setPolicyResult(undefined);
    setValidation(undefined); setCheckedRevision(undefined);
  };
  useEffect(() => { active.current = true; return () => { active.current = false; discard.current?.destroy(); }; }, []);
  useEffect(() => { if (stageContent.current) stageContent.current.inert = busy; }, [busy]);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (panel.current) { panel.current.scrollTop = 0; panel.current.querySelector('h3')?.focus({ preventScroll: true }); }
  }, [step, stage]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty, busy]);
  useEffect(() => {
    let alive = true;
    setProjects({ loading: true }); setDrafts({ loading: true });
    listOntologyProjects().then(data => { if (alive && current()) setProjects({ data }); }, failure => { if (alive && current()) setProjects({ error: problem(failure, 'Unable to load projects.') }); });
    listOntologyDrafts().then(data => { if (alive && current()) setDrafts({ data }); }, failure => { if (alive && current()) setDrafts({ error: problem(failure, 'Unable to load saved drafts.') }); });
    return () => { alive = false; };
  }, [identity, retry]);
  useEffect(() => {
    let alive = true;
    if (!definition.projectId) { setPrincipals({}); return; }
    setPrincipals({ loading: true });
    listOntologyPrincipals(definition.projectId).then(data => { if (alive && current()) setPrincipals({ data }); }, failure => { if (alive && current()) setPrincipals({ error: problem(failure, 'Unable to load project principals.') }); });
    return () => { alive = false; };
  }, [definition.projectId, identity, retry]);
  useEffect(() => {
    let alive = true;
    const key = definition.source.schema || '';
    setTables({ key, loading: existing && !!key });
    if (existing && key) fetchLayerTables(key).then(data => { if (alive && current()) setTables({ key, data }); }, failure => { if (alive && current()) setTables({ key, error: problem(failure, 'Unable to load tables.') }); });
    return () => { alive = false; };
  }, [definition.source.schema, existing, identity, retry]);
  useEffect(() => {
    let alive = true;
    setRows({});
    setSourcePreview({ key: sourceKey, loading: !!sourceKey });
    if (sourceKey && definition.source.schema && definition.source.table) fetchEtlObjectPreview(definition.source.schema, definition.source.table, true).then(
      data => { if (alive && current()) setSourcePreview({ key: sourceKey, data }); },
      () => { if (alive && current()) setSourcePreview({ key: sourceKey, error: 'Unable to load source columns.' }); },
    );
    return () => { alive = false; };
  }, [sourceKey, identity, retry]);

  const guards = (action: () => void) => {
    if (outstanding.current || discard.current) return;
    if (!dirty) { action(); return; }
    discard.current = Modal.confirm({ className: 'flow-surface-modal flow-create-confirmation', title: 'Discard unsaved changes?', content: saved ? 'The saved revision is retained. Only changes since your last save are discarded.' : 'This object type draft has not been saved.', okText: 'Discard changes', onCancel: () => { discard.current = null; }, onOk: () => { discard.current = null; if (current()) action(); } });
  };
  const run = async (work: () => Promise<void>) => {
    if (!current() || outstanding.current) return;
    outstanding.current = true; setBusy(true); setError(''); setStatus('');
    try { await work(); }
    catch (failure) {
      if (current()) setError(problem(failure, fromError(failure) === 409 ? 'This draft changed in another session. Reopen the latest revision before publishing.' : 'The request did not complete. Your changes are retained.'));
    }
    finally { outstanding.current = false; if (current()) setBusy(false); }
  };
  const loadDraft = (id: string) => guards(() => void run(async () => {
    const item = await fetchOntologyDraft(id);
    if (!current()) return;
    setSaved(item); setDefinition(item.definition); setValidation(undefined); setCheckedRevision(undefined);
    setPolicyResult(undefined); setTestPrincipal(''); setStep(0); setStage('wizard'); setStatus('Draft revision ' + item.revision + ' loaded.');
  }));
  const sourceChange = (source: SemanticSource) => set({ source, properties: [], primaryKey: [], titleKey: '', actions: [], security: { mode: source.mode === 'existing' ? 'inherit' : 'explicit', objectReaders: [], fieldReaders: {} } });
  const projectChange = (projectId: string) => {
    setTestPrincipal('');
    set({ projectId, actions: definition.actions.map(action => ({ ...action, submitters: [] })), security: { mode: definition.security.mode, objectReaders: [], fieldReaders: {} } });
  };
  const updateProperty = (position: number, value: Partial<SemanticProperty>) => {
    const previous = definition.properties[position];
    const nextId = value.id ?? previous.id;
    const remap = (item: string) => item === previous.id ? nextId : item;
    set({
      properties: definition.properties.map((item, index) => index === position ? { ...item, ...value } : item),
      primaryKey: definition.primaryKey.map(remap).filter(Boolean), titleKey: remap(definition.titleKey),
      actions: definition.actions.map(action => ({ ...action, fields: action.fields.map(remap).filter(Boolean) })),
      security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).map(([id, readers]) => [remap(id), readers]).filter(([id]) => id)) },
    });
  };
  const removeProperty = (position: number) => {
    const id = definition.properties[position].id;
    set({ properties: definition.properties.filter((_, index) => index !== position), primaryKey: definition.primaryKey.filter(item => item !== id), titleKey: definition.titleKey === id ? '' : definition.titleKey,
      actions: definition.actions.map(action => ({ ...action, fields: action.fields.filter(field => field !== id) })),
      security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).filter(([field]) => field !== id)) },
    });
  };
  const mapSource = () => {
    if (!preview) return;
    const properties = preview.properties.map(makeProperty);
    set({ properties, primaryKey: [...preview.primaryKey], titleKey: preview.titleProperty ?? '', security: { ...definition.security, fieldReaders: {} } });
  };
  const primaryOptions = definition.properties.filter(item => idPattern.test(item.id) && !['number', 'json'].includes(item.type)).map(item => ({ value: item.id, label: item.name ? item.name + ' · ' + item.id : item.id }));
  const titleOptions = definition.properties.filter(item => idPattern.test(item.id) && item.type !== 'json').map(item => ({ value: item.id, label: item.name ? item.name + ' · ' + item.id : item.id }));
  const propertyOptions = definition.properties.filter(item => idPattern.test(item.id)).map(item => ({ value: item.id, label: item.name ? item.name + ' · ' + item.id : item.id }));
  function stepError(index: number) {
    if (index === 0 && existing && (!sourceKey || !preview)) return 'Choose a source table and load its columns.';
    if (index === 1 && (!idPattern.test(definition.id) || !definition.name.trim() || !definition.pluralName.trim())) return 'Enter a valid API ID and singular and plural display names.';
    if (index === 2) {
      const ids = definition.properties.map(item => item.id);
      if (!ids.length || new Set(ids).size !== ids.length || definition.properties.some(item => !idPattern.test(item.id) || !item.name.trim() || existing && !preview?.properties.some(column => column.name === item.sourceColumn))) return 'Define distinct property IDs, display names and an existing source column for every mapped property.';
      if (!definition.primaryKey.length || definition.primaryKey.some(id => !definition.properties.some(item => item.id === id && item.required))) return 'Choose at least one required property as the primary key.';
      if (!definition.titleKey || !ids.includes(definition.titleKey)) return 'Choose a title property.';
    }
    if (index === 3 && definition.actions.some(item => !item.submitters.length || (item.kind !== 'delete' && !item.fields.length))) return 'Choose submitters and writable fields for each selected action.';
    if (index === 5 && definition.security.mode === 'explicit' && !definition.security.objectReaders.length) return 'Select who can read this object type.';
    if (index === 4 && !projects.data?.some(item => item.id === definition.projectId)) return 'Select an available project.';
    return '';
  }
  const next = () => {
    const msg = stepError(step);
    if (msg) { setError(msg); return; }
    if (step === 0 && !definition.properties.length && existing) mapSource();
    setError(''); setStep(value => value + 1);
  };
  const save = async () => {
    if (saved && !dirty) return saved;
    const item = saved ? await updateOntologyDraft(saved.id, saved.revision, definition) : await createOntologyDraft(definition);
    if (current()) {
      setSaved(item); setDefinition(item.definition);
      setDrafts(state => ({ ...state, data: [item, ...(state.data || []).filter(old => old.id !== item.id)] }));
      setValidation(undefined); setCheckedRevision(undefined);
    }
    return item;
  };
  const saveDraft = () => void run(async () => {
    const item = await save();
    if (current()) setStatus('Draft saved at revision ' + item.revision + '. Nothing has been published.');
  });
  const review = () => void run(async () => {
    for (let index = 0; index < steps.length; index++) {
      const message = stepError(index);
      if (message) { setStep(index); setError(message); return; }
    }
    const item = await save();
    if (!current()) return;
    setStage('review'); setValidation(undefined); setCheckedRevision(undefined);
    const result = await validateOntologyDraft(item.id, item.revision);
    if (current()) { setValidation(result); setCheckedRevision(item.revision); }
  });
  const publish = () => {
    if (!saved || !canPublish) return;
    const item = saved;
    void run(async () => {
      const object = await publishOntologyDraft(item.id, item.revision);
      if (current()) onPublished({ id: object.id, revision: object.revision, definition: object.objectType });
    });
  };
  const test = () => void run(async () => {
    if (!testPrincipal) return;
    const result = await testOntologyPolicy(definition, testPrincipal);
    if (current()) setPolicyResult(result);
  });
  const loadRows = () => void run(async () => {
    if (!definition.source.schema || !definition.source.table) return;
    const data = await fetchTableData(definition.source.schema, definition.source.table, 5);
    if (current()) setRows({ data });
  });

  const changeAction = (kind: SemanticAction['kind'], next: Partial<SemanticAction>) => set({ actions: definition.actions.map(action => action.kind === kind ? { ...action, ...next } : action) });
  const toggleAction = (kind: SemanticAction['kind'], checked: boolean) => {
    const fields = kind === 'create' ? definition.properties.filter(item => item.required).map(item => item.id) : [];
    set({ actions: checked ? [...definition.actions, { kind, submitters: [], fields }] : definition.actions.filter(action => action.kind !== kind) });
  };
  const projectOptions = (projects.data || []).map(item => ({ value: item.id, label: item.name }));
  const principalOptions = (principals.data || []).map(principal => ({ value: principalKey(principal), label: principal.name + ' · ' + principal.kind }));
  const principalList = (keys: string[]) => (principals.data || []).filter(principal => keys.includes(principalKey(principal))).map(({kind, id}) => ({kind, id}));
  const userOptions = (principals.data || []).filter(principal => principal.kind === 'user').map(principal => ({ value: principal.id, label: principal.name }));
  const sourceCols = opt(preview?.properties.map(item => item.name) || []);
  const schemaOptions = schemas.filter(schema => warehouseSchemas.has(schema.schemaName || schema.key)).map(schema => ({ value: schema.schemaName || schema.key, label: schema.alias ? schema.alias + ' · ' + (schema.schemaName || schema.key) : schema.schemaName || schema.key }));
  const projectInput = <label className="flow-create-field"><span>Project</span><Select {...selectStyle} aria-label="Destination project" showSearch options={projectOptions} value={definition.projectId || undefined} loading={projects.loading} disabled={busy || projects.loading} placeholder="Select a project" onChange={projectChange} /></label>;
  const retries = () => setRetry(value => value + 1);
  return <Modal open centered className="flow-surface-modal flow-create-modal flow-create-object" title={stage === 'wizard' ? 'Create a new object type' : 'Review object type changes'} width={1160} onCancel={() => guards(onCancel)} maskClosable={!busy} closable={!busy} keyboard={!busy} footer={<>
    <Button disabled={busy} onClick={() => guards(onCancel)}>Close</Button>
    {stage === 'wizard' ? <>
      {step > 0 && <Button disabled={busy} onClick={() => { setStep(value => value - 1); setError(''); }}>Back</Button>}
      <Button disabled={busy || !definition.id || !definition.projectId || !structuralReady || (!!saved && !dirty)} onClick={saveDraft}>Save draft</Button>
      {step < steps.length - 1 ? <Button type="primary" disabled={busy} onClick={next}>Next</Button> : <Button type="primary" loading={busy} onClick={review}>Review changes</Button>}
    </> : <>
      <Button disabled={busy} onClick={() => { setStage('wizard'); setError(''); }}>Edit draft</Button>
      <Button disabled={busy} onClick={review}>Revalidate</Button>
      <Button type="primary" disabled={busy || !canPublish} onClick={publish}>Publish object type</Button>
    </>}
  </>}>
    <div className="flow-create-layout">
      <ol className="flow-create-steps" aria-label={stage === 'wizard' ? 'Create object type steps' : 'Review progress'}>
        {(stage === 'wizard' ? steps : ['Review changes'] as const).map((label, index) => <li key={label} className={stage === 'review' || index === step ? 'is-current' : index < step ? 'is-complete' : ''} aria-current={stage === 'review' || index === step ? 'step' : undefined}><span aria-hidden="true">{stage === 'wizard' && index < step ? <CheckOutlined /> : index + 1}</span><span>{label}</span></li>)}
      </ol>
      <div className="flow-create-step" role="region" aria-label={stage === 'wizard' ? steps[step] + ' step' : 'Publish review'} ref={panel}>
        {error && <div className="flow-create-error" role="alert">{error}</div>}
        {status && <p className="flow-semantic-status" role="status">{status}</p>}
        <p className="flow-create-kicker">{stage === 'review' ? 'Publish review' : 'Step ' + (step + 1) + ' of ' + steps.length}</p>
        <div ref={stageContent}>
        {stage === 'wizard' && step === 0 && <section className="flow-create-section">
          <Heading title="Choose a datasource" detail="Bind an existing PostgreSQL table without copying data, or start with no backing table." />
          <fieldset className="flow-create-source"><legend>Datasource option</legend>
            <label className="flow-create-source-option"><input type="radio" name="semantic-source" checked={existing} disabled={busy || !allowedSource} onChange={() => sourceChange({ mode: 'existing', schema: warehouseSchemas.has(initialSchema || '') ? initialSchema : '', table: '' })} /><span><strong>Existing PostgreSQL table</strong><small>Map existing columns to semantic properties. The source table is not changed.</small></span></label>
            <label className="flow-create-source-option"><input type="radio" name="semantic-source" checked={!existing} disabled={busy} onChange={() => sourceChange({ mode: 'none' })} /><span><strong>No backing datasource</strong><small>Define the object model independently from physical storage.</small></span></label>
          </fieldset>
          {!allowedSource && <p className="flow-create-help">A platform administrator is required to bind a warehouse table.</p>}
          {existing && <div className="flow-create-grid">
            <label className="flow-create-field"><span>Schema</span><Select {...selectStyle} aria-label="Object type schema" value={definition.source.schema || undefined} options={schemaOptions} disabled={busy} placeholder="Select a schema" onChange={(schema: string) => sourceChange({ mode: 'existing', schema, table: '' })} /></label>
            <label className="flow-create-field"><span>Source table</span><Select {...selectStyle} aria-label="Source table" showSearch value={definition.source.table || undefined} disabled={busy || tables.loading || !definition.source.schema} options={opt((tables.key === definition.source.schema ? tables.data : [])?.map(item => item.name) || [])} placeholder="Select a table" onChange={(table: string) => sourceChange({ ...definition.source, table })} /></label>
          </div>}
          {(tables.error || sourcePreview.error) && <p className="flow-create-error" role="alert">{tables.error || sourcePreview.error}<Button size="small" icon={<ReloadOutlined />} onClick={retries}>Retry</Button></p>}
          {(tables.loading || sourcePreview.loading) && <p role="status" className="flow-create-help">Reading source metadata…</p>}
          {preview && <p className="flow-create-notice">{preview.properties.length} source columns available. Primary key: {preview.primaryKey.join(', ') || 'None declared'}.</p>}
          {(!!drafts.data?.length || drafts.error || drafts.loading) && <section className="flow-semantic-saved">
            <h4>Resume a draft</h4>
            {drafts.error ? <p className="flow-create-error" role="alert">{drafts.error}<Button size="small" onClick={retries}>Retry</Button></p> :
              <Select {...selectStyle} aria-label="Resume saved draft" showSearch loading={drafts.loading} disabled={busy} placeholder="Choose a saved draft" options={(drafts.data || []).map(item => ({ value: item.id, label: (item.definition.name || item.definition.id) + ' · revision ' + item.revision }))} onChange={loadDraft} />}
          </section>}
        </section>}
        {stage === 'wizard' && step === 1 && <section className="flow-create-section">
          <Heading title="Object type metadata" detail="Business display names are separate from the stable API ID." />
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Display name</span><Input maxLength={120} value={definition.name} onChange={event => set({ name: event.target.value })} placeholder="Airport" /></label>
            <label className="flow-create-field"><span>Plural display name</span><Input maxLength={120} value={definition.pluralName} onChange={event => set({ pluralName: event.target.value })} placeholder="Airports" /></label>
            <label className="flow-create-field"><span>API ID</span><Input aria-label="API ID" value={definition.id} maxLength={63} onChange={event => set({ id: event.target.value })} placeholder="airport" /><small>Lowercase letters, digits, hyphens and underscores. Start with a letter.</small></label>
            <label className="flow-create-field"><span>Icon</span><Select {...selectStyle} aria-label="Object type icon" value={definition.icon || undefined} placeholder="Default" options={opt(['box', 'database', 'globe', 'map-pin', 'users', 'document'])} onChange={(icon: string) => set({ icon })} /></label>
            <label className="flow-create-field"><span>Color</span><Input value={definition.color} placeholder="#3b82f6" onChange={event => set({ color: event.target.value })} /></label>
            <label className="flow-create-field"><span>Groups (comma-separated)</span><Input key={saved?.id || 'new-groups'} defaultValue={definition.groups.join(', ')} onBlur={event => { const groups = [...new Set(event.target.value.split(',').map(value => value.trim()).filter(Boolean))]; if (JSON.stringify(groups) !== JSON.stringify(definition.groups)) set({ groups }); }} placeholder="Aviation, infrastructure" /></label>
            <label className="flow-create-field is-wide"><span>Description</span><Input.TextArea rows={4} value={definition.description} onChange={event => set({ description: event.target.value })} /></label>
          </div>
        </section>}
        {stage === 'wizard' && step === 2 && <section className="flow-create-section">
          <Heading title="Create properties" detail={existing ? 'Map source columns to typed object properties and choose the identifier and title.' : 'Define typed properties without a backing dataset.'} />
          <div className="flow-create-grid flow-create-keys">
            <label className="flow-create-field"><span>Primary key</span><Select {...selectStyle} aria-label="Primary key properties" mode="multiple" options={primaryOptions} value={definition.primaryKey} onChange={(primaryKey: string[]) => set({ primaryKey, properties: definition.properties.map(item => primaryKey.includes(item.id) ? { ...item, required: true } : item) })} placeholder="Choose one or more properties" /></label>
            <label className="flow-create-field"><span>Title property</span><Select {...selectStyle} aria-label="Title property" options={titleOptions} value={definition.titleKey || undefined} onChange={(titleKey: string) => set({ titleKey })} placeholder="Choose a readable title" /></label>
          </div>
          <div className="flow-create-property-heading"><strong>Properties ({definition.properties.length})</strong><div className="flow-semantic-buttons">
            {existing && !definition.properties.length && <Button size="small" disabled={!preview} onClick={mapSource}>Map source columns</Button>}
            <Button size="small" icon={<PlusOutlined />} onClick={() => set({ properties: [...definition.properties, { id: '', name: '', type: 'string', required: false }] })}>Add property</Button>
          </div></div>
          <Table<SemanticProperty> className="flow-create-table flow-semantic-props" scroll={{ x: 780 }} size="small" pagination={false} rowKey={(_: SemanticProperty, index?: number) => index ?? 0} dataSource={definition.properties} columns={[
            ...(existing ? [{ title: 'Source column', width: 152, render: (_: unknown, item: SemanticProperty, index: number) => <Select {...selectStyle} aria-label={'Source column ' + (index + 1)} options={sourceCols} showSearch value={item.sourceColumn || undefined} onChange={(sourceColumn: string) => updateProperty(index, { sourceColumn })} /> }] : []),
            { title: 'Property ID', width: 160, render: (_, item, index) => <Input aria-label={'Property ID ' + (index + 1)} value={item.id} onChange={event => updateProperty(index, { id: event.target.value })} /> },
            { title: 'Display name', width: 185, render: (_, item, index) => <Input aria-label={'Property name ' + (index + 1)} value={item.name} onChange={event => updateProperty(index, { name: event.target.value })} /> },
            { title: 'Type', width: 125, render: (_, item, index) => <Select {...selectStyle} aria-label={'Property type ' + (index + 1)} value={item.type} options={opt(typeOptions)} onChange={(type: string) => updateProperty(index, { type })} /> },
            { title: 'Required', width: 75, render: (_, item, index) => <Checkbox aria-label={'Required property ' + (index + 1)} checked={item.required} disabled={definition.primaryKey.includes(item.id)} onChange={event => updateProperty(index, { required: event.target.checked })} /> },
            { title: '', width: 44, render: (_, _item, index) => <span className="flow-create-row-actions"><button type="button" aria-label={'Remove property ' + (index + 1)} onClick={() => removeProperty(index)}><DeleteOutlined /></button></span> },
          ]} />
          {existing && <section className="flow-semantic-saved">
            <div className="flow-create-property-heading"><strong>Source sample</strong><Button size="small" disabled={!preview} loading={busy} onClick={loadRows}>Load 5 rows</Button></div>
            <p className="flow-create-help">Read-only source records are shown here only to an authorized model builder.</p>
            {!!rows.data && <Table<Record<string, unknown>> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey={(_, index) => index ?? 0} dataSource={rows.data.rows} columns={rows.data.columns.map(column => ({ title: column, render: (_, row) => <span title={display(row[column])}>{display(row[column]).slice(0, 120)}</span> }))} />}
          </section>}
        </section>}
        {stage === 'wizard' && step === 3 && <section className="flow-create-section">
          <Heading title="Generate action types" detail="Choose how authorized users can create, modify or delete object instances." />
          {existing ? <div className="flow-create-notice">Source-backed objects are read-only. Actions cannot change records in the source table.</div> : <>
            <div className="flow-create-grid">{projectInput}</div>
            {principals.error && <p className="flow-create-error" role="alert">{principals.error}<Button size="small" onClick={retries}>Retry</Button></p>}
            <div className="flow-semantic-saved">{actionTypes.map(kind => {
              const action = definition.actions.find(item => item.kind === kind);
              const fields = kind === 'modify' ? propertyOptions.filter(item => !definition.primaryKey.includes(item.value)) : propertyOptions;
              return <section key={kind} className="flow-semantic-config">
                <Checkbox checked={!!action} onChange={event => toggleAction(kind, event.target.checked)}>{kind[0].toUpperCase() + kind.slice(1)} objects</Checkbox>
                {action && <div className="flow-create-grid">
                  <label className="flow-create-field"><span>Submitters</span><Select {...selectStyle} aria-label={kind + ' action submitters'} mode="multiple" value={action.submitters.map(principalKey)} options={principalOptions} loading={principals.loading} disabled={!definition.projectId || principals.loading} onChange={(keys: string[]) => changeAction(kind, { submitters: principalList(keys) })} placeholder="Select users, groups or organization" /></label>
                  {kind !== 'delete' && <label className="flow-create-field"><span>Editable properties</span><Select {...selectStyle} aria-label={kind + ' action properties'} mode="multiple" value={action.fields} options={fields} onChange={(fields: string[]) => changeAction(kind, { fields })} /></label>}
                </div>}
              </section>;
            })}</div>
          </>}
        </section>}
        {stage === 'wizard' && step === 4 && <section className="flow-create-section">
          <Heading title="Choose where to save" detail="The chosen project owns the definition. The source table stays where it is." />
          <div className="flow-create-grid">{projectInput}<div className="flow-create-field"><span>Resource</span><div className="flow-create-value">{definition.id || 'Object type'}</div></div></div>
          {projects.error && <p className="flow-create-error" role="alert">{projects.error}<Button size="small" onClick={retries}>Retry</Button></p>}
          <p className="flow-create-help">Saving a draft does not publish a resource or alter its data source.</p>
        </section>}
        {stage === 'wizard' && step === 5 && <section className="flow-create-section">
          <Heading title="Configure security policies" detail="Select who can read objects and add restrictions for individual properties." />
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Object security policy</span><Select {...selectStyle} aria-label="Object security policy" options={[...(existing ? [{ value: 'inherit', label: 'Inherit source access' }] : [{ value: 'explicit', label: 'Explicit readers' }])]} value={definition.security.mode} onChange={(mode: 'inherit' | 'explicit') => set({ security: { ...definition.security, mode, objectReaders: [] } })} /></label>
            {definition.security.mode === 'explicit' && <label className="flow-create-field"><span>Object readers</span><Select {...selectStyle} aria-label="Object readers" mode="multiple" options={principalOptions} loading={principals.loading} value={definition.security.objectReaders.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, objectReaders: principalList(keys) } })} placeholder="Choose who can read objects" /></label>}
          </div>
          {principals.error && <p className="flow-create-error" role="alert">{principals.error}<Button size="small" onClick={retries}>Retry</Button></p>}
          <div className="flow-semantic-saved">
            <div className="flow-create-property-heading"><strong>Property policies</strong><Select {...selectStyle} aria-label="Add a property policy" value={undefined} placeholder="Add restriction" options={propertyOptions.filter(item => !Object.prototype.hasOwnProperty.call(definition.security.fieldReaders, item.value))} onChange={(id: string) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: [] } } })} /></div>
            {Object.entries(definition.security.fieldReaders).map(([id, readers]) => <div key={id} className="flow-semantic-config">
              <div className="flow-create-property-heading"><strong>{id}</strong><Button size="small" onClick={() => set({ security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).filter(([field]) => field !== id)) } })}>Remove policy</Button></div>
              <Select {...selectStyle} aria-label={id + ' property readers'} mode="multiple" options={principalOptions} value={readers.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: principalList(keys) } } })} placeholder="Choose who can read this property" />
            </div>)}
          </div>
          <div className="flow-semantic-saved">
            <h4>Test access as a project user</h4>
            <div className="flow-create-grid"><Select {...selectStyle} aria-label="Test policy user" showSearch options={userOptions} value={testPrincipal || undefined} onChange={(id: string) => { setTestPrincipal(id); setPolicyResult(undefined); }} placeholder="Select a project user" /><Button loading={busy} disabled={!testPrincipal} onClick={test}>Test policy</Button></div>
            {policyResult && <p className="flow-create-notice" role="status">{policyResult.visible ? 'Object visible. Visible properties: ' + (policyResult.visibleFields.join(', ') || 'none') + '.' : 'Object hidden for this user.'}</p>}
          </div>
        </section>}
        {stage === 'review' && <section className="flow-create-section">
          <Heading title="Review and publish" detail="Review server validation for this saved revision before publishing the object type." />
          {busy && !validation && <p role="status">Checking draft…</p>}
          {canPublish && <p className="flow-create-notice" role="status">Validation passed for revision {saved?.revision}. Review any warnings before publishing.</p>}
          {validation && <><IssueList title="Blocking errors" issues={validation.errors} /><IssueList title="Warnings" issues={validation.warnings} /></>}
          <dl className="flow-create-summary">
            <dt>Object type</dt><dd>{definition.name} · {definition.id}</dd><dt>Project</dt><dd>{projects.data?.find(item => item.id === definition.projectId)?.name || definition.projectId}</dd>
            <dt>Datasource</dt><dd>{existing ? sourceKey : 'Unbacked object type'}</dd><dt>Primary key</dt><dd>{definition.primaryKey.join(', ')}</dd>
            <dt>Title property</dt><dd>{definition.titleKey}</dd><dt>Actions</dt><dd>{definition.actions.map(action => action.kind).join(', ') || 'None'}</dd>
            <dt>Access policy</dt><dd>{definition.security.mode === 'inherit' ? 'Inherit source access' : 'Explicit readers'} · {Object.keys(definition.security.fieldReaders).length} property restrictions</dd>
          </dl>
          <Table<SemanticProperty> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey="id" dataSource={definition.properties} columns={[{ title: 'ID', dataIndex: 'id' }, { title: 'Display name', dataIndex: 'name' }, { title: 'Type', dataIndex: 'type' }, ...(existing ? [{ title: 'Source', dataIndex: 'sourceColumn' }] : [])]} />
        </section>}
        </div>
      </div>
    </div>
  </Modal>;
}

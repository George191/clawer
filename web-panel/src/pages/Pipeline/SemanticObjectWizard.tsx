import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select, Table } from '@/components/ui';
import { CheckOutlined, ReloadOutlined } from '@/components/ui/icons';
import { fetchEtlObjectPreview, fetchLayerTables } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { EtlObjectPreview, LayerNode, LayerTable } from '../../services/types';
import {
  createOntologyDraft, createOntologyProject, fetchOntologyDraft, listOntologyDrafts, listOntologyPrincipals, listOntologyProjects,
  publishOntologyDraft, testOntologyPolicy, updateOntologyDraft, validateOntologyDraft,
  type OntologyDraft, type OntologyIssue, type OntologyPrincipal, type OntologyProject, type OntologyValidation,
  type SemanticDefinition, type SemanticProperty, type SemanticAction, type SemanticSource, type Principal, type PublishedOntologyType,
} from '../../services/ontology';
import SemanticPropertiesWorkbench from './SemanticPropertiesWorkbench';
import OntologyRecordPolicyEditor from './OntologyRecordPolicyEditor';
import './ontology-create.css';

const steps = ['Datasource', 'Metadata', 'Properties', 'Actions', 'Save location', 'Security'] as const;
const idPattern = /^[a-z][a-z0-9_-]{0,62}$/;
const warehouseSchemas = new Set(['ts_ods', 'ts_dwd']);
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };
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
  const [projectName, setProjectName] = useState('');
  const [principals, setPrincipals] = useState<Load<OntologyPrincipal[]>>({});
  const [drafts, setDrafts] = useState<Load<OntologyDraft[]>>({ loading: true });
  const [retry, setRetry] = useState(0);
  const [tables, setTables] = useState<Load<LayerTable[]> & { key?: string }>({});
  const [sourcePreview, setSourcePreview] = useState<Load<EtlObjectPreview> & { key?: string }>({});
  const [validation, setValidation] = useState<OntologyValidation>();
  const [checkedRevision, setCheckedRevision] = useState<number>();
  const [testPrincipal, setTestPrincipal] = useState('');
  const [policyResult, setPolicyResult] = useState<{ visible: boolean; visibleFields: string[] }>();
  const existing = definition.source.mode === 'existing';
  const sourceKey = existing && definition.source.schema && definition.source.table ? path(definition.source.schema, definition.source.table) : '';
  const preview = sourceKey && sourcePreview.key === sourceKey ? sourcePreview.data : undefined;
  const dirty = JSON.stringify(saved?.definition ?? initial.current) !== JSON.stringify(definition);
  const selectedProject = projects.data?.find(item => item.id === definition.projectId);
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
    setPolicyResult(undefined); setTestPrincipal(''); setProjectName(''); setStep(0); setStage('wizard'); setStatus('Draft revision ' + item.revision + ' loaded.');
  }));
  const sourceChange = (source: SemanticSource) => set({ source, properties: [], primaryKey: [], titleKey: '', actions: [], security: { mode: source.mode === 'existing' ? 'inherit' : 'explicit', objectReaders: [], fieldReaders: {} } });
  const projectChange = (projectId: string) => {
    setTestPrincipal('');
    setProjectName('');
    set({ projectId, actions: definition.actions.map(action => ({ ...action, submitters: [] })), security: { mode: definition.security.mode, objectReaders: [], fieldReaders: {} } });
  };
  const nameProject = () => void run(async () => {
    if (!selectedProject?.canName || !projectName.trim()) return;
    try {
      const named = await createOntologyProject(selectedProject.id, projectName.trim());
      if (current()) {
        setProjects(previous => ({ ...previous, data: previous.data?.map(project => project.id === named.id ? named : project) }));
        setProjectName('');
        setStatus('Project name saved.');
      }
    } catch (failure) {
      if (current()) {
        setError(problem(failure, 'Unable to save the project name.'));
        if (fromError(failure) === 409) setRetry(value => value + 1);
      }
    }
  });
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
  const changeAction = (kind: SemanticAction['kind'], next: Partial<SemanticAction>) => set({ actions: definition.actions.map(action => action.kind === kind ? { ...action, ...next } : action) });
  const toggleAction = (kind: SemanticAction['kind'], checked: boolean) => {
    const fields = kind === 'create' ? definition.properties.filter(item => item.required).map(item => item.id) : [];
    set({ actions: checked ? [...definition.actions, { kind, submitters: [], fields }] : definition.actions.filter(action => action.kind !== kind) });
  };
  const projectOptions = (projects.data || []).map(item => ({ value: item.id, label: item.ownerId === null ? `${item.teamName} · Unnamed project` : `${item.name} · Team ${item.teamName}` }));
  const principalOptions = (principals.data || []).map(principal => ({ value: principalKey(principal), label: principal.name + ' · ' + principal.kind }));
  const principalList = (keys: string[]) => (principals.data || []).filter(principal => keys.includes(principalKey(principal))).map(({kind, id}) => ({kind, id}));
  const userOptions = (principals.data || []).filter(principal => principal.kind === 'user').map(principal => ({ value: principal.id, label: principal.name }));
  const schemaOptions = schemas.filter(schema => warehouseSchemas.has(schema.schemaName || schema.key)).map(schema => ({ value: schema.schemaName || schema.key, label: schema.alias ? schema.alias + ' · ' + (schema.schemaName || schema.key) : schema.schemaName || schema.key }));
  const retries = () => setRetry(value => value + 1);
  const projectInput = <div>
    <label className="flow-create-field"><span>Ontology project</span><Select {...selectStyle} aria-label="Destination project" showSearch options={projectOptions} value={definition.projectId || undefined} loading={projects.loading} disabled={busy || projects.loading} placeholder="Select a project" onChange={projectChange} /></label>
    {projects.error && <p className="flow-create-error" role="alert">{projects.error}<Button size="small" onClick={retries}>Retry</Button></p>}
    {selectedProject?.canName && <div className="flow-semantic-config">
      <label className="flow-create-field"><span>New project name</span><Input size="small" value={projectName} maxLength={64} disabled={busy} onChange={event => setProjectName(event.target.value)} /></label>
      <p className="flow-create-help">Saving the name records you as the project owner.</p>
      <Button size="small" disabled={busy || !projectName.trim()} onClick={nameProject}>Save project name</Button>
    </div>}
  </div>;
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
          <Heading title="Choose a datasource" detail="An object type describes one kind of business record, such as an airport. Define its properties and access rules here, then publish the definition to a project so its readers can find it in Published." />
          <fieldset className="flow-create-source"><legend>Datasource option</legend>
            <label className="flow-create-source-option"><input type="radio" name="semantic-source" checked={existing} disabled={busy || !allowedSource} onChange={() => sourceChange({ mode: 'existing', schema: warehouseSchemas.has(initialSchema || '') ? initialSchema : '', table: '' })} /><span><strong>Existing PostgreSQL table</strong><small>Each source row is a read-only object. Choose which columns appear as properties.</small></span></label>
            <label className="flow-create-source-option"><input type="radio" name="semantic-source" checked={!existing} disabled={busy} onChange={() => sourceChange({ mode: 'none' })} /><span><strong>No backing datasource</strong><small>Start with an empty set of records. Enable a Create action if users should add objects after publication.</small></span></label>
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
        {stage === 'wizard' && step === 2 && <SemanticPropertiesWorkbench key={sourceKey || 'unbacked'}
          definition={definition} preview={preview || undefined} busy={busy} onAutoMap={mapSource}
          onChange={updateProperty} onRemove={removeProperty}
          onAdd={() => set({ properties: [...definition.properties, { id: '', name: '', type: 'string', required: false }] })}
          onPrimary={(key, enabled) => set({
            primaryKey: enabled ? [...definition.primaryKey, key] : definition.primaryKey.filter(item => item !== key),
            properties: enabled ? definition.properties.map(item => item.id === key ? { ...item, required: true } : item) : definition.properties,
          })}
          onTitle={(titleKey) => set({ titleKey })} />}
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
          <Heading title="Choose the project for this object type" detail="The project owns the definition. Review access for its members in the Security step." />
          <div className="flow-create-grid">{projectInput}<div className="flow-create-field"><span>Resource</span><div className="flow-create-value">{definition.id || 'Object type'}</div></div></div>
          <p className="flow-create-help">Save draft lets you resume this work later. Select Publish object type on the review screen to add the definition to Published.</p>
        </section>}
        {stage === 'wizard' && step === 5 && <section className="flow-create-section">
          <Heading title="Configure security policies" detail="Select who can read objects and add restrictions for individual properties." />
          <div className="flow-create-grid">
            <label className="flow-create-field"><span>Object security policy</span><Select {...selectStyle} aria-label="Object security policy" options={[...(existing ? [{ value: 'inherit', label: 'Inherit source access' }] : [{ value: 'explicit', label: 'Explicit readers' }])]} value={definition.security.mode} onChange={(mode: 'inherit' | 'explicit') => set({ security: { ...definition.security, mode, objectReaders: [] } })} /></label>
            {definition.security.mode === 'explicit' && <label className="flow-create-field"><span>Object readers</span><Select {...selectStyle} aria-label="Object readers" mode="multiple" options={principalOptions} loading={principals.loading} value={definition.security.objectReaders.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, objectReaders: principalList(keys) } })} placeholder="Choose who can read objects" /></label>}
          </div>
          {principals.error && <p className="flow-create-error" role="alert">{principals.error}<Button size="small" onClick={retries}>Retry</Button></p>}
          <OntologyRecordPolicyEditor definition={definition} principals={principals.data || []} onChange={security => set({security})} />
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
            {policyResult && <p className="flow-create-notice" role="status">{policyResult.visible
              ? 'Object type visible. Eligible properties: ' + (policyResult.visibleFields.join(', ') || 'none') + '.' +
                (definition.security.recordRules?.length ? ' Each record is checked separately against this user’s identity, its property values and its relationships.' : '')
              : 'Object type hidden for this user.'}</p>}
          </div>
        </section>}
        {stage === 'review' && <section className="flow-create-section">
          <Heading title={`Publish “${definition.name}” to ${selectedProject?.name || definition.projectId}?`} detail="This publishes the object type definition, including its properties, keys, actions and access rules. Project members with read access can open it in Ontology → Published." />
          {busy && !validation && <p role="status">Checking draft…</p>}
          {canPublish && <p className="flow-create-notice" role="status">Validation passed for revision {saved?.revision}. Review any warnings before publishing.</p>}
          {validation && <><IssueList title="Blocking errors" issues={validation.errors} /><IssueList title="Warnings" issues={validation.warnings} /></>}
          <dl className="flow-create-summary">
            <dt>Object type</dt><dd>{definition.name} · {definition.id}</dd><dt>Ontology project</dt><dd>{selectedProject?.name || definition.projectId}</dd>
            {selectedProject && <><dt>Team</dt><dd>{selectedProject.teamName}</dd></>}
            <dt>Records after publication</dt><dd>{existing ? `Read-only rows from ${sourceKey}` : 'Records are managed in the ontology. Publishing the definition creates zero records.'}</dd><dt>Primary key</dt><dd>{definition.primaryKey.join(', ')}</dd>
            <dt>Title property</dt><dd>{definition.titleKey}</dd><dt>Actions</dt><dd>{definition.actions.map(action => action.kind).join(', ') || 'None'}</dd>
            <dt>Who can read this type</dt><dd>{existing ? 'Project members with platform administrator access' : definition.security.objectReaders.map(reader => principalOptions.find(option => option.value === principalKey(reader))?.label || principalKey(reader)).join(', ')}</dd>
            <dt>Additional restrictions</dt><dd>{definition.security.markings?.length || 0} security markings · {definition.security.recordRules?.length || 0} record rules · {Object.keys(definition.security.fieldReaders).length} property restrictions</dd>
          </dl>
          <Table<SemanticProperty> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey="id" dataSource={definition.properties} columns={[{ title: 'ID', dataIndex: 'id' }, { title: 'Display name', dataIndex: 'name' }, { title: 'Type', dataIndex: 'type' }, ...(existing ? [{ title: 'Source', dataIndex: 'sourceColumn' }] : [])]} />
        </section>}
        </div>
      </div>
    </div>
  </Modal>;
}

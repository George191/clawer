import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select, Table } from '@/components/ui';
import { AppstoreOutlined, CheckOutlined, ReloadOutlined, TableOutlined } from '@/components/ui/icons';
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
import SemanticTypeAppearance from './SemanticTypeAppearance';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import { explorerPropertyType } from '../../services/explorerProperties';
import { ontologyTypeIcons } from './ontologyTypeIcons';
import OntologyRecordPolicyEditor from './OntologyRecordPolicyEditor';
import './ontology-create.css';
import './semantic-object-wizard.css';
import './ontology-security.css';

const allSteps = ['Datasource', 'Metadata', 'Properties', 'Actions', 'Save location', 'Security'] as const;
const idPattern = /^[a-z][a-z0-9_-]{0,62}$/;
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };
const actionTypes: SemanticAction['kind'][] = ['create', 'modify', 'delete'];
const principalKey = (p: Principal) => p.kind + ':' + p.id;
const opt = (values: readonly string[]) => values.map(value => ({ value, label: value }));
const path = (schema: string, table: string) => [schema, table].filter(Boolean).join('.');
const emptyDefinition = (schema = '', allowedSource = true): SemanticDefinition => ({
  id: '', name: '', pluralName: '', description: '', icon: '', color: '', groups: [], projectId: '',
  source: allowedSource ? { mode: 'existing', schema, table: '' } : { mode: 'none' }, properties: [], primaryKey: [], titleKey: '', actions: [],
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
function makeProperty(column: EtlObjectPreview['properties'][number]): SemanticProperty {
  return { id: column.name, name: column.displayName || column.name, type: explorerPropertyType(column.type), sourceColumn: column.name, required: !column.nullable };
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
  const sourceSchemas = schemas.filter(schema => schema.ontologySource);
  const startingSchema = sourceSchemas.some(schema => (schema.schemaName || schema.key) === initialSchema) ? initialSchema : '';
  const initial = useRef(emptyDefinition(startingSchema, allowedSource));
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
  const [securityView, setSecurityView] = useState('Object access');
  const existing = definition.source.mode === 'existing';
  const steps = existing ? allSteps.filter(label => label !== 'Actions') : allSteps;
  const currentStep = steps[step];
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
    if (existing && key) fetchLayerTables(key).then(data => { if (alive && current()) setTables({ key, data: data.filter(table => table.ontologySource) }); }, failure => { if (alive && current()) setTables({ key, error: problem(failure, 'Unable to load tables.') }); });
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
  const unrestrictedProperties = propertyOptions.filter(({value}) => !definition.primaryKey.includes(value) && !Object.prototype.hasOwnProperty.call(definition.security.fieldReaders, value));
  function stepError(label: typeof allSteps[number]) {
    if (label === 'Datasource' && existing && (!sourceKey || !preview)) return 'Choose a source table and load its columns.';
    if (label === 'Metadata' && (!idPattern.test(definition.id) || !definition.name.trim() || !definition.pluralName.trim())) return 'Enter a valid API ID and singular and plural display names.';
    if (label === 'Properties') {
      const ids = definition.properties.map(item => item.id);
      if (!ids.length || new Set(ids).size !== ids.length || definition.properties.some(item => !idPattern.test(item.id) || !item.name.trim() || existing && !preview?.properties.some(column => column.name === item.sourceColumn))) return 'Define distinct property IDs, display names and an existing source column for every mapped property.';
      if (!definition.primaryKey.length || definition.primaryKey.some(id => !definition.properties.some(item => item.id === id && item.required))) return 'Choose at least one required property as the primary key.';
      if (!definition.titleKey || !ids.includes(definition.titleKey)) return 'Choose a title property.';
    }
    if (label === 'Actions' && definition.actions.some(item => !item.submitters.length || (item.kind !== 'delete' && !item.fields.length))) return 'Choose submitters and writable fields for each selected action.';
    if (label === 'Security' && definition.security.mode === 'explicit' && !definition.security.objectReaders.length) return 'Select who can read this object type.';
    if (label === 'Save location' && !projects.data?.some(item => item.id === definition.projectId)) return 'Select an available project.';
    return '';
  }
  const next = () => {
    const msg = stepError(currentStep);
    if (msg) { setError(msg); panel.current?.scrollTo({ top: 0 }); return; }
    if (currentStep === 'Datasource' && !definition.properties.length && existing) mapSource();
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
      const message = stepError(steps[index]);
      if (message) {
        if (steps[index] === 'Security') setSecurityView('Object access');
        setStep(index); setError(message); return;
      }
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
  const schemaOptions = sourceSchemas.map(schema => ({ value: schema.schemaName || schema.key, label: schema.alias ? schema.alias + ' · ' + (schema.schemaName || schema.key) : schema.schemaName || schema.key }));
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
  return <Modal open centered className="flow-surface-modal flow-create-modal flow-create-object flow-semantic-wizard" title={stage === 'wizard' ? 'Create a new object type' : 'Review object type changes'} width={1160} onCancel={() => guards(onCancel)} maskClosable={!busy} closable={!busy} keyboard={!busy} footer={<>
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
        {(stage === 'wizard' ? steps : ['Review changes'] as const).map((label, index) => <li key={label} className={stage === 'review' || index === step ? 'is-current' : index < step ? 'is-complete' : ''} aria-current={stage === 'review' || index === step ? 'step' : undefined}>
          <span aria-hidden="true">{stage === 'wizard' && index < step ? <CheckOutlined /> : index + 1}</span>
          {stage === 'wizard' && index < step
            ? <button type="button" disabled={busy} aria-label={`Return to ${label}`} onClick={() => { setStep(index); setError(''); }}>{label}</button>
            : <span>{label}</span>}
        </li>)}
      </ol>
      <div className="flow-create-step" role="region" aria-label={stage === 'wizard' ? steps[step] + ' step' : 'Publish review'} ref={panel}>
        {error && <div className="flow-create-error" role="alert">{error}</div>}
        {status && <p className="flow-semantic-status" role="status">{status}</p>}
        <p className="flow-create-kicker">{stage === 'review' ? 'Publish review' : 'Step ' + (step + 1) + ' of ' + steps.length}</p>
        <div ref={stageContent}>
        {stage === 'wizard' && currentStep === 'Datasource' && <section className="flow-create-section">
          <Heading title="Choose a datasource" detail="An object type describes one kind of business record, such as an airport. Define its properties and access rules here, then publish the definition to a project so its readers can find it in Published." />
          <fieldset className="flow-create-source"><legend>Datasource</legend>
            <label className="flow-create-source-option">
              <input type="radio" name="semantic-source" checked={!existing} disabled={busy} onChange={() => sourceChange({ mode: 'none' })} />
              <AppstoreOutlined aria-hidden="true" /><span><strong>No backing datasource</strong><small>Start with an empty set of records. Enable a Create action if users should add objects after publication.</small></span><CheckOutlined aria-hidden="true" />
            </label>
            <label className="flow-create-source-option">
              <input type="radio" name="semantic-source" checked={existing} disabled={busy || !allowedSource} onChange={() => sourceChange({ mode: 'existing', schema: startingSchema, table: '' })} />
              <TableOutlined aria-hidden="true" /><span><strong>Existing PostgreSQL table</strong><small>Each source row is a read-only object. Choose which columns appear as properties.</small></span><CheckOutlined aria-hidden="true" />
            </label>
          </fieldset>
          {!allowedSource && <p className="flow-create-help">A platform administrator is required to bind a warehouse table.</p>}
          {existing && <div className="flow-create-grid">
            <label className="flow-create-field"><span>Schema</span><Select {...selectStyle} aria-label="Object type schema" value={definition.source.schema || undefined} options={schemaOptions} disabled={busy} placeholder="Select a schema" onChange={(schema: string) => sourceChange({ mode: 'existing', schema, table: '' })} /></label>
            <label className="flow-create-field"><span>Source table</span><Select {...selectStyle} aria-label="Source table" showSearch value={definition.source.table || undefined} disabled={busy || tables.loading || !definition.source.schema || tables.key !== definition.source.schema} options={opt((tables.key === definition.source.schema ? tables.data : [])?.map(item => item.name) || [])} placeholder="Select a table" onChange={(table: string) => sourceChange({ ...definition.source, table })} /></label>
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
        {stage === 'wizard' && currentStep === 'Metadata' && <section className="flow-create-section">
          <Heading title="Object type metadata" detail="Business display names are separate from the stable API ID." />
          <div className="flow-create-grid flow-type-metadata">
            <div className="flow-metadata-identity">
              <div className="flow-create-field"><span>Icon</span>
                <Select aria-label="Object type icon" className="flow-ontology__select flow-object-icon-select"
                  popupClassName="flow-ontology__select-popup flow-object-icon-popup" popupMatchSelectWidth={216}
                  value={definition.icon} onChange={icon => set({ icon })}
                  options={['', ...Object.keys(ontologyTypeIcons)].map(icon => ({
                    value: icon, label: <span title={icon || 'Automatic'} className="flow-object-icon-choice">
                      <SemanticTypeGlyph definition={{ ...definition, icon }} />
                      <span className="flow-published-sr-only">{icon || 'Automatic'}</span>
                    </span>,
                  }))} />
              </div>
              <label className="flow-create-field"><span>Display name</span><Input maxLength={120} value={definition.name} onChange={event => set({ name: event.target.value })} placeholder="Airport" /></label>
              <label className="flow-create-field"><span>Plural display name</span><Input aria-label="Plural display name" maxLength={120} value={definition.pluralName} onChange={event => set({ pluralName: event.target.value })} placeholder="Airports" /><small>Collection title in Published. For Airport, use Airports.</small></label>
            </div>
            <label className="flow-create-field is-wide"><span>Description</span><Input.TextArea rows={4} value={definition.description} onChange={event => set({ description: event.target.value })} /></label>
            <SemanticTypeAppearance definition={definition} drafts={drafts.data || []} onChange={set} />
          </div>
        </section>}
        {stage === 'wizard' && currentStep === 'Properties' && <SemanticPropertiesWorkbench key={sourceKey || 'unbacked'}
          definition={definition} preview={preview || undefined} busy={busy} onAutoMap={mapSource}
          onChange={updateProperty} onRemove={removeProperty}
          onAdd={() => set({ properties: [...definition.properties, { id: '', name: '', type: 'string', required: false }] })}
          onPrimary={(key, enabled) => set({
            primaryKey: enabled ? [...definition.primaryKey, key] : definition.primaryKey.filter(item => item !== key),
            properties: enabled ? definition.properties.map(item => item.id === key ? { ...item, required: true } : item) : definition.properties,
          })}
          onTitle={(titleKey) => set({ titleKey })} />}
        {stage === 'wizard' && currentStep === 'Actions' && <section className="flow-create-section">
          <Heading title="Generate action types" detail="Choose how authorized users can create, modify or delete object instances." />
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
        </section>}
        {stage === 'wizard' && currentStep === 'Save location' && <section className="flow-create-section">
          <Heading title="Choose the project for this object type" detail="The project owns the definition. Review access for its members in the Security step." />
          <div className="flow-create-grid">{projectInput}<div className="flow-create-field"><span>Resource</span><div className="flow-create-value">{definition.id || 'Object type'}</div></div></div>
          <p className="flow-create-help">Each team has one ontology project. Select an unnamed project here to give it a name. Teams are managed in <a href="/organization?view=teams" target="_blank" rel="noreferrer">Access → Teams</a>.</p>
          <p className="flow-create-help">Save draft lets you resume this work later. Select Publish object type on the review screen to add the definition to Published.</p>
        </section>}
        {stage === 'wizard' && currentStep === 'Security' && <section className="flow-create-section">
          <Heading title="Security policies" detail="Set access to this object type and preview the properties a project user can read." />
          <nav className="flow-security-nav" aria-label="Security settings">{['Object access', 'Record access', 'Property access', 'Test access'].map(view => <button type="button" key={view} aria-pressed={securityView === view} onClick={() => setSecurityView(view)}>{view}</button>)}</nav>
          <div className="flow-security-panel">
          {securityView === 'Object access' && <>
            <h4>Object security policy</h4>
            <p className="flow-create-help">{existing ? 'This type inherits source access. Project members with platform administrator access can read the mapped rows, subject to any additional restrictions.' : 'Choose users, teams or organizations whose project members can read this type.'}</p>
            {definition.security.mode === 'explicit' && <label className="flow-create-field"><span>Object readers</span><Select {...selectStyle} aria-label="Object readers" mode="multiple" options={principalOptions} loading={principals.loading} value={definition.security.objectReaders.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, objectReaders: principalList(keys) } })} placeholder="Choose who can read objects" /></label>}
          </>}
          {principals.error && <p className="flow-create-error" role="alert">{principals.error}<Button size="small" onClick={retries}>Retry</Button></p>}
          {securityView === 'Record access' && <OntologyRecordPolicyEditor definition={definition} principals={principals.data || []} onChange={security => set({security})} />}
          {securityView === 'Property access' && <>
            <h4>Property policies</h4>
            <p className="flow-create-help">{Object.keys(definition.security.fieldReaders).length ? 'Only the selected readers can see each restricted property.' : 'All properties currently inherit object access.'}</p>
            <label className="flow-create-field"><span>Restrict a property</span><Select {...selectStyle} aria-label="Add a property policy" value={undefined} disabled={!unrestrictedProperties.length} placeholder={unrestrictedProperties.length ? 'Choose a property' : 'No eligible properties'} options={unrestrictedProperties} onChange={(id: string) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: [] } } })} /></label>
            {Object.entries(definition.security.fieldReaders).map(([id, readers]) => <div key={id} className="flow-semantic-config">
              <div className="flow-create-property-heading"><strong>{id}</strong><Button size="small" onClick={() => set({ security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).filter(([field]) => field !== id)) } })}>Remove policy</Button></div>
              <Select {...selectStyle} aria-label={id + ' property readers'} mode="multiple" options={principalOptions} value={readers.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: principalList(keys) } } })} placeholder="Choose who can read this property" />
            </div>)}
          </>}
          {securityView === 'Test access' && <>
            <h4>Test access as a project user</h4>
            <div className="flow-security-test"><Select {...selectStyle} aria-label="Test policy user" showSearch options={userOptions} value={testPrincipal || undefined} onChange={(id: string) => { setTestPrincipal(id); setPolicyResult(undefined); }} placeholder="Select a project user" /><Button loading={busy} disabled={!testPrincipal} onClick={test}>Test policy</Button></div>
            {policyResult && <div className="flow-create-notice" role="status">
              <strong>{policyResult.visible ? 'Object type visible' : 'Object type hidden for this user'}</strong>
              {policyResult.visible && <><p>Eligible properties ({policyResult.visibleFields.length})</p>
                <ul>{policyResult.visibleFields.map(field => <li key={field}>{propertyOptions.find(option => option.value === field)?.label || field}</li>)}</ul>
                {!!definition.security.recordRules?.length && <p>Access to each record also depends on its values and relationships.</p>}</>}
            </div>}
          </>}
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

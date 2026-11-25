import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select, Table } from '@/components/ui';
import { AppstoreOutlined, CheckOutlined, ReloadOutlined, TableOutlined } from '@/components/ui/icons';
import { fetchEtlObjectPreview, fetchLayerTables, type WarehouseConnection } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { EtlObjectPreview, LayerNode, LayerTable } from '../../services/types';
import {
  createOntologyDraft, createOntologyProject, fetchOntologyDraft, listOntologyDrafts, listOntologyPrincipals, listOntologyProjects,
  publishOntologyDraft, testOntologyPolicy, updateOntologyDraft, validateOntologyDraft,
  type OntologyDraft, type OntologyIssue, type OntologyPrincipal, type OntologyProject, type OntologyValidation,
  type SemanticDefinition, type SemanticProperty, type SemanticAction, type SemanticSource, type Principal, type PublishedOntologyType,
} from '../../services/ontology';
import SemanticPropertiesWorkbench from './SemanticPropertiesWorkbench';
import OntologyIconPicker from './OntologyIconPicker';
import OntologySourceConnection from './OntologySourceConnection';
import SemanticTypeAppearance from './SemanticTypeAppearance';
import { initialProperties, propertyIssue, propertyRemovalReason, sourceProperties } from './semanticWizardProperties';
import OntologyMarkingsEditor from './OntologyMarkingsEditor';
import OntologyRecordPolicyEditor from './OntologyRecordPolicyEditor';
import './ontology-create.css';
import './semantic-object-wizard.css';
import './ontology-security.css';

const allSteps = ['Datasource', 'Metadata', 'Properties', 'Actions', 'Save location', 'Security'] as const;
const idPattern = /^[a-z][a-z0-9_-]{0,62}$/;
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };
const draftsPageSize = 100;
const actionTypes: SemanticAction['kind'][] = ['create', 'modify', 'delete'];
const principalKey = (p: Principal) => p.kind + ':' + p.id;
const principalLabel = (p: OntologyPrincipal) => p.email && p.email !== p.name ? p.name + ' · ' + p.email : p.name;
const policyPrincipals = (d: SemanticDefinition): Principal[] => [
  ...d.security.objectReaders, ...Object.values(d.security.fieldReaders).flat(),
  ...(d.security.recordRules || []).flatMap(rule => rule.readers), ...d.actions.flatMap(action => action.submitters),
];
const securityTabs = ['Object access', 'Record access', 'Property access', 'Test access'] as const;
const opt = (values: readonly string[]) => values.map(value => ({ value, label: value }));
const path = (schema: string, table: string) => [schema, table].filter(Boolean).join('.');
const emptyDefinition = (schema = '', allowedSource = true, upstreamConnectionId?: string, connectionId?: string): SemanticDefinition => ({
  id: '', name: '', pluralName: '', description: '', icon: '', color: '#ffffff', iconColor: '#000000', groups: [], projectId: '',
  source: allowedSource ? { mode: 'existing', schema, table: '', connectionId, upstreamConnectionId } : { mode: 'none' }, ...initialProperties(allowedSource), actions: [],
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
type Load<T> = { data?: T; error?: string; loading?: boolean };
function IssueList({ title, issues }: { title: string; issues: OntologyIssue[] }) {
  return issues.length ? <section className="flow-semantic-issues"><h4>{title} ({issues.length})</h4><ul>{issues.map((item, index) => <li key={index}><strong>{item.path || 'Definition'}</strong> {item.message}</li>)}</ul></section> : null;
}
const Heading = ({ title, detail }: { title: string; detail: string }) => <header className="flow-create-heading"><h3 tabIndex={-1}>{title}</h3><p>{detail}</p></header>;

type Props = {
  allowedSource: boolean; schemas: LayerNode[]; initialSchema?: string; initialConnectionId?: string; initialUpstreamConnectionId?: string;
  warehouse?: WarehouseConnection; warehouseFailed: boolean; onRetryWarehouse: () => void;
  onCancel: () => void; onPublished: (definition: PublishedOntologyType) => void;
};
export default function SemanticObjectWizard({ allowedSource, schemas, initialSchema, initialConnectionId, initialUpstreamConnectionId, warehouse, warehouseFailed, onRetryWarehouse, onCancel, onPublished }: Props) {
  const sourceScopes = schemas.flatMap(node => {
    if (!node.ontologySource || !node.connectionId || node.engine !== 'postgresql') return [];
    const schema = node.schemaName || node.key;
    return [{ key: JSON.stringify([node.connectionId, schema]), schema, connectionId: node.connectionId,
      label: [node.database || node.connectionId, node.alias, schema].filter(Boolean).join(' · ') }];
  });
  const startingScopes = sourceScopes.filter(scope => scope.schema === initialSchema && (!initialConnectionId || scope.connectionId === initialConnectionId));
  const startingScope = startingScopes.length === 1 ? startingScopes[0] : undefined;
  const initial = useRef(emptyDefinition(startingScope?.schema, allowedSource, initialUpstreamConnectionId, startingScope?.connectionId));
  const [definition, setDefinition] = useState(initial.current);
  const [connectionChosen, setConnectionChosen] = useState(!!initial.current.source.upstreamConnectionId);
  const [connectionAvailable, setConnectionAvailable] = useState(false);
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
  const [drafts, setDrafts] = useState<Load<OntologyDraft[]> & { nextOffset?: number }>({ loading: true });
  const [draftSearch, setDraftSearch] = useState('');
  const [draftOffset, setDraftOffset] = useState(0);
  const [draftVersion, setDraftVersion] = useState(0);
  const [retry, setRetry] = useState(0);
  const [tables, setTables] = useState<Load<LayerTable[]> & { key?: string }>({});
  const [sourcePreview, setSourcePreview] = useState<Load<EtlObjectPreview> & { key?: string }>({});
  const [validation, setValidation] = useState<OntologyValidation>();
  const [checkedRevision, setCheckedRevision] = useState<number>();
  const [testPrincipal, setTestPrincipal] = useState('');
  const [policyResult, setPolicyResult] = useState<{ visible: boolean; visibleFields: string[] }>();
  const [securityView, setSecurityView] = useState('Object access');
  const [principalQuery, setPrincipalQuery] = useState('');
  const existing = definition.source.mode === 'existing';
  const steps = existing ? allSteps.filter(label => label !== 'Actions') : allSteps;
  const currentStep = steps[step];
  const storageScope = existing ? sourceScopes.find(scope => scope.schema === definition.source.schema &&
    scope.connectionId === definition.source.connectionId) : undefined;
  const storageKey = storageScope?.key || '';
  const storageConnectionId = storageScope?.connectionId;
  const storageSchema = storageScope?.schema;
  const storageTable = definition.source.table;
  const sourcePath = storageSchema && storageTable ? path(storageSchema, storageTable) : '';
  const sourceKey = storageKey && storageTable ? JSON.stringify([storageKey, storageTable]) : '';
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
  useEffect(() => { if (stageContent.current) stageContent.current.inert = busy; }, [busy, step, stage]);
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
    setProjects({ loading: true });
    listOntologyProjects().then(data => { if (alive && current()) setProjects({ data }); }, failure => { if (alive && current()) setProjects({ error: problem(failure, 'Unable to load projects.') }); });
    return () => { alive = false; };
  }, [identity, retry]);
  useEffect(() => {
    const controller = new AbortController();
    setDrafts(previous => ({ data: draftOffset ? previous.data : [], loading: true }));
    const timer = window.setTimeout(async () => {
      try {
        const page = await listOntologyDrafts(draftSearch, draftOffset, draftsPageSize, controller.signal);
        if (!controller.signal.aborted && current()) setDrafts(previous => ({
          data: draftOffset ? [...new Map([...(previous.data || []), ...page.items].map(draft => [draft.id, draft])).values()] : page.items,
          nextOffset: page.hasMore ? draftOffset + page.items.length : undefined,
        }));
      } catch (failure) {
        if (!controller.signal.aborted && current()) setDrafts(previous => ({ data: previous.data, error: problem(failure, 'Unable to load saved drafts.') }));
      }
    }, draftSearch && !draftOffset ? 200 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [draftSearch, draftOffset, draftVersion, identity]);
  useEffect(() => {
    if (!definition.projectId) { setPrincipals({}); return; }
    const controller = new AbortController();
    setPrincipals({ loading: true });
    const load = async () => {
      const data = await listOntologyPrincipals(definition.projectId, '', controller.signal);
      if (saved?.definition.projectId === definition.projectId) {
        const loaded = new Set(data.filter(p => p.kind === 'user').map(p => p.id));
        const missing = [...new Set(policyPrincipals(saved.definition).filter(p => p.kind === 'user' && !loaded.has(p.id)).map(p => p.id))];
        for (let index = 0; index < missing.length; index += 100) {
          data.push(...await listOntologyPrincipals(definition.projectId, '', controller.signal, missing.slice(index, index + 100)));
        }
      }
      return data;
    };
    load().then(data => {
      if (!controller.signal.aborted && current()) setPrincipals(previous => ({ data: [...new Map([...data, ...(previous.data || [])].map(p => [principalKey(p), p])).values()] }));
    }, failure => {
      if (!controller.signal.aborted && current()) setPrincipals({ error: problem(failure, 'Unable to load project principals.') });
    });
    return () => controller.abort();
  }, [definition.projectId, saved?.id, identity, retry]);
  useEffect(() => {
    const search = principalQuery.trim().slice(0, 100);
    if (!search || !definition.projectId) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      listOntologyPrincipals(definition.projectId, search, controller.signal).then(items => {
        if (controller.signal.aborted || !current()) return;
        setPrincipals(previous => ({ data: [...new Map([...(previous.data || []), ...items].map(p => [principalKey(p), p])).values()] }));
      }, failure => {
        if (!controller.signal.aborted && current()) setPrincipals(previous => ({ ...previous, error: problem(failure, 'Unable to search project members.') }));
      });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [definition.projectId, identity, principalQuery, retry]);
  useEffect(() => {
    let alive = true;
    setTables({ key: storageKey, loading: !!storageKey });
    if (storageSchema && storageConnectionId) fetchLayerTables(storageSchema, 'current', undefined, undefined, storageConnectionId).then(
      data => { if (alive && current()) setTables({ key: storageKey, data: data.filter(table => table.ontologySource) }); },
      failure => { if (alive && current()) setTables({ key: storageKey, error: problem(failure, 'Unable to load tables.') }); },
    );
    return () => { alive = false; };
  }, [storageKey, storageSchema, storageConnectionId, identity, retry]);
  useEffect(() => {
    let alive = true;
    setSourcePreview({ key: sourceKey, loading: !!sourceKey });
    if (sourceKey && storageSchema && storageTable && storageConnectionId) fetchEtlObjectPreview(storageSchema, storageTable, true, undefined, storageConnectionId).then(
      data => { if (alive && current()) setSourcePreview({ key: sourceKey, data }); },
      () => { if (alive && current()) setSourcePreview({ key: sourceKey, error: 'Unable to load source columns.' }); },
    );
    return () => { alive = false; };
  }, [sourceKey, storageSchema, storageTable, storageConnectionId, identity, retry]);

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
    setConnectionChosen(item.definition.source.mode === 'existing');
    setSaved(item); setDefinition(item.definition); setValidation(undefined); setCheckedRevision(undefined);
    setPolicyResult(undefined); setTestPrincipal(''); setProjectName(''); setStep(0); setStage('wizard'); setStatus('Draft revision ' + item.revision + ' loaded.');
  }));
  const sourceChange = (source: SemanticSource) => set({ source, ...initialProperties(source.mode === 'existing'), actions: [], security: { mode: source.mode === 'existing' ? 'inherit' : 'explicit', objectReaders: [], fieldReaders: {} } });
  const projectChange = (projectId: string) => {
    if (projectId === definition.projectId) return;
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
    const properties = definition.properties.map((item, index) => index === position ? { ...item, ...value } : item);
    if (nextId === previous.id) { set({ properties }); return; }
    const isUnique = !!previous.id && !definition.properties.some((p, i) => i !== position && p.id === previous.id);
    const referenced = definition.primaryKey.includes(previous.id) || definition.titleKey === previous.id
      || definition.actions.some(action => action.fields.includes(previous.id))
      || definition.security.recordRules?.some(rule => rule.kind === 'property' && rule.field === previous.id)
      || Object.prototype.hasOwnProperty.call(definition.security.fieldReaders, previous.id);
    if (isUnique && referenced && (!nextId || definition.properties.some((p, i) => i !== position && p.id === nextId))) {
      setError('Property ' + (position + 1) + ' (' + previous.name + ') · Replace its API name with a nonempty, unique identifier.');
      return;
    }
    const remap = (item: string) => isUnique && item === previous.id ? nextId : item;
    set({
      properties,
      primaryKey: definition.primaryKey.map(remap), titleKey: remap(definition.titleKey),
      actions: definition.actions.map(action => ({ ...action, fields: action.fields.map(remap).filter(Boolean) })),
      security: {
        ...definition.security,
        fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).map(([id, readers]) => [remap(id), readers]).filter(([id]) => id)),
        recordRules: definition.security.recordRules?.map(rule => rule.kind === 'property' ? { ...rule, field: remap(rule.field) } : rule),
      },
    });
  };
  const removeProperty = (position: number) => {
    const property = definition.properties[position];
    if (propertyRemovalReason(definition, property, preview || undefined)) return;
    const {id} = property;
    set({ properties: definition.properties.filter((_, index) => index !== position),
      actions: definition.actions.map(action => ({ ...action, fields: action.fields.filter(field => field !== id) })),
      security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).filter(([field]) => field !== id)) },
    });
  };
  const mapSource = () => {
    if (!preview) return;
    set({ ...sourceProperties(preview), security: { ...definition.security, fieldReaders: {} } });
  };
  const propertyOptions = definition.properties.filter(item => idPattern.test(item.id)).map(item => ({ value: item.id, label: item.name ? item.name + ' · ' + item.id : item.id }));
  const unrestrictedProperties = propertyOptions.filter(({value}) => !definition.primaryKey.includes(value) && !Object.prototype.hasOwnProperty.call(definition.security.fieldReaders, value));
  function stepError(label: typeof allSteps[number]) {
    if (label === 'Datasource' && existing && !storageScope) return 'Select the backing connection and schema.';
    if (label === 'Datasource' && existing && warehouse && definition.source.upstreamConnectionId && !connectionAvailable) return 'Choose an available upstream connection or refresh the connection list.';
    if (label === 'Datasource' && existing && (!sourceKey || !preview)) return 'Choose the backing dataset and load its columns.';
    if (label === 'Metadata' && (!idPattern.test(definition.id) || !definition.name.trim() || !definition.pluralName.trim())) return 'Enter a valid API ID and singular and plural display names.';
    if (label === 'Properties') return propertyIssue(definition, preview || undefined)?.message || '';
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
      setDraftOffset(0); setDraftVersion(value => value + 1);
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
    setPolicyResult(undefined);
    const result = await testOntologyPolicy(definition, testPrincipal);
    if (current()) setPolicyResult(result);
  });
  const changeAction = (kind: SemanticAction['kind'], next: Partial<SemanticAction>) => set({ actions: definition.actions.map(action => action.kind === kind ? { ...action, ...next } : action) });
  const toggleAction = (kind: SemanticAction['kind'], checked: boolean) => {
    const fields = kind === 'create' ? definition.properties.filter(item => item.required).map(item => item.id) : [];
    set({ actions: checked ? [...definition.actions, { kind, submitters: [], fields }] : definition.actions.filter(action => action.kind !== kind) });
  };
  const projectOptions = (projects.data || []).map(item => ({ value: item.id, label: item.ownerId === null ? `${item.teamName} · Unnamed project` : `${item.name} · Team ${item.teamName}` }));
  const principalOptions: { value: string; label: string; disabled?: boolean }[] = (principals.data || []).map(principal => ({ value: principalKey(principal), label: principalLabel(principal) + ' · ' + principal.kind }));
  const available = new Set(principalOptions.map(option => option.value));
  for (const reader of policyPrincipals(definition)) {
    const value = principalKey(reader);
    if (!available.has(value)) {
      available.add(value);
      principalOptions.push({ value, label: principals.loading ? 'Loading name…' : 'Name unavailable · ' + reader.kind, disabled: true });
    }
  }
  const principalList = (keys: string[]): Principal[] => keys.map(key => {
    const [kind, id] = key.split(':');
    return { kind: kind as Principal['kind'], id };
  });
  const userOptions = (principals.data || []).filter(principal => principal.kind === 'user').map(principal => ({ value: principal.id, label: principalLabel(principal) }));
  const schemaOptions = sourceScopes.map(scope => ({ value: scope.key, label: scope.label }));
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
        <div key={stage === 'review' ? stage : currentStep} className="flow-semantic-step-content" ref={stageContent}>
        {stage === 'wizard' && currentStep === 'Datasource' && <section className="flow-create-section">
          <Heading title="Choose a datasource" detail="An object type describes one kind of business record, such as an airport. Define its properties and access rules here, then publish the definition to a project so its readers can find it in Published." />
          <fieldset className="flow-create-source"><legend>Datasource</legend>
            <label className="flow-create-source-option">
              <input type="radio" name="semantic-source" checked={!existing} disabled={busy} onChange={() => { setConnectionChosen(false); sourceChange({ mode: 'none' }); }} />
              <AppstoreOutlined aria-hidden="true" /><span><strong>No backing datasource</strong><small>Start with an empty set of records. Enable a Create action if users should add objects after publication.</small></span><CheckOutlined aria-hidden="true" />
            </label>
            <label className="flow-create-source-option">
              <input type="radio" name="semantic-source" checked={existing} disabled={busy || !allowedSource} onChange={() => sourceChange({ mode: 'existing', schema: startingScope?.schema || '', connectionId: startingScope?.connectionId, table: '' })} />
              <TableOutlined aria-hidden="true" /><span><strong>Existing dataset</strong><small>Choose a backing warehouse table. Each row becomes a read-only object.</small></span><CheckOutlined aria-hidden="true" />
            </label>
          </fieldset>
          {!allowedSource && <p className="flow-create-help">A platform administrator is required to bind a warehouse table.</p>}
          {existing && allowedSource && <div className="flow-create-grid">
            <label className="flow-create-field"><span>Backing connection and schema</span><Select {...selectStyle} aria-label="Backing connection and schema" value={storageKey || undefined} options={schemaOptions} disabled={busy} placeholder="Select a schema" onChange={(key: string) => {
              const scope = sourceScopes.find(item => item.key === key);
              if (scope && key !== storageKey) sourceChange({ ...definition.source, schema: scope.schema, connectionId: scope.connectionId, table: '' });
            }} /></label>
            <label className="flow-create-field"><span>Backing table</span><Select {...selectStyle} aria-label="Backing table" showSearch value={storageKey && storageTable || undefined} disabled={busy || tables.loading || !storageKey || tables.key !== storageKey} options={opt((tables.key === storageKey ? tables.data : [])?.map(item => item.name) || [])} placeholder="Select a table" onChange={(table: string) => { if (table !== storageTable) sourceChange({ ...definition.source, table }); }} /></label>
          </div>}
          {existing && allowedSource && !warehouse && warehouseFailed && definition.source.upstreamConnectionId &&
            <p className="flow-create-error" role="alert">Unable to load the upstream connection selector.<Button size="small" onClick={onRetryWarehouse}>Retry</Button></p>}
          {existing && allowedSource && warehouse && <OntologySourceConnection disabled={busy} onAvailabilityChange={setConnectionAvailable} warehouse={warehouse}
            value={connectionChosen ? definition.source.upstreamConnectionId || warehouse.id : undefined} onChange={id => {
              if (connectionChosen && id === (definition.source.upstreamConnectionId || warehouse.id)) return;
              setConnectionChosen(true);
              set({ source: { ...definition.source, upstreamConnectionId: id === warehouse.id ? undefined : id } });
            }}>
            {null}
          </OntologySourceConnection>}
          {(tables.error || sourcePreview.error) && <p className="flow-create-error" role="alert">{tables.error || sourcePreview.error}<Button size="small" icon={<ReloadOutlined />} onClick={retries}>Retry</Button></p>}
          {(tables.loading || sourcePreview.loading) && <p role="status" className="flow-create-help">Reading source metadata…</p>}
          {preview && <p className="flow-create-notice">{preview.properties.length} source columns available. Primary key: {preview.primaryKey.join(', ') || 'None declared'}.</p>}
          {(!!drafts.data?.length || draftSearch || drafts.error || drafts.loading) && <section className="flow-semantic-saved">
            <h4>Resume a draft</h4>
            <Select {...selectStyle} aria-label="Resume saved draft" showSearch filterOption={false} value={undefined} loading={drafts.loading} disabled={busy}
              placeholder="Choose a saved draft" notFoundContent={drafts.loading ? 'Loading drafts…' : 'No matching drafts'}
              onSearch={value => { setDraftSearch(value.trim()); setDraftOffset(0); }}
              onPopupScroll={event => {
                const { scrollHeight, scrollTop, clientHeight } = event.currentTarget;
                if (drafts.nextOffset !== undefined && scrollHeight - scrollTop - clientHeight < 32) setDraftOffset(drafts.nextOffset);
              }}
              options={(drafts.data || []).map(item => ({ value: item.id, label: (item.definition.name || item.definition.id) + ' · revision ' + item.revision }))}
              onChange={loadDraft} />
            {drafts.error && <p className="flow-create-error" role="alert">{drafts.error}<Button size="small" onClick={() => setDraftVersion(value => value + 1)}>Retry</Button></p>}
          </section>}
        </section>}
        {stage === 'wizard' && currentStep === 'Metadata' && <section className="flow-create-section">
          <Heading title="Object type metadata" detail="Business display names are separate from the stable API ID." />
          <div className="flow-create-grid">
            <div className="flow-metadata-identity">
              <div className="flow-create-field"><span>Icon</span><OntologyIconPicker definition={definition} onChange={set} /></div>
              <label className="flow-create-field"><span>Display name</span><Input maxLength={120} value={definition.name} onChange={event => set({ name: event.target.value })} placeholder="Airport" /></label>
              <label className="flow-create-field"><span>Plural display name</span><Input aria-label="Plural display name" maxLength={120} value={definition.pluralName} onChange={event => set({ pluralName: event.target.value })} placeholder="Airports" /><small>Collection title in Published. For Airport, use Airports.</small></label>
            </div>
            <label className="flow-create-field is-wide"><span>Description</span><Input.TextArea rows={4} value={definition.description} onChange={event => set({ description: event.target.value })} /></label>
            <SemanticTypeAppearance definition={definition} drafts={drafts.data || []} onChange={set} />
          </div>
        </section>}
        {stage === 'wizard' && currentStep === 'Properties' && <SemanticPropertiesWorkbench key={sourceKey || 'unbacked'}
          definition={definition} preview={preview || undefined} busy={busy} showIssues={!!error} onAutoMap={mapSource}
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
                  <label className="flow-create-field"><span>Submitters</span><Select {...selectStyle} aria-label={kind + ' action submitters'} mode="multiple" onSearch={setPrincipalQuery} value={action.submitters.map(principalKey)} options={principalOptions} loading={principals.loading} disabled={!definition.projectId || principals.loading} onChange={(keys: string[]) => changeAction(kind, { submitters: principalList(keys) })} placeholder="Select users, groups or organization" /></label>
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
          <div className="flow-security-nav" role="tablist" aria-label="Security settings">{securityTabs.map((view, index) =>
            <button type="button" role="tab" id={'security-tab-' + index} aria-controls="security-panel" key={view}
              aria-selected={securityView === view} tabIndex={securityView === view ? 0 : -1} onClick={() => setSecurityView(view)}
              onKeyDown={e => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
                e.preventDefault();
                const next = e.key === 'Home' ? 0 : e.key === 'End' ? securityTabs.length - 1
                  : (index + (e.key === 'ArrowRight' ? 1 : -1) + securityTabs.length) % securityTabs.length;
                setSecurityView(securityTabs[next]); document.getElementById('security-tab-' + next)?.focus();
              }}>{view}</button>)}</div>
          <div className="flow-security-panel" role="tabpanel" id="security-panel" aria-labelledby={'security-tab-' + securityTabs.findIndex(view => view === securityView)}>
          {securityView === 'Object access' && <>
            <h4>Who can read these objects?</h4>
            <dl className="flow-security-facts">
              <dt>Project</dt><dd>{selectedProject?.name || selectedProject?.teamName}</dd>
              <dt>Policy</dt><dd>{existing ? 'Inherit source access' : 'Selected project readers'}</dd>
              {existing && <><dt>Data source</dt><dd>{sourcePath}</dd><dt>Eligible readers</dt><dd>Project members with platform administrator access</dd></>}
            </dl>
            <p className="flow-create-help">{existing ? 'Record and property rules can further restrict what these readers see.' : 'Select project users, teams or organizations to grant access.'}</p>
            {definition.security.mode === 'explicit' && <label className="flow-create-field"><span>Object readers</span><Select {...selectStyle} aria-label="Object readers" mode="multiple" onSearch={setPrincipalQuery} options={principalOptions} loading={principals.loading} value={definition.security.objectReaders.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, objectReaders: principalList(keys) } })} placeholder="Choose who can read objects" /></label>}
            <OntologyMarkingsEditor key={definition.projectId} project={definition.projectId} value={definition.security.markings ?? []}
              principals={principals.data || []} onChange={markings => set({ security: { ...definition.security, markings } })} />
          </>}
          {principals.error && <p className="flow-create-error" role="alert">{principals.error}<Button size="small" onClick={retries}>Retry</Button></p>}
          {securityView === 'Record access' && <OntologyRecordPolicyEditor definition={definition} principals={principals.data || []} onChange={security => set({security})} />}
          {securityView === 'Property access' && <>
            <h4>Property policies</h4>
            <p className="flow-create-help">{Object.keys(definition.security.fieldReaders).length ? 'Only the selected readers can see each restricted property.' : 'All properties currently inherit object access.'}</p>
            <label className="flow-create-field"><span>Restrict a property</span><Select {...selectStyle} aria-label="Add a property policy" value={undefined} disabled={!unrestrictedProperties.length} placeholder={unrestrictedProperties.length ? 'Choose a property' : 'No eligible properties'} options={unrestrictedProperties} onChange={(id: string) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: [] } } })} /></label>
            {Object.entries(definition.security.fieldReaders).map(([id, readers]) => <div key={id} className="flow-semantic-config">
              <div className="flow-create-property-heading"><strong>{definition.properties.find(property => property.id === id)?.name || id}</strong><Button size="small" onClick={() => set({ security: { ...definition.security, fieldReaders: Object.fromEntries(Object.entries(definition.security.fieldReaders).filter(([field]) => field !== id)) } })}>Remove policy</Button></div>
              <Select {...selectStyle} aria-label={id + ' property readers'} mode="multiple" onSearch={setPrincipalQuery} options={principalOptions} value={readers.map(principalKey)} onChange={(keys: string[]) => set({ security: { ...definition.security, fieldReaders: { ...definition.security.fieldReaders, [id]: principalList(keys) } } })} placeholder="Choose who can read this property" />
            </div>)}
          </>}
          {securityView === 'Test access' && <>
            <h4>Preview a user's access</h4>
            <p className="flow-create-help">Check which properties a project member can read under the current draft policy.</p>
            <label className="flow-create-field"><span>Project member</span><Select {...selectStyle} aria-label="Test policy user" showSearch onSearch={setPrincipalQuery} loading={principals.loading} options={userOptions} value={testPrincipal || undefined} onChange={(id: string) => { setTestPrincipal(id); setPolicyResult(undefined); }} placeholder="Search by name or email" /></label>
            <Button className="flow-security-test-button" loading={busy} disabled={!testPrincipal || !!principals.error} onClick={test}>Test policy</Button>
            {policyResult && <div className="flow-create-notice" role="status">
              <strong>{userOptions.find(user => user.value === testPrincipal)?.label} · {policyResult.visible ? 'Object type visible' : 'Object type hidden'}</strong>
              {policyResult.visible && <><p>{policyResult.visibleFields.length} of {definition.properties.length} properties visible</p>
                <ul className="flow-security-results">{definition.properties.map(property => <li key={property.id}><span>{property.name || property.id}</span><span>{policyResult.visibleFields.includes(property.id) ? 'Visible' : 'Hidden'}</span></li>)}</ul>
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
            <dt>Records after publication</dt><dd>{existing ? `Read-only rows from ${sourcePath}` : 'Records are managed in the ontology. Publishing the definition creates zero records.'}</dd><dt>Primary key</dt><dd>{definition.primaryKey.join(', ')}</dd>
            <dt>Title property</dt><dd>{definition.titleKey}</dd><dt>Actions</dt><dd>{definition.actions.map(action => action.kind).join(', ') || 'None'}</dd>
            <dt>Who can read this type</dt><dd>{existing ? 'Project members with platform administrator access' : definition.security.objectReaders.map(reader => principalOptions.find(option => option.value === principalKey(reader))?.label || 'Name unavailable').join(', ')}</dd>
            <dt>Additional restrictions</dt><dd>{definition.security.markings?.length || 0} security markings · {definition.security.recordRules?.length || 0} record rules · {Object.keys(definition.security.fieldReaders).length} property restrictions</dd>
          </dl>
          <Table<SemanticProperty> className="flow-create-table flow-create-review-table" pagination={false} size="small" rowKey="id" dataSource={definition.properties} columns={[{ title: 'ID', dataIndex: 'id' }, { title: 'Display name', dataIndex: 'name' }, { title: 'Type', dataIndex: 'type' }, ...(existing ? [{ title: 'Source', dataIndex: 'sourceColumn' }] : [])]} />
        </section>}
        </div>
      </div>
    </div>
  </Modal>;
}

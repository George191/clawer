import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, message, type MenuItem } from '@/components/ui';
import { ApiOutlined, BranchesOutlined, CodeOutlined, FilterOutlined, DeleteOutlined, HistoryOutlined, PlusOutlined, SyncOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  ontologyError as describe, submitOntologyAction,
  type OntologyRecord, type OntologyRecordPage, type PublishedOntologyType, type SemanticAction,
} from '../../services/ontology';
import type { ExplorerToolAction } from './ExplorerWorkbenchHeader';
import OntologyGraph, { DynamicObjectSets } from './OntologyGraph';
import OntologyFunctions from './OntologyFunctions';
import OntologyRevisions from './OntologyRevisions';
import OntologyApiPublications from './OntologyApiPublications';
import type { PublishedExplorerQuery } from '../../services/publishedExplorerHandoff';
import OntologyTransactions from './OntologyTransactions';
import { parsePropertyValue } from './ontologyPropertyValue';
import DeletedObjects, { RecordHistory } from './PublishedDeletedObjects';
import ExplorerRecordMutation, { recordMutationActions } from './ExplorerRecordMutation';
import './published-tools.css';
import './explorer-resource-tools.css';
import './published-results.css';
import './explorer-history.css';

type ActionInputs = Record<string, { text: string; isNull: boolean }>;
type Operation = { kind: SemanticAction['kind']; fields: string[]; record?: OntologyRecord; initialInputs: ActionInputs };
type Props = { publicationQuery?: PublishedExplorerQuery; objectType?: PublishedOntologyType; actions: NonNullable<OntologyRecordPage['actions']>; record?: OntologyRecord; returnTo: string; onChanged: (kind?: SemanticAction['kind']) => void; busy?: boolean };
const title = (value: unknown) => value == null ? 'Untitled object' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export function usePublishedObjectTools({ objectType, publicationQuery, actions, record, returnTo, onChanged, busy = false }: Props) {
  const [tool, setTool] = useState('');
  const [operation, setOperation] = useState<Operation>();
  const [inputs, setInputs] = useState<ActionInputs>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [relationsVersion, setRelationsVersion] = useState(0);
  const locked = useRef(false);
  const active = useRef(true);
  const typeId = useRef(objectType?.id);
  typeId.current = objectType?.id;
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && identity === session.identity;
  };
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
    if (!objectType || !current() || !operation || busy || locked.current) return;
    const definitionId = objectType.id;
    locked.current = true; setPending(true); setError('');
    let submitted = false;
    try {
      const values: Record<string, unknown> = {};
      for (const id of operation.fields) {
        const property = objectType.definition.properties.find(item => item.id === id);
        if (!property) throw new Error('The selected property is unavailable.');
        const input = inputs[id];
        if (!input) { if (operation.kind === 'create' && property.required) throw new Error(property.name + ' is required.'); continue; }
        const initial = operation.initialInputs[id];
        if (initial && input.text === initial.text && input.isNull === initial.isNull) continue;
        if (input.isNull && property.required) throw new Error(property.name + ' is required.');
        try { values[id] = input.isNull ? null : parsePropertyValue(input.text, property.type); }
        catch (failure) { throw new Error(property.name + ': ' + (failure instanceof Error ? failure.message : 'invalid value')); }
      }
      const selected = operation.record;
      if (operation.kind !== 'create' && (!selected?.id || selected.revision == null)) throw new Error('This record cannot be changed.');
      if (operation.kind === 'modify' && !Object.keys(values).length) { setOperation(undefined); return; }
      submitted = true;
      await submitOntologyAction(objectType.id, operation.kind,
        operation.kind === 'create' ? { values } : operation.kind === 'modify'
          ? { recordId: selected!.id!, expectedRevision: selected!.revision!, values }
          : { recordId: selected!.id!, expectedRevision: selected!.revision! });
      if (current() && typeId.current === definitionId) {
        message.success({ create: 'Object created.', modify: 'Object updated.', delete: 'Object deleted.' }[operation.kind]);
        setOperation(undefined); onChanged(operation.kind);
      }
    } catch (failure) { if (current()) {
      const detail = describe(failure, failure instanceof Error ? failure.message : 'The action failed.');
      if (!submitted) setError(detail);
      else if ((failure as { code?: string })?.code !== 'ERR_CANCELED') message.error({ content: 'Object action failed', description: detail, className: 'explorer-toast' });
    } }
    finally { locked.current = false; if (current()) setPending(false); }
  };
  const tools: ExplorerToolAction[] = objectType ? [
    ...(native ? [
      { key: 'relationships', label: 'Relationships', icon: <BranchesOutlined />, group: 'analyze' as const, run: () => setTool('Relationships') },
      { key: 'dynamic-object-sets', label: 'Dynamic object sets', icon: <FilterOutlined />, group: 'analyze' as const, run: () => setTool('Dynamic object sets') },
      { key: 'transactions', label: 'Transactions', icon: <SyncOutlined />, group: 'analyze' as const, run: () => setTool('Transactions') },
      { key: 'functions', label: 'Functions', icon: <CodeOutlined />, group: 'analyze' as const, run: () => setTool('Functions') },
      { key: 'deleted', label: 'Deleted objects', icon: <DeleteOutlined />, group: 'inspect' as const, run: () => setTool('Deleted objects') },
    ] : []),
    { key: 'revisions', label: 'Model revisions', icon: <HistoryOutlined />, group: 'inspect', run: () => setTool('Model revisions') },
    { key: 'published-api', label: 'Published APIs', icon: <ApiOutlined />, group: 'inspect', run: () => setTool('Published APIs') },
  ] : [];
  const create = native ? actions.find(action => action.kind === 'create') : undefined;
  const creating = operation?.kind === 'create';
  const actionsForRecord = (selected?: OntologyRecord): MenuItem[] => {
    if (!native || !selected?.id || selected.revision == null) return [];
    const edit = actions.find(action => action.kind === 'modify');
    const remove = actions.find(action => action.kind === 'delete');
    return recordMutationActions({
      edit: edit ? () => startAction('modify', edit.fields, selected) : undefined,
      remove: remove ? () => startAction('delete', remove.fields, selected) : undefined,
      disabled: busy || pending,
    });
  };
  const renderMutation = (selected?: OntologyRecord) => {
    if (!objectType || !selected?.id || operation?.kind !== 'modify' || operation.record?.id !== selected.id) return null;
    return <ExplorerRecordMutation mode="edit" title={title(selected.title)}
      error={error} busy={pending} disabled={busy} fields={operation.fields.map(id => {
        const property = objectType.definition.properties.find(item => item.id === id);
        const input = inputs[id];
        return { key: id, label: property?.name || id, type: property?.type || 'string', value: input?.text || '',
          onChange: (value: string | null) => setInputs(previous => ({ ...previous, [id]: { text: value ?? '', isNull: value === null } })),
          nullControl: !property?.required ? { checked: !!input?.isNull, onChange: (isNull: boolean) => setInputs(previous => ({ ...previous, [id]: { text: previous[id]?.text || '', isNull } })) } : undefined };
      })} onSubmit={applyAction} onCancel={() => { if (!locked.current) { setOperation(undefined); setError(''); } }} />;
  };
  const renderHistory = (selected?: OntologyRecord) => objectType && native && selected?.id
    ? <RecordHistory key={selected.id} objectType={objectType.id} recordId={selected.id} onChanged={onChanged} /> : null;
  return {
    tools,
    relationsVersion,
    createButton: create ? <Button size="small" className="wb-create-object" icon={<PlusOutlined />} disabled={busy || pending} onClick={() => startAction('create', create.fields)}>Create object</Button> : null,
    actionsForRecord, renderHistory, renderMutation, mutationOpen: !!operation && !creating,
    dialogs: objectType ? <>
      {operation?.kind === 'delete' && operation.record && <ExplorerRecordMutation mode="delete" title={title(operation.record.title)}
        busy={pending} disabled={busy} onSubmit={applyAction}
        onCancel={() => { if (!locked.current) { setOperation(undefined); setError(''); } }} />}
      <Modal key={objectType.id} open={!!tool} motion centered width="var(--explorer-tool-width, 1040px)" className={`flow-surface-modal flow-published-dialog explorer-resource-dialog explorer-tool-workbench-dialog${tool === 'Relationships' ? ' ontology-relationships-dialog' : ''}`} title={tool} onCancel={() => setTool('')} footer={<Button onClick={() => setTool('')}>Close</Button>}>
        <div className="explorer-resource-context"><strong>{objectType.definition.name}</strong><code>{objectType.definition.id}</code></div>
        <div className="flow-ontology-tool-content" role="region" aria-label={tool || undefined} tabIndex={0}>
          {tool === 'Published APIs' && <OntologyApiPublications key={objectType.id} objectType={objectType} query={publicationQuery} />}
          {tool === 'Relationships' && <OntologyGraph key={objectType.id} objectType={objectType} onRelationsChanged={() => setRelationsVersion(value => value + 1)} />}
          {tool === 'Dynamic object sets' && <DynamicObjectSets key={objectType.id} object={objectType} returnTo={returnTo} onOpenExplorer={() => setTool('')} />}
          {tool === 'Transactions' && <OntologyTransactions key={objectType.id} objectType={objectType} onCommitted={onChanged} />}
          {tool === 'Functions' && <OntologyFunctions key={objectType.id} id={objectType.id} record={record} onCommitted={name => { message.success('Committed: ' + name); onChanged(); }} />}
          {tool === 'Model revisions' && <OntologyRevisions key={objectType.id} showTitle={false} definitionId={objectType.id} onMerged={onChanged} />}
          {tool === 'Deleted objects' && <DeletedObjects key={objectType.id} objectType={objectType} onChanged={onChanged} />}
        </div>
      </Modal>
      {creating && operation && <Modal open centered width={560} className="flow-surface-modal flow-published-dialog published-create-object-dialog" title="Create object" okText="Create object" onOk={applyAction} onCancel={() => { if (!locked.current) setOperation(undefined); }} confirmLoading={pending} maskClosable={!pending} closable={!pending} keyboard={!pending}>
          <div className="published-create-object-context"><strong>{objectType.definition.name}</strong><p>Enter the property values for the new object.</p></div>
          {error && <p role="alert" className="flow-create-error">{error}</p>}
          <div className="published-create-object-fields" role="group" aria-label="Object properties">
            {operation.fields.map(id => {
              const property = objectType.definition.properties.find(item => item.id === id);
              const input = inputs[id];
              return <div key={id} className="published-create-object-field">
                <label><span className="published-create-object-label"><span>{property?.name || id}</span><span className="published-create-object-type">{property?.type === 'json' ? 'JSON' : property?.type || 'value'}</span>{property?.required && <span className="published-create-object-required">Required</span>}</span>
                  {property?.type === 'json'
                    ? <Input.TextArea aria-label={property.name} aria-required={property.required || undefined} rows={4} spellCheck={false} placeholder={'{"key": "value"}'} value={input?.text || ''} disabled={pending || input?.isNull} onChange={event => { const text = event.target.value; setInputs(previous => ({ ...previous, [id]: { text, isNull: false } })); }} />
                    : <Input aria-label={property?.name || id} aria-required={property?.required || undefined} value={input?.text || ''} disabled={pending || input?.isNull} onChange={event => { const text = event.target.value; setInputs(previous => ({ ...previous, [id]: { text, isNull: false } })); }} />}
                </label>
                <div className="published-create-object-field-help">{property?.type === 'json' && <p>Enter valid JSON, such as an object or array.</p>}
                  {!property?.required && <Checkbox aria-label={'Set ' + (property?.name || id) + ' to null'} checked={!!input?.isNull} disabled={pending} onChange={event => { const isNull = event.target.checked; setInputs(previous => ({ ...previous, [id]: { text: previous[id]?.text || '', isNull } })); }}>Set to null</Checkbox>}
                </div>
              </div>;
            })}
          </div>
      </Modal>}
    </> : null,
  };
}

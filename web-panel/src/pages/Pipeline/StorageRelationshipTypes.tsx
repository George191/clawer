import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, Select, message, NativeSelect } from '@/components/ui';
import { ApartmentOutlined, ArrowRightOutlined, PlusOutlined, SearchOutlined } from '@/components/ui/icons';
import { fetchEtlObjectTypes } from '../../services/api';
import type { EtlObjectCatalog, EtlObjectType } from '../../services/types';
import { getSessionState } from '../../services/session';
import { ontologyError } from '../../services/ontology';
import {
  createStorageRelationshipType, fetchStorageRelationshipTypes, saveStorageRelationshipType,
  type StorageRelationshipType, type StorageRelationshipTypesPage,
} from '../../services/storageRelationshipTypes';
import useExplorerErrorToast from './useExplorerErrorToast';
import './ontology-tool-workbench.css';
import './storage-relationship-types.css';

type Props = { schema: string; table: string; open: boolean; initialRelationId?: string; onClose: () => void; onChanged?: (id: string, direction: StorageRelationshipType['direction']) => void };
type Draft = { name: string; description: string };
const draftOf = (item: StorageRelationshipType): Draft => ({ name: item.name, description: item.description });
const cardinalities = { one_to_one: 'One to one', one_to_many: 'One to many', many_to_one: 'Many to one', many_to_many: 'Many to many' };
const cardinalityHelp = {
  one_to_one: 'Each source can link to one target, and each target to one source.',
  one_to_many: 'One source can link to many targets; each target can link to one source.',
  many_to_one: 'Many sources can link to one target; each source can link to one target.',
  many_to_many: 'Objects on either side can have multiple links.',
};
const address = (item: StorageRelationshipType['source']) => `${item.schema}.${item.table}`;
const targetPageSize = 20;

function RelationshipManager({ schema, table, initialRelationId, onClose, onChanged }: Props) {
  const [page, setPage] = useState<StorageRelationshipTypesPage>();
  const [selectedId, setSelectedId] = useState('');
  const [draft, setDraft] = useState<Draft>({ name: '', description: '' });
  const [search, setSearch] = useState('');
  const [revision, refresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [direction, setDirection] = useState<StorageRelationshipType['direction']>('outgoing');
  const [cardinality, setCardinality] = useState<StorageRelationshipType['cardinality']>('many_to_many');
  const [target, setTarget] = useState<EtlObjectType>();
  const [targetSearch, setTargetSearch] = useState('');
  const [targetOffset, setTargetOffset] = useState(0);
  const [targetPage, setTargetPage] = useState<EtlObjectCatalog>();
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [targetError, setTargetError] = useState('');
  const [targetRevision, retryTargets] = useState(0);
  const [decision, setDecision] = useState<{ nextId?: string; reload?: boolean; create?: boolean; cancelCreate?: boolean }>();
  const alive = useRef(true);
  const writing = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return alive.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const selected = page?.items.find(item => item.id === selectedId);
  const dirty = creating ? !!(draft.name || draft.description || target || direction !== 'outgoing' || cardinality !== 'many_to_many')
    : !!selected && (draft.name !== selected.name || draft.description !== selected.description);
  const canEdit = !!page?.canManage;
  const select = (item: StorageRelationshipType) => { setSelectedId(item.id); setDraft(draftOf(item)); setConflict(false); setCreating(false); };

  useEffect(() => { alive.current = true; return () => { alive.current = false; writing.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    fetchStorageRelationshipTypes(schema, table, controller.signal).then(result => {
      if (controller.signal.aborted || !current()) return;
      setPage(result);
      const item = result.items.find(item => item.id === (selectedId || initialRelationId)) ?? result.items[0];
      if (item) select(item); else { setSelectedId(''); setDraft({ name: '', description: '' }); }
      setLoading(false);
    }).catch(failure => {
      if (!controller.signal.aborted && current() && failure?.code !== 'ERR_CANCELED') {
        setLoading(false); setError(ontologyError(failure, 'Unable to read relationship types.'));
      }
    });
    return () => controller.abort();
  }, [schema, table, revision]);
  useExplorerErrorToast(error, 'Relationship types unavailable', () => refresh(value => value + 1));
  useEffect(() => {
    if (!creating) return;
    const controller = new AbortController();
    setTargetsLoading(true); setTargetError('');
    fetchEtlObjectTypes({ search: targetSearch.trim(), limit: targetPageSize, offset: targetOffset }, controller.signal).then(result => {
      if (controller.signal.aborted || !current()) return;
      setTargetPage(previous => targetOffset === 0 ? result : { ...result,
        items: [...new Map([...previous!.items, ...result.items].map(item => [item.id, item])).values()],
      });
      setTargetsLoading(false);
    }).catch(failure => {
      if (!controller.signal.aborted && current() && failure?.code !== 'ERR_CANCELED') {
        setTargetsLoading(false); setTargetError(ontologyError(failure, 'Unable to read target object types.'));
      }
    });
    return () => controller.abort();
  }, [creating, targetSearch, targetOffset, targetRevision]);
  useExplorerErrorToast(creating ? targetError : '', 'Target object types unavailable', () => retryTargets(value => value + 1));

  const beginCreate = () => {
    setCreating(true); setDraft({ name: '', description: '' }); setConflict(false);
    setDirection('outgoing'); setCardinality('many_to_many'); setTarget(undefined); setTargetSearch(''); setTargetOffset(0); setTargetPage(undefined); setTargetsLoading(true); setTargetError('');
  };
  const newType = () => {
    if (!canEdit || loading || writing.current || !current() || creating) return;
    if (dirty) setDecision({ create: true }); else beginCreate();
  };
  const cancelCreate = () => { if (selected) select(selected); else { setCreating(false); setDraft({ name: '', description: '' }); } };
  const searchTargets = (value: string) => {
    if (writing.current || !current() || value.trim() === targetSearch.trim()) return;
    setTargetSearch(value); setTargetOffset(0); setTargetPage(undefined); setTargetsLoading(true); setTargetError('');
  };

  const close = () => { if (!writing.current && current()) { if (dirty) setDecision({}); else onClose(); } };
  const choose = (item: StorageRelationshipType) => {
    if (writing.current || loading || !current() || !creating && item.id === selectedId) return;
    if (dirty) setDecision({ nextId: item.id }); else select(item);
  };
  const discard = () => {
    if (writing.current || !current()) return;
    if (creating) { if (dirty) setDecision({ cancelCreate: true }); else cancelCreate(); }
    else if (conflict) setDecision({ reload: true });
    else if (selected) setDraft(draftOf(selected));
  };
  const confirmDiscard = () => {
    if (writing.current || !current()) return;
    const nextId = decision?.nextId, reload = decision?.reload, create = decision?.create, cancel = decision?.cancelCreate;
    setDecision(undefined);
    if (reload) { refresh(value => value + 1); return; }
    if (create) { beginCreate(); return; }
    if (cancel) { cancelCreate(); return; }
    const item = page?.items.find(item => item.id === nextId);
    if (item) select(item); else onClose();
  };
  const save = async () => {
    if (!current() || !creating && !selected || !canEdit || !dirty || conflict || loading || writing.current) return;
    const name = draft.name.trim();
    if (!name || name.length > 128 || /[\u0000-\u001f\u007f]/.test(draft.name) || draft.description.length > 4000) {
      message.error({ content: 'Check relationship details', description: 'Use a name of 1–128 visible characters and a description of at most 4,000 characters.', className: 'explorer-toast' });
      return;
    }
    if (creating && (!target || targetsLoading || targetError)) {
      message.error({ content: 'Choose a target object type', description: 'Select the other object type before creating the relationship.', className: 'explorer-toast' });
      return;
    }
    const controller = new AbortController(); writing.current = controller; setBusy(true);
    try {
      const saved = creating ? await createStorageRelationshipType(schema, table,
        { name, description: draft.description, cardinality, direction, target: { schema: target!.schema, table: target!.table } }, controller.signal)
        : await saveStorageRelationshipType(schema, table, selected!.id,
          { name, description: draft.description, expectedRevision: selected!.revision }, controller.signal);
      if (controller.signal.aborted || !current()) return;
      setPage(previous => previous ? { ...previous, items: creating ? [...previous.items, saved] : previous.items.map(item => item.id === saved.id ? saved : item) } : previous);
      select(saved);
      message.success({ content: creating ? 'Relationship type created' : 'Relationship type saved', className: 'explorer-toast' });
      onChanged?.(saved.id, saved.direction);
    } catch (failure) {
      if (controller.signal.aborted || !current() || (failure as { code?: string })?.code === 'ERR_CANCELED') return;
      const response = (failure as { response?: { data?: { errors?: { code?: string }; detail?: { code?: string } } } })?.response;
      const stale = (response?.data?.errors?.code ?? response?.data?.detail?.code) === 'relation_version_conflict';
      setConflict(stale);
      message.error({ content: 'Unable to save relationship type',
        description: stale ? 'This relationship changed. Your edits are kept. Discard and reload to review the latest version.' : ontologyError(failure, 'The relationship type could not be saved.'), className: 'explorer-toast' });
    } finally {
      if (writing.current === controller) writing.current = undefined;
      if (alive.current && getSessionState().identity === identity) setBusy(false);
    }
  };
  const matches = page?.items.filter(item => [item.name, address(item.source), address(item.target)].some(value => value.toLowerCase().includes(search.toLowerCase()))) ?? [];
  const anchor = { schema, table, fields: [] };
  const peer = target && { schema: target.schema, table: target.table, fields: [] };
  const sourceEndpoint = creating ? direction === 'outgoing' ? anchor : peer : selected?.source;
  const targetEndpoint = creating ? direction === 'incoming' ? anchor : peer : selected?.target;
  const targetIds = new Set(targetPage?.items.map(item => item.id));
  const targetOptions = target && !targetIds.has(target.id)
    ? [target, ...(targetPage?.items ?? [])] : targetPage?.items ?? [];

  return <>
    <Modal open centered width={960} title="Relationship types" className="flow-surface-modal storage-relationship-types-dialog"
      onCancel={close} closable={!busy} maskClosable={!busy} keyboard={!busy} footer={<>
        <Button onClick={close} disabled={busy}>Close</Button>
        {canEdit && <Button onClick={discard} disabled={busy || loading || !creating && !dirty && !conflict}>{creating ? 'Cancel creation' : conflict ? 'Discard and reload' : 'Discard changes'}</Button>}
        {canEdit && <Button type="primary" onClick={save} disabled={loading || !creating && !selected || !dirty || conflict || creating && (!target || targetsLoading || !!targetError)} loading={busy}>{creating ? 'Create relationship type' : 'Save changes'}</Button>}
      </>}>
      <section className="ontology-tool-workbench storage-relationship-types" aria-label="Relationship type management" aria-busy={loading || busy}>
        <header className="ontology-tool-header"><div><h3><ApartmentOutlined />{schema}.{table}</h3><p>Define business relationships between object types, then link individual objects from Links.</p></div>
          {canEdit ? <Button size="small" icon={<PlusOutlined />} disabled={busy || loading || creating} onClick={newType}>New relationship type</Button>
            : page && <span className="storage-relationship-readonly">Read only</span>}</header>
        <div className="ontology-tool-layout">
          <aside className="ontology-tool-catalog" aria-label="Relationship types">
            <div className="ontology-tool-search"><Input aria-label="Find relationship types" placeholder="Find relationship types" prefix={<SearchOutlined />} value={search} disabled={busy} onChange={event => setSearch(event.target.value)} /></div>
            <div className="ontology-tool-catalog-list">
              {loading && !page ? <p role="status"><LoadingIndicator size={20}>Loading relationship types…</LoadingIndicator></p> : matches.map(item => <button type="button" key={item.id} aria-pressed={!creating && item.id === selectedId} disabled={busy || loading} onClick={() => choose(item)}>
                <ApartmentOutlined /><span><strong>{item.name}</strong><small>{item.source.table} → {item.target.table}</small></span>
              </button>)}
              {page && !matches.length && <p>{search ? 'No matching relationship types.' : 'No relationship types are defined for this object type.'}</p>}
            </div>
          </aside>
          <div className="ontology-tool-detail">
            {selected || creating ? <>
              <header className="ontology-tool-detail-header"><div><h4>{creating ? 'New relationship type' : selected!.name}</h4><span>{creating ? 'Choose the two object types and the allowed number of links.' : `${selected!.direction === 'incoming' ? 'Incoming' : 'Outgoing'} relationship`}</span></div>{!creating && <span>{selected!.enabled ? 'Enabled' : 'Disabled'}</span>}</header>
              <form className="ontology-tool-form" onSubmit={event => { event.preventDefault(); void save(); }}>
                <div className="ontology-tool-fields">
                  <label><span>Display name</span><Input aria-label="Relationship display name" maxLength={128} value={draft.name} disabled={!canEdit || busy || loading || conflict} onChange={event => setDraft(previous => ({ ...previous, name: event.target.value }))} /></label>
                  {creating && <p className="storage-relationship-help">Describe the business meaning, for example 任职于、参与、负责 or 所属. The same object types can have several relationship types.</p>}
                  <label><span>Description <small>Optional</small></span><Input.TextArea aria-label="Relationship description" maxLength={4000} rows={3} value={draft.description} disabled={!canEdit || busy || loading || conflict} onChange={event => setDraft(previous => ({ ...previous, description: event.target.value }))} /></label>
                  {creating && <>
                    <label><span>Direction</span><NativeSelect aria-label="New relationship direction" value={direction} disabled={busy} onChange={event => setDirection(event.target.value as StorageRelationshipType['direction'])}>
                      <option value="outgoing">Outgoing · current type → selected type</option><option value="incoming">Incoming · selected type → current type</option>
                    </NativeSelect></label>
                    <label><span>Target object type</span><Select className="storage-relationship-target-select" popupClassName="flow-select-popup storage-relationship-target-popup"
                      aria-label="New relationship target type" showSearch filterOption={(_query, option) => option?.catalogResult === true} allowClear placeholder="Search object types"
                      value={target?.id} disabled={busy} loading={targetsLoading} onSearch={searchTargets}
                      notFoundContent={targetsLoading ? 'Loading object types…' : 'No matching object types'}
                      options={targetOptions.map(item => ({ value: item.id, catalogResult: targetIds.has(item.id), title: `${item.displayName} · ${item.schema}.${item.table}`,
                        label: <span className="storage-relationship-target-option"><span>{item.displayName}</span><small>{item.schema}.{item.table}</small></span>,
                        disabled: targetsLoading || !!targetError,
                      }))}
                      onChange={(id: string | undefined) => {
                        if (writing.current || !current()) return;
                        if (id === undefined) setTarget(undefined);
                        else if (!targetsLoading && !targetError) setTarget(targetOptions.find(item => item.id === id));
                      }}
                      onPopupScroll={event => {
                        const menu = event.currentTarget;
                        if (writing.current || !current() || targetsLoading || targetError || !targetPage || targetPage.offset + targetPage.limit >= targetPage.total) return;
                        if (menu.scrollTop + menu.clientHeight >= menu.scrollHeight - 24) setTargetOffset(targetPage.offset + targetPage.limit);
                      }} /></label>
                    <label><span>Cardinality</span><NativeSelect aria-label="New relationship cardinality" value={cardinality} disabled={busy} onChange={event => setCardinality(event.target.value as StorageRelationshipType['cardinality'])}>
                      {Object.entries(cardinalities).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </NativeSelect></label><p className="storage-relationship-help">{cardinalityHelp[cardinality]}</p>
                  </>}
                </div>
                <section className="storage-relationship-definition" aria-label="Relationship definition">
                  <h4>Connected object types</h4>{sourceEndpoint && targetEndpoint ? <div className="storage-relationship-endpoints"><div><span>Source</span><strong>{sourceEndpoint.table}</strong><code>{address(sourceEndpoint)}</code></div><ArrowRightOutlined /><div><span>Target</span><strong>{targetEndpoint.table}</strong><code>{address(targetEndpoint)}</code></div></div>
                    : <p className="storage-relationship-help">Current object type: <code>{schema}.{table}</code>. Select the other type to preview the direction.</p>}
                  {!creating && <dl><div><dt>Cardinality</dt><dd>{cardinalities[selected!.cardinality]}</dd></div></dl>}
                </section>
                {!creating && !!selected!.source.fields.length && !!selected!.target.fields.length && <details className="storage-relationship-import"><summary>Import rule</summary><p>Field matching used for explicit imports. Saved links are stored independently.</p>
                  <div>{selected!.source.fields.map((field, index) => <span key={field}><code>{field}</code><ArrowRightOutlined /><code>{selected!.target.fields[index]}</code></span>)}</div>
                </details>}
              </form>
            </> : <div className="ontology-tool-empty"><ApartmentOutlined /><p>{loading ? <LoadingIndicator size={20}>Loading relationship types…</LoadingIndicator> : 'Select a relationship type to view its definition.'}</p></div>}
          </div>
        </div>
      </section>
    </Modal>
    <Modal open={!!decision} centered width={420} title="Discard changes?" className="flow-surface-modal storage-relationship-discard-dialog"
      okText="Discard changes" okButtonProps={{ danger: true }} onOk={confirmDiscard} onCancel={() => setDecision(undefined)}>
      <p>Your changes to this relationship type have not been saved.</p>
    </Modal>
  </>;
}

export default function StorageRelationshipTypes(props: Props) {
  return props.open ? <RelationshipManager key={JSON.stringify([props.schema, props.table, getSessionState().identity])} {...props} /> : null;
}

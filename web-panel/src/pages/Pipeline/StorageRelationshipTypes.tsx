import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, message } from '@/components/ui';
import { ApartmentOutlined, ArrowRightOutlined, SearchOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import { ontologyError } from '../../services/ontology';
import {
  fetchStorageRelationshipTypes, saveStorageRelationshipType,
  type StorageRelationshipType, type StorageRelationshipTypesPage,
} from '../../services/storageRelationshipTypes';
import useExplorerErrorToast from './useExplorerErrorToast';
import './ontology-tool-workbench.css';
import './storage-relationship-types.css';

type Props = { schema: string; table: string; open: boolean; initialRelationId?: string; onClose: () => void; onChanged?: (id: string) => void };
type Draft = { name: string; description: string };
const draftOf = (item: StorageRelationshipType): Draft => ({ name: item.name, description: item.description });
const cardinalities = { one_to_one: 'One to one', one_to_many: 'One to many', many_to_one: 'Many to one', many_to_many: 'Many to many' };
const address = (item: StorageRelationshipType['source']) => `${item.schema}.${item.table}`;

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
  const [decision, setDecision] = useState<{ nextId?: string; reload?: boolean }>();
  const alive = useRef(true);
  const writing = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return alive.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const selected = page?.items.find(item => item.id === selectedId);
  const dirty = !!selected && (draft.name !== selected.name || draft.description !== selected.description);
  const canEdit = !!page?.canManage;
  const select = (item: StorageRelationshipType) => { setSelectedId(item.id); setDraft(draftOf(item)); setConflict(false); };

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

  const close = () => { if (!writing.current && current()) { if (dirty) setDecision({}); else onClose(); } };
  const choose = (item: StorageRelationshipType) => {
    if (writing.current || loading || !current() || item.id === selectedId) return;
    if (dirty) setDecision({ nextId: item.id }); else select(item);
  };
  const discard = () => {
    if (writing.current || !current()) return;
    if (conflict) setDecision({ reload: true });
    else if (selected) setDraft(draftOf(selected));
  };
  const confirmDiscard = () => {
    if (writing.current || !current()) return;
    const nextId = decision?.nextId, reload = decision?.reload;
    setDecision(undefined);
    if (reload) { refresh(value => value + 1); return; }
    const item = page?.items.find(item => item.id === nextId);
    if (item) select(item); else onClose();
  };
  const save = async () => {
    if (!current() || !selected || !canEdit || !dirty || conflict || loading || writing.current) return;
    const name = draft.name.trim();
    if (!name || name.length > 128 || /[\u0000-\u001f\u007f]/.test(name) || draft.description.length > 4000) {
      message.error({ content: 'Check relationship details', description: 'Use a name of 1–128 visible characters and a description of at most 4,000 characters.', className: 'explorer-toast' });
      return;
    }
    const controller = new AbortController(); writing.current = controller; setBusy(true);
    try {
      const saved = await saveStorageRelationshipType(schema, table, selected.id,
        { name, description: draft.description, expectedRevision: selected.revision }, controller.signal);
      if (controller.signal.aborted || !current()) return;
      setPage(previous => previous ? { ...previous, items: previous.items.map(item => item.id === saved.id ? saved : item) } : previous);
      setDraft(draftOf(saved)); setConflict(false);
      message.success({ content: 'Relationship type saved', className: 'explorer-toast' });
      onChanged?.(saved.id);
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

  return <>
    <Modal open centered width={960} title="Relationship types" className="flow-surface-modal storage-relationship-types-dialog"
      onCancel={close} closable={!busy} maskClosable={!busy} keyboard={!busy} footer={<>
        <Button onClick={close} disabled={busy}>Close</Button>
        {canEdit && <Button onClick={discard} disabled={busy || loading || !dirty && !conflict}>{conflict ? 'Discard and reload' : 'Discard changes'}</Button>}
        {canEdit && <Button type="primary" onClick={save} disabled={loading || !selected || !dirty || conflict} loading={busy}>Save changes</Button>}
      </>}>
      <section className="ontology-tool-workbench storage-relationship-types" aria-label="Relationship type management" aria-busy={loading || busy}>
        <header className="ontology-tool-header"><div><h3><ApartmentOutlined />{schema}.{table}</h3><p>Manage the names and descriptions of relationships connected to this object type.</p></div>
          {page && !canEdit && <span className="storage-relationship-readonly">Read only</span>}</header>
        <div className="ontology-tool-layout">
          <aside className="ontology-tool-catalog" aria-label="Relationship types">
            <div className="ontology-tool-search"><Input aria-label="Find relationship types" placeholder="Find relationship types" prefix={<SearchOutlined />} value={search} disabled={busy} onChange={event => setSearch(event.target.value)} /></div>
            <div className="ontology-tool-catalog-list">
              {loading && !page ? <p role="status">Loading relationship types…</p> : matches.map(item => <button type="button" key={item.id} aria-pressed={item.id === selectedId} disabled={busy || loading} onClick={() => choose(item)}>
                <ApartmentOutlined /><span><strong>{item.name}</strong><small>{item.source.table} → {item.target.table}</small></span>
              </button>)}
              {page && !matches.length && <p>{search ? 'No matching relationship types.' : 'No relationship types are defined for this object type.'}</p>}
            </div>
          </aside>
          <div className="ontology-tool-detail">
            {selected ? <>
              <header className="ontology-tool-detail-header"><div><h4>{selected.name}</h4><span>{selected.direction === 'incoming' ? 'Incoming' : 'Outgoing'} relationship</span></div><span>{selected.enabled ? 'Enabled' : 'Disabled'}</span></header>
              <form className="ontology-tool-form" onSubmit={event => { event.preventDefault(); void save(); }}>
                <div className="ontology-tool-fields">
                  <label><span>Display name</span><Input aria-label="Relationship display name" maxLength={128} value={draft.name} disabled={!canEdit || busy || loading || conflict} onChange={event => setDraft(previous => ({ ...previous, name: event.target.value }))} /></label>
                  <label><span>Description <small>Optional</small></span><Input.TextArea aria-label="Relationship description" maxLength={4000} rows={3} value={draft.description} disabled={!canEdit || busy || loading || conflict} onChange={event => setDraft(previous => ({ ...previous, description: event.target.value }))} /></label>
                </div>
                <section className="storage-relationship-definition" aria-label="Relationship definition">
                  <h4>Connected object types</h4><div className="storage-relationship-endpoints"><div><span>Source</span><strong>{selected.source.table}</strong><code>{address(selected.source)}</code></div><ArrowRightOutlined /><div><span>Target</span><strong>{selected.target.table}</strong><code>{address(selected.target)}</code></div></div>
                  <dl><div><dt>Cardinality</dt><dd>{cardinalities[selected.cardinality]}</dd></div></dl>
                </section>
                <details className="storage-relationship-import"><summary>Import rule</summary><p>Field matching used for explicit imports. Saved links are stored independently.</p>
                  <div>{selected.source.fields.map((field, index) => <span key={field}><code>{field}</code><ArrowRightOutlined /><code>{selected.target.fields[index]}</code></span>)}</div>
                </details>
              </form>
            </> : <div className="ontology-tool-empty"><ApartmentOutlined /><p>{loading ? 'Loading relationship types…' : 'Select a relationship type to view its definition.'}</p></div>}
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

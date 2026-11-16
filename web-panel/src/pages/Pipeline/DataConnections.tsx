import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { ApartmentOutlined, ArrowLeftOutlined, DatabaseOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { createEtlConnection, deleteEtlConnection, deleteEtlSchema, fetchCurrentUserContext, fetchEtlConnections, fetchLayers, updateEtlConnection, updateEtlSchema } from '../../services/api';
import type { EtlConnection } from '../../services/api';
import type { LayerNode } from '../../services/types';
import { getSessionState } from '../../services/session';

type Props = { onCancel: () => void; onChanged: () => void };
type ConnectionDraft = { name: string; database: string; schemas: string[] };
type Confirmation = { title: string; detail: string; label: string; action: () => Promise<boolean> };
const metadataSchemas = new Set(['ts_meta', 'ts_meta_hist']);
const validSchema = (name: string) => /^ts_[a-z_][a-z0-9_]{0,59}$/.test(name) && !metadataSchemas.has(name);
const emptyDraft: ConnectionDraft = { name: '', database: '', schemas: [] };
const errorText = (error: unknown) => {
  const response = (error as { response?: { data?: { errors?: unknown; detail?: unknown; message?: unknown } }; message?: string })?.response?.data;
  const detail = response?.errors ?? response?.detail ?? response?.message;
  return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (error as Error)?.message || 'Request failed';
};

export default function DataConnections({ onCancel, onChanged }: Props) {
  const [connections, setConnections] = useState<EtlConnection[]>([]);
  const [layers, setLayers] = useState<LayerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [canManage, setCanManage] = useState(false);
  const [reload, setReload] = useState(0);
  const [search, setSearch] = useState('');
  const [view, setView] = useState<'empty' | 'type' | 'connection' | 'schema'>('type');
  const [selectedId, setSelectedId] = useState('');
  const [editingId, setEditingId] = useState('');
  const [draft, setDraft] = useState<ConnectionDraft>(emptyDraft);
  const [baseline, setBaseline] = useState('');
  const [schemaInput, setSchemaInput] = useState('');
  const [selectedSchema, setSelectedSchema] = useState('');
  const [schemaName, setSchemaName] = useState('');
  const [schemaAlias, setSchemaAlias] = useState('');
  const [schemaBaseline, setSchemaBaseline] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirmationError, setConfirmationError] = useState('');
  const active = useRef(true);
  const pendingRef = useRef(false);
  const sessionIdentity = useRef(getSessionState().identity).current;
  const current = () => active.current && getSessionState().identity === sessionIdentity;
  const blocked = pending || loading || !!loadError || !canManage;
  const selected = connections.find((connection) => connection.id === selectedId);
  const dirty = view === 'connection' ? JSON.stringify(draft) !== baseline || !!schemaInput.trim()
    : view === 'schema' && JSON.stringify([schemaName, schemaAlias]) !== schemaBaseline;

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let valid = true;
    setLoading(true); setLoadError('');
    Promise.all([fetchEtlConnections(), fetchLayers(), fetchCurrentUserContext()]).then(([resources, schemas, context]) => {
      if (!valid || !current()) return;
      setConnections(resources); setLayers(schemas); setCanManage(context.user.is_superuser === true);
    }).catch((failure) => { if (valid && current()) setLoadError(`Unable to load data connections. ${errorText(failure)}`); })
      .finally(() => { if (valid && current()) setLoading(false); });
    return () => { valid = false; };
  }, [reload, sessionIdentity]);

  const openConnection = (connection: EtlConnection) => {
    const next = { name: connection.name, database: connection.database, schemas: [...connection.schemas] };
    setSelectedId(connection.id); setEditingId(connection.id); setDraft(next); setBaseline(JSON.stringify(next));
    setSchemaInput(''); setSelectedSchema(''); setView('connection'); setError('');
  };
  useEffect(() => { if (!loading && !loadError && view === 'empty' && connections.length) openConnection(connections[0]); }, [loading, loadError, view, connections]);

  const navigate = (action: () => void) => {
    if (pendingRef.current) return;
    if (dirty) {
      setConfirmationError('');
      setConfirmation({ title: 'Discard unsaved changes?', detail: 'Your unsaved changes will be discarded.', label: 'Discard changes', action: async () => { if (!current() || pendingRef.current) return false; action(); return true; } });
    } else action();
  };
  const close = () => navigate(onCancel);
  const newConnection = () => navigate(() => { setView('type'); setSelectedId(''); setError(''); setNotice(''); });
  const configurePostgres = () => {
    const databases = [...new Set([...connections.map((connection) => connection.database), ...layers.map((layer) => layer.database).filter((database): database is string => !!database)])];
    const next = { name: '', database: databases.length === 1 ? databases[0] : '', schemas: [] };
    setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(''); setSchemaInput(''); setView('connection'); setError('');
  };
  const openSchema = (schema: string) => navigate(() => {
    const layer = layers.find((entry) => entry.schemaName === schema);
    const alias = layer?.alias || '';
    setSelectedSchema(schema); setSchemaName(schema); setSchemaAlias(alias);
    setSchemaBaseline(JSON.stringify([schema, alias])); setView('schema'); setError(''); setNotice('');
  });
  const mutate = async (action: () => Promise<void>) => {
    if (!current() || pendingRef.current || blocked) return false;
    pendingRef.current = true; setPending(true); setError(''); setNotice(''); setConfirmationError('');
    try { await action(); return current(); }
    catch (failure) { if (current()) { const detail = errorText(failure); setError(detail); setConfirmationError(detail); } return false; }
    finally { pendingRef.current = false; if (current()) setPending(false); }
  };
  const changed = (text: string) => { setNotice(text); setReload((value) => value + 1); onChanged(); };
  const addSchema = () => {
    const name = schemaInput.trim();
    if (!validSchema(name)) { setError('Use a schema name beginning with ts_, followed by lowercase letters, numbers or underscores (maximum 63 characters). Metadata schemas are managed by the system.'); return; }
    if (draft.schemas.includes(name)) { setError('This schema is already included in the connection.'); return; }
    setDraft((value) => ({ ...value, schemas: [...value.schemas, name] })); setSchemaInput(''); setError('');
  };
  const saveConnection = async () => {
    const name = draft.name.trim(), database = draft.database.trim(), extra = schemaInput.trim();
    if (!name || name.length > 200 || !database) { setError('Enter a connection name (maximum 200 characters) and the configured PostgreSQL database name.'); return; }
    if (extra && !validSchema(extra)) { setError('Enter a valid schema name beginning with ts_ (maximum 63 characters).'); return; }
    const payload = { name, database, schemas: [...new Set([...draft.schemas, ...(extra ? [extra] : [])])] };
    const completed = await mutate(async () => {
      const saved = editingId ? await updateEtlConnection(editingId, payload) : await createEtlConnection(payload);
      if (!current()) return;
      setConnections((items) => [...items.filter((item) => item.id !== saved.id), saved]);
      openConnection(saved);
    });
    if (completed) changed(editingId ? 'Connection saved.' : 'Connection created.');
  };
  const removeConnection = () => {
    if (!selected || blocked) return;
    const connection = selected;
    setConfirmationError('');
    setConfirmation({ title: 'Delete connection?', detail: `Delete “${connection.name}” from the connection catalog? Its database, schemas and data will be retained.`, label: 'Delete connection', action: async () => {
      const completed = await mutate(async () => {
        await deleteEtlConnection(connection.id);
        if (!current()) return;
        setConnections((items) => items.filter((item) => item.id !== connection.id)); setSelectedId(''); setView('empty');
      });
      if (completed) changed('Connection deleted.');
      return completed;
    } });
  };
  const saveSchema = async () => {
    const name = schemaName.trim();
    if (!validSchema(name) || schemaAlias.length > 200) { setError('Enter a valid schema name beginning with ts_ (maximum 63 characters) and an alias of at most 200 characters.'); return; }
    const previous = selectedSchema;
    const completed = await mutate(async () => {
      await updateEtlSchema(previous, { name, alias: schemaAlias });
      if (!current()) return;
      setConnections((items) => items.map((item) => ({ ...item, schemas: item.schemas.map((schema) => schema === previous ? name : schema) })));
      setLayers((items) => items.map((item) => item.schemaName === previous ? { ...item, schemaName: name, key: name, alias: schemaAlias } : item));
      setSelectedSchema(name); setSchemaName(name); setSchemaBaseline(JSON.stringify([name, schemaAlias]));
    });
    if (completed) changed('Schema saved.');
  };
  const removeSchema = () => {
    if (blocked) return;
    const schema = selectedSchema;
    setConfirmationError('');
    setConfirmation({ title: 'Delete empty schema?', detail: `Delete “${schema}”? Only empty, unprotected schemas can be deleted. This operation cannot be undone.`, label: 'Delete schema', action: async () => {
      const completed = await mutate(async () => {
        await deleteEtlSchema(schema);
        if (!current()) return;
        setConnections((items) => items.map((item) => ({ ...item, schemas: item.schemas.filter((name) => name !== schema) })));
        setLayers((items) => items.filter((item) => item.schemaName !== schema));
        setSelectedSchema(''); setView('empty');
      });
      if (completed) changed('Schema deleted.');
      return completed;
    } });
  };
  const filtered = connections.filter((connection) => `${connection.name} ${connection.database}`.toLowerCase().includes(search.trim().toLowerCase()));

  return <>
    <Modal className="flow-surface-modal flow-connections" title="Data connections" open onCancel={close} closable={!pending} maskClosable={false} footer={null} width={1000}>
      <div className="flow-connections__layout" aria-busy={loading || pending}>
        <aside className="flow-connections__sidebar">
          <div className="flow-connections__toolbar"><h3>Connections <small>{loading ? '…' : connections.length}</small></h3><Button size="small" icon={<PlusOutlined />} disabled={blocked} onClick={newConnection}>New connection</Button></div>
          <Input aria-label="Search connections" placeholder="Search connections" prefix={<SearchOutlined />} value={search} onChange={(event) => setSearch(event.target.value)} disabled={pending} allowClear />
          <nav className="flow-connections__list" aria-label="Connections">{filtered.map((connection) => <button type="button" key={connection.id} className={`flow-connections__item${selectedId === connection.id ? ' is-active' : ''}`} disabled={pending || loading} aria-pressed={selectedId === connection.id} onClick={() => navigate(() => { openConnection(connection); setNotice(''); })}><DatabaseOutlined /><span><strong>{connection.name}</strong><small>{connection.database}</small></span><em>{connection.schemas.filter((schema) => !metadataSchemas.has(schema)).length}</em></button>)}</nav>
          {!loading && !loadError && !filtered.length && <p className="flow-connections__empty">{connections.length ? 'No matching connections.' : 'No connections yet.'}</p>}
        </aside>
        <main className="flow-connections__content">
          {loading && <p className="flow-connections__feedback" role="status"><ReloadOutlined spin /> Loading connections…</p>}
          {loadError && <div className="flow-connections__feedback is-error" role="alert"><span>{loadError}</span><Button size="small" onClick={() => setReload((value) => value + 1)} disabled={pending}>Retry</Button></div>}
          {!loading && !loadError && !canManage && <p className="flow-connections__feedback is-error" role="alert">Only administrators can manage data connections.</p>}
          {notice && <p className="flow-connections__feedback is-success" role="status">{notice}</p>}
          {error && !confirmation && <p className="flow-connections__feedback is-error" role="alert">{error}</p>}
          {view === 'empty' && !loading && !loadError && <div className="flow-connections__empty"><DatabaseOutlined /><h3>Connect your data</h3><p>Create a connection to organize the database and schemas available to your ontology.</p><Button type="primary" disabled={blocked} onClick={newConnection}>New connection</Button></div>}
          {view === 'type' && <section className="flow-connections__section"><h3>Select a connector</h3><p>Choose the source type for your connection.</p><button type="button" className="flow-connections__type" aria-label="Configure PostgreSQL" disabled={blocked} onClick={configurePostgres}><DatabaseOutlined /><span><strong>PostgreSQL</strong><small>Use the database configured for this workspace.</small></span></button></section>}
          {view === 'connection' && <form className="flow-connections__form" onSubmit={(event) => { event.preventDefault(); void saveConnection(); }}>
            <header className="flow-connections__toolbar"><div><span className="flow-connections__eyebrow">PostgreSQL</span><h3>{editingId ? 'Connection settings' : 'New PostgreSQL connection'}</h3></div>{!editingId && <Button size="small" disabled={blocked} icon={<ArrowLeftOutlined />} onClick={() => navigate(() => setView('type'))}>Connector types</Button>}</header>
            <fieldset disabled={blocked}><label className="flow-connections__field">Connection name<Input aria-label="Connection name" autoComplete="off" value={draft.name} maxLength={200} required onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} /></label>
              <label className="flow-connections__field">Database<Input aria-label="Database" autoComplete="off" value={draft.database} required placeholder="Configured PostgreSQL database" onChange={(event) => setDraft((value) => ({ ...value, database: event.target.value }))} /></label>
              <p className="flow-connections__help">The database must match the PostgreSQL database configured for this workspace.</p>
              <section className="flow-connections__section"><h4>Schema scope</h4><p className="flow-connections__help">Add existing schemas or create new empty schemas when saving. Removing a schema from this list does not delete its data.</p>
                <label className="flow-connections__field">Schema name<div className="flow-connections__schema-input"><Input aria-label="Schema name" value={schemaInput} placeholder="ts_example" maxLength={63} onChange={(event) => setSchemaInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addSchema(); } }} /><Button size="small" aria-label="Add schema" icon={<PlusOutlined />} onClick={addSchema} disabled={!schemaInput.trim()}>Add</Button></div></label>
                <div className="flow-connections__chips">{draft.schemas.filter((schema) => !metadataSchemas.has(schema)).map((schema) => <span key={schema}>{schema}<button type="button" aria-label={`Remove ${schema} from connection`} onClick={() => setDraft((value) => ({ ...value, schemas: value.schemas.filter((entry) => entry !== schema) }))}>×</button></span>)}</div>
              </section>
            </fieldset>
            <div className="flow-connections__footer">{editingId && <Button danger icon={<DeleteOutlined />} disabled={blocked} onClick={removeConnection}>Delete connection</Button>}<Button type="primary" htmlType="submit" loading={pending} disabled={blocked || !!editingId && !dirty}>{editingId ? 'Save connection' : 'Create connection'}</Button></div>
            {selected && editingId && <section className="flow-connections__section"><h4>Schemas</h4><div className="flow-connections__schema-list">{selected.schemas.filter((schema) => !metadataSchemas.has(schema)).map((schema) => <button type="button" key={schema} aria-label={`Manage schema ${schema}`} disabled={blocked} onClick={() => openSchema(schema)}><ApartmentOutlined /><span><strong>{schema}</strong><small>{layers.find((layer) => layer.schemaName === schema)?.alias}</small></span></button>)}</div>{!selected.schemas.some((schema) => !metadataSchemas.has(schema)) && <p className="flow-connections__help">No schemas in this connection.</p>}</section>}
          </form>}
          {view === 'schema' && <form className="flow-connections__form" onSubmit={(event) => { event.preventDefault(); void saveSchema(); }}><header className="flow-connections__toolbar"><div><span className="flow-connections__eyebrow">{selected?.name}</span><h3>Schema details</h3></div><Button size="small" icon={<ArrowLeftOutlined />} disabled={blocked} onClick={() => navigate(() => { if (selected) openConnection(selected); })}>Connection</Button></header>
            <fieldset disabled={blocked}><label className="flow-connections__field">Schema name<Input aria-label="Schema name" value={schemaName} required maxLength={63} onChange={(event) => setSchemaName(event.target.value)} /></label><label className="flow-connections__field">Schema alias<Input aria-label="Schema alias" value={schemaAlias} maxLength={200} onChange={(event) => setSchemaAlias(event.target.value)} /></label><p className="flow-connections__help">Aliases change the display name. Protected pipeline schemas and schemas with managed object definitions cannot be renamed.</p></fieldset>
            <div className="flow-connections__footer"><Button danger icon={<DeleteOutlined />} disabled={blocked} onClick={removeSchema}>Delete schema</Button><Button htmlType="submit" type="primary" loading={pending} disabled={blocked || !dirty}>Save schema</Button></div>
          </form>}
        </main>
      </div>
      <footer className="flow-connections__dialog-footer"><Button disabled={pending} onClick={close}>Close</Button></footer>
    </Modal>
    {confirmation && <Modal className="flow-surface-modal flow-create-confirmation flow-connections__confirmation" title={confirmation.title} open okText={confirmation.label} confirmLoading={pending} cancelButtonProps={{ disabled: pending }} closable={!pending} maskClosable={false} onCancel={() => { if (!pendingRef.current) setConfirmation(null); }} onOk={async () => { if (await confirmation.action() && current()) setConfirmation(null); }}><p>{confirmation.detail}</p>{confirmationError && <p className="flow-connections__feedback is-error" role="alert">{confirmationError}</p>}</Modal>}
  </>;
}

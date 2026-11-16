import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select } from '@/components/ui';
import { ArrowLeftOutlined, CheckCircleOutlined, DatabaseOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { createDataSource, deleteDataSource, fetchCurrentUserContext, fetchDataConnectorTypes, fetchDataSources, testDataSource, updateDataSource } from '../../services/api';
import type { DataConnectorEngine, DataConnectorType, DataSource, DataSourceConfig, DataSourcePayload, DataSourceTest } from '../../services/api';
import { getSessionState } from '../../services/session';
import PostgresGlyph from './PostgresGlyph';
import './data-connections.css';

type Props = { onCancel: () => void; onChanged: () => void };
type Draft = { name: string; engine: DataConnectorEngine; config: Omit<DataSourceConfig, 'port'> & { port: string }; secret: string };
type Confirmation = { title: string; detail: string; label: string; action: () => Promise<boolean> };
const categories = [{ key: 'all', label: 'All connectors' }, { key: 'relational', label: 'Relational databases' }, { key: 'non_relational', label: 'Non-relational databases' }, { key: 'vector', label: 'Vector databases' }];
const errorText = (error: unknown) => {
  const response = (error as { response?: { data?: { errors?: unknown; detail?: unknown; message?: unknown } }; message?: string })?.response?.data;
  const detail = response?.errors ?? response?.detail ?? response?.message;
  return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (error as Error)?.message || 'Request failed';
};
const initialDraft = (connector: DataConnectorType): Draft => ({ name: '', engine: connector.engine, secret: '', config: { host: '', port: String(connector.port), database: connector.engine === 'milvus' ? 'default' : '', username: '', auth: 'password', tls: connector.engine !== 'dameng' } });

function ConnectorMark({ engine }: { engine: DataConnectorEngine }) {
  return <span className={`ontology-connectors__mark is-${engine}`} aria-hidden="true">{engine === 'postgresql' ? <PostgresGlyph /> : engine === 'oracle' ? <span className="ontology-connectors__oracle" /> : engine === 'elasticsearch' ? <svg viewBox="0 0 24 24"><path fill="#FEC514" d="M3 8a9 9 0 0 1 15-3L11 9Z" /><path fill="#00BFB3" d="m12 10 9-3a9 9 0 0 1-1 11l-8-4Z" /><path fill="#F04E98" d="m3 10 7 1v4l-5 4a9 9 0 0 1-2-9Z" /><path fill="#009EBB" d="m11 16 7 4a9 9 0 0 1-11 1Z" /></svg> : engine === 'milvus' ? <svg viewBox="0 0 24 24" fill="currentColor"><path d="m3 8 5-4v12l-5 4Zm7-3 5 4v11l-5-4Zm7 4 5-4v11l-5 4Z" /></svg> : <span className="ontology-connectors__wordmark">{engine === 'mysql' ? 'MySQL' : 'DM'}</span>}</span>;
}

export default function DataConnections({ onCancel, onChanged }: Props) {
  const [connectors, setConnectors] = useState<DataConnectorType[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [canManage, setCanManage] = useState(false);
  const [reload, setReload] = useState(0);
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editingId, setEditingId] = useState('');
  const [baseline, setBaseline] = useState('');
  const [operation, setOperation] = useState<'' | 'test' | 'save' | 'delete'>('');
  const [testResult, setTestResult] = useState<DataSourceTest | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [confirmationError, setConfirmationError] = useState('');
  const active = useRef(true);
  const pendingRef = useRef(false);
  const sessionIdentity = useRef(getSessionState().identity).current;
  const current = () => active.current && getSessionState().identity === sessionIdentity;
  const pending = !!operation;
  const blocked = pending || loading || !!loadError || !canManage;
  const connector = connectors.find((item) => item.engine === draft?.engine);
  const selected = sources.find((item) => item.id === editingId);
  const dirty = !!draft && JSON.stringify(draft) !== baseline;

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let valid = true;
    setLoading(true); setLoadError('');
    fetchCurrentUserContext().then(async (context) => {
      if (!valid || !current()) return;
      const allowed = context.user.is_superuser === true;
      setCanManage(allowed);
      if (!allowed) return;
      const [types, saved] = await Promise.all([fetchDataConnectorTypes(), fetchDataSources()]);
      if (valid && current()) { setConnectors(types); setSources(saved); }
    }).catch((failure) => { if (valid && current()) setLoadError(`Unable to load data sources. ${errorText(failure)}`); })
      .finally(() => { if (valid && current()) setLoading(false); });
    return () => { valid = false; };
  }, [reload, sessionIdentity]);

  const navigate = (action: () => void) => {
    if (pendingRef.current) return;
    if (dirty) {
      setConfirmationError('');
      setConfirmation({ title: 'Discard unsaved changes?', detail: 'Your unsaved connection settings will be discarded.', label: 'Discard changes', action: async () => { if (!current() || pendingRef.current) return false; action(); return true; } });
    } else action();
  };
  const resetFeedback = () => { setError(''); setNotice(''); setTestResult(null); };
  const newSource = () => navigate(() => { setDraft(null); setEditingId(''); resetFeedback(); });
  const configure = (type: DataConnectorType) => {
    const next = initialDraft(type);
    setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(''); resetFeedback();
  };
  const openSource = (source: DataSource) => navigate(() => {
    const next = { name: source.name, engine: source.engine, config: { ...source.config, port: String(source.config.port) }, secret: '' };
    setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(source.id); resetFeedback();
  });
  const change = (patch: Partial<Draft>) => { setDraft((value) => value ? { ...value, ...patch } : value); resetFeedback(); };
  const changeConfig = (patch: Partial<Draft['config']>) => { if (draft) change({ config: { ...draft.config, ...patch } }); };
  const payload = (): DataSourcePayload | null => {
    if (!draft) return null;
    const name = draft.name.trim(), config = draft.config;
    if (!name || !config.host.trim() || !/^\d+$/.test(config.port) || Number(config.port) < 1 || Number(config.port) > 65535) { setError('Enter a connection name, host and port between 1 and 65535.'); return null; }
    if (config.auth === 'password' && !config.username.trim()) { setError('Enter the database username.'); return null; }
    if (['postgresql', 'mysql', 'oracle', 'milvus'].includes(draft.engine) && !config.database.trim()) { setError('Enter the database or service name.'); return null; }
    if (!draft.secret && (!selected?.credentialsConfigured || selected.config.auth !== config.auth)) { setError('Enter credentials for this data source.'); return null; }
    const key = config.auth === 'api_key' ? 'apiKey' : config.auth === 'token' ? 'token' : 'password';
    return { name, engine: draft.engine, config: { ...config, host: config.host.trim(), port: Number(config.port), username: config.username.trim(), database: config.database.trim() }, credentials: draft.secret ? { [key]: draft.secret } : {} };
  };
  const mutate = async (kind: 'test' | 'save' | 'delete', action: () => Promise<void>) => {
    if (!current() || pendingRef.current || blocked) return false;
    pendingRef.current = true; setOperation(kind); setError(''); setNotice(''); setConfirmationError('');
    try { await action(); return current(); }
    catch (failure) { if (current()) { const detail = errorText(failure); setError(detail); setConfirmationError(detail); } return false; }
    finally { pendingRef.current = false; if (current()) setOperation(''); }
  };
  const test = async () => {
    const body = payload();
    if (!body) return;
    setTestResult(null);
    await mutate('test', async () => {
      const result = await testDataSource(body, editingId || undefined);
      if (current()) setTestResult(result);
    });
  };
  const save = async () => {
    const body = payload();
    if (!body) return;
    await mutate('save', async () => {
      const source = editingId ? await updateDataSource(editingId, body) : await createDataSource(body);
      if (!current()) return;
      setSources((items) => [...items.filter((item) => item.id !== source.id), source].sort((a, b) => a.name.localeCompare(b.name)));
      const next: Draft = { name: source.name, engine: source.engine, config: { ...source.config, port: String(source.config.port) }, secret: '' };
      setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(source.id);
      setNotice('Source saved. Its connection was verified with a read-only request.'); setTestResult(null); onChanged();
    });
  };
  const remove = () => {
    if (!selected || blocked) return;
    const source = selected;
    setConfirmationError('');
    setConfirmation({ title: 'Delete data source?', detail: `Delete “${source.name}” and its stored credentials? This retains the external database and all data.`, label: 'Delete source', action: async () => {
      const done = await mutate('delete', async () => {
        await deleteDataSource(source.id);
        if (!current()) return;
        setSources((items) => items.filter((item) => item.id !== source.id)); setDraft(null); setEditingId(''); setNotice('Data source deleted.'); onChanged();
      });
      return done;
    } });
  };
  const filtered = connectors.filter((item) => (category === 'all' || item.category === category) && `${item.name} ${item.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  const secretLabel = draft?.config.auth === 'api_key' ? 'API key' : draft?.config.auth === 'token' ? 'Token' : 'Password';

  return <>
    <Modal className="flow-surface-modal ontology-connectors" title="Data connections" open onCancel={() => navigate(onCancel)} closable={!pending} maskClosable={false} footer={null} width={1100}>
      <div className="ontology-connectors__layout" aria-busy={loading || pending}>
        <aside className="ontology-connectors__sidebar">
          <Button icon={<PlusOutlined />} disabled={blocked} onClick={newSource}>New source</Button>
          <nav className="ontology-connectors__categories" aria-label="Connector category">{categories.map((item) => <button key={item.key} type="button" disabled={pending} aria-pressed={!draft && category === item.key} onClick={() => navigate(() => { setDraft(null); setEditingId(''); setCategory(item.key); resetFeedback(); })}>{item.label}<span>{connectors.filter((type) => item.key === 'all' || type.category === item.key).length}</span></button>)}</nav>
          <h3 className="ontology-connectors__sidebar-heading">Saved sources <span>{sources.length}</span></h3>
          <nav className="ontology-connectors__saved" aria-label="Saved data sources">{sources.map((source) => <button type="button" key={source.id} disabled={pending} aria-pressed={source.id === editingId} onClick={() => openSource(source)}><ConnectorMark engine={source.engine} /><span><strong>{source.name}</strong><small>{source.config.host}</small></span></button>)}{!loading && !sources.length && <p>No sources configured.</p>}</nav>
        </aside>
        <main className="ontology-connectors__content">
          <div className="ontology-connectors__status" aria-live="polite">
            {loading && <p role="status"><ReloadOutlined spin /> Loading data sources…</p>}
            {loadError && <p className="is-error" role="alert"><span>{loadError}</span><Button size="small" onClick={() => setReload((value) => value + 1)} disabled={pending}>Retry</Button></p>}
            {!loading && !loadError && !canManage && <p className="is-error" role="alert">Only administrators can manage data sources.</p>}
            {notice && <p className="is-success" role="status"><CheckCircleOutlined />{notice}</p>}
            {error && !confirmation && <p className="is-error" role="alert">{error}</p>}
          </div>
          {!draft && <section className="ontology-connectors__catalog">
            <header><span className="ontology-connectors__eyebrow">New source</span><h2>Choose a connector</h2><p>Connect an external database to your workspace.</p></header>
            <Input aria-label="Search connector types" prefix={<SearchOutlined />} value={search} placeholder="Search connectors" onChange={(event) => setSearch(event.target.value)} disabled={pending} allowClear />
            <div className="ontology-connectors__grid">{filtered.map((item) => <button type="button" key={item.engine} className="ontology-connectors__card" aria-label={`Configure ${item.name}`} disabled={blocked} onClick={() => configure(item)}><ConnectorMark engine={item.engine} /><strong>{item.name}</strong><small>{item.description}</small><span className={`ontology-connectors__capability${item.driverAvailable ? '' : ' is-unavailable'}`}>{item.driverAvailable ? 'Read-only connection test' : 'Driver required'}</span></button>)}</div>
            {!loading && canManage && !loadError && !filtered.length && <p className="ontology-connectors__empty">No connectors match your search.</p>}
            <p className="ontology-connectors__scope"><DatabaseOutlined /> Saving a source stores its connection settings. Tables are not imported or synchronized automatically.</p>
          </section>}
          {draft && connector && <form className="ontology-connectors__form" onSubmit={(event) => event.preventDefault()} onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.target instanceof HTMLInputElement && !event.target.closest('[role="combobox"]')) event.preventDefault(); }}>
            <header className="ontology-connectors__form-heading"><div><ConnectorMark engine={draft.engine} /><span><span className="ontology-connectors__eyebrow">{connector.name}</span><h2>{editingId ? 'Source settings' : `New ${connector.name} source`}</h2></span></div><Button size="small" icon={<ArrowLeftOutlined />} disabled={blocked} onClick={newSource}>Connectors</Button></header>
            {!connector.driverAvailable && <p className="ontology-connectors__prerequisite" role="alert">{connector.prerequisite} Connection testing and saving require this driver.</p>}
            <fieldset disabled={blocked}>
              <section className="ontology-connectors__section"><h3>Source details</h3><label>Connection name<Input aria-label="Connection name" value={draft.name} maxLength={200} autoComplete="off" placeholder="Production database" onChange={(event) => change({ name: event.target.value })} /></label></section>
              <section className="ontology-connectors__section"><h3>Connection</h3><div className="ontology-connectors__endpoint"><label>Host<Input aria-label="Host" value={draft.config.host} maxLength={253} autoComplete="off" placeholder="database.example.com" onChange={(event) => changeConfig({ host: event.target.value })} /></label><label>Port<Input aria-label="Port" value={draft.config.port} inputMode="numeric" maxLength={5} onChange={(event) => changeConfig({ port: event.target.value })} /></label></div>
                {!['elasticsearch', 'dameng'].includes(draft.engine) && <label>{draft.engine === 'oracle' ? 'Service name' : 'Database'}<Input aria-label={draft.engine === 'oracle' ? 'Service name' : 'Database'} value={draft.config.database} maxLength={128} autoComplete="off" onChange={(event) => changeConfig({ database: event.target.value })} /></label>}
                {draft.engine !== 'dameng' ? <><Checkbox aria-label="Use TLS" checked={draft.config.tls} onChange={(event) => changeConfig({ tls: event.target.checked })}>Use TLS</Checkbox><p className="ontology-connectors__help">{draft.config.tls ? 'Verify the server certificate and hostname using the API image’s trusted certificate authorities.' : 'TLS is disabled. Connection traffic is not protected by this application.'}</p></> : <p className="ontology-connectors__help">Uses the DPI client’s default transport. Vendor certificate configuration is not managed by this form.</p>}
              </section>
              <section className="ontology-connectors__section"><h3>Authentication</h3>
                {['elasticsearch', 'milvus'].includes(draft.engine) && <label>Authentication method<Select aria-label="Authentication method" value={draft.config.auth} disabled={blocked} onChange={(auth: DataSourceConfig['auth']) => { change({ config: { ...draft.config, auth, username: '' }, secret: '' }); }} options={[{ value: 'password', label: 'Username and password' }, draft.engine === 'elasticsearch' ? { value: 'api_key', label: 'API key' } : { value: 'token', label: 'Token' }]} /></label>}
                {draft.config.auth === 'password' && <label>Username<Input aria-label="Username" value={draft.config.username} maxLength={200} autoComplete="off" onChange={(event) => changeConfig({ username: event.target.value })} /></label>}
                <label>{secretLabel}<Input aria-label={secretLabel} type="password" value={draft.secret} maxLength={8192} autoComplete="new-password" placeholder={editingId ? `Leave blank to keep the stored ${secretLabel.toLowerCase()}` : `Enter ${secretLabel.toLowerCase()}`} onChange={(event) => change({ secret: event.target.value })} /></label>
                <p className="ontology-connectors__help">Credentials are encrypted at rest and are never returned by the API. Use a dedicated account with read access.</p>
              </section>
            </fieldset>
            <div className="ontology-connectors__test-result" aria-live="polite">{testResult && <p className={testResult.ok ? 'is-success' : 'is-error'} role={testResult.ok ? 'status' : 'alert'}>{testResult.ok && <CheckCircleOutlined />}<span>{testResult.message}</span><small>{testResult.latencyMs} ms</small></p>}{selected && !testResult && <small>Last verified: {new Date(selected.testedAt).toLocaleString()}</small>}</div>
            <div className="ontology-connectors__actions">{editingId && <Button danger icon={<DeleteOutlined />} disabled={blocked} onClick={remove}>Delete source</Button>}<span /><Button onClick={() => void test()} loading={operation === 'test'} disabled={blocked || !connector.driverAvailable}>Test connection</Button><Button type="primary" onClick={() => void save()} loading={operation === 'save'} disabled={blocked || !connector.driverAvailable || !!editingId && !dirty}>Save source</Button></div>
            <p className="ontology-connectors__help">Saving verifies the connection again. This does not import tables or change the external database.</p>
          </form>}
        </main>
      </div>
      <footer className="ontology-connectors__dialog-footer"><Button disabled={pending} onClick={() => navigate(onCancel)}>Close</Button></footer>
    </Modal>
    {confirmation && <Modal className="flow-surface-modal ontology-connectors__confirmation" title={confirmation.title} open okText={confirmation.label} confirmLoading={pending} cancelButtonProps={{ disabled: pending }} closable={!pending} maskClosable={false} onCancel={() => { if (!pendingRef.current) setConfirmation(null); }} onOk={async () => { if (await confirmation.action() && current()) setConfirmation(null); }}><p>{confirmation.detail}</p>{confirmationError && <p role="alert">{confirmationError}</p>}</Modal>}
  </>;
}

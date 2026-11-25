import React, { useEffect, useId, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Button, Checkbox, Input, message, Modal, Select, NativeSelect } from '@/components/ui';
import { ArrowLeftOutlined, ArrowRightOutlined, DatabaseOutlined, DeleteOutlined, EyeOutlined, FolderOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TableOutlined } from '@/components/ui/icons';
import apiClient, { createDataSource, deleteDataSource, fetchCurrentUserContext, fetchDataConnectorTypes, fetchDataSourceCatalog, fetchDataSourceColumns, fetchDataSources, selectBusinessDatabase, setDataSourcePaused, testDataSource, updateDataSource } from '../../services/api';
import type { DataConnectorEngine, DataConnectorType, DataSource, DataSourceCatalog, DataSourceColumns, DataSourceConfig, DataSourcePayload, DataSourceProvider, DataSourceRelation, DataSourceTest, BatchSourceIdentity, WarehouseConnection } from '../../services/api';
import { ontologyCatalogHref } from '../../services/explorerNavigation';
import { getSessionState } from '../../services/session';
import ConnectorMark from './ConnectorMark';
import HostingProviderIcon from './HostingProviderIcon';
import SourceBatchSyncs from './SourceBatchSyncs';
import SourceIdentity from './SourceIdentity';
import SourceCdc from './SourceCdc';
import SourceDamengCdc from './SourceDamengCdc';
import SourceObjectEvents from './SourceObjectEvents';
import SourceCatalogReferences from './SourceCatalogReferences';
import { useSandboxIdentity } from './SandboxMarkings';
import SavedConnectionCapabilities from './SavedConnectionCapabilities';
import type { BatchSyncSource } from './SourceBatchSyncs';
import TableExports from './TableExports';
import SandboxCdcReplay from './SandboxCdcReplay';
import SyntheticPushInbox from './SyntheticPushInbox';
import SandboxHandoffRecipientProof from './SandboxHandoffRecipientProof';
import useExplorerErrorToast from './useExplorerErrorToast';
import './data-connections.css';

type Props = {
  warehouse?: WarehouseConnection; warehouseFailed: boolean; onRetryWarehouse: () => void;
  onCancel: () => void; onChanged?: () => void;
};
type Draft = { name: string; engine: DataConnectorEngine; config: Omit<DataSourceConfig, 'port'> & { port: string }; secret: string };
type Confirmation = { title: string; detail: string; label: string; action: () => Promise<boolean> };
const categories = [{ key: 'all', label: 'All connectors' }, { key: 'relational', label: 'Relational databases' }, { key: 'document', label: 'Document databases' }, { key: 'key_value', label: 'Key-value databases' }, { key: 'search', label: 'Search engines' }, { key: 'vector', label: 'Vector databases' }, { key: 'object_storage', label: 'Object storage' }];
const providerNames: Record<DataSourceProvider, string> = {
  self_hosted: 'Self-hosted', aws: 'Amazon Web Services', azure: 'Microsoft Azure', gcp: 'Google Cloud',
  aliyun: 'Alibaba Cloud', tencent: 'Tencent Cloud', huawei: 'Huawei Cloud', oracle_cloud: 'Oracle Cloud', other: 'Other cloud provider',
};
function providerLabel(provider: DataSourceProvider = 'self_hosted') {
  return <span className="ontology-connectors__provider"><HostingProviderIcon provider={provider} /><span>{providerNames[provider]}</span></span>;
}
const providerOptions = (Object.keys(providerNames) as DataSourceProvider[]).map(value => ({ value, label: providerLabel(value) }));
const addressingStyles = { path: 'Path style', virtual: 'Virtual-hosted style' };
const authFields = {
  password: { method: 'Username and password', label: 'Password', key: 'password' },
  api_key: { method: 'API key', label: 'API key', key: 'apiKey' },
  token: { method: 'Token', label: 'Token', key: 'token' },
  access_key: { method: 'Access key', label: 'Secret access key', key: 'secretAccessKey' },
} as const;
const errorText = (error: unknown) => {
  const response = (error as { response?: { data?: { errors?: unknown; detail?: unknown; message?: unknown } }; message?: string })?.response?.data;
  const detail = response?.errors ?? response?.detail ?? response?.message;
  return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (error as Error)?.message || 'Request failed';
};
const mongoAuthDatabase = (value?: string) => value?.trim() || 'admin';
const initialDraft = ({ engine, port }: DataConnectorType): Draft => ({
  name: '', engine, secret: '',
  config: {
    host: '', port: String(port), database: engine === 'redis' ? '0' : ['milvus', 'clickhouse'].includes(engine) ? 'default' : '',
    username: engine === 'redis' ? 'default' : '', auth: engine === 's3' ? 'access_key' : 'password', tls: engine !== 'dameng',
    ...(engine === 's3' ? { bucket: '', region: '' } : {}),
    ...(engine === 'mongodb' ? { authDatabase: 'admin' } : {}),
  },
});


const formatDate = (value?: string | null) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString() : 'Not recorded';
const isConnectionPaused = (source?: DataSource) => source?.engine === 'postgresql' && Boolean(source.pausedAt);
const databaseLabel = (engine: DataConnectorEngine) => engine === 'oracle' ? 'Service name' : engine === 'redis' ? 'Database index' : 'Database';
const endpoint = (config: DataSourceConfig) => `${config.host.includes(':') && !config.host.startsWith('[') ? `[${config.host}]` : config.host}:${config.port}`;

const relationLabel: Record<DataSourceRelation['kind'], string> = {
  table: 'Table', partitioned_table: 'Partitioned table', view: 'View', materialized_view: 'Materialized view', foreign_table: 'Foreign table',
};
const CATALOG_PAGE_SIZE = 50;
const CATALOG_LAST_OFFSET = 5000;

function catalogError(failure: unknown) {
  const status = (failure as { response?: { status?: number }; code?: string })?.response?.status;
  if (status === 403 || status === 404) return 'The saved connection or this resource is no longer accessible.';
  if (status === 504 || (failure as { code?: string })?.code === 'ECONNABORTED') return 'The source catalog request timed out.';
  return 'Could not read metadata from the saved connection.';
}

function SourceCatalog({ sourceId, sourceRevision, onSetUpSync }: { sourceId: string; sourceRevision: string; onSetUpSync: (selection: BatchSyncSource) => void }) {
  const liveIdentity = useSandboxIdentity();
  const [catalog, setCatalog] = useState<DataSourceCatalog>();
  const [relations, setRelations] = useState<DataSourceCatalog>();
  const [columns, setColumns] = useState<DataSourceColumns>();
  const [columnRevision, setColumnRevision] = useState<string>();
  const [schema, setSchema] = useState('');
  const [table, setTable] = useState('');
  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [schemaError, setSchemaError] = useState('');
  const [relationError, setRelationError] = useState('');
  const [columnError, setColumnError] = useState('');
  const identity = useRef(getSessionState().identity).current;
  const current = (signal: AbortSignal) => {
    const session = getSessionState();
    return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => {
    const controller = new AbortController();
    setCatalog(undefined);
    setSchemaError('');
    void fetchDataSourceCatalog(sourceId, {}, controller.signal).then(value => {
      if (!current(controller.signal)) return;
      setCatalog(value);
    }).catch(failure => { if (current(controller.signal)) setSchemaError(catalogError(failure)); });
    return () => controller.abort();
  }, [sourceId, sourceRevision, refresh]);

  useEffect(() => {
    if (!schema) return;
    const controller = new AbortController();
    setRelations(undefined);
    setRelationError('');
    void fetchDataSourceCatalog(sourceId, { schema, search: appliedSearch || undefined, limit: CATALOG_PAGE_SIZE, offset }, controller.signal).then(value => {
      if (current(controller.signal)) setRelations(value);
    }).catch(failure => { if (current(controller.signal)) setRelationError(catalogError(failure)); });
    return () => controller.abort();
  }, [sourceId, sourceRevision, schema, appliedSearch, offset, refresh]);

  useEffect(() => {
    if (!schema || !table) return;
    const controller = new AbortController();
    setColumns(undefined);
    setColumnError('');
    void fetchDataSourceColumns(sourceId, schema, table, controller.signal).then(value => {
      if (current(controller.signal)) { setColumns(value); setColumnRevision(sourceRevision); }
    }).catch(failure => { if (current(controller.signal)) setColumnError(catalogError(failure)); });
    return () => controller.abort();
  }, [sourceId, sourceRevision, schema, table, refresh]);

  const clearTable = () => { setTable(''); setColumns(undefined); setColumnError(''); };
  const changeSchema = (name: string) => {
    if (schema === name) return;
    setSchema(name); setSearch(''); setAppliedSearch(''); setOffset(0); setRelations(undefined); setRelationError(''); clearTable();
  };
  const changePage = (pageOffset: number) => { setOffset(pageOffset); setRelations(undefined); setRelationError(''); clearTable(); };
  const searchRelations = (value: string) => {
    const next = value.trim();
    if (next === appliedSearch && offset === 0) return;
    setAppliedSearch(next); changePage(0);
  };
  const changeTable = (name: string) => { if (table !== name) { clearTable(); setTable(name); } };
  useExplorerErrorToast(schemaError, 'Unable to load source schemas', () => setRefresh(value => value + 1));
  useExplorerErrorToast(relationError, 'Unable to load tables and views', () => setRefresh(value => value + 1));
  useExplorerErrorToast(columnError, 'Unable to load column metadata', () => setRefresh(value => value + 1));

  return <section className="ontology-source-catalog" aria-label="Source catalog">
    <header><div><h3>Source catalog</h3><p>Read-only metadata. No rows are read or imported. Results depend on the saved database account’s permissions.</p></div><Button size="small" icon={<ReloadOutlined />} onClick={() => setRefresh(value => value + 1)}>Refresh catalog</Button></header>
    <div className="ontology-source-catalog__layout">
      <section aria-label="Schemas">
        <h4>Schemas</h4>
        {schemaError ? null : !catalog ? <p role="status">Loading schemas…</p> : !catalog.schemas.length ? <p>No visible schemas.</p> : <>
          <ul className="ontology-source-catalog__list">{catalog.schemas.map(item => <li key={item}>
            <button type="button" aria-pressed={schema === item} onClick={() => changeSchema(item)}><FolderOutlined /><span>{item}</span></button>
          </li>)}</ul>
          {catalog.schemaHasMore && <p role="status">Schema list limited to 100 visible results.</p>}
        </>}
      </section>
      <section aria-label="Source relations">
        <h4>{schema ? <><code>{schema}</code> · Tables and views</> : 'Tables and views'}</h4>
        {!schema ? <p>Select a schema to browse its tables and views.</p> : <>
          <form className="ontology-source-catalog__search" onSubmit={event => { event.preventDefault(); searchRelations(search); }}>
            <Input aria-label="Search tables and views" prefix={<SearchOutlined />} placeholder="Search tables and views" maxLength={100} value={search} onChange={event => setSearch(event.target.value)} />
            <Button size="small" htmlType="submit">Search</Button>
            {(search || appliedSearch) && <Button size="small" onClick={() => { setSearch(''); searchRelations(''); }}>Clear</Button>}
          </form>
          {relationError ? null : !relations ? <p role="status">Loading tables and views…</p> : relations.relations.length ? <ul className="ontology-source-catalog__list">
            {relations.relations.map(relation => <li key={relation.name}><button type="button" aria-pressed={table === relation.name} aria-label={`${relation.name} · ${relationLabel[relation.kind]}`} onClick={() => changeTable(relation.name)}>
              {relation.kind === 'view' || relation.kind === 'materialized_view' ? <EyeOutlined /> : <TableOutlined />}
              <span>{relation.name}</span><small>{relationLabel[relation.kind]}</small>
            </button></li>)}
          </ul> : <p>{appliedSearch ? 'No tables or views match the search.' : 'No visible tables or views in this schema.'}</p>}
          {relations && (offset > 0 || relations.hasMore) && <div className="ontology-source-catalog__pager" aria-label="Relation pages">
            <Button size="small" disabled={offset === 0} onClick={() => changePage(offset - CATALOG_PAGE_SIZE)}>Previous page</Button>
            <span>Page {offset / CATALOG_PAGE_SIZE + 1}</span>
            <Button size="small" disabled={!relations.hasMore || offset >= CATALOG_LAST_OFFSET} onClick={() => changePage(offset + CATALOG_PAGE_SIZE)}>Next page</Button>
          </div>}
          {relations?.hasMore && offset >= CATALOG_LAST_OFFSET && <p>Browsing limit reached. Search to narrow the results.</p>}
        </>}
      </section>
      <section aria-label="Column metadata">
        <h4>{table ? <code>{schema}.{table}</code> : 'Columns'}</h4>
        {!table ? <p>Select a table or view to inspect its column metadata.</p>
          : columnError ? null
          : !columns || columnRevision !== sourceRevision ? <p role="status">Loading column metadata…</p>
          : !columns.columns.length ? <p>No columns are visible with this connection.</p>
          : <><div className="ontology-source-catalog__columns"><table aria-label="Source columns">
            <thead><tr><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Nullable</th><th scope="col">Key</th></tr></thead>
            <tbody>{columns.columns.map(column => <tr key={column.position}><th scope="row"><code>{column.name}</code></th><td><code>{column.type}</code></td><td>{column.nullable ? 'Yes' : 'No'}</td><td>{column.primaryKey ? 'Primary key' : '—'}</td></tr>)}</tbody>
          </table></div>{columns.hasMore && <p role="status">Column list limited to 200 visible results.</p>}
          {!columns.hasMore && relations?.relations.some(relation => relation.name === table && relation.kind === 'table') &&
            <Button className="ontology-source-catalog__setup" size="small" onClick={() => onSetUpSync({ schema, table })}>Set up batch sync</Button>}
          {liveIdentity && liveIdentity === identity && columns.referencesAvailable === true && columns.schema === schema && columns.table === table &&
            <SourceCatalogReferences key={JSON.stringify([sourceRevision, schema, table, refresh])} sourceId={sourceId} schema={schema} table={table} sourceRevision={sourceRevision} />}
          </>}
      </section>
    </div>
  </section>;
}

export default function DataConnections({ warehouse, warehouseFailed, onRetryWarehouse, onCancel, onChanged }: Props) {
  const feedbackKey = useId();
  const [connectors, setConnectors] = useState<DataConnectorType[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [businessSelection, setBusinessSelection] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [canManage, setCanManage] = useState(false);
  const [reload, setReload] = useState(0);
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [sourceSearch, setSourceSearch] = useState('');
  const [sourceEngine, setSourceEngine] = useState('all');
  const [params, setParams] = useSearchParams();
  const sourceId = params.get('sourceId') ?? '';
  const selectedSchedule = params.get('batchSync') || undefined;
  const openTableExport = params.get('tableExport') === '1';
  const [exploringSource, setExploringSource] = useState('');
  const [syncSelection, setSyncSelection] = useState<{ sourceId: string; selection: BatchSyncSource }>();
  const [sourcePanel, setSourcePanel] = useState<'overview' | 'catalog' | 'sync' | 'capture'>('overview');
  const [createdSource, setCreatedSource] = useState('');
  const [captureBusy, setCaptureBusy] = useState(false);
  const exploring = !!sourceId && exploringSource === sourceId;
  const [wizard, setWizard] = useState<'choose' | 'configure' | null>(null);
  const [chosenEngine, setChosenEngine] = useState<DataConnectorEngine>();
  const [sourceProof, setSourceProof] = useState<{key: string; value: BatchSourceIdentity | undefined}>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editingId, setEditingId] = useState('');
  const [editingRevision, setEditingRevision] = useState<{ updatedAt: string; engine: DataConnectorEngine }>();
  const [editConflict, setEditConflict] = useState(false);
  const [baseline, setBaseline] = useState('');
  const [operation, setOperation] = useState<'' | 'test' | 'save' | 'delete' | 'pause' | 'business'>('');
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const active = useRef(true);
  const pendingRef = useRef(false);
  const metadataRequest = useRef<AbortController>();
  const metadataPending = useRef(new Set<string>());
  const metadataEpoch = useRef(new Map<string, number>());
  const metadataVerifying = useRef(new Set<string>());
  const sessionIdentity = useRef(getSessionState().identity).current;
  const current = () => active.current && getSessionState().identity === sessionIdentity;
  const notify = (kind: 'error' | 'success', content: string, description?: string) => {
    const session = getSessionState();
    if (current() && session.authenticated && !session.locked) message[kind]({ key: feedbackKey, content, description, className: 'explorer-toast' });
  };
  const notifyTest = (result: DataSourceTest) => notify(result.ok ? 'success' : 'error', result.message, `${result.latencyMs} ms`);
  const pending = !!operation;
  const businessId = businessSelection || warehouse?.connectionId || '';
  const businessSources = sources.filter(source => source.engine === 'postgresql' && !isConnectionPaused(source));
  const chooseBusinessDatabase = async () => {
    const source = businessSources.find(item => item.id === businessId);
    if (!source || pendingRef.current) return;
    pendingRef.current = true;
    setOperation('business');
    try {
      await selectBusinessDatabase(source);
      if (!current()) return;
      notify('success', 'Business database selected. Restart ETL Workers and the PostgreSQL CDC relay to apply it.');
      onRetryWarehouse();
      onChanged?.();
    } catch (error) {
      notify('error', errorText(error));
    } finally {
      pendingRef.current = false;
      if (current()) setOperation('');
    }
  };
  const blocked = pending || loading || !!loadError || !canManage;
  const connector = connectors.find((item) => item.engine === draft?.engine);
  const selected = sources.find((item) => item.id === editingId);
  const viewedSource = sources.find((item) => item.id === sourceId);
  const detailsOpen = !!viewedSource && canManage && !loading && !loadError && !wizard;
  const sourcePaused = isConnectionPaused(viewedSource);
  const pauseAvailable = viewedSource?.engine === 'postgresql' && viewedSource.pauseAvailable === true;
  const chosen = connectors.find((item) => item.engine === chosenEngine);
  const proofKey = viewedSource ? `${sessionIdentity}:${viewedSource.id}:${viewedSource.updatedAt}` : '';
  const activeProof = sourceProof?.key === proofKey ? sourceProof.value : undefined;
  const viewedType = connectors.find((item) => item.engine === viewedSource?.engine);
  const captureAvailable = !sourcePaused && Boolean(viewedType?.cdcSupported || viewedType?.captureKind === 'dmdrs' || viewedType?.captureKind === 'object_events');
  const captureLabel = viewedType?.captureKind === 'object_events' ? 'Object events' : viewedType?.captureKind === 'dmdrs' ? 'DMDRS CDC' : 'Continuous CDC';
  const catalogAvailable = !sourcePaused && viewedSource?.catalogAvailable && viewedType?.catalogAvailable;
  const syncAvailable = sourcePaused || viewedSource?.catalogAvailable && viewedType?.catalogAvailable;
  const sameDestination = !!draft && !!selected && draft.engine === selected.engine && Object.entries(draft.config).every(([key, value]) => {
    const saved = selected.config[key as keyof DataSourceConfig];
    if (key === 'authDatabase') return mongoAuthDatabase(String(value ?? '')) === mongoAuthDatabase(selected.config.authDatabase);
    if (key === 'provider') return (value ?? 'self_hosted') === (saved ?? 'self_hosted');
    if (key === 'addressingStyle') return (value ?? 'path') === (saved ?? 'path');
    return typeof value === 'string' ? value.trim() === String(saved) : value === saved;
  });
  const canReuseSecret = !!selected?.credentialsConfigured && sameDestination;
  const dirty = !!draft && JSON.stringify(draft) !== baseline;

  useEffect(() => { active.current = true; return () => { active.current = false; metadataRequest.current?.abort(); message.destroy(feedbackKey); }; }, [feedbackKey]);
  useEffect(() => {
    setExploringSource(''); setSyncSelection(undefined); setSourceProof(undefined);
    setSourcePanel(selectedSchedule ? 'sync' : 'overview');
  }, [sourceId]);
  useEffect(() => { if (selectedSchedule) setSourcePanel('sync'); }, [selectedSchedule]);
  useEffect(() => {
    const session = getSessionState();
    if (loading && current() && session.authenticated && !session.locked) return message.loading({ key: `${feedbackKey}-loading`, content: 'Loading data sources…', className: 'explorer-toast' });
  }, [loading, feedbackKey, sessionIdentity]);
  useEffect(() => {
    if (!dirty && !pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty, pending]);
  useEffect(() => {
    let valid = true;
    setLoading(true); setLoadError('');
    fetchCurrentUserContext().then(async (context) => {
      if (!valid || !current()) return;
      const allowed = context.user.is_superuser === true;
      setCanManage(allowed);
      if (!allowed) return;
      const [types, saved] = await Promise.all([fetchDataConnectorTypes(), fetchDataSources()]);
      if (valid && current()) { metadataVerifying.current.clear(); setConnectors(types); setSources(saved); }
    }).catch((failure) => { if (valid && current()) setLoadError(errorText(failure)); })
      .finally(() => { if (valid && current()) setLoading(false); });
    return () => { valid = false; };
  }, [reload, sessionIdentity]);

  const navigate = (action: () => void) => {
    if (pendingRef.current) return;
    if (dirty) {
      setConfirmation({ title: 'Discard unsaved changes?', detail: 'Your unsaved connection settings will be discarded.', label: 'Discard changes', action: async () => { if (!current() || pendingRef.current) return false; action(); return true; } });
    } else action();
  };
  const resetFeedback = () => message.destroy(feedbackKey);
  const closeWizard = () => {
    setWizard(null); setDraft(null); setBaseline(''); setEditingId(''); setEditingRevision(undefined); setEditConflict(false); setChosenEngine(undefined); resetFeedback();
  };
  const reloadSavedSource = () => { closeWizard(); setReload(value => value + 1); };
  const newSource = () => navigate(() => {
    setDraft(null); setBaseline(''); setEditingId(''); setEditingRevision(undefined); setEditConflict(false); setChosenEngine(undefined); setCategory('all'); setSearch('');
    setWizard('choose'); resetFeedback();
  });
  const chooseAgain = () => navigate(() => {
    setChosenEngine(draft?.engine);
    setDraft(null); setBaseline(''); setWizard('choose'); resetFeedback();
  });
  const selectSource = (id?: string) => setParams((currentParams) => {
    const next = new URLSearchParams(currentParams);
    if (id) next.set('sourceId', id); else next.delete('sourceId');
    next.delete('batchSync');
    next.delete('tableExport');
    return next;
  });
  const showSource = (source?: DataSource) => {
    if (captureBusy) return;
    navigate(() => { setCreatedSource(''); selectSource(source?.id); resetFeedback(); });
  };
  const openSourcePanel = (panel: typeof sourcePanel) => {
    if (captureBusy) return;
    setSourcePanel(panel);
    setExploringSource(panel === 'catalog' && viewedSource ? viewedSource.id : '');
  };
  const configure = (type: DataConnectorType) => {
    const next = initialDraft(type);
    setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(''); setEditingRevision(undefined); setEditConflict(false); setWizard('configure'); resetFeedback();
  };
  const openSource = (source: DataSource) => navigate(() => {
    if (isConnectionPaused(source)) return;
    const next = { name: source.name, engine: source.engine, config: { ...source.config, port: String(source.config.port) }, secret: '' };
    setDraft(next); setBaseline(JSON.stringify(next)); setEditingId(source.id); setEditingRevision({ updatedAt: source.updatedAt, engine: source.engine }); setEditConflict(false); setWizard('configure'); resetFeedback();
  });
  const change = (patch: Partial<Draft>) => { setDraft((value) => value ? { ...value, ...patch } : value); resetFeedback(); };
  const changeConfig = (patch: Partial<Draft['config']>) => { if (draft) change({ config: { ...draft.config, ...patch } }); };
  const postgresEdit = Boolean(editingId && (editingRevision?.engine === 'postgresql' || draft?.engine === 'postgresql'));
  const payload = (): DataSourcePayload | null => {
    if (editConflict || (postgresEdit && !editingRevision?.updatedAt) || !draft || isConnectionPaused(selected)) return null;
    const name = draft.name.trim(), config = draft.config;
    if (!name || !config.host.trim() || !/^\d+$/.test(config.port) || Number(config.port) < 1 || Number(config.port) > 65535) { notify('error', 'Enter a connection name, host and port between 1 and 65535.'); return null; }
    if (config.auth === 'password' && !config.username.trim()) { notify('error', 'Enter the database username.'); return null; }
    if (['postgresql', 'mysql', 'oracle', 'mongodb', 'milvus', 'clickhouse'].includes(draft.engine) && !config.database.trim()) { notify('error', 'Enter the database or service name.'); return null; }
    if (draft.engine === 'redis' && !/^(0|[1-9]\d*)$/.test(config.database.trim())) { notify('error', 'Enter a non-negative Redis database index without leading zeros.'); return null; }
    if (draft.engine === 's3' && (!config.bucket?.trim() || !config.region?.trim() || !config.username.trim())) { notify('error', 'Enter a bucket, signing region and access key ID.'); return null; }
    if (!draft.secret && !canReuseSecret) {
      notify('error', editingId ? 'Enter credentials again after changing the destination, transport, or authentication.' : 'Enter credentials for this data source.');
      return null;
    }
    return { name, engine: draft.engine, ...(postgresEdit && editingRevision ? { expectedUpdatedAt: editingRevision.updatedAt } : {}),
      config: { ...config, host: config.host.trim(), port: Number(config.port), username: config.username.trim(), database: config.database.trim(),
        ...(draft.engine === 's3' ? { bucket: config.bucket?.trim(), region: config.region?.trim() } : {}),
        ...(draft.engine === 'mongodb' ? { authDatabase: mongoAuthDatabase(config.authDatabase) } : {}),
      },
      credentials: draft.secret ? { [authFields[config.auth].key]: draft.secret } : {} };
  };
  const mutate = async (kind: 'test' | 'save' | 'delete' | 'pause', action: () => Promise<void>, draftEdit = false) => {
    if (!current() || pendingRef.current || blocked) return false;
    pendingRef.current = true; setOperation(kind); resetFeedback();
    try { await action(); return current(); }
    catch (failure) {
      if (current()) {
        const response = (failure as { response?: { status?: number; data?: { detail?: { code?: string }; errors?: { code?: string } } } })?.response;
        const code = response?.data?.errors?.code ?? response?.data?.detail?.code;
        if (draftEdit && response?.status === 409 && code === 'source_revision_changed') {
          setEditConflict(true);
          setDraft(value => value ? { ...value, secret: '' } : value);
          notify('error', 'This saved source changed after you opened the editor. The entered password was cleared. Reload the saved source and reopen Edit before testing or saving.');
        } else {
          notify('error', errorText(failure));
        }
      }
      return false;
    }
    finally { pendingRef.current = false; if (current()) setOperation(''); }
  };
  const test = async () => {
    const body = payload();
    if (!body) return;
    await mutate('test', async () => {
      const result = await testDataSource(body, editingId || undefined);
      notifyTest(result);
    }, postgresEdit);
  };
  const refreshCapabilities = (id: string) => {
    if (!current() || metadataVerifying.current.has(id)) return;
    metadataPending.current.add(id);
    metadataRequest.current?.abort();
    const request = new AbortController(); metadataRequest.current = request;
    const pendingIds = new Set(metadataPending.current);
    const epochs = new Map([...pendingIds].map(itemId => [itemId, metadataEpoch.current.get(itemId) ?? 0]));
    setSources(items => items.map(item => pendingIds.has(item.id) ? { ...item, capabilities: undefined } : item));
    void fetchDataSources(request.signal).then(saved => {
      if (!request.signal.aborted && current()) {
        const byId = new Map(saved.map(item => [item.id, item]));
        setSources(items => items.map(item => {
          const updated = byId.get(item.id);
          return pendingIds.has(item.id) && !metadataVerifying.current.has(item.id) &&
            epochs.get(item.id) === (metadataEpoch.current.get(item.id) ?? 0) && updated?.updatedAt === item.updatedAt
            ? { ...item, capabilities: updated.capabilities } : item;
        }));
      }
    }).catch(() => { /* Unavailable metadata is not inferred from the controls. */ })
      .finally(() => {
        if (metadataRequest.current === request) {
          metadataRequest.current = undefined;
          pendingIds.forEach(id => metadataPending.current.delete(id));
        }
      });
  };
  const invalidateVerificationCapability = (id: string) => {
    if (!current()) return;
    metadataVerifying.current.add(id);
    const epoch = (metadataEpoch.current.get(id) ?? 0) + 1;
    metadataEpoch.current.set(id, epoch);
    metadataPending.current.delete(id);
    setSources(items => items.map(item => item.id === id ? { ...item, capabilities: undefined } : item));
    return () => { if (metadataEpoch.current.get(id) === epoch) metadataVerifying.current.delete(id); };
  };
  const save = async () => {
    const body = payload();
    if (!body) return;
    await mutate('save', async () => {
      const creating = !editingId;
      const source = editingId ? await updateDataSource(editingId, body) : await createDataSource(body);
      if (!current()) return;
      setSources((items) => [...items.filter((item) => item.id !== source.id), source].sort((a, b) => a.name.localeCompare(b.name)));
      closeWizard();
      setCreatedSource(creating ? source.id : '');
      selectSource(source.id); setExploringSource(''); setSyncSelection(undefined);
      refreshCapabilities(source.id);
      notify('success', 'Source saved. Its connectivity was tested with a read-only request.'); onChanged?.();
    }, postgresEdit);
  };
  const testSaved = async () => {
    if (!viewedSource || sourcePaused || blocked || !viewedType?.driverAvailable) return;
    const source = viewedSource;
    await mutate('test', async () => {
      const response = await apiClient.post<DataSourceTest | { code: number; data: DataSourceTest }>(`/etl/data-connections/${encodeURIComponent(source.id)}/test`, undefined, { timeout: 45000 });
      if (!current()) return;
      const result = 'data' in response.data ? response.data.data : response.data;
      notifyTest(result);
      if (result.ok && result.testedAt) setSources((items) => items.map((item) => item.id === source.id ? { ...item, testedAt: result.testedAt! } : item));
    });
    refreshCapabilities(source.id);
  };
  const changePause = () => {
    if (!viewedSource || !pauseAvailable || blocked) return;
    const source = viewedSource;
    const paused = !sourcePaused;
    setConfirmation({
      title: paused ? 'Pause saved PostgreSQL connection?' : 'Resume saved PostgreSQL connection?',
      label: paused ? 'Confirm pause' : 'Confirm resume',
      detail: paused
        ? `Pause “${source.name}”? New tests, catalog reads, identity checks and syncs cannot use this source while paused. Existing history is kept and already committed work is not undone.`
        : `Resume “${source.name}”? This only permits new source access. Identity checks, sync review and schedule rearming are not automatic.`,
      action: async () => await mutate('pause', async () => {
        try {
          const updated = await setDataSourcePaused(source.id, { paused, expectedUpdatedAt: source.updatedAt });
          if (!current()) return;
          setSources(items => items.map(item => item.id === source.id ? updated : item));
          setExploringSource(''); setSyncSelection(undefined); setSourceProof(undefined);
          refreshCapabilities(source.id);
          notify('success', paused ? 'Connection paused. No new source work can start.'
            : 'Connection resumed. Review affected syncs and explicitly rearm schedules; nothing was started.');
          onChanged?.();
        } catch {
          if (current()) { setConfirmation(null); setReload(value => value + 1); }
          throw new Error('The connection status change could not be confirmed. Review the refreshed source before retrying.');
        }
      }),
    });
  };
  const remove = () => {
    if (!viewedSource || blocked) return;
    const source = viewedSource;
    setConfirmation({ title: 'Delete data source?', detail: `Delete “${source.name}” and its stored credentials? This retains the external database and all data.`, label: 'Delete source', action: async () => {
      const done = await mutate('delete', async () => {
        await deleteDataSource(source.id);
        if (!current()) return;
        setSources((items) => items.filter((item) => item.id !== source.id)); selectSource(); notify('success', 'Data source deleted.'); onChanged?.();
      });
      return done;
    } });
  };
  const filtered = connectors.filter((item) => (category === 'all' || item.category === category) && `${item.name} ${item.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  const secretLabel = draft ? authFields[draft.config.auth].label : '';
  const usernameLabel = draft?.config.auth === 'access_key' ? 'Access key ID' : 'Username';

  const labelFor = (engine: DataConnectorEngine) => connectors.find((type) => type.engine === engine)?.name || engine;
  const filteredSources = sources.filter((source) => (sourceEngine === 'all' || source.engine === sourceEngine)
    && `${source.name} ${source.config.host} ${source.config.bucket || source.config.database} ${labelFor(source.engine)}`.toLowerCase().includes(sourceSearch.trim().toLowerCase()));
  useExplorerErrorToast(loadError, 'Unable to load data sources', () => { if (!pendingRef.current) setReload(value => value + 1); });
  useExplorerErrorToast(!loading && !loadError && !canManage && (sourceId || openTableExport) ? 'Only administrators can manage external sources.' : undefined, 'Access unavailable');
  useExplorerErrorToast(!loading && !loadError && canManage && sourceId && !viewedSource ? 'The linked data source is not available.' : undefined, 'Source unavailable');
  const visibleConnector = wizard === 'choose' ? chosen : wizard === 'configure' ? connector : viewedType;
  useExplorerErrorToast(visibleConnector && !visibleConnector.driverAvailable ? visibleConnector.prerequisite : undefined, 'Connector unavailable');

  return <>
    {detailsOpen && viewedSource && <Modal className="flow-surface-modal ontology-connectors ontology-connections-modal" title="Source details" open centered width={960}
        onCancel={() => showSource()} closable={!pending && !captureBusy} keyboard={!pending && !captureBusy && !confirmation} maskClosable={false}
        footer={<Button disabled={pending || captureBusy} onClick={() => showSource()}>Done</Button>}>
      <section className="ontology-connections-detail" aria-label="Source details" aria-busy={pending || captureBusy}>
        <header>
          <div className="ontology-connections-detail__identity"><ConnectorMark engine={viewedSource.engine} /><div><span className="ontology-connectors__eyebrow">Source details · {labelFor(viewedSource.engine)}</span><h2 title={viewedSource.name}>{viewedSource.name}</h2>
            {sourcePaused && <span className="ontology-connectors__capability is-unavailable" role="status">Paused</span>}</div></div>
          <div className="ontology-connections-detail__actions">
            <Button disabled={blocked || captureBusy || sourcePaused || !viewedType?.driverAvailable} loading={operation === 'test'} onClick={() => void testSaved()}>Test saved connection</Button>
            <Button type="primary" disabled={blocked || captureBusy || sourcePaused || !viewedType} onClick={() => openSource(viewedSource)}>Edit connection</Button>
            {pauseAvailable && <Button disabled={blocked || captureBusy} loading={operation === 'pause'} onClick={changePause}>{sourcePaused ? 'Resume connection' : 'Pause connection'}</Button>}
          </div>
        </header>
        {createdSource === viewedSource.id && <div className="ontology-connections-detail__next">
          <strong>Connection verified and saved</strong><p>Choose the next step, or close this dialog and configure it later. Nothing starts automatically.</p>
          <div>{captureAvailable && <Button disabled={blocked || captureBusy} onClick={() => openSourcePanel('capture')}>Configure {captureLabel}</Button>}
            {catalogAvailable && <Button disabled={blocked || captureBusy} onClick={() => openSourcePanel('catalog')}>Explore source</Button>}</div>
        </div>}
        <nav className="ontology-connections-detail__nav" aria-label="Source sections">
          <Button aria-pressed={sourcePanel === 'overview'} disabled={pending || captureBusy} onClick={() => openSourcePanel('overview')}>Overview</Button>
          {catalogAvailable && <Button aria-pressed={sourcePanel === 'catalog'} disabled={pending || captureBusy} onClick={() => openSourcePanel('catalog')}>Source catalog</Button>}
          {syncAvailable && <Button aria-pressed={sourcePanel === 'sync'} disabled={pending || captureBusy} onClick={() => openSourcePanel('sync')}>Batch syncs</Button>}
          {captureAvailable && <Button aria-pressed={sourcePanel === 'capture'} disabled={pending || captureBusy} onClick={() => openSourcePanel('capture')}>{captureLabel}</Button>}
          <Link className="ui-btn ui-btn-link" to={ontologyCatalogHref(new URLSearchParams({ publishedSource: viewedSource.id }).toString(), 'published')}
            aria-disabled={pending || captureBusy || undefined} tabIndex={pending || captureBusy ? -1 : undefined}
            onClick={event => { if (pending || captureBusy) event.preventDefault(); }}>View published object types <ArrowRightOutlined /></Link>
        </nav>
        {sourcePanel === 'overview' && <>
        <dl className="ontology-connections-summary">
          <div><dt>Endpoint</dt><dd><code>{endpoint(viewedSource.config)}</code></dd></div>
          <div><dt>Hosting provider</dt><dd>{providerLabel(viewedSource.config.provider)}</dd></div>
          {viewedSource.engine === 's3' && <>
            <div><dt>Bucket</dt><dd>{viewedSource.config.bucket}</dd></div><div><dt>Signing region</dt><dd>{viewedSource.config.region}</dd></div>
            <div><dt>Bucket addressing</dt><dd>{addressingStyles[viewedSource.config.addressingStyle ?? 'path']}</dd></div>
          </>}
          {viewedSource.config.database && <div><dt>{databaseLabel(viewedSource.engine)}</dt><dd>{viewedSource.config.database}</dd></div>}
          <div><dt>Authentication</dt><dd>{authFields[viewedSource.config.auth].method}</dd></div>
          {viewedSource.engine === 'mongodb' && <div>
            <dt>Authentication database</dt><dd>{mongoAuthDatabase(viewedSource.config.authDatabase)}</dd>
          </div>}
          <div><dt>Transport</dt><dd>{viewedSource.engine === 'dameng' ? 'Vendor client default' : viewedSource.config.tls ? 'TLS verification enabled' : 'TLS disabled'}</dd></div>
          <div><dt>Credentials</dt><dd>{viewedSource.credentialsConfigured ? 'Configured' : 'Not configured'}</dd></div>
          <div><dt>Last connectivity test</dt><dd>{formatDate(viewedSource.testedAt)}</dd></div>
          {sourcePaused && <div><dt>Paused at</dt><dd>{formatDate(viewedSource.pausedAt)}</dd></div>}
        </dl>
        <SavedConnectionCapabilities capabilities={viewedSource.capabilities} />
        <p className="ontology-connectors__scope"><DatabaseOutlined /> These settings do not import or synchronize tables.</p>
        {sourcePaused && <p>This source cannot be tested, explored, verified or edited while paused. Sync history remains available.</p>}
        {viewedSource.engine === 'postgresql' && !sourcePaused && <SourceIdentity key={`identity:${proofKey}`} source={viewedSource} identity={activeProof}
          onIdentity={value => { if (current()) setSourceProof({ key: proofKey, value }); }}
          onVerificationStarted={() => invalidateVerificationCapability(viewedSource.id)}
          onVerificationSettled={() => { metadataVerifying.current.delete(viewedSource.id); refreshCapabilities(viewedSource.id); }} disabled={blocked} />}
        <div className="ontology-connections-detail__destructive"><Button size="small" danger icon={<DeleteOutlined />} disabled={blocked || captureBusy} onClick={remove}>Delete source</Button></div>
        </>}
        {sourcePanel === 'catalog' && exploring && catalogAvailable && <SourceCatalog key={JSON.stringify(["catalog", sessionIdentity, viewedSource.id])} sourceId={viewedSource.id} sourceRevision={viewedSource.updatedAt} onSetUpSync={selection => { setSyncSelection({ sourceId: viewedSource.id, selection }); openSourcePanel('sync'); }} />}
        {sourcePanel === 'sync' && syncAvailable && <SourceBatchSyncs key={`sync:${proofKey}`} source={viewedSource} sourceProof={sourcePaused ? undefined : activeProof} selection={!sourcePaused && syncSelection?.sourceId === viewedSource.id ? syncSelection.selection : undefined} selectedSchedule={selectedSchedule} onComposerClosed={() => setSyncSelection(undefined)} />}
        {sourcePanel === 'capture' && captureAvailable && <>
          {viewedType?.cdcSupported && <SourceCdc key={`cdc:${proofKey}`} source={viewedSource} onBusyChange={setCaptureBusy} />}
          {viewedType?.captureKind === 'dmdrs' && <SourceDamengCdc key={`dmdrs:${proofKey}`} source={viewedSource} onBusyChange={setCaptureBusy} />}
          {viewedType?.captureKind === 'object_events' && <SourceObjectEvents key={`events:${proofKey}`} source={viewedSource} onBusyChange={setCaptureBusy} />}
        </>}
      </section></Modal>}

    <Modal className="flow-surface-modal ontology-connectors ontology-connections-inventory-modal" title="Data Connections" centered width={1280}
      open={!detailsOpen && !(wizard && canManage)} onCancel={onCancel} closable={!pending && !captureBusy}
      keyboard={!pending && !captureBusy} maskClosable={false} footer={<Button disabled={pending || captureBusy} onClick={onCancel}>Done</Button>}>
      <div className="ontology-connections-workspace">
        <p className="ontology-connections-intro">Connect a data source, choose your ETL database, or validate a data flow with sample data.</p>
        <section className="ontology-connections-warehouse" aria-label="Workspace warehouse" aria-busy={!warehouse && !warehouseFailed}>
          <div className="ontology-connections-warehouse__identity">
            {warehouse && <ConnectorMark engine={warehouse.engine} />}
            <div><h2>Business database</h2>
              {warehouse ? <p>PostgreSQL · <code>{warehouse.database}</code> · {warehouse.name}</p>
                : warehouseFailed ? <p role="alert">Unable to read the warehouse connection.</p>
                  : <p role="status">Loading warehouse connection…</p>}
            </div>
          </div>
          {warehouse && <Link className="ui-btn ui-btn-default" to={ontologyCatalogHref(params.toString(), 'backed')}>Browse warehouse tables <ArrowRightOutlined /></Link>}
          {warehouseFailed && <Button size="small" icon={<ReloadOutlined />} onClick={onRetryWarehouse}>Retry warehouse</Button>}
          {canManage && <div>
            <label>ETL business connection <NativeSelect aria-label="ETL business connection" value={businessId}
              disabled={blocked} onChange={event => setBusinessSelection(event.target.value)}>
              <option value="">Select a PostgreSQL connection</option>
              {businessSources.map(source => <option key={source.id} value={source.id}>{source.name} · {source.config.database}</option>)}
            </NativeSelect></label>
            <Button disabled={blocked || !businessId || businessId === warehouse?.connectionId}
              loading={operation === 'business'} onClick={chooseBusinessDatabase}>Use for ETL</Button>
            <p>Stop Workers and the current PostgreSQL CDC capture before switching. Configure capture for the selected database, then restart Workers and the relay.</p>
          </div>}
        </section>

        {canManage && <section className="ontology-connections-inventory" aria-label="External sources" aria-busy={loading || pending}>
        <header><h2>External sources {!loading && !loadError && <span>{sources.length}</span>}</h2>
          <div className="ontology-connectors__actions">
            <Button size="small" icon={<ReloadOutlined />} disabled={pending || loading} onClick={() => setReload(value => value + 1)}>Refresh sources</Button>
            <Button type="primary" icon={<PlusOutlined />} disabled={blocked} onClick={newSource}>New source</Button>
          </div>
        </header>
        {!loading && !loadError && <>
        {!!sources.length && <div className="ontology-connections-inventory__toolbar">
          <Input aria-label="Search sources" placeholder="Search sources" prefix={<SearchOutlined />} value={sourceSearch} allowClear onChange={(event) => setSourceSearch(event.target.value)} />
          <label className="ontology-connections-inventory__filter">Connector <NativeSelect aria-label="Filter sources by connector" popupWidth={224} value={sourceEngine} onChange={(event) => setSourceEngine(event.target.value)}>
            <option value="all">All connectors</option>{connectors.map((type) => <option key={type.engine} value={type.engine}>{type.name}</option>)}
          </NativeSelect></label>
          <span className="ontology-connections-inventory__count" role="status">{filteredSources.length} of {sources.length} sources</span>
        </div>}
        {filteredSources.length ? <div className="ontology-connections-inventory__table" role="region" aria-label="Saved external sources" tabIndex={0}><table>
          <colgroup><col className="ontology-connections-inventory__source-column" /><col className="ontology-connections-inventory__connector-column" /><col className="ontology-connections-inventory__endpoint-column" /><col className="ontology-connections-inventory__tested-column" /></colgroup>
          <thead><tr><th scope="col">Source</th><th scope="col">Connector</th><th scope="col">Endpoint</th><th scope="col">Last connectivity test</th></tr></thead>
          <tbody>{filteredSources.map((source) => <tr key={source.id}>
            <td><div className="ontology-connections-source"><button type="button" className="ontology-connections-source-link" title={source.name} onClick={() => showSource(source)}><ConnectorMark engine={source.engine} /><span>{source.name}</span></button>
              {isConnectionPaused(source) && <span className="ontology-connections-source__paused">Paused</span>}</div></td>
            <td>{labelFor(source.engine)}</td><td><code title={endpoint(source.config)}>{endpoint(source.config)}</code></td><td><time dateTime={source.testedAt || undefined}>{formatDate(source.testedAt)}</time></td>
          </tr>)}</tbody>
        </table></div> : <div className="ontology-connections-inventory__empty" role="status">
          <DatabaseOutlined /><strong>{sources.length ? 'No sources match your filters' : 'No external sources configured'}</strong>
          <p>{sources.length ? 'Try another search or connector.' : 'Add a source to configure ingestion from a database or object store.'}</p>
        </div>}
        </>}
        </section>}
        {canManage && !loading && !loadError && <>
        <section className="ontology-connections-sandbox" aria-label="Test data flows">
          <header>
            <div><h2>Test data flows <span className="ontology-connections-sandbox__badge">Sandbox</span></h2>
              <p>Choose what you want to test. These tools use dedicated sample data and test destinations.</p></div>
          </header>
          <div className="ontology-connections-sandbox__tools">
        <TableExports openFromLink={openTableExport} onLinkClose={() => setParams((current) => {
          const next = new URLSearchParams(current);
          next.delete('tableExport');
          return next;
        }, { replace: true })} />
        <SandboxCdcReplay />
        <SyntheticPushInbox />
          </div>
        </section>
        </>}
        {canManage && !sourceId && <SandboxHandoffRecipientProof authorizedIdentity={sessionIdentity} />}
      </div>
    </Modal>

    {!!wizard && canManage && <Modal className="flow-surface-modal ontology-connectors ontology-connectors__wizard" title={editingId ? 'Edit connection' : 'New source'} open centered
      onCancel={() => navigate(closeWizard)} closable={!pending} keyboard={!pending && !confirmation} maskClosable={false} footer={null} width={780}>
      <div className="ontology-connectors__content" aria-busy={pending}>
          {!editingId && <ol className="ontology-connectors__steps" aria-label="New source steps">
            <li aria-current={wizard === 'choose' ? 'step' : undefined}><span>1</span> Choose connector</li>
            <li aria-current={wizard === 'configure' ? 'step' : undefined}><span>2</span> Configure &amp; verify</li>
          </ol>}
          {wizard === 'choose' && <section className="ontology-connectors__catalog">
            <header><h2>Choose a connector</h2><p>Select a database or object storage type.</p></header>
            <div className="ontology-connectors__picker-tools">
              <Input aria-label="Search connector types" prefix={<SearchOutlined />} value={search} placeholder="Search connectors" onChange={(event) => setSearch(event.target.value)} allowClear />
              <label>Category <NativeSelect aria-label="Filter connector category" value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</NativeSelect></label>
            </div>
            <div className="ontology-connectors__grid">{filtered.map((item) => <button type="button" key={item.engine} className="ontology-connectors__card" aria-label={item.name} aria-pressed={chosen?.engine === item.engine} disabled={blocked} onClick={() => setChosenEngine(item.engine)}>
              <ConnectorMark engine={item.engine} /><strong title={item.name}>{item.name}</strong><small>{item.description}</small><span className={`ontology-connectors__capability${item.driverAvailable ? '' : ' is-unavailable'}`}>{item.driverAvailable ? 'Read-only connection test' : 'Driver required'}</span>
            </button>)}</div>
            {!filtered.length && <p className="ontology-connectors__empty">No connectors match your search.</p>}
            <div className="ontology-connectors__actions ontology-connectors__wizard-footer"><span /><Button disabled={pending} onClick={() => navigate(closeWizard)}>Cancel</Button><Button type="primary" disabled={blocked || !chosen?.driverAvailable} onClick={() => { if (chosen?.driverAvailable) configure(chosen); }}>Continue</Button></div>
          </section>}
          {wizard === 'configure' && draft && connector && <form className="ontology-connectors__form" onSubmit={(event) => event.preventDefault()} onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.target instanceof HTMLInputElement && !event.target.closest('[role="combobox"]')) event.preventDefault(); }}>
            <header className="ontology-connectors__form-heading"><div><ConnectorMark engine={draft.engine} /><span><span className="ontology-connectors__eyebrow">{connector.name}</span><h2>{editingId ? 'Source settings' : `New ${connector.name} source`}</h2></span></div>{!editingId && <Button size="small" icon={<ArrowLeftOutlined />} disabled={blocked} onClick={chooseAgain}>Change connector</Button>}</header>
            <fieldset disabled={blocked || editConflict}>
              <section className="ontology-connectors__section"><h3>Source details</h3>
                <div className="ontology-connectors__credentials">
                  <label>Connection name<Input aria-label="Connection name" value={draft.name} maxLength={200} autoComplete="off" placeholder="Production source" onChange={(event) => change({ name: event.target.value })} /></label>
                  <label>Hosting provider<Select<DataSourceProvider> aria-label="Hosting provider" className="ontology-connectors__provider-select"
                    value={draft.config.provider ?? 'self_hosted'} options={providerOptions} disabled={blocked || editConflict}
                    onChange={provider => changeConfig({ provider: provider === 'self_hosted' ? undefined : provider })} /></label>
                </div>
              </section>
              <section className="ontology-connectors__section"><h3>Connection</h3><div className="ontology-connectors__endpoint"><label>Host<Input aria-label="Host" value={draft.config.host} maxLength={253} autoComplete="off" placeholder={draft.engine === 's3' ? 's3.region.example.com' : 'database.example.com'} onChange={(event) => changeConfig({ host: event.target.value })} /></label><label>Port<Input aria-label="Port" value={draft.config.port} inputMode="numeric" maxLength={5} onChange={(event) => changeConfig({ port: event.target.value })} /></label></div>
                {draft.engine === 's3' && <>
                  <p className="ontology-connectors__help">Use the regional hostname of your S3-compatible service. Enter the bucket name separately.</p>
                  <div className="ontology-connectors__credentials">
                    <label>Bucket<Input aria-label="Bucket" value={draft.config.bucket} maxLength={63} autoComplete="off" onChange={event => changeConfig({ bucket: event.target.value })} /></label>
                    <label>Signing region<Input aria-label="Signing region" value={draft.config.region} maxLength={128} autoComplete="off" placeholder="us-east-1" onChange={event => changeConfig({ region: event.target.value })} /></label>
                  </div>
                  <label>Bucket addressing<NativeSelect aria-label="Bucket addressing" value={draft.config.addressingStyle ?? 'path'} disabled={blocked || editConflict}
                    onChange={event => changeConfig({ addressingStyle: event.target.value === 'virtual' ? 'virtual' : undefined })}>
                    {Object.entries(addressingStyles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </NativeSelect></label>
                </>}
                {draft.engine === 'clickhouse' && <p className="ontology-connectors__help">Enter your ClickHouse HTTP or HTTPS endpoint.</p>}
                {!['elasticsearch', 'dameng', 's3'].includes(draft.engine) && <label>{databaseLabel(draft.engine)}<Input aria-label={databaseLabel(draft.engine)} value={draft.config.database} maxLength={draft.engine === 'mongodb' ? 63 : 128} inputMode={draft.engine === 'redis' ? 'numeric' : undefined} autoComplete="off" onChange={(event) => changeConfig({ database: event.target.value })} /></label>}
                {draft.engine !== 'dameng' ? <><Checkbox aria-label="Use TLS" checked={draft.config.tls} onChange={(event) => changeConfig({ tls: event.target.checked })}>Use TLS</Checkbox><p className="ontology-connectors__help">{draft.config.tls ? 'Verify the server certificate and hostname using the API image’s trusted certificate authorities.' : 'TLS is disabled. Connection traffic is not protected by this application.'}</p></> : <p className="ontology-connectors__help">Uses the DPI client’s default transport. Vendor certificate configuration is not managed by this form.</p>}
              </section>
              <section className="ontology-connectors__section"><h3>Authentication</h3>
                {['elasticsearch', 'milvus'].includes(draft.engine) && <label>Authentication method<Select aria-label="Authentication method" value={draft.config.auth} disabled={blocked} onChange={(auth: DataSourceConfig['auth']) => { change({ config: { ...draft.config, auth, username: '' }, secret: '' }); }} options={[{ value: 'password', label: 'Username and password' }, draft.engine === 'elasticsearch' ? { value: 'api_key', label: 'API key' } : { value: 'token', label: 'Token' }]} /></label>}
                <div className="ontology-connectors__credentials">
                  {(draft.config.auth === 'password' || draft.config.auth === 'access_key') && <label>{usernameLabel}<Input aria-label={usernameLabel} value={draft.config.username} maxLength={200} autoComplete="off" onChange={(event) => changeConfig({ username: event.target.value })} /></label>}
                  <label>{secretLabel}<Input aria-label={secretLabel} type="password" value={draft.secret} maxLength={8192} autoComplete="new-password" placeholder={canReuseSecret ? `Leave blank to keep the stored ${secretLabel.toLowerCase()}` : `Enter ${secretLabel.toLowerCase()}`} onChange={(event) => change({ secret: event.target.value })} /></label>
                </div>
                {draft.engine === 'mongodb' && <label>Authentication database
                  <Input aria-label="Authentication database" value={draft.config.authDatabase ?? ''} maxLength={63}
                    autoComplete="off" placeholder="admin" onChange={(event) => changeConfig({ authDatabase: event.target.value })} />
                  <span className="ontology-connectors__help">Where the MongoDB user was created. Defaults to admin.</span>
                </label>}
                {postgresEdit && <p className="ontology-connectors__help">Replacing the saved password does not revoke the old password on the PostgreSQL server. Saving changes requires a new review of existing sync plans and schedules before they can use the new revision.</p>}
                <p className="ontology-connectors__help">Credentials are encrypted at rest and are never returned by the API. Use a dedicated account with read access.</p>
              </section>
            </fieldset>
            <p className="ontology-connectors__help">Saving runs a read-only connection test.
              {!editingId && (connector.cdcSupported || connector.captureKind === 'dmdrs' || connector.captureKind === 'object_events') && ' After saving, you can configure change capture in source details; it is optional and starts only when you enable it.'}</p>
            <div className="ontology-connectors__actions ontology-connectors__wizard-footer"><span /><Button disabled={pending} onClick={() => navigate(closeWizard)}>Cancel</Button><Button onClick={() => editConflict ? reloadSavedSource() : void test()} loading={operation === 'test'} disabled={blocked || !editConflict && !connector.driverAvailable}>{editConflict ? 'Reload saved source' : 'Test connection'}</Button><Button type="primary" onClick={() => void save()} loading={operation === 'save'} disabled={blocked || editConflict || !connector.driverAvailable || !!editingId && !dirty}>Save source</Button></div>
          </form>}
      </div>
    </Modal>}
    {confirmation && <Modal className="flow-surface-modal ontology-connectors__confirmation" title={confirmation.title} open okText={confirmation.label} confirmLoading={pending} cancelButtonProps={{ disabled: pending }} closable={!pending} maskClosable={false} onCancel={() => { if (!pendingRef.current) setConfirmation(null); }} onOk={async () => { if (await confirmation.action() && current()) setConfirmation(null); }}><p>{confirmation.detail}</p></Modal>}
  </>;
}

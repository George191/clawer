import ExplorerFilterEditor, { normalizeFilterDepth, randomId, type ExplorerSort as SortCondition } from './ExplorerFilterEditor';
import { editorValue, rowChanges, rowNullControl, type RowDraft } from './explorerRowEditing';
import  React,  {  useCallback,  useEffect,  useRef,  useState  }  from  'react';
import  {  Link,  matchPath,  Navigate,  useLocation,  useNavigate  }  from  'react-router-dom';
import  {  Button,  Checkbox,  ConfigProvider,  Input,  InputNumber,  Modal,  Select,  Space,  Spin,  Table,  Tag,  Upload,  message, NativeSelect  }  from  '@/components/ui';
import  enUS  from  '@/components/ui/locale';
import  {  AlertOutlined,  BranchesOutlined,  CodeOutlined,  DatabaseOutlined,  DownOutlined,  DownloadOutlined,  SettingOutlined,  TableOutlined,  UploadOutlined  }  from  '@/components/ui/icons';
import  '../ProductWorkspace/workspace.css';
import CdcStatusPanel from './CdcStatusPanel';
import OntologyExplorer from './OntologyExplorer';
import DataConnections from './DataConnections';
import OntologyLineage from './OntologyLineage';
import './ontology-frame.css';
import './foundry-workbench-tokens.css';
import './ontology-workspace.css';
import './explorer-resource-tools.css';
import ExplorerToolDialog from './ExplorerToolDialog';
import ExplorerCatalog from './ExplorerCatalog';
import ExplorerWorkbench, { ExplorerFilterButton, ExplorerResults, ExplorerResultsFooter, explorerResultColumns } from './ExplorerWorkbench';
import ExplorerFilterIndexNotice, { ExplorerSortIndexNotice } from './ExplorerFilterIndexNotice';
import PublishedApiWorkbench from './PublishedApiWorkbench';
import useStoragePublicationKey from './useStoragePublicationKey';
import StoragePublicationNameDialog from './StoragePublicationNameDialog';
import PublishedExplorer from './PublishedExplorer';
import { openSaved, savedPath, type SavedDetail } from '../../services/explorerSaved';
import { catalogReturnHref, compactExplorerSearch } from '../../services/explorerNavigation';
import { ExplorerTypeIcon } from './ExplorerObjectOverview';
import { explorerRowTitle, useExplorerPresentation, type ExplorerPreviewRow } from './ExplorerSelection';
import ExplorerRecordLink from './ExplorerRecordLink';
import ExplorerRowLinks from './ExplorerRowLinks';
import StorageRelationshipTypes from './StorageRelationshipTypes';
import NewObjectType from './NewObjectType';
import ExplorerTableSchema from './ExplorerTableSchema';
import ExplorerRecordMutation, { recordMutationActions } from './ExplorerRecordMutation';
import useExplorerErrorToast from './useExplorerErrorToast';
import { LayoutToolbar, useResultLayout } from './ExplorerResultLayout';
import ExplorerTableEvidence from './ExplorerTableEvidence';
import ExplorerStreamPositions, { topicRate } from './ExplorerStreamPositions';
import ExplorerIndexedProjection from './ExplorerIndexedProjection';
import ExplorerIndexedIdSample from './ExplorerIndexedIdSample';
import { useSandboxIdentity } from './SandboxMarkings';
import { getSessionState } from '../../services/session';
import { explorerResourceCapabilities } from '../../services/explorerCapabilities';
import SandboxIndexPreview, { INDEX_PREVIEW_SCHEMA, INDEX_PREVIEW_TABLE } from './SandboxIndexPreview';
import { activeExplorerFilters, appendAndFilters, blankExplorerFilter, cellFilterRows, countFilterTerms, exactCellValue, explorerFilterTerms, hasExplorerFilterInput } from './explorerFilters';
import type { ExplorerFilter } from './explorerFilters';
import { firstIncompleteExplorerFilter } from './explorerFilterDraft';
import { fetchCurrentUserContext, fetchLayers, fetchLayerTables, fetchTableData, fetchHandlerCode, queryExplorerPreview, fetchRowPreviewPolicy, fetchEtlTableColumns, fetchTableStream, fetchLayerScript, setTableOffset, validateHandlerCode, saveHandlerCode, updateEtlRow, deleteEtlRow, fetchEtlApis, createEtlApi, updateEtlApi, renameEtlApi, deleteEtlApi, fetchVulnerabilityKeywords, uploadVulnerabilityKeywords, activateVulnerabilityKeywordVersion, downloadVulnerabilityKeywordVersion, downloadCurrentVulnerabilityKeywords } from '../../services/api';
import type { RowPreviewMode, EtlDataQuery, EtlPublishedApi, VulnerabilityKeywordConfig } from '../../services/api';
import  type  {  LayerNode,  LayerTable,  QueryResult,  ExplorerPreviewResult,  EtlStreamState  }  from  '../../services/types';

type RowSnapshot = { data: QueryResult; row: Record<string, unknown>; version: string };
const emptyPreviewRows: Record<string, unknown>[] = [];
const emptyPreviewColumns: string[] = [];
type OpenApiAccess = EtlPublishedApi;
const storageToolTitles = { preview: '', schema: 'Properties & column mapping', evidence: 'Evidence', stream: 'Stream', api: 'Published APIs' };
const  EditIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Edit"  fill="none"><path  d="M5.5  17.8V19h1.2l9.9-9.9-1.2-1.2-9.9  9.9Z"  stroke="currentColor"  strokeWidth="1.6"  strokeLinejoin="round"/><path  d="m14.7  7.9  1.4-1.4a1.7  1.7  0  0  1  2.4  2.4l-1.4  1.4"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"/></svg>;
const  DeleteIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Delete"  fill="none"><path  d="M6.5  7.5h11M9.5  7.5V5.8h5v1.7m-6.7  0  .7  11h5l.7-11"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"  strokeLinejoin="round"/></svg>;
const Pipeline: React.FC = () => {
    const { pathname, search, hash } = useLocation();
    const explorerParams = new URLSearchParams(search);
    const publishedExplorer = !!matchPath('/ontology/explorer', pathname)
        && (['objectType', 'objectSet', 'savedSet'].some(key => explorerParams.has(key)) || new URLSearchParams(hash.replace(/^#/, '')).has('objectSet'));
    const compactSearch = matchPath('/ontology/explorer', pathname) ? compactExplorerSearch(search, publishedExplorer ? 'published' : 'backed') : search;
    if (compactSearch !== search) return <Navigate replace to={{ pathname, search: compactSearch, hash }} />;
    return <ConfigProvider locale={enUS}><main className="flow-console flow-console--ontology">
        <section className="flow-core">
            {matchPath('/ontology', pathname) ? <OntologyExplorer refreshToken={0} />
                : matchPath('/ontology/connections', pathname) ? <DataConnections />
                : matchPath('/ontology/lineage', pathname) ? <OntologyLineage refreshToken={0} />
                    : matchPath('/ontology/runtime', pathname) ? <FlowRuntime /> : <div className="flow-exploration-workspace">{publishedExplorer ? <PublishedExplorer /> : <DataLayersWorkspace />}</div>}
        </section>
    </main></ConfigProvider>;
};

const FlowRuntime: React.FC = () => {
    const [allowed, setAllowed] = useState<boolean>();
    const [error, setError] = useState(false);
    const [version, setVersion] = useState(0);
    useEffect(() => {
        let active = true;
        setError(false);
        fetchCurrentUserContext().then((context) => { if (active) setAllowed(context.user.is_superuser); })
            .catch(() => { if (active) setError(true); });
        return () => { active = false; };
    }, [version]);
    return <div className="flow-ontology-runtime" data-testid="ontology-runtime"><div className="flow-ontology-page-heading"><span><Link to="/ontology" style={{ color: 'inherit' }}>Object types</Link></span><h1>Runtime status</h1><p>Inspect the current ingestion and consumption infrastructure.</p></div>
        {error ? <div role="alert">Unable to verify your access.<Button onClick={() => setVersion((value) => value + 1)}>Retry</Button></div>
            : allowed === undefined ? <Spin />
                : allowed ? <CdcStatusPanel /> : <div className="flow-ontology-message">Runtime diagnostics are available to administrators. You can continue exploring data assets and tables.</div>}
    </div>;
};
const DataLayersWorkspace: React.FC = () => {
    const location = useLocation();
    const pathname = location.pathname;
    const search = location.search;

    const workspacePath = '/ontology/explorer';
    const requestedView = new URLSearchParams(search).get('view');
    const requestedSavedId = new URLSearchParams(search).get('saved') || '';
    const navigate = useNavigate();
    const catalogReturn = catalogReturnHref(new URLSearchParams(search).get('returnTo'), 'backed');
    const backToTypes = () => navigate(catalogReturn);
    const [canManage, setCanManage] = useState(false);
    const [mappingDirty, setMappingDirty] = useState(false);
    const [catalogOpen, setCatalogOpen] = useState(false);
    const [workbenchMode, setWorkbenchMode] = useState<'explore' | 'results'>('results');
    const [workbenchRows, setWorkbenchRows] = useState<ExplorerPreviewRow[]>([]);
    const [selectionEpoch, setSelectionEpoch] = useState(0);
    const [savedState, setSavedState] = useState<{ key: string; detail?: SavedDetail; error: string; loading: boolean }>();
    const [savedRetry, setSavedRetry] = useState(0);
    const [queryScope, setQueryScope] = useState('');
    const savedController = useRef<AbortController>();
    const adminPreviewIdentity = useRef('');
    const managePermission = useRef(false);
    managePermission.current = canManage;
    const [catalogLoaded, setCatalogLoaded] = useState(false);
    const [schemas, setSchemas] = useState<LayerNode[]>([]);
    const [serverTables, setServerTables] = useState<LayerTable[]>([]);
    const [data, setData] = useState<QueryResult>();
    const [previewPage, setPreviewPage] = useState<{ offset: number; limit: number; result: ExplorerPreviewResult }>();
    const previewViewport = useRef<HTMLDivElement>(null);
    const [stream, setStream] = useState<EtlStreamState>();
    const [loading, setLoading] = useState(false);
    const queryController = useRef<AbortController>();
    const catalogController = useRef<AbortController>();
    const tablesController = useRef<AbortController>();
    const policyController = useRef<AbortController>();
    const [catalogLoading, setCatalogLoading] = useState(false);
    const [policyLoading, setPolicyLoading] = useState(false);
    const queryStarted = useRef(0);
    const [queryElapsed, setQueryElapsed] = useState<number>();
    const [queryCancelled, setQueryCancelled] = useState(false);
    const [queryStatusVisible, setQueryStatusVisible] = useState(false);
    const cancelQuery = () => {
        const metadataLoading = catalogLoading || tablesLoading;
        catalogRequest.current++; tableRequest.current++;
        catalogController.current?.abort(); tablesController.current?.abort();
        policyController.current?.abort(); setPolicyLoading(false);
        if (policyLoading) setPolicy({ key: policyKey, mode: 'unavailable' });
        queryController.current?.abort();
        setQueryElapsed(performance.now() - queryStarted.current);
        setQueryCancelled(true); setLoading(false); setCatalogLoading(false); setTablesLoading(false);
        if (metadataLoading) setRequestError('Loading cancelled');
    };
    const [previewError, setPreviewError] = useState('');
    const [previewUnavailable, setPreviewUnavailable] = useState(false);
    const [previewRevision, setPreviewRevision] = useState(0);
    const [repairFields, setRepairFields] = useState<string[]>([]);
    const [tablesLoading, setTablesLoading] = useState(false);
    const explorerLoading = loading || catalogLoading || tablesLoading || policyLoading;
    useEffect(() => {
        if (explorerLoading) {
            queryStarted.current = performance.now(); setQueryElapsed(0); setQueryCancelled(false); setQueryStatusVisible(true);
            const timer = window.setInterval(() => setQueryElapsed(performance.now() - queryStarted.current), 100);
            return () => window.clearInterval(timer);
        }
        const timer = window.setTimeout(() => setQueryStatusVisible(false), 180);
        return () => window.clearTimeout(timer);
    }, [explorerLoading]);
    const [tablesLoadedSchema, setTablesLoadedSchema] = useState<string>();
    const [requestError, setRequestError] = useState('');
    const [streamError, setStreamError] = useState('');
    const [streamLoading, setStreamLoading] = useState(false);
    const [scriptNote, setScriptNote] = useState('');
    const [mutationLoading, setMutationLoading] = useState(false);
    const [keywordsOpen, setKeywordsOpen] = useState(false);
    const [keywordConfig, setKeywordConfig] = useState<VulnerabilityKeywordConfig>();
    const [keywordsLoading, setKeywordsLoading] = useState(false);
    const [keywordsError, setKeywordsError] = useState('');
    const [keywordsSaving, setKeywordsSaving] = useState(false);
    const [keywordDownload, setKeywordDownload] = useState<string>();
    const [refreshVersion, setRefreshVersion] = useState(0);
    const [pageNumber, setPageNumber] = useState(1);
    const [pageSize, setPageSize] = useState(20);
    const [offsetPartition, setOffsetPartition] = useState<number>();
    const [topicDraft, setTopicDraft] = useState('');
    const [appliedQuery, setAppliedQuery] = useState<Pick<EtlDataQuery, 'filters' | 'sorts' | 'search'>>({ filters: [], sorts: [], search: '' });
    const tableRequest = useRef(0);
    const loadedSchema = useRef('');
    const dataRequest = useRef(0);
    const metadataRequest = useRef(0);
    const apiRequest = useRef(0);
    const streamRequest = useRef(0);
    const selection = useRef('');
    const catalogRequest = useRef(0);
    const workspaceActive = useRef(true);
    const mutationPending = useRef(false);
    const validationPending = useRef(false);
    const errorText = (error: unknown) => {
        const response = (error as { response?: { data?: { errors?: unknown; detail?: unknown; message?: unknown } }; message?: string })?.response?.data;
        const detail = response?.errors ?? response?.detail ?? response?.message;
        return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (error as Error)?.message || 'Request failed';
    };
    const beginMutation = () => {
        if (!workspaceActive.current || !managePermission.current || mutationPending.current) return false;
        mutationPending.current = true; setMutationLoading(true); return true;
    };
    const endMutation = () => { mutationPending.current = false; if (workspaceActive.current) setMutationLoading(false); };
    const refreshCatalog = async () => {
        const version = ++catalogRequest.current;
        catalogController.current?.abort();
        const controller = new AbortController(); catalogController.current = controller;
        setCatalogLoading(true); setRequestError('');
        const identity = getSessionState().identity;
        const current = () => {
            const session = getSessionState();
            return version === catalogRequest.current && workspaceActive.current && !controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
        };
        try {
            const [layers, context] = await Promise.all([fetchLayers(controller.signal), fetchCurrentUserContext(controller.signal)]);
            if (!current()) return;
            adminPreviewIdentity.current = context.user.is_superuser === true ? identity : '';
            setCanManage(context.user.is_superuser === true);
            const visibleLayers = layers.map((item) => ({ ...item, key: item.schemaName || (item.key.startsWith('ts_') ? item.key : `ts_${item.key}`) })).filter((item) => item.key !== 'ts_meta');
            setSchemas(visibleLayers);

            setCatalogLoaded(true);
        } catch (error) {
            if (current() && (error as { code?: string })?.code !== 'ERR_CANCELED') { controller.abort(); throw error; }
        } finally { if (version === catalogRequest.current && workspaceActive.current) setCatalogLoading(false); }
    };

    const [selectedSchema, setSelectedSchema] = useState('');
    const [createTableSchema, setCreateTableSchema] = useState('');
    const [relationshipTypesOpen, setRelationshipTypesOpen] = useState(false);
    const [relationshipRevision, setRelationshipRevision] = useState(0);
    const creationSchemas = schemas.filter((item) => item.status === 'running' && !['ts_meta', 'ts_meta_hist'].includes(item.key));
    const canCreateTable = canManage && creationSchemas.some((item) => item.key === selectedSchema);
    const openTableWizard = () => {
        if (!canCreateTable) return;
        if (editingRowKey || scriptDirty || apisDirty || mappingDirty || mutationPending.current) { message.warning('Save or cancel current changes before creating a table'); return; }
        setCreateTableSchema(selectedSchema);
    };
    const openWizardTable = (schema: string, name: string) => {
        setCreateTableSchema('');

        setRefreshVersion((value) => value + 1);
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
        const params = new URLSearchParams(search);
        params.delete('create'); params.delete('saved'); params.set('schema', schema); params.set('table', name);
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const [table, setTable] = useState<LayerTable>({ name: '', rowCount: 0, size: '', updatedAt: '' });
    const  [partition,  setPartition]  =  useState<string>();
    const  [offsetOpen,  setOffsetOpen]  =  useState(false);
    const  [offset,  setOffset]  =  useState<number>();
    const  [offsets,  setOffsets]  =  useState<Array<{ partition: number; offset: number }>>([]);
    const [script, setScript] = useState('');
    const [savedScript, setSavedScript] = useState('');
    const [scriptLoaded, setScriptLoaded] = useState(false);
    const [scriptValidating, setScriptValidating] = useState(false);
    const scriptContent = useRef('');
    scriptContent.current = script;
    const  [inputTopics,  setInputTopics]  =  useState<string[]>([]);
    const  [activeTopicIndex,  setActiveTopicIndex]  =  useState(0);
    const  [scriptOpen,  setScriptOpen]  =  useState(false);
    const  [editingRowKey,  setEditingRowKey]  =  useState<string>();
    const [editingDraft, setEditingDraft] = useState<Record<string, string | null> | null>(null);
    const rememberedEditValues = useRef<RowDraft>({});
    const [deleteSnapshot, setDeleteSnapshot] = useState<RowSnapshot>();
    const [conflictedRows, setConflictedRows] = useState<Set<Record<string, unknown>>>(() => new Set());
    const [rowDetailsError, setRowDetailsError] = useState('');
    const editSnapshot = useRef<RowSnapshot>();
    const  [filterOpen,  setFilterOpen]  =  useState(false);
    const [filterValidationRequest, setFilterValidationRequest] = useState(0);
    const  [filterText,  setFilterText]  =  useState('');
    const  [filterRules,  setFilterRules]  =  useState<ExplorerFilter[]>([{  id:  'rule-1',  field:  'record_id',  operator:  '=',  value:  '',  active:  true,  join:  'and'  }]);
    const  [sortField,  setSortField]  =  useState('record_id');
    const  [sortDirection,  setSortDirection]  =  useState<'asc'  |  'desc'>('asc');
    const  [sortConditions,  setSortConditions]  =  useState<SortCondition[]>([]);
    const activateTool = (tool: 'filter' | 'script') => {
        if (tool === 'script' && scriptOpen) { closeScript(); return; }
        setFilterOpen(tool === 'filter' && !filterOpen);
        setScriptOpen(tool === 'script');
    };
    const [openApis, setOpenApis] = useState<OpenApiAccess[]>([]);
    const [apisDirty, setApisDirty] = useState(false);
    const [apisLoading, setApisLoading] = useState(false);
    const [apisError, setApisError] = useState('');
    const [apiRevision, setApiRevision] = useState(0);
    const [apiNameForm, setApiNameForm] = useState<{scope: string; initialName: string; id?: string}>();
    const saveApiName = async (name: string) => {
        const form = apiNameForm;
        if (!form || form.scope !== workspaceAsset || resourceView !== 'api' || !selectedTableAvailable || apisDirty || apisLoading || !beginMutation()) return;
        const target = selection.current; apiRequest.current++;
        const current = () => {
            const session = getSessionState();
            return workspaceActive.current && selection.current === target && session.authenticated && !session.locked && session.identity === previewIdentity;
        };
        try {
            const now = new Date();
            const api = form.id ? await renameEtlApi(selectedSchema, table.name, form.id, name) : await createEtlApi(selectedSchema, table.name, {
                name,
                validFrom: now.toISOString(), validUntil: new Date(now.getTime() + 30 * 86400000).toISOString(),
                filterType: [], filterSource: [], ...appliedQuery, partition,
            });
            if (!current()) return;
            setOpenApis(items => form.id ? items.map(item => item.id === form.id ? api : item) : [...items, api]);
            setApiNameForm(undefined);
            message.success(form.id ? 'Publication renamed' : 'Read API published');
        } catch (error) { if (current()) message.error(errorText(error)); } finally { endMutation(); }
    };
    const updateApi = (id: string, changes: Partial<Omit<OpenApiAccess, 'id'>>) => { setApisDirty(true); setOpenApis((current) => current.map((api) => api.id === id ? { ...api, ...changes } : api)); };
    const saveApis = async () => {
        if (!selectedTableAvailable || apisLoading || !openApis.length || !beginMutation()) return;
        const target = selection.current; apiRequest.current++;
        let savedCount = 0;
        try {
            if (openApis.some((api) => !Number.isFinite(Date.parse(api.validFrom)) || !Number.isFinite(Date.parse(api.validUntil)) || Date.parse(api.validUntil) <= Date.parse(api.validFrom))) throw new Error('API expiry must be later than its valid-from date');
            for (const api of openApis) {
                const saved = await updateEtlApi(selectedSchema, table.name, api.id, {
                    validFrom: api.validFrom, validUntil: api.validUntil, filterType: api.filterType, filterSource: api.filterSource,
                    filters: api.filters || [], sorts: api.sorts || [], search: api.search || '', partition: api.partition,
                });
                savedCount++;
                if (selection.current === target) setOpenApis((current) => current.map((item) => item.id === saved.id ? saved : item));
            }
            if (selection.current === target) setApisDirty(false);
            message.success('API access settings saved');
        } catch (error) { message.error(`${savedCount ? `${savedCount} API settings saved before the failure. ` : ''}${errorText(error)}`); } finally { endMutation(); }
    };
    const removeApi = (api: OpenApiAccess) => {
        if (!canManage || !selectedTableAvailable || apisLoading || mutationPending.current) return;
        const target = selection.current;
        Modal.confirm({ className: 'flow-surface-modal', title: 'Delete this publication and its key?', content: api.url, onOk: async () => {
            if (selection.current !== target || !beginMutation()) return;
            apiRequest.current++;
            try { await deleteEtlApi(selectedSchema, table.name, api.id); if (selection.current === target) { setOpenApis((current) => current.filter((item) => item.id !== api.id)); if (openApis.length === 1) setApisDirty(false); } }
            catch (error) { message.error(errorText(error)); throw error; } finally { endMutation(); }
        } });
    };
    const uploadKeywords = async (file: File) => {
        if (!canManage || keywordsSaving || mutationPending.current) return;
        if (file.size > 1024 * 1024) { message.error('YAML files must be at most 1 MiB'); return; }
        if (!beginMutation()) return;
        setKeywordsSaving(true);
        try {
            setKeywordConfig(await uploadVulnerabilityKeywords(file));
            setKeywordsError(''); message.success('YAML uploaded and activated; changes take effect within 30 seconds');
        } catch (error) { message.error(errorText(error)); } finally { if (workspaceActive.current) setKeywordsSaving(false); endMutation(); }
    };
    const activateKeywords = async (version: string) => {
        if (!canManage || keywordsSaving || mutationPending.current) return;
        if (!beginMutation()) return;
        setKeywordsSaving(true);
        try {
            setKeywordConfig(await activateVulnerabilityKeywordVersion(version));
            setKeywordsError(''); message.success('Version activated; changes take effect within 30 seconds');
        } catch (error) { message.error(errorText(error)); } finally { if (workspaceActive.current) setKeywordsSaving(false); endMutation(); }
    };
    const downloadKeywords = async (version?: string) => {
        setKeywordDownload(version || 'current');
        try {
            const blob = version ? await downloadVulnerabilityKeywordVersion(version) : await downloadCurrentVulnerabilityKeywords();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url; link.download = `satellite-vulnerability-keywords${version ? `-${version}` : ''}.yaml`; link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { message.error(errorText(error)); } finally { setKeywordDownload(undefined); }
    };
    const schemaNode = schemas.find((item) => item.key === selectedSchema);
    const requestedSchema = new URLSearchParams(search).get('schema');
    const selectedSchemaAvailable = catalogLoaded && !!schemaNode && requestedSchema === selectedSchema;
    const requestedTable = new URLSearchParams(search).get('table');
    const loadExplorerCatalog = !!requestedSchema || catalogOpen;
    const selectedTableAvailable = selectedSchemaAvailable && tablesLoadedSchema === selectedSchema
        && !!requestedTable && requestedTable === table.name
        && serverTables.some((item) => item.name === table.name);
    const requestedResource = (requestedView === 'schema' || requestedView === 'stream' || requestedView === 'api' || requestedView === 'evidence') ? requestedView : 'preview';
    const scriptDirty = scriptLoaded && script !== savedScript;
    const openTool = useRef<{ view: typeof requestedResource; previewKey?: string }>();
    const toolToRestore = requestedResource === 'preview' && openTool.current?.previewKey === location.key &&
        (mappingDirty || apisDirty || scriptDirty || mutationLoading) ? openTool.current : undefined;
    const resourceView = toolToRestore?.view ?? requestedResource;
    useEffect(() => {
        if (requestedResource !== 'preview') {
            openTool.current = { view: requestedResource, previewKey: location.state?.previewKey };
        } else if (toolToRestore) {
            // Keep the editor mounted while returning to its history entry.
            navigate(1);
            message.warning('Save or cancel current changes before switching views');
        } else {
            openTool.current = undefined;
        }
    }, [location.key, requestedResource, toolToRestore, navigate]);
    useEffect(() => {
        setScriptOpen(false);
    }, [selectedSchema, table.name, selectedTableAvailable]);
    const closeScript = () => {
        if (mutationPending.current) return;
        if (scriptDirty) Modal.confirm({ className: 'flow-surface-modal', title: 'Discard unsaved script changes?', content: 'Your changes have not been saved as a draft.', okText: 'Discard changes', onOk: () => { setScript(savedScript); setScriptOpen(false); } });
        else setScriptOpen(false);
    };
    useEffect(() => {
        if (!scriptDirty && !mappingDirty && !apisDirty) return;
        const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
    }, [scriptDirty, mappingDirty, apisDirty]);
    const appliedTerms = explorerFilterTerms(appliedQuery.filters);
    const filterCount = countFilterTerms(appliedTerms) + Number(Boolean(appliedQuery.search?.trim()));
    const sortCount = appliedQuery.sorts.length;
    const indexPreviewAsset = selectedSchema === INDEX_PREVIEW_SCHEMA && table.name === INDEX_PREVIEW_TABLE;
    const previewIdentity = useSandboxIdentity();
    const workspaceAsset = JSON.stringify([previewIdentity, requestedSchema, requestedTable]);
    const requestedScope = JSON.stringify([workspaceAsset, requestedSavedId]);
    const savedKey = requestedSavedId ? requestedScope : '';
    const savedScope = useRef(savedKey);
    savedScope.current = savedKey;
    const currentQueryScope = useRef(requestedScope);
    currentQueryScope.current = requestedScope;
    const activeSavedState = savedState?.key === savedKey ? savedState : undefined;
    const activeSaved = activeSavedState?.detail;
    const savedLoading = !!requestedSavedId && (!activeSavedState || activeSavedState.loading);
    const savedError = activeSavedState?.error || '';
    const savedBlocked = !!requestedSavedId && (savedLoading || !!savedError || !activeSaved);
    const queryScopeReady = queryScope === requestedScope;
    const activeSavedListId = activeSaved?.kind === 'list' ? activeSaved.id : undefined;
    function applySavedQuery(detail?: SavedDetail) {
        const query = detail?.kind === 'exploration' ? detail.content : { filters: [], sorts: [], search: '', partition: null, view: 'results' as const };
        setFilterRules(query.filters.length ? normalizeFilterDepth(query.filters) : [blankExplorerFilter('rule-1')]);
        setFilterText(query.search || ''); setSortField(query.sorts[0]?.field || ''); setSortDirection(query.sorts[0]?.direction || 'asc');
        setSortConditions(query.sorts.slice(1)); setPartition(query.partition || undefined);
        setAppliedQuery({ filters: query.filters, sorts: query.sorts, search: query.search || '' });
        setWorkbenchMode(query.view); setWorkbenchRows([]); setSelectionEpoch(value => value + 1); setPageNumber(1);
        setData(undefined); setPreviewPage(undefined); setPreviewError(''); setPreviewUnavailable(false);
        setQueryScope(requestedScope);
    }
    const explorerPresentation = useExplorerPresentation(
        selectedTableAvailable && previewIdentity ? selectedSchema : '', table.name,
        JSON.stringify([previewIdentity, refreshVersion, previewRevision]),
    );
    const [policyRetry, setPolicyRetry] = useState(0);
    const [policy, setPolicy] = useState<{ key: string; mode: RowPreviewMode | 'unavailable'; error?: string }>();
    const previewVisible = requestedResource === 'preview' || !!location.state?.previewKey;
    const previewRequested = previewVisible && !indexPreviewAsset;
    const policyKey = previewIdentity && selectedTableAvailable && previewRequested ?
        JSON.stringify([previewIdentity, pathname, selectedSchema, table.name, refreshVersion, policyRetry]) : '';
    const policyMode = policyKey && policy?.key === policyKey ? policy.mode : undefined;
    const showIndexPreview = previewVisible && indexPreviewAsset;
    const showPreview = policyMode === 'classic';
    const classicPreviewScope = useRef('');
    classicPreviewScope.current = showPreview ? policyKey : '';
    useEffect(() => {
        if (!policyKey) { setPolicy(undefined); setPolicyLoading(false); return; }
        const read = new AbortController();
        policyController.current = read; setPolicyLoading(true);
        const current = () => {
            const session = getSessionState();
            return !read.signal.aborted && session.authenticated && !session.locked && session.identity === previewIdentity;
        };
        void fetchRowPreviewPolicy(selectedSchema, table.name, read.signal).then(value => {
            if (current()) setPolicy({ key: policyKey, mode: value?.mode === 'classic' || value?.mode === 'indexed_id_required' || value?.mode === 'indexed_projection_required' ? value.mode : 'unavailable' });
        }).catch(error => { if (current() && error?.code !== 'ERR_CANCELED') setPolicy({ key: policyKey, mode: 'unavailable', error: errorText(error) }); })
            .finally(() => { if (current()) setPolicyLoading(false); });
        return () => read.abort();
    }, [policyKey]);
    useEffect(() => {
        if (showPreview) return;
        editSnapshot.current = undefined;
        setData(undefined); setPreviewPage(undefined); setEditingRowKey(undefined); setEditingDraft(null);
        setDeleteSnapshot(undefined); setPreviewError(''); setPreviewUnavailable(false);
    }, [policyKey, showPreview]);
    useEffect(() => {
        setDeleteSnapshot(undefined);
        setConflictedRows(current => current.size ? new Set() : current);
        setRowDetailsError('');
        if (editSnapshot.current && editSnapshot.current.data !== data) {
            editSnapshot.current = undefined;
            setEditingRowKey(undefined); setEditingDraft(null);
        }
    }, [data]);
    const showStream = resourceView === 'stream';

    const filterFields = data?.columns.length ? data.columns : repairFields;
    const tableDescription = `${selectedSchema}.${table.name || 'No table selected'}`;
    const dataRows = data?.rows ?? emptyPreviewRows;
    const primaryKey = (row: Record<string, unknown>, index = dataRows.indexOf(row)) => {
        const fields = data?.primaryKey ?? [];
        const exact = data?.rowIdentities?.[index];
        const values = fields.map(field => exact ? exact[field] : row[field]);
        if (values.some(value => typeof value !== 'string' && typeof value !== 'boolean' &&
            !(typeof value === 'number' && Number.isSafeInteger(value)))) return {};
        return Object.fromEntries(fields.map((field, position) => [field, values[position]]));
    };
    const rowKey = (row: Record<string, unknown>, index = dataRows.indexOf(row)) => {
        const identity = primaryKey(row, index);
        return Object.keys(identity).length ? JSON.stringify(identity) : 'row:' + index;
    };
    const formatCell = (value: unknown) => value == null ? 'null' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    const editCell = (key: string, value: unknown) => editorValue(data?.columnTypes?.[key], value);
    const exactEditorRow = (row: Record<string, unknown>) => {
        const precise = Object.entries(primaryKey(row)).filter(([key]) => typeof row[key] === 'number' && !Number.isSafeInteger(row[key]));
        return precise.length ? { ...row, ...Object.fromEntries(precise) } : row;
    };
    const canEditRows = canManage && data?.rowActions?.edit === true && !!data.primaryKey?.length;
    const canDeleteRows = canManage && data?.rowActions?.delete === true && !!data.primaryKey?.length;
    const rowVersion = (row: Record<string, unknown>) => {
        const version = data?.rowVersions?.[dataRows.indexOf(row)];
        return typeof version === 'string' && version && !conflictedRows.has(row) && Object.keys(primaryKey(row)).length ? version : undefined;
    };
    const startRowEdit = (key: string, preview?: ExplorerPreviewRow) => {
        if (!showPreview || !canEditRows || !data || !selectedTableAvailable || loading || mutationPending.current) return;
        const row = preview?.record ?? dataRows.find((item) => rowKey(item) === key);
        if (!row || !dataRows.includes(row)) return;
        const version = preview?.version;
        if (!version || version !== rowVersion(row)) return;
        if (editingRowKey && editingRowKey !== key) { message.warning('Save or cancel the current row edit first'); return; }
        setRowDetailsError('');
        editSnapshot.current = { data, row, version };
        rememberedEditValues.current = {};
        setEditingRowKey(key); setEditingDraft(Object.fromEntries(Object.entries(exactEditorRow(row)).map(([column, value]) => [column, editCell(column, value)])));
    };
    const cancelRowEdit = () => {
        if (!mutationPending.current) { editSnapshot.current = undefined; setEditingRowKey(undefined); setEditingDraft(null); setRowDetailsError(''); }
    };
    const rowMutationError = (error: unknown, row: Record<string, unknown>) => {
        const response = (error as { response?: { status?: number; data?: { detail?: { code?: string }; errors?: { code?: string } } } })?.response;
        if (response?.status !== 409 || (response.data?.errors?.code ?? response.data?.detail?.code) !== 'row_version_conflict') return errorText(error);
        setConflictedRows(current => new Set([...current, row]));
        return 'This row changed. Cancel, refresh the catalog, and select the row again.';
    };
    const saveRowEdit = async () => {
        const snapshot = editSnapshot.current;
        if (!showPreview || loading || !canEditRows || !selectedTableAvailable || !editingDraft || !snapshot || snapshot.data !== data ||
            rowVersion(snapshot.row) !== snapshot.version || !beginMutation()) return;
        setRowDetailsError('');
        const target = selection.current;
        const original = snapshot.row;
        const identity = getSessionState().identity;
        let writing = false;
        try {
            const values = rowChanges(editingDraft, exactEditorRow(original), data?.columnTypes ?? {});
            if (Object.keys(values).length) { writing = true; await updateEtlRow(selectedSchema, table.name, primaryKey(original), values, snapshot.version); }
            if (selection.current === target) {
                editSnapshot.current = undefined;
                setEditingRowKey(undefined); setEditingDraft(null);
                if (Object.keys(values).length) setRefreshVersion((value) => value + 1);
            }
            if (Object.keys(values).length) message.success('Row saved');
        } catch (error) {
            const session = getSessionState();
            if (!workspaceActive.current || selection.current !== target || !session.authenticated || session.locked || session.identity !== identity || (error as { code?: string })?.code === 'ERR_CANCELED') return;
            const detail = rowMutationError(error, original);
            if (!writing) setRowDetailsError(detail);
            else message.error({ content: 'Unable to save row', description: detail, className: 'explorer-toast' });
        } finally { endMutation(); }
    };
    const removeRow = async (row: Record<string, unknown>) => {
        if (!showPreview || !canDeleteRows || !data || !selectedTableAvailable || loading || mutationPending.current) return;
        const version = deleteSnapshot?.data === data && deleteSnapshot.row === row ? deleteSnapshot.version : undefined;
        if (!version || version !== rowVersion(row)) return;
        const target = selection.current;
        const reviewedScope = policyKey;
        const key = primaryKey(row);
        if (selection.current !== target || classicPreviewScope.current !== reviewedScope || !beginMutation()) return;
        setRowDetailsError('');
        const identity = getSessionState().identity;
        try {
            await deleteEtlRow(selectedSchema, table.name, key, version);
            const session = getSessionState();
            if (!workspaceActive.current || selection.current !== target || !session.authenticated || session.locked || session.identity !== identity) return;
            setDeleteSnapshot(undefined); setRefreshVersion((value) => value + 1); message.success('Object deleted.');
        } catch (error) {
            const session = getSessionState();
            if (!workspaceActive.current || selection.current !== target || !session.authenticated || session.locked || session.identity !== identity || (error as { code?: string })?.code === 'ERR_CANCELED') return;
            const detail = rowMutationError(error, row);
            message.error({ content: 'Unable to delete row', description: detail, className: 'explorer-toast' });

        } finally { endMutation(); }
    };
    const rowActions = (row: ExplorerPreviewRow) => {
        if (!row.version) return [];
        return recordMutationActions({
            edit: canEditRows ? () => startRowEdit(row.key, row) : undefined,
            remove: canDeleteRows ? () => {
                if (!data || !row.version || !dataRows.includes(row.record) || mutationPending.current) return;
                setRowDetailsError('');
                setDeleteSnapshot({ data, row: row.record, version: row.version });
            } : undefined,
            disabled: mutationLoading,
        });
    };
    const rowMutation = (row: ExplorerPreviewRow) => {
        const editing = editingDraft && editSnapshot.current?.data === data && editSnapshot.current?.row === row.record;
        if (!editing) return null;
        return <ExplorerRecordMutation mode="edit" title={row.title} error={rowDetailsError}
            busy={mutationLoading} disabled={loading || !canEditRows || rowVersion(row.record) !== editSnapshot.current?.version}
            fields={data?.columns.filter(key => !Object.prototype.hasOwnProperty.call(row.identity, key)).map(key => ({
                key, label: key, type: data.columnTypes?.[key] || 'string', value: editingDraft?.[key] ?? null,
                onChange: value => setEditingDraft(current => current ? { ...current, [key]: value } : current),
                nullControl: data.columnNullable?.[key] === true ? rowNullControl(key, editingDraft?.[key] ?? null, rememberedEditValues.current,
                    value => setEditingDraft(current => current ? { ...current, [key]: value } : current)) : undefined,
            }))}
            onSubmit={saveRowEdit} onCancel={cancelRowEdit} />;
    };
    const canChangePreview = () => {
        if (editingRowKey || deleteSnapshot || mutationPending.current) {
            message.warning('Save or cancel the current row edit first'); return false;
        }
        return selectedTableAvailable && showPreview && !loading && !savedBlocked && queryScopeReady;
    };
    const filterDraft = (rows: ExplorerFilter[]) => rows.filter((rule) => hasExplorerFilterInput(rule) || /^group-(open|close)-/.test(rule.id));
    useEffect(() => setFilterValidationRequest(0), [filterRules]);
    const canAppendFilter = () => {
        if ((!filterOpen && !filterRules.some(rule => rule.field || rule.value)) || !firstIncompleteExplorerFilter(filterRules)) return true;
        setWorkbenchMode('results'); setFilterOpen(true); setFilterValidationRequest(value => value + 1);
        return false;
    };
    const hasFilterDraft = () => JSON.stringify(filterDraft(filterRules)) !== JSON.stringify(filterDraft(appliedQuery.filters));
    const requireAppliedFilterDraft = () => {
        if (!canChangePreview()) return false;
        if (!hasFilterDraft()) return true;
        setFilterOpen(true);
        message.warning('Apply your pending column filter edits before using this shortcut');
        return false;
    };
    const setExplorerFilters = (rows: ExplorerFilter[]) => {
        if (rows.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
        const normalized = normalizeFilterDepth(rows);
        setAppliedQuery((current) => ({ ...current, filters: normalized }));
        setFilterRules(normalized.length ? normalized : [blankExplorerFilter('rule-1')]);
        setPageNumber(1);
    };
    const addColumnFilter = (field: string) => {
        if (!canChangePreview() || !canAppendFilter()) return;
        const id = randomId();
        const next = appendAndFilters(filterDraft(filterRules), [{ ...blankExplorerFilter(id), field }], randomId());
        if (next.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
        setFilterRules(normalizeFilterDepth(next));
        setFilterOpen(true);
        window.requestAnimationFrame(() => document.getElementById(`explorer-filter-${id}`)?.scrollIntoView({ block: 'nearest' }));
    };
    const openColumnFilter = (field: string) => {
        if (!canChangePreview()) return;
        const applied = activeExplorerFilters(appliedQuery.filters).find(rule => rule.field === field);
        const row = filterRules.find(rule => rule.id === applied?.id) || filterRules.find(rule => rule.field === field);
        setWorkbenchMode('results'); setFilterOpen(true);
        if (row) window.requestAnimationFrame(() => {
            const element = document.getElementById(`explorer-filter-${row.id}`);
            element?.scrollIntoView({ block: 'nearest' });
            element?.querySelector<HTMLElement>('[role="button"]')?.focus();
        });
    };
    const quickCellFilter = (field: string, value: string | null, exclude: boolean) => {
        if (!canChangePreview() || !canAppendFilter() || !requireAppliedFilterDraft()) return;
        const id = randomId();
        setExplorerFilters(appendAndFilters(appliedQuery.filters, cellFilterRows(field, value, exclude, id), `${id}-previous`));
    };
    const updateExplorerSorts = (sorts: SortCondition[]) => {
        setAppliedQuery((current) => ({ ...current, sorts }));
        setSortField(sorts[0]?.field || '');
        setSortDirection(sorts[0]?.direction || 'asc');
        setSortConditions(sorts.slice(1));
        setPageNumber(1);
    };
    const requireAppliedSortDraft = () => {
        if (!canChangePreview()) return false;
        const draft = [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)];
        if (JSON.stringify(draft) === JSON.stringify(appliedQuery.sorts)) return true;
        setFilterOpen(true);
        message.warning('Apply your pending sort edits before using this shortcut');
        return false;
    };
    const setHeaderSort = (field: string, direction: 'asc' | 'desc') => {
        if (!requireAppliedSortDraft()) return;
        if (appliedQuery.sorts.length >= 20 && !appliedQuery.sorts.some(sort => sort.field === field)) {
            message.warning('Remove a sort before adding another. The table supports 20 sorts.'); return;
        }
        updateExplorerSorts([{ field, direction }, ...appliedQuery.sorts.filter((sort) => sort.field !== field)]);
    };

    const resultLayout = useResultLayout(selectedTableAvailable && showPreview ? previewIdentity : '',
        selectedSchema, table.name, data?.columns || [], appliedQuery.sorts, updateExplorerSorts, !requestedSavedId);
    const dataColumns: any[] = explorerResultColumns({ layout: resultLayout, sorts: appliedQuery.sorts, filters: appliedQuery.filters, openFilter: openColumnFilter, types: data?.columnTypes,
        addFilter: addColumnFilter, setSort: setHeaderSort, clearSorts: () => { if (requireAppliedSortDraft()) updateExplorerSorts([]); }, filterValue: quickCellFilter,
    });

    useEffect(() => {
        workspaceActive.current = true;
        return () => { workspaceActive.current = false; catalogRequest.current++; catalogController.current?.abort(); selection.current = ''; };
    }, []);
    useEffect(() => {
        if (!loadExplorerCatalog || catalogLoaded) return;
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
    }, [loadExplorerCatalog, catalogLoaded]);
    const refreshWorkspace = () => {
        if (mutationPending.current || editingRowKey || scriptDirty || apisDirty) { message.warning('Save or cancel current changes before refreshing'); return; }
        setRefreshVersion((value) => value + 1);
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
    };
    useExplorerErrorToast(requestError !== 'Loading cancelled' ? requestError : '', 'Unable to load Explorer', refreshWorkspace);
    useExplorerErrorToast(savedError, 'Unable to open saved item', () => setSavedRetry(value => value + 1));
    useExplorerErrorToast(previewError && (previewUnavailable ? `${previewError} For an expensive query, narrow the filters before trying again.` : previewError), 'Data preview unavailable', () => setPreviewRevision(value => value + 1));
    useExplorerErrorToast(streamError, 'Stream status unavailable');
    useExplorerErrorToast(policy?.key === policyKey ? policy.error : '', 'Unable to check preview policy', () => setPolicyRetry(value => value + 1));
    useEffect(() => {
        if (!catalogLoaded) return;
        const params = new URLSearchParams(search);
        const requested = params.get('schema');
        setSelectedSchema(schemas.find(item => item.key === requested)?.key || '');
    }, [search, schemas, catalogLoaded]);
    useEffect(() => {
        const version = ++tableRequest.current;
        const controller = new AbortController(); tablesController.current = controller;
        const identity = getSessionState().identity;
        const current = () => {
            const session = getSessionState();
            return version === tableRequest.current && !controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
        };
        if (!selectedSchemaAvailable) {
            loadedSchema.current = '';
            setTablesLoading(false); setTablesLoadedSchema(undefined); setServerTables([]);
            setTable({ name: '', rowCount: 0, size: '', updatedAt: '' }); setData(undefined);
            return () => { tableRequest.current++; controller.abort(); };
        }
        setTablesLoading(true); setTablesLoadedSchema(undefined); setRequestError('');
        if (loadedSchema.current !== selectedSchema) {
            loadedSchema.current = selectedSchema;
            setServerTables([]); setTable({ name: '', rowCount: 0, size: '', updatedAt: '' });
            setPartition(undefined); setPageNumber(1); setEditingRowKey(undefined); setEditingDraft(null); setData(undefined); setStream(undefined); setOffsets([]); setInputTopics([]); setScript(''); setOpenApis([]);
        }
        fetchLayerTables(selectedSchema, 'current', controller.signal).then((items) => { if (current()) { setServerTables(items); setTablesLoadedSchema(selectedSchema); } }).catch((error) => { if (current() && error?.code !== 'ERR_CANCELED') setRequestError(errorText(error)); }).finally(() => { if (version === tableRequest.current) setTablesLoading(false); });
        return () => { tableRequest.current++; controller.abort(); };
    }, [selectedSchema, refreshVersion, selectedSchemaAvailable]);
    useEffect(() => {
        const params = new URLSearchParams(search);
        if (tablesLoadedSchema !== selectedSchema || (params.get('schema') && params.get('schema') !== selectedSchema)) return;
        const name = params.get('table');
        const selected = serverTables.find((item) => item.name === name);
        if (selected) setTable(selected);
        else {
            setTable({ name: '', rowCount: 0, size: '', updatedAt: '' });
            if (name && !tablesLoading) setRequestError(`Table ${name} was not found in ${selectedSchema}`);
        }
    }, [search, serverTables, tablesLoading, tablesLoadedSchema, selectedSchema]);
    selection.current = `${selectedSchema}/${table.name}`;
    useEffect(() => {
        setKeywordsOpen(false);
        setEditingRowKey(undefined); setEditingDraft(null); setPartition(undefined); setPageNumber(1); setData(undefined); setScript(''); setSavedScript(''); setScriptLoaded(false); setScriptNote('Loading script…'); setOpenApis([]); setInputTopics([]); setActiveTopicIndex(0); setStream(undefined); setOffsets([]); setStreamError(''); setOffsetPartition(undefined); setOffset(undefined); setOffsetOpen(false);
        setWorkbenchRows([]); setWorkbenchMode('results');
        setFilterText(''); setFilterRules([{ id: 'rule-1', field: '', operator: '=', value: '', active: true, join: 'and' }]); setSortField(''); setSortConditions([]); setAppliedQuery({ filters: [], sorts: [], search: '' });
        setPreviewError(''); setPreviewUnavailable(false); setPreviewPage(undefined); setRepairFields([]);
    }, [workspaceAsset]);
    useEffect(() => {

        if (!requestedSavedId) {
            savedController.current?.abort();
            setSavedState(undefined);
            if (queryScope !== requestedScope) applySavedQuery();
            return;
        }
        if (!selectedTableAvailable || !previewIdentity) return;
        if (activeSavedState?.detail && queryScope === requestedScope) return;
        const controller = new AbortController();
        savedController.current?.abort(); savedController.current = controller;
        setSavedState({ key: savedKey, error: '', loading: true });
        setPreviewError(''); setPreviewUnavailable(false);
        setData(undefined); setPreviewPage(undefined); setWorkbenchRows([]); setSelectionEpoch(value => value + 1); setPageNumber(1);
        const current = () => {
            const session = getSessionState();
            return !controller.signal.aborted && savedScope.current === savedKey && session.authenticated && !session.locked && session.identity === previewIdentity;
        };
        void openSaved(savedPath(requestedSchema!, requestedTable!), requestedSavedId, controller.signal).then(detail => {
            if (!current()) return;
            applySavedQuery(detail);
            setSavedState({ key: savedKey, detail, error: '', loading: false });
            setCatalogOpen(false);
        }).catch(error => {
            if (current()) setSavedState({ key: savedKey, error: errorText(error), loading: false });
        });
        return () => controller.abort();
    }, [requestedScope, selectedTableAvailable, savedRetry]);
    useEffect(() => {
        if (!selectedTableAvailable || !scriptOpen || scriptLoaded || scriptDirty) return;
        const version = ++metadataRequest.current;
        const valid = () => version === metadataRequest.current;
        Promise.all([fetchLayerScript(selectedSchema, table.name), fetchHandlerCode(selectedSchema, table.name)]).then(([result, draft]) => { if (valid()) { const code = draft.code || result.code || ''; setScript(code); setSavedScript(code); setScriptLoaded(true); setScriptNote(draft.code && draft.code !== result.code ? 'Saved draft' : result.available ? result.path || 'Deployed handler' : result.reason || 'No deployed handler'); } }).catch((error) => { if (valid()) setScriptNote(errorText(error)); });
        return () => { metadataRequest.current++; };
    }, [selectedSchema, table.name, selectedTableAvailable, scriptOpen, scriptLoaded, scriptDirty]);
    useEffect(() => {
        if (!keywordsOpen) return;
        let active = true;
        setKeywordsLoading(true); setKeywordsError(''); setKeywordConfig(undefined);
        fetchVulnerabilityKeywords().then((config) => {
            if (active) setKeywordConfig(config);
        }).catch((error) => { if (active) setKeywordsError(errorText(error)); }).finally(() => { if (active) setKeywordsLoading(false); });
        return () => { active = false; };
    }, [keywordsOpen]);
    useEffect(() => {
        const version = ++apiRequest.current;
        setApisError('');
        if (!canManage || !selectedTableAvailable) { setOpenApis([]); setApisDirty(false); setApisLoading(false); return; }
        setApisLoading(true);
        const identity = getSessionState().identity;
        const current = () => {
            const session = getSessionState();
            return version === apiRequest.current && session.authenticated && !session.locked && session.identity === identity;
        };
        fetchEtlApis(selectedSchema, table.name).then((items) => { if (current()) { setOpenApis(items); setApisDirty(false); } }).catch((error) => { if (current() && error?.code !== 'ERR_CANCELED') setApisError(errorText(error)); }).finally(() => { if (version === apiRequest.current) setApisLoading(false); });
        return () => { apiRequest.current++; };
    }, [selectedSchema, table.name, canManage, selectedTableAvailable, apiRevision]);
    useEffect(() => {
        const version = ++dataRequest.current;
        if (!selectedTableAvailable || !showPreview || savedBlocked || !queryScopeReady) { setLoading(false); return; }
        setLoading(true); setPreviewError(''); setPreviewUnavailable(false);
        const controller = new AbortController();
        queryController.current = controller;
        const offset = (pageNumber - 1) * pageSize;
        const query = { ...appliedQuery, limit: pageSize, offset, partition, forEditing: canManage, withIdentity: true, ...(activeSavedListId ? { savedListId: activeSavedListId } : {}) };
        const current = () => {
            const session = getSessionState();
            return version === dataRequest.current && !controller.signal.aborted && classicPreviewScope.current === policyKey &&
                currentQueryScope.current === requestedScope && session.authenticated && !session.locked && session.identity === previewIdentity;
        };
        const timer = window.setTimeout(async () => {
            try {
                if (!current()) return;
                const result = await queryExplorerPreview(selectedSchema, table.name, query, controller.signal);
                if (!current()) return;
                setData(result); setPreviewPage({ offset, limit: pageSize, result });
            } catch (error) {
                if (!current()) return;
                const response = (error as { response?: { status?: number; data?: { detail?: { code?: string }; errors?: { code?: string } } } })?.response;
                if (response?.status === 409 && (response.data?.errors?.code ?? response.data?.detail?.code) === 'indexed_preview_required') {
                    setPolicy({ key: policyKey, mode: 'unavailable' }); setData(undefined); setPreviewPage(undefined);
                    return;
                }
                setData((current) => current ? { ...current, rows: [], rowCount: 0 } : undefined);
                setPreviewUnavailable((error as { response?: { status?: number } }).response?.status === 503);
                setPreviewPage(undefined); setPreviewError(errorText(error));
            } finally {
                if (current()) { setQueryElapsed(performance.now() - queryStarted.current); setLoading(false); }
            }
        }, 100);
        return () => { window.clearTimeout(timer); controller.abort(); dataRequest.current++; };
    }, [selectedSchema, table.name, partition, appliedQuery, pageNumber, pageSize, refreshVersion, previewRevision, selectedTableAvailable, showPreview, policyKey, canManage, savedBlocked, queryScopeReady, activeSavedListId]);
    useEffect(() => {
        if (!showPreview || !selectedTableAvailable || !previewError || filterFields.length) return;
        const controller = new AbortController();
        fetchEtlTableColumns(selectedSchema, table.name, controller.signal).then((columns) => {
            if (!controller.signal.aborted) setRepairFields(columns.map((column) => column.name));
        }).catch(() => { /* The original preview failure has already been reported. */ });
        return () => controller.abort();
    }, [showPreview, selectedTableAvailable, selectedSchema, table.name, previewError, filterFields.length]);
    useEffect(() => {
        const scroll = previewViewport.current?.querySelector('.ui-table-content');
        if (previewPage && scroll) scroll.scrollTop = 0;
    }, [previewPage]);
    useEffect(() => {
        if (!selectedTableAvailable || !showStream) { setStreamLoading(false); return; }
        setStreamLoading(true);
        let active = true;
        let timer: number | undefined;
        let previous: EtlStreamState | undefined;
        const identity = getSessionState().identity;
        const current = () => {
            const session = getSessionState();
            return active && session.authenticated && !session.locked && session.identity === identity;
        };
        const refresh = async () => {
            const version = ++streamRequest.current;
            try {
                const result = await fetchTableStream(selectedSchema, table.name, inputTopics[activeTopicIndex]);
                if (!current() || version !== streamRequest.current) return;
                setStream({ ...result, throughput: topicRate(previous, result) }); previous = result; setOffsets(result.offsets || []); setStreamError('');
                setOffsetPartition((current) => result.offsets?.some((item) => item.partition === current) ? current : result.offsets?.[0]?.partition);
                if (result.topics?.length) setInputTopics(result.topics);
            } catch (error) { if (current() && version === streamRequest.current && (error as { code?: string })?.code !== 'ERR_CANCELED') { setStream(undefined); setOffsets([]); setStreamError(errorText(error)); } }
            finally { if (current()) { setStreamLoading(false); timer = window.setTimeout(refresh, 5000); } }
        };
        void refresh();
        return () => { active = false; streamRequest.current++; window.clearTimeout(timer); };
    }, [selectedSchema, table.name, inputTopics[activeTopicIndex], selectedTableAvailable, showStream]);
    const selectResourceView = (view: typeof resourceView) => {
        if (editingRowKey || scriptDirty || apisDirty || mappingDirty || mutationPending.current) { message.warning('Save or cancel current changes before switching views'); return; }
        if (view === resourceView) return;
        const params = new URLSearchParams(search);
        if (view === 'preview') {
            if (location.state?.previewKey) { navigate(-1); return; }
            params.delete('view');
            navigate(`${workspacePath}?${params.toString()}`, { replace: true });
        } else {
            params.set('view', view);
            navigate(`${workspacePath}?${params.toString()}`, { state: { previewKey: location.key } });
        }
    };
    const openSavedItem = (id: string) => {
        if (currentQueryScope.current !== requestedScope) return;
        if (editingRowKey || deleteSnapshot || scriptDirty || apisDirty || mappingDirty || mutationPending.current) {
            message.warning('Save or cancel current changes before opening a saved item'); return;
        }
        queryController.current?.abort();
        setWorkbenchRows([]); setSelectionEpoch(value => value + 1); setPageNumber(1); setData(undefined); setPreviewPage(undefined);
        if (id === requestedSavedId) {
            savedController.current?.abort(); setSavedState(undefined); setSavedRetry(value => value + 1);
        }
        const params = new URLSearchParams(search);
        params.set('saved', id); params.delete('view');
        navigate(`${workspacePath}?${params.toString()}`);
        setCatalogOpen(false);
    };
    const closeSavedItem = () => {
        if (currentQueryScope.current !== requestedScope) return;
        if (editingRowKey || deleteSnapshot || scriptDirty || apisDirty || mappingDirty || mutationPending.current) {
            message.warning('Save or cancel current changes before returning to the table'); return;
        }
        savedController.current?.abort(); queryController.current?.abort(); setSavedState(undefined);
        const params = new URLSearchParams(search); params.delete('saved'); params.delete('view');
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const updateSavedItem = (detail: SavedDetail) => {
        if (detail.id === requestedSavedId) setSavedState(current => current?.key === savedKey ? { ...current, detail } : current);
    };
    const selectSchema = (schema: string) => {
        if (editingRowKey || scriptDirty || apisDirty || mappingDirty || mutationPending.current) {
            message.warning('Save or cancel current changes before switching schemas'); return;
        }
        const params = new URLSearchParams(search);
        params.set('schema', schema);
        params.delete('saved');
        params.delete('table');
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const selectTable = (selected: LayerTable) => {
        if (scriptDirty || apisDirty || mappingDirty || mutationPending.current) {
            message.warning('Save or cancel current changes before switching tables'); return;
        }
        if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; }
        setCatalogOpen(false);
        if (selected.name === table.name && requestedSchema === selectedSchema && requestedTable === selected.name) return;
        setTable(selected);

        const params = new URLSearchParams(search); params.delete('create'); params.delete('saved'); params.set('schema', selectedSchema); params.set('table', selected.name);
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const runDryRun = async () => {
        if (!selectedTableAvailable || !scriptLoaded || !script.trim() || validationPending.current) return;
        const target = selection.current; const code = script;
        validationPending.current = true; setScriptValidating(true);
        try {
            const result = await validateHandlerCode(selectedSchema, table.name, code);
            if (selection.current === target && scriptContent.current === code) { if (result.valid) message.success('Python syntax is valid'); else message.error(result.errors.join('; ')); }
        } catch (error) { if (selection.current === target) message.error(errorText(error)); }
        finally { validationPending.current = false; if (workspaceActive.current) setScriptValidating(false); }
    };
    const saveScript = async () => {
        if (!selectedTableAvailable || !scriptLoaded || !script.trim() || !beginMutation()) return;
        const target = selection.current; const code = script;
        try { await saveHandlerCode(selectedSchema, table.name, code); if (selection.current === target) { setSavedScript(code); setScriptNote('Saved draft'); } message.success('Draft saved; deployed worker code is unchanged'); }
        catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const applyFilters = () => { if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; } setAppliedQuery({ filters: filterRules.filter((rule) => rule.field || rule.id.startsWith('group-')), sorts: [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)], search: filterText }); setPageNumber(1); };
    const saveOffset = async () => {
        if (!selectedTableAvailable || !stream?.available || offsetPartition === undefined || offset === undefined || offset < 0 || !Number.isSafeInteger(offset) || !beginMutation()) return;
        const target = selection.current;
        try { await setTableOffset(selectedSchema, table.name, offsetPartition, offset, stream.topic); if (selection.current === target) { setOffsetOpen(false); setRefreshVersion((value) => value + 1); } message.success('Offset saved; worker restart required'); }
        catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };

    const publicationKey = useStoragePublicationKey(canManage && selectedTableAvailable && resourceView === 'api'
        ? JSON.stringify([selectedSchema, table.name]) : '', (id, keyStatus) => {
        setOpenApis(items => items.map(api => api.id === id ? {...api, keyStatus} : api));
    });
    useEffect(() => { setApiNameForm(undefined); }, [workspaceAsset, resourceView]);
    const apiPanel = <>
        <PublishedApiWorkbench items={openApis} loading={apisLoading} busy={mutationLoading || !selectedTableAvailable || publicationKey.busy}
            dirty={apisDirty} error={apisError} onReload={() => { if (!apisDirty && !mutationLoading) setApiRevision(value => value + 1); }}
            onManageKey={publicationKey.open}
            onAdd={() => setApiNameForm({scope: workspaceAsset, initialName: `${table.name} API`})}
            onEdit={api => setApiNameForm({scope: workspaceAsset, id: api.id, initialName: api.name || ''})}
            onUpdate={updateApi} onSave={() => void saveApis()} onDelete={removeApi}
            onDiscard={() => { if (!mutationLoading && !apisLoading) setApiRevision(value => value + 1); }}
            storageAsset={{schema: selectedSchema, table: table.name}} />
        {publicationKey.dialog}
        {apiNameForm?.scope === workspaceAsset && <StoragePublicationNameDialog mode={apiNameForm.id ? 'rename' : 'create'}
            initialName={apiNameForm.initialName} busy={mutationLoading} onSave={saveApiName} onClose={() => setApiNameForm(undefined)} />}
    </>;

    useEffect(() => {
        if (!catalogOpen) return;
        const outside = (event: PointerEvent) => {
            if (event.target instanceof Element && !event.target.closest('.flow-explorer-catalog,.wb-catalog-toggle'))
                setCatalogOpen(false);
        };
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setCatalogOpen(false); };
        document.addEventListener('pointerdown', outside, true);
        document.addEventListener('keydown', escape);
        return () => {
            document.removeEventListener('pointerdown', outside, true);
            document.removeEventListener('keydown', escape);
        };
    }, [catalogOpen]);
    const mapping = explorerPresentation?.presentation;
    const workbenchLocked = !!editingRowKey || !!deleteSnapshot || mutationLoading || scriptDirty || apisDirty || mappingDirty;
    const emptyExplorer = (!requestedSchema || !requestedTable) && !catalogOpen && !createTableSchema;
    if (emptyExplorer) return <section className="flow-exploration-empty" aria-label="Empty exploration">
        <DatabaseOutlined />
        <h2>Choose an object type to explore</h2>
        <p>Open a Storage-backed object type to see its results in Explorer.</p>
        <Link to="/ontology">Browse Storage-backed object types</Link>
    </section>;
    return <div className={`flow-layers flow-layers--tagged flow-schema-workbench explorer-object-workspace has-asset${catalogOpen ? ' is-catalog-open' : ''}${workbenchMode === 'explore' && previewVisible && !savedBlocked && queryScopeReady ? ' is-exploring' : ''}`}>
        <div  className="flow-layer-layout">
            <ExplorerCatalog schemas={schemas} tables={tablesLoadedSchema === selectedSchema ? serverTables : []}
                selectedSchema={selectedSchema} selectedTable={table.name} catalogLoaded={catalogLoaded} tablesLoading={tablesLoading}
                requestError={requestError} overlayOpen={catalogOpen} onCreateTable={canCreateTable ? openTableWizard : undefined} onRefresh={refreshWorkspace} onSchema={selectSchema} onTable={selectTable} />
            <section  className="flow-layer-main">

                <ExplorerWorkbench locked={workbenchLocked} header={requestedSchema && requestedTable ? {source: { kind: 'backed', schema: requestedSchema, table: requestedTable },
available: selectedTableAvailable,
backToTypes: backToTypes,
label: selectedTableAvailable ? mapping?.displayName || requestedTable : requestedTable,
glyph: <ExplorerTypeIcon compact dataType={selectedTableAvailable ? mapping?.dataType ?? table.dataType : undefined} table={requestedTable} />,
fields: data?.columns || filterFields,
types: data?.columnTypes,
appliedSearch: appliedQuery.search,
onClearSearch: () => {
    if (!canChangePreview()) return;
    if (filterText === appliedQuery.search) setFilterText('');
    setAppliedQuery(current => ({ ...current, search: '' })); setPageNumber(1);
},
onSearchValues: value => {
    if (!requireAppliedFilterDraft() || !requireAppliedSortDraft()) return false;
    const search = value.trim();
    setFilterText(search); setAppliedQuery(current => ({ ...current, search })); setPageNumber(1);
    return true;
},
onExploreFilterValue: (field, value) => {
    const scalar = exactCellValue(value, data?.columnTypes?.[field]);
    if (scalar !== undefined) quickCellFilter(field, scalar, false);
},
names: Object.fromEntries((mapping?.properties || []).flatMap(p => p.sourceColumn ? [[p.sourceColumn, p.name]] : [])),
rows: dataRows.map((row,i) => ({...row,...primaryKey(row,i)})),
selected: workbenchRows,
query: { ...appliedQuery, partition },
resource: resourceView,
availableResources: explorerResourceCapabilities({ mode: 'existing', schema: requestedSchema, table: requestedTable }, canManage),
admin: canManage,
additionalTools: [{ key: 'relationship-types', label: 'Relationship types', group: 'inspect', icon: <BranchesOutlined />, run: () => setRelationshipTypesOpen(true) }],
layoutControl: showPreview && <LayoutToolbar controls={resultLayout} admin={canManage} types={data?.columnTypes} />,
selectResource: selectResourceView,
saved: activeSaved,
savedLoading: savedLoading,
savedError: savedError,
openSavedItem: openSavedItem,
closeSavedItem: closeSavedItem,
saveCompleted: (detail: SavedDetail) => openSavedItem(detail.id),
updateSavedItem: updateSavedItem,
addFilter: addColumnFilter,
toggleCatalog: () => setCatalogOpen(value => !value),
catalogOpen: catalogOpen,
closeCatalog: () => setCatalogOpen(false),
mode: workbenchMode,
setMode: setWorkbenchMode,
clearSelection: () => { setWorkbenchRows([]); setSelectionEpoch(n => n + 1); },
filterButton: showPreview && <ExplorerFilterButton open={filterOpen} count={filterCount + sortCount} onClick={() => { if (workbenchMode === 'explore') { setWorkbenchMode('results'); setFilterOpen(true); } else activateTool('filter'); }} />} : undefined}>

                    {!selectedTableAvailable ? <div className="flow-inspector-body" aria-busy={explorerLoading}>
                        {!requestError && !explorerLoading && <div className="flow-schema-state" role="status">{requestedSchema && requestedTable ? 'Object type is unavailable.' : 'Choose an object type from the catalog.'}</div>}
                    </div> : <>
                    <div  className="flow-table-header">
                        <div className="flow-table-header-left">
                            <span className="flow-schema-asset-icon" aria-hidden="true"><TableOutlined /></span>
                            <div><h2>{table.name}</h2><p className="flow-schema-asset-source">{schemaNode?.database ? <span>{schemaNode.database}</span> : null}<code>{tableDescription}</code></p></div>
                        </div>

                        <div className="flow-table-header-right">
                            <div className="flow-health-badge"><i className={`flow-dot ${schemaNode?.status === 'running' ? 'flow-dot--green' : ''}`} /><span>{schemaNode?.status === 'running' ? 'Schema accessible' : 'Unavailable'}</span></div>
                            {selectedSchema === 'ts_task' && table.name === 'task_vulnerability' && <Button size="small" onClick={() => setKeywordsOpen(true)}>Satellite extraction</Button>}

                        </div>
                    </div>

                    <nav className="flow-schema-view-tabs" aria-label="Table view">
                        <button type="button" aria-pressed={resourceView === 'preview'} onClick={() => selectResourceView('preview')}><DatabaseOutlined /> Data preview</button>
                        <button type="button" aria-pressed={resourceView === 'schema'} onClick={() => selectResourceView('schema')}><SettingOutlined /> Schema</button>
                        <button type="button" aria-pressed={resourceView === 'stream'} onClick={() => selectResourceView('stream')}><DownloadOutlined /> Stream</button>
                        <button type="button" aria-pressed={resourceView === 'evidence'} onClick={() => selectResourceView('evidence')}><AlertOutlined /> Evidence</button>
                        {canManage && <button type="button" aria-pressed={resourceView === 'api'} onClick={() => selectResourceView('api')}><CodeOutlined /> Published APIs</button>}
                    </nav>
                    <ExplorerResults viewportRef={previewViewport} selectionKey={JSON.stringify([selectionEpoch, policyKey])} results={showPreview && !savedBlocked && queryScopeReady && !previewError ? {onSelectedRowsChange: setWorkbenchRows,
schema: selectedSchema,
table: table.name,
dataType: mapping?.dataType ?? table.dataType,
rows: dataRows,
columns: data?.columns ?? emptyPreviewColumns,
columnTypes: data?.columnTypes,
metadata: explorerPresentation,
dataColumns: dataColumns,
rowKey: rowKey,
rowIdentity: primaryKey,
rowVersion: rowVersion,
presentations: Object.fromEntries(resultLayout.columns.map(column => [column.field, column.presentation ?? 'value'])),
loading: loading,
locked: !!editingRowKey || !!deleteSnapshot || mutationLoading,
renderTitleLink: row => <ExplorerRecordLink schema={selectedSchema} table={table.name} identity={row.identity}>{row.title}</ExplorerRecordLink>,
renderFullDetails: row => <ExplorerRecordLink schema={selectedSchema} table={table.name} identity={row.identity} />,
renderRelationships: (row, page) => <ExplorerRowLinks schema={selectedSchema} table={table.name} primaryKey={row.identity} sourceTitle={row.title} initialPage={page} revision={relationshipRevision} />,
rowActions: rowActions,
renderMutation: rowMutation,
onRefresh: () => setPreviewRevision(value => value + 1),
footer: previewPage && <ExplorerResultsFooter offset={previewPage.offset} rowCount={previewPage.result.rowCount} pageSize={pageSize} loading={loading} locked={!!editingRowKey || !!deleteSnapshot || mutationLoading}
 canPageFurther={previewPage.offset === (pageNumber - 1) * pageSize && previewPage.limit === pageSize && previewPage.result.canPageFurther}
 onPageSize={size => { if (canChangePreview()) { setPageSize(size); setPageNumber(1); } }}
 onPrevious={() => { if (canChangePreview()) setPageNumber(pageNumber - 1); }} onNext={() => { if (canChangePreview()) setPageNumber(pageNumber + 1); }}
 notices={(!previewPage.result.stableOrder || previewPage.result.hasMore && !previewPage.result.canPageFurther) && <>
 {!previewPage.result.stableOrder && <span>No stable primary key. Rows may repeat or be skipped when paging.</span>}
 {previewPage.result.hasMore && !previewPage.result.canPageFurther && <span>Interactive preview limit reached. Narrow filters to continue.</span>}</>} />} : undefined}>
                        {savedLoading && <div className="flow-schema-state" role="status">Loading saved item…</div>}
                        {showIndexPreview && !savedBlocked && (canManage ? <SandboxIndexPreview /> :
                            <div className="flow-schema-state" role="status">Index-aware sandbox preview is available to administrators only.</div>)}
                        {previewRequested && !showPreview && !savedBlocked && (policyMode === 'indexed_projection_required'
                            ? <section className="flow-schema-details" aria-label="Verified indexed projection preview">
                                <header><h3>Verified indexed projection preview</h3></header>
                                <p>This table requires a verified covering index path. Only an explicitly reviewed, read-only preview of approved fields is available.</p>
                                {canManage && adminPreviewIdentity.current === previewIdentity ? <ExplorerIndexedProjection key={policyKey}
                                    schema={selectedSchema} table={table.name} identity={previewIdentity} /> :
                                    <p role="status">Verified indexed projection is unavailable. No preview query was run.</p>}
                            </section>
                            : policyMode === 'indexed_id_required' ? <section className="flow-schema-details" aria-label="Verified-index-only preview">
                                <header><h3>Verified-index-only preview</h3></header>
                                <p>This table requires a verified index path. Review one key equality or inclusive range to request up to 20 IDs. No rows, edits, or fallback query are available here.</p>
                                {canManage && adminPreviewIdentity.current === previewIdentity ? <ExplorerIndexedIdSample key={policyKey} schema={selectedSchema} table={table.name}
                                    identity={previewIdentity} required /> : <p role="status">Verified-index ID sampling is unavailable. No preview query was run.</p>}
                            </section>
                            : policy?.key === policyKey && policy.error ? null : <div className="flow-schema-state" role="status">Preview policy unavailable.
                                {policyMode === 'unavailable' ? <> <Button size="small" onClick={() => setPolicyRetry(value => value + 1)}>Retry preview policy</Button></>
                                    : ' Checking the selected table.'}
                            </div>)}

                        {showPreview && !savedBlocked && queryScopeReady && filterOpen && <ExplorerFilterEditor
                            validationRequest={filterValidationRequest}
                            rules={filterRules} setRules={setFilterRules} search={filterText} onSearchChange={setFilterText}
                            renderRuleNotice={rule => activeExplorerFilters(filterRules).some(item => item.id === rule.id) ? <ExplorerFilterIndexNotice
                                schema={selectedSchema} table={table.name} rule={rule} columnTypes={data?.columnTypes} /> : null}
                            renderSortNotice={sorts => <ExplorerSortIndexNotice schema={selectedSchema} table={table.name} sorts={sorts} />}
                            sorts={[{ field: sortField, direction: sortDirection }, ...sortConditions]}
                            onSortsChange={(sorts, apply) => {
                                setSortField(sorts[0]?.field || ''); setSortDirection(sorts[0]?.direction || 'asc'); setSortConditions(sorts.slice(1));
                                if (apply) { setAppliedQuery(current => ({ ...current, sorts: sorts.filter(item => item.field) })); setPageNumber(1); }
                            }}
                            fields={filterFields} rows={dataRows} formatValue={formatCell} onApply={applyFilters} columnMode />}

                    </ExplorerResults>
                    <ExplorerToolDialog key={tableDescription} title={storageToolTitles[resourceView]}
                        context={<><strong>{mapping?.displayName || table.name}</strong><code>{tableDescription}</code></>}
                        bodyClassName={`explorer-resource-view explorer-resource-view--${resourceView}`}
                        onClose={() => selectResourceView('preview')}>
                        {resourceView === 'schema' && <ExplorerTableSchema key={`${selectedSchema}.${table.name}`} schema={selectedSchema} table={table.name} canManage={canManage} onDirtyChange={setMappingDirty} onSaved={() => setPreviewRevision(v=>v+1)} />}
                        {resourceView === 'evidence' && <ExplorerTableEvidence key={tableDescription} schema={selectedSchema} table={table.name} canManage={canManage} />}
                        {resourceView === 'stream' && <section className="flow-schema-details flow-explorer-stream" aria-label="Stream status">
                            <header>
                                <div><h3>Stream</h3><p>Kafka input topic and consumer group positions. Shared topics include traffic for other tables.</p></div>
                                <div className="flow-explorer-stream-actions">
                                    <Button size="small" icon={<CodeOutlined />} onClick={() => activateTool('script')}>Layer script</Button>
                                    {canManage && <Button size="small" disabled={!stream?.resumeAvailable || !offsets.length} onClick={() => { setOffsetPartition(offsets[0]?.partition); setOffset(offsets[0]?.offset); setTopicDraft(stream?.topic || ''); setOffsetOpen(true); }}>Adjust offset</Button>}
                                </div>
                            </header>
                            {!!inputTopics.length && <label>Input topic <NativeSelect aria-label="Input topic" value={inputTopics[activeTopicIndex] || ''} onChange={event => { setActiveTopicIndex(inputTopics.indexOf(event.target.value)); setStream(undefined); setOffsets([]); setStreamLoading(true); }}>
                                {inputTopics.map(topic => <option key={topic} value={topic}>{topic}</option>)}
                            </NativeSelect></label>}
                            {streamLoading && !stream && !streamError ? <div className="flow-schema-state" role="status">Checking stream status…</div>
                                : streamError ? null
                                : !stream?.available ? <div className="flow-schema-state" role="status"><strong>{stream?.topic ? 'Stream unavailable' : 'No active stream'}</strong><p>{stream?.reason || 'There is no stream information for this table.'}</p></div>
                                : <ExplorerStreamPositions stream={stream} />}
                        </section>}
                        {resourceView === 'api' && (canManage ? apiPanel : <div className="flow-schema-state" role="status">Published APIs are available to administrators only.</div>)}
                    </ExplorerToolDialog>
                    <Modal  className="flow-surface-modal flow-script-modal"  title="Layer  script"  open={scriptOpen} onCancel={closeScript} keyboard={!mutationLoading} maskClosable={!mutationLoading}  footer={null}  width={720}>
                        <div  className="flow-script-editor">
                            <div  className="flow-script-note">{scriptNote}. Drafts are stored separately and are not deployed to workers.</div>
                            <Input.TextArea aria-label="Layer script code" readOnly={!canManage || !scriptLoaded || mutationLoading} value={script}  onChange={(event)  =>  setScript(event.target.value)}  autoSize={{  minRows:  12,  maxRows:  22  }}  />
                            <div  className="flow-script-actions"><Button type="primary" onClick={runDryRun} loading={scriptValidating} disabled={!scriptLoaded || !script.trim() || mutationLoading}>Validate syntax</Button>{canManage && <Button onClick={saveScript} loading={mutationLoading} disabled={!scriptLoaded || !script.trim() || !scriptDirty}>Save draft</Button>}<span>AST validation only; code is not executed</span></div>

                        </div>
                    </Modal>
                    <Modal className="flow-surface-modal" title="Satellite vulnerability extraction" open={keywordsOpen} onCancel={() => { if (!keywordsSaving) setKeywordsOpen(false); }} footer={<Button disabled={keywordsSaving} onClick={() => setKeywordsOpen(false)}>Close</Button>} width={760}>
                        <p>Keywords select satellite-related vulnerability records. Upload a YAML file with a keywords array (up to 1 MiB, 1,000 keywords, 200 characters each). Matching ignores case, uses word boundaries, and supports multiple keywords per record. An empty array disables matching.</p>
                        <p>Activating a version takes effect within 30 seconds for subsequent processing. Rerun historical records to update their results.</p>
                        <div className="flow-script-actions">
                            <Button icon={<DownloadOutlined />} loading={keywordDownload === 'current'} disabled={!!keywordDownload || !keywordConfig?.current_version} onClick={() => downloadKeywords()}>Download current YAML</Button>
                            {canManage && <Upload accept=".yaml,.yml" showUploadList={false} disabled={keywordsLoading || keywordsSaving} beforeUpload={(file) => { void uploadKeywords(file); return false; }}><Button type="primary" icon={<UploadOutlined />} loading={keywordsSaving} disabled={keywordsLoading}>Upload and activate YAML</Button></Upload>}
                        </div>
                        {keywordsError && <p role="alert">{keywordsError}</p>}
                        {keywordsLoading ? <Spin size="small" /> : keywordConfig && <>
                            <p>Current version: <strong>{keywordConfig.current_version || 'None'}</strong> · {keywordConfig.keywords.length} keywords</p>
                            <div aria-label="Current keywords" style={{ maxHeight: 160, overflowY: 'auto', marginBottom: 16 }}>{keywordConfig.keywords.length ? keywordConfig.keywords.map((keyword) => <Tag key={keyword}>{keyword}</Tag>) : <span>No active keywords</span>}</div>
                            <Table className="flow-data-table" size="small" rowKey="version" dataSource={keywordConfig.versions} pagination={{ pageSize: 5 }} columns={[
                                { title: 'Version', dataIndex: 'version', key: 'version', ellipsis: true, render: (version: string) => <span>{version}{version === keywordConfig.current_version && <Tag color="blue">Current</Tag>}</span> },
                                { title: 'Created', dataIndex: 'created_at', key: 'created_at', render: (value: string) => new Date(value).toLocaleString('en-US') },
                                { title: 'Size', dataIndex: 'size', key: 'size', render: (size: number) => `${size.toLocaleString('en-US')} B` },
                                { title: 'Actions', key: 'actions', width: 180, render: (_: unknown, row: VulnerabilityKeywordConfig['versions'][number]) => <><Button size="small" loading={keywordDownload === row.version} disabled={!!keywordDownload} onClick={() => downloadKeywords(row.version)}>Download</Button>{canManage && row.version !== keywordConfig.current_version && <Button size="small" disabled={keywordsSaving} onClick={() => activateKeywords(row.version)}>Activate</Button>}</> },
                            ]} />
                        </>}
                    </Modal>
                    </>}
                    {queryStatusVisible && queryElapsed !== undefined && <div className={`flow-explorer-query-status${explorerLoading ? '' : ' is-leaving'}`} role="status">
                        <span>{queryCancelled ? 'Cancelled · ' : explorerLoading ? 'Running · ' : ''}Elapsed: {(queryElapsed / 1000).toFixed(1)} s</span>
                        {explorerLoading && <button type="button" onClick={cancelQuery}>Cancel</button>}
                    </div>}
                </ExplorerWorkbench>
            </section>
        </div>
        {canManage && createTableSchema && creationSchemas.some((item) => item.key === createTableSchema) && <NewObjectType variant="storage" schemas={creationSchemas.filter((item) => item.key === createTableSchema)} initialSchema={createTableSchema} onCancel={() => setCreateTableSchema('')} onOpenExisting={(object) => openWizardTable(object.schema, object.table)} onCreated={(schema, created) => openWizardTable(schema, created.name)} />}
        {selectedTableAvailable && <StorageRelationshipTypes key={`${selectedSchema}.${table.name}`} schema={selectedSchema} table={table.name}
            open={relationshipTypesOpen} onClose={() => setRelationshipTypesOpen(false)} onChanged={() => setRelationshipRevision(value => value + 1)} />}
        {deleteSnapshot?.data === data && deleteSnapshot && <ExplorerRecordMutation mode="delete"
            title={explorerRowTitle(deleteSnapshot.row, primaryKey(deleteSnapshot.row), explorerPresentation?.presentation?.titleProperty, data?.primaryKey)}
            busy={mutationLoading}
            disabled={loading || !canDeleteRows || rowVersion(deleteSnapshot.row) !== deleteSnapshot.version}
            onSubmit={() => removeRow(deleteSnapshot.row)} onCancel={() => { if (!mutationPending.current) { setDeleteSnapshot(undefined); setRowDetailsError(''); } }} />}
        <Modal  className="flow-surface-modal flow-offset-modal"  title="Adjust  Kafka  offset"  open={offsetOpen}  onCancel={() => { if (!mutationPending.current) setOffsetOpen(false); }}  cancelText="Cancel"  onOk={saveOffset} confirmLoading={mutationLoading} okButtonProps={{ disabled: !canManage || !stream?.available || offsetPartition === undefined || offset === undefined || offset < 0 || !Number.isSafeInteger(offset) }}  okText="Confirm  SET  OFFSET"  width={460}>
            <div  className="flow-offset-modal-body">
                <div  className="flow-offset-modal-info">
                    <div  className="flow-offset-modal-info-row"><span>Consumer  group</span><strong>{stream?.consumerGroup || 'Unavailable'}</strong></div>
                </div>
                <div className="flow-offset-modal-section-label">Input topic</div>
                <Input.Search value={topicDraft} onChange={(event) => setTopicDraft(event.target.value)} onSearch={(topic) => { if (topic.trim()) { setInputTopics([topic.trim()]); setActiveTopicIndex(0); setOffset(undefined); setOffsetPartition(undefined); setStream(undefined); setOffsets([]); setStreamError(''); } }} enterButton="Load topic" />
                <div className="flow-offset-modal-section-label">Partition</div>
                <Select popupClassName="flow-select-popup" style={{ width: '100%' }} value={offsetPartition} options={offsets.map((item) => ({ value: item.partition, label: `Partition ${item.partition}` }))} onChange={(value) => { setOffsetPartition(value); setOffset(offsets.find((item) => item.partition === value)?.offset); }} />
                {!stream?.available && <div role="alert">{streamError || stream?.reason || 'Stream unavailable'}</div>}
                <div  className="flow-offset-modal-section-label">Current  offsets  <em>live</em></div>
                <div  className="flow-offset-modal-grid">{offsets.map((item)  =>  <div  key={item.partition}  className="flow-offset-modal-cell"><small>Partition  {item.partition}</small><strong>{item.offset.toLocaleString('en-US')}</strong><i  className="flow-dot  flow-dot--green"  /></div>)}</div>
                <div  className="flow-offset-modal-section-label">New  offset</div>
                <InputNumber  min={0}  value={offset}  onChange={(value)  =>  setOffset(value  ??  undefined)}  placeholder="Enter  new  Kafka  offset"  style={{  width:  '100%'  }}  formatter={(value)  =>  `${value  ??  ''}`.replace(/[^\d]/g,  '')}  onKeyDown={(e)  =>  {  const  allowed  =  ['Backspace',  'Delete',  'ArrowLeft',  'ArrowRight',  'ArrowUp',  'ArrowDown',  'Tab',  'Enter',  'Home',  'End'];  if  (allowed.includes(e.key)  ||  e.ctrlKey  ||  e.metaKey)  return;  if  (!/^\d$/.test(e.key))  e.preventDefault();  }}  />
                <div  className="flow-offset-modal-warn"><AlertOutlined  />  Changes  apply  after  the  worker  restarts.  The  consumer  group  will  resume  from  the  new  position.</div>
            </div>
        </Modal>
    </div>;
};
export  default  Pipeline;

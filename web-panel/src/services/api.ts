import axios from 'axios';
import type { AxiosError, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import { clearSession, getAccessToken, getRefreshToken, getSessionState, lockSession, updateAccessToken } from './session';
import type {
  DashboardMetrics,
  LayerNode,
  LayerTable,
  QueryResult,
  TaskInfo,
  TemplateInfo,
  HandlerCode,
  EtlPartition,
  EtlScript,
  EtlStreamState,
  CdcStatus,
  KafkaTopic,
  RedisOffset,
  SchedulerItem,
  Alert,
  MonitorStats,
  UrlPreflightRequest,
  GenerateTemplateRequest,
  DryRunRequest,
  GenerateAdapterRequest,
  WorkspaceTemplateUpdateRequest,
  WorkspaceTaskRequest,
  WorkspaceReleaseRequest,
  WorkspaceTaskActionRequest,
  AnalysisResult,
  DryRunResult,
  WorkspaceTemplate,
  WorkspaceTask,
  AdapterResult,
  AutomationWorkflow,
  SchedulerTaskConfig,
  ProductDomain,
  CurrentUserContext,
} from './types';

// ── 全局 Loading 状态 ──
let activeRequests = 0;
const loadingListeners = new Set<(loading: boolean) => void>();

/** 注册全局 loading 监听器，返回取消函数 */
export function onGlobalLoading(fn: (loading: boolean) => void): () => void {
  loadingListeners.add(fn);
  return () => loadingListeners.delete(fn);
}

function setLoading(v: boolean) {
  if (v) {
    activeRequests++;
  } else {
    activeRequests = Math.max(0, activeRequests - 1);
  }
  const isLoading = activeRequests > 0;
  loadingListeners.forEach((fn) => fn(isLoading));
}

// ── 友好错误消息映射 ──
export function getFriendlyErrorMessage(error: AxiosError): string {
  if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
    return 'The service did not respond. Please try again.';
  }
  if (!error.response) {
    return 'Unable to connect. Check your connection and try again.';
  }
  const { status, data } = error.response;
  const serverMsg =
    (data as Record<string, unknown>)?.errors ||
    (data as Record<string, unknown>)?.detail ||
    (data as Record<string, unknown>)?.message;
  const msg =
    typeof serverMsg === 'string'
      ? serverMsg
      : `Service error (HTTP ${status})`;

  switch (status) {
    case 400:
      return `Invalid request: ${msg}`;
    case 401:
      return 'Your session has expired. Sign in again.';
    case 403:
      return 'You do not have permission to access this resource.';
    case 404:
      return `Resource not found: ${msg}`;
    case 422:
      return `Invalid data: ${msg}`;
    case 500:
      return `Internal service error: ${msg}`;
    case 502:
    case 503:
      return 'The service is temporarily unavailable. Try again later.';
    default:
      return msg;
  }
}

// ── 统一响应解包 ────────────────────────────────────────────────────────────
// 后端使用统一格式: { code: 0, data: ..., message: "...", timestamp: "..." }
// 此处提取 data 字段；旧格式直接透传

function unwrap<T>(body: unknown): T {
  if (
    body &&
    typeof body === 'object' &&
    'code' in body &&
    'data' in body
  ) {
    return (body as { data: T }).data;
  }
  return body as T;
}

// ── Axios 实例 ──
const BASE_URL = '/api/v1';

const client = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

let refreshPromise: Promise<string> | null = null;
let redirectingToLogin = false;

function isAuthenticationError(error: AxiosError): boolean {
  const data = error.response?.data as { errors?: unknown; detail?: unknown } | undefined;
  const detail = data?.errors ?? data?.detail;
  return error.response?.status === 401 || (error.response?.status === 403 &&
    (detail === 'Not authenticated' || detail === 'Could not validate credentials'));
}

function requireLogin() {
  clearSession();
  if (!redirectingToLogin && !['/login', '/reset-password'].includes(location.pathname)) {
    redirectingToLogin = true;
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
  }
}

export const fetchCurrentUserContext = (): Promise<CurrentUserContext> =>
  client.get('/users/me/context').then((r) => unwrap<CurrentUserContext>(r.data));

// 请求拦截器
client.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const session = getSessionState();
    if (!session.authenticated) { requireLogin(); return Promise.reject(new axios.CanceledError('Session expired')); }
    if (session.locked) { lockSession(); return Promise.reject(new axios.CanceledError('Session locked')); }
    const auth = config as InternalAxiosRequestConfig & { _sessionIdentity?: string; _loadingCounted?: boolean };
    if (auth._sessionIdentity && auth._sessionIdentity !== session.identity) return Promise.reject(new axios.CanceledError('Session changed'));
    auth._sessionIdentity = session.identity;
    setLoading(true);
    auth._loadingCounted = true;
    const token = getAccessToken();
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  },
  (error) => {
    return Promise.reject(error);
  },
);

// 响应拦截器
client.interceptors.response.use(
  (response: AxiosResponse) => {
    if ((response.config as InternalAxiosRequestConfig & { _loadingCounted?: boolean })._loadingCounted) setLoading(false);
    return response;
  },
  async (error: AxiosError) => {
    const config = error.config as (InternalAxiosRequestConfig & { _authRetried?: boolean; _loadingCounted?: boolean; _sessionIdentity?: string }) | undefined;
    if (config?._loadingCounted) { setLoading(false); config._loadingCounted = false; }
    if (isAuthenticationError(error) && config) {
      if (config._sessionIdentity !== getSessionState().identity) return Promise.reject(error);
      if (!getSessionState().authenticated) { requireLogin(); return Promise.reject(error); }
      if (!config._authRetried) {
        config._authRetried = true;
        const currentToken = getAccessToken();
        // Another request may already have refreshed this request's token.
        if (currentToken && config.headers.Authorization !== `Bearer ${currentToken}`) {
          return client(config);
        }
        const refreshToken = getRefreshToken();
        if (refreshToken) {
          try {
            if (!refreshPromise) {
              refreshPromise = axios.post(`${BASE_URL}/login/refresh-token`, null, {
                params: { refresh_token: refreshToken }, timeout: 15000,
              }).then((response) => {
                const token = unwrap<{ access_token?: string }>(response.data).access_token;
                if (typeof token !== 'string' || !token) throw new Error('No access token returned');
                // Do not restore a session that was signed out during the refresh.
                if (!updateAccessToken(token, refreshToken)) throw new Error('Session changed');
                return token;
              }).finally(() => { refreshPromise = null; });
            }
            await refreshPromise;
            return client(config);
          } catch (refreshError) {
            // A temporary outage must not discard a valid refresh token.
            if (axios.isAxiosError(refreshError) &&
                ![400, 401, 403, 404].includes(refreshError.response?.status ?? 0)) {
              return Promise.reject(refreshError);
            }
            if (getRefreshToken() !== refreshToken) return Promise.reject(error);
          }
        }
      }
      requireLogin();
    }
    // 统一记录错误日志
    if (axios.isAxiosError(error)) {
      const friendly = getFriendlyErrorMessage(error);
      console.error('[API Error]', friendly);
    } else {
      console.error('[API Error]', 'Unexpected request failure');
    }
    return Promise.reject(error);
  },
);

// ============================================================
//  Dashboard
// ============================================================
export const fetchDashboardMetrics = (): Promise<DashboardMetrics> =>
  client.get('/dashboard/metrics').then((r) => unwrap<DashboardMetrics>(r.data));

export const fetchDashboardAlerts = (): Promise<Alert[]> =>
  client.get('/dashboard/alerts').then((r) => unwrap<Alert[]>(r.data));

export const DASHBOARD_WS_URL = `${
  location.protocol === 'https:' ? 'wss:' : 'ws:'
}//${location.host}/ws`;

// ============================================================
//  ETL Layers
// ============================================================
export const fetchLayers = (): Promise<LayerNode[]> =>
  client.get('/etl/layers').then((r) => unwrap<LayerNode[]>(r.data));

export const fetchCdcStatus = (signal?: AbortSignal): Promise<CdcStatus> =>
  client.get('/cdc/status', { signal }).then((r) => unwrap<CdcStatus>(r.data));

export const fetchLayerTables = (layer: string, tableRole = 'current'): Promise<LayerTable[]> =>
  client.get(`/etl/${layer}/tables`, { params: { tableRole } }).then((r) => unwrap<LayerTable[]>(r.data));

export const fetchTableData = (
  layer: string,
  table: string,
  limit = 50,
  partition?: string,
): Promise<QueryResult> =>
  client
    .get(`/etl/${layer}/${table}/data`, { params: { limit, partition } })
    .then((r) => unwrap<QueryResult>(r.data));

export const fetchTablePartitions = (layer: string, table: string): Promise<EtlPartition[]> =>
  client.get(`/etl/${layer}/${table}/partitions`).then((r) => unwrap<EtlPartition[]>(r.data));

export const fetchTableStream = (layer: string, table: string, topic?: string): Promise<EtlStreamState> =>
  client.get(`/etl/${layer}/${table}/stream`, { params: { topic } }).then((r) => unwrap<EtlStreamState>(r.data));

export const setTableOffset = (layer: string, table: string, partition: number, offset: number, topic?: string): Promise<{ restartRequired: boolean }> =>
  client.put(`/etl/${layer}/${table}/offset`, { partition, offset, topic, confirmation: 'SET OFFSET' }).then((r) => unwrap<{ restartRequired: boolean }>(r.data));

export const fetchLayerScript = (layer: string, table: string): Promise<EtlScript> =>
  client.get(`/etl/${layer}/${table}/script`).then((r) => unwrap<EtlScript>(r.data));

export interface EtlConnection { id: string; name: string; database: string; schemas: string[]; }
export interface EtlDataQuery {
  limit: number; offset: number; partition?: string; search?: string;
  filters: Array<{ id: string; field: string; operator: string; value: string; active: boolean; join: string }>;
  sorts: Array<{ field: string; direction: 'asc' | 'desc' }>;
}
export interface EtlPublishedApi {
  id: string; token: string; validFrom: string; validUntil: string; filterType: string[]; filterSource: string[]; url: string;
  filters?: EtlDataQuery['filters']; sorts?: EtlDataQuery['sorts']; search?: string; partition?: string;
}
export const queryEtlTable = (layer: string, table: string, query: EtlDataQuery): Promise<QueryResult> =>
  client.post(`/etl/${layer}/${table}/data`, query).then((r) => unwrap<QueryResult>(r.data));
export const fetchEtlConnections = (): Promise<EtlConnection[]> =>
  client.get('/etl/connections').then((r) => unwrap<EtlConnection[]>(r.data));
export const createEtlConnection = (payload: Omit<EtlConnection, 'id'>): Promise<EtlConnection> =>
  client.post('/etl/connections', payload).then((r) => unwrap<EtlConnection>(r.data));
export const updateEtlConnection = (id: string, payload: Omit<EtlConnection, 'id'>): Promise<EtlConnection> =>
  client.put(`/etl/connections/${id}`, payload).then((r) => unwrap<EtlConnection>(r.data));
export const deleteEtlConnection = (id: string): Promise<void> =>
  client.delete(`/etl/connections/${id}`).then(() => undefined);
export const updateEtlSchema = (schema: string, payload: { name: string; alias: string }): Promise<void> =>
  client.put(`/etl/schemas/${schema}`, payload).then(() => undefined);
export const deleteEtlSchema = (schema: string): Promise<void> =>
  client.delete(`/etl/schemas/${schema}`).then(() => undefined);
export const createEtlTable = (layer: string, payload: Record<string, unknown>): Promise<LayerTable> =>
  client.post(`/etl/${layer}/tables`, payload).then((r) => unwrap<LayerTable>(r.data));
export const updateEtlRow = (layer: string, table: string, key: Record<string, unknown>, values: Record<string, unknown>): Promise<void> =>
  client.patch(`/etl/${layer}/${table}/rows`, { key, values }).then(() => undefined);
export const deleteEtlRow = (layer: string, table: string, key: Record<string, unknown>): Promise<void> =>
  client.delete(`/etl/${layer}/${table}/rows`, { data: { key } }).then(() => undefined);
export const fetchEtlApis = (layer: string, table: string): Promise<EtlPublishedApi[]> =>
  client.get(`/etl/${layer}/${table}/apis`).then((r) => unwrap<EtlPublishedApi[]>(r.data));
export const createEtlApi = (layer: string, table: string, payload: Partial<EtlPublishedApi>): Promise<EtlPublishedApi> =>
  client.post(`/etl/${layer}/${table}/apis`, payload).then((r) => unwrap<EtlPublishedApi>(r.data));
export const updateEtlApi = (layer: string, table: string, id: string, payload: Partial<EtlPublishedApi>): Promise<EtlPublishedApi> =>
  client.put(`/etl/${layer}/${table}/apis/${id}`, payload).then((r) => unwrap<EtlPublishedApi>(r.data));
export const deleteEtlApi = (layer: string, table: string, id: string): Promise<void> =>
  client.delete(`/etl/${layer}/${table}/apis/${id}`).then(() => undefined);

export interface VulnerabilityKeywordConfig {
  current_version: string | null;
  keywords: string[];
  versions: Array<{ version: string; created_at: string; size: number }>;
}
export const fetchVulnerabilityKeywords = (): Promise<VulnerabilityKeywordConfig> =>
  client.get('/etl/task-config/vulnerability-keywords').then((r) => unwrap<VulnerabilityKeywordConfig>(r.data));
export const uploadVulnerabilityKeywords = (file: File): Promise<VulnerabilityKeywordConfig> => {
  const data = new FormData();
  data.append('file', file);
  return client.post('/etl/task-config/vulnerability-keywords/versions', data, { headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => unwrap<VulnerabilityKeywordConfig>(r.data));
};
export const activateVulnerabilityKeywordVersion = (version: string): Promise<VulnerabilityKeywordConfig> =>
  client.put('/etl/task-config/vulnerability-keywords/current', { version }).then((r) => unwrap<VulnerabilityKeywordConfig>(r.data));
export const downloadVulnerabilityKeywordVersion = (version: string): Promise<Blob> =>
  client.get(`/etl/task-config/vulnerability-keywords/versions/${encodeURIComponent(version)}/file`, { responseType: 'blob' }).then((r) => r.data as Blob);
export const downloadCurrentVulnerabilityKeywords = (): Promise<Blob> =>
  client.get('/etl/task-config/vulnerability-keywords/current/file', { responseType: 'blob' }).then((r) => r.data as Blob);

export const executeQuery = (sql: string): Promise<QueryResult> =>
  client.post('/etl/query', { sql }).then((r) => unwrap<QueryResult>(r.data));

// ============================================================
//  Handlers
// ============================================================
export const fetchHandlerCode = (
  layer: string,
  table: string,
): Promise<HandlerCode> =>
  client.get(`/etl/handlers/${layer}/${table}`).then((r) => unwrap<HandlerCode>(r.data));

export const saveHandlerCode = (
  layer: string,
  table: string,
  code: string,
): Promise<void> =>
  client.put(`/etl/handlers/${layer}/${table}`, { code }).then(() => undefined);

export const validateHandlerCode = (
  layer: string,
  table: string,
  code: string,
): Promise<{ valid: boolean; errors: string[] }> =>
  client
    .post(`/etl/handlers/${layer}/${table}/validate`, { code })
    .then((r) => unwrap<{ valid: boolean; errors: string[] }>(r.data));

// ============================================================
//  Kafka / Redis
// ============================================================
export const fetchKafkaTopics = (): Promise<KafkaTopic[]> =>
  client.get('/kafka/topics').then((r) => unwrap<KafkaTopic[]>(r.data));

export const fetchRedisOffsets = (): Promise<RedisOffset[]> =>
  client.get('/redis/offsets').then((r) => unwrap<RedisOffset[]>(r.data));

// ============================================================
//  Scheduler
// ============================================================
export const fetchSchedulerQueue = (): Promise<SchedulerItem[]> =>
  client.get('/scheduler/queue').then((r) => unwrap<SchedulerItem[]>(r.data));

export const enqueueTask = (
  payload: Record<string, unknown>,
): Promise<void> =>
  client.post('/scheduler/enqueue', payload).then(() => undefined);

// ============================================================
//  Tasks
// ============================================================
export const fetchTasks = (): Promise<TaskInfo[]> =>
  client.get('/tasks').then((r) => unwrap<TaskInfo[]>(r.data));

export const runTask = (taskId: string): Promise<void> =>
  client.post('/tasks/run', { taskId }).then(() => undefined);

export const scheduleTask = (
  payload: Record<string, unknown>,
): Promise<void> =>
  client.post('/tasks/schedule', payload).then(() => undefined);

export const deleteTask = (taskId: string): Promise<void> =>
  client.delete(`/tasks/${taskId}`).then(() => undefined);

// ============================================================
//  Templates
// ============================================================
export const fetchTemplates = (): Promise<TemplateInfo[]> =>
  client.get('/templates').then((r) => unwrap<TemplateInfo[]>(r.data));

export const automationCreateTemplate = (body: {
  name: string; display_name: string; base_url: string; data_type: string; description: string; yaml_content?: string;
  product_domain: ProductDomain; template_kind: string;
}): Promise<{ name: string; message: string }> =>
  client.post('/templates', body).then((r) => unwrap(r.data));

export const automationDeleteTemplate = (name: string): Promise<void> =>
  client.delete(`/templates/${encodeURIComponent(name)}`).then(() => undefined);

// ============================================================
//  Monitoring
// ============================================================
export const fetchMonitorStats = (): Promise<MonitorStats> =>
  client.get('/monitor/stats').then((r) => unwrap<MonitorStats>(r.data));

// ============================================================
//  Monitoring WebSocket
// ============================================================
export const MONITOR_WS_URL = `${
  location.protocol === 'https:' ? 'wss:' : 'ws:'
}//${location.host}/api/monitor/ws`;

// ============================================================
//  AI Collect
// ============================================================
export const aiPreflightUrl = (body: UrlPreflightRequest): Promise<Record<string, unknown>> =>
  client.post('/ai/preflight', body).then((r) => unwrap<Record<string, unknown>>(r.data));

export const aiGenerateTemplate = (body: GenerateTemplateRequest): Promise<AnalysisResult> =>
  client.post('/ai/generate-template', body).then((r) => unwrap<AnalysisResult>(r.data));

export const aiDryRun = (body: DryRunRequest): Promise<DryRunResult> =>
  client.post('/ai/dry-run', body).then((r) => unwrap<DryRunResult>(r.data));

export const aiGenerateAdapter = (body: GenerateAdapterRequest): Promise<AdapterResult> =>
  client.post('/ai/generate-adapter', body).then((r) => unwrap<AdapterResult>(r.data));

export const aiFetchWorkspaceTemplates = (): Promise<{ items: WorkspaceTemplate[] }> =>
  client.get('/ai/workspace/templates').then((r) => unwrap<{ items: WorkspaceTemplate[] }>(r.data));

export const aiFetchWorkspaceTasks = (): Promise<{ items: WorkspaceTask[] }> =>
  client.get('/ai/workspace/tasks').then((r) => unwrap<{ items: WorkspaceTask[] }>(r.data));

export const aiCreateWorkspaceTask = (body: WorkspaceTaskRequest): Promise<WorkspaceTask> =>
  client.post('/ai/workspace/tasks', body).then((r) => unwrap<WorkspaceTask>(r.data));

export const aiUpdateWorkspaceTemplate = (
  templateId: string,
  body: WorkspaceTemplateUpdateRequest,
): Promise<WorkspaceTemplate> =>
  client.put(`/ai/workspace/templates/${templateId}`, body).then((r) => unwrap<WorkspaceTemplate>(r.data));

export const aiReleaseWorkspaceTemplate = (body: WorkspaceReleaseRequest): Promise<{
  template: WorkspaceTemplate;
  task?: WorkspaceTask;
  artifacts?: Record<string, string>;
}> =>
  client.post('/ai/workspace/templates/release', body).then((r) => unwrap(r.data));

export const aiTaskAction = (
  taskId: string,
  body: WorkspaceTaskActionRequest,
): Promise<WorkspaceTask> =>
  client.post(`/ai/workspace/tasks/${taskId}/action`, body).then((r) => unwrap<WorkspaceTask>(r.data));

export const aiDeleteWorkspaceTask = (taskId: string): Promise<void> =>
  client.delete(`/ai/workspace/tasks/${taskId}`).then(() => undefined);

export const automationFetchWorkflows = (): Promise<AutomationWorkflow[]> =>
  client.get('/automation/workflows').then((r) => unwrap<AutomationWorkflow[]>(r.data));

export const automationCreateWorkflow = (body: Omit<AutomationWorkflow, 'id' | 'created_at' | 'updated_at'>): Promise<AutomationWorkflow> =>
  client.post('/automation/workflows', body).then((r) => unwrap<AutomationWorkflow>(r.data));

export const automationUpdateWorkflow = (name: string, body: Partial<AutomationWorkflow>): Promise<AutomationWorkflow> =>
  client.put(`/automation/workflows/${encodeURIComponent(name)}`, body).then((r) => unwrap<AutomationWorkflow>(r.data));

export const automationDeleteWorkflow = (name: string): Promise<void> =>
  client.delete(`/automation/workflows/${encodeURIComponent(name)}`).then(() => undefined);

export const automationFetchSchedules = (): Promise<SchedulerTaskConfig[]> =>
  client.get('/scheduler/tasks').then((r) => unwrap<SchedulerTaskConfig[]>(r.data));

export const automationCreateSchedule = (body: Omit<SchedulerTaskConfig, 'id' | 'created_at' | 'updated_at'>): Promise<SchedulerTaskConfig> =>
  client.post('/scheduler/tasks', body).then((r) => unwrap<SchedulerTaskConfig>(r.data));

export const automationUpdateSchedule = (name: string, body: Partial<SchedulerTaskConfig>): Promise<SchedulerTaskConfig> =>
  client.put(`/scheduler/tasks/${encodeURIComponent(name)}`, body).then((r) => unwrap<SchedulerTaskConfig>(r.data));

export const automationToggleSchedule = (name: string, enabled: boolean): Promise<SchedulerTaskConfig> =>
  client.post(`/scheduler/tasks/${encodeURIComponent(name)}/toggle`, { enabled }).then((r) => unwrap<SchedulerTaskConfig>(r.data));

export const automationDeleteSchedule = (name: string): Promise<void> =>
  client.delete(`/scheduler/tasks/${encodeURIComponent(name)}`).then(() => undefined);

export const automationReloadSchedules = (): Promise<{ loaded: boolean; task_count: number; message: string }> =>
  client.post('/scheduler/reload').then((r) => unwrap(r.data));

export const aiFetchPlatformOverview = (): Promise<Record<string, unknown>> =>
  client.get('/ai/platform/overview').then((r) => unwrap<Record<string, unknown>>(r.data));

export default client;

// Identity and access directory: existing server-managed resources.
export interface AccessPage<T> { items: T[]; total: number; page: number; size: number; pages?: number; }
export interface AccessUser { id: string; full_name?: string | null; email: string; phone: string; status: number; is_superuser: boolean; is_tenant_admin: boolean; created_at?: string; updated_at?: string; }
export interface AccessTenant { id: string; name: string; description?: string | null; plan: number; status: number; created_at?: string; updated_at?: string; }
export interface AccessTeam { id: string; name: string; description?: string | null; tenant_id: string; created_at?: string; updated_at?: string; }
export interface AccessUserInput { full_name: string; email: string; phone: string; password?: string; status: number; is_superuser?: boolean; is_tenant_admin?: boolean; }
export interface AccessTenantInput { name: string; description?: string; plan: number; status: number; }
export interface AccessTeamInput { name: string; description?: string; }
export interface AccessQuery { page?: number; size?: number; search?: string; status?: number; is_superuser?: boolean; is_tenant_admin?: boolean; }
export const fetchAccessUsers = (teamId: string, query: AccessQuery) => client.get('/users/', { params: { team_id: teamId, ...query } }).then(r => unwrap<AccessPage<AccessUser>>(r.data));
export const fetchAccessUser = (id: string, teamId: string) => client.get(`/users/${encodeURIComponent(id)}`, { params: { team_id: teamId } }).then(r => unwrap<AccessUser>(r.data));
export const createAccessUser = (teamId: string, body: AccessUserInput) => client.post('/users/', body, { params: { team_id: teamId } }).then(r => unwrap<AccessUser>(r.data));
export const updateAccessUser = (id: string, teamId: string, body: Partial<AccessUserInput>) => client.patch(`/users/${encodeURIComponent(id)}`, body, { params: { team_id: teamId } }).then(r => unwrap<AccessUser>(r.data));
export const deleteAccessUser = (id: string, teamId: string) => client.delete(`/users/${encodeURIComponent(id)}`, { params: { team_id: teamId } });
export const fetchAccessTenants = (query: AccessQuery) => client.get('/tenant/', { params: query }).then(r => unwrap<AccessPage<AccessTenant>>(r.data));
export const createAccessTenant = (body: AccessTenantInput) => client.post('/tenant/', body).then(r => unwrap<AccessTenant>(r.data));
export const updateAccessTenant = (id: string, body: Partial<AccessTenantInput>) => client.patch(`/tenant/${encodeURIComponent(id)}`, body).then(r => unwrap<AccessTenant>(r.data));
export const deleteAccessTenant = (id: string) => client.delete(`/tenant/${encodeURIComponent(id)}`);
export const fetchAccessTeams = (tenantId: string, query: AccessQuery) => client.get('/team/', { params: { tenant_id: tenantId, ...query } }).then(r => unwrap<AccessPage<AccessTeam>>(r.data));
export const createAccessTeam = (tenantId: string, body: AccessTeamInput) => client.post('/team/', body, { params: { tenant_id: tenantId } }).then(r => unwrap<AccessTeam>(r.data));
export const updateAccessTeam = (id: string, tenantId: string, body: AccessTeamInput) => client.put(`/team/${encodeURIComponent(id)}`, body, { params: { tenant_id: tenantId } }).then(r => unwrap<AccessTeam>(r.data));
export const deleteAccessTeam = (id: string, tenantId: string) => client.delete(`/team/${encodeURIComponent(id)}`, { params: { tenant_id: tenantId } });

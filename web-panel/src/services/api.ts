import axios from 'axios';
import type { AxiosError, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
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
function getFriendlyErrorMessage(error: AxiosError): string {
  if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
    return '后端服务未响应，请检查服务是否已启动';
  }
  if (!error.response) {
    return '网络连接失败，请检查网络后重试';
  }
  const { status, data } = error.response;
  const serverMsg =
    (data as Record<string, unknown>)?.detail ||
    (data as Record<string, unknown>)?.message;
  const msg =
    typeof serverMsg === 'string'
      ? serverMsg
      : `服务器错误 (HTTP ${status})`;

  switch (status) {
    case 400:
      return `请求参数错误: ${msg}`;
    case 401:
      return '未授权，请重新登录';
    case 403:
      return '无权限访问该资源';
    case 404:
      return `资源不存在: ${msg}`;
    case 422:
      return `请求数据校验失败: ${msg}`;
    case 500:
      return `服务器内部错误: ${msg}`;
    case 502:
    case 503:
      return '服务暂时不可用，请稍后重试';
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

export const fetchCurrentUserContext = (): Promise<CurrentUserContext> =>
  client.get('/users/me/context').then((r) => unwrap<CurrentUserContext>(r.data));

// 请求拦截器
client.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    setLoading(true);
    const token = localStorage.getItem('access_token');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  },
  (error) => {
    setLoading(false);
    return Promise.reject(error);
  },
);

// 响应拦截器
client.interceptors.response.use(
  (response: AxiosResponse) => {
    setLoading(false);
    return response;
  },
  (error: AxiosError) => {
    setLoading(false);
    // 统一记录错误日志
    if (axios.isAxiosError(error)) {
      const friendly = getFriendlyErrorMessage(error);
      console.error('[API Error]', friendly, error);
    } else {
      console.error('[API Error]', error);
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

export const fetchTableStream = (layer: string, table: string): Promise<EtlStreamState> =>
  client.get(`/etl/${layer}/${table}/stream`).then((r) => unwrap<EtlStreamState>(r.data));

export const setTableOffset = (layer: string, table: string, partition: number, offset: number): Promise<{ restartRequired: boolean }> =>
  client.put(`/etl/${layer}/${table}/offset`, { partition, offset, confirmation: 'SET OFFSET' }).then((r) => unwrap<{ restartRequired: boolean }>(r.data));

export const fetchLayerScript = (layer: string, table: string): Promise<EtlScript> =>
  client.get(`/etl/${layer}/${table}/script`).then((r) => unwrap<EtlScript>(r.data));

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

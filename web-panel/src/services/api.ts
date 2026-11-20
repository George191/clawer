import axios from 'axios';
import type { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import { clearSession, getAccessToken, getRefreshToken, getSessionState, lockSession, updateAccessToken } from './session';
import type {
  DashboardMetrics,
  LayerNode,
  LayerTable,
  EtlObjectCatalog,
  EtlObjectVisibility,
  EtlObjectFavorites,
  EtlObjectFavoriteMembership,
  EtlObjectPreview,
  EtlTableColumn, ExplorerPreviewResult, QueryResult,
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

export function unwrap<T>(body: unknown): T {
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

export const fetchCurrentUserContext = (signal?: AbortSignal): Promise<CurrentUserContext> =>
  client.get('/users/me/context', { signal, ...(signal ? { timeout: 0 } : {}) }).then((r) => unwrap<CurrentUserContext>(r.data));

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
    const config = error.config as (InternalAxiosRequestConfig & { _authRetried?: boolean; _loadingCounted?: boolean; _sessionIdentity?: string; _noAutomaticAuthReplay?: boolean }) | undefined;
    if (config?._loadingCounted) { setLoading(false); config._loadingCounted = false; }
    if (axios.isCancel(error)) return Promise.reject(error);
    if (config?._noAutomaticAuthReplay) return Promise.reject(error);
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
export const fetchLayers = (signal?: AbortSignal): Promise<LayerNode[]> =>
  client.get('/etl/layers', { signal, ...(signal ? { timeout: 0 } : {}) }).then((r) => unwrap<LayerNode[]>(r.data));

export const fetchEtlObjectTypes = (params: { schema?: string; search?: string; group?: string; ids?: string; limit?: number; offset?: number; includeHidden?: boolean } = {}, signal?: AbortSignal): Promise<EtlObjectCatalog> =>
  client.get('/etl/object-types', { params, signal }).then((r) => unwrap<EtlObjectCatalog>(r.data));

export const fetchEtlObjectPreview = (schema: string, table: string, includeHidden = false): Promise<EtlObjectPreview> =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/preview`, { params: { includeHidden } }).then((r) => unwrap<EtlObjectPreview>(r.data));

export const updateEtlObjectVisibility = (schema: string, table: string, visibility: EtlObjectVisibility): Promise<{ schema: string; table: string; visibility: EtlObjectVisibility }> =>
  client.put(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/visibility`, { visibility }).then((r) => unwrap<{ schema: string; table: string; visibility: EtlObjectVisibility }>(r.data));

export const fetchEtlObjectFavorites = (): Promise<EtlObjectFavorites> =>
  client.get('/etl/object-types/favorites').then((r) => unwrap<EtlObjectFavorites>(r.data));

export const importEtlObjectFavorites = (ids: string[]): Promise<EtlObjectFavorites & { imported: number; skipped: number }> =>
  client.post('/etl/object-types/favorites/import', { ids }).then((r) => unwrap<EtlObjectFavorites & { imported: number; skipped: number }>(r.data));

export const updateEtlObjectFavorite = (schema: string, table: string, favorite: boolean): Promise<EtlObjectFavoriteMembership> => {
  const path = `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/favorite`;
  return (favorite ? client.put(path) : client.delete(path)).then((r) => unwrap<EtlObjectFavoriteMembership>(r.data));
};

export const fetchCdcStatus = (signal?: AbortSignal): Promise<CdcStatus> =>
  client.get('/cdc/status', { signal, timeout: 22000 }).then((r) => unwrap<CdcStatus>(r.data));

export const fetchLayerTables = (layer: string, tableRole = 'current', signal?: AbortSignal): Promise<LayerTable[]> =>
  client.get(`/etl/${layer}/tables`, { params: { tableRole }, signal, ...(signal ? { timeout: 0 } : {}) }).then((r) => unwrap<LayerTable[]>(r.data));

export const fetchEtlTableColumns = (layer: string, table: string, signal?: AbortSignal): Promise<EtlTableColumn[]> =>
  client.get(`/etl/${encodeURIComponent(layer)}/${encodeURIComponent(table)}/columns`, { signal }).then((r) => unwrap<EtlTableColumn[]>(r.data));

export type ColumnPlannerStatistic = { status: 'unavailable' } |
  { status: 'observed'; nullFraction: number; observedAt: string };
export const fetchColumnPlannerStatistic = (schema: string, table: string, column: string, signal?: AbortSignal): Promise<ColumnPlannerStatistic> =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/column-planner-statistic`, { params: { column }, signal })
    .then(r => unwrap<ColumnPlannerStatistic>(r.data));

export type TransferPlanInput = {
  name: string;
  sourceSchema: string;
  sourceTable: string;
  targetSchema: string;
  targetTable: string;
  writeMode: 'append' | 'upsert';
  incrementalCursor?: string | null;
  trigger: 'manual' | 'scheduled' | 'upstream' | 'weekly' | 'monthly';
  scheduleTime: string | null;
  scheduleWeekdayUtc?: number | null;
  scheduleDayOfMonthUtc?: number | null;
};

export type TransferPlan = TransferPlanInput & { id: string; status: 'draft'; updatedAt: string };

export type TransferRunCapabilities = { enabled: boolean; sourceSchema: string; targetSchema: string; maxRows: number; incrementalAppend: boolean; scheduledUpsert: boolean; upstreamUpsert: boolean; weeklyUpsert?: true; monthlyUpsert?: true };
export type TransferRunReceipt = {
  id: string;
  planId: string;
  status: 'committed';
  rowsAffected: number;
  cursorThrough?: string | null;
  hasMore?: boolean | null;
  sourceSchema: string;
  sourceTable: string;
  targetSchema: string;
  targetTable: string;
  recordedAt: string;
  writeMode: TransferPlanInput['writeMode'];
};
export type TransferRunAttempt = {
  id: string;
  planId: string;
  status: 'started' | 'committed' | 'replayed' | 'rejected' | 'outcome_unknown';
  startedAt: string;
  finishedAt?: string | null;
  receiptId?: string | null;
  message?: string | null;
  upstreamJobId?: string | null;
};

export type TransferSchedule = {
  available: boolean;
  enabled: boolean;
  generation: number | null;
  plannedTimeUtc: string | null;
  plannedWeekdayUtc?: number | null;
  plannedDayOfMonthUtc?: number | null;
  armedForUpdatedAt: string | null;
  updatedAt: string | null;
  jobCancelAvailable?: true;
};

export type TransferScheduleJob = {
  id: string;
  slot: string;
  state: 'pending' | 'dispatched' | 'committed' | 'rejected' | 'outcome_unknown' | 'expired' | 'canceled';
  dispatchedAt: string | null;
  finishedAt: string | null;
  receiptId: string | null;
  message: string | null;
  generation?: number;
  cancelable?: true;
  canceledAt?: string | null;
  scheduleWeekdayUtc?: number | null;
  scheduleDayOfMonthUtc?: number | null;
};

export type TransferScheduleSummary = TransferSchedule & { planId: string; planName: string; sourceTable: string; targetTable: string };

export const fetchTransferSchedules = (signal?: AbortSignal): Promise<TransferScheduleSummary[]> =>
  client.get('/etl/transfer-schedules', { signal }).then((r) => unwrap<TransferScheduleSummary[]>(r.data));

const transferSchedulePath = (planId: string) => `/etl/transfer-plans/${encodeURIComponent(planId)}/schedule`;

export const fetchTransferSchedule = (planId: string, signal?: AbortSignal): Promise<TransferSchedule> =>
  client.get(transferSchedulePath(planId), { signal }).then((r) => unwrap<TransferSchedule>(r.data));

export const updateTransferSchedule = (planId: string, request: { enabled: boolean; expectedUpdatedAt: string }): Promise<TransferSchedule> =>
  client.put(transferSchedulePath(planId), request).then((r) => unwrap<TransferSchedule>(r.data));

export const fetchTransferScheduleJobs = (planId: string, signal?: AbortSignal): Promise<TransferScheduleJob[]> =>
  client.get(`${transferSchedulePath(planId)}/jobs`, { signal }).then((r) => unwrap<TransferScheduleJob[]>(r.data));
export type MonthlyCommittedRun = {
  kind: 'monthly_utc'; planId: string; jobId: string; receiptId: string;
  sourceSchema: string; sourceTable: string; targetSchema: string; targetTable: string;
  slot: string; scheduleDayOfMonthUtc: number; recordedAt: string; rowsAffected: number;
};
export const fetchMonthlyCommittedRun = (planId: string, jobId: string, signal?: AbortSignal): Promise<MonthlyCommittedRun> =>
  client.get(`${transferSchedulePath(planId)}/jobs/${encodeURIComponent(jobId)}/committed-run`, { signal })
    .then(r => unwrap<MonthlyCommittedRun>(r.data));
export const cancelTransferScheduleJob = (planId: string, jobId: string, request: { expectedGeneration: number; expectedSlot: string }): Promise<TransferScheduleJob> =>
  client.put(`${transferSchedulePath(planId)}/jobs/${encodeURIComponent(jobId)}/cancel`, request).then((r) => unwrap<TransferScheduleJob>(r.data));

export type TransferUpstreamOperator = 'AND' | 'OR';
export type TransferUpstreamGroup = { operator: TransferUpstreamOperator; parentPlanIds: [string, string] };
export type TransferUpstreamRecordedRule = { operator: TransferUpstreamOperator; group?: TransferUpstreamGroup | null; ruleParentPlanIds?: [string, string, string] };
export type TransferUpstreamTrigger = {
  available: boolean; enabled: boolean; configuredEnabled: boolean; generation: number | null;
  mixedAvailable?: true; mixedConflict?: true; mixedHasHistory?: true; mixedTimeUtc?: string | null;
  operator: TransferUpstreamOperator; group?: TransferUpstreamGroup | null;
  parents: { planId: string; planName: string; armedForUpdatedAt: string }[];
  armedForUpdatedAt: string | null; updatedAt: string | null;
};
export type TransferUpstreamCandidate = { id: string; name: string; updatedAt: string; sourceTable: string; targetTable: string };
export type TransferUpstreamJob = Omit<TransferScheduleJob, 'slot' | 'state' | 'generation' | 'cancelable' | 'canceledAt'> & TransferUpstreamRecordedRule & {
  state: Exclude<TransferScheduleJob['state'], 'canceled'>;
  mixedSlot?: string | null; witnessObservedAt?: string | null;
  events: { eventRecordedAt: string; eventId: string; parentPlanId: string; parentReceiptId: string }[];
};
export type TransferUpstreamSlot = {
  slot: string; decidedAt: string; state: 'admitted' | 'no_witness'; jobId?: string | null; witnessObservedAt?: string | null;
};
export type TransferUpstreamSummary = TransferUpstreamTrigger & {
  planId: string; planName: string; sourceSchema: string; sourceTable: string; targetSchema: string; targetTable: string;
};
const transferUpstreamPath = (planId: string) => `/etl/transfer-plans/${encodeURIComponent(planId)}/upstream-trigger`;
export const fetchTransferUpstreamTrigger = (planId: string, signal?: AbortSignal): Promise<TransferUpstreamTrigger> =>
  client.get(transferUpstreamPath(planId), { signal }).then((r) => unwrap<TransferUpstreamTrigger>(r.data));
export const fetchTransferUpstreamCandidates = (planId: string, signal?: AbortSignal): Promise<TransferUpstreamCandidate[]> =>
  client.get(`${transferUpstreamPath(planId)}/candidates`, { signal }).then((r) => unwrap<TransferUpstreamCandidate[]>(r.data));
export const updateTransferUpstreamTrigger = (planId: string, request: {
  enabled: boolean; operator: TransferUpstreamOperator; group?: TransferUpstreamGroup; parents: { planId: string; expectedUpdatedAt: string }[];
  expectedUpdatedAt: string; expectedGeneration: number | null; mixedTimeUtc?: string | null;
}): Promise<TransferUpstreamTrigger> =>
  client.put(transferUpstreamPath(planId), request).then((r) => unwrap<TransferUpstreamTrigger>(r.data));
export const fetchTransferUpstreamJobs = (planId: string, signal?: AbortSignal): Promise<TransferUpstreamJob[]> =>
  client.get(`${transferUpstreamPath(planId)}/jobs`, { signal }).then((r) => unwrap<TransferUpstreamJob[]>(r.data));
export const fetchTransferUpstreamSlots = (planId: string, signal?: AbortSignal): Promise<TransferUpstreamSlot[]> =>
  client.get(`${transferUpstreamPath(planId)}/slots`, { signal }).then((r) => unwrap<TransferUpstreamSlot[]>(r.data));
export const fetchTransferUpstreamTriggers = (signal?: AbortSignal): Promise<TransferUpstreamSummary[]> =>
  client.get('/etl/transfer-upstream-triggers', { signal }).then((r) => unwrap<TransferUpstreamSummary[]>(r.data));

export const fetchTransferRunCapabilities = (signal?: AbortSignal): Promise<TransferRunCapabilities> =>
  client.get('/etl/transfer-plans/run-capabilities', { signal }).then((r) => unwrap<TransferRunCapabilities>(r.data));

export const fetchTransferRunHistory = (planId: string, signal?: AbortSignal): Promise<TransferRunReceipt[]> =>
  client.get(`/etl/transfer-plans/${encodeURIComponent(planId)}/runs`, { signal }).then((r) => unwrap<TransferRunReceipt[]>(r.data));

export const fetchTransferRunAttempts = (planId: string, signal?: AbortSignal): Promise<TransferRunAttempt[]> =>
  client.get(`/etl/transfer-plans/${encodeURIComponent(planId)}/attempts`, { signal }).then((r) => unwrap<TransferRunAttempt[]>(r.data));

export type TransferTimelineEntry = {
  id: string; kind: 'attempt' | 'receipt'; occurredAt: string; planId: string; planUpdatedAt: string | null;
  status: string; receiptId: string | null; rowsAffected: number | null;
  physicalScope: 'current' | 'historical' | 'unknown'; trigger: 'manual' | 'daily_utc' | 'weekly_utc' | 'monthly_utc' | 'upstream' | null;
  sourceSchema: string | null; sourceTable: string | null; finishedAt: string | null; message: string | null;
};
export type TransferTimelineDetail = TransferTimelineEntry & {
  job: Partial<TransferUpstreamRecordedRule> & { id: string; kind: 'daily_utc' | 'weekly_utc' | 'monthly_utc' | 'upstream'; dueAt: string | null; state: string; scheduleWeekdayUtc?: number | null; scheduleDayOfMonthUtc?: number | null;
    parentEvents: { eventId: string; parentPlanId: string; parentReceiptId: string; eventRecordedAt: string }[];
  } | null;
};
type TransferTimelinePage = { entries: TransferTimelineEntry[]; nextCursor: string | null; latestCurrentReceipt: TransferRunReceipt | null };
const transferTimelinePath = (schema: string, table: string) =>
  `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/transfer-timeline`;
export type TransferTimelineFilters = { view?: 'receipts' | 'rejected_attempts' | 'unconfirmed_attempts'; from?: string; before?: string };
export const fetchTransferTimeline = (schema: string, table: string, cursor?: string, signal?: AbortSignal, filters?: TransferTimelineFilters): Promise<TransferTimelinePage> =>
  client.get(transferTimelinePath(schema, table), { params: { limit: 25, cursor, ...filters }, signal }).then((r) => unwrap<TransferTimelinePage>(r.data));
export const fetchTransferTimelineDetail = (
  schema: string, table: string, kind: TransferTimelineEntry['kind'], id: string, signal?: AbortSignal, requireVisible?: true,
): Promise<TransferTimelineDetail> => client.get(`${transferTimelinePath(schema, table)}/${kind}/${encodeURIComponent(id)}`,
  { signal, params: requireVisible ? { requireVisible: true } : undefined })
  .then((r) => unwrap<TransferTimelineDetail>(r.data));

export const runSandboxTransfer = (planId: string, request: { requestId: string; expectedUpdatedAt: string }): Promise<TransferRunReceipt> =>
  client.post(`/etl/transfer-plans/${encodeURIComponent(planId)}/runs`, request).then((r) => unwrap<TransferRunReceipt>(r.data));

export type TransferPlanPreflight = {
  status: 'compatible' | 'blocked';
  issues: { code: string; message: string; column?: string | null }[];
  checkedColumns: number;
};

export const fetchTransferPlans = (schema: string, signal?: AbortSignal): Promise<TransferPlan[]> =>
  client.get('/etl/transfer-plans', { params: { schema }, signal }).then((r) => unwrap<TransferPlan[]>(r.data));

export const checkTransferPlan = (draft: TransferPlanInput, signal?: AbortSignal): Promise<TransferPlanPreflight> =>
  client.post('/etl/transfer-plans/preflight', draft, { signal }).then((r) => unwrap<TransferPlanPreflight>(r.data));

export const saveTransferPlan = (draft: TransferPlanInput, id?: string): Promise<TransferPlan> =>
  (id ? client.put(`/etl/transfer-plans/${encodeURIComponent(id)}`, draft) : client.post('/etl/transfer-plans', draft))
    .then((r) => unwrap<TransferPlan>(r.data));

export type ExplorerTableEvidenceSnapshot = {
  observedAt: string;
  latestFiledDate: string | null;
  observation: 'observed' | 'no_filed_date' | 'safe_plan_unavailable' | 'metadata_only';
  quality: { source: 'postgres_catalog'; columnCount: number; primaryKeyColumnCount: number; notNullColumnCount: number };
  history: { status: 'unavailable' };
  lineage: { status: 'unavailable' } | { status: 'declared'; mongoCollection: string; rdsTable: string; odsTable: string };
};

export const fetchExplorerTableEvidence = (schema: string, table: string, signal?: AbortSignal): Promise<ExplorerTableEvidenceSnapshot> =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/evidence`, { signal }).then((r) => unwrap<ExplorerTableEvidenceSnapshot>(r.data));

export type ExplorerRecordDetails = {
  row: Record<string, unknown>; identity: Record<string, string>; primaryKey: string[];
  columnTypes: Record<string, string>; rowVersion?: string; rowActions?: {edit: boolean; delete: boolean};
};

export const fetchExplorerRowByKey = (schema: string, table: string, key: Record<string, unknown>, signal?: AbortSignal) =>
  client.post(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/row-by-key`, { key }, { signal })
    .then(r => unwrap<ExplorerRecordDetails>(r.data));

export type ExplorerRowLinkPage = {
  relations: {id: string; name: string; direction: 'outgoing'|'incoming'; kind: 'foreign_key'|'logical';
    schema: string; table: string; fields: string[]; targetFields: string[]}[];
  selected: string | null; identities: Record<string,string>[]; hasMore: boolean; offset: number; reason?: string;
};

export const fetchExplorerRowLinks = (schema: string, table: string, key: Record<string,unknown>, relation: string | null, offset: number, signal: AbortSignal) =>
  client.post(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/row-links`, { key, relation, offset, limit: 10 }, { signal })
    .then(r => unwrap<ExplorerRowLinkPage>(r.data));

export type CdcTableCapture = ({
  state: 'observed'; topic: string; partition: number; offset: number; operation: string;
  sourceLsn: string; eventTimeMs: number | null; checkedAt: string;
} | {
  state: 'outside_capture' | 'unavailable' | 'no_sample' | 'no_topic' | 'unconfigured';
  reason: string; topic?: string;
}) & { recordLink?: { state: 'matched'; source: string; target: string; eventTime: string; checkedAt: string }
  | { state: 'no_sample' | 'unavailable'; reason: string } };
export const fetchTableCapture = (schema: string, table: string, signal: AbortSignal) =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/cdc-evidence`, { signal, timeout: 20000 })
    .then((r) => unwrap<CdcTableCapture>(r.data));

export type SandboxIndexPreviewQuery = {
  mode: 'explain' | 'preview'; idEquals?: string; idFrom?: string; idTo?: string; limit: number; cursor?: string;
};
export type SandboxIndexPreviewResult = {
  status: 'allowed' | 'blocked';
  reason: 'index_path' | 'table_unavailable' | 'primary_index_required' | 'unsupported_plan' | 'query_unavailable';
  guidance: string;
  mode: SandboxIndexPreviewQuery['mode'];
  plan: { scan: 'index' | 'index_only'; key: 'id'; order: 'ascending' } | null;
  rows: { id: string; label: string }[] | null;
  hasMore: boolean | null;
  page?: number; nextCursor?: string; paginationStatus?: 'more' | 'end' | 'capped';
};
const indexPreviewSandboxPath = '/etl/index-preview-sandbox';
type SandboxIndexPreviewCapability = { available: boolean; rangePagination?: true };
export const fetchIndexPreviewSandbox = (signal?: AbortSignal): Promise<SandboxIndexPreviewCapability> =>
  client.get(indexPreviewSandboxPath, { signal }).then((r) => unwrap<SandboxIndexPreviewCapability>(r.data));
export const queryIndexPreviewSandbox = (request: SandboxIndexPreviewQuery, signal?: AbortSignal): Promise<SandboxIndexPreviewResult> =>
  client.post(indexPreviewSandboxPath, request, { signal }).then((r) => unwrap<SandboxIndexPreviewResult>(r.data));

type AssetFreshnessAssessment = {
  state: 'not_configured' | 'not_checked' | 'healthy' | 'overdue' | 'unknown' | 'paused';
  reason: string | null;
  checkedAt: string | null;
  validUntil: string | null;
  receiptRecordedAt: string | null;
  receiptId: string | null;
};
export type AssetHeapBounds = { minBytes: string | null; maxBytes: string | null };
export type AssetSchemaColumn = {
  name: string; type: string; typmod: number; nullable: boolean; primaryKeyPosition: number | null;
};
export type AssetSchemaPreview = { fingerprint: string; columns: AssetSchemaColumn[] };
export type AssetQualityResult = {
  state: 'not_configured' | 'not_checked' | 'healthy' | 'unhealthy' | 'unknown' | 'paused';
  reason: string | null;
  checkedAt: string | null;
  validUntil: string | null;
  heapBytes: string | null;
};
export type AssetSchemaDrift = {
  changes: { kind: 'added' | 'removed' | 'changed'; column: string; before?: AssetSchemaColumn; after?: AssetSchemaColumn }[];
  relativeOrderChanged: boolean; truncated: boolean;
};
export type AssetQualityCheck = AssetQualityResult & {
  schemaDrift?: AssetSchemaDrift;
  history: (Omit<AssetQualityResult, 'validUntil'> & { id: string; generation: number })[];
};
export type AssetQualityIncident = {
  id: string; generation: number; kind: 'freshness' | 'schema' | 'heap_size' | 'daily_job' | 'weekly_job' | 'field_time' | 'minimum_rows';
  status: 'open' | 'acknowledged' | 'recovered' | 'superseded';
  reason: 'write_overdue' | 'schema_drift' | 'below_minimum' | 'above_maximum' | 'target_rejected' | 'field_lag' | 'row_threshold';
  openedAt: string; lastConfirmedAt: string; acknowledgedAt: string | null; closedAt: string | null;
  closeReason: 'healthy' | 'policy_paused' | 'policy_reviewed' | 'schema_baseline_captured' | 'schedule_changed' | 'target_changed' | null;
};
export type AssetFreshness = {
  available: boolean;
  policy: {
    planId: string; planUpdatedAt: string; thresholdMinutes: number; enabled: boolean; generation: number; updatedAt: string;
    schemaCheckEnabled: boolean; heapSize: AssetHeapBounds | null;
    schemaBaseline: (AssetSchemaPreview & { capturedAt: string }) | null;
  } | null;
  assessment: AssetFreshnessAssessment;
  history: (AssetFreshnessAssessment & { id: string; generation: number })[];
  checks: { schema: AssetQualityCheck; heapSize: AssetQualityCheck };
  incidents: { active: AssetQualityIncident[]; history: AssetQualityIncident[] };
};
export type AssetFreshnessCandidate = { id: string; name: string; updatedAt: string };
export type AssetFreshnessChange = {
  planId: string; expectedPlanUpdatedAt: string; expectedGeneration: number | null; thresholdMinutes: number; enabled: boolean;
  schemaCheckEnabled?: boolean; heapSize?: AssetHeapBounds | null;
};
const assetFreshnessPath = (schema: string, table: string) =>
  `/etl/asset-freshness/${encodeURIComponent(schema)}/${encodeURIComponent(table)}`;
export const fetchAssetFreshness = (schema: string, table: string, signal?: AbortSignal): Promise<AssetFreshness> =>
  client.get(assetFreshnessPath(schema, table), { signal }).then((r) => unwrap<AssetFreshness>(r.data));
export const fetchAssetFreshnessCandidates = (schema: string, table: string, signal?: AbortSignal): Promise<AssetFreshnessCandidate[]> =>
  client.get(`${assetFreshnessPath(schema, table)}/candidates`, { signal }).then((r) => unwrap<AssetFreshnessCandidate[]>(r.data));
export const updateAssetFreshness = (schema: string, table: string, request: AssetFreshnessChange): Promise<AssetFreshness> =>
  client.put(assetFreshnessPath(schema, table), request).then((r) => unwrap<AssetFreshness>(r.data));
export const previewAssetSchemaBaseline = (schema: string, table: string, expectedGeneration: number, signal?: AbortSignal): Promise<AssetSchemaPreview> =>
  client.post(`${assetFreshnessPath(schema, table)}/schema-baseline/preview`, { expectedGeneration }, { signal }).then((r) => unwrap<AssetSchemaPreview>(r.data));
export const captureAssetSchemaBaseline = (schema: string, table: string, expectedGeneration: number, expectedFingerprint: string): Promise<AssetFreshness> =>
  client.post(`${assetFreshnessPath(schema, table)}/schema-baseline`, { expectedGeneration, expectedFingerprint }).then((r) => unwrap<AssetFreshness>(r.data));
export const acknowledgeAssetQualityIncident = (schema: string, table: string, id: string, expectedGeneration: number): Promise<AssetQualityIncident> =>
  client.post(`${assetFreshnessPath(schema, table)}/incidents/${encodeURIComponent(id)}/acknowledge`, { expectedGeneration }).then((r) => unwrap<AssetQualityIncident>(r.data));

export type DailyJobQualityCandidate = {
  planId: string; planUpdatedAt: string; freshnessGeneration: number; scheduleGeneration: number; plannedTimeUtc: string;
};
export type DailyJobQuality = {
  available: boolean; state: 'disabled' | 'not_configured' | 'not_checked' | 'healthy' | 'unhealthy' | 'unknown' | 'superseded';
  reason: string | null; checkedAt: string | null; validUntil: string | null;
  candidate: DailyJobQualityCandidate | null;
  binding: (DailyJobQualityCandidate & { generation: number; reviewedAt: string }) | null;
  observation: { slot: string; state: TransferScheduleJob['state']; finishedAt: string | null; receiptId: string | null; rowsAffected: number | null } | null;
  incidents: AssetFreshness['incidents'];
};
export type DailyJobQualityReview = {
  expectedGeneration: number | null; planId: string; expectedPlanUpdatedAt: string;
  expectedFreshnessGeneration: number; expectedScheduleGeneration: number;
};
const dailyJobQualityPath = (schema: string, table: string) => `${assetFreshnessPath(schema, table)}/daily-job-quality`;
export const fetchDailyJobQuality = (schema: string, table: string, signal?: AbortSignal): Promise<DailyJobQuality> =>
  client.get(dailyJobQualityPath(schema, table), { signal }).then(r => unwrap<DailyJobQuality>(r.data));
export const reviewDailyJobQuality = (schema: string, table: string, request: DailyJobQualityReview): Promise<DailyJobQuality> =>
  client.put(dailyJobQualityPath(schema, table), request).then(r => unwrap<DailyJobQuality>(r.data));
export const acknowledgeDailyJobQualityIncident = (schema: string, table: string, id: string, expectedGeneration: number): Promise<AssetQualityIncident> =>
  client.post(`${dailyJobQualityPath(schema, table)}/incidents/${encodeURIComponent(id)}/acknowledge`, { expectedGeneration }).then(r => unwrap<AssetQualityIncident>(r.data));

type WeeklyJobQualityCandidate = DailyJobQualityCandidate & { plannedWeekdayUtc: number };
export type WeeklyJobQuality = Omit<DailyJobQuality, 'candidate' | 'binding'> & {
  candidate: WeeklyJobQualityCandidate | null;
  binding: (WeeklyJobQualityCandidate & { generation: number; reviewedAt: string }) | null;
};
const weeklyJobQualityPath = (schema: string, table: string) => `${assetFreshnessPath(schema, table)}/weekly-job-quality`;
export const fetchWeeklyJobQuality = (schema: string, table: string, signal?: AbortSignal): Promise<WeeklyJobQuality> =>
  client.get(weeklyJobQualityPath(schema, table), { signal }).then(r => unwrap<WeeklyJobQuality>(r.data));
export const reviewWeeklyJobQuality = (schema: string, table: string, request: DailyJobQualityReview): Promise<WeeklyJobQuality> =>
  client.put(weeklyJobQualityPath(schema, table), request).then(r => unwrap<WeeklyJobQuality>(r.data));
export const acknowledgeWeeklyJobQualityIncident = (schema: string, table: string, id: string, expectedGeneration: number): Promise<AssetQualityIncident> =>
  client.post(`${weeklyJobQualityPath(schema, table)}/incidents/${encodeURIComponent(id)}/acknowledge`, { expectedGeneration }).then(r => unwrap<AssetQualityIncident>(r.data));

export type WeeklyJobEvidence = { jobId: string; slot: string; finishedAt: string } & (
  { kind: 'receipt'; receiptId: string; recordedAt: string; rowsAffected: number } |
  { kind: 'target_constraint'; attemptId: string; startedAt: string; attemptFinishedAt: string }
);
export const fetchWeeklyJobEvidence = (
  schema: string, table: string, expectedGeneration: number, expectedSlot: string, signal?: AbortSignal,
): Promise<WeeklyJobEvidence> =>
  client.get(`${weeklyJobQualityPath(schema, table)}/evidence`, { params: { expectedGeneration, expectedSlot }, signal })
    .then(r => unwrap<WeeklyJobEvidence>(r.data));

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
  client.get(`/etl/${layer}/${table}/stream`, { params: { topic }, timeout: 25000 }).then((r) => unwrap<EtlStreamState>(r.data));

export type SandboxCdcStatus = {
  available: boolean;
  state: 'unreviewed' | 'armed' | 'blocked';
  generation: number | null;
  appliedThrough: string;
  sourceHighWater: string | null;
  source: string;
  target: string;
  maxRows: number;
  maxBytes: number;
};
export type SandboxCdcRunRequest = { requestId: string; expectedGeneration: number; expectedAppliedThrough: string };
export type SandboxCdcReceipt = {
  id: string; requestId: string; generation: number; fromExclusive: string; appliedThrough: string;
  rowsAffected: number; recordedAt: string; markings?: string[];
};
const sandboxCdcPath = '/etl/cdc-sandbox';
export type SyntheticPushInboxStatus = {
  available: boolean; state: 'disabled' | 'unconfigured' | 'ready' | 'full' | 'paused';
  intakeControlAvailable?: true; intakeGeneration?: number; pausedAt?: string; secondaryProducerAvailable?: true; eventPathAvailable?: true;
  acceptedCount: number; totalLimit: number; windowSeconds: number; windowLimit: number;
  recent: { id: string; ordinal: string; acceptedAt: string }[];
};
export const fetchSyntheticPushInbox = (signal?: AbortSignal): Promise<SyntheticPushInboxStatus> =>
  client.get('/etl/push-inbox-sandbox', { signal }).then(r => unwrap<SyntheticPushInboxStatus>(r.data));
export const setSyntheticPushIntakePaused = (
  expectedGeneration: number, paused: boolean, signal?: AbortSignal,
): Promise<SyntheticPushInboxStatus> =>
  client.put('/etl/push-inbox-sandbox/intake', { expectedGeneration, paused }, { signal })
    .then(r => unwrap<SyntheticPushInboxStatus>(r.data));
export type SyntheticPushAcceptedReceipt = SyntheticPushInboxStatus['recent'][number];
export type SyntheticPushEventPath = {
  status: 'applied_recorded' | 'accepted_not_applied_at_observation';
  receiptId: string; ordinal: string; acceptedAt: string; observedAt: string;
  applyReceiptId?: string; appliedAt?: string;
};
export const checkSyntheticPushEventPath = (receiptId: string, signal?: AbortSignal): Promise<SyntheticPushEventPath> =>
  client.post('/etl/push-inbox-sandbox/event-path', { receiptId }, { signal })
    .then(r => unwrap<SyntheticPushEventPath>(r.data));
export type PushApplyResult = {
  status: 'applied' | 'no_new_data'; id: string | null; fromExclusive: string; throughInclusive: string;
  reviewedAcceptedThrough: string; eventsApplied: number; appliedAt: string | null; checkedAt: string | null; hasMoreReviewed: boolean;
};
export type PushApplyStatus = {
  state: 'disabled' | 'unconfigured' | 'unreviewed' | 'ready' | 'blocked'; available: boolean; canReplay: boolean; generation: 1 | null;
  acceptedThrough: string | null; appliedThrough: string | null; reviewedAt: string | null; maxEvents: number; recent: PushApplyResult[];
};
export type PushApplyRequest = {
  requestId: string; expectedGeneration: 1; expectedAppliedThrough: string; expectedAcceptedThrough: string;
};
const pushApplyPath = '/etl/push-inbox-sandbox/apply';
export const fetchPushApplyStatus = (signal?: AbortSignal): Promise<PushApplyStatus> =>
  client.get(pushApplyPath, { signal }).then(r => unwrap<PushApplyStatus>(r.data));
export const reviewPushApplyTarget = (): Promise<PushApplyStatus> =>
  client.put(pushApplyPath, { expectedGeneration: null }).then(r => unwrap<PushApplyStatus>(r.data));
export const runPushApply = (request: PushApplyRequest): Promise<PushApplyResult> =>
  client.post(`${pushApplyPath}/runs`, request).then(r => unwrap<PushApplyResult>(r.data));
export const fetchSandboxCdc = (signal?: AbortSignal): Promise<SandboxCdcStatus> =>
  client.get(sandboxCdcPath, { signal }).then((r) => unwrap<SandboxCdcStatus>(r.data));
export const reviewSandboxCdc = (): Promise<SandboxCdcStatus> =>
  client.put(sandboxCdcPath, { expectedGeneration: null }).then((r) => unwrap<SandboxCdcStatus>(r.data));
export const fetchSandboxCdcReceipts = (signal?: AbortSignal): Promise<SandboxCdcReceipt[]> =>
  client.get(`${sandboxCdcPath}/runs`, { signal }).then((r) => unwrap<SandboxCdcReceipt[]>(r.data));
export const applySandboxCdc = (request: SandboxCdcRunRequest): Promise<SandboxCdcReceipt> =>
  client.post(`${sandboxCdcPath}/runs`, request).then((r) => unwrap<SandboxCdcReceipt>(r.data));
export type SandboxCdcVersion = {
  id: string; receiptId: string; generation: number; appliedThrough: string;
  rowsCaptured: number; bytesCaptured: number; recordedAt: string; markings?: string[];
};
export type SandboxCdcVersionList = { available: boolean; versions: SandboxCdcVersion[] };
export type SandboxCdcVersionDetail = SandboxCdcVersion & { rows: { id: string; value: string }[] };
export type SandboxCdcVersionComparison = {
  fromVersionId: string; toVersionId: string;
  markings?: string[];
  changes: { id: string; kind: 'inserted' | 'updated' | 'deleted'; before: string | null; after: string | null }[];
};
const sandboxCdcVersionsPath = `${sandboxCdcPath}/versions`;
export const fetchSandboxCdcVersions = (signal?: AbortSignal): Promise<SandboxCdcVersionList> =>
  client.get(sandboxCdcVersionsPath, { signal }).then((r) => unwrap<SandboxCdcVersionList>(r.data));
export const fetchSandboxCdcVersion = (id: string, signal?: AbortSignal): Promise<SandboxCdcVersionDetail> =>
  client.get(`${sandboxCdcVersionsPath}/${encodeURIComponent(id)}`, { signal }).then((r) => unwrap<SandboxCdcVersionDetail>(r.data));
export const compareSandboxCdcVersions = (
  fromVersionId: string, toVersionId: string, signal?: AbortSignal,
): Promise<SandboxCdcVersionComparison> => client.get(`${sandboxCdcVersionsPath}/compare`, { params: { fromVersionId, toVersionId }, signal })
  .then((r) => unwrap<SandboxCdcVersionComparison>(r.data));

export type SandboxPipelineColumn = 'id' | 'value';
export type SandboxPipelineFilter =
  | { field: 'id'; operator: 'eq' | 'gte' | 'lte'; value: string }
  | { field: 'value'; operator: 'eq' | 'prefix'; value: string };
export type SandboxPipelineConfig = {
  versionId: string; matchVersionId?: string; filters: SandboxPipelineFilter[]; projection: SandboxPipelineColumn[]; additionalMarking?: 'sandbox-restricted' | null;
};
export type SandboxPipelineStatus = {
  available: boolean; latestGeneration: number | null; governanceEnabled?: boolean; allowedMarkings?: string[]; canSave?: boolean; matchEnabled?: true; restoreEnabled?: true;
};
export type SandboxPipelinePreview = {
  versionId: string; matchVersionId?: string; configHash: string; inputRows: number; rightInputRows?: number;
  matchedRows: number; columns: SandboxPipelineColumn[]; rows: string[][]; markings?: string[];
};
export type SandboxPipelineRevision = { generation: number; configHash: string; config: SandboxPipelineConfig; recordedAt: string; markings?: string[]; restoredFromGeneration?: number };
const sandboxPipelinePath = '/etl/pipeline-sandbox';
export const fetchSandboxPipelineStatus = (signal?: AbortSignal): Promise<SandboxPipelineStatus> =>
  client.get(sandboxPipelinePath, { signal }).then((r) => unwrap<SandboxPipelineStatus>(r.data));
export const previewSandboxPipeline = (config: SandboxPipelineConfig, signal?: AbortSignal): Promise<SandboxPipelinePreview> =>
  client.post(`${sandboxPipelinePath}/preview`, config, { signal }).then((r) => unwrap<SandboxPipelinePreview>(r.data));
export const fetchSandboxPipelineRevisions = (signal?: AbortSignal): Promise<SandboxPipelineRevision[]> =>
  client.get(`${sandboxPipelinePath}/revisions`, { signal }).then((r) => unwrap<SandboxPipelineRevision[]>(r.data));
export const saveSandboxPipelineRevision = (config: SandboxPipelineConfig, expectedGeneration: number | null): Promise<SandboxPipelineRevision> =>
  client.post(`${sandboxPipelinePath}/revisions`, { expectedGeneration, config }).then((r) => unwrap<SandboxPipelineRevision>(r.data));
export type SandboxPipelineRestoreRequest = {
  sourceGeneration: number; expectedSourceConfigHash: string; expectedHeadGeneration: number; expectedHeadConfigHash: string;
};
export const restoreSandboxPipelineRevision = (request: SandboxPipelineRestoreRequest, signal?: AbortSignal): Promise<SandboxPipelineRevision> =>
  client.post(`${sandboxPipelinePath}/revisions/restore`, request,
    { signal, _noAutomaticAuthReplay: true } as AxiosRequestConfig & { _noAutomaticAuthReplay: true })
    .then(r => unwrap<SandboxPipelineRevision>(r.data));
export type SandboxPipelineOutput = {
  id: string; requestId: string; reviewedGeneration: number; configHash: string; versionId: string; matchVersionId?: string;
  columns: SandboxPipelineColumn[]; inputRows: number; outputRows: number; recordedAt: string; resultHash: string; markings?: string[];
};
export type SandboxPipelineOutputList = { available: boolean; outputs: SandboxPipelineOutput[] };
export type SandboxPipelineOutputDetail = SandboxPipelineOutput & { rows: string[][] };
export type SandboxPipelineOutputComparison = {
  fromOutputId: string; toOutputId: string; projection: SandboxPipelineColumn[]; markings: string[] | null;
  added: number; removed: number; updated: number; unchanged: number;
  changes: { id: string; kind: 'added' | 'removed' | 'updated'; before: string[] | null; after: string[] | null }[];
  truncated: boolean;
};
export type SandboxPipelineOutputRequest = { requestId: string; expectedGeneration: number; expectedConfigHash: string };
const sandboxPipelineOutputPath = `${sandboxPipelinePath}/outputs`;
export const fetchSandboxPipelineOutputs = (signal?: AbortSignal): Promise<SandboxPipelineOutputList> =>
  client.get(sandboxPipelineOutputPath, { signal }).then((r) => unwrap<SandboxPipelineOutputList>(r.data));
export const fetchSandboxPipelineOutput = (id: string, signal?: AbortSignal): Promise<SandboxPipelineOutputDetail> =>
  client.get(`${sandboxPipelineOutputPath}/${encodeURIComponent(id)}`, { signal }).then((r) => unwrap<SandboxPipelineOutputDetail>(r.data));
export const compareSandboxPipelineOutputs = (request: { fromOutputId: string; toOutputId: string }, signal?: AbortSignal): Promise<SandboxPipelineOutputComparison> =>
  client.post(`${sandboxPipelineOutputPath}/compare`, request, { signal }).then(r => unwrap<SandboxPipelineOutputComparison>(r.data));
export type SandboxPipelineRecordHistory = {
  recordId: string; projection: SandboxPipelineColumn[]; markings?: string[] | null;
  steps: { outputId: string; recordedAt: string; kind: 'baseline' | 'added' | 'changed' | 'removed' | 'unchanged' | 'absent'; row: string[] | null }[];
};
export const readSandboxPipelineRecordHistory = (request: { outputIds: string[]; recordId: string }, signal?: AbortSignal): Promise<SandboxPipelineRecordHistory> =>
  client.post(`${sandboxPipelineOutputPath}/record-history`, request, { signal })
    .then(r => unwrap<SandboxPipelineRecordHistory>(r.data));
export const commitSandboxPipelineOutput = (request: SandboxPipelineOutputRequest): Promise<SandboxPipelineOutput> =>
  client.post(sandboxPipelineOutputPath, request).then((r) => unwrap<SandboxPipelineOutput>(r.data));

export const setTableOffset = (layer: string, table: string, partition: number, offset: number, topic?: string): Promise<{ restartRequired: boolean }> =>
  client.put(`/etl/${layer}/${table}/offset`, { partition, offset, topic, confirmation: 'SET OFFSET' }).then((r) => unwrap<{ restartRequired: boolean }>(r.data));

export const fetchLayerScript = (layer: string, table: string): Promise<EtlScript> =>
  client.get(`/etl/${layer}/${table}/script`).then((r) => unwrap<EtlScript>(r.data));

export interface EtlConnection { id: string; name: string; database: string; schemas: string[]; }
export type DataConnectorEngine = 'postgresql' | 'mysql' | 'oracle' | 'dameng' | 'elasticsearch' | 'milvus';
export interface DataConnectorType {
  engine: DataConnectorEngine; name: string; category: 'relational' | 'non_relational' | 'vector';
  port: number; description: string; driverAvailable: boolean; prerequisite: string; catalogAvailable: boolean;
}
export interface DataSourceConfig {
  host: string; port: number; database: string; username: string;
  auth: 'password' | 'api_key' | 'token'; tls: boolean;
}
export interface DataSourcePayload {
  name: string; engine: DataConnectorEngine; config: DataSourceConfig;
  expectedUpdatedAt?: string;
  credentials: { password?: string; apiKey?: string; token?: string };
}
export type DataSourceCapabilityKey = 'connectionTest' | 'catalog' | 'snapshot' | 'committedBatch' | 'reviewedRange';
export type DataSourceCapability = { status: 'available_to_try' | 'unavailable'; reason?: string | null };
export interface DataSource extends Omit<DataSourcePayload, 'credentials' | 'expectedUpdatedAt'> {
  id: string; credentialsConfigured: boolean; catalogAvailable: boolean; createdBy: string;
  createdAt: string; updatedAt: string; testedAt: string | null; pausedAt?: string | null; pauseAvailable?: boolean;
  capabilities?: Partial<Record<DataSourceCapabilityKey, DataSourceCapability>>;
}
export interface DataSourceTest { ok: boolean; message: string; latencyMs: number; testedAt?: string; }
export type DataSourceRelation = { name: string; kind: 'table' | 'partitioned_table' | 'view' | 'materialized_view' | 'foreign_table' };
export type DataSourceCatalog = { schemas: string[]; schema: string | null; relations: DataSourceRelation[]; hasMore: boolean; schemaHasMore: boolean };
export type DataSourceColumns = { referencesAvailable?: true; schema: string; table: string; columns: { name: string; type: string; nullable: boolean; primaryKey: boolean; position: number }[]; hasMore: boolean };
const dataSourceCatalogPath = (id: string) => `/etl/data-connections/${encodeURIComponent(id)}/catalog`;
export const fetchDataSourceCatalog = (
  id: string, params: { schema?: string; search?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal,
): Promise<DataSourceCatalog> =>
  client.get(dataSourceCatalogPath(id), { params, signal }).then((r) => unwrap<DataSourceCatalog>(r.data));
export const fetchDataSourceColumns = (id: string, schema: string, table: string, signal?: AbortSignal): Promise<DataSourceColumns> =>
  client.get(`${dataSourceCatalogPath(id)}/columns`, { params: { schema, table }, signal }).then((r) => unwrap<DataSourceColumns>(r.data));
export type SourceReferenceDirection = 'incoming' | 'outgoing';
export type DataSourceReferences = {
  direction: SourceReferenceDirection;
  references: { schema: string; table: string; pairs: { fromColumn: string; toColumn: string }[] }[];
  hasMore: boolean;
};
export const fetchDataSourceReferences = (id: string, schema: string, table: string, direction: SourceReferenceDirection, expectedUpdatedAt: string, signal?: AbortSignal): Promise<DataSourceReferences> =>
  client.get(`${dataSourceCatalogPath(id)}/references`, { params: { schema, table, direction, expectedUpdatedAt }, signal }).then(r => unwrap<DataSourceReferences>(r.data));
export type BatchSourceIdentity = {
  state: 'disabled' | 'blocked' | 'paused' | 'unverified' | 'verified' | 'stale'; sourceUpdatedAt: string | null; verifiedAt: string | null;
};
const batchSourceIdentityPath = (id: string) => `/etl/data-connections/${encodeURIComponent(id)}/batch-source-identity`;
export const fetchBatchSourceIdentity = (id: string, signal?: AbortSignal): Promise<BatchSourceIdentity> =>
  client.get(batchSourceIdentityPath(id), { signal }).then(r => unwrap<BatchSourceIdentity>(r.data));
export const verifyBatchSourceIdentity = (id: string, expectedSourceUpdatedAt: string, signal?: AbortSignal): Promise<BatchSourceIdentity> =>
  client.post(batchSourceIdentityPath(id), { expectedSourceUpdatedAt }, { signal, timeout: 45000 }).then(r => unwrap<BatchSourceIdentity>(r.data));
export type ManagedBatchLineageAsset = { schema: string; table: string };
export type BatchSyncCapabilities = { rangeSnapshot?: true; incrementalLineage?: true; lineageAsset?: ManagedBatchLineageAsset; incrementalBatchSync?: true; incrementalPreflight?: true; incrementalMaxRows?: number; incrementalMaxBytes?: number; sourceIdentityRequired?: true; enabled: boolean; maxRows: number; maxBytes: number; targetSchema: string; scheduledBatchSync: boolean };
export type BatchSyncMapping = { sourceColumn: string; targetColumn: string };
export type BatchSyncInput = {
  name: string; connectionId: string; sourceSchema: string; sourceTable: string; sourceKeyColumn: string; targetTable: string;
  mode?: 'incremental'; keyRange?: { min: string; max: string }; mapping: BatchSyncMapping[]; expectedSourceUpdatedAt: string;
};
export type BatchSyncPlan = Omit<BatchSyncInput, 'expectedSourceUpdatedAt'> & { id: string; updatedAt: string; sourceUpdatedAt: string; sourceIdentityReviewed?: true; appliedThrough?: string };
export type BatchSyncReceipt = {
  id: string; planId: string; status: 'committed' | 'no_new_data'; rowsAffected: number; recordedAt: string; sourceUpdatedAt: string; targetTable: string;
  sourceIdentityVerified?: true; keyRange?: { min: string; max: string }; fromExclusive?: string; throughInclusive?: string; sourceHighWater?: string; eventsRead?: number; manifestHash?: string | null;
};
export type BatchSyncAttempt = {
  id: string; planId: string; requestId: string; planUpdatedAt: string;
  status: 'started' | 'committed' | 'no_new_data' | 'replayed' | 'rejected' | 'retryable' | 'outcome_unknown';
  startedAt: string; finishedAt: string | null; receiptId: string | null; code: string | null; message: string | null;
  scheduledJobId: string | null;
};
const batchSyncPath = '/etl/batch-syncs';
const batchSyncItemPath = (id: string) => `${batchSyncPath}/${encodeURIComponent(id)}`;
export const fetchBatchSyncCapabilities = (signal?: AbortSignal): Promise<BatchSyncCapabilities> =>
  client.get(`${batchSyncPath}/capabilities`, { signal }).then((r) => unwrap<BatchSyncCapabilities>(r.data));
export type BatchIncrementalPreflight = {
  status: 'matched' | 'no_new_data'; observedAppliedThrough: string; sourcePublishedThrough: string;
  nextOrdinal?: string; nextRowCount?: number; observedAt: string;
};
export const verifyNextCommittedBatch = (planId: string, review: {
  expectedUpdatedAt: string; expectedSourceUpdatedAt: string;
}, signal?: AbortSignal): Promise<BatchIncrementalPreflight> =>
  client.post(`${batchSyncItemPath(planId)}/incremental-preflight`, review, { signal })
    .then(r => unwrap<BatchIncrementalPreflight>(r.data));
export const fetchBatchSyncs = (connectionId: string, signal?: AbortSignal, planId?: string): Promise<BatchSyncPlan[]> =>
  client.get(batchSyncPath, { params: { connectionId, planId }, signal }).then((r) => unwrap<BatchSyncPlan[]>(r.data));
export const saveBatchSync = (payload: BatchSyncInput, id?: string): Promise<BatchSyncPlan> =>
  (id ? client.put(batchSyncItemPath(id), payload) : client.post(batchSyncPath, payload)).then((r) => unwrap<BatchSyncPlan>(r.data));
export const fetchBatchSyncReceipts = (id: string, signal?: AbortSignal): Promise<BatchSyncReceipt[]> =>
  client.get(`${batchSyncItemPath(id)}/runs`, { signal }).then((r) => unwrap<BatchSyncReceipt[]>(r.data));
export const fetchBatchSyncAttempts = (id: string, signal?: AbortSignal): Promise<BatchSyncAttempt[]> =>
  client.get(`${batchSyncItemPath(id)}/attempts`, { signal }).then((r) => unwrap<BatchSyncAttempt[]>(r.data));
export const runBatchSync = (id: string, request: { requestId: string; expectedUpdatedAt: string }): Promise<BatchSyncReceipt> =>
  client.post(`${batchSyncItemPath(id)}/runs`, request).then((r) => unwrap<BatchSyncReceipt>(r.data));
export type BatchSyncSchedule = TransferSchedule & { armedForSourceUpdatedAt: string | null; configuredEnabled: boolean };
export type BatchSyncScheduleSummary = BatchSyncSchedule & { planId: string; planName: string; connectionId: string; sourceTable: string; targetTable: string };
export const fetchBatchSyncSchedules = (signal?: AbortSignal): Promise<BatchSyncScheduleSummary[]> =>
  client.get('/etl/batch-sync-schedules', { signal }).then((r) => unwrap<BatchSyncScheduleSummary[]>(r.data));
const batchSyncSchedulePath = (id: string) => `${batchSyncItemPath(id)}/schedule`;
export const fetchBatchSyncSchedule = (id: string, signal?: AbortSignal): Promise<BatchSyncSchedule> =>
  client.get(batchSyncSchedulePath(id), { signal }).then((r) => unwrap<BatchSyncSchedule>(r.data));
export const updateBatchSyncSchedule = (id: string, request: { enabled: boolean; plannedTimeUtc: string; expectedUpdatedAt: string; expectedGeneration: number | null }): Promise<BatchSyncSchedule> =>
  client.put(batchSyncSchedulePath(id), request).then((r) => unwrap<BatchSyncSchedule>(r.data));
export type BatchSyncScheduleJob = Omit<TransferScheduleJob, 'state' | 'generation' | 'cancelable' | 'canceledAt'> & {
  state: Exclude<TransferScheduleJob['state'], 'canceled'> | 'no_new_data';
};
export const fetchBatchSyncScheduleJobs = (id: string, signal?: AbortSignal): Promise<BatchSyncScheduleJob[]> =>
  client.get(`${batchSyncSchedulePath(id)}/jobs`, { signal }).then((r) => unwrap<BatchSyncScheduleJob[]>(r.data));

const tableExportPath = '/etl/table-export';
const tableExportSnapshotPreviewPath = '/etl/table-export-sandbox/snapshot-preview';
export type TableExportSnapshotCapability = { available: boolean; configRevision?: string };
export type TableExportSnapshotResult = {
  configRevision: string; sourceRows: number; targetRows: number; added: number; changed: number; unchanged: number; wouldRemove: number;
};
export const fetchTableExportSnapshotCapability = (signal?: AbortSignal): Promise<TableExportSnapshotCapability> =>
  client.get(tableExportSnapshotPreviewPath, { signal }).then(r => unwrap<TableExportSnapshotCapability>(r.data));
export const previewTableExportSnapshot = (expectedConfigRevision: string, signal?: AbortSignal): Promise<TableExportSnapshotResult> =>
  client.post(tableExportSnapshotPreviewPath, { expectedConfigRevision }, { signal }).then(r => unwrap<TableExportSnapshotResult>(r.data));
const snapshotDeliveryEvidencePath = '/etl/table-export-sandbox/snapshot-delivery-evidence';
export type SnapshotDeliveryCapability = { available: boolean; configRevision?: string };
export type SnapshotDeliveryEvidence = {
  state: 'not_admitted' | 'unconfirmed' | 'confirmed_recorded_receipt';
  generation?: number; rowCount?: number; recordedAt?: string;
};
export const fetchSnapshotDeliveryCapability = (signal?: AbortSignal): Promise<SnapshotDeliveryCapability> =>
  client.get(snapshotDeliveryEvidencePath, { signal }).then(r => unwrap<SnapshotDeliveryCapability>(r.data));
export const checkSnapshotDeliveryEvidence = (request: {
  intentId: string; requestId: string; expectedConfigRevision: string;
}, signal?: AbortSignal): Promise<SnapshotDeliveryEvidence> =>
  client.post(snapshotDeliveryEvidencePath, request, { signal }).then(r => unwrap<SnapshotDeliveryEvidence>(r.data));
const snapshotDeliveryPath = '/etl/table-export-sandbox/snapshot-delivery';
export type FrozenDeliveryIds = { intentId: string; requestId: string };
export type FrozenDeliveryReview = { rowCount: number; expectedPreviousGeneration: number; webRevision: string };
export type FrozenDeliveryReceipt = FrozenDeliveryIds & { generation: number; rowCount: number; recordedAt: string };
export const fetchFrozenDeliveryCapability = (signal?: AbortSignal): Promise<{ available: boolean }> =>
  client.get(snapshotDeliveryPath, { signal }).then(r => unwrap<{ available: boolean }>(r.data));
export const reviewFrozenDelivery = (request: FrozenDeliveryIds, signal?: AbortSignal): Promise<FrozenDeliveryReview> =>
  client.post(`${snapshotDeliveryPath}/review`, request, { signal }).then(r => unwrap<FrozenDeliveryReview>(r.data));
export const executeFrozenDelivery = (request: FrozenDeliveryIds & { expectedWebRevision: string }, signal?: AbortSignal): Promise<FrozenDeliveryReceipt> =>
  client.post(snapshotDeliveryPath, request,
    { signal, _noAutomaticAuthReplay: true } as AxiosRequestConfig & { _noAutomaticAuthReplay: true })
    .then(r => unwrap<FrozenDeliveryReceipt>(r.data));
const snapshotIntentPath = '/etl/table-export-sandbox/snapshot-intent';
export type SnapshotIntentRequest = { requestId: string; expectedConfigRevision: string; expectedPreviousGeneration: number };
export type SnapshotIntentHead = { generation: number };
export type SnapshotIntentResult = { id: string; expectedPreviousGeneration: number; rowCount: number; createdAt: string };
export const fetchSnapshotIntentCapability = (signal?: AbortSignal): Promise<SnapshotDeliveryCapability> =>
  client.get(snapshotIntentPath, { signal }).then(r => unwrap<SnapshotDeliveryCapability>(r.data));
export const checkSnapshotIntentHead = (expectedConfigRevision: string, signal?: AbortSignal): Promise<SnapshotIntentHead> =>
  client.post(`${snapshotIntentPath}/head`, { expectedConfigRevision }, { signal }).then(r => unwrap<SnapshotIntentHead>(r.data));
export const prepareSnapshotIntent = (request: SnapshotIntentRequest, signal?: AbortSignal): Promise<SnapshotIntentResult> =>
  client.post(snapshotIntentPath, request, { signal, _noAutomaticAuthReplay: true } as AxiosRequestConfig & { _noAutomaticAuthReplay: true }).then(r => unwrap<SnapshotIntentResult>(r.data));
export type TableExportDestination = 'primary' | 'secondary';
export type TableExportDestinationOption = { id: TableExportDestination; label: string };
export type TableExportWriteMode = 'upsert' | 'append';
export type TableExportCapabilities = {
  enabled: boolean; provisioned: boolean; sourceSchema: string; sourceTable: string; targetSchema: string; targetTable: string;
  maxRows: number; maxCaptionBytes: number; mapping: { sourceColumn: string; targetColumn: string; type: string }[];
  approvedDestinations?: TableExportDestinationOption[]; destinationLabel?: string;
  appendAvailable?: true;
};
export type TableExportPlan = { id: string; name: string; updatedAt: string; writeMode?: TableExportWriteMode };
export type TableExportGrant = {
  marking: 'sandbox-synthetic'; available: boolean; effective: boolean; generation: number | null;
  status: 'not_granted' | 'active' | 'stale' | 'revoked';
  planUpdatedAt: string | null; approvedAt: string | null; revokedAt: string | null;
  writeMode?: TableExportWriteMode;
};
export type TableExportGrantChange = {
  approved: boolean; expectedPlanUpdatedAt: string; expectedGeneration: number | null;
  marking: TableExportGrant['marking']; reason: string; writeMode?: TableExportWriteMode;
};
export type TableExportGrantEvent = {
  id: string; action: 'approved' | 'revoked'; generation: number;
  marking: TableExportGrant['marking']; recordedAt: string; planUpdatedAt: string; reason: string; writeMode?: TableExportWriteMode;
};
export type TableExportReceipt = { id: string; requestId: string; status: 'committed'; rowsAffected: number; recordedAt: string; grantGeneration: number; writeMode?: TableExportWriteMode };
export type TableExportAttempt = Omit<BatchSyncAttempt, 'planId' | 'scheduledJobId' | 'status'> & {
  grantGeneration: number; status: Exclude<BatchSyncAttempt['status'], 'no_new_data'>; writeMode?: TableExportWriteMode;
};
export type TableExportLineageReceipt = {
  id: string; recordedAt: string; rowsAffected: number; reviewedAt: string; observed: boolean;
  reviewRelation: 'current_review' | 'earlier_review' | 'different_binding';
};
export type TableExportLineage = {
  source: { schema: string; table: string };
  destination: { schema: string; table: string; label: string };
  declaration: { id: string; name: string; reviewedAt: string; state: 'enabled' | 'inactive' | 'stale' | 'blocked' } | null;
  receipts: TableExportLineageReceipt[];
  latestObservation: TableExportLineageReceipt | null;
  observed: boolean;
};
export const fetchTableExportLineage = (signal?: AbortSignal): Promise<TableExportLineage> =>
  client.get(`${tableExportPath}/lineage`, { signal }).then((r) => unwrap<TableExportLineage>(r.data));
const exportDestinationParams = (destination?: TableExportDestination) => destination ? { destination } : undefined;
export const fetchTableExportCapabilities = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportCapabilities> =>
  client.get(`${tableExportPath}/capabilities`, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportCapabilities>(r.data));
export const fetchTableExportPlan = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportPlan | null> =>
  client.get(tableExportPath, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportPlan | null>(r.data));
export const saveTableExportPlan = (request: { name: string; expectedUpdatedAt: string | null; writeMode?: TableExportWriteMode }, destination?: TableExportDestination): Promise<TableExportPlan> =>
  client.put(tableExportPath, request, { params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportPlan>(r.data));
export const fetchTableExportGrant = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportGrant> =>
  client.get(`${tableExportPath}/grant`, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportGrant>(r.data));
export const updateTableExportGrant = (request: TableExportGrantChange, destination?: TableExportDestination): Promise<TableExportGrant> =>
  client.put(`${tableExportPath}/grant`, request, { params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportGrant>(r.data));
export const fetchTableExportGrantHistory = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportGrantEvent[]> =>
  client.get(`${tableExportPath}/grant/history`, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportGrantEvent[]>(r.data));
export const fetchTableExportReceipts = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportReceipt[]> =>
  client.get(`${tableExportPath}/runs`, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportReceipt[]>(r.data));
export const fetchTableExportAttempts = (signal?: AbortSignal, destination?: TableExportDestination): Promise<TableExportAttempt[]> =>
  client.get(`${tableExportPath}/attempts`, { signal, params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportAttempt[]>(r.data));
export const runTableExport = (request: { requestId: string; expectedUpdatedAt: string; expectedGrantGeneration: number; writeMode?: TableExportWriteMode }, destination?: TableExportDestination): Promise<TableExportReceipt> =>
  client.post(`${tableExportPath}/runs`, request, { params: exportDestinationParams(destination) }).then((r) => unwrap<TableExportReceipt>(r.data));
export const fetchDataConnectorTypes = (): Promise<DataConnectorType[]> =>
  client.get('/etl/connector-types').then((r) => unwrap<DataConnectorType[]>(r.data));
export const fetchDataSources = (signal?: AbortSignal): Promise<DataSource[]> =>
  client.get('/etl/data-connections', { signal }).then((r) => unwrap<DataSource[]>(r.data));
export const testDataSource = (payload: DataSourcePayload, connectionId?: string): Promise<DataSourceTest> =>
  client.post('/etl/data-connections/test', payload, { params: { connectionId }, timeout: 45000 }).then((r) => unwrap<DataSourceTest>(r.data));
export const createDataSource = (payload: DataSourcePayload): Promise<DataSource> =>
  client.post('/etl/data-connections', payload, { timeout: 45000 }).then((r) => unwrap<DataSource>(r.data));
export const updateDataSource = (id: string, payload: DataSourcePayload): Promise<DataSource> =>
  client.put(`/etl/data-connections/${id}`, payload, { timeout: 45000 }).then((r) => unwrap<DataSource>(r.data));
export const setDataSourcePaused = (id: string, request: { paused: boolean; expectedUpdatedAt: string }): Promise<DataSource> =>
  client.put(`/etl/data-connections/${encodeURIComponent(id)}/pause`, request).then((r) => unwrap<DataSource>(r.data));
export const deleteDataSource = (id: string): Promise<void> =>
  client.delete(`/etl/data-connections/${id}`).then(() => undefined);
export interface EtlDataQuery {
  withIdentity?: boolean;
  forEditing?: boolean;
  limit: number; offset: number; partition?: string; search?: string;
  filters: Array<{ id: string; field: string; operator: string; value: string; active: boolean; join: string }>;
  sorts: Array<{ field: string; direction: 'asc' | 'desc' }>;
}
export interface EtlPublishedApi {
  id: string; keyStatus: 'active' | 'unissued' | 'revoked'; validFrom: string; validUntil: string; filterType: string[]; filterSource: string[]; url: string;
  filters?: EtlDataQuery['filters']; sorts?: EtlDataQuery['sorts']; search?: string; partition?: string;
}
export type PublishedKey = Pick<EtlPublishedApi,'id'|'keyStatus'|'validFrom'|'validUntil'|'url'> & {schema:string;table:string};
export type TeamKey = {id: string; short_key:string; description:string | null; status:number};
export const publicationKeys = (q:string, offset:number) =>
  client.get('/apikey/publications', {params:{q,offset,limit:25}}).then(r=>unwrap<{items:PublishedKey[];hasMore:boolean}>(r.data));
export const issuePublicationKey = (id:string) =>
  client.post('/apikey/publications/'+id+'/rotate').then(r=>unwrap<{key:string}>(r.data));
export const revokePublicationKey = (id:string) => client.delete('/apikey/publications/'+id+'/key');
export const teamKeys = (team:string,page:number) =>
  client.get('/apikey/'+team,{params:{page,size:25}}).then(r=>unwrap<{items:TeamKey[];pages:number}>(r.data));
export const createTeamKey = (team:string,description:string) =>
  client.post('/apikey/',{description},{params:{team_id:team}}).then(r=>unwrap<{key:string}>(r.data));
export const rotateTeamKey = (team:string,id:string) =>
  client.post('/apikey/'+id+'/rotate',null,{params:{team_id:team}}).then(r=>unwrap<{key:string}>(r.data));
export const deleteTeamKey = (team:string,id:string) => client.delete('/apikey/'+id,{params:{team_id:team}});
export const queryEtlTable = (layer: string, table: string, query: EtlDataQuery, signal?: AbortSignal): Promise<QueryResult> =>
  client.post(`/etl/${layer}/${table}/data`, query, { signal }).then((r) => unwrap<QueryResult>(r.data));
export const queryExplorerPreview = (layer: string, table: string, query: EtlDataQuery, signal?: AbortSignal): Promise<ExplorerPreviewResult> =>
  client.post(`/etl/${encodeURIComponent(layer)}/${encodeURIComponent(table)}/preview-rows`, query, { signal, timeout: 0 }).then((r) => unwrap<ExplorerPreviewResult>(r.data));

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
export const updateEtlRow = (layer: string, table: string, key: Record<string, unknown>, values: Record<string, unknown>, expectedVersion: string): Promise<void> =>
  client.patch(`/etl/${layer}/${table}/rows`, { key, values, expectedVersion }).then(() => undefined);
export const deleteEtlRow = (layer: string, table: string, key: Record<string, unknown>, expectedVersion: string): Promise<void> =>
  client.delete(`/etl/${layer}/${table}/rows`, { data: { key, expectedVersion } }).then(() => undefined);
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

export type CommittedBatchLineageSummary = {
  state: 'unreviewed' | 'reviewed' | 'observed' | 'archived'; sourceSchema: string; sourceTable: string;
  targetSchema: string; targetTable: string; appliedThrough: string | null;
  latestPositiveReceiptId: string | null; latestPositiveRecordedAt: string | null;
};
export type CommittedBatchLineageEntry = {
  id: string; kind: 'receipt' | 'attempt'; occurredAt: string; planUpdatedAt: string;
  status: 'started' | 'committed' | 'no_new_data' | 'replayed' | 'rejected' | 'retryable' | 'outcome_unknown';
  physicalScope: 'current' | 'archived'; finishedAt: string | null; receiptId: string | null; trigger: 'manual' | 'daily_utc' | null;
  rowsAffected: number | null; fromExclusive: string | null; throughInclusive: string | null; sourceHighWater: string | null;
  eventsRead: number | null; code: string | null;
};
export type CommittedBatchLineageDetail = CommittedBatchLineageEntry & {
  job: { slot: string; state: BatchSyncScheduleJob['state']; dispatchedAt: string | null; finishedAt: string | null } | null;
};
export type CommittedBatchLineagePage = {
  lineage: CommittedBatchLineageSummary; entries: CommittedBatchLineageEntry[]; nextCursor: string | null; from: string; to: string;
};
const committedBatchLineagePath = ({ schema, table }: ManagedBatchLineageAsset) =>
  `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/batch-lineage-timeline`;
export const fetchCommittedBatchLineage = (asset: ManagedBatchLineageAsset, params: { from?: string; to?: string; limit?: number; cursor?: string }, signal?: AbortSignal): Promise<CommittedBatchLineagePage> =>
  client.get(committedBatchLineagePath(asset), { params, signal }).then(r => unwrap<CommittedBatchLineagePage>(r.data));
export const fetchCommittedBatchLineageEntry = (asset: ManagedBatchLineageAsset, kind: CommittedBatchLineageEntry['kind'], id: string, signal?: AbortSignal): Promise<CommittedBatchLineageDetail> =>
  client.get(`${committedBatchLineagePath(asset)}/${kind}/${encodeURIComponent(id)}`, { signal }).then(r => unwrap<CommittedBatchLineageDetail>(r.data));

export type CollectedStatisticsSample = {
  capturedAt: string; policyGeneration: number; planUpdatedAt: string; estimatedRows: string | null; lastAnalyzedAt: string | null;
};
export type CollectedAssetStatistics = {
  available: boolean; state: 'unavailable' | 'not_configured' | 'not_collected' | 'no_stats' | 'current' | 'unknown';
  reason: 'checking_disabled' | 'target_unavailable' | 'target_changed' | 'policy_changed' | 'policy_paused'
    | 'stats_reset' | 'observation_stale' | 'probe_failed' | null;
  current: CollectedStatisticsSample | null; historical: CollectedStatisticsSample | null; validUntil: string | null;
};
export const fetchCollectedAssetStatistics = (schema: string, table: string, signal?: AbortSignal): Promise<CollectedAssetStatistics> =>
  client.get(`/etl/asset-freshness/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/collected-stats`, { signal })
    .then(r => unwrap<CollectedAssetStatistics>(r.data));

export type TableLineageDirection = 'incoming' | 'outgoing';
export type TableLineageReferenceKind = 'object_relation' | 'foreign_key';
export type TableLineageIdentity = { schema: string; table: string };
export type TableLineageNeighbor = TableLineageIdentity & { kinds: TableLineageReferenceKind[] };
export type TableLineageNeighborhood = { direction: TableLineageDirection; neighbors: TableLineageNeighbor[]; truncated: boolean };
export const fetchTableLineageNeighbors = (schema: string, table: string, direction: TableLineageDirection, signal?: AbortSignal): Promise<TableLineageNeighborhood> =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/lineage-neighbors`, { params: { direction }, signal })
    .then(r => unwrap<TableLineageNeighborhood>(r.data));

export type TableLineagePathEdge = { from: TableLineageIdentity; to: TableLineageIdentity; kinds: TableLineageReferenceKind[] };
export type TableLineagePathResult = {
  state: 'found' | 'not_found_within_limit' | 'incomplete'; searchTruncated: boolean; maxHops: 4;
  edges: TableLineagePathEdge[];
};
export type TableLineageCommonUpstream = {
  state: 'found' | 'none_within_limit' | 'incomplete'; searchTruncated: boolean; ancestor: TableLineageIdentity | null;
  leftEdges: TableLineagePathEdge[]; rightEdges: TableLineagePathEdge[];
};
export const fetchTableLineageTargets = (source: TableLineageIdentity, search: string, signal?: AbortSignal): Promise<{
  items: TableLineageIdentity[]; truncated: boolean;
}> => client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-path-targets`, { params: { search }, signal })
  .then(r => unwrap<{ items: TableLineageIdentity[]; truncated: boolean }>(r.data));
export const fetchTableLineagePath = (source: TableLineageIdentity, target: TableLineageIdentity, signal?: AbortSignal): Promise<TableLineagePathResult> =>
  client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-path`, {
    params: { targetSchema: target.schema, targetTable: target.table }, signal,
  }).then(r => unwrap<TableLineagePathResult>(r.data));
export const fetchTableLineageCommonUpstream = (source: TableLineageIdentity, target: TableLineageIdentity, signal?: AbortSignal): Promise<TableLineageCommonUpstream> =>
  client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-common-upstream`, {
    params: { targetSchema: target.schema, targetTable: target.table }, signal,
  }).then(r => unwrap<TableLineageCommonUpstream>(r.data));

export type FieldFreshness = {
  enabled: boolean; generation: number; policyGeneration: number | null; maxLagSeconds: number | null;
  state: 'unconfigured' | 'unknown' | 'fresh' | 'stale' | 'superseded'; reason: string | null;
  fieldRecordedAt: string | null; receiptRecordedAt: string | null; checkedAt: string | null; validUntil: string | null;
  lagSeconds: number | null; incident: AssetQualityIncident | null;
};
export type FieldFreshnessReview = { expectedGeneration: number; policyGeneration: number; maxLagSeconds: number };
const fieldFreshnessPath = `${assetFreshnessPath('ts_transfer_sandbox', 'field_freshness_fixture')}/field-freshness`;
export const fetchFieldFreshness = (signal?: AbortSignal): Promise<FieldFreshness> =>
  client.get(fieldFreshnessPath, { signal }).then(r => unwrap<FieldFreshness>(r.data));
export const reviewFieldFreshness = (request: FieldFreshnessReview): Promise<FieldFreshness> =>
  client.put(fieldFreshnessPath, request).then(r => unwrap<FieldFreshness>(r.data));
export const acknowledgeFieldFreshnessIncident = (id: string, expectedGeneration: number): Promise<AssetQualityIncident> =>
  client.post(`${fieldFreshnessPath}/incidents/${encodeURIComponent(id)}/acknowledge`, { expectedGeneration }).then(r => unwrap<AssetQualityIncident>(r.data));

export type ObservedTableLineage = {
  available: boolean; direction: TableLineageDirection; truncated: boolean;
  neighbors: (TableLineageIdentity & { receiptId: string; receiptRecordedAt: string; rowsAffected: number; trigger?: 'daily_utc' | 'weekly_utc' })[];
};
export const fetchObservedTableLineage = (source: TableLineageIdentity, direction: TableLineageDirection, signal?: AbortSignal): Promise<ObservedTableLineage> =>
  client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-observed`, { params: { direction }, signal })
    .then(r => unwrap<ObservedTableLineage>(r.data));

export type PreviewIndexCoverageOperator = 'eq' | 'lt' | 'lte' | 'gt' | 'gte' | 'between';
export type PreviewIndexCoverageState = 'candidate' | 'no_candidate' | 'unsupported' | 'unavailable';
export type PreviewIndexCoverageTerm = { field: string; operator: PreviewIndexCoverageOperator };
export type PreviewIndexCoverageInput = PreviewIndexCoverageTerm | { terms: PreviewIndexCoverageTerm[] };
export const checkPreviewIndexCoverage = (
  schema: string, table: string, request: PreviewIndexCoverageInput, signal?: AbortSignal,
): Promise<{ state: PreviewIndexCoverageState }> =>
  client.post(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/preview-index-coverage`,
    'terms' in request ? { terms: request.terms.map(({ field, operator }) => ({ field, operator })) } :
      { field: request.field, operator: request.operator }, { signal }).then(r => unwrap<{ state: PreviewIndexCoverageState }>(r.data));

export type RowPreviewMode = 'classic' | 'indexed_id_required' | 'indexed_projection_required';
export const fetchRowPreviewPolicy = (schema: string, table: string, signal?: AbortSignal): Promise<{ mode: RowPreviewMode }> =>
  client.get(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/row-preview-policy`, { signal, timeout: 0 })
    .then(r => unwrap<{ mode: RowPreviewMode }>(r.data));

export type IndexedIdSamplePredicate = { idEquals: string } | { idFrom: string; idTo: string };
export type IndexedIdSampleCapability = { available: boolean; keyColumn?: string; limit: 20 };
export type IndexedIdSampleResult = {
  state: 'sampled' | 'blocked'; reason: 'index_path' | 'scope_unavailable' | 'unsupported_plan' | 'query_unavailable';
  ids: string[]; hasMore: boolean;
};
const indexedIdSamplePath = (schema: string, table: string) =>
  `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/indexed-id-sample`;
export const fetchIndexedIdSample = (schema: string, table: string, signal?: AbortSignal): Promise<IndexedIdSampleCapability> =>
  client.get(indexedIdSamplePath(schema, table), { signal }).then(r => unwrap<IndexedIdSampleCapability>(r.data));
export const queryIndexedIdSample = (schema: string, table: string, predicate: IndexedIdSamplePredicate, signal?: AbortSignal): Promise<IndexedIdSampleResult> =>
  client.post(indexedIdSamplePath(schema, table), predicate, { signal }).then(r => unwrap<IndexedIdSampleResult>(r.data));

export type IndexedProjectionField = { name: string; type: 'bigint' | 'boolean' | 'varchar(32)' };
export type IndexedProjectionCapability = { available: boolean; limit: 20; fields?: IndexedProjectionField[]; projectionRevision?: string };
export type IndexedProjectionResult = Omit<IndexedIdSampleResult, 'ids'> & { rows: (string | boolean | null)[][]; fields?: IndexedProjectionField[]; projectionRevision?: string };
const indexedProjectionPath = (schema: string, table: string) =>
  `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/indexed-projection-preview`;
export const fetchIndexedProjectionCapability = (schema: string, table: string, signal?: AbortSignal): Promise<IndexedProjectionCapability> =>
  client.get(indexedProjectionPath(schema, table), { signal }).then(r => unwrap<IndexedProjectionCapability>(r.data));
export const queryIndexedProjection = (schema: string, table: string, predicate: IndexedIdSamplePredicate, expectedProjectionRevision: string, signal?: AbortSignal): Promise<IndexedProjectionResult> =>
  client.post(indexedProjectionPath(schema, table), { ...predicate, expectedProjectionRevision }, { signal }).then(r => unwrap<IndexedProjectionResult>(r.data));
export type IndexedCompoundSamplePredicate = { tenantId: string; groupId: string; idFrom: string; idTo: string };
const indexedCompoundSamplePath = (schema: string, table: string) =>
  `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/indexed-compound-id-sample`;
export const fetchIndexedCompoundSample = (schema: string, table: string, signal?: AbortSignal): Promise<IndexedIdSampleCapability> =>
  client.get(indexedCompoundSamplePath(schema, table), { signal }).then(r => unwrap<IndexedIdSampleCapability>(r.data));
export const queryIndexedCompoundSample = (schema: string, table: string, predicate: IndexedCompoundSamplePredicate, signal?: AbortSignal): Promise<IndexedIdSampleResult> =>
  client.post(indexedCompoundSamplePath(schema, table), predicate, { signal }).then(r => unwrap<IndexedIdSampleResult>(r.data));


export type TableLineageCommonDownstream = Omit<TableLineageCommonUpstream, 'ancestor'> & { descendant: TableLineageIdentity | null };
export const fetchTableLineageCommonDownstream = (source: TableLineageIdentity, target: TableLineageIdentity, signal?: AbortSignal): Promise<TableLineageCommonDownstream> =>
  client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-common-downstream`, {
    params: { targetSchema: target.schema, targetTable: target.table }, signal,
  }).then(r => unwrap<TableLineageCommonDownstream>(r.data));

export type ColumnLineageReferences = {
  direction: TableLineageDirection; column: string; truncated: boolean;
  references: (TableLineageIdentity & { column: string; kinds: TableLineageReferenceKind[] })[];
};
export type ColumnMappingNode = TableLineageIdentity & { column: string };
export type ColumnMappingPaths = {
  status: 'complete' | 'incomplete' | 'unavailable'; direction: TableLineageDirection;
  paths: { nodes: ColumnMappingNode[] }[];
};
export const traceColumnMappingPaths = (scope: ColumnMappingNode, direction: TableLineageDirection, signal?: AbortSignal): Promise<ColumnMappingPaths> =>
  client.get(`/etl/${encodeURIComponent(scope.schema)}/${encodeURIComponent(scope.table)}/lineage-column-mapping-paths`, {
    params: { column: scope.column, direction }, signal,
  }).then(r => unwrap<ColumnMappingPaths>(r.data));
export const fetchColumnLineageReferences = (source: TableLineageIdentity, column: string, direction: TableLineageDirection, signal?: AbortSignal): Promise<ColumnLineageReferences> =>
  client.get(`/etl/${encodeURIComponent(source.schema)}/${encodeURIComponent(source.table)}/lineage-column-references`,
    { params: { column, direction }, signal }).then(r => unwrap<ColumnLineageReferences>(r.data));

export type MinimumRowHealth = {
  enabled: boolean; generation: number; policyGeneration: number | null; minimumRows: number | null;
  state: 'unconfigured' | 'unknown' | 'healthy' | 'below_minimum' | 'superseded'; reason: string | null;
  receiptRecordedAt: string | null; checkedAt: string | null; validUntil: string | null; incident: AssetQualityIncident | null;
};
export type MinimumRowHealthReview = { expectedGeneration: number; policyGeneration: number; minimumRows: number };
const minimumRowHealthPath = `${assetFreshnessPath('ts_transfer_sandbox', 'field_freshness_fixture')}/min-rows`;
export const fetchMinimumRowHealth = (signal?: AbortSignal): Promise<MinimumRowHealth> =>
  client.get(minimumRowHealthPath, { signal }).then(r => unwrap<MinimumRowHealth>(r.data));
export const reviewMinimumRowHealth = (request: MinimumRowHealthReview): Promise<MinimumRowHealth> =>
  client.put(minimumRowHealthPath, request).then(r => unwrap<MinimumRowHealth>(r.data));
export const acknowledgeMinimumRowHealthIncident = (id: string, expectedGeneration: number): Promise<AssetQualityIncident> =>
  client.post(`${minimumRowHealthPath}/incidents/${encodeURIComponent(id)}/acknowledge`, { expectedGeneration }).then(r => unwrap<AssetQualityIncident>(r.data));

export type SandboxHandoffSink = { id: 'synthetic-review-sink'; label: string; generation: string };
export type SandboxHandoffCapability = { available: boolean; sink?: SandboxHandoffSink; copyAvailable?: true; senderProofAvailable?: true };
export type SandboxHandoffEligibility = {
  outputId: string; sink: SandboxHandoffSink; eligible: boolean; reason: 'eligible_for_review' | 'clearance_mismatch'; markings: string[];
};
export const fetchSandboxHandoffCapability = (signal?: AbortSignal): Promise<SandboxHandoffCapability> =>
  client.get(`${sandboxPipelinePath}/handoff`, { signal }).then(r => unwrap<SandboxHandoffCapability>(r.data));
export const checkSandboxHandoffEligibility = (review: {
  outputId: string; expectedConfigHash: string; expectedResultHash: string; expectedSinkGeneration: string;
}, signal?: AbortSignal): Promise<SandboxHandoffEligibility> =>
  client.post(`${sandboxPipelinePath}/handoff/eligibility`, review, { signal }).then(r => unwrap<SandboxHandoffEligibility>(r.data));

const sandboxHandoffRecipientProofPath = `${sandboxPipelinePath}/handoff/recipient-proof`;
export type SandboxHandoffRecipientCapability = { available: boolean; sink?: SandboxHandoffSink };
export type SandboxHandoffRecipientEvidence = {
  id: string; sink: SandboxHandoffSink; markings: string[]; rowsRecorded: number; recordedAt: string;
};
export const fetchSandboxHandoffRecipientCapability = (signal?: AbortSignal): Promise<SandboxHandoffRecipientCapability> =>
  client.get(sandboxHandoffRecipientProofPath, { signal }).then(r => unwrap<SandboxHandoffRecipientCapability>(r.data));
export const verifySandboxHandoffRecipientReceipt = (
  receiptId: string, expectedSinkGeneration: string, signal?: AbortSignal,
): Promise<SandboxHandoffRecipientEvidence> =>
  client.post(sandboxHandoffRecipientProofPath, { receiptId, expectedSinkGeneration }, { signal })
    .then(r => unwrap<SandboxHandoffRecipientEvidence>(r.data));

export type SandboxHandoffCopyRequest = {
  requestId: string; outputId: string; expectedConfigHash: string; expectedResultHash: string; expectedSinkGeneration: string;
};
export type SandboxHandoffCopyReceipt = {
  id: string; requestId: string; outputId: string; sink: SandboxHandoffSink; markings: string[]; rowsCopied: number; recordedAt: string;
};
export const checkSandboxHandoffSenderProof = (request: SandboxHandoffCopyRequest, signal?: AbortSignal): Promise<SandboxHandoffCopyReceipt> =>
  client.post(`${sandboxPipelinePath}/handoff/sender-proof`, request, { signal, _noAutomaticAuthReplay: true } as AxiosRequestConfig & { _noAutomaticAuthReplay: true }).then(r => unwrap<SandboxHandoffCopyReceipt>(r.data));
export const copySandboxHandoff = (request: SandboxHandoffCopyRequest, signal?: AbortSignal): Promise<SandboxHandoffCopyReceipt> =>
  client.post(`${sandboxPipelinePath}/handoff/copies`, request, { signal, _noAutomaticAuthReplay: true } as AxiosRequestConfig & { _noAutomaticAuthReplay: true }).then(r => unwrap<SandboxHandoffCopyReceipt>(r.data));

export type ExplorerStorage = { baseUrl: string; origins: string[]; bucket: string };
export const fetchExplorerStorage = (signal: AbortSignal): Promise<ExplorerStorage> =>
  client.get('/etl/object-storage', { signal }).then(r => unwrap<ExplorerStorage>(r.data));
export const fetchExplorerHtml = (schema: string, table: string, key: Record<string, unknown>, field: string, signal?: AbortSignal): Promise<{ html: string }> =>
  client.post(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/record-html`, { key, field }, { signal }).then(r => unwrap<{ html: string }>(r.data));
export const fetchExplorerFile = (schema: string, table: string, key: Record<string, unknown>, field: string, download = true, signal?: AbortSignal): Promise<{ url: string }> =>
  client.post(`/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/record-file`, { key, field, download }, { signal }).then(r => unwrap<{ url: string }>(r.data));

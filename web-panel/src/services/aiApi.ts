/** AI 采集相关 API */
import client from './api';

// ── Types ──────────────────────────────────────────────────────────────────

export interface FieldDef {
  name: string;
  selector: string;
  type: 'text' | 'number' | 'image' | 'url' | 'date' | 'html';
  sample: string;
  required: boolean;
}

export interface PaginationStrategy {
  type: 'click' | 'scroll' | 'url' | 'none';
  selector?: string;
  maxPages: number;
  params?: Record<string, unknown>;
}

export interface GenerateTemplateRequest {
  url: string;
  options?: {
    maxPages?: number;
    fieldOverrides?: { name: string; rename?: string }[];
  };
}

export interface GenerateTemplateResponse {
  templateId: string;
  name: string;
  domain: string;
  yaml: string;
  fields: FieldDef[];
  pagination: PaginationStrategy;
  createdAt: string;
}

export interface DryRunResponse {
  totalPages: number;
  totalItems: number;
  sampleItems: Record<string, unknown>[];
  columns: string[];
  duration: number;
  errors: string[];
}

export interface AdapterResponse {
  adapterId: string;
  code: string;
  language: string;
  testResult: {
    passed: boolean;
    sampleCount: number;
  };
}

export type PlatformHealthStatus = 'healthy' | 'degraded' | 'inactive';
export type PlatformTaskStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'planned';

export interface PlatformSummary {
  healthScore: number;
  templateCount: number;
  sourceCount: number;
  liveTaskCount: number;
  dataDomainCount: number;
  healthyStageCount: number;
}

export interface PlatformStage {
  key: string;
  title: string;
  accent: string;
  status: PlatformHealthStatus;
  description: string;
  command: string;
  primaryMetric: string;
  secondaryMetric: string;
  badge: string;
  dependencies: string[];
}

export interface PlatformSourceGroup {
  key: string;
  label: string;
  count: number;
  fieldCount: number;
  domains: string[];
  templates: string[];
  updatedAt: string;
}

export interface PlatformTask {
  id: string;
  name: string;
  template: string;
  status: PlatformTaskStatus;
  progress: number;
  records: number;
  startedAt?: string;
  kind: 'live' | 'suggested';
  stage: string;
  mode?: string;
}

export interface PlatformEtlLayer {
  key: string;
  label: string;
  schema: string;
  status: PlatformHealthStatus;
  topicIn: string;
  topicOut: string;
  focus: string;
}

export interface PlatformGuardrail {
  key: string;
  label: string;
  value: string;
  hint: string;
  status: PlatformHealthStatus;
}

export interface PlatformRecommendation {
  title: string;
  detail: string;
  action: string;
  path: string;
  level: 'critical' | 'warning' | 'info';
}

export interface PlatformOverview {
  updatedAt: string;
  summary: PlatformSummary;
  stages: PlatformStage[];
  sources: PlatformSourceGroup[];
  taskBoard: PlatformTask[];
  etlLayers: PlatformEtlLayer[];
  guardrails: PlatformGuardrail[];
  recommendations: PlatformRecommendation[];
}

// ── SSE 事件类型 ────────────────────────────────────────────────────────────

export type SSEEventType =
  | 'thinking'
  | 'step'
  | 'fields'
  | 'pagination'
  | 'complete'
  | 'error';

export interface SSEStepEvent {
  step: string;
  label: string;
  status: 'pending' | 'running' | 'done' | 'error';
  error?: string;
}

export interface SSEFieldsEvent {
  fields: FieldDef[];
}

export interface SSECompleteEvent {
  templateYaml: string;
  templateId: string;
  fields: FieldDef[];
  pagination: PaginationStrategy;
  adapterCode?: string;
}

export interface WorkspaceTemplate {
  id: string;
  name: string;
  version: string;
  title: string;
  domain: string;
  status: 'active' | 'draft' | 'deprecated';
  yaml_content: string;
  adapter: string;
  description: string;
  output_tag: string;
  owner: string;
  metadata: Record<string, unknown>;
  task_count: number;
  updated_at: string;
}

export interface WorkspaceTaskLog {
  level: 'info' | 'ok' | 'warn';
  message: string;
  created_at: string;
}

export interface WorkspaceTask {
  id: string;
  name: string;
  template_name: string;
  template_version: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'paused';
  progress: number;
  records: number;
  throughput: number;
  control_state: 'canceled' | null;
  download_state: 'idle' | 'running' | 'paused';
  sync_state: 'idle' | 'running' | 'canceled';
  schedule: Record<string, unknown>;
  parameters: Record<string, unknown>;
  policies: Record<string, unknown>;
  owner: string;
  created_at: string;
  updated_at: string;
  started_at?: string;
  logs: WorkspaceTaskLog[];
}

export interface WorkspaceTaskPayload {
  name: string;
  template_name: string;
  template_version: string;
  schedule: Record<string, unknown>;
  parameters: Record<string, unknown>;
  policies: Record<string, unknown>;
  owner?: string;
}

export interface WorkspaceReleasePayload {
  name: string;
  version: string;
  title: string;
  domain: string;
  status: 'active' | 'draft' | 'deprecated';
  yaml_content: string;
  adapter: string;
  description?: string;
  output_tag?: string;
  metadata?: Record<string, unknown>;
  task?: WorkspaceTaskPayload;
}

// ── API ────────────────────────────────────────────────────────────────────

/** SSE 流式分析（返回 EventSource，调用方负责关闭） */
export function createAnalyzeStream(url: string): EventSource {
  return new EventSource(
    `/api/ai/analyze-stream?url=${encodeURIComponent(url)}`,
  );
}

/** 生成模板 */
export const generateTemplate = (
  data: GenerateTemplateRequest,
): Promise<GenerateTemplateResponse> =>
  client.post('/ai/generate-template', data).then((r) => r.data);

/** 试采集 */
export const dryRun = (
  templateId: string,
  limit = 20,
): Promise<DryRunResponse> =>
  client.post('/ai/dry-run', { templateId, limit }).then((r) => r.data);

/** 生成适配器 */
export const generateAdapter = (
  url: string,
  siteType = 'default',
): Promise<AdapterResponse> =>
  client.post('/ai/generate-adapter', { url, siteType }).then((r) => r.data);

export const fetchPlatformOverview = (): Promise<PlatformOverview> =>
  client.get('/ai/platform/overview').then((r) => r.data);

export const fetchWorkspaceTemplates = (): Promise<WorkspaceTemplate[]> =>
  client.get('/ai/workspace/templates').then((r) => r.data.items);

export const updateWorkspaceTemplate = (
  templateId: string,
  data: Pick<WorkspaceTemplate, 'yaml_content' | 'adapter' | 'description' | 'output_tag'>,
): Promise<WorkspaceTemplate> =>
  client.put(`/ai/workspace/templates/${templateId}`, data).then((r) => r.data);

export const releaseWorkspaceTemplate = (
  data: WorkspaceReleasePayload,
): Promise<{ template: WorkspaceTemplate; task: WorkspaceTask | null }> =>
  client.post('/ai/workspace/templates/release', data).then((r) => r.data);

export const fetchWorkspaceTasks = (): Promise<WorkspaceTask[]> =>
  client.get('/ai/workspace/tasks').then((r) => r.data.items);

export const createWorkspaceTask = (data: WorkspaceTaskPayload): Promise<WorkspaceTask> =>
  client.post('/ai/workspace/tasks', data).then((r) => r.data);

export const runWorkspaceTaskAction = (
  taskId: string,
  action: 'pause' | 'resume' | 'cancel' | 'start_download' | 'pause_download' | 'start_sync' | 'cancel_sync',
): Promise<WorkspaceTask> =>
  client.post(`/ai/workspace/tasks/${taskId}/action`, { action }).then((r) => r.data);

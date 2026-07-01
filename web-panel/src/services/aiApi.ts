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

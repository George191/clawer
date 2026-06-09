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

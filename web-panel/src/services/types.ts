/* eslint-disable @typescript-eslint/no-explicit-any */
// ==================== Dashboard ====================
export interface DashboardMetrics {
  tasks: {
    total: number;
    running: number;
    completed: number;
    failed: number;
  };
  etl_throughput: {
    current: number;
    history: { ts: string; v: number }[];
  };
  kafka_lag: {
    total: number;
    by_layer: Record<string, number>;
  };
  data_volume: {
    total: number;
    daily_increment: number;
  };
  /** Pipeline topology nodes */
  pipeline_nodes: PipelineNodeData[];
  /** Per-layer throughput history for stacked area chart */
  layer_throughput_history: LayerThroughputPoint[];
  /** Task status distribution (running/completed/failed/queued) */
  task_status_dist: { name: string; value: number }[];
  /** Error rate time series */
  error_rate_history: { ts: string; v: number }[];
  /** Error rate threshold % */
  error_threshold: number;
  /** Kafka lag time series for trend chart */
  kafka_lag_history: { ts: string; v: number }[];
}

/** Pipeline topology node */
export interface PipelineNodeData {
  name: string;
  status: 'running' | 'stopped' | 'error';
  throughput: number;
  lag: number;
}

/** Multi-layer throughput time-series point */
export interface LayerThroughputPoint {
  time: string;
  Crawl: number;
  RDS: number;
  ODS: number;
  TASK: number;
  DWD: number;
  DWS: number;
  ADS: number;
}

// ==================== ETL Layers ====================
export interface LayerNode {
  key: string;
  label: string;
  icon: string;
  status: 'running' | 'stopped' | 'error';
  rateIn: number;
  rateOut: number;
  lag: number;
  tables?: number;
}

export interface LayerTable {
  name: string;
  schemaName?: string;
  tableRole?: 'current' | 'history';
  partitioned?: boolean;
  rowCount: number;
  size: string;
  updatedAt: string;
}

// ==================== Tasks ====================
export interface TaskInfo {
  id: string;
  template: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'paused';
  progress: number;
  records: number;
  startedAt: string;
  duration: string;
}

export interface TemplateInfo {
  name: string;
  type: string;
  description: string;
  status: 'active' | 'inactive';
  fields: number;
  steps: number;
}

// ==================== Kafka / Redis ====================
export interface KafkaTopic {
  name: string;
  partitions: number;
  messages: number;
  lag: number;
}

export interface RedisOffset {
  key: string;
  offset: number;
  updatedAt: string;
}

// ==================== Monitoring ====================
export interface LogEntry {
  timestamp: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG';
  source: string;
  message: string;
}

export interface MonitorStats {
  reqRate: number;
  successRate: number;
  antiCrawlTriggers: number;
  proxyAvailable: number;
  proxyTotal: number;
}

// ==================== Scheduler ====================
export interface SchedulerItem {
  id: string;
  template: string;
  cron: string;
  nextRun: string;
  status: string;
}

// ==================== Query ====================
export interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number;
  elapsed: number;
}

export interface HandlerCode {
  layer: string;
  table: string;
  code: string;
  updatedAt: string;
}

// ==================== Alerts ====================
export interface Alert {
  id: string;
  level: 'critical' | 'warning' | 'info';
  source: string;
  message: string;
  time: string;
  status: 'active' | 'resolved';
}

// ==================== AI Collect ====================
export interface FieldOverride {
  name: string;
  rename?: string;
}

export interface GenerateOptions {
  maxPages?: number;
  fieldOverrides?: FieldOverride[];
}

export interface GenerateTemplateRequest {
  url: string;
  options?: GenerateOptions;
}

export interface UrlPreflightRequest {
  url: string;
}

export interface DryRunRequest {
  templateId: string;
  limit?: number;
}

export interface GenerateAdapterRequest {
  url: string;
  siteType?: string;
  templateId?: string;
}

export interface WorkspaceTemplateUpdateRequest {
  yaml_content: string;
  adapter?: string;
  description?: string;
}

export interface WorkspaceTaskRequest {
  name: string;
  template_name: string;
  template_version?: string;
  schedule?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  policies?: Record<string, unknown>;
  owner?: string;
}

export interface WorkspaceReleaseRequest {
  analysisId?: string;
  name: string;
  version?: string;
  title: string;
  domain?: string;
  favicon_url?: string;
  status: string;
  yaml_content: string;
  adapter?: string;
  description?: string;
  metadata?: Record<string, unknown>;
  task?: WorkspaceTaskRequest;
}

export interface WorkspaceTaskActionRequest {
  action: string;
}

export interface AnalysisField {
  name: string;
  selector: string;
  type: string;
  sample?: string;
  required?: boolean;
}

export interface PaginationInfo {
  type: string;
  selector?: string;
  maxPages: number;
  params?: Record<string, unknown>;
}

export interface AnalysisResult {
  templateId: string;
  name: string;
  domain: string;
  yaml: string;
  adapter: string;
  adapterPath: string;
  fields: AnalysisField[];
  pagination: PaginationInfo;
  sampleItems: Record<string, unknown>[];
  warnings: string[];
  acquisition: Record<string, unknown>;
  agent: Record<string, unknown>;
  createdAt: string;
}

export interface DryRunResult {
  totalPages: number;
  totalItems: number;
  sampleItems: Record<string, unknown>[];
  columns: string[];
  duration: number;
  errors: string[];
}

export interface WorkspaceTemplate {
  id: string;
  name: string;
  version: string;
  title: string;
  domain: string;
  data_type?: string;
  template?: string;
  template_path?: string;
  icon?: string;
  status: 'active' | 'draft' | 'deprecated';
  adapter: string;
  description: string;
  owner: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  yaml_content?: string;
  favicon_url?: string;
  task_count?: number;
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
  control_state?: string;
  download_state: string;
  sync_state: string;
  schedule: Record<string, unknown>;
  parameters: Record<string, unknown>;
  policies: Record<string, unknown>;
  owner: string;
  created_at: string;
  updated_at: string;
  started_at?: string;
  logs?: { level: string; message: string; created_at: string }[];
}

export interface EtlPartition { name: string; }

export interface EtlStreamState {
  available: boolean;
  reason?: string;
  consumerGroup?: string;
  topic?: string;
  offsets?: { partition: number; offset: number }[];
  throughput: number | null;
  throughputReason?: string;
}

export interface EtlScript {
  available: boolean;
  reason?: string;
  path?: string;
  language?: string;
  code: string;
}

export type ProductDomain = 'ai-collect' | 'data-lake' | 'etl-pipeline' | 'data-cockpit' | 'knowledge-graph' | 'knowledge-rag' | 'platform';

export interface CurrentUserContext {
  user: { id: string; full_name?: string | null; email: string };
  tenants: Array<{ id: string; name: string }>;
  teams: Array<{ id: string; name: string; tenant_id: string }>;
}

export interface AutomationWorkflow {
  id?: number;
  name: string;
  product_domain: ProductDomain;
  description: string;
  nodes: Array<{ name: string; type?: string; template?: string }>;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SchedulerTaskConfig {
  id?: number;
  task_name: string;
  task_path: string;
  product_domain: ProductDomain;
  description?: string;
  schedule_type: 'crontab' | 'interval';
  cron_minute: string;
  cron_hour: string;
  cron_day_of_week: string;
  cron_day_of_month: string;
  cron_month_of_year: string;
  interval_seconds?: number;
  args: unknown[];
  kwargs: Record<string, unknown>;
  options: Record<string, unknown>;
  enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface AdapterResult {
  adapterId: string;
  code: string;
  language: string;
  testResult: { passed: boolean; sampleCount: number };
}

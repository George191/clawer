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
  rate: number;
  lag: number;
}

export interface LayerTable {
  name: string;
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

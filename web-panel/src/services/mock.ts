import type {
  DashboardMetrics,
  PipelineNodeData,
  LayerThroughputPoint,
  LayerNode,
  LayerTable,
  TaskInfo,
  TemplateInfo,
  Alert,
  LogEntry,
  MonitorStats,
  KafkaTopic,
  RedisOffset,
  QueryResult,
  HandlerCode,
  SchedulerItem,
} from './types';

// ==================== Helpers ====================

function rand(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randFloat(min: number, max: number, decimals = 2): number {
  return parseFloat((Math.random() * (max - min) + min).toFixed(decimals));
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function now(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function hoursAgo(n: number): string {
  const d = new Date(Date.now() - n * 3600_000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// ==================== mockDashboardMetrics ====================

export function mockDashboardMetrics(): DashboardMetrics {
  const now = new Date();
  const history: { ts: string; v: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    const t = new Date(now.getTime() - i * 3600_000);
    const hh = String(t.getHours()).padStart(2, '0');
    const mm = String(t.getMinutes()).padStart(2, '0');
    history.push({ ts: `${hh}:${mm}`, v: randFloat(200, 600) });
  }

  const layers = ['ods', 'dwd', 'dws', 'ads', 'dim'];
  const by_layer: Record<string, number> = {};
  let totalLag = 0;
  for (const l of layers) {
    const v = rand(0, 500);
    by_layer[l] = v;
    totalLag += v;
  }

  // Pipeline topology nodes
  const pipelineNodes: PipelineNodeData[] = [
    { name: 'Crawl', status: 'running', throughput: randFloat(1000, 1500), lag: rand(0, 50) },
    { name: 'RDS', status: 'running', throughput: randFloat(1000, 1400), lag: rand(50, 200) },
    { name: 'ODS', status: 'running', throughput: randFloat(900, 1300), lag: rand(150, 450) },
    { name: 'TASK', status: 'running', throughput: randFloat(800, 1200), lag: rand(300, 600) },
    { name: 'DWD', status: 'running', throughput: randFloat(700, 1000), lag: rand(100, 300) },
    { name: 'DWS', status: 'running', throughput: randFloat(600, 900), lag: rand(40, 150) },
    { name: 'ADS', status: 'running', throughput: randFloat(500, 800), lag: rand(20, 80) },
  ];

  // Layer throughput history (24 points, 7 series)
  const layerHistory: LayerThroughputPoint[] = [];
  for (let i = 23; i >= 0; i--) {
    const t = new Date(now.getTime() - i * 3600_000);
    const hh = String(t.getHours()).padStart(2, '0');
    const mm = String(t.getMinutes()).padStart(2, '0');
    layerHistory.push({
      time: `${hh}:${mm}`,
      Crawl: randFloat(900, 1300),
      RDS: randFloat(850, 1200),
      ODS: randFloat(800, 1150),
      TASK: randFloat(700, 1050),
      DWD: randFloat(600, 950),
      DWS: randFloat(500, 850),
      ADS: randFloat(400, 750),
    });
  }

  // Error rate history
  const errorHistory: { ts: string; v: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    const t = new Date(now.getTime() - i * 3600_000);
    const hh = String(t.getHours()).padStart(2, '0');
    const mm = String(t.getMinutes()).padStart(2, '0');
    errorHistory.push({ ts: `${hh}:${mm}`, v: randFloat(0.2, 2.8) });
  }

  // Kafka lag history
  const lagHistory: { ts: string; v: number }[] = [];
  for (let i = 23; i >= 0; i--) {
    const t = new Date(now.getTime() - i * 3600_000);
    const hh = String(t.getHours()).padStart(2, '0');
    const mm = String(t.getMinutes()).padStart(2, '0');
    lagHistory.push({ ts: `${hh}:${mm}`, v: rand(800, 1800) });
  }

  return {
    tasks: {
      total: rand(80, 200),
      running: rand(5, 20),
      completed: rand(50, 150),
      failed: rand(0, 5),
    },
    etl_throughput: {
      current: randFloat(200, 600),
      history,
    },
    kafka_lag: {
      total: totalLag,
      by_layer,
    },
    data_volume: {
      total: randFloat(0.5, 10, 2),
      daily_increment: randFloat(0.01, 0.5, 2),
    },
    pipeline_nodes: pipelineNodes,
    layer_throughput_history: layerHistory,
    task_status_dist: [
      { name: '运行中', value: rand(15, 30) },
      { name: '已完成', value: rand(80, 150) },
      { name: '失败', value: rand(0, 8) },
      { name: '排队中', value: rand(10, 25) },
    ],
    error_rate_history: errorHistory,
    error_threshold: 3.0,
    kafka_lag_history: lagHistory,
  };
}

// ==================== mockLayers ====================

export function mockLayers(): LayerNode[] {
  const layerDefs: Omit<LayerNode, 'rate' | 'lag'>[] = [
    { key: 'ods', label: 'ODS 操作数据层', icon: 'DatabaseOutlined', status: 'running' },
    { key: 'dwd', label: 'DWD 明细数据层', icon: 'TableOutlined', status: 'running' },
    { key: 'dws', label: 'DWS 汇总数据层', icon: 'BarChartOutlined', status: 'running' },
    { key: 'ads', label: 'ADS 应用数据层', icon: 'DashboardOutlined', status: 'running' },
    { key: 'dim', label: 'DIM 维度层', icon: 'AppstoreOutlined', status: 'running' },
    { key: 'tmp', label: 'TMP 临时层', icon: 'FolderOutlined', status: pick(['stopped', 'running'] as const) },
    { key: 'raw', label: 'RAW 原始数据层', icon: 'CloudOutlined', status: pick(['running', 'error'] as const) },
  ];
  return layerDefs.map((d) => ({
    ...d,
    rate: randFloat(0.1, 20, 1),
    lag: rand(0, 500),
    status: d.status,
  }));
}

// ==================== mockLayerTables ====================

export function mockLayerTables(layer: string): LayerTable[] {
  const tableNames: Record<string, string[]> = {
    ods: ['web_page', 'api_response', 'app_log', 'iot_event', 'third_party_feed'],
    dwd: ['user_behavior', 'order_detail', 'content_detail', 'payment_flow'],
    dws: ['user_profile', 'daily_stats', 'weekly_trend', 'regional_summary'],
    ads: ['report_sales', 'dashboard_kpi', 'user_insight'],
    dim: ['user', 'product', 'region', 'time'],
    tmp: ['temp_join_1', 'temp_agg_2'],
    raw: ['raw_kafka_log', 'raw_mongo_dump', 'raw_api_snapshot', 'raw_file_ingest', 'raw_cdc_stream'],
  };

  const names = tableNames[layer] ?? [`${layer}_table_1`, `${layer}_table_2`, `${layer}_table_3`];
  return names.map((name) => ({
    name,
    rowCount: rand(100, 50_000_000),
    size: pick(['128 MB', '2.3 GB', '45 GB', '512 KB', '1.8 TB', '340 MB', '12 GB', '890 MB']),
    updatedAt: hoursAgo(rand(0, 72)),
  }));
}

// ==================== mockTasks ====================

export function mockTasks(count: number): TaskInfo[] {
  const statuses: TaskInfo['status'][] = ['queued', 'running', 'completed', 'failed', 'paused'];
  const templates = ['网页采集', 'API采集', '日志采集', '消息采集', '数据清洗'];
  const result: TaskInfo[] = [];
  for (let i = 0; i < count; i++) {
    const status = pick(statuses);
    const isActive = status === 'running' || status === 'completed';
    result.push({
      id: `task-${String(i + 1).padStart(3, '0')}`,
      template: pick(templates),
      status,
      progress: status === 'completed' ? 100 : status === 'queued' ? 0 : rand(1, 99),
      records: status === 'queued' ? 0 : rand(100, 500_000),
      startedAt: isActive ? hoursAgo(rand(1, 48)) : '-',
      duration: isActive ? `${rand(1, 120)}m ${rand(0, 59)}s` : '-',
    });
  }
  return result;
}

// ==================== mockTemplates ====================

export function mockTemplates(): TemplateInfo[] {
  return [
    { name: '网页采集模板', type: '采集', description: '通用网页数据采集，支持翻页和反爬', status: 'active', fields: 12, steps: 5 },
    { name: 'API数据同步', type: '采集', description: '基于 OpenAPI 的增量数据同步', status: 'active', fields: 8, steps: 4 },
    { name: '日志清洗管道', type: '清洗', description: 'Nginx/CDN 日志结构化清洗', status: 'active', fields: 15, steps: 6 },
    { name: '数据质量校验', type: '校验', description: '全链路数据质量监控与校验', status: 'active', fields: 10, steps: 3 },
    { name: '旧版消息采集', type: '采集', description: '已废弃，请使用新版', status: 'inactive', fields: 6, steps: 2 },
  ];
}

// ==================== mockAlerts ====================

export function mockAlerts(count: number): Alert[] {
  const alertPool: Omit<Alert, 'id' | 'time'>[] = [
    { level: 'critical', source: 'Kafka Monitor', message: 'Broker 节点 2 离线超过 5 分钟', status: 'active' },
    { level: 'critical', source: 'Pipeline Monitor', message: 'ODS → DWD 数据管道中断', status: 'active' },
    { level: 'warning', source: 'Crawl Monitor', message: '代理池可用率降至 60%', status: 'active' },
    { level: 'warning', source: 'ETL Monitor', message: 'DWD 用户画像任务耗时超阈值', status: 'active' },
    { level: 'warning', source: 'Redis Monitor', message: '内存使用率超过 80%', status: 'active' },
    { level: 'info', source: 'System', message: '模板更新通知：微博采集模板 v2.1 已发布', status: 'resolved' },
    { level: 'info', source: 'Scheduler', message: '每日凌晨备份任务执行成功', status: 'resolved' },
    { level: 'critical', source: 'DB Monitor', message: 'MySQL 主从同步延迟 > 10s', status: 'active' },
    { level: 'warning', source: 'ETL Monitor', message: 'ADS 报表产出延迟 15 分钟', status: 'active' },
    { level: 'info', source: 'Deploy', message: '新版 handler 代码已上线 ODS 层', status: 'resolved' },
  ];
  return alertPool.slice(0, Math.min(count, alertPool.length)).map((a, i) => ({
    ...a,
    id: `alert-${String(i + 1).padStart(3, '0')}`,
    time: hoursAgo(rand(0, 24)),
  }));
}

// ==================== mockLogs ====================

export function mockLogs(count: number): LogEntry[] {
  const sources = ['crawler', 'etl-worker', 'kafka-consumer', 'api-gateway', 'scheduler', 'proxy-pool'];
  const messages: Record<string, string[]> = {
    crawler: [
      '正在采集第 3 页...',
      '请求成功，获取 120 条记录',
      '命中反爬验证码，自动切换代理',
      '页面解析完成，耗时 2.3s',
      'cookie 已过期，正在刷新',
    ],
    'etl-worker': [
      '开始处理批次 #A12F，共 5000 条',
      'transform 阶段完成，耗时 1.8s',
      '写入 ODS 层完成，5000 rows affected',
      '字段映射异常，跳过 3 条脏数据',
    ],
    'kafka-consumer': [
      '消费主题 raw.web_page，offset=2450100',
      '消费者组 rebalance 完成，分配 2 个分区',
    ],
    'api-gateway': [
      '接收到查询请求 GET /api/query',
      '限流触发，请求被拒绝，IP=10.0.1.45',
    ],
    scheduler: [
      '调度任务 task-042 启动',
      'cron 表达式解析通过: 0 */4 * * *',
    ],
    'proxy-pool': [
      '代理池检测完成：可用 45/50',
      '剔除慢速代理 proxy-17，延迟 8.5s',
    ],
  };

  const baseTime = new Date();
  const result: LogEntry[] = [];
  for (let i = 0; i < count; i++) {
    const t = new Date(baseTime.getTime() - (count - i) * rand(30, 120) * 1000);
    const pad = (n: number) => String(n).padStart(2, '0');
    const ts = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    const source = pick(sources);
    const level = pick(['INFO', 'INFO', 'INFO', 'WARN', 'ERROR', 'DEBUG'] as const);
    const pool = messages[source] ?? ['操作完成'];
    result.push({
      timestamp: ts,
      level,
      source,
      message: pick(pool),
    });
  }
  return result;
}

// ==================== mockKafkaTopics ====================

export function mockKafkaTopics(): KafkaTopic[] {
  return [
    { name: 'raw.web_page', partitions: 12, messages: rand(10000, 500000), lag: rand(0, 200) },
    { name: 'raw.api_response', partitions: 6, messages: rand(5000, 200000), lag: rand(0, 150) },
    { name: 'raw.app_log', partitions: 8, messages: rand(20000, 1000000), lag: rand(0, 300) },
    { name: 'ods.web_page', partitions: 12, messages: rand(10000, 400000), lag: rand(0, 100) },
    { name: 'ods.api_response', partitions: 6, messages: rand(5000, 150000), lag: rand(0, 80) },
    { name: 'dwd.user_behavior', partitions: 4, messages: rand(10000, 300000), lag: rand(0, 50) },
    { name: 'dws.user_profile', partitions: 3, messages: rand(5000, 100000), lag: rand(0, 30) },
  ];
}

// ==================== mockRedisOffsets ====================

export function mockRedisOffsets(): RedisOffset[] {
  const keys = [
    'consumer:web_page:offset',
    'consumer:api_response:offset',
    'consumer:app_log:offset',
    'consumer:user_behavior:offset',
    'consumer:order_detail:offset',
    'consumer:content_detail:offset',
    'consumer:daily_stats:offset',
  ];
  return keys.map((key) => ({
    key,
    offset: rand(100000, 99999999),
    updatedAt: hoursAgo(rand(0, 6)),
  }));
}

// ==================== mockMonitorStats ====================

export function mockMonitorStats(): MonitorStats {
  return {
    reqRate: randFloat(10, 200, 1),
    successRate: randFloat(90, 99.9, 1),
    antiCrawlTriggers: rand(0, 50),
    proxyAvailable: rand(30, 80),
    proxyTotal: rand(50, 100),
  };
}

// ==================== mockSchedulerQueue ====================

export function mockSchedulerQueue(count: number): SchedulerItem[] {
  const templates = ['网页采集', 'API采集', '日志采集', '数据清洗', '质量校验', '报表生成'];
  const statuses = ['scheduled', 'running', 'idle'];
  const result: SchedulerItem[] = [];
  for (let i = 0; i < count; i++) {
    const cronExps = ['0 */2 * * *', '0 */4 * * *', '0 */6 * * *', '0 2 * * *', '0 8 * * 1-5', '*/30 * * * *'];
    result.push({
      id: `sched-${String(i + 1).padStart(3, '0')}`,
      template: pick(templates),
      cron: pick(cronExps),
      nextRun: hoursAgo(rand(-24, 24)),
      status: pick(statuses),
    });
  }
  return result;
}

// ==================== mockQueryResult ====================

export function mockQueryResult(_sql: string): QueryResult {
  const rowCount = rand(2, 100);
  const columns = ['id', 'user_id', 'event', 'page', 'timestamp', 'duration_ms', 'source', 'ip'];
  const events = ['page_view', 'click', 'scroll', 'add_cart', 'purchase', 'login', 'logout', 'search'];
  const pages = ['/home', '/product/123', '/checkout', '/profile', '/search?q=test', '/category/electronics'];
  const sources = ['web', 'mobile', 'api', 'miniapp'];

  const rows: Record<string, unknown>[] = [];
  for (let i = 0; i < rowCount; i++) {
    rows.push({
      id: i + 1,
      user_id: `U${String(rand(10000, 99999))}`,
      event: pick(events),
      page: pick(pages),
      timestamp: hoursAgo(rand(0, 48)),
      duration_ms: rand(5, 10000),
      source: pick(sources),
      ip: `192.168.${rand(1, 255)}.${rand(1, 255)}`,
    });
  }

  return {
    columns,
    rows,
    rowCount,
    elapsed: randFloat(0.01, 5, 3),
  };
}

// ==================== mockHandlerCode ====================

export function mockHandlerCode(layer: string, table: string): HandlerCode {
  const code = `"""
${layer}.${table} - ETL Handler
Generated for ${layer.toUpperCase()} layer
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from etl_core import BaseHandler, register, Context
from etl_core.schema import Field, SchemaRegistry
from etl_core.exceptions import DataQualityException


@register(layer="${layer}", table="${table}")
class ${toPascal(table)}Handler(BaseHandler):
    """Handle ETL pipeline for ${layer}.${table}"""

    # ── Schema ──────────────────────────────────
    SCHEMA = SchemaRegistry.get("${layer}.${table}")
${fieldExamples(layer, table)}

    # ── Lifecycle ───────────────────────────────

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.batch_size = ctx.config.get("batch_size", 1000)
        self.retry_max = ctx.config.get("retry_max", 3)

    async def extract(self, ctx: Context) -> List[Dict[str, Any]]:
        """Extract raw data from source"""
        batch = await ctx.kafka.consume(
            topic="raw.${table}",
            max_records=self.batch_size,
            timeout_ms=30_000,
        )
        ctx.logger.info(f"[extract] consumed {len(batch)} records from raw.${table}")
        return batch

    async def transform(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform and validate records"""
        result: List[Dict[str, Any]] = []
        for r in records:
            try:
                item = self._transform_one(r)
                self._validate(item)
                result.append(item)
            except DataQualityException as e:
                ctx.metrics.inc("transform.skipped")
                ctx.logger.warning(f"跳过脏数据: {e}")
        ctx.metrics.inc("transform.processed", len(result))
        return result

    async def load(self, ctx: Context, rows: List[Dict[str, Any]]) -> None:
        """Load transformed data into target storage"""
        async with ctx.pg.pool.acquire() as conn:
            await conn.copy_records_to_table(
                "${layer}.${table}",
                records=rows,
                timeout=60,
            )
        ctx.logger.info(f"[load] 写入 {len(rows)} 条 → ${layer}.${table}")

    # ── Private Helpers ─────────────────────────

    def _transform_one(${_transformSignature(layer)}):
${_transformBody(layer)}

    def _validate(self, item: Dict[str, Any]) -> None:
        """Validate required fields"""
        required = [${requiredFields(layer)}]
        for f in required:
            if f not in item or item[f] is None:
                raise DataQualityException(f"缺少必填字段: {f}")`;

  return { layer, table, code, updatedAt: now() };
}

// ── Handler code helpers ──

function toPascal(s: string): string {
  return s
    .split(/[_\s-]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join('');
}

function fieldExamples(layer: string, _table: string): string {
  if (layer === 'ods') {
    return `    FIELDS = [
        Field("url", "string", nullable=False),
        Field("raw_content", "text", nullable=True),
        Field("crawl_ts", "datetime", nullable=False),
        Field("source", "string", nullable=False, default="web"),
    ]`;
  }
  return `    FIELDS = [
        Field("event_id", "string", nullable=False),
        Field("user_id", "string", nullable=False),
        Field("event_type", "string", nullable=False),
        Field("page_url", "string", nullable=True),
        Field("duration_ms", "int", nullable=True),
        Field("created_at", "datetime", nullable=False),
    ]`;
}

function _transformSignature(layer: string): string {
  return layer === 'ods'
    ? 'self, r: Dict[str, Any]) -> Dict[str, Any]'
    : 'self, r: Dict[str, Any]) -> Dict[str, Any]';
}

function _transformBody(layer: string): string {
  if (layer === 'ods') {
    return `        return {
            "url": r.get("url", "").strip(),
            "raw_content": r.get("content", ""),
            "crawl_ts": r.get("crawl_ts", datetime.utcnow()),
            "source": r.get("source", "unknown"),
        }`;
  }
  return `        return {
            "event_id": r.get("event_id", ""),
            "user_id": r.get("user_id", ""),
            "event_type": r.get("event_type", "unknown"),
            "page_url": r.get("page_url"),
            "duration_ms": r.get("duration_ms"),
            "created_at": r.get("created_at", datetime.utcnow()),
        }`;
}

function requiredFields(layer: string): string {
  if (layer === 'ods') return '"url", "crawl_ts"';
  return '"event_id", "user_id", "created_at"';
}

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Input, Segmented, Select, Space, Tag, Tooltip, Typography } from 'antd';
import {
  ArrowRightOutlined,
  CaretDownOutlined,
  CheckCircleOutlined,
  ClearOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
  FilterOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  SearchOutlined,
  BranchesOutlined,
  ThunderboltOutlined,
  VerticalAlignBottomOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

/* ──────────────────────────────────────────────
 * Aura 色板
 * ────────────────────────────────────────────── */
const aura = {
  bg: '#0E1116',
  terminal: '#0A0D11',
  surface: '#1A1F25',
  surfaceSoft: '#151A1F',
  panel: '#161B21',
  row: '#14181D',
  rowAlt: '#11151A',
  border: '#2A323C',
  borderSoft: '#222830',
  text: '#E8EAED',
  muted: '#8B95A1',
  subtle: '#5C6573',
  purple: '#8B5CF6',
  cyan: '#8FE3E8',
  green: '#31D26B',
  amber: '#FBBF24',
  danger: '#F87171',
  blue: '#60A5FA',
  gray: '#6B7280',
};

/* ──────────────────────────────────────────────
 * 日志等级配色
 * ────────────────────────────────────────────── */
type LogLevel = 'INFO' | 'WARN' | 'ERROR' | 'DEBUG' | 'TRACE';

const levelMeta: Record<LogLevel, { color: string; bg: string; label: string }> = {
  INFO:  { color: aura.blue,   bg: 'rgba(96,165,250,0.08)',  label: 'INFO'  },
  WARN:  { color: aura.amber,  bg: 'rgba(251,191,36,0.08)',  label: 'WARN'  },
  ERROR: { color: aura.danger, bg: 'rgba(248,113,113,0.10)', label: 'ERROR' },
  DEBUG: { color: aura.gray,   bg: 'rgba(107,114,128,0.08)', label: 'DEBUG' },
  TRACE: { color: aura.purple, bg: 'rgba(139,92,246,0.08)',  label: 'TRACE' },
};

/* ──────────────────────────────────────────────
 * 服务列表
 * ────────────────────────────────────────────── */
type ProductLine = 'ai-collect' | 'sync' | 'etl' | 'governance' | 'schedule' | 'service';

interface ServiceDef {
  name: string;
  line: ProductLine;
  desc: string;
}

const services: ServiceDef[] = [
  // AI 采集线
  { name: 'ai-agent',       line: 'ai-collect', desc: 'AI 智能采集引擎' },
  { name: 'crawler-engine', line: 'ai-collect', desc: '爬虫调度引擎' },
  { name: 'browser-render', line: 'ai-collect', desc: '浏览器渲染池' },
  { name: 'identity-pool',  line: 'ai-collect', desc: '身份代理池' },
  // 同步层
  { name: 'writer-rds',     line: 'sync', desc: 'RDS 写入器' },
  { name: 'writer-ods',     line: 'sync', desc: 'ODS 写入器' },
  { name: 'syncer',         line: 'sync', desc: '数据同步引擎' },
  // ETL 层
  { name: 'etl-ods',        line: 'etl', desc: 'ODS→DWD 加工' },
  { name: 'etl-dwd',        line: 'etl', desc: 'DWD→DWS 加工' },
  { name: 'etl-dws',        line: 'etl', desc: 'DWS→ADS 加工' },
  // 治理层
  { name: 'quality-engine', line: 'governance', desc: '质量规则引擎' },
  { name: 'metadata-collector', line: 'governance', desc: '元数据采集' },
  { name: 'lineage-parser', line: 'governance', desc: '血缘解析器' },
  // 调度层
  { name: 'scheduler',      line: 'schedule', desc: '任务调度器' },
  { name: 'dag-engine',     line: 'schedule', desc: 'DAG 执行引擎' },
  // 服务层
  { name: 'api-gateway',    line: 'service', desc: 'API 网关' },
  { name: 'api-cache',      line: 'service', desc: 'API 缓存层' },
];

const productLineMeta: Record<ProductLine, { label: string; icon: string; color: string }> = {
  'ai-collect':  { label: 'AI 智能采集', icon: '🤖', color: aura.purple },
  'sync':        { label: '数据同步',     icon: '🔄', color: aura.blue },
  'etl':         { label: 'ETL 加工',     icon: '⚡', color: aura.cyan },
  'governance':  { label: '数据治理',     icon: '🛡', color: aura.green },
  'schedule':    { label: '任务调度',     icon: '📅', color: aura.amber },
  'service':     { label: '数据服务',     icon: '🔗', color: '#F97316' },
};

/* ──────────────────────────────────────────────
 * Mock 日志数据
 * ────────────────────────────────────────────── */
interface LogEntry {
  id: string;
  timestamp: string;
  epoch: number;
  level: LogLevel;
  service: string;
  traceId: string;
  spanId: string;
  message: string;
  labels: string[];
  duration?: string;
  context?: Record<string, string>;
}

const logs: LogEntry[] = [
  { id: 'l01', timestamp: '18:24:12.842', epoch: 1718499852.842, level: 'INFO',  service: 'scheduler',      traceId: 'trc-82f1', spanId: 'sp-001', message: 'DAG google_patent_daily triggered by cron schedule', labels: ['dag', 'cron'], duration: '0ms', context: { dag_id: 'google_patent_daily', trigger: 'cron' } },
  { id: 'l02', timestamp: '18:24:12.914', epoch: 1718499852.914, level: 'INFO',  service: 'dag-engine',     traceId: 'trc-82f1', spanId: 'sp-002', message: 'task [fetch_list] started, target: patents.google.com/patents', labels: ['dag', 'fetch'], duration: '2ms' },
  { id: 'l03', timestamp: '18:24:13.108', epoch: 1718499853.108, level: 'INFO',  service: 'ai-agent',       traceId: 'trc-82f1', spanId: 'sp-003', message: 'schema contract generated for google_patent_contract', labels: ['template', 'contract'], duration: '194ms', context: { template: 'google_patent_contract@v1.8', fields: '18' } },
  { id: 'l04', timestamp: '18:24:13.412', epoch: 1718499853.412, level: 'DEBUG', service: 'crawler-engine',  traceId: 'trc-82f1', spanId: 'sp-004', message: 'HTTP GET https://patents.google.com/patents?page=1 → 200 OK (1.2KB)', labels: ['fetch', 'http'], duration: '304ms' },
  { id: 'l05', timestamp: '18:24:13.821', epoch: 1718499853.821, level: 'INFO',  service: 'browser-render',  traceId: 'trc-82f1', spanId: 'sp-005', message: 'page rendered, 42 items detected on list page', labels: ['render', 'detect'], duration: '409ms' },
  { id: 'l06', timestamp: '18:24:14.503', epoch: 1718499854.503, level: 'INFO',  service: 'crawler-engine',  traceId: 'trc-82f1', spanId: 'sp-006', message: 'detail page batch fetched: 42/42 success', labels: ['fetch', 'batch'], duration: '682ms' },
  { id: 'l07', timestamp: '18:24:15.103', epoch: 1718499855.103, level: 'WARN',  service: 'quality-engine',  traceId: 'trc-82f1', spanId: 'sp-007', message: 'abstract missing rate reached 1.7%, threshold 1.0%', labels: ['quality', 'field_missing'], duration: '120ms', context: { field: 'abstract', missing_rate: '1.7%', threshold: '1.0%' } },
  { id: 'l08', timestamp: '18:24:15.642', epoch: 1718499855.642, level: 'INFO',  service: 'writer-ods',      traceId: 'trc-82f1', spanId: 'sp-008', message: 'batch committed to ods_patent.raw_page, 42 records', labels: ['ods', 'commit'], duration: '539ms', context: { table: 'ods_patent.raw_page', rows: '42' } },
  { id: 'l09', timestamp: '18:24:16.021', epoch: 1718499856.021, level: 'TRACE', service: 'etl-ods',        traceId: 'trc-82f1', spanId: 'sp-009', message: 'ODS→DWD transform started for batch #8421', labels: ['etl', 'transform'], duration: '379ms' },
  { id: 'l10', timestamp: '18:24:16.842', epoch: 1718499856.842, level: 'INFO',  service: 'etl-ods',        traceId: 'trc-82f1', spanId: 'sp-010', message: 'ODS→DWD transform completed, 42 rows → 42 rows, 0 rejected', labels: ['etl', 'transform', 'done'], duration: '821ms' },

  { id: 'l11', timestamp: '18:24:18.421', epoch: 1718499858.421, level: 'ERROR', service: 'identity-pool',   traceId: 'trc-a19c', spanId: 'sp-011', message: 'captcha risk triggered on zdopen_notice, switching to slow lane', labels: ['identity', 'captcha', 'risk'], duration: '1.2s', context: { source: 'zdopen_notice', risk_level: 'high', action: 'switch_slow_lane' } },
  { id: 'l12', timestamp: '18:24:19.103', epoch: 1718499859.103, level: 'WARN',  service: 'crawler-engine',  traceId: 'trc-a19c', spanId: 'sp-012', message: 'retry attempt 1/3 after captcha detection', labels: ['retry', 'captcha'], duration: '682ms' },
  { id: 'l13', timestamp: '18:24:20.842', epoch: 1718499860.842, level: 'ERROR', service: 'crawler-engine',  traceId: 'trc-a19c', spanId: 'sp-013', message: 'retry attempt 2/3 failed, HTTP 403 Forbidden', labels: ['retry', 'failed', '403'], duration: '1.7s' },
  { id: 'l14', timestamp: '18:24:23.103', epoch: 1718499863.103, level: 'ERROR', service: 'crawler-engine',  traceId: 'trc-a19c', spanId: 'sp-014', message: 'retry attempt 3/3 failed, giving up on zdopen_notice', labels: ['retry', 'failed', 'give_up'], duration: '2.3s' },
  { id: 'l15', timestamp: '18:24:23.842', epoch: 1718499863.842, level: 'ERROR', service: 'dag-engine',     traceId: 'trc-a19c', spanId: 'sp-015', message: 'task [fetch_zdopen] marked as FAILED after 3 retries', labels: ['dag', 'failed'], duration: '739ms', context: { task: 'fetch_zdopen', retries: '3', status: 'FAILED' } },

  { id: 'l16', timestamp: '18:24:25.421', epoch: 1718499865.421, level: 'INFO',  service: 'syncer',         traceId: 'trc-b3d2', spanId: 'sp-016', message: 'MySQL CDC binlog consumed, position: mysql-bin.000342:4821', labels: ['cdc', 'mysql', 'binlog'], duration: '12ms' },
  { id: 'l17', timestamp: '18:24:25.842', epoch: 1718499865.842, level: 'INFO',  service: 'writer-rds',     traceId: 'trc-b3d2', spanId: 'sp-017', message: 'batch committed to rds_market_data.raw, 240 records', labels: ['rds', 'commit'], duration: '421ms', context: { table: 'rds_market_data.raw', rows: '240' } },
  { id: 'l18', timestamp: '18:24:26.103', epoch: 1718499866.103, level: 'DEBUG', service: 'etl-dwd',        traceId: 'trc-b3d2', spanId: 'sp-018', message: 'DWD partition key generated: dt=2026-06-22', labels: ['etl', 'partition'], duration: '261ms' },
  { id: 'l19', timestamp: '18:24:26.842', epoch: 1718499866.842, level: 'INFO',  service: 'etl-dws',        traceId: 'trc-b3d2', spanId: 'sp-019', message: 'DWS aggregation completed for market_topic_daily', labels: ['etl', 'dws', 'done'], duration: '739ms', context: { table: 'dws_market.topic_daily', rows: '312' } },
  { id: 'l20', timestamp: '18:24:27.421', epoch: 1718499867.421, level: 'INFO',  service: 'api-gateway',    traceId: 'trc-b3d2', spanId: 'sp-020', message: 'GET /api/v1/market/topic-summary → 200 (12ms)', labels: ['api', 'http', '200'], duration: '12ms' },

  { id: 'l21', timestamp: '18:24:28.842', epoch: 1718499868.842, level: 'WARN',  service: 'api-cache',      traceId: 'trc-c4e3', spanId: 'sp-021', message: 'cache miss for key market:topic:summary, falling back to DB', labels: ['cache', 'miss'], duration: '3ms' },
  { id: 'l22', timestamp: '18:24:29.103', epoch: 1718499869.103, level: 'TRACE', service: 'metadata-collector', traceId: 'trc-c4e3', spanId: 'sp-022', message: 'collecting metadata for table dws_market.topic_daily', labels: ['metadata', 'collect'], duration: '261ms' },
  { id: 'l23', timestamp: '18:24:29.842', epoch: 1718499869.842, level: 'INFO',  service: 'lineage-parser', traceId: 'trc-c4e3', spanId: 'sp-023', message: 'lineage updated: rds_market_data.raw → ods_market.raw → dwd_market.patent → dws_market.topic_daily', labels: ['lineage', 'update'], duration: '739ms' },
  { id: 'l24', timestamp: '18:24:30.421', epoch: 1718499870.421, level: 'DEBUG', service: 'quality-engine', traceId: 'trc-c4e3', spanId: 'sp-024', message: 'quality rules executed: 8 passed, 0 failed for dws_market.topic_daily', labels: ['quality', 'rules'], duration: '579ms' },
  { id: 'l25', timestamp: '18:24:31.103', epoch: 1718499871.103, level: 'INFO',  service: 'api-gateway',    traceId: 'trc-c4e3', spanId: 'sp-025', message: 'GET /api/v1/market/topic-summary → 200 (4ms, cached)', labels: ['api', 'http', '200', 'cached'], duration: '4ms' },
];

/* ──────────────────────────────────────────────
 * Trace 瀑布图数据 — 选中 trace 的 span 序列
 * ────────────────────────────────────────────── */
interface TraceSpan {
  spanId: string;
  service: string;
  operation: string;
  startMs: number;
  durationMs: number;
  level: LogLevel;
  status: 'ok' | 'warn' | 'error';
}

const traceSpans: Record<string, TraceSpan[]> = {
  'trc-82f1': [
    { spanId: 'sp-001', service: 'scheduler',      operation: 'trigger DAG',       startMs: 0,    durationMs: 2,   level: 'INFO',  status: 'ok' },
    { spanId: 'sp-002', service: 'dag-engine',     operation: 'start task',        startMs: 72,   durationMs: 2,   level: 'INFO',  status: 'ok' },
    { spanId: 'sp-003', service: 'ai-agent',       operation: 'generate contract', startMs: 266,  durationMs: 194, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-004', service: 'crawler-engine', operation: 'HTTP GET list',     startMs: 570,  durationMs: 304, level: 'DEBUG', status: 'ok' },
    { spanId: 'sp-005', service: 'browser-render', operation: 'render page',       startMs: 979,  durationMs: 409, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-006', service: 'crawler-engine', operation: 'fetch detail batch',startMs: 1661, durationMs: 682, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-007', service: 'quality-engine', operation: 'check field missing',startMs: 2261,durationMs: 120, level: 'WARN',  status: 'warn' },
    { spanId: 'sp-008', service: 'writer-ods',     operation: 'commit batch',      startMs: 2800, durationMs: 539, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-009', service: 'etl-ods',        operation: 'ODS→DWD transform', startMs: 3179, durationMs: 379, level: 'TRACE', status: 'ok' },
    { spanId: 'sp-010', service: 'etl-ods',        operation: 'transform complete',startMs: 4000, durationMs: 821, level: 'INFO',  status: 'ok' },
  ],
  'trc-a19c': [
    { spanId: 'sp-011', service: 'identity-pool',  operation: 'captcha risk detected', startMs: 0,    durationMs: 1200, level: 'ERROR', status: 'error' },
    { spanId: 'sp-012', service: 'crawler-engine', operation: 'retry 1/3',             startMs: 682,  durationMs: 682,  level: 'WARN',  status: 'warn' },
    { spanId: 'sp-013', service: 'crawler-engine', operation: 'retry 2/3 (403)',       startMs: 1740, durationMs: 1700, level: 'ERROR', status: 'error' },
    { spanId: 'sp-014', service: 'crawler-engine', operation: 'retry 3/3 (give up)',   startMs: 3720, durationMs: 2300, level: 'ERROR', status: 'error' },
    { spanId: 'sp-015', service: 'dag-engine',     operation: 'mark task FAILED',      startMs: 4421, durationMs: 739,  level: 'ERROR', status: 'error' },
  ],
  'trc-b3d2': [
    { spanId: 'sp-016', service: 'syncer',         operation: 'CDC binlog consume',    startMs: 0,    durationMs: 12,  level: 'INFO',  status: 'ok' },
    { spanId: 'sp-017', service: 'writer-rds',     operation: 'commit to RDS',         startMs: 421,  durationMs: 421, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-018', service: 'etl-dwd',        operation: 'partition key gen',     startMs: 682,  durationMs: 261, level: 'DEBUG', status: 'ok' },
    { spanId: 'sp-019', service: 'etl-dws',        operation: 'DWS aggregation',       startMs: 1421, durationMs: 739, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-020', service: 'api-gateway',    operation: 'GET /market/summary',   startMs: 2240, durationMs: 12,  level: 'INFO',  status: 'ok' },
  ],
  'trc-c4e3': [
    { spanId: 'sp-021', service: 'api-cache',           operation: 'cache miss',          startMs: 0,    durationMs: 3,   level: 'WARN',  status: 'warn' },
    { spanId: 'sp-022', service: 'metadata-collector',  operation: 'collect metadata',    startMs: 261,  durationMs: 261, level: 'TRACE', status: 'ok' },
    { spanId: 'sp-023', service: 'lineage-parser',      operation: 'update lineage',      startMs: 682,  durationMs: 739, level: 'INFO',  status: 'ok' },
    { spanId: 'sp-024', service: 'quality-engine',      operation: 'execute rules',       startMs: 1421, durationMs: 579, level: 'DEBUG', status: 'ok' },
    { spanId: 'sp-025', service: 'api-gateway',         operation: 'GET (cached)',        startMs: 2000, durationMs: 4,   level: 'INFO',  status: 'ok' },
  ],
};

const spanStatusMeta: Record<TraceSpan['status'], { color: string; icon: React.ReactNode }> = {
  ok:    { color: aura.green,  icon: <CheckCircleOutlined /> },
  warn:  { color: aura.amber,  icon: <ExclamationCircleOutlined /> },
  error: { color: aura.danger, icon: <CloseCircleOutlined /> },
};

/* ──────────────────────────────────────────────
 * 终端日志行渲染
 * ────────────────────────────────────────────── */
const TerminalLogLine: React.FC<{ log: LogEntry; isSelected: boolean; onClick: () => void }> = ({ log, isSelected, onClick }) => {
  const meta = levelMeta[log.level];
  return (
    <div
      className={`log-line ${isSelected ? 'is-selected' : ''}`}
      onClick={onClick}
      style={{
        display: 'grid',
        gridTemplateColumns: '120px 64px 120px 96px 1fr',
        gap: '0',
        padding: '4px 12px',
        borderBottom: `1px solid ${aura.borderSoft}`,
        background: isSelected ? `${aura.purple}11` : 'transparent',
        cursor: 'pointer',
        fontFamily: '"Fira Code", "SF Mono", "Cascadia Code", monospace',
        fontSize: 12.5,
        lineHeight: 1.6,
        color: aura.text,
        transition: 'background 0.1s',
      }}
      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = `${aura.surface}55`; }}
      onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
    >
      <span style={{ color: aura.subtle }}>{log.timestamp}</span>
      <span style={{ color: meta.color, fontWeight: 700, padding: '0 4px' }}>{meta.label}</span>
      <span style={{ color: aura.muted }}>{log.service}</span>
      <span style={{ color: aura.cyan, textDecoration: 'underline', textDecorationStyle: 'dotted', textUnderlineOffset: '3px' }}>{log.traceId}</span>
      <span style={{ color: aura.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {log.message}
        {log.duration && <span style={{ color: aura.subtle, marginLeft: 8 }}>· {log.duration}</span>}
      </span>
    </div>
  );
};

/* ──────────────────────────────────────────────
 * Trace 瀑布图
 * ────────────────────────────────────────────── */
const TraceWaterfall: React.FC<{ traceId: string; logs: LogEntry[] }> = ({ traceId, logs: traceLogs }) => {
  const spans = traceSpans[traceId] || [];
  if (spans.length === 0) return null;

  const totalDuration = Math.max(...spans.map((s) => s.startMs + s.durationMs));
  const barWidth = 320; // 瀑布图条形区域宽度 px
  const rowHeight = 32;
  const labelWidth = 200;

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `${labelWidth}px 1fr`,
          gap: 0,
          height: 36,
          alignItems: 'center',
          borderBottom: `1px solid ${aura.border}`,
          background: aura.surfaceSoft,
          padding: '0 12px',
        }}
      >
        <span style={{ color: aura.muted, fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Service · Operation
        </span>
        <span style={{ color: aura.muted, fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Timeline · {totalDuration}ms total
        </span>
      </div>

      {/* 时间刻度 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `${labelWidth}px 1fr`,
          gap: 0,
          height: 24,
          alignItems: 'center',
          borderBottom: `1px solid ${aura.borderSoft}`,
          padding: '0 12px',
        }}
      >
        <span />
        <div style={{ position: 'relative', height: '100%' }}>
          {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
            <div
              key={pct}
              style={{
                position: 'absolute',
                left: `${pct * 100}%`,
                top: 0,
                bottom: 0,
                borderLeft: `1px dashed ${aura.borderSoft}`,
              }}
            >
              <span style={{ position: 'absolute', top: 4, left: 4, color: aura.subtle, fontSize: 10, fontFamily: '"Fira Code", monospace' }}>
                {Math.round(pct * totalDuration)}ms
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Span 行 */}
      {spans.map((span) => {
        const meta = spanStatusMeta[span.status];
        const leftPct = (span.startMs / totalDuration) * 100;
        const widthPct = Math.max((span.durationMs / totalDuration) * 100, 0.5);
        const logEntry = traceLogs.find((l) => l.spanId === span.spanId);
        return (
          <div
            key={span.spanId}
            className="waterfall-row"
            style={{
              display: 'grid',
              gridTemplateColumns: `${labelWidth}px 1fr`,
              gap: 0,
              height: rowHeight,
              alignItems: 'center',
              borderBottom: `1px solid ${aura.borderSoft}`,
              padding: '0 12px',
              transition: 'background 0.1s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = `${aura.surface}55`; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            {/* 左侧标签 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, paddingRight: 12 }}>
              <span style={{ color: meta.color, fontSize: 12 }}>{meta.icon}</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ color: aura.text, fontSize: 12, fontWeight: 600, fontFamily: '"Fira Code", monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {span.service}
                </div>
                <div style={{ color: aura.subtle, fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {span.operation}
                </div>
              </div>
            </div>
            {/* 右侧瀑布条 */}
            <div style={{ position: 'relative', height: '100%', display: 'flex', alignItems: 'center' }}>
              <div
                style={{
                  position: 'absolute',
                  left: `${leftPct}%`,
                  width: `${widthPct}%`,
                  height: 18,
                  borderRadius: 3,
                  background: `${meta.color}22`,
                  border: `1px solid ${meta.color}66`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  paddingRight: 4,
                }}
              >
                <span style={{ color: meta.color, fontSize: 10, fontFamily: '"Fira Code", monospace', fontWeight: 600 }}>
                  {span.durationMs}ms
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

/* ──────────────────────────────────────────────
 * 主组件
 * ────────────────────────────────────────────── */
const LogExplorer: React.FC = () => {
  const [query, setQuery] = useState('service:ai-agent');
  const [levelFilter, setLevelFilter] = useState<string>('all');
  const [timeRange, setTimeRange] = useState<string>('live');
  const [activeProductLine, setActiveProductLine] = useState<string>('all');
  const [selectedTrace, setSelectedTrace] = useState<string>('trc-82f1');
  const [followMode, setFollowMode] = useState(true);
  const [liveTick, setLiveTick] = useState(0);
  const [showTracePanel, setShowTracePanel] = useState(true);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const terminalRef = useRef<HTMLDivElement>(null);

  // 模拟实时刷新
  useEffect(() => {
    if (!followMode) return;
    const timer = setInterval(() => setLiveTick((t) => t + 1), 2000);
    return () => clearInterval(timer);
  }, [followMode]);

  // 自动滚动到底部
  useEffect(() => {
    if (followMode && terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [liveTick, followMode]);

  // 过滤日志
  const filtered = useMemo(() => {
    return logs.filter((log) => {
      const matchLevel = levelFilter === 'all' || log.level === levelFilter;
      const svc = services.find((s) => s.name === log.service);
      const matchLine = activeProductLine === 'all' || svc?.line === activeProductLine;
      const matchQuery = !query ||
        query.split(' ').every((term) => {
          const [key, val] = term.split(':');
          if (val) {
            if (key === 'service') return log.service.includes(val);
            if (key === 'trace') return log.traceId.includes(val);
            if (key === 'label') return log.labels.includes(val);
            if (key === 'level') return log.level === val.toUpperCase();
          }
          return `${log.service} ${log.traceId} ${log.message} ${log.labels.join(' ')}`.toLowerCase().includes(term.toLowerCase());
        });
      return matchLevel && matchLine && matchQuery;
    });
  }, [levelFilter, activeProductLine, query]);

  // 选中 trace 的日志
  const traceLogs = useMemo(() => logs.filter((l) => l.traceId === selectedTrace), [selectedTrace]);

  // 统计
  const stats = useMemo(() => ({
    total: filtered.length,
    error: filtered.filter((l) => l.level === 'ERROR').length,
    warn: filtered.filter((l) => l.level === 'WARN').length,
    traces: new Set(filtered.map((l) => l.traceId)).size,
  }), [filtered]);

  return (
    <ErrorBoundary>
      <style>
        {`
          @keyframes log-breathe {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
          }
          @keyframes log-cursor-blink {
            0%, 49% { opacity: 1; }
            50%, 100% { opacity: 0; }
          }
          .log-explorer {
            height: calc(100vh - 52px);
            background: ${aura.bg};
            color: ${aura.text};
            display: flex;
            flex-direction: column;
          }
          .log-explorer *::-webkit-scrollbar { width: 6px; height: 6px; }
          .log-explorer *::-webkit-scrollbar-thumb { background: ${aura.border}; border-radius: 3px; }
          .log-explorer *::-webkit-scrollbar-track { background: transparent; }

          /* ── Header ── */
          .log-header {
            flex-shrink: 0;
            padding: 14px 20px 0;
            border-bottom: 1px solid ${aura.border};
          }
          .log-title-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .log-title-row h1 {
            color: ${aura.text};
            font-size: 24px;
            font-weight: 700;
            margin: 0;
            line-height: 1.2;
          }
          .log-actions {
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .log-actions .ant-btn {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.muted};
            box-shadow: none;
          }
          .log-actions .ant-btn:hover {
            border-color: ${aura.cyan};
            color: ${aura.text};
          }
          .log-actions .ant-btn.is-active {
            background: ${aura.cyan}15;
            border-color: ${aura.cyan}66;
            color: ${aura.cyan};
          }
          .log-actions .ant-input-affix-wrapper {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.text};
          }
          .log-actions .ant-input {
            background: transparent;
            color: ${aura.text};
            font-family: 'Fira Code', monospace;
            font-size: 13px;
          }
          .log-actions .ant-select-selector {
            background: transparent !important;
            border-color: ${aura.border} !important;
            color: ${aura.muted} !important;
          }
          .log-actions .ant-select-arrow {
            color: ${aura.subtle};
          }
          .log-actions .ant-segmented {
            background: transparent;
            padding: 0;
          }
          .log-actions .ant-segmented-item {
            color: ${aura.muted};
          }
          .log-actions .ant-segmented-item-selected {
            background: ${aura.surface};
            color: ${aura.text};
            box-shadow: inset 0 0 0 1px ${aura.border};
          }

          /* ── LIVE badge ── */
          .log-live-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 12px;
            background: ${aura.green}15;
            border: 1px solid ${aura.green}33;
            color: ${aura.green};
            font-size: 11px;
            font-weight: 600;
            font-family: 'Fira Code', monospace;
          }
          .log-live-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: ${aura.green};
            animation: log-breathe 1.5s ease-in-out infinite;
          }
          .log-paused-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 12px;
            background: ${aura.amber}15;
            border: 1px solid ${aura.amber}33;
            color: ${aura.amber};
            font-size: 11px;
            font-weight: 600;
            font-family: 'Fira Code', monospace;
          }

          /* ── Stats bar ── */
          .log-stats {
            display: flex;
            align-items: center;
            gap: 20px;
            margin-top: 14px;
            padding-bottom: 12px;
          }
          .log-stat {
            display: flex;
            align-items: center;
            gap: 6px;
            color: ${aura.muted};
            font-size: 12px;
            font-weight: 600;
          }
          .log-stat-value {
            color: ${aura.text};
            font-family: 'Fira Code', monospace;
            font-size: 14px;
            font-weight: 700;
          }

          /* ── Product line filter ── */
          .line-filter {
            display: flex;
            align-items: center;
            gap: 6px;
            padding-bottom: 12px;
            flex-wrap: wrap;
          }
          .line-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 14px;
            border: 1px solid ${aura.borderSoft};
            background: transparent;
            color: ${aura.muted};
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
          }
          .line-chip:hover {
            border-color: ${aura.border};
            color: ${aura.text};
          }
          .line-chip.is-active {
            background: ${aura.surface};
            border-color: ${aura.cyan}66;
            color: ${aura.cyan};
          }

          /* ── Main split ── */
          .log-main {
            flex: 1;
            min-height: 0;
            display: flex;
            gap: 1px;
            background: ${aura.border};
          }

          /* ── Terminal panel ── */
          .log-terminal-panel {
            flex: 1;
            min-width: 0;
            background: ${aura.terminal};
            display: flex;
            flex-direction: column;
          }
          .log-terminal-head {
            height: 38px;
            padding: 0 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid ${aura.border};
            background: ${aura.panel};
            flex-shrink: 0;
          }
          .log-terminal-title {
            color: ${aura.muted};
            font-size: 12px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: 'Fira Code', monospace;
          }
          .log-terminal-dots {
            display: flex;
            gap: 6px;
          }
          .log-terminal-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
          }
          .log-terminal-body {
            flex: 1;
            overflow: auto;
            padding: 8px 0;
          }
          .log-terminal-cursor {
            display: inline-block;
            width: 8px;
            height: 14px;
            background: ${aura.cyan};
            animation: log-cursor-blink 1s step-end infinite;
            vertical-align: text-bottom;
            margin-left: 4px;
          }
          .log-expanded {
            padding: 8px 12px;
            background: ${aura.surfaceSoft};
            border-top: 1px solid ${aura.borderSoft};
            border-bottom: 1px solid ${aura.borderSoft};
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            color: ${aura.muted};
            line-height: 1.7;
          }
          .log-expanded-key { color: ${aura.cyan}; }
          .log-expanded-val { color: ${aura.text}; }

          /* ── Trace panel ── */
          .log-trace-panel {
            width: 540px;
            flex-shrink: 0;
            background: ${aura.panel};
            display: flex;
            flex-direction: column;
            overflow: hidden;
          }
          .log-trace-head {
            height: 38px;
            padding: 0 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid ${aura.border};
            background: ${aura.panel};
            flex-shrink: 0;
          }
          .log-trace-title {
            color: ${aura.text};
            font-size: 13px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .log-trace-body {
            flex: 1;
            overflow: auto;
          }
          .log-trace-summary {
            padding: 14px 16px;
            border-bottom: 1px solid ${aura.border};
            background: ${aura.surfaceSoft};
          }
          .log-trace-summary-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
          }
          .log-trace-summary-row:last-child { margin-bottom: 0; }

          /* ── Context panel ── */
          .log-context-section {
            padding: 12px 16px;
            border-bottom: 1px solid ${aura.borderSoft};
          }
          .log-context-title {
            color: ${aura.muted};
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
          }
          .log-context-log {
            display: grid;
            grid-template-columns: 80px 56px 1fr;
            gap: 6px;
            padding: 3px 0;
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            line-height: 1.5;
            cursor: pointer;
            border-radius: 3px;
            padding-left: 4px;
            transition: background 0.1s;
          }
          .log-context-log:hover { background: ${aura.surface}55; }
          .log-context-log.is-current { background: ${aura.purple}11; }
        `}
      </style>

      <div className="log-explorer">
        {/* ── Header ── */}
        <header className="log-header">
          <div className="log-title-row">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <FileSearchOutlined style={{ color: aura.cyan, fontSize: 20 }} />
              <h1>日志追踪</h1>
              {followMode ? (
                <span className="log-live-badge">
                  <span className="log-live-dot" />
                  LIVE · {liveTick}
                </span>
              ) : (
                <span className="log-paused-badge">
                  <PauseCircleOutlined /> PAUSED
                </span>
              )}
            </div>
            <div className="log-actions">
              <Input
                prefix={<SearchOutlined />}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="service:ai-agent trace:trc-82f1 label:quality"
                style={{ width: 320, fontFamily: 'Fira Code, monospace' }}
              />
              <Select
                value={levelFilter}
                onChange={setLevelFilter}
                style={{ width: 120 }}
                options={[
                  { label: '全部等级', value: 'all' },
                  { label: 'INFO', value: 'INFO' },
                  { label: 'WARN', value: 'WARN' },
                  { label: 'ERROR', value: 'ERROR' },
                  { label: 'DEBUG', value: 'DEBUG' },
                  { label: 'TRACE', value: 'TRACE' },
                ]}
              />
              <Segmented
                value={timeRange}
                onChange={(v) => setTimeRange(v as string)}
                options={[
                  { label: '实时', value: 'live' },
                  { label: '15min', value: '15m' },
                  { label: '1h', value: '1h' },
                  { label: '24h', value: '24h' },
                ]}
              />
              <Button
                className={followMode ? 'is-active' : ''}
                icon={followMode ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                onClick={() => setFollowMode(!followMode)}
              >
                {followMode ? '暂停' : '跟随'}
              </Button>
              <Button
                className={showTracePanel ? 'is-active' : ''}
                icon={<BranchesOutlined />}
                onClick={() => setShowTracePanel(!showTracePanel)}
              >
                Trace
              </Button>
              <Button icon={<ClearOutlined />} />
              <Button icon={<DownloadOutlined />} />
            </div>
          </div>

          {/* Stats bar */}
          <div className="log-stats">
            <span className="log-stat">
              <DatabaseOutlined style={{ color: aura.muted }} />
              日志总数 <span className="log-stat-value">{stats.total}</span>
            </span>
            <span className="log-stat">
              <CloseCircleOutlined style={{ color: aura.danger }} />
              ERROR <span className="log-stat-value" style={{ color: aura.danger }}>{stats.error}</span>
            </span>
            <span className="log-stat">
              <ExclamationCircleOutlined style={{ color: aura.amber }} />
              WARN <span className="log-stat-value" style={{ color: aura.amber }}>{stats.warn}</span>
            </span>
            <span className="log-stat">
              <BranchesOutlined style={{ color: aura.cyan }} />
              Trace <span className="log-stat-value">{stats.traces}</span>
            </span>
            <span style={{ flex: 1 }} />
            <span className="log-stat" style={{ color: aura.subtle }}>
              <ClockCircleOutlined /> 最后更新: 18:24:31
            </span>
          </div>

          {/* Product line filter */}
          <div className="line-filter">
            <span
              className={`line-chip ${activeProductLine === 'all' ? 'is-active' : ''}`}
              onClick={() => setActiveProductLine('all')}
            >
              全部服务
            </span>
            {(Object.keys(productLineMeta) as ProductLine[]).map((line) => {
              const meta = productLineMeta[line];
              const count = services.filter((s) => s.line === line).length;
              return (
                <span
                  key={line}
                  className={`line-chip ${activeProductLine === line ? 'is-active' : ''}`}
                  onClick={() => setActiveProductLine(line)}
                  style={activeProductLine === line ? { borderColor: `${meta.color}66`, color: meta.color } : {}}
                >
                  {meta.icon} {meta.label} · {count}
                </span>
              );
            })}
          </div>
        </header>

        {/* ── Main split: Terminal + Trace panel ── */}
        <div className="log-main">
          {/* Terminal */}
          <div className="log-terminal-panel">
            <div className="log-terminal-head">
              <div className="log-terminal-title">
                <span className="log-terminal-dots">
                  <span className="log-terminal-dot" style={{ background: aura.danger }} />
                  <span className="log-terminal-dot" style={{ background: aura.amber }} />
                  <span className="log-terminal-dot" style={{ background: aura.green }} />
                </span>
                <span>logs — follow={followMode ? 'true' : 'false'} · {filtered.length} entries</span>
              </div>
              <Space size={8}>
                <span style={{ color: aura.subtle, fontSize: 11, fontFamily: '"Fira Code", monospace' }}>
                  Fira Code 12.5px
                </span>
                <Button size="small" type="text" icon={<VerticalAlignBottomOutlined />} style={{ color: followMode ? aura.cyan : aura.subtle }} />
              </Space>
            </div>
            <div className="log-terminal-body" ref={terminalRef}>
              {filtered.map((log) => (
                <React.Fragment key={log.id}>
                  <TerminalLogLine
                    log={log}
                    isSelected={log.traceId === selectedTrace}
                    onClick={() => {
                      setSelectedTrace(log.traceId);
                      setExpandedLog(expandedLog === log.id ? null : log.id);
                    }}
                  />
                  {expandedLog === log.id && log.context && (
                    <div className="log-expanded">
                      <div style={{ color: aura.subtle, marginBottom: 4 }}>── context ──</div>
                      {Object.entries(log.context).map(([k, v]) => (
                        <div key={k}>
                          <span className="log-expanded-key">{k}</span>
                          <span style={{ color: aura.subtle }}>: </span>
                          <span className="log-expanded-val">{v}</span>
                        </div>
                      ))}
                      <div style={{ marginTop: 6, color: aura.subtle }}>
                        <span className="log-expanded-key">span</span>
                        <span style={{ color: aura.subtle }}>: </span>
                        <span className="log-expanded-val">{log.spanId}</span>
                        <span style={{ color: aura.subtle, margin: '0 8px' }}>·</span>
                        <span className="log-expanded-key">duration</span>
                        <span style={{ color: aura.subtle }}>: </span>
                        <span className="log-expanded-val">{log.duration}</span>
                      </div>
                    </div>
                  )}
                </React.Fragment>
              ))}
              {/* Terminal 光标 */}
              {followMode && (
                <div style={{ padding: '4px 12px', fontFamily: '"Fira Code", monospace', fontSize: 12.5, color: aura.muted }}>
                  <span style={{ color: aura.green }}>➜</span>{' '}
                  <span style={{ color: aura.cyan }}>logs</span>
                  <span style={{ color: aura.muted }}> ~</span>
                  <span className="log-terminal-cursor" />
                </div>
              )}
              {filtered.length === 0 && (
                <div style={{ padding: '40px 20px', textAlign: 'center', color: aura.subtle, fontFamily: '"Fira Code", monospace', fontSize: 13 }}>
                  No log entries matching current filters.
                </div>
              )}
            </div>
          </div>

          {/* Trace Waterfall Panel */}
          {showTracePanel && (
            <div className="log-trace-panel">
              <div className="log-trace-head">
                <div className="log-trace-title">
                  <BranchesOutlined style={{ color: aura.cyan }} />
                  Trace <span style={{ color: aura.cyan, fontFamily: '"Fira Code", monospace' }}>{selectedTrace}</span>
                </div>
                <Tooltip title="复制 Trace ID">
                  <Button size="small" type="text" icon={<CopyOutlined />} style={{ color: aura.muted }} />
                </Tooltip>
              </div>
              <div className="log-trace-body">
                {/* Trace summary */}
                <div className="log-trace-summary">
                  <div className="log-trace-summary-row">
                    <span style={{ color: aura.muted, fontSize: 12 }}>Trace ID</span>
                    <span style={{ color: aura.cyan, fontFamily: '"Fira Code", monospace', fontSize: 12 }}>{selectedTrace}</span>
                  </div>
                  <div className="log-trace-summary-row">
                    <span style={{ color: aura.muted, fontSize: 12 }}>Span 数</span>
                    <span style={{ color: aura.text, fontFamily: '"Fira Code", monospace', fontSize: 12 }}>{traceSpans[selectedTrace]?.length || 0}</span>
                  </div>
                  <div className="log-trace-summary-row">
                    <span style={{ color: aura.muted, fontSize: 12 }}>总耗时</span>
                    <span style={{ color: aura.text, fontFamily: '"Fira Code", monospace', fontSize: 12 }}>
                      {traceSpans[selectedTrace] ? Math.max(...traceSpans[selectedTrace].map((s) => s.startMs + s.durationMs)) : 0}ms
                    </span>
                  </div>
                  <div className="log-trace-summary-row">
                    <span style={{ color: aura.muted, fontSize: 12 }}>状态</span>
                    {traceSpans[selectedTrace]?.some((s) => s.status === 'error') ? (
                      <span style={{ color: aura.danger, fontSize: 12, fontWeight: 600 }}>
                        <CloseCircleOutlined /> 包含错误
                      </span>
                    ) : traceSpans[selectedTrace]?.some((s) => s.status === 'warn') ? (
                      <span style={{ color: aura.amber, fontSize: 12, fontWeight: 600 }}>
                        <ExclamationCircleOutlined /> 包含告警
                      </span>
                    ) : (
                      <span style={{ color: aura.green, fontSize: 12, fontWeight: 600 }}>
                        <CheckCircleOutlined /> 全部正常
                      </span>
                    )}
                  </div>
                </div>

                {/* Trace 瀑布图 */}
                <TraceWaterfall traceId={selectedTrace} logs={traceLogs} />

                {/* 同 Trace 上下文日志 */}
                <div className="log-context-section">
                  <div className="log-context-title">同 Trace 日志上下文</div>
                  {traceLogs.map((log) => (
                    <div
                      key={log.id}
                      className={`log-context-log ${log.traceId === selectedTrace ? 'is-current' : ''}`}
                      onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
                    >
                      <span style={{ color: aura.subtle }}>{log.timestamp}</span>
                      <span style={{ color: levelMeta[log.level].color, fontWeight: 700 }}>{log.level}</span>
                      <span style={{ color: aura.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {log.message}
                      </span>
                    </div>
                  ))}
                </div>

                {/* 可用 Trace 列表 */}
                <div className="log-context-section" style={{ borderBottom: 'none' }}>
                  <div className="log-context-title">可切换 Trace</div>
                  {Object.keys(traceSpans).map((tid) => {
                    const spans = traceSpans[tid];
                    const hasError = spans.some((s) => s.status === 'error');
                    const hasWarn = spans.some((s) => s.status === 'warn');
                    return (
                      <div
                        key={tid}
                        className={`log-context-log ${tid === selectedTrace ? 'is-current' : ''}`}
                        onClick={() => setSelectedTrace(tid)}
                      >
                        <span style={{ color: tid === selectedTrace ? aura.cyan : aura.subtle, fontFamily: '"Fira Code", monospace' }}>{tid}</span>
                        <span style={{ color: hasError ? aura.danger : hasWarn ? aura.amber : aura.green }}>
                          {hasError ? 'ERR' : hasWarn ? 'WRN' : 'OK'}
                        </span>
                        <span style={{ color: aura.muted }}>
                          {spans.length} spans · {Math.max(...spans.map((s) => s.startMs + s.durationMs))}ms
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
};

export default LogExplorer;

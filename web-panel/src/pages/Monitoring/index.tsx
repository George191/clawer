import React, { useEffect, useRef, useState } from 'react';
import { Badge, Button, Input, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import {
  AlertOutlined,
  ArrowRightOutlined,
  CaretDownOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FieldTimeOutlined,
  FilterOutlined,
  LineChartOutlined,
  PauseCircleOutlined,
  RadarChartOutlined,
  ReloadOutlined,
  SearchOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

/* ──────────────────────────────────────────────
 * Aura 色板 — 与 TaskCenter / Templates 对齐
 * ────────────────────────────────────────────── */
const aura = {
  bg: '#15191F',
  surface: '#1F2429',
  surfaceSoft: '#171C22',
  panel: '#1A1F25',
  row: '#1F252D',
  rowAlt: '#1C2229',
  border: '#2D3540',
  borderSoft: '#252C36',
  text: '#F3F4F6',
  muted: '#9AA3AF',
  subtle: '#697280',
  purple: '#8B5CF6',
  cyan: '#8FE3E8',
  green: '#31D26B',
  amber: '#FBBF24',
  danger: '#F87171',
  blue: '#60A5FA',
};

/* ──────────────────────────────────────────────
 * Mock 数据
 * ────────────────────────────────────────────── */

type AlertLevel = 'P0' | 'P1' | 'P2';
type SourceHealth = 'healthy' | 'warning' | 'critical' | 'idle';
type TrendDirection = 'up' | 'down' | 'flat';

interface KpiCard {
  key: string;
  label: string;
  value: string;
  unit: string;
  trend: string;
  trendDir: TrendDirection;
  color: string;
  icon: React.ReactNode;
  sparkline: number[];
}

const kpis: KpiCard[] = [
  {
    key: 'success',
    label: '采集成功率',
    value: '96.4',
    unit: '%',
    trend: '+1.2%',
    trendDir: 'up',
    color: aura.green,
    icon: <CheckCircleOutlined />,
    sparkline: [92, 93, 91, 94, 95, 93, 96, 95, 96, 97, 96, 96.4],
  },
  {
    key: 'latency',
    label: 'P95 页面延迟',
    value: '1.8',
    unit: 's',
    trend: '-0.3s',
    trendDir: 'down',
    color: aura.blue,
    icon: <ClockCircleOutlined />,
    sparkline: [2.4, 2.2, 2.5, 2.1, 2.0, 2.3, 1.9, 2.1, 1.8, 2.0, 1.9, 1.8],
  },
  {
    key: 'alerts',
    label: '活跃告警',
    value: '3',
    unit: '',
    trend: '2 已收敛',
    trendDir: 'flat',
    color: aura.amber,
    icon: <AlertOutlined />,
    sparkline: [7, 6, 8, 5, 4, 6, 3, 5, 4, 3, 3, 3],
  },
  {
    key: 'capacity',
    label: '运行容量',
    value: '72',
    unit: '%',
    trend: 'AI Agent 槽位',
    trendDir: 'flat',
    color: aura.purple,
    icon: <CloudServerOutlined />,
    sparkline: [55, 60, 58, 65, 70, 68, 72, 69, 71, 74, 70, 72],
  },
];

interface SourceNode {
  key: string;
  name: string;
  type: 'crawler' | 'api' | 'file' | 'db';
  qps: number;
  health: SourceHealth;
  errorRate: number;
  lastTick: string;
}

const sources: SourceNode[] = [
  { key: 's1', name: 'google_patent', type: 'crawler', qps: 142, health: 'healthy', errorRate: 0.4, lastTick: '2s' },
  { key: 's2', name: 'sealagom_navwarn', type: 'crawler', qps: 86, health: 'healthy', errorRate: 0.8, lastTick: '1s' },
  { key: 's3', name: 'zdopen_notice', type: 'crawler', qps: 0, health: 'critical', errorRate: 34.2, lastTick: '8m' },
  { key: 's4', name: 'market_data_api', type: 'api', qps: 320, health: 'healthy', errorRate: 0.1, lastTick: '1s' },
  { key: 's5', name: 'gov_open_data', type: 'api', qps: 48, health: 'warning', errorRate: 5.3, lastTick: '3s' },
  { key: 's6', name: 'pdf_repo_sync', type: 'file', qps: 12, health: 'healthy', errorRate: 0.0, lastTick: '5s' },
  { key: 's7', name: 'rds_pg_sync', type: 'db', qps: 240, health: 'healthy', errorRate: 0.2, lastTick: '1s' },
  { key: 's8', name: 'mysql_cdc', type: 'db', qps: 180, health: 'healthy', errorRate: 0.3, lastTick: '1s' },
  { key: 's9', name: 'kafka_consumer', type: 'api', qps: 560, health: 'healthy', errorRate: 0.0, lastTick: '1s' },
  { key: 's10', name: 'rss_feeds', type: 'crawler', qps: 24, health: 'idle', errorRate: 0.0, lastTick: '15m' },
  { key: 's11', name: 'minio_objects', type: 'file', qps: 8, health: 'healthy', errorRate: 0.0, lastTick: '4s' },
  { key: 's12', name: 'custom_webhook', type: 'api', qps: 0, health: 'warning', errorRate: 12.5, lastTick: '2m' },
];

const sourceTypeMeta: Record<SourceNode['type'], { label: string; icon: string }> = {
  crawler: { label: '爬虫', icon: '🕷' },
  api: { label: 'API', icon: '🔗' },
  file: { label: '文件', icon: '📄' },
  db: { label: 'DB', icon: '🗄' },
};

const healthMeta: Record<SourceHealth, { color: string; label: string; pulse: boolean }> = {
  healthy: { color: aura.green, label: '正常', pulse: false },
  warning: { color: aura.amber, label: '告警', pulse: true },
  critical: { color: aura.danger, label: '异常', pulse: true },
  idle: { color: aura.subtle, label: '空闲', pulse: false },
};

/* 吞吐曲线数据 — 各层 records/min */
const throughputLayers = [
  { name: 'RDS', color: '#7C3AED', data: [420, 460, 380, 520, 480, 560, 610, 580, 640, 600, 680, 720] },
  { name: 'ODS', color: '#0EA5E9', data: [380, 420, 360, 480, 460, 520, 580, 540, 600, 560, 640, 680] },
  { name: 'DWD', color: '#059669', data: [320, 360, 340, 420, 400, 460, 500, 480, 540, 500, 580, 620] },
  { name: 'DWS', color: '#F59E0B', data: [180, 200, 190, 240, 220, 260, 300, 280, 340, 300, 380, 420] },
  { name: 'ADS', color: '#F97316', data: [80, 100, 90, 120, 110, 140, 160, 150, 180, 160, 200, 220] },
];

const throughputMax = Math.max(...throughputLayers.flatMap((l) => l.data));

/* SLI 指标行 */
interface SliRow {
  key: string;
  signal: string;
  value: string;
  threshold: string;
  status: 'healthy' | 'warning' | 'critical';
  owner: string;
  sparkline: number[];
}

const sliMetrics: SliRow[] = [
  { key: '1', signal: '采集成功率', value: '96.4%', threshold: '> 95%', status: 'healthy', owner: 'AI Collect', sparkline: [93, 94, 92, 95, 96, 94, 96, 95, 96, 97, 96, 96.4] },
  { key: '2', signal: 'P95 页面延迟', value: '1.8s', threshold: '< 3s', status: 'healthy', owner: 'Runtime', sparkline: [2.4, 2.2, 2.5, 2.1, 2.0, 2.3, 1.9, 2.1, 1.8, 2.0, 1.9, 1.8] },
  { key: '3', signal: '字段缺失率', value: '1.7%', threshold: '< 1%', status: 'warning', owner: 'Governance', sparkline: [0.8, 0.9, 1.1, 1.0, 1.2, 1.4, 1.3, 1.5, 1.6, 1.5, 1.7, 1.7] },
  { key: '4', signal: '代理池可用率', value: '86%', threshold: '> 80%', status: 'healthy', owner: 'Identity', sparkline: [78, 80, 82, 79, 81, 83, 85, 84, 86, 85, 86, 86] },
  { key: '5', signal: 'Kafka 消费延迟', value: '2.4K', threshold: '< 5K', status: 'healthy', owner: 'Pipeline', sparkline: [4.2, 3.8, 3.5, 3.0, 2.8, 3.2, 2.6, 2.4, 2.5, 2.4, 2.3, 2.4] },
  { key: '6', signal: 'DAG 任务失败率', value: '0.8%', threshold: '< 2%', status: 'healthy', owner: 'Scheduler', sparkline: [1.8, 1.5, 1.2, 1.0, 0.9, 1.1, 0.8, 0.9, 0.7, 0.8, 0.9, 0.8] },
];

/* 告警事件 */
interface AlertEvent {
  key: string;
  level: AlertLevel;
  title: string;
  source: string;
  time: string;
  status: 'firing' | 'acknowledged' | 'resolved';
  description: string;
}

const alerts: AlertEvent[] = [
  {
    key: 'a1',
    level: 'P0',
    title: 'zdopen_notice 采集失败率飙升',
    source: 'zdopen_notice',
    time: '3 分钟前',
    status: 'firing',
    description: '验证码风险等级升高，已触发降速。失败率 34.2%，远超 5% 阈值。',
  },
  {
    key: 'a2',
    level: 'P1',
    title: '字段缺失率超过阈值',
    source: 'google_patent_contract',
    time: '12 分钟前',
    status: 'acknowledged',
    description: 'abstract 字段缺失率 1.7%，阈值 1%。已通知负责人复核字段合约。',
  },
  {
    key: 'a3',
    level: 'P1',
    title: 'custom_webhook 间歇性 502',
    source: 'custom_webhook',
    time: '18 分钟前',
    status: 'firing',
    description: '近 5 分钟 12.5% 请求返回 502，上游服务可能不可用。',
  },
  {
    key: 'a4',
    level: 'P2',
    title: '代理池恢复到 86%',
    source: 'identity pool',
    time: '25 分钟前',
    status: 'resolved',
    description: '代理池自动恢复，之前触发的降速策略已撤销。',
  },
  {
    key: 'a5',
    level: 'P2',
    title: '队列积压下降',
    source: 'browser-render pool',
    time: '42 分钟前',
    status: 'resolved',
    description: '渲染池积压从 58% 降至 31%，恢复正常调度。',
  },
];

const alertLevelMeta: Record<AlertLevel, { color: string; bg: string }> = {
  P0: { color: aura.danger, bg: 'rgba(248,113,113,0.12)' },
  P1: { color: aura.amber, bg: 'rgba(251,191,36,0.12)' },
  P2: { color: aura.blue, bg: 'rgba(96,165,250,0.12)' },
};

const alertStatusMeta: Record<AlertEvent['status'], { color: string; label: string; icon: React.ReactNode }> = {
  firing: { color: aura.danger, label: 'Firing', icon: <AlertOutlined /> },
  acknowledged: { color: aura.amber, label: '已确认', icon: <EyeOutlined /> },
  resolved: { color: aura.green, label: '已恢复', icon: <CheckCircleOutlined /> },
};

/* DAG 任务概览 */
interface DagTask {
  key: string;
  name: string;
  status: 'running' | 'queued' | 'done' | 'failed';
  records: string;
  duration: string;
}

const dagTasks: DagTask[] = [
  { key: 'd1', name: 'google_patent_daily', status: 'running', records: '1.2M', duration: '24m' },
  { key: 'd2', name: 'sealagom_navwarn_sync', status: 'running', records: '48.6K', duration: '8m' },
  { key: 'd3', name: 'quality_missing_scan', status: 'queued', records: '8 tables', duration: '-' },
  { key: 'd4', name: 'ads_topic_market', status: 'done', records: '312K', duration: '6m' },
  { key: 'd5', name: 'navarea_backfill', status: 'failed', records: '0', duration: '3m' },
  { key: 'd6', name: 'ods_to_dwd_transform', status: 'running', records: '820K', duration: '15m' },
];

const dagStatusMeta: Record<DagTask['status'], { color: string; icon: React.ReactNode; label: string }> = {
  running: { color: aura.purple, icon: <SyncOutlined spin />, label: 'Running' },
  queued: { color: aura.subtle, icon: <ClockCircleOutlined />, label: 'Queued' },
  done: { color: aura.green, icon: <CheckCircleOutlined />, label: 'Done' },
  failed: { color: aura.danger, icon: <CloseCircleOutlined />, label: 'Failed' },
};

/* 存储层记录数 */
const storageLayers = [
  { name: 'RDS', total: '4.2B', todayDelta: '+12.4M', color: '#7C3AED', utilization: 68 },
  { name: 'ODS', total: '8.6B', todayDelta: '+28.1M', color: '#0EA5E9', utilization: 54 },
  { name: 'DWD', total: '6.1B', todayDelta: '+18.3M', color: '#059669', utilization: 47 },
  { name: 'DWS', total: '2.4B', todayDelta: '+6.8M', color: '#F59E0B', utilization: 38 },
  { name: 'ADS', total: '820M', todayDelta: '+2.1M', color: '#F97316', utilization: 22 },
];

/* ──────────────────────────────────────────────
 * Sparkline 组件（纯 SVG）
 * ────────────────────────────────────────────── */
const Sparkline: React.FC<{ data: number[]; color: string; width?: number; height?: number; max?: number; fill?: boolean }> = ({
  data,
  color,
  width = 120,
  height = 36,
  max,
  fill = true,
}) => {
  const maxVal = max ?? Math.max(...data);
  const minVal = Math.min(...data);
  const range = maxVal - minVal || 1;
  const stepX = width / (data.length - 1);
  const points = data.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - minVal) / range) * (height - 4) - 2;
    return [x, y] as const;
  });
  const pathD = points.map(([x, y], i) => (i === 0 ? `M ${x},${y}` : `L ${x},${y}`)).join(' ');
  const areaD = `${pathD} L ${width},${height} L 0,${height} Z`;

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {fill && <path d={areaD} fill={color} opacity={0.12} />}
      <path d={pathD} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={points[points.length - 1][0]} cy={points[points.length - 1][1]} r={2} fill={color} />
    </svg>
  );
};

/* ──────────────────────────────────────────────
 * 多层吞吐面积图（纯 SVG）
 * ────────────────────────────────────────────── */
const ThroughputChart: React.FC = () => {
  const width = 720;
  const height = 200;
  const padding = { top: 16, right: 16, bottom: 28, left: 44 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;
  const numPoints = throughputLayers[0].data.length;
  const stepX = chartW / (numPoints - 1);

  const timeLabels = ['00:00', '02:00', '04:00', '06:00', '08:00', '10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00'];

  // 累积堆叠
  const stacked = throughputLayers.map((layer, layerIdx) => {
    return layer.data.map((v, i) => {
      let cum = 0;
      for (let l = 0; l < layerIdx; l++) cum += throughputLayers[l].data[i];
      return cum + v;
    });
  });

  const yMax = Math.max(...stacked[stacked.length - 1]) * 1.1;
  const yTicks = 4;

  const yToPx = (val: number) => chartH - (val / yMax) * chartH;
  const xToPx = (i: number) => i * stepX;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      {/* Y 轴网格线 */}
      {Array.from({ length: yTicks + 1 }, (_, i) => {
        const val = (yMax / yTicks) * i;
        const y = padding.top + yToPx(val);
        return (
          <g key={i}>
            <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke={aura.borderSoft} strokeWidth={1} strokeDasharray="2 4" />
            <text x={padding.left - 8} y={y + 4} fill={aura.subtle} fontSize={10} textAnchor="end" fontFamily="Fira Code, monospace">
              {val >= 1000 ? `${(val / 1000).toFixed(1)}K` : Math.round(val)}
            </text>
          </g>
        );
      })}
      {/* X 轴标签 */}
      {timeLabels.map((label, i) => (
        <text key={label} x={padding.left + xToPx(i)} y={height - 8} fill={aura.subtle} fontSize={10} textAnchor="middle" fontFamily="Fira Code, monospace">
          {label}
        </text>
      ))}
      {/* 堆叠面积 */}
      {throughputLayers.map((layer, layerIdx) => {
        const topPath = layer.data.map((v, i) => {
          let cum = 0;
          for (let l = 0; l < layerIdx; l++) cum += throughputLayers[l].data[i];
          return `${padding.left + xToPx(i)},${padding.top + yToPx(cum + v)}`;
        });
        const bottomPath = [...layer.data].reverse().map((v, i) => {
          const dataIdx = layer.data.length - 1 - i;
          let cum = 0;
          for (let l = 0; l < layerIdx; l++) cum += throughputLayers[l].data[dataIdx];
          return `${padding.left + xToPx(dataIdx)},${padding.top + yToPx(cum)}`;
        });
        const areaPath = `M ${topPath.join(' L ')} L ${bottomPath.join(' L ')} Z`;
        const linePath = `M ${topPath.join(' L ')}`;
        return (
          <g key={layer.name}>
            <path d={areaPath} fill={layer.color} opacity={0.15} />
            <path d={linePath} fill="none" stroke={layer.color} strokeWidth={1.5} strokeLinejoin="round" />
          </g>
        );
      })}
      {/* 图例 */}
      {throughputLayers.map((layer, i) => (
        <g key={layer.name} transform={`translate(${padding.left + 8 + i * 80}, ${padding.top + 4})`}>
          <rect width={8} height={8} rx={2} fill={layer.color} />
          <text x={12} y={8} fill={aura.muted} fontSize={10} fontFamily="Fira Code, monospace">{layer.name}</text>
        </g>
      ))}
    </svg>
  );
};

/* ──────────────────────────────────────────────
 * 主组件
 * ────────────────────────────────────────────── */
type ViewMode = 'overview' | 'source' | 'capacity';

const Monitoring: React.FC = () => {
  const [view, setView] = useState<ViewMode>('overview');
  const [keyword, setKeyword] = useState('');
  const [noiseFilter, setNoiseFilter] = useState(true);
  const [liveTick, setLiveTick] = useState(0);

  // 模拟 WebSocket 实时刷新
  useEffect(() => {
    const timer = setInterval(() => setLiveTick((t) => t + 1), 3000);
    return () => clearInterval(timer);
  }, []);

  const filteredSources = sources.filter(
    (s) => !keyword || s.name.toLowerCase().includes(keyword.toLowerCase())
  );

  const firingAlerts = noiseFilter
    ? alerts.filter((a) => a.status !== 'resolved' || a.level === 'P0')
    : alerts;

  const renderHealthDot = (health: SourceHealth) => {
    const meta = healthMeta[health];
    return (
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: meta.color,
          display: 'inline-block',
          boxShadow: meta.pulse ? `0 0 0 2px ${meta.color}33` : 'none',
          animation: meta.pulse ? 'monitor-pulse 2s ease-in-out infinite' : 'none',
        }}
      />
    );
  };

  return (
    <ErrorBoundary>
      <style>
        {`
          @keyframes monitor-pulse {
            0%, 100% { opacity: 1; box-shadow: 0 0 0 0 ${aura.amber}44; }
            50% { opacity: 0.7; box-shadow: 0 0 0 4px ${aura.amber}11; }
          }
          @keyframes monitor-breathe {
            0%, 100% { opacity: 0.6; }
            50% { opacity: 1; }
          }
          .monitor-board {
            min-height: calc(100vh - 52px);
            background: ${aura.bg};
            color: ${aura.text};
            display: flex;
            flex-direction: column;
          }
          .monitor-board *::-webkit-scrollbar { width: 6px; height: 6px; }
          .monitor-board *::-webkit-scrollbar-thumb { background: ${aura.border}; border-radius: 3px; }
          .monitor-board *::-webkit-scrollbar-track { background: transparent; }

          /* ── Header ── */
          .monitor-header {
            flex-shrink: 0;
            padding: 16px 24px 0;
            border-bottom: 1px solid ${aura.border};
          }
          .monitor-title-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .monitor-title-row h1 {
            color: ${aura.text};
            font-size: 26px;
            font-weight: 700;
            margin: 0;
            line-height: 1.2;
          }
          .monitor-actions {
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .monitor-actions .ant-btn {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.muted};
            box-shadow: none;
          }
          .monitor-actions .ant-btn:hover {
            border-color: ${aura.purple};
            color: ${aura.text};
          }
          .monitor-actions .ant-input-affix-wrapper {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.text};
          }
          .monitor-actions .ant-input {
            background: transparent;
            color: ${aura.text};
          }
          .monitor-actions .ant-segmented {
            background: transparent;
            padding: 0;
          }
          .monitor-actions .ant-segmented-item {
            color: ${aura.muted};
          }
          .monitor-actions .ant-segmented-item-selected {
            background: ${aura.surface};
            color: ${aura.text};
            box-shadow: inset 0 0 0 1px ${aura.border};
          }

          /* ── Live indicator ── */
          .monitor-live-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 12px;
            background: ${aura.green}15;
            border: 1px solid ${aura.green}33;
            color: ${aura.green};
            font-size: 12px;
            font-weight: 600;
            font-family: 'Fira Code', monospace;
          }
          .monitor-live-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: ${aura.green};
            animation: monitor-breathe 1.5s ease-in-out infinite;
          }

          /* ── Tabs ── */
          .monitor-tabs {
            display: flex;
            align-items: center;
            gap: 22px;
            margin-top: 16px;
          }
          .monitor-tab {
            color: ${aura.muted};
            padding: 10px 0;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border-bottom: 2px solid transparent;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: color 0.2s;
          }
          .monitor-tab:hover { color: ${aura.text}; }
          .monitor-tab.is-active {
            color: ${aura.text};
            border-bottom-color: ${aura.cyan};
          }

          /* ── Content ── */
          .monitor-content {
            flex: 1;
            padding: 20px 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            overflow: auto;
          }

          /* ── KPI 卡片 ── */
          .kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 14px;
          }
          .kpi-card {
            background: ${aura.surface};
            border: 1px solid ${aura.borderSoft};
            border-radius: 8px;
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .kpi-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
          }
          .kpi-label {
            color: ${aura.muted};
            font-size: 12px;
            font-weight: 600;
          }
          .kpi-icon {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
          }
          .kpi-value-row {
            display: flex;
            align-items: baseline;
            gap: 6px;
          }
          .kpi-value {
            color: ${aura.text};
            font-size: 30px;
            font-weight: 800;
            line-height: 1;
            font-family: 'Fira Code', monospace;
          }
          .kpi-unit {
            color: ${aura.muted};
            font-size: 14px;
            font-weight: 600;
          }
          .kpi-trend {
            font-size: 12px;
            font-weight: 600;
          }
          .kpi-sparkline {
            margin-top: 2px;
          }

          /* ── Panel 通用 ── */
          .monitor-panel {
            background: ${aura.surface};
            border: 1px solid ${aura.borderSoft};
            border-radius: 8px;
            overflow: hidden;
          }
          .monitor-panel-head {
            height: 46px;
            padding: 0 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid ${aura.border};
            background: ${aura.surfaceSoft};
          }
          .monitor-panel-title {
            color: ${aura.text};
            font-size: 14px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .monitor-panel-body {
            padding: 16px 18px;
          }

          /* ── 两栏布局 ── */
          .two-col {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
          }
          .three-col {
            display: grid;
            grid-template-columns: 1.4fr 1fr 1fr;
            gap: 14px;
          }

          /* ── 吞吐曲线 ── */
          .throughput-panel { grid-column: span 2; }
          .throughput-chart-container {
            width: 100%;
            overflow: hidden;
          }

          /* ── 源站网格 ── */
          .source-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
          }
          .source-block {
            background: ${aura.row};
            border: 1px solid ${aura.borderSoft};
            border-radius: 6px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: border-color 0.2s;
            cursor: pointer;
          }
          .source-block:hover {
            border-color: ${aura.border};
          }
          .source-block-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
          }
          .source-name {
            color: ${aura.text};
            font-size: 13px;
            font-weight: 700;
            font-family: 'Fira Code', monospace;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .source-type-badge {
            font-size: 10px;
            color: ${aura.subtle};
            background: ${aura.surfaceSoft};
            padding: 2px 6px;
            border-radius: 4px;
            border: 1px solid ${aura.borderSoft};
          }
          .source-qps {
            color: ${aura.text};
            font-size: 20px;
            font-weight: 700;
            font-family: 'Fira Code', monospace;
            line-height: 1;
          }
          .source-qps-label {
            color: ${aura.subtle};
            font-size: 10px;
            margin-left: 4px;
          }
          .source-error {
            color: ${aura.danger};
            font-size: 11px;
            font-family: 'Fira Code', monospace;
          }

          /* ── SLI 表 ── */
          .sli-row {
            display: grid;
            grid-template-columns: minmax(120px, 1.2fr) 80px 100px 80px 100px 100px;
            align-items: center;
            height: 42px;
            border-bottom: 1px solid ${aura.borderSoft};
            font-size: 13px;
          }
          .sli-row:last-child { border-bottom: none; }
          .sli-cell {
            padding: 0 10px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .sli-header {
            color: ${aura.muted};
            font-weight: 700;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }
          .sli-value {
            color: ${aura.text};
            font-weight: 700;
            font-family: 'Fira Code', monospace;
          }

          /* ── 告警列表 ── */
          .alert-item {
            display: flex;
            gap: 12px;
            padding: 14px 0;
            border-bottom: 1px solid ${aura.borderSoft};
          }
          .alert-item:last-child { border-bottom: none; }
          .alert-level-tag {
            width: 36px;
            height: 24px;
            border-radius: 4px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 800;
            font-family: 'Fira Code', monospace;
            flex-shrink: 0;
          }
          .alert-content { flex: 1; min-width: 0; }
          .alert-title {
            color: ${aura.text};
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 4px;
          }
          .alert-desc {
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.5;
          }
          .alert-meta {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 6px;
          }
          .alert-time {
            color: ${aura.subtle};
            font-size: 11px;
            font-family: 'Fira Code', monospace;
          }

          /* ── DAG 任务 ── */
          .dag-row {
            display: grid;
            grid-template-columns: minmax(100px, 1.4fr) 90px 80px 60px;
            align-items: center;
            height: 40px;
            border-bottom: 1px solid ${aura.borderSoft};
            font-size: 13px;
          }
          .dag-row:last-child { border-bottom: none; }
          .dag-cell {
            padding: 0 8px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .dag-name {
            color: ${aura.text};
            font-weight: 600;
            font-family: 'Fira Code', monospace;
            font-size: 12px;
          }

          /* ── 存储层 ── */
          .storage-row {
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding: 10px 0;
            border-bottom: 1px solid ${aura.borderSoft};
          }
          .storage-row:last-child { border-bottom: none; }
          .storage-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
          }
          .storage-name {
            color: ${aura.text};
            font-weight: 700;
            font-size: 13px;
            font-family: 'Fira Code', monospace;
          }
          .storage-total {
            color: ${aura.text};
            font-weight: 800;
            font-family: 'Fira Code', monospace;
            font-size: 15px;
          }
          .storage-bar {
            height: 6px;
            background: ${aura.surfaceSoft};
            border-radius: 3px;
            overflow: hidden;
          }
          .storage-bar-fill {
            height: 100%;
            border-radius: 3px;
          }
          .storage-delta {
            color: ${aura.green};
            font-size: 11px;
            font-family: 'Fira Code', monospace;
            font-weight: 600;
          }

          /* ── Toggle ── */
          .noise-toggle {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            color: ${aura.muted};
            font-size: 12px;
            font-weight: 600;
          }
          .noise-toggle-switch {
            width: 32px;
            height: 18px;
            border-radius: 9px;
            background: ${aura.surfaceSoft};
            border: 1px solid ${aura.border};
            position: relative;
            transition: background 0.2s;
          }
          .noise-toggle-switch.on {
            background: ${aura.purple}33;
            border-color: ${aura.purple};
          }
          .noise-toggle-switch::after {
            content: '';
            position: absolute;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: ${aura.muted};
            top: 2px;
            left: 2px;
            transition: all 0.2s;
          }
          .noise-toggle-switch.on::after {
            background: ${aura.purple};
            left: 16px;
          }
        `}
      </style>

      <div className="monitor-board">
        {/* ── Header ── */}
        <header className="monitor-header">
          <div className="monitor-title-row">
            <Space size={10}>
              <RadarChartOutlined style={{ color: aura.cyan, fontSize: 22 }} />
              <h1>实时监控</h1>
              <span className="monitor-live-badge">
                <span className="monitor-live-dot" />
                LIVE · tick {liveTick}
              </span>
            </Space>
            <div className="monitor-actions">
              <Input
                prefix={<SearchOutlined />}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索任务 / 源站 / 指标"
                style={{ width: 240 }}
              />
              <Segmented
                value={view}
                onChange={(v) => setView(v as ViewMode)}
                options={[
                  { label: '总览', value: 'overview' },
                  { label: '源站', value: 'source' },
                  { label: '容量', value: 'capacity' },
                ]}
              />
              <Button icon={<ReloadOutlined />} />
            </div>
          </div>

          <nav className="monitor-tabs">
            <span className="monitor-tab is-active"><LineChartOutlined /> 指标面板</span>
            <span className="monitor-tab"><AlertOutlined /> 告警中心</span>
            <span className="monitor-tab"><DatabaseOutlined /> 存储概览</span>
            <span className="monitor-tab"><ThunderboltOutlined /> 容量规划</span>
          </nav>
        </header>

        {/* ── Content ── */}
        <main className="monitor-content">
          {/* ── KPI 卡片行 ── */}
          <div className="kpi-grid">
            {kpis.map((kpi) => (
              <div className="kpi-card" key={kpi.key}>
                <div className="kpi-header">
                  <span className="kpi-label">{kpi.label}</span>
                  <span className="kpi-icon" style={{ background: `${kpi.color}15`, color: kpi.color }}>
                    {kpi.icon}
                  </span>
                </div>
                <div className="kpi-value-row">
                  <span className="kpi-value" style={{ color: kpi.color }}>{kpi.value}</span>
                  <span className="kpi-unit">{kpi.unit}</span>
                </div>
                <div className="kpi-sparkline">
                  <Sparkline data={kpi.sparkline} color={kpi.color} width={180} height={32} />
                </div>
                <span className="kpi-trend" style={{ color: kpi.trendDir === 'up' ? aura.green : kpi.trendDir === 'down' ? aura.blue : aura.muted }}>
                  {kpi.trend}
                </span>
              </div>
            ))}
          </div>

          {/* ── 吞吐曲线 + 告警面板 ── */}
          <div className="two-col">
            {/* 吞吐曲线 */}
            <div className="monitor-panel throughput-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <LineChartOutlined style={{ color: aura.cyan }} />
                  数据流吞吐 · records/min
                </span>
                <Space size={16}>
                  {throughputLayers.map((layer) => (
                    <span key={layer.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: layer.color, display: 'inline-block' }} />
                      <span style={{ color: aura.muted, fontSize: 11, fontFamily: 'Fira Code, monospace' }}>{layer.name}</span>
                    </span>
                  ))}
                </Space>
              </div>
              <div className="monitor-panel-body" style={{ padding: '12px 18px' }}>
                <div className="throughput-chart-container">
                  <ThroughputChart />
                </div>
              </div>
            </div>

            {/* 告警收敛 */}
            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <AlertOutlined style={{ color: aura.amber }} />
                  告警收敛
                  <Tag style={{ background: aura.danger + '22', border: 'none', color: aura.danger, fontSize: 11 }}>{firingAlerts.filter((a) => a.status === 'firing').length} firing</Tag>
                </span>
                <span
                  className="noise-toggle"
                  onClick={() => setNoiseFilter(!noiseFilter)}
                >
                  <span className={`noise-toggle-switch ${noiseFilter ? 'on' : ''}`} />
                  噪音抑制
                </span>
              </div>
              <div className="monitor-panel-body" style={{ maxHeight: 260, overflow: 'auto' }}>
                {firingAlerts.map((alert) => {
                  const levelMeta = alertLevelMeta[alert.level];
                  const statusMeta = alertStatusMeta[alert.status];
                  return (
                    <div className="alert-item" key={alert.key}>
                      <span
                        className="alert-level-tag"
                        style={{ background: levelMeta.bg, color: levelMeta.color, border: `1px solid ${levelMeta.color}44` }}
                      >
                        {alert.level}
                      </span>
                      <div className="alert-content">
                        <div className="alert-title">{alert.title}</div>
                        <div className="alert-desc">{alert.description}</div>
                        <div className="alert-meta">
                          <span style={{ color: statusMeta.color, fontSize: 11, fontWeight: 600 }}>
                            {statusMeta.icon} {statusMeta.label}
                          </span>
                          <span className="alert-time">{alert.time}</span>
                          <span className="alert-time">· {alert.source}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* ── 源站状态网格 ── */}
          <div className="monitor-panel">
            <div className="monitor-panel-head">
              <span className="monitor-panel-title">
                <CloudServerOutlined style={{ color: aura.purple }} />
                采集源站实时状态
                <span style={{ color: aura.subtle, fontSize: 12, fontWeight: 400 }}>
                  {filteredSources.length} 个源站 · {filteredSources.filter((s) => s.health === 'healthy').length} 正常 · {filteredSources.filter((s) => s.health === 'warning').length} 告警 · {filteredSources.filter((s) => s.health === 'critical').length} 异常
                </span>
              </span>
              <Button size="small" type="text" style={{ color: aura.muted }}>
                <FilterOutlined /> 筛选
              </Button>
            </div>
            <div className="monitor-panel-body">
              <div className="source-grid">
                {filteredSources.map((src) => {
                  const meta = healthMeta[src.health];
                  const typeMeta = sourceTypeMeta[src.type];
                  return (
                    <Tooltip
                      key={src.key}
                      title={`${src.name} · ${typeMeta.label} · 错误率 ${src.errorRate}% · 最近心跳 ${src.lastTick}前`}
                      placement="top"
                    >
                      <div className="source-block" style={{ borderColor: src.health === 'critical' ? aura.danger + '44' : src.health === 'warning' ? aura.amber + '44' : aura.borderSoft }}>
                        <div className="source-block-head">
                          <span className="source-name">{src.name}</span>
                          {renderHealthDot(src.health)}
                        </div>
                        <div>
                          <span className="source-qps" style={{ color: src.health === 'critical' ? aura.danger : src.health === 'idle' ? aura.subtle : aura.text }}>
                            {src.qps}
                          </span>
                          <span className="source-qps-label">qps</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span className="source-type-badge">{typeMeta.icon} {typeMeta.label}</span>
                          {src.errorRate > 0 && (
                            <span className="source-error" style={{ color: src.errorRate > 10 ? aura.danger : src.errorRate > 1 ? aura.amber : aura.muted }}>
                              {src.errorRate}% err
                            </span>
                          )}
                        </div>
                      </div>
                    </Tooltip>
                  );
                })}
              </div>
            </div>
          </div>

          {/* ── SLI 指标表 + DAG 任务 + 存储层 ── */}
          <div className="three-col">
            {/* SLI 指标表 */}
            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <FieldTimeOutlined style={{ color: aura.blue }} />
                  服务水平信号 (SLI)
                </span>
              </div>
              <div className="monitor-panel-body" style={{ padding: 0 }}>
                <div className="sli-row">
                  <div className="sli-cell sli-header">信号</div>
                  <div className="sli-cell sli-header">当前值</div>
                  <div className="sli-cell sli-header">阈值</div>
                  <div className="sli-cell sli-header">状态</div>
                  <div className="sli-cell sli-header">趋势</div>
                  <div className="sli-cell sli-header">负责人</div>
                </div>
                {sliMetrics.map((row) => (
                  <div className="sli-row" key={row.key}>
                    <div className="sli-cell" style={{ color: aura.text, fontWeight: 600 }}>{row.signal}</div>
                    <div className="sli-cell sli-value">{row.value}</div>
                    <div className="sli-cell" style={{ color: aura.muted, fontFamily: 'Fira Code, monospace', fontSize: 12 }}>{row.threshold}</div>
                    <div className="sli-cell">
                      {renderHealthDot(row.status === 'healthy' ? 'healthy' : row.status === 'warning' ? 'warning' : 'critical')}
                      <span style={{ color: row.status === 'healthy' ? aura.green : row.status === 'warning' ? aura.amber : aura.danger, fontSize: 11, fontWeight: 600, marginLeft: 6 }}>
                        {row.status === 'healthy' ? 'OK' : row.status === 'warning' ? 'WARN' : 'CRIT'}
                      </span>
                    </div>
                    <div className="sli-cell">
                      <Sparkline data={row.sparkline} color={row.status === 'healthy' ? aura.green : row.status === 'warning' ? aura.amber : aura.danger} width={80} height={24} fill={false} />
                    </div>
                    <div className="sli-cell" style={{ color: aura.muted, fontSize: 12 }}>{row.owner}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* DAG 任务概览 */}
            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <SyncOutlined style={{ color: aura.purple }} />
                  DAG 任务概览
                </span>
                <span style={{ color: aura.subtle, fontSize: 11 }}>
                  {dagTasks.filter((t) => t.status === 'running').length} 运行 · {dagTasks.filter((t) => t.status === 'queued').length} 排队 · {dagTasks.filter((t) => t.status === 'failed').length} 失败
                </span>
              </div>
              <div className="monitor-panel-body" style={{ padding: 0 }}>
                <div className="dag-row">
                  <div className="dag-cell sli-header">任务</div>
                  <div className="dag-cell sli-header">记录数</div>
                  <div className="dag-cell sli-header">耗时</div>
                  <div className="dag-cell sli-header">状态</div>
                </div>
                {dagTasks.map((task) => {
                  const meta = dagStatusMeta[task.status];
                  return (
                    <div className="dag-row" key={task.key}>
                      <div className="dag-cell dag-name">{task.name}</div>
                      <div className="dag-cell" style={{ color: aura.muted, fontFamily: 'Fira Code, monospace', fontSize: 12 }}>{task.records}</div>
                      <div className="dag-cell" style={{ color: aura.muted, fontFamily: 'Fira Code, monospace', fontSize: 12 }}>{task.duration}</div>
                      <div className="dag-cell">
                        <span style={{ color: meta.color, fontSize: 11, fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          {meta.icon} {meta.label}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 存储层记录数 */}
            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <DatabaseOutlined style={{ color: aura.green }} />
                  存储层记录数
                </span>
              </div>
              <div className="monitor-panel-body">
                {storageLayers.map((layer) => (
                  <div className="storage-row" key={layer.name}>
                    <div className="storage-head">
                      <span className="storage-name">{layer.name}</span>
                      <span className="storage-total">{layer.total}</span>
                    </div>
                    <div className="storage-bar">
                      <div className="storage-bar-fill" style={{ width: `${layer.utilization}%`, background: layer.color }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span className="storage-delta">{layer.todayDelta} 今日</span>
                      <span style={{ color: aura.subtle, fontSize: 11, fontFamily: 'Fira Code, monospace' }}>{layer.utilization}% 已用</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ── 底部入口 ── */}
          <div className="two-col">
            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <WarningOutlined style={{ color: aura.amber }} />
                  错误分布
                </span>
              </div>
              <div className="monitor-panel-body">
                {[
                  { label: '字段缺失', count: 42, pct: 58, color: aura.amber },
                  { label: '网络超时', count: 18, pct: 25, color: aura.blue },
                  { label: '身份拦截', count: 8, pct: 11, color: aura.danger },
                  { label: '解析异常', count: 4, pct: 6, color: aura.purple },
                ].map((item) => (
                  <div key={item.label} style={{ marginBottom: 14 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ color: aura.text, fontSize: 13, fontWeight: 600 }}>{item.label}</span>
                      <span style={{ color: aura.muted, fontFamily: 'Fira Code, monospace', fontSize: 12 }}>{item.count} 次 · {item.pct}%</span>
                    </div>
                    <div className="storage-bar">
                      <div className="storage-bar-fill" style={{ width: `${item.pct}%`, background: item.color }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="monitor-panel">
              <div className="monitor-panel-head">
                <span className="monitor-panel-title">
                  <ArrowRightOutlined style={{ color: aura.cyan }} />
                  日志 & 追踪入口
                </span>
              </div>
              <div className="monitor-panel-body">
                <div style={{ color: aura.muted, fontSize: 13, lineHeight: 1.7, marginBottom: 16 }}>
                  监控面板只保留可行动摘要。深入排查请使用日志追踪页面，支持 trace 链路、服务过滤和等级筛选。
                </div>
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Button block icon={<ArrowRightOutlined />} href="/logs" style={{ background: aura.purple + '15', borderColor: aura.purple + '44', color: aura.text }}>
                    打开日志追踪
                  </Button>
                  <Button block icon={<ArrowRightOutlined />} href="/tasks" style={{ background: 'transparent', borderColor: aura.border, color: aura.muted }}>
                    查看任务中心
                  </Button>
                </Space>
              </div>
            </div>
          </div>
        </main>
      </div>
    </ErrorBoundary>
  );
};

export default Monitoring;

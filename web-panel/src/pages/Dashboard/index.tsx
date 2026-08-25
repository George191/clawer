import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRightOutlined, CheckCircleFilled, ExclamationCircleFilled } from '@ant-design/icons';
import { App, Button, Select, Skeleton } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useWebSocket } from '@/hooks/useWebSocket';
import workspacePalette from '@/pages/AICollect/palette';
import { DASHBOARD_WS_URL } from '@/services/api';
import type { Alert, DashboardMetrics, PipelineNodeData } from '@/services/types';
import { useDashboardStore } from '@/stores/dashboard';
import './style.css';

type TimeWindow = 15 | 30 | 60;

interface DashboardSnapshot {
  type: 'dashboard_snapshot';
  timestamp?: string;
  data?: {
    metrics?: DashboardMetrics;
    alerts?: Alert[];
  };
}

const PIPELINE_STAGES = ['Crawl', 'RDS', 'ODS', 'TASK', 'DWD', 'DWS', 'ADS'];
const numberFormatter = new Intl.NumberFormat('zh-CN');
const compactFormatter = new Intl.NumberFormat('zh-CN', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const formatNumber = (value: unknown) =>
  isFiniteNumber(value) ? numberFormatter.format(value) : '--';

const formatCompact = (value: unknown) =>
  isFiniteNumber(value) ? compactFormatter.format(value) : '--';

const formatTime = (value?: string | null) => {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

const formatAlertTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const makeLinePath = (values: number[], width: number, height: number, padding = 8) => {
  if (!values.length) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map((value, index) => {
    const x = values.length === 1
      ? width / 2
      : padding + (index / (values.length - 1)) * (width - padding * 2);
    const y = padding + ((max - value) / range) * (height - padding * 2);
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
};

const Sparkline: React.FC<{ values: number[]; tone?: 'accent' | 'warning' }> = ({
  values,
  tone = 'accent',
}) => {
  if (!values.length) return <span className="dashboard-spark-empty">暂无趋势</span>;
  const path = makeLinePath(values, 104, 34, 3);
  return (
    <svg className={`dashboard-spark dashboard-spark--${tone}`} viewBox="0 0 104 34" aria-hidden="true">
      <path d={path} />
    </svg>
  );
};

const TrendChart: React.FC<{
  points: { ts: string; v: number }[];
  alerts: Alert[];
}> = ({ points, alerts }) => {
  const width = 760;
  const height = 230;
  const paddingX = 18;
  const paddingY = 18;
  const values = points.map((point) => point.v);
  const path = makeLinePath(values, width, height, paddingY);
  const firstTime = points.length ? new Date(points[0].ts).getTime() : Number.NaN;
  const lastTime = points.length ? new Date(points[points.length - 1].ts).getTime() : Number.NaN;
  const eventMarkers = alerts.flatMap((alert) => {
    const time = new Date(alert.time).getTime();
    if (!Number.isFinite(time) || !Number.isFinite(firstTime) || !Number.isFinite(lastTime)) return [];
    if (time < firstTime || time > lastTime || lastTime === firstTime) return [];
    return [{
      id: alert.id,
      x: paddingX + ((time - firstTime) / (lastTime - firstTime)) * (width - paddingX * 2),
      level: alert.level,
    }];
  });

  if (!points.length) {
    return <div className="dashboard-chart-empty">当前时间范围内暂无吞吐趋势</div>;
  }

  return (
    <div className="dashboard-chart-wrap">
      <svg className="dashboard-trend-chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="ETL 吞吐趋势">
        {[0.25, 0.5, 0.75].map((ratio) => (
          <line key={ratio} x1="0" x2={width} y1={height * ratio} y2={height * ratio} className="dashboard-grid-line" />
        ))}
        <defs>
          <linearGradient id="dashboardTrendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={workspacePalette.accent} stopOpacity="0.24" />
            <stop offset="1" stopColor={workspacePalette.accent} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={`${path} L ${width - paddingY} ${height - paddingY} L ${paddingY} ${height - paddingY} Z`} className="dashboard-trend-area" />
        <path d={path} className="dashboard-trend-line" />
        {eventMarkers.map((marker) => (
          <g key={marker.id}>
            <line x1={marker.x} x2={marker.x} y1="10" y2={height - 12} className={`dashboard-event-line dashboard-event-line--${marker.level}`} />
            <circle cx={marker.x} cy="18" r="4" className={`dashboard-event-dot dashboard-event-dot--${marker.level}`} />
          </g>
        ))}
      </svg>
      <div className="dashboard-chart-axis">
        <span>{formatTime(points[0]?.ts)}</span>
        <span>{formatTime(points[points.length - 1]?.ts)}</span>
      </div>
    </div>
  );
};

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const {
    metrics,
    alerts,
    loading,
    error,
    updatedAt,
    fetchMetrics,
    fetchAlerts,
    applySnapshot,
  } = useDashboardStore();
  const [timeWindow, setTimeWindow] = useState<TimeWindow>(15);
  const [hasConnected, setHasConnected] = useState(false);
  const [connectionInterrupted, setConnectionInterrupted] = useState(false);
  const [waitingForSnapshot, setWaitingForSnapshot] = useState(true);
  const fallbackRequested = useRef(false);

  const handleMessage = useCallback((raw: string) => {
    try {
      const message = JSON.parse(raw) as DashboardSnapshot;
      if (message.type !== 'dashboard_snapshot' || !message.data?.metrics) return;
      applySnapshot(
        message.data.metrics,
        Array.isArray(message.data.alerts) ? message.data.alerts : [],
        message.timestamp || new Date().toISOString(),
      );
      setWaitingForSnapshot(false);
    } catch {
      // Ignore messages that do not belong to the dashboard channel.
    }
  }, [applySnapshot]);

  const { connected, send } = useWebSocket(DASHBOARD_WS_URL, {
    onMessage: handleMessage,
    onOpen: () => {
      setHasConnected(true);
      setConnectionInterrupted(false);
    },
    onClose: () => setConnectionInterrupted(true),
  });

  useEffect(() => {
    if (!connected) return;
    setWaitingForSnapshot(true);
    send(JSON.stringify({ type: 'subscribe', channel: 'dashboard:main' }));
  }, [connected, send]);

  useEffect(() => {
    const fallbackTimer = window.setTimeout(() => {
      if (useDashboardStore.getState().metrics || fallbackRequested.current) return;
      fallbackRequested.current = true;
      void Promise.all([fetchMetrics(), fetchAlerts()]).finally(() => setWaitingForSnapshot(false));
    }, 6000);
    return () => window.clearTimeout(fallbackTimer);
  }, [fetchAlerts, fetchMetrics]);

  useEffect(() => {
    if (!error) return;
    message.warning({
      key: 'dashboard-data-unavailable',
      content: '部分实时数据暂不可用，系统将继续重试。',
      duration: 3,
    });
  }, [error, message]);

  const activeAlerts = useMemo(
    () => alerts.filter((alert) => alert.status === 'active'),
    [alerts],
  );

  const history = useMemo(() => {
    const rawHistory = metrics?.etl_throughput?.history;
    if (!Array.isArray(rawHistory)) return [];
    const cutoff = Date.now() - timeWindow * 60 * 1000;
    return rawHistory.filter((point) => {
      const timestamp = new Date(point.ts).getTime();
      return Number.isFinite(timestamp) && timestamp >= cutoff && isFiniteNumber(point.v);
    });
  }, [metrics, timeWindow]);

  const taskCounts = metrics?.tasks;
  const failedTasks = isFiniteNumber(taskCounts?.failed) ? taskCounts.failed : 0;
  const hasAttention = activeAlerts.length > 0 || failedTasks > 0;
  const topAlerts = activeAlerts.slice(0, 3);

  const summaryText = metrics
    ? `当前 ${formatNumber(taskCounts?.running)} 个任务运行中，ETL 处理速率 ${formatNumber(metrics.etl_throughput?.current)} msg/s，Kafka Lag 为 ${formatNumber(metrics.kafka_lag?.total)}，失败任务 ${formatNumber(taskCounts?.failed)} 个。`
    : '正在等待实时快照，收到数据后将在这里汇总当前运行情况。';

  const largestLag = useMemo(() => {
    const entries = Object.entries(metrics?.kafka_lag?.by_layer || {})
      .filter((entry): entry is [string, number] => isFiniteNumber(entry[1]));
    return entries.sort((a, b) => b[1] - a[1])[0];
  }, [metrics]);

  const pipelineNodes = Array.isArray(metrics?.pipeline_nodes) ? metrics.pipeline_nodes : [];
  const runningNodes = pipelineNodes.filter((node) => node.status === 'running').length;
  const errorNodes = pipelineNodes.filter((node) => node.status === 'error').length;
  const errorRateValues = Array.isArray(metrics?.error_rate_history)
    ? metrics.error_rate_history.map((point) => point.v).filter(isFiniteNumber)
    : [];
  const latestErrorRate = errorRateValues[errorRateValues.length - 1];
  const errorThreshold = metrics?.error_threshold;
  const lagHistoryValues = Array.isArray(metrics?.kafka_lag_history)
    ? metrics.kafka_lag_history.map((point) => point.v).filter(isFiniteNumber)
    : [];
  const latestLagSample = lagHistoryValues[lagHistoryValues.length - 1];
  const previousLagSample = lagHistoryValues[lagHistoryValues.length - 2];
  const lagDelta = isFiniteNumber(latestLagSample) && isFiniteNumber(previousLagSample)
    ? latestLagSample - previousLagSample
    : undefined;
  const resolvePipelineStage = (stage: string): { node?: PipelineNodeData; lag?: number } => {
    const node = pipelineNodes.find((item) => item.name?.toLowerCase() === stage.toLowerCase());
    if (node) return { node };
    const lagEntry = Object.entries(metrics?.kafka_lag?.by_layer || {})
      .find(([key]) => key.toLowerCase() === stage.toLowerCase());
    return { lag: lagEntry && isFiniteNumber(lagEntry[1]) ? lagEntry[1] : undefined };
  };

  const distribution = [
    { key: 'running', label: '运行中', value: taskCounts?.running, color: workspacePalette.accent },
    { key: 'completed', label: '已完成', value: taskCounts?.completed, color: workspacePalette.success },
    { key: 'failed', label: '失败', value: taskCounts?.failed, color: workspacePalette.danger },
  ];
  const distributionTotal = distribution.reduce(
    (sum, item) => sum + (isFiniteNumber(item.value) ? Math.max(item.value, 0) : 0),
    0,
  );
  let ringOffset = 0;
  const ringStops = distribution.map((item) => {
    const start = distributionTotal ? (ringOffset / distributionTotal) * 100 : 0;
    ringOffset += isFiniteNumber(item.value) ? Math.max(item.value, 0) : 0;
    const end = distributionTotal ? (ringOffset / distributionTotal) * 100 : 0;
    return `${item.color} ${start}% ${end}%`;
  });
  const ringStyle = distributionTotal
    ? { background: `conic-gradient(${ringStops.join(', ')})` }
    : undefined;

  const wsLabel = connected
    ? waitingForSnapshot ? 'WS 已连接 · 等待快照' : 'WS 已连接'
    : metrics ? '数据更新已暂停' : connectionInterrupted || hasConnected ? 'WS 重连中' : 'WS 连接中';
  const wsTone = connected ? 'connected' : connectionInterrupted || hasConnected || metrics ? 'stale' : 'connecting';
  const showLoading = !metrics && (loading || waitingForSnapshot);

  return (
    <div className="dashboard-v2">
      {showLoading ? (
        <div className="dashboard-loading" aria-label="正在加载 Dashboard">
          <Skeleton active paragraph={{ rows: 14 }} title={{ width: '28%' }} />
        </div>
      ) : (
        <>
          <section className="dashboard-hero-grid">
            <article className={`dashboard-card dashboard-summary ${hasAttention ? 'dashboard-summary--attention' : ''}`}>
              <div className="dashboard-summary-top">
                <div className={`dashboard-summary-kicker ${hasAttention ? 'dashboard-summary-kicker--attention' : ''}`}>
                  {hasAttention ? <ExclamationCircleFilled /> : <CheckCircleFilled />}
                  {hasAttention ? '存在待处理事项' : metrics ? '运行状态良好' : '等待实时数据'}
                </div>
                <div className="dashboard-controls">
                  <Select<TimeWindow>
                    value={timeWindow}
                    onChange={setTimeWindow}
                    className="dashboard-time-select"
                    options={[
                      { value: 15, label: '最近 15 分钟' },
                      { value: 30, label: '最近 30 分钟' },
                      { value: 60, label: '最近 1 小时' },
                    ]}
                  />
                  <span className="dashboard-context-pill">全部来源</span>
                  <span className={`dashboard-connection dashboard-connection--${wsTone}`}>
                    <i />
                    <span>{wsLabel}</span>
                    {updatedAt && <small>更新于 {formatTime(updatedAt)}</small>}
                  </span>
                </div>
              </div>
              <h2>{!metrics ? '等待实时运行快照' : hasAttention ? '有事项需要关注' : '今日数据链路运行平稳'}</h2>
              <p>{summaryText}</p>
              <div className="dashboard-summary-actions">
                <Button type="primary" onClick={() => navigate('/tasks')}>查看运行任务 <ArrowRightOutlined /></Button>
                <Button onClick={() => navigate('/monitor')}>打开实时监控</Button>
              </div>
            </article>

            <aside className="dashboard-card dashboard-attention">
              <div className="dashboard-section-head">
                <div>
                  <h3>需要关注</h3>
                  <p>影响当前运行的活动事项</p>
                </div>
                <span>{topAlerts.length || failedTasks ? `${topAlerts.length || 1} 个进行中` : '暂无事项'}</span>
              </div>
              <div className="dashboard-attention-list">
                {topAlerts.map((alert) => (
                  <button type="button" className="dashboard-issue" key={alert.id} onClick={() => navigate('/monitor')}>
                    <i className={`dashboard-severity dashboard-severity--${alert.level}`} />
                    <span>
                      <b>{alert.message}</b>
                      <small>{alert.source || '未知来源'} · {formatAlertTime(alert.time)}</small>
                    </span>
                    <ArrowRightOutlined />
                  </button>
                ))}
                {!topAlerts.length && failedTasks > 0 && (
                  <button type="button" className="dashboard-issue" onClick={() => navigate('/tasks')}>
                    <i className="dashboard-severity dashboard-severity--critical" />
                    <span>
                      <b>{failedTasks} 个任务处于失败状态</b>
                      <small>前往任务中心查看失败原因和重试状态</small>
                    </span>
                    <ArrowRightOutlined />
                  </button>
                )}
                {!topAlerts.length && failedTasks === 0 && (
                  <div className="dashboard-healthy-empty">
                    <CheckCircleFilled />
                    <div><b>当前没有需要处理的告警</b><span>数据链路将持续通过 WebSocket 更新</span></div>
                  </div>
                )}
              </div>
            </aside>
          </section>

          <section className="dashboard-kpi-grid">
            <article className="dashboard-card dashboard-kpi">
              <div className="dashboard-kpi-head"><span>运行任务</span><em>{failedTasks ? '有失败' : metrics ? '正常' : '--'}</em></div>
              <div className="dashboard-kpi-value">{formatNumber(taskCounts?.running)} <small>/ {formatNumber(taskCounts?.total)}</small></div>
              <div className="dashboard-kpi-foot"><span>当前活动 / 任务总数</span></div>
            </article>
            <article className="dashboard-card dashboard-kpi">
              <div className="dashboard-kpi-head"><span>今日新增</span><em>records</em></div>
              <div className="dashboard-kpi-value">{formatCompact(metrics?.data_volume?.daily_increment)}</div>
              <div className="dashboard-kpi-foot"><span>累计 {formatCompact(metrics?.data_volume?.total)}</span></div>
            </article>
            <article className="dashboard-card dashboard-kpi">
              <div className="dashboard-kpi-head"><span>ETL 吞吐</span><em>msg/s</em></div>
              <div className="dashboard-kpi-value">{formatCompact(metrics?.etl_throughput?.current)} <small>msg/s</small></div>
              <div className="dashboard-kpi-foot"><span>最近 {timeWindow} 分钟</span><Sparkline values={history.map((point) => point.v)} /></div>
            </article>
            <article className="dashboard-card dashboard-kpi">
              <div className="dashboard-kpi-head"><span>Kafka Lag</span><em className={isFiniteNumber(metrics?.kafka_lag?.total) && metrics!.kafka_lag.total > 0 ? 'is-warning' : ''}>消息积压</em></div>
              <div className="dashboard-kpi-value">{formatNumber(metrics?.kafka_lag?.total)}</div>
              <div className="dashboard-kpi-foot">
                <span>{largestLag ? `最大 ${largestLag[0]} · ${formatNumber(largestLag[1])}` : '暂无分层数据'}</span>
              </div>
            </article>
          </section>

          <section className="dashboard-analytics-grid">
            <article className="dashboard-card dashboard-chart-card">
              <div className="dashboard-panel-head">
                <div><h3>处理速率与数据流入</h3><p>最近 {timeWindow} 分钟 · 仅叠加落在当前时间范围内的告警事件</p></div>
                <span>最新 {formatNumber(metrics?.etl_throughput?.current)} msg/s</span>
              </div>
              <TrendChart points={history} alerts={activeAlerts} />
            </article>

            <article className="dashboard-card dashboard-distribution-card">
              <div className="dashboard-panel-head">
                <div><h3>任务状态</h3><p>{formatNumber(taskCounts?.total)} 个任务</p></div>
                <button type="button" onClick={() => navigate('/tasks')}>查看全部 <ArrowRightOutlined /></button>
              </div>
              <div className="dashboard-distribution">
                <div className={`dashboard-ring ${distributionTotal ? '' : 'dashboard-ring--empty'}`} style={ringStyle}>
                  <div><b>{formatNumber(metrics ? distributionTotal : undefined)}</b><span>已分类</span></div>
                </div>
                <div className="dashboard-distribution-list">
                  {distribution.map((item) => (
                    <div key={item.key}><span><i style={{ background: item.color }} />{item.label}</span><b>{formatNumber(item.value)}</b></div>
                  ))}
                </div>
              </div>
              <div className="dashboard-distribution-note">
                状态构成仅使用当前任务计数，不对未提供的状态进行推算。
              </div>
            </article>
          </section>

          <section className="dashboard-card dashboard-pipeline">
            <div className="dashboard-section-head">
              <div><h3>数据管道健康</h3><p>从采集到服务的实时处理状态</p></div>
              <button type="button" onClick={() => navigate('/monitor')}>查看管道详情 <ArrowRightOutlined /></button>
            </div>
            <div className="dashboard-pipeline-flow">
              {PIPELINE_STAGES.map((stage) => {
                const { node, lag } = resolvePipelineStage(stage);
                const value = node && isFiniteNumber(node.throughput)
                  ? `${formatCompact(node.throughput)}/s`
                  : isFiniteNumber(lag) ? `Lag ${formatNumber(lag)}` : '--';
                const detail = node && isFiniteNumber(node.lag) ? `Lag ${formatNumber(node.lag)}` : '实时指标未提供';
                return (
                  <button type="button" className={`dashboard-stage dashboard-stage--${node?.status || 'unknown'}`} key={stage} onClick={() => navigate('/monitor')}>
                    <span><b>{stage}</b><i /></span>
                    <strong>{value}</strong>
                    <small>{value === '--' ? '实时指标未提供' : detail}</small>
                  </button>
                );
              })}
            </div>
            <div className="dashboard-signal-grid">
              <div className="dashboard-signal">
                <span>错误率</span>
                <strong className={isFiniteNumber(latestErrorRate) && isFiniteNumber(errorThreshold) && latestErrorRate > errorThreshold ? 'is-warning' : ''}>
                  {isFiniteNumber(latestErrorRate) ? `${latestErrorRate.toFixed(2)}%` : '--'}
                </strong>
                <small>阈值 {isFiniteNumber(errorThreshold) ? `${errorThreshold.toFixed(2)}%` : '--'}</small>
                <Sparkline values={errorRateValues} tone="warning" />
              </div>
              <div className="dashboard-signal">
                <span>积压趋势</span>
                <strong>{formatNumber(metrics?.kafka_lag?.total)}</strong>
                <small>{isFiniteNumber(lagDelta) ? `较上一采样 ${lagDelta > 0 ? '+' : ''}${formatNumber(lagDelta)}` : '暂无趋势数据'}</small>
                <Sparkline values={lagHistoryValues} tone={isFiniteNumber(lagDelta) && lagDelta > 0 ? 'warning' : 'accent'} />
              </div>
              <div className="dashboard-signal">
                <span>在线节点</span>
                <strong>{pipelineNodes.length ? `${runningNodes} / ${pipelineNodes.length}` : '--'}</strong>
                <small>{pipelineNodes.length ? `${errorNodes} 个异常节点` : '暂无节点数据'}</small>
                <div className="dashboard-signal-progress"><i style={{ width: pipelineNodes.length ? `${(runningNodes / pipelineNodes.length) * 100}%` : '0%' }} /></div>
              </div>
              <div className="dashboard-signal">
                <span>实时快照</span>
                <strong>{updatedAt ? formatTime(updatedAt) : '--'}</strong>
                <small>{connected ? 'WebSocket 已连接' : metrics ? '保留最后快照' : '等待实时快照'}</small>
                <span className={`dashboard-signal-state dashboard-signal-state--${wsTone}`}><i />{wsLabel}</span>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
};

export default Dashboard;

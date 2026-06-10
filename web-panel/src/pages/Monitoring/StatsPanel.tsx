import React, { useState, useEffect, useCallback } from 'react';
import { Card, Progress, Typography, Skeleton, theme, Button, Result } from 'antd';
import {
  ThunderboltOutlined,
  CheckCircleOutlined,
  SafetyOutlined,
  ClusterOutlined,
} from '@ant-design/icons';
import { usePolling } from '@/hooks/usePolling';
import { fetchMonitorStats } from '@/services/api';
import type { MonitorStats } from '@/services/types';
import { semanticHex } from '@/theme/tokens';

const { Text } = Typography;

// ── Mini Sparkline SVG ──
const MiniSparkline: React.FC<{ data: number[]; color: string; height?: number }> = ({ data, color, height = 32 }) => {
  if (data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const width = 100;
  const pad = 2;
  const points = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (width - pad * 2);
    const y = pad + ((max - v) / range) * (height - pad * 2);
    return `${x},${y}`;
  });
  const linePath = `M${points.join(' L')}`;
  const areaPath = `${linePath} L${width - pad},${height - pad} L${pad},${height - pad} Z`;

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#spark-grad)" />
      <path d={linePath} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
};

const StatsPanel: React.FC = () => {
  const [stats, setStats] = useState<MonitorStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<number[]>([]);
  const { token } = theme.useToken();

  const loadStats = useCallback(async () => {
    try {
      const newStats = await fetchMonitorStats();
      setStats(newStats);
      setHistory((prev) => [...prev.slice(-20), newStats.reqRate].slice(-20));
      setError(null);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setError(err?.message || '获取监控状态失败');
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    setLoading(true);
    loadStats();
  }, [loadStats]);

  // Poll every 3 seconds
  usePolling(loadStats, 3000, true);

  // ── Error state ──
  if (error && !stats) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Card size="small" bodyStyle={{ padding: '16px' }}>
          <Result
            status="error"
            title="加载失败"
            subTitle={error}
            extra={<Button onClick={loadStats}>重试</Button>}
          />
        </Card>
      </div>
    );
  }

  // ── Loading state ──
  if (loading || !stats) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} size="small" bodyStyle={{ padding: '16px' }}>
            <Skeleton active paragraph={{ rows: 1 }} />
          </Card>
        ))}
      </div>
    );
  }

  const proxyPercent = Math.round((stats.proxyAvailable / stats.proxyTotal) * 100);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Inline error banner */}
      {error && (
        <Card size="small" bodyStyle={{ padding: '8px 12px' }}>
          <Text type="danger" style={{ fontSize: 12 }}>{error}</Text>
          <Button type="link" size="small" onClick={loadStats} style={{ fontSize: 12 }}>重试</Button>
        </Card>
      )}

      {/* Request Rate */}
      <Card size="small" bodyStyle={{ padding: '16px' }} style={{ borderLeft: '3px solid var(--theme-color-success-bg-strong)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              <ThunderboltOutlined style={{ marginRight: 4 }} />请求速率
            </Text>
            <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.2 }}>
              {stats.reqRate.toFixed(1)}
              <Text style={{ fontSize: 13, fontWeight: 400, marginLeft: 4, color: token.colorTextSecondary }}>req/s</Text>
            </div>
          </div>
        </div>
        <MiniSparkline data={history} color="var(--theme-color-success-bg-strong)" height={32} />
      </Card>

      {/* Success Rate */}
      <Card
        size="small" bodyStyle={{ padding: '16px' }}
        style={{
          borderLeft: `3px solid ${stats.successRate >= 95 ? 'var(--theme-color-success-bg-strong)' : stats.successRate >= 80 ? 'var(--theme-color-warning-bg-strong)' : 'var(--theme-color-danger-bg-strong)'}`,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              <CheckCircleOutlined style={{ marginRight: 4 }} />成功率
            </Text>
            <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.2 }}>
              {stats.successRate.toFixed(1)}
              <Text style={{ fontSize: 13, fontWeight: 400, marginLeft: 2, color: token.colorTextSecondary }}>%</Text>
            </div>
          </div>
          <Progress
            type="circle"
            percent={stats.successRate}
            size={52}
            strokeWidth={6}
            strokeColor={
              stats.successRate >= 95 ? 'var(--theme-color-success-bg-strong)' :
              stats.successRate >= 80 ? 'var(--theme-color-warning-bg-strong)' : 'var(--theme-color-danger-bg-strong)'
            }
            trailColor={token.colorFillSecondary}
            format={() => ''}
          />
        </div>
      </Card>

      {/* Anti-Crawl Triggers */}
      <Card size="small" bodyStyle={{ padding: '16px' }} style={{ borderLeft: '3px solid var(--theme-color-warning-bg-status)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              <SafetyOutlined style={{ marginRight: 4 }} />Anti-Crawl 触发
            </Text>
            <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.2, color: 'var(--theme-color-warning-text)' }}>
              {stats.antiCrawlTriggers}
              <Text style={{ fontSize: 13, fontWeight: 400, marginLeft: 4, color: token.colorTextSecondary }}>次/h</Text>
            </div>
          </div>
        </div>
        <div style={{ marginTop: 8, background: token.colorFillSecondary, borderRadius: 4, height: 6, overflow: 'hidden' }}>
          <div
            style={{
              height: '100%',
              width: `${Math.min(100, (stats.antiCrawlTriggers / 50) * 100)}%`,
              background: 'linear-gradient(90deg, var(--theme-color-warning-bg-status), var(--theme-color-danger-bg-strong))',
              borderRadius: 4, transition: 'width 0.6s ease',
            }}
          />
        </div>
      </Card>

      {/* Proxy Pool */}
      <Card size="small" bodyStyle={{ padding: '16px' }} style={{ borderLeft: '3px solid var(--theme-color-primary-bg-strong)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              <ClusterOutlined style={{ marginRight: 4 }} />代理池状态
            </Text>
            <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.2 }}>
              <span style={{ color: 'var(--theme-color-primary-text)' }}>{stats.proxyAvailable}</span>
              <Text style={{ fontSize: 18, fontWeight: 400, margin: '0 4px', color: token.colorTextQuaternary }}>/</Text>
              <Text style={{ fontSize: 18, fontWeight: 400, color: token.colorTextSecondary }}>{stats.proxyTotal}</Text>
              <Text style={{ fontSize: 13, fontWeight: 400, marginLeft: 6, color: token.colorTextSecondary }}>可用</Text>
            </div>
          </div>
          <Progress
            type="circle"
            percent={proxyPercent}
            size={52}
            strokeWidth={6}
            strokeColor={proxyPercent >= 80 ? 'var(--theme-color-success-bg-strong)' : proxyPercent >= 50 ? 'var(--theme-color-primary-bg-strong)' : 'var(--theme-color-warning-bg-status)'}
            trailColor={token.colorFillSecondary}
            format={() => `${proxyPercent}%`}
          />
        </div>
      </Card>
    </div>
  );
};

export default StatsPanel;
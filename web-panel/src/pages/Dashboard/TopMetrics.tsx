import React from 'react';
import { Row, Col, Card, theme, Empty } from 'antd';
import {
  ThunderboltOutlined,
  CloudUploadOutlined,
  HourglassOutlined,
  DatabaseOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from '@ant-design/icons';
import Sparkline from './Sparkline';
import { semanticHex } from '@/theme/tokens';
import type { DashboardMetrics } from '@/services/types';

interface TopMetricsProps {
  metrics: DashboardMetrics | null;
  loading: boolean;
}

// ── Color config per card (CSS var for icon, semantic hex for gradient alpha) ──
const cardConfigs = [
  { key: 'tasks', hex: semanticHex.primary, icon: <ThunderboltOutlined /> },
  { key: 'throughput', hex: semanticHex.success, icon: <CloudUploadOutlined /> },
  { key: 'kafkaLag', hex: semanticHex.warning, icon: <HourglassOutlined /> },
  { key: 'dataVolume', hex: semanticHex.discovery, icon: <DatabaseOutlined /> },
] as const;

const TopMetrics: React.FC<TopMetricsProps> = ({ metrics, loading }) => {
  const { token } = theme.useToken();

  if (!metrics && loading) {
    return (
      <Row gutter={[16, 16]}>
        {cardConfigs.map((c) => (
          <Col xs={24} md={12} xl={6} key={c.key}>
            <Card loading style={{ height: 160 }} />
          </Col>
        ))}
      </Row>
    );
  }

  if (!metrics) {
    return (
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Empty description="暂无指标数据" />
        </Col>
      </Row>
    );
  }

  // ── value & sparkline extractors per card ──
  const cards: {
    key: string;
    title: string;
    value: number;
    unit: string;
    subtitle?: string;
    trend: number;
    sparklineData: number[];
    color: string;
    icon: React.ReactNode;
  }[] = [
    {
      key: 'tasks',
      title: '采集任务',
      value: metrics.tasks.total,
      unit: '个',
      subtitle: `运行 ${metrics.tasks.running}  |  完成 ${metrics.tasks.completed}  |  失败 ${metrics.tasks.failed}`,
      trend: metrics.tasks.failed > 0 ? -(metrics.tasks.failed / metrics.tasks.total * 100) : 8.5,
      sparklineData: metrics.layer_throughput_history.map((p) => p.Crawl),
      color: 'var(--theme-color-primary-text)',
      icon: <ThunderboltOutlined />,
    },
    {
      key: 'throughput',
      title: 'ETL 吞吐',
      value: metrics.etl_throughput.current,
      unit: 'msg/s',
      trend: metrics.etl_throughput.current > 400 ? 5.2 : -2.8,
      sparklineData: metrics.etl_throughput.history.map((p) => p.v),
      color: 'var(--theme-color-success-text)',
      icon: <CloudUploadOutlined />,
    },
    {
      key: 'kafkaLag',
      title: 'Kafka Lag',
      value: metrics.kafka_lag.total,
      unit: '',
      trend: metrics.kafka_lag.total > 500 ? 7.3 : -12.5,
      sparklineData: metrics.kafka_lag_history.map((p) => p.v),
      color: 'var(--theme-color-warning-text)',
      icon: <HourglassOutlined />,
    },
    {
      key: 'dataVolume',
      title: '数据总量',
      value: metrics.data_volume.total,
      unit: 'TB',
      trend: metrics.data_volume.daily_increment > 0.2 ? 6.8 : -1.5,
      sparklineData: metrics.etl_throughput.history.map((p) => p.v),
      color: 'var(--theme-color-discovery-text)',
      icon: <DatabaseOutlined />,
    },
  ];

  return (
    <Row gutter={[16, 16]}>
      {cards.map((c) => {
        const trendDir = c.trend >= 0 ? 1 : -1;
        const trendColor = trendDir > 0 ? 'var(--theme-color-success-text)' : 'var(--theme-color-danger-text)';

        return (
          <Col xs={24} md={12} xl={6} key={c.key}>
            <Card
              hoverable
              style={{
                borderRadius: token.borderRadiusLG,
                transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                cursor: 'default',
                height: '100%',
              }}
              styles={{ body: { padding: '20px 24px' } }}
            >
              {/* Top row: icon + sparkline */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  marginBottom: 16,
                }}
              >
                {/* Colored icon block */}
                <div
                  style={{
                    width: 42,
                    height: 42,
                    borderRadius: 10,
                    background: `linear-gradient(135deg, ${c.hex}20, ${c.hex}35)`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 20,
                    color: c.color,
                  }}
                >
                  {c.icon}
                </div>
                {/* Mini sparkline */}
                <Sparkline data={c.sparklineData} color={c.hex} width={90} height={40} />
              </div>

              {/* Title */}
              <div
                style={{
                  fontSize: 12,
                  color: token.colorTextSecondary,
                  marginBottom: 4,
                  fontWeight: 500,
                }}
              >
                {c.title}
              </div>

              {/* Value */}
              <div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.3, marginBottom: 8, fontFamily: 'var(--theme-font-code)', fontVariantNumeric: 'tabular-nums' }}>
                {c.value.toLocaleString()}
                {c.unit && (
                  <span style={{ fontSize: 14, fontWeight: 500, color: token.colorTextSecondary, marginLeft: 4 }}>
                    {c.unit}
                  </span>
                )}
              </div>

              {/* Subtitle (task breakdown) */}
              {c.subtitle && (
                <div
                  style={{
                    fontSize: 11,
                    color: token.colorTextQuaternary,
                    marginBottom: 6,
                    lineHeight: 1.4,
                  }}
                >
                  {c.subtitle}
                </div>
              )}

              {/* Trend */}
              <div style={{ display: 'flex', alignItems: 'center' }}>
                {trendDir > 0 ? (
                  <ArrowUpOutlined style={{ color: trendColor, fontSize: 12 }} />
                ) : (
                  <ArrowDownOutlined style={{ color: trendColor, fontSize: 12 }} />
                )}
                <span style={{ color: trendColor, fontSize: 13, fontWeight: 500, marginLeft: 4 }}>
                  同比 {c.trend >= 0 ? '+' : ''}{Math.abs(c.trend).toFixed(1)}%
                </span>
              </div>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
};

export default TopMetrics;
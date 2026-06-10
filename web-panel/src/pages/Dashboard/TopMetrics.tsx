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
import type { DashboardMetrics } from '@/services/types';

interface TopMetricsProps {
  metrics: DashboardMetrics | null;
  loading: boolean;
}

const cardConfigs = [
  {
    key: 'tasks',
    icon: <ThunderboltOutlined />,
    label: '采集任务',
    gradient: 'linear-gradient(135deg, #3B82F6, #2563EB)',
    glow: 'rgba(59, 130, 246, 0.3)',
  },
  {
    key: 'throughput',
    icon: <CloudUploadOutlined />,
    label: 'ETL 吞吐',
    gradient: 'linear-gradient(135deg, #10B981, #059669)',
    glow: 'rgba(16, 185, 129, 0.3)',
  },
  {
    key: 'kafkaLag',
    icon: <HourglassOutlined />,
    label: 'Kafka Lag',
    gradient: 'linear-gradient(135deg, #F59E0B, #D97706)',
    glow: 'rgba(245, 158, 11, 0.3)',
  },
  {
    key: 'dataVolume',
    icon: <DatabaseOutlined />,
    label: '数据总量',
    gradient: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
    glow: 'rgba(139, 92, 246, 0.3)',
  },
] as const;

const TopMetrics: React.FC<TopMetricsProps> = ({ metrics, loading }) => {
  const { token } = theme.useToken();

  if (!metrics && loading) {
    return (
      <Row gutter={[16, 16]}>
        {cardConfigs.map((c) => (
          <Col xs={24} md={12} xl={6} key={c.key}>
            <Card loading style={{ height: 170, borderRadius: 12 }} />
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

  const cards = [
    {
      key: 'tasks',
      title: '采集任务',
      value: metrics.tasks.total,
      unit: '个',
      subtitle: `运行 ${metrics.tasks.running}  |  完成 ${metrics.tasks.completed}  |  失败 ${metrics.tasks.failed}`,
      trend: metrics.tasks.failed > 0 ? -(metrics.tasks.failed / Math.max(1, metrics.tasks.total) * 100) : 8.5,
      sparklineData: metrics.layer_throughput_history.map((p) => p.Crawl),
      icon: <ThunderboltOutlined />,
      gradient: 'linear-gradient(135deg, #3B82F6, #2563EB)',
      glow: 'rgba(59, 130, 246, 0.3)',
    },
    {
      key: 'throughput',
      title: 'ETL 吞吐',
      value: metrics.etl_throughput.current,
      unit: 'msg/s',
      trend: metrics.etl_throughput.current > 400 ? 5.2 : -2.8,
      sparklineData: metrics.etl_throughput.history.map((p) => p.v),
      icon: <CloudUploadOutlined />,
      gradient: 'linear-gradient(135deg, #10B981, #059669)',
      glow: 'rgba(16, 185, 129, 0.3)',
    },
    {
      key: 'kafkaLag',
      title: 'Kafka Lag',
      value: metrics.kafka_lag.total,
      unit: '',
      trend: metrics.kafka_lag.total > 500 ? 7.3 : -12.5,
      sparklineData: metrics.kafka_lag_history.map((p) => p.v),
      icon: <HourglassOutlined />,
      gradient: 'linear-gradient(135deg, #F59E0B, #D97706)',
      glow: 'rgba(245, 158, 11, 0.3)',
    },
    {
      key: 'dataVolume',
      title: '数据总量',
      value: metrics.data_volume.total,
      unit: 'TB',
      trend: metrics.data_volume.daily_increment > 0.2 ? 6.8 : -1.5,
      sparklineData: metrics.etl_throughput.history.map((p) => p.v),
      icon: <DatabaseOutlined />,
      gradient: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
      glow: 'rgba(139, 92, 246, 0.3)',
    },
  ];

  return (
    <Row gutter={[16, 16]}>
      {cards.map((c) => {
        const trendDir = c.trend >= 0 ? 1 : -1;
        const trendColor = trendDir > 0 ? '#34D399' : '#F87171';

        return (
          <Col xs={24} md={12} xl={6} key={c.key}>
            <Card
              className="premium-card"
              style={{ height: '100%', cursor: 'default' }}
              styles={{ body: { padding: '20px 24px' } }}
            >
              {/* Top: icon + sparkline */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  marginBottom: 16,
                }}
              >
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 10,
                    background: c.gradient,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 18,
                    color: '#fff',
                    boxShadow: `0 4px 12px ${c.glow}`,
                  }}
                >
                  {c.icon}
                </div>
                {c.sparklineData && c.sparklineData.length > 1 && (
                  <Sparkline data={c.sparklineData} color={trendColor} width={90} height={40} />
                )}
              </div>

              {/* Label */}
              <div
                style={{
                  fontSize: 11,
                  color: '#64748B',
                  marginBottom: 4,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                {c.title}
              </div>

              {/* Value */}
              <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.3, marginBottom: 6, color: '#F1F5F9', letterSpacing: '-0.02em' }}>
                {c.value.toLocaleString()}
                {c.unit && (
                  <span style={{ fontSize: 13, fontWeight: 500, color: '#94A3B8', marginLeft: 4 }}>
                    {c.unit}
                  </span>
                )}
              </div>

              {/* Subtitle */}
              {c.subtitle && (
                <div
                  style={{
                    fontSize: 11,
                    color: '#475569',
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
                  <ArrowUpOutlined style={{ color: trendColor, fontSize: 11 }} />
                ) : (
                  <ArrowDownOutlined style={{ color: trendColor, fontSize: 11 }} />
                )}
                <span style={{ color: trendColor, fontSize: 12, fontWeight: 600, marginLeft: 4 }}>
                  {c.trend >= 0 ? '+' : ''}{Math.abs(c.trend).toFixed(1)}%
                </span>
                <span style={{ color: '#475569', fontSize: 11, marginLeft: 4 }}>同比</span>
              </div>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
};

export default TopMetrics;
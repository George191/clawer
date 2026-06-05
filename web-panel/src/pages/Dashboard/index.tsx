import React, { useEffect } from 'react';
import { Row, Col, Badge, Typography, Space, theme, Result, Button, Spin } from 'antd';
import ErrorBoundary from '@/components/ErrorBoundary';
import { useDashboardStore } from '@/stores/dashboard';
import { usePolling } from '@/hooks/usePolling';
import TopMetrics from './TopMetrics';
import PipelineTopology from './PipelineTopology';
import AlertList from './AlertList';
import { ChartsGrid } from './Charts';

const { Title } = Typography;

const Dashboard: React.FC = () => {
  const { metrics, loading, error, fetchMetrics, fetchAlerts } = useDashboardStore();
  const { token } = theme.useToken();

  useEffect(() => {
    fetchMetrics();
    fetchAlerts();
  }, [fetchMetrics, fetchAlerts]);

  usePolling(fetchMetrics, 5000);

  // ── Error state ──
  if (error && !metrics) {
    return (
      <ErrorBoundary>
        <Result
          status="error"
          title="数据加载失败"
          subTitle={error}
          extra={
            <Button type="primary" onClick={fetchMetrics}>
              重试
            </Button>
          }
        />
      </ErrorBoundary>
    );
  }

  // ── Loading state (first load) ──
  if (loading && !metrics) {
    return (
      <ErrorBoundary>
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
          <Spin size="large" tip="正在加载仪表盘数据..." />
        </div>
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <div style={{ padding: '0 0 24px' }}>
        {/* ── Page Header ── */}
        <Space align="center" style={{ marginBottom: 20 }}>
          <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
            数据采集总览
          </Title>
          <Badge
            status="processing"
            text={<span style={{ color: token.colorTextSecondary, fontSize: 12 }}>实时更新</span>}
          />
          {/* Inline error banner for partial failures */}
          {error && metrics && (
            <Badge
              status="error"
              text={
                <Button type="link" size="small" onClick={fetchMetrics} style={{ padding: 0, fontSize: 12 }}>
                  加载失败，点击重试
                </Button>
              }
            />
          )}
        </Space>

        {/* ── Row 1: Top Metrics ── */}
        <TopMetrics metrics={metrics} loading={loading} />

        {/* ── Row 2: Pipeline Topology (full-width) ── */}
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={24}>
            <PipelineTopology nodes={metrics?.pipeline_nodes ?? []} />
          </Col>
        </Row>

        {/* ── Row 3: Charts Grid 2x2 ── */}
        <ChartsGrid metrics={metrics} />

        {/* ── Row 4: Alert List ── */}
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={24}>
            <AlertList />
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default Dashboard;
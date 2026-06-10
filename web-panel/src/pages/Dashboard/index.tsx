import React, { useEffect } from 'react';
import { Row, Col, Badge, Typography, Space, theme, Result, Button, Spin } from 'antd';
import {
  ThunderboltOutlined,
  ApiOutlined,
  SafetyCertificateOutlined,
  RocketOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';
import { useDashboardStore } from '@/stores/dashboard';
import { usePolling } from '@/hooks/usePolling';
import TopMetrics from './TopMetrics';
import PipelineTopology from './PipelineTopology';
import AlertList from './AlertList';
import { ChartsGrid } from './Charts';

const { Title, Text } = Typography;

const Dashboard: React.FC = () => {
  const { metrics, loading, error, fetchMetrics, fetchAlerts } = useDashboardStore();
  const { token } = theme.useToken();

  useEffect(() => {
    fetchMetrics();
    fetchAlerts();
  }, [fetchMetrics, fetchAlerts]);

  usePolling(fetchMetrics, 5000);

  const mode = document.querySelector('.light-mode') ? 'light' : 'dark';

  // ── Error state ──
  if (error && !metrics) {
    return (
      <ErrorBoundary>
        <Result
          status="error"
          title="数据加载失败"
          subTitle={error}
          extra={<Button type="primary" onClick={fetchMetrics}>重试</Button>}
        />
      </ErrorBoundary>
    );
  }

  // ── Loading state (first load) ──
  if (loading && !metrics) {
    return (
      <ErrorBoundary>
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
          <Spin size="large" />
        </div>
      </ErrorBoundary>
    );
  }

  // ── How it works steps (Tellius 01/02/03 pattern) ──
  const howItWorksSteps = [
    {
      num: '01',
      title: '配置采集任务',
      desc: '选择数据源模板，配置采集参数和目标。支持网页、API、消息队列等多种数据源类型，一键启动智能采集流程。',
      icon: <ApiOutlined />,
      gradient: 'gradient-accent',
    },
    {
      num: '02',
      title: '智能数据管道',
      desc: '数据自动流经 Crawl → RDS → ODS → TASK → DWD → DWS → ADS 七层架构，实时清洗、转换、聚合，确保数据质量。',
      icon: <ThunderboltOutlined />,
      gradient: 'gradient-secondary',
    },
    {
      num: '03',
      title: 'AI 驱动分析',
      desc: '基于 AI 模型自动识别数据模式、检测异常、生成洞察报告。支持自然语言查询，让数据分析触手可及。',
      icon: <RocketOutlined />,
      gradient: 'gradient-emerald',
    },
  ];

  return (
    <ErrorBoundary>
      <div className={mode === 'light' ? 'light-mode' : ''} style={{ padding: '0 0 24px', margin: '-24px -24px 0' }}>
        {/* ═══ Hero Section ═══ */}
        <div className="hero-section" style={{ padding: '48px 24px 32px' }}>
          <div style={{ position: 'relative', zIndex: 1, maxWidth: 800 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <div
                style={{
                  width: 36, height: 36,
                  borderRadius: 10,
                  background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 4px 16px rgba(59, 130, 246, 0.4)',
                }}
              >
                <ThunderboltOutlined style={{ fontSize: 18, color: '#fff' }} />
              </div>
              <span
                style={{
                  fontSize: 11, fontWeight: 700, letterSpacing: '0.08em',
                  color: '#93C5FD', textTransform: 'uppercase',
                  padding: '2px 10px', borderRadius: 4,
                  background: 'rgba(59, 130, 246, 0.12)',
                }}
              >
                Intelligence Platform
              </span>
            </div>
            <h1 className="hero-title" style={{ fontSize: 32, lineHeight: 1.2 }}>
              数据采集总览
            </h1>
            <p className="hero-subtitle" style={{ marginTop: 8 }}>
              实时监控数据管道的每个环节。从采集到分析，全链路可观测，AI 驱动的智能数据平台让您随时掌握数据态势。
            </p>
            <Space size={12} style={{ marginTop: 20 }}>
              <Badge
                status="processing"
                text={<span style={{ color: '#60A5FA', fontSize: 12, fontWeight: 500 }}>实时更新 · 5s 刷新</span>}
              />
              {metrics && (
                <span style={{ color: '#64748B', fontSize: 12 }}>
                  {metrics.tasks?.total || 0} 个任务 · {metrics.tasks?.running || 0} 运行中
                </span>
              )}
            </Space>
          </div>
        </div>

        <div style={{ padding: '0 24px' }}>
          {/* ═══ Row 1: Top Metrics ═══ */}
          <div className="fade-in-up" style={{ marginTop: -16 }}>
            <TopMetrics metrics={metrics} loading={loading} />
          </div>

          {/* ═══ Row 2: Pipeline Topology (full-width) ═══ */}
          <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
            <Col span={24}>
              <div className="fade-in-up stagger-1">
                <PipelineTopology nodes={metrics?.pipeline_nodes ?? []} />
              </div>
            </Col>
          </Row>

          {/* ═══ Row 3: Charts Grid 2x2 ═══ */}
          <div className="fade-in-up stagger-2" style={{ marginTop: 24 }}>
            <ChartsGrid metrics={metrics} />
          </div>

          {/* ═══ Row 4: Alert List ═══ */}
          <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
            <Col span={24}>
              <div className="fade-in-up stagger-3">
                <AlertList />
              </div>
            </Col>
          </Row>

          {/* ═══ How It Works Section (Tellius 01/02/03) ═══ */}
          <div style={{ marginTop: 48, padding: '32px 0', borderTop: '1px solid rgba(255, 255, 255, 0.06)' }}>
            <div style={{ textAlign: 'center', marginBottom: 40 }}>
              <span
                style={{
                  fontSize: 11, fontWeight: 700, letterSpacing: '0.08em',
                  color: '#93C5FD', textTransform: 'uppercase',
                  padding: '2px 10px', borderRadius: 4,
                  background: 'rgba(59, 130, 246, 0.12)',
                }}
              >
                How It Works
              </span>
              <h2 style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em', marginTop: 12, color: '#F1F5F9' }}>
                从配置到洞察，三步完成
              </h2>
              <p style={{ color: '#94A3B8', fontSize: 14, maxWidth: 500, margin: '8px auto 0' }}>
                无需复杂配置，通过直观的界面快速搭建数据管道
              </p>
            </div>

            {howItWorksSteps.map((step) => (
              <div className="step-section fade-in-up stagger-1" key={step.num}>
                <div className="step-number">{step.num}</div>
                <div className="step-content">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                    <div
                      style={{
                        width: 34, height: 34,
                        borderRadius: 8,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 16, color: '#fff',
                        background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                      }}
                    >
                      {step.icon}
                    </div>
                    <h2>{step.title}</h2>
                  </div>
                  <p className="step-desc">{step.desc}</p>
                </div>
              </div>
            ))}
          </div>

          {/* ═══ Bottom CTA ═══ */}
          <div
            style={{
              textAlign: 'center', padding: '48px 24px', marginTop: 16,
              background: 'rgba(15, 23, 42, 0.5)',
              border: '1px solid rgba(59, 130, 246, 0.1)',
              borderRadius: 16,
            }}
          >
            <SafetyCertificateOutlined style={{ fontSize: 32, color: '#60A5FA', marginBottom: 16 }} />
            <h3 style={{ fontSize: 18, fontWeight: 700, color: '#F1F5F9', margin: '0 0 8px' }}>
              准备好开始了吗？
            </h3>
            <p style={{ color: '#94A3B8', fontSize: 14, marginBottom: 20 }}>
              前往任务中心创建您的第一个数据采集任务
            </p>
            <Button
              type="primary"
              size="large"
              icon={<RocketOutlined />}
              href="/tasks"
              style={{
                height: 44, paddingInline: 32,
                fontSize: 15, fontWeight: 600,
                borderRadius: 10,
                background: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
                border: 'none',
                boxShadow: '0 4px 20px rgba(59, 130, 246, 0.4)',
              }}
            >
              创建采集任务
            </Button>
          </div>
        </div>
      </div>
    </ErrorBoundary>
  );
};

export default Dashboard;
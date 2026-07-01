import React, { useEffect } from 'react';
import { Badge, Button, Result, Space, Spin, Typography } from 'antd';
import {
  ApiOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  RocketOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';
import { usePolling } from '@/hooks/usePolling';
import workspacePalette from '@/pages/AICollect/palette';
import { useDashboardStore } from '@/stores/dashboard';
import AlertList from './AlertList';
import { ChartsGrid } from './Charts';
import PipelineTopology from './PipelineTopology';
import TopMetrics from './TopMetrics';

const { Title, Text } = Typography;

const aura = workspacePalette;

const Dashboard: React.FC = () => {
  const { metrics, loading, error, fetchMetrics, fetchAlerts } = useDashboardStore();

  useEffect(() => {
    fetchMetrics();
    fetchAlerts();
  }, [fetchMetrics, fetchAlerts]);

  usePolling(fetchMetrics, 5000);

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

  if (loading && !metrics) {
    return (
      <ErrorBoundary>
        <div
          style={{
            minHeight: 'calc(100vh - 120px)',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          <Spin size="large" />
        </div>
      </ErrorBoundary>
    );
  }

  const howItWorksSteps = [
    {
      num: '01',
      title: '配置采集任务',
      desc: '选择模板、输入参数、声明抓取边界，让 AI 在一开始就明确采集目标、翻页策略和质量基线。',
      icon: <RobotOutlined />,
      accent: aura.accent,
      chips: ['模板驱动', '入口识别', '字段意图'],
    },
    {
      num: '02',
      title: '穿行智能管道',
      desc: '原始数据经过 Crawl、RDS、ODS、DWD、DWS 分层流转，下载、同步、标准化和质量门禁同步发生。',
      icon: <DatabaseOutlined />,
      accent: aura.success,
      chips: ['Mongo / MinIO', 'Kafka Sync', 'ETL 分层'],
    },
    {
      num: '03',
      title: '生成 AI 洞察',
      desc: '系统基于实时指标和质量信号自动发现异常、聚合主题视角，并为后续数据服务提供稳定输入。',
      icon: <RocketOutlined />,
      accent: aura.warning,
      chips: ['实时监控', '异常发现', '洞察输出'],
    },
  ];

  return (
    <ErrorBoundary>
      <style>
        {`
          .dashboard-step-page {
            min-height: calc(100vh - 48px);
            margin: 0;
            padding: 26px 20px 56px;
            background:
              linear-gradient(180deg, rgba(15, 18, 26, 0.96) 0%, rgba(23, 26, 34, 0.98) 100%),
              radial-gradient(90% 68% at 50% 0%, rgba(138, 180, 255, 0.18) 0%, rgba(138, 180, 255, 0.05) 44%, rgba(23, 26, 34, 0) 72%);
          }
          .dashboard-step-shell {
            width: min(1480px, 100%);
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 28px;
          }
          .dashboard-hero-shell {
            position: relative;
            overflow: hidden;
            border-radius: 20px;
            border: 1px solid ${aura.border};
            background:
              linear-gradient(145deg, rgba(34, 39, 51, 0.96) 0%, rgba(19, 23, 31, 0.98) 100%);
            box-shadow: ${aura.shadow};
            padding: 30px;
          }
          .dashboard-hero-shell::before {
            content: '';
            position: absolute;
            inset: 0;
            background:
              linear-gradient(90deg, rgba(138, 180, 255, 0.12), rgba(138, 180, 255, 0) 42%),
              linear-gradient(180deg, rgba(101, 213, 163, 0.06), rgba(101, 213, 163, 0) 40%);
            pointer-events: none;
          }
          .dashboard-hero-grid {
            position: relative;
            z-index: 1;
            display: grid;
            grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.9fr);
            gap: 24px;
            align-items: stretch;
          }
          .dashboard-hero-kicker {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 10px;
            border-radius: 999px;
            border: 1px solid rgba(138, 180, 255, 0.18);
            background: rgba(138, 180, 255, 0.08);
            color: ${aura.accent};
            font-size: 12px;
            font-weight: 600;
          }
          .dashboard-hero-title {
            margin: 16px 0 0;
            color: ${aura.text};
            font-size: clamp(30px, 4vw, 38px);
            line-height: 1.14;
            letter-spacing: 0;
          }
          .dashboard-hero-copy {
            margin-top: 14px;
            max-width: 760px;
            color: ${aura.muted};
            font-size: 15px;
            line-height: 1.85;
          }
          .dashboard-hero-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            margin-top: 20px;
          }
          .dashboard-hero-meta span {
            color: ${aura.subtle};
            font-size: 12px;
          }
          .dashboard-hero-side {
            border-radius: 18px;
            border: 1px solid ${aura.borderSoft};
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
            padding: 20px;
            backdrop-filter: ${aura.backdrop};
            display: grid;
            gap: 16px;
          }
          .dashboard-side-label {
            color: ${aura.subtle};
            font-size: 12px;
          }
          .dashboard-side-value {
            margin-top: 8px;
            color: ${aura.text};
            font-size: 28px;
            font-weight: 800;
            line-height: 1;
          }
          .dashboard-side-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
          }
          .dashboard-side-card {
            border-radius: 14px;
            border: 1px solid ${aura.borderSoft};
            background: rgba(255, 255, 255, 0.03);
            padding: 14px;
          }
          .dashboard-side-card strong {
            display: block;
            margin-top: 8px;
            color: ${aura.text};
            font-size: 18px;
            font-weight: 700;
          }
          .dashboard-section-head {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 14px;
          }
          .dashboard-section-head h2 {
            margin: 0;
            color: ${aura.text};
            font-size: 20px;
            line-height: 1.2;
          }
          .dashboard-section-head p {
            margin: 8px 0 0;
            color: ${aura.subtle};
            font-size: 13px;
            line-height: 1.7;
          }
          .dashboard-flow-shell {
            border-radius: 20px;
            border: 1px solid ${aura.border};
            background: linear-gradient(180deg, rgba(26, 31, 40, 0.95), rgba(20, 24, 32, 0.98));
            box-shadow: ${aura.shadow};
            padding: 28px;
          }
          .dashboard-flow-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 16px;
          }
          .dashboard-flow-card {
            position: relative;
            overflow: hidden;
            min-height: 250px;
            border-radius: 18px;
            border: 1px solid ${aura.borderSoft};
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
            padding: 22px 20px 20px;
          }
          .dashboard-flow-card::before {
            content: '';
            position: absolute;
            inset: 0 auto auto 0;
            width: 100%;
            height: 3px;
            background: var(--step-accent);
            opacity: 0.92;
          }
          .dashboard-flow-number {
            position: absolute;
            right: 18px;
            top: 16px;
            color: color-mix(in srgb, var(--step-accent) 22%, transparent);
            font-size: 60px;
            font-weight: 800;
            line-height: 0.9;
            letter-spacing: 0;
          }
          .dashboard-flow-icon {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: ${aura.text};
            background: color-mix(in srgb, var(--step-accent) 22%, rgba(255, 255, 255, 0.05));
            box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--step-accent) 34%, transparent);
            font-size: 18px;
          }
          .dashboard-flow-title {
            margin-top: 18px;
            color: ${aura.text};
            font-size: 20px;
            font-weight: 700;
            line-height: 1.25;
          }
          .dashboard-flow-desc {
            margin-top: 12px;
            color: ${aura.muted};
            font-size: 14px;
            line-height: 1.85;
          }
          .dashboard-flow-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 18px;
          }
          .dashboard-flow-chip {
            padding: 5px 10px;
            border-radius: 999px;
            border: 1px solid ${aura.borderSoft};
            background: rgba(255, 255, 255, 0.03);
            color: ${aura.subtle};
            font-size: 12px;
            line-height: 1;
          }
          .dashboard-cta-shell {
            border-radius: 20px;
            border: 1px solid rgba(138, 180, 255, 0.16);
            background:
              linear-gradient(180deg, rgba(23, 27, 36, 0.98), rgba(18, 22, 30, 1)),
              radial-gradient(80% 120% at 0% 0%, rgba(138, 180, 255, 0.12) 0%, transparent 52%);
            box-shadow: ${aura.shadow};
            padding: 34px 28px;
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 24px;
            align-items: center;
          }
          .dashboard-cta-shell h3 {
            margin: 12px 0 0;
            color: ${aura.text};
            font-size: 26px;
            line-height: 1.2;
          }
          .dashboard-cta-shell p {
            margin: 12px 0 0;
            color: ${aura.muted};
            font-size: 14px;
            line-height: 1.8;
            max-width: 720px;
          }
          .dashboard-cta-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: flex-end;
          }
          .dashboard-cta-shell .ant-btn {
            height: 42px;
            padding-inline: 22px;
            border-radius: 10px;
            font-weight: 600;
          }
          .dashboard-cta-shell .ant-btn-primary {
            background: ${aura.accentSoft};
            border-color: rgba(138, 180, 255, 0.18);
            color: ${aura.text};
            box-shadow: none;
          }
          .dashboard-cta-shell .ant-btn-default {
            background: rgba(255, 255, 255, 0.03);
            border-color: ${aura.border};
            color: ${aura.text};
          }
          @media (max-width: 1180px) {
            .dashboard-hero-grid,
            .dashboard-cta-shell {
              grid-template-columns: 1fr;
            }
            .dashboard-cta-actions {
              justify-content: flex-start;
            }
            .dashboard-flow-grid {
              grid-template-columns: 1fr;
            }
          }
          @media (max-width: 768px) {
            .dashboard-step-page {
              padding: 18px 12px 42px;
            }
            .dashboard-step-shell {
              gap: 22px;
            }
            .dashboard-hero-shell,
            .dashboard-flow-shell,
            .dashboard-cta-shell {
              border-radius: 18px;
              padding: 22px 18px;
            }
            .dashboard-side-grid {
              grid-template-columns: 1fr 1fr;
            }
            .dashboard-flow-card {
              min-height: 0;
            }
            .dashboard-flow-number {
              font-size: 46px;
            }
          }
        `}
      </style>

      <div className="dashboard-step-page">
        <div className="dashboard-step-shell">
          <section className="dashboard-hero-shell">
            <div className="dashboard-hero-grid">
              <div>
                <span className="dashboard-hero-kicker">
                  <ThunderboltOutlined />
                  AI Collect Mission Board
                </span>
                <Title level={1} className="dashboard-hero-title">
                  智能采集 Step Center
                </Title>
                <p className="dashboard-hero-copy">
                  这页聚焦展示采集任务从配置、穿行管道到生成洞察的完整三步路径。整体视觉沿用智能采集首页的深色玻璃质感，四周保持充足留白，让关键流程在更清爽的空间里被看见。
                </p>
                <div className="dashboard-hero-meta">
                  <Badge
                    status="processing"
                    text={<span style={{ color: aura.accent, fontSize: 12, fontWeight: 500 }}>实时更新 · 5s 刷新</span>}
                  />
                  <span>{metrics?.tasks?.total || 0} 个任务</span>
                  <span>{metrics?.tasks?.running || 0} 个运行中</span>
                  <span>{metrics?.data_volume?.daily_increment || 0} 条今日新增</span>
                </div>
              </div>

              <aside className="dashboard-hero-side">
                <div>
                  <Text className="dashboard-side-label">当前运行态</Text>
                  <div className="dashboard-side-value">{metrics?.tasks?.running || 0}</div>
                  <Text style={{ color: aura.muted }}>活跃采集任务正在推进端到端链路</Text>
                </div>
                <div className="dashboard-side-grid">
                  <div className="dashboard-side-card">
                    <Text className="dashboard-side-label">Kafka Lag</Text>
                    <strong>{metrics?.kafka_lag?.total ?? 0}</strong>
                  </div>
                  <div className="dashboard-side-card">
                    <Text className="dashboard-side-label">ETL 吞吐</Text>
                    <strong>{metrics?.etl_throughput?.current ?? 0}</strong>
                  </div>
                  <div className="dashboard-side-card">
                    <Text className="dashboard-side-label">数据总量</Text>
                    <strong>{metrics?.data_volume?.total ?? 0}</strong>
                  </div>
                  <div className="dashboard-side-card">
                    <Text className="dashboard-side-label">失败任务</Text>
                    <strong>{metrics?.tasks?.failed ?? 0}</strong>
                  </div>
                </div>
              </aside>
            </div>
          </section>

          <section>
            <div className="dashboard-section-head">
              <div>
                <h2>实时指标视图</h2>
                <p>保留现有监控能力，只对外层空间和视觉节奏做收口，让页面更像采集工作台而不是传统报表页。</p>
              </div>
            </div>
            <TopMetrics metrics={metrics} loading={loading} />
          </section>

          <section>
            <div className="dashboard-section-head">
              <div>
                <h2>管道拓扑</h2>
                <p>观察采集流进入 RDS、ODS、DWD、DWS 等层级时的节点健康度与吞吐节奏。</p>
              </div>
            </div>
            <PipelineTopology nodes={metrics?.pipeline_nodes ?? []} />
          </section>

          <section>
            <div className="dashboard-section-head">
              <div>
                <h2>趋势与质量</h2>
                <p>把吞吐、Lag、错误率和状态分布留在主视区，方便继续配合 step 流程解释系统如何工作。</p>
              </div>
            </div>
            <ChartsGrid metrics={metrics} />
          </section>

          <section>
            <div className="dashboard-section-head">
              <div>
                <h2>告警视图</h2>
                <p>把最近的异常、质量提示和链路抖动放在流程段落前，形成先看态势、再看动作路径的阅读顺序。</p>
              </div>
            </div>
            <AlertList />
          </section>

          <section className="dashboard-flow-shell">
            <div className="dashboard-section-head">
              <div>
                <h2>How It Works</h2>
                <p>三步流程严格参考智能采集首页的冷色、发光、毛玻璃语言，同时保留更适合阅读说明页的舒展留白。</p>
              </div>
              <TagOutline />
            </div>
            <div className="dashboard-flow-grid">
              {howItWorksSteps.map((step) => (
                <article
                  key={step.num}
                  className="dashboard-flow-card"
                  style={{ ['--step-accent' as string]: step.accent }}
                >
                  <div className="dashboard-flow-number">{step.num}</div>
                  <div className="dashboard-flow-icon">{step.icon}</div>
                  <div className="dashboard-flow-title">{step.title}</div>
                  <p className="dashboard-flow-desc">{step.desc}</p>
                  <div className="dashboard-flow-chips">
                    {step.chips.map((chip) => (
                      <span className="dashboard-flow-chip" key={chip}>{chip}</span>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="dashboard-cta-shell">
            <div>
              <span className="dashboard-hero-kicker">
                <SafetyCertificateOutlined />
                Next Action
              </span>
              <h3>继续进入智能采集工作台</h3>
              <p>
                这页负责把 step 逻辑讲清楚，真正的操作入口仍然回到智能采集首页。你可以直接发起 URL 分析、生成字段合约、试跑数据样本，再把模板发布到任务编排链路里。
              </p>
            </div>
            <div className="dashboard-cta-actions">
              <Button type="primary" icon={<RocketOutlined />} href="/ai-collect">
                打开智能采集
              </Button>
              <Button icon={<ApiOutlined />} href="/tasks">
                查看任务中心
              </Button>
              <Button icon={<ClockCircleOutlined />} href="/monitor">
                查看实时监控
              </Button>
            </div>
          </section>
        </div>
      </div>
    </ErrorBoundary>
  );
};

const TagOutline: React.FC = () => (
  <Space
    size={8}
    style={{
      padding: '6px 10px',
      borderRadius: 999,
      border: `1px solid ${aura.borderSoft}`,
      background: 'rgba(255,255,255,0.03)',
      color: aura.subtle,
      fontSize: 12,
      fontWeight: 500,
      whiteSpace: 'nowrap',
    }}
  >
    <ApiOutlined style={{ color: aura.accent }} />
    采集首页视觉参考
  </Space>
);

export default Dashboard;

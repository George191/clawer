import React from 'react';
import { Button, Card, Col, Progress, Row, Space, Tag, Typography, theme } from 'antd';
import {
  ApiOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  LineChartOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';

const { Title, Text } = Typography;

const kpis = [
  { label: '活跃数据源', value: '36', trend: '+8', color: '#7C3AED', icon: <RobotOutlined /> },
  { label: '湖仓数据量', value: '3.8 TB', trend: '+420 GB', color: '#059669', icon: <DatabaseOutlined /> },
  { label: 'ETL 管道', value: '168', trend: '142 运行中', color: '#0EA5E9', icon: <BranchesOutlined /> },
  { label: '服务 SLA', value: '99.92%', trend: '近 24 小时', color: '#F59E0B', icon: <SafetyCertificateOutlined /> },
];

const flowStages = [
  {
    title: 'AI 智能采集',
    desc: 'URL 识别、字段推断、翻页策略、模板生成',
    metric: '18 个任务运行中',
    progress: 76,
    color: '#7C3AED',
    icon: <RobotOutlined />,
  },
  {
    title: '数据湖入湖',
    desc: '原始层沉淀、对象存储、元数据登记、分区归档',
    metric: '6.2 万条/分钟',
    progress: 88,
    color: '#059669',
    icon: <CloudServerOutlined />,
  },
  {
    title: 'ETL 标准化',
    desc: 'RDS → ODS → DWD → DWS → ADS 分层加工',
    metric: 'P95 延迟 42s',
    progress: 64,
    color: '#0EA5E9',
    icon: <DeploymentUnitOutlined />,
  },
  {
    title: '数据服务输出',
    desc: 'SQL 探索、指标集市、API 发布、质量审计',
    metric: '24 个 API 在线',
    progress: 91,
    color: '#F97316',
    icon: <ApiOutlined />,
  },
];

const runQueue = [
  { name: 'google_patent_daily', layer: 'ODS → DWD', status: 'running', records: '1.2M', eta: '12 min' },
  { name: 'sealagom_navwarn_sync', layer: 'Crawl → RDS', status: 'running', records: '48.6K', eta: '4 min' },
  { name: 'quality_missing_scan', layer: 'DWD', status: 'queued', records: '8 tables', eta: '18:30' },
  { name: 'ads_topic_market', layer: 'DWS → ADS', status: 'done', records: '312K', eta: '完成' },
];

const recommendations = [
  { title: '建议拆分高延迟采集源', desc: 'navwarn 批次中 3 个源站响应超过 4s，可启用分组调度。', color: '#F59E0B' },
  { title: '可补齐字段映射规则', desc: 'patent_abstract 字段在 2 个模板中命名不一致，建议归一化。', color: '#0EA5E9' },
  { title: '湖仓分区需要压缩', desc: 'RDS 原始层近 7 日小文件数增长 31%，可安排合并任务。', color: '#059669' },
];

const CommandCenter: React.FC = () => {
  const navigate = useNavigate();
  const { token } = theme.useToken();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <section
        className="command-center-hero"
        style={{
          padding: '26px 28px',
          borderRadius: 8,
          border: `1px solid ${token.colorBorderSecondary}`,
          background: token.colorBgContainer,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.5fr) minmax(280px, 0.8fr)',
          gap: 24,
          alignItems: 'center',
        }}
      >
        <div>
          <Space size={8} style={{ marginBottom: 12 }} wrap>
            <Tag color="processing">AI Collect</Tag>
            <Tag color="success">Data Lake</Tag>
            <Tag color="blue">ETL Pipeline</Tag>
          </Space>
          <Title level={2} style={{ margin: 0, fontSize: 30, lineHeight: 1.25 }}>
            AI 数据中台驾驶舱
          </Title>
          <Text type="secondary" style={{ display: 'block', marginTop: 10, maxWidth: 720, fontSize: 14, lineHeight: 1.8 }}>
            面向智能采集、数据湖沉淀和 ETL 管道生产的一体化工作台。先从前端把业务域、运行态势和操作入口打通，后续再接服务端真实指标。
          </Text>
          <Space size={10} style={{ marginTop: 20 }} wrap>
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => navigate('/ai-collect')}>
              发起智能采集
            </Button>
            <Button icon={<DatabaseOutlined />} onClick={() => navigate('/lake/catalog')}>
              查看数据目录
            </Button>
            <Button icon={<BranchesOutlined />} onClick={() => navigate('/pipeline')}>
              打开管道画布
            </Button>
          </Space>
        </div>

        <div
          style={{
            borderRadius: 8,
            border: `1px solid ${token.colorBorderSecondary}`,
            padding: 18,
            background: token.colorFillAlter,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <Text strong>今日端到端健康度</Text>
            <Tag color="success">Stable</Tag>
          </div>
          <Progress percent={92} strokeColor={{ '0%': '#7C3AED', '45%': '#059669', '100%': '#0EA5E9' }} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 14 }}>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>平均入湖延迟</Text>
              <div style={{ fontSize: 20, fontWeight: 700, color: token.colorText }}>42s</div>
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>质量通过率</Text>
              <div style={{ fontSize: 20, fontWeight: 700, color: token.colorText }}>98.7%</div>
            </div>
          </div>
        </div>
      </section>

      <Row gutter={[16, 16]}>
        {kpis.map((item) => (
          <Col xs={24} sm={12} xl={6} key={item.label}>
            <Card style={{ height: '100%', borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text type="secondary" style={{ fontSize: 13 }}>{item.label}</Text>
                <span style={{ color: item.color, fontSize: 18 }}>{item.icon}</span>
              </div>
              <div style={{ marginTop: 14, color: token.colorText, fontSize: 28, fontWeight: 800, lineHeight: 1 }}>
                {item.value}
              </div>
              <Text style={{ display: 'block', marginTop: 10, color: item.color, fontSize: 12 }}>{item.trend}</Text>
            </Card>
          </Col>
        ))}
      </Row>

      <Card title="端到端数据链路" style={{ borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(180px, 1fr))', gap: 14, overflowX: 'auto' }}>
          {flowStages.map((stage, index) => (
            <div
              key={stage.title}
              style={{
                minWidth: 180,
                borderRadius: 8,
                border: `1px solid ${token.colorBorderSecondary}`,
                padding: 16,
                background: token.colorFillAlter,
                position: 'relative',
              }}
            >
              {index < flowStages.length - 1 && (
                <ArrowRightOutlined
                  style={{
                    position: 'absolute',
                    right: -13,
                    top: 30,
                    color: token.colorTextTertiary,
                    zIndex: 1,
                  }}
                />
              )}
              <span
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 8,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: `${stage.color}22`,
                  color: stage.color,
                  fontSize: 18,
                }}
              >
                {stage.icon}
              </span>
              <div style={{ marginTop: 12, fontWeight: 700, color: token.colorText }}>{stage.title}</div>
              <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12, minHeight: 38 }}>
                {stage.desc}
              </Text>
              <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', color: stage.color, fontSize: 12 }}>
                <span>{stage.metric}</span>
                <span>{stage.progress}%</span>
              </div>
              <Progress percent={stage.progress} showInfo={false} strokeColor={stage.color} size="small" style={{ marginTop: 4 }} />
            </div>
          ))}
        </div>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card title="今日运行队列" extra={<Button type="link" onClick={() => navigate('/tasks')}>任务中心</Button>} style={{ borderRadius: 8, height: '100%' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {runQueue.map((item) => (
                <div
                  key={item.name}
                  className="command-center-queue-row"
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(180px, 1.1fr) 120px 90px 90px',
                    gap: 12,
                    alignItems: 'center',
                    padding: '12px 0',
                    borderBottom: `1px solid ${token.colorBorderSecondary}`,
                  }}
                >
                  <div>
                    <Text strong>{item.name}</Text>
                    <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>{item.layer}</Text>
                  </div>
                  <Text>{item.records}</Text>
                  <Tag color={item.status === 'running' ? 'processing' : item.status === 'queued' ? 'warning' : 'success'}>
                    {item.status}
                  </Tag>
                  <Text type="secondary">{item.eta}</Text>
                </div>
              ))}
            </div>
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card title="AI 运维建议" style={{ borderRadius: 8, height: '100%' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {recommendations.map((item) => (
                <div
                  key={item.title}
                  style={{
                    borderRadius: 8,
                    border: `1px solid ${token.colorBorderSecondary}`,
                    padding: 12,
                    background: token.colorFillAlter,
                  }}
                >
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                    <CheckCircleOutlined style={{ color: item.color }} />
                    <Text strong>{item.title}</Text>
                  </div>
                  <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.7 }}>{item.desc}</Text>
                </div>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
            <Space align="start">
              <ExperimentOutlined style={{ color: '#059669', fontSize: 20, marginTop: 2 }} />
              <div>
                <Text strong>质量规则覆盖</Text>
                <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                  39 张核心表已绑定必填、唯一性、枚举和时效规则。
                </Text>
              </div>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
            <Space align="start">
              <FieldTimeOutlined style={{ color: '#0EA5E9', fontSize: 20, marginTop: 2 }} />
              <div>
                <Text strong>调度窗口</Text>
                <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                  18:00-20:00 为高峰窗口，系统已预留 24 个并发槽位。
                </Text>
              </div>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
            <Space align="start">
              <LineChartOutlined style={{ color: '#F59E0B', fontSize: 20, marginTop: 2 }} />
              <div>
                <Text strong>指标服务</Text>
                <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                  ADS 指标集市已发布 12 个主题域，可供 API 和 SQL 查询。
                </Text>
              </div>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default CommandCenter;

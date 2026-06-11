import React from 'react';
import { Button, Card, Col, Progress, Row, Segmented, Space, Table, Tag, Timeline, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ControlOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

type GovernanceKind = 'source' | 'identity' | 'field';

interface RuleRow {
  key: string;
  name: string;
  target: string;
  mode: string;
  status: 'active' | 'review' | 'paused' | 'warning';
  score: number;
  updatedAt: string;
}

const statusColor: Record<RuleRow['status'], string> = {
  active: 'success',
  review: 'processing',
  paused: 'default',
  warning: 'warning',
};

const configs: Record<GovernanceKind, {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  accent: string;
  metrics: Array<{ label: string; value: string; hint: string }>;
  rows: RuleRow[];
  timeline: string[];
}> = {
  source: {
    title: '源站策略',
    subtitle: '管理 AI 采集的源站探索、频率、翻页、失败重试和调度窗口',
    icon: <ControlOutlined />,
    accent: '#7C3AED',
    metrics: [
      { label: '活跃源站', value: '36', hint: '+4 本周新增' },
      { label: '成功率', value: '96.4%', hint: '近 24 小时' },
      { label: '限流规则', value: '18', hint: '6 条自动生成' },
    ],
    rows: [
      { key: '1', name: 'google_patent_search', target: 'patents.google.com', mode: 'AI Agent + 详情页追踪', status: 'active', score: 94, updatedAt: '8 分钟前' },
      { key: '2', name: 'sealagom_navwarn', target: 'navigation warning', mode: '列表翻页 + 增量窗口', status: 'active', score: 97, updatedAt: '16 分钟前' },
      { key: '3', name: 'zdopen_notice', target: '政务公告', mode: '浏览器渲染 + 慢速队列', status: 'warning', score: 72, updatedAt: '42 分钟前' },
    ],
    timeline: ['google_patent 夜间并发提升到 12', 'navwarn 增量窗口缩短为 10 分钟', 'zdopen 失败重试进入人工复核'],
  },
  identity: {
    title: '反爬身份',
    subtitle: '维护代理池、浏览器指纹、请求节奏、验证码风险和站点适配策略',
    icon: <SafetyCertificateOutlined />,
    accent: '#EF4444',
    metrics: [
      { label: '代理可用率', value: '86%', hint: '住宅代理池' },
      { label: '拦截事件', value: '12', hint: '-18% 较昨日' },
      { label: '平均延迟', value: '380ms', hint: 'P95 920ms' },
    ],
    rows: [
      { key: '1', name: 'residential_pool_asia', target: 'Google / Navwarn', mode: '区域轮换 + 会话保持', status: 'active', score: 88, updatedAt: '5 分钟前' },
      { key: '2', name: 'browser_fingerprint_v3', target: '动态站点', mode: '指纹轮换 + WebGL 隔离', status: 'review', score: 81, updatedAt: '22 分钟前' },
      { key: '3', name: 'captcha_risk_guard', target: '高风险源站', mode: '降速 + 熔断', status: 'warning', score: 69, updatedAt: '1 小时前' },
    ],
    timeline: ['剔除 7 个慢代理节点', '新增站点级请求延迟策略', '验证码风险命中后自动降速'],
  },
  field: {
    title: '字段识别',
    subtitle: '治理 AI 识别出的字段、类型、命名、目标表映射和字段漂移',
    icon: <FileSearchOutlined />,
    accent: '#8B5CF6',
    metrics: [
      { label: '识别字段', value: '2.4K', hint: '跨 36 个源站' },
      { label: '自动映射率', value: '87%', hint: '+5% 本周' },
      { label: '待处理冲突', value: '11', hint: '命名/类型冲突' },
    ],
    rows: [
      { key: '1', name: 'patent_owner', target: 'dim_company.owner_name', mode: '实体归一 + 字典校验', status: 'review', score: 84, updatedAt: '12 分钟前' },
      { key: '2', name: 'navwarn_area', target: 'dim_geo.area_code', mode: '地理编码 + 枚举映射', status: 'active', score: 96, updatedAt: '24 分钟前' },
      { key: '3', name: 'abstract', target: 'ods_patent.abstract_text', mode: '长文本清洗', status: 'active', score: 92, updatedAt: '38 分钟前' },
    ],
    timeline: ['patent_owner 建议映射到企业维表', 'navwarn_area 识别为地理维度', 'abstract 长文本字段漂移已恢复'],
  },
};

function getKind(pathname: string): GovernanceKind {
  if (pathname === '/anti-crawl') return 'identity';
  if (pathname === '/field-mapping') return 'field';
  return 'source';
}

const AICollectGovernance: React.FC = () => {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const kind = getKind(pathname);
  const config = configs[kind];
  const { token } = theme.useToken();

  const columns: ColumnsType<RuleRow> = [
    {
      title: '策略对象',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={2}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.target}</Text>
        </Space>
      ),
    },
    {
      title: '运行模式',
      dataIndex: 'mode',
      render: (value: string) => <Text>{value}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: RuleRow['status']) => <Tag color={statusColor[status]}>{status}</Tag>,
    },
    {
      title: '健康度',
      dataIndex: 'score',
      width: 160,
      render: (score: number) => <Progress percent={score} showInfo={false} strokeColor={config.accent} size="small" />,
    },
    {
      title: '更新时间',
      dataIndex: 'updatedAt',
      width: 130,
      render: (value: string) => <Text type="secondary">{value}</Text>,
    },
  ];

  return (
    <ErrorBoundary>
      <PageHeader
        title={config.title}
        subtitle={config.subtitle}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />}>新建策略</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <section
          style={{
            borderRadius: 8,
            border: `1px solid ${token.colorBorderSecondary}`,
            background: token.colorBgContainer,
            padding: 18,
          }}
        >
          <Row gutter={[18, 18]} align="middle">
            <Col xs={24} lg={10}>
              <Space align="start" size={14}>
                <span
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 8,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: config.accent,
                    background: `${config.accent}1F`,
                    fontSize: 22,
                  }}
                >
                  {config.icon}
                </span>
                <div>
                  <Text strong style={{ fontSize: 16 }}>AI 采集治理面板</Text>
                  <Text type="secondary" style={{ display: 'block', marginTop: 6, lineHeight: 1.7 }}>
                    将频率、身份、字段漂移、质量门禁和熔断策略从单个任务中抽离出来，形成模板和任务可复用的策略资产。
                  </Text>
                </div>
              </Space>
            </Col>
            <Col xs={24} lg={14}>
              <Row gutter={[12, 12]}>
                {config.metrics.map((metric) => (
                  <Col xs={24} sm={8} key={metric.label}>
                    <div style={{ borderRadius: 8, background: token.colorFillAlter, padding: 14 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{metric.label}</Text>
                      <div style={{ marginTop: 6, fontSize: 24, fontWeight: 800, color: config.accent }}>{metric.value}</div>
                      <Text type="secondary" style={{ fontSize: 12 }}>{metric.hint}</Text>
                    </div>
                  </Col>
                ))}
              </Row>
            </Col>
          </Row>
        </section>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={16}>
            <Card
              title={<Space>{config.icon} 策略列表</Space>}
              extra={
                <Segmented
                  value={kind}
                  onChange={(value) => {
                    const target = value === 'identity'
                      ? '/anti-crawl'
                      : value === 'field'
                        ? '/field-mapping'
                        : '/source-strategy';
                    navigate(target);
                  }}
                  options={[
                    { label: '源站', value: 'source', icon: <GlobalOutlined /> },
                    { label: '身份', value: 'identity', icon: <SafetyCertificateOutlined /> },
                    { label: '字段', value: 'field', icon: <FileSearchOutlined /> },
                  ]}
                />
              }
              style={{ borderRadius: 8 }}
            >
              <Table rowKey="key" columns={columns} dataSource={config.rows} pagination={false} scroll={{ x: 760 }} />
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title={<Space><ThunderboltOutlined /> 自动化建议</Space>} style={{ borderRadius: 8, height: '100%' }}>
              <Timeline
                items={config.timeline.map((item, index) => ({
                  color: index === 0 ? config.accent : 'gray',
                  children: (
                    <div>
                      <Text>{item}</Text>
                      <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
                        {index === 0 ? '刚刚' : `${index * 14 + 8} 分钟前`}
                      </Text>
                    </div>
                  ),
                }))}
              />
              <Card size="small" style={{ marginTop: 12, background: token.colorFillAlter }}>
                <Space align="start">
                  <FieldTimeOutlined style={{ color: config.accent, marginTop: 4 }} />
                  <Text type="secondary">
                    策略变更会在下一次试跑中验证，通过后再发布到调度和采集 worker。
                  </Text>
                </Space>
              </Card>
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={8}>
            <Card title="策略为什么存在" style={{ borderRadius: 8, height: '100%' }}>
              <Text type="secondary" style={{ lineHeight: 1.8 }}>
                策略治理不是看图表，而是把运行风险前置成可复用规则：源站变慢时自动降速，身份风险升高时切换代理，字段漂移时阻断发布，质量失败时进入审批。
              </Text>
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title="影响范围" style={{ borderRadius: 8, height: '100%' }}>
              {[
                ['绑定模板', '12'],
                ['影响任务', '36'],
                ['自动恢复', '8'],
              ].map(([label, value]) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                  <Text>{label}</Text>
                  <Text strong>{value}</Text>
                </div>
              ))}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title="发布流程" style={{ borderRadius: 8, height: '100%' }}>
              <Timeline
                items={[
                  { color: config.accent, children: '策略草案' },
                  { color: config.accent, children: '试跑验证' },
                  { color: 'gray', children: '审批发布' },
                  { color: 'gray', children: '运行监控' },
                ]}
              />
            </Card>
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default AICollectGovernance;

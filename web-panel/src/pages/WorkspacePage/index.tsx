import React from 'react';
import { Button, Card, Col, Progress, Row, Space, Table, Tag, Timeline, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ApiOutlined,
  AuditOutlined,
  BellOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  CodeOutlined,
  ControlOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { useLocation } from 'react-router-dom';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

interface PageMetric {
  label: string;
  value: string;
  tone: string;
}

interface WorkspaceConfig {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  accent: string;
  status: string;
  metrics: PageMetric[];
  timeline: string[];
  rows: WorkspaceRow[];
}

interface WorkspaceRow {
  key: string;
  name: string;
  owner: string;
  status: 'running' | 'healthy' | 'warning' | 'draft' | 'paused';
  updatedAt: string;
  score: number;
}

const statusColor: Record<WorkspaceRow['status'], string> = {
  running: 'processing',
  healthy: 'success',
  warning: 'warning',
  draft: 'default',
  paused: 'default',
};

const fallbackRows: WorkspaceRow[] = [
  { key: '1', name: 'google_patent_core', owner: 'Data Ops', status: 'running', updatedAt: '10 分钟前', score: 92 },
  { key: '2', name: 'navwarn_realtime', owner: 'Crawler Team', status: 'healthy', updatedAt: '18 分钟前', score: 97 },
  { key: '3', name: 'quality_scan_daily', owner: 'Governance', status: 'warning', updatedAt: '32 分钟前', score: 76 },
  { key: '4', name: 'ads_topic_publish', owner: 'Platform', status: 'draft', updatedAt: '1 小时前', score: 64 },
];

const catalog: Record<string, WorkspaceConfig> = {
  '/lake/catalog': {
    title: '数据目录',
    subtitle: '统一查看入湖资产、分层表、主题域和数据服务可用状态',
    icon: <DatabaseOutlined />,
    accent: '#059669',
    status: '湖仓资产在线',
    metrics: [
      { label: '数据表', value: '428', tone: '#059669' },
      { label: '主题域', value: '18', tone: '#0EA5E9' },
      { label: '近 24h 增量', value: '420 GB', tone: '#F59E0B' },
    ],
    timeline: ['RDS 原始层完成分区归档', 'ODS 标准层更新 16 张表', 'DWS 指标宽表发布到集市'],
    rows: fallbackRows,
  },
  '/lake/metadata': {
    title: '元数据管理',
    subtitle: '字段、血缘、分区、负责人和业务口径的统一登记面板',
    icon: <CloudServerOutlined />,
    accent: '#10B981',
    status: '元数据同步中',
    metrics: [
      { label: '字段档案', value: '12.8K', tone: '#10B981' },
      { label: '同步任务', value: '24', tone: '#0EA5E9' },
      { label: '待认领资产', value: '9', tone: '#F97316' },
    ],
    timeline: ['采集模板字段自动写入元数据', '新增 patent_owner 业务口径', '分区热度统计已刷新'],
    rows: fallbackRows,
  },
  '/lake/quality': {
    title: '质量规则',
    subtitle: '配置完整性、唯一性、枚举、时效和跨层一致性规则',
    icon: <ExperimentOutlined />,
    accent: '#F59E0B',
    status: '3 条规则需确认',
    metrics: [
      { label: '规则总数', value: '156', tone: '#F59E0B' },
      { label: '通过率', value: '98.7%', tone: '#059669' },
      { label: '告警表', value: '5', tone: '#EF4444' },
    ],
    timeline: ['navwarn.content 缺失率触达阈值', 'patent_id 唯一性检查通过', 'DWD 与 ODS 行数校验完成'],
    rows: fallbackRows,
  },
  '/lake/lineage': {
    title: '血缘关系',
    subtitle: '查看从采集源到 ADS 服务的字段级和任务级血缘',
    icon: <BranchesOutlined />,
    accent: '#0EA5E9',
    status: '血缘图谱已更新',
    metrics: [
      { label: '节点', value: '1,284', tone: '#0EA5E9' },
      { label: '边', value: '3,912', tone: '#7C3AED' },
      { label: '影响分析', value: '42', tone: '#F59E0B' },
    ],
    timeline: ['google_patent 采集源关联 7 张下游表', 'ADS 指标服务新增 API 血缘', 'DWD 处理器依赖刷新完成'],
    rows: fallbackRows,
  },
  '/lake/security': {
    title: '权限审计',
    subtitle: '审计数据资产授权、访问记录和敏感字段使用情况',
    icon: <AuditOutlined />,
    accent: '#6366F1',
    status: '审计策略生效',
    metrics: [
      { label: '授权策略', value: '64', tone: '#6366F1' },
      { label: '敏感字段', value: '28', tone: '#F97316' },
      { label: '异常访问', value: '0', tone: '#059669' },
    ],
    timeline: ['ADS API 访问白名单更新', '字段脱敏策略命中 328 次', '管理员审计报告已生成'],
    rows: fallbackRows,
  },
  '/lake/market': {
    title: '指标集市',
    subtitle: '沉淀可复用指标、维度和数据产品，支撑 API 与看板消费',
    icon: <ApiOutlined />,
    accent: '#F97316',
    status: '24 个指标可发布',
    metrics: [
      { label: '指标', value: '212', tone: '#F97316' },
      { label: '维度', value: '74', tone: '#0EA5E9' },
      { label: '订阅方', value: '16', tone: '#059669' },
    ],
    timeline: ['专利主题域新增 6 个指标', '航警主题域 API 调用量增长 12%', 'ADS 指标口径完成复核'],
    rows: fallbackRows,
  },
  '/data-api': {
    title: '数据 API',
    subtitle: '把湖仓结果发布为稳定 API，并跟踪调用、权限和 SLA',
    icon: <ApiOutlined />,
    accent: '#0EA5E9',
    status: 'API 网关正常',
    metrics: [
      { label: '在线 API', value: '24', tone: '#0EA5E9' },
      { label: '今日调用', value: '186K', tone: '#059669' },
      { label: 'P95 延迟', value: '86ms', tone: '#F59E0B' },
    ],
    timeline: ['patent-search/v2 灰度发布', 'navwarn/latest 缓存命中率 91%', 'API Key 轮换提醒已生成'],
    rows: fallbackRows,
  },
  '/source-strategy': {
    title: '源站策略',
    subtitle: '维护采集源的频率、失败重试、限流和优先级策略',
    icon: <ControlOutlined />,
    accent: '#7C3AED',
    status: '策略编排中',
    metrics: [
      { label: '源站', value: '36', tone: '#7C3AED' },
      { label: '限流规则', value: '18', tone: '#F59E0B' },
      { label: '成功率', value: '96.4%', tone: '#059669' },
    ],
    timeline: ['google patents 降低夜间并发', 'navwarn 失败重试窗口延长', '低优先级源站进入队列'],
    rows: fallbackRows,
  },
  '/anti-crawl': {
    title: '反爬身份',
    subtitle: '统一维护代理池、身份轮换、请求延迟和站点适配策略',
    icon: <SafetyCertificateOutlined />,
    accent: '#EF4444',
    status: '身份池健康',
    metrics: [
      { label: '可用代理', value: '86%', tone: '#059669' },
      { label: '触发拦截', value: '12', tone: '#EF4444' },
      { label: '平均延迟', value: '380ms', tone: '#F59E0B' },
    ],
    timeline: ['代理池剔除 7 个慢节点', 'User-Agent 轮换策略更新', 'zdopen 适配器命中降速规则'],
    rows: fallbackRows,
  },
  '/field-mapping': {
    title: '字段识别',
    subtitle: '管理 AI 识别字段、重命名、类型推断和目标层映射',
    icon: <FileSearchOutlined />,
    accent: '#8B5CF6',
    status: '字段建议待审核',
    metrics: [
      { label: '识别字段', value: '2.4K', tone: '#8B5CF6' },
      { label: '冲突字段', value: '11', tone: '#F59E0B' },
      { label: '自动映射', value: '87%', tone: '#059669' },
    ],
    timeline: ['patent_owner 建议映射到 dim_company', 'navwarn_area 识别为地理维度', 'abstract 字段类型建议为 text'],
    rows: fallbackRows,
  },
  '/pipeline/schedule': {
    title: '编排调度',
    subtitle: '配置周期调度、依赖关系、并发槽位和失败补偿策略',
    icon: <FieldTimeOutlined />,
    accent: '#0EA5E9',
    status: '调度器在线',
    metrics: [
      { label: '调度流', value: '72', tone: '#0EA5E9' },
      { label: '运行中', value: '19', tone: '#059669' },
      { label: '待补偿', value: '2', tone: '#F59E0B' },
    ],
    timeline: ['DWD 聚合任务等待上游 ODS 完成', '高峰窗口并发提升至 24', '失败补偿任务已排队'],
    rows: fallbackRows,
  },
  '/pipeline/releases': {
    title: '版本发布',
    subtitle: '管理处理器、模板和管道配置的发布、回滚与灰度',
    icon: <DeploymentUnitOutlined />,
    accent: '#6366F1',
    status: '2 个版本待发布',
    metrics: [
      { label: '发布包', value: '34', tone: '#6366F1' },
      { label: '灰度中', value: '3', tone: '#0EA5E9' },
      { label: '可回滚', value: '12', tone: '#059669' },
    ],
    timeline: ['patent_normalizer v1.8 进入灰度', 'navwarn_parser v2.1 发布完成', 'ADS 服务配置已备份'],
    rows: fallbackRows,
  },
  '/pipeline/alerts': {
    title: '告警规则',
    subtitle: '维护任务失败、延迟、质量波动和服务 SLA 的告警策略',
    icon: <BellOutlined />,
    accent: '#F59E0B',
    status: '告警策略生效',
    metrics: [
      { label: '规则', value: '48', tone: '#F59E0B' },
      { label: '活跃告警', value: '3', tone: '#EF4444' },
      { label: '已收敛', value: '91%', tone: '#059669' },
    ],
    timeline: ['Kafka lag 告警自动收敛', '质量失败告警升级到负责人', 'API SLA 告警静默窗口更新'],
    rows: fallbackRows,
  },
  '/logs': {
    title: '运行日志',
    subtitle: '聚合采集、调度、ETL 和数据服务日志，快速定位异常',
    icon: <FileTextOutlined />,
    accent: '#0EA5E9',
    status: '日志流在线',
    metrics: [
      { label: '日志吞吐', value: '18K/min', tone: '#0EA5E9' },
      { label: '错误', value: '7', tone: '#EF4444' },
      { label: '追踪链路', value: '128', tone: '#059669' },
    ],
    timeline: ['采集 worker 输出 2 条重试日志', 'DWD handler 执行耗时恢复正常', 'API 网关无 5xx 错误'],
    rows: fallbackRows,
  },
  '/project-users': {
    title: '项目成员',
    subtitle: '管理空间成员、角色、资产负责人和审批链路',
    icon: <TeamOutlined />,
    accent: '#0EA5E9',
    status: '成员权限正常',
    metrics: [
      { label: '成员', value: '18', tone: '#0EA5E9' },
      { label: '角色', value: '5', tone: '#6366F1' },
      { label: '待审批', value: '2', tone: '#F59E0B' },
    ],
    timeline: ['新增 Data Ops 角色', 'API 发布审批已通过', '质量规则负责人已变更'],
    rows: fallbackRows,
  },
  '/project-settings': {
    title: '项目设置',
    subtitle: '配置项目级资源、默认环境、通知和发布策略',
    icon: <CodeOutlined />,
    accent: '#64748B',
    status: '设置已保存',
    metrics: [
      { label: '环境', value: '3', tone: '#64748B' },
      { label: '连接器', value: '12', tone: '#0EA5E9' },
      { label: '策略', value: '26', tone: '#059669' },
    ],
    timeline: ['默认运行环境切换到 prod', '通知渠道新增 webhook', '资源配额完成复核'],
    rows: fallbackRows,
  },
};

function getConfig(pathname: string): WorkspaceConfig {
  const exact = catalog[pathname];
  if (exact) return exact;
  if (pathname === '/instances') return catalog['/lake/catalog'];
  if (pathname === '/graph-analytics') return catalog['/lake/lineage'];
  if (pathname === '/billing') return {
    title: '资源计量',
    subtitle: '查看采集、存储、计算和 API 调用消耗，作为成本看板入口',
    icon: <CloudServerOutlined />,
    accent: '#F97316',
    status: '计量数据已刷新',
    metrics: [
      { label: '本月计算', value: '842 CU', tone: '#F97316' },
      { label: '存储', value: '3.8 TB', tone: '#059669' },
      { label: 'API 调用', value: '2.1M', tone: '#0EA5E9' },
    ],
    timeline: ['ETL 高峰任务消耗增长 9%', '对象存储冷归档节省 14%', 'API 调用成本保持稳定'],
    rows: fallbackRows,
  };
  return catalog['/lake/catalog'];
}

const WorkspacePage: React.FC = () => {
  const { pathname } = useLocation();
  const config = getConfig(pathname);
  const { token } = theme.useToken();

  const columns: ColumnsType<WorkspaceRow> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, record) => (
        <div>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>{record.owner}</Text>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (status: WorkspaceRow['status']) => <Tag color={statusColor[status]}>{status}</Tag>,
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
      width: 140,
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
            <Button type="primary" icon={<PlusOutlined />}>新建</Button>
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
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) auto',
            gap: 16,
            alignItems: 'center',
          }}
        >
          <Space align="start" size={14}>
            <span
              style={{
                width: 42,
                height: 42,
                borderRadius: 8,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: config.accent,
                background: `${config.accent}1F`,
                fontSize: 21,
              }}
            >
              {config.icon}
            </span>
            <div>
              <Text strong style={{ fontSize: 16 }}>{config.status}</Text>
              <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 13 }}>
                当前页面先承接前端核心流程，后续可按模块接入真实 API、权限和审计事件。
              </Text>
            </div>
          </Space>
          <Button type="link">查看配置</Button>
        </section>

        <Row gutter={[16, 16]}>
          {config.metrics.map((metric) => (
            <Col xs={24} md={8} key={metric.label}>
              <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 18 } }}>
                <Text type="secondary" style={{ fontSize: 13 }}>{metric.label}</Text>
                <div style={{ marginTop: 10, color: token.colorText, fontSize: 26, fontWeight: 800 }}>
                  {metric.value}
                </div>
                <div style={{ marginTop: 10, height: 4, borderRadius: 2, background: `${metric.tone}33` }}>
                  <div style={{ width: '72%', height: 4, borderRadius: 2, background: metric.tone }} />
                </div>
              </Card>
            </Col>
          ))}
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={16}>
            <Card title="核心对象" style={{ borderRadius: 8, height: '100%' }}>
              <Table
                rowKey="key"
                columns={columns}
                dataSource={config.rows}
                pagination={false}
                size="middle"
                scroll={{ x: 640 }}
              />
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title="近期动态" style={{ borderRadius: 8, height: '100%' }}>
              <Timeline
                items={config.timeline.map((item, index) => ({
                  color: index === 0 ? config.accent : 'gray',
                  children: (
                    <div>
                      <Text>{item}</Text>
                      <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
                        {index === 0 ? '刚刚' : `${index * 12 + 8} 分钟前`}
                      </Text>
                    </div>
                  ),
                }))}
              />
            </Card>
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default WorkspacePage;

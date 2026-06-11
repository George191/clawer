import React, { useState } from 'react';
import { Button, Card, Col, Input, Progress, Row, Segmented, Space, Table, Tag, Timeline, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  LineChartOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

interface MetricRow {
  key: string;
  signal: string;
  value: string;
  threshold: string;
  status: 'healthy' | 'warning' | 'critical';
  owner: string;
}

const metrics: MetricRow[] = [
  { key: '1', signal: '采集成功率', value: '96.4%', threshold: '> 95%', status: 'healthy', owner: 'AI Collect' },
  { key: '2', signal: 'P95 页面延迟', value: '1.8s', threshold: '< 3s', status: 'healthy', owner: 'Runtime' },
  { key: '3', signal: '字段缺失率', value: '1.7%', threshold: '< 1%', status: 'warning', owner: 'Governance' },
  { key: '4', signal: '代理池可用率', value: '86%', threshold: '> 80%', status: 'healthy', owner: 'Identity' },
];

const statusColor: Record<MetricRow['status'], string> = {
  healthy: 'success',
  warning: 'warning',
  critical: 'error',
};

const Monitoring: React.FC = () => {
  const { token } = theme.useToken();
  const [view, setView] = useState('overview');

  const columns: ColumnsType<MetricRow> = [
    { title: '信号', dataIndex: 'signal', render: (value: string) => <Text strong>{value}</Text> },
    { title: '当前值', dataIndex: 'value', width: 120 },
    { title: '阈值', dataIndex: 'threshold', width: 120, render: (value: string) => <Text type="secondary">{value}</Text> },
    { title: '状态', dataIndex: 'status', width: 120, render: (status: MetricRow['status']) => <Tag color={statusColor[status]}>{status}</Tag> },
    { title: '负责人', dataIndex: 'owner', width: 140 },
  ];

  return (
    <ErrorBoundary>
      <PageHeader
        title="实时监控"
        subtitle="面向 AI 采集任务的 SLI、队列、错误、容量和告警收敛视图。"
        extra={
          <Space>
            <Input prefix={<SearchOutlined />} placeholder="搜索任务 / 源站 / 指标" style={{ width: 240 }} />
            <Segmented value={view} onChange={(value) => setView(String(value))} options={[
              { label: '总览', value: 'overview' },
              { label: '源站', value: 'source' },
              { label: '容量', value: 'capacity' },
            ]} />
            <Button icon={<ReloadOutlined />}>刷新</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={[16, 16]}>
          {[
            ['成功率', '96.4%', '目标 > 95%', '#10B981', <LineChartOutlined />],
            ['P95 延迟', '1.8s', '目标 < 3s', '#0EA5E9', <ClockCircleOutlined />],
            ['活跃告警', '3', '2 条已收敛', '#F59E0B', <AlertOutlined />],
            ['运行容量', '72%', 'AI Agent 槽位', '#7C3AED', <CloudServerOutlined />],
          ].map(([label, value, hint, color, icon]) => (
            <Col xs={24} sm={12} xl={6} key={String(label)}>
              <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 16 } }}>
                <Space align="start">
                  <span style={{ color: String(color), fontSize: 20 }}>{icon}</span>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{label}</Text>
                    <div style={{ color: token.colorText, fontSize: 26, fontWeight: 800 }}>{value}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{hint}</Text>
                  </div>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={15}>
            <Card title={<Space><BarChartOutlined /> 服务水平信号</Space>} style={{ borderRadius: 8 }}>
              <Table rowKey="key" columns={columns} dataSource={metrics} pagination={false} />
            </Card>
          </Col>
          <Col xs={24} xl={9}>
            <Card title="告警收敛" style={{ borderRadius: 8, height: '100%' }}>
              <Timeline
                items={[
                  { color: 'orange', children: <><Text strong>字段缺失率超过阈值</Text><Text type="secondary" style={{ display: 'block' }}>google_patent_contract · 已通知负责人</Text></> },
                  { color: 'green', children: <><Text strong>代理池恢复到 86%</Text><Text type="secondary" style={{ display: 'block' }}>identity pool · 自动恢复</Text></> },
                  { color: 'blue', children: <><Text strong>队列积压下降</Text><Text type="secondary" style={{ display: 'block' }}>browser-render pool · 12 分钟前</Text></> },
                ]}
              />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={8}>
            <Card title="队列深度" style={{ borderRadius: 8 }}>
              {[
                ['AI Agent', 72],
                ['Browser Render', 58],
                ['HTTP Parser', 31],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text>{label}</Text>
                    <Text type="secondary">{value}%</Text>
                  </div>
                  <Progress percent={Number(value)} showInfo={false} />
                </div>
              ))}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title="错误分布" style={{ borderRadius: 8 }}>
              {[
                ['字段缺失', 42, '#F59E0B'],
                ['网络超时', 18, '#0EA5E9'],
                ['身份拦截', 12, '#EF4444'],
              ].map(([label, value, color]) => (
                <div key={String(label)} style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text>{label}</Text>
                    <Text type="secondary">{value}</Text>
                  </div>
                  <Progress percent={Number(value)} showInfo={false} strokeColor={String(color)} />
                </div>
              ))}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title={<Space><FileTextOutlined /> 日志入口</Space>} style={{ borderRadius: 8, height: '100%' }}>
              <Text type="secondary" style={{ lineHeight: 1.8 }}>
                对应日志追踪页面提供任务、源站、等级、trace id 和字段漂移标签过滤。监控页只保留可行动摘要，避免把日志流塞进指标面板。
              </Text>
              <Button type="primary" style={{ marginTop: 18 }} href="/logs">打开日志追踪</Button>
            </Card>
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default Monitoring;

import React, { useMemo, useState } from 'react';
import { Button, Card, Col, Input, Progress, Row, Segmented, Space, Table, Tag, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  FieldTimeOutlined,
  FileTextOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

interface CollectTask {
  key: string;
  name: string;
  template: string;
  status: 'running' | 'queued' | 'completed' | 'failed' | 'paused';
  progress: number;
  records: string;
  lag: string;
  nextRun: string;
  owner: string;
}

const tasks: CollectTask[] = [
  { key: '1', name: 'google_patent_daily', template: 'google_patent_contract@v1.8', status: 'running', progress: 68, records: '18.2K', lag: '42s', nextRun: '持续运行', owner: 'AI Collect' },
  { key: '2', name: 'sealagom_navwarn_sync', template: 'sealagom_navwarn_contract@v2.1', status: 'running', progress: 82, records: '4.6K', lag: '18s', nextRun: '持续运行', owner: 'Crawler Team' },
  { key: '3', name: 'zdopen_notice_probe', template: 'zdopen_notice_contract@v0.9', status: 'failed', progress: 34, records: '860', lag: 'blocked', nextRun: '等待恢复', owner: 'Data Ops' },
  { key: '4', name: 'pdf_document_extract', template: 'pdf_document_extract@v1.3', status: 'queued', progress: 0, records: '0', lag: '-', nextRun: '18:30', owner: 'ETL Team' },
];

const statusColor: Record<CollectTask['status'], string> = {
  running: 'processing',
  queued: 'default',
  completed: 'success',
  failed: 'error',
  paused: 'warning',
};

const TaskCenter: React.FC = () => {
  const { token } = theme.useToken();
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<string>('all');

  const filtered = useMemo(() => tasks.filter((task) => {
    const matchStatus = status === 'all' || task.status === status;
    const matchKeyword = !keyword || `${task.name} ${task.template}`.toLowerCase().includes(keyword.toLowerCase());
    return matchStatus && matchKeyword;
  }), [keyword, status]);

  const columns: ColumnsType<CollectTask> = [
    {
      title: '任务',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={2}>
          <Space>
            <Text strong>{name}</Text>
            <Tag color={statusColor[record.status]}>{record.status}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.template}</Text>
        </Space>
      ),
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 180,
      render: (value: number, record) => <Progress percent={value} status={record.status === 'failed' ? 'exception' : record.status === 'running' ? 'active' : 'normal'} />,
    },
    { title: '记录数', dataIndex: 'records', width: 100 },
    { title: '延迟', dataIndex: 'lag', width: 100, render: (value: string) => <Text type={value === 'blocked' ? 'danger' : undefined}>{value}</Text> },
    { title: '下次运行', dataIndex: 'nextRun', width: 120 },
    { title: '负责人', dataIndex: 'owner', width: 130 },
    {
      title: '操作',
      width: 190,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={record.status === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}>{record.status === 'running' ? '暂停' : '启动'}</Button>
          <Button size="small" icon={<FileTextOutlined />}>日志</Button>
        </Space>
      ),
    },
  ];

  return (
    <ErrorBoundary>
      <PageHeader
        title="采集任务"
        subtitle="基于模板版本运行采集任务，关注队列、进度、延迟、失败恢复和下游写入。"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />}>新建任务</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={[16, 16]}>
          {[
            ['运行中', '2', '端到端持续运行', '#0EA5E9', <ThunderboltOutlined />],
            ['队列等待', '1', '18:30 调度窗口', '#F59E0B', <FieldTimeOutlined />],
            ['今日入湖', '22.8K', '写入 ODS/RDS', '#10B981', <PlayCircleOutlined />],
          ].map(([label, value, hint, color, icon]) => (
            <Col xs={24} md={8} key={String(label)}>
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

        <Card
          title="任务运行控制台"
          extra={
            <Space>
              <Input prefix={<SearchOutlined />} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索任务/模板" style={{ width: 240 }} />
              <Segmented value={status} onChange={(value) => setStatus(String(value))} options={[
                { label: '全部', value: 'all' },
                { label: '运行', value: 'running' },
                { label: '队列', value: 'queued' },
                { label: '失败', value: 'failed' },
              ]} />
            </Space>
          }
          style={{ borderRadius: 8 }}
        >
          <Table rowKey="key" columns={columns} dataSource={filtered} pagination={false} scroll={{ x: 980 }} />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={14}>
            <Card title="失败恢复队列" style={{ borderRadius: 8, height: '100%' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {[
                  ['zdopen_notice_probe', '验证码风险升高，已触发降速和身份池切换', '待试跑验证'],
                  ['quality_missing_scan', 'abstract 缺失率超过 1%，等待字段合约复核', '待负责人确认'],
                ].map(([name, desc, tag]) => (
                  <div key={name} style={{ padding: 14, borderRadius: 8, background: token.colorFillAlter }}>
                    <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                      <Text strong>{name}</Text>
                      <Tag color="warning">{tag}</Tag>
                    </Space>
                    <Text type="secondary" style={{ display: 'block', marginTop: 6 }}>{desc}</Text>
                  </div>
                ))}
              </div>
            </Card>
          </Col>
          <Col xs={24} xl={10}>
            <Card title="调度容量" style={{ borderRadius: 8, height: '100%' }}>
              {[
                ['AI Agent 并发槽', 72],
                ['浏览器渲染池', 58],
                ['代理池可用率', 86],
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
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default TaskCenter;

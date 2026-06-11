import React, { useMemo, useState } from 'react';
import { Button, Card, Col, Input, Row, Segmented, Select, Space, Table, Tag, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ClearOutlined,
  CodeOutlined,
  FieldTimeOutlined,
  FileTextOutlined,
  SearchOutlined,
  VerticalAlignBottomOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

interface LogRow {
  key: string;
  time: string;
  level: 'INFO' | 'WARN' | 'ERROR' | 'DEBUG';
  service: string;
  traceId: string;
  message: string;
  labels: string[];
}

const logs: LogRow[] = [
  { key: '1', time: '18:24:12.842', level: 'INFO', service: 'ai-agent', traceId: 'trc-82f1', message: 'schema contract generated for google_patent_contract', labels: ['template', 'contract'] },
  { key: '2', time: '18:24:15.103', level: 'WARN', service: 'quality-gate', traceId: 'trc-82f1', message: 'abstract missing rate reached 1.7%', labels: ['quality', 'field_missing'] },
  { key: '3', time: '18:24:18.421', level: 'ERROR', service: 'identity-pool', traceId: 'trc-a19c', message: 'captcha risk triggered, switch to slow lane', labels: ['identity', 'captcha'] },
  { key: '4', time: '18:24:21.006', level: 'INFO', service: 'writer-ods', traceId: 'trc-82f1', message: 'batch committed to ods_patent.raw_page', labels: ['ods', 'commit'] },
];

const levelColor: Record<LogRow['level'], string> = {
  INFO: 'blue',
  WARN: 'warning',
  ERROR: 'error',
  DEBUG: 'default',
};

const LogExplorer: React.FC = () => {
  const { token } = theme.useToken();
  const [query, setQuery] = useState('service:ai-agent trace:trc-82f1');
  const [level, setLevel] = useState<string>('all');

  const filtered = useMemo(() => logs.filter((log) => {
    const matchLevel = level === 'all' || log.level === level;
    const matchQuery = !query || `${log.service} ${log.traceId} ${log.message} ${log.labels.join(' ')}`.toLowerCase().includes(query.replace(/service:|trace:/g, '').toLowerCase().split(' ')[0] || '');
    return matchLevel && matchQuery;
  }), [level, query]);

  const columns: ColumnsType<LogRow> = [
    { title: '时间', dataIndex: 'time', width: 120, render: (time: string) => <Text type="secondary">{time}</Text> },
    { title: '等级', dataIndex: 'level', width: 90, render: (value: LogRow['level']) => <Tag color={levelColor[value]}>{value}</Tag> },
    { title: '服务', dataIndex: 'service', width: 140 },
    { title: 'Trace', dataIndex: 'traceId', width: 120, render: (value: string) => <Text code>{value}</Text> },
    { title: '消息', dataIndex: 'message', render: (value: string) => <Text>{value}</Text> },
    { title: '标签', dataIndex: 'labels', width: 190, render: (labels: string[]) => <Space size={4} wrap>{labels.map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space> },
  ];

  return (
    <ErrorBoundary>
      <PageHeader
        title="日志追踪"
        subtitle="参考 Log Explorer / Loki Explore 的查询体验，按 trace、服务、等级和标签定位采集链路问题。"
        extra={
          <Space>
            <Button icon={<VerticalAlignBottomOutlined />}>自动滚动</Button>
            <Button icon={<ClearOutlined />}>清空</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card style={{ borderRadius: 8 }} styles={{ body: { padding: 16 } }}>
          <Row gutter={[12, 12]} align="middle">
            <Col xs={24} xl={13}>
              <Input prefix={<SearchOutlined />} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="service:ai-agent trace:trc-82f1 label:quality" />
            </Col>
            <Col xs={24} md={8} xl={5}>
              <Select value={level} onChange={setLevel} style={{ width: '100%' }} options={[
                { label: '全部等级', value: 'all' },
                { label: 'INFO', value: 'INFO' },
                { label: 'WARN', value: 'WARN' },
                { label: 'ERROR', value: 'ERROR' },
                { label: 'DEBUG', value: 'DEBUG' },
              ]} />
            </Col>
            <Col xs={24} md={16} xl={6}>
              <Segmented block options={[
                { label: '实时', value: 'live' },
                { label: '近 15 分钟', value: '15m' },
                { label: '近 1 小时', value: '1h' },
              ]} defaultValue="live" />
            </Col>
          </Row>
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={17}>
            <Card title={<Space><FileTextOutlined /> 日志流</Space>} style={{ borderRadius: 8 }}>
              <Table rowKey="key" columns={columns} dataSource={filtered} pagination={false} size="small" scroll={{ x: 980 }} />
            </Card>
          </Col>
          <Col xs={24} xl={7}>
            <Card title={<Space><CodeOutlined /> 选中 Trace 摘要</Space>} style={{ borderRadius: 8, height: '100%' }}>
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <div style={{ padding: 12, borderRadius: 8, background: token.colorFillAlter }}>
                  <Text type="secondary">Trace ID</Text>
                  <div><Text code>trc-82f1</Text></div>
                </div>
                {[
                  ['ai-agent', '生成字段合约', '18:24:12'],
                  ['quality-gate', '发现缺失率异常', '18:24:15'],
                  ['writer-ods', '写入 ODS 成功', '18:24:21'],
                ].map(([service, message, time]) => (
                  <div key={service} style={{ display: 'flex', gap: 10 }}>
                    <FieldTimeOutlined style={{ color: '#7C3AED', marginTop: 3 }} />
                    <div>
                      <Text strong>{service}</Text>
                      <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{message} · {time}</Text>
                    </div>
                  </div>
                ))}
              </Space>
            </Card>
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default LogExplorer;

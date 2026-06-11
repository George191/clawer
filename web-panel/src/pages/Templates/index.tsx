import React, { useMemo, useState } from 'react';
import { Button, Card, Col, Input, Progress, Row, Segmented, Space, Table, Tag, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ApiOutlined,
  BranchesOutlined,
  CodeOutlined,
  CopyOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text, Paragraph } = Typography;

interface TemplateAsset {
  key: string;
  name: string;
  domain: string;
  adapter: string;
  version: string;
  status: 'active' | 'draft' | 'deprecated';
  fields: number;
  quality: number;
  lastRun: string;
  owner: string;
}

const assets: TemplateAsset[] = [
  { key: '1', name: 'google_patent_contract', domain: 'patents.google.com', adapter: 'browser-agent', version: 'v1.8', status: 'active', fields: 18, quality: 94, lastRun: '8 分钟前', owner: 'AI Collect' },
  { key: '2', name: 'sealagom_navwarn_contract', domain: 'navigation warning', adapter: 'http-parser', version: 'v2.1', status: 'active', fields: 12, quality: 98, lastRun: '16 分钟前', owner: 'Crawler Team' },
  { key: '3', name: 'zdopen_notice_contract', domain: '政务公告', adapter: 'browser-render', version: 'v0.9', status: 'draft', fields: 15, quality: 76, lastRun: '1 小时前', owner: 'Data Ops' },
  { key: '4', name: 'pdf_document_extract', domain: 'PDF 附件', adapter: 'doc-parser', version: 'v1.3', status: 'active', fields: 9, quality: 91, lastRun: '32 分钟前', owner: 'ETL Team' },
];

const statusColor: Record<TemplateAsset['status'], string> = {
  active: 'success',
  draft: 'processing',
  deprecated: 'default',
};

const Templates: React.FC = () => {
  const { token } = theme.useToken();
  const [keyword, setKeyword] = useState('');
  const [view, setView] = useState<'assets' | 'versions'>('assets');

  const filtered = useMemo(() => (
    assets.filter((asset) => !keyword || `${asset.name} ${asset.domain} ${asset.adapter}`.toLowerCase().includes(keyword.toLowerCase()))
  ), [keyword]);

  const columns: ColumnsType<TemplateAsset> = [
    {
      title: '模板合约',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={2}>
          <Space>
            <Text strong>{name}</Text>
            <Tag color={statusColor[record.status]}>{record.status}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.domain}</Text>
        </Space>
      ),
    },
    {
      title: '适配器',
      dataIndex: 'adapter',
      render: (adapter: string, record) => (
        <Space>
          <CodeOutlined />
          <span>{adapter}</span>
          <Tag>{record.version}</Tag>
        </Space>
      ),
    },
    { title: '字段', dataIndex: 'fields', width: 80 },
    {
      title: '试跑质量',
      dataIndex: 'quality',
      width: 160,
      render: (quality: number) => <Progress percent={quality} showInfo={false} strokeColor={quality >= 90 ? '#10B981' : '#F59E0B'} size="small" />,
    },
    { title: '最近运行', dataIndex: 'lastRun', width: 120, render: (value: string) => <Text type="secondary">{value}</Text> },
    { title: '负责人', dataIndex: 'owner', width: 130 },
    {
      title: '操作',
      width: 180,
      render: () => (
        <Space>
          <Button size="small" icon={<ExperimentOutlined />}>试跑</Button>
          <Button size="small" icon={<CopyOutlined />}>复制</Button>
        </Space>
      ),
    },
  ];

  return (
    <ErrorBoundary>
      <PageHeader
        title="模板与适配器库"
        subtitle="沉淀 AI 编排生成的字段合约、解析适配器、版本和试跑质量，作为采集任务的标准输入。"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />}>新建模板</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row gutter={[16, 16]}>
          {[
            ['模板资产', '42', '32 个已发布', '#7C3AED', <FileSearchOutlined />],
            ['适配器', '18', '5 类运行时', '#0EA5E9', <CodeOutlined />],
            ['平均质量', '93.6%', '近 24 小时试跑', '#10B981', <ExperimentOutlined />],
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
          title={<Space><BranchesOutlined /> 资产列表</Space>}
          extra={
            <Space>
              <Input prefix={<SearchOutlined />} value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索模板/域名/适配器" style={{ width: 260 }} />
              <Segmented value={view} onChange={(value) => setView(value as 'assets' | 'versions')} options={[
                { label: '资产', value: 'assets' },
                { label: '版本', value: 'versions' },
              ]} />
            </Space>
          }
          style={{ borderRadius: 8 }}
        >
          <Table rowKey="key" columns={columns} dataSource={filtered} pagination={false} scroll={{ x: 980 }} />
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <Card title={<Space><ApiOutlined /> 标准模板结构</Space>} style={{ borderRadius: 8, height: '100%' }}>
              <Paragraph type="secondary">
                一个可发布模板由 source contract、field schema、adapter runtime、run policy 和 quality gate 组成。任务只引用模板版本，不直接复制解析逻辑。
              </Paragraph>
              <pre style={{ margin: 0, padding: 14, borderRadius: 8, background: token.colorFillAlter, color: token.colorTextSecondary, fontSize: 12 }}>
{`template:
  name: google_patent_contract
  adapter: browser-agent@v1.8
  fields: [title, publication_date, abstract]
  run_policy:
    schedule: "*/30 * * * *"
    rate_limit: "12 req/min"
  quality_gate:
    required_missing_rate: "< 1%"`}
              </pre>
            </Card>
          </Col>
          <Col xs={24} xl={12}>
            <Card title={<Space><ExperimentOutlined /> 发布门禁</Space>} style={{ borderRadius: 8, height: '100%' }}>
              {[
                ['字段必填缺失率', 98],
                ['重复记录检测', 94],
                ['字段漂移稳定性', 88],
                ['适配器错误恢复', 91],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <Text>{label}</Text>
                    <Text type="secondary">{value}%</Text>
                  </div>
                  <Progress percent={Number(value)} showInfo={false} strokeColor={Number(value) >= 90 ? '#10B981' : '#F59E0B'} />
                </div>
              ))}
            </Card>
          </Col>
        </Row>
      </div>
    </ErrorBoundary>
  );
};

export default Templates;

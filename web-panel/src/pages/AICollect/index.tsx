import React, { useCallback, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Input,
  InputNumber,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Steps,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  theme,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleOutlined,
  CodeOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import {
  type DryRunResponse,
  type FieldDef,
  createAnalyzeStream,
  dryRun as dryRunApi,
  generateTemplate as generateTemplateApi,
} from '@/services/aiApi';

const { Text } = Typography;
const { TextArea } = Input;

type WorkMode = 'explore' | 'contract' | 'dryrun' | 'publish';

const sampleFields: FieldDef[] = [
  { name: 'title', selector: 'h1, .title', type: 'text', sample: 'Autonomous navigation route planning', required: true },
  { name: 'publication_date', selector: 'time, [data-date]', type: 'date', sample: '2026-05-28', required: true },
  { name: 'source_url', selector: 'link[canonical]', type: 'url', sample: 'https://patents.google.com/...', required: true },
  { name: 'abstract', selector: '.abstract, meta[name=description]', type: 'text', sample: 'Route planning method using sensor fusion', required: false },
  { name: 'attachment', selector: 'a[href$=".pdf"]', type: 'url', sample: 'US202601234.pdf', required: false },
];

const sampleRows = [
  {
    title: 'Autonomous navigation route planning',
    publication_date: '2026-05-28',
    source_url: 'patents.google.com/patent/US...',
    abstract: 'Route planning method using sensor fusion',
    attachment: 'US202601234.pdf',
  },
  {
    title: 'Maritime warning ingestion',
    publication_date: '2026-05-26',
    source_url: 'navcen.example/notice/...',
    abstract: 'Structured warning notice extraction',
    attachment: 'notice.html',
  },
];

const runEvents = [
  '发现列表页与详情页结构',
  '生成字段合约和命名建议',
  '验证翻页、速率和失败重试策略',
  '准备发布模板与适配器版本',
];

const AICollect: React.FC = () => {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [mode, setMode] = useState<WorkMode>('explore');
  const [url, setUrl] = useState('https://patents.google.com/search?q=autonomous+navigation');
  const [intent, setIntent] = useState('采集标题、发布日期、摘要、附件链接和来源 URL，写入 ODS 专利主题表。');
  const [renderMode, setRenderMode] = useState('agent');
  const [maxPages, setMaxPages] = useState(20);
  const [respectRobots, setRespectRobots] = useState(true);
  const [fields, setFields] = useState<FieldDef[]>(sampleFields);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set(sampleFields.map((field) => field.name)));
  const [streamError, setStreamError] = useState('');
  const [templateId, setTemplateId] = useState('ai-contract-preview');
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);

  const selectedCount = fields.filter((field) => selectedFields.has(field.name)).length;

  const validateUrl = useCallback((value: string) => {
    if (!value.trim()) return '请输入目标 URL';
    try {
      const parsed = new URL(value);
      if (!['http:', 'https:'].includes(parsed.protocol)) return '仅支持 HTTP/HTTPS 协议';
    } catch {
      return '请输入有效的 URL';
    }
    return '';
  }, []);

  const handleAnalyze = useCallback(() => {
    const error = validateUrl(url);
    if (error) {
      message.error(error);
      return;
    }

    setStreamError('');
    setMode('contract');
    const es = createAnalyzeStream(url);

    es.addEventListener('fields', (event: MessageEvent) => {
      const data: { fields: FieldDef[] } = JSON.parse(event.data);
      setFields(data.fields);
      setSelectedFields(new Set(data.fields.map((field) => field.name)));
    });

    es.addEventListener('complete', (event: MessageEvent) => {
      const data: { templateId: string } = JSON.parse(event.data);
      setTemplateId(data.templateId);
      message.success('AI 合约已生成');
      es.close();
    });

    es.addEventListener('error', () => {
      setStreamError('分析服务暂不可用，当前展示前端预览合约。');
      es.close();
    });

    es.onerror = () => {
      setStreamError('SSE 连接已断开，当前展示前端预览合约。');
      es.close();
    };
  }, [message, url, validateUrl]);

  const handleDryRun = useCallback(async () => {
    setMode('dryrun');
    try {
      const result = await dryRunApi(templateId, 20);
      setDryRunResult(result);
      message.success('试跑完成');
    } catch {
      setDryRunResult({
        totalPages: 3,
        totalItems: 42,
        columns: sampleFields.map((field) => field.name),
        sampleItems: sampleRows,
        duration: 8.4,
        errors: ['后端试跑接口暂不可用，当前展示前端样本。'],
      });
      message.warning('试跑接口暂不可用，已展示前端样本');
    }
  }, [message, templateId]);

  const handleSave = useCallback(async () => {
    try {
      await generateTemplateApi({
        url,
        options: {
          maxPages,
          fieldOverrides: fields
            .filter((field) => selectedFields.has(field.name))
            .map((field) => ({ name: field.name })),
        },
      });
      message.success('模板和适配器已发布');
    } catch {
      message.success('前端模板草案已生成，等待接入发布接口');
    }
    setMode('publish');
  }, [fields, maxPages, message, selectedFields, url]);

  const fieldColumns: ColumnsType<FieldDef> = [
    {
      title: (
        <Checkbox
          checked={selectedCount === fields.length}
          indeterminate={selectedCount > 0 && selectedCount < fields.length}
          onChange={() => {
            setSelectedFields((prev) => (
              prev.size === fields.length ? new Set() : new Set(fields.map((field) => field.name))
            ));
          }}
        />
      ),
      width: 42,
      render: (_, record) => (
        <Checkbox
          checked={selectedFields.has(record.name)}
          onChange={() => {
            setSelectedFields((prev) => {
              const next = new Set(prev);
              next.has(record.name) ? next.delete(record.name) : next.add(record.name);
              return next;
            });
          }}
        />
      ),
    },
    {
      title: '字段',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={2}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.selector}</Text>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'type', width: 90, render: (type: string) => <Tag>{type}</Tag> },
    { title: '样本', dataIndex: 'sample', ellipsis: true, render: (sample: string) => <Text type="secondary">{sample}</Text> },
    { title: '规则', dataIndex: 'required', width: 90, render: (required: boolean) => required ? <Tag color="green">必填</Tag> : <Tag>可选</Tag> },
  ];

  const previewColumns: ColumnsType<Record<string, unknown>> = useMemo(() => {
    const columns = dryRunResult?.columns ?? fields.map((field) => field.name);
    return columns.map((column) => ({
      title: column,
      dataIndex: column,
      key: column,
      ellipsis: true,
    }));
  }, [dryRunResult?.columns, fields]);

  return (
    <ErrorBoundary>
      <PageHeader
        title="智能采集编排"
        subtitle="用采集意图生成标准模板与适配器，并在发布前完成合约、试跑和运行策略确认。"
        extra={
          <Space>
            <Button icon={<ExperimentOutlined />} onClick={handleDryRun}>试跑</Button>
            <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>发布模板</Button>
          </Space>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Card style={{ borderRadius: 8 }} styles={{ body: { padding: '10px 14px' } }}>
          <Row gutter={[0, 8]} align="middle">
            {[
              ['模板合约', `${selectedCount}/${fields.length} 字段`, '已确认', <FileSearchOutlined />],
              ['适配器', renderMode === 'agent' ? 'AI Agent' : renderMode, 'adapter-v1.8', <RobotOutlined />],
              ['运行策略', `${maxPages} 页`, respectRobots ? '合规限流' : '自定义', <SafetyCertificateOutlined />],
              ['质量门禁', '88%', '可发布', <CheckCircleOutlined />],
            ].map(([title, value, hint, icon], index) => (
              <Col xs={12} lg={6} key={String(title)}>
                <div
                  style={{
                    minHeight: 48,
                    padding: '4px 14px',
                    borderLeft: index === 0 ? 'none' : `1px solid ${token.colorBorderSecondary}`,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                  }}
                >
                  <span style={{ color: token.colorTextSecondary, fontSize: 16 }}>{icon}</span>
                  <div style={{ minWidth: 0 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>{title}</Text>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <Text strong style={{ fontSize: 15 }}>{value}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>{hint}</Text>
                    </div>
                  </div>
                </div>
              </Col>
            ))}
          </Row>
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={8}>
            <Card
              title={<Space><ThunderboltOutlined /> 采集意图</Space>}
              size="small"
              style={{ borderRadius: 8, height: '100%' }}
              styles={{ body: { padding: 14 } }}
            >
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>目标 URL / 通配范围</Text>
                  <Input size="middle" value={url} prefix={<LinkOutlined />} onChange={(event) => setUrl(event.target.value)} style={{ marginTop: 4 }} />
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>采集目标</Text>
                  <TextArea value={intent} onChange={(event) => setIntent(event.target.value)} autoSize={{ minRows: 3, maxRows: 4 }} style={{ marginTop: 4 }} />
                </div>
                <Row gutter={10}>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 12 }}>渲染方式</Text>
                    <Select
                      size="middle"
                      value={renderMode}
                      onChange={setRenderMode}
                      style={{ width: '100%', marginTop: 4 }}
                      options={[
                        { label: '静态解析', value: 'static' },
                        { label: '浏览器渲染', value: 'browser' },
                        { label: 'AI Agent', value: 'agent' },
                      ]}
                    />
                  </Col>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 12 }}>最大页数</Text>
                    <InputNumber min={1} max={500} value={maxPages} onChange={(value) => setMaxPages(value ?? 20)} style={{ width: '100%', marginTop: 4 }} />
                  </Col>
                </Row>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
                  <Text type="secondary">启用合规速率</Text>
                  <Switch checked={respectRobots} onChange={setRespectRobots} />
                </div>
                <Button type="primary" block icon={<RobotOutlined />} onClick={handleAnalyze}>生成模板与适配器</Button>
              </Space>
            </Card>
          </Col>

          <Col xs={24} xl={16}>
            <Card
              title={<Space><GlobalOutlined /> 工作流</Space>}
              extra={<Segmented value={mode} onChange={(value) => setMode(value as WorkMode)} options={[
                { label: '探索', value: 'explore' },
                { label: '合约', value: 'contract' },
                { label: '试跑', value: 'dryrun' },
                { label: '发布', value: 'publish' },
              ]} />}
              size="small"
              style={{ borderRadius: 8, height: '100%' }}
              styles={{ body: { padding: 14 } }}
            >
              <Steps
                size="small"
                current={['explore', 'contract', 'dryrun', 'publish'].indexOf(mode)}
                items={[
                  { title: '源站探索', description: 'URL、列表/详情、翻页' },
                  { title: '模板合约', description: '字段、类型、必填规则' },
                  { title: '适配器试跑', description: '样本、错误、质量评分' },
                  { title: '发布运行', description: '调度、限流、监控' },
                ]}
              />
              {streamError && <Alert type="warning" showIcon message={streamError} style={{ marginTop: 16 }} />}
              <Divider style={{ margin: '14px 0' }} />
              <Row gutter={[14, 14]}>
                <Col xs={24} lg={9}>
                  <div style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 8, padding: '10px 12px', height: '100%' }}>
                    <Text strong style={{ display: 'block', marginBottom: 8 }}>阶段事件</Text>
                    <Timeline
                      items={runEvents.map((event, index) => ({
                        color: index <= ['explore', 'contract', 'dryrun', 'publish'].indexOf(mode) ? token.colorPrimary : 'gray',
                        children: <Text style={{ fontSize: 13 }}>{event}</Text>,
                      }))}
                    />
                  </div>
                </Col>
                <Col xs={24} lg={15}>
                  <div style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 8, padding: '10px 12px', height: '100%' }}>
                    <Descriptions column={2} size="small" colon={false}>
                      <Descriptions.Item label="模板类型">网页结构化采集</Descriptions.Item>
                      <Descriptions.Item label="适配器版本">adapter-v1.8</Descriptions.Item>
                      <Descriptions.Item label="翻页策略">next-selector / scroll fallback</Descriptions.Item>
                      <Descriptions.Item label="失败重试">3 次 / 指数退避</Descriptions.Item>
                      <Descriptions.Item label="身份策略">代理池 + 指纹轮换</Descriptions.Item>
                      <Descriptions.Item label="质量检查">必填、重复、漂移</Descriptions.Item>
                    </Descriptions>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 }}>
                      <Text type="secondary" style={{ whiteSpace: 'nowrap' }}>发布就绪度</Text>
                      <Progress percent={88} strokeColor={token.colorPrimary} style={{ margin: 0 }} />
                    </div>
                  </div>
                </Col>
              </Row>
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={14}>
            <Card title={<Space><FileSearchOutlined /> 字段合约</Space>} size="small" style={{ borderRadius: 8, height: '100%' }} styles={{ body: { padding: 0 } }}>
              <Table rowKey="name" columns={fieldColumns} dataSource={fields} pagination={false} scroll={{ x: 760 }} size="small" />
            </Card>
          </Col>
          <Col xs={24} xl={10}>
            <Card title={<Space><CodeOutlined /> 适配器草案</Space>} size="small" style={{ borderRadius: 8, height: '100%' }} styles={{ body: { padding: 12 } }}>
              <pre
                style={{
                  margin: 0,
                  minHeight: 230,
                  maxHeight: 320,
                  overflow: 'auto',
                  padding: 12,
                  borderRadius: 8,
                  background: token.colorFillAlter,
                  color: token.colorTextSecondary,
                  fontSize: 12,
                }}
              >
                {JSON.stringify({
                  template: 'google_patent_contract',
                  adapter: 'browser-agent',
                  source: url,
                  intent,
                  fields: fields.filter((field) => selectedFields.has(field.name)).map((field) => field.name),
                  runPolicy: { maxPages, retry: 3, rateLimit: '12 req/min' },
                }, null, 2)}
              </pre>
            </Card>
          </Col>
        </Row>

        <Card title={<Space><PlayCircleOutlined /> 试跑样本</Space>} size="small" style={{ borderRadius: 8 }} styles={{ body: { padding: 0 } }}>
          <Table
            columns={previewColumns}
            dataSource={(dryRunResult?.sampleItems ?? sampleRows).map((row, index) => ({ ...row, key: index }))}
            pagination={false}
            size="small"
            scroll={{ x: 900 }}
          />
        </Card>
      </div>
    </ErrorBoundary>
  );
};

export default AICollect;

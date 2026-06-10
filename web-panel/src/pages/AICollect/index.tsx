/**
 * AI 智能采集页面 — 输入 URL → AI 分析 → 预览确认 → 试采 → 保存
 */
import React, { useState, useRef, useCallback, useEffect } from 'react';
import {
  Card,
  Input,
  Button,
  Steps,
  Table,
  Checkbox,
  Tag,
  Space,
  Typography,
  Alert,
  Spin,
  Descriptions,
  message,
  Tooltip,
  Input as AntInput,
} from 'antd';
import {
  ThunderboltOutlined,
  LinkOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PlayCircleOutlined,
  SaveOutlined,
  ReloadOutlined,
  EditOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  type FieldDef,
  type PaginationStrategy,
  type DryRunResponse,
  createAnalyzeStream,
  dryRun as dryRunApi,
  generateTemplate as generateTemplateApi,
} from '@/services/aiApi';

const { Title, Text, Paragraph } = Typography;

// ── 流程步骤 ────────────────────────────────────────────────────────────────

type FlowStep = 'input' | 'analyzing' | 'preview' | 'dry-running' | 'result';

const flowSteps = [
  { key: 'input' as const, title: '输入 URL' },
  { key: 'analyzing' as const, title: 'AI 分析' },
  { key: 'preview' as const, title: '预览确认' },
  { key: 'dry-running' as const, title: '试采验证' },
  { key: 'result' as const, title: '保存' },
];

function stepIndex(key: FlowStep): number {
  return flowSteps.findIndex((s) => s.key === key);
}

// ── 组件 ────────────────────────────────────────────────────────────────────

const AICollect: React.FC = () => {
  // ── 状态 ──
  const [flowStep, setFlowStep] = useState<FlowStep>('input');
  const [url, setUrl] = useState('');
  const [urlError, setUrlError] = useState('');

  // SSE 分析状态
  const [thinking, setThinking] = useState<string[]>([]);
  const [steps, setSteps] = useState<Map<string, { step: string; label: string; status: string; error?: string }>>(new Map());
  const [fields, setFields] = useState<FieldDef[]>([]);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set());
  const [pagination, setPagination] = useState<PaginationStrategy | null>(null);
  const [templateId, setTemplateId] = useState('');
  const [templateYaml, setTemplateYaml] = useState('');
  const [streamError, setStreamError] = useState('');

  // 试采结果
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);

  // 字段改名
  const [editingField, setEditingField] = useState<string | null>(null);
  const [fieldNames, setFieldNames] = useState<Record<string, string>>({});

  const thinkingRef = useRef<HTMLDivElement>(null);

  // ── URL 校验 ──
  const validateUrl = useCallback((value: string) => {
    if (!value.trim()) return '请输入目标 URL';
    try {
      const u = new URL(value);
      if (!['http:', 'https:'].includes(u.protocol)) return '仅支持 HTTP/HTTPS 协议';
    } catch {
      return '请输入有效的 URL（以 http:// 或 https:// 开头）';
    }
    return '';
  }, []);

  // ── 开始分析 ──
  const handleAnalyze = useCallback(() => {
    const err = validateUrl(url);
    if (err) {
      setUrlError(err);
      return;
    }
    setUrlError('');
    setFlowStep('analyzing');
    setThinking([]);
    setSteps(new Map());
    setFields([]);
    setPagination(null);
    setStreamError('');
    setTemplateId('');
    setTemplateYaml('');

    const es = createAnalyzeStream(url);

    es.addEventListener('thinking', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setThinking((prev) => [...prev, data.content]);
      // 自动滚动
      setTimeout(() => {
        thinkingRef.current?.scrollTo({ top: thinkingRef.current.scrollHeight, behavior: 'smooth' });
      }, 50);
    });

    es.addEventListener('step', (e: MessageEvent) => {
      const data: { step: string; label: string; status: 'pending' | 'running' | 'done' | 'error'; error?: string } = JSON.parse(e.data);
      setSteps((prev) => {
        const next = new Map(prev);
        next.set(data.step, data);
        return next;
      });
    });

    es.addEventListener('fields', (e: MessageEvent) => {
      const data: { fields: FieldDef[] } = JSON.parse(e.data);
      setFields(data.fields);
      setSelectedFields(new Set(data.fields.map((f) => f.name)));
      setFieldNames(
        Object.fromEntries(data.fields.map((f) => [f.name, f.name])),
      );
    });

    es.addEventListener('pagination', (e: MessageEvent) => {
      const data: PaginationStrategy = JSON.parse(e.data);
      setPagination(data);
    });

    es.addEventListener('complete', (e: MessageEvent) => {
      const data: { templateYaml: string; templateId: string; fields: FieldDef[]; pagination: PaginationStrategy } = JSON.parse(e.data);
      setTemplateId(data.templateId);
      setTemplateYaml(data.templateYaml);
      setFlowStep('preview');
      es.close();
    });

    es.addEventListener('error', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        setStreamError(data.message || '分析过程出错');
      } catch {
        setStreamError('分析过程出错，连接已断开');
      }
      es.close();
    });

    // 连接级错误
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setStreamError((prev) => prev || 'SSE 连接已断开');
      }
    };
  }, [url, validateUrl]);

  // ── 字段选择 ──
  const toggleField = useCallback((name: string) => {
    setSelectedFields((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setSelectedFields((prev) => {
      if (prev.size === fields.length) return new Set();
      return new Set(fields.map((f) => f.name));
    });
  }, [fields]);

  // ── 字段改名 ──
  const startRename = useCallback((name: string) => {
    setEditingField(name);
  }, []);

  const commitRename = useCallback((oldName: string, newName: string) => {
    setFieldNames((prev) => ({ ...prev, [oldName]: newName || oldName }));
    setEditingField(null);
  }, []);

  // ── 试采 ──
  const handleDryRun = useCallback(async () => {
    setFlowStep('dry-running');
    try {
      const result = await dryRunApi(templateId || 'temp', 20);
      setDryRunResult(result);
      setFlowStep('result');
    } catch {
      message.error('试采失败，请重试');
      setFlowStep('preview');
    }
  }, [templateId]);

  // ── 保存模板 ──
  const handleSave = useCallback(async () => {
    const selectedFieldsList = fields.filter((f) => selectedFields.has(f.name));
    const overrides = Object.entries(fieldNames)
      .filter(([oldName, newName]) => oldName !== newName)
      .map(([name, rename]) => ({ name, rename }));

    try {
      await generateTemplateApi({
        url,
        options: {
          maxPages: pagination?.maxPages || 50,
          fieldOverrides: overrides,
        },
      });
      message.success('模板已保存到模板库');
      // 重置
      setFlowStep('input');
      setUrl('');
    } catch {
      message.error('保存失败，请重试');
    }
  }, [url, fields, selectedFields, fieldNames, pagination]);

  // ── 重置 ──
  const handleReset = () => {
    setFlowStep('input');
    setUrl('');
    setUrlError('');
    setThinking([]);
    setSteps(new Map());
    setFields([]);
    setPagination(null);
    setTemplateId('');
    setTemplateYaml('');
    setStreamError('');
    setDryRunResult(null);
  };

  // ── 表格列 ──
  const tableColumns: ColumnsType<Record<string, unknown>> =
    dryRunResult?.columns.map((col) => ({
      title: fieldNames[col] || col,
      dataIndex: col,
      key: col,
      ellipsis: true,
    })) || [];

  // ── 字段预览列 ──
  const fieldColumns: ColumnsType<FieldDef & { displayName: string }> = [
    {
      title: (
        <Checkbox
          checked={selectedFields.size === fields.length && fields.length > 0}
          indeterminate={selectedFields.size > 0 && selectedFields.size < fields.length}
          onChange={toggleAll}
        />
      ),
      width: 40,
      render: (_, record) => (
        <Checkbox
          checked={selectedFields.has(record.name)}
          onChange={() => toggleField(record.name)}
        />
      ),
    },
    {
      title: '字段名',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => {
        if (editingField === name) {
          return (
            <AntInput
              size="small"
              autoFocus
              defaultValue={fieldNames[name] || name}
              onPressEnter={(e) => commitRename(name, (e.target as HTMLInputElement).value)}
              onBlur={(e) => commitRename(name, e.target.value)}
              style={{ width: 120 }}
            />
          );
        }
        return (
          <Space size={4}>
            <Text>{fieldNames[name] || name}</Text>
            <Tooltip title="改名">
              <EditOutlined
                style={{ fontSize: 12, color: '#8b949e', cursor: 'pointer' }}
                onClick={() => startRename(name)}
              />
            </Tooltip>
          </Space>
        );
      },
    },
    {
      title: '选择器',
      dataIndex: 'selector',
      key: 'selector',
      render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 80,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: '采样值',
      dataIndex: 'sample',
      key: 'sample',
      ellipsis: true,
      render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: '必选',
      dataIndex: 'required',
      key: 'required',
      width: 60,
      render: (v: boolean) =>
        v ? (
          <CheckCircleOutlined style={{ color: '#52c41a' }} />
        ) : (
          <CloseCircleOutlined style={{ color: '#8b949e' }} />
        ),
    },
  ];

  // ── 渲染 ──
  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      {/* Hero */}
      <div className="hero-section" style={{ padding: '40px 24px 32px', marginBottom: 8 }}>
        <div style={{ position: 'relative', zIndex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <div
              style={{
                width: 36, height: 36,
                borderRadius: 10,
                background: 'linear-gradient(135deg, #8B5CF6 0%, #6D28D9 100%)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                boxShadow: '0 4px 16px rgba(139, 92, 246, 0.4)',
              }}
            >
              <ThunderboltOutlined style={{ fontSize: 18, color: '#fff' }} />
            </div>
            <span style={{
              fontSize: 11, fontWeight: 700, letterSpacing: '0.08em',
              color: '#C4B5FD', textTransform: 'uppercase',
              padding: '2px 10px', borderRadius: 4,
              background: 'rgba(139, 92, 246, 0.12)',
            }}>
              AI Powered
            </span>
          </div>
          <Title level={3} style={{ marginBottom: 6, fontSize: 22, letterSpacing: '-0.02em' }}>
            AI 智能采集
          </Title>
          <Text style={{ color: '#94A3B8' }}>
            输入目标网址，AI 自动分析页面结构，一键生成采集模板
          </Text>
        </div>
      </div>

      {/* 步骤条 */}
      <Steps
        current={stepIndex(flowStep)}
        items={flowSteps.map((s) => ({
          title: s.title,
          icon:
            s.key === 'analyzing' && flowStep === 'analyzing' ? (
              <LoadingOutlined />
            ) : stepIndex(flowStep) > stepIndex(s.key) ? (
              <CheckCircleOutlined style={{ color: '#52c41a' }} />
            ) : undefined,
        }))}
        style={{ marginBottom: 32 }}
      />

      {/* ── Step 1: 输入 URL ── */}
      {flowStep === 'input' && (
        <Card className="mission-card" styles={{ body: { padding: '24px' } }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="large"
              prefix={<LinkOutlined />}
              placeholder="输入目标网页 URL，如 https://example.com/list"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                if (urlError) setUrlError('');
              }}
              onPressEnter={handleAnalyze}
              status={urlError ? 'error' : undefined}
            />
            <Button
              size="large"
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={handleAnalyze}
              disabled={!url.trim()}
            >
              分析
            </Button>
          </Space.Compact>
          {urlError && (
            <Text type="danger" style={{ display: 'block', marginTop: 8 }}>
              {urlError}
            </Text>
          )}
          <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            支持任意公开网页。AI 会自动识别列表结构、提取字段、检测翻页方式。
          </Paragraph>
        </Card>
      )}

      {/* ── Step 2: AI 分析中 ── */}
      {flowStep === 'analyzing' && (
        <Card>
          <div style={{ textAlign: 'center', marginBottom: 24 }}>
            <Spin size="large" />
            <Title level={5} style={{ marginTop: 16 }}>
              AI 正在分析页面结构...
            </Title>
          </div>

          {streamError && (
            <Alert
              type="error"
              message="分析出错"
              description={streamError}
              style={{ marginBottom: 16 }}
              action={
                <Button size="small" onClick={handleAnalyze}>
                  重试
                </Button>
              }
            />
          )}

          {/* 步骤列表 */}
          {steps.size > 0 && (
            <div style={{ marginBottom: 16 }}>
              {Array.from(steps.values()).map((s) => (
                <div
                  key={s.step}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 0',
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  {s.status === 'running' && <LoadingOutlined style={{ color: '#3B82F6' }} />}
                  {s.status === 'done' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                  {s.status === 'error' && <CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
                  {s.status === 'pending' && (
                    <span style={{ width: 14, height: 14, borderRadius: '50%', border: '2px solid #8b949e' }} />
                  )}
                  <Text>{s.label}</Text>
                  {s.status === 'error' && s.error && (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {s.error}
                    </Text>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* 思考过程 */}
          <div
            ref={thinkingRef}
            style={{
              background: 'rgba(0,0,0,0.2)',
              borderRadius: 8,
              padding: 12,
              maxHeight: 200,
              overflow: 'auto',
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: 12,
            }}
          >
            {thinking.map((t, i) => (
              <div
                key={i}
                style={{
                  color: '#8b949e',
                  padding: '2px 0',
                  borderLeft: '2px solid rgba(59,130,246,0.3)',
                  paddingLeft: 8,
                  marginBottom: 4,
                }}
              >
                {t}
              </div>
            ))}
            {thinking.length === 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                等待 AI 响应...
              </Text>
            )}
          </div>
        </Card>
      )}

      {/* ── Step 3: 预览确认 ── */}
      {flowStep === 'preview' && (
        <>
          {/* 字段预览 */}
          <Card title="识别到的字段" style={{ marginBottom: 16 }}>
            <Paragraph type="secondary" style={{ marginBottom: 12 }}>
              勾选需要采集的字段，点击字段名可改名
            </Paragraph>
            <Table
              columns={fieldColumns}
              dataSource={fields.map((f) => ({ ...f, displayName: fieldNames[f.name] || f.name, key: f.name }))}
              pagination={false}
              size="small"
              scroll={{ x: 600 }}
            />
          </Card>

          {/* 分页策略 */}
          {pagination && (
            <Card title="分页策略" style={{ marginBottom: 16 }}>
              <Descriptions column={2} size="small">
                <Descriptions.Item label="翻页方式">
                  <Tag color="blue">{pagination.type}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="最大页数">{pagination.maxPages}</Descriptions.Item>
                {pagination.selector && (
                  <Descriptions.Item label="翻页选择器">
                    <Text code>{pagination.selector}</Text>
                  </Descriptions.Item>
                )}
                {pagination.params && (
                  <Descriptions.Item label="翻页参数">
                    <Text code>{JSON.stringify(pagination.params)}</Text>
                  </Descriptions.Item>
                )}
              </Descriptions>
            </Card>
          )}

          {/* YAML 预览 */}
          <Card title="生成的 YAML 模板" style={{ marginBottom: 16 }}>
            <pre
              style={{
                background: 'rgba(0,0,0,0.2)',
                borderRadius: 8,
                padding: 12,
                maxHeight: 200,
                overflow: 'auto',
                fontSize: 12,
                fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              {templateYaml}
            </pre>
          </Card>

          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleReset}>
              重新分析
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleDryRun}
              disabled={selectedFields.size === 0}
            >
              试采集
            </Button>
          </Space>
        </>
      )}

      {/* ── Step 4: 试采中 ── */}
      {flowStep === 'dry-running' && (
        <Card>
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin size="large" />
            <Title level={5} style={{ marginTop: 16 }}>
              正在试采集...
            </Title>
            <Text type="secondary">使用生成的模板采集少量数据以验证效果</Text>
          </div>
        </Card>
      )}

      {/* ── Step 5: 试采结果 ── */}
      {flowStep === 'result' && dryRunResult && (
        <>
          <Card style={{ marginBottom: 16 }}>
            <Descriptions column={4} size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="总页数">
                <Text strong>{dryRunResult.totalPages}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="总条数">
                <Text strong>{dryRunResult.totalItems}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="耗时">
                <Text strong>{dryRunResult.duration}s</Text>
              </Descriptions.Item>
              <Descriptions.Item label="错误">
                {dryRunResult.errors.length > 0 ? (
                  <Tag color="red">{dryRunResult.errors.length}</Tag>
                ) : (
                  <Tag color="green">0</Tag>
                )}
              </Descriptions.Item>
            </Descriptions>

            {dryRunResult.errors.length > 0 && (
              <Alert
                type="warning"
                message={`${dryRunResult.errors.length} 条错误`}
                description={dryRunResult.errors.join('; ')}
                style={{ marginBottom: 16 }}
              />
            )}
          </Card>

          <Card title="试采数据预览" style={{ marginBottom: 16 }}>
            <Table
              columns={tableColumns}
              dataSource={dryRunResult.sampleItems.map((item, i) => ({
                ...item,
                _key: i,
              }))}
              rowKey="_key"
              size="small"
              scroll={{ x: 800 }}
              pagination={{ pageSize: 10, showSizeChanger: false }}
            />
          </Card>

          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleDryRun}>
              重新试采
            </Button>
            <Button onClick={() => setFlowStep('preview')}>返回修改</Button>
            <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>
              保存到模板库
            </Button>
          </Space>
        </>
      )}
    </div>
  );
};

export default AICollect;

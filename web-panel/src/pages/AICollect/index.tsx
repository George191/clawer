import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Checkbox,
  Divider,
  Input,
  InputNumber,
  Progress,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  theme,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  BranchesOutlined,
  CaretRightOutlined,
  CheckCircleOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SearchOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
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
type MissionTab = 'goal' | 'policy';
type RunStatus = 'idle' | 'running' | 'paused' | 'completed';

const stageOrder: WorkMode[] = ['explore', 'contract', 'dryrun', 'publish'];

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

const renderModeLabel: Record<string, string> = {
  static: '静态解析',
  browser: '浏览器渲染',
  agent: 'AI Agent',
};

const scheduleModeLabel: Record<string, string> = {
  manual: '手动任务',
  cron: '周期任务',
  incremental: '增量任务',
};

const outputTargetLabel: Record<string, string> = {
  ods_patent: 'ODS 专利主题表',
  raw_dataset: '原始 Dataset',
  object_storage: '对象存储附件区',
};

const stageMeta: Record<WorkMode, { title: string; desc: string; action: string; score: number }> = {
  explore: {
    title: 'AI 正在还原页面逻辑',
    desc: '自动识别入口、列表、详情页、翻页、动态接口和可复用采集边界。',
    action: '生成字段合约',
    score: 92,
  },
  contract: {
    title: '字段合约待确认',
    desc: 'AI 已生成字段、类型、选择器和样本证据，用户只需要修正异常项。',
    action: '开始试跑',
    score: 88,
  },
  dryrun: {
    title: '样本试跑与质量门禁',
    desc: '用小批量数据验证字段完整率、重复率、漂移风险和失败重试策略。',
    action: '发布模板',
    score: 86,
  },
  publish: {
    title: '发布为模板资产',
    desc: '服务端生成模板、适配器和任务输入 Schema，再接入任务调度与 Socket 监控。',
    action: '查看发布计划',
    score: 94,
  },
};

const logicNodes = [
  {
    title: '入口识别',
    desc: '检测到搜索页可作为入口，URL 参数可以转为任务输入。',
    meta: 'query / page / sort',
    icon: <SearchOutlined />,
    status: '92%',
  },
  {
    title: '列表到详情',
    desc: 'AI 判断列表项需要进入详情页补齐摘要、附件与 canonical URL。',
    meta: 'list -> detail',
    icon: <BranchesOutlined />,
    status: '89%',
  },
  {
    title: '动态渲染',
    desc: '页面存在脚本渲染和跳转，建议优先使用 Browser Agent，静态解析作为 fallback。',
    meta: 'browserHtml + actions',
    icon: <GlobalOutlined />,
    status: '86%',
  },
  {
    title: '采集边界',
    desc: '设置最大页数、并发、速率、重试和断点续采，避免任务失控。',
    meta: 'policy guard',
    icon: <SafetyCertificateOutlined />,
    status: '95%',
  },
];

const publishPlan = [
  ['模板 YAML', '字段合约、选择器、翻页、输入参数'],
  ['适配器代码', 'Browser Agent、重试、fallback、附件处理'],
  ['模板库记录', '版本、启停、灰度、回滚、最近试跑'],
  ['调度任务', '手动、周期、增量窗口和失败补偿'],
  ['Socket 订阅', '按 taskId/templateId 推送进度、日志和产出'],
];

const socketEvents = [
  ['14:02:11', '任务触发', 'task-run-20260611-042 已进入调度队列'],
  ['14:02:18', '模板装载', 'google_patent_contract@v3 绑定 adapter-v1.8'],
  ['14:03:04', '批次产出', '第 2 页完成，累计 42 条，错误 0 条'],
  ['14:03:18', '质量检查', '字段完整率 98.2%，结构漂移低风险'],
];

const nextStepTips: Record<WorkMode, string[]> = {
  explore: ['确认入口 URL 是否覆盖完整范围', '让 AI 继续识别详情页字段', '对动态页面启用 Browser Agent'],
  contract: ['保留业务必需字段', '检查字段命名与目标表映射', '对低置信度字段补充样本'],
  dryrun: ['查看失败样本并回放', '确认完整率和重复率阈值', '将小样本结果保存为基线'],
  publish: ['发布模板版本', '创建周期采集任务', '订阅任务与模板监控事件'],
};

const runStatusMeta: Record<RunStatus, { label: string; color: string }> = {
  idle: { label: '未开始', color: 'default' },
  running: { label: '分析中', color: 'processing' },
  paused: { label: '已暂停', color: 'warning' },
  completed: { label: '待确认', color: 'success' },
};

const aura = {
  bg: '#171A1A',
  surface: '#1F2323',
  surfaceSoft: '#202525',
  border: '#3A4242',
  borderSoft: '#2B3131',
  text: '#F5F7F7',
  muted: '#BEC7C7',
  subtle: '#8F9999',
  accent: '#8FE3E8',
  accentSoft: 'rgba(143, 227, 232, 0.12)',
};

const AICollect: React.FC = () => {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const analyzeStreamRef = useRef<EventSource | null>(null);
  const [mode, setMode] = useState<WorkMode>('explore');
  const [missionTab, setMissionTab] = useState<MissionTab>('goal');
  const [runStatus, setRunStatus] = useState<RunStatus>('idle');
  const [url, setUrl] = useState('https://patents.google.com/search?q=autonomous+navigation');
  const [intent, setIntent] = useState('采集标题、发布日期、摘要、附件链接和来源 URL，写入 ODS 专利主题表。');
  const [renderMode, setRenderMode] = useState('agent');
  const [maxPages, setMaxPages] = useState(20);
  const [scheduleMode, setScheduleMode] = useState('cron');
  const [concurrency, setConcurrency] = useState(4);
  const [outputTarget, setOutputTarget] = useState('ods_patent');
  const [enableDriftGuard, setEnableDriftGuard] = useState(true);
  const [respectRobots, setRespectRobots] = useState(true);
  const [fields, setFields] = useState<FieldDef[]>(sampleFields);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set(sampleFields.map((field) => field.name)));
  const [streamError, setStreamError] = useState('');
  const [templateId, setTemplateId] = useState('ai-contract-preview');
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);

  const hasSession = runStatus !== 'idle';
  const stageIndex = stageOrder.indexOf(mode);
  const selectedCount = fields.filter((field) => selectedFields.has(field.name)).length;
  const qualityScore = mode === 'publish' ? 94 : mode === 'dryrun' ? 86 : mode === 'contract' ? 88 : 92;

  useEffect(() => () => {
    analyzeStreamRef.current?.close();
  }, []);

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

    analyzeStreamRef.current?.close();
    setStreamError('');
    setRunStatus('running');
    setMode('explore');
    const es = createAnalyzeStream(url);
    analyzeStreamRef.current = es;

    es.addEventListener('fields', (event: MessageEvent) => {
      const data: { fields: FieldDef[] } = JSON.parse(event.data);
      setFields(data.fields);
      setSelectedFields(new Set(data.fields.map((field) => field.name)));
      setMode('contract');
    });

    es.addEventListener('complete', (event: MessageEvent) => {
      const data: { templateId: string } = JSON.parse(event.data);
      setTemplateId(data.templateId);
      setRunStatus('completed');
      message.success('AI 合约已生成');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.addEventListener('error', () => {
      setStreamError('分析服务暂不可用，当前展示前端预览合约。');
      setRunStatus('completed');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.onerror = () => {
      setStreamError('SSE 连接已断开，当前展示前端预览合约。');
      setRunStatus('completed');
      es.close();
      analyzeStreamRef.current = null;
    };
  }, [message, url, validateUrl]);

  const handlePauseAnalysis = useCallback(() => {
    analyzeStreamRef.current?.close();
    analyzeStreamRef.current = null;
    setRunStatus('paused');
    message.info('已暂停当前分析');
  }, [message]);

  const handleResumeAnalysis = useCallback(() => {
    setRunStatus('running');
    message.success('已继续当前分析');
  }, [message]);

  const handleCancelAnalysis = useCallback(() => {
    analyzeStreamRef.current?.close();
    analyzeStreamRef.current = null;
    setRunStatus('idle');
    setMode('explore');
    setStreamError('');
    message.info('已取消当前分析');
  }, [message]);

  const handleDryRun = useCallback(async () => {
    setRunStatus('running');
    setMode('dryrun');
    try {
      const result = await dryRunApi(templateId, 20);
      setDryRunResult(result);
      setRunStatus('completed');
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
      setRunStatus('completed');
      message.warning('试跑接口暂不可用，已展示前端样本');
    }
  }, [message, templateId]);

  const handleSave = useCallback(async () => {
    setRunStatus('running');
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
    setRunStatus('completed');
  }, [fields, maxPages, message, selectedFields, url]);

  const fieldColumns: ColumnsType<FieldDef> = [
    {
      title: (
        <Checkbox
          checked={fields.length > 0 && selectedCount === fields.length}
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
      render: (name: string, record, index) => (
        <Space direction="vertical" size={2}>
          <Space size={6}>
            <Text strong>{name}</Text>
            <Tag color={index < 3 ? 'green' : 'blue'}>{index < 3 ? '高置信' : '可确认'}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.selector}</Text>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'type', width: 92, render: (type: string) => <Tag>{type}</Tag> },
    { title: '样本证据', dataIndex: 'sample', ellipsis: true, render: (sample: string) => <Text type="secondary">{sample}</Text> },
    { title: '规则', dataIndex: 'required', width: 88, render: (required: boolean) => required ? <Tag color="green">必填</Tag> : <Tag>可选</Tag> },
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

  const panelStyle: React.CSSProperties = {
    border: `1px solid ${aura.border}`,
    background: aura.surface,
    borderRadius: 8,
    minHeight: 0,
  };

  const renderMissionPanel = (variant: 'hero' | 'compact') => (
    <aside className={`ai-collect-panel ai-mission-panel ${variant === 'hero' ? 'ai-mission-hero' : ''}`} style={panelStyle}>
      <div className="ai-panel-head">
        <Space size={8}>
          <ThunderboltOutlined style={{ color: aura.accent }} />
          <Text strong className="ai-panel-title">{variant === 'hero' ? '创建智能采集' : '采集意图'}</Text>
        </Space>
        <Tag className="ai-aura-tag">{runStatusMeta[runStatus].label}</Tag>
      </div>

      <Segmented
        block
        size="small"
        value={missionTab}
        onChange={(value) => setMissionTab(value as MissionTab)}
        options={[
          { label: '目标', value: 'goal' },
          { label: '策略', value: 'policy' },
        ]}
      />

      <div className="ai-mission-content">
        {missionTab === 'goal' ? (
          <div className="ai-form-stack">
            <label>
              <Text type="secondary" style={{ fontSize: 12 }}>目标 URL / 通配范围</Text>
              <Input
                value={url}
                prefix={<LinkOutlined />}
                onChange={(event) => setUrl(event.target.value)}
                style={{ marginTop: 6 }}
              />
            </label>
            <label>
              <Text type="secondary" style={{ fontSize: 12 }}>采集目标</Text>
              <TextArea
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
                autoSize={{ minRows: 5, maxRows: 7 }}
                style={{ marginTop: 6 }}
              />
            </label>
            <Button className="ai-aura-primary" type="primary" size={variant === 'hero' ? 'large' : 'middle'} icon={<RobotOutlined />} block onClick={handleAnalyze}>
              开始智能分析
            </Button>
          </div>
        ) : (
          <div className="ai-form-stack">
            <label>
              <Text type="secondary" style={{ fontSize: 12 }}>渲染方式</Text>
              <Select
                value={renderMode}
                onChange={setRenderMode}
                style={{ width: '100%', marginTop: 6 }}
                options={[
                  { label: '静态解析', value: 'static' },
                  { label: '浏览器渲染', value: 'browser' },
                  { label: 'AI Agent', value: 'agent' },
                ]}
              />
            </label>
            <div className="ai-two-cols">
              <label>
                <Text type="secondary" style={{ fontSize: 12 }}>最大页数</Text>
                <InputNumber
                  min={1}
                  max={500}
                  value={maxPages}
                  onChange={(value) => setMaxPages(value ?? 20)}
                  style={{ width: '100%', marginTop: 6 }}
                />
              </label>
              <label>
                <Text type="secondary" style={{ fontSize: 12 }}>并发数</Text>
                <InputNumber
                  min={1}
                  max={50}
                  value={concurrency}
                  onChange={(value) => setConcurrency(value ?? 4)}
                  style={{ width: '100%', marginTop: 6 }}
                />
              </label>
            </div>
            <label>
              <Text type="secondary" style={{ fontSize: 12 }}>调度方式</Text>
              <Select
                value={scheduleMode}
                onChange={setScheduleMode}
                style={{ width: '100%', marginTop: 6 }}
                options={[
                  { label: '手动任务', value: 'manual' },
                  { label: '周期任务', value: 'cron' },
                  { label: '增量任务', value: 'incremental' },
                ]}
              />
            </label>
            <label>
              <Text type="secondary" style={{ fontSize: 12 }}>输出目标</Text>
              <Select
                value={outputTarget}
                onChange={setOutputTarget}
                style={{ width: '100%', marginTop: 6 }}
                options={[
                  { label: 'ODS 专利主题表', value: 'ods_patent' },
                  { label: '原始 Dataset', value: 'raw_dataset' },
                  { label: '对象存储附件区', value: 'object_storage' },
                ]}
              />
            </label>
            <div className="ai-toggle-row">
              <Text type="secondary">合规速率</Text>
              <Switch checked={respectRobots} onChange={setRespectRobots} />
            </div>
            <div className="ai-toggle-row">
              <Text type="secondary">漂移门禁</Text>
              <Switch checked={enableDriftGuard} onChange={setEnableDriftGuard} />
            </div>
          </div>
        )}
      </div>

      {variant === 'compact' && <Divider style={{ margin: '0 0 12px' }} />}

      {variant === 'compact' && (
        <div className="ai-mini-summary">
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>AI 推荐</Text>
              <Text strong className="ai-aura-value">{renderModeLabel[renderMode]}</Text>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>任务形态</Text>
              <Text strong className="ai-aura-value">{scheduleModeLabel[scheduleMode]}</Text>
          </div>
        </div>
      )}
    </aside>
  );

  const renderExplore = () => (
    <div className="ai-stage-stack ai-aura-flow">
      <div className="ai-aura-intro">
        <div>
          <Text className="ai-aura-kicker">Application development</Text>
          <Text strong className="ai-aura-title">AI 正在还原页面逻辑</Text>
          <Text className="ai-aura-copy">
            系统会按页面行为分层推断采集路线，再把用户确认过的结果发布成模板、适配器和可调度任务。
          </Text>
        </div>
        <Progress type="circle" percent={stageMeta.explore.score} size={72} strokeColor={aura.accent} trailColor={aura.borderSoft} />
      </div>

      <div className="ai-aura-steps">
        {logicNodes.map((item, index) => (
          <section className="ai-aura-step" key={item.title}>
            <span className="ai-aura-step-index">{index + 1}</span>
            <div className="ai-aura-step-body">
              <Space size={10} align="center">
                <span className="ai-aura-step-icon">{item.icon}</span>
                <Text strong className="ai-aura-step-title">{item.title}</Text>
                <Tag className="ai-aura-tag">{item.status}</Tag>
              </Space>
              <Text className="ai-aura-copy" style={{ display: 'block', marginTop: 10 }}>
                {item.desc}
              </Text>
              <Space size={14} wrap style={{ marginTop: 14 }}>
                <Button className="ai-aura-button">{item.meta}</Button>
                <Button type="link" className="ai-aura-link">查看证据</Button>
              </Space>
            </div>
          </section>
        ))}
      </div>
    </div>
  );

  const renderContract = () => (
    <div className="ai-stage-stack">
      <div className="ai-stage-toolbar">
        <div>
          <Text strong>{selectedCount}/{fields.length} 个字段进入模板</Text>
          <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
            AI 已附带选择器、样本证据和必填规则，取消勾选即可排除字段。
          </Text>
        </div>
        <Button icon={<ExperimentOutlined />} onClick={handleDryRun}>试跑</Button>
      </div>
      <Table
        rowKey="name"
        columns={fieldColumns}
        dataSource={fields}
        pagination={false}
        scroll={{ x: 820, y: 390 }}
        size="small"
      />
    </div>
  );

  const renderDryRun = () => (
    <div className="ai-stage-stack">
      {dryRunResult?.errors?.length ? (
        <Alert type="warning" showIcon message={dryRunResult.errors[0]} />
      ) : null}
      <div className="ai-quality-grid">
        {[
          ['字段完整率', '98.2%', 'title/source_url 必填通过'],
          ['重复率', '0.8%', 'URL + 标题去重'],
          ['结构漂移', '低', '选择器稳定'],
          ['耗时', `${dryRunResult?.duration ?? 8.4}s`, '20 条样本试跑'],
        ].map(([label, value, hint]) => (
          <div className="ai-quality-item" key={label}>
            <Text type="secondary" style={{ fontSize: 12 }}>{label}</Text>
            <Text strong style={{ display: 'block', marginTop: 6, fontSize: 20 }}>{value}</Text>
            <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>{hint}</Text>
          </div>
        ))}
      </div>
      <Table
        columns={previewColumns}
        dataSource={(dryRunResult?.sampleItems ?? sampleRows).map((row, index) => ({ ...row, key: index }))}
        pagination={false}
        size="small"
        scroll={{ x: 980, y: 270 }}
      />
    </div>
  );

  const renderPublish = () => (
    <div className="ai-stage-stack">
      <div className="ai-stage-focus">
        <div>
          <Tag color="green">Ready</Tag>
          <Text strong style={{ display: 'block', marginTop: 10, fontSize: 18 }}>模板资产可发布</Text>
          <Text type="secondary" style={{ display: 'block', marginTop: 6, lineHeight: 1.7 }}>
            发布后进入模板库维护，采集任务基于模板调度，实时监控通过 Socket 订阅任务和模板事件。
          </Text>
        </div>
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>发布模板</Button>
      </div>
      <div className="ai-publish-list">
        {publishPlan.map(([title, desc], index) => (
          <div className="ai-publish-row" key={title}>
            <span className="ai-publish-index">{index + 1}</span>
            <div>
              <Text strong>{title}</Text>
              <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 3 }}>{desc}</Text>
            </div>
          </div>
        ))}
      </div>
      <pre className="ai-code-block">
        {JSON.stringify({
          template: 'google_patent_contract',
          adapter: 'browser-agent',
          source: url,
          fields: fields.filter((field) => selectedFields.has(field.name)).map((field) => field.name),
          runPolicy: {
            scheduleMode,
            maxPages,
            concurrency,
            retry: 3,
            rateLimit: respectRobots ? '12 req/min' : 'custom',
            driftGuard: enableDriftGuard,
          },
          output: { target: outputTarget, mode: 'raw + normalized' },
        }, null, 2)}
      </pre>
    </div>
  );

  const renderStageContent = () => {
    if (mode === 'contract') return renderContract();
    if (mode === 'dryrun') return renderDryRun();
    if (mode === 'publish') return renderPublish();
    return renderExplore();
  };

  const renderContextRail = () => (
    <aside className="ai-collect-panel ai-collect-scroll" style={panelStyle}>
      <div className="ai-panel-head">
        <Space size={8}>
          <RobotOutlined style={{ color: aura.accent }} />
          <Text strong className="ai-panel-title">AI 上下文</Text>
        </Space>
        <Tag className="ai-aura-tag">{qualityScore}%</Tag>
      </div>

      <div className="ai-context-card">
        <Text type="secondary" style={{ fontSize: 12 }}>当前模板摘要</Text>
        <div className="ai-summary-list">
          <div><span>字段</span><strong>{selectedCount}/{fields.length}</strong></div>
          <div><span>渲染</span><strong>{renderModeLabel[renderMode]}</strong></div>
          <div><span>调度</span><strong>{scheduleModeLabel[scheduleMode]}</strong></div>
          <div><span>输出</span><strong>{outputTargetLabel[outputTarget]}</strong></div>
        </div>
      </div>

      <div className="ai-context-card">
        <Text type="secondary" style={{ fontSize: 12 }}>下一步建议</Text>
        <div className="ai-tip-list">
          {nextStepTips[mode].map((tip) => (
            <div className="ai-tip-item" key={tip}>
              <CheckCircleOutlined style={{ color: aura.accent, marginTop: 3 }} />
              <Text>{tip}</Text>
            </div>
          ))}
        </div>
      </div>

      <div className="ai-context-card">
        <Text type="secondary" style={{ fontSize: 12 }}>实时监控事件</Text>
        <Timeline
          style={{ marginTop: 12 }}
          items={socketEvents.map(([time, title, desc], index) => ({
            color: index < 3 ? aura.accent : 'green',
            children: (
              <div>
                <Space size={8}>
                  <Text strong style={{ fontSize: 13 }}>{title}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>{time}</Text>
                </Space>
                <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>{desc}</Text>
              </div>
            ),
          }))}
        />
      </div>
    </aside>
  );

  const renderRunActions = () => (
    <Space size={8}>
      {runStatus === 'running' ? (
        <Button icon={<PauseCircleOutlined />} onClick={handlePauseAnalysis}>暂停</Button>
      ) : null}
      {runStatus === 'paused' ? (
        <Button icon={<CaretRightOutlined />} onClick={handleResumeAnalysis}>继续</Button>
      ) : null}
      {runStatus !== 'idle' ? (
        <Button danger icon={<StopOutlined />} onClick={handleCancelAnalysis}>取消</Button>
      ) : null}
      <Button
        type={mode === 'publish' ? 'primary' : 'default'}
        icon={mode === 'explore' ? <RobotOutlined /> : mode === 'contract' ? <ExperimentOutlined /> : mode === 'dryrun' ? <SaveOutlined /> : <DeploymentUnitOutlined />}
        onClick={mode === 'explore' ? handleAnalyze : mode === 'contract' ? handleDryRun : mode === 'dryrun' ? handleSave : undefined}
      >
        {stageMeta[mode].action}
      </Button>
    </Space>
  );

  return (
    <ErrorBoundary>
      <style>
        {`
          .ai-collect-workbench {
            height: calc(100vh - 100px);
            max-height: calc(100vh - 100px);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: ${aura.bg};
            border-radius: 8px;
            padding: 14px;
            color: ${aura.text};
          }
          .ai-collect-workbench,
          .ai-collect-workbench * {
            scrollbar-width: none;
          }
          .ai-collect-workbench *::-webkit-scrollbar {
            display: none;
            width: 0;
            height: 0;
          }
          .ai-collect-header {
            flex-shrink: 0;
            position: sticky;
            top: 0;
            z-index: 5;
            border-radius: 8px;
            padding: 12px 0 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .ai-collect-body {
            flex: 1;
            min-height: 0;
            display: grid;
            grid-template-columns: minmax(280px, 320px) minmax(0, 1fr) minmax(280px, 320px);
            gap: 12px;
            overflow: hidden;
          }
          .ai-collect-panel {
            padding: 14px;
            overflow: auto;
            scrollbar-width: none;
            -ms-overflow-style: none;
            color: ${aura.text};
          }
          .ai-collect-workbench .ant-typography {
            color: ${aura.text};
          }
          .ai-collect-workbench .ant-typography-secondary,
          .ai-collect-workbench .ant-typography.ant-typography-secondary {
            color: ${aura.muted};
          }
          .ai-collect-workbench .ant-input,
          .ai-collect-workbench .ant-input-affix-wrapper,
          .ai-collect-workbench .ant-input-number,
          .ai-collect-workbench .ant-select-selector {
            background: #151818 !important;
            border-color: ${aura.border} !important;
            color: ${aura.text} !important;
            border-radius: 6px !important;
          }
          .ai-collect-workbench .ant-input::placeholder {
            color: ${aura.subtle};
          }
          .ai-collect-workbench .ant-segmented {
            background: transparent;
            border-bottom: 1px solid ${aura.border};
            border-radius: 0;
            padding: 0;
          }
          .ai-collect-workbench .ant-segmented-item {
            color: ${aura.muted};
            border-radius: 0;
          }
          .ai-collect-workbench .ant-segmented-item-selected {
            background: transparent;
            color: ${aura.accent};
            box-shadow: inset 0 -3px 0 ${aura.accent};
          }
          .ai-collect-workbench .ant-btn {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.text};
            box-shadow: none;
          }
          .ai-collect-workbench .ant-btn-link {
            border: none;
            color: ${aura.accent};
          }
          .ai-collect-workbench .ant-btn-primary,
          .ai-collect-workbench .ai-aura-primary {
            border-color: ${aura.accent} !important;
            background: transparent !important;
            color: ${aura.text} !important;
          }
          .ai-collect-workbench .ant-tag {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.muted};
          }
          .ai-mission-panel {
            overflow: hidden;
            display: flex;
            flex-direction: column;
          }
          .ai-collect-body.is-idle {
            display: flex;
            align-items: center;
            justify-content: center;
          }
          .ai-mission-hero {
            width: min(720px, 100%);
            min-height: 420px;
          }
          .ai-mission-hero .ai-mission-content {
            flex: initial;
            overflow: visible;
          }
          .ai-mission-hero .ai-form-stack {
            padding-bottom: 0;
          }
          .ai-mission-content {
            flex: 1;
            min-height: 0;
            overflow: auto;
            scrollbar-width: none;
            -ms-overflow-style: none;
            padding-right: 1px;
          }
          .ai-collect-panel::-webkit-scrollbar,
          .ai-mission-content::-webkit-scrollbar,
          .ai-code-block::-webkit-scrollbar,
          .ai-collect-workbench .ant-table-body::-webkit-scrollbar,
          .ai-collect-workbench .ant-table-content::-webkit-scrollbar {
            display: none;
            width: 0;
            height: 0;
          }
          .ai-panel-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
          }
          .ai-form-stack {
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding-top: 14px;
            padding-bottom: 14px;
          }
          .ai-two-cols {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
          }
          .ai-toggle-row,
          .ai-mini-summary,
          .ai-stage-toolbar,
          .ai-stage-focus,
          .ai-publish-row,
          .ai-tip-item,
          .ai-summary-list div {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
          }
          .ai-mini-summary {
            align-items: stretch;
          }
          .ai-mini-summary > div {
            flex: 1;
            padding: 10px;
            border-radius: 8px;
            background: ${aura.surfaceSoft};
            border: 1px solid ${aura.borderSoft};
          }
          .ai-stage-shell {
            padding: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
          }
          .ai-stage-top {
            flex-shrink: 0;
            padding: 14px;
            border-bottom: 1px solid ${aura.border};
          }
          .ai-stage-content {
            flex: 1;
            min-height: 0;
            padding: 14px;
            overflow: auto;
            scrollbar-width: none;
          }
          .ai-stage-content::-webkit-scrollbar {
            display: none;
          }
          .ai-stage-stack {
            display: flex;
            flex-direction: column;
            gap: 14px;
            min-height: 100%;
          }
          .ai-stage-focus {
            align-items: center;
            padding: 14px;
            border-radius: 8px;
            background: ${aura.surfaceSoft};
          }
          .ai-aura-flow {
            display: flex;
            flex-direction: column;
            gap: 18px;
          }
          .ai-aura-intro {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 20px;
          }
          .ai-aura-kicker {
            display: inline-block;
            color: ${aura.accent};
            font-weight: 700;
            margin-bottom: 10px;
          }
          .ai-aura-title {
            display: block;
            color: ${aura.text};
            font-size: 28px;
            line-height: 1.2;
            font-weight: 700;
          }
          .ai-aura-copy {
            color: ${aura.muted};
            line-height: 1.6;
          }
          .ai-aura-steps {
            position: relative;
            display: flex;
            flex-direction: column;
            gap: 34px;
            margin-top: 6px;
            padding-left: 62px;
          }
          .ai-aura-steps::before {
            content: '';
            position: absolute;
            left: 15px;
            top: 13px;
            bottom: 18px;
            width: 2px;
            background: ${aura.accent};
          }
          .ai-aura-step {
            position: relative;
            min-height: 112px;
          }
          .ai-aura-step-index {
            position: absolute;
            left: -62px;
            top: 0;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: ${aura.accent};
            color: ${aura.bg};
            font-weight: 800;
            font-size: 13px;
          }
          .ai-aura-step-title {
            color: ${aura.text};
            font-size: 18px;
          }
          .ai-aura-step-icon {
            color: ${aura.text};
            font-size: 17px;
          }
          .ai-aura-tag {
            border-color: ${aura.border} !important;
            color: ${aura.accent} !important;
            background: transparent !important;
          }
          .ai-aura-button {
            border-color: ${aura.accent} !important;
            color: ${aura.text} !important;
          }
          .ai-aura-link {
            color: ${aura.accent} !important;
            padding: 0;
          }
          .ai-aura-value {
            display: block;
            margin-top: 4px;
            color: ${aura.text};
          }
          .ai-panel-title {
            color: ${aura.text};
            font-size: 16px;
          }
          .ai-quality-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
          }
          .ai-quality-item {
            padding: 12px;
            border-radius: 8px;
            background: ${aura.surfaceSoft};
            border: 1px solid ${aura.borderSoft};
          }
          .ai-publish-list,
          .ai-summary-list,
          .ai-tip-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-top: 12px;
          }
          .ai-publish-row {
            justify-content: flex-start;
            padding: 10px 12px;
            border-radius: 8px;
            border: 1px solid ${aura.border};
          }
          .ai-publish-index {
            width: 24px;
            height: 24px;
            border-radius: 8px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #fff;
            background: ${aura.accent};
            color: ${aura.bg};
            font-size: 12px;
            flex-shrink: 0;
          }
          .ai-code-block {
            margin: 0;
            max-height: 260px;
            overflow: auto;
            padding: 12px;
            border-radius: 8px;
            background: ${aura.surfaceSoft};
            color: ${aura.muted};
            font-size: 12px;
          }
          .ai-context-card {
            padding: 12px;
            border-radius: 8px;
            border: 1px solid ${aura.border};
            margin-bottom: 12px;
          }
          .ai-summary-list span {
            color: ${aura.muted};
            font-size: 12px;
          }
          @media (max-width: 1280px) {
            .ai-collect-body {
              grid-template-columns: minmax(260px, 300px) minmax(0, 1fr);
            }
            .ai-collect-body.is-idle {
              display: flex;
            }
            .ai-collect-body > aside:last-child {
              display: none;
            }
          }
          @media (max-width: 767px) {
            .ai-collect-workbench {
              height: calc(100vh - 84px);
              max-height: calc(100vh - 84px);
            }
          }
        `}
      </style>

      <div className="ai-collect-workbench">
        <header
          className="ai-collect-header"
          style={{
            borderBottom: `1px solid ${aura.border}`,
            background: aura.bg,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <Space size={10} wrap>
              <Text strong style={{ fontSize: 28, color: aura.text }}>AI 智能采集</Text>
              <Tag className="ai-aura-tag" icon={<RobotOutlined />}>Copilot</Tag>
              <Tag className="ai-aura-tag">Socket 监控</Tag>
            </Space>
          </div>
        </header>

        <div className={`ai-collect-body ${hasSession ? '' : 'is-idle'}`}>
          {!hasSession ? (
            renderMissionPanel('hero')
          ) : (
            <>
              {renderMissionPanel('compact')}

              <main className="ai-collect-panel ai-stage-shell" style={panelStyle}>
                <div className="ai-stage-top">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                    <div style={{ minWidth: 0 }}>
                      <Space size={8} wrap>
                        <Text strong style={{ fontSize: 18 }}>{stageMeta[mode].title}</Text>
                        <Tag color="blue">{stageMeta[mode].score}% 置信</Tag>
                        <Tag color={runStatusMeta[runStatus].color}>{runStatusMeta[runStatus].label}</Tag>
                      </Space>
                      <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
                        {stageMeta[mode].desc}
                      </Text>
                    </div>
                    {renderRunActions()}
                  </div>
                  <div style={{ marginTop: 14 }}>
                    <Segmented
                      block
                      value={mode}
                      onChange={(value) => setMode(value as WorkMode)}
                      options={[
                        { label: '源站探索', value: 'explore', icon: <SearchOutlined /> },
                        { label: '字段合约', value: 'contract', icon: <FileSearchOutlined /> },
                        { label: '试跑确认', value: 'dryrun', icon: <ExperimentOutlined /> },
                        { label: '发布调度', value: 'publish', icon: <DeploymentUnitOutlined /> },
                      ]}
                    />
                  </div>
                  <Progress
                    percent={Math.round(((stageIndex + 1) / stageOrder.length) * 100)}
                    showInfo={false}
                    size="small"
                    style={{ marginTop: 12 }}
                  />
                  {streamError && <Alert type="warning" showIcon message={streamError} style={{ marginTop: 12 }} />}
                </div>
                <div className="ai-stage-content">
                  {renderStageContent()}
                </div>
              </main>

              {renderContextRail()}
            </>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
};

export default AICollect;

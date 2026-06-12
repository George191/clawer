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
  AudioOutlined,
  BranchesOutlined,
  CaretRightOutlined,
  CheckCircleOutlined,
  DeploymentUnitOutlined,
  EditOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  FileTextOutlined,
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
type ProcessStepKey = 'prepare' | 'entry' | 'structure' | 'contract' | 'dryrun' | 'publish';
type TerminalLogLevel = 'info' | 'ok' | 'warn';

const processStepOrder: ProcessStepKey[] = ['prepare', 'entry', 'structure', 'contract', 'dryrun', 'publish'];

const processStepMode: Record<ProcessStepKey, WorkMode> = {
  prepare: 'explore',
  entry: 'explore',
  structure: 'explore',
  contract: 'contract',
  dryrun: 'dryrun',
  publish: 'publish',
};

const processStepMeta: Record<ProcessStepKey, { title: string; desc: string; needConfirm: boolean }> = {
  prepare: {
    title: '准备投射源站',
    desc: '解析输入目标、变量占位和采集边界，生成源站投影画布。',
    needConfirm: false,
  },
  entry: {
    title: '识别入口与变量',
    desc: '定位搜索入口、请求参数和分页变量，准备可复用任务输入。',
    needConfirm: false,
  },
  structure: {
    title: '还原列表与详情',
    desc: '扫描列表容器、详情跳转、附件区域和动态渲染线索。',
    needConfirm: false,
  },
  contract: {
    title: '确认字段合约',
    desc: '生成字段名、类型、选择器、样本证据和必填规则。',
    needConfirm: true,
  },
  dryrun: {
    title: '试跑质量门禁',
    desc: '用小样本验证完整率、重复率、漂移风险和失败重试策略。',
    needConfirm: true,
  },
  publish: {
    title: '发布模板资产',
    desc: '固化模板版本、适配器策略和调度任务输入 Schema。',
    needConfirm: true,
  },
};

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
  const simulationTimerRef = useRef<number | null>(null);
  const referenceEditCanceledRef = useRef(false);
  const accountDisplayName = 'Blank George';
  const currentUserName = accountDisplayName.split(/\s+/)[0] || accountDisplayName;
  const [mode, setMode] = useState<WorkMode>('explore');
  const [missionTab, setMissionTab] = useState<MissionTab>('goal');
  const [runStatus, setRunStatus] = useState<RunStatus>('idle');
  const [url, setUrl] = useState('');
  const [intent, setIntent] = useState('');
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
  const [taskDraft, setTaskDraft] = useState('');
  const [submittedPrompt, setSubmittedPrompt] = useState('');
  const [referenceEditing, setReferenceEditing] = useState(false);
  const [referenceDraft, setReferenceDraft] = useState('');
  const [expandedStep, setExpandedStep] = useState<WorkMode>('explore');
  const [activeProcessStep, setActiveProcessStep] = useState<ProcessStepKey>('prepare');
  const [completedProcessSteps, setCompletedProcessSteps] = useState<Set<ProcessStepKey>>(new Set());
  const [visibleProcessSteps, setVisibleProcessSteps] = useState<ProcessStepKey[]>(['prepare']);
  const [selectedLogStep, setSelectedLogStep] = useState<ProcessStepKey>('prepare');
  const [scanPulse, setScanPulse] = useState(0);
  const [liveLogs, setLiveLogs] = useState<string[]>(['等待采集目标']);

  const hasSession = runStatus !== 'idle';
  const selectedCount = fields.filter((field) => selectedFields.has(field.name)).length;
  const qualityScore = mode === 'publish' ? 94 : mode === 'dryrun' ? 86 : mode === 'contract' ? 88 : 92;

  useEffect(() => () => {
    analyzeStreamRef.current?.close();
    if (simulationTimerRef.current) {
      window.clearTimeout(simulationTimerRef.current);
    }
  }, []);

  useEffect(() => {
    setExpandedStep(mode);
  }, [mode]);

  const pushLiveLog = useCallback((log: string) => {
    setLiveLogs((prev) => [log, ...prev].slice(0, 8));
  }, []);

  const resetSimulation = useCallback(() => {
    if (simulationTimerRef.current) {
      window.clearTimeout(simulationTimerRef.current);
      simulationTimerRef.current = null;
    }
    setActiveProcessStep('prepare');
    setSelectedLogStep('prepare');
    setCompletedProcessSteps(new Set());
    setVisibleProcessSteps(['prepare']);
    setScanPulse(0);
    setLiveLogs(['已接收采集目标，准备投射源站页面']);
  }, []);

  useEffect(() => {
    if (runStatus !== 'running') return undefined;
    if (processStepMeta[activeProcessStep].needConfirm) return undefined;

    const currentIndex = processStepOrder.indexOf(activeProcessStep);
    const timer = window.setTimeout(() => {
      const nextStep = processStepOrder[currentIndex + 1];
      setCompletedProcessSteps((prev) => new Set(prev).add(activeProcessStep));
      setScanPulse((prev) => prev + 1);
      pushLiveLog(`${processStepMeta[activeProcessStep].title} 已完成`);

      if (nextStep) {
        setVisibleProcessSteps((prev) => (prev.includes(nextStep) ? prev : [...prev, nextStep]));
        setActiveProcessStep(nextStep);
        setSelectedLogStep(nextStep);
        setMode(processStepMode[nextStep]);
        setExpandedStep(processStepMode[nextStep]);
        pushLiveLog(`进入 ${processStepMeta[nextStep].title}`);
      }
    }, activeProcessStep === 'prepare' ? 1200 : 1800);

    simulationTimerRef.current = timer;
    return () => window.clearTimeout(timer);
  }, [activeProcessStep, pushLiveLog, runStatus]);

  const validateUrl = useCallback((value: string) => {
    if (!value.trim()) return '请输入目标 URL';
    try {
      const normalized = value.replace(/\{\{\s*[^}]+\s*\}\}|\{\s*[^}]+\s*\}/g, 'sample');
      const parsed = new URL(normalized);
      if (!['http:', 'https:'].includes(parsed.protocol)) return '仅支持 HTTP/HTTPS 协议';
    } catch {
      return '请输入有效的 URL';
    }
    return '';
  }, []);

  const extractUrlFromPrompt = useCallback((value: string) => {
    const match = value.match(/https?:\/\/[^\s，。；,]+/i);
    return match?.[0].replace(/[)\]}>。；,，]+$/, '') ?? '';
  }, []);

  const handleAnalyze = useCallback(() => {
    const draftPrompt = taskDraft.trim();
    const currentReference = (submittedPrompt || intent || url).trim();
    const sourcePrompt = hasSession && draftPrompt
      ? `${currentReference} ${draftPrompt}`.trim()
      : (draftPrompt || currentReference).trim();
    const promptUrl = extractUrlFromPrompt(sourcePrompt);
    const targetUrl = promptUrl || url;
    const error = validateUrl(targetUrl);
    if (error) {
      message.error(targetUrl ? error : '请在问题中包含目标 URL');
      return;
    }

    const normalizedPrompt = sourcePrompt || targetUrl;
    setSubmittedPrompt(normalizedPrompt);
    setTaskDraft('');
    setReferenceEditing(false);
    setReferenceDraft('');
    setIntent(normalizedPrompt);
    if (promptUrl && promptUrl !== url) {
      setUrl(promptUrl);
    }
    analyzeStreamRef.current?.close();
    resetSimulation();
    setStreamError('');
    setRunStatus('running');
    setMode('explore');
    setExpandedStep('explore');
    const es = createAnalyzeStream(targetUrl);
    analyzeStreamRef.current = es;

    es.addEventListener('fields', (event: MessageEvent) => {
      const data: { fields: FieldDef[] } = JSON.parse(event.data);
      setFields(data.fields);
      setSelectedFields(new Set(data.fields.map((field) => field.name)));
      pushLiveLog('服务端字段候选已同步');
    });

    es.addEventListener('complete', (event: MessageEvent) => {
      const data: { templateId: string } = JSON.parse(event.data);
      setTemplateId(data.templateId);
      pushLiveLog('服务端合约草案已生成，等待前端确认');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.addEventListener('error', () => {
      setStreamError('分析服务暂不可用，当前展示前端预览合约。');
      pushLiveLog('分析服务暂不可用，切换为前端模拟流程');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.onerror = () => {
      setStreamError('SSE 连接已断开，当前展示前端预览合约。');
      pushLiveLog('SSE 连接断开，继续前端模拟流程');
      es.close();
      analyzeStreamRef.current = null;
    };
  }, [extractUrlFromPrompt, hasSession, intent, message, resetSimulation, submittedPrompt, taskDraft, url, validateUrl]);

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
    setSubmittedPrompt('');
    setTaskDraft('');
    setReferenceEditing(false);
    setReferenceDraft('');
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

  const handleConfirmProcessStep = useCallback((step: ProcessStepKey) => {
    setCompletedProcessSteps((prev) => new Set(prev).add(step));
    pushLiveLog(`${processStepMeta[step].title} 已确认`);

    if (step === 'contract') {
      setMode('dryrun');
      setActiveProcessStep('dryrun');
      setVisibleProcessSteps((prev) => (prev.includes('dryrun') ? prev : [...prev, 'dryrun']));
      void handleDryRun();
      return;
    }

    if (step === 'dryrun') {
      setMode('publish');
      setActiveProcessStep('publish');
      setVisibleProcessSteps((prev) => (prev.includes('publish') ? prev : [...prev, 'publish']));
      return;
    }

    if (step === 'publish') {
      void handleSave();
    }
  }, [handleDryRun, handleSave, pushLiveLog]);

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

  const handleGuideSubmit = useCallback(() => {
    const guide = taskDraft.trim();
    if (!guide) return;
    pushLiveLog(`用户引导：${guide}`);
    setTaskDraft('');
    setScanPulse((prev) => prev + 1);
  }, [pushLiveLog, taskDraft]);

  const handlePromptKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    if (hasSession) {
      handleGuideSubmit();
      return;
    }
    handleAnalyze();
  }, [handleAnalyze, handleGuideSubmit, hasSession]);

  const commitReferenceEdit = useCallback(() => {
    if (referenceEditCanceledRef.current) {
      referenceEditCanceledRef.current = false;
      return;
    }

    const nextReference = referenceDraft.trim();
    setReferenceEditing(false);
    if (!nextReference) return;

    setSubmittedPrompt(nextReference);
    setIntent(nextReference);
    const promptUrl = extractUrlFromPrompt(nextReference);
    if (promptUrl) {
      setUrl(promptUrl);
    }
  }, [extractUrlFromPrompt, referenceDraft]);

  const handleReferenceKeyDown = useCallback((event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitReferenceEdit();
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      referenceEditCanceledRef.current = true;
      setReferenceEditing(false);
    }
  }, [commitReferenceEdit]);

  const renderMissionPanel = (variant: 'hero' | 'compact') => {
    if (variant === 'hero') {
      return (
        <section className="ai-prompt-landing">
          <div className="ai-prompt-copy">
            <h1 className="ai-prompt-title">嗨，{currentUserName}，又有新灵感了吗？</h1>
          </div>

          <div className="ai-prompt-shell">
            <span className="ai-prompt-leading-icon" aria-hidden="true"><GlobalOutlined /></span>
            <TextArea
              className="ai-prompt-input"
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
              onKeyDown={handlePromptKeyDown}
              autoSize={{ minRows: 1, maxRows: 3 }}
              placeholder="贴个网址，问问 Helio"
            />
            <Button className="ai-prompt-icon" shape="circle" icon={<AudioOutlined />} aria-label="语音输入" disabled />
          </div>
        </section>
      );
    }

    return (
      <aside className="ai-collect-panel ai-mission-panel" style={panelStyle}>
      <div className="ai-panel-head">
        <Space size={8}>
          <ThunderboltOutlined style={{ color: aura.accent }} />
          <Text strong className="ai-panel-title">采集意图</Text>
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
            <Button className="ai-aura-primary" type="primary" icon={<RobotOutlined />} block onClick={handleAnalyze}>
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
  };

  const renderExplore = () => (
    <div className="ai-stage-stack ai-logic-workbench">
      <section className={`ai-projection-stage scan-${activeProcessStep}`} key={scanPulse}>
        <div className="ai-projection-head">
          <div>
            <Text className="ai-aura-kicker">Source Projection</Text>
            <Text strong className="ai-aura-title">源站页面投射中</Text>
            <Text className="ai-aura-copy">AI 正在把目标页面转换为可采集结构，识别入口、列表、详情和字段证据。</Text>
          </div>
          <Tag className="ai-aura-tag">{processStepMeta[activeProcessStep].title}</Tag>
        </div>

        <div className="ai-page-projection">
          <div className="ai-scan-line" />
          <div className="ai-page-toolbar">
            <span />
            <span />
            <span />
            <strong>{url || 'https://source.example/search?q={{keyword}}'}</strong>
          </div>
          <div className="ai-page-search">
            <SearchOutlined />
            <span>{'query={{keyword}}'}</span>
            <em className="ai-detect-tag is-entry">入口参数</em>
          </div>
          <div className="ai-page-layout">
            <div className="ai-page-filter">
              <i />
              <i />
              <i />
              <em className="ai-detect-tag is-policy">筛选区</em>
            </div>
            <div className="ai-page-list">
              {[0, 1, 2].map((item) => (
                <div className="ai-page-row" key={item}>
                  <b />
                  <span />
                  <small />
                  <em className={`ai-detect-tag ${item === 1 ? 'is-detail' : ''}`}>{item === 1 ? '详情入口' : '列表项'}</em>
                </div>
              ))}
              <div className="ai-page-pagination">
                <span />
                <span />
                <span />
                <em className="ai-detect-tag is-page">分页规则</em>
              </div>
            </div>
            <div className="ai-page-detail">
              <i />
              <i />
              <i />
              <em className="ai-detect-tag is-field">字段候选</em>
            </div>
          </div>
        </div>
      </section>

      <div className="ai-logic-metrics">
        {[
          ['入口候选', '3', '搜索页 / 列表页 / API'],
          ['字段线索', `${fields.length}`, '标题、时间、摘要、来源'],
          ['详情链路', '2 层', 'list -> detail -> attachment'],
          ['风险等级', '低', '速率与漂移可控'],
        ].map(([label, value, hint]) => (
          <div className="ai-logic-metric" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{hint}</small>
          </div>
        ))}
      </div>

      <section className="ai-logic-route">
        <div>
          <Text strong>推荐采集路线</Text>
          <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
            AI 会先确认入口和详情链路，再生成字段合约，最后交给试跑门禁。
          </Text>
        </div>
        <div className="ai-logic-route-flow">
          {['入口识别', '列表解析', '详情补齐', '字段合约'].map((step, index) => (
            <React.Fragment key={step}>
              <span>{step}</span>
              {index < 3 ? <i /> : null}
            </React.Fragment>
          ))}
        </div>
      </section>
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

  const getStepStatus = (step: ProcessStepKey) => {
    if (completedProcessSteps.has(step)) return 'done';
    if (step === activeProcessStep) return 'active';
    return 'pending';
  };

  const stepLogs: Record<ProcessStepKey, Array<{ time: string; level: TerminalLogLevel; message: string }>> = {
    prepare: [
      { time: '00:00.120', level: 'info', message: 'normalize prompt: extract target url, variables and crawl boundary' },
      { time: '00:00.418', level: 'ok', message: 'url pattern accepted: https/http with template placeholders enabled' },
      { time: '00:00.906', level: 'info', message: 'projection canvas initialized; waiting for source page fingerprint' },
    ],
    entry: [
      { time: '00:01.240', level: 'info', message: 'scan entry candidates: search form, list route, pagination parameter' },
      { time: '00:01.876', level: 'ok', message: 'query variable detected; page cursor mapped to task input schema' },
      { time: '00:02.104', level: 'info', message: 'entry confidence updated: route stability 0.92, duplicate risk low' },
    ],
    structure: [
      { time: '00:02.620', level: 'info', message: 'inspect list container: item density, href pattern, detail transition' },
      { time: '00:03.118', level: 'ok', message: 'detail page relation restored; attachment and canonical fields marked' },
      { time: '00:03.560', level: 'warn', message: 'dynamic region found; browser-agent fallback retained for drift guard' },
    ],
    contract: [
      { time: '00:04.020', level: 'info', message: 'generate field contract: name, type, selector and sample evidence' },
      { time: '00:04.488', level: 'ok', message: 'required fields locked: title, source_url, publish_time' },
      { time: '00:04.920', level: 'warn', message: 'low confidence field queued for user confirmation before dry run' },
    ],
    dryrun: [
      { time: '00:05.220', level: 'info', message: 'run small sample: 20 pages, concurrency 4, respect robots policy' },
      { time: '00:05.946', level: 'ok', message: 'quality gate passed: completeness 0.96, duplicate ratio 0.03' },
      { time: '00:06.178', level: 'info', message: 'retry and timeout strategy persisted to adapter draft' },
    ],
    publish: [
      { time: '00:06.540', level: 'info', message: 'freeze template version and adapter policy snapshot' },
      { time: '00:06.904', level: 'ok', message: 'task input schema generated; schedule payload ready' },
      { time: '00:07.200', level: 'info', message: 'asset publication prepared for template library and task center' },
    ],
  };

  const renderDockedPrompt = () => {
    const referenceText = submittedPrompt || url || '目标源站待识别';

    return (
      <section className="ai-session-prompt">
        <div className="ai-session-reference">
          <span className="ai-session-reference-icon"><LinkOutlined /></span>
          {referenceEditing ? (
            <Input
              className="ai-reference-input"
              value={referenceDraft}
              autoFocus
              onChange={(event) => setReferenceDraft(event.target.value)}
              onBlur={commitReferenceEdit}
              onKeyDown={handleReferenceKeyDown}
            />
          ) : (
            <>
              <em title={referenceText}>{referenceText}</em>
              <Button
                className="ai-reference-edit"
                type="text"
                icon={<EditOutlined />}
                aria-label="编辑引用"
                onClick={() => {
                  setReferenceDraft(referenceText);
                  setReferenceEditing(true);
                }}
              />
            </>
          )}
        </div>
        <div className="ai-session-prompt-main">
          <span className="ai-session-leading-icon" aria-hidden="true"><GlobalOutlined /></span>
          <TextArea
            value={taskDraft}
            onChange={(event) => setTaskDraft(event.target.value)}
            onKeyDown={handlePromptKeyDown}
            autoSize={{ minRows: 1, maxRows: 2 }}
            placeholder="随时引导 Helio 的采集方向"
          />
          <Button
            className="ai-session-icon-btn ai-session-sparkle-btn"
            aria-label="智能优化输入内容"
          >
            <span className="ai-session-sparkle" aria-hidden="true">✦</span>
          </Button>
          <Button className="ai-session-icon-btn" icon={<AudioOutlined />} aria-label="语音输入" disabled />
        </div>
      </section>
    );
  };

  const renderStepNavigator = () => (
    <aside className="ai-step-rail">
      {visibleProcessSteps.map((step, index) => {
        const status = getStepStatus(step);
        const expanded = activeProcessStep === step;
        const meta = processStepMeta[step];
        const statusText = status === 'done' ? '完成' : status === 'active' ? '分析中' : '等待';
        return (
          <section className={`ai-step-item is-${status} ${expanded ? 'is-expanded' : ''}`} key={step}>
            <button
              type="button"
              className="ai-step-summary"
              onClick={() => {
                setActiveProcessStep(step);
                setMode(processStepMode[step]);
                setExpandedStep(processStepMode[step]);
              }}
            >
              <span className="ai-step-index">{status === 'done' ? <CheckCircleOutlined /> : index + 1}</span>
              <span className="ai-step-title">
                <strong>{meta.title}</strong>
              </span>
              <small>{statusText}</small>
            </button>
            {expanded ? (
              <div className="ai-step-detail">
                <span className="ai-step-pill">{statusText}</span>
                <div className="ai-step-actions">
                  <Button
                    size="small"
                    className="ai-step-log-icon"
                    icon={<FileTextOutlined />}
                    aria-label="查看阶段日志"
                    onClick={() => {
                      setSelectedLogStep(step);
                      pushLiveLog(`focus trace group: ${meta.title}`);
                    }}
                  />
                  {meta.needConfirm ? (
                    <Button size="small" className="ai-step-confirm" onClick={() => handleConfirmProcessStep(step)}>
                      确认
                    </Button>
                  ) : (
                    <span className="ai-step-auto">自动</span>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        );
      })}
    </aside>
  );

  const renderGuidancePanel = () => (
    <aside className="ai-guidance-panel">
      <div className="ai-terminal-window">
        <div className="ai-terminal-bar">
          <span className="is-red" />
          <span className="is-yellow" />
          <span className="is-green" />
        </div>
        <div className="ai-terminal-body">
          {processStepOrder.map((step) => {
            const isActive = step === activeProcessStep;
            const isSelected = step === selectedLogStep;
            const isOpen = isActive || isSelected;
            const status = getStepStatus(step);
            const runtimeLogs = isActive ? liveLogs.slice(0, 3) : [];

            return (
              <section
                className={`ai-terminal-group is-${status} ${isActive ? 'is-active' : ''} ${isSelected ? 'is-selected' : ''} ${isOpen ? 'is-open' : ''}`}
                key={step}
              >
                <button
                  type="button"
                  className="ai-terminal-group-head"
                  onClick={() => setSelectedLogStep(step)}
                >
                  <span className="ai-terminal-caret">$</span>
                  <span>{processStepMeta[step].title}</span>
                  <em>{status === 'done' ? 'done' : status === 'active' ? 'running' : 'queued'}</em>
                </button>
                <div className="ai-terminal-lines" key={`${step}-${isOpen ? scanPulse : 'closed'}`}>
                  {runtimeLogs.map((log, logIndex) => (
                    <p className="ai-terminal-line is-live" key={`live-${log}`}>
                      <span>{`00:0${logIndex}.now`}</span>
                      <b>live</b>
                      <code>{log}</code>
                    </p>
                  ))}
                  {stepLogs[step].map((log) => (
                    <p className={`ai-terminal-line is-${log.level}`} key={`${step}-${log.time}-${log.message}`}>
                      <span>{log.time}</span>
                      <b>{log.level}</b>
                      <code>{log.message}</code>
                    </p>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </aside>
  );

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
          body:has(.ai-collect-workbench) .ant-layout-content {
            padding: 0 !important;
            height: calc(100vh - 48px) !important;
            min-height: calc(100vh - 48px) !important;
            overflow: hidden !important;
            background:
              radial-gradient(ellipse at 50% 38%, rgba(44, 72, 151, 0.36) 0%, rgba(28, 47, 103, 0.18) 34%, rgba(23, 26, 26, 0) 64%),
              linear-gradient(180deg, #101212 0%, ${aura.bg} 58%, #141818 100%) !important;
          }
          body:has(.ai-collect-workbench),
          body:has(.ai-collect-workbench) #root {
            height: 100vh;
            overflow: hidden !important;
          }
          body:has(.ai-collect-workbench) .ant-layout-content > div,
          body:has(.ai-collect-workbench) main {
            height: calc(100vh - 48px) !important;
            min-height: calc(100vh - 48px) !important;
            overflow: hidden !important;
            background:
              radial-gradient(ellipse at 50% 38%, rgba(44, 72, 151, 0.36) 0%, rgba(28, 47, 103, 0.18) 34%, rgba(23, 26, 26, 0) 64%),
              linear-gradient(180deg, #101212 0%, ${aura.bg} 58%, #141818 100%) !important;
          }
          .ai-collect-workbench {
            height: calc(100vh - 48px);
            max-height: calc(100vh - 48px);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            gap: 12px;
            position: relative;
            background: transparent;
            border-radius: 0;
            padding: 14px;
            color: ${aura.text};
            font-family: "Google Sans", "Product Sans", Roboto, Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
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
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .ai-collect-header.is-idle {
            height: 0;
            min-height: 0;
            padding: 0;
            opacity: 0;
            overflow: hidden;
            pointer-events: none;
            border-bottom-color: transparent !important;
            background: transparent !important;
          }
          .ai-collect-header.is-session {
            height: 0;
            min-height: 0;
            padding: 0;
            opacity: 0;
            overflow: hidden;
            pointer-events: none;
            border-bottom-color: transparent !important;
            background: transparent !important;
          }
          .ai-collect-body {
            flex: 1;
            min-height: 0;
            display: grid;
            grid-template-columns: minmax(204px, 232px) minmax(0, 1.12fr) minmax(250px, 292px);
            gap: 0 12px;
            overflow: hidden;
          }
          .ai-collect-body.is-session {
            padding-bottom: 112px;
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
            padding: 0 24px 11vh;
            overflow: hidden;
          }
          .ai-collect-body.is-session > .ai-mission-panel {
            animation: aiWorkbenchIn 360ms ease both;
          }
          .ai-collect-body.is-session > .ai-step-rail {
            animation: aiWorkbenchRise 520ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-collect-body.is-session > .ai-stage-shell {
            animation: aiWorkbenchRise 560ms 70ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-collect-body.is-session > .ai-collect-scroll,
          .ai-collect-body.is-session > .ai-guidance-panel {
            animation: aiWorkbenchRise 560ms 140ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-session-prompt {
            position: absolute;
            left: 50%;
            bottom: 18px;
            z-index: 16;
            width: min(760px, calc(100% - 52px));
            margin: 0;
            display: flex;
            flex-direction: column;
            padding: 2px 0 6px;
            animation: aiComposerDock 380ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-session-reference {
            height: 28px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            align-self: flex-start;
            padding: 0 8px 0 10px;
            border-radius: 14px 14px 0 0;
            background: rgba(31, 35, 35, 0.86);
            border: 1px solid rgba(143, 227, 232, 0.14);
            border-bottom: none;
            color: rgba(245, 247, 247, 0.86);
            font-size: 12px;
            font-weight: 400;
            max-width: min(100%, 560px);
            position: relative;
          }
          .ai-session-reference-icon {
            width: 17px;
            height: 17px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 5px;
            background: rgba(255, 255, 255, 0.08);
            color: ${aura.text};
            font-size: 11px;
          }
          .ai-session-reference em {
            max-width: 360px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: ${aura.subtle};
            font-size: 12px;
            font-style: normal;
            transition: max-width 160ms ease;
          }
          .ai-reference-edit {
            width: 0 !important;
            height: 22px !important;
            min-width: 0 !important;
            border: none !important;
            border-radius: 50% !important;
            background: transparent !important;
            color: ${aura.muted} !important;
            box-shadow: none !important;
            opacity: 0;
            padding: 0 !important;
            overflow: hidden;
            pointer-events: none;
            transition: width 140ms ease, min-width 140ms ease, opacity 140ms ease, background 140ms ease, color 140ms ease;
          }
          .ai-session-reference:hover em {
            max-width: 336px;
          }
          .ai-session-reference:hover .ai-reference-edit,
          .ai-reference-edit:focus-visible {
            width: 22px !important;
            min-width: 22px !important;
            opacity: 1;
            pointer-events: auto;
          }
          .ai-reference-edit:hover {
            color: ${aura.accent} !important;
            background: rgba(143, 227, 232, 0.1) !important;
          }
          .ai-collect-workbench .ai-reference-input.ant-input {
            width: min(420px, 52vw);
            height: 24px !important;
            min-height: 24px !important;
            padding: 0 !important;
            background: transparent !important;
            border-color: transparent !important;
            box-shadow: none !important;
            color: ${aura.text} !important;
            font-size: 12px;
            line-height: 24px;
          }
          .ai-session-prompt-main {
            display: grid;
            grid-template-columns: 22px minmax(0, 1fr) 30px 30px;
            align-items: center;
            gap: 8px;
            min-height: 56px;
            padding: 0 12px 0 15px;
            border-radius: 0 14px 14px 14px;
            background: #202124;
            border: 1px solid rgba(143, 227, 232, 0.14);
            box-shadow: 0 18px 54px rgba(0, 0, 0, 0.38);
            position: relative;
          }
          .ai-session-leading-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: ${aura.muted};
            font-size: 15px;
          }
          .ai-session-prompt-main .ant-input {
            background: transparent !important;
            border-color: transparent !important;
            box-shadow: none !important;
            padding: 0 !important;
            color: ${aura.text} !important;
            font-size: 13px;
            line-height: 1.5;
            resize: none;
          }
          .ai-session-icon-btn {
            width: 30px !important;
            height: 30px !important;
            border: none !important;
            border-radius: 50% !important;
            background: transparent !important;
            color: ${aura.muted} !important;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            box-shadow: none !important;
          }
          .ai-session-icon-btn:hover {
            color: ${aura.accent} !important;
            background: rgba(143, 227, 232, 0.12) !important;
          }
          .ai-session-icon-btn.ant-btn[disabled],
          .ai-session-icon-btn.ant-btn[disabled]:hover {
            color: rgba(245, 247, 247, 0.34) !important;
            background: transparent !important;
            cursor: not-allowed;
          }
          .ai-session-sparkle {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            color: ${aura.text};
            font-size: 16px;
            line-height: 1;
            position: relative;
          }
          .ai-session-sparkle::after {
            content: '✦';
            position: absolute;
            right: -3px;
            top: -5px;
            color: ${aura.text};
            font-size: 8px;
            line-height: 1;
            transform: scale(0.82);
          }
          .ai-step-rail,
          .ai-guidance-panel {
            min-height: 0;
            overflow: auto;
            padding: 12px;
            background: ${aura.surface};
            border: 1px solid ${aura.border};
          }
          .ai-step-rail {
            border-radius: 8px 0 0 8px;
            border-right: none;
          }
          .ai-stage-shell {
            border-radius: 0 8px 8px 0 !important;
          }
          .ai-guidance-panel {
            border-radius: 8px;
          }
          .ai-step-rail {
            display: flex;
            flex-direction: column;
            gap: 6px;
            background: rgba(18, 20, 21, 0.82);
          }
          .ai-step-item {
            position: relative;
            border-radius: 10px;
            border: 1px solid transparent;
            background: transparent;
            overflow: visible;
            transition: border-color 160ms ease, background 160ms ease, opacity 160ms ease;
          }
          .ai-step-item::before {
            content: '';
            position: absolute;
            left: 20px;
            top: 38px;
            bottom: -8px;
            width: 1px;
            background: rgba(255, 255, 255, 0.08);
          }
          .ai-step-item:last-child::before {
            display: none;
          }
          .ai-step-item.is-active {
            border-color: rgba(143, 227, 232, 0.16);
            background: rgba(143, 227, 232, 0.06);
          }
          .ai-step-item.is-done {
            opacity: 0.82;
          }
          .ai-step-item:hover {
            background: rgba(255, 255, 255, 0.035);
            opacity: 1;
          }
          .ai-step-summary {
            position: relative;
            z-index: 1;
            width: 100%;
            border: none;
            background: transparent;
            display: grid;
            grid-template-columns: 24px minmax(0, 1fr) auto;
            gap: 9px;
            align-items: center;
            padding: 8px 9px;
            color: ${aura.text};
            text-align: left;
            cursor: pointer;
          }
          .ai-step-index {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: ${aura.subtle};
            background: #202124;
            border: 1px solid rgba(255, 255, 255, 0.1);
            font-size: 11px;
            font-weight: 600;
          }
          .ai-step-item.is-active .ai-step-index {
            color: ${aura.accent};
            background: rgba(143, 227, 232, 0.12);
            border-color: rgba(143, 227, 232, 0.32);
          }
          .ai-step-item.is-done .ai-step-index {
            color: #9FE7D7;
            background: rgba(159, 231, 215, 0.1);
            border-color: rgba(159, 231, 215, 0.2);
          }
          .ai-step-title {
            min-width: 0;
          }
          .ai-step-summary strong {
            display: block;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 12px;
            font-weight: 500;
            line-height: 1.25;
          }
          .ai-step-summary small {
            min-height: 20px;
            display: inline-flex;
            align-items: center;
            padding: 0 7px;
            border-radius: 10px;
            color: rgba(245, 247, 247, 0.48);
            background: rgba(255, 255, 255, 0.045);
            font-size: 10px;
            line-height: 1;
            white-space: nowrap;
          }
          .ai-step-item.is-active .ai-step-summary small {
            color: ${aura.accent};
            background: rgba(143, 227, 232, 0.1);
          }
          .ai-step-item.is-done .ai-step-summary small {
            color: #9FE7D7;
            background: rgba(159, 231, 215, 0.08);
          }
          .ai-step-detail {
            position: relative;
            z-index: 1;
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            gap: 8px;
            padding: 0 9px 8px 42px;
          }
          .ai-step-pill,
          .ai-step-auto {
            min-height: 24px;
            display: inline-flex;
            align-items: center;
            color: ${aura.subtle};
            font-size: 11px;
          }
          .ai-step-actions {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 6px;
          }
          .ai-step-log-icon {
            width: 24px !important;
            height: 24px !important;
            border-radius: 50% !important;
            border: none !important;
            color: ${aura.muted} !important;
            background: transparent !important;
          }
          .ai-step-log-icon:hover {
            color: ${aura.accent} !important;
            background: rgba(143, 227, 232, 0.1) !important;
          }
          .ai-step-confirm {
            height: 24px !important;
            padding: 0 10px !important;
            border-radius: 12px !important;
            border: 1px solid rgba(143, 227, 232, 0.24) !important;
            color: ${aura.text} !important;
            background: rgba(143, 227, 232, 0.1) !important;
            font-size: 12px !important;
          }
          .ai-step-confirm:hover {
            color: ${aura.accent} !important;
            border-color: rgba(143, 227, 232, 0.42) !important;
          }
          .ai-guidance-panel {
            display: flex;
            flex-direction: column;
            padding: 0;
            border-radius: 8px;
            overflow: hidden;
            background: #111314;
            border-color: rgba(255, 255, 255, 0.08);
          }
          .ai-terminal-window {
            height: 100%;
            min-height: 0;
            display: flex;
            flex-direction: column;
            background:
              linear-gradient(180deg, rgba(31, 35, 35, 0.92), rgba(14, 16, 17, 0.96)),
              #111314;
          }
          .ai-terminal-bar {
            height: 36px;
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 0 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.035);
          }
          .ai-terminal-bar span {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
          }
          .ai-terminal-bar .is-red {
            background: #FF5F57;
          }
          .ai-terminal-bar .is-yellow {
            background: #FEBC2E;
          }
          .ai-terminal-bar .is-green {
            background: #28C840;
          }
          .ai-terminal-body {
            flex: 1;
            min-height: 0;
            overflow: auto;
            padding: 10px;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          }
          .ai-terminal-group {
            border-radius: 8px;
            border: 1px solid transparent;
            padding: 7px 8px 8px;
            margin-bottom: 8px;
            opacity: 0.52;
            transition: opacity 180ms ease, border-color 180ms ease, background 180ms ease;
          }
          .ai-terminal-group.is-done {
            opacity: 0.72;
          }
          .ai-terminal-group.is-active,
          .ai-terminal-group.is-selected {
            opacity: 1;
            border-color: rgba(143, 227, 232, 0.2);
            background: rgba(143, 227, 232, 0.055);
          }
          .ai-terminal-group.is-active {
            animation: aiTerminalFocus 900ms ease both;
          }
          .ai-terminal-group-head {
            width: 100%;
            border: none;
            background: transparent;
            color: rgba(245, 247, 247, 0.88);
            display: grid;
            grid-template-columns: 14px minmax(0, 1fr) auto;
            align-items: center;
            gap: 7px;
            padding: 0;
            font-family: inherit;
            font-size: 12px;
            text-align: left;
            cursor: pointer;
          }
          .ai-terminal-caret {
            color: #74D6DB;
          }
          .ai-terminal-group-head span:nth-child(2) {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-terminal-group-head em {
            color: rgba(245, 247, 247, 0.44);
            font-size: 10px;
            font-style: normal;
          }
          .ai-terminal-lines {
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-top: 7px;
            max-height: 0;
            opacity: 0;
            overflow: hidden;
            transition: max-height 260ms ease, opacity 180ms ease;
          }
          .ai-terminal-group.is-open .ai-terminal-lines {
            max-height: 360px;
            opacity: 1;
          }
          .ai-terminal-line {
            display: grid;
            grid-template-columns: 58px 34px minmax(0, 1fr);
            gap: 7px;
            align-items: baseline;
            margin: 0;
            color: rgba(245, 247, 247, 0.62);
            font-size: 11px;
            line-height: 1.45;
          }
          .ai-terminal-line span {
            color: rgba(245, 247, 247, 0.36);
          }
          .ai-terminal-line b {
            color: #7DD3FC;
            font-weight: 500;
          }
          .ai-terminal-line code {
            min-width: 0;
            white-space: normal;
            word-break: break-word;
            font-family: inherit;
          }
          .ai-terminal-line.is-ok b {
            color: #86EFAC;
          }
          .ai-terminal-line.is-warn b {
            color: #FDE68A;
          }
          .ai-terminal-line.is-live {
            color: rgba(245, 247, 247, 0.86);
            animation: aiTerminalLineIn 260ms ease both;
          }
          .ai-terminal-line.is-live b {
            color: #8FE3E8;
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
          .ai-prompt-landing {
            width: min(824px, 100%);
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 54px;
            animation: aiPromptIn 460ms ease both;
          }
          .ai-prompt-copy {
            text-align: center;
            padding-left: 0;
          }
          .ai-prompt-title {
            margin: 0;
            color: rgba(245, 247, 247, 0.84);
            font-size: clamp(26px, 2.45vw, 34px);
            line-height: 1.2;
            font-weight: 300;
            letter-spacing: 0;
          }
          .ai-prompt-shell {
            position: relative;
            display: grid;
            grid-template-columns: 24px minmax(0, 1fr) 36px;
            align-items: center;
            gap: 14px;
            min-height: 80px;
            padding: 0 30px 0 24px;
            border-radius: 40px;
            background: #202124;
            border: 1px solid rgba(255, 255, 255, 0.02);
            box-shadow: 0 18px 64px rgba(0, 0, 0, 0.28);
            overflow: visible;
          }
          .ai-prompt-leading-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(245, 247, 247, 0.72);
            font-size: 18px;
          }
          .ai-prompt-icon {
            width: 34px !important;
            height: 34px !important;
            min-width: 34px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border: none !important;
            background: transparent !important;
            color: rgba(245, 247, 247, 0.84) !important;
            font-size: 20px;
          }
          .ai-prompt-icon.ant-btn[disabled],
          .ai-prompt-icon.ant-btn[disabled]:hover {
            color: rgba(245, 247, 247, 0.34) !important;
            cursor: not-allowed;
          }
          .ai-collect-workbench .ai-prompt-input.ant-input {
            min-height: 34px !important;
            max-height: 70px;
            padding: 0 !important;
            background: transparent !important;
            border-color: transparent !important;
            box-shadow: none !important;
            resize: none;
            font-size: 15px;
            line-height: 34px;
            color: rgba(245, 247, 247, 0.92) !important;
            font-family: inherit;
            font-weight: 400;
          }
          .ai-collect-workbench .ai-prompt-input.ant-input::placeholder {
            color: rgba(245, 247, 247, 0.72);
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
            gap: 12px;
            padding-top: 12px;
            padding-bottom: 12px;
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
            padding: 9px;
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
          .ai-logic-workbench {
            gap: 12px;
          }
          .ai-projection-stage {
            min-height: 390px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding: 16px;
            border-radius: 8px;
            background:
              linear-gradient(180deg, rgba(31, 35, 35, 0.84), rgba(21, 24, 24, 0.78)),
              repeating-linear-gradient(90deg, rgba(143, 227, 232, 0.04) 0 1px, transparent 1px 24px);
            border: 1px solid rgba(143, 227, 232, 0.16);
            overflow: hidden;
          }
          .ai-projection-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
          }
          .ai-page-projection {
            position: relative;
            flex: 1;
            min-height: 280px;
            padding: 16px;
            border-radius: 10px;
            background: rgba(12, 15, 18, 0.72);
            border: 1px solid rgba(143, 227, 232, 0.16);
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02), 0 24px 80px rgba(0, 0, 0, 0.24);
            overflow: hidden;
          }
          .ai-page-projection::before {
            content: '';
            position: absolute;
            inset: 0;
            background-image:
              linear-gradient(rgba(143, 227, 232, 0.05) 1px, transparent 1px),
              linear-gradient(90deg, rgba(143, 227, 232, 0.05) 1px, transparent 1px);
            background-size: 28px 28px;
            opacity: 0.35;
            pointer-events: none;
          }
          .ai-scan-line {
            position: absolute;
            left: 0;
            right: 0;
            top: -30%;
            height: 38%;
            background: linear-gradient(180deg, transparent, rgba(143, 227, 232, 0.18), transparent);
            filter: blur(1px);
            animation: aiScanSweep 2.6s ease-in-out infinite;
            pointer-events: none;
            z-index: 2;
          }
          .ai-page-toolbar,
          .ai-page-search,
          .ai-page-layout {
            position: relative;
            z-index: 1;
          }
          .ai-page-toolbar {
            height: 32px;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 0 10px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.05);
          }
          .ai-page-toolbar span {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: rgba(245, 247, 247, 0.34);
          }
          .ai-page-toolbar strong {
            margin-left: 6px;
            color: ${aura.subtle};
            font-size: 12px;
            font-weight: 500;
          }
          .ai-page-search {
            height: 42px;
            margin-top: 12px;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 0 14px;
            border-radius: 21px;
            background: rgba(143, 227, 232, 0.08);
            border: 1px solid rgba(143, 227, 232, 0.18);
          }
          .ai-page-search span {
            color: ${aura.muted};
            font-size: 13px;
          }
          .ai-page-layout {
            display: grid;
            grid-template-columns: 150px minmax(0, 1fr) 190px;
            gap: 12px;
            margin-top: 12px;
          }
          .ai-page-filter,
          .ai-page-list,
          .ai-page-detail {
            min-height: 174px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.045);
            border: 1px solid rgba(255, 255, 255, 0.06);
            padding: 12px;
            position: relative;
          }
          .ai-page-filter i,
          .ai-page-detail i {
            display: block;
            height: 12px;
            border-radius: 6px;
            background: rgba(245, 247, 247, 0.12);
            margin-bottom: 12px;
          }
          .ai-page-row {
            position: relative;
            height: 44px;
            display: grid;
            grid-template-columns: 42px minmax(0, 1fr) 56px;
            gap: 10px;
            align-items: center;
            border-radius: 8px;
            padding: 0 10px;
            background: rgba(255, 255, 255, 0.045);
            margin-bottom: 9px;
          }
          .ai-page-row b,
          .ai-page-row span,
          .ai-page-row small,
          .ai-page-pagination span {
            height: 12px;
            border-radius: 6px;
            background: rgba(245, 247, 247, 0.12);
          }
          .ai-page-row b {
            height: 26px;
          }
          .ai-page-pagination {
            position: relative;
            display: flex;
            gap: 8px;
            justify-content: flex-end;
            margin-top: 12px;
          }
          .ai-page-pagination span {
            width: 28px;
          }
          .ai-detect-tag {
            position: absolute;
            right: 10px;
            top: -9px;
            min-height: 20px;
            display: inline-flex;
            align-items: center;
            padding: 0 7px;
            border-radius: 10px;
            background: rgba(143, 227, 232, 0.12);
            border: 1px solid rgba(143, 227, 232, 0.32);
            color: ${aura.accent};
            font-size: 11px;
            font-style: normal;
            opacity: 0.32;
            transform: translateY(4px);
            transition: opacity 240ms ease, transform 240ms ease;
          }
          .scan-entry .is-entry,
          .scan-structure .is-entry,
          .scan-structure .is-detail,
          .scan-structure .is-page,
          .scan-contract .is-field,
          .scan-contract .is-detail,
          .scan-dryrun .ai-detect-tag,
          .scan-publish .ai-detect-tag {
            opacity: 1;
            transform: translateY(0);
            animation: aiDetectPulse 1.5s ease-in-out infinite;
          }
          .ai-logic-hero {
            min-height: 132px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding: 18px;
            border-radius: 8px;
            background: rgba(31, 35, 35, 0.72);
            border: 1px solid rgba(143, 227, 232, 0.12);
          }
          .ai-logic-score {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
          }
          .ai-logic-score .ant-typography {
            font-size: 12px;
          }
          .ai-logic-metrics {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
          }
          .ai-logic-metric {
            min-height: 88px;
            padding: 12px;
            border-radius: 8px;
            background: rgba(31, 35, 35, 0.62);
            border: 1px solid ${aura.borderSoft};
          }
          .ai-logic-metric span,
          .ai-logic-metric small {
            display: block;
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.45;
          }
          .ai-logic-metric strong {
            display: block;
            margin: 8px 0 4px;
            color: ${aura.text};
            font-size: 22px;
            line-height: 1;
            font-weight: 600;
          }
          .ai-logic-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
          }
          .ai-logic-card {
            min-height: 154px;
            display: flex;
            flex-direction: column;
            gap: 9px;
            padding: 13px;
            border-radius: 8px;
            background: rgba(31, 35, 35, 0.7);
            border: 1px solid ${aura.borderSoft};
          }
          .ai-logic-card-top {
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .ai-logic-index {
            width: 22px;
            height: 22px;
            border-radius: 7px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: ${aura.accentSoft};
            color: ${aura.accent};
            font-size: 12px;
            font-weight: 700;
          }
          .ai-logic-icon {
            color: ${aura.muted};
            display: inline-flex;
            align-items: center;
            font-size: 15px;
          }
          .ai-logic-title {
            color: ${aura.text};
            font-size: 15px;
            line-height: 1.4;
          }
          .ai-logic-card .ai-aura-copy {
            flex: 1;
            display: block;
            font-size: 13px;
          }
          .ai-logic-card-foot {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding-top: 8px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            color: ${aura.subtle};
            font-size: 12px;
          }
          .ai-logic-route {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 13px 14px;
            border-radius: 8px;
            background: rgba(31, 35, 35, 0.58);
            border: 1px solid ${aura.borderSoft};
          }
          .ai-logic-route-flow {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
          }
          .ai-logic-route-flow span {
            min-height: 28px;
            display: inline-flex;
            align-items: center;
            padding: 0 10px;
            border-radius: 14px;
            color: ${aura.text};
            background: rgba(143, 227, 232, 0.1);
            border: 1px solid rgba(143, 227, 232, 0.16);
            font-size: 12px;
            font-weight: 500;
          }
          .ai-logic-route-flow i {
            width: 18px;
            height: 1px;
            background: ${aura.border};
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
          @keyframes aiPromptIn {
            from {
              opacity: 0;
              transform: translateY(18px) scale(0.985);
            }
            to {
              opacity: 1;
              transform: translateY(0) scale(1);
            }
          }
          @keyframes aiWorkbenchIn {
            from {
              opacity: 0;
              transform: translateY(18px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }
          @keyframes aiWorkbenchRise {
            from {
              opacity: 0;
              transform: translateY(56px) scale(0.985);
            }
            to {
              opacity: 1;
              transform: translateY(0) scale(1);
            }
          }
          @keyframes aiComposerDock {
            from {
              opacity: 0;
              transform: translate(-50%, -132px) scale(1.08);
            }
            to {
              opacity: 1;
              transform: translate(-50%, 0) scale(1);
            }
          }
          @keyframes aiTerminalSwitch {
            from {
              opacity: 0;
              transform: translateY(8px);
            }
            to {
              opacity: 1;
              transform: translateY(0);
            }
          }
          @keyframes aiTerminalFocus {
            0% {
              box-shadow: inset 0 0 0 1px rgba(143, 227, 232, 0), 0 0 0 rgba(143, 227, 232, 0);
            }
            48% {
              box-shadow: inset 0 0 0 1px rgba(143, 227, 232, 0.22), 0 0 22px rgba(143, 227, 232, 0.08);
            }
            100% {
              box-shadow: inset 0 0 0 1px rgba(143, 227, 232, 0.04), 0 0 0 rgba(143, 227, 232, 0);
            }
          }
          @keyframes aiTerminalLineIn {
            from {
              opacity: 0;
              transform: translateX(-5px);
            }
            to {
              opacity: 1;
              transform: translateX(0);
            }
          }
          @keyframes aiStepSweep {
            from {
              transform: translateX(-100%);
            }
            to {
              transform: translateX(100%);
            }
          }
          @keyframes aiScanSweep {
            0% {
              transform: translateY(0);
              opacity: 0;
            }
            16% {
              opacity: 1;
            }
            100% {
              transform: translateY(360%);
              opacity: 0;
            }
          }
          @keyframes aiDetectPulse {
            0%, 100% {
              box-shadow: 0 0 0 rgba(143, 227, 232, 0);
            }
            50% {
              box-shadow: 0 0 18px rgba(143, 227, 232, 0.22);
            }
          }
          @media (max-width: 1280px) {
            .ai-collect-body {
              grid-template-columns: minmax(260px, 300px) minmax(0, 1fr);
            }
            .ai-collect-body.is-idle {
              display: flex;
            }
            .ai-collect-body > .ai-collect-scroll {
              display: none;
            }
            .ai-prompt-landing {
              width: min(824px, 100%);
            }
            .ai-session-prompt {
              width: min(680px, calc(100% - 36px));
            }
          }
          @media (max-width: 767px) {
            .ai-collect-workbench {
              height: calc(100vh - 48px);
              max-height: calc(100vh - 48px);
              padding: 12px;
            }
            .ai-collect-body.is-idle {
              padding: 0;
            }
            .ai-prompt-copy {
              padding-left: 0;
            }
            .ai-prompt-landing {
              gap: 34px;
            }
            .ai-prompt-shell {
              grid-template-columns: 22px minmax(0, 1fr) 32px;
              gap: 8px;
              min-height: 66px;
              padding: 0 16px 0 18px;
              border-radius: 33px;
            }
            .ai-prompt-title {
              font-size: clamp(23px, 6vw, 28px);
            }
            .ai-collect-workbench .ai-prompt-input.ant-input {
              font-size: 14px;
            }
            .ai-collect-body.is-session {
              padding-bottom: 106px;
            }
            .ai-session-prompt {
              bottom: 12px;
              width: calc(100% - 24px);
            }
            .ai-session-reference {
              max-width: calc(100% - 12px);
            }
            .ai-session-reference em {
              max-width: 52vw;
            }
            .ai-session-prompt-main {
              grid-template-columns: 20px minmax(0, 1fr) 28px 28px;
              min-height: 52px;
              border-radius: 0 14px 14px 14px;
            }
          }
        `}
      </style>

      <div className="ai-collect-workbench">
        <header
          className={`ai-collect-header ${hasSession ? 'is-session' : 'is-idle'}`}
          style={{
            borderBottom: `1px solid ${aura.border}`,
            background: hasSession ? aura.bg : 'transparent',
          }}
        >
          <div />
        </header>

        <div className={`ai-collect-body ${hasSession ? 'is-session' : 'is-idle'}`}>
          {!hasSession ? (
            renderMissionPanel('hero')
          ) : (
            <>
              {renderStepNavigator()}

              <main className="ai-collect-panel ai-stage-shell" style={panelStyle}>
                <div className="ai-stage-content">
                  {streamError && <Alert type="warning" showIcon message={streamError} style={{ marginBottom: 12 }} />}
                  {renderStageContent()}
                </div>
              </main>

              {renderGuidancePanel()}
            </>
          )}
        </div>
        {hasSession ? renderDockedPrompt() : null}
      </div>
    </ErrorBoundary>
  );
};

export default AICollect;

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
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AudioOutlined,
  BranchesOutlined,
  CaretRightOutlined,
  CheckCircleOutlined,
  CloseOutlined,
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
import { useSearchParams } from 'react-router-dom';
import ErrorBoundary from '@/components/ErrorBoundary';
import {
  type DryRunResponse,
  type FieldDef,
  createAnalyzeStream,
  dryRun as dryRunApi,
  generateTemplate as generateTemplateApi,
} from '@/services/aiApi';
import WorkspaceDock, { type WorkspacePanel } from './WorkspaceDock';
import workspacePalette from './palette';

const { Text } = Typography;
const { TextArea } = Input;
const tiffanyAccent = '#81D8D0';
const SessionStatusIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg
    viewBox="0 0 24 24"
    className={className}
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M5.5 6.25H14.25C15.9069 6.25 17.25 7.59315 17.25 9.25V10.75"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
    />
    <path
      d="M5.5 8.75H14.5"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
    />
    <path
      d="M6 11.75V16C6 17.6569 7.34315 19 9 19H11.5"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
    />
    <circle cx="7.4" cy="7.45" r="0.65" fill="currentColor" />
    <circle cx="9.85" cy="7.45" r="0.65" fill="currentColor" />
    <path
      d="M14.8 12.7L19.4 14.5L17.2 15.65L18.35 19L16.8 19.55L15.65 16.2L13.5 18.05L14.8 12.7Z"
      stroke="currentColor"
      strokeWidth="1.35"
      strokeLinejoin="round"
      strokeLinecap="round"
    />
  </svg>
);

type WorkMode = 'explore' | 'contract' | 'dryrun' | 'publish';
type MissionTab = 'goal' | 'policy';
type RunStatus = 'idle' | 'running' | 'paused' | 'completed';
type ProcessStepKey = 'prepare' | 'entry' | 'structure' | 'contract' | 'dryrun' | 'publish';
type TerminalLogLevel = 'info' | 'ok' | 'warn';
type TemplateEntry = {
  key: string;
  value: string;
  step: ProcessStepKey;
  multiline: boolean;
  depth: number;
};

type TemplateCatalogItem = {
  id: string;
  fileName: string;
  displayName: string;
  entries: TemplateEntry[];
};

const templateSourceModules = import.meta.glob('../../../../templates/*.yaml', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

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

const templateStepKeys: Record<ProcessStepKey, string[]> = {
  prepare: ['name', 'display_name', 'base_url', 'data_type', 'adapter', 'anti_crawl_enabled', 'description'],
  entry: [
    'batch_params',
    'params',
    'response_type',
    'json_item_path',
    'json_total_path',
    'json_page_path',
    'json_total_num_pages',
    'list_page',
    'list_request',
  ],
  structure: ['dedup_fields', 'list_fields', 'list_pagination'],
  contract: ['download'],
  dryrun: [],
  publish: [],
};

const inferTemplateStep = (key: string): ProcessStepKey => {
  const matchedStep = processStepOrder.find((step) => templateStepKeys[step].includes(key));
  return matchedStep ?? 'publish';
};

const stripYamlQuotes = (value: string) => value.trim().replace(/^['"]|['"]$/g, '');

const parseTemplateEntries = (raw: string): TemplateEntry[] => {
  const entries: TemplateEntry[] = [];
  const lines = raw.replace(/\r\n/g, '\n').split('\n');
  const keyPathStack: Array<{ indent: number; path: string }> = [];
  const listItemContextStack: Array<{ indent: number; path: string }> = [];
  const listIndexMap = new Map<string, number>();

  const normalizeYamlValue = (value: string) => {
    const trimmed = value.trim();
    return trimmed ? trimmed : 'null';
  };

  const collectBlockValue = (startIndex: number, parentIndent: number) => {
    const blockLines: string[] = [];
    let nextIndex = startIndex;

    while (nextIndex < lines.length) {
      const nextLine = lines[nextIndex];
      const nextTrimmed = nextLine.trim();

      if (!nextTrimmed) {
        blockLines.push('');
        nextIndex += 1;
        continue;
      }

      const nextIndent = nextLine.match(/^\s*/)?.[0].length ?? 0;
      if (nextIndent <= parentIndent) break;

      blockLines.push(nextLine.slice(Math.min(nextLine.length, parentIndent + 2)));
      nextIndex += 1;
    }

    return {
      nextIndex: nextIndex - 1,
      value: blockLines.join('\n').trimEnd() || 'null',
    };
  };

  const pruneStacks = (indent: number, isListItem: boolean) => {
    while (keyPathStack.length && keyPathStack[keyPathStack.length - 1].indent >= indent) {
      keyPathStack.pop();
    }
    while (
      listItemContextStack.length
      && (
        listItemContextStack[listItemContextStack.length - 1].indent > indent
        || (isListItem && listItemContextStack[listItemContextStack.length - 1].indent === indent)
      )
    ) {
      listItemContextStack.pop();
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    const isListItem = trimmed.startsWith('- ');
    pruneStacks(indent, isListItem);

    const parentPath = keyPathStack[keyPathStack.length - 1]?.path
      ?? listItemContextStack[listItemContextStack.length - 1]?.path
      ?? '';
    const depth = Math.max(0, Math.floor(indent / 2));

    const listItemMatch = trimmed.match(/^- (.+)$/);
    if (listItemMatch) {
      const listBase = parentPath || 'list';
      const listKey = `${listBase}@${indent}`;
      const listIndex = (listIndexMap.get(listKey) ?? -1) + 1;
      const itemPath = `${listBase}[${listIndex}]`;
      listIndexMap.set(listKey, listIndex);

      const value = listItemMatch[1];
      const nestedKeyMatch = value.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
      if (nestedKeyMatch) {
        const keyName = nestedKeyMatch[1];
        const rawValue = (nestedKeyMatch[2] ?? '').trim();
        const key = `${itemPath}.${keyName}`;
        let keyValue = normalizeYamlValue(rawValue);
        let multiline = false;

        if (rawValue === '|' || rawValue === '>') {
          const block = collectBlockValue(index + 1, indent);
          keyValue = block.value;
          multiline = true;
          index = block.nextIndex;
        } else if (keyValue.includes('[') || keyValue.includes('{')) {
          multiline = true;
        }

        entries.push({
          key,
          value: keyValue,
          step: inferTemplateStep(keyName),
          multiline,
          depth,
        });

        if (!rawValue) {
          listItemContextStack.push({ indent, path: itemPath });
          keyPathStack.push({ indent, path: key });
        } else {
          listItemContextStack.push({ indent, path: itemPath });
        }
      } else {
        entries.push({
          key: itemPath,
          value: normalizeYamlValue(value),
          step: inferTemplateStep(listBase.split('.').pop() ?? listBase),
          multiline: value.includes('[') || value.includes('{'),
          depth,
        });
      }
      continue;
    }

    const keyMatch = trimmed.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
    if (!keyMatch) continue;

    const keyName = keyMatch[1];
    const rawValue = (keyMatch[2] ?? '').trim();
    const path = parentPath ? `${parentPath}.${keyName}` : keyName;
    let value = normalizeYamlValue(rawValue);
    let multiline = false;

    if (rawValue === '|' || rawValue === '>') {
      const block = collectBlockValue(index + 1, indent);
      value = block.value;
      multiline = true;
      index = block.nextIndex;
    } else if (value.includes('[') || value.includes('{')) {
      multiline = true;
    }

    entries.push({
      key: path,
      value,
      step: inferTemplateStep(keyName),
      multiline,
      depth,
    });

    if (!rawValue) {
      keyPathStack.push({ indent, path });
    }
  }

  return entries;
};

const templateCatalog: TemplateCatalogItem[] = Object.entries(templateSourceModules)
  .map(([path, raw]) => {
    const fileName = path.split('/').pop() ?? path;
    const id = fileName.replace(/\.ya?ml$/i, '');
    const entries = parseTemplateEntries(raw);
    const name = entries.find((entry) => entry.key === 'display_name')?.value
      ?? entries.find((entry) => entry.key === 'name')?.value
      ?? id;
    return {
      id,
      fileName,
      displayName: stripYamlQuotes(name),
      entries,
    };
  })
  .sort((left, right) => left.id.localeCompare(right.id));

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

const aura = workspacePalette;

const AICollect: React.FC = () => {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const analyzeStreamRef = useRef<EventSource | null>(null);
  const simulationTimerRef = useRef<number | null>(null);
  const promptGenerationTimerRef = useRef<number | null>(null);
  const templateScrollRef = useRef<HTMLDivElement | null>(null);
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
  const [selectedStageGuideStep, setSelectedStageGuideStep] = useState<ProcessStepKey | null>(null);
  const [hoveredStageGuideStep, setHoveredStageGuideStep] = useState<ProcessStepKey | null>(null);
  const [completedProcessSteps, setCompletedProcessSteps] = useState<Set<ProcessStepKey>>(new Set());
  const [visibleProcessSteps, setVisibleProcessSteps] = useState<ProcessStepKey[]>(['prepare']);
  const [selectedLogStep, setSelectedLogStep] = useState<ProcessStepKey>('prepare');
  const [scanPulse, setScanPulse] = useState(0);
  const [liveLogs, setLiveLogs] = useState<string[]>(['等待采集目标']);
  const [promptGenerating, setPromptGenerating] = useState(false);

  const [editingTemplateKey, setEditingTemplateKey] = useState<string | null>(null);
  const [templateValueDrafts, setTemplateValueDrafts] = useState<Record<string, string>>({});
  const [browserPreviewVisible, setBrowserPreviewVisible] = useState(true);
  const [sideInspectorOpen, setSideInspectorOpen] = useState(false);
  const [composerMaskVisible, setComposerMaskVisible] = useState(false);

  const hasSession = runStatus !== 'idle';
  const selectedCount = fields.filter((field) => selectedFields.has(field.name)).length;
  const qualityScore = mode === 'publish' ? 94 : mode === 'dryrun' ? 86 : mode === 'contract' ? 88 : 92;
  const activeStepIndex = processStepOrder.indexOf(activeProcessStep);
  const activeTemplate = useMemo(() => {
    if (!templateCatalog.length) return { id: 'empty', fileName: 'empty.yaml', displayName: 'Template', entries: [] };

    const signal = `${templateId} ${submittedPrompt} ${intent} ${url}`.toLowerCase();
    return templateCatalog.find((template) => signal.includes(template.id.toLowerCase()))
      ?? (signal.includes('patent') ? templateCatalog.find((template) => template.id === 'google_patent') : undefined)
      ?? templateCatalog.find((template) => template.id === 'google_patent')
      ?? templateCatalog[0];
  }, [intent, submittedPrompt, templateId, url]);
  const visibleTemplateEntries = useMemo(
    () => activeTemplate.entries.filter((entry) => processStepOrder.indexOf(entry.step) <= activeStepIndex),
    [activeStepIndex, activeTemplate.entries],
  );
  const browserPreviewHost = useMemo(() => {
    const candidate = url || submittedPrompt.match(/https?:\/\/[^\s，。；,]+/i)?.[0] || '';
    if (!candidate) return '';

    try {
      const normalized = candidate.replace(/\{\{\s*[^}]+\s*\}\}|\{\s*[^}]+\s*\}/g, 'sample');
      return new URL(normalized).host;
    } catch {
      return candidate.replace(/^https?:\/\//i, '').split('/')[0] ?? candidate;
    }
  }, [submittedPrompt, url]);
  const adapterFileName = useMemo(
    () => `app/adapters/${activeTemplate.id === 'empty' ? 'generated_adapter' : activeTemplate.id}.py`,
    [activeTemplate.id],
  );
  const adapterClassName = useMemo(() => {
    const raw = activeTemplate.id === 'empty' ? 'generated_adapter' : activeTemplate.id;
    return raw
      .split(/[_-]+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join('') || 'GeneratedAdapter';
  }, [activeTemplate.id]);
  const sessionSideMode = useMemo<'browser' | 'code'>(
    () => (
      processStepOrder.indexOf(activeProcessStep) < processStepOrder.indexOf('contract')
        ? 'browser'
        : 'code'
    ),
    [activeProcessStep],
  );
  const sessionStatusText = useMemo(
    () => (
      sessionSideMode === 'browser'
        ? `AI 控制浏览器 · ${submittedPrompt || url || '目标页面待分析'} · ${browserPreviewHost || 'live session'}`
        : `AI 正在编写适配器 · ${adapterFileName} · ${processStepMeta[activeProcessStep].title} · ${processStepMeta[activeProcessStep].desc}`
    ),
    [activeProcessStep, adapterFileName, browserPreviewHost, sessionSideMode, submittedPrompt, url],
  );
  const browserInspectorNotes = useMemo(
    () => [
      `打开 ${browserPreviewHost || '目标页面'} 并采集页面结构证据`,
      `围绕 ${processStepMeta[activeProcessStep].title} 提取列表与详情信号`,
      liveLogs[0] ?? '等待新的浏览器事件',
    ],
    [activeProcessStep, browserPreviewHost, liveLogs],
  );
  const codeInspectorNotes = useMemo(
    () => [
      `生成 ${adapterFileName}`,
      `同步 ${visibleTemplateEntries.length} 个模板字段与提取规则`,
      `当前阶段：${processStepMeta[activeProcessStep].desc}`,
    ],
    [activeProcessStep, adapterFileName, visibleTemplateEntries.length],
  );
  const codePreviewText = useMemo(
    () => [
      `# ${activeTemplate.displayName}`,
      `class ${adapterClassName}Adapter(BaseAdapter):`,
      `    source_host = "${browserPreviewHost || 'pending-host'}"`,
      `    template_file = "${activeTemplate.fileName}"`,
      `    current_stage = "${processStepMeta[activeProcessStep].title}"`,
      `    mapped_keys = ${visibleTemplateEntries.length}`,
    ].join('\n'),
    [activeProcessStep, activeTemplate.displayName, activeTemplate.fileName, adapterClassName, browserPreviewHost, visibleTemplateEntries.length],
  );
  const activeWorkspacePanel = useMemo<WorkspacePanel | null>(() => {
    const panel = searchParams.get('panel');
    return panel === 'templates' || panel === 'tasks' ? panel : null;
  }, [searchParams]);

  useEffect(() => () => {
    analyzeStreamRef.current?.close();
    if (simulationTimerRef.current) {
      window.clearTimeout(simulationTimerRef.current);
    }
    if (promptGenerationTimerRef.current) {
      window.clearTimeout(promptGenerationTimerRef.current);
    }
  }, []);

  useEffect(() => {
    setExpandedStep(mode);
  }, [mode]);

  useEffect(() => {
    if (!hasSession) {
      setEditingTemplateKey(null);
      setSelectedStageGuideStep(null);
      setHoveredStageGuideStep(null);
      setPromptGenerating(false);
      setSideInspectorOpen(false);
      setComposerMaskVisible(false);
      if (promptGenerationTimerRef.current) {
        window.clearTimeout(promptGenerationTimerRef.current);
        promptGenerationTimerRef.current = null;
      }
    }
  }, [hasSession]);

  useEffect(() => {
    if (!hasSession) {
      setBrowserPreviewVisible(true);
      return;
    }
    setBrowserPreviewVisible(Boolean(browserPreviewHost));
  }, [browserPreviewHost, hasSession, submittedPrompt]);

  const pushLiveLog = useCallback((log: string) => {
    setLiveLogs((prev) => [log, ...prev].slice(0, 8));
  }, []);

  const updateComposerMaskVisibility = useCallback(() => {
    const node = templateScrollRef.current;
    if (!node) {
      setComposerMaskVisible(false);
      return;
    }
    if (node.scrollHeight <= node.clientHeight + 4) {
      setComposerMaskVisible(false);
      return;
    }
    const remaining = node.scrollHeight - node.scrollTop - node.clientHeight;
    setComposerMaskVisible(remaining <= 168);
  }, []);

  useEffect(() => {
    if (!hasSession) {
      setComposerMaskVisible(false);
      return;
    }
    const rafId = window.requestAnimationFrame(() => {
      updateComposerMaskVisibility();
    });
    return () => window.cancelAnimationFrame(rafId);
  }, [activeProcessStep, hasSession, submittedPrompt, updateComposerMaskVisibility, visibleTemplateEntries.length]);

  const finishPromptGeneration = useCallback(() => {
    if (promptGenerationTimerRef.current) {
      window.clearTimeout(promptGenerationTimerRef.current);
      promptGenerationTimerRef.current = null;
    }
    setPromptGenerating(false);
  }, []);

  const triggerPromptGeneration = useCallback(() => {
    if (promptGenerationTimerRef.current) {
      window.clearTimeout(promptGenerationTimerRef.current);
    }
    setPromptGenerating(true);
    promptGenerationTimerRef.current = window.setTimeout(() => {
      promptGenerationTimerRef.current = null;
      setPromptGenerating(false);
    }, 1400);
  }, []);

  const resetSimulation = useCallback(() => {
    if (simulationTimerRef.current) {
      window.clearTimeout(simulationTimerRef.current);
      simulationTimerRef.current = null;
    }
    if (promptGenerationTimerRef.current) {
      window.clearTimeout(promptGenerationTimerRef.current);
      promptGenerationTimerRef.current = null;
    }
    setActiveProcessStep('prepare');
    setSelectedStageGuideStep(null);
    setHoveredStageGuideStep(null);
    setSelectedLogStep('prepare');
    setCompletedProcessSteps(new Set());
    setVisibleProcessSteps(['prepare']);
    setScanPulse(0);
    setPromptGenerating(false);
    setComposerMaskVisible(false);
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
    finishPromptGeneration();
    setRunStatus('idle');
    setMode('explore');
    setStreamError('');
    setSubmittedPrompt('');
    setTaskDraft('');
    setReferenceEditing(false);
    setReferenceDraft('');
    message.info('已取消当前分析');
  }, [finishPromptGeneration, message]);

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
    backdropFilter: aura.backdrop,
    boxShadow: aura.shadow,
  };

  const handleGuideSubmit = useCallback(() => {
    const guide = taskDraft.trim();
    if (!guide) return;
    pushLiveLog(`用户引导：${guide}`);
    setTaskDraft('');
    setScanPulse((prev) => prev + 1);
    triggerPromptGeneration();
  }, [pushLiveLog, taskDraft, triggerPromptGeneration]);

  const handleSessionSparkleAction = useCallback(() => {
    if (promptGenerating) {
      finishPromptGeneration();
      return;
    }
    handleGuideSubmit();
  }, [finishPromptGeneration, handleGuideSubmit, promptGenerating]);

  const handlePromptKeyDown = useCallback((event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    if (hasSession) {
      if (promptGenerating) {
        finishPromptGeneration();
        return;
      }
      handleGuideSubmit();
      return;
    }
    handleAnalyze();
  }, [finishPromptGeneration, handleAnalyze, handleGuideSubmit, hasSession, promptGenerating]);

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

  const handleWorkspacePanelToggle = useCallback((panel: WorkspacePanel) => {
    const nextParams = new URLSearchParams(searchParams);
    if (activeWorkspacePanel === panel) {
      nextParams.delete('panel');
    } else {
      nextParams.set('panel', panel);
    }
    setSearchParams(nextParams);
  }, [activeWorkspacePanel, searchParams, setSearchParams]);

  const handleWorkspacePanelClose = useCallback(() => {
    if (!activeWorkspacePanel) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('panel');
    setSearchParams(nextParams);
  }, [activeWorkspacePanel, searchParams, setSearchParams]);

  const renderMissionPanel = (variant: 'hero' | 'compact') => {
    if (variant === 'hero') {
      return (
        <section className="ai-prompt-landing">
          <div className="ai-prompt-copy">
            <h1 className="ai-prompt-title">嗨，<span className="ai-prompt-name">{currentUserName}</span>，又有新灵感了吗？</h1>
          </div>

          <div className="ai-prompt-shell">
            <span className="ai-prompt-leading-icon" aria-hidden="true"><GlobalOutlined /></span>
            <TextArea
              className="ai-prompt-input"
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
              onKeyDown={handlePromptKeyDown}
              autoSize={{ minRows: 1, maxRows: 3 }}
              placeholder="输入目标网址、采集意图或数据范围"
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
            <Text strong className="ai-aura-title">源站结构识别</Text>
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

  useEffect(() => {
    setTemplateValueDrafts((prev) => {
      const next = { ...prev };
      activeTemplate.entries.forEach((entry) => {
        if (!(entry.key in next)) {
          next[entry.key] = entry.value;
        }
      });
      return next;
    });
  }, [activeTemplate]);

  const handleTemplateValueChange = useCallback((key: string, value: string) => {
    setTemplateValueDrafts((prev) => ({ ...prev, [key]: value }));
  }, []);

  const renderTemplateField = useCallback((entry: TemplateEntry) => {
    const value = templateValueDrafts[entry.key] ?? entry.value;
    const isEditing = editingTemplateKey === entry.key;
    const label = entry.key.split('.').pop() ?? entry.key;
    const pathHint = label === entry.key ? '' : entry.key.slice(0, -(label.length + 1));

    return (
      <div
        className={`ai-template-field ${entry.multiline ? 'is-multiline' : ''}`}
        key={entry.key}
        style={{ ['--ai-template-depth' as string]: String(entry.depth) }}
      >
        <div className="ai-template-field-key">
          <span>{label}</span>
          {pathHint ? <small>{pathHint}</small> : null}
        </div>
        <div
          className={`ai-template-field-value ${isEditing ? 'is-editing' : ''}`}
          onDoubleClick={() => setEditingTemplateKey(entry.key)}
        >
          {isEditing ? (
            entry.multiline ? (
              <TextArea
                autoFocus
                value={value}
                autoSize={{ minRows: 3, maxRows: 10 }}
                onChange={(event) => handleTemplateValueChange(entry.key, event.target.value)}
                onBlur={() => setEditingTemplateKey(null)}
              />
            ) : (
              <Input
                autoFocus
                value={value}
                onChange={(event) => handleTemplateValueChange(entry.key, event.target.value)}
                onBlur={() => setEditingTemplateKey(null)}
                onPressEnter={() => setEditingTemplateKey(null)}
              />
            )
          ) : (
            <pre>{value || 'null'}</pre>
          )}
        </div>
      </div>
    );
  }, [editingTemplateKey, handleTemplateValueChange, templateValueDrafts]);

  const renderSessionTemplateSheet = () => (
    <section className="ai-session-template-shell">
      <header className="ai-session-fixed-meta">
        <div className="ai-session-fixed-copy">
          <Text className="ai-session-fixed-eyebrow">Template Contract</Text>
          <div className="ai-session-fixed-title-row">
            <h2>{activeTemplate.displayName}</h2>
            <Text className="ai-session-fixed-stat">{visibleTemplateEntries.length} keys</Text>
          </div>
          <Text className="ai-session-fixed-subtitle">{activeTemplate.fileName}</Text>
        </div>
        {renderSessionBrowserPreview()}
      </header>

      <div
        className="ai-session-template-scroll"
        ref={templateScrollRef}
        onScroll={updateComposerMaskVisibility}
      >
        <article className="ai-template-sheet">
          <div className="ai-template-sheet-body">
            {visibleTemplateEntries.map(renderTemplateField)}
          </div>
        </article>
        <div className="ai-session-template-divider" aria-hidden="true" />
      </div>
    </section>
  );

  const renderSessionStageRail = () => {
    const guideStep = selectedStageGuideStep;
    const focusStep = hoveredStageGuideStep ?? selectedStageGuideStep;
    const guideStageIndex = guideStep ? Math.max(0, processStepOrder.indexOf(guideStep)) : 0;
    const popoverOffset = -8 + guideStageIndex * 16;
    const focusStageIndex = focusStep ? Math.max(0, processStepOrder.indexOf(focusStep)) : -1;

    return (
      <aside
        className="ai-session-stage-float"
        aria-label="分析阶段提示"
        onMouseLeave={() => setHoveredStageGuideStep(null)}
      >
        <div className="ai-session-stage-bars">
          {processStepOrder.map((step, index) => {
            const status = completedProcessSteps.has(step)
              ? 'done'
              : step === activeProcessStep
                ? 'active'
                : visibleProcessSteps.includes(step)
                  ? 'visible'
                  : 'idle';
            const distance = focusStageIndex >= 0 ? Math.abs(index - focusStageIndex) : null;
            const barWidth = distance === null
              ? 6
              : distance === 0
                ? (hoveredStageGuideStep ? 20 : 18)
                : distance === 1
                  ? 14
                  : distance === 2
                    ? 10
                    : distance === 3
                      ? 8
                      : 6;
            const barOpacity = distance === null
              ? 0.24
              : distance === 0
                ? (hoveredStageGuideStep ? 0.72 : 0.94)
                : distance === 1
                  ? 0.52
                  : distance === 2
                    ? 0.38
                    : distance === 3
                      ? 0.3
                      : 0.24;
            return (
              <button
                type="button"
                key={step}
                className={`ai-session-stage-bar is-${status}`}
                style={{
                  ['--ai-stage-bar-width' as string]: `${barWidth}px`,
                  ['--ai-stage-bar-opacity' as string]: String(barOpacity),
                }}
                onMouseEnter={() => setHoveredStageGuideStep(step)}
                onClick={() => {
                  if (!visibleProcessSteps.includes(step)) return;
                  setActiveProcessStep(step);
                  setMode(processStepMode[step]);
                  setExpandedStep(processStepMode[step]);
                  setSelectedLogStep(step);
                  setSelectedStageGuideStep((prev) => (prev === step ? null : step));
                }}
                aria-label={processStepMeta[step].title}
              >
                <span />
              </button>
            );
          })}
        </div>
        {guideStep ? (
          <div className="ai-session-stage-card" style={{ top: `${popoverOffset}px` }}>
            <strong>{processStepMeta[guideStep].title}</strong>
            <p>{processStepMeta[guideStep].desc}</p>
            <div className="ai-session-stage-card-foot">
              <span className="ai-session-stage-file">{activeTemplate.fileName}</span>
              {guideStep === activeProcessStep && processStepMeta[guideStep].needConfirm ? (
                <Button
                  size="small"
                  className="ai-session-stage-confirm"
                  onClick={() => handleConfirmProcessStep(guideStep)}
                >
                  确认阶段
                </Button>
              ) : (
                <em>{guideStageIndex + 1}/{processStepOrder.length}</em>
              )}
            </div>
          </div>
        ) : null}
      </aside>
    );
  };

  const renderSessionBrowserPreview = () => {
    return (
      <button
        type="button"
        className={`ai-session-status-line is-${sessionSideMode}`}
        aria-label={sessionSideMode === 'browser' ? '展开浏览器状态面板' : '展开编码状态面板'}
        onClick={() => setSideInspectorOpen(true)}
      >
        <span className="ai-session-status-icon" aria-hidden="true">
          <SessionStatusIcon className="ai-session-status-icon-svg" />
        </span>
        <span className="ai-session-status-copy">
          <span className="ai-session-status-copy-base">{sessionStatusText}</span>
          <span className="ai-session-status-copy-sweep" aria-hidden="true">{sessionStatusText}</span>
        </span>
      </button>
    );
  };

  const renderSessionSidePanel = () => (
    <aside className={`ai-session-side-panel is-${sessionSideMode}`}>
      <div className="ai-session-side-head">
        <div className="ai-session-side-copy">
          <Text className="ai-session-side-label">
            {sessionSideMode === 'browser' ? 'AI Browser' : 'Adapter Coding'}
          </Text>
          <Text strong className="ai-session-side-title">
            {sessionSideMode === 'browser' ? 'AI 正在控制浏览器' : 'AI 正在编写适配器'}
          </Text>
        </div>
        <Button
          type="text"
          className="ai-session-side-close"
          icon={<CloseOutlined />}
          aria-label="关闭右侧状态面板"
          onClick={() => setSideInspectorOpen(false)}
        />
      </div>

      {sessionSideMode === 'browser' ? (
        <div className="ai-side-browser-shell">
          <div className="ai-side-browser-bar">
            <span className="ai-side-browser-dot is-red" />
            <span className="ai-side-browser-dot is-yellow" />
            <span className="ai-side-browser-dot is-green" />
            <strong>{browserPreviewHost || 'browser-session'}</strong>
          </div>
          <div className="ai-side-browser-viewport">
            <div className="ai-side-browser-chip">AI browsing</div>
            {browserInspectorNotes.map((note) => (
              <div className="ai-side-browser-row" key={note}>
                <span />
                <strong>{note}</strong>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="ai-side-code-shell">
          <div className="ai-side-code-bar">
            <strong>{adapterFileName}</strong>
            <em>coding</em>
          </div>
          <pre className="ai-side-code-block">{codePreviewText}</pre>
          <div className="ai-side-code-list">
            {codeInspectorNotes.map((note) => (
              <div className="ai-side-code-row" key={note}>
                <span />
                <strong>{note}</strong>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  );

  const renderSessionLayout = () => (
    <div className="ai-session-layout">
      <div className="ai-session-template-frame">
        {renderSessionStageRail()}
        {renderSessionTemplateSheet()}
      </div>
      <button
        type="button"
        className={`ai-session-side-trigger is-${sessionSideMode} ${sideInspectorOpen ? 'is-open' : ''}`}
        aria-label={sessionSideMode === 'browser' ? '点击查看浏览器状态' : '点击查看适配器编写状态'}
        onClick={() => setSideInspectorOpen((prev) => !prev)}
      >
        <SessionStatusIcon className="ai-session-side-trigger-icon" />
        <span>{sessionSideMode === 'browser' ? '查看浏览器' : '查看编码状态'}</span>
      </button>
      {sideInspectorOpen ? renderSessionSidePanel() : null}
    </div>
  );

  const renderDockedPrompt = () => {
    const referenceText = submittedPrompt || url || '目标源站待识别';

    return (
      <section className={`ai-session-prompt ${composerMaskVisible ? 'is-masked' : ''}`}>
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
            placeholder="补充字段规则、调度要求或数据边界"
          />
          <Button
            className={`ai-session-icon-btn ai-session-sparkle-btn ${promptGenerating ? 'is-busy' : ''}`}
            aria-label={promptGenerating ? '暂停本次生成' : '智能优化输入内容'}
            onClick={handleSessionSparkleAction}
          >
            {promptGenerating ? (
              <PauseCircleOutlined className="ai-session-sparkle-pause" aria-hidden="true" />
            ) : (
              <span className="ai-session-sparkle" aria-hidden="true">✦</span>
            )}
          </Button>
          <Button className="ai-session-icon-btn" icon={<AudioOutlined />} aria-label="语音输入" disabled />
        </div>
      </section>
    );
  };

  const renderStepNavigator = () => (
    <div className="ai-step-strip">
      <div className="ai-step-strip-head">
        <div>
          <Text className="ai-step-strip-label">流程推进</Text>
          <Text className="ai-step-strip-current">{processStepMeta[activeProcessStep].desc}</Text>
        </div>
        <div className="ai-step-strip-meta">
          {processStepMeta[activeProcessStep].needConfirm ? (
            <Button size="small" className="ai-step-confirm" onClick={() => handleConfirmProcessStep(activeProcessStep)}>
              确认当前阶段
            </Button>
          ) : (
            <Tag className="ai-aura-tag">自动推进</Tag>
          )}
        </div>
      </div>
      <div className="ai-step-strip-track">
        {visibleProcessSteps.map((step, index) => {
          const status = getStepStatus(step);
          const meta = processStepMeta[step];
          const statusLabel = status === 'done' ? '已完成' : status === 'active' ? '进行中' : '待推进';
          return (
            <button
              type="button"
              key={step}
              className={`ai-step-chip is-${status}`}
              onClick={() => {
                setActiveProcessStep(step);
                setMode(processStepMode[step]);
                setExpandedStep(processStepMode[step]);
                setSelectedLogStep(step);
              }}
            >
              <div className="ai-step-chip-top">
                <span className="ai-step-chip-index">
                  {status === 'done' ? <CheckCircleOutlined /> : index + 1}
                </span>
                <span className="ai-step-chip-state">{statusLabel}</span>
              </div>
              <span className="ai-step-chip-title">{meta.title}</span>
              <span className="ai-step-chip-desc">{meta.desc}</span>
            </button>
          );
        })}
      </div>
    </div>
  );

  const renderGuidancePanel = () => (
    <section className="ai-runtime-dock">
      <div className="ai-runtime-head">
        <Space size={8}>
          <FileTextOutlined style={{ color: aura.accent }} />
          <Text strong className="ai-panel-title">运行动态</Text>
        </Space>
        <Button
          size="small"
          className="ai-step-log-icon"
          icon={<FileTextOutlined />}
          onClick={() => {
            setSelectedLogStep(activeProcessStep);
            pushLiveLog(`focus trace group: ${processStepMeta[activeProcessStep].title}`);
          }}
        />
      </div>
      <div className="ai-runtime-list">
        {liveLogs.slice(0, 2).map((log) => (
          <div className="ai-runtime-item is-live" key={log}>
            <span>live</span>
            <strong>{log}</strong>
          </div>
        ))}
        {stepLogs[selectedLogStep].slice(0, 3).map((log) => (
          <div className={`ai-runtime-item is-${log.level}`} key={`${selectedLogStep}-${log.time}-${log.message}`}>
            <span>{log.time}</span>
            <strong>{log.message}</strong>
          </div>
        ))}
      </div>
    </section>
  );

  const renderStageOverview = () => (
    <div className="ai-stage-overview">
      {[
        ['字段', `${selectedCount}/${fields.length}`],
        ['渲染', renderModeLabel[renderMode]],
        ['调度', scheduleModeLabel[scheduleMode]],
        ['输出', outputTargetLabel[outputTarget]],
      ].map(([label, value]) => (
        <div className="ai-overview-card" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
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
            --ai-session-prompt-bottom: 18px;
            --ai-session-prompt-height: 96px;
            --ai-session-runtime-safe-bottom: 76px;
            --ai-session-body-safe-bottom: 18px;
            --ai-session-veil-height: 148px;
            --ai-session-dock-rail-bottom: 35px;
            --ai-session-dock-panel-bottom: 138px;
            height: calc(100vh - 48px);
            max-height: calc(100vh - 48px);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            gap: 16px;
            position: relative;
            background: transparent;
            border-radius: 0;
            padding: 18px;
            color: ${aura.text};
            font-family: "SF Pro Display", "SF Pro Text", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
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
            position: relative;
          }
          .ai-collect-body.is-session {
            display: flex;
            justify-content: center;
            padding: 8px 10px 0;
          }
          .ai-session-shell {
            width: 100%;
            flex: 1;
            min-height: 0;
            display: flex;
            justify-content: center;
          }
          .ai-session-bottom-veil {
            position: absolute;
            left: 50%;
            bottom: 0;
            width: min(980px, calc(100% - 64px));
            height: var(--ai-session-veil-height);
            pointer-events: none;
            opacity: 0;
            z-index: 12;
            transform: translateX(-50%);
            background:
              radial-gradient(120% 118% at 50% 100%, rgba(19, 23, 31, 0.96) 0%, rgba(19, 23, 31, 0.88) 34%, rgba(19, 23, 31, 0.58) 56%, rgba(19, 23, 31, 0.22) 76%, rgba(19, 23, 31, 0) 100%);
            clip-path: ellipse(50% 88% at 50% 100%);
            filter: blur(2px);
            transition: opacity 220ms ease;
          }
          .ai-session-bottom-veil::before {
            content: '';
            position: absolute;
            inset: 14px 10% 0;
            border-radius: 50%;
            background: radial-gradient(72% 78% at 50% 100%, rgba(138, 180, 255, 0.12) 0%, rgba(138, 180, 255, 0.07) 22%, rgba(138, 180, 255, 0) 72%);
            opacity: 0.82;
          }
          .ai-session-bottom-veil.is-visible {
            opacity: 1;
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
            background: rgba(255, 255, 255, 0.035) !important;
            border-color: ${aura.border} !important;
            color: ${aura.text} !important;
            border-radius: 8px !important;
          }
          .ai-collect-workbench .ant-input::placeholder {
            color: ${aura.subtle};
          }
          .ai-collect-workbench .ant-segmented {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid ${aura.border};
            border-radius: 8px;
            padding: 2px;
          }
          .ai-collect-workbench .ant-segmented-item {
            color: ${aura.muted};
            border-radius: 6px;
          }
          .ai-collect-workbench .ant-segmented-item-selected {
            background: ${aura.accentSoft};
            color: ${aura.text};
            box-shadow: inset 0 0 0 1px rgba(138, 180, 255, 0.18);
          }
          .ai-collect-workbench .ant-btn {
            background: rgba(255, 255, 255, 0.03);
            border-color: ${aura.border};
            color: ${aura.text};
            box-shadow: none;
            border-radius: 8px;
          }
          .ai-collect-workbench .ant-btn-link {
            border: none;
            color: ${aura.accent};
            background: transparent;
          }
          .ai-collect-workbench .ant-btn-primary,
          .ai-collect-workbench .ai-aura-primary {
            border-color: rgba(138, 180, 255, 0.18) !important;
            background: ${aura.accentSoft} !important;
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
            bottom: var(--ai-session-prompt-bottom);
            z-index: 18;
            width: min(648px, calc(100% - 52px));
            margin: 0;
            display: flex;
            flex-direction: column;
            padding: 2px 0 6px;
            animation: aiComposerDock 380ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-session-prompt::before {
            content: '';
            position: absolute;
            left: 50%;
            bottom: -18px;
            width: min(860px, calc(100vw - 32px));
            height: 176px;
            transform: translateX(-50%);
            pointer-events: none;
            opacity: 0;
            background:
              linear-gradient(180deg, rgba(16, 18, 18, 0) 0%, rgba(16, 18, 18, 0.26) 22%, rgba(16, 18, 18, 0.68) 58%, ${aura.bg} 100%);
            transition: opacity 180ms ease;
          }
          .ai-session-prompt.is-masked::before {
            opacity: 1;
          }
          .ai-session-reference {
            height: 28px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            align-self: flex-start;
            padding: 0 8px 0 10px;
            border-radius: 14px 14px 0 0;
            background: rgba(29, 33, 41, 0.9);
            border: 1px solid ${aura.border};
            border-bottom: none;
            color: ${aura.text};
            font-size: 12px;
            font-weight: 400;
            max-width: min(100%, 560px);
            position: relative;
            z-index: 1;
            backdrop-filter: ${aura.backdrop};
            box-shadow: 0 18px 42px rgba(0, 0, 0, 0.18);
          }
          .ai-session-reference-icon {
            width: 17px;
            height: 17px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 5px;
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
          .ai-collect-workbench .ai-reference-input.ant-input {
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
            --prompt-surface: rgba(29, 33, 41, 0.9);
            display: grid;
            grid-template-columns: 22px minmax(0, 1fr) 30px 30px;
            align-items: center;
            gap: 8px;
            min-height: 58px;
            padding: 0 14px 0 16px;
            border-radius: 0 18px 18px 18px;
            border: 1px solid ${aura.border};
            box-shadow: 0 28px 58px rgba(0, 0, 0, 0.34);
            position: relative;
            backdrop-filter: ${aura.backdrop};
            overflow: hidden;
            background: var(--prompt-surface);
            z-index: 1;
          }
          .ai-session-prompt-main::before {
            content: '';
            position: absolute;
            inset: 0;
            pointer-events: none;
          }
          .ai-session-leading-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 15px;
            position: relative;
            z-index: 1;
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
            position: relative;
            z-index: 1;
          }
          .ai-session-layout {
            width: 100%;
            min-height: 0;
            flex: 1;
            display: flex;
            justify-content: center;
            position: relative;
          }
          .ai-session-template-frame {
            width: min(100%, 882px);
            min-height: 0;
            display: flex;
            justify-content: center;
            position: relative;
            padding-left: 54px;
          }
          .ai-session-template-shell {
            width: 100%;
            max-width: 794px;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 16px;
          }
          .ai-session-fixed-meta {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(236px, 286px);
            align-items: center;
            gap: 14px;
            padding: 2px 2px 10px;
            background: transparent;
            border: none;
            box-shadow: none;
          }
          .ai-session-fixed-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 4px;
          }
          .ai-session-fixed-eyebrow,
          .ai-session-fixed-subtitle,
          .ai-session-fixed-stat {
            color: ${aura.subtle};
            font-size: 10px;
            letter-spacing: 0.06em;
            text-transform: uppercase;
          }
          .ai-session-fixed-title-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            flex-wrap: wrap;
          }
          .ai-session-fixed-title-row h2 {
            margin: 0;
            color: ${aura.text};
            font-size: 21px;
            line-height: 1.2;
            font-weight: 600;
          }
          .ai-session-fixed-stat {
            white-space: nowrap;
            font-size: 10px;
          }
          .ai-session-fixed-subtitle {
            color: ${aura.muted};
            text-transform: none;
            letter-spacing: 0;
            font-size: 11px;
          }
          .ai-session-stage-float {
            position: absolute;
            left: 10px;
            top: 50%;
            z-index: 7;
            display: flex;
            align-items: center;
            gap: 10px;
            pointer-events: none;
            transform: translateY(-50%);
          }
          .ai-session-stage-bars {
            width: 12px;
            padding: 0;
            display: flex;
            flex-direction: column;
            gap: 6px;
            pointer-events: auto;
          }
          .ai-session-stage-bar {
            width: 12px;
            height: 10px;
            padding: 0;
            border: none;
            background: transparent;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
          }
          .ai-session-stage-bar span {
            width: 6px;
            height: 2px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.2);
            transition: width 160ms ease, background 160ms ease, opacity 160ms ease, transform 160ms ease;
          }
          .ai-session-stage-bar.is-visible span {
            width: 8px;
            background: rgba(255, 255, 255, 0.34);
          }
          .ai-session-stage-bar.is-done span {
            width: 8px;
            background: rgba(255, 255, 255, 0.28);
          }
          .ai-session-stage-bar.is-active span {
            width: 12px;
            background: rgba(255, 255, 255, 0.9);
            transform: translateX(1px);
          }
          .ai-session-stage-card {
            position: absolute;
            left: 20px;
            width: 244px;
            padding: 12px 12px 10px;
            border-radius: 14px;
            background: rgba(42, 46, 53, 0.92);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.22);
            pointer-events: auto;
            transition: top 220ms ease;
          }
          .ai-session-stage-card strong {
            display: block;
            color: ${aura.text};
            font-size: 14px;
            line-height: 1.4;
            font-weight: 600;
          }
          .ai-session-stage-card p {
            margin: 8px 0 10px;
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.66;
          }
          .ai-session-stage-card-foot {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
          }
          .ai-session-stage-file {
            min-width: 0;
            color: rgba(255, 255, 255, 0.6);
            font-size: 11px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .ai-session-stage-card-foot em {
            color: rgba(255, 255, 255, 0.52);
            font-size: 12px;
            font-style: normal;
          }
          .ai-session-stage-confirm {
            height: 26px !important;
            padding: 0 10px !important;
            border-radius: 13px !important;
            border: 1px solid rgba(129, 216, 208, 0.28) !important;
            background: rgba(129, 216, 208, 0.12) !important;
            color: ${aura.text} !important;
            font-size: 12px !important;
          }
          .ai-session-stage-confirm:hover {
            color: ${tiffanyAccent} !important;
            border-color: rgba(129, 216, 208, 0.46) !important;
          }
          .ai-session-template-scroll {
            width: 100%;
            flex: 1;
            min-height: 0;
            overflow: auto;
            padding: 2px 0 0;
          }
          .ai-template-sheet {
            width: 100%;
            margin: 0;
            padding: 28px 30px 30px;
            background:
              linear-gradient(180deg, rgba(44, 49, 60, 0.98), rgba(34, 39, 49, 0.98)),
              rgba(28, 33, 42, 0.98);
            color: ${aura.text};
            box-shadow: 0 18px 46px rgba(0, 0, 0, 0.24);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 18px;
          }
          .ai-session-status-line {
            width: 100%;
            min-width: 0;
            height: 28px;
            padding: 0 2px 0 6px;
            border-radius: 0;
            border: none;
            background: transparent;
            display: grid;
            grid-template-columns: 16px minmax(0, 1fr);
            align-items: center;
            gap: 8px;
            color: ${aura.text};
            cursor: pointer;
            overflow: hidden;
            transition: opacity 160ms ease;
          }
          .ai-session-status-line:hover {
            opacity: 0.92;
          }
          .ai-session-status-icon {
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.92);
            flex-shrink: 0;
          }
          .ai-session-status-icon-svg {
            width: 16px;
            height: 16px;
            display: block;
          }
          .ai-session-status-copy {
            min-width: 0;
            position: relative;
            display: block;
            overflow: hidden;
            white-space: nowrap;
          }
          .ai-session-status-copy-base,
          .ai-session-status-copy-sweep {
            display: block;
            font-size: 12px;
            line-height: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: clip;
          }
          .ai-session-status-copy-base {
            color: rgba(255, 255, 255, 0.72);
          }
          .ai-session-status-copy-sweep {
            position: absolute;
            inset: 0;
            color: rgba(255, 255, 255, 0.96);
            background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.98) 48%, transparent 100%);
            background-size: 34% 100%;
            background-repeat: no-repeat;
            background-position: -120% 0;
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: aiStatusSweep 4.2s ease-in-out infinite;
          }
          .ai-session-status-meta {
            display: none;
          }
          .ai-session-side-trigger {
            position: absolute;
            right: 0;
            top: 112px;
            bottom: calc(var(--ai-session-prompt-height) + 52px);
            width: clamp(72px, calc((100vw - 882px) / 2 - 12px), 118px);
            border: none;
            background: transparent;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 14px;
            color: rgba(255, 255, 255, 0.28);
            cursor: pointer;
            transition: color 160ms ease, transform 160ms ease;
          }
          .ai-session-side-trigger::before {
            content: '';
            position: absolute;
            inset: 18px 0;
            border-left: 1px dashed rgba(255, 255, 255, 0.08);
          }
          .ai-session-side-trigger:hover,
          .ai-session-side-trigger.is-open {
            color: rgba(255, 255, 255, 0.72);
            transform: translateX(-2px);
          }
          .ai-session-side-trigger-icon {
            width: 20px;
            height: 20px;
            position: relative;
            z-index: 1;
          }
          .ai-session-side-trigger span {
            position: relative;
            z-index: 1;
            writing-mode: vertical-rl;
            text-orientation: mixed;
            font-size: 11px;
            letter-spacing: 0.16em;
          }
          .ai-session-side-panel {
            position: absolute;
            right: 16px;
            top: 110px;
            bottom: calc(var(--ai-session-prompt-height) + 48px);
            width: clamp(280px, calc((100vw - 882px) / 2 + 116px), 340px);
            padding: 16px;
            border-radius: 18px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(29, 33, 41, 0.96);
            box-shadow: 0 22px 54px rgba(0, 0, 0, 0.28);
            backdrop-filter: blur(18px);
            display: flex;
            flex-direction: column;
            gap: 14px;
            z-index: 9;
            animation: aiSidePanelIn 220ms ease both;
          }
          .ai-session-side-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
          }
          .ai-session-side-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 6px;
          }
          .ai-session-side-label {
            color: ${aura.subtle};
            font-size: 11px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
          }
          .ai-session-side-title {
            color: ${aura.text};
            font-size: 15px;
            line-height: 1.4;
          }
          .ai-session-side-close {
            width: 26px !important;
            height: 26px !important;
            min-width: 26px !important;
            border-radius: 50% !important;
            border: none !important;
            background: transparent !important;
            color: rgba(255, 255, 255, 0.5) !important;
            box-shadow: none !important;
          }
          .ai-session-side-close:hover {
            color: ${tiffanyAccent} !important;
            background: rgba(129, 216, 208, 0.08) !important;
          }
          .ai-side-browser-shell,
          .ai-side-code-shell {
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
            flex: 1;
          }
          .ai-side-browser-bar,
          .ai-side-code-bar {
            min-height: 40px;
            padding: 0 12px;
            border-radius: 12px;
            background: rgba(18, 21, 27, 0.72);
            border: 1px solid rgba(255, 255, 255, 0.07);
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .ai-side-browser-bar strong,
          .ai-side-code-bar strong {
            min-width: 0;
            color: rgba(255, 255, 255, 0.82);
            font-size: 12px;
            font-weight: 500;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-side-code-bar em {
            margin-left: auto;
            padding: 0 8px;
            border-radius: 999px;
            background: rgba(129, 216, 208, 0.12);
            color: ${tiffanyAccent};
            font-size: 11px;
            line-height: 22px;
            font-style: normal;
            white-space: nowrap;
          }
          .ai-side-browser-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
          }
          .ai-side-browser-dot.is-red {
            background: #FF5F57;
          }
          .ai-side-browser-dot.is-yellow {
            background: #FEBC2E;
          }
          .ai-side-browser-dot.is-green {
            background: #28C840;
          }
          .ai-side-browser-viewport {
            flex: 1;
            min-height: 0;
            padding: 14px;
            border-radius: 16px;
            background:
              linear-gradient(180deg, rgba(40, 45, 54, 0.98), rgba(24, 28, 36, 0.98));
            border: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-side-browser-chip {
            align-self: flex-start;
            padding: 0 10px;
            border-radius: 999px;
            background: rgba(129, 216, 208, 0.12);
            color: ${tiffanyAccent};
            font-size: 11px;
            line-height: 24px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
          }
          .ai-side-browser-row,
          .ai-side-code-row {
            display: grid;
            grid-template-columns: 10px minmax(0, 1fr);
            align-items: start;
            gap: 10px;
            color: rgba(255, 255, 255, 0.74);
            font-size: 12px;
            line-height: 1.7;
          }
          .ai-side-browser-row span,
          .ai-side-code-row span {
            width: 6px;
            height: 6px;
            margin-top: 7px;
            border-radius: 50%;
            background: rgba(129, 216, 208, 0.82);
            display: inline-block;
          }
          .ai-side-code-block {
            margin: 0;
            padding: 14px;
            border-radius: 16px;
            background: rgba(18, 21, 27, 0.78);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: rgba(255, 255, 255, 0.86);
            font-size: 12px;
            line-height: 1.72;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            white-space: pre-wrap;
            word-break: break-word;
          }
          .ai-side-code-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-template-sheet-body {
            display: flex;
            flex-direction: column;
            gap: 18px;
          }
          .ai-template-field {
            --ai-template-indent: calc(var(--ai-template-depth, 0) * 14px);
            display: grid;
            grid-template-columns: minmax(168px, 220px) minmax(0, 1fr);
            gap: 12px 18px;
            align-items: start;
            padding: 0 0 18px;
            padding-left: var(--ai-template-indent);
            border-bottom: 1px dashed rgba(255, 255, 255, 0.09);
          }
          .ai-template-field:last-child {
            padding-bottom: 0;
            border-bottom: none;
          }
          .ai-template-field-key {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
            padding-top: 2px;
          }
          .ai-template-field-key span {
            color: ${aura.text};
            font-size: 13px;
            font-weight: 600;
            line-height: 1.35;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          }
          .ai-template-field-key small {
            color: ${aura.muted};
            font-size: 11px;
            line-height: 1.4;
            letter-spacing: 0;
            text-transform: none;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          }
          .ai-template-field-value {
            min-height: 42px;
            padding: 10px 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(13, 16, 22, 0.26);
            border-radius: 10px;
          }
          .ai-template-field-value.is-editing {
            border-color: rgba(138, 180, 255, 0.48);
            box-shadow: 0 0 0 3px rgba(138, 180, 255, 0.14);
          }
          .ai-template-field-value pre {
            margin: 0;
            white-space: pre-wrap;
            word-break: break-word;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            font-size: 12px;
            line-height: 1.7;
            color: ${aura.text};
          }
          .ai-template-field-value .ant-input,
          .ai-template-field-value .ant-input-affix-wrapper,
          .ai-template-field-value .ant-input-textarea textarea {
            background: transparent !important;
            color: ${aura.text} !important;
            border-color: transparent !important;
            box-shadow: none !important;
            padding: 0 !important;
            border-radius: 0 !important;
          }
          .ai-session-template-divider {
            width: min(620px, calc(100% - 120px));
            margin: 22px auto calc(var(--ai-session-prompt-height) + 44px);
            border-top: 1px dashed rgba(255, 255, 255, 0.2);
            opacity: 0.58;
          }
          .ai-session-bottom-veil {
            display: none !important;
          }
          .ai-session-shell {
            align-items: stretch;
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
            position: relative;
            z-index: 1;
          }
          .ai-session-icon-btn:hover {
            color: ${aura.accent} !important;
            background: ${aura.accentSoft} !important;
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
            color: rgba(255, 255, 255, 0.96);
            font-size: 16px;
            line-height: 1;
            position: relative;
          }
          .ai-session-sparkle-pause {
            font-size: 16px;
            color: rgba(255, 255, 255, 0.96);
          }
          .ai-session-sparkle-btn {
            color: rgba(255, 255, 255, 0.96) !important;
          }
          .ai-session-sparkle-btn:hover {
            background: transparent !important;
            color: ${tiffanyAccent} !important;
          }
          .ai-session-sparkle-btn.is-busy {
            color: rgba(255, 255, 255, 0.96) !important;
          }
          .ai-session-sparkle-btn:hover .ai-session-sparkle,
          .ai-session-sparkle-btn:hover .ai-session-sparkle::after,
          .ai-session-sparkle-btn:hover .ai-session-sparkle-pause {
            color: ${tiffanyAccent};
          }
          .ai-session-sparkle::after {
            content: '✦';
            position: absolute;
            right: -3px;
            top: -5px;
            color: rgba(255, 255, 255, 0.96);
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
            backdrop-filter: ${aura.backdrop};
            box-shadow: ${aura.shadow};
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
            background: rgba(29, 33, 41, 0.72);
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
            border-color: rgba(138, 180, 255, 0.18);
            background: ${aura.accentSoft};
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
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid ${aura.border};
            font-size: 11px;
            font-weight: 600;
          }
          .ai-step-item.is-active .ai-step-index {
            color: ${aura.accent};
            background: ${aura.accentSoft};
            border-color: rgba(138, 180, 255, 0.24);
          }
          .ai-step-item.is-done .ai-step-index {
            color: ${aura.success};
            background: rgba(101, 213, 163, 0.12);
            border-color: rgba(101, 213, 163, 0.24);
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
            background: ${aura.accentSoft};
          }
          .ai-step-item.is-done .ai-step-summary small {
            color: ${aura.success};
            background: rgba(101, 213, 163, 0.08);
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
            background: ${aura.accentSoft} !important;
          }
          .ai-step-confirm {
            height: 24px !important;
            padding: 0 10px !important;
            border-radius: 12px !important;
            border: 1px solid rgba(138, 180, 255, 0.24) !important;
            color: ${aura.text} !important;
            background: ${aura.accentSoft} !important;
            font-size: 12px !important;
          }
          .ai-step-confirm:hover {
            color: ${aura.accent} !important;
            border-color: rgba(138, 180, 255, 0.42) !important;
          }
          .ai-guidance-panel {
            display: flex;
            flex-direction: column;
            padding: 0;
            border-radius: 8px;
            overflow: hidden;
            background: rgba(29, 33, 41, 0.84);
            border-color: ${aura.border};
          }
          .ai-terminal-window {
            height: 100%;
            min-height: 0;
            display: flex;
            flex-direction: column;
            background:
              linear-gradient(180deg, rgba(38, 43, 54, 0.96), rgba(23, 27, 35, 0.96)),
              ${aura.bg};
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
            border-color: rgba(138, 180, 255, 0.2);
            background: rgba(138, 180, 255, 0.08);
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
            color: ${aura.accent};
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
            color: ${aura.accent};
            font-weight: 500;
          }
          .ai-terminal-line code {
            min-width: 0;
            white-space: normal;
            word-break: break-word;
            font-family: inherit;
          }
          .ai-terminal-line.is-ok b {
            color: ${aura.success};
          }
          .ai-terminal-line.is-warn b {
            color: ${aura.warning};
          }
          .ai-terminal-line.is-live {
            color: rgba(245, 247, 247, 0.86);
            animation: aiTerminalLineIn 260ms ease both;
          }
          .ai-terminal-line.is-live b {
            color: ${aura.accent};
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
            gap: 30px;
            animation: aiPromptIn 460ms ease both;
          }
          .ai-prompt-copy {
            text-align: center;
            padding-left: 0;
          }
          .ai-prompt-title {
            margin: 0;
            color: ${aura.text};
            font-size: clamp(30px, 2.8vw, 40px);
            line-height: 1.14;
            font-weight: 600;
            letter-spacing: 0;
          }
          .ai-prompt-name {
            display: inline-block;
            color: #9fc7ff;
            background: linear-gradient(135deg, #c2d8ff 0%, #8ab4ff 58%, #8ee3f0 100%);
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            font-family: "SF Pro Rounded", "SF Pro Display", "SF Pro Text", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
            font-weight: 700;
            text-shadow: 0 10px 26px rgba(138, 180, 255, 0.18);
          }
          .ai-prompt-shell {
            --prompt-surface-base: rgba(29, 33, 41, 0.88);
            position: relative;
            display: grid;
            grid-template-columns: 24px minmax(0, 1fr) 36px;
            align-items: center;
            gap: 14px;
            min-height: 80px;
            padding: 0 30px 0 24px;
            border-radius: 999px;
            background: var(--prompt-surface-base);
            border: 1px solid ${aura.border};
            box-shadow: ${aura.shadow};
            backdrop-filter: ${aura.backdrop};
            overflow: hidden;
          }
          .ai-prompt-shell::before {
            content: '';
            position: absolute;
            inset: 0;
            border-radius: inherit;
            pointer-events: none;
          }
          .ai-prompt-leading-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: ${aura.subtle};
            font-size: 18px;
            position: relative;
            z-index: 1;
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
            position: relative;
            z-index: 1;
          }
          .ai-prompt-icon.ant-btn[disabled],
          .ai-prompt-icon.ant-btn[disabled]:hover {
            color: rgba(245, 247, 247, 0.34) !important;
            cursor: not-allowed;
          }
          .ai-collect-workbench .ai-prompt-input.ant-input {
            min-height: 34px !important;
            max-height: 70px;
            padding: 0 0 0 2px !important;
            background: transparent !important;
            background-color: transparent !important;
            border-color: transparent !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            resize: none;
            font-size: 15px;
            line-height: 34px;
            color: ${aura.text} !important;
            font-family: inherit;
            font-weight: 400;
            caret-color: ${aura.accent};
            position: relative;
            z-index: 1;
          }
          .ai-prompt-shell .ant-input,
          .ai-session-prompt-main .ant-input {
            background-color: transparent !important;
            background-image: none !important;
          }
          .ai-collect-workbench .ai-prompt-input.ant-input::placeholder {
            color: ${aura.subtle};
            text-indent: 5px;
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
            backdrop-filter: ${aura.backdrop};
          }
          .ai-stage-shell {
            padding: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            background:
              linear-gradient(180deg, rgba(31, 36, 45, 0.97) 0%, rgba(21, 25, 33, 0.98) 100%);
            position: relative;
          }
          .ai-stage-shell-full {
            width: min(1380px, 100%);
            height: 100%;
            border-radius: 8px !important;
            margin: 0 auto;
          }
          .ai-stage-top {
            flex-shrink: 0;
            padding: 20px 20px 16px;
            border-bottom: 1px solid ${aura.border};
            background:
              linear-gradient(180deg, rgba(138, 180, 255, 0.08) 0%, rgba(16, 20, 30, 0.34) 56%, rgba(16, 20, 30, 0.16) 100%);
            backdrop-filter: ${aura.backdrop};
            display: flex;
            flex-direction: column;
            gap: 14px;
          }
          .ai-stage-headline {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
          }
          .ai-stage-headline-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 8px;
          }
          .ai-stage-subtitle {
            max-width: 720px;
            color: ${aura.muted};
            font-size: 13px;
            line-height: 1.75;
          }
          .ai-stage-overview {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
            gap: 10px;
          }
          .ai-overview-card {
            min-height: 74px;
            padding: 13px 14px;
            border-radius: 8px;
            border: 1px solid ${aura.borderSoft};
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.025));
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
          }
          .ai-overview-card span {
            color: ${aura.subtle};
            font-size: 11px;
          }
          .ai-overview-card strong {
            color: ${aura.text};
            font-size: 16px;
            line-height: 1.3;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-step-strip {
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding: 14px;
            border-radius: 8px;
            border: 1px solid ${aura.borderSoft};
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.032), rgba(255, 255, 255, 0.02));
          }
          .ai-step-strip-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 14px;
            flex-wrap: wrap;
          }
          .ai-step-strip-label {
            display: block;
            color: ${aura.subtle};
            font-size: 11px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
          }
          .ai-step-strip-current {
            display: block;
            margin-top: 6px;
            color: ${aura.muted};
            font-size: 13px;
            line-height: 1.72;
            max-width: 760px;
          }
          .ai-step-strip-track {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 8px;
            min-width: 0;
            overflow: visible;
          }
          .ai-step-strip-meta {
            flex-shrink: 0;
          }
          .ai-step-chip {
            min-height: 110px;
            padding: 14px;
            border-radius: 8px;
            border: 1px solid ${aura.border};
            background: rgba(255, 255, 255, 0.03);
            color: ${aura.muted};
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 12px;
            cursor: pointer;
            white-space: normal;
            text-align: left;
            position: relative;
            overflow: hidden;
            transition: background 160ms ease, border-color 160ms ease, color 160ms ease, transform 160ms ease;
          }
          .ai-step-chip::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            height: 2px;
            background: rgba(255, 255, 255, 0.12);
            opacity: 0.5;
          }
          .ai-step-chip:hover {
            color: ${aura.text};
            background: rgba(255, 255, 255, 0.055);
            transform: translateY(-1px);
          }
          .ai-step-chip.is-active {
            color: ${aura.text};
            border-color: rgba(138, 180, 255, 0.22);
            background: ${aura.accentSoft};
            box-shadow: inset 0 0 0 1px rgba(138, 180, 255, 0.06);
          }
          .ai-step-chip.is-done {
            color: ${aura.success};
            border-color: rgba(101, 213, 163, 0.2);
            background: rgba(101, 213, 163, 0.08);
          }
          .ai-step-chip.is-active::before {
            background: linear-gradient(90deg, rgba(138, 180, 255, 0.94), rgba(142, 227, 240, 0.6));
            opacity: 1;
          }
          .ai-step-chip.is-done::before {
            background: linear-gradient(90deg, rgba(101, 213, 163, 0.94), rgba(101, 213, 163, 0.48));
            opacity: 1;
          }
          .ai-step-chip-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
          }
          .ai-step-chip-index {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(255, 255, 255, 0.06);
            font-size: 11px;
            font-weight: 600;
          }
          .ai-step-chip-state {
            min-height: 22px;
            display: inline-flex;
            align-items: center;
            padding: 0 8px;
            border-radius: 999px;
            border: 1px solid ${aura.borderSoft};
            background: rgba(255, 255, 255, 0.035);
            color: ${aura.subtle};
            font-size: 11px;
            line-height: 1;
          }
          .ai-step-chip-title {
            font-size: 13px;
            font-weight: 600;
            color: ${aura.text};
            line-height: 1.45;
          }
          .ai-step-chip-desc {
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.65;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
          }
          .ai-runtime-dock {
            flex-shrink: 0;
            padding: 14px 20px calc(16px + var(--ai-session-runtime-safe-bottom));
            border-top: 1px solid ${aura.border};
            background:
              linear-gradient(180deg, rgba(16, 20, 30, 0.26), rgba(16, 20, 30, 0.44));
            display: flex;
            flex-direction: column;
            gap: 12px;
          }
          .ai-runtime-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
          }
          .ai-runtime-list {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
            gap: 10px;
          }
          .ai-runtime-item {
            min-height: 70px;
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid ${aura.borderSoft};
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02));
            display: flex;
            flex-direction: column;
            gap: 6px;
          }
          .ai-runtime-item span {
            color: ${aura.subtle};
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
          }
          .ai-runtime-item strong {
            color: ${aura.text};
            font-size: 12px;
            line-height: 1.45;
            font-weight: 500;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
          }
          .ai-runtime-item.is-live {
            border-color: rgba(138, 180, 255, 0.18);
            background: rgba(138, 180, 255, 0.08);
          }
          .ai-runtime-item.is-ok span {
            color: ${aura.success};
          }
          .ai-runtime-item.is-warn span {
            color: ${aura.warning};
          }
          .ai-stage-content {
            flex: 1;
            min-height: 0;
            padding: 18px 20px;
            overflow: auto;
            scrollbar-width: none;
            background: linear-gradient(180deg, rgba(22, 26, 34, 0.08), rgba(22, 26, 34, 0));
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
            border: 1px solid ${aura.borderSoft};
            backdrop-filter: ${aura.backdrop};
          }
          .ai-logic-workbench {
            gap: 12px;
          }
          .ai-projection-stage {
            min-height: 390px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding: 20px;
            border-radius: 8px;
            background:
              linear-gradient(180deg, rgba(37, 42, 52, 0.92), rgba(25, 29, 37, 0.88)),
              repeating-linear-gradient(90deg, rgba(138, 180, 255, 0.035) 0 1px, transparent 1px 24px);
            border: 1px solid ${aura.border};
            backdrop-filter: ${aura.backdrop};
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
            background: rgba(17, 20, 26, 0.72);
            border: 1px solid ${aura.border};
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02), 0 24px 80px rgba(0, 0, 0, 0.24);
            overflow: hidden;
          }
          .ai-page-projection::before {
            content: '';
            position: absolute;
            inset: 0;
            background-image:
              linear-gradient(rgba(138, 180, 255, 0.045) 1px, transparent 1px),
              linear-gradient(90deg, rgba(138, 180, 255, 0.045) 1px, transparent 1px);
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
            background: linear-gradient(180deg, transparent, rgba(138, 180, 255, 0.2), transparent);
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
            background: rgba(138, 180, 255, 0.08);
            border: 1px solid rgba(138, 180, 255, 0.18);
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
            background: ${aura.accentSoft};
            border: 1px solid rgba(138, 180, 255, 0.28);
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
            background: rgba(34, 39, 49, 0.76);
            border: 1px solid ${aura.border};
            backdrop-filter: ${aura.backdrop};
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
            grid-template-columns: repeat(auto-fit, minmax(146px, 1fr));
            gap: 10px;
          }
          .ai-logic-metric {
            min-height: 88px;
            padding: 12px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.04);
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
            background: rgba(255, 255, 255, 0.04);
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
            flex-wrap: wrap;
            gap: 16px;
            padding: 13px 14px;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.035);
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
            background: ${aura.accentSoft};
            border: 1px solid rgba(138, 180, 255, 0.16);
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
            font-size: 24px;
            line-height: 1.2;
            font-weight: 600;
          }
          .ai-aura-copy {
            color: ${aura.muted};
            line-height: 1.55;
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
            grid-template-columns: repeat(auto-fit, minmax(146px, 1fr));
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
          @keyframes aiStatusSweep {
            from {
              background-position: -120% 0;
            }
            to {
              background-position: 220% 0;
            }
          }
          @keyframes aiSidePanelIn {
            from {
              opacity: 0;
              transform: translateX(24px);
            }
            to {
              opacity: 1;
              transform: translateX(0);
            }
          }
          @keyframes aiTerminalFocus {
            0% {
              box-shadow: inset 0 0 0 1px rgba(138, 180, 255, 0), 0 0 0 rgba(138, 180, 255, 0);
            }
            48% {
              box-shadow: inset 0 0 0 1px rgba(138, 180, 255, 0.22), 0 0 22px rgba(138, 180, 255, 0.08);
            }
            100% {
              box-shadow: inset 0 0 0 1px rgba(138, 180, 255, 0.04), 0 0 0 rgba(138, 180, 255, 0);
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
              box-shadow: 0 0 0 rgba(138, 180, 255, 0);
            }
            50% {
              box-shadow: 0 0 18px rgba(138, 180, 255, 0.22);
            }
          }
          @media (max-width: 1280px) {
            .ai-collect-body {
              grid-template-columns: minmax(204px, 232px) minmax(0, 1fr);
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
            .ai-session-template-frame {
              width: min(100%, 850px);
              padding-left: 76px;
            }
            .ai-session-stage-card {
              width: 252px;
            }
            .ai-stage-overview {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .ai-runtime-list {
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }
          }
          @media (max-width: 1140px) {
            .ai-session-fixed-meta {
              grid-template-columns: 1fr;
            }
            .ai-session-status-line {
              margin-top: 2px;
            }
            .ai-session-side-trigger,
            .ai-session-side-panel {
              display: none;
            }
          }
          @media (max-width: 767px) {
            .ai-collect-workbench {
              --ai-session-prompt-bottom: 12px;
              --ai-session-prompt-height: 86px;
              --ai-session-runtime-safe-bottom: 92px;
              --ai-session-body-safe-bottom: 12px;
              --ai-session-veil-height: 132px;
              --ai-session-dock-rail-bottom: 92px;
              --ai-session-dock-panel-bottom: 166px;
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
              padding: 4px 0 0;
            }
            .ai-session-shell {
              width: 100%;
            }
            .ai-session-template-frame {
              padding-left: 0;
            }
            .ai-session-stage-float {
              display: none;
            }
            .ai-session-template-shell {
              gap: 12px;
            }
            .ai-session-fixed-meta {
              gap: 12px;
              padding: 14px 16px;
              border-radius: 14px;
            }
            .ai-session-status-line {
              height: 40px;
              grid-template-columns: 18px minmax(0, 1fr);
              padding-right: 10px;
            }
            .ai-session-status-meta {
              display: none;
            }
            .ai-session-fixed-title-row {
              flex-direction: column;
              align-items: flex-start;
              gap: 8px;
            }
            .ai-session-fixed-title-row h2 {
              font-size: 24px;
            }
            .ai-session-template-scroll {
              width: 100%;
              padding-top: 2px;
            }
            .ai-template-sheet {
              padding: 20px 16px 20px;
              border-radius: 14px;
            }
            .ai-template-field {
              grid-template-columns: 1fr;
              gap: 10px;
              padding-left: 0;
            }
            .ai-session-template-divider {
              width: calc(100% - 40px);
              margin-bottom: calc(var(--ai-session-prompt-height) + 30px);
            }
            .ai-session-prompt {
              bottom: var(--ai-session-prompt-bottom);
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
            .ai-stage-headline {
              flex-direction: column;
              align-items: flex-start;
            }
            .ai-stage-top,
            .ai-stage-content,
            .ai-runtime-dock {
              padding-left: 16px;
              padding-right: 16px;
            }
            .ai-stage-overview {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .ai-step-strip-track {
              grid-template-columns: 1fr;
            }
            .ai-step-strip-head {
              flex-direction: column;
              align-items: stretch;
            }
            .ai-step-strip-meta {
              display: flex;
              justify-content: flex-start;
            }
            .ai-runtime-list {
              grid-template-columns: 1fr;
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
            <div className="ai-session-shell">
              {renderSessionLayout()}
              {false && (
              <main className="ai-collect-panel ai-stage-shell ai-stage-shell-full" style={panelStyle}>
                <div className="ai-stage-top">
                  <div className="ai-stage-headline">
                    <div className="ai-stage-headline-copy">
                      <div>
                        <Text className="ai-aura-kicker">当前阶段</Text>
                        <Text strong className="ai-panel-title">{processStepMeta[activeProcessStep].title}</Text>
                      </div>
                      <Text className="ai-stage-subtitle">{processStepMeta[activeProcessStep].desc}</Text>
                    </div>
                    {renderRunActions()}
                  </div>
                  {renderStageOverview()}
                  {renderStepNavigator()}
                </div>
                <div className="ai-stage-content">
                  {streamError && <Alert type="warning" showIcon message={streamError} style={{ marginBottom: 12 }} />}
                  {renderStageContent()}
                </div>
                {renderGuidancePanel()}
              </main>
              )}
            </div>
          )}
        </div>
        <div className={`ai-session-bottom-veil ${hasSession ? 'is-visible' : ''}`} aria-hidden="true" />
        <WorkspaceDock
          activePanel={activeWorkspacePanel}
          sessionActive={hasSession}
          onToggle={handleWorkspacePanelToggle}
          onClose={handleWorkspacePanelClose}
        />
        {hasSession ? renderDockedPrompt() : null}
      </div>
    </ErrorBoundary>
  );
};

export default AICollect;

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Segmented, Select, Switch, Tooltip, Typography, Upload } from 'antd';
import {
  BellOutlined,
  CaretRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CloseOutlined,
  CodeOutlined,
  DeploymentUnitOutlined,
  DownloadOutlined,
  EditOutlined,
  ExperimentOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  PushpinOutlined,
  RadarChartOutlined,
  ReadOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
  UploadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  createWorkspaceTask,
  fetchWorkspaceTasks,
  fetchWorkspaceTemplates,
  runWorkspaceTaskAction,
  updateWorkspaceTemplate,
  type WorkspaceTask,
  type WorkspaceTemplate,
} from '@/services/aiApi';
import workspacePalette from './palette';
import { ReleaseArchiveIcon, ReleaseDraftIcon } from './releaseIcons';
import type {
  CollectTask,
  TaskStatus,
  TemplateAsset,
  TemplateStatus,
} from '@/pages/CollectConsole/shared/types';

const { Text } = Typography;
const { TextArea } = Input;
const aura = workspacePalette;
const defaultPageSize = 10;
const listLoadThreshold = 56;

export type WorkspacePanel = 'templates' | 'tasks';

interface WorkspaceDockProps {
  activePanel: WorkspacePanel | null;
  sessionActive?: boolean;
  onToggle: (panel: WorkspacePanel) => void;
  onClose: () => void;
  analysisTemplate?: { yaml: string; adapter: string };
  onTemplateApply?: (draft: { yaml: string; adapter: string }) => void;
  releaseTaskDefaults?: {
    concurrency: number;
    respectRobots: boolean;
    driftGuard: boolean;
    params: Array<{ name: string; description: string; defaultValue: string; required: boolean }>;
    batch?: { paramName: string; batchSize: string; startLine: string; limit: string; delay: string } | null;
  };
}

type TemplateFilter = 'all' | TemplateStatus;
type TaskFilter = 'all' | TaskStatus;
type TemplateDetailMode = 'overview' | 'edit';
type TaskComposerMode = 'once' | 'recurring';
type TaskRecurringMode = 'daily' | 'interval';
type TaskIncrementalMode = 'time_window' | 'stop_condition';
type TaskIntervalUnit = 'minute' | 'hour';
type TaskLookbackUnit = 'hour' | 'day';
type TaskLogLevel = 'info' | 'ok' | 'warn';
type SiteKind = 'news' | 'patent' | 'intelligence' | 'warning' | 'signal' | 'generic';

interface TemplateDraft {
  adapter: string;
  outputTag: string;
  notes: string;
  yaml: string;
  savedAt: string;
}

interface TaskLog {
  time: string;
  level: TaskLogLevel;
  message: string;
}

interface TaskRuntimeItem {
  status: TaskStatus;
  progress: number;
  recordsValue: number;
  throughput: number;
  lastDelta: number;
  history: number[];
  logs: TaskLog[];
  controlState: 'canceled' | null;
  downloadState: 'idle' | 'running' | 'paused';
  syncState: 'idle' | 'running' | 'canceled';
}

interface TaskRow extends CollectTask {
  runtime: TaskRuntimeItem;
  site: SiteProfile;
  display: {
    label: string;
    color: string;
    icon: React.ReactNode;
    isRunning: boolean;
  };
}

interface SiteProfile {
  kind: SiteKind;
  brand: string;
  logo: string;
  hue: string;
  faviconUrl?: string;
}

interface TaskTemplateParameterDraft {
  key: string;
  label: string;
  value: string;
  placeholder: string;
}

interface TaskComposerDraft {
  name: string;
  template: string;
  templateLocked: boolean;
  scheduleMode: TaskComposerMode;
  recurringMode: TaskRecurringMode;
  dailyTime: string;
  intervalValue: number;
  intervalUnit: TaskIntervalUnit;
  templateParams: TaskTemplateParameterDraft[];
  incremental: boolean;
  incrementalMode: TaskIncrementalMode;
  incrementalField: string;
  lookbackValue: number;
  lookbackUnit: TaskLookbackUnit;
  overlapMinutes: number;
  stopField: string;
  stopComparator: '<' | '<=';
  stopThreshold: string;
  stopConsecutivePages: number;
  maxEmptyPages: number;
}

const templateStatusMeta: Record<TemplateStatus, { label: string; color: string }> = {
  active: { label: 'Active', color: '#31D26B' },
  draft: { label: 'Draft', color: '#FBBF24' },
  deprecated: { label: 'Archived', color: aura.subtle },
};

const extractListFields = (yaml: string) => {
  const lines = yaml.replace(/\r\n/g, '\n').split('\n');
  const rootIndex = lines.findIndex((line) => /^\s*list_fields\s*:/.test(line));
  if (rootIndex < 0) return [];

  const rootLine = lines[rootIndex];
  const inlineValue = rootLine.replace(/^\s*list_fields\s*:\s*/, '').trim();
  if (inlineValue.startsWith('[') && inlineValue.endsWith(']')) {
    return inlineValue.slice(1, -1).split(',').map((item) => item.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  }

  const rootIndent = rootLine.match(/^\s*/)?.[0].length ?? 0;
  const fields: string[] = [];
  for (let index = rootIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) continue;
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (indent <= rootIndent) break;

    const listMatch = line.trim().match(/^-\s*(?:name\s*:\s*)?([^:#]+?)(?:\s*:)?$/);
    const mapMatch = indent === rootIndent + 2 ? line.trim().match(/^([^:#]+):/) : null;
    const value = (listMatch?.[1] ?? mapMatch?.[1] ?? '').trim().replace(/^['"]|['"]$/g, '');
    if (value && !fields.includes(value)) fields.push(value);
  }
  return fields;
};

const extractTemplateDataType = (yaml: string) => (
  yaml.match(/^\s*data_type\s*:\s*['"]?([^'"\r\n#]+)['"]?/m)?.[1]?.trim() || 'other'
);

const taskComposerModeMeta: Record<TaskComposerMode, { label: string }> = {
  once: { label: '一次性任务' },
  recurring: { label: '周期任务' },
};

const recurringModeMeta: Record<TaskRecurringMode, { label: string }> = {
  daily: { label: '每天几点' },
  interval: { label: '每隔多久' },
};

const intervalUnitMeta: Array<{ value: TaskIntervalUnit; label: string }> = [
  { value: 'minute', label: '分钟' },
  { value: 'hour', label: '小时' },
];

const lookbackUnitMeta: Array<{ value: TaskLookbackUnit; label: string }> = [
  { value: 'hour', label: '小时' },
  { value: 'day', label: '天' },
];

const formatDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

const formatTemplateDomain = (value: string) => ({
  '政务公告': 'Government notices',
  'PDF 附件': 'PDF attachments',
  '质量校验': 'Quality checks',
  '历史公告': 'Legacy notices',
}[value] ?? value);

const formatTaskNextRunLabel = (value: string) => ({
  '持续运行': 'Continuous',
  '等待恢复': 'Awaiting recovery',
  '人工确认': 'Manual review',
}[value] ?? value);

const incrementalModeMeta: Record<TaskIncrementalMode, { label: string }> = {
  time_window: { label: '时间范围过滤' },
  stop_condition: { label: '阈值停止规则' },
};

const siteKindMeta: Record<SiteKind, { icon: React.ReactNode; label: string; tint: string }> = {
  news: { icon: <ReadOutlined />, label: '新闻', tint: '#BFA8FF' },
  patent: { icon: <ExperimentOutlined />, label: '专利', tint: '#8AB4FF' },
  intelligence: { icon: <RadarChartOutlined />, label: '情报', tint: '#7DD3FC' },
  warning: { icon: <BellOutlined />, label: '告警', tint: '#F6C35B' },
  signal: { icon: <DeploymentUnitOutlined />, label: '信号', tint: '#65D5A3' },
  generic: { icon: <CodeOutlined />, label: '通用', tint: '#A0AEC0' },
};

const siteProfileRegistry: Record<string, SiteProfile> = {
  google_patent_contract: { kind: 'patent', brand: 'Google Patent', logo: 'GP', hue: '#7BA8FF' },
  sealagom_navwarn_contract: { kind: 'warning', brand: 'Navwarn', logo: 'NW', hue: '#66D5A3' },
  zdopen_notice_contract: { kind: 'news', brand: 'ZD Open', logo: 'ZD', hue: '#B08CFF' },
  pdf_document_extract: { kind: 'intelligence', brand: 'PDF Source', logo: 'PD', hue: '#7DD3FC' },
  quality_missing_scan: { kind: 'signal', brand: 'Quality Gate', logo: 'QG', hue: '#62D6C8' },
  legacy_notice_parser: { kind: 'news', brand: 'Legacy Notice', logo: 'LN', hue: '#94A3B8' },
  market_data_contract: { kind: 'intelligence', brand: 'Market Data', logo: 'MD', hue: '#5FA8FF' },
};

const parseCompactNumber = (value: string) => {
  const normalized = value.trim().toUpperCase();
  if (normalized === '-' || normalized === 'BLOCKED' || normalized === 'MANUAL') return 0;
  if (normalized.endsWith('K')) return Math.round(Number.parseFloat(normalized) * 1000);
  if (normalized.endsWith('M')) return Math.round(Number.parseFloat(normalized) * 1000 * 1000);
  return Number.parseInt(normalized.replace(/,/g, ''), 10) || 0;
};

const formatCompactNumber = (value: number) => {
  if (value >= 1000 * 1000) return `${(value / (1000 * 1000)).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`;
  return `${value}`;
};

const togglePinnedState = (prev: Record<string, true>, key: string) => {
  const next = { ...prev };
  if (next[key]) {
    delete next[key];
  } else {
    next[key] = true;
  }
  return next;
};

const normalizeTemplateKey = (value: string) => value.replace(/@.*$/, '');

const createHistory = (recordsValue: number) => {
  const span = Math.max(420, Math.round(recordsValue * 0.12));
  const start = Math.max(recordsValue - span, 0);
  return Array.from({ length: 12 }, (_, index) => Math.round(start + ((recordsValue - start) * (index + 1)) / 12));
};

const buildInitialTaskLogs = (item: CollectTask): TaskLog[] => [
  {
    time: '09:12:04',
    level: item.status === 'failed' ? 'warn' : 'ok',
    message: item.comments[0] ?? '任务已接入运行面板',
  },
  {
    time: '09:12:36',
    level: 'info',
    message: `template loaded: ${item.template}`,
  },
  {
    time: '09:13:08',
    level: item.status === 'running' ? 'ok' : 'info',
    message: `${item.area} 链路${item.status === 'running' ? '持续采集' : '等待调度'}`,
  },
];

const buildTaskRuntimeItem = (item: CollectTask, index: number): TaskRuntimeItem => {
  const recordsValue = parseCompactNumber(item.records);
  return {
    status: item.status,
    progress: item.progress,
    recordsValue,
    throughput: item.status === 'running' ? 18 + index * 4 : item.status === 'queued' ? 0 : 12,
    lastDelta: item.status === 'running' ? 96 + index * 14 : 0,
    history: createHistory(recordsValue),
    logs: buildInitialTaskLogs(item),
    controlState: null,
    downloadState: item.status === 'running' ? 'running' : item.status === 'completed' ? 'paused' : 'idle',
    syncState: item.status === 'running' || item.status === 'completed' ? 'running' : 'idle',
  };
};

const mapWorkspaceTemplate = (item: WorkspaceTemplate): TemplateAsset => ({
  key: item.id,
  name: item.name,
  title: item.title,
  domain: item.domain,
  adapter: item.adapter,
  version: item.version,
  status: item.status,
  fields: extractListFields(item.yaml_content).length,
  quality: Number(item.metadata?.quality ?? 0),
  lastRun: item.updated_at,
  owner: item.owner,
  description: item.description,
  action: 'Open template',
  icon: 'code',
  taskCount: item.task_count,
  faviconUrl: item.favicon_url,
  dataType: extractTemplateDataType(item.yaml_content),
});

const mapWorkspaceTask = (item: WorkspaceTask): CollectTask => ({
  key: item.id,
  name: item.name,
  template: `${item.template_name}@${item.template_version}`,
  group: 'prototype',
  area: `${item.template_name.replace(/_/g, ' ')} workspace`,
  status: item.status,
  progress: item.progress,
  records: String(item.records),
  lag: item.status === 'running' ? 'live' : '-',
  nextRun: String(item.schedule?.label ?? (item.status === 'running' ? 'Continuous' : 'Waiting')),
  owner: item.owner,
  avatar: toAvatarLabel(item.owner),
  comments: [],
  subIssues: [],
});

const mapWorkspaceTaskRuntime = (item: WorkspaceTask): TaskRuntimeItem => ({
  status: item.status,
  progress: item.progress,
  recordsValue: item.records,
  throughput: item.throughput,
  lastDelta: 0,
  history: createHistory(item.records),
  logs: item.logs.map((log) => ({
    time: formatDateTime(log.created_at),
    level: log.level,
    message: log.message,
  })),
  controlState: item.control_state,
  downloadState: item.download_state,
  syncState: item.sync_state,
});

const toAvatarLabel = (value: string) => {
  const initials = value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
    .slice(0, 2);
  return initials || 'AI';
};

const inferSiteKind = (value: string): SiteKind => {
  const text = value.toLowerCase();
  if (text.includes('patent') || text.includes('专利')) return 'patent';
  if (text.includes('warn') || text.includes('warning') || text.includes('告警')) return 'warning';
  if (text.includes('notice') || text.includes('news') || text.includes('公告') || text.includes('新闻')) return 'news';
  if (text.includes('signal') || text.includes('quality') || text.includes('质量')) return 'signal';
  if (text.includes('market') || text.includes('intel') || text.includes('情报') || text.includes('pdf') || text.includes('document')) return 'intelligence';
  return 'generic';
};

const deriveBrandFromKey = (value: string) => {
  const normalized = value
    .replace(/@.*$/, '')
    .split(/[_\-\s]+/)
    .filter(Boolean);
  const words = normalized.slice(0, 2).map((part) => part.charAt(0).toUpperCase() + part.slice(1));
  const brand = words.join(' ') || 'Source';
  const logo = words.map((part) => part[0]).join('').slice(0, 2).toUpperCase() || 'SC';
  return { brand, logo };
};

const resolveSiteProfile = (value: string): SiteProfile => {
  const normalized = value.replace(/@.*$/, '');
  const exact = siteProfileRegistry[normalized];
  if (exact) return exact;

  const kind = inferSiteKind(value);
  const derived = deriveBrandFromKey(normalized);
  return {
    kind,
    brand: derived.brand,
    logo: derived.logo,
    hue: siteKindMeta[kind].tint,
  };
};

const taskTemplateParameterBlueprints: Record<SiteKind, Array<{ key: string; label: string; placeholder: string }>> = {
  news: [
    { key: 'keyword', label: '关键词', placeholder: '例如：satellite / policy / tender' },
    { key: 'page_limit', label: '抓取页数', placeholder: '例如：20' },
    { key: 'category', label: '栏目过滤', placeholder: '例如：最新公告' },
  ],
  patent: [
    { key: 'query', label: '检索式', placeholder: '例如：autonomous navigation' },
    { key: 'assignee', label: '申请人', placeholder: '例如：OpenAI' },
    { key: 'page_limit', label: '抓取页数', placeholder: '例如：50' },
  ],
  intelligence: [
    { key: 'dataset', label: '数据主题', placeholder: '例如：market intelligence' },
    { key: 'region', label: '区域范围', placeholder: '例如：global' },
    { key: 'page_limit', label: '抓取页数', placeholder: '例如：30' },
  ],
  warning: [
    { key: 'region', label: '海域/区域', placeholder: '例如：South China Sea' },
    { key: 'notice_type', label: '通告类型', placeholder: '例如：navwarn' },
    { key: 'page_limit', label: '抓取页数', placeholder: '例如：10' },
  ],
  signal: [
    { key: 'check_scope', label: '校验范围', placeholder: '例如：abstract / attachment' },
    { key: 'sample_limit', label: '抽样量', placeholder: '例如：200' },
    { key: 'threshold', label: '质量阈值', placeholder: '例如：0.95' },
  ],
  generic: [
    { key: 'keyword', label: '关键词', placeholder: '例如：policy update' },
    { key: 'page_limit', label: '抓取页数', placeholder: '例如：20' },
    { key: 'detail_limit', label: '详情上限', placeholder: '例如：200' },
  ],
};

const defaultIncrementalFields = [
  'publish_time',
  'updated_at',
  'notice_time',
  'filing_date',
  'created_at',
  'record_id',
] as const;

const defaultStopFields = [
  'publish_time',
  'record_id',
  'priority_score',
  'sort_value',
  'page_cursor',
] as const;

const taskComposerFieldLabels: Record<string, string> = {
  keyword: 'Keyword',
  page_limit: 'Page Limit',
  category: 'Category',
  query: 'Query',
  assignee: 'Assignee',
  dataset: 'Dataset',
  region: 'Region',
  notice_type: 'Notice Type',
  check_scope: 'Check Scope',
  sample_limit: 'Sample Limit',
  threshold: 'Threshold',
  detail_limit: 'Detail Limit',
};

const taskComposerFieldPlaceholders: Record<string, string> = {
  keyword: 'satellite / policy / tender',
  page_limit: '50',
  category: 'latest notice',
  query: 'autonomous navigation',
  assignee: 'OpenAI',
  dataset: 'market intelligence',
  region: 'global',
  notice_type: 'navwarn',
  check_scope: 'abstract / attachment',
  sample_limit: '100',
  threshold: '0.95',
  detail_limit: '100',
};

const buildTaskTemplateParameterDrafts = (templateValue: string) => {
  const kind = resolveSiteProfile(templateValue).kind;
  return taskTemplateParameterBlueprints[kind].map((item) => ({
    ...item,
    value: '',
  }));
};

const inferIncrementalField = (templateValue: string) => {
  const normalized = templateValue.toLowerCase();
  if (normalized.includes('patent')) return 'filing_date';
  if (normalized.includes('warn')) return 'notice_time';
  if (normalized.includes('market')) return 'updated_at';
  return 'publish_time';
};

const formatTaskNextRun = (draft: TaskComposerDraft) => {
  if (draft.scheduleMode === 'once') return '待手动启动';
  if (draft.recurringMode === 'daily') return `每天 ${draft.dailyTime}`;
  return `每 ${draft.intervalValue} ${draft.intervalUnit === 'minute' ? '分钟' : '小时'}`;
};

const formatIncrementalSummary = (draft: TaskComposerDraft) => {
  if (!draft.incremental) return '全量采集';
  if (draft.incrementalMode === 'time_window') {
    return `${draft.incrementalField} 回看 ${draft.lookbackValue}${draft.lookbackUnit === 'hour' ? '小时' : '天'}，重叠 ${draft.overlapMinutes} 分钟`;
  }
  return `${draft.stopField} ${draft.stopComparator} ${draft.stopThreshold} 时停止，连续命中 ${draft.stopConsecutivePages} 页`;
};

const stripDecorativeSuffix = (value: string) => value.replace(/\s+[^\u4E00-\u9FFFa-zA-Z0-9]+$/g, '');

const TemplateGlyph: React.FC<{ kind: SiteKind; className?: string }> = ({ kind, className }) => (
  <span
    className={`workspace-glyph ${className ?? ''}`}
    aria-hidden="true"
    style={{ color: siteKindMeta[kind].tint }}
  >
    {siteKindMeta[kind].icon}
  </span>
);

const TaskPulseGlyph: React.FC<{ kind: SiteKind; active?: boolean; className?: string }> = ({ kind, active = false, className }) => (
  <span
    className={`workspace-glyph workspace-task-glyph ${active ? 'is-active' : ''} ${className ?? ''}`}
    aria-hidden="true"
    style={{ color: siteKindMeta[kind].tint }}
  >
    {siteKindMeta[kind].icon}
    <i />
  </span>
);

const SiteLogoMark: React.FC<{ site: SiteProfile; faviconUrl?: string }> = ({ site, faviconUrl }) => {
  const [failed, setFailed] = useState(false);
  const source = faviconUrl || site.faviconUrl;
  return (
    <i className="workspace-dock-meta-logo" style={{ '--brand-hue': site.hue } as React.CSSProperties} aria-hidden="true">
      {source && !failed ? <img src={source} alt="" referrerPolicy="no-referrer" onError={() => setFailed(true)} /> : site.logo}
    </i>
  );
};

const getTaskDisplay = (runtime: TaskRuntimeItem) => {
  if (runtime.controlState === 'canceled') {
    return {
      label: 'Canceled',
      color: 'rgba(255, 255, 255, 0.48)',
      icon: <CloseCircleOutlined />,
      isRunning: false,
    };
  }

  switch (runtime.status) {
    case 'running':
      return { label: 'Running', color: aura.accent, icon: <SyncOutlined spin />, isRunning: true };
    case 'queued':
      return { label: 'Queued', color: '#FBBF24', icon: <ClockCircleOutlined />, isRunning: false };
    case 'completed':
      return { label: 'Completed', color: '#31D26B', icon: <CheckCircleOutlined />, isRunning: false };
    case 'failed':
      return { label: 'Failed', color: '#F87171', icon: <WarningOutlined />, isRunning: false };
    case 'paused':
    default:
      return { label: 'Paused', color: '#FBBF24', icon: <PauseCircleOutlined />, isRunning: false };
  }
};

const WorkspaceDock: React.FC<WorkspaceDockProps> = ({
  activePanel,
  sessionActive = false,
  onToggle,
  onClose,
  analysisTemplate,
  onTemplateApply,
  releaseTaskDefaults,
}) => {
  const bodyScrollRef = useRef<HTMLDivElement>(null);
  const templateSaveTimerRef = useRef<number | null>(null);
  const [templates, setTemplates] = useState<TemplateAsset[]>([]);
  const [keyword, setKeyword] = useState('');
  const [templateFilter, setTemplateFilter] = useState<TemplateFilter>('all');
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('all');
  const [taskTemplateFilter, setTaskTemplateFilter] = useState<string | null>(null);
  const [templateDetailMode, setTemplateDetailMode] = useState<TemplateDetailMode>('overview');
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string | null>(null);
  const [selectedTaskKey, setSelectedTaskKey] = useState<string | null>(null);
  const [templateVisibleCount, setTemplateVisibleCount] = useState(defaultPageSize);
  const [taskVisibleCount, setTaskVisibleCount] = useState(defaultPageSize);
  const [pinnedTemplateKeys, setPinnedTemplateKeys] = useState<Record<string, true>>({});
  const [pinnedTaskKeys, setPinnedTaskKeys] = useState<Record<string, true>>({});
  const [taskComposerOpen, setTaskComposerOpen] = useState(false);
  const [taskComposerDraft, setTaskComposerDraft] = useState<TaskComposerDraft>({
    name: '',
    template: '',
    templateLocked: false,
    scheduleMode: 'once',
    recurringMode: 'daily',
    dailyTime: '09:00',
    intervalValue: 30,
    intervalUnit: 'minute',
    templateParams: [],
    incremental: false,
    incrementalMode: 'time_window',
    incrementalField: 'publish_time',
    lookbackValue: 6,
    lookbackUnit: 'hour',
    overlapMinutes: 15,
    stopField: 'publish_time',
    stopComparator: '<=',
    stopThreshold: '7d',
    stopConsecutivePages: 2,
    maxEmptyPages: 2,
  });
  const [taskConcurrency, setTaskConcurrency] = useState(releaseTaskDefaults?.concurrency ?? 4);
  const [taskRespectRobots, setTaskRespectRobots] = useState(releaseTaskDefaults?.respectRobots ?? true);
  const [taskDriftGuard, setTaskDriftGuard] = useState(releaseTaskDefaults?.driftGuard ?? true);
  const [taskBatchInput, setTaskBatchInput] = useState(false);
  const [taskBatchFile, setTaskBatchFile] = useState('');
  const [taskBatchParam, setTaskBatchParam] = useState(releaseTaskDefaults?.batch?.paramName ?? '');
  const [taskBatchSize, setTaskBatchSize] = useState(Number(releaseTaskDefaults?.batch?.batchSize) || 1);
  const [taskBatchStartLine, setTaskBatchStartLine] = useState(Number(releaseTaskDefaults?.batch?.startLine) || 0);
  const [taskBatchLimit, setTaskBatchLimit] = useState<number | null>(() => {
    const limit = Number(releaseTaskDefaults?.batch?.limit);
    return Number.isFinite(limit) && limit > 0 ? limit : null;
  });
  const [taskBatchDelay, setTaskBatchDelay] = useState(Number(releaseTaskDefaults?.batch?.delay) || 0);
  const [bodyScrollState, setBodyScrollState] = useState({ canScroll: false, isAtBottom: true });

  useEffect(() => {
    if (!releaseTaskDefaults) return;
    setTaskConcurrency(releaseTaskDefaults.concurrency);
    setTaskRespectRobots(releaseTaskDefaults.respectRobots);
    setTaskDriftGuard(releaseTaskDefaults.driftGuard);
    if (releaseTaskDefaults.batch) {
      setTaskBatchParam(releaseTaskDefaults.batch.paramName);
      setTaskBatchSize(Number(releaseTaskDefaults.batch.batchSize) || 1);
      setTaskBatchStartLine(Number(releaseTaskDefaults.batch.startLine) || 0);
      const limit = Number(releaseTaskDefaults.batch.limit);
      setTaskBatchLimit(Number.isFinite(limit) && limit > 0 ? limit : null);
      setTaskBatchDelay(Number(releaseTaskDefaults.batch.delay) || 0);
    }
  }, [
    releaseTaskDefaults?.batch?.batchSize,
    releaseTaskDefaults?.batch?.delay,
    releaseTaskDefaults?.batch?.limit,
    releaseTaskDefaults?.batch?.paramName,
    releaseTaskDefaults?.batch?.startLine,
    releaseTaskDefaults?.concurrency,
    releaseTaskDefaults?.driftGuard,
    releaseTaskDefaults?.respectRobots,
  ]);

  const [templateDrafts, setTemplateDrafts] = useState<Record<string, TemplateDraft>>({});
  const [taskItems, setTaskItems] = useState<CollectTask[]>([]);
  const [taskRuntime, setTaskRuntime] = useState<Record<string, TaskRuntimeItem>>({});

  const applyWorkspaceTasks = useCallback((items: WorkspaceTask[]) => {
    setTaskItems(items.map(mapWorkspaceTask));
    setTaskRuntime(Object.fromEntries(items.map((item) => [item.id, mapWorkspaceTaskRuntime(item)])));
  }, []);

  const refreshWorkspaceTasks = useCallback(async () => {
    applyWorkspaceTasks(await fetchWorkspaceTasks());
  }, [applyWorkspaceTasks]);

  useEffect(() => {
    let active = true;
    Promise.all([fetchWorkspaceTemplates(), fetchWorkspaceTasks()])
      .then(([templateItems, taskItemsResponse]) => {
        if (!active) return;
        setTemplates(templateItems.map(mapWorkspaceTemplate));
        setTemplateDrafts(Object.fromEntries(templateItems.map((item) => [item.id, {
          adapter: item.adapter,
          outputTag: item.output_tag,
          notes: item.description,
          yaml: item.yaml_content,
          savedAt: item.updated_at,
        }])));
        applyWorkspaceTasks(taskItemsResponse);
      })
      .catch((error) => console.error('Failed to load AI Collect workspace', error));
    return () => {
      active = false;
    };
  }, [applyWorkspaceTasks]);

  useEffect(() => {
    if (activePanel !== 'tasks') return undefined;
    const timer = window.setInterval(() => {
      void refreshWorkspaceTasks().catch((error) => console.error('Failed to refresh tasks', error));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [activePanel, refreshWorkspaceTasks]);

  const templateRows = useMemo(() => templates
    .filter((item) => {
      const matchFilter = templateFilter === 'all' || item.status === templateFilter;
      const matchKeyword = !keyword
        || `${item.name} ${item.title} ${item.domain} ${item.adapter}`.toLowerCase().includes(keyword.toLowerCase());
      return matchFilter && matchKeyword;
    })
    .sort((left, right) => {
      const leftPinned = Boolean(pinnedTemplateKeys[left.key]);
      const rightPinned = Boolean(pinnedTemplateKeys[right.key]);
      if (leftPinned === rightPinned) return 0;
      return leftPinned ? -1 : 1;
    }), [keyword, pinnedTemplateKeys, templateFilter, templates]);

  const allTaskRows = useMemo<TaskRow[]>(() => taskItems.map((item, index) => {
    const runtime = taskRuntime[item.key] ?? buildTaskRuntimeItem(item, index);
    const site = resolveSiteProfile(item.template);
    const template = templates.find((candidate) => normalizeTemplateKey(candidate.name) === normalizeTemplateKey(item.template));
    return {
      ...item,
      runtime,
      site: template ? { ...site, kind: inferSiteKind(template.dataType), faviconUrl: template.faviconUrl } : site,
      display: getTaskDisplay(runtime),
    };
  }), [taskItems, taskRuntime, templates]);

  const taskRows = useMemo(() => allTaskRows
    .filter((item) => {
      const matchFilter = taskFilter === 'all' || item.runtime.status === taskFilter;
      const matchTemplate = !taskTemplateFilter || normalizeTemplateKey(item.template) === taskTemplateFilter;
      const matchKeyword = !keyword
        || `${item.name} ${item.template} ${item.area} ${item.owner}`.toLowerCase().includes(keyword.toLowerCase());
      return matchFilter && matchTemplate && matchKeyword;
    })
    .sort((left, right) => {
      const leftPinned = Boolean(pinnedTaskKeys[left.key]);
      const rightPinned = Boolean(pinnedTaskKeys[right.key]);
      if (leftPinned === rightPinned) return 0;
      return leftPinned ? -1 : 1;
    }), [allTaskRows, keyword, pinnedTaskKeys, taskFilter, taskTemplateFilter]);

  useEffect(() => {
    setTemplateVisibleCount(defaultPageSize);
  }, [keyword, templateFilter]);

  useEffect(() => {
    setTaskVisibleCount(defaultPageSize);
  }, [keyword, taskFilter, taskTemplateFilter]);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.key === selectedTemplateKey) ?? null,
    [selectedTemplateKey, templates],
  );
  const taskTemplateOptions = useMemo(
    () => templates
      .filter((item) => item.status !== 'deprecated')
      .map((item) => ({
        value: `${item.name}@${item.version}`,
        label: `${item.name}@${item.version}`,
        title: item.title,
        version: item.version,
        site: resolveSiteProfile(item.name),
      })),
    [templates],
  );
  const selectedTask = useMemo(
    () => allTaskRows.find((item) => item.key === selectedTaskKey) ?? null,
    [allTaskRows, selectedTaskKey],
  );
  const selectedTemplateDraft = selectedTemplate ? templateDrafts[selectedTemplate.key] : null;
  const templateTaskCounts = useMemo<Record<string, number>>(() => Object.fromEntries(
    templates.map((item) => [item.key, taskItems.filter(
      (taskItem) => normalizeTemplateKey(taskItem.template) === normalizeTemplateKey(item.name),
    ).length]),
  ) as Record<string, number>, [taskItems, templates]);
  const visibleTemplateRows = useMemo(
    () => templateRows.slice(0, templateVisibleCount),
    [templateRows, templateVisibleCount],
  );
  const visibleTaskRows = useMemo(
    () => taskRows.slice(0, taskVisibleCount),
    [taskRows, taskVisibleCount],
  );
  const templateHasMore = visibleTemplateRows.length < templateRows.length;
  const taskHasMore = visibleTaskRows.length < taskRows.length;
  const showBodyFade = bodyScrollState.canScroll && !bodyScrollState.isAtBottom;

  const loadMoreRows = useCallback(() => {
    if (activePanel === 'templates' && templateHasMore) {
      setTemplateVisibleCount((current) => Math.min(current + defaultPageSize, templateRows.length));
    }
    if (activePanel === 'tasks' && taskHasMore) {
      setTaskVisibleCount((current) => Math.min(current + defaultPageSize, taskRows.length));
    }
  }, [activePanel, taskHasMore, taskRows.length, templateHasMore, templateRows.length]);

  const syncBodyScrollState = useCallback(() => {
    const container = bodyScrollRef.current;
    if (!container) {
      setBodyScrollState({ canScroll: false, isAtBottom: true });
      return;
    }

    const canScroll = container.scrollHeight - container.clientHeight > 6;
    const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight <= 18;
    setBodyScrollState((current) => (
      current.canScroll === canScroll && current.isAtBottom === isAtBottom
        ? current
        : { canScroll, isAtBottom }
    ));
  }, []);

  useEffect(() => {
    const container = bodyScrollRef.current;
    if (!container) return undefined;

    const handleScroll = () => {
      if (container.scrollHeight - container.scrollTop - container.clientHeight <= listLoadThreshold) {
        loadMoreRows();
      }
      window.requestAnimationFrame(syncBodyScrollState);
    };

    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => window.requestAnimationFrame(syncBodyScrollState))
      : null;

    observer?.observe(container);
    if (container.firstElementChild) observer?.observe(container.firstElementChild);
    container.addEventListener('scroll', handleScroll, { passive: true });
    window.requestAnimationFrame(handleScroll);

    return () => {
      observer?.disconnect();
      container.removeEventListener('scroll', handleScroll);
    };
  }, [activePanel, loadMoreRows, syncBodyScrollState, visibleTaskRows.length, visibleTemplateRows.length]);

  const hasDetail = activePanel === 'templates'
    ? Boolean(selectedTemplate) || taskComposerOpen
    : activePanel === 'tasks'
      ? Boolean(selectedTask)
      : false;

  const taskInsertedLines = selectedTask ? Math.max(selectedTask.runtime.lastDelta, 24) : 0;
  const taskUpdatedLines = selectedTask ? Math.max(Math.round(selectedTask.runtime.lastDelta * 0.42), 12) : 0;
  const taskDeletedLines = selectedTask ? Math.max(Math.round(selectedTask.runtime.lastDelta * 0.18), selectedTask.runtime.controlState === 'canceled' ? 11 : 5) : 0;
  const taskDownloadedResources = selectedTask
    ? Math.max(
      Math.round(selectedTask.runtime.recordsValue / 72),
      selectedTask.runtime.downloadState === 'running' ? 12 : selectedTask.runtime.downloadState === 'paused' ? 7 : 0,
    )
    : 0;
  const taskSyncedRecords = selectedTask
    ? (selectedTask.runtime.syncState === 'canceled'
      ? Math.max(selectedTask.runtime.recordsValue - Math.round(selectedTask.runtime.lastDelta * 1.2), 0)
      : Math.max(selectedTask.runtime.recordsValue - Math.round(selectedTask.runtime.lastDelta * 0.24), 0))
    : 0;

  const updateTemplateDraft = (templateKey: string, patch: Partial<TemplateDraft>) => {
    setTemplateDrafts((prev) => ({
      ...prev,
      [templateKey]: {
        ...prev[templateKey],
        ...patch,
      },
    }));
    if (templateKey === selectedTemplateKey && onTemplateApply && ('yaml' in patch || 'adapter' in patch)) {
      const current = templateDrafts[templateKey];
      onTemplateApply({
        yaml: patch.yaml ?? analysisTemplate?.yaml ?? current.yaml,
        adapter: patch.adapter ?? analysisTemplate?.adapter ?? current.adapter,
      });
    }
  };

  useEffect(() => {
    if (templateDetailMode !== 'edit' || !selectedTemplate || !selectedTemplateDraft) return undefined;
    if (templateSaveTimerRef.current) window.clearTimeout(templateSaveTimerRef.current);
    templateSaveTimerRef.current = window.setTimeout(() => {
      void updateWorkspaceTemplate(selectedTemplate.key, {
        yaml_content: selectedTemplateDraft.yaml,
        adapter: selectedTemplateDraft.adapter,
        description: selectedTemplateDraft.notes,
        output_tag: selectedTemplateDraft.outputTag,
      }).then((updated) => {
        setTemplateDrafts((prev) => ({
          ...prev,
          [selectedTemplate.key]: { ...prev[selectedTemplate.key], savedAt: updated.updated_at },
        }));
      }).catch((error) => console.error('Failed to update template', error));
    }, 500);
    return () => {
      if (templateSaveTimerRef.current) window.clearTimeout(templateSaveTimerRef.current);
    };
  }, [
    selectedTemplate?.key,
    selectedTemplateDraft?.adapter,
    selectedTemplateDraft?.notes,
    selectedTemplateDraft?.outputTag,
    selectedTemplateDraft?.yaml,
    templateDetailMode,
  ]);

  const toggleTemplatePinned = useCallback((templateKey: string) => {
    setPinnedTemplateKeys((prev) => togglePinnedState(prev, templateKey));
  }, []);

  const toggleTaskPinned = useCallback((taskKey: string) => {
    setPinnedTaskKeys((prev) => togglePinnedState(prev, taskKey));
  }, []);

  const handleTemplatePinClick = useCallback((event: React.MouseEvent<HTMLElement>, templateKey: string) => {
    event.preventDefault();
    event.stopPropagation();
    toggleTemplatePinned(templateKey);
  }, [toggleTemplatePinned]);

  const handleTaskPinClick = useCallback((event: React.MouseEvent<HTMLElement>, taskKey: string) => {
    event.preventDefault();
    event.stopPropagation();
    toggleTaskPinned(taskKey);
  }, [toggleTaskPinned]);

  const openLinkedTasks = useCallback((event: React.SyntheticEvent<HTMLElement>, templateName: string) => {
    event.preventDefault();
    event.stopPropagation();
    setTaskTemplateFilter(normalizeTemplateKey(templateName));
    setTaskFilter('all');
    setKeyword('');
    setTaskVisibleCount(defaultPageSize);
    onToggle('tasks');
  }, [onToggle]);

  const buildTaskComposerDraft = useCallback((patch?: Partial<TaskComposerDraft>): TaskComposerDraft => {
    const fallbackTemplate = taskTemplateOptions[0]?.value ?? 'generic';
    const template = patch?.template ?? fallbackTemplate;
    const scheduleMode = patch?.scheduleMode ?? 'once';
    const defaultField = inferIncrementalField(template);
    return {
      name: '',
      template,
      templateLocked: false,
      scheduleMode,
      recurringMode: 'daily',
      dailyTime: '09:00',
      intervalValue: 30,
      intervalUnit: 'minute' as TaskIntervalUnit,
      templateParams: releaseTaskDefaults?.params.length
        ? releaseTaskDefaults.params.map((param) => ({
          key: param.name,
          label: `${param.name}${param.required ? ' *' : ''}`,
          value: param.defaultValue,
          placeholder: param.description || param.name,
        }))
        : buildTaskTemplateParameterDrafts(template),
      incremental: scheduleMode === 'recurring',
      incrementalMode: 'time_window' as TaskIncrementalMode,
      incrementalField: defaultField,
      lookbackValue: 6,
      lookbackUnit: 'hour' as TaskLookbackUnit,
      overlapMinutes: 15,
      stopField: defaultField,
      stopComparator: '<=' as const,
      stopThreshold: '7d',
      stopConsecutivePages: 2,
      maxEmptyPages: 2,
      ...patch,
    };
  }, [releaseTaskDefaults?.params, taskTemplateOptions]);

  const resetTaskComposer = useCallback((patch?: Partial<TaskComposerDraft>) => {
    setTaskComposerDraft(buildTaskComposerDraft(patch));
  }, [buildTaskComposerDraft]);

  const openTaskComposer = useCallback((patch?: Partial<TaskComposerDraft>) => {
    setSelectedTaskKey(null);
    setKeyword('');
    setTaskFilter('all');
    setTaskComposerOpen(true);
    resetTaskComposer(patch);
  }, [resetTaskComposer]);

  const closeTaskComposer = useCallback(() => {
    setTaskComposerOpen(false);
    resetTaskComposer();
  }, [resetTaskComposer]);

  const updateTaskComposerDraft = useCallback((patch: Partial<TaskComposerDraft>) => {
    setTaskComposerDraft((prev) => ({
      ...prev,
      ...patch,
    }));
  }, []);

  const updateTaskComposerTemplate = useCallback((templateValue: string, patch?: Partial<TaskComposerDraft>) => {
    setTaskComposerDraft((prev) => ({
      ...prev,
      template: templateValue,
      templateParams: releaseTaskDefaults?.params.length
        ? releaseTaskDefaults.params.map((param) => ({
          key: param.name,
          label: `${param.name}${param.required ? ' *' : ''}`,
          value: param.defaultValue,
          placeholder: param.description || param.name,
        }))
        : buildTaskTemplateParameterDrafts(templateValue),
      incrementalField: inferIncrementalField(templateValue),
      stopField: inferIncrementalField(templateValue),
      ...patch,
    }));
  }, [releaseTaskDefaults?.params]);

  const updateTaskComposerParameter = useCallback((index: number, value: string) => {
    setTaskComposerDraft((prev) => ({
      ...prev,
      templateParams: prev.templateParams.map((item, itemIndex) => (itemIndex === index ? { ...item, value } : item)),
    }));
  }, []);

  const handleTaskComposerModeChange = useCallback((scheduleMode: TaskComposerMode) => {
    setTaskComposerDraft((prev) => ({
      ...prev,
      scheduleMode,
      incremental: scheduleMode === 'recurring',
    }));
  }, []);

  const handleCreateTask = useCallback(async () => {
    const normalizedTemplate = taskComposerDraft.template
      || (selectedTemplate ? `${selectedTemplate.name}@${selectedTemplate.version}` : '')
      || taskTemplateOptions[0]?.value
      || '';
    const matchedTemplate = templates.find((item) => `${item.name}@${item.version}` === normalizedTemplate) ?? null;
    const fallbackName = normalizedTemplate
      ? `${normalizeTemplateKey(normalizedTemplate).replace(/_/g, ' ')} task`
      : 'New collect task';
    const nextTask: CollectTask = {
      key: `task-${Date.now()}`,
      name: taskComposerDraft.name.trim() || fallbackName,
      template: normalizedTemplate,
      group: 'prototype',
      area: matchedTemplate ? `${matchedTemplate.title} workspace` : selectedTemplate ? `${selectedTemplate.title} workspace` : 'Collect workspace',
      status: 'queued',
      progress: 0,
      records: '0',
      lag: '-',
      nextRun: formatTaskNextRun(taskComposerDraft),
      owner: matchedTemplate?.owner || 'AI Collect',
      avatar: toAvatarLabel(matchedTemplate?.owner || 'AI Collect'),
      comments: [
        `${taskComposerModeMeta[taskComposerDraft.scheduleMode].label} / ${formatIncrementalSummary(taskComposerDraft)}`,
        `concurrency=${taskConcurrency}; robots=${taskRespectRobots}; drift_guard=${taskDriftGuard}${taskBatchInput ? `; batch=${taskBatchFile || 'pending'}:${taskBatchParam}` : ''}`,
      ],
      subIssues: [],
    };

    const [templateName, templateVersion = 'v1.0'] = normalizedTemplate.split('@');
    try {
      const created = await createWorkspaceTask({
        name: nextTask.name,
        template_name: templateName,
        template_version: templateVersion,
        schedule: {
          mode: taskComposerDraft.scheduleMode,
          recurring_mode: taskComposerDraft.recurringMode,
          daily_time: taskComposerDraft.dailyTime,
          interval_value: taskComposerDraft.intervalValue,
          interval_unit: taskComposerDraft.intervalUnit,
          label: formatTaskNextRun(taskComposerDraft),
        },
        parameters: Object.fromEntries(taskComposerDraft.templateParams.map((item) => [item.key, item.value])),
        policies: {
          concurrency: taskConcurrency,
          incremental: taskComposerDraft.incremental,
          incremental_mode: taskComposerDraft.incrementalMode,
          respect_robots: taskRespectRobots,
          drift_guard: taskDriftGuard,
          batch: taskBatchInput ? {
            file: taskBatchFile,
            parameter: taskBatchParam,
            size: taskBatchSize,
            start_line: taskBatchStartLine,
            limit: taskBatchLimit,
            delay: taskBatchDelay,
          } : null,
        },
        owner: nextTask.owner,
      });
      const createdTask = mapWorkspaceTask(created);
      setKeyword('');
      setTaskFilter('all');
      setTaskItems((prev) => [createdTask, ...prev.filter((item) => item.key !== createdTask.key)]);
      setTaskRuntime((prev) => ({ ...prev, [created.id]: mapWorkspaceTaskRuntime(created) }));
      setSelectedTaskKey(created.id);
      setTaskVisibleCount(defaultPageSize);
      setTaskComposerOpen(false);
      resetTaskComposer({
        template: normalizedTemplate,
        templateLocked: taskComposerDraft.templateLocked,
      });
    } catch (error) {
      console.error('Failed to create task', error);
    }
  }, [resetTaskComposer, selectedTemplate, taskBatchDelay, taskBatchFile, taskBatchInput, taskBatchLimit, taskBatchParam, taskBatchSize, taskBatchStartLine, taskComposerDraft, taskConcurrency, taskDriftGuard, taskRespectRobots, taskTemplateOptions]);

  const handleWorkspaceTaskAction = useCallback(async (
    taskKey: string,
    action: Parameters<typeof runWorkspaceTaskAction>[1],
  ) => {
    try {
      const updated = await runWorkspaceTaskAction(taskKey, action);
      const mappedTask = mapWorkspaceTask(updated);
      setTaskItems((prev) => prev.map((item) => (item.key === taskKey ? mappedTask : item)));
      setTaskRuntime((prev) => ({ ...prev, [taskKey]: mapWorkspaceTaskRuntime(updated) }));
    } catch (error) {
      console.error(`Failed to run task action: ${action}`, error);
    }
  }, []);

  const handlePauseTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'pause');
  const handleResumeTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'resume');
  const handleCancelTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'cancel');
  const handleStartDownload = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'start_download');
  const handlePauseDownload = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'pause_download');
  const handleStartSync = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'start_sync');
  const handleCancelSync = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'cancel_sync');

  const renderTemplateList = () => (
    <div className="workspace-dock-list is-templates">
      {visibleTemplateRows.map((item) => {
        const status = templateStatusMeta[item.status];
        const draft = templateDrafts[item.key];
        const isSelected = selectedTemplateKey === item.key;
        const isPinned = Boolean(pinnedTemplateKeys[item.key]);
        const site = resolveSiteProfile(item.name);
        const linkedTaskCount = templateTaskCounts[item.key] ?? item.taskCount;
        const statusIcon = item.status === 'draft'
          ? <ReleaseDraftIcon />
          : item.status === 'deprecated'
            ? <ReleaseArchiveIcon />
            : null;

        return (
          <button
            type="button"
            key={item.key}
            className={`workspace-dock-card workspace-dock-selectable ${isSelected ? 'is-selected' : ''} ${isPinned ? 'is-pinned' : ''}`}
            onClick={() => {
              setSelectedTemplateKey(item.key);
              setTemplateDetailMode('overview');
            }}
          >
            <div className="workspace-dock-card-row">
              <div className="workspace-dock-card-main">
                <span className="workspace-dock-card-icon"><TemplateGlyph kind={inferSiteKind(item.dataType)} /></span>
                <div className="workspace-dock-card-copy">
                  <div className="workspace-dock-card-titleline">
                    <Text strong>{item.title}</Text>
                    <span className="workspace-dock-card-title-actions">
                      <span
                        className={`workspace-dock-card-pin ${isPinned ? 'is-pinned' : ''}`}
                        title={isPinned ? 'Unpin template' : 'Pin template'}
                        onClick={(event) => handleTemplatePinClick(event, item.key)}
                      >
                        <PushpinOutlined />
                      </span>
                    </span>
                  </div>
                  <div className="workspace-dock-card-subline">
                    <Text type="secondary">{draft.adapter}</Text>
                    {statusIcon ? (
                      <span className="workspace-dock-card-runtime">
                        <span className="workspace-dock-card-state" style={{ color: status.color }}>
                          <span className="workspace-dock-template-status-icon" role="img" aria-label={status.label}>
                            {statusIcon}
                          </span>
                          {status.label}
                        </span>
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={site} faviconUrl={item.faviconUrl} />{formatTemplateDomain(item.domain)}</span>
              <span>{item.version} · {item.fields} fields</span>
            </div>

            <div className="workspace-dock-card-footer">
              <span>{formatDateTime(draft.savedAt)}</span>
              <span className="workspace-dock-linked-tasks">
                <span
                  className={`workspace-dock-linked-task-count ${linkedTaskCount ? 'is-link' : ''}`}
                  role={linkedTaskCount ? 'link' : undefined}
                  tabIndex={linkedTaskCount ? 0 : undefined}
                  onClick={linkedTaskCount ? (event) => openLinkedTasks(event, item.name) : undefined}
                  onKeyDown={linkedTaskCount ? (event) => {
                    if (event.key === 'Enter' || event.key === ' ') openLinkedTasks(event, item.name);
                  } : undefined}
                >
                  {linkedTaskCount}
                </span>
                <span> tasks</span>
              </span>
            </div>

          </button>
        );
      })}
      {!templateRows.length && <div className="workspace-dock-empty">No matching templates</div>}
    </div>
  );

  const renderTaskList = () => (
    <div className="workspace-dock-list is-tasks">
      {visibleTaskRows.map((item) => {
        const isSelected = selectedTaskKey === item.key;
        const isPinned = Boolean(pinnedTaskKeys[item.key]);

        return (
          <button
            type="button"
            key={item.key}
            className={`workspace-dock-card workspace-dock-selectable ${isSelected ? 'is-selected' : ''} ${isPinned ? 'is-pinned' : ''}`}
            onClick={() => {
              setTaskComposerOpen(false);
              setSelectedTaskKey(item.key);
            }}
          >
            <div className="workspace-dock-card-row">
              <div className="workspace-dock-card-main">
                <span className="workspace-dock-card-icon">
                  <TaskPulseGlyph kind={item.site.kind} active={item.display.isRunning} />
                </span>
                <div className="workspace-dock-card-copy">
                  <div className="workspace-dock-card-titleline">
                    <Text strong>{item.name}</Text>
                    <span className="workspace-dock-card-title-actions">
                      <span
                        className={`workspace-dock-card-pin ${isPinned ? 'is-pinned' : ''}`}
                        title={isPinned ? 'Unpin task' : 'Pin task'}
                        onClick={(event) => handleTaskPinClick(event, item.key)}
                      >
                        <PushpinOutlined />
                      </span>
                    </span>
                  </div>
                  <div className="workspace-dock-card-subline">
                    <Text type="secondary">{item.template}</Text>
                    <span className="workspace-dock-card-runtime">
                      <span className="workspace-dock-card-state" style={{ color: item.display.color }}>
                        {item.display.icon}
                        {item.display.label}
                      </span>
                      <span className={`workspace-dock-card-score ${item.display.isRunning ? 'is-live' : ''}`}>{item.runtime.progress}%</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={item.site} />{stripDecorativeSuffix(item.area)}</span>
              <span>{formatCompactNumber(item.runtime.recordsValue)}</span>
              <span>{item.owner}</span>
            </div>

            <div className="workspace-dock-card-footer">
              <span>Next {formatTaskNextRunLabel(item.nextRun)}</span>
              <span className={item.runtime.status === 'failed' || item.runtime.status === 'paused' ? 'is-alert' : ''}>
                Lag {item.lag}
              </span>
            </div>

            <div className={`workspace-dock-card-bar ${item.display.isRunning ? 'is-running' : ''}`}>
              <i style={{ width: `${Math.max(item.runtime.progress, 6)}%`, background: item.display.color }} />
            </div>
          </button>
        );
      })}
      {!taskRows.length && <div className="workspace-dock-empty">No matching tasks</div>}
    </div>
  );

  const renderTaskComposer = () => {
    const matchedTemplate = templates.find((item) => `${item.name}@${item.version}` === taskComposerDraft.template) ?? null;
    const composerKind = matchedTemplate ? resolveSiteProfile(matchedTemplate.name).kind : 'generic';
    const composerSummary = taskComposerDraft.scheduleMode === 'once'
      ? '一次性任务适合补采、验证模板参数，默认按全量采集执行。'
      : taskComposerDraft.recurringMode === 'daily'
        ? `周期任务会在每天 ${taskComposerDraft.dailyTime} 执行，默认按增量方式续采。`
        : `周期任务会按每 ${taskComposerDraft.intervalValue} ${taskComposerDraft.intervalUnit === 'minute' ? '分钟' : '小时'} 执行，默认按增量方式续采。`;
    const incrementalSummary = formatIncrementalSummary(taskComposerDraft);
    const nextRunSummary = formatTaskNextRun(taskComposerDraft);
    const templateSelectionRequired = !taskComposerDraft.templateLocked;

    return (
      <section className="workspace-dock-detail is-task-composer">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TaskPulseGlyph kind={composerKind} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">新建任务</Text>
              <Text type="secondary">{matchedTemplate ? matchedTemplate.title : '配置任务模板与运行方式'}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            <button
              type="button"
              className="workspace-dock-detail-close"
              aria-label="关闭任务创建"
              onClick={closeTaskComposer}
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        <div className="workspace-dock-detail-body">
          <div className="workspace-dock-inline-meta">
            <span className="is-domain">{matchedTemplate?.domain ?? '选择模板'}</span>
            <span className="is-version">{taskComposerModeMeta[taskComposerDraft.scheduleMode].label}</span>
            <span className="is-asset">{nextRunSummary}</span>
          </div>

          <label className="workspace-dock-form-block">
            <span>任务名称</span>
            <Input
              value={taskComposerDraft.name}
              placeholder="输入任务名称"
              onChange={(event) => updateTaskComposerDraft({ name: event.target.value })}
            />
          </label>

          <label className="workspace-dock-form-block">
            <span>模板版本</span>
            <Select
              value={taskComposerDraft.template || undefined}
              disabled={taskComposerDraft.templateLocked}
              placeholder={taskTemplateOptions[0]?.label ?? 'template@v1.0'}
              onChange={(value) => updateTaskComposerTemplate(value)}
              showSearch
              popupClassName="workspace-dock-template-select-dropdown"
              optionFilterProp="label"
              filterOption={(input, option) => {
                const searchText = `${String(option?.value ?? '')} ${String((option as { title?: string } | undefined)?.title ?? '')}`.toLowerCase();
                return searchText.includes(input.toLowerCase());
              }}
              options={taskTemplateOptions.map((option) => ({
                value: option.value,
                title: option.title,
                label: (
                  <span className="workspace-dock-template-select-option">
                    <SiteLogoMark site={option.site} />
                    <span>{option.label}</span>
                  </span>
                ),
              }))}
            />
          </label>

          <label className="workspace-dock-form-block">
            <span>运行方式</span>
            <Segmented
              block
              size="small"
              value={taskComposerDraft.scheduleMode}
              onChange={(value) => handleTaskComposerModeChange(value as TaskComposerMode)}
              options={[
                { label: '一次性任务', value: 'once' },
                { label: '周期任务', value: 'recurring' },
              ]}
            />
          </label>

          {taskComposerDraft.scheduleMode === 'recurring' ? (
            <>
              <label className="workspace-dock-form-block">
                <span>执行策略</span>
                <Segmented
                  block
                  size="small"
                  value={taskComposerDraft.recurringMode}
                  onChange={(value) => updateTaskComposerDraft({ recurringMode: value as TaskRecurringMode })}
                  options={[
                    { label: recurringModeMeta.daily.label, value: 'daily' },
                    { label: recurringModeMeta.interval.label, value: 'interval' },
                  ]}
                />
              </label>

              {taskComposerDraft.recurringMode === 'daily' ? (
                <div className="workspace-dock-form-grid">
                  <label>
                    <span>执行时间</span>
                    <Input
                      type="time"
                      value={taskComposerDraft.dailyTime}
                      onChange={(event) => updateTaskComposerDraft({ dailyTime: event.target.value || '09:00' })}
                    />
                  </label>
                  <label>
                    <span>空页停止阈值</span>
                    <InputNumber
                      min={1}
                      max={20}
                      value={taskComposerDraft.maxEmptyPages}
                      onChange={(value) => updateTaskComposerDraft({ maxEmptyPages: value ?? 2 })}
                    />
                  </label>
                </div>
              ) : (
                <div className="workspace-dock-form-grid">
                  <label>
                    <span>执行间隔</span>
                    <InputNumber
                      min={5}
                      max={720}
                      value={taskComposerDraft.intervalValue}
                      onChange={(value) => updateTaskComposerDraft({ intervalValue: value ?? 30 })}
                    />
                  </label>
                  <label>
                    <span>间隔单位</span>
                    <Select
                      value={taskComposerDraft.intervalUnit}
                      options={intervalUnitMeta}
                      onChange={(value) => updateTaskComposerDraft({ intervalUnit: value as TaskIntervalUnit })}
                    />
                  </label>
                </div>
              )}
            </>
          ) : null}

          <div className="workspace-dock-note-panel">
            <small>执行摘要</small>
            <p>{composerSummary}</p>
          </div>

          <div className="workspace-dock-form-block">
            <span>模板参数</span>
            <div className="workspace-dock-form-grid">
              {taskComposerDraft.templateParams.map((item, index) => (
                <label key={item.key}>
                  <span>{item.label}</span>
                  <Input
                    value={item.value}
                    placeholder={item.placeholder}
                    onChange={(event) => updateTaskComposerParameter(index, event.target.value)}
                  />
                </label>
              ))}
            </div>
          </div>

          <div className="workspace-dock-switch-row">
            <div>
              <strong>增量采集</strong>
              <small>关闭后按全量任务执行；开启后会追加增量边界与停止规则。</small>
            </div>
            <Switch
              checked={taskComposerDraft.incremental}
              onChange={(checked) => updateTaskComposerDraft({ incremental: checked })}
            />
          </div>
          {taskComposerDraft.incremental ? (
            <div className="workspace-dock-progress-panel">
              <div className="workspace-dock-progress-meta">
                <strong>增量参数配置</strong>
                <span>{incrementalSummary}</span>
              </div>

              <label className="workspace-dock-form-block" style={{ marginTop: 10 }}>
                <span>增量策略</span>
                <Segmented
                  block
                  size="small"
                  value={taskComposerDraft.incrementalMode}
                  onChange={(value) => updateTaskComposerDraft({ incrementalMode: value as TaskIncrementalMode })}
                  options={[
                    { label: incrementalModeMeta.time_window.label, value: 'time_window' },
                    { label: incrementalModeMeta.stop_condition.label, value: 'stop_condition' },
                  ]}
                />
              </label>

              {taskComposerDraft.incrementalMode === 'time_window' ? (
                <>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 10 }}>
                    <label>
                      <span>时间字段</span>
                      <Select
                        value={taskComposerDraft.incrementalField}
                        options={defaultIncrementalFields.map((item) => ({ value: item, label: item }))}
                        onChange={(value) => updateTaskComposerDraft({ incrementalField: value })}
                      />
                    </label>
                    <label>
                      <span>重叠分钟</span>
                      <InputNumber
                        min={0}
                        max={240}
                        value={taskComposerDraft.overlapMinutes}
                        onChange={(value) => updateTaskComposerDraft({ overlapMinutes: value ?? 15 })}
                      />
                    </label>
                  </div>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>回看窗口</span>
                      <InputNumber
                        min={1}
                        max={90}
                        value={taskComposerDraft.lookbackValue}
                        onChange={(value) => updateTaskComposerDraft({ lookbackValue: value ?? 6 })}
                      />
                    </label>
                    <label>
                      <span>窗口单位</span>
                      <Select
                        value={taskComposerDraft.lookbackUnit}
                        options={lookbackUnitMeta}
                        onChange={(value) => updateTaskComposerDraft({ lookbackUnit: value as TaskLookbackUnit })}
                      />
                    </label>
                  </div>
                </>
              ) : (
                <>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 10 }}>
                    <label>
                      <span>判断字段</span>
                      <Select
                        value={taskComposerDraft.stopField}
                        options={defaultStopFields.map((item) => ({ value: item, label: item }))}
                        onChange={(value) => updateTaskComposerDraft({ stopField: value })}
                      />
                    </label>
                    <label>
                      <span>比较方式</span>
                      <Select
                        value={taskComposerDraft.stopComparator}
                        options={[
                          { value: '<', label: '< 阈值' },
                          { value: '<=', label: '<= 阈值' },
                        ]}
                        onChange={(value) => updateTaskComposerDraft({ stopComparator: value as '<' | '<=' })}
                      />
                    </label>
                  </div>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>停止阈值</span>
                      <Input
                        value={taskComposerDraft.stopThreshold}
                        placeholder="例如：7d / 2026-06-01 / 1000"
                        onChange={(event) => updateTaskComposerDraft({ stopThreshold: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>连续命中页数</span>
                      <InputNumber
                        min={1}
                        max={20}
                        value={taskComposerDraft.stopConsecutivePages}
                        onChange={(value) => updateTaskComposerDraft({ stopConsecutivePages: value ?? 2 })}
                      />
                    </label>
                  </div>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>最多空页数</span>
                      <InputNumber
                        min={1}
                        max={20}
                        value={taskComposerDraft.maxEmptyPages}
                        onChange={(value) => updateTaskComposerDraft({ maxEmptyPages: value ?? 2 })}
                      />
                    </label>
                    <label>
                      <span>策略说明</span>
                      <Input value="若页面抓取到的排序字段持续低于阈值，则提前停止翻页" readOnly />
                    </label>
                  </div>
                </>
              )}
            </div>
          ) : null}

          <div className="workspace-dock-detail-actions">
            <span>{incrementalSummary}</span>
            <div className="workspace-dock-action-row">
              <button type="button" className="workspace-dock-inline-action" onClick={closeTaskComposer}>
                Cancel
              </button>
              <button
                type="button"
                className="workspace-dock-inline-action is-primary"
                disabled={templateSelectionRequired && !taskComposerDraft.template.trim()}
                onClick={handleCreateTask}
              >
                <PlusOutlined />
                Create
              </button>
            </div>
          </div>
        </div>
      </section>
    );
  };

  const renderTaskComposerClean = () => {
    const matchedTemplate = templates.find((item) => `${item.name}@${item.version}` === taskComposerDraft.template) ?? null;
    const composerKind = matchedTemplate ? resolveSiteProfile(matchedTemplate.name).kind : 'generic';
    const scheduleModeLabel = taskComposerDraft.scheduleMode === 'once' ? 'One-time Task' : 'Recurring Task';
    const nextRunSummary = taskComposerDraft.scheduleMode === 'once'
      ? 'Manual start'
      : taskComposerDraft.recurringMode === 'daily'
        ? `Daily ${taskComposerDraft.dailyTime}`
        : `Every ${taskComposerDraft.intervalValue} ${taskComposerDraft.intervalUnit === 'minute' ? 'minutes' : 'hours'}`;
    const incrementalSummary = taskComposerDraft.incremental ? 'Incremental collect' : 'Full collect';
    const composerSummary = taskComposerDraft.scheduleMode === 'once'
      ? 'Run this task once for validation, sampling, or a full manual collection.'
      : taskComposerDraft.recurringMode === 'daily'
        ? `Run this task every day at ${taskComposerDraft.dailyTime}. Incremental collect is enabled by default.`
        : `Run this task every ${taskComposerDraft.intervalValue} ${taskComposerDraft.intervalUnit === 'minute' ? 'minutes' : 'hours'}. Incremental collect is enabled by default.`;

    return (
      <section className="workspace-dock-detail is-task-composer">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TaskPulseGlyph kind={composerKind} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">New Task</Text>
              <Text type="secondary">{matchedTemplate ? matchedTemplate.title : 'Create a task from a template version.'}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            <button
              type="button"
              className="workspace-dock-detail-close"
              aria-label="Close task composer"
              onClick={closeTaskComposer}
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        <div className="workspace-dock-detail-body">
          <div className="workspace-dock-inline-meta">
            <span className="is-domain">{matchedTemplate?.domain ?? 'Select template'}</span>
            <span className="is-version">{scheduleModeLabel}</span>
            <span className="is-asset">{nextRunSummary}</span>
          </div>

          <label className="workspace-dock-form-block">
            <span>Task Name</span>
            <Input
              value={taskComposerDraft.name}
              placeholder="Enter task name"
              onChange={(event) => updateTaskComposerDraft({ name: event.target.value })}
            />
          </label>

          <label className="workspace-dock-form-block">
            <span>Template Version</span>
            <Select
              value={taskComposerDraft.template || undefined}
              disabled={taskComposerDraft.templateLocked}
              placeholder={taskTemplateOptions[0]?.label ?? 'Select template version'}
              onChange={(value) => updateTaskComposerTemplate(value)}
              showSearch
              popupClassName="workspace-dock-template-select-dropdown"
              filterOption={(input, option) => {
                const searchText = String((option as { searchText?: string } | undefined)?.searchText ?? '').toLowerCase();
                return searchText.includes(input.toLowerCase());
              }}
              options={taskTemplateOptions.map((option) => ({
                value: option.value,
                searchText: `${option.value} ${option.title}`.toLowerCase(),
                label: (
                  <span className="workspace-dock-template-select-option">
                    <SiteLogoMark site={option.site} />
                    <span>{option.label}</span>
                  </span>
                ),
              }))}
            />
          </label>

          <div className="workspace-dock-schedule-row">
            <label className="workspace-dock-form-block workspace-dock-schedule-control">
              <span>Run Schedule</span>
              <Segmented
                className="workspace-dock-run-schedule"
                size="small"
                value={taskComposerDraft.scheduleMode === 'once' ? 'once' : taskComposerDraft.recurringMode}
                onChange={(value) => {
                  if (value === 'once') {
                    handleTaskComposerModeChange('once');
                  } else {
                    handleTaskComposerModeChange('recurring');
                    updateTaskComposerDraft({ recurringMode: value as TaskRecurringMode });
                  }
                }}
                options={[
                  { label: 'Once', value: 'once' },
                  { label: 'Daily', value: 'daily' },
                  { label: 'Interval', value: 'interval' },
                ]}
              />
            </label>
            <label className="workspace-dock-form-block workspace-dock-concurrency">
              <span>Concurrency</span>
              <InputNumber min={1} max={50} value={taskConcurrency} onChange={(value) => setTaskConcurrency(value ?? 4)} />
            </label>
          </div>

          <div className="workspace-dock-schedule-options">
            {taskComposerDraft.scheduleMode === 'once' ? (
              <div className="workspace-dock-form-grid">
                <label>
                  <span>First Run</span>
                  <Input value="Manual start" disabled />
                </label>
              </div>
            ) : taskComposerDraft.recurringMode === 'daily' ? (
              <div className="workspace-dock-form-grid">
                <label>
                  <span>Run At</span>
                  <Input
                    type="time"
                    value={taskComposerDraft.dailyTime}
                    onChange={(event) => updateTaskComposerDraft({ dailyTime: event.target.value || '09:00' })}
                  />
                </label>
                <label>
                  <span>Empty Page Limit</span>
                  <InputNumber
                    min={1}
                    max={20}
                    value={taskComposerDraft.maxEmptyPages}
                    onChange={(value) => updateTaskComposerDraft({ maxEmptyPages: value ?? 2 })}
                  />
                </label>
              </div>
            ) : (
              <div className="workspace-dock-form-grid">
                <label>
                  <span>Interval</span>
                  <InputNumber
                    min={5}
                    max={720}
                    value={taskComposerDraft.intervalValue}
                    onChange={(value) => updateTaskComposerDraft({ intervalValue: value ?? 30 })}
                  />
                </label>
                <label>
                  <span>Unit</span>
                  <Select
                    value={taskComposerDraft.intervalUnit}
                    options={[
                      { value: 'minute', label: 'Minute' },
                      { value: 'hour', label: 'Hour' },
                    ]}
                    onChange={(value) => updateTaskComposerDraft({ intervalUnit: value as TaskIntervalUnit })}
                  />
                </label>
              </div>
            )}
          </div>

          <div className="workspace-dock-note-panel">
            <small>Run Summary</small>
            <p>{composerSummary}</p>
          </div>

          <div className="workspace-dock-progress-panel">
            <div className="workspace-dock-progress-meta">
              <strong>Template Parameters</strong>
              <span>{taskComposerDraft.templateParams.length} fields</span>
            </div>
            <div className="workspace-dock-form-grid" style={{ marginTop: 10 }}>
              {taskComposerDraft.templateParams.map((item, index) => (
                <label key={item.key}>
                  <span>{taskComposerFieldLabels[item.key] ?? item.key}</span>
                  <Input
                    value={item.value}
                    placeholder={taskComposerFieldPlaceholders[item.key] ?? item.placeholder}
                    onChange={(event) => updateTaskComposerParameter(index, event.target.value)}
                  />
                </label>
              ))}
            </div>
          </div>

          {releaseTaskDefaults?.batch ? (
            <div className="workspace-dock-progress-panel">
              <div className="workspace-dock-switch-row">
                <div>
                  <strong>Batch Input</strong>
                  <small>Inject values from a TXT or CSV file into a template parameter.</small>
                </div>
                <Checkbox
                  className="workspace-dock-task-checkbox"
                  checked={taskBatchInput}
                  onChange={(event) => setTaskBatchInput(event.target.checked)}
                />
              </div>
              {taskBatchInput ? (
                <>
                  <label className="workspace-dock-form-block" style={{ marginTop: 10 }}>
                    <span>List File *</span>
                    <Upload
                      accept=".txt,.csv"
                      showUploadList={false}
                      beforeUpload={() => false}
                      onChange={({ file }) => setTaskBatchFile(file.name || '')}
                    >
                      <Button className="workspace-dock-file-picker-button" size="small" icon={<UploadOutlined />}>{taskBatchFile || 'Choose'}</Button>
                    </Upload>
                  </label>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>Inject Into *</span>
                      <Select
                        value={taskBatchParam || undefined}
                        options={releaseTaskDefaults.params.map((param) => ({ value: param.name, label: param.name }))}
                        onChange={setTaskBatchParam}
                      />
                    </label>
                    <label>
                      <span>Batch Size</span>
                      <InputNumber min={1} value={taskBatchSize} onChange={(value) => setTaskBatchSize(value ?? 1)} />
                    </label>
                    <label>
                      <span>Start Line</span>
                      <InputNumber min={0} value={taskBatchStartLine} onChange={(value) => setTaskBatchStartLine(value ?? 0)} />
                    </label>
                    <label>
                      <span>Limit</span>
                      <InputNumber min={1} placeholder="No limit" value={taskBatchLimit} onChange={setTaskBatchLimit} />
                    </label>
                    <label>
                      <span>Delay (sec)</span>
                      <InputNumber min={0} value={taskBatchDelay} onChange={(value) => setTaskBatchDelay(value ?? 0)} />
                    </label>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}

          <div className="workspace-dock-switch-row">
            <div>
              <strong>Incremental Collect</strong>
              <small>Turn off for a full run. Turn on to continue incrementally.</small>
            </div>
            <Checkbox
              className="workspace-dock-task-checkbox"
              checked={taskComposerDraft.incremental}
              onChange={(event) => updateTaskComposerDraft({ incremental: event.target.checked })}
            />
          </div>
          <div className="workspace-dock-switch-row">
            <div>
              <strong>Robots Policy</strong>
              <small>Honor source limits and robots directives.</small>
            </div>
            <Checkbox
              className="workspace-dock-task-checkbox"
              checked={taskRespectRobots}
              onChange={(event) => setTaskRespectRobots(event.target.checked)}
            />
          </div>
          <div className="workspace-dock-switch-row">
            <div>
              <strong>Drift Guard</strong>
              <small>Pause the task when the source structure changes.</small>
            </div>
            <Checkbox
              className="workspace-dock-task-checkbox"
              checked={taskDriftGuard}
              onChange={(event) => setTaskDriftGuard(event.target.checked)}
            />
          </div>

          <div className="workspace-dock-detail-actions">
            <span>{incrementalSummary}</span>
            <div className="workspace-dock-action-row">
              <button type="button" className="workspace-dock-inline-action" onClick={closeTaskComposer}>
                Cancel
              </button>
              <button
                type="button"
                className="workspace-dock-inline-action is-primary"
                disabled={!taskComposerDraft.template.trim()}
                onClick={handleCreateTask}
              >
                <PlusOutlined />
                Create
              </button>
            </div>
          </div>
        </div>
      </section>
    );
  };

  const renderTemplateDetail = () => {
    if (!selectedTemplate || !selectedTemplateDraft) return null;

    const templatePinned = Boolean(pinnedTemplateKeys[selectedTemplate.key]);
    const linkedTaskCount = templateTaskCounts[selectedTemplate.key] ?? selectedTemplate.taskCount;
    const templateListFields = extractListFields(selectedTemplateDraft.yaml);

    return (
      <section className="workspace-dock-detail">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TemplateGlyph kind={inferSiteKind(selectedTemplate.dataType)} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">{selectedTemplate.title}</Text>
              <Text type="secondary">{selectedTemplate.name}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            <Tooltip title="编辑模板" placement="top">
              <button
                type="button"
                className={`workspace-dock-detail-icon-btn ${templateDetailMode === 'edit' ? 'is-pinned' : ''}`}
                aria-label="编辑模板"
                aria-pressed={templateDetailMode === 'edit'}
                onClick={() => {
                  if (templateDetailMode === 'overview') {
                    onTemplateApply?.({ yaml: selectedTemplateDraft.yaml, adapter: selectedTemplateDraft.adapter });
                    setTemplateDetailMode('edit');
                  } else {
                    setTemplateDetailMode('overview');
                  }
                }}
              >
                <EditOutlined />
              </button>
            </Tooltip>
            <button
              type="button"
              className="workspace-dock-detail-close"
              aria-label="关闭模板详情"
              onClick={() => {
                setTemplateDetailMode('overview');
                setSelectedTemplateKey(null);
              }}
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        {templateDetailMode === 'overview' ? (
          <div className="workspace-dock-detail-body">
            <div className="workspace-dock-inline-meta">
              <span className="is-domain">域名 {selectedTemplate.domain}</span>
              <span className="is-version">版本 {selectedTemplate.version}</span>
              <span className="is-field">字段 {selectedTemplate.fields}</span>
              <span className="workspace-dock-linked-tasks workspace-dock-detail-linked-tasks">
                <span
                  className="workspace-dock-linked-task-count is-link"
                  role="link"
                  tabIndex={0}
                  onClick={(event) => openLinkedTasks(event, selectedTemplate.name)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') openLinkedTasks(event, selectedTemplate.name);
                  }}
                >
                  {linkedTaskCount}
                </span>
                <span> tasks</span>
              </span>
              <span className="is-quality">质量 {selectedTemplate.quality}%</span>
            </div>

            <div className="workspace-dock-chip-row">
              {templateListFields.map((field) => (
                <span key={field} className="workspace-dock-mini-chip">{field}</span>
              ))}
            </div>

            <div className="workspace-dock-code-panel">
              <small>YAML</small>
              <pre>{selectedTemplateDraft.yaml}</pre>
            </div>

            <div className="workspace-dock-note-panel">
              <small>Description</small>
              <p>{selectedTemplateDraft.notes}</p>
            </div>

            <div className="workspace-dock-detail-actions">
              <span>Last modified {formatDateTime(selectedTemplateDraft.savedAt)}</span>
              <div className="workspace-dock-action-row">
                <button
                  type="button"
                  className="workspace-dock-inline-action is-primary"
                  onClick={() => {
                    openTaskComposer({
                      name: `${selectedTemplate.title} task`,
                      template: `${selectedTemplate.name}@${selectedTemplate.version}`,
                      templateLocked: true,
                      scheduleMode: 'recurring',
                    });
                  }}
                >
                  <PlusOutlined />
                  Create Task
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="workspace-dock-detail-body">
            <div className="workspace-dock-form-grid">
              <label>
                <span>Adapter</span>
                <Input
                  value={selectedTemplateDraft.adapter}
                  onChange={(event) => updateTemplateDraft(selectedTemplate.key, {
                    adapter: event.target.value,
                  })}
                />
              </label>
              <label>
                <span>输出标签</span>
                <Input
                  value={selectedTemplateDraft.outputTag}
                  onChange={(event) => updateTemplateDraft(selectedTemplate.key, { outputTag: event.target.value })}
                />
              </label>
            </div>

            <label className="workspace-dock-form-block">
              <span>YAML</span>
              <TextArea
                value={selectedTemplateDraft.yaml}
                onChange={(event) => updateTemplateDraft(selectedTemplate.key, { yaml: event.target.value })}
                autoSize={{ minRows: 6, maxRows: 10 }}
              />
            </label>

            <label className="workspace-dock-form-block">
              <span>Description</span>
              <TextArea
                value={selectedTemplateDraft.notes}
                onChange={(event) => updateTemplateDraft(selectedTemplate.key, { notes: event.target.value })}
                autoSize={{ minRows: 3, maxRows: 6 }}
              />
            </label>

            <div className="workspace-dock-detail-actions">
              <span>Last saved {formatDateTime(selectedTemplateDraft.savedAt)}</span>
            </div>
          </div>
        )}
      </section>
    );
  };

  const renderTaskDetail = () => {
    if (!selectedTask) return null;

    const { runtime, display } = selectedTask;
    const taskCanceled = runtime.controlState === 'canceled';
    const taskPinned = Boolean(pinnedTaskKeys[selectedTask.key]);

    return (
      <section className="workspace-dock-detail is-task-log-only">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TaskPulseGlyph kind={selectedTask.site.kind} active={display.isRunning} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">{selectedTask.name}</Text>
              <Text type="secondary">{selectedTask.template}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            {runtime.status === 'running' && runtime.controlState !== 'canceled' ? (
              <Tooltip title="暂停任务" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-pause"
                  aria-label="暂停任务"
                  onClick={() => handlePauseTask(selectedTask.key)}
                >
                  <PauseCircleOutlined />
                </button>
              </Tooltip>
            ) : null}
            {(runtime.status === 'paused' || (runtime.status === 'failed' && runtime.controlState !== 'canceled')) ? (
              <Tooltip title="继续任务" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-run"
                  aria-label="继续任务"
                  onClick={() => handleResumeTask(selectedTask.key)}
                >
                  <CaretRightOutlined />
                </button>
              </Tooltip>
            ) : null}
            {runtime.status !== 'completed' && runtime.controlState !== 'canceled' ? (
              <Tooltip title="取消任务" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-danger"
                  aria-label="取消任务"
                  onClick={() => handleCancelTask(selectedTask.key)}
                >
                  <StopOutlined />
                </button>
              </Tooltip>
            ) : null}
            {runtime.downloadState === 'running' ? (
              <Tooltip title="暂停下载" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-download"
                  aria-label="暂停下载"
                  disabled={taskCanceled}
                  onClick={() => handlePauseDownload(selectedTask.key)}
                >
                  <PauseCircleOutlined />
                </button>
              </Tooltip>
            ) : (
              <Tooltip title="下载资源" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-download"
                  aria-label="下载资源"
                  disabled={taskCanceled}
                  onClick={() => handleStartDownload(selectedTask.key)}
                >
                  <DownloadOutlined />
                </button>
              </Tooltip>
            )}
            {runtime.syncState === 'running' ? (
              <Tooltip title="取消同步" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-sync"
                  aria-label="取消同步"
                  disabled={taskCanceled}
                  onClick={() => handleCancelSync(selectedTask.key)}
                >
                  <CloseCircleOutlined />
                </button>
              </Tooltip>
            ) : (
              <Tooltip title="开始同步" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-sync"
                  aria-label="开始同步"
                  disabled={taskCanceled}
                  onClick={() => handleStartSync(selectedTask.key)}
                >
                  <SyncOutlined />
                </button>
              </Tooltip>
            )}
            <button
              type="button"
              className="workspace-dock-detail-close"
              aria-label="关闭任务详情"
              onClick={() => setSelectedTaskKey(null)}
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        <div className="workspace-dock-detail-body">
          <div className={`workspace-dock-inline-meta ${display.isRunning ? 'is-live' : ''}`}>
            <span className="is-quality">进度 {runtime.progress}%</span>
            <span className="is-asset">吞吐 {runtime.throughput}/min</span>
            <span className="is-domain">总量 {formatCompactNumber(runtime.recordsValue)}</span>
            <span className="is-version">{display.label}</span>
          </div>

          <div className="workspace-dock-progress-panel">
            <div className="workspace-dock-progress-meta">
              <span><SiteLogoMark site={selectedTask.site} />{stripDecorativeSuffix(selectedTask.area)}</span>
              <strong>{selectedTask.nextRun}</strong>
            </div>
            <div className={`workspace-dock-marquee ${display.isRunning ? 'is-running' : ''}`}>
              <i style={{ width: `${Math.max(runtime.progress, 8)}%`, background: display.color }} />
            </div>
            <div className="workspace-dock-progress-meta is-subtle">
              <span>{selectedTask.lag}</span>
              <span>{display.isRunning ? `+${formatCompactNumber(runtime.lastDelta)}/轮` : display.label}</span>
            </div>
          </div>

          <div className="workspace-dock-log-panel">
            <div className="workspace-dock-history-head">
              <div className="workspace-dock-log-caption">日志</div>
              <div className="workspace-dock-diff-stats" aria-label="任务变化统计">
                <span className="is-added">+{taskInsertedLines} 新增</span>
                <span className="is-updated">~{taskUpdatedLines} 修改</span>
                <span className="is-deleted">-{taskDeletedLines} 删除</span>
                <span className="is-download"><DownloadOutlined />{formatCompactNumber(taskDownloadedResources)} 下载</span>
                <span className="is-sync"><SyncOutlined spin={runtime.syncState === 'running'} />{formatCompactNumber(taskSyncedRecords)} 同步</span>
              </div>
            </div>
            <div className="workspace-dock-log-list is-detail">
              {runtime.logs.map((log) => (
                <div className={`workspace-dock-log-row is-${log.level}`} key={`${selectedTask.key}-${log.time}-${log.message}`}>
                  <span>{log.time}</span>
                  <b>{log.level}</b>
                  <code>{log.message}</code>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    );
  };

  return (
    <>
      <style>{`
        .workspace-dock-shell {
          position: absolute;
          inset: 0;
          pointer-events: none;
          z-index: 100;
        }
        .workspace-dock-hitbox {
          position: absolute;
          inset: 0;
          pointer-events: none;
        }
        .workspace-dock-hitbox.is-open {
          pointer-events: none;
        }
        .workspace-glyph {
          width: 18px;
          height: 18px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: inherit;
        }
        .workspace-glyph .anticon {
          font-size: 18px;
          line-height: 1;
        }
        .workspace-task-glyph {
          position: relative;
        }
        .workspace-task-glyph i {
          position: absolute;
          left: 2px;
          right: 2px;
          bottom: -4px;
          height: 2px;
          border-radius: 999px;
          opacity: 0;
          background: currentColor;
          transform: scaleX(0.35);
          transform-origin: left center;
        }
        .workspace-task-glyph.is-active i {
          opacity: 0.94;
          animation: workspaceTaskPulse 1.2s ease-in-out infinite;
        }
        .workspace-dock-panel {
          position: absolute;
          top: 12px;
          right: 12px;
          bottom: 12px;
          width: min(400px, calc(100% - 24px));
          min-width: min(400px, calc(100% - 24px));
          height: auto;
          max-height: none;
          border-radius: 14px;
          border: 1px solid ${aura.border};
          background:
            linear-gradient(180deg, rgba(31, 36, 48, 0.96), rgba(20, 24, 34, 0.95)),
            rgba(18, 22, 31, 0.96);
          box-shadow: -18px 20px 52px rgba(0, 0, 0, 0.34);
          backdrop-filter: ${aura.backdrop};
          overflow: hidden;
          display: flex;
          flex-direction: column;
          opacity: 0;
          transform: translateX(28px);
          transition: opacity 180ms ease, transform 200ms ease;
          pointer-events: none;
        }
        .workspace-dock-panel.is-open {
          opacity: 1;
          transform: translateX(0);
          pointer-events: auto;
        }
        .workspace-dock-panel.is-detail {
          width: min(852px, calc(100% - 24px));
          min-width: min(852px, calc(100% - 24px));
        }
        .workspace-dock-stack {
          flex: 1;
          height: 100%;
          min-height: 0;
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          grid-template-rows: minmax(0, 1fr);
        }
        .workspace-dock-panel.is-detail .workspace-dock-stack {
          grid-template-columns: 400px 450px;
        }
        .workspace-dock-master {
          flex: 1;
          min-width: 0;
          min-height: 0;
          display: flex;
          flex-direction: column;
          border-right: 1px solid transparent;
        }
        .workspace-dock-panel.is-detail .workspace-dock-master {
          width: 400px;
          border-right-color: ${aura.borderSoft};
        }
        .workspace-dock-panel.is-detail .workspace-dock-detail {
          width: 450px;
        }
        .workspace-dock-toolbar {
          padding: 14px 16px;
          display: grid;
          gap: 8px;
          border-bottom: 1px solid ${aura.borderSoft};
        }
        .workspace-dock-toolbar-search-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .workspace-dock-toolbar-search-row .ant-input-affix-wrapper {
          flex: 1;
          min-width: 0;
        }
        .workspace-dock-toolbar-actions {
          display: flex;
          justify-content: flex-end;
          flex-shrink: 0;
        }
        .workspace-dock-toolbar-action-btn {
          min-width: 34px;
          height: 34px;
          padding: 0 10px;
          border-radius: 10px;
          border: 1px solid ${aura.border};
          background: rgba(255, 255, 255, 0.035);
          color: ${aura.subtle};
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          transition: border-color 160ms ease, background 160ms ease, color 160ms ease, transform 160ms ease;
        }
        .workspace-dock-toolbar-action-btn:hover {
          color: ${aura.text};
          border-color: rgba(138, 180, 255, 0.2);
          background: rgba(255, 255, 255, 0.05);
          transform: translateY(-1px);
        }
        .workspace-dock-toolbar-action-btn .anticon {
          font-size: 14px;
          line-height: 1;
        }
        .workspace-dock-toolbar .ant-input-affix-wrapper,
        .workspace-dock-toolbar .ant-segmented {
          background: rgba(255, 255, 255, 0.035);
          border-color: ${aura.border};
          box-shadow: none;
        }
        .workspace-dock-toolbar .ant-input {
          background: transparent !important;
        }
        .workspace-dock-toolbar .ant-input::placeholder {
          font-size: 11px;
        }
        .workspace-dock-toolbar .ant-segmented {
          padding: 3px;
        }
        .workspace-dock-toolbar .ant-segmented-item {
          color: ${aura.subtle};
          font-size: 12px;
        }
        .workspace-dock-toolbar .ant-segmented-item-selected {
          background: rgba(138, 180, 255, 0.14);
          color: ${aura.text};
          box-shadow: none;
        }
        .workspace-dock-body {
          flex: 1;
          min-height: 0;
          position: relative;
          overflow: auto;
        }
        .workspace-dock-list {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 4px 8px 24px;
        }
        .workspace-dock-list.is-templates {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .workspace-dock-list.is-templates .workspace-dock-card {
          width: 100%;
          min-height: 118px;
          height: auto;
          padding: 8px 10px;
          overflow: hidden;
        }
        .workspace-dock-list.is-templates .workspace-dock-card-footer {
          margin-top: 5px;
        }
        .workspace-dock-list.is-tasks {
          gap: 6px;
        }
        .workspace-dock-list.is-tasks .workspace-dock-card {
          width: 100%;
          min-height: 118px;
          padding: 8px 10px;
          border-radius: 9px;
          overflow: hidden;
        }
        .workspace-dock-list.is-tasks .workspace-dock-card-row {
          align-items: center;
        }
        .workspace-dock-list.is-tasks .workspace-dock-card-meta {
          margin-top: 8px;
        }
        .workspace-dock-card {
          width: 100%;
          padding: 8px 10px;
          border-radius: 9px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.035);
          text-align: left;
        }
        .workspace-dock-selectable {
          cursor: pointer;
          transition: border-color 180ms ease, background 180ms ease, transform 180ms ease;
        }
        .workspace-dock-selectable:hover {
          border-color: rgba(138, 180, 255, 0.18);
          background: rgba(255, 255, 255, 0.05);
          transform: translateY(-1px);
        }
        .workspace-dock-selectable.is-selected {
          border-color: rgba(138, 180, 255, 0.24);
          background: rgba(138, 180, 255, 0.08);
        }
        .workspace-dock-selectable.is-pinned {
          border-color: rgba(246, 195, 91, 0.18);
        }
        .workspace-dock-card-row {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 8px;
        }
        .workspace-dock-card-main {
          min-width: 0;
          flex: 1;
          display: flex;
          align-items: flex-start;
          gap: 8px;
        }
        .workspace-dock-card-icon {
          flex-shrink: 0;
          color: rgba(255, 255, 255, 0.68);
          font-size: 13px;
          line-height: 18px;
          margin-top: 1px;
        }
        .workspace-dock-card-copy {
          min-width: 0;
          flex: 1;
        }
        .workspace-dock-card-titleline {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          min-width: 0;
        }
        .workspace-dock-card-titleline .ant-typography {
          min-width: 0;
          flex: 1;
          color: ${aura.text};
          font-size: 12px;
          line-height: 1.35;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .workspace-dock-card-copy .ant-typography-secondary {
          display: block;
          margin-top: 2px;
          color: ${aura.subtle};
          font-size: 10px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .workspace-dock-card-subline {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-top: 2px;
        }
        .workspace-dock-card-runtime {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          flex-shrink: 0;
        }
        .workspace-dock-card-state {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          flex-shrink: 0;
          font-size: 9px;
          line-height: 1;
        }
        .workspace-dock-card-state .anticon {
          font-size: 11px;
        }
        .workspace-dock-card-pill,
        .workspace-dock-detail-pill {
          min-height: 18px;
          padding: 0 7px;
          border-radius: 999px;
          border: 1px solid ${aura.border};
          background: rgba(255, 255, 255, 0.04);
          display: inline-flex;
          align-items: center;
          gap: 6px;
          white-space: nowrap;
          font-size: 9px;
          line-height: 1;
          flex-shrink: 0;
        }
        .workspace-dock-template-status-icon {
          width: 14px;
          height: 18px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          line-height: 18px;
          flex-shrink: 0;
        }
        .workspace-dock-template-status-icon > svg {
          width: 12px;
          height: 12px;
        }
        .workspace-dock-card-title-actions {
          flex-shrink: 0;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .workspace-dock-card-pin {
          width: 20px;
          height: 20px;
          border-radius: 999px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: rgba(255, 255, 255, 0.34);
          opacity: 0;
          pointer-events: none;
          transition: color 160ms ease, opacity 160ms ease, transform 160ms ease;
        }
        .workspace-dock-card:hover .workspace-dock-card-pin,
        .workspace-dock-card:focus-visible .workspace-dock-card-pin {
          opacity: 1;
          pointer-events: auto;
        }
        .workspace-dock-card-pin:hover {
          color: ${aura.text};
          transform: translateY(-1px);
        }
        .workspace-dock-card-pin.is-pinned {
          color: #F6C35B;
        }
        .workspace-dock-card-pin .anticon {
          font-size: 11px;
          line-height: 1;
        }
        .workspace-dock-card-score {
          flex-shrink: 0;
          color: ${aura.subtle};
          font-size: 10px;
          line-height: 1;
          font-weight: 600;
        }
        .workspace-dock-card-score.is-live {
          color: ${aura.accent};
        }
        .workspace-dock-card-meta {
          margin-top: 8px;
          display: flex;
          flex-wrap: wrap;
          gap: 5px;
        }
        .workspace-dock-card-meta span,
        .workspace-dock-mini-chip {
          min-height: 18px;
          padding: 0 7px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.045);
          display: inline-flex;
          align-items: center;
          color: ${aura.subtle};
          font-size: 9px;
          line-height: 1;
        }
        .workspace-dock-card-meta span {
          gap: 6px;
        }
        .workspace-dock-card-footer {
          margin-top: 8px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          color: ${aura.subtle};
          font-size: 10px;
          line-height: 1.3;
        }
        .workspace-dock-card-footer span {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .workspace-dock-linked-tasks {
          display: inline-flex;
          align-items: center;
          color: ${aura.subtle};
        }
        .workspace-dock-linked-task-count {
          color: inherit;
          font-weight: 600;
        }
        .workspace-dock-linked-task-count.is-link {
          cursor: pointer;
        }
        .workspace-dock-linked-task-count.is-link:hover,
        .workspace-dock-linked-task-count.is-link:focus-visible {
          color: #002FA7;
          text-decoration: underline;
          text-underline-offset: 2px;
        }
        .workspace-dock-detail-linked-tasks {
          color: ${aura.subtle};
        }
        .workspace-dock-template-filter {
          border: none;
          background: transparent;
          color: #97B8FF;
          font: inherit;
        }
        .workspace-dock-template-filter {
          width: max-content;
          max-width: 100%;
          padding: 0;
          color: ${aura.subtle};
          font-size: 10px;
          cursor: pointer;
        }
        .workspace-dock-card-footer .is-alert {
          color: #F6C35B;
        }
        .workspace-dock-meta-logo {
          width: 14px;
          height: 14px;
          border-radius: 4px;
          background:
            linear-gradient(180deg, rgba(255, 255, 255, 0.18), rgba(255, 255, 255, 0) 48%),
            var(--brand-hue);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.16);
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: rgba(255, 255, 255, 0.96);
          font-size: 7px;
          font-style: normal;
          font-weight: 700;
          line-height: 1;
          letter-spacing: 0;
          flex-shrink: 0;
        }
        .workspace-dock-meta-logo img {
          width: 100%;
          height: 100%;
          display: block;
          object-fit: contain;
          border-radius: inherit;
        }
        .workspace-dock-card-bar,
        .workspace-dock-marquee {
          position: relative;
          margin-top: 8px;
          height: 5px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.08);
          overflow: hidden;
        }
        .workspace-dock-card-bar i,
        .workspace-dock-marquee i {
          display: block;
          height: 100%;
          border-radius: inherit;
        }
        .workspace-dock-card-bar.is-running::after,
        .workspace-dock-marquee.is-running::after {
          content: '';
          position: absolute;
          inset: 0;
          background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.08) 34%, rgba(255, 255, 255, 0.45) 50%, rgba(255, 255, 255, 0.08) 66%, transparent 100%);
          transform: translateX(-100%);
          animation: workspaceMarquee 1.35s linear infinite;
        }
        .workspace-dock-empty {
          min-height: 120px;
          border-radius: 10px;
          border: 1px dashed ${aura.border};
          display: flex;
          align-items: center;
          justify-content: center;
          color: ${aura.subtle};
          font-size: 11px;
        }
        .workspace-dock-empty.workspace-dock-empty-compact {
          min-height: 72px;
        }
        .workspace-dock-bottom-fade {
          position: sticky;
          bottom: 0;
          z-index: 2;
          height: 20px;
          margin-top: -26px;
          pointer-events: none;
          background: linear-gradient(180deg, rgba(255, 255, 255, 0), rgba(125, 125, 126, 0.32));
        }
        .workspace-dock-detail {
          min-width: 0;
          min-height: 0;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }
        .workspace-dock-detail-head {
          padding: 10px 12px;
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
          border-bottom: 1px solid ${aura.borderSoft};
        }
        .workspace-dock-detail-leading {
          min-width: 0;
          display: flex;
          align-items: flex-start;
          gap: 10px;
        }
        .workspace-dock-detail-icon {
          color: rgba(255, 255, 255, 0.84);
          margin-top: 1px;
        }
        .workspace-dock-detail-icon .workspace-glyph {
          width: 18px;
          height: 18px;
        }
        .workspace-dock-detail-title {
          display: block;
          color: ${aura.text};
          font-size: 13px;
          line-height: 1.4;
        }
        .workspace-dock-detail-leading .ant-typography-secondary {
          display: block;
          margin-top: 2px;
          font-size: 10px;
        }
        .workspace-dock-detail-close {
          margin-left: 2px;
          border: none;
          background: transparent;
          color: ${aura.subtle};
          width: 26px;
          height: 26px;
          border-radius: 50%;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
        }
        .workspace-dock-detail-close:hover {
          color: ${aura.text};
          background: rgba(255, 255, 255, 0.06);
        }
        .workspace-dock-detail-head-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .workspace-dock-detail-icon-btn {
          border: none;
          background: transparent;
          color: ${aura.subtle};
          width: 26px;
          height: 26px;
          border-radius: 50%;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          transition: color 160ms ease, background 160ms ease, transform 160ms ease;
        }
        .workspace-dock-detail-icon-btn:hover:not(:disabled) {
          color: ${aura.text};
          background: rgba(255, 255, 255, 0.06);
          transform: translateY(-1px);
        }
        .workspace-dock-detail-icon-btn:disabled {
          opacity: 0.34;
          cursor: not-allowed;
          transform: none;
          filter: none;
        }
        .workspace-dock-detail-icon-btn.is-run {
          color: #65D5A3;
          background: rgba(101, 213, 163, 0.09);
        }
        .workspace-dock-detail-icon-btn.is-pause {
          color: #F6C35B;
          background: rgba(246, 195, 91, 0.08);
        }
        .workspace-dock-detail-icon-btn.is-danger {
          color: #F4A4C1;
          background: rgba(244, 164, 193, 0.08);
        }
        .workspace-dock-detail-icon-btn.is-download {
          color: #F6C35B;
          background: rgba(246, 195, 91, 0.08);
        }
        .workspace-dock-detail-icon-btn.is-pinned {
          color: #F6C35B;
          background: rgba(246, 195, 91, 0.08);
        }
        .workspace-dock-detail-icon-btn.is-sync {
          color: #8AB4FF;
          background: rgba(138, 180, 255, 0.08);
        }
        .workspace-dock-detail-body {
          flex: 1;
          min-height: 0;
          padding: 10px 12px 12px;
          overflow: auto;
          scrollbar-width: thin;
          scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
          overscroll-behavior: contain;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .workspace-dock-inline-meta {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
          color: ${aura.subtle};
          font-size: 10px;
          line-height: 1.5;
        }
        .workspace-dock-inline-meta span {
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .workspace-dock-inline-meta .is-domain {
          color: #97B8FF;
        }
        .workspace-dock-inline-meta .is-version {
          color: #B5A7FF;
        }
        .workspace-dock-inline-meta .is-field {
          color: #8FDEC2;
        }
        .workspace-dock-inline-meta .is-asset {
          color: #F0BE77;
        }
        .workspace-dock-inline-meta .is-quality {
          color: #F4A4C1;
        }
        .workspace-dock-inline-meta .is-added {
          color: #8FDEC2;
        }
        .workspace-dock-inline-meta .is-updated {
          color: #97B8FF;
        }
        .workspace-dock-inline-meta .is-deleted {
          color: #F4A4C1;
        }
        .workspace-dock-inline-meta.is-live .is-added,
        .workspace-dock-inline-meta.is-live .is-updated,
        .workspace-dock-inline-meta.is-live .is-deleted {
          animation: workspaceDiffPulse 1.45s ease-in-out infinite;
        }
        .workspace-dock-metric-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
        }
        .workspace-dock-metric-card,
        .workspace-dock-history-card,
        .workspace-dock-log-panel,
        .workspace-dock-note-panel,
        .workspace-dock-code-panel,
        .workspace-dock-progress-panel {
          padding: 10px;
          border-radius: 9px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.035);
        }
        .workspace-dock-progress-panel {
          background: transparent;
        }
        .workspace-dock-log-panel {
          flex: 1;
          min-height: 0;
          display: flex;
          flex-direction: column;
        }
        .workspace-dock-detail.is-task-log-only .workspace-dock-detail-icon-btn {
          display: none;
        }
        .workspace-dock-detail.is-task-log-only .workspace-dock-log-panel {
          height: 100%;
          border: none;
          background: transparent;
        }
        .workspace-dock-metric-card span,
        .workspace-dock-history-head span,
        .workspace-dock-note-panel small,
        .workspace-dock-code-panel small,
        .workspace-dock-progress-meta span,
        .workspace-dock-form-grid label > span,
        .workspace-dock-form-block > span {
          display: block;
          color: ${aura.subtle};
          font-size: 10px;
        }
        .workspace-dock-metric-card strong,
        .workspace-dock-history-head strong,
        .workspace-dock-progress-meta strong {
          display: block;
          margin-top: 6px;
          color: ${aura.text};
          font-size: 12px;
          line-height: 1;
        }
        .workspace-dock-chip-row {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }
                .workspace-dock-code-panel pre {
          margin: 8px 0 0;
          color: ${aura.text};
          font-size: 11px;
          line-height: 1.5;
          white-space: pre-wrap;
          word-break: break-word;
          font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        }
        .workspace-dock-note-panel p {
          margin: 8px 0 0;
          color: ${aura.muted};
          font-size: 11px;
          line-height: 1.55;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-detail-body {
          gap: 12px;
          padding: 12px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-inline-meta {
          gap: 8px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-inline-meta span {
          min-height: 20px;
          padding: 0 8px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.045);
          font-size: 10px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block,
        .workspace-dock-detail.is-task-composer .workspace-dock-progress-panel,
        .workspace-dock-detail.is-task-composer .workspace-dock-note-panel,
        .workspace-dock-detail.is-task-composer .workspace-dock-switch-row {
          border: none;
          border-radius: 0;
          background: transparent;
          box-shadow: none;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block {
          padding: 0;
          gap: 8px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid {
          gap: 10px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid label {
          padding: 0;
          border: none;
          border-radius: 0;
          background: transparent;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-schedule-row {
          display: flex;
          align-items: flex-end;
          justify-content: space-between;
          gap: 10px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-schedule-control {
          width: fit-content;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-schedule-options {
          min-width: 0;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-schedule-options .workspace-dock-form-grid {
          grid-template-columns: repeat(2, 132px);
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-note-panel,
        .workspace-dock-detail.is-task-composer .workspace-dock-progress-panel {
          padding: 0;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-progress-meta strong,
        .workspace-dock-detail.is-task-composer .workspace-dock-switch-row strong {
          font-size: 12px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-note-panel p,
        .workspace-dock-detail.is-task-composer .workspace-dock-switch-row small {
          font-size: 11px;
          line-height: 1.6;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-detail-actions {
          padding: 0;
          border: none;
          border-radius: 0;
          background: transparent;
          font-size: 11px;
        }
        .workspace-dock-form-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
        }
        .workspace-dock-form-grid label,
        .workspace-dock-form-block {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .workspace-dock-form-block .ant-input,
        .workspace-dock-form-grid .ant-input,
        .workspace-dock-form-block .ant-input-number,
        .workspace-dock-form-grid .ant-input-number,
        .workspace-dock-form-block .ant-input-affix-wrapper,
        .workspace-dock-form-grid .ant-input-affix-wrapper,
        .workspace-dock-form-block .ant-select-selector,
        .workspace-dock-form-grid .ant-select-selector {
          background: rgba(255, 255, 255, 0.04);
          border-color: ${aura.border};
          color: ${aura.text};
          font-size: 11px;
          line-height: 1.5;
        }
        .workspace-dock-form-block .ant-select,
        .workspace-dock-form-grid .ant-select,
        .workspace-dock-form-block .ant-input-number,
        .workspace-dock-form-grid .ant-input-number {
          width: 100%;
        }
        .workspace-dock-form-block .ant-segmented,
        .workspace-dock-form-grid .ant-segmented {
          padding: 3px;
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid ${aura.border};
        }
        .workspace-dock-form-block .ant-segmented-item,
        .workspace-dock-form-grid .ant-segmented-item {
          font-size: 12px;
          color: ${aura.subtle};
        }
        .workspace-dock-form-block .ant-segmented-item-selected,
        .workspace-dock-form-grid .ant-segmented-item-selected {
          color: ${aura.text};
          background: rgba(138, 180, 255, 0.14);
          box-shadow: none;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid label > span,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block > span {
          font-size: 10px;
          letter-spacing: 0.02em;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-input,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-input,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-input-number,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-input-number,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-input-affix-wrapper,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-input-affix-wrapper,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-select-selector,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-select-selector {
          min-height: 34px;
          border-radius: 9px;
          font-size: 12px;
          background: rgba(255, 255, 255, 0.055);
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-input,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-input {
          padding-top: 6px;
          padding-bottom: 6px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-segmented,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-segmented {
          padding: 4px;
          border-radius: 10px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block .ant-segmented-item,
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid .ant-segmented-item {
          min-height: 30px;
          font-size: 11px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-run-schedule.ant-segmented {
          width: fit-content;
          height: 32px;
          padding: 2px;
          border-radius: 8px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-run-schedule .ant-segmented-item {
          min-height: 26px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-run-schedule .ant-segmented-item-label {
          padding-inline: 10px;
          line-height: 26px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-concurrency {
          width: 82px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-file-picker-button.ant-btn {
          height: 30px;
          padding: 0 8px;
          border-radius: 6px;
          font-size: 11px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-task-checkbox {
          flex-shrink: 0;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-task-checkbox .ant-checkbox-inner {
          width: 16px;
          height: 16px;
          border-radius: 4px;
          border-color: ${aura.border};
          background: rgba(255, 255, 255, 0.04);
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-task-checkbox .ant-checkbox-checked .ant-checkbox-inner {
          border-color: rgba(129, 216, 208, 0.68);
          background: #81D8D0;
        }
        .workspace-dock-template-select-option {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
        }
        .workspace-dock-template-select-option span:last-child {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .workspace-dock-template-select-dropdown .ant-select-item {
          border-radius: 8px;
          margin: 2px 6px;
          padding-top: 8px;
          padding-bottom: 8px;
        }
        .workspace-dock-template-select-dropdown .ant-select-item-option-content {
          overflow: visible;
        }
        .workspace-dock-detail-actions {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          color: ${aura.subtle};
          font-size: 10px;
        }
        .workspace-dock-switch-row {
          padding: 10px;
          border-radius: 9px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.035);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .workspace-dock-switch-row strong {
          display: block;
          color: ${aura.text};
          font-size: 12px;
          line-height: 1.2;
        }
        .workspace-dock-switch-row small {
          display: block;
          margin-top: 4px;
          color: ${aura.subtle};
          font-size: 10px;
          line-height: 1.45;
        }
        .workspace-dock-action-row {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          flex-wrap: wrap;
        }
        .workspace-dock-inline-action {
          min-height: 30px;
          padding: 0 11px;
          border-radius: 999px;
          border: 1px solid ${aura.border};
          background: rgba(255, 255, 255, 0.035);
          color: ${aura.subtle};
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          cursor: pointer;
          font-size: 10px;
          line-height: 1;
          transition: border-color 160ms ease, color 160ms ease, background 160ms ease, transform 160ms ease;
        }
        .workspace-dock-inline-action:hover:not(:disabled) {
          border-color: ${aura.border};
          color: ${aura.text};
          background: rgba(255, 255, 255, 0.065);
          transform: translateY(-1px);
        }
        .workspace-dock-inline-action:disabled {
          opacity: 0.38;
          cursor: not-allowed;
          transform: none;
        }
        .workspace-dock-inline-action.is-primary {
          border-color: rgba(138, 180, 255, 0.42);
          background: rgba(138, 180, 255, 0.14);
          color: #DCE8FF;
        }
        .workspace-dock-inline-action.is-primary:hover:not(:disabled) {
          border-color: rgba(138, 180, 255, 0.58);
          background: rgba(138, 180, 255, 0.2);
          color: #F5F8FF;
        }
        .workspace-dock-inline-action.is-icon-only {
          width: 32px;
          height: 32px;
          padding: 0;
          gap: 0;
          border-radius: 10px;
          border-color: rgba(138, 180, 255, 0.24);
          background:
            linear-gradient(180deg, rgba(138, 180, 255, 0.18), rgba(138, 180, 255, 0.08)),
            rgba(255, 255, 255, 0.035);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 8px 20px rgba(0, 0, 0, 0.18);
        }
        .workspace-dock-inline-action.is-icon-only:hover:not(:disabled) {
          border-color: rgba(138, 180, 255, 0.42);
          background:
            linear-gradient(180deg, rgba(138, 180, 255, 0.26), rgba(138, 180, 255, 0.12)),
            rgba(255, 255, 255, 0.05);
        }
        .workspace-dock-inline-action.is-icon-only .anticon {
          font-size: 14px;
        }
        .workspace-dock-progress-meta {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
        }
        .workspace-dock-progress-meta > span {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .workspace-dock-progress-meta.is-subtle {
          margin-top: 8px;
          background: transparent;
        }
        .workspace-dock-progress-meta.is-subtle span {
          display: inline;
          font-size: 10px;
        }
        .workspace-dock-history-head {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 10px;
        }
        .workspace-dock-log-caption {
          flex-shrink: 0;
          color: ${aura.subtle};
          font-size: 11px;
          line-height: 1;
        }
        .workspace-dock-diff-stats {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          font-size: 10px;
          line-height: 1;
          font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          white-space: nowrap;
        }
        .workspace-dock-diff-stats span {
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .workspace-dock-diff-stats .anticon {
          font-size: 10px;
          line-height: 1;
        }
        .workspace-dock-diff-stats .is-added {
          color: ${aura.success};
        }
        .workspace-dock-diff-stats .is-updated {
          color: ${aura.accent};
        }
        .workspace-dock-diff-stats .is-deleted {
          color: #F4A4C1;
        }
        .workspace-dock-diff-stats .is-download {
          color: #F6C35B;
        }
        .workspace-dock-diff-stats .is-download .anticon {
          color: #F6C35B;
        }
        .workspace-dock-diff-stats .is-sync {
          color: #8AB4FF;
        }
        .workspace-dock-diff-stats .is-sync .anticon {
          color: #8AB4FF;
        }
        .workspace-dock-history-bars {
          margin-top: 10px;
          height: 72px;
          display: grid;
          grid-template-columns: repeat(12, minmax(0, 1fr));
          gap: 5px;
          align-items: end;
        }
        .workspace-dock-history-bars span {
          display: block;
          min-height: 12px;
          border-radius: 999px 999px 4px 4px;
          background: rgba(138, 180, 255, 0.16);
          transition: height 220ms ease, background 220ms ease;
        }
        .workspace-dock-history-bars span.is-current {
          background: rgba(138, 180, 255, 0.42);
        }
        .workspace-dock-log-list {
          margin-top: 10px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .workspace-dock-log-list.is-detail {
          flex: 1 1 auto;
          width: 100%;
          height: 100%;
          min-height: 0;
          overflow: auto;
          background: transparent;
          scrollbar-width: none;
        }
        .workspace-dock-log-list.is-detail::-webkit-scrollbar {
          display: none;
          width: 0;
          height: 0;
        }
        .workspace-dock-log-row {
          display: grid;
          grid-template-columns: 54px 28px minmax(0, 1fr);
          gap: 6px;
          color: ${aura.muted};
          font-size: 10px;
          line-height: 1.45;
          align-items: baseline;
        }
        .workspace-dock-log-row span {
          color: rgba(255, 255, 255, 0.4);
        }
        .workspace-dock-log-row b {
          font-weight: 600;
          text-transform: lowercase;
        }
        .workspace-dock-log-row code {
          color: inherit;
          font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          white-space: normal;
          word-break: break-word;
        }
        .workspace-dock-log-row.is-ok b {
          color: ${aura.success};
        }
        .workspace-dock-log-row.is-warn b {
          color: ${aura.warning};
        }
        .workspace-dock-log-row.is-info b {
          color: ${aura.accent};
        }
        .workspace-dock-subtask-list {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .workspace-dock-subtask-item {
          padding: 8px 10px;
          border-radius: 9px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.028);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .workspace-dock-subtask-item strong,
        .workspace-dock-subtask-item span,
        .workspace-dock-subtask-item em {
          display: block;
        }
        .workspace-dock-subtask-item strong {
          color: ${aura.text};
          font-size: 11px;
        }
        .workspace-dock-subtask-item span {
          margin-top: 2px;
          color: ${aura.subtle};
          font-size: 9px;
        }
        .workspace-dock-subtask-item em {
          color: ${aura.subtle};
          font-size: 9px;
          font-style: normal;
          text-transform: uppercase;
        }
        @keyframes workspaceTaskPulse {
          0%, 100% {
            opacity: 0.42;
            transform: scaleX(0.35);
          }
          50% {
            opacity: 1;
            transform: scaleX(1);
          }
        }
        @keyframes workspaceMarquee {
          from {
            transform: translateX(-100%);
          }
          to {
            transform: translateX(100%);
          }
        }
        @keyframes workspaceDiffPulse {
          0%, 100% {
            opacity: 0.82;
            transform: translateY(0);
          }
          50% {
            opacity: 1;
            transform: translateY(-1px);
          }
        }
        @media (max-width: 960px) {
          .workspace-dock-panel.is-detail .workspace-dock-stack {
            grid-template-columns: minmax(0, 1fr);
          }
          .workspace-dock-panel.is-detail .workspace-dock-master {
            width: auto;
            border-right-color: transparent;
            border-bottom: 1px solid ${aura.borderSoft};
          }
          .workspace-dock-panel.is-detail .workspace-dock-detail {
            width: auto;
          }
        }
        @media (max-width: 767px) {
          .workspace-dock-panel,
          .workspace-dock-shell.is-session .workspace-dock-panel {
            top: 8px;
            right: 8px;
            bottom: 8px;
            width: calc(100% - 16px) !important;
            min-width: 0;
            height: auto;
            max-height: none;
          }
          .workspace-dock-form-grid,
          .workspace-dock-metric-grid {
            grid-template-columns: 1fr;
          }
          .workspace-dock-detail.is-task-composer .workspace-dock-schedule-row {
            align-items: stretch;
            flex-direction: column;
          }
          .workspace-dock-detail.is-task-composer .workspace-dock-concurrency {
            width: 100%;
          }
          .workspace-dock-detail.is-task-composer .workspace-dock-schedule-options .workspace-dock-form-grid {
            grid-template-columns: 1fr;
          }
          .workspace-dock-detail-actions {
            align-items: stretch;
            flex-direction: column;
          }
        }
      `}</style>

      <div className={`workspace-dock-shell ${activePanel ? 'is-open' : ''} ${sessionActive ? 'is-session' : ''}`}>
        <div
          className={`workspace-dock-hitbox ${activePanel ? 'is-open' : ''}`}
          onClick={onClose}
          aria-hidden={!activePanel}
        />

        <aside
          className={`workspace-dock-panel ${activePanel ? 'is-open' : ''} ${hasDetail ? 'is-detail' : ''}`}
        >
          {activePanel ? (
            <div className="workspace-dock-stack">
              <section className="workspace-dock-master">
                <div className="workspace-dock-toolbar">
                  <div className="workspace-dock-toolbar-search-row">
                    <Input
                      allowClear
                      prefix={<SearchOutlined />}
                      value={keyword}
                      onChange={(event) => setKeyword(event.target.value)}
                      placeholder={activePanel === 'templates' ? 'Search templates, domains, or adapters' : 'Search tasks or templates'}
                    />
                  </div>
                  {activePanel === 'tasks' && taskTemplateFilter ? (
                    <button type="button" className="workspace-dock-template-filter" onClick={() => setTaskTemplateFilter(null)}>
                      template: {taskTemplateFilter} <CloseOutlined />
                    </button>
                  ) : null}
                  {activePanel === 'templates' ? (
                    <Segmented
                      block
                      size="small"
                      value={templateFilter}
                      onChange={(value) => setTemplateFilter(value as TemplateFilter)}
                      options={[
                        { label: 'All', value: 'all' },
                        { label: 'Active', value: 'active' },
                        { label: 'Draft', value: 'draft' },
                        { label: 'Archived', value: 'deprecated' },
                      ]}
                    />
                  ) : (
                    <Segmented
                      block
                      size="small"
                      value={taskFilter}
                      onChange={(value) => setTaskFilter(value as TaskFilter)}
                      options={[
                        { label: 'All', value: 'all' },
                        { label: 'Running', value: 'running' },
                        { label: 'Queued', value: 'queued' },
                        { label: 'Failed', value: 'failed' },
                      ]}
                    />
                  )}
                </div>

                <div ref={bodyScrollRef} className="workspace-dock-body">
                  {activePanel === 'templates' ? renderTemplateList() : renderTaskList()}
                  {showBodyFade ? <div className="workspace-dock-bottom-fade" aria-hidden="true" /> : null}
                </div>
              </section>

              {activePanel === 'templates'
                ? (taskComposerOpen ? renderTaskComposerClean() : renderTemplateDetail())
                : activePanel === 'tasks'
                  ? renderTaskDetail()
                  : null}
            </div>
          ) : null}
        </aside>
      </div>
    </>
  );
};

export default WorkspaceDock;

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Input, InputNumber, Segmented, Select, Switch, Tooltip, Typography } from 'antd';
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
  SaveOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
  UndoOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { templates, tasks } from '@/pages/CollectConsole/shared/mockData';
import workspacePalette from './palette';
import type {
  CollectTask,
  TaskStatus,
  TemplateAsset,
  TemplateStatus,
} from '@/pages/CollectConsole/shared/types';

const { Text } = Typography;
const { TextArea } = Input;
const aura = workspacePalette;
const listPageSize = 4;
const listLoadThreshold = 56;

export type WorkspacePanel = 'templates' | 'tasks';

interface WorkspaceDockProps {
  activePanel: WorkspacePanel | null;
  sessionActive?: boolean;
  onToggle: (panel: WorkspacePanel) => void;
  onClose: () => void;
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
  active: { label: '已启用', color: '#31D26B' },
  draft: { label: '草稿', color: '#FBBF24' },
  deprecated: { label: '归档', color: aura.subtle },
};

const fieldHints = [
  'title',
  'source_url',
  'publish_time',
  'summary',
  'attachment_url',
  'category',
  'author',
  'abstract',
  'region_code',
  'detail_html',
] as const;

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

const nowLabel = () => new Date().toLocaleTimeString('zh-CN', {
  hour12: false,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

const pushTaskLog = (logs: TaskLog[], level: TaskLogLevel, message: string) => [
  { time: nowLabel(), level, message },
  ...logs,
].slice(0, 14);

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

const buildTemplateYaml = (item: TemplateAsset, adapter: string) => [
  `template: ${item.name}`,
  `title: ${item.title}`,
  `source: ${item.domain}`,
  `adapter: ${adapter}`,
  `version: ${item.version}`,
  `fields: ${item.fields}`,
  `quality_gate: ${item.quality}`,
  `dispatch_mode: task-center`,
].join('\n');

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

const SiteLogoMark: React.FC<{ site: SiteProfile }> = ({ site }) => (
  <i className="workspace-dock-meta-logo" style={{ '--brand-hue': site.hue } as React.CSSProperties} aria-hidden="true">
    {site.logo}
  </i>
);

const getTaskDisplay = (runtime: TaskRuntimeItem) => {
  if (runtime.controlState === 'canceled') {
    return {
      label: '已取消',
      color: 'rgba(255, 255, 255, 0.48)',
      icon: <CloseCircleOutlined />,
      isRunning: false,
    };
  }

  switch (runtime.status) {
    case 'running':
      return { label: '运行中', color: aura.accent, icon: <SyncOutlined spin />, isRunning: true };
    case 'queued':
      return { label: '队列中', color: '#FBBF24', icon: <ClockCircleOutlined />, isRunning: false };
    case 'completed':
      return { label: '已完成', color: '#31D26B', icon: <CheckCircleOutlined />, isRunning: false };
    case 'failed':
      return { label: '异常', color: '#F87171', icon: <WarningOutlined />, isRunning: false };
    case 'paused':
    default:
      return { label: '已暂停', color: '#FBBF24', icon: <PauseCircleOutlined />, isRunning: false };
  }
};

const WorkspaceDock: React.FC<WorkspaceDockProps> = ({
  activePanel,
  sessionActive = false,
  onToggle,
  onClose,
}) => {
  const bodyScrollRef = useRef<HTMLDivElement | null>(null);
  const [keyword, setKeyword] = useState('');
  const [templateFilter, setTemplateFilter] = useState<TemplateFilter>('all');
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('all');
  const [templateDetailMode, setTemplateDetailMode] = useState<TemplateDetailMode>('overview');
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string | null>(null);
  const [selectedTaskKey, setSelectedTaskKey] = useState<string | null>(null);
  const [templateEditSnapshot, setTemplateEditSnapshot] = useState<TemplateDraft | null>(null);
  const [templateVisibleCount, setTemplateVisibleCount] = useState(listPageSize);
  const [taskVisibleCount, setTaskVisibleCount] = useState(listPageSize);
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
  const [bodyScrollState, setBodyScrollState] = useState({ canScroll: false, isAtBottom: true });

  const [templateDrafts, setTemplateDrafts] = useState<Record<string, TemplateDraft>>(() => Object.fromEntries(
    templates.map((item) => [item.key, {
      adapter: item.adapter,
      outputTag: `${item.domain} / ${item.version}`,
      notes: item.description,
      yaml: buildTemplateYaml(item, item.adapter),
      savedAt: item.lastRun,
    }]),
  ) as Record<string, TemplateDraft>);
  const [taskItems, setTaskItems] = useState<CollectTask[]>(tasks);

  const [taskRuntime, setTaskRuntime] = useState<Record<string, TaskRuntimeItem>>(() => Object.fromEntries(
    tasks.map((item, index) => [item.key, buildTaskRuntimeItem(item, index)]),
  ) as Record<string, TaskRuntimeItem>);

  useEffect(() => {
    let cycle = 0;

    const timer = window.setInterval(() => {
      cycle += 1;
      setTaskRuntime((prev) => {
        let changed = false;
        const next = Object.fromEntries(Object.entries(prev).map(([taskKey, runtime], index) => {
          if (runtime.status !== 'running' || runtime.controlState === 'canceled') {
            return [taskKey, runtime];
          }

          changed = true;
          const delta = 84 + ((cycle + index) % 4) * 18;
          const progressBump = runtime.progress >= 98 ? 0 : 1 + ((cycle + index) % 2);
          const nextProgress = Math.min(runtime.progress + progressBump, 99);
          const nextRecordsValue = runtime.recordsValue + delta;
          const nextThroughput = Math.max(14, runtime.throughput + (((cycle + index) % 3) - 1) * 2);
          const nextHistory = [...runtime.history.slice(-11), nextRecordsValue];
          const messagePool = [
            'list worker accepted another page window',
            'detail parser returned new records batch',
            'attachment queue drained without retries',
            'field drift baseline remains within tolerance',
          ];

          return [taskKey, {
            ...runtime,
            progress: nextProgress,
            recordsValue: nextRecordsValue,
            throughput: nextThroughput,
            lastDelta: delta,
            history: nextHistory,
            logs: cycle % 2 === 0
              ? pushTaskLog(runtime.logs, cycle % 4 === 0 ? 'ok' : 'info', messagePool[(cycle + index) % messagePool.length])
              : runtime.logs,
          }];
        }));

        return changed ? next as Record<string, TaskRuntimeItem> : prev;
      });
    }, 1400);

    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (activePanel !== 'templates') {
      setSelectedTemplateKey(null);
      setTemplateDetailMode('overview');
    }
    if (activePanel !== 'tasks') {
      setSelectedTaskKey(null);
      setTaskComposerOpen(false);
    }
  }, [activePanel]);

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
    }), [keyword, pinnedTemplateKeys, templateFilter]);

  const allTaskRows = useMemo<TaskRow[]>(() => taskItems.map((item, index) => {
    const runtime = taskRuntime[item.key] ?? buildTaskRuntimeItem(item, index);
    const site = resolveSiteProfile(item.template);
    return {
      ...item,
      runtime,
      site,
      display: getTaskDisplay(runtime),
    };
  }), [taskItems, taskRuntime]);

  const taskRows = useMemo(() => allTaskRows
    .filter((item) => {
      const matchFilter = taskFilter === 'all' || item.runtime.status === taskFilter;
      const matchKeyword = !keyword
        || `${item.name} ${item.template} ${item.area} ${item.owner}`.toLowerCase().includes(keyword.toLowerCase());
      return matchFilter && matchKeyword;
    })
    .sort((left, right) => {
      const leftPinned = Boolean(pinnedTaskKeys[left.key]);
      const rightPinned = Boolean(pinnedTaskKeys[right.key]);
      if (leftPinned === rightPinned) return 0;
      return leftPinned ? -1 : 1;
    }), [allTaskRows, keyword, pinnedTaskKeys, taskFilter]);

  useEffect(() => {
    if (selectedTemplateKey && !templateRows.some((item) => item.key === selectedTemplateKey)) {
      setSelectedTemplateKey(null);
    }
  }, [selectedTemplateKey, templateRows]);

  useEffect(() => {
    setTemplateVisibleCount(listPageSize);
  }, [keyword, templateFilter]);

  useEffect(() => {
    if (selectedTaskKey && !taskRows.some((item) => item.key === selectedTaskKey)) {
      setSelectedTaskKey(null);
    }
  }, [selectedTaskKey, taskRows]);

  useEffect(() => {
    setTaskVisibleCount(listPageSize);
  }, [keyword, taskFilter]);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.key === selectedTemplateKey) ?? null,
    [selectedTemplateKey],
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
    [],
  );
  const selectedTask = useMemo(
    () => taskRows.find((item) => item.key === selectedTaskKey) ?? null,
    [selectedTaskKey, taskRows],
  );
  const selectedTemplateDraft = selectedTemplate ? templateDrafts[selectedTemplate.key] : null;
  const templateTaskCounts = useMemo<Record<string, number>>(() => Object.fromEntries(
    templates.map((item) => [item.key, taskItems.filter(
      (taskItem) => normalizeTemplateKey(taskItem.template) === normalizeTemplateKey(item.name),
    ).length]),
  ) as Record<string, number>, [taskItems]);
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

  const hasDetail = activePanel === 'templates'
    ? Boolean(selectedTemplate)
    : activePanel === 'tasks'
      ? Boolean(selectedTask) || taskComposerOpen
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

  useEffect(() => {
    if (!selectedTemplate || templateDetailMode === 'overview') {
      setTemplateEditSnapshot(null);
    }
  }, [selectedTemplate, templateDetailMode]);

  const loadMoreRows = useCallback(() => {
    if (activePanel === 'templates' && templateHasMore) {
      setTemplateVisibleCount((prev) => Math.min(prev + listPageSize, templateRows.length));
    }
    if (activePanel === 'tasks' && taskHasMore) {
      setTaskVisibleCount((prev) => Math.min(prev + listPageSize, taskRows.length));
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

    setBodyScrollState((prev) => (
      prev.canScroll === canScroll && prev.isAtBottom === isAtBottom
        ? prev
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
      window.requestAnimationFrame(() => {
        syncBodyScrollState();
      });
    };

    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => {
        window.requestAnimationFrame(() => {
          syncBodyScrollState();
        });
      })
      : null;

    observer?.observe(container);
    if (container.firstElementChild) {
      observer?.observe(container.firstElementChild);
    }

    container.addEventListener('scroll', handleScroll, { passive: true });
    window.requestAnimationFrame(() => {
      handleScroll();
      syncBodyScrollState();
    });

    return () => {
      observer?.disconnect();
      container.removeEventListener('scroll', handleScroll);
    };
  }, [activePanel, loadMoreRows, syncBodyScrollState, visibleTaskRows.length, visibleTemplateRows.length]);

  const updateTemplateDraft = (templateKey: string, patch: Partial<TemplateDraft>) => {
    setTemplateDrafts((prev) => ({
      ...prev,
      [templateKey]: {
        ...prev[templateKey],
        ...patch,
      },
    }));
  };

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
      templateParams: buildTaskTemplateParameterDrafts(template),
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
  }, [taskTemplateOptions]);

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
      templateParams: buildTaskTemplateParameterDrafts(templateValue),
      incrementalField: inferIncrementalField(templateValue),
      stopField: inferIncrementalField(templateValue),
      ...patch,
    }));
  }, []);

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

  const handleCreateTask = useCallback(() => {
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
      ],
      subIssues: [],
    };

    setKeyword('');
    setTaskFilter('all');
    setTaskItems((prev) => [nextTask, ...prev]);
    setTaskRuntime((prev) => ({
      ...prev,
      [nextTask.key]: buildTaskRuntimeItem(nextTask, 0),
    }));
    setSelectedTaskKey(nextTask.key);
    setTaskVisibleCount((prev) => Math.max(prev, listPageSize));
    setTaskComposerOpen(false);
    resetTaskComposer({
      template: normalizedTemplate,
      templateLocked: taskComposerDraft.templateLocked,
    });
  }, [resetTaskComposer, selectedTemplate, taskComposerDraft, taskTemplateOptions]);

  const updateTaskState = (
    taskKey: string,
    updater: (current: TaskRuntimeItem) => TaskRuntimeItem,
    logEntry?: { level: TaskLogLevel; message: string },
  ) => {
    setTaskRuntime((prev) => {
      const current = prev[taskKey];
      if (!current) return prev;
      const next = updater(current);
      return {
        ...prev,
        [taskKey]: logEntry
          ? { ...next, logs: pushTaskLog(next.logs, logEntry.level, logEntry.message) }
          : next,
      };
    });
  };

  const handlePauseTask = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...current,
    status: 'paused',
    throughput: 0,
    lastDelta: 0,
  }), {
    level: 'warn',
    message: 'operator paused task; workers entered hold state',
  });

  const handleResumeTask = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...current,
    status: 'running',
    controlState: null,
    throughput: Math.max(current.throughput, 18),
    lastDelta: Math.max(current.lastDelta, 96),
  }), {
    level: 'ok',
    message: 'task resumed and scheduler returned to live crawl',
  });

  const handleCancelTask = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...current,
    status: 'failed',
    controlState: 'canceled',
    throughput: 0,
    lastDelta: 0,
    downloadState: 'paused',
    syncState: 'canceled',
  }), {
    level: 'warn',
    message: 'task canceled by operator; current queue is being drained',
  });

  const handleStartDownload = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...(current.controlState === 'canceled' ? current : {
      ...current,
      downloadState: 'running',
    }),
  }), {
    level: 'ok',
    message: 'download lane activated; attachment workers resumed dispatch',
  });

  const handlePauseDownload = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...(current.controlState === 'canceled' ? current : {
      ...current,
      downloadState: 'paused',
    }),
  }), {
    level: 'warn',
    message: 'download lane paused; current attachment queue is held',
  });

  const handleStartSync = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...(current.controlState === 'canceled' ? current : {
      ...current,
      syncState: 'running',
    }),
  }), {
    level: 'ok',
    message: 'sync lane activated; downstream dataset writer resumed commit',
  });

  const handleCancelSync = (taskKey: string) => updateTaskState(taskKey, (current) => ({
    ...(current.controlState === 'canceled' ? current : {
      ...current,
      syncState: 'canceled',
    }),
  }), {
    level: 'warn',
    message: 'sync lane canceled; downstream commit window has been stopped',
  });

  const renderTemplateList = () => (
    <div className="workspace-dock-list">
      {visibleTemplateRows.map((item) => {
        const status = templateStatusMeta[item.status];
        const draft = templateDrafts[item.key];
        const isSelected = selectedTemplateKey === item.key;
        const isPinned = Boolean(pinnedTemplateKeys[item.key]);
        const site = resolveSiteProfile(item.name);
        const linkedTaskCount = templateTaskCounts[item.key] ?? item.taskCount;

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
                <span className="workspace-dock-card-icon"><TemplateGlyph kind={site.kind} /></span>
                <div className="workspace-dock-card-copy">
                  <div className="workspace-dock-card-titleline">
                    <Text strong>{item.title}</Text>
                    <span className="workspace-dock-card-pill" style={{ color: status.color, borderColor: `${status.color}33` }}>
                      {status.label}
                    </span>
                  </div>
                  <Text type="secondary">{draft.adapter}</Text>
                </div>
              </div>
              <div className="workspace-dock-card-side">
                <span
                  className={`workspace-dock-card-pin ${isPinned ? 'is-pinned' : ''}`}
                  title={isPinned ? 'Unpin template' : 'Pin template'}
                  onClick={(event) => handleTemplatePinClick(event, item.key)}
                >
                  <PushpinOutlined />
                </span>
                <span className="workspace-dock-card-score">{item.quality}%</span>
              </div>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={site} />{item.domain}</span>
              <span>{item.version} · {item.fields} 字段</span>
            </div>

            <div className="workspace-dock-card-footer">
              <span>{draft.savedAt}</span>
              <span className={linkedTaskCount ? 'is-linked' : ''}>
                {linkedTaskCount ? `${linkedTaskCount} 个任务` : '未调度'}
              </span>
            </div>

            <div className="workspace-dock-card-bar">
              <i style={{ width: `${item.quality}%`, background: aura.accent }} />
            </div>
          </button>
        );
      })}
      {!templateRows.length && <div className="workspace-dock-empty">没有符合条件的模板</div>}
    </div>
  );

  const renderTaskList = () => (
    <div className="workspace-dock-list">
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
                  </div>
                  <div className="workspace-dock-card-subline">
                    <Text type="secondary">{item.template}</Text>
                    <span className="workspace-dock-card-state" style={{ color: item.display.color }}>
                      {item.display.icon}
                      {item.display.label}
                    </span>
                  </div>
                </div>
              </div>
              <div className="workspace-dock-card-side">
                <span
                  className={`workspace-dock-card-pin ${isPinned ? 'is-pinned' : ''}`}
                  title={isPinned ? 'Unpin task' : 'Pin task'}
                  onClick={(event) => handleTaskPinClick(event, item.key)}
                >
                  <PushpinOutlined />
                </span>
                <span className={`workspace-dock-card-score ${item.display.isRunning ? 'is-live' : ''}`}>{item.runtime.progress}%</span>
              </div>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={item.site} />{stripDecorativeSuffix(item.area)}</span>
              <span>{formatCompactNumber(item.runtime.recordsValue)}</span>
              <span>{item.owner}</span>
            </div>

            <div className="workspace-dock-card-footer">
              <span>下次 {item.nextRun}</span>
              <span className={item.runtime.status === 'failed' || item.runtime.status === 'paused' ? 'is-alert' : ''}>
                延迟 {item.lag}
              </span>
            </div>

            <div className={`workspace-dock-card-bar ${item.display.isRunning ? 'is-running' : ''}`}>
              <i style={{ width: `${Math.max(item.runtime.progress, 6)}%`, background: item.display.color }} />
            </div>
          </button>
        );
      })}
      {!taskRows.length && <div className="workspace-dock-empty">没有符合条件的调度任务</div>}
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
    const incrementalSummary = !taskComposerDraft.incremental
      ? 'Full collect'
      : taskComposerDraft.incrementalMode === 'time_window'
        ? `${taskComposerDraft.incrementalField} lookback ${taskComposerDraft.lookbackValue} ${taskComposerDraft.lookbackUnit === 'hour' ? 'hours' : 'days'}, overlap ${taskComposerDraft.overlapMinutes} min`
        : `${taskComposerDraft.stopField} ${taskComposerDraft.stopComparator} ${taskComposerDraft.stopThreshold}, hit ${taskComposerDraft.stopConsecutivePages} pages`;
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

          <label className="workspace-dock-form-block">
            <span>Run Mode</span>
            <Segmented
              block
              size="small"
              value={taskComposerDraft.scheduleMode}
              onChange={(value) => handleTaskComposerModeChange(value as TaskComposerMode)}
              options={[
                { label: 'One-time', value: 'once' },
                { label: 'Recurring', value: 'recurring' },
              ]}
            />
          </label>

          {taskComposerDraft.scheduleMode === 'recurring' ? (
            <>
              <label className="workspace-dock-form-block">
                <span>Recurring Strategy</span>
                <Segmented
                  block
                  size="small"
                  value={taskComposerDraft.recurringMode}
                  onChange={(value) => updateTaskComposerDraft({ recurringMode: value as TaskRecurringMode })}
                  options={[
                    { label: 'Daily At', value: 'daily' },
                    { label: 'Every', value: 'interval' },
                  ]}
                />
              </label>

              {taskComposerDraft.recurringMode === 'daily' ? (
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
            </>
          ) : null}

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

          <div className="workspace-dock-switch-row">
            <div>
              <strong>Incremental Collect</strong>
              <small>Turn off for a full run. Turn on to collect with time-window or stop-condition rules.</small>
            </div>
            <Switch
              checked={taskComposerDraft.incremental}
              onChange={(checked) => updateTaskComposerDraft({ incremental: checked })}
            />
          </div>

          {taskComposerDraft.incremental ? (
            <div className="workspace-dock-progress-panel">
              <div className="workspace-dock-progress-meta">
                <strong>Incremental Rules</strong>
                <span>{incrementalSummary}</span>
              </div>

              <label className="workspace-dock-form-block" style={{ marginTop: 10 }}>
                <span>Incremental Strategy</span>
                <Segmented
                  block
                  size="small"
                  value={taskComposerDraft.incrementalMode}
                  onChange={(value) => updateTaskComposerDraft({ incrementalMode: value as TaskIncrementalMode })}
                  options={[
                    { label: 'Time Window', value: 'time_window' },
                    { label: 'Stop Condition', value: 'stop_condition' },
                  ]}
                />
              </label>

              {taskComposerDraft.incrementalMode === 'time_window' ? (
                <>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 10 }}>
                    <label>
                      <span>Time Field</span>
                      <Select
                        value={taskComposerDraft.incrementalField}
                        options={defaultIncrementalFields.map((item) => ({ value: item, label: item }))}
                        onChange={(value) => updateTaskComposerDraft({ incrementalField: value })}
                      />
                    </label>
                    <label>
                      <span>Overlap Minutes</span>
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
                      <span>Lookback Window</span>
                      <InputNumber
                        min={1}
                        max={90}
                        value={taskComposerDraft.lookbackValue}
                        onChange={(value) => updateTaskComposerDraft({ lookbackValue: value ?? 6 })}
                      />
                    </label>
                    <label>
                      <span>Window Unit</span>
                      <Select
                        value={taskComposerDraft.lookbackUnit}
                        options={[
                          { value: 'hour', label: 'Hour' },
                          { value: 'day', label: 'Day' },
                        ]}
                        onChange={(value) => updateTaskComposerDraft({ lookbackUnit: value as TaskLookbackUnit })}
                      />
                    </label>
                  </div>
                </>
              ) : (
                <>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 10 }}>
                    <label>
                      <span>Judge Field</span>
                      <Select
                        value={taskComposerDraft.stopField}
                        options={defaultStopFields.map((item) => ({ value: item, label: item }))}
                        onChange={(value) => updateTaskComposerDraft({ stopField: value })}
                      />
                    </label>
                    <label>
                      <span>Comparator</span>
                      <Select
                        value={taskComposerDraft.stopComparator}
                        options={[
                          { value: '<', label: '< threshold' },
                          { value: '<=', label: '<= threshold' },
                        ]}
                        onChange={(value) => updateTaskComposerDraft({ stopComparator: value as '<' | '<=' })}
                      />
                    </label>
                  </div>
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>Stop Threshold</span>
                      <Input
                        value={taskComposerDraft.stopThreshold}
                        placeholder="7d / 2026-06-01 / 1000"
                        onChange={(event) => updateTaskComposerDraft({ stopThreshold: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>Hit Pages</span>
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
                      <span>Max Empty Pages</span>
                      <InputNumber
                        min={1}
                        max={20}
                        value={taskComposerDraft.maxEmptyPages}
                        onChange={(value) => updateTaskComposerDraft({ maxEmptyPages: value ?? 2 })}
                      />
                    </label>
                    <label>
                      <span>Rule Note</span>
                      <Input value="Stop early when the captured sort field keeps falling below the threshold." readOnly />
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

    return (
      <section className="workspace-dock-detail">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TemplateGlyph kind={resolveSiteProfile(selectedTemplate.name).kind} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">{selectedTemplate.title}</Text>
              <Text type="secondary">{selectedTemplate.name}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            {templateDetailMode === 'overview' ? (
              <Tooltip title="编辑模板" placement="top">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn"
                  aria-label="编辑模板"
                  onClick={() => {
                    setTemplateEditSnapshot(selectedTemplateDraft);
                    setTemplateDetailMode('edit');
                  }}
                >
                  <EditOutlined />
                </button>
              </Tooltip>
            ) : (
              <>
                <Tooltip title="撤销修改" placement="top">
                  <button
                    type="button"
                    className="workspace-dock-detail-icon-btn"
                    aria-label="撤销修改"
                    onClick={() => {
                      if (templateEditSnapshot) {
                        setTemplateDrafts((prev) => ({
                          ...prev,
                          [selectedTemplate.key]: templateEditSnapshot,
                        }));
                      }
                    }}
                  >
                    <UndoOutlined />
                  </button>
                </Tooltip>
                <Tooltip title="保存模板" placement="top">
                  <button
                    type="button"
                    className="workspace-dock-detail-icon-btn"
                    aria-label="保存模板"
                    onClick={() => {
                      updateTemplateDraft(selectedTemplate.key, { savedAt: nowLabel() });
                      setTemplateDetailMode('overview');
                    }}
                  >
                    <SaveOutlined />
                  </button>
                </Tooltip>
              </>
            )}
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
              <span className="is-asset">资源 {linkedTaskCount}</span>
              <span className="is-quality">质量 {selectedTemplate.quality}%</span>
            </div>

            <div className="workspace-dock-chip-row">
              {fieldHints.slice(0, Math.min(selectedTemplate.fields, 6)).map((field) => (
                <span key={field} className="workspace-dock-mini-chip">{field}</span>
              ))}
              {selectedTemplate.fields > 6 ? (
                <span className="workspace-dock-mini-chip">+{selectedTemplate.fields - 6}</span>
              ) : null}
            </div>

            <div className="workspace-dock-code-panel">
              <small>YAML</small>
              <pre>{selectedTemplateDraft.yaml}</pre>
            </div>

            <div className="workspace-dock-note-panel">
              <small>说明</small>
              <p>{selectedTemplateDraft.notes}</p>
            </div>

            <div className="workspace-dock-detail-actions">
              <span>已关联 {linkedTaskCount} 个任务</span>
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
                    onToggle('tasks');
                  }}
                >
                  <PlusOutlined />
                  New Task
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
                    yaml: buildTemplateYaml(selectedTemplate, event.target.value),
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
              <span>沉淀说明</span>
              <TextArea
                value={selectedTemplateDraft.notes}
                onChange={(event) => updateTemplateDraft(selectedTemplate.key, { notes: event.target.value })}
                autoSize={{ minRows: 3, maxRows: 6 }}
              />
            </label>

            <div className="workspace-dock-detail-actions">
              <span>最近保存 {selectedTemplateDraft.savedAt}</span>
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
      <section className="workspace-dock-detail">
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
          z-index: 16;
        }
        .workspace-dock-hitbox {
          position: absolute;
          inset: 0;
          pointer-events: none;
        }
        .workspace-dock-hitbox.is-open {
          pointer-events: auto;
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
          position: fixed;
          top: 64px;
          right: 18px;
          width: min(396px, calc(100vw - 48px));
          height: min(488px, calc(100vh - 140px));
          max-height: min(488px, calc(100vh - 140px));
          border-radius: 12px;
          border: 1px solid ${aura.border};
          background:
            linear-gradient(180deg, rgba(31, 36, 48, 0.9), rgba(20, 24, 34, 0.88)),
            rgba(18, 22, 31, 0.92);
          box-shadow: 0 22px 54px rgba(0, 0, 0, 0.34);
          backdrop-filter: ${aura.backdrop};
          overflow: hidden;
          display: flex;
          flex-direction: column;
          opacity: 0;
          transform: translateY(-8px);
          transition: opacity 200ms ease, transform 220ms ease;
          pointer-events: none;
        }
        .workspace-dock-panel.is-open {
          opacity: 1;
          transform: translateY(0);
          pointer-events: auto;
        }
        .workspace-dock-panel.is-detail {
          width: min(812px, calc(100vw - 48px));
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
          grid-template-columns: 396px minmax(0, 1fr);
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
          border-right-color: ${aura.borderSoft};
        }
        .workspace-dock-toolbar {
          padding: 10px 12px;
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
          padding: 8px 10px 10px;
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
        .workspace-dock-card-side {
          flex-shrink: 0;
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 6px;
        }
        .workspace-dock-card-pin {
          width: 20px;
          height: 20px;
          border-radius: 999px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: rgba(255, 255, 255, 0.34);
          transition: color 160ms ease, background 160ms ease, transform 160ms ease;
        }
        .workspace-dock-card-pin:hover {
          color: ${aura.text};
          background: rgba(255, 255, 255, 0.06);
          transform: translateY(-1px);
        }
        .workspace-dock-card-pin.is-pinned {
          color: #F6C35B;
          background: rgba(246, 195, 91, 0.08);
        }
        .workspace-dock-card-pin .anticon {
          font-size: 11px;
          line-height: 1;
        }
        .workspace-dock-card-score {
          flex-shrink: 0;
          color: ${aura.subtle};
          font-size: 10px;
          line-height: 18px;
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
        .workspace-dock-card-footer .is-linked {
          color: #97B8FF;
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
          margin-top: -26px;
          height: 20px;
          background: linear-gradient(180deg, rgba(255, 255, 255, 0) 0%, rgba(125, 125, 126, 0.32) 100%);
          pointer-events: none;
          z-index: 2;
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
        .workspace-dock-log-panel {
          flex: 1;
          min-height: 0;
          display: flex;
          flex-direction: column;
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
          border-radius: 10px;
          border-color: ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.04);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-block {
          padding: 10px;
          gap: 8px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid {
          gap: 10px;
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-form-grid label {
          padding: 10px;
          border-radius: 10px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.028);
        }
        .workspace-dock-detail.is-task-composer .workspace-dock-note-panel,
        .workspace-dock-detail.is-task-composer .workspace-dock-progress-panel {
          padding: 12px;
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
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid ${aura.borderSoft};
          background: rgba(255, 255, 255, 0.035);
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
        .workspace-dock-detail.is-task-composer .workspace-dock-switch-row .ant-switch {
          flex-shrink: 0;
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
          flex: 1;
          min-height: 0;
          overflow: auto;
          scrollbar-width: none;
          overscroll-behavior: contain;
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
            border-right-color: transparent;
            border-bottom: 1px solid ${aura.borderSoft};
          }
        }
        @media (max-width: 767px) {
          .workspace-dock-panel,
          .workspace-dock-shell.is-session .workspace-dock-panel {
            left: 12px;
            right: 12px;
            width: auto;
            top: 64px;
            bottom: auto;
            height: min(74vh, calc(100vh - 76px));
            max-height: min(74vh, calc(100vh - 76px));
          }
          .workspace-dock-form-grid,
          .workspace-dock-metric-grid {
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
                      placeholder={activePanel === 'templates' ? '搜索模板、域名或适配器' : '搜索任务或模板'}
                    />
                    {activePanel === 'tasks' ? (
                      <div className="workspace-dock-toolbar-actions">
                        <button
                          type="button"
                          className="workspace-dock-toolbar-action-btn"
                          aria-label="New Task"
                          title="New Task"
                          onClick={() => openTaskComposer()}
                        >
                          <PlusOutlined />
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {activePanel === 'templates' ? (
                    <Segmented
                      block
                      size="small"
                      value={templateFilter}
                      onChange={(value) => setTemplateFilter(value as TemplateFilter)}
                      options={[
                        { label: '全部', value: 'all' },
                        { label: '启用', value: 'active' },
                        { label: '草稿', value: 'draft' },
                        { label: '归档', value: 'deprecated' },
                      ]}
                    />
                  ) : (
                    <Segmented
                      block
                      size="small"
                      value={taskFilter}
                      onChange={(value) => setTaskFilter(value as TaskFilter)}
                      options={[
                        { label: '全部', value: 'all' },
                        { label: '运行', value: 'running' },
                        { label: '队列', value: 'queued' },
                        { label: '异常', value: 'failed' },
                      ]}
                    />
                  )}
                </div>

                <div ref={bodyScrollRef} className="workspace-dock-body">
                  {activePanel === 'templates' ? renderTemplateList() : renderTaskList()}
                  {showBodyFade ? (
                    <div className="workspace-dock-bottom-fade" aria-hidden="true" />
                  ) : null}
                </div>
              </section>

              {activePanel === 'templates'
                ? renderTemplateDetail()
                : activePanel === 'tasks'
                  ? (taskComposerOpen ? renderTaskComposerClean() : renderTaskDetail())
                  : null}
            </div>
          ) : null}
        </aside>
      </div>
    </>
  );
};

export default WorkspaceDock;

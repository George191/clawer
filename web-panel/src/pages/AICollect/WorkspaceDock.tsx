import React, { useEffect, useMemo, useState } from 'react';
import { Input, Segmented, Tooltip, Typography } from 'antd';
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
  RadarChartOutlined,
  ReadOutlined,
  ScheduleOutlined,
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

const DockTemplateGlyph: React.FC = () => (
  <span className="workspace-glyph" aria-hidden="true">
    <CodeOutlined />
  </span>
);

const DockTaskGlyph: React.FC<{ active?: boolean }> = ({ active = false }) => (
  <span className={`workspace-glyph workspace-task-glyph ${active ? 'is-active' : ''}`} aria-hidden="true">
    <ScheduleOutlined />
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
  const [keyword, setKeyword] = useState('');
  const [templateFilter, setTemplateFilter] = useState<TemplateFilter>('all');
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('all');
  const [templateDetailMode, setTemplateDetailMode] = useState<TemplateDetailMode>('overview');
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string | null>(null);
  const [selectedTaskKey, setSelectedTaskKey] = useState<string | null>(null);
  const [templateEditSnapshot, setTemplateEditSnapshot] = useState<TemplateDraft | null>(null);

  const [templateDrafts, setTemplateDrafts] = useState<Record<string, TemplateDraft>>(() => Object.fromEntries(
    templates.map((item) => [item.key, {
      adapter: item.adapter,
      outputTag: `${item.domain} / ${item.version}`,
      notes: item.description,
      yaml: buildTemplateYaml(item, item.adapter),
      savedAt: item.lastRun,
    }]),
  ) as Record<string, TemplateDraft>);

  const [taskRuntime, setTaskRuntime] = useState<Record<string, TaskRuntimeItem>>(() => Object.fromEntries(
    tasks.map((item, index) => {
      const recordsValue = parseCompactNumber(item.records);
      return [item.key, {
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
      }];
    }),
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
    }
  }, [activePanel]);

  const templateRows = useMemo(() => templates.filter((item) => {
    const matchFilter = templateFilter === 'all' || item.status === templateFilter;
    const matchKeyword = !keyword
      || `${item.name} ${item.title} ${item.domain} ${item.adapter}`.toLowerCase().includes(keyword.toLowerCase());
    return matchFilter && matchKeyword;
  }), [keyword, templateFilter]);

  const taskRows = useMemo<TaskRow[]>(() => tasks.map((item) => {
    const runtime = taskRuntime[item.key];
    const site = resolveSiteProfile(item.template);
    return {
      ...item,
      runtime,
      site,
      display: getTaskDisplay(runtime),
    };
  }).filter((item) => {
    const matchFilter = taskFilter === 'all' || item.runtime.status === taskFilter;
    const matchKeyword = !keyword
      || `${item.name} ${item.template} ${item.area} ${item.owner}`.toLowerCase().includes(keyword.toLowerCase());
    return matchFilter && matchKeyword;
  }), [keyword, taskFilter, taskRuntime]);

  useEffect(() => {
    if (selectedTemplateKey && !templateRows.some((item) => item.key === selectedTemplateKey)) {
      setSelectedTemplateKey(null);
    }
  }, [selectedTemplateKey, templateRows]);

  useEffect(() => {
    if (selectedTaskKey && !taskRows.some((item) => item.key === selectedTaskKey)) {
      setSelectedTaskKey(null);
    }
  }, [selectedTaskKey, taskRows]);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.key === selectedTemplateKey) ?? null,
    [selectedTemplateKey],
  );
  const selectedTask = useMemo(
    () => taskRows.find((item) => item.key === selectedTaskKey) ?? null,
    [selectedTaskKey, taskRows],
  );
  const selectedTemplateDraft = selectedTemplate ? templateDrafts[selectedTemplate.key] : null;
  const hasRunningTask = useMemo(
    () => Object.values(taskRuntime).some((runtime) => runtime.status === 'running' && runtime.controlState !== 'canceled'),
    [taskRuntime],
  );
  const selectedTemplateSite = useMemo(
    () => (selectedTemplate ? resolveSiteProfile(selectedTemplate.name) : null),
    [selectedTemplate],
  );

  const hasDetail = activePanel === 'templates' ? Boolean(selectedTemplate) : activePanel === 'tasks' ? Boolean(selectedTask) : false;
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

  const updateTemplateDraft = (templateKey: string, patch: Partial<TemplateDraft>) => {
    setTemplateDrafts((prev) => ({
      ...prev,
      [templateKey]: {
        ...prev[templateKey],
        ...patch,
      },
    }));
  };

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
      {templateRows.map((item) => {
        const status = templateStatusMeta[item.status];
        const draft = templateDrafts[item.key];
        const isSelected = selectedTemplateKey === item.key;
        const site = resolveSiteProfile(item.name);

        return (
          <button
            type="button"
            key={item.key}
            className={`workspace-dock-card workspace-dock-selectable ${isSelected ? 'is-selected' : ''}`}
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
              <span className="workspace-dock-card-score">{item.quality}%</span>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={site} />{item.domain}</span>
              <span>{item.version}</span>
              <span>{item.fields} 字段</span>
              <span>{item.taskCount} 任务</span>
            </div>

            <div className="workspace-dock-card-bar">
              <i style={{ width: `${item.quality}%`, background: aura.accent }} />
            </div>
          </button>
        );
      })}
      {!templateRows.length && <div className="workspace-dock-empty">没有匹配到模板</div>}
    </div>
  );

  const renderTaskList = () => (
    <div className="workspace-dock-list">
      {taskRows.map((item) => {
        const isSelected = selectedTaskKey === item.key;

        return (
          <button
            type="button"
            key={item.key}
            className={`workspace-dock-card workspace-dock-selectable ${isSelected ? 'is-selected' : ''}`}
            onClick={() => setSelectedTaskKey(item.key)}
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
              <span className={`workspace-dock-card-score ${item.display.isRunning ? 'is-live' : ''}`}>{item.runtime.progress}%</span>
            </div>

            <div className="workspace-dock-card-meta">
              <span><SiteLogoMark site={item.site} />{stripDecorativeSuffix(item.area)}</span>
              <span>{formatCompactNumber(item.runtime.recordsValue)} records</span>
              <span>{item.nextRun}</span>
              <span>{item.owner}</span>
            </div>

            <div className={`workspace-dock-card-bar ${item.display.isRunning ? 'is-running' : ''}`}>
              <i style={{ width: `${Math.max(item.runtime.progress, 6)}%`, background: item.display.color }} />
            </div>
          </button>
        );
      })}
      {!taskRows.length && <div className="workspace-dock-empty">没有匹配到任务</div>}
    </div>
  );

  const renderTemplateDetail = () => {
    if (!selectedTemplate || !selectedTemplateDraft) return null;

    return (
      <section className="workspace-dock-detail">
        <div className="workspace-dock-detail-head">
          <div className="workspace-dock-detail-leading">
            <span className="workspace-dock-detail-icon"><TemplateGlyph kind={selectedTemplateSite?.kind ?? 'generic'} /></span>
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
              <span className="is-asset">资源 {selectedTemplate.taskCount}</span>
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
        .workspace-dock-rail {
          position: absolute;
          left: max(12px, calc(50% - 524px));
          bottom: 34px;
          display: flex;
          flex-direction: row;
          align-items: center;
          gap: 18px;
          pointer-events: auto;
          z-index: 2;
        }
        .workspace-dock-shell.is-session .workspace-dock-rail {
          left: max(12px, calc(50% - 492px));
          bottom: 35px;
        }
        .workspace-dock-trigger {
          border: none;
          background: transparent;
          color: rgba(255, 255, 255, 0.72);
          opacity: 0.82;
          padding: 0;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          transition: color 160ms ease, transform 160ms ease, opacity 160ms ease, filter 160ms ease;
        }
        .workspace-dock-trigger:hover,
        .workspace-dock-trigger.is-active {
          opacity: 1;
          color: ${aura.text};
          transform: scale(1.03);
          filter: drop-shadow(0 8px 20px rgba(0, 0, 0, 0.22));
        }
        .workspace-dock-trigger .workspace-glyph {
          width: 21px;
          height: 21px;
        }
        .workspace-dock-panel {
          position: absolute;
          left: max(22px, calc(50% - 468px));
          bottom: 74px;
          width: min(396px, calc(100vw - 48px));
          max-height: min(488px, calc(100vh - 140px));
          border-radius: 12px;
          border: 1px solid ${aura.border};
          background:
            linear-gradient(180deg, rgba(31, 36, 48, 0.9), rgba(20, 24, 34, 0.88)),
            rgba(18, 22, 31, 0.92);
          box-shadow: 0 22px 54px rgba(0, 0, 0, 0.34);
          backdrop-filter: ${aura.backdrop};
          overflow: hidden;
          opacity: 0;
          transform: translateY(16px);
          transition: opacity 200ms ease, transform 220ms ease;
          pointer-events: none;
        }
        .workspace-dock-shell.is-session .workspace-dock-panel {
          left: max(22px, calc(50% - 436px));
          bottom: 116px;
          max-height: min(488px, calc(100vh - 196px));
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
          height: 100%;
          display: grid;
          grid-template-columns: minmax(0, 1fr);
        }
        .workspace-dock-panel.is-detail .workspace-dock-stack {
          grid-template-columns: 396px minmax(0, 1fr);
        }
        .workspace-dock-master {
          min-width: 0;
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
          padding: 8px 10px 10px;
          overflow: auto;
          scrollbar-width: none;
          overscroll-behavior: contain;
        }
        .workspace-dock-body::-webkit-scrollbar,
        .workspace-dock-detail-body::-webkit-scrollbar {
          display: none;
          width: 0;
          height: 0;
        }
        .workspace-dock-list {
          display: flex;
          flex-direction: column;
          gap: 6px;
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
        .workspace-dock-detail {
          min-width: 0;
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
        .workspace-dock-detail-icon-btn.is-sync {
          color: #8AB4FF;
          background: rgba(138, 180, 255, 0.08);
        }
        .workspace-dock-detail-body {
          flex: 1;
          min-height: 0;
          padding: 10px 12px 12px;
          overflow: auto;
          scrollbar-width: none;
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
        .workspace-dock-form-block .ant-input-affix-wrapper,
        .workspace-dock-form-grid .ant-input-affix-wrapper {
          background: rgba(255, 255, 255, 0.04);
          border-color: ${aura.border};
          color: ${aura.text};
          font-size: 11px;
          line-height: 1.5;
        }
        .workspace-dock-detail-actions {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          color: ${aura.subtle};
          font-size: 10px;
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
          .workspace-dock-panel.is-detail {
            width: min(420px, calc(100vw - 36px));
          }
          .workspace-dock-panel.is-detail .workspace-dock-stack {
            grid-template-columns: minmax(0, 1fr);
          }
          .workspace-dock-panel.is-detail .workspace-dock-master {
            border-right-color: transparent;
            border-bottom: 1px solid ${aura.borderSoft};
          }
        }
        @media (max-width: 767px) {
          .workspace-dock-rail,
          .workspace-dock-shell.is-session .workspace-dock-rail {
            left: 18px;
            bottom: 92px;
            gap: 12px;
          }
          .workspace-dock-panel,
          .workspace-dock-shell.is-session .workspace-dock-panel {
            left: 12px;
            right: 12px;
            width: auto;
            bottom: 126px;
            max-height: min(74vh, calc(100vh - 132px));
          }
          .workspace-dock-shell.is-session .workspace-dock-panel {
            bottom: 152px;
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

        <div className="workspace-dock-rail">
          <Tooltip title="模板库" placement="top">
            <button
              type="button"
              aria-label="模板库"
              className={`workspace-dock-trigger ${activePanel === 'templates' ? 'is-active' : ''}`}
              onClick={() => onToggle('templates')}
            >
              <DockTemplateGlyph />
            </button>
          </Tooltip>
          <Tooltip title="采集任务" placement="top">
            <button
              type="button"
              aria-label="采集任务"
              className={`workspace-dock-trigger ${activePanel === 'tasks' ? 'is-active' : ''}`}
              onClick={() => onToggle('tasks')}
            >
              <DockTaskGlyph active={hasRunningTask} />
            </button>
          </Tooltip>
        </div>

        <aside className={`workspace-dock-panel ${activePanel ? 'is-open' : ''} ${hasDetail ? 'is-detail' : ''}`}>
          {activePanel ? (
            <div className="workspace-dock-stack">
              <section className="workspace-dock-master">
                <div className="workspace-dock-toolbar">
                  <Input
                    allowClear
                    prefix={<SearchOutlined />}
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                    placeholder={activePanel === 'templates' ? '搜索模板、域名或适配器' : '搜索任务、模板或负责人'}
                  />
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

                <div className="workspace-dock-body">
                  {activePanel === 'templates' ? renderTemplateList() : renderTaskList()}
                </div>
              </section>

              {activePanel === 'templates' ? renderTemplateDetail() : renderTaskDetail()}
            </div>
          ) : null}
        </aside>
      </div>
    </>
  );
};

export default WorkspaceDock;

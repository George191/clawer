import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Popconfirm, Segmented, Select, Switch, Tooltip, Typography, Upload } from 'antd';
import {
  BellOutlined,
  CaretRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CloseOutlined,
  CodeOutlined,
  DeploymentUnitOutlined,
  DeleteOutlined,
  DownOutlined,
  DownloadOutlined,
  EditOutlined,
  ExperimentOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  PushpinOutlined,
  RadarChartOutlined,
  ReadOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
  SyncOutlined,
  UploadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  createWorkspaceTask,
  deleteWorkspaceTask,
  fetchWorkspaceTask,
  fetchWorkspaceTaskLogRuns,
  fetchWorkspaceTaskLogs,
  fetchWorkspaceTasks,
  fetchWorkspaceTemplates,
  AI_ANALYZE_WS_URL,
  runWorkspaceTaskAction,
  uploadWorkspaceBatchInput,
  updateWorkspaceTemplate,
  type WorkspaceTask,
  type WorkspaceTaskLog,
  type WorkspaceTaskLogRun,
  type WorkspaceTemplate,
} from '@/services/aiApi';
import { useWebSocket } from '@/hooks/useWebSocket';
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
  analysisTemplate?: { yaml: string; adapter: string; adapterCode: string };
  onTemplateApply?: (draft: { yaml: string; adapter: string; adapterCode: string }) => void;
  onTaskCreated?: (task: WorkspaceTask) => void;
  focusTask?: WorkspaceTask | null;
  releaseTaskDefaults?: {
    concurrency: number;
    priority: number;
    respectRobots: boolean;
    driftGuard: boolean;
    params: Array<{ name: string; description: string; defaultValue: string; required: boolean }>;
    batch?: { filePath: string; paramName: string; batchSize: string; startLine: string; limit: string; delay: string } | null;
  };
}

interface TaskBatchConfig {
  filePath: string;
  paramName: string;
  batchSize: string;
  startLine: string;
  limit: string;
  delay: string;
}

type TemplateFilter = 'all' | TemplateStatus;
type TaskFilter = 'all' | TaskStatus;
type TemplateDetailMode = 'overview' | 'edit';
type TaskComposerMode = 'once' | 'recurring';
type TaskRecurringMode = 'daily' | 'interval';
type TaskIntervalUnit = 'minute' | 'hour';
type TaskLogLevel = 'info' | 'ok' | 'warn';
type SiteKind = 'news' | 'patent' | 'intelligence' | 'financial_report' | 'warning' | 'signal' | 'game' | 'generic';

interface TemplateDraft {
  adapter: string;
  adapterCode: string;
  notes: string;
  yaml: string;
  savedAt: string;
}

interface TaskLog {
  createdAt: string;
  time: string;
  level: TaskLogLevel;
  message: string;
  runId: string | null;
}

interface TaskRuntimeItem {
  status: TaskStatus;
  progress: number;
  recordsValue: number;
  throughput: number;
  lastDelta: number;
  history: number[];
  logs: TaskLog[];
  logRuns: WorkspaceTaskLogRun[];
  logRunCount: number;
  isRecurring: boolean;
  controlState: 'canceled' | null;
  downloadState: 'idle' | 'running' | 'paused';
  syncState: 'idle' | 'running' | 'paused' | 'canceled';
  insertedRecords: number;
  updatedRecords: number;
  deletedRecords: number;
  downloadedRecords: number;
  syncedRecords: number;
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
  incrementalField: string;
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

const normalizePanelTemplateYaml = (yaml: string) => {
  const lines = yaml.replace(/\r\n/g, '\n').split('\n')
    .filter((line) => !/^priority\s*:/.test(line));
  if (!lines.some((line) => /^download_use_proxy\s*:/.test(line))) {
    const downloadIndex = lines.findIndex((line) => /^download\s*:/.test(line));
    if (downloadIndex >= 0) lines.splice(downloadIndex, 0, 'download_use_proxy: null');
  }
  return lines.join('\n');
};

type TemplatePreviewStage = 'site' | 'param' | 'request' | 'response' | 'pagination' | 'fields' | 'dedup' | 'incremental' | 'download';

interface TemplatePreviewEntry {
  id: string;
  key: string;
  value: string;
  depth: number;
  group: boolean;
  stage: TemplatePreviewStage;
}

const templatePreviewStages: Array<{ id: TemplatePreviewStage; title: string; description: string }> = [
  { id: 'site', title: 'Site', description: 'base url, source identity and crawler baseline' },
  { id: 'param', title: 'Param', description: 'request parameters and batch inputs' },
  { id: 'request', title: 'Request', description: 'list/detail page routes and fetch contract' },
  { id: 'response', title: 'Response', description: 'response type and result path resolution' },
  { id: 'pagination', title: 'Pagination', description: 'page turning strategy and continuation cursor' },
  { id: 'fields', title: 'Fields', description: 'list/detail fields, selectors and output schema' },
  { id: 'dedup', title: 'Dedup', description: 'unique fields and record identity contract' },
  { id: 'incremental', title: 'Incremental', description: 'daily update field and date parsing format' },
  { id: 'download', title: 'Download', description: 'asset selectors, file types and download policy' },
];

const inferTemplatePreviewStage = (key: string, path: string): TemplatePreviewStage => {
  if (path === 'batch_params' || path.startsWith('batch_params.') || path === 'params' || path.startsWith('params')) return 'param';
  if (
    key === 'list_page'
    || path.startsWith('list_request')
    || key === 'detail_page'
    || key === 'detail_url_selector'
    || key === 'detail_url_selector_type'
    || path.startsWith('detail_request')
  ) return 'request';
  if (path === 'list_pagination' || path.startsWith('list_pagination.')) return 'pagination';
  if (path === 'dedup_fields' || path.startsWith('dedup_fields')) return 'dedup';
  if (path === 'incremental' || path.startsWith('incremental.')) return 'incremental';
  if (path === 'download_use_proxy' || path === 'download' || path.startsWith('download')) return 'download';
  if (path === 'list_fields' || path.startsWith('list_fields') || path === 'detail_fields' || path.startsWith('detail_fields')) return 'fields';
  if (['name', 'display_name', 'base_url', 'data_type', 'adapter', 'anti_crawl_enabled', 'description'].includes(path)) return 'site';
  if (['response_type', 'json_item_path', 'json_total_path', 'json_page_path', 'json_total_num_pages'].includes(key)) return 'response';
  return 'fields';
};

const parseTemplatePreviewEntries = (raw: string): TemplatePreviewEntry[] => {
  const entries: TemplatePreviewEntry[] = [];
  const lines = raw.replace(/\r\n/g, '\n').split('\n');
  const pathStack: Array<{ indent: number; path: string; listItem?: boolean }> = [];
  const listIndexes = new Map<string, number>();
  const collectBlockValue = (startIndex: number, parentIndent: number) => {
    const blockLines: string[] = [];
    let nextIndex = startIndex;
    while (nextIndex < lines.length) {
      const nextLine = lines[nextIndex];
      if (!nextLine.trim()) {
        blockLines.push('');
        nextIndex += 1;
        continue;
      }
      const nextIndent = nextLine.match(/^\s*/)?.[0].length ?? 0;
      if (nextIndent <= parentIndent) break;
      blockLines.push(nextLine.slice(Math.min(nextLine.length, parentIndent + 2)));
      nextIndex += 1;
    }
    return { nextIndex: nextIndex - 1, value: blockLines.join('\n').trimEnd() || 'null' };
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    while (
      pathStack.length
      && (
        pathStack[pathStack.length - 1].indent > indent
        || (
          pathStack[pathStack.length - 1].indent === indent
          && (
            !trimmed.startsWith('- ')
            || pathStack[pathStack.length - 1].listItem
          )
        )
      )
    ) pathStack.pop();
    const parentPath = pathStack[pathStack.length - 1]?.path ?? '';
    const depth = Math.max(0, Math.floor(indent / 2));

    if (trimmed.startsWith('- ')) {
      const listKey = `${parentPath}@${indent}`;
      const itemIndex = (listIndexes.get(listKey) ?? -1) + 1;
      const itemPath = `${parentPath || 'list'}[${itemIndex}]`;
      listIndexes.set(listKey, itemIndex);
      const itemValue = trimmed.slice(2).trim();
      const childMatch = itemValue.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
      if (childMatch) {
        entries.push({
          id: itemPath,
          key: itemPath,
          value: '',
          depth,
          group: true,
          stage: inferTemplatePreviewStage(parentPath.split('.').pop() ?? parentPath, itemPath),
        });
        const childPath = `${itemPath}.${childMatch[1]}`;
        const rawChildValue = (childMatch[2] ?? '').trim();
        let childValue = rawChildValue;
        if (rawChildValue === '|' || rawChildValue === '>') {
          const block = collectBlockValue(index + 1, indent);
          childValue = block.value;
          index = block.nextIndex;
        }
        entries.push({
          id: childPath,
          key: childPath,
          value: childValue || 'null',
          depth: depth + 1,
          group: !rawChildValue,
          stage: inferTemplatePreviewStage(childMatch[1], childPath),
        });
        pathStack.push({ indent, path: itemPath, listItem: true });
        if (!rawChildValue) pathStack.push({ indent: indent + 2, path: childPath });
      } else {
        entries.push({
          id: itemPath,
          key: itemPath,
          value: itemValue || 'null',
          depth,
          group: false,
          stage: inferTemplatePreviewStage(parentPath.split('.').pop() ?? parentPath, itemPath),
        });
      }
      continue;
    }

    const keyMatch = trimmed.match(/^([A-Za-z_][\w-]*):(?:\s*(.*))?$/);
    if (!keyMatch) continue;
    const key = keyMatch[1];
    const rawValue = (keyMatch[2] ?? '').trim();
    let value = rawValue;
    if (rawValue === '|' || rawValue === '>') {
      const block = collectBlockValue(index + 1, indent);
      value = block.value;
      index = block.nextIndex;
    }
    const path = parentPath ? `${parentPath}.${key}` : key;
    entries.push({
      id: path,
      key: path,
      value: value || 'null',
      depth,
      group: !rawValue,
      stage: inferTemplatePreviewStage(key, path),
    });
    if (!rawValue) pathStack.push({ indent, path });
  }

  return entries;
};

const templatePreviewListItemKey = (key: string) => key.match(
  /^(?:(?:params|dedup_fields|list_fields|detail_fields|download)\[\d+\]|batch_params\.param_name\[\d+\])(?=\.|$)/,
)?.[0] ?? null;

const renderCompactTemplatePreview = (yaml: string) => {
  const entries = parseTemplatePreviewEntries(yaml);
  return (
    <div className="workspace-dock-template-preview">
      {templatePreviewStages.map((stage) => {
        const stageEntries = entries.filter((entry) => entry.stage === stage.id);
        const valueEntries = stageEntries.filter((entry) => !entry.group);
        const displayEntries = stageEntries.filter(
          (entry) => (
            !entry.group
            || entry.key === 'params'
            || entry.key === 'batch_params'
            || entry.key === 'batch_params.param_name'
            || entry.key === 'list_request'
            || entry.key === 'list_request.headers'
            || entry.key === 'detail_request'
            || entry.key === 'detail_request.headers'
          ),
        );
        if (!valueEntries.length) return null;
        return (
          <section className="ai-template-stage-section" key={stage.id}>
            <div className="ai-template-stage-head">
              <div className="ai-template-stage-copy">
                <span className="ai-template-stage-title">{stage.title}</span>
                <small>{stage.description}</small>
              </div>
              <div className="ai-template-stage-actions">
                <span>{valueEntries.length}</span>
              </div>
            </div>
            <div className="ai-template-stage-body">
              {displayEntries.map((entry, index) => {
                const rawLabel = entry.key.split('.').pop() ?? entry.key;
                const isBatchParamNameItem = /^batch_params\.param_name\[\d+\]$/.test(entry.key);
                const label = isBatchParamNameItem
                  ? entry.value.replace(/^['"]|['"]$/g, '')
                  : /^dedup_fields\[\d+\]$/.test(entry.key)
                  ? 'field'
                  : rawLabel.replace(/^([a-z_]+)\[(\d+)\]$/, '$1 $2');
                const listItemKey = templatePreviewListItemKey(entry.key);
                const previousListItemKey = templatePreviewListItemKey(displayEntries[index - 1]?.key ?? '');
                const fieldSectionRoot = entry.key.match(/^(list_fields|detail_fields)(?:\[|\.|$)/)?.[1] ?? null;
                const previousFieldSectionRoot = displayEntries[index - 1]?.key.match(/^(list_fields|detail_fields)(?:\[|\.|$)/)?.[1] ?? null;
                const showFieldSectionLabel = Boolean(fieldSectionRoot && fieldSectionRoot !== previousFieldSectionRoot);
                const isDownloadField = Boolean(listItemKey?.startsWith('download['));
                const previousIsDownloadField = Boolean(previousListItemKey?.startsWith('download['));
                const showDownloadFieldsLabel = stage.id === 'download' && isDownloadField && !previousIsDownloadField;
                const pageSectionRoot = stage.id === 'request'
                  ? entry.key.match(/^(list_page|list_request)(?:\[|\.|$)/)
                    || entry.key === 'detail_url_selector'
                    || entry.key === 'detail_url_selector_type'
                    ? 'list'
                    : entry.key.match(/^(detail_page|detail_request)(?:\[|\.|$)/)
                      ? 'detail'
                      : null
                  : null;
                const previousPageSectionRoot = stage.id === 'request'
                  ? displayEntries[index - 1]?.key.match(/^(list_page|list_request)(?:\[|\.|$)/)
                    || displayEntries[index - 1]?.key === 'detail_url_selector'
                    || displayEntries[index - 1]?.key === 'detail_url_selector_type'
                    ? 'list'
                    : displayEntries[index - 1]?.key.match(/^(detail_page|detail_request)(?:\[|\.|$)/)
                      ? 'detail'
                      : null
                  : null;
                const showPageSectionLabel = Boolean(
                  pageSectionRoot && pageSectionRoot !== previousPageSectionRoot,
                );
                const showListDash = Boolean(listItemKey && listItemKey !== previousListItemKey);
                const isRootGroup = entry.group && (
                  entry.key === 'params'
                  || entry.key === 'batch_params'
                );
                const isRequestHeadersGroup = entry.group
                  && (entry.key === 'list_request.headers' || entry.key === 'detail_request.headers');
                const isRequestContainerGroup = entry.group
                  && (entry.key === 'list_request' || entry.key === 'detail_request');
                const isYamlListItem = Boolean(listItemKey);
                const displayDepth = entry.depth
                  - (isYamlListItem ? 1 : 0)
                  + (stage.id === 'download' && isYamlListItem ? 1 : 0)
                  + (pageSectionRoot ? 1 : 0);
                return (
                  <React.Fragment key={entry.id}>
                    {showFieldSectionLabel ? (
                      <div className="ai-template-fields-subsection" role="heading" aria-level={3}>
                        {fieldSectionRoot === 'detail_fields' ? 'Details fields' : 'List fields'}
                      </div>
                    ) : null}
                    {showDownloadFieldsLabel ? (
                      <div className="ai-template-fields-subsection" role="heading" aria-level={3}>
                        Fields
                      </div>
                    ) : null}
                    {showPageSectionLabel ? (
                      <div className="ai-template-fields-subsection" role="heading" aria-level={3}>
                        {pageSectionRoot === 'detail' ? 'Detail' : 'List'}
                      </div>
                    ) : null}
                    <div
                      className={`ai-template-field ${entry.group && !isRequestHeadersGroup && !isRequestContainerGroup ? 'is-group' : ''} ${isRootGroup ? 'is-root-group' : ''} ${isYamlListItem ? 'is-yaml-list-item' : ''} ${showListDash ? 'has-yaml-dash' : ''}`}
                      style={{ ['--ai-template-depth' as string]: String(displayDepth) }}
                    >
                      <div className="ai-template-field-key">
                        {listItemKey ? <i className="ai-template-field-dash" aria-hidden="true">-</i> : null}
                        <span>{label}</span>
                      </div>
                      {entry.group || isBatchParamNameItem ? null : (
                        <div className={`ai-template-field-value ${entry.key === 'description' ? 'is-rich' : ''}`}>
                          <pre>{entry.value.replace(/^['"]|['"]$/g, '')}</pre>
                        </div>
                      )}
                    </div>
                  </React.Fragment>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
};

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

const formatDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

const sortTaskLogRuns = (runs: WorkspaceTaskLogRun[]) => [...runs].sort((left, right) => (
  new Date(right.started_at).getTime() - new Date(left.started_at).getTime()
));

const mergeTaskLogRuns = (
  incoming: WorkspaceTaskLogRun[],
  existing: WorkspaceTaskLogRun[],
) => {
  const incomingIds = new Set(incoming.map((run) => run.id));
  return sortTaskLogRuns([
    ...incoming,
    ...existing.filter((run) => !incomingIds.has(run.id)),
  ]);
};

const mergeTaskLogRun = (
  runs: WorkspaceTaskLogRun[],
  log: WorkspaceTaskLog,
): { runs: WorkspaceTaskLogRun[]; isNewRun: boolean } => {
  if (!log.run_id) return { runs, isNewRun: false };

  const existingRun = runs.find((run) => run.id === log.run_id);
  if (!existingRun) {
    return {
      runs: sortTaskLogRuns([{
        id: log.run_id,
        started_at: log.created_at,
        ended_at: log.created_at,
        log_count: 1,
      }, ...runs]),
      isNewRun: true,
    };
  }

  return {
    runs: sortTaskLogRuns(runs.map((run) => run.id === log.run_id ? {
      ...run,
      ended_at: log.created_at > run.ended_at ? log.created_at : run.ended_at,
      log_count: run.log_count + 1,
    } : run)),
    isNewRun: false,
  };
};

const consoleLogPrefixPattern = /^(\[)(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})(:\s*)([A-Z]+)(\/)([^\]]+)(\])(\s*)/;

const renderConsoleLogMessage = (message: string): React.ReactNode => {
  const match = message.match(consoleLogPrefixPattern);
  if (!match) return message;
  const level = match[4].toLowerCase();
  return (
    <>
      <span className="workspace-dock-log-bracket">{match[1]}</span>
      <span className="workspace-dock-log-time">{match[2]}</span>
      <span className="workspace-dock-log-bracket">{match[3]}</span>
      <span className={`workspace-dock-log-level is-${level}`}>{match[4]}</span>
      <span className="workspace-dock-log-bracket">{match[5]}</span>
      <span className="workspace-dock-log-process">{match[6]}</span>
      <span className="workspace-dock-log-bracket">{match[7]}</span>
      {match[8]}
      {message.slice(match[0].length)}
    </>
  );
};

const formatTemplateDomain = (value: string) => ({
  '政务公告': 'Government notices',
  'PDF 附件': 'PDF attachments',
  '质量校验': 'Quality checks',
  '历史公告': 'Legacy notices',
}[value] ?? value);

const formatTaskNextRunLabel = (value: string) => {
  const exact = {
    '持续运行': 'Continuous',
    '等待恢复': 'Awaiting recovery',
    '人工确认': 'Manual review',
    '一次性任务': 'One-time task',
  }[value];
  if (exact) return exact;

  const daily = value.match(/^每天\s+(.+)$/);
  if (daily) return `Daily at ${daily[1]}`;
  const interval = value.match(/^每\s+(\d+)\s+(分钟|小时)$/);
  if (interval) {
    const unit = interval[2] === '分钟' ? 'minute' : 'hour';
    return `Every ${interval[1]} ${unit}${interval[1] === '1' ? '' : 's'}`;
  }
  return value;
};

const GamepadGlyph = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
    <path d="M7.1 6h9.8a4.5 4.5 0 0 1 4.2 3l1.5 4.3a3.2 3.2 0 0 1-5 3.6l-1.8-1.4H8.2l-1.8 1.4a3.2 3.2 0 0 1-5-3.6L2.9 9A4.5 4.5 0 0 1 7.1 6Zm-1.6 4v1.5H4V13h1.5v1.5H7V13h1.5v-1.5H7V10H5.5Zm11.8.5a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2Zm2.7 2.7a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2Z" />
  </svg>
);

const FinancialReportGlyph = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M6.5 3.5h7l4 4v13h-11z" />
    <path d="M13.5 3.5v4h4" />
    <path d="M9.5 16.5v-3M12 16.5v-5M14.5 16.5v-2" />
  </svg>
);

const siteKindMeta: Record<SiteKind, { icon: React.ReactNode; label: string; tint: string }> = {
  financial_report: { icon: <FinancialReportGlyph />, label: 'Financial report', tint: '#69D3B0' },
  news: { icon: <ReadOutlined />, label: '新闻', tint: '#BFA8FF' },
  patent: { icon: <ExperimentOutlined />, label: '专利', tint: '#8AB4FF' },
  intelligence: { icon: <RadarChartOutlined />, label: '情报', tint: '#7DD3FC' },
  warning: { icon: <BellOutlined />, label: '告警', tint: '#F6C35B' },
  signal: { icon: <DeploymentUnitOutlined />, label: '信号', tint: '#65D5A3' },
  game: { icon: <GamepadGlyph />, label: '游戏', tint: '#FFFFFF' },
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

const buildTaskRuntimeItem = (item: CollectTask): TaskRuntimeItem => {
  const recordsValue = parseCompactNumber(item.records);
  return {
    status: item.status,
    progress: item.progress,
    recordsValue,
    throughput: 0,
    lastDelta: 0,
    history: createHistory(recordsValue),
    logs: [],
    logRuns: [],
    logRunCount: 0,
    isRecurring: false,
    controlState: null,
    downloadState: item.status === 'running' ? 'running' : item.status === 'completed' ? 'paused' : 'idle',
    syncState: item.status === 'running' || item.status === 'completed' ? 'running' : 'idle',
    insertedRecords: 0,
    updatedRecords: 0,
    deletedRecords: 0,
    downloadedRecords: 0,
    syncedRecords: 0,
  };
};

const versionTemplateUrl = (url?: string, updatedAt?: string) => {
  if (!url || !updatedAt) return url;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}v=${encodeURIComponent(updatedAt)}`;
};

const mapWorkspaceTemplate = (item: WorkspaceTemplate): TemplateAsset => ({
  key: item.id,
  name: item.name,
  title: item.title,
  domain: item.domain,
  adapter: item.adapter,
  version: item.version,
  status: item.status,
  fields: extractListFields(item.yaml_content ?? '').length,
  quality: Number(item.metadata?.quality ?? 0),
  lastRun: item.updated_at,
  owner: item.owner,
  description: item.description,
  action: 'Open template',
  icon: 'code',
  taskCount: item.task_count,
  faviconUrl: item.favicon_url,
  dataType: item.data_type ?? extractTemplateDataType(item.yaml_content ?? ''),
  templateUrl: versionTemplateUrl(item.template, item.updated_at),
  templatePath: item.template_path,
});

const fetchArtifactText = async (url: string, label: string) => {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${label} request failed with status ${response.status}`);
  }
  return response.text();
};

const mapWorkspaceTask = (item: WorkspaceTask): CollectTask => ({
  key: item.id,
  name: item.name.replace(/\s+task$/i, ''),
  template: `${item.template_name}@${item.template_version}`,
  group: 'prototype',
  area: `${item.template_name.replace(/_/g, ' ')} workspace`,
  status: item.status,
  progress: item.progress,
  records: String(item.records),
  lag: item.status === 'running' ? 'live' : '-',
  nextRun: item.schedule?.mode === 'once'
    ? '一次性任务'
    : String(item.schedule?.label ?? (item.status === 'running' ? 'Continuous' : 'Waiting')),
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
    createdAt: log.created_at,
    time: formatDateTime(log.created_at),
    level: log.level,
    message: log.message,
    runId: log.run_id,
  })),
  logRuns: item.log_runs ?? [],
  logRunCount: item.log_run_count ?? item.log_runs?.length ?? 0,
  isRecurring: item.schedule?.mode === 'recurring'
    ? ['daily', 'interval'].includes(String(item.schedule?.recurring_mode ?? ''))
    : ['daily', 'interval'].includes(String(item.schedule?.mode ?? '')),
  controlState: item.control_state,
  downloadState: item.download_state,
  syncState: item.sync_state,
  insertedRecords: item.inserted_records ?? 0,
  updatedRecords: item.updated_records ?? 0,
  deletedRecords: item.deleted_records ?? 0,
  downloadedRecords: item.downloaded_records ?? 0,
  syncedRecords: item.synced_records ?? 0,
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
  if (/financial[_\s-]*report/.test(text)) return 'financial_report';
  if (text.includes('game') || text.includes('游戏')) return 'game';
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

const defaultIncrementalFields = [
  'modified',
  'date',
  'issue_time',
  'patent.publication_date',
  'updated_at',
  'created_at',
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

const stripYamlScalar = (value: string) => {
  const trimmed = value.trim();
  if (trimmed === 'null' || trimmed === '~') return '';
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
    || (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) return trimmed.slice(1, -1);
  return trimmed;
};

const extractTaskTemplateParameterDrafts = (yaml: string) => {
  const lines = yaml.replace(/\r\n/g, '\n').split('\n');
  const paramsIndex = lines.findIndex((line) => /^params:\s*(?:#.*)?$/.test(line));
  if (paramsIndex < 0) return [];

  const params: Array<{ key: string; label: string; placeholder: string; value: string }> = [];
  let current: { key: string; label: string; placeholder: string; value: string } | null = null;

  for (let index = paramsIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (trimmed && !/^\s/.test(line)) break;
    if (!trimmed || trimmed.startsWith('#')) continue;

    const nameMatch = trimmed.match(/^-\s+name:\s*(.*)$/);
    if (nameMatch) {
      if (current) params.push(current);
      const name = stripYamlScalar(nameMatch[1]);
      current = { key: name, label: name, placeholder: name, value: '' };
      continue;
    }
    if (!current) continue;

    const propertyMatch = trimmed.match(/^(description|default|required):\s*(.*)$/);
    if (!propertyMatch) continue;
    const [, property, rawValue] = propertyMatch;
    if (property === 'required') {
      if (stripYamlScalar(rawValue) === 'true') current.label = `${current.key} *`;
      continue;
    }
    if (property === 'description' && (rawValue === '>' || rawValue === '|')) {
      const propertyIndent = line.match(/^\s*/)?.[0].length ?? 0;
      const blockLines: string[] = [];
      while (index + 1 < lines.length) {
        const nextLine = lines[index + 1];
        const nextTrimmed = nextLine.trim();
        const nextIndent = nextLine.match(/^\s*/)?.[0].length ?? 0;
        if (nextTrimmed && nextIndent <= propertyIndent) break;
        index += 1;
        if (nextTrimmed) blockLines.push(nextTrimmed);
      }
      current.placeholder = blockLines.join(' ');
      continue;
    }
    const value = stripYamlScalar(rawValue);
    if (property === 'description') current.placeholder = value || current.key;
    if (property === 'default') current.value = value;
  }

  if (current) params.push(current);
  return params.filter((param) => Boolean(param.key));
};

const extractTaskBatchConfig = (yaml: string): TaskBatchConfig | null => {
  const lines = yaml.replace(/\r\n/g, '\n').split('\n');
  const batchIndex = lines.findIndex((line) => /^batch_params:\s*(?:#.*)?$/.test(line));
  if (batchIndex < 0) return null;
  const values: Record<string, string> = {};
  const paramNames: string[] = [];
  let readingParamNames = false;
  for (let index = batchIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() && !/^\s/.test(line)) break;
    const listItem = line.trim().match(/^-\s+(.+)$/);
    if (readingParamNames && listItem) {
      paramNames.push(stripYamlScalar(listItem[1]));
      continue;
    }
    const match = line.trim().match(/^([a-z_]+):\s*(.*?)\s*(?:#.*)?$/);
    if (match) {
      values[match[1]] = stripYamlScalar(match[2]);
      readingParamNames = match[1] === 'param_name' && !values[match[1]];
    }
  }
  const paramName = values.param_name || paramNames.filter(Boolean).join(',');
  if (!paramName) return null;
  return {
    filePath: values.file_path ?? '',
    paramName,
    batchSize: values.batch_size || '1',
    startLine: values.start_line || '0',
    limit: values.limit ?? '',
    delay: values.delay || '0',
  };
};

const inferIncrementalField = (templateValue: string) => {
  const normalized = templateValue.toLowerCase();
  if (normalized.includes('patent')) return 'patent.publication_date';
  if (normalized.includes('warn')) return 'issue_time';
  if (normalized.includes('planet') || normalized.includes('blacksky') || normalized.includes('satellite')) return 'modified';
  return 'date';
};

const formatTaskNextRun = (draft: TaskComposerDraft) => {
  if (draft.scheduleMode === 'once') return 'One-time task';
  if (draft.recurringMode === 'daily') return `Daily at ${draft.dailyTime}`;
  const unit = draft.intervalUnit === 'minute' ? 'minute' : 'hour';
  return `Every ${draft.intervalValue} ${unit}${draft.intervalValue === 1 ? '' : 's'}`;
};

const formatIncrementalSummary = (draft: TaskComposerDraft) => {
  if (!draft.incremental) return '全量采集';
  return `${draft.incrementalField}：Redis 水位线减 1 天`;
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
  const [failedSource, setFailedSource] = useState('');
  const source = faviconUrl || site.faviconUrl;
  return (
    <i className="workspace-dock-meta-logo" style={{ '--brand-hue': site.hue } as React.CSSProperties} aria-hidden="true">
      {source && source !== failedSource
        ? <img src={source} alt="" referrerPolicy="no-referrer" onError={() => setFailedSource(source)} />
        : site.logo}
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
  onTaskCreated,
  focusTask,
  releaseTaskDefaults,
}) => {
  const bodyScrollRef = useRef<HTMLDivElement>(null);
  const templateSaveTimerRef = useRef<number | null>(null);
  const templateListRequestRef = useRef<Promise<WorkspaceTemplate[]> | null>(null);
  const templateDetailRequestRef = useRef(0);
  const taskListRequestRef = useRef<Promise<WorkspaceTask[]> | null>(null);
  const taskListRevisionRef = useRef(0);
  const [templates, setTemplates] = useState<TemplateAsset[]>([]);
  const [keyword, setKeyword] = useState('');
  const [templateFilter, setTemplateFilter] = useState<TemplateFilter>('all');
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('all');
  const [taskTemplateFilter, setTaskTemplateFilter] = useState<string | null>(null);
  const [templateDetailMode, setTemplateDetailMode] = useState<TemplateDetailMode>('overview');
  const [templateDetailLoading, setTemplateDetailLoading] = useState(false);
  const [templateDetailError, setTemplateDetailError] = useState('');
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
    incrementalField: 'date',
    maxEmptyPages: 2,
  });
  const [taskConcurrency, setTaskConcurrency] = useState(releaseTaskDefaults?.concurrency ?? 4);
  const [taskPriority, setTaskPriority] = useState(releaseTaskDefaults?.priority ?? 50);
  const [taskRespectRobots, setTaskRespectRobots] = useState(releaseTaskDefaults?.respectRobots ?? true);
  const [taskDriftGuard, setTaskDriftGuard] = useState(releaseTaskDefaults?.driftGuard ?? true);
  const [taskBatchInput, setTaskBatchInput] = useState(false);
  const [taskBatchFile, setTaskBatchFile] = useState('');
  const [taskBatchObjectKey, setTaskBatchObjectKey] = useState('');
  const [taskBatchUploading, setTaskBatchUploading] = useState(false);
  const [taskBatchUploadError, setTaskBatchUploadError] = useState('');
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
    setTaskPriority(releaseTaskDefaults.priority);
    setTaskRespectRobots(releaseTaskDefaults.respectRobots);
    setTaskDriftGuard(releaseTaskDefaults.driftGuard);
  }, [
    releaseTaskDefaults?.concurrency,
    releaseTaskDefaults?.priority,
    releaseTaskDefaults?.driftGuard,
    releaseTaskDefaults?.respectRobots,
  ]);

  const [templateDrafts, setTemplateDrafts] = useState<Record<string, TemplateDraft>>({});
  const [taskItems, setTaskItems] = useState<CollectTask[]>([]);
  const [taskRuntime, setTaskRuntime] = useState<Record<string, TaskRuntimeItem>>({});
  const [selectedTaskLogRunId, setSelectedTaskLogRunId] = useState<string | null>(null);
  const [historicalTaskLogs, setHistoricalTaskLogs] = useState<{
    taskId: string;
    runId: string;
    logs: TaskLog[];
  } | null>(null);
  const [taskLogsLoading, setTaskLogsLoading] = useState(false);
  const [taskLogRunsLoading, setTaskLogRunsLoading] = useState(false);
  const [taskLogRunOpen, setTaskLogRunOpen] = useState(false);

  const applyWorkspaceTask = useCallback((item: WorkspaceTask) => {
    const mappedTask = mapWorkspaceTask(item);
    setTaskItems((current) => (
      current.some((task) => task.key === item.id)
        ? current.map((task) => (task.key === item.id ? mappedTask : task))
        : [mappedTask, ...current]
    ));
    setTaskRuntime((current) => {
      const runtime = mapWorkspaceTaskRuntime(item);
      const existing = current[item.id];
      if (!runtime.logs.length && existing?.logs.length) {
        runtime.logs = existing.logs;
      }
      if (existing) {
        runtime.logRuns = mergeTaskLogRuns(runtime.logRuns, existing.logRuns);
        runtime.logRunCount = Math.max(runtime.logRunCount, existing.logRunCount);
      }
      return { ...current, [item.id]: runtime };
    });
  }, []);

  const applyWorkspaceTasks = useCallback((items: WorkspaceTask[]) => {
    setTaskItems(items.map(mapWorkspaceTask));
    setTaskRuntime((current) => Object.fromEntries(items.map((item) => {
      const runtime = mapWorkspaceTaskRuntime(item);
      const existing = current[item.id];
      if (!runtime.logs.length && existing?.logs.length) {
        runtime.logs = existing.logs;
      }
      if (existing) {
        runtime.logRuns = mergeTaskLogRuns(runtime.logRuns, existing.logRuns);
        runtime.logRunCount = Math.max(runtime.logRunCount, existing.logRunCount);
      }
      return [item.id, runtime];
    })));
  }, []);

  const applyWorkspaceTemplates = useCallback((items: WorkspaceTemplate[]) => {
    setTemplates(items.map(mapWorkspaceTemplate));
    setTemplateDrafts((current) => Object.fromEntries(items.map((item) => [item.id, {
      adapter: item.adapter,
      adapterCode: item.adapter_code ?? current[item.id]?.adapterCode ?? '',
      notes: item.description,
      yaml: normalizePanelTemplateYaml(item.yaml_content ?? current[item.id]?.yaml ?? ''),
      savedAt: item.updated_at,
    }])));
  }, []);

  const refreshWorkspaceTemplates = useCallback(async () => {
    if (!templateListRequestRef.current) {
      templateListRequestRef.current = fetchWorkspaceTemplates();
    }
    try {
      applyWorkspaceTemplates(await templateListRequestRef.current);
    } finally {
      templateListRequestRef.current = null;
    }
  }, [applyWorkspaceTemplates]);

  const refreshWorkspaceTasks = useCallback(async () => {
    if (!taskListRequestRef.current) {
      taskListRequestRef.current = fetchWorkspaceTasks();
    }
    const request = taskListRequestRef.current;
    const revision = taskListRevisionRef.current;
    try {
      const items = await request;
      if (revision === taskListRevisionRef.current) {
        applyWorkspaceTasks(items);
      }
    } finally {
      if (taskListRequestRef.current === request) {
        taskListRequestRef.current = null;
      }
    }
  }, [applyWorkspaceTasks]);

  useEffect(() => {
    if (activePanel !== 'templates' && activePanel !== 'tasks') return;
    void refreshWorkspaceTemplates().catch((error) => {
      console.error('Failed to refresh templates', error);
    });
  }, [activePanel, refreshWorkspaceTemplates]);

  useEffect(() => {
    if (activePanel !== 'tasks') return undefined;
    void refreshWorkspaceTasks().catch((error) => {
      console.error('Failed to refresh tasks', error);
    });
    return undefined;
  }, [activePanel, refreshWorkspaceTasks]);

  useEffect(() => {
    if (activePanel !== 'tasks' || !focusTask) return;
    const mappedTask = mapWorkspaceTask(focusTask);
    setKeyword('');
    setTaskFilter('all');
    setTaskTemplateFilter(null);
    setTaskItems((current) => (
      current.some((task) => task.key === focusTask.id)
        ? current.map((task) => (task.key === focusTask.id ? mappedTask : task))
        : [mappedTask, ...current]
    ));
    setTaskRuntime((current) => ({
      ...current,
      [focusTask.id]: mapWorkspaceTaskRuntime(focusTask),
    }));
    setSelectedTaskKey(focusTask.id);
  }, [activePanel, focusTask]);

  const handleTaskSocketMessage = useCallback((rawMessage: string) => {
    try {
      const message = JSON.parse(rawMessage) as {
        type?: string;
        task_id?: string;
        data?: WorkspaceTask | WorkspaceTaskLog;
      };
      if (message.type === 'task_detail' && message.data) {
        applyWorkspaceTask(message.data as WorkspaceTask);
      } else if (message.type === 'task_log' && message.task_id && message.data) {
        const log = message.data as WorkspaceTaskLog;
        const mappedLog: TaskLog = {
          createdAt: log.created_at,
          time: formatDateTime(log.created_at),
          level: log.level,
          message: log.message,
          runId: log.run_id,
        };
        setTaskRuntime((current) => {
          const runtime = current[message.task_id!];
          if (!runtime) return current;
          const duplicate = runtime.logs.some((item) => (
            item.runId === mappedLog.runId
            && item.createdAt === mappedLog.createdAt
            && item.message === mappedLog.message
          ));
          if (duplicate) return current;
          const { runs: nextRuns, isNewRun } = mergeTaskLogRun(runtime.logRuns, log);
          const latestRunId = nextRuns[0]?.id ?? null;
          const currentLogRunId = runtime.logs[runtime.logs.length - 1]?.runId ?? null;
          const nextLogs = log.run_id === latestRunId
            ? currentLogRunId === latestRunId
              ? [...runtime.logs, mappedLog].slice(-200)
              : [mappedLog]
            : runtime.logs;
          return {
            ...current,
            [message.task_id!]: {
              ...runtime,
              logs: nextLogs,
              logRuns: nextRuns,
              logRunCount: runtime.logRunCount + (isNewRun ? 1 : 0),
            },
          };
        });
        setHistoricalTaskLogs((current) => {
          if (
            !current
            || current.taskId !== message.task_id
            || current.runId !== log.run_id
          ) return current;
          const duplicateHistoricalLog = current.logs.some((item) => (
            item.runId === mappedLog.runId
            && item.createdAt === mappedLog.createdAt
            && item.message === mappedLog.message
          ));
          return duplicateHistoricalLog
            ? current
            : { ...current, logs: [...current.logs, mappedLog].slice(-200) };
        });
      } else if (message.type === 'task_deleted' && message.task_id) {
        setTaskItems((current) => current.filter((item) => item.key !== message.task_id));
        setTaskRuntime((current) => {
          const next = { ...current };
          delete next[message.task_id!];
          return next;
        });
        setSelectedTaskKey((current) => (current === message.task_id ? null : current));
      }
    } catch (error) {
      console.error('Failed to handle task WebSocket message', error);
    }
  }, [applyWorkspaceTask]);

  const { connected: taskSocketConnected, send: sendTaskSocketMessage } = useWebSocket(
    AI_ANALYZE_WS_URL,
    { onMessage: handleTaskSocketMessage },
  );

  useEffect(() => {
    const selectedStatus = selectedTaskKey ? taskRuntime[selectedTaskKey]?.status : null;
    if (
      activePanel !== 'tasks'
      || !selectedTaskKey
      || taskSocketConnected
      || selectedStatus !== 'running'
    ) return undefined;
    let active = true;
    const refreshSelectedTask = async () => {
      try {
        const task = await fetchWorkspaceTask(selectedTaskKey);
        if (active) applyWorkspaceTask(task);
      } catch (error) {
        console.error('Failed to refresh selected task', error);
      }
    };
    const timer = window.setInterval(() => {
      void refreshSelectedTask();
    }, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [
    activePanel,
    applyWorkspaceTask,
    selectedTaskKey,
    selectedTaskKey ? taskRuntime[selectedTaskKey]?.status : null,
    taskSocketConnected,
  ]);

  useEffect(() => {
    if (activePanel !== 'tasks' || !taskSocketConnected) return undefined;
    const channel = 'tasks';
    sendTaskSocketMessage(JSON.stringify({ type: 'subscribe', channel }));
    return () => {
      sendTaskSocketMessage(JSON.stringify({ type: 'unsubscribe', channel }));
    };
  }, [activePanel, sendTaskSocketMessage, taskSocketConnected]);

  useEffect(() => {
    if (activePanel !== 'tasks' || !selectedTaskKey || !taskSocketConnected) return undefined;
    const channel = `task:${selectedTaskKey}`;
    sendTaskSocketMessage(JSON.stringify({ type: 'subscribe', channel }));
    return () => {
      sendTaskSocketMessage(JSON.stringify({ type: 'unsubscribe', channel }));
    };
  }, [activePanel, selectedTaskKey, sendTaskSocketMessage, taskSocketConnected]);

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

  const allTaskRows = useMemo<TaskRow[]>(() => taskItems.map((item) => {
    const runtime = taskRuntime[item.key] ?? buildTaskRuntimeItem(item);
    const site = resolveSiteProfile(item.template);
    const template = templates.find((candidate) => normalizeTemplateKey(candidate.name) === normalizeTemplateKey(item.template));
    return {
      ...item,
      runtime,
      site: template ? { ...site, kind: inferSiteKind(template.dataType || template.name), faviconUrl: template.faviconUrl } : site,
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
  const taskBatchConfig = useMemo(() => {
    const template = templates.find((item) => `${item.name}@${item.version}` === taskComposerDraft.template);
    return extractTaskBatchConfig(template ? templateDrafts[template.key]?.yaml ?? '' : '');
  }, [taskComposerDraft.template, templateDrafts, templates]);
  useEffect(() => {
    setTaskBatchInput(false);
    setTaskBatchFile('');
    setTaskBatchObjectKey('');
    setTaskBatchUploadError('');
    if (!taskBatchConfig) return;
    setTaskBatchParam(taskBatchConfig.paramName);
    setTaskBatchSize(Number(taskBatchConfig.batchSize) || 1);
    setTaskBatchStartLine(Number(taskBatchConfig.startLine) || 0);
    const limit = Number(taskBatchConfig.limit);
    setTaskBatchLimit(Number.isFinite(limit) && limit > 0 ? limit : null);
    setTaskBatchDelay(Number(taskBatchConfig.delay) || 0);
  }, [taskBatchConfig, taskComposerDraft.template]);
  const buildTaskTemplateParameterDrafts = useCallback((templateValue: string) => {
    const template = templates.find((item) => `${item.name}@${item.version}` === templateValue);
    return extractTaskTemplateParameterDrafts(template ? templateDrafts[template.key]?.yaml ?? '' : '');
  }, [templateDrafts, templates]);
  const selectedTask = useMemo(
    () => allTaskRows.find((item) => item.key === selectedTaskKey) ?? null,
    [allTaskRows, selectedTaskKey],
  );
  const latestTaskLogRunId = selectedTask?.runtime.logRuns[0]?.id ?? null;

  useEffect(() => {
    setSelectedTaskLogRunId(null);
    setHistoricalTaskLogs(null);
    setTaskLogRunOpen(false);
  }, [selectedTaskKey]);

  useEffect(() => {
    setSelectedTaskLogRunId((current) => (
      current && selectedTask?.runtime.logRuns.some((run) => run.id === current)
        ? current
        : latestTaskLogRunId
    ));
  }, [latestTaskLogRunId, selectedTask]);

  const handleTaskLogRunChange = useCallback(async (runId: string) => {
    if (!selectedTaskKey) return;
    setSelectedTaskLogRunId(runId);
    if (runId === latestTaskLogRunId) {
      setHistoricalTaskLogs(null);
      return;
    }
    setTaskLogsLoading(true);
    try {
      const logs = await fetchWorkspaceTaskLogs(selectedTaskKey, runId);
      setHistoricalTaskLogs({
        taskId: selectedTaskKey,
        runId,
        logs: logs.map((log) => ({
          createdAt: log.created_at,
          time: formatDateTime(log.created_at),
          level: log.level,
          message: log.message,
          runId: log.run_id,
        })),
      });
    } catch (error) {
      console.error('Failed to load task run logs', error);
      setHistoricalTaskLogs({ taskId: selectedTaskKey, runId, logs: [] });
    } finally {
      setTaskLogsLoading(false);
    }
  }, [latestTaskLogRunId, selectedTaskKey]);

  useEffect(() => {
    if (
      !selectedTaskLogRunId
      || selectedTaskLogRunId === latestTaskLogRunId
      || taskLogsLoading
      || (
        historicalTaskLogs?.taskId === selectedTaskKey
        && historicalTaskLogs.runId === selectedTaskLogRunId
      )
    ) return;
    void handleTaskLogRunChange(selectedTaskLogRunId);
  }, [
    handleTaskLogRunChange,
    historicalTaskLogs?.runId,
    historicalTaskLogs?.taskId,
    latestTaskLogRunId,
    selectedTaskKey,
    selectedTaskLogRunId,
    taskLogsLoading,
  ]);

  const handleTaskLogRunPopupScroll = useCallback(async (
    event: React.UIEvent<HTMLDivElement>,
  ) => {
    const target = event.currentTarget;
    const reachedBottom = target.scrollTop + target.clientHeight >= target.scrollHeight - 24;
    const runtime = selectedTaskKey ? taskRuntime[selectedTaskKey] : null;
    if (
      !reachedBottom
      || !selectedTaskKey
      || !runtime
      || taskLogRunsLoading
      || runtime.logRuns.length >= runtime.logRunCount
    ) return;

    setTaskLogRunsLoading(true);
    try {
      const nextRuns = await fetchWorkspaceTaskLogRuns(
        selectedTaskKey,
        runtime.logRuns.length,
      );
      setTaskRuntime((current) => {
        const currentRuntime = current[selectedTaskKey];
        if (!currentRuntime) return current;
        const knownIds = new Set(currentRuntime.logRuns.map((run) => run.id));
        return {
          ...current,
          [selectedTaskKey]: {
            ...currentRuntime,
            logRuns: [
              ...currentRuntime.logRuns,
              ...nextRuns.filter((run) => !knownIds.has(run.id)),
            ],
          },
        };
      });
    } catch (error) {
      console.error('Failed to load more task log runs', error);
    } finally {
      setTaskLogRunsLoading(false);
    }
  }, [selectedTaskKey, taskLogRunsLoading, taskRuntime]);
  const selectedTemplateDraft = selectedTemplate ? templateDrafts[selectedTemplate.key] : null;
  const templateTaskCounts = useMemo<Record<string, number>>(() => Object.fromEntries(
    templates.map((item) => [item.key, Math.max(item.taskCount, taskItems.filter(
      (taskItem) => normalizeTemplateKey(taskItem.template) === normalizeTemplateKey(item.name),
    ).length)]),
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

  const taskInsertedLines = selectedTask?.runtime.insertedRecords ?? 0;
  const taskUpdatedLines = selectedTask?.runtime.updatedRecords ?? 0;
  const taskDeletedLines = selectedTask?.runtime.deletedRecords ?? 0;
  const taskDownloadedResources = selectedTask?.runtime.downloadedRecords ?? 0;
  const taskSyncedRecords = selectedTask?.runtime.syncedRecords ?? 0;

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
        yaml: normalizePanelTemplateYaml(patch.yaml ?? analysisTemplate?.yaml ?? current.yaml),
        adapter: patch.adapter ?? analysisTemplate?.adapter ?? current.adapter,
        adapterCode: patch.adapterCode ?? analysisTemplate?.adapterCode ?? current.adapterCode,
      });
    }
  };

  useEffect(() => {
    if (templateDetailMode !== 'edit' || !selectedTemplate || !selectedTemplateDraft) return undefined;
    if (templateSaveTimerRef.current) window.clearTimeout(templateSaveTimerRef.current);
    templateSaveTimerRef.current = window.setTimeout(() => {
      void updateWorkspaceTemplate(selectedTemplate.key, {
        yaml_content: normalizePanelTemplateYaml(selectedTemplateDraft.yaml),
        adapter: selectedTemplateDraft.adapter,
        adapter_code: selectedTemplateDraft.adapterCode,
        description: selectedTemplateDraft.notes,
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
      templateParams: buildTaskTemplateParameterDrafts(template),
      incremental: scheduleMode === 'recurring',
      incrementalField: defaultField,
      maxEmptyPages: 2,
      ...patch,
    };
  }, [buildTaskTemplateParameterDrafts, taskTemplateOptions]);

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
      ...patch,
    }));
  }, [buildTaskTemplateParameterDrafts]);

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

  const handleTaskBatchUpload = useCallback(async (file: File) => {
    const [templateName, templateVersion = 'v1.0'] = taskComposerDraft.template.split('@');
    if (!templateName) return;
    setTaskBatchUploading(true);
    setTaskBatchUploadError('');
    try {
      const uploaded = await uploadWorkspaceBatchInput(file, templateName, templateVersion);
      setTaskBatchFile(uploaded.filename);
      setTaskBatchObjectKey(uploaded.object_key);
    } catch (error) {
      setTaskBatchFile('');
      setTaskBatchObjectKey('');
      setTaskBatchUploadError('Upload failed; the file was not stored in MinIO.');
      console.error('Failed to upload batch input', error);
    } finally {
      setTaskBatchUploading(false);
    }
  }, [taskComposerDraft.template]);

  const handleCreateTask = useCallback(async () => {
    const normalizedTemplate = taskComposerDraft.template
      || (selectedTemplate ? `${selectedTemplate.name}@${selectedTemplate.version}` : '')
      || taskTemplateOptions[0]?.value
      || '';
    const matchedTemplate = templates.find((item) => `${item.name}@${item.version}` === normalizedTemplate) ?? null;
    const fallbackName = normalizedTemplate
      ? normalizeTemplateKey(normalizedTemplate).replace(/_/g, ' ')
      : 'New collect';
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
        `priority=${taskPriority}; concurrency=${taskConcurrency}; robots=${taskRespectRobots}; drift_guard=${taskDriftGuard}${taskBatchInput ? `; batch=${taskBatchFile || 'pending'}:${taskBatchParam}` : ''}`,
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
          priority: taskPriority,
          concurrency: taskConcurrency,
          incremental: taskComposerDraft.incremental,
          incremental_field: taskComposerDraft.incrementalField,
          respect_robots: taskRespectRobots,
          drift_guard: taskDriftGuard,
          batch: taskBatchInput ? {
            file: taskBatchFile,
            object_key: taskBatchObjectKey,
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
      taskListRevisionRef.current += 1;
      taskListRequestRef.current = null;
      setKeyword('');
      setTaskFilter('all');
      setTaskTemplateFilter(null);
      setTaskItems((prev) => [createdTask, ...prev.filter((item) => item.key !== createdTask.key)]);
      setTaskRuntime((prev) => ({ ...prev, [created.id]: mapWorkspaceTaskRuntime(created) }));
      setSelectedTaskKey(created.id);
      setTaskVisibleCount(defaultPageSize);
      setTaskComposerOpen(false);
      resetTaskComposer({
        template: normalizedTemplate,
        templateLocked: taskComposerDraft.templateLocked,
      });
      onTaskCreated?.(created);
    } catch (error) {
      console.error('Failed to create task', error);
    }
  }, [onTaskCreated, resetTaskComposer, selectedTemplate, taskBatchDelay, taskBatchFile, taskBatchInput, taskBatchLimit, taskBatchObjectKey, taskBatchParam, taskBatchSize, taskBatchStartLine, taskComposerDraft, taskConcurrency, taskDriftGuard, taskPriority, taskRespectRobots, taskTemplateOptions]);

  const handleWorkspaceTaskAction = useCallback(async (
    taskKey: string,
    action: Parameters<typeof runWorkspaceTaskAction>[1],
  ) => {
    try {
      const updated = await runWorkspaceTaskAction(taskKey, action);
      applyWorkspaceTask(updated);
    } catch (error) {
      console.error(`Failed to run task action: ${action}`, error);
    }
  }, [applyWorkspaceTask]);

  const handlePauseTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'pause');
  const handleStartTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'start');
  const handleRestartTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'restart');
  const handleResumeTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'resume');
  const handleCancelTask = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'cancel');
  const handleStartDownload = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'start_download');
  const handlePauseDownload = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'pause_download');
  const handleStartSync = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'start_sync');
  const handlePauseSync = (taskKey: string) => void handleWorkspaceTaskAction(taskKey, 'pause_sync');
  const handleDeleteTask = useCallback(async (taskKey: string) => {
    try {
      await deleteWorkspaceTask(taskKey);
      setTaskItems((prev) => prev.filter((item) => item.key !== taskKey));
      setTaskRuntime((prev) => {
        const next = { ...prev };
        delete next[taskKey];
        return next;
      });
      setSelectedTaskKey((current) => (current === taskKey ? null : current));
    } catch (error) {
      console.error('Failed to delete task', error);
    }
  }, []);

  const handleTemplateSelect = useCallback(async (item: TemplateAsset) => {
    const requestId = templateDetailRequestRef.current + 1;
    templateDetailRequestRef.current = requestId;
    setSelectedTemplateKey(item.key);
    setTemplateDetailError('');
    setTemplateDetailLoading(true);
    if (!taskComposerOpen) setTemplateDetailMode('overview');

    try {
      if (!item.templateUrl) throw new Error('Template artifact URL is missing');
      const templateYaml = await fetchArtifactText(item.templateUrl, 'Template artifact');
      if (templateDetailRequestRef.current !== requestId) return;
      const detailDraft = {
        adapter: item.adapter,
        adapterCode: templateDrafts[item.key]?.adapterCode ?? '',
        notes: item.description,
        yaml: templateYaml,
        savedAt: item.lastRun,
      };
      setTemplateDrafts((current) => ({ ...current, [item.key]: detailDraft }));
      if (taskComposerOpen) {
        const templateValue = `${item.name}@${item.version}`;
        setTaskComposerDraft((current) => ({
          ...current,
          template: templateValue,
          templateLocked: true,
          templateParams: extractTaskTemplateParameterDrafts(detailDraft.yaml),
          incrementalField: inferIncrementalField(templateValue),
        }));
      }
    } catch (error) {
      if (templateDetailRequestRef.current !== requestId) return;
      setTemplateDetailError('Failed to load template artifacts');
      console.error('Failed to load template artifacts', error);
    } finally {
      if (templateDetailRequestRef.current === requestId) {
        setTemplateDetailLoading(false);
      }
    }
  }, [taskComposerOpen, templateDrafts]);

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
            onClick={() => void handleTemplateSelect(item)}
          >
            <div className="workspace-dock-card-row">
              <div className="workspace-dock-card-main">
                <div className="workspace-dock-card-copy">
                  <div className="workspace-dock-card-titleline">
                    <span className="workspace-dock-card-title-leading">
                      <span className="workspace-dock-card-icon">
                        <TemplateGlyph kind={inferSiteKind(item.dataType || item.name)} />
                      </span>
                      <Text strong>{item.title}</Text>
                    </span>
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
                    <Text type="secondary" className="workspace-dock-card-subtitle">{item.templatePath}</Text>
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
                <span>tasks</span>
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
              <small>开启后使用 Redis 记录最后入库时间，下次从该时间减 1 天采集。</small>
            </div>
            <Switch
              checked={taskComposerDraft.incremental}
              onChange={(checked) => updateTaskComposerDraft({ incremental: checked })}
            />
          </div>
          {taskComposerDraft.incremental ? (
            <div className="workspace-dock-progress-panel">
              <div className="workspace-dock-progress-meta">
                <strong>Incremental Parameters</strong>
                <span>{incrementalSummary}</span>
              </div>

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
                  <span>固定策略</span>
                  <Input value="最后入库时间减 1 天" readOnly />
                </label>
              </div>
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

          {taskBatchConfig ? (
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
                      beforeUpload={(file) => {
                        void handleTaskBatchUpload(file);
                        return false;
                      }}
                    >
                      <Button className="workspace-dock-file-picker-button" size="small" icon={<UploadOutlined />} loading={taskBatchUploading}>{taskBatchFile || 'Upload to MinIO'}</Button>
                    </Upload>
                  </label>
                  {taskBatchUploadError ? <Text type="danger">{taskBatchUploadError}</Text> : null}
                  <div className="workspace-dock-form-grid" style={{ marginTop: 8 }}>
                    <label>
                      <span>Inject Into *</span>
                      <Select
                        value={taskBatchParam || undefined}
                        disabled={taskBatchParam.includes(',')}
                        options={[
                          ...(taskBatchParam.includes(',') ? [{ value: taskBatchParam, label: taskBatchParam }] : []),
                          ...taskComposerDraft.templateParams.map((param) => ({ value: param.key, label: param.key })),
                        ]}
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
              <strong>Priority</strong>
              <small>Lower values run before higher values.</small>
            </div>
            <InputNumber min={0} max={100} value={taskPriority} onChange={(value) => setTaskPriority(value ?? 50)} />
          </div>
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
                disabled={!taskComposerDraft.template.trim() || (taskBatchInput && (!taskBatchObjectKey || taskBatchUploading))}
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
            <span className="workspace-dock-detail-icon"><TemplateGlyph kind={inferSiteKind(selectedTemplate.dataType || selectedTemplate.name)} /></span>
            <div>
              <Text strong className="workspace-dock-detail-title">{selectedTemplate.title}</Text>
              <Text type="secondary">{selectedTemplate.name}</Text>
            </div>
          </div>
          <div className="workspace-dock-detail-head-actions">
            <Tooltip title="创建任务" placement="top">
              <button
                type="button"
                className="workspace-dock-detail-icon-btn"
                aria-label="创建任务"
                disabled={templateDetailLoading}
                onClick={() => {
                  openTaskComposer({
                    name: selectedTemplate.title,
                    template: `${selectedTemplate.name}@${selectedTemplate.version}`,
                    templateLocked: true,
                    scheduleMode: 'recurring',
                  });
                }}
              >
                <PlusOutlined />
              </button>
            </Tooltip>
            <Tooltip title="编辑模板" placement="top">
              <button
                type="button"
                className={`workspace-dock-detail-icon-btn ${templateDetailMode === 'edit' ? 'is-pinned' : ''}`}
                aria-label="编辑模板"
                aria-pressed={templateDetailMode === 'edit'}
                disabled={templateDetailLoading}
                onClick={() => {
                  if (templateDetailMode === 'overview') {
                    onTemplateApply?.({
                      yaml: selectedTemplateDraft.yaml,
                      adapter: selectedTemplateDraft.adapter,
                      adapterCode: selectedTemplateDraft.adapterCode,
                    });
                    onClose();
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
                templateDetailRequestRef.current += 1;
                setTemplateDetailLoading(false);
                setTemplateDetailError('');
                setTemplateDetailMode('overview');
                setSelectedTemplateKey(null);
              }}
            >
              <CloseOutlined />
            </button>
          </div>
        </div>

        {templateDetailLoading ? (
          <div className="workspace-dock-empty">Loading template artifacts…</div>
        ) : templateDetailError ? (
          <div className="workspace-dock-empty">{templateDetailError}</div>
        ) : templateDetailMode === 'overview' ? (
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

            {renderCompactTemplatePreview(selectedTemplateDraft.yaml)}

            <div className="workspace-dock-detail-actions">
              <span>Last modified {formatDateTime(selectedTemplateDraft.savedAt)}</span>
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
            </div>

            <label className="workspace-dock-form-block">
              <span>YAML</span>
              <TextArea
                value={selectedTemplateDraft.yaml}
                onChange={(event) => updateTemplateDraft(selectedTemplate.key, { yaml: event.target.value })}
                autoSize={{ minRows: 6, maxRows: 10 }}
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
    const selectedRunLogs = selectedTaskLogRunId
      && selectedTaskLogRunId !== latestTaskLogRunId
      && historicalTaskLogs?.taskId === selectedTask.key
      && historicalTaskLogs.runId === selectedTaskLogRunId
      ? historicalTaskLogs.logs
      : runtime.logs;
    const selectedLogRun = runtime.logRuns.find((run) => run.id === selectedTaskLogRunId)
      ?? runtime.logRuns[0]
      ?? null;
    const logRunOptions = runtime.logRuns.map((run) => ({
      value: run.id,
      label: formatDateTime(run.started_at),
    }));
    const taskCanceled = runtime.controlState === 'canceled';
    const pipelineControlsDisabled = taskCanceled || runtime.status === 'queued';
    const taskDeletable = runtime.status === 'queued' || runtime.status === 'completed' || runtime.status === 'failed';
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
            {runtime.status === 'queued' ? (
              <Tooltip title="启动任务" placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-run"
                  aria-label="启动任务"
                  onClick={() => handleStartTask(selectedTask.key)}
                >
                  <CaretRightOutlined />
                </button>
              </Tooltip>
            ) : null}
            {runtime.status === 'failed' && !taskCanceled ? (
              <Tooltip title="重新运行" placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-run"
                  aria-label="重新运行"
                  onClick={() => handleRestartTask(selectedTask.key)}
                >
                  <ReloadOutlined />
                </button>
              </Tooltip>
            ) : null}
            {(runtime.status === 'running' || runtime.status === 'paused') && !taskCanceled ? (
              <Tooltip title="重新运行" placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-run"
                  aria-label="重新运行"
                  onClick={() => handleRestartTask(selectedTask.key)}
                >
                  <ReloadOutlined />
                </button>
              </Tooltip>
            ) : null}
            {runtime.status === 'running' && runtime.controlState !== 'canceled' ? (
              <Tooltip title="暂停任务" placement="top" rootClassName="workspace-dock-control-tooltip">
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
            {runtime.status === 'paused' ? (
              <Tooltip title="继续任务" placement="top" rootClassName="workspace-dock-control-tooltip">
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
              <Tooltip title="取消任务" placement="top" rootClassName="workspace-dock-control-tooltip">
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
            {taskDeletable ? (
              <Popconfirm
                title="删除任务？"
                description="任务及其运行记录将被删除，无法恢复。"
                okText="删除"
                cancelText="保留"
                okButtonProps={{ danger: true }}
                rootClassName="workspace-dock-control-popconfirm"
                onConfirm={() => handleDeleteTask(selectedTask.key)}
              >
                <Tooltip title="删除任务" placement="top" rootClassName="workspace-dock-control-tooltip">
                  <button
                    type="button"
                    className="workspace-dock-detail-icon-btn is-danger"
                    aria-label="删除任务"
                  >
                    <DeleteOutlined />
                  </button>
                </Tooltip>
              </Popconfirm>
            ) : null}
            {runtime.downloadState === 'running' ? (
              <Tooltip title="暂停下载" placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-download"
                  aria-label="暂停下载"
                  disabled={pipelineControlsDisabled}
                  onClick={() => handlePauseDownload(selectedTask.key)}
                >
                  <DownloadOutlined />
                </button>
              </Tooltip>
            ) : (
              <Tooltip title={runtime.downloadState === 'paused' ? '继续下载' : '开始下载'} placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className={`workspace-dock-detail-icon-btn is-download ${runtime.downloadState === 'paused' ? 'is-paused' : ''}`}
                  aria-label={runtime.downloadState === 'paused' ? '继续下载' : '开始下载'}
                  disabled={pipelineControlsDisabled}
                  onClick={() => handleStartDownload(selectedTask.key)}
                >
                  <DownloadOutlined />
                </button>
              </Tooltip>
            )}
            {runtime.syncState === 'running' ? (
              <Tooltip title="暂停同步" placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className="workspace-dock-detail-icon-btn is-sync"
                  aria-label="暂停同步"
                  disabled={pipelineControlsDisabled}
                  onClick={() => handlePauseSync(selectedTask.key)}
                >
                  <SyncOutlined />
                </button>
              </Tooltip>
            ) : (
              <Tooltip title={runtime.syncState === 'paused' ? '继续同步' : '开始同步'} placement="top" rootClassName="workspace-dock-control-tooltip">
                <button
                  type="button"
                  className={`workspace-dock-detail-icon-btn is-sync ${runtime.syncState === 'paused' ? 'is-paused' : ''}`}
                  aria-label={runtime.syncState === 'paused' ? '继续同步' : '开始同步'}
                  disabled={pipelineControlsDisabled}
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
              <strong>{formatTaskNextRunLabel(selectedTask.nextRun)}</strong>
            </div>
            <div className={`workspace-dock-marquee ${display.isRunning ? 'is-running' : ''}`}>
              <i style={{ width: `${Math.max(runtime.progress, 8)}%`, background: display.color }} />
            </div>
            <div className="workspace-dock-progress-meta is-subtle">
              {runtime.isRecurring && logRunOptions.length ? (
                <span
                  className="workspace-dock-log-run-trigger"
                  role="button"
                  tabIndex={0}
                  aria-expanded={taskLogRunOpen}
                  onMouseDownCapture={(event) => {
                    if ((event.target as HTMLElement).closest('.workspace-dock-log-run-popup')) {
                      return;
                    }
                    event.preventDefault();
                    event.stopPropagation();
                    setTaskLogRunOpen((current) => !current);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setTaskLogRunOpen((current) => !current);
                    }
                  }}
                >
                  <Select
                    className="workspace-dock-log-run-select"
                    popupClassName="workspace-dock-log-run-popup"
                    popupMatchSelectWidth={168}
                    size="small"
                    listHeight={160}
                    suffixIcon={<DownOutlined className="workspace-dock-log-run-arrow" />}
                    open={taskLogRunOpen}
                    onOpenChange={setTaskLogRunOpen}
                    value={selectedTaskLogRunId ?? logRunOptions[0].value}
                    options={logRunOptions}
                    loading={taskLogsLoading || taskLogRunsLoading}
                    onChange={(runId) => {
                      setTaskLogRunOpen(false);
                      void handleTaskLogRunChange(runId);
                    }}
                    onPopupScroll={(event) => void handleTaskLogRunPopupScroll(event)}
                    aria-label="选择采集执行日志"
                  />
                </span>
              ) : (
                <span>{selectedLogRun ? formatDateTime(selectedLogRun.started_at) : selectedTask.lag}</span>
              )}
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
              {selectedRunLogs.slice().reverse().map((log) => (
                <div className={`workspace-dock-log-row is-${log.level}`} key={`${selectedTask.key}-${log.runId}-${log.createdAt}-${log.message}`}>
                  <code>{renderConsoleLogMessage(log.message)}</code>
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
          padding: 8px;
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
          margin-top: 12px;
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
          display: inline-flex;
          align-items: center;
          justify-content: center;
          color: rgba(255, 255, 255, 0.68);
          font-size: 13px;
          line-height: 18px;
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
        .workspace-dock-card-title-leading {
          min-width: 0;
          display: inline-flex;
          align-items: center;
          gap: 8px;
        }
        .workspace-dock-card-title-leading {
          flex: 1;
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
          align-items: flex-start;
          justify-content: space-between;
          gap: 8px;
          margin-top: 2px;
        }
        .workspace-dock-card-subtitle.ant-typography-secondary {
          min-width: 0;
          flex: 1;
          margin-top: 0;
          white-space: normal;
          line-height: 1.4;
          display: -webkit-box;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
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
        .workspace-dock-list.is-templates .workspace-dock-card-footer {
          font-size: 9px;
          line-height: 1;
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
          gap: 4px;
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
        .workspace-dock-control-tooltip .ant-tooltip-inner {
          min-height: 0;
          padding: 3px 7px;
          border-radius: 5px;
          font-size: 11px;
          font-weight: 400;
          line-height: 16px;
        }
        .workspace-dock-control-popconfirm .ant-popover-inner {
          padding: 9px 10px;
          border-radius: 7px;
        }
        .workspace-dock-control-popconfirm .ant-popconfirm-title {
          font-size: 12px;
          font-weight: 500;
          line-height: 18px;
        }
        .workspace-dock-control-popconfirm .ant-popconfirm-description {
          margin-top: 1px;
          font-size: 11px;
          line-height: 16px;
        }
        .workspace-dock-control-popconfirm .ant-popconfirm-buttons {
          margin-top: 7px;
        }
        .workspace-dock-control-popconfirm .ant-popconfirm-buttons .ant-btn {
          height: 24px;
          padding: 0 8px;
          border-radius: 5px;
          font-size: 11px;
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
        .workspace-dock-detail-icon-btn.is-paused {
          color: ${aura.subtle};
          background: rgba(148, 163, 184, 0.08);
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
          padding: 0 10px 10px 10px;
        }
        .workspace-dock-detail.is-task-log-only .workspace-dock-log-panel {
          flex: 0 0 auto;
          height: auto;
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
        .workspace-dock-template-preview {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 16px;
          border-radius: 16px;
          border: 1px solid rgba(255, 255, 255, 0.08);
          background:
            linear-gradient(180deg, rgba(44, 49, 60, 0.98), rgba(34, 39, 49, 0.98)),
            rgba(28, 33, 42, 0.98);
          box-shadow: 0 20px 52px rgba(0, 0, 0, 0.24);
        }
        .workspace-dock-template-preview .ai-template-stage-section {
          gap: 8px;
          padding-bottom: 10px;
          border-bottom: 1px dashed rgba(255, 255, 255, 0.18);
        }
        .workspace-dock-template-preview .ai-template-stage-section:last-child {
          padding-bottom: 0;
          border-bottom: none;
        }
        .workspace-dock-template-preview .ai-template-stage-copy {
          gap: 3px;
        }
        .workspace-dock-template-preview .ai-template-stage-title {
          font-size: 12px;
          line-height: 1.3;
        }
        .workspace-dock-template-preview .ai-template-stage-copy small,
        .workspace-dock-template-preview .ai-template-stage-actions {
          font-size: 10px;
          line-height: 1.45;
        }
        .workspace-dock-template-preview .ai-template-stage-body {
          gap: 0;
          padding: 4px 6px;
          border-radius: 12px;
        }
        .workspace-dock-template-preview .ai-template-fields-subsection {
          margin: 10px 2px 2px;
          padding: 7px 8px 5px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.1);
          color: ${aura.muted};
          font-size: 10px;
          font-weight: 700;
          line-height: 1.3;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }
        .workspace-dock-template-preview .ai-template-fields-subsection:first-child {
          margin-top: 2px;
        }
        .workspace-dock-template-preview .ai-template-field {
          display: grid;
          grid-template-columns: minmax(120px, 156px) minmax(0, 1fr);
          gap: 8px 10px;
          align-items: start;
          --ai-template-indent: calc(var(--ai-template-depth, 0) * 12px);
          padding-left: var(--ai-template-indent);
        }
        .workspace-dock-template-preview .ai-template-field:last-child {
          border-bottom: none;
        }
        .workspace-dock-template-preview .ai-template-field.is-group {
          display: block;
          margin-top: 6px;
          padding: 4px 4px 2px;
          padding-left: var(--ai-template-indent);
          border-bottom: none;
        }
        .workspace-dock-template-preview .ai-template-field.is-group:first-child {
          margin-top: 0;
        }
        .workspace-dock-template-preview .ai-template-field-key span,
        .workspace-dock-template-preview .ai-template-field-value pre {
          font-size: 12px;
          line-height: 1.45;
        }
        .workspace-dock-template-preview .ai-template-field-value {
          min-height: 16px;
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
        .workspace-dock-progress-meta.is-subtle > span {
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
        .workspace-dock-progress-meta.is-subtle > .workspace-dock-log-run-trigger {
          display: inline-flex;
          align-items: center;
          width: 168px;
          cursor: pointer;
          outline: 0;
        }
        .workspace-dock-log-run-select {
          width: 100%;
        }
        .workspace-dock-log-run-select.ant-select-single,
        .workspace-dock-log-run-select.ant-select-single.ant-select-sm {
          height: auto !important;
          min-height: 0 !important;
        }
        .workspace-dock-log-run-select .ant-select-selector {
          display: flex !important;
          align-items: center !important;
          justify-content: center !important;
          height: 22px !important;
          padding: 0 24px 0 8px !important;
          border: 0 !important;
          background: transparent !important;
          box-shadow: none !important;
          font-size: 9px !important;
        }
        .workspace-dock-log-run-select.ant-select-focused .ant-select-selector,
        .workspace-dock-log-run-select:focus-within .ant-select-selector {
          border: 0 !important;
          outline: 0 !important;
          box-shadow: none !important;
        }
        .workspace-dock-log-run-select .ant-select-selection-wrap {
          display: flex;
          align-items: center;
          align-self: stretch;
        }
        .workspace-dock-log-run-select .ant-select-selection-item {
          display: flex;
          align-items: center;
          justify-content: center;
          line-height: normal !important;
          text-align: center;
        }
        .workspace-dock-log-run-select .ant-select-arrow {
          top: 50%;
          inset-inline-end: 8px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 8px;
          height: 8px;
          margin-top: 0;
          transform: translateY(-50%);
          color: ${aura.subtle};
          font-size: 8px;
        }
        .workspace-dock-log-run-select .ant-select-arrow .anticon {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          line-height: 1;
        }
        .workspace-dock-log-run-select .ant-select-arrow svg {
          width: 8px;
          height: 8px;
        }
        .workspace-dock-log-run-arrow {
          transition: transform 160ms ease;
        }
        .workspace-dock-log-run-select.ant-select-open .workspace-dock-log-run-arrow {
          transform: rotate(180deg);
        }
        .workspace-dock-log-run-popup {
          box-sizing: border-box;
          padding: 4px;
          border: 1px solid ${aura.borderSoft};
          border-radius: 8px;
          background: ${aura.surfaceElevated};
          box-shadow: ${aura.shadow};
        }
        .workspace-dock-log-run-popup .ant-select-item {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 22px;
          margin: 1px 0;
          padding: 2px 6px;
          border-radius: 5px;
          font-size: 9px;
          text-align: center;
        }
        .workspace-dock-log-run-popup .ant-select-item-option-content {
          width: 100%;
          overflow: hidden;
          text-align: center;
          text-overflow: ellipsis;
        }
        .workspace-dock-log-run-popup .ant-select-item-option-active:not(.ant-select-item-option-disabled) {
          background: ${aura.surfaceSoft};
        }
        .workspace-dock-log-run-popup .ant-select-item-option-selected:not(.ant-select-item-option-disabled) {
          color: ${aura.text};
          background: ${aura.accentSoft};
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
          width: 100%;
          background: transparent;
        }
        .workspace-dock-log-row {
          display: block;
          color: ${aura.muted};
          font-size: 10px;
          line-height: 1.45;
        }
        .workspace-dock-log-row code {
          display: block;
          color: inherit;
          font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          white-space: pre-wrap;
          word-break: break-word;
        }
        .workspace-dock-log-bracket {
          color: rgba(255, 255, 255, 0.38);
        }
        .workspace-dock-log-time {
          color: #7DD3FC;
        }
        .workspace-dock-log-level {
          color: #8AB4FF;
          font-weight: 700;
        }
        .workspace-dock-log-level.is-debug {
          color: #BFA8FF;
        }
        .workspace-dock-log-level.is-info {
          color: #65D5A3;
        }
        .workspace-dock-log-level.is-warning {
          color: #F6C35B;
        }
        .workspace-dock-log-level.is-error,
        .workspace-dock-log-level.is-critical {
          color: #F87171;
        }
        .workspace-dock-log-process {
          color: #C4A7FF;
          font-weight: 600;
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
            grid-template-rows: repeat(2, minmax(0, 1fr));
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
                        { label: 'Paused', value: 'paused' },
                        { label: 'Completed', value: 'completed' },
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

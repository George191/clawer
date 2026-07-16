import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
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
  TimePicker,
  Timeline,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AudioOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloudUploadOutlined,
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
  UploadOutlined,
} from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import ErrorBoundary from '@/components/ErrorBoundary';
import {
  type DryRunResponse,
  type FieldDef,
  type UrlPreflightResponse,
  createAnalyzeStream,
  dryRun as dryRunApi,
  generateAdapter as generateAdapterApi,
  generateTemplate as generateTemplateApi,
  preflightUrl,
  releaseWorkspaceTemplate,
} from '@/services/aiApi';
import workspacePalette from './palette';
import { ReleaseArchiveIcon, ReleaseDraftIcon } from './releaseIcons';
import WorkspaceDock, { type WorkspacePanel } from './WorkspaceDock';

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

const YamlFileIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg
    viewBox="0 0 480 511.65"
    className={className}
    xmlns="http://www.w3.org/2000/svg"
    shapeRendering="geometricPrecision"
    textRendering="geometricPrecision"
    imageRendering="optimizeQuality"
    fillRule="evenodd"
    clipRule="evenodd"
    aria-hidden="true"
  >
    <path
      fill="currentColor"
      d="M84.68 237.33H375.8v-81.86h-86.02c-9.02 0-21.62-4.88-27.56-10.83-5.95-5.95-9.6-16.68-9.6-25.7V31.81H33.92c-.77 0-1.34.39-1.72.77-.58.38-.77.96-.77 1.73v443.23c0 .58.38 1.34.77 1.73.38.57 1.15.77 1.72.77h339.39c.76 0 .72-.39 1.1-.77.58-.39 1.39-1.15 1.39-1.73v-46.46H84.68c-17.25 0-31.47-14.16-31.47-31.47V268.79c0-17.31 14.16-31.46 31.47-31.46zm1.86 52.82h29.79l17.57 29.23 17.48-29.23h29.63l-33.71 50.47v36.36h-26.92v-36.36l-33.84-50.47zm143.04 72.52h-30.4l-4.36 14.31h-27.39l32.68-86.83h29.37l32.54 86.83h-28.09l-4.35-14.31zm-5.68-18.79-9.48-31.21-9.52 31.21h19zm44.32-53.73h35.4l13.48 52.84 13.52-52.84h35.23v86.83H343.9v-66.19l-16.94 66.19h-19.89l-16.9-66.19v66.19h-21.95v-86.83zm109.98 0H405v65.49h41.96v21.34H378.2v-86.83zm28.98-52.82h41.36c17.3 0 31.46 14.2 31.46 31.46v130.82c0 17.26-14.2 31.47-31.46 31.47h-41.36v56.4c0 6.72-2.69 12.66-7.1 17.08-4.41 4.41-10.36 7.09-17.07 7.09H24.17c-6.71 0-12.66-2.68-17.07-7.09C2.69 500.14 0 494.2 0 487.48V24.37C0 17.65 2.69 11.7 7.1 7.29 11.51 2.88 17.65.19 24.17.19h244.49c.58-.19 1.16-.19 1.73-.19 2.69 0 5.37 1.15 7.29 2.88h.38c.39.19.58.38.96.77l124.9 126.43c2.11 2.1 3.64 4.98 3.64 8.24 0 .96-.19 1.73-.38 2.69v96.32zM281.13 116.45V37.22l89.22 90.36h-78.09c-3.07 0-5.75-1.34-7.86-3.26-1.92-1.92-3.27-4.8-3.27-7.87z"
    />
  </svg>
);

const AdapterEditorIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg
    viewBox="0 0 512 380.24"
    className={className}
    xmlns="http://www.w3.org/2000/svg"
    shapeRendering="geometricPrecision"
    textRendering="geometricPrecision"
    imageRendering="optimizeQuality"
    fillRule="evenodd"
    clipRule="evenodd"
    aria-hidden="true"
  >
    <path
      fill="currentColor"
      d="M34.66 0h442.68C496.4 0 512 15.6 512 34.66v310.92c0 19.06-15.6 34.66-34.66 34.66H34.66C15.6 380.24 0 364.64 0 345.58V34.66C0 15.6 15.6 0 34.66 0zm173.92 264.36c5.76 5.04 6.34 13.81 1.3 19.57-5.05 5.76-13.81 6.35-19.57 1.3l-52.73-46.19c-5.76-5.05-6.35-13.81-1.3-19.58.43-.49.89-.94 1.37-1.36l52.66-46.14c5.76-5.04 14.52-4.46 19.57 1.31 5.04 5.76 4.46 14.52-1.3 19.57l-40.82 35.76 40.82 35.76zm113.11 20.87c-5.76 5.05-14.52 4.46-19.57-1.3-5.04-5.76-4.46-14.53 1.3-19.57l40.82-35.76-40.82-35.76c-5.76-5.05-6.34-13.81-1.3-19.57 5.05-5.77 13.81-6.35 19.57-1.31l52.66 46.14c.48.42.94.87 1.37 1.36 5.05 5.77 4.46 14.53-1.3 19.58l-52.73 46.19zm-65.95-124.31c1.74-7.47 9.22-12.12 16.69-10.38 7.47 1.74 12.12 9.22 10.38 16.69l-30.13 129.04c-1.74 7.48-9.22 12.13-16.69 10.39-7.47-1.74-12.12-9.22-10.38-16.69l30.13-129.05zM22.03 97.05v251.91a9.56 9.56 0 0 0 9.59 9.59H481.8a9.56 9.56 0 0 0 9.59-9.59V97.05H22.03zm422.32-58.09c9.46 0 17.12 7.67 17.12 17.12 0 9.46-7.66 17.12-17.12 17.12-9.45 0-17.12-7.66-17.12-17.12 0-9.45 7.67-17.12 17.12-17.12zm-116.03 0c9.46 0 17.12 7.67 17.12 17.12 0 9.46-7.66 17.12-17.12 17.12-9.45 0-17.11-7.66-17.11-17.12 0-9.45 7.66-17.12 17.11-17.12zm58.02 0c9.45 0 17.12 7.67 17.12 17.12 0 9.46-7.67 17.12-17.12 17.12-9.45 0-17.12-7.66-17.12-17.12 0-9.45 7.67-17.12 17.12-17.12z"
    />
  </svg>
);

const AdapterPinnedIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg
    version="1.1"
    viewBox="0 0 122.83 122.88"
    className={className}
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <g>
      <path
        fill="currentColor"
        d="M60.18 24.74l.86.86L84.54 2.11C85.94.71 87.8 0 89.66 0h.03c1.86.01 3.71.71 5.11 2.11l25.91 25.91c1.41 1.41 2.12 3.27 2.12 5.13 0 .1-.01.2-.01.3-.07 1.76-.77 3.5-2.11 4.84L97.22 61.77l.92.92c.99.99 1.49 2.29 1.49 3.6 0 .11-.01.22-.02.33-.07 1.19-.56 2.36-1.47 3.26l-48.38 48.38c-3.08 3.08-7.13 4.61-11.18 4.61-4.05 0-8.1-1.54-11.18-4.61l-2.18-2.18L7.24 98.1l-2.63-2.63C1.54 92.4 0 88.35 0 84.3c0-4.05 1.54-8.1 4.61-11.18l48.38-48.38c.99-.99 2.29-1.48 3.59-1.48v-.01c1.31 0 2.61.5 3.6 1.49zM37.63 79.35c1.47-1.47 3.39-1.55 4.95-.64l1.31-1.31c.03-1.46-.54-2.89-1.07-4.23-1.15-2.88-2.15-5.38 1.3-7.7-.68-1.17-.51-2.7.49-3.7 1.2-1.2 3.14-1.2 4.34 0 1.2 1.2 1.2 3.14 0 4.34-.86.86-2.12 1.11-3.2.72l.02.03c-.4.23-.72.47-.98.71-1.45 1.39-.81 3-.07 4.83l.13.33c.35.88.7 1.81.91 2.79L57.8 63.5l-1.62-1.62-.37-.37.63-.16 6.95-1.78-1.94 7.59-2.2-2.2-9.18 9.18c.99.2 1.92.54 2.82.9l.41.16c1.85.74 3.48 1.39 4.88-.13.19-.2.37-.45.55-.74l-1.03-1.03a.444.444 0 010-.63l3.81-3.81c.17-.17.45-.17.63 0L66 72.74c.17.17.17.45 0 .63l-3.81 3.81c-.17.17-.45.17-.63 0l-1.35-1.35c-2.31 3.43-4.81 2.43-7.68 1.28-1.37-.55-2.85-1.14-4.35-1.07l-4.14 4.14c.92 1.56.83 3.48-.64 4.95-1.47 1.47-4.18 1.6-5.77 0-1.59-1.6-1.47-4.31 0-5.78zm-24.48 16.22c.16.11.31.23.45.37l13.79 13.79c.14.14.26.29.37.45l3.87 3.87c1.91 1.91 4.43 2.87 6.96 2.87 2.52 0 5.05-.96 6.96-2.87L93.3 66.29 56.59 29.58 8.83 77.34c-1.91 1.91-2.87 4.43-2.87 6.96 0 2.52.96 5.05 2.87 6.96l4.32 4.31zm87.52-58.76c1.26 1.26 1.26 3.32 0 4.57l-4.23 4.23c-1.26 1.26-3.32 1.26-4.57 0-1.26-1.26-1.26-3.31 0-4.57l4.23-4.23c1.26-1.26 3.32-1.26 4.57 0zM87 23.13c1.26 1.26 1.26 3.32 0 4.57l-4.23 4.23c-1.26 1.26-3.32 1.26-4.57 0-1.26-1.26-1.26-3.31 0-4.57l4.23-4.23c1.25-1.25 3.31-1.25 4.57 0zm29.49 9.11L90.58 6.33a1.283 1.283 0 00-.9-.38h-.02c-.32 0-.65.13-.91.38L65.32 29.76 93.06 57.5l23.43-23.43c.22-.22.35-.51.38-.79 0-.04 0-.08 0-.12-.01-.34-.13-.68-.38-.92z"
      />
    </g>
  </svg>
);

const ChevronRightIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg
    viewBox="0 0 12 12"
    className={className}
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M4 2.25L7.5 6L4 9.75"
      stroke="currentColor"
      strokeWidth="1.35"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

type WorkMode = 'explore' | 'contract' | 'dryrun' | 'publish';
type MissionTab = 'goal' | 'policy';
type RunStatus = 'idle' | 'running' | 'paused' | 'completed';
type ProcessStepKey = 'prepare' | 'entry' | 'structure' | 'contract' | 'dryrun' | 'publish';
type TerminalLogLevel = 'info' | 'ok' | 'warn';
type TemplateStageId = 'site' | 'request' | 'response' | 'pagination' | 'fields' | 'dedup' | 'download';
type SessionWorkflowPhase = 'analyzing-template' | 'confirm-template' | 'generating-adapter' | 'release-template';
type SessionGuideStepId = TemplateStageId | 'confirm-template' | 'generate-adapter' | 'save-template';
type SessionInspectorTabKind = 'browser' | 'code';
type SessionInspectorTab = {
  id: string;
  kind: SessionInspectorTabKind;
  title: string;
  subtitle: string;
};
type AdapterBuildStepId = 'request' | 'fields' | 'download' | 'file';
type AdapterBuildStep = {
  id: AdapterBuildStepId;
  title: string;
  desc: string;
  log: string;
  elapsed: string;
  details: string[];
};
type AdapterPreviewLine = {
  key: string;
  lineNumber: number;
  prefix: '+' | ' ';
  content: string;
  added?: boolean;
};
type PythonPreviewTokenKind =
  | 'plain'
  | 'keyword'
  | 'builtin'
  | 'string'
  | 'comment'
  | 'number'
  | 'function-name'
  | 'class-name'
  | 'property'
  | 'decorator'
  | 'operator'
  | 'punctuation';
type PythonPreviewToken = {
  value: string;
  kind: PythonPreviewTokenKind;
};
type ReleaseAction = 'draft' | 'archive' | 'publish';
type TaskPublishMode = 'launch' | 'skip';
type ReleaseScheduleKind = 'once' | 'daily' | 'interval';
type ReleaseTemplateParam = {
  name: string;
  description: string;
  defaultValue: string;
  required: boolean;
};
type ReleaseBatchConfig = {
  filePath: string;
  paramName: string;
  batchSize: string;
  startLine: string;
  limit: string;
  delay: string;
};
type TemplateEntry = {
  id: string;
  key: string;
  value: string;
  nodeType: 'group' | 'value';
  step: ProcessStepKey;
  stageId: TemplateStageId;
  multiline: boolean;
  depth: number;
};

type TemplateCatalogItem = {
  id: string;
  fileName: string;
  displayName: string;
  entries: TemplateEntry[];
  raw: string;
};

const pythonPreviewKeywords = new Set([
  'and',
  'as',
  'async',
  'await',
  'class',
  'def',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
]);

const pythonPreviewBuiltins = new Set(['False', 'None', 'self', 'True']);

function tokenizePythonPreviewLine(line: string): PythonPreviewToken[] {
  const tokens: PythonPreviewToken[] = [];
  let index = 0;
  let previousSignificantKind: PythonPreviewTokenKind | null = null;
  let previousSignificantValue = '';

  const commitToken = (value: string, kind: PythonPreviewTokenKind) => {
    if (!value) {
      return;
    }
    const token = { value, kind };
    tokens.push(token);
    if (value.trim()) {
      previousSignificantKind = kind;
      previousSignificantValue = value;
    }
  };

  while (index < line.length) {
    const char = line[index];

    if (char === ' ' || char === '\t') {
      let end = index + 1;
      while (end < line.length && (line[end] === ' ' || line[end] === '\t')) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'plain');
      index = end;
      continue;
    }

    if (char === '#') {
      commitToken(line.slice(index), 'comment');
      break;
    }

    if (char === '@') {
      let end = index + 1;
      while (end < line.length && /[A-Za-z0-9_.]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'decorator');
      index = end;
      continue;
    }

    if (char === '"' || char === "'") {
      const quote = char;
      let end = index + 1;
      while (end < line.length) {
        if (line[end] === '\\') {
          end += 2;
          continue;
        }
        if (line[end] === quote) {
          end += 1;
          break;
        }
        end += 1;
      }
      commitToken(line.slice(index, end), 'string');
      index = end;
      continue;
    }

    if (/\d/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[\d._]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'number');
      index = end;
      continue;
    }

    if (/[A-Za-z_]/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[A-Za-z0-9_]/.test(line[end])) {
        end += 1;
      }

      const value = line.slice(index, end);
      let kind: PythonPreviewTokenKind = 'plain';
      let nextNonSpaceIndex = end;
      while (nextNonSpaceIndex < line.length && /\s/.test(line[nextNonSpaceIndex])) {
        nextNonSpaceIndex += 1;
      }
      let previousNonSpaceIndex = index - 1;
      while (previousNonSpaceIndex >= 0 && /\s/.test(line[previousNonSpaceIndex])) {
        previousNonSpaceIndex -= 1;
      }

      const nextNonSpaceChar = line[nextNonSpaceIndex] ?? '';
      const previousNonSpaceChar = previousNonSpaceIndex >= 0 ? line[previousNonSpaceIndex] : '';

      if (pythonPreviewKeywords.has(value)) {
        kind = 'keyword';
      } else if (pythonPreviewBuiltins.has(value)) {
        kind = 'builtin';
      } else if (previousSignificantKind === 'keyword' && previousSignificantValue === 'class') {
        kind = 'class-name';
      } else if (previousSignificantKind === 'keyword' && previousSignificantValue === 'def') {
        kind = 'function-name';
      } else if (nextNonSpaceChar === '(') {
        kind = 'function-name';
      } else if (previousNonSpaceChar === '.') {
        kind = 'property';
      } else if (/^[A-Z]/.test(value)) {
        kind = 'class-name';
      }

      commitToken(value, kind);
      index = end;
      continue;
    }

    if (/[=:+\-*/%<>!&|]/.test(char)) {
      let end = index + 1;
      while (end < line.length && /[=:+\-*/%<>!&|]/.test(line[end])) {
        end += 1;
      }
      commitToken(line.slice(index, end), 'operator');
      index = end;
      continue;
    }

    commitToken(char, 'punctuation');
    index += 1;
  }

  return tokens;
}

function renderPythonPreviewContent(line: string, keyPrefix: string) {
  const tokens = tokenizePythonPreviewLine(line);

  if (!tokens.length) {
    return ' ';
  }

  return tokens.map((token, index) => (
    <span className={`ai-session-python-token is-${token.kind}`} key={`${keyPrefix}-${index}`}>
      {token.value}
    </span>
  ));
}

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

const templateStageMeta: Record<TemplateStageId, {
  title: string;
  desc: string;
  threshold: ProcessStepKey;
  addable?: boolean;
  rootKey?: 'download' | 'list_fields';
}> = {
  site: {
    title: 'Site',
    desc: 'base url, source identity and crawler baseline',
    threshold: 'prepare',
  },
  request: {
    title: 'Request',
    desc: 'request method, query params and fetch contract',
    threshold: 'entry',
  },
  response: {
    title: 'Response',
    desc: 'response type and result path resolution',
    threshold: 'entry',
  },
  pagination: {
    title: 'Pagination',
    desc: 'page turning strategy and continuation cursor',
    threshold: 'structure',
  },
  fields: {
    title: 'Fields',
    desc: 'list/detail fields, selectors and output schema',
    threshold: 'structure',
    addable: true,
    rootKey: 'list_fields',
  },
  dedup: {
    title: 'Dedup',
    desc: 'unique fields and record identity contract',
    threshold: 'contract',
  },
  download: {
    title: 'Download',
    desc: 'asset selectors, file types and download policy',
    threshold: 'contract',
    addable: true,
    rootKey: 'download',
  },
};

const templateStageOrder: TemplateStageId[] = ['site', 'request', 'response', 'pagination', 'fields', 'dedup', 'download'];
const INSPECTOR_TRANSITION_MS = 360;

const sessionGuideMeta: Record<Exclude<SessionGuideStepId, TemplateStageId>, { title: string; desc: string }> = {
  'confirm-template': {
    title: 'Confirm',
    desc: 'Freeze the YAML contract and lock template editing.',
  },
  'generate-adapter': {
    title: 'Adapter',
    desc: 'Generate the adapter draft from the confirmed template.',
  },
  'save-template': {
    title: 'Save',
    desc: 'Finalize release, archive, publish and task routing.',
  },
};

const adapterBuildStepBlueprints: ReadonlyArray<{ id: AdapterBuildStepId }> = [
  { id: 'request' },
  { id: 'fields' },
  { id: 'download' },
  { id: 'file' },
];

const formatElapsedLabel = (totalSeconds: number) => (
  totalSeconds >= 60
    ? `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`
    : `${totalSeconds}s`
);

const releaseActionMeta: Record<ReleaseAction, { title: string; desc: string; cta: string }> = {
  draft: {
    title: 'Draft',
    desc: 'Keep the template editable as an internal release draft.',
    cta: 'Save Draft',
  },
  archive: {
    title: 'Archive',
    desc: 'Store the finished template without exposing it to task publish.',
    cta: 'Archive Template',
  },
  publish: {
    title: 'Publish',
    desc: 'Promote the template to the active library and prepare task dispatch.',
    cta: 'Publish Template',
  },
};

const taskPublishMeta: Record<TaskPublishMode, { title: string; desc: string }> = {
  launch: {
    title: 'Launch task',
    desc: 'Create the task draft immediately after the template action is applied.',
  },
  skip: {
    title: 'Skip task',
    desc: 'Finish the template flow without opening a task draft.',
  },
};

const isTemplateStageId = (value: SessionGuideStepId): value is TemplateStageId => (
  templateStageOrder.includes(value as TemplateStageId)
);

const toPascalCase = (value: string) => value
  .split(/[^a-zA-Z0-9]+/)
  .filter(Boolean)
  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  .join('');

const inferTemplateStep = (key: string, path: string): ProcessStepKey => {
  if (
    path === 'batch_params'
    || path.startsWith('batch_params.')
    || path === 'params'
    || path.startsWith('params')
    || key === 'list_page'
    || path.startsWith('list_request')
    || key === 'response_type'
    || key === 'json_item_path'
    || key === 'json_total_path'
    || key === 'json_page_path'
    || key === 'json_total_num_pages'
  ) {
    return 'entry';
  }
  if (
    path === 'list_pagination'
    || path.startsWith('list_pagination.')
    || path === 'dedup_fields'
    || path.startsWith('dedup_fields')
    || path === 'list_fields'
    || path.startsWith('list_fields')
  ) {
    return 'structure';
  }
  if (path === 'download' || path.startsWith('download')) {
    return 'contract';
  }
  if (
    path === 'name'
    || path === 'display_name'
    || path === 'base_url'
    || path === 'data_type'
    || path === 'adapter'
    || path === 'anti_crawl_enabled'
    || path === 'description'
  ) {
    return 'prepare';
  }
  const matchedStep = processStepOrder.find((step) => templateStepKeys[step].includes(key));
  return matchedStep ?? 'publish';
};

const inferTemplateStageId = (key: string, path: string): TemplateStageId => {
  if (
    path === 'batch_params'
    || path.startsWith('batch_params.')
    || path === 'params'
    || path.startsWith('params')
    || key === 'list_page'
    || path.startsWith('list_request')
  ) {
    return 'request';
  }
  if (
    path === 'list_pagination'
    || path.startsWith('list_pagination.')
  ) {
    return 'pagination';
  }
  if (path === 'dedup_fields' || path.startsWith('dedup_fields')) {
    return 'dedup';
  }
  if (path === 'download' || path.startsWith('download')) {
    return 'download';
  }
  if (path === 'list_fields' || path.startsWith('list_fields')) {
    return 'fields';
  }
  if (
    path === 'name'
    || path === 'display_name'
    || path === 'base_url'
    || path === 'data_type'
    || path === 'adapter'
    || path === 'anti_crawl_enabled'
    || path === 'description'
  ) {
    return 'site';
  }
  if (
    key === 'response_type'
    || key === 'json_item_path'
    || key === 'json_total_path'
    || key === 'json_page_path'
    || key === 'json_total_num_pages'
  ) {
    return 'response';
  }
  return 'fields';
};

const stripYamlQuotes = (value: string) => value.trim().replace(/^['"]|['"]$/g, '');
const isStructuredInlineYamlValue = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) return true;
  if (trimmed.startsWith('{') && trimmed.endsWith('}') && /[:,]/.test(trimmed.slice(1, -1))) return true;
  return false;
};
const yamlListPreviewRoots = new Set(['params', 'dedup_fields', 'list_fields', 'download']);
const flattenedTemplateRootKeys = new Set(['list_pagination', 'dedup_fields', 'download']);

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

    const keyPathParent = keyPathStack[keyPathStack.length - 1];
    const listItemParent = listItemContextStack[listItemContextStack.length - 1];
    const parentPath = listItemParent && (!keyPathParent || listItemParent.indent > keyPathParent.indent)
      ? listItemParent.path
      : keyPathParent?.path ?? '';
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

        entries.push({
          id: itemPath,
          key: itemPath,
          value: '',
          nodeType: 'group',
          step: inferTemplateStep(listBase.split('.').pop() ?? listBase, itemPath),
          stageId: inferTemplateStageId(listBase.split('.').pop() ?? listBase, itemPath),
          multiline: false,
          depth,
        });

        if (rawValue === '|' || rawValue === '>') {
          const block = collectBlockValue(index + 1, indent);
          keyValue = block.value;
          multiline = true;
          index = block.nextIndex;
        } else if (isStructuredInlineYamlValue(keyValue)) {
          multiline = true;
        }

        entries.push({
          id: key,
          key,
          value: keyValue,
          nodeType: rawValue ? 'value' : 'group',
          step: inferTemplateStep(keyName, key),
          stageId: inferTemplateStageId(keyName, key),
          multiline,
          depth: depth + 1,
        });

        if (!rawValue) {
          listItemContextStack.push({ indent, path: itemPath });
          keyPathStack.push({ indent: indent + 2, path: key });
        } else {
          listItemContextStack.push({ indent, path: itemPath });
        }
      } else {
        entries.push({
          id: itemPath,
          key: itemPath,
          value: normalizeYamlValue(value),
          nodeType: 'value',
          step: inferTemplateStep(listBase.split('.').pop() ?? listBase, itemPath),
          stageId: inferTemplateStageId(listBase.split('.').pop() ?? listBase, itemPath),
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
    } else if (isStructuredInlineYamlValue(value)) {
      multiline = true;
    }

    entries.push({
      id: path,
      key: path,
      value,
      nodeType: rawValue ? 'value' : 'group',
      step: inferTemplateStep(keyName, path),
      stageId: inferTemplateStageId(keyName, path),
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
      raw,
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
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [urlPreflight, setUrlPreflight] = useState<UrlPreflightResponse | null>(null);
  const [templateId, setTemplateId] = useState('ai-contract-preview');
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [taskDraft, setTaskDraft] = useState('');
  const [submittedPrompt, setSubmittedPrompt] = useState('');
  const [workflowPhase, setWorkflowPhase] = useState<SessionWorkflowPhase>('analyzing-template');
  const [expandedStep, setExpandedStep] = useState<WorkMode>('explore');
  const [activeProcessStep, setActiveProcessStep] = useState<ProcessStepKey>('prepare');
  const [hoveredStageGuideStep, setHoveredStageGuideStep] = useState<SessionGuideStepId | null>(null);
  const [activeTemplateStage, setActiveTemplateStage] = useState<TemplateStageId | null>(null);
  const [templateStageVisibility, setTemplateStageVisibility] = useState<Partial<Record<TemplateStageId, number>>>({});
  const [guidePreviewPhase, setGuidePreviewPhase] = useState<SessionWorkflowPhase | null>(null);
  const [templateTabVisible, setTemplateTabVisible] = useState(false);
  const [templateTabAnimating, setTemplateTabAnimating] = useState(false);
  const [adapterTabVisible, setAdapterTabVisible] = useState(false);
  const [adapterTabAnimating, setAdapterTabAnimating] = useState(false);
  const [expandingPinnedPanel, setExpandingPinnedPanel] = useState<'template' | 'adapter' | null>(null);
  const [completedProcessSteps, setCompletedProcessSteps] = useState<Set<ProcessStepKey>>(new Set());
  const [visibleProcessSteps, setVisibleProcessSteps] = useState<ProcessStepKey[]>(['prepare']);
  const [selectedLogStep, setSelectedLogStep] = useState<ProcessStepKey>('prepare');
  const [scanPulse, setScanPulse] = useState(0);
  const [liveLogs, setLiveLogs] = useState<string[]>(['等待采集目标']);
  const [promptGenerating, setPromptGenerating] = useState(false);

  const [templateValueDrafts, setTemplateValueDrafts] = useState<Record<string, string>>({});
  const [templateDraftEntries, setTemplateDraftEntries] = useState<TemplateEntry[]>([]);
  const [adapterBuildIndex, setAdapterBuildIndex] = useState(0);
  const [expandedAdapterStep, setExpandedAdapterStep] = useState<number | null>(0);
  const [selectedReleaseAction, setSelectedReleaseAction] = useState<ReleaseAction>('draft');
  const [selectedTaskPublishMode, setSelectedTaskPublishMode] = useState<TaskPublishMode>('launch');
  const [releaseScheduleKind, setReleaseScheduleKind] = useState<ReleaseScheduleKind>('once');
  const [releaseDailyTime, setReleaseDailyTime] = useState('09:00');
  const [releaseIntervalMinutes, setReleaseIntervalMinutes] = useState(60);
  const [releaseIntervalUnit, setReleaseIntervalUnit] = useState<'minute' | 'hour'>('minute');
  const [releaseEmptyPageLimit, setReleaseEmptyPageLimit] = useState(2);
  const [releaseIncremental, setReleaseIncremental] = useState(false);
  const [releaseBatchInput, setReleaseBatchInput] = useState(false);
  const [releaseTaskParamValues, setReleaseTaskParamValues] = useState<Record<string, string>>({});
  const [workspaceAdapterFile, setWorkspaceAdapterFile] = useState('');
  const [workspaceTemplateYaml, setWorkspaceTemplateYaml] = useState('');
  const [generatedAdapterRequired, setGeneratedAdapterRequired] = useState(false);
  const [releaseExit, setReleaseExit] = useState<{ x: number; y: number; scale: number } | null>(null);
  const [sessionInspectorTabs, setSessionInspectorTabs] = useState<SessionInspectorTab[]>([]);
  const [activeInspectorTabId, setActiveInspectorTabId] = useState<string | null>(null);
  const [inspectorMounted, setInspectorMounted] = useState(false);
  const [inspectorExpanded, setInspectorExpanded] = useState(false);
  const [inspectorAnimating, setInspectorAnimating] = useState(false);
  const templateScrollRef = useRef<HTMLDivElement | null>(null);
  const templateStageSectionRefs = useRef<Partial<Record<TemplateStageId, HTMLElement | null>>>({});
  const inspectorTransitionTimerRef = useRef<number | null>(null);
  const releaseExitTimerRef = useRef<number | null>(null);

  const hasSession = runStatus !== 'idle';
  const selectedCount = fields.filter((field) => selectedFields.has(field.name)).length;
  const qualityScore = mode === 'publish' ? 94 : mode === 'dryrun' ? 86 : mode === 'contract' ? 88 : 92;
  const activeStepIndex = processStepOrder.indexOf(activeProcessStep);
  const activeTemplate = useMemo(() => {
    if (!templateCatalog.length) return { id: 'empty', fileName: 'empty.yaml', displayName: 'Template', entries: [], raw: '' };

    if (workspaceTemplateYaml) {
      const generatedName = workspaceTemplateYaml.match(/^name:\s*["']?([^\s"']+)/m)?.[1] ?? 'generated_template';
      const generatedTitle = workspaceTemplateYaml.match(/^display_name:\s*["']?([^\r\n"']+)/m)?.[1]
        ?? urlPreflight?.title
        ?? generatedName.replace(/_/g, ' ');
      return {
        id: generatedName,
        fileName: `${generatedName}.yaml`,
        displayName: generatedTitle,
        entries: parseTemplateEntries(workspaceTemplateYaml),
        raw: workspaceTemplateYaml,
      };
    }

    const signal = `${templateId} ${submittedPrompt} ${intent} ${url}`.toLowerCase();
    const matchedTemplate = templateCatalog.find((template) => signal.includes(template.id.toLowerCase()));
    if (matchedTemplate) return matchedTemplate;
    return {
      id: 'generated_template',
      fileName: 'generated_template.yaml',
      displayName: urlPreflight?.title || urlPreflight?.host || 'Generated Template',
      entries: [],
      raw: '',
    };
  }, [intent, submittedPrompt, templateCatalog, templateId, url, urlPreflight, workspaceTemplateYaml]);
  const releaseTemplateParams = useMemo<ReleaseTemplateParam[]>(() => {
    const getEntryValue = (key: string) => {
      const entry = templateDraftEntries.find((item) => item.key === key);
      return stripYamlQuotes(templateValueDrafts[entry?.id ?? key] ?? entry?.value ?? '');
    };

    return templateDraftEntries
      .filter((entry) => /^params\[\d+\]\.name$/.test(entry.key))
      .map((entry) => {
        const prefix = entry.key.replace(/\.name$/, '');
        return {
          name: getEntryValue(entry.key),
          description: getEntryValue(`${prefix}.description`),
          defaultValue: getEntryValue(`${prefix}.default`),
          required: getEntryValue(`${prefix}.required`) === 'true',
        };
      })
      .filter((param) => Boolean(param.name));
  }, [templateDraftEntries, templateValueDrafts]);
  const releaseBatchConfig = useMemo<ReleaseBatchConfig | null>(() => {
    const getEntryValue = (key: string) => {
      const entry = templateDraftEntries.find((item) => item.key === key);
      return stripYamlQuotes(templateValueDrafts[entry?.id ?? key] ?? entry?.value ?? '');
    };
    const filePath = getEntryValue('batch_params.file_path');
    if (!filePath) return null;
    return {
      filePath,
      paramName: getEntryValue('batch_params.param_name'),
      batchSize: getEntryValue('batch_params.batch_size'),
      startLine: getEntryValue('batch_params.start_line'),
      limit: getEntryValue('batch_params.limit'),
      delay: getEntryValue('batch_params.delay'),
    };
  }, [templateDraftEntries, templateValueDrafts]);
  const browserPreviewHost = useMemo(() => {
    if (urlPreflight?.host) return urlPreflight.host;
    const candidate = url || submittedPrompt.match(/https?:\/\/[^\s，。；,]+/i)?.[0] || '';
    if (!candidate) return '';

    try {
      const normalized = candidate.replace(/\{\{\s*[^}]+\s*\}\}|\{\s*[^}]+\s*\}/g, 'sample');
      return new URL(normalized).host;
    } catch {
      return candidate.replace(/^https?:\/\//i, '').split('/')[0] ?? candidate;
    }
  }, [submittedPrompt, url, urlPreflight?.host]);
  const browserPreviewUrl = useMemo(() => {
    if (urlPreflight?.normalizedUrl) return urlPreflight.normalizedUrl;
    const candidate = url || submittedPrompt.match(/https?:\/\/[^\s锛屻€傦紱,]+/i)?.[0] || '';
    if (!candidate) return '';

    try {
      const normalized = candidate.replace(/\{\{\s*[^}]+\s*\}\}|\{\s*[^}]+\s*\}/g, 'sample');
      return new URL(normalized).toString();
    } catch {
      return '';
    }
  }, [submittedPrompt, url, urlPreflight?.normalizedUrl]);
  const browserPreviewTitle = useMemo(() => {
    if (urlPreflight?.title) return urlPreflight.title;
    const normalizedHost = browserPreviewHost.replace(/^www\./i, '').split(':')[0];
    const fallbackTitle = activeTemplate.displayName || 'Website';

    if (!normalizedHost) return fallbackTitle;
    if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(normalizedHost)) return fallbackTitle;

    const hostParts = normalizedHost.split('.').filter(Boolean);
    const titleSource = hostParts.length > 1 ? hostParts[hostParts.length - 2] : hostParts[0];
    const title = titleSource
      ?.split(/[-_]/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ');

    return title || fallbackTitle;
  }, [activeTemplate.displayName, browserPreviewHost, urlPreflight?.title]);
  const adapterFileName = useMemo(
    () => workspaceAdapterFile || `app/adapters/${activeTemplate.id === 'empty' ? 'generated_adapter' : activeTemplate.id}.py`,
    [activeTemplate.id, workspaceAdapterFile],
  );
  const browserPreviewFavicon = useMemo(
    () => (browserPreviewHost ? `https://www.google.com/s2/favicons?sz=64&domain_url=https://${browserPreviewHost}` : ''),
    [browserPreviewHost],
  );
  const adapterFileLabel = useMemo(
    () => adapterFileName.split('/').pop() ?? adapterFileName,
    [adapterFileName],
  );
  const adapterFileDirectory = useMemo(
    () => adapterFileName.split('/').slice(0, -1).join('/'),
    [adapterFileName],
  );
  const sessionSideMode = useMemo<'browser' | 'code'>(
    () => (workflowPhase === 'generating-adapter' || workflowPhase === 'release-template' ? 'code' : 'browser'),
    [workflowPhase],
  );
  const sessionStatusText = useMemo(
    () => (
      sessionSideMode === 'browser'
        ? browserPreviewTitle
        : adapterFileName
    ),
    [adapterFileName, browserPreviewTitle, sessionSideMode],
  );
  const currentInspectorTab = useMemo<SessionInspectorTab>(
    () => (
      sessionSideMode === 'browser'
        ? {
            id: `browser:${browserPreviewUrl || browserPreviewHost || 'source.local'}`,
            kind: 'browser',
            title: browserPreviewTitle,
            subtitle: browserPreviewUrl || browserPreviewHost || 'source.local',
          }
        : {
            id: `code:${adapterFileName}`,
            kind: 'code',
            title: adapterFileLabel,
            subtitle: adapterFileName,
          }
    ),
    [adapterFileLabel, adapterFileName, browserPreviewHost, browserPreviewTitle, browserPreviewUrl, sessionSideMode],
  );
  const sideInspectorVisible = inspectorMounted && sessionInspectorTabs.length > 0;
  const sideInspectorOpen = sideInspectorVisible && inspectorExpanded;
  const activeInspectorTab = useMemo(
    () => sessionInspectorTabs.find((tab) => tab.id === activeInspectorTabId) ?? sessionInspectorTabs[sessionInspectorTabs.length - 1] ?? null,
    [activeInspectorTabId, sessionInspectorTabs],
  );
  const activeWorkspacePanel = useMemo<WorkspacePanel | null>(() => {
    const panel = searchParams.get('panel');
    return panel === 'templates' || panel === 'tasks' ? panel : null;
  }, [searchParams]);
  const templateStages = useMemo(() => {
    const stageSet = new Set(templateDraftEntries.map((entry) => entry.stageId));
    return templateStageOrder.filter((stageId) => stageSet.has(stageId));
  }, [templateDraftEntries]);
  const visibleTemplateStages = useMemo(
    () => templateStages.filter(
      (stageId) => processStepOrder.indexOf(templateStageMeta[stageId].threshold) <= activeStepIndex,
    ),
    [activeStepIndex, templateStages],
  );
  const visibleTemplateEntries = useMemo(
    () => templateDraftEntries.filter((entry) => visibleTemplateStages.includes(entry.stageId)),
    [templateDraftEntries, visibleTemplateStages],
  );
  const visibleTemplateValueCount = useMemo(
    () => visibleTemplateEntries.filter((entry) => entry.nodeType === 'value').length,
    [visibleTemplateEntries],
  );
  const templateStagesReady = templateStages.length > 0
    && visibleTemplateStages.length === templateStages.length;
  const templateAnalysisComplete = activeProcessStep === 'contract'
    && templateStagesReady;
  const templateReadyForConfirm = workflowPhase === 'confirm-template' && templateStagesReady;
  const displayWorkflowPhase = guidePreviewPhase ?? workflowPhase;
  const templateCollapsed = displayWorkflowPhase === 'generating-adapter' || displayWorkflowPhase === 'release-template';
  const sessionGuideSteps = useMemo<SessionGuideStepId[]>(() => {
    const steps: SessionGuideStepId[] = [...visibleTemplateStages];

    if (templateStagesReady || templateCollapsed) {
      steps.push('confirm-template');
    }
    if (workflowPhase === 'generating-adapter' || workflowPhase === 'release-template') {
      steps.push('generate-adapter');
    }
    if (workflowPhase === 'release-template') {
      steps.push('save-template');
    }

    return steps;
  }, [templateCollapsed, templateStagesReady, visibleTemplateStages, workflowPhase]);
  const currentGuideStep = useMemo<SessionGuideStepId | null>(() => {
    if (workflowPhase === 'release-template') return 'save-template';
    if (workflowPhase === 'generating-adapter') return 'generate-adapter';
    if (workflowPhase === 'confirm-template') return 'confirm-template';
    return activeTemplateStage ?? visibleTemplateStages[visibleTemplateStages.length - 1] ?? null;
  }, [activeTemplateStage, visibleTemplateStages, workflowPhase]);
  const displayGuideStep = useMemo<SessionGuideStepId | null>(() => {
    if (displayWorkflowPhase === 'release-template') return 'save-template';
    if (displayWorkflowPhase === 'generating-adapter') return 'generate-adapter';
    return activeTemplateStage ?? visibleTemplateStages[visibleTemplateStages.length - 1] ?? null;
  }, [activeTemplateStage, displayWorkflowPhase, visibleTemplateStages]);
  const activeGuideIndex = currentGuideStep ? sessionGuideSteps.indexOf(currentGuideStep) : -1;
  const displayTemplatePanel = displayWorkflowPhase === 'analyzing-template' || displayWorkflowPhase === 'confirm-template';
  const displayAdapterPanel = displayWorkflowPhase === 'generating-adapter';
  const showPinnedTemplateTab = templateTabVisible && !displayTemplatePanel;
  const showPinnedAdapterTab = adapterTabVisible && !displayAdapterPanel;
  const adapterDiffStats = useMemo(
    () => ({
      added: Math.max(visibleTemplateEntries.length * 4 + 12, 32),
      removed: Math.max(Math.round(visibleTemplateEntries.length * 0.75), 6),
    }),
    [visibleTemplateEntries.length],
  );
  const adapterBuildPlan = useMemo<AdapterBuildStep[]>(() => {
    const getEntryValue = (key: string, fallback = '') => {
      const entry = templateDraftEntries.find((item) => item.key === key);
      return stripYamlQuotes(
        (entry ? templateValueDrafts[entry.id] : undefined)
        ?? entry?.value
        ?? fallback,
      );
    };

    const baseUrl = getEntryValue('base_url', url || 'https://source.local');
    const responseType = (getEntryValue('response_type', 'html') || 'html').toLowerCase();
    const requestMethod = (getEntryValue('list_request.method', 'GET') || 'GET').toUpperCase();
    const paginationType = getEntryValue('list_pagination.type', 'page_number') || 'page_number';
    const className = `${toPascalCase(activeTemplate.id || 'Generated')}Adapter`;
    const fieldNames = templateDraftEntries
      .filter((entry) => /^list_fields\[\d+\]\.name$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const fieldTypes = templateDraftEntries
      .filter((entry) => /^list_fields\[\d+\]\.field_type$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const paramNames = templateDraftEntries
      .filter((entry) => /^params\[\d+\]\.name$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const dedupKeys = templateDraftEntries
      .filter((entry) => /^dedup_fields\[\d+\]$/.test(entry.key) && entry.nodeType === 'value')
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const downloadSelectors = templateDraftEntries
      .filter((entry) => /^download\[\d+\]\.selector$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const downloadAssetTypes = templateDraftEntries
      .filter((entry) => /^download\[\d+\]\.asset_type$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const dynamicSteps: AdapterBuildStep[] = adapterBuildStepBlueprints.flatMap((blueprint): AdapterBuildStep[] => {
      if (blueprint.id === 'request') {
        const elapsedSeconds = 18 + paramNames.length * 7 + (paginationType ? 8 : 0);
        return [{
          id: blueprint.id,
          title: `${requestMethod} request contract`,
          desc: `${responseType.toUpperCase()} adapter entry, task inputs and pagination flow are being bound.`,
          log: `adapter draft: ${requestMethod.toLowerCase()} request contract bound`,
          elapsed: formatElapsedLabel(elapsedSeconds),
          details: [
            `Input parameters resolved: ${paramNames.length ? paramNames.join(', ') : 'no explicit params'}.`,
            `Request method, base URL and encoding align to ${requestMethod} ${baseUrl}.`,
            `Pagination strategy uses ${paginationType} before parser execution starts.`,
          ],
        }];
      }

      if (blueprint.id === 'fields') {
        const elapsedSeconds = 26 + fieldNames.length * 6 + dedupKeys.length * 5;
        return [{
          id: blueprint.id,
          title: `${fieldNames.length || 0} field mapping`,
          desc: 'Collected fields, output types and dedup identity are being normalized.',
          log: 'adapter draft: field mapping merged into parser',
          elapsed: formatElapsedLabel(elapsedSeconds),
          details: [
            `Mapped fields: ${fieldNames.slice(0, 6).join(', ') || 'no fields detected yet'}.`,
            `Output types sampled: ${fieldTypes.slice(0, 4).join(', ') || responseType}.`,
            `Record identity uses ${dedupKeys.join(', ') || 'fallback source_url + title'} for deduplication.`,
          ],
        }];
      }

      if (blueprint.id === 'download') {
        if (!downloadSelectors.length) return [];

        const elapsedSeconds = 16 + downloadSelectors.length * 8;
        return [{
          id: blueprint.id,
          title: `${downloadSelectors.length} asset hooks`,
          desc: 'Asset selectors, file hints and downstream download handoff are being prepared.',
          log: 'adapter draft: download policy attached',
          elapsed: formatElapsedLabel(elapsedSeconds),
          details: [
            `Asset selectors resolved: ${downloadSelectors.slice(0, 3).join(', ')}${downloadSelectors.length > 3 ? '...' : ''}.`,
            `Asset categories include ${downloadAssetTypes.slice(0, 4).join(', ') || 'attachment'}.`,
            'Download handoff points are reserved for storage and worker execution.',
          ],
        }];
      }

      const elapsedSeconds = 20 + Math.round(adapterDiffStats.added / 18) + Math.round(adapterDiffStats.removed / 12);
      return [{
        id: blueprint.id,
        title: `Finalize ${className}`,
        desc: 'The adapter scaffold, parser route and diff footprint are being finalized.',
        log: 'adapter draft: file scaffold completed',
        elapsed: formatElapsedLabel(elapsedSeconds),
        details: [
          `Parser entry targets ${responseType === 'json' ? 'JSON payload traversal' : 'HTML document traversal'}.`,
          `Generated scaffold locks into ${activeTemplate.id || 'generated_template'} with ${fieldNames.length} mapped fields.`,
          `Current file delta is +${adapterDiffStats.added} / -${adapterDiffStats.removed}.`,
        ],
      }];
    });

    return dynamicSteps;
  }, [activeTemplate.id, adapterDiffStats.added, adapterDiffStats.removed, templateDraftEntries, templateValueDrafts, url]);
  const adapterPreviewLines = useMemo<AdapterPreviewLine[]>(() => {
    const getEntryValue = (key: string, fallback = '') => {
      const entry = templateDraftEntries.find((item) => item.key === key);
      return stripYamlQuotes(
        (entry ? templateValueDrafts[entry.id] : undefined)
        ?? entry?.value
        ?? fallback,
      );
    };

    const baseUrl = getEntryValue('base_url', url || 'https://source.local');
    const responseType = (getEntryValue('response_type', 'html') || 'html').toLowerCase();
    const requestMethod = (getEntryValue('list_request.method', 'GET') || 'GET').toUpperCase();
    const paginationType = getEntryValue('list_pagination.type', 'page_number') || 'page_number';
    const className = `${toPascalCase(activeTemplate.id || 'Generated')}Adapter`;
    const fieldNames = templateDraftEntries
      .filter((entry) => /^list_fields\[\d+\]\.name$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const dedupKeys = templateDraftEntries
      .filter((entry) => /^dedup_fields\[\d+\]$/.test(entry.key) && entry.nodeType === 'value')
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);
    const downloadSelectors = templateDraftEntries
      .filter((entry) => /^download\[\d+\]\.selector$/.test(entry.key))
      .map((entry) => stripYamlQuotes(templateValueDrafts[entry.id] ?? entry.value))
      .filter(Boolean);

    const responseAccessor = responseType === 'json' ? 'response.json()' : 'response.text()';
    const dedupLiteral = dedupKeys.length
      ? `[${dedupKeys.map((key) => `'${key}'`).join(', ')}]`
      : "['source_url', 'title']";
    const fieldComment = fieldNames.length ? fieldNames.join(', ') : 'title, source_url';
    const downloadComment = downloadSelectors.length ? downloadSelectors.join(', ') : 'no download selectors';

    return [
      { key: 'adapter-1', lineNumber: 1, prefix: '+', added: true, content: `class ${className}(BaseAdapter):` },
      { key: 'adapter-2', lineNumber: 2, prefix: '+', added: true, content: `    template_key = '${activeTemplate.id || 'generated_template'}'` },
      { key: 'adapter-3', lineNumber: 3, prefix: '+', added: true, content: `    start_urls = ['${baseUrl}']` },
      { key: 'adapter-4', lineNumber: 4, prefix: ' ', content: '' },
      { key: 'adapter-5', lineNumber: 5, prefix: '+', added: true, content: '    async def fetch_list(self, page: int = 1):' },
      { key: 'adapter-6', lineNumber: 6, prefix: '+', added: true, content: `        return await self.request(method='${requestMethod}', page=page)` },
      { key: 'adapter-7', lineNumber: 7, prefix: ' ', content: '' },
      { key: 'adapter-8', lineNumber: 8, prefix: '+', added: true, content: '    def parse_list(self, response):' },
      { key: 'adapter-9', lineNumber: 9, prefix: '+', added: true, content: `        payload = ${responseAccessor}` },
      { key: 'adapter-10', lineNumber: 10, prefix: '+', added: true, content: `        # fields: ${fieldComment}` },
      { key: 'adapter-11', lineNumber: 11, prefix: '+', added: true, content: `        # dedup: ${dedupLiteral}` },
      { key: 'adapter-12', lineNumber: 12, prefix: '+', added: true, content: `        # pagination: ${paginationType}` },
      { key: 'adapter-13', lineNumber: 13, prefix: '+', added: true, content: `        # downloads: ${downloadComment}` },
    ];
  }, [activeTemplate.id, templateDraftEntries, templateValueDrafts, url]);
  const adapterProgressPercent = Math.min(
    100,
    workflowPhase === 'release-template'
      ? 100
      : Math.round((adapterBuildIndex / adapterBuildPlan.length) * 100),
  );
  const sessionPromptPlaceholder = useMemo(() => {
    if (displayWorkflowPhase === 'confirm-template') return 'Confirm fields, dedup keys or download rules before locking the template';
    if (displayWorkflowPhase === 'generating-adapter') return 'Refine adapter logic, parser hints or asset handling';
    if (displayWorkflowPhase === 'release-template') return 'Choose draft, archive, publish and whether to launch a task';
    return 'Refine fields, request rules or crawl boundaries while the template is forming';
  }, [displayWorkflowPhase]);
  const sessionHeaderMeta = useMemo(() => {
    if (displayWorkflowPhase === 'release-template') {
      return {
        eyebrow: 'Release Gate',
        title: 'Template Release',
        subtitle: activeTemplate.fileName,
        stat: releaseActionMeta[selectedReleaseAction].title,
      };
    }
    if (displayWorkflowPhase === 'generating-adapter') {
      const currentAdapterStep = adapterBuildPlan[Math.min(adapterBuildIndex, adapterBuildPlan.length - 1)];
      return {
        eyebrow: 'Adapter Build',
        title: 'Implementation Steps',
        subtitle: currentAdapterStep?.title ?? 'Adapter generation in progress',
        stat: `${Math.min(adapterBuildIndex + 1, adapterBuildPlan.length)}/${adapterBuildPlan.length}`,
      };
    }
    return {
      eyebrow: displayWorkflowPhase === 'confirm-template' ? 'Template Confirm' : 'Template Analysis',
      title: activeTemplate.displayName,
      subtitle: activeTemplate.fileName,
      stat: `${visibleTemplateValueCount} keys`,
    };
  }, [
    activeTemplate.displayName,
    activeTemplate.fileName,
    adapterBuildIndex,
    displayWorkflowPhase,
    selectedReleaseAction,
    visibleTemplateValueCount,
  ]);

  useEffect(() => () => {
    analyzeStreamRef.current?.close();
    if (simulationTimerRef.current) {
      window.clearTimeout(simulationTimerRef.current);
    }
    if (promptGenerationTimerRef.current) {
      window.clearTimeout(promptGenerationTimerRef.current);
    }
    if (inspectorTransitionTimerRef.current) {
      window.clearTimeout(inspectorTransitionTimerRef.current);
    }
    if (releaseExitTimerRef.current) {
      window.clearTimeout(releaseExitTimerRef.current);
    }
  }, []);

  useEffect(() => {
    setExpandedStep(mode);
  }, [mode]);

  useEffect(() => {
    if (!hasSession) {
      setUrlPreflight(null);
      setHoveredStageGuideStep(null);
      setActiveTemplateStage(null);
      setTemplateStageVisibility({});
      setGuidePreviewPhase(null);
      setTemplateTabVisible(false);
      setTemplateTabAnimating(false);
      setAdapterTabVisible(false);
      setAdapterTabAnimating(false);
      setExpandingPinnedPanel(null);
      setPromptGenerating(false);
      setWorkflowPhase('analyzing-template');
      setAdapterBuildIndex(0);
      setExpandedAdapterStep(0);
      setSelectedReleaseAction('draft');
      setSelectedTaskPublishMode('launch');
      setReleaseScheduleKind('once');
      setReleaseDailyTime('09:00');
      setReleaseIntervalMinutes(60);
      setReleaseIncremental(false);
      setReleaseBatchInput(false);
      setReleaseTaskParamValues({});
      setGeneratedAdapterRequired(false);
      setReleaseExit(null);
      if (inspectorTransitionTimerRef.current) {
        window.clearTimeout(inspectorTransitionTimerRef.current);
        inspectorTransitionTimerRef.current = null;
      }
      setInspectorMounted(false);
      setInspectorExpanded(false);
      setInspectorAnimating(false);
      setSessionInspectorTabs([]);
      setActiveInspectorTabId(null);
      if (promptGenerationTimerRef.current) {
        window.clearTimeout(promptGenerationTimerRef.current);
        promptGenerationTimerRef.current = null;
      }
    }
  }, [hasSession]);

  const pushLiveLog = useCallback((log: string) => {
    setLiveLogs((prev) => [log, ...prev].slice(0, 8));
  }, []);

  const clearInspectorTransitionTimer = useCallback(() => {
    if (inspectorTransitionTimerRef.current) {
      window.clearTimeout(inspectorTransitionTimerRef.current);
      inspectorTransitionTimerRef.current = null;
    }
  }, []);

  const clearSessionInspectorImmediately = useCallback(() => {
    clearInspectorTransitionTimer();
    setInspectorMounted(false);
    setInspectorExpanded(false);
    setInspectorAnimating(false);
    setSessionInspectorTabs([]);
    setActiveInspectorTabId(null);
  }, [clearInspectorTransitionTimer]);

  const openSessionInspector = useCallback((tab: SessionInspectorTab = currentInspectorTab) => {
    clearInspectorTransitionTimer();
    setSessionInspectorTabs((prev) => (prev.some((item) => item.id === tab.id) ? prev : [...prev, tab]));
    setActiveInspectorTabId(tab.id);
    setInspectorMounted(true);
    setInspectorAnimating(true);
    window.requestAnimationFrame(() => {
      setInspectorExpanded(true);
    });
    inspectorTransitionTimerRef.current = window.setTimeout(() => {
      inspectorTransitionTimerRef.current = null;
      setInspectorAnimating(false);
    }, INSPECTOR_TRANSITION_MS);
  }, [clearInspectorTransitionTimer, currentInspectorTab]);

  const closeSessionInspector = useCallback(() => {
    clearInspectorTransitionTimer();
    if (!inspectorMounted) {
      clearSessionInspectorImmediately();
      return;
    }
    setInspectorAnimating(true);
    setInspectorExpanded(false);
    inspectorTransitionTimerRef.current = window.setTimeout(() => {
      inspectorTransitionTimerRef.current = null;
      setInspectorMounted(false);
      setInspectorAnimating(false);
      setSessionInspectorTabs([]);
      setActiveInspectorTabId(null);
    }, INSPECTOR_TRANSITION_MS);
  }, [clearInspectorTransitionTimer, clearSessionInspectorImmediately, inspectorMounted]);

  const closeSessionInspectorTab = useCallback((tabId: string) => {
    setSessionInspectorTabs((prev) => {
      const index = prev.findIndex((tab) => tab.id === tabId);
      const next = prev.filter((tab) => tab.id !== tabId);

      if (!next.length) {
        closeSessionInspector();
        return prev;
      }

      setActiveInspectorTabId((current) => {
        if (current !== tabId) {
          return current && next.some((tab) => tab.id === current) ? current : next[next.length - 1]?.id ?? null;
        }

        const fallback = next[index] ?? next[index - 1] ?? null;
        return fallback?.id ?? null;
      });

      return next;
    });
  }, [closeSessionInspector]);

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
    setHoveredStageGuideStep(null);
    setActiveTemplateStage(null);
    setTemplateStageVisibility({});
    setGuidePreviewPhase(null);
    setTemplateTabVisible(false);
    setTemplateTabAnimating(false);
    setAdapterTabVisible(false);
    setAdapterTabAnimating(false);
    setExpandingPinnedPanel(null);
    setSelectedLogStep('prepare');
    setCompletedProcessSteps(new Set());
    setVisibleProcessSteps(['prepare']);
    setScanPulse(0);
    setPromptGenerating(false);
    setWorkflowPhase('analyzing-template');
    setAdapterBuildIndex(0);
    setExpandedAdapterStep(0);
    setSelectedReleaseAction('draft');
    setSelectedTaskPublishMode('launch');
    setSessionInspectorTabs([]);
    setActiveInspectorTabId(null);
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

  useEffect(() => {
    if (!hasSession || workflowPhase !== 'analyzing-template' || !templateAnalysisComplete) return;

    setWorkflowPhase('confirm-template');
    setGuidePreviewPhase(null);
    setRunStatus('completed');
    pushLiveLog('template contract ready for confirmation');
  }, [hasSession, pushLiveLog, templateAnalysisComplete, workflowPhase]);

  useEffect(() => {
    if (!templateCollapsed) {
      setTemplateTabVisible(false);
      setTemplateTabAnimating(false);
      return undefined;
    }

    setTemplateTabVisible(true);
    setTemplateTabAnimating(true);
    const timer = window.setTimeout(() => {
      setTemplateTabAnimating(false);
    }, 360);

    return () => window.clearTimeout(timer);
  }, [templateCollapsed]);

  useEffect(() => {
    if (workflowPhase !== 'release-template') {
      setAdapterTabVisible(false);
      setAdapterTabAnimating(false);
      return undefined;
    }

    setAdapterTabVisible(true);
    setAdapterTabAnimating(true);
    const timer = window.setTimeout(() => {
      setAdapterTabAnimating(false);
    }, 360);

    return () => window.clearTimeout(timer);
  }, [workflowPhase]);

  useEffect(() => {
    if (workflowPhase !== 'generating-adapter') return;

    const currentStepIndex = Math.min(adapterBuildIndex, adapterBuildPlan.length - 1);
    const previousStepIndex = Math.max(0, Math.min(adapterBuildIndex - 1, adapterBuildPlan.length - 1));

    setExpandedAdapterStep((prev) => (
      prev === null || prev === previousStepIndex ? currentStepIndex : prev
    ));
  }, [adapterBuildIndex, workflowPhase]);

  useEffect(() => {
    if (!expandingPinnedPanel) return undefined;

    const timer = window.setTimeout(() => {
      setExpandingPinnedPanel(null);
    }, 420);

    return () => window.clearTimeout(timer);
  }, [expandingPinnedPanel]);

  useEffect(() => {
    if (!guidePreviewPhase) return;

    const previewLocked = guidePreviewPhase === 'confirm-template' && !templateStagesReady;
    const previewPastWorkflow = (
      (guidePreviewPhase === 'generating-adapter' && workflowPhase === 'confirm-template')
      || (guidePreviewPhase === 'release-template' && workflowPhase !== 'release-template')
    );

    if (previewLocked || previewPastWorkflow) {
      setGuidePreviewPhase(null);
    }
  }, [guidePreviewPhase, templateStagesReady, workflowPhase]);

  useEffect(() => {
    if (workflowPhase !== 'generating-adapter') return undefined;

    if (adapterBuildIndex >= adapterBuildPlan.length) {
      const timer = window.setTimeout(() => {
        setCompletedProcessSteps((prev) => {
          const next = new Set(prev);
          next.add('dryrun');
          return next;
        });
        setWorkflowPhase('release-template');
        setMode('publish');
        setRunStatus('completed');
        setActiveProcessStep('publish');
        setSelectedLogStep('publish');
        setVisibleProcessSteps((prev) => (prev.includes('publish') ? prev : [...prev, 'publish']));
        pushLiveLog('adapter draft completed; release gate unlocked');
      }, 280);

      return () => window.clearTimeout(timer);
    }

    const nextStep = adapterBuildPlan[adapterBuildIndex];
    if (!nextStep) return undefined;

    const timer = window.setTimeout(() => {
      pushLiveLog(nextStep.log);
      setAdapterBuildIndex((prev) => prev + 1);
    }, adapterBuildIndex === 0 ? 520 : 860);

    return () => window.clearTimeout(timer);
  }, [adapterBuildIndex, adapterBuildPlan, pushLiveLog, workflowPhase]);

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

  const handleAnalyze = useCallback(async () => {
    if (preflightLoading) return;
    const draftPrompt = taskDraft.trim();
    const currentReference = (submittedPrompt || intent || url).trim();
    const sourcePrompt = hasSession && draftPrompt
      ? `${currentReference} ${draftPrompt}`.trim()
      : (draftPrompt || currentReference).trim();
    const promptUrl = extractUrlFromPrompt(sourcePrompt);
    const targetUrl = promptUrl || url;
    const error = validateUrl(targetUrl);
    if (error) {
      const errorMessage = targetUrl ? error : '请在问题中包含目标 URL';
      message.error(errorMessage);
      return;
    }

    setPreflightLoading(true);
    let preflight: UrlPreflightResponse;
    try {
      preflight = await preflightUrl(targetUrl);
    } catch (preflightError) {
      const errorMessage = preflightError instanceof Error ? preflightError.message : 'URL 预检服务不可用';
      message.error(errorMessage);
      return;
    } finally {
      setPreflightLoading(false);
    }
    if (!preflight.ok) {
      const errorMessage = preflight.errorMessage || '目标网页无法访问';
      message.error(errorMessage);
      return;
    }

    const verifiedUrl = preflight.normalizedUrl;
    setUrlPreflight(preflight);
    setUrl(verifiedUrl);
    setWorkspaceTemplateYaml('');
    setWorkspaceAdapterFile('');
    setGeneratedAdapterRequired(false);

    const normalizedPrompt = sourcePrompt || verifiedUrl;
    setSubmittedPrompt(normalizedPrompt);
    setTaskDraft('');
    setIntent(normalizedPrompt);
    analyzeStreamRef.current?.close();
    resetSimulation();
    setStreamError('');
    setRunStatus('running');
    setMode('explore');
    setExpandedStep('explore');
    const es = createAnalyzeStream(verifiedUrl);
    analyzeStreamRef.current = es;

    es.addEventListener('fields', (event: MessageEvent) => {
      const data: { fields: FieldDef[] } = JSON.parse(event.data);
      setFields(data.fields);
      setSelectedFields(new Set(data.fields.map((field) => field.name)));
      pushLiveLog('服务端字段候选已同步');
    });

    es.addEventListener('complete', (event: MessageEvent) => {
      const data: {
        templateId: string;
        templateYaml?: string;
        adapterPath?: string;
        agent?: { decision?: { requires_adapter?: boolean } };
      } = JSON.parse(event.data);
      setTemplateId(data.templateId);
      if (data.templateYaml) {
        setWorkspaceTemplateYaml(data.templateYaml);
        setTemplateDraftEntries(parseTemplateEntries(data.templateYaml));
        setTemplateValueDrafts({});
      }
      if (data.adapterPath) setWorkspaceAdapterFile(data.adapterPath);
      setGeneratedAdapterRequired(Boolean(data.agent?.decision?.requires_adapter));
      pushLiveLog('服务端合约草案已生成，等待前端确认');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.addEventListener('error', () => {
      setStreamError('分析服务暂不可用，请检查服务端日志后重试。');
      setRunStatus('completed');
      pushLiveLog('分析服务返回错误，已停止当前流程');
      es.close();
      analyzeStreamRef.current = null;
    });

    es.onerror = () => {
      setStreamError('SSE 连接已断开，请重新发起分析。');
      setRunStatus('completed');
      pushLiveLog('SSE 连接断开，当前流程已停止');
      es.close();
      analyzeStreamRef.current = null;
    };
  }, [extractUrlFromPrompt, hasSession, intent, message, preflightLoading, resetSimulation, submittedPrompt, taskDraft, url, validateUrl]);

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
    } catch (error) {
      setDryRunResult(null);
      setRunStatus('completed');
      message.error('试跑失败，请检查服务端日志后重试');
      pushLiveLog(`dry run failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [message, pushLiveLog, templateId]);

  const handleSave = useCallback(async (showMessage = true) => {
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
      if (showMessage) message.success('模板和适配器已发布');
    } catch (error) {
      if (showMessage) message.error('模板生成失败，请检查服务端日志');
      pushLiveLog(`template generation failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    setMode('publish');
    setRunStatus('completed');
  }, [fields, maxPages, message, pushLiveLog, selectedFields, url]);

  const handleConfirmTemplate = useCallback(async () => {
    if (!templateReadyForConfirm) return;

    setCompletedProcessSteps((prev) => {
      const next = new Set(prev);
      next.add('contract');
      return next;
    });
    setGuidePreviewPhase(null);
    setWorkflowPhase('generating-adapter');
    setMode('dryrun');
    setRunStatus('running');
    setActiveProcessStep('dryrun');
    setSelectedLogStep('dryrun');
    setVisibleProcessSteps((prev) => (prev.includes('dryrun') ? prev : [...prev, 'dryrun']));
    setAdapterBuildIndex(0);
    setExpandedAdapterStep(0);
    pushLiveLog('template contract confirmed; adapter generation started');
    try {
      const adapter = await generateAdapterApi(url, 'default', templateId);
      pushLiveLog(`adapter generated by service: ${adapter.adapterId}`);
    } catch (error) {
      setRunStatus('completed');
      message.error('Adapter generation failed');
      pushLiveLog(`adapter generation failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [message, pushLiveLog, templateId, templateReadyForConfirm, url]);

  const playReleaseCompletionAnimation = useCallback((includeTask: boolean) => {
    if (releaseExitTimerRef.current) return;

    const sessionShell = document.querySelector<HTMLElement>('.ai-session-shell');
    const templateButton = document.querySelector<HTMLElement>('[data-ai-workspace-panel="templates"]');

    const finish = () => {
      const attentionFrames: Keyframe[] = [
        { transform: 'translateX(0) rotate(0deg) scale(1)' },
        { transform: 'translateX(-3px) rotate(-8deg) scale(1.08)' },
        { transform: 'translateX(3px) rotate(8deg) scale(1.08)' },
        { transform: 'translateX(-2px) rotate(-5deg) scale(1.04)' },
        { transform: 'translateX(2px) rotate(5deg) scale(1.04)' },
        { transform: 'translateX(0) rotate(0deg) scale(1)' },
      ];
      const attentionOptions: KeyframeAnimationOptions = {
        duration: 620,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
      };

      templateButton?.animate(attentionFrames, attentionOptions);
      if (includeTask) {
        document.querySelector<HTMLElement>('[data-ai-workspace-panel="tasks"]')
          ?.animate(attentionFrames, attentionOptions);
      }

      if (activeWorkspacePanel) {
        const nextParams = new URLSearchParams(searchParams);
        nextParams.delete('panel');
        setSearchParams(nextParams);
      }
      setRunStatus('idle');
      setReleaseExit(null);
      releaseExitTimerRef.current = null;
    };

    if (!sessionShell || !templateButton) {
      finish();
      return;
    }

    const shellRect = sessionShell.getBoundingClientRect();
    const targetRect = templateButton.getBoundingClientRect();
    const x = targetRect.left + targetRect.width / 2 - (shellRect.left + shellRect.width / 2);
    const y = targetRect.top + targetRect.height / 2 - (shellRect.top + shellRect.height / 2);
    const scale = Math.min(0.06, 30 / Math.max(shellRect.width, shellRect.height));

    setReleaseExit({ x, y, scale });
    releaseExitTimerRef.current = window.setTimeout(finish, 680);
  }, [activeWorkspacePanel, searchParams, setSearchParams]);

  const handleApplyReleaseAction = useCallback(async () => {
    const releaseLabel = releaseActionMeta[selectedReleaseAction].title;
    const taskLabel = taskPublishMeta[selectedTaskPublishMode].title;
    const createsTask = selectedReleaseAction === 'publish' && selectedTaskPublishMode === 'launch';

    pushLiveLog(`release action: ${releaseLabel.toLowerCase()} | task: ${taskLabel.toLowerCase()}`);

    try {
      const status = selectedReleaseAction === 'publish'
        ? 'active'
        : selectedReleaseAction === 'archive'
          ? 'deprecated'
          : 'draft';
      const parameters = Object.fromEntries(releaseTemplateParams.map((param) => [
        param.name,
        releaseTaskParamValues[param.name] ?? param.defaultValue,
      ]));
      await releaseWorkspaceTemplate({
        analysisId: templateId,
        name: activeTemplate.id === 'empty' ? templateId : activeTemplate.id,
        version: 'v1.0',
        title: browserPreviewTitle,
        domain: browserPreviewHost,
        status,
        yaml_content: workspaceTemplateYaml || activeTemplate.raw,
        adapter: generatedAdapterRequired ? adapterFileName : '',
        description: releaseActionMeta[selectedReleaseAction].desc,
        output_tag: outputTarget,
        metadata: { field_count: selectedCount },
        task: createsTask ? {
          name: `${browserPreviewTitle} task`,
          template_name: activeTemplate.id === 'empty' ? templateId : activeTemplate.id,
          template_version: 'v1.0',
          schedule: {
            mode: releaseScheduleKind,
            daily_time: releaseDailyTime,
            interval_value: releaseIntervalMinutes,
            interval_unit: releaseIntervalUnit,
          },
          parameters,
          policies: {
            concurrency,
            incremental: releaseIncremental,
            respect_robots: respectRobots,
            drift_guard: enableDriftGuard,
            empty_page_limit: releaseEmptyPageLimit,
            batch_input: releaseBatchInput,
          },
          owner: 'AI Collect',
        } : undefined,
      });
      setRunStatus('completed');
      message.success(createsTask
        ? 'Template published and crawl task created'
        : `${releaseLabel} ready`);
      playReleaseCompletionAnimation(createsTask);
    } catch (error) {
      message.error('Release failed; no template or task was saved');
      pushLiveLog(`release failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }, [activeTemplate.id, activeTemplate.raw, adapterFileName, browserPreviewHost, browserPreviewTitle, concurrency, enableDriftGuard, generatedAdapterRequired, message, outputTarget, playReleaseCompletionAnimation, pushLiveLog, releaseDailyTime, releaseEmptyPageLimit, releaseIncremental, releaseIntervalMinutes, releaseIntervalUnit, releaseScheduleKind, releaseTaskParamValues, releaseTemplateParams, respectRobots, selectedCount, selectedReleaseAction, selectedTaskPublishMode, templateId, workspaceTemplateYaml]);

  const handleReleaseActionSelect = useCallback((action: ReleaseAction) => {
    setSelectedReleaseAction(action);
    if (action === 'publish') setSelectedTaskPublishMode('launch');
  }, []);

  const scrollTemplateToGuideStep = useCallback((step: TemplateStageId | 'confirm-template', defer = false) => {
    const scrollToStep = () => {
      const scrollElement = templateScrollRef.current;
      if (!scrollElement) return;

      if (step === 'confirm-template') {
        scrollElement.scrollTo({
          top: scrollElement.scrollHeight,
          behavior: 'smooth',
        });
        return;
      }

      const node = templateStageSectionRefs.current[step];
      if (!node) return;

      scrollElement.scrollTo({
        top: Math.max(0, node.offsetTop - 12),
        behavior: 'smooth',
      });
    };

    if (!defer) {
      scrollToStep();
      return;
    }

    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        scrollToStep();
      });
    });
  }, []);

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
    const guideLabel = displayWorkflowPhase === 'release-template'
      ? 'release hint'
      : displayWorkflowPhase === 'generating-adapter'
        ? 'adapter hint'
        : 'template hint';
    pushLiveLog(`${guideLabel}: ${guide}`);
    setTaskDraft('');
    setScanPulse((prev) => prev + 1);
    triggerPromptGeneration();
  }, [displayWorkflowPhase, pushLiveLog, taskDraft, triggerPromptGeneration]);

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

  const handleWorkspaceTemplateApply = useCallback((draft: { yaml: string; adapter: string }) => {
    setWorkspaceTemplateYaml(draft.yaml);
    setWorkspaceAdapterFile(draft.adapter);
    setTemplateDraftEntries(parseTemplateEntries(draft.yaml));
    setTemplateValueDrafts({});
  }, []);

  const renderMissionPanel = (variant: 'hero' | 'compact') => {
    if (variant === 'hero') {
      return (
        <section className="ai-prompt-landing">
          <div className="ai-prompt-copy">
            <h1 className="ai-prompt-title">嗨，<span className="ai-prompt-name">{currentUserName}</span>，又有新灵感了吗？</h1>
          </div>

          <div className="ai-prompt-shell">
            <span className="ai-prompt-leading-icon" aria-hidden="true">
              <GlobalOutlined spin={preflightLoading} />
            </span>
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
        <Button type="primary" icon={<SaveOutlined />} onClick={() => void handleSave()}>发布模板</Button>
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
    setTemplateDraftEntries(activeTemplate.entries);
    setTemplateValueDrafts(
      Object.fromEntries(activeTemplate.entries.map((entry) => [entry.id, entry.value])),
    );
  }, [activeTemplate]);

  useEffect(() => {
    const scrollElement = templateScrollRef.current;
    if (!scrollElement || !visibleTemplateStages.length) return undefined;

    let frame = 0;
    const observers: ResizeObserver[] = [];

    const measureVisibleStages = () => {
      if (frame) {
        window.cancelAnimationFrame(frame);
      }

      frame = window.requestAnimationFrame(() => {
        const viewportTop = scrollElement.scrollTop;
        const viewportHeight = scrollElement.clientHeight;
        const viewportBottom = viewportTop + viewportHeight;
        const anchorLine = viewportTop + Math.min(144, viewportHeight * 0.32);

        const nextVisibility: Partial<Record<TemplateStageId, number>> = {};
        let nextActiveStage: TemplateStageId | null = null;
        let fallbackStage: TemplateStageId | null = null;
        let smallestDistance = Number.POSITIVE_INFINITY;

        visibleTemplateStages.forEach((stageId) => {
          const node = templateStageSectionRefs.current[stageId];
          if (!node) return;

          const top = node.offsetTop;
          const height = node.offsetHeight;
          const bottom = top + height;
          const visibleTop = Math.max(top, viewportTop);
          const visibleBottom = Math.min(bottom, viewportBottom);
          const visibleHeight = Math.max(0, visibleBottom - visibleTop);
          const visibility = height > 0 ? Math.min(1, visibleHeight / height) : 0;

          nextVisibility[stageId] = visibility;

          if (anchorLine >= top && anchorLine <= bottom) {
            nextActiveStage = stageId;
          }

          const distance = Math.abs(top - anchorLine);
          if (distance < smallestDistance) {
            smallestDistance = distance;
            fallbackStage = stageId;
          }
        });

        const resolvedStage = nextActiveStage ?? fallbackStage ?? visibleTemplateStages[0] ?? null;

        setActiveTemplateStage((prev) => (prev === resolvedStage ? prev : resolvedStage));
        setTemplateStageVisibility((prev) => {
          const keys = Array.from(new Set([...Object.keys(prev), ...Object.keys(nextVisibility)])) as TemplateStageId[];
          const changed = keys.some((key) => Math.abs((prev[key] ?? 0) - (nextVisibility[key] ?? 0)) > 0.01);
          return changed ? nextVisibility : prev;
        });
      });
    };

    measureVisibleStages();
    scrollElement.addEventListener('scroll', measureVisibleStages, { passive: true });
    window.addEventListener('resize', measureVisibleStages);

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {
        measureVisibleStages();
      });
      observer.observe(scrollElement);
      visibleTemplateStages.forEach((stageId) => {
        const node = templateStageSectionRefs.current[stageId];
        if (node) observer.observe(node);
      });
      observers.push(observer);
    }

    return () => {
      scrollElement.removeEventListener('scroll', measureVisibleStages);
      window.removeEventListener('resize', measureVisibleStages);
      observers.forEach((observer) => observer.disconnect());
      if (frame) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, [displayTemplatePanel, visibleTemplateEntries.length, visibleTemplateStages]);

  useEffect(() => {
    if (!hoveredStageGuideStep) return undefined;

    const handlePointerDown = () => {
      setHoveredStageGuideStep(null);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [hoveredStageGuideStep]);

  const getTemplateEntryValueByKey = useCallback((key: string) => {
    const match = templateDraftEntries.find((entry) => entry.key === key);
    if (!match) return '';
    return stripYamlQuotes(templateValueDrafts[match.id] ?? match.value);
  }, [templateDraftEntries, templateValueDrafts]);

  const normalizeTemplateDisplayPath = useCallback(
    (path: string) => path.replace(/\[\d+\](?=\.|$)/g, ''),
    [],
  );

  const getTemplateListItemTitle = useCallback((rootKey: string, itemKey: string, itemIndex: number, fallbackValue = '') => {
    if (rootKey === 'params') {
      return getTemplateEntryValueByKey(`${itemKey}.name`) || `param ${itemIndex + 1}`;
    }
    if (rootKey === 'list_fields') {
      return getTemplateEntryValueByKey(`${itemKey}.name`) || `field ${itemIndex + 1}`;
    }
    if (rootKey === 'download') {
      return (
        getTemplateEntryValueByKey(`${itemKey}.asset_type`)
        || getTemplateEntryValueByKey(`${itemKey}.selector`)
        || `asset ${itemIndex + 1}`
      );
    }
    if (rootKey === 'dedup_fields') {
      return fallbackValue || `field ${itemIndex + 1}`;
    }
    return `item ${itemIndex + 1}`;
  }, [getTemplateEntryValueByKey]);

  const getTemplateEntryDisplayMeta = useCallback((entry: TemplateEntry, value: string) => {
    const normalizedValue = stripYamlQuotes(value);
    const arrayGroupMatch = entry.nodeType === 'group'
      ? entry.key.match(/^([a-z_]+)\[(\d+)\]$/)
      : null;

    if (arrayGroupMatch) {
      const [, rootKey, rawIndex] = arrayGroupMatch;
      const itemIndex = Number(rawIndex);
      return {
        label: getTemplateListItemTitle(rootKey, entry.key, itemIndex),
        pathHint: rootKey,
      };
    }

    const arrayValueMatch = entry.nodeType === 'value'
      ? entry.key.match(/^([a-z_]+)\[(\d+)\]$/)
      : null;

    if (arrayValueMatch) {
      const [, rootKey, rawIndex] = arrayValueMatch;
      const itemIndex = Number(rawIndex);
      return {
        label: rootKey === 'dedup_fields' ? 'field' : getTemplateListItemTitle(rootKey, entry.key, itemIndex, normalizedValue),
        pathHint: rootKey,
      };
    }

    const arrayChildMatch = entry.key.match(/^([a-z_]+)\[(\d+)\]\.(.+)$/);
    if (arrayChildMatch) {
      const [, rootKey, rawIndex, childPath] = arrayChildMatch;
      const itemIndex = Number(rawIndex);
      const label = childPath.split('.').pop() ?? childPath;
      if (rootKey === 'params') {
        return {
          label: label,
          pathHint: 'params',
        };
      }
      const itemTitle = getTemplateListItemTitle(rootKey, `${rootKey}[${itemIndex}]`, itemIndex);
      return {
        label,
        pathHint: itemTitle ? `${rootKey} / ${itemTitle}` : rootKey,
      };
    }

    const label = entry.key.split('.').pop() ?? entry.key;
    return {
      label,
      pathHint: label === entry.key ? '' : normalizeTemplateDisplayPath(entry.key.slice(0, -(label.length + 1))),
    };
  }, [getTemplateListItemTitle, normalizeTemplateDisplayPath]);

  const renderTemplateStageSection = useCallback((stageId: TemplateStageId) => {
    const stageEntries = visibleTemplateEntries.filter((entry) => {
      if (entry.stageId !== stageId) return false;
      if (
        entry.nodeType === 'group'
        && (
          /^(params|dedup_fields|list_fields|download)\[\d+\]$/.test(entry.key)
          || flattenedTemplateRootKeys.has(entry.key)
        )
      ) {
        return false;
      }
      return true;
    });
    if (!stageEntries.length) return null;
    const stageValueCount = stageEntries.filter((entry) => entry.nodeType === 'value').length;
    const getTemplateEntryGroupKey = (entry: TemplateEntry, nextEntry?: TemplateEntry) => {
      const rootGroupMatch = entry.key.match(/^([A-Za-z_][\w-]*)$/);
      if (entry.nodeType === 'group' && rootGroupMatch) {
        const rootKey = rootGroupMatch[1];
        const nextItemMatch = nextEntry?.key.match(new RegExp(`^(${rootKey}\\[\\d+\\])(?:\\.|$)`));
        return nextItemMatch?.[1] ?? rootKey;
      }

      const itemMatch = entry.key.match(/^([A-Za-z_][\w-]*\[\d+\])(?:\.|$)/);
      if (itemMatch) {
        return itemMatch[1];
      }

      const rootMatch = entry.key.match(/^([A-Za-z_][\w-]*)(?:\.|$)/);
      return rootMatch?.[1] ?? entry.key;
    };

    return (
      <section
        className="ai-template-stage-section"
        key={stageId}
        data-stage-id={stageId}
        ref={(node) => {
          templateStageSectionRefs.current[stageId] = node;
        }}
      >
        <div className="ai-template-stage-head">
          <div className="ai-template-stage-copy">
            <span className="ai-template-stage-title">{templateStageMeta[stageId].title}</span>
            <small>{templateStageMeta[stageId].desc}</small>
          </div>
          <div className="ai-template-stage-actions">
            <span>{stageValueCount}</span>
          </div>
        </div>
        <div className="ai-template-stage-body">
          {stageEntries.map((entry, index) => {
            const previousEntry = stageEntries[index - 1];
            const nextEntry = stageEntries[index + 1];
            const nextNextEntry = stageEntries[index + 2];
            const currentGroupKey = getTemplateEntryGroupKey(entry, nextEntry);
            const nextGroupKey = nextEntry ? getTemplateEntryGroupKey(nextEntry, nextNextEntry) : null;
            const extraClass = currentGroupKey !== nextGroupKey ? 'is-group-end' : '';
            const yamlListItemKey = entry.key.match(/^(params|dedup_fields|list_fields|download)\[\d+\](?=\.|$)/)?.[0] ?? null;
            const previousYamlListItemKey = previousEntry?.key.match(/^(params|dedup_fields|list_fields|download)\[\d+\](?=\.|$)/)?.[0] ?? null;
            const yamlListRoot = yamlListItemKey?.split('[')[0] ?? null;
            const rootKey = entry.key.match(/^([A-Za-z_][\w-]*)(?:\.|\[|$)/)?.[1] ?? entry.key;
            const isYamlListItem = Boolean(
              yamlListItemKey
              && yamlListRoot
              && yamlListPreviewRoots.has(yamlListRoot)
            );
            const isYamlListAnchor = Boolean(
              isYamlListItem
              && yamlListItemKey !== previousYamlListItemKey
            );
            const flattenRootIndent = flattenedTemplateRootKeys.has(rootKey) && entry.key !== rootKey;
            const displayDepth = Math.max(
              0,
              entry.depth - (isYamlListItem || flattenRootIndent ? 1 : 0),
            );
            const value = templateValueDrafts[entry.id] ?? entry.value;
            const isGroup = entry.nodeType === 'group';
            const isItemGroup = isGroup && /\[\d+\]$/.test(entry.key);
            const isRootGroup = isGroup && !entry.key.includes('.') && !/\[\d+\]$/.test(entry.key);
            const displayValue = entry.multiline ? value : stripYamlQuotes(value);
            const { label } = getTemplateEntryDisplayMeta(entry, value);
            const displayLabel = label;

            return (
              <div
                className={`ai-template-field ${entry.multiline ? 'is-multiline' : ''} ${isGroup ? 'is-group' : ''} ${isItemGroup ? 'is-item-group' : ''} ${isRootGroup ? 'is-root-group' : ''} ${isYamlListItem ? 'is-yaml-list-item' : ''} ${isYamlListAnchor ? 'has-yaml-dash' : ''} ${extraClass}`}
                key={entry.id}
                style={{ ['--ai-template-depth' as string]: String(displayDepth) }}
              >
                <div className="ai-template-field-key">
                  {isYamlListItem ? <i className="ai-template-field-dash" aria-hidden="true">-</i> : null}
                  <span>{displayLabel}</span>
                </div>
                {isGroup ? null : (
                  <div className={`ai-template-field-value ${entry.multiline || label === 'description' ? 'is-rich' : ''}`}>
                    <pre>{displayValue || 'null'}</pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    );
  }, [getTemplateEntryDisplayMeta, templateValueDrafts, visibleTemplateEntries]);

  const renderSessionTemplateSheet = () => (
    <section className="ai-session-template-shell">
      <header className="ai-session-fixed-meta">
        <div className="ai-session-fixed-copy">
          <Text className="ai-session-fixed-eyebrow">Template Contract</Text>
          <div className="ai-session-fixed-title-row">
            <h2>{activeTemplate.displayName}</h2>
            <Text className="ai-session-fixed-stat">{visibleTemplateValueCount} keys</Text>
          </div>
          <Text className="ai-session-fixed-subtitle">{activeTemplate.fileName}</Text>
        </div>
        {renderSessionBrowserPreview()}
      </header>

      <div className="ai-session-template-scroll" ref={templateScrollRef}>
        <article className="ai-template-sheet">
          <div className="ai-template-sheet-body">
            {visibleTemplateStages.map(renderTemplateStageSection)}
          </div>
          {templateReadyForConfirm ? (
            <div className="ai-template-confirm-bar">
              <Button
                type="primary"
                className="ai-template-confirm-btn"
                onClick={() => handleConfirmProcessStep('contract')}
              >
                Confirm Template
              </Button>
            </div>
          ) : null}
        </article>
        <div className="ai-session-template-tail" aria-hidden="true">
          <div className="ai-session-template-divider" />
        </div>
      </div>
    </section>
  );

  const renderSessionStageRail = () => {
    const guideStep = hoveredStageGuideStep;
    const guideStageIndex = guideStep && isTemplateStageId(guideStep)
      ? Math.max(0, visibleTemplateStages.indexOf(guideStep))
      : 0;
    const hoveredStageIndex = hoveredStageGuideStep && isTemplateStageId(hoveredStageGuideStep)
      ? visibleTemplateStages.indexOf(hoveredStageGuideStep)
      : -1;
    const popoverOffset = -4 + guideStageIndex * 9;
    const handleStageBarClick = (stageId: TemplateStageId) => {
      const scrollElement = templateScrollRef.current;
      const node = templateStageSectionRefs.current[stageId];
      if (!scrollElement || !node) return;

      const nextScrollTop = Math.max(0, node.offsetTop - 12);
      scrollElement.scrollTo({
        top: nextScrollTop,
        behavior: 'smooth',
      });
    };

    return (
      <aside
        className="ai-session-stage-float"
        aria-label="分析阶段提示"
        onMouseLeave={() => setHoveredStageGuideStep(null)}
      >
        <div className="ai-session-stage-bars">
          {visibleTemplateStages.map((step, index) => {
            const visibility = templateStageVisibility[step] ?? 0;
            const isHovered = hoveredStageGuideStep === step;
            const isActiveAnchor = activeTemplateStage === step;
            const hoverDistance = hoveredStageIndex >= 0 ? Math.abs(index - hoveredStageIndex) : null;
            const hoverWidth = hoverDistance === null
              ? 0
              : hoverDistance === 0
                ? 30
                : hoverDistance === 1
                  ? 15
                  : 0;
            const barWidth = Math.max(6, hoverWidth);
            const barColor = isHovered
              ? '#FFFFFF'
              : isActiveAnchor
                ? `rgba(255, 255, 255, ${Math.min(0.68, 0.42 + visibility * 0.18)})`
                : hoverDistance === 1
                  ? `rgba(255, 255, 255, ${Math.min(0.86, 0.42 + visibility * 0.26)})`
                  : hoverDistance === 2
                    ? `rgba(255, 255, 255, ${Math.min(0.7, 0.32 + visibility * 0.24)})`
                    : `rgba(255, 255, 255, ${Math.min(0.54, 0.14 + visibility * 0.52)})`;
            return (
              <button
                type="button"
                key={step}
                className={`ai-session-stage-bar ${isActiveAnchor ? 'is-active' : ''} ${visibility > 0.08 ? 'is-visible' : ''}`}
                style={{
                  ['--ai-stage-bar-width' as string]: `${barWidth}px`,
                  ['--ai-stage-bar-color' as string]: barColor,
                }}
                onMouseEnter={() => {
                  setHoveredStageGuideStep(step);
                }}
                onClick={() => handleStageBarClick(step)}
                aria-label={templateStageMeta[step].title}
                aria-current={isActiveAnchor ? 'true' : undefined}
              >
                <span />
              </button>
            );
          })}
        </div>
        {guideStep && isTemplateStageId(guideStep) ? (
          <div className="ai-session-stage-card" style={{ top: `${popoverOffset}px` }}>
            <strong>{templateStageMeta[guideStep].title}</strong>
            <p>{templateStageMeta[guideStep].desc}</p>
            <div className="ai-session-stage-card-foot">
              <span className="ai-session-stage-file">{activeTemplate.fileName}</span>
              <em>{guideStageIndex + 1}/{visibleTemplateStages.length}</em>
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
        className={`ai-session-status-line ${sideInspectorOpen ? 'is-open' : ''}`}
        aria-label={sessionSideMode === 'browser' ? '展开浏览器状态面板' : '展开编码状态面板'}
        onClick={() => openSessionInspector()}
      >
        {sessionSideMode === 'browser' ? (
          <span className="ai-session-status-favicon" aria-hidden="true">
            {browserPreviewFavicon ? (
              <img src={browserPreviewFavicon} alt="" />
            ) : (
              <GlobalOutlined />
            )}
          </span>
        ) : (
          <span className="ai-session-status-icon" aria-hidden="true">
            <AdapterEditorIcon className="ai-session-status-icon-svg" />
          </span>
        )}
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
        <Button
          type="text"
          className="ai-session-side-close"
          icon={<CloseOutlined />}
          aria-label="关闭右侧状态面板"
          onClick={() => {
            setSessionInspectorTabs([]);
            setActiveInspectorTabId(null);
          }}
        />
      </div>

      {sessionSideMode === 'browser' ? (
        <div className="ai-side-browser-shell">
          <div className="ai-side-browser-bar">
            <span className="ai-side-browser-favicon" aria-hidden="true">
              {browserPreviewFavicon ? (
                <img src={browserPreviewFavicon} alt="" />
              ) : (
                <GlobalOutlined />
              )}
            </span>
            <strong>{browserPreviewHost || 'source.local'}</strong>
          </div>
        <div className="ai-side-browser-viewport">
          {urlPreflight?.previewHtml ? (
            <iframe
              className="ai-side-browser-frame"
              title={`${browserPreviewTitle} 页面预览`}
              sandbox=""
              referrerPolicy="no-referrer"
              srcDoc={urlPreflight.previewHtml}
            />
          ) : (
            <div className="ai-side-browser-empty">暂无经过服务端验证的页面快照</div>
          )}
          </div>
        </div>
      ) : (
        <div className="ai-side-code-shell">
          <div className="ai-side-code-card">
            <div className="ai-side-code-card-top">
              <span className="ai-side-code-card-icon" aria-hidden="true">
                <AdapterEditorIcon className="ai-side-code-card-icon-svg" />
              </span>
              <strong>{adapterFileLabel}</strong>
            </div>
            <span className="ai-side-code-path">{adapterFileDirectory}</span>
            <div className="ai-side-code-diff">
              <b className="is-added">+{adapterDiffStats.added}</b>
              <b className="is-removed">-{adapterDiffStats.removed}</b>
            </div>
          </div>
          <div className="ai-side-code-list">
            {(workflowPhase === 'release-template'
              ? [
                  `template: ${releaseActionMeta[selectedReleaseAction].title}`,
                  `task: ${taskPublishMeta[selectedTaskPublishMode].title}`,
                  'release gate ready',
                ]
              : adapterBuildPlan.map((step, index) => (
                  `${index < adapterBuildIndex ? 'done' : index === adapterBuildIndex ? 'active' : 'pending'} · ${step.title}`
                ))).map((label) => (
              <div className="ai-side-code-row" key={label}>
                <span />
                <strong>{label}</strong>
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
        className={`ai-session-side-trigger ${sideInspectorOpen ? 'is-open' : ''}`}
        aria-label={sessionSideMode === 'browser' ? '点击查看浏览器状态' : '点击查看适配器编写状态'}
        onClick={() => openSessionInspector()}
      >
        {sessionSideMode === 'browser' ? (
          <SessionStatusIcon className="ai-session-side-trigger-icon" />
        ) : (
          <AdapterEditorIcon className="ai-session-side-trigger-icon" />
        )}
      </button>
      {sideInspectorOpen ? renderSessionInspectorPanel() : null}
    </div>
  );

  const renderSessionInspectorPanel = () => {
    if (!activeInspectorTab) return null;

    return (
      <aside className={`ai-session-inspector-shell ${sideInspectorOpen ? 'is-expanded' : ''}`}>
        <div className="ai-session-inspector-tabs">
          {sessionInspectorTabs.map((tab) => (
            <div
              className={`ai-session-inspector-tab ${activeInspectorTab.id === tab.id ? 'is-active' : ''}`}
              key={tab.id}
            >
              <button
                type="button"
                className="ai-session-inspector-tab-main"
                onClick={() => setActiveInspectorTabId(tab.id)}
              >
                {tab.kind === 'browser' ? (
                  <span className="ai-session-inspector-tab-icon is-browser" aria-hidden="true">
                    {browserPreviewFavicon ? <img src={browserPreviewFavicon} alt="" /> : <GlobalOutlined />}
                  </span>
                ) : (
                  <span className="ai-session-inspector-tab-icon" aria-hidden="true">
                    <AdapterEditorIcon className="ai-session-status-icon-svg" />
                  </span>
                )}
                <span className="ai-session-inspector-tab-label">{tab.title}</span>
              </button>
              <button
                type="button"
                className="ai-session-inspector-tab-close"
                aria-label={`Close ${tab.title}`}
                onClick={() => closeSessionInspectorTab(tab.id)}
              >
                <CloseOutlined />
              </button>
            </div>
          ))}
        </div>

        <div className="ai-session-inspector-body">
          {activeInspectorTab.kind === 'browser' ? (
            <div className="ai-session-inspector-browser">
              <div className="ai-session-inspector-browser-frame">
                {urlPreflight?.previewHtml ? (
                  <iframe
                    sandbox=""
                    referrerPolicy="no-referrer"
                    srcDoc={urlPreflight.previewHtml}
                    title={activeInspectorTab.title}
                  />
                ) : (
                  <div className="ai-session-inspector-empty">
                    <strong>No verified browser snapshot</strong>
                    <span>{activeInspectorTab.subtitle}</span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="ai-session-inspector-editor">
              <div className="ai-session-inspector-editor-body">
                {adapterPreviewLines.map((line) => (
                  <div
                    className={`ai-session-inspector-editor-line ${line.added ? 'is-added' : ''}`}
                    key={line.key}
                  >
                    <span className="ai-session-inspector-editor-no">{line.lineNumber}</span>
                    <span className="ai-session-inspector-editor-prefix">{line.prefix}</span>
                    <code>{renderPythonPreviewContent(line.content || ' ', line.key)}</code>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>
    );
  };

  const renderWorkflowGuideRail = () => {
    const guideStep = hoveredStageGuideStep;
    const guideStepIndex = guideStep ? Math.max(0, sessionGuideSteps.indexOf(guideStep)) : 0;
    const hoveredGuideIndex = hoveredStageGuideStep ? sessionGuideSteps.indexOf(hoveredStageGuideStep) : -1;
    const popoverOffset = 6 + guideStepIndex * 13;
    const getGuideMeta = (step: SessionGuideStepId) => (
      isTemplateStageId(step) ? templateStageMeta[step] : sessionGuideMeta[step]
    );
    const handleGuideStepClick = (step: SessionGuideStepId) => {
      setHoveredStageGuideStep(null);

      if (isTemplateStageId(step) || step === 'confirm-template') {
        const nextPreviewPhase = workflowPhase === 'analyzing-template' ? null : 'confirm-template';
        setGuidePreviewPhase(nextPreviewPhase);
        scrollTemplateToGuideStep(step, !displayTemplatePanel);
        return;
      }

      if (step === 'generate-adapter') {
        setGuidePreviewPhase(workflowPhase === 'generating-adapter' ? null : 'generating-adapter');
        return;
      }

      setGuidePreviewPhase(workflowPhase === 'release-template' ? null : 'release-template');
    };

    return (
      <aside
        className="ai-session-stage-float"
        aria-label="session workflow guide"
        onMouseLeave={() => setHoveredStageGuideStep(null)}
      >
        <div className="ai-session-stage-bars">
          {sessionGuideSteps.map((step, index) => {
            const visibility = isTemplateStageId(step)
              ? (templateStageVisibility[step] ?? 0)
              : index < activeGuideIndex
                ? 1
                : index === activeGuideIndex
                  ? 0.92
                  : 0.24;
            const isHovered = hoveredStageGuideStep === step;
            const isVisibleTemplateStage = isTemplateStageId(step)
              && displayTemplatePanel
              && visibility > 0.01;
            const isCurrentView = isVisibleTemplateStage
              || (!isTemplateStageId(step) && displayGuideStep === step);
            const isPrimaryView = isTemplateStageId(step)
              ? activeTemplateStage === step
              : displayGuideStep === step;
            const isDone = activeGuideIndex > -1 && index < activeGuideIndex;
            const hoverDistance = hoveredGuideIndex >= 0 ? Math.abs(index - hoveredGuideIndex) : null;
            const hoverWidth = hoverDistance === null
              ? 6
              : hoverDistance === 0
                ? 30
                : hoverDistance === 1
                  ? 18
                  : hoverDistance === 2
                    ? 11
                    : 6;
            const needsAttention = step === 'confirm-template' && templateReadyForConfirm;
            const barHeight = isHovered ? 1.8 : 1.4;
            const barColor = isHovered
              ? '#FFFFFF'
              : isCurrentView
                ? '#D9E0EA'
                : isDone
                  ? 'rgba(208, 214, 224, 0.68)'
                  : `rgba(204, 211, 221, ${Math.min(0.52, 0.26 + visibility * 0.26)})`;

            return (
              <button
                type="button"
                key={step}
                className={`ai-session-stage-bar ${isCurrentView ? 'is-active' : ''} ${isDone ? 'is-done' : ''} ${needsAttention ? 'is-attention' : ''}`}
                style={{
                  ['--ai-stage-bar-width' as string]: `${Math.max(6, hoverWidth)}px`,
                  ['--ai-stage-bar-height' as string]: `${barHeight}px`,
                  ['--ai-stage-bar-color' as string]: barColor,
                }}
                onMouseEnter={() => setHoveredStageGuideStep(step)}
                onMouseDown={() => setHoveredStageGuideStep(step)}
                onFocus={() => setHoveredStageGuideStep(step)}
                onClick={() => handleGuideStepClick(step)}
                aria-label={getGuideMeta(step).title}
                aria-current={isPrimaryView ? 'true' : undefined}
              >
                <span />
              </button>
            );
          })}
        </div>
        {guideStep ? (
          <div className="ai-session-stage-card" style={{ top: `${popoverOffset}px` }}>
            <strong>{getGuideMeta(guideStep).title}</strong>
            <p>{getGuideMeta(guideStep).desc}</p>
            <div className="ai-session-stage-card-foot">
              <span className="ai-session-stage-file">
                {isTemplateStageId(guideStep) || guideStep === 'confirm-template' ? activeTemplate.fileName : adapterFileName}
              </span>
              <em>{guideStepIndex + 1}/{sessionGuideSteps.length}</em>
            </div>
          </div>
        ) : null}
      </aside>
    );
  };

  const renderWorkflowHeader = () => (
    <header className="ai-session-fixed-meta">
      <div className="ai-session-fixed-copy">
        <Text className="ai-session-fixed-eyebrow">{sessionHeaderMeta.eyebrow}</Text>
        <div className="ai-session-fixed-title-row">
          <h2>{sessionHeaderMeta.title}</h2>
          <Text className="ai-session-fixed-stat">{sessionHeaderMeta.stat}</Text>
        </div>
        <Text className="ai-session-fixed-subtitle">{sessionHeaderMeta.subtitle}</Text>
      </div>
      {renderSessionBrowserPreview()}
    </header>
  );

  const renderPinnedTabs = () => {
    if (!showPinnedTemplateTab && !showPinnedAdapterTab) return null;

    return (
      <div className="ai-session-pinned-tab-stack">
        {showPinnedTemplateTab ? (
          <button
            type="button"
            className={`ai-session-pinned-tab is-template ${templateTabAnimating ? 'is-entering' : ''}`}
            aria-label={activeTemplate.fileName}
            title={activeTemplate.fileName}
            onClick={() => {
              setExpandingPinnedPanel('template');
              setGuidePreviewPhase('confirm-template');
              window.requestAnimationFrame(() => {
                window.requestAnimationFrame(() => {
                  templateScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
                });
              });
            }}
          >
            <YamlFileIcon className="ai-session-pinned-tab-icon is-template" />
          </button>
        ) : null}
        {showPinnedAdapterTab ? (
          <button
            type="button"
            className={`ai-session-pinned-tab is-adapter ${adapterTabAnimating ? 'is-entering' : ''}`}
            aria-label={adapterFileName}
            title={adapterFileName}
            onClick={() => {
              setExpandingPinnedPanel('adapter');
              setGuidePreviewPhase('generating-adapter');
            }}
          >
            <AdapterPinnedIcon className="ai-session-pinned-tab-icon is-adapter" />
          </button>
        ) : null}
      </div>
    );
  };

  const renderWorkflowTemplatePanel = () => (
    <section className={`ai-session-main-shell is-template ${expandingPinnedPanel === 'template' ? 'is-restoring-from-tab' : ''}`}>
      {renderWorkflowHeader()}
      <div className="ai-session-template-scroll" ref={templateScrollRef}>
        {streamError ? (
          <Alert className="ai-session-inline-alert" type="warning" showIcon message={streamError} />
        ) : null}
        <article className="ai-template-sheet">
          <div className="ai-template-sheet-body">
            {visibleTemplateStages.map(renderTemplateStageSection)}
          </div>
          {templateReadyForConfirm ? (
            <div className="ai-template-confirm-bar">
              <div className="ai-template-confirm-copy">
                <strong>Confirm template</strong>
                <span>Lock the YAML contract before adapter generation.</span>
              </div>
              <Button
                type="primary"
                className="ai-template-confirm-btn"
                onClick={handleConfirmTemplate}
              >
                Confirm Template
              </Button>
            </div>
          ) : null}
        </article>
        <div className="ai-session-template-tail" aria-hidden="true">
          <div className="ai-session-template-divider" />
        </div>
      </div>
    </section>
  );

  const renderWorkflowAdapterPanel = () => {
    const currentStepIndex = Math.min(adapterBuildIndex, adapterBuildPlan.length - 1);
    const currentStep = adapterBuildPlan[currentStepIndex];

    return (
      <section className={`ai-session-main-shell is-adapter ${expandingPinnedPanel === 'adapter' ? 'is-restoring-from-tab' : ''}`}>
        {renderWorkflowHeader()}
        <div className="ai-session-scroll-frame">
          {renderPinnedTabs()}
          <div className="ai-session-adapter-scroll">
            <div className="ai-session-adapter-shell">
            <div className="ai-session-adapter-overview">
              <div className="ai-session-adapter-copy">
                <Text className="ai-session-fixed-eyebrow">Adapter Build</Text>
                <h3>{currentStep?.title ?? 'Adapter Build'}</h3>
                <p>AI is implementing the adapter in serial steps from the confirmed template contract.</p>
              </div>
              <div className="ai-session-adapter-progress">
                <Progress percent={adapterProgressPercent} showInfo={false} strokeColor="#ffffff" trailColor="rgba(255,255,255,0.1)" />
                <span>{Math.min(adapterBuildIndex + 1, adapterBuildPlan.length)}/{adapterBuildPlan.length}</span>
              </div>
            </div>
            <div className="ai-session-adapter-steps is-flow">
              {adapterBuildPlan.map((step, index) => {
                const status = index < adapterBuildIndex ? 'done' : index === currentStepIndex ? 'active' : 'pending';
                const expanded = expandedAdapterStep === index;
                const statusLabel = status === 'done'
                  ? `已处理 ${step.elapsed}`
                  : status === 'active'
                    ? `处理中 ${step.elapsed}`
                    : '待处理';

                return (
                  <div className={`ai-session-adapter-task is-${status} ${expanded ? 'is-expanded' : ''}`} key={step.title}>
                    <div className="ai-session-adapter-task-head">
                      <div className="ai-session-adapter-task-copy">
                        <strong>{step.title}</strong>
                      </div>
                      <button
                        type="button"
                        className="ai-session-adapter-task-status"
                        aria-expanded={expanded}
                        onClick={() => {
                          setExpandedAdapterStep((prev) => (prev === index ? null : index));
                        }}
                      >
                        <span className="ai-session-adapter-task-status-label">{statusLabel}</span>
                        <span
                          className={`ai-session-adapter-task-status-caret ${expanded ? 'is-expanded' : ''}`}
                          aria-hidden="true"
                        >
                          <ChevronRightIcon />
                        </span>
                      </button>
                    </div>
                    {expanded ? (
                      <div className="ai-session-adapter-task-body">
                        <p>{step.desc}</p>
                        <div className="ai-session-adapter-task-list">
                          {step.details.map((detail) => (
                            <div className="ai-session-adapter-task-item" key={detail}>
                              <i aria-hidden="true" />
                              <span>{detail}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            </div>
            <div className="ai-session-template-tail" aria-hidden="true">
              <div className="ai-session-template-divider" />
            </div>
          </div>
        </div>
      </section>
    );
  };

  const renderWorkflowReleasePanel = () => {
    const releaseActionIcons: Record<ReleaseAction, React.ReactNode> = {
      draft: <ReleaseDraftIcon />,
      archive: <ReleaseArchiveIcon />,
      publish: <CloudUploadOutlined />,
    };
    const isPublishing = selectedReleaseAction === 'publish';
    const createsTask = isPublishing && selectedTaskPublishMode === 'launch';
    const hasTaskComposer = isPublishing && createsTask;
    const batchParamName = releaseTaskParamValues.batch_param_name ?? releaseBatchConfig?.paramName ?? '';
    const selectedBatchFile = releaseTaskParamValues.list_file ?? '';
    const hasSelectedBatchFile = Boolean(selectedBatchFile);
    const visibleTaskParams = releaseTemplateParams.filter(
      (param) => !(releaseBatchInput && batchParamName === param.name),
    );
    const releaseCta = hasTaskComposer ? 'Publish & Create Task' : releaseActionMeta[selectedReleaseAction].cta;

    return (
      <section className="ai-session-main-shell is-release">
        {renderWorkflowHeader()}
        <div className="ai-session-scroll-frame">
          {renderPinnedTabs()}
          <div className="ai-session-release-scroll">
            <div className="ai-session-release-shell">
            <div className="ai-session-release-head">
              <div className="ai-session-release-heading">
                <div className="ai-session-task-create-title">
                  <DeploymentUnitOutlined aria-hidden="true" />
                  <div>
                    <strong>{hasTaskComposer ? 'Create Crawl Task' : releaseActionMeta[selectedReleaseAction].title}</strong>
                    <span>{hasTaskComposer ? 'Set the schedule and crawl boundary before publish.' : releaseActionMeta[selectedReleaseAction].desc}</span>
                  </div>
                </div>
                <div className="ai-session-release-meta">
                  <span>{activeTemplate.fileName}</span>
                  <span>{adapterFileLabel}</span>
                  <span>{selectedCount} fields</span>
                </div>
              </div>
              <div className="ai-session-release-head-controls">
                {hasTaskComposer ? (
                  <button
                    type="button"
                    className="ai-session-task-create-skip"
                    onClick={() => setSelectedTaskPublishMode(createsTask ? 'skip' : 'launch')}
                  >
                    {createsTask ? 'Skip' : 'Create task'}
                  </button>
                ) : null}
                <div className="ai-session-release-actions" aria-label="Template release action">
                  {(['draft', 'archive', 'publish'] as ReleaseAction[]).map((action) => (
                    <Tooltip
                      key={action}
                      placement="top"
                      title={(
                        <div className="ai-session-release-tooltip">
                          <strong>{releaseActionMeta[action].title}</strong>
                          <span>{releaseActionMeta[action].desc}</span>
                        </div>
                      )}
                    >
                      <button
                        type="button"
                        className={`ai-session-release-action ${selectedReleaseAction === action ? 'is-selected' : ''}`}
                        aria-label={releaseActionMeta[action].title}
                        aria-pressed={selectedReleaseAction === action}
                        onClick={() => handleReleaseActionSelect(action)}
                      >
                        {releaseActionIcons[action]}
                      </button>
                    </Tooltip>
                  ))}
                </div>
              </div>
            </div>

            {hasTaskComposer ? (
              <section className="ai-session-task-create">
                <div className="ai-session-task-scheduler">
                    <div className="ai-session-task-scheduler-row">
                      <label className="ai-session-task-control ai-session-task-schedule-control">
                        <span>Run schedule</span>
                        <Segmented
                          className="ai-session-task-run-schedule"
                          size="small"
                          value={releaseScheduleKind}
                          onChange={(value) => setReleaseScheduleKind(value as ReleaseScheduleKind)}
                          options={[
                            { label: 'Once', value: 'once' },
                            { label: 'Daily', value: 'daily' },
                            { label: 'Interval', value: 'interval' },
                          ]}
                        />
                      </label>
                      <label className="ai-session-task-control ai-session-task-concurrency-control">
                        <span>Concurrency</span>
                        <InputNumber min={1} max={50} value={concurrency} onChange={(value) => setConcurrency(value ?? 4)} />
                      </label>
                    </div>
                    <div className="ai-session-task-schedule-options">
                      {releaseScheduleKind === 'daily' ? (
                        <>
                          <label className="ai-session-task-control">
                            <span>Run at</span>
                            <TimePicker
                              allowClear={false}
                              value={dayjs(releaseDailyTime, 'HH:mm')}
                              format="HH:mm"
                              minuteStep={5}
                              needConfirm
                              popupClassName="ai-session-time-picker-dropdown"
                              onChange={(_, value) => setReleaseDailyTime((value as string) || '09:00')}
                            />
                          </label>
                          <label className="ai-session-task-control">
                            <span>Empty page limit</span>
                            <InputNumber min={1} max={20} value={releaseEmptyPageLimit} onChange={(value) => setReleaseEmptyPageLimit(value ?? 2)} />
                          </label>
                        </>
                      ) : releaseScheduleKind === 'interval' ? (
                        <>
                          <label className="ai-session-task-control">
                            <span>Interval</span>
                            <InputNumber min={5} max={720} value={releaseIntervalMinutes} onChange={(value) => setReleaseIntervalMinutes(value ?? 60)} />
                          </label>
                          <label className="ai-session-task-control">
                            <span>Unit</span>
                            <Select
                              value={releaseIntervalUnit}
                              options={[
                                { value: 'minute', label: 'Minute' },
                                { value: 'hour', label: 'Hour' },
                              ]}
                              onChange={(value) => setReleaseIntervalUnit(value as 'minute' | 'hour')}
                            />
                          </label>
                        </>
                      ) : (
                        <label className="ai-session-task-control">
                          <span>First run</span>
                          <Input value="After publish" disabled />
                        </label>
                      )}
                    </div>
                    {(visibleTaskParams.length || releaseBatchConfig) ? (
                      <section className="ai-session-task-template-params">
                        <div className="ai-session-task-template-params-head">
                          <span>Template inputs</span>
                          {releaseBatchConfig ? (
                            <label>
                              <span>Batch input</span>
                              <Checkbox
                                className="ai-session-task-checkbox"
                                checked={releaseBatchInput}
                                onChange={(event) => setReleaseBatchInput(event.target.checked)}
                              />
                            </label>
                          ) : null}
                        </div>
                        {visibleTaskParams.length ? (
                          <div className="ai-session-task-param-grid">
                            {visibleTaskParams.map((param) => {
                              const sizeClass = param.name === 'query'
                                ? 'is-long'
                                : ['domain', 'sort', 'order'].includes(param.name)
                                  ? 'is-compact'
                                  : '';
                              return (
                            <label className={`ai-session-task-control ${sizeClass}`} key={param.name}>
                              <span>{param.name}{param.required ? ' *' : ''}</span>
                              <Input
                                value={releaseTaskParamValues[param.name] ?? param.defaultValue}
                                placeholder={param.description || param.name}
                                onChange={(event) => setReleaseTaskParamValues((prev) => ({ ...prev, [param.name]: event.target.value }))}
                              />
                            </label>
                              );
                            })}
                          </div>
                        ) : null}
                        {releaseBatchInput && releaseBatchConfig ? (
                          <div className="ai-session-task-batch-params">
                            <div className="ai-session-task-batch-params-body">
                              <label className="ai-session-task-control is-file">
                                <span>List file *</span>
                                <div className="ai-session-task-file-picker">
                                  <Upload
                                    accept=".txt,.csv"
                                    showUploadList={false}
                                    beforeUpload={() => false}
                                    onChange={({ file }) => {
                                      if (file.name) {
                                        setReleaseTaskParamValues((prev) => ({ ...prev, list_file: file.name }));
                                      }
                                    }}
                                  >
                                    <Button size="small" icon={<UploadOutlined />}>Choose</Button>
                                  </Upload>
                                  {hasSelectedBatchFile ? (
                                    <span className="ai-session-task-file-reference" title={selectedBatchFile}>
                                      <span>{selectedBatchFile}</span>
                                      <button
                                        type="button"
                                        className="ai-session-task-file-remove"
                                        aria-label="Remove selected file"
                                        onClick={() => setReleaseTaskParamValues(({ list_file, ...rest }) => rest)}
                                      >
                                        <CloseOutlined />
                                      </button>
                                    </span>
                                  ) : null}
                                </div>
                              </label>
                              {hasSelectedBatchFile ? (
                                <div className="ai-session-task-batch-details">
                                  <label className="ai-session-task-control is-binding">
                                    <span>Inject into *</span>
                                    <Select
                                      value={batchParamName || undefined}
                                      options={releaseTemplateParams.map((param) => ({ value: param.name, label: param.name }))}
                                      onChange={(value) => setReleaseTaskParamValues((prev) => ({ ...prev, batch_param_name: value }))}
                                    />
                                  </label>
                              <div className="ai-session-task-batch-number-grid">
                                <label className="ai-session-task-control">
                                  <span>Batch size</span>
                                  <InputNumber min={1} value={Number(releaseTaskParamValues.batch_size ?? releaseBatchConfig.batchSize) || 1} onChange={(value) => setReleaseTaskParamValues((prev) => ({ ...prev, batch_size: String(value ?? 1) }))} />
                                </label>
                                <label className="ai-session-task-control">
                                  <span>Start line</span>
                                  <InputNumber min={0} value={Number(releaseTaskParamValues.batch_start_line ?? releaseBatchConfig.startLine) || 0} onChange={(value) => setReleaseTaskParamValues((prev) => ({ ...prev, batch_start_line: String(value ?? 0) }))} />
                                </label>
                                <label className="ai-session-task-control">
                                  <span>Limit</span>
                                  <InputNumber min={1} placeholder="No limit" value={(() => {
                                    const value = releaseTaskParamValues.batch_limit ?? releaseBatchConfig.limit;
                                    const parsed = Number(value);
                                    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
                                  })()} onChange={(value) => setReleaseTaskParamValues((prev) => ({ ...prev, batch_limit: value === null ? '' : String(value) }))} />
                                </label>
                                <label className="ai-session-task-control">
                                  <span>Delay (sec)</span>
                                  <InputNumber min={0} value={Number(releaseTaskParamValues.batch_delay ?? releaseBatchConfig.delay) || 0} onChange={(value) => setReleaseTaskParamValues((prev) => ({ ...prev, batch_delay: String(value ?? 0) }))} />
                                </label>
                              </div>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        ) : null}
                      </section>
                    ) : null}
                    <div className="ai-session-task-policies">
                      <label>
                        <span><strong>Incremental</strong><small>{releaseIncremental ? 'Continue from last boundary' : 'Collect full scope'}</small></span>
                        <Checkbox
                          className="ai-session-task-checkbox"
                          checked={releaseIncremental}
                          onChange={(event) => setReleaseIncremental(event.target.checked)}
                        />
                      </label>
                      <label>
                        <span><strong>Robots policy</strong><small>Honor source limits</small></span>
                        <Checkbox
                          className="ai-session-task-checkbox"
                          checked={respectRobots}
                          onChange={(event) => setRespectRobots(event.target.checked)}
                        />
                      </label>
                      <label>
                        <span><strong>Drift guard</strong><small>Pause on structural change</small></span>
                        <Checkbox
                          className="ai-session-task-checkbox"
                          checked={enableDriftGuard}
                          onChange={(event) => setEnableDriftGuard(event.target.checked)}
                        />
                      </label>
                    </div>
                </div>
              </section>
            ) : null}
            <div className="ai-session-release-footer">
              <Button
                type="primary"
                className="ai-template-confirm-btn"
                onClick={() => void handleApplyReleaseAction()}
              >
                {releaseCta}
              </Button>
              <Text className="ai-session-release-note">
                {hasTaskComposer ? taskPublishMeta.launch.desc : releaseActionMeta[selectedReleaseAction].desc}
              </Text>
            </div>
            </div>
            <div className="ai-session-template-tail" aria-hidden="true">
              <div className="ai-session-template-divider" />
            </div>
          </div>
        </div>
      </section>
    );
  };

  const renderWorkflowLayout = () => (
    <div
      className={[
        'ai-session-layout',
        `is-${workflowPhase}`,
        sideInspectorVisible ? 'has-inspector' : '',
        sideInspectorOpen ? 'is-inspector-expanded' : '',
      ].filter(Boolean).join(' ')}
    >
      <div className={`ai-session-template-frame ${templateCollapsed ? 'is-template-collapsed' : ''}`}>
        <div className="ai-session-guide-anchor">
          {renderWorkflowGuideRail()}
        </div>
        {displayWorkflowPhase === 'generating-adapter'
          ? renderWorkflowAdapterPanel()
          : displayWorkflowPhase === 'release-template'
            ? renderWorkflowReleasePanel()
            : renderWorkflowTemplatePanel()}
        {renderDockedPrompt()}
      </div>
      {sideInspectorVisible ? <div className={`ai-session-inspector-divider ${sideInspectorOpen ? 'is-expanded' : ''}`} aria-hidden="true" /> : null}
      {sideInspectorVisible ? renderSessionInspectorPanel() : null}
    </div>
  );

  const renderDockedPrompt = () => {
    return (
      <section className="ai-session-prompt">
        <div className="ai-session-prompt-shell">
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
        <Button icon={<ChevronRightIcon />} onClick={handleResumeAnalysis}>继续</Button>
      ) : null}
      {runStatus !== 'idle' ? (
        <Button danger icon={<StopOutlined />} onClick={handleCancelAnalysis}>取消</Button>
      ) : null}
      <Button
        type={mode === 'publish' ? 'primary' : 'default'}
        icon={mode === 'explore' ? <RobotOutlined /> : mode === 'contract' ? <ExperimentOutlined /> : mode === 'dryrun' ? <SaveOutlined /> : <DeploymentUnitOutlined />}
        onClick={mode === 'explore' ? handleAnalyze : mode === 'contract' ? handleDryRun : mode === 'dryrun' ? () => void handleSave() : undefined}
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
            --ai-session-template-tail-gap: calc(var(--ai-session-prompt-height) + 38px);
            --ai-session-runtime-safe-bottom: 76px;
            --ai-session-body-safe-bottom: 18px;
            --ai-session-veil-height: 148px;
            --ai-session-dock-rail-bottom: 35px;
            --ai-session-dock-panel-bottom: 138px;
            --ai-session-divider-width: 2px;
            --ai-session-inspector-top-offset: 0px;
            --ai-session-inspector-bottom-offset: var(--ai-session-prompt-bottom);
            --ai-session-split-transition: 360ms cubic-bezier(0.22, 1, 0.36, 1);
            --ai-session-split-gap: 20px;
            --ai-session-shell-max-width: min(1360px, calc(100% - 8px));
            --ai-session-split-column-width: calc((var(--ai-session-shell-max-width) - (var(--ai-session-split-gap) * 2) - var(--ai-session-divider-width)) / 2);
            --ai-session-split-offset: calc((var(--ai-session-split-column-width) + (var(--ai-session-split-gap) * 2) + var(--ai-session-divider-width)) / 2);
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
          .ai-session-shell.is-releasing {
            pointer-events: none;
            transform-origin: center center;
            will-change: transform, opacity, filter;
            animation: aiReleaseToTemplate 680ms cubic-bezier(0.4, 0, 0.2, 1) forwards;
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
            isolation: isolate;
            transform: translate(-50%, 0);
            will-change: transform, width;
            transition: width var(--ai-session-split-transition);
          }
          .ai-session-prompt-shell {
            display: flex;
            flex-direction: column;
            animation: aiComposerDock 380ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
          }
          .ai-session-prompt::before {
            content: '';
            position: absolute;
            inset: -18px -26px -22px;
            border-radius: 28px;
            background:
              linear-gradient(180deg, rgba(16, 20, 30, 0.12), rgba(16, 20, 30, 0.36) 54%, rgba(16, 20, 30, 0.58));
            filter: blur(18px);
            opacity: 0.82;
            pointer-events: none;
            z-index: 0;
          }
          .ai-session-prompt-main {
            --prompt-surface: rgba(29, 33, 41);
            display: grid;
            grid-template-columns: 22px minmax(0, 1fr) 30px 30px;
            align-items: center;
            gap: 8px;
            min-height: 58px;
            padding: 0 14px 0 16px;
            border-radius: 18px;
            border: 1px solid ${aura.border};
            box-shadow: 0 34px 78px rgba(0, 0, 0, 0.42), 0 0 0 1px rgba(255, 255, 255, 0.03);
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
            --ai-session-inspector-width: var(--ai-session-split-column-width);
            width: 100%;
            min-height: 0;
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: stretch;
            position: relative;
            max-width: 100%;
            gap: 0;
            transition: max-width var(--ai-session-split-transition), gap var(--ai-session-split-transition), padding var(--ai-session-split-transition);
          }
          .ai-session-layout.has-inspector {
            max-width: var(--ai-session-shell-max-width);
          }
          .ai-session-layout.has-inspector.is-inspector-expanded {
            gap: var(--ai-session-split-gap);
            padding-right: 0;
          }
          .ai-session-template-frame {
            width: min(100%, 882px);
            max-width: 882px;
            flex: 0 1 882px;
            min-width: 0;
            min-height: 0;
            display: flex;
            justify-content: center;
            position: relative;
            margin: 0 auto;
            padding-left: 0;
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition), flex-basis var(--ai-session-split-transition), margin var(--ai-session-split-transition);
          }
          .ai-session-layout.has-inspector.is-inspector-expanded .ai-session-template-frame {
            width: auto;
            max-width: var(--ai-session-split-column-width);
            flex-basis: var(--ai-session-split-column-width);
            margin: 0;
          }
          .ai-session-template-shell {
            width: 100%;
            max-width: 794px;
            min-width: 0;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition);
          }
          .ai-session-main-shell {
            width: 100%;
            max-width: 794px;
            min-width: 0;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
            position: relative;
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition), transform var(--ai-session-split-transition);
          }
          .ai-session-main-shell.is-restoring-from-tab .ai-session-template-scroll,
          .ai-session-main-shell.is-restoring-from-tab .ai-session-adapter-scroll,
          .ai-session-main-shell.is-restoring-from-tab .ai-session-release-scroll {
            animation: ai-session-panel-restore 380ms cubic-bezier(0.2, 0.82, 0.28, 1);
            transform-origin: left top;
          }
          .ai-session-guide-anchor {
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 72px;
            overflow: visible;
            pointer-events: none;
            z-index: 7;
          }
          .ai-session-layout.has-inspector.is-inspector-expanded .ai-session-guide-anchor {
            left: -40px;
          }
          .ai-session-template-frame.is-template-collapsed {
            padding-left: 0;
          }
          .ai-session-fixed-meta {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 280px);
            align-items: center;
            gap: 10px;
            padding: 0 2px 6px;
            background: transparent;
            border: none;
            box-shadow: none;
          }
          .ai-session-fixed-meta > * {
            min-width: 0;
          }
          .ai-session-fixed-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 2px;
            overflow: hidden;
          }
          .ai-session-fixed-eyebrow,
          .ai-session-fixed-subtitle,
          .ai-session-fixed-stat {
            color: ${aura.subtle};
            font-size: 9px;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .ai-session-fixed-title-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: nowrap;
            min-width: 0;
          }
          .ai-session-fixed-title-row h2 {
            margin: 0;
            color: ${aura.text};
            font-size: 18px;
            line-height: 1.2;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .ai-session-fixed-stat {
            white-space: nowrap;
            flex-shrink: 0;
          }
          .ai-session-fixed-subtitle {
            color: ${aura.muted};
            text-transform: none;
            letter-spacing: 0;
            font-size: 10px;
          }
          .ai-session-stage-float {
            position: absolute;
            left: 0;
            top: 50%;
            z-index: 7;
            display: flex;
            align-items: center;
            gap: 6px;
            pointer-events: none;
            transform: translateY(-50%);
          }
          .ai-session-stage-bars {
            width: 44px;
            padding: 0;
            display: flex;
            flex-direction: column;
            gap: 1px;
            pointer-events: auto;
          }
          .ai-session-stage-bar {
            width: 44px;
            padding: 4px 0;
            border: none;
            background: transparent;
            display: inline-flex;
            align-items: center;
            justify-content: flex-start;
            cursor: pointer;
          }
          .ai-session-stage-bar span {
            width: var(--ai-stage-bar-width, 5px);
            height: var(--ai-stage-bar-height, 2px);
            border-radius: 999px;
            background: var(--ai-stage-bar-color, rgba(255, 255, 255, 0.24));
            box-shadow: 0 0 16px color-mix(in srgb, var(--ai-stage-bar-color, rgba(255,255,255,0.24)) 68%, transparent);
            transition: width 180ms ease, height 180ms ease, background 180ms ease, opacity 180ms ease, transform 180ms ease, box-shadow 180ms ease;
          }
          .ai-session-stage-bar:hover span,
          .ai-session-stage-bar:focus-visible span {
            transform: translateX(1px);
          }
          .ai-session-stage-bar.is-visible span {
            opacity: 0.96;
          }
          .ai-session-stage-bar.is-active span {
            box-shadow: 0 0 16px rgba(217, 224, 234, 0.46);
          }
          .ai-session-stage-bar.is-attention span {
            animation: ai-session-step-attention 1.2s ease-in-out infinite;
          }
          .ai-session-stage-card {
            position: absolute;
            left: 40px;
            transform: translateY(-50%);
            width: 228px;
            padding: 10px 10px 8px;
            border-radius: 14px;
            background: rgba(42, 46, 53, 0.82);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 18px 46px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.03);
            pointer-events: auto;
            isolation: isolate;
            transition: top 220ms ease, transform 220ms ease;
          }
          .ai-session-pinned-tab-stack {
            position: absolute;
            left: -18px;
            top: 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            z-index: 8;
          }
          .ai-session-pinned-tab {
            width: 34px;
            height: 34px;
            padding: 0;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px 12px 12px 6px;
            background: rgba(36, 41, 51, 0.94);
            box-shadow: 0 16px 30px rgba(0, 0, 0, 0.22);
            color: ${aura.text};
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease, box-shadow 160ms ease;
          }
          .ai-session-pinned-tab.is-adapter {
            border-radius: 12px 12px 6px 12px;
            background: rgba(30, 36, 48, 0.96);
          }
          .ai-session-pinned-tab:hover,
          .ai-session-pinned-tab:focus-visible {
            border-color: rgba(255, 255, 255, 0.14);
            background: rgba(52, 58, 71, 0.96);
            transform: translateY(-1px);
          }
          .ai-session-pinned-tab.is-entering {
            animation: ai-session-template-tab-enter 360ms cubic-bezier(0.2, 0.82, 0.28, 1);
          }
          .ai-session-pinned-tab-icon {
            flex-shrink: 0;
          }
          .ai-session-pinned-tab-icon.is-template {
            width: 14px;
            height: 15px;
          }
          .ai-session-pinned-tab-icon.is-adapter {
            width: 16px;
            height: 16px;
            color: #fff;
            fill: #fff;
          }
          .ai-session-stage-card::before {
            content: '';
            position: absolute;
            inset: -14px -16px -16px;
            border-radius: 18px;
            background:
              linear-gradient(180deg, rgba(16, 20, 30, 0.12), rgba(16, 20, 30, 0.34) 56%, rgba(16, 20, 30, 0.08));
            filter: blur(16px);
            opacity: 0.92;
            pointer-events: none;
            z-index: -1;
          }
          .ai-session-stage-card strong {
            display: block;
            color: ${aura.text};
            font-size: 13px;
            line-height: 1.4;
            font-weight: 600;
          }
          .ai-session-stage-card p {
            margin: 6px 0 8px;
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.55;
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
            overflow-x: hidden;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2px 0 0;
            position: relative;
            isolation: isolate;
            border-radius: 18px;
            background: transparent;
          }
          .ai-session-scroll-frame {
            width: 100%;
            flex: 1;
            min-height: 0;
            display: flex;
            position: relative;
            overflow: visible;
          }
          .ai-session-scroll-frame > .ai-session-pinned-tab-stack {
            left: max(-18px, calc(50% - 406px));
            top: 16px;
          }
          .ai-session-adapter-scroll {
            width: 100%;
            flex: 1;
            min-height: 0;
            overflow-x: hidden;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2px 0 0;
            position: relative;
            isolation: isolate;
            border-radius: 18px;
            background: transparent;
          }
          .ai-session-release-scroll {
            width: 100%;
            flex: 1;
            min-height: 0;
            overflow-x: hidden;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2px 0 0;
            position: relative;
            isolation: isolate;
            border-radius: 18px;
            background: transparent;
          }
          .ai-template-sheet {
            width: calc(100% - 18px);
            max-width: 776px;
            margin: 0 auto;
            margin-bottom: 20px;
            padding: 22px 24px 22px;
            background:
              linear-gradient(180deg, rgba(44, 49, 60, 0.98), rgba(34, 39, 49, 0.98)),
              rgba(28, 33, 42, 0.98);
            color: ${aura.text};
            box-shadow: 0 20px 52px rgba(0, 0, 0, 0.24);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            flex: none;
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition), padding var(--ai-session-split-transition);
          }
          .ai-template-sheet-body {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-template-stage-section {
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding-bottom: 10px;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.18);
          }
          .ai-template-stage-section:last-child {
            padding-bottom: 0;
            border-bottom: none;
          }
          .ai-template-stage-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 10px;
          }
          .ai-template-stage-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 3px;
          }
          .ai-template-stage-title {
            color: ${aura.text};
            font-size: 12px;
            font-weight: 600;
            line-height: 1.3;
            letter-spacing: 0.04em;
            text-transform: uppercase;
          }
          .ai-template-stage-copy small {
            color: ${aura.muted};
            font-size: 10px;
            line-height: 1.45;
          }
          .ai-template-stage-actions {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: ${aura.subtle};
            font-size: 10px;
            white-space: nowrap;
          }
          .ai-template-stage-add {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: rgba(255, 255, 255, 0.04);
            color: ${aura.text};
            cursor: pointer;
            transition: border-color 160ms ease, background 160ms ease, color 160ms ease;
          }
          .ai-template-stage-add:hover {
            border-color: rgba(138, 180, 255, 0.32);
            background: rgba(138, 180, 255, 0.12);
            color: #ffffff;
          }
          .ai-template-stage-body {
            display: flex;
            flex-direction: column;
            gap: 5px;
            padding: 4px 6px;
            border-radius: 12px;
            transition: background 160ms ease, box-shadow 160ms ease;
          }
          .ai-template-stage-section:hover .ai-template-stage-body,
          .ai-template-stage-section:focus-within .ai-template-stage-body {
            background: rgba(255, 255, 255, 0.04);
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06);
          }
          .ai-template-confirm-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 14px 16px;
            margin: 18px -4px -4px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.025);
          }
          .ai-template-confirm-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 4px;
          }
          .ai-template-confirm-copy strong {
            color: ${aura.text};
            font-size: 13px;
            line-height: 1.35;
            font-weight: 600;
          }
          .ai-template-confirm-copy span {
            color: ${aura.muted};
            font-size: 11px;
            line-height: 1.55;
          }
          .ai-template-confirm-btn {
            min-width: 126px;
            height: 32px !important;
            border-radius: 8px !important;
            border: 1px solid rgba(129, 216, 208, 0.34) !important;
            background: rgba(129, 216, 208, 0.14) !important;
            box-shadow: none !important;
            color: rgba(222, 255, 251, 0.96) !important;
            font-weight: 600;
          }
          .ai-template-confirm-btn:hover {
            border-color: rgba(129, 216, 208, 0.56) !important;
            background: rgba(129, 216, 208, 0.22) !important;
            color: #f1fffd !important;
          }
          .ai-session-inline-alert {
            width: min(100%, 776px);
            margin: 0 auto 12px;
            border-radius: 14px;
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition);
          }
          .ai-session-inline-alert .ant-alert-message {
            font-size: 11px;
            line-height: 1.35;
          }
          .ai-session-adapter-shell,
          .ai-session-release-shell {
            width: calc(100% - 18px);
            max-width: 776px;
            margin: 0 auto;
            padding: 20px 22px;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background:
              linear-gradient(180deg, rgba(44, 49, 60, 0.98), rgba(34, 39, 49, 0.98)),
              rgba(28, 33, 42, 0.98);
            box-shadow: 0 20px 52px rgba(0, 0, 0, 0.24);
            color: ${aura.text};
            transition: width var(--ai-session-split-transition), max-width var(--ai-session-split-transition), padding var(--ai-session-split-transition);
          }
          .ai-session-adapter-shell {
            flex: none;
            margin-bottom: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            position: relative;
          }
          .ai-session-adapter-overview {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .ai-session-adapter-copy {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 5px;
          }
          .ai-session-adapter-copy h3 {
            margin: 0;
            font-size: 16px;
            line-height: 1.3;
            font-weight: 600;
            word-break: break-word;
          }
          .ai-session-adapter-copy p {
            margin: 0;
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.55;
          }
          .ai-session-adapter-progress {
            width: min(184px, 100%);
            display: flex;
            flex-direction: column;
            gap: 8px;
            align-items: flex-end;
            flex-shrink: 0;
          }
          .ai-session-adapter-progress span {
            color: ${aura.subtle};
            font-size: 11px;
          }
          .ai-session-adapter-steps {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-session-adapter-steps.is-flow {
            padding-top: 2px;
          }
          .ai-session-adapter-task {
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.028);
            overflow: hidden;
          }
          .ai-session-adapter-task-head {
            min-height: 52px;
            padding: 0 14px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: inherit;
            text-align: left;
          }
          .ai-session-adapter-task-copy {
            min-width: 0;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 3px;
          }
          .ai-session-adapter-task-copy strong {
            display: block;
            color: ${aura.text};
            font-size: 13px;
            line-height: 1.4;
            font-weight: 600;
          }
          .ai-session-adapter-task-status {
            border: none;
            background: transparent;
            padding: 0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: rgba(255, 255, 255, 0.64);
            font-size: 12px;
            line-height: 1.4;
            white-space: nowrap;
            flex-shrink: 0;
            cursor: pointer;
            transition: color 180ms ease, opacity 180ms ease;
          }
          .ai-session-adapter-task-status:hover {
            color: rgba(255, 255, 255, 0.88);
          }
          .ai-session-adapter-task-status[aria-expanded='true'] {
            color: rgba(255, 255, 255, 0.92);
          }
          .ai-session-adapter-task-status-label {
            display: inline-flex;
            align-items: center;
          }
          .ai-session-adapter-task-status-caret {
            width: 12px;
            height: 12px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: currentColor;
            transform: rotate(0deg);
            transform-origin: center;
            transition: transform 180ms ease, color 180ms ease, opacity 180ms ease;
          }
          .ai-session-adapter-task-status-caret.is-expanded {
            transform: rotate(90deg);
          }
          .ai-session-adapter-task-body {
            padding: 0 18px 14px 14px;
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-session-adapter-task-body p {
            margin: 0;
            color: ${aura.muted};
            font-size: 11px;
            line-height: 1.6;
          }
          .ai-session-adapter-task-list {
            display: flex;
            flex-direction: column;
            gap: 7px;
          }
          .ai-session-adapter-task-item {
            display: grid;
            grid-template-columns: 8px minmax(0, 1fr);
            gap: 8px;
            align-items: start;
          }
          .ai-session-adapter-task-item i {
            width: 4px;
            height: 4px;
            margin-top: 7px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.34);
            display: block;
          }
          .ai-session-adapter-task-item span {
            color: rgba(255, 255, 255, 0.78);
            font-size: 11px;
            line-height: 1.55;
          }
          .ai-session-adapter-task.is-active {
            border-color: rgba(138, 180, 255, 0.22);
            background: rgba(138, 180, 255, 0.08);
          }
          .ai-session-adapter-task.is-done {
            background: transparent;
          }
          .ai-session-adapter-task.is-active .ai-session-adapter-task-status {
            color: rgba(188, 219, 255, 0.88);
          }
          .ai-session-adapter-task.is-done .ai-session-adapter-task-status {
            color: rgba(255, 255, 255, 0.66);
          }
          .ai-session-release-shell {
            flex: none;
            margin-bottom: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            position: relative;
          }
          .ai-session-release-head {
            min-height: 54px;
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            padding-bottom: 12px;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.12);
          }
          .ai-session-release-heading {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 8px;
          }
          .ai-session-release-head-controls {
            display: flex;
            align-items: center;
            gap: 10px;
            flex: none;
          }
          .ai-session-release-actions {
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .ai-session-release-action {
            width: 34px;
            height: 34px;
            padding: 0;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.025);
            color: rgba(255, 255, 255, 0.54);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: color 160ms ease, border-color 160ms ease, background 160ms ease, transform 160ms ease;
          }
          .ai-session-release-action:hover,
          .ai-session-release-action:focus-visible,
          .ai-session-release-action.is-selected {
            color: #ffffff;
            border-color: rgba(138, 180, 255, 0.28);
            background: rgba(138, 180, 255, 0.1);
            transform: translateY(-1px);
          }
          .ai-session-release-action .anticon {
            font-size: 15px;
          }
          .ai-session-release-action > svg {
            width: 15px;
            height: 15px;
          }
          .ai-session-release-tooltip {
            width: 210px;
            display: flex;
            flex-direction: column;
            gap: 4px;
          }
          .ai-session-release-tooltip strong {
            color: #ffffff;
            font-size: 12px;
            line-height: 1.35;
          }
          .ai-session-release-tooltip span {
            color: rgba(255, 255, 255, 0.68);
            font-size: 11px;
            line-height: 1.45;
          }
          .ai-session-task-create {
            padding: 12px 0;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            border-bottom: 1px dashed rgba(255, 255, 255, 0.12);
            display: flex;
            flex-direction: column;
            gap: 12px;
          }
          .ai-session-task-create-title {
            min-width: 0;
            display: flex;
            align-items: center;
            gap: 9px;
          }
          .ai-session-task-create-title > .anticon {
            margin-top: 0;
            color: rgba(188, 219, 255, 0.9);
            font-size: 16px;
          }
          .ai-session-task-create-title > div {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 3px;
          }
          .ai-session-task-create-title strong {
            color: ${aura.text};
            font-size: 13px;
            line-height: 1.35;
            font-weight: 600;
          }
          .ai-session-task-create-title span {
            color: ${aura.muted};
            font-size: 11px;
            line-height: 1.45;
          }
          .ai-session-task-create-skip {
            padding: 0;
            border: 0;
            background: transparent;
            color: rgba(255, 255, 255, 0.54);
            font-size: 11px;
            line-height: 20px;
            cursor: pointer;
            flex-shrink: 0;
          }
          .ai-session-task-create-skip:hover {
            color: #ffffff;
            text-decoration: underline;
          }
          .ai-session-release-meta {
            display: flex;
            align-items: center;
            gap: 0;
            min-height: 16px;
            color: ${aura.muted};
            font-size: 10px;
            line-height: 1.35;
            white-space: nowrap;
            overflow: hidden;
          }
          .ai-session-release-meta span {
            max-width: 180px;
            padding: 0 9px;
            overflow: hidden;
            text-overflow: ellipsis;
            border-right: 1px solid rgba(255, 255, 255, 0.1);
          }
          .ai-session-release-meta span:first-child {
            padding-left: 0;
          }
          .ai-session-release-meta span:last-child {
            border-right: none;
          }
          .ai-session-task-scheduler {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-session-task-scheduler-row {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 10px;
            min-height: 54px;
          }
          .ai-session-task-schedule-control,
          .ai-session-task-run-schedule {
            width: fit-content;
          }
          .ai-session-task-concurrency-control {
            width: 82px;
            flex: none;
          }
          .ai-session-task-schedule-options {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
          }
          .ai-session-task-schedule-options .ai-session-task-control {
            width: 132px;
            flex: none;
          }
          .ai-session-task-schedule-options .ant-select {
            width: 100%;
          }
          .ai-session-task-control {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 6px;
          }
          .ai-session-task-control > span {
            color: ${aura.subtle};
            font-size: 9px;
            line-height: 1.3;
            letter-spacing: 0;
          }
          .ai-session-task-control .ant-input,
          .ai-session-task-control .ant-input-number,
          .ai-session-task-control .ant-select-selector,
          .ai-session-task-control .ant-picker {
            font-size: 12px;
          }
          .ai-session-task-control .ant-input,
          .ai-session-task-control .ant-input-number,
          .ai-session-task-control .ant-select,
          .ai-session-task-control .ant-select-selector,
          .ai-session-task-control .ant-picker,
          .ai-session-task-control .ant-segmented {
            height: 32px;
          }
          .ai-session-task-control .ant-picker {
            width: 100%;
          }
          .ai-session-task-control .ant-picker-suffix {
            color: rgba(255, 255, 255, 0.5);
          }
          .ai-session-task-control .ant-input-number-input-wrap,
          .ai-session-task-control .ant-input-number-input {
            height: 30px;
          }
          .ai-session-task-control .ant-segmented-item-label {
            min-width: 0;
            padding-inline: 10px;
            line-height: 30px;
            white-space: nowrap;
          }
          .ai-session-task-run-schedule .ant-segmented-item-label {
            font-size: 10px;
          }
          .ai-session-task-control .ant-input-number {
            width: 100%;
          }
          .ai-session-task-control .ant-segmented {
            background: rgba(255, 255, 255, 0.045);
          }
          .ai-session-task-checkbox {
            flex: none;
            line-height: 1;
          }
          .ai-session-task-checkbox .ant-checkbox-inner {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border-color: ${aura.border};
            background: rgba(255, 255, 255, 0.04);
          }
          .ai-session-task-checkbox .ant-checkbox-checked .ant-checkbox-inner {
            border-color: rgba(129, 216, 208, 0.68);
            background: ${tiffanyAccent};
          }
          .ai-session-task-template-params {
            padding: 10px 0;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
          }
          .ai-session-task-template-params-head {
            min-height: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 8px;
            color: ${aura.subtle};
            font-size: 10px;
          }
          .ai-session-task-template-params-head > label {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            color: ${aura.muted};
            white-space: nowrap;
          }
          .ai-session-task-param-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
          }
          .ai-session-task-param-grid .ai-session-task-control {
            width: 168px;
          }
          .ai-session-task-param-grid .ai-session-task-control.is-compact {
            width: 124px;
          }
          .ai-session-task-param-grid .ai-session-task-control.is-long {
            width: 320px;
          }
          .ai-session-task-file-picker {
            min-width: 0;
            height: 32px;
            display: flex;
            align-items: center;
            gap: 7px;
          }
          .ai-session-task-file-picker .ant-btn {
            height: 30px;
            padding: 0 8px;
            border-radius: 6px;
            color: rgba(255, 255, 255, 0.72);
            font-size: 11px;
          }
          .ai-session-task-file-reference {
            min-width: 0;
            max-width: 124px;
            padding: 3px 6px;
            border: 1px solid rgba(138, 180, 255, 0.18);
            border-radius: 5px;
            background: rgba(138, 180, 255, 0.08);
            color: rgba(188, 219, 255, 0.86);
            font-size: 10px;
            line-height: 1.2;
            display: inline-flex;
            align-items: center;
            gap: 3px;
          }
          .ai-session-task-file-reference > span {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-session-task-file-remove {
            width: 14px;
            height: 14px;
            padding: 0;
            border: 0;
            border-radius: 50%;
            background: transparent;
            color: rgba(255, 255, 255, 0.48);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            flex: none;
          }
          .ai-session-task-file-remove:hover {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.08);
          }
          .ai-session-task-file-remove .anticon {
            font-size: 10px;
          }
          .ai-session-task-batch-params {
            margin-top: 10px;
          }
          .ai-session-task-batch-params-body {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 12px;
          }
          .ai-session-task-batch-params-body .ai-session-task-control.is-file {
            width: 100%;
            flex: none;
          }
          .ai-session-task-batch-details {
            display: flex;
            align-items: end;
            gap: 12px;
          }
          .ai-session-task-batch-details .ai-session-task-control.is-binding {
            width: 126px;
            flex: none;
          }
          .ai-session-task-batch-number-grid {
            display: flex;
            gap: 8px;
          }
          .ai-session-task-batch-number-grid .ai-session-task-control {
            width: 84px;
            flex: none;
          }
          .ai-session-task-policies {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            padding: 9px 0;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
          }
          .ai-session-task-policies label {
            min-width: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
          }
          .ai-session-task-policies label > span {
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 2px;
          }
          .ai-session-task-policies strong {
            color: ${aura.text};
            font-size: 11px;
            line-height: 1.35;
            font-weight: 500;
          }
          .ai-session-task-policies small {
            color: ${aura.muted};
            font-size: 10px;
            line-height: 1.35;
          }
          .ai-session-time-picker-dropdown .ant-picker-panel-container {
            border: 1px solid rgba(255, 255, 255, 0.1);
            background: rgba(37, 42, 52, 0.98);
            box-shadow: 0 18px 44px rgba(0, 0, 0, 0.34);
          }
          .ai-session-time-picker-dropdown .ant-picker-time-panel-column {
            overscroll-behavior: contain;
            scrollbar-width: thin;
            scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
          }
          .ai-session-time-picker-dropdown .ant-picker-time-panel-column > li.ant-picker-time-panel-cell .ant-picker-time-panel-cell-inner {
            color: rgba(255, 255, 255, 0.68);
          }
          .ai-session-time-picker-dropdown .ant-picker-time-panel-column > li.ant-picker-time-panel-cell-selected .ant-picker-time-panel-cell-inner,
          .ai-session-time-picker-dropdown .ant-picker-time-panel-column > li.ant-picker-time-panel-cell:hover .ant-picker-time-panel-cell-inner {
            background: rgba(129, 216, 208, 0.16);
            color: #edfffc;
          }
          .ai-session-time-picker-dropdown .ant-picker-time-panel-column::after {
            border-color: rgba(255, 255, 255, 0.07);
          }
          .ai-session-time-picker-dropdown .ant-picker-footer {
            padding: 7px 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
          }
          .ai-session-time-picker-dropdown .ant-picker-now {
            color: rgba(188, 219, 255, 0.9);
            font-size: 11px;
          }
          .ai-session-time-picker-dropdown .ant-picker-now:hover {
            color: #ffffff;
          }
          .ai-session-time-picker-dropdown .ant-picker-ok .ant-btn {
            height: 26px;
            padding: 0 10px;
            border-radius: 6px;
            border-color: rgba(129, 216, 208, 0.34);
            background: rgba(129, 216, 208, 0.16);
            color: #e6fffb;
            font-size: 11px;
          }
          .ai-session-time-picker-dropdown .ant-picker-ok .ant-btn:hover {
            border-color: rgba(129, 216, 208, 0.58);
            background: rgba(129, 216, 208, 0.24);
          }
          .ai-session-release-footer {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding-top: 6px;
          }
          .ai-session-release-note {
            color: ${aura.muted};
            font-size: 12px;
            line-height: 1.6;
          }
          .ai-session-template-tail {
            width: 100%;
            min-height: var(--ai-session-template-tail-gap);
            display: flex;
            flex-direction: column;
            align-items: center;
            flex: none;
            background: transparent;
            box-shadow: none;
          }
          .ai-session-template-divider {
            width: calc(100% + 88px);
            max-width: 882px;
            height: 0;
            border-top: 1px dashed rgba(255, 255, 255, 0.26);
            opacity: 0.96;
          }
          .ai-session-status-line {
            width: 100%;
            min-width: 0;
            height: 24px;
            padding: 0;
            border-radius: 0;
            border: none;
            background: transparent;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 5px;
            color: ${aura.text};
            cursor: pointer;
            overflow: hidden;
            align-self: center;
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
            display: block;
          }
          .ai-session-status-favicon {
            width: 18px;
            height: 18px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.9);
            border-radius: 4px;
            overflow: hidden;
            flex-shrink: 0;
          }
          .ai-session-status-favicon img {
            width: 16px;
            height: 16px;
            display: block;
            border-radius: 4px;
          }
          .ai-session-status-copy {
            min-width: 0;
            max-width: min(100%, 236px);
            position: relative;
            display: block;
            overflow: hidden;
            white-space: nowrap;
            flex: 0 1 auto;
          }
          .ai-session-status-copy-base,
          .ai-session-status-copy-sweep {
            display: block;
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
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
          .ai-session-status-line.is-open {
            opacity: 1;
          }
          .ai-session-inspector-divider {
            position: relative;
            top: auto;
            right: auto;
            bottom: auto;
            align-self: stretch;
            flex: 0 0 0;
            width: 0;
            margin: var(--ai-session-inspector-top-offset) 0 var(--ai-session-inspector-bottom-offset);
            border-left: 1px dashed rgba(255, 255, 255, 0.26);
            pointer-events: none;
            z-index: 7;
            opacity: 0;
            transform: translateX(30px);
            transition: flex-basis var(--ai-session-split-transition), opacity var(--ai-session-split-transition), transform var(--ai-session-split-transition);
          }
          .ai-session-layout.has-inspector.is-inspector-expanded .ai-session-inspector-divider {
            flex-basis: var(--ai-session-divider-width);
            width: 0;
            opacity: 1;
            transform: translateX(0);
          }
          .ai-session-inspector-shell {
            position: relative;
            right: auto;
            top: auto;
            bottom: auto;
            align-self: stretch;
            flex: 0 0 0;
            width: 0;
            max-width: 0;
            min-width: 0;
            border-radius: 18px;
            border: 1px solid rgba(255, 255, 255, 0);
            background: rgba(24, 28, 36, 0.97);
            box-shadow: 0 24px 58px rgba(0, 0, 0, 0);
            backdrop-filter: blur(20px);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            z-index: 9;
            opacity: 0;
            transform: translateX(54px);
            pointer-events: none;
            transition:
              width var(--ai-session-split-transition),
              max-width var(--ai-session-split-transition),
              flex-basis var(--ai-session-split-transition),
              margin var(--ai-session-split-transition),
              opacity var(--ai-session-split-transition),
              transform var(--ai-session-split-transition),
              border-color var(--ai-session-split-transition),
              box-shadow var(--ai-session-split-transition);
          }
          .ai-session-inspector-shell.is-expanded {
            flex-basis: var(--ai-session-inspector-width);
            width: var(--ai-session-inspector-width);
            max-width: var(--ai-session-inspector-width);
            border-color: rgba(255, 255, 255, 0.08);
            box-shadow: 0 24px 58px rgba(0, 0, 0, 0.28);
            opacity: 1;
            transform: translateX(0);
            pointer-events: auto;
          }
          .ai-session-inspector-tabs {
            height: 44px;
            padding: 8px 10px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            display: flex;
            align-items: flex-end;
            gap: 6px;
            overflow-x: auto;
            overflow-y: hidden;
            scrollbar-width: none;
          }
          .ai-session-inspector-tabs::-webkit-scrollbar {
            display: none;
          }
          .ai-session-inspector-tab {
            flex: 0 0 auto;
            max-width: 220px;
            height: 32px;
            padding: 0 6px 0 10px;
            border-radius: 10px 10px 0 0;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-bottom: none;
            background: rgba(255, 255, 255, 0.04);
            display: flex;
            align-items: center;
            gap: 4px;
          }
          .ai-session-inspector-tab.is-active {
            background: rgba(255, 255, 255, 0.1);
          }
          .ai-session-inspector-tab-main,
          .ai-session-inspector-tab-close {
            border: none;
            background: transparent;
            color: ${aura.text};
            padding: 0;
            cursor: pointer;
          }
          .ai-session-inspector-tab-main {
            min-width: 0;
            flex: 1;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            overflow: hidden;
          }
          .ai-session-inspector-tab-icon {
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.9);
            flex-shrink: 0;
          }
          .ai-session-inspector-tab-icon.is-browser img {
            width: 14px;
            height: 14px;
            border-radius: 4px;
            display: block;
          }
          .ai-session-inspector-tab-label {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 12px;
          }
          .ai-session-inspector-tab-close {
            width: 18px;
            height: 18px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.46);
          }
          .ai-session-inspector-tab-close:hover {
            color: rgba(255, 255, 255, 0.88);
          }
          .ai-session-inspector-body {
            flex: 1;
            min-height: 0;
            display: flex;
            flex-direction: column;
            padding: 12px;
          }
          .ai-session-inspector-browser,
          .ai-session-inspector-editor {
            flex: 1;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 12px;
          }
          .ai-session-inspector-browser-frame,
          .ai-session-inspector-editor-body {
            flex: 1;
            min-height: 0;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(14, 17, 23, 0.78);
            overflow: hidden;
          }
          .ai-session-inspector-browser-frame {
            flex: 1 1 auto;
            min-height: 420px;
          }
          .ai-session-inspector-browser-frame iframe {
            width: 100%;
            height: 100%;
            border: none;
            background: #fff;
          }
          .ai-session-inspector-empty {
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
            color: ${aura.muted};
            text-align: center;
            padding: 24px;
          }
          .ai-session-inspector-empty strong {
            color: ${aura.text};
            font-size: 13px;
          }
          .ai-session-inspector-empty span {
            font-size: 11px;
            word-break: break-word;
          }
          .ai-session-inspector-editor-body {
            padding: 12px 0;
            overflow: auto;
            background:
              radial-gradient(circle at top, rgba(102, 217, 239, 0.08), transparent 44%),
              linear-gradient(180deg, rgba(45, 46, 74, 0.98), rgba(33, 34, 55, 0.98));
            border-color: rgba(255, 255, 255, 0.06);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
          }
          .ai-session-inspector-editor-line {
            display: grid;
            grid-template-columns: 34px 12px minmax(0, 1fr);
            gap: 10px;
            padding: 0 14px;
            align-items: start;
            color: #f8f8f2;
            font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
            font-size: 12px;
            line-height: 1.76;
          }
          .ai-session-inspector-editor-line + .ai-session-inspector-editor-line {
            margin-top: 2px;
          }
          .ai-session-inspector-editor-line.is-added .ai-session-inspector-editor-prefix,
          .ai-session-inspector-editor-line.is-added code {
            color: #a6e3a1;
          }
          .ai-session-inspector-editor-no {
            color: rgba(98, 114, 164, 0.82);
            text-align: right;
            font-variant-numeric: tabular-nums;
          }
          .ai-session-inspector-editor-prefix {
            color: #6272a4;
            text-align: center;
          }
          .ai-session-inspector-editor-line code {
            white-space: pre-wrap;
            word-break: break-word;
            color: #f8f8f2;
            font-weight: 500;
          }
          .ai-session-python-token {
            color: inherit;
            -webkit-text-stroke: 0.12px currentColor;
          }
          .ai-session-python-token.is-keyword {
            color: #ff79c6;
          }
          .ai-session-python-token.is-builtin {
            color: #8be9fd;
          }
          .ai-session-python-token.is-string {
            color: #f1fa8c;
          }
          .ai-session-python-token.is-comment {
            color: #6272a4;
            font-style: italic;
          }
          .ai-session-python-token.is-number {
            color: #bd93f9;
          }
          .ai-session-python-token.is-function-name {
            color: #50fa7b;
          }
          .ai-session-python-token.is-class-name {
            color: #8be9fd;
          }
          .ai-session-python-token.is-property {
            color: #66d9ef;
          }
          .ai-session-python-token.is-decorator {
            color: #ffb86c;
          }
          .ai-session-python-token.is-operator,
          .ai-session-python-token.is-punctuation {
            color: #f8f8f2;
          }
          .ai-session-side-trigger,
          .ai-session-side-hotspot {
            position: absolute;
            right: 0;
            top: 112px;
            bottom: calc(var(--ai-session-prompt-height) + 52px);
            width: clamp(52px, calc((100vw - 882px) / 2 - 12px), 86px);
            border: none;
            background: transparent;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.28);
            cursor: pointer;
            transition: color 160ms ease, transform 160ms ease;
          }
          .ai-session-side-hotspot {
            align-items: flex-end;
            padding-right: 18px;
          }
          .ai-session-side-trigger::before,
          .ai-session-side-hotspot::before {
            content: '';
            position: absolute;
            inset: 18px 0;
            border-left: 1px dashed rgba(255, 255, 255, 0.08);
          }
          .ai-session-side-trigger:hover,
          .ai-session-side-trigger.is-open,
          .ai-session-side-hotspot:hover,
          .ai-session-side-hotspot.is-open {
            color: rgba(255, 255, 255, 0.72);
            transform: translateX(-2px);
          }
          .ai-session-side-trigger-icon {
            width: 20px;
            height: 20px;
            position: relative;
            z-index: 1;
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
          .ai-session-side-trigger,
          .ai-session-side-hotspot,
          .ai-session-side-panel,
          .ai-side-browser-shell,
          .ai-side-code-shell,
          .ai-side-browser-bar,
          .ai-side-browser-viewport,
          .ai-side-browser-row,
          .ai-side-code-row,
          .ai-side-code-card,
          .ai-side-code-card-top,
          .ai-side-code-card-icon,
          .ai-side-code-card-icon-svg,
          .ai-side-code-path,
          .ai-side-code-diff,
          .ai-side-code-list {
            display: none;
          }
          .ai-session-side-head {
            display: flex;
            align-items: center;
            justify-content: flex-end;
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
          .ai-side-browser-bar {
            min-height: 40px;
            padding: 0 12px;
            border-radius: 12px;
            background: rgba(18, 21, 27, 0.72);
            border: 1px solid rgba(255, 255, 255, 0.07);
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .ai-side-browser-bar strong {
            min-width: 0;
            color: rgba(255, 255, 255, 0.82);
            font-size: 12px;
            font-weight: 500;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-side-browser-favicon {
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: rgba(255, 255, 255, 0.9);
            border-radius: 4px;
            overflow: hidden;
            flex-shrink: 0;
          }
          .ai-side-browser-favicon img {
            width: 16px;
            height: 16px;
            display: block;
            border-radius: 4px;
          }
          .ai-side-browser-viewport {
            flex: 1;
            min-height: 0;
            padding: 0;
            border-radius: 16px;
            background:
              linear-gradient(180deg, rgba(40, 45, 54, 0.98), rgba(24, 28, 36, 0.98));
            border: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            flex-direction: column;
            gap: 10px;
            overflow: hidden;
          }
          .ai-side-browser-frame {
            width: 100%;
            height: 100%;
            min-height: 0;
            border: 0;
            background: #fff;
          }
          .ai-side-browser-empty {
            padding: 16px;
            color: rgba(255, 255, 255, 0.5);
            font-size: 12px;
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
          .ai-side-code-card {
            padding: 14px;
            border-radius: 16px;
            background: rgba(18, 21, 27, 0.78);
            border: 1px solid rgba(255, 255, 255, 0.08);
            display: flex;
            flex-direction: column;
            gap: 12px;
          }
          .ai-side-code-card-top {
            display: flex;
            align-items: center;
            gap: 10px;
          }
          .ai-side-code-card-top strong {
            min-width: 0;
            color: rgba(255, 255, 255, 0.92);
            font-size: 13px;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .ai-side-code-card-icon {
            width: 24px;
            height: 24px;
            border-radius: 7px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(255, 255, 255, 0.06);
            color: rgba(255, 255, 255, 0.86);
            flex-shrink: 0;
          }
          .ai-side-code-card-icon-svg {
            width: 14px;
            height: 10px;
            display: block;
          }
          .ai-side-code-path {
            color: ${aura.muted};
            font-size: 11px;
            line-height: 1.5;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            word-break: break-word;
          }
          .ai-side-code-diff {
            display: flex;
            align-items: center;
            gap: 12px;
          }
          .ai-side-code-diff b {
            font-size: 28px;
            line-height: 1;
            font-weight: 600;
          }
          .ai-side-code-diff .is-added {
            color: #65d5a3;
          }
          .ai-side-code-diff .is-removed {
            color: #ff7d7d;
          }
          .ai-side-code-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .ai-template-field {
            --ai-template-indent: calc(var(--ai-template-depth, 0) * 12px);
            display: grid;
            grid-template-columns: minmax(156px, 198px) minmax(0, 1fr);
            gap: 8px 10px;
            align-items: start;
            padding: 2px 8px 2px 0;
            padding-left: var(--ai-template-indent);
            border-bottom: none;
            border-radius: 10px;
            transition: background 160ms ease, box-shadow 160ms ease;
          }
          .ai-template-field.is-group {
            grid-template-columns: minmax(0, 1fr);
            gap: 0;
            padding: 2px 8px 0 0;
            padding-left: var(--ai-template-indent);
            border-bottom: none;
          }
          .ai-template-field.is-root-group {
            margin-top: 6px;
            padding-top: 2px;
          }
          .ai-template-field.is-root-group:first-child {
            margin-top: 0;
          }
          .ai-template-field.is-item-group {
            padding-top: 6px;
          }
          .ai-template-field:last-child,
          .ai-template-field.is-group-end:last-child {
            padding-bottom: 0;
            border-bottom: none;
            margin-bottom: 0;
          }
          .ai-template-field-key {
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 3px;
            padding-top: 1px;
          }
          .ai-template-field-key span {
            color: ${aura.text};
            font-size: 12px;
            font-weight: 600;
            line-height: 1.35;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          }
          .ai-template-field.is-group .ai-template-field-key {
            gap: 1px;
          }
          .ai-template-field.is-group .ai-template-field-key span {
            color: ${aura.text};
            font-size: 12px;
            line-height: 1.35;
            letter-spacing: 0;
            text-transform: none;
          }
          .ai-template-field.is-yaml-list-item .ai-template-field-key {
            display: grid;
            grid-template-columns: 10px minmax(0, 1fr);
            align-items: start;
            column-gap: 6px;
          }
          .ai-template-field-dash {
            grid-column: 1;
            justify-self: end;
            visibility: hidden;
            color: rgba(255, 255, 255, 0.72);
            font-size: 12px;
            line-height: 1.35;
            font-style: normal;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
          }
          .ai-template-field.is-yaml-list-item .ai-template-field-key span {
            grid-column: 2;
          }
          .ai-template-field.has-yaml-dash .ai-template-field-dash {
            visibility: visible;
          }
          .ai-template-field.is-root-group .ai-template-field-key {
            gap: 0;
          }
          .ai-template-field.is-root-group .ai-template-field-key span {
            color: rgba(255, 255, 255, 0.92);
            font-size: 11px;
            line-height: 1.45;
            letter-spacing: 0.02em;
            text-transform: none;
          }
          .ai-template-field.is-item-group .ai-template-field-key span {
            color: rgba(255, 255, 255, 0.9);
            font-size: 10.5px;
            letter-spacing: 0.02em;
            text-transform: none;
          }
          .ai-template-field-value {
            min-height: 22px;
            padding: 0;
            border: none;
            background: transparent;
            border-radius: 0;
            display: flex;
            align-items: center;
          }
          .ai-template-field-value.is-rich {
            align-items: stretch;
          }
          .ai-template-field.is-site-description {
            align-items: stretch;
          }
          .ai-template-field.is-site-description .ai-template-field-key {
            padding-top: 8px;
          }
          .ai-template-field-value pre {
            margin: 0;
            white-space: pre-wrap;
            word-break: break-word;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            font-size: 12px;
            line-height: 1.45;
            color: ${aura.text};
          }
          .ai-template-field-value .ant-input,
          .ai-template-field-value .ant-input-affix-wrapper,
          .ai-template-field-value .ant-input-number,
          .ai-template-field-value .ant-select-selector,
          .ai-template-field-value .ant-input-textarea textarea {
            background: transparent !important;
            color: ${aura.text} !important;
            border-color: transparent !important;
            box-shadow: none !important;
            padding: 0 !important;
            border-radius: 0 !important;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            font-size: 12px !important;
            line-height: 1.45 !important;
          }
          .ai-template-field-value input.ant-input,
          .ai-template-field-value .ant-input-affix-wrapper,
          .ai-template-field-value .ant-input-number,
          .ai-template-field-value .ant-select-selector {
            min-height: 22px !important;
            height: 22px !important;
          }
          .ai-template-field-value .ant-select,
          .ai-template-field-value .ant-input-number {
            width: 100%;
          }
          .ai-template-field-value input.ant-input {
            line-height: 22px !important;
          }
          .ai-template-field-value .ant-input-number-input-wrap {
            height: 100%;
            display: flex;
            align-items: center;
          }
          .ai-template-field-value .ant-input-number-input {
            height: 22px !important;
            line-height: 22px !important;
            padding: 0;
            color: ${aura.text};
          }
          .ai-template-number-input {
            max-width: 100%;
          }
          .ai-template-number-input .ant-input-number-input-wrap {
            justify-content: flex-start;
            padding-inline-start: 0 !important;
          }
          .ai-template-number-input .ant-input-number-handler-wrap {
            opacity: 1;
            width: 22px;
            min-width: 22px;
            margin-inline-start: 4px;
            pointer-events: auto;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.16) !important;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.035) !important;
            transition: border-color 160ms ease, background 160ms ease;
          }
          .ai-template-number-input .ant-input-number-handler {
            height: 10px;
            color: rgba(255, 255, 255, 0.62);
            border-color: transparent !important;
            background: transparent !important;
            transition: color 160ms ease, background 160ms ease, transform 160ms ease;
          }
          .ai-template-number-input .ant-input-number-input {
            text-align: left !important;
            padding-inline-start: 0 !important;
          }
          .ai-template-number-input:hover .ant-input-number-handler,
          .ai-template-number-input:focus-within .ant-input-number-handler {
            color: rgba(255, 255, 255, 0.9);
          }
          .ai-template-number-input:hover .ant-input-number-handler-wrap,
          .ai-template-number-input:focus-within .ant-input-number-handler-wrap {
            border-color: rgba(255, 255, 255, 0.3) !important;
            background: rgba(255, 255, 255, 0.08) !important;
          }
          .ai-template-number-input .ant-input-number-handler:hover {
            color: rgba(255, 255, 255, 1);
            background: rgba(255, 255, 255, 0.1) !important;
          }
          .ai-template-field-value .ant-select-single.ant-select-sm .ant-select-selector,
          .ai-template-field-value .ant-select-single .ant-select-selector {
            display: flex;
            align-items: center;
          }
          .ai-template-field-value .ant-select-selection-item,
          .ai-template-field-value .ant-select-selection-placeholder {
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
            font-size: 12px;
            line-height: 22px !important;
          }
          .ai-template-field-value .ant-select-selector {
            border: 1px solid rgba(255, 255, 255, 0.14) !important;
            border-radius: 4px !important;
            background: rgba(255, 255, 255, 0.025) !important;
            padding-inline: 6px !important;
          }
          .ai-template-field-value .ant-select:hover .ant-select-selector,
          .ai-template-field-value .ant-select-focused .ant-select-selector {
            border-color: rgba(255, 255, 255, 0.3) !important;
            box-shadow: none !important;
          }
          .ai-template-field-value .ant-select-arrow {
            color: rgba(255, 255, 255, 0.45);
          }
          .ai-template-field-value.is-rich .ant-input-textarea textarea {
            min-height: 70px !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            border-radius: 10px !important;
            background: rgba(13, 16, 22, 0.24) !important;
            line-height: 1.6 !important;
            overflow-y: hidden !important;
            resize: none;
          }
          .ai-template-site-description textarea {
            min-height: 160px !important;
            max-height: none !important;
          }
          .ai-template-boolean-control {
            min-height: 22px;
            display: inline-flex;
            align-items: center;
          }
          .ai-template-bool-switch.ant-switch {
            min-width: 36px;
            height: 20px;
            padding: 1px;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            box-shadow: none;
            transition: background 160ms ease, border-color 160ms ease;
          }
          .ai-template-bool-switch.ant-switch .ant-switch-handle {
            top: 1px;
            inset-inline-start: 1px;
            width: 16px;
            height: 16px;
          }
          .ai-template-bool-switch.ant-switch .ant-switch-handle::before {
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: none;
          }
          .ai-template-bool-switch.ant-switch.ant-switch-checked {
            background: rgba(129, 216, 208, 0.52);
            border-color: rgba(129, 216, 208, 0.68);
          }
          .ai-template-bool-switch.ant-switch.ant-switch-checked .ant-switch-handle::before {
            background: linear-gradient(180deg, rgba(186, 255, 247, 0.98), rgba(129, 216, 208, 0.92));
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
          @keyframes aiReleaseToTemplate {
            0% {
              opacity: 1;
              filter: blur(0);
              transform: translate3d(0, 0, 0) scale(1);
            }
            48% {
              opacity: 0.96;
              filter: blur(0);
              transform: translate3d(0, 0, 0) scale(0.72);
            }
            100% {
              opacity: 0.08;
              filter: blur(1px);
              transform: translate3d(var(--release-exit-x), var(--release-exit-y), 0) scale(var(--release-exit-scale));
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
              filter: blur(10px);
              transform: translateY(-132px) scale(1.08);
            }
            to {
              opacity: 1;
              filter: blur(0);
              transform: translateY(0) scale(1);
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
          @keyframes ai-session-step-attention {
            0%, 100% {
              background: rgba(194, 200, 210, 0.34);
              box-shadow: 0 0 0 rgba(235, 241, 255, 0);
              opacity: 0.74;
            }
            50% {
              background: rgba(246, 247, 251, 0.9);
              box-shadow: 0 0 14px rgba(246, 247, 251, 0.28);
              opacity: 1;
            }
          }
          @keyframes ai-session-template-tab-enter {
            0% {
              opacity: 0;
              transform: translate3d(14px, 8px, 0) scale(1.72);
            }
            58% {
              opacity: 1;
              transform: translate3d(-2px, -1px, 0) scale(0.96);
            }
            100% {
              opacity: 1;
              transform: translate3d(0, 0, 0) scale(1);
            }
          }
          @keyframes ai-session-panel-restore {
            0% {
              opacity: 0.38;
              transform: translate3d(-22px, -12px, 0) scale(0.76);
            }
            55% {
              opacity: 1;
              transform: translate3d(1px, 0, 0) scale(1.02);
            }
            100% {
              opacity: 1;
              transform: translate3d(0, 0, 0) scale(1);
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
              padding-left: 0;
            }
            .ai-session-stage-card {
              width: 236px;
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
            .ai-session-layout.has-inspector.is-inspector-expanded {
              max-width: 100%;
              gap: 0;
              padding-right: 0;
            }
            .ai-session-layout.has-inspector.is-inspector-expanded .ai-session-template-frame {
              width: min(100%, 850px);
              max-width: 850px;
              flex-basis: 850px;
              margin: 0 auto;
            }
            .ai-session-inspector-divider,
            .ai-session-inspector-shell {
              display: none;
            }
          }
          @media (max-width: 767px) {
            .ai-collect-workbench {
              --ai-session-prompt-bottom: 12px;
              --ai-session-prompt-height: 86px;
              --ai-session-template-tail-gap: calc(var(--ai-session-prompt-height) + 28px);
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
            .ai-session-main-shell {
              gap: 10px;
            }
            .ai-session-stage-float {
              display: none;
            }
            .ai-session-template-shell {
              gap: 10px;
            }
            .ai-session-fixed-meta {
              gap: 8px;
              padding: 0 0 6px;
            }
            .ai-session-status-line {
              height: 24px;
              grid-template-columns: 18px minmax(0, 1fr);
              padding-right: 0;
            }
            .ai-session-inspector-divider,
            .ai-session-inspector-shell {
              display: none;
            }
            .ai-session-status-meta {
              display: none;
            }
            .ai-session-fixed-title-row {
              align-items: center;
              gap: 8px;
            }
            .ai-session-fixed-title-row h2 {
              font-size: 16px;
            }
            .ai-session-template-scroll,
            .ai-session-adapter-scroll,
            .ai-session-release-scroll {
              width: 100%;
              padding-top: 2px;
              border-radius: 14px;
            }
            .ai-session-adapter-shell,
            .ai-session-release-shell {
              width: 100%;
              max-width: none;
              padding: 18px 14px;
              border-radius: 14px;
            }
            .ai-session-adapter-overview,
            .ai-session-release-footer {
              flex-direction: column;
              align-items: flex-start;
            }
            .ai-session-adapter-progress {
              width: 100%;
              align-items: flex-start;
            }
            .ai-session-adapter-grid {
              grid-template-columns: 1fr;
            }
            .ai-session-task-scheduler-row {
              align-items: stretch;
              flex-direction: column;
            }
            .ai-session-task-concurrency-control {
              width: 100%;
            }
            .ai-session-task-schedule-options .ai-session-task-control {
              width: 100%;
            }
            .ai-session-task-policies {
              grid-template-columns: 1fr;
            }
            .ai-session-task-param-grid .ai-session-task-control,
            .ai-session-task-param-grid .ai-session-task-control.is-compact,
            .ai-session-task-param-grid .ai-session-task-control.is-long,
            .ai-session-task-batch-params-body .ai-session-task-control.is-file,
            .ai-session-task-batch-details .ai-session-task-control.is-binding {
              width: 100%;
            }
            .ai-session-task-batch-params-body,
            .ai-session-task-batch-details {
              align-items: stretch;
              flex-direction: column;
            }
            .ai-session-task-batch-number-grid {
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .ai-session-task-batch-number-grid .ai-session-task-control {
              width: auto;
            }
            .ai-template-sheet {
              width: 100%;
              max-width: none;
              padding: 18px 14px 18px;
              border-radius: 14px;
            }
            .ai-template-confirm-bar {
              align-items: stretch;
              flex-direction: column;
            }
            .ai-template-field {
              grid-template-columns: 1fr;
              gap: 8px;
              padding-left: 0;
            }
            .ai-session-template-divider {
              width: calc(100% + 44px);
            }
            .ai-session-prompt {
              bottom: var(--ai-session-prompt-bottom);
              width: calc(100% - 24px);
            }
            .ai-session-prompt-main {
              grid-template-columns: 20px minmax(0, 1fr) 28px 28px;
              min-height: 52px;
              border-radius: 14px;
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
            <div
              className={`ai-session-shell ${releaseExit ? 'is-releasing' : ''}`}
              style={releaseExit ? ({
                '--release-exit-x': `${releaseExit.x}px`,
                '--release-exit-y': `${releaseExit.y}px`,
                '--release-exit-scale': releaseExit.scale,
              } as React.CSSProperties) : undefined}
            >
              {renderWorkflowLayout()}
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
        <WorkspaceDock
          activePanel={activeWorkspacePanel}
          sessionActive={hasSession}
          onToggle={handleWorkspacePanelToggle}
          onClose={handleWorkspacePanelClose}
          analysisTemplate={{ yaml: workspaceTemplateYaml || activeTemplate.raw, adapter: adapterFileName }}
          onTemplateApply={handleWorkspaceTemplateApply}
          releaseTaskDefaults={{
            concurrency,
            respectRobots,
            driftGuard: enableDriftGuard,
            params: releaseTemplateParams,
            batch: releaseBatchConfig,
          }}
        />
      </div>
    </ErrorBoundary>
  );
};

export default AICollect;

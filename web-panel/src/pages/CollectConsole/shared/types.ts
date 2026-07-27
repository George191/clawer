// 共享类型定义 — 智能采集工作台

export type TaskStatus = 'running' | 'queued' | 'completed' | 'failed' | 'paused';
export type TaskGroup = 'prototype' | 'beta' | 'launch';
export type GroupDimension = 'stage' | 'template' | 'status';

export type TemplateStatus = 'active' | 'draft' | 'deprecated';
export type TemplateIcon = 'cloud' | 'tool' | 'api' | 'code' | 'search' | 'branch';

export interface TemplateAsset {
  key: string;
  name: string;
  title: string;
  domain: string;
  adapter: string;
  version: string;
  status: TemplateStatus;
  fields: number;
  quality: number;
  lastRun: string;
  owner: string;
  description: string;
  action: string;
  icon: TemplateIcon;
  taskCount: number;
  faviconUrl?: string;
  dataType?: string;
  templateUrl?: string;
  templatePath?: string;
}

export interface CollectTask {
  key: string;
  name: string;
  template: string;
  group: TaskGroup;
  area: string;
  status: TaskStatus;
  progress: number;
  records: string;
  lag: string;
  nextRun: string;
  owner: string;
  avatar: string;
  comments: string[];
  subIssues: Array<{ title: string; status: TaskStatus; id: string }>;
}

export type ViewTab = 'templates' | 'tasks';

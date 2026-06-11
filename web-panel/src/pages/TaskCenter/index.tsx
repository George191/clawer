import React, { useMemo, useState } from 'react';
import { Avatar, Button, Drawer, Input, Progress, Segmented, Space, Tag, Typography } from 'antd';
import {
  AppstoreOutlined,
  BarChartOutlined,
  CaretDownOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  FieldTimeOutlined,
  FileTextOutlined,
  MoreOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  SearchOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

type TaskStatus = 'running' | 'queued' | 'completed' | 'failed' | 'paused';
type TaskGroup = 'prototype' | 'beta' | 'launch';

interface CollectTask {
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

const aura = {
  bg: '#15191F',
  surface: '#20252D',
  surfaceSoft: '#171C22',
  row: '#222831',
  rowAlt: '#1F252D',
  border: '#303844',
  borderSoft: '#29313B',
  text: '#F3F4F6',
  muted: '#9AA3AF',
  subtle: '#697280',
  purple: '#8B5CF6',
  green: '#31D26B',
  danger: '#F87171',
};

const tasks: CollectTask[] = [
  {
    key: '1',
    name: 'Google Patent daily sync',
    template: 'google_patent_contract@v1.8',
    group: 'prototype',
    area: 'Patents 🧭',
    status: 'completed',
    progress: 85,
    records: '18.2K',
    lag: '42s',
    nextRun: '持续运行',
    owner: 'lucid-jellybean',
    avatar: 'LJ',
    comments: ['页面结构稳定，列表与详情链路已进入持续同步。', '附件下载成功率 98.7%，失败样本进入补偿队列。'],
    subIssues: [
      { title: '详情页 canonical URL 校验', status: 'completed', id: '#1752' },
      { title: 'PDF 附件断点续采', status: 'running', id: '#1753' },
      { title: '字段漂移基线固化', status: 'queued', id: '#1754' },
    ],
  },
  {
    key: '2',
    name: 'Sealagom navwarn sync',
    template: 'sealagom_navwarn_contract@v2.1',
    group: 'prototype',
    area: 'Navwarn 🌊',
    status: 'running',
    progress: 54,
    records: '4.6K',
    lag: '18s',
    nextRun: '持续运行',
    owner: 'aiden-garcia',
    avatar: 'AG',
    comments: ['Socket 正在推送批次进度，最近 10 分钟无失败记录。'],
    subIssues: [
      { title: '区域编码映射', status: 'running', id: '#1761' },
      { title: '增量窗口缩短到 10 分钟', status: 'queued', id: '#1762' },
      { title: '重复航警合并', status: 'completed', id: '#1763' },
    ],
  },
  {
    key: '3',
    name: 'ZDOpen notice probe',
    template: 'zdopen_notice_contract@v0.9',
    group: 'prototype',
    area: 'Gov notice 🧾',
    status: 'failed',
    progress: 24,
    records: '860',
    lag: 'blocked',
    nextRun: '等待恢复',
    owner: 'amere-jess',
    avatar: 'AJ',
    comments: ['验证码风险升高，已触发降速和身份池切换。'],
    subIssues: [
      { title: '验证码风险熔断', status: 'failed', id: '#1771' },
      { title: '低速代理池验证', status: 'queued', id: '#1772' },
    ],
  },
  {
    key: '4',
    name: 'PDF document extract',
    template: 'pdf_document_extract@v1.3',
    group: 'beta',
    area: 'Documents 📄',
    status: 'queued',
    progress: 0,
    records: '0',
    lag: '-',
    nextRun: '18:30',
    owner: 'dareal-daryl',
    avatar: 'DD',
    comments: ['等待浏览器渲染池空闲后启动。'],
    subIssues: [
      { title: 'Docling fallback', status: 'queued', id: '#1781' },
      { title: '对象存储路径归档', status: 'queued', id: '#1782' },
    ],
  },
  {
    key: '5',
    name: 'Quality missing scan',
    template: 'quality_missing_scan@v1.1',
    group: 'beta',
    area: 'Quality ✅',
    status: 'paused',
    progress: 10,
    records: '1.1K',
    lag: 'manual',
    nextRun: '人工确认',
    owner: 'nova-blaster',
    avatar: 'NB',
    comments: ['abstract 缺失率超过 1%，等待字段合约复核。'],
    subIssues: [
      { title: '字段映射复核', status: 'paused', id: '#1791' },
      { title: '缺失样本回放', status: 'queued', id: '#1792' },
    ],
  },
  {
    key: '6',
    name: 'Market data launch backfill',
    template: 'market_data_contract@v1.0',
    group: 'launch',
    area: 'Market 🚀',
    status: 'running',
    progress: 33,
    records: '7.4K',
    lag: '1m 12s',
    nextRun: '持续运行',
    owner: 'exactly-myra',
    avatar: 'EM',
    comments: ['发布前回填任务，完成后会锁定模板版本。'],
    subIssues: [
      { title: '历史 URL 队列导入', status: 'completed', id: '#1801' },
      { title: 'RDS 写入校验', status: 'running', id: '#1802' },
    ],
  },
];

const groupMeta: Record<TaskGroup, { title: string; icon: string }> = {
  prototype: { title: 'Prototype', icon: '🧪' },
  beta: { title: 'Beta', icon: '🌱' },
  launch: { title: 'Launch', icon: '🚀' },
};

const statusMeta: Record<TaskStatus, { label: string; className: string; icon: React.ReactNode }> = {
  running: { label: 'Running', className: 'is-running', icon: <SyncOutlined /> },
  queued: { label: 'Not Started', className: 'is-queued', icon: <ClockCircleOutlined /> },
  completed: { label: 'Complete', className: 'is-completed', icon: <CheckCircleOutlined /> },
  failed: { label: 'Behind', className: 'is-failed', icon: <CloseCircleOutlined /> },
  paused: { label: 'Paused', className: 'is-paused', icon: <PauseCircleOutlined /> },
};

const groupOrder: TaskGroup[] = ['prototype', 'beta', 'launch'];

const TaskCenter: React.FC = () => {
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<string>('all');
  const [selectedTask, setSelectedTask] = useState<CollectTask | null>(null);

  const filtered = useMemo(() => tasks.filter((task) => {
    const matchStatus = status === 'all' || task.status === status;
    const matchKeyword = !keyword || `${task.name} ${task.template} ${task.area}`.toLowerCase().includes(keyword.toLowerCase());
    return matchStatus && matchKeyword;
  }), [keyword, status]);

  const grouped = groupOrder.map((group) => ({
    group,
    rows: filtered.filter((task) => task.group === group),
  })).filter((item) => item.rows.length > 0);

  const renderStatusPill = (taskStatus: TaskStatus) => {
    const meta = statusMeta[taskStatus];
    return <span className={`task-pill ${meta.className}`}>{meta.icon}{meta.label}</span>;
  };

  return (
    <ErrorBoundary>
      <style>
        {`
          .task-board {
            height: calc(100vh - 100px);
            max-height: calc(100vh - 100px);
            overflow: hidden;
            background: ${aura.bg};
            color: ${aura.text};
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            border: 1px solid ${aura.borderSoft};
          }
          .task-board,
          .task-board * {
            scrollbar-width: none;
          }
          .task-board *::-webkit-scrollbar {
            display: none;
          }
          .task-board-header {
            flex-shrink: 0;
            padding: 14px 18px 0;
            border-bottom: 1px solid ${aura.border};
          }
          .task-board-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .task-board-title h1 {
            color: ${aura.text};
            font-size: 28px;
            line-height: 1.2;
            font-weight: 700;
            margin: 0;
          }
          .task-board-actions {
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .task-board .ant-btn,
          .task-board .ant-input-affix-wrapper {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.text};
            box-shadow: none;
          }
          .task-board .ant-input {
            background: transparent;
            color: ${aura.text};
          }
          .task-board .ant-segmented {
            background: transparent;
            padding: 0;
          }
          .task-board .ant-segmented-item {
            color: ${aura.muted};
            border-radius: 6px;
          }
          .task-board .ant-segmented-item-selected {
            background: ${aura.surface};
            color: ${aura.text};
            box-shadow: inset 0 0 0 1px ${aura.border};
          }
          .task-view-tabs {
            display: flex;
            align-items: center;
            gap: 22px;
            margin-top: 18px;
          }
          .task-view-tab {
            color: ${aura.muted};
            padding: 12px 0;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border-bottom: 2px solid transparent;
            font-weight: 600;
          }
          .task-view-tab.is-active {
            color: ${aura.text};
            border-bottom-color: ${aura.purple};
          }
          .task-board-main {
            flex: 1;
            min-height: 0;
            overflow: auto;
          }
          .task-grid {
            min-width: 1180px;
          }
          .task-grid-head,
          .task-row {
            display: grid;
            grid-template-columns: 40px minmax(360px, 1.8fr) 190px 180px 220px 280px 70px;
            align-items: center;
          }
          .task-grid-head {
            height: 42px;
            color: ${aura.muted};
            background: ${aura.surface};
            border-bottom: 1px solid ${aura.border};
            font-weight: 700;
          }
          .task-grid-cell {
            min-height: 42px;
            padding: 0 14px;
            border-right: 1px solid ${aura.border};
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
          }
          .task-grid-cell:last-child {
            border-right: none;
          }
          .task-group {
            border-bottom: 1px solid ${aura.border};
          }
          .task-group-title {
            height: 72px;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 0 18px;
            color: ${aura.text};
            font-size: 22px;
            font-weight: 800;
            background: ${aura.surfaceSoft};
            border-bottom: 1px solid ${aura.border};
          }
          .task-row {
            min-height: 62px;
            background: ${aura.row};
            border-bottom: 1px solid ${aura.border};
            cursor: pointer;
          }
          .task-row:hover {
            background: #27303A;
          }
          .task-index {
            color: ${aura.muted};
            justify-content: center;
          }
          .task-name {
            color: ${aura.text};
            font-size: 16px;
            font-weight: 650;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .task-template {
            color: ${aura.muted};
            display: block;
            font-size: 12px;
            margin-top: 2px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .task-pill {
            height: 30px;
            padding: 0 12px;
            border-radius: 16px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: 1px solid ${aura.border};
            color: ${aura.muted};
            font-weight: 700;
            white-space: nowrap;
          }
          .task-pill.is-running { color: ${aura.purple}; }
          .task-pill.is-completed { color: ${aura.green}; }
          .task-pill.is-failed { color: ${aura.danger}; }
          .task-pill.is-paused { color: #FBBF24; }
          .task-progress {
            flex: 1;
            height: 10px;
            background: #3A3150;
            border-radius: 999px;
            overflow: hidden;
          }
          .task-progress > span {
            display: block;
            height: 100%;
            background: ${aura.purple};
          }
          .task-owner {
            display: flex;
            align-items: center;
            gap: 9px;
            color: ${aura.muted};
            min-width: 0;
          }
          .task-owner span {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .task-detail .ant-drawer-content {
            background: ${aura.bg};
            color: ${aura.text};
          }
          .task-detail .ant-drawer-header {
            background: ${aura.bg};
            border-bottom-color: ${aura.border};
          }
          .task-detail .ant-drawer-title,
          .task-detail .ant-drawer-close {
            color: ${aura.text};
          }
          .task-detail-body {
            color: ${aura.text};
          }
          .task-detail-title {
            color: ${aura.text};
            font-size: 32px;
            line-height: 1.2;
            font-weight: 500;
            margin-bottom: 14px;
          }
          .task-detail-card {
            border: 1px solid ${aura.border};
            border-radius: 8px;
            background: ${aura.surfaceSoft};
            margin-top: 16px;
            overflow: hidden;
          }
          .task-comment-head {
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            border-bottom: 1px solid ${aura.border};
            color: ${aura.muted};
          }
          .task-comment-body {
            padding: 18px 16px;
          }
          .task-comment-body h2 {
            color: ${aura.text};
            font-size: 24px;
            margin: 0 0 12px;
          }
          .task-comment-body p {
            color: ${aura.muted};
            line-height: 1.6;
            margin: 0;
          }
          .task-subissue-row {
            height: 52px;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 0 16px;
            border-top: 1px solid ${aura.border};
            color: ${aura.text};
          }
          .task-detail-meta {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 18px;
          }
          .task-detail .ant-tag {
            border-color: ${aura.border};
            background: transparent;
            color: ${aura.muted};
          }
        `}
      </style>

      <div className="task-board">
        <header className="task-board-header">
          <div className="task-board-title">
            <Space size={10}>
              <DatabaseOutlined style={{ color: aura.purple, fontSize: 20 }} />
              <h1>采集任务</h1>
            </Space>
            <div className="task-board-actions">
              <Input
                prefix={<SearchOutlined />}
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索任务 / 模板 / 领域"
                style={{ width: 260 }}
              />
              <Segmented
                value={status}
                onChange={(value) => setStatus(String(value))}
                options={[
                  { label: '全部', value: 'all' },
                  { label: '运行', value: 'running' },
                  { label: '队列', value: 'queued' },
                  { label: '失败', value: 'failed' },
                ]}
              />
              <Button icon={<BarChartOutlined />} />
              <Button icon={<PlusOutlined />}>新建任务</Button>
            </div>
          </div>

          <nav className="task-view-tabs">
            <span className="task-view-tab is-active"><AppstoreOutlined /> 任务计划</span>
            <span className="task-view-tab"><FieldTimeOutlined /> 按调度窗口</span>
            <span className="task-view-tab"><FileTextOutlined /> 运行日志</span>
            <span className="task-view-tab"><PlusOutlined /> 新视图</span>
          </nav>
        </header>

        <main className="task-board-main">
          <div className="task-grid">
            <div className="task-grid-head">
              <div className="task-grid-cell" />
              <div className="task-grid-cell">Title</div>
              <div className="task-grid-cell">Area</div>
              <div className="task-grid-cell">Status</div>
              <div className="task-grid-cell">Assignee</div>
              <div className="task-grid-cell">Sub-issue progress</div>
              <div className="task-grid-cell"><PlusOutlined /></div>
            </div>

            {grouped.map(({ group, rows }) => (
              <section className="task-group" key={group}>
                <div className="task-group-title">
                  <CaretDownOutlined style={{ color: aura.muted, fontSize: 14 }} />
                  <span>{groupMeta[group].title} {groupMeta[group].icon}</span>
                  <Tag>{rows.length}</Tag>
                </div>
                {rows.map((task, index) => (
                  <div className="task-row" key={task.key} onClick={() => setSelectedTask(task)}>
                    <div className="task-grid-cell task-index">{index + 1}</div>
                    <div className="task-grid-cell">
                      <CheckCircleOutlined style={{ color: task.status === 'completed' ? aura.purple : task.status === 'failed' ? aura.danger : aura.green }} />
                      <div style={{ minWidth: 0 }}>
                        <span className="task-name">{task.name}</span>
                        <span className="task-template">{task.template}</span>
                      </div>
                    </div>
                    <div className="task-grid-cell">
                      <span className="task-pill">{task.area}</span>
                    </div>
                    <div className="task-grid-cell">{renderStatusPill(task.status)}</div>
                    <div className="task-grid-cell">
                      <div className="task-owner">
                        <Avatar size={28} style={{ background: aura.border }}>{task.avatar}</Avatar>
                        <span>{task.owner}</span>
                      </div>
                    </div>
                    <div className="task-grid-cell">
                      <div className="task-progress"><span style={{ width: `${task.progress}%` }} /></div>
                      <Text style={{ color: aura.muted, width: 44, textAlign: 'right' }}>{task.progress}%</Text>
                    </div>
                    <div className="task-grid-cell"><MoreOutlined style={{ color: aura.muted }} /></div>
                  </div>
                ))}
                <div className="task-row" style={{ cursor: 'default' }}>
                  <div className="task-grid-cell" />
                  <div className="task-grid-cell" style={{ color: aura.muted }}><PlusOutlined /> Add item</div>
                  <div className="task-grid-cell" />
                  <div className="task-grid-cell" />
                  <div className="task-grid-cell" />
                  <div className="task-grid-cell" />
                  <div className="task-grid-cell" />
                </div>
              </section>
            ))}
          </div>
        </main>
      </div>

      <Drawer
        className="task-detail"
        width={760}
        open={Boolean(selectedTask)}
        onClose={() => setSelectedTask(null)}
        title={selectedTask ? `${selectedTask.name} #${920 + Number(selectedTask.key)}` : ''}
        footer={null}
      >
        {selectedTask && (
          <div className="task-detail-body">
            <div className="task-detail-title">{selectedTask.name} <span style={{ color: aura.subtle }}>#{920 + Number(selectedTask.key)}</span></div>
            <div className="task-detail-meta">
              {renderStatusPill(selectedTask.status)}
              <span className="task-pill">{selectedTask.subIssues.filter((issue) => issue.status === 'completed').length} / {selectedTask.subIssues.length} sub-issues</span>
              <Tag>{selectedTask.template}</Tag>
              <Tag>{selectedTask.nextRun}</Tag>
            </div>

            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <Avatar size={48} style={{ background: aura.border }}>{selectedTask.avatar}</Avatar>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="task-detail-card">
                  <div className="task-comment-head">
                    <span><strong style={{ color: aura.text }}>{selectedTask.owner}</strong> commented 5 minutes ago</span>
                    <MoreOutlined />
                  </div>
                  <div className="task-comment-body">
                    <h2>任务说明</h2>
                    <p>{selectedTask.comments[0]}</p>
                    <div style={{ marginTop: 16 }}>
                      <Progress percent={selectedTask.progress} strokeColor={aura.purple} trailColor="#3A3150" />
                    </div>
                  </div>
                </div>

                <div className="task-detail-card">
                  <div className="task-comment-head" style={{ justifyContent: 'flex-start', gap: 8 }}>
                    <CaretDownOutlined />
                    <strong style={{ color: aura.text }}>Sub-issues</strong>
                    <span className="task-pill">{selectedTask.subIssues.filter((issue) => issue.status === 'completed').length} / {selectedTask.subIssues.length}</span>
                  </div>
                  {selectedTask.subIssues.map((issue) => (
                    <div className="task-subissue-row" key={issue.id}>
                      <CheckCircleOutlined style={{ color: issue.status === 'completed' ? aura.purple : issue.status === 'failed' ? aura.danger : aura.green }} />
                      <span>{issue.title}</span>
                      <span style={{ color: aura.subtle }}>{issue.id}</span>
                    </div>
                  ))}
                  <div className="task-subissue-row">
                    <Button>Create sub-issue</Button>
                  </div>
                </div>

                <div className="task-detail-card">
                  <div className="task-comment-body">
                    <h2>运行指标</h2>
                    <Space size={24} wrap>
                      <span><Text style={{ color: aura.muted }}>记录数</Text><strong style={{ display: 'block', color: aura.text }}>{selectedTask.records}</strong></span>
                      <span><Text style={{ color: aura.muted }}>延迟</Text><strong style={{ display: 'block', color: selectedTask.lag === 'blocked' ? aura.danger : aura.text }}>{selectedTask.lag}</strong></span>
                      <span><Text style={{ color: aura.muted }}>下次运行</Text><strong style={{ display: 'block', color: aura.text }}>{selectedTask.nextRun}</strong></span>
                    </Space>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </Drawer>
    </ErrorBoundary>
  );
};

export default TaskCenter;

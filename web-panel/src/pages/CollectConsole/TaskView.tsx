import React, { useMemo, useState } from 'react';
import { Avatar, Drawer, Progress, Segmented, Space, Tag, Typography } from 'antd';
import {
  AppstoreOutlined,
  BarChartOutlined,
  CaretDownOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FieldTimeOutlined,
  FileTextOutlined,
  MoreOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  SearchOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { aiAura } from './shared/aura';
import { tasks } from './shared/mockData';
import type { CollectTask, TaskStatus, TaskGroup } from './shared/types';

const { Text } = Typography;

const groupMeta: Record<TaskGroup, { title: string; icon: string }> = {
  prototype: { title: 'Prototype', icon: '🧪' },
  beta: { title: 'Beta', icon: '🌱' },
  launch: { title: 'Launch', icon: '🚀' },
};

const statusMeta: Record<TaskStatus, { label: string; className: string; icon: React.ReactNode }> = {
  running: { label: 'Running', className: 'is-running', icon: <SyncOutlined /> },
  queued: { label: 'Queued', className: 'is-queued', icon: <ClockCircleOutlined /> },
  completed: { label: 'Complete', className: 'is-completed', icon: <CheckCircleOutlined /> },
  failed: { label: 'Failed', className: 'is-failed', icon: <CloseCircleOutlined /> },
  paused: { label: 'Paused', className: 'is-paused', icon: <PauseCircleOutlined /> },
};

const groupOrder: TaskGroup[] = ['prototype', 'beta', 'launch'];

interface Props {
  keyword: string;
}

const TaskView: React.FC<Props> = ({ keyword }) => {
  const [status, setStatus] = useState<string>('all');
  const [selectedTask, setSelectedTask] = useState<CollectTask | null>(null);

  const filtered = useMemo(() => tasks.filter((t) => {
    const matchStatus = status === 'all' || t.status === status;
    const matchKeyword = !keyword ||
      `${t.name} ${t.template} ${t.area}`.toLowerCase().includes(keyword.toLowerCase());
    return matchStatus && matchKeyword;
  }), [keyword, status]);

  const grouped = groupOrder.map((g) => ({
    group: g,
    rows: filtered.filter((t) => t.group === g),
  })).filter((item) => item.rows.length > 0);

  const renderPill = (s: TaskStatus) => {
    const m = statusMeta[s];
    return <span className={`cc-task-pill ${m.className}`}>{m.icon}{m.label}</span>;
  };

  return (
    <>
      <style>{`
        .cc-task {
          flex: 1;
          min-height: 0;
          overflow: hidden;
          display: flex;
          flex-direction: column;
        }
        .cc-task-toolbar {
          flex-shrink: 0;
          padding: 12px 22px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border-bottom: 1px solid ${aiAura.borderSoft};
        }
        .cc-task-toolbar .ant-segmented {
          background: ${aiAura.surfaceSoft};
          border: 1px solid ${aiAura.borderSoft};
          padding: 2px;
        }
        .cc-task-toolbar .ant-segmented-item {
          color: ${aiAura.subtle};
          border-radius: 6px;
        }
        .cc-task-toolbar .ant-segmented-item-selected {
          background: ${aiAura.surface};
          color: ${aiAura.text};
          box-shadow: inset 0 0 0 1px ${aiAura.border};
        }
        .cc-task-main {
          flex: 1;
          min-height: 0;
          overflow: auto;
        }
        .cc-task-grid {
          min-width: 1100px;
        }
        .cc-task-grid-head,
        .cc-task-row {
          display: grid;
          grid-template-columns: 40px minmax(340px, 1.8fr) 170px 170px 210px 260px 60px;
          align-items: center;
        }
        .cc-task-grid-head {
          height: 40px;
          color: ${aiAura.muted};
          background: ${aiAura.surface};
          border-bottom: 1px solid ${aiAura.border};
          font-weight: 700;
          font-size: 13px;
        }
        .cc-task-grid-cell {
          min-height: 40px;
          padding: 0 14px;
          border-right: 1px solid ${aiAura.border};
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
        }
        .cc-task-grid-cell:last-child {
          border-right: none;
        }
        .cc-task-group {
          border-bottom: 1px solid ${aiAura.border};
        }
        .cc-task-group-title {
          height: 64px;
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 0 18px;
          color: ${aiAura.text};
          font-size: 20px;
          font-weight: 800;
          background: ${aiAura.surfaceSoft};
          border-bottom: 1px solid ${aiAura.border};
        }
        .cc-task-row {
          min-height: 58px;
          background: #1A1F1F;
          border-bottom: 1px solid ${aiAura.border};
          cursor: pointer;
        }
        .cc-task-row:hover {
          background: #222727;
        }
        .cc-task-index {
          color: ${aiAura.muted};
          justify-content: center;
        }
        .cc-task-name {
          color: ${aiAura.text};
          font-size: 15px;
          font-weight: 650;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .cc-task-template {
          color: ${aiAura.muted};
          display: block;
          font-size: 12px;
          margin-top: 2px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .cc-task-pill {
          height: 28px;
          padding: 0 11px;
          border-radius: 14px;
          display: inline-flex;
          align-items: center;
          gap: 5px;
          border: 1px solid ${aiAura.border};
          color: ${aiAura.muted};
          font-weight: 700;
          white-space: nowrap;
          font-size: 12px;
        }
        .cc-task-pill.is-running { color: ${aiAura.accent}; }
        .cc-task-pill.is-completed { color: #31D26B; }
        .cc-task-pill.is-failed { color: #F87171; }
        .cc-task-pill.is-paused { color: #FBBF24; }
        .cc-task-progress {
          flex: 1;
          height: 8px;
          background: #2D3535;
          border-radius: 999px;
          overflow: hidden;
        }
        .cc-task-progress > span {
          display: block;
          height: 100%;
          background: ${aiAura.accent};
        }
        .cc-task-owner {
          display: flex;
          align-items: center;
          gap: 8px;
          color: ${aiAura.muted};
          min-width: 0;
        }
        .cc-task-owner span {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        /* Drawer */
        .cc-task-detail .ant-drawer-content {
          background: ${aiAura.bg};
          color: ${aiAura.text};
        }
        .cc-task-detail .ant-drawer-header {
          background: ${aiAura.bg};
          border-bottom-color: ${aiAura.border};
        }
        .cc-task-detail .ant-drawer-title,
        .cc-task-detail .ant-drawer-close {
          color: ${aiAura.text};
        }
        .cc-task-detail-title {
          color: ${aiAura.text};
          font-size: 28px;
          line-height: 1.2;
          font-weight: 500;
          margin-bottom: 14px;
        }
        .cc-task-detail-card {
          border: 1px solid ${aiAura.border};
          border-radius: 8px;
          background: ${aiAura.surfaceSoft};
          margin-top: 14px;
          overflow: hidden;
        }
        .cc-task-comment-head {
          height: 44px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 16px;
          border-bottom: 1px solid ${aiAura.border};
          color: ${aiAura.muted};
        }
        .cc-task-comment-body {
          padding: 16px;
        }
        .cc-task-comment-body h2 {
          color: ${aiAura.text};
          font-size: 22px;
          margin: 0 0 10px;
        }
        .cc-task-comment-body p {
          color: ${aiAura.muted};
          line-height: 1.6;
          margin: 0;
        }
        .cc-task-subissue-row {
          height: 48px;
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 0 16px;
          border-top: 1px solid ${aiAura.border};
          color: ${aiAura.text};
        }
        .cc-task-detail-meta {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-bottom: 16px;
        }
        .cc-task-detail .ant-tag {
          border-color: ${aiAura.border};
          background: transparent;
          color: ${aiAura.muted};
        }
      `}</style>

      <div className="cc-task">
        <div className="cc-task-toolbar">
          <Segmented
            value={status}
            onChange={(v) => setStatus(String(v))}
            options={[
              { label: '全部', value: 'all' },
              { label: '运行', value: 'running' },
              { label: '队列', value: 'queued' },
              { label: '失败', value: 'failed' },
            ]}
          />
          <Space size={8}>
            <span style={{ color: aiAura.subtle, fontSize: 12 }}>
              <FieldTimeOutlined /> 按调度窗口
            </span>
            <span style={{ color: aiAura.subtle, fontSize: 12 }}>
              <FileTextOutlined /> 运行日志
            </span>
          </Space>
        </div>

        <div className="cc-task-main">
          <div className="cc-task-grid">
            <div className="cc-task-grid-head">
              <div className="cc-task-grid-cell" />
              <div className="cc-task-grid-cell">Title</div>
              <div className="cc-task-grid-cell">Area</div>
              <div className="cc-task-grid-cell">Status</div>
              <div className="cc-task-grid-cell">Assignee</div>
              <div className="cc-task-grid-cell">Sub-issue progress</div>
              <div className="cc-task-grid-cell"><PlusOutlined /></div>
            </div>

            {grouped.map(({ group, rows }) => (
              <section className="cc-task-group" key={group}>
                <div className="cc-task-group-title">
                  <CaretDownOutlined style={{ color: aiAura.muted, fontSize: 14 }} />
                  <span>{groupMeta[group].title} {groupMeta[group].icon}</span>
                  <Tag>{rows.length}</Tag>
                </div>
                {rows.map((task, index) => (
                  <div
                    className="cc-task-row"
                    key={task.key}
                    onClick={() => setSelectedTask(task)}
                  >
                    <div className="cc-task-grid-cell cc-task-index">{index + 1}</div>
                    <div className="cc-task-grid-cell">
                      <CheckCircleOutlined style={{
                        color: task.status === 'completed' ? aiAura.accent :
                               task.status === 'failed' ? '#F87171' : '#31D26B'
                      }} />
                      <div style={{ minWidth: 0 }}>
                        <span className="cc-task-name">{task.name}</span>
                        <span className="cc-task-template">{task.template}</span>
                      </div>
                    </div>
                    <div className="cc-task-grid-cell">
                      <span className="cc-task-pill">{task.area}</span>
                    </div>
                    <div className="cc-task-grid-cell">{renderPill(task.status)}</div>
                    <div className="cc-task-grid-cell">
                      <div className="cc-task-owner">
                        <Avatar size={26} style={{ background: aiAura.border, fontSize: 11 }}>
                          {task.avatar}
                        </Avatar>
                        <span>{task.owner}</span>
                      </div>
                    </div>
                    <div className="cc-task-grid-cell">
                      <div className="cc-task-progress">
                        <span style={{ width: `${task.progress}%` }} />
                      </div>
                      <Text style={{ color: aiAura.muted, width: 40, textAlign: 'right' }}>
                        {task.progress}%
                      </Text>
                    </div>
                    <div className="cc-task-grid-cell">
                      <MoreOutlined style={{ color: aiAura.muted }} />
                    </div>
                  </div>
                ))}
                <div className="cc-task-row" style={{ cursor: 'default' }}>
                  <div className="cc-task-grid-cell" />
                  <div className="cc-task-grid-cell" style={{ color: aiAura.muted }}>
                    <PlusOutlined /> Add item
                  </div>
                  <div className="cc-task-grid-cell" />
                  <div className="cc-task-grid-cell" />
                  <div className="cc-task-grid-cell" />
                  <div className="cc-task-grid-cell" />
                  <div className="cc-task-grid-cell" />
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>

      <Drawer
        className="cc-task-detail"
        width={720}
        open={Boolean(selectedTask)}
        onClose={() => setSelectedTask(null)}
        title={selectedTask ? `${selectedTask.name} #${920 + Number(selectedTask.key)}` : ''}
        footer={null}
      >
        {selectedTask && (
          <div>
            <div className="cc-task-detail-title">
              {selectedTask.name}{' '}
              <span style={{ color: aiAura.subtle }}>#{920 + Number(selectedTask.key)}</span>
            </div>
            <div className="cc-task-detail-meta">
              {renderPill(selectedTask.status)}
              <span className="cc-task-pill">
                {selectedTask.subIssues.filter((i) => i.status === 'completed').length} /{' '}
                {selectedTask.subIssues.length} sub-issues
              </span>
              <Tag>{selectedTask.template}</Tag>
              <Tag>{selectedTask.nextRun}</Tag>
            </div>

            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <Avatar size={44} style={{ background: aiAura.border }}>
                {selectedTask.avatar}
              </Avatar>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="cc-task-detail-card">
                  <div className="cc-task-comment-head">
                    <span>
                      <strong style={{ color: aiAura.text }}>{selectedTask.owner}</strong>
                      {' '}commented 5 minutes ago
                    </span>
                    <MoreOutlined />
                  </div>
                  <div className="cc-task-comment-body">
                    <h2>任务说明</h2>
                    <p>{selectedTask.comments[0]}</p>
                    <div style={{ marginTop: 14 }}>
                      <Progress
                        percent={selectedTask.progress}
                        strokeColor={aiAura.accent}
                        trailColor="#2D3535"
                      />
                    </div>
                  </div>
                </div>

                <div className="cc-task-detail-card">
                  <div className="cc-task-comment-head" style={{ justifyContent: 'flex-start', gap: 8 }}>
                    <CaretDownOutlined />
                    <strong style={{ color: aiAura.text }}>Sub-issues</strong>
                    <span className="cc-task-pill">
                      {selectedTask.subIssues.filter((i) => i.status === 'completed').length} /{' '}
                      {selectedTask.subIssues.length}
                    </span>
                  </div>
                  {selectedTask.subIssues.map((issue) => (
                    <div className="cc-task-subissue-row" key={issue.id}>
                      <CheckCircleOutlined style={{
                        color: issue.status === 'completed' ? aiAura.accent :
                               issue.status === 'failed' ? '#F87171' : '#31D26B'
                      }} />
                      <span>{issue.title}</span>
                      <span style={{ color: aiAura.subtle }}>{issue.id}</span>
                    </div>
                  ))}
                </div>

                <div className="cc-task-detail-card">
                  <div className="cc-task-comment-body">
                    <h2>运行指标</h2>
                    <Space size={24} wrap>
                      <span>
                        <Text style={{ color: aiAura.muted }}>记录数</Text>
                        <strong style={{ display: 'block', color: aiAura.text }}>
                          {selectedTask.records}
                        </strong>
                      </span>
                      <span>
                        <Text style={{ color: aiAura.muted }}>延迟</Text>
                        <strong style={{
                          display: 'block',
                          color: selectedTask.lag === 'blocked' ? '#F87171' : aiAura.text
                        }}>
                          {selectedTask.lag}
                        </strong>
                      </span>
                      <span>
                        <Text style={{ color: aiAura.muted }}>下次运行</Text>
                        <strong style={{ display: 'block', color: aiAura.text }}>
                          {selectedTask.nextRun}
                        </strong>
                      </span>
                    </Space>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </Drawer>
    </>
  );
};

export default TaskView;

import React, { useMemo, useState } from 'react';
import { Button, Input, Segmented, Space, Tag } from 'antd';
import {
  AppstoreOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  PlusOutlined,
  ProfileOutlined,
  RobotOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';
import TemplateView from './TemplateView';
import TaskView from './TaskView';
import CollectWizard from './CollectWizard';
import { aiAura } from './shared/aura';
import type { ViewTab } from './shared/types';

const CollectConsole: React.FC = () => {
  const path = window.location.pathname;
  const initialTab: ViewTab = path.includes('/tasks') ? 'tasks' : 'templates';
  const [activeTab, setActiveTab] = useState<ViewTab>(initialTab);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [keyword, setKeyword] = useState('');

  const headerRight = useMemo(() => (
    <Space size={8}>
      <Input
        prefix={<SearchOutlined />}
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        placeholder="搜索模板 / 任务 / 域名"
        style={{ width: 240 }}
      />
      <Button icon={<ExperimentOutlined />}>批量试跑</Button>
      <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setWizardOpen(true)}>
        智能采集
      </Button>
    </Space>
  ), [keyword]);

  return (
    <ErrorBoundary>
      <style>{`
        .collect-console {
          height: calc(100vh - 100px);
          max-height: calc(100vh - 100px);
          overflow: hidden;
          border-radius: 8px;
          border: 1px solid ${aiAura.border};
          background: ${aiAura.bg};
          color: ${aiAura.text};
          display: flex;
          flex-direction: column;
        }
        .collect-console,
        .collect-console * {
          scrollbar-width: none;
        }
        .collect-console *::-webkit-scrollbar {
          display: none;
        }
        /* Header */
        .cc-header {
          flex-shrink: 0;
          padding: 16px 22px 0;
          border-bottom: 1px solid ${aiAura.border};
        }
        .cc-header-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
        }
        .cc-heading {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
        }
        .cc-heading h1 {
          margin: 0;
          color: ${aiAura.text};
          font-size: 24px;
          line-height: 1.2;
          font-weight: 720;
        }
        .cc-heading .anticon {
          color: ${aiAura.accent};
          font-size: 22px;
        }
        .cc-header-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          justify-content: flex-end;
        }
        /* View Tabs */
        .cc-tabs {
          display: flex;
          align-items: center;
          gap: 0;
          margin-top: 14px;
        }
        .cc-tab {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 10px 18px 12px;
          color: ${aiAura.muted};
          font-size: 14px;
          font-weight: 600;
          border-bottom: 2px solid transparent;
          cursor: pointer;
          transition: color 160ms ease, border-color 160ms ease;
          white-space: nowrap;
        }
        .cc-tab:hover {
          color: ${aiAura.text};
        }
        .cc-tab.is-active {
          color: ${aiAura.accent};
          border-bottom-color: ${aiAura.accent};
        }
        .cc-tab .anticon {
          font-size: 16px;
        }
        .cc-tab-count {
          font-size: 11px;
          color: ${aiAura.subtle};
          margin-left: 2px;
        }
        /* Body */
        .cc-body {
          flex: 1;
          min-height: 0;
          overflow: hidden;
          display: flex;
          flex-direction: column;
        }
        /* Status bar */
        .cc-status-bar {
          flex-shrink: 0;
          height: 32px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 18px;
          border-top: 1px solid ${aiAura.border};
          background: ${aiAura.surfaceSoft};
          font-size: 12px;
          color: ${aiAura.subtle};
        }
        .cc-status-left,
        .cc-status-right {
          display: flex;
          align-items: center;
          gap: 14px;
        }
        .cc-status-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          display: inline-block;
          margin-right: 4px;
        }
        /* Ant overrides */
        .collect-console .ant-btn,
        .collect-console .ant-input-affix-wrapper {
          background: transparent;
          border-color: ${aiAura.border};
          color: ${aiAura.text};
          box-shadow: none;
        }
        .collect-console .ant-input {
          background: transparent;
          color: ${aiAura.text};
        }
        .collect-console .ant-input::placeholder {
          color: ${aiAura.subtle};
        }
        .collect-console .ant-btn-primary {
          background: ${aiAura.accent};
          border-color: ${aiAura.accent};
          color: ${aiAura.bg};
        }
        .collect-console .ant-btn-primary:hover {
          opacity: 0.88;
        }
        .collect-console .ant-tag {
          background: transparent;
          border-color: ${aiAura.border};
          color: ${aiAura.subtle};
          margin: 0;
        }
      `}</style>

      <div className="collect-console">
        {/* Header */}
        <header className="cc-header">
          <div className="cc-header-top">
            <div className="cc-heading">
              <RobotOutlined />
              <h1>智能采集工作台</h1>
              <Tag>beta</Tag>
            </div>
            <div className="cc-header-actions">{headerRight}</div>
          </div>

          {/* View Tabs */}
          <nav className="cc-tabs">
            <span
              className={`cc-tab ${activeTab === 'templates' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('templates')}
            >
              <ProfileOutlined />
              模板库
              <span className="cc-tab-count">6</span>
            </span>
            <span
              className={`cc-tab ${activeTab === 'tasks' ? 'is-active' : ''}`}
              onClick={() => setActiveTab('tasks')}
            >
              <AppstoreOutlined />
              采集任务
              <span className="cc-tab-count">6</span>
            </span>
          </nav>
        </header>

        {/* Body */}
        <main className="cc-body">
          {activeTab === 'templates' ? (
            <TemplateView keyword={keyword} />
          ) : (
            <TaskView keyword={keyword} />
          )}
        </main>

        {/* Status Bar */}
        <footer className="cc-status-bar">
          <div className="cc-status-left">
            <span>
              <span className="cc-status-dot" style={{ background: aiAura.accent }} />
              引擎在线
            </span>
            <span>调度器: 3 运行 / 2 队列</span>
            <span>代理池: 47 可用</span>
          </div>
          <div className="cc-status-right">
            <span>WebSocket: 已连接</span>
            <span>v2.1.0-beta</span>
          </div>
        </footer>
      </div>

      {/* Collect Wizard Overlay */}
      <CollectWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
    </ErrorBoundary>
  );
};

export default CollectConsole;

import React, { useEffect, useState, useCallback } from 'react';
import { Row, Col, Select, DatePicker, Space, Button, Tabs, Segmented, App, Result, Empty, Spin } from 'antd';
import {
  PlusOutlined,
  ReloadOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import { useTasksStore } from '@/stores/tasks';
import { usePolling } from '@/hooks/usePolling';
import type { TaskInfo } from '@/services/types';
import TaskCard from './TaskCard';
import TaskDrawer from './TaskDrawer';

const STATUS_TABS = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
  { key: 'queued', label: '队列中' },
];

const TEMPLATE_OPTIONS = [
  { value: '网页采集', label: '网页采集' },
  { value: 'API采集', label: 'API采集' },
  { value: '日志采集', label: '日志采集' },
  { value: '数据清洗', label: '数据清洗' },
  { value: '消息采集', label: '消息采集' },
];

const TaskCenter: React.FC = () => {
  const { tasks, loading, error, fetchTasks, fetchTemplates, deleteTask, runTask } = useTasksStore();
  const [statusFilter, setStatusFilter] = useState('all');
  const [templateFilter, setTemplateFilter] = useState<string | undefined>();
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [hasLoaded, setHasLoaded] = useState(false);
  const { message } = App.useApp();

  // Polling 10s
  usePolling(useCallback(() => { fetchTasks(); }, [fetchTasks]), 10_000, true);

  useEffect(() => {
    Promise.all([fetchTasks(), fetchTemplates()]).finally(() => setHasLoaded(true));
  }, [fetchTasks, fetchTemplates]);

  // Filter
  const filtered = tasks.filter((t: TaskInfo) => {
    if (statusFilter !== 'all' && t.status !== statusFilter) return false;
    if (templateFilter && t.template !== templateFilter) return false;
    if (dateRange) {
      const [start, end] = dateRange;
      if (start && t.startedAt < start) return false;
      if (end && t.startedAt > end) return false;
    }
    return true;
  });

  const handleDelete = async (id: string) => {
    try {
      await deleteTask(id);
      message.success('任务已删除');
    } catch {
      message.error('删除失败');
    }
  };

  const handleRetry = async (id: string) => {
    try {
      await runTask(id);
      message.success('任务已重新启动');
    } catch {
      message.error('操作失败');
    }
  };

  // ── Error state ──
  if (error && tasks.length === 0) {
    return (
      <ErrorBoundary>
        <PageHeader title="任务中心" />
        <Result
          status="error"
          title="加载失败"
          subTitle={error}
          extra={<Button onClick={() => { fetchTasks(); fetchTemplates(); }}>重试</Button>}
        />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <PageHeader
        title="任务中心"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchTasks} loading={loading}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setDrawerOpen(true)}>新建任务</Button>
          </Space>
        }
      />

      {/* Filter Bar */}
      <div
        className="glass-card"
        style={{
          marginBottom: 16, padding: '12px 18px',
          display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', flexWrap: 'wrap', gap: 12,
        }}
      >
        <Space wrap size={12}>
          <Tabs
            activeKey={statusFilter}
            onChange={setStatusFilter}
            items={STATUS_TABS}
            size="small"
            style={{ marginBottom: 0 }}
          />
          <Select
            allowClear placeholder="选择模板" style={{ width: 140 }} size="small"
            value={templateFilter} onChange={setTemplateFilter}
            options={TEMPLATE_OPTIONS}
          />
          <DatePicker.RangePicker
            size="small" placeholder={['开始日期', '结束日期']}
            onChange={(_, ds) => setDateRange(ds as [string, string] | null)}
            style={{ width: 220 }}
          />
        </Space>
        <Segmented
          size="small" value={viewMode}
          onChange={(v) => setViewMode(v as 'grid' | 'list')}
          options={[
            { label: <><AppstoreOutlined /> 卡片</>, value: 'grid' },
            { label: <><UnorderedListOutlined /> 列表</>, value: 'list' },
          ]}
        />
      </div>

      {/* Inline error banner when data exists but there's a partial error */}
      {error && tasks.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Button type="link" size="small" onClick={fetchTasks} danger>
            刷新失败: {error}，点击重试
          </Button>
        </div>
      )}

      {/* ── Loading state (first load) ── */}
      {loading && !hasLoaded && (
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
          <Spin size="large" tip="正在加载任务列表..." />
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && hasLoaded && filtered.length === 0 && (
        <Empty
          description={tasks.length === 0 ? '暂无任务，点击新建任务开始' : '没有匹配的任务'}
        >
          {tasks.length === 0 && (
            <Button type="primary" onClick={() => setDrawerOpen(true)}>新建任务</Button>
          )}
        </Empty>
      )}

      {/* Task List */}
      {filtered.length > 0 && (viewMode === 'grid' ? (
        <Row gutter={[16, 16]}>
          {filtered.map((task: TaskInfo) => (
            <Col xs={24} sm={12} lg={8} key={task.id}>
              <TaskCard task={task} onDelete={handleDelete} onRetry={handleRetry} />
            </Col>
          ))}
        </Row>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {filtered.map((task: TaskInfo) => (
            <TaskCard key={task.id} task={task} compact onDelete={handleDelete} onRetry={handleRetry} />
          ))}
        </div>
      ))}

      <TaskDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </ErrorBoundary>
  );
};

export default TaskCenter;
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, App, Badge, Button, Card, ConfigProvider, Descriptions, Drawer, Empty, Form, Input,
  InputNumber, Modal, Popconfirm, Segmented, Select, Space, Spin, Switch,
  Table, Tag, Timeline, Typography,
} from 'antd';
import enUS from 'antd/locale/en_US';
import {
  ApartmentOutlined, BarChartOutlined, ClockCircleOutlined, DatabaseOutlined, DeleteOutlined,
  DeploymentUnitOutlined,
  EditOutlined, FieldTimeOutlined,
  PlusOutlined, RobotOutlined, SearchOutlined, SettingOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  automationCreateWorkflow, automationDeleteWorkflow,
  automationUpdateWorkflow,
} from '@/services/api';
import { AI_ANALYZE_WS_URL } from '@/services/aiApi';
import {
  automationCreateSchedule, automationDeleteSchedule,
  automationReloadSchedules, automationToggleSchedule, automationUpdateSchedule,
  mapWorkspaceSchedules, type AutomationSchedule,
} from '@/services/scheduler';
import { useWebSocket } from '@/hooks/useWebSocket';
import WorkspaceDock from '@/pages/AICollect/WorkspaceDock';
import type {
  AutomationWorkflow, ProductDomain, SchedulerTaskConfig, WorkspaceTask,
} from '@/services/types';
import './style.css';

type Section = 'workflows' | 'schedules' | 'runs';
type DomainFilter = ProductDomain | 'all';
type SelectedRecord = AutomationWorkflow | AutomationSchedule;

const domainMeta: Record<ProductDomain, { label: string; color: string; className: string; icon: React.ReactNode }> = {
  'ai-collect': { label: 'Scout', color: 'purple', className: 'is-ai', icon: <RobotOutlined /> },
  'data-lake': { label: 'Vault', color: 'green', className: 'is-lake', icon: <DatabaseOutlined /> },
  'etl-pipeline': { label: 'Flow', color: 'blue', className: 'is-etl', icon: <ApartmentOutlined /> },
  'data-cockpit': { label: 'Atlas', color: 'gold', className: 'is-cockpit', icon: <BarChartOutlined /> },
  'knowledge-graph': { label: 'Graph', color: 'purple', className: 'is-graph', icon: <DeploymentUnitOutlined /> },
  'knowledge-rag': { label: 'RAG', color: 'cyan', className: 'is-rag', icon: <DatabaseOutlined /> },
  platform: { label: 'Platform', color: 'default', className: 'is-platform', icon: <SettingOutlined /> },
};

const sectionMeta: Record<Section, { label: string; description: string }> = {
  workflows: { label: 'Workflows', description: 'Compose templates and execution nodes into reusable workflows' },
  schedules: { label: 'Schedules', description: 'Manage recurring triggers, queues, and retry policies' },
  runs: { label: 'Runs', description: '' },
};
const objectLabel: Record<Section, string> = {
  workflows: 'Workflow', schedules: 'Schedule', runs: 'Run',
};

const sectionByPath = (pathname: string): Section => {
  if (pathname.includes('/schedules')) return 'schedules';
  if (pathname.includes('/runs')) return 'runs';
  if (pathname.includes('/workflows')) return 'workflows';
  return 'workflows';
};

const relativeTime = (value?: string) => {
  if (!value) return '—';
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return value;
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
};

const formatSchedule = (item: AutomationSchedule) => item.schedule_label ?? (item.schedule_type === 'interval'
  ? `Every ${item.interval_seconds ?? 60}s`
  : `${item.cron_minute} ${item.cron_hour} ${item.cron_day_of_month} ${item.cron_month_of_year} ${item.cron_day_of_week}`);

const AutomationCenter: React.FC = () => {
  const { message } = App.useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [form] = Form.useForm();
  const section = sectionByPath(location.pathname);
  const requestedDomain = searchParams.get('domain') as DomainFilter | null;
  const domain: DomainFilter = requestedDomain && (requestedDomain === 'all' || requestedDomain in domainMeta)
    ? requestedDomain
    : 'all';
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(true);
  const [streamAvailable, setStreamAvailable] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [schedules, setSchedules] = useState<AutomationSchedule[]>([]);
  const [workspaceSchedules, setWorkspaceSchedules] = useState<AutomationSchedule[]>([]);
  const pendingSnapshots = useRef(new Set<string>());
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SelectedRecord | null>(null);
  const [detail, setDetail] = useState<SelectedRecord | null>(null);
  const tableCardRef = useRef<HTMLDivElement>(null);
  const [tableBodyHeight, setTableBodyHeight] = useState(240);

  const { connected, send } = useWebSocket(AI_ANALYZE_WS_URL, {
    onMessage: (raw) => {
      const event = JSON.parse(raw);
      if (event.type === 'task_stream_status') {
        setStreamAvailable(Boolean(event.data.available));
        return;
      }
      if (section === 'workflows' && event.type === 'workflows_snapshot') {
        setWorkflows(event.data);
        pendingSnapshots.current.delete('workflows');
      } else if (section === 'schedules') {
        if (event.type === 'schedules_snapshot') {
          setSchedules(event.data);
          pendingSnapshots.current.delete('schedules');
        } else if (event.type === 'tasks_snapshot') {
          setWorkspaceSchedules(mapWorkspaceSchedules(event.data));
          pendingSnapshots.current.delete('tasks');
        } else if (event.type === 'task_detail') {
          const task = event.data as WorkspaceTask;
          setWorkspaceSchedules((current) => [
            ...mapWorkspaceSchedules([task]),
            ...current.filter((item) => item.workspace_task_id !== task.id),
          ]);
        } else if (event.type === 'task_deleted') {
          setWorkspaceSchedules((current) => current.filter((item) => item.workspace_task_id !== event.task_id));
        }
      }
      if (!pendingSnapshots.current.size) setLoading(false);
    },
    onClose: () => setStreamAvailable(false),
  });

  useEffect(() => {
    const channels = section === 'runs' ? [] : section === 'workflows' ? ['workflows'] : ['schedules', 'tasks'];
    pendingSnapshots.current = new Set(channels);
    setLoading(channels.length > 0);
    if (!connected) return;
    channels.forEach((channel) => send(JSON.stringify({ type: 'subscribe', channel })));
    return () => {
      channels.forEach((channel) => send(JSON.stringify({ type: 'unsubscribe', channel })));
    };
  }, [connected, section, send]);

  const selectRun = useCallback((taskId: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (taskId) next.set('task', taskId);
      else next.delete('task');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const viewRun = (id: string) => {
    setDetail(null);
    navigate(`/automation/runs?task=${encodeURIComponent(id)}`);
  };

  const changeSection = (value: string) => {
    setDetail(null);
    navigate(`/automation/${value}${requestedDomain ? `?domain=${domain}` : ''}`);
  };
  const changeDomain = (value: DomainFilter) => {
    const next = new URLSearchParams(searchParams);
    next.set('domain', value);
    setSearchParams(next);
  };

  const domainTag = (value: ProductDomain) => <Tag color={(domainMeta[value] ?? domainMeta.platform).color}>{(domainMeta[value] ?? domainMeta.platform).label}</Tag>;
  const matches = (text: string) => text.toLowerCase().includes(keyword.trim().toLowerCase());
  const filteredWorkflows = useMemo(() => workflows.filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.name} ${item.description}`)), [workflows, domain, keyword]);
  const filteredSchedules = useMemo(() => [...workspaceSchedules, ...schedules].filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.display_name ?? item.task_name} ${item.task_path} ${item.description ?? ''}`)), [workspaceSchedules, schedules, domain, keyword]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ product_domain: domain === 'all' ? 'ai-collect' : domain, enabled: true, schedule_type: 'crontab', cron_minute: '0', cron_hour: '2', nodes: '' });
    setEditorOpen(true);
  };

  const openEdit = (record: SelectedRecord) => {
    if ('workspace_task_id' in record && record.workspace_task_id) {
      viewRun(record.workspace_task_id);
      return;
    }
    setEditing(record);
    if (section === 'workflows') {
      const item = record as AutomationWorkflow;
      form.setFieldsValue({ ...item, nodes: item.nodes.map((node) => node.name).join('\n') });
    } else if (section === 'schedules') {
      form.setFieldsValue(record);
    }
    setEditorOpen(true);
  };

  const save = async () => {
    const values = await form.validateFields();
    setMutating(true);
    try {
      if (section === 'workflows') {
        const payload = {
          name: values.name,
          product_domain: values.product_domain as ProductDomain,
          description: values.description ?? '',
          nodes: String(values.nodes ?? '').split(/\r?\n|,/).map((name) => name.trim()).filter(Boolean).map((name) => ({ name })),
          enabled: values.enabled,
        };
        if (editing) await automationUpdateWorkflow((editing as AutomationWorkflow).name, payload);
        else await automationCreateWorkflow(payload);
      } else if (section === 'schedules') {
        const payload: Omit<SchedulerTaskConfig, 'id' | 'created_at' | 'updated_at'> = {
          task_name: values.task_name, task_path: values.task_path,
          product_domain: values.product_domain, description: values.description ?? '',
          schedule_type: values.schedule_type, cron_minute: values.cron_minute ?? '*',
          cron_hour: values.cron_hour ?? '*', cron_day_of_week: '*', cron_day_of_month: '*',
          cron_month_of_year: '*', interval_seconds: values.interval_seconds,
          args: [], kwargs: {}, options: {}, enabled: values.enabled,
        };
        if (editing) await automationUpdateSchedule((editing as SchedulerTaskConfig).task_name, payload);
        else await automationCreateSchedule(payload);
        const reload = await automationReloadSchedules();
        if (!reload.loaded || reload.restart_required) message.warning(reload.message);
      }
      message.success(editing ? 'Changes saved' : 'Created successfully');
      setEditorOpen(false);
    } finally {
      setMutating(false);
    }
  };

  const remove = async (record: SelectedRecord) => {
    setMutating(true);
    try {
      if (section === 'workflows') await automationDeleteWorkflow((record as AutomationWorkflow).name);
      else if (section === 'schedules') {
        await automationDeleteSchedule((record as SchedulerTaskConfig).task_name);
        const reload = await automationReloadSchedules();
        if (!reload.loaded || reload.restart_required) message.warning(reload.message);
      }
      if (detail === record) setDetail(null);
      message.success('Deleted');
    } finally { setMutating(false); }
  };

  const toggleSchedule = async (record: AutomationSchedule, enabled: boolean) => {
    await automationToggleSchedule(record.task_name, enabled);
    const reload = await automationReloadSchedules();
    if (!reload.loaded || reload.restart_required) message.warning(reload.message);
    message.success(enabled ? 'Schedule enabled' : 'Schedule paused');
  };

  const rowActions = (record: SelectedRecord) => {
    if ('workspace_task_id' in record && record.workspace_task_id) {
      const taskId = record.workspace_task_id;
      return <Button type="link" onClick={(event) => { event.stopPropagation(); viewRun(taskId); }}>View Run</Button>;
    }
    return <Space size={2}>
      <Button type="text" icon={<EditOutlined />} aria-label="Edit" onClick={(event) => { event.stopPropagation(); openEdit(record); }} />
      <Popconfirm title="Delete this object?" onConfirm={() => void remove(record)}><Button type="text" danger icon={<DeleteOutlined />} aria-label="Delete" onClick={(event) => event.stopPropagation()} /></Popconfirm>
    </Space>;
  };

  const workflowColumns = [
    { title: 'Workflow', dataIndex: 'name', render: (_: string, item: AutomationWorkflow) => <div className="automation-name"><ApartmentOutlined /><span><strong>{item.name}</strong><small>{item.description || 'No description'}</small></span></div> },
    { title: 'Domain', width: 140, render: (_: unknown, item: AutomationWorkflow) => domainTag(item.product_domain) },
    { title: 'Nodes', width: 100, render: (_: unknown, item: AutomationWorkflow) => `${item.nodes.length} nodes` },
    { title: 'Status', width: 110, render: (_: unknown, item: AutomationWorkflow) => <Badge status={item.enabled ? 'success' : 'default'} text={item.enabled ? 'Enabled' : 'Disabled'} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: AutomationWorkflow) => relativeTime(item.updated_at) },
    { title: '', width: 82, render: (_: unknown, item: AutomationWorkflow) => rowActions(item) },
  ];
  const scheduleColumns = [
    { title: 'Schedule', dataIndex: 'task_name', render: (_: string, item: AutomationSchedule) => <div className="automation-name"><FieldTimeOutlined /><span><strong>{item.display_name ?? item.task_name}</strong><small>{item.description || item.task_path}</small></span></div> },
    { title: 'Domain', width: 140, render: (_: unknown, item: AutomationSchedule) => domainTag(item.product_domain) },
    { title: 'Trigger', width: 190, render: (_: unknown, item: AutomationSchedule) => <Typography.Text code>{formatSchedule(item)}</Typography.Text> },
    { title: 'Enabled', width: 90, render: (_: unknown, item: AutomationSchedule) => item.workspace_task_id
      ? <Badge status={item.enabled ? 'success' : 'default'} text={item.enabled ? 'Enabled' : 'Paused'} />
      : <Switch size="small" checked={item.enabled} onChange={(checked) => void toggleSchedule(item, checked)} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: AutomationSchedule) => relativeTime(item.updated_at) },
    { title: '', width: 100, render: (_: unknown, item: AutomationSchedule) => rowActions(item) },
  ];
  const currentData = section === 'workflows' ? filteredWorkflows : filteredSchedules;
  const currentColumns = section === 'workflows' ? workflowColumns : scheduleColumns;
  useLayoutEffect(() => {
    const updateTableHeight = () => {
      const card = tableCardRef.current;
      if (!card) return;
      const header = card.querySelector<HTMLElement>('.ant-table-thead');
      const pagination = card.querySelector<HTMLElement>('.ant-pagination');
      const paginationStyle = pagination ? getComputedStyle(pagination) : null;
      const paginationMargins = paginationStyle
        ? parseFloat(paginationStyle.marginTop) + parseFloat(paginationStyle.marginBottom)
        : 0;
      const fixedHeight = (header?.offsetHeight ?? 0) + (pagination?.offsetHeight ?? 0) + paginationMargins + 2;
      setTableBodyHeight(Math.max(56, card.clientHeight - fixedHeight));
    };
    updateTableHeight();
    const observer = new ResizeObserver(updateTableHeight);
    if (tableCardRef.current) observer.observe(tableCardRef.current);
    return () => observer.disconnect();
  }, [currentData.length, loading, section]);

  const detailName = detail && ('task_name' in detail ? detail.display_name ?? detail.task_name : detail.name);
  const editorTitle = `${editing ? 'Edit' : 'Create'} ${objectLabel[section]}`;

  return <ConfigProvider locale={enUS}><div className="automation-page">
    <header className="automation-header">
      <div><Typography.Text className="automation-kicker">AUTOMATION FOUNDATION</Typography.Text><Typography.Title level={2}>Automation Center</Typography.Title></div>
    </header>

    <div className="access-tabs automation-tabs">
      <Segmented value={section} onChange={(value) => changeSection(String(value))} options={[
        { label: 'Workflows', value: 'workflows', icon: <ApartmentOutlined /> },
        { label: 'Schedules', value: 'schedules', icon: <FieldTimeOutlined /> }, { label: 'Runs', value: 'runs', icon: <ClockCircleOutlined /> },
      ]} />
      <Space wrap className="automation-filters"><Select<DomainFilter> value={requestedDomain && (requestedDomain === 'all' || requestedDomain in domainMeta) ? domain : undefined} placeholder="Select domain" onChange={changeDomain} className="automation-domain-select" options={[{ value: 'all', label: <Space size={7}><DeploymentUnitOutlined />All</Space> }, ...Object.entries(domainMeta).map(([value, meta]) => ({ value: value as ProductDomain, label: <Space size={7}>{meta.icon}{meta.label}</Space> }))]} /><Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} prefix={<SearchOutlined />} placeholder="Search name, type, or owner" className="automation-search" />{section !== 'runs' && <Button icon={<PlusOutlined />} onClick={openCreate}>New Record</Button>}</Space>
    </div>

    {section !== 'runs' && (!connected || !streamAvailable) && <Alert className="automation-load-alert" type="warning" showIcon message="Live updates unavailable. Reconnecting automatically…" />}

    {section === 'runs' ? <Card className="automation-task-panel" styles={{ body: { padding: 0 } }}>
      <WorkspaceDock
        activePanel="tasks"
        embedded
        taskDomain={domain}
        taskKeyword={keyword}
        taskId={searchParams.get('task')}
        onTaskSelect={selectRun}
        onToggle={() => changeSection('runs')}
        onClose={() => selectRun(null)}
      />
    </Card> : <Card ref={tableCardRef} className="automation-table-card" styles={{ body: { padding: 0 } }}>
      <Spin spinning={loading}><Table scroll={{ y: tableBodyHeight }} rowKey={(record) => String('id' in record && record.id ? record.id : 'task_name' in record ? record.task_name : record.name)} columns={currentColumns as never} dataSource={currentData as never} pagination={{ pageSize: 10, hideOnSinglePage: true }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`No ${sectionMeta[section].label.toLowerCase()} match the current filters`} /> }} onRow={(record) => ({ onClick: () => setDetail(record as SelectedRecord), style: { cursor: 'pointer' } })} /></Spin>
    </Card>}

    <Modal title={editorTitle} open={editorOpen} confirmLoading={mutating} onCancel={() => setEditorOpen(false)} onOk={() => void save()} okText={editing ? 'Save Changes' : 'Create'} width={640} forceRender>
      <Form form={form} layout="vertical" className="automation-form">
        {section === 'workflows' && <><Form.Item name="name" label="Workflow Name" rules={[{ required: true }]}><Input disabled={Boolean(editing)} /></Form.Item><Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item><Form.Item name="description" label="Description"><Input.TextArea rows={2} /></Form.Item><Form.Item name="nodes" label="Execution Nodes" extra="One node per line, executed in order"><Input.TextArea rows={6} placeholder={'Collect data\nNormalize fields\nWrite to ODS'} /></Form.Item><Form.Item name="enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item></>}
        {section === 'schedules' && <><Form.Item name="task_name" label="Schedule Name" rules={[{ required: true }]}><Input disabled={Boolean(editing)} /></Form.Item><Form.Item name="task_path" label="Celery Task Path" rules={[{ required: true }]}><Input placeholder="app.scheduler.tasks.workspace.dispatch_due" /></Form.Item><Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item><Form.Item name="description" label="Description"><Input /></Form.Item><Form.Item name="schedule_type" label="Trigger Type"><Segmented options={[{ value: 'crontab', label: 'Cron' }, { value: 'interval', label: 'Interval' }]} /></Form.Item><Form.Item noStyle shouldUpdate={(previous, current) => previous.schedule_type !== current.schedule_type}>{({ getFieldValue }) => getFieldValue('schedule_type') === 'interval' ? <Form.Item name="interval_seconds" label="Interval Seconds" rules={[{ required: true }]}><InputNumber min={10} style={{ width: '100%' }} /></Form.Item> : <Space align="start"><Form.Item name="cron_minute" label="Minute"><Input /></Form.Item><Form.Item name="cron_hour" label="Hour"><Input /></Form.Item></Space>}</Form.Item><Form.Item name="enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item></>}
      </Form>
    </Modal>

    <Drawer title={detailName} open={Boolean(detail)} onClose={() => { setDetail(null); if (searchParams.has('task')) { const next = new URLSearchParams(searchParams); next.delete('task'); setSearchParams(next); } }} width={520} extra={detail && section !== 'runs' ? <Button onClick={() => openEdit(detail)}>{'workspace_task_id' in detail ? 'View Run' : 'Edit'}</Button> : null}>
      {detail && <>
        <Descriptions column={1} bordered size="small" items={Object.entries(detail).filter(([key]) => !['yaml_content', 'metadata', 'parameters', 'policies', 'logs'].includes(key)).slice(0, 12).map(([key, value]) => ({ key, label: key, children: typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—') }))} />
        {section === 'workflows' && <><Typography.Title level={5} style={{ marginTop: 24 }}>Execution Path</Typography.Title><Timeline items={(detail as AutomationWorkflow).nodes.map((node, index) => ({ color: index === 0 ? 'blue' : 'gray', children: node.name }))} /></>}
      </>}
    </Drawer>
  </div></ConfigProvider>;
};

export default AutomationCenter;

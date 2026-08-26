import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Badge, Button, Card, ConfigProvider, Descriptions, Drawer, Dropdown, Empty, Form, Input,
  InputNumber, Modal, Popconfirm, Progress, Segmented, Select, Space, Spin, Switch,
  Table, Tag, Timeline, Typography,
} from 'antd';
import enUS from 'antd/locale/en_US';
import {
  ApartmentOutlined, BarChartOutlined, ClockCircleOutlined, DatabaseOutlined, DeleteOutlined,
  DeploymentUnitOutlined,
  EditOutlined, EllipsisOutlined, FieldTimeOutlined, PauseOutlined, PlayCircleOutlined,
  PlusOutlined, ReloadOutlined, RobotOutlined, SearchOutlined, SettingOutlined, StopOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  aiCreateWorkspaceTask, aiDeleteWorkspaceTask, aiFetchWorkspaceTasks,
  aiTaskAction,
  automationCreateSchedule, automationCreateWorkflow,
  automationDeleteSchedule, automationDeleteWorkflow,
  automationFetchSchedules, automationFetchWorkflows, automationReloadSchedules,
  automationToggleSchedule, automationUpdateSchedule, automationUpdateWorkflow,
} from '@/services/api';
import type {
  AutomationWorkflow, ProductDomain, SchedulerTaskConfig, WorkspaceTask,
} from '@/services/types';
import './style.css';

type Section = 'workflows' | 'schedules' | 'runs';
type DomainFilter = ProductDomain | 'all';
type SelectedRecord = AutomationWorkflow | SchedulerTaskConfig | WorkspaceTask;

const domainMeta: Record<ProductDomain, { label: string; color: string; className: string; icon: React.ReactNode }> = {
  'ai-collect': { label: 'Collect', color: 'purple', className: 'is-ai', icon: <RobotOutlined /> },
  'data-lake': { label: 'Lake', color: 'green', className: 'is-lake', icon: <DatabaseOutlined /> },
  'etl-pipeline': { label: 'Pileline', color: 'blue', className: 'is-etl', icon: <ApartmentOutlined /> },
  'data-cockpit': { label: 'Cockpit', color: 'gold', className: 'is-cockpit', icon: <BarChartOutlined /> },
  'knowledge-graph': { label: 'Knowledge Graph', color: 'purple', className: 'is-graph', icon: <DeploymentUnitOutlined /> },
  'knowledge-rag': { label: 'Knowledge RAG', color: 'cyan', className: 'is-rag', icon: <DatabaseOutlined /> },
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

const taskDomain = (item: WorkspaceTask): ProductDomain => {
  const domain = String(item.parameters?.product_domain ?? 'ai-collect') as ProductDomain;
  return domain in domainMeta ? domain : 'ai-collect';
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

const formatSchedule = (item: SchedulerTaskConfig) => item.schedule_type === 'interval'
  ? `Every ${item.interval_seconds ?? 60}s`
  : `${item.cron_minute} ${item.cron_hour} ${item.cron_day_of_month} ${item.cron_month_of_year} ${item.cron_day_of_week}`;

const AutomationCenter: React.FC = () => {
  const { message } = App.useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [form] = Form.useForm();
  const section = sectionByPath(location.pathname);
  const requestedDomain = searchParams.get('domain') as DomainFilter | null;
  const domain: DomainFilter = requestedDomain && (requestedDomain === 'all' || requestedDomain in domainMeta) ? requestedDomain : 'all';
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadFailures, setLoadFailures] = useState(0);
  const [mutating, setMutating] = useState(false);
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [schedules, setSchedules] = useState<SchedulerTaskConfig[]>([]);
  const [runs, setRuns] = useState<WorkspaceTask[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SelectedRecord | null>(null);
  const [detail, setDetail] = useState<SelectedRecord | null>(null);

  const refresh = useCallback(async (announce = false) => {
    setLoading(true);
    const results = await Promise.allSettled([
      automationFetchWorkflows(), automationFetchSchedules(), aiFetchWorkspaceTasks(),
    ]);
    if (results[0].status === 'fulfilled') setWorkflows(results[0].value);
    if (results[1].status === 'fulfilled') setSchedules(results[1].value);
    if (results[2].status === 'fulfilled') setRuns(results[2].value.items);
    const failed = results.filter((result) => result.status === 'rejected').length;
    setLoadFailures(failed);
    if (failed && announce) message.warning(`${failed} automation data sources are unavailable`);
    else if (announce) message.success('Automation data refreshed');
    setLoading(false);
  }, [message]);

  useEffect(() => { void refresh(); }, [refresh]);

  const changeSection = (value: string) => navigate(`/automation/${value}?domain=${domain}`);
  const changeDomain = (value: DomainFilter) => {
    const next = new URLSearchParams(searchParams);
    next.set('domain', value);
    setSearchParams(next);
  };

  const domainTag = (value: ProductDomain) => <Tag color={domainMeta[value].color}>{domainMeta[value].label}</Tag>;
  const matches = (text: string) => text.toLowerCase().includes(keyword.trim().toLowerCase());
  const filteredWorkflows = useMemo(() => workflows.filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.name} ${item.description}`)), [workflows, domain, keyword]);
  const filteredSchedules = useMemo(() => schedules.filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.task_name} ${item.task_path} ${item.description ?? ''}`)), [schedules, domain, keyword]);
  const filteredRuns = useMemo(() => runs.filter((item) =>
    (domain === 'all' || taskDomain(item) === domain) && matches(`${item.name} ${item.template_name} ${item.owner}`)), [runs, domain, keyword]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ product_domain: domain === 'all' ? 'ai-collect' : domain, enabled: true, schedule_type: 'crontab', cron_minute: '0', cron_hour: '2', nodes: '' });
    setEditorOpen(true);
  };

  const openEdit = (record: SelectedRecord) => {
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
        if (!reload.loaded) message.warning(reload.message);
      } else {
        await aiCreateWorkspaceTask({
          name: values.name, template_name: values.template_name,
          template_version: 'v1.0', schedule: { mode: 'once' }, parameters: { product_domain: values.product_domain }, policies: {}, owner: values.owner ?? 'Current User',
        });
      }
      message.success(editing ? 'Changes saved' : 'Created successfully');
      setEditorOpen(false);
      await refresh();
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
        if (!reload.loaded) message.warning(reload.message);
      }
      else await aiDeleteWorkspaceTask((record as WorkspaceTask).id);
      if (detail === record) setDetail(null);
      message.success('Deleted');
      await refresh();
    } finally { setMutating(false); }
  };

  const toggleSchedule = async (record: SchedulerTaskConfig, enabled: boolean) => {
    await automationToggleSchedule(record.task_name, enabled);
    const reload = await automationReloadSchedules();
    if (!reload.loaded) message.warning(reload.message);
    message.success(enabled ? 'Schedule enabled' : 'Schedule paused');
    await refresh();
  };

  const runAction = async (record: WorkspaceTask, action: string) => {
    setMutating(true);
    try {
      await aiTaskAction(record.id, { action });
      message.success({ start: 'Run submitted', pause: 'Run paused', resume: 'Run resumed', restart: 'Run restarted', cancel: 'Run cancelled' }[action] ?? 'Action completed');
      await refresh();
    } finally { setMutating(false); }
  };

  const rowActions = (record: SelectedRecord) => {
    if (section === 'runs') {
      const item = record as WorkspaceTask;
      const items = [];
      if (['queued', 'failed'].includes(item.status)) items.push({ key: 'start', label: 'Start', icon: <PlayCircleOutlined /> });
      if (item.status === 'running') items.push({ key: 'pause', label: 'Pause', icon: <PauseOutlined /> });
      if (item.status === 'paused') items.push({ key: 'resume', label: 'Resume', icon: <PlayCircleOutlined /> });
      if (['running', 'paused', 'failed'].includes(item.status)) items.push({ key: 'restart', label: 'Restart', icon: <ReloadOutlined /> });
      if (['running', 'paused', 'queued'].includes(item.status)) items.push({ key: 'cancel', label: 'Cancel', icon: <StopOutlined />, danger: true });
      return <Dropdown menu={{ items, onClick: ({ key }) => void runAction(item, key) }}><Button type="text" icon={<EllipsisOutlined />} aria-label="Run actions" /></Dropdown>;
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
    { title: 'Schedule', dataIndex: 'task_name', render: (_: string, item: SchedulerTaskConfig) => <div className="automation-name"><FieldTimeOutlined /><span><strong>{item.task_name}</strong><small>{item.description || item.task_path}</small></span></div> },
    { title: 'Domain', width: 140, render: (_: unknown, item: SchedulerTaskConfig) => domainTag(item.product_domain) },
    { title: 'Trigger', width: 190, render: (_: unknown, item: SchedulerTaskConfig) => <Typography.Text code>{formatSchedule(item)}</Typography.Text> },
    { title: 'Enabled', width: 90, render: (_: unknown, item: SchedulerTaskConfig) => <Switch size="small" checked={item.enabled} onChange={(checked) => void toggleSchedule(item, checked)} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: SchedulerTaskConfig) => relativeTime(item.updated_at) },
    { title: '', width: 82, render: (_: unknown, item: SchedulerTaskConfig) => rowActions(item) },
  ];
  const runColumns = [
    { title: 'Run', dataIndex: 'name', render: (_: string, item: WorkspaceTask) => <div className="automation-name"><ClockCircleOutlined /><span><strong>{item.name}</strong><small>{item.template_name}@{item.template_version}</small></span></div> },
    { title: 'Domain', width: 140, render: (_: unknown, item: WorkspaceTask) => domainTag(taskDomain(item)) },
    { title: 'Status', width: 110, render: (_: unknown, item: WorkspaceTask) => <Badge status={item.status === 'running' ? 'processing' : item.status === 'completed' ? 'success' : item.status === 'failed' ? 'error' : 'default'} text={item.status} /> },
    { title: 'Progress', width: 170, render: (_: unknown, item: WorkspaceTask) => <Progress percent={item.progress} size="small" status={item.status === 'failed' ? 'exception' : undefined} /> },
    { title: 'Records', width: 100, dataIndex: 'records' },
    { title: 'Updated', width: 110, render: (_: unknown, item: WorkspaceTask) => relativeTime(item.updated_at) },
    { title: '', width: 58, render: (_: unknown, item: WorkspaceTask) => rowActions(item) },
  ];

  const currentData = section === 'workflows' ? filteredWorkflows : section === 'schedules' ? filteredSchedules : filteredRuns;
  const currentColumns = section === 'workflows' ? workflowColumns : section === 'schedules' ? scheduleColumns : runColumns;
  const running = runs.filter((item) => item.status === 'running').length;
  const attention = runs.filter((item) => item.status === 'failed').length;

  const detailName = detail && ('task_name' in detail ? detail.task_name : detail.name);
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
      <Space wrap className="automation-filters"><Select<DomainFilter> value={domain} onChange={changeDomain} className="automation-domain-select" options={[{ value: 'all', label: <Space size={7}><DeploymentUnitOutlined />All</Space> }, ...Object.entries(domainMeta).map(([value, meta]) => ({ value: value as ProductDomain, label: <Space size={7}>{meta.icon}{meta.label}</Space> }))]} /><Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} prefix={<SearchOutlined />} placeholder="Search name, type, or owner" className="automation-search" />{section !== 'runs' && <Button icon={<PlusOutlined />} onClick={openCreate}>New Record</Button>}</Space>
    </div>

    {loadFailures > 0 && <Alert className="automation-load-alert" type="warning" showIcon message="Some automation data is temporarily unavailable. Available content remains usable." />}

    <div className="automation-stats">
      <div><span>Current Objects</span><strong>{currentData.length}</strong></div><div><span>Running</span><strong>{running}</strong></div><div className={attention ? 'has-attention' : ''}><span>Needs Attention</span><strong>{attention}</strong></div>
    </div>

    <Card className="automation-table-card" styles={{ body: { padding: 0 } }}>
      <Spin spinning={loading}><Table rowKey={(record) => String('id' in record && record.id ? record.id : 'task_name' in record ? record.task_name : record.name)} columns={currentColumns as never} dataSource={currentData as never} pagination={{ pageSize: 10, hideOnSinglePage: true }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`No ${sectionMeta[section].label.toLowerCase()} match the current filters`} /> }} onRow={(record) => ({ onClick: () => setDetail(record as SelectedRecord), style: { cursor: 'pointer' } })} /></Spin>
    </Card>

    <Modal title={editorTitle} open={editorOpen} confirmLoading={mutating} onCancel={() => setEditorOpen(false)} onOk={() => void save()} okText={editing ? 'Save Changes' : section === 'templates' ? 'Import' : 'Create'} width={640} forceRender>
      <Form form={form} layout="vertical" className="automation-form">
        {section === 'workflows' && <><Form.Item name="name" label="Workflow Name" rules={[{ required: true }]}><Input disabled={Boolean(editing)} /></Form.Item><Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item><Form.Item name="description" label="Description"><Input.TextArea rows={2} /></Form.Item><Form.Item name="nodes" label="Execution Nodes" extra="One node per line, executed in order"><Input.TextArea rows={6} placeholder={'Collect data\nNormalize fields\nWrite to ODS'} /></Form.Item><Form.Item name="enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item></>}
        {section === 'schedules' && <><Form.Item name="task_name" label="Schedule Name" rules={[{ required: true }]}><Input disabled={Boolean(editing)} /></Form.Item><Form.Item name="task_path" label="Celery Task Path" rules={[{ required: true }]}><Input placeholder="app.scheduler.tasks.workspace.dispatch_due" /></Form.Item><Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item><Form.Item name="description" label="Description"><Input /></Form.Item><Form.Item name="schedule_type" label="Trigger Type"><Segmented options={[{ value: 'crontab', label: 'Cron' }, { value: 'interval', label: 'Interval' }]} /></Form.Item><Form.Item noStyle shouldUpdate={(previous, current) => previous.schedule_type !== current.schedule_type}>{({ getFieldValue }) => getFieldValue('schedule_type') === 'interval' ? <Form.Item name="interval_seconds" label="Interval Seconds" rules={[{ required: true }]}><InputNumber min={10} style={{ width: '100%' }} /></Form.Item> : <Space align="start"><Form.Item name="cron_minute" label="Minute"><Input /></Form.Item><Form.Item name="cron_hour" label="Hour"><Input /></Form.Item></Space>}</Form.Item><Form.Item name="enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item></>}
      </Form>
    </Modal>

    <Drawer title={detailName} open={Boolean(detail)} onClose={() => setDetail(null)} width={520} extra={detail && section !== 'runs' ? <Button icon={<EditOutlined />} onClick={() => openEdit(detail)}>Edit</Button> : null}>
      {detail && <>
        <Descriptions column={1} bordered size="small" items={Object.entries(detail).filter(([key]) => !['yaml_content', 'metadata', 'parameters', 'policies', 'logs'].includes(key)).slice(0, 12).map(([key, value]) => ({ key, label: key, children: typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—') }))} />
        {section === 'workflows' && <><Typography.Title level={5} style={{ marginTop: 24 }}>Execution Path</Typography.Title><Timeline items={(detail as AutomationWorkflow).nodes.map((node, index) => ({ color: index === 0 ? 'blue' : 'gray', children: node.name }))} /></>}
        {section === 'runs' && <><Typography.Title level={5} style={{ marginTop: 24 }}>Run Controls</Typography.Title><Space wrap>{['queued', 'failed'].includes((detail as WorkspaceTask).status) && <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => void runAction(detail as WorkspaceTask, 'start')}>Start</Button>}{(detail as WorkspaceTask).status === 'running' && <Button icon={<PauseOutlined />} onClick={() => void runAction(detail as WorkspaceTask, 'pause')}>Pause</Button>}{(detail as WorkspaceTask).status === 'paused' && <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => void runAction(detail as WorkspaceTask, 'resume')}>Resume</Button>}<Button icon={<ReloadOutlined />} onClick={() => void runAction(detail as WorkspaceTask, 'restart')}>Restart</Button><Popconfirm title="Cancel this run?" onConfirm={() => void runAction(detail as WorkspaceTask, 'cancel')}><Button danger icon={<StopOutlined />}>Cancel</Button></Popconfirm><Popconfirm title="Only stopped runs can be deleted. Continue?" onConfirm={() => void remove(detail)}><Button danger type="text" icon={<DeleteOutlined />}>Delete Record</Button></Popconfirm></Space></>}
      </>}
    </Drawer>
  </div></ConfigProvider>;
};

export default AutomationCenter;

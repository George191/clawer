import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, App, Badge, Button, Card, ConfigProvider, Descriptions, Drawer, Empty, Form, Input,
  InputNumber, Modal, Popconfirm, Segmented, Select, Space, Spin, Switch,
  Table, Tag,
} from '@/components/ui';
import enUS from '@/components/ui/locale';
import {
  ApartmentOutlined, ArrowRightOutlined, BranchesOutlined, CloseOutlined, DeleteOutlined,
  EditOutlined, ExportOutlined, FieldTimeOutlined,
  PlusOutlined, SearchOutlined,
} from '@/components/ui/icons';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  automationCreateWorkflow, automationDeleteWorkflow,
  automationUpdateWorkflow,
} from '@/services/api';
import { getAiAnalyzeWsUrl } from '@/services/aiApi';
import {
  automationCreateSchedule, automationDeleteSchedule,
  automationReloadSchedules, automationToggleSchedule, automationUpdateSchedule, automationUpdateWorkspaceSchedule,
  getScheduleInterval, intervalUnitSeconds, mapWorkspaceSchedules, type AutomationSchedule, type IntervalUnit,
} from '@/services/scheduler';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useThemeStore } from '@/stores/settings';
import AutomationRuns from './AutomationRuns';
import WorkflowNodes, { WorkflowPath } from './WorkflowNodes';
import type {
  AutomationWorkflow, ProductDomain, SchedulerTaskConfig, WorkspaceTask,
} from '@/services/types';
import '@/pages/Pipeline/ontology-frame.css';
import '@/pages/Pipeline/foundry-workbench-tokens.css';
import './style.css';
import TransferSchedules from './TransferSchedules';

type Section = 'workflows' | 'schedules' | 'runs';
type DomainFilter = ProductDomain | 'all';
type ScheduleFilter = 'all' | 'enabled' | 'paused';
type SelectedRecord = AutomationWorkflow | AutomationSchedule;

const domainMeta: Record<ProductDomain, { label: string }> = {
  'ai-collect': { label: 'Scout' },
  'data-lake': { label: 'Vault' },
  'etl-pipeline': { label: 'Helio' },
  'data-cockpit': { label: 'Atlas' },
  'knowledge-graph': { label: 'Graph' },
  'knowledge-rag': { label: 'RAG' },
  platform: { label: 'Platform' },
};

const sectionMeta: Record<Section, { label: string; description: string }> = {
  workflows: { label: 'Workflows', description: 'Reusable execution paths across your workspace.' },
  schedules: { label: 'Schedules', description: 'Control when work starts and inspect its trigger rules.' },
  runs: { label: 'Runs', description: 'Follow execution progress and inspect individual runs.' },
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

const formatSchedule = (item: AutomationSchedule) => {
  if (item.schedule_label) return item.schedule_label;
  if (item.schedule_type === 'interval') {
    const { interval_value: value, interval_unit: unit } = getScheduleInterval(item);
    return `Every ${value} ${unit}${value === 1 ? '' : 's'}`;
  }
  return `${item.cron_minute} ${item.cron_hour} ${item.cron_day_of_month} ${item.cron_month_of_year} ${item.cron_day_of_week}`;
};

const AutomationCenter: React.FC = () => {
  const { message } = App.useApp();
  const isLight = useThemeStore((state) => state.mode === 'light');
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
  const [scheduleFilter, setScheduleFilter] = useState<ScheduleFilter>('all');
  const [loading, setLoading] = useState(true);
  const [streamAvailable, setStreamAvailable] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [togglingSchedule, setTogglingSchedule] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [schedules, setSchedules] = useState<AutomationSchedule[]>([]);
  const [workspaceSchedules, setWorkspaceSchedules] = useState<AutomationSchedule[]>([]);
  const pendingSnapshots = useRef(new Set<string>());
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SelectedRecord | null>(null);
  const [detail, setDetail] = useState<SelectedRecord | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AutomationSchedule | null>(null);
  const tableCardRef = useRef<HTMLDivElement>(null);
  const [tableBodyHeight, setTableBodyHeight] = useState(240);

  useEffect(() => {
    if (requestedDomain === domain) return;
    const next = new URLSearchParams(searchParams);
    next.set('domain', domain);
    setSearchParams(next, { replace: true });
  }, [domain, requestedDomain, searchParams, setSearchParams]);

  const { connected, send } = useWebSocket(getAiAnalyzeWsUrl, {
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

  const changeDomain = (value: DomainFilter) => {
    const next = new URLSearchParams(searchParams);
    next.set('domain', value);
    next.delete('data_type');
    setSearchParams(next);
  };

  const domainTag = (value: ProductDomain) => <Tag>{(domainMeta[value] ?? domainMeta.platform).label}</Tag>;
  const matches = (text: string) => text.toLowerCase().includes(keyword.trim().toLowerCase());
  const filteredWorkflows = useMemo(() => workflows.filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.name} ${item.description}`)), [workflows, domain, keyword]);
  const filteredSchedules = useMemo(() => [...workspaceSchedules, ...schedules].filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.display_name ?? item.task_name} ${item.task_path} ${item.description ?? ''}`)), [workspaceSchedules, schedules, domain, keyword]);
  const visibleSchedules = filteredSchedules.filter((item) => scheduleFilter === 'all' || item.enabled === (scheduleFilter === 'enabled'));

  const openScheduleDetail = (record: AutomationSchedule) => {
    setEditorOpen(false);
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ ...record, ...getScheduleInterval(record), task_name: record.display_name ?? record.task_name });
    setDetail(record);
  };

  const closeScheduleModal = () => {
    if (mutating) return;
    setDeleteTarget(null);
    setEditorOpen(false);
    setEditing(null);
    setDetail(null);
  };

  const openCreate = () => {
    if (section === 'schedules') setDetail(null);
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ product_domain: domain === 'all' ? 'ai-collect' : domain, enabled: true, schedule_type: 'crontab', cron_minute: '0', cron_hour: '2', interval_value: 1, interval_unit: 'minute', nodes: [{ name: '', type: 'task' }], cron_day_of_week: '*', cron_day_of_month: '*', cron_month_of_year: '*' });
    setEditorOpen(true);
  };

  const openEdit = (record: SelectedRecord) => {
    setEditing(record);
    if (section === 'workflows') {
      const item = record as AutomationWorkflow;
      form.setFieldsValue({ ...item, nodes: item.nodes });
    } else if (section === 'schedules') {
      const schedule = record as AutomationSchedule;
      form.setFieldsValue({ ...schedule, ...getScheduleInterval(schedule), task_name: schedule.display_name ?? schedule.task_name });
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
          nodes: values.nodes.map((node: AutomationWorkflow['nodes'][number]) => ({ ...node, name: node.name.trim() })),
          enabled: values.enabled,
        };
        const saved = editing ? await automationUpdateWorkflow((editing as AutomationWorkflow).name, payload) : await automationCreateWorkflow(payload);
        setWorkflows(current => [saved, ...current.filter(item => item.name !== saved.name)]);
        setDetail(saved);
      } else if (section === 'schedules') {
        const existing = editing as AutomationSchedule | null;
        if (existing?.workspace_task_id) {
          const saved = await automationUpdateWorkspaceSchedule(existing.workspace_task_id, {
            enabled: values.enabled,
            ...(values.schedule_type === 'interval'
              ? { mode: 'interval', interval_value: values.interval_value, interval_unit: values.interval_unit }
              : { mode: 'daily', daily_time: `${String(values.cron_hour).padStart(2, '0')}:${String(values.cron_minute).padStart(2, '0')}` }),
          });
          setWorkspaceSchedules((current) => current.map((item) => item.workspace_task_id === saved.workspace_task_id ? saved : item));
          setDetail(saved);
          form.setFieldsValue(saved);
        } else {
          const payload: Omit<SchedulerTaskConfig, 'id' | 'created_at' | 'updated_at'> = {
            task_name: values.task_name, task_path: values.task_path,
            product_domain: values.product_domain, description: values.description ?? '',
            schedule_type: values.schedule_type, cron_minute: values.cron_minute ?? existing?.cron_minute ?? '*',
            cron_hour: values.cron_hour ?? existing?.cron_hour ?? '*', cron_day_of_week: values.cron_day_of_week ?? '*', cron_day_of_month: values.cron_day_of_month ?? '*',
            cron_month_of_year: values.cron_month_of_year ?? '*', interval_seconds: values.schedule_type === 'interval' ? values.interval_value * intervalUnitSeconds[values.interval_unit as IntervalUnit] : existing?.interval_seconds,
            args: existing?.args ?? [], kwargs: existing?.kwargs ?? {}, options: existing?.options ?? {}, enabled: values.enabled,
          };
          const saved = editing
            ? await automationUpdateSchedule((editing as SchedulerTaskConfig).task_name, payload)
            : await automationCreateSchedule(payload);
          setSchedules(current => [saved, ...current.filter(item => item.task_name !== saved.task_name)]);
          const reload = await automationReloadSchedules();
          if (!reload.loaded || reload.restart_required) message.warning(reload.message);
          setDetail(saved);
          form.setFieldsValue(saved);
        }
      }
      message.success(editing ? 'Changes saved' : 'Created successfully');
      setEditorOpen(false);
    } catch {
      message.error('Unable to save. Check the required fields and schedule expression.');
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
      if (section === 'workflows') setWorkflows(current => current.filter(item => item.name !== (record as AutomationWorkflow).name));
      else setSchedules(current => current.filter(item => item.task_name !== (record as AutomationSchedule).task_name));
      message.success('Deleted');
    } finally { setMutating(false); }
  };

  const confirmScheduleDelete = async () => {
    if (!deleteTarget || mutating) return;
    try {
      await remove(deleteTarget);
      setDeleteTarget(null);
    } catch {
      message.error('Unable to delete this schedule. Please try again.');
    }
  };

  const toggleSchedule = async (record: AutomationSchedule, enabled: boolean) => {
    if (togglingSchedule) return;
    setTogglingSchedule(record.task_name);
    try {
      let saved: AutomationSchedule;
      if (record.workspace_task_id) {
        saved = await automationUpdateWorkspaceSchedule(record.workspace_task_id, { enabled });
        setWorkspaceSchedules((current) => current.map((item) => item.workspace_task_id === saved.workspace_task_id ? saved : item));
      } else {
        saved = await automationToggleSchedule(record.task_name, enabled);
        setSchedules((current) => current.map((item) => item.task_name === saved.task_name ? saved : item));
        const reload = await automationReloadSchedules();
        if (!reload.loaded || reload.restart_required) message.warning(reload.message);
      }
      setDetail((current) => current && 'task_name' in current && current.task_name === record.task_name ? saved : current);
      message.success(enabled ? 'Schedule enabled' : 'Schedule disabled');
    } catch {
      message.error('Unable to update this schedule. Please try again.');
    } finally {
      setTogglingSchedule(null);
    }
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
    { title: 'Execution path', width: 260, render: (_: unknown, item: AutomationWorkflow) => <div className="automation-node-path" title={item.nodes.map(node => node.name).join(' → ')}>{item.nodes.length ? <>{item.nodes.slice(0, 2).map((node, index) => <React.Fragment key={index}>{index > 0 && <ArrowRightOutlined />}<span>{node.name}</span></React.Fragment>)}{item.nodes.length > 2 && <small>+{item.nodes.length - 2}</small>}</> : <span className="automation-muted">No nodes configured</span>}</div> },
    { title: 'Status', width: 110, render: (_: unknown, item: AutomationWorkflow) => <Badge status={item.enabled ? 'success' : 'default'} text={item.enabled ? 'Enabled' : 'Disabled'} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: AutomationWorkflow) => relativeTime(item.updated_at) },
    { title: '', width: 82, render: (_: unknown, item: AutomationWorkflow) => rowActions(item) },
  ];
  const scheduleColumns = [
    { title: 'Schedule', width: 280, render: (_: unknown, item: AutomationSchedule) => <div className="automation-name"><FieldTimeOutlined /><span><strong>{item.display_name ?? item.task_name}</strong><small title={item.description || item.task_path}>{item.description || item.task_path}</small></span></div> },
    { title: 'Trigger', width: 210, render: (_: unknown, item: AutomationSchedule) => <div className="automation-trigger"><span>{item.schedule_type === 'interval' ? 'Interval' : 'Cron schedule'}</span><code>{formatSchedule(item)}</code></div> },
    { title: 'Domain', width: 110, render: (_: unknown, item: AutomationSchedule) => domainTag(item.product_domain) },
    { title: 'Status', width: 110, render: (_: unknown, item: AutomationSchedule) => <Badge status={item.enabled ? 'success' : 'default'} text={item.enabled ? 'Enabled' : 'Disabled'} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: AutomationSchedule) => relativeTime(item.updated_at) },
    { title: 'Enabled', width: 82, render: (_: unknown, item: AutomationSchedule) => <span onClick={(event) => event.stopPropagation()}><Switch size="small" checked={item.enabled} loading={togglingSchedule === item.task_name} disabled={mutating || Boolean(togglingSchedule) || item.workspace_task_canceled} title={item.workspace_task_canceled ? 'Canceled runs cannot be scheduled' : undefined} aria-label={`Enable ${item.display_name ?? item.task_name}`} onChange={(checked) => void toggleSchedule(item, checked)} /></span> },
  ];
  const sectionRecords = section === 'workflows' ? filteredWorkflows : filteredSchedules;
  const enabledCount = sectionRecords.filter((item) => item.enabled).length;
  const currentData = section === 'workflows'
    ? filteredWorkflows.filter((item) => scheduleFilter === 'all' || item.enabled === (scheduleFilter === 'enabled'))
    : visibleSchedules;
  const currentColumns = section === 'workflows' ? workflowColumns : scheduleColumns;
  useLayoutEffect(() => {
    const updateTableHeight = () => {
      const card = tableCardRef.current;
      if (!card) return;
      const header = card.querySelector<HTMLElement>('.ui-table-thead');
      const pagination = card.querySelector<HTMLElement>('.ui-pagination');
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
  const scheduleDetail = detail as AutomationSchedule | null;
  const editingLinkedSchedule = Boolean((editing as AutomationSchedule | null)?.workspace_task_id);

  return <ConfigProvider locale={enUS}><div className={`automation-page flow-console--ontology flow-foundry-workbench${isLight ? ' is-light' : ''}`}>
    <header className="automation-header">
      <div className="automation-breadcrumb"><BranchesOutlined /><strong>Automation</strong><span>/</span><span>{sectionMeta[section].label}</span></div>
      <div className="automation-header-actions">
        <span className={`automation-connection${connected && streamAvailable ? ' is-live' : ''}`} role="status"><i />{connected && streamAvailable ? 'Live updates' : 'Connecting…'}</span>
        {section !== 'runs' && <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>New {objectLabel[section].toLowerCase()}</Button>}
      </div>
    </header>
    <div className="automation-workspace">
      <aside className="automation-sidebar" aria-label="Product domain filters">
        <div className="automation-sidebar-title">Product domains</div>
        <nav aria-label="Filter by product domain">
          <button type="button" aria-pressed={domain === 'all'} className={domain === 'all' ? 'is-active' : ''} onClick={() => changeDomain('all')}><span className="automation-domain-mark">∗</span><span>All domains</span></button>
          {Object.entries(domainMeta).map(([value, meta]) => <button type="button" key={value} aria-pressed={domain === value} className={domain === value ? 'is-active' : ''} onClick={() => changeDomain(value as ProductDomain)}><span className="automation-domain-mark">{meta.label.slice(0, 1)}</span><span>{meta.label}</span></button>)}
        </nav>
        <Link className="automation-ontology-link" to="/ontology"><ApartmentOutlined /><span>Open Ontology</span><ArrowRightOutlined /></Link>
      </aside>
      <main className={`automation-main is-${section}`}>
        <div className="automation-page-heading"><div><span className="automation-eyebrow">{domain === 'all' ? 'All domains' : domainMeta[domain].label}</span><h1>{sectionMeta[section].label}</h1><p>{sectionMeta[section].description}</p></div>
          {section !== 'runs' && <div className="automation-summary" aria-label={`${sectionMeta[section].label} summary`} aria-busy={loading}><div><strong>{loading ? '—' : sectionRecords.length}</strong><span>Total</span></div><div><strong>{loading ? '—' : enabledCount}</strong><span>Enabled</span></div><div><strong>{loading ? '—' : sectionRecords.length - enabledCount}</strong><span>Disabled</span></div></div>}
        </div>
        <div className="automation-toolbar">
          <Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} prefix={<SearchOutlined />} aria-label={`Search ${sectionMeta[section].label.toLowerCase()}`} placeholder={`Search ${sectionMeta[section].label.toLowerCase()}…`} className="automation-search" />
          {section !== 'runs' && <div className="automation-status-filters" role="group" aria-label="Filter by status">{(['all', 'enabled', 'paused'] as const).map(value => <button type="button" key={value} aria-pressed={scheduleFilter === value} className={scheduleFilter === value ? 'is-active' : ''} onClick={() => setScheduleFilter(value)}>{value === 'all' ? 'All' : value === 'enabled' ? 'Enabled' : 'Disabled'}</button>)}</div>}
        </div>
        {section !== 'runs' && (!connected || !streamAvailable) && <Alert className="automation-load-alert" type="warning" showIcon message="Live updates unavailable. Reconnecting automatically…" />}
        {section === 'schedules' && (domain === 'all' || domain === 'etl-pipeline') && <TransferSchedules keyword={keyword} filter={scheduleFilter} />}
    {section === 'runs' ? <AutomationRuns domain={domain} keyword={keyword} taskId={searchParams.get('task')} onSelect={selectRun} /> : <Card ref={tableCardRef} className="automation-table-card" styles={{ body: { padding: 0 } }}>
      <Spin spinning={loading}><Table scroll={{ y: tableBodyHeight, x: 900 }} rowKey={(record) => String('id' in record && record.id ? record.id : 'task_name' in record ? record.task_name : record.name)} columns={currentColumns as never} dataSource={currentData as never} pagination={{ pageSize: section === 'schedules' ? 20 : 10, hideOnSinglePage: false, ...(section === 'schedules' ? { showSizeChanger: false } : {}) }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`No ${sectionMeta[section].label.toLowerCase()} match the current filters`} /> }} onRow={(record) => ({ onClick: () => section === 'schedules' ? openScheduleDetail(record as AutomationSchedule) : setDetail(record as SelectedRecord), style: { cursor: 'pointer' } })} /></Spin>
    </Card>}

      </main>
    </div>

    {section === 'workflows' && <Modal className="flow-surface-modal automation-workflow-modal" title={editorTitle} open={editorOpen} confirmLoading={mutating} onCancel={() => setEditorOpen(false)} onOk={() => void save()} okText={editing ? 'Save Changes' : 'Create'} width={940} forceRender>
      <Form form={form} layout="vertical" className="automation-form">
        {section === 'workflows' && <><Form.Item name="name" label="Workflow Name" rules={[{ required: true }]}><Input disabled={Boolean(editing)} /></Form.Item><Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item><Form.Item name="description" label="Description"><Input.TextArea rows={2} /></Form.Item><Form.Item name="nodes" label="Execution steps" rules={[{ validator: async (_, nodes) => { if (!Array.isArray(nodes) || !nodes.length || nodes.some(node => !node.name?.trim())) throw new Error('Give every step a name and include at least one step.'); } }]}><WorkflowNodes /></Form.Item><Form.Item name="enabled" label="Enabled" valuePropName="checked"><Switch /></Form.Item></>}
      </Form>
    </Modal>}

    {section === 'schedules' ? <>
    <Modal
      className={`flow-surface-modal automation-schedule-modal${!editorOpen ? ' is-readonly' : ''}${isLight ? ' is-light' : ''}`}
      rootClassName="automation-schedule-modal-root"
      title={<div className="automation-schedule-modal-heading">
        <span className="automation-schedule-modal-mark" aria-hidden="true"><FieldTimeOutlined /></span>
        <div className="automation-schedule-modal-heading-copy"><span className="automation-schedule-modal-eyebrow">AUTOMATION / SCHEDULES</span><h3>{editorOpen ? editorTitle : detailName}</h3></div>
        <div className="automation-schedule-modal-actions">
          {detail && !editorOpen && <>
              {'workspace_task_id' in detail && detail.workspace_task_id && <Button type="text" icon={<ExportOutlined />} aria-label="View Run" title="View Run" onClick={() => viewRun(detail.workspace_task_id!)} />}
              <Button type="text" icon={<EditOutlined />} aria-label="Edit schedule" title="Edit schedule" disabled={mutating || Boolean(deleteTarget) || Boolean(togglingSchedule) || scheduleDetail?.workspace_task_canceled} onClick={() => openEdit(detail)} />
              {!('workspace_task_id' in detail && detail.workspace_task_id) && <Button type="text" danger icon={<DeleteOutlined />} aria-label="Delete schedule" title="Delete schedule" disabled={mutating || Boolean(deleteTarget)} onClick={() => setDeleteTarget(detail as AutomationSchedule)} />}
            </>}
          <Button type="text" icon={<CloseOutlined />} aria-label="Close schedule" title="Close" disabled={mutating || Boolean(deleteTarget)} onClick={closeScheduleModal} />
        </div>
      </div>}
      open={editorOpen || Boolean(detail)}
      onCancel={closeScheduleModal}
      closable={false}
      maskClosable={!mutating && !deleteTarget}
      keyboard={!mutating && !deleteTarget}
      width={680}
      centered
      footer={editorOpen ? <Space className="automation-schedule-footer">
        <Button disabled={mutating} onClick={() => detail ? openScheduleDetail(detail as AutomationSchedule) : closeScheduleModal()}>Cancel</Button>
        <Button type="primary" loading={mutating} onClick={() => void save()}>{editing ? 'Save Changes' : 'Create Schedule'}</Button>
      </Space> : null}
      forceRender
    >
      <Form form={form} layout="vertical" className="automation-form automation-schedule-fields" disabled={mutating}>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'task_name' : undefined} label="Schedule Name" rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Input disabled={Boolean(editing) || mutating} /> : <span className="automation-schedule-value is-name">{scheduleDetail?.display_name ?? scheduleDetail?.task_name}</span>}
        </Form.Item>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'task_path' : undefined} label="Task Path" rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Input placeholder="app.scheduler.tasks.workspace.dispatch_due" /> : <code className="automation-schedule-value is-code">{scheduleDetail?.task_path || '—'}</code>}
        </Form.Item>
        <Form.Item name={editorOpen && !editingLinkedSchedule ? 'product_domain' : undefined} label="Domain" rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Select popupClassName="flow-select-popup" options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /> : scheduleDetail && domainTag(scheduleDetail.product_domain)}
        </Form.Item>
        {editorOpen && !editingLinkedSchedule && <Form.Item className="is-wide" label="Start from a preset"><Select aria-label="Schedule preset" placeholder="Choose a trigger pattern" options={[
          { value: 'daily', label: 'Daily · 09:00 UTC' }, { value: 'weekdays', label: 'Weekdays · 09:00 UTC' }, { value: 'weekly', label: 'Weekly · Monday 09:00 UTC' }, { value: 'monthly', label: 'Monthly · day 1 at 09:00 UTC' }, { value: 'interval', label: 'Every 30 minutes' },
        ]} onChange={value => form.setFieldsValue({ schedule_type: value === 'interval' ? 'interval' : 'crontab', cron_minute: '0', cron_hour: '9', cron_day_of_week: value === 'weekdays' ? '1-5' : value === 'weekly' ? '1' : '*', cron_day_of_month: value === 'monthly' ? '1' : '*', cron_month_of_year: '*', interval_value: 30, interval_unit: 'minute' })} /></Form.Item>}
        <Form.Item name={editorOpen ? 'schedule_type' : undefined} label="Trigger Type">
          {editorOpen ? <Segmented disabled={mutating} options={[{ value: 'crontab', label: editingLinkedSchedule ? 'Daily' : 'Cron' }, { value: 'interval', label: 'Interval' }]} /> : <span className="automation-schedule-value">{scheduleDetail?.schedule_type === 'interval' ? 'Interval' : scheduleDetail?.workspace_task_id ? 'Daily' : 'Cron'}</span>}
        </Form.Item>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'description' : undefined} label="Description">
          {editorOpen && !editingLinkedSchedule ? <Input.TextArea rows={2} /> : <span className="automation-schedule-value">{scheduleDetail?.description || '—'}</span>}
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(previous, current) => previous.schedule_type !== current.schedule_type}>{({ getFieldValue }) => (editorOpen ? getFieldValue('schedule_type') : scheduleDetail?.schedule_type) === 'interval'
          ? <Form.Item className="is-wide" label="Interval" htmlFor="" required={editorOpen}>
            {editorOpen ? <>
              <div className="automation-schedule-interval-inputs">
                <Form.Item name="interval_value" noStyle rules={[{ required: true, type: 'integer', min: 1, message: 'Enter a positive whole number.' }]}>
                  <InputNumber min={1} precision={0} inputMode="numeric" aria-label="Interval value" />
                </Form.Item>
                <Form.Item name="interval_unit" noStyle rules={[{ required: true }]}>
                  <Select aria-label="Interval unit" popupClassName="flow-select-popup" options={[
                    { value: 'minute', label: 'Minute' }, { value: 'hour', label: 'Hour' },
                    { value: 'day', label: 'Day' }, { value: 'week', label: 'Week' }, { value: 'month', label: 'Month' },
                    ...(!editingLinkedSchedule ? [{ value: 'second', label: 'Second' }] : []),
                  ]} />
                </Form.Item>
              </div>
              <Form.Item noStyle shouldUpdate={(previous, current) => previous.interval_unit !== current.interval_unit}>{({ getFieldValue: getValue }) => getValue('interval_unit') === 'month' ? <span className="automation-schedule-field-hint">1 month = 30 days.</span> : null}</Form.Item>
            </> : <span className="automation-schedule-value">{scheduleDetail && formatSchedule(scheduleDetail)}{scheduleDetail && getScheduleInterval(scheduleDetail).interval_unit === 'month' && <small className="automation-schedule-field-hint">1 month = 30 days.</small>}</span>}
          </Form.Item>
          : <>
            <Form.Item name={editorOpen ? 'cron_minute' : undefined} label="Minute" rules={editorOpen && editingLinkedSchedule ? [{ required: true, pattern: /^([0-5]?\d)$/, message: 'Enter a minute from 0 to 59.' }] : undefined}>
              {editorOpen ? editingLinkedSchedule ? <InputNumber<string> stringMode min="0" max="59" precision={0} inputMode="numeric" parser={(value) => value?.replace(/\D/g, '') ?? ''} style={{ width: '100%' }} /> : <Input /> : <code className="automation-schedule-value is-code">{scheduleDetail?.cron_minute || '*'}</code>}
            </Form.Item>
            <Form.Item name={editorOpen ? 'cron_hour' : undefined} label="Hour" rules={editorOpen && editingLinkedSchedule ? [{ required: true, pattern: /^([01]?\d|2[0-3])$/, message: 'Enter an hour from 0 to 23 (Asia/Shanghai).' }] : undefined}>
              {editorOpen ? editingLinkedSchedule ? <InputNumber<string> stringMode min="0" max="23" precision={0} inputMode="numeric" parser={(value) => value?.replace(/\D/g, '') ?? ''} style={{ width: '100%' }} /> : <Input /> : <code className="automation-schedule-value is-code">{scheduleDetail?.cron_hour || '*'}</code>}
            </Form.Item>
            {!editingLinkedSchedule && <>
              <Form.Item name={editorOpen ? 'cron_day_of_week' : undefined} label="Days of week" extra="0–6 (Sunday–Saturday), * for every day">{editorOpen ? <Input placeholder="1-5" /> : <code>{scheduleDetail?.cron_day_of_week || '*'}</code>}</Form.Item>
              <Form.Item name={editorOpen ? 'cron_day_of_month' : undefined} label="Days of month" extra="1–31, * for every day">{editorOpen ? <Input placeholder="*" /> : <code>{scheduleDetail?.cron_day_of_month || '*'}</code>}</Form.Item>
              <Form.Item name={editorOpen ? 'cron_month_of_year' : undefined} label="Months" extra="1–12, * for every month">{editorOpen ? <Input placeholder="*" /> : <code>{scheduleDetail?.cron_month_of_year || '*'}</code>}</Form.Item>
              <div className="automation-schedule-field-hint">Scheduler timezone: UTC. Lists, ranges and steps are supported, e.g. 1,3,5 or */15.</div>
            </>}
          </>}
        </Form.Item>
        {editorOpen && <Form.Item className="is-wide" noStyle shouldUpdate>{({ getFieldsValue }) => { const values = getFieldsValue(); return <div className="automation-rule-preview"><span>TRIGGER PREVIEW · {editingLinkedSchedule ? 'Asia/Shanghai' : 'UTC'}</span><code>{values.schedule_type === 'interval' ? `Every ${values.interval_value || 1} ${values.interval_unit || 'minute'}(s)` : `${values.cron_minute || '*'} ${values.cron_hour || '*'} ${values.cron_day_of_month || '*'} ${values.cron_month_of_year || '*'} ${values.cron_day_of_week || '*'}`}</code></div>; }}</Form.Item>}
        <Form.Item className="is-wide" label="Status" htmlFor="">
          {editorOpen ? <div className="automation-schedule-status-control">
            <Form.Item name="enabled" noStyle valuePropName="checked"><Switch className="automation-schedule-status-switch" aria-label="Schedule status" /></Form.Item>
            <Form.Item noStyle shouldUpdate={(previous, current) => previous.enabled !== current.enabled}>{({ getFieldValue }) => <div className="automation-schedule-status-copy">
              <span>{getFieldValue('enabled') ? 'Enabled' : 'Disabled'}</span>
              <small>{editingLinkedSchedule
                ? getFieldValue('enabled') ? 'Visible in Runs. Future runs follow this schedule.' : 'Hidden from Runs. No new scheduled runs; the current run can finish.'
                : getFieldValue('enabled') ? 'Future executions follow this schedule.' : 'No new scheduled executions; the current run can finish.'}</small>
            </div>}</Form.Item>
          </div> : <span className={`automation-schedule-status${scheduleDetail?.enabled ? ' is-enabled' : ''}`}><i />{scheduleDetail?.enabled ? 'Enabled' : 'Disabled'}</span>}
        </Form.Item>
      </Form>
    </Modal>
    <Modal
      className={`flow-surface-modal automation-schedule-delete-modal${isLight ? ' is-light' : ''}`}
      rootClassName="automation-schedule-delete-root"
      open={Boolean(deleteTarget)}
      title={<div className="automation-schedule-delete-heading"><span className="automation-schedule-delete-mark" aria-hidden="true"><DeleteOutlined /></span><div className="automation-schedule-delete-heading-copy"><span className="automation-schedule-delete-eyebrow">REMOVE SCHEDULE</span><h3>Delete this schedule?</h3></div><Button type="text" icon={<CloseOutlined />} aria-label="Close delete confirmation" disabled={mutating} onClick={() => setDeleteTarget(null)} /></div>}
      onCancel={() => { if (!mutating) setDeleteTarget(null); }}
      closable={false}
      maskClosable={!mutating}
      keyboard={!mutating}
      width={440}
      zIndex={1100}
      centered
      footer={null}
    >
      <p>This removes the schedule configuration. This action cannot be undone.</p>
      {deleteTarget && <div className="automation-schedule-delete-target"><FieldTimeOutlined /><div><strong>{deleteTarget.display_name ?? deleteTarget.task_name}</strong><span>{(domainMeta[deleteTarget.product_domain] ?? domainMeta.platform).label} · {formatSchedule(deleteTarget)}</span></div></div>}
      <div className="automation-schedule-delete-footer"><Button autoFocus disabled={mutating} onClick={() => setDeleteTarget(null)}>Keep schedule</Button><Button type="primary" danger loading={mutating} onClick={() => void confirmScheduleDelete()}>Delete schedule</Button></div>
    </Modal>
    </> : <Drawer className="flow-surface-modal automation-detail" title={detailName} open={Boolean(detail)} onClose={() => { setDetail(null); if (searchParams.has('task')) { const next = new URLSearchParams(searchParams); next.delete('task'); setSearchParams(next); } }} width={520} extra={detail && section !== 'runs' ? <Button onClick={() => openEdit(detail)}>{'workspace_task_id' in detail ? 'View Run' : 'Edit'}</Button> : null}>
      {detail && <>
        <Descriptions column={1} bordered size="small" items={[
          { key: 'name', label: 'Name', children: detailName },
          { key: 'description', label: 'Description', children: detail.description || 'No description' },
          { key: 'domain', label: 'Domain', children: domainTag(detail.product_domain) },
          { key: 'status', label: 'Status', children: <Badge status={detail.enabled ? 'success' : 'default'} text={detail.enabled ? 'Enabled' : 'Disabled'} /> },
          { key: 'updated', label: 'Updated', children: detail.updated_at ? new Date(detail.updated_at).toLocaleString() : '—' },
        ]} />
        {section === 'workflows' && <section className="automation-inspector-section"><h3>Execution sequence</h3><WorkflowPath nodes={(detail as AutomationWorkflow).nodes} /></section>}
      </>}
    </Drawer>}
  </div></ConfigProvider>;
};

export default AutomationCenter;

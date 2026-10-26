import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, App, Badge, Button, Card, ConfigProvider, Descriptions, Drawer, Empty, Form, Input,
  InputNumber, Modal, Popconfirm, Segmented, Select, Space, Spin, Switch,
  Table, Tag, Timeline, Tooltip, Typography,
} from 'antd';
import enUS from 'antd/locale/en_US';
import {
  ApartmentOutlined, ClockCircleOutlined, CloseOutlined, DeleteOutlined,
  EditOutlined, ExportOutlined, FieldTimeOutlined, GlobalOutlined,
  LockOutlined, PlusOutlined, SearchOutlined,
} from '@ant-design/icons';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  aiDeleteWorkspaceTask,
  automationCreateWorkflow, automationDeleteWorkflow,
  automationUpdateWorkflow,
} from '@/services/api';
import { AI_ANALYZE_WS_URL, fetchWorkspaceTaskDataTypes } from '@/services/aiApi';
import {
  automationCreateSchedule, automationDeleteSchedule,
  automationReloadSchedules, automationToggleSchedule, automationUpdateSchedule, automationUpdateWorkspaceSchedule,
  getScheduleInterval, intervalUnitSeconds, mapWorkspaceSchedules, type AutomationSchedule, type IntervalUnit,
} from '@/services/scheduler';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useThemeStore } from '@/stores/settings';
import WorkspaceDock, { inferSiteKind, TemplateGlyph } from '@/pages/AICollect/WorkspaceDock';
import type {
  AutomationWorkflow, ProductDomain, SchedulerTaskConfig, WorkspaceTask,
} from '@/services/types';
import './style.css';

type Section = 'workflows' | 'schedules' | 'runs';
type DomainFilter = ProductDomain | 'all';
type ScheduleFilter = 'all' | 'enabled' | 'paused';
type SelectedRecord = AutomationWorkflow | AutomationSchedule;

const domainMeta: Record<ProductDomain, { label: string; color: string; className: string; accent: string }> = {
  'ai-collect': { label: 'Scout', color: 'purple', className: 'is-ai', accent: '#7C3AED' },
  'data-lake': { label: 'Vault', color: 'green', className: 'is-lake', accent: '#059669' },
  'etl-pipeline': { label: 'Flow', color: 'blue', className: 'is-etl', accent: '#0EA5E9' },
  'data-cockpit': { label: 'Atlas', color: 'gold', className: 'is-cockpit', accent: '#F59E0B' },
  'knowledge-graph': { label: 'Graph', color: 'purple', className: 'is-graph', accent: '#8B5CF6' },
  'knowledge-rag': { label: 'RAG', color: 'cyan', className: 'is-rag', accent: '#0D9488' },
  platform: { label: 'Platform', color: 'default', className: 'is-platform', accent: '#64748B' },
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

const formatSchedule = (item: AutomationSchedule) => {
  if (item.schedule_label) return item.schedule_label;
  if (item.schedule_type === 'interval') {
    const { interval_value: value, interval_unit: unit } = getScheduleInterval(item);
    return `Every ${value} ${unit}${value === 1 ? '' : 's'}`;
  }
  return `${item.cron_minute} ${item.cron_hour} ${item.cron_day_of_month} ${item.cron_month_of_year} ${item.cron_day_of_week}`;
};

const formatDateTime = (value?: string) => {
  if (!value) return '—';
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
};

const isLinkedSchedule = (record: SelectedRecord): record is AutomationSchedule =>
  'workspace_task_id' in record && Boolean(record.workspace_task_id);

const extractErrorMessage = (error: unknown, fallback: string) => {
  const data = (error as { response?: { data?: { detail?: unknown } } })?.response?.data;
  return typeof data?.detail === 'string' && data.detail ? data.detail : fallback;
};

const workspaceStatusMeta: Record<string, { label: string; color: string }> = {
  queued: { label: 'Queued', color: 'default' },
  running: { label: 'Running', color: 'processing' },
  completed: { label: 'Completed', color: 'success' },
  failed: { label: 'Failed', color: 'error' },
  paused: { label: 'Paused', color: 'warning' },
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
  const dataType = searchParams.get('data_type') || undefined;
  const [dataTypes, setDataTypes] = useState<string[]>([]);
  const [dataTypesLoading, setDataTypesLoading] = useState(false);
  const [dataTypesError, setDataTypesError] = useState<string | null>(null);
  const [dataTypesRetry, setDataTypesRetry] = useState(0);
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

  useEffect(() => {
    if (section !== 'runs') return;
    let active = true;
    setDataTypesLoading(true);
    setDataTypesError(null);
    fetchWorkspaceTaskDataTypes()
      .then((items) => { if (active) setDataTypes(items); })
      .catch((error) => {
        if (active) setDataTypesError(extractErrorMessage(error, 'Unable to load data types'));
      })
      .finally(() => { if (active) setDataTypesLoading(false); });
    return () => { active = false; };
  }, [dataTypesRetry, section]);

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
    next.delete('data_type');
    setSearchParams(next);
  };
  const changeDataType = (value?: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set('data_type', value);
    else next.delete('data_type');
    setSearchParams(next);
  };

  const domainTag = (value: ProductDomain) => <Tag color={(domainMeta[value] ?? domainMeta.platform).color}>{(domainMeta[value] ?? domainMeta.platform).label}</Tag>;
  const matches = (text: string) => text.toLowerCase().includes(keyword.trim().toLowerCase());
  const filteredWorkflows = useMemo(() => workflows.filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.name} ${item.description}`)), [workflows, domain, keyword]);
  const filteredSchedules = useMemo(() => [...workspaceSchedules, ...schedules].filter((item) =>
    (domain === 'all' || item.product_domain === domain) && matches(`${item.display_name ?? item.task_name} ${item.task_path} ${item.description ?? ''}`)), [workspaceSchedules, schedules, domain, keyword]);
  const enabledScheduleCount = filteredSchedules.filter((item) => item.enabled).length;
  const visibleSchedules = filteredSchedules.filter((item) => scheduleFilter === 'all' || item.enabled === (scheduleFilter === 'enabled'));
  const scheduleDomain = (value: ProductDomain) => {
    const meta = domainMeta[value] ?? domainMeta.platform;
    return <span className="automation-schedule-domain"><span className="automation-domain-initial" style={{ background: `linear-gradient(135deg, ${meta.accent}, #1D4ED8)` }} aria-hidden="true">{meta.label.slice(0, 1)}</span>{meta.label}</span>;
  };

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
    form.setFieldsValue({ product_domain: domain === 'all' ? 'ai-collect' : domain, enabled: true, schedule_type: 'crontab', cron_minute: '0', cron_hour: '2', interval_value: 1, interval_unit: 'minute', nodes: '' });
    setEditorOpen(true);
  };

  const openEdit = (record: SelectedRecord) => {
    setEditing(record);
    if (section === 'workflows') {
      const item = record as AutomationWorkflow;
      form.setFieldsValue({ ...item, nodes: item.nodes.map((node) => node.name).join('\n') });
    } else if (section === 'schedules') {
      const schedule = record as AutomationSchedule;
      setDetail(schedule);
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
          nodes: String(values.nodes ?? '').split(/\r?\n|,/).map((name) => name.trim()).filter(Boolean).map((name) => ({ name })),
          enabled: values.enabled,
        };
        if (editing) await automationUpdateWorkflow((editing as AutomationWorkflow).name, payload);
        else await automationCreateWorkflow(payload);
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
            cron_hour: values.cron_hour ?? existing?.cron_hour ?? '*', cron_day_of_week: existing?.cron_day_of_week ?? '*', cron_day_of_month: existing?.cron_day_of_month ?? '*',
            cron_month_of_year: existing?.cron_month_of_year ?? '*', interval_seconds: values.schedule_type === 'interval' ? values.interval_value * intervalUnitSeconds[values.interval_unit as IntervalUnit] : existing?.interval_seconds,
            args: existing?.args ?? [], kwargs: existing?.kwargs ?? {}, options: existing?.options ?? {}, enabled: values.enabled,
          };
          const saved = editing
            ? await automationUpdateSchedule((editing as SchedulerTaskConfig).task_name, payload)
            : await automationCreateSchedule(payload);
          const reload = await automationReloadSchedules();
          if (!reload.loaded || reload.restart_required) message.warning(reload.message);
          setDetail(saved);
          form.setFieldsValue(saved);
        }
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
      if (section === 'workflows') {
        await automationDeleteWorkflow((record as AutomationWorkflow).name);
        setWorkflows((current) => current.filter((item) => item.name !== (record as AutomationWorkflow).name));
      } else if (section === 'schedules') {
        const schedule = record as AutomationSchedule;
        if (schedule.workspace_task_id) {
          await aiDeleteWorkspaceTask(schedule.workspace_task_id);
          setWorkspaceSchedules((current) => current.filter((item) => item.workspace_task_id !== schedule.workspace_task_id));
        } else {
          await automationDeleteSchedule(schedule.task_name);
          const reload = await automationReloadSchedules();
          if (!reload.loaded || reload.restart_required) message.warning(reload.message);
          setSchedules((current) => current.filter((item) => item.task_name !== schedule.task_name));
        }
      }
      setDetail((current) => (current === record ? null : current));
      setDeleteTarget((current) => (current === record ? null : current));
      message.success(isLinkedSchedule(record) ? 'Collection task deleted' : 'Deleted');
    } catch (error) {
      message.error(extractErrorMessage(error, 'Unable to delete. Please try again.'));
    } finally { setMutating(false); }
  };

  const confirmScheduleDelete = async () => {
    if (!deleteTarget || mutating) return;
    await remove(deleteTarget);
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

  const rowActions = (record: SelectedRecord) => (
    <Space size={2}>
      <Button type="text" icon={<EditOutlined />} aria-label="Edit" onClick={(event) => { event.stopPropagation(); openEdit(record); }} />
      <Popconfirm title="Delete this object?" onConfirm={() => void remove(record)}><Button type="text" danger icon={<DeleteOutlined />} aria-label="Delete" onClick={(event) => event.stopPropagation()} /></Popconfirm>
    </Space>
  );

  const workflowColumns = [
    { title: 'Workflow', dataIndex: 'name', render: (_: string, item: AutomationWorkflow) => <div className="automation-name"><ApartmentOutlined /><span><strong>{item.name}</strong><small>{item.description || 'No description'}</small></span></div> },
    { title: 'Domain', width: 140, render: (_: unknown, item: AutomationWorkflow) => domainTag(item.product_domain) },
    { title: 'Nodes', width: 100, render: (_: unknown, item: AutomationWorkflow) => `${item.nodes.length} nodes` },
    { title: 'Status', width: 110, render: (_: unknown, item: AutomationWorkflow) => <Badge status={item.enabled ? 'success' : 'default'} text={item.enabled ? 'Enabled' : 'Disabled'} /> },
    { title: 'Updated', width: 110, render: (_: unknown, item: AutomationWorkflow) => relativeTime(item.updated_at) },
    { title: '', width: 82, render: (_: unknown, item: AutomationWorkflow) => rowActions(item) },
  ];
  const scheduleColumns = [{ key: 'schedule', render: (_: unknown, item: AutomationSchedule) => (
    <article className={`automation-schedule-row ${item.enabled ? 'is-enabled' : 'is-paused'}`}>
      <div className="automation-schedule-main">
        <span className="automation-schedule-icon" aria-hidden="true">{item.schedule_type === 'interval' ? <ClockCircleOutlined /> : <FieldTimeOutlined />}</span>
        <div className="automation-schedule-heading">
          <button type="button" className="automation-schedule-title" title={item.display_name ?? item.task_name} onClick={(event) => { event.stopPropagation(); openScheduleDetail(item); }}>{item.display_name ?? item.task_name}</button>
          <span title={item.description || item.task_path}>{item.description || item.task_path}</span>
        </div>
        <span className={`automation-schedule-status ${item.enabled ? 'is-enabled' : ''}`}><i />{item.enabled ? 'Enabled' : 'Disabled'}</span>
      </div>
      <div className="automation-schedule-meta">
        {scheduleDomain(item.product_domain)}
        <span className={`automation-schedule-trigger ${item.schedule_type === 'interval' ? 'is-interval' : 'is-cron'}`}><span>{item.schedule_type === 'interval' ? 'Interval' : 'Cron'}</span><code title={formatSchedule(item)}>{formatSchedule(item)}</code></span>
        {item.workspace_task_id
          ? <Link className="automation-schedule-source is-linked" to={`/automation/runs?task=${encodeURIComponent(item.workspace_task_id)}`} onClick={(event) => event.stopPropagation()}>Linked run</Link>
          : <span className="automation-schedule-source">Scheduled task</span>}
        <span className="automation-schedule-updated">Updated {relativeTime(item.updated_at)}</span>
      </div>
      <div className="automation-schedule-actions" onClick={(event) => event.stopPropagation()}>
        <Switch size="small" checked={item.enabled} loading={togglingSchedule === item.task_name} disabled={mutating || Boolean(togglingSchedule) || item.workspace_task_canceled} title={item.workspace_task_canceled ? 'Canceled runs cannot be scheduled' : undefined} aria-label={`Enable ${item.display_name ?? item.task_name}`} onChange={(checked) => void toggleSchedule(item, checked)} />
      </div>
    </article>
  ) }];
  const currentData = section === 'workflows' ? filteredWorkflows : visibleSchedules;
  const currentColumns = section === 'workflows' ? workflowColumns : scheduleColumns;
  useLayoutEffect(() => {
    const updateTableHeight = () => {
      const card = tableCardRef.current;
      if (!card) return;
      const header = card.querySelector<HTMLElement>('.ant-table-thead');
      const toolbar = card.querySelector<HTMLElement>('.automation-schedule-toolbar');
      const pagination = card.querySelector<HTMLElement>('.ant-pagination');
      const paginationStyle = pagination ? getComputedStyle(pagination) : null;
      const paginationMargins = paginationStyle
        ? parseFloat(paginationStyle.marginTop) + parseFloat(paginationStyle.marginBottom)
        : 0;
      const fixedHeight = (toolbar?.offsetHeight ?? 0) + (header?.offsetHeight ?? 0) + (pagination?.offsetHeight ?? 0) + paginationMargins + 2;
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
  const linkedLockHint = <span className="automation-schedule-readonly-hint"><LockOutlined />Managed by the linked collection task</span>;
  const lockedValueClass = editorOpen && editingLinkedSchedule ? ' is-locked' : '';

  return <ConfigProvider locale={enUS}><div className={`automation-page${section === 'schedules' ? ` is-schedules${isLight ? ' is-light' : ''}` : ''}`}>
    <header className="automation-header">
      <div><Typography.Text className="automation-kicker">AUTOMATION FOUNDATION</Typography.Text><Typography.Title level={2}>Automation Center</Typography.Title></div>
    </header>

    <div className="access-tabs automation-tabs">
      <Segmented value={section} onChange={(value) => changeSection(String(value))} options={[
        { label: 'Workflows', value: 'workflows', icon: <ApartmentOutlined /> },
        { label: 'Schedules', value: 'schedules', icon: <FieldTimeOutlined /> }, { label: 'Runs', value: 'runs', icon: <ClockCircleOutlined /> },
      ]} />
      <Space wrap className="automation-filters">
        <Select<DomainFilter>
          value={domain}
          placeholder="Select domain"
          onChange={changeDomain}
          className="automation-domain-select"
          popupClassName="automation-filter-dropdown"
          options={[{ value: 'all' as const, label: 'All', accent: '#64748B' }, ...Object.entries(domainMeta).map(([value, meta]) => ({ value: value as ProductDomain, ...meta }))].map(({ value, label, accent }) => ({
            value,
            label: <span className="automation-filter-option"><span className="automation-domain-initial" style={{ background: `linear-gradient(135deg, ${accent}, #1D4ED8)` }} aria-hidden="true">{label.slice(0, 1)}</span><span>{label}</span></span>,
          }))}
        />
        {section === 'runs' && <Select<string>
          allowClear
          loading={dataTypesLoading}
          value={dataType}
          placeholder="Select data type"
          onChange={changeDataType}
          className="automation-data-type-select"
          popupClassName="automation-filter-dropdown"
          options={dataTypes.map((value) => ({ value, label: <span className="automation-filter-option"><TemplateGlyph kind={inferSiteKind(value)} /><span>{value}</span></span> }))}
        />}
        <Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} prefix={<SearchOutlined />} placeholder={section === 'schedules' ? 'Search schedules or tasks' : 'Search name, type, or owner'} className="automation-search" />
        {section !== 'runs' && <Button icon={<PlusOutlined />} onClick={openCreate}>{section === 'schedules' ? 'New Schedule' : 'New Record'}</Button>}
      </Space>
    </div>

    {section === 'workflows' && (!connected || !streamAvailable) && <Alert className="automation-load-alert" type="warning" showIcon message="Live updates unavailable. Reconnecting automatically…" />}
    {section === 'runs' && dataTypesError && <Alert className="automation-load-alert" type="error" showIcon message={dataTypesError} action={<Button size="small" onClick={() => setDataTypesRetry((current) => current + 1)}>Retry</Button>} />}

    {section === 'runs' ? <Card className="automation-task-panel" styles={{ body: { padding: 0 } }}>
      <WorkspaceDock
        activePanel="tasks"
        embedded
        taskDomain={domain}
        taskDataType={dataType}
        taskKeyword={keyword}
        taskId={searchParams.get('task')}
        onTaskSelect={selectRun}
        onToggle={() => changeSection('runs')}
        onClose={() => selectRun(null)}
      />
    </Card> : <Card ref={tableCardRef} className="automation-table-card" styles={{ body: { padding: 0 } }}>
      {section === 'schedules' && <div className="automation-schedule-toolbar">
        <div className="automation-schedule-caption"><span className={`automation-schedule-connection ${connected && streamAvailable ? 'is-live' : ''}`} role="status"><i />{connected && streamAvailable ? 'Live updates' : 'Reconnecting automatically…'}</span><span>{filteredSchedules.length} schedules</span></div>
        <Segmented block value={scheduleFilter} onChange={(value) => setScheduleFilter(value as ScheduleFilter)} options={[
          { value: 'all', label: <span>All <small>{filteredSchedules.length}</small></span> },
          { value: 'enabled', label: <span>Enabled <small>{enabledScheduleCount}</small></span> },
          { value: 'paused', label: <span>Disabled <small>{filteredSchedules.length - enabledScheduleCount}</small></span> },
        ]} />
      </div>}
      <Spin spinning={loading}><Table showHeader={section !== 'schedules'} scroll={{ y: tableBodyHeight }} rowKey={(record) => String('id' in record && record.id ? record.id : 'task_name' in record ? record.task_name : record.name)} columns={currentColumns as never} dataSource={currentData as never} pagination={{ pageSize: section === 'schedules' ? 20 : 10, hideOnSinglePage: true, ...(section === 'schedules' ? { showSizeChanger: false } : {}) }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`No ${sectionMeta[section].label.toLowerCase()} match the current filters`} /> }} onRow={(record) => ({ onClick: () => section === 'schedules' ? openScheduleDetail(record as AutomationSchedule) : setDetail(record as SelectedRecord), style: { cursor: 'pointer' } })} /></Spin>
    </Card>}

    {section === 'workflows' && <Modal
      className={`automation-schedule-modal${isLight ? ' is-light' : ''}`}
      rootClassName="automation-schedule-modal-root"
      title={<div className="automation-schedule-modal-heading">
        <span className="automation-schedule-modal-mark" aria-hidden="true"><ApartmentOutlined /></span>
        <div className="automation-schedule-modal-heading-copy"><span className="automation-schedule-modal-eyebrow">AUTOMATION / WORKFLOWS</span><h3>{editorTitle}</h3></div>
        <div className="automation-schedule-modal-actions"><Button type="text" icon={<CloseOutlined />} aria-label="Close workflow editor" disabled={mutating} onClick={() => setEditorOpen(false)} /></div>
      </div>}
      open={editorOpen}
      onCancel={() => { if (!mutating) setEditorOpen(false); }}
      closable={false}
      maskClosable={!mutating}
      keyboard={!mutating}
      width={640}
      centered
      forceRender
      footer={<div className="automation-schedule-footer">
        <Button disabled={mutating} onClick={() => setEditorOpen(false)}>Cancel</Button>
        <Button type="primary" loading={mutating} onClick={() => void save()}>{editing ? 'Save Changes' : 'Create Workflow'}</Button>
      </div>}
    >
      <Form form={form} layout="vertical" className="automation-form automation-schedule-fields" disabled={mutating}>
        <div className="automation-schedule-section is-wide"><span>Workflow</span><i /></div>
        <Form.Item className="is-wide" name="name" label="Workflow Name" rules={[{ required: true }]} extra={editing ? 'Unique identifier — the workflow name cannot be changed after creation.' : undefined}><Input disabled={Boolean(editing)} /></Form.Item>
        <Form.Item name="product_domain" label="Domain" rules={[{ required: true }]}><Select popupClassName="automation-filter-dropdown" options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item>
        <Form.Item name="enabled" label="Status" valuePropName="checked" className="automation-schedule-switch-item"><Switch aria-label="Workflow status" /></Form.Item>
        <div className="automation-schedule-section is-wide"><span>Pipeline</span><i /></div>
        <Form.Item className="is-wide" name="description" label="Description"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item className="is-wide" name="nodes" label="Execution Nodes" extra="One node per line, executed in order"><Input.TextArea rows={6} placeholder={'Collect data\nNormalize fields\nWrite to ODS'} /></Form.Item>
      </Form>
    </Modal>}

    {section === 'schedules' ? <>
    <Modal
      className={`automation-schedule-modal${!editorOpen ? ' is-readonly' : ''}${isLight ? ' is-light' : ''}`}
      rootClassName="automation-schedule-modal-root"
      title={<div className="automation-schedule-modal-heading">
        <span className="automation-schedule-modal-mark" aria-hidden="true"><FieldTimeOutlined /></span>
        <div className="automation-schedule-modal-heading-copy"><span className="automation-schedule-modal-eyebrow">AUTOMATION / SCHEDULES</span><h3>{editorOpen ? editorTitle : detailName}</h3></div>
        <div className="automation-schedule-modal-actions">
          {detail && !editorOpen && <>
              {isLinkedSchedule(detail) && <Button type="text" icon={<ExportOutlined />} aria-label="View Run" title="View Run" onClick={() => viewRun(detail.workspace_task_id!)} />}
              <Tooltip title={scheduleDetail?.workspace_task_canceled ? 'Canceled runs cannot be rescheduled' : 'Edit schedule'}>
                <Button type="text" icon={<EditOutlined />} aria-label="Edit schedule" disabled={mutating || Boolean(deleteTarget) || Boolean(togglingSchedule) || scheduleDetail?.workspace_task_canceled} onClick={() => openEdit(detail)} />
              </Tooltip>
              <Button type="text" danger icon={<DeleteOutlined />} aria-label="Delete schedule" title={isLinkedSchedule(detail) ? 'Delete collection task' : 'Delete schedule'} disabled={mutating || Boolean(deleteTarget)} onClick={() => setDeleteTarget(detail as AutomationSchedule)} />
            </>}
          <Button type="text" icon={<CloseOutlined />} aria-label="Close schedule" title="Close" disabled={mutating || Boolean(deleteTarget)} onClick={closeScheduleModal} />
        </div>
      </div>}
      open={editorOpen || Boolean(detail)}
      onCancel={closeScheduleModal}
      closable={false}
      maskClosable={!mutating && !deleteTarget}
      keyboard={!mutating && !deleteTarget}
      width={720}
      centered
      footer={editorOpen ? <div className="automation-schedule-footer">
        <span className="automation-schedule-footer-note"><GlobalOutlined />Times are interpreted in Asia/Shanghai</span>
        <Button disabled={mutating} onClick={() => detail ? openScheduleDetail(detail as AutomationSchedule) : closeScheduleModal()}>Cancel</Button>
        <Button type="primary" loading={mutating} onClick={() => void save()}>{editing ? 'Save Changes' : 'Create Schedule'}</Button>
      </div> : null}
      forceRender
    >
      <Form form={form} layout="vertical" className="automation-form automation-schedule-fields" disabled={mutating}>
        <div className="automation-schedule-section is-wide"><span>Task</span><i /></div>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'task_name' : undefined} label="Schedule Name"
          extra={editorOpen ? (editingLinkedSchedule ? linkedLockHint : editing ? 'Unique identifier — the schedule name cannot be changed after creation.' : undefined) : undefined}
          rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Input disabled={Boolean(editing) || mutating} /> : <span className={`automation-schedule-value is-name${lockedValueClass}`}>{editorOpen && editingLinkedSchedule && <LockOutlined />}{scheduleDetail?.display_name ?? scheduleDetail?.task_name}</span>}
        </Form.Item>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'task_path' : undefined} label="Task Path"
          extra={editorOpen && editingLinkedSchedule ? linkedLockHint : undefined}
          rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Input placeholder="app.scheduler.tasks.workspace.dispatch_due" /> : <code className={`automation-schedule-value is-code${lockedValueClass}`}>{editorOpen && editingLinkedSchedule && <LockOutlined />}{scheduleDetail?.task_path || '—'}</code>}
        </Form.Item>
        <Form.Item className="is-wide" name={editorOpen && !editingLinkedSchedule ? 'description' : undefined} label="Description"
          extra={editorOpen && editingLinkedSchedule ? linkedLockHint : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Input.TextArea rows={2} /> : <span className={`automation-schedule-value${lockedValueClass}`}>{editorOpen && editingLinkedSchedule && <LockOutlined />}{scheduleDetail?.description || '—'}</span>}
        </Form.Item>
        <div className="automation-schedule-section is-wide"><span>Trigger</span><i /></div>
        <Form.Item name={editorOpen && !editingLinkedSchedule ? 'product_domain' : undefined} label="Domain"
          extra={editorOpen && editingLinkedSchedule ? linkedLockHint : undefined}
          rules={editorOpen && !editingLinkedSchedule ? [{ required: true }] : undefined}>
          {editorOpen && !editingLinkedSchedule ? <Select popupClassName="automation-filter-dropdown" options={Object.entries(domainMeta).map(([value, meta]) => ({ value, label: meta.label }))} /> : <span className={`automation-schedule-value${lockedValueClass}`}>{editorOpen && editingLinkedSchedule && <LockOutlined />}{scheduleDetail && scheduleDomain(scheduleDetail.product_domain)}</span>}
        </Form.Item>
        <Form.Item name={editorOpen ? 'schedule_type' : undefined} label="Trigger Type">
          {editorOpen ? <Segmented disabled={mutating} options={[{ value: 'crontab', label: editingLinkedSchedule ? 'Daily' : 'Cron' }, { value: 'interval', label: 'Interval' }]} /> : <span className="automation-schedule-value">{scheduleDetail?.schedule_type === 'interval' ? 'Interval' : scheduleDetail?.workspace_task_id ? 'Daily' : 'Cron'}</span>}
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(previous, current) => previous.schedule_type !== current.schedule_type}>{({ getFieldValue }) => (editorOpen ? getFieldValue('schedule_type') : scheduleDetail?.schedule_type) === 'interval'
          ? <Form.Item className="is-wide" label="Interval" htmlFor="" required={editorOpen}>
            {editorOpen ? <>
              <div className="automation-schedule-interval-inputs">
                <Form.Item name="interval_value" noStyle rules={[{ required: true, type: 'integer', min: 1, message: 'Enter a positive whole number.' }]}>
                  <InputNumber min={1} precision={0} inputMode="numeric" aria-label="Interval value" />
                </Form.Item>
                <Form.Item name="interval_unit" noStyle rules={[{ required: true }]}>
                  <Select aria-label="Interval unit" popupClassName="automation-filter-dropdown" options={[
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
          </>}
        </Form.Item>
        <div className="automation-schedule-section is-wide"><span>Options</span><i /></div>
        <Form.Item className="is-wide" label="Status" htmlFor="">
          {editorOpen ? <div className="automation-schedule-status-control">
            <Form.Item name="enabled" noStyle valuePropName="checked"><Switch className="automation-schedule-status-switch" aria-label="Schedule status" /></Form.Item>
            <Form.Item noStyle shouldUpdate={(previous, current) => previous.enabled !== current.enabled}>{({ getFieldValue }) => <div className="automation-schedule-status-copy">
              <span>{getFieldValue('enabled') ? 'Enabled' : 'Disabled'}</span>
              <small>{editingLinkedSchedule
                ? getFieldValue('enabled') ? 'Visible in Runs. Future runs follow this schedule.' : 'Hidden from Runs. No new scheduled runs; a running execution will be stopped.'
                : getFieldValue('enabled') ? 'Future executions follow this schedule.' : 'No new scheduled executions; a running execution will be stopped.'}</small>
            </div>}</Form.Item>
          </div> : <span className={`automation-schedule-status${scheduleDetail?.enabled ? ' is-enabled' : ''}`}><i />{scheduleDetail?.enabled ? 'Enabled' : 'Disabled'}</span>}
        </Form.Item>
      </Form>
      {!editorOpen && scheduleDetail && <div className="automation-schedule-meta-grid">
        <div><span className="automation-schedule-meta-label">Source</span><span className="automation-schedule-meta-value">{isLinkedSchedule(scheduleDetail) ? `Linked collection task${scheduleDetail.workspace_task_owner ? ` · ${scheduleDetail.workspace_task_owner}` : ''}` : 'Standalone schedule'}</span></div>
        {isLinkedSchedule(scheduleDetail) && (() => {
          const statusMeta = workspaceStatusMeta[scheduleDetail.workspace_task_status ?? ''];
          return <div><span className="automation-schedule-meta-label">Run Status</span><span className="automation-schedule-meta-value">{statusMeta ? <Badge status={statusMeta.color as never} text={statusMeta.label} /> : '—'}</span></div>;
        })()}
        <div><span className="automation-schedule-meta-label">Created</span><span className="automation-schedule-meta-value">{formatDateTime(scheduleDetail.created_at)}</span></div>
        <div><span className="automation-schedule-meta-label">Updated</span><span className="automation-schedule-meta-value">{formatDateTime(scheduleDetail.updated_at)}</span></div>
      </div>}
    </Modal>
    <Modal
      className={`automation-schedule-delete-modal${isLight ? ' is-light' : ''}`}
      rootClassName="automation-schedule-delete-root"
      open={Boolean(deleteTarget)}
      title={<div className="automation-schedule-delete-heading"><span className="automation-schedule-delete-mark" aria-hidden="true"><DeleteOutlined /></span><div className="automation-schedule-delete-heading-copy"><span className="automation-schedule-delete-eyebrow">{deleteTarget && isLinkedSchedule(deleteTarget) ? 'REMOVE COLLECTION TASK' : 'REMOVE SCHEDULE'}</span><h3>{deleteTarget && isLinkedSchedule(deleteTarget) ? 'Delete this collection task?' : 'Delete this schedule?'}</h3></div><Button type="text" icon={<CloseOutlined />} aria-label="Close delete confirmation" disabled={mutating} onClick={() => setDeleteTarget(null)} /></div>}
      onCancel={() => { if (!mutating) setDeleteTarget(null); }}
      closable={false}
      maskClosable={!mutating}
      keyboard={!mutating}
      width={440}
      zIndex={1100}
      centered
      footer={null}
    >
      <p>{deleteTarget && isLinkedSchedule(deleteTarget)
        ? `This removes the collection task, its schedule and all run records.${deleteTarget.workspace_task_status === 'running' ? ' The running execution will be canceled.' : deleteTarget.workspace_task_status === 'paused' ? ' Its paused run will be canceled.' : ''} This action cannot be undone.`
        : 'This removes the schedule configuration. This action cannot be undone.'}</p>
      {deleteTarget && <div className="automation-schedule-delete-target"><FieldTimeOutlined /><div><strong>{deleteTarget.display_name ?? deleteTarget.task_name}</strong><span>{(domainMeta[deleteTarget.product_domain] ?? domainMeta.platform).label} · {formatSchedule(deleteTarget)}</span></div></div>}
      <div className="automation-schedule-delete-footer"><Button autoFocus disabled={mutating} onClick={() => setDeleteTarget(null)}>{deleteTarget && isLinkedSchedule(deleteTarget) ? 'Keep task' : 'Keep schedule'}</Button><Button type="primary" danger loading={mutating} onClick={() => void confirmScheduleDelete()}>{deleteTarget && isLinkedSchedule(deleteTarget) ? 'Delete task' : 'Delete schedule'}</Button></div>
    </Modal>
    </> : <Drawer title={detailName} open={Boolean(detail)} onClose={() => { setDetail(null); if (searchParams.has('task')) { const next = new URLSearchParams(searchParams); next.delete('task'); setSearchParams(next); } }} width={520} extra={detail && section !== 'runs' ? <Button onClick={() => openEdit(detail)}>{'workspace_task_id' in detail ? 'View Run' : 'Edit'}</Button> : null}>
      {detail && <>
        <Descriptions column={1} bordered size="small" items={Object.entries(detail).filter(([key]) => !['yaml_content', 'metadata', 'parameters', 'policies', 'logs'].includes(key)).slice(0, 12).map(([key, value]) => ({ key, label: key, children: typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—') }))} />
        {section === 'workflows' && <><Typography.Title level={5} style={{ marginTop: 24 }}>Execution Path</Typography.Title><Timeline items={(detail as AutomationWorkflow).nodes.map((node, index) => ({ color: index === 0 ? 'blue' : 'gray', children: node.name }))} /></>}
      </>}
    </Drawer>}
  </div></ConfigProvider>;
};

export default AutomationCenter;

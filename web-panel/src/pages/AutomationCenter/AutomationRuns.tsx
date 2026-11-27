import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, Badge, Button, Empty, Input, Popconfirm, Select, Table } from '@/components/ui';
import { CloseOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { fetchWorkspaceTasks, fetchWorkspaceTask, fetchWorkspaceTaskLogs, fetchWorkspaceTaskLogRuns, runWorkspaceTaskAction, getAiAnalyzeWsUrl, type WorkspaceTask, type WorkspaceTaskLog, type WorkspaceTaskLogRun } from '@/services/aiApi';
import type { ProductDomain } from '@/services/types';
import { useWebSocket } from '@/hooks/useWebSocket';
import { mergeRunLogs, runLogPresentation } from './runLogEvents';
import { workerNoticeStatusLabel } from '@/pages/AICollect/workerNotices';
import { renderConsoleLogMessage } from '@/pages/AICollect/WorkspaceDock';

const statuses = ['all', 'running', 'queued', 'paused', 'completed', 'failed', 'canceled'] as const;
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—';
const status = (value: string) => <Badge status={value === 'failed' ? 'error' : value === 'completed' ? 'success' : value === 'running' ? 'processing' : 'default'} text={value} />;
const duration = (run?: WorkspaceTaskLogRun | null) => {
  if (!run) return '—';
  const seconds = Math.max(0, Math.round((Date.parse(run.ended_at || new Date().toISOString()) - Date.parse(run.started_at)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
};

export default function AutomationRuns({ domain, keyword, onKeywordChange, taskId, onSelect }: {
  domain: ProductDomain | 'all'; keyword: string; onKeywordChange: (value: string) => void; taskId: string | null; onSelect: (id: string | null) => void;
}) {
  const { message } = App.useApp();
  const [acting, setActing] = useState(false);
  const [tasks, setTasks] = useState<WorkspaceTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [dataType, setDataType] = useState<string>();
  const [filter, setFilter] = useState<string>('all');
  const [detail, setDetail] = useState<WorkspaceTask>();
  const [detailError, setDetailError] = useState('');
  const [runs, setRuns] = useState<WorkspaceTaskLogRun[]>([]);
  const [runId, setRunId] = useState<string>();
  const [logs, setLogs] = useState<WorkspaceTaskLog[]>([]);
  const [logsError, setLogsError] = useState('');
  const [logsLoading, setLogsLoading] = useState(false);
  const [logQuery, setLogQuery] = useState('');
  const [logLevel, setLogLevel] = useState('all');
  const [before, setBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [historyMore, setHistoryMore] = useState(false);
  const selection = useRef({ taskId, runId });
  selection.current = { taskId, runId };
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    fetchWorkspaceTasks().then(items => { if (active) setTasks(items); })
      .catch(() => { if (active) setError('Unable to load runs. Retry to read the current state.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [version]);
  const { connected, send } = useWebSocket(getAiAnalyzeWsUrl, {
    onMessage(raw) {
      const event = JSON.parse(raw);
      if (event.type === 'tasks_snapshot') { setTasks(event.data); setLoading(false); setError(''); }
      if (event.type === 'task_detail') {
        setTasks(current => [event.data, ...current.filter(task => task.id !== event.data.id)]);
        if (event.data.id === taskId) {
          if (detail && runId === detail.current_run_id && event.data.current_run_id !== detail.current_run_id) setRunId(event.data.current_run_id || undefined);
          setDetail(event.data);
        }
      }
      if (event.type === 'task_log' && event.task_id === taskId && event.data.run_id === runId) setLogs(current => mergeRunLogs(current, [event.data]));
      if (event.type === 'task_run' && event.task_id === taskId) setRuns(current => [event.data, ...current.filter(run => run.id !== event.data.id)]);
      if (event.type === 'task_deleted') setTasks(current => current.filter(task => task.id !== event.task_id));
    },
  });
  useEffect(() => {
    if (!connected) return;
    send(JSON.stringify({ type: 'subscribe', channel: 'tasks' }));
    if (taskId) send(JSON.stringify({ type: 'subscribe', channel: `task:${taskId}` }));
    return () => {
      send(JSON.stringify({ type: 'unsubscribe', channel: 'tasks' }));
      if (taskId) send(JSON.stringify({ type: 'unsubscribe', channel: `task:${taskId}` }));
    };
  }, [connected, send, taskId]);
  useEffect(() => {
    let active = true;
    setDetail(undefined); setDetailError(''); setRunId(undefined); setRuns([]); setLogs([]); setHistoryError(''); setHistoryLoading(false); setLogsLoading(false);
    if (taskId) fetchWorkspaceTask(taskId).then(item => {
      if (!active) return;
      setDetail(item); setRuns(item.log_runs || []); setHistoryMore((item.log_run_count || 0) > (item.log_runs?.length || 0));
      setRunId(item.current_run_id || item.log_runs?.[0]?.id);
    }).catch(() => { if (active) setDetailError('Unable to load this run. Refresh to retry.'); });
    return () => { active = false; };
  }, [taskId, version]);
  useEffect(() => {
    let active = true;
    setLogs([]); setLogsError(''); setBefore(null); setHasMore(false);
    if (!taskId || !runId) return;
    setLogsLoading(true);
    fetchWorkspaceTaskLogs(taskId, runId).then(page => {
      if (active) { setLogs(current => mergeRunLogs(current, [...page.items, ...(page.notifications || [])])); setBefore(page.next_before_id); setHasMore(page.has_more); }
    }).catch(() => { if (active) setLogsError('Unable to load execution logs. Refresh to retry.'); })
      .finally(() => { if (active) setLogsLoading(false); });
    return () => { active = false; };
  }, [taskId, runId, version]);
  const scoped = useMemo(() => tasks.filter(task => (domain === 'all' || task.product_domain === domain) &&
    (!dataType || task.data_type === dataType || task.data_types?.includes(dataType)) &&
    `${task.name} ${task.owner} ${task.task_path || task.template_name || ''}`.toLowerCase().includes(keyword.trim().toLowerCase())), [tasks, domain, keyword, dataType]);
  const visible = scoped.filter(task => filter === 'all' || task.status === filter);
  const selectedRun = runs.find(run => run.id === runId);
  const { lines, sync, download } = runLogPresentation(logs, runId);
  const visibleLogs = lines.filter(log => (logLevel === 'all' || log.level === logLevel) && log.message.toLowerCase().includes(logQuery.trim().toLowerCase()));
  useEffect(() => { setLogQuery(''); setLogLevel('all'); }, [taskId, runId]);
  const writes = selectedRun?.inserted_records != null && selectedRun.updated_records != null
    ? selectedRun.inserted_records + selectedRun.updated_records : null;
  const loadOlder = async () => {
    if (!taskId || !runId || !before) return;
    setLogsLoading(true); setLogsError('');
    try {
      const page = await fetchWorkspaceTaskLogs(taskId, runId, before);
      if (selection.current.taskId !== taskId || selection.current.runId !== runId) return;
      setLogs(current => mergeRunLogs(current, [...page.items, ...(page.notifications || [])])); setBefore(page.next_before_id); setHasMore(page.has_more);
    } catch { if (selection.current.taskId === taskId && selection.current.runId === runId) setLogsError('Unable to load older logs. Try again.'); }
    finally { if (selection.current.taskId === taskId && selection.current.runId === runId) setLogsLoading(false); }
  };
  const loadHistory = async () => {
    if (!taskId) return;
    setHistoryLoading(true); setHistoryError('');
    try { const page = await fetchWorkspaceTaskLogRuns(taskId, runs.length); if (selection.current.taskId !== taskId) return; setRuns(current => [...current, ...page]); setHistoryMore(page.length === 20); }
    catch { if (selection.current.taskId === taskId) setHistoryError('Unable to load earlier executions. Try again.'); }
    finally { if (selection.current.taskId === taskId) setHistoryLoading(false); }
  };
  const act = async (action: Parameters<typeof runWorkspaceTaskAction>[1]) => {
    if (!taskId || acting) return;
    setActing(true);
    try { await runWorkspaceTaskAction(taskId, action); setVersion(value => value + 1); message.success('Run updated'); }
    catch { message.error('Unable to update this run. Refresh to check its current state.'); }
    finally { setActing(false); }
  };
  return <div className="automation-runs-workspace">
    <p className="automation-list-context">Latest execution per task. Open a task to inspect its execution history and logs.</p>
    <div className="automation-run-stats">{(['running', 'queued', 'completed', 'failed'] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(filter === value ? 'all' : value)}><span>{value}</span><strong>{loading || error ? '—' : scoped.filter(task => task.status === value).length}</strong></button>)}</div>
    <div className="automation-toolbar"><Input allowClear value={keyword} onChange={event => onKeywordChange(event.target.value)} prefix={<SearchOutlined />} aria-label="Search runs" placeholder="Search runs…" className="automation-search" /><div className="automation-status-filters" role="group" aria-label="Run status">{statuses.map(value => <button type="button" key={value} aria-pressed={filter === value} className={filter === value ? 'is-active' : ''} onClick={() => setFilter(value)}>{value === 'all' ? 'All runs' : value}</button>)}</div><Select allowClear aria-label="Run data type" placeholder="All data types" value={dataType} onChange={setDataType} options={[...new Set(tasks.flatMap(task => [task.data_type || '', ...(task.data_types || [])]).filter(Boolean))].map(value => ({ value, label: value }))} /><Button icon={<ReloadOutlined />} onClick={() => setVersion(value => value + 1)}>Refresh</Button></div>
    {error && <Alert type="error" showIcon message={error} />}
    <div className={`automation-runs-body${taskId ? ' has-detail' : ''}`}>
      <div className="automation-run-list"><Table rowKey="id" loading={loading} dataSource={error ? [] : visible} scroll={{ x: 780, y: 'calc(100vh - 470px)' }} pagination={{ pageSize: 12, showSizeChanger: false }} rowClassName={task => task.id === taskId ? 'is-selected' : ''} onRow={task => ({ onClick: () => onSelect(task.id) })} locale={{ emptyText: <Empty description={error ? 'Run data unavailable' : 'No runs match these filters'} /> }} columns={[
        { title: 'Run', width: 270, render: (_: unknown, task: WorkspaceTask) => <button className="automation-record-link" onClick={() => onSelect(task.id)}><strong>{task.name}</strong><small>{task.task_path || task.template_name || task.product_domain}</small></button> },
        { title: 'Status', width: 110, render: (_: unknown, task: WorkspaceTask) => status(task.status) },
        { title: 'Progress', width: 110, render: (_: unknown, task: WorkspaceTask) => <div className="automation-progress"><span>{task.progress}%</span><progress max={100} value={task.progress} /></div> },
        { title: 'Records', width: 100, render: (_: unknown, task: WorkspaceTask) => task.records.toLocaleString() },
        { title: 'Duration', width: 100, render: (_: unknown, task: WorkspaceTask) => duration(task.current_run) },
        { title: 'Owner', width: 120, dataIndex: 'owner' },
        { title: 'Started', width: 170, render: (_: unknown, task: WorkspaceTask) => date(task.started_at) },
      ]} /></div>
      {taskId && <aside className="automation-run-inspector" aria-label="Run details"><header><div><h2>{detail?.name || 'Run details'}</h2></div><Button type="text" icon={<CloseOutlined />} aria-label="Close run details" onClick={() => onSelect(null)} /></header>
        {detailError ? <Alert type="error" message={detailError} /> : !detail ? <p role="status"><LoadingIndicator size={20}>Loading run details…</LoadingIndicator></p> : <>
          {detail.control_state !== 'canceled' && <div className="automation-run-actions">
            {detail.status === 'running' && <Button loading={acting} onClick={() => void act('pause')}>Pause</Button>}
            {detail.status === 'paused' && <Button loading={acting} onClick={() => void act('resume')}>Resume</Button>}
            {detail.status === 'queued' && <Popconfirm title="Start this task now?" onConfirm={() => void act('start')}><Button disabled={acting}>Start run</Button></Popconfirm>}
            {['failed', 'completed'].includes(detail.status) && <Popconfirm title="Start a new execution of this task?" onConfirm={() => void act('restart')}><Button disabled={acting}>Run again</Button></Popconfirm>}
            {['running', 'paused', 'queued', 'failed'].includes(detail.status) && <Popconfirm title="Cancel this task and its future scheduled executions?" onConfirm={() => void act('cancel')}><Button danger disabled={acting}>Cancel task</Button></Popconfirm>}
            {detail.task_type === 'template' && <>
              <Button disabled={acting || detail.status === 'queued'} onClick={() => void act(detail.download_state === 'running' ? 'pause_download' : 'start_download')}>{detail.download_state === 'running' ? 'Pause download' : 'Start download'}</Button>
              <Button disabled={acting || detail.status === 'queued'} onClick={() => void act(detail.sync_state === 'running' ? 'pause_sync' : 'start_sync')}>{detail.sync_state === 'running' ? 'Pause data push' : 'Start data push'}</Button>
              <Popconfirm title="Cancel data push for this task?" onConfirm={() => void act('cancel_sync')}><Button disabled={acting || detail.status === 'queued' || detail.sync_state === 'canceled'}>Cancel data push</Button></Popconfirm>
            </>}
          </div>}
          <div className="automation-run-meta">{status(selectedRun?.status || detail.status)}<span>{duration(selectedRun)}</span><span>Throughput {runId === detail.current_run_id && detail.throughput ? `${detail.throughput}/s` : '—'}</span><span>Batch writes {writes?.toLocaleString() ?? '—'}</span>
            <details className="automation-run-metadata"><summary>Task details</summary><dl className="automation-run-facts"><div><dt>Owner</dt><dd>{detail.owner}</dd></div><div><dt>Started</dt><dd>{date(selectedRun?.started_at || detail.started_at)}</dd></div><div><dt>Task records</dt><dd>{detail.records.toLocaleString()}</dd></div><div><dt>Run ID</dt><dd><code>{runId || 'Not started'}</code></dd></div><div><dt>Task path</dt><dd><code>{detail.task_path || detail.template_name || '—'}</code></dd></div></dl></details>
          </div>
          <div className="automation-run-history"><h3>Logs</h3><Select aria-label="Select execution" value={runId} onChange={setRunId} options={runs.map(run => ({ value: run.id, label: `${date(run.started_at)} · ${run.status}` }))} /><span className="automation-run-diff" aria-label="Selected run statistics"><span title="Inserted records">+{selectedRun?.inserted_records ?? '—'}</span><span title="Updated records">~{selectedRun?.updated_records ?? '—'}</span><span title="Deleted records">−{selectedRun?.deleted_records ?? '—'}</span></span>{historyMore && <Button size="small" loading={historyLoading} onClick={() => void loadHistory()}>Earlier executions</Button>}</div>
          {historyError && <p role="alert">{historyError}</p>}
          <div className="automation-worker-notices" aria-label="Download and sync notifications">
            <div className="automation-worker-notice is-sync" role="status"><strong>Data push</strong><span className="automation-notice-record" title={sync?.recordId}>{sync?.recordId || (sync ? 'Record details unavailable' : 'Waiting for updates')}{sync?.topic && <> → <code>{sync.topic}</code></>}</span><span>{sync ? workerNoticeStatusLabel(sync.status) : runId === detail.current_run_id ? detail.sync_state : 'No notifications'}</span><span>Total <b>{selectedRun?.synced_records ?? '—'}</b></span></div>
            <div className="automation-worker-notice is-download" role="status"><strong>Download</strong><span className="automation-notice-record" title={download?.recordId}>{download?.recordId || (download ? 'Record details unavailable' : 'Waiting for updates')}</span><span>Items <b>{download?.itemsCompleted ?? '—'}/{download?.itemsTotal ?? '—'}</b></span>{!!download?.itemsMissing && <span>Missing {download.itemsMissing}</span>}{!!download?.itemsFailed && <span>Failed {download.itemsFailed}</span>}<span>{download ? workerNoticeStatusLabel(download.status) : runId === detail.current_run_id ? detail.download_state : 'No notifications'}</span><span>Run records <b>{selectedRun?.downloaded_records ?? '—'}</b></span></div>
          </div>
          <div className="automation-log-toolbar"><Input allowClear aria-label="Search execution logs" placeholder="Search loaded logs…" value={logQuery} onChange={event => setLogQuery(event.target.value)} /><Select aria-label="Log level" value={logLevel} onChange={setLogLevel} options={['all', ...new Set(lines.map(log => log.level))].map(value => ({ value, label: value === 'all' ? 'All levels' : value }))} /><span>{visibleLogs.length} / {lines.length} loaded</span></div>
          <div className="automation-inspector-section automation-logs">{logsError && <p role="alert">{logsError}</p>}{hasMore && <Button size="small" loading={logsLoading} onClick={() => void loadOlder()}>Load older logs</Button>}
            {logsLoading && !lines.length ? <p role="status"><LoadingIndicator size={20}>Loading logs…</LoadingIndicator></p> : visibleLogs.length ? <div className="workspace-dock-log-list is-detail">{visibleLogs.slice().reverse().map(log => <div key={log.id} className={`workspace-dock-log-row is-${log.level}`}><code>{renderConsoleLogMessage(log.message)}</code></div>)}</div> : <p className="automation-muted">{lines.length ? 'No loaded logs match these filters.' : runId ? 'No logs recorded for this execution.' : 'This task has not started.'}</p>}
          </div>
        </>}
      </aside>}
    </div>
  </div>;
}

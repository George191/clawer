import { useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Alert, Button, Empty, Input, Select, Table } from '@/components/ui';
import { CloseOutlined, ReloadOutlined } from '@/components/ui/icons';
import { useThemeStore } from '@/stores/settings';
import {
  cancelManagedAgentRun, createManagedAgentRun, fetchManagedAgentEvents, fetchManagedAgentRun, fetchManagedAgentRuns,
  buildManagedAgent, type AgentBuildDraft, type AgentDefinition, type ManagedAgentEvent, type ManagedAgentRun,
} from '@/services/agentManagement';

const terminal = (run: ManagedAgentRun) => !['queued', 'running'].includes(run.status);
const when = (value: string) => new Date(value).toLocaleString();
const failureText = (error: unknown) => error instanceof Error ? error.message : 'Unable to load agent activity.';
const title = (run: ManagedAgentRun) => String(run.request.input.task ?? run.request.input.goal ?? 'Agent run');

export function AgentBuilder({ agent, onApply }: { agent: AgentDefinition; onApply: (draft: AgentBuildDraft) => void }) {
  const [prompt, setPrompt] = useState('');
  const [draft, setDraft] = useState<AgentBuildDraft>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <section className="dify-preview" aria-label="Agent builder"><header><strong>Build with AI</strong><span>Review changes before applying</span></header><div className="dify-preview-conversation">
    {error && <Alert type="error" message={error} />}
    {draft ? <div className="dify-build-draft"><h3>Build draft</h3>{(['name', 'description', 'instructions'] as const).map(field => <div key={field}><h4>{field}</h4><del>{agent[field] || '(empty)'}</del><pre>{draft[field]}</pre></div>)}<div><Button disabled={busy} onClick={() => setDraft(undefined)}>Discard draft</Button><Button type="primary" disabled={busy} onClick={() => { onApply(draft); setDraft(undefined); }}>Apply draft</Button></div></div> : <div className="dify-preview-empty"><span>✦</span><h3>Build your agent</h3><p>Describe its purpose and behavior. Review the proposed prompt before saving.</p></div>}
  </div><div className="dify-preview-composer"><textarea aria-label="Agent build request" placeholder="Describe what your agent should do" value={prompt} onChange={event => setPrompt(event.target.value)} disabled={busy} /><div><small>Uses the saved model connection and model quota. Save a personal agent first.</small><Button type="primary" loading={busy} disabled={!agent.id || !agent.can_edit || !prompt.trim() || busy} onClick={async () => {
    setBusy(true); setError('');
    try { setDraft(await buildManagedAgent(agent, prompt)); } catch (cause) { setError(failureText(cause)); }
    finally { setBusy(false); }
  }}>Build</Button></div></div></section>;
}

export function AgentPreview({ agent }: { agent: AgentDefinition }) {
  const [prompt, setPrompt] = useState('');
  const [url, setUrl] = useState('');
  const [run, setRun] = useState<ManagedAgentRun>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!run || terminal(run)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchManagedAgentRun(run.id, controller.signal).then(setRun).catch(cause => {
        if (!controller.signal.aborted) setError(failureText(cause));
      });
    }, 1500);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [run]);
  const submit = async () => {
    if (!prompt.trim() || busy) return;
    setError(''); setBusy(true);
    try {
      setRun(await createManagedAgentRun(agent.id, agent.harness === 'collection' ? { goal: prompt, url } : { task: prompt }));
    } catch (cause) { setError(failureText(cause)); }
    finally { setBusy(false); }
  };
  return <section className="dify-preview" aria-label="Agent preview">
    <header><strong>Preview</strong><span>Saved configuration · Revision {agent.revision}</span></header>
    <div className="dify-preview-conversation">
      {error && <Alert type="error" message={error} />}
      {run ? <><div className="dify-message is-user">{title(run)}</div><div className="dify-message is-agent"><span className="dify-run-state">{run.status} · {run.stage}/{run.stage_count} steps</span>
        {run.error_code ? <Alert type="error" message={run.error_code} /> : run.result ? <pre>{typeof run.result.text === 'string' ? run.result.text : JSON.stringify(run.result, null, 2)}</pre> : <p>{terminal(run) ? 'No output recorded.' : 'The agent is processing your request…'}</p>}
      </div></> : <div className="dify-preview-empty"><span>✦</span><h3>{agent.name || 'Your agent'}</h3><p>Test a real task using the saved model and instructions.</p></div>}
    </div>
    <div className="dify-preview-composer">
      {agent.harness === 'collection' && <Input aria-label="Preview source URL" placeholder="https://example.com" value={url} onChange={event => setUrl(event.target.value)} />}
      <textarea aria-label="Preview message" placeholder="Talk to your agent…" value={prompt} onChange={event => setPrompt(event.target.value)} disabled={busy || Boolean(run && !terminal(run))} />
      <div><small>Uses model quota. Save, verify and enable the agent first.</small>{run && !terminal(run) ? <Button onClick={async () => {
        setBusy(true); setError('');
        try { setRun(await cancelManagedAgentRun(run.id)); } catch (cause) { setError(failureText(cause)); }
        finally { setBusy(false); }
      }} disabled={busy}>Stop</Button> : <Button type="primary" disabled={!agent.id || !agent.enabled || !agent.verified || !prompt.trim() || busy || (agent.harness === 'collection' && !url.trim())} loading={busy} onClick={() => void submit()}>Send</Button>}</div>
    </div>
  </section>;
}

export default function AgentActivity({ agent, monitoring = false }: { agent: AgentDefinition; monitoring?: boolean }) {
  const [runs, setRuns] = useState<ManagedAgentRun[]>([]);
  const [selected, setSelected] = useState<ManagedAgentRun>();
  const [events, setEvents] = useState<ManagedAgentEvent[]>([]);
  const [eventsError, setEventsError] = useState('');
  const [eventsLoading, setEventsLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const light = useThemeStore(state => state.mode === 'light');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setSelected(undefined);
    fetchManagedAgentRuns(agent.id, controller.signal).then(setRuns).catch(cause => {
      if (!controller.signal.aborted) setError(failureText(cause));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [agent.id, version]);
  useEffect(() => {
    const controller = new AbortController();
    setEvents([]); setEventsError('');
    if (!selected) return;
    setEventsLoading(true);
    const read = async () => {
      let after = 0;
      while (!controller.signal.aborted) {
        const page = await fetchManagedAgentEvents(selected.id, after, controller.signal);
        if (controller.signal.aborted) return;
        setEvents(current => [...current, ...page]);
        if (page.length < 100) break;
        after = page[page.length - 1].seq;
      }
    };
    void read().catch(cause => { if (!controller.signal.aborted) setEventsError(failureText(cause)); })
      .finally(() => { if (!controller.signal.aborted) setEventsLoading(false); });
    return () => controller.abort();
  }, [selected]);
  const recent = runs.filter(run => Date.parse(run.created_at) >= Date.now() - days * 86400000);
  const visible = recent.filter(run => (status === 'all' || run.status === status) && `${title(run)} ${run.product_domain} ${run.id}`.toLowerCase().includes(query.trim().toLowerCase()));
  const completed = recent.filter(run => run.status === 'completed');
  const chart = {
    color: ['#155eef'], textStyle: { color: light ? '#667085' : '#98a2b3' },
    grid: { left: 45, right: 20, top: 20, bottom: 35 },
    tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: Array.from({ length: days }, (_, i) => new Date(Date.now() - (days - i - 1) * 86400000).toLocaleDateString()) },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: light ? '#eaecf0' : '#344054' } } },
    series: [{ type: 'line', smooth: true, areaStyle: { opacity: .08 }, data: Array.from({ length: days }, (_, i) => recent.filter(run => new Date(run.created_at).toDateString() === new Date(Date.now() - (days - i - 1) * 86400000).toDateString()).length) }],
  };
  return <section className="dify-activity">
    <header className="dify-section-heading"><div><h1>{monitoring ? 'Monitoring' : 'Logs'}</h1><p>{monitoring ? 'Usage and reliability of this agent.' : 'Inspect user inputs, agent outputs and execution events.'} Metrics cover your latest 100 runs.</p></div><Button icon={<ReloadOutlined />} onClick={() => setVersion(value => value + 1)}>Refresh</Button></header>
    <div className="dify-activity-filters"><Select aria-label="Agent activity date range" value={days} onChange={setDays} options={[7, 30, 90].map(value => ({ value, label: `Last ${value} days` }))} />{!monitoring && <><Select aria-label="Agent run status" value={status} onChange={setStatus} options={['all', 'queued', 'running', 'completed', 'failed', 'cancelled'].map(value => ({ value, label: value === 'all' ? 'All statuses' : value }))} /><Input aria-label="Search agent logs" placeholder="Search" value={query} onChange={event => setQuery(event.target.value)} /></>}</div>
    {error && <Alert type="error" message={error} />}
    {monitoring ? <><div className="dify-metrics">{[
      ['Total runs', recent.length], ['Successful runs', completed.length], ['Failed runs', recent.filter(run => run.status === 'failed').length], ['Success rate', recent.filter(terminal).length ? `${Math.round(completed.length / recent.filter(terminal).length * 100)}%` : '—'],
    ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{loading || error ? '—' : value}</strong></div>)}</div><div className="dify-chart"><h3>Runs over time</h3>{loading ? <p role="status">Loading metrics…</p> : error ? <Empty description="Metrics unavailable" /> : <ReactECharts option={chart} style={{ height: 280 }} />}</div></> : <div className={`dify-log-layout${selected ? ' has-detail' : ''}`}><Table rowKey="id" loading={loading} dataSource={error ? [] : visible} pagination={{ pageSize: 15, showSizeChanger: false }} scroll={{ x: 700 }} locale={{ emptyText: <Empty description={error ? 'Logs unavailable' : 'No runs match these filters'} /> }} columns={[
      { title: 'Title', render: (_, run) => <button className="dify-text-link" onClick={() => setSelected(run)}>{title(run)}</button> },
      { title: 'Source', dataIndex: 'product_domain' }, { title: 'Status', render: (_, run) => <span className={`dify-run-state is-${run.status}`}>{run.status}</span> },
      { title: 'Created at', render: (_, run) => when(run.created_at) },
    ]} />{selected && <aside className="dify-log-detail" aria-label="Agent run details"><header><div><small>RUN ID</small><code>{selected.id}</code></div><Button type="text" aria-label="Close agent run details" icon={<CloseOutlined />} onClick={() => setSelected(undefined)} /></header><div className="dify-message is-user">{title(selected)}</div><div className="dify-message is-agent"><pre>{selected.result ? JSON.stringify(selected.result, null, 2) : selected.error_code || selected.status}</pre></div><h3>Execution trace</h3>{eventsError && <Alert type="error" message={eventsError} />}{eventsLoading && <p role="status">Loading execution trace…</p>}{events.map(event => <details key={event.seq}><summary>{String(event.event.type || 'Event')} · {when(event.created_at)}</summary><pre>{JSON.stringify(event.event, null, 2)}</pre></details>)}</aside>}</div>}
  </section>;
}

import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Drawer, Empty, Form, Input, Switch, Modal, Table, message } from '@/components/ui';
import { RobotOutlined, PlusOutlined, ApiOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { fetchAgentCatalog, saveAgentConnection, saveAgentDefinition, deleteAgentItem, testAgentDefinition, type AgentCatalog, type AgentDefinition, type AgentConnection } from '@/services/agentManagement';
import './style.css';

const blankAgent: AgentDefinition = { id: '', name: '', description: '', harness: 'assistant', connection_id: '', instructions: '', model: 'gpt-6-sol', reasoning_effort: 'high', timeout_seconds: 120, max_output_tokens: 8192, enabled: false, revision: 0, verified: false, visibility: 'private', shared_with: [], system_key: null, can_edit: true };
const blankConnection: AgentConnection = { id: '', name: '', base_url: 'https://api.openai.com/v1', revision: 0 };
const agentStatus = (agent: AgentDefinition) => !agent.verified ? 'unverified' : agent.enabled ? 'enabled' : 'disabled';
const statusLabel = { enabled: 'Enabled', disabled: 'Disabled', unverified: 'Needs verification' };

export default function Agents() {
  const [params, setParams] = useSearchParams();
  const connectionsView = params.get('view') === 'connections';
  const executorsView = params.get('view') === 'executors';
  const [catalog, setCatalog] = useState<AgentCatalog>({ agents: [], connections: [], harnesses: {} });
  const [agent, setAgent] = useState<AgentDefinition | null>(null);
  const [connection, setConnection] = useState<AgentConnection | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [recipients, setRecipients] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [scopeFilter, setScopeFilter] = useState('all');
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  async function load() { const data = await fetchAgentCatalog(); setCatalog(data); setLoaded(true); return data; }
  async function act(work: () => Promise<void>) {
    setBusy(true); setError('');
    try { await work(); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to complete this action.'); }
    finally { setBusy(false); }
  }
  useEffect(() => { void act(async () => { await load(); }); }, []);
  function select(next: AgentDefinition | AgentConnection | null, view = connectionsView) {
    if (view) setConnection(next as AgentConnection | null); else setAgent(next as AgentDefinition | null);
    setApiKey(''); setDirty(false); setError('');
    setRecipients(next && 'shared_with' in next ? next.shared_with.join(', ') : '');
  }
  function switchView(view: boolean | 'executors') {
    const change = () => { select(null); setAgent(null); setConnection(null); setSearch(''); setStatusFilter('all'); setScopeFilter('all'); setParams(view === 'executors' ? { view } : view ? { view: 'connections' } : {}); };
    if (dirty) { Modal.confirm({ title: 'Discard unsaved changes?', onOk: change }); return; }
    change();
  }
  function choose(next: AgentDefinition | AgentConnection) {
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: () => select(next) }); else select(next);
  }
  function closeEditor() {
    if (busy) return;
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: () => select(null) });
    else select(null);
  }
  async function save() {
    if (connectionsView && connection) {
      if (!connection.name.trim() || !connection.base_url.trim() || (!connection.id && !apiKey.trim())) throw new Error('Enter a connection name, API endpoint and API key.');
      const result = await saveAgentConnection({ name: connection.name, base_url: connection.base_url, expected_revision: connection.revision, ...(apiKey ? { api_key: apiKey } : {}) }, connection.id || undefined);
      const data = await load(); select(data.connections.find((item) => item.id === result.id)!, true);
    } else if (agent) {
      if (!agent.name.trim() || !agent.model.trim() || !agent.connection_id) throw new Error('Enter an agent name and model, and choose a model connection.');
      if (agent.visibility === 'shared' && !recipients.trim()) throw new Error('Enter at least one recipient.');
      const { id, verified: _verified, revision, system_key: _system, can_edit: _canEdit, ...fields } = agent;
      const result = await saveAgentDefinition({ ...fields, shared_with: agent.visibility === 'shared' ? recipients.split(/[,;\s]+/).filter(Boolean) : [], expected_revision: revision }, id || undefined);
      const data = await load(); select(data.agents.find((item) => item.id === result.id)!, false);
    }
    message.success('Saved to server');
  }
  const selected = connectionsView ? connection : agent;
  const readOnly = !connectionsView && Boolean(agent && !agent.can_edit);
  function remove() {
    if (!selected?.id) return;
    Modal.confirm({ title: `Delete ${selected.name}?`, content: connectionsView ? 'Connections used by agents cannot be deleted.' : 'Saved runs are retained. Queued runs using this agent will fail explicitly.', okText: 'Delete', onOk: () => act(async () => { await deleteAgentItem(connectionsView ? 'connections' : 'agents', selected.id, selected.revision); await load(); select(null); }) });
  }
  const query = search.trim().toLowerCase();
  const agents = catalog.agents.filter(item => `${item.name} ${item.description} ${item.model} ${catalog.harnesses[item.harness]?.name ?? item.harness}`.toLowerCase().includes(query)
    && (statusFilter === 'all' || agentStatus(item) === statusFilter)
    && (scopeFilter === 'all' || (scopeFilter === 'system' ? Boolean(item.system_key) : scopeFilter === 'mine' ? item.can_edit && !item.system_key : !item.can_edit && !item.system_key)));
  const connections = catalog.connections.filter(item => `${item.name} ${item.base_url}`.toLowerCase().includes(query));
  const executors = Object.entries(catalog.harnesses).map(([id, value]) => ({ id, ...value })).filter(item => `${item.name} ${item.id} ${item.tools.join(' ')}`.toLowerCase().includes(query));
  const create = () => choose(connectionsView ? { ...blankConnection } : { ...blankAgent, connection_id: catalog.connections[0]?.id ?? '' });
  const summaryValue = (value: number) => loaded ? value : '—';
  return <div className="agent-management">
    <header><div><Link className="agent-back-link" to="/automation/workflows">Automation</Link><h1>Agent management</h1><p>Configure reusable AI agents, model connections and registered execution tools.</p></div><div className="agent-header-actions"><Button icon={<ReloadOutlined />} onClick={() => void act(async () => { await load(); })} disabled={busy || dirty}>Refresh</Button>{!executorsView && <Button type="primary" icon={<PlusOutlined />} disabled={busy} onClick={create}>{connectionsView ? 'New connection' : 'New agent'}</Button>}</div></header>
    <div className="agent-summary" aria-label="Agent catalog summary"><div><strong>{summaryValue(catalog.agents.length)}</strong><span>Agents</span></div><div><strong>{summaryValue(catalog.agents.filter(item => item.enabled && item.verified).length)}</strong><span>Enabled & verified</span></div><div><strong>{summaryValue(catalog.agents.filter(item => !item.verified).length)}</strong><span>Needs verification</span></div><div><strong>{summaryValue(catalog.connections.length)}</strong><span>Model connections</span></div></div>
    <nav className="agent-tabs" aria-label="Agent management sections"><button aria-current={!connectionsView && !executorsView ? 'page' : undefined} onClick={() => switchView(false)}><RobotOutlined />Agents <span>{catalog.agents.length}</span></button><button aria-current={connectionsView ? 'page' : undefined} onClick={() => switchView(true)}><ApiOutlined />Model connections <span>{catalog.connections.length}</span></button><button aria-current={executorsView ? 'page' : undefined} onClick={() => switchView('executors')}><RobotOutlined />Executors <span>{Object.keys(catalog.harnesses).length}</span></button></nav>
    {error && !selected && <Alert type="error" message={error} />}
    <section className="agent-catalog-panel">
      <div className="agent-catalog-toolbar"><Input allowClear prefix={<SearchOutlined />} aria-label="Search agents and connections" placeholder={executorsView ? 'Search executors and tools…' : connectionsView ? 'Search connections…' : 'Search agents, models and executors…'} value={search} onChange={event => setSearch(event.target.value)} />
        {!connectionsView && !executorsView && <><select aria-label="Filter agent status" value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="all">All statuses</option><option value="enabled">Enabled</option><option value="unverified">Needs verification</option><option value="disabled">Disabled</option></select><select aria-label="Filter agent ownership" value={scopeFilter} onChange={event => setScopeFilter(event.target.value)}><option value="all">All agents</option><option value="mine">My agents</option><option value="system">System agents</option><option value="shared">Shared with me</option></select></>}
      </div>
      {executorsView ? <><p className="agent-catalog-note">Registered execution tools available to agents. Select an agent to inspect its configuration.</p><Table rowKey="id" loading={busy && !loaded} dataSource={executors} pagination={false} scroll={{ x: 700 }} locale={{ emptyText: <Empty description="No registered executors match your search" /> }} columns={[
        { title: 'Executor', render: (_, item) => <div className="agent-record-name"><strong>{item.name}</strong><small>{item.id}</small></div> },
        { title: 'Registered tools', render: (_, item) => <div className="agent-tool-tags">{item.tools.length ? item.tools.map(tool => <code key={tool}>{tool}</code>) : <span>No tools registered</span>}</div> },
        { title: 'Bound agents', render: (_, item) => <div className="agent-tool-tags">{catalog.agents.filter(row => row.harness === item.id).map(row => <button key={row.id} className="agent-record-link" onClick={() => { setParams({}); select(row, false); }}>{row.name}</button>)}</div> },
      ]} /></> : connectionsView ? <Table rowKey="id" loading={busy && !loaded} dataSource={connections} scroll={{ x: 700 }} pagination={{ pageSize: 12, showSizeChanger: false }} locale={{ emptyText: <Empty description={loaded ? 'No model connections match. Add a connection to use it across agents.' : 'Loading model connections…'} /> }} columns={[
        { title: 'Connection', render: (_, item) => <button className="agent-record-link" onClick={() => choose(item)}>{item.name}</button> },
        { title: 'API endpoint', dataIndex: 'base_url', render: value => <code className="agent-endpoint">{value}</code> },
        { title: 'Agent references', render: (_, item) => catalog.agents.filter(row => row.connection_id === item.id).length },
        { title: 'Revision', dataIndex: 'revision' },
      ]} /> : <Table rowKey="id" loading={busy && !loaded} dataSource={agents} scroll={{ x: 940 }} pagination={{ pageSize: 12, showSizeChanger: false }} locale={{ emptyText: <Empty description={loaded ? 'No agents match your filters. Create an agent or adjust the filters.' : 'Loading agents…'} /> }} columns={[
        { title: 'Agent', width: 270, render: (_, item) => <div className="agent-record-name"><button className="agent-record-link" onClick={() => choose(item)}>{item.name}</button><small>{item.description || 'No description'}</small></div> },
        { title: 'Status', width: 160, render: (_, item) => <span className={`agent-status is-${agentStatus(item)}`}>{statusLabel[agentStatus(item)]}</span> },
        { title: 'Model', render: (_, item) => <code>{item.model}</code> },
        { title: 'Executor', render: (_, item) => catalog.harnesses[item.harness]?.name ?? item.harness },
        { title: 'Access', render: (_, item) => item.system_key ? 'System' : item.can_edit ? item.visibility : 'Shared with you' },
        { title: 'Revision', dataIndex: 'revision', width: 90 },
      ]} />}
      <div className="agent-catalog-footer">{loaded ? `${executorsView ? executors.length : connectionsView ? connections.length : agents.length} matching ${executorsView ? 'executors' : connectionsView ? 'connections' : 'agents'}` : <LoadingIndicator size={20}>Loading catalog…</LoadingIndicator>}</div>
    </section>
    <Drawer open={Boolean(selected)} title={selected?.id ? connectionsView ? 'Connection details' : 'Agent details' : connectionsView ? 'New connection' : 'New agent'} width="min(720px, 100vw)" onClose={closeEditor} className="agent-editor-drawer" destroyOnHidden>
      {selected && <Form layout="vertical" onFinish={() => void act(save)}>{error && <Alert type="error" message={error} />}
        {readOnly && <Alert type="info" message={agent?.system_key ? <>Built-in system agent. Its definition and workflow cannot be edited or deleted. Model connections are configured separately in <a href="/settings?section=agents">System settings</a>.</> : 'This agent is available for you to use. Only its owner can edit its definition and sharing.'} />}
        <fieldset disabled={busy || readOnly}><div className="agent-editor-title"><h2>{selected.id ? selected.name : connectionsView ? 'New connection' : 'New agent'}</h2>{selected.id && <small>Revision {selected.revision}</small>}</div>
          {connectionsView && connection ? <>
            <Form.Item label="Connection name"><Input aria-label="Connection name" required value={connection.name} onChange={(e) => { setConnection({ ...connection, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Responses API base URL"><Input aria-label="Responses API base URL" required value={connection.base_url} onChange={(e) => { setConnection({ ...connection, base_url: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="API key"><Input aria-label="API key" type="password" required={!connection.id} autoComplete="new-password" value={apiKey} placeholder={connection.id ? 'Configured — leave blank to keep' : 'Enter API key'} onChange={(e) => { setApiKey(e.target.value); setDirty(true); }} /></Form.Item><p className="agent-help">Updating a connection requires its agents to be tested again. Keys are not stored in browser storage.</p>
          </> : agent && <>
            <h3 className="agent-editor-section">Identity & access</h3><Form.Item label="Name"><Input aria-label="Agent name" required value={agent.name} onChange={(e) => { setAgent({ ...agent, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Description"><Input aria-label="Description" value={agent.description} onChange={(e) => { setAgent({ ...agent, description: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Visibility"><select aria-label="Agent visibility" value={agent.visibility} onChange={(e) => { setAgent({ ...agent, visibility: e.target.value as AgentDefinition['visibility'] }); setDirty(true); }}><option value="private">Private — only you</option><option value="shared">Share with selected users</option><option value="public">Public — all signed-in users</option></select></Form.Item>
            {agent.visibility === 'shared' && agent.can_edit && <Form.Item label="Recipients"><Input aria-label="Share with users" required value={recipients} placeholder="Registered email addresses, separated by commas" onChange={(e) => { setRecipients(e.target.value); setDirty(true); }} /></Form.Item>}
            <h3 className="agent-editor-section">Model configuration</h3><Form.Item label="Model connection"><select required aria-label="Model connection" value={agent.connection_id ?? ''} onChange={(e) => { setAgent({ ...agent, connection_id: e.target.value }); setDirty(true); }}><option value="">{agent.system_key ? 'System model defaults' : 'Choose a connection'}</option>{readOnly && agent.connection_id && <option value={agent.connection_id}>Owner-managed connection</option>}{!readOnly && catalog.connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Form.Item>
            {!readOnly && !catalog.connections.length && <Button onClick={() => switchView(true)}>Add a model connection</Button>}
            <div className="agent-field-pair"><Form.Item label="Model ID"><Input aria-label="Model ID" required value={agent.model} onChange={(e) => { setAgent({ ...agent, model: e.target.value }); setDirty(true); }} /></Form.Item><Form.Item label="Reasoning effort"><select aria-label="Reasoning effort" value={agent.reasoning_effort} onChange={(e) => { setAgent({ ...agent, reasoning_effort: e.target.value }); setDirty(true); }}>{['low', 'medium', 'high', 'xhigh', 'max'].map((value) => <option key={value}>{value}</option>)}</select></Form.Item></div>
            <h3 className="agent-editor-section">Behavior</h3><Form.Item label="Instructions"><textarea aria-label="Agent instructions" rows={7} maxLength={12000} value={agent.instructions} onChange={(e) => { setAgent({ ...agent, instructions: e.target.value }); setDirty(true); }} /></Form.Item>
            <p className="agent-help">{agent.system_key ? `Built-in workflow: ${catalog.harnesses[agent.harness]?.tools.join(' → ')}` : 'Define this agent’s purpose, behavior, and response style. Its configuration can be used across all projects.'}</p>
            <h3 className="agent-editor-section">Execution limits</h3><div className="agent-field-pair"><Form.Item label="Request timeout (seconds)"><Input type="number" min={10} max={300} value={agent.timeout_seconds} onChange={(e) => { setAgent({ ...agent, timeout_seconds: Number(e.target.value) }); setDirty(true); }} /></Form.Item><Form.Item label="Maximum output tokens"><Input type="number" min={1024} max={16384} value={agent.max_output_tokens} onChange={(e) => { setAgent({ ...agent, max_output_tokens: Number(e.target.value) }); setDirty(true); }} /></Form.Item></div>
            <Form.Item label="Enabled"><Switch aria-label="Enable agent" checked={agent.enabled} onChange={(enabled) => { setAgent({ ...agent, enabled }); setDirty(true); }} /></Form.Item><p role="status" className="agent-help">{agent.verified && !dirty ? 'Saved model configuration verified.' : 'Save, test the model connection, then enable. Testing sends a real request and uses model quota.'}</p>
          </>}
        </fieldset>
        {!readOnly && <footer><Button type="primary" htmlType="submit" loading={busy} disabled={!dirty || busy}>Save</Button>{!connectionsView && agent?.id && <Button disabled={busy || dirty} onClick={() => void act(async () => { const result = await testAgentDefinition(agent.id); const data = await load(); select(data.agents.find((row) => row.id === agent.id)!, false); message.success(`Connection verified (${result.latency_ms} ms)`); })}>Test connection</Button>}<Button disabled={busy || !dirty} onClick={() => select(connectionsView ? catalog.connections.find((row) => row.id === selected.id) ?? null : catalog.agents.find((row) => row.id === selected.id) ?? null)}>Discard</Button>{selected.id && <Button danger disabled={busy || dirty} onClick={remove}>Delete</Button>}</footer>}
      </Form>}
    </Drawer>
  </div>;
}

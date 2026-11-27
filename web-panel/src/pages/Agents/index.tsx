import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Alert, Button, Drawer, Empty, Form, Input, Switch, Modal, Table, message } from '@/components/ui';
import { RobotOutlined, PlusOutlined, ApiOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { fetchAgentCatalog, saveAgentConnection, saveAgentDefinition, deleteAgentItem, testAgentDefinition, type AgentCatalog, type AgentDefinition, type AgentConnection } from '@/services/agentManagement';
import { apiUrl } from '@/services/endpoints';
import AgentActivity, { AgentBuilder, AgentPreview } from './AgentActivity';
import './style.css';

const blankAgent: AgentDefinition = { id: '', name: '', description: '', harness: 'assistant', connection_id: '', instructions: '', model: 'gpt-6-sol', reasoning_effort: 'high', timeout_seconds: 120, max_output_tokens: 8192, enabled: false, revision: 0, verified: false, visibility: 'private', shared_with: [], system_key: null, can_edit: true };
const blankConnection: AgentConnection = { id: '', name: '', base_url: 'https://api.openai.com/v1', revision: 0 };
const agentStatus = (agent: AgentDefinition) => !agent.verified ? 'unverified' : agent.enabled ? 'enabled' : 'disabled';

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
  const [sort, setSort] = useState('modified');
  const [previewOpen, setPreviewOpen] = useState(true);
  const [buildMode, setBuildMode] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const panel = params.get('panel') || 'configure';
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
    const open = () => { select(next); if (!connectionsView) setParams(next.id ? { agent: next.id } : { new: 'agent' }); };
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: open }); else open();
  }
  function closeEditor() {
    if (busy) return;
    const close = () => { select(null); setParams(connectionsView ? { view: 'connections' } : {}); };
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: close });
    else close();
  }
  async function save(publish = false) {
    if (connectionsView && connection) {
      if (!connection.name.trim() || !connection.base_url.trim() || (!connection.id && !apiKey.trim())) throw new Error('Enter a connection name, API endpoint and API key.');
      const result = await saveAgentConnection({ name: connection.name, base_url: connection.base_url, expected_revision: connection.revision, ...(apiKey ? { api_key: apiKey } : {}) }, connection.id || undefined);
      const data = await load(); select(data.connections.find((item) => item.id === result.id)!, true);
    } else if (agent) {
      if (!agent.name.trim() || !agent.model.trim() || !agent.connection_id) throw new Error('Enter an agent name and model, and choose a model connection.');
      if (agent.visibility === 'shared' && !recipients.trim()) throw new Error('Enter at least one recipient.');
      const { id, verified: _verified, revision, system_key: _system, can_edit: _canEdit, created_at: _createdAt, updated_at: _updatedAt, ...fields } = agent;
      const result = await saveAgentDefinition({ ...fields, enabled: publish || fields.enabled, shared_with: agent.visibility === 'shared' ? recipients.split(/[,;\s]+/).filter(Boolean) : [], expected_revision: revision }, id || undefined);
      const data = await load(); select(data.agents.find((item) => item.id === result.id)!, false); setParams({ agent: result.id });
    }
    message.success('Saved to server');
  }
  const selected = connectionsView ? connection : agent;
  const readOnly = !connectionsView && Boolean(agent && !agent.can_edit);
  function remove() {
    if (!selected?.id) return;
    Modal.confirm({ title: `Delete ${selected.name}?`, content: connectionsView ? 'Connections used by agents cannot be deleted.' : 'Saved runs are retained. Queued runs using this agent will fail explicitly.', okText: 'Delete', onOk: () => act(async () => { await deleteAgentItem(connectionsView ? 'connections' : 'agents', selected.id, selected.revision); await load(); select(null); setParams(connectionsView ? { view: 'connections' } : {}); }) });
  }
  const query = search.trim().toLowerCase();
  const agents = catalog.agents.filter(item => `${item.name} ${item.description} ${item.model} ${catalog.harnesses[item.harness]?.name ?? item.harness}`.toLowerCase().includes(query)
    && (statusFilter === 'all' || (statusFilter === 'published' ? agentStatus(item) === 'enabled' : statusFilter === 'draft' ? agentStatus(item) !== 'enabled' : agentStatus(item) === statusFilter))
    && (scopeFilter === 'all' || (scopeFilter === 'system' ? Boolean(item.system_key) : scopeFilter === 'mine' ? item.can_edit && !item.system_key : !item.can_edit && !item.system_key)));
  const connections = catalog.connections.filter(item => `${item.name} ${item.base_url}`.toLowerCase().includes(query));
  const executors = Object.entries(catalog.harnesses).map(([id, value]) => ({ id, ...value })).filter(item => `${item.name} ${item.id} ${item.tools.join(' ')}`.toLowerCase().includes(query));
  agents.sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name) : Date.parse((sort === 'created' ? b.created_at : b.updated_at) || '') - Date.parse((sort === 'created' ? a.created_at : a.updated_at) || ''));
  const create = () => choose(connectionsView ? { ...blankConnection } : { ...blankAgent, connection_id: catalog.connections[0]?.id ?? '' });
  const savedAgent = catalog.agents.find(item => item.id === agent?.id);
  const goPanel = (value: string) => {
    const change = () => { if (agent?.id && savedAgent) select(savedAgent, false); setParams({ agent: agent?.id || '', panel: value }); };
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: change }); else change();
  };
  useEffect(() => {
    const id = params.get('agent');
    if (!id && !params.has('new') && !connectionsView && agent) { setAgent(null); return; }
    if (id && loaded && agent?.id !== id) {
      const found = catalog.agents.find(item => item.id === id);
      if (found) select(found, false);
      else setError('Agent not found or no longer available to you.');
    }
  }, [params, loaded, catalog.agents, agent?.id]);
  const copy = async (value: string) => {
    try { await navigator.clipboard.writeText(value); message.success('Copied'); }
    catch { message.error('Unable to copy. Select and copy the text manually.'); }
  };
  const connectionTable = <Table rowKey="id" loading={busy && !loaded} dataSource={connections} scroll={{ x: 700 }} pagination={{ pageSize: 12, showSizeChanger: false }} locale={{ emptyText: <Empty description="No model connections match your search" /> }} columns={[
    { title: 'Connection', render: (_, item) => <button className="agent-record-link" onClick={() => choose(item)}>{item.name}</button> },
    { title: 'API endpoint', dataIndex: 'base_url' }, { title: 'Agent references', render: (_, item) => catalog.agents.filter(row => row.connection_id === item.id).length }, { title: 'Revision', dataIndex: 'revision' },
  ]} />;
  const executorTable = <Table rowKey="id" loading={busy && !loaded} dataSource={executors} pagination={false} columns={[
    { title: 'Executor', render: (_, item) => <div className="agent-record-name"><strong>{item.name}</strong><small>{item.id}</small></div> },
    { title: 'Tools', render: (_, item) => <div className="agent-tool-tags">{item.tools.map(tool => <code key={tool}>{tool}</code>)}</div> },
    { title: 'Agents', render: (_, item) => <div className="agent-tool-tags">{catalog.agents.filter(row => row.harness === item.id).map(row => <button className="agent-record-link" key={row.id} onClick={() => { select(row, false); setParams({ agent: row.id }); }}>{row.name}</button>)}</div> },
  ]} />;
  const editor = selected && <Form id="agent-config-form" layout="vertical" onFinish={() => void act(() => save())}>{error && <Alert type="error" message={error} />}
        {readOnly && <Alert type="info" message={agent?.system_key ? <>Built-in system agent. Its definition and workflow cannot be edited or deleted. Model connections are configured separately in <a href="/settings?section=agents">System settings</a>.</> : 'This agent is available for you to use. Only its owner can edit its definition and sharing.'} />}
        <fieldset disabled={busy || readOnly}><div className="agent-editor-title"><h2>{selected.id ? selected.name : connectionsView ? 'New connection' : 'New agent'}</h2>{selected.id && <small>Revision {selected.revision}</small>}</div>
          {connectionsView && connection ? <>
            <Form.Item label="Connection name"><Input aria-label="Connection name" required value={connection.name} onChange={(e) => { setConnection({ ...connection, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Responses API base URL"><Input aria-label="Responses API base URL" required value={connection.base_url} onChange={(e) => { setConnection({ ...connection, base_url: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="API key"><Input aria-label="API key" type="password" required={!connection.id} autoComplete="new-password" value={apiKey} placeholder={connection.id ? 'Configured — leave blank to keep' : 'Enter API key'} onChange={(e) => { setApiKey(e.target.value); setDirty(true); }} /></Form.Item><p className="agent-help">Updating a connection requires its agents to be tested again. Keys are not stored in browser storage.</p>
          </> : agent && <>
            <details className="dify-identity-settings"><summary>Agent identity & access</summary><div className="dify-icon-picker" aria-label="Agent icon">{['🤖', '🔎', '🧭', '📝', '💡', '🪄', '📊', '💬'].map(icon => <button type="button" key={icon} aria-label={`Use ${icon} icon`} aria-pressed={(agent.icon || '🤖') === icon} onClick={() => { setAgent({ ...agent, icon }); setDirty(true); }}>{icon}</button>)}</div><Form.Item label="Name"><Input aria-label="Agent name" required value={agent.name} onChange={(e) => { setAgent({ ...agent, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Description"><Input aria-label="Description" value={agent.description} onChange={(e) => { setAgent({ ...agent, description: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Visibility"><select aria-label="Agent visibility" value={agent.visibility} onChange={(e) => { setAgent({ ...agent, visibility: e.target.value as AgentDefinition['visibility'] }); setDirty(true); }}><option value="private">Private — only you</option><option value="shared">Share with selected users</option><option value="public">Public — all signed-in users</option></select></Form.Item>
            {agent.visibility === 'shared' && agent.can_edit && <Form.Item label="Recipients"><Input aria-label="Share with users" required value={recipients} placeholder="Registered email addresses, separated by commas" onChange={(e) => { setRecipients(e.target.value); setDirty(true); }} /></Form.Item>}
            </details><h3 className="agent-editor-section">MODEL</h3><Form.Item label="Model connection"><select required aria-label="Model connection" value={agent.connection_id ?? ''} onChange={(e) => { setAgent({ ...agent, connection_id: e.target.value }); setDirty(true); }}><option value="">{agent.system_key ? 'System model defaults' : 'Choose a connection'}</option>{readOnly && agent.connection_id && <option value={agent.connection_id}>Owner-managed connection</option>}{!readOnly && catalog.connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Form.Item>
            {!readOnly && !catalog.connections.length && <Button onClick={() => switchView(true)}>Add a model connection</Button>}
            <div className="agent-field-pair"><Form.Item label="Model ID"><Input aria-label="Model ID" required value={agent.model} onChange={(e) => { setAgent({ ...agent, model: e.target.value }); setDirty(true); }} /></Form.Item><Form.Item label="Reasoning effort"><select aria-label="Reasoning effort" value={agent.reasoning_effort} onChange={(e) => { setAgent({ ...agent, reasoning_effort: e.target.value }); setDirty(true); }}>{['low', 'medium', 'high', 'xhigh', 'max'].map((value) => <option key={value}>{value}</option>)}</select></Form.Item></div>
            <Form.Item label="PROMPT"><textarea aria-label="Agent instructions" placeholder="Write instructions here…" rows={12} maxLength={12000} value={agent.instructions} onChange={(e) => { setAgent({ ...agent, instructions: e.target.value }); setDirty(true); }} /></Form.Item>
            <p className="agent-help">{agent.system_key ? `Built-in workflow: ${catalog.harnesses[agent.harness]?.tools.join(' → ')}` : 'Define this agent’s purpose, behavior, and response style. Its configuration can be used across all projects.'}</p>
            <h3 className="agent-editor-section">TOOLS</h3><div className="agent-tool-tags">{catalog.harnesses[agent.harness]?.tools.map(tool => <code key={tool}>{tool}</code>)}</div><details className="dify-advanced-settings"><summary>ADVANCED SETTINGS</summary><div className="agent-field-pair"><Form.Item label="Request timeout (seconds)"><Input type="number" min={10} max={300} value={agent.timeout_seconds} onChange={(e) => { setAgent({ ...agent, timeout_seconds: Number(e.target.value) }); setDirty(true); }} /></Form.Item><Form.Item label="Maximum output tokens"><Input type="number" min={1024} max={16384} value={agent.max_output_tokens} onChange={(e) => { setAgent({ ...agent, max_output_tokens: Number(e.target.value) }); setDirty(true); }} /></Form.Item></div>
            </details><Form.Item label="Published"><Switch aria-label="Enable agent" checked={agent.enabled} onChange={(enabled) => { setAgent({ ...agent, enabled }); setDirty(true); }} /></Form.Item><p role="status" className="agent-help">{agent.verified && !dirty ? 'Saved model configuration verified.' : 'Save, test the model connection, then enable. Testing sends a real request and uses model quota.'}</p>
          </>}
        </fieldset>
        {!readOnly && <footer><Button type="primary" htmlType="submit" loading={busy} disabled={!dirty || busy}>Save</Button>{!connectionsView && agent?.id && <Button disabled={busy || dirty} onClick={() => void act(async () => { const result = await testAgentDefinition(agent.id); const data = await load(); select(data.agents.find((row) => row.id === agent.id)!, false); message.success(`Connection verified (${result.latency_ms} ms)`); })}>Test connection</Button>}<Button disabled={busy || !dirty} onClick={() => select(connectionsView ? catalog.connections.find((row) => row.id === selected.id) ?? null : catalog.agents.find((row) => row.id === selected.id) ?? null)}>Discard</Button>{selected.id && <Button danger disabled={busy || dirty} onClick={remove}>Delete</Button>}</footer>}
      </Form>;
  return <div className={`agent-management dify-workspace${agent && !connectionsView ? ` is-detail${sidebarCollapsed ? ' is-rail' : ''}` : ''}`}>
    <aside className="dify-sidebar">
      <Link className="dify-workspace-brand" to="/automation/workflows">ASTRAL <small>AGENT STUDIO</small></Link>
      {agent && !connectionsView ? <><button className="dify-sidebar-toggle" aria-label={sidebarCollapsed ? 'Expand agent sidebar' : 'Collapse agent sidebar'} onClick={() => setSidebarCollapsed(value => !value)}>◫</button><button className="dify-back" onClick={closeEditor}>‹ <span>AGENTS</span></button><div className="dify-agent-identity"><span className="dify-agent-avatar">{agent.icon || '🤖'}</span><strong>{agent.name || 'New agent'}</strong></div><nav aria-label="Agent detail sections">{[['configure', 'Configure', '◎'], ['access', 'Access Point', '✧'], ['logs', 'Logs', '▤'], ['monitoring', 'Monitoring', '◉']].map(([value, label, icon]) => <button key={value} disabled={!agent.id && value !== 'configure'} aria-label={label} aria-current={panel === value ? 'page' : undefined} onClick={() => goPanel(value)}><span>{icon}</span><span className="dify-nav-label">{label}</span></button>)}</nav></> : <><div className="dify-workspace-identity"><span>A</span><div><strong>Agent workspace</strong><small>System & personal agents</small></div></div><nav aria-label="Agent management sections"><Link to="/automation/workflows"><span>⌂</span>Studio</Link><button aria-current={!connectionsView && !executorsView ? 'page' : undefined} onClick={() => switchView(false)}><RobotOutlined />Agents <small>{catalog.agents.length}</small></button><button aria-current={connectionsView ? 'page' : undefined} onClick={() => switchView(true)}><ApiOutlined />Model connections <small>{catalog.connections.length}</small></button><button aria-current={executorsView ? 'page' : undefined} onClick={() => switchView('executors')}><RobotOutlined />Executors <small>{Object.keys(catalog.harnesses).length}</small></button></nav></>}
      <Link className="dify-sidebar-footer" to="/settings">Workspace settings</Link>
    </aside>
    <main className="dify-main">
      {agent && !connectionsView ? <>
        <header className="dify-detail-header"><div><strong>{agent.name || 'New agent'}</strong><span className={`dify-publication ${savedAgent?.enabled && savedAgent.verified ? 'is-published' : ''}`}>{savedAgent?.enabled && savedAgent.verified ? 'Published' : 'Draft'}</span>{dirty && <small>Unsaved changes</small>}</div><div><Button onClick={() => setPreviewOpen(value => !value)}>{previewOpen ? 'Hide preview' : 'Preview'}</Button>{!readOnly && <><Button htmlType="submit" form="agent-config-form" disabled={!dirty || busy} loading={busy}>Save</Button><Button type="primary" disabled={!agent.id || !agent.verified || dirty || busy || agent.enabled} onClick={() => void act(() => save(true))}>Publish</Button></>}</div></header>
        {panel === 'configure' ? <div className={`dify-config-layout${previewOpen ? ' has-preview' : ''}`}><section className="dify-config-editor" aria-label="Agent configuration"><h1 className="dify-config-title">Configure</h1>{editor}</section>{previewOpen && <div className="dify-preview-pane"><div className="dify-preview-modes" role="group" aria-label="Agent testing mode"><button aria-pressed={buildMode} onClick={() => setBuildMode(true)}>Build</button><button aria-pressed={!buildMode} onClick={() => setBuildMode(false)}>Preview</button></div>{buildMode ? <AgentBuilder key={agent.id || 'new'} agent={savedAgent || agent} onApply={draft => { setAgent({ ...agent, ...draft }); setDirty(true); }} /> : <AgentPreview key={agent.id || 'new'} agent={savedAgent || agent} />}</div>}</div>
          : panel === 'logs' || panel === 'monitoring' ? <AgentActivity key={`${agent.id}:${panel}`} agent={agent} monitoring={panel === 'monitoring'} />
          : <section className="dify-access"><header className="dify-section-heading"><div><h1>Access Point</h1><p>Every surface this agent is reachable from.</p></div></header><div className="dify-access-grid">
            <article><header><span className="dify-access-icon">▣</span><h2>Web app</h2><span className="dify-service-state">{savedAgent?.enabled && savedAgent.verified ? 'IN SERVICE' : 'NOT PUBLISHED'}</span></header><p>Access URL</p><div className="dify-access-url"><code>{`${window.location.origin}/agents?agent=${agent.id}&panel=configure`}</code><Button onClick={() => void copy(`${window.location.origin}/agents?agent=${agent.id}&panel=configure`)}>Copy</Button></div><footer><Button onClick={() => { goPanel('configure'); setPreviewOpen(true); }}>Launch</Button></footer></article>
            <article><header><span className="dify-access-icon">⌘</span><h2>Backend service API</h2><span className="dify-service-state">{savedAgent?.enabled && savedAgent.verified ? 'IN SERVICE' : 'NOT PUBLISHED'}</span></header><p>Service API endpoint · existing account authentication required</p><div className="dify-access-url"><code>{apiUrl('/api/v1/agents/runs')}</code><Button onClick={() => void copy(JSON.stringify({ agent_id: agent.id, product_domain: 'platform', input: agent.harness === 'collection' ? { url: 'https://example.com', goal: 'Describe the data to collect' } : { task: 'Your task' } }, null, 2))}>Copy request</Button></div><footer><Link to="/account/settings">Account API keys</Link></footer></article>
          </div><h2>Sharing</h2><p>{agent.visibility === 'public' ? 'Available to all signed-in users.' : agent.visibility === 'shared' ? `Shared with ${agent.shared_with.length} selected users.` : 'Private. Only you can use this agent.'} {readOnly ? 'Sharing is managed by the owner.' : 'Change access in Configure.'}</p><h2>Execution tools</h2><div className="agent-tool-tags">{catalog.harnesses[agent.harness]?.tools.map(tool => <code key={tool}>{tool}</code>)}</div></section>}
      </> : <>
        <header className="dify-catalog-heading"><h1>{connectionsView ? 'Model connections' : executorsView ? 'Executors' : 'Agents'}</h1><Button icon={<ReloadOutlined />} disabled={busy} onClick={() => void act(async () => { await load(); })}>Refresh</Button></header>
        <div className="dify-catalog-toolbar">{!connectionsView && !executorsView && <div className="dify-status-tabs" role="group" aria-label="Agent publication status">{[['all', 'All'], ['published', 'Published'], ['draft', 'Drafts']].map(([value, label]) => <button key={value} aria-pressed={statusFilter === value} onClick={() => setStatusFilter(value)}>{label}{value !== 'all' && <small>{catalog.agents.filter(item => value === 'published' ? agentStatus(item) === 'enabled' : agentStatus(item) !== 'enabled').length}</small>}</button>)}</div>}<Input allowClear prefix={<SearchOutlined />} aria-label="Search agents and connections" placeholder="Search agents by name…" value={search} onChange={event => setSearch(event.target.value)} />{!connectionsView && !executorsView && <label className="dify-created-by"><input type="checkbox" checked={scopeFilter === 'mine'} onChange={event => setScopeFilter(event.target.checked ? 'mine' : 'all')} />Created by me</label>}<div className="dify-toolbar-end"><select aria-label="Sort agents" value={sort} onChange={event => setSort(event.target.value)}><option value="modified">Sort by Last modified</option><option value="created">Sort by Created at</option><option value="name">Sort by Name</option></select>{!executorsView && <Button type="primary" icon={<PlusOutlined />} disabled={busy} onClick={create}>{connectionsView ? 'New connection' : 'Create'}</Button>}</div></div>
        {error && <Alert type="error" message={error} />}
        {connectionsView ? connectionTable : executorsView ? executorTable : <div className="dify-agent-grid" aria-busy={busy && !loaded}>{agents.map(item => <button className="dify-agent-card" key={item.id} onClick={() => choose(item)}><span className={`dify-card-ribbon${agentStatus(item) === 'enabled' ? ' is-published' : ''}`}>{agentStatus(item) === 'enabled' ? 'PUBLISHED' : 'DRAFT'}</span><div className="dify-card-identity"><span className="dify-agent-avatar">{item.icon || '🤖'}</span><div><strong>{item.name}</strong><span>{catalog.harnesses[item.harness]?.name || item.harness}</span></div></div><p>{item.description || 'No description'}</p><footer><span>{item.system_key ? 'System' : item.can_edit ? 'Created by me' : 'Shared with you'}</span><span>·</span><time>{item.updated_at ? new Date(item.updated_at).toLocaleString() : `Revision ${item.revision}`}</time></footer></button>)}{!agents.length && <Empty description={!loaded && busy ? 'Loading agents…' : 'No agents match these filters'} />}</div>}
      </>}
    </main>
    <Drawer open={Boolean(connection && connectionsView)} title={connection?.id ? 'Connection details' : 'New connection'} width="min(640px, 100vw)" onClose={closeEditor} className="agent-editor-drawer" destroyOnHidden>{connectionsView && connection && editor}</Drawer>
  </div>;
}

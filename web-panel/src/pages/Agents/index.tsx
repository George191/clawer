import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Form, Input, Switch, Modal, message } from '@/components/ui';
import { RobotOutlined, PlusOutlined, ApiOutlined } from '@/components/ui/icons';
import { fetchAgentCatalog, saveAgentConnection, saveAgentDefinition, deleteAgentItem, testAgentDefinition, type AgentCatalog, type AgentDefinition, type AgentConnection } from '@/services/agentManagement';
import './style.css';

const blankAgent: AgentDefinition = { id: '', name: '', description: '', harness: 'assistant', connection_id: '', instructions: '', model: 'gpt-6-sol', reasoning_effort: 'high', timeout_seconds: 120, max_output_tokens: 8192, enabled: false, revision: 0, verified: false, visibility: 'private', shared_with: [], system_key: null, can_edit: true };
const blankConnection: AgentConnection = { id: '', name: '', base_url: 'https://api.openai.com/v1', revision: 0 };

export default function Agents() {
  const [params, setParams] = useSearchParams();
  const connectionsView = params.get('view') === 'connections';
  const [catalog, setCatalog] = useState<AgentCatalog>({ agents: [], connections: [], harnesses: {} });
  const [agent, setAgent] = useState<AgentDefinition | null>(null);
  const [connection, setConnection] = useState<AgentConnection | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [recipients, setRecipients] = useState('');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  async function load() { const data = await fetchAgentCatalog(); setCatalog(data); return data; }
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
  function switchView(view: boolean) {
    if (dirty) { Modal.confirm({ title: 'Discard unsaved changes?', onOk: () => { setDirty(false); setParams(view ? { view: 'connections' } : {}); setAgent(null); setConnection(null); setApiKey(''); } }); return; }
    setParams(view ? { view: 'connections' } : {}); setAgent(null); setConnection(null); setApiKey('');
  }
  function choose(next: AgentDefinition | AgentConnection) {
    if (dirty) Modal.confirm({ title: 'Discard unsaved changes?', onOk: () => select(next) }); else select(next);
  }
  async function save() {
    if (connectionsView && connection) {
      const result = await saveAgentConnection({ name: connection.name, base_url: connection.base_url, expected_revision: connection.revision, ...(apiKey ? { api_key: apiKey } : {}) }, connection.id || undefined);
      const data = await load(); select(data.connections.find((item) => item.id === result.id)!, true);
    } else if (agent) {
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
  const rows = (connectionsView ? catalog.connections : catalog.agents).filter((item) => item.name.toLowerCase().includes(search.toLowerCase()));
  return <div className="agent-management">
    <header><div><h1>Agents</h1><p>System and personal agents across all projects. Keep agents private, publish them, or share with selected users.</p></div><Button onClick={() => void act(async () => { await load(); })} disabled={busy || dirty}>Refresh</Button></header>
    <nav className="agent-tabs" aria-label="Agent management sections"><button aria-current={!connectionsView ? 'page' : undefined} onClick={() => switchView(false)}><RobotOutlined />Agents <span>{catalog.agents.length}</span></button><button aria-current={connectionsView ? 'page' : undefined} onClick={() => switchView(true)}><ApiOutlined />Model connections <span>{catalog.connections.length}</span></button></nav>
    {error && <Alert type="error" message={error} />}
    <div className="agent-manager-grid">
      <aside><div className="agent-list-tools"><Input aria-label="Search agents and connections" placeholder="Search…" value={search} onChange={(event) => setSearch(event.target.value)} /><Button aria-label={connectionsView ? 'New connection' : 'New agent'} icon={<PlusOutlined />} disabled={busy} onClick={() => choose(connectionsView ? { ...blankConnection } : { ...blankAgent, connection_id: catalog.connections[0]?.id ?? '' })} /></div>
        <div className="agent-list">{rows.map((row) => <button key={row.id} className={selected?.id === row.id ? 'is-selected' : ''} onClick={() => choose(row)} disabled={busy}><strong>{row.name}</strong><small>{'model' in row ? `${row.system_key ? 'System' : row.can_edit ? row.visibility : 'Shared with you'} · ${row.model} · ${row.enabled ? row.verified ? 'Enabled' : 'Needs verification' : 'Disabled'}` : row.base_url}</small></button>)}{!rows.length && <p>{busy ? 'Loading…' : connectionsView ? 'Add a model connection once and reuse it across agents.' : 'Create an agent using a registered executor and a model connection.'}</p>}</div>
      </aside>
      <main>{!selected ? <div className="agent-empty"><RobotOutlined /><h2>{connectionsView ? 'Model connections' : 'Available agents'}</h2><p>{connectionsView ? 'Credentials are encrypted on the server and never returned to the browser.' : 'Choose an agent or create one. Definitions and model bindings are saved on the server.'}</p></div> : <Form layout="vertical" onFinish={() => void act(save)}>
        {readOnly && <Alert type="info" message={agent?.system_key ? <>Built-in system agent, available across all projects. Administrators configure its model in <a href="/settings?section=agents">System settings</a>.</> : 'This agent is available for you to use. Only its owner can edit its definition and sharing.'} />}
        <fieldset disabled={busy || readOnly}><div className="agent-editor-title"><h2>{selected.id ? selected.name : connectionsView ? 'New connection' : 'New agent'}</h2>{selected.id && <small>Revision {selected.revision}</small>}</div>
          {connectionsView && connection ? <>
            <Form.Item label="Connection name"><Input aria-label="Connection name" required value={connection.name} onChange={(e) => { setConnection({ ...connection, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Responses API base URL"><Input aria-label="Responses API base URL" required value={connection.base_url} onChange={(e) => { setConnection({ ...connection, base_url: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="API key"><Input aria-label="API key" type="password" required={!connection.id} autoComplete="new-password" value={apiKey} placeholder={connection.id ? 'Configured — leave blank to keep' : 'Enter API key'} onChange={(e) => { setApiKey(e.target.value); setDirty(true); }} /></Form.Item><p className="agent-help">Updating a connection requires its agents to be tested again. Keys are not stored in browser storage.</p>
          </> : agent && <>
            <Form.Item label="Name"><Input aria-label="Agent name" required value={agent.name} onChange={(e) => { setAgent({ ...agent, name: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Description"><Input aria-label="Description" value={agent.description} onChange={(e) => { setAgent({ ...agent, description: e.target.value }); setDirty(true); }} /></Form.Item>
            <Form.Item label="Visibility"><select aria-label="Agent visibility" value={agent.visibility} onChange={(e) => { setAgent({ ...agent, visibility: e.target.value as AgentDefinition['visibility'] }); setDirty(true); }}><option value="private">Private — only you</option><option value="shared">Share with selected users</option><option value="public">Public — all signed-in users</option></select></Form.Item>
            {agent.visibility === 'shared' && agent.can_edit && <Form.Item label="Recipients"><Input aria-label="Share with users" required value={recipients} placeholder="Registered email addresses, separated by commas" onChange={(e) => { setRecipients(e.target.value); setDirty(true); }} /></Form.Item>}
            <div className="agent-field-pair"><Form.Item label="Executor"><select aria-label="Executor" value={agent.harness} onChange={(e) => { setAgent({ ...agent, harness: e.target.value }); setDirty(true); }}>{Object.entries(catalog.harnesses).map(([key, item]) => <option key={key} value={key}>{item.name}</option>)}</select></Form.Item>
              <Form.Item label="Model connection"><select required aria-label="Model connection" value={agent.connection_id ?? ''} onChange={(e) => { setAgent({ ...agent, connection_id: e.target.value }); setDirty(true); }}><option value="">{agent.system_key ? 'System model defaults' : 'Choose a connection'}</option>{readOnly && agent.connection_id && <option value={agent.connection_id}>Owner-managed connection</option>}{!readOnly && catalog.connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Form.Item></div>
            {!readOnly && !catalog.connections.length && <Button onClick={() => switchView(true)}>Add a model connection</Button>}
            <div className="agent-field-pair"><Form.Item label="Model ID"><Input aria-label="Model ID" required value={agent.model} onChange={(e) => { setAgent({ ...agent, model: e.target.value }); setDirty(true); }} /></Form.Item><Form.Item label="Reasoning effort"><select aria-label="Reasoning effort" value={agent.reasoning_effort} onChange={(e) => { setAgent({ ...agent, reasoning_effort: e.target.value }); setDirty(true); }}>{['low', 'medium', 'high', 'xhigh', 'max'].map((value) => <option key={value}>{value}</option>)}</select></Form.Item></div>
            <Form.Item label="Task instructions"><textarea aria-label="Task instructions" rows={7} maxLength={12000} value={agent.instructions} onChange={(e) => { setAgent({ ...agent, instructions: e.target.value }); setDirty(true); }} /></Form.Item>
            <p className="agent-help">Tools: {catalog.harnesses[agent.harness]?.tools.join(' → ')}. Instructions cannot add tools or execute arbitrary code.</p>
            <div className="agent-field-pair"><Form.Item label="Request timeout (seconds)"><Input type="number" min={10} max={300} value={agent.timeout_seconds} onChange={(e) => { setAgent({ ...agent, timeout_seconds: Number(e.target.value) }); setDirty(true); }} /></Form.Item><Form.Item label="Maximum output tokens"><Input type="number" min={1024} max={16384} value={agent.max_output_tokens} onChange={(e) => { setAgent({ ...agent, max_output_tokens: Number(e.target.value) }); setDirty(true); }} /></Form.Item></div>
            <Form.Item label="Enabled"><Switch aria-label="Enable agent" checked={agent.enabled} onChange={(enabled) => { setAgent({ ...agent, enabled }); setDirty(true); }} /></Form.Item><p role="status" className="agent-help">{agent.verified && !dirty ? 'Saved model configuration verified.' : 'Save, test the model connection, then enable. Testing sends a real request and uses model quota.'}</p>
          </>}
        </fieldset>
        {!readOnly && <footer><Button type="primary" htmlType="submit" loading={busy} disabled={!dirty}>Save</Button>{!connectionsView && agent?.id && <Button disabled={busy || dirty} onClick={() => void act(async () => { const result = await testAgentDefinition(agent.id); const data = await load(); select(data.agents.find((row) => row.id === agent.id)!, false); message.success(`Connection verified (${result.latency_ms} ms)`); })}>Test connection</Button>}<Button disabled={busy || !dirty} onClick={() => select(connectionsView ? catalog.connections.find((row) => row.id === selected.id) ?? null : catalog.agents.find((row) => row.id === selected.id) ?? null)}>Discard</Button>{selected.id && <Button danger disabled={busy || dirty} onClick={remove}>Delete</Button>}</footer>}
      </Form>}</main>
    </div>
  </div>;
}

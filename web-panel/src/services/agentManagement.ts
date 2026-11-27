import client, { unwrap } from './api';

export type AgentConnection = { id: string; name: string; base_url: string; revision: number; updated_at?: string };
export type AgentDefinition = {
  id: string; name: string; description: string; harness: string; connection_id: string | null;
  instructions: string; model: string; reasoning_effort: string; enabled: boolean;
  icon?: string;
  timeout_seconds: number; max_output_tokens: number; revision: number; verified: boolean; visibility: 'private' | 'shared' | 'public'; shared_with: string[]; system_key: string | null; can_edit: boolean;
  updated_at?: string;
};
export type AgentCatalog = { connections: AgentConnection[]; agents: AgentDefinition[]; harnesses: Record<string, { name: string; tools: string[] }> };
export const fetchAgentCatalog = () => client.get('/agents/catalog').then((r) => unwrap<AgentCatalog>(r.data));
export const saveAgentDefinition = (agent: Omit<AgentDefinition, 'id' | 'verified' | 'revision' | 'system_key' | 'can_edit' | 'updated_at'> & { expected_revision: number }, id?: string) =>
  (id ? client.put(`/agents/definitions/${id}`, agent) : client.post('/agents/definitions', agent)).then((r) => unwrap<{ id: string }>(r.data));
export const saveAgentConnection = (connection: { name: string; base_url: string; api_key?: string; expected_revision: number }, id?: string) =>
  (id ? client.put(`/agents/connections/${id}`, connection) : client.post('/agents/connections', connection)).then((r) => unwrap<{ id: string }>(r.data));
export const deleteAgentItem = (kind: 'agents' | 'connections', id: string, revision: number) => client.delete(`/agents/${kind === 'agents' ? 'definitions' : kind}/${id}`, { params: { revision } });
export const testAgentDefinition = (id: string) => client.post(`/agents/definitions/${id}/test`, {}, { timeout: 610000 }).then((r) => unwrap<{ latency_ms: number }>(r.data));

export type ManagedAgentRun = {
  id: string; agent_id: string | null; product_domain: string; harness: string;
  request: { input: Record<string, unknown> }; model_config: Record<string, unknown>;
  status: string; stage: number; stage_count: number; result: Record<string, unknown> | null;
  error_code: string | null; attempts: number; created_at: string; updated_at: string;
};
export type ManagedAgentEvent = { seq: number; event: Record<string, unknown>; created_at: string };
export const fetchManagedAgentRuns = (id: string, signal?: AbortSignal) => client.get('/agents/runs', { params: { agent_id: id, limit: 100 }, signal }).then(r => unwrap<{ items: ManagedAgentRun[] }>(r.data).items);
export const fetchManagedAgentRun = (id: string, signal?: AbortSignal) => client.get(`/agents/runs/${id}`, { signal }).then(r => unwrap<ManagedAgentRun>(r.data));
export const fetchManagedAgentEvents = (id: string, after = 0, signal?: AbortSignal) => client.get(`/agents/runs/${id}/events`, { params: { after, limit: 100 }, signal }).then(r => unwrap<{ items: ManagedAgentEvent[] }>(r.data).items);
export const createManagedAgentRun = (id: string, input: Record<string, unknown>) => client.post('/agents/runs', { agent_id: id, product_domain: 'platform', input }, { headers: { 'Idempotency-Key': crypto.randomUUID() } }).then(r => unwrap<ManagedAgentRun>(r.data));
export const cancelManagedAgentRun = (id: string) => client.post(`/agents/runs/${id}/cancel`).then(r => unwrap<ManagedAgentRun>(r.data));
export type AgentBuildDraft = Pick<AgentDefinition, 'name' | 'description' | 'instructions'>;
export const buildManagedAgent = (agent: AgentDefinition, message: string) => client.post(`/agents/definitions/${agent.id}/build`, { message, expected_revision: agent.revision }, { timeout: 610000 }).then(r => unwrap<AgentBuildDraft>(r.data));

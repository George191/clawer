import client, { unwrap } from './api';

export type AgentConnection = { id: string; name: string; base_url: string; revision: number };
export type AgentDefinition = {
  id: string; name: string; description: string; harness: string; connection_id: string | null;
  instructions: string; model: string; reasoning_effort: string; enabled: boolean;
  timeout_seconds: number; max_output_tokens: number; revision: number; verified: boolean; visibility: 'private' | 'shared' | 'public'; shared_with: string[]; system_key: string | null; can_edit: boolean;
};
export type AgentCatalog = { connections: AgentConnection[]; agents: AgentDefinition[]; harnesses: Record<string, { name: string; tools: string[] }> };
export const fetchAgentCatalog = () => client.get('/agents/catalog').then((r) => unwrap<AgentCatalog>(r.data));
export const saveAgentDefinition = (agent: Omit<AgentDefinition, 'id' | 'verified' | 'revision' | 'system_key' | 'can_edit'> & { expected_revision: number }, id?: string) =>
  (id ? client.put(`/agents/definitions/${id}`, agent) : client.post('/agents/definitions', agent)).then((r) => unwrap<{ id: string }>(r.data));
export const saveAgentConnection = (connection: { name: string; base_url: string; api_key?: string; expected_revision: number }, id?: string) =>
  (id ? client.put(`/agents/connections/${id}`, connection) : client.post('/agents/connections', connection)).then((r) => unwrap<{ id: string }>(r.data));
export const deleteAgentItem = (kind: 'agents' | 'connections', id: string, revision: number) => client.delete(`/agents/${kind === 'agents' ? 'definitions' : kind}/${id}`, { params: { revision } });
export const testAgentDefinition = (id: string) => client.post(`/agents/definitions/${id}/test`, {}, { timeout: 610000 }).then((r) => unwrap<{ latency_ms: number }>(r.data));

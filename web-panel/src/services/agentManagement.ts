import client, { unwrap } from './api';

export type AgentConnection = { id: string; name: string; base_url: string; revision: number };
export type AgentDefinition = {
  id: string; name: string; description: string; harness: string; connection_id: string;
  instructions: string; model: string; reasoning_effort: string; enabled: boolean;
  timeout_seconds: number; max_output_tokens: number; revision: number; verified: boolean;
};
export type AgentCatalog = { connections: AgentConnection[]; agents: AgentDefinition[]; harnesses: Record<string, { name: string; tools: string[] }> };
export const fetchAgentCatalog = () => client.get('/agent-management').then((r) => unwrap<AgentCatalog>(r.data));
export const saveAgentDefinition = (agent: Omit<AgentDefinition, 'id' | 'verified' | 'revision'> & { expected_revision: number }, id?: string) =>
  (id ? client.put(`/agent-management/agents/${id}`, agent) : client.post('/agent-management/agents', agent)).then((r) => unwrap<{ id: string }>(r.data));
export const saveAgentConnection = (connection: { name: string; base_url: string; api_key?: string; expected_revision: number }, id?: string) =>
  (id ? client.put(`/agent-management/connections/${id}`, connection) : client.post('/agent-management/connections', connection)).then((r) => unwrap<{ id: string }>(r.data));
export const deleteAgentItem = (kind: 'agents' | 'connections', id: string, revision: number) => client.delete(`/agent-management/${kind}/${id}`, { params: { revision } });
export const testAgentDefinition = (id: string) => client.post(`/agent-management/agents/${id}/test`, {}, { timeout: 610000 }).then((r) => unwrap<{ latency_ms: number }>(r.data));

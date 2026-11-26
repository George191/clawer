import client, { unwrap } from './api';

export interface AgentSettings {
  enabled: boolean;
  base_url: string;
  model: string;
  reasoning_effort: 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  timeout_seconds: number;
  max_output_tokens: number;
  api_key_configured: boolean;
  connection_verified: boolean;
  revision: number;
}
export type AgentUpdate = Omit<AgentSettings, 'revision' | 'api_key_configured' | 'connection_verified'> & {
  expected_revision: number;
  api_key?: string;
  clear_api_key?: boolean;
};
export const getAgentSettings = () =>
  client.get('/agents/settings').then((r) => unwrap<AgentSettings>(r.data));
export const saveAgentSettings = (data: AgentUpdate) =>
  client.put('/agents/settings', data).then((r) => unwrap<AgentSettings>(r.data));
export const testAgentSettings = () =>
  client.post('/agents/settings/test', {}, { timeout: 610000 }).then((r) => unwrap<{ ok: boolean; latency_ms: number }>(r.data));

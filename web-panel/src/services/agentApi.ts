import client, { unwrap } from './api';

export interface CollectionAgentSettings {
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
export type CollectionAgentUpdate = Omit<CollectionAgentSettings, 'revision' | 'api_key_configured' | 'connection_verified'> & {
  expected_revision: number;
  api_key?: string;
  clear_api_key?: boolean;
};
export const getCollectionAgentSettings = () =>
  client.get('/agents/collection/settings').then((r) => unwrap<CollectionAgentSettings>(r.data));
export const saveCollectionAgentSettings = (data: CollectionAgentUpdate) =>
  client.put('/agents/collection/settings', data).then((r) => unwrap<CollectionAgentSettings>(r.data));
export const testCollectionAgentSettings = () =>
  client.post('/agents/collection/settings/test', {}, { timeout: 310000 }).then((r) => unwrap<{ ok: boolean; latency_ms: number }>(r.data));

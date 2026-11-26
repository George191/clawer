import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Select, Space, Switch, Typography, message } from '@/components/ui';
import { getCollectionAgentSettings, saveCollectionAgentSettings, testCollectionAgentSettings, type CollectionAgentSettings as Settings } from '@/services/agentApi';

export default function CollectionAgentSettings() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    setBusy(true); setError('');
    try { const config = await getCollectionAgentSettings(); setSaved(config); setDraft(config); setKey(''); setClearKey(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load collection agent settings.'); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  const dirty = Boolean(key || clearKey || JSON.stringify(saved) !== JSON.stringify(draft));
  async function save() {
    if (!draft || !saved) return;
    setBusy(true); setError('');
    try {
      const { api_key_configured: _key, connection_verified: _verified, revision: _revision, ...config } = draft;
      const next = await saveCollectionAgentSettings({ ...config, expected_revision: saved.revision, ...(key ? { api_key: key } : {}), clear_api_key: clearKey });
      setSaved(next); setDraft(next); setKey(''); setClearKey(false); message.success('Collection agent settings saved');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to save settings.'); }
    finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setError('');
    try {
      const result = await testCollectionAgentSettings();
      const config = await getCollectionAgentSettings(); setSaved(config); setDraft(config);
      message.success(`Model connection verified (${result.latency_ms} ms)`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Model connection failed.'); }
    finally { setBusy(false); }
  }
  return <>
    <Form layout="vertical" className="system-settings-form">
      <section className="system-settings-section">
        <Typography.Title level={5}>COLLECTION AGENT</Typography.Title>
        <Typography.Paragraph type="secondary">Configure the OpenAI Responses connection used only by the collection harness. Save with the agent disabled, test the connection, then enable collection.</Typography.Paragraph>
        {error && <Alert type="error" message={error} />}
        {!draft && <Button onClick={() => void load()} loading={busy}>Reload configuration</Button>}
        {draft && <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
          <Form.Item label="Enabled"><Switch aria-label="Enable collection agent" checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} /></Form.Item>
          <Form.Item label="API base URL"><Input aria-label="Collection API base URL" value={draft.base_url} onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} /></Form.Item>
          <Form.Item label="API key"><Input type="password" aria-label="Collection API key" autoComplete="new-password" value={key} placeholder={draft.api_key_configured ? 'Configured — leave blank to keep' : 'Enter API key'} onChange={(e) => { setKey(e.target.value); setClearKey(false); }} /></Form.Item>
          <Form.Item><label><input type="checkbox" checked={clearKey} onChange={(e) => { setClearKey(e.target.checked); setKey(''); }} /> Clear saved API key</label></Form.Item>
          <Form.Item label="Model ID"><Input aria-label="Collection model ID" value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} /></Form.Item>
          <Form.Item label="Reasoning effort"><Select aria-label="Collection reasoning effort" value={draft.reasoning_effort} onChange={(reasoning_effort) => setDraft({ ...draft, reasoning_effort })} options={(['low', 'medium', 'high', 'xhigh', 'max'] as const).map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item label="Request timeout (seconds)"><Input type="number" min={10} max={300} aria-label="Collection request timeout" value={draft.timeout_seconds} onChange={(e) => setDraft({ ...draft, timeout_seconds: Number(e.target.value) })} /></Form.Item>
          <Form.Item label="Maximum output tokens"><Input type="number" min={1024} max={16384} aria-label="Collection output token limit" value={draft.max_output_tokens} onChange={(e) => setDraft({ ...draft, max_output_tokens: Number(e.target.value) })} /></Form.Item>
          <Typography.Text type="secondary">{draft.connection_verified ? 'Saved model connection verified.' : 'Connection not verified for this configuration.'} API keys are encrypted and never returned to the browser.</Typography.Text>
        </fieldset>}
      </section>
    </Form>
    <footer className="system-settings-actions"><Space wrap>
      <Button disabled={busy} onClick={() => void load()}>Reload</Button>
      <Button disabled={busy || !saved} onClick={() => { setDraft(saved); setKey(''); setClearKey(false); }}>Discard</Button>
      <Button loading={busy} disabled={busy || dirty || !saved?.api_key_configured} onClick={() => void test()}>Test connection</Button>
      <Button type="primary" disabled={busy || !draft || !dirty} onClick={() => void save()}>Save Changes</Button>
    </Space></footer>
  </>;
}

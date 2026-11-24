import React, { useEffect, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { bindSourceDamengCdc, fetchSourceDamengCdc, removeSourceDamengCdc } from '../../services/api';
import type { DataSource, SourceDamengCdcStatus } from '../../services/api';

export default function SourceDamengCdc({ source }: { source: DataSource }) {
  const [status, setStatus] = useState<SourceDamengCdcStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [topic, setTopic] = useState('');
  const [tables, setTables] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setStatus(undefined); setError('');
    fetchSourceDamengCdc(source.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value);
    }).catch(() => { if (!controller.signal.aborted) setError('Cannot load the saved DMDRS binding. Check connection and registry availability.'); });
    return () => controller.abort();
  }, [source.id, source.updatedAt, reload]);

  const save = async (remove: boolean) => {
    if (remove && !window.confirm('Remove this DMDRS output binding? The upstream DMDRS capture and its Kafka output will continue running.')) return;
    setBusy(true); setError('');
    try {
      if (remove) await removeSourceDamengCdc(source.id, source.updatedAt);
      else await bindSourceDamengCdc(source.id, source.updatedAt, topic, tables.split(',').map(value => value.trim()).filter(Boolean));
      setReload(value => value + 1);
    } catch (failure) {
      const detail = (failure as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
      setError(typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'DMDRS binding failed. Verify that its output topic exists on the project Kafka cluster.');
    } finally { setBusy(false); }
  };

  return <section className="ontology-source-cdc" aria-label="Dameng log CDC">
    <header><h3>Log CDC · DMDRS</h3><Button size="small" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh DMDRS binding</Button></header>
    <p>DMDRS captures database log changes. Deploy and license DMDRS, enable Dameng archive / logical logs, and configure the selected tables and Kafka output in DMDRS first.</p>
    <p>This form binds an existing DMDRS output on the project's Kafka cluster. Snapshotting, log positions, start, pause and recovery remain managed in DMDRS. Binding a topic does not verify capture health, message format or table coverage.</p>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status">Loading DMDRS binding…</p>}
    {status && <>
      {!status.schemaReady && <p role="alert">Environment preparation required: ask an administrator to install DMDRS binding storage before configuring this source.</p>}
      <p role="status">Binding: <strong>{status.state}</strong> · Capture health: externally managed.</p>
      {status.binding ? <>
        {status.binding.sourceChanged && <p role="alert">The source changed. Remove this binding and review the DMDRS chain before binding again.</p>}
        <dl><dt>Kafka output topic</dt><dd><code>{status.binding.topic}</code></dd><dt>Expected tables</dt><dd>{status.binding.tables.join(', ')}</dd></dl>
        <Button danger disabled={busy} onClick={() => void save(true)}>Remove output binding</Button>
      </> : <form onSubmit={event => { event.preventDefault(); void save(false); }}>
        <label>Existing DMDRS Kafka output topic<Input required value={topic} onChange={event => setTopic(event.target.value)} /></label>
        <label>Expected tables (comma-separated schema.table)<Input required value={tables} onChange={event => setTables(event.target.value)} /></label>
        <Button type="primary" htmlType="submit" disabled={busy || !status.schemaReady}>Bind DMDRS output</Button>
      </form>}
    </>}
  </section>;
}

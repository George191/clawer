import { LoadingIndicator } from '@/components/ui/loading';
import React, { useEffect, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { bindSourceDamengCdc, fetchSourceDamengCdc, removeSourceDamengCdc } from '../../services/api';
import type { DataSource, SourceDamengCdcStatus } from '../../services/api';
import './source-capture.css';

export default function SourceDamengCdc({ source, onBusyChange, onDirtyChange }: { source: DataSource; onBusyChange?: (busy: boolean) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [status, setStatus] = useState<SourceDamengCdcStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [topic, setTopic] = useState('');
  const [tables, setTables] = useState('');
  const [savedDraft, setSavedDraft] = useState(JSON.stringify(['', '']));
  const draftKey = JSON.stringify([topic, tables]);
  const dirty = !status?.binding && draftKey !== savedDraft;
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
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
    setBusy(true); onBusyChange?.(true); setError('');
    try {
      if (remove) await removeSourceDamengCdc(source.id, source.updatedAt);
      else {
        await bindSourceDamengCdc(source.id, source.updatedAt, topic, tables.split(',').map(value => value.trim()).filter(Boolean));
        setSavedDraft(draftKey);
      }
      setReload(value => value + 1);
    } catch (failure) {
      const detail = (failure as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
      setError(typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'DMDRS binding failed. Verify that its output topic exists on the project Kafka cluster.');
    } finally { setBusy(false); onBusyChange?.(false); }
  };

  return <section className="ontology-source-cdc source-capture" aria-label="Dameng log CDC">
    <header className="source-capture__heading"><div><span className="source-capture__eyebrow">External capture binding</span><h3>Log CDC · DMDRS</h3><p>Register the Kafka output of a capture already configured in DMDRS.</p></div><Button size="small" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh DMDRS binding</Button></header>
    <div className="source-capture__route" aria-label="DMDRS output path"><span>Dameng logs</span><span aria-hidden="true">→</span><span>External DMDRS task</span><span aria-hidden="true">→</span><strong>Existing Kafka topic</strong></div>
    <p className="source-capture__intro">This form binds an existing output on the project's Kafka cluster. It does not start DMDRS or write into a destination database. Snapshotting, log positions, start, pause and recovery remain managed in DMDRS.</p>
    <ol className="source-capture__steps" aria-label="DMDRS binding steps">
      <li><span>1</span><div><strong>Prepare DMDRS</strong><p>Configure and start capture outside this application.</p></div></li>
      <li><span>2</span><div><strong>Bind its existing output</strong><p>Enter the Kafka topic and expected source tables.</p></div></li>
      <li><span>3</span><div><strong>Verify the capture chain</strong><p>Check DMDRS health and inspect Kafka messages separately.</p></div></li>
    </ol>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status"><LoadingIndicator size={20}>Loading DMDRS binding…</LoadingIndicator></p>}
    {status && <div className="source-capture__layout">
      <section className="source-capture__panel source-capture__prepare" aria-label="DMDRS prerequisites">
        <h4>1. Prepare the external capture</h4>
        <p>Deploy and license DMDRS, enable Dameng archive / logical logs, and configure the selected tables and Kafka output in DMDRS first.</p>
        <p>Choose the historical snapshot and log starting position in DMDRS. This binding form cannot change them.</p>
      {!status.schemaReady && <p role="alert">Environment preparation required: ask an administrator to install DMDRS binding storage before configuring this source.</p>}
      </section>
      <section className="source-capture__panel source-capture__configure" aria-label="DMDRS output binding">
      <h4>2. Bind the Kafka output</h4>
      {status.binding ? <>
        {status.binding.sourceChanged && <p role="alert">The source changed. Remove this binding and review the DMDRS chain before binding again.</p>}
        <dl className="source-capture__facts"><div><dt>Kafka output topic</dt><dd><code>{status.binding.topic}</code></dd></div><div><dt>Expected tables</dt><dd>{status.binding.tables.join(', ')}</dd></div></dl>
        <p>The binding records the expected output and scope. To change them, remove the binding and review the DMDRS configuration first.</p>
      </> : <form onSubmit={event => { event.preventDefault(); void save(false); }}>
        <label>Existing DMDRS Kafka output topic<Input required value={topic} onChange={event => setTopic(event.target.value)} /></label>
        <p className="source-capture__help">Use a topic that already exists on the project's Kafka cluster. This action does not create a DMDRS task or a topic.</p>
        <label>Expected tables (comma-separated schema.table)<Input required value={tables} onChange={event => setTables(event.target.value)} /></label>
        <p className="source-capture__help">Match the tables selected in DMDRS, for example <code>PUBLIC.ITEMS, PUBLIC.USERS</code>.</p>
        <Button type="primary" htmlType="submit" disabled={busy || !status.schemaReady}>Bind DMDRS output</Button>
      </form>}
      </section>
      <section className="source-capture__panel source-capture__output" aria-label="DMDRS binding status">
        <h4>3. Check binding and delivery</h4>
        <p className="source-capture__status" role="status">Binding: <strong>{status.state}</strong> · Capture health: externally managed.</p>
        <p>Binding a topic does not verify capture health, message format or table coverage. Check the DMDRS task, then inspect messages on its output topic.</p>
        {status.binding && <Button danger disabled={busy} onClick={() => void save(true)}>Remove output binding</Button>}
        <div className="source-capture__note"><strong>Binding removal does not stop capture</strong><p>Remove only unregisters this output from the saved connection. The upstream DMDRS task and its Kafka output continue running. Pause or stop capture in DMDRS.</p></div>
      </section>
    </div>}
  </section>;
}

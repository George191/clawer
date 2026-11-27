import { LoadingIndicator } from '@/components/ui/loading';
import React, { useEffect, useState } from 'react';
import { Button, Input, NativeSelect } from '@/components/ui';
import { controlSourceCdc, createSourceCdc, fetchSourceCdc } from '../../services/api';
import type { DataSource, SourceCdcStatus } from '../../services/api';
import './source-capture.css';

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
  return typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'CDC operation failed. Refresh and try again.';
}

export default function SourceCdc({ source, onBusyChange, onDirtyChange }: { source: DataSource; onBusyChange?: (busy: boolean) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [status, setStatus] = useState<SourceCdcStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [tables, setTables] = useState('');
  const [snapshot, setSnapshot] = useState<'initial' | 'no_data'>('initial');
  const [publication, setPublication] = useState('');
  const [serverId, setServerId] = useState('');
  const [oracleDatabase, setOracleDatabase] = useState('');
  const [oraclePdb, setOraclePdb] = useState('');
  const [milvusKafka, setMilvusKafka] = useState('');
  const [milvusEtcd, setMilvusEtcd] = useState('');
  const [milvusRoot, setMilvusRoot] = useState('by-dev');
  const [milvusChannels, setMilvusChannels] = useState('');
  const [savedDraft, setSavedDraft] = useState('');
  const draftKey = JSON.stringify([tables, snapshot, publication, serverId, oracleDatabase, oraclePdb, milvusKafka, milvusEtcd, milvusRoot, milvusChannels]);
  const configured = Boolean(status?.selection);
  const changed = savedDraft ? draftKey !== savedDraft : Boolean(tables || snapshot !== 'initial' || publication || serverId || oracleDatabase || oraclePdb || milvusKafka || milvusEtcd || milvusRoot !== 'by-dev' || milvusChannels);
  const dirty = !configured && changed;
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await fetchSourceCdc(source.id, controller.signal);
        if (!controller.signal.aborted) { setStatus(next); setError(''); }
      } catch (failure) {
        if (!controller.signal.aborted) { setStatus(undefined); setError(errorMessage(failure)); }
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 10000);
    };
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [source.id, source.updatedAt, revision]);

  const act = async (action: 'create' | 'pause' | 'resume' | 'restart' | 'remove') => {
    if (action === 'remove' && !window.confirm('Remove this capture task? Existing Kafka data and source replication slots remain. Creating another task starts a new capture with a new topic namespace.')) return;
    setBusy(true); onBusyChange?.(true); setError('');
    try {
      if (action === 'create') {
        await createSourceCdc(source.id, {
          tables: tables.split(/[\n,]/).map(item => item.trim()).filter(Boolean), snapshot,
          ...(source.engine === 'postgresql' ? { publication } : {}),
          ...(source.engine === 'mysql' ? { serverId: Number(serverId) } : {}),
          ...(source.engine === 'oracle' ? { oracleDatabase, ...(oraclePdb ? { oraclePdb } : {}) } : {}),
          ...(source.engine === 'milvus' ? { milvus: {
            kafkaBrokers: milvusKafka, etcdEndpoints: milvusEtcd.split(',').map(value => value.trim()).filter(Boolean),
            etcdRoot: milvusRoot, pchannels: milvusChannels.split(',').map(value => value.trim()).filter(Boolean),
          } } : {}),
        }, source.updatedAt);
        setSavedDraft(draftKey);
      } else await controlSourceCdc(source.id, action, source.updatedAt);
      setStatus(undefined); setRevision(value => value + 1);
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); onBusyChange?.(false); }
  };

  return <section className="ontology-source-cdc source-capture" aria-label="Continuous CDC">
    <header className="source-capture__heading"><div><span className="source-capture__eyebrow">Database changes</span><h3>Continuous CDC</h3><p>Keep a stream of changes from selected source tables or collections.</p></div><Button size="small" disabled={busy} onClick={() => setRevision(value => value + 1)}>Refresh CDC</Button></header>
    <div className="source-capture__route" aria-label="CDC output path"><span>Source tables / collections</span><span aria-hidden="true">→</span><strong>Kafka topics</strong><span aria-hidden="true">→</span><span>Downstream consumers</span></div>
    <p className="source-capture__intro">This captures Debezium events into Kafka; it does not write tables into a destination database. Configure downstream consumption separately. Recovery can repeat events, so consumers must apply changes idempotently.</p>
    <ol className="source-capture__steps" aria-label="Continuous capture steps">
      <li><span>1</span><div><strong>Prepare the source</strong><p>Check log access, permissions and the connector plugin.</p></div></li>
      <li><span>2</span><div><strong>Choose scope and starting data</strong><p>Select tables and decide whether to include existing rows.</p></div></li>
      <li><span>3</span><div><strong>Check status and output</strong><p>Inspect tasks and verify events in the Kafka topics.</p></div></li>
    </ol>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status"><LoadingIndicator size={20}>Loading CDC status…</LoadingIndicator></p>}
    {status && <div className="source-capture__layout">
      <section className="source-capture__panel source-capture__prepare" aria-label="Capture prerequisites">
      <h4>1. Check prerequisites</h4>
      <p>{status.prerequisite || 'Ask the source administrator to prepare log-based capture permissions and source log retention.'}</p>
      <p className="source-capture__status">Connector plugin: <strong>{status.pluginAvailable ? 'Installed' : 'Missing'}</strong></p>
      {!status.pluginAvailable && <p role="alert">The required source connector is not installed on Kafka Connect. Install it before starting capture.</p>}
      </section>
      <section className="source-capture__panel source-capture__configure" aria-label="Capture scope and starting data">
      <h4>2. Choose capture scope</h4>
      {configured ? <>
        <dl className="source-capture__facts"><div><dt>Tables / collections</dt><dd>{status.selection?.tables.join(', ')}</dd></div>
          <div><dt>Initial data</dt><dd>{status.selection?.snapshot === 'initial' ? 'Existing rows, then new changes' : 'New changes only'}</dd></div>
          {status.selection?.milvus && <div><dt>Physical channels</dt><dd>{status.selection.milvus.pchannels.join(', ')}</dd></div>}</dl>
        <p>The selected scope is fixed for this capture. To change it, remove the capture and create a new one.</p>
      </> : <form onSubmit={event => { event.preventDefault(); void act('create'); }}>
        <label>Tables / collections<Input required value={tables} onChange={event => setTables(event.target.value)} placeholder={source.engine === 'mongodb' || source.engine === 'mysql' ? `${source.config.database}.example` : 'schema.table'} /></label>
        <p className="source-capture__help">Enter explicit qualified names separated by commas, for example <code>{source.engine === 'mongodb' || source.engine === 'mysql' || source.engine === 'milvus' ? `${source.config.database}.records` : 'public.records'}</code>. Letters, digits, underscores and dollar signs are supported.</p>
        {['mysql', 'mongodb', 'milvus'].includes(source.engine) && <p className="source-capture__help">This capture is limited to the saved database <code>{source.config.database}</code>. Other storage nodes discovered in Syncs are not included.</p>}
        <label>Initial data<NativeSelect value={snapshot} onChange={event => setSnapshot(event.target.value as 'initial' | 'no_data')}>
          <option value="initial">Snapshot existing data, then capture changes</option><option value="no_data">Capture new changes only</option>
        </NativeSelect></label>
        <p className="source-capture__help">{snapshot === 'initial' ? 'Use a snapshot to include rows already in the source, then keep capturing new changes.' : 'Existing rows are skipped. Use this when historical data has already been loaded and only new changes are needed.'}</p>
        {source.engine === 'postgresql' && <label>Existing publication<Input required value={publication} onChange={event => setPublication(event.target.value)} /></label>}
        {source.engine === 'postgresql' && <p className="source-capture__help">Use a publication already prepared by your database administrator for these tables.</p>}
        {source.engine === 'mysql' && <label>Unique replication server ID<Input required type="number" min={1} max={4294967295} value={serverId} onChange={event => setServerId(event.target.value)} /></label>}
        {source.engine === 'oracle' && <>
          <label>Database name (CDB / non-CDB)<Input required value={oracleDatabase} onChange={event => setOracleDatabase(event.target.value)} /></label>
          <label>PDB name (optional)<Input value={oraclePdb} onChange={event => setOraclePdb(event.target.value)} /></label>
        </>}
        {source.engine === 'milvus' && <>
          <p>Experimental: requires the Debezium Milvus connector (3.7+) on every worker. Enter all single-partition physical channels for the selected collections; one connector captures each channel. Source MQ must accept unauthenticated PLAINTEXT Kafka; etcd authentication options are not provided by this form.</p>
          <label>Milvus MQ Kafka brokers<Input required value={milvusKafka} onChange={event => setMilvusKafka(event.target.value)} placeholder="milvus-kafka:9092" /></label>
          <label>Milvus etcd endpoints<Input required value={milvusEtcd} onChange={event => setMilvusEtcd(event.target.value)} placeholder="http://etcd:2379, http://etcd2:2379" /></label>
          <label>etcd root path<Input required value={milvusRoot} onChange={event => setMilvusRoot(event.target.value)} /></label>
          <label>All physical channels<Input required value={milvusChannels} onChange={event => setMilvusChannels(event.target.value)} placeholder="by-dev-rootcoord-dml_0, by-dev-rootcoord-dml_1" /></label>
        </>}
        <Button type="primary" htmlType="submit" disabled={busy || !status.pluginAvailable}>Start continuous capture</Button>
      </form>}
      </section>
      <section className="source-capture__panel source-capture__output" aria-label="Capture status and output">
        <h4>3. Check status and output</h4>
        <p className="source-capture__status" role="status">Connector: <strong>{status.state}</strong></p>
        {!!status.tasks.length && <ul className="source-capture__tasks">{status.tasks.map(task => <li key={`${task.connector || ''}:${task.id}`}><span>{task.connector ? `${task.connector} / ` : ''}Task {task.id}</span><strong>{task.state}</strong></li>)}</ul>}
        {status.sourceChanged && <p role="alert">The saved connection differs from this capture. Remove the task and configure it again.</p>}
        {configured ? <>
          <dl className="source-capture__facts"><div><dt>Kafka topic prefix</dt><dd><code>{status.topicPrefix}</code></dd></div>
            {status.slot && <div><dt>Replication slot</dt><dd><code>{status.slot}</code></dd></div>}</dl>
          <p>Check connector and task states, then inspect events under this topic prefix. A running connector alone does not verify downstream database writes.</p>
          <div className="ontology-source-cdc__actions source-capture__controls">
            <Button disabled={busy} onClick={() => void act('pause')}>Pause capture</Button>
            <Button disabled={busy || status.sourceChanged} onClick={() => void act('resume')}>Resume capture</Button>
            <Button disabled={busy || status.sourceChanged} onClick={() => void act('restart')}>Restart failed tasks</Button>
            <Button danger disabled={busy} onClick={() => void act('remove')}>Remove CDC</Button>
          </div>
          <div className="source-capture__note"><strong>Pause or remove?</strong><p>Pause retains the source position for resuming. Ensure WAL, binlog or oplog retention covers the pause.</p><p>Remove deletes the capture task, while existing Kafka messages and PostgreSQL replication slots remain. A new capture uses a new topic namespace. An administrator must drop unused PostgreSQL slots.</p></div>
        </> : <p>No capture is configured yet. Start capture after completing steps 1 and 2; its task state and Kafka topic prefix will appear here.</p>}
      </section>
    </div>}
  </section>;
}

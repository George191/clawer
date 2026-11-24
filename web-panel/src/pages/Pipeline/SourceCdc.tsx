import React, { useEffect, useState } from 'react';
import { Button, Input, NativeSelect } from '@/components/ui';
import { controlSourceCdc, createSourceCdc, fetchSourceCdc } from '../../services/api';
import type { DataSource, SourceCdcStatus } from '../../services/api';

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
  return typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'CDC operation failed. Refresh and try again.';
}

export default function SourceCdc({ source, onBusyChange }: { source: DataSource; onBusyChange?: (busy: boolean) => void }) {
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
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);

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
      } else await controlSourceCdc(source.id, action, source.updatedAt);
      setStatus(undefined); setRevision(value => value + 1);
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); onBusyChange?.(false); }
  };

  const configured = Boolean(status?.selection);
  return <section className="ontology-source-cdc" aria-label="Continuous CDC">
    <header><h3>Continuous CDC</h3><Button size="small" disabled={busy} onClick={() => setRevision(value => value + 1)}>Refresh CDC</Button></header>
    <p>Capture changes continuously into dedicated Kafka topics using Debezium events. Delivery may repeat events after recovery; consumers must apply changes idempotently.</p>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status">Loading CDC status…</p>}
    {status && <>
      <p>{status.prerequisite}</p>
      {!status.pluginAvailable && <p role="alert">The required source connector is not installed on Kafka Connect. Install it before starting capture.</p>}
      <p role="status">Connector: <strong>{status.state}</strong>{status.tasks.map(task => ` · ${task.connector ? `${task.connector} / ` : ''}Task ${task.id}: ${task.state}`).join('')}</p>
      {status.sourceChanged && <p role="alert">The saved connection differs from this capture. Remove the task and configure it again.</p>}
      {configured ? <>
        <dl><dt>Tables / collections</dt><dd>{status.selection?.tables.join(', ')}</dd><dt>Topic prefix</dt><dd><code>{status.topicPrefix}</code></dd>
          {status.selection?.milvus && <><dt>Physical channels</dt><dd>{status.selection.milvus.pchannels.join(', ')}</dd></>}
          {status.slot && <><dt>Replication slot</dt><dd><code>{status.slot}</code></dd></>}</dl>
        <div className="ontology-source-cdc__actions">
          <Button disabled={busy} onClick={() => void act('pause')}>Pause capture</Button>
          <Button disabled={busy || status.sourceChanged} onClick={() => void act('resume')}>Resume capture</Button>
          <Button disabled={busy || status.sourceChanged} onClick={() => void act('restart')}>Restart failed tasks</Button>
          <Button danger disabled={busy} onClick={() => void act('remove')}>Remove CDC</Button>
        </div>
        <p>Pausing retains the source position. Ensure WAL, binlog or oplog retention covers the pause. Remove CDC before editing this connection. PostgreSQL slots remain after removal and must be dropped by a database administrator when no longer needed.</p>
      </> : <form onSubmit={event => { event.preventDefault(); void act('create'); }}>
        <label>Tables / collections<Input required value={tables} onChange={event => setTables(event.target.value)} placeholder={source.engine === 'mongodb' || source.engine === 'mysql' ? `${source.config.database}.example` : 'schema.table'} /></label>
        <p>Enter explicit qualified names separated by commas. Letters, digits, underscores and dollar signs are supported.</p>
        <label>Initial data<NativeSelect value={snapshot} onChange={event => setSnapshot(event.target.value as 'initial' | 'no_data')}>
          <option value="initial">Snapshot existing data, then capture changes</option><option value="no_data">Capture new changes only</option>
        </NativeSelect></label>
        {source.engine === 'postgresql' && <label>Existing publication<Input required value={publication} onChange={event => setPublication(event.target.value)} /></label>}
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
    </>}
  </section>;
}

import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchCdcStatus, type DataSource } from '../../services/api';
import type { CdcProbe, CdcStatus } from '../../services/types';

const probeLabel = (probe: CdcProbe) => ({
  available: 'Available', not_ready: 'Not ready', unavailable: 'Unavailable', unconfigured: 'Not configured',
})[probe.state];

export default function SourceRuntimeStatus({ source }: { source: DataSource }) {
  const [status, setStatus] = useState<CdcStatus>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [consumersOpen, setConsumersOpen] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await fetchCdcStatus(controller.signal);
        if (!controller.signal.aborted) { setStatus(next); setError(''); }
      } catch {
        if (!controller.signal.aborted) { setStatus(undefined); setError('Live CDC and Kafka status could not be read. Refresh to retry.'); }
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 15000);
    };
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [source.id, source.updatedAt, revision]);
  const connectors = status?.connect.connectors.filter(item => item.connectionId === source.id) ?? [];
  const target = status?.postgres.targets?.find(item => item.connectionId === source.id);
  const slots = status?.postgres.slots.filter(item => item.connectionId === source.id) ?? [];
  return <section className="ontology-source-catalog" aria-label="Live CDC and Kafka status">
    <header><div><h3>Live CDC and Kafka status</h3><p>{status ? `Checked ${new Date(status.checkedAt).toLocaleString()} · Refreshes every 15 seconds` : 'Reading runtime status…'}</p></div>
      <div className="source-runtime-actions"><Button size="small" disabled={!status} onClick={() => setConsumersOpen(true)}>Kafka consumer lag · cluster-wide</Button><Button size="small" onClick={() => setRevision(value => value + 1)}>Refresh live status</Button></div></header>
    {error && <p role="alert">{error}</p>}
    {status && <>
      <dl className="ontology-connection-capabilities__list">
        <div><dt>CDC for this connection</dt><dd>{connectors.length ? connectors.map(item => <div key={item.name}><strong>{item.state}</strong><span> · {item.name}</span><p>Tasks: {item.taskStates.join(', ') || 'No tasks reported'}</p></div>) : <><strong>{status.connect.state === 'available' ? 'No matching connector' : 'Could not confirm'}</strong><span>{status.connect.reason}</span></>}</dd></div>
        <div><dt>PostgreSQL WAL for this connection</dt><dd><strong>{target ? probeLabel(target) : 'Not verified'}</strong>
          <span>{target?.reason || (!target ? status.postgres.reason : null)}</span>
          {slots.map(slot => <div key={slot.name}><code>{slot.name}</code><p>{slot.active ? 'Active reader' : 'No active reader'}</p></div>)}
        </dd></div>
        <div><dt>Kafka cluster</dt><dd><strong>{probeLabel(status.kafka)}</strong><span>{status.kafka.reason}</span><span>Cluster-wide connectivity and consumer offsets; downstream writes are not verified here.</span></dd></div>
      </dl>
      <Modal className="flow-surface-modal source-runtime-consumers" title="Kafka consumer lag · cluster-wide" open={consumersOpen} centered width={840} onCancel={() => setConsumersOpen(false)}
        footer={<Button onClick={() => setConsumersOpen(false)}>Done</Button>}>
        <p>Cluster-wide consumer offsets · Checked {new Date(status.checkedAt).toLocaleString()}</p>
        {status.kafka.consumers.length ? <table><thead><tr><th>Consumer group</th><th>Pending messages</th><th>Partitions</th></tr></thead><tbody>{status.kafka.consumers.map(item => <tr key={item.group}><td>{item.group}</td><td>{item.lag.toLocaleString()}</td><td>{item.partitions}</td></tr>)}</tbody></table> : <p>No consumer offsets reported.</p>}
      </Modal>
    </>}
  </section>;
}

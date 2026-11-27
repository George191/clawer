import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Input, Table } from '@/components/ui';
import { ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import client, { unwrap } from '@/services/api';

type Worker = { name: string; pid: number | null; uptime: number | null; concurrency: number | null; processed: number; active: { id: string; name: string; started_at: number | null }[] | null; queues: string[] | null };

export default function Workers() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [selected, setSelected] = useState<Worker>();
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setSelected(undefined);
    client.get('/automation/workers', { signal: controller.signal, timeout: 15000 }).then(r => setWorkers(unwrap<{ items: Worker[] }>(r.data).items))
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Unable to inspect workers.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [version]);
  const visible = workers.filter(worker => `${worker.name} ${worker.queues?.join(' ')}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <section className="windmill-workers"><div className="automation-toolbar"><Input prefix={<SearchOutlined />} aria-label="Search workers" placeholder="Search workers and queues…" value={query} onChange={event => setQuery(event.target.value)} /><Button icon={<ReloadOutlined />} disabled={loading} onClick={() => setVersion(value => value + 1)}>Refresh workers</Button></div>
    <p>Live executor inspection. Administrator access required. Missing task or queue replies are shown as unknown.</p>
    {error && <Alert type="error" message={error} />}
    <Table rowKey="name" loading={loading} dataSource={error ? [] : visible} scroll={{ x: 800 }} pagination={{ pageSize: 20, showSizeChanger: false }} locale={{ emptyText: <Empty description={error ? 'Worker inspection unavailable' : 'No workers match your search'} /> }} columns={[
      { title: 'Worker', render: (_, worker) => <button className="automation-record-link" onClick={() => setSelected(worker)}>{worker.name}</button> },
      { title: 'Queues', render: (_, worker) => worker.queues?.join(', ') ?? 'Unknown' },
      { title: 'Active jobs', render: (_, worker) => worker.active?.length ?? 'Unknown' },
      { title: 'Concurrency', dataIndex: 'concurrency', render: value => value ?? 'Unknown' },
      { title: 'Processed', dataIndex: 'processed' },
      { title: 'Uptime', render: (_, worker) => worker.uptime == null ? 'Unknown' : `${Math.floor(worker.uptime / 3600)}h ${Math.floor(worker.uptime % 3600 / 60)}m` },
    ]} />
    {selected && <div className="windmill-worker-detail"><header><h2>{selected.name}</h2><Button onClick={() => setSelected(undefined)}>Close details</Button></header><p>PID {selected.pid ?? 'Unknown'} · Queues: {selected.queues?.join(', ') ?? 'Unknown'}</p><h3>Active jobs</h3>{selected.active === null ? <p>Active jobs inspection unavailable for this worker.</p> : selected.active.length ? selected.active.map(task => <div key={task.id}><strong>{task.name}</strong><code>{task.id}</code><span>{task.started_at ? new Date(task.started_at * 1000).toLocaleString() : 'Start time unknown'}</span></div>) : <p>No active jobs reported.</p>}</div>}
  </section>;
}

import React, { useEffect, useState } from 'react';
import { Button, Checkbox, Input } from '@/components/ui';
import { createSourceObjectEvents, fetchSourceObjectEvents, removeSourceObjectEvents } from '../../services/api';
import type { DataSource, SourceObjectEventPayload, SourceObjectEventStatus } from '../../services/api';

export default function SourceObjectEvents({ source }: { source: DataSource }) {
  const [status, setStatus] = useState<SourceObjectEventStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [targetArn, setTargetArn] = useState('');
  const [prefix, setPrefix] = useState('');
  const [suffix, setSuffix] = useState('');
  const [events, setEvents] = useState<SourceObjectEventPayload['events']>(['s3:ObjectCreated:*', 's3:ObjectRemoved:*']);

  useEffect(() => {
    const controller = new AbortController();
    setStatus(undefined); setError('');
    fetchSourceObjectEvents(source.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setStatus(value);
    }).catch(() => { if (!controller.signal.aborted) setError('Cannot read bucket notifications. Verify MinIO connectivity and notification read permissions.'); });
    return () => controller.abort();
  }, [source.id, source.updatedAt, reload]);

  const save = async (remove: boolean) => {
    if (!status) return;
    if (remove && !window.confirm('Remove this object event subscription? Events during the disabled period cannot be replayed by this subscription. Existing Kafka messages remain.')) return;
    setBusy(true); setError('');
    try {
      if (remove) await removeSourceObjectEvents(source.id, source.updatedAt, status.configurationHash);
      else await createSourceObjectEvents(source.id, { expectedUpdatedAt: source.updatedAt, expectedConfigurationHash: status.configurationHash, targetArn, prefix, suffix, events });
      setReload(value => value + 1);
    } catch (failure) {
      const detail = (failure as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
      setError(typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'Notification configuration failed. Refresh before retrying.');
    } finally { setBusy(false); }
  };

  return <section className="ontology-source-cdc" aria-label="Object event notifications">
    <header><h3>Object event notifications · MinIO</h3><Button size="small" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh notifications</Button></header>
    <p>Object create and delete notifications are not row-level CDC. No initial snapshot, before/after row images or replay position is provided. Notifications can be duplicated or arrive out of order.</p>
    <p>A MinIO administrator must first configure a Kafka notification target, including its topic, security and persistent delivery queue. This form subscribes the saved bucket to that existing target; it does not install a server target.</p>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status">Loading notification configuration…</p>}
    {status && <>
      <p role="status">Subscription: <strong>{status.state}</strong> · End-to-end delivery is not verified.</p>
      {status.notification ? <>
        <dl><dt>Destination</dt><dd><code>{status.notification.QueueArn}</code></dd><dt>Events</dt><dd>{status.notification.Events.join(', ')}</dd>
          {status.notification.Filter?.Key.FilterRules.map(rule => <React.Fragment key={rule.Name}><dt>{rule.Name}</dt><dd>{rule.Value}</dd></React.Fragment>)}</dl>
        <Button danger disabled={busy} onClick={() => void save(true)}>Remove subscription</Button>
      </> : <form onSubmit={event => { event.preventDefault(); void save(false); }}>
        <label>Existing MinIO Kafka target ARN<Input required value={targetArn} onChange={event => setTargetArn(event.target.value)} placeholder="arn:minio:sqs::target-id:kafka" /></label>
        <label>Object key prefix (optional)<Input value={prefix} maxLength={1024} onChange={event => setPrefix(event.target.value)} /></label>
        <label>Object key suffix (optional)<Input value={suffix} maxLength={1024} onChange={event => setSuffix(event.target.value)} /></label>
        {(['s3:ObjectCreated:*', 's3:ObjectRemoved:*'] as const).map(value => <Checkbox key={value} checked={events.includes(value)} onChange={event => setEvents(current => event.target.checked ? [...current, value] : current.filter(item => item !== value))}>{value === 's3:ObjectCreated:*' ? 'Object created or overwritten' : 'Object removed'}</Checkbox>)}
        <Button type="primary" htmlType="submit" disabled={busy || !events.length}>Configure object notifications</Button>
      </form>}
    </>}
  </section>;
}

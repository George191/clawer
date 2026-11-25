import React, { useEffect, useState } from 'react';
import { Button, Checkbox, Input } from '@/components/ui';
import { createSourceObjectEvents, fetchSourceObjectEvents, removeSourceObjectEvents } from '../../services/api';
import type { DataSource, SourceObjectEventPayload, SourceObjectEventStatus } from '../../services/api';
import './source-capture.css';

export default function SourceObjectEvents({ source, onBusyChange, onDirtyChange }: { source: DataSource; onBusyChange?: (busy: boolean) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [status, setStatus] = useState<SourceObjectEventStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [targetArn, setTargetArn] = useState('');
  const [prefix, setPrefix] = useState('');
  const [suffix, setSuffix] = useState('');
  const [events, setEvents] = useState<SourceObjectEventPayload['events']>(['s3:ObjectCreated:*', 's3:ObjectRemoved:*']);
  const [savedDraft, setSavedDraft] = useState(JSON.stringify(['', '', '', ['s3:ObjectCreated:*', 's3:ObjectRemoved:*']]));
  const draftKey = JSON.stringify([targetArn, prefix, suffix, [...events].sort()]);
  const dirty = !status?.notification && draftKey !== savedDraft;
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

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
    setBusy(true); onBusyChange?.(true); setError('');
    try {
      if (remove) await removeSourceObjectEvents(source.id, source.updatedAt, status.configurationHash);
      else {
        await createSourceObjectEvents(source.id, { expectedUpdatedAt: source.updatedAt, expectedConfigurationHash: status.configurationHash, targetArn, prefix, suffix, events });
        setSavedDraft(draftKey);
      }
      setReload(value => value + 1);
    } catch (failure) {
      const detail = (failure as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
      setError(typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : 'Notification configuration failed. Refresh before retrying.');
    } finally { setBusy(false); onBusyChange?.(false); }
  };

  return <section className="ontology-source-cdc source-capture" aria-label="Object event notifications">
    <header className="source-capture__heading"><div><span className="source-capture__eyebrow">Bucket notifications</span><h3>Object event notifications · MinIO</h3><p>Notify an existing Kafka target when matching objects are created or removed.</p></div><Button size="small" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh notifications</Button></header>
    <div className="source-capture__route" aria-label="Object event output path"><span>Saved bucket</span><span aria-hidden="true">→</span><span>Existing MinIO target</span><span aria-hidden="true">→</span><strong>Kafka notifications</strong></div>
    <p className="source-capture__intro">Object notifications are not row-level CDC and do not copy object contents into a destination database. No initial snapshot, before/after row images or replay position is provided. Notifications can be duplicated or arrive out of order.</p>
    <ol className="source-capture__steps" aria-label="Object notification steps">
      <li><span>1</span><div><strong>Prepare a MinIO target</strong><p>An administrator configures Kafka and its delivery queue.</p></div></li>
      <li><span>2</span><div><strong>Select object events</strong><p>Choose the target, object key filters and event types.</p></div></li>
      <li><span>3</span><div><strong>Check notification delivery</strong><p>Perform a matching object change and inspect Kafka.</p></div></li>
    </ol>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p role="status">Loading notification configuration…</p>}
    {status && <div className="source-capture__layout">
      <section className="source-capture__panel source-capture__prepare" aria-label="Object notification prerequisites">
        <h4>1. Prepare the notification target</h4>
        <p>A MinIO administrator must first configure a Kafka notification target, including its topic, security and persistent delivery queue.</p>
        <p>This form subscribes the saved bucket to that existing target; it does not install a server target. Existing objects are not sent when the subscription is created.</p>
      </section>
      <section className="source-capture__panel source-capture__configure" aria-label="Object event scope">
      <h4>2. Select events and object keys</h4>
      {status.notification ? <>
        <dl className="source-capture__facts"><div><dt>MinIO target ARN</dt><dd><code>{status.notification.QueueArn}</code></dd></div><div><dt>Events</dt><dd>{status.notification.Events.join(', ')}</dd></div>
          {status.notification.Filter?.Key.FilterRules.map(rule => <div key={rule.Name}><dt>Object key {rule.Name}</dt><dd>{rule.Value}</dd></div>)}</dl>
        {!status.notification.Filter?.Key.FilterRules.length && <p>Object scope: all keys in the saved bucket.</p>}
        <p>To change the target, filters or event types, remove this subscription and configure it again.</p>
      </> : <form onSubmit={event => { event.preventDefault(); void save(false); }}>
        <label>Existing MinIO Kafka target ARN<Input required value={targetArn} onChange={event => setTargetArn(event.target.value)} placeholder="arn:minio:sqs::target-id:kafka" /></label>
        <p className="source-capture__help">Copy the ARN of the Kafka target configured by your MinIO administrator.</p>
        <label>Object key prefix (optional)<Input value={prefix} maxLength={1024} onChange={event => setPrefix(event.target.value)} /></label>
        <label>Object key suffix (optional)<Input value={suffix} maxLength={1024} onChange={event => setSuffix(event.target.value)} /></label>
        <p className="source-capture__help">For example, prefix <code>incoming/</code> and suffix <code>.json</code> match JSON objects in that path. Leave both empty for all object keys.</p>
        {(['s3:ObjectCreated:*', 's3:ObjectRemoved:*'] as const).map(value => <Checkbox key={value} checked={events.includes(value)} onChange={event => setEvents(current => event.target.checked ? [...current, value] : current.filter(item => item !== value))}>{value === 's3:ObjectCreated:*' ? 'Object created or overwritten' : 'Object removed'}</Checkbox>)}
        <Button type="primary" htmlType="submit" disabled={busy || !events.length}>Configure object notifications</Button>
      </form>}
      </section>
      <section className="source-capture__panel source-capture__output" aria-label="Object notification status">
        <h4>3. Verify delivery separately</h4>
        <p className="source-capture__status" role="status">Subscription: <strong>{status.state}</strong> · End-to-end delivery is not verified.</p>
        <p>After configuring the subscription, create or remove a test object that matches the selected filters, then inspect the configured Kafka target. This page does not perform that test.</p>
        {status.notification && <Button danger disabled={busy} onClick={() => void save(true)}>Remove subscription</Button>}
        <div className="source-capture__note"><strong>Removal has no replay position</strong><p>Removing the subscription stops these notifications. Events during the disabled period cannot be replayed by this subscription; existing Kafka messages remain.</p></div>
      </section>
    </div>}
  </section>;
}

import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Table, Tag, Typography } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchCdcStatus, fetchCurrentUserContext } from '../../services/api';
import type { CdcProbe, CdcStatus } from '../../services/types';
import { cdcNeedsAttention, formatWalBytes } from './cdcStatus';
import './cdc-status.css';

const probeLabels: Record<CdcProbe['state'], { text: string; color: string }> = {
  available: { text: 'Available', color: 'success' },
  not_ready: { text: 'Not ready', color: 'warning' },
  unavailable: { text: 'Unavailable', color: 'error' },
  unconfigured: { text: 'Not configured', color: 'default' },
};

function Probe({ title, probe }: { title: string; probe: CdcProbe }) {
  const label = probeLabels[probe.state];
  return <div className="cdc-probe"><strong>{title}</strong><Tag color={label.color}>{label.text}</Tag>
    {probe.reason && <span>{probe.reason}</span>}</div>;
}

function CdcDetails({ status }: { status: CdcStatus }) {
  return <>
    <Alert showIcon type={cdcNeedsAttention(status) ? 'warning' : 'info'} message={status.outputMode === 'direct'
      ? `ETL is using direct-output fallback. Database change events do not control inter-layer output.${cdcNeedsAttention(status) ? ' Mongo CDC or consumer offsets are not ready.' : ''}`
      : cdcNeedsAttention(status) ? 'CDC output is selected. Some runtime requirements have not been verified.'
        : 'CDC infrastructure has been inspected. Connector health and committed offsets do not confirm downstream persistence.'} />
    <div className="cdc-probes">
      <Probe title="Kafka Connect" probe={status.connect} />
      <Probe title="Mongo Change Streams" probe={status.mongo} />
      <Probe title="PostgreSQL WAL" probe={status.postgres} />
      <Probe title="CDC consumer offsets" probe={status.kafka} />
    </div>
    <Table size="small" rowKey="name" pagination={false} dataSource={status.connect.connectors}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No connectors available for inspection" /> }}
      columns={[
        { title: 'Connector', dataIndex: 'name', render: (name: string) => <Typography.Text code>{name}</Typography.Text> },
        { title: 'Connector status', dataIndex: 'state', render: (state: string) => <Tag color={state === 'RUNNING' ? 'success' : state === 'FAILED' ? 'error' : 'warning'}>{state}</Tag> },
        { title: 'Task status', dataIndex: 'taskStates', render: (states: string[]) => states.length ? states.join(' / ') : 'No tasks assigned' },
      ]} />
    {!!status.kafka.consumers.length && <Table size="small" rowKey="group" pagination={false}
      dataSource={status.kafka.consumers} columns={[
        { title: 'CDC consumer group', dataIndex: 'group', ellipsis: true },
        { title: 'Partitions', dataIndex: 'partitions', width: 90 },
        { title: 'Committed offset lag (messages)', dataIndex: 'lag', width: 180, render: (lag: number) => lag.toLocaleString('en-US') },
      ]} />}
    {!!status.postgres.slots.length && <Table size="small" rowKey="name" pagination={false}
      dataSource={status.postgres.slots} columns={[
        { title: 'WAL replication slot', dataIndex: 'name', render: (name: string) => <>{name}{name === status.postgres.expectedSlot && <Tag>This project</Tag>}</> },
        { title: 'Reader connection', dataIndex: 'active', render: (active: boolean) => active ? 'Active' : 'Disconnected' },
        { title: 'Retained WAL', dataIndex: 'retainedBytes', render: formatWalBytes },
        { title: 'Unconfirmed WAL', dataIndex: 'unconfirmedBytes', render: formatWalBytes },
      ]} />}
    <Typography.Paragraph type="secondary" className="cdc-explanation">
      Workers rescan pending Mongo download and synchronization records. Postgres inter-layer output resumes from WAL and Kafka offsets.
      This view does not change offsets or trigger recovery. Verify target tables and pending records after a restart.
    </Typography.Paragraph>
  </>;
}

export default function CdcStatusPanel() {
  const [allowed, setAllowed] = useState(false);
  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState<CdcStatus>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    fetchCurrentUserContext().then((context) => { if (active) setAllowed(!!context.user.is_superuser); })
      .catch(() => { if (active) setAllowed(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!allowed) return;
    const request = new AbortController();
    setLoading(true);
    setError('');
    fetchCdcStatus(request.signal).then((next) => {
      if (!request.signal.aborted) setStatus(next);
    }).catch(() => {
      if (!request.signal.aborted) setError('Unable to load CDC status. Try again. Any previous snapshot may be out of date.');
    }).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [allowed, version]);

  if (!allowed) return null;
  return <Card className="cdc-status-panel" size="small" title="CDC runtime status"
    extra={<Space><Tag>{status ? status.outputMode === 'direct' ? 'Direct-output fallback' : 'Debezium' : 'Not loaded'}</Tag>
      <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => setVersion((value) => value + 1)}>Refresh</Button></Space>}>
    <div aria-busy={loading} aria-live="polite">
      {error && <Alert type="error" showIcon message={error} />}
      {!status && loading && <Spin size="small" tip="Loading CDC status" />}
      {status && <><Typography.Paragraph type="secondary">Last checked: {new Date(status.checkedAt).toLocaleString('en-US')}</Typography.Paragraph>
        <CdcDetails status={status} /></>}
    </div>
  </Card>;
}

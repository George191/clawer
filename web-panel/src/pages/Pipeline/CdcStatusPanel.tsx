import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Space, Spin, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { fetchCdcStatus, fetchCurrentUserContext } from '../../services/api';
import type { CdcProbe, CdcStatus } from '../../services/types';
import { cdcNeedsAttention, formatWalBytes } from './cdcStatus';
import './cdc-status.css';

const probeLabels: Record<CdcProbe['state'], { text: string; color: string }> = {
  available: { text: '可读取', color: 'success' },
  not_ready: { text: '尚未就绪', color: 'warning' },
  unavailable: { text: '无法读取', color: 'error' },
  unconfigured: { text: '未配置', color: 'default' },
};

function Probe({ title, probe }: { title: string; probe: CdcProbe }) {
  const label = probeLabels[probe.state];
  return <div className="cdc-probe"><strong>{title}</strong><Tag color={label.color}>{label.text}</Tag>
    {probe.reason && <span>{probe.reason}</span>}</div>;
}

function CdcDetails({ status }: { status: CdcStatus }) {
  return <>
    <Alert showIcon type={cdcNeedsAttention(status) ? 'warning' : 'info'} message={status.outputMode === 'direct'
      ? `ETL 当前使用直发回滚模式，数据库变更未接管层间输出。${cdcNeedsAttention(status) ? 'Mongo CDC 或消费位点尚未就绪。' : ''}`
      : cdcNeedsAttention(status) ? 'ETL 已选择 CDC 输出，运行条件尚未全部验证通过。'
        : '已读取 CDC 基础设施状态；连接器运行和 Kafka 位点追平不代表下游数据已落库。'} />
    <div className="cdc-probes">
      <Probe title="Kafka Connect" probe={status.connect} />
      <Probe title="Mongo Change Streams" probe={status.mongo} />
      <Probe title="PostgreSQL WAL" probe={status.postgres} />
      <Probe title="CDC 消费位点" probe={status.kafka} />
    </div>
    <Table size="small" rowKey="name" pagination={false} dataSource={status.connect.connectors}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可验证的连接器" /> }}
      columns={[
        { title: '连接器', dataIndex: 'name', render: (name: string) => <Typography.Text code>{name}</Typography.Text> },
        { title: '连接器状态', dataIndex: 'state', render: (state: string) => <Tag color={state === 'RUNNING' ? 'success' : state === 'FAILED' ? 'error' : 'warning'}>{state}</Tag> },
        { title: '任务状态', dataIndex: 'taskStates', render: (states: string[]) => states.length ? states.join(' / ') : '尚未分配任务' },
      ]} />
    {!!status.kafka.consumers.length && <Table size="small" rowKey="group" pagination={false}
      dataSource={status.kafka.consumers} columns={[
        { title: 'CDC 消费组', dataIndex: 'group', ellipsis: true },
        { title: '分区数', dataIndex: 'partitions', width: 90 },
        { title: '已提交位点差（消息）', dataIndex: 'lag', width: 180, render: (lag: number) => lag.toLocaleString() },
      ]} />}
    {!!status.postgres.slots.length && <Table size="small" rowKey="name" pagination={false}
      dataSource={status.postgres.slots} columns={[
        { title: 'WAL 复制槽', dataIndex: 'name', render: (name: string) => <>{name}{name === status.postgres.expectedSlot && <Tag>本项目</Tag>}</> },
        { title: '读取连接', dataIndex: 'active', render: (active: boolean) => active ? '活跃' : '未连接' },
        { title: '保留 WAL', dataIndex: 'retainedBytes', render: formatWalBytes },
        { title: '待确认 WAL', dataIndex: 'unconfirmedBytes', render: formatWalBytes },
      ]} />}
    <Typography.Paragraph type="secondary" className="cdc-explanation">
      Mongo 下载与同步的待处理记录由各 Worker 扫描补偿；Postgres 层间输出依赖 WAL/Kafka 位点恢复。
      页面不会修改消费位点或启动补偿。停机恢复后需核对目标表和待处理记录。
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
      if (!request.signal.aborted) setError('CDC 状态读取失败，请稍后重试。下方历史快照不代表当前状态。');
    }).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [allowed, version]);

  if (!allowed) return null;
  return <Card className="cdc-status-panel" size="small" title="CDC 运行状态"
    extra={<Space><Tag>{status ? status.outputMode === 'direct' ? '直发回滚' : 'Debezium' : '尚未读取'}</Tag>
      <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => setVersion((value) => value + 1)}>刷新</Button></Space>}>
    <div aria-busy={loading} aria-live="polite">
      {error && <Alert type="error" showIcon message={error} />}
      {!status && loading && <Spin size="small" tip="读取 CDC 状态" />}
      {status && <><Typography.Paragraph type="secondary">检查时间：{new Date(status.checkedAt).toLocaleString()}</Typography.Paragraph>
        <CdcDetails status={status} /></>}
    </div>
  </Card>;
}

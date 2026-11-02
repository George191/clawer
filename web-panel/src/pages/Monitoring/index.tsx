import React, { useCallback, useState } from 'react';
import { Alert as AlertBanner, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { usePolling } from '@/hooks/usePolling';
import { fetchDashboardAlerts, fetchDashboardMetrics } from '@/services/api';
import type { Alert, DashboardMetrics } from '@/services/types';
import CdcStatusPanel from '../Pipeline/CdcStatusPanel';
import StatsPanel from './StatsPanel';

export default function Monitoring() {
  const [metrics, setMetrics] = useState<DashboardMetrics>();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string>();
  const refresh = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([fetchDashboardMetrics(), fetchDashboardAlerts()]);
    if (results[0].status === 'fulfilled') setMetrics(results[0].value);
    else setMetrics(undefined);
    if (results[1].status === 'fulfilled') setAlerts(results[1].value);
    else setAlerts([]);
    setError(results.some((result) => result.status === 'rejected') ? '部分监控数据读取失败，缺失指标不能视为正常或零。' : '');
    setCheckedAt(new Date().toLocaleString());
    setLoading(false);
  }, []);
  usePolling(refresh, 10000);

  const counts = [
    { key: 'total', title: '采集任务', value: metrics?.tasks.total },
    { key: 'running', title: '运行中的采集任务', value: metrics?.tasks.running },
    { key: 'completed', title: '采集完成的任务', value: metrics?.tasks.completed },
    { key: 'failed', title: '采集失败的任务', value: metrics?.tasks.failed },
  ];
  return <main style={{ padding: 24 }}>
    <Space wrap style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
      <div><Typography.Title level={2}>运行监控</Typography.Title>
        <Typography.Text type="secondary">任务状态来自实际采集记录；采集完成、Kafka ACK 和 ETL 落库分别确认。</Typography.Text></div>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新任务与告警</Button>
    </Space>
    {error && <AlertBanner type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
    {checkedAt && <Typography.Paragraph type="secondary">任务检查时间：{checkedAt}</Typography.Paragraph>}
    <Row gutter={[16, 16]}>{counts.map((item) => <Col xs={24} sm={12} xl={6} key={item.key}>
      <Card size="small"><Statistic title={item.title} value={item.value ?? '—'} /></Card>
    </Col>)}</Row>
    <section aria-label="请求与资源指标" style={{ marginTop: 20 }}><StatsPanel /></section>
    <CdcStatusPanel />
    <Card title="实际任务告警" size="small" style={{ marginTop: 20 }}>
      <Table<Alert> rowKey="id" pagination={{ pageSize: 10 }} dataSource={alerts} loading={loading}
        locale={{ emptyText: error ? '告警数据未完整读取' : '当前任务快照没有失败告警；未采集的基础设施指标仍为未知' }}
        columns={[
          { title: '来源', dataIndex: 'source', width: 180 },
          { title: '级别', dataIndex: 'level', width: 110, render: (level: string) => <Tag color={level === 'critical' ? 'error' : 'warning'}>{level}</Tag> },
          { title: '告警', dataIndex: 'message' },
          { title: '更新时间', dataIndex: 'time', width: 200, render: (time: string) => time ? new Date(time).toLocaleString() : '—' },
        ]} />
      <Space><Button href="/logs">查看日志</Button><Button href="/automation/runs">查看任务</Button></Space>
    </Card>
  </main>;
}

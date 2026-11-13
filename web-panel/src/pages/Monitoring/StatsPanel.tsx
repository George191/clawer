import React, { useCallback, useState } from 'react';
import { Alert, Button, Card, Col, Row, Skeleton, Statistic } from '@/components/ui';
import { usePolling } from '@/hooks/usePolling';
import { fetchMonitorStats } from '@/services/api';
import type { MonitorStats } from '@/services/types';

export default function StatsPanel() {
  const [stats, setStats] = useState<MonitorStats>();
  const [error, setError] = useState('');
  const loadStats = useCallback(async () => {
    try {
      setStats(await fetchMonitorStats());
      setError('');
    } catch {
      setStats(undefined);
      setError('请求、反爬和代理指标暂时无法读取。');
    }
  }, []);
  usePolling(loadStats, 10000);

  if (error) return <Alert type="error" showIcon message={error} action={<Button onClick={loadStats}>重试</Button>} />;
  if (!stats) return <Skeleton active />;
  const values = [
    { key: 'rate', label: '请求速率', value: stats.reqRate, suffix: 'req/s' },
    { key: 'success', label: '请求成功率', value: stats.successRate, suffix: '%' },
    { key: 'triggers', label: '反爬触发', value: stats.antiCrawlTriggers, suffix: '次/h' },
    { key: 'proxy', label: '可用代理', value: stats.proxyAvailable, suffix: stats.proxyTotal === null ? '' : `/ ${stats.proxyTotal}` },
  ];
  return <>
    {stats.reason && <Alert type="info" showIcon message={stats.reason} style={{ marginBottom: 16 }} />}
    <Row gutter={[16, 16]}>{values.map((item) => <Col xs={24} sm={12} xl={6} key={item.key}>
      <Card size="small"><Statistic title={item.label} value={item.value === null ? '—' : item.value} suffix={item.suffix} /></Card>
    </Col>)}</Row>
  </>;
}

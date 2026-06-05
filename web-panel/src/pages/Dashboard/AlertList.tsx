import React, { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Badge, Card, theme, Typography, Spin, Empty, Button, Result } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';
import { useDashboardStore } from '@/stores/dashboard';
import type { Alert } from '@/services/types';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

// ── Level config ──
const levelConfig: Record<string, { color: string; label: string }> = {
  critical: { color: 'red', label: 'P0 严重' },
  warning: { color: 'orange', label: 'P1 警告' },
  info: { color: 'blue', label: 'P3 信息' },
};

const levelOrder: Record<string, number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

const AlertList: React.FC = () => {
  const { alerts, fetchAlerts, error } = useDashboardStore();
  const { token } = theme.useToken();
  const [expandedRowKeys, setExpandedRowKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAlerts().finally(() => setLoading(false));
  }, [fetchAlerts]);

  const formatRelativeTime = useCallback((time: string) => {
    const d = dayjs(time);
    if (!d.isValid()) return time;
    const hoursAgo = dayjs().diff(d, 'hour');
    if (hoursAgo < 24) return d.fromNow();
    if (hoursAgo < 48) return '昨天 ' + d.format('HH:mm');
    return d.format('MM-DD HH:mm');
  }, []);

  const columns: ColumnsType<Alert> = [
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 100,
      render: (level: string) => {
        const cfg = levelConfig[level] || { color: 'default', label: level };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
      sorter: (a, b) => (levelOrder[a.level] ?? 9) - (levelOrder[b.level] ?? 9),
      defaultSortOrder: 'ascend',
      filters: [
        { text: 'P0 严重', value: 'critical' },
        { text: 'P1 警告', value: 'warning' },
        { text: 'P3 信息', value: 'info' },
      ],
      onFilter: (value, record) => record.level === value,
    },
    {
      title: '时间',
      dataIndex: 'time',
      key: 'time',
      width: 140,
      render: (time: string) => (
        <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>
          {formatRelativeTime(time)}
        </span>
      ),
      sorter: (a, b) => new Date(a.time).getTime() - new Date(b.time).getTime(),
      defaultSortOrder: 'descend',
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 150,
      render: (src: string) => (
        <span style={{ fontSize: 12 }}>{src}</span>
      ),
    },
    {
      title: '告警内容',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => {
        const isActive = status === 'active';
        return (
          <Badge
            status={isActive ? 'error' : 'default'}
            text={
              <span style={{ fontSize: 12, color: isActive ? token.colorError : token.colorTextQuaternary }}>
                {isActive ? '活跃' : '已解决'}
              </span>
            }
          />
        );
      },
      filters: [
        { text: '活跃', value: 'active' },
        { text: '已解决', value: 'resolved' },
      ],
      onFilter: (value, record) => record.status === value,
    },
  ];

  // ── Error state ──
  if (error && alerts.length === 0) {
    return (
      <Card size="small" styles={{ body: { padding: '16px' } }}>
        <Result
          status="error"
          title="告警加载失败"
          subTitle={error}
          extra={
            <Button onClick={fetchAlerts}>重试</Button>
          }
        />
      </Card>
    );
  }

  // ── Empty state ──
  if (!loading && alerts.length === 0) {
    return (
      <Card size="small" styles={{ body: { padding: '48px 0' } }}>
        <Empty description="暂无告警 ✨ 系统运行正常" />
      </Card>
    );
  }

  const activeCount = alerts.filter((a) => a.status === 'active').length;

  return (
    <Card
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Typography.Text strong style={{ fontSize: 14 }}>最近告警</Typography.Text>
          <Badge
            count={activeCount}
            overflowCount={99}
            style={{ backgroundColor: activeCount > 0 ? token.colorError : token.colorTextQuaternary }}
          />
          {loading && <Spin size="small" style={{ marginLeft: 8 }} />}
        </div>
      }
      size="small"
      styles={{ body: { padding: '4px 0 0' } }}
    >
      <Table
        dataSource={alerts}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{ pageSize: 8, size: 'small', showSizeChanger: false }}
        expandable={{
          expandedRowKeys,
          onExpandedRowsChange: (keys) => setExpandedRowKeys(keys as string[]),
          expandedRowRender: (record) => (
            <div
              style={{
                padding: '12px 16px',
                background: token.colorFillQuaternary,
                borderRadius: token.borderRadiusSM,
                margin: '4px 0',
              }}
            >
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>告警 ID: </span>
                <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{record.id}</span>
              </div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>完整时间: </span>
                <span style={{ fontSize: 12 }}>{record.time}</span>
              </div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>来源系统: </span>
                <Tag style={{ fontSize: 11 }}>{record.source}</Tag>
              </div>
              <div>
                <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>告警策略: </span>
                <span style={{ fontSize: 12 }}>
                  自动触发 · 阈值监测 · 已通知相关负责人
                </span>
              </div>
            </div>
          ),
          rowExpandable: () => true,
        }}
        locale={{ emptyText: '暂无告警' }}
      />
    </Card>
  );
};

export default AlertList;
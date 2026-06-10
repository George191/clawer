import React, { useState } from 'react';
import {
  Card, Progress, Button, Space, Modal, Typography, Popconfirm, Tooltip, theme, Row, Col,
} from 'antd';
import {
  PauseCircleOutlined,
  FileTextOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  CloseOutlined,
  ClockCircleOutlined,
  GlobalOutlined,
  ApiOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
  MessageOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import StatusBadge from '@/components/StatusBadge';
import { semanticHex } from '@/theme/tokens';
import type { TaskInfo } from '@/services/types';

const { Text } = Typography;

interface TaskCardProps {
  task: TaskInfo;
  compact?: boolean;
  onDelete: (id: string) => void;
  onRetry: (id: string) => void;
}

const TEMPLATE_ICON: Record<string, React.ReactNode> = {
  '网页采集': <GlobalOutlined />,
  'API采集': <ApiOutlined />,
  '日志采集': <FileTextOutlined />,
  '数据清洗': <FileSearchOutlined />,
  '质量校验': <SafetyCertificateOutlined />,
  '消息采集': <MessageOutlined />,
};

const TEMPLATE_COLOR: Record<string, string> = {
  '网页采集': 'var(--theme-color-primary-text)',
  'API采集': 'var(--theme-color-success-text)',
  '日志采集': 'var(--theme-color-warning-text)',
  '数据清洗': 'var(--theme-color-discovery-text)',
  '质量校验': 'var(--theme-color-discovery-text)',
  '消息采集': 'var(--theme-color-danger-text)',
};

const STATUS_COLOR: Record<string, string> = {
  running: 'var(--theme-color-primary-text)',
  completed: 'var(--theme-color-success-text)',
  failed: 'var(--theme-color-danger-text)',
  paused: 'var(--theme-color-warning-text)',
  queued: 'var(--theme-color-neutral-text-weakest)',
};

// Template type → hex for gradient background (CSS var doesn't support alpha composition)
const TEMPLATE_HEX: Record<string, string> = {
  '网页采集': semanticHex.primary,
  'API采集': semanticHex.success,
  '日志采集': semanticHex.warning,
  '数据清洗': semanticHex.discovery,
  '质量校验': semanticHex.discovery,
  '消息采集': semanticHex.danger,
};

const TaskCard: React.FC<TaskCardProps> = ({ task, compact, onDelete, onRetry }) => {
  const [logVisible, setLogVisible] = useState(false);
  const { token } = theme.useToken();

  const statusColor = STATUS_COLOR[task.status] || 'var(--theme-color-neutral-text-weakest)';
  const iconColor = TEMPLATE_COLOR[task.template] || 'var(--theme-color-primary-text)';
  const iconHex = TEMPLATE_HEX[task.template] || semanticHex.primary;
  const templateIcon = TEMPLATE_ICON[task.template] || <BarChartOutlined />;

  const progressStatus =
    task.status === 'failed' ? 'exception' :
    task.status === 'completed' ? 'success' :
    task.status === 'running' ? 'active' : 'normal';

  const formatRecords = (n: number): string => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
  };

  const logModal = (
    <Modal
      title={`任务日志 - ${task.template}`}
      open={logVisible}
      onCancel={() => setLogVisible(false)}
      footer={[
        <Button key="close" icon={<CloseOutlined />} onClick={() => setLogVisible(false)}>关闭</Button>,
      ]}
      width={720}
    >
      <div
        style={{
          background: 'var(--theme-color-neutral-bg-default)', color: 'var(--theme-color-neutral-text-default)', padding: 16, borderRadius: 6,
          fontSize: 12, fontFamily: 'var(--theme-font-code)',
          maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: 1.7,
        }}
      >
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:00] [INFO] 任务启动: {task.template}</div>
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:01] [INFO] 连接数据源...</div>
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:03] [INFO] 数据源连接成功</div>
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:05] [INFO] 开始处理记录...</div>
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:15] [INFO] 处理完成, 共 {formatRecords(task.records)} 条</div>
        <div style={{ color: '#58a6ff' }}>[2026-06-01 23:00:18] [INFO] 写入目标完成</div>
      </div>
    </Modal>
  );

  if (compact) {
    return (
      <>
        <Card
          size="small"
          style={{
            borderLeft: `3px solid ${statusColor}`,
            transition: 'box-shadow 0.3s, transform 0.2s',
          }}
          bodyStyle={{ padding: '12px 16px' }}
          onMouseEnter={(e) => { e.currentTarget.style.boxShadow = token.boxShadowTertiary; e.currentTarget.style.transform = 'translateY(-1px)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.boxShadow = ''; e.currentTarget.style.transform = ''; }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 36, height: 36, borderRadius: '50%', background: `${iconHex}15`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: iconColor, fontSize: 18, flexShrink: 0,
              }}
            >
              {templateIcon}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text strong ellipsis style={{ maxWidth: 200 }}>{task.template}</Text>
                <StatusBadge status={task.status} />
              </div>
              <Progress percent={task.progress} size="small" status={progressStatus} style={{ marginBottom: 0 }} />
            </div>
            <Space size={4}>
              <Tooltip title={task.status === 'running' ? '暂停' : '重试'}>
                <Button type="text" size="small"
                  icon={task.status === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                  onClick={() => task.status !== 'running' && onRetry(task.id)}
                />
              </Tooltip>
              <Tooltip title="查看日志">
                <Button type="text" size="small" icon={<FileTextOutlined />} onClick={() => setLogVisible(true)} />
              </Tooltip>
              <Popconfirm title="确认删除" description="删除后任务将无法恢复" okText="确认" cancelText="取消" okButtonProps={{ danger: true }}
                onConfirm={() => onDelete(task.id)}>
                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </Space>
          </div>
          <Row justify="space-between" style={{ marginTop: 8 }}>
            <Space size={16}>
              <Text type="secondary" style={{ fontSize: 11 }}>📊 {formatRecords(task.records)} 条</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>⏱ {task.duration}</Text>
            </Space>
            <Text type="secondary" style={{ fontSize: 11 }}>开始: {task.startedAt}</Text>
          </Row>
        </Card>
        {logModal}
      </>
    );
  }

  return (
    <>
      <Card
        size="small"
        hoverable
        actions={[
          <Tooltip title={task.status === 'running' ? '暂停' : '重试'} key="toggle">
            <Button type="text" size="small"
              icon={task.status === 'running' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              onClick={() => { if (task.status !== 'running') onRetry(task.id); }}
            >
              {task.status === 'running' ? '暂停' : '重试'}
            </Button>
          </Tooltip>,
          <Tooltip title="查看日志" key="log">
            <Button type="text" size="small" icon={<FileTextOutlined />} onClick={() => setLogVisible(true)}>日志</Button>
          </Tooltip>,
          <Popconfirm key="delete" title="确认删除" description="删除后任务将无法恢复" okText="确认" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={() => onDelete(task.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>,
        ]}
        style={{
          borderLeft: `3px solid ${statusColor}`,
          transition: 'box-shadow 0.3s ease, transform 0.2s ease',
        }}
        bodyStyle={{ padding: '16px' }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Space size={8}>
            <div
              style={{
                width: 32, height: 32, borderRadius: '50%', background: `${iconHex}15`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: iconColor, fontSize: 16,
              }}
            >
              {templateIcon}
            </div>
            <Text strong style={{ fontSize: 14 }}>{task.template}</Text>
          </Space>
          <StatusBadge status={task.status} />
        </div>

        {/* Stats */}
        <Row gutter={12} style={{ marginBottom: 8 }}>
          <Col span={12}>
            <Text type="secondary" style={{ fontSize: 11 }}>📊 记录数：{formatRecords(task.records)}</Text>
          </Col>
          <Col span={12} style={{ textAlign: 'right' }}>
            <Text type="secondary" style={{ fontSize: 11 }}>⏱ 耗时：{task.duration}</Text>
          </Col>
        </Row>

        {/* Progress */}
        <Progress percent={task.progress} size="small" status={progressStatus} style={{ marginBottom: 8 }} />

        {/* Bottom */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text type="secondary" style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 3 }}>
            <ClockCircleOutlined />{task.startedAt}
          </Text>
          <Text type="secondary" style={{ fontSize: 11 }}>ID: {task.id}</Text>
        </div>
      </Card>
      {logModal}
    </>
  );
};

export default TaskCard;
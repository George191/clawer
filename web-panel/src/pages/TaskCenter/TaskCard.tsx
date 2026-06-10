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
  GlobalOutlined,
  ApiOutlined,
  FileSearchOutlined,
  SafetyCertificateOutlined,
  MessageOutlined,
  BarChartOutlined,
} from '@ant-design/icons';
import StatusBadge from '@/components/StatusBadge';
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
  '网页采集': '#3B82F6',
  'API采集': '#10B981',
  '日志采集': '#F59E0B',
  '数据清洗': '#8B5CF6',
  '质量校验': '#06B6D4',
  '消息采集': '#EC4899',
};

const TEMPLATE_GRADIENT: Record<string, string> = {
  '网页采集': 'linear-gradient(135deg, #3B82F6, #2563EB)',
  'API采集': 'linear-gradient(135deg, #10B981, #059669)',
  '日志采集': 'linear-gradient(135deg, #F59E0B, #D97706)',
  '数据清洗': 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
  '质量校验': 'linear-gradient(135deg, #06B6D4, #0891B2)',
  '消息采集': 'linear-gradient(135deg, #EC4899, #DB2777)',
};

const TaskCard: React.FC<TaskCardProps> = ({ task, compact, onDelete, onRetry }) => {
  const [logVisible, setLogVisible] = useState(false);
  const { token } = theme.useToken();

  const iconColor = TEMPLATE_COLOR[task.template] || '#3B82F6';
  const iconGradient = TEMPLATE_GRADIENT[task.template] || 'linear-gradient(135deg, #3B82F6, #2563EB)';
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
        className="console-card"
        style={{ maxHeight: 400 }}>
        <div className="console-line">
          <span className="badge success">✓</span>
          <span className="time">23:00:00</span>
          <span>[INFO] 任务启动: {task.template}</span>
        </div>
        <div className="console-line" style={{ animationDelay: '0.1s' }}>
          <span className="badge success">✓</span>
          <span className="time">23:00:01</span>
          <span>[INFO] 连接数据源...</span>
        </div>
        <div className="console-line" style={{ animationDelay: '0.2s' }}>
          <span className="badge success">✓</span>
          <span className="time">23:00:03</span>
          <span>[INFO] 数据源连接成功</span>
        </div>
        <div className="console-line" style={{ animationDelay: '0.3s' }}>
          <span className="badge success">✓</span>
          <span className="time">23:00:05</span>
          <span>[INFO] 开始处理记录...</span>
        </div>
        <div className="console-line" style={{ animationDelay: '0.4s' }}>
          <span className="badge success">✓</span>
          <span className="time">23:00:15</span>
          <span>[INFO] 处理完成, 共 {formatRecords(task.records)} 条</span>
        </div>
        <div className="console-line" style={{ animationDelay: '0.5s' }}>
          <span className="badge success">✓</span>
          <span className="time">23:00:18</span>
          <span>[INFO] 写入目标完成</span>
        </div>
      </div>
    </Modal>
  );

  if (compact) {
    return (
      <>
        <Card
          className="premium-card"
          size="small"
          styles={{ body: { padding: '14px 18px' } }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 38, height: 38, borderRadius: 8,
                background: iconGradient,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#fff', fontSize: 16, flexShrink: 0,
              }}
            >
              {templateIcon}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text strong ellipsis style={{ maxWidth: 200, color: '#F1F5F9', fontSize: 14 }}>{task.template}</Text>
                <StatusBadge status={task.status} />
              </div>
              <Progress
                percent={task.progress}
                size="small"
                status={progressStatus}
                style={{ marginBottom: 0 }}
                strokeColor={{
                  '0%': '#3B82F6',
                  '100%': '#6366F1',
                }}
              />
            </div>
            <Space size={2}>
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
          <Row justify="space-between" style={{ marginTop: 10 }}>
            <Space size={16}>
              <Text style={{ fontSize: 11, color: '#94A3B8' }}>{formatRecords(task.records)} 条</Text>
              <Text style={{ fontSize: 11, color: '#94A3B8' }}>{task.duration}</Text>
            </Space>
            <Text style={{ fontSize: 11, color: '#64748B' }}>开始: {task.startedAt}</Text>
          </Row>
        </Card>
        {logModal}
      </>
    );
  }

  return (
    <>
      <Card
        className="premium-card"
        size="small"
        styles={{ body: { padding: '18px' } }}
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
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <Space size={10}>
            <div
              style={{
                width: 36, height: 36, borderRadius: 8,
                background: iconGradient,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#fff', fontSize: 16,
              }}
            >
              {templateIcon}
            </div>
            <Text strong style={{ fontSize: 14, color: '#F1F5F9' }}>{task.template}</Text>
          </Space>
          <StatusBadge status={task.status} />
        </div>

        {/* Progress */}
        <Progress
          percent={task.progress}
          size="small"
          status={progressStatus}
          strokeColor={{
            '0%': '#3B82F6',
            '100%': '#6366F1',
          }}
          style={{ marginBottom: 14 }}
        />

        {/* Footer stats */}
        <Row justify="space-between">
          <Space size={20}>
            <div>
              <Text style={{ fontSize: 10, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>记录数</Text>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#F1F5F9', marginTop: 2 }}>
                {formatRecords(task.records)}
              </div>
            </div>
            <div>
              <Text style={{ fontSize: 10, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>耗时</Text>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#F1F5F9', marginTop: 2 }}>
                {task.duration}
              </div>
            </div>
          </Space>
          <Text style={{ fontSize: 11, color: '#475569', alignSelf: 'flex-end' }}>
            {task.startedAt}
          </Text>
        </Row>
      </Card>
      {logModal}
    </>
  );
};

export default TaskCard;
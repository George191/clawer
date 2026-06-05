import React from 'react';
import { Tag, theme } from 'antd';
import {
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  PauseCircleOutlined,
  StopOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';

type StatusType =
  | 'running'
  | 'completed'
  | 'failed'
  | 'queued'
  | 'paused'
  | 'stopped'
  | 'error'
  | 'active'
  | 'inactive';

interface StatusBadgeProps {
  status: StatusType;
}

const statusConfig: Record<
  StatusType,
  { color: string; icon: React.ReactNode; label: string; pulse?: boolean }
> = {
  running: { color: '#1677ff', icon: <PlayCircleOutlined />, label: '运行中', pulse: true },
  completed: { color: '#52c41a', icon: <CheckCircleOutlined />, label: '已完成' },
  failed: { color: '#ff4d4f', icon: <CloseCircleOutlined />, label: '失败' },
  queued: { color: '#faad14', icon: <ClockCircleOutlined />, label: '排队中' },
  paused: { color: '#8c8c8c', icon: <PauseCircleOutlined />, label: '已暂停' },
  stopped: { color: '#8c8c8c', icon: <StopOutlined />, label: '已停止' },
  error: { color: '#ff4d4f', icon: <ExclamationCircleOutlined />, label: '异常' },
  active: { color: '#52c41a', icon: <CheckCircleOutlined />, label: '活跃' },
  inactive: { color: '#8c8c8c', icon: <PauseCircleOutlined />, label: '已停用' },
};

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const { token } = theme.useToken();
  const config = statusConfig[status] ?? statusConfig.inactive;

  const dotStyle: React.CSSProperties = {
    display: 'inline-block',
    width: 6,
    height: 6,
    borderRadius: '50%',
    backgroundColor: config.color,
    marginRight: 4,
    verticalAlign: 'middle',
  };

  const pulseKeyframes = `
    @keyframes status-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
  `;

  return (
    <>
      {config.pulse && <style>{pulseKeyframes}</style>}
      <Tag
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          padding: '2px 10px',
          borderRadius: token.borderRadiusSM,
          border: `1px solid ${config.color}20`,
          background: `${config.color}08`,
          color: config.color,
          fontSize: 12,
          fontWeight: 500,
          lineHeight: '20px',
        }}
      >
        <span
          style={{
            ...dotStyle,
            animation: config.pulse ? 'status-pulse 1.5s ease-in-out infinite' : undefined,
          }}
        />
        <span style={{ fontSize: 13, marginLeft: 2 }}>{config.icon}</span>
        {config.label}
      </Tag>
    </>
  );
};

export default StatusBadge;

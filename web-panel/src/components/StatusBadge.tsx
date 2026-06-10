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
  { color: string; bg: string; border: string; icon: React.ReactNode; label: string; pulse?: boolean }
> = {
  running: { color: '#34D399', bg: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.25)', icon: <PlayCircleOutlined />, label: '运行中', pulse: true },
  completed: { color: '#60A5FA', bg: 'rgba(59, 130, 246, 0.12)', border: 'rgba(59, 130, 246, 0.25)', icon: <CheckCircleOutlined />, label: '已完成' },
  failed: { color: '#F87171', bg: 'rgba(239, 68, 68, 0.12)', border: 'rgba(239, 68, 68, 0.25)', icon: <CloseCircleOutlined />, label: '失败' },
  queued: { color: '#FBBF24', bg: 'rgba(245, 158, 11, 0.12)', border: 'rgba(245, 158, 11, 0.25)', icon: <ClockCircleOutlined />, label: '排队中' },
  paused: { color: '#94A3B8', bg: 'rgba(100, 116, 139, 0.12)', border: 'rgba(100, 116, 139, 0.25)', icon: <PauseCircleOutlined />, label: '已暂停' },
  stopped: { color: '#94A3B8', bg: 'rgba(100, 116, 139, 0.12)', border: 'rgba(100, 116, 139, 0.25)', icon: <StopOutlined />, label: '已停止' },
  error: { color: '#F87171', bg: 'rgba(239, 68, 68, 0.12)', border: 'rgba(239, 68, 68, 0.25)', icon: <ExclamationCircleOutlined />, label: '异常' },
  active: { color: '#34D399', bg: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.25)', icon: <CheckCircleOutlined />, label: '活跃' },
  inactive: { color: '#94A3B8', bg: 'rgba(100, 116, 139, 0.12)', border: 'rgba(100, 116, 139, 0.25)', icon: <PauseCircleOutlined />, label: '已停用' },
};

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const config = statusConfig[status] ?? statusConfig.inactive;

  const pulseKeyframes = `
    @keyframes status-pulse-badge {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(0.8); }
    }
  `;

  return (
    <>
      {config.pulse && <style>{pulseKeyframes}</style>}
      <span
        className="status-pill"
        style={{
          background: config.bg,
          borderColor: config.border,
          color: config.color,
          fontSize: 12,
          fontWeight: 500,
          padding: '3px 12px',
          borderRadius: 20,
          border: `1px solid ${config.border}`,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          letterSpacing: '0.02em',
        }}
      >
        <span
          style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: '50%',
            backgroundColor: config.color,
            animation: config.pulse ? 'status-pulse-badge 2s ease-in-out infinite' : undefined,
          }}
        />
        <span style={{ fontSize: 11 }}>{config.icon}</span>
        {config.label}
      </span>
    </>
  );
};

export default StatusBadge;
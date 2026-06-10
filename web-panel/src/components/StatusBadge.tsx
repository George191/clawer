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

type StatusVariant = 'primary' | 'success' | 'danger' | 'warning' | 'neutral';

const statusVariant: Record<StatusType, StatusVariant> = {
  running: 'primary',
  completed: 'success',
  failed: 'danger',
  queued: 'warning',
  paused: 'neutral',
  stopped: 'neutral',
  error: 'danger',
  active: 'success',
  inactive: 'neutral',
};

const statusConfig: Record<
  StatusType,
  { icon: React.ReactNode; label: string; pulse?: boolean }
> = {
  running: { icon: <PlayCircleOutlined />, label: '运行中', pulse: true },
  completed: { icon: <CheckCircleOutlined />, label: '已完成' },
  failed: { icon: <CloseCircleOutlined />, label: '失败' },
  queued: { icon: <ClockCircleOutlined />, label: '排队中' },
  paused: { icon: <PauseCircleOutlined />, label: '已暂停' },
  stopped: { icon: <StopOutlined />, label: '已停止' },
  error: { icon: <ExclamationCircleOutlined />, label: '异常' },
  active: { icon: <CheckCircleOutlined />, label: '活跃' },
  inactive: { icon: <PauseCircleOutlined />, label: '已停用' },
};

const variantTokens: Record<StatusVariant, { text: string; bg: string; border: string }> = {
  primary: {
    text: 'var(--theme-color-primary-text)',
    bg: 'var(--theme-color-primary-bg-weak)',
    border: 'var(--theme-color-primary-border-weak)',
  },
  success: {
    text: 'var(--theme-color-success-text)',
    bg: 'var(--theme-color-success-bg-weak)',
    border: 'var(--theme-color-success-border-weak)',
  },
  danger: {
    text: 'var(--theme-color-danger-text)',
    bg: 'var(--theme-color-danger-bg-weak)',
    border: 'var(--theme-color-danger-border-weak)',
  },
  warning: {
    text: 'var(--theme-color-warning-text)',
    bg: 'var(--theme-color-warning-bg-weak)',
    border: 'var(--theme-color-warning-border-weak)',
  },
  neutral: {
    text: 'var(--theme-color-neutral-text-weaker)',
    bg: 'var(--theme-color-neutral-hover)',
    border: 'var(--theme-color-neutral-border-weak)',
  },
};

const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const { token } = theme.useToken();
  const config = statusConfig[status] ?? statusConfig.inactive;
  const variant = statusVariant[status] ?? 'neutral';
  const v = variantTokens[variant];

  const dotStyle: React.CSSProperties = {
    display: 'inline-block',
    width: 6,
    height: 6,
    borderRadius: '50%',
    backgroundColor: v.text,
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
          border: `1px solid ${v.border}`,
          background: v.bg,
          color: v.text,
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

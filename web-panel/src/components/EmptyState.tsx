import React from 'react';
import { Empty, Button } from 'antd';
import {
  InboxOutlined,
  FileTextOutlined,
  AlertOutlined,
  SearchOutlined,
  ExclamationCircleOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';

type EmptyPreset = 'no-data' | 'no-tasks' | 'no-alerts' | 'no-results' | 'error' | 'no-templates';

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  preset?: EmptyPreset;
}

const presetIcons: Record<EmptyPreset, React.ReactNode> = {
  'no-data': <InboxOutlined />,
  'no-tasks': <FileTextOutlined />,
  'no-alerts': <AlertOutlined />,
  'no-results': <SearchOutlined />,
  error: <ExclamationCircleOutlined />,
  'no-templates': <AppstoreOutlined />,
};

const EmptyState: React.FC<EmptyStateProps> = ({
  icon,
  title,
  description,
  action,
  preset,
}) => {
  const displayIcon = icon ?? (preset ? presetIcons[preset] : <InboxOutlined />);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '64px 24px',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 20,
          background: 'rgba(59, 130, 246, 0.08)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 24,
        }}
      >
        <span style={{ fontSize: 32, color: '#93C5FD', lineHeight: 1 }}>
          {displayIcon}
        </span>
      </div>

      <div
        style={{
          fontSize: 15,
          fontWeight: 600,
          color: '#E2E8F0',
          marginBottom: 6,
        }}
      >
        {title}
      </div>

      {description && (
        <div
          style={{
            fontSize: 13,
            color: '#64748B',
            maxWidth: 320,
            lineHeight: 1.6,
            marginBottom: 24,
          }}
        >
          {description}
        </div>
      )}

      {action && <div>{action}</div>}
    </div>
  );
};

/** Convenience presets with appropriate defaults */
export function EmptyStateNoData(props?: { description?: string; action?: React.ReactNode }) {
  return (
    <EmptyState
      preset="no-data"
      title="暂无数据"
      description={props?.description ?? '当前没有可展示的数据'}
      action={props?.action}
    />
  );
}

export function EmptyStateNoTasks(props?: { action?: React.ReactNode }) {
  return (
    <EmptyState
      preset="no-tasks"
      title="暂无任务"
      description="当前没有运行中的任务，点击下方按钮创建一个"
      action={props?.action ?? <Button type="primary">创建任务</Button>}
    />
  );
}

export function EmptyStateNoAlerts() {
  return (
    <EmptyState
      preset="no-alerts"
      title="告警为空"
      description="系统运行正常，当前没有告警信息"
    />
  );
}

export function EmptyStateNoResults(props?: { description?: string }) {
  return (
    <EmptyState
      preset="no-results"
      title="无匹配结果"
      description={props?.description ?? '请调整查询条件后重试'}
    />
  );
}

export function EmptyStateError(props?: { description?: string; onRetry?: () => void }) {
  return (
    <EmptyState
      preset="error"
      title="加载失败"
      description={props?.description ?? '数据加载异常，请稍后重试'}
      action={
        props?.onRetry ? (
          <Button type="primary" onClick={props.onRetry}>
            重新加载
          </Button>
        ) : null
      }
    />
  );
}

export function EmptyStateNoTemplates(props?: { action?: React.ReactNode }) {
  return (
    <EmptyState
      preset="no-templates"
      title="暂无模板"
      description="没有可用的任务模板"
      action={props?.action ?? <Button type="primary">创建模板</Button>}
    />
  );
}

export default EmptyState;
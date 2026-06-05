import React, { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Collapse, Typography } from 'antd';
import {
  ExclamationCircleOutlined,
  ReloadOutlined,
  BugOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ errorInfo: info });
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleRefresh = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: 320,
            padding: '48px 24px',
            textAlign: 'center',
          }}
        >
          <ExclamationCircleOutlined
            style={{
              fontSize: 64,
              color: 'var(--ant-color-error, #ff4d4f)',
              marginBottom: 24,
            }}
          />

          <h2
            style={{
              fontSize: 20,
              fontWeight: 600,
              margin: '0 0 8px',
              color: 'var(--ant-color-text, #1d1d1d)',
            }}
          >
            页面出错了
          </h2>

          <Text
            type="secondary"
            style={{
              maxWidth: 400,
              display: 'block',
              marginBottom: 24,
              fontSize: 14,
            }}
          >
            {this.state.error?.message ?? '发生了未知错误，请尝试刷新页面'}
          </Text>

          <div style={{ display: 'flex', gap: 12, marginBottom: 32 }}>
            <Button type="primary" icon={<ReloadOutlined />} onClick={this.handleRefresh}>
              刷新页面
            </Button>
            <Button onClick={this.handleReset}>
              重试
            </Button>
          </div>

          {/* Error details (collapsible) */}
          <Collapse
            ghost
            size="small"
            style={{ maxWidth: 640, width: '100%' }}
            items={[
              {
                key: 'error-detail',
                label: (
                  <span style={{ fontSize: 13, color: 'var(--ant-color-text-secondary, #8b949e)' }}>
                    <BugOutlined style={{ marginRight: 6 }} />
                    错误详情
                  </span>
                ),
                children: (
                  <div style={{ textAlign: 'left', fontSize: 12 }}>
                    <Paragraph
                      code
                      style={{
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                        borderRadius: 6,
                        padding: 12,
                        margin: 0,
                      }}
                    >
                      {this.state.error?.stack ?? this.state.error?.message}
                    </Paragraph>
                    {this.state.errorInfo?.componentStack && (
                      <>
                        <Text strong style={{ display: 'block', marginTop: 12, marginBottom: 4 }}>
                          Component Stack:
                        </Text>
                        <Paragraph
                          code
                          style={{
                            whiteSpace: 'pre-wrap',
                            borderRadius: 6,
                            padding: 12,
                            margin: 0,
                          }}
                        >
                          {this.state.errorInfo.componentStack}
                        </Paragraph>
                      </>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
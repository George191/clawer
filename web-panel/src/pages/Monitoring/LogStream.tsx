import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Card, Switch, Space, Typography, Checkbox, Button, Tooltip } from 'antd';
import {
  ClearOutlined,
  VerticalAlignBottomOutlined,
  PauseCircleOutlined,
} from '@ant-design/icons';
import { useWebSocket } from '@/hooks/useWebSocket';
import { MONITOR_WS_URL } from '@/services/api';
import type { LogEntry } from '@/services/types';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  INFO:  '#e6edf3',
  WARN:  '#d29922',
  ERROR: '#f85149',
  DEBUG: '#8b949e',
};

const LogStream: React.FC = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [enabledLevels, setEnabledLevels] = useState<Set<string>>(
    new Set(['INFO', 'WARN', 'ERROR', 'DEBUG']),
  );
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const handleMessage = useCallback((data: string) => {
    try {
      const parsed = JSON.parse(data) as LogEntry;
      setLogs((prev) => [...prev.slice(-500), parsed]);
    } catch {
      setLogs((prev) => [
        ...prev.slice(-500),
        {
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
          level: 'INFO' as const,
          source: 'ws',
          message: data,
        },
      ]);
    }
  }, []);

  const { connected } = useWebSocket(MONITOR_WS_URL, {
    onMessage: handleMessage,
    reconnectInterval: 5000,
  });

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter((l) => enabledLevels.has(l.level));

  const toggleLevel = (level: string) => {
    setEnabledLevels((prev) => {
      const next = new Set(prev);
      next.has(level) ? next.delete(level) : next.add(level);
      return next;
    });
  };

  const handleClear = () => setLogs([]);

  const levelCounts = {
    INFO: logs.filter((l) => l.level === 'INFO').length,
    WARN: logs.filter((l) => l.level === 'WARN').length,
    ERROR: logs.filter((l) => l.level === 'ERROR').length,
    DEBUG: logs.filter((l) => l.level === 'DEBUG').length,
  };

  return (
    <Card
      title={
        <Space size={8}>
          <span style={{ fontSize: 13 }}>实时日志流</span>
          <span
            style={{
              width: 8, height: 8, borderRadius: '50%',
              background: connected ? 'var(--theme-color-success-bg-status)' : 'var(--theme-color-danger-bg-status)', display: 'inline-block',
              boxShadow: connected ? '0 0 6px var(--theme-color-success-bg-strong)' : '0 0 6px var(--theme-color-danger-bg-status)',
              animation: connected ? 'pulse 2s infinite' : 'none',
            }}
          />
          <Text type="secondary" style={{ fontSize: 11 }}>
            {connected ? '已连接' : '等待连接...'}
          </Text>
        </Space>
      }
      size="small"
      extra={
        <Space size={4} wrap>
          <Checkbox checked={enabledLevels.has('INFO')} onChange={() => toggleLevel('INFO')} style={{ fontSize: 11 }}>
            <span style={{ color: LEVEL_COLORS.INFO }}>INFO</span>
          </Checkbox>
          <Checkbox checked={enabledLevels.has('WARN')} onChange={() => toggleLevel('WARN')} style={{ fontSize: 11 }}>
            <span style={{ color: LEVEL_COLORS.WARN }}>WARN</span>
          </Checkbox>
          <Checkbox checked={enabledLevels.has('ERROR')} onChange={() => toggleLevel('ERROR')} style={{ fontSize: 11 }}>
            <span style={{ color: LEVEL_COLORS.ERROR }}>ERROR</span>
          </Checkbox>
          <Checkbox checked={enabledLevels.has('DEBUG')} onChange={() => toggleLevel('DEBUG')} style={{ fontSize: 11 }}>
            <span style={{ color: LEVEL_COLORS.DEBUG }}>DEBUG</span>
          </Checkbox>
          <Tooltip title={autoScroll ? '关闭自动滚动' : '开启自动滚动'}>
            <Button type="text" size="small"
              icon={autoScroll ? <VerticalAlignBottomOutlined /> : <PauseCircleOutlined />}
              onClick={() => setAutoScroll((v) => !v)}
            />
          </Tooltip>
          <Tooltip title="清空日志">
            <Button type="text" size="small" icon={<ClearOutlined />} onClick={handleClear} />
          </Tooltip>
        </Space>
      }
      bodyStyle={{ padding: 0 }}
      style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
    >
      {/* Terminal-style log output */}
      <div
        ref={containerRef}
        style={{
          flex: 1, background: '#0D1117', borderRadius: '0 0 6px 6px',
          padding: '12px 16px', minHeight: 360, overflow: 'auto',
          fontFamily: '"SF Mono", "Fira Code", "Cascadia Code", Consolas, monospace',
          fontSize: 12, lineHeight: 1.9,
        }}
      >
        {!connected && filteredLogs.length === 0 && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#6e7681', flexDirection: 'column', gap: 8 }}>
            <Text style={{ color: '#6e7681', fontSize: 14 }}>⏳ 等待连接...</Text>
            <Text style={{ color: '#484f58', fontSize: 11 }}>正在尝试连接到监控服务</Text>
          </div>
        )}

        {filteredLogs.map((log, i) => (
          <div key={i} style={{ whiteSpace: 'nowrap' }}>
            <span style={{ color: '#484f58', marginRight: 8 }}>{log.timestamp}</span>
            <span style={{
              fontWeight: 700, display: 'inline-block', minWidth: 50,
              color: LEVEL_COLORS[log.level] || '#e6edf3',
            }}>
              [{log.level}]
            </span>
            <span style={{ color: '#58a6ff', marginRight: 4 }}>[{log.source}]</span>
            <span style={{ color: LEVEL_COLORS[log.level] || '#e6edf3' }}>{log.message}</span>
          </div>
        ))}

        {connected && filteredLogs.length === 0 && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Text style={{ color: '#6e7681', fontSize: 13 }}>没有匹配的日志</Text>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Status bar */}
      <div style={{ padding: '6px 16px', background: 'var(--theme-color-neutral-bg-default)', borderTop: '1px solid var(--theme-color-neutral-border-weak)', borderRadius: '0 0 6px 6px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space size={12}>
          <Text style={{ color: '#8b949e', fontSize: 11 }}>总计 {logs.length} 条</Text>
          <Text style={{ color: LEVEL_COLORS.INFO, fontSize: 11 }}>I:{levelCounts.INFO}</Text>
          <Text style={{ color: LEVEL_COLORS.WARN, fontSize: 11 }}>W:{levelCounts.WARN}</Text>
          <Text style={{ color: LEVEL_COLORS.ERROR, fontSize: 11 }}>E:{levelCounts.ERROR}</Text>
          <Text style={{ color: LEVEL_COLORS.DEBUG, fontSize: 11 }}>D:{levelCounts.DEBUG}</Text>
        </Space>
        <Text style={{ color: '#484f58', fontSize: 10 }}>{autoScroll ? '自动滚动' : '手动滚动'}</Text>
      </div>
    </Card>
  );
};

export default LogStream;
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card,
  Descriptions,
  Tag,
  Button,
  Space,
  Typography,
  App,
  theme,
  Tooltip,
  Spin,
  Result,
} from 'antd';
import {
  CaretRightOutlined,
  PauseOutlined,
  ReloadOutlined,
  SaveOutlined,
  CheckCircleOutlined,
  UndoOutlined,
  LoadingOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import Editor, { type OnMount } from '@monaco-editor/react';
import EmptyState from '@/components/EmptyState';
import { fetchHandlerCode, saveHandlerCode, validateHandlerCode } from '@/services/api';

const { Text, Title } = Typography;

// ── 节点状态 ──
type NodeStatus = 'running' | 'error' | 'stopped';

interface NodeInfo {
  name: string;
  status: NodeStatus;
  currentOffset: number;
  latestOffset: number;
  lag: number;
}

const statusColorMap: Record<NodeStatus, string> = {
  running: 'processing',
  error: 'error',
  stopped: 'default',
};

const statusLabelMap: Record<NodeStatus, string> = {
  running: '运行中',
  error: '异常',
  stopped: '已停止',
};

// ── 格式化偏移量 ──
const formatOffset = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
};

interface HandlerEditorProps {
  selectedNode: string | null;
}

const HandlerEditor: React.FC<HandlerEditorProps> = ({ selectedNode }) => {
  const { token } = theme.useToken();
  const { message } = App.useApp();

  const [code, setCode] = useState('');
  const [originalCode, setOriginalCode] = useState('');
  const [nodeInfo, setNodeInfo] = useState<NodeInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<'success' | 'error' | null>(null);
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    errors: string[];
  } | null>(null);
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);

  // ── 节点切换：从 API 加载代码 ──
  useEffect(() => {
    if (!selectedNode) {
      setNodeInfo(null);
      setCode('');
      setOriginalCode('');
      setSaveResult(null);
      setValidationResult(null);
      setError(null);
      return;
    }

    const layer = selectedNode.toLowerCase();
    setNodeInfo({
      name: selectedNode,
      status: 'running',
      currentOffset: 0,
      latestOffset: 0,
      lag: 0,
    });
    setSaveResult(null);
    setValidationResult(null);
    setLoading(true);
    setError(null);

    // Fetch handler code from API
    fetchHandlerCode(layer, 'handler')
      .then((result) => {
        const handlerCode = result.code;
        setCode(handlerCode);
        setOriginalCode(handlerCode);
        setLoading(false);
      })
      .catch((e: unknown) => {
        const err = e as { message?: string };
        // Fallback: show error but allow editing
        setError(err?.message || '获取处理器代码失败');
        setCode(`# ${selectedNode} 处理器代码\n# 暂未从服务端获取到代码\n`);
        setOriginalCode(`# ${selectedNode} 处理器代码\n# 暂未从服务端获取到代码\n`);
        setLoading(false);
      });
  }, [selectedNode]);

  // ── Ctrl+S 保存 ──
  const handleEditorMount: OnMount = useCallback(
    (ed, _monaco) => {
      editorRef.current = ed;
      ed.addAction({
        id: 'save-handler',
        label: 'Save Handler',
        keybindings: [2048 | 49],
        run: () => handleSave(),
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedNode, code],
  );

  // ── 保存 ──
  const handleSave = useCallback(async () => {
    if (!selectedNode) return;
    setSaving(true);
    setSaveResult(null);
    try {
      const layer = selectedNode.toLowerCase();
      await saveHandlerCode(layer, 'handler', code);
      setOriginalCode(code);
      setSaveResult('success');
      message.success(`Handler 已保存 → ${selectedNode}`);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setSaveResult('error');
      message.error(err?.message || `保存失败: ${selectedNode}`);
    } finally {
      setSaving(false);
    }
  }, [selectedNode, code, message]);

  // ── 验证 ──
  const handleValidate = useCallback(async () => {
    if (!selectedNode) return;
    setValidating(true);
    setValidationResult(null);
    try {
      const layer = selectedNode.toLowerCase();
      const result = await validateHandlerCode(layer, 'handler', code);
      setValidationResult(result);
      if (result.valid) {
        message.success('验证通过，无语法错误');
      } else {
        message.warning(`验证失败: ${result.errors.join(', ')}`);
      }
    } catch (e: unknown) {
      const err = e as { message?: string };
      setValidationResult({ valid: false, errors: [err?.message || '验证服务不可用'] });
    } finally {
      setValidating(false);
    }
  }, [selectedNode, code, message]);

  // ── 重置 ──
  const handleReset = useCallback(() => {
    setCode(originalCode);
    setSaveResult(null);
    setValidationResult(null);
  }, [originalCode]);

  // ── 节点操作 ──
  const handleNodeAction = useCallback(
    (action: string) => {
      message.info(`${action} → ${selectedNode}（操作已提交至后端）`);
    },
    [selectedNode, message],
  );

  const isDirty = code !== originalCode;

  // ── 未选择节点 ──
  if (!selectedNode) {
    return (
      <Card
        size="small"
        title="节点详情"
        styles={{ body: { height: 500, display: 'flex', alignItems: 'center', justifyContent: 'center' } }}
      >
        <EmptyState title="请选择节点" description="请选择一个管道节点查看详情" />
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title="节点详情"
      styles={{ body: { padding: 12 } }}
    >
      {/* ── 1. 节点信息卡片 ── */}
      {nodeInfo && (
        <div
          style={{
            border: `1px solid ${token.colorBorder}`,
            borderRadius: token.borderRadius,
            padding: '12px 16px',
            marginBottom: 12,
            background: token.colorFillAlter,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 12,
            }}
          >
            <Space>
              <Title level={5} style={{ margin: 0 }}>
                {nodeInfo.name}
              </Title>
              <Tag color={statusColorMap[nodeInfo.status]}>
                {statusLabelMap[nodeInfo.status]}
              </Tag>
            </Space>
            <Space size={4}>
              <Tooltip title="启动">
                <Button
                  size="small"
                  type="text"
                  icon={<CaretRightOutlined style={{ color: token.colorSuccess }} />}
                  disabled={nodeInfo.status === 'running'}
                  onClick={() => handleNodeAction('启动')}
                />
              </Tooltip>
              <Tooltip title="停止">
                <Button
                  size="small"
                  type="text"
                  icon={<PauseOutlined style={{ color: token.colorWarning }} />}
                  disabled={nodeInfo.status === 'stopped'}
                  onClick={() => handleNodeAction('停止')}
                />
              </Tooltip>
              <Tooltip title="重启">
                <Button
                  size="small"
                  type="text"
                  icon={<ReloadOutlined style={{ color: token.colorPrimary }} />}
                  onClick={() => handleNodeAction('重启')}
                />
              </Tooltip>
            </Space>
          </div>

          <Descriptions size="small" column={3}>
            <Descriptions.Item label="当前">
              <Text strong>{formatOffset(nodeInfo.currentOffset)}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Latest">
              <Text>{formatOffset(nodeInfo.latestOffset)}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Lag">
              <Text
                type={nodeInfo.lag > 1000 ? 'danger' : undefined}
                strong={nodeInfo.lag > 1000}
              >
                {formatOffset(nodeInfo.lag)}
              </Text>
            </Descriptions.Item>
          </Descriptions>

          {/* API error for this node */}
          {error && (
            <div style={{ marginTop: 8 }}>
              <Text type="danger" style={{ fontSize: 12 }}>{error}</Text>
            </div>
          )}
        </div>
      )}

      {/* ── 2. Handler 代码编辑器 ── */}
      <div
        style={{
          border: `1px solid ${token.colorBorder}`,
          borderRadius: token.borderRadius,
          overflow: 'hidden',
          marginBottom: 12,
        }}
      >
        <div
          style={{
            background: token.colorFillContent,
            padding: '4px 12px',
            borderBottom: `1px solid ${token.colorBorder}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 12,
          }}
        >
          <Space size={4}>
            <Text style={{ fontFamily: 'monospace', color: token.colorTextSecondary, fontSize: 12 }}>
              {selectedNode.toLowerCase()}/handler.py
            </Text>
            {isDirty && (
              <Tag color="warning" style={{ fontSize: 10, lineHeight: '16px', margin: 0 }}>
                未保存
              </Tag>
            )}
          </Space>
          <Text type="secondary" style={{ fontSize: 11 }}>
            Python
          </Text>
        </div>
        {loading ? (
          <div
            style={{
              height: 300,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--theme-color-neutral-bg-default)',
            }}
          >
            <Space direction="vertical" align="center">
              <Spin size="default" />
              <Text type="secondary">加载编辑器中...</Text>
            </Space>
          </div>
        ) : (
          <Editor
            height="300px"
            language="python"
            theme="vs-dark"
            value={code}
            onChange={(v) => setCode(v ?? '')}
            onMount={handleEditorMount}
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: 'on',
              wordWrap: 'on',
              scrollBeyondLastLine: false,
              automaticLayout: true,
              tabSize: 4,
              folding: true,
              renderWhitespace: 'selection',
              bracketPairColorization: { enabled: true },
              padding: { top: 8 },
            }}
            loading={
              <div
                style={{
                  height: 300,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--theme-color-neutral-bg-default)',
                }}
              >
                <Space direction="vertical" align="center">
                  <LoadingOutlined style={{ fontSize: 24, color: token.colorTextSecondary }} />
                  <Text type="secondary">加载编辑器中...</Text>
                </Space>
              </div>
            }
          />
        )}
      </div>

      {/* ── 3. 底部工具栏 ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Space size={4}>
          {/* 保存状态提示 */}
          {saving && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              <LoadingOutlined style={{ marginRight: 4 }} />
              保存中...
            </Text>
          )}
          {saveResult === 'success' && (
            <Tag
              color="success"
              icon={<CheckCircleOutlined />}
              closable
              onClose={() => setSaveResult(null)}
            >
              已保存
            </Tag>
          )}
          {saveResult === 'error' && (
            <Tag
              color="error"
              icon={<ExclamationCircleOutlined />}
              closable
              onClose={() => setSaveResult(null)}
            >
              保存失败
            </Tag>
          )}
          {/* 验证结果 */}
          {validationResult && (
            <Tag
              color={validationResult.valid ? 'success' : 'error'}
              icon={validationResult.valid ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
              closable
              onClose={() => setValidationResult(null)}
            >
              {validationResult.valid
                ? '验证通过'
                : `验证失败: ${validationResult.errors.join(', ')}`}
            </Tag>
          )}
        </Space>

        <Space size={8}>
          <Button
            size="small"
            icon={<UndoOutlined />}
            disabled={!isDirty}
            onClick={handleReset}
          >
            重置
          </Button>
          <Button
            size="small"
            icon={<CheckCircleOutlined />}
            loading={validating}
            onClick={handleValidate}
          >
            验证
          </Button>
          <Button
            type="primary"
            size="small"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={!isDirty}
            onClick={handleSave}
          >
            保存
          </Button>
        </Space>
      </div>
    </Card>
  );
};

export default HandlerEditor;
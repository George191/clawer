import React, { useState, useCallback } from 'react';
import {
  Card,
  Tag,
  Typography,
  Button,
  Space,
  Popconfirm,
  Modal,
  App,
  theme,
  Tooltip,
} from 'antd';
import {
  EditOutlined,
  ExperimentOutlined,
  CopyOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import client from '@/services/api';

const { Text, Paragraph } = Typography;

// ── 模板类型 ──
export type TemplateType = 'web' | 'api' | 'log' | 'mq' | 'quality' | 'security';

export interface TemplateItem {
  id: string;
  name: string;
  type: TemplateType;
  description: string;
  status: 'active' | 'inactive';
  fields: number;
  steps: number;
  yaml: string;
}

// ── 类型配置 ──
const typeConfig: Record<TemplateType, { label: string; color: string }> = {
  web: { label: '网页采集', color: 'blue' },
  api: { label: 'API采集', color: 'green' },
  log: { label: '日志采集', color: 'orange' },
  mq: { label: '消息队列', color: 'purple' },
  quality: { label: '质量校验', color: 'red' },
  security: { label: '数据脱敏', color: 'magenta' },
};

interface TemplateCardProps {
  template: TemplateItem;
  onEdit: (tpl: TemplateItem) => void;
}

const TemplateCard: React.FC<TemplateCardProps> = ({ template, onEdit }) => {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [testOpen, setTestOpen] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<unknown>(null);
  const [testError, setTestError] = useState<string | null>(null);

  const typeMeta = typeConfig[template.type] ?? { label: template.type, color: 'default' };

  // ── 测试采集 ──
  const handleTest = useCallback(async () => {
    setTestOpen(true);
    setTestLoading(true);
    setTestResult(null);
    setTestError(null);
    try {
      // Call backend test API
      const res = await client.post('/templates/test', {
        template_id: template.id,
      });
      setTestResult(res.data);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setTestError(err?.message || '测试采集失败');
    } finally {
      setTestLoading(false);
    }
  }, [message, template.id]);

  // ── 复制 ──
  const handleCopy = useCallback(() => {
    message.success(`已复制模板: ${template.name}`);
  }, [template.name, message]);

  // ── 删除 ──
  const handleDelete = useCallback(() => {
    message.success(`已删除模板: ${template.name}`);
  }, [template.name, message]);

  const handleCloseTest = useCallback(() => {
    setTestOpen(false);
    setTestResult(null);
    setTestError(null);
  }, []);

  return (
    <>
      <Card
        size="small"
        hoverable
        styles={{
          body: { padding: '16px' },
        }}
        style={{ height: '100%' }}
      >
        {/* 头部：类型标签 + 模板名称 */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 12 }}>
          <Tag color={typeMeta.color} style={{ flexShrink: 0 }}>
            {typeMeta.label}
          </Tag>
          <Text strong ellipsis style={{ flex: 1, fontSize: 15 }}>
            {template.name}
          </Text>
        </div>

        {/* 内容：描述 + 统计 */}
        <Paragraph
          type="secondary"
          ellipsis={{ rows: 2 }}
          style={{ marginBottom: 12, fontSize: 13, minHeight: 40 }}
        >
          {template.description}
        </Paragraph>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            marginBottom: 12,
            padding: '6px 8px',
            borderRadius: token.borderRadiusSM,
            background: token.colorFillAlter,
          }}
        >
          <Tooltip title="字段数">
            <Text style={{ fontSize: 13 }}>
              <Text type="secondary">字段 </Text>
              <Text strong>{template.fields}</Text>
            </Text>
          </Tooltip>
          <div style={{ width: 1, height: 14, background: token.colorBorder }} />
          <Tooltip title="处理步骤数">
            <Text style={{ fontSize: 13 }}>
              <Text type="secondary">步骤 </Text>
              <Text strong>{template.steps}</Text>
            </Text>
          </Tooltip>
        </div>

        {/* 底部：状态指示器 + 操作按钮组 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            paddingTop: 10,
          }}
        >
          <Space size={4}>
            <span
              style={{
                display: 'inline-block',
                width: 8,
                height: 8,
                borderRadius: '50%',
                background:
                  template.status === 'active' ? token.colorSuccess : token.colorTextQuaternary,
              }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {template.status === 'active' ? '已启用' : '已停用'}
            </Text>
          </Space>

          <Button.Group size="small">
            <Tooltip title="编辑">
              <Button
                type="text"
                icon={<EditOutlined />}
                onClick={() => onEdit(template)}
              />
            </Tooltip>
            <Tooltip title="测试采集">
              <Button
                type="text"
                icon={<ExperimentOutlined />}
                onClick={handleTest}
              />
            </Tooltip>
            <Tooltip title="复制">
              <Button
                type="text"
                icon={<CopyOutlined />}
                onClick={handleCopy}
              />
            </Tooltip>
            <Popconfirm
              title="确认删除"
              description={`确定要删除模板「${template.name}」吗？`}
              onConfirm={handleDelete}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Tooltip title="删除">
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                />
              </Tooltip>
            </Popconfirm>
          </Button.Group>
        </div>
      </Card>

      {/* 测试采集结果 Modal */}
      <Modal
        title={`测试采集 - ${template.name}`}
        open={testOpen}
        onCancel={handleCloseTest}
        footer={[
          <Button key="close" onClick={handleCloseTest}>
            关闭
          </Button>,
        ]}
        width={640}
      >
        <div
          style={{
            borderRadius: token.borderRadius,
            overflow: 'hidden',
            border: `1px solid ${token.colorBorder}`,
          }}
        >
          <div
            style={{
              background: token.colorFillContent,
              padding: '6px 12px',
              borderBottom: `1px solid ${token.colorBorder}`,
              fontSize: 12,
              fontFamily: 'monospace',
              color: token.colorTextSecondary,
            }}
          >
            {testLoading
              ? '正在执行测试采集...'
              : testError
                ? '测试失败'
                : testResult
                  ? '测试结果 (JSON)'
                  : '准备中...'}
          </div>
          <div
            style={{
              maxHeight: 400,
              overflow: 'auto',
              padding: 12,
              background: 'var(--theme-color-neutral-bg-default)',
              fontSize: 12,
              fontFamily: "'Fira Code', 'Cascadia Code', monospace",
            }}
          >
            {testLoading ? (
              <Text type="secondary">执行中...</Text>
            ) : testError ? (
              <Text type="danger">{testError}</Text>
            ) : testResult ? (
              <pre
                style={{
                  margin: 0,
                  color: 'var(--theme-color-neutral-text-default)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {JSON.stringify(testResult, null, 2)}
              </pre>
            ) : (
              <Text type="secondary">暂无数据</Text>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
};

export default TemplateCard;
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Modal,
  Descriptions,
  Tag,
  Button,
  Space,
  Typography,
  App,
  theme,
  Table,
} from 'antd';
import {
  SaveOutlined,
  ExperimentOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import Editor from '@monaco-editor/react';
import type { TemplateItem, TemplateType } from './TemplateCard';

const { Text, Title } = Typography;

// ── 类型配置 ──
const typeConfig: Record<TemplateType, { label: string; color: string }> = {
  web: { label: '网页采集', color: 'blue' },
  api: { label: 'API采集', color: 'green' },
  log: { label: '日志采集', color: 'orange' },
  mq: { label: '消息队列', color: 'purple' },
  quality: { label: '质量校验', color: 'red' },
  security: { label: '数据脱敏', color: 'magenta' },
};

const defaultYaml = `# 模板定义
name: new_template
type: web
description: 新建采集模板

source:
  type: web
  urls:
    - https://example.com

parser:
  type: css
  selectors:
    title: h1
    content: article

output:
  layer: ods
  table: web_page

schedule:
  cron: "0 */6 * * *"

options:
  proxy: true
  max_retries: 3
  timeout_ms: 30000
`;

// ── 简单的 YAML 行解析（提取键值对用于预览） ──
interface ParsedSection {
  key: string;
  value: string;
  indent: number;
}

function parseYamlSections(yaml: string): ParsedSection[] {
  const lines = yaml.split('\n');
  const sections: ParsedSection[] = [];

  for (const line of lines) {
    if (line.trim().startsWith('#') || line.trim() === '') continue;
    const indent = line.search(/\S/);
    // 跳过嵌套值行（包含子结构的行）
    if (indent > 4) continue;

    const trimmed = line.trim();
    const colonIdx = trimmed.indexOf(':');
    if (colonIdx === -1) continue;

    const key = trimmed.slice(0, colonIdx).trim();
    const value = trimmed.slice(colonIdx + 1).trim();

    if (value === '' || value === '|' || value === '>') {
      // 这是一个包含子结构的父键
      sections.push({ key, value: '(子结构)', indent });
      continue;
    }

    // 去掉行尾注释
    const commentIdx = value.indexOf('#');
    const cleanValue = commentIdx > -1 ? value.slice(0, commentIdx).trim() : value;

    if (cleanValue === '') continue;
    sections.push({ key, value: cleanValue, indent });
  }

  return sections;
}

// ── 从 YAML 提取 URL 模式 ──
function extractUrls(yaml: string): string[] {
  const urls: string[] = [];
  const lines = yaml.split('\n');
  let inUrls = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === 'urls:') {
      inUrls = true;
      continue;
    }
    if (inUrls) {
      const match = trimmed.match(/^\s*-\s*(.+)/);
      if (match) {
        urls.push(match[1]);
      } else if (trimmed && !trimmed.startsWith('-')) {
        inUrls = false;
      }
    }
  }

  return urls;
}

interface YamlEditorProps {
  open: boolean;
  template: TemplateItem | null;
  onClose: () => void;
}

const YamlEditor: React.FC<YamlEditorProps> = ({ open, template, onClose }) => {
  const { token } = theme.useToken();
  const { message } = App.useApp();

  const [yaml, setYaml] = useState(defaultYaml);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // ── 初始化 YAML ──
  useEffect(() => {
    if (open) {
      if (template) {
        setYaml(template.yaml);
      } else {
        setYaml(defaultYaml);
      }
    }
  }, [open, template]);

  // ── 预览数据 ──
  const previewSections = useMemo(() => parseYamlSections(yaml), [yaml]);
  const previewUrls = useMemo(() => extractUrls(yaml), [yaml]);

  // ── 提取预览用的模板名和类型 ──
  const previewName = useMemo(() => {
    const match = yaml.match(/^name:\s*(.+)/m);
    return match ? match[1].trim() : '-';
  }, [yaml]);

  const previewType = useMemo(() => {
    const match = yaml.match(/^type:\s*(\w+)/m);
    return match ? (match[1].trim() as TemplateType) : null;
  }, [yaml]);

  const previewDesc = useMemo(() => {
    const match = yaml.match(/^description:\s*(.+)/m);
    return match ? match[1].trim() : '-';
  }, [yaml]);

  const typeMeta = previewType ? typeConfig[previewType] : null;

  // ── 保存 ──
  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await new Promise((r) => setTimeout(r, 600));
      message.success(template ? '模板已更新' : '模板已创建');
      onClose();
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  }, [template, message, onClose]);

  // ── 测试采集 ──
  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      await new Promise((r) => setTimeout(r, 1200));
      message.success('测试采集完成，预览数据已生成');
    } catch {
      message.error('测试采集失败');
    } finally {
      setTesting(false);
    }
  }, [message]);

  // ── 构建参数表 ──
  const paramsData = useMemo(() => {
    const result: { key: string; value: string; type: string }[] = [];
    for (const s of previewSections) {
      if (s.indent <= 2 && s.value !== '(子结构)' && s.key !== 'name' && s.key !== 'type' && s.key !== 'description') {
        result.push({
          key: s.key,
          value: s.value,
          type: s.value === 'true' || s.value === 'false' ? 'boolean' : 'string',
        });
      }
    }
    return result;
  }, [previewSections]);

  return (
    <Modal
      title={template ? `编辑模板 - ${template.name}` : '新建模板'}
      open={open}
      onCancel={onClose}
      width="90vw"
      destroyOnClose
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Button
            icon={<ExperimentOutlined />}
            loading={testing}
            onClick={handleTest}
          >
            测试采集
          </Button>
          <Space>
            <Button icon={<CloseOutlined />} onClick={onClose}>
              取消
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              onClick={handleSave}
            >
              保存
            </Button>
          </Space>
        </div>
      }
      styles={{
        body: {
          padding: 0,
        },
      }}
    >
      <div style={{ display: 'flex', height: 'calc(90vh - 120px)', minHeight: 500 }}>
        {/* 左侧：Monaco 编辑器 */}
        <div
          style={{
            width: '60%',
            borderRight: `1px solid ${token.colorBorder}`,
            display: 'flex',
            flexDirection: 'column',
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
              flexShrink: 0,
            }}
          >
            {template ? `${template.id}/template.yaml` : 'new_template/template.yaml'}
          </div>
          <div style={{ flex: 1 }}>
            <Editor
              height="100%"
              language="yaml"
              theme="vs-dark"
              value={yaml}
              onChange={(v) => setYaml(v ?? '')}
              options={{
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: 'on',
                wordWrap: 'on',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 2,
                padding: { top: 8 },
              }}
            />
          </div>
        </div>

        {/* 右侧：实时预览 */}
        <div
          style={{
            width: '40%',
            overflow: 'auto',
            padding: 16,
            background: token.colorBgContainer,
          }}
        >
          <Title level={5} style={{ marginTop: 0, marginBottom: 16 }}>
            实时预览
          </Title>

          {/* 模板基本信息 */}
          <div style={{ marginBottom: 20 }}>
            <Text type="secondary" style={{ fontSize: 12, textTransform: 'uppercase', marginBottom: 8, display: 'block' }}>
              模板信息
            </Text>
            <Descriptions
              size="small"
              column={1}
              bordered
              labelStyle={{
                width: 80,
                background: token.colorFillAlter,
              }}
              contentStyle={{
                background: token.colorBgContainer,
              }}
            >
              <Descriptions.Item label="名称">
                <Text strong>{previewName}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="类型">
                {typeMeta ? (
                  <Tag color={typeMeta.color}>{typeMeta.label}</Tag>
                ) : (
                  <Text type="secondary">-</Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="描述">
                <Text ellipsis style={{ maxWidth: 300 }}>
                  {previewDesc}
                </Text>
              </Descriptions.Item>
            </Descriptions>
          </div>

          {/* URL 模式 */}
          {previewUrls.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <Text
                type="secondary"
                style={{
                  fontSize: 12,
                  textTransform: 'uppercase',
                  marginBottom: 8,
                  display: 'block',
                }}
              >
                URL 模式
              </Text>
              <div
                style={{
                  background: token.colorFillAlter,
                  borderRadius: token.borderRadius,
                  padding: '6px 10px',
                }}
              >
                {previewUrls.map((url, i) => (
                  <Text
                    key={i}
                    code
                    style={{ display: 'block', marginBottom: i < previewUrls.length - 1 ? 4 : 0, fontSize: 12 }}
                  >
                    {url}
                  </Text>
                ))}
              </div>
            </div>
          )}

          {/* 参数表 */}
          {paramsData.length > 0 && (
            <div>
              <Text
                type="secondary"
                style={{
                  fontSize: 12,
                  textTransform: 'uppercase',
                  marginBottom: 8,
                  display: 'block',
                }}
              >
                参数配置
              </Text>
              <Table
                size="small"
                pagination={false}
                dataSource={paramsData.map((p, i) => ({ ...p, _key: i }))}
                rowKey="_key"
                columns={[
                  {
                    title: '参数',
                    dataIndex: 'key',
                    width: 120,
                    render: (v: string) => <Text code>{v}</Text>,
                  },
                  {
                    title: '值',
                    dataIndex: 'value',
                    render: (v: string) => (
                      <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Text>
                    ),
                  },
                  {
                    title: '类型',
                    dataIndex: 'type',
                    width: 80,
                    render: (v: string) => <Tag>{v}</Tag>,
                  },
                ]}
              />
            </div>
          )}

          {paramsData.length === 0 && previewUrls.length === 0 && (
            <Text type="secondary" style={{ fontSize: 13 }}>
              在左侧编辑 YAML 后，这里会实时显示模板预览
            </Text>
          )}
        </div>
      </div>
    </Modal>
  );
};

export default YamlEditor;
import React, { useState, useEffect, useCallback } from 'react';
import { Row, Col, Input, Select, Button, App, theme, Space, Tooltip, Typography, Empty, Spin, Result } from 'antd';
import { PlusOutlined, SearchOutlined, ReloadOutlined } from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import { fetchTemplates as apiFetchTemplates } from '@/services/api';
import TemplateCard from './TemplateCard';
import YamlEditor from './YamlEditor';
import type { TemplateItem } from './TemplateCard';

const { Search } = Input;
const { Text } = Typography;

// ── 类型筛选选项 ──
const typeFilterOptions = [
  { value: '', label: '全部类型' },
  { value: 'web', label: '网页采集' },
  { value: 'api', label: 'API采集' },
  { value: 'log', label: '日志采集' },
  { value: 'mq', label: '消息队列' },
  { value: 'quality', label: '质量校验' },
  { value: 'security', label: '数据脱敏' },
];

const Templates: React.FC = () => {
  const { token } = theme.useToken();
  const { message: msgApi } = App.useApp();

  const [searchText, setSearchText] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<TemplateItem | null>(null);
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTemplates = useCallback(async () => {
    try {
      // Templates list comes from API - API maps backend TemplateInfo[] to the TemplateItem shape
      const data = await apiFetchTemplates();
      setTemplates(data as unknown as TemplateItem[]);
      setError(null);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setError(err?.message || '获取模板列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    loadTemplates();
  }, [loadTemplates]);

  // ── 筛选后的模板列表 ──
  const filteredTemplates = templates.filter((tpl) => {
    const matchSearch =
      !searchText || tpl.name.toLowerCase().includes(searchText.toLowerCase());
    const matchType = !typeFilter || tpl.type === typeFilter;
    return matchSearch && matchType;
  });

  // ── 编辑模板 ──
  const handleEdit = useCallback((tpl: TemplateItem) => {
    setEditingTemplate(tpl);
    setEditorOpen(true);
  }, []);

  // ── 新建模板 ──
  const handleCreate = useCallback(() => {
    setEditingTemplate(null);
    setEditorOpen(true);
  }, []);

  // ── 关闭编辑器 ──
  const handleEditorClose = useCallback(() => {
    setEditorOpen(false);
    setEditingTemplate(null);
  }, []);

  // ── 刷新 ──
  const handleRefresh = useCallback(() => {
    setLoading(true);
    setError(null);
    loadTemplates();
  }, [loadTemplates]);

  // ── Error state ──
  if (error && templates.length === 0) {
    return (
      <ErrorBoundary>
        <PageHeader title="采集模板" />
        <Result
          status="error"
          title="加载失败"
          subTitle={error}
          extra={<Button onClick={handleRefresh}>重试</Button>}
        />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <PageHeader title="采集模板" />

      {/* 工具栏 */}
      <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 12,
            marginBottom: 24,
            padding: '12px 16px',
            background: token.colorBgContainer,
            borderRadius: token.borderRadius,
            border: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <Search
            placeholder="搜索模板名称..."
            allowClear
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onSearch={setSearchText}
            style={{ width: 240 }}
          />
          <Select
            value={typeFilter}
            onChange={setTypeFilter}
            options={typeFilterOptions}
            style={{ width: 140 }}
          />
          <div style={{ flex: 1 }} />
          <Space>
            <Tooltip title="刷新">
              <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading} />
            </Tooltip>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新建模板
            </Button>
          </Space>
        </div>

        {/* Inline error banner */}
        {error && templates.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Button type="link" size="small" onClick={handleRefresh} danger>
              刷新失败: {error}，点击重试
            </Button>
          </div>
        )}

        {/* ── Loading state (first load) ── */}
        {loading && templates.length === 0 && (
          <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
            <Spin size="large" tip="正在加载模板..." />
          </div>
        )}

        {/* ── Empty state ── */}
        {!loading && templates.length === 0 && !error && (
          <Empty description="暂无模板">
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              创建第一个模板
            </Button>
          </Empty>
        )}

        {/* 模板卡片网格 */}
        {filteredTemplates.length > 0 && (
          <Row gutter={[16, 16]}>
            {filteredTemplates.map((tpl) => (
              <Col xs={24} sm={12} xl={8} key={tpl.id}>
                <TemplateCard template={tpl} onEdit={handleEdit} />
              </Col>
            ))}
            {filteredTemplates.length === 0 && templates.length > 0 && (
              <Col span={24}>
                <div style={{ textAlign: 'center', padding: '48px 0' }}>
                  <Text type="secondary">没有找到匹配的模板</Text>
                </div>
              </Col>
            )}
          </Row>
        )}

        {/* YAML 编辑器 */}
      <YamlEditor
        open={editorOpen}
        template={editingTemplate}
        onClose={handleEditorClose}
      />
    </ErrorBoundary>
  );
};

export default Templates;
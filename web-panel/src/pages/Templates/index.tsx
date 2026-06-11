import React, { useMemo, useState } from 'react';
import { Button, Input, Segmented, Space, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudDownloadOutlined,
  CodeOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  MoreOutlined,
  PlusOutlined,
  SearchOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import ErrorBoundary from '@/components/ErrorBoundary';

const { Text } = Typography;

type TemplateStatus = 'active' | 'draft' | 'deprecated';
type StatusFilter = 'all' | TemplateStatus;
type TemplateIcon = 'cloud' | 'tool' | 'api' | 'code' | 'search' | 'branch';

interface TemplateAsset {
  key: string;
  name: string;
  title: string;
  domain: string;
  adapter: string;
  version: string;
  status: TemplateStatus;
  fields: number;
  quality: number;
  lastRun: string;
  owner: string;
  description: string;
  action: string;
  icon: TemplateIcon;
}

const aura = {
  bg: '#15191F',
  surface: '#1F2429',
  surfaceSoft: '#191E24',
  border: '#3A414B',
  borderSoft: '#2B333D',
  text: '#F3F4F6',
  muted: '#C8CDD4',
  subtle: '#8C95A1',
  cyan: '#88EEF4',
  purple: '#8B5CF6',
  green: '#31D26B',
  amber: '#FBBF24',
};

const assets: TemplateAsset[] = [
  {
    key: '1',
    name: 'google_patent_contract',
    title: 'Google Patent Contract',
    domain: 'patents.google.com',
    adapter: 'browser-agent',
    version: 'v1.8',
    status: 'active',
    fields: 18,
    quality: 94,
    lastRun: '8 分钟前',
    owner: 'AI Collect',
    description: '沉淀专利列表、详情页、附件与翻页路径，支持字段漂移检测和增量同步。',
    action: '打开模板',
    icon: 'cloud',
  },
  {
    key: '2',
    name: 'sealagom_navwarn_contract',
    title: 'Navwarn Sync Template',
    domain: 'navigation warning',
    adapter: 'http-parser',
    version: 'v2.1',
    status: 'active',
    fields: 12,
    quality: 98,
    lastRun: '16 分钟前',
    owner: 'Crawler Team',
    description: '面向航警数据的稳定采集模板，内置区域编码、重复合并和低延迟入湖策略。',
    action: '查看运行',
    icon: 'api',
  },
  {
    key: '3',
    name: 'zdopen_notice_contract',
    title: 'Gov Notice Template',
    domain: '政务公告',
    adapter: 'browser-render',
    version: 'v0.9',
    status: 'draft',
    fields: 15,
    quality: 76,
    lastRun: '1 小时前',
    owner: 'Data Ops',
    description: '用于公告列表和详情页还原，当前处于草稿验证阶段，等待验证码风险策略确认。',
    action: '继续调试',
    icon: 'tool',
  },
  {
    key: '4',
    name: 'pdf_document_extract',
    title: 'PDF Extract Template',
    domain: 'PDF 附件',
    adapter: 'doc-parser',
    version: 'v1.3',
    status: 'active',
    fields: 9,
    quality: 91,
    lastRun: '32 分钟前',
    owner: 'ETL Team',
    description: '专门处理附件下载、文本抽取、对象存储归档和结构化字段回填。',
    action: '试跑模板',
    icon: 'search',
  },
  {
    key: '5',
    name: 'quality_missing_scan',
    title: 'Quality Gate Template',
    domain: '质量校验',
    adapter: 'quality-agent',
    version: 'v1.1',
    status: 'draft',
    fields: 7,
    quality: 88,
    lastRun: '47 分钟前',
    owner: 'Data Ops',
    description: '集中维护字段缺失率、重复记录、字段漂移和发布门禁，供任务发布前复用。',
    action: '配置门禁',
    icon: 'branch',
  },
  {
    key: '6',
    name: 'legacy_notice_parser',
    title: 'Legacy Notice Parser',
    domain: '历史公告',
    adapter: 'legacy-parser',
    version: 'v0.6',
    status: 'deprecated',
    fields: 11,
    quality: 63,
    lastRun: '7 天前',
    owner: 'Crawler Team',
    description: '旧版解析器模板，仅保留回滚和历史任务兼容，不建议新任务继续引用。',
    action: '查看归档',
    icon: 'code',
  },
];

const iconMap: Record<TemplateIcon, React.ReactNode> = {
  cloud: <CloudDownloadOutlined />,
  tool: <ToolOutlined />,
  api: <ApiOutlined />,
  code: <CodeOutlined />,
  search: <FileSearchOutlined />,
  branch: <BranchesOutlined />,
};

const statusMeta: Record<TemplateStatus, { label: string; className: string; icon: React.ReactNode }> = {
  active: { label: '已启用', className: 'is-active', icon: <CheckCircleOutlined /> },
  draft: { label: '草稿', className: 'is-draft', icon: <ClockCircleOutlined /> },
  deprecated: { label: '已停用', className: 'is-deprecated', icon: <StopOutlined /> },
};

const Templates: React.FC = () => {
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');

  const filtered = useMemo(() => (
    assets.filter((asset) => {
      const matchStatus = status === 'all' || asset.status === status;
      const matchKeyword = !keyword || `${asset.name} ${asset.title} ${asset.domain} ${asset.adapter}`.toLowerCase().includes(keyword.toLowerCase());
      return matchStatus && matchKeyword;
    })
  ), [keyword, status]);

  return (
    <ErrorBoundary>
      <style>
        {`
          .template-library {
            height: calc(100vh - 100px);
            max-height: calc(100vh - 100px);
            overflow: hidden;
            border-radius: 8px;
            border: 1px solid ${aura.borderSoft};
            background: ${aura.bg};
            color: ${aura.text};
            display: flex;
            flex-direction: column;
          }
          .template-library,
          .template-library * {
            scrollbar-width: none;
          }
          .template-library *::-webkit-scrollbar {
            display: none;
          }
          .template-header {
            flex-shrink: 0;
            padding: 18px 22px 16px;
            border-bottom: 1px solid ${aura.borderSoft};
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
          }
          .template-heading {
            min-width: 0;
          }
          .template-heading h1 {
            margin: 0;
            color: ${aura.text};
            font-size: 28px;
            line-height: 1.2;
            font-weight: 760;
          }
          .template-heading p {
            margin: 8px 0 0;
            color: ${aura.subtle};
            font-size: 13px;
          }
          .template-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
          }
          .template-library .ant-input-affix-wrapper,
          .template-library .ant-btn {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.text};
            box-shadow: none;
          }
          .template-library .ant-input {
            background: transparent;
            color: ${aura.text};
          }
          .template-library .ant-input::placeholder {
            color: ${aura.subtle};
          }
          .template-library .ant-segmented {
            background: ${aura.surfaceSoft};
            border: 1px solid ${aura.borderSoft};
            padding: 2px;
          }
          .template-library .ant-segmented-item {
            color: ${aura.subtle};
            border-radius: 6px;
          }
          .template-library .ant-segmented-item-selected {
            background: ${aura.surface};
            color: ${aura.text};
            box-shadow: inset 0 0 0 1px ${aura.border};
          }
          .template-body {
            flex: 1;
            min-height: 0;
            overflow: auto;
            padding: 28px 30px 34px;
          }
          .template-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(280px, 1fr));
            gap: 22px;
          }
          .template-card {
            min-height: 304px;
            border: 1px solid ${aura.border};
            border-radius: 8px;
            background: ${aura.surface};
            padding: 30px 32px 28px;
            display: flex;
            flex-direction: column;
            transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
          }
          .template-card:hover {
            border-color: ${aura.cyan};
            background: #22282E;
            transform: translateY(-1px);
          }
          .template-icon {
            color: ${aura.text};
            font-size: 32px;
            line-height: 1;
            margin-bottom: 36px;
          }
          .template-title-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
          }
          .template-title-row h2 {
            margin: 0;
            color: ${aura.text};
            font-size: 24px;
            line-height: 1.22;
            font-weight: 760;
            overflow-wrap: anywhere;
          }
          .template-card p {
            color: ${aura.muted};
            font-size: 16px;
            line-height: 1.55;
            margin: 22px 0 0;
          }
          .template-meta {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
            margin-top: 22px;
          }
          .template-meta-item {
            border: 1px solid ${aura.borderSoft};
            border-radius: 6px;
            padding: 8px 10px;
            min-width: 0;
          }
          .template-meta-label {
            display: block;
            color: ${aura.subtle};
            font-size: 11px;
            margin-bottom: 4px;
          }
          .template-meta-value {
            display: block;
            color: ${aura.text};
            font-size: 13px;
            font-weight: 700;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .template-card-footer {
            margin-top: auto;
            padding-top: 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
          }
          .template-link {
            color: ${aura.cyan};
            font-size: 18px;
            font-weight: 500;
            text-decoration: underline;
            text-underline-offset: 4px;
            cursor: pointer;
            border: none;
            padding: 0;
            background: transparent;
          }
          .template-status {
            height: 28px;
            padding: 0 10px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border-radius: 16px;
            border: 1px solid ${aura.border};
            color: ${aura.subtle};
            background: transparent;
            white-space: nowrap;
          }
          .template-status.is-active {
            color: ${aura.green};
          }
          .template-status.is-draft {
            color: ${aura.amber};
          }
          .template-status.is-deprecated {
            color: ${aura.subtle};
          }
          .template-quality {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 20px;
          }
          .template-quality-track {
            flex: 1;
            height: 8px;
            border-radius: 999px;
            background: #323947;
            overflow: hidden;
          }
          .template-quality-track span {
            display: block;
            height: 100%;
            border-radius: inherit;
            background: ${aura.purple};
          }
          .template-empty {
            min-height: 260px;
            border: 1px dashed ${aura.border};
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: ${aura.subtle};
          }
          .template-card .ant-tag {
            background: transparent;
            border-color: ${aura.border};
            color: ${aura.subtle};
            margin: 0;
          }
          @media (max-width: 1280px) {
            .template-grid {
              grid-template-columns: repeat(2, minmax(280px, 1fr));
            }
          }
          @media (max-width: 860px) {
            .template-header {
              align-items: stretch;
              flex-direction: column;
            }
            .template-actions {
              justify-content: flex-start;
            }
            .template-actions .ant-input-affix-wrapper {
              width: 100% !important;
            }
            .template-grid {
              grid-template-columns: 1fr;
            }
            .template-body {
              padding: 18px;
            }
          }
        `}
      </style>

      <div className="template-library">
        <header className="template-header">
          <div className="template-heading">
            <h1>模板与适配器库</h1>
            <p>管理可发布模板、运行适配器、字段合约、版本状态和最近试跑质量。</p>
          </div>
          <div className="template-actions">
            <Input
              prefix={<SearchOutlined />}
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索模板 / 域名 / 适配器"
              style={{ width: 260 }}
            />
            <Segmented
              value={status}
              onChange={(value) => setStatus(value as StatusFilter)}
              options={[
                { label: '全部', value: 'all' },
                { label: '启用', value: 'active' },
                { label: '草稿', value: 'draft' },
                { label: '停用', value: 'deprecated' },
              ]}
            />
            <Button icon={<ExperimentOutlined />}>批量试跑</Button>
            <Button type="primary" icon={<PlusOutlined />}>新建模板</Button>
          </div>
        </header>

        <main className="template-body">
          {filtered.length > 0 ? (
            <div className="template-grid">
              {filtered.map((asset) => {
                const statusInfo = statusMeta[asset.status];

                return (
                  <article className="template-card" key={asset.key}>
                    <div className="template-icon">{iconMap[asset.icon]}</div>

                    <div className="template-title-row">
                      <h2>{asset.title}</h2>
                      <Button type="text" icon={<MoreOutlined />} style={{ color: aura.subtle }} />
                    </div>

                    <p>{asset.description}</p>

                    <div className="template-meta">
                      <div className="template-meta-item">
                        <span className="template-meta-label">版本</span>
                        <span className="template-meta-value">{asset.version}</span>
                      </div>
                      <div className="template-meta-item">
                        <span className="template-meta-label">字段</span>
                        <span className="template-meta-value">{asset.fields}</span>
                      </div>
                      <div className="template-meta-item">
                        <span className="template-meta-label">适配器</span>
                        <span className="template-meta-value">{asset.adapter}</span>
                      </div>
                    </div>

                    <div className="template-quality">
                      <div className="template-quality-track"><span style={{ width: `${asset.quality}%` }} /></div>
                      <Text style={{ color: aura.subtle, width: 44, textAlign: 'right' }}>{asset.quality}%</Text>
                    </div>

                    <div className="template-card-footer">
                      <button className="template-link" type="button">{asset.action} ↗</button>
                      <Space size={8}>
                        <span className={`template-status ${statusInfo.className}`}>{statusInfo.icon}{statusInfo.label}</span>
                        <Tag>{asset.lastRun}</Tag>
                      </Space>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="template-empty">未找到匹配的模板</div>
          )}
        </main>
      </div>
    </ErrorBoundary>
  );
};

export default Templates;

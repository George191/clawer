import React, { useMemo, useState } from 'react';
import { Button, Segmented, Space, Tag, Typography } from 'antd';
import {
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudDownloadOutlined,
  CodeOutlined,
  ApiOutlined,
  FileSearchOutlined,
  MoreOutlined,
  PlusOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { aiAura } from './shared/aura';
import { templates } from './shared/mockData';
import type { TemplateAsset, TemplateStatus, TemplateIcon } from './shared/types';

const { Text } = Typography;

type StatusFilter = 'all' | TemplateStatus;

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

interface Props {
  keyword: string;
}

const TemplateView: React.FC<Props> = ({ keyword }) => {
  const [status, setStatus] = useState<StatusFilter>('all');

  const filtered = useMemo(() => templates.filter((t) => {
    const matchStatus = status === 'all' || t.status === status;
    const matchKeyword = !keyword ||
      `${t.name} ${t.title} ${t.domain} ${t.adapter}`.toLowerCase().includes(keyword.toLowerCase());
    return matchStatus && matchKeyword;
  }), [keyword, status]);

  return (
    <>
      <style>{`
        .cc-tpl {
          flex: 1;
          min-height: 0;
          overflow: hidden;
          display: flex;
          flex-direction: column;
        }
        .cc-tpl-toolbar {
          flex-shrink: 0;
          padding: 12px 22px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border-bottom: 1px solid ${aiAura.borderSoft};
        }
        .cc-tpl-toolbar .ant-segmented {
          background: ${aiAura.surfaceSoft};
          border: 1px solid ${aiAura.borderSoft};
          padding: 2px;
        }
        .cc-tpl-toolbar .ant-segmented-item {
          color: ${aiAura.subtle};
          border-radius: 6px;
        }
        .cc-tpl-toolbar .ant-segmented-item-selected {
          background: ${aiAura.surface};
          color: ${aiAura.text};
          box-shadow: inset 0 0 0 1px ${aiAura.border};
        }
        .cc-tpl-body {
          flex: 1;
          min-height: 0;
          overflow: auto;
          padding: 24px 28px 30px;
        }
        .cc-tpl-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(280px, 1fr));
          gap: 20px;
        }
        .cc-tpl-card {
          min-height: 290px;
          border: 1px solid ${aiAura.border};
          border-radius: 8px;
          background: ${aiAura.surface};
          padding: 28px 30px 26px;
          display: flex;
          flex-direction: column;
          transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
        }
        .cc-tpl-card:hover {
          background: #202525;
        }
        .cc-tpl-icon {
          color: ${aiAura.text};
          font-size: 30px;
          line-height: 1;
          margin-bottom: 32px;
        }
        .cc-tpl-title-row {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
        }
        .cc-tpl-title-row h2 {
          margin: 0;
          color: ${aiAura.text};
          font-size: 22px;
          line-height: 1.22;
          font-weight: 760;
          overflow-wrap: anywhere;
        }
        .cc-tpl-card p {
          color: ${aiAura.muted};
          font-size: 15px;
          line-height: 1.55;
          margin: 20px 0 0;
        }
        .cc-tpl-meta {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
          margin-top: 20px;
        }
        .cc-tpl-meta-item {
          border: 1px solid ${aiAura.borderSoft};
          border-radius: 6px;
          padding: 7px 10px;
          min-width: 0;
        }
        .cc-tpl-meta-label {
          display: block;
          color: ${aiAura.subtle};
          font-size: 11px;
          margin-bottom: 3px;
        }
        .cc-tpl-meta-value {
          display: block;
          color: ${aiAura.text};
          font-size: 13px;
          font-weight: 700;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .cc-tpl-card-footer {
          margin-top: auto;
          padding-top: 24px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .cc-tpl-link {
          color: ${aiAura.accent};
          font-size: 16px;
          font-weight: 500;
          text-decoration: underline;
          text-underline-offset: 4px;
          cursor: pointer;
          border: none;
          padding: 0;
          background: transparent;
        }
        .cc-tpl-status {
          height: 26px;
          padding: 0 10px;
          display: inline-flex;
          align-items: center;
          gap: 5px;
          border-radius: 14px;
          border: 1px solid ${aiAura.border};
          color: ${aiAura.subtle};
          background: transparent;
          white-space: nowrap;
          font-size: 12px;
        }
        .cc-tpl-status.is-active { color: #31D26B; }
        .cc-tpl-status.is-draft { color: #FBBF24; }
        .cc-tpl-status.is-deprecated { color: ${aiAura.subtle}; }
        .cc-tpl-quality {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-top: 18px;
        }
        .cc-tpl-quality-track {
          flex: 1;
          height: 7px;
          border-radius: 999px;
          background: #2D3535;
          overflow: hidden;
        }
        .cc-tpl-quality-track span {
          display: block;
          height: 100%;
          border-radius: inherit;
          background: ${aiAura.accent};
        }
        .cc-tpl-empty {
          min-height: 240px;
          border: 1px dashed ${aiAura.border};
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: ${aiAura.subtle};
        }
        .cc-tpl-card .ant-tag {
          background: transparent;
          border-color: ${aiAura.border};
          color: ${aiAura.subtle};
          margin: 0;
        }
        @media (max-width: 1280px) {
          .cc-tpl-grid { grid-template-columns: repeat(2, minmax(280px, 1fr)); }
        }
        @media (max-width: 860px) {
          .cc-tpl-grid { grid-template-columns: 1fr; }
          .cc-tpl-body { padding: 16px; }
        }
      `}</style>

      <div className="cc-tpl">
        <div className="cc-tpl-toolbar">
          <Segmented
            value={status}
            onChange={(v) => setStatus(v as StatusFilter)}
            options={[
              { label: '全部', value: 'all' },
              { label: '启用', value: 'active' },
              { label: '草稿', value: 'draft' },
              { label: '停用', value: 'deprecated' },
            ]}
          />
          <Button icon={<PlusOutlined />} type="text" style={{ color: aiAura.subtle }}>
            新建模板
          </Button>
        </div>

        <div className="cc-tpl-body">
          {filtered.length > 0 ? (
            <div className="cc-tpl-grid">
              {filtered.map((asset: TemplateAsset) => {
                const si = statusMeta[asset.status];
                return (
                  <article className="cc-tpl-card" key={asset.key}>
                    <div className="cc-tpl-icon">{iconMap[asset.icon]}</div>

                    <div className="cc-tpl-title-row">
                      <h2>{asset.title}</h2>
                      <Button type="text" icon={<MoreOutlined />} style={{ color: aiAura.subtle }} />
                    </div>

                    <p>{asset.description}</p>

                    <div className="cc-tpl-meta">
                      <div className="cc-tpl-meta-item">
                        <span className="cc-tpl-meta-label">版本</span>
                        <span className="cc-tpl-meta-value">{asset.version}</span>
                      </div>
                      <div className="cc-tpl-meta-item">
                        <span className="cc-tpl-meta-label">字段</span>
                        <span className="cc-tpl-meta-value">{asset.fields}</span>
                      </div>
                      <div className="cc-tpl-meta-item">
                        <span className="cc-tpl-meta-label">关联任务</span>
                        <span className="cc-tpl-meta-value">{asset.taskCount}</span>
                      </div>
                    </div>

                    <div className="cc-tpl-quality">
                      <div className="cc-tpl-quality-track">
                        <span style={{ width: `${asset.quality}%` }} />
                      </div>
                      <Text style={{ color: aiAura.subtle, width: 40, textAlign: 'right' }}>
                        {asset.quality}%
                      </Text>
                    </div>

                    <div className="cc-tpl-card-footer">
                      <button className="cc-tpl-link" type="button">{asset.action} ↗</button>
                      <Space size={8}>
                        <span className={`cc-tpl-status ${si.className}`}>{si.icon}{si.label}</span>
                        <Tag>{asset.lastRun}</Tag>
                      </Space>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="cc-tpl-empty">未找到匹配的模板</div>
          )}
        </div>
      </div>
    </>
  );
};

export default TemplateView;

import React from 'react';
import { Typography, Breadcrumb, Divider, Space } from 'antd';
import { useLocation } from 'react-router-dom';
import { HomeOutlined } from '@ant-design/icons';

const { Title, Text } = Typography;

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
}

const routeLabels: Record<string, string> = {
  '/': '仪表盘',
  '/dashboard': '仪表盘',
  '/explorer': '数据探索',
  '/data': '数据探索',
  '/tasks': '任务中心',
  '/monitor': '采集监控',
  '/monitoring': '采集监控',
  '/pipeline': '管道管理',
  '/templates': '模板管理',
  '/settings': '系统设置',
  '/ai-collect': 'AI 采集',
};

function toBreadcrumbItems(pathname: string) {
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length === 0) {
    return [{ title: <><HomeOutlined /> 仪表盘</> }];
  }
  const items: { title: React.ReactNode; href?: string }[] = [
    { title: <><HomeOutlined /> 首页</> },
  ];
  let accumulated = '';
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    accumulated += '/' + seg;
    const label = i === 0 ? (routeLabels[accumulated] ?? seg) : seg;
    if (i === segments.length - 1) {
      items.push({ title: <span>{label}</span> });
    } else {
      items.push({ title: <a href={accumulated}>{label}</a> });
    }
  }
  return items;
}

const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, extra }) => {
  const { pathname } = useLocation();
  const breadcrumbItems = toBreadcrumbItems(pathname);

  return (
    <div style={{ marginBottom: 24 }}>
      <Breadcrumb
        items={breadcrumbItems}
        style={{ fontSize: 12, marginBottom: 6, color: '#94A3B8' }}
      />

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          paddingTop: 2,
        }}
      >
        <div>
          <Title
            level={3}
            style={{
              margin: 0,
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: '-0.02em',
            }}
          >
            {title}
          </Title>
          {subtitle && (
            <Text
              type="secondary"
              style={{
                fontSize: 13,
                display: 'block',
                marginTop: 4,
              }}
            >
              {subtitle}
            </Text>
          )}
        </div>
        {extra && <div style={{ flexShrink: 0 }}>{extra}</div>}
      </div>

      <Divider style={{ marginTop: 14, marginBottom: 0, borderColor: 'rgba(255, 255, 255, 0.06)' }} />
    </div>
  );
};

export default PageHeader;
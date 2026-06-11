import React from 'react';
import { Typography, Divider } from 'antd';

const { Title, Text } = Typography;

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
}

const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, extra }) => {
  return (
    <div style={{ marginBottom: 24 }}>
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
              letterSpacing: 0,
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

import React from 'react';
import { Card, Skeleton, Tooltip } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, MinusOutlined } from '@ant-design/icons';

interface DataCardProps {
  icon?: React.ReactNode;
  title: string;
  value: number | string;
  change?: number; // percentage, e.g. 12.5 or -8.3
  sparklineData?: number[];
  color?: string;
  loading?: boolean;
}

const DataCard: React.FC<DataCardProps> = ({
  icon,
  title,
  value,
  change,
  sparklineData,
  color,
  loading = false,
}) => {
  const trendDir = !change ? 0 : change > 0 ? 1 : -1;
  const trendColor =
    trendDir > 0 ? '#52c41a' : trendDir < 0 ? '#ff4d4f' : 'var(--color-text-tertiary, #8c8c8c)';

  if (loading) {
    return (
      <Card bodyStyle={{ padding: '20px 24px' }}>
        <Skeleton active paragraph={{ rows: 2 }} title={{ width: '40%' }} />
      </Card>
    );
  }

  return (
    <Card
      bodyStyle={{ padding: '20px 24px' }}
      style={{ height: '100%' }}
    >
      {/* Header row: icon + title */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
        {icon && (
          <span
            style={{
              fontSize: 18,
              color: color ?? 'var(--ant-color-primary, #1677ff)',
              marginRight: 10,
              display: 'flex',
              alignItems: 'center',
            }}
          >
            {icon}
          </span>
        )}
        <span
          style={{
            fontSize: 13,
            color: 'var(--ant-color-text-secondary, #8b949e)',
            fontWeight: 500,
          }}
        >
          {title}
        </span>
      </div>

      {/* Value */}
      <div
        style={{
          fontSize: 32,
          fontWeight: 700,
          lineHeight: 1.2,
          marginBottom: 8,
          color: 'var(--ant-color-text, #1d1d1d)',
        }}
      >
        {value}
      </div>

      {/* Trend row */}
      <div style={{ display: 'flex', alignItems: 'center', height: 22 }}>
        {trendDir !== 0 && (
          <Tooltip title={`较上周期 ${trendDir > 0 ? '上升' : '下降'} ${Math.abs(change!)}%`}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
              {trendDir > 0 ? (
                <ArrowUpOutlined style={{ color: trendColor, fontSize: 12 }} />
              ) : (
                <ArrowDownOutlined style={{ color: trendColor, fontSize: 12 }} />
              )}
              <span style={{ color: trendColor, fontSize: 13, fontWeight: 500 }}>
                {Math.abs(change!)}%
              </span>
            </span>
          </Tooltip>
        )}
        {trendDir === 0 && change !== undefined && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, color: trendColor, fontSize: 13 }}>
            <MinusOutlined style={{ fontSize: 12 }} />
            持平
          </span>
        )}

        {/* Mini sparkline */}
        {sparklineData && sparklineData.length > 1 && (
          <div style={{ marginLeft: 'auto', width: 80, height: 22 }}>
            <Sparkline
              data={sparklineData}
              color={trendColor}
              width={80}
              height={22}
            />
          </div>
        )}
      </div>
    </Card>
  );
};

// ── Inline Sparkline (SVG) ──

interface SparklineProps {
  data: number[];
  color: string;
  width: number;
  height: number;
}

const Sparkline: React.FC<SparklineProps> = ({ data, color, width, height }) => {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const padding = 2;

  const points = data
    .map((v, i) => {
      const x = padding + (i / (data.length - 1)) * (width - padding * 2);
      const y = height - padding - ((v - min) / range) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.7}
      />
    </svg>
  );
};

export default DataCard;

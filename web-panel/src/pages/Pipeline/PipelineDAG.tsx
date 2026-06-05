import React, { useState, useEffect, useMemo, useCallback } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Card, theme, Spin, Empty, Button, Result } from 'antd';
import { fetchLayers } from '@/services/api';
import type { LayerNode as ApiLayerNode } from '@/services/types';
import { layerColors } from '@/theme/tokens';

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer]);

// ── 节点状态类型 ──
type NodeStatus = 'running' | 'error' | 'stopped';

interface LayerNode {
  name: string;
  status: NodeStatus;
  throughput: string;
  x: number;
  y: number;
}

interface LayerLink {
  source: string;
  target: string;
}

interface PipelineDAGProps {
  onNodeClick: (layer: string) => void;
  selectedNode: string | null;
}

// ── Hardcoded layout (visual positions) ──
const LAYOUT: Record<string, { x: number; y: number; label: string }> = {
  crawl: { x: 400, y: 60, label: 'Crawl' },
  rds: { x: 400, y: 170, label: 'RDS' },
  ods: { x: 400, y: 280, label: 'ODS' },
  task: { x: 400, y: 390, label: 'TASK' },
  dwd: { x: 180, y: 500, label: 'DWD' },
  dws: { x: 620, y: 500, label: 'DWS' },
  ads: { x: 400, y: 610, label: 'ADS' },
};

const PIPELINE_LINKS: LayerLink[] = [
  { source: 'Crawl', target: 'RDS' },
  { source: 'RDS', target: 'ODS' },
  { source: 'ODS', target: 'TASK' },
  { source: 'TASK', target: 'DWD' },
  { source: 'TASK', target: 'DWS' },
  { source: 'DWD', target: 'ADS' },
  { source: 'DWS', target: 'ADS' },
];

// ── 状态色映射 ──
const statusColorMap: Record<NodeStatus, string> = {
  running: '#1677ff',
  error: '#ff4d4f',
  stopped: '#8c8c8c',
};

const statusLabelMap: Record<NodeStatus, string> = {
  running: '运行中',
  error: '异常',
  stopped: '已停止',
};

/** 将 hex 转 rgba */
const hexToRgba = (hex: string, alpha: number) => {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
};

/** 创建径向渐变对象（供 ECharts 序列化使用） */
const makeRadialGradient = (hex: string) => ({
  type: 'radial' as const,
  x: 0.5,
  y: 0.4,
  r: 0.5,
  colorStops: [
    { offset: 0, color: hexToRgba(hex, 0.95) },
    { offset: 0.5, color: hexToRgba(hex, 0.7) },
    { offset: 1, color: hexToRgba(hex, 0.15) },
  ],
});

function mapApiLayerToNode(apiLayer: ApiLayerNode): LayerNode {
  const layout = LAYOUT[apiLayer.key];
  return {
    name: layout?.label ?? apiLayer.label,
    status: apiLayer.status as NodeStatus,
    throughput: `${apiLayer.rate.toFixed(1)} msg/s`,
    x: layout?.x ?? 400,
    y: layout?.y ?? 300,
  };
}

const PipelineDAG: React.FC<PipelineDAGProps> = ({ onNodeClick, selectedNode }) => {
  const { token } = theme.useToken();
  const textColor = token.colorText;
  const textColorSecondary = token.colorTextSecondary;

  const [nodes, setNodes] = useState<LayerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchLayers()
      .then((apiLayers) => {
        const mapped = apiLayers.map(mapApiLayerToNode);
        setNodes(mapped);
        setLoading(false);
      })
      .catch((e: unknown) => {
        const err = e as { message?: string };
        setError(err?.message || '获取管道拓扑失败');
        setLoading(false);
      });
  }, []);

  const option = useMemo(() => {
    if (nodes.length === 0) return null;

    const nodeData = nodes.map((n) => {
      const isSelected = n.name === selectedNode;
      const color = statusColorMap[n.status];
      const size = isSelected ? 84 : 72;

      return {
        name: n.name,
        x: n.x,
        y: n.y,
        symbolSize: size,
        symbol: 'circle',
        itemStyle: {
          color: makeRadialGradient(color),
          borderColor: isSelected ? '#fff' : hexToRgba(color, 0.6),
          borderWidth: isSelected ? 3 : 2,
          shadowBlur: isSelected ? 30 : 15,
          shadowColor: hexToRgba(color, 0.5),
        },
        label: {
          show: true,
          position: 'bottom',
          distance: 10,
          formatter: [
            `{name|${n.name}}`,
            `{rate|${n.throughput}}`,
          ].join('\n'),
          rich: {
            name: {
              fontSize: 14,
              fontWeight: 'bold',
              color: textColor,
              padding: [0, 0, 2, 0],
            },
            rate: {
              fontSize: 11,
              color: textColorSecondary,
            },
          },
        },
        emphasis: {
          itemStyle: {
            borderColor: '#fff',
            borderWidth: 3,
            shadowBlur: 35,
            shadowColor: hexToRgba(color, 0.7),
          },
          label: {
            rich: {
              name: { fontSize: 16, fontWeight: 'bold', color: '#fff' },
              rate: { fontSize: 12, color: '#d9d9d9' },
            },
          },
        },
      };
    });

    const linkData = PIPELINE_LINKS.map((l) => {
      const sourceNode = nodes.find((n) => n.name === l.source);
      const linkColor = sourceNode
        ? hexToRgba(statusColorMap[sourceNode.status], 0.4)
        : token.colorBorder;

      return {
        source: l.source,
        target: l.target,
        lineStyle: {
          color: linkColor,
          width: 1.5,
          curveness: 0.3,
          opacity: 0.7,
        },
        emphasis: {
          lineStyle: {
            color: token.colorPrimary,
            width: 3,
            opacity: 1,
          },
        },
      };
    });

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item' as const,
        backgroundColor: token.colorBgElevated,
        borderColor: token.colorBorder,
        textStyle: { color: token.colorText },
        formatter: (params: { dataType?: string; name?: string; data?: Record<string, unknown> }) => {
          if (params.dataType === 'node' && params.name) {
            const node = nodes.find((n) => n.name === params.name);
            if (!node) return params.name;
            return [
              `<strong style="font-size:14px">${node.name}</strong>`,
              `<br/>状态: ${statusLabelMap[node.status]}`,
              `<br/>吞吐: ${node.throughput}`,
            ].join('');
          }
          return params.name ?? '';
        },
      },
      legend: {
        data: [
          { name: '运行中', itemStyle: { color: statusColorMap.running } },
          { name: '异常', itemStyle: { color: statusColorMap.error } },
          { name: '已停止', itemStyle: { color: statusColorMap.stopped } },
        ],
        top: 8,
        left: 8,
        orient: 'vertical',
        textStyle: { color: token.colorTextSecondary, fontSize: 12 },
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 8,
      },
      series: [
        {
          type: 'graph',
          layout: 'none',
          roam: true,
          draggable: true,
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: [0, 10],
          data: nodeData,
          links: linkData,
          emphasis: {
            focus: 'adjacency' as const,
            scale: 1.1,
          },
          animation: true,
          animationDuration: 800,
          animationEasing: 'cubicOut',
        },
      ],
    };
  }, [nodes, selectedNode, token, textColor, textColorSecondary]);

  const handleChartClick = useCallback(
    (params: { dataType?: string; name?: string }) => {
      if (params.dataType === 'node' && params.name) {
        onNodeClick(params.name);
      }
    },
    [onNodeClick],
  );

  // ── Error state ──
  if (error && nodes.length === 0) {
    return (
      <Card title="管道 DAG 拓扑" size="small" styles={{ body: { padding: 0 } }}>
        <div style={{ padding: 48 }}>
          <Result
            status="error"
            title="加载失败"
            subTitle={error}
            extra={
              <Button
                onClick={() => {
                  setError(null);
                  setLoading(true);
                  fetchLayers()
                    .then((apiLayers) => setNodes(apiLayers.map(mapApiLayerToNode)))
                    .catch((e: unknown) => setError((e as { message?: string }).message || '获取失败'))
                    .finally(() => setLoading(false));
                }}
              >
                重试
              </Button>
            }
          />
        </div>
      </Card>
    );
  }

  // ── Loading state ──
  if (loading) {
    return (
      <Card title="管道 DAG 拓扑" size="small" styles={{ body: { padding: 0 } }}>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
          <Spin size="large" tip="正在加载拓扑数据..." />
        </div>
      </Card>
    );
  }

  // ── Empty state ──
  if (!option) {
    return (
      <Card title="管道 DAG 拓扑" size="small" styles={{ body: { padding: 0 } }}>
        <div style={{ padding: 48 }}>
          <Empty description="暂无管道拓扑数据" />
        </div>
      </Card>
    );
  }

  return (
    <Card
      title="管道 DAG 拓扑"
      size="small"
      styles={{ body: { padding: 0 } }}
    >
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: 680, width: '100%' }}
        onEvents={{ click: handleChartClick }}
        notMerge
        lazyUpdate
      />
    </Card>
  );
};

export default PipelineDAG;
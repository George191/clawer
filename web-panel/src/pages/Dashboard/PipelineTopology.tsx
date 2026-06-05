import React, { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Card, theme } from 'antd';
import { layerColors } from '@/theme/tokens';
import type { PipelineNodeData } from '@/services/types';

echarts.use([GraphChart, TooltipComponent, CanvasRenderer]);

interface PipelineTopologyProps {
  nodes: PipelineNodeData[];
}

// ── Color helpers ──
function lighten(hex: string, amt: number): string {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgb(${Math.min(255, (n >> 16) + Math.round(255 * amt))},${Math.min(255, ((n >> 8) & 0xff) + Math.round(255 * amt))},${Math.min(255, (n & 0xff) + Math.round(255 * amt))})`;
}
function darken(hex: string, amt: number): string {
  const n = parseInt(hex.replace('#', ''), 16);
  return `rgb(${Math.max(0, (n >> 16) - Math.round(255 * amt))},${Math.max(0, ((n >> 8) & 0xff) - Math.round(255 * amt))},${Math.max(0, (n & 0xff) - Math.round(255 * amt))})`;
}

const ALL_LAYERS = ['Crawl', 'RDS', 'ODS', 'TASK', 'DWD', 'DWS', 'ADS'] as const;

const PipelineTopology: React.FC<PipelineTopologyProps> = ({ nodes }) => {
  const { token } = theme.useToken();

  const option = useMemo(() => {
    const nodeMap = new Map(nodes.map((n) => [n.name, n]));
    const spacing = 180;
    const startX = 120;

    const links = ALL_LAYERS.slice(1).map((name, i) => ({
      source: ALL_LAYERS[i],
      target: name,
    }));

    const statusLabel: Record<string, string> = {
      running: '🟢 运行中',
      stopped: '⚫ 已停止',
      error: '🔴 异常',
    };

    return {
      tooltip: {
        trigger: 'item' as const,
        backgroundColor: token.colorBgElevated,
        borderColor: token.colorBorder,
        textStyle: { color: token.colorText },
        extraCssText: 'border-radius:8px;box-shadow:0 6px 16px rgba(0,0,0,0.12);padding:12px 16px;',
        formatter: (params: any) => {
          if (params.dataType === 'edge') return '';
          const n = nodeMap.get(params.name);
          if (!n) return `<b>${params.name}</b><br/>无数据`;
          return [
            `<div style="font-weight:700;font-size:14px;margin-bottom:8px;color:${layerColors[n.name] || token.colorPrimary}">${n.name}</div>`,
            `<div style="margin:4px 0"><span style="color:${token.colorTextSecondary}">状态</span> ${statusLabel[n.status]}</div>`,
            `<div style="margin:4px 0"><span style="color:${token.colorTextSecondary}">处理速率</span> ${n.throughput.toLocaleString()} msg/s</div>`,
            `<div style="margin:4px 0"><span style="color:${token.colorTextSecondary}">Lag</span> ${n.lag.toLocaleString()}</div>`,
            `<div style="margin-top:8px;padding-top:6px;border-top:1px solid ${token.colorBorder}">`,
            `<a style="color:${token.colorPrimary};font-size:12px;cursor:pointer">📋 查看详情</a>`,
            `&nbsp;<a style="color:${token.colorPrimary};font-size:12px;cursor:pointer">📊 监控面板</a>`,
            `</div>`,
          ].join('');
        },
      },
      series: [{
        type: 'graph',
        layout: 'none',
        roam: false,
        draggable: false,
        data: ALL_LAYERS.map((name, i) => {
          const node = nodeMap.get(name);
          const color = layerColors[name] || '#1677ff';
          const isError = node?.status === 'error';
          const isStopped = !node || node.status === 'stopped';

          return {
            name,
            x: startX + i * spacing,
            y: 100,
            symbol: 'roundRect' as const,
            symbolSize: [88, 46],
            itemStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: isStopped ? '#555' : isError ? '#ff4d4f' : lighten(color, 0.18) },
                { offset: 1, color: isStopped ? '#333' : isError ? '#cf1322' : darken(color, 0.12) },
              ]),
              borderColor: isStopped ? '#444' : isError ? '#ff7875' : color + '99',
              borderWidth: 2,
              borderRadius: 8,
              shadowBlur: isStopped ? 4 : isError ? 20 : 16,
              shadowColor: isStopped ? 'transparent' : isError ? '#ff4d4f60' : color + '60',
            },
            label: {
              show: true,
              position: 'inside',
              formatter: node
                ? `{name|${name}}\n{rate|${node.throughput.toLocaleString()} msg/s}`
                : `{name|${name}}\n{rate|--}`,
              rich: {
                name: {
                  fontSize: 12,
                  fontWeight: 700,
                  color: '#fff',
                  lineHeight: 18,
                  textShadowColor: 'rgba(0,0,0,0.4)',
                  textShadowBlur: 3,
                },
                rate: {
                  fontSize: 9,
                  color: 'rgba(255,255,255,0.72)',
                  lineHeight: 14,
                },
              },
            },
            emphasis: {
              itemStyle: {
                shadowBlur: isStopped ? 8 : isError ? 30 : 24,
                shadowColor: isStopped ? '#666' : isError ? '#ff4d4f90' : color + '90',
                borderWidth: 3,
              },
            },
          };
        }),
        links: links.map((l) => {
          const targetNode = nodeMap.get(l.target);
          const lineWidth = targetNode ? Math.max(1.5, Math.min(5, targetNode.throughput / 280)) : 2;
          return {
            source: l.source,
            target: l.target,
            lineStyle: {
              color: token.colorBorderSecondary || '#30363d',
              width: lineWidth,
              curveness: 0,
              type: [6, 4] as any,
              cap: 'round' as const,
            },
            emphasis: {
              lineStyle: {
                color: '#58a6ff',
                width: lineWidth + 1,
                type: [4, 2] as any,
              },
            },
          };
        }),
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 12],
        edgeLabel: { show: false },
        animation: true,
        animationDuration: 2800,
        animationEasing: 'cubicInOut' as const,
      }],
    };
  }, [nodes, token]);

  return (
    <Card
      title={<span style={{ fontWeight: 600, fontSize: 14 }}>ETL 数据流拓扑</span>}
      styles={{ body: { padding: '4px 12px 0' } }}
    >
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: 280, width: '100%' }}
        notMerge
      />
    </Card>
  );
};

export default PipelineTopology;
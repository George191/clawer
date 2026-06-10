import React, { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, PieChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Row, Col, Card, theme } from 'antd';
import { chartPalette, semanticHex } from '@/theme/tokens';
import type { DashboardMetrics, LayerThroughputPoint } from '@/services/types';

echarts.use([
  LineChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent, MarkLineComponent,
  CanvasRenderer,
]);

// ── Task status colors ──
const taskStatusColors = [semanticHex.primary, semanticHex.success, semanticHex.danger, semanticHex.warning];

const gridBase = { top: 24, right: 16, bottom: 40, left: 48 };

// ── Shared axis text style builder ──
const axisText = (token: any) => ({
  color: token.colorTextTertiary,
  fontSize: 10,
});

// ============ Stacked Area: ETL Layer Throughput ============
const StackedAreaChart: React.FC<{
  data: LayerThroughputPoint[];
  token: any;
}> = ({ data, token }) => {
  const layerKeys = ['Crawl', 'RDS', 'ODS', 'TASK', 'DWD', 'DWS', 'ADS'] as const;

  const option = useMemo(() => ({
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: token.colorBgElevated,
      borderColor: token.colorBorder,
      textStyle: { color: token.colorText, fontSize: 12 },
    },
    legend: {
      bottom: 0,
      textStyle: { color: token.colorTextSecondary, fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: gridBase,
    xAxis: {
      type: 'category' as const,
      data: data.map((d) => d.time),
      axisLabel: axisText(token),
      axisLine: { lineStyle: { color: token.colorBorder } },
    },
    yAxis: {
      type: 'value' as const,
      name: 'msg/s',
      nameTextStyle: { color: token.colorTextTertiary, fontSize: 10 },
      axisLabel: axisText(token),
      splitLine: { lineStyle: { color: token.colorBorderSecondary, type: 'dashed' as const } },
    },
    series: layerKeys.map((key, i) => ({
      name: key,
      type: 'line' as const,
      stack: 'total',
      data: data.map((d) => d[key]),
      smooth: true,
      symbol: 'none' as const,
      lineStyle: { width: 1, color: chartPalette[i % chartPalette.length] },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: chartPalette[i % chartPalette.length] + '50' },
          { offset: 1, color: chartPalette[i % chartPalette.length] + '08' },
        ]),
      },
      emphasis: { focus: 'series' as const },
    })),
  }), [data, token]);

  return (
    <Card
      title={<span style={{ fontSize: 13, fontWeight: 600 }}>ETL 各层吞吐量</span>}
      size="small"
      styles={{ body: { padding: '8px 12px 4px' } }}
    >
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} notMerge />
    </Card>
  );
};

// ============ Line: Kafka Lag Trend ============
const KafkaLagTrendChart: React.FC<{
  data: { ts: string; v: number }[];
  token: any;
}> = ({ data, token }) => {
  const threshold = 1500;

  const option = useMemo(() => ({
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: token.colorBgElevated,
      borderColor: token.colorBorder,
      textStyle: { color: token.colorText, fontSize: 12 },
    },
    legend: {
      bottom: 0,
      textStyle: { color: token.colorTextSecondary, fontSize: 11 },
    },
    grid: gridBase,
    xAxis: {
      type: 'category' as const,
      data: data.map((d) => d.ts),
      axisLabel: axisText(token),
      axisLine: { lineStyle: { color: token.colorBorder } },
    },
    yAxis: {
      type: 'value' as const,
      name: '条',
      nameTextStyle: { color: token.colorTextTertiary, fontSize: 10 },
      axisLabel: axisText(token),
      splitLine: { lineStyle: { color: token.colorBorderSecondary, type: 'dashed' as const } },
    },
    series: [{
      name: 'Kafka Lag',
      type: 'line' as const,
      data: data.map((d) => d.v),
      smooth: true,
      symbol: 'circle' as const,
      symbolSize: 4,
      lineStyle: { color: chartPalette[2], width: 2 },
      itemStyle: { color: chartPalette[2] },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: chartPalette[2] + '30' },
          { offset: 1, color: chartPalette[2] + '03' },
        ]),
      },
      markLine: {
        silent: true,
        symbol: 'none' as const,
        lineStyle: { color: semanticHex.warning, type: 'dashed' as const, width: 1.5 },
        label: {
          formatter: `阈值 ${threshold}`,
          color: semanticHex.warning,
          fontSize: 10,
          position: 'end' as const,
        },
        data: [{ yAxis: threshold }],
      },
      emphasis: { focus: 'series' as const },
    }],
  }), [data, token]);

  return (
    <Card
      title={<span style={{ fontSize: 13, fontWeight: 600 }}>Kafka Lag 趋势</span>}
      size="small"
      styles={{ body: { padding: '8px 12px 4px' } }}
    >
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} notMerge />
    </Card>
  );
};

// ============ Donut: Task Status Distribution ============
const TaskStatusDonutChart: React.FC<{
  data: { name: string; value: number }[];
  token: any;
}> = ({ data, token }) => {
  const total = useMemo(() => data.reduce((s, d) => s + d.value, 0), [data]);

  const option = useMemo(() => ({
    tooltip: {
      trigger: 'item' as const,
      formatter: '{b}: {c} 个 ({d}%)',
      backgroundColor: token.colorBgElevated,
      borderColor: token.colorBorder,
      textStyle: { color: token.colorText, fontSize: 12 },
    },
    legend: {
      bottom: 0,
      textStyle: { color: token.colorTextSecondary, fontSize: 11 },
      itemWidth: 10,
      itemHeight: 10,
    },
    graphic: {
      type: 'text' as const,
      left: 'center',
      top: '42%',
      style: {
        text: `${total}`,
        textAlign: 'center' as const,
        fill: token.colorText,
        fontSize: 22,
        fontWeight: 700,
      },
    },
    series: [{
      type: 'pie' as const,
      radius: ['52%', '78%'],
      center: ['50%', '46%'],
      avoidLabelOverlap: false,
      itemStyle: {
        borderRadius: 4,
        borderColor: token.colorBgContainer,
        borderWidth: 2,
      },
      label: { show: false },
      emphasis: {
        label: { show: true, fontWeight: 'bold' as const },
        scaleSize: 8,
      },
      data: data.map((d, i) => ({
        name: d.name,
        value: d.value,
        itemStyle: { color: taskStatusColors[i % taskStatusColors.length] },
      })),
    }],
  }), [data, token, total]);

  return (
    <Card
      title={<span style={{ fontSize: 13, fontWeight: 600 }}>任务分布</span>}
      size="small"
      styles={{ body: { padding: '8px 12px 4px' } }}
    >
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} notMerge />
    </Card>
  );
};

// ============ Line: Error Rate Trend ============
const ErrorRateTrendChart: React.FC<{
  data: { ts: string; v: number }[];
  threshold: number;
  token: any;
}> = ({ data, threshold, token }) => {
  const option = useMemo(() => ({
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: token.colorBgElevated,
      borderColor: token.colorBorder,
      textStyle: { color: token.colorText, fontSize: 12 },
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        const v = p?.value;
        const overThreshold = typeof v === 'number' && v > threshold;
        return `<span>${p?.axisValue}</span><br/>
          <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${semanticHex.danger};margin-right:6px"></span>
          错误率: <b style="color:${overThreshold ? semanticHex.danger : token.colorText}">${v}%</b>
          ${overThreshold ? '<br/><span style="color:${semanticHex.danger};font-size:11px">⚠️ 超过阈值</span>' : ''}`;
      },
    },
    legend: {
      bottom: 0,
      textStyle: { color: token.colorTextSecondary, fontSize: 11 },
    },
    grid: gridBase,
    xAxis: {
      type: 'category' as const,
      data: data.map((d) => d.ts),
      axisLabel: axisText(token),
      axisLine: { lineStyle: { color: token.colorBorder } },
    },
    yAxis: {
      type: 'value' as const,
      name: '%',
      min: 0,
      nameTextStyle: { color: token.colorTextTertiary, fontSize: 10 },
      axisLabel: axisText(token),
      splitLine: { lineStyle: { color: token.colorBorderSecondary, type: 'dashed' as const } },
    },
    series: [{
      name: '错误率',
      type: 'line' as const,
      data: data.map((d) => d.v),
      smooth: true,
      symbol: 'circle' as const,
      symbolSize: 4,
      lineStyle: { color: semanticHex.danger, width: 2 },
      itemStyle: { color: semanticHex.danger },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: semanticHex.danger + '20' },
          { offset: 1, color: semanticHex.danger + '02' },
        ]),
      },
      markLine: {
        silent: true,
        symbol: 'none' as const,
        lineStyle: { color: semanticHex.danger, type: 'dashed' as const, width: 1.5 },
        label: {
          formatter: `阈值 ${threshold}%`,
          color: semanticHex.danger,
          fontSize: 10,
          position: 'end' as const,
        },
        data: [{ yAxis: threshold }],
      },
      emphasis: { focus: 'series' as const },
    }],
  }), [data, threshold, token]);

  return (
    <Card
      title={<span style={{ fontSize: 13, fontWeight: 600 }}>错误率趋势</span>}
      size="small"
      styles={{ body: { padding: '8px 12px 4px' } }}
    >
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 280 }} notMerge />
    </Card>
  );
};

// ============ Charts Grid (2x2) ============
export const ChartsGrid: React.FC<{ metrics: DashboardMetrics | null }> = ({ metrics }) => {
  const { token } = theme.useToken();

  if (!metrics) return null;

  return (
    <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
      <Col xs={24} lg={12}>
        <StackedAreaChart
          data={metrics.layer_throughput_history}
          token={token}
        />
      </Col>
      <Col xs={24} lg={12}>
        <KafkaLagTrendChart
          data={metrics.kafka_lag_history}
          token={token}
        />
      </Col>
      <Col xs={24} lg={12}>
        <TaskStatusDonutChart
          data={metrics.task_status_dist}
          token={token}
        />
      </Col>
      <Col xs={24} lg={12}>
        <ErrorRateTrendChart
          data={metrics.error_rate_history}
          threshold={metrics.error_threshold}
          token={token}
        />
      </Col>
    </Row>
  );
};

import ReactECharts from 'echarts-for-react';
import type { WorkspaceExecution } from '@/services/aiApi';
import { useThemeStore } from '@/stores/settings';

export default function RunTimeline({ runs, onSelect }: { runs: WorkspaceExecution[]; onSelect: (taskId: string, runId: string) => void }) {
  const light = useThemeStore(state => state.mode === 'light');
  const options = {
    animationDuration: 200,
    grid: { left: 70, right: 24, top: 20, bottom: 58 },
    tooltip: { trigger: 'item', formatter: (params: { data: { value: [number, number]; name: string } }) => `${new Date(params.data.value[0]).toLocaleString()}<br/>${Math.round(params.data.value[1])} ms` },
    xAxis: { type: 'time', axisLabel: { color: light ? '#64748b' : '#94a3b8' }, splitLine: { show: false } },
    yAxis: { type: 'log', min: 1, name: 'Duration (ms)', nameTextStyle: { color: light ? '#64748b' : '#94a3b8' }, axisLabel: { color: light ? '#64748b' : '#94a3b8' }, splitLine: { lineStyle: { color: light ? '#e2e8f0' : '#334155' } } },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none' }, { type: 'slider', height: 16, bottom: 8, borderColor: 'transparent', showDetail: false }],
    series: ['completed', 'failed', 'running', 'paused', 'canceled', 'legacy'].map(status => ({
      type: 'scatter', name: status, symbolSize: 8,
      itemStyle: { color: status === 'completed' ? '#4ade80' : status === 'failed' ? '#f87171' : status === 'running' ? '#60a5fa' : '#94a3b8' },
      data: runs.filter(run => run.status === status).map(run => ({ name: run.name, taskId: run.task_id, runId: run.id, value: [Date.parse(run.started_at), Math.max(1, Date.parse(run.ended_at || new Date().toISOString()) - Date.parse(run.started_at))] })),
    })),
  };
  return <div className="windmill-run-timeline" aria-label="Execution duration over time"><ReactECharts option={options} style={{ height: 230 }} onEvents={{ click: (params: { data: { taskId: string; runId: string } }) => onSelect(params.data.taskId, params.data.runId) }} /></div>;
}

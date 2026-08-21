import React, { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Badge, Button, Card, Descriptions, Input, Segmented, Space, Tabs, Tag, Typography } from 'antd';
import { ApartmentOutlined, DatabaseOutlined, SearchOutlined, ShareAltOutlined } from '@ant-design/icons';
import './workspace.css';

type GraphNode = { id: string; name: string; category: number; value: number; meta: { type: string; owner: string; schema?: string; status: string } };

const nodes: GraphNode[] = [
  { id: 'patent', name: 'patent_assets', category: 0, value: 50, meta: { type: 'Table', schema: 'ts_ods', owner: 'Data Ops', status: 'healthy' } },
  { id: 'normalize', name: 'Patent normalization', category: 2, value: 64, meta: { type: 'Task', owner: 'Default Team', status: 'running' } },
  { id: 'pipeline', name: 'Patent ETL', category: 1, value: 56, meta: { type: 'Pipeline', owner: 'Data Ops', status: 'healthy' } },
  { id: 'dwd', name: 'patent_detail', category: 0, value: 48, meta: { type: 'Table', schema: 'ts_dwd', owner: 'Research Lab', status: 'healthy' } },
  { id: 'model', name: 'Entity classifier', category: 3, value: 46, meta: { type: 'Model', owner: 'AI Team', status: 'ready' } },
  { id: 'team', name: 'Research Lab', category: 4, value: 42, meta: { type: 'Team', owner: 'Acme Research', status: 'active' } },
  { id: 'report', name: 'Patent insights', category: 5, value: 44, meta: { type: 'Dataset', schema: 'web', owner: 'Research Lab', status: 'healthy' } },
];

const links = [
  { source: 'patent', target: 'pipeline', value: 'consumes' }, { source: 'pipeline', target: 'normalize', value: 'runs' },
  { source: 'normalize', target: 'dwd', value: 'produces' }, { source: 'model', target: 'normalize', value: 'enriches' },
  { source: 'team', target: 'dwd', value: 'owns' }, { source: 'dwd', target: 'report', value: 'feeds' }, { source: 'team', target: 'report', value: 'owns' },
];

const DataGraph: React.FC = () => {
  const [selected, setSelected] = useState(nodes[1]);
  const [schema, setSchema] = useState('全部');
  const option = useMemo(() => ({
    backgroundColor: 'transparent', tooltip: { formatter: (p: { data?: GraphNode }) => p.data?.name || '' },
    legend: [{ data: ['Table', 'Pipeline', 'Task', 'Model', 'Team', 'Dataset'], textStyle: { color: '#94A3B8' }, bottom: 8 }],
    series: [{ type: 'graph', layout: 'force', roam: true, draggable: true, data: nodes.map(n => ({ ...n, symbolSize: n.value })), links,
      categories: ['Table', 'Pipeline', 'Task', 'Model', 'Team', 'Dataset'].map(name => ({ name })),
      force: { repulsion: 280, edgeLength: [110, 190] }, label: { show: true, color: '#E4E7EB', fontSize: 11, position: 'bottom', distance: 8 },
      edgeLabel: { show: true, formatter: '{c}', color: '#6B7280', fontSize: 9 }, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 7,
      lineStyle: { color: '#018BFF', opacity: .42, width: 1.5, curveness: .08 },
      itemStyle: { borderWidth: 2, borderColor: '#018BFF', shadowBlur: 12, shadowColor: 'rgba(1,139,255,.28)' },
      emphasis: { focus: 'adjacency', lineStyle: { opacity: .9, width: 3 }, itemStyle: { shadowBlur: 24 } },
    }],
  }), []);

  return <div className="graph-workspace">
    <header className="graph-page-header"><div><Typography.Text className="graph-kicker">DATA ASSETS / RELATIONSHIPS</Typography.Text><Typography.Title level={2}>数据图谱</Typography.Title><Typography.Text type="secondary">探索资产、任务、模型和团队之间的运行关系</Typography.Text></div><Space><Button icon={<ShareAltOutlined />}>保存视图</Button><Button type="primary" icon={<ApartmentOutlined />}>新建关系视图</Button></Space></header>
    <div className="graph-shell">
      <aside className="graph-filter-panel"><Input prefix={<SearchOutlined />} placeholder="搜索资产或任务" allowClear /><Typography.Text className="panel-label">SCHEMA</Typography.Text><Segmented block vertical options={['全部', 'public', 'etl', 'web', 'meta', 'ts_*']} value={schema} onChange={value => setSchema(String(value))} /><Typography.Text className="panel-label">保存的视图</Typography.Text><div className="saved-view active"><DatabaseOutlined /> 平台运行图谱</div><div className="saved-view">ETL 数据血缘</div><div className="saved-view">团队资源范围</div><Typography.Text className="panel-label">图例</Typography.Text>{['Table', 'Pipeline', 'Task', 'Model', 'Team', 'Dataset'].map((x, i) => <div className="legend-row" key={x}><i style={{ background: ['#018BFF','#8B5CF6','#10B981','#F59E0B','#EC4899','#06B6D4'][i] }} />{x}</div>)}</aside>
      <section className="graph-canvas"><div className="canvas-toolbar"><Badge status="processing" text={`${nodes.length} 个节点`} /><span>滚轮缩放 · 拖动画布 · 单击检查</span></div><ReactECharts option={option} style={{ height: '100%', width: '100%' }} onEvents={{ click: (params: { data?: GraphNode }) => params.data?.meta && setSelected(params.data) }} /></section>
      <aside className="graph-inspector"><div className="inspector-title"><Tag color="blue">{selected.meta.type}</Tag><Typography.Title level={4}>{selected.name}</Typography.Title><Badge status={selected.meta.status === 'running' ? 'processing' : 'success'} text={selected.meta.status} /></div><Tabs size="small" items={[{ key: 'overview', label: '概览', children: <><Descriptions column={1} size="small" items={[{ key: 'owner', label: '负责人', children: selected.meta.owner }, { key: 'schema', label: 'Schema', children: selected.meta.schema || '—' }, { key: 'tenant', label: '租户', children: 'Acme Research' }, { key: 'team', label: '团队', children: 'Default Team' }]} /><Card size="small" title="最近运行" className="inspector-card"><Badge status="processing" text="Patent normalization · 68%" /><Typography.Text type="secondary">2 分钟前更新</Typography.Text></Card></> }, { key: 'lineage', label: '血缘', children: <Typography.Text type="secondary">上游 3 个资源，下游 2 个资源</Typography.Text> }, { key: 'access', label: '权限', children: <Typography.Text type="secondary">Default Team 可编辑，Research Lab 可查看</Typography.Text> }]} /></aside>
    </div>
  </div>;
};
export default DataGraph;

import React, { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Checkbox, Dropdown, Input, InputNumber, Modal, Select, Spin, Table, Tag, Tabs, message } from 'antd';
import { AlertOutlined, ApartmentOutlined, BranchesOutlined, CaretRightOutlined, CodeOutlined, DatabaseOutlined, DeleteOutlined, DownOutlined, ExperimentOutlined, PlusOutlined, RightOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons';
import '../ProductWorkspace/workspace.css';
import type { LayerNode, LayerTable, QueryResult, EtlPartition, EtlStreamState } from '../../services/types';

const pageMeta: Record<string, { title: string; description: string }> = {
  '/flow/layers': { title: 'Data connectors', description: 'Browse PostgreSQL schemas, tables, stream positions and deployed connector code.' },
  '/flow/transforms': { title: 'Transformation assets', description: 'Review deployed transformations, their dependencies and validation state.' },
  '/flow/quality': { title: 'Quality gates', description: 'Assess output contracts before downstream publication.' },
  '/flow/lineage': { title: 'Lineage and impact', description: 'Trace ETL dependencies and assess downstream impact.' },
};
const mockLayers: LayerNode[] = [
  { key: 'rds', label: 'RDS', icon: 'DatabaseOutlined', status: 'running', rate: 842, lag: 12, tables: 12 },
  { key: 'ods', label: 'ODS', icon: 'FolderOutlined', status: 'running', rate: 796, lag: 8, tables: 28 },
  { key: 'task', label: 'TASK', icon: 'ScheduleOutlined', status: 'running', rate: 624, lag: 21, tables: 16 },
  { key: 'dwd', label: 'DWD', icon: 'TableOutlined', status: 'running', rate: 588, lag: 4, tables: 46 },
  { key: 'dws', label: 'DWS', icon: 'BarChartOutlined', status: 'running', rate: 210, lag: 2, tables: 19 },
];
const mockTables: Record<string, LayerTable[]> = {
  rds: [{ name: 'rds_news', schemaName: 'ts_rds', partitioned: true, rowCount: 128400, size: '1.8 GB', updatedAt: '2 min ago' }, { name: 'rds_patent', schemaName: 'ts_rds', partitioned: true, rowCount: 84210, size: '940 MB', updatedAt: '4 min ago' }],
  ods: [{ name: 'ods_news', schemaName: 'ts_ods', partitioned: true, rowCount: 127980, size: '2.4 GB', updatedAt: '3 min ago' }, { name: 'ods_patent', schemaName: 'ts_ods', partitioned: true, rowCount: 83950, size: '1.1 GB', updatedAt: '5 min ago' }],
  task: [{ name: 'task_document_enrichment', schemaName: 'ts_task', partitioned: false, rowCount: 39120, size: '620 MB', updatedAt: '8 min ago' }],
  dwd: [{ name: 'dwd_vessel_events', schemaName: 'ts_dwd', partitioned: true, rowCount: 5620040, size: '18.2 GB', updatedAt: '1 min ago' }],
  dws: [{ name: 'dws_daily_activity', schemaName: 'ts_dws', partitioned: false, rowCount: 18420, size: '88 MB', updatedAt: '12 min ago' }],
};
const mockPartitions: EtlPartition[] = [{ name: 'p2026_08_24' }, { name: 'p2026_08_25' }, { name: 'p2026_08_26' }];
const PostgresIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Postgres" fill="currentColor"><path fillRule="evenodd" clipRule="evenodd" d="M23.5594 14.7228a.5269.5269 0 0 0-.0563-.1191c-.139-.2632-.4768-.3418-1.0074-.2321-1.6533.3411-2.2935.1312-2.5256-.0191 1.342-2.0482 2.445-4.522 3.0411-6.8297.2714-1.0507.7982-3.5237.1222-4.7316a1.5641 1.5641 0 0 0-.1509-.235C21.6931.9086 19.8007.0248 17.5099.0005c-1.4947-.0158-2.7705.3461-3.1161.4794a9.449 9.449 0 0 0-.5159-.0816 8.044 8.044 0 0 0-1.3114-.1278c-1.1822-.0184-2.2038.2642-3.0498.8406-.8573-.3211-4.7888-1.645-7.2219.0788C.9359 2.1526.3086 3.8733.4302 6.3043c.0409.818.5069 3.334 1.2423 5.7436.4598 1.5065.9387 2.7019 1.4334 3.582.553.9942 1.1259 1.5933 1.7143 1.7895.4474.1491 1.1327.1441 1.8581-.7279.8012-.9635 1.5903-1.8258 1.9446-2.2069.4351.2355.9064.3625 1.39.3772a.0569.0569 0 0 0 .0004.0041 11.0312 11.0312 0 0 0-.2472.3054c-.3389.4302-.4094.5197-1.5002.7443-.3102.064-1.1344.2339-1.1464.8115-.0025.1224.0329.2309.0919.3268.2269.4231.9216.6097 1.015.6331 1.3345.3335 2.5044.092 3.3714-.6787-.017 2.231.0775 4.4174.3454 5.0874.2212.5529.7618 1.9045 2.4692 1.9043.2505 0 .5263-.0291.8296-.0941 1.7819-.3821 2.5557-1.1696 2.855-2.9059.1503-.8707.4016-2.8753.5388-4.1012.0169-.0703.0357-.1207.057-.1362.0007-.0005.0697-.0471.4272.0307a.3673.3673 0 0 0 .0443.0068l.2539.0223.0149.001c.8468.0384 1.9114-.1426 2.5312-.4308.6438-.2988 1.8057-1.0323 1.5951-1.6698zM2.371 11.8765c-.7435-2.4358-1.1779-4.8851-1.2123-5.5719-.1086-2.1714.4171-3.6829 1.5623-4.4927 1.8367-1.2986 4.8398-.5408 6.108-.13-.0032.0032-.0066.0061-.0098.0094-2.0238 2.044-1.9758 5.536-1.9708 5.7495-.0002.0823.0066.1989.0162.3593.0348.5873.0996 1.6804-.0735 2.9184-.1609 1.1504.1937 2.2764.9728 3.0892.0806.0841.1648.1631.2518.2374-.3468.3714-1.1004 1.1926-1.9025 2.1576-.5677.6825-.9597.5517-1.0886.5087-.3919-.1307-.813-.5871-1.2381-1.3223-.4796-.839-.9635-2.0317-1.4155-3.5126zm6.0072 5.0871c-.1711-.0428-.3271-.1132-.4322-.1772.0889-.0394.2374-.0902.4833-.1409 1.2833-.2641 1.4815-.4506 1.9143-1.0002.0992-.126.2116-.2687.3673-.4426a.3549.3549 0 0 0 .0737-.1298c.1708-.1513.2724-.1099.4369-.0417.156.0646.3078.26.3695.4752.0291.1016.0619.2945-.0452.4444-.9043 1.2658-2.2216 1.2494-3.1676 1.0128zm2.094-3.988-.0525.141c-.133.3566-.2567.6881-.3334 1.003-.6674-.0021-1.3168-.2872-1.8105-.8024-.6279-.6551-.9131-1.5664-.7825-2.5004.1828-1.3079.1153-2.4468.079-3.0586-.005-.0857-.0095-.1607-.0122-.2199.2957-.2621 1.6659-.9962 2.6429-.7724.4459.1022.7176.4057.8305.928.5846 2.7038.0774 3.8307-.3302 4.7363-.084.1866-.1633.3629-.2311.5454zm7.3637 4.5725c-.0169.1768-.0358.376-.0618.5959l-.146.4383a.3547.3547 0 0 0-.0182.1077c-.0059.4747-.054.6489-.115.8693-.0634.2292-.1353.4891-.1794 1.0575-.11 1.4143-.8782 2.2267-2.4172 2.5565-1.5155.3251-1.7843-.4968-2.0212-1.2217a6.5824 6.5824 0 0 0-.0769-.2266c-.2154-.5858-.1911-1.4119-.1574-2.5551.0165-.5612-.0249-1.9013-.3302-2.6462.0044-.2932.0106-.5909.019-.8918a.3529.3529 0 0 0-.0153-.1126 1.4927 1.4927 0 0 0-.0439-.208c-.1226-.4283-.4213-.7866-.7797-.9351-.1424-.059-.4038-.1672-.7178-.0869.067-.276.1831-.5875.309-.9249l.0529-.142c.0595-.16.134-.3257.213-.5012.4265-.9476 1.0106-2.2453.3766-5.1772-.2374-1.0981-1.0304-1.6343-2.2324-1.5098-.7207.0746-1.3799.3654-1.7088.5321a5.6716 5.6716 0 0 0-.1958.1041c.0918-1.1064.4386-3.1741 1.7357-4.4823a4.0306 4.0306 0 0 1 .3033-.276.3532.3532 0 0 0 .1447-.0644c.7524-.5706 1.6945-.8506 2.802-.8325.4091.0067.8017.0339 1.1742.081 1.939.3544 3.2439 1.4468 4.0359 2.3827.8143.9623 1.2552 1.9315 1.4312 2.4543-1.3232-.1346-2.2234.1268-2.6797.779-.9926 1.4189.543 4.1729 1.2811 5.4964.1353.2426.2522.4522.2889.5413.2403.5825.5515.9713.7787 1.2552.0696.087.1372.1714.1885.245-.4008.1155-1.1208.3825-1.0552 1.717-.0123.1563-.0423.4469-.0834.8148-.0461.2077-.0702.4603-.0994.7662zm.8905-1.6211c-.0405-.8316.2691-.9185.5967-1.0105a2.8566 2.8566 0 0 0 .135-.0406 1.202 1.202 0 0 0 .1342.103c.5703.3765 1.5823.4213 3.0068.1344-.2016.1769-.5189.3994-.9533.6011-.4098.1903-1.0957.333-1.7473.3636-.7197.0336-1.0859-.0807-1.1721-.151zm.5695-9.2712c-.0059.3508-.0542.6692-.1054 1.0017-.055.3576-.112.7274-.1264 1.1762-.0142.4368.0404.8909.0932 1.3301.1066.887.216 1.8003-.2075 2.7014a3.5272 3.5272 0 0 1-.1876-.3856c-.0527-.1276-.1669-.3326-.3251-.6162-.6156-1.1041-2.0574-3.6896-1.3193-4.7446.3795-.5427 1.3408-.5661 2.1781-.463zm.2284 7.0137a12.3762 12.3762 0 0 0-.0853-.1074l-.0355-.0444c.7262-1.1995.5842-2.3862.4578-3.4385-.0519-.4318-.1009-.8396-.0885-1.2226.0129-.4061.0666-.7543.1185-1.0911.0639-.415.1288-.8443.1109-1.3505.0134-.0531.0188-.1158.0118-.1902-.0457-.4855-.5999-1.938-1.7294-3.253-.6076-.7073-1.4896-1.4972-2.6889-2.0395.5251-.1066 1.2328-.2035 2.0244-.1859 2.0515.0456 3.6746.8135 4.8242 2.2824a.908.908 0 0 1 .0667.1002c.7231 1.3556-.2762 6.2751-2.9867 10.5405zm-8.8166-6.1162c-.025.1794-.3089.4225-.6211.4225a.5821.5821 0 0 1-.0809-.0056c-.1873-.026-.3765-.144-.5059-.3156-.0458-.0605-.1203-.178-.1055-.2844.0055-.0401.0261-.0985.0925-.1488.1182-.0894.3518-.1226.6096-.0867.3163.0441.6426.1938.6113.4186zm7.9305-.4114c.0111.0792-.049.201-.1531.3102-.0683.0717-.212.1961-.4079.2232a.5456.5456 0 0 1-.075.0052c-.2935 0-.5414-.2344-.5607-.3717-.024-.1765.2641-.3106.5611-.352.297-.0414.6111.0088.6356.1851z" /></svg>;
const MongoDbIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="MongoDB" fill="currentColor"><path d="M17.193 9.555c-1.264-5.58-4.252-7.414-4.573-8.115-.28-.394-.53-.954-.735-1.44-.036.495-.055.685-.523 1.184-.723.566-4.438 3.682-4.74 10.02-.282 5.912 4.27 9.435 4.888 9.884l.07.05A73.49 73.49 0 0 1 11.91 24h.481c.114-1.032.284-2.056.51-3.07.417-.296.604-.463.85-.693a11.342 11.342 0 0 0 3.639-8.464c.01-.814-.103-1.662-.197-2.218zm-5.336 8.195s0-8.291.275-8.29c.213 0 .49 10.695.49 10.695-.381-.045-.765-1.76-.765-2.405z" /></svg>;
const ElasticsearchIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 512 512" role="img" aria-label="Elasticsearch" fillRule="evenodd" clipRule="evenodd" strokeLinejoin="round"><path d="M21.625 256c0 21.62 3.035 42.495 8.195 62.5h304.304c34.516 0 62.5-27.985 62.5-62.5 0-34.516-27.984-62.5-62.5-62.5H29.82a249.101 249.101 0 0 0-8.195 62.5" fill="#343741" /><path d="M442.308 125.718a240.051 240.051 0 0 0 24.324-25.968C420.796 42.637 350.527 6 271.624 6 172.855 6 87.863 63.46 47.304 146.625h341.664a78.627 78.627 0 0 0 53.328-20.907" fill="#fec514" /><path d="M388.968 365.374H47.316C87.88 448.538 172.856 506 271.624 506c78.907 0 149.172-36.652 195.008-93.75a238.559 238.559 0 0 0-24.324-25.968 78.682 78.682 0 0 0-53.34-20.907" fill="#00bfb3" /></svg>;
const S3Icon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 512 512" role="img" aria-label="S3"><rect width="512" height="512" rx="15%" fill="#fff"/><path fill="#e05243" d="M260 348l-137 33V131l137 32z"/><path fill="#8c3123" d="M256 349l133 32V131l-133 32v186"/><path fill="#e05243" d="M256 64v97l58 14V93zm133 67v250l26-13V143zm-133 77v97l58-8v-82zm58 129l-58 14v97l58-29z"/><path fill="#8c3123" d="M256 448V351l-58-14v82zm-133-67V131l-26 13v238zm133-77v-97l-58 8v82zm-58-129l58-14V64l-58 29z"/><path fill="#5e1f18" d="M314 175l-58 11-58-11 58-15 58 15"/><path fill="#f2b0a9" d="M314 337l-58-11-58 11 58 16 58-16"/></svg>;
const BackIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Back" fill="none"><path d="M15.5 6.5L10 12l5.5 5.5M10.5 12H19" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>;
const CloseIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Close" fill="none"><path d="M7 7l10 10M17 7L7 17" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>;

const Pipeline: React.FC = () => {
  const { pathname } = useLocation();
  const active = pageMeta[pathname] ? pathname : '/flow/layers';
  const page = pageMeta[active];
  return <main className="flow-console" data-testid="pipeline-workspace"><header className="flow-header"><div><div className="flow-eyebrow"><span className="flow-live-dot" /> ETL LIFECYCLE</div><h1>{page.title}</h1><p>{page.description}</p></div></header><section className="flow-core"><LifecycleView mode={active} /></section></main>;
};
const LifecycleView: React.FC<{ mode: string }> = ({ mode }) => {
  if (mode === '/flow/layers') return <DataLayersWorkspace />;
  const meta: Record<string, [string, string, string[]]> = {
    '/flow/transforms': ['Transformation assets', 'Code, dependencies and validation state for each deployed layer.', ['ODS normalizers', 'TASK handlers', 'DWD materializations']],
    '/flow/quality': ['Quality gates', 'Contract checks are evaluated before downstream publication.', ['Schema compatibility', 'Required-field validation', 'Freshness and volume']],
    '/flow/lineage': ['Lineage and impact', 'Trace the actual Kafka, table and handler relationships before release.', ['Upstream source → RDS', 'RDS → ODS standardization', 'ODS/TASK → DWD/DWS']],
  };
  const [title, copy, items] = meta[mode] ?? meta['/flow/transforms'];
  return <div className="flow-card flow-list-view"><div className="flow-card-head"><div><h2>{title}</h2><p>{copy}</p></div><div className="flow-list-tools"><Button icon={<SearchOutlined />}>Search</Button><Button icon={<SettingOutlined />}>Filters</Button></div></div><div className="flow-asset-grid">{items.map((item) => <div className="flow-asset-card" key={item}><CodeOutlined /><strong>{item}</strong><span>Lifecycle metadata is available when the connected ETL snapshot reports this asset.</span><Tag>Awaiting snapshot</Tag></div>)}</div><div className="flow-empty-state flow-empty-state--compact"><span>Execution orchestration, schedules and task runs remain in Automation Center.</span></div></div>;
};

const DataLayers: React.FC = () => {
  const [layers, setLayers] = useState<LayerNode[]>([]); const [layer, setLayer] = useState('rds'); const [tables, setTables] = useState<LayerTable[]>([]); const [table, setTable] = useState<LayerTable | null>(null); const [partitions, setPartitions] = useState<EtlPartition[]>([]); const [partition, setPartition] = useState<string>(); const [data, setData] = useState<QueryResult>(); const [stream, setStream] = useState<EtlStreamState>(); const [loading, setLoading] = useState(false); const [offsetOpen, setOffsetOpen] = useState(false); const [offset, setOffset] = useState<number>(); const [script, setScript] = useState(''); const [dryRunRows, setDryRunRows] = useState<Record<string, unknown>[]>([]); const [sql, setSql] = useState('SELECT * FROM ts_rds.rds_news LIMIT 50');
  useEffect(() => { setLayers(mockLayers); }, []);
  useEffect(() => { setLoading(true); const timer = window.setTimeout(() => { const nextTables = mockTables[layer] ?? []; setTables(nextTables); if (nextTables[0]) inspect(nextTables[0]); else setTable(null); setLoading(false); }, 180); return () => window.clearTimeout(timer); }, [layer]);
  useEffect(() => { if (!table) return; setPartitions(mockPartitions); setPartition(undefined); setStream({ available: true, consumerGroup: `etl-${layer}-group`, topic: `etl.${layer}.processed`, offsets: [{ partition: 0, offset: 184220 }, { partition: 1, offset: 182904 }], throughput: mockLayers.find((item) => item.key === layer)?.rate ?? 0, throughputReason: 'Mock snapshot for design review' }); setScript(`# ${layer}/${table.name}\n\ndef handler(message, context):\n    normalized = normalize(message)\n    validate_contract(normalized)\n    return normalized\n`); setDryRunRows([]); setData({ columns: ['record_id', 'source', 'updated_at'], rows: [{ record_id: 'rec_001928', source: 'news', updated_at: '2026-08-25T10:42:00Z' }, { record_id: 'rec_001929', source: 'patent', updated_at: '2026-08-25T10:41:55Z' }], rowCount: 2, elapsed: 0.018 }); }, [layer, table]);
  useEffect(() => { if (!table || !stream) return; const timer = window.setInterval(() => setStream((current) => current ? ({ ...current, offsets: current.offsets?.map((item) => ({ ...item, offset: item.offset + Math.floor(Math.random() * 4) })) }) : current), 1800); return () => window.clearInterval(timer); }, [table, stream?.consumerGroup]);
  const inspect = (selected: LayerTable) => { setTable(selected); setSql(`SELECT * FROM ${selected.schemaName}.${selected.name} LIMIT 50`); };
  const runSql = () => { setData((current) => current ? { ...current, elapsed: 0.012 } : current); message.success('Query executed against the mock result set'); };
  const queryPartition = (value?: string) => { setPartition(value); };
  const saveOffset = async () => { if (offset === undefined) return; message.success('Mock offset updated — worker restart required in production'); setOffsetOpen(false); };
  const runDryRun = () => { setDryRunRows([{ record_id: 'rec_001928', normalized_title: 'Example title', quality_score: 0.98, validation: 'PASS' }, { record_id: 'rec_001929', normalized_title: 'Second title', quality_score: 0.86, validation: 'WARN: source missing' }]); message.success('Dry run complete — no data was persisted'); };
  return <div className="flow-layers"><div className="flow-layer-layout"><aside className="flow-layer-tree"><div className="flow-tree-label">Data connectors</div>{layers.map((item) => <button className={layer === item.key ? 'is-active' : ''} key={item.key} onClick={() => setLayer(item.key)}><span>{item.key.toUpperCase()}</span><small>{item.tables ?? 0} tables</small></button>)}</aside><section className="flow-layer-main"><div className="flow-card flow-table-card"><div className="flow-card-head"><div><h2>{layer.toUpperCase()} tables</h2><p>Schema: ts_{layer}</p></div>{loading && <Spin size="small" />}</div><div className="flow-table"><div className="flow-table-row flow-table-head"><span>Table</span><span>Rows</span><span>Size</span><span /></div>{tables.map((item) => <button className={`flow-table-row flow-table-button ${table?.name === item.name ? 'is-selected' : ''}`} key={item.name} onClick={() => inspect(item)}><span><strong>{item.name}</strong><small>{item.partitioned ? 'Partitioned' : item.schemaName}</small></span><span>{item.rowCount}</span><span>{item.size}</span><span><RightOutlined /></span></button>)}</div></div>{table && <div className="flow-card flow-inspector"><div className="flow-card-head"><div><h2>{table.name}</h2><p>Health and stream position</p></div><Button danger size="small" onClick={() => { setOffset(stream?.offsets?.[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button></div><div className="flow-stream-grid flow-health-default"><div><span>Health</span><strong className="flow-health-good">Healthy</strong><small>Schema compatible · last check 18s ago</small></div><div><span>Topic</span><strong>{stream?.topic ?? 'Unavailable'}</strong></div><div><span>Throughput</span><strong>{stream?.throughput ?? 0}/s</strong><small>Mock snapshot</small></div><div><span>Redis offsets</span><strong>{stream?.offsets?.map((item) => `p${item.partition}: ${item.offset}`).join(' · ')}</strong><small>Live refresh every 1.8s</small></div></div><Tabs items={[{ key: 'data', label: 'Data preview', children: <><div className="flow-sql-bar"><Input value={sql} onChange={(event) => setSql(event.target.value)} onPressEnter={runSql} /><Button type="primary" onClick={runSql}>Run</Button></div><div className="flow-partition-tags">{partitions.map((item) => <Tag.CheckableTag key={item.name} checked={partition === item.name} onChange={() => queryPartition(partition === item.name ? undefined : item.name)}>{item.name.replace('p2026_', '')}</Tag.CheckableTag>)}</div><Table className="flow-data-table" size="small" pagination={false} rowKey="record_id" dataSource={data?.rows ?? []} columns={(data?.columns ?? []).map((key) => ({ title: key, dataIndex: key, key }))} /></> }, { key: 'script', label: 'Layer script', children: <div className="flow-script-editor"><div className="flow-script-note">{layer.toUpperCase()} handler · edits are local until a release is created.</div><Input.TextArea value={script} onChange={(event) => setScript(event.target.value)} autoSize={{ minRows: 12, maxRows: 22 }} /><div className="flow-script-actions"><Button type="primary" onClick={runDryRun}>Run dry run</Button><Button onClick={() => message.info('Changes are local only and were not persisted')}>Validate fields</Button><span>Runs 2 sample records · no writes</span></div>{dryRunRows.length > 0 && <Table size="small" pagination={false} rowKey="record_id" dataSource={dryRunRows} columns={Object.keys(dryRunRows[0]).map((key) => ({ title: key, dataIndex: key, key }))} />}</div> }]} /></div>}</section></div><Modal title="Adjust Redis offset" open={offsetOpen} onCancel={() => setOffsetOpen(false)} onOk={saveOffset} okText="Confirm SET OFFSET"><p>Current offset updates live while this panel is open. Changes apply after the worker restarts.</p><div className="flow-offset-live">{stream?.offsets?.map((item) => <div key={item.partition}><span>Partition {item.partition}</span><strong>{item.offset}</strong></div>)}</div><InputNumber min={0} value={offset} onChange={(value) => setOffset(value ?? undefined)} placeholder="New Kafka offset" style={{ width: '100%' }} /></Modal></div>;
};

const DataLayersWorkspace: React.FC = () => {
  const [collectionOpen, setCollectionOpen] = useState(false);
  const [connectorStep, setConnectorStep] = useState<'type' | 'config'>('type');
  const [connectorDatabase, setConnectorDatabase] = useState<'postgres' | 'mongodb' | 'elasticsearch' | 's3'>('postgres');
  const [connectorSelectionActive, setConnectorSelectionActive] = useState(false);
  const [databaseSearch, setDatabaseSearch] = useState('');
  const [databaseName, setDatabaseName] = useState('');
  const [expandedDatabases, setExpandedDatabases] = useState<Record<string, boolean>>({});
  const [selectedSchema, setSelectedSchema] = useState('');
  const [databaseAliases, setDatabaseAliases] = useState<Record<string, string>>({ dw_etl: 'ETL warehouse', spider_prod: 'Production', analytics: 'Analytics' });
  const [schemaAliases, setSchemaAliases] = useState<Record<string, string>>({ ts_rds: 'Raw data', ts_ods: 'Operational data', ts_task: 'Task staging', ts_dwd: 'Detail warehouse', ts_dws: 'Summary warehouse' });
  const [layer, setLayer] = useState('rds');
  const [table, setTable] = useState<LayerTable>(mockTables.rds[0]);
  const [partition, setPartition] = useState<string>();
  const [offsetOpen, setOffsetOpen] = useState(false);
  const [offset, setOffset] = useState<number>();
  const [offsets, setOffsets] = useState([{ partition: 0, offset: 184220 }, { partition: 1, offset: 182904 }]);
  const [sql, setSql] = useState('SELECT * FROM ts_rds.rds_news LIMIT 50');
  const [sqlOpen, setSqlOpen] = useState(false);
  const [script, setScript] = useState('');
  const [dryRunRows, setDryRunRows] = useState<Record<string, unknown>[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [draftExists, setDraftExists] = useState(false);
  const [topicIn, setTopicIn] = useState('etl.rds.input');
  const [topicOut, setTopicOut] = useState('etl.rds.processed');
  const [topicEditing, setTopicEditing] = useState(false);
  const [transactionOpen, setTransactionOpen] = useState(false);
  const [cellEditing, setCellEditing] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [statusColumnVisible, setStatusColumnVisible] = useState(true);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [newTableName, setNewTableName] = useState('');
  const [newTableDescription, setNewTableDescription] = useState('');
  const [addedTables, setAddedTables] = useState<Record<string, LayerTable[]>>({});
  const [fields, setFields] = useState([{ id: 'field-1', name: 'record_id', type: 'TEXT', length: '', nullable: false, primary: true, comment: 'Business record identifier' }]);
  const tables = [...(mockTables[layer] ?? []), ...(addedTables[layer] ?? [])];
  const rate = mockLayers.find((item) => item.key === layer)?.rate ?? 0;
  const fieldCount = layer === 'rds' ? 9 : layer === 'ods' ? 22 : layer === 'task' ? 14 : 18;
  const tableDescription = layer === 'rds' ? 'Raw ingestion records from Kafka' : layer === 'ods' ? 'Standardized operational records' : 'Managed ETL output asset';
  const dataRows = [
    { record_id: 'rec_001928', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:42:00' },
    { record_id: 'rec_001929', source: 'patent', data_type: 'document', partition: 1, status: 'ready', updated_at: '2026-08-25 10:41:55' },
    { record_id: 'rec_001930', source: 'navwarn', data_type: 'event', partition: 0, status: 'processing', updated_at: '2026-08-25 10:41:48' },
  ].filter((row) => (partition === undefined || row.partition === mockPartitions.findIndex((item) => item.name === partition) % 2) && (!filterText || Object.values(row).join(' ').toLowerCase().includes(filterText.toLowerCase())));
  const dataColumns = Object.keys(dataRows[0] ?? {}).filter((key) => statusColumnVisible || key !== 'status').map((key) => ({ title: key, dataIndex: key, key }));

  useEffect(() => {
    const first = mockTables[layer]?.[0];
    if (first) selectTable(first);
  }, [layer]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setOffsets((current) => current.map((item) => ({ ...item, offset: item.offset + Math.floor(Math.random() * 4) })));
    }, 1800);
    return () => window.clearInterval(timer);
  }, []);

  const selectTable = (selected: LayerTable) => {
    setCreateOpen(false);
    setTable(selected);
    setPartition(undefined);
    setSql(`SELECT * FROM ${selected.schemaName}.${selected.name} LIMIT 50`);
    setScript(`# ${layer}/${selected.name}\n\ndef handler(message, context):\n    normalized = normalize(message)\n    validate_contract(normalized)\n    return normalized\n`);
    setDryRunRows([]);
    setTopicIn(`etl.${layer}.${selected.name}.input`);
    setTopicOut(layer === 'dws' ? '' : `etl.${layer}.${selected.name}.output`);
    setTopicEditing(false);
  };

  const runDryRun = () => {
    setDryRunRows([
      { record_id: 'rec_001928', normalized_title: 'Example title', quality_score: 0.98, validation: 'PASS' },
      { record_id: 'rec_001929', normalized_title: 'Second title', quality_score: 0.86, validation: 'WARN: source missing' },
    ]);
    message.success('Dry run complete — no data was persisted');
  };

  const updateField = (id: string, key: string, value: string | boolean) => setFields((current) => current.map((field) => field.id === id ? { ...field, [key]: value } : field));
  const addField = (index = fields.length) => setFields((current) => [...current.slice(0, index), { id: crypto.randomUUID(), name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' }, ...current.slice(index)]);
  const removeField = (id: string) => setFields((current) => current.length > 1 ? current.filter((field) => field.id !== id) : current);
  const moveField = (index: number, direction: -1 | 1) => setFields((current) => { const target = index + direction; if (target < 0 || target >= current.length) return current; const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next; });
  const createTable = () => {
    const name = newTableName.trim();
    if (!name || fields.some((field) => !field.name.trim())) { message.warning('Table name and field names are required'); return; }
    const created: LayerTable = { name, schemaName: `ts_${layer}`, partitioned: false, rowCount: 0, size: '0 bytes', updatedAt: 'Just now' };
    setAddedTables((current) => ({ ...current, [layer]: [...(current[layer] ?? []), created] }));
    setDraftExists(false); selectTable(created); setCreateOpen(false); setNewTableName(''); setNewTableDescription(''); message.success('Mock table created');
  };
  const ddlPreview = `CREATE TABLE ts_${layer}.${newTableName || 'new_table'} (\n${fields.map((field) => `  ${field.name || 'field_name'} ${field.type}${field.length ? `(${field.length})` : ''}${field.nullable ? '' : ' NOT NULL'}${field.primary ? ' PRIMARY KEY' : ''}`).join(',\n')}\n);`;
  const databaseOptions = [
    { key: 'postgres' as const, label: 'Postgres', icon: <PostgresIcon className="flow-database-logo flow-postgres-logo" /> },
    { key: 'mongodb' as const, label: 'MongoDB', icon: <MongoDbIcon className="flow-database-logo flow-mongodb-logo" /> },
    { key: 'elasticsearch' as const, label: 'Elasticsearch', icon: <ElasticsearchIcon className="flow-database-logo flow-elasticsearch-logo" /> },
    { key: 's3' as const, label: 'S3', icon: <S3Icon className="flow-database-logo flow-s3-logo" /> },
  ];
  const visibleDatabaseOptions = databaseOptions.filter(({ label }) => label.toLowerCase().includes(databaseSearch.trim().toLowerCase()));
  const selectedDatabaseOption = databaseOptions.find(({ key }) => key === connectorDatabase) ?? databaseOptions[0];
  const changeConnectorStep = (step: 'type' | 'config', database = connectorDatabase) => {
    const update = () => { setConnectorDatabase(database); setConnectorSelectionActive(step === 'config'); if (step === 'config') setDatabaseName(''); setConnectorStep(step); };
    const documentWithTransitions = document as Document & { startViewTransition?: (callback: () => void) => void };
    if (documentWithTransitions.startViewTransition) documentWithTransitions.startViewTransition(update);
    else update();
  };

  return <div className="flow-layers flow-layers--tagged">
    <div className="flow-layer-layout">
      <aside className="flow-layer-tree">
        <div className="flow-tree-label flow-tree-label--actions"><span>Data connectors</span><button type="button" aria-label="Add data connector" title="Add data connector" onClick={() => { setConnectorStep('type'); setConnectorSelectionActive(false); setDatabaseSearch(''); setCollectionOpen(true); }}><PlusOutlined /></button></div>
        {mockLayers.map((item) => <button className={`flow-connector-tree-item ${layer === item.key ? 'is-active' : ''}`} key={item.key} onClick={() => setLayer(item.key)}><PostgresIcon className="flow-connector-tree-icon" /><span className="flow-connector-tree-name"><strong>spider-prod · ts_{item.key}</strong><small>{item.tables} tables</small></span></button>)}
      </aside>
      <section className="flow-layer-main">
        <div className="flow-table-switcher">
        <Dropdown overlayClassName="flow-table-tab-dropdown" menu={{ items: tables.map((item) => ({ key: item.name, label: item.name })), onClick: ({ key }) => { const selected = tables.find((item) => item.name === key); if (selected) selectTable(selected); } }}>
          <button type="button" className="flow-table-tab-menu" aria-label="Select open table"><DownOutlined /></button>
        </Dropdown>
        <div className="flow-table-tabs" role="tablist" aria-label={`${layer} tables`}>
          {tables.map((item) => { const active = !createOpen && table.name === item.name; return <button type="button" role="tab" aria-selected={active} className={active ? 'is-active' : ''} key={item.name} onClick={() => selectTable(item)}>{item.name}</button>; })}
          {draftExists && <button type="button" role="tab" aria-selected={createOpen} className={createOpen ? 'is-active is-draft' : 'is-draft'} onClick={() => setCreateOpen(true)}>* Untitled</button>}
        </div>
        <button type="button" className="flow-table-add-tag" aria-label="Create table" title="Create table" onClick={() => { setDraftExists(true); setCreateOpen(true); setNewTableName(''); setNewTableDescription(''); }}>+</button>
        </div>
        {createOpen ? <div className="flow-card flow-inline-designer">
          <div className="flow-designer-header"><strong>* Untitled @ ts_{layer}</strong><span /><Button size="small" onClick={() => setCreateOpen(false)}>Close</Button><Button type="primary" size="small" onClick={createTable}>Save</Button></div>
          <div className="flow-designer-identity"><Input value={newTableName} onChange={(event) => setNewTableName(event.target.value)} placeholder="Table name" /><Input value={newTableDescription} onChange={(event) => setNewTableDescription(event.target.value)} placeholder="Description" /></div>
          <div className="flow-designer-toolbar"><Button size="small" onClick={() => addField()}>Add field</Button><Button size="small" onClick={() => addField(0)}>Insert field</Button><Button size="small" danger onClick={() => removeField(fields[fields.length - 1]?.id ?? '')}>Delete field</Button><span /><Button size="small" onClick={() => moveField(fields.length - 1, -1)}>Move up</Button><Button size="small" onClick={() => moveField(0, 1)}>Move down</Button></div>
          <Tabs className="flow-designer-tabs" items={[
            { key: 'fields', label: 'Fields', children: <Table size="small" pagination={false} rowKey="id" dataSource={fields} columns={[
              { title: 'Name', dataIndex: 'name', render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'name', event.target.value)} /> },
              { title: 'Type', dataIndex: 'type', width: 130, render: (value, row) => <Select size="small" value={value} onChange={(next) => updateField(row.id, 'type', next)} options={['TEXT','BIGINT','INTEGER','BOOLEAN','TIMESTAMPTZ','JSONB'].map((item) => ({ value: item, label: item }))} /> },
              { title: 'Length', dataIndex: 'length', width: 90, render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'length', event.target.value)} /> },
              { title: 'Not null', dataIndex: 'nullable', width: 80, align: 'center', render: (value, row) => <Checkbox checked={!value} onChange={(event) => updateField(row.id, 'nullable', !event.target.checked)} /> },
              { title: 'Key', dataIndex: 'primary', width: 65, align: 'center', render: (value, row) => <Checkbox checked={value} onChange={(event) => updateField(row.id, 'primary', event.target.checked)} /> },
              { title: 'Comment', dataIndex: 'comment', render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'comment', event.target.value)} /> },
            ]} /> },
            { key: 'indexes', label: 'Indexes', children: <div className="flow-designer-placeholder">Add indexes after defining fields.</div> },
            { key: 'foreign', label: 'Foreign keys', children: <div className="flow-designer-placeholder">No foreign keys defined.</div> },
            { key: 'checks', label: 'Checks', children: <div className="flow-designer-placeholder">No check constraints defined.</div> },
            { key: 'rules', label: 'Rules', children: <div className="flow-designer-placeholder">No table rules defined.</div> },
            { key: 'triggers', label: 'Triggers', children: <div className="flow-designer-placeholder">No triggers defined.</div> },
            { key: 'options', label: 'Options', children: <div className="flow-designer-placeholder">Timescale and partition options.</div> },
            { key: 'sql', label: 'SQL preview', children: <pre className="flow-designer-sql">{ddlPreview}</pre> },
          ]} />
        </div> : <div className="flow-card flow-inspector">
          <div className="flow-card-head flow-table-title"><div><h2>{table.name}</h2><span>{table.rowCount.toLocaleString()} rows</span><span>{table.size}</span><span>{fieldCount} fields</span><p>{tableDescription}</p></div></div>
          <div className="flow-stream-grid flow-health-default">
            <div><span>Health</span><strong className="flow-health-good">Healthy</strong><small>Schema compatible · last check 18s ago</small></div>
            <div className="flow-topic-card flow-topic-pair"><span>Topics</span>{topicEditing ? <div className="flow-topic-editor flow-topic-editor-pair"><Input size="small" value={topicIn} onChange={(event) => setTopicIn(event.target.value)} placeholder="Input topic" /><Input size="small" value={topicOut} onChange={(event) => setTopicOut(event.target.value)} placeholder="Output topic (optional)" /><Button size="small" onClick={() => { setTopicEditing(false); message.success('Topics saved'); }}>Save</Button></div> : <><div className="flow-topic-line"><small>IN</small><strong>{topicIn || 'Not configured'}</strong><em>{rate}/s</em></div><div className="flow-topic-line"><small>OUT</small><strong>{topicOut || 'Not configured'}</strong><em>{topicOut ? Math.max(0, rate - 46) : '—'}</em></div><button type="button" onClick={() => setTopicEditing(true)}>Edit topics</button></>}</div>
            <div className="flow-offset-card"><span>Topic offsets</span><strong>IN · {offsets.map((item) => `p${item.partition}: ${item.offset}`).join(' · ')}</strong><strong>OUT · {topicOut ? offsets.map((item) => `p${item.partition}: ${item.offset - 18}`).join(' · ') : '—'}</strong><small>Live refresh every 1.8s · click to adjust</small><Button danger size="small" onClick={() => { setOffset(offsets[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button></div>
          </div>
          <Tabs items={[
            { key: 'data', label: 'Data preview', children: <>
              <div className="flow-navicat-toolbar"><Button size="small" className={sqlOpen ? 'is-active' : ''} onClick={() => setSqlOpen((value) => !value)}>SQL console</Button><Button size="small" className={transactionOpen ? 'is-active' : ''} onClick={() => { setTransactionOpen((value) => !value); message.info(transactionOpen ? 'Transaction rolled back' : 'Transaction started'); }}>{transactionOpen ? 'Rollback transaction' : 'Start transaction'}</Button><Button size="small" className={cellEditing ? 'is-active' : ''} onClick={() => setCellEditing((value) => !value)}>{cellEditing ? 'Finish editing' : 'Cell editor'}</Button><Button size="small" className={filterOpen ? 'is-active' : ''} onClick={() => setFilterOpen((value) => !value)}>Filter &amp; sort</Button><Button size="small" onClick={() => setStatusColumnVisible((value) => !value)}>{statusColumnVisible ? 'Hide status' : 'Show status'}</Button><Button size="small" onClick={() => setAnalysisOpen(true)}>Data analysis</Button><Button size="small" onClick={() => message.success('Mock rows exported as CSV')}>Export CSV</Button></div>
              {filterOpen && <div className="flow-filter-bar"><Input allowClear size="small" value={filterText} onChange={(event) => setFilterText(event.target.value)} placeholder="Filter current result set" /><Select size="small" defaultValue="updated_desc" options={[{ value: 'updated_desc', label: 'updated_at DESC' }, { value: 'record_asc', label: 'record_id ASC' }]} /></div>}
              {sqlOpen && <div className="flow-sql-bar flow-sql-bar--real"><Input value={sql} onChange={(event) => setSql(event.target.value)} onPressEnter={() => message.success('Query executed')} /><Button type="primary" onClick={() => message.success('Query executed')}>Run</Button></div>}
              <div className="flow-partition-tags">{mockPartitions.map((item) => <Tag.CheckableTag key={item.name} checked={partition === item.name} onChange={() => setPartition(partition === item.name ? undefined : item.name)}>{item.name.replace('p2026_', '')}</Tag.CheckableTag>)}</div>
              <Table className={`flow-data-table ${cellEditing ? 'is-editing' : ''}`} size="small" pagination={{ pageSize: 20, size: 'small' }} rowKey="record_id" scroll={{ x: 920, y: 300 }} dataSource={dataRows} columns={dataColumns.map((column) => cellEditing ? { ...column, render: (value: unknown) => <Input size="small" defaultValue={String(value ?? '')} /> } : column)} />
            </> },
            { key: 'script', label: 'Layer script', children: <div className="flow-script-editor"><div className="flow-script-note">{layer.toUpperCase()} handler · edits are local until a release is created.</div><Input.TextArea value={script} onChange={(event) => setScript(event.target.value)} autoSize={{ minRows: 12, maxRows: 22 }} /><div className="flow-script-actions"><Button type="primary" onClick={runDryRun}>Run dry run</Button><Button onClick={() => message.info('Field contract is valid')}>Validate fields</Button><span>Runs 2 sample records · no writes</span></div>{dryRunRows.length > 0 && <Table size="small" pagination={false} rowKey="record_id" dataSource={dryRunRows} columns={Object.keys(dryRunRows[0]).map((key) => ({ title: key, dataIndex: key, key }))} />}</div> },
          ]} />
        </div>}
      </section>
    </div>
    <Modal title="Adjust Redis offset" open={offsetOpen} onCancel={() => setOffsetOpen(false)} onOk={() => { message.success('Mock offset updated'); setOffsetOpen(false); }} okText="Confirm SET OFFSET"><p>Current offsets update live while this panel is open.</p><div className="flow-offset-live">{offsets.map((item) => <div key={item.partition}><span>Partition {item.partition}</span><strong>{item.offset}</strong></div>)}</div><InputNumber min={0} value={offset} onChange={(value) => setOffset(value ?? undefined)} placeholder="New Kafka offset" style={{ width: '100%' }} /></Modal>
    <Modal title="Data analysis" open={analysisOpen} onCancel={() => setAnalysisOpen(false)} footer={<Button onClick={() => setAnalysisOpen(false)}>Close</Button>}><div className="flow-analysis-grid"><div><span>Visible rows</span><strong>{dataRows.length}</strong></div><div><span>Partitions</span><strong>{new Set(dataRows.map((row) => row.partition)).size}</strong></div><div><span>Ready</span><strong>{dataRows.filter((row) => row.status === 'ready').length}</strong></div></div></Modal>
    <Modal className="flow-table-designer" title={`Create table in ts_${layer}`} open={false} onCancel={() => setCreateOpen(false)} onOk={createTable} okText="Save table" width={920}>
      <div className="flow-designer-identity"><Input value={newTableName} onChange={(event) => setNewTableName(event.target.value)} placeholder="Table name" /><Input value={newTableDescription} onChange={(event) => setNewTableDescription(event.target.value)} placeholder="Description" /></div>
      <div className="flow-designer-toolbar"><Button size="small" onClick={() => addField()}>Add field</Button><Button size="small" onClick={() => addField(0)}>Insert field</Button><Button size="small" danger onClick={() => removeField(fields[fields.length - 1]?.id ?? '')}>Delete field</Button><span /><Button size="small" onClick={() => moveField(fields.length - 1, -1)}>Move up</Button><Button size="small" onClick={() => moveField(0, 1)}>Move down</Button></div>
      <Tabs className="flow-designer-tabs" items={[
        { key: 'fields', label: 'Fields', children: <Table size="small" pagination={false} rowKey="id" dataSource={fields} columns={[
          { title: 'Name', dataIndex: 'name', render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'name', event.target.value)} /> },
          { title: 'Type', dataIndex: 'type', width: 130, render: (value, row) => <Select size="small" value={value} onChange={(next) => updateField(row.id, 'type', next)} options={['TEXT','BIGINT','INTEGER','BOOLEAN','TIMESTAMPTZ','JSONB'].map((item) => ({ value: item, label: item }))} /> },
          { title: 'Length', dataIndex: 'length', width: 90, render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'length', event.target.value)} /> },
          { title: 'Not null', dataIndex: 'nullable', width: 80, align: 'center', render: (value, row) => <Checkbox checked={!value} onChange={(event) => updateField(row.id, 'nullable', !event.target.checked)} /> },
          { title: 'Key', dataIndex: 'primary', width: 65, align: 'center', render: (value, row) => <Checkbox checked={value} onChange={(event) => updateField(row.id, 'primary', event.target.checked)} /> },
          { title: 'Comment', dataIndex: 'comment', render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'comment', event.target.value)} /> },
          { title: '', width: 70, render: (_, row, index) => <span className="flow-field-actions"><button onClick={() => moveField(index, -1)}>↑</button><button onClick={() => moveField(index, 1)}>↓</button><button onClick={() => removeField(row.id)}>×</button></span> },
        ]} /> },
        { key: 'indexes', label: 'Indexes', children: <div className="flow-designer-placeholder">Add indexes after defining fields.</div> },
        { key: 'foreign', label: 'Foreign keys', children: <div className="flow-designer-placeholder">No foreign keys defined.</div> },
        { key: 'checks', label: 'Checks', children: <div className="flow-designer-placeholder">No check constraints defined.</div> },
        { key: 'rules', label: 'Rules', children: <div className="flow-designer-placeholder">No table rules defined.</div> },
        { key: 'triggers', label: 'Triggers', children: <div className="flow-designer-placeholder">No triggers defined.</div> },
        { key: 'options', label: 'Options', children: <div className="flow-designer-placeholder">Timescale and partition options will be configured here.</div> },
        { key: 'comment', label: 'Comment', children: <Input.TextArea value={newTableDescription} onChange={(event) => setNewTableDescription(event.target.value)} rows={5} /> },
        { key: 'sql', label: 'SQL preview', children: <pre className="flow-designer-sql">{ddlPreview}</pre> },
      ]} />
    </Modal>
    <Modal className="flow-connector-modal" closable={false} title={<div className="flow-modal-title-row"><span className={connectorStep === 'type' ? '' : 'flow-modal-database-title'} style={connectorStep === 'config' ? ({ viewTransitionName: `flow-database-${connectorDatabase}` } as React.CSSProperties) : undefined}>{connectorStep === 'type' ? 'New data connector' : <>{selectedDatabaseOption.icon}<strong>{selectedDatabaseOption.label}</strong></>}</span><div className="flow-modal-title-actions">{connectorStep === 'config' && <button type="button" aria-label="Back to database selection" onClick={() => changeConnectorStep('type')}><BackIcon className="flow-modal-action-icon" /></button>}<button type="button" aria-label="Close" onClick={() => setCollectionOpen(false)}><CloseIcon className="flow-modal-action-icon" /></button></div></div>} open={collectionOpen} onCancel={() => setCollectionOpen(false)} footer={null} width={560}>
      {connectorStep === 'type' ? <div className="flow-connector-type-step"><Input className="flow-database-search" prefix={<SearchOutlined />} allowClear value={databaseSearch} onChange={(event) => setDatabaseSearch(event.target.value)} placeholder="Search databases" /><div className="flow-connector-picker">{visibleDatabaseOptions.map(({ key, label, icon }) => <button key={key} type="button" style={{ viewTransitionName: `flow-database-${key}` } as React.CSSProperties} className={`flow-database-option ${connectorSelectionActive && connectorDatabase === key ? 'is-selected' : ''}`} onClick={() => changeConnectorStep('config', key)}>{icon}<strong>{label}</strong></button>)}</div></div> : <div className="flow-connector-config"><div className="flow-tree-toolbar"><button type="button" title="Add database"><PlusOutlined /></button><button type="button" title="Settings"><SettingOutlined /></button><button type="button" title="Delete" disabled={!databaseName}><DeleteOutlined /></button></div><div className="flow-resource-tree flow-database-list">{Object.keys(databaseAliases).map((value) => <React.Fragment key={value}><div className={`flow-tree-resource-row ${databaseName === value ? 'is-selected' : ''}`} onClick={() => { setDatabaseName(value); setExpandedDatabases((current) => ({ ...current, [value]: !current[value] })); }}><button type="button" className="flow-tree-expand" aria-label={`${expandedDatabases[value] ? 'Collapse' : 'Expand'} ${value}`}><CaretRightOutlined rotate={expandedDatabases[value] ? 90 : 0} /></button><button type="button" className="flow-tree-resource-main"><DatabaseOutlined /><strong>{value}</strong><small>{databaseAliases[value]}</small><em>{Object.keys(schemaAliases).length}</em></button></div>{expandedDatabases[value] && <div className="flow-tree-children">{Object.keys(schemaAliases).map((schema) => <div className={`flow-tree-resource-row flow-tree-schema-row ${selectedSchema === schema ? 'is-selected' : ''}`} key={`${value}-${schema}`}><span className="flow-tree-branch" /><ApartmentOutlined /><button type="button" onClick={() => setSelectedSchema(schema)}><strong>{schema}</strong><small>{schemaAliases[schema]}</small></button></div>)}</div>}</React.Fragment>)}</div><Button className="flow-create-connector" size="small" type="primary" onClick={() => { setCollectionOpen(false); message.success('Data connector created (mock)'); }}>Save record</Button></div>}
    </Modal>
  </div>;
};
export default Pipeline;

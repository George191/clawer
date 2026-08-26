import React, { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Button, Checkbox, Dropdown, Input, InputNumber, Modal, Select, Spin, Table, Tag, Tabs, message } from 'antd';
import { AlertOutlined, BranchesOutlined, CodeOutlined, DownOutlined, ExperimentOutlined, RightOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons';
import '../ProductWorkspace/workspace.css';
import type { LayerNode, LayerTable, QueryResult, EtlPartition, EtlStreamState } from '../../services/types';

const pageMeta: Record<string, { title: string; description: string }> = {
  '/flow/layers': { title: 'Data layers', description: 'Browse source-backed tables, partitions, stream positions and deployed layer code.' },
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
  return <div className="flow-layers"><div className="flow-layer-layout"><aside className="flow-layer-tree"><div className="flow-tree-label">ETL layers</div>{layers.map((item) => <button className={layer === item.key ? 'is-active' : ''} key={item.key} onClick={() => setLayer(item.key)}><span>{item.key.toUpperCase()}</span><small>{item.tables ?? 0} tables</small></button>)}</aside><section className="flow-layer-main"><div className="flow-card flow-table-card"><div className="flow-card-head"><div><h2>{layer.toUpperCase()} tables</h2><p>Schema: ts_{layer}</p></div>{loading && <Spin size="small" />}</div><div className="flow-table"><div className="flow-table-row flow-table-head"><span>Table</span><span>Rows</span><span>Size</span><span /></div>{tables.map((item) => <button className={`flow-table-row flow-table-button ${table?.name === item.name ? 'is-selected' : ''}`} key={item.name} onClick={() => inspect(item)}><span><strong>{item.name}</strong><small>{item.partitioned ? 'Partitioned' : item.schemaName}</small></span><span>{item.rowCount}</span><span>{item.size}</span><span><RightOutlined /></span></button>)}</div></div>{table && <div className="flow-card flow-inspector"><div className="flow-card-head"><div><h2>{table.name}</h2><p>Health and stream position</p></div><Button danger size="small" onClick={() => { setOffset(stream?.offsets?.[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button></div><div className="flow-stream-grid flow-health-default"><div><span>Health</span><strong className="flow-health-good">Healthy</strong><small>Schema compatible · last check 18s ago</small></div><div><span>Topic</span><strong>{stream?.topic ?? 'Unavailable'}</strong></div><div><span>Throughput</span><strong>{stream?.throughput ?? 0}/s</strong><small>Mock snapshot</small></div><div><span>Redis offsets</span><strong>{stream?.offsets?.map((item) => `p${item.partition}: ${item.offset}`).join(' · ')}</strong><small>Live refresh every 1.8s</small></div></div><Tabs items={[{ key: 'data', label: 'Data preview', children: <><div className="flow-sql-bar"><Input value={sql} onChange={(event) => setSql(event.target.value)} onPressEnter={runSql} /><Button type="primary" onClick={runSql}>Run</Button></div><div className="flow-partition-tags">{partitions.map((item) => <Tag.CheckableTag key={item.name} checked={partition === item.name} onChange={() => queryPartition(partition === item.name ? undefined : item.name)}>{item.name.replace('p2026_', '')}</Tag.CheckableTag>)}</div><Table className="flow-data-table" size="small" pagination={false} rowKey="record_id" dataSource={data?.rows ?? []} columns={(data?.columns ?? []).map((key) => ({ title: key, dataIndex: key, key }))} /></> }, { key: 'script', label: 'Layer script', children: <div className="flow-script-editor"><div className="flow-script-note">{layer.toUpperCase()} handler · edits are local until a release is created.</div><Input.TextArea value={script} onChange={(event) => setScript(event.target.value)} autoSize={{ minRows: 12, maxRows: 22 }} /><div className="flow-script-actions"><Button type="primary" onClick={runDryRun}>Run dry run</Button><Button onClick={() => message.info('Changes are local only and were not persisted')}>Validate fields</Button><span>Runs 2 sample records · no writes</span></div>{dryRunRows.length > 0 && <Table size="small" pagination={false} rowKey="record_id" dataSource={dryRunRows} columns={Object.keys(dryRunRows[0]).map((key) => ({ title: key, dataIndex: key, key }))} />}</div> }]} /></div>}</section></div><Modal title="Adjust Redis offset" open={offsetOpen} onCancel={() => setOffsetOpen(false)} onOk={saveOffset} okText="Confirm SET OFFSET"><p>Current offset updates live while this panel is open. Changes apply after the worker restarts.</p><div className="flow-offset-live">{stream?.offsets?.map((item) => <div key={item.partition}><span>Partition {item.partition}</span><strong>{item.offset}</strong></div>)}</div><InputNumber min={0} value={offset} onChange={(value) => setOffset(value ?? undefined)} placeholder="New Kafka offset" style={{ width: '100%' }} /></Modal></div>;
};

const DataLayersWorkspace: React.FC = () => {
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
  const [topic, setTopic] = useState('etl.rds.processed');
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
    setTopic(`etl.${layer}.processed`);
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

  return <div className="flow-layers flow-layers--tagged">
    <div className="flow-layer-layout">
      <aside className="flow-layer-tree">
        <div className="flow-tree-label">ETL layers</div>
        {mockLayers.map((item) => <button className={layer === item.key ? 'is-active' : ''} key={item.key} onClick={() => setLayer(item.key)}><span>{item.key.toUpperCase()}</span><small>{item.tables} tables</small></button>)}
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
            <div className="flow-topic-card"><span>Topic</span>{topicEditing ? <div className="flow-topic-editor"><Input size="small" value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="Kafka topic" /><Button size="small" onClick={() => { setTopicEditing(false); message.success(topic ? 'Topic saved' : 'Topic cleared'); }}>Save</Button></div> : <><strong>{topic || 'Not configured'}</strong><button type="button" onClick={() => setTopicEditing(true)}>{topic ? 'Edit' : 'Add topic'}</button></>}</div>
            <div><span>Throughput</span><strong>{rate}/s</strong><small>Mock snapshot</small></div>
            <div className="flow-offset-card"><span>Redis offsets</span><strong>{offsets.map((item) => `p${item.partition}: ${item.offset}`).join(' · ')}</strong><small>Live refresh every 1.8s</small><Button danger size="small" onClick={() => { setOffset(offsets[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button></div>
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
  </div>;
};
export default Pipeline;

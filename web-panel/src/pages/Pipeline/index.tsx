import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Checkbox, ConfigProvider, Dropdown, Input, InputNumber, Modal, Select, Spin, Switch, Table, Tag, Tabs, message } from 'antd';
import enUS from 'antd/locale/en_US';
import { AlertOutlined, ApartmentOutlined, BranchesOutlined, CaretRightFilled, CaretRightOutlined, CheckOutlined, CloseOutlined, CodeOutlined, DatabaseOutlined, DeleteOutlined, DownOutlined, DownloadOutlined, EditOutlined, ExperimentOutlined, PauseOutlined, PlusOutlined, RightOutlined, SearchOutlined, SettingOutlined, UploadOutlined } from '@ant-design/icons';
import '../ProductWorkspace/workspace.css';
import type { LayerNode, LayerTable, QueryResult, EtlPartition, EtlStreamState } from '../../services/types';

const pageMeta: Record<string, { title: string; description: string }> = {
  '/flow': { title: 'Flow overview', description: 'Live topology of connectors, topics and ETL layers.' },
  '/flow/layers': { title: 'Data connectors', description: 'Browse PostgreSQL schemas, tables, stream positions and deployed connector code.' },
  '/flow/transforms': { title: 'Transformation assets', description: 'Review deployed transformations, their dependencies and validation state.' },
  '/flow/quality': { title: 'Quality gates', description: 'Assess output contracts before downstream publication.' },
  '/flow/lineage': { title: 'Lineage and impact', description: 'Trace ETL dependencies and assess downstream impact.' },
};
const mockSchemas: LayerNode[] = [
  { key: 'ts_rds', label: 'RDS', icon: 'DatabaseOutlined', status: 'running', rateIn: 842, rateOut: 796, lag: 12, tables: 12 },
  { key: 'ts_ods', label: 'ODS', icon: 'FolderOutlined', status: 'running', rateIn: 796, rateOut: 624, lag: 8, tables: 28 },
  { key: 'ts_task', label: 'TASK', icon: 'ScheduleOutlined', status: 'running', rateIn: 624, rateOut: 588, lag: 21, tables: 16 },
  { key: 'ts_dwd', label: 'DWD', icon: 'TableOutlined', status: 'running', rateIn: 588, rateOut: 210, lag: 4, tables: 46 },
  { key: 'ts_dws', label: 'DWS', icon: 'BarChartOutlined', status: 'running', rateIn: 210, rateOut: 164, lag: 2, tables: 19 },
  { key: 'ts_ads', label: 'ADS', icon: 'ApartmentOutlined', status: 'running', rateIn: 164, rateOut: 0, lag: 0, tables: 7 },
];
const mockTables: Record<string, LayerTable[]> = {
  ts_rds: [{ name: 'rds_news', schemaName: 'ts_rds', partitioned: true, rowCount: 128400, size: '1.8 GB', updatedAt: '2 min ago' }, { name: 'rds_patent', schemaName: 'ts_rds', partitioned: true, rowCount: 84210, size: '940 MB', updatedAt: '4 min ago' }],
  ts_ods: [{ name: 'ods_news', schemaName: 'ts_ods', partitioned: true, rowCount: 127980, size: '2.4 GB', updatedAt: '3 min ago' }, { name: 'ods_patent', schemaName: 'ts_ods', partitioned: true, rowCount: 83950, size: '1.1 GB', updatedAt: '5 min ago' }],
  ts_task: [{ name: 'task_document_enrichment', schemaName: 'ts_task', partitioned: false, rowCount: 39120, size: '620 MB', updatedAt: '8 min ago' }],
  ts_dwd: [{ name: 'dwd_vessel_events', schemaName: 'ts_dwd', partitioned: true, rowCount: 5620040, size: '18.2 GB', updatedAt: '1 min ago' }],
  ts_dws: [{ name: 'dws_daily_activity', schemaName: 'ts_dws', partitioned: false, rowCount: 18420, size: '88 MB', updatedAt: '12 min ago' }],
};
const mockPartitions: EtlPartition[] = [{ name: 'p2026_08_24' }, { name: 'p2026_08_25' }, { name: 'p2026_08_26' }];
const PostgresIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Postgres" fill="currentColor"><path fillRule="evenodd" clipRule="evenodd" d="M23.5594 14.7228a.5269.5269 0 0 0-.0563-.1191c-.139-.2632-.4768-.3418-1.0074-.2321-1.6533.3411-2.2935.1312-2.5256-.0191 1.342-2.0482 2.445-4.522 3.0411-6.8297.2714-1.0507.7982-3.5237.1222-4.7316a1.5641 1.5641 0 0 0-.1509-.235C21.6931.9086 19.8007.0248 17.5099.0005c-1.4947-.0158-2.7705.3461-3.1161.4794a9.449 9.449 0 0 0-.5159-.0816 8.044 8.044 0 0 0-1.3114-.1278c-1.1822-.0184-2.2038.2642-3.0498.8406-.8573-.3211-4.7888-1.645-7.2219.0788C.9359 2.1526.3086 3.8733.4302 6.3043c.0409.818.5069 3.334 1.2423 5.7436.4598 1.5065.9387 2.7019 1.4334 3.582.553.9942 1.1259 1.5933 1.7143 1.7895.4474.1491 1.1327.1441 1.8581-.7279.8012-.9635 1.5903-1.8258 1.9446-2.2069.4351.2355.9064.3625 1.39.3772a.0569.0569 0 0 0 .0004.0041 11.0312 11.0312 0 0 0-.2472.3054c-.3389.4302-.4094.5197-1.5002.7443-.3102.064-1.1344.2339-1.1464.8115-.0025.1224.0329.2309.0919.3268.2269.4231.9216.6097 1.015.6331 1.3345.3335 2.5044.092 3.3714-.6787-.017 2.231.0775 4.4174.3454 5.0874.2212.5529.7618 1.9045 2.4692 1.9043.2505 0 .5263-.0291.8296-.0941 1.7819-.3821 2.5557-1.1696 2.855-2.9059.1503-.8707.4016-2.8753.5388-4.1012.0169-.0703.0357-.1207.057-.1362.0007-.0005.0697-.0471.4272.0307a.3673.3673 0 0 0 .0443.0068l.2539.0223.0149.001c.8468.0384 1.9114-.1426 2.5312-.4308.6438-.2988 1.8057-1.0323 1.5951-1.6698zM2.371 11.8765c-.7435-2.4358-1.1779-4.8851-1.2123-5.5719-.1086-2.1714.4171-3.6829 1.5623-4.4927 1.8367-1.2986 4.8398-.5408 6.108-.13-.0032.0032-.0066.0061-.0098.0094-2.0238 2.044-1.9758 5.536-1.9708 5.7495-.0002.0823.0066.1989.0162.3593.0348.5873.0996 1.6804-.0735 2.9184-.1609 1.1504.1937 2.2764.9728 3.0892.0806.0841.1648.1631.2518.2374-.3468.3714-1.1004 1.1926-1.9025 2.1576-.5677.6825-.9597.5517-1.0886.5087-.3919-.1307-.813-.5871-1.2381-1.3223-.4796-.839-.9635-2.0317-1.4155-3.5126zm6.0072 5.0871c-.1711-.0428-.3271-.1132-.4322-.1772.0889-.0394.2374-.0902.4833-.1409 1.2833-.2641 1.4815-.4506 1.9143-1.0002.0992-.126.2116-.2687.3673-.4426a.3549.3549 0 0 0 .0737-.1298c.1708-.1513.2724-.1099.4369-.0417.156.0646.3078.26.3695.4752.0291.1016.0619.2945-.0452.4444-.9043 1.2658-2.2216 1.2494-3.1676 1.0128zm2.094-3.988-.0525.141c-.133.3566-.2567.6881-.3334 1.003-.6674-.0021-1.3168-.2872-1.8105-.8024-.6279-.6551-.9131-1.5664-.7825-2.5004.1828-1.3079.1153-2.4468.079-3.0586-.005-.0857-.0095-.1607-.0122-.2199.2957-.2621 1.6659-.9962 2.6429-.7724.4459.1022.7176.4057.8305.928.5846 2.7038.0774 3.8307-.3302 4.7363-.084.1866-.1633.3629-.2311.5454zm7.3637 4.5725c-.0169.1768-.0358.376-.0618.5959l-.146.4383a.3547.3547 0 0 0-.0182.1077c-.0059.4747-.054.6489-.115.8693-.0634.2292-.1353.4891-.1794 1.0575-.11 1.4143-.8782 2.2267-2.4172 2.5565-1.5155.3251-1.7843-.4968-2.0212-1.2217a6.5824 6.5824 0 0 0-.0769-.2266c-.2154-.5858-.1911-1.4119-.1574-2.5551.0165-.5612-.0249-1.9013-.3302-2.6462.0044-.2932.0106-.5909.019-.8918a.3529.3529 0 0 0-.0153-.1126 1.4927 1.4927 0 0 0-.0439-.208c-.1226-.4283-.4213-.7866-.7797-.9351-.1424-.059-.4038-.1672-.7178-.0869.067-.276.1831-.5875.309-.9249l.0529-.142c.0595-.16.134-.3257.213-.5012.4265-.9476 1.0106-2.2453.3766-5.1772-.2374-1.0981-1.0304-1.6343-2.2324-1.5098-.7207.0746-1.3799.3654-1.7088.5321a5.6716 5.6716 0 0 0-.1958.1041c.0918-1.1064.4386-3.1741 1.7357-4.4823a4.0306 4.0306 0 0 1 .3033-.276.3532.3532 0 0 0 .1447-.0644c.7524-.5706 1.6945-.8506 2.802-.8325.4091.0067.8017.0339 1.1742.081 1.939.3544 3.2439 1.4468 4.0359 2.3827.8143.9623 1.2552 1.9315 1.4312 2.4543-1.3232-.1346-2.2234.1268-2.6797.779-.9926 1.4189.543 4.1729 1.2811 5.4964.1353.2426.2522.4522.2889.5413.2403.5825.5515.9713.7787 1.2552.0696.087.1372.1714.1885.245-.4008.1155-1.1208.3825-1.0552 1.717-.0123.1563-.0423.4469-.0834.8148-.0461.2077-.0702.4603-.0994.7662zm.8905-1.6211c-.0405-.8316.2691-.9185.5967-1.0105a2.8566 2.8566 0 0 0 .135-.0406 1.202 1.202 0 0 0 .1342.103c.5703.3765 1.5823.4213 3.0068.1344-.2016.1769-.5189.3994-.9533.6011-.4098.1903-1.0957.333-1.7473.3636-.7197.0336-1.0859-.0807-1.1721-.151zm.5695-9.2712c-.0059.3508-.0542.6692-.1054 1.0017-.055.3576-.112.7274-.1264 1.1762-.0142.4368.0404.8909.0932 1.3301.1066.887.216 1.8003-.2075 2.7014a3.5272 3.5272 0 0 1-.1876-.3856c-.0527-.1276-.1669-.3326-.3251-.6162-.6156-1.1041-2.0574-3.6896-1.3193-4.7446.3795-.5427 1.3408-.5661 2.1781-.463zm.2284 7.0137a12.3762 12.3762 0 0 0-.0853-.1074l-.0355-.0444c.7262-1.1995.5842-2.3862.4578-3.4385-.0519-.4318-.1009-.8396-.0885-1.2226.0129-.4061.0666-.7543.1185-1.0911.0639-.415.1288-.8443.1109-1.3505.0134-.0531.0188-.1158.0118-.1902-.0457-.4855-.5999-1.938-1.7294-3.253-.6076-.7073-1.4896-1.4972-2.6889-2.0395.5251-.1066 1.2328-.2035 2.0244-.1859 2.0515.0456 3.6746.8135 4.8242 2.2824a.908.908 0 0 1 .0667.1002c.7231 1.3556-.2762 6.2751-2.9867 10.5405zm-8.8166-6.1162c-.025.1794-.3089.4225-.6211.4225a.5821.5821 0 0 1-.0809-.0056c-.1873-.026-.3765-.144-.5059-.3156-.0458-.0605-.1203-.178-.1055-.2844.0055-.0401.0261-.0985.0925-.1488.1182-.0894.3518-.1226.6096-.0867.3163.0441.6426.1938.6113.4186zm7.9305-.4114c.0111.0792-.049.201-.1531.3102-.0683.0717-.212.1961-.4079.2232a.5456.5456 0 0 1-.075.0052c-.2935 0-.5414-.2344-.5607-.3717-.024-.1765.2641-.3106.5611-.352.297-.0414.6111.0088.6356.1851z" /></svg>;
const MongoDbIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="MongoDB" fill="currentColor"><path d="M17.193 9.555c-1.264-5.58-4.252-7.414-4.573-8.115-.28-.394-.53-.954-.735-1.44-.036.495-.055.685-.523 1.184-.723.566-4.438 3.682-4.74 10.02-.282 5.912 4.27 9.435 4.888 9.884l.07.05A73.49 73.49 0 0 1 11.91 24h.481c.114-1.032.284-2.056.51-3.07.417-.296.604-.463.85-.693a11.342 11.342 0 0 0 3.639-8.464c.01-.814-.103-1.662-.197-2.218zm-5.336 8.195s0-8.291.275-8.29c.213 0 .49 10.695.49 10.695-.381-.045-.765-1.76-.765-2.405z" /></svg>;
const ElasticsearchIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 512 512" role="img" aria-label="Elasticsearch" fillRule="evenodd" clipRule="evenodd" strokeLinejoin="round"><path d="M21.625 256c0 21.62 3.035 42.495 8.195 62.5h304.304c34.516 0 62.5-27.985 62.5-62.5 0-34.516-27.984-62.5-62.5-62.5H29.82a249.101 249.101 0 0 0-8.195 62.5" fill="#343741" /><path d="M442.308 125.718a240.051 240.051 0 0 0 24.324-25.968C420.796 42.637 350.527 6 271.624 6 172.855 6 87.863 63.46 47.304 146.625h341.664a78.627 78.627 0 0 0 53.328-20.907" fill="#fec514" /><path d="M388.968 365.374H47.316C87.88 448.538 172.856 506 271.624 506c78.907 0 149.172-36.652 195.008-93.75a238.559 238.559 0 0 0-24.324-25.968 78.682 78.682 0 0 0-53.34-20.907" fill="#00bfb3" /></svg>;
const S3Icon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 512 512" role="img" aria-label="S3"><rect width="512" height="512" rx="15%" fill="#fff"/><path fill="#e05243" d="M260 348l-137 33V131l137 32z"/><path fill="#8c3123" d="M256 349l133 32V131l-133 32v186"/><path fill="#e05243" d="M256 64v97l58 14V93zm133 67v250l26-13V143zm-133 77v97l58-8v-82zm58 129l-58 14v97l58-29z"/><path fill="#8c3123" d="M256 448V351l-58-14v82zm-133-67V131l-26 13v238zm133-77v-97l-58 8v82zm-58-129l58-14V64l-58 29z"/><path fill="#5e1f18" d="M314 175l-58 11-58-11 58-15 58 15"/><path fill="#f2b0a9" d="M314 337l-58-11-58 11 58 16 58-16"/></svg>;
const NavicatConnectionIcon: React.FC<{ className?: string }> = ({ className }) => <PostgresIcon className={className} />;
const BackIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Back" fill="none"><path d="M15.5 6.5L10 12l5.5 5.5M10.5 12H19" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>;
const CloseIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Close" fill="none"><path d="M7 7l10 10M17 7L7 17" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>;
const EditIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Edit" fill="none"><path d="M5.5 17.8V19h1.2l9.9-9.9-1.2-1.2-9.9 9.9Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"/><path d="m14.7 7.9 1.4-1.4a1.7 1.7 0 0 1 2.4 2.4l-1.4 1.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>;
const DeleteIcon: React.FC<{ className?: string }> = ({ className }) => <svg className={className} viewBox="0 0 24 24" role="img" aria-label="Delete" fill="none"><path d="M6.5 7.5h11M9.5 7.5V5.8h5v1.7m-6.7 0 .7 11h5l.7-11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>;
const SqlEditor: React.FC<{ value: string; onChange: (value: string) => void; onRun: () => void; executing: boolean; suggestionItems: string[] }> = ({ value, onChange, onRun, executing, suggestionItems }) => {
  const editorRef = React.useRef<HTMLTextAreaElement>(null);
  const [cursor, setCursor] = React.useState(value.length);
  const keywords = ['SELECT', 'FROM', 'WHERE', 'ORDER BY', 'GROUP BY', 'LIMIT', 'JOIN', 'AND', 'OR', 'AS', 'INSERT', 'UPDATE', 'DELETE', ...suggestionItems];
  const beforeCursor = value.slice(0, cursor);
  const tokenMatch = beforeCursor.match(/[\w$.]+$/);
  const rawToken = tokenMatch?.[0] ?? '';
  const lastToken = rawToken.toUpperCase();
  const suggestions = lastToken ? keywords.filter((item) => item.toUpperCase().startsWith(lastToken) && item.toUpperCase() !== lastToken) : keywords.slice(0, 8);
  const [activeSuggestion, setActiveSuggestion] = React.useState(0);
  React.useEffect(() => setActiveSuggestion(0), [lastToken]);
  const highlighted = value.split(/(--.*?$|'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b|\b(?:SELECT|FROM|WHERE|ORDER|BY|GROUP|LIMIT|JOIN|AND|OR|AS|INSERT|UPDATE|DELETE|CREATE|TABLE|PRIMARY|KEY|NULL|NOT)\b)/gim).map((token, index) => { const cls = /^--/.test(token) ? 'is-comment' : /^'/.test(token) ? 'is-string' : /^\d/.test(token) ? 'is-number' : /^(SELECT|FROM|WHERE|ORDER|BY|GROUP|LIMIT|JOIN|AND|OR|AS|INSERT|UPDATE|DELETE|CREATE|TABLE|PRIMARY|KEY|NULL|NOT)$/i.test(token) ? 'is-keyword' : undefined; return <span className={cls} key={`${index}-${token}`}>{token}</span>; });
  const pickSuggestion = (item: string) => { const next = `${value.slice(0, cursor - rawToken.length)}${item} ${value.slice(cursor)}`; onChange(next); window.requestAnimationFrame(() => { const position = cursor - rawToken.length + item.length + 1; editorRef.current?.focus(); editorRef.current?.setSelectionRange(position, position); setCursor(position); }); };
  const lineCount = beforeCursor.split('\n').length;
  return <div className="flow-sql-editor"><pre aria-hidden="true">{highlighted}</pre><textarea ref={editorRef} value={value} onChange={(event) => { onChange(event.target.value); setCursor(event.target.selectionStart); }} onSelect={(event) => setCursor(event.currentTarget.selectionStart)} spellCheck={false} onKeyDown={(event) => { if (event.key === 'ArrowDown' && suggestions.length) { event.preventDefault(); setActiveSuggestion((current) => (current + 1) % suggestions.length); } else if (event.key === 'ArrowUp' && suggestions.length) { event.preventDefault(); setActiveSuggestion((current) => (current - 1 + suggestions.length) % suggestions.length); } else if (event.key === 'Enter' && suggestions.length && !event.ctrlKey && !event.metaKey) { event.preventDefault(); pickSuggestion(suggestions[activeSuggestion]); } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); onRun(); } }} />{suggestions.length > 0 && <div className="flow-sql-suggestions" style={{ left: `${Math.min(300, Math.max(8, (rawToken.length + 1) * 6))}px`, top: `${Math.max(28, lineCount * 15 + 8)}px` }}>{suggestions.slice(0, 8).map((item, index) => <button type="button" className={index === activeSuggestion ? 'is-active' : ''} key={item} onMouseDown={(event) => event.preventDefault()} onClick={() => pickSuggestion(item)}>{item}</button>)}</div>}<button type="button" className={`flow-sql-run ${executing ? 'is-paused' : ''}`} aria-label={executing ? 'Pause query' : 'Run query'} title={executing ? 'Pause query' : 'Run query'} onClick={onRun}>{executing ? <PauseOutlined /> : <CaretRightFilled />}</button></div>;
};
const SqlPreview: React.FC<{ sql: string }> = React.memo(({ sql }) => {
  const tokens = sql.split(/(--.*?$|'(?:''|[^'])*'|\b(?:CREATE|TABLE|PRIMARY|KEY|NOT|NULL|DEFAULT|UNIQUE|REFERENCES|CONSTRAINT|CHECK|ON|DELETE|UPDATE|CASCADE|RESTRICT|SET|TEXT|BIGINT|INTEGER|BOOLEAN|TIMESTAMPTZ|JSONB)\b|\b\d+(?:\.\d+)?\b)/gim);
  return <pre className="flow-td-sql-preview">{tokens.map((token, index) => {
    const className = /^--/.test(token) ? 'is-comment'
      : /^'/.test(token) ? 'is-string'
        : /^\d/.test(token) ? 'is-number'
          : /^(CREATE|TABLE|PRIMARY|KEY|NOT|NULL|DEFAULT|UNIQUE|REFERENCES|CONSTRAINT|CHECK|ON|DELETE|UPDATE|CASCADE|RESTRICT|SET|TEXT|BIGINT|INTEGER|BOOLEAN|TIMESTAMPTZ|JSONB)$/i.test(token) ? 'is-keyword'
            : undefined;
    return <span className={className} key={`${index}-${token}`}>{token}</span>;
  })}</pre>;
});

const Pipeline: React.FC = () => {
  const { pathname } = useLocation();
  const active = pathname === '/flow' ? '/flow' : '/flow/layers';
  const page = pageMeta[active];
  return <main className="flow-console" data-testid="pipeline-workspace"><header className="flow-header"><div><div className="flow-eyebrow"><span className="flow-live-dot" /> ETL LIFECYCLE</div><h1>{page.title}</h1><p>{page.description}</p></div></header><section className="flow-core"><LifecycleView mode={active} /></section></main>;
};
const FlowHome: React.FC = () => {
  const navigate = useNavigate();
  const mapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layerRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const schemas = mockSchemas;
  const totalIn = schemas.reduce((sum, s) => sum + s.rateIn, 0);
  const totalOut = schemas.reduce((sum, s) => sum + s.rateOut, 0);

  const handleSchemaClick = (schema: string) => {
    // Record exact positions of all 6 layer cards for FLIP transition
    const positions: Record<string, { x: number; y: number; w: number; h: number }> = {};
    schemas.forEach((item) => {
      const el = layerRefs.current[item.key];
      if (el) {
        const rect = el.getBoundingClientRect();
        positions[item.key] = { x: rect.left, y: rect.top, w: rect.width, h: rect.height };
      }
    });
    sessionStorage.setItem('flow-transition', JSON.stringify({
      clicked: schema,
      positions,
      timestamp: Date.now(),
    }));
    navigate(`/flow/layers?resource=dw_etl&schema=${schema}`);
  };
  const dataFlow = [
    { route: 'ts_rds', reverse: false },
    { route: 'ts_ods', reverse: true },
    { route: 'ts_ods', reverse: false },
    { route: 'ts_task', reverse: true },
    { route: 'ts_task', reverse: false },
    { route: 'ts_dwd', reverse: true },
    { route: 'ts_dwd', reverse: false },
    { route: 'ts_dws', reverse: true },
    { route: 'ts_dws', reverse: false },
    { route: 'ts_ads', reverse: true },
  ];
  useLayoutEffect(() => {
    const map = mapRef.current;
    const canvas = canvasRef.current;
    if (!map || !canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const core = map.querySelector<HTMLElement>('.flow-home-core');
    const nodes = Array.from(map.querySelectorAll<HTMLElement>('.flow-home-layer'));
    if (!core || !nodes.length) return;
    let animationFrame = 0;
    let disposed = false;
    let previousGeometry = '';
    let frameCount = 0;
    let hoverTransitionUntil = 0;
    let geometryDirty = true;
    let coreX = 0;
    let coreY = 0;
    const curves: Record<string, { startX: number; startY: number; controlX: number; endY: number; endX: number }> = {};
    const startedAt = performance.now();
    const stageDuration = 1900;
    const trackHoverTransition = () => { hoverTransitionUntil = performance.now() + 260; };
    nodes.forEach((node) => {
      node.addEventListener('pointerenter', trackHoverTransition);
      node.addEventListener('pointerleave', trackHoverTransition);
    });
    const resizeObserver = new ResizeObserver(() => {
      geometryDirty = true;
    });
    resizeObserver.observe(map);
    resizeObserver.observe(core);
    nodes.forEach((node) => resizeObserver.observe(node));
    const beamEase = (progress: number) => {
      const sample = (t: number, a: number, b: number) => 3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t ** 2 * b + t ** 3;
      const derivative = (t: number, a: number, b: number) => 3 * (1 - t) ** 2 * a + 6 * (1 - t) * t * (b - a) + 3 * t ** 2 * (1 - b);
      let t = progress;
      for (let iteration = 0; iteration < 4; iteration += 1) {
        const slope = derivative(t, .76, .24);
        if (Math.abs(slope) < .001) break;
        t = Math.max(0, Math.min(1, t - (sample(t, .76, .24) - progress) / slope));
      }
      return sample(t, 0, 1);
    };
    const updateRoutes = () => {
      if (disposed) return;
      const now = performance.now();
      const shouldSyncGeometry = !previousGeometry
        || now < hoverTransitionUntil
        || geometryDirty
        || (!geometryDirty && frameCount++ % 30 === 0);
      if (shouldSyncGeometry) {
        const mapRect = map.getBoundingClientRect();
        const coreRect = core.getBoundingClientRect();
        const nodeRects = nodes.map((node) => node.getBoundingClientRect());
        const coreCenterX = coreRect.left + coreRect.width / 2;
        const coreCenterY = coreRect.top + coreRect.height / 2;
        const geometry = [mapRect.width, mapRect.height, coreRect.left, coreRect.top, coreRect.width, coreRect.height, ...nodeRects.flatMap((rect) => [rect.left, rect.top, rect.width, rect.height])].join(':');
        if (geometry !== previousGeometry) {
          nodeRects.forEach((nodeRect, index) => {
            const onLeft = nodeRect.left + nodeRect.width / 2 < coreCenterX;
            const startX = (onLeft ? nodeRect.right : nodeRect.left) - mapRect.left;
            const startY = nodeRect.top + nodeRect.height / 2 - mapRect.top;
            const endX = (onLeft ? coreRect.left : coreRect.right) - mapRect.left;
            const endY = coreCenterY - mapRect.top;
            const controlX = startX + (endX - startX) * .55;
            const key = schemas[index].key;
            curves[key] = { startX, startY, controlX, endY, endX };
          });
          const dpr = Math.min(window.devicePixelRatio || 1, 2);
          canvas.width = Math.round(mapRect.width * dpr);
          canvas.height = Math.round(mapRect.height * dpr);
          context.setTransform(dpr, 0, 0, dpr, 0, 0);
          coreX = coreCenterX - mapRect.left;
          coreY = coreCenterY - mapRect.top;
          previousGeometry = geometry;
          geometryDirty = false;
        }
      }
      const elapsed = performance.now() - startedAt;
      let coreActivity = 0;
      context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      context.save();
      context.strokeStyle = 'rgba(155,124,255,.72)';
      context.lineWidth = 1;
      context.setLineDash([6, 6]);
      context.lineCap = 'round';
      Object.values(curves).forEach((curve) => {
        context.beginPath();
        context.moveTo(curve.startX, curve.startY);
        context.bezierCurveTo(curve.controlX, curve.startY, curve.controlX, curve.endY, curve.endX, curve.endY);
        context.stroke();
      });
      context.restore();
      const pointOnCurve = (curve: typeof curves[string], progress: number) => {
        const t = progress;
        const inverse = 1 - t;
        return {
          x: inverse * inverse * inverse * curve.startX + 3 * inverse * inverse * t * curve.controlX + 3 * inverse * t * t * curve.controlX + t * t * t * curve.endX,
          y: inverse * inverse * inverse * curve.startY + 3 * inverse * inverse * t * curve.startY + 3 * inverse * t * t * curve.endY + t * t * t * curve.endY,
        };
      };
      for (let packetIndex = 0; packetIndex < 8; packetIndex += 1) {
        const packetElapsed = elapsed - packetIndex * stageDuration;
        if (packetElapsed < 0) continue;
        const cycleElapsed = packetElapsed % (stageDuration * dataFlow.length);
        const stage = dataFlow[Math.floor(cycleElapsed / stageDuration)];
        const curve = curves[stage.route];
        if (!curve) continue;
        const stageProgress = (cycleElapsed % stageDuration) / stageDuration;
        const travelProgress = beamEase(stageProgress);
        const point = pointOnCurve(curve, stage.reverse ? 1 - travelProgress : travelProgress);
        const opacity = Math.min(stageProgress * 12, (1 - stageProgress) * 12, 1);
        context.globalAlpha = opacity;
        context.fillStyle = '#c4b5fd';
        context.shadowColor = '#8b5cf6';
        context.shadowBlur = 7;
        context.beginPath();
        context.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
        context.fill();
        coreActivity = Math.max(coreActivity, Math.max(0, ((stage.reverse ? 1 - stageProgress : stageProgress) - .72) / .28));
      }
      context.globalAlpha = 1;
      context.shadowBlur = 5;
      for (let index = 0; index < 3; index += 1) {
        const angle = performance.now() / 120 + index * (Math.PI * 2 / 3);
        const radius = 50 + index * 5;
        context.globalAlpha = coreActivity * (1 - index * .12);
        context.fillStyle = '#e5ddff';
        context.beginPath();
        context.arc(coreX + Math.cos(angle) * radius, coreY + Math.sin(angle) * radius, 2.4, 0, Math.PI * 2);
        context.fill();
      }
      context.globalAlpha = 1;
      animationFrame = window.requestAnimationFrame(updateRoutes);
    };
    animationFrame = window.requestAnimationFrame(updateRoutes);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      nodes.forEach((node) => {
        node.removeEventListener('pointerenter', trackHoverTransition);
        node.removeEventListener('pointerleave', trackHoverTransition);
      });
    };
  }, []);
  return <div className="flow-home">
    <div className="flow-home-heading"><div><div className="flow-eyebrow"><span className="flow-live-dot" /> FLOW OVERVIEW</div><h2>Connections in motion</h2><p>Watch data move through each ETL layer.</p></div><Button type="primary" onClick={() => navigate('/flow/layers')}>Open workspace <RightOutlined /></Button></div>
    <div className="flow-home-map" ref={mapRef}>
      <canvas className="flow-home-canvas" ref={canvasRef} aria-hidden="true" />
      <div className="flow-home-sequence">{schemas.map((item, index) => <React.Fragment key={item.key}><button type="button" ref={(el) => { layerRefs.current[item.key] = el; }} className="flow-home-layer" onClick={() => handleSchemaClick(item.key)}><NavicatConnectionIcon /><span className="flow-home-layer-body"><span className="flow-home-layer-identity"><strong>{item.label}</strong><small>{item.key}</small></span><span className="flow-home-layer-rate"><i>IN</i><b>{item.rateIn.toLocaleString()}</b><i>OUT</i><b>{item.rateOut.toLocaleString()}</b></span></span></button>{index < schemas.length - 1 && <span className="flow-home-connector"><b>FLOW</b><i /></span>}</React.Fragment>)}</div>
      <div className="flow-home-core" aria-hidden="true"><span className="flow-home-core-label">FLOW</span><strong>Asiral Helio</strong><div className="flow-home-core-throughput"><span><em>IN</em><b>{totalIn.toLocaleString()}</b></span><span><em>OUT</em><b>{totalOut.toLocaleString()}</b></span></div></div>
    </div>
    <div className="flow-home-status"><span><i />6 layers running</span><span>IN {totalIn.toLocaleString()} · OUT {totalOut.toLocaleString()} msg/s</span><span>Click a layer to open its resource</span></div>
  </div>;
};
const LifecycleView: React.FC<{ mode: string }> = ({ mode }) => {
  if (mode === '/flow') return <FlowHome />;
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
  const [schemas, setSchemas] = useState<LayerNode[]>([]); const [schema, setSchema] = useState('ts_rds'); const [tables, setTables] = useState<LayerTable[]>([]); const [table, setTable] = useState<LayerTable | null>(null); const [partitions, setPartitions] = useState<EtlPartition[]>([]); const [partition, setPartition] = useState<string>(); const [data, setData] = useState<QueryResult>(); const [stream, setStream] = useState<EtlStreamState>(); const [loading, setLoading] = useState(false); const [offsetOpen, setOffsetOpen] = useState(false); const [offset, setOffset] = useState<number>(); const [script, setScript] = useState(''); const [dryRunRows, setDryRunRows] = useState<Record<string, unknown>[]>([]); const [sql, setSql] = useState('SELECT * FROM ts_rds.rds_news LIMIT 50');
  useEffect(() => { setSchemas(mockSchemas); }, []);
  useEffect(() => { setLoading(true); const timer = window.setTimeout(() => { const nextTables = mockTables[schema] ?? []; setTables(nextTables); if (nextTables[0]) inspect(nextTables[0]); else setTable(null); setLoading(false); }, 180); return () => window.clearTimeout(timer); }, [schema]);
  useEffect(() => { if (!table) return; setPartitions(mockPartitions); setPartition(undefined); setStream({ available: true, consumerGroup: `etl-${schema}-group`, topic: `etl.${schema}.processed`, offsets: [{ partition: 0, offset: 184220 }, { partition: 1, offset: 182904 }], throughput: mockSchemas.find((item) => item.key === schema)?.rateIn ?? 0, throughputReason: 'Mock snapshot for design review' }); setScript(`# ${schema}/${table.name}\n\ndef handler(message, context):\n    normalized = normalize(message)\n    validate_contract(normalized)\n    return normalized\n`); setDryRunRows([]); setData({ columns: ['record_id', 'source', 'updated_at'], rows: [{ record_id: 'rec_001928', source: 'news', updated_at: '2026-08-25T10:42:00Z' }, { record_id: 'rec_001929', source: 'patent', updated_at: '2026-08-25T10:41:55Z' }], rowCount: 2, elapsed: 0.018 }); }, [schema, table]);
  useEffect(() => { if (!table || !stream) return; const timer = window.setInterval(() => setStream((current) => current ? ({ ...current, offsets: current.offsets?.map((item) => ({ ...item, offset: item.offset + Math.floor(Math.random() * 4) })) }) : current), 1800); return () => window.clearInterval(timer); }, [table, stream?.consumerGroup]);
  const inspect = (selected: LayerTable) => { setTable(selected); setSql(`SELECT * FROM ${selected.schemaName}.${selected.name} LIMIT 50`); };
  const runSql = () => { setData((current) => current ? { ...current, elapsed: 0.012 } : current); message.success('Query executed against the mock result set'); };
  const queryPartition = (value?: string) => { setPartition(value); };
  const saveOffset = async () => { if (offset === undefined) return; message.success('Mock offset updated — worker restart required in production'); setOffsetOpen(false); };
  const runDryRun = () => { setDryRunRows([{ record_id: 'rec_001928', normalized_title: 'Example title', quality_score: 0.98, validation: 'PASS' }, { record_id: 'rec_001929', normalized_title: 'Second title', quality_score: 0.86, validation: 'WARN: source missing' }]); message.success('Dry run complete — no data was persisted'); };
  return <div className="flow-layers"><div className="flow-layer-layout"><aside className="flow-layer-tree"><div className="flow-tree-label">Data connectors</div>{schemas.map((item) => <button className={schema === item.key ? 'is-active' : ''} key={item.key} onClick={() => setSchema(item.key)}><span>{item.label}</span><small>{item.tables ?? 0} tables</small></button>)}</aside><section className="flow-layer-main"><div className="flow-card flow-table-card"><div className="flow-card-head"><div><h2>{schema} tables</h2><p>Schema: {schema}</p></div>{loading && <Spin size="small" />}</div><div className="flow-table"><div className="flow-table-row flow-table-head"><span>Table</span><span>Rows</span><span>Size</span><span /></div>{tables.map((item) => <button className={`flow-table-row flow-table-button ${table?.name === item.name ? 'is-selected' : ''}`} key={item.name} onClick={() => inspect(item)}><span><strong>{item.name}</strong><small>{item.partitioned ? 'Partitioned' : item.schemaName}</small></span><span>{item.rowCount}</span><span>{item.size}</span><span><RightOutlined /></span></button>)}</div></div>{table && <div className="flow-card flow-inspector"><div className="flow-card-head"><div><h2>{table.name}</h2><p>Health and stream position</p></div><Button size="small" onClick={() => { setOffset(stream?.offsets?.[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button></div><div className="flow-stream-grid flow-health-default"><div><span>Health</span><strong className="flow-health-good">Healthy</strong><small>Schema compatible · last check 18s ago</small></div><div><span>Topic</span><strong>{stream?.topic ?? 'Unavailable'}</strong></div><div><span>Throughput</span><strong>{stream?.throughput ?? 0}/s</strong><small>Mock snapshot</small></div><div><span>Redis offsets</span><strong>{stream?.offsets?.map((item) => `p${item.partition}: ${item.offset}`).join(' · ')}</strong><small>Live refresh every 1.8s</small></div></div><Tabs items={[{ key: 'data', label: 'Data preview', children: <><div className="flow-sql-bar"><Input value={sql} onChange={(event) => setSql(event.target.value)} onPressEnter={runSql} /><Button type="primary" onClick={runSql}>Run</Button></div><div className="flow-partition-tags">{partitions.map((item) => <Tag.CheckableTag key={item.name} checked={partition === item.name} onChange={() => queryPartition(partition === item.name ? undefined : item.name)}>{item.name.replace('p2026_', '')}</Tag.CheckableTag>)}</div><Table className="flow-data-table" size="small" pagination={false} rowKey="record_id" dataSource={data?.rows ?? []} columns={(data?.columns ?? []).map((key) => ({ title: key, dataIndex: key, key }))} /></> }, { key: 'script', label: 'Layer script', children: <div className="flow-script-editor"><div className="flow-script-note">{schema} handler · edits are local until a release is created.</div><Input.TextArea value={script} onChange={(event) => setScript(event.target.value)} autoSize={{ minRows: 12, maxRows: 22 }} /><div className="flow-script-actions"><Button type="primary" onClick={runDryRun}>Run dry run</Button><Button onClick={() => message.info('Changes are local only and were not persisted')}>Validate fields</Button><span>Runs 2 sample records · no writes</span></div>{dryRunRows.length > 0 && <Table size="small" pagination={false} rowKey="record_id" dataSource={dryRunRows} columns={Object.keys(dryRunRows[0]).map((key) => ({ title: key, dataIndex: key, key }))} />}</div> }]} /></div>}</section></div><Modal title="Adjust Redis offset" open={offsetOpen} onCancel={() => setOffsetOpen(false)} onOk={saveOffset} okText="Confirm SET OFFSET"><p>Current offset updates live while this panel is open. Changes apply after the worker restarts.</p><div className="flow-offset-live">{stream?.offsets?.map((item) => <div key={item.partition}><span>Partition {item.partition}</span><strong>{item.offset}</strong></div>)}</div><InputNumber min={0} value={offset} onChange={(value) => setOffset(value ?? undefined)} placeholder="New Kafka offset" style={{ width: '100%' }} /></Modal></div>;
};

const DataLayersWorkspace: React.FC = () => {
  const { search } = useLocation();
  const navigate = useNavigate();
  const treeItemRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const cachedRects = useRef<Record<string, { x: number; y: number; w: number; h: number }> | null>(null);
  const [flipStyles, setFlipStyles] = useState<Record<string, React.CSSProperties>>({});
  const [flipActive, setFlipActive] = useState(false);

  useLayoutEffect(() => {
    const raw = sessionStorage.getItem('flow-transition');
    if (!raw) return;
    let data: { clicked: string; positions: Record<string, { x: number; y: number; w: number; h: number }>; timestamp: number };
    try { data = JSON.parse(raw); } catch { return; }
    if (Date.now() - data.timestamp > 2000) return;
    // Don't remove sessionStorage here — StrictMode double-invokes this effect.
    // We remove it only when the PLAY phase actually fires.

    // Only measure on first invocation — StrictMode re-invokes this effect,
    // and by the second run the DOM already has INVERT transforms applied,
    // so getBoundingClientRect() would return wrong (already-displaced) positions.
    if (cachedRects.current === null) {
      const cached: Record<string, { x: number; y: number; w: number; h: number }> = {};
      mockSchemas.forEach((item) => {
        const el = treeItemRefs.current[item.key];
        if (el) {
          const r = el.getBoundingClientRect();
          cached[item.key] = { x: r.left, y: r.top, w: r.width, h: r.height };
        }
      });
      cachedRects.current = cached;
    }

    const deltas: Record<string, { dx: number; dy: number; sx: number; sy: number }> = {};
    mockSchemas.forEach((item) => {
      const from = data.positions[item.key];
      const to = cachedRects.current?.[item.key];
      if (!from || !to) {
        deltas[item.key] = { dx: 0, dy: 0, sx: 1, sy: 1 };
        return;
      }
      deltas[item.key] = {
        dx: from.x - to.x,
        dy: from.y - to.y,
        sx: from.w / to.w,
        sy: from.h / to.h,
      };
    });

    // Apply INVERT: position elements as if they were still on the home page
    const init: Record<string, React.CSSProperties> = {};
    mockSchemas.forEach((item) => {
      const d = deltas[item.key];
      const isClicked = data.clicked === item.key;
      init[item.key] = {
        transform: `translate(${d.dx}px, ${d.dy}px) scale(${d.sx}, ${d.sy})`,
        transition: 'none',
        opacity: 1,
        zIndex: isClicked ? 20 : 5,
      };
    });
    setFlipStyles(init);
    setFlipActive(true);

    // PLAY: use setTimeout(0) instead of RAF — StrictMode cancels RAFs in cleanup,
    // but setTimeout re-scheduled in the second invocation will still fire.
    const playTimer = window.setTimeout(() => {
      // Now safe to remove — PLAY is starting
      sessionStorage.removeItem('flow-transition');
      const play: Record<string, React.CSSProperties> = {};
      mockSchemas.forEach((item, idx) => {
        const isClicked = data.clicked === item.key;
        play[item.key] = {
          transform: 'translate(0, 0) scale(1, 1)',
          // No inline transition — CSS class .is-flipping provides it.
          // This ensures the browser sees the CSS transition as "already active"
          // when the transform changes, instead of seeing transition change
          // from 'none' to a value in the same frame.
          transitionDelay: isClicked ? '0ms' : `${idx * 40}ms`,
          opacity: 1,
          zIndex: isClicked ? 20 : 5,
        };
      });
      setFlipStyles(play);
      window.setTimeout(() => {
        setFlipStyles({});
        setFlipActive(false);
      }, 800);
    }, 30);

    return () => window.clearTimeout(playTimer);
  }, []);
  const [collectionOpen, setCollectionOpen] = useState(false);
  const [connectorStep, setConnectorStep] = useState<'type' | 'config'>('type');
  const [connectorDatabase, setConnectorDatabase] = useState<'postgres' | 'mongodb' | 'elasticsearch' | 's3'>('postgres');
  const [connectorSelectionActive, setConnectorSelectionActive] = useState(false);
  const [databaseSearch, setDatabaseSearch] = useState('');
  const [databaseName, setDatabaseName] = useState('');
  const [expandedDatabases, setExpandedDatabases] = useState<Record<string, boolean>>({});
  const [selectedSchema, setSelectedSchema] = useState('ts_rds');
  const [connectorSelectedSchema, setConnectorSelectedSchema] = useState('');
  const [schemaNameDraft, setSchemaNameDraft] = useState('ts_rds');
  const [schemaEditing, setSchemaEditing] = useState(true);
  const [resourcePanel, setResourcePanel] = useState<'none' | 'create' | 'edit'>('none');
  const [resourceConnectionName, setResourceConnectionName] = useState('');
  const [resourceDatabase, setResourceDatabase] = useState('');
  const [resourceSchema, setResourceSchema] = useState('');
  const [resourceSchemas, setResourceSchemas] = useState<string[]>([]);
  const [databaseAliases, setDatabaseAliases] = useState<Record<string, string>>({ dw_etl: 'ETL warehouse', spider_prod: 'Production', analytics: 'Analytics' });
  const [schemaAliases, setSchemaAliases] = useState<Record<string, string>>({ ts_rds: 'Raw data', ts_ods: 'Operational data', ts_task: 'Task staging', ts_dwd: 'Detail warehouse', ts_dws: 'Summary warehouse' });
  useEffect(() => {
    const params = new URLSearchParams(search);
    const resource = params.get('resource');
    const schema = params.get('schema');
    const tableName = params.get('table');
    if (resource) {
      setDatabaseName(resource);
      setExpandedDatabases((current) => ({ ...current, [resource]: true }));
    }
    if (schema && mockSchemas.some((item) => item.key === schema)) {
      setSelectedSchema(schema);
      setSchemaNameDraft(schema);
      const schemaTables = [...(mockTables[schema] ?? []), ...(addedTables[schema] ?? [])];
      const target = schemaTables.find((t) => t.name === tableName) ?? schemaTables[0];
      if (target) applyTableState(target, schema);
    }
  }, [search]);
  const [table, setTable] = useState<LayerTable>(mockTables['ts_rds'][0]);
  const [partition, setPartition] = useState<string>();
  const [offsetOpen, setOffsetOpen] = useState(false);
  const [offset, setOffset] = useState<number>();
  const [offsets, setOffsets] = useState([{ partition: 0, offset: 184220 }, { partition: 1, offset: 182904 }]);
  const [sql, setSql] = useState('SELECT * FROM ts_rds.rds_news LIMIT 50');
  const [sqlOpen, setSqlOpen] = useState(false);
  const [sqlExecuting, setSqlExecuting] = useState(false);
  const [script, setScript] = useState('');
  const [dryRunRows, setDryRunRows] = useState<Record<string, unknown>[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [draftExists, setDraftExists] = useState(false);
  const [inputTopics, setInputTopics] = useState<string[]>(['etl.ts_rds.rds_news.input']);
  const [activeTopicIndex, setActiveTopicIndex] = useState(0);
  const [scriptOpen, setScriptOpen] = useState(false);
  const [tableSearch, setTableSearch] = useState('');
  const [closedTables, setClosedTables] = useState<Set<string>>(new Set());
  const [editingRowKey, setEditingRowKey] = useState<string>();
  const [editingDraft, setEditingDraft] = useState<Record<string, unknown> | null>(null);
  const [rowEdits, setRowEdits] = useState<Record<string, Record<string, unknown>>>({});
  const [deletedRowKeys, setDeletedRowKeys] = useState<Set<string>>(new Set());
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [partitionOpen, setPartitionOpen] = useState(false);
  const [statusColumnVisible, setStatusColumnVisible] = useState(true);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [apiOpen, setApiOpen] = useState(false);
  const [apiEnabled, setApiEnabled] = useState(false);
  const [apiFilterType, setApiFilterType] = useState<string[]>([]);
  const [apiFilterSource, setApiFilterSource] = useState<string[]>([]);
  const [newTableName, setNewTableName] = useState('');
  const [newTableDescription, setNewTableDescription] = useState('');
  const [addedTables, setAddedTables] = useState<Record<string, LayerTable[]>>({});
  const [fields, setFields] = useState([{ id: 'field-1', name: 'record_id', type: 'TEXT', length: '', nullable: false, primary: true, comment: 'Business record identifier' }, { id: 'field-empty', name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' }]);
  const [indexes, setIndexes] = useState([{ id: 'idx-1', name: 'idx_record_id', type: 'BTREE', unique: true, fields: 'record_id', comment: '' }, { id: 'idx-empty', name: '', type: 'BTREE', unique: false, fields: '', comment: '' }]);
  const [foreignKeys, setForeignKeys] = useState([{ id: 'fk-1', name: 'fk_source', fields: 'source', refTable: 'source_dim', refFields: 'source_code', onDelete: 'RESTRICT', onUpdate: 'CASCADE' }, { id: 'fk-empty', name: '', fields: '', refTable: '', refFields: '', onDelete: 'RESTRICT', onUpdate: 'RESTRICT' }]);
  const [checks, setChecks] = useState([{ id: 'chk-1', name: 'chk_status', expression: "status IN ('ready','processing','error')", comment: '' }, { id: 'chk-empty', name: '', expression: '', comment: '' }]);
  const [tableOptions, setTableOptions] = useState({ engine: 'InnoDB', charset: 'utf8mb4', collation: 'utf8mb4_general_ci', rowFormat: 'Dynamic', comment: '' });
  const [ddlText, setDdlText] = useState('');
  const tables = [...(mockTables[selectedSchema] ?? []), ...(addedTables[selectedSchema] ?? [])].filter((t) => !closedTables.has(t.name));
  const schemaNode = mockSchemas.find((item) => item.key === selectedSchema);
  const fieldCount = selectedSchema === 'ts_rds' ? 9 : selectedSchema === 'ts_ods' ? 22 : selectedSchema === 'ts_task' ? 14 : 18;
  const tableDescription = selectedSchema === 'ts_rds' ? 'Raw ingestion records from Kafka' : selectedSchema === 'ts_ods' ? 'Standardized operational records' : 'Managed ETL output asset';
  const baseDataRows = [
    { record_id: 'rec_001928', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:42:00' },
    { record_id: 'rec_001929', source: 'patent', data_type: 'document', partition: 1, status: 'ready', updated_at: '2026-08-25 10:41:55' },
    { record_id: 'rec_001930', source: 'navwarn', data_type: 'event', partition: 0, status: 'processing', updated_at: '2026-08-25 10:41:48' },
    { record_id: 'rec_001931', source: 'news', data_type: 'article', partition: 1, status: 'ready', updated_at: '2026-08-25 10:41:42' },
    { record_id: 'rec_001932', source: 'patent', data_type: 'document', partition: 0, status: 'ready', updated_at: '2026-08-25 10:41:35' },
    { record_id: 'rec_001933', source: 'navwarn', data_type: 'event', partition: 1, status: 'processing', updated_at: '2026-08-25 10:41:28' },
    { record_id: 'rec_001934', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:41:21' },
    { record_id: 'rec_001935', source: 'patent', data_type: 'document', partition: 1, status: 'ready', updated_at: '2026-08-25 10:41:14' },
    { record_id: 'rec_001936', source: 'navwarn', data_type: 'event', partition: 0, status: 'ready', updated_at: '2026-08-25 10:41:07' },
    { record_id: 'rec_001937', source: 'news', data_type: 'article', partition: 1, status: 'ready', updated_at: '2026-08-25 10:41:00' },
    { record_id: 'rec_001938', source: 'patent', data_type: 'document', partition: 0, status: 'processing', updated_at: '2026-08-25 10:40:53' },
    { record_id: 'rec_001939', source: 'navwarn', data_type: 'event', partition: 1, status: 'ready', updated_at: '2026-08-25 10:40:46' },
    { record_id: 'rec_001940', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:40:39' },
    { record_id: 'rec_001941', source: 'patent', data_type: 'document', partition: 1, status: 'ready', updated_at: '2026-08-25 10:40:32' },
    { record_id: 'rec_001942', source: 'navwarn', data_type: 'event', partition: 0, status: 'ready', updated_at: '2026-08-25 10:40:25' },
    { record_id: 'rec_001943', source: 'news', data_type: 'article', partition: 1, status: 'processing', updated_at: '2026-08-25 10:40:18' },
    { record_id: 'rec_001944', source: 'patent', data_type: 'document', partition: 0, status: 'ready', updated_at: '2026-08-25 10:40:11' },
    { record_id: 'rec_001945', source: 'navwarn', data_type: 'event', partition: 1, status: 'ready', updated_at: '2026-08-25 10:40:04' },
    { record_id: 'rec_001946', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:39:57' },
    { record_id: 'rec_001947', source: 'patent', data_type: 'document', partition: 1, status: 'ready', updated_at: '2026-08-25 10:39:50' },
    { record_id: 'rec_001948', source: 'navwarn', data_type: 'event', partition: 0, status: 'ready', updated_at: '2026-08-25 10:39:43' },
    { record_id: 'rec_001949', source: 'news', data_type: 'article', partition: 1, status: 'ready', updated_at: '2026-08-25 10:39:36' },
    { record_id: 'rec_001950', source: 'patent', data_type: 'document', partition: 0, status: 'processing', updated_at: '2026-08-25 10:39:29' },
    { record_id: 'rec_001951', source: 'navwarn', data_type: 'event', partition: 1, status: 'ready', updated_at: '2026-08-25 10:39:22' },
    { record_id: 'rec_001952', source: 'news', data_type: 'article', partition: 0, status: 'ready', updated_at: '2026-08-25 10:39:15' },
  ];
  const dataRows = baseDataRows.map((row) => ({ ...row, ...(rowEdits[row.record_id] ?? {}) })).filter((row) => !deletedRowKeys.has(row.record_id) && (partition === undefined || row.partition === mockPartitions.findIndex((item) => item.name === partition) % 2) && (!filterText || Object.values(row).join(' ').toLowerCase().includes(filterText.toLowerCase())));
  const sqlSuggestionItems = [selectedSchema, table?.name ?? '', ...Object.keys(dataRows[0] ?? {})].filter(Boolean);
  const startRowEdit = (recordId: string) => { const row = dataRows.find((item) => item.record_id === recordId); if (row) { setEditingRowKey(recordId); setEditingDraft({ ...row }); } };
  const cancelRowEdit = () => { setEditingRowKey(undefined); setEditingDraft(null); };
  const saveRowEdit = () => { if (editingRowKey && editingDraft) setRowEdits((current) => ({ ...current, [editingRowKey]: editingDraft })); cancelRowEdit(); };
  const dataColumns: any[] = Object.keys(dataRows[0] ?? {}).filter((key) => statusColumnVisible || key !== 'status').map((key) => ({ title: key.toUpperCase(), dataIndex: key, key, render: (value: unknown, row: { record_id: string }) => editingRowKey === row.record_id && editingDraft ? <Input size="small" value={String(editingDraft[key] ?? '')} onChange={(event) => setEditingDraft((current) => current ? ({ ...current, [key]: event.target.value }) : current)} /> : key === 'status' ? <span className={`flow-status flow-status--${String(value)}`}>{String(value)}</span> : String(value) }))
    .concat([{ title: 'ACTION', key: 'action', fixed: 'right' as const, width: 74, render: (_: unknown, row: { record_id: string }) => editingRowKey === row.record_id ? <span className="flow-row-actions is-editing"><button type="button" aria-label="Save row" title="Save" onClick={saveRowEdit}><CheckOutlined /></button><button type="button" aria-label="Cancel row edit" title="Cancel" onClick={cancelRowEdit}><CloseOutlined /></button></span> : <span className="flow-row-actions flow-row-hover-actions"><button type="button" aria-label="Edit row" title="Edit" onClick={() => startRowEdit(row.record_id)}><EditOutlined /></button><button type="button" aria-label="Delete row" title="Delete" onClick={() => setDeletedRowKeys((current) => new Set(current).add(row.record_id))}><DeleteOutlined /></button></span> }] as any);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setOffsets((current) => current.map((item) => ({ ...item, offset: item.offset + Math.floor(Math.random() * 4) })));
    }, 1800);
    return () => window.clearInterval(timer);
  }, []);

  const applyTableState = (selected: LayerTable, schema: string) => {
    setCreateOpen(false);
    setTable(selected);
    setPartition(undefined);
    setSql(`SELECT * FROM ${selected.schemaName}.${selected.name} LIMIT 50`);
    setScript(`# ${schema}/${selected.name}\n\ndef handler(message, context):\n    normalized = normalize(message)\n    validate_contract(normalized)\n    return normalized\n`);
    setDryRunRows([]);
    setInputTopics([`etl.${schema}.${selected.name}.input`]);
    setActiveTopicIndex(0);
  };

  const selectTable = (selected: LayerTable) => {
    setCreateOpen(false);
    setDraftExists(false);
    const params = new URLSearchParams(search);
    if (params.get('table') === selected.name) return;
    params.set('table', selected.name);
    navigate(`/flow/layers?${params.toString()}`, { replace: true });
  };

  const runDryRun = () => {
    setDryRunRows([
      { record_id: 'rec_001928', normalized_title: 'Example title', quality_score: 0.98, validation: 'PASS' },
      { record_id: 'rec_001929', normalized_title: 'Second title', quality_score: 0.86, validation: 'WARN: source missing' },
    ]);
    message.success('Dry run complete — no data was persisted');
  };

  const updateField = (id: string, key: string, value: string | boolean) => setFields((current) => current.map((field) => field.id === id ? { ...field, [key]: value } : field));
  const handleNameChange = (id: string, value: string) => {
    if (!/^[a-zA-Z0-9_]*$/.test(value)) return;
    setFields((current) => {
      const idx = current.findIndex((f) => f.id === id);
      const isLast = idx === current.length - 1;
      const wasEmpty = !current[idx].name;
      const next = current.map((f) => f.id === id ? { ...f, name: value } : f);
      if (isLast && wasEmpty && value) next.push({ id: crypto.randomUUID(), name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' });
      return next;
    });
  };
  const handleLengthChange = (id: string, value: string) => { if (/^\d*$/.test(value)) updateField(id, 'length', value); };
  const removeField = (id: string) => setFields((current) => { const filtered = current.filter((f) => f.id !== id); if (!filtered.length || filtered[filtered.length - 1]?.name) filtered.push({ id: crypto.randomUUID(), name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' }); return filtered; });
  const moveField = (index: number, direction: -1 | 1) => setFields((current) => { const target = index + direction; if (target < 0 || target >= current.length - 1) return current; const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next; });
  const updateIndex = (id: string, changes: Partial<(typeof indexes)[number]>) => setIndexes((current) => {
    const next = current.map((row) => row.id === id ? { ...row, ...changes } : row);
    if (next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', type: 'BTREE', unique: false, fields: '', comment: '' });
    return next;
  });
  const removeIndex = (id: string) => setIndexes((current) => {
    const next = current.filter((row) => row.id !== id);
    if (!next.length || next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', type: 'BTREE', unique: false, fields: '', comment: '' });
    return next;
  });
  const updateForeignKey = (id: string, changes: Partial<(typeof foreignKeys)[number]>) => setForeignKeys((current) => {
    const next = current.map((row) => row.id === id ? { ...row, ...changes } : row);
    if (next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', fields: '', refTable: '', refFields: '', onDelete: 'RESTRICT', onUpdate: 'RESTRICT' });
    return next;
  });
  const removeForeignKey = (id: string) => setForeignKeys((current) => {
    const next = current.filter((row) => row.id !== id);
    if (!next.length || next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', fields: '', refTable: '', refFields: '', onDelete: 'RESTRICT', onUpdate: 'RESTRICT' });
    return next;
  });
  const updateCheck = (id: string, changes: Partial<(typeof checks)[number]>) => setChecks((current) => {
    const next = current.map((row) => row.id === id ? { ...row, ...changes } : row);
    if (next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', expression: '', comment: '' });
    return next;
  });
  const removeCheck = (id: string) => setChecks((current) => {
    const next = current.filter((row) => row.id !== id);
    if (!next.length || next[next.length - 1]?.name) next.push({ id: crypto.randomUUID(), name: '', expression: '', comment: '' });
    return next;
  });
  const createTable = () => {
    const name = newTableName.trim();
    if (!name || !fields.some((field) => field.name.trim())) { message.warning('Table name and field names are required'); return; }
    const created: LayerTable = { name, schemaName: selectedSchema, partitioned: false, rowCount: 0, size: '0 bytes', updatedAt: 'Just now' };
    setAddedTables((current) => ({ ...current, [selectedSchema]: [...(current[selectedSchema] ?? []), created] }));
    setDraftExists(false); selectTable(created); setCreateOpen(false); setNewTableName(''); setNewTableDescription(''); message.success('Mock table created');
  };
  const ddlPreview = `CREATE TABLE ${selectedSchema}.${newTableName || 'new_table'} (\n${fields.filter((field) => field.name.trim()).map((field) => `  ${field.name} ${field.type}${field.length ? `(${field.length})` : ''}${field.nullable ? '' : ' NOT NULL'}${field.primary ? ' PRIMARY KEY' : ''}`).join(',\n')}\n);`;
  const databaseOptions = [
    { key: 'postgres' as const, label: 'Postgres', icon: <PostgresIcon className="flow-database-logo flow-postgres-logo" /> },
    { key: 'mongodb' as const, label: 'MongoDB', icon: <MongoDbIcon className="flow-database-logo flow-mongodb-logo" /> },
    { key: 'elasticsearch' as const, label: 'Elasticsearch', icon: <ElasticsearchIcon className="flow-database-logo flow-elasticsearch-logo" /> },
    { key: 's3' as const, label: 'S3', icon: <S3Icon className="flow-database-logo flow-s3-logo" /> },
  ];
  const visibleDatabaseOptions = databaseOptions.filter(({ label }) => label.toLowerCase().includes(databaseSearch.trim().toLowerCase()));
  const selectedDatabaseOption = databaseOptions.find(({ key }) => key === connectorDatabase) ?? databaseOptions[0];
  const changeConnectorStep = (step: 'type' | 'config', database = connectorDatabase) => {
    const update = () => { setConnectorDatabase(database); setConnectorSelectionActive(step === 'config'); setConnectorSelectedSchema(''); if (step === 'config') setDatabaseName(''); setConnectorStep(step); };
    const documentWithTransitions = document as Document & { startViewTransition?: (callback: () => void) => void };
    if (documentWithTransitions.startViewTransition) documentWithTransitions.startViewTransition(update);
    else update();
  };
  const removeResource = () => {
    if (!databaseName) return;
    setDatabaseAliases((current) => { const next = { ...current }; delete next[databaseName]; return next; });
    setExpandedDatabases((current) => { const next = { ...current }; delete next[databaseName]; return next; });
    setDatabaseName('');
    setConnectorSelectedSchema('');
  };
  const renameSchema = () => {
    if (!schemaEditing) return;
    const nextName = schemaNameDraft.trim();
    if (!connectorSelectedSchema || !nextName || nextName === connectorSelectedSchema) return;
    setSchemaAliases((current) => { const next = { ...current, [nextName]: current[connectorSelectedSchema] || 'Schema' }; delete next[connectorSelectedSchema]; return next; });
    setConnectorSelectedSchema(nextName);
    setSchemaEditing(false);
  };
  const deleteSchema = () => {
    if (!connectorSelectedSchema) return;
    setSchemaAliases((current) => { const next = { ...current }; delete next[connectorSelectedSchema]; return next; });
    setConnectorSelectedSchema(''); setSchemaNameDraft(''); setSchemaEditing(false);
  };
  const openCreateResource = () => { setConnectorSelectedSchema(''); setSchemaNameDraft(''); setResourceDatabase(''); setResourceSchema(''); setResourceSchemas([]); setResourcePanel('create'); };
  const openEditResource = () => { if (!databaseName) return; setConnectorSelectedSchema(''); setSchemaNameDraft(''); setResourceDatabase(databaseName); setResourceSchema(''); setResourceSchemas(Object.keys(schemaAliases)); setResourcePanel('edit'); };
  const saveResource = () => {
    const name = resourceDatabase.trim();
    const pendingSchema = resourceSchema.trim();
    const schemas = [...resourceSchemas, pendingSchema].filter(Boolean);
    if (!name) { message.warning('Database name is required'); return; }
    const previousKey = resourcePanel === 'edit' ? databaseName : '';
    const key = name;
    setDatabaseAliases((current) => { const next = { ...current }; if (previousKey && previousKey !== key) delete next[previousKey]; next[key] = next[key] || key; return next; });
    if (previousKey && previousKey !== key) setExpandedDatabases((current) => { const next = { ...current }; next[key] = Boolean(next[previousKey]); delete next[previousKey]; return next; });
    setDatabaseName(key); setExpandedDatabases((current) => ({ ...current, [key]: true }));
    if (schemas.length) setSchemaAliases((current) => ({ ...current, ...Object.fromEntries(schemas.map((schema) => [schema, 'New schema'])) }));
    setResourcePanel('none'); message.success('Resource saved');
  };
  useEffect(() => { if (connectorSelectedSchema) setSchemaEditing(true); }, [connectorSelectedSchema]);

  return <div className="flow-layers flow-layers--tagged">
    <div className="flow-layer-layout">
      <aside className="flow-layer-tree">
        <div className="flow-tree-label flow-tree-label--actions"><span>Data connectors</span><button type="button" aria-label="Add data connector" title="Add data connector" onClick={() => { setConnectorStep('type'); setConnectorSelectionActive(false); setConnectorSelectedSchema(''); setDatabaseSearch(''); setCollectionOpen(true); }}><PlusOutlined /></button></div>
        {mockSchemas.map((item, i) => <button ref={(el) => { treeItemRefs.current[item.key] = el; }} className={`flow-connector-tree-item ${selectedSchema === item.key ? 'is-active' : ''} ${flipActive ? 'is-flipping' : ''}`} key={item.key} style={flipStyles[item.key] || {}} onClick={() => navigate(`/flow/layers?resource=dw_etl&schema=${item.key}`)}><PostgresIcon className="flow-connector-tree-icon" /><span className="flow-connector-tree-name"><strong>spider-prod · {item.key}</strong><small>{item.tables} tables</small></span></button>)}
      </aside>
      <section className="flow-layer-main">
        <div className="flow-layer-overview">
          <div className="flow-layer-glyph"><PostgresIcon className="flow-layer-glyph-svg" /></div>
          <h3>{selectedSchema}<em>{schemaAliases[selectedSchema] ?? 'Schema'}</em></h3>
          <div className="flow-layer-overview-stats">
            <span><b>{tables.length}</b> tables</span>
            <i />
            <span><b>{(schemaNode?.rateIn ?? 0).toLocaleString()}</b>/s in</span>
            <i />
            <span><b>{(schemaNode?.rateOut ?? 0).toLocaleString()}</b>/s out</span>
            <i />
            <span><b>{schemaNode?.lag ?? 0}</b>ms lag</span>
          </div>
          <div className="flow-layer-overview-health">
            <i className="flow-dot flow-dot--green" /><span>Running</span>
          </div>
        </div>
        <div className="flow-table-switcher">
        <Dropdown overlayClassName="flow-table-tab-dropdown" trigger={['click']} dropdownRender={() => (
          <div className="flow-tab-search-panel">
            <div className="flow-tab-search-input-wrap">
              <SearchOutlined className="flow-tab-search-icon" />
              <input className="flow-tab-search-input" autoFocus placeholder="Search tables..." value={tableSearch} onChange={(e) => setTableSearch(e.target.value)} />
            </div>
            <div className="flow-tab-search-section-label">Open tables</div>
            <div className="flow-tab-search-list">
              {tables.filter((item) => item.name.toLowerCase().includes(tableSearch.toLowerCase())).map((item) => (
                <button key={item.name} className={`flow-tab-search-item ${table.name === item.name ? 'is-active' : ''}`} onClick={() => { selectTable(item); setTableSearch(''); }}>
                  <span className="flow-tab-search-icon-wrap"><DatabaseOutlined /></span>
                  <span className="flow-tab-search-name">{item.name}</span>
                  <span className="flow-tab-search-meta">{table.name === item.name ? 'current' : `${item.rowCount.toLocaleString()} rows`}</span>
                </button>
              ))}
              {tables.filter((item) => item.name.toLowerCase().includes(tableSearch.toLowerCase())).length === 0 && <div className="flow-tab-search-empty">No tables found</div>}
            </div>
          </div>
        )}>
          <button type="button" className="flow-table-tab-menu" aria-label="Select open table"><DownOutlined /></button>
        </Dropdown>
        <div className="flow-table-tabs" role="tablist" aria-label={`${selectedSchema} tables`}>
          {tables.map((item) => { const active = !createOpen && table.name === item.name; return <button type="button" role="tab" aria-selected={active} className={active ? 'is-active' : ''} key={item.name} onClick={() => selectTable(item)}><span className="flow-tab-favicon"><DatabaseOutlined /></span><span className="flow-tab-label">{item.name}</span><span className="flow-tab-close" onClick={(e) => { e.stopPropagation(); const remaining = tables.filter((t) => t.name !== item.name); if (remaining.length > 0) { selectTable(remaining[0]); } else { setCreateOpen(false); setDraftExists(false); } setClosedTables((prev) => new Set(prev).add(item.name)); }}>&times;</span></button>; })}
          {draftExists && <button type="button" role="tab" aria-selected={createOpen} className={createOpen ? 'is-active is-draft' : 'is-draft'} onClick={() => setCreateOpen(true)}><span className="flow-tab-favicon"><EditOutlined /></span><span className="flow-tab-label">* Untitled</span><span className="flow-tab-close" onClick={(e) => { e.stopPropagation(); setDraftExists(false); setCreateOpen(false); }}>&times;</span></button>}
          <button type="button" className="flow-table-add-tag" aria-label="Create table" title="Create table" onClick={() => { setDraftExists(true); setCreateOpen(true); setNewTableName(''); setNewTableDescription(''); }}><PlusOutlined /></button>
        </div>
        </div>
        {createOpen ? <div className="flow-inspector flow-inline-designer-v2">
          <div className="flow-table-header">
            <div className="flow-table-header-left">
              <h2>* Untitled <span className="flow-td-schema-badge">{selectedSchema}</span></h2>
            </div>
            <div className="flow-table-header-right">
              <Button size="small" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button size="small" type="primary" onClick={createTable}>Save table</Button>
            </div>
          </div>
          <div className="flow-inspector-body">
            <div className="flow-td-identity">
              <div className="flow-td-field"><label>Table name</label><Input value={newTableName} onChange={(event) => setNewTableName(event.target.value)} placeholder="e.g. rds_news" /></div>
              <div className="flow-td-field"><label>Description</label><Input value={newTableDescription} onChange={(event) => setNewTableDescription(event.target.value)} placeholder="Brief description of the table" /></div>
            </div>
            <Tabs className="flow-td-tabs" size="small" items={[
              { key: 'fields', label: 'Fields', children: <Table className="flow-td-fields-table" size="small" pagination={false} rowKey="id" dataSource={fields} columns={[
                { title: '#', width: 36, render: (_, __, index) => <span className="flow-td-row-idx">{index + 1}</span> },
                { title: 'Name', dataIndex: 'name', render: (value, row) => <Input size="small" value={value} onChange={(event) => handleNameChange(row.id, event.target.value)} /> },
                { title: 'Type', dataIndex: 'type', width: 130, render: (value, row) => <Select size="small" value={value} onChange={(next) => updateField(row.id, 'type', next)} popupMatchSelectWidth={false} options={['TEXT','BIGINT','INTEGER','BOOLEAN','TIMESTAMPTZ','JSONB'].map((item) => ({ value: item, label: item }))} /> },
                { title: 'Length', dataIndex: 'length', width: 80, render: (value, row) => <InputNumber size="small" min={0} value={value ? Number(value) : undefined} onChange={(val) => updateField(row.id, 'length', val != null ? String(val) : '')} controls /> },
                { title: 'Not null', dataIndex: 'nullable', width: 70, align: 'center', render: (value, row) => <Checkbox checked={!value} onChange={(event) => updateField(row.id, 'nullable', !event.target.checked)} /> },
                { title: 'PK', dataIndex: 'primary', width: 50, align: 'center', render: (value, row) => <Checkbox checked={value} onChange={(event) => updateField(row.id, 'primary', event.target.checked)} /> },
                { title: 'Comment', dataIndex: 'comment', render: (value, row) => <Input size="small" value={value} onChange={(event) => updateField(row.id, 'comment', event.target.value)} /> },
                { title: '', width: 60, render: (_, row, index) => <span className="flow-field-actions"><button onClick={() => moveField(index, -1)}>↑</button><button onClick={() => moveField(index, 1)}>↓</button><button onClick={() => removeField(row.id)}>×</button></span> },
              ]} /> },
              { key: 'indexes', label: 'Indexes', children: <div>
                <Table className="flow-td-fields-table" size="small" pagination={false} rowKey="id" dataSource={indexes} columns={[
                  { title: '#', width: 36, render: (_, __, i) => <span className="flow-td-row-idx">{i + 1}</span> },
                  { title: 'Name', dataIndex: 'name', render: (v, row) => <Input size="small" value={v} onChange={(e) => updateIndex(row.id, { name: e.target.value })} /> },
                  { title: 'Type', dataIndex: 'type', width: 100, render: (v, row) => <Select size="small" value={v} onChange={(n) => updateIndex(row.id, { type: n })} popupMatchSelectWidth={false} options={['BTREE','HASH','GIN','GIST','BRIN'].map((o) => ({ value: o, label: o }))} /> },
                  { title: 'Unique', dataIndex: 'unique', width: 60, align: 'center', render: (v, row) => <Checkbox checked={v} onChange={(e) => updateIndex(row.id, { unique: e.target.checked })} /> },
                  { title: 'Fields', dataIndex: 'fields', width: 180, render: (v, row) => <Select className="flow-fields-select" size="small" mode="multiple" value={v ? v.split(',').filter(Boolean) : []} onChange={(vals) => updateIndex(row.id, { fields: vals.join(',') })} popupMatchSelectWidth={false} options={fields.filter((f) => f.name).map((f) => ({ value: f.name, label: f.name }))} /> },
                  { title: 'Comment', dataIndex: 'comment', width: 140, render: (v, row) => <Input size="small" value={v} onChange={(e) => updateIndex(row.id, { comment: e.target.value })} /> },
                  { title: '', width: 40, render: (_, row) => <span className="flow-field-actions"><button onClick={() => removeIndex(row.id)}>×</button></span> },
                ]} />
              </div> },
              { key: 'foreign', label: 'Foreign keys', children: <div>
                <Table className="flow-td-fields-table" size="small" pagination={false} rowKey="id" dataSource={foreignKeys} columns={[
                  { title: '#', width: 36, render: (_, __, i) => <span className="flow-td-row-idx">{i + 1}</span> },
                  { title: 'Name', dataIndex: 'name', render: (v, row) => <Input size="small" value={v} onChange={(e) => updateForeignKey(row.id, { name: e.target.value })} /> },
                  { title: 'Fields', dataIndex: 'fields', width: 180, render: (v, row) => <Select className="flow-fields-select" size="small" mode="multiple" value={v ? v.split(',').filter(Boolean) : []} onChange={(vals) => updateForeignKey(row.id, { fields: vals.join(',') })} popupMatchSelectWidth={false} options={fields.filter((f) => f.name).map((f) => ({ value: f.name, label: f.name }))} /> },
                  { title: 'Ref. table', dataIndex: 'refTable', width: 130, render: (v, row) => <Select size="small" value={v} onChange={(n) => updateForeignKey(row.id, { refTable: n, refFields: '' })} popupMatchSelectWidth={false} showSearch options={tables.map((t) => ({ value: t.name, label: t.name }))} /> },
                  { title: 'Ref. fields', dataIndex: 'refFields', width: 180, render: (v, row) => <Select className="flow-fields-select" size="small" mode="multiple" value={v ? v.split(',').filter(Boolean) : []} onChange={(vals) => updateForeignKey(row.id, { refFields: vals.join(',') })} popupMatchSelectWidth={false} disabled={!row.refTable} options={fields.filter((f) => f.name).map((f) => ({ value: f.name, label: f.name }))} /> },
                  { title: 'On Delete', dataIndex: 'onDelete', width: 100, render: (v, row) => <Select size="small" value={v} onChange={(n) => updateForeignKey(row.id, { onDelete: n })} popupMatchSelectWidth={false} options={['RESTRICT','CASCADE','SET NULL','NO ACTION','SET DEFAULT'].map((o) => ({ value: o, label: o }))} /> },
                  { title: 'On Update', dataIndex: 'onUpdate', width: 100, render: (v, row) => <Select size="small" value={v} onChange={(n) => updateForeignKey(row.id, { onUpdate: n })} popupMatchSelectWidth={false} options={['RESTRICT','CASCADE','SET NULL','NO ACTION','SET DEFAULT'].map((o) => ({ value: o, label: o }))} /> },
                  { title: '', width: 40, render: (_, row) => <span className="flow-field-actions"><button onClick={() => removeForeignKey(row.id)}>×</button></span> },
                ]} />
              </div> },
              { key: 'checks', label: 'Checks', children: <div>
                <Table className="flow-td-fields-table" size="small" pagination={false} rowKey="id" dataSource={checks} columns={[
                  { title: '#', width: 36, render: (_, __, i) => <span className="flow-td-row-idx">{i + 1}</span> },
                  { title: 'Name', dataIndex: 'name', width: 150, render: (v, row) => <Input size="small" value={v} onChange={(e) => updateCheck(row.id, { name: e.target.value })} /> },
                  { title: 'Expression', dataIndex: 'expression', render: (v, row) => <Input size="small" value={v} onChange={(e) => updateCheck(row.id, { expression: e.target.value })} style={{ fontFamily: 'ui-monospace' }} /> },
                  { title: 'Comment', dataIndex: 'comment', width: 140, render: (v, row) => <Input size="small" value={v} onChange={(e) => updateCheck(row.id, { comment: e.target.value })} /> },
                  { title: '', width: 40, render: (_, row) => <span className="flow-field-actions"><button onClick={() => removeCheck(row.id)}>×</button></span> },
                ]} />
              </div> },
              { key: 'options', label: 'Options', children: <div className="flow-td-options">
                <div className="flow-td-options-grid">
                  <div className="flow-td-options-row"><label>Engine</label><Select size="small" suffixIcon={null} value={tableOptions.engine} onChange={(v) => setTableOptions((o) => ({ ...o, engine: v }))} options={['InnoDB','MyISAM','MEMORY','ARCHIVE'].map((o) => ({ value: o, label: o }))} /></div>
                  <div className="flow-td-options-row"><label>Charset</label><Select size="small" suffixIcon={null} value={tableOptions.charset} onChange={(v) => setTableOptions((o) => ({ ...o, charset: v }))} options={['utf8mb4','utf8','latin1','ascii'].map((o) => ({ value: o, label: o }))} /></div>
                  <div className="flow-td-options-row"><label>Collation</label><Select size="small" suffixIcon={null} value={tableOptions.collation} onChange={(v) => setTableOptions((o) => ({ ...o, collation: v }))} options={['utf8mb4_general_ci','utf8mb4_unicode_ci','utf8mb4_bin','utf8_general_ci'].map((o) => ({ value: o, label: o }))} /></div>
                  <div className="flow-td-options-row"><label>Row format</label><Select size="small" suffixIcon={null} value={tableOptions.rowFormat} onChange={(v) => setTableOptions((o) => ({ ...o, rowFormat: v }))} options={['Default','Dynamic','Fixed','Compressed','Redundant','Compact'].map((o) => ({ value: o, label: o }))} /></div>
                </div>
              </div> },
              { key: 'sql', label: 'SQL', children: <div className="flow-td-sql-wrap">
                <div className="flow-td-sql-header">
                  <span>DDL Script</span>
                  <div className="flow-td-sql-actions">
                    <Button size="small" onClick={() => { setDdlText(ddlPreview); message.success('Regenerated from table definition'); }}>Generate</Button>
                    <Button size="small" onClick={() => { navigator.clipboard?.writeText(ddlText || ddlPreview); message.success('SQL copied'); }}>Copy</Button>
                  </div>
                </div>
                <SqlPreview sql={ddlText || ddlPreview} />
              </div> },
            ]} />
          </div>
        </div> : <div className="flow-inspector">
          <div className="flow-table-header">
            <div className="flow-table-header-left">
              <h2>{table.name}</h2>
            </div>
            <div className="flow-stream-pipeline">
              <div className="flow-pipeline-node flow-pipeline-node--input">
                <div className="flow-pipeline-line-row">
                  <DownloadOutlined className="flow-pipeline-icon flow-pipeline-icon--in" />
                  <strong>{inputTopics[activeTopicIndex] || 'Not configured'}</strong>
                  <em>· {schemaNode?.rateIn ?? 0}/s</em>
                  <code>[{offsets.map((item) => `p${item.partition}: ${item.offset.toLocaleString()}`).join('  ·  ')}]</code>
                </div>
              </div>
              <div className="flow-pipeline-node">
                <div className="flow-pipeline-line-row">
                  <UploadOutlined className="flow-pipeline-icon flow-pipeline-icon--out" />
                  <strong>{table.name}</strong>
                  <em>· {schemaNode?.rateOut ?? 0}/s</em>
                </div>
              </div>
            </div>
            <div className="flow-table-header-right">
              <div className="flow-health-badge"><i className="flow-dot flow-dot--green" /><span>Healthy</span></div>
              <Button size="small" onClick={() => { setOffset(offsets[0]?.offset); setOffsetOpen(true); }}>Adjust offset</Button>
            </div>
          </div>
          <p className="flow-table-desc">{tableDescription}</p>
          <div className="flow-inspector-body">
            <div className="flow-navicat-toolbar">
                  <span className={`flow-tool ${sqlOpen ? 'is-active' : ''}`} onClick={() => setSqlOpen((value) => !value)}><CodeOutlined />SQL console</span>
                  <span className={`flow-tool ${filterOpen ? 'is-active' : ''}`} onClick={() => setFilterOpen((value) => !value)}>Filter</span>
                  <span className={`flow-tool ${partitionOpen ? 'is-active' : ''}`} onClick={() => setPartitionOpen((value) => !value)}>Partitions</span>
                  <span className="flow-tool" onClick={() => setStatusColumnVisible((value) => !value)}>{statusColumnVisible ? 'Hide status' : 'Status'}</span>
                  <span className="flow-tool" onClick={() => setScriptOpen(true)}>Layer script</span>
                  <span className="flow-tool" onClick={() => setAnalysisOpen(true)}>Analysis</span>
                  <span className={`flow-tool ${apiOpen ? 'is-active' : ''}`} onClick={() => setApiOpen((value) => !value)}>Data API</span>
                  <span className="flow-tool" onClick={() => message.success('Mock rows exported as CSV')}>Export</span>
            </div>
              {filterOpen && <div className="flow-filter-bar"><Input allowClear size="small" value={filterText} onChange={(event) => setFilterText(event.target.value)} placeholder="Filter current result set" /><Select size="small" defaultValue="updated_desc" options={[{ value: 'updated_desc', label: 'updated_at DESC' }, { value: 'record_asc', label: 'record_id ASC' }]} /></div>}
              {sqlOpen && <div className="flow-sql-bar flow-sql-bar--real"><SqlEditor value={sql} onChange={setSql} suggestionItems={sqlSuggestionItems} executing={sqlExecuting} onRun={() => { if (sqlExecuting) { setSqlExecuting(false); message.info('Query paused'); } else { setSqlExecuting(true); window.setTimeout(() => setSqlExecuting(false), 1200); message.success('Query executed'); } }} /></div>}
              {partitionOpen && <div className="flow-partition-tags">{mockPartitions.map((item) => <Tag.CheckableTag key={item.name} checked={partition === item.name} onChange={() => setPartition(partition === item.name ? undefined : item.name)}>{item.name.replace('p2026_', '')}</Tag.CheckableTag>)}</div>}
              {apiOpen && <div className="flow-api-bar">
                <div className="flow-api-bar-row">
                  <span className="flow-api-label">Endpoint</span>
                  <Input className="flow-api-url" readOnly value={`https://api.asiral.io/v1/data/${selectedSchema}/${table.name}`} />
                  <Button size="small" onClick={() => { navigator.clipboard?.writeText(`https://api.asiral.io/v1/data/${selectedSchema}/${table.name}`); message.success('URL copied'); }}>Copy</Button>
                </div>
                <div className="flow-api-bar-row">
                  <span className="flow-api-label">Access</span>
                  <Switch size="small" checked={apiEnabled} onChange={setApiEnabled} />
                  <span className="flow-api-status">{apiEnabled ? 'Public — token required' : 'Disabled'}</span>
                </div>
                <div className="flow-api-bar-row">
                  <span className="flow-api-label">Filter</span>
                  <Select className="flow-api-filter" mode="multiple" size="small" placeholder="Data type" value={apiFilterType} onChange={setApiFilterType} options={[{ value: 'article', label: 'article' }, { value: 'document', label: 'document' }, { value: 'record', label: 'record' }, { value: 'image', label: 'image' }]} allowClear />
                  <Select className="flow-api-filter" mode="multiple" size="small" placeholder="Source" value={apiFilterSource} onChange={setApiFilterSource} options={[{ value: 'news', label: 'news' }, { value: 'patent', label: 'patent' }, { value: 'social', label: 'social' }, { value: 'satellite', label: 'satellite' }]} allowClear />
                </div>
                {(apiFilterType.length > 0 || apiFilterSource.length > 0) && <div className="flow-api-preview">GET <code>{`https://api.asiral.io/v1/data/${selectedSchema}/${table.name}?${[apiFilterType.length > 0 ? `data_type=${apiFilterType.join(',')}` : '', apiFilterSource.length > 0 ? `source=${apiFilterSource.join(',')}` : ''].filter(Boolean).join('&')}`}</code></div>}
              </div>}
              <ConfigProvider locale={enUS}><Table className="flow-data-table" size="small" pagination={{ pageSize: 20, size: 'small', showTotal: (total: number, range: [number, number]) => `${range[0]}–${range[1]} / ${table.rowCount.toLocaleString()} rows · ${table.size} · ${fieldCount} fields`, showSizeChanger: true, pageSizeOptions: [10, 20, 50], showQuickJumper: false }} rowKey="record_id" scroll={{ x: 920 }} dataSource={dataRows} columns={dataColumns} onRow={(row) => ({ onDoubleClick: () => startRowEdit(row.record_id), className: editingRowKey === row.record_id ? 'flow-row-editing' : undefined })} /></ConfigProvider>
          </div>
          <Modal className="flow-script-modal" title="Layer script" open={scriptOpen} onCancel={() => setScriptOpen(false)} footer={null} width={720}>
            <div className="flow-script-editor">
              <div className="flow-script-note">{schemaNode?.label ?? selectedSchema} handler · edits are local until a release is created.</div>
              <Input.TextArea value={script} onChange={(event) => setScript(event.target.value)} autoSize={{ minRows: 12, maxRows: 22 }} />
              <div className="flow-script-actions"><Button type="primary" onClick={runDryRun}>Run dry run</Button><Button onClick={() => message.info('Field contract is valid')}>Validate fields</Button><span>Runs 2 sample records · no writes</span></div>
              {dryRunRows.length > 0 && <Table size="small" pagination={false} rowKey="record_id" dataSource={dryRunRows} columns={Object.keys(dryRunRows[0]).map((key) => ({ title: key, dataIndex: key, key }))} />}
            </div>
          </Modal>
        </div>}
      </section>
    </div>
    <Modal className="flow-offset-modal" title="Adjust Kafka offset" open={offsetOpen} onCancel={() => setOffsetOpen(false)} cancelText="Cancel" onOk={() => { message.success('Mock offset updated'); setOffsetOpen(false); }} okText="Confirm SET OFFSET" width={460}>
      <div className="flow-offset-modal-body">
        <div className="flow-offset-modal-info">
          <div className="flow-offset-modal-info-row"><span>Consumer group</span><strong>etl-{selectedSchema}-group</strong></div>
        </div>
        <div className="flow-offset-modal-section-label flow-topic-label">
          <span>Input topic</span>
          <div className="flow-topic-tags">
            {inputTopics.map((topic, idx) => <button key={idx} type="button" className={`flow-topic-tag ${idx === activeTopicIndex ? 'is-active' : ''}`} onClick={() => { setActiveTopicIndex(idx); setOffsets([{ partition: 0, offset: 184220 + idx * 5000 }, { partition: 1, offset: 182904 + idx * 5000 }]); setOffset(undefined); }}>{topic ? topic.split('.').slice(-2).join('.') : 'New'}</button>)}
            <button type="button" className="flow-topic-tag flow-topic-tag-add" onClick={() => { setInputTopics([...inputTopics, '']); setActiveTopicIndex(inputTopics.length); setOffsets([{ partition: 0, offset: 184220 + inputTopics.length * 5000 }, { partition: 1, offset: 182904 + inputTopics.length * 5000 }]); setOffset(undefined); }}><PlusOutlined /></button>
          </div>
        </div>
        <Select value={inputTopics[activeTopicIndex] || undefined} onChange={(value) => { const next = [...inputTopics]; next[activeTopicIndex] = value; setInputTopics(next); }} placeholder="Select input topic" style={{ width: '100%' }} options={tables.map((item) => ({ value: `etl.${selectedSchema}.${item.name}.input`, label: `etl.${selectedSchema}.${item.name}.input` }))} popupClassName="flow-offset-dropdown" />
        <div className="flow-offset-modal-section-label">Current offsets <em>live</em></div>
        <div className="flow-offset-modal-grid">{offsets.map((item) => <div key={item.partition} className="flow-offset-modal-cell"><small>Partition {item.partition}</small><strong>{item.offset.toLocaleString()}</strong><i className="flow-dot flow-dot--green" /></div>)}</div>
        <div className="flow-offset-modal-section-label">New offset</div>
        <InputNumber min={0} value={offset} onChange={(value) => setOffset(value ?? undefined)} placeholder="Enter new Kafka offset" style={{ width: '100%' }} formatter={(value) => `${value ?? ''}`.replace(/[^\d]/g, '')} onKeyDown={(e) => { const allowed = ['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Tab', 'Enter', 'Home', 'End']; if (allowed.includes(e.key) || e.ctrlKey || e.metaKey) return; if (!/^\d$/.test(e.key)) e.preventDefault(); }} />
        <div className="flow-offset-modal-warn"><AlertOutlined /> Changes apply after the worker restarts. The consumer group will resume from the new position.</div>
      </div>
    </Modal>
    <Modal title="Data analysis" open={analysisOpen} onCancel={() => setAnalysisOpen(false)} footer={<Button onClick={() => setAnalysisOpen(false)}>Close</Button>}><div className="flow-analysis-grid"><div><span>Visible rows</span><strong>{dataRows.length}</strong></div><div><span>Partitions</span><strong>{new Set(dataRows.map((row) => row.partition)).size}</strong></div><div><span>Ready</span><strong>{dataRows.filter((row) => row.status === 'ready').length}</strong></div></div></Modal>
    <Modal className="flow-connector-modal" closable={false} title={<div className="flow-modal-title-row"><span className={connectorStep === 'type' ? '' : 'flow-modal-database-title'} style={connectorStep === 'config' ? ({ viewTransitionName: `flow-database-${connectorDatabase}` } as React.CSSProperties) : undefined}>{connectorStep === 'type' ? 'New data connector' : <>{selectedDatabaseOption.icon}<strong>{selectedDatabaseOption.label}</strong></>}</span><div className="flow-modal-title-actions">{connectorStep === 'config' && <button type="button" aria-label="Back to database selection" onClick={() => changeConnectorStep('type')}><BackIcon className="flow-modal-action-icon" /></button>}<button type="button" aria-label="Close" onClick={() => setCollectionOpen(false)}><CloseIcon className="flow-modal-action-icon" /></button></div></div>} open={collectionOpen} onCancel={() => setCollectionOpen(false)} footer={null} width={connectorStep === 'config' ? 760 : 560}>
      {connectorStep === 'type' ? <div className="flow-connector-type-step"><Input className="flow-database-search" prefix={<SearchOutlined />} allowClear value={databaseSearch} onChange={(event) => setDatabaseSearch(event.target.value)} placeholder="Search databases" /><div className="flow-connector-picker">{visibleDatabaseOptions.map(({ key, label, icon }) => <button key={key} type="button" style={{ viewTransitionName: `flow-database-${key}` } as React.CSSProperties} className={`flow-database-option ${connectorSelectionActive && connectorDatabase === key ? 'is-selected' : ''}`} onClick={() => changeConnectorStep('config', key)}>{icon}<strong>{label}</strong></button>)}</div></div> : <div className={`flow-connector-config${connectorSelectedSchema ? ' has-schema-detail' : ''}${resourcePanel !== 'none' ? ' has-resource-panel' : ''}`}><div className="flow-tree-toolbar"><button type="button" title="Add resource" onClick={openCreateResource}><PlusOutlined /></button>{databaseName && <><button type="button" title="Edit resource" onClick={openEditResource}><EditOutlined /></button><button type="button" title="Delete resource" className="is-danger" onClick={removeResource}><DeleteOutlined /></button></>}</div><div className="flow-resource-tree flow-database-list" onClick={() => { setDatabaseName(''); setConnectorSelectedSchema(''); setResourcePanel('none'); }}>{Object.keys(databaseAliases).map((value) => <React.Fragment key={value}><div className={`flow-tree-resource-row ${databaseName === value ? 'is-selected' : ''}`} onClick={(event) => { event.stopPropagation(); setDatabaseName(value); setConnectorSelectedSchema(''); setSchemaNameDraft(''); setSchemaEditing(false); setResourcePanel('none'); setExpandedDatabases((current) => ({ ...current, [value]: !current[value] })); }}><button type="button" className="flow-tree-expand" aria-label={`${expandedDatabases[value] ? 'Collapse' : 'Expand'} ${value}`}><CaretRightOutlined rotate={expandedDatabases[value] ? 90 : 0} /></button><button type="button" className="flow-tree-resource-main"><NavicatConnectionIcon className="flow-navicat-connection-icon" /><strong>{value}</strong><small>{databaseAliases[value]}</small><em>{Object.keys(schemaAliases).length}</em></button></div>{expandedDatabases[value] && <div className="flow-tree-children">{Object.keys(schemaAliases).map((schema) => <div className={`flow-tree-resource-row flow-tree-schema-row ${connectorSelectedSchema === schema ? 'is-selected' : ''}`} key={`${value}-${schema}`} onClick={(event) => event.stopPropagation()}><span className="flow-tree-branch" /><ApartmentOutlined /><button type="button" onClick={() => { setConnectorSelectedSchema(schema); setSchemaNameDraft(schema); setSchemaEditing(false); setResourcePanel('none'); }}><strong>{schema}</strong><small>{schemaAliases[schema]}</small></button></div>)}</div>}</React.Fragment>)}</div>{resourcePanel !== 'none' && <aside className="flow-resource-panel"><div className="flow-resource-panel-head"><strong>{resourcePanel === 'create' ? 'New PostgreSQL database' : 'Edit PostgreSQL database'}</strong><button type="button" onClick={() => setResourcePanel('none')} aria-label="Close">×</button></div><label>Connection name<Input size="small" value={resourceConnectionName} onChange={(event) => setResourceConnectionName(event.target.value)} placeholder="Postgres connection" /></label><label>Database<Input size="small" value={resourceDatabase} onChange={(event) => setResourceDatabase(event.target.value)} placeholder="Database name" /></label><div className="flow-resource-schema-editor"><span>Schema (optional)</span><div className="flow-resource-schema-input"><Input size="small" value={resourceSchema} onChange={(event) => setResourceSchema(event.target.value)} placeholder="Add schema" /><Button size="small" onClick={() => { const schema = resourceSchema.trim(); if (schema && !resourceSchemas.includes(schema)) { setResourceSchemas((current) => [...current, schema]); setResourceSchema(''); } }}><PlusOutlined /></Button></div>{resourceSchemas.length > 0 && <div className="flow-resource-schema-tags">{resourceSchemas.map((schema) => <span key={schema}>{schema}<button type="button" onClick={() => setResourceSchemas((current) => current.filter((item) => item !== schema))}>×</button></span>)}</div>}</div><Button type="primary" size="small" onClick={saveResource}>Save</Button></aside>}{connectorSelectedSchema && resourcePanel === 'none' && <aside className="flow-schema-detail"><div className="flow-resource-panel-head"><strong>Schema details</strong><div className="flow-schema-detail-actions"><button type="button" className="flow-schema-action-icon" aria-label="Edit schema" title="Edit schema" onClick={() => setSchemaEditing(true)}><EditOutlined /></button><button type="button" className="flow-schema-action-icon is-danger" aria-label="Delete schema" title="Delete schema" onClick={deleteSchema}><DeleteOutlined /></button><button type="button" className="flow-schema-action-icon" onClick={() => setConnectorSelectedSchema('')} aria-label="Close"><CloseIcon className="flow-modal-action-icon" /></button></div></div><label className="flow-schema-detail-field">Name<Input size="small" readOnly={!schemaEditing} value={schemaNameDraft} onChange={(event) => setSchemaNameDraft(event.target.value)} onPressEnter={renameSchema} /></label><label className="flow-schema-detail-field">Alias<Input size="small" readOnly={!schemaEditing} value={schemaAliases[connectorSelectedSchema] || ''} onChange={(event) => setSchemaAliases((current) => ({ ...current, [connectorSelectedSchema]: event.target.value }))} /></label><Button type="primary" size="small" onClick={renameSchema}>Save</Button></aside>}<Button className="flow-create-connector" size="small" type="primary" onClick={() => { setCollectionOpen(false); message.success('Data connector created (mock)'); }}>Save connection</Button></div>}
    </Modal>
  </div>;
};
export default Pipeline;

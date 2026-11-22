import { useState } from 'react';
import { NativeSelect } from '@/components/ui';
import { BarChartOutlined, FilterOutlined, ReloadOutlined, TableOutlined } from '@/components/ui/icons';
import { explorerPropertyType } from '../../services/explorerProperties';
import PropertyTypeIcon from './PropertyTypeIcon';
import './explorer-explore-view.css';
import useExplorerErrorToast from './useExplorerErrorToast';

type Row = Record<string, unknown>;
type Group = { value: unknown; count: number; sum?: number | string | null };
export type ExplorerExploreAggregation = {
  count?: number; groups?: Group[]; hasMore?: boolean; loading?: boolean; error?: string; limitReached?: boolean;
  onLoadMore?: () => void; onRetry?: () => void;
};
export type ExplorerExploreViewProps = {
  fields: string[]; names?: Record<string, string>; types?: Record<string, string>; rows: Row[];
  field: string; select: (field: string) => void; onFilter: (field: string) => void;
  onFilterValue: (field: string, value: unknown) => void; aggregation?: ExplorerExploreAggregation;
};

function propertyType(field: string, types: Record<string, string> | undefined, rows: Row[]) {
  const declared = types?.[field];
  if (declared) return ['string', 'integer', 'number', 'boolean', 'date', 'datetime', 'json'].includes(declared) ? declared : explorerPropertyType(declared);
  const value = rows.find(row => row[field] != null)?.[field];
  return value != null && typeof value === 'object' ? 'json' : typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string';
}
function mediaProperty(field: string, names: Record<string, string> | undefined, values: unknown[]) {
  const name = [field, names?.[field]].join(' ').replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase();
  return /(?:^|[_\s])(?:url|uri)(?:$|[_\s])/.test(name)
    || values.some(value => typeof value === 'string' && (/^(?:data:|blob:|\/media\/)|\.(?:png|jpe?g|gif|webp|svg|mp4|webm|mov|mp3|wav|ogg|pdf)(?:[?#].*)?$/i.test(value)
      || /image|video|audio|media|attachment|file|图片|视频|音频|附件/.test(name) && /^https?:\/\//i.test(value)));
}
function scalar(value: unknown) { return value == null || ['string', 'number', 'boolean'].includes(typeof value); }
function valueKey(value: unknown) { return value == null ? 'null' : typeof value + ':' + String(value); }
function valueLabel(value: unknown) { return value == null ? 'No value' : value === '' ? 'Empty string' : typeof value === 'boolean' ? value ? 'True' : 'False' : String(value); }
const number = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 2 });

export function chooseExploreField(fields: string[], types: Record<string, string> | undefined, rows: Row[], names?: Record<string, string>): string {
  const categorical = fields.filter(field => ['string', 'boolean'].includes(propertyType(field, types, rows))
    && !/(?:^|[_\s])(?:id|key|uuid)(?:$|[_\s])|编号|标识/i.test([field, names?.[field]].join(' '))
    && !mediaProperty(field, names, rows.map(row => row[field])));
  return categorical.find(field => {
    const values = rows.map(row => row[field]).filter(value => value != null);
    const count = new Set(values.map(valueKey)).size;
    return count >= 2 && count <= 12 && count < values.length;
  }) ?? categorical.find(field => propertyType(field, types, rows) === 'boolean') ?? categorical[0] ?? fields[0] ?? '';
}

export default function ExplorerExploreView({ fields, names, types, rows, field, select, onFilter, onFilterValue, aggregation }: ExplorerExploreViewProps) {
  const [sort, setSort] = useState('count');
  const [view, setView] = useState<'bars' | 'table'>('bars');
  const [showAll, setShowAll] = useState(false);
  const type = propertyType(field, types, rows), label = names?.[field] || field;
  const values = aggregation ? (aggregation.groups ?? []).map(group => group.value) : rows.map(row => row[field]);
  const structured = type === 'json' || values.some(value => !scalar(value));
  const media = mediaProperty(field, names, values);
  const chartable = !structured && !media;
  const localGroups = new Map<string, Group>();
  if (!aggregation && chartable) for (const value of values) {
    const key = valueKey(value), existing = localGroups.get(key);
    if (existing) existing.count += 1;
    else localGroups.set(key, { value, count: 1 });
  }
  const groups = aggregation ? aggregation.groups ?? [] : Array.from(localGroups.values());
  const total = aggregation ? aggregation.count : rows.length;
  const complete = !aggregation || total !== undefined && !aggregation.hasMore && !aggregation.loading && !aggregation.error;
  const nullGroup = groups.find(group => group.value == null);
  const missing = aggregation ? nullGroup?.count ?? (complete && chartable ? 0 : undefined) : values.filter(value => value == null).length;
  const distinct = chartable && (complete || groups.length) ? (complete ? '' : '≥') + number(groups.filter(group => group.value != null).length) : '—';
  const ordered = chartable ? [...groups].sort((left, right) => sort === 'count' ? right.count - left.count || valueLabel(left.value).localeCompare(valueLabel(right.value))
    : typeof left.value === 'number' && typeof right.value === 'number' ? left.value - right.value : valueLabel(left.value).localeCompare(valueLabel(right.value))) : [];
  const shown = showAll ? ordered : ordered.slice(0, 12), maximum = Math.max(1, ...ordered.map(group => group.count));
  const numeric = complete && ['integer', 'number'].includes(type) && groups.filter(group => group.value != null).every(group => typeof group.value === 'number' && Number.isFinite(group.value));
  const numericGroups = numeric ? groups.filter(group => group.value != null) as { value: number; count: number }[] : [];
  const numericCount = numericGroups.reduce((sum, group) => sum + group.count, 0);
  const busy = !!aggregation?.loading, error = aggregation?.error;
  useExplorerErrorToast(aggregation?.limitReached ? undefined : error, 'Unable to load this analysis', aggregation?.onRetry);
  return <section className="explore-analysis" aria-label="Explore results" aria-busy={busy}>
    <header className="explore-analysis__header"><div><h2>Explore</h2><p>Analyze a property and select values to refine your results.</p></div>
      <label className="explore-analysis__property"><span>Property</span><div><PropertyTypeIcon type={type} /><NativeSelect aria-label="Property to analyze" disabled={!fields.length} value={field} onChange={event => { setShowAll(false); select(event.target.value); }}>
        {fields.map(name => <option key={name} value={name}>{names?.[name] || name}</option>)}
      </NativeSelect></div></label>
    </header>
    <div className="explore-analysis__scope"><span>{aggregation ? 'Current object set' : 'Current results page'}</span>
      <p>{aggregation ? 'Includes the current search, filters and saved object set.' : 'Statistics cover the loaded page, not the full object set.'}</p>
    </div>
    <dl className="explore-analysis__summary">
      <div><dt>Objects</dt><dd>{total === undefined ? '—' : number(total)}</dd></div>
      <div><dt>With value</dt><dd>{total === undefined || missing === undefined ? '—' : number(total - missing)}</dd></div>
      <div><dt>No value</dt><dd>{missing === undefined ? '—' : number(missing)}</dd></div>
      <div><dt>Distinct values</dt><dd>{distinct}</dd></div>
    </dl>
    <article className="explore-analysis__card">
      <header><div><PropertyTypeIcon type={type} /><h3>{label || 'Choose a property'}</h3><span>{media ? 'Media' : ({ string: 'Text', integer: 'Number', number: 'Number', boolean: 'Boolean', date: 'Date', datetime: 'Timestamp', json: 'JSON' } as Record<string, string>)[type]}</span></div>
        <button type="button" disabled={!field} onClick={() => onFilter(field)}><FilterOutlined /> Add filter</button>
      </header>
      {error ? aggregation?.limitReached ? <div className="explore-analysis__empty" role="alert"><strong>Unable to load this analysis</strong><p>{error}</p>{aggregation?.onRetry && <button type="button" onClick={aggregation.onRetry}>Retry</button>}</div> : null
        : busy && !groups.length ? <div className="explore-analysis__empty" role="status"><ReloadOutlined spin /><span>Calculating property values…</span></div>
          : !field || !fields.length ? <div className="explore-analysis__empty"><p>Choose an available property to begin.</p></div>
            : !chartable ? <div className="explore-analysis__empty"><PropertyTypeIcon type={type} /><strong>{media ? 'Media properties are explored in object details' : 'Structured properties do not have a scalar distribution'}</strong><p>Choose a text, number, date or boolean property to compare values. Use Add filter to refine this property.</p></div>
              : !groups.length ? <div className="explore-analysis__empty"><BarChartOutlined /><strong>No values to analyze</strong><p>Change the current filters or choose another property.</p></div> : <>
                <div className="explore-analysis__controls"><span>Value distribution</span><div><label>Sort <NativeSelect aria-label="Sort analysis values" value={sort} onChange={event => setSort(event.target.value)}><option value="count">Count, high to low</option><option value="value">Value, ascending</option></NativeSelect></label>
                  <div role="group" aria-label="Analysis display"><button type="button" aria-label="Show bar chart" title="Bar chart" aria-pressed={view === 'bars'} onClick={() => setView('bars')}><BarChartOutlined /></button><button type="button" aria-label="Show value table" title="Value table" aria-pressed={view === 'table'} onClick={() => setView('table')}><TableOutlined /></button></div>
                </div></div>
                {numericCount > 0 && <p className="explore-analysis__range">Min <strong>{number(Math.min(...numericGroups.map(group => group.value)))}</strong><span>Max <strong>{number(Math.max(...numericGroups.map(group => group.value)))}</strong></span><span>Average <strong>{number(numericGroups.reduce((sum, group) => sum + group.value * group.count, 0) / numericCount)}</strong></span></p>}
                <div className={`explore-analysis__distribution${view === 'table' ? ' is-table' : ''}`}>
                  <div className="explore-analysis__columns"><span>Value</span><span aria-hidden="true" /><span>Objects</span><span>Share</span></div>
                  {shown.map(group => <button type="button" key={valueKey(group.value)} title={`Filter ${label}: ${valueLabel(group.value)}`} disabled={busy || typeof group.value === 'string' && group.value.length > 4096}
                    aria-label={`Filter ${label} to ${valueLabel(group.value)}, ${number(group.count)} objects`} onClick={() => onFilterValue(field, group.value)} className="explore-analysis__value">
                    <span>{valueLabel(group.value)}</span><span className="explore-analysis__bar" aria-hidden="true"><i style={{ width: group.count / maximum * 100 + '%' }} /></span><strong>{number(group.count)}</strong><span>{total ? (group.count / total * 100).toLocaleString('en-US', { maximumFractionDigits: 1 }) + '%' : '—'}</span>
                  </button>)}
                </div>
                <footer className="explore-analysis__footer"><span>Showing {number(shown.length)}{aggregation?.hasMore ? '+' : ` of ${number(groups.length)}`} values</span>
                  <div>{ordered.length > 12 && <button type="button" onClick={() => setShowAll(value => !value)}>{showAll ? 'Show less' : `Show all ${number(ordered.length)} values`}</button>}
                    {aggregation?.hasMore && aggregation.onLoadMore && <button type="button" disabled={busy} onClick={() => { setShowAll(true); aggregation.onLoadMore?.(); }}>{busy ? 'Loading…' : 'Load more values'}</button>}</div>
                </footer>
              </>}
    </article>
  </section>;
}

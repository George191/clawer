import { useEffect, useState } from 'react';
import { Select } from '@/components/ui';
import { fetchEtlTableColumns, queryExplorerPreview, type EtlPublishedApi } from '../../services/api';

const sampleLimit = 50;
type RangeField = 'filterType' | 'filterSource';
type RangeOption = { field: RangeField; column: string; label: string; empty: string; values: string[] };

export default function PublishedApiRangeFilters({schema, table, item, disabled, onUpdate}: {
  schema: string; table: string; item: EtlPublishedApi; disabled: boolean;
  onUpdate: (changes: Partial<Pick<EtlPublishedApi, RangeField>>) => void;
}) {
  const [fields, setFields] = useState<RangeOption[]>();
  const [error, setError] = useState('');
  const [sampling, setSampling] = useState(true);
  useEffect(() => {
    const read = new AbortController();
    async function load() {
      try {
        const columns = await fetchEtlTableColumns(schema, table, read.signal);
        if (read.signal.aborted) return;
        const names = new Set(columns.map(column => column.name));
        const options: RangeOption[] = [];
        if (names.has('data_type')) options.push({field: 'filterType', column: 'data_type', label: 'Data types', empty: 'All data types', values: []});
        const source = names.has('data_source') ? 'data_source' : names.has('source') ? 'source' : undefined;
        if (source) options.push({field: 'filterSource', column: source, label: 'Sources', empty: 'All sources', values: []});
        setFields(options);
        if (!options.length) return;
        const {rows} = await queryExplorerPreview(schema, table, {limit: sampleLimit, offset: 0, filters: [], sorts: [], partition: item.partition}, read.signal);
        if (read.signal.aborted) return;
        setFields(options.map(option => ({...option, values: [...new Set(rows.flatMap(row => {
          const value = row[option.column];
          return value == null || value === '' ? [] : [String(value)];
        }))].sort()})));
      } catch {
        if (!read.signal.aborted) setError('Unable to load suggested range values.');
      } finally {
        if (!read.signal.aborted) setSampling(false);
      }
    }
    void load();
    return () => read.abort();
  }, [schema, table, item.partition]);

  if (!fields) return <p role="status">{error || 'Loading range fields…'}</p>;
  if (!fields.length) return <p>This table has no data type or source column. Use property conditions to limit the range.</p>;
  return <>
    <div className="api-validity-fields">{fields.map(({field, column, label, empty, values}) => <label key={field}>
      <span>{label} <code>({column})</code></span>
      <Select mode="tags" size="small" popupClassName="flow-select-popup" aria-label={`Published ${label.toLowerCase()}`}
        disabled={disabled} loading={sampling} value={item[field]} onChange={(selected: string[]) => onUpdate({[field]: selected})}
        options={values.map(value => ({value, label: value}))} placeholder={empty} allowClear
        notFoundContent={sampling ? 'Loading suggestions…' : 'Type an exact value and press Enter.'} />
    </label>)}</div>
    <p>{error && <span role="status">{error} </span>}Leave a field empty to include all its values. Suggestions come from up to {sampleLimit} rows. Type an exact value and press Enter to add it.</p>
  </>;
}

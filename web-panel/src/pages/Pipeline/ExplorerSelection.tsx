import { lazy, Suspense, useId, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { Button, Checkbox, Input, Table } from '@/components/ui';
import type { ColumnsType, TableProps } from '@/components/ui';
import { ArrowLeftOutlined, LinkOutlined, SearchOutlined, TableOutlined } from '@/components/ui/icons';
import './explorer-selection.css';

type RecordRow = Record<string, unknown>;
export type ExplorerPropertyPresentation = 'value' | 'timeSeries' | 'geoJson' | 'media';
export type ExplorerPreviewRow = {
  record: RecordRow;
  key: string;
  title: string;
  identity: RecordRow;
  columnTypes: Record<string, string>;
  version?: string;
};

const titleProperties = ['title', 'name', 'display_name', 'label'];
const previewLimit = 20;
const noTypes: Record<string, string> = {};
const ExplorerPropertyPreview = lazy(() => import('./ExplorerPropertyPreview'));
const format = (value: unknown) => typeof value === 'object' ? JSON.stringify(value) : String(value);
const propertyValue = (row: ExplorerPreviewRow, name: string) =>
  Object.prototype.hasOwnProperty.call(row.identity, name) ? row.identity[name] : row.record[name];

export function explorerRowTitle(row: RecordRow, identity: RecordRow, fallback = 'Untitled row') {
  const value = titleProperties.map(name => row[name]).find(item => typeof item === 'string' && item.trim());
  return typeof value === 'string' ? value : Object.values(identity).map(format).join(' · ') || fallback;
}

function PropertyValue({ value }: { value: unknown }) {
  if (value === undefined) return <span className="flow-explorer-selection__empty-value">Missing</span>;
  if (value === null) return <span className="flow-explorer-selection__empty-value">No value</span>;
  if (value === '') return <span className="flow-explorer-selection__empty-value">Empty string</span>;
  return <span className="flow-explorer-selection__property-value">{format(value)}</span>;
}

export function ExplorerProperties({ row, columns = Object.keys(row.record), presentations }: {
  row: ExplorerPreviewRow; columns?: readonly string[]; presentations?: Record<string, ExplorerPropertyPresentation>;
}) {
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');
  const [valueFilter, setValueFilter] = useState('');
  const fieldType = (field: string) => row.columnTypes[field] || 'Unknown';
  const types = [...new Set(columns.map(fieldType))].sort();
  const search = query.trim().toLocaleLowerCase();
  const visible = columns.filter(field => {
    const value = propertyValue(row, field);
    const empty = value === undefined || value === null || value === '';
    return (!search || field.toLocaleLowerCase().includes(search))
      && (!type || fieldType(field) === type)
      && (!valueFilter || empty === (valueFilter === 'empty'));
  });
  return <section className="flow-explorer-selection__properties" aria-label="Object properties">
    <div className="flow-explorer-selection__filters">
      <Input size="small" prefix={<SearchOutlined />} aria-label="Search properties" placeholder="Search properties"
        allowClear value={query} onChange={event => setQuery(event.target.value)} />
      <label><span>Type</span><select aria-label="Property type" value={type} onChange={event => setType(event.target.value)}>
        <option value="">All types</option>
        {types.map(item => <option key={item}>{item}</option>)}
      </select></label>
      <label><span>Values</span><select aria-label="Property values" value={valueFilter} onChange={event => setValueFilter(event.target.value)}>
        <option value="">All values</option><option value="present">With value</option><option value="empty">Empty</option>
      </select></label>
    </div>
    {visible.length ? <dl className="flow-explorer-selection__fields">{visible.map(field => {
      const kind = presentations?.[field];
      const value = propertyValue(row, field);
      return <div key={field}>
        <dt><span>{field}</span><small>{fieldType(field)}</small></dt>
        <dd>{kind && kind !== 'value' && value != null && value !== '' ? <Suspense fallback={<span role="status">Loading property preview…</span>}>
          <ExplorerPropertyPreview kind={kind} value={value} label={field} />
        </Suspense> : <PropertyValue value={value} />}</dd>
      </div>;
    })}</dl> : <p role="status">{columns.length ? 'No properties match these filters.' : 'This row has no properties.'}</p>}
  </section>;
}

function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object' || Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as RecordRow, b = right as RecordRow;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key => Object.prototype.hasOwnProperty.call(b, key) && sameValue(a[key], b[key]));
}

function Comparison({ columns, choices }: { columns: readonly string[]; choices: ExplorerPreviewRow[] }) {
  const [leftKey, setLeft] = useState(choices[0].key);
  const [rightKey, setRight] = useState(choices[1].key);
  const [differencesOnly, setDifferencesOnly] = useState(false);
  const left = choices.find(item => item.key === leftKey) ?? choices[0];
  const right = choices.find(item => item.key === rightKey && item.key !== left.key) ?? choices.find(item => item.key !== left.key)!;
  const fields = columns.filter(field => !differencesOnly || !sameValue(propertyValue(left, field), propertyValue(right, field)));
  function objectPicker(label: string, row: ExplorerPreviewRow, other: ExplorerPreviewRow, update: (key: string) => void) {
    return <label><span>{label}</span><select aria-label={label} value={row.key} onChange={event => update(event.target.value)}>
      {choices.map(item => <option key={item.key} value={item.key} disabled={item.key === other.key}>{item.title}</option>)}
    </select></label>;
  }
  return <section aria-label="Object comparison" className="flow-explorer-selection__comparison">
    <div className="flow-explorer-selection__comparison-controls">
      {objectPicker('First object', left, right, setLeft)}
      {objectPicker('Second object', right, left, setRight)}
      <Checkbox checked={differencesOnly} onChange={event => setDifferencesOnly(event.target.checked)}>Differences only</Checkbox>
    </div>
    {fields.length ? <table><thead><tr><th scope="col">Property</th><th scope="col">{left.title}</th><th scope="col">{right.title}</th></tr></thead>
      <tbody>{fields.map(field => {
        const leftValue = propertyValue(left, field), rightValue = propertyValue(right, field);
        const different = !sameValue(leftValue, rightValue);
        return <tr key={field} className={different ? 'is-different' : undefined}>
          <th scope="row">{field}{different && <small>Different</small>}</th>
          <td><PropertyValue value={leftValue} /></td><td><PropertyValue value={rightValue} /></td>
        </tr>;
      })}</tbody></table> : <p role="status">No differing properties.</p>}
  </section>;
}

type Props = {
  schema: string;
  table: string;
  rows: RecordRow[];
  columns: readonly string[];
  columnTypes?: Record<string, string>;
  presentations?: Record<string, ExplorerPropertyPresentation>;
  dataColumns: ColumnsType<RecordRow>;
  rowKey: (record: RecordRow, index: number) => string;
  rowIdentity: (record: RecordRow) => RecordRow;
  rowVersion: (record: RecordRow) => string | undefined;
  loading: boolean;
  locked: boolean;
  footer?: ReactNode;
  renderTitleLink?: (row: ExplorerPreviewRow) => ReactNode;
  renderFullDetails?: (row: ExplorerPreviewRow) => ReactNode;
  renderRelationships?: (row: ExplorerPreviewRow) => ReactNode;
  renderActions?: (row: ExplorerPreviewRow) => ReactNode;
  renderMutation?: (row: ExplorerPreviewRow) => ReactNode;
  renderJson?: (row: ExplorerPreviewRow) => ReactNode;
};

type Selection = { rows: RecordRow[]; picked: number[]; active: number; anchor: number; collapsed: boolean };

export default function ExplorerSelection({
  schema, table, rows, columns, columnTypes = noTypes, presentations, dataColumns, rowKey, rowIdentity, rowVersion, loading, locked, footer,
  renderTitleLink, renderFullDetails, renderRelationships, renderActions, renderMutation, renderJson,
}: Props) {
  const panelId = useId();
  const results = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const restore = useRef<HTMLButtonElement>(null);
  const [selection, setSelection] = useState<Selection>();
  const [view, setView] = useState<'properties' | 'links' | 'json' | 'compare'>('properties');
  const current = selection?.rows === rows ? selection : undefined;
  const picked = current?.picked ?? [];
  const displayed = picked.slice(0, previewLimit);
  const selected = new Set(picked);
  const activeIndex = displayed.includes(current?.active ?? -1) ? current!.active : displayed[0];
  const disabled = loading || locked;

  function describeRow(record: RecordRow, index: number): ExplorerPreviewRow {
    const identity = rowIdentity(record);
    return { record, identity, key: rowKey(record, index), title: explorerRowTitle(record, identity, 'Row ' + (index + 1)),
      columnTypes, version: rowVersion(record) };
  }
  const findRow = (index: number) => results.current?.querySelector<HTMLTableRowElement>('[data-preview-index="' + index + '"]');
  const select = (indices: number[], active: number, anchor = active) => {
    if (disabled) return;
    const unique = [...new Set(indices)].sort((a, b) => a - b);
    setSelection({ rows, picked: unique, active, anchor, collapsed: false });
    if (!picked.length || unique.length < 2 && view === 'compare') setView('properties');
  };
  const selectIndex = (index: number, mode: 'row' | 'toggle' | 'range') => {
    if (mode === 'range' && current && picked.length) {
      const start = Math.min(current.anchor, index);
      const end = Math.max(current.anchor, index);
      select([...picked, ...Array.from({ length: end - start + 1 }, (_, offset) => start + offset)], index, current.anchor);
    } else if (mode === 'toggle') {
      select(selected.has(index) ? picked.filter(item => item !== index) : [...picked, index], index);
    } else {
      select(selected.has(index) ? picked : [index], index);
      if (!selected.has(index)) setView('properties');
    }
  };
  const collapse = () => {
    if (!current || disabled) return;
    setSelection({ ...current, collapsed: true });
    requestAnimationFrame(() => restore.current?.focus());
  };
  const show = () => {
    if (!current) return;
    setSelection({ ...current, collapsed: false });
    requestAnimationFrame(() => heading.current?.focus());
  };
  const clear = () => {
    if (disabled) return;
    findRow(activeIndex)?.focus({ preventScroll: true });
    setSelection(undefined);
    setView('properties');
  };

  function rowKeyboard(event: KeyboardEvent<HTMLTableRowElement>, index: number) {
    if (event.target !== event.currentTarget || disabled) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectIndex(index, event.shiftKey ? 'range' : event.key === ' ' || event.ctrlKey || event.metaKey ? 'toggle' : 'row');
      if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey) requestAnimationFrame(() => heading.current?.focus());
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      const target = Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
      if (event.shiftKey) {
        if (!picked.length) select([index, target], target, index);
        else selectIndex(target, 'range');
      }
      findRow(target)?.focus();
    }
  }
  const rowProps: TableProps<RecordRow>['onRow'] = (_record, index = 0) => ({
    'data-preview-index': index,
    className: 'flow-explorer-result-row',
    'aria-selected': selected.has(index),
    'aria-keyshortcuts': 'Enter Space Shift+ArrowDown Shift+ArrowUp',
    tabIndex: loading ? -1 : 0,
    onClick: event => {
      if (disabled || !(event.target instanceof Element) || event.target.closest('button, a, input')) return;
      event.currentTarget.focus({ preventScroll: true });
      selectIndex(index, event.shiftKey ? 'range' : event.ctrlKey || event.metaKey ? 'toggle' : 'row');
    },
    onKeyDown: event => rowKeyboard(event, index),
  });
  const checkboxProps = {
    fixed: true,
    selectedRowKeys: picked.map(index => rowKey(rows[index], index)),
    getCheckboxProps: (record: RecordRow) => ({
      disabled, 'aria-label': 'Select ' + describeRow(record, rows.indexOf(record)).title,
    }),
    onChange: (_keys: unknown[], checked: RecordRow[], info?: { type: string }) => {
      const indices = checked.map(record => rows.indexOf(record));
      const active = info?.type === 'all' ? indices[0] : indices.find(index => !selected.has(index)) ?? activeIndex;
      select(indices, active ?? -1);
    },
  };
  const titleColumn = titleProperties.find(field => columns.includes(field));
  const titleCell = (_value: unknown, record: RecordRow, index: number) => {
    const row = describeRow(record, index);
    return <span className="flow-explorer-selection__table-title"><TableOutlined />
      {Object.keys(row.identity).length > 0 && renderTitleLink ? renderTitleLink(row) : <span>{row.title}</span>}
    </span>;
  };
  const tableColumns = dataColumns.map(column => {
    if (column.dataIndex === titleColumn) return { ...column, render: titleCell };
    if (typeof column.dataIndex !== 'string' || !column.render) return column;
    const field = column.dataIndex, render = column.render;
    return { ...column, render: (value: unknown, record: RecordRow, index: number) => {
      const identity = rowIdentity(record);
      return render(Object.prototype.hasOwnProperty.call(identity, field) ? identity[field] : value, record, index);
    } };
  });
  if (!titleColumn) tableColumns.unshift({ title: 'Title', key: 'explorer-result-title', render: titleCell });
  const active = activeIndex == null ? undefined : describeRow(rows[activeIndex], activeIndex);
  const panelOpen = Boolean(active && !current?.collapsed);
  const hasLinks = Boolean(active && renderRelationships && Object.keys(active.identity).length);
  const displayView = view === 'links' && !hasLinks ? 'properties' : view;
  const shownCount = Math.min(picked.length, previewLimit);
  return <div className={`flow-explorer-selection${panelOpen ? ' is-open' : ''}`}>
    <div ref={results} className="flow-explorer-selection__results">
      <header className="flow-explorer-selection__results-header"><h3>Results</h3>
        {!!picked.length && <span role="status">{picked.length} selected</span>}
        {current?.collapsed && active && <>
          <button ref={restore} type="button" className="flow-explorer-selection__control"
            aria-controls={panelId} aria-expanded={false} onClick={show}>Show selection preview</button>
          <Button size="small" disabled={disabled} onClick={clear}>Clear selection</Button>
        </>}
      </header>
      <Table<RecordRow> className="flow-data-table" size="small" loading={loading} pagination={false} rowKey={(record, index = 0) => rowKey(record, index)}
        scroll={{ x: 'max-content' }} dataSource={rows} columns={tableColumns} rowSelection={checkboxProps} onRow={rowProps} />
      {footer}
    </div>
    {panelOpen && active && <aside className="flow-explorer-selection__panel" id={panelId} aria-labelledby={panelId + '-heading'}
      onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented && !disabled) { event.preventDefault(); collapse(); } }}>
      <header className="flow-explorer-selection__panel-header">
        <button className="flow-explorer-selection__control" type="button" aria-label="Collapse selection preview" aria-controls={panelId}
          aria-expanded={true} disabled={disabled} onClick={collapse}><ArrowLeftOutlined /></button>
        <h3 ref={heading} id={panelId + '-heading'} tabIndex={-1}>Selection Preview</h3>
        {picked.length > 1 && <Button size="small" disabled={disabled} aria-pressed={displayView === 'compare'}
          onClick={() => setView(displayView === 'compare' ? 'properties' : 'compare')}>Compare objects</Button>}
        <Button size="small" disabled={disabled} onClick={clear}>Clear selection</Button>
      </header>
      {picked.length > 1 && <div className="flow-explorer-selection__picked">
        <div className="flow-explorer-selection__cards" role="group" aria-label="Selected objects">
          {displayed.map(index => {
            const row = describeRow(rows[index], index);
            return <button type="button" key={row.key} aria-pressed={activeIndex === index && displayView !== 'compare'}
              title={row.title} disabled={disabled} onClick={() => {
                setSelection(current => current && { ...current, active: index });
                setView('properties');
              }}><TableOutlined /><span>{row.title}</span></button>;
          })}
        </div>
        {picked.length > shownCount && <p>Showing the first {shownCount} of {picked.length} selected objects.</p>}
      </div>}
      <div className="flow-explorer-selection__content">
        {displayView === 'compare' && displayed.length > 1
          ? <Comparison columns={columns} choices={displayed.map(index => describeRow(rows[index], index))} />
          : <>
            <div className="flow-explorer-selection__object">
              <span className="flow-explorer-selection__icon" aria-hidden="true"><TableOutlined /></span>
              <div><span>{schema}.{table}</span><h4>{active.title}</h4>
                {!!Object.keys(active.identity).length && <small>{Object.entries(active.identity).map(([field, value]) => field + ': ' + format(value)).join(' · ')}</small>}
              </div>
              {!!Object.keys(active.identity).length && renderFullDetails?.(active)}
            </div>
            {renderMutation?.(active) ?? <>
              <div className="flow-explorer-selection__view">
                <nav aria-label="Selected object view">
                  <button type="button" aria-pressed={displayView === 'properties'} onClick={() => setView('properties')}>Properties</button>
                  {hasLinks && <button type="button" aria-pressed={displayView === 'links'} onClick={() => setView('links')}><LinkOutlined /> Links</button>}
                  <button type="button" aria-pressed={displayView === 'json'} onClick={() => setView('json')}>JSON preview</button>
                </nav>
                {picked.length === 1 && renderActions?.(active)}
              </div>
              {displayView === 'links' ? renderRelationships?.(active)
                : displayView === 'json' ? <section aria-label="Row JSON">{renderJson?.(active) ?? <pre>{JSON.stringify(active.record, null, 2)}</pre>}</section>
                : <ExplorerProperties key={active.key} row={active} columns={columns} presentations={presentations} />}
            </>}
          </>}
      </div>
    </aside>}
  </div>;
}

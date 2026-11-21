import { PropertyLabel } from './PropertyTypeIcon';
import { ExplorerValueProvider } from './ExplorerValue';
import { useExplorerLinkAvailability } from './ExplorerRowLinks';
import type { ExplorerRowLinkPage } from '../../services/api';
import type { ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { Button, Checkbox, Dropdown, Input, Select, Table, message } from '@/components/ui';
import type { ColumnsType, MenuItem, TableProps } from '@/components/ui';
import { BarsOutlined, CopyOutlined, DownOutlined, LinkOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import {
  ExplorerFieldValue, ExplorerObjectOverview, ExplorerTypeIcon,
  explorerFields, explorerFieldValue, explorerRowTitle, propertyValue,
  type ExplorerOverviewPresentation, type ExplorerPresentationRead, type ExplorerPreviewRow, type ExplorerPropertyPresentation,
} from './ExplorerObjectOverview';
import './explorer-selection.css';
import { selectedPageRows } from './explorerSelectionState';

type RecordRow = Record<string, unknown>;
export { ExplorerObjectOverview, ExplorerTypeIcon, explorerRowTitle, useExplorerPresentation } from './ExplorerObjectOverview';
export type { ExplorerOverviewPresentation, ExplorerPreviewRow, ExplorerPropertyPresentation } from './ExplorerObjectOverview';
const previewLimit = 20;
const noTypes: Record<string, string> = {};
const format = (value: unknown) => typeof value === 'object' ? JSON.stringify(value) : String(value);

export function ExplorerRecordActions({ items, disabled, row }: { items: MenuItem[]; disabled?: boolean; row?: ExplorerPreviewRow }) {
  const actions = row ? [...items, { key: 'copy-json', label: 'Copy as JSON', icon: <CopyOutlined />, onClick: async () => {
    const text = JSON.stringify({ ...row.record, ...row.identity }, null, 2);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const field = document.createElement('textarea');
        field.value = text; field.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.appendChild(field); field.select();
        try { if (!document.execCommand('copy')) throw new Error('Clipboard unavailable'); }
        finally { field.remove(); focused?.focus(); }
      }
      message.success('JSON copied');
    } catch { message.error('Unable to copy JSON. Check clipboard permissions.'); }
  } }] : items;
  return actions.length ? <Dropdown trigger={['click']} placement="bottomRight" overlayClassName="explorer-record-actions-menu" menu={{ items: actions }}
    getPopupContainer={(anchor: HTMLElement) => anchor.closest('.explorer-object-workspace') ?? document.body}>
    <Button size="small" disabled={disabled}>Actions <DownOutlined /></Button>
  </Dropdown> : null;
}

export function ExplorerProperties({ row, columns = Object.keys(row.record), presentation, presentations }: {
  row: ExplorerPreviewRow; columns?: readonly string[]; presentation?: ExplorerOverviewPresentation;
  presentations?: Record<string, ExplorerPropertyPresentation>;
}) {
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');
  const [valueFilter, setValueFilter] = useState('');
  const fields = explorerFields(row, columns, presentation, presentations);
  const types = [...new Set(fields.map(field => field.type))].sort();
  const search = query.trim().toLocaleLowerCase();
  const visible = fields.filter(field => {
    const value = explorerFieldValue(row, field);
    const empty = value === undefined || value === null || value === '';
    return (!search || field.label.toLocaleLowerCase().includes(search) || !!field.field?.toLocaleLowerCase().includes(search))
      && (!type || field.type === type)
      && (!valueFilter || empty === (valueFilter === 'empty'));
  });
  return <section className="flow-explorer-selection__properties" aria-label="Object properties">
    <div className="flow-explorer-selection__filters">
      <Input size="small" prefix={<SearchOutlined />} aria-label="Search properties" placeholder="Search properties"
        allowClear value={query} onChange={event => setQuery(event.target.value)} />
      <Select size="small" popupClassName="flow-select-popup" aria-label="Property type" value={type} onChange={setType}
        options={[{ value: '', label: 'All types' }, ...types.map(item => ({ value: item, label: item }))]} />
      <Select size="small" popupClassName="flow-select-popup" aria-label="Property values" value={valueFilter} onChange={setValueFilter}
        options={[{ value: '', label: 'All values' }, { value: 'present', label: 'With value' }, { value: 'empty', label: 'Empty' }]} />
    </div>
    {visible.length ? <dl className="flow-explorer-selection__fields">{visible.map(field => <div key={field.key}>
      <dt title={field.description || undefined}><PropertyLabel type={field.type}>{field.label}</PropertyLabel></dt>
      <dd><ExplorerFieldValue row={row} field={field} /></dd>
    </div>)}</dl> : <p role="status">{fields.length ? 'No properties match these filters.' : 'This row has no visible properties.'}</p>}
  </section>;
}

function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object' || Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as RecordRow, b = right as RecordRow;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key => Object.prototype.hasOwnProperty.call(b, key) && sameValue(a[key], b[key]));
}

function Comparison({ columns, choices, presentations }: { columns: readonly string[]; choices: ExplorerPreviewRow[]; presentations?: Record<string, ExplorerPropertyPresentation> }) {
  const [leftKey, setLeft] = useState(choices[0].key);
  const [rightKey, setRight] = useState(choices[1].key);
  const [differencesOnly, setDifferencesOnly] = useState(false);
  const left = choices.find(item => item.key === leftKey) ?? choices[0];
  const right = choices.find(item => item.key === rightKey && item.key !== left.key) ?? choices.find(item => item.key !== left.key)!;
  const fields = columns.filter(field => !differencesOnly || !sameValue(propertyValue(left, field), propertyValue(right, field)));
  function objectPicker(label: string, row: ExplorerPreviewRow, other: ExplorerPreviewRow, update: (key: string) => void) {
    return <label><span>{label}</span><Select size="small" popupClassName="flow-select-popup" aria-label={label} value={row.key} onChange={update}
      options={choices.map(item => ({ value: item.key, label: item.title, disabled: item.key === other.key }))} /></label>;
  }
  return <section aria-label="Object comparison" className="flow-explorer-selection__comparison">
    <div className="flow-explorer-selection__comparison-controls">
      {objectPicker('First object', left, right, setLeft)}
      {objectPicker('Second object', right, left, setRight)}
      <Checkbox checked={differencesOnly} onChange={event => setDifferencesOnly(event.target.checked)}>Differences only</Checkbox>
    </div>
    {fields.length ? <div className="flow-explorer-selection__comparison-body"><table><thead><tr><th scope="col">Property</th><th scope="col">{left.title}</th><th scope="col">{right.title}</th></tr></thead>
      <tbody>{fields.map(field => {
        const leftValue = propertyValue(left, field), rightValue = propertyValue(right, field);
        const different = !sameValue(leftValue, rightValue);
        return <tr key={field} className={different ? 'is-different' : undefined}>
          <th scope="row"><PropertyLabel type={left.columnTypes[field] || right.columnTypes[field] || 'string'}>{field}</PropertyLabel>{different && <small>Different</small>}</th>
          <td><ExplorerFieldValue row={left} field={explorerFields(left, [field], undefined, presentations)[0]} /></td><td><ExplorerFieldValue row={right} field={explorerFields(right, [field], undefined, presentations)[0]} /></td>
        </tr>;
      })}</tbody></table></div> : <p role="status">No differing properties.</p>}
  </section>;
}

type Props = {
  source?: ExplorerDetailSource;
  schema: string;
  table: string;
  dataType?: string;
  rows: RecordRow[];
  columns: readonly string[];
  columnTypes?: Record<string, string>;
  presentations?: Record<string, ExplorerPropertyPresentation>;
  metadata?: ExplorerPresentationRead;
  dataColumns: ColumnsType<RecordRow>;
  rowKey: (record: RecordRow, index: number) => string;
  rowIdentity: (record: RecordRow) => RecordRow;
  rowVersion: (record: RecordRow) => string | undefined;
  loading: boolean;
  locked: boolean;
  footer?: ReactNode;
  renderTitleLink?: (row: ExplorerPreviewRow) => ReactNode;
  renderFullDetails?: (row: ExplorerPreviewRow) => ReactNode;
  renderRelationships?: (row: ExplorerPreviewRow, page?: ExplorerRowLinkPage) => ReactNode;
  rowActions?: (row: ExplorerPreviewRow) => MenuItem[];
  renderMutation?: (row: ExplorerPreviewRow) => ReactNode;
  onRefresh?: () => void;
  onSelectedRowsChange?: (rows: ExplorerPreviewRow[]) => void;
  selectedRows?: ExplorerPreviewRow[];
  selectionLimit?: number;
  renderGlyph?: (compact: boolean) => ReactNode;
  extraViews?: { key: string; label: ReactNode; render: (row: ExplorerPreviewRow) => ReactNode; available?: (row: ExplorerPreviewRow) => boolean }[];
  renderPublishedRelationships?: (row: ExplorerPreviewRow) => ReactNode;
  onActiveRowChange?: (row?: ExplorerPreviewRow) => void;
};

type Selection = { rows: RecordRow[]; picked: number[]; active: number; anchor: number; collapsed: boolean };
type View = 'overview' | 'properties' | 'links' | 'compare' | `group:${string}` | `extra:${string}`;

export default function ExplorerSelection({
  source, schema, table, dataType, rows, columns, columnTypes = noTypes, presentations, metadata, dataColumns, rowKey, rowIdentity, rowVersion, loading, locked, footer,
  renderTitleLink, renderFullDetails, renderRelationships, rowActions, renderMutation, onRefresh, onSelectedRowsChange,
  selectedRows, selectionLimit = Infinity, renderGlyph, extraViews = [], renderPublishedRelationships, onActiveRowChange,
}: Props) {
  const panelId = useId();
  const results = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const restore = useRef<HTMLButtonElement>(null);
  const refreshState = useRef<{ from: RecordRow[]; keys: Set<string>; active: string }>();
  const reported = useRef<ExplorerPreviewRow[]>([]);
  const presentation = metadata?.presentation;
  const typeName = presentation?.displayName || table;
  const [selection, setSelection] = useState<Selection>();
  const [activeKey, setActiveKey] = useState('');
  const [view, setView] = useState<View>('overview');
  const current = selection?.rows === rows ? selection : undefined;
  const controlled = selectedRows !== undefined;
  const controlledKeys = new Set(selectedRows?.map(row => row.key));
  const picked = controlled ? rows.flatMap((record, index) => controlledKeys.has(rowKey(record, index)) ? [index] : []) : current?.picked ?? [];
  const selected = new Set(picked);
  const previewIndices = picked.slice(0, previewLimit);
  const activeIndex = previewIndices.includes(current?.active ?? -1) ? current!.active : previewIndices[0];
  const disabled = loading || locked;

  function describeRow(record: RecordRow, index: number): ExplorerPreviewRow {
    const identity = rowIdentity(record);
    return { record, identity, key: rowKey(record, index),
      title: explorerRowTitle(record, identity, presentation?.titleProperty, presentation?.primaryKey, 'Row ' + (index + 1)),
      columnTypes, version: rowVersion(record) };
  }
  const choices = selectedRows ?? picked.map(index => describeRow(rows[index], index));
  const displayed = choices.slice(0, previewLimit);
  const active = controlled ? displayed.find(row => row.key === activeKey) ?? displayed[0]
    : activeIndex == null ? undefined : describeRow(rows[activeIndex], activeIndex);
  const glyph = (compact = false) => renderGlyph ? renderGlyph(compact) : <ExplorerTypeIcon dataType={dataType} table={table} compact={compact} />;
  const reportedActive = useRef<ExplorerPreviewRow>();
  useEffect(() => {
    const next = choices.length === 1 ? active : undefined;
    if (reportedActive.current?.record === next?.record && reportedActive.current?.key === next?.key) return;
    reportedActive.current = next;
    onActiveRowChange?.(next);
  });
  useEffect(() => {
    const snapshot = refreshState.current;
    if (!snapshot || snapshot.from === rows || loading) return;
    refreshState.current = undefined;
    const matching = rows.flatMap((record, index) => snapshot.keys.has(rowKey(record, index)) ? [index] : []);
    const index = matching.find(position => rowKey(rows[position], position) === snapshot.active) ?? matching[0];
    setSelection(matching.length ? { rows, picked: matching, active: index, anchor: index, collapsed: false } : undefined);
  }, [rows, loading, rowKey]);
  useEffect(() => {
    if (controlled || !onSelectedRowsChange) return;
    const next = picked.map(index => describeRow(rows[index], index));
    if (reported.current.length === next.length && next.every((row, index) => {
      const previous = reported.current[index];
      return row.record === previous.record && row.key === previous.key &&
        row.title === previous.title && row.version === previous.version;
    })) return;
    reported.current = next;
    onSelectedRowsChange(next);
  });
  useEffect(() => () => {
    if (controlled || !reported.current.length) return;
    reported.current = [];
    onSelectedRowsChange?.([]);
  }, [onSelectedRowsChange, controlled]);
  const findRow = (index: number) => results.current?.querySelector<HTMLTableRowElement>('[data-preview-index="' + index + '"]');
  const select = (indices: number[], active: number, anchor = active, preserveOtherPages = false) => {
    if (disabled) return;
    const unique = [...new Set(indices)].sort((a, b) => a - b);
    const next = unique.map(index => describeRow(rows[index], index));
    const pageKeys = new Set(rows.map((row, index) => rowKey(row, index)));
    let combined: ExplorerPreviewRow[];
    try { combined = selectedPageRows(choices, pageKeys, next, controlled && preserveOtherPages, selectionLimit); }
    catch (error) { message.warning((error as Error).message); return; }
    if (controlled) onSelectedRowsChange?.(combined);
    setActiveKey(rows[active] ? rowKey(rows[active], active) : '');
    setSelection({ rows, picked: unique, active, anchor, collapsed: false });
    if (!picked.length || unique.length < 2 && view === 'compare') setView('overview');
  };
  const selectIndex = (index: number, mode: 'row' | 'toggle' | 'range') => {
    if (mode === 'range' && current && picked.length) {
      const start = Math.min(current.anchor, index);
      const end = Math.max(current.anchor, index);
      select([...picked, ...Array.from({ length: end - start + 1 }, (_, offset) => start + offset)], index, current.anchor, true);
    } else if (mode === 'toggle') {
      select(selected.has(index) ? picked.filter(item => item !== index) : [...picked, index], index, index, true);
    } else {
      select(selected.has(index) ? picked : [index], index, index, selected.has(index));
      if (!selected.has(index)) setView('overview');
    }
  };
  const collapse = () => {
    if (!active || disabled) return;
    setSelection({ rows, picked, active: activeIndex ?? -1, anchor: current?.anchor ?? activeIndex ?? -1, collapsed: true });
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
    if (controlled) onSelectedRowsChange?.([]);
    setView('overview');
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
    columnWidth: 36,
    selectedRowKeys: picked.map(index => rowKey(rows[index], index)),
    getCheckboxProps: (record: RecordRow) => ({
      disabled: disabled || (source?.kind === 'published' && Object.keys(rowIdentity(record)).length === 0) || (choices.length >= selectionLimit && !controlledKeys.has(rowKey(record, rows.indexOf(record))) && !selected.has(rows.indexOf(record))),
      'aria-label': 'Select ' + describeRow(record, rows.indexOf(record)).title,
    }),
    onChange: (_keys: unknown[], checked: RecordRow[], info?: { type: string }) => {
      const indices = checked.map(record => rows.indexOf(record));
      const active = info?.type === 'all' ? indices[0] : indices.find(index => !selected.has(index)) ?? activeIndex;
      select(indices, active ?? -1, active ?? -1, true);
    },
  };
  const titleCell = (_value: unknown, record: RecordRow, index: number) => {
    const row = describeRow(record, index);
    return <span className="flow-explorer-selection__table-title" title={row.title}>
      {glyph(true)}
      {!metadata ? <span>Loading title…</span> : Object.keys(row.identity).length > 0 && renderTitleLink ? renderTitleLink(row) : <span>{row.title}</span>}
    </span>;
  };
  const tableColumns = dataColumns.map(column => {
    if (typeof column.dataIndex !== 'string' || !column.render) return column;
    const field = column.dataIndex, render = column.render;
    return { ...column, render: (value: unknown, record: RecordRow, index: number) => {
      const identity = rowIdentity(record);
      return render(Object.prototype.hasOwnProperty.call(identity, field) ? identity[field] : value, record, index);
    } };
  });
  tableColumns.unshift({ title: 'TITLE', key: 'explorer-result-title', className: 'flow-explorer-selection__title-column',
    width: 232, fixed: 'left', onHeaderCell: () => ({ scope: 'col' }), render: titleCell });
  const panelOpen = Boolean(active && !current?.collapsed);
  const links = useExplorerLinkAvailability(source?.kind === 'published' ? '' : schema, table, panelOpen && renderRelationships ? active?.identity : undefined, active?.version);
  const hasLinks = Boolean(links.page) || Boolean(renderPublishedRelationships && source?.kind === 'published');
  const groups = presentation?.groups ?? [];
  const group = view.startsWith('group:') ? view.slice(6) : undefined;
  const availableViews = active ? extraViews.filter(view => !view.available || view.available(active)) : [];
  const displayView = view === 'links' && !hasLinks || group && !groups.includes(group)
    || view.startsWith('extra:') && !availableViews.some(item => `extra:${item.key}` === view) ? 'overview' : view;
  const shownCount = Math.min(choices.length, previewLimit);
  const actions = active && choices.length === 1 ? rowActions?.(active) ?? [] : [];
  const refresh = () => {
    if (!active || !onRefresh || disabled) return;
    refreshState.current = {
      from: rows, active: active.key,
      keys: new Set(picked.filter(index => Object.keys(rowIdentity(rows[index])).length > 0)
        .map(index => rowKey(rows[index], index))),
    };
    onRefresh();
  };
  return <div className={`flow-explorer-selection${panelOpen ? ' is-open' : ''}`}>
    <div ref={results} role="region" aria-label="Object results" className="flow-explorer-selection__results">
      {current?.collapsed && active && <header className="flow-explorer-selection__results-header">
          <button ref={restore} type="button" className="flow-explorer-selection__control"
            aria-controls={panelId} aria-expanded={false} onClick={show}>Show selection preview</button>
          <Button size="small" disabled={disabled} onClick={clear}>Clear selection</Button>
      </header>}
      {metadata?.error && <div role="alert" className="flow-explorer-selection__mapping-error">
        Unable to load object presentation. {onRefresh && <Button size="small" onClick={onRefresh}>Retry</Button>}
      </div>}
      <Table<RecordRow> className="flow-data-table" size="small" loading={loading && !columns.length} pagination={false} rowKey={(record, index = 0) => rowKey(record, index)}
        scroll={{ x: 'max-content' }} dataSource={rows} columns={tableColumns} rowSelection={checkboxProps} onRow={rowProps} />
      {footer}
    </div>
    {panelOpen && active && <ExplorerValueProvider schema={schema} table={table} source={source}><aside className="flow-explorer-selection__panel" id={panelId} aria-labelledby={panelId + '-heading'}
      onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented && !disabled) { event.preventDefault(); collapse(); } }}>
      <header className="flow-explorer-selection__panel-header">
        <button className="flow-explorer-selection__control" type="button" aria-label="Collapse selection preview" aria-controls={panelId}
          aria-expanded={true} disabled={disabled} onClick={collapse}><BarsOutlined /></button>
        <h3 ref={heading} id={panelId + '-heading'} tabIndex={-1}>Selection Preview</h3>
        {choices.length > 1 && <Button size="small" disabled={disabled} aria-pressed={displayView === 'compare'}
          onClick={() => setView(displayView === 'compare' ? 'overview' : 'compare')}>Compare objects</Button>}
        <Button size="small" disabled={disabled} onClick={clear}>Clear selection</Button>
      </header>
      {choices.length > 1 && <div className="flow-explorer-selection__picked">
        <div className="flow-explorer-selection__cards" role="group" aria-label="Selected objects">
          {displayed.map(row => {
            return <button type="button" key={row.key} aria-pressed={active?.key === row.key && displayView !== 'compare'}
              title={row.title} disabled={disabled} onClick={() => {
                setActiveKey(row.key);
                const index = rows.findIndex((record, index) => rowKey(record, index) === row.key);
                setSelection(current => current && { ...current, active: index });
                setView('overview');
              }}>{glyph(true)}<span>{row.title}</span></button>;
          })}
        </div>
        {choices.length > shownCount && <p>Showing the first {shownCount} of {choices.length} selected objects.</p>}
      </div>}
      <div className="flow-explorer-selection__content">
        {displayView === 'compare' && displayed.length > 1
          ? <Comparison columns={columns} presentations={presentations} choices={displayed} />
          : <>
            <div className="flow-explorer-selection__object">
              {glyph()}
              <div className="flow-explorer-selection__identity"><span>{typeName}</span><h4>{metadata ? active.title : 'Loading title…'}</h4>
                {!!Object.keys(active.identity).length && <small>{Object.entries(active.identity).map(([field, value]) => field + ': ' + format(value)).join(' · ')}</small>}
              </div>
              <div className="flow-explorer-selection__object-actions">
                {onRefresh && <Button size="small" type="text" icon={<ReloadOutlined />} aria-label="Refresh selected records" title="Refresh selected records"
                  disabled={disabled} onClick={refresh} />}
                {!!Object.keys(active.identity).length && renderFullDetails?.(active)}
                <ExplorerRecordActions items={actions} row={active} disabled={disabled} />
              </div>
            </div>
            {renderMutation?.(active) ?? <>
              <div className="flow-explorer-selection__view">
                <nav aria-label="Selected object view">
                  <button type="button" aria-pressed={displayView === 'overview'} onClick={() => setView('overview')}>Overview</button>
                  <button type="button" aria-pressed={displayView === 'properties'} onClick={() => setView('properties')}>Properties</button>
                  {groups.map(name => <button type="button" key={name} aria-pressed={displayView === 'group:' + name}
                    onClick={() => setView(`group:${name}`)}>{name}</button>)}
                  {hasLinks && <button type="button" aria-pressed={displayView === 'links'} onClick={() => setView('links')}><LinkOutlined /> Links</button>}
                  {availableViews.map(view => <button type="button" key={view.key} aria-pressed={displayView === `extra:${view.key}`} onClick={() => setView(`extra:${view.key}`)}>{view.label}</button>)}
                </nav>
              </div>
              {links.error && <p role="alert">Unable to read relationships. <Button size="small" onClick={links.retry}>Retry</Button></p>}
              {displayView === 'links' ? renderPublishedRelationships?.(active) ?? renderRelationships?.(active, links.page)
                : displayView.startsWith('extra:') ? availableViews.find(view => `extra:${view.key}` === displayView)?.render(active)
                : !metadata ? <p role="status">Loading object presentation…</p>
                : metadata.error ? <p>Properties are unavailable until the object presentation loads.</p>
                : displayView === 'properties'
                  ? <ExplorerProperties key={active.key} row={active} columns={columns} presentation={presentation} presentations={presentations} />
                  : <ExplorerObjectOverview row={active} typeName={typeName} columns={columns}
                    presentation={presentation} presentations={presentations} group={displayView === 'overview' ? undefined : group} />}
            </>}
          </>}
      </div>
    </aside></ExplorerValueProvider>}
  </div>;
}

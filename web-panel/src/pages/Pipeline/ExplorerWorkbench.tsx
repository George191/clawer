import type { ComponentProps, ReactNode, Ref } from 'react';
import { Dropdown } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { ArrowDownOutlined, ArrowUpOutlined, DownOutlined, FilterOutlined, PlusOutlined } from '@/components/ui/icons';
import ExplorerWorkbenchHeader from './ExplorerWorkbenchHeader';
import ExplorerSelection from './ExplorerSelection';
import { ExplorerSelectionOverlayContext, useExplorerSelectionOverlay } from './ExplorerSelectionOverlayContext';
import { LayoutHeader, type useResultLayout } from './ExplorerResultLayout';
import { activeExplorerFilters, exactCellValue, type ExplorerFilter } from './explorerFilters';
import type { ExplorerSort } from '../../services/explorerLayouts';
export { ExplorerResultsFooter } from './ExplorerResultsFooter';

type Row = Record<string, unknown>;
type Layout = ReturnType<typeof useResultLayout>;

export function explorerResultColumns({ layout, sorts, sortFields, filters = [], types, names = {}, addFilter, openFilter, setSort, clearSorts, filterValue }: {
  layout: Layout; sorts: ExplorerSort[]; sortFields?: string[]; filters?: ExplorerFilter[]; types?: Record<string, string>; names?: Record<string, string>;
  addFilter: (field: string) => void; openFilter: (field: string) => void; setSort: (field: string, direction: 'asc' | 'desc') => void;
  clearSorts: () => void; filterValue: (field: string, value: string | null, exclude: boolean) => void;
}): ColumnsType<Row> {
  const filteredFields = new Set(activeExplorerFilters(filters).map(rule => rule.field));
  return layout.columns.filter(column => column.visible).map(({ field, width }) => {
    const sort = sorts.find(item => item.field === field), label = names[field] || field;
    const canSort = !sortFields || sortFields.includes(field);
    return {
      key: field, dataIndex: field, width,
      className: layout.layout.wrap ? 'result-cell-wrap' : 'result-cell-clip',
      onCell: () => ({ style: { width, minWidth: width, maxWidth: width } }),
      title: <LayoutHeader field={field} width={width} controls={layout}><Dropdown overlayClassName="flow-menu-popup flow-explorer-menu" trigger={['click']} menu={{
        items: [
          { key: 'asc', label: 'Sort ascending', icon: <ArrowUpOutlined />, disabled: !canSort },
          { key: 'desc', label: 'Sort descending', icon: <ArrowDownOutlined />, disabled: !canSort },
          { key: 'filter', label: 'Add filter', icon: <PlusOutlined /> },
          { key: 'hide', label: 'Hide this column', disabled: !layout.ready || layout.saving },
          { key: 'clear', label: 'Clear all sorts', disabled: !sorts.length },
        ],
        selectedKeys: sort ? [sort.direction] : [],
        onClick: ({ key }) => {
          if (key === 'filter') addFilter(field);
          else if (key === 'hide') layout.column(field, { visible: false });
          else if (key === 'clear') clearSorts();
          else if (canSort && (key === 'asc' || key === 'desc')) setSort(field, key === 'desc' ? 'desc' : 'asc');
        },
      }}><button type="button" className={`flow-column-menu${sort ? ' is-sorted' : ''}`} aria-label={`Column actions for ${field}`} title={label}>
        <span>{label}</span><span className="flow-column-menu-end" role={sort ? 'img' : undefined} aria-label={sort ? `Sorted ${sort.direction === 'asc' ? 'ascending' : 'descending'}` : undefined}>
          {sort ? sort.direction === 'asc' ? <ArrowUpOutlined /> : <ArrowDownOutlined /> : <DownOutlined />}
        </span>
      </button></Dropdown>{filteredFields.has(field) && <button type="button" className="flow-column-filter" draggable={false}
        aria-label={`Edit filters for ${field}`} title={`Edit filters for ${label}`} onClick={event => { event.stopPropagation(); openFilter(field); }}
        onDragStart={event => { event.preventDefault(); event.stopPropagation(); }}><FilterOutlined /></button>}</LayoutHeader>,
      render: (value: unknown) => {
        const shown = value == null ? '(Null)' : typeof value === 'object' ? JSON.stringify(value) : String(value);
        const scalar = exactCellValue(value, types?.[field]);
        if (scalar === undefined) return value == null ? <span className="flow-explorer-null" title="SQL NULL: no value">(Null)</span> : shown;
        return <div className="flow-explorer-value"><span className={value == null ? 'flow-explorer-null' : undefined} title={value == null ? 'SQL NULL: no value' : shown}>{shown || 'Empty'}</span><Dropdown overlayClassName="flow-menu-popup flow-explorer-menu" trigger={['click']} menu={{
          items: [{ key: 'include', label: 'Include this value' }, { key: 'exclude', label: 'Exclude this value' }],
          onClick: ({ key }) => filterValue(field, scalar, key === 'exclude'),
        }}><button type="button" className="flow-cell-menu" aria-label={`Filter ${field} by ${shown || 'empty string'}`} title={`Filter ${field} by ${shown || 'empty string'}`}><DownOutlined /></button></Dropdown></div>;
      },
    };
  });
}

export function ExplorerFilterButton({ open, count, onClick }: { open: boolean; count: number; onClick: () => void }) {
  return <button type="button" aria-expanded={open} onClick={onClick}><FilterOutlined /> Filter &amp; sort{count ? ` (${count})` : ''}</button>;
}

export function ExplorerResults({ results, children, viewportRef, selectionKey }: {
  results?: ComponentProps<typeof ExplorerSelection>;
  children?: ReactNode; viewportRef?: Ref<HTMLDivElement>; selectionKey?: string;
}) {
  return <div className="flow-inspector-body" ref={viewportRef}>{children}{results && <ExplorerSelection key={selectionKey} {...results} />}</div>;
}

export default function ExplorerWorkbench<Q extends object>({ header, children, locked }: {
  header?: ComponentProps<typeof ExplorerWorkbenchHeader<Q>>; children?: ReactNode; locked?: boolean;
}) {
  const overlay = useExplorerSelectionOverlay();
  return <ExplorerSelectionOverlayContext.Provider value={overlay}><div className="flow-inspector explorer-workbench">
    {header && <fieldset className="wb-shell" disabled={locked || overlay.active} {...(overlay.active ? { inert: '' } : {})}><ExplorerWorkbenchHeader key={JSON.stringify(header.source)} {...header} /></fieldset>}
    {children}
  </div></ExplorerSelectionOverlayContext.Provider>;
}

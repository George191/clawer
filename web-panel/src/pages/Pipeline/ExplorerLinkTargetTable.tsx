import type { ReactNode } from 'react';
import { Table } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { FilterOutlined } from '@/components/ui/icons';
import type { ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { propertyValue, type ExplorerPreviewRow } from './ExplorerObjectOverview';
import ExplorerWorkbenchHeader from './ExplorerWorkbenchHeader';
import './explorer-selection.css';
import './explorer-result-layout.css';
import './explorer-link-target-table.css';

type Props = {
  source: ExplorerDetailSource;
  label: string;
  rows: ExplorerPreviewRow[];
  columns: readonly string[];
  columnTypes: Record<string, string>;
  names?: Record<string, string>;
  widths?: Record<string, number>;
  wrap?: boolean;
  glyph: ReactNode;
  selectedKey: string;
  loading: boolean;
  busy: boolean;
  onSelect: (key: string) => void;
};
const noop = () => {};
const cellText = (value: unknown) => value == null ? 'null' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export default function ExplorerLinkTargetTable({ source, label, rows, columns, columnTypes, names = {}, widths = {}, wrap = false, glyph, selectedKey, loading, busy, onSelect }: Props) {
  const blocked = loading || busy;
  const choose = (key: string) => { if (!blocked && rows.some(row => row.key === key)) onSelect(key); };
  const dataColumns: ColumnsType<ExplorerPreviewRow> = [{
    title: 'TITLE', key: 'explorer-result-title', className: 'flow-explorer-selection__title-column', width: 232, fixed: 'left',
    onHeaderCell: () => ({ scope: 'col' }),
    render: (_value, row) => <span className="flow-explorer-selection__table-title">{glyph}<span title={row.title}>{row.title}</span></span>,
  }, ...columns.map(field => {
    const width = widths[field] ?? 180;
    return { key: field, dataIndex: ['record', field], title: <span title={field}>{names[field] || field}</span>, width,
      className: wrap ? 'result-cell-wrap' : 'result-cell-clip', onHeaderCell: () => ({ scope: 'col' as const }),
      onCell: () => ({ style: { width, minWidth: width, maxWidth: width } }),
      render: (_value: unknown, row: ExplorerPreviewRow) => {
        const shown = cellText(propertyValue(row, field));
        return <span title={shown}>{shown}</span>;
      },
    };
  })];
  return <section className="explorer-link-target-table flow-console--ontology" aria-label="Target object table">
    <fieldset className="wb-shell explorer-link-target-table__tools" disabled aria-label="Target table controls" aria-disabled="true">
      <div {...{ inert: '' }}>
        <ExplorerWorkbenchHeader source={source} label={label} available glyph={glyph} fields={[...columns]} names={names} types={columnTypes}
          rows={rows.map(row => ({ ...row.record, ...row.identity }))} selected={[]} query={{ filters: [], sorts: [], search: '' }}
          appliedSearch="" onSearchValues={() => false} onClearSearch={noop} onExploreFilterValue={noop}
          resource="preview" availableResources={['preview']} admin={false} selectResource={noop}
          layoutControl={<button type="button" className="flow-tool" aria-label="Configure columns" disabled>Custom layout <span aria-hidden="true">▾</span></button>}
          savedLoading={false} savedError="" openSavedItem={noop} closeSavedItem={noop} saveCompleted={noop} updateSavedItem={noop}
          addFilter={noop} backToTypes={noop} toggleCatalog={noop} closeCatalog={noop} catalogOpen={false}
          mode="results" setMode={noop} clearSelection={noop}
          filterButton={<button type="button" disabled><FilterOutlined /> Filter &amp; sort</button>} />
      </div>
    </fieldset>
    <div className="flow-explorer-selection">
      <div className="flow-explorer-selection__results">
        <Table<ExplorerPreviewRow> className="flow-data-table" size="small" loading={loading} pagination={false}
          dataSource={rows} columns={dataColumns} rowKey={row => row.key} scroll={{ x: 'max-content' }}
          rowSelection={{ type: 'radio', fixed: true, columnWidth: 36, selectedRowKeys: selectedKey ? [selectedKey] : [],
            getCheckboxProps: row => ({ disabled: blocked, 'aria-label': `Select ${row.title}` }),
            onChange: keys => { if (typeof keys[0] === 'string') choose(keys[0]); },
          }}
          onRow={row => ({ tabIndex: blocked ? -1 : 0, className: 'flow-explorer-result-row', 'aria-selected': row.key === selectedKey,
            onClick: () => choose(row.key), onKeyDown: event => {
              if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); choose(row.key); }
            },
          })} />
      </div>
    </div>
  </section>;
}

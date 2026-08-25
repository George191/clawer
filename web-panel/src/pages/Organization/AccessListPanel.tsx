import React, { useLayoutEffect, useRef, useState } from 'react';
import { Card, Input, Table } from 'antd';
import type { InputProps, TableProps } from 'antd';
import { SearchOutlined } from '@ant-design/icons';

type AccessListPanelProps<T extends object> = {
  intro?: React.ReactNode;
  filters?: React.ReactNode;
  search: InputProps;
  actions?: React.ReactNode;
  table: TableProps<T>;
};

const AccessListPanel = <T extends object>({ intro, filters, search, actions, table }: AccessListPanelProps<T>) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const filtersRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [bodyHeight, setBodyHeight] = useState(260);
  const [pageSize, setPageSize] = useState(10);
  const paginationProps = typeof table.pagination === 'object' ? table.pagination : {};

  useLayoutEffect(() => {
    const update = () => {
      if (!rootRef.current || !toolbarRef.current) return;
      const available = rootRef.current.clientHeight;
      const fixed = (filtersRef.current?.offsetHeight ?? 0) + toolbarRef.current.offsetHeight + 100;
      setBodyHeight(Math.max(56, available - fixed));
    };
    update();
    const observer = new ResizeObserver(update);
    if (rootRef.current) observer.observe(rootRef.current);
    if (filtersRef.current) observer.observe(filtersRef.current);
    if (toolbarRef.current) observer.observe(toolbarRef.current);
    return () => observer.disconnect();
  }, [Boolean(intro || filters)]);

  return <div ref={rootRef} className="access-list-panel">
    <Card className="access-table-card" styles={{ body: { padding: 0 } }}>
      {(intro || filters) && <div ref={filtersRef} className="access-list-filters">{intro}{filters}</div>}
      <div ref={toolbarRef} className="access-toolbar access-list-toolbar">
        <Input allowClear prefix={<SearchOutlined />} {...search} />
        {actions}
      </div>
      <Table<T>
        {...table}
        scroll={{ ...table.scroll, y: bodyHeight }}
        pagination={table.pagination === false ? false : {
          pageSize,
          showSizeChanger: { showSearch: false },
          pageSizeOptions: [10, 25, 50, 100],
          showTotal: (total, range) => `Showing ${range[0]} to ${range[1]} of ${total} entries`,
          ...paginationProps,
          onShowSizeChange: (current, size) => {
            setPageSize(size);
            paginationProps.onShowSizeChange?.(current, size);
          },
        }}
      />
    </Card>
  </div>;
};

export default AccessListPanel;

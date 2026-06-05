import React, { useCallback, useMemo } from 'react';
import { Table, Button, Space, Card, Skeleton, Typography, Tooltip } from 'antd';
import { FileExcelOutlined, FileTextOutlined } from '@ant-design/icons';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import type { QueryResult } from '@/services/types';
import EmptyState from '@/components/EmptyState';

const { Text } = Typography;

interface ResultTableProps {
  result: QueryResult | null;
  loading?: boolean;
}

const ResultTable: React.FC<ResultTableProps> = ({ result, loading }) => {
  const columns: ColumnsType<Record<string, unknown>> | undefined = useMemo(() => {
    if (!result) return undefined;
    return result.columns.map((col) => ({
      title: col,
      dataIndex: col,
      key: col,
      ellipsis: true,
      width: Math.max(120, col.length * 14 + 40),
      sorter: (a, b) => {
        const av = a[col];
        const bv = b[col];
        if (typeof av === 'number' && typeof bv === 'number') return av - bv;
        return String(av ?? '').localeCompare(String(bv ?? ''));
      },
      filterSearch: true,
      filters: [...new Set(result.rows.map((r) => String(r[col] ?? '')))].slice(0, 50).map((v) => ({
        text: v.length > 30 ? v.slice(0, 30) + '...' : v,
        value: v,
      })),
      onFilter: (value, record) =>
        String(record[col] ?? '').toLowerCase().includes(String(value).toLowerCase()),
      render: (val: unknown) => {
        const str = String(val ?? '');
        return (
          <Tooltip title={str.length > 50 ? str : undefined}>
            <span>{str.length > 50 ? str.slice(0, 50) + '...' : str}</span>
          </Tooltip>
        );
      },
    }));
  }, [result]);

  const pagination: TablePaginationConfig = {
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (total) => `共 ${total} 条`,
    defaultPageSize: 20,
    pageSizeOptions: ['20', '50', '100'],
  };

  const handleExportCSV = useCallback(() => {
    if (!result) return;
    const header = result.columns.join(',');
    const rows = result.rows.map((row) =>
      result.columns
        .map((c) => {
          const val = String(row[c] ?? '');
          return val.includes(',') || val.includes('"') || val.includes('\n')
            ? `"${val.replace(/"/g, '""')}"`
            : val;
        })
        .join(',')
    );
    const csv = '\uFEFF' + [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'query_result.csv'; a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  const handleExportExcel = useCallback(() => {
    if (!result) return;
    const header = result.columns.join('\t');
    const rows = result.rows.map((row) => result.columns.map((c) => String(row[c] ?? '')).join('\t'));
    const tsv = '\uFEFF' + [header, ...rows].join('\n');
    const blob = new Blob([tsv], { type: 'text/tab-separated-values;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'query_result.xls'; a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  // Loading state
  if (loading) {
    return (
      <Card size="small">
        <div style={{ padding: '12px 0' }}>
          <Skeleton active paragraph={{ rows: 6 }} />
        </div>
      </Card>
    );
  }

  // Empty state
  if (!result) {
    return (
      <Card size="small">
        <EmptyState title="暂无数据" description="编写 SQL 并按 Ctrl+Enter 执行查询" />
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title={
        <Space size={12}>
          <span>查询结果</span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            返回 {result.rowCount} 条 · {result.elapsed}s
          </Text>
        </Space>
      }
      extra={
        <Space size={8}>
          <Tooltip title="导出 CSV">
            <Button size="small" icon={<FileTextOutlined />} onClick={handleExportCSV}>导出 CSV</Button>
          </Tooltip>
          <Tooltip title="导出 Excel">
            <Button size="small" icon={<FileExcelOutlined />} onClick={handleExportExcel}>导出 Excel</Button>
          </Tooltip>
        </Space>
      }
    >
      <Table
        columns={columns}
        dataSource={result.rows.map((r, i) => ({ ...r, _key: i }))}
        rowKey="_key"
        size="small"
        scroll={{ y: 400, x: 'max-content' }}
        pagination={pagination}
        sticky
      />
    </Card>
  );
};

export default ResultTable;
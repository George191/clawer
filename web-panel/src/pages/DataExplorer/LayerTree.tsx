import React, { useState, useEffect, useMemo } from 'react';
import { Tree, Dropdown, Card, Input, Skeleton, theme, Typography, Spin, Empty, Button, Result } from 'antd';
import type { MenuProps } from 'antd';
import {
  TableOutlined,
  SearchOutlined,
  CopyOutlined,
  EyeOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import { fetchLayers, fetchLayerTables } from '@/services/api';
import type { LayerNode, LayerTable } from '@/services/types';

const { Text } = Typography;

interface LayerTreeProps {
  onInsertTable: (tableRef: string) => void;
  onDoubleClickTable: (tableRef: string) => void;
}

const LAYER_ICONS: Record<string, string> = {
  rds: '🧬',
  raw: '🧬',
  ods: '🧹',
  task: '⚙️',
  dwd: '📊',
  dws: '📈',
  dim: '🗂️',
  ads: '🚀',
  tmp: '📦',
};

interface TableCache {
  [layer: string]: LayerTable[];
}

const LayerTree: React.FC<LayerTreeProps> = ({ onInsertTable, onDoubleClickTable }) => {
  const [searchText, setSearchText] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [layers, setLayers] = useState<LayerNode[]>([]);
  const [tableCache, setTableCache] = useState<TableCache>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { token } = theme.useToken();

  // Load layers on mount
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchLayers()
      .then((data) => {
        setLayers(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        const err = e as { message?: string };
        setError(err?.message || '获取 ETL 层级失败');
        setLoading(false);
      });
  }, []);

  // Load tables when layer expands
  const handleExpand = async (keys: React.Key[]) => {
    setExpandedKeys(keys as string[]);
    // Load tables for newly expanded layers that aren't cached
    const newKeys = (keys as string[]).filter((k) => !tableCache[k]);
    if (newKeys.length === 0) return;

    const results = await Promise.allSettled(
      newKeys.map((key) => fetchLayerTables(key).then((tables) => ({ key, tables }))),
    );

    setTableCache((prev) => {
      const next = { ...prev };
      for (const r of results) {
        if (r.status === 'fulfilled') {
          next[r.value.key] = r.value.tables;
        }
      }
      return next;
    });
  };

  const contextMenuItems = (fullRef: string): MenuProps['items'] => [
    {
      key: 'select',
      label: 'SELECT *',
      icon: <CopyOutlined />,
      onClick: () => onInsertTable(fullRef),
    },
    {
      key: 'schema',
      label: '查看 Schema',
      icon: <InfoCircleOutlined />,
    },
    {
      key: 'sample',
      label: '样本数据 (10条)',
      icon: <EyeOutlined />,
      onClick: () => onDoubleClickTable(fullRef),
    },
  ];

  // Build tree from layers + tables
  const treeData = useMemo(() => {
    return layers.map((layer) => {
      const tables = tableCache[layer.key] ?? [];
      const icon = LAYER_ICONS[layer.key] || '📦';

      const filteredTables = tables.filter((t) =>
        !searchText || t.name.toLowerCase().includes(searchText.toLowerCase()),
      );

      return {
        key: layer.key,
        title: (
          <span style={{ fontWeight: 600, color: token.colorText, fontSize: 13 }}>
            {icon} {layer.label}
          </span>
        ),
        selectable: false,
        isLeaf: false,
        children: filteredTables.map((table) => {
          const fullRef = `${layer.key}.${table.name}`;
          return {
            key: fullRef,
            title: (
              <Dropdown menu={{ items: contextMenuItems(fullRef) }} trigger={['contextMenu']}>
                <span style={{ color: token.colorTextSecondary, fontSize: 12, fontFamily: '"Fira Code", monospace' }}>
                  {table.name}
                </span>
              </Dropdown>
            ),
            icon: <TableOutlined style={{ fontSize: 11 }} />,
            isLeaf: true,
            selectable: true,
          };
        }),
      };
    });
  }, [layers, tableCache, searchText, token, contextMenuItems]);

  // Auto-expand all when searching
  const allKeys = useMemo(() => layers.map((l) => l.key), [layers]);
  const effectiveExpandedKeys = searchText ? allKeys : expandedKeys;

  // ── Error state ──
  if (error) {
    return (
      <Card
        size="small"
        title={<span style={{ fontWeight: 600, fontSize: 13 }}>🗃️ ETL 层级</span>}
        style={{ height: '100%' }}
        bodyStyle={{ padding: '8px 12px' }}
      >
        <Result
          status="error"
          title="加载失败"
          subTitle={error}
          extra={
            <Button
              onClick={() => {
                setError(null);
                setLoading(true);
                fetchLayers()
                  .then(setLayers)
                  .catch((e: unknown) => setError((e as { message?: string }).message || '获取失败'))
                  .finally(() => setLoading(false));
              }}
            >
              重试
            </Button>
          }
        />
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title={<span style={{ fontWeight: 600, fontSize: 13 }}>🗃️ ETL 层级</span>}
      style={{ height: '100%' }}
      bodyStyle={{ padding: '8px 12px' }}
    >
      <Input
        size="small"
        placeholder="搜索表名..."
        prefix={<SearchOutlined style={{ color: token.colorTextQuaternary }} />}
        allowClear
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        style={{ marginBottom: 8 }}
      />

      {loading && <Skeleton active paragraph={{ rows: 4 }} />}

      {!loading && treeData.length === 0 && (
        <Empty description="暂无 ETL 层级数据" />
      )}

      {treeData.length > 0 && (
        <Tree.DirectoryTree
          showIcon
          selectedKeys={selectedKeys}
          expandedKeys={effectiveExpandedKeys}
          onExpand={handleExpand}
          onSelect={(keys) => setSelectedKeys(keys as string[])}
          onDoubleClick={(_, node) => {
            const n = node as { isLeaf?: boolean; key: string };
            if (n.isLeaf) onDoubleClickTable(n.key);
          }}
          treeData={treeData}
          style={{ fontSize: 12, background: 'transparent' }}
        />
      )}
    </Card>
  );
};

export default LayerTree;